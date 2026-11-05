import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import configs
import installer
import server
from store import State
from test_server import FakeChecks


class OfficialModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = server.App("install", checks_mod=FakeChecks)
        self.app.root = self.root
        self.app.state = State.load(self.root)
        self.app.state.data["platform"] = "official"
        self.app.installer = mock.Mock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_official_mode_requires_openid_and_bot_credentials(self):
        result = self.app.handle_keys({"owner_qq": "123456", "deepseek": "sk-test",
                                       "memory": "0", "voice": "0"})
        self.assertFalse(result["ok"])
        self.assertIn("official_appid", result["errors"])
        self.assertIn("official_secret", result["errors"])

    def test_official_mode_preserves_bound_owner_and_saves_secret(self):
        self.app.state.data["owner_qq"] = "A1B2C3D4E5F67890"
        self.app.state.set_secret("official_appid", "123456")
        result = self.app.handle_keys({"owner_qq": "A1B2C3D4E5F67890", "deepseek": "sk-test",
                                      "memory": "0", "voice": "0",
                                      "official_appid": "123456", "official_secret": "secret"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.app.state.get_secret("official_secret"), "secret")
        self.assertEqual(self.app.state.data["owner_qq"], "A1B2C3D4E5F67890")

    def test_official_config_is_restricted_and_has_no_onebot(self):
        sec = configs.new_secrets()
        configs.write_astrbot_config(self.root, ports={"dashboard": 6185, "onebot": 6199},
                                     secrets=sec, deepseek_key="sk", platform="official",
                                     official_appid="1234", official_secret="secret",
                                     owner_openid="ABCDEF1234567890")
        data = json.loads((self.root / "cmd_config.json").read_text("utf-8"))
        self.assertEqual(data["platform"][0]["type"], "qq_official")
        self.assertEqual(data["platform"][0]["appid"], "1234")
        self.assertTrue(data["platform_settings"]["enable_id_white_list"])
        self.assertIn("dafeiyu:FriendMessage:ABCDEF1234567890", data["platform_settings"]["id_whitelist"])

    def test_reapplying_official_config_keeps_other_allowed_sessions(self):
        sec = configs.new_secrets()
        path = self.root / "cmd_config.json"
        path.write_text(json.dumps({"platform_settings": {"id_whitelist": ["another:GroupMessage:group"]},
                                    "admins_id": ["other-admin"]}), encoding="utf-8")
        configs.write_astrbot_config(self.root, ports={"dashboard": 6185, "onebot": 6199},
                                     secrets=sec, deepseek_key="sk", platform="official",
                                     official_appid="1234", official_secret="secret",
                                     owner_openid="ABCDEF1234567890")
        data = json.loads(path.read_text("utf-8"))
        self.assertIn("another:GroupMessage:group", data["platform_settings"]["id_whitelist"])
        self.assertIn("other-admin", data["admins_id"])

    def test_unbound_official_config_never_grants_admin(self):
        configs.write_astrbot_config(self.root, ports={"dashboard": 6185, "onebot": 6199},
                                     secrets=configs.new_secrets(), deepseek_key="sk", platform="official",
                                     owner_openid="DAFEIYU_UNBOUND_OWNER")
        data = json.loads((self.root / "cmd_config.json").read_text("utf-8"))
        self.assertTrue(data["platform_settings"]["enable_id_white_list"])
        self.assertIn("dafeiyu:FriendMessage:DAFEIYU_UNBOUND_OWNER", data["platform_settings"]["id_whitelist"])
        self.assertNotIn("DAFEIYU_UNBOUND_OWNER", data.get("admins_id", []))

    def test_new_official_install_allows_unbound_owner(self):
        result = self.app.handle_keys({"deepseek": "sk-test", "memory": "0", "voice": "0",
                                       "official_appid": "123456", "official_secret": "secret"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.app.state.data.get("owner_qq"))

    def test_frontend_cannot_claim_owner_without_binding(self):
        result = self.app.handle_keys({"owner_qq": "MALICIOUS1234567890", "deepseek": "sk-test",
                                       "memory": "0", "voice": "0", "official_appid": "123456",
                                       "official_secret": "secret"})
        self.assertTrue(result["ok"])
        self.assertFalse(self.app.state.data.get("owner_qq"))

    def test_binding_requires_keys_and_cannot_rebind(self):
        self.assertFalse(self.app.handle_official_bind({})["ok"])
        self.app.state.data.update(keys_ok=True, owner_qq="ABCDEF1234567890")
        self.assertTrue(self.app.handle_official_bind({})["bound"])
        self.assertTrue(self.app.handle_official_bind_confirm({"candidate_id": "id"})["ok"])

    def test_binding_save_failure_keeps_candidate_and_rolls_back_owner(self):
        self.app.state.data["keys_ok"] = True
        with mock.patch.object(server.official_binding, "session_id", return_value="current"), \
             mock.patch.object(server.official_binding, "candidate", return_value="ABCDEF1234567890"), \
             mock.patch.object(server.official_binding, "consume", return_value="ABCDEF1234567890") as consume, \
             mock.patch.object(self.app.state, "save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.app.handle_official_bind_confirm({"candidate_id": "current"})
            consume.assert_not_called()
        self.assertFalse(self.app.state.data.get("owner_qq"))
        self.app.installer.provide.assert_not_called()

    def test_binding_confirms_only_current_candidate(self):
        self.app.state.data["keys_ok"] = True
        with mock.patch.object(server.official_binding, "create", return_value="abc123"), \
             mock.patch.object(server.official_binding, "session_id", return_value="current"), \
             mock.patch.object(server.official_binding, "candidate", return_value="ABCDEF1234567890"), \
             mock.patch.object(server.official_binding, "consume", return_value="ABCDEF1234567890") as consume:
            self.assertEqual(self.app.handle_official_bind({})["candidate_id"], "current")
            self.assertFalse(self.app.handle_official_bind_confirm({"candidate_id": "old"})["ok"])
            consume.assert_not_called()
            self.assertTrue(self.app.handle_official_bind_confirm({"candidate_id": "current"})["ok"])
        self.assertEqual(self.app.state.data["owner_qq"], "ABCDEF1234567890")
        self.app.installer.provide.assert_called_with("official_bound", {"owner_qq": "ABCDEF1234567890"})

    def test_binding_check_reports_receipt_without_trusting_sent_claim(self):
        self.app.state.data["keys_ok"] = True
        result = self.app.handle_official_bind({"check": ["1"], "sent": ["true"]})
        self.assertEqual(result["receipt"], "not_received")
        self.assertFalse(result["candidate_id"])
        self.assertFalse(result["bound"])
        self.assertFalse(self.app.state.data.get("owner_qq"))
        self.app.installer.provide.assert_not_called()
        code = result["code"]
        server.official_binding.capture(code, "A" * 32, self.root)
        result = self.app.handle_official_bind({})
        self.assertEqual(result["receipt"], "received")
        self.assertTrue(result["candidate_id"])
        self.assertFalse(result["bound"])

    def test_poll_does_not_open_a_new_session_after_concurrent_confirmation(self):
        self.app.state.data["keys_ok"] = True
        self.app._lock = mock.MagicMock()
        self.app._lock.__enter__.side_effect = lambda: self.app.state.data.update(owner_qq="A" * 32)
        with mock.patch.object(server.official_binding, "create", return_value="unused") as create:
            result = self.app.handle_official_bind({})
        self.assertTrue(result["bound"])
        create.assert_not_called()

    def test_official_mode_does_not_start_napcat(self):
        inst = installer.Installer(self.root, self.app.state, steps=[])
        with mock.patch.object(installer.services, "start_napcat") as start:
            installer.ensure_napcat(installer.Ctx(inst))
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
