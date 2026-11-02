"""安装状态（state.json）、key 加密保存、日志脱敏。"""
import base64
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Iterable


_SAVE_LOCK = threading.RLock()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(fn, data: bytes) -> bytes:
    src, out = _blob(data), _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN = 1：永远不弹窗
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    """用 Windows DPAPI 加密（只有当前 Windows 用户能解开）。"""
    return base64.b64encode(_crypt(ctypes.windll.crypt32.CryptProtectData, text.encode("utf-8"))).decode()


def unprotect(blob: str) -> str:
    return _crypt(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(blob)).decode("utf-8")


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def app_home(home: Path | None = None) -> Path:
    """记「上次装在哪」的地方：%LOCALAPPDATA%\\DaFeiYu（一键包本身每次从临时目录运行）。"""
    return Path(home) if home else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DaFeiYu"


def remember_install(root: Path, home: Path | None = None) -> None:
    h = app_home(home)
    h.mkdir(parents=True, exist_ok=True)
    (h / "install.json").write_text(json.dumps({"root": str(root)}), encoding="utf-8")


def last_install(home: Path | None = None) -> str | None:
    try:
        return json.loads((app_home(home) / "install.json").read_text(encoding="utf-8")).get("root")
    except (OSError, ValueError):
        return None


class State:
    def __init__(self, root: Path, data: dict):
        self.root = Path(root)
        self.data = data

    @classmethod
    def load(cls, root: Path) -> "State":
        path = Path(root) / "state.json"
        with _SAVE_LOCK:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.setdefault("done", [])
        data.setdefault("secrets", {})
        return cls(root, data)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "state.json"
        tmp = path.with_suffix(".tmp")
        with _SAVE_LOCK:  # 安装线程和接口线程会同时保存
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(20):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:  # 杀毒软件或读的人短暂占着文件
                    if attempt == 19:
                        raise
                    time.sleep(0.05)

    def set_secret(self, name: str, value: str) -> None:
        self.data["secrets"][name] = protect(value)

    def get_secret(self, name: str) -> str:
        blob = self.data["secrets"].get(name)
        return unprotect(blob) if blob else ""

    def secret_values(self) -> list[str]:
        # list() 先拷一份键：安装线程在这里脱敏日志时，接口线程可能正往里存新的 key
        return [self.get_secret(n) for n in list(self.data["secrets"])]

    def reload_pids(self) -> dict:
        """重新从磁盘读 pids。

        安装向导和状态页是两个进程，各自起着服务、各自往 state.json 里写 pids。只信自己内存里
        那份的话，另一个窗口刚起的进程在这边看着像没起，页面就会显示「她下线了」。
        """
        try:
            self.data["pids"] = State.load(self.root).data.get("pids", {})
        except (OSError, ValueError):
            pass
        return self.data.get("pids", {})

    def mark_done(self, step: str) -> None:
        if step not in self.data["done"]:
            self.data["done"].append(step)
        self.save()

    def is_done(self, step: str) -> bool:
        return step in self.data["done"]
