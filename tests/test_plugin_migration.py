import os
import unittest
from unittest.mock import Mock, patch

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"

import failovarr
from failovarr.config import ConfigValidationError


class PluginMigrationTests(unittest.TestCase):
    def test_enabled_legacy_plugin_blocks_failovarr_runtime_actions(self):
        with patch.object(failovarr, "_legacy_plugin_is_enabled", return_value=True):
            with self.assertRaises(ConfigValidationError) as raised:
                failovarr._ensure_no_legacy_plugin()
        self.assertEqual(raised.exception.field, "legacy_plugin")
        self.assertEqual(raised.exception.code, "legacy_plugin_enabled")

    def test_force_import_action_requires_confirmation_and_uses_exact_reapply_flag(self):
        action = next(item for item in failovarr.Plugin.actions if item["id"] == "force_import_latest")
        self.assertEqual(action["button_color"], "red")
        self.assertTrue(action["confirm"]["required"])

        engine = Mock()
        engine.apply_latest.return_value = {"status": "applied", "forced_reapply": True}
        plugin = failovarr.Plugin()
        with patch.object(failovarr, "effective_settings", return_value={}), patch.object(
            failovarr, "ReplicationEngine", return_value=engine,
        ):
            result = plugin.run("force_import_latest", {}, {"settings": {}})
        engine.apply_latest.assert_called_once_with(force_reapply=True)
        self.assertTrue(result["forced_reapply"])


if __name__ == "__main__":
    unittest.main()
