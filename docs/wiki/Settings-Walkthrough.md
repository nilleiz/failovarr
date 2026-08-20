# Settings Walkthrough

The Assistant is the recommended setup surface. Dispatcharr's native Plugin
Settings mirror the same non-secret configuration for inspection and routine
changes. Secrets, storage passwords and SFTP private keys stay node-local and
are entered through the Assistant.

## Node role and identity

**Node role** is the first field. Choose Main only for the authoritative
writer; choose Follower for every importing node. Give every running node a
different **Node name**. **Cluster name** and **Shared cluster secret** must
match on every node in the same replication cluster.

**Redundancy mode** chooses the client-access model:

- **Cold Standby**: one Dispatcharr container is active at a time.
- **Online — Plugin-managed Linux VIP**: both nodes run; the active node owns a
  secondary IPv4 address.
- **Online — external HA reverse proxy**: the user supplies the proxy; the
  plugin provides readiness only.

## Transport and storage

Choose Shared Storage, Direct Pull or Hybrid according to the selected mode.
Shared Storage is mandatory for Cold Standby. Direct and Hybrid use the peer
URL and peer node name over port 9192.

The Assistant shows only the inputs needed by the selected backend. **Test
connection** writes, atomically publishes, reads and removes a temporary test
object. It does not create a production bundle.

`Local state path` is persistent node-local storage for sequences, hashes,
replay/apply state and service state. It belongs under `/data` and must not be
only on shared storage.

## Data imported by this Follower

Main always exports the full supported plugin scope. Each Follower decides what
it applies locally:

- **Complete IPTV setup** — complete supported graph and selected settings.
- **Profiles and Settings only** — profiles/settings without provider or
  channel lists.
- **M3U, EPG and Channels only** — provider, guide and channel graph without
  Core Settings or Output Profiles.
- **Custom selection** — select individual areas; required dependencies are
  added automatically.

The display order follows Dispatcharr: Channels, M3U & EPG Manager, Logo
Manager, then Settings. `Allow replicated deletions` is a Follower-local policy
and is off by default.

`Mirror Main stream assignments exactly` is also Follower-local and appears
when Channel Streams are in scope. It removes only Channel Stream assignments
that are absent from Main; it does not enable deletion of Channels, Streams or
any other replicated records. Failovarr always compares Channel Stream rows by
their Channel-and-Stream identity, so a mapping plugin may recreate those rows
with new local IDs without blocking import.

## Records kept local on this Follower

This section appears only for imported areas, ordered as M3U Accounts, EPG
Sources, Stream Profiles and Output Profiles. A selected record is retained in
full: it is neither updated nor deleted by a bundle, and its stable ID remains
local.

For each visible area, choose the policy for a **new record from Main**:

- create it disabled and keep it local;
- import it from Main;
- block the complete import until an operator decides.

Use this for host-specific FFmpeg hardware commands, provider credentials or
other intentional per-node differences. Do not use it to hide unexpected
configuration drift.

## Automation and actions

`Enable automatic replication after Dispatcharr starts` starts one
container-wide service despite Dispatcharr's multiple uWSGI workers. Cold
Standby imports the last verified bundle once when a Follower service starts;
continuous follower polling is disabled there. Online mode additionally offers
start import and periodic automatic import controls.

The status action shows node role, service state, last export/import and known
sequence information. Main offers **Save and export now**. Follower offers
Refresh latest bundle, Preview, Import and the separately confirmed
Initialization action.
