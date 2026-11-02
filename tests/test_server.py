import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import server
from installer import Step
from store import State

PACK = Path(__file__).resolve().parent.parent / "vendor" / "dafeiyu-config-pack"


class FakeChecks:
    """key 里带 bad 就算错。"""

    calls = []

    @classmethod
    def check_deepseek(cls, key):
        cls.calls.append(("deepseek", key))
        return ("bad" not in key, "ds")

    @classmethod
    def check_siliconflow(cls, key):
        cls.calls.append(("siliconflow", key))
        return ("bad" not in key, "sf")

    @classmethod
    def check_doubao(cls, app_id, token, voice=None, text=None):
        cls.calls.append(("doubao", app_id))
        return ("bad" not in app_id + token, "db", "QUJD")

    check_install_path = staticmethod(lambda p: None if p.isascii() and " " not in p else "路径里不能有中文和空格")
    default_install_path = staticmethod(lambda: "D:\\DaFeiYu")


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.boot = Path(self.tmp.name) / "boot"
        shutil.copytree(PACK, self.boot / "pack")
        self.home = Path(self.tmp.name) / "home"
        FakeChecks.calls = []
        self.app = server.App("install", boot=self.boot, home=self.home, checks_mod=FakeChecks,
                              steps_factory=lambda: [Step("configs", "写配置", lambda c: c.wait_for("keys")),
                                                     Step("import", "导入", lambda c: c.wait_for("profile"))])
        self.httpd = server.make_server(self.app, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, token=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        if token:
            req.add_header("X-Token", self.app.token)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def install_root(self):
        return str(Path(self.tmp.name) / "DaFeiYu")

    def test_token_required(self):
        self.assertEqual(self.call("/api/info", token=False)[0], 403)
        self.assertEqual(self.call("/api/info")[0], 200)

    def test_index_has_token(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn(self.app.token, r.read().decode("utf-8"))

    def test_info_defaults(self):
        _, info = self.call("/api/info")
        self.assertEqual(info["mode"], "install")
        self.assertEqual(info["default_path"], "D:\\DaFeiYu")
        self.assertEqual(info["profile"]["name"], "大肥鱼")
        self.assertEqual(len(info["voice_presets"]), 3)

    def test_error_shows_cause_and_log_path(self):
        """接口异常要把原因和日志路径带回前端，否则只能看到「出了点问题」。"""
        shutil.rmtree(self.boot / "pack")  # 真实事故：boot 里漏带配置包
        code, r = self.call("/api/info")
        self.assertEqual(code, 500)
        self.assertIn("找不到配置包", r["message"])
        self.assertIn("wizard.log", r["message"])
        self.assertIn("找不到配置包", (self.home / "wizard.log").read_text(encoding="utf-8"))

    def test_path_endpoint_rejects_chinese(self):
        _, r = self.call("/api/path", {"path": "D:\\大肥鱼"})
        self.assertFalse(r["ok"])
        self.assertIn("中文", r["message"])

    def test_path_starts_installer_and_remembers(self):
        _, r = self.call("/api/path", {"path": self.install_root()})
        self.assertTrue(r["ok"])
        self.assertEqual(self.call("/api/info")[1]["default_path"], self.install_root())
        time.sleep(0.1)
        p = self.call("/api/progress")[1]
        self.assertEqual(p["waiting"], "keys")
        self.assertEqual([s["key"] for s in p["steps"]], ["configs", "import"])

    def keys(self, **over):
        k = {"owner_qq": "123456", "deepseek": "sk-1", "siliconflow": "sf-1", "app_id": "app", "token": "tok"}
        k.update(over)
        return k

    def test_keys_requires_all_ok(self):
        self.call("/api/path", {"path": self.install_root()})
        _, r = self.call("/api/keys", self.keys(siliconflow="bad"))
        self.assertFalse(r["ok"])
        self.assertEqual(set(r["errors"]), {"siliconflow"})
        _, r = self.call("/api/keys", self.keys(owner_qq="12ab"))
        self.assertIn("owner_qq", r["errors"])
        _, r = self.call("/api/keys", self.keys())
        self.assertTrue(r["ok"])
        self.assertEqual(self.app.state.get_secret("doubao_token"), "tok")
        self.assertEqual(self.app.state.data["owner_qq"], "123456")
        time.sleep(0.1)
        self.assertEqual(self.call("/api/progress")[1]["waiting"], "profile")

    def test_checked_values_are_cached(self):
        self.call("/api/path", {"path": self.install_root()})
        self.assertTrue(self.call("/api/check", {"field": "doubao", "app_id": "app", "token": "tok"})[1]["ok"])
        self.call("/api/keys", self.keys())
        self.assertEqual([c for c in FakeChecks.calls if c[0] == "doubao"], [("doubao", "app")])

    def test_profile_validation_and_progress_shape(self):
        self.call("/api/path", {"path": self.install_root()})
        self.call("/api/keys", self.keys())
        _, info = self.call("/api/info")
        bad = dict(info["profile"], memes={k: False for k in info["profile"]["memes"]})
        self.assertFalse(self.call("/api/profile", bad)[1]["ok"])
        self.assertTrue(self.call("/api/profile", info["profile"])[1]["ok"])
        deadline = time.monotonic() + 3
        while not self.call("/api/progress")[1]["finished"] and time.monotonic() < deadline:
            time.sleep(0.02)
        p = self.call("/api/progress")[1]
        self.assertTrue(p["finished"])
        self.assertEqual(set(p["steps"][0]), {"key", "title", "status", "message"})

    def test_resume_restores_provided(self):
        root = self.install_root()
        self.call("/api/path", {"path": root})
        self.call("/api/keys", self.keys())
        app2 = server.App("install", boot=self.boot, home=self.home, checks_mod=FakeChecks,
                          steps_factory=lambda: [Step("configs", "写配置", lambda c: c.wait_for("keys"))])
        r = app2.handle_path({"path": root})
        self.assertEqual(r["resume"], {"keys": True, "profile": False})
        deadline = time.monotonic() + 3
        while not app2.installer.progress()["finished"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(app2.installer.progress()["finished"])

    def test_refresh_qr_restarts_napcat_when_already_logged_in(self):
        """NapCat 报「已登录」时只换码没用：要清掉记下的 QQ 号并停掉它，才会重新出码。"""
        root = Path(self.install_root())
        self.call("/api/path", {"path": str(root)})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.data["qq"] = "3973062989"
        self.app.state.set_secret("napcat_webui_token", "t")
        web = self.app.napcat()
        web.last_error = "当前账号(3973062989)已登录,无法重复登录"
        web.refresh_qr = lambda: self.fail("不该只换码")
        with mock.patch.object(server.services, "stop") as stop:
            _, r = self.call("/api/refresh_qr", {})
        self.assertTrue(r["ok"])
        stop.assert_called_once_with(self.app.state, "napcat")
        self.assertNotIn("qq", self.app.state.data)

    def test_refresh_qr_plain_path_just_changes_code(self):
        root = Path(self.install_root())
        self.call("/api/path", {"path": str(root)})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.set_secret("napcat_webui_token", "t")
        called = []
        self.app.napcat().refresh_qr = lambda: called.append(1)
        _, r = self.call("/api/refresh_qr", {})
        self.assertTrue(r["ok"])
        self.assertEqual(called, [1])

    def test_done_writes_info_file(self):
        root = Path(self.install_root())
        self.call("/api/path", {"path": str(root)})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.set_secret("dashboard_password", "Pw123456")
        _, r = self.call("/api/done")
        self.assertEqual(r["dashboard_url"], "http://127.0.0.1:6185")
        self.assertEqual(r["password"], "Pw123456")
        self.assertIn("Pw123456", (root / "我的信息.txt").read_text(encoding="utf-8"))

    def test_keys_blank_keeps_saved_one(self):
        """改设置时只改一项：留空的那几项要沿用已经存着的，不能被清空。"""
        self.call("/api/path", {"path": self.install_root()})
        self.call("/api/keys", self.keys())
        _, r = self.call("/api/keys", self.keys(deepseek="sk-2", siliconflow="", app_id="", token=""))
        self.assertTrue(r["ok"])
        self.assertEqual(self.app.state.get_secret("deepseek"), "sk-2")
        self.assertEqual(self.app.state.get_secret("siliconflow"), "sf-1")
        self.assertEqual(self.app.state.get_secret("doubao_token"), "tok")

    def test_info_reports_which_keys_are_saved(self):
        self.call("/api/path", {"path": self.install_root()})
        self.assertEqual(self.call("/api/info")[1]["saved_keys"], [])
        self.call("/api/keys", self.keys())
        self.assertEqual(self.call("/api/info")[1]["saved_keys"],
                         ["app_id", "deepseek", "siliconflow", "token"])

    def test_switch_qq_clears_login_and_drops_cached_web(self):
        self.call("/api/path", {"path": self.install_root()})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.set_secret("napcat_webui_token", "t")
        self.assertIsNotNone(self.app.napcat())
        with mock.patch.object(server.services, "clear_qq_login") as clear:
            _, r = self.call("/api/switch_qq", {})
        self.assertTrue(r["ok"])
        clear.assert_called_once()
        self.assertIsNone(self.app._web)  # 换号后不能再用旧凭证

    def test_quit_stops_everything_and_asks_to_exit(self):
        self.call("/api/path", {"path": self.install_root()})
        with mock.patch.object(server.services, "stop_all") as stop_all:
            _, r = self.call("/api/quit", {})
        self.assertTrue(r["ok"])
        stop_all.assert_called_once_with(self.app.state)
        self.assertTrue(self.app.quitting)

    def test_uninstall_runs_cleanup_and_asks_to_exit(self):
        root = self.install_root()
        self.call("/api/path", {"path": root})
        import uninstall as uninstall_mod
        with mock.patch.object(uninstall_mod, "uninstall") as un:
            _, r = self.call("/api/uninstall", {})
        self.assertTrue(r["ok"])
        self.assertEqual(un.call_args[0][0], Path(root))
        self.assertEqual(un.call_args[1]["home"], self.home)
        self.assertTrue(self.app.quitting)

    def test_opening_existing_install_syncs_the_ui(self):
        """重装到老目录时要把界面代码换成新包里的，不然页面上看不到新功能。"""
        root = Path(self.install_root())
        (root / "app").mkdir(parents=True)
        (root / "app" / "main.py").write_text("old", encoding="utf-8")
        (self.boot / "app").mkdir(parents=True)
        (self.boot / "app" / "main.py").write_text("new", encoding="utf-8")
        self.call("/api/path", {"path": str(root)})
        self.assertEqual((root / "app" / "main.py").read_text("utf-8"), "new")

    def test_status_remembers_qq_after_login(self):
        """换号重新扫过之后要记住新号，否则下次启动不带 -q，又得重扫一次。"""
        self.call("/api/path", {"path": self.install_root()})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.set_secret("napcat_webui_token", "t")
        web = self.app.napcat()
        web.status = lambda: "online"
        web.login_uin = lambda: "374451537"
        self.assertEqual(self.app.state.data.get("qq"), None)
        self.call("/api/status")
        self.assertEqual(self.app.state.data["qq"], "374451537")
        self.assertEqual(State.load(Path(self.install_root())).data["qq"], "374451537")

    def test_status_reads_pids_from_disk(self):
        """另一个窗口起了服务，这边不能还按启动时那份 pids 说「她下线了」。"""
        self.call("/api/path", {"path": self.install_root()})
        self.app.state.data["ports"] = {"dashboard": 6185, "onebot": 6199, "napcat_webui": 6099}
        self.app.state.set_secret("napcat_webui_token", "t")
        other = State.load(Path(self.install_root()))
        other.data["pids"] = {"astrbot": 111, "napcat": 222}
        other.save()
        self.app.napcat().status = lambda: "online"
        with mock.patch.object(server.services, "is_running", side_effect=lambda pid: pid in (111, 222)):
            _, s = self.call("/api/status")
        self.assertTrue(s["astrbot"])
        self.assertTrue(s["napcat"])

    def test_opening_install_repairs_broken_plugin(self):
        """上游 group_chat_plus 少打包一个文件，打开向导时补上，老安装也能修好。"""
        root = Path(self.install_root())
        utils = root / "astrbot" / "data" / "plugins" / "astrbot_plugin_group_chat_plus" \
            / "private_chat" / "private_chat_utils"
        utils.mkdir(parents=True)
        (utils / "private_chat_session_guard.py").write_text("def guard_session(s): pass\n", encoding="utf-8")
        self.call("/api/path", {"path": str(root)})
        shim = utils / "_session_guard.py"
        self.assertTrue(shim.exists())
        self.assertIn("private_chat_session_guard", shim.read_text("utf-8"))

    def test_quitting_starts_false(self):
        self.assertFalse(self.app.quitting)

    def test_unknown_api_404(self):
        self.assertEqual(self.call("/api/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
