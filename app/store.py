"""安装状态（state.json）、key 加密保存、日志脱敏。"""
import base64
import copy
import ctypes
import hashlib
import json
import msvcrt
import os
import threading
import time
import uuid
from ctypes import wintypes
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from fileio import atomic_write_text
from safe_logging import sanitize


_SAVE_LOCK = threading.RLock()


@contextmanager
def file_lock(path: Path):
    """同时串行化两个向导进程和各自的接口线程。"""
    with _SAVE_LOCK, open(path, "a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _merge_changes(current: dict, before: dict, after: dict) -> dict:
    merged = copy.deepcopy(current)
    for key in before.keys() - after.keys():
        merged.pop(key, None)
    for key, value in after.items():
        if key in before and value == before[key]:
            continue
        if isinstance(value, dict) and isinstance(before.get(key, {}), dict):
            merged[key] = _merge_changes(merged.get(key, {}), before.get(key, {}), value)
        elif key == "done":
            removed = set(before.get(key, [])) - set(value)
            merged[key] = list(dict.fromkeys([x for x in merged.get(key, []) if x not in removed] + value))
        else:
            merged[key] = copy.deepcopy(value)
    return merged


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
    return sanitize(text, secrets)


def app_home(home: Path | None = None) -> Path:
    """记「上次装在哪」的地方：%LOCALAPPDATA%\\DaFeiYu（一键包本身每次从临时目录运行）。"""
    return Path(home) if home else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DaFeiYu"


def instance_home(root: Path, home: Path | None = None) -> Path:
    identity = os.path.normcase(str(Path(root).resolve())).encode("utf-8")
    return app_home(home) / "instances" / hashlib.sha256(identity).hexdigest()[:16]


def remember_install(root: Path, home: Path | None = None) -> None:
    h = app_home(home)
    h.mkdir(parents=True, exist_ok=True)
    atomic_write_text(h / "install.json", json.dumps({"root": str(root)}))


def last_install(home: Path | None = None) -> str | None:
    try:
        value = json.loads((app_home(home) / "install.json").read_text(encoding="utf-8-sig")).get("root")
        return value if isinstance(value, str) and value.strip() else None
    except (OSError, ValueError, AttributeError):
        return None


class StateCorruptError(ValueError):
    """状态无法安全读取；原文件留待恢复，不以空状态覆盖。"""


def read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError()
        for name in ("secrets", "pids", "ports", "profile"):
            if name in data and not isinstance(data[name], dict):
                raise ValueError()
        if not isinstance(data.get("done", []), list) or not all(isinstance(x, str) for x in data.get("done", [])):
            raise ValueError()
        if not all(isinstance(value, str) for value in data.get("secrets", {}).values()):
            raise ValueError()
        if not all(type(value) is int and value > 0 for value in data.get("pids", {}).values()):
            raise ValueError()
        data.setdefault("done", [])
        data.setdefault("secrets", {})
        return data
    except (ValueError, UnicodeError) as e:
        raise StateCorruptError("安装状态文件损坏，原文件已保留。请从有效备份恢复后继续。") from e


def restore_state_backup(root: Path) -> None:
    root = Path(root)
    with file_lock(root / "state.lock"):
        path = root / "state.json"
        backup = root / "state.json.bak"
        recovered = read_state(backup)
        if path.exists():
            try:
                read_state(path)
            except StateCorruptError:
                preserved = root / ("state.json.corrupt-" + uuid.uuid4().hex)
                with preserved.open("xb") as out:
                    out.write(path.read_bytes())
            else:
                raise ValueError("状态文件已恢复有效，请重新检查，避免覆盖新的设置。")
        recovered["pids"] = {}
        recovered["services_stopped"] = True
        recovered.pop("uninstalling", None)
        atomic_write_text(path, json.dumps(recovered, ensure_ascii=False, indent=2))


class State:
    def __init__(self, root: Path, data: dict):
        self.root = Path(root)
        self.data = data
        self._saved = copy.deepcopy(data)

    @classmethod
    def load(cls, root: Path) -> "State":
        path = Path(root) / "state.json"
        with _SAVE_LOCK:
            data = read_state(path) if path.exists() else {}
        data.setdefault("done", [])
        data.setdefault("secrets", {})
        return cls(root, data)

    def save(self, *, force: tuple[str, ...] = ()) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "state.json"
        with file_lock(self.root / "state.lock"):
            snapshot = copy.deepcopy(self.data)
            current = read_state(path) if path.exists() else {}
            current.setdefault("done", [])
            current.setdefault("secrets", {})
            merged = _merge_changes(current, self._saved, snapshot)
            for key in force:
                merged[key] = copy.deepcopy(snapshot[key])
            atomic_write_text(path, json.dumps(merged, ensure_ascii=False, indent=2), backup=True)
            self.data = _merge_changes(merged, snapshot, self.data)
            self._saved = copy.deepcopy(merged)

    def set_secret(self, name: str, value: str) -> None:
        self.data["secrets"][name] = protect(value)

    def get_secret(self, name: str) -> str:
        blob = self.data["secrets"].get(name)
        try:
            return unprotect(blob) if blob else ""
        except (OSError, ValueError, UnicodeError) as e:
            if name in ("dashboard_password", "onebot_token", "napcat_webui_token", "jwt_secret"):
                raise ValueError(f"内部连接凭据 {name} 无法解密，请使用原 Windows 用户打开；重填外部 API Key 无法恢复该凭据。") from e
            raise ValueError(f"保存的凭据 {name} 无法解密，请在原 Windows 用户下打开，或重新填写该凭据。") from e

    def secret_values(self) -> list[str]:
        # list() 先拷一份键：安装线程在这里脱敏日志时，接口线程可能正往里存新的 key
        values = []
        for name in list(self.data["secrets"]):
            try:
                values.append(self.get_secret(name))
            except (OSError, ValueError):
                continue
        return values

    def reload_pids(self) -> dict:
        """重新从磁盘读 pids。

        安装向导和状态页是两个进程，各自起着服务、各自往 state.json 里写 pids。只信自己内存里
        那份的话，另一个窗口刚起的进程在这边看着像没起，页面就会显示「她下线了」。
        """
        try:
            with _SAVE_LOCK:
                self.data["pids"] = State.load(self.root).data.get("pids", {})
                self._saved["pids"] = copy.deepcopy(self.data["pids"])
        except (OSError, ValueError):
            pass
        return self.data.get("pids", {})

    def mark_done(self, step: str) -> None:
        if step not in self.data["done"]:
            self.data["done"].append(step)
        self.save()

    def is_done(self, step: str) -> bool:
        return step in self.data["done"]
