"""一键包入口。

安装：pythonw app\\main.py            （自解压后从临时目录运行）
状态：pythonw app\\main.py --status --root D:\\DaFeiYu   （桌面快捷方式）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402
from installer import _tree_hash  # noqa: E402
from store import State, app_home, instance_home, last_install  # noqa: E402
from fileio import atomic_write_text  # noqa: E402
from safe_logging import append_log  # noqa: E402

IDLE_EXIT_S = 600
BIND_IDLE_EXIT_S = 600
CLOSED_EXIT_S = 20


def should_idle_exit(workers, elapsed: float, *, closed=False) -> bool:
    statuses = [worker.progress() for worker in workers]
    if any(status["running"] and status["waiting"] is None for status in statuses):
        return False
    binding = any(status["running"] and status["waiting"] == "official_bound" for status in statuses)
    return elapsed > (BIND_IDLE_EXIT_S if binding else CLOSED_EXIT_S if closed else IDLE_EXIT_S)


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.exists():
                return str(exe)
    return shutil.which("msedge")


def open_window(url: str, profile_dir: Path) -> None:
    edge = find_edge()
    if edge:
        # 独立的 Edge 配置目录：窗口没有地址栏，也不会和用户自己的 Edge 窗口混在一起
        subprocess.Popen([edge, f"--app={url}", "--window-size=1000,740", f"--user-data-dir={profile_dir}",
                          "--no-first-run", "--disable-features=Translate"])
    else:
        webbrowser.open(url)


def existing_instance(lock: Path, *, version=None) -> str | None:
    """已经开着一个向导就返回它的地址（带 token），没有返回 None。"""
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{info['port']}/api/ping"
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"X-Token": info["token"]})
        with urllib.request.urlopen(req, timeout=2):
            pass
        if version and info.get("version") != version:
            # 空闲窗口才能交接；解压、导入或启动期间保留原实例。
            try:
                base = f"http://127.0.0.1:{info['port']}"
                headers = {"X-Token": info["token"]}
                with urllib.request.urlopen(urllib.request.Request(base + "/api/progress", headers=headers), timeout=2) as response:
                    progress = json.load(response)
                idle = progress.get("running") is True and progress.get("waiting") == "official_bound"
                if progress.get("running") is False:
                    with urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=headers), timeout=3) as response:
                        status = json.load(response)
                    idle = isinstance(status.get("astrbot"), bool) and status.get("starting") is False
                if idle:
                    request = urllib.request.Request(base + "/api/quit", data=b"{}", headers=headers)
                    with urllib.request.urlopen(request, timeout=90) as response:
                        stopped = json.load(response).get("ok") is True
                    if stopped:
                        # A successful stop response precedes the old controller's exit.
                        if not info.get("pid"):
                            return None
                        deadline = time.monotonic() + 6
                        while time.monotonic() < deadline:
                            try:
                                with urllib.request.urlopen(req, timeout=0.3):
                                    pass
                            except OSError:
                                return None
                            time.sleep(0.1)
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # A failed stop must keep the old instance, not create a second writer.
        return f"http://127.0.0.1:{info['port']}/?token={info['token']}"
    except Exception:
        return None


def resolve_mode(args, home: Path) -> tuple[str, Path | None]:
    """这次该开状态页还是安装向导。

    双击一键包时不带参数：装过就直接进状态页，别让人以为要重装一遍。桌面快捷方式带 --status。
    """
    if args.root:
        root = Path(args.root)
    else:
        last = last_install(home)
        root = Path(last) if last else None
    try:
        if root and ((root / "state.json").exists() or (root / "state.json.bak").exists() or (root / "astrbot" / "data" / "cmd_config.json").exists()):
            data = State.load(root).data
            return ("status" if "login" in data.get("done", []) else "install"), root
    except (OSError, ValueError):
        return "status", root
    return "install", None


def window_files(mode: str, home: Path, root: Path | None = None) -> tuple[Path, Path]:
    """One controller and browser profile per installation, including unfinished installs."""
    if root is not None:
        home = instance_home(root, home)
    return home / "wizard.lock", home / "edge"


def find_existing(root: Path | None, home: Path, *, version=None) -> str | None:
    directory = instance_home(root, home) if root is not None else home
    locks = [directory / name for name in ("wizard.lock", "wizard-install.lock", "wizard-status.lock")]
    if root is not None:
        locks += [home / name for name in ("wizard.lock", "wizard-install.lock", "wizard-status.lock")]
    seen = set()
    for lock in locks:
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
            if not isinstance(info, dict) or info.get("pid") == os.getpid():
                continue
            if root is not None and (not info.get("root") or Path(info["root"]).resolve() != root.resolve()):
                continue
        except (OSError, ValueError, TypeError):
            continue
        identity = json.dumps([info.get("pid"), info.get("port"), info.get("token")], sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        if url := existing_instance(lock, version=version):
            return url
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--stop-elevated", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.stop_elevated:
        if not args.root:
            ap.error("--stop-elevated 需要 --root")
        import services
        services.stop_all(State.load(Path(args.root)))
        return

    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    mode, root = resolve_mode(args, home)
    lock, edge_dir = window_files(mode, home, root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if root is not None:
        atomic_write_text(lock.parent / "owner.json", json.dumps({"root": str(root.resolve())}))
    version = _tree_hash(Path(__file__).resolve().parent)
    if url := find_existing(root, home, version=version):
        open_window(url, edge_dir)
        return

    app = server.App(mode, root=root, exclusive=True, find_instance=lambda selected: find_existing(selected, home))
    if app.recovery and app.recovery.get("busy"):
        # Do not overwrite the registry/profile belonging to the active controller.
        lock = home / f"waiting-{os.getpid()}.lock"
    httpd = server.make_server(app, 0)
    port = httpd.server_address[1]
    lock_info = {"port": port, "token": app.token, "pid": os.getpid(), "root": str(root) if root else "", "version": version}
    atomic_write_text(lock, json.dumps(lock_info))
    owned_locks = {lock}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if not args.no_window:
        open_window(f"http://127.0.0.1:{port}/?token={app.token}", edge_dir)

    # 窗口关掉（没有心跳）且没在安装，就退出；AstrBot / NapCat 是独立进程，不受影响
    last_tick = time.monotonic()
    try:
        while True:
            time.sleep(2)
            now = time.monotonic()
            if now - last_tick > 30:  # 电脑休眠恢复后，先给浏览器恢复心跳的时间。
                app.last_ping = now
            last_tick = now
            if app.quitting:  # 点了「退出」或「卸载」，不用等心跳超时
                break
            if app.root and lock_info["root"] != str(app.root):
                lock_info["root"] = str(app.root)
                for owned in owned_locks:
                    atomic_write_text(owned, json.dumps(lock_info))
            if app.state and not app.recovery:
                selected_lock, _ = window_files(mode, home, app.root)
                if selected_lock not in owned_locks:
                    selected_lock.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(selected_lock.parent / "owner.json", json.dumps({"root": str(app.root.resolve())}))
                    atomic_write_text(selected_lock, json.dumps(lock_info))
                    owned_locks.add(selected_lock)
            workers = [worker for worker in (app.installer, app._starter) if worker]
            try:
                uninstalling = app.root and (not app.recovery or app.recovery.get("busy")) and State.load(app.root).data.get("uninstalling")
            except (OSError, ValueError):
                uninstalling = False
            if uninstalling:
                if all([worker.cancel(timeout=1) for worker in workers]):
                    break
            elapsed = time.monotonic() - app.last_ping
            if not app._lock.locked() and should_idle_exit(workers, elapsed, closed=app.window_closed):
                try:
                    app.log_error(f"向导退出：窗口心跳超时（{elapsed:.0f} 秒）；后台服务不受影响")
                except Exception:
                    pass
                break
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()
        for owned in owned_locks:
            try:
                if json.loads(owned.read_text(encoding="utf-8")).get("port") == port:
                    owned.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # pythonw 没有控制台，崩在这里会静默消失；落到日志里才查得到
        import traceback
        h = app_home()
        append_log(h / "wizard.log", "启动失败：\n" + traceback.format_exc())
        raise
