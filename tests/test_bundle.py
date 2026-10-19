import os
import unittest
import base64
from importlib.util import find_spec

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.bundle import BUNDLE_FORMAT, create_envelope, payload_hash, sign_payload, verify_envelope
from failovarr.user_accounts import decrypt_user_snapshot, encrypt_user_snapshot


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
        self.assertEqual(payload["format"], 2)
        self.assertEqual(BUNDLE_FORMAT, 3)
        self.assertEqual(payload["domains"]["output_profiles"][0]["id"], 3)

    def test_created_at_must_be_a_timezone_aware_iso_timestamp(self):
        for created_at in ("not-a-time", "2026-08-13T12:00:00"):
            with self.subTest(created_at=created_at):
                envelope = create_envelope(
                    cluster_id="home", source_node="main", sequence=7,
                    domains={}, secret=self.secret, created_at=created_at,
                )
                with self.assertRaisesRegex(ValueError, "created_at"):
                    verify_envelope(envelope, self.secret, "home")

        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={}, secret=self.secret,
        )
        envelope["payload"]["created_at"] = ""
        envelope["payload_sha256"] = payload_hash(envelope["payload"])
        envelope["signature"] = sign_payload(envelope["payload"], self.secret)
        with self.assertRaisesRegex(ValueError, "created_at"):
            verify_envelope(envelope, self.secret, "home")

    def test_signed_legacy_format_remains_verifiable(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={}, secret=self.secret,
        )
        envelope["payload"]["format"] = 1
        envelope["payload_sha256"] = payload_hash(envelope["payload"])
        envelope["signature"] = sign_payload(envelope["payload"], self.secret)

        self.assertEqual(verify_envelope(envelope, self.secret, "home")["format"], 1)

    def test_unknown_bundle_format_is_rejected_even_when_signed(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={}, secret=self.secret,
        )
        envelope["payload"]["format"] = 4
        envelope["payload_sha256"] = payload_hash(envelope["payload"])
        envelope["signature"] = sign_payload(envelope["payload"], self.secret)

        with self.assertRaisesRegex(ValueError, "Unsupported bundle format: 4"):
            verify_envelope(envelope, self.secret, "home")

    def test_format_three_requires_encrypted_users(self):
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={}, secret=self.secret,
        )
        envelope["payload"]["format"] = 3
        envelope["payload_sha256"] = payload_hash(envelope["payload"])
        envelope["signature"] = sign_payload(envelope["payload"], self.secret)
        with self.assertRaisesRegex(ValueError, "requires a valid encrypted user section"):
            verify_envelope(envelope, self.secret, "home")

    @unittest.skipUnless(find_spec("cryptography"), "Dispatcharr supplies cryptography at runtime")
    def test_encrypted_user_snapshot_round_trip_and_authentication(self):
        context = {
            "cluster_id": "home", "source_node": "main", "sequence": 7,
            "created_at": "2026-08-13T12:00:00+00:00",
        }
        snapshot = {"format": 1, "fields": [], "users": []}
        encrypted = encrypt_user_snapshot(snapshot, self.secret, context)
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=7,
            domains={}, secret=self.secret, created_at=context["created_at"],
            encrypted_users=encrypted,
        )
        payload = verify_envelope(envelope, self.secret, "home")
        self.assertEqual(payload["format"], 3)
        self.assertEqual(decrypt_user_snapshot(encrypted, self.secret, context), snapshot)
        with self.assertRaisesRegex(ValueError, "authentication"):
            decrypt_user_snapshot(encrypted, "wrong-secret", context)

        altered = dict(encrypted)
        ciphertext = bytearray(base64.b64decode(altered["ciphertext"]))
        ciphertext[0] ^= 1
        altered["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        with self.assertRaisesRegex(ValueError, "authentication"):
            decrypt_user_snapshot(altered, self.secret, context)

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
