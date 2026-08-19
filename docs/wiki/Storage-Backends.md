# Storage Backends

All backends use the same atomic bundle contract: write a temporary object,
flush it, atomically publish it, read it back and verify the signature before
an import. Configure and test the backend on both nodes.

| Backend | Typical use | Important requirement |
| --- | --- | --- |
| Filesystem/bind mount | Cold Standby NAS share | Mount on the host and expose a writable path under `/data`. |
| WebDAV | Existing HTTPS storage | Prefer HTTPS and configure a custom CA file when necessary. |
| S3-compatible | NAS object store or cloud | Provide endpoint, bucket, credentials, region and prefix. |
| SFTP | Small remote server | Fetch and independently verify the server host key before trusting it. |
| SMB 3 | Direct SMB share | Supply server/share credentials; encrypted SMB is used. |

## Filesystem and network mounts

The plugin receives a container path, not a host path. Mount NFS/SMB on each
host and bind-mount the same logical directory into each Dispatcharr container,
for example below `/data/redundancy`. The node-local state path must remain
separate from this shared directory.

## SFTP

Use `sftp://host:22` and a dedicated storage directory. First click **Fetch
host key**, compare the displayed fingerprint with an independent source, then
click **Trust fetched key**. The known-hosts file must be writable by the
Dispatcharr container user. Password or imported client-private-key
authentication may be used; private keys are never mirrored to native settings.

## Retention and recovery

Bundle retention defaults to three. A missing, unreachable, invalid or
foreign-cluster bundle is never applied. Correct the storage configuration and
use **Refresh latest bundle** before retrying Preview or Import.
