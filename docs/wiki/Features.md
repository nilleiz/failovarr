# Feature Status

## Implemented and verified

- CORE-001 — Signed, replay-protected bundles.
- CORE-002 — Complete Main export and Follower-local scope/protection.
- CORE-003 — Identity-aware planning, transactional apply and deletion safety.
- UX-001 — Assistant plus safe native Plugin Settings.
- TRANSPORT-001 — Direct Pull, readiness and shared storage.
- STORAGE-001 — Filesystem, WebDAV, S3, SFTP and SMB.

## Implemented, verification incomplete

- HANDOFF-001 — Planned online handoff and plugin-managed Linux VIP; no
  witness/fencing proof.
- RECOVERY-001 — Production-like initialization and restore; no independent
  full rollback proof.

## Planned

- HA-001 — Witness/fencing and automatic promotion.
- SEC-001 — Encrypted bundle payloads.
- OPS-001 — Celery execution for large operations.
- OPS-002 — Plugin-code distribution/version coordination.
- RECOVERY-002 — Full restore, promotion and rollback acceptance suite.

See the canonical repository [feature catalogue](../docs/FEATURES.md) for
evidence and contribution rules.
