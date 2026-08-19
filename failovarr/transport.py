"""Direct pull transport and read-only management endpoint."""

from __future__ import annotations

import json
import hashlib
import hmac
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


MAX_BUNDLE_BYTES = 64 * 1024 * 1024


def _request_signature(secret: str, timestamp: str, method: str, path: str) -> str:
    message = f"{timestamp}\n{method}\n{path}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def request_is_authorized(
    headers: Any, secret: str, method: str, path: str, now: int | None = None,
) -> bool:
    """Validate a short-lived management request signature."""
    timestamp = headers.get("X-Dispatcharr-Timestamp", "")
    signature = headers.get("X-Dispatcharr-Signature", "")
    try:
        current = int(time.time()) if now is None else int(now)
        fresh = abs(current - int(timestamp)) <= 30
    except (TypeError, ValueError):
        fresh = False
    expected = _request_signature(secret, timestamp, method, path)
    return fresh and hmac.compare_digest(signature, expected)


def fetch_latest(peer_url: str, secret: str, timeout: float = 10.0) -> dict[str, Any]:
    return _fetch_json(peer_url, secret, "/v1/latest", timeout)


def fetch_status(peer_url: str, secret: str, timeout: float = 10.0) -> dict[str, Any]:
    return _fetch_json(peer_url, secret, "/v1/status", timeout)


def _fetch_json(peer_url: str, secret: str, path: str, timeout: float) -> dict[str, Any]:
    timestamp = str(int(time.time()))
    request = urllib.request.Request(
        f"{peer_url.rstrip('/')}{path}",
        headers={
            "Accept": "application/json",
            "User-Agent": "failovarr/0.1",
            "X-Dispatcharr-Timestamp": timestamp,
            "X-Dispatcharr-Signature": _request_signature(secret, timestamp, "GET", path),
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"Peer returned HTTP {response.status}")
        if response.headers.get_content_type() != "application/json":
            raise RuntimeError("Peer did not return JSON")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_BUNDLE_BYTES:
            raise ValueError("Peer bundle exceeds the maximum allowed size")
        raw = response.read(MAX_BUNDLE_BYTES + 1)
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("Peer bundle exceeds the maximum allowed size")
        value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Peer bundle must be a JSON object")
    return value


class BundleHttpServer:
    def __init__(self, host: str, port: int, latest: Callable[[], dict[str, Any] | None], secret: str,
                 readiness: Callable[[], bool] | None = None):
        self.host = host
        self.port = port
        self.latest = latest
        self.secret = secret
        self.readiness = readiness or (lambda: False)
        self.status_provider: Callable[[], dict[str, Any]] = lambda: {"status": "unavailable"}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        latest_provider = self.latest
        shared_secret = self.secret
        readiness_provider = self.readiness
        status_provider = self.status_provider

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib callback name
                if self.path == "/v1/health":
                    self._json(200, {"status": "ok"})
                elif self.path == "/v1/readiness":
                    ready = bool(readiness_provider())
                    self._json(200 if ready else 503, {"ready": ready})
                elif self.path == "/v1/status":
                    if not self._authorized("/v1/status"):
                        self._json(401, {"error": "unauthorized"})
                        return
                    self._json(200, status_provider())
                elif self.path == "/v1/latest":
                    if not self._authorized("/v1/latest"):
                        self._json(401, {"error": "unauthorized"})
                        return
                    envelope = latest_provider()
                    self._json(200, envelope) if envelope else self._json(404, {"error": "no_bundle"})
                else:
                    self._json(404, {"error": "not_found"})

            def _authorized(self, path: str) -> bool:
                return request_is_authorized(
                    self.headers, shared_secret, "GET", path,
                )

            def _json(self, status: int, value: Any) -> None:
                data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="redundancy-http")
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None
