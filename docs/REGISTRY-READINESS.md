# Dispatcharr registry readiness

Failovarr is not ready for the official Dispatcharr plugin registry while it
is a Work in Progress / Development Preview.

Before opening a future registry pull request, confirm:

- a public stable release exists with a reachable versioned ZIP and checksum;
- the repository remains MIT licensed and has an active security policy;
- the manifest contains accurate author, repository, help and source URLs;
- `failovarr/logo.png` and a concise registry README are ready;
- an independent restore/rollback and upgrade acceptance was recorded;
- witness/fencing limitations remain clear and no automatic-failover claim is made.

The future contribution target is `Dispatcharr/Plugins`, directory
`plugins/failovarr/`, with PR title `[failovarr] Add Failovarr`.
