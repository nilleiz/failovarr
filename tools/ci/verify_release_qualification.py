"""Reuse full PR qualification evidence only when a squash merge has the same tree."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Callable


REQUIRED_JOBS = {"repository contract", "package", "synthetic cluster", "synthetic storage", "qualification"}


def is_full_success(jobs: list[dict]) -> bool:
    outcomes = {job.get("name"): job.get("conclusion") for job in jobs}
    return all(outcomes.get(name) == "success" for name in REQUIRED_JOBS)


def select_qualified_run(
    commit: str,
    exact_runs: list[dict],
    associated_prs: list[dict],
    pr_runs: list[dict],
    trees: dict[str, str],
    jobs_for_run: Callable[[int], list[dict]],
) -> tuple[int, str] | None:
    for run in exact_runs:
        if run.get("conclusion") == "success" and is_full_success(jobs_for_run(run["databaseId"])):
            return run["databaseId"], "exact-main"

    merged = [pr for pr in associated_prs if pr.get("merged_at") and pr.get("merge_commit_sha") == commit]
    if len(merged) != 1:
        return None
    head = merged[0]["head"]["sha"]
    if trees.get(commit) != trees.get(head):
        return None
    for run in pr_runs:
        if run.get("conclusion") == "success" and is_full_success(jobs_for_run(run["databaseId"])):
            return run["databaseId"], f"merged-pr-{merged[0]['number']}"
    return None


def gh_json(*args: str):
    output = subprocess.check_output(["gh", *args], text=True)
    return json.loads(output)


def runs_for_commit(repo: str, commit: str) -> list[dict]:
    return gh_json("run", "list", "--repo", repo, "--workflow", "qualification", "--commit", commit, "--status", "completed", "--limit", "50", "--json", "conclusion,databaseId,event,headSha")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    def jobs_for_run(run_id: int) -> list[dict]:
        return gh_json("run", "view", str(run_id), "--repo", args.repo, "--json", "jobs")["jobs"]

    exact_runs = runs_for_commit(args.repo, args.commit)
    associated_prs = gh_json("api", f"repos/{args.repo}/commits/{args.commit}/pulls")
    pr_runs: list[dict] = []
    trees = {args.commit: gh_json("api", f"repos/{args.repo}/git/commits/{args.commit}")["tree"]["sha"]}
    merged = [pr for pr in associated_prs if pr.get("merged_at") and pr.get("merge_commit_sha") == args.commit]
    if len(merged) == 1:
        head = merged[0]["head"]["sha"]
        pr_runs = runs_for_commit(args.repo, head)
        trees[head] = gh_json("api", f"repos/{args.repo}/git/commits/{head}")["tree"]["sha"]

    result = select_qualified_run(args.commit, exact_runs, associated_prs, pr_runs, trees, jobs_for_run)
    if result is None:
        raise SystemExit("No successful full qualification exists for this release tree. Run qualification with full=true on the final PR head.")
    run_id, source = result
    print(f"FAILOVARR_CI_QUALIFICATION_RUN={run_id}")
    print(f"FAILOVARR_CI_QUALIFICATION_SOURCE={source}")


if __name__ == "__main__":
    main()
