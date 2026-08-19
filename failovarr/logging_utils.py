"""Consistent human-readable logging for Failovarr."""

from __future__ import annotations

import logging


LOG_PREFIX = "[Failovarr]"


class _PrefixAdapter(logging.LoggerAdapter):
    def process(self, message, kwargs):
        if not str(message).startswith(LOG_PREFIX):
            message = f"{LOG_PREFIX} {message}"
        return message, kwargs


def plugin_logger(logger: logging.Logger | logging.LoggerAdapter) -> logging.LoggerAdapter:
    """Add one stable prefix while retaining Dispatcharr's own log formatter."""
    if isinstance(logger, _PrefixAdapter):
        return logger
    return _PrefixAdapter(logger, {})
