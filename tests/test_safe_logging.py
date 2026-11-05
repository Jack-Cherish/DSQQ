import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import safe_logging as logs


class SafeLoggingTest(unittest.TestCase):
    def test_masks_credentials_accounts_and_profile_paths_but_keeps_failure_details(self):
        text = '\x1b[31mERROR PID=123456 WinError 5 code=45000030 QQ=374451537 Authorization: Bearer private-token\n'
        text += 'WebUi Token: abc123secret\n"password": "a secret with spaces"\n'
        text += 'GET https://example.test/api?token=link-secret&uin=374451537\n'
        text += 'File "C:\\Users\\private-name\\DaFeiYu\\astrbot\\main.py", line 8\n'
        text += "ModuleNotFoundError: No module named 'astrbot'"
        value = logs.sanitize(text)
        for secret in ('private-token', 'abc123secret', 'a secret with spaces', 'link-secret', '374451537', 'private-name', '\x1b'):
            self.assertNotIn(secret, value)
        for diagnostic in ('PID=123456', 'WinError 5', 'code=45000030', 'main.py', 'line 8', 'ModuleNotFoundError', "'astrbot'"):
            self.assertIn(diagnostic, value)

    def test_chat_and_multiline_payload_are_omitted_without_losing_next_traceback(self):
        parser = logs.Sanitizer()
        lines = ['11-04 13:50:56 [info] private-nick | 接收 <- 私聊 (374451537) secret conversation',
                 'another private line', 'Traceback (most recent call last):',
                 '  File "main.py", line 8, in <module>', "ModuleNotFoundError: No module named 'astrbot'"]
        value = '\n'.join(filter(None, (parser.line(line) for line in lines)))
        for private in ('private-nick', 'secret conversation', 'another private line', '374451537'):
            self.assertNotIn(private, value)
        self.assertIn('Traceback', value)
        self.assertIn('ModuleNotFoundError', value)

    def test_chat_date_continuation_is_private_but_error_messages_remain_useful(self):
        parser = logs.Sanitizer()
        parser.line('11-04 13:50:56 [info] private-nick | 接收 <- private-body')
        self.assertEqual(parser.line('2026-11-04 private-continuation'), '')
        error = '[13:50:56.000] [Core] [ERRO] [source:88]: request rejected: messages: invalid role; code=45000030'
        self.assertEqual(parser.line(error), error)

    def test_rotation_is_bounded_and_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'service.log'
            for n in range(30):
                self.assertTrue(logs.append_log(path, f'阶段 {n}：中文错误 sk-private-key', max_bytes=180, backups=2))
            files = [path, path.with_name('service.log.1'), path.with_name('service.log.2')]
            self.assertTrue(all(p.stat().st_size <= 180 for p in files))
            for p in files:
                self.assertNotIn('sk-private-key', p.read_text(encoding='utf-8'))
            self.assertFalse(path.with_name('service.log.3').exists())

    def test_log_failure_does_not_raise_or_fallback_to_raw_data(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'service.log'
            with mock.patch.object(Path, 'open', side_effect=PermissionError('denied')):
                self.assertFalse(logs.append_log(path, 'token=secret-value'))

    def test_bounded_lines_discard_giant_payload_and_keep_next_error(self):
        stream = io.BytesIO(b'private-payload' * 10000 + b'\nModuleNotFoundError: astrbot\n')
        values = list(logs.iter_lines(stream, limit=1024))
        self.assertEqual(len(values), 2)
        self.assertNotIn('private-payload', ''.join(values))
        self.assertIn('ModuleNotFoundError', values[-1])

    def test_exact_secret_is_masked_even_without_a_label(self):
        self.assertNotIn('unlabelled-private', logs.sanitize('request unlabelled-private failed', ['unlabelled-private']))

    def test_existing_logs_and_backups_are_sanitized_and_capped_before_append(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'service.log'
            for p in (path, path.with_name('service.log.1')):
                p.write_text(('token=old-private 中文错误\n' * 500) + 'ModuleNotFoundError: astrbot\n', encoding='utf-8')
            self.assertTrue(logs.append_log(path, 'PID=345678 started', max_bytes=256, root=Path(d)))
            for p in Path(d).glob('service.log*'):
                if p.suffix == '.lock':
                    continue
                text = p.read_text(encoding='utf-8')
                self.assertNotIn('old-private', text)
                self.assertLessEqual(p.stat().st_size, 256)
            self.assertIn('ModuleNotFoundError', ''.join(p.read_text(encoding='utf-8') for p in Path(d).glob('service.log*')))

    def test_astrbot_console_payloads_and_whole_cookie_are_hidden(self):
        prefix = '[13:50:56.000] [Core] [INFO] [event_bus:117]: '
        for payload in ('[default] [dafeiyu(aiocqhttp)] private-nickname/374451537: private-body',
                        'Prepare to send - private-nickname/374451537: private-body',
                        '使用工具：search，参数：{"query":"private-body"}',
                        'Tool `search` Result: private-body', 'Speech-to-text result: private-body',
                        '[aiocqhttp] RawMessage {"message":"private-body"}'):
            value = logs.sanitize(prefix + payload)
            self.assertNotIn('private-body', value)
            self.assertNotIn('private-nickname', value)
            self.assertIn('[event_bus:117]', value)
        value = logs.sanitize('Cookie: session=private-one; credential=private-two; auth=private-three')
        for token in ('private-one', 'private-two', 'private-three'):
            self.assertNotIn(token, value)

    def test_legacy_migration_reads_only_bounded_tail(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'service.log'
            path.write_bytes(b'private-fragment\n' * 10000 + b'Traceback (most recent call last):\nModuleNotFoundError: astrbot\n')
            calls = []
            original = logs.iter_lines
            def bounded(stream, *args, **kwargs):
                calls.append(stream.tell())
                yield from original(stream, *args, **kwargs)
            with mock.patch.object(logs, 'iter_lines', bounded):
                self.assertTrue(logs.append_log(path, 'done', root=Path(d), max_bytes=512))
            self.assertGreater(calls[0], 100000)
            self.assertNotIn('private-fragment', path.read_text(encoding='utf-8'))

