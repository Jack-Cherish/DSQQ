import io
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path

import checks


def fake_opener(status, body):
    """返回一个替身 urlopen：成功时给 body，status>=400 时抛 HTTPError。"""
    calls = []

    def opener(req, timeout=None):
        calls.append(req)
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(data))
        return _Resp(data, status)

    opener.calls = calls
    return opener


class _Resp(io.BytesIO):
    def __init__(self, data, status):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def raising_opener(req, timeout=None):
    raise urllib.error.URLError("no route")


class PathTest(unittest.TestCase):
    def test_path_rules(self):
        self.assertIsNotNone(checks.check_install_path("D:\\大肥鱼"))
        self.assertIsNotNone(checks.check_install_path("D:\\Da Fei"))
        self.assertIsNotNone(checks.check_install_path("relative\\dir"))
        self.assertIsNotNone(checks.check_install_path(""))
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "DaFeiYu"
            if str(target).isascii() and " " not in str(target):
                self.assertIsNone(checks.check_install_path(str(target), min_free_gb=0))

    def test_nonempty_foreign_dir_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.txt").write_text("x")
            if str(d).isascii() and " " not in str(d):
                self.assertIn("已经有", checks.check_install_path(d, min_free_gb=0))
                (Path(d) / "state.json").write_text("{}")
                self.assertIsNone(checks.check_install_path(d, min_free_gb=0))

    def test_disk_space(self):
        with tempfile.TemporaryDirectory() as d:
            if str(d).isascii() and " " not in str(d):
                self.assertIn("空间", checks.check_install_path(str(Path(d) / "a"), min_free_gb=10**9))

    def test_existing_install_with_missing_state_can_reach_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            backup = root / 'state.json.bak'
            backup.write_text('{}', encoding='utf-8')
            self.assertIsNone(checks.check_install_path(d, min_free_gb=0))
            backup.unlink()
            config = root / 'astrbot' / 'data' / 'cmd_config.json'
            config.parent.mkdir(parents=True)
            config.write_text('{}', encoding='utf-8')
            self.assertIsNone(checks.check_install_path(d, min_free_gb=0))


class PortTest(unittest.TestCase):
    def test_free_port_skips_busy(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        try:
            port = checks.free_port(busy)
            self.assertNotEqual(port, busy)
            t = socket.socket()
            t.bind(("127.0.0.1", port))
            t.close()
        finally:
            s.close()


class KeyTest(unittest.TestCase):
    def test_deepseek_ok(self):
        op = fake_opener(200, {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "9.50"}]})
        ok, msg = checks.check_deepseek("sk-x", opener=op)
        self.assertTrue(ok)
        self.assertIn("9.50", msg)
        self.assertEqual(op.calls[0].get_header("Authorization"), "Bearer sk-x")

    def test_deepseek_invalid_key(self):
        ok, msg = checks.check_deepseek("sk-x", opener=fake_opener(401, {"error": {}}))
        self.assertFalse(ok)
        self.assertIn("key 不对", msg)

    def test_deepseek_no_balance(self):
        ok, msg = checks.check_deepseek("sk-x", opener=fake_opener(200, {"is_available": False, "balance_infos": []}))
        self.assertFalse(ok)
        self.assertIn("充值", msg)

    def test_siliconflow(self):
        self.assertTrue(checks.check_siliconflow("k", opener=fake_opener(200, {"data": [{"embedding": [0.1]}]}))[0])
        ok, msg = checks.check_siliconflow("k", opener=fake_opener(401, {"message": "Invalid token"}))
        self.assertFalse(ok)
        self.assertIn("key 不对", msg)

    def test_doubao_ok_returns_audio(self):
        op = fake_opener(200, {"audio": "QUJD", "subtitle": {"text": "你好呀"}})
        ok, msg, audio = checks.check_doubao("app", "tok", opener=op)
        self.assertTrue(ok)
        self.assertEqual(audio, "QUJD")
        self.assertEqual(op.calls[0].get_header("X-api-app-id"), "app")
        self.assertEqual(op.calls[0].get_header("X-api-resource-id"), "seed-audio-1.0")

    def test_doubao_not_granted(self):
        ok, msg, _ = checks.check_doubao("a", "t", opener=fake_opener(403, {"code": 45000030, "message": "requested resource not granted"}))
        self.assertFalse(ok)
        self.assertIn("试用", msg)

    def test_doubao_bad_credentials(self):
        ok, msg, _ = checks.check_doubao("a", "t", opener=fake_opener(401, {"message": "invalid auth"}))
        self.assertFalse(ok)
        self.assertIn("APP ID 或 Access Token 不对", msg)

    def test_network_error(self):
        ok, msg = checks.check_deepseek("sk", opener=raising_opener)
        self.assertFalse(ok)
        self.assertIn("连不上", msg)
        self.assertFalse(checks.check_doubao("a", "t", opener=raising_opener)[0])


if __name__ == "__main__":
    unittest.main()
