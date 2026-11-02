import sys
from pathlib import Path

# 被测代码在 app/ 下，直接按模块名导入（和打包后 app\main.py 的运行方式一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
