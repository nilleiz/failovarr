"""Configuration parsing shared by the plugin UI and replication engine."""

from __future__ import annotations

import ipaddress
import json
import os
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


PLUGIN_DB_KEY = "failovarr"
DEFAULT_KNOWN_HOSTS_PATH = "/data/failovarr-state/known_hosts"
LEGACY_KNOWN_HOSTS_PATH = "/data/redundancy-secrets/known_hosts"
SUPPORTED_DOMAINS = (
    "user_agents",
    "stream_profiles",
    "output_profiles",
    "core_settings",
    "server_groups",
    "m3u_accounts",
    "m3u_filters",
    "m3u_account_profiles",
    "epg_sources",
    "epg_data",
    "channel_groups",
    "logos",
    "streams",
    "channels",
    "channel_overrides",
    "channel_profiles",
    "channel_profile_memberships",
    "channel_streams",
    "channel_group_m3u_accounts",
)
LOCAL_PROTECTION_DOMAINS = (
    "m3u_accounts",
    "epg_sources",
    "stream_profiles",
    "output_profiles",
)
NEW_RECORD_POLICY_FIELDS = {
    "output_profiles": "new_output_profile_policy",
    "stream_profiles": "new_stream_profile_policy",
    "epg_sources": "new_epg_source_policy",
    "m3u_accounts": "new_m3u_account_policy",
}
NEW_RECORD_POLICY_OPTIONS = (
    ("disabled", "Create disabled and keep local"),
    ("source", "Use Main configuration"),
    ("block", "Block import until decided"),
)


def new_record_policy_description(domain: str) -> str:
    return (
        "Choose whether newly seen Main records are created disabled and kept local, "
        "imported from Main, or block the complete import."
    )
AUTOMATION_TEXT = {
    "section": "Automatic replication",
    "cold_standby_disabled": (
        "Automatic replication is disabled. Start the replication service manually when this "
        "Cold Standby node becomes active; the Follower then imports the last verified bundle once."
    ),
    "auto_start_label": "Enable automatic replication after Dispatcharr starts",
    "auto_start_main": "Main publishes signed bundles at the configured interval.",
    "auto_start_follower": "Follower starts its configured import behavior automatically.",
    "import_on_start_label": "Import once when the replication service starts",
    "import_on_start_description": (
        "Follower only: verify and import the latest bundle once when the service starts."
    ),
    "automatic_apply_label": "Continuously import newer verified bundles",
    "automatic_apply_description": (
        "Follower only: keep checking for and importing newer bundles while the service runs."
    ),
    "interval_label": "Replication interval (seconds)",
    "interval_description": "Main export or online Follower check interval; minimum 10 seconds.",
}
DEFAULT_DOMAINS = ",".join((
    "user_agents",
    "stream_profiles",
    "output_profiles",
    "core_settings",
))
FULL_DOMAINS = SUPPORTED_DOMAINS
# Complete provider and channel graph, deliberately without generic Dispatcharr
# settings or hardware-specific output profiles. Dependency order matches
# SUPPORTED_DOMAINS so adapter application remains deterministic.
IPTV_CONTENT_DOMAINS = (
    "user_agents", "stream_profiles", "server_groups", "m3u_accounts",
    "m3u_filters", "m3u_account_profiles", "epg_sources", "epg_data",
    "channel_groups", "logos", "streams", "channels", "channel_overrides",
    "channel_profiles", "channel_profile_memberships", "channel_streams",
    "channel_group_m3u_accounts",
)
DOMAIN_GROUPS = {
    # Presentation follows Dispatcharr navigation. SUPPORTED_DOMAINS stays in
    # dependency-safe order for export and transactional apply.
    "channels": (
        "channels", "streams", "channel_groups", "channel_overrides",
        "channel_profiles", "channel_profile_memberships", "channel_streams",
        "channel_group_m3u_accounts",
    ),
    "m3u_epg": (
        "m3u_accounts", "m3u_account_profiles", "m3u_filters", "server_groups",
        "epg_sources", "epg_data",
    ),
    "logos": ("logos",),
    "settings": ("core_settings", "stream_profiles", "output_profiles", "user_agents"),
}
DOMAIN_GROUP_LABELS = {
    "channels": "Channels",
    "m3u_epg": "M3U & EPG Manager",
    "logos": "Logo Manager",
    "settings": "Settings",
}
DOMAIN_LABELS = {
    "user_agents": "User Agents",
    "stream_profiles": "Stream Profiles",
    "output_profiles": "Output Profiles",
    "core_settings": "Settings",
    "server_groups": "M3U Server Groups",
    "m3u_accounts": "M3U Accounts",
    "m3u_filters": "M3U Filters",
    "m3u_account_profiles": "M3U Account Profiles",
    "epg_sources": "EPG Sources",
    "epg_data": "EPG Channel Mappings",
    "channel_groups": "Channel Groups",
    "logos": "Logos",
    "streams": "Streams",
    "channels": "Channels",
    "channel_overrides": "Channel Overrides",
    "channel_profiles": "Channel Profiles",
    "channel_profile_memberships": "Channel Profile Memberships",
    "channel_streams": "Channel Streams",
    "channel_group_m3u_accounts": "Channel Group M3U Accounts",
}
DOMAIN_DESCRIPTIONS = {
    "user_agents": "User Agent templates used by providers and streams.",
    "stream_profiles": "Input and stream-processing profiles.",
    "output_profiles": "Stable Output Profile IDs and configuration. Selected follower profiles can stay local.",
    "core_settings": "Selected groups from Dispatcharr Settings.",
    "server_groups": "Logical M3U server groups.",
    "m3u_accounts": "M3U providers, including URLs and credentials.",
    "m3u_filters": "Include and exclude filters for M3U Accounts.",
    "m3u_account_profiles": "Sub-profiles and rules for M3U Accounts.",
    "epg_sources": "EPG sources, including URLs and credentials.",
    "epg_data": "EPG channel mappings, not the large programme cache.",
    "channel_groups": "Channel Groups.",
    "logos": "Logo definitions and URLs; binary files are not included.",
    "streams": "Stream definitions and provider relationships.",
    "channels": "Channels, numbers and stable UUIDs.",
    "channel_overrides": "Manual Channel Overrides.",
    "channel_profiles": "Named M3U and EPG output profiles.",
    "channel_profile_memberships": "Channel membership in Channel Profiles.",
    "channel_streams": "Prioritized Stream assignments per Channel.",
    "channel_group_m3u_accounts": "M3U Account assignments to Channel Groups.",
}
CORE_SETTING_GROUPS = {
    "stream_settings": "Streaming",
    "proxy_settings": "Proxy",
    "epg_settings": "EPG",
    "user_limit_settings": "Client limits",
    "dvr_settings": "DVR and recording paths (node-local recommended)",
    "hdhomerun_settings": "HDHomeRun (node-local recommended)",
}
# Safe initial selection used only until a follower has verified its first
# signed Main bundle. DVR and HDHomeRun settings deliberately remain local by
# default.
DEFAULT_CORE_SETTING_KEYS = (
    "stream_settings",
    "proxy_settings",
    "epg_settings",
    "user_limit_settings",
)


def full_export_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Build the fixed, complete Main export scope for Milestone 1.

    A follower chooses what it applies locally.  The authoritative node always
    signs every supported area, independently of legacy leader scope fields.
    """
    result = dict(settings)
    result.update({
        "replication_scope": "custom",
        "domains": ",".join(FULL_DOMAINS),
        "core_setting_keys": ",".join(CORE_SETTING_GROUPS),
    })
    return result


DOMAIN_DEPENDENCIES = {
    "stream_profiles": {"user_agents"},
    "m3u_accounts": {
        "server_groups", "user_agents", "stream_profiles",
    },
    "m3u_filters": {"m3u_accounts"},
    "m3u_account_profiles": {"m3u_accounts"},
    "epg_data": {"epg_sources"},
    "streams": {"m3u_accounts", "channel_groups", "stream_profiles"},
    "channels": {
        "m3u_accounts", "channel_groups", "logos", "epg_data", "stream_profiles",
    },
    "channel_overrides": {
        "channels", "channel_groups", "logos", "epg_data", "stream_profiles",
    },
    "channel_profile_memberships": {"channel_profiles", "channels"},
    "channel_streams": {"channels", "streams"},
    "channel_group_m3u_accounts": {"channel_groups", "m3u_accounts"},
}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _load_metadata() -> dict[str, Any]:
    with open(os.path.join(os.path.dirname(__file__), "plugin.json"), encoding="utf-8") as handle:
        return json.load(handle)


PLUGIN_CONFIG = _load_metadata()


class ConfigValidationError(ValueError):
    """A safe, actionable validation problem for a named configuration field."""

    def __init__(self, field: str, code: str, message: str):
        self.field = field
        self.code = code
        super().__init__(message)


def _info(label: str, description: str) -> dict[str, Any]:
    return {"id": f"_section_{label.lower().replace(' ', '_')}", "label": label,
            "type": "info", "description": description}


def normalize_redundancy_mode(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Map the single user-facing mode to the legacy engine fields.

    Existing node files and v1 cluster profiles keep working; only the UI is
    simplified.  Cold standby remains the safe default for a fresh node.
    """
    result = dict(settings)
    selected = str(result.get("redundancy_mode", "")).strip().lower()
    if selected not in {"cold_standby", "plugin_vip", "external_proxy"}:
        if str(result.get("deployment_mode", "")).lower() == "cold_standby":
            selected = "cold_standby"
        elif str(result.get("client_access_mode", "")).lower() == "plugin_vip":
            selected = "plugin_vip"
        elif str(result.get("client_access_mode", "")).lower() == "external_proxy":
            selected = "external_proxy"
        else:
            selected = "cold_standby"
    result["redundancy_mode"] = selected
    if selected == "cold_standby":
        result.update({
            "deployment_mode": "cold_standby", "client_access_mode": "disabled",
            # A new Cold Standby configuration is automatic by default, but a
            # deliberately stored false remains an operator choice.
            "auto_start": as_bool(result.get("auto_start", True)),
            "import_on_start": True,
            "automatic_apply": False,
        })
    elif selected == "plugin_vip":
        result.update({"deployment_mode": "online", "client_access_mode": "plugin_vip"})
    else:
        result.update({"deployment_mode": "online", "client_access_mode": "external_proxy"})
    return result


def _select(field_id: str, label: str, default: str, description: str,
            options: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return {"id": field_id, "label": label, "type": "select", "default": default,
            "description": description,
            "options": [{"value": value, "label": option_label} for value, option_label in options]}


def build_plugin_fields(settings: Mapping[str, Any] | None = None,
                        protection_records: Mapping[str, list[Mapping[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Build the native Dispatcharr settings schema without exposing secrets.

    Dispatcharr 0.29 supports a flat field list, so the schema is rebuilt after
    a save/reopen to show only role/backend-relevant choices.  The assistant
    remains the place to enter secrets and upload an SFTP private key.
    """
    current = normalize_redundancy_mode(settings or {})
    role = str(current.get("role", "follower")).lower()
    mode = str(current.get("mode", "shared_storage")).lower()
    backend = str(current.get("storage_backend", "filesystem")).lower()
    domains = set(resolve_domains(current))
    fields: list[dict[str, Any]] = [
        _info("Recommended: use the Setup Assistant", "For first setup, storage credentials, SFTP host-key trust and Main-to-Follower configuration transfer, go to Actions → Open setup. The Assistant runs on the plugin port 9192."),
        _select("role", "Node role", role, "Exactly one node is Main (authoritative); the other one is Follower.", (("leader", "Main / authoritative"), ("follower", "Follower / passive"))),
        _info("Node and deployment", "Both nodes need different node names and the same cluster name. Save, then reopen this panel after changing role, transport or storage backend."),
        {"id": "node_id", "label": "Node name", "type": "string", "default": current.get("node_id", ""), "placeholder": "main or slave", "description": "1–64 letters, digits, dots, underscores or hyphens."},
        {"id": "cluster_id", "label": "Cluster name", "type": "string", "default": current.get("cluster_id", ""), "placeholder": "dispatcharr-home", "description": "The identical cluster name on both nodes."},
        _select("redundancy_mode", "Redundancy mode", str(current.get("redundancy_mode", "cold_standby")), "Cold Standby uses one container and its existing client IP. Online modes require separate management IPs.", (("cold_standby", "Cold Standby — one node running"), ("plugin_vip", "Online — Plugin-managed Linux VIP"), ("external_proxy", "Online — external HA reverse proxy"))),
        {"id": "setup_public_url", "label": "Setup Assistant URL override (optional)", "type": "string", "default": current.get("setup_public_url", ""), "placeholder": "http://dispatcharr-host:9192", "description": "Only set this when port 9192 is published under another address. Then use Actions → Open setup."},
        {"id": "client_vip", "label": "Client VIP", "type": "string", "default": current.get("client_vip", ""), "placeholder": "192.168.178.210", "description": "Plugin-managed Linux VIP only. Both nodes still need separate management addresses."},
        {"id": "vip_interface", "label": "VIP network interface", "type": "string", "default": current.get("vip_interface", "eth0"), "description": "Linux interface inside the container or network namespace for the VIP."},
        {"id": "vip_prefix_length", "label": "VIP prefix length", "type": "number", "default": int(current.get("vip_prefix_length", 24)), "description": "IPv4 network prefix length for the VIP."},
        _info("Transport and storage", "The Main publishes signed bundles. The Follower pulls and verifies them; it never accepts a remote database write."),
        _select("mode", "Replication transport", mode, "Shared storage suits Cold Standby. Direct Pull needs the Main peer URL; Hybrid uses both.", (("shared_storage", "Shared storage"), ("direct", "Direct Pull"), ("hybrid", "Direct Pull with storage fallback"))),
        {"id": "peer_url", "label": "Main peer URL", "type": "string", "default": current.get("peer_url", ""), "placeholder": "http://main-host:9192", "description": "Required for a Direct-Pull Follower; no path, credentials or query string."},
        {"id": "peer_node_id", "label": "Peer node name", "type": "string", "default": current.get("peer_node_id", ""), "description": "Optional for manual operation; required for a planned automatic handoff."},
        _select("storage_backend", "Storage backend", backend, "Connection credentials and SFTP private keys are kept in Open setup, never in this native form.", (("filesystem", "Filesystem bind mount"), ("webdav", "WebDAV"), ("s3", "S3 / MinIO"), ("sftp", "SFTP"), ("smb", "SMB 3"))),
        {"id": "shared_path", "label": "Shared bind-mount path", "type": "string", "default": current.get("shared_path", "/data/redundancy"), "description": "Filesystem backend only. It must be a shared directory below /data."},
        {"id": "storage_endpoint", "label": "Storage endpoint", "type": "string", "default": current.get("storage_endpoint", ""), "placeholder": "https://storage.example or sftp://storage.example:22", "description": "Remote backend endpoint; credentials are configured in Open setup."},
        {"id": "storage_container", "label": "Storage directory / bucket", "type": "string", "default": current.get("storage_container", ""), "description": "A bucket, share subdirectory or remote directory; never a host filesystem path."},
        {"id": "storage_username", "label": "Storage username / access key", "type": "string", "default": current.get("storage_username", ""), "description": "The non-secret account identifier. Enter the password or secret access key in Open setup."},
        {"id": "storage_timeout_seconds", "label": "Storage timeout (seconds)", "type": "number", "default": int(current.get("storage_timeout_seconds", 20)), "description": "Connection timeout for storage operations."},
        {"id": "storage_ca_path", "label": "Custom storage CA path (optional)", "type": "string", "default": current.get("storage_ca_path", ""), "placeholder": "/data/certs/ca.pem", "description": "Optional CA certificate below /data. TLS verification cannot be disabled."},
        {"id": "storage_allow_insecure_http", "label": "Allow plain HTTP storage", "type": "boolean", "default": as_bool(current.get("storage_allow_insecure_http", False)), "description": "Only for an isolated protected test lab; HTTPS is strongly recommended."},
        {"id": "state_path", "label": "Node-local state path", "type": "string", "default": current.get("state_path", "/data/failovarr-state"), "description": "Stores sequences, hashes, replay/apply state and service state on this node. Keep it on persistent local storage below /data, never only on shared storage."},
    ]
    # Dispatcharr caches a plugin field schema until an explicit plugin reload.
    # Keep every safe backend/role field in that schema; the setup assistant is
    # the mode-aware UI that hides irrelevant controls. This prevents a role or
    # backend switch from making fields disappear in the native Settings panel.
    fields.extend([
        _info("Backend-specific settings", "Only fill the fields for the selected storage backend. Credentials remain in Open setup."),
        {"id": "sftp_known_hosts_path", "label": "SFTP known_hosts path", "type": "string", "default": current.get("sftp_known_hosts_path", DEFAULT_KNOWN_HOSTS_PATH), "description": "SFTP only: strict server host-key verification file. The default is node-local and writable by Dispatcharr; fetch and trust the key in Open setup."},
        {"id": "s3_region", "label": "S3 region", "type": "string", "default": current.get("s3_region", "us-east-1"), "description": "S3 / MinIO only."},
        _select("s3_addressing_style", "S3 addressing style", str(current.get("s3_addressing_style", "path")), "S3 / MinIO only.", (("path", "Path style"), ("virtual", "Virtual-hosted style"))),
        {"id": "s3_prefix", "label": "S3 key prefix", "type": "string", "default": current.get("s3_prefix", "failovarr"), "description": "S3 / MinIO only; prefix below the selected bucket."},
        {"id": "smb_domain", "label": "SMB domain (optional)", "type": "string", "default": current.get("smb_domain", ""), "description": "SMB only. SMB encryption remains mandatory."},
    ])
    if role != "leader":
        fields.extend([
            _info("Data imported by this Follower", "Choose the verified Main areas this node applies. Dependencies are added automatically; Main always exports the complete supported graph."),
            _select("replication_scope", "Import scope", str(current.get("replication_scope", "basic")), "Complete, content-only or individually selected Dispatcharr areas on this Follower.", (("full", "Complete IPTV setup"), ("basic", "Profiles and Settings only"), ("iptv_content", "M3U, EPG and Channels only"), ("custom", "Custom selection"))),
        ])
        for group, grouped_domains in DOMAIN_GROUPS.items():
            fields.append(_info(DOMAIN_GROUP_LABELS[group], "Follower import selection."))
            for domain in grouped_domains:
                fields.append({"id": f"replicate_{domain}", "label": DOMAIN_LABELS[domain], "type": "boolean", "default": domain in domains, "description": DOMAIN_DESCRIPTIONS[domain]})
        fields.append(_info("Settings groups", "Choose which Dispatcharr Settings groups this Follower applies. DVR and HDHomeRun are commonly left local."))
        core = set(parse_csv(current.get("core_setting_keys", "")))
        for key, label in CORE_SETTING_GROUPS.items():
            fields.append({"id": f"replicate_core_{key}", "label": label, "type": "boolean", "default": key in core, "description": "Import this Dispatcharr Settings group from Main."})
        fields.append({"id": "allow_deletes", "label": "Allow replicated deletions", "type": "boolean", "default": as_bool(current.get("allow_deletes", False)), "description": "Follower-only. Off by default; enable only after a verified first synchronization and rollback plan."})
        fields.append(_info("Records kept local", "Protected records retain their complete local configuration and stable ID. They are inactive until their domain is imported."))
    raw_protected = current.get("protected_records") or {}
    if not isinstance(raw_protected, Mapping):
        try:
            raw_protected = parse_protected_records(raw_protected)
        except ValueError:
            raw_protected = {}
    protection_domains = (
        domain for domain in LOCAL_PROTECTION_DOMAINS
        if role == "follower" and domain in domains
    )
    for domain in protection_domains:
        protected = set(parse_int_ids(raw_protected.get(domain, [])))
        if domain == "output_profiles":
            protected.update(parse_int_ids(current.get("protected_output_profile_ids", "")))
        for record in (protection_records or {}).get(domain, []):
            record_id = int(record["id"])
            fields.append({"id": f"protect_{domain}_{record_id}", "label": f"Follower only: keep {DOMAIN_LABELS[domain]} #{record_id} local — {record.get('name') or '(unnamed)'}", "type": "boolean", "default": record_id in protected, "description": "Do not overwrite or delete this complete local record during import."})
        policy_field = NEW_RECORD_POLICY_FIELDS[domain]
        fields.append(_select(
            policy_field, f"New {DOMAIN_LABELS[domain]} from Main",
            str(current.get(policy_field, "disabled")),
            new_record_policy_description(domain),
            NEW_RECORD_POLICY_OPTIONS,
        ))
    fields.extend([
        _info(AUTOMATION_TEXT["section"], AUTOMATION_TEXT["cold_standby_disabled"] if current.get("redundancy_mode") == "cold_standby" and not as_bool(current.get("auto_start", True)) else "Bundle retention is fixed at three. Validate configuration and storage before enabling automation."),
        {"id": "auto_start", "label": AUTOMATION_TEXT["auto_start_label"], "type": "boolean", "default": as_bool(current.get("auto_start", True)), "description": AUTOMATION_TEXT["auto_start_main"] if role == "leader" else AUTOMATION_TEXT["auto_start_follower"]},
        {"id": "interval_seconds", "label": AUTOMATION_TEXT["interval_label"], "type": "number", "default": int(current.get("interval_seconds", 60)), "description": AUTOMATION_TEXT["interval_description"]},
        {"id": "client_identity_users", "label": "Client identity users", "type": "string", "default": current.get("client_identity_users", "*"), "description": "Use * to protect all IPTV API identities without exposing their credentials."},
        {"id": "confirm", "label": "Confirm actions", "type": "boolean", "default": as_bool(current.get("confirm", True)), "description": "Require Dispatcharr confirmation for sensitive actions."},
    ])
    if role == "follower" and current.get("redundancy_mode") != "cold_standby":
        fields.insert(-4, {"id": "import_on_start", "label": AUTOMATION_TEXT["import_on_start_label"], "type": "boolean", "default": as_bool(current.get("import_on_start", False)), "description": AUTOMATION_TEXT["import_on_start_description"]})
        fields.insert(-4, {"id": "automatic_apply", "label": AUTOMATION_TEXT["automatic_apply_label"], "type": "boolean", "default": as_bool(current.get("automatic_apply", False)), "description": AUTOMATION_TEXT["automatic_apply_description"]})
    return fields


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_csv(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value or "").split(",")
    return tuple(dict.fromkeys(str(item).strip() for item in parts if str(item).strip()))


def parse_json_object(value: Any, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        try:
            result = json.loads(str(value))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return result


def parse_int_ids(value: Any) -> tuple[int, ...]:
    values = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    result: list[int] = []
    for item in values:
        if str(item).strip():
            parsed = int(item)
            if parsed < 1:
                raise ValueError("protected output profile IDs must be positive integers")
            if parsed not in result:
                result.append(parsed)
    return tuple(result)


def parse_protected_records(value: Any) -> dict[str, tuple[int, ...]]:
    """Parse the node-local whole-record protection list.

    This deliberately is not a generic override mechanism.  The four supported
    domains have stable numeric IDs which can be preserved locally while their
    dependent records continue to refer to those IDs.
    """
    raw = parse_json_object(value, "protected_records")
    unknown = sorted(set(raw) - set(LOCAL_PROTECTION_DOMAINS))
    if unknown:
        raise ValueError("protected_records contains unsupported domains: " + ", ".join(unknown))
    result: dict[str, tuple[int, ...]] = {}
    for domain, ids in raw.items():
        result[domain] = parse_int_ids(ids)
    return result


def expand_domain_dependencies(domains: tuple[str, ...]) -> tuple[str, ...]:
    """Return selected domains plus prerequisites in deterministic adapter order."""
    selected = set(domains)
    changed = True
    while changed:
        changed = False
        for domain in tuple(selected):
            for dependency in DOMAIN_DEPENDENCIES.get(domain, set()):
                if dependency not in selected:
                    selected.add(dependency)
                    changed = True
    return tuple(domain for domain in SUPPORTED_DOMAINS if domain in selected)


def resolve_domains(settings: Mapping[str, Any]) -> tuple[str, ...]:
    scope = str(settings.get("replication_scope", "legacy")).strip().lower()
    if scope == "full":
        return tuple(FULL_DOMAINS)
    if scope == "basic":
        return parse_csv(DEFAULT_DOMAINS)
    if scope == "iptv_content":
        return IPTV_CONTENT_DOMAINS
    domains = parse_csv(settings.get("domains", DEFAULT_DOMAINS))
    if scope == "custom":
        return expand_domain_dependencies(domains)
    return domains


def build_storage_options(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate legacy JSON while preferring explicit setup-assistant fields."""
    options = parse_json_object(settings.get("storage_options", "{}"), "storage_options")
    explicit = {
        "timeout_seconds": settings.get("storage_timeout_seconds"),
        "ca_path": settings.get("storage_ca_path"),
        "known_hosts_path": settings.get("sftp_known_hosts_path") or DEFAULT_KNOWN_HOSTS_PATH,
        "private_key": settings.get("sftp_private_key"),
        "private_key_passphrase": settings.get("sftp_private_key_passphrase"),
        "region": settings.get("s3_region"),
        "addressing_style": settings.get("s3_addressing_style"),
        "prefix": settings.get("s3_prefix"),
        "session_token": settings.get("s3_session_token"),
        "domain": settings.get("smb_domain"),
    }
    for key, value in explicit.items():
        if value not in (None, ""):
            options[key] = value
    if "storage_allow_insecure_http" in settings:
        options["allow_insecure_http"] = as_bool(settings["storage_allow_insecure_http"])
    options.setdefault("require_encryption", True)
    options.setdefault("timeout_seconds", 20)
    return options


def storage_probe_config(settings: Mapping[str, Any]) -> "ReplicationConfig":
    """Build an isolated configuration used exclusively for a storage probe.

    A connection test must be available before the operator has chosen a real
    cluster name, a peer or an HMAC secret. The returned configuration retains
    the submitted storage fields, but deliberately uses a non-persisted test
    identity. It must never be used by export, preview or apply operations.
    """
    candidate = dict(settings)
    candidate.update({
        "node_id": "storage-test",
        "cluster_id": "storage-test",
        "role": "leader",
        "mode": "shared_storage",
        "shared_secret": "storage-test-secret-which-is-never-persisted",
        "replication_scope": "custom",
        "domains": "output_profiles",
        "core_setting_keys": "",
        "state_path": str(candidate.get("state_path") or "/data/failovarr-state"),
        "shared_path": str(candidate.get("shared_path") or "/data/redundancy"),
    })
    return ReplicationConfig.from_settings(candidate)


def configuration_issues(settings: Mapping[str, Any]) -> list[ConfigValidationError]:
    """Return safe, field-specific setup blockers without parsing secrets.

    This is intentionally lighter than full validation so Status can be useful
    on a fresh install instead of failing while it tries to construct an engine.
    """
    issues: list[ConfigValidationError] = []
    for field, label in (("node_id", "Node name"), ("cluster_id", "Cluster name")):
        value = str(settings.get(field, "")).strip()
        if not value:
            issues.append(ConfigValidationError(field, "required", f"{label} is required."))
        elif len(value) > 64:
            issues.append(ConfigValidationError(field, "too_long", f"{label} must not exceed 64 characters."))
        elif not IDENTIFIER_PATTERN.fullmatch(value):
            issues.append(ConfigValidationError(field, "invalid_characters", f"{label} may contain only letters, digits, dots, underscores and hyphens."))
    settings = normalize_redundancy_mode(settings)
    secret = str(settings.get("shared_secret", ""))
    if not secret:
        issues.append(ConfigValidationError("shared_secret", "required", "Shared cluster secret is required."))
    return issues


def validation_error_payload(exc: Exception, *, operation: str) -> dict[str, str]:
    """Make plugin/API errors explainable without returning configuration data."""
    if isinstance(exc, ConfigValidationError):
        return {
            "status": "error", "code": exc.code, "field": exc.field,
            "message": f"{operation}: {exc}",
        }
    return {"status": "error", "code": "operation_failed", "message": f"{operation}: {exc}"}


def _validate_identifier(value: str, field: str, label: str) -> None:
    if not value:
        raise ConfigValidationError(field, "required", f"{label} is required.")
    if len(value) > 64:
        raise ConfigValidationError(field, "too_long", f"{label} must not exceed 64 characters.")
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ConfigValidationError(
            field, "invalid_characters",
            f"{label} may contain only letters, digits, dots, underscores and hyphens.",
        )


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class ReplicationConfig:
    node_id: str
    cluster_id: str
    role: str
    mode: str
    shared_path: str
    storage_backend: str
    storage_endpoint: str
    storage_container: str
    storage_username: str
    storage_password: str
    storage_options: dict[str, Any]
    state_path: str
    peer_url: str
    peer_node_id: str
    shared_secret: str
    domains: tuple[str, ...]
    core_setting_keys: tuple[str, ...]
    client_identity_users: tuple[str, ...]
    local_overrides: dict[str, Any]
    protected_output_profile_ids: tuple[int, ...]
    protected_records: dict[str, tuple[int, ...]]
    new_output_profile_policy: str
    new_stream_profile_policy: str
    new_epg_source_policy: str
    new_m3u_account_policy: str
    allow_deletes: bool
    automatic_apply: bool
    auto_start: bool
    interval_seconds: int
    bind_host: str
    bind_port: int
    client_access_mode: str
    client_vip: str
    vip_interface: str
    vip_prefix_length: int
    deployment_mode: str
    import_on_start: bool
    bundle_retention: int

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "ReplicationConfig":
        settings = normalize_redundancy_mode(settings)
        domains = resolve_domains(settings)
        unknown = sorted(set(domains) - set(SUPPORTED_DOMAINS))
        if unknown:
            raise ValueError(f"Unsupported domains: {', '.join(unknown)}")
        role = str(settings.get("role", "follower")).strip().lower()
        mode = str(settings.get("mode", "shared_storage")).strip().lower()
        storage_backend = str(settings.get("storage_backend", "filesystem")).strip().lower()
        if role not in {"leader", "follower"}:
            raise ConfigValidationError("role", "invalid_choice", "Node role must be Main or Follower.")
        if mode not in {"shared_storage", "direct", "hybrid"}:
            raise ConfigValidationError("mode", "invalid_choice", "Replication transport must be Shared storage, Direct Pull or Hybrid.")
        if storage_backend not in {"filesystem", "webdav", "s3", "sftp", "smb"}:
            raise ConfigValidationError("storage_backend", "invalid_choice", "Select a supported storage backend.")
        interval = max(10, int(settings.get("interval_seconds", 60)))
        protected_records = parse_protected_records(settings.get("protected_records", "{}"))
        legacy_output_protection = parse_int_ids(settings.get("protected_output_profile_ids", ""))
        protected_output_ids = tuple(dict.fromkeys((
            *legacy_output_protection,
            *protected_records.get("output_profiles", ()),
        )))
        if protected_output_ids:
            protected_records["output_profiles"] = protected_output_ids
        config = cls(
            node_id=str(settings.get("node_id", "")).strip(),
            cluster_id=str(settings.get("cluster_id", "")).strip(),
            role=role,
            mode=mode,
            shared_path=str(settings.get("shared_path", "/data/redundancy")).strip(),
            storage_backend=storage_backend,
            storage_endpoint=str(settings.get("storage_endpoint", "")).strip().rstrip("/"),
            storage_container=str(settings.get("storage_container", "")).strip().strip("/"),
            storage_username=str(settings.get("storage_username", "")).strip(),
            storage_password=str(settings.get("storage_password", "")),
            storage_options=build_storage_options(settings),
            state_path=str(settings.get("state_path", "/data/failovarr-state")).strip(),
            peer_url=str(settings.get("peer_url", "")).strip().rstrip("/"),
            peer_node_id=str(settings.get("peer_node_id", "")).strip(),
            shared_secret=str(settings.get("shared_secret", "")),
            domains=domains,
            core_setting_keys=parse_csv(settings.get("core_setting_keys", "")),
            client_identity_users=parse_csv(settings.get("client_identity_users", "*")),
            local_overrides=parse_json_object(settings.get("local_overrides", "{}"), "local_overrides"),
            protected_output_profile_ids=protected_output_ids,
            protected_records=protected_records,
            new_output_profile_policy=str(settings.get("new_output_profile_policy", "disabled")).strip().lower(),
            new_stream_profile_policy=str(settings.get("new_stream_profile_policy", "disabled")).strip().lower(),
            new_epg_source_policy=str(settings.get("new_epg_source_policy", "disabled")).strip().lower(),
            new_m3u_account_policy=str(settings.get("new_m3u_account_policy", "disabled")).strip().lower(),
            allow_deletes=as_bool(settings.get("allow_deletes", False)),
            automatic_apply=as_bool(settings.get("automatic_apply", False)),
            auto_start=as_bool(settings.get("auto_start", False)),
            interval_seconds=interval,
            bind_host=str(settings.get("bind_host", "0.0.0.0")).strip(),
            bind_port=int(settings.get("bind_port", 9192)),
            client_access_mode=str(settings.get("client_access_mode", "disabled")).strip().lower(),
            client_vip=str(settings.get("client_vip", "")).strip(),
            vip_interface=str(settings.get("vip_interface", "eth0")).strip(),
            vip_prefix_length=int(settings.get("vip_prefix_length", 24)),
            deployment_mode=str(settings.get("deployment_mode", "online")).strip().lower(),
            import_on_start=as_bool(settings.get("import_on_start", False)),
            bundle_retention=int(settings.get("bundle_retention", 3)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        _validate_identifier(self.node_id, "node_id", "Node name")
        _validate_identifier(self.cluster_id, "cluster_id", "Cluster name")
        if not self.shared_secret:
            raise ConfigValidationError("shared_secret", "required", "Shared cluster secret is required.")
        if not self.domains:
            raise ValueError("at least one replication domain is required")
        for domain, field in NEW_RECORD_POLICY_FIELDS.items():
            if getattr(self, field) not in {"disabled", "source", "block"}:
                raise ValueError(f"{field} must be disabled, source or block")
        # A Main node may later remove a domain from the signed scope.  Local
        # protection is deliberately retained for a future re-enable, but is
        # inactive while its domain is not replicated.  Rejecting the complete
        # follower profile here made harmless scope changes impossible.
        unknown_protection = set(self.protected_records) - set(LOCAL_PROTECTION_DOMAINS)
        if unknown_protection:
            raise ValueError("protected_records contains unsupported domains")
        unknown_override_domains = set(self.local_overrides) - set(self.domains)
        if unknown_override_domains:
            raise ValueError(
                "local_overrides contains domains that are not enabled: "
                + ", ".join(sorted(unknown_override_domains))
            )
        selected = set(self.domains)
        for domain in self.domains:
            missing = DOMAIN_DEPENDENCIES.get(domain, set()) - selected
            if missing:
                raise ValueError(
                    f"{domain} requires replication domains: {', '.join(sorted(missing))}"
                )
        if "core_settings" in self.domains and not self.core_setting_keys:
            raise ValueError("core_setting_keys may not be empty when core_settings is enabled")
        if "*" in self.client_identity_users and self.client_identity_users != ("*",):
            raise ValueError("client_identity_users may use * only by itself")
        if self.mode in {"direct", "hybrid"} and self.role == "follower" and not self.peer_url:
            raise ConfigValidationError("peer_url", "required", "Main peer URL is required for a Direct-Pull Follower.")
        if self.peer_url:
            parsed = urlsplit(self.peer_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ConfigValidationError("peer_url", "invalid_url", "Main peer URL must be an absolute HTTP(S) URL.")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("peer_url may not contain credentials, query parameters or a fragment")
            if parsed.path not in {"", "/"}:
                raise ValueError("peer_url may not contain a path")
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("peer_url contains an invalid port") from exc
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("peer_url contains an invalid port")
        if self.peer_node_id:
            _validate_identifier(self.peer_node_id, "peer_node_id", "Peer node name")
            if self.peer_node_id == self.node_id:
                raise ValueError("peer_node_id must differ from node_id")
        uses_shared_storage = self.mode in {"shared_storage", "hybrid"}
        if uses_shared_storage and self.storage_backend == "filesystem" and not self.shared_path:
            raise ConfigValidationError("shared_path", "required", "Shared bind-mount path is required for filesystem storage.")
        if uses_shared_storage and self.storage_backend != "filesystem":
            if not self.storage_endpoint or not self.storage_container:
                field = "storage_endpoint" if not self.storage_endpoint else "storage_container"
                label = "Storage endpoint" if field == "storage_endpoint" else "Storage directory / bucket"
                raise ConfigValidationError(field, "required", f"{label} is required for remote storage.")
            parsed_storage = urlsplit(self.storage_endpoint)
            required_scheme = {
                "webdav": {"https", "http"},
                "s3": {"https", "http"},
                "sftp": {"sftp"},
                "smb": {"smb"},
            }[self.storage_backend]
            if parsed_storage.scheme not in required_scheme or not parsed_storage.hostname:
                raise ConfigValidationError("storage_endpoint", "invalid_url", f"Storage endpoint must be a valid {self.storage_backend.upper()} URL.")
            if parsed_storage.username or parsed_storage.password or parsed_storage.query or parsed_storage.fragment:
                raise ValueError("storage_endpoint may not contain credentials, query parameters or a fragment")
            if "\\" in self.storage_container or any(
                part in {"", ".", ".."} for part in self.storage_container.split("/")
            ):
                raise ValueError("storage_container must be a safe relative path")
            if parsed_storage.scheme == "http" and not as_bool(self.storage_options.get("allow_insecure_http", False)):
                raise ValueError("HTTP storage requires storage_options.allow_insecure_http=true")
            if self.storage_backend == "sftp" and not self.storage_options.get("known_hosts_path"):
                raise ConfigValidationError("sftp_known_hosts_path", "required", "SFTP known_hosts path is required. Fetch and trust the server host key first.")
            ca_path = self.storage_options.get("ca_path", True)
            if ca_path is False:
                raise ValueError("TLS certificate verification may not be disabled")
            for option_name in ("ca_path", "known_hosts_path"):
                option_path = self.storage_options.get(option_name)
                if isinstance(option_path, str):
                    normalized = posixpath.normpath(option_path)
                    if not normalized.startswith("/data/"):
                        raise ValueError(f"storage_options.{option_name} must be below /data")
            if self.storage_backend == "smb" and self.storage_options.get("require_encryption", True) is not True:
                raise ValueError("SMB transport encryption may not be disabled")
        if not self.state_path:
            raise ValueError("state_path is required")
        paths = [("state_path", self.state_path)]
        if self.storage_backend == "filesystem":
            paths.append(("shared_path", self.shared_path))
        for field_name, path in paths:
            normalized = posixpath.normpath(path)
            if not normalized.startswith("/data/"):
                raise ValueError(f"{field_name} must be an absolute directory below /data")
        if self.storage_backend == "filesystem" and self.state_path == self.shared_path:
            raise ValueError("state_path must be node-local and different from shared_path")
        if not 1024 <= self.bind_port <= 65535:
            raise ValueError("bind_port must be between 1024 and 65535")
        if self.deployment_mode not in {"online", "cold_standby"}:
            raise ValueError("deployment_mode must be online or cold_standby")
        if self.bundle_retention != 3:
            raise ValueError("bundle_retention is fixed at 3")
        if self.client_access_mode not in {"disabled", "plugin_vip", "external_proxy"}:
            raise ValueError("client_access_mode must be disabled, plugin_vip or external_proxy")
        if self.client_access_mode == "plugin_vip":
            try:
                address = ipaddress.ip_address(self.client_vip)
            except ValueError as exc:
                raise ValueError("client_vip must be a valid IPv4 address") from exc
            if address.version != 4 or not address.is_private or address.is_multicast:
                raise ValueError("client_vip must be a private unicast IPv4 address")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", self.vip_interface):
                raise ValueError("vip_interface contains invalid characters")
            if not 1 <= self.vip_prefix_length <= 32:
                raise ValueError("vip_prefix_length must be between 1 and 32")
