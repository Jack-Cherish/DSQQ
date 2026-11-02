import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main as entry
from store import remember_install


def args(status=False, root=None):
    return argparse.Namespace(status=status, root=root, no_window=True)


class ResolveModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.root = Path(self.tmp.name) / "DaFeiYu"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def install(self):
        (self.root / "state.json").write_text("{}", encoding="utf-8")
        remember_install(self.root, self.home)

    def test_fresh_machine_gets_the_wizard(self):
        self.assertEqual(entry.resolve_mode(args(), self.home), ("install", None))

    def test_installed_machine_gets_the_status_page(self):
        """双击一键包时已经装过，就该直接看到状态页，而不是又让人选一次安装位置。"""
        self.install()
        self.assertEqual(entry.resolve_mode(args(), self.home), ("status", self.root))

    def test_shortcut_always_gets_the_status_page(self):
        self.install()
        self.assertEqual(entry.resolve_mode(args(status=True, root=str(self.root)), self.home),
                         ("status", self.root))

    def test_install_dir_deleted_falls_back_to_the_wizard(self):
        remember_install(self.root, self.home)  # 记着，但目录里的 state.json 已经没了
        self.assertEqual(entry.resolve_mode(args(), self.home), ("install", None))

    def test_explicit_root_wins(self):
        self.install()
        other = Path(self.tmp.name) / "其他"
        other.mkdir()
        (other / "state.json").write_text("{}", encoding="utf-8")
        self.assertEqual(entry.resolve_mode(args(status=True, root=str(other)), self.home), ("status", other))


class LockTest(unittest.TestCase):
    def test_install_and_status_use_different_locks(self):
        """安装向导开着时点桌面图标，不能被拽到安装页面上——两把锁互不相干。"""
        home = Path("home")
        self.assertEqual(entry.window_files("install", home), (home / "wizard-install.lock", home / "edge-install"))
        self.assertEqual(entry.window_files("status", home), (home / "wizard-status.lock", home / "edge-status"))
        self.assertNotEqual(entry.window_files("install", home)[0], entry.window_files("status", home)[0])


if __name__ == "__main__":
    unittest.main()
