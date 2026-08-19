# Operations and Planned Handoff

## Routine checks

Use **Refresh replication status** to confirm node role, replication service,
last export/import and sequence state. Main should export after relevant
configuration changes. Follower status should show a verified or current Main
bundle, never an unexplained storage error.

`Already up to date` is a healthy result: the latest signed Main bundle was
already applied for this Follower scope. It is not an import failure.

## Cold Standby switch

1. Export a fresh bundle from Main.
2. Confirm that the old container is stopped and no longer owns the shared
   production IP.
3. Start the Follower with automatic replication enabled.
4. Verify its one-time startup import, service status, client identity and a
   real M3U/EPG/stream request.
5. Only then expose the reused client IP to users.

Returning nodes always begin as Followers of the newer authority. Never restore
an old Main backup over newer Follower changes.

## Planned online handoff

Planned handoff requires Direct or Hybrid transport, both services running,
correct peer addresses and deliberate operator control. The sequence is:

1. Main exports a preparation bundle.
2. Follower pulls, verifies, previews/applies and confirms the exact hash.
3. The old node is fenced from the client endpoint.
4. Authority is granted to the peer.

If confirmation fails before fencing, the old node remains authoritative. If a
failure occurs after fencing, both nodes may be passive until an operator
intervenes. Do not add automatic promotion without reliable fencing or a third
witness.
