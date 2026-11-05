import os
import subprocess
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import services
import shortcut
import uninstall
from store import State, instance_home


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

    def run_uninstall(self, popen):
        original_popen = subprocess.Popen
        def launch(*args, **kwargs):
            if args[0][-2] != "-File":
                return original_popen(*args, **kwargs)
            result = popen(*args, **kwargs)
            (Path(args[0][-1]).parent / "ready").touch()
            result.poll.return_value = None
            return result
        original = tempfile.mkdtemp
        with mock.patch.object(services, "is_running", return_value=False), \
             mock.patch.object(uninstall.tempfile, "mkdtemp", side_effect=lambda **kw: original(dir=self.base, **kw)), \
             mock.patch.object(uninstall.subprocess, "Popen", side_effect=launch):
            return uninstall.uninstall(self.root, self.state, home=self.home, desktop=self.desktop)

    def manifest(self, script):
        return json.loads((script.parent / "manifest.json").read_text(encoding="utf-8"))

    def private_home(self, root=None):
        root = root or self.root
        directory = instance_home(root, self.home)
        directory.mkdir(parents=True)
        (directory / "owner.json").write_text(json.dumps({"root": str(root)}), encoding="utf-8")
        (directory / "edge-status").mkdir()
        (directory / "edge-status" / "cache").write_text("browser cache", encoding="utf-8")
        return directory

    def execute_cleanup(self, script, **changes):
        data = self.manifest(script)
        for target in data["paths"]:
            self.assertTrue(Path(target).resolve().is_relative_to(self.base.resolve()))
        # 只测试目录清理；测试不等待或关闭任何真实应用/浏览器进程。
        data.update(parent_pid=999999999, window_pids=[], profiles=[])
        data.update(changes)
        (script.parent / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                              capture_output=True, creationflags=services.CREATE_NO_WINDOW, timeout=60)

    def junction(self, link, target):
        self.assertTrue(link.absolute().is_relative_to(self.base.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.base.resolve()))
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET | Out-Null"],
            env=dict(os.environ, TEST_LINK=str(link), TEST_TARGET=str(target)),
            capture_output=True, check=True, creationflags=services.CREATE_NO_WINDOW)

    def test_stops_services_and_removes_shortcut(self):
        lnk = shortcut.create_shortcut(self.root / "a.exe", f'--root "{self.root}"', uninstall.SHORTCUT_NAME,
                                       self.base / "i.ico", desktop=self.desktop)
        popen = mock.MagicMock()
        self.run_uninstall(popen)
        self.assertEqual(self.state.data["pids"], {})
        self.assertFalse(lnk.exists())

    def test_script_targets_root_and_preserves_unknown_shared_home(self):
        popen = mock.MagicMock()
        bat = self.run_uninstall(popen)
        paths = self.manifest(bat)["paths"]
        self.assertIn(str(self.root), paths)
        self.assertNotIn(str(self.home), paths)
        self.assertEqual(self.manifest(bat)["profiles"], [])
        (argv,), kw = popen.call_args
        self.assertEqual(argv[-2:], ["-File", str(bat)])
        self.assertIn("-NonInteractive", argv)
        self.assertTrue(kw["creationflags"] & services.CREATE_NO_WINDOW)
        bat.unlink(missing_ok=True)

    def test_script_preserves_shared_qq_data(self):
        """QQ 按号目录、登录标记和 NapCat 公共缓存均不能证明属于某一安装。"""
        bat = self.run_uninstall(mock.MagicMock())
        text = json.dumps(self.manifest(bat))
        self.assertNotIn(str(self.tencent / "374451537"), self.manifest(bat)["paths"])
        self.assertNotIn(str(self.tencent / "NapCat"), self.manifest(bat)["paths"])
        # 主人自己的号和所有号共用的 global 绝对不能进清理名单
        self.assertNotIn("1960074081", text)
        self.assertNotIn("global", text)
        bat.unlink(missing_ok=True)

    def test_forgets_only_this_install_login_immediately(self):
        self.run_uninstall(mock.MagicMock())
        self.assertNotIn("qq", State.load(self.root).data)
        self.assertNotIn("qq", self.state.data)
        self.assertTrue((self.login / ".374451537").exists())
        self.assertTrue((self.login / ".1960074081").exists())

    def test_old_install_preserves_new_install_shortcut_home_and_same_account(self):
        other = self.base / "second-install"
        other.mkdir()
        other_state = State.load(other)
        other_state.data["qq"] = "374451537"
        other_state.save()
        marker = self.home / "install.json"
        marker.write_text(json.dumps({"root": str(other)}), encoding="utf-8")
        profile = self.home / "edge-status"
        profile.mkdir()
        (profile / "cache").write_text("second install", encoding="utf-8")
        lnk = shortcut.create_shortcut(other / "a.exe", f'--root "{other}"', uninstall.SHORTCUT_NAME,
                                      other / "i.ico", desktop=self.desktop)
        script = self.run_uninstall(mock.MagicMock())
        self.assertTrue(lnk.exists())
        self.assertEqual(self.manifest(script)["paths"], [str(self.root)])
        self.assertEqual(self.manifest(script)["profiles"], [])
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["root"], str(other))
        self.assertEqual(State.load(other).data["qq"], "374451537")
        self.assertTrue((self.tencent / "374451537").is_dir())
        self.assertTrue((self.login / ".374451537").exists())

    def test_only_own_instance_profile_is_removed_in_two_install_migration(self):
        private = self.private_home()
        other = self.base / "other-install"
        other.mkdir()
        (other / "state.json").write_text('{"qq":"374451537"}', encoding="utf-8")
        other_private = self.private_home(other)
        legacy = self.home / "edge-status"
        legacy.mkdir()
        (legacy / "cache").write_text("unowned legacy cache", encoding="utf-8")
        script = self.run_uninstall(mock.MagicMock())
        data = self.manifest(script)
        self.assertEqual(data["paths"], [str(self.root), str(private)])
        self.assertEqual(data["profiles"], [str(private / "edge"), str(private / "edge-install"), str(private / "edge-status")])
        result = self.execute_cleanup(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertFalse(self.root.exists())
        self.assertFalse(private.exists())
        self.assertTrue((other / "state.json").exists())
        self.assertTrue((other_private / "edge-status" / "cache").exists())
        self.assertTrue((legacy / "cache").exists())
        self.assertTrue((self.tencent / "374451537").exists())

    def test_missing_or_mismatched_instance_owner_preserves_profile(self):
        private = self.private_home()
        owner = private / "owner.json"
        for info in (None, {}, [], {"root": str(self.base / "other")}, {"root": ""}):
            with self.subTest(owner=info):
                owner.write_text(json.dumps(info), encoding="utf-8")
                self.assertEqual(uninstall.leftovers(self.root, self.state, self.home), [self.root])
        owner.unlink()
        self.assertEqual(uninstall.leftovers(self.root, self.state, self.home), [self.root])

    def test_instance_junction_preserves_other_install_profile(self):
        private = self.private_home()
        elsewhere = self.base / "other-profile"
        private.rename(elsewhere)
        self.junction(private, elsewhere)
        script = self.run_uninstall(mock.MagicMock())
        self.assertEqual(self.manifest(script)["paths"], [str(self.root)])
        self.assertEqual(self.manifest(script)["profiles"], [])
        result = self.execute_cleanup(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue((elsewhere / "edge-status" / "cache").exists())

    def test_nested_junction_is_unlinked_without_deleting_target(self):
        external = self.base / "unrelated-data"
        external.mkdir()
        (external / "keep.txt").write_text("keep", encoding="utf-8")
        self.junction(self.root / "cache", external)
        script = self.run_uninstall(mock.MagicMock())
        result = self.execute_cleanup(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertFalse(self.root.exists())
        self.assertTrue((external / "keep.txt").exists())

    def test_linked_browser_profile_is_never_closed(self):
        private = self.private_home()
        profile = private / "edge-status"
        shared = self.base / "shared-browser-profile"
        profile.rename(shared)
        self.junction(profile, shared)
        script = self.run_uninstall(mock.MagicMock())
        self.assertNotIn(str(profile), self.manifest(script)["profiles"])
        result = self.execute_cleanup(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue((shared / "cache").exists())

    def test_only_own_window_locks_are_waited(self):
        private = self.private_home()
        (private / "wizard-status.lock").write_text(json.dumps({"root": str(self.root), "pid": 101}), encoding="utf-8")
        (private / "wizard.lock").write_text(json.dumps({"root": str(self.root), "pid": 104}), encoding="utf-8")
        (self.home / "waiting-105.lock").write_text(json.dumps({"root": str(self.root), "pid": 105}), encoding="utf-8")
        (self.home / "wizard-install.lock").write_text(json.dumps({"root": str(self.base / "other"), "pid": 102}), encoding="utf-8")
        (self.home / "wizard-status.lock").write_text(json.dumps({"root": str(self.root), "pid": 103}), encoding="utf-8")
        script = self.run_uninstall(mock.MagicMock())
        self.assertCountEqual(self.manifest(script)["window_pids"], [101, 103, 104, 105])

    def test_missing_shortcut_does_not_block(self):
        self.run_uninstall(mock.MagicMock())  # 桌面上本来就没有快捷方式
        self.assertEqual(self.state.data["pids"], {})

    def test_script_really_deletes_after_we_exit(self):
        """只对临时测试目录运行清理程序。"""
        bat = self.run_uninstall(mock.MagicMock())
        result = self.execute_cleanup(bat)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        for _ in range(40):
            if not self.root.exists():
                break
            time.sleep(0.25)
        self.assertFalse(self.root.exists())
        self.assertTrue(self.home.exists())
        self.assertFalse(bat.exists())
        self.assertIn("SUCCESS", (bat.parent / "uninstall.log").read_text(encoding="utf-8-sig"))

    def test_special_characters_in_paths_are_literal(self):
        renamed = self.base / "鱼 %TEMP% & [test]"
        self.root.rename(renamed)
        self.root = renamed
        self.state = State.load(renamed)
        self.test_script_really_deletes_after_we_exit()

    def test_invalid_account_is_never_used_as_a_cleanup_path(self):
        self.state.data["qq"] = "../unrelated"
        self.assertEqual(uninstall.leftovers(self.root, self.state, self.home), [self.root])

    def test_cannot_uninstall_a_directory_other_than_the_install(self):
        with self.assertRaises(ValueError):
            uninstall.uninstall(self.base, self.state, home=self.home, desktop=self.desktop)

    def test_install_root_junction_is_not_treated_as_owned_directory(self):
        real = self.base / "other-install"
        self.root.rename(real)
        self.junction(self.root, real)
        with self.assertRaises(ValueError):
            uninstall.leftovers(self.root, self.state, self.home)
        self.assertTrue((real / "state.json").exists())

    def test_locked_file_produces_a_failure_report(self):
        script = self.run_uninstall(mock.MagicMock())
        with (self.root / "locked.txt").open("w"):
            result = self.execute_cleanup(script, attempts=1)
        self.assertEqual(result.returncode, 1)
        log = (script.parent / "uninstall.log").read_text(encoding="utf-8-sig")
        self.assertIn("FAILED", log)
        self.assertIn("locked.txt", log)
        self.assertNotIn(str(self.root), log)
        self.assertTrue(script.exists())

    def test_failure_log_hides_account_in_config_filename(self):
        script = self.run_uninstall(mock.MagicMock())
        with (self.root / "onebot11_374451537.json").open("w"):
            result = self.execute_cleanup(script, attempts=1)
        self.assertEqual(result.returncode, 1)
        log = (script.parent / "uninstall.log").read_text(encoding="utf-8-sig")
        self.assertIn("FAILED", log)
        self.assertIn("onebot11_", log)
        self.assertNotIn("374451537", log)
        self.assertNotIn(str(self.root), log)

    def test_cleanup_start_failure_does_not_mark_uninstalling(self):
        original = tempfile.mkdtemp
        with mock.patch.object(services, "stop_all"), \
             mock.patch.object(uninstall.tempfile, "mkdtemp", side_effect=lambda **kw: original(dir=self.base, **kw)), \
             mock.patch.object(uninstall.subprocess, "Popen", return_value=mock.Mock(poll=lambda: 1)):
            with self.assertRaisesRegex(RuntimeError, "清理程序"):
                uninstall.uninstall(self.root, self.state, home=self.home, desktop=self.desktop)
        self.assertFalse(State.load(self.root).data.get("uninstalling"))
        logs = list(self.base.glob("dafeiyu-uninstall-*/uninstall.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("FAILED", logs[0].read_text(encoding="utf-8-sig"))

    def test_cleanup_process_launch_error_has_a_safe_failure_log(self):
        original = tempfile.mkdtemp
        secret = "launch-secret-token-374451537"
        with mock.patch.object(services, "stop_all"), \
             mock.patch.object(uninstall.tempfile, "mkdtemp", side_effect=lambda **kw: original(dir=self.base, **kw)), \
             mock.patch.object(uninstall.subprocess, "Popen", side_effect=OSError(secret)):
            with self.assertRaisesRegex(RuntimeError, "日志"):
                uninstall.uninstall(self.root, self.state, home=self.home, desktop=self.desktop)
        logs = list(self.base.glob("dafeiyu-uninstall-*/uninstall.log"))
        self.assertEqual(len(logs), 1)
        log = logs[0].read_text(encoding="utf-8-sig")
        self.assertIn("OSError", log)
        self.assertNotIn(secret, log)
        self.assertFalse(State.load(self.root).data.get("uninstalling"))


if __name__ == "__main__":
    unittest.main()
