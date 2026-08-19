import os
import unittest
import urllib.request

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.transport import BundleHttpServer, fetch_latest, fetch_status


class TransportTests(unittest.TestCase):
    secret = "a-development-secret-long-enough"

    def test_authenticated_fetch(self):
        server = BundleHttpServer(
            "127.0.0.1", 0, lambda: {"payload": {"sequence": 1}},
            "a-development-secret-long-enough",
        )
        server.start()
        try:
            port = server._server.server_address[1]
            result = fetch_latest(
                f"http://127.0.0.1:{port}",
                "a-development-secret-long-enough",
            )
            self.assertEqual(result["payload"]["sequence"], 1)
        finally:
            server.stop()

    def test_readiness_status_is_public_but_contains_no_details(self):
        server = BundleHttpServer("127.0.0.1", 0, lambda: None, self.secret, lambda: False)
        server.start()
        try:
            port = server._server.server_address[1]
            with self.assertRaises(Exception) as context:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/readiness")
            self.assertEqual(context.exception.code, 503)
        finally:
            server.stop()

    def test_authenticated_peer_status(self):
        server = BundleHttpServer("127.0.0.1", 0, lambda: None, self.secret)
        server.status_provider = lambda: {
            "node_id": "slave", "applied_sequence": 9, "applied_hash": "abc",
        }
        server.start()
        try:
            port = server._server.server_address[1]
            result = fetch_status(f"http://127.0.0.1:{port}", self.secret)
            self.assertEqual(result["node_id"], "slave")
            self.assertEqual(result["applied_sequence"], 9)
        finally:
            server.stop()
