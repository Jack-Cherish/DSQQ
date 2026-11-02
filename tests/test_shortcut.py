import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import shortcut


class ShortcutTest(unittest.TestCase):
    def test_creates_lnk_with_target_args_and_icon(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()  # 临时目录可能是 8.3 短路径，.lnk 里存的是长路径
            target = d / "pythonw.exe"
            target.write_bytes(b"")
            icon = d / "icon.ico"
            icon.write_bytes(b"")
            # 参数里带引号和空格，确认原样写进去
            args = f'"{d / "app" / "main.py"}" --status --root "{d / "大肥 鱼"}"'
            lnk = shortcut.create_shortcut(target, args, "启动大肥鱼", icon, desktop=d)
            self.assertEqual(lnk, d / "启动大肥鱼.lnk")
            self.assertTrue(lnk.exists())
            env = dict(os.environ, LNK=str(lnk))
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                 "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
                 "$s.TargetPath; $s.Arguments; $s.IconLocation; $s.WorkingDirectory"],
                capture_output=True, env=env, check=True).stdout.decode("utf-8").splitlines()
            self.assertEqual(Path(out[0]), target)
            self.assertEqual(out[1], args)
            self.assertTrue(out[2].startswith(str(icon)))
            self.assertEqual(Path(out[3]), d)

    def test_remove_deletes_lnk(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            lnk = shortcut.create_shortcut(d / "a.exe", "", "启动大肥鱼", d / "i.ico", desktop=d)
            shortcut.remove_shortcut("启动大肥鱼", desktop=d)
            self.assertFalse(lnk.exists())

    def test_remove_missing_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            shortcut.remove_shortcut("不存在的", desktop=Path(d))

    def test_desktop_dir_from_system(self):
        self.assertTrue(shortcut.desktop_dir().is_dir())

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            shortcut.create_shortcut(d / "a.exe", "", "x", d / "i.ico", desktop=d)
            lnk = shortcut.create_shortcut(d / "b.exe", "", "x", d / "i.ico", desktop=d)
            self.assertTrue(lnk.exists())


if __name__ == "__main__":
    unittest.main()
