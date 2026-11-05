"""先完整落盘再替换，避免配置写到一半损坏原文件。"""
import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, backup: bool = False) -> None:
    """原子写 UTF-8；backup 保留调用方已验证的上一版内容到同名 .bak。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            staged = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if backup and path.exists():
            atomic_write_text(path.with_name(path.name + ".bak"), path.read_bytes().decode("utf-8"))
        for attempt in range(20):
            try:
                os.replace(staged, path)
                break
            except PermissionError:  # 杀毒软件可能短暂占用目标文件。
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
