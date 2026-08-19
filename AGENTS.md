# Failovarr agent instructions

Public code and documentation must never contain real hostnames, IP addresses,
backups, user data, credentials, tokens, configuration snapshots or production
logs. Use synthetic fixtures only.

Failovarr is active/passive: exactly one authoritative writer, follower pull,
signed replay-protected bundles, node-local state and overrides. Never claim
automatic two-node failover, quorum or fencing.

Planning and architecture use GPT-5.6 Sol/high. Approved implementation,
commits and pushes use GPT-5.6 Terra/medium. If the host cannot switch models
automatically, pause once and request the exact manual selection.

Before implementation, update `docs/requirements-matrix.md`. Every PR must
meet `.github/pull_request_template.md` and `docs/testing/TEST-MATRIX.md`.
Prefer targeted inspection and existing scripts; do not repeat large reads or
poll CI. Evaluate repeated deterministic work for an idempotent script.

Unit and synthetic integration tests run in GitHub Actions only. Do not run
local Docker or production-like test environments unless an operator explicitly
does so and supplies the result for analysis. Release candidates require the
full qualification set on the exact release commit.
