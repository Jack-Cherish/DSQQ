import tempfile
import threading
import time
import unittest
from pathlib import Path

import installer
from installer import InstallError, Installer, Step
from store import State


def wait_idle(inst, timeout=5):
    deadline = time.monotonic() + timeout
    while inst.progress()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return inst.progress()


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, key, fn=None):
        def run(ctx):
            self.ran.append(key)
            if fn:
                fn(ctx)
        return Step(key, f"标题{key}", run)

    def test_resume_skips_done_steps(self):
        st = State.load(self.root)
        st.mark_done("a")
        st.mark_done("b")
        inst = Installer(self.root, State.load(self.root), steps=[self.step("a"), self.step("b"), self.step("c")])
        inst.start()
        p = wait_idle(inst)
        self.assertEqual(self.ran, ["c"])
        self.assertEqual([s["status"] for s in p["steps"]], ["done", "done", "done"])
        self.assertTrue(p["finished"])
        self.assertTrue(State.load(self.root).is_done("c"))

    def test_error_then_retry(self):
        attempts = []

        def flaky(ctx):
            attempts.append(1)
            if len(attempts) == 1:
                raise InstallError("网络断了，点重试")

        inst = Installer(self.root, State.load(self.root), steps=[self.step("a"), self.step("b", flaky), self.step("c")])
        inst.start()
        p = wait_idle(inst)
        self.assertEqual([s["status"] for s in p["steps"]], ["done", "error", "todo"])
        self.assertEqual(p["steps"][1]["message"], "网络断了，点重试")
        self.assertFalse(p["finished"])
        inst.retry()
        p = wait_idle(inst)
        self.assertEqual([s["status"] for s in p["steps"]], ["done", "done", "done"])
        self.assertEqual(self.ran, ["a", "b", "b", "c"])

    def test_unexpected_error_is_friendly_and_redacted(self):
        st = State.load(self.root)
        st.set_secret("deepseek", "sk-very-secret")

        def boom(ctx):
            raise ValueError("bad sk-very-secret")

        inst = Installer(self.root, st, steps=[self.step("a", boom)])
        inst.start()
        p = wait_idle(inst)
        self.assertNotIn("sk-very-secret", p["steps"][0]["message"])
        self.assertIn("重试", p["steps"][0]["message"])
        # 兜底文案之外还要有异常原文和日志路径，否则没法定位
        self.assertIn("ValueError", p["steps"][0]["message"])
        self.assertIn("installer.log", p["steps"][0]["message"])
        log = (self.root / "logs" / "installer.log").read_text(encoding="utf-8")
        self.assertIn("ValueError", log)
        self.assertNotIn("sk-very-secret", log)

    def test_missing_extract_output_resets_progress(self):
        """解压产物被删掉后，不能再信 done 标记跳过解压，否则后面每步都失败且重试无效。"""
        st = State.load(self.root)
        for k in ("extract", "ports", "configs"):
            st.mark_done(k)
        inst = Installer(self.root, st, steps=[self.step("extract"), self.step("ports")])
        self.assertEqual([s["status"] for s in inst.progress()["steps"]], ["todo", "todo"])
        self.assertEqual(State.load(self.root).data["done"], [])

    def test_intact_extract_output_keeps_progress(self):
        import personalize
        import services
        layout = services.Layout(self.root)
        for p in (layout.python, layout.astrbot / "main.py", layout.pack / personalize.PERSONA,
                  layout.napcat / "node.exe"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="utf-8")
        st = State.load(self.root)
        st.mark_done("extract")
        inst = Installer(self.root, st, steps=[self.step("extract"), self.step("ports")])
        self.assertEqual([s["status"] for s in inst.progress()["steps"]], ["done", "todo"])

    def test_sync_app_replaces_stale_ui(self):
        """换新版重装时，安装目录里的界面代码要换成新的，否则新功能在页面上看不见。"""
        boot = self.root / "boot"
        (boot / "app" / "web").mkdir(parents=True)
        (boot / "app" / "main.py").write_text("new", encoding="utf-8")
        (boot / "app" / "web" / "app.js").write_text("new js", encoding="utf-8")
        dst = self.root / "install" / "app"
        dst.mkdir(parents=True)
        (dst / "main.py").write_text("old", encoding="utf-8")
        (dst / "gone.py").write_text("old", encoding="utf-8")
        self.assertTrue(installer.sync_app(self.root / "install", boot))
        self.assertEqual((dst / "main.py").read_text("utf-8"), "new")
        self.assertEqual((dst / "web" / "app.js").read_text("utf-8"), "new js")
        self.assertFalse((dst / "gone.py").exists())  # 旧版本多出来的文件要清掉

    def test_sync_app_noop_when_same(self):
        boot = self.root / "boot"
        (boot / "app").mkdir(parents=True)
        (boot / "app" / "main.py").write_text("same", encoding="utf-8")
        (self.root / "app").mkdir()
        (self.root / "app" / "main.py").write_text("old", encoding="utf-8")
        self.assertTrue(installer.sync_app(self.root, boot))
        self.assertFalse(installer.sync_app(self.root, boot))  # 内容一样，什么都不做

    def test_sync_app_skips_when_boot_is_install_app(self):
        """桌面快捷方式开状态页时 boot 就是安装目录，不能自己删自己。"""
        app_dir = self.root / "app"
        app_dir.mkdir()
        (app_dir / "main.py").write_text("x", encoding="utf-8")
        self.assertFalse(installer.sync_app(self.root, self.root))
        self.assertTrue((app_dir / "main.py").exists())

    def test_sync_app_survives_locked_files(self):
        from unittest import mock
        boot = self.root / "boot"
        (boot / "app").mkdir(parents=True)
        (boot / "app" / "main.py").write_text("new", encoding="utf-8")
        (self.root / "app").mkdir()
        with mock.patch.object(installer.shutil, "copytree", side_effect=PermissionError("占用")):
            self.assertFalse(installer.sync_app(self.root, boot))  # 同步失败不该让向导起不来

    def test_wait_for_blocks_until_provide(self):
        got = {}

        def needs_keys(ctx):
            got.update(ctx.wait_for("keys"))

        inst = Installer(self.root, State.load(self.root), steps=[self.step("a", needs_keys)])
        inst.start()
        time.sleep(0.1)
        p = inst.progress()
        self.assertTrue(p["running"])
        self.assertEqual(p["waiting"], "keys")
        inst.provide("keys", {"x": 1})
        p = wait_idle(inst)
        self.assertEqual(got, {"x": 1})
        self.assertIsNone(p["waiting"])

    def test_provided_before_step_does_not_block(self):
        inst = Installer(self.root, State.load(self.root), steps=[self.step("a", lambda c: c.wait_for("keys"))])
        inst.provide("keys", {})
        inst.start()
        self.assertTrue(wait_idle(inst)["finished"])

    def test_start_twice_runs_once(self):
        gate = threading.Event()
        inst = Installer(self.root, State.load(self.root), steps=[self.step("a", lambda c: gate.wait(2))])
        inst.start()
        inst.start()
        gate.set()
        wait_idle(inst)
        self.assertEqual(self.ran, ["a"])

    def run_import(self, result):
        """用假的导入工具跑 step_import：subprocess.run 被替换成直接写结果文件。"""
        import json
        import shutil
        from unittest import mock
        pack = Path(__file__).resolve().parent.parent / "vendor" / "dafeiyu-config-pack"
        shutil.copytree(pack, self.root / "pack")
        st = State.load(self.root)
        st.data.update(ports={"dashboard": 1, "onebot": 2, "napcat_webui": 3}, owner_qq="123")
        for k in ("dashboard_password", "siliconflow", "doubao_app_id", "doubao_token"):
            st.set_secret(k, f"secret-{k}")
        seen = {}

        def fake_run(argv, **kw):
            a = json.loads(Path(argv[argv.index("--answers") + 1]).read_text("utf-8"))
            seen["answers"] = a
            seen["sf"] = Path(a["siliconflow_key_file"]).read_text("utf-8")
            if result is not None:
                Path(argv[argv.index("--result-json") + 1]).write_text(json.dumps(result), "utf-8")
            return mock.Mock(stdout="ok secret-siliconflow", stderr="")

        inst = Installer(self.root, st, steps=[])
        inst.provide("profile", {"name": "小鲸", "persona": "你是大肥鱼", "voice": "甜",
                                 "memes": {"blue-fat-fish-meme-pack": True}})
        with mock.patch.object(installer, "ensure_astrbot"), mock.patch.object(installer.subprocess, "run", fake_run):
            try:
                installer.step_import(installer.Ctx(inst))
                err = None
            except InstallError as e:
                err = str(e)
        return err, seen

    def test_import_ok(self):
        err, seen = self.run_import({"fatal": None, "results": [{"name": "人格", "ok": True}]})
        self.assertIsNone(err)
        self.assertEqual(seen["sf"], "secret-siliconflow")
        self.assertEqual(seen["answers"]["volc_app_id"], "secret-doubao_app_id")
        self.assertFalse((self.root / ".import" / "s").exists())  # 放 key 的临时文件用完就删
        log = (self.root / "logs" / "import.log").read_text("utf-8")
        self.assertNotIn("secret-siliconflow", log)
        self.assertTrue((self.root / "pack" / "persona" / "大肥鱼.md").read_text("utf-8").startswith("你是小鲸"))

    def test_import_reports_failed_items(self):
        err, _ = self.run_import({"fatal": None, "results": [{"name": "人格", "ok": True},
                                                             {"name": "语音", "ok": False},
                                                             {"name": "表情包 x", "ok": False}]})
        self.assertIn("语音、表情包 x", err)

    def test_import_fatal_or_missing_result(self):
        self.assertIn("重试", self.run_import({"fatal": "登录失败", "results": []})[0])
        self.tearDown()
        self.setUp()
        self.assertIn("重试", self.run_import(None)[0])

    def test_default_steps_order(self):
        self.assertEqual([s.key for s in installer.default_steps()],
                         ["extract", "ports", "configs", "astrbot", "napcat", "import", "shortcut", "login"])


if __name__ == "__main__":
    unittest.main()
