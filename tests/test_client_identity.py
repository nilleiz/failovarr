import os
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.client_identity import build_client_identity, compare_client_identity


class ClientIdentityTests(unittest.TestCase):
    secret = "a-development-secret-long-enough"

    def test_manifest_never_contains_credentials(self):
        manifest = build_client_identity([{
            "username": "viewer",
            "api_key": "api-secret-value",
            "xc_password": "xc-secret-value",
            "output_profile": 3,
        }], self.secret)
        serialized = str(manifest)
        self.assertNotIn("api-secret-value", serialized)
        self.assertNotIn("xc-secret-value", serialized)
        self.assertTrue(manifest["users"][0]["has_api_key"])
        self.assertTrue(manifest["users"][0]["has_xc_password"])

    def test_order_does_not_change_manifest(self):
        first = build_client_identity([
            {"username": "b", "api_key": "2"},
            {"username": "a", "api_key": "1"},
        ], self.secret)
        second = build_client_identity([
            {"username": "a", "api_key": "1"},
            {"username": "b", "api_key": "2"},
        ], self.secret)
        self.assertEqual(first, second)

    def test_changed_xc_password_is_reported_without_value(self):
        expected = build_client_identity([
            {"username": "viewer", "xc_password": "old"},
        ], self.secret)
        actual = build_client_identity([
            {"username": "viewer", "xc_password": "new"},
        ], self.secret)
        result = compare_client_identity(expected, actual)
        self.assertEqual(result["different"], ["viewer"])
        self.assertNotIn("old", str(result))
        self.assertNotIn("new", str(result))

    def test_missing_and_unexpected_users_are_reported(self):
        expected = build_client_identity([
            {"username": "expected", "api_key": "one"},
        ], self.secret)
        actual = build_client_identity([
            {"username": "other", "api_key": "two"},
        ], self.secret)
        result = compare_client_identity(expected, actual)
        self.assertEqual(result["missing"], ["expected"])
        self.assertEqual(result["unexpected"], ["other"])
