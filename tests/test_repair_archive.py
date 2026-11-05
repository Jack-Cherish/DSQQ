import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import diagnostics


@unittest.skipUnless(os.environ.get("DAFEIYU_7ZR_TEST"), "set DAFEIYU_7ZR_TEST to a local 7zr.exe")
class RepairArchiveTest(unittest.TestCase):
    def test_actual_7z_archive_restores_only_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, boot = base / "src", base / "installed", base / "boot"
            for root in (source, target):
                (root / "astrbot" / "astrbot" / "utils").mkdir(parents=True)
                (root / "runtime" / "python").mkdir(parents=True)
                (root / "astrbot" / "main.py").write_text("entry", encoding="utf-8")
                (root / "astrbot" / "pyproject.toml").write_text("version 4.29.3", encoding="utf-8")
                (root / "astrbot" / "astrbot" / "__init__.py").write_text("core", encoding="utf-8")
                (root / "astrbot" / "astrbot" / "utils" / "http_ssl_common.py").write_text("", encoding="utf-8")
                (root / "runtime" / "python" / "python312._pth").write_text(
                    ".\n..\\..\\astrbot\nimport site\n", encoding="utf-8")
            (target / "astrbot" / "astrbot" / "__init__.py").unlink()
            (target / "astrbot" / "data").mkdir()
            (target / "astrbot" / "data" / "history.db").write_text("keep", encoding="utf-8")
            boot.mkdir()
            seven = boot / "7zr.exe"
            shutil.copy2(os.environ["DAFEIYU_7ZR_TEST"], seven)
            subprocess.run([str(seven), "a", str(boot / "payload.7z"), ".\\*"],
                           cwd=source, check=True, capture_output=True)
            diagnostics.repair(target, boot)
            self.assertEqual((target / "astrbot" / "astrbot" / "__init__.py").read_text("utf-8"), "core")
            self.assertEqual((target / "astrbot" / "data" / "history.db").read_text("utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
