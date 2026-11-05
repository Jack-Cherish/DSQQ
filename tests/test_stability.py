import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import diagnostics
import main
import server
import installer
from store import State

SOURCE = Path(__file__).resolve().parents[1] / 'vendor/dafeiyu-config-pack/tools/import_dafeiyu.py'
spec = importlib.util.spec_from_file_location('binding_tool', SOURCE)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class StabilityTests(unittest.TestCase):
    def test_background_tab_does_not_kill_controller_after_twenty_seconds(self):
        self.assertFalse(main.should_idle_exit([], 65))
        self.assertTrue(main.should_idle_exit([], 25, closed=True))

    def test_partial_extraction_uses_complete_boot_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, boot = Path(tmp) / 'install', Path(tmp) / 'boot'
            (root / 'pack').mkdir(parents=True)
            (boot / 'pack/settings').mkdir(parents=True)
            (boot / 'pack/settings/pack.json').write_text('{}')
            app = server.App('install', boot=boot)
            app.root = root
            self.assertEqual(app.pack_dir(), boot / 'pack')

    def test_qq_auth_error_is_actionable_and_previous_run_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'logs').mkdir()
            log = root / 'logs/astrbot.log'
            error = "[ERRO] Task platform_qq_official_dafeiyu failed: {'code': 100016, 'message': 'invalid appid or secret'}\n"
            log.write_text(error, encoding='utf8')
            issue = diagnostics.official_error(root)
            self.assertEqual(issue['code'], 'qq_credentials_invalid')
            self.assertIn('AppSecret', issue['message'])
            log.write_text(error + '=== DA FEI YU PROCESS START ===\nAstrBot started.\n', encoding='utf8')
            self.assertIsNone(diagnostics.official_error(root))

    def test_no_ai_request_without_diagnostic_evidence(self):
        opener = mock.Mock()
        text = diagnostics.analyze_with_deepseek('test-key', {'items': []}, opener=opener)
        opener.assert_not_called()
        self.assertIn('证据', text)

    def test_official_readiness_rejects_auth_failure_before_install_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = State.load(root)
            state.data.update(platform='official', pids={'astrbot': 123})
            ctx = mock.Mock(root=root, state=state)
            with mock.patch('official_binding.connection_state', return_value='starting'), \
                 mock.patch.object(diagnostics, 'official_error', return_value={'code': 'qq_credentials_invalid', 'message': '检查 AppSecret'}):
                with self.assertRaisesRegex(installer.InstallError, 'AppSecret'):
                    installer.wait_official(ctx, timeout=0)

    def test_binding_only_updates_owner_without_install_or_restart(self):
        owner = 'a' * 32
        cfg = {'platform': [{'id': 'dafeiyu', 'type': 'qq_official', 'secret': 'keep-secret'}],
               'platform_settings': {'enable_id_white_list': True,
                  'id_whitelist': ['other', 'dafeiyu:FriendMessage:DAFEIYU_UNBOUND_OWNER']}, 'admins_id': []}
        prompt = {'persona_id': 'fish', 'system_prompt': 'owner=DAFEIYU_UNBOUND_OWNER; keep my text'}
        plugin = {'target': 'dafeiyu:FriendMessage:DAFEIYU_UNBOUND_OWNER', 'custom': 42}
        calls = []
        api = mock.Mock()
        api.ok.side_effect = tool.Api.ok
        def call(method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if method == 'GET':
                data = {'config': cfg} if path == '/config-profiles/default' else prompt if path.startswith('/personas/') else {'config': plugin}
                return {'data': data}
            return {'status': 'ok'}
        api.call.side_effect = call
        pack = {'persona': {'id': 'fish'}, 'plugins': [{'name': 'fish_plugin', 'config': {'target': '{主人会话}'}}]}
        tool.bind_owner(api, pack, owner)
        put = next(body for method, path, body in calls if method == 'PUT' and path == '/config-profiles/default')
        self.assertEqual(put['platform'][0]['secret'], 'keep-secret')
        self.assertEqual(put['platform_settings']['id_whitelist'], ['other', 'dafeiyu:FriendMessage:' + owner])
        self.assertEqual(put['admins_id'], [owner])
        self.assertNotIn('DAFEIYU_UNBOUND_OWNER', str([b for m, p, b in calls if m == 'PUT']))
        self.assertFalse(any('install' in p or 'community' in p for m, p, b in calls))
        self.assertEqual(calls[-1][1], '/config-profiles/default')

    def test_binding_failure_does_not_open_chat_whitelist(self):
        api = mock.Mock()
        api.ok.side_effect = tool.Api.ok
        api.call.return_value = {'status': 'error', 'message': 'save failed'}
        with self.assertRaises(RuntimeError):
            tool.bind_owner(api, {'persona': {'id': 'fish'}, 'plugins': []}, 'a' * 32)
        self.assertFalse(any(c.args[0] == 'PUT' and c.args[1] == '/config-profiles/default' for c in api.call.call_args_list))

    def test_connection_recovery_clears_old_error_in_same_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'logs').mkdir()
            (root / 'logs/astrbot.log').write_text('invalid appid or secret\n[INFO] [DaFeiYu connection] status=ready\n', encoding='utf8')
            self.assertIsNone(diagnostics.official_error(root))

    def test_authentication_heartbeat_restores_window_after_reload(self):
        app = server.App('install', boot=Path('.'))
        app.handle_window_closed({})
        self.assertTrue(app.window_closed)
        app.handle_ping({})
        self.assertFalse(app.window_closed)


    def test_cancelled_import_terminates_only_its_own_child(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(SOURCE.parent.parent, root / 'pack')
            state = State.load(root)
            state.data.update(ports={'dashboard': 1, 'napcat_webui': 2, 'onebot': 3}, owner_qq='123', voice='0', memory='0')
            state.set_secret('dashboard_password', 'test')
            inst = installer.Installer(root, state, steps=[])
            inst.provide('profile', {'name':'鱼', 'persona':'keep', 'voice':'甜', 'memes':{'blue-fat-fish-meme-pack':True}})
            ctx = installer.Ctx(inst)
            child = mock.Mock(pid=987654)
            child.wait.return_value = 0
            def spawn(*args):
                inst._cancelled.set()
                return child
            with mock.patch.object(installer, 'ensure_astrbot'), \
                 mock.patch.object(installer.services, 'logged_process', side_effect=spawn), \
                 mock.patch.object(installer.subprocess, 'run') as kill:
                with self.assertRaises(installer.Cancelled):
                    installer.step_import(ctx)
            self.assertEqual(kill.call_args.args[0], ['taskkill', '/PID', '987654', '/T', '/F'])
            self.assertFalse((root / '.tmp/import/answers.json').exists())
