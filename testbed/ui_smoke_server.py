"""Serve the real setup HTML with synthetic API data for visual smoke tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ["FAILOVARR_NO_AUTOSTART"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from failovarr.config import (
    CORE_SETTING_GROUPS,
    DEFAULT_DOMAINS,
    DOMAIN_DEPENDENCIES,
    DOMAIN_DESCRIPTIONS,
    DOMAIN_GROUP_LABELS,
    DOMAIN_GROUPS,
    DOMAIN_LABELS,
)
from failovarr.setup_assistant import SETUP_HTML


TOKEN = "synthetic-ui-token"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=48092)
    parser.add_argument("--role", choices=("leader", "follower"), default="follower")
    args = parser.parse_args()
    settings = {
        "node_id": "lab-main" if args.role == "leader" else "lab-follower",
        "cluster_id": "dispatcharr-lab",
        "role": args.role,
        "mode": "shared_storage",
        "storage_backend": "sftp",
        "storage_endpoint": "sftp://storage:22",
        "storage_container": "bundles",
        "storage_username": "replication",
        "storage_password": "",
        "storage_password_is_set": True,
        "shared_secret": "",
        "shared_secret_is_set": True,
        "replication_scope": "full",
        "domains": DEFAULT_DOMAINS,
        "core_setting_keys": "stream_settings,proxy_settings,epg_settings,user_limit_settings",
        "protected_output_profile_ids": "3",
        "new_output_profile_policy": "disabled",
        "client_access_mode": "disabled",
        "state_path": "/data/failovarr-state",
        "shared_path": "/data/redundancy",
        "sftp_known_hosts_path": "/data/redundancy-secrets/known_hosts",
        "storage_timeout_seconds": 20,
        "client_identity_users": "*",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _send(self, status: int, value, content_type: str = "application/json"):
            payload = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            return parse_qs(urlsplit(self.path).query).get("token", [""])[0] == TOKEN

        def do_GET(self):
            if not self._authorized():
                self._send(403, {"status": "error", "message": "Invalid setup token"})
                return
            path = urlsplit(self.path).path
            if path == "/setup":
                self._send(200, SETUP_HTML, "text/html; charset=utf-8")
            elif path == "/api/config":
                self._send(200, {
                    "settings": settings,
                    "profiles": [
                        {"id": 1, "name": "Default", "is_active": True},
                        {"id": 3, "name": "Transcode HQ (Intel QSV)", "is_active": True},
                        {"id": 5, "name": "Low bandwidth", "is_active": False},
                    ],
                    "domain_groups": DOMAIN_GROUPS,
                    "domain_group_labels": DOMAIN_GROUP_LABELS,
                    "domain_labels": DOMAIN_LABELS,
                    "domain_dependencies": {
                        key: sorted(value) for key, value in DOMAIN_DEPENDENCIES.items()
                    },
                    "domain_descriptions": DOMAIN_DESCRIPTIONS,
                    "default_domains": DEFAULT_DOMAINS.split(","),
                    "core_setting_groups": CORE_SETTING_GROUPS,
                })
            elif path == "/api/profile":
                self._send(200, {"format": "failovarr-profile", "version": 1})
            else:
                self._send(404, {"status": "error", "message": "Not found"})

        def do_POST(self):
            if not self._authorized():
                self._send(403, {"status": "error", "message": "Invalid setup token"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size) or b"{}")
            path = urlsplit(self.path).path
            if path == "/api/config":
                settings.update(body)
                self._send(200, {"status": "success", "message": "Configuration saved and validated."})
            elif path == "/api/test-storage":
                self._send(200, {"status": "success", "message": "Connection test completed successfully."})
            elif path == "/api/preview":
                self._send(200, {"status": "preview", "sequence": 4, "summary": {"create": 0, "update": 0, "delete": 0, "conflicts": 0}, "conflicts": {}})
            elif path == "/api/import":
                self._send(200, {"status": "applied", "sequence": 4, "summary": {"create": 0, "update": 0, "delete": 0, "conflicts": 0}})
            elif path == "/api/initialize":
                self._send(200, {"status": "initialized", "backup": "dispatcharr-backup-example.zip", "summary": {"create": 100, "delete": 100, "conflicts": 0}})
            elif path == "/api/profile/import":
                self._send(200, {"status": "success", "message": "Main configuration imported. Node-local settings were preserved."})
            else:
                self._send(404, {"status": "error", "message": "Not found"})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"UI_SMOKE_URL=http://127.0.0.1:{args.port}/setup?token={TOKEN}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
