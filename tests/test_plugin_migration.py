import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
