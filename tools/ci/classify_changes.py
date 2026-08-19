"""Select only the Failovarr CI suites affected by a change set."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Selection:
    package: bool
    cluster: bool
    storage: bool
    full: bool


DOC_PREFIXES = ("docs/", ".github/ISSUE_TEMPLATE/")
DOC_FILES = {"README.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE", "AGENTS.md", ".github/PULL_REQUEST_TEMPLATE.md"}
TEST_INFRA = {".github/workflows/test.yml", "tools/ci/classify_changes.py", "tools/ci/verify_release_qualification.py"}
RELEASE_PACKAGE = {".github/workflows/release.yml", "tools/build_release.ps1", "requirements-vendor.txt"}
CI_HELPERS = {"tools/ci/ci_helpers.ps1"}
CLUSTER_RUNNER = {"tools/ci/run_cluster.ps1"}
STORAGE_RUNNER = {"tools/ci/run_storage.ps1"}
CLUSTER_TOKENS = ("engine", "autostart", "domains", "transport", "vip", "planner", "client_identity", "integration_probe")
STORAGE_TOKENS = ("storage", "vendor", "remote_storage")


def is_docs_path(path: str) -> bool:
    return path in DOC_FILES or path.startswith(DOC_PREFIXES) or path == ".github/workflows/sync-wiki.yml"


def classify(paths: list[str], *, event: str, force_full: str) -> Selection:
    manual_full = event == "workflow_dispatch" and force_full.lower() == "true"
    normalized = [str(PurePosixPath(path)) for path in paths if path]
    if manual_full:
        return Selection(True, True, True, True)
    if not normalized:
        return Selection(False, False, False, False)

    non_docs = [path for path in normalized if not is_docs_path(path)]
    if not non_docs:
        return Selection(False, False, False, False)

    full = any(path in TEST_INFRA or (path.startswith(".github/workflows/") and path != ".github/workflows/release.yml") for path in non_docs)
    if full:
        return Selection(True, True, True, True)

    runtime = any(path.startswith("failovarr/") for path in non_docs)
    tests = any(path.startswith("tests/") for path in non_docs)
    package = runtime or tests or any(path in RELEASE_PACKAGE for path in non_docs)
    cluster = any(path in CI_HELPERS or path in CLUSTER_RUNNER or any(token in path.lower() for token in CLUSTER_TOKENS) for path in non_docs)
    storage = any(path in CI_HELPERS or path in STORAGE_RUNNER or any(token in path.lower() for token in STORAGE_TOKENS) for path in non_docs)

    known = runtime or tests or any(path in RELEASE_PACKAGE | CI_HELPERS | CLUSTER_RUNNER | STORAGE_RUNNER for path in non_docs)
    if not known:
        return Selection(True, True, True, True)
    return Selection(package or cluster or storage, cluster, storage, False)


def changed_paths(base: str, head: str) -> list[str]:
    if not base:
        raise ValueError("A base commit is required for pull-request classification")
    output = subprocess.check_output(["git", "diff", "--name-only", f"{base}...{head}"], text=True)
    return output.splitlines()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--force-full", default="false")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args()

    paths = ["manual full qualification"] if args.event == "workflow_dispatch" and args.force_full.lower() == "true" else changed_paths(args.base, args.head)
    selection = classify(paths, event=args.event, force_full=args.force_full)
    with open(args.github_output, "a", encoding="utf-8") as output:
        for key, value in asdict(selection).items():
            output.write(f"{key}={str(value).lower()}\n")
    print("FAILOVARR_CI_CHANGED=" + ",".join(paths))
    print("FAILOVARR_CI_SELECTION=" + ",".join(f"{key}:{value}" for key, value in asdict(selection).items()))


if __name__ == "__main__":
    main()
