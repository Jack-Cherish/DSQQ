import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import installer
import server
import services
from store import State


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = State.load(self.root)
        self.state.save()
        self.app = server.App("status", self.root, boot=self.root, home=self.root / "home", steps_factory=lambda: [])

    def test_stop_cancels_startup_before_it_can_launch_the_next_service(self):
        entered, release = threading.Event(), threading.Event()
        launched = []
        def wait(ctx):
            entered.set()
            release.wait(3)
        self.app._starter = installer.Installer(self.root, self.app.state, resume=False,
            steps=[installer.Step("a", "waiting", wait), installer.Step("b", "launch", lambda c: launched.append(1))])
        self.app._starter.start()
        self.assertTrue(entered.wait(2))
        with mock.patch.object(services, "stop_all", side_effect=lambda s: release.set()):
            self.app.handle_stop({})
        self.app._starter._thread.join(3)
        self.assertEqual(launched, [])

    def test_quit_failure_keeps_window_open(self):
        with mock.patch.object(services, "stop_all", side_effect=RuntimeError("Access denied")):
            with self.assertRaises(RuntimeError):
                self.app.handle_quit({})
        self.assertFalse(self.app.quitting)

    def test_stop_retries_permission_failure_with_elevation_and_rechecks(self):
        with mock.patch.object(services, 'stop_all', side_effect=[services.StopPermissionError('denied'), None]) as stop, \
             mock.patch.object(services, 'elevated_stop') as elevated:
            self.assertTrue(self.app.handle_stop({})['ok'])
        elevated.assert_called_once_with(self.root)
        self.assertEqual(stop.call_count, 2)

    def test_cancelled_elevation_keeps_window_open_and_reports_failure(self):
        with mock.patch.object(services, 'stop_all', side_effect=services.StopPermissionError('denied')), \
             mock.patch.object(services, 'elevated_stop', side_effect=RuntimeError('未获得管理员授权')):
            with self.assertRaisesRegex(RuntimeError, '授权'):
                self.app.handle_quit({})
        self.assertFalse(self.app.quitting)

    def test_other_stop_failure_does_not_request_elevation(self):
        with mock.patch.object(services, 'stop_all', side_effect=RuntimeError('timeout')), \
             mock.patch.object(services, 'elevated_stop', create=True) as elevated:
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.app.handle_stop({})
        elevated.assert_not_called()

    def test_elevated_stop_is_not_reported_successful_without_rechecking(self):
        with mock.patch.object(services, 'stop_all', side_effect=[services.StopPermissionError('denied'), RuntimeError('still running')]), \
             mock.patch.object(services, 'elevated_stop'):
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                self.app.handle_quit({})
        self.assertFalse(self.app.quitting)

    def test_stopped_service_cannot_be_started_by_stale_installation(self):
        services.stop_all(self.state)
        with mock.patch.object(services.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "停止"):
                services.start_napcat(services.Layout(self.root), State.load(self.root), None)
        spawn.assert_not_called()

    def test_old_window_can_explicitly_start_after_another_window_stops(self):
        services.allow_start(self.state)
        stale = State.load(self.root)
        services.stop_all(self.state)
        services.allow_start(stale)
        self.assertFalse(State.load(self.root).data["services_stopped"])

