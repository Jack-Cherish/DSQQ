import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import personalize
import server
from store import State
from test_server import FakeChecks


class OptionalSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = server.App("install", checks_mod=FakeChecks)
        self.app.root = self.root
        self.app.state = State.load(self.root)
        self.app.installer = Mock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_official_install_does_not_require_openid(self):
        self.app.state.data["platform"] = "official"
        result = self.app.handle_keys({"deepseek": "sk-test", "official_appid": "12345",
                                       "official_secret": "secret", "memory": "0", "voice": "0"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.app.state.data.get("owner_qq"))

    def test_text_only_install_does_not_require_optional_keys(self):
        result = self.app.handle_keys({"owner_qq": "123456", "deepseek": "sk-test",
                                       "memory": "0", "voice": "0"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.app.state.data["memory"], "0")
        self.assertEqual(self.app.state.data["voice"], "0")

    def test_enabled_voice_requires_both_credentials(self):
        result = self.app.handle_keys({"owner_qq": "123456", "deepseek": "sk-test",
                                       "memory": "0", "voice": "3", "app_id": "app"})
        self.assertFalse(result["ok"])
        self.assertIn("doubao", result["errors"])

    def test_voice_check_can_keep_one_saved_credential(self):
        self.app.state.set_secret("doubao_app_id", "app-old")
        self.app.state.set_secret("doubao_token", "token-old")
        result = self.app.handle_check({"field": "doubao", "app_id": "", "token": "token-new"})
        self.assertTrue(result["ok"], result)

    def test_existing_choices_and_credentials_survive_blank_edit(self):
        for name, value in (("deepseek", "sk-old"), ("siliconflow", "sf-old"),
                            ("doubao_app_id", "app"), ("doubao_token", "tok")):
            self.app.state.set_secret(name, value)
        self.app.state.data.update(keys_ok=True, owner_qq="123456", memory="2", voice="3")
        result = self.app.handle_keys({"owner_qq": "123456", "deepseek": "sk-new"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.app.state.data["memory"], "2")
        self.assertEqual(self.app.state.data["voice"], "3")
        self.assertEqual(self.app.state.get_secret("doubao_token"), "tok")

    def test_answers_respect_selected_optional_features(self):
        answer_path = self.root / "answers.json"
        personalize.write_answers(answer_path, base_url="http://localhost", username="u", password="p",
                                  owner_qq="123456", secrets_dir=self.root / "s",
                                  sf_key="", app_id="", token="", memory="0", voice="0")
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        self.assertEqual((answer["memory"], answer["voice"]), ("0", "0"))


if __name__ == "__main__":
    unittest.main()
