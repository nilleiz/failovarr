"""Verified extraction of optional pure-Python remote-storage dependencies."""

from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from pathlib import Path


VENDOR_ARCHIVE_SHA256 = "18d56c8118a14cb394dfc69d55baff069a9386c0cf327e7d00e1b3e570c49ee0"


def ensure_vendor_dependencies(state_path: str) -> None:
    try:
        import asyncssh  # noqa: F401
        import boto3  # noqa: F401
        import smbclient  # noqa: F401
        return
    except ImportError:
        pass

    archive = Path(__file__).with_name("vendor") / "remote_storage.zip"
    if not archive.is_file() or not VENDOR_ARCHIVE_SHA256:
        raise RuntimeError(
            "Remote storage dependencies are not installed in this development build"
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != VENDOR_ARCHIVE_SHA256:
        raise RuntimeError("Remote storage dependency archive digest mismatch")
    destination = Path(state_path) / "vendor" / digest
    complete = destination / ".complete"
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{digest}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError("Vendor extraction requires the Linux Dispatcharr container") from exc
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            # Multiple uWSGI workers may load the backend concurrently. Only a
            # complete, digest-specific extraction becomes importable.
            if not complete.exists():
                destination.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive) as package:
                    root = destination.resolve()
                    for member in package.infolist():
                        target = (destination / member.filename).resolve()
                        if target != root and root not in target.parents:
                            raise RuntimeError("Unsafe path in remote storage dependency archive")
                    package.extractall(destination)
                descriptor = os.open(complete, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                try:
                    os.write(descriptor, (digest + "\n").encode("ascii"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    path = str(destination)
    if path not in sys.path:
        sys.path.insert(0, path)
