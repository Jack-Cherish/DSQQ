import tempfile
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "installed"
        self.source = Path(self.tmp.name) / "package"
        for root in (self.root, self.source):
            (root / "astrbot" / "astrbot" / "utils").mkdir(parents=True)
            (root / "runtime" / "python").mkdir(parents=True)
            (root / "astrbot" / "main.py").write_text("entry", encoding="utf-8")
            (root / "astrbot" / "pyproject.toml").write_text("version 4.29.3", encoding="utf-8")
            (root / "astrbot" / "astrbot" / "__init__.py").write_text("", encoding="utf-8")
            (root / "astrbot" / "astrbot" / "utils" / "http_ssl_common.py").write_text("", encoding="utf-8")
            (root / "runtime" / "python" / "python.exe").write_bytes(b"python")
            (root / "runtime" / "python" / "python312._pth").write_text(
                "python312.zip\n.\n..\\..\\astrbot\nimport site\n", encoding="utf-8")
        (self.root / "astrbot" / "data").mkdir()
        (self.root / "astrbot" / "data" / "history.db").write_text("keep", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_core_is_reported_even_when_main_exists(self):
        (self.root / "astrbot" / "astrbot" / "__init__.py").unlink()
        report = diagnostics.inspect(self.root)
        self.assertIn("astrbot_core_missing", [item["code"] for item in report["items"]])

    def test_reports_unresolved_stop_access_denial_from_services_log(self):
        logs = self.root / 'logs'
        logs.mkdir()
        path = logs / 'services.log'
        path.write_text('停止 napcat：PID=42，返回码=128\n原因: 拒绝访问。\n停止失败：napcat PID=42 未退出\n', encoding='utf-8')
        self.assertIn('stop_permission_denied', [x['code'] for x in diagnostics.inspect(self.root)['items']])
        with path.open('a', encoding='utf-8') as out:
            out.write('已确认 napcat 停止\n')
        self.assertNotIn('stop_permission_denied', [x['code'] for x in diagnostics.inspect(self.root)['items']])

    def test_dependency_error_reports_only_safe_module_name(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "astrbot.log").write_text(
            "secret user conversation\nModuleNotFoundError: No module named 'certifi'\n", encoding="utf-8")
        report = diagnostics.inspect(self.root)
        item = next(x for x in report["items"] if x["code"] == "python_dependency_missing")
        self.assertIn("certifi", item["message"])
        self.assertNotIn("secret user conversation", str(report))

    def test_reported_astrbot_import_error_is_identified(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "astrbot.log").write_text(
            "Traceback (most recent call last):\n"
            "  File \"D:\\DaFeiYu\\astrbot\\runtime_bootstrap.py\", line 7, in <module>\n"
            "    from astrbot.utils.http_ssl_common import build_ssl_context_with_certifi\n"
            "ModuleNotFoundError: No module named 'astrbot'\n", encoding="utf-8")
        report = diagnostics.inspect(self.root)
        self.assertIn("astrbot_import_error", [item["code"] for item in report["items"]])

    def test_missing_import_path_is_reported(self):
        (self.root / "runtime" / "python" / "python312._pth").write_text("python312.zip\n.\n", encoding="utf-8")
        report = diagnostics.inspect(self.root)
        self.assertIn("astrbot_path_missing", [item["code"] for item in report["items"]])

    def test_restore_core_keeps_database_and_settings(self):
        (self.root / "astrbot" / "astrbot" / "__init__.py").unlink()
        (self.root / "runtime" / "python" / "python312._pth").write_text(".\n", encoding="utf-8")
        diagnostics.restore_core(self.root, self.source)
        self.assertTrue((self.root / "astrbot" / "astrbot" / "__init__.py").is_file())
        self.assertIn("..\\..\\astrbot", (self.root / "runtime" / "python" / "python312._pth").read_text("utf-8"))
        self.assertEqual((self.root / "astrbot" / "data" / "history.db").read_text("utf-8"), "keep")

    def test_wrong_package_version_refused_without_changes(self):
        (self.source / "astrbot" / "pyproject.toml").write_text("version 9", encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostics.restore_core(self.root, self.source)
        self.assertTrue((self.root / "astrbot" / "astrbot" / "__init__.py").is_file())
        self.assertEqual((self.root / "astrbot" / "data" / "history.db").read_text("utf-8"), "keep")

    def test_move_failure_leaves_original_core(self):
        (self.root / "runtime" / "python" / "python312._pth").write_text(".\\n", encoding="utf-8")
        with mock.patch.object(diagnostics.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                diagnostics.restore_core(self.root, self.source)
        self.assertTrue((self.root / "astrbot" / "astrbot" / "__init__.py").is_file())

    def test_partial_copy_does_not_leave_corrupt_missing_file(self):
        dest = self.root / "astrbot" / "astrbot" / "__init__.py"
        dest.unlink()
        def fail_after_write(src, out):
            out.write(b"partial")
            raise OSError("disk full")
        with mock.patch.object(diagnostics.shutil, "copyfileobj", side_effect=fail_after_write):
            with self.assertRaises(OSError):
                diagnostics.restore_core(self.root, self.source)
        self.assertFalse(dest.exists())

    def test_copy_failure_leaves_original_core(self):
        with mock.patch.object(diagnostics.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                diagnostics.restore_core(self.root, self.source)
        self.assertTrue((self.root / "astrbot" / "astrbot" / "__init__.py").is_file())
        self.assertEqual((self.root / "astrbot" / "data" / "history.db").read_text("utf-8"), "keep")

    def test_repair_refuses_core_junction_before_writing_other_directory(self):
        core = self.root / "astrbot" / "astrbot"
        other = self.root.parent / "other-core"
        core.rename(other)
        (other / "__init__.py").unlink()
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                        'New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET | Out-Null'],
                       env=dict(os.environ, TEST_LINK=str(core), TEST_TARGET=str(other)),
                       check=True, capture_output=True, creationflags=diagnostics.services.CREATE_NO_WINDOW)
        with self.assertRaisesRegex(ValueError, '链接'):
            diagnostics.restore_core(self.root, self.source)
        self.assertFalse((other / '__init__.py').exists())


if __name__ == "__main__":
    unittest.main()
