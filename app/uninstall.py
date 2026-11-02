"""彻底卸载：停服务、删桌面快捷方式、退掉小号登录，再交给一个清理脚本删掉剩下的目录。

一键包不写注册表、不设环境变量、不加开机自启，所以痕迹就是这几个目录加一个桌面快捷方式。

向导自己就跑在安装目录里的 python 上，没法删掉自己脚下的文件：所以把 rmdir 交给临时目录里的
一个 .bat，它等我们这个进程退出后再动手，最后把自己也删掉。
"""
import os
import subprocess
import tempfile
from pathlib import Path

import services
import shortcut
from store import app_home

SHORTCUT_NAME = "启动大肥鱼"

_BAT = r"""@echo off
chcp 65001 >nul
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
ping -n 3 127.0.0.1 >nul
rem Edge 窗口关掉后还会占一会儿 home\edge，多试几次
for /l %%i in (1,1,10) do (
{removes}
{checks} goto done
  ping -n 3 127.0.0.1 >nul
)
:done
del "%~f0"
"""

_REMOVE = '  rmdir /s /q "{path}" 2>nul'


def leftovers(root: Path, state, home: Path | None = None) -> list[Path]:
    """卸载要清掉的目录。

    除了安装目录和 %LOCALAPPDATA%\\DaFeiYu，NapCat 登小号时还会在用户的 QQ 数据目录里留下
    按号存的那份数据和一个 NapCat 自己的缓存目录，留着就不算卸干净。nt_qq\\global 是主人自己
    QQ 客户端共用的，和主人的号一样绝对不碰。
    """
    paths = [Path(root), app_home(home)]
    layout = services.Layout(root)
    qq, owner = str(state.data.get("qq") or ""), str(state.data.get("owner_qq") or "")
    for tencent in services.tencent_roots(layout):
        paths.append(tencent / "NapCat")
        if qq and qq != owner:
            paths.append(tencent / qq)
    return [p for p in paths if p.exists()]


def uninstall(root: Path, state, home: Path | None = None, desktop: Path | None = None) -> Path:
    """停掉所有服务、删快捷方式和小号登录数据，启动清理脚本。返回清理脚本路径。"""
    paths = leftovers(root, state, home)  # 先算好：清理脚本要用到日志里的 QQ 数据目录路径
    services.stop_all(state)
    try:
        shortcut.remove_shortcut(SHORTCUT_NAME, desktop=desktop)
    except OSError:
        pass  # 快捷方式删不掉不该挡住卸载
    services.forget_qq_account(services.Layout(root), state.data.get("qq", ""), state.data.get("owner_qq", ""))
    bat = Path(tempfile.gettempdir()) / "dafeiyu-uninstall.bat"
    bat.write_text(_BAT.format(pid=os.getpid(),
                               removes="\n".join(_REMOVE.format(path=p) for p in paths),
                               checks=" ".join(f'if not exist "{p}"' for p in paths) or "if 1==1"),
                   encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=bat.parent, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=services.CREATE_NO_WINDOW | services.CREATE_NEW_PROCESS_GROUP)
    return bat
