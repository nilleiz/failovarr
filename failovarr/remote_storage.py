"""Atomic remote bundle publication backends."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ElementTree
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from .vendor_loader import ensure_vendor_dependencies

from .logging_utils import plugin_logger

logger = plugin_logger(logging.getLogger("dispatcharr.plugins.failovarr"))


def _encoded(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _decoded(value: bytes) -> dict[str, Any]:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Remote bundle must be a JSON object")
    return result


def _bundle_name(envelope: Mapping[str, Any]) -> str:
    sequence = int(envelope["payload"]["sequence"])
    digest = str(envelope["payload_sha256"])
    return f"bundles/{sequence:020d}-{digest}.json"


def _retained_bundle_names(names: list[str], retention: int = 3) -> list[str]:
    """Return completed bundle names which may be removed after publication."""
    candidates = sorted(name for name in names if name.startswith("bundles/") and name.endswith(".json"))
    return candidates[:-retention]


class FilesystemBundleStore:
    """Shared bind-mount backend with the same layout as remote stores."""

    def __init__(self, config):
        self.directory = Path(config.shared_path)
        self.retention = config.bundle_retention

    def _path(self, name: str) -> Path:
        return self.directory / name

    def _atomic_write(self, path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        with temporary.open("wb") as handle:
            handle.write(_encoded(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _prune(self) -> None:
        directory = self._path("bundles")
        if not directory.exists():
            return
        for name in _retained_bundle_names([f"bundles/{item.name}" for item in directory.iterdir() if item.is_file()], self.retention):
            self._path(name).unlink(missing_ok=True)

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        final = _bundle_name(envelope)
        final_path = self._path(final)
        if not final_path.exists():
            self._atomic_write(final_path, envelope)
        self._atomic_write(self._path("latest.json"), {"object": final})
        self._prune()

    def read_latest(self) -> dict[str, Any]:
        pointer = _decoded(self._path("latest.json").read_bytes())
        # Accept the pre-0.5 filesystem layout only long enough for an
        # existing lab to be upgraded without manual cleanup.
        if "object" not in pointer:
            return pointer
        return _decoded(self._path(str(pointer["object"])).read_bytes())

    def test_connection(self) -> None:
        token = uuid.uuid4().hex
        temporary = self._path(f".connection-test/{token}.tmp")
        final = self._path(f".connection-test/{token}.json")
        self._atomic_write(temporary, {"probe": token})
        os.replace(temporary, final)
        if _decoded(final.read_bytes()) != {"probe": token}:
            raise RuntimeError("Filesystem connection test returned different content")
        final.unlink(missing_ok=True)


def _python_interpreter() -> str:
    """Return Python even when the hosting uWSGI worker owns sys.executable."""
    candidates = [Path(sys.prefix) / "bin" / "python", Path(sys.executable)]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("No executable Python interpreter is available for the SFTP helper")


class WebDavBundleStore:
    def __init__(self, config):
        import requests

        self.requests = requests
        self.base = f"{config.storage_endpoint}/{quote(config.storage_container, safe='/')}"
        self.auth = (config.storage_username, config.storage_password) if config.storage_username else None
        self.verify = config.storage_options.get("ca_path", True)
        self.timeout = float(config.storage_options.get("timeout_seconds", 20))

    def _url(self, name: str) -> str:
        return f"{self.base}/{quote(name, safe='/')}"

    def _request(self, method: str, name: str, **kwargs):
        response = self.requests.request(
            method, self._url(name), auth=self.auth, verify=self.verify,
            timeout=self.timeout, **kwargs,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"WebDAV {method} failed with HTTP {response.status_code}")
        return response

    def _ensure_collection(self, name: str) -> None:
        root_response = self.requests.request(
            "MKCOL", self.base, auth=self.auth, verify=self.verify, timeout=self.timeout,
        )
        if root_response.status_code not in {201, 301, 405}:
            raise RuntimeError(f"WebDAV root MKCOL failed with HTTP {root_response.status_code}")
        current = ""
        for part in name.strip("/").split("/"):
            current = f"{current}/{part}".strip("/")
            response = self.requests.request(
                "MKCOL", self._url(current), auth=self.auth, verify=self.verify, timeout=self.timeout,
            )
            if response.status_code not in {201, 301, 405}:
                raise RuntimeError(f"WebDAV MKCOL failed with HTTP {response.status_code}")

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        self._ensure_collection("bundles")
        final = _bundle_name(envelope)
        temporary = f".tmp/{uuid.uuid4().hex}.json"
        self._ensure_collection(".tmp")
        self._request("PUT", temporary, data=_encoded(envelope), headers={"Content-Type": "application/json"})
        self._request("MOVE", temporary, headers={"Destination": self._url(final), "Overwrite": "F"})
        pointer_temp = f".tmp/{uuid.uuid4().hex}.latest"
        self._request("PUT", pointer_temp, data=_encoded({"object": final}), headers={"Content-Type": "application/json"})
        self._request("MOVE", pointer_temp, headers={"Destination": self._url("latest.json"), "Overwrite": "T"})
        self._prune()

    def _prune(self) -> None:
        response = self.requests.request(
            "PROPFIND", self._url("bundles"), auth=self.auth, verify=self.verify,
            timeout=self.timeout, headers={"Depth": "1"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"WebDAV PROPFIND failed with HTTP {response.status_code}")
        try:
            root = ElementTree.fromstring(response.content)
            names = []
            for href in root.findall(".//{DAV:}href"):
                value = href.text or ""
                name = value.rstrip("/").rsplit("/", 1)[-1]
                if name.endswith(".json"):
                    names.append(f"bundles/{name}")
        except ElementTree.ParseError as exc:
            raise RuntimeError("WebDAV PROPFIND returned invalid XML") from exc
        for name in _retained_bundle_names(names):
            self._request("DELETE", name)

    def read_latest(self) -> dict[str, Any]:
        pointer = _decoded(self._request("GET", "latest.json").content)
        return _decoded(self._request("GET", str(pointer["object"])).content)

    def test_connection(self) -> None:
        self._ensure_collection(".connection-test")
        token = uuid.uuid4().hex
        temporary = f".connection-test/{token}.tmp"
        final = f".connection-test/{token}.json"
        payload = _encoded({"probe": token})
        self._request("PUT", temporary, data=payload)
        self._request("MOVE", temporary, headers={"Destination": self._url(final), "Overwrite": "F"})
        if self._request("GET", final).content != payload:
            raise RuntimeError("WebDAV connection test returned different content")
        self._request("DELETE", final)


class S3BundleStore:
    def __init__(self, config):
        ensure_vendor_dependencies(config.state_path)
        import boto3
        from botocore.config import Config

        options = config.storage_options
        verify = options.get("ca_path", True)
        self.prefix = str(options.get("prefix", "failovarr")).strip("/")
        self.bucket = config.storage_container
        self.client = boto3.client(
            "s3",
            endpoint_url=config.storage_endpoint,
            aws_access_key_id=config.storage_username,
            aws_secret_access_key=config.storage_password,
            aws_session_token=options.get("session_token"),
            region_name=options.get("region", "us-east-1"),
            verify=verify,
            config=Config(s3={"addressing_style": options.get("addressing_style", "path")}),
        )

    def _key(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        final = _bundle_name(envelope)
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(final), Body=_encoded(envelope),
            ContentType="application/json", IfNoneMatch="*",
        )
        self.client.put_object(
            Bucket=self.bucket, Key=self._key("latest.json"),
            Body=_encoded({"object": final}), ContentType="application/json",
        )
        self._prune()

    def _prune(self) -> None:
        prefix = self._key("bundles/")
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        names = [str(item["Key"])[len(self.prefix) + 1:] if self.prefix else str(item["Key"])
                 for item in response.get("Contents", [])]
        for name in _retained_bundle_names(names):
            self.client.delete_object(Bucket=self.bucket, Key=self._key(name))

    def read_latest(self) -> dict[str, Any]:
        pointer = _decoded(self.client.get_object(Bucket=self.bucket, Key=self._key("latest.json"))["Body"].read())
        return _decoded(self.client.get_object(Bucket=self.bucket, Key=self._key(str(pointer["object"])))["Body"].read())

    def test_connection(self) -> None:
        token = uuid.uuid4().hex
        key = self._key(f".connection-test/{token}.json")
        payload = _encoded({"probe": token})
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload)
        if self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read() != payload:
            raise RuntimeError("S3 connection test returned different content")
        self.client.delete_object(Bucket=self.bucket, Key=key)


class SftpBundleStore:
    def __init__(self, config):
        ensure_vendor_dependencies(config.state_path)
        parsed = urlsplit(config.storage_endpoint)
        self.worker = Path(__file__).with_name("sftp_worker.py")
        self.timeout = float(config.storage_options.get("timeout_seconds", 20))
        self.connection = {
            "host": parsed.hostname,
            "port": parsed.port or 22,
            "username": config.storage_username,
            "password": config.storage_password,
            "known_hosts": config.storage_options["known_hosts_path"],
            "private_key": config.storage_options.get("private_key", ""),
            "private_key_passphrase": config.storage_options.get("private_key_passphrase", ""),
            "base": "/" + config.storage_container.strip("/"),
            "state_path": config.state_path,
            "timeout_seconds": self.timeout,
        }

    def _run(self, operation: str, envelope: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request = {"operation": operation, "connection": self.connection}
        if envelope is not None:
            request["envelope"] = envelope
        # uWSGI's gevent runtime can prevent AsyncSSH from completing its
        # handshake. Run only the SFTP client in a clean Python interpreter.
        # Credentials are supplied on stdin, never argv, environment or logs.
        environment = {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TZ"}
        }
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            completed = subprocess.run(
                [_python_interpreter(), str(self.worker)],
                input=json.dumps(request, separators=(",", ":")),
                text=True,
                capture_output=True,
                timeout=self.timeout + 10,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("SFTP helper exceeded its configured timeout") from exc
        try:
            response = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("SFTP helper returned an invalid response") from exc
        if completed.returncode != 0 or response.get("status") != "success":
            error_type = str(response.get("error_type", "SftpHelperError"))
            error_stage = str(response.get("error_stage", "unknown"))
            safe_stages = {
                "read_request", "prepare_dependencies", "dispatch", "connect",
                "start_sftp_client", "ensure_bundles_directory",
                "ensure_temporary_directory", "write_bundle", "publish_bundle",
                "write_pointer", "publish_pointer", "prune_bundles", "read_pointer", "read_bundle",
                "ensure_test_directory", "write_test_file", "publish_test_file",
                "read_test_file", "remove_test_file", "read_host_key",
            }
            if error_stage not in safe_stages:
                error_stage = "unknown"
            raise RuntimeError(f"SFTP helper failed ({error_type} at {error_stage})")
        result = response.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError("SFTP helper returned an invalid result")
        return result

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        self._run("write", envelope)

    def read_latest(self) -> dict[str, Any]:
        result = self._run("read")
        envelope = result.get("envelope")
        if not isinstance(envelope, dict):
            raise RuntimeError("SFTP helper did not return a bundle")
        return envelope

    def test_connection(self) -> None:
        self._run("test")

    def inspect_host_key(self) -> dict[str, Any]:
        result = self._run("inspect_host_key")
        if not isinstance(result.get("host_key"), str) or not result["host_key"].startswith("ssh-"):
            raise RuntimeError("SFTP helper returned an invalid host key")
        return result


class SmbBundleStore:
    def __init__(self, config):
        ensure_vendor_dependencies(config.state_path)
        import smbclient
        import logging

        # smbprotocol logs negotiated session details at INFO by default. The
        # Dispatcharr container log should contain plugin lifecycle events,
        # never third-party transport chatter or user names.
        logging.getLogger("smbprotocol").setLevel(logging.WARNING)

        parsed = urlsplit(config.storage_endpoint)
        self.smbclient = smbclient
        self.server = parsed.hostname
        self.base = "\\\\" + self.server + "\\" + config.storage_container.replace("/", "\\")
        smbclient.register_session(
            self.server,
            username=config.storage_username,
            password=config.storage_password,
            port=parsed.port or 445,
            encrypt=True,
            connection_timeout=int(config.storage_options.get("timeout_seconds", 20)),
        )

    def _path(self, name: str) -> str:
        return self.base.rstrip("\\") + "\\" + name.replace("/", "\\")

    def _write_file(self, path: str, value: bytes) -> None:
        with self.smbclient.open_file(path, mode="wb") as handle:
            handle.write(value)

    def write_latest(self, envelope: Mapping[str, Any]) -> None:
        self.smbclient.makedirs(self._path("bundles"), exist_ok=True)
        self.smbclient.makedirs(self._path(".tmp"), exist_ok=True)
        final = _bundle_name(envelope)
        temporary = f".tmp/{uuid.uuid4().hex}.json"
        self._write_file(self._path(temporary), _encoded(envelope))
        # smbclient.rename uses replace_if_exists=False for immutable bundles.
        self.smbclient.rename(self._path(temporary), self._path(final))
        pointer_temp = f".tmp/{uuid.uuid4().hex}.latest"
        self._write_file(self._path(pointer_temp), _encoded({"object": final}))
        # smbclient.replace maps to SMB2 FILE_RENAME_INFORMATION with
        # ReplaceIfExists. Some otherwise-valid SMB3 servers deny DELETE on
        # an existing target even when they permit write/rename (Samba shares
        # configured for simple authenticated users are a common example).
        # The fallback never changes an immutable bundle. A reader that sees a
        # torn small pointer falls back to the newest complete bundle below,
        # so it cannot import a partial export.
        try:
            self.smbclient.replace(self._path(pointer_temp), self._path("latest.json"))
        except Exception as exc:
            logger.warning(
                "SMB server rejected atomic latest-pointer replace (%s); using safe pointer fallback",
                type(exc).__name__,
            )
            self._write_file(self._path("latest.json"), _encoded({"object": final}))
            try:
                self.smbclient.remove(self._path(pointer_temp))
            except OSError:
                pass
        self._prune()

    def _prune(self) -> None:
        names = [f"bundles/{name}" for name in self.smbclient.listdir(self._path("bundles"))]
        for name in _retained_bundle_names(names):
            self.smbclient.remove(self._path(name))

    def read_latest(self) -> dict[str, Any]:
        try:
            with self.smbclient.open_file(self._path("latest.json"), mode="rb") as handle:
                pointer = _decoded(handle.read())
            with self.smbclient.open_file(self._path(str(pointer["object"])), mode="rb") as handle:
                return _decoded(handle.read())
        except Exception as exc:
            logger.warning(
                "SMB latest pointer could not be read (%s); selecting newest complete bundle",
                type(exc).__name__,
            )
            names = sorted(
                name for name in self.smbclient.listdir(self._path("bundles"))
                if name.endswith(".json")
            )
            if not names:
                raise RuntimeError("SMB storage contains no completed bundle") from exc
            with self.smbclient.open_file(self._path(f"bundles/{names[-1]}"), mode="rb") as handle:
                return _decoded(handle.read())

    def test_connection(self) -> None:
        directory = self._path(".connection-test")
        self.smbclient.makedirs(directory, exist_ok=True)
        token = uuid.uuid4().hex
        temporary = self._path(f".connection-test/{token}.tmp")
        final = self._path(f".connection-test/{token}.json")
        payload = _encoded({"probe": token})
        self._write_file(temporary, payload)
        self.smbclient.rename(temporary, final)
        try:
            with self.smbclient.open_file(final, mode="rb") as handle:
                if handle.read() != payload:
                    raise RuntimeError("SMB connection test returned different content")
        finally:
            self.smbclient.remove(final)


def create_bundle_store(config):
    if config.storage_backend == "filesystem":
        return FilesystemBundleStore(config)
    store_type = {
        "webdav": WebDavBundleStore,
        "s3": S3BundleStore,
        "sftp": SftpBundleStore,
        "smb": SmbBundleStore,
    }[config.storage_backend]
    return store_type(config)


def test_storage_connection(config) -> dict[str, Any]:
    """Exercise the configured backend without publishing a replication bundle."""
    if config.mode not in {"shared_storage", "hybrid"}:
        raise ValueError("Storage is not used by the selected transport mode")
    create_bundle_store(config).test_connection()
    return {
        "status": "success",
        "message": "Storage connection verified",
        "backend": config.storage_backend,
        "checks": ["authenticate", "write", "read", "publish", "cleanup"],
    }


def inspect_sftp_host_key(config) -> dict[str, Any]:
    """Return a key only for an explicit trust decision in the setup UI."""
    if config.storage_backend != "sftp":
        raise ValueError("SFTP host-key inspection requires the SFTP backend")
    result = SftpBundleStore(config).inspect_host_key()
    return {
        "status": "success",
        "message": "SFTP host key retrieved. Compare its fingerprint, then explicitly trust it.",
        "host_key": result["host_key"],
        "fingerprint": result.get("fingerprint", ""),
    }
