"""Encrypted Main-to-Follower Dispatcharr user snapshots and local import rules."""

from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from .bundle import canonical_json


USER_SNAPSHOT_FORMAT = 1
USER_RULE_FORMAT = 1
ACTIVE_POLICIES = frozenset({"follow", "active", "inactive"})
RELATIONSHIPS = ("groups", "permissions", "channel_profiles")


def normalize_user_import_rules(
    value: Any = None, legacy_client_identity_users: Any = None,
) -> dict[str, Any]:
    """Validate persisted follower rules and migrate the old identity list."""
    if value is None:
        legacy = legacy_client_identity_users
        if isinstance(legacy, (list, tuple)):
            names = [str(item).strip() for item in legacy if str(item).strip()]
            if not names or names == ["*"]:
                return {"format": USER_RULE_FORMAT, "default_selected": True, "users": {}}
            if "*" in names:
                raise ValueError("client_identity_users may use * only by itself")
        elif legacy is None or not str(legacy).strip() or str(legacy).strip() == "*":
            names = []
            return {"format": USER_RULE_FORMAT, "default_selected": True, "users": {}}
        else:
            names = [item.strip() for item in str(legacy).split(",") if item.strip()]
        return {
            "format": USER_RULE_FORMAT,
            "default_selected": False,
            "users": {name: {"selected": True, "active": "follow"} for name in names},
        }
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("user_import_rules must contain valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("user_import_rules must be an object")
    if set(value) - {"format", "default_selected", "users"}:
        raise ValueError("user_import_rules contains unsupported fields")
    if type(value.get("format", USER_RULE_FORMAT)) is not int or value.get("format", USER_RULE_FORMAT) != USER_RULE_FORMAT:
        raise ValueError("user_import_rules has an unsupported format")
    default_selected = value.get("default_selected", True)
    if type(default_selected) is not bool:
        raise ValueError("user_import_rules.default_selected must be a boolean")
    raw_users = value.get("users", {})
    if not isinstance(raw_users, Mapping):
        raise ValueError("user_import_rules.users must be an object")
    users: dict[str, dict[str, Any]] = {}
    for username, raw_rule in raw_users.items():
        if not isinstance(username, str) or not username or username != username.strip():
            raise ValueError("user_import_rules contains an invalid username")
        if not isinstance(raw_rule, Mapping) or set(raw_rule) - {"selected", "active"}:
            raise ValueError(f"user_import_rules for {username!r} is invalid")
        rule: dict[str, Any] = {}
        if "selected" in raw_rule:
            if type(raw_rule["selected"]) is not bool:
                raise ValueError(f"user_import_rules selection for {username!r} must be a boolean")
            rule["selected"] = raw_rule["selected"]
        active = raw_rule.get("active", "follow")
        if not isinstance(active, str) or active not in ACTIVE_POLICIES:
            raise ValueError(f"user_import_rules active policy for {username!r} is invalid")
        rule["active"] = active
        users[username] = rule
    return {
        "format": USER_RULE_FORMAT,
        "default_selected": default_selected,
        "users": users,
    }


def _aad(context: Mapping[str, Any]) -> bytes:
    keys = ("cluster_id", "source_node", "sequence", "created_at")
    if any(key not in context for key in keys):
        raise ValueError("User snapshot encryption context is incomplete")
    return canonical_json({"purpose": "failovarr-user-snapshot-v1", **{key: context[key] for key in keys}})


def _encryption_key(secret: str, cluster_id: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise ValueError("A cluster secret is required to encrypt user accounts")
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:
        raise ValueError("Dispatcharr cryptography support is required for encrypted user snapshots") from exc
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=cluster_id.encode("utf-8"),
        info=b"failovarr/user-snapshot/aes-gcm/v1",
    ).derive(secret.encode("utf-8"))


def encrypt_user_snapshot(
    snapshot: Mapping[str, Any], secret: str, context: Mapping[str, Any],
) -> dict[str, Any]:
    """Encrypt user JSON with an authenticated context unique to this bundle."""
    if not isinstance(snapshot, Mapping) or type(snapshot.get("format")) is not int or snapshot.get("format") != USER_SNAPSHOT_FORMAT:
        raise ValueError("User snapshot has an unsupported format")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError("Dispatcharr cryptography support is required for encrypted user snapshots") from exc

    nonce = os.urandom(12)
    ciphertext = AESGCM(_encryption_key(secret, str(context["cluster_id"]))).encrypt(
        nonce, canonical_json(snapshot), _aad(context),
    )
    return {
        "format": 1,
        "algorithm": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_user_snapshot(
    encrypted: Mapping[str, Any], secret: str, context: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate and decrypt the encrypted user section."""
    if (
        not isinstance(encrypted, Mapping)
        or set(encrypted) != {"format", "algorithm", "nonce", "ciphertext"}
        or type(encrypted.get("format")) is not int
        or encrypted.get("format") != 1
        or encrypted.get("algorithm") != "AES-256-GCM"
        or not isinstance(encrypted.get("nonce"), str)
        or not isinstance(encrypted.get("ciphertext"), str)
    ):
        raise ValueError("Bundle encrypted user section is malformed")
    try:
        nonce = base64.b64decode(encrypted["nonce"], validate=True)
        ciphertext = base64.b64decode(encrypted["ciphertext"], validate=True)
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise ValueError
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        plaintext = AESGCM(_encryption_key(secret, str(context["cluster_id"]))).decrypt(
            nonce, ciphertext, _aad(context),
        )
        value = json.loads(plaintext)
    except Exception:
        raise ValueError("Encrypted user snapshot authentication or decoding failed") from None
    if not isinstance(value, dict):
        raise ValueError("Decrypted user snapshot must be an object")
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Dispatcharr user account contains a value that cannot be serialized safely")


def export_user_snapshot() -> dict[str, Any]:
    """Export all Dispatcharr user fields and stable relationship identities."""
    from apps.accounts.models import User

    concrete_fields = [field for field in User._meta.concrete_fields if not field.primary_key]
    if any(field.is_relation for field in concrete_fields):
        raise ValueError("Dispatcharr user schema has unsupported foreign-key fields")
    field_names = [field.name for field in concrete_fields]
    queryset = User.objects.prefetch_related(
        "groups", "user_permissions__content_type", "channel_profiles",
    ).order_by("username")
    users = []
    for user in queryset:
        fields = {field.name: _json_value(getattr(user, field.attname)) for field in concrete_fields}
        permissions = sorted(
            (
                {
                    "app_label": item.content_type.app_label,
                    "model": item.content_type.model,
                    "codename": item.codename,
                }
                for item in user.user_permissions.all()
            ),
            key=lambda item: (item["app_label"], item["model"], item["codename"]),
        )
        users.append({
            "fields": fields,
            "relationships": {
                "groups": sorted(user.groups.values_list("name", flat=True)),
                "permissions": permissions,
                "channel_profiles": sorted(user.channel_profiles.values_list("name", flat=True)),
            },
        })
    snapshot = {"format": USER_SNAPSHOT_FORMAT, "fields": field_names, "users": users}
    canonical_json(snapshot)
    return snapshot


def _validate_snapshot(snapshot: Mapping[str, Any], User: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(snapshot, Mapping) or set(snapshot) != {"format", "fields", "users"}:
        raise ValueError("Decrypted user snapshot has an invalid schema")
    if type(snapshot.get("format")) is not int or snapshot.get("format") != USER_SNAPSHOT_FORMAT:
        raise ValueError("Decrypted user snapshot has an unsupported format")
    fields = snapshot.get("fields")
    users = snapshot.get("users")
    target_fields = [field for field in User._meta.concrete_fields if not field.primary_key]
    if any(field.is_relation for field in target_fields):
        raise ValueError("Follower Dispatcharr user schema has unsupported foreign-key fields")
    expected_names = [field.name for field in target_fields]
    if not isinstance(fields, list) or fields != expected_names:
        raise ValueError("Main and Follower Dispatcharr user schemas differ; upgrade both nodes to compatible versions")
    if not isinstance(users, list):
        raise ValueError("Decrypted user snapshot users must be an array")
    field_by_name = {field.name: field for field in target_fields}
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, user in enumerate(users):
        if not isinstance(user, Mapping) or set(user) != {"fields", "relationships"}:
            raise ValueError(f"User record {index} has an invalid schema")
        source_fields = user.get("fields")
        relations = user.get("relationships")
        if not isinstance(source_fields, Mapping) or set(source_fields) != set(expected_names):
            raise ValueError(f"User record {index} has fields incompatible with Dispatcharr")
        values: dict[str, Any] = {}
        for name, field in field_by_name.items():
            try:
                raw = source_fields[name]
                field_type = field.get_internal_type()
                if field_type in {"BooleanField", "NullBooleanField"} and type(raw) is not bool and raw is not None:
                    raise ValueError
                if "IntegerField" in field_type and (type(raw) is not int):
                    raise ValueError
                if field_type in {"CharField", "TextField"} and raw is not None and not isinstance(raw, str):
                    raise ValueError
                if field_type == "JSONField" and name in {"avatar_config", "custom_properties"} and raw is not None and not isinstance(raw, dict):
                    raise ValueError
                values[name] = field.clean(raw, None)
            except Exception:
                # Exception details from framework validators can include the
                # rejected value. The action layer logs tracebacks, so keep the
                # chained error out of logs as well as the API response.
                raise ValueError(f"User record {index} has an invalid {name} field") from None
        username = values.get("username")
        if not isinstance(username, str) or not username or username != username.strip():
            raise ValueError(f"User record {index} has an invalid username")
        if username in seen:
            raise ValueError(f"User snapshot contains duplicate username {username!r}")
        seen.add(username)
        if not isinstance(relations, Mapping) or set(relations) != set(RELATIONSHIPS):
            raise ValueError(f"User {username!r} has invalid relationship data")
        clean_relations: dict[str, Any] = {}
        for relation in ("groups", "channel_profiles"):
            names = relations.get(relation)
            if (
                not isinstance(names, list)
                or any(not isinstance(name, str) or not name for name in names)
                or len(names) != len(set(names))
            ):
                raise ValueError(f"User {username!r} has invalid {relation} relationships")
            clean_relations[relation] = list(names)
        permissions = relations.get("permissions")
        if not isinstance(permissions, list):
            raise ValueError(f"User {username!r} has invalid permissions relationships")
        permission_keys: set[tuple[str, str, str]] = set()
        clean_permissions = []
        for permission in permissions:
            if (
                not isinstance(permission, Mapping)
                or set(permission) != {"app_label", "model", "codename"}
                or any(not isinstance(permission[key], str) or not permission[key] for key in ("app_label", "model", "codename"))
            ):
                raise ValueError(f"User {username!r} has invalid permission identity data")
            identity = (permission["app_label"], permission["model"], permission["codename"])
            if identity in permission_keys:
                raise ValueError(f"User {username!r} contains a duplicate permission")
            permission_keys.add(identity)
            clean_permissions.append({"app_label": identity[0], "model": identity[1], "codename": identity[2]})
        clean_relations["permissions"] = clean_permissions
        normalized.append({"fields": values, "relationships": clean_relations})
    return expected_names, normalized


def _selected(rule: Mapping[str, Any], default_selected: bool) -> bool:
    return rule.get("selected", default_selected)


def preview_user_snapshot(snapshot: Mapping[str, Any], rules: Any) -> dict[str, Any]:
    """Return names and status choices only; no account fields or credentials."""
    from apps.accounts.models import User

    _fields, users = _validate_snapshot(snapshot, User)
    normalized_rules = normalize_user_import_rules(rules)
    output = []
    for user in users:
        fields = user["fields"]
        username = fields["username"]
        rule = normalized_rules["users"].get(username, {})
        policy = rule.get("active", "follow")
        main_active = bool(fields.get("is_active", False))
        target_active = main_active if policy == "follow" else policy == "active"
        output.append({
            "username": username,
            "main_active": main_active,
            "selected": _selected(rule, normalized_rules["default_selected"]),
            "active_policy": policy,
            "target_active": target_active,
        })
    return {
        "status": "available",
        "default_selected": normalized_rules["default_selected"],
        "users": output,
        "message": f"{len(output)} Main user accounts are available for import.",
    }


def prepare_user_import(
    snapshot: Mapping[str, Any], rules: Any,
    available_channel_profile_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate the complete snapshot and all selected relationship dependencies."""
    from apps.accounts.models import User
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType
    from apps.channels.models import ChannelProfile

    _field_names, users = _validate_snapshot(snapshot, User)
    normalized_rules = normalize_user_import_rules(rules)
    selected_rows = []
    for row in users:
        username = row["fields"]["username"]
        rule = normalized_rules["users"].get(username, {})
        if _selected(rule, normalized_rules["default_selected"]):
            selected_rows.append((row, rule))
    group_names = {name for row, _ in selected_rows for name in row["relationships"]["groups"]}
    profile_names = {name for row, _ in selected_rows for name in row["relationships"]["channel_profiles"]}
    permission_keys = {
        (item["app_label"], item["model"], item["codename"])
        for row, _ in selected_rows for item in row["relationships"]["permissions"]
    }
    groups = {item.name: item for item in Group.objects.filter(name__in=group_names)}
    profiles = set(ChannelProfile.objects.filter(name__in=profile_names).values_list("name", flat=True))
    profiles.update(available_channel_profile_names or set())
    content_types = {
        (item.app_label, item.model): item
        for item in ContentType.objects.filter(
            app_label__in={key[0] for key in permission_keys},
            model__in={key[1] for key in permission_keys},
        )
    }
    permissions = {
        (item.content_type.app_label, item.content_type.model, item.codename): item
        for item in Permission.objects.select_related("content_type").filter(
            content_type__in=list(content_types.values()),
            codename__in={key[2] for key in permission_keys},
        )
    }
    prepared = []
    for row, rule in selected_rows:
        username = row["fields"]["username"]
        relations = row["relationships"]
        missing = []
        missing.extend(f"group {name!r}" for name in relations["groups"] if name not in groups)
        missing.extend(f"channel profile {name!r}" for name in relations["channel_profiles"] if name not in profiles)
        missing.extend(
            f"permission {item['app_label']}.{item['model']}.{item['codename']}"
            for item in relations["permissions"]
            if (item["app_label"], item["model"], item["codename"]) not in permissions
        )
        if missing:
            raise ValueError(
                f"User {username!r} references missing Follower dependencies: " + ", ".join(missing)
            )
        try:
            existing = User.objects.get(username=username)
        except User.DoesNotExist:
            existing = None
        values = dict(row["fields"])
        active = rule.get("active", "follow")
        if "is_active" in values:
            values["is_active"] = bool(values["is_active"]) if active == "follow" else active == "active"
        prepared.append({
            "username": username,
            "instance": existing,
            "values": values,
            "channel_profiles": list(relations["channel_profiles"]),
            "permissions": [
                (item["app_label"], item["model"], item["codename"])
                for item in relations["permissions"]
            ],
            "groups": list(relations["groups"]),
        })
    return prepared


def apply_user_import(prepared: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert selected users and relationships without changing local IDs."""
    from apps.accounts.models import User
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType
    from apps.channels.models import ChannelProfile

    created = updated = 0
    for item in prepared:
        user = item["instance"]
        if user is None:
            user = User(**item["values"])
            created += 1
        else:
            for name, value in item["values"].items():
                setattr(user, name, value)
            updated += 1
        user.save()
        groups = {group.name: group for group in Group.objects.filter(name__in=item["groups"])}
        profiles = {profile.name: profile for profile in ChannelProfile.objects.filter(name__in=item["channel_profiles"])}
        permission_keys = item["permissions"]
        content_types = {
            (content_type.app_label, content_type.model): content_type
            for content_type in ContentType.objects.filter(
                app_label__in={key[0] for key in permission_keys},
                model__in={key[1] for key in permission_keys},
            )
        }
        permissions = list(Permission.objects.select_related("content_type").filter(
            content_type__in=list(content_types.values()),
            codename__in={key[2] for key in permission_keys},
        ))
        resolved_permissions = {
            (permission.content_type.app_label, permission.content_type.model, permission.codename): permission
            for permission in permissions
        }
        if (
            set(groups) != set(item["groups"])
            or set(profiles) != set(item["channel_profiles"])
            or set(resolved_permissions) != set(permission_keys)
        ):
            raise ValueError(f"User {item['username']!r} dependencies changed during import")
        user.groups.set([groups[name] for name in item["groups"]])
        user.user_permissions.set(permissions)
        user.channel_profiles.set([profiles[name] for name in item["channel_profiles"]])
    return {"created": created, "updated": updated, "skipped": 0}
