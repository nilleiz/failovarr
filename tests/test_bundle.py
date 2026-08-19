import os
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.bundle import create_envelope, verify_envelope


class BundleTests(unittest.TestCase):
    secret = "a-development-secret-long-enough"

    def test_round_trip(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={"output_profiles": [{"id": 3, "name": "HQ"}]},
            secret=self.secret, created_at="2026-08-13T12:00:00+00:00",
        )
        payload = verify_envelope(envelope, self.secret, "home")
        self.assertEqual(payload["sequence"], 7)
        self.assertEqual(payload["domains"]["output_profiles"][0]["id"], 3)

    def test_client_identity_is_signed_but_optional(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=8,
            domains={}, client_identity={"format": 1, "users": []},
            secret=self.secret,
        )
        payload = verify_envelope(envelope, self.secret, "home")
        self.assertEqual(payload["client_identity"], {"format": 1, "users": []})

    def test_handoff_metadata_is_signed(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=9,
            domains={}, handoff={"phase": "prepare", "target_node": "slave"},
            secret=self.secret,
        )
        envelope["payload"]["handoff"]["target_node"] = "other"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_envelope(envelope, self.secret, "home")

    def test_main_scope_is_signed(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=10, domains={},
            scope={"domains": ["output_profiles"], "core_setting_keys": []},
            secret=self.secret,
        )
        envelope["payload"]["scope"]["domains"] = ["channels"]
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_envelope(envelope, self.secret, "home")

    def test_tampering_is_rejected(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=1,
            domains={}, secret=self.secret,
        )
        envelope["payload"]["source_node"] = "attacker"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_envelope(envelope, self.secret, "home")

    def test_wrong_cluster_is_rejected(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=1,
            domains={}, secret=self.secret,
        )
        with self.assertRaisesRegex(ValueError, "different cluster"):
            verify_envelope(envelope, self.secret, "other")
