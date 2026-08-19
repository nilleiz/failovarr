## Summary

Describe the user-visible change and why it is needed.

## Test impact (required)

- Affected feature IDs: <!-- e.g. CORE-001, STORAGE-001 -->
- Behaviour changed:
- Required test environments:
- Coverage matrix reviewed: <!-- unchanged / updated, with reason -->
- Tests added, removed, or intentionally unchanged: <!-- explain -->

## Release qualification

- [ ] This is the final release-candidate commit. After the final push, trigger one `qualification` workflow with `full=true` on this PR branch before merging.

## Safety

- [ ] No production data, credentials, hostnames, IP addresses or private snapshots are included.
- [ ] This change does not claim Active/Active, automatic two-node failover or fencing.
