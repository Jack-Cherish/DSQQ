import json
import shutil
import tempfile
import unittest
from pathlib import Path

import personalize

PACK = Path(__file__).resolve().parent.parent / "vendor" / "dafeiyu-config-pack"


class PersonalizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack = Path(self.tmp.name) / "pack"
        shutil.copytree(PACK, self.pack)

    def tearDown(self):
        self.tmp.cleanup()

    def persona(self):
        return (self.pack / "persona" / "大肥鱼.md").read_text(encoding="utf-8")

    def pack_json(self):
        return json.loads((self.pack / "settings" / "pack.json").read_text(encoding="utf-8"))

    def test_default_profile_reads_pack(self):
        p = personalize.default_profile(self.pack)
        self.assertEqual(p["name"], "大肥鱼")
        self.assertTrue(p["persona"].startswith("你是大肥鱼"))
        self.assertIn("憨萌软糯", p["voice"])
        self.assertEqual(p["memes"], {"blue-fat-fish-meme-pack": True, "deepseek-fanart-meme-pack": True})
        self.assertEqual(len(personalize.VOICE_PRESETS), 3)

    def test_rename_and_voice_written(self):
        p = personalize.default_profile(self.pack)
        p.update(name="小鲸", voice="温柔的大姐姐", memes={"blue-fat-fish-meme-pack": False, "deepseek-fanart-meme-pack": True})
        personalize.apply_profile(self.pack, p)
        text = self.persona()
        self.assertTrue(text.startswith("你是小鲸"))
        self.assertNotIn("大肥鱼", text)
        self.assertIn("{OWNER_QQ}", text)
        pj = self.pack_json()
        self.assertEqual(pj["voice"]["doubao"]["persona"], "温柔的大姐姐")
        self.assertEqual({m["id"]: m["use"] for m in pj["meme_packs"]},
                         {"blue-fat-fish-meme-pack": False, "deepseek-fanart-meme-pack": True})
        # 关掉的那套不能还是默认主套，否则 meme_manager 默认规则会选到它
        self.assertTrue(next(m for m in pj["meme_packs"] if m["use"])["default"])

    def test_owner_placeholder_restored(self):
        p = personalize.default_profile(self.pack)
        p["persona"] = "你是小鲸，很懒。"
        personalize.apply_profile(self.pack, p)
        self.assertIn("{OWNER_QQ}", self.persona())

    def test_at_least_one_meme_pack(self):
        p = personalize.default_profile(self.pack)
        p["memes"] = {k: False for k in p["memes"]}
        with self.assertRaises(ValueError):
            personalize.apply_profile(self.pack, p)

    def test_empty_name_rejected(self):
        p = personalize.default_profile(self.pack)
        p["name"] = "  "
        with self.assertRaises(ValueError):
            personalize.apply_profile(self.pack, p)

    def test_answers_have_no_plain_secrets(self):
        sec = Path(self.tmp.name) / "sec"
        ans = Path(self.tmp.name) / "a.json"
        personalize.write_answers(ans, base_url="http://127.0.0.1:6185", username="dafeiyu", password="Pw123456789",
                                  owner_qq="123456", secrets_dir=sec, sf_key="sf-key-xyz", app_id="app1",
                                  token="tok-abc")
        raw = ans.read_text(encoding="utf-8")
        for s in ("Pw123456789", "sf-key-xyz", "tok-abc"):
            self.assertNotIn(s, raw)
        a = json.loads(raw)
        self.assertEqual((a["memory"], a["voice"], a["owner_qq"], a["volc_app_id"]), ("2", "3", "123456", "app1"))
        self.assertEqual(Path(a["password_file"]).read_text(encoding="utf-8"), "Pw123456789")
        self.assertEqual(Path(a["siliconflow_key_file"]).read_text(encoding="utf-8"), "sf-key-xyz")
        self.assertEqual(Path(a["volc_access_token_file"]).read_text(encoding="utf-8"), "tok-abc")


if __name__ == "__main__":
    unittest.main()
