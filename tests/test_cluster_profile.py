import unittest

from failovarr.cluster_profile import (
    export_cluster_profile,
    import_cluster_profile,
)


class ClusterProfileTests(unittest.TestCase):
    def setUp(self):
        self.main = {
            "node_id": "main",
            "role": "leader",
            "cluster_id": "home",
            "mode": "shared_storage",
            "storage_backend": "sftp",
            "storage_endpoint": "sftp://storage:22",
            "storage_container": "bundles",
            "storage_username": "replication",
            "storage_password": "storage-password",
            "shared_secret": "a" * 32,
            "replication_scope": "full",
            "domains": "output_profiles,channels",
            "core_setting_keys": "stream_settings,proxy_settings",
            "protected_output_profile_ids": "3",
            "local_overrides": '{"output_profiles":{"3":{"command":"ffmpeg"}}}',
            "peer_url": "http://slave:9192",
            "state_path": "/data/main-state",
            "automatic_apply": False,
            "allow_deletes": True,
        }

    def test_export_excludes_node_local_fields_and_password_by_default(self):
        exported = export_cluster_profile(self.main)["settings"]
        self.assertEqual(exported["cluster_id"], "home")
        self.assertEqual(exported["shared_secret"], "a" * 32)
        for field in (
            "storage_password", "node_id", "role", "peer_url",
            "protected_output_profile_ids", "local_overrides", "replication_scope",
            "domains", "core_setting_keys",
            "allow_deletes",
        ):
            self.assertNotIn(field, exported)

    def test_profile_copies_the_single_redundancy_mode(self):
        profile = export_cluster_profile({**self.main, "redundancy_mode": "cold_standby"})
        self.assertEqual(profile["settings"]["redundancy_mode"], "cold_standby")

    def test_password_export_is_explicit(self):
        profile = export_cluster_profile(self.main, include_storage_passwords=True)
        self.assertTrue(profile["includes_storage_passwords"])
        self.assertEqual(profile["settings"]["storage_password"], "storage-password")

    def test_import_preserves_follower_local_settings(self):
        follower = {
            "node_id": "slave",
            "role": "follower",
            "peer_url": "http://main:9192",
            "protected_output_profile_ids": "3",
            "local_overrides": '{"output_profiles":{"3":{"parameters":"qsv"}}}',
            "storage_password": "local-password",
            "shared_path": "/data/follower-mounted-storage",
            "sftp_known_hosts_path": "/data/follower-secrets/known_hosts",
            "state_path": "/data/slave-state",
        }
        imported = import_cluster_profile(follower, export_cluster_profile(self.main))
        self.assertEqual(imported["node_id"], "slave")
        self.assertEqual(imported["role"], "follower")
        self.assertEqual(imported["peer_url"], "http://main:9192")
        self.assertEqual(imported["protected_output_profile_ids"], "3")
        self.assertIn("qsv", imported["local_overrides"])
        self.assertEqual(imported["storage_password"], "local-password")
        self.assertEqual(imported["shared_path"], "/data/follower-mounted-storage")
        self.assertEqual(
            imported["sftp_known_hosts_path"], "/data/follower-secrets/known_hosts",
        )
        self.assertEqual(imported["cluster_id"], "home")

    def test_profile_keeps_follower_local_scope_and_deletion_policy(self):
        follower = {
            "role": "follower", "replication_scope": "custom",
            "domains": "output_profiles", "core_setting_keys": "stream_settings",
            "allow_deletes": False, "auto_start": False,
        }
        imported = import_cluster_profile(follower, export_cluster_profile(self.main))
        self.assertEqual(imported["replication_scope"], "custom")
        self.assertEqual(imported["domains"], "output_profiles")
        self.assertFalse(imported["allow_deletes"])
        self.assertFalse(imported["auto_start"])

    def test_legacy_profile_deletion_policy_is_ignored(self):
        profile = export_cluster_profile(self.main)
        profile["settings"]["allow_deletes"] = True
        imported = import_cluster_profile({"allow_deletes": False}, profile)
        self.assertFalse(imported["allow_deletes"])

    def test_imported_password_replaces_follower_password(self):
        follower = {"role": "follower", "storage_password": "old"}
        profile = export_cluster_profile(self.main, include_storage_passwords=True)
        imported = import_cluster_profile(follower, profile)
        self.assertEqual(imported["storage_password"], "storage-password")

    def test_unknown_profile_fields_are_rejected(self):
        profile = export_cluster_profile(self.main)
        profile["settings"]["node_id"] = "attacker"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            import_cluster_profile({}, profile)

    def test_legacy_dispatcharr_redundancy_profile_is_accepted(self):
        profile = export_cluster_profile(self.main)
        profile["format"] = "dispatcharr-redundancy-profile"
        imported = import_cluster_profile({"role": "follower"}, profile)
        self.assertEqual(imported["cluster_id"], "home")


if __name__ == "__main__":
    unittest.main()
