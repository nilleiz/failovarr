# First Sync and Initialization

## Normal import

Normal Import is non-destructive by default. It validates the signed bundle,
cluster, sequence, selected Follower scope, natural identities, protected
records and local overrides before a transaction changes Dispatcharr data.

1. Export from Main.
2. Refresh the Follower bundle state.
3. Preview and read creates, updates, deletions and conflicts.
4. Resolve unexpected conflicts.
5. Import the verified bundle.

The Assistant labels a bundle as **current** when the same signed payload is
already applied for the current Follower scope. Preview remains available;
normal Import is disabled. If local data in the selected scope later drifts,
preview it and use **Force import latest bundle** only after its confirmation.
That action reapplies only the exact current signed payload and Follower scope;
it cannot accept an older, changed or untrusted bundle and never repeats a
Handoff or promotion. If a scope change makes the same bundle applicable again,
it is shown as verified and normal Import applies it.

## M3U Hash Key parity

Before every preview or import, Failovarr compares the Follower's global M3U
Hash Key with the exact signed Main value. It is independent of the selected
replication scope because Dispatcharr uses it for every periodic M3U update.
On a mismatch, automatic replication stops with a conflict. The Assistant can
adopt only the signed Main value after confirmation, binds that confirmation to
the reviewed bundle hash, and retries that same import. It does not run
Dispatcharr's destructive stream-rehash task: replicated streams already carry
the authoritative Main hashes.

## 0.8.0 lifecycle-bundle upgrade

Version 2 bundles carry the authoritative Stream and channel-group/account
lifecycle state, including `is_stale` and `last_seen`. Upgrade Main first and
export a fresh bundle before upgrading a Follower. A new Follower intentionally
refuses a version 1 bundle when its selected scope includes either lifecycle
domain; this prevents stale local availability flags from being retained
silently. No initialization or direct database cleanup is required for this
upgrade.

## Initialize follower from Main

Use Initialization only when a Follower was built independently and normal
Import reports identity conflicts across the selected graph. It is never
automatic.

Initialization:

1. requires the exact typed confirmation shown by the Assistant;
2. creates a native Dispatcharr full backup on the Follower;
3. preserves applicable DVR history and remaps it through stable channel
   identity where possible;
4. clears only rebuildable EPG cache data;
5. replaces the selected graph inside one transaction.

Locked records, unsupported external references, client-identity mismatches or
unresolvable recording relationships block the operation before partial data is
applied. Keep the generated backup until the Follower has passed real client
checks.

## Local overrides

Overrides are evaluated after signature verification but before planning and
apply. They may alter node-specific values such as FFmpeg hardware parameters,
but can never change a record ID. Use protected records when an entire record
must remain local instead.
