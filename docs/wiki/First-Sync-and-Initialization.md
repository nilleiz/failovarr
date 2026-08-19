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
Import is disabled. If a scope change makes the same bundle applicable again,
it is shown as verified.

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
