"""确认服务停止后，由独立 PowerShell 程序清理目录并记录最终结果。"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import services
import shortcut
from safe_logging import append_log
from store import app_home, instance_home

SHORTCUT_NAME = "启动大肥鱼"


def owned_instance(root: Path, home: Path | None = None) -> Path | None:
    """旧版共享 home 无安装归属记录，不能据最近使用者推断它属于本安装。"""
    directory = instance_home(root, home)
    owner = directory / "owner.json"
    try:
        info = json.loads(owner.read_text(encoding="utf-8"))
        if (directory.resolve() == directory.absolute() and owner.resolve() == owner.absolute()
                and isinstance(info, dict) and info.get("root")
                and Path(info["root"]).resolve() == root.resolve()):
            return directory
    except (OSError, ValueError, TypeError):
        pass
    return None


def leftovers(root: Path, state, home: Path | None = None) -> list[Path]:
    root = Path(root).absolute()
    if (root.resolve() != root or root != state.root.resolve() or root.parent == root
            or not (root / "state.json").is_file()):
        raise ValueError("无法确认安装目录，已取消卸载。")
    own_home = app_home(home).resolve()
    if own_home.parent == own_home or own_home.is_relative_to(root):
        raise ValueError("无效的程序数据目录，已取消卸载。")
    private = owned_instance(root, home)
    return [root] + ([private] if private else [])


def uninstall(root: Path, state, home: Path | None = None, desktop: Path | None = None) -> Path:
    paths = leftovers(root, state, home)
    services.stop_all(state)
    shortcut.remove_shortcut(SHORTCUT_NAME, desktop=desktop, root=Path(root))
    state.data.pop("qq", None)
    state.save()
    work = Path(tempfile.mkdtemp(prefix="dafeiyu-uninstall-"))
    if any(work.resolve().is_relative_to(p) for p in paths):
        work.rmdir()
        raise ValueError("临时目录位于待卸载目录中，请关闭程序后从一键包重新打开再卸载。")
    script = work / "cleanup.ps1"
    # Windows PowerShell 5 需要 BOM 才能可靠读取中文脚本；路径只放 JSON，绝不拼接为命令。
    script.write_text(Path(__file__).with_suffix(".ps1").read_text(encoding="utf-8"), encoding="utf-8-sig")
    own_home = app_home(home).resolve()
    private = paths[1] if len(paths) > 1 else None
    others = []
    locks = list(own_home.glob("wizard*.lock")) + list(own_home.glob("waiting-*.lock"))
    if private:
        locks += list(private.glob("wizard*.lock"))
    for lock in locks:
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
            if (lock.resolve() == lock.absolute() and isinstance(info, dict) and info.get("root")
                    and Path(info["root"]).resolve() == Path(root).resolve() and int(info.get("pid", 0)) > 0):
                others.append(int(info["pid"]))
        except (OSError, ValueError, TypeError):
            continue
    profiles = [private / name for name in ("edge", "edge-install", "edge-status")] if private else []
    manifest = {"root": str(Path(root).resolve()), "home": str(own_home), "paths": [str(p) for p in paths],
                "parent_pid": os.getpid(), "window_pids": others,
                "profiles": [str(p) for p in profiles if p.resolve() == p.absolute()]}
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    report = work / "uninstall.log"
    try:
        proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=services.CREATE_NO_WINDOW | services.CREATE_NEW_PROCESS_GROUP)
    except OSError as error:
        append_log(report, f"FAILED 清理程序启动失败：{type(error).__name__} errno={error.errno}", root=root)
        raise RuntimeError("清理程序未能启动，文件尚未卸载。日志：" + str(report)) from None
    deadline = time.monotonic() + 10
    while not (work / "ready").exists():
        if proc.poll() is not None or time.monotonic() >= deadline:
            append_log(report, f"FAILED 清理程序未就绪：exit={proc.poll()}（None 表示等待超时）", root=root)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            raise RuntimeError("清理程序未能启动，文件尚未卸载。日志：" + str(report))
        time.sleep(0.05)
    try:
        state.data["uninstalling"] = True
        state.save()
        (work / "go").touch()
    except Exception:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        state.data["uninstalling"] = False
        state.save(force=("uninstalling",))
        raise
    return script
