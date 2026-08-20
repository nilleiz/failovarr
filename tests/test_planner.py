import os
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.planner import plan_records, reconcile_surrogate_ids


class PlannerTests(unittest.TestCase):
    def test_stable_id_update(self):
        plan = plan_records(
            [{"id": 3, "name": "HQ", "parameters": "cuda"}],
            [{"id": 3, "name": "HQ", "parameters": "qsv"}],
            natural_key="name", allow_deletes=False,
        )
        self.assertEqual(plan["update"][0]["id"], 3)
        self.assertEqual(plan["update"][0]["changes"]["parameters"]["to"], "qsv")

    def test_id_collision_is_conflict(self):
        plan = plan_records(
            [{"id": 3, "name": "Unrelated"}],
            [{"id": 3, "name": "HQ"}],
            natural_key="name", allow_deletes=False,
        )
        self.assertEqual(plan["conflicts"][0]["reason"], "id_has_different_natural_key")

    def test_natural_key_on_other_id_is_conflict(self):
        plan = plan_records(
            [{"id": 4, "name": "HQ"}],
            [{"id": 3, "name": "HQ"}],
            natural_key="name", allow_deletes=False,
        )
        self.assertEqual(plan["conflicts"][0]["existing_id"], 4)

    def test_deletes_are_opt_in(self):
        current = [{"id": 1, "name": "old"}]
        self.assertEqual(
            plan_records(current, [], natural_key="name", allow_deletes=False)["delete"], []
        )
        self.assertEqual(
            plan_records(current, [], natural_key="name", allow_deletes=True)["delete"], current
        )

    def test_duplicate_incoming_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate IDs"):
            plan_records(
                [], [{"id": 3, "name": "one"}, {"id": 3, "name": "two"}],
                natural_key="name", allow_deletes=False,
            )

    def test_duplicate_incoming_natural_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate name"):
            plan_records(
                [], [{"id": 3, "name": "same"}, {"id": 4, "name": "same"}],
                natural_key="name", allow_deletes=False,
            )

    def test_composite_natural_key_update(self):
        plan = plan_records(
            [{"id": 9, "channel_profile_id": 2, "channel_id": 7, "enabled": False}],
            [{"id": 9, "channel_profile_id": 2, "channel_id": 7, "enabled": True}],
            natural_key=("channel_profile_id", "channel_id"),
            allow_deletes=False,
        )
        self.assertEqual(plan["update"][0]["changes"]["enabled"]["to"], True)

    def test_composite_natural_key_collision_is_conflict(self):
        plan = plan_records(
            [{"id": 9, "channel_profile_id": 2, "channel_id": 8, "enabled": True}],
            [{"id": 9, "channel_profile_id": 2, "channel_id": 7, "enabled": True}],
            natural_key=("channel_profile_id", "channel_id"),
            allow_deletes=False,
        )
        self.assertEqual(plan["conflicts"][0]["reason"], "id_has_different_natural_key")
        self.assertEqual(plan["conflicts"][0]["field"], "channel_profile_id+channel_id")

    def test_duplicate_composite_natural_keys_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "duplicate channel_profile_id\\+channel_id"
        ):
            plan_records(
                [],
                [
                    {"id": 9, "channel_profile_id": 2, "channel_id": 7},
                    {"id": 10, "channel_profile_id": 2, "channel_id": 7},
                ],
                natural_key=("channel_profile_id", "channel_id"),
                allow_deletes=False,
            )

    def test_nullable_unique_key_allows_multiple_nulls(self):
        result = plan_records(
            [],
            [{"id": 1, "name": "one", "hash": None}, {"id": 2, "name": "two", "hash": None}],
            natural_key="name", unique_keys=("name", "hash"), allow_deletes=False,
        )
        self.assertEqual(len(result["create"]), 2)

    def test_non_null_unique_key_still_conflicts(self):
        with self.assertRaisesRegex(ValueError, "duplicate hash"):
            plan_records(
                [],
                [{"id": 1, "name": "one", "hash": "same"}, {"id": 2, "name": "two", "hash": "same"}],
                natural_key="name", unique_keys=("name", "hash"), allow_deletes=False,
            )

    def test_surrogate_relation_keeps_local_id_for_same_natural_key(self):
        result = reconcile_surrogate_ids(
            [{"id": 40, "channel_id": 2, "stream_id": 7, "order": 0}],
            [{"id": 90, "channel_id": 2, "stream_id": 7, "order": 1}],
            natural_key=("channel_id", "stream_id"),
        )
        self.assertEqual(result[0]["id"], 40)
        plan = plan_records(
            [{"id": 40, "channel_id": 2, "stream_id": 7, "order": 0}], result,
            natural_key=("channel_id", "stream_id"), allow_deletes=False,
        )
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(plan["update"][0]["id"], 40)
        self.assertEqual(plan["update"][0]["changes"]["order"]["to"], 1)

    def test_surrogate_relation_handles_recreated_id_swaps(self):
        current = [
            {"id": 40, "channel_id": 2, "stream_id": 7, "order": 0},
            {"id": 41, "channel_id": 2, "stream_id": 8, "order": 1},
        ]
        incoming = [
            {"id": 41, "channel_id": 2, "stream_id": 7, "order": 0},
            {"id": 40, "channel_id": 2, "stream_id": 8, "order": 1},
        ]
        result = reconcile_surrogate_ids(
            current, incoming, natural_key=("channel_id", "stream_id"),
        )
        self.assertEqual([row["id"] for row in result], [40, 41])
        self.assertEqual(
            plan_records(
                current, result, natural_key=("channel_id", "stream_id"), allow_deletes=False,
            )["conflicts"],
            [],
        )

    def test_surrogate_relation_allocates_above_colliding_ids(self):
        result = reconcile_surrogate_ids(
            [{"id": 40, "channel_id": 2, "stream_id": 7, "order": 0}],
            [{"id": 40, "channel_id": 2, "stream_id": 8, "order": 0}],
            natural_key=("channel_id", "stream_id"),
        )
        self.assertEqual(result[0]["id"], 41)

    def test_surrogate_relation_keeps_free_main_id_for_new_assignment(self):
        result = reconcile_surrogate_ids(
            [{"id": 40, "channel_id": 2, "stream_id": 7, "order": 0}],
            [{"id": 70, "channel_id": 2, "stream_id": 8, "order": 0}],
            natural_key=("channel_id", "stream_id"),
        )
        self.assertEqual(result[0]["id"], 70)

    def test_strict_records_still_conflict_without_surrogate_reconciliation(self):
        plan = plan_records(
            [{"id": 40, "channel_id": 2, "stream_id": 7}],
            [{"id": 90, "channel_id": 2, "stream_id": 7}],
            natural_key=("channel_id", "stream_id"), allow_deletes=False,
        )
        self.assertEqual(plan["conflicts"][0]["reason"], "natural_key_is_used_by_another_id")
