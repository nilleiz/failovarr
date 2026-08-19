"""Non-secret promotion guard for client-visible Dispatcharr identities."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Iterable, Mapping

from .bundle import canonical_json


CLIENT_IDENTITY_FORMAT = 1
CLIENT_CUSTOM_KEYS = (
    "catchup_enabled",
    "epg_days",
    "epg_prev_days",
    "hide_adult_content",
    "output_format",
    "output_profile",
    "xc_password",
)


def _fingerprint(secret: str, username: str, contract: Mapping[str, Any]) -> str:
    key = hmac.new(
        secret.encode("utf-8"),
        b"failovarr/client-identity/v1",
        hashlib.sha256,
    ).digest()
    message = username.encode("utf-8") + b"\0" + canonical_json(contract)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def build_client_identity(
    records: Iterable[Mapping[str, Any]], secret: str,
) -> dict[str, Any]:
    """Return comparison material without serializing any credential values."""
    users = []
    seen: set[str] = set()
    for source in sorted(records, key=lambda item: str(item.get("username", ""))):
        username = str(source.get("username", "")).strip()
        if not username or username in seen:
            raise ValueError("Client identity usernames must be non-empty and unique")
        seen.add(username)
        contract = {key: source.get(key) for key in sorted(source) if key != "username"}
        users.append({
            "username": username,
            "fingerprint": _fingerprint(secret, username, contract),
            "has_api_key": bool(source.get("api_key")),
            "has_xc_password": bool(source.get("xc_password")),
        })
    return {"format": CLIENT_IDENTITY_FORMAT, "users": users}


def compare_client_identity(
    expected: Mapping[str, Any], actual: Mapping[str, Any],
) -> dict[str, list[str]]:
    if expected.get("format") != CLIENT_IDENTITY_FORMAT:
        raise ValueError(f"Unsupported client identity format: {expected.get('format')}")
    if actual.get("format") != CLIENT_IDENTITY_FORMAT:
        raise ValueError(f"Unsupported local client identity format: {actual.get('format')}")

    def by_name(value: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        rows = value.get("users")
        if not isinstance(rows, list):
            raise ValueError("Client identity users must be an array")
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("username"), str):
                raise ValueError("Malformed client identity user")
            username = row["username"]
            if username in result:
                raise ValueError(f"Duplicate client identity user: {username}")
            result[username] = row
        return result

    expected_users = by_name(expected)
    actual_users = by_name(actual)
    return {
        "missing": sorted(set(expected_users) - set(actual_users)),
        "unexpected": sorted(set(actual_users) - set(expected_users)),
        "different": sorted(
            username for username in set(expected_users) & set(actual_users)
            if not hmac.compare_digest(
                str(expected_users[username].get("fingerprint", "")),
                str(actual_users[username].get("fingerprint", "")),
            )
        ),
    }


def _selected_usernames(config) -> tuple[str, ...]:
    return tuple(config.client_identity_users)


def export_client_identity(config) -> dict[str, Any] | None:
    selected = _selected_usernames(config)
    if not selected:
        return None

    from apps.accounts.models import User

    queryset = User.objects.prefetch_related("channel_profiles").order_by("username")
    if selected != ("*",):
        queryset = queryset.filter(username__in=selected)

    records = []
    found = set()
    for user in queryset:
        custom = user.custom_properties or {}
        if selected == ("*",) and not (user.api_key or custom.get("xc_password")):
            continue
        found.add(user.username)
        records.append({
            "username": user.username,
            "api_key": user.api_key or "",
            "xc_password": custom.get("xc_password") or "",
            "is_active": bool(user.is_active),
            "user_level": int(user.user_level),
            "stream_limit": int(user.stream_limit),
            "custom_properties": {
                key: custom.get(key) for key in CLIENT_CUSTOM_KEYS if key in custom
            },
            "channel_profiles": sorted(user.channel_profiles.values_list("name", flat=True)),
        })
    if selected != ("*",):
        missing = sorted(set(selected) - found)
        if missing:
            raise ValueError("Configured client identity users do not exist: " + ", ".join(missing))
    return build_client_identity(records, config.shared_secret)


def require_matching_client_identity(expected: Mapping[str, Any] | None, config) -> dict[str, Any]:
    if not config.client_identity_users:
        return {"status": "disabled"}
    if expected is None:
        raise ValueError("Bundle has no client identity guard")
    actual = export_client_identity(config)
    differences = compare_client_identity(expected, actual or {})
    if any(differences.values()):
        parts = [f"{name}={','.join(values)}" for name, values in differences.items() if values]
        raise ValueError("Client identity mismatch: " + "; ".join(parts))
    return {"status": "matched", "users": len(expected.get("users", []))}
