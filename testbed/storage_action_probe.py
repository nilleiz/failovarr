"""Invoke storage tests and bundle paths through Dispatcharr's real HTTP plugin API."""

from __future__ import annotations

import json
import os
import time
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from urllib.parse import parse_qs


BASE = os.environ.get("LAB_DISPATCHARR_URL", "http://127.0.0.1:9191")
USERNAME = "storagelab"
PASSWORD = "storage-lab-only-password"
SECRET = "storage-action-lab-secret-32-characters"


def request(path: str, body: dict, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(BASE + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=90) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("Dispatcharr returned an invalid response")
    return value


login = request("/api/accounts/token/", {"username": USERNAME, "password": PASSWORD})
token = login["access"]


def plugin(action: str) -> dict:
    response = request(
        "/api/plugins/plugins/failovarr/run/",
        {"action": action, "params": {}}, token,
    )
    result = response.get("result", {})
    if not response.get("success") or result.get("status") not in {
        "success", "ready", "exported", "preview", "applied",
    }:
        raise RuntimeError(f"Plugin action {action} failed: {result.get('message', response.get('error', 'unknown'))}")
    return result


assistant = plugin("start_setup_assistant")
assistant_url = assistant["url"]
parts = urlsplit(assistant_url)
# This probe runs inside the synthetic Dispatcharr container. Use the plugin's
# fixed internal management port while preserving the token returned by the
# real action.
assistant_origin = os.environ.get(
    "LAB_ASSISTANT_INTERNAL_URL", "http://127.0.0.1:9192",
).rstrip("/")
assistant_token = parse_qs(parts.query)["token"][0]
assistant_local = f"{assistant_origin}/setup?token={assistant_token}"


def wait_for_assistant() -> None:
    """The plugin action starts the sidecar thread asynchronously in uWSGI."""
    health_url = f"{assistant_origin}/v1/health"
    last_error: Exception | None = None
    for _attempt in range(30):
        try:
            with urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # ConnectionRefusedError during normal startup.
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Setup assistant did not become ready: {type(last_error).__name__}")


wait_for_assistant()


def assistant_post(path: str, settings: dict) -> dict:
    target = f"{assistant_origin}{path}?token={assistant_token}"
    req = Request(target, data=json.dumps(settings).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=90) as response:
        result = json.load(response)
    if result.get("status") not in {"success", "verified"}:
        raise RuntimeError(f"Setup assistant rejected settings: {result.get('message', 'unknown')}")
    return result


base = {
    "cluster_id": "storage-action-lab",
    "role": "leader",
    "mode": "shared_storage",
    "redundancy_mode": "external_proxy",
    "shared_path": "/data/redundancy",
    "state_path": "/data/failovarr-state",
    "shared_secret": SECRET,
    "domains": "output_profiles",
    "core_setting_keys": "stream_settings",
    "client_identity_users": "",
    "allow_deletes": False,
    "automatic_apply": False,
    "auto_start": False,
    "storage_timeout_seconds": 20,
}

backends = {
    "filesystem": {},
    "webdav": {
        "storage_endpoint": "http://storage-webdav:8080", "storage_container": "actions",
        "storage_username": "lab", "storage_password": "lab-webdav-password",
        "storage_allow_insecure_http": True,
    },
    "s3": {
        "storage_endpoint": "http://storage-s3:9000", "storage_container": "redundancy",
        "storage_username": "lab-access-key", "storage_password": "lab-secret-key-32-characters",
        "storage_allow_insecure_http": True, "s3_region": "us-east-1", "s3_addressing_style": "path",
    },
    "sftp": {
        "storage_endpoint": "sftp://storage-sftp:22", "storage_container": "upload/actions",
        "storage_username": "lab", "storage_password": "lab-sftp-password",
        "sftp_known_hosts_path": "/data/failovarr-state/storage-lab-known_hosts",
    },
    "smb": {
        "storage_endpoint": "smb://storage-smb:445", "storage_container": "redundancy/actions",
        "storage_username": "lab", "storage_password": "lab-smb-password",
    },
}

results = {}
for index, (backend, backend_settings) in enumerate(backends.items(), 1):
    leader = {**base, **backend_settings, "node_id": f"{backend}-leader", "storage_backend": backend}
    leader_saved = assistant_post("/api/config", leader)
    if not leader_saved.get("native_settings_synced"):
        raise RuntimeError("Assistant did not mirror Main native Plugin Settings")
    connection = plugin("test_storage")
    exported = plugin("export_now")
    # A follower has independent replay state. Sharing the leader state path
    # would correctly reject this bundle as already exported, but would not
    # exercise the real follower-import path.
    follower = {
        **leader,
        "node_id": f"{backend}-follower",
        "role": "follower",
        "state_path": f"/data/failovarr-state/{backend}-follower",
    }
    follower_saved = assistant_post("/api/config", follower)
    if not follower_saved.get("native_settings_synced"):
        raise RuntimeError("Assistant did not mirror Follower native Plugin Settings")
    bundle_info = assistant_post("/api/bundle-info", follower)
    if bundle_info.get("status") != "verified":
        raise RuntimeError("Follower could not discover the verified Main bundle")
    preview = plugin("preview_latest")
    imported = plugin("import_latest")
    results[backend] = {
        "connection": connection["status"],
        "export_sequence": exported.get("sequence"),
        "bundle": bundle_info.get("status"),
        "preview": preview["status"],
        "import": imported["status"],
    }

# Start the setup UI via the same plugin action and verify its token-protected
# bootstrap endpoint without exposing the configured backend password.
with urlopen(assistant_local, timeout=10) as response:
    if response.status != 200 or b"Failovarr" not in response.read():
        raise RuntimeError("Setup assistant HTML did not load")
bootstrap_url = f"{assistant_origin}/api/config?token={assistant_token}"
with urlopen(bootstrap_url, timeout=10) as response:
    bootstrap = json.load(response)
if bootstrap["settings"].get("storage_password") != "":
    raise RuntimeError("Setup assistant exposed a storage password")
if bootstrap["settings"].get("setup_access_token") != "":
    raise RuntimeError("Setup assistant exposed its access token in bootstrap data")

print("FAILOVARR_STORAGE_ACTIONS=" + json.dumps(results, sort_keys=True))
