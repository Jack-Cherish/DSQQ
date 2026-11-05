import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import installer
import server
from store import State


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_corrupt_state_opens_recovery_without_overwriting_file(self):
        path = self.root / 'state.json'
        path.write_text('{broken', encoding='utf-8')
        app = server.App('status', self.root, boot=self.root, home=self.root / 'home', steps_factory=lambda: [])
        info = app.handle_info({})
        self.assertEqual(info['mode'], 'recovery')
        self.assertIn('状态', info['recovery']['message'])
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_valid_backup_can_be_restored_and_broken_original_is_kept(self):
        path = self.root / 'state.json'
        path.write_text('{broken', encoding='utf-8')
        (self.root / 'state.json.bak').write_text(json.dumps({'done': ['login'], 'secrets': {}}), encoding='utf-8')
        app = server.App('status', self.root, boot=self.root, home=self.root / 'home', steps_factory=lambda: [])
        result = app.handle_recover({'action': 'restore_backup'})
        self.assertTrue(result['ok'])
        self.assertTrue(State.load(self.root).is_done('login'))
        self.assertTrue(any(p.read_text(encoding='utf-8') == '{broken' for p in self.root.glob('state.json.corrupt-*')))

    def test_decryption_failure_does_not_break_error_logging_or_progress(self):
        state = State.load(self.root)
        state.data['secrets']['deepseek'] = 'invalid-encrypted-value'
        def broken(ctx):
            raise installer.InstallError('request token=private-key failed')
        inst = installer.Installer(self.root, state, steps=[installer.Step('a', '测试步骤', broken)])
        with mock.patch.object(state, 'secret_values', side_effect=OSError('decryption failed')):
            inst._run()
        step = inst.progress()['steps'][0]
        self.assertEqual(step['status'], 'error')
        self.assertNotIn('private-key', step['message'])
        value = (self.root / 'logs' / 'installer.log').read_text(encoding='utf-8')
        self.assertIn('失败', value)
        self.assertNotIn('private-key', value)

    def test_structurally_invalid_state_is_rejected(self):
        for value in ([], {'pids': 'broken'}, {'secrets': []}, {'done': 'login'}):
            with self.subTest(value=value):
                (self.root / 'state.json').write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    State.load(self.root)

    def test_recovered_incomplete_install_can_continue_on_explicit_start(self):
        from test_server import FakeChecks
        (self.root / 'state.json').write_text('{broken', encoding='utf-8')
        (self.root / 'state.json.bak').write_text('{"done": [], "secrets": {}}', encoding='utf-8')
        ran = []
        app = server.App('status', self.root, boot=self.root, home=self.root / 'home', checks_mod=FakeChecks,
                         steps_factory=lambda: [installer.Step('test', 'test', lambda ctx: ran.append(True))])
        self.assertTrue(app.handle_recover({'action': 'restore_backup'})['ok'])
        self.assertEqual(app.mode, 'install')
        self.assertTrue(State.load(self.root).data['services_stopped'])
        self.assertTrue(app.handle_path({'path': str(self.root)})['ok'])
        app.installer._thread.join(3)
        self.assertEqual(ran, [True])

    def test_disabled_optional_credentials_do_not_block_key_updates(self):
        from test_server import FakeChecks
        state = State.load(self.root)
        state.data.update(memory='0', voice='0')
        state.data['secrets']['doubao_token'] = 'invalid-encrypted-value'
        state.save()
        app = server.App('install', self.root, boot=self.root, home=self.root / 'home', checks_mod=FakeChecks,
                         steps_factory=lambda: [])
        result = app.handle_keys({'deepseek': 'new-deepseek-key', 'owner_qq': '123456', 'memory': '0', 'voice': '0'})
        self.assertTrue(result['ok'])

    def test_resolve_mode_survives_invalid_install_marker_and_access_denied(self):
        import main
        from types import SimpleNamespace
        home = self.root / 'home'
        home.mkdir()
        (home / 'install.json').write_text('{"root": 123}', encoding='utf-8')
        self.assertEqual(main.resolve_mode(SimpleNamespace(root=None, status=False), home), ('install', None))
        with mock.patch.object(Path, 'exists', side_effect=PermissionError('denied')):
            self.assertEqual(main.resolve_mode(SimpleNamespace(root=str(self.root), status=False), home), ('status', self.root))

    def test_napcat_configuration_ignores_corrupt_official_credentials(self):
        state = State.load(self.root)
        state.data['ports'] = {'dashboard': 1, 'onebot': 2, 'napcat_webui': 3}
        state.data['secrets']['official_secret'] = 'invalid-encrypted-value'
        for name, value in installer.configs.new_secrets().items():
            state.set_secret(name, value)
        state.set_secret('deepseek', 'new-deepseek-key')
        inst = installer.Installer(self.root, state, steps=[])
        inst.provide('keys', {'owner_qq': '123456'})
        with mock.patch.object(installer.configs, 'write_astrbot_config') as astrbot, \
             mock.patch.object(installer.configs, 'write_napcat_config'):
            installer.step_configs(installer.Ctx(inst))
        self.assertEqual(astrbot.call_args.kwargs['official_secret'], '')
