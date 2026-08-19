"""High-level export, verification, planning and apply orchestration."""

from __future__ import annotations

import logging
import posixpath
import threading
import time
import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from .bundle import canonical_json, create_envelope, verify_envelope
from .client_identity import export_client_identity, require_matching_client_identity
from .config import (
    CORE_SETTING_GROUPS,
    ReplicationConfig,
    SUPPORTED_DOMAINS,
    full_export_settings,
    parse_int_ids,
    parse_protected_records,
)
from .domains import (
    apply_domains,
    export_domains,
    initialize_domains,
    plan_domains,
    summarize_conflicts,
    summarize_plan,
)
from .storage import AtomicJsonStore
from .remote_storage import create_bundle_store
from .transport import BundleHttpServer, fetch_latest, fetch_status
from .autostart import FLUSH_KEY, LEASE_TTL_SECONDS, STOP_KEY, refresh_service_lease, release_service_lease
from .vip import VipManager


class ExpectedBundleState(ValueError):
    """A normal follower wait state that must not produce recurring traces."""

    code = "waiting"


class OwnBundleState(ExpectedBundleState):
    code = "own_bundle"


class BundleNotNewerState(ExpectedBundleState):
    code = "not_newer"


class ReplicationEngine:
    def __init__(self, settings: Mapping[str, Any], logger: logging.Logger | None = None):
        self.raw_settings = dict(settings)
        self.config = ReplicationConfig.from_settings(settings)
        from .logging_utils import plugin_logger

        self.logger = plugin_logger(logger or logging.getLogger(__name__))
        self.state_store = AtomicJsonStore(self.config.state_path, self.config.node_id)
        self.store = (
            create_bundle_store(self.config)
            if self.config.mode in {"shared_storage", "hybrid"}
            else AtomicJsonStore(posixpath.join(self.config.state_path, "direct-unused"), self.config.node_id)
        )
        self.outbound_store = AtomicJsonStore(
            posixpath.join(self.config.state_path, "outbound"), self.config.node_id,
        )
        self._latest: dict[str, Any] | None = None

    def _full_export_config(self) -> ReplicationConfig:
        return ReplicationConfig.from_settings(full_export_settings(self.raw_settings))

    @staticmethod
    def _scope_fingerprint(config: ReplicationConfig) -> str:
        scope = {
            "domains": list(config.domains),
            "core_setting_keys": list(config.core_setting_keys),
        }
        return hashlib.sha256(canonical_json(scope)).hexdigest()

    def is_authoritative(self) -> bool:
        state = self.state_store.read_state()
        if "authoritative" in state:
            return bool(state["authoritative"])
        return self.config.role == "leader"

    def export_now(self, handoff: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_authoritative():
            raise ValueError("Only the authoritative node may export")
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            sequence = max(
                int(state.get("exported_sequence", 0)),
                int(state.get("applied_sequence", 0)),
            ) + 1
            export_config = self._full_export_config()
            envelope = create_envelope(
                cluster_id=self.config.cluster_id,
                source_node=self.config.node_id,
                sequence=sequence,
                domains=export_domains(export_config),
                secret=export_config.shared_secret,
                client_identity=export_client_identity(export_config),
                handoff=handoff,
                scope={
                    "domains": list(export_config.domains),
                    "core_setting_keys": list(export_config.core_setting_keys),
                },
            )
            state.update({
                "exported_sequence": sequence,
                "exported_hash": envelope["payload_sha256"],
            })
            # Reserve the sequence locally before publishing the bundle. A
            # failed publish may skip a sequence but can never reuse one.
            self.state_store.write_state(state)
            # The direct HTTP server may run in another uWSGI worker. Persist
            # its latest response in node-local state instead of relying only
            # on this process' memory.
            self.outbound_store.write_latest(envelope)
            if self.config.mode in {"shared_storage", "hybrid"}:
                self.store.write_latest(envelope)
            self._latest = envelope
            state = self.state_store.read_state()
            state["last_export_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state_store.write_state(state)
            self.logger.debug("Exported bundle sequence %s", sequence)
        return {
            "status": "exported", "sequence": sequence,
            "hash": envelope["payload_sha256"],
            "domains": {name: len(rows) for name, rows in envelope["payload"]["domains"].items()},
            "scope": envelope["payload"]["scope"],
        }

    def latest_for_http(self) -> dict[str, Any] | None:
        try:
            return self.outbound_store.read_latest()
        except FileNotFoundError:
            return self._latest

    def _load_candidate(self) -> dict[str, Any]:
        direct_error: Exception | None = None
        if self.config.mode in {"direct", "hybrid"}:
            try:
                return fetch_latest(self.config.peer_url, self.config.shared_secret)
            except Exception as exc:
                direct_error = exc
                if self.config.mode == "direct":
                    raise
                # Hybrid's storage fallback is designed to cover a temporarily
                # unreachable peer.  The background service reports a real
                # failure in a deduplicated way when both paths fail; logging
                # every successful fallback at warning level would flood the
                # Dispatcharr container log.
                self.logger.debug("Direct peer pull failed; trying shared storage: %s", type(exc).__name__)
        try:
            return self.store.read_latest()
        except Exception:
            if direct_error:
                raise RuntimeError(f"Direct pull and shared-storage fallback failed; direct error: {direct_error}")
            raise

    def verified_candidate(self, require_new: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        envelope = self._load_candidate()
        payload = verify_envelope(envelope, self.config.shared_secret, self.config.cluster_id)
        if payload["source_node"] == self.config.node_id:
            raise OwnBundleState("Latest bundle was exported by this node; use different node names")
        state = self.state_store.read_state()
        known_sequence = max(
            int(state.get("applied_sequence", 0)),
            int(state.get("exported_sequence", 0)),
        )
        if require_new and payload["sequence"] <= known_sequence:
            same_verified_payload = (
                payload["sequence"] == int(state.get("applied_sequence", 0))
                and envelope["payload_sha256"] == state.get("applied_hash")
            )
            changed_local_scope = (
                state.get("applied_scope_fingerprint") != self._scope_fingerprint(self.config)
            )
            if not (same_verified_payload and changed_local_scope):
                raise BundleNotNewerState(
                    f"Bundle sequence {payload['sequence']} is not newer than local sequence {known_sequence}"
                )
        return envelope, payload

    def config_for_payload(self, payload: Mapping[str, Any]) -> ReplicationConfig:
        """Validate bundle availability without replacing follower-local scope."""
        scope = payload.get("scope")
        if scope is None:
            return self.config
        domains = scope.get("domains")
        core_keys = scope.get("core_setting_keys")
        if (
            not isinstance(domains, list)
            or not domains
            or not all(isinstance(domain, str) and domain in SUPPORTED_DOMAINS for domain in domains)
            or not isinstance(core_keys, list)
            or not all(isinstance(key, str) and key in CORE_SETTING_GROUPS for key in core_keys)
        ):
            raise ValueError("Bundle scope is invalid")
        missing_domains = sorted(set(self.config.domains) - set(domains))
        missing_core = sorted(set(self.config.core_setting_keys) - set(core_keys))
        if missing_domains or missing_core:
            missing = ", ".join([
                *(f"domain {name}" for name in missing_domains),
                *(f"Settings group {name}" for name in missing_core),
            ])
            raise ValueError(
                "Bundle does not contain the selected Follower scope "
                f"({missing}). Export a new complete bundle from Main."
            )
        return self.config

    @staticmethod
    def _selected_payload_domains(payload: Mapping[str, Any], config: ReplicationConfig) -> dict[str, Any]:
        domains = payload.get("domains")
        if not isinstance(domains, Mapping):
            raise ValueError("Bundle domains must be an object")
        missing = [domain for domain in config.domains if domain not in domains]
        if missing:
            raise ValueError(
                "Bundle is missing selected Follower domains: " + ", ".join(missing)
            )
        selected = {domain: domains[domain] for domain in config.domains}
        # Main intentionally signs every supported Settings group. The
        # Follower owns its local Settings-group choice, so filter before all
        # planning/apply paths. Without this, initialization could preserve a
        # local dvr_settings row and then attempt to insert Main's same unique
        # key, even though DVR was not selected for replication.
        core_records = selected.get("core_settings")
        if isinstance(core_records, list) and all(isinstance(row, Mapping) for row in core_records):
            selected["core_settings"] = [
                row for row in core_records
                if row.get("key") in set(config.core_setting_keys)
            ]
        return selected

    def bundle_info(self) -> dict[str, Any]:
        """Return sanitized follower-facing metadata about the latest bundle."""
        try:
            envelope = self._load_candidate()
        except FileNotFoundError:
            return {"status": "missing", "message": "No Main bundle is available in the configured storage yet."}
        except Exception as exc:
            return {
                "status": "unavailable",
                "message": f"The latest bundle could not be read ({type(exc).__name__}).",
            }
        try:
            payload = verify_envelope(envelope, self.config.shared_secret, self.config.cluster_id)
        except Exception:
            return {
                "status": "invalid",
                "message": "The latest bundle could not be verified. Check the cluster name and shared secret.",
            }
        if payload["source_node"] == self.config.node_id:
            return {
                "status": "own_bundle",
                "source_node": payload["source_node"],
                "sequence": payload["sequence"],
                "message": "The latest bundle was exported by this node. Choose a different Follower node name.",
            }
        try:
            scoped = self.config_for_payload(payload)
        except ValueError:
            return {
                "status": "incompatible",
                "message": "The verified Main bundle does not contain this Follower's selected import scope.",
            }
        state = self.state_store.read_state()
        applied_sequence = int(state.get("applied_sequence", 0))
        known_sequence = max(applied_sequence, int(state.get("exported_sequence", 0)))
        same_payload = (
            payload["sequence"] == applied_sequence
            and envelope["payload_sha256"] == state.get("applied_hash")
        )
        same_scope = state.get("applied_scope_fingerprint") == self._scope_fingerprint(scoped)
        if same_payload and same_scope:
            return {
                "status": "current",
                "source_node": payload["source_node"],
                "sequence": payload["sequence"],
                "scope": {
                    "domains": list(scoped.domains),
                    "core_setting_keys": list(scoped.core_setting_keys),
                },
                "message": f"Latest verified Main bundle {payload['sequence']} is already applied for this Follower scope.",
            }
        if payload["sequence"] <= known_sequence and not same_payload:
            return {
                "status": "stale",
                "source_node": payload["source_node"],
                "sequence": payload["sequence"],
                "message": "The latest verified Main bundle is older than this node's known replication sequence.",
            }
        return {
            "status": "verified",
            "source_node": payload["source_node"],
            "sequence": payload["sequence"],
            "scope": {
                "domains": list(scoped.domains),
                "core_setting_keys": list(scoped.core_setting_keys),
            },
            "message": f"Verified Main bundle {payload['sequence']} from {payload['source_node']} is ready to import.",
        }

    def preview_latest(self) -> dict[str, Any]:
        _envelope, payload = self.verified_candidate(require_new=False)
        scoped_config = self.config_for_payload(payload)
        client_identity = require_matching_client_identity(payload.get("client_identity"), scoped_config)
        plans = plan_domains(self._selected_payload_domains(payload, scoped_config), scoped_config)
        return {
            "status": "preview", "sequence": payload["sequence"],
            "source_node": payload["source_node"], "summary": summarize_plan(plans),
            "client_identity": client_identity,
            "conflicts": summarize_conflicts(plans),
            "scope": payload.get("scope", {"domains": list(scoped_config.domains), "core_setting_keys": list(scoped_config.core_setting_keys)}),
        }

    def apply_latest(self) -> dict[str, Any]:
        if self.is_authoritative():
            raise ValueError("Only a follower may apply a peer bundle")
        grant_handoff: Mapping[str, Any] | None = None
        cold_takeover: Mapping[str, Any] | None = None
        with self.state_store.exclusive_lock():
            envelope, payload = self.verified_candidate(require_new=True)
            scoped_config = self.config_for_payload(payload)
            client_identity = require_matching_client_identity(payload.get("client_identity"), scoped_config)
            handoff = payload.get("handoff")
            if handoff is not None:
                if handoff.get("source_node") != payload["source_node"]:
                    raise ValueError("Handoff source does not match bundle source")
                if handoff.get("target_node") != self.config.node_id:
                    raise ValueError("Handoff bundle targets a different node")
                if handoff.get("phase") not in {"prepare", "grant", "cold_shutdown"}:
                    raise ValueError("Unsupported handoff phase")
                if handoff.get("phase") == "grant":
                    state_before = self.state_store.read_state()
                    if handoff.get("prepare_hash") != state_before.get("applied_hash"):
                        raise ValueError("Handoff grant does not match the applied preparation bundle")
                    if payload["source_node"] != state_before.get("source_node"):
                        raise ValueError("Handoff grant source changed after preparation")
                    grant_handoff = handoff
                if handoff.get("phase") == "cold_shutdown":
                    if self.config.deployment_mode != "cold_standby":
                        raise ValueError("Cold-standby handoff received by an online node")
                    cold_takeover = handoff
            result = apply_domains(self._selected_payload_domains(payload, scoped_config), scoped_config)
            result.update({
                "sequence": payload["sequence"],
                "source_node": payload["source_node"],
                "client_identity": client_identity,
            })
            if result["status"] == "applied":
                self._remember_disabled_records(result)
                state = self.state_store.read_state()
                state.update({
                    "applied_sequence": payload["sequence"],
                    "applied_hash": envelope["payload_sha256"],
                    "source_node": payload["source_node"],
                    "bundle_scope": payload.get("scope"),
                    "applied_scope": {
                        "domains": list(scoped_config.domains),
                        "core_setting_keys": list(scoped_config.core_setting_keys),
                    },
                    "applied_scope_fingerprint": self._scope_fingerprint(scoped_config),
                    "last_import_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                self.state_store.write_state(state)
                self.logger.info(
                    "Imported bundle (source=%s sequence=%s)",
                    payload["source_node"], payload["sequence"],
                )
            else:
                result["conflicts"] = summarize_conflicts(result.get("domains", {}))
                result["message"] = (
                    "Import was blocked because the follower database uses conflicting "
                    "record identities. Preview the conflicts or explicitly initialize "
                    "this follower from the Main node."
                )
        # Avoid returning commands or settings values to the plugin action log.
        result.pop("domains", None)
        if grant_handoff is not None:
            result["handoff"] = self._accept_handoff_grant(grant_handoff)
        if cold_takeover is not None and result.get("status") == "applied":
            result["handoff"] = self._accept_cold_shutdown(cold_takeover)
        return result

    def initialize_follower(self) -> dict[str, Any]:
        """Back up and replace the selected follower graph from a verified bundle."""
        if self.is_authoritative():
            raise ValueError("Only a follower may be initialized from a peer bundle")
        with self.state_store.exclusive_lock():
            envelope, payload = self.verified_candidate(require_new=True)
            scoped_config = self.config_for_payload(payload)
            client_identity = require_matching_client_identity(payload.get("client_identity"), scoped_config)

            # Dispatcharr's own full-backup service is deliberately called
            # immediately before the transaction so the operator has a native
            # recovery point for this destructive, one-time operation.
            from apps.backups import services as backup_services

            backup_path = backup_services.create_backup()
            result = initialize_domains(self._selected_payload_domains(payload, scoped_config), scoped_config)
            result.update({
                "sequence": payload["sequence"],
                "source_node": payload["source_node"],
                "client_identity": client_identity,
                "backup": posixpath.basename(str(backup_path)),
            })
            if result["status"] == "initialized":
                self._remember_disabled_records(result)
                state = self.state_store.read_state()
                state.update({
                    "applied_sequence": payload["sequence"],
                    "applied_hash": envelope["payload_sha256"],
                    "source_node": payload["source_node"],
                    "bundle_scope": payload.get("scope"),
                    "applied_scope": {
                        "domains": list(scoped_config.domains),
                        "core_setting_keys": list(scoped_config.core_setting_keys),
                    },
                    "applied_scope_fingerprint": self._scope_fingerprint(scoped_config),
                    "last_import_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                self.state_store.write_state(state)
        result.pop("domains", None)
        return result

    def _remember_disabled_records(self, result: dict[str, Any]) -> None:
        """Keep newly imported disabled records local until the user opts in."""
        disabled = result.pop("new_disabled_record_ids", {})
        # Accept a 0.6.3 result during a rolling code upgrade.
        legacy_profiles = tuple(result.pop("new_disabled_output_profile_ids", ()))
        if legacy_profiles:
            disabled = {**disabled, "output_profiles": legacy_profiles}
        if not disabled:
            return
        from .node_config import load_node_config, save_node_config

        current = load_node_config(self.raw_settings)
        try:
            protected_records = {
                domain: list(ids)
                for domain, ids in parse_protected_records(
                    current.get("protected_records", {}),
                ).items()
            }
        except ValueError:
            protected_records = {}
        # Migrate the pre-0.6 flat Output Profile protection field while
        # retaining it for rolling upgrades.
        legacy_output_ids = parse_int_ids(current.get("protected_output_profile_ids", ""))
        if legacy_output_ids:
            protected_records["output_profiles"] = sorted(
                set(protected_records.get("output_profiles", ())) | set(legacy_output_ids)
            )
        kept_local = {}
        for domain, record_ids in disabled.items():
            record_ids = sorted({int(record_id) for record_id in record_ids})
            if not record_ids:
                continue
            existing = set(protected_records.get(domain, ()))
            existing.update(record_ids)
            protected_records[domain] = sorted(existing)
            kept_local[domain] = record_ids
        current["protected_records"] = protected_records
        current["protected_output_profile_ids"] = ",".join(
            str(profile_id)
            for profile_id in protected_records.get("output_profiles", ())
        )
        save_node_config(current)
        if kept_local:
            result["new_records_kept_local"] = kept_local

    def _remember_disabled_output_profiles(self, result: dict[str, Any]) -> None:
        """Compatibility shim for 0.6.3 integrations and tests."""
        self._remember_disabled_records(result)
        kept = result.pop("new_records_kept_local", {}).get("output_profiles", [])
        if kept:
            result["new_output_profiles_kept_local"] = kept

    def status(self) -> dict[str, Any]:
        state = self.state_store.read_state()
        return {
            "status": "ok",
            "node_id": self.config.node_id,
            "cluster_id": self.config.cluster_id,
            "role": self.config.role,
            "authoritative": self.is_authoritative(),
            "mode": self.config.mode,
            "domains": list(self.config.domains),
            "automatic_apply": self.config.automatic_apply,
            "deployment_mode": self.config.deployment_mode,
            "client_access": self.client_status(),
            "state": state,
        }

    def _vip(self) -> VipManager:
        if self.config.client_access_mode != "plugin_vip":
            raise ValueError("client_access_mode is not plugin_vip")
        return VipManager(
            self.config.client_vip, self.config.vip_interface, self.config.vip_prefix_length,
        )

    def is_client_ready(self) -> bool:
        state = self.state_store.read_state()
        return self.is_authoritative() and bool(state.get("client_serving", False))

    def client_status(self) -> dict[str, Any]:
        result = {
            "mode": self.config.client_access_mode,
            "ready": self.is_client_ready(),
        }
        if self.config.client_access_mode == "plugin_vip":
            try:
                result["vip_owned"] = self._vip().owns()
            except Exception as exc:
                result["vip_owned"] = False
                result["error"] = str(exc)
        return result

    def acquire_client_vip(self) -> dict[str, Any]:
        if not self.is_authoritative():
            raise ValueError("Only the authoritative node may acquire the client VIP")
        with self.state_store.exclusive_lock():
            vip_result = self._vip().acquire()
            state = self.state_store.read_state()
            state["client_serving"] = True
            self.state_store.write_state(state)
        return {"status": "acquired", **vip_result, "ready": True}

    def release_client_vip(self) -> dict[str, Any]:
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            state["client_serving"] = False
            self.state_store.write_state(state)
            vip_result = self._vip().release()
        return {"status": "released", **vip_result, "ready": False}

    def set_proxy_readiness(self, ready: bool) -> dict[str, Any]:
        if self.config.client_access_mode != "external_proxy":
            raise ValueError("client_access_mode is not external_proxy")
        if ready and not self.is_authoritative():
            raise ValueError("Only the authoritative node may become client-ready")
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            state["client_serving"] = bool(ready)
            self.state_store.write_state(state)
        return {"status": "ready" if ready else "not_ready", "ready": bool(ready)}

    def peer_status(self) -> dict[str, Any]:
        state = self.state_store.read_state()
        return {
            "node_id": self.config.node_id,
            "authoritative": self.is_authoritative(),
            "client_ready": self.is_client_ready(),
            "applied_sequence": int(state.get("applied_sequence", 0)),
            "applied_hash": state.get("applied_hash", ""),
            "exported_sequence": int(state.get("exported_sequence", 0)),
            "handoff_phase": (state.get("handoff") or {}).get("phase"),
        }

    def request_handoff(self) -> dict[str, Any]:
        if not self.is_authoritative():
            raise ValueError("Only the authoritative node may request a handoff")
        if self.config.mode not in {"direct", "hybrid"}:
            raise ValueError("Automatic planned handoff requires direct or hybrid transport")
        if not self.config.peer_url or not self.config.peer_node_id:
            raise ValueError("Automatic planned handoff requires peer_url and peer_node_id")
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            existing = state.get("handoff") or {}
            if existing.get("phase") not in {None, "complete", "failed"}:
                raise ValueError(f"A handoff is already in progress: {existing.get('phase')}")
            state["handoff"] = {
                "phase": "requested",
                "target_node": self.config.peer_node_id,
            }
            self.state_store.write_state(state)
        return {"status": "queued", "target_node": self.config.peer_node_id}

    def export_on_shutdown(self) -> dict[str, Any]:
        """Publish a final cold-standby handoff during a graceful stop.

        Docker SIGTERM reaches Dispatcharr's plugin lifecycle.  An abrupt kill
        remains intentionally non-promoting: the peer can import the last
        normal bundle, but must not infer that the old writer is fenced.
        """
        if not self.is_authoritative():
            return {"status": "skipped", "message": "Follower has no final bundle to export"}
        handoff = None
        if self.config.deployment_mode == "cold_standby" and self.config.peer_node_id:
            handoff = {
                "phase": "cold_shutdown",
                "source_node": self.config.node_id,
                "target_node": self.config.peer_node_id,
            }
        result = self.export_now(handoff)
        self.logger.info(
            "Final shutdown bundle exported (sequence=%s)", result["sequence"],
        )
        return result

    def _disable_client_serving(self) -> None:
        if self.config.client_access_mode == "plugin_vip":
            self.release_client_vip()
        else:
            with self.state_store.exclusive_lock():
                state = self.state_store.read_state()
                state["client_serving"] = False
                self.state_store.write_state(state)

    def _enable_client_serving(self) -> dict[str, Any]:
        if self.config.client_access_mode == "plugin_vip":
            return self.acquire_client_vip()
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            state["client_serving"] = self.config.client_access_mode == "external_proxy"
            self.state_store.write_state(state)
        return {"ready": self.config.client_access_mode == "external_proxy"}

    def _accept_handoff_grant(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        if handoff.get("source_node") == self.config.node_id:
            raise ValueError("Refusing a self-issued handoff grant")
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            state["authoritative"] = True
            state["handoff"] = {
                "phase": "complete",
                "source_node": handoff.get("source_node"),
            }
            self.state_store.write_state(state)
        client = self._enable_client_serving()
        return {"status": "accepted", "client": client}

    def _accept_cold_shutdown(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            state["authoritative"] = True
            state["handoff"] = {
                "phase": "cold_takeover_complete",
                "source_node": handoff.get("source_node"),
            }
            self.state_store.write_state(state)
        client = self._enable_client_serving()
        self.logger.info("Accepted expected cold-standby takeover")
        return {"status": "accepted_cold_takeover", "client": client}

    def recover_cold_standby(self) -> dict[str, Any] | None:
        """Follow a newer signed writer before this node starts serving.

        This handles a returning former Main after the Slave took over.  It is
        not failure detection and never promotes a node on a missing peer.
        """
        if self.config.deployment_mode != "cold_standby":
            return None
        try:
            _envelope, payload = self.verified_candidate(require_new=False)
        except (FileNotFoundError, OwnBundleState):
            return None
        except Exception as exc:
            self.logger.warning("Cold-standby startup could not inspect latest bundle: %s", type(exc).__name__)
            return None
        state = self.state_store.read_state()
        known = max(int(state.get("applied_sequence", 0)), int(state.get("exported_sequence", 0)))
        if payload["source_node"] == self.config.node_id or payload["sequence"] <= known:
            return None
        with self.state_store.exclusive_lock():
            updated = self.state_store.read_state()
            updated["authoritative"] = False
            updated["client_serving"] = False
            self.state_store.write_state(updated)
        result = self.apply_latest()
        self.logger.info(
            "Cold-standby startup followed newer node %s (sequence=%s)",
            payload["source_node"], payload["sequence"],
        )
        return result

    def process_handoff(self) -> dict[str, Any] | None:
        state = self.state_store.read_state()
        handoff = state.get("handoff") or {}
        phase = handoff.get("phase")
        if phase == "requested":
            metadata = {
                "phase": "prepare",
                "source_node": self.config.node_id,
                "target_node": handoff["target_node"],
            }
            exported = self.export_now(metadata)
            with self.state_store.exclusive_lock():
                updated = self.state_store.read_state()
                updated["handoff"] = {
                    **metadata,
                    "phase": "waiting_for_apply",
                    "prepare_hash": exported["hash"],
                    "prepare_sequence": exported["sequence"],
                }
                self.state_store.write_state(updated)
            return {"status": "waiting_for_peer", "sequence": exported["sequence"]}
        if phase != "waiting_for_apply":
            return None

        peer = fetch_status(self.config.peer_url, self.config.shared_secret)
        if peer.get("node_id") != handoff.get("target_node"):
            raise ValueError("Handoff peer returned an unexpected node_id")
        if peer.get("applied_hash") != handoff.get("prepare_hash"):
            return {"status": "waiting_for_peer", "sequence": handoff.get("prepare_sequence")}

        self._disable_client_serving()
        with self.state_store.exclusive_lock():
            updated = self.state_store.read_state()
            updated["authoritative"] = False
            updated["handoff"] = {
                **handoff,
                "phase": "granting",
            }
            self.state_store.write_state(updated)

        # The old client endpoint is fenced before this signed grant becomes visible.
        grant = {
            "phase": "grant",
            "source_node": self.config.node_id,
            "target_node": handoff["target_node"],
            "prepare_hash": handoff["prepare_hash"],
        }
        exported = self._export_after_surrender(grant)
        with self.state_store.exclusive_lock():
            updated = self.state_store.read_state()
            updated["handoff"] = {**grant, "phase": "complete", "grant_hash": exported["hash"]}
            self.state_store.write_state(updated)
        return {"status": "granted", "sequence": exported["sequence"]}

    def _export_after_surrender(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        with self.state_store.exclusive_lock():
            state = self.state_store.read_state()
            sequence = max(int(state.get("exported_sequence", 0)), int(state.get("applied_sequence", 0))) + 1
            prepared = self.latest_for_http()
            if not prepared or prepared.get("payload_sha256") != handoff.get("prepare_hash"):
                raise RuntimeError("Prepared handoff bundle is no longer available")
            prepared_payload = prepared["payload"]
            envelope = create_envelope(
                cluster_id=self.config.cluster_id,
                source_node=self.config.node_id,
                sequence=sequence,
                domains=prepared_payload["domains"],
                secret=self.config.shared_secret,
                client_identity=prepared_payload.get("client_identity"),
                handoff=handoff,
                scope=prepared_payload.get("scope"),
            )
            state.update({"exported_sequence": sequence, "exported_hash": envelope["payload_sha256"]})
            self.state_store.write_state(state)
            self.outbound_store.write_latest(envelope)
            if self.config.mode in {"shared_storage", "hybrid"}:
                self.store.write_latest(envelope)
            self._latest = envelope
        return {"status": "exported", "sequence": sequence, "hash": envelope["payload_sha256"]}


class BackgroundService:
    def __init__(
        self, engine: ReplicationEngine, redis_client=None,
        lease_token: str | None = None, manage_http: bool = True,
    ):
        self.engine = engine
        self.redis_client = redis_client
        self.lease_token = lease_token
        self.manage_http = manage_http
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease_thread: threading.Thread | None = None
        self._http: BundleHttpServer | None = None
        self._http_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._flushed = False
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._reported_error_signature: tuple[str, str] | None = None

    def _idle_result(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, OwnBundleState):
            return {
                "status": "waiting",
                "reason": exc.code,
                "message": "Latest bundle belongs to this node; choose a different Follower node name.",
            }
        if isinstance(exc, BundleNotNewerState):
            return {"status": "waiting", "reason": exc.code, "message": "No newer Main bundle is available."}
        return {"status": "waiting", "reason": "missing", "message": "No Main bundle is available yet."}

    def _record_background_error(self, operation: str, exc: Exception) -> None:
        """Keep unexpected recurring failures visible without flooding logs."""
        signature = (operation, type(exc).__name__)
        self.last_error = f"{operation} failed ({signature[1]})"
        if signature == self._reported_error_signature:
            return
        self._reported_error_signature = signature
        self.engine.logger.error(
            "%s failed (%s); identical failures are suppressed until recovery",
            operation, signature[1],
        )

    def _record_success(self) -> None:
        self.last_error = None
        self._reported_error_signature = None

    def start(self) -> None:
        config = self.engine.config
        if self.manage_http and (
            config.mode in {"direct", "hybrid"}
            or config.client_access_mode == "external_proxy"
        ):
            self._http = BundleHttpServer(
                config.bind_host, config.bind_port,
                self.engine.latest_for_http, config.shared_secret,
                self.engine.is_client_ready,
            )
            self._http.status_provider = self.engine.peer_status
            self._http.start()
        # The lease is acquired before this service object is created.  A
        # Cold Standby startup import can legitimately exceed the 30-second
        # lease TTL, so refresh ownership before any recovery or import work.
        # The loop still stops safely if another owner replaces this token.
        if self.redis_client is not None and self.lease_token:
            self._lease_thread = threading.Thread(
                target=self._lease_loop, daemon=True, name="redundancy-lease",
            )
            self._lease_thread.start()
        try:
            recovered = self.engine.recover_cold_standby()
            if recovered is not None:
                self.last_result = recovered
            elif not self.engine.is_authoritative() and (
                config.import_on_start or config.deployment_mode == "cold_standby"
            ):
                try:
                    self.last_result = self.engine.apply_latest()
                except (ExpectedBundleState, FileNotFoundError) as exc:
                    self.last_result = self._idle_result(exc)
        except Exception as exc:
            self._record_background_error("Startup synchronization", exc)
        self._thread = threading.Thread(target=self._run, daemon=True, name="failovarr")
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self.engine.is_authoritative():
                        handoff_result = self.engine.process_handoff()
                        self.last_result = handoff_result or self.engine.export_now()
                    elif self.engine.config.deployment_mode == "cold_standby":
                        # Cold Standby imports once at service start. It keeps
                        # only lifecycle/lease ownership afterwards and never
                        # polls a storage bundle while passive.
                        self.last_result = self.last_result or {
                            "status": "waiting", "message": "Cold Standby follower is waiting for activation.",
                        }
                    elif self.engine.config.automatic_apply:
                        try:
                            self.last_result = self.engine.apply_latest()
                        except (ExpectedBundleState, FileNotFoundError) as exc:
                            self.last_result = self._idle_result(exc)
                    else:
                        try:
                            self.last_result = self.engine.preview_latest()
                        except (ExpectedBundleState, FileNotFoundError) as exc:
                            self.last_result = self._idle_result(exc)
                    self._record_success()
                except Exception as exc:
                    self._record_background_error("Replication cycle", exc)
                self._stop.wait(self.engine.config.interval_seconds)
        finally:
            self._shutdown_http()

    def _lease_loop(self) -> None:
        try:
            while not self._stop.wait(2):
                if self.redis_client.get(STOP_KEY):
                    if self.redis_client.get(FLUSH_KEY):
                        try:
                            self.flush_on_shutdown()
                        finally:
                            self.redis_client.delete(FLUSH_KEY)
                    self._stop.set()
                    break
                if not refresh_service_lease(self.redis_client, self.lease_token):
                    self.last_error = "Container-wide service lease was lost"
                    self._stop.set()
                    break
        except Exception as exc:
            self.last_error = f"Service lease check failed: {exc}"
            self._stop.set()
            self.engine.logger.exception("Redundancy service lease loop failed")
        finally:
            self._shutdown_http()
            release_service_lease(self.redis_client, self.lease_token)

    def _shutdown_http(self) -> None:
        with self._http_lock:
            if self._http:
                self._http.stop()
                self._http = None

    def stop(self) -> None:
        self._stop.set()
        self._shutdown_http()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._lease_thread and self._lease_thread.is_alive():
            self._lease_thread.join(timeout=5)
        if self.redis_client is not None and self.lease_token:
            release_service_lease(self.redis_client, self.lease_token)

    def flush_on_shutdown(self) -> dict[str, Any] | None:
        with self._flush_lock:
            if self._flushed:
                return self.last_result
            self._flushed = True
            try:
                result = self.engine.export_on_shutdown()
                self.last_result = result
                return result
            except Exception as exc:
                self.last_error = str(exc)
                self.engine.logger.exception("Final shutdown export failed")
                raise

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_result": self.last_result,
            "last_error": self.last_error,
            "lease_ttl_seconds": LEASE_TTL_SECONDS if self.lease_token else None,
        }
