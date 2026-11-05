"""在桌面放「启动大肥鱼」快捷方式。用系统自带的 WScript.Shell，不加依赖。"""

import ctypes
import json
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


def _arguments(command: str) -> list[str]:
    """Windows 参数中的引号/转义必须按快捷方式实际执行时的规则解释。"""
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    argv = split("shortcut " + command, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError()
    try:
        return list(argv[:count.value])[1:]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


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


def remove_shortcut(name: str, desktop: Path | None = None, *, root: Path | None = None) -> None:
    """仅删除目标和 --root 均属于本安装的快捷方式；未知归属保留。"""
    if root is None:
        return
    link = desktop_dir(desktop) / f"{name}.lnk"
    if not link.is_file():
        return
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SC_LINK);"
              "@{target=$s.TargetPath; args=$s.Arguments} | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, env=dict(os.environ, SC_LINK=str(link)), timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0:
            return
        info = json.loads(out.stdout.decode("utf-8"))
        target = Path(info["target"])
        args = _arguments(info["args"])
        roots = []
        for index, argument in enumerate(args):
            if argument == "--":
                break
            if argument == "--root" and index + 1 < len(args):
                roots.append(Path(args[index + 1]))
            elif argument.startswith("--root="):
                roots.append(Path(argument[7:]))
        owned = (target.is_absolute() and target.resolve().is_relative_to(root.resolve())
                 and len(roots) == 1 and roots[0].is_absolute() and roots[0].resolve() == root.resolve())
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return
    if owned:
        link.unlink(missing_ok=True)


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
