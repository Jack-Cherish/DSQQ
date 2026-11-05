import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import checks
import main
import server
from store import State, instance_home
from test_checks import fake_opener
from test_server import FakeChecks


class RecoveryFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def app(self, mode='install', **options):
        app = server.App(mode, self.root, boot=self.root, checks_mod=FakeChecks, **options)
        self.addCleanup(app.close)
        return app

    def test_shortcut_resumes_incomplete_installation(self):
        state = State.load(self.root)
        state.data['done'] = ['extract', 'ports', 'configs', 'import']
        state.save()
        actual = main.resolve_mode(mock.Mock(root=str(self.root), status=True), self.root / 'home')
        self.assertEqual(actual, ('install', self.root))

    def test_legacy_status_window_can_complete_pending_official_binding(self):
        app = self.app('status')
        app.state.data.update(platform='official', keys_ok=True)
        app.installer = mock.Mock()
        pending = app.handle_official_bind({})
        self.assertTrue(pending['ok'], pending)
        server.official_binding.capture(pending['code'], 'A' * 32, self.root)
        seen = app.handle_official_bind({})
        self.assertTrue(app.handle_official_bind_confirm({'candidate_id': seen['candidate_id']})['ok'])
        app.installer.provide.assert_called_with('official_bound', {'owner_qq': 'A' * 32})

    def test_official_credentials_verified_without_leaking_response(self):
        op = fake_opener(200, {'access_token': 'DO_NOT_RETURN_TOKEN', 'expires_in': '7200'})
        result = checks.check_official('123456', 'secret', opener=op)
        self.assertTrue(result[0])
        self.assertNotIn('DO_NOT_RETURN_TOKEN', str(result))
        self.assertEqual(op.calls[0].full_url, checks.QQ_ACCESS_TOKEN)
        self.assertEqual(json.loads(op.calls[0].data), {'appId': '123456', 'clientSecret': 'secret'})
        result = checks.check_official('123456', 'bad', opener=fake_opener(200, {'code': 100016, 'message': 'secret'}))
        self.assertFalse(result[0])
        self.assertIn('AppSecret', result[1])

    def test_official_check_fails_closed_for_network_and_malformed_responses(self):
        for error in (TimeoutError('secret'), urllib.error.URLError('secret')):
            with self.subTest(error=error):
                ok, message = checks.check_official('123', 'secret', opener=mock.Mock(side_effect=error))
                self.assertFalse(ok)
                self.assertNotIn('secret', message)
        for status, data in ((200, []), (200, {'access_token': 'secret'}), (429, {'message': 'secret'})):
            self.assertFalse(checks.check_official('123', 'secret', opener=fake_opener(status, data))[0])

    def test_second_controller_cannot_open_or_change_same_installation(self):
        first = self.app(exclusive=True)
        first.state.data['sentinel'] = 'preserve'
        first.state.save()
        before = (self.root / 'state.json').read_bytes()
        with mock.patch.object(server.installer, 'sync_app') as sync:
            second = self.app('status', exclusive=True)
        self.assertTrue(second.recovery['busy'])
        self.assertIsNone(second.state)
        sync.assert_not_called()
        self.assertEqual((self.root / 'state.json').read_bytes(), before)
        first.close()
        self.assertTrue(second.handle_recover({'action': 'recheck'})['ok'])

    def test_existing_global_wizard_is_found_only_for_its_actual_root(self):
        home = self.root / 'home'
        home.mkdir()
        lock = home / 'wizard-install.lock'
        lock.write_text(json.dumps({'root': str(self.root), 'pid': os.getpid() + 1}))
        with mock.patch.object(main, 'existing_instance', return_value='local-window') as lookup:
            self.assertEqual(main.find_existing(self.root, home), 'local-window')
            self.assertIsNone(main.find_existing(self.root / 'other', home))
            self.assertEqual(lookup.call_count, 1)
        lock.write_text(json.dumps({'root': str(self.root), 'pid': os.getpid()}))
        with mock.patch.object(main, 'existing_instance') as lookup:
            self.assertIsNone(main.find_existing(self.root, home))
            lookup.assert_not_called()

    def test_invalid_official_key_does_not_overwrite_saved_credentials(self):
        app = self.app()
        app.state.data.update(platform='official', voice='0', memory='0')
        app.state.set_secret('official_appid', '123456')
        app.state.set_secret('official_secret', 'previous-good')
        app.state.save()
        before = (self.root / 'state.json').read_bytes()
        result = app.handle_keys({'official_appid': '123456', 'official_secret': 'bad-key',
                                  'deepseek': 'sk-test', 'voice': '0', 'memory': '0'})
        self.assertFalse(result['ok'])
        self.assertEqual((self.root / 'state.json').read_bytes(), before)

    def test_new_credentials_invalidate_a_candidate_from_the_previous_bot(self):
        app = self.app()
        app.state.data.update(platform='official', keys_ok=True)
        pending = app.handle_official_bind({})
        server.official_binding.capture(pending['code'], 'A' * 32, self.root)
        old_id = app.handle_official_bind({})['candidate_id']
        result = app.handle_keys({'official_appid': '123456', 'official_secret': 'new', 'deepseek': 'sk-test'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(app.handle_official_bind_confirm({'candidate_id': old_id})['ok'])
        self.assertEqual(app.handle_official_bind({})['candidate_id'], '')

    def test_reconnect_preserves_completed_extract_and_import(self):
        app = self.app()
        app.installer = mock.Mock()
        with mock.patch.object(app, '_stop_runtime') as stop, mock.patch.object(server.services, 'allow_start'):
            self.assertTrue(app.handle_reconnect({})['ok'])
        stop.assert_called_once()
        app.installer.redo.assert_called_once_with(['astrbot', 'login'])
        with app._lock:
            self.assertFalse(app.handle_reconnect({})['ok'])
        self.assertEqual(app.installer.redo.call_count, 1)

    def test_token_service_failure_is_not_misreported_as_invalid_secret(self):
        logs = self.root / 'logs'
        logs.mkdir()
        (logs / 'astrbot.log').write_text('获取token失败\nserver unavailable', encoding='utf-8')
        self.assertEqual(server.diagnostics.official_error(self.root)['code'], 'qq_connection_failed')
        (logs / 'astrbot.log').write_text("{'code': 100016}", encoding='utf-8')
        self.assertEqual(server.diagnostics.official_error(self.root)['code'], 'qq_credentials_invalid')

    def test_bound_owner_cannot_be_silently_transferred_to_another_appid(self):
        app = self.app()
        app.state.data.update(platform='official', owner_qq='A' * 32)
        app.state.set_secret('official_appid', '123')
        app.state.save()
        before = (self.root / 'state.json').read_bytes()
        result = app.handle_keys({'official_appid': '456', 'official_secret': 'valid', 'deepseek': 'sk-test'})
        self.assertFalse(result['ok'])
        self.assertEqual((self.root / 'state.json').read_bytes(), before)

    def test_upgrade_waits_for_old_controller_exit_before_writing(self):
        lock = self.root / 'old.lock'
        lock.write_text(json.dumps({'port': 1234, 'token': 'test', 'pid': 100, 'version': 'old'}))
        def response(value):
            result = mock.MagicMock()
            result.__enter__.return_value.read.return_value = json.dumps(value).encode()
            return result
        replies = [response({'ok': True}), response({'running': True, 'waiting': 'official_bound'}),
                   response({'ok': True}), urllib.error.URLError('controller exited')]
        with mock.patch.object(main.urllib.request, 'urlopen', side_effect=replies) as request:
            self.assertIsNone(main.existing_instance(lock, version='new'))
        self.assertEqual(request.call_count, 4)
