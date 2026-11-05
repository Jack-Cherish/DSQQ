"""独立排障日志读进程：向导退出后继续排空服务管道，绝不写原始输出。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from safe_logging import Sanitizer, append_log, iter_lines, secret_values
from store import State


def main():
    root, state_root, name, ready = sys.argv[1:]
    root, state_root = Path(root), Path(state_root)
    try:
        secrets = secret_values(State.load(state_root))
    except (OSError, ValueError):
        secrets = []
    path = root / "logs" / f"{name}.log"
    if not append_log(path, "=== DA FEI YU PROCESS START ===", secrets=secrets, root=root):
        return 1
    # pythonw 的 sys.stdin 在无控制台环境可能为 None；显式打开继承的管道句柄。
    stream = sys.stdin.buffer if sys.stdin is not None else None
    if stream is None:
        import ctypes
        import msvcrt
        import os
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        stream = os.fdopen(msvcrt.open_osfhandle(kernel.GetStdHandle(-10), os.O_RDONLY | os.O_BINARY), "rb")
    Path(ready).touch()
    parser = Sanitizer(secrets, root)
    with stream:
        for raw in iter_lines(stream):
            text = parser.line(raw)
            if text:
                append_log(path, text, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
