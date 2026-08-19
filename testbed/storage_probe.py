"""Exercise every remote bundle backend with synthetic credentials and data."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from failovarr.bundle import create_envelope
from failovarr.remote_storage import create_bundle_store


SECRET = "storage-lab-shared-secret-32-characters"
STATE = "/data/failovarr-state"


def config(backend, endpoint, container, username, password, options):
    return SimpleNamespace(
        storage_backend=backend,
        storage_endpoint=endpoint,
        storage_container=container,
        storage_username=username,
        storage_password=password,
        storage_options=options,
        state_path=STATE,
        shared_path="/data/redundancy",
        node_id="storage-lab",
    )


def round_trip(name, settings, sequence, store=None):
    envelope = create_envelope(
        cluster_id="storage-lab",
        source_node="lab-main",
        sequence=sequence,
        domains={"output_profiles": [{"id": sequence, "name": name}]},
        secret=SECRET,
    )
    store = store or create_bundle_store(settings)
    store.write_latest(envelope)
    received = store.read_latest()
    return received == envelope


host_key = os.environ["LAB_SFTP_HOST_KEY"].strip()
known_hosts = Path(STATE) / "storage-lab-known_hosts"
known_hosts.parent.mkdir(parents=True, exist_ok=True)
known_hosts.write_text(f"storage-sftp {host_key}\n", encoding="utf-8")

s3_settings = config(
    "s3", "http://storage-s3:9000", "redundancy",
    "lab-access-key", "lab-secret-key-32-characters",
    {"allow_insecure_http": True, "region": "us-east-1", "addressing_style": "path"},
)
s3_store = create_bundle_store(s3_settings)
try:
    s3_store.client.create_bucket(Bucket="redundancy")
except s3_store.client.exceptions.BucketAlreadyOwnedByYou:
    pass

base_sequence = int(time.time()) * 10
results = {
    "webdav": round_trip("webdav", config(
        "webdav", "http://storage-webdav:8080", "redundancy",
        "lab", "lab-webdav-password", {"allow_insecure_http": True},
    ), base_sequence + 1),
    "s3": round_trip("s3", s3_settings, base_sequence + 2, store=s3_store),
    "sftp": round_trip("sftp", config(
        "sftp", "sftp://storage-sftp:22", "upload/redundancy",
        "lab", "lab-sftp-password", {"known_hosts_path": str(known_hosts)},
    ), base_sequence + 3),
    "smb": round_trip("smb", config(
        "smb", "smb://storage-smb:445", "redundancy/dispatcharr",
        "lab", "lab-smb-password", {"require_encryption": True},
    ), base_sequence + 4),
}

print("FAILOVARR_STORAGE_PROBE=" + json.dumps(results, sort_keys=True))
if not all(results.values()):
    raise RuntimeError("A remote storage round trip failed")
