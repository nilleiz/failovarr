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
does so and supplies the result for analysis. An unreleased Draft-PR keeps its
planned semantic version through CI corrections; increment it only for a new
release candidate, after publication, or on explicit user direction. Release
candidates require the full qualification set once on the final PR tree.
After a squash merge, the release gate may reuse that evidence only when the
merged PR is unambiguous, its tree ID is identical to `main`, and all required
suites succeeded. Do not trigger tests merely by marking a Draft PR ready.

## Required public delivery lifecycle

For every approved public change, create or reuse one feature branch, commit
the complete change deliberately, push it promptly, and create or update one
Draft PR against `main`. Complete the PR template and report the resulting
Actions URL, then wait for the operator to say that the run has finished; do
not poll Actions. Only then inspect the completed checks and logs. For a CI
failure, first provide a concrete fix plan and wait for explicit approval
before editing; commit and push each approved fix to the same Draft PR and
repeat the wait.

When the final PR tree is selectively green, run one full qualification on
that exact head and wait for the operator's completed-run notice. Only a green
final qualification authorizes setting the PR ready and squash-merging it.
After the merge, publish the requested pre-release through the release
workflow. Stable promotion is a separate, explicit operator decision after
local-lab approval; it must promote the same verified tagged assets in place,
not rebuild or retag them.
