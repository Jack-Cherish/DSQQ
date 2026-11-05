import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import diagnostics
import server
from store import State
from test_server import FakeChecks


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self):
        return b'{"choices":[{"message":{"content":"Check the installation package."}}]}'


class DiagnosticAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = server.App("status", checks_mod=FakeChecks)
        self.app.root = self.root
        self.app.boot = self.root / "boot"
        self.app.state = State.load(self.root)
        self.app.state.set_secret("deepseek", "sk-supersecret")
        self.app.installer = mock.Mock()
        self.app.installer.progress.return_value = {"running": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_ai_requires_explicit_consent(self):
        with mock.patch.object(diagnostics, "analyze_with_deepseek") as analyze:
            result = self.app.handle_ai_diagnose({})
        self.assertFalse(result["ok"])
        analyze.assert_not_called()

    def test_status_reads_processes_after_slow_login_check(self):
        self.app.state.data["ports"] = {"napcat_webui": 6099}
        self.app.state.data["qq"] = "123456"
        def login():
            other = State.load(self.root)
            other.data["pids"] = {"astrbot": 123, "napcat": 456}
            other.save()
            return "online"
        with mock.patch.object(self.app, "login_status", side_effect=login), \
             mock.patch.object(server.services, "find_processes", return_value={"astrbot": [123], "napcat": [456]}):
            result = self.app.handle_status({})
        self.assertTrue(result["astrbot"])
        self.assertTrue(result["napcat"])

    def test_status_keeps_recorded_pid_when_windows_denies_process_inspection(self):
        self.app.state.data['pids'] = {'napcat': 4242}
        self.app.state.save()
        with mock.patch.object(self.app, 'login_status', return_value='starting'), \
             mock.patch.object(server.services, 'find_processes', return_value={'astrbot': [], 'napcat': []}), \
             mock.patch.object(server.services, 'is_running', side_effect=PermissionError('access denied')):
            result = self.app.handle_status({})
        self.assertTrue(result['permission_denied'])
        self.assertIsNone(result['napcat'])
        self.assertEqual(State.load(self.root).data['pids']['napcat'], 4242)

    def test_status_reports_unknown_if_process_snapshot_is_denied(self):
        self.app.state.data['pids'] = {'napcat': 4242}
        self.app.state.save()
        with mock.patch.object(self.app, 'login_status', return_value='starting'), \
             mock.patch.object(server.services, 'find_processes', side_effect=PermissionError('access denied')):
            result = self.app.handle_status({})
        self.assertTrue(result['permission_denied'])
        self.assertEqual(State.load(self.root).data['pids']['napcat'], 4242)

    def test_remember_qq_does_not_erase_new_processes(self):
        other = State.load(self.root)
        other.data["pids"] = {"astrbot": 123, "napcat": 456}
        other.save()
        web = mock.Mock()
        web.login_uin.return_value = "123456"
        with mock.patch.object(self.app, "napcat", return_value=web):
            self.app.remember_qq("online")
        self.assertEqual(State.load(self.root).data.get("pids"), {"astrbot": 123, "napcat": 456})

    def test_login_reports_expired_code_without_repeated_refresh(self):
        web = mock.Mock()
        web.last_error = "二维码已过期"
        with mock.patch.object(self.app, "login_status", return_value="error"), \
             mock.patch.object(self.app, "napcat", return_value=web):
            result = self.app.handle_login({})
        self.assertEqual(result["status"], "error")
        web.refresh_qr.assert_not_called()

    def test_qr_revision_changes_when_file_changes(self):
        cache = self.root / "napcat-data" / "cache"
        cache.mkdir(parents=True)
        qr = cache / "qrcode.png"
        qr.write_bytes(b"first")
        first = self.app.qr_revision()
        qr.write_bytes(b"second")
        self.assertNotEqual(first, self.app.qr_revision())

    def test_refresh_restarts_napcat_on_status_page_when_already_logged_in(self):
        web = mock.Mock()
        web.last_error = "当前账号已登录"
        with mock.patch.object(self.app, "napcat", return_value=web), \
             mock.patch.object(server.services, "stop"), \
             mock.patch.object(self.app, "handle_start", return_value={"ok": True}) as start:
            result = self.app.handle_refresh_qr({})
        self.assertTrue(result["ok"])
        start.assert_called_once()

    def test_ai_request_contains_only_fixed_diagnostic_codes(self):
        sent = {}
        def opener(req, timeout):
            sent["payload"] = json.loads(req.data.decode())
            return Response()
        result = diagnostics.analyze_with_deepseek(
            "sk-supersecret",
            {"items": [{"code": "astrbot_core_missing", "message": "private chat and password"}]},
            opener=opener,
        )
        self.assertIn("Check", result)
        payload = json.dumps(sent["payload"])
        self.assertIn("astrbot_core_missing", payload)
        self.assertNotIn("private chat", payload)
        self.assertNotIn("sk-supersecret", payload)

    def test_repair_refuses_while_service_running(self):
        self.app.state.data["pids"] = {"astrbot": 123}
        self.app.state.save()
        with mock.patch.object(server.services, "find_processes", return_value={"astrbot": [123], "napcat": []}):
            with mock.patch.object(diagnostics, "repair") as repair:
                result = self.app.handle_repair({})
        self.assertFalse(result["ok"])
        self.assertIn("下线", result["message"])
        repair.assert_not_called()

    def test_repair_refuses_running_services_even_when_pids_were_lost(self):
        self.app.state.data["pids"] = {}
        self.app.state.save()
        with mock.patch.object(server.services, "find_processes", return_value={"astrbot": [123], "napcat": []}), \
             mock.patch.object(diagnostics, "repair") as repair:
            result = self.app.handle_repair({})
        self.assertFalse(result["ok"])
        repair.assert_not_called()


if __name__ == "__main__":
    unittest.main()
