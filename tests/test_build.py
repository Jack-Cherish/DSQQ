import importlib.util
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_script", Path(__file__).resolve().parents[1] / "build" / "build.py")
build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build)


class BuildTest(unittest.TestCase):
    def test_sfx_config_text(self):
        text = build.sfx_config_text()
        self.assertTrue(text.startswith(";!@Install@!UTF-8!"))
        self.assertIn('Title="大肥鱼一键包"', text)
        self.assertIn(r'RunProgram="runtime\\python\\pythonw.exe app\\main.py"', text)
        self.assertTrue(text.rstrip().endswith(";!@InstallEnd@!"))

    def test_clean_removes_db_sidecar_files(self):
        """*.db-wal / *.db-shm 留着预热期的数据，删主库不删它们等于没清。"""
        with tempfile.TemporaryDirectory() as d:
            stage = Path(d)
            data = stage / "astrbot" / "data"
            data.mkdir(parents=True)
            for name in ("data_v4.db", "data_v4.db-wal", "data_v4.db-shm", "cmd_config.json"):
                (data / name).write_text("{}", encoding="utf-8")
            build.clean(stage, [])
            self.assertEqual(list(data.iterdir()), [])

    def test_scan_for_secrets_flags_key(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a").mkdir()
            (root / "a" / "conf.json").write_text('{"key": "sk-build"}', encoding="utf-8")
            (root / "clean.json").write_text('{"key": ""}', encoding="utf-8")
            (root / "note.txt").write_text("sk-build", encoding="utf-8")  # 只扫 json
            self.assertEqual(build.scan_for_secrets(root, ["sk-build"]), [root / "a" / "conf.json"])


if __name__ == "__main__":
    unittest.main()
