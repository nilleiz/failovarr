# Test impact and coverage matrix

| Change area | Required CI suites | Feature examples |
| --- | --- | --- |
| Documentation, community files, metadata | repository contract | all documentation IDs |
| Python/UI/planning logic | package | CORE-*, UX-* |
| Replication, lifecycle, ORM adapters, transport, handoff or VIP | package + synthetic cluster | CORE-*, TRANSPORT-001, HANDOFF-001 |
| Storage backends, vendor dependencies or storage fixtures | package + synthetic storage | STORAGE-001 |
| Release publishing workflow or unit-test change | package | release and unit-test contracts |
| Classifier, qualification workflow or shared CI helper | full qualification | all |
| Release candidate | one explicit full qualification on the final PR tree; reuse allowed only for an identical squash-merge tree | all release-facing features |

Removing a test requires an explanation in the pull request and an update to
this matrix showing the remaining coverage. A feature can move to
`implemented_verified` only with recorded CI and required operator-lab evidence.
