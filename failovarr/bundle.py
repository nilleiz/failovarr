"""Deterministic signed snapshot envelopes."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Mapping


BUNDLE_FORMAT = 1


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sign_payload(payload: Mapping[str, Any], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()


def create_envelope(
    *, cluster_id: str, source_node: str, sequence: int,
    domains: Mapping[str, Any], secret: str, created_at: str | None = None,
    client_identity: Mapping[str, Any] | None = None,
    handoff: Mapping[str, Any] | None = None,
    scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "format": BUNDLE_FORMAT,
        "cluster_id": cluster_id,
        "source_node": source_node,
        "sequence": int(sequence),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "domains": domains,
    }
    if client_identity is not None:
        payload["client_identity"] = dict(client_identity)
    if handoff is not None:
        payload["handoff"] = dict(handoff)
    if scope is not None:
        payload["scope"] = dict(scope)
    return {
        "payload": payload,
        "payload_sha256": payload_hash(payload),
        "signature": sign_payload(payload, secret),
    }


def verify_envelope(envelope: Mapping[str, Any], secret: str, cluster_id: str) -> dict[str, Any]:
    try:
        payload = envelope["payload"]
        declared_hash = str(envelope["payload_sha256"])
        signature = str(envelope["signature"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed replication envelope") from exc
    if not isinstance(payload, dict):
        raise ValueError("Envelope payload must be an object")
    actual_hash = payload_hash(payload)
    if not hmac.compare_digest(actual_hash, declared_hash):
        raise ValueError("Bundle payload hash mismatch")
    expected = sign_payload(payload, secret)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Bundle signature is invalid")
    if payload.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"Unsupported bundle format: {payload.get('format')}")
    if payload.get("cluster_id") != cluster_id:
        raise ValueError("Bundle belongs to a different cluster")
    if not isinstance(payload.get("sequence"), int) or payload["sequence"] < 1:
        raise ValueError("Bundle sequence must be a positive integer")
    if not isinstance(payload.get("domains"), dict):
        raise ValueError("Bundle domains must be an object")
    if "client_identity" in payload and not isinstance(payload["client_identity"], dict):
        raise ValueError("Bundle client_identity must be an object")
    if "handoff" in payload and not isinstance(payload["handoff"], dict):
        raise ValueError("Bundle handoff must be an object")
    if "scope" in payload and not isinstance(payload["scope"], dict):
        raise ValueError("Bundle scope must be an object")
    return payload
