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

Storage credentials, private SFTP keys and plugin-specific settings are not
replicated by default. Version-3 bundles can include Dispatcharr user accounts
only in an AES-GCM encrypted section. The key is derived from the cluster
secret, and the section is authenticated by both AES-GCM and the signed bundle.
It contains password hashes, API keys, account properties and user relations;
those values are never included in previews or logs. A Follower applies only
the usernames selected in its node-local import rules, retains its own user
primary keys, and does not delete accounts. Selected users also receive Main's
account privileges, including staff and superuser flags, so restrict selection
to trusted accounts. Formats 1 and 2 remain readable without user changes.

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
