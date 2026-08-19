import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.node_config import (
    effective_settings, load_node_config, native_settings_snapshot, save_node_config,
)
from failovarr.config import IPTV_CONTENT_DOMAINS
from failovarr.engine import BundleNotNewerState, ReplicationEngine
from failovarr.setup_assistant import SetupServer, _merge_secret_fields, _public_settings
from failovarr.cluster_profile import export_cluster_profile


class NodeConfigTests(unittest.TestCase):
    def test_round_trip_and_public_url_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({"node_id": "main", "setup_public_url": "http://old:9192"})
                self.assertEqual(load_node_config()["node_id"], "main")
                effective = effective_settings({"setup_public_url": "http://new:9192"})
                self.assertEqual(effective["node_id"], "main")
                self.assertEqual(effective["setup_public_url"], "http://new:9192")

    def test_missing_failovarr_file_migrates_legacy_node_file_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "failovarr.json"
            legacy = Path(directory) / "dispatcharr-redundancy.json"
            legacy.write_text('{"node_id":"migrated","state_path":"/data/legacy-state"}\n', encoding="utf-8")
            with patch("failovarr.node_config.CONFIG_PATH", current), patch(
                "failovarr.node_config.LEGACY_CONFIG_PATH", legacy,
            ):
                migrated = load_node_config()
            self.assertEqual(migrated["node_id"], "migrated")
            self.assertEqual(migrated["state_path"], "/data/legacy-state")
            self.assertTrue(current.exists())
            self.assertTrue(legacy.exists())

    def test_legacy_migration_copies_legacy_file_owner_to_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "failovarr.json"
            legacy = Path(directory) / "dispatcharr-redundancy.json"
            legacy.write_text('{"node_id":"migrated"}\n', encoding="utf-8")
            expected = legacy.stat()
            with patch("failovarr.node_config.CONFIG_PATH", current), patch(
                "failovarr.node_config.LEGACY_CONFIG_PATH", legacy,
            ), patch("failovarr.node_config.os.fchown") as chown:
                load_node_config()
            self.assertEqual(chown.call_count, 1)
            self.assertEqual(chown.call_args.args[1:], (expected.st_uid, expected.st_gid))

    def test_existing_failovarr_owner_is_preserved_on_atomic_save(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "failovarr.json"
            current.write_text('{"node_id":"existing"}\n', encoding="utf-8")
            expected = current.stat()
            with patch("failovarr.node_config.CONFIG_PATH", current), patch(
                "failovarr.node_config.os.fchown",
            ) as chown:
                save_node_config({"node_id": "updated"})
            self.assertEqual(chown.call_count, 1)
            self.assertEqual(chown.call_args.args[1:], (expected.st_uid, expected.st_gid))

    def test_empty_secret_input_preserves_existing_values(self):
        merged = _merge_secret_fields(
            {"shared_secret": "", "setup_access_token": ""},
            {"shared_secret": "existing-secret", "setup_access_token": "existing-token"},
        )
        self.assertEqual(merged["shared_secret"], "existing-secret")
        self.assertEqual(merged["setup_access_token"], "existing-token")

    def test_native_settings_overlay_never_replaces_node_local_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({
                    "node_id": "main", "cluster_id": "home", "shared_secret": "x" * 32,
                    "role": "follower", "domains": "output_profiles", "protected_records": {"output_profiles": [3]},
                })
                effective = effective_settings({
                    "node_id": "main-new", "replicate_output_profiles": False,
                    "protect_output_profiles_3": False,
                })
        self.assertEqual(effective["node_id"], "main-new")
        self.assertEqual(effective["shared_secret"], "x" * 32)
        self.assertNotIn("output_profiles", effective["domains"])
        self.assertEqual(effective["protected_records"], {})

    def test_native_snapshot_excludes_secrets_and_retains_protection(self):
        snapshot = native_settings_snapshot({
            "node_id": "slave", "cluster_id": "home", "shared_secret": "do-not-export",
            "domains": "output_profiles", "protected_records": {"output_profiles": [3]},
        })
        self.assertNotIn("shared_secret", snapshot)
        self.assertTrue(snapshot["replicate_output_profiles"])
        self.assertTrue(snapshot["protect_output_profiles_3"])

    def test_native_snapshot_retains_all_new_record_policies(self):
        policies = {
            "new_output_profile_policy": "source",
            "new_stream_profile_policy": "block",
            "new_epg_source_policy": "disabled",
            "new_m3u_account_policy": "source",
        }
        snapshot = native_settings_snapshot(policies)
        self.assertEqual({key: snapshot[key] for key in policies}, policies)

    def test_native_follower_scope_controls_are_applied_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({"role": "follower", "domains": "output_profiles"})
                effective = effective_settings({
                    "role": "follower", "replication_scope": "custom",
                    "replicate_output_profiles": False, "replicate_stream_profiles": True,
                })
        self.assertEqual(effective["replication_scope"], "custom")
        self.assertIn("stream_profiles", effective["domains"])
        self.assertNotIn("output_profiles", effective["domains"])

    def test_native_leader_scope_controls_do_not_change_main_export_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({"role": "leader", "domains": "output_profiles"})
                effective = effective_settings({
                    "role": "leader", "replication_scope": "basic",
                    "replicate_output_profiles": False,
                })
        self.assertEqual(effective["domains"], "output_profiles")

    def test_content_only_native_preset_persists_its_checkbox_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({"role": "follower", "domains": "output_profiles", "core_setting_keys": "stream_settings"})
                effective = effective_settings({
                    "role": "follower", "replication_scope": "iptv_content",
                    "replicate_output_profiles": True, "replicate_core_stream_settings": True,
                })
        self.assertEqual(effective["domains"], ",".join(IPTV_CONTENT_DOMAINS))
        self.assertEqual(effective["core_setting_keys"], "")

    def test_native_overlay_preserves_legacy_output_profile_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({
                    "role": "follower", "protected_output_profile_ids": "303",
                })
                effective = effective_settings({"role": "follower"})
        self.assertEqual(effective["protected_records"], {"output_profiles": [303]})
        self.assertEqual(effective["protected_output_profile_ids"], "303")

    def test_native_overlay_preserves_hidden_protection_when_scope_excludes_its_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({
                    "role": "follower", "domains": "user_agents,stream_profiles",
                    "protected_records": {
                        "stream_profiles": [7], "output_profiles": [303],
                    },
                })
                effective = effective_settings({
                    "role": "follower", "protect_stream_profiles_7": False,
                })
        self.assertEqual(effective["protected_records"], {"output_profiles": [303]})
        self.assertEqual(effective["protected_output_profile_ids"], "303")

    def test_legacy_sftp_default_migrates_to_node_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({
                    "sftp_known_hosts_path": "/data/redundancy-secrets/known_hosts",
                })
                effective = effective_settings({})
        self.assertEqual(
            effective["sftp_known_hosts_path"],
            "/data/failovarr-state/known_hosts",
        )

    def test_public_bootstrap_redacts_all_secrets(self):
        public = _public_settings({
            "shared_secret": "secret", "storage_password": "password",
            "s3_session_token": "session", "setup_access_token": "setup-token",
            "sftp_private_key": "private-key", "sftp_private_key_passphrase": "passphrase",
        })
        for field in (
            "shared_secret", "storage_password", "s3_session_token", "setup_access_token",
            "sftp_private_key", "sftp_private_key_passphrase",
        ):
            self.assertEqual(public[field], "")
            self.assertTrue(public[f"{field}_is_set"])

    def test_setup_server_uses_latest_persisted_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            original_token = "a" * 32
            rotated_token = "b" * 32
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({"setup_access_token": original_token})
                server = SetupServer({}, logging.getLogger("test"), token=original_token)
                self.assertEqual(server.current_token(), original_token)
                save_node_config({"setup_access_token": rotated_token})
                self.assertEqual(server.current_token(), rotated_token)
                self.assertTrue(server.url().endswith(rotated_token))
                self.assertEqual(
                    server.url("http://dispatcharr:9192"),
                    f"http://dispatcharr:9192/setup?token={rotated_token}",
                )

    def test_setup_server_prefers_node_local_settings_without_importing_orm(self):
        server = SetupServer({}, logging.getLogger("test"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config({
                    "node_id": "node-file", "role": "follower",
                    "shared_secret": "node-local-secret",
                })
                configured = server._database_settings()
        self.assertEqual(configured["node_id"], "node-file")
        self.assertEqual(configured["shared_secret"], "node-local-secret")

    def test_setup_server_uses_startup_snapshot_before_node_config_exists(self):
        server = SetupServer({"node_id": "startup-snapshot", "role": "leader"}, logging.getLogger("test"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-node.json"
            with patch("failovarr.node_config.CONFIG_PATH", path):
                configured = server._database_settings()
        self.assertEqual(configured["node_id"], "startup-snapshot")

    def test_native_settings_sync_uses_isolated_child_without_arguments(self):
        server = SetupServer({}, logging.getLogger("test"))
        completed = SimpleNamespace(returncode=0)
        with patch("failovarr.setup_assistant.subprocess.run", return_value=completed) as run:
            self.assertTrue(server._sync_native_settings())
        args, kwargs = run.call_args
        self.assertEqual(args[0], [
            sys.executable, "-m", "failovarr.native_settings_sync",
        ])
        self.assertEqual(kwargs["timeout"], 10)
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("shared_secret", repr(args))
        self.assertNotIn("storage_password", repr(args))

    def test_save_warns_but_reconciles_when_native_sync_fails(self):
        settings = {
            "node_id": "main", "cluster_id": "home", "role": "leader",
            "mode": "shared_storage", "storage_backend": "filesystem",
            "shared_path": "/data/redundancy", "shared_secret": "test-secret",
            "domains": "output_profiles", "core_setting_keys": "stream_settings",
        }
        server = SetupServer(settings, logging.getLogger("test"))
        persisted = {}
        with patch.object(server, "_database_settings", return_value=settings), patch(
            "failovarr.setup_assistant.save_node_config",
            side_effect=lambda value: persisted.update(value),
        ), patch.object(server, "_sync_native_settings", return_value=False), patch(
            "failovarr.reconcile_service", return_value={"running": True},
        ) as reconcile:
            result = server.save(settings)
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["native_settings_synced"])
        self.assertTrue(result["warnings"])
        self.assertEqual(persisted["node_id"], "main")
        reconcile.assert_called_once()

    def test_sftp_host_key_can_be_trusted_before_cluster_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            known_hosts = Path(directory) / "known_hosts"
            server = SetupServer({}, logging.getLogger("test"))
            probe = SimpleNamespace(
                storage_backend="sftp", storage_endpoint="sftp://storage.invalid:2222",
                storage_options={"known_hosts_path": str(known_hosts)},
            )
            with patch.object(server, "_database_settings", return_value={}), patch(
                "failovarr.setup_assistant.storage_probe_config", return_value=probe,
            ):
                result = server.trust_sftp_host_key({
                    "storage_backend": "sftp",
                    "storage_endpoint": "sftp://storage.invalid:2222",
                    "storage_container": "bundles",
                    "sftp_known_hosts_path": str(known_hosts),
                    "host_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyHostKey",
                })
            self.assertEqual(result["status"], "success")
            self.assertIn("[storage.invalid]:2222 ssh-ed25519", known_hosts.read_text(encoding="utf-8"))

    def test_sftp_trust_reports_an_actionable_unwritable_directory(self):
        server = SetupServer({}, logging.getLogger("test"))
        probe = SimpleNamespace(
            storage_backend="sftp", storage_endpoint="sftp://storage.invalid",
            storage_options={"known_hosts_path": "/data/not-writable/known_hosts"},
        )
        with patch.object(server, "_database_settings", return_value={}), patch(
            "failovarr.setup_assistant.storage_probe_config", return_value=probe,
        ), patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(Exception, "directory is not writable"):
                server.trust_sftp_host_key({
                    "storage_backend": "sftp", "storage_endpoint": "sftp://storage.invalid",
                    "host_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyHostKey",
                })

    def test_profile_import_accepts_unsaved_follower_identity(self):
        profile = export_cluster_profile({
            "role": "leader", "node_id": "main", "cluster_id": "home", "mode": "shared_storage",
            "storage_backend": "filesystem", "shared_path": "/data/redundancy",
            "shared_secret": "short", "domains": "output_profiles",
        })
        server = SetupServer({}, logging.getLogger("test"))
        saved = {}
        with patch.object(server, "_database_settings", return_value={}), patch(
            "failovarr.setup_assistant.save_node_config", side_effect=lambda value: saved.update(value),
        ), patch.object(server, "_sync_native_settings", return_value=False), patch(
            "failovarr.reconcile_service", return_value={"running": False},
        ):
            result = server.import_profile({
                "profile": profile,
                "local_settings": {"node_id": "slave", "role": "follower", "shared_path": "/data/redundancy"},
            })
        self.assertEqual(result["status"], "success")
        self.assertEqual(saved["node_id"], "slave")
        self.assertEqual(saved["shared_secret"], "short")
        self.assertTrue(saved["core_setting_keys"])
        self.assertFalse(result["native_settings_synced"])
        self.assertTrue(result["warnings"])

    def test_new_disabled_output_profiles_become_explicitly_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            settings = {
                "node_id": "slave",
                "cluster_id": "home",
                "role": "follower",
                "mode": "shared_storage",
                "storage_backend": "filesystem",
                "shared_path": "/data/redundancy",
                "state_path": "/data/redundancy-state",
                "shared_secret": "x" * 32,
                "domains": "output_profiles",
                "protected_output_profile_ids": "3",
            }
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config(settings)
                engine = ReplicationEngine(settings)
                result = {"new_disabled_output_profile_ids": [5]}
                engine._remember_disabled_output_profiles(result)
                self.assertEqual(load_node_config()["protected_output_profile_ids"], "3,5")
                self.assertEqual(result["new_output_profiles_kept_local"], [5])
                self.assertNotIn("new_disabled_output_profile_ids", result)

    def test_new_disabled_records_are_protected_per_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "node.json"
            settings = {
                "node_id": "slave", "cluster_id": "home", "role": "follower",
                "mode": "shared_storage", "storage_backend": "filesystem",
                "shared_path": "/data/redundancy", "state_path": "/data/redundancy-state",
                "shared_secret": "x", "domains": "stream_profiles,output_profiles,user_agents",
                "protected_records": {"output_profiles": [3]},
            }
            with patch("failovarr.node_config.CONFIG_PATH", path):
                save_node_config(settings)
                engine = ReplicationEngine(settings)
                result = {"new_disabled_record_ids": {
                    "output_profiles": [5], "stream_profiles": [7],
                }}
                engine._remember_disabled_records(result)
                saved = load_node_config()
                self.assertEqual(saved["protected_records"]["output_profiles"], [3, 5])
                self.assertEqual(saved["protected_records"]["stream_profiles"], [7])
                self.assertEqual(result["new_records_kept_local"], {
                    "output_profiles": [5], "stream_profiles": [7],
                })

    def test_follower_initialization_requires_exact_node_confirmation(self):
        settings = {
            "node_id": "slave",
            "cluster_id": "home",
            "role": "follower",
            "mode": "shared_storage",
            "storage_backend": "filesystem",
            "shared_path": "/data/redundancy",
            "state_path": "/data/redundancy-state",
            "shared_secret": "x" * 32,
            "domains": "output_profiles",
        }
        server = SetupServer(settings, logging.getLogger("test"))
        engine = Mock()
        engine.initialize_follower.return_value = {"status": "initialized"}
        with patch.object(server, "_database_settings", return_value=settings), patch.object(
            server, "_engine", return_value=engine,
        ):
            with self.assertRaisesRegex(ValueError, "INITIALIZE slave"):
                server.initialize_follower({"confirmation": "yes"})
            self.assertEqual(
                server.initialize_follower({"confirmation": "INITIALIZE slave"}),
                {"status": "initialized"},
            )
            engine.initialize_follower.assert_called_once_with()

    def test_bundle_info_uses_entered_follower_transport_without_persisting(self):
        settings = {
            "node_id": "slave", "cluster_id": "home", "role": "follower",
            "mode": "shared_storage", "storage_backend": "filesystem",
            "shared_path": "/data/redundancy", "state_path": "/data/redundancy-state",
            "shared_secret": "x" * 32, "domains": "output_profiles",
        }
        server = SetupServer(settings, logging.getLogger("test"))
        engine = Mock()
        engine.bundle_info.return_value = {"status": "verified", "sequence": 4}
        with patch.object(server, "_database_settings", return_value=settings), patch.object(
            server, "_engine", return_value=engine,
        ):
            result = server.bundle_info(settings)
        self.assertEqual(result["status"], "verified")
        engine.bundle_info.assert_called_once_with()

    def test_import_latest_returns_a_benign_result_when_the_bundle_is_already_applied(self):
        server = SetupServer({}, logging.getLogger("test"))
        engine = Mock()
        engine.apply_latest.side_effect = BundleNotNewerState("same sequence")
        with patch.object(server, "_engine", return_value=engine):
            result = server.import_latest()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["reason"], "not_newer")
        self.assertIn("Already up to date", result["message"])


if __name__ == "__main__":
    unittest.main()
