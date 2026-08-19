import os
import unittest
from unittest.mock import patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.autostart import (
    LEADER_KEY,
    is_dispatcharr_web_process,
    refresh_service_lease,
    release_service_lease,
    wait_for_service_stop,
)


class FakeRedis:
    def __init__(self, owner):
        self.values = {LEADER_KEY: owner}

    def eval(self, script, _key_count, key, token, *args):
        if self.values.get(key) != token:
            return 0
        if "expire" in script:
            return 1
        if "del" in script:
            del self.values[key]
            return 1
        return 0


class ServiceLeaseTests(unittest.TestCase):
    def test_only_uwsgi_processes_are_autostart_eligible(self):
        with patch("builtins.open", side_effect=OSError):
            with patch("failovarr.autostart.sys.argv", ["uwsgi", "--ini", "app.ini"]):
                self.assertTrue(is_dispatcharr_web_process())
            with patch("failovarr.autostart.sys.argv", ["celery", "-A", "dispatcharr"]):
                self.assertFalse(is_dispatcharr_web_process())

    def test_only_owner_can_refresh_or_release(self):
        client = FakeRedis("owner")
        self.assertFalse(refresh_service_lease(client, "other"))
        release_service_lease(client, "other")
        self.assertIn(LEADER_KEY, client.values)
        self.assertTrue(refresh_service_lease(client, "owner"))
        release_service_lease(client, "owner")
        self.assertNotIn(LEADER_KEY, client.values)

    def test_wait_for_service_stop_observes_cross_worker_release(self):
        with patch(
            "failovarr.autostart.service_is_running",
            side_effect=[True, True, False],
        ), patch("failovarr.autostart.time.sleep"):
            self.assertTrue(wait_for_service_stop(timeout_seconds=1, poll_seconds=0.01))

    def test_wait_for_service_stop_is_bounded(self):
        with patch("failovarr.autostart.service_is_running", return_value=True), patch(
            "failovarr.autostart.time.monotonic", side_effect=[0, 0, 2, 2],
        ), patch("failovarr.autostart.time.sleep"):
            self.assertFalse(wait_for_service_stop(timeout_seconds=1, poll_seconds=0.01))
