"""Isolated AsyncSSH worker for gevent/uWSGI-safe SFTP operations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import posixpath
import sys
import uuid


PLUGIN_PARENT = Path(__file__).resolve().parent.parent
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from failovarr.vendor_loader import ensure_vendor_dependencies  # noqa: E402


class StagedSftpError(Exception):
    """Carry only a fixed operation stage and the original exception type."""

    def __init__(self, stage: str, cause: Exception):
        super().__init__(stage)
        self.stage = stage
        self.error_type = type(cause).__name__


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _decoded(value: bytes) -> dict:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Remote bundle must be a JSON object")
    return result


def _bundle_name(envelope: dict) -> str:
    sequence = int(envelope["payload"]["sequence"])
    digest = str(envelope["payload_sha256"])
    return f"bundles/{sequence:020d}-{digest}.json"


async def _connect(connection: dict):
    import asyncssh

    options = {
        "port": int(connection["port"]),
        "username": str(connection["username"]),
        "password": str(connection["password"]),
        "known_hosts": connection.get("known_hosts"),
        "connect_timeout": float(connection["timeout_seconds"]),
    }
    if connection.get("private_key_path"):
        options["client_keys"] = [str(connection["private_key_path"])]
        if connection.get("private_key_passphrase"):
            options["passphrase"] = str(connection["private_key_passphrase"])
    return await asyncssh.connect(
        str(connection["host"]),
        **options,
    )


async def _write(connection: dict, envelope: dict) -> dict:
    client = None
    stage = "connect"
    try:
        client = await _connect(connection)
        stage = "start_sftp_client"
        async with client.start_sftp_client() as sftp:
            base = str(connection["base"])
            stage = "ensure_bundles_directory"
            await sftp.makedirs(posixpath.join(base, "bundles"), exist_ok=True)
            stage = "ensure_temporary_directory"
            await sftp.makedirs(posixpath.join(base, ".tmp"), exist_ok=True)
            final_name = _bundle_name(envelope)
            final = posixpath.join(base, final_name)
            temporary = posixpath.join(base, ".tmp", uuid.uuid4().hex + ".json")
            stage = "write_bundle"
            async with sftp.open(temporary, "wb") as handle:
                await handle.write(_encoded(envelope))
            stage = "publish_bundle"
            await sftp.rename(temporary, final)
            pointer_temp = posixpath.join(base, ".tmp", uuid.uuid4().hex + ".latest")
            stage = "write_pointer"
            async with sftp.open(pointer_temp, "wb") as handle:
                await handle.write(_encoded({"object": final_name}))
            stage = "publish_pointer"
            await sftp.posix_rename(pointer_temp, posixpath.join(base, "latest.json"))
            stage = "prune_bundles"
            entries = await sftp.glob(posixpath.join(base, "bundles", "*.json"))
            for entry in sorted(str(item) for item in entries)[:-3]:
                await sftp.remove(entry)
        return {}
    except Exception as exc:
        raise StagedSftpError(stage, exc) from exc
    finally:
        if client is not None:
            client.close()
            await client.wait_closed()


async def _read(connection: dict) -> dict:
    client = None
    stage = "connect"
    try:
        client = await _connect(connection)
        stage = "start_sftp_client"
        async with client.start_sftp_client() as sftp:
            base = str(connection["base"])
            stage = "read_pointer"
            async with sftp.open(posixpath.join(base, "latest.json"), "rb") as handle:
                pointer = _decoded(await handle.read())
            stage = "read_bundle"
            async with sftp.open(posixpath.join(base, str(pointer["object"])), "rb") as handle:
                envelope = _decoded(await handle.read())
        return {"envelope": envelope}
    except Exception as exc:
        raise StagedSftpError(stage, exc) from exc
    finally:
        if client is not None:
            client.close()
            await client.wait_closed()


async def _test(connection: dict) -> dict:
    client = None
    stage = "connect"
    try:
        client = await _connect(connection)
        stage = "start_sftp_client"
        async with client.start_sftp_client() as sftp:
            base = str(connection["base"])
            directory = posixpath.join(base, ".connection-test")
            stage = "ensure_test_directory"
            await sftp.makedirs(directory, exist_ok=True)
            token = uuid.uuid4().hex
            temporary = posixpath.join(directory, token + ".tmp")
            final = posixpath.join(directory, token + ".json")
            payload = _encoded({"probe": token})
            stage = "write_test_file"
            async with sftp.open(temporary, "wb") as handle:
                await handle.write(payload)
            stage = "publish_test_file"
            await sftp.rename(temporary, final)
            try:
                stage = "read_test_file"
                async with sftp.open(final, "rb") as handle:
                    if await handle.read() != payload:
                        raise RuntimeError("SFTP connection test returned different content")
            finally:
                stage = "remove_test_file"
                await sftp.remove(final)
        return {}
    except Exception as exc:
        raise StagedSftpError(stage, exc) from exc
    finally:
        if client is not None:
            client.close()
            await client.wait_closed()


async def _inspect_host_key(connection: dict) -> dict:
    """Fetch a key for explicit first-time trust, never silently trust it."""
    client = None
    stage = "connect"
    try:
        unverified = dict(connection)
        unverified["known_hosts"] = None
        client = await _connect(unverified)
        stage = "read_host_key"
        key = client.get_server_host_key()
        exported = key.export_public_key(format_name="openssh")
        host_key = exported.decode("ascii").strip() if isinstance(exported, bytes) else str(exported).strip()
        return {"host_key": host_key, "fingerprint": key.get_fingerprint("sha256")}
    except Exception as exc:
        raise StagedSftpError(stage, exc) from exc
    finally:
        if client is not None:
            client.close()
            await client.wait_closed()


async def _dispatch(request: dict) -> dict:
    connection = request["connection"]
    timeout = float(connection["timeout_seconds"]) + 5
    if request["operation"] == "write":
        return await asyncio.wait_for(_write(connection, request["envelope"]), timeout)
    if request["operation"] == "read":
        return await asyncio.wait_for(_read(connection), timeout)
    if request["operation"] == "test":
        return await asyncio.wait_for(_test(connection), timeout)
    if request["operation"] == "inspect_host_key":
        return await asyncio.wait_for(_inspect_host_key(connection), timeout)
    raise ValueError("Unsupported SFTP helper operation")


def main() -> int:
    stage = "read_request"
    temporary_key: Path | None = None
    try:
        request = json.load(sys.stdin)
        connection = request["connection"]
        stage = "prepare_dependencies"
        ensure_vendor_dependencies(str(connection["state_path"]))
        private_key = str(connection.pop("private_key", ""))
        if private_key:
            key_dir = Path(str(connection["state_path"])) / ".sftp-client-keys"
            key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary_key = key_dir / f"{uuid.uuid4().hex}.key"
            descriptor = os.open(temporary_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(private_key)
            connection["private_key_path"] = str(temporary_key)
        stage = "dispatch"
        result = asyncio.run(_dispatch(request))
        print(json.dumps({"status": "success", "result": result}, separators=(",", ":")))
        return 0
    except Exception as exc:
        # Error messages from third-party libraries can echo endpoints or
        # paths. Return only the exception type and never the request.
        if isinstance(exc, StagedSftpError):
            stage = exc.stage
            error_type = exc.error_type
        else:
            error_type = type(exc).__name__
        print(json.dumps({
            "status": "error",
            "error_type": error_type,
            "error_stage": stage,
        }, separators=(",", ":")))
        return 1
    finally:
        if temporary_key is not None:
            try:
                temporary_key.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
