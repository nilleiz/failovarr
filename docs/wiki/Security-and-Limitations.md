# Security and Limitations

## Security model

Bundles are canonically serialized and authenticated with HMAC-SHA256. The
Follower validates the signature, cluster identity, source node, monotonic
sequence and selected scope before planning any database mutation. Replay of an
older sequence is rejected.

HMAC authenticates but does **not** encrypt. Provider definitions can contain
sensitive URLs or credentials. Protect management traffic and storage with a
firewall, VLAN, WireGuard and/or TLS. Treat Main configuration profiles that
include storage passwords as plaintext secrets.

Secrets, Dispatcharr users, API keys, private SFTP keys and plugin-specific
settings are not replicated by default. Client identity is compared as a
non-reversible keyed fingerprint, not copied into a bundle.

## Current limitations

- No Active/Active database replication or multiple writers.
- No automatic failover/promotion without external fencing or a third witness.
- No generic replication of third-party plugin code or settings.
- No automatic replica of large derived EPG programme tables, runtime buffers,
  sessions, logs, backups or statistics.
- Direct container stop does not guarantee a final bundle export.
- Plugin-managed VIP is Linux-only and controls only its own secondary address.

## Release status

This project remains a Development Preview until it completes a rehearsed,
production-representative restore/failover test with appropriate fencing.
