# Third-party notices

The release package contains `failovarr/vendor/remote_storage.zip`.
It holds the pinned, pure-Python dependencies listed in
`requirements-vendor.txt`. Their original package metadata and license files
remain inside the archive.

| Package | Version | License declared by package |
| --- | ---: | --- |
| boto3 | 1.43.70 | Apache-2.0 |
| botocore | 1.43.70 | Apache-2.0 |
| jmespath | 1.0.1 | MIT |
| s3transfer | 0.19.0 | Apache-2.0 |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD-3-Clause |
| urllib3 | 2.5.0 | MIT |
| six | 1.17.0 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| AsyncSSH | 2.24.0 | EPL-2.0 / GPL-2.0-or-later |
| smbprotocol | 1.17.0 | MIT |
| pyspnego | 0.12.1 | MIT |

`cryptography` is not bundled. Dispatcharr 0.29.0 already supplies a compatible
version in its application image.

The runtime verifies the archive's SHA-256 digest before extracting it below
the configured node-local state directory. It never downloads or installs
dependencies at runtime.
