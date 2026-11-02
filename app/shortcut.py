"""在桌面放「启动大肥鱼」快捷方式。用系统自带的 WScript.Shell，不加依赖。"""

import os
import subprocess
from pathlib import Path

# 值走环境变量，路径里的引号、空格、中文不会把命令拼坏
_PS = (
    "$d = if ($env:SC_DESKTOP) { $env:SC_DESKTOP } else { [Environment]::GetFolderPath('Desktop') };"
    "$p = Join-Path $d ($env:SC_NAME + '.lnk');"
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p);"
    "$s.TargetPath = $env:SC_TARGET; $s.Arguments = $env:SC_ARGS;"
    "$s.WorkingDirectory = $env:SC_WORKDIR; $s.IconLocation = $env:SC_ICON + ',0';"
    "$s.Save();"
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $p"
)


_PS_DESKTOP = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
               "[Environment]::GetFolderPath('Desktop')")


def desktop_dir(desktop: Path | None = None) -> Path:
    """桌面目录。默认问系统要（会跟着 OneDrive 重定向）。"""
    if desktop:
        return Path(desktop)
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_DESKTOP],
                         capture_output=True, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise OSError("找不到桌面目录")
    return Path(out.stdout.decode("utf-8", "replace").strip().splitlines()[-1])


def remove_shortcut(name: str, desktop: Path | None = None) -> None:
    """删掉桌面快捷方式。本来就没有也算成功。"""
    (desktop_dir(desktop) / f"{name}.lnk").unlink(missing_ok=True)


def create_shortcut(target: Path, args: str, name: str, icon: Path, desktop: Path | None = None) -> Path:
    """建（或覆盖）桌面快捷方式，返回 .lnk 路径。desktop 只给测试用；默认取系统桌面（含 OneDrive 重定向）。"""
    env = dict(os.environ, SC_TARGET=str(target), SC_ARGS=args, SC_NAME=name,
               SC_WORKDIR=str(Path(target).parent), SC_ICON=str(icon), SC_DESKTOP=str(desktop or ""))
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
                         capture_output=True, env=env, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise OSError("建桌面快捷方式失败：" + out.stderr.decode("utf-8", "replace").strip()[-300:])
    return Path(out.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
