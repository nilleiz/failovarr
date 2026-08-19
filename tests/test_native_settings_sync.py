import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from failovarr import native_settings_sync


class NativeSettingsSyncTests(unittest.TestCase):
    def test_child_writes_only_the_native_secret_free_snapshot(self):
        django_module = ModuleType("django")
        django_module.setup = Mock()
        apps_module = ModuleType("apps")
        apps_module.__path__ = []
        plugins_module = ModuleType("apps.plugins")
        plugins_module.__path__ = []
        models_module = ModuleType("apps.plugins.models")
        plugin_config = Mock()
        manager = Mock()
        manager.get.return_value = plugin_config
        models_module.PluginConfig = SimpleNamespace(objects=manager)
        settings = {
            "node_id": "main", "shared_secret": "must-not-reach-native-settings",
            "storage_password": "must-not-reach-native-settings",
        }
        snapshot = {"node_id": "main", "role": "leader"}
        with patch.object(native_settings_sync, "load_node_config", return_value=settings), patch.object(
            native_settings_sync, "native_settings_snapshot", return_value=snapshot,
        ) as build_snapshot, patch.dict(sys.modules, {
            "django": django_module,
            "apps": apps_module,
            "apps.plugins": plugins_module,
            "apps.plugins.models": models_module,
        }):
            native_settings_sync.sync_native_settings()
        django_module.setup.assert_called_once_with()
        build_snapshot.assert_called_once_with(settings)
        self.assertEqual(plugin_config.settings, snapshot)
        self.assertNotIn("shared_secret", plugin_config.settings)
        self.assertNotIn("storage_password", plugin_config.settings)
        plugin_config.save.assert_called_once_with(update_fields=["settings"])


if __name__ == "__main__":
    unittest.main()
