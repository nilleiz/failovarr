"""Single container-owned process for the setup/direct-pull HTTP endpoint."""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import sys
import threading
from pathlib import Path


LOCK_PATH = Path(os.environ.get(
    "FAILOVARR_SETUP_LOCK",
    os.environ.get("DISPATCHARR_REDUNDANCY_SETUP_LOCK", "/data/failovarr-setup.lock"),
))
PID_PATH = Path(os.environ.get(
    "FAILOVARR_SETUP_PID",
    os.environ.get("DISPATCHARR_REDUNDANCY_SETUP_PID", "/data/failovarr-setup.pid"),
))


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9192
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("a+", encoding="utf-8")
    os.chmod(LOCK_PATH, 0o600)
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
    import django

    django.setup()
    from apps.plugins.models import PluginConfig

    from .config import PLUGIN_DB_KEY
    from .logging_utils import plugin_logger
    from .node_config import effective_settings
    from .setup_assistant import SetupServer

    logger = plugin_logger(logging.getLogger("dispatcharr.plugins.failovarr"))
    record = PluginConfig.objects.get(key=PLUGIN_DB_KEY)
    settings = effective_settings(record.settings or {})
    token = str(settings.get("setup_access_token", ""))
    server = SetupServer(settings, logger, port=port, token=token)
    stopped = threading.Event()
    graceful = threading.Event()

    def stop(_signum=None, _frame=None):
        graceful.set()
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    PID_PATH.write_text(f"{os.getpid()}\n", encoding="ascii")
    os.chmod(PID_PATH, 0o600)
    try:
        server.start()
        logger.info("Setup assistant helper started (port=%s)", port)
        while not stopped.wait(1):
            if server.thread is None or not server.thread.is_alive():
                logger.error("Setup assistant HTTP thread stopped unexpectedly")
                return 1
    finally:
        # The helper may own the elected replication loop after an Assistant
        # save. Preserve the same graceful Cold-Standby final-export contract
        # as a uWSGI-owned service; an unexpected HTTP-thread crash is not a
        # handoff signal and therefore does not flush.
        from . import _service, _stop_service

        if _service is not None:
            _stop_service(flush=graceful.is_set(), logger_override=logger)
        server.stop()
        PID_PATH.unlink(missing_ok=True)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
        logger.info("Setup assistant helper stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
