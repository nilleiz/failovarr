import os
import unittest
from types import SimpleNamespace

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.domains import DomainSpec, _prepare_desired_records, apply_local_overrides


class OverrideTests(unittest.TestCase):
    def test_output_profile_override_keeps_id(self):
        result = apply_local_overrides(
            "output_profiles",
            [{"id": 3, "name": "HQ", "parameters": "cuda"}],
            {"output_profiles": {"3": {"parameters": "qsv"}}},
            "name",
            ("id", "name", "parameters"),
        )
        self.assertEqual(result, [{"id": 3, "name": "HQ", "parameters": "qsv"}])

    def test_override_cannot_change_id(self):
        with self.assertRaisesRegex(ValueError, "may not change id"):
            apply_local_overrides(
                "output_profiles",
                [{"id": 3, "name": "HQ"}],
                {"output_profiles": {"3": {"id": 99}}},
                "name",
                ("id", "name"),
            )

    def test_override_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            apply_local_overrides(
                "output_profiles",
                [{"id": 3, "name": "HQ"}],
                {"output_profiles": {"3": {"password": "do-not-copy"}}},
                "name",
                ("id", "name"),
            )

    def test_protected_profile_preserves_complete_local_record(self):
        spec = DomainSpec("output_profiles", None, ("id", "name", "command", "parameters", "locked", "is_active"), "name")
        source = [{"id": 3, "name": "Source", "command": "ffmpeg", "parameters": "source", "locked": False, "is_active": True}]
        local = [{"id": 3, "name": "Local QSV", "command": "ffmpeg", "parameters": "local", "locked": False, "is_active": False}]
        config = SimpleNamespace(local_overrides={}, protected_output_profile_ids=(3,), new_output_profile_policy="disabled")
        desired, blocked = _prepare_desired_records(spec, source, local, config)
        self.assertEqual(desired, local)
        self.assertEqual(blocked, [])

    def test_new_profile_is_disabled_by_default(self):
        spec = DomainSpec("output_profiles", None, ("id", "name", "is_active"), "name")
        config = SimpleNamespace(local_overrides={}, protected_output_profile_ids=(), new_output_profile_policy="disabled")
        desired, _ = _prepare_desired_records(spec, [{"id": 5, "name": "New", "is_active": True}], [], config)
        self.assertFalse(desired[0]["is_active"])

    def test_new_record_policy_applies_to_every_protectable_domain(self):
        fields = {
            "output_profiles": "new_output_profile_policy",
            "stream_profiles": "new_stream_profile_policy",
            "epg_sources": "new_epg_source_policy",
            "m3u_accounts": "new_m3u_account_policy",
        }
        for domain, field in fields.items():
            spec = DomainSpec(domain, None, ("id", "name", "is_active"), "name")
            for policy in ("disabled", "source", "block"):
                with self.subTest(domain=domain, policy=policy):
                    config = SimpleNamespace(
                        local_overrides={}, protected_records={},
                        protected_output_profile_ids=(), **{field: policy},
                    )
                    desired, blocked = _prepare_desired_records(
                        spec, [{"id": 7, "name": "New", "is_active": True}], [], config,
                    )
                    self.assertEqual(desired[0]["is_active"], policy != "disabled")
                    self.assertEqual(blocked, [7] if policy == "block" else [])
