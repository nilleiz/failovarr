# Troubleshooting

## Setup and storage

**Setup incomplete / Node name is required**

Enter a valid node name, cluster name and shared secret in the Assistant. A
Follower can import the Main profile before its storage/secret fields have been
saved locally.

**No Main bundle is available**

Save and export on Main, then test the Follower storage connection and click
**Refresh latest bundle**.

**Bundle verification failed**

Check cluster name, shared secret, storage endpoint and selected path. Do not
disable signature verification.

**SFTP host key cannot be saved**

The known-hosts directory is not writable by the Dispatcharr container user.
Use the default node-local path under `/data` or correct ownership/mount
permissions.

## Import

**Already up to date**

No action is needed. The latest verified Main bundle is already applied for
this Follower scope. Preview can still be used for inspection.

**Latest bundle is stale**

This node knows a newer sequence. Do not force an old bundle; export from the
current authority and investigate unexpected promotion or state loss.

**Bundle format 1 does not include lifecycle domains**

An upgraded Follower selected Stream or channel-group/account replication, but
Main still exported a pre-0.8.0 bundle. Upgrade Main, export a fresh format-2
or later bundle, then preview it again. Do not reset the Follower or manually
clear stale Streams: the normal import updates the signed lifecycle fields.

**Main users are unavailable for selection**

The bundle predates format 3 or its encrypted user section cannot be verified.
Update the Follower first, then update Main and export a fresh bundle. Formats
1 and 2 still import other selected data but do not change Dispatcharr users.

**A user import reports missing dependencies**

Check that every referenced Group, Permission and selected Channel Profile
exists on the Follower or is included in the selected Main import. Failovarr
validates selected user relationships before changing data.

**ID has different natural key / record is locked**

Review the conflict. For an independently built Follower, Initialization may
be appropriate. Never remove Dispatcharr locks merely to force a replication.

**Channel Stream IDs changed after a mapping plugin ran**

Failovarr matches Channel Stream assignments by Channel and Stream, not by the
join-row ID. A current release should therefore import the recreated rows. If
Main should also remove assignments that exist only on the Follower, enable
**Mirror Main stream assignments exactly** and preview the deletion first.

## Service and logs

The replication service is container-wide even though Dispatcharr has multiple
uWSGI workers. If status says stopped after a save, check whether automatic
start was deliberately disabled and inspect `[Failovarr]` lines
in container logs.

uWSGI `Broken pipe` messages during browser logout, refresh or navigation are
client disconnects, not a plugin replication failure. Investigate plugin log
entries that explicitly carry the `[Failovarr]` prefix instead.
