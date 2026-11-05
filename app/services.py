"""拉起 / 结束 AstrBot 和 NapCat，读 NapCat 的登录状态和二维码。"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

from store import State, file_lock, redact
import safe_logging

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class StopPermissionError(RuntimeError):
    """进程仍在运行，但当前权限不足以结束它。"""


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def process_path(pid: int) -> Path | None:
    handle = _k32.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # 已退出
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buf.value)
    finally:
        _k32.CloseHandle(handle)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                ("exe", wintypes.WCHAR * 260)]


_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]


def find_processes(layout: "Layout") -> dict[str, list[int]]:
    """按完整可执行文件路径找回丢失的进程号，不按进程名结束用户自己的 Python / QQ。"""
    expected = {layout.python.resolve(): "astrbot", (layout.napcat / "node.exe").resolve(): "napcat"}
    found = {"astrbot": [], "napcat": []}
    parents = {}
    snapshot = _k32.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        more = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.exe.lower() in ("python.exe", "node.exe"):
                try:
                    path = process_path(entry.pid)
                    if path and path.resolve() in expected:
                        found[expected[path.resolve()]].append(entry.pid)
                        parents[entry.pid] = entry.parent
                except OSError:
                    pass  # 其他用户的进程不可读；已记录的本程序 PID 会在 stop 中单独校验
            more = _k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snapshot)
    # 先结束 NapCat 的监督父进程，避免只停 worker 时被它重新拉起。
    for ids in found.values():
        members = set(ids)
        ids.sort(key=lambda pid: parents.get(pid) in members)
    return found


def _log(state, message: str) -> None:
    safe_logging.append_log(state.root / "logs" / "services.log", message,
                            secrets=safe_logging.secret_values(state), root=state.root)


class Layout:
    """安装目录里各部分的位置（和 build/build.py 组装的结构一致）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.runtime = self.root / "runtime" / "python"
        self.python = self.runtime / "python.exe"
        self.pythonw = self.runtime / "pythonw.exe"
        self.astrbot = self.root / "astrbot"
        self.astrbot_data = self.astrbot / "data"
        self.napcat = self.root / "napcat"
        self.napcat_work = self.root / "napcat-data"
        self.pack = self.root / "pack"
        self.app = self.root / "app"
        self.logs = self.root / "logs"


def _spawn(name: str, argv: list[str], cwd: Path, env_extra: dict, layout: Layout, state) -> int:
    state.root.mkdir(parents=True, exist_ok=True)
    with file_lock(state.root / "services.lock"):
        if State.load(state.root).data.get("services_stopped"):
            raise RuntimeError("服务已手动停止，请点「让她上线」。")
        existing = find_processes(layout)[name]
        if existing:
            if state.reload_pids().get(name) != existing[0]:
                state.data.setdefault("pids", {})[name] = existing[0]
                state.save()
            return existing[0]
        return _spawn_new(name, argv, cwd, env_extra, layout, state)


def _spawn_new(name: str, argv: list[str], cwd: Path, env_extra: dict, layout: Layout, state) -> int:
    proc = logged_process(name, argv, cwd, env_extra, layout, state)
    state.reload_pids()  # 别把另一个窗口刚起的进程从 state.json 里冲掉
    state.data.setdefault("pids", {})[name] = proc.pid
    state.save()
    _log(state, f"启动 {name}：PID={proc.pid}")
    return proc.pid


def _start_capture(name: str, layout: Layout, state):
    temp_dir = layout.root / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-", dir=temp_dir) as temp:
        ready = Path(temp) / "ready"
        reader = subprocess.Popen([str(layout.pythonw), str(Path(__file__).with_name("log_capture.py")),
                                   str(layout.root), str(state.root), name, str(ready)],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        deadline = time.monotonic() + 30
        while reader.poll() is None and not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        if reader.poll() is not None or not ready.exists():
            reader.stdin.close()
            if reader.poll() is None:
                reader.kill()
            reader.wait(timeout=5)
            raise OSError("无法启动脱敏日志采集，请检查安装目录写入权限和内置 Python。")
        return reader


def logged_process(name: str, argv: list[str], cwd: Path, env_extra: dict, layout: Layout, state):
    reader = _start_capture(name, layout, state)
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env={**os.environ, **env_extra}, stdin=subprocess.DEVNULL,
                                stdout=reader.stdin, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    except OSError:
        reader.stdin.close()
        reader.wait(timeout=5)
        raise
    reader.stdin.close()
    proc.log_reader = reader
    return proc


def start_astrbot(layout: Layout, state) -> int:
    return _spawn("astrbot", [str(layout.python), "main.py"], layout.astrbot,
                  {"ASTRBOT_ROOT": str(layout.astrbot), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                  layout, state)


def start_napcat(layout: Layout, state, quick_qq: str | None) -> int:
    argv = [str(layout.napcat / "node.exe"), str(layout.napcat / "index.js")]
    if quick_qq:
        argv += ["-q", str(quick_qq)]
    return _spawn("napcat", argv, layout.napcat, {"NAPCAT_WORKDIR": str(layout.napcat_work)}, layout, state)


def is_running(pid: int | None) -> bool:
    if not pid:
        return False
    handle = _k32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # 进程已退出。
            return False
        raise ctypes.WinError(error)
    try:
        code = ctypes.c_ulong()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259  # STILL_ACTIVE
    finally:
        _k32.CloseHandle(handle)


def can_terminate(pid: int) -> bool:
    handle = _k32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE；只检查权限，不结束进程。
    if handle:
        _k32.CloseHandle(handle)
        return True
    error = ctypes.get_last_error()
    if error == 5:
        return False
    if error == 87:  # 进程在检查期间退出。
        return True
    raise ctypes.WinError(error)


def stop(state, name: str, *, layout: Layout | None = None) -> None:
    with file_lock(state.root / "services.lock"):
        layout = layout or Layout(state.root)
        expected = {"astrbot": layout.python, "napcat": layout.napcat / "node.exe"}[name].resolve()
        recorded = state.reload_pids().get(name)
        pids = find_processes(layout)[name]
        if recorded and is_running(recorded) and process_path(recorded) == expected:
            if recorded not in pids:
                pids.append(recorded)
        failures = []
        for pid in pids:
            if not is_running(pid):
                continue
            if process_path(pid) != expected:
                continue
            try:
                r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=15)
                detail = (r.stdout + r.stderr).decode("oem", errors="replace").strip()
                _log(state, f"停止 {name}：PID={pid}，返回码={r.returncode}，{detail}")
                deadline = time.monotonic() + 3
                while r.returncode == 0 and is_running(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if is_running(pid):
                    lower_detail = detail.lower()
                    denied = bool(r.returncode) and ("拒绝访问" in lower_detail or "access is denied" in lower_detail
                                                     or "access denied" in lower_detail or not can_terminate(pid))
                    failures.append((pid, f"{name} PID={pid} 未退出（返回码 {r.returncode}）", denied))
            except (OSError, subprocess.TimeoutExpired) as e:
                failures.append((pid, f"{name} PID={pid}：{e}", isinstance(e, PermissionError)))
        candidates = list(dict.fromkeys(find_processes(layout)[name] + [pid for pid, _, _ in failures]))
        remaining, permission_denied = [], False
        for pid in candidates:
            try:
                if is_running(pid) and process_path(pid) == expected:
                    remaining.append(pid)
            except PermissionError:
                remaining.append(pid)
                permission_denied = True
        if remaining:
            state.data.setdefault("pids", {})[name] = remaining[0]
            state.save()
            unresolved = [(msg, denied) for pid, msg, denied in failures if pid in remaining]
            permission_denied = permission_denied or any(denied for _, denied in unresolved)
            message = "；".join(msg for msg, _ in unresolved) or f"{name} 仍在运行：PID={remaining}"
            _log(state, "停止失败：" + message)
            error_type = StopPermissionError if permission_denied else RuntimeError
            raise error_type(message + "。请查看 logs/services.log 后重试。")
        state.data.get("pids", {}).pop(name, None)
        state.save()
        _log(state, f"已确认 {name} 停止")


def stop_all(state) -> None:
    with file_lock(state.root / "services.lock"):
        state.data["services_stopped"] = True
        state.save(force=("services_stopped",))
    errors = []
    permission_denied = False
    for name in ("napcat", "astrbot"):
        try:
            stop(state, name)
        except (OSError, RuntimeError) as e:
            errors.append(str(e))
            if isinstance(e, (PermissionError, StopPermissionError)):
                permission_denied = True
                _log(state, f"停止失败：{name} 权限不足（访问被拒绝）")
    if errors:
        raise (StopPermissionError if permission_denied else RuntimeError)("；".join(errors))


def elevated_stop(root: Path) -> None:
    """让 Windows 请求管理员授权，提升后重新按安装目录核验进程。"""
    layout = Layout(root)
    args = subprocess.list2cmdline([str(Path(__file__).with_name("main.py")), "--stop-elevated", "--root", str(root)])
    env = {**os.environ, "DAFEIYU_STOP_PYTHON": str(layout.pythonw), "DAFEIYU_STOP_ARGS": args}
    script = ("$ErrorActionPreference='Stop'; try { "
              "$p=Start-Process -FilePath $env:DAFEIYU_STOP_PYTHON -ArgumentList $env:DAFEIYU_STOP_ARGS "
              "-Verb RunAs -WindowStyle Hidden -PassThru -Wait -ErrorAction Stop; exit $p.ExitCode "
              "} catch { if ($_.Exception.NativeErrorCode -eq 1223) { exit 1223 }; exit 1 }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        raise RuntimeError("无法请求管理员授权，请关闭当前启动窗口，以管理员身份打开桌面快捷方式后重试。") from e
    if result.returncode:
        raise RuntimeError("未获得管理员授权或停止失败。请关闭当前启动窗口，以管理员身份打开桌面快捷方式后重试；详情见 logs/services.log。")


def allow_start(state) -> None:
    with file_lock(state.root / "services.lock"):
        if State.load(state.root).data.get("uninstalling"):
            raise RuntimeError("正在卸载，无法启动。")
        state.data["services_stopped"] = False
        state.save(force=("services_stopped",))


def clear_qq_login(layout: Layout, state) -> None:
    """停止本安装的 QQ 服务并忘记账号，保留 QQ 客户端共享登录和聊天数据。"""
    stop(state, "napcat")
    state.data.pop("qq", None)
    state.save()
    # 旧版目录可能被用户迁移为 junction，不能穿过链接清理另一安装的数据。
    if layout.napcat_work.resolve() != layout.napcat_work.absolute():
        return
    cache = layout.napcat_work / "cache"
    if cache.exists() and cache.resolve() == cache.absolute():
        shutil.rmtree(cache)
    cfg = layout.napcat_work / "config"
    if cfg.resolve() == cfg.absolute():
        for path in list(cfg.glob("napcat_*.json")) + list(cfg.glob("onebot11_*.json")):
            path.unlink(missing_ok=True)


def wait_http(url: str, timeout_s: float, opener=urllib.request.urlopen, interval: float = 1.0,
              alive=None) -> bool:
    """等到这个地址有 HTTP 响应（哪怕是 4xx）就算服务起来了。"""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with opener(url, timeout=5):
                return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            pass
        if (alive is not None and not alive()) or time.monotonic() >= deadline:
            return False
        time.sleep(interval)


class NapCatWeb:
    """NapCat WebUI 接口（只用登录相关的几个）。返回格式 {code: 0|-1, data, message}，HTTP 都是 200。"""

    def __init__(self, base_url: str, token: str, opener=urllib.request.urlopen):
        self.base = base_url.rstrip("/") + "/api"
        self.token = token
        self.opener = opener
        self.credential = None
        self.last_error = ""

    def _post(self, path: str, body: dict | None = None, auth: bool = True) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", f"Bearer {self.credential}")
        with self.opener(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    def login(self) -> None:
        r = self._post("/auth/login", {"hash": hashlib.sha256((self.token + ".napcat").encode()).hexdigest()},
                       auth=False)
        if r.get("code") != 0:
            raise RuntimeError(f"NapCat WebUI 登录失败：{r.get('message')}")
        self.credential = r["data"]["Credential"]

    def call(self, path: str, body: dict | None = None):
        if not self.credential:
            self.login()
        r = self._post(path, body)
        if r.get("code") != 0 and "Unauthorized" in str(r.get("message")):
            # 凭证一小时过期，重新登录一次
            self.login()
            r = self._post(path, body)
        if r.get("code") != 0:
            raise RuntimeError(str(r.get("message")))
        return r.get("data")

    def status(self) -> str:
        """starting / need_scan / scanned / online / offline / error；出错原因留在 self.last_error"""
        try:
            d = self.call("/QQLogin/CheckLoginStatus") or {}
        except (urllib.error.URLError, OSError, RuntimeError, ValueError):
            return "starting"
        self.last_error = str(d.get("loginError") or "")
        if d.get("isLogin"):
            return "online"
        if d.get("isOffline"):
            return "offline"
        if d.get("loginPhase") == "qrcode_scanned":
            return "scanned"
        if d.get("loginError") and ("过期" in self.last_error or "失效" in self.last_error):
            return "error"
        if d.get("qrcodeurl"):
            return "need_scan"
        if d.get("loginError"):
            return "error"
        return "starting"

    def refresh_qr(self) -> None:
        self.call("/QQLogin/RefreshQRcode")

    def login_uin(self) -> str | None:
        try:
            uin = (self.call("/QQLogin/GetQQLoginInfo") or {}).get("uin")
        except Exception:
            return None
        return str(uin) if uin else None


def qrcode_png(layout: Layout) -> bytes | None:
    path = layout.napcat_work / "cache" / "qrcode.png"
    try:
        return path.read_bytes()
    except OSError:
        return None
