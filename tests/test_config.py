import os
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.config import (
    ConfigValidationError, DEFAULT_KNOWN_HOSTS_PATH, ReplicationConfig, build_plugin_fields, configuration_issues, deep_merge,
    IPTV_CONTENT_DOMAINS, storage_probe_config,
)


BASE = {
    "node_id": "slave",
    "cluster_id": "home",
    "role": "follower",
    "mode": "shared_storage",
    "shared_path": "/data/bundles",
    "state_path": "/data/local-state",
    "shared_secret": "a-development-secret-long-enough",
    "core_setting_keys": "stream_settings,proxy_settings",
}


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_siblings(self):
        result = deep_merge(
            {"value": {"comskip": False, "suffix": ".ts"}},
            {"value": {"comskip": True}},
        )
        self.assertEqual(result, {"value": {"comskip": True, "suffix": ".ts"}})

    def test_short_nonempty_secret_is_accepted(self):
        settings = {**BASE, "shared_secret": "short"}
        self.assertEqual(ReplicationConfig.from_settings(settings).shared_secret, "short")

    def test_redundancy_mode_maps_legacy_engine_fields(self):
        vip = ReplicationConfig.from_settings({
            **BASE, "redundancy_mode": "plugin_vip", "client_vip": "192.168.178.210",
        })
        self.assertEqual(vip.deployment_mode, "online")
        self.assertEqual(vip.client_access_mode, "plugin_vip")
        cold = ReplicationConfig.from_settings({**BASE, "redundancy_mode": "cold_standby"})
        self.assertEqual(cold.deployment_mode, "cold_standby")
        self.assertEqual(cold.client_access_mode, "disabled")
        self.assertTrue(cold.import_on_start)
        self.assertTrue(cold.auto_start)
        self.assertFalse(cold.automatic_apply)

    def test_cold_standby_preserves_explicitly_disabled_autostart(self):
        cold = ReplicationConfig.from_settings({
            **BASE, "redundancy_mode": "cold_standby", "auto_start": False,
        })
        self.assertFalse(cold.auto_start)
        self.assertTrue(cold.import_on_start)
        self.assertFalse(cold.automatic_apply)

    def test_missing_node_name_is_a_field_specific_error(self):
        with self.assertRaises(ConfigValidationError) as raised:
            ReplicationConfig.from_settings({**BASE, "node_id": ""})
        self.assertEqual(raised.exception.field, "node_id")
        self.assertEqual(raised.exception.code, "required")
        self.assertEqual(str(raised.exception), "Node name is required.")

    def test_invalid_node_name_explains_the_actual_constraint(self):
        with self.assertRaises(ConfigValidationError) as raised:
            ReplicationConfig.from_settings({**BASE, "node_id": "bad name"})
        self.assertEqual(raised.exception.field, "node_id")
        self.assertEqual(raised.exception.code, "invalid_characters")
        self.assertIn("may contain only", str(raised.exception))

    def test_fresh_setup_reports_missing_fields_without_constructing_engine(self):
        issues = configuration_issues({})
        self.assertEqual([issue.field for issue in issues], ["node_id", "cluster_id", "shared_secret"])

    def test_unknown_domain_is_rejected(self):
        settings = {**BASE, "domains": "output_profiles,users"}
        with self.assertRaisesRegex(ValueError, "Unsupported domains"):
            ReplicationConfig.from_settings(settings)

    def test_direct_follower_requires_peer(self):
        settings = {**BASE, "mode": "direct"}
        with self.assertRaisesRegex(ValueError, "Main peer URL"):
            ReplicationConfig.from_settings(settings)

    def test_stream_profiles_require_user_agents(self):
        settings = {**BASE, "domains": "stream_profiles"}
        with self.assertRaisesRegex(ValueError, "requires replication domains: user_agents"):
            ReplicationConfig.from_settings(settings)

    def test_channel_domain_requires_all_foreign_key_domains(self):
        settings = {**BASE, "domains": "channels"}
        with self.assertRaisesRegex(ValueError, "channels requires replication domains"):
            ReplicationConfig.from_settings(settings)

    def test_m3u_accounts_do_not_depend_on_child_profiles(self):
        settings = {
            **BASE,
            "domains": "user_agents,stream_profiles,server_groups,m3u_accounts",
        }
        config = ReplicationConfig.from_settings(settings)
        self.assertIn("m3u_accounts", config.domains)

    def test_m3u_account_profiles_require_parent_accounts(self):
        settings = {**BASE, "domains": "m3u_account_profiles"}
        with self.assertRaisesRegex(
            ValueError, "m3u_account_profiles requires replication domains: m3u_accounts",
        ):
            ReplicationConfig.from_settings(settings)

    def test_provider_domains_are_not_enabled_by_default(self):
        config = ReplicationConfig.from_settings(BASE)
        self.assertNotIn("m3u_accounts", config.domains)
        self.assertNotIn("epg_sources", config.domains)
        self.assertNotIn("channels", config.domains)

    def test_paths_must_stay_below_data(self):
        settings = {**BASE, "state_path": "/data/../etc"}
        with self.assertRaisesRegex(ValueError, "below /data"):
            ReplicationConfig.from_settings(settings)

    def test_peer_url_rejects_credentials_and_paths(self):
        settings = {
            **BASE, "mode": "direct",
            "peer_url": "http://user:secret@peer.invalid:9192/private",
        }
        with self.assertRaisesRegex(ValueError, "may not contain credentials"):
            ReplicationConfig.from_settings(settings)

    def test_override_domain_must_be_enabled(self):
        settings = {
            **BASE,
            "domains": "output_profiles",
            "local_overrides": '{"core_settings": {}}',
        }
        with self.assertRaisesRegex(ValueError, "not enabled"):
            ReplicationConfig.from_settings(settings)

    def test_client_identity_guard_checks_all_credential_users_by_default(self):
        config = ReplicationConfig.from_settings(BASE)
        self.assertEqual(config.client_identity_users, ("*",))

    def test_client_identity_wildcard_cannot_be_combined(self):
        settings = {**BASE, "client_identity_users": "*,viewer"}
        with self.assertRaisesRegex(ValueError, "only by itself"):
            ReplicationConfig.from_settings(settings)

    def test_webdav_requires_https_by_default(self):
        settings = {
            **BASE,
            "storage_backend": "webdav",
            "storage_endpoint": "http://webdav.invalid",
            "storage_container": "redundancy",
        }
        with self.assertRaisesRegex(ValueError, "allow_insecure_http"):
            ReplicationConfig.from_settings(settings)

    def test_sftp_uses_secure_default_known_hosts_path(self):
        settings = {
            **BASE,
            "storage_backend": "sftp",
            "storage_endpoint": "sftp://storage.invalid:22",
            "storage_container": "redundancy",
        }
        config = ReplicationConfig.from_settings(settings)
        self.assertEqual(config.storage_options["known_hosts_path"], DEFAULT_KNOWN_HOSTS_PATH)

    def test_tls_verification_cannot_be_disabled(self):
        settings = {
            **BASE,
            "storage_backend": "webdav",
            "storage_endpoint": "https://webdav.invalid",
            "storage_container": "redundancy",
            "storage_options": '{"ca_path": false}',
        }
        with self.assertRaisesRegex(ValueError, "may not be disabled"):
            ReplicationConfig.from_settings(settings)

    def test_smb_encryption_cannot_be_disabled(self):
        settings = {
            **BASE,
            "storage_backend": "smb",
            "storage_endpoint": "smb://storage.invalid",
            "storage_container": "redundancy",
            "storage_options": '{"require_encryption": false}',
        }
        with self.assertRaisesRegex(ValueError, "may not be disabled"):
            ReplicationConfig.from_settings(settings)

    def test_plugin_vip_requires_private_ipv4(self):
        settings = {**BASE, "client_access_mode": "plugin_vip", "client_vip": "8.8.8.8"}
        with self.assertRaisesRegex(ValueError, "private unicast"):
            ReplicationConfig.from_settings(settings)

    def test_cold_standby_does_not_require_vip(self):
        config = ReplicationConfig.from_settings(BASE)
        self.assertEqual(config.client_access_mode, "disabled")

    def test_peer_node_must_differ_from_local_node(self):
        settings = {**BASE, "peer_node_id": "slave"}
        with self.assertRaisesRegex(ValueError, "must differ"):
            ReplicationConfig.from_settings(settings)

    def test_custom_scope_adds_dependencies(self):
        config = ReplicationConfig.from_settings({
            **BASE, "replication_scope": "custom", "domains": "channels",
        })
        self.assertIn("m3u_accounts", config.domains)
        self.assertIn("logos", config.domains)
        self.assertIn("channels", config.domains)

    def test_content_only_preset_has_the_full_iptv_graph_without_core_or_output_settings(self):
        config = ReplicationConfig.from_settings({
            **BASE, "replication_scope": "iptv_content", "core_setting_keys": "",
        })
        self.assertEqual(config.domains, IPTV_CONTENT_DOMAINS)
        self.assertNotIn("core_settings", config.domains)
        self.assertNotIn("output_profiles", config.domains)
        self.assertEqual(config.core_setting_keys, ())

    def test_explicit_storage_fields_replace_json_editing(self):
        config = ReplicationConfig.from_settings({
            **BASE,
            "storage_backend": "sftp",
            "storage_endpoint": "sftp://storage.invalid",
            "storage_container": "redundancy",
            "sftp_known_hosts_path": "/data/trust/known_hosts",
            "storage_timeout_seconds": 42,
        })
        self.assertEqual(config.storage_options["known_hosts_path"], "/data/trust/known_hosts")
        self.assertEqual(config.storage_options["timeout_seconds"], 42)

    def test_local_protection_is_inactive_when_main_removes_its_domain(self):
        config = ReplicationConfig.from_settings({
            **BASE, "domains": "core_settings", "protected_output_profile_ids": "3",
        })
        self.assertEqual(config.protected_records["output_profiles"], (3,))
        self.assertNotIn("output_profiles", config.domains)

    def test_multiple_local_record_domains_are_supported(self):
        config = ReplicationConfig.from_settings({
            **BASE,
            "domains": "user_agents,stream_profiles,server_groups,m3u_accounts,epg_sources,output_profiles",
            "protected_records": {
                "stream_profiles": [2], "m3u_accounts": [4], "epg_sources": [6],
            },
        })
        self.assertEqual(config.protected_records["stream_profiles"], (2,))
        self.assertEqual(config.protected_records["m3u_accounts"], (4,))
        self.assertEqual(config.protected_records["epg_sources"], (6,))

    def test_native_fields_show_only_protection_domains_in_follower_scope(self):
        fields = build_plugin_fields(
            {
                **BASE,
                "replication_scope": "custom",
                "domains": "user_agents,stream_profiles",
                "protected_records": {
                    "m3u_accounts": [2], "stream_profiles": [3],
                    "output_profiles": [4],
                },
            },
            {
                "m3u_accounts": [{"id": 2, "name": "provider"}],
                "stream_profiles": [{"id": 3, "name": "profile"}],
                "output_profiles": [{"id": 4, "name": "output"}],
            },
        )
        ids = [field.get("id") for field in fields]
        self.assertIn("protect_stream_profiles_3", ids)
        self.assertIn("new_stream_profile_policy", ids)
        self.assertNotIn("protect_m3u_accounts_2", ids)
        self.assertNotIn("new_m3u_account_policy", ids)
        self.assertNotIn("protect_output_profiles_4", ids)
        self.assertNotIn("new_output_profile_policy", ids)

    def test_storage_probe_works_before_cluster_setup(self):
        config = storage_probe_config({
            "storage_backend": "filesystem",
            "shared_path": "/data/storage-probe",
        })
        self.assertEqual(config.node_id, "storage-test")
        self.assertEqual(config.cluster_id, "storage-test")
        self.assertEqual(config.domains, ("output_profiles",))
        self.assertTrue(config.shared_secret)

    def test_native_fields_mirror_safe_settings_without_secrets(self):
        fields = build_plugin_fields(
            {**BASE, "role": "follower", "domains": "output_profiles", "storage_backend": "sftp"},
            {"output_profiles": [{"id": 3, "name": "Local QSV", "is_active": True}]},
        )
        field_ids = {field["id"] for field in fields}
        self.assertIn("node_id", field_ids)
        self.assertIn("storage_endpoint", field_ids)
        self.assertIn("sftp_known_hosts_path", field_ids)
        self.assertIn("redundancy_mode", field_ids)
        self.assertNotIn("deployment_mode", field_ids)
        self.assertNotIn("client_access_mode", field_ids)
        self.assertIn("protect_output_profiles_3", field_ids)
        self.assertIn("replication_scope", field_ids)
        self.assertNotIn("shared_secret", field_ids)
        self.assertNotIn("storage_password", field_ids)

    def test_main_native_settings_omit_redundant_export_copy_and_put_role_first(self):
        fields = build_plugin_fields({**BASE, "role": "leader"})
        ids = {field.get("id") for field in fields}
        labels = {field.get("label") for field in fields}
        self.assertNotIn("replication_scope", ids)
        self.assertNotIn("allow_deletes", ids)
        self.assertNotIn("Data exported by Main", labels)
        self.assertEqual(next(field["id"] for field in fields if field.get("type") != "info"), "role")

    def test_native_follower_offers_content_only_preset_and_local_state_help(self):
        fields = build_plugin_fields({**BASE, "role": "follower"})
        scope = next(field for field in fields if field.get("id") == "replication_scope")
        self.assertIn(
            {"value": "iptv_content", "label": "M3U, EPG and Channels only"},
            scope["options"],
        )
        state = next(field for field in fields if field.get("id") == "state_path")
        self.assertIn("sequences", state["description"])

    def test_every_local_record_area_has_an_adjacent_new_record_policy(self):
        records = {
            domain: [{"id": index, "name": domain, "is_active": True}]
            for index, domain in enumerate(
            ("m3u_accounts", "epg_sources", "stream_profiles", "output_profiles"), 1,
            )
        }
        fields = build_plugin_fields({
            **BASE,
            "role": "follower",
            "replication_scope": "custom",
            "domains": "user_agents,stream_profiles,server_groups,m3u_accounts,epg_sources,output_profiles",
        }, records)
        ids = [field["id"] for field in fields]
        expected = {
            "output_profiles": "new_output_profile_policy",
            "stream_profiles": "new_stream_profile_policy",
            "epg_sources": "new_epg_source_policy",
            "m3u_accounts": "new_m3u_account_policy",
        }
        for domain, policy in expected.items():
            self.assertEqual(ids.index(policy), ids.index(f"protect_{domain}_{list(records).index(domain) + 1}") + 1)

    def test_new_record_policies_default_to_disabled(self):
        config = ReplicationConfig.from_settings(BASE)
        self.assertEqual(config.new_output_profile_policy, "disabled")
        self.assertEqual(config.new_stream_profile_policy, "disabled")
        self.assertEqual(config.new_epg_source_policy, "disabled")
        self.assertEqual(config.new_m3u_account_policy, "disabled")

    def test_channel_stream_mirroring_is_follower_local_and_defaults_off(self):
        default = ReplicationConfig.from_settings({
            **BASE, "replication_scope": "iptv_content", "core_setting_keys": "",
        })
        enabled = ReplicationConfig.from_settings({
            **BASE, "replication_scope": "iptv_content", "core_setting_keys": "",
            "mirror_channel_stream_assignments": True,
        })
        self.assertFalse(default.mirror_channel_stream_assignments)
        self.assertTrue(enabled.mirror_channel_stream_assignments)

    def test_native_settings_show_channel_stream_mirroring_only_when_selected(self):
        selected = build_plugin_fields({
            **BASE, "replication_scope": "iptv_content", "core_setting_keys": "",
        })
        excluded = build_plugin_fields({
            **BASE, "domains": "output_profiles",
        })
        selected_ids = {field.get("id") for field in selected}
        excluded_ids = {field.get("id") for field in excluded}
        main_ids = {field.get("id") for field in build_plugin_fields({
            **BASE, "role": "leader", "domains": "channel_streams",
        })}
        self.assertIn("mirror_channel_stream_assignments", selected_ids)
        self.assertNotIn("mirror_channel_stream_assignments", excluded_ids)
        self.assertNotIn("mirror_channel_stream_assignments", main_ids)
