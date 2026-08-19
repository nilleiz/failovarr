"""Pure record planning logic, deliberately independent of Django."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeAlias


MISSING = object()
IdentityKey: TypeAlias = str | tuple[str, ...]


def identity_fields(key: IdentityKey) -> tuple[str, ...]:
    return (key,) if isinstance(key, str) else tuple(key)


def identity_label(key: IdentityKey) -> str:
    return "+".join(identity_fields(key))


def identity_value(record: Mapping[str, Any], key: IdentityKey) -> Any:
    fields = identity_fields(key)
    values = tuple(record.get(field) for field in fields)
    return values[0] if len(values) == 1 else values


def nullable_identity(value: Any) -> bool:
    return value is None or (isinstance(value, tuple) and any(item is None for item in value))


def _display(value: Any) -> Any:
    return {"missing": True} if value is MISSING else value


def normalized_record(record: Mapping[str, Any], ignored: Iterable[str] = ()) -> dict[str, Any]:
    ignored_set = set(ignored)
    return {key: value for key, value in record.items() if key not in ignored_set}


def plan_records(
    current: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
    *, natural_key: IdentityKey, allow_deletes: bool,
    unique_keys: Iterable[IdentityKey] | None = None,
) -> dict[str, Any]:
    current_rows = [dict(row) for row in current]
    incoming_rows = [dict(row) for row in incoming]
    current_by_id = {int(row["id"]): row for row in current_rows}
    incoming_by_id = {int(row["id"]): row for row in incoming_rows}
    if len(current_by_id) != len(current_rows):
        raise ValueError("Current records contain duplicate IDs")
    if len(incoming_by_id) != len(incoming_rows):
        raise ValueError("Incoming records contain duplicate IDs")
    unique_keys = (natural_key,) if unique_keys is None else tuple(unique_keys)
    for unique_key in unique_keys:
        incoming_values = [
            value for row in incoming_rows
            if not nullable_identity(value := identity_value(row, unique_key))
        ]
        if len(set(incoming_values)) != len(incoming_values):
            raise ValueError(
                f"Incoming records contain duplicate {identity_label(unique_key)} values"
            )
    create: list[dict[str, Any]] = []
    update: list[dict[str, Any]] = []
    delete: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    current_unique = {
        unique_key: {
            value: int(row["id"]) for row in current_rows
            if not nullable_identity(value := identity_value(row, unique_key))
        }
        for unique_key in unique_keys
    }
    for record_id, desired in sorted(incoming_by_id.items()):
        existing = current_by_id.get(record_id)
        desired_natural = identity_value(desired, natural_key)
        unique_conflict = False
        for unique_key in unique_keys:
            desired_value = identity_value(desired, unique_key)
            if nullable_identity(desired_value):
                continue
            other_id = current_unique[unique_key].get(desired_value)
            if other_id is not None and other_id != record_id:
                conflicts.append({
                    "id": record_id,
                    "reason": (
                        "natural_key_is_used_by_another_id"
                        if unique_key == natural_key else "unique_key_is_used_by_another_id"
                    ),
                    "field": identity_label(unique_key),
                    "value": desired_value,
                    "existing_id": other_id,
                })
                unique_conflict = True
                break
        if unique_conflict:
            continue
        if existing is None:
            create.append(desired)
            continue
        if identity_value(existing, natural_key) != desired_natural:
            conflicts.append({
                "id": record_id,
                "reason": "id_has_different_natural_key",
                "field": identity_label(natural_key),
                "current": identity_value(existing, natural_key),
                "desired": desired_natural,
            })
            continue
        changes = {
            key: {"from": _display(existing.get(key, MISSING)), "to": value}
            for key, value in desired.items()
            if key != "id" and existing.get(key, MISSING) != value
        }
        if changes:
            update.append({"id": record_id, "changes": changes, "record": desired})

    if allow_deletes:
        for record_id, existing in sorted(current_by_id.items()):
            if record_id not in incoming_by_id:
                delete.append(existing)

    return {"create": create, "update": update, "delete": delete, "conflicts": conflicts}
