import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "build_script", Path(__file__).resolve().parents[1] / "build" / "build.py")
build = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build)


class BuildTest(unittest.TestCase):
    def test_unzip_checks_all_paths_before_writing(self):
        unsafe = ("../escaped.txt", "safe/../../escaped.txt", "safe\\..\\escaped.txt",
                  "/absolute.txt", "D:/absolute.txt", "D:relative.txt", "//host/share/file.txt",
                  "\\\\host\\share\\file.txt", "file.txt:stream", "safe/.. /escaped.txt", "NUL")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, dest = root / "bad.zip", root / "out"
            for name in unsafe:
                with self.subTest(name=name):
                    with zipfile.ZipFile(archive, "w") as z:
                        z.writestr("safe.txt", "safe")
                        z.writestr(name, "unsafe")
                    # 旧实现也不能在测试时写入真正的绝对路径或网络共享。
                    with mock.patch("builtins.open", side_effect=AssertionError("wrote before validation")):
                        with self.assertRaises(ValueError):
                            build.unzip(archive, dest)
                    self.assertFalse((dest / "safe.txt").exists())

    def test_unzip_rejects_traversal_before_stripping_top_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, dest = root / "bad.zip", root / "out"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../escaped.txt", "unsafe")
            with self.assertRaises(ValueError):
                build.unzip(archive, dest)
            self.assertFalse((root / "escaped.txt").exists())
            self.assertFalse(dest.exists())

    def test_unzip_rejects_zip_symlink_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, dest = root / "bad.zip", root / "out"
            link = zipfile.ZipInfo("release/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("release/safe.txt", "safe")
                z.writestr(link, "../outside")
            with self.assertRaises(ValueError):
                build.unzip(archive, dest)
            self.assertFalse(dest.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_unzip_rejects_existing_junction_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, dest, outside = root / "bad.zip", root / "out", root / "outside"
            dest.mkdir()
            outside.mkdir()
            subprocess.run(["cmd", "/c", "mklink", "/J", str(dest / "link"), str(outside)],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("release/safe.txt", "safe")
                z.writestr("release/link/escaped.txt", "unsafe")
            with self.assertRaises(ValueError):
                build.unzip(archive, dest)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((dest / "safe.txt").exists())

    def test_unzip_rejects_existing_hardlink_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive, dest, outside = root / "bad.zip", root / "out", root / "outside.txt"
            dest.mkdir()
            outside.write_text("keep", encoding="utf-8")
            os.link(outside, dest / "linked.txt")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("release/safe.txt", "safe")
                z.writestr("release/linked.txt", "unsafe")
            with self.assertRaises(ValueError):
                build.unzip(archive, dest)
            self.assertEqual(outside.read_text("utf-8"), "keep")
            self.assertFalse((dest / "safe.txt").exists())

    def test_unzip_preserves_files_and_strips_only_shared_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / "good.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("release/", "")
                z.writestr("release/main.py", "main")
                z.writestr("release/nested/file.txt", "text")
            build.unzip(archive, root / "one")
            self.assertEqual((root / "one/main.py").read_text(), "main")
            self.assertEqual((root / "one/nested/file.txt").read_text(), "text")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("main.py", "main")
                z.writestr("nested/file.txt", "text")
            build.unzip(archive, root / "two")
            self.assertEqual((root / "two/main.py").read_text(), "main")
            self.assertEqual((root / "two/nested/file.txt").read_text(), "text")

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
            for name in ("data_v4.db", "data_v4.db-wal", "data_v4.db-shm", "cmd_config.json", "cmd_config.json.bak"):
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

    def test_scan_for_secrets_includes_json_backups(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json.bak"
            path.write_text('{"key": "sk-build"}', encoding="utf-8")
            self.assertEqual(build.scan_for_secrets(Path(d), ["sk-build"]), [path])


if __name__ == "__main__":
    unittest.main()
