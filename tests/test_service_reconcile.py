import os
import inspect
import threading
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

import failovarr as plugin
from failovarr.config import ConfigValidationError
from failovarr.engine import BackgroundService


class ServiceReconcileTests(unittest.TestCase):
    def test_running_service_is_stopped_before_new_owner_starts(self):
        service = Mock()
        service.status.return_value = {"running": True, "last_error": None}
        with patch.object(plugin, "effective_settings", side_effect=lambda value: value), patch.object(
            plugin, "_stop_service", return_value=True,
        ) as stop, patch.object(
            plugin, "wait_for_service_stop", return_value=True,
        ) as wait, patch.object(plugin, "_start_service", return_value=service) as start:
            result = plugin.reconcile_service({"auto_start": True})
        stop.assert_called_once()
        wait.assert_called_once()
        start.assert_called_once()
        self.assertTrue(result["running"])

    def test_timeout_explains_saved_config_and_skipped_export(self):
        with patch.object(plugin, "effective_settings", side_effect=lambda value: value), patch.object(
            plugin, "_stop_service", return_value=True,
        ), patch.object(plugin, "wait_for_service_stop", return_value=False):
            with self.assertRaises(ConfigValidationError) as raised:
                plugin.reconcile_service({"auto_start": True})
        self.assertEqual(raised.exception.code, "service_reload_timeout")
        self.assertIn("Configuration was saved", str(raised.exception))
        self.assertIn("export was not run", str(raised.exception))

    def test_setup_listener_is_accepted_only_after_health_response(self):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        with patch.object(plugin, "urlopen", return_value=response):
            self.assertTrue(plugin._setup_is_healthy())
        with patch.object(plugin, "urlopen", side_effect=TimeoutError):
            self.assertFalse(plugin._setup_is_healthy())

    def test_setup_helper_health_confirmation_is_debug_only(self):
        source = inspect.getsource(plugin._start_setup)
        self.assertIn('action_logger.debug("Setup assistant helper is healthy")', source)
        self.assertNotIn('action_logger.info("Setup assistant helper is healthy")', source)

    def test_graceful_flush_is_idempotent_across_stop_signals(self):
        engine = Mock()
        engine.export_on_shutdown.return_value = {"status": "exported", "sequence": 4}
        service = BackgroundService(engine)
        self.assertEqual(service.flush_on_shutdown()["sequence"], 4)
        self.assertEqual(service.flush_on_shutdown()["sequence"], 4)
        engine.export_on_shutdown.assert_called_once()

    def test_cold_standby_follower_keeps_lease_without_periodic_preview(self):
        class OneCycleStop:
            calls = 0

            def is_set(self):
                return self.calls > 0

            def wait(self, _seconds):
                self.calls += 1
                return True

        engine = Mock()
        engine.config.deployment_mode = "cold_standby"
        engine.config.interval_seconds = 10
        engine.is_authoritative.return_value = False
        service = BackgroundService(engine)
        service._stop = OneCycleStop()
        service._run()
        engine.preview_latest.assert_not_called()
        engine.apply_latest.assert_not_called()
        self.assertEqual(service.last_result["status"], "waiting")

    def test_lease_refresh_starts_before_a_blocking_cold_start_import(self):
        entered_import = threading.Event()
        release_import = threading.Event()
        engine = Mock()
        engine.config.mode = "shared_storage"
        engine.config.client_access_mode = "disabled"
        engine.config.deployment_mode = "cold_standby"
        engine.config.import_on_start = True
        engine.config.interval_seconds = 60
        engine.is_authoritative.return_value = False
        engine.recover_cold_standby.return_value = None

        def blocking_import():
            entered_import.set()
            self.assertTrue(release_import.wait(timeout=2))
            return {"status": "applied"}

        engine.apply_latest.side_effect = blocking_import
        redis_client = Mock()
        redis_client.get.return_value = None
        service = BackgroundService(engine, redis_client=redis_client, lease_token="test-lease")
        starter = threading.Thread(target=service.start, daemon=True)
        with patch("failovarr.engine.refresh_service_lease", return_value=True):
            starter.start()
            self.assertTrue(entered_import.wait(timeout=1))
            self.assertIsNotNone(service._lease_thread)
            self.assertTrue(service._lease_thread.is_alive())
            release_import.set()
            starter.join(timeout=2)
            self.assertFalse(starter.is_alive())
            service.stop()

    def test_repeated_background_failure_is_logged_once_until_recovery(self):
        engine = Mock()
        service = BackgroundService(engine)
        service._record_background_error("Replication cycle", RuntimeError("first"))
        service._record_background_error("Replication cycle", RuntimeError("second"))
        self.assertEqual(engine.logger.error.call_count, 1)
        service._record_success()
        service._record_background_error("Replication cycle", RuntimeError("third"))
        self.assertEqual(engine.logger.error.call_count, 2)


if __name__ == "__main__":
    unittest.main()
