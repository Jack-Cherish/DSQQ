import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

import configs

PORTS = {"dashboard": 6185, "onebot": 6299, "napcat_webui": 6099}


class ConfigsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.secrets = configs.new_secrets()

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_secrets(self):
        s = self.secrets
        self.assertGreaterEqual(len(s["onebot_token"]), 20)
        pw = s["dashboard_password"]
        self.assertGreaterEqual(len(pw), 16)
        self.assertTrue(re.search("[A-Z]", pw) and re.search("[a-z]", pw) and re.search(r"\d", pw))
        self.assertNotEqual(configs.new_secrets()["onebot_token"], s["onebot_token"])

    def test_password_hash_matches_astrbot(self):
        pbkdf2, md5 = configs.hash_dashboard_password("Abc12345xyz")
        algo, iters, salt, digest = pbkdf2.split("$")
        self.assertEqual((algo, iters), ("pbkdf2_sha256", "600000"))
        again = hashlib.pbkdf2_hmac("sha256", b"Abc12345xyz", bytes.fromhex(salt), 600000).hex()
        self.assertEqual(again, digest)
        self.assertEqual(md5, hashlib.md5(b"Abc12345xyz").hexdigest())

    def astrbot(self):
        configs.write_astrbot_config(self.dir, ports=PORTS, secrets=self.secrets, deepseek_key="sk-ds")
        return json.loads((self.dir / "cmd_config.json").read_text(encoding="utf-8"))

    def test_astrbot_config_fields(self):
        c = self.astrbot()
        d = c["dashboard"]
        self.assertEqual((d["host"], d["port"], d["username"]), ("127.0.0.1", 6185, "dafeiyu"))
        self.assertFalse(d["password_change_required"])
        self.assertTrue(d["password_storage_upgraded"])
        self.assertTrue(d["jwt_secret"])
        self.assertEqual(c["platform"], [{"id": "dafeiyu", "type": "aiocqhttp", "enable": True,
                                          "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6299,
                                          "ws_reverse_token": self.secrets["onebot_token"]}])
        self.assertEqual(c["provider_sources"][0]["key"], ["sk-ds"])
        self.assertEqual(c["provider"][0]["id"], "deepseek/deepseek-flash")
        self.assertEqual(c["provider"][0]["model"], "deepseek-flash")
        self.assertEqual(c["agent_runner"]["config"]["model"]["provider_id"], "deepseek/deepseek-flash")
        self.assertEqual(c["provider_settings"]["default_image_caption_provider_id"], "deepseek/deepseek-flash")
        self.assertEqual(c["config_version"], 5)
        self.assertNotIn(self.secrets["dashboard_password"], json.dumps(c))

    def test_merge_keeps_other_keys(self):
        (self.dir / "cmd_config.json").write_text(json.dumps({
            "foo": 1,
            "platform": [{"id": "other", "type": "x"}, {"id": "dafeiyu", "ws_reverse_port": 1}],
            "provider": [{"id": "keep-me"}],
            "agent_runner": {"runner_type": "local", "config": {"model": {"provider_id": "", "fallback_provider_ids": []}, "x": 2}},
            "dashboard": {"totp": {"enable": False}},
        }), encoding="utf-8")
        c = self.astrbot()
        self.assertEqual(c["foo"], 1)
        self.assertEqual([p["id"] for p in c["platform"]], ["other", "dafeiyu"])
        self.assertEqual(c["platform"][1]["ws_reverse_port"], 6299)
        self.assertEqual({p["id"] for p in c["provider"]}, {"keep-me", "deepseek/deepseek-flash"})
        self.assertEqual(c["agent_runner"]["config"]["x"], 2)
        self.assertEqual(c["dashboard"]["totp"], {"enable": False})
        self.astrbot()  # 再写一次不重复
        self.assertEqual(len(json.loads((self.dir / "cmd_config.json").read_text("utf-8"))["provider"]), 2)

    def test_ports_consistent(self):
        work = self.dir / "napcat-data"
        (work / "config").mkdir(parents=True)
        (work / "config" / "onebot11_123.json").write_text(json.dumps(
            {"network": {"websocketClients": [{"name": "AstrBot", "url": "ws://127.0.0.1:1/ws", "token": "old"}],
                         "httpServers": [{"name": "keep"}]}}), encoding="utf-8")
        configs.write_napcat_config(work, ports=PORTS, secrets=self.secrets)
        c = self.astrbot()
        webui = json.loads((work / "config" / "webui.json").read_text("utf-8"))
        self.assertEqual((webui["host"], webui["port"], webui["token"]),
                         ("127.0.0.1", 6099, self.secrets["napcat_webui_token"]))
        for name in ("onebot11.json", "onebot11_123.json"):
            ob = json.loads((work / "config" / name).read_text("utf-8"))
            ws = ob["network"]["websocketClients"]
            self.assertEqual(len(ws), 1, name)
            self.assertEqual(ws[0]["url"], f"ws://127.0.0.1:{c['platform'][0]['ws_reverse_port']}/ws")
            self.assertEqual(ws[0]["token"], c["platform"][0]["ws_reverse_token"])
            self.assertTrue(ws[0]["enable"])
            self.assertEqual((ws[0]["reconnectInterval"], ws[0]["heartInterval"]), (1000, 1000))
        self.assertEqual(json.loads((work / "config" / "onebot11_123.json").read_text("utf-8"))["network"]["httpServers"],
                         [{"name": "keep"}])

    def test_pick_ports_distinct(self):
        p = configs.pick_ports()
        self.assertEqual(set(p), {"dashboard", "onebot", "napcat_webui"})
        self.assertEqual(len(set(p.values())), 3)


if __name__ == "__main__":
    unittest.main()
