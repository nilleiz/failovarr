# 0.7.0 public product extraction gate

| ID | Requirement | Implementation | Acceptance evidence | Status |
| --- | --- | --- | --- | --- |
| P700-01 | Public product repository contains no private history, production data or host details. | Clean repository root with a public allowlist; private lab remains separate. | Boundary scan and PR review. | implemented; pending CI/review |
| P700-02 | Product is named Failovarr and is visibly WIP/vibe-coded. | Rename package, manifest, logs, assets, docs and release naming; add public notices. | Manifest and documentation contract. | implemented; pending CI |
| P700-03 | Existing 0.6.17 installations can migrate safely. | Accept legacy profile format, read old configuration location only when new one is absent, retain old state path, block concurrent plugin identities. | Unit/contract tests and operator migration acceptance. | implemented; CI and operator migration pending |
| P700-04 | Public repo supports future Dispatcharr registry submission. | MIT, manifest URLs, logo, community files and registry-readiness checklist. | Static contract and public review. | implemented; CI pending |
| P700-05 | Public docs include feature status, Wiki sources, Getting Started and security/contribution guidance. | Add feature catalogue, public Wiki sources and community files. | Repository contract. | implemented; CI pending |
| P700-06 | CI selects relevant suites and requires documented test impact; release candidates always fully qualify. | Change classifier, PR template, aggregate check and exact-commit release gate. | Workflow/contract tests. | implemented; CI pending |
| P700-07 | The existing workspace becomes a private human/AI laboratory with the public product as submodule. | Rename private remote; replace duplicate product tree with `product/failovarr`; retarget lab scripts/docs. | Operator-approved migration review after public PR. | deferred until public qualification and operator migration acceptance |
