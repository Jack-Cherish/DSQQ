import json
import tempfile
import unittest
from pathlib import Path

from store import State, protect, redact, unprotect


class StoreTest(unittest.TestCase):
    def test_stale_window_save_keeps_process_records_and_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            old = State.load(Path(d))
            live = State.load(Path(d))
            live.data.update(pids={"astrbot": 123}, qq="123456")
            live.save()
            old.mark_done("login")
            saved = State.load(Path(d)).data
            self.assertEqual(saved["pids"], {"astrbot": 123})
            self.assertEqual(saved["qq"], "123456")

    def test_stale_window_does_not_resurrect_stopped_process(self):
        with tempfile.TemporaryDirectory() as d:
            live = State.load(Path(d))
            live.data["pids"] = {"astrbot": 123}
            live.save()
            old = State.load(Path(d))
            live.data["pids"].pop("astrbot")
            live.save()
            old.data["qq"] = "123456"
            old.save()
            self.assertEqual(State.load(Path(d)).data["pids"], {})

    def test_independent_process_updates_merge(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = State.load(Path(d)), State.load(Path(d))
            a.data["pids"] = {"astrbot": 123}
            b.data["pids"] = {"napcat": 456}
            a.save()
            b.save()
            self.assertEqual(State.load(Path(d)).data["pids"], {"astrbot": 123, "napcat": 456})

    def test_concurrent_windows_merge_on_disk(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            code = "from pathlib import Path; from store import State; import sys,time; s=State.load(Path(sys.argv[1])); time.sleep(0.2); s.data[sys.argv[2]]=True; s.save()"
            children = [subprocess.Popen([sys.executable, "-c", code, d, f"window_{n}"],
                                         creationflags=subprocess.CREATE_NO_WINDOW) for n in range(6)]
            for child in children:
                self.assertEqual(child.wait(timeout=15), 0)
            saved = State.load(Path(d)).data
            self.assertTrue(all(saved.get(f"window_{n}") for n in range(6)))

    def test_dpapi_roundtrip(self):
        blob = protect("sk-abc")
        self.assertNotIn("sk-abc", blob)
        self.assertEqual(unprotect(blob), "sk-abc")

    def test_state_persists_and_hides_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            s = State.load(Path(d))
            s.set_secret("deepseek", "sk-secret-123")
            s.save()
            raw = (Path(d) / "state.json").read_text(encoding="utf-8")
            self.assertNotIn("sk-secret-123", raw)
            self.assertEqual(State.load(Path(d)).get_secret("deepseek"), "sk-secret-123")

    def test_missing_secret_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(State.load(Path(d)).get_secret("nope"), "")

    def test_mark_done(self):
        with tempfile.TemporaryDirectory() as d:
            s = State.load(Path(d))
            self.assertFalse(s.is_done("extract"))
            s.mark_done("extract")
            self.assertTrue(State.load(Path(d)).is_done("extract"))
            self.assertEqual(json.loads((Path(d) / "state.json").read_text("utf-8"))["done"], ["extract"])

    def test_reload_pids_sees_other_window(self):
        """两个向导窗口各写各的 pids，状态页必须读盘上那份，否则会显示「她下线了」。"""
        with tempfile.TemporaryDirectory() as d:
            page = State.load(Path(d))       # 状态页：启动时盘上还没有 pid
            other = State.load(Path(d))      # 另一个窗口刚起了服务
            other.data["pids"] = {"astrbot": 123, "napcat": 456}
            other.save()
            self.assertFalse(page.data.get("pids"))
            self.assertEqual(page.reload_pids(), {"astrbot": 123, "napcat": 456})

    def test_reload_pids_survives_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            s = State.load(Path(d))
            s.data["pids"] = {"astrbot": 1}
            s.save()
            (Path(d) / "state.json").unlink()
            self.assertEqual(s.reload_pids(), {})

    def test_concurrent_saves(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            states = [State.load(Path(d)) for _ in range(2)]
            errors = []

            def hammer(s):
                try:
                    for i in range(100):
                        s.data["n"] = i
                        s.save()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=hammer, args=(s,)) for s in states for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(State.load(Path(d)).data["n"], 99)

    def test_remember_install(self):
        from store import last_install, remember_install
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            self.assertIsNone(last_install(home))
            remember_install(Path("D:\\DaFeiYu"), home)
            self.assertEqual(last_install(home), "D:\\DaFeiYu")

    def test_redact_masks_all_secrets(self):
        out = redact("key sk-aaaa and tok-bbbb and ab", ["sk-aaaa", "tok-bbbb", "ab", ""])
        self.assertEqual(out, "key *** and *** and ab")


if __name__ == "__main__":
    unittest.main()
