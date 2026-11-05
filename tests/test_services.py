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

    def test_expired_qr_is_not_reported_as_scannable(self):
        fake = FakeNapCat({"/auth/login": ok({"Credential": CRED}),
                           "/QQLogin/CheckLoginStatus": ok({"isLogin": False, "qrcodeurl": "old-code",
                                                             "loginError": "二维码已过期"})})
        self.assertEqual(services.NapCatWeb("http://x", "t", opener=fake).status(), "error")

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
        with mock.patch("subprocess.Popen") as popen, mock.patch.object(services, "_start_capture"):
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

    def test_stop_all_reads_processes_started_by_another_window(self):
        other = State.load(self.root)
        other.data["pids"] = {"astrbot": 4242, "napcat": 4243}
        other.save()
        with mock.patch.object(services, "stop") as stop:
            services.stop_all(self.state)
        self.assertEqual({call.args[1] for call in stop.call_args_list}, {"astrbot", "napcat"})

    def test_failed_stop_preserves_pid_and_reports_failure(self):
        self.state.data["pids"] = {"napcat": 4242}
        self.state.save()
        with mock.patch.object(services, "find_processes", return_value={"napcat": [4242], "astrbot": []}, create=True), \
             mock.patch.object(services, "process_path", return_value=self.layout.napcat / "node.exe", create=True), \
             mock.patch.object(services, "is_running", return_value=True), \
             mock.patch.object(services, "can_terminate", return_value=True, create=True), \
             mock.patch.object(services.subprocess, "run", return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"Access denied")):
            with self.assertRaisesRegex(RuntimeError, "4242"):
                services.stop(self.state, "napcat")
        self.assertEqual(State.load(self.root).data["pids"]["napcat"], 4242)

    def test_access_denied_is_reported_as_permission_failure(self):
        self.state.data['pids'] = {'napcat': 4242}
        self.state.save()
        with mock.patch.object(services, 'find_processes', return_value={'napcat': [4242], 'astrbot': []}), \
             mock.patch.object(services, 'process_path', return_value=self.layout.napcat / 'node.exe'), \
             mock.patch.object(services, 'is_running', return_value=True), \
             mock.patch.object(services, 'can_terminate', return_value=False, create=True), \
             mock.patch.object(services.subprocess, 'run', return_value=mock.Mock(returncode=128, stdout=b'', stderr=b'Access denied')):
            with self.assertRaises(services.StopPermissionError):
                services.stop_all(self.state)
        self.assertEqual(State.load(self.root).data['pids']['napcat'], 4242)

    def test_taskkill_child_access_denial_requests_permission_even_if_parent_can_be_opened(self):
        self.state.data['pids'] = {'napcat': 4242}
        self.state.save()
        error = '错误：无法终止子进程。原因：拒绝访问。'.encode('oem')
        with mock.patch.object(services, 'find_processes', return_value={'napcat': [4242], 'astrbot': []}), \
             mock.patch.object(services, 'process_path', return_value=self.layout.napcat / 'node.exe'), \
             mock.patch.object(services, 'is_running', return_value=True), \
             mock.patch.object(services, 'can_terminate', return_value=True), \
             mock.patch.object(services.subprocess, 'run', return_value=mock.Mock(returncode=128, stdout=b'', stderr=error)):
            with self.assertRaises(services.StopPermissionError):
                services.stop(self.state, 'napcat')

    def test_stop_does_not_report_an_error_if_failed_pid_exits_during_later_cleanup(self):
        alive = {4242: True, 4343: True}
        self.state.data['pids'] = {'napcat': 4242}
        self.state.save()
        def fake_kill(argv, **kwargs):
            if argv[2] == '4242':
                return mock.Mock(returncode=1, stdout=b'', stderr=b'busy')
            alive[4242] = alive[4343] = False
            return mock.Mock(returncode=0, stdout=b'', stderr=b'')
        with mock.patch.object(services, 'find_processes', side_effect=[{'napcat': [4242, 4343], 'astrbot': []}, {'napcat': [], 'astrbot': []}]), \
             mock.patch.object(services, 'process_path', return_value=self.layout.napcat / 'node.exe'), \
             mock.patch.object(services, 'is_running', side_effect=lambda pid: alive[pid]), \
             mock.patch.object(services, 'can_terminate', return_value=True), \
             mock.patch.object(services.subprocess, 'run', side_effect=fake_kill):
            services.stop(self.state, 'napcat')
        self.assertNotIn('napcat', State.load(self.root).data['pids'])

    def test_access_check_does_not_terminate_process(self):
        with mock.patch.object(services._k32, 'OpenProcess', return_value=1) as open_process, \
             mock.patch.object(services._k32, 'CloseHandle'):
            self.assertTrue(services.can_terminate(4242))
        open_process.assert_called_once_with(1, False, 4242)

    def test_unexpected_windows_process_query_error_is_not_treated_as_offline(self):
        with mock.patch.object(services._k32, 'OpenProcess', return_value=None), \
             mock.patch.object(services.ctypes, 'get_last_error', return_value=299):
            with self.assertRaises(OSError):
                services.is_running(4242)

    def test_elevated_helper_uses_known_script_and_windows_argument_quoting(self):
        root = self.root / 'folder & spaces'
        with mock.patch.object(services.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            services.elevated_stop(root)
        args, kwargs = run.call_args
        self.assertIn('powershell', args[0][0].lower())
        self.assertIn('-Verb RunAs', args[0][-1])
        self.assertEqual(kwargs['env']['DAFEIYU_STOP_PYTHON'], str(services.Layout(root).pythonw))
        expected = services.subprocess.list2cmdline([str(Path(services.__file__).with_name('main.py')), '--stop-elevated', '--root', str(root)])
        self.assertEqual(kwargs['env']['DAFEIYU_STOP_ARGS'], expected)

    def test_elevated_helper_refusal_is_a_clear_failure(self):
        with mock.patch.object(services.subprocess, 'run', return_value=mock.Mock(returncode=1223)):
            with self.assertRaisesRegex(RuntimeError, '授权'):
                services.elevated_stop(self.root)

    def test_stop_recovers_a_missing_process_record(self):
        with mock.patch.object(services, "find_processes", side_effect=[{"napcat": [4242], "astrbot": []}, {"napcat": [], "astrbot": []}], create=True), \
             mock.patch.object(services, "process_path", return_value=self.layout.napcat / "node.exe", create=True), \
             mock.patch.object(services, "is_running", side_effect=[True, False, False]), \
             mock.patch.object(services.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")) as kill:
            services.stop(self.state, "napcat")
        kill.assert_called_once()
        self.assertIn("4242", kill.call_args.args[0])

    def test_stale_pid_cannot_kill_an_unrelated_program(self):
        self.state.data["pids"] = {"napcat": 4242}
        self.state.save()
        with mock.patch.object(services, "find_processes", return_value={"napcat": [], "astrbot": []}, create=True), \
             mock.patch.object(services, "process_path", return_value=Path("C:/Other/node.exe"), create=True), \
             mock.patch.object(services, "is_running", return_value=True), \
             mock.patch.object(services.subprocess, "run") as kill:
            services.stop(self.state, "napcat")
        kill.assert_not_called()

    def test_build_can_start_with_a_separate_new_state_directory(self):
        state = State.load(self.root / "build-state")
        with mock.patch.object(services.subprocess, "Popen", return_value=mock.Mock(pid=4242)), \
             mock.patch.object(services, "_start_capture"):
            self.assertEqual(services.start_astrbot(self.layout, state), 4242)

    def test_build_stops_the_stage_runtime_instead_of_the_state_directory(self):
        state = State.load(self.root / "build-state")
        state.data["pids"] = {"astrbot": 4242}
        state.save()
        with mock.patch.object(services, "find_processes", side_effect=[{"astrbot": [4242], "napcat": []}, {"astrbot": [], "napcat": []}]), \
             mock.patch.object(services, "process_path", return_value=self.layout.python), \
             mock.patch.object(services, "is_running", side_effect=[True, True, False, False]), \
             mock.patch.object(services.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")) as kill:
            services.stop(state, "astrbot", layout=self.layout)
        kill.assert_called_once()


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
        with mock.patch.object(services, "is_running", return_value=False):
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

    def test_preserves_shared_account_login_and_chat_data(self):
        self.clear()
        self.assertTrue((self.tencent / "374451537" / "nt_db").exists())
        self.assertTrue((self.login / ".374451537").exists())

    def test_same_account_in_another_install_is_preserved(self):
        other = self.root / "second-install"
        cfg = services.Layout(other).napcat_work / "config"
        cfg.mkdir(parents=True)
        config = cfg / "onebot11_374451537.json"
        config.write_text('{"second": true}', encoding="utf-8")
        other_state = State.load(other)
        other_state.data["qq"] = "374451537"
        other_state.save()
        self.clear()
        self.assertEqual(State.load(other).data["qq"], "374451537")
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"second": True})
        self.assertTrue((self.login / ".374451537").exists())

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

    def test_config_junction_does_not_delete_other_install_config(self):
        cfg = self.layout.napcat_work / "config"
        shared = self.root / "shared-config"
        cfg.rename(shared)
        services.subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET | Out-Null"],
            env=dict(services.os.environ, TEST_LINK=str(cfg), TEST_TARGET=str(shared)),
            capture_output=True, check=True, creationflags=services.CREATE_NO_WINDOW)
        self.clear()
        self.assertTrue((shared / "onebot11_374451537.json").exists())
        self.assertNotIn("qq", self.state.data)

    def test_workdir_junction_does_not_delete_other_install_cache(self):
        shared = self.root / "shared-napcat-data"
        self.layout.napcat_work.rename(shared)
        services.subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET | Out-Null"],
            env=dict(services.os.environ, TEST_LINK=str(self.layout.napcat_work), TEST_TARGET=str(shared)),
            capture_output=True, check=True, creationflags=services.CREATE_NO_WINDOW)
        self.clear()
        self.assertTrue((shared / "cache" / "qrcode.png").exists())
        self.assertTrue((shared / "config" / "onebot11_374451537.json").exists())
        self.assertNotIn("qq", self.state.data)


class ProcessTest(unittest.TestCase):
    def test_recovers_and_stops_a_real_node_process_tree(self):
        import shutil
        import subprocess
        import time
        source = Path(__file__).resolve().parents[1] / ".tmp" / "build" / "stage" / "napcat" / "node.exe"
        if not source.is_file():
            self.skipTest("packaged node is not available")
        with tempfile.TemporaryDirectory() as tmp:
            layout = services.Layout(Path(tmp))
            layout.napcat.mkdir()
            node = layout.napcat / "node.exe"
            shutil.copy2(source, node)
            code = "require('child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});setInterval(()=>{},1000)"
            process = subprocess.Popen([str(node), "-e", code], creationflags=services.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 5
                while len(services.find_processes(layout)["napcat"]) < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)
                found = services.find_processes(layout)["napcat"]
                self.assertEqual(len(found), 2)
                self.assertEqual(found[0], process.pid)
                state = State.load(Path(tmp))  # 模拟旧版已丢失全部 PID 的安装
                services.stop_all(state)
                process.wait(timeout=5)
                self.assertEqual(services.find_processes(layout)["napcat"], [])
                self.assertTrue(all(not services.is_running(pid) for pid in found))
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=services.CREATE_NO_WINDOW)
                    process.wait(timeout=5)

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
