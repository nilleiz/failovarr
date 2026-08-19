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

**Client identity mismatch**

Client-relevant IPTV user settings differ. Align them manually; passwords and
API keys are intentionally not copied.

**ID has different natural key / record is locked**

Review the conflict. For an independently built Follower, Initialization may
be appropriate. Never remove Dispatcharr locks merely to force a replication.

## Service and logs

The replication service is container-wide even though Dispatcharr has multiple
uWSGI workers. If status says stopped after a save, check whether automatic
start was deliberately disabled and inspect `[Failovarr]` lines
in container logs.

uWSGI `Broken pipe` messages during browser logout, refresh or navigation are
client disconnects, not a plugin replication failure. Investigate plugin log
entries that explicitly carry the `[Failovarr]` prefix instead.
