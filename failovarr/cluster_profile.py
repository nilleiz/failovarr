"""Portable Main-to-follower configuration profiles for the setup assistant."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


PROFILE_FORMAT = "failovarr-profile"
PROFILE_VERSION = 1
LEGACY_PROFILE_FORMATS = {"dispatcharr-redundancy-profile"}

# These values describe the shared transport contract.  Replication scope is
# deliberately *not* copied: domains, Settings groups and deletion policy are
# chosen locally by every Follower. Node identity, role, peer address, local
# state, record protection and automation are likewise node-local.
COMMON_FIELDS = (
    "cluster_id",
    "mode",
    "storage_backend",
    "storage_endpoint",
    "storage_container",
    "storage_username",
    "storage_timeout_seconds",
    "storage_allow_insecure_http",
    "s3_region",
    "s3_addressing_style",
    "s3_prefix",
    "smb_domain",
    "shared_secret",
    "client_identity_users",
    "redundancy_mode",
    # Kept in exported v1 profiles so a 0.6 node can still consume the
    # shared contract. New UI writes one redundancy_mode instead.
    "client_access_mode",
    "client_vip",
    "vip_prefix_length",
)
OPTIONAL_PASSWORD_FIELDS = ("storage_password", "s3_session_token")
# Profiles emitted by 0.6.7 included this Main-side field. Accepting but
# ignoring it keeps an upgrade from changing the follower's local policy.
LEGACY_IGNORED_FIELDS = ("allow_deletes",)


def export_cluster_profile(
    settings: Mapping[str, Any], include_storage_passwords: bool = False,
) -> dict[str, Any]:
    exported = {
        key: settings[key] for key in COMMON_FIELDS if key in settings
    }
    if include_storage_passwords:
        exported.update({
            key: settings[key]
            for key in OPTIONAL_PASSWORD_FIELDS
            if settings.get(key) not in (None, "")
        })
    return {
        "format": PROFILE_FORMAT,
        "version": PROFILE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "includes_storage_passwords": bool(include_storage_passwords),
        "settings": exported,
    }


def import_cluster_profile(
    current: Mapping[str, Any], profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile_format = profile.get("format")
    if profile_format not in {PROFILE_FORMAT, *LEGACY_PROFILE_FORMATS}:
        raise ValueError("The selected file is not a Failovarr or legacy Dispatcharr Redundancy profile")
    if profile.get("version") != PROFILE_VERSION:
        raise ValueError(f"Unsupported configuration profile version: {profile.get('version')!r}")
    incoming = profile.get("settings")
    if not isinstance(incoming, Mapping):
        raise ValueError("Configuration profile settings must be an object")
    allowed = set(COMMON_FIELDS) | set(OPTIONAL_PASSWORD_FIELDS) | set(LEGACY_IGNORED_FIELDS)
    unknown = sorted(set(incoming) - allowed)
    if unknown:
        raise ValueError(f"Configuration profile contains unsupported fields: {', '.join(unknown)}")

    result = dict(current)
    for key in COMMON_FIELDS:
        if key in incoming:
            result[key] = incoming[key]
    for key in OPTIONAL_PASSWORD_FIELDS:
        # A profile without passwords must never clear a locally configured
        # credential. Passwords are only written when explicitly included.
        if key in incoming and incoming[key] not in (None, ""):
            result[key] = incoming[key]
    return result
