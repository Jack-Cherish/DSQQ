import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import services
from store import State


class LogCaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.layout = services.Layout(self.root)
        self.layout.pythonw = Path(sys.executable).with_name('pythonw.exe')
        self.state = State.load(self.root)
        self.state.set_secret('deepseek', 'unlabelled-private')
        self.state.save()

    def test_child_output_is_redacted_utf8_and_reader_exits_at_eof(self):
        code = "print('token=private-secret 中文错误', flush=True); print('unlabelled-private'); print('ModuleNotFoundError: astrbot')"
        p = services.logged_process('astrbot', [sys.executable, '-X', 'utf8', '-c', code], self.root, {}, self.layout, self.state)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertEqual(p.log_reader.wait(timeout=10), 0)
        text = (self.layout.logs / 'astrbot.log').read_text(encoding='utf-8')
        self.assertIn('中文错误', text)
        self.assertIn('ModuleNotFoundError', text)
        self.assertNotIn('private-secret', text)
        self.assertNotIn('unlabelled-private', text)

    def test_capture_initialization_failure_never_starts_service(self):
        with mock.patch.object(services, '_start_capture', side_effect=OSError('disk unavailable')), \
             mock.patch.object(services.subprocess, 'Popen') as popen:
            with self.assertRaises(OSError):
                services.logged_process('astrbot', ['never-start'], self.root, {}, self.layout, self.state)
        popen.assert_not_called()

    def test_stop_with_capture_ends_the_owned_service_and_reader(self):
        node = Path(__file__).resolve().parents[1] / '.tmp' / 'build' / 'stage' / 'napcat' / 'node.exe'
        if not node.exists():
            self.skipTest('local test Node runtime is unavailable')
        self.layout.napcat.mkdir()
        shutil.copy2(node, self.layout.napcat / 'node.exe')
        (self.layout.napcat / 'index.js').write_text("console.log('token=private-running'); setInterval(()=>{}, 1000)", encoding='utf-8')
        launched = []
        original = services.logged_process
        def remember(*args):
            p = original(*args)
            launched.append(p)
            return p
        try:
            with mock.patch.object(services, 'logged_process', remember):
                pid = services.start_napcat(self.layout, self.state, None)
            self.assertTrue(services.is_running(pid))
            services.stop_all(self.state)
            self.assertFalse(services.is_running(pid))
            self.assertEqual(launched[0].log_reader.wait(timeout=10), 0)
            self.assertEqual(State.load(self.root).data['pids'], {})
            self.assertTrue(State.load(self.root).data['services_stopped'])
        finally:
            for p in launched:
                if p.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True,
                                   creationflags=services.CREATE_NO_WINDOW)
                p.wait(timeout=10)
                p.log_reader.wait(timeout=10)

    def test_logging_continues_after_launcher_process_exits(self):
        app_path = Path(services.__file__).parent
        code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(app_path)!r}); import services; from store import State; "
            f"root=Path({str(self.root)!r}); layout=services.Layout(root); "
            f"layout.pythonw=Path({str(self.layout.pythonw)!r}); "
            "p=services.logged_process('napcat', [sys.executable,'-X','utf8','-c',"
            "\"import time; time.sleep(0.4); print('token=private-after-exit 中文正常',flush=True)\"], root, {}, layout, State.load(root)); "
            "(root/'pids.json').write_text(__import__('json').dumps([p.pid,p.log_reader.pid]))"
        )
        parent = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=15,
                                creationflags=services.CREATE_NO_WINDOW)
        self.assertEqual(parent.returncode, 0, parent.stderr.decode('utf-8', 'replace'))
        ids = json.loads((self.root / 'pids.json').read_text())
        try:
            deadline = time.monotonic() + 10
            while any(services.is_running(pid) for pid in ids) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(any(services.is_running(pid) for pid in ids))
            text = (self.layout.logs / 'napcat.log').read_text(encoding='utf-8')
            self.assertIn('中文正常', text)
            self.assertNotIn('private-after-exit', text)
        finally:
            for pid in ids:
                if services.is_running(pid):
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                                   creationflags=services.CREATE_NO_WINDOW)
