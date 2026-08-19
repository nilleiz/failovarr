import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from failovarr.remote_storage import SftpBundleStore, _python_interpreter


class SftpSubprocessTests(unittest.TestCase):
    def config(self):
        return SimpleNamespace(
            storage_endpoint="sftp://storage.internal:22",
            storage_username="lab-user",
            storage_password="do-not-leak-this-password",
            storage_container="bundles-root",
            storage_options={"known_hosts_path": "/data/known_hosts", "timeout_seconds": 3},
            state_path="/data/state",
        )

    def test_virtualenv_python_wins_over_uwsgi_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "bin" / "python"
            interpreter.parent.mkdir()
            interpreter.write_bytes(b"")
            interpreter.chmod(0o700)
            with patch("failovarr.remote_storage.sys.prefix", directory), patch(
                "failovarr.remote_storage.sys.executable", "/dispatcharrpy/bin/uwsgi"
            ):
                self.assertEqual(_python_interpreter(), str(interpreter))

    @patch("failovarr.remote_storage.ensure_vendor_dependencies")
    @patch("failovarr.remote_storage.subprocess.run")
    def test_credentials_are_sent_only_on_stdin(self, run, _ensure):
        run.return_value = subprocess.CompletedProcess([], 0, '{"status":"success","result":{}}', "")
        store = SftpBundleStore(self.config())
        store.write_latest({"payload": {"sequence": 1}, "payload_sha256": "abc"})

        args, kwargs = run.call_args
        self.assertNotIn("do-not-leak-this-password", " ".join(args[0]))
        self.assertNotIn("do-not-leak-this-password", json.dumps(kwargs["env"]))
        self.assertEqual(json.loads(kwargs["input"])["connection"]["password"], "do-not-leak-this-password")
        self.assertTrue(kwargs["capture_output"])

    @patch("failovarr.remote_storage.ensure_vendor_dependencies")
    @patch("failovarr.remote_storage.subprocess.run")
    def test_read_returns_worker_envelope(self, run, _ensure):
        envelope = {"payload": {"sequence": 7}, "payload_sha256": "digest"}
        run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps({"status": "success", "result": {"envelope": envelope}}), ""
        )
        self.assertEqual(SftpBundleStore(self.config()).read_latest(), envelope)

    @patch("failovarr.remote_storage.ensure_vendor_dependencies")
    @patch("failovarr.remote_storage.subprocess.run")
    def test_host_key_inspection_uses_isolated_helper(self, run, _ensure):
        run.return_value = subprocess.CompletedProcess(
            [], 0,
            json.dumps({"status": "success", "result": {
                "host_key": "ssh-ed25519 AAAATEST", "fingerprint": "SHA256:test",
            }}), "",
        )
        result = SftpBundleStore(self.config()).inspect_host_key()
        self.assertEqual(result["fingerprint"], "SHA256:test")
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["operation"], "inspect_host_key")

    @patch("failovarr.remote_storage.ensure_vendor_dependencies")
    @patch("failovarr.remote_storage.subprocess.run")
    def test_worker_error_does_not_echo_stderr(self, run, _ensure):
        run.return_value = subprocess.CompletedProcess(
            [], 1,
            '{"status":"error","error_type":"PermissionDenied","error_stage":"write_bundle"}',
            "secret diagnostic",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            r"SFTP helper failed \(PermissionDenied at write_bundle\)",
        ) as caught:
            SftpBundleStore(self.config()).read_latest()
        self.assertNotIn("secret diagnostic", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
