import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import services
import shortcut
import uninstall
from store import State


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "DaFeiYu"
        self.root.mkdir()
        (self.root / "state.json").write_text("{}", encoding="utf-8")
        self.home = self.base / "home"
        self.home.mkdir()
        self.desktop = self.base / "desktop"
        self.desktop.mkdir()
        self.state = State.load(self.root)
        self.state.data["pids"] = {"astrbot": 1, "napcat": 2}
        self.state.data["qq"] = "374451537"
        self.state.data["owner_qq"] = "1960074081"

        # 假的「Tencent Files」：真目录是用户自己 QQ 的数据，测试绝对不能碰
        self.tencent = self.base / "fake-tencent" / "Tencent Files"
        self.login = self.tencent / "nt_qq" / "global" / "nt_data" / "Login"
        self.login.mkdir(parents=True)
        for qq in ("374451537", "1960074081"):
            (self.tencent / qq).mkdir()
            (self.login / f".{qq}").write_bytes(b"")
        (self.tencent / "NapCat" / "temp").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_uninstall(self, popen, tencent=True):
        with mock.patch.object(services, "is_running", return_value=False), \
             mock.patch.object(services, "tencent_roots", return_value=[self.tencent] if tencent else []), \
             mock.patch.object(uninstall.subprocess, "Popen", popen):
            return uninstall.uninstall(self.root, self.state, home=self.home, desktop=self.desktop)

    def test_stops_services_and_removes_shortcut(self):
        lnk = shortcut.create_shortcut(self.base / "a.exe", "", uninstall.SHORTCUT_NAME,
                                       self.base / "i.ico", desktop=self.desktop)
        popen = mock.MagicMock()
        self.run_uninstall(popen)
        self.assertEqual(self.state.data["pids"], {})
        self.assertFalse(lnk.exists())

    def test_script_targets_root_and_home(self):
        popen = mock.MagicMock()
        bat = self.run_uninstall(popen)
        text = bat.read_text(encoding="utf-8")
        self.assertIn(f'rmdir /s /q "{self.root}"', text)
        self.assertIn(f'rmdir /s /q "{self.home}"', text)
        self.assertIn("del \"%~f0\"", text)  # 脚本删完自己也走
        (argv,), kw = popen.call_args
        self.assertEqual(argv, ["cmd", "/c", str(bat)])
        self.assertTrue(kw["creationflags"] & services.CREATE_NO_WINDOW)
        bat.unlink(missing_ok=True)

    def test_script_also_clears_qq_leftovers(self):
        """小号在 QQ 数据目录里的数据和 NapCat 的缓存目录，留着就不算卸干净。"""
        bat = self.run_uninstall(mock.MagicMock())
        text = bat.read_text(encoding="utf-8")
        self.assertIn(f'rmdir /s /q "{self.tencent / "374451537"}"', text)
        self.assertIn(f'rmdir /s /q "{self.tencent / "NapCat"}"', text)
        # 主人自己的号和所有号共用的 global 绝对不能进清理名单
        self.assertNotIn("1960074081", text)
        self.assertNotIn("global", text)
        bat.unlink(missing_ok=True)

    def test_logs_out_the_account_immediately(self):
        """登录标记要当场删掉：卸载中途被打断也不能让小号还能自己登回来。"""
        self.run_uninstall(mock.MagicMock())
        self.assertFalse((self.login / ".374451537").exists())
        self.assertTrue((self.login / ".1960074081").exists())

    def test_without_qq_data_dir_only_cleans_own_files(self):
        bat = self.run_uninstall(mock.MagicMock(), tencent=False)
        text = bat.read_text(encoding="utf-8")
        self.assertIn(f'rmdir /s /q "{self.root}"', text)
        self.assertNotIn("Tencent Files", text)
        bat.unlink(missing_ok=True)

    def test_missing_shortcut_does_not_block(self):
        self.run_uninstall(mock.MagicMock())  # 桌面上本来就没有快捷方式
        self.assertEqual(self.state.data["pids"], {})

    def test_script_really_deletes_after_we_exit(self):
        """真跑一遍 .bat：它等的进程退出后，目录和 home 都该没了。"""
        bat = self.run_uninstall(mock.MagicMock())
        # 换成一个马上就会退出的进程 PID，模拟「向导已经退出」
        probe = subprocess.Popen(["cmd", "/c", "exit"], creationflags=services.CREATE_NO_WINDOW)
        probe.wait()
        bat.write_text(bat.read_text(encoding="utf-8").replace(str(uninstall.os.getpid()), str(probe.pid)),
                       encoding="utf-8")
        subprocess.run(["cmd", "/c", str(bat)], creationflags=services.CREATE_NO_WINDOW, timeout=60)
        for _ in range(40):
            if not self.root.exists() and not self.home.exists():
                break
            time.sleep(0.25)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.home.exists())
        self.assertFalse(bat.exists())


if __name__ == "__main__":
    unittest.main()
