import base64
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services
from store import State


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNapCat:
    """按路径返回预设结果的 urlopen 替身。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        path = req.full_url.split("/api", 1)[1]
        value = self.routes[path]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return _Resp(json.dumps(value).encode())


def ok(data):
    return {"code": 0, "data": data, "message": "success"}


CRED = base64.b64encode(b'{"Data":1}').decode()


class NapCatWebTest(unittest.TestCase):
    def test_login_hash(self):
        fake = FakeNapCat({"/auth/login": ok({"Credential": CRED}),
                           "/QQLogin/CheckLoginStatus": ok({"isLogin": True, "isOffline": False})})
        web = services.NapCatWeb("http://127.0.0.1:6099", "tok123", opener=fake)
        self.assertEqual(web.status(), "online")
        body = json.loads(fake.requests[0].data)
        self.assertEqual(body["hash"], hashlib.sha256(b"tok123.napcat").hexdigest())
        self.assertEqual(fake.requests[1].get_header("Authorization"), f"Bearer {CRED}")

    def test_relogin_after_unauthorized(self):
        fake = FakeNapCat({"/auth/login": [ok({"Credential": CRED}), ok({"Credential": CRED})],
                           "/QQLogin/CheckLoginStatus": [{"code": -1, "message": "Unauthorized"},
                                                         ok({"isLogin": True, "isOffline": False})]})
        web = services.NapCatWeb("http://127.0.0.1:6099", "t", opener=fake)
        self.assertEqual(web.status(), "online")
        self.assertEqual(sum(r.full_url.endswith("/auth/login") for r in fake.requests), 2)

    def test_login_status_maps_phases(self):
        cases = [
            ({"isLogin": True, "isOffline": False}, "online"),
            ({"isLogin": False, "isOffline": True}, "offline"),
            ({"isLogin": False, "loginPhase": "qrcode_scanned", "qrcodeurl": "x"}, "scanned"),
            ({"isLogin": False, "loginPhase": "waiting_qrcode", "qrcodeurl": "https://q"}, "need_scan"),
            ({"isLogin": False, "loginError": "二维码已过期", "qrcodeurl": ""}, "error"),
            ({"isLogin": False, "qrcodeurl": ""}, "starting"),
        ]
        for data, expected in cases:
            fake = FakeNapCat({"/auth/login": ok({"Credential": CRED}), "/QQLogin/CheckLoginStatus": ok(data)})
            self.assertEqual(services.NapCatWeb("http://x", "t", opener=fake).status(), expected, data)

    def test_unreachable_is_starting(self):
        import urllib.error
        fake = FakeNapCat({"/auth/login": urllib.error.URLError("refused")})
        self.assertEqual(services.NapCatWeb("http://x", "t", opener=fake).status(), "starting")

    def test_login_uin(self):
        fake = FakeNapCat({"/auth/login": ok({"Credential": CRED}),
                           "/QQLogin/GetQQLoginInfo": ok({"uin": "123456", "nick": "鱼"})})
        self.assertEqual(services.NapCatWeb("http://x", "t", opener=fake).login_uin(), "123456")


class StartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.layout = services.Layout(self.root)
        self.state = State.load(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, fn, *args):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            pid = fn(self.layout, self.state, *args)
        self.assertEqual(pid, 4242)
        (argv,), kw = popen.call_args
        return argv, kw

    def test_start_napcat_command_line(self):
        argv, kw = self.start(services.start_napcat, None)
        self.assertEqual(argv, [str(self.layout.napcat / "node.exe"), str(self.layout.napcat / "index.js")])
        self.assertEqual(kw["cwd"], str(self.layout.napcat))
        self.assertEqual(kw["env"]["NAPCAT_WORKDIR"], str(self.layout.napcat_work))
        self.assertTrue(kw["creationflags"] & services.CREATE_NO_WINDOW)
        argv, _ = self.start(services.start_napcat, "123456")
        self.assertEqual(argv[-2:], ["-q", "123456"])
        self.assertEqual(self.state.data["pids"]["napcat"], 4242)

    def test_start_astrbot_command_line(self):
        argv, kw = self.start(services.start_astrbot)
        self.assertEqual(argv, [str(self.layout.python), "main.py"])
        self.assertEqual(kw["cwd"], str(self.layout.astrbot))
        self.assertEqual(kw["env"]["ASTRBOT_ROOT"], str(self.layout.astrbot))
        self.assertEqual(kw["env"]["PYTHONUTF8"], "1")
        self.assertEqual(self.state.data["pids"]["astrbot"], 4242)


class ClearQQLoginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.layout = services.Layout(self.root)
        self.state = State.load(self.root)
        cfg = self.layout.napcat_work / "config"
        cfg.mkdir(parents=True)
        for name in ("webui.json", "onebot11.json", "onebot11_374451537.json", "napcat_374451537.json"):
            (cfg / name).write_text("{}", encoding="utf-8")
        (self.layout.napcat_work / "cache").mkdir()
        (self.layout.napcat_work / "cache" / "qrcode.png").write_bytes(b"x")
        self.state.data["qq"] = "374451537"
        self.state.data["owner_qq"] = "1960074081"
        self.state.data["pids"] = {"napcat": 1, "astrbot": 2}
        self.state.save()  # pids 以盘上的为准，内存里不落盘就不算数
        # 假的「Tencent Files」：真目录是用户自己 QQ 的数据，测试绝对不能碰
        self.tencent = self.root / "fake-tencent" / "Tencent Files"
        self.login = self.tencent / "nt_qq" / "global" / "nt_data" / "Login"
        self.login.mkdir(parents=True)
        for qq in ("374451537", "1960074081"):
            (self.tencent / qq).mkdir()
            (self.tencent / qq / "nt_db").mkdir()
            (self.login / f".{qq}").write_bytes(b"")

    def tearDown(self):
        self.tmp.cleanup()

    def clear(self):
        with mock.patch.object(services, "is_running", return_value=False), \
             mock.patch.object(services, "tencent_roots", return_value=[self.tencent]):
            services.clear_qq_login(self.layout, self.state)

    def test_forgets_account_but_keeps_shared_config(self):
        self.clear()
        cfg = self.layout.napcat_work / "config"
        self.assertNotIn("qq", self.state.data)
        self.assertFalse((cfg / "onebot11_374451537.json").exists())
        self.assertFalse((cfg / "napcat_374451537.json").exists())
        self.assertFalse((self.layout.napcat_work / "cache").exists())
        # 这两个是连接设置，不按号区分，删了 AstrBot 就连不上了
        self.assertTrue((cfg / "webui.json").exists())
        self.assertTrue((cfg / "onebot11.json").exists())

    def test_stops_only_napcat(self):
        self.clear()
        self.assertNotIn("napcat", self.state.data["pids"])
        self.assertIn("astrbot", self.state.data["pids"])

    def test_leaves_qq_runtime_alone(self):
        """用户自己的 QQ 数据目录和 QQ 客户端共用，碰了会把人家常用号挤下线。"""
        self.layout.napcat.mkdir(parents=True, exist_ok=True)
        (self.layout.napcat / "guild1.db").write_bytes(b"x")
        self.clear()
        self.assertTrue((self.layout.napcat / "guild1.db").exists())

    def test_logs_out_the_account_for_real(self):
        """不删这个号在 QQ 数据目录里的东西，NapCat 的快速登录列表里还有它，能自己登回来。"""
        self.clear()
        self.assertFalse((self.tencent / "374451537").exists())
        self.assertFalse((self.login / ".374451537").exists())

    def test_never_touches_owner_or_shared_data(self):
        """主人自己常用的号、所有号共用的 global，碰了就是把人家挤下线。"""
        self.clear()
        self.assertTrue((self.tencent / "1960074081" / "nt_db").exists())
        self.assertTrue((self.login / ".1960074081").exists())
        self.assertTrue((self.tencent / "nt_qq" / "global").exists())

    def test_owner_qq_as_bot_is_left_alone(self):
        """小号填成了主人自己的号：宁可留着也不能误删主人的聊天记录。"""
        self.state.data["qq"] = "1960074081"
        self.clear()
        self.assertTrue((self.tencent / "1960074081" / "nt_db").exists())


class TencentRootsTest(unittest.TestCase):
    def test_picks_up_relocated_dir_from_napcat_log(self):
        """QQ 的数据目录可以改到别处，改过的路径只能从 NapCat 日志里捞。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            layout = services.Layout(base / "root")
            moved = base / "MyCache" / "QQ" / "Tencent Files"
            moved.mkdir(parents=True)
            layout.logs.mkdir(parents=True)
            (layout.logs / "napcat.log").write_text(
                f'wrote "{moved}\\NapCat\\temp\\x.png" ok\n', encoding="utf-8")
            with mock.patch.object(services.Path, "home", staticmethod(lambda: base / "nobody")):
                self.assertEqual(services.tencent_roots(layout), [moved])

    def test_no_log_and_no_documents_means_nothing_to_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch.object(services.Path, "home", staticmethod(lambda: base / "nobody")):
                self.assertEqual(services.tencent_roots(services.Layout(base / "root")), [])


class ProcessTest(unittest.TestCase):
    def test_is_running(self):
        import os
        import subprocess
        import sys
        self.assertTrue(services.is_running(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(services.is_running(p.pid))
        self.assertFalse(services.is_running(None))

    def test_wait_http_times_out_and_succeeds(self):
        import urllib.error
        fails = mock.Mock(side_effect=urllib.error.URLError("x"))
        self.assertFalse(services.wait_http("http://x", 0, opener=fails, interval=0))
        self.assertTrue(services.wait_http("http://x", 0, opener=lambda *a, **k: _Resp(b"")))
        http_err = mock.Mock(side_effect=urllib.error.HTTPError("http://x", 401, "no", {}, io.BytesIO()))
        self.assertTrue(services.wait_http("http://x", 0, opener=http_err))


if __name__ == "__main__":
    unittest.main()
