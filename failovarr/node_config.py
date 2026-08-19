"""Persistent node-local setup configuration independent of Dispatcharr UI fields."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .config import (
    CORE_SETTING_GROUPS, DEFAULT_KNOWN_HOSTS_PATH, LEGACY_KNOWN_HOSTS_PATH,
    IPTV_CONTENT_DOMAINS, LOCAL_PROTECTION_DOMAINS, SUPPORTED_DOMAINS, as_bool,
    normalize_redundancy_mode, parse_csv, parse_int_ids, parse_protected_records,
)


CONFIG_PATH = Path(os.environ.get(
    "FAILOVARR_CONFIG_PATH", "/data/failovarr-config.json",
))
LEGACY_CONFIG_PATH = Path(os.environ.get(
    "DISPATCHARR_REDUNDANCY_CONFIG_PATH", "/data/dispatcharr-redundancy-config.json",
))


def _read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Node-local Failovarr configuration is not an object")
    return value


def load_node_config(fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return _read_config(CONFIG_PATH)
    # Failovarr is an in-place successor of Dispatcharr Redundancy.  Migrate a
    # valid legacy file only when the new location is still absent; never
    # overwrite either an explicit Failovarr configuration or the source file.
    if LEGACY_CONFIG_PATH != CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
        legacy = _read_config(LEGACY_CONFIG_PATH)
        save_node_config(legacy, ownership_source=LEGACY_CONFIG_PATH)
        return legacy
    return dict(fallback or {})


def _ownership_for_write(ownership_source: Path | None = None) -> tuple[int, int] | None:
    """Preserve a readable node-local owner across an atomic replacement.

    Dispatcharr's ZIP importer can discover a plugin as root, while the web
    workers subsequently run as ``dispatch``. Prefer the existing Failovarr
    file, then a one-time migration source; a fresh file remains owned by the
    process that creates it.
    """
    for candidate in (CONFIG_PATH, ownership_source):
        if candidate is None:
            continue
        try:
            metadata = candidate.stat()
        except FileNotFoundError:
            continue
        return metadata.st_uid, metadata.st_gid
    return None


def save_node_config(value: Mapping[str, Any], *, ownership_source: Path | None = None) -> None:
    owner = _ownership_for_write(ownership_source)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{CONFIG_PATH.name}.", suffix=".tmp", dir=CONFIG_PATH.parent,
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if owner is not None and hasattr(os, "fchown"):
            os.fchown(descriptor, owner[0], owner[1])
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(dict(value), handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


_NATIVE_SCALAR_FIELDS = {
    "setup_public_url", "node_id", "cluster_id", "role", "redundancy_mode", "deployment_mode", "client_access_mode",
    "mode", "peer_url", "peer_node_id", "storage_backend", "shared_path", "storage_endpoint",
    "storage_container", "storage_username", "storage_timeout_seconds", "sftp_known_hosts_path",
    "s3_region", "s3_addressing_style", "s3_prefix", "smb_domain", "new_output_profile_policy",
    "new_stream_profile_policy", "new_epg_source_policy", "new_m3u_account_policy",
    "automatic_apply", "allow_deletes", "auto_start", "import_on_start", "interval_seconds",
    "confirm", "replication_scope", "state_path", "client_vip", "vip_interface", "vip_prefix_length",
    "storage_ca_path", "storage_allow_insecure_http", "client_identity_users",
}


def _native_overlay(visible: Mapping[str, Any], configured: Mapping[str, Any]) -> dict[str, Any]:
    """Translate flat native plugin settings into the canonical node config.

    Secrets are deliberately absent from this overlay. A partially saved dynamic
    field list only changes fields explicitly present, leaving all other local
    configuration intact.
    """
    result = dict(configured)
    target_role = str(visible.get("role", configured.get("role", "follower"))).lower()
    for key in _NATIVE_SCALAR_FIELDS:
        if key == "replication_scope" and target_role == "leader":
            continue
        if key in visible:
            result[key] = visible[key]
    role = target_role
    domains = set(parse_csv(configured.get("domains", "")))
    saw_domain = False
    for domain in SUPPORTED_DOMAINS:
        key = f"replicate_{domain}"
        if key in visible:
            saw_domain = True
            if as_bool(visible[key]):
                domains.add(domain)
            else:
                domains.discard(domain)
    if saw_domain and role == "follower":
        result["domains"] = ",".join(domain for domain in SUPPORTED_DOMAINS if domain in domains)
    core = set(parse_csv(configured.get("core_setting_keys", "")))
    saw_core = False
    for key in CORE_SETTING_GROUPS:
        field = f"replicate_core_{key}"
        if field in visible:
            saw_core = True
            if as_bool(visible[field]):
                core.add(key)
            else:
                core.discard(key)
    if saw_core and role == "follower":
        result["core_setting_keys"] = ",".join(key for key in CORE_SETTING_GROUPS if key in core)
    # This preset deliberately excludes generic Dispatcharr settings and
    # Output Profiles. Persist its visible checkbox state so switching to
    # Custom selection later starts from the same meaningful baseline.
    if role == "follower" and result.get("replication_scope") == "iptv_content":
        result["domains"] = ",".join(IPTV_CONTENT_DOMAINS)
        result["core_setting_keys"] = ""
    try:
        protected = {key: list(value) for key, value in parse_protected_records(configured.get("protected_records", {})).items()}
    except ValueError:
        protected = {}
    # Keep the 0.5.x Output Profile protection field lossless while exposing
    # all four supported record domains through the native checkboxes.
    legacy_outputs = parse_int_ids(configured.get("protected_output_profile_ids", ""))
    if legacy_outputs:
        protected["output_profiles"] = sorted(set(protected.get("output_profiles", [])) | set(legacy_outputs))
    for domain in LOCAL_PROTECTION_DOMAINS:
        if role != "follower":
            continue
        existing = set(protected.get(domain, []))
        for key, value in visible.items():
            prefix = f"protect_{domain}_"
            if key.startswith(prefix):
                try:
                    record_id = int(key[len(prefix):])
                except ValueError:
                    continue
                if as_bool(value):
                    existing.add(record_id)
                else:
                    existing.discard(record_id)
        if existing:
            protected[domain] = sorted(existing)
        else:
            protected.pop(domain, None)
    result["protected_records"] = protected
    result["protected_output_profile_ids"] = ",".join(str(value) for value in protected.get("output_profiles", []))
    if result.get("sftp_known_hosts_path") == LEGACY_KNOWN_HOSTS_PATH:
        result["sftp_known_hosts_path"] = DEFAULT_KNOWN_HOSTS_PATH
    return normalize_redundancy_mode(result)


def native_settings_snapshot(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a non-secret mirror suitable for Dispatcharr's Plugin Settings."""
    source = normalize_redundancy_mode(settings)
    snapshot = {key: source[key] for key in _NATIVE_SCALAR_FIELDS if key in source}
    domains = set(parse_csv(source.get("domains", "")))
    snapshot.update({f"replicate_{domain}": domain in domains for domain in SUPPORTED_DOMAINS})
    core = set(parse_csv(source.get("core_setting_keys", "")))
    snapshot.update({f"replicate_core_{key}": key in core for key in CORE_SETTING_GROUPS})
    try:
        protected = parse_protected_records(source.get("protected_records", {}))
    except ValueError:
        protected = {}
    for domain, ids in protected.items():
        for record_id in ids:
            snapshot[f"protect_{domain}_{record_id}"] = True
    return snapshot


def effective_settings(dispatcharr_settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    visible = dict(dispatcharr_settings or {})
    configured = load_node_config(visible)
    return _native_overlay(visible, configured)
