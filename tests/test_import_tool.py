import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "vendor" / "dafeiyu-config-pack" / "tools" / "import_dafeiyu.py"


class ImportToolTest(unittest.TestCase):
    def test_import_result_json_fatal(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            pw = Path(d) / "pw.txt"
            pw.write_text("x", encoding="utf-8")
            answers = Path(d) / "a.json"
            answers.write_text(json.dumps({"base_url": f"http://127.0.0.1:{dead_port}", "username": "u",
                                           "password_file": str(pw), "owner_qq": "1", "memory": "0",
                                           "voice": "0"}), encoding="utf-8")
            result = Path(d) / "r.json"
            p = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--answers", str(answers),
                                "--result-json", str(result)], capture_output=True, timeout=120)
            self.assertNotEqual(p.returncode, 0)
            data = json.loads(result.read_text(encoding="utf-8"))
            self.assertIn("URLError", data["fatal"])  # 后台连不上时导入工具直接抛 URLError
            self.assertEqual(data["results"], [])


if __name__ == "__main__":
    unittest.main()
