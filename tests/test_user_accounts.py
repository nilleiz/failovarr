import os
import unittest

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

from failovarr.user_accounts import normalize_user_import_rules


class UserImportRuleTests(unittest.TestCase):
    def test_default_selects_all_current_and_future_main_users(self):
        rules = normalize_user_import_rules()
        self.assertTrue(rules["default_selected"])
        self.assertEqual(rules["users"], {})

    def test_legacy_wildcard_maps_to_all_users(self):
        rules = normalize_user_import_rules(None, "*")
        self.assertTrue(rules["default_selected"])
        self.assertEqual(rules["users"], {})

        list_rules = normalize_user_import_rules(None, ["*"])
        self.assertTrue(list_rules["default_selected"])
        self.assertEqual(list_rules["users"], {})

    def test_legacy_empty_list_uses_the_new_all_users_default(self):
        rules = normalize_user_import_rules(None, "")
        self.assertTrue(rules["default_selected"])
        self.assertEqual(rules["users"], {})

    def test_legacy_name_list_maps_to_only_those_users(self):
        rules = normalize_user_import_rules(None, "viewer, editor")
        self.assertFalse(rules["default_selected"])
        self.assertEqual(set(rules["users"]), {"viewer", "editor"})
        self.assertTrue(all(rule["selected"] for rule in rules["users"].values()))

    def test_per_user_selection_and_active_override_are_preserved(self):
        rules = normalize_user_import_rules({
            "format": 1,
            "default_selected": True,
            "users": {"admin": {"selected": False, "active": "inactive"}},
        })
        self.assertEqual(rules["users"]["admin"], {"selected": False, "active": "inactive"})

    def test_invalid_rules_fail_closed(self):
        invalid = (
            {"format": 2, "default_selected": True, "users": {}},
            {"format": 1, "default_selected": "yes", "users": {}},
            {"format": 1, "default_selected": True, "users": {"admin": {"active": "sometimes"}}},
            {"format": 1, "default_selected": True, "users": {"admin": {"selected": 1}}},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_user_import_rules(value)


if __name__ == "__main__":
    unittest.main()
