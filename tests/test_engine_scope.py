import os
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.bundle import create_envelope
from failovarr.config import CORE_SETTING_GROUPS, FULL_DOMAINS
from failovarr.engine import ReplicationEngine


def settings(role="follower", **overrides):
    result = {
        "node_id": "main" if role == "leader" else "slave",
        "cluster_id": "home",
        "role": role,
        "mode": "shared_storage",
        "shared_path": "/data/redundancy",
        "state_path": "/data/failovarr-state",
        "shared_secret": "scope-test-secret",
        "replication_scope": "custom",
        "domains": "output_profiles",
        "core_setting_keys": "stream_settings",
    }
    result.update(overrides)
    return result


class EngineScopeTests(unittest.TestCase):
    def test_main_export_uses_complete_supported_scope(self):
        engine = ReplicationEngine(settings("leader"))
        engine.state_store = MagicMock()
        engine.state_store.read_state.return_value = {"authoritative": True}
        engine.state_store.exclusive_lock.return_value.__enter__.return_value = None
        engine.outbound_store = Mock()
        engine.store = Mock()
        with patch("failovarr.engine.export_domains", return_value={}) as export, patch(
            "failovarr.engine.export_client_identity", return_value={"format": 1, "users": []},
        ):
            result = engine.export_now()
        exported_config = export.call_args.args[0]
        self.assertEqual(exported_config.domains, FULL_DOMAINS)
        self.assertEqual(exported_config.core_setting_keys, tuple(CORE_SETTING_GROUPS))
        self.assertEqual(result["scope"]["domains"], list(FULL_DOMAINS))

    def test_follower_keeps_local_scope_and_filters_complete_bundle(self):
        engine = ReplicationEngine(settings())
        payload = {
            "scope": {"domains": list(FULL_DOMAINS), "core_setting_keys": list(CORE_SETTING_GROUPS)},
            "domains": {domain: [] for domain in FULL_DOMAINS},
        }
        local = engine.config_for_payload(payload)
        self.assertEqual(local.domains, ("output_profiles",))
        self.assertEqual(engine._selected_payload_domains(payload, local), {"output_profiles": []})

    def test_follower_filters_unselected_core_settings_before_all_apply_paths(self):
        engine = ReplicationEngine(settings(domains="core_settings", core_setting_keys="stream_settings"))
        payload = {
            "scope": {"domains": list(FULL_DOMAINS), "core_setting_keys": list(CORE_SETTING_GROUPS)},
            "domains": {
                **{domain: [] for domain in FULL_DOMAINS},
                "core_settings": [
                    {"id": 1, "key": "stream_settings", "name": "Stream", "value": {}},
                    {"id": 2, "key": "dvr_settings", "name": "DVR", "value": {}},
                ],
            },
        }
        selected = engine._selected_payload_domains(payload, engine.config_for_payload(payload))
        self.assertEqual([row["key"] for row in selected["core_settings"]], ["stream_settings"])

    def test_bundle_info_reports_own_node_without_exposing_bundle_data(self):
        engine = ReplicationEngine(settings())
        envelope = create_envelope(
            cluster_id="home", source_node="slave", sequence=4,
            domains={"output_profiles": []}, secret="scope-test-secret",
        )
        engine._load_candidate = Mock(return_value=envelope)
        info = engine.bundle_info()
        self.assertEqual(info["status"], "own_bundle")
        self.assertIn("different Follower node name", info["message"])

    def test_bundle_info_reports_current_when_payload_and_scope_are_applied(self):
        engine = ReplicationEngine(settings())
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=4,
            domains={"output_profiles": []}, secret="scope-test-secret",
        )
        engine._load_candidate = Mock(return_value=envelope)
        engine.state_store = Mock()
        engine.state_store.read_state.return_value = {
            "applied_sequence": 4,
            "exported_sequence": 0,
            "applied_hash": envelope["payload_sha256"],
            "applied_scope_fingerprint": engine._scope_fingerprint(engine.config),
        }
        info = engine.bundle_info()
        self.assertEqual(info["status"], "current")
        self.assertIn("already applied", info["message"])

    def test_bundle_info_reports_stale_when_node_knows_a_newer_sequence(self):
        engine = ReplicationEngine(settings())
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=4,
            domains={"output_profiles": []}, secret="scope-test-secret",
        )
        engine._load_candidate = Mock(return_value=envelope)
        engine.state_store = Mock()
        engine.state_store.read_state.return_value = {
            "applied_sequence": 5,
            "exported_sequence": 0,
            "applied_hash": "newer-payload",
        }
        self.assertEqual(engine.bundle_info()["status"], "stale")

    def test_incomplete_legacy_bundle_explains_that_main_must_export_again(self):
        engine = ReplicationEngine(settings(domains="stream_profiles"))
        payload = {
            "scope": {"domains": ["output_profiles"], "core_setting_keys": []},
            "domains": {"output_profiles": []},
        }
        with self.assertRaisesRegex(ValueError, "Export a new complete bundle from Main"):
            engine.config_for_payload(payload)

    def test_same_verified_bundle_can_be_reapplied_after_local_scope_change(self):
        engine = ReplicationEngine(settings())
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=4,
            domains={"output_profiles": []}, secret="scope-test-secret",
        )
        engine._load_candidate = Mock(return_value=envelope)
        engine.state_store = Mock()
        engine.state_store.read_state.return_value = {
            "applied_sequence": 4,
            "exported_sequence": 0,
            "applied_hash": envelope["payload_sha256"],
            "applied_scope_fingerprint": "different-local-selection",
        }
        _envelope, payload = engine.verified_candidate(require_new=True)
        self.assertEqual(payload["sequence"], 4)
        self.assertEqual(engine.bundle_info()["status"], "verified")

    def test_same_sequence_with_a_different_payload_remains_rejected(self):
        engine = ReplicationEngine(settings())
        envelope = create_envelope(
            cluster_id="home", source_node="main", sequence=4,
            domains={"output_profiles": []}, secret="scope-test-secret",
        )
        engine._load_candidate = Mock(return_value=envelope)
        engine.state_store = Mock()
        engine.state_store.read_state.return_value = {
            "applied_sequence": 4,
            "exported_sequence": 0,
            "applied_hash": "different-hash",
            "applied_scope_fingerprint": "different-local-selection",
        }
        with self.assertRaisesRegex(ValueError, "not newer"):
            engine.verified_candidate(require_new=True)


if __name__ == "__main__":
    unittest.main()
