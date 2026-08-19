import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


classifier = load_module("ci_classifier", "tools/ci/classify_changes.py")
verifier = load_module("ci_verifier", "tools/ci/verify_release_qualification.py")


class ChangeClassifierTests(unittest.TestCase):
    def test_docs_only_skips_runtime_suites(self):
        self.assertEqual(classifier.classify(["docs/wiki/Home.md"], event="pull_request", force_full="false"), classifier.Selection(False, False, False, False))

    def test_release_workflow_and_unit_tests_need_package_only(self):
        self.assertEqual(classifier.classify([".github/workflows/release.yml"], event="pull_request", force_full="false"), classifier.Selection(True, False, False, False))
        self.assertEqual(classifier.classify(["tests/test_bundle.py"], event="pull_request", force_full="false"), classifier.Selection(True, False, False, False))

    def test_runtime_domains_select_only_relevant_integration_suites(self):
        self.assertEqual(classifier.classify(["failovarr/engine.py"], event="pull_request", force_full="false"), classifier.Selection(True, True, False, False))
        self.assertEqual(classifier.classify(["failovarr/remote_storage.py"], event="pull_request", force_full="false"), classifier.Selection(True, False, True, False))
        self.assertEqual(classifier.classify(["tools/ci/ci_helpers.ps1"], event="pull_request", force_full="false"), classifier.Selection(True, True, True, False))

    def test_test_framework_and_unknown_changes_fail_safe_to_full(self):
        self.assertEqual(classifier.classify([".github/workflows/test.yml"], event="pull_request", force_full="false"), classifier.Selection(True, True, True, True))
        self.assertEqual(classifier.classify(["unclassified-file"], event="pull_request", force_full="false"), classifier.Selection(True, True, True, True))

    def test_manual_full_overrides_path_selection(self):
        self.assertEqual(classifier.classify(["README.md"], event="workflow_dispatch", force_full="true"), classifier.Selection(True, True, True, True))


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [{"name": name, "conclusion": "success"} for name in verifier.REQUIRED_JOBS]
        self.runs = [{"databaseId": 42, "conclusion": "success"}]
        self.pr = {"number": 7, "merged_at": "now", "merge_commit_sha": "main", "head": {"sha": "head"}}

    def test_accepts_exact_main_full_run(self):
        result = verifier.select_qualified_run("main", self.runs, [], [], {"main": "tree"}, lambda _: self.jobs)
        self.assertEqual(result, (42, "exact-main"))

    def test_accepts_identical_merged_pr_tree(self):
        result = verifier.select_qualified_run("main", [], [self.pr], self.runs, {"main": "tree", "head": "tree"}, lambda _: self.jobs)
        self.assertEqual(result, (42, "merged-pr-7"))

    def test_rejects_different_tree_or_partial_run(self):
        self.assertIsNone(verifier.select_qualified_run("main", [], [self.pr], self.runs, {"main": "new", "head": "old"}, lambda _: self.jobs))
        partial = [job for job in self.jobs if job["name"] != "synthetic storage"]
        self.assertIsNone(verifier.select_qualified_run("main", [], [self.pr], self.runs, {"main": "tree", "head": "tree"}, lambda _: partial))


if __name__ == "__main__":
    unittest.main()
