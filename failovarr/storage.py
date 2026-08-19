"""Atomic bundle and local state persistence."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping


class AtomicJsonStore:
    def __init__(self, directory: str, node_id: str = "node"):
        self.directory = Path(directory)
        self.latest_path = self.directory / "latest.json"
        safe_node = "".join(char if char.isalnum() or char in "-_" else "_" for char in node_id)
        self.state_path = self.directory / f"state-{safe_node}.json"

    @staticmethod
    def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return value

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        self._atomic_write(self.latest_path, envelope)

    def read_latest(self) -> dict[str, Any]:
        return self._read(self.latest_path)

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return self._read(self.state_path)

    def write_state(self, state: Mapping[str, Any]) -> None:
        self._atomic_write(self.state_path, state)

    @contextmanager
    def exclusive_lock(self):
        """Serialize state transitions across Dispatcharr worker processes."""
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / ".replication.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            try:
                import fcntl
            except ImportError as exc:
                raise RuntimeError("Cross-process locking requires the Linux Dispatcharr container") from exc
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if "fcntl" in locals():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
