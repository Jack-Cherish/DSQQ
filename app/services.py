"""拉起 / 结束 AstrBot 和 NapCat，读 NapCat 的登录状态和二维码。"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


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
    layout.logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **env_extra}
    # 子进程直接写日志文件：向导窗口关掉后它们还要接着跑，不能挂在向导的管道上
    with open(layout.logs / f"{name}.log", "ab") as log:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    state.reload_pids()  # 别把另一个窗口刚起的进程从 state.json 里冲掉
    state.data.setdefault("pids", {})[name] = proc.pid
    state.save()
    return proc.pid


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
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def stop(state, name: str) -> None:
    state.reload_pids()  # 同上：先并上盘里的，只删自己要停的那个
    pid = state.data.get("pids", {}).pop(name, None)
    if pid and is_running(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    state.save()


def stop_all(state) -> None:
    for name in list(state.data.get("pids", {})):
        stop(state, name)


def tencent_roots(layout: Layout) -> list[Path]:
    """找用户的 QQ 数据目录（……\\Tencent Files）。

    默认在「我的文档」下，但 QQ 允许改到别处（这台机器就在 D:\\MyCache\\QQ 下）。改过的位置记在
    QQ 自己的 mmkv 里，不好读；NapCat 跑起来后日志里会写出完整路径，从那儿捞最省事。
    """
    roots = []
    docs = Path.home() / "Documents" / "Tencent Files"
    if docs.is_dir():
        roots.append(docs)
    log = layout.logs / "napcat.log"
    if log.exists():
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in re.finditer(r"([A-Za-z]:\\(?:[^\\\r\n\"',]+\\)*Tencent Files)\\", text):
            p = Path(m.group(1))
            if p.is_dir() and p not in roots:
                roots.append(p)
    return roots


def forget_qq_account(layout: Layout, qq: str, owner_qq: str = "") -> None:
    """删掉这个号在 QQ 数据目录里的登录数据，让 NapCat 不再认识它。

    按号分开存的那份（Tencent Files\\<QQ>\\）和 nt_data\\Login\\.<QQ> 这个标记，是「这个号登录过」
    的全部凭据，删了它下次只能重新扫码。nt_qq\\global 是所有号共用的，里头有主人自己常用号的东西，
    绝对不碰。主人的号也绝对不碰（号填错时的兜底）。
    """
    if not qq or str(qq) == str(owner_qq):
        return
    qq = str(qq)
    for root in tencent_roots(layout):
        shutil.rmtree(root / qq, ignore_errors=True)
        (root / "nt_qq" / "global" / "nt_data" / "Login" / f".{qq}").unlink(missing_ok=True)


def clear_qq_login(layout: Layout, state) -> None:
    """让小号彻底不再是机器人：退出登录，下次启动回到扫码。

    三样东西都要清：安装目录里记的 QQ 号（有它就走 -q 快速登录，直接登回老号）、NapCat 按号生成的
    配置和二维码缓存、以及这个号在 QQ 数据目录里的登录凭据（不删它 NapCat 的快速登录列表里还有它，
    还能自己登回去）。
    """
    stop(state, "napcat")
    qq = state.data.pop("qq", None)
    state.save()
    shutil.rmtree(layout.napcat_work / "cache", ignore_errors=True)
    cfg = layout.napcat_work / "config"
    for path in list(cfg.glob("napcat_*.json")) + list(cfg.glob("onebot11_*.json")):
        path.unlink(missing_ok=True)
    forget_qq_account(layout, qq, state.data.get("owner_qq", ""))


def wait_http(url: str, timeout_s: float, opener=urllib.request.urlopen, interval: float = 1.0) -> bool:
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
        if time.monotonic() >= deadline:
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
    return path.read_bytes() if path.exists() else None
