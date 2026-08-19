# Failovarr

> **Work in Progress / Development Preview — not production ready.** Failovarr
> is an AI-assisted vibe coding project under human direction. Automated tests
> and operator acceptance reduce risk; they are not an independent audit.

Failovarr provides signed, active/passive configuration replication
between independent Dispatcharr installations. **Main** is the only
authoritative writer. A **Follower** downloads or reads its verified bundles,
previews the selected changes and applies them transactionally.

It preserves stable client-facing identities such as Channel UUIDs, Channel
Profile names and Output Profile IDs. Hardware-specific records can remain
local on a Follower under the same ID.

> This is a Development Preview. It is not Active/Active replication and it
> does not automatically resolve a two-node network partition.

## Start here

1. [Getting Started](Getting-Started.md) — install and configure Main and Follower.
2. [Settings Walkthrough](Settings-Walkthrough.md) — every Assistant and native
   Plugin Settings field.
3. [First Sync and Initialization](First-Sync-and-Initialization.md) — preview,
   import and the one-time initializer.
4. [Operations and Planned Handoff](Operations-and-Planned-Handoff.md) — daily
   operation, Cold Standby and returning nodes.
5. [Feature Status](Features.md) — verified, incomplete and planned work.

For network choices see [Deployment Modes](Deployment-Modes.md); for supported
destinations see [Storage Backends](Storage-Backends.md).

## Core safety rules

- Keep independent Dispatcharr and PostgreSQL backups.
- Use distinct node names. In online modes, also use distinct management IPs.
- Never allow two nodes to become authoritative writers at once.
- Main exports the complete supported scope; each Follower controls its own
  local import selection, deletion policy and record protection.
- Verify every Preview before the first Import or Initialization.

See [Security and Limitations](Security-and-Limitations.md) for the exact trust
boundaries and [Troubleshooting](Troubleshooting.md) for common recovery steps.
