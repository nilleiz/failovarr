"""Container-wide service election and delayed plugin auto-start."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid

from .config import as_bool
from .logging_utils import plugin_logger

logger = plugin_logger(logging.getLogger("dispatcharr.plugins.failovarr"))
_launch_guard = threading.Lock()
_launched = False

LEADER_KEY = "failovarr:service_owner"
STOP_KEY = "failovarr:stop_requested"
FLUSH_KEY = "failovarr:flush_before_stop"
LEASE_TTL_SECONDS = 30


class ServiceAlreadyRunning(RuntimeError):
    pass


def get_redis_client():
    try:
        from core.utils import RedisClient
        return RedisClient.get_client()
    except Exception as exc:
        raise RuntimeError("Dispatcharr Redis is unavailable") from exc


def acquire_service_lease():
    client = get_redis_client()
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    if not client.set(LEADER_KEY, token, nx=True, ex=LEASE_TTL_SECONDS):
        raise ServiceAlreadyRunning("Background service is already running in another worker")
    # Do not inherit a stale shutdown request if a different uWSGI worker
    # acquires the lease immediately after the former owner exited.
    client.delete(STOP_KEY, FLUSH_KEY)
    return client, token


def refresh_service_lease(client, token: str) -> bool:
    script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
      return redis.call('expire', KEYS[1], ARGV[2])
    end
    return 0
    """
    return bool(client.eval(script, 1, LEADER_KEY, token, LEASE_TTL_SECONDS))


def release_service_lease(client, token: str) -> None:
    script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
      return redis.call('del', KEYS[1])
    end
    return 0
    """
    try:
        client.eval(script, 1, LEADER_KEY, token)
    except Exception:
        logger.debug("Could not release redundancy service lease", exc_info=True)


def request_service_stop(flush: bool = False) -> bool:
    """Ask the elected worker to stop, optionally after a final export.

    Dispatcharr invokes ``Plugin.stop()`` in more than one uWSGI worker.  The
    worker receiving that callback is not necessarily the elected replication
    worker, so a direct in-process flush would be lost.  A short-lived Redis
    signal lets the actual owner perform the one final cold-standby export.
    """
    try:
        client = get_redis_client()
        if not client.get(LEADER_KEY):
            return False
        if flush:
            client.set(FLUSH_KEY, "1", ex=60)
        client.set(STOP_KEY, "1", ex=60)
        return True
    except Exception:
        logger.debug("Could not signal redundancy service stop", exc_info=True)
        return False


def service_is_running() -> bool:
    try:
        return bool(get_redis_client().get(LEADER_KEY))
    except Exception:
        return False


def wait_for_service_stop(timeout_seconds: float = 10.0, poll_seconds: float = 0.1) -> bool:
    """Wait until the elected worker releases its container-wide lease."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not service_is_running():
            return True
        time.sleep(poll_seconds)
    return not service_is_running()


def attempt_autostart(start_callback) -> None:
    global _launched
    if not is_dispatcharr_web_process():
        logger.debug("Autostart skipped: this process is not a Dispatcharr uWSGI worker")
        return
    with _launch_guard:
        if _launched:
            return
        _launched = True
    threading.Thread(
        target=_worker, args=(start_callback,), daemon=True,
        name="redundancy-autostart",
    ).start()


def is_dispatcharr_web_process() -> bool:
    """Only durable uWSGI workers may acquire the container service lease.

    Dispatcharr imports plugins in Celery worker children too.  Those children
    are autoscaled and must never own a long-lived replication loop.
    """
    try:
        command = open("/proc/self/cmdline", "rb").read().lower()
    except OSError:
        command = " ".join(sys.argv).encode("utf-8", "ignore").lower()
    return b"uwsgi" in command and b"celery" not in command


def _worker(start_callback) -> None:
    time.sleep(5)
    for attempt in range(8):
        if attempt:
            time.sleep(3)
        try:
            from apps.plugins.models import PluginConfig
            config = PluginConfig.objects.filter(key__in=[
                "failovarr", "failovarr",
            ]).first()
            if config is None:
                continue
            # Guided setup persists the complete contract node-locally.  The
            # original implementation only inspected PluginConfig.settings,
            # so a correctly enabled service was never started after restart.
            from .node_config import effective_settings
            from .config import configuration_issues

            settings = effective_settings(config.settings or {})
            issues = configuration_issues(settings)
            if issues:
                logger.debug(
                    "Autostart is waiting for completed setup (fields=%s)",
                    ",".join(issue.field for issue in issues),
                )
                return
            if not config.enabled or not as_bool(settings.get("auto_start", False)):
                logger.debug("Autostart is disabled")
                return
            try:
                start_callback(settings)
                logger.info("Background service started by uWSGI autostart")
            except ServiceAlreadyRunning:
                logger.debug("Another Dispatcharr worker owns the redundancy service")
            return
        except Exception as exc:
            logger.debug("Autostart attempt %s is waiting for Dispatcharr", attempt + 1)
            logger.debug("Redundancy autostart detail", exc_info=True)
    logger.warning("Could not auto-start after waiting for the ORM")
