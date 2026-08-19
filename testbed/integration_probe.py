"""Disposable-container probe for Failovarr.

Copy this file to a lab container and execute it through ``manage.py shell``.
It uses synthetic profiles and a synthetic HMAC key only.
"""

from __future__ import annotations

import json
import logging
import os
import pwd
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/data/plugins")

from apps.plugins.loader import PluginManager
from apps.plugins.models import PluginConfig
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
from apps.accounts.models import User
from core.models import CoreSettings, OutputProfile
from failovarr.engine import ReplicationEngine
from failovarr.config import FULL_DOMAINS
from failovarr.node_config import CONFIG_PATH, save_node_config
from failovarr.transport import fetch_status


ACTION = os.environ.get("FAILOVARR_TEST_ACTION", "inspect")
PLUGIN_KEY = "failovarr"
SHARED_SECRET = "integration-test-secret-32-characters"
SHARED_PATH = "/data/redundancy"
STATE_PATH = "/data/failovarr-state"
MAIN_URL = os.environ.get("LAB_MAIN_URL", "http://main:9192")
SLAVE_URL = os.environ.get("LAB_SLAVE_URL", "http://slave:9192")
GRAPH_IDS = range(7301, 7316)
GRAPH_DOMAINS = ",".join((
    "user_agents",
    "stream_profiles",
    "server_groups",
    "m3u_accounts",
    "m3u_account_profiles",
    "m3u_filters",
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
))


def emit(value, condition=True, failure="Integration probe assertion failed"):
    print("FAILOVARR_PROBE=" + json.dumps(value, sort_keys=True))
    if not condition:
        raise RuntimeError(failure)


def write_background_service_diagnostic(settings):
    """Persist redacted, synthetic-only service state for CI failure analysis."""
    try:
        import failovarr as redundancy_plugin

        service = getattr(redundancy_plugin, "_service", None)
        report = {
            "service": service.status() if service is not None else {"running": False, "missing": True},
            "peer": ReplicationEngine(settings).peer_status(),
        }
    except Exception as exc:
        report = {"diagnostic_error": str(exc)}
    path = Path(STATE_PATH) / "ci-background-service.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


def plugin_settings(role: str):
    settings = {
        "node_id": "lab-main" if role == "leader" else "lab-slave",
        "cluster_id": "dispatcharr-lab",
        "role": role,
        "mode": "shared_storage",
        "redundancy_mode": "external_proxy",
        "shared_path": SHARED_PATH,
        "state_path": STATE_PATH,
        "peer_url": "",
        "shared_secret": SHARED_SECRET,
        "domains": "output_profiles",
        "core_setting_keys": "",
        "local_overrides": "{}",
        "protected_output_profile_ids": "303" if role == "follower" else "",
        "new_output_profile_policy": "disabled",
        "confirm": True,
        "allow_deletes": False,
        "automatic_apply": False,
        "auto_start": False,
        "interval_seconds": 60,
        "bind_host": "0.0.0.0",
        "bind_port": 9192,
    }
    return settings


def direct_settings(role: str):
    settings = plugin_settings(role)
    settings["mode"] = "direct"
    settings["peer_url"] = SLAVE_URL if role == "leader" else MAIN_URL
    settings["peer_node_id"] = "lab-slave" if role == "leader" else "lab-main"
    return settings


def handoff_settings(role: str):
    settings = direct_settings(role)
    settings["automatic_apply"] = True
    settings["interval_seconds"] = 10
    settings["client_access_mode"] = "external_proxy"
    return settings


def cold_settings(role: str):
    settings = plugin_settings(role)
    settings.update({
        "redundancy_mode": "cold_standby",
        "auto_start": True,
        "import_on_start": True,
        "automatic_apply": False,
    })
    return settings


def reset_handoff_state(role: str, *, full_lab_reset: bool = False):
    engine = ReplicationEngine(handoff_settings(role))
    with engine.state_store.exclusive_lock():
        state = {} if full_lab_reset else engine.state_store.read_state()
        if not full_lab_reset:
            for key in ("authoritative", "handoff", "client_serving"):
                state.pop(key, None)
        engine.state_store.write_state(state)
        if full_lab_reset:
            engine.outbound_store.latest_path.unlink(missing_ok=True)
            (Path(SHARED_PATH) / "latest.json").unlink(missing_ok=True)


def graph_settings(role: str):
    settings = plugin_settings(role)
    settings["domains"] = GRAPH_DOMAINS
    settings["local_overrides"] = "{}"
    settings["protected_output_profile_ids"] = ""
    settings["client_identity_users"] = "redundancy-fixture"
    return settings


def upsert_without_signals(model, record_id: int, **values):
    if not model.objects.filter(pk=record_id).update(**values):
        model.objects.bulk_create([model(id=record_id, **values)])


def cleanup_graph():
    # Delete only the dedicated synthetic IDs, in reverse dependency order.
    ChannelOverride.objects.filter(pk=7311).delete()
    ChannelProfileMembership.objects.filter(pk=7313).delete()
    ChannelStream.objects.filter(pk=7314).delete()
    Channel.objects.filter(pk=7310).delete()
    Stream.objects.filter(pk=7309).delete()
    ChannelGroupM3UAccount.objects.filter(pk=7315).delete()
    ChannelProfile.objects.filter(pk=7312).delete()
    Logo.objects.filter(pk=7308).delete()
    EPGData.objects.filter(pk=7306).delete()
    EPGSource.objects.filter(pk=7305).delete()
    M3UFilter.objects.filter(pk=7304).delete()
    M3UAccountProfile.objects.filter(pk=7303).delete()
    M3UAccount.objects.filter(pk=7302).delete()
    ChannelGroup.objects.filter(pk=7307).delete()
    ServerGroup.objects.filter(pk=7301).delete()


def prepare_graph():
    cleanup_graph()
    upsert_without_signals(ServerGroup, 7301, name="Synthetic provider group")
    upsert_without_signals(
        M3UAccount,
        7302,
        name="Synthetic provider",
        server_url="https://provider.invalid/playlist.m3u",
        server_group_id=7301,
        max_streams=2,
        is_active=False,
        user_agent_id=None,
        locked=False,
        stream_profile_id=None,
        account_type="STD",
        username="synthetic-user",
        password="synthetic-password",
        custom_properties={"fixture": True},
        refresh_interval=12,
        stale_stream_days=7,
        priority=3,
        refresh_task_id=None,
    )
    upsert_without_signals(
        M3UAccountProfile,
        7303,
        m3u_account_id=7302,
        name="Synthetic provider default",
        is_default=True,
        max_streams=2,
        is_active=True,
        search_pattern="^(.*)$",
        replace_pattern="$1",
        custom_properties={"fixture": True},
    )
    upsert_without_signals(
        M3UFilter,
        7304,
        m3u_account_id=7302,
        filter_type="group",
        regex_pattern="^Synthetic$",
        exclude=False,
        order=1,
        custom_properties={"fixture": True},
    )
    upsert_without_signals(
        EPGSource,
        7305,
        name="Synthetic EPG",
        source_type="xmltv",
        url="https://epg.invalid/guide.xml",
        username="synthetic-epg-user",
        password="synthetic-epg-password",
        is_active=False,
        refresh_interval=24,
        custom_properties={"fixture": True},
        priority=2,
        refresh_task_id=None,
    )
    upsert_without_signals(
        EPGData,
        7306,
        tvg_id="synthetic.channel",
        name="Synthetic Channel Guide",
        icon_url="https://assets.invalid/synthetic-guide.png",
        epg_source_id=7305,
    )
    upsert_without_signals(ChannelGroup, 7307, name="Synthetic Group")
    upsert_without_signals(
        Logo,
        7308,
        name="Synthetic Logo",
        url="https://assets.invalid/synthetic-logo.png",
    )
    upsert_without_signals(
        Stream,
        7309,
        name="Synthetic Stream",
        url="https://stream.invalid/live.ts",
        m3u_account_id=7302,
        logo_url="https://assets.invalid/synthetic-logo.png",
        tvg_id="synthetic.channel",
        channel_group_id=7307,
        stream_profile_id=None,
        is_custom=False,
        stream_hash="synthetic-stream-hash-7309",
        is_adult=False,
        custom_properties={"fixture": True},
        stream_id=7309,
        stream_chno=73.1,
        is_catchup=True,
        catchup_days=2,
    )
    upsert_without_signals(
        Channel,
        7310,
        channel_number=73.0,
        name="Synthetic Channel",
        logo_id=7308,
        channel_group_id=7307,
        tvg_id="synthetic.channel",
        tvc_guide_stationid="synthetic-station",
        epg_data_id=7306,
        stream_profile_id=None,
        uuid="00000000-0000-0000-0000-000000007310",
        user_level=0,
        is_adult=False,
        auto_created=True,
        auto_created_by_id=7302,
        is_catchup=True,
        catchup_days=2,
        hidden_from_output=False,
    )
    upsert_without_signals(
        ChannelOverride,
        7311,
        channel_id=7310,
        name="Synthetic Channel Override",
        channel_number=73.5,
        channel_group_id=7307,
        logo_id=7308,
        tvg_id="synthetic.channel.override",
        tvc_guide_stationid="synthetic-station",
        epg_data_id=7306,
        stream_profile_id=None,
    )
    upsert_without_signals(ChannelProfile, 7312, name="Synthetic Clients")
    upsert_without_signals(
        ChannelProfileMembership,
        7313,
        channel_profile_id=7312,
        channel_id=7310,
        enabled=True,
    )
    upsert_without_signals(
        ChannelStream,
        7314,
        channel_id=7310,
        stream_id=7309,
        order=0,
    )
    upsert_without_signals(
        ChannelGroupM3UAccount,
        7315,
        channel_group_id=7307,
        m3u_account_id=7302,
        custom_properties={"fixture": True},
        enabled=True,
        auto_channel_sync=True,
        auto_sync_channel_start=73.0,
        auto_sync_channel_end=79.0,
    )
    from core.scheduling import create_or_update_periodic_task

    m3u_task = create_or_update_periodic_task(
        task_name="m3u_account-refresh-7302",
        celery_task_path="apps.m3u.tasks.refresh_single_m3u_account",
        kwargs={"account_id": 7302},
        interval_hours=12,
        cron_expression="15 4 * * *",
        enabled=False,
    )
    M3UAccount.objects.filter(pk=7302).update(refresh_task=m3u_task)
    epg_task = create_or_update_periodic_task(
        task_name="epg_source-refresh-7305",
        celery_task_path="apps.epg.tasks.refresh_epg_data",
        kwargs={"source_id": 7305},
        interval_hours=24,
        cron_expression="30 3 * * 1-5",
        enabled=False,
    )
    EPGSource.objects.filter(pk=7305).update(refresh_task=epg_task)


def prepare_client_identity():
    User.objects.update_or_create(
        id=7316,
        defaults={
            "username": "redundancy-fixture",
            "password": "!",
            "api_key": "synthetic-api-key",
            "is_active": True,
            "user_level": 0,
            "stream_limit": 2,
            "custom_properties": {
                "xc_password": "synthetic-xc-password",
                "output_profile": 303,
                "output_format": "mpegts",
            },
        },
    )


def configure(role: str, settings=None):
    selected_settings = settings or plugin_settings(role)
    manager = PluginManager()
    manager.discover_plugins(sync_db=True, force_reload=False, use_cache=False)
    config = PluginConfig.objects.get(key=PLUGIN_KEY)
    config.enabled = True
    config.ever_enabled = True
    config.settings = selected_settings
    config.save(update_fields=["enabled", "ever_enabled", "settings", "updated_at"])
    # The production plugin intentionally prefers its node-local assistant
    # config over the tiny Dispatcharr bootstrap mask. Keep this synthetic
    # probe on the same path whenever it changes roles or transport modes.
    save_node_config(selected_settings)
    dispatch_user = pwd.getpwnam("dispatch")
    os.chown(CONFIG_PATH, dispatch_user.pw_uid, dispatch_user.pw_gid)
    manager.discover_plugins(sync_db=False, force_reload=True, use_cache=False)
    return manager


if ACTION == "inspect":
    PluginManager().discover_plugins(sync_db=True, force_reload=False, use_cache=False)
    emit({
        "plugins": list(PluginConfig.objects.values("key", "name", "version", "enabled")),
        "profiles": list(OutputProfile.objects.order_by("id").values("id", "name")),
    })

elif ACTION == "legacy_config_migration_verify":
    # Install-CiPlugin discovered Failovarr as root after the helper wrote a
    # dispatch-owned legacy file. This probe runs as dispatch and proves both
    # the migrated file ownership and the real Assistant action remain usable.
    metadata = CONFIG_PATH.stat()
    dispatch_user = pwd.getpwnam("dispatch")
    manager = PluginManager()
    manager.discover_plugins(sync_db=True, force_reload=False, use_cache=False)
    result = manager.run_action(PLUGIN_KEY, "start_setup_assistant")
    probe = {
        "owner": metadata.st_uid,
        "group": metadata.st_gid,
        "mode": oct(metadata.st_mode & 0o777),
        "assistant": result.get("status"),
        "legacy_present": Path("/data/dispatcharr-redundancy-config.json").exists(),
    }
    emit(probe, all((
        probe["owner"] == dispatch_user.pw_uid,
        probe["group"] == dispatch_user.pw_gid,
        probe["mode"] == "0o600",
        probe["assistant"] == "success",
        probe["legacy_present"],
    )))

elif ACTION == "prepare_main":
    reset_handoff_state("leader", full_lab_reset=True)
    OutputProfile.objects.update_or_create(
        id=303,
        defaults={
            "name": "Synthetic HQ",
            "command": "ffmpeg",
            "parameters": "-synthetic-hardware cuda",
            "locked": False,
            "is_active": True,
        },
    )
    manager = configure("leader")
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "prepare_slave":
    reset_handoff_state("follower", full_lab_reset=True)
    OutputProfile.objects.update_or_create(
        id=303,
        defaults={
            "name": "Synthetic HQ",
            "command": "ffmpeg",
            "parameters": "-synthetic-hardware qsv",
            "locked": False,
            "is_active": True,
        },
    )
    manager = configure("follower")
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "prepare_cold_autostart":
    role = os.environ["FAILOVARR_TEST_ROLE"]
    settings = cold_settings(role)
    manager = configure(role, settings)
    if role == "leader":
        result = manager.run_action(PLUGIN_KEY, "export_now")
        emit(result, result.get("status") == "exported" and set(result["scope"]["domains"]) == set(FULL_DOMAINS))
    else:
        result = manager.run_action(PLUGIN_KEY, "validate_config")
        emit(result, result.get("status") == "success")

elif ACTION == "cold_autostart_status":
    role = os.environ["FAILOVARR_TEST_ROLE"]
    settings = cold_settings(role)
    from failovarr.autostart import get_redis_client

    owner = get_redis_client().get("failovarr:service_owner")
    owner = owner.decode("utf-8") if isinstance(owner, bytes) else str(owner or "")
    owner_pid = owner.split("-", 1)[0]
    command = ""
    if owner_pid.isdigit():
        try:
            command = Path(f"/proc/{owner_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            pass
    state = ReplicationEngine(settings).status()["state"]
    imported_or_exported = state.get("last_export_at") if role == "leader" else state.get("last_import_at")
    probe = {"owner": owner, "command": command, "last_sync": imported_or_exported}
    emit(probe, bool(owner) and "uwsgi" in command and "celery" not in command and bool(imported_or_exported))

elif ACTION == "stop_current_service":
    manager = PluginManager()
    manager.discover_plugins(sync_db=True, force_reload=False, use_cache=False)
    result = manager.run_action(PLUGIN_KEY, "stop_service")
    emit(result, result.get("status") == "success")

elif ACTION == "slow_cold_start_lease":
    # Exercise the actual Redis lease with a deliberately slow initial
    # follower import. The lease refresh worker must already be active while
    # ``apply_latest`` blocks, otherwise a short real TTL expires here.
    from failovarr import autostart
    from failovarr.engine import BackgroundService

    class SlowColdStartEngine:
        config = SimpleNamespace(
            mode="shared_storage", client_access_mode="disabled",
            deployment_mode="cold_standby", import_on_start=True,
            interval_seconds=60,
        )
        logger = logging.getLogger("redundancy-ci")

        def recover_cold_standby(self):
            return None

        def is_authoritative(self):
            return False

        def apply_latest(self):
            time.sleep(6)
            return {"status": "applied", "message": "Synthetic delayed startup import"}

    original_ttl = autostart.LEASE_TTL_SECONDS
    client = autostart.get_redis_client()
    client.delete(autostart.LEADER_KEY, autostart.STOP_KEY, autostart.FLUSH_KEY)
    service = None
    try:
        autostart.LEASE_TTL_SECONDS = 3
        client, token = autostart.acquire_service_lease()
        service = BackgroundService(SlowColdStartEngine(), redis_client=client, lease_token=token)
        started = time.monotonic()
        service.start()
        elapsed = time.monotonic() - started
        ttl = int(client.ttl(autostart.LEADER_KEY))
        probe = {
            "delayed_import_seconds": round(elapsed, 1),
            "lease_ttl_after_import": ttl,
            "service_running": service.status().get("running"),
        }
        emit(probe, elapsed >= 6 and ttl > 0 and probe["service_running"])
    finally:
        if service is not None:
            service.stop()
        autostart.LEASE_TTL_SECONDS = original_ttl

elif ACTION == "export":
    manager = configure("leader")
    result = manager.run_action(PLUGIN_KEY, "export_now")
    emit(result, result.get("status") == "exported" and set(result["scope"]["domains"]) == set(FULL_DOMAINS))

elif ACTION == "preview_apply_verify":
    manager = configure("follower")
    preview = manager.run_action(PLUGIN_KEY, "preview_latest")
    applied = manager.run_action(PLUGIN_KEY, "apply_latest")
    replay = manager.run_action(PLUGIN_KEY, "apply_latest")
    profile = OutputProfile.objects.get(pk=303)
    auto_created, _created = OutputProfile.objects.get_or_create(
        name="Synthetic sequence probe",
        defaults={
            "command": "ffmpeg",
            "parameters": "-synthetic",
            "locked": False,
            "is_active": True,
        },
    )
    result = {
        "preview": preview,
        "applied": applied,
        "replay": replay,
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "uses_qsv_override": profile.parameters == "-synthetic-hardware qsv",
        },
        "sequence_advanced": auto_created.id > 303,
    }
    emit(result, all((
        preview.get("status") == "preview",
        applied.get("status") == "applied",
        replay.get("status") == "error",
        result["profile"]["uses_qsv_override"],
        result["sequence_advanced"],
    )))

elif ACTION == "shared_apply_verify":
    manager = configure("follower")
    preview = manager.run_action(PLUGIN_KEY, "preview_latest")
    applied = manager.run_action(PLUGIN_KEY, "apply_latest")
    result = {"preview": preview, "applied": applied}
    emit(result, preview.get("status") == "preview" and applied.get("status") == "applied")

elif ACTION == "tamper_rejected":
    manager = configure("follower")
    latest_path = Path(SHARED_PATH) / "latest.json"
    pointer = json.loads(latest_path.read_bytes())
    object_name = pointer.get("object")
    if object_name is None:
        bundle_path = latest_path
    else:
        object_path = Path(str(object_name))
        if object_path.is_absolute() or ".." in object_path.parts:
            raise RuntimeError("Test bundle pointer is unsafe")
        bundle_path = Path(SHARED_PATH) / object_path
    original = bundle_path.read_bytes()
    try:
        envelope = json.loads(original)
        envelope["payload"]["source_node"] = "tampered-node"
        bundle_path.write_text(json.dumps(envelope), encoding="utf-8")
        result = manager.run_action(PLUGIN_KEY, "preview_latest")
    finally:
        bundle_path.write_bytes(original)
    probe = {
        "status": result.get("status"),
        "tamper_rejected": "hash mismatch" in result.get("message", ""),
    }
    emit(probe, probe["status"] == "error" and probe["tamper_rejected"])

elif ACTION == "prepare_graph_main":
    prepare_graph()
    prepare_client_identity()
    manager = configure("leader", graph_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "prepare_graph_slave":
    cleanup_graph()
    prepare_client_identity()
    manager = configure("follower", graph_settings("follower"))
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "export_graph":
    manager = configure("leader", graph_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "export_now")
    emit(result, result.get("status") == "exported")

elif ACTION == "client_identity_mismatch":
    manager = configure("follower", graph_settings("follower"))
    user = User.objects.get(username="redundancy-fixture")
    original = dict(user.custom_properties or {})
    changed = dict(original)
    changed["xc_password"] = "deliberately-different"
    user.custom_properties = changed
    user.save(update_fields=["custom_properties"])
    try:
        result = manager.run_action(PLUGIN_KEY, "preview_latest")
    finally:
        user.custom_properties = original
        user.save(update_fields=["custom_properties"])
    probe = {
        "status": result.get("status"),
        "identity_mismatch_rejected": (
            "Client identity mismatch" in result.get("message", "")
            and "redundancy-fixture" in result.get("message", "")
            and "deliberately-different" not in result.get("message", "")
        ),
    }
    emit(probe, probe["status"] == "error" and probe["identity_mismatch_rejected"])

elif ACTION == "apply_graph_verify":
    manager = configure("follower", graph_settings("follower"))
    preview = manager.run_action(PLUGIN_KEY, "preview_latest")
    applied = manager.run_action(PLUGIN_KEY, "apply_latest")
    account = M3UAccount.objects.get(pk=7302)
    channel = Channel.objects.get(pk=7310)
    channel_override = ChannelOverride.objects.get(pk=7311)
    membership = ChannelProfileMembership.objects.get(pk=7313)
    channel_stream = ChannelStream.objects.get(pk=7314)
    group_account = ChannelGroupM3UAccount.objects.get(pk=7315)
    epg_source = EPGSource.objects.get(pk=7305)
    m3u_cron = account.refresh_task.crontab
    epg_cron = epg_source.refresh_task.crontab
    probe = {
        "preview": preview,
        "applied": applied,
        "all_ids_present": all(
            model.objects.filter(pk=record_id).exists()
            for model, record_id in (
                (ServerGroup, 7301),
                (M3UAccount, 7302),
                (M3UAccountProfile, 7303),
                (M3UFilter, 7304),
                (EPGSource, 7305),
                (EPGData, 7306),
                (ChannelGroup, 7307),
                (Logo, 7308),
                (Stream, 7309),
                (Channel, 7310),
                (ChannelOverride, 7311),
                (ChannelProfile, 7312),
                (ChannelProfileMembership, 7313),
                (ChannelStream, 7314),
                (ChannelGroupM3UAccount, 7315),
            )
        ),
        "credentials_preserved": (
            account.username == "synthetic-user"
            and account.password == "synthetic-password"
        ),
        "cron_schedules_preserved": (
            f"{m3u_cron.minute} {m3u_cron.hour} {m3u_cron.day_of_month} {m3u_cron.month_of_year} {m3u_cron.day_of_week}"
            == "15 4 * * *"
            and f"{epg_cron.minute} {epg_cron.hour} {epg_cron.day_of_month} {epg_cron.month_of_year} {epg_cron.day_of_week}"
            == "30 3 * * 1-5"
        ),
        "stable_channel_uuid": str(channel.uuid) == "00000000-0000-0000-0000-000000007310",
        "relations_preserved": all((
            channel.auto_created_by_id == 7302,
            channel.epg_data_id == 7306,
            channel_override.channel_id == 7310,
            membership.channel_profile_id == 7312 and membership.channel_id == 7310,
            channel_stream.channel_id == 7310 and channel_stream.stream_id == 7309,
            group_account.channel_group_id == 7307 and group_account.m3u_account_id == 7302,
        )),
    }
    emit(probe, all((
        preview.get("status") == "preview",
        applied.get("status") == "applied",
        probe["all_ids_present"],
        probe["credentials_preserved"],
        probe["cron_schedules_preserved"],
        probe["stable_channel_uuid"],
        probe["relations_preserved"],
    )))

elif ACTION == "prepare_core_scope_main":
    CoreSettings.objects.update_or_create(
        key="stream_settings",
        defaults={"name": "Stream Settings", "value": {"fixture": "main-stream"}},
    )
    CoreSettings.objects.update_or_create(
        key="dvr_settings",
        defaults={"name": "DVR Settings", "value": {"fixture": "main-dvr"}},
    )
    scoped = plugin_settings("leader")
    scoped.update({"domains": "core_settings", "core_setting_keys": "stream_settings"})
    manager = configure("leader", scoped)
    result = manager.run_action(PLUGIN_KEY, "export_now")
    emit(result, result.get("status") == "exported")

elif ACTION == "initialize_core_scope_verify":
    local_dvr, _created = CoreSettings.objects.update_or_create(
        key="dvr_settings",
        defaults={"name": "DVR Settings", "value": {"fixture": "follower-local-dvr"}},
    )
    local_id, local_value = local_dvr.id, local_dvr.value
    scoped = plugin_settings("follower")
    scoped.update({"domains": "core_settings", "core_setting_keys": "stream_settings"})
    configure("follower", scoped)
    result = ReplicationEngine(scoped).initialize_follower()
    preserved = CoreSettings.objects.get(key="dvr_settings")
    stream = CoreSettings.objects.get(key="stream_settings")
    probe = {
        "initialized": result,
        "dvr_preserved": preserved.id == local_id and preserved.value == local_value,
        "stream_imported": stream.value == {"fixture": "main-stream"},
    }
    emit(probe, result.get("status") == "initialized" and probe["dvr_preserved"] and probe["stream_imported"])

elif ACTION == "serve_direct":
    # Remove the deliberately colliding record from the earlier conflict probe.
    OutputProfile.objects.filter(id=304, name="Synthetic direct profile").delete()
    OutputProfile.objects.update_or_create(
        id=404,
        defaults={
            "name": "Synthetic direct profile",
            "command": "ffmpeg",
            "parameters": "-synthetic-direct",
            "locked": False,
            "is_active": True,
        },
    )
    manager = configure("leader", direct_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "start_service")
    emit(result, result.get("status") == "success" and result.get("running"))
    # ``stop_direct`` runs in a separate management process.  It signals the
    # elected service through Redis, so this process must wait for that signal
    # and exit before the handoff test is allowed to bind the same plugin port.
    # A fixed sleep here previously left an obsolete SetupServer on 9192.
    import failovarr as redundancy_plugin

    for _ in range(40):
        service = getattr(redundancy_plugin, "_service", None)
        if service is None or not service.status().get("running"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Direct-pull service did not stop after its Redis stop signal")

elif ACTION == "direct_apply_verify":
    manager = configure("follower", direct_settings("follower"))
    preview = manager.run_action(PLUGIN_KEY, "preview_latest")
    applied = manager.run_action(PLUGIN_KEY, "apply_latest")
    profile = OutputProfile.objects.get(pk=404)
    probe = {
        "preview": preview,
        "applied": applied,
        "direct_profile": {"id": profile.id, "name": profile.name},
    }
    emit(probe, all((
        preview.get("status") == "preview",
        applied.get("status") == "applied",
        probe["direct_profile"]["id"] == 404,
    )))

elif ACTION == "singleton_probe":
    manager = configure("leader", direct_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "start_service")
    probe = {
        "status": result.get("status"),
        "already_running": "already running" in result.get("message", ""),
        "running": result.get("running"),
    }
    emit(probe, probe["status"] == "success" and probe["already_running"] and probe["running"])

elif ACTION == "save_export_cross_worker":
    # The service is deliberately owned by the detached serve_direct process.
    # Saving through the Assistant contract must signal that owner, wait for
    # its lease release, acquire the service locally and only then export.
    from failovarr.setup_assistant import SetupServer

    settings = direct_settings("leader")
    # This probe verifies the save path used when the user explicitly enables
    # service autostart. The detached process owns the previous lease; save()
    # must stop it, become the new owner and restart the service.
    settings["auto_start"] = True
    manager = configure("leader", settings)
    server = SetupServer(settings, logging.getLogger("redundancy-ci"))
    saved = server.save(settings)
    exported = server.export_now()
    stopped = manager.run_action(PLUGIN_KEY, "stop_service")
    probe = {"saved": saved, "exported": exported, "stopped": stopped}
    emit(probe, all((
        saved.get("status") == "success",
        saved.get("service", {}).get("running") is True,
        exported.get("status") == "exported",
        stopped.get("status") == "success",
    )))

elif ACTION == "stop_direct":
    manager = configure("leader", direct_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "stop_service")
    emit(result, result.get("status") == "success")

elif ACTION == "prepare_handoff_main":
    reset_handoff_state("leader")
    manager = configure("leader", handoff_settings("leader"))
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "prepare_handoff_slave":
    reset_handoff_state("follower")
    manager = configure("follower", handoff_settings("follower"))
    result = manager.run_action(PLUGIN_KEY, "validate_config")
    emit(result, result.get("status") == "success")

elif ACTION == "serve_handoff_main":
    settings = handoff_settings("leader")
    manager = configure("leader", settings)
    result = manager.run_action(PLUGIN_KEY, "start_service")
    emit(result, result.get("status") == "success" and result.get("running"))
    for _ in range(120):
        write_background_service_diagnostic(settings)
        time.sleep(1)
    write_background_service_diagnostic(settings)
    manager.run_action(PLUGIN_KEY, "stop_service")

elif ACTION == "serve_handoff_slave":
    settings = handoff_settings("follower")
    manager = configure("follower", settings)
    result = manager.run_action(PLUGIN_KEY, "start_service")
    emit(result, result.get("status") == "success" and result.get("running"))
    for _ in range(120):
        write_background_service_diagnostic(settings)
        time.sleep(1)
    write_background_service_diagnostic(settings)
    manager.run_action(PLUGIN_KEY, "stop_service")

elif ACTION == "request_handoff_verify":
    settings = handoff_settings("leader")
    manager = configure("leader", settings)
    queued = manager.run_action(PLUGIN_KEY, "handoff_to_peer")
    main_status = {}
    slave_status = {}
    # Startup, lease election and the two signed phases each cross independent
    # worker loops. Keep enough margin for a worst-case interval boundary.
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        main_status = ReplicationEngine(settings).peer_status()
        try:
            slave_status = fetch_status(settings["peer_url"], SHARED_SECRET)
        except Exception:
            slave_status = {}
        if (
            main_status.get("authoritative") is False
            and slave_status.get("authoritative") is True
            and slave_status.get("client_ready") is True
        ):
            break
        time.sleep(1)
    probe = {
        "queued": queued,
        "main": main_status,
        "slave": slave_status,
        "handoff_complete": (
            main_status.get("authoritative") is False
            and main_status.get("client_ready") is False
            and slave_status.get("authoritative") is True
            and slave_status.get("client_ready") is True
            and slave_status.get("applied_sequence", 0) >= main_status.get("exported_sequence", 0)
        ),
    }
    emit(probe, probe["queued"].get("status") == "queued" and probe["handoff_complete"])

else:
    raise RuntimeError(f"Unknown FAILOVARR_TEST_ACTION: {ACTION}")
