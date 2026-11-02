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
from store import app_home, last_install  # noqa: E402

IDLE_EXIT_S = 20


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


def existing_instance(lock: Path) -> str | None:
    """已经开着一个向导就返回它的地址（带 token），没有返回 None。"""
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{info['port']}/api/ping"
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"X-Token": info["token"]})
        with urllib.request.urlopen(req, timeout=2):
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
    if root and (root / "state.json").exists():
        return "status", root
    return "install", None


def window_files(mode: str, home: Path) -> tuple[Path, Path]:
    """两种窗口各用一把锁、一个 Edge 配置目录。

    安装向导开着的时候点桌面图标，不该被拽到那个安装页面上；反过来也一样。
    """
    tag = "status" if mode == "status" else "install"
    return home / f"wizard-{tag}.lock", home / f"edge-{tag}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()

    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    mode, root = resolve_mode(args, home)
    lock, edge_dir = window_files(mode, home)
    if url := existing_instance(lock):
        open_window(url, edge_dir)
        return

    app = server.App(mode, root=root if mode == "status" else None)
    httpd = server.make_server(app, 0)
    port = httpd.server_address[1]
    lock.write_text(json.dumps({"port": port, "token": app.token}), encoding="utf-8")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if not args.no_window:
        open_window(f"http://127.0.0.1:{port}/?token={app.token}", edge_dir)

    # 窗口关掉（没有心跳）且没在安装，就退出；AstrBot / NapCat 是独立进程，不受影响
    try:
        while True:
            time.sleep(2)
            if app.quitting:  # 点了「退出」或「卸载」，不用等心跳超时
                break
            busy = app.installer is not None and app.installer.progress()["running"] \
                and app.installer.progress()["waiting"] is None
            if not busy and time.monotonic() - app.last_ping > IDLE_EXIT_S:
                break
    finally:
        httpd.shutdown()
        try:
            if json.loads(lock.read_text(encoding="utf-8")).get("port") == port:
                lock.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # pythonw 没有控制台，崩在这里会静默消失；落到日志里才查得到
        import traceback
        h = app_home()
        h.mkdir(parents=True, exist_ok=True)
        with open(h / "wizard.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S 启动失败：\n") + traceback.format_exc() + "\n")
        raise
