"""Dispatcharr plugin entry point."""

from __future__ import annotations

import logging
import os
import threading
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from .autostart import (
    ServiceAlreadyRunning,
    acquire_service_lease,
    attempt_autostart,
    release_service_lease,
    request_service_stop,
    service_is_running,
    wait_for_service_stop,
)
from .config import (
    LOCAL_PROTECTION_DOMAINS, PLUGIN_CONFIG, PLUGIN_DB_KEY, ConfigValidationError,
    ReplicationConfig, build_plugin_fields, configuration_issues, storage_probe_config,
    validation_error_payload,
)
from .engine import BackgroundService, ReplicationEngine
from .remote_storage import test_storage_connection
from .node_config import effective_settings, save_node_config
from .logging_utils import plugin_logger

logger = plugin_logger(logging.getLogger("dispatcharr.plugins.failovarr"))
_service_lock = threading.Lock()
_service: BackgroundService | None = None
_setup_lock = threading.Lock()
_setup_process: subprocess.Popen | None = None
LEGACY_PLUGIN_DB_KEY = "dispatcharr_redundancy"


def _legacy_plugin_is_enabled() -> bool:
    """Avoid two plugin identities competing for the same container resources."""
    try:
        from apps.plugins.models import PluginConfig

        legacy = PluginConfig.objects.filter(key=LEGACY_PLUGIN_DB_KEY).first()
        return bool(legacy and legacy.enabled)
    except Exception:
        # Plugin discovery can happen while the database is unavailable.  The
        # regular action path checks again before it makes a runtime change.
        return False


def _ensure_no_legacy_plugin() -> None:
    if _legacy_plugin_is_enabled():
        raise ConfigValidationError(
            "legacy_plugin", "legacy_plugin_enabled",
            "Dispatcharr Redundancy is still enabled. Disable it before enabling Failovarr; both plugins cannot run together.",
        )


def _start_service(settings, logger_override=None) -> BackgroundService:
    global _service
    with _service_lock:
        if _service and _service.status()["running"]:
            return _service
        action_logger = plugin_logger(logger_override or logger)
        effective = effective_settings(settings)
        # Port 9192 is a single plugin-owned endpoint for setup, direct pull and
        # readiness. Start it before the worker loop so the two servers can
        # never race for the same port.
        _start_setup(effective, action_logger)
        engine = ReplicationEngine(effective, action_logger)
        redis_client, lease_token = acquire_service_lease()
        service = BackgroundService(engine, redis_client, lease_token, manage_http=False)
        try:
            service.start()
        except Exception:
            release_service_lease(redis_client, lease_token)
            raise
        _service = service
        action_logger.info(
            "Service started (node=%s role=%s deployment=%s)",
            engine.config.node_id, engine.config.role, engine.config.deployment_mode,
        )
        return service


def _stop_service(flush: bool = False, logger_override=None) -> bool:
    global _service
    with _service_lock:
        if not _service:
            return request_service_stop(flush=flush)
        if flush:
            _service.flush_on_shutdown()
        _service.stop()
        plugin_logger(logger_override or logger).info("Service stopped")
        _service = None
        return True


def reconcile_service(settings, logger_override=None) -> dict:
    """Hand the container-wide service lease to the saved configuration."""
    action_logger = plugin_logger(logger_override or logger)
    effective = effective_settings(settings)
    if not effective.get("auto_start", False):
        was_running = _stop_service(logger_override=action_logger)
        if was_running and not wait_for_service_stop():
            raise ConfigValidationError(
                "auto_start", "service_reload_timeout",
                "Configuration was saved, but the existing replication service did not stop. Runtime settings were not reloaded.",
            )
        return {"running": False, "changed": bool(was_running)}
    was_running = _stop_service(logger_override=action_logger)
    if was_running and not wait_for_service_stop():
        raise ConfigValidationError(
            "auto_start", "service_reload_timeout",
            "Configuration was saved, but the existing replication service did not stop. Runtime settings were not reloaded and export was not run.",
        )
    service = _start_service(effective, action_logger)
    return {"running": True, "changed": True, **service.status()}


def _python_interpreter() -> str:
    candidates = [Path(sys.prefix) / "bin" / "python", Path(sys.executable)]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("No executable Python interpreter is available for the setup assistant")


def _setup_is_healthy(port: int = 9192, timeout: float = 0.75) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _wait_for_setup_health(process: subprocess.Popen, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _setup_is_healthy():
            return True
        # Another worker may have won the file lock and be starting the
        # healthy helper while this worker's candidate exits normally.
        time.sleep(0.1)
    return False


def _start_setup(settings, action_logger, rotate_token: bool = False) -> str:
    global _setup_process
    from apps.plugins.models import PluginConfig

    with _setup_lock:
        record = PluginConfig.objects.get(key="failovarr")
        saved = effective_settings(dict(record.settings or {}))
        token = str(saved.get("setup_access_token", ""))
        if rotate_token or len(token) < 24:
            token = secrets.token_urlsafe(24)
            saved["setup_access_token"] = token
            save_node_config(saved)
            plugin_logger(action_logger).info("Setup link token was rotated")
        public_url = str(saved.get("setup_public_url", "")).strip().rstrip("/") or "http://127.0.0.1:9192"
        url = (
            f"{public_url}?token={token}"
            if public_url.endswith("/setup")
            else f"{public_url}/setup?token={token}"
        )
        if _setup_is_healthy():
            return url
        if _setup_process and _setup_process.poll() is None:
            _setup_process.terminate()
            _setup_process.wait(timeout=3)
        env = os.environ.copy()
        env["FAILOVARR_NO_AUTOSTART"] = "1"
        package_parent = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            package_parent, env.get("PYTHONPATH", ""),
        )))
        _setup_process = subprocess.Popen(
            [_python_interpreter(), "-m", "failovarr.setup_process", "9192"],
            stdin=subprocess.DEVNULL, stdout=None, stderr=None, env=env, close_fds=True,
        )
        if not _wait_for_setup_health(_setup_process):
            code = _setup_process.poll()
            if code is None:
                _setup_process.terminate()
            _setup_process = None
            raise RuntimeError("Setup assistant helper did not become healthy")
        # Every uWSGI worker performs the health check. The helper itself
        # logs its single startup at INFO; this per-worker confirmation is
        # useful only for diagnostics.
        action_logger.debug("Setup assistant helper is healthy")
        return url


def _stop_setup() -> None:
    global _setup_process
    with _setup_lock:
        if _setup_process and _setup_process.poll() is None:
            _setup_process.terminate()
            try:
                _setup_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _setup_process.kill()
        else:
            pid_path = Path(os.environ.get(
                "FAILOVARR_SETUP_PID",
                os.environ.get("DISPATCHARR_REDUNDANCY_SETUP_PID", "/data/failovarr-setup.pid"),
            ))
            try:
                pid = int(pid_path.read_text(encoding="ascii").strip())
                command = Path(f"/proc/{pid}/cmdline").read_bytes()
                if b"failovarr.setup_process" not in command:
                    raise ValueError("Stale setup assistant PID file")
                os.kill(pid, 15)
            except (FileNotFoundError, ProcessLookupError, ValueError, OSError):
                pass
        _setup_process = None


class Plugin:
    name = PLUGIN_CONFIG["name"]
    description = PLUGIN_CONFIG["description"]
    version = PLUGIN_CONFIG["version"]
    author = PLUGIN_CONFIG["author"]
    @property
    def fields(self):
        """Expose the full non-secret configuration in Dispatcharr's native UI."""
        try:
            from apps.plugins.models import PluginConfig
            from core.models import OutputProfile, StreamProfile
            from apps.epg.models import EPGSource
            from apps.m3u.models import M3UAccount

            stored = PluginConfig.objects.get(key=PLUGIN_DB_KEY).settings or {}
            models = {
                "output_profiles": OutputProfile, "stream_profiles": StreamProfile,
                "epg_sources": EPGSource, "m3u_accounts": M3UAccount,
            }
            records = {
                domain: list(model.objects.order_by("id").values("id", "name", "is_active"))
                for domain, model in models.items() if domain in LOCAL_PROTECTION_DOMAINS
            }
            return build_plugin_fields(effective_settings(stored), records)
        except Exception:
            # Discovery must remain robust during migrations and test imports.
            logger.exception("Could not build dynamic native settings fields")
            return build_plugin_fields()

    actions = [
        {
            "id": "start_setup_assistant", "label": "Open setup assistant",
            "description": "Start the guided, mode-aware configuration on port 9192.",
            "button_label": "Open setup", "button_color": "green",
        },
        {
            "id": "validate_config", "label": "Validate configuration",
            "description": "Validate settings without connecting to the peer.",
            "button_label": "Validate", "button_color": "blue",
        },
        {
            "id": "test_storage", "label": "Test storage connection",
            "description": "Authenticate, write, publish, read and remove a temporary test object.",
            "button_label": "Test connection", "button_color": "blue",
        },
        {
            "id": "export_now", "label": "Export configuration",
            "description": "Create a signed bundle on the authoritative node.",
            "button_label": "Export", "button_color": "green",
        },
        {
            "id": "preview_latest", "label": "Preview latest import",
            "description": "Verify and plan changes without writing to the database.",
            "button_label": "Preview import", "button_color": "blue",
        },
        {
            "id": "import_latest", "label": "Import latest configuration",
            "description": "Transactionally apply a newer verified bundle on a follower.",
            "button_label": "Import", "button_color": "red",
        },
        {
            "id": "start_service", "label": "Start background service",
            "description": "Start periodic export or follower import using the saved node configuration.",
            "button_label": "Start", "button_color": "green",
        },
        {
            "id": "stop_service", "label": "Stop background service",
            "description": "Stop scheduled replication on this node without deleting its persisted state.",
            "button_label": "Stop", "button_color": "red",
        },
        {
            "id": "status", "label": "Replication status",
            "description": "Show service health, the persisted sequence and the latest export/import result.",
            "button_label": "Status", "button_color": "blue",
        },
        {
            "id": "acquire_vip", "label": "Acquire client VIP",
            "description": "Refuse duplicates, add the configured VIP and announce it by GARP.",
            "button_label": "Acquire VIP", "button_color": "red",
        },
        {
            "id": "release_vip", "label": "Release client VIP",
            "description": "Mark this node not ready before removing its VIP.",
            "button_label": "Release VIP", "button_color": "red",
        },
        {
            "id": "proxy_ready", "label": "Enable proxy readiness",
            "description": "Allow an externally managed HA proxy to route clients to this authoritative node.",
            "button_label": "Ready", "button_color": "green",
        },
        {
            "id": "proxy_not_ready", "label": "Disable proxy readiness",
            "description": "Make this node unhealthy for an externally managed HA proxy without changing replication data.",
            "button_label": "Not ready", "button_color": "red",
        },
        {
            "id": "handoff_to_peer", "label": "Planned handoff to peer",
            "description": (
                "Queue a two-phase handoff. The active node remains serving until the peer "
                "has applied the signed preparation bundle."
            ),
            "button_label": "Handoff", "button_color": "red",
        },
    ]

    def __init__(self):
        if _legacy_plugin_is_enabled():
            logger.error("Legacy Dispatcharr Redundancy plugin is enabled; Failovarr will remain inactive")
            return
        if os.environ.get(
            "FAILOVARR_NO_AUTOSTART",
            os.environ.get("DISPATCHARR_REDUNDANCY_NO_AUTOSTART"),
        ) != "1":
            attempt_autostart(_start_service)

    def run(self, action: str, params: dict, context: dict):
        # Dispatcharr merges every field default into ``context.settings``.
        # Those defaults are display values, not an operator's persisted edit,
        # so use the raw PluginConfig record whenever the ORM is available.
        try:
            from apps.plugins.models import PluginConfig
            raw_settings = PluginConfig.objects.get(key=PLUGIN_DB_KEY).settings or {}
        except Exception:
            raw_settings = context.get("settings", {})
        settings = effective_settings(raw_settings)
        action_logger = plugin_logger(context.get("logger", logger))
        try:
            _ensure_no_legacy_plugin()
            if action == "start_setup_assistant":
                url = _start_setup(settings, action_logger, rotate_token=True)
                return {
                    "status": "success",
                    "message": f"Setup assistant is ready. Copy this URL into your browser: {url}",
                    "url": url,
                }
            if action == "validate_config":
                config = ReplicationConfig.from_settings(settings)
                return {
                    "status": "success",
                    "message": "Configuration is valid",
                    "node_id": config.node_id,
                    "cluster_id": config.cluster_id,
                    "role": config.role,
                    "mode": config.mode,
                    "domains": list(config.domains),
                }
            if action == "start_service":
                try:
                    service = _start_service(settings, action_logger)
                    return {"status": "success", "message": "Background service started", **service.status()}
                except ServiceAlreadyRunning:
                    action_logger.info("Manual start found an existing container-wide service owner")
                    return {
                        "status": "success",
                        "message": "Background service is already running in this container",
                        "running": True,
                    }
            if action == "stop_service":
                stopped = _stop_service(logger_override=action_logger)
                return {"status": "success", "message": "Background service stopped" if stopped else "Service was not running"}
            if action == "status":
                issues = configuration_issues(settings)
                if issues:
                    message = "Setup incomplete: " + " ".join(str(issue) for issue in issues)
                    action_logger.info("Status pending (fields=%s)", ",".join(issue.field for issue in issues))
                    return {
                        "status": "pending", "code": "setup_incomplete",
                        "missing_fields": [issue.field for issue in issues],
                        "message": message,
                        "service": _service.status() if _service else {"running": service_is_running()},
                    }
                engine = ReplicationEngine(settings, action_logger)
                result = engine.status()
                result["service"] = _service.status() if _service else {"running": service_is_running()}
                state = result.get("state", {})
                last_export = state.get("last_export_at") or "never"
                last_import = state.get("last_import_at") or "never"
                role_label = "Main" if result["role"] == "leader" else "Follower"
                result["message"] = (
                    f"Node {result['node_id']} ({role_label}); replication service "
                    f"{'running' if result['service'].get('running') else 'stopped'}; "
                    f"last export {last_export}; last import {last_import}."
                )
                return result

            if action == "test_storage":
                # This action is intentionally available before the guided
                # setup has a valid cluster contract. It checks only the
                # selected storage backend using an isolated test identity.
                result = test_storage_connection(storage_probe_config(settings))
                result.setdefault("message", "Storage connection verified")
                return result

            engine = ReplicationEngine(settings, action_logger)
            if action == "export_now":
                result = engine.export_now()
                result.setdefault("message", "Signed configuration bundle exported")
                return result
            if action == "preview_latest":
                result = engine.preview_latest()
                result.setdefault("message", "Latest bundle verified; preview contains no database changes")
                return result
            if action in {"import_latest", "apply_latest"}:
                result = engine.apply_latest()
                result.setdefault("message", "Latest verified bundle imported")
                return result
            if action == "acquire_vip":
                result = engine.acquire_client_vip()
                result.setdefault("message", "Client VIP acquired")
                return result
            if action == "release_vip":
                result = engine.release_client_vip()
                result.setdefault("message", "Client VIP released")
                return result
            if action == "proxy_ready":
                result = engine.set_proxy_readiness(True)
                result.setdefault("message", "External proxy readiness enabled")
                return result
            if action == "proxy_not_ready":
                result = engine.set_proxy_readiness(False)
                result.setdefault("message", "External proxy readiness disabled")
                return result
            if action == "handoff_to_peer":
                result = engine.request_handoff()
                result.setdefault("message", "Planned handoff bundle exported")
                return result
            return {"status": "error", "message": f"Unknown action: {action}"}
        except Exception as exc:
            if isinstance(exc, ConfigValidationError):
                action_logger.warning("Action %s failed (field=%s code=%s)", action, exc.field, exc.code)
            else:
                action_logger.exception("Action %s failed", action)
            labels = {
                "validate_config": "Configuration validation failed",
                "test_storage": "Storage test failed",
                "export_now": "Export failed", "preview_latest": "Import preview failed",
                "import_latest": "Import failed", "start_service": "Service start failed",
            }
            return validation_error_payload(exc, operation=labels.get(action, f"{action.replace('_', ' ').capitalize()} failed"))

    def stop(self, context: dict):
        action_logger = plugin_logger(context.get("logger", logger))
        try:
            _stop_service(flush=True, logger_override=action_logger)
        except Exception:
            action_logger.exception("Final shutdown export failed")
        _stop_setup()
        request_service_stop(flush=True)


__version__ = PLUGIN_CONFIG["version"]
__all__ = ["Plugin"]
