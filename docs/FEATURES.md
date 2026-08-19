# Failovarr feature status

This page is generated in intent from `docs/features.toml`; keep the IDs and
statuses aligned when changing it.

## Implemented and verified

- **CORE-001** — Signed, replay-protected configuration bundles.
- **CORE-002** — Complete Main export, Follower-local scope and local record protection.
- **CORE-003** — Stable IDs, dry-run planning, transactional apply and deletion safety.
- **UX-001** — Setup Assistant and native non-secret Plugin Settings.
- **TRANSPORT-001** — Direct Pull, readiness and shared-storage transport.
- **STORAGE-001** — Filesystem, WebDAV, S3, SFTP and SMB.

## Implemented, verification incomplete

- **HANDOFF-001** — Planned online handoff and plugin-managed Linux VIP; no witness/fencing proof.
- **RECOVERY-001** — Production-like initialization and restore workflow; no independent full rollback proof.

## Planned

- **HA-001** — Witness/fencing and safe automatic promotion.
- **SEC-001** — Encrypted bundle payloads.
- **OPS-001** — Celery execution for large exports/imports.
- **OPS-002** — Plugin-code distribution and version coordination.
- **RECOVERY-002** — Full restore, promotion and rollback acceptance suite.
