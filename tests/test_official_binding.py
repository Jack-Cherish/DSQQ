import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import official_binding as binding

class BindingTests(unittest.TestCase):
    def test_capture_requires_valid_unexpired_code_and_accepts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = binding.create(root)
            self.assertTrue(binding.capture('绑定 wrong', 'a' * 32, root))
            self.assertTrue(binding.capture('绑定 ' + code, 'bad', root))
            self.assertTrue(binding.capture('绑定 ' + code, 'a' * 32, root))
            self.assertTrue(binding.capture('绑定 ' + code, 'b' * 32, root))
            self.assertEqual(binding.candidate(root), 'a' * 32)
            self.assertEqual(binding.consume(root, binding.session_id(root)), 'a' * 32)
            self.assertIsNone(binding.consume(root, 'a' * 32))
    def test_session_is_stable_until_expired_and_confirmation_is_bound_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = binding.create(root)
            session = binding.session_id(root)
            self.assertEqual(binding.create(root), code)
            binding.capture('绑定 ' + code, 'a' * 32, root)
            self.assertIsNone(binding.consume(root, 'b' * 32))
            self.assertEqual(binding.candidate(root), 'a' * 32)
            self.assertEqual(binding.consume(root, session), 'a' * 32)

    def test_connection_is_bound_to_process_creation_and_reports_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(binding, '_process_birth', return_value=123):
                binding.set_connection('ready', root=root, pid=42)
                self.assertEqual(binding.connection_state(root, 42), 'ready')
                self.assertEqual(binding.connection_state(root, 43), 'unknown')
                binding.set_connection('disconnected', root=root, pid=42)
                self.assertEqual(binding.connection_state(root, 42), 'disconnected')
            with mock.patch.object(binding, '_process_birth', return_value=456):
                self.assertEqual(binding.connection_state(root, 42), 'unknown')
            with mock.patch.object(binding, '_process_birth', return_value=None):
                self.assertEqual(binding.connection_state(root, 42), 'unknown')

    def test_patched_callbacks_report_only_real_connection_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'astrbot/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py'
            p.parent.mkdir(parents=True)
            p.write_text("from __future__ import annotations\nclass ManagedBotWebSocket(BotWebSocket):\n    async def on_closed(self, close_status_code, close_msg):\n        await super().on_closed(close_status_code, close_msg)\nclass botClient(Client):\n    async def bot_connect(self, session) -> None:\n        pass\n    async def on_c2c_message_create(self, message: botpy.message.C2CMessage) -> None:\n        pass\n", encoding='utf8')
            binding.install(root)
            class Client:
                pass
            class BotWebSocket:
                async def on_closed(self, *args): pass
                async def on_error(self, *args): pass
            namespace = {'Client': Client, 'BotWebSocket': BotWebSocket}
            exec(compile(p.read_text(encoding='utf8'), str(p), 'exec'), namespace)
            client = namespace['botClient']()
            socket = namespace['ManagedBotWebSocket']()
            with mock.patch.object(binding, 'set_connection') as mark:
                asyncio.run(client.bot_connect(None))
                asyncio.run(client.on_ready())
                asyncio.run(client.on_resumed())
                asyncio.run(socket.on_closed(1000, ''))
                asyncio.run(socket.on_error(RuntimeError('transport')))
            self.assertEqual([c.args[0] for c in mark.call_args_list], ['starting', 'ready', 'ready', 'disconnected', 'disconnected'])

    def test_expired_session_does_not_accept_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(binding.time, 'time', return_value=100):
                code = binding.create(root)
            with mock.patch.object(binding.time, 'time', return_value=10000):
                self.assertTrue(binding.capture('绑定 ' + code, 'a' * 32, root))
                self.assertIsNone(binding.consume(root, 'a' * 32))
    def test_patch_only_installs_hook_in_c2c_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'astrbot/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py'
            p.parent.mkdir(parents=True)
            p.write_text('class ManagedBotWebSocket(BotWebSocket):\n    async def on_closed(self, close_status_code, close_msg):\n        pass\nclass botClient(Client):\n    async def bot_connect(self, session) -> None:\n        pass\n    async def on_c2c_message_create(self, message: botpy.message.C2CMessage) -> None:\n        abm = None\n', encoding='utf8')
            def stop_before_patch():
                self.assertFalse((root / 'astrbot/official_binding.py').exists())
                self.assertNotIn(binding.HOOK, p.read_text(encoding='utf8'))
            stop = mock.Mock(side_effect=stop_before_patch)
            binding.install(root, before_update=stop)
            binding.install(root, before_update=stop)
            stop.assert_called_once_with()
            self.assertEqual(p.read_text(encoding='utf8').count('from official_binding import capture'), 1)
            self.assertTrue((root / 'astrbot/official_binding.py').is_file())


    def test_receipt_distinguishes_message_failures_without_storing_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = binding.create(root)
            self.assertEqual(binding.receipt(root), 'not_received')
            self.assertFalse(binding.capture('private chat contents', 'a' * 32, root))
            self.assertEqual(binding.receipt(root), 'not_command')
            binding.capture('绑定 ' + '0' * 16, 'a' * 32, root)
            self.assertEqual(binding.receipt(root), 'wrong_code')
            binding.capture('绑定 ' + code, 'bad', root)
            self.assertEqual(binding.receipt(root), 'invalid_sender')
            binding.capture('绑定 ' + code, 'a' * 32, root)
            self.assertEqual(binding.receipt(root), 'received')
            binding.capture('绑定 wrong', 'b' * 32, root)
            self.assertEqual(binding.receipt(root), 'received')
            record = (root / '.official-binding/receipt.json').read_text()
            for secret in (code, 'a' * 32, 'b' * 32, 'private chat contents'):
                self.assertNotIn(secret, record)

    def test_failed_candidate_write_can_be_retried_without_a_poisoned_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = binding.create(root)
            replace = binding.os.replace
            def fail_candidate(src, dest):
                if str(dest).endswith('.candidate'):
                    raise OSError('disk unavailable')
                return replace(src, dest)
            with mock.patch.object(binding.os, 'replace', side_effect=fail_candidate):
                self.assertTrue(binding.capture('绑定 ' + code, 'a' * 32, root))
            self.assertEqual(binding.receipt(root), 'write_error')
            self.assertIsNone(binding.candidate(root))
            binding.capture('绑定 ' + code, 'a' * 32, root)
            self.assertEqual(binding.candidate(root), 'a' * 32)

    def test_expired_receipt_does_not_leak_into_the_next_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(binding.time, 'time', return_value=100):
                code = binding.create(root)
            with mock.patch.object(binding.time, 'time', return_value=1000):
                binding.capture('绑定 ' + code, 'a' * 32, root)
                self.assertEqual(binding.receipt(root), 'expired')
                binding.create(root)
                self.assertEqual(binding.receipt(root), 'not_received')


    def test_result_log_survives_redaction_after_a_private_message(self):
        import contextlib
        import io
        from safe_logging import sanitize
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = binding.create(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                binding.capture('绑定 ' + code, 'a' * 32, root)
            text = sanitize('received message: private-chat-secret\n' + output.getvalue())
            self.assertIn('[DaFeiYu binding] result=received', text)
            self.assertNotIn('private-chat-secret', text)
            self.assertNotIn(code, text)
