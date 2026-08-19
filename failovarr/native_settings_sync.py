"""Synchronize the secret-free native Plugin Settings from node-local state."""

from __future__ import annotations

import os

from .config import PLUGIN_DB_KEY
from .node_config import load_node_config, native_settings_snapshot


def sync_native_settings() -> None:
    """Run Django ORM work in a dedicated process main thread.

    The caller has already atomically written the canonical node-local file.
    This module deliberately accepts no settings argument, preventing secrets
    from crossing a process boundary through argv or standard streams.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
    import django

    django.setup()
    from apps.plugins.models import PluginConfig

    settings = load_node_config()
    if not settings:
        raise RuntimeError("Node-local redundancy configuration is not available")
    plugin_config = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
    plugin_config.settings = native_settings_snapshot(settings)
    plugin_config.save(update_fields=["settings"])


def main() -> int:
    try:
        sync_native_settings()
    except Exception:
        # The parent deliberately exposes only a boolean warning. Avoid
        # leaking configuration through child tracebacks or stderr.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
