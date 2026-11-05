import importlib.util
import contextlib
import io
import json
import tempfile
from unittest import mock
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "vendor/dafeiyu-config-pack/tools/import_dafeiyu.py"
spec = importlib.util.spec_from_file_location("official_import", SOURCE)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class OfficialImportTests(unittest.TestCase):
    def test_chooses_only_official_platform(self):
        cfg = {"platform": [{"id": "personal", "type": "aiocqhttp", "enable": True},
                            {"id": "official", "type": "qq_official", "enable": True}]}
        self.assertEqual(tool.choose_platform(cfg, "official")["id"], "official")

    def test_no_fallback_to_onebot_if_official_is_missing(self):
        cfg = {"platform": [{"id": "personal", "type": "aiocqhttp", "enable": True}]}
        self.assertIsNone(tool.choose_platform(cfg, "official"))

    def test_full_official_import_disables_napcat_plugins_and_keeps_owner_session(self):
        calls = []
        cfg = {"platform": [{"id": "dafeiyu", "type": "qq_official", "enable": True}],
               "agent_runner": {"config": {"model": {"provider_id": "deepseek/test"}}},
               "platform_settings": {"enable_id_white_list": True, "id_whitelist": []}}
        plugins = [{"name": name} for name in (
            "astrbot_plugin_qzone", "astrbot_plugin_dafeiyu_signature",
            "astrbot_plugin_livingmemory", "meme_manager")]
        class FakeApi:
            def __init__(self, base): pass
            def login(self, user, password): return True
            def call(self, method, path, body=None, **kwargs):
                calls.append((method, path, body))
                if path == "/config-profiles/default" and method == "GET":
                    return {"data": {"config": cfg}}
                if path == "/plugins" and method == "GET":
                    return {"data": plugins}
                if path.startswith("/plugins/config?"):
                    return {"data": {"config": {}}}
                if path.startswith("/personas/"):
                    return {"status": "error"}
                return {"status": "ok", "data": {}}
            ok = staticmethod(lambda r: r.get("status") != "error")
            msg = staticmethod(lambda r: "")
        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / "answers.json"
            answer.write_text(json.dumps({"base_url": "http://localhost:6185", "username": "u",
                "password": "p", "owner_qq": "ABCDEF1234567890", "memory": "0", "voice": "0",
                "platform": "official"}), encoding="utf-8")
            tool.ANSWERS = {}
            tool.RESULTS.clear()
            with mock.patch.object(tool, "Api", FakeApi), \
                 mock.patch.object(tool.sys, "argv", ["tool", "--answers", str(answer)]), \
                 mock.patch.object(tool, "provider_config", return_value=None), \
                 mock.patch.object(tool, "extra_plugins", return_value=[]), \
                 mock.patch.object(tool, "meme_accelerator", return_value=None), \
                 mock.patch.object(tool, "meme_pack_ids", return_value=["blue-fat-fish-meme-pack", "deepseek-fanart-meme-pack"]), \
                 mock.patch.object(tool, "meme_use_together", return_value=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                tool.main()
        self.assertIn(("PATCH", "/plugins/enabled", {"plugin_id": "astrbot_plugin_qzone", "enabled": False}), calls)
        self.assertIn("dafeiyu:FriendMessage:ABCDEF1234567890", cfg["platform_settings"]["id_whitelist"])
        self.assertIn("ABCDEF1234567890", cfg["admins_id"])

    def test_unbound_import_keeps_whitelist_without_granting_admin(self):
        calls = []
        cfg = {"platform": [{"id": "dafeiyu", "type": "qq_official", "enable": True}],
               "agent_runner": {"config": {"model": {"provider_id": "deepseek/test"}}},
               "platform_settings": {"enable_id_white_list": True, "id_whitelist": []}}
        plugins = [{"name": name} for name in (
            "astrbot_plugin_qzone", "astrbot_plugin_dafeiyu_signature",
            "astrbot_plugin_livingmemory", "meme_manager")]
        class FakeApi:
            def __init__(self, base): pass
            def login(self, user, password): return True
            def call(self, method, path, body=None, **kwargs):
                calls.append((method, path, body))
                if path == "/config-profiles/default" and method == "GET":
                    return {"data": {"config": cfg}}
                if path == "/plugins" and method == "GET":
                    return {"data": plugins}
                if path.startswith("/plugins/config?"):
                    return {"data": {"config": {}}}
                if path.startswith("/personas/"):
                    return {"status": "error"}
                return {"status": "ok", "data": {}}
            ok = staticmethod(lambda r: r.get("status") != "error")
            msg = staticmethod(lambda r: "")
        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / "answers.json"
            answer.write_text(json.dumps({"base_url": "http://localhost:6185", "username": "u",
                "password": "p", "owner_qq": "DAFEIYU_UNBOUND_OWNER", "memory": "0", "voice": "0",
                "platform": "official"}), encoding="utf-8")
            tool.ANSWERS = {}
            tool.RESULTS.clear()
            with mock.patch.object(tool, "Api", FakeApi), \
                 mock.patch.object(tool.sys, "argv", ["tool", "--answers", str(answer)]), \
                 mock.patch.object(tool, "provider_config", return_value=None), \
                 mock.patch.object(tool, "extra_plugins", return_value=[]), \
                 mock.patch.object(tool, "meme_accelerator", return_value=None), \
                 mock.patch.object(tool, "meme_pack_ids", return_value=["blue-fat-fish-meme-pack", "deepseek-fanart-meme-pack"]), \
                 mock.patch.object(tool, "meme_use_together", return_value=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                tool.main()
        self.assertIn(("PATCH", "/plugins/enabled", {"plugin_id": "astrbot_plugin_qzone", "enabled": False}), calls)
        self.assertIn("dafeiyu:FriendMessage:DAFEIYU_UNBOUND_OWNER", cfg["platform_settings"]["id_whitelist"])
        self.assertNotIn("DAFEIYU_UNBOUND_OWNER", cfg["admins_id"])

    def test_official_disables_unsupported_plugins(self):
        blocked = tool.unsupported_plugins("official")
        self.assertIn("astrbot_plugin_dafeiyu_signature", blocked)
        self.assertIn("astrbot_plugin_qzone", blocked)
        self.assertEqual(tool.unsupported_plugins("napcat"), set())


if __name__ == "__main__":
    unittest.main()
