# Getting Started

This guide configures two Dispatcharr nodes safely. Perform the first run in a
lab or maintenance window.

## Prerequisites

- Dispatcharr v0.29.0 or a compatible version on Main and Follower.
- An independent backup of each node.
- A shared storage backend for Cold Standby; Direct or Hybrid additionally
  needs reachability between node management networks on port 9192.
- A stable client endpoint: one reused IP for Cold Standby, a plugin-managed
  Linux VIP, or a user-provided HA reverse proxy for online operation.

Install the release ZIP in **Dispatcharr → Settings → Plugins**, enable the
plugin and expose container port **9192** where needed. This one port serves
the Assistant, authenticated Direct Pull and readiness; there is no second
setup port.

## Configure Main

1. Open **Plugins → Failovarr → Actions → Open setup assistant**.
   The generated URL contains a private, rotating access token.
2. Choose **Main — authoritative source**, set a unique node name and a shared
   cluster name, then choose the redundancy mode.
3. Enter a shared secret and configure transport/storage. Test the storage
   connection before saving.
4. Save the configuration. Enable automatic service start only when the node
   is ready to replicate.
5. In **Copy shared configuration**, download the Main configuration profile.
   Include storage passwords only if the profile file can be protected.
6. Use **Save and export now** to create the first signed bundle.

## Configure the Follower

1. Open its Setup Assistant. Choose **Follower**, enter its unique node name
   and select the same redundancy mode.
2. Under **Import configuration**, select the downloaded Main profile and
   import it. The import transfers shared cluster/transport/storage values but
   keeps node name, role, local automation, import scope and local protection.
3. Test the storage connection. For SFTP, fetch the host key, compare its
   fingerprint independently, then trust it. Import a private key only when
   password authentication is not used.
4. Select the local import preset and any protected local records. Save.
5. Refresh the latest bundle. A verified bundle can be previewed; a current
   bundle is already applied and does not need importing again.

## First import

1. On Main, export a fresh bundle after all selected data is ready.
2. On Follower, click **Preview import** and inspect creates, updates,
   deletions and conflicts.
3. Resolve unexpected conflicts. Keep replicated deletions off for the first
   synchronization.
4. Click **Import latest bundle**. Repeating the action after a successful
   import shows **Already up to date** rather than an error.
5. Confirm a client M3U/EPG URL and a stream. Check that protected Output
   Profiles retain their local hardware command.

If a separately created Follower has many identity conflicts, continue with
[First Sync and Initialization](First-Sync-and-Initialization.md).
