import argparse
import os
import shutil
import subprocess
import sys
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
        (self.root / "state.json").write_text('{"done":["login"]}', encoding="utf-8")
        remember_install(self.root, self.home)

    def test_fresh_machine_gets_the_wizard(self):
        self.assertEqual(entry.resolve_mode(args(), self.home), ("install", None))

    def test_installed_machine_gets_the_status_page(self):
        """双击一键包时已经装过，就该直接看到状态页，而不是又让人选一次安装位置。"""
        self.install()
        self.assertEqual(entry.resolve_mode(args(), self.home), ("status", self.root))

    def test_incomplete_install_returns_to_wizard_with_existing_root(self):
        self.install()
        (self.root / "state.json").write_text('{"done":["extract","ports"]}', encoding="utf-8")
        self.assertEqual(entry.resolve_mode(args(), self.home), ("install", self.root))

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
        self.assertEqual(entry.resolve_mode(args(status=True, root=str(other)), self.home), ("install", other))


class LockTest(unittest.TestCase):
    def test_distinct_installations_have_distinct_window_files(self):
        home = Path("home")
        a = entry.window_files("status", home, Path("D:/one"))
        b = entry.window_files("status", home, Path("D:/two"))
        self.assertNotEqual(a, b)
        self.assertEqual(a, entry.window_files("status", home, Path("D:/one")))

    def test_install_and_status_share_a_controller(self):
        home = Path("home")
        self.assertEqual(entry.window_files("install", home), (home / "wizard.lock", home / "edge"))
        self.assertEqual(entry.window_files("install", home), entry.window_files("status", home))


class IdleExitTest(unittest.TestCase):
    def test_waiting_for_official_binding_has_a_longer_grace_period(self):
        worker = mock.Mock()
        worker.progress.return_value = {"running": True, "waiting": "official_bound"}
        self.assertFalse(entry.should_idle_exit([worker], entry.CLOSED_EXIT_S + 1, closed=True))
        self.assertTrue(entry.should_idle_exit([worker], entry.BIND_IDLE_EXIT_S + 1, closed=True))

    def test_ordinary_idle_and_active_install_behavior_is_preserved(self):
        worker = mock.Mock()
        worker.progress.return_value = {"running": True, "waiting": "keys"}
        self.assertFalse(entry.should_idle_exit([worker], entry.IDLE_EXIT_S - 1))
        self.assertTrue(entry.should_idle_exit([worker], entry.IDLE_EXIT_S + 1))
        worker.progress.return_value = {"running": True, "waiting": None}
        self.assertFalse(entry.should_idle_exit([worker], entry.BIND_IDLE_EXIT_S + 1))


class ElevatedStopEntryTest(unittest.TestCase):
    def test_stop_entry_operates_only_on_empty_temporary_install(self):
        from store import State
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'install with spaces'
            root.mkdir()
            state = State.load(root)
            state.save()
            result = subprocess.run([sys.executable, str(Path(entry.__file__)), '--stop-elevated', '--root', str(root)],
                                    capture_output=True, env=os.environ.copy(), timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            self.assertTrue(State.load(root).data['services_stopped'])

    def test_stop_entry_stops_only_its_own_test_process(self):
        import services
        from store import State
        node = Path(__file__).resolve().parents[1] / '.tmp' / 'build' / 'stage' / 'napcat' / 'node.exe'
        if not node.is_file():
            self.skipTest('local test Node runtime is unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'install with spaces'
            own_node = root / 'napcat' / 'node.exe'
            own_node.parent.mkdir(parents=True)
            shutil.copy2(node, own_node)
            proc = subprocess.Popen([str(own_node), '-e', 'setInterval(()=>{},1000)'], creationflags=services.CREATE_NO_WINDOW)
            try:
                state = State.load(root)
                state.data['pids'] = {'napcat': proc.pid}
                state.save()
                result = subprocess.run([sys.executable, str(Path(entry.__file__)), '--stop-elevated', '--root', str(root)],
                                        capture_output=True, env=os.environ.copy(), timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
                proc.wait(timeout=5)
                self.assertFalse(services.is_running(proc.pid))
                self.assertEqual(State.load(root).data['pids'], {})
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)


class WizardUpgradeTests(unittest.TestCase):
    def response(self, data):
        import json
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        return response

    def test_new_package_replaces_a_waiting_old_binding_wizard(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "wizard.lock"
            lock.write_text(json.dumps({"port": 12345, "token": "test", "version": "old"}))
            responses = [self.response({"ok": True}), self.response({"running": True, "waiting": "official_bound"}),
                         self.response({"ok": True})]
            with mock.patch.object(entry.urllib.request, "urlopen", side_effect=responses) as request:
                self.assertIsNone(entry.existing_instance(lock, version="new"))
            self.assertTrue(request.call_args_list[-1].args[0].full_url.endswith("/api/quit"))

    def test_new_package_can_replace_idle_status_but_not_starting_services(self):
        import json
        for starting in (False, True):
            with self.subTest(starting=starting), tempfile.TemporaryDirectory() as tmp:
                lock = Path(tmp) / "wizard.lock"
                lock.write_text(json.dumps({"port": 12345, "token": "test", "version": "old"}))
                responses = [self.response({"ok": True}), self.response({"running": False}),
                             self.response({"astrbot": True, "starting": starting}), self.response({"ok": True})]
                with mock.patch.object(entry.urllib.request, "urlopen", side_effect=responses):
                    result = entry.existing_instance(lock, version="new")
                if starting:
                    self.assertIn("127.0.0.1:12345", result)
                else:
                    self.assertIsNone(result)

    def test_upgrade_preserves_active_work_and_failed_stop(self):
        import json
        for progress, stopped in (({"running": True, "waiting": None}, None),
                                  ({"running": True, "waiting": "official_bound"}, {"ok": False})):
            with self.subTest(progress=progress), tempfile.TemporaryDirectory() as tmp:
                lock = Path(tmp) / "wizard.lock"
                lock.write_text(json.dumps({"port": 12345, "token": "test"}))
                responses = [self.response({"ok": True}), self.response(progress)]
                if stopped is not None:
                    responses.append(self.response(stopped))
                with mock.patch.object(entry.urllib.request, "urlopen", side_effect=responses):
                    self.assertIn("127.0.0.1:12345", entry.existing_instance(lock, version="new"))


if __name__ == "__main__":
    unittest.main()
