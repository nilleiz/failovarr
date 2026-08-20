"""Field-aware Dispatcharr ORM adapters in dependency-safe apply order."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from .config import NEW_RECORD_POLICY_FIELDS, ReplicationConfig, deep_merge
from .planner import (
    IdentityKey,
    identity_fields,
    identity_label,
    identity_value,
    nullable_identity,
    plan_records,
    reconcile_surrogate_ids,
)


@dataclass(frozen=True)
class DomainSpec:
    name: str
    model: Any
    fields: tuple[str, ...]
    natural_key: IdentityKey
    unique_keys: tuple[IdentityKey, ...] = ()
    supports_locked: bool = False
    virtual_fields: tuple[str, ...] = ()
    surrogate_id: bool = False

    @property
    def model_fields(self) -> tuple[str, ...]:
        return tuple(field for field in self.fields if field not in self.virtual_fields)


def _specs() -> dict[str, DomainSpec]:
    # Delayed imports keep bundle/storage unit tests runnable outside Dispatcharr.
    from core.models import CoreSettings, OutputProfile, StreamProfile, UserAgent
    from apps.channels.models import (
        Channel,
        ChannelGroup,
        ChannelGroupM3UAccount,
        ChannelOverride,
        ChannelProfile,
        ChannelProfileMembership,
        ChannelStream,
        Logo,
        Stream,
    )
    from apps.epg.models import EPGData, EPGSource
    from apps.m3u.models import M3UAccount, M3UAccountProfile, M3UFilter, ServerGroup

    ordered = (
        DomainSpec(
            "user_agents", UserAgent,
            ("id", "name", "user_agent", "description", "is_active"),
            "name", ("user_agent",),
        ),
        DomainSpec(
            "stream_profiles", StreamProfile,
            ("id", "name", "command", "parameters", "locked", "is_active", "user_agent_id"),
            "name", supports_locked=True,
        ),
        DomainSpec(
            "output_profiles", OutputProfile,
            ("id", "name", "command", "parameters", "locked", "is_active"),
            # Output Profile IDs are part of generated M3U URLs. Names are
            # descriptive and Dispatcharr permits duplicates, so treating a
            # duplicate name as a replication identity was incorrect.
            "id", supports_locked=True,
        ),
        DomainSpec(
            "core_settings", CoreSettings,
            ("id", "key", "name", "value"), "key",
        ),
        DomainSpec(
            "server_groups", ServerGroup,
            ("id", "name"), "name",
        ),
        DomainSpec(
            "m3u_accounts", M3UAccount,
            (
                "id", "name", "server_url", "server_group_id", "max_streams",
                "is_active", "user_agent_id", "locked", "stream_profile_id",
                "account_type", "username", "password", "custom_properties",
                "refresh_interval", "stale_stream_days", "priority",
                "cron_expression",
            ),
            "name", supports_locked=True, virtual_fields=("cron_expression",),
        ),
        DomainSpec(
            "m3u_filters", M3UFilter,
            (
                "id", "m3u_account_id", "filter_type", "regex_pattern",
                "exclude", "order", "custom_properties",
            ),
            ("m3u_account_id", "id"),
        ),
        DomainSpec(
            "m3u_account_profiles", M3UAccountProfile,
            (
                "id", "m3u_account_id", "name", "is_default", "max_streams",
                "is_active", "search_pattern", "replace_pattern", "custom_properties",
            ),
            ("m3u_account_id", "name"),
        ),
        DomainSpec(
            "epg_sources", EPGSource,
            (
                "id", "name", "source_type", "url", "username", "password",
                "is_active", "refresh_interval", "custom_properties", "priority",
                "cron_expression",
            ),
            "name", virtual_fields=("cron_expression",),
        ),
        DomainSpec(
            "epg_data", EPGData,
            ("id", "tvg_id", "name", "icon_url", "epg_source_id"),
            ("epg_source_id", "tvg_id", "id"),
            (("epg_source_id", "tvg_id"),),
        ),
        DomainSpec(
            "channel_groups", ChannelGroup,
            ("id", "name"), "name",
        ),
        DomainSpec(
            "logos", Logo,
            ("id", "name", "url"), "url",
        ),
        DomainSpec(
            "streams", Stream,
            (
                "id", "name", "url", "m3u_account_id", "logo_url", "tvg_id",
                "channel_group_id", "stream_profile_id", "is_custom", "stream_hash",
                "is_adult", "custom_properties", "stream_id", "stream_chno",
                "is_catchup", "catchup_days",
            ),
            ("m3u_account_id", "stream_hash", "id"),
            ("stream_hash",),
        ),
        DomainSpec(
            "channels", Channel,
            (
                "id", "channel_number", "name", "logo_id", "channel_group_id",
                "tvg_id", "tvc_guide_stationid", "epg_data_id", "stream_profile_id",
                "uuid", "user_level", "is_adult", "auto_created",
                "auto_created_by_id", "is_catchup", "catchup_days",
                "hidden_from_output",
            ),
            "uuid",
        ),
        DomainSpec(
            "channel_overrides", ChannelOverride,
            (
                "id", "channel_id", "name", "channel_number", "channel_group_id",
                "logo_id", "tvg_id", "tvc_guide_stationid", "epg_data_id",
                "stream_profile_id",
            ),
            "channel_id",
        ),
        DomainSpec(
            "channel_profiles", ChannelProfile,
            ("id", "name"), "name",
        ),
        DomainSpec(
            "channel_profile_memberships", ChannelProfileMembership,
            ("id", "channel_profile_id", "channel_id", "enabled"),
            ("channel_profile_id", "channel_id"),
        ),
        DomainSpec(
            "channel_streams", ChannelStream,
            ("id", "channel_id", "stream_id", "order"),
            ("channel_id", "stream_id"),
            # Stream-Mapparr deliberately rebuilds this leaf relation. Its
            # primary key is not referenced by another replicated domain.
            surrogate_id=True,
        ),
        DomainSpec(
            "channel_group_m3u_accounts", ChannelGroupM3UAccount,
            (
                "id", "channel_group_id", "m3u_account_id", "custom_properties",
                "enabled", "auto_channel_sync", "auto_sync_channel_start",
                "auto_sync_channel_end",
            ),
            ("channel_group_id", "m3u_account_id"),
        ),
    )
    return {spec.name: spec for spec in ordered}


def _selected_specs(config: ReplicationConfig) -> list[DomainSpec]:
    available = _specs()
    selected = set(config.domains)
    return [spec for name, spec in available.items() if name in selected]


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (UUID, datetime, date, time, Decimal)):
        return str(value)
    return value


def _domain_queryset(spec: DomainSpec, config: ReplicationConfig):
    queryset = spec.model.objects.all()
    if spec.name == "core_settings":
        queryset = queryset.filter(key__in=config.core_setting_keys)
    return queryset


def _query_records(spec: DomainSpec, config: ReplicationConfig) -> list[dict[str, Any]]:
    queryset = _domain_queryset(spec, config)
    rows = [
        {key: _json_compatible(value) for key, value in row.items()}
        for row in queryset.order_by("pk").values(*spec.model_fields)
    ]
    if "cron_expression" in spec.virtual_fields:
        schedules = {}
        for instance in queryset.select_related("refresh_task__crontab").only(
            "id", "refresh_task__crontab__minute", "refresh_task__crontab__hour",
            "refresh_task__crontab__day_of_month", "refresh_task__crontab__month_of_year",
            "refresh_task__crontab__day_of_week",
        ):
            crontab = instance.refresh_task.crontab if instance.refresh_task_id else None
            schedules[instance.id] = (
                f"{crontab.minute} {crontab.hour} {crontab.day_of_month} "
                f"{crontab.month_of_year} {crontab.day_of_week}"
                if crontab else ""
            )
        for row in rows:
            row["cron_expression"] = schedules[row["id"]]
    return rows


def export_domains(config: ReplicationConfig) -> dict[str, list[dict[str, Any]]]:
    return {spec.name: _query_records(spec, config) for spec in _selected_specs(config)}


def apply_local_overrides(
    domain_name: str,
    records: list[Mapping[str, Any]],
    overrides: Mapping[str, Any],
    natural_key: IdentityKey,
    allowed_fields: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    domain_overrides = overrides.get(domain_name, {})
    if not isinstance(domain_overrides, Mapping):
        raise ValueError(f"Override for {domain_name} must be an object")
    result: list[dict[str, Any]] = []
    for source in records:
        record = dict(source)
        override = domain_overrides.get(str(record["id"]))
        if override is None:
            natural_value = identity_value(record, natural_key)
            natural_override_key = (
                json.dumps(natural_value, separators=(",", ":"), ensure_ascii=False)
                if isinstance(natural_value, tuple)
                else str(natural_value)
            )
            override = domain_overrides.get(natural_override_key)
        if override is not None:
            if not isinstance(override, Mapping):
                raise ValueError(f"Override for {domain_name}/{record['id']} must be an object")
            if "id" in override:
                raise ValueError(f"Override for {domain_name}/{record['id']} may not change id")
            if allowed_fields is not None:
                unknown = set(override) - set(allowed_fields)
                if unknown:
                    raise ValueError(
                        f"Override for {domain_name}/{record['id']} contains unknown fields: "
                        f"{', '.join(sorted(unknown))}"
                    )
            record = deep_merge(record, override)
            # Overrides may alter execution fields but never database identity.
            record["id"] = source["id"]
        result.append(record)
    return result


def _prepare_desired_records(
    spec: DomainSpec,
    incoming: list[dict[str, Any]],
    current: list[dict[str, Any]],
    config: ReplicationConfig,
) -> tuple[list[dict[str, Any]], list[int]]:
    desired = apply_local_overrides(
        spec.name, incoming, config.local_overrides, spec.natural_key, spec.fields,
    )
    current_by_id = {int(record["id"]): dict(record) for record in current}
    incoming_ids = {int(record["id"]) for record in desired}
    protected_records = getattr(config, "protected_records", {})
    protected = set(protected_records.get(spec.name, ()))
    # Backward compatibility for callers and existing node configuration.
    if spec.name == "output_profiles":
        protected.update(getattr(config, "protected_output_profile_ids", ()))
    desired = [
        current_by_id[int(record["id"])]
        if int(record["id"]) in protected and int(record["id"]) in current_by_id
        else record
        for record in desired
    ]

    # A protected local record must also survive an optional deletion run when
    # Main no longer carries that record.  Keeping it in desired makes the
    # regular planner preserve it without introducing a special delete path.
    desired.extend(
        record for record_id, record in current_by_id.items()
        if record_id in protected and record_id not in incoming_ids
    )

    policy_field = NEW_RECORD_POLICY_FIELDS.get(spec.name)
    if not policy_field:
        return desired, []

    new_ids = sorted(incoming_ids - set(current_by_id))
    # getattr keeps pre-0.6.4 test fixtures and integrations compatible. All
    # persisted configurations receive the explicit defaults in config.py.
    policy = getattr(config, policy_field, "disabled")
    if policy == "disabled":
        desired = [
            ({**record, "is_active": False} if int(record["id"]) in new_ids else record)
            for record in desired
        ]
    blocked = new_ids if policy == "block" else []
    return desired, blocked


def _validate_records(spec: DomainSpec, records: list[dict[str, Any]]) -> None:
    expected = set(spec.fields)
    seen_ids: set[int] = set()
    identity_keys = tuple(dict.fromkeys((spec.natural_key, *spec.unique_keys)))
    seen_unique: dict[IdentityKey, set[Any]] = {key: set() for key in identity_keys}
    for index, record in enumerate(records):
        actual = set(record)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                f"Domain {spec.name} record {index} has invalid schema "
                f"(missing={missing}, unknown={unknown})"
            )
        record_id = record["id"]
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise ValueError(f"Domain {spec.name} record {index} has an invalid id")
        for field in identity_fields(spec.natural_key):
            value = record[field]
            if isinstance(value, str) and not value.strip():
                raise ValueError(
                    f"Domain {spec.name} record {record_id} has an invalid {field}"
                )
            if isinstance(value, (dict, list, set)):
                raise ValueError(
                    f"Domain {spec.name} record {record_id} has an invalid {field}"
                )
        if record_id in seen_ids:
            raise ValueError(f"Domain {spec.name} contains duplicate id {record_id}")
        for unique_key in identity_keys:
            unique_value = identity_value(record, unique_key)
            try:
                hash(unique_value)
            except TypeError as exc:
                raise ValueError(
                    f"Domain {spec.name} record {record_id} has an invalid "
                    f"{identity_label(unique_key)}"
                ) from exc
            if nullable_identity(unique_value):
                continue
            if unique_value in seen_unique[unique_key]:
                raise ValueError(
                    f"Domain {spec.name} contains duplicate "
                    f"{identity_label(unique_key)} {unique_value!r}"
                )
            seen_unique[unique_key].add(unique_value)
        seen_ids.add(record_id)


def plan_domains(payload_domains: Mapping[str, Any], config: ReplicationConfig) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    expected = set(config.domains)
    unexpected = set(payload_domains) - expected
    if unexpected:
        raise ValueError(f"Bundle contains unconfigured domains: {', '.join(sorted(unexpected))}")

    for spec in _selected_specs(config):
        incoming = payload_domains.get(spec.name)
        if incoming is None:
            raise ValueError(f"Bundle is missing configured domain {spec.name}")
        if not isinstance(incoming, list) or not all(isinstance(row, dict) for row in incoming):
            raise ValueError(f"Domain {spec.name} must be a list of objects")
        _validate_records(spec, incoming)
        current = _query_records(spec, config)
        desired, blocked_new_ids = _prepare_desired_records(spec, incoming, current, config)
        if spec.surrogate_id:
            desired = reconcile_surrogate_ids(
                current, desired, natural_key=spec.natural_key,
            )
        _validate_records(spec, desired)
        plan = plan_records(
            current, desired, natural_key=spec.natural_key,
            allow_deletes=(
                config.allow_deletes
                or (
                    spec.name == "channel_streams"
                    and getattr(config, "mirror_channel_stream_assignments", False)
                )
            ),
            unique_keys=tuple(dict.fromkeys((spec.natural_key, *spec.unique_keys))),
        )
        for record_id in blocked_new_ids:
            plan["create"] = [row for row in plan["create"] if row["id"] != record_id]
            plan["conflicts"].append({
                "id": record_id, "reason": "new_record_requires_decision",
                "domain": spec.name,
            })
        if spec.supports_locked:
            locked_ids = {row["id"] for row in current if row.get("locked")}
            for update in list(plan["update"]):
                if update["id"] in locked_ids:
                    plan["conflicts"].append({
                        "id": update["id"], "reason": "record_is_locked",
                    })
                    plan["update"].remove(update)
            for record in list(plan["delete"]):
                if record["id"] in locked_ids:
                    plan["conflicts"].append({
                        "id": record["id"], "reason": "record_is_locked",
                    })
                    plan["delete"].remove(record)
        plans[spec.name] = plan
    return plans


def summarize_plan(plans: Mapping[str, Any]) -> dict[str, int]:
    result = {"create": 0, "update": 0, "delete": 0, "conflicts": 0}
    for plan in plans.values():
        for key in result:
            result[key] += len(plan.get(key, []))
    return result


def summarize_conflicts(plans: Mapping[str, Any], sample_size: int = 20) -> dict[str, Any]:
    """Return an operator-safe conflict report without record values."""
    result: dict[str, Any] = {}
    for name, plan in plans.items():
        conflicts = list(plan.get("conflicts", []))
        if not conflicts:
            continue
        reasons: dict[str, int] = {}
        for conflict in conflicts:
            reason = str(conflict.get("reason", "unknown"))
            reasons[reason] = reasons.get(reason, 0) + 1
        result[name] = {
            "count": len(conflicts),
            "reasons": reasons,
            "record_ids": [
                int(conflict["id"]) for conflict in conflicts[:sample_size]
                if isinstance(conflict.get("id"), int)
            ],
            "truncated": len(conflicts) > sample_size,
        }
    return result


def _bootstrap_preserved_ids(
    spec: DomainSpec,
    current: list[dict[str, Any]],
    desired: list[dict[str, Any]],
    config: ReplicationConfig,
) -> tuple[set[int], list[dict[str, Any]]]:
    """Preserve protected output profiles and byte-for-byte equal locked rows."""
    current_by_id = {int(row["id"]): row for row in current}
    desired_by_id = {int(row["id"]): row for row in desired}
    preserved: set[int] = set()
    conflicts: list[dict[str, Any]] = []

    protected_records = getattr(config, "protected_records", {})
    protected = set(protected_records.get(spec.name, ()))
    if spec.name == "output_profiles":
        protected.update(config.protected_output_profile_ids)
    preserved.update(record_id for record_id in protected if record_id in current_by_id)

    if spec.supports_locked:
        for record_id, row in current_by_id.items():
            if not row.get("locked"):
                continue
            incoming = desired_by_id.get(record_id)
            if incoming == row:
                preserved.add(record_id)
            elif record_id not in preserved:
                conflicts.append({"id": record_id, "reason": "locked_record_would_change"})
    return preserved, conflicts


DERIVED_EXTERNAL_MODELS = {
    "epg.EPGSourceIndex",
    "epg.ProgramData",
    "epg.SDScheduleMD5",
}
PRESERVED_EXTERNAL_MODELS = {
    "dispatcharr_channels.Recording",
    "dispatcharr_channels.RecurringRecordingRule",
}


def _external_reference_plan(
    specs: list[DomainSpec],
    preserved_ids: Mapping[str, set[int]],
    current_records: Mapping[str, list[dict[str, Any]]],
    desired_records: Mapping[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Plan safe cache purges and historical-reference remaps.

    Derived EPG data is node-local and can be rebuilt. DVR recordings and
    recurring rules are user data, so their Channel foreign keys are remapped
    by the stable Channel natural identity and never deleted.
    """
    selected_models = {spec.model for spec in specs}
    actions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for spec in specs:
        preserved = preserved_ids.get(spec.name, set())
        current = current_records[spec.name]
        deleted_ids = {
            int(row["id"]) for row in current if int(row["id"]) not in preserved
        }
        if not deleted_ids:
            continue
        current_by_id = {int(row["id"]): row for row in current}
        desired_by_identity = {
            identity_value(row, spec.natural_key): int(row["id"])
            for row in desired_records[spec.name]
        }
        for relation in spec.model._meta.related_objects:
            related_model = relation.related_model
            if related_model in selected_models:
                continue
            field = relation.field
            if not getattr(field, "name", None):
                continue
            lookup = {f"{field.name}__in": deleted_ids}
            try:
                queryset = related_model.objects.filter(**lookup)
                count = queryset.count()
            except (AttributeError, TypeError):
                continue
            if not count:
                continue
            label = related_model._meta.label
            if label in DERIVED_EXTERNAL_MODELS:
                actions.append({
                    "kind": "purge",
                    "model": related_model,
                    "field": field,
                    "parent_ids": deleted_ids,
                    "count": int(count),
                    "label": label,
                })
                continue
            if label in PRESERVED_EXTERNAL_MODELS and not field.unique:
                referenced_ids = set(queryset.values_list(field.attname, flat=True))
                remap: dict[int, int] = {}
                missing = 0
                for current_id in referenced_ids:
                    row = current_by_id.get(int(current_id))
                    target_id = (
                        desired_by_identity.get(identity_value(row, spec.natural_key))
                        if row else None
                    )
                    if target_id is None:
                        missing += queryset.filter(**{field.attname: current_id}).count()
                    else:
                        remap[int(current_id)] = int(target_id)
                if not missing:
                    actions.append({
                        "kind": "remap",
                        "model": related_model,
                        "field": field,
                        "mapping": remap,
                        "count": int(count),
                        "label": label,
                    })
                    continue
                reason = "historical_records_have_no_main_identity"
            else:
                reason = "external_records_reference_replaced_data"
            conflicts.append({
                "domain": spec.name,
                "related_model": label,
                "count": int(count),
                "reason": reason,
            })
    return actions, conflicts


def _reconcile_runtime(
    specs: list[DomainSpec], config: ReplicationConfig,
    desired_records: Mapping[str, list[dict[str, Any]]],
) -> None:
    """Rebuild node-local scheduler links and invalidate shared runtime caches.

    ORM writes intentionally bypass model save hooks so imports do not launch
    provider refreshes or auto-create related rows with different primary keys.
    Only deterministic node-local side effects are reconciled here inside the
    same database transaction.
    """
    names = {spec.name for spec in specs}

    if names & {"user_agents", "core_settings"}:
        from core.models import CoreSettings

        if "user_agents" in names:
            CoreSettings.invalidate_default_user_agent_cache()
        if "core_settings" in names:
            for key in config.core_setting_keys:
                CoreSettings.invalidate_group_cache(key)

    if "m3u_accounts" in names:
        from apps.m3u.models import M3UAccount
        from core.scheduling import create_or_update_periodic_task

        cron_by_id = {row["id"]: row.get("cron_expression", "") for row in desired_records["m3u_accounts"]}
        for account in M3UAccount.objects.filter(pk__in=cron_by_id).order_by("pk"):
            task = create_or_update_periodic_task(
                task_name=f"m3u_account-refresh-{account.id}",
                celery_task_path="apps.m3u.tasks.refresh_single_m3u_account",
                kwargs={"account_id": account.id},
                interval_hours=int(account.refresh_interval),
                cron_expression=cron_by_id[account.id],
                enabled=account.is_active,
            )
            if account.refresh_task_id != task.id:
                M3UAccount.objects.filter(pk=account.pk).update(refresh_task=task)

    if "epg_sources" in names:
        from apps.epg.models import EPGSource
        from core.scheduling import create_or_update_periodic_task

        cron_by_id = {row["id"]: row.get("cron_expression", "") for row in desired_records["epg_sources"]}
        for source in EPGSource.objects.filter(pk__in=cron_by_id).order_by("pk"):
            if source.source_type == "dummy":
                if source.refresh_task_id:
                    source.refresh_task.enabled = False
                    source.refresh_task.save(update_fields=["enabled"])
                continue
            task = create_or_update_periodic_task(
                task_name=f"epg_source-refresh-{source.id}",
                celery_task_path="apps.epg.tasks.refresh_epg_data",
                kwargs={"source_id": source.id},
                interval_hours=int(source.refresh_interval),
                cron_expression=cron_by_id[source.id],
                enabled=source.is_active,
            )
            if source.refresh_task_id != task.id:
                EPGSource.objects.filter(pk=source.pk).update(refresh_task=task)

    if names & {
        "channels", "channel_overrides", "channel_profile_memberships", "channel_streams",
    }:
        from apps.output.streaming_chunk_cache import invalidate_epg_chunk_cache

        invalidate_epg_chunk_cache()


def apply_domains(payload_domains: Mapping[str, Any], config: ReplicationConfig) -> dict[str, Any]:
    from django.db import transaction
    from django.core.management.color import no_style
    from django.db import connection

    specs = _selected_specs(config)
    desired_records = {}
    for spec in specs:
        current = _query_records(spec, config)
        desired_records[spec.name], _ = _prepare_desired_records(
            spec, payload_domains[spec.name], current, config,
        )
    plans = plan_domains(payload_domains, config)
    summary = summarize_plan(plans)
    if summary["conflicts"]:
        return {"status": "conflict", "summary": summary, "domains": plans}

    with transaction.atomic():
        for spec in specs:
            plan = plans[spec.name]
            creates = [
                spec.model(**{key: value for key, value in record.items() if key in spec.model_fields})
                for record in plan["create"]
            ]
            if creates:
                # Explicit IDs plus bulk_create avoid Dispatcharr signals that
                # would otherwise create related rows under different IDs.
                spec.model.objects.bulk_create(creates)
            for update in plan["update"]:
                changes = {
                    field: change["to"]
                    for field, change in update["changes"].items()
                    if field in spec.model_fields and field != "id"
                }
                if changes:
                    # QuerySet.update likewise avoids refresh jobs and other
                    # model signals during the atomic configuration import.
                    spec.model.objects.filter(pk=update["id"]).update(**changes)

        # Delete in reverse dependency order.
        for spec in reversed(specs):
            ids = [row["id"] for row in plans[spec.name]["delete"]]
            if ids:
                spec.model.objects.filter(pk__in=ids).delete()

        # Explicit primary keys do not advance PostgreSQL sequences. Reset all
        # touched model sequences before normal Dispatcharr creates new rows.
        statements = connection.ops.sequence_reset_sql(no_style(), [spec.model for spec in specs])
        if statements:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

        # Scheduler rows are part of the same transaction. Cache invalidation
        # may happen before commit, which is safe: a cache miss rebuilds from
        # the committed database state.
        _reconcile_runtime(specs, config, desired_records)

    new_disabled_record_ids = {}
    for domain, policy_field in NEW_RECORD_POLICY_FIELDS.items():
        if getattr(config, policy_field, "disabled") == "disabled" and domain in plans:
            created = sorted(int(row["id"]) for row in plans[domain]["create"])
            if created:
                new_disabled_record_ids[domain] = created
    return {
        "status": "applied",
        "summary": summary,
        "domains": plans,
        "new_disabled_record_ids": new_disabled_record_ids,
    }


def initialize_domains(payload_domains: Mapping[str, Any], config: ReplicationConfig) -> dict[str, Any]:
    """Replace the selected follower graph with source IDs in one transaction.

    This is deliberately separate from normal apply. It is intended only for
    explicit first-time follower initialization after independent IDs have
    already drifted.
    """
    from django.core.management.color import no_style
    from django.db import connection, transaction

    specs = _selected_specs(config)
    desired_records: dict[str, list[dict[str, Any]]] = {}
    current_records: dict[str, list[dict[str, Any]]] = {}
    preserved_ids: dict[str, set[int]] = {}
    locked_conflicts: dict[str, list[dict[str, Any]]] = {}

    expected = set(config.domains)
    unexpected = set(payload_domains) - expected
    missing = expected - set(payload_domains)
    if unexpected:
        raise ValueError(f"Bundle contains unconfigured domains: {', '.join(sorted(unexpected))}")
    if missing:
        raise ValueError(f"Bundle is missing configured domains: {', '.join(sorted(missing))}")

    for spec in specs:
        incoming = payload_domains[spec.name]
        if not isinstance(incoming, list) or not all(isinstance(row, dict) for row in incoming):
            raise ValueError(f"Domain {spec.name} must be a list of objects")
        _validate_records(spec, incoming)
        current = _query_records(spec, config)
        desired, blocked_new_ids = _prepare_desired_records(spec, incoming, current, config)
        _validate_records(spec, desired)
        if blocked_new_ids:
            locked_conflicts[spec.name] = [
                {"id": record_id, "reason": "new_record_requires_decision", "domain": spec.name}
                for record_id in blocked_new_ids
            ]
        preserve, locked = _bootstrap_preserved_ids(spec, current, desired, config)
        if locked:
            locked_conflicts.setdefault(spec.name, []).extend(locked)
        current_records[spec.name] = current
        desired_records[spec.name] = desired
        preserved_ids[spec.name] = preserve

    if locked_conflicts:
        plans = {
            name: {"create": [], "update": [], "delete": [], "conflicts": conflicts}
            for name, conflicts in locked_conflicts.items()
        }
        return {
            "status": "conflict",
            "summary": summarize_plan(plans),
            "conflicts": summarize_conflicts(plans),
        }

    external_actions, external_conflicts = _external_reference_plan(
        specs, preserved_ids, current_records, desired_records,
    )
    if external_conflicts:
        return {
            "status": "conflict",
            "summary": {
                "create": 0, "update": 0, "delete": 0,
                "conflicts": len(external_conflicts),
            },
            "external_dependencies": external_conflicts,
        }

    delete_count = sum(
        len(current_records[spec.name]) - len(preserved_ids[spec.name]) for spec in specs
    )
    create_count = sum(
        len([row for row in desired_records[spec.name] if int(row["id"]) not in preserved_ids[spec.name]])
        for spec in specs
    )

    with transaction.atomic():
        # Dispatcharr uses PostgreSQL with deferred Django foreign keys. Move
        # preserved historical records to their future Main IDs, purge only
        # explicitly classified derived caches, then perform raw deletes so
        # Django's application-level CASCADE collector cannot delete history.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        for action in external_actions:
            model = action["model"]
            field = action["field"]
            if action["kind"] == "purge":
                model.objects.filter(**{
                    f"{field.name}__in": action["parent_ids"],
                }).delete()
            else:
                for current_id, target_id in action["mapping"].items():
                    if current_id != target_id:
                        model.objects.filter(**{
                            field.attname: current_id,
                        }).update(**{field.attname: target_id})

        for spec in reversed(specs):
            queryset = _domain_queryset(spec, config)
            preserved = preserved_ids[spec.name]
            if preserved:
                queryset = queryset.exclude(pk__in=preserved)
            queryset._raw_delete(queryset.db)

        for spec in specs:
            preserved = preserved_ids[spec.name]
            creates = [
                spec.model(**{
                    key: value for key, value in record.items() if key in spec.model_fields
                })
                for record in desired_records[spec.name]
                if int(record["id"]) not in preserved
            ]
            if creates:
                spec.model.objects.bulk_create(creates)

        statements = connection.ops.sequence_reset_sql(no_style(), [spec.model for spec in specs])
        if statements:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        _reconcile_runtime(specs, config, desired_records)

    return {
        "status": "initialized",
        "summary": {
            "create": create_count,
            "update": 0,
            "delete": delete_count,
            "conflicts": 0,
        },
        "preserved": {
            name: sorted(record_ids) for name, record_ids in preserved_ids.items() if record_ids
        },
        "external": {
            "historical_records_remapped": sum(
                action["count"] for action in external_actions if action["kind"] == "remap"
            ),
            "derived_records_cleared": sum(
                action["count"] for action in external_actions if action["kind"] == "purge"
            ),
        },
        "new_disabled_record_ids": {
            domain: sorted(
                int(row["id"])
                for row in desired_records.get(domain, [])
                if int(row["id"]) not in {
                    int(current["id"])
                    for current in current_records.get(domain, [])
                }
            )
            for domain, policy_field in NEW_RECORD_POLICY_FIELDS.items()
            if getattr(config, policy_field, "disabled") == "disabled"
            and desired_records.get(domain)
        },
    }
