import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import installer
from store import State

class OfficialLoginTests(unittest.TestCase):
    def test_confirmed_owner_uses_binding_only_and_verifies_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = State.load(root)
            state.data.update(platform='official', owner_qq='a' * 32)
            ctx = mock.Mock(root=root, state=state)
            with mock.patch.object(installer.services, 'stop') as stop, \
                 mock.patch.object(installer, 'step_import') as imp, \
                 mock.patch.object(installer, 'wait_official') as ready:
                installer.step_login(ctx)
            stop.assert_not_called()
            imp.assert_called_once_with(ctx, bind_only=True)
            ready.assert_called_once_with(ctx)

if __name__ == '__main__':
    unittest.main()

class OfficialResumeTests(unittest.TestCase):
    def test_resume_binding_restarts_astrbot_before_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            state = State.load(Path(temp))
            state.data.update(platform='official', owner_qq='')
            ctx = mock.Mock(root=Path(temp), state=state)
            ctx.wait_for.side_effect = installer.Cancelled()
            with mock.patch.object(installer, 'ensure_astrbot') as ensure:
                with self.assertRaises(installer.Cancelled):
                    installer.step_login(ctx)
            ensure.assert_called_once_with(ctx)
            ctx.wait_for.assert_called_once_with('official_bound')
