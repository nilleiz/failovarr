# Contributing to Failovarr

Failovarr is an AI-assisted vibe coding project under human direction. Please
keep contributions reviewable, document assumptions, and do not add real
deployment data or secrets.

Every pull request must complete the Test Impact section from the template:
affected feature IDs, behaviour change, required environments, coverage-matrix
decision, and test additions/removals or the reason none are needed.

The classifier selects lightweight checks for documentation-only changes and
the relevant synthetic suites for runtime, cluster and storage changes. A
release candidate runs one explicit complete qualification on its final PR
tree. A squash merge reuses that evidence only when the PR is unambiguous, the
Git tree is identical, and every required suite succeeded. Local
production-like lab testing is operator-controlled and is not part of public
CI.

Use Python standard-library unit tests where possible. Synthetic CI fixtures
must be invented data only. Do not make a feature "Implemented and verified"
until its CI and, where required, manual acceptance evidence is recorded in
the feature catalogue.

## Wiki maintenance

`docs/wiki/` is the canonical source for the public GitHub Wiki. After a
Wiki-source change has merged to `main`, a maintainer publishes it from a
clean, up-to-date checkout with:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/publish_wiki.ps1
```

This is deliberately maintainer-run: GitHub Actions cannot authenticate to a
separate Wiki Git repository with the repository `GITHUB_TOKEN`. The script
uses the maintainer's existing GitHub CLI login, stores no token, and leaves
unmanaged Wiki pages untouched.
