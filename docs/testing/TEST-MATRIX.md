# Test impact and coverage matrix

| Change area | Required CI suites | Feature examples |
| --- | --- | --- |
| Documentation, community files, metadata | repository contract | all documentation IDs |
| Python/UI/planning logic | package | CORE-*, UX-* |
| Replication, lifecycle, ORM adapters, transport, handoff or VIP | package + synthetic cluster | CORE-*, TRANSPORT-001, HANDOFF-001 |
| Storage backends, vendor dependencies or storage fixtures | package + synthetic storage | STORAGE-001 |
| Classifier, workflow or test framework | full qualification | all |
| Release candidate | full qualification on the exact commit | all release-facing features |

Removing a test requires an explanation in the pull request and an update to
this matrix showing the remaining coverage. A feature can move to
`implemented_verified` only with recorded CI and required operator-lab evidence.
