"""Token-protected, mode-aware setup and management server on port 9192."""

from __future__ import annotations

import json
import logging
import posixpath
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .cluster_profile import export_cluster_profile, import_cluster_profile
from .engine import BundleNotNewerState
from .config import (
    AUTOMATION_TEXT,
    CORE_SETTING_GROUPS,
    DEFAULT_CORE_SETTING_KEYS,
    DEFAULT_DOMAINS,
    DOMAIN_DEPENDENCIES,
    DOMAIN_DESCRIPTIONS,
    DOMAIN_GROUP_LABELS,
    DOMAIN_GROUPS,
    DOMAIN_LABELS,
    IPTV_CONTENT_DOMAINS,
    LOCAL_PROTECTION_DOMAINS,
    NEW_RECORD_POLICY_FIELDS,
    NEW_RECORD_POLICY_OPTIONS,
    ConfigValidationError,
    DEFAULT_KNOWN_HOSTS_PATH,
    LEGACY_KNOWN_HOSTS_PATH,
    ReplicationConfig,
    normalize_redundancy_mode,
    storage_probe_config,
    validation_error_payload,
)
from .node_config import effective_settings, load_node_config, save_node_config
from .logging_utils import plugin_logger
from .remote_storage import test_storage_connection
from .transport import MAX_BUNDLE_BYTES, request_is_authorized


SECRET_FIELDS = {
    "shared_secret", "storage_password", "s3_session_token", "setup_access_token",
    "sftp_private_key", "sftp_private_key_passphrase",
}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    result = dict(settings)
    for field in SECRET_FIELDS:
        result[field] = ""
        result[f"{field}_is_set"] = bool(settings.get(field))
    result.pop("storage_options", None)
    result.pop("local_overrides", None)
    return result


def _merge_secret_fields(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    result = dict(incoming)
    for field in SECRET_FIELDS:
        if not result.get(field) and existing.get(field):
            result[field] = existing[field]
    return result


class SetupServer:
    def __init__(
        self, settings: dict[str, Any], logger: logging.Logger,
        port: int = 9192, token: str = "",
    ):
        self.settings = dict(settings)
        self.logger = plugin_logger(logger)
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def current_token(self) -> str:
        """Return the node-local token shared by all uWSGI workers."""
        try:
            configured = load_node_config(self.settings)
        except (OSError, ValueError, json.JSONDecodeError):
            self.logger.exception("Could not read the current setup token")
            return self.token
        token = str(configured.get("setup_access_token", ""))
        return token if len(token) >= 24 else self.token

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DispatcharrRedundancy/1"

            def log_message(self, fmt, *args):
                owner.logger.debug("HTTP: " + fmt, *args)

            def _path(self) -> str:
                return urlsplit(self.path).path.rstrip("/") or "/"

            def _setup_authorized(self) -> bool:
                return secrets.compare_digest(
                    parse_qs(urlsplit(self.path).query).get("token", [""])[0],
                    owner.current_token(),
                )

            def _peer_authorized(self, path: str) -> bool:
                secret = str(owner._database_settings().get("shared_secret", ""))
                return bool(secret) and request_is_authorized(
                    self.headers, secret, "GET", path,
                )

            def _send(
                self, status: int, value: Any, content_type: str = "application/json",
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                if isinstance(value, bytes):
                    payload = value
                elif isinstance(value, str):
                    payload = value.encode("utf-8")
                else:
                    payload = _json_bytes(value)
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'")
                for name, header_value in (extra_headers or {}).items():
                    self.send_header(name, header_value)
                self.end_headers()
                self.wfile.write(payload)

            def _body(self) -> dict[str, Any]:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 1_000_000:
                    raise ValueError("Request is too large")
                value = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(value, dict):
                    raise ValueError("Request body must be an object")
                return value

            def _management_get(self, path: str) -> bool:
                if path == "/v1/health":
                    self._send(200, {"status": "ok"})
                    return True
                if path == "/v1/readiness":
                    ready = owner.is_client_ready()
                    self._send(200 if ready else 503, {"ready": ready})
                    return True
                if path not in {"/v1/status", "/v1/latest"}:
                    return False
                if not self._peer_authorized(path):
                    self._send(401, {"error": "unauthorized"})
                    return True
                if path == "/v1/status":
                    self._send(200, owner.peer_status())
                else:
                    envelope = owner.latest_for_http()
                    if envelope is None:
                        self._send(404, {"error": "no_bundle"})
                    else:
                        payload = _json_bytes(envelope)
                        if len(payload) > MAX_BUNDLE_BYTES:
                            raise ValueError("Peer bundle exceeds the maximum allowed size")
                        self._send(200, payload)
                return True

            def do_GET(self):
                path = self._path()
                try:
                    if self._management_get(path):
                        return
                    if not self._setup_authorized():
                        self._send(403, INVALID_TOKEN_HTML, "text/html")
                        return
                    if path in {"/", "/setup"}:
                        self._send(200, SETUP_HTML, "text/html")
                    elif path == "/api/config":
                        self._send(200, owner.get_bootstrap())
                    elif path == "/api/profile":
                        query = parse_qs(urlsplit(self.path).query)
                        include_passwords = query.get("include_passwords", ["false"])[0].lower() == "true"
                        profile = owner.export_profile(include_passwords)
                        self._send(200, profile, extra_headers={
                            "Content-Disposition": (
                                "attachment; filename=failovarr-profile.json"
                            ),
                        })
                    else:
                        self._send(404, {"status": "error", "message": "Not found"})
                except Exception as exc:
                    owner.logger.exception("HTTP GET failed")
                    self._send(500, validation_error_payload(exc, operation="Request failed"))

            def do_POST(self):
                if not self._setup_authorized():
                    self._send(403, {"status": "error", "message": "Invalid setup token"})
                    return
                path = self._path()
                try:
                    body = self._body()
                    handlers = {
                        "/api/config": owner.save,
                        "/api/test-storage": owner.test_storage,
                        "/api/profile/import": owner.import_profile,
                        "/api/bundle-info": owner.bundle_info,
                        "/api/export": lambda _body: owner.export_now(),
                        "/api/sftp/host-key": owner.inspect_sftp_host_key,
                        "/api/sftp/trust-host-key": owner.trust_sftp_host_key,
                        "/api/sftp/private-key": owner.import_sftp_private_key,
                        "/api/status": lambda _body: owner.replication_status(),
                        "/api/preview": lambda _body: owner.preview_latest(),
                        "/api/import": lambda _body: owner.import_latest(),
                        "/api/initialize": owner.initialize_follower,
                    }
                    handler = handlers.get(path)
                    if handler is None:
                        self._send(404, {"status": "error", "message": "Not found"})
                    else:
                        self._send(200, handler(body))
                except Exception as exc:
                    if isinstance(exc, ConfigValidationError):
                        owner.logger.warning("Setup assistant POST failed (field=%s code=%s)", exc.field, exc.code)
                    else:
                        owner.logger.exception("Setup assistant POST failed")
                    self._send(400, validation_error_payload(exc, operation="Request failed"))

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True, name="redundancy-setup-http",
        )
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.server = None
        self.thread = None

    def _database_settings(self) -> dict[str, Any]:
        # The guided setup file is the canonical complete configuration.  In
        # particular it contains secrets which the native Plugin Settings
        # mirror deliberately omits.  Reading that file also keeps requests
        # handled by ThreadingHTTPServer out of Django's gevent-managed ORM
        # connection path. The Assistant receives a startup snapshot from the
        # process main thread for the short first-setup interval before the
        # node-local configuration exists.
        configured = load_node_config()
        if configured:
            return effective_settings({})
        return effective_settings(self.settings)

    def _sync_native_settings(self) -> bool:
        """Sync native settings outside the Assistant HTTP thread.

        The child reads the already atomically saved node-local configuration;
        no configuration values, especially no secrets, are passed on argv or
        emitted from the subprocess.
        """
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "failovarr.native_settings_sync"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                close_fds=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.logger.warning("Native Settings sync was not completed (%s)", type(exc).__name__)
            return False
        if completed.returncode != 0:
            self.logger.warning("Native Settings sync exited with status %s", completed.returncode)
            return False
        return True

    def _engine(self, settings: dict[str, Any] | None = None):
        from .engine import ReplicationEngine

        return ReplicationEngine(settings or self._database_settings(), self.logger)

    def latest_for_http(self) -> dict[str, Any] | None:
        return self._engine().latest_for_http()

    def peer_status(self) -> dict[str, Any]:
        return self._engine().peer_status()

    def is_client_ready(self) -> bool:
        try:
            return self._engine().is_client_ready()
        except Exception as exc:
            # Readiness must always fail closed. Do not surface an internal
            # exception as HTTP 500 to a HA proxy, and do not log arbitrary
            # exception text because configuration values may be sensitive.
            self.logger.warning(
                "Readiness check failed (%s); reporting this node as not ready",
                type(exc).__name__,
            )
            return False

    def get_bootstrap(self) -> dict[str, Any]:
        from core.models import OutputProfile, StreamProfile
        from apps.epg.models import EPGSource
        from apps.m3u.models import M3UAccount

        current = self._database_settings()
        protection_models = {
            "output_profiles": OutputProfile,
            "stream_profiles": StreamProfile,
            "epg_sources": EPGSource,
            "m3u_accounts": M3UAccount,
        }
        protected_records = {
            domain: list(model.objects.order_by("id").values("id", "name", "is_active"))
            for domain, model in protection_models.items()
        }
        bundle_info = self._bundle_info(current)
        try:
            replication_status = self.replication_status()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.logger.debug("Replication status is not available before setup: %s", type(exc).__name__)
            replication_status = {
                "status": "pending",
                "message": "Save a valid configuration to show replication status.",
            }
        return {
            "settings": _public_settings(current),
            # Compatibility with the first guided setup UI. New UI uses the
            # generic protection_records collection below.
            "profiles": protected_records["output_profiles"],
            "protection_records": protected_records,
            "local_protection_domains": LOCAL_PROTECTION_DOMAINS,
            "new_record_policy_fields": NEW_RECORD_POLICY_FIELDS,
            "new_record_policy_options": [
                {"value": value, "label": label}
                for value, label in NEW_RECORD_POLICY_OPTIONS
            ],
            "automation_text": AUTOMATION_TEXT,
            "bundle_info": bundle_info,
            "replication_status": replication_status,
            "domain_groups": DOMAIN_GROUPS,
            "domain_group_labels": DOMAIN_GROUP_LABELS,
            "domain_labels": DOMAIN_LABELS,
            "domain_dependencies": {
                key: sorted(value) for key, value in DOMAIN_DEPENDENCIES.items()
            },
            "domain_descriptions": DOMAIN_DESCRIPTIONS,
            "default_domains": DEFAULT_DOMAINS.split(","),
            "iptv_content_domains": list(IPTV_CONTENT_DOMAINS),
            "core_setting_groups": CORE_SETTING_GROUPS,
        }

    def _bundle_info(self, current: dict[str, Any]) -> dict[str, Any]:
        """Read only safe availability metadata; never log expected wait states."""
        if str(current.get("role", "follower")).lower() != "follower":
            return {"status": "not_applicable"}
        try:
            return self._engine(current).bundle_info()
        except (ConfigValidationError, ValueError):
            return {
                "status": "configuration_incomplete",
                "message": "Save or import the Follower configuration before checking Main bundles.",
            }

    def bundle_info(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inspect the currently entered transport without persisting it."""
        return self._bundle_info(self._normalized(body or self._database_settings()))

    def _normalized(self, incoming: dict[str, Any]) -> dict[str, Any]:
        current = self._database_settings()
        result = normalize_redundancy_mode(_merge_secret_fields(incoming, current))
        result.setdefault("setup_public_url", current.get("setup_public_url", ""))
        result["domains"] = (
            ",".join(result.get("domains", []))
            if isinstance(result.get("domains"), list)
            else result.get("domains", current.get("domains", DEFAULT_DOMAINS))
        )
        result["core_setting_keys"] = (
            ",".join(result.get("core_setting_keys", []))
            if isinstance(result.get("core_setting_keys"), list)
            # A fresh Follower has no signed Main bundle yet, but the
            # transport configuration must still validate before it can save
            # the imported Main profile. This temporary safe default is
            # replaced by the signed bundle scope on first import.
            else result.get("core_setting_keys", current.get(
                "core_setting_keys", ",".join(DEFAULT_CORE_SETTING_KEYS),
            ))
        )
        protected = result.get(
            "protected_output_profile_ids",
            current.get("protected_output_profile_ids", []),
        )
        result["protected_output_profile_ids"] = (
            ",".join(str(value) for value in protected)
            if isinstance(protected, list) else protected
        )
        protected_records = result.get("protected_records", current.get("protected_records", {}))
        if not isinstance(protected_records, dict):
            # The browser submits a JSON-compatible object. Keep legacy
            # string data intact here so ReplicationConfig can explain it.
            protected_records = current.get("protected_records", {})
        legacy_outputs = result["protected_output_profile_ids"]
        if legacy_outputs:
            protected_records = dict(protected_records)
            protected_records["output_profiles"] = [
                int(value) for value in str(legacy_outputs).split(",") if value.strip()
            ]
        result["protected_records"] = protected_records
        defaults = {
            "state_path": "/data/failovarr-state",
            "shared_path": "/data/redundancy",
            "storage_timeout_seconds": 20,
            "sftp_known_hosts_path": DEFAULT_KNOWN_HOSTS_PATH,
            "s3_region": "us-east-1",
            "s3_addressing_style": "path",
            "s3_prefix": "failovarr",
            "new_output_profile_policy": "disabled",
            "new_stream_profile_policy": "disabled",
            "new_epg_source_policy": "disabled",
            "new_m3u_account_policy": "disabled",
            "allow_deletes": False,
            "automatic_apply": False,
            "deployment_mode": "online",
            "import_on_start": False,
            "bundle_retention": 3,
            "interval_seconds": 60,
            "client_identity_users": "*",
        }
        for key, value in defaults.items():
            result.setdefault(key, value)
        # v0.6 used a root-owned helper directory in the local lab. Preserve
        # explicit custom paths, but move the former implicit default to the
        # node-local state directory where the Dispatcharr user can write.
        if result.get("sftp_known_hosts_path") == LEGACY_KNOWN_HOSTS_PATH:
            result["sftp_known_hosts_path"] = DEFAULT_KNOWN_HOSTS_PATH
        result["bind_host"] = "0.0.0.0"
        result["bind_port"] = 9192
        result.setdefault("local_overrides", current.get("local_overrides", "{}"))
        result.setdefault("setup_access_token", current.get("setup_access_token", self.current_token()))
        return normalize_redundancy_mode(result)

    def save(self, incoming: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalized(incoming)
        config = ReplicationConfig.from_settings(normalized)
        current = self._database_settings()
        preserved = {
            key: current[key]
            for key in ("storage_options", "local_overrides")
            if key in current and key not in normalized
        }
        saved = {**preserved, **normalized}
        save_node_config(saved)
        native_settings_synced = self._sync_native_settings()
        self.settings = saved
        # Keep service lifecycle in sync with the persisted node configuration.
        # Import locally to avoid a Plugin -> SetupServer import cycle.
        from . import reconcile_service
        service = reconcile_service(saved, self.logger)
        warning = (
            "Configuration is active, but native Plugin Settings could not be mirrored. "
            "Save again to retry the mirror."
        )
        return {
            "status": "success",
            "message": "Configuration saved and validated." if native_settings_synced else warning,
            "node_id": config.node_id,
            "role": config.role,
            "mode": config.mode,
            "service": service,
            "native_settings_synced": native_settings_synced,
            "warnings": [] if native_settings_synced else [warning],
        }

    def test_storage(self, incoming: dict[str, Any]) -> dict[str, Any]:
        # Connection checks must work before the cluster identity and secret
        # are chosen. Construct a deliberately isolated validation contract:
        # it uses exactly the submitted storage fields but cannot publish a
        # real replication bundle.
        return test_storage_connection(storage_probe_config(self._normalized(incoming)))

    def replication_status(self) -> dict[str, Any]:
        from .config import configuration_issues

        issues = configuration_issues(self._database_settings())
        if issues:
            return {
                "status": "pending", "code": "setup_incomplete",
                "missing_fields": [issue.field for issue in issues],
                "message": "Setup incomplete: " + " ".join(str(issue) for issue in issues),
            }
        engine = self._engine()
        result = engine.status()
        state = result.get("state", {})
        from .autostart import service_is_running
        running = service_is_running()
        role_label = "Main" if result["role"] == "leader" else "Follower"
        result.update({
            "status": "success",
            "service_running": running,
            "last_export_at": state.get("last_export_at"),
            "last_import_at": state.get("last_import_at"),
            "message": (
                f"Node {result['node_id']} ({role_label}); service "
                f"{'running' if running else 'stopped'}; last export "
                f"{state.get('last_export_at') or 'never'}; last import "
                f"{state.get('last_import_at') or 'never'}."
            ),
        })
        return result

    def inspect_sftp_host_key(self, _body: dict[str, Any]) -> dict[str, Any]:
        from .remote_storage import inspect_sftp_host_key

        return inspect_sftp_host_key(storage_probe_config(self._normalized(_body)))

    def trust_sftp_host_key(self, body: dict[str, Any]) -> dict[str, Any]:
        host_key = str(body.get("host_key", "")).strip()
        if len(host_key) > 10_000 or not host_key.startswith("ssh-") or "\n" in host_key:
            raise ValueError("SFTP host key must be one single OpenSSH public-key line")
        # The form is deliberately used here rather than the persisted full
        # cluster configuration: trusting a storage host key is part of first
        # setup and must work before node/cluster/secret exist.
        config = storage_probe_config(self._normalized(body))
        if config.storage_backend != "sftp":
            raise ValueError("Select SFTP storage before trusting an SFTP host key")
        parsed = urlsplit(config.storage_endpoint)
        key_path = str(config.storage_options["known_hosts_path"])
        import os
        from pathlib import Path

        target = Path(key_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigValidationError(
                "sftp_known_hosts_path", "not_writable",
                f"SFTP host-key directory is not writable: {target.parent} ({exc.strerror or exc})",
            ) from exc
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        hostname = parsed.hostname or ""
        port = parsed.port or 22
        known_hosts_host = hostname if port == 22 else f"[{hostname}]:{port}"
        try:
            temporary.write_text(f"{known_hosts_host} {host_key}\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConfigValidationError(
                "sftp_known_hosts_path", "not_writable",
                f"SFTP host key cannot be saved at {target}. The directory must be writable by the Dispatcharr container user ({exc.strerror or exc}).",
            ) from exc
        self.logger.info("SFTP host key was trusted for %s", known_hosts_host)
        return {"status": "success", "message": "SFTP host key saved with strict verification enabled"}

    def import_sftp_private_key(self, body: dict[str, Any]) -> dict[str, Any]:
        private_key = str(body.get("private_key", ""))
        if not private_key.startswith("-----BEGIN ") or len(private_key) > 100_000:
            raise ValueError("Select a valid PEM or OpenSSH private-key file")
        current = self._database_settings()
        saved = self._normalized(current)
        saved["sftp_private_key"] = private_key
        if body.get("passphrase"):
            saved["sftp_private_key_passphrase"] = str(body["passphrase"])
        save_node_config(saved)
        self.settings = saved
        self.logger.info("SFTP client private key was imported into node-local configuration")
        return {"status": "success", "message": "SFTP client key imported. It is stored only on this node."}

    def export_profile(self, include_passwords: bool) -> dict[str, Any]:
        current = self._database_settings()
        config = ReplicationConfig.from_settings(current)
        if config.role != "leader":
            raise ValueError("Configuration profiles can only be downloaded from the Main node")
        return export_cluster_profile(current, include_passwords)

    def export_now(self) -> dict[str, Any]:
        result = self._engine().export_now()
        result.setdefault("message", "Signed configuration bundle exported")
        return result

    def import_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        # New assistant clients submit the profile plus their unsaved local
        # node name. This removes the impossible requirement to save a
        # follower before its shared secret/storage settings have arrived.
        profile = body.get("profile", body)
        if not isinstance(profile, dict):
            raise ValueError("Configuration profile must be an object")
        current = self._database_settings()
        local_settings = body.get("local_settings", {})
        if not isinstance(local_settings, dict):
            raise ValueError("Local follower settings must be an object")
        local = self._normalized(local_settings)
        if str(local.get("role", "follower")).lower() != "follower":
            raise ValueError("Configuration profiles can only be imported on a follower")
        imported = import_cluster_profile(local, profile)
        normalized = self._normalized(imported)
        config = ReplicationConfig.from_settings(normalized)
        save_node_config(normalized)
        native_settings_synced = self._sync_native_settings()
        self.settings = normalized
        from . import reconcile_service
        service = reconcile_service(normalized, self.logger)
        warning = (
            "Main configuration is active, but native Plugin Settings could not be mirrored. "
            "Import or save again to retry the mirror."
        )
        return {
            "status": "success",
            "message": (
                "Main configuration imported. Node-local settings were preserved."
                if native_settings_synced else warning
            ),
            "node_id": config.node_id,
            "mode": config.mode,
            "storage_passwords_imported": bool(profile.get("includes_storage_passwords")),
            "service": service,
            "native_settings_synced": native_settings_synced,
            "warnings": [] if native_settings_synced else [warning],
        }

    def preview_latest(self) -> dict[str, Any]:
        return self._engine().preview_latest()

    def import_latest(self) -> dict[str, Any]:
        try:
            return self._engine().apply_latest()
        except BundleNotNewerState:
            # A status refresh and an import click can race with a completed
            # background apply. This is an expected no-op, not an Assistant
            # request failure and must not produce an HTTP-400 traceback.
            return {
                "status": "waiting",
                "reason": "not_newer",
                "message": "Already up to date. No newer Main bundle is available to import.",
            }

    def initialize_follower(self, body: dict[str, Any]) -> dict[str, Any]:
        config = ReplicationConfig.from_settings(self._database_settings())
        expected = f"INITIALIZE {config.node_id}"
        if not secrets.compare_digest(str(body.get("confirmation", "")), expected):
            raise ValueError(f"Confirmation must exactly match: {expected}")
        return self._engine().initialize_follower()

    def url(self, public_url: str = "") -> str:
        base = public_url.strip().rstrip("/") or f"http://127.0.0.1:{self.port}"
        if base.endswith("/setup"):
            return f"{base}?token={self.current_token()}"
        return f"{base}/setup?token={self.current_token()}"


INVALID_TOKEN_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Setup link expired</title><style>body{font-family:system-ui,sans-serif;background:#18191a;color:#f1f3f5;display:grid;place-items:center;min-height:100vh;margin:0}.card{max-width:560px;background:#25262b;border:1px solid #373a40;border-radius:8px;padding:28px}h1{margin-top:0}p{line-height:1.5;color:#c1c2c5}</style></head><body><main class="card"><h1>Setup link expired</h1><p>In Dispatcharr, run <b>Plugins → Failovarr → Actions → Open setup</b> again. Old links intentionally stop working after the node token changes.</p></main></body></html>'''


SETUP_HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Failovarr Setup</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#f1f3f5;background:#18191a;color-scheme:dark;--panel:#25262b;--border:#373a40;--muted:#a6a7ab;--blue:#228be6;--green:#40c057;--red:#fa5252;--yellow:#fab005}*{box-sizing:border-box}body{margin:0;background:#18191a}.top{height:58px;background:#141517;border-bottom:1px solid #2c2e33;display:flex;align-items:center;padding:0 24px;position:sticky;top:0;z-index:3}.brand{font-weight:700;font-size:18px}.wrap{max-width:1040px;margin:auto;padding:28px 20px 80px}h1{font-size:28px;margin:0 0 6px}h2{font-size:18px;margin:0 0 6px}p{line-height:1.5}.muted,small{color:var(--muted)}.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px;margin:16px 0;box-shadow:0 1px 2px #0004}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:14px;margin-top:16px}label{display:flex;flex-direction:column;gap:6px;font-size:14px;font-weight:500}input,select{width:100%;background:#1a1b1e;color:#f1f3f5;border:1px solid #4a4d52;border-radius:6px;padding:10px 12px;font:inherit}input:focus,select:focus{outline:2px solid #228be655;border-color:var(--blue)}input[type=checkbox],input[type=radio]{width:auto;accent-color:var(--blue)}.check{flex-direction:row;align-items:flex-start;font-weight:400}.choice{cursor:pointer;margin:0}.choice.card{padding:14px}.choice:has(input:checked){border-color:var(--blue);background:#1c3044}button{border:0;border-radius:6px;padding:10px 16px;background:var(--blue);color:white;font-weight:600;cursor:pointer}button:hover{filter:brightness(1.08)}button:disabled{opacity:.55;cursor:wait}.secondary{background:#495057}.danger{background:#e03131}.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:16px}.result{white-space:pre-wrap;padding:10px 12px;border-radius:6px;background:#1a1b1e;border:1px solid var(--border);margin-top:10px;font:13px/1.5 ui-monospace,SFMono-Regular,monospace}.result.ok{border-color:#2b8a3e;color:#8ce99a}.result.warn{border-color:#f08c00;color:#ffd43b}.result.error{border-color:#c92a2a;color:#ffa8a8}.hidden{display:none!important}.badge{display:inline-block;font-size:11px;border-radius:99px;background:#1971c2;padding:2px 8px}.warn{color:#ffd43b}.dangerbox{border-left:4px solid var(--red);background:#321f22;padding:12px 14px;border-radius:4px;margin-top:14px}fieldset{border:0;padding:0;margin:16px 0}legend{font-weight:650;margin-bottom:10px}.domain-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px}.domain{padding:10px;border:1px solid var(--border);border-radius:6px}.profile{display:grid;grid-template-columns:auto 80px 1fr;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--border);font-weight:400}.section-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.rolebadge{background:#343a40;border-radius:99px;padding:4px 10px;font-size:12px}.summary{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.summary span{background:#343a40;padding:5px 9px;border-radius:99px;font-size:12px}details{margin-top:14px}summary{cursor:pointer;font-weight:600}.footer-save{position:sticky;bottom:0;background:#18191aee;border-top:1px solid #2c2e33;padding:14px 0;backdrop-filter:blur(6px)}@media(max-width:640px){.top{padding:0 14px}.wrap{padding:20px 12px 70px}.card{padding:16px}.section-head{display:block}}
.notice{margin-top:14px;padding:11px 13px;border-radius:6px;border:1px solid var(--border);background:#1a1b1e;line-height:1.45}.notice.ok{border-color:#2b8a3e;color:#8ce99a}.notice.warn{border-color:#f08c00;color:#ffd43b}.notice.error{border-color:#c92a2a;color:#ffa8a8}.status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin-top:10px}.metric{background:#1a1b1e;border:1px solid var(--border);border-radius:6px;padding:10px}.metric b{display:block;font-size:18px}.metric small{display:block}.technical{margin-top:10px}.technical dl{display:grid;grid-template-columns:minmax(120px,1fr) 2fr;gap:7px 12px;margin:8px 0 0;color:var(--muted);font-size:12px}.technical dt{font-weight:650;color:#ced4da}.technical dd{margin:0;word-break:break-word}</style></head><body><header class="top"><span class="brand">Failovarr</span></header><main class="wrap">
<h1>Setup</h1><p class="muted">Only settings relevant to this node and deployment mode are shown.</p>
<form id="form">
<section class="card"><h2>Node role</h2><label>Node role<select name="role"><option value="leader">Main — authoritative source</option><option value="follower">Follower — imports from Main</option></select></label></section>
<section class="card"><div class="section-head"><div><h2>Node and deployment</h2><p class="muted">Give each running node its own identity. Cold Standby may reuse one client IP because only one container runs.</p></div><span id="roleBadge" class="rolebadge">Not configured</span></div><div class="grid">
<label>Node name<input name="node_id" required placeholder="main or slave"></label><label>Cluster name<input name="cluster_id" required placeholder="dispatcharr-home"></label>
<label>Redundancy mode<select name="redundancy_mode"><option value="cold_standby">Cold Standby — one node running</option><option value="plugin_vip">Online — Plugin-managed Linux VIP</option><option value="external_proxy">Online — external HA reverse proxy</option></select></label></div>
<div id="vip" class="grid hidden"><label>Client VIP<input name="client_vip" placeholder="192.168.178.210"></label><label>Network interface<input name="vip_interface" value="eth0"></label><label>Prefix length<input name="vip_prefix_length" type="number" value="24"></label></div></section>

<section id="followerImportProfile" class="card follower-only hidden"><h2>Import Main configuration</h2><p class="muted">Import the shared Main profile before completing this Follower. Cluster secret and storage fields come from Main; node name, automatic start, import selection, local records and overrides remain local.</p><label>Configuration file<input id="profileFile" type="file" accept="application/json,.json"></label><div class="actions"><button type="button" id="importProfile">Import Main configuration</button></div><div id="profileResult" class="result hidden"></div></section>

<section class="card"><h2>Replication transport and storage</h2><p class="muted">Main publishes signed bundles; the follower pulls and verifies them.</p><div class="grid"><label>Transport<select name="mode"><option value="shared_storage">Shared storage / Cold Standby</option><option value="direct">Direct pull from Main</option><option value="hybrid">Direct pull with storage fallback</option></select></label><label class="peer">Peer URL<input name="peer_url" placeholder="http://main-host:9192"></label><label class="peer">Peer node name<input name="peer_node_id" placeholder="main or slave"></label><label>Shared cluster secret<input name="shared_secret" type="password" autocomplete="new-password" placeholder="Leave empty to keep the existing secret"></label></div>
<div id="storage"><div class="grid"><label>Storage backend<select name="storage_backend"><option value="filesystem">Bind mount / mounted network path</option><option value="sftp">SFTP</option><option value="webdav">WebDAV</option><option value="s3">S3 / MinIO</option><option value="smb">SMB 3</option></select></label><label id="sharedPath">Path inside the container<input name="shared_path" value="/data/redundancy"></label><label class="remote">Server address<input name="storage_endpoint" placeholder="sftp://storage:22"></label><label class="remote">Bucket, share or directory<input name="storage_container"></label><label class="remote">Username / Access Key<input name="storage_username"></label><label class="remote">Password / Secret Key<input name="storage_password" type="password" autocomplete="new-password" placeholder="Leave empty to keep the existing password"></label></div>
<div class="sftp hidden"><p class="muted">SFTP always verifies the server key. Fetch it, compare its fingerprint, then explicitly trust it. A client private key is optional and remains local.</p><div class="actions"><button type="button" id="inspectSftpKey">Fetch server key</button><button type="button" id="trustSftpKey" disabled>Trust fetched key</button></div><div id="sftpKeyResult" class="result hidden"></div><label>Optional SFTP client private-key file<input id="sftpPrivateKeyFile" type="file" accept=".pem,.key,application/octet-stream,text/plain"></label><label>Private-key passphrase (if required)<input id="sftpPrivateKeyPassphrase" type="password" autocomplete="new-password"></label><div class="actions"><button type="button" id="importSftpPrivateKey">Import client key</button></div><div id="sftpPrivateKeyResult" class="result hidden"></div></div>
<details><summary>Advanced storage settings</summary><div class="grid"><label>Timeout in seconds<input name="storage_timeout_seconds" type="number" value="20"></label><label class="sftp">known_hosts file<input name="sftp_known_hosts_path" value="/data/failovarr-state/known_hosts"></label><label class="s3">Region<input name="s3_region" value="us-east-1"></label><label class="s3">Object prefix<input name="s3_prefix" value="failovarr"></label><label class="smb">SMB domain<input name="smb_domain"></label><label class="tls">Custom CA file<input name="storage_ca_path" placeholder="/data/certs/ca.pem"></label><label class="check http"><input name="storage_allow_insecure_http" type="checkbox">Allow plain HTTP in a protected test lab</label></div></details>
<div class="actions"><button type="button" id="testStorage">Test connection</button></div><div id="storageResult" class="result hidden"></div></div></section>

<section id="followerScope" class="card follower-only hidden"><h2>Data imported by this Follower</h2><p class="muted">Choose which verified Main areas this Follower applies. Dependencies are added automatically.</p><div class="grid"><label class="choice card"><span><input type="radio" name="replication_scope" value="full" checked> Complete IPTV setup <span class="badge">Recommended</span></span><small>M3U Accounts, EPG, Channels, Groups, Profiles and selected Settings.</small></label><label class="choice card"><span><input type="radio" name="replication_scope" value="basic"> Profiles and Settings only</span><small>No provider or Channel lists.</small></label><label class="choice card"><span><input type="radio" name="replication_scope" value="iptv_content"> M3U, EPG and Channels only</span><small>Provider, guide and complete channel graph without Core or Output Settings.</small></label><label class="choice card"><span><input type="radio" name="replication_scope" value="custom"> Custom selection</span><small>Select individual Dispatcharr areas; dependencies are added automatically.</small></label></div><div id="domains" class="hidden"></div><fieldset id="coreSettings"><legend>Settings groups</legend><div id="core" class="domain-grid"></div></fieldset><label class="check"><input name="allow_deletes" type="checkbox">Allow replicated deletions <span class="warn">(off by default)</span></label><label id="mirrorChannelStreamsControl" class="check"><input name="mirror_channel_stream_assignments" type="checkbox">Mirror Main stream assignments exactly <span class="warn">(removes follower-only assignments)</span></label><div id="bundleInfo" class="notice hidden"></div><div class="actions"><button type="button" id="refreshBundleInfo" class="secondary">Refresh latest bundle</button></div><div id="scopeSummary" class="summary"></div></section>

<section id="recordProtection" class="card follower-only hidden"><h2>Records kept local on this follower</h2><p class="muted">Checked complete records are never overwritten or deleted. Their IDs stay stable. Each area also defines how records first seen in a Main bundle are handled.</p><div id="protectedRecords"></div></section>

<section class="card"><h2 id="automationHeading">Automatic replication</h2><div id="coldAutomationNotice" class="dangerbox hidden"></div><div id="automationControls" class="grid"><label class="check"><input name="auto_start" type="checkbox"><span><b id="autoStartLabel"></b><br><small id="autoStartDescription"></small></span></label><label id="importOnStartControl" class="check follower-only"><input name="import_on_start" type="checkbox"><span><b id="importOnStartLabel"></b><br><small id="importOnStartDescription"></small></span></label><label id="automaticApplyControl" class="check follower-only"><input name="automatic_apply" type="checkbox"><span><b id="automaticApplyLabel"></b><br><small id="automaticApplyDescription"></small></span></label></div><details><summary>Advanced node settings</summary><div class="grid"><label>Local state path<input name="state_path" value="/data/failovarr-state"><small>Stores sequences, hashes, replay/apply state and service state on this node. Keep it on persistent local storage below /data, never only on shared storage.</small></label><label><span id="intervalLabel">Replication interval (seconds)</span><input name="interval_seconds" type="number" value="60"><small id="intervalDescription"></small></span></label><label>Client identity users<input name="client_identity_users" value="*"><small>Use * to protect all M3U/EPG API identities.</small></label></div></details></section>

<section class="card leader-only"><h2>Copy shared configuration</h2><p class="muted">Save and download one profile for the Follower. The cluster secret is included because both nodes need it.</p><label class="check"><input id="includePasswords" type="checkbox">Include storage passwords <span class="warn">— the JSON file will contain them in plain text</span></label><div class="actions"><button type="button" id="downloadProfile">Save and download Main configuration</button></div><div id="downloadResult" class="result hidden"></div></section>

<section class="card leader-only"><h2>Replication status</h2><p class="muted">Export publishes the current signed configuration bundle for the follower.</p><div class="actions"><button type="button" id="refreshStatusLeader">Refresh replication status</button><button type="button" id="exportNow">Save and export now</button></div><div id="leaderStatus" class="result hidden"></div></section>

<section class="card follower-only hidden"><h2>Import data from Main</h2><p class="muted">Preview is always read-only. Normal import never applies partial changes.</p><div class="actions"><button type="button" id="refreshStatus">Refresh replication status</button><button type="button" id="preview">Preview import</button><button type="button" id="importLatest">Import latest bundle</button></div><div id="replicationStatus" class="result hidden"></div><div id="importResult" class="result hidden"></div><div class="dangerbox"><b>First-time follower initialization</b><p>If Main and follower were created independently, identical objects can already have different database IDs. Initialization first creates a native Dispatcharr full backup, then transactionally replaces only the selected replication graph with Main IDs. Protected records remain local.</p><button type="button" id="initialize" class="danger">Initialize follower from Main</button><div id="initializeResult" class="result hidden"></div></div></section>

<div class="footer-save"><div class="actions"><button type="submit">Save and validate configuration</button></div><div id="saveResult" class="result hidden"></div></div></form></main>
<script>
const token=new URLSearchParams(location.search).get('token')||'',api=(p,extra='')=>p+'?token='+encodeURIComponent(token)+extra,form=document.querySelector('#form');let meta,customRoots=new Set();
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function detailValue(value){if(Array.isArray(value))return value.map(item=>detailValue(item)).join('; ');if(value&&typeof value==='object')return Object.entries(value).map(([key,item])=>`${esc(key)}: ${detailValue(item)}`).join(' · ');return esc(String(value??''))}
function technical(label,value){if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length)||(typeof value==='object'&&!Array.isArray(value)&&!Object.keys(value).length))return '';const rows=Object.entries(value).filter(([,item])=>item!==undefined&&item!==null&&item!==''&&(!Array.isArray(item)||item.length)&&!(typeof item==='object'&&!Array.isArray(item)&&!Object.keys(item).length)).map(([key,item])=>`<dt>${esc(key.replaceAll('_',' '))}</dt><dd>${detailValue(item)}</dd>`).join('');return rows?`<details class="technical"><summary>${esc(label)}</summary><dl>${rows}</dl></details>`:''}
function metrics(summary){if(!summary)return '';return `<div class="status-grid">${['create','update','delete','conflicts'].map(k=>`<div class="metric"><b>${Number(summary[k]||0)}</b><small>${esc(k[0].toUpperCase()+k.slice(1))}</small></div>`).join('')}</div>`}
function renderResult(value){if(typeof value==='string')return esc(value);const x=value||{},message=x.message||({preview:'Preview ready',applied:'Import completed',initialized:'Follower initialization completed',exported:'Bundle exported',waiting:'Already up to date',success:'Completed'}[x.status]||'Completed');let html=`<b>${esc(message)}</b>`;if(x.role||x.service_running!==undefined||x.last_export_at!==undefined||x.last_import_at!==undefined){html+=`<div class="status-grid">${x.role?`<div class="metric"><b>${esc(x.role==='leader'?'Main':'Follower')}</b><small>Node role</small></div>`:''}${x.service_running!==undefined?`<div class="metric"><b>${x.service_running?'Running':'Stopped'}</b><small>Replication service</small></div>`:''}${x.last_export_at!==undefined?`<div class="metric"><b>${esc(x.last_export_at||'Never')}</b><small>Last export</small></div>`:''}${x.last_import_at!==undefined?`<div class="metric"><b>${esc(x.last_import_at||'Never')}</b><small>Last import</small></div>`:''}</div>`}html+=metrics(x.summary);const details={sequence:x.sequence,source_node:x.source_node,hash:x.hash,scope:x.scope,domains:x.domains,conflicts:x.conflicts,preserved:x.preserved,external:x.external,new_disabled_record_ids:x.new_disabled_record_ids,last_error:x.last_error};return html+technical('Technical details',details)}
function result(id,value,tone='ok'){const e=document.querySelector('#'+id);e.innerHTML=renderResult(value);e.className='result '+(tone===true?'ok':tone===false?'error':tone)}
function busy(button,on,label){if(on){button.dataset.label=button.textContent;button.textContent=label;button.disabled=true}else{button.textContent=button.dataset.label||button.textContent;button.disabled=button.dataset.bundleDisabled==='true'}}
function showFieldError(error){const field=error?.field;if(!field)return;const input=form.querySelector(`[name="${field}"]`);if(input){input.focus();input.setAttribute('aria-invalid','true');input.title=error.message||'Fix this field';setTimeout(()=>{input.removeAttribute('aria-invalid');input.title=''},8000)}}
async function request(path,options={}){const response=await fetch(api(path),options);let value;try{value=await response.json()}catch(_e){throw new Error('The plugin returned an invalid response')}if(!response.ok){const error=new Error(value.message||`HTTP ${response.status}`);error.field=value.field;error.code=value.code;showFieldError(error);throw error}return value}
function collect(){const o={protected_records:{}};for(const e of form.querySelectorAll('[name]')){const n=e.name;if(n.startsWith('domain_')||n.startsWith('core_')||n.startsWith('protect_'))continue;if(e.type==='radio'){if(e.checked)o[n]=e.value}else if(e.type==='checkbox')o[n]=e.checked;else o[n]=e.value}o.domains=[...form.querySelectorAll('[name^=domain_]:checked')].map(e=>e.value);o.core_setting_keys=[...form.querySelectorAll('[name^=core_]:checked')].map(e=>e.value);if(o.replication_scope==='iptv_content'){o.domains=meta.iptv_content_domains||[];o.core_setting_keys=[]}for(const e of form.querySelectorAll('[name^=protect_]:checked')){const domain=e.name.slice('protect_'.length);(o.protected_records[domain]??=[]).push(Number(e.value))}o.protected_output_profile_ids=o.protected_records.output_profiles||[];return o}
function set(name,value){const es=form.querySelectorAll(`[name="${name}"]`);if(!es.length)return;if(es[0].type==='radio')es.forEach(e=>e.checked=e.value===String(value));else if(es[0].type==='checkbox')es[0].checked=!!value;else es[0].value=value??''}
function effectiveDomains(){const scope=form.replication_scope.value;if(scope==='full')return Object.values(meta.domain_groups).flat();if(scope==='basic')return meta.default_domains;if(scope==='iptv_content')return meta.iptv_content_domains||[];return [...form.querySelectorAll('[name^=domain_]:checked')].map(e=>e.value)}
function applyPreset(scope){if(!meta||scope==='custom')return;const selected=new Set(effectiveDomains());customRoots=new Set(selected);for(const e of form.querySelectorAll('[name^=domain_]'))e.checked=selected.has(e.value);if(scope==='iptv_content')for(const e of form.querySelectorAll('[name^=core_]'))e.checked=false;syncDomains()}
function conditional(){const role=form.role.value,mode=form.mode.value,backend=form.storage_backend.value,storage=mode!=='direct',redundancy=form.redundancy_mode.value,cold=redundancy==='cold_standby',auto=form.auto_start.checked;if(cold){set('import_on_start',true);set('automatic_apply',false)}if(meta?.automation_text){document.querySelector('#autoStartDescription').textContent=(role==='leader'?meta.automation_text.auto_start_main:meta.automation_text.auto_start_follower)||'';document.querySelector('#coldAutomationNotice').textContent=auto?'':meta.automation_text.cold_standby_disabled}document.querySelectorAll('.leader-only').forEach(e=>e.classList.toggle('hidden',role!=='leader'));document.querySelectorAll('.follower-only').forEach(e=>e.classList.toggle('hidden',role!=='follower'));document.querySelector('#coldAutomationNotice').classList.toggle('hidden',!cold||auto);document.querySelector('#importOnStartControl').classList.toggle('hidden',cold||role!=='follower');document.querySelector('#automaticApplyControl').classList.toggle('hidden',cold||role!=='follower');document.querySelector('#roleBadge').textContent=role==='leader'?'MAIN / AUTHORITATIVE':'FOLLOWER';document.querySelector('#storage').classList.toggle('hidden',!storage);document.querySelectorAll('.peer').forEach(e=>e.classList.toggle('hidden',mode==='shared_storage'));document.querySelector('#vip').classList.toggle('hidden',redundancy!=='plugin_vip');document.querySelector('#sharedPath').classList.toggle('hidden',backend!=='filesystem');document.querySelectorAll('.remote').forEach(e=>e.classList.toggle('hidden',backend==='filesystem'));for(const c of ['sftp','s3','smb'])document.querySelectorAll('.'+c).forEach(e=>e.classList.toggle('hidden',backend!==c));document.querySelectorAll('.tls,.http').forEach(e=>e.classList.toggle('hidden',!['webdav','s3'].includes(backend)));document.querySelector('#domains').classList.toggle('hidden',form.replication_scope.value!=='custom'||role!=='follower');document.querySelector('#coreSettings').classList.toggle('hidden',role!=='follower'||!effectiveDomains().includes('core_settings'));document.querySelector('#mirrorChannelStreamsControl').classList.toggle('hidden',role!=='follower'||!effectiveDomains().includes('channel_streams'));const protectedDomains=new Set(effectiveDomains().filter(d=>meta.local_protection_domains.includes(d)));document.querySelector('#recordProtection').classList.toggle('hidden',role!=='follower'||!protectedDomains.size);for(const e of document.querySelectorAll('[data-protection-domain]'))e.classList.toggle('hidden',role!=='follower'||!protectedDomains.has(e.dataset.protectionDomain));renderScopeSummary()}
function syncDomains(){const selected=new Set(customRoots),required=new Set();let changed=true;while(changed){changed=false;for(const d of [...selected])for(const dep of(meta.domain_dependencies[d]||[]))if(!selected.has(dep)){selected.add(dep);required.add(dep);changed=true}}for(const e of form.querySelectorAll('[name^=domain_]')){e.checked=selected.has(e.value);e.disabled=required.has(e.value)&&!customRoots.has(e.value);e.closest('label').title=e.disabled?'Required by another selected area':''}}
function renderBundleInfo(info){const e=document.querySelector('#bundleInfo');if(!e)return;const x=info||{status:'configuration_incomplete',message:'Save or import the Follower configuration before checking Main bundles.'},ready=x.status==='verified',previewable=ready||x.status==='current';e.innerHTML=esc(x.message||'Bundle status unavailable.');e.className='notice '+(ready||x.status==='current'?'ok':(x.status==='missing'||x.status==='configuration_incomplete'||x.status==='stale'?'warn':'error'));for(const id of ['preview','importLatest','initialize']){const button=document.querySelector('#'+id);if(!button)continue;const enabled=id==='preview'?previewable:ready;button.dataset.bundleDisabled=String(!enabled);button.disabled=!enabled}}
function renderScopeSummary(){if(!meta)return;document.querySelector('#scopeSummary').innerHTML=effectiveDomains().map(d=>`<span>${esc(meta.domain_labels[d]||d)}</span>`).join('');renderBundleInfo(meta.bundle_info)}
async function refreshBundleInfo(button){if(!meta)return;try{if(button)busy(button,true,'Refreshing…');const info=await request('/api/bundle-info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});meta.bundle_info=info;renderBundleInfo(info)}catch(e){renderBundleInfo({status:'unavailable',message:e.message})}finally{if(button)busy(button,false)}}
async function load(){meta=await request('/api/config');const x=meta,settings=x.settings||{},selected=new Set(String(settings.domains||x.default_domains.join(',')).split(',').filter(Boolean));customRoots=new Set(selected);document.querySelector('#domains').innerHTML=Object.entries(x.domain_groups).map(([g,ds])=>`<fieldset><legend>${esc(x.domain_group_labels[g]||g)}</legend><div class="domain-grid">${ds.map(d=>`<label class="check domain"><input type="checkbox" name="domain_${esc(d)}" value="${esc(d)}" ${selected.has(d)?'checked':''}><span><b>${esc(x.domain_labels[d]||d)}</b><br><small>${esc(x.domain_descriptions[d]||'')}</small></span></label>`).join('')}</div></fieldset>`).join('');const cores=new Set(String(settings.core_setting_keys||'stream_settings,proxy_settings,epg_settings,user_limit_settings').split(','));document.querySelector('#core').innerHTML=Object.entries(x.core_setting_groups).map(([k,v])=>`<label class="check domain"><input type="checkbox" name="core_${esc(k)}" value="${esc(k)}" ${cores.has(k)?'checked':''}><span><b>${esc(v)}</b></span></label>`).join('');const protected=settings.protected_records||{};document.querySelector('#protectedRecords').innerHTML=(x.local_protection_domains||[]).map(d=>{const rows=x.protection_records?.[d]||[],ids=new Set((protected[d]||[]).map(Number)),field=x.new_record_policy_fields[d],options=(x.new_record_policy_options||[]).map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('');return `<details data-protection-domain="${esc(d)}" open><summary>${esc(x.domain_labels[d]||d)} kept local</summary>${rows.length?rows.map(p=>`<label class="profile"><input type="checkbox" name="protect_${esc(d)}" value="${Number(p.id)}" ${ids.has(Number(p.id))?'checked':''}><b>ID ${Number(p.id)}</b><span>${esc(p.name)}${p.is_active?'':' (inactive)'}</span></label>`).join(''):'<p class="muted">No records exist on this node.</p>'}<label style="margin-top:14px">New ${esc(x.domain_labels[d]||d)} from Main<select name="${esc(field)}">${options}</select><small>Choose whether newly seen Main records are created disabled and kept local, imported from Main, or block the complete import.</small></label></details>`}).join('');const a=x.automation_text||{};document.querySelector('#automationHeading').textContent=a.section||'Automatic replication';document.querySelector('#autoStartLabel').textContent=a.auto_start_label||'';document.querySelector('#autoStartDescription').textContent=(form.role.value==='leader'?a.auto_start_main:a.auto_start_follower)||'';document.querySelector('#importOnStartLabel').textContent=a.import_on_start_label||'';document.querySelector('#importOnStartDescription').textContent=a.import_on_start_description||'';document.querySelector('#automaticApplyLabel').textContent=a.automatic_apply_label||'';document.querySelector('#automaticApplyDescription').textContent=a.automatic_apply_description||'';document.querySelector('#intervalLabel').textContent=a.interval_label||'Replication interval (seconds)';document.querySelector('#intervalDescription').textContent=a.interval_description||'';for(const[k,v]of Object.entries(settings))if(!k.endsWith('_is_set')&&!['domains','core_setting_keys','protected_output_profile_ids','protected_records'].includes(k))set(k,v);if(settings.auto_start===undefined&&form.redundancy_mode.value==='cold_standby')set('auto_start',true);if(!settings.node_id)set('node_id',location.hostname.replace(/[^a-z0-9.-]/gi,'-')||'dispatcharr-node');if(!settings.cluster_id)set('cluster_id','dispatcharr-home');syncDomains();conditional();if(x.replication_status)result('replicationStatus',x.replication_status.message||x.replication_status,x.replication_status.status==='success')}
form.addEventListener('change',e=>{if(e.target.name==='replication_scope')applyPreset(e.target.value);if(e.target.name?.startsWith('domain_')){if(e.target.checked)customRoots.add(e.target.value);else customRoots.delete(e.target.value);syncDomains()}conditional()});
document.querySelector('#testStorage').onclick=async function(){busy(this,true,'Testing…');try{const x=await request('/api/test-storage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});result('storageResult',x.message||x,x.status==='success');if(x.status==='success')await refreshBundleInfo()}catch(e){result('storageResult',e.message,false)}finally{busy(this,false)}};
let fetchedSftpHostKey='';
document.querySelector('#inspectSftpKey').onclick=async function(){busy(this,true,'Fetching…');try{const x=await request('/api/sftp/host-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});fetchedSftpHostKey=x.host_key||'';document.querySelector('#trustSftpKey').disabled=!fetchedSftpHostKey;result('sftpKeyResult',{message:x.message,fingerprint:x.fingerprint,host_key:fetchedSftpHostKey},x.status==='success')}catch(e){result('sftpKeyResult',e.message,false)}finally{busy(this,false)}};
document.querySelector('#trustSftpKey').onclick=async function(){if(!fetchedSftpHostKey)return; if(!confirm('Trust this SFTP server key after comparing its fingerprint?'))return;busy(this,true,'Saving…');try{const body=collect();body.host_key=fetchedSftpHostKey;const x=await request('/api/sftp/trust-host-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});result('sftpKeyResult',x.message||x,x.status==='success')}catch(e){result('sftpKeyResult',e.message,false)}finally{busy(this,false)}};
document.querySelector('#importSftpPrivateKey').onclick=async function(){const file=document.querySelector('#sftpPrivateKeyFile').files[0];if(!file){result('sftpPrivateKeyResult','Select a private-key file first.',false);return}busy(this,true,'Importing…');try{const x=await request('/api/sftp/private-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({private_key:await file.text(),passphrase:document.querySelector('#sftpPrivateKeyPassphrase').value})});document.querySelector('#sftpPrivateKeyPassphrase').value='';result('sftpPrivateKeyResult',x.message||x,x.status==='success')}catch(e){result('sftpPrivateKeyResult',e.message,false)}finally{busy(this,false)}};
form.onsubmit=async e=>{e.preventDefault();const b=e.submitter;busy(b,true,'Saving…');try{const x=await request('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});result('saveResult',x.message||x,x.status==='success');await load()}catch(err){result('saveResult',err.message,false)}finally{busy(b,false)}};
document.querySelector('#downloadProfile').onclick=async function(){const button=this,include=document.querySelector('#includePasswords').checked;busy(button,true,'Saving…');try{await request('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});busy(button,true,'Downloading…');const response=await fetch(api('/api/profile','&include_passwords='+include));if(!response.ok){const error=await response.json();throw new Error(error.message||'Profile download failed')}const blob=await response.blob(),link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='failovarr-profile.json';link.click();URL.revokeObjectURL(link.href);result('downloadResult',include?'Saved and downloaded profile including storage passwords.':'Saved and downloaded profile without storage passwords.',true);await load()}catch(e){result('downloadResult',e.message,false)}finally{busy(button,false)}};
document.querySelector('#importProfile').onclick=async function(){const file=document.querySelector('#profileFile').files[0];if(!file){result('profileResult','Select a JSON configuration file first.',false);return}busy(this,true,'Importing…');try{const profile=JSON.parse(await file.text()),x=await request('/api/profile/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile,local_settings:collect()})});result('profileResult',x.message||x,x.status==='success');await load()}catch(e){result('profileResult',e.message,false)}finally{busy(this,false)}};
document.querySelector('#refreshBundleInfo').onclick=function(){return refreshBundleInfo(this)};
async function refreshStatus(target,button){busy(button,true,'Refreshing…');try{const x=await request('/api/status',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});result(target,x,x.status==='success')}catch(e){result(target,e.message,false)}finally{busy(button,false)}}
document.querySelector('#refreshStatusLeader').onclick=function(){return refreshStatus('leaderStatus',this)};
document.querySelector('#exportNow').onclick=async function(){const button=this;busy(button,true,'Saving…');try{await request('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});busy(button,true,'Exporting…');const x=await request('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});result('leaderStatus',x,x.status==='exported');await load()}catch(e){result('leaderStatus',e.message,false)}finally{busy(button,false)}};
document.querySelector('#preview').onclick=async function(){busy(this,true,'Previewing…');try{const x=await request('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});result('importResult',x,x.status==='preview')}catch(e){result('importResult',e.message,false)}finally{busy(this,false)}};
document.querySelector('#importLatest').onclick=async function(){let response;busy(this,true,'Importing…');try{response=await request('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});result('importResult',response,response.status==='applied'?'ok':response.status==='waiting'?'warn':'error');if(response.status==='waiting')await refreshBundleInfo()}catch(e){result('importResult',e.message,false)}finally{busy(this,false)}};
document.querySelector('#refreshStatus').onclick=function(){return refreshStatus('replicationStatus',this)};
document.querySelector('#initialize').onclick=async function(){const node=form.node_id.value,confirmation=prompt(`This replaces the selected follower data after creating a Dispatcharr backup.\n\nType exactly: INITIALIZE ${node}`);if(confirmation===null)return;busy(this,true,'Initializing…');try{const x=await request('/api/initialize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation})});result('initializeResult',x,x.status==='initialized')}catch(e){result('initializeResult',e.message,false)}finally{busy(this,false)}};
load().catch(e=>result('saveResult',e.message,false));
</script></body></html>'''
