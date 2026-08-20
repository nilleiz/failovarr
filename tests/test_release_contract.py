import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIKI_PAGES = {
    "Home.md", "Features.md", "Getting-Started.md", "Settings-Walkthrough.md", "Deployment-Modes.md",
    "Storage-Backends.md", "First-Sync-and-Initialization.md",
    "Operations-and-Planned-Handoff.md", "Troubleshooting.md",
    "Security-and-Limitations.md", "_Sidebar.md",
}


class ReleaseContractTests(unittest.TestCase):
    def test_manifest_points_to_versioned_release_asset_and_repository_docs(self):
        manifest = json.loads((ROOT / "failovarr" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "Failovarr")
        self.assertEqual(manifest["version"], "0.7.1")
        self.assertEqual(manifest["source_type"], "external")
        self.assertIn("releases/download/v{version}/failovarr-{version}.zip", manifest["source_url"])
        self.assertEqual(
            manifest["help_url"],
            "https://github.com/nilleiz/failovarr/wiki",
        )

    def test_release_workflow_builds_and_verifies_both_assets_before_publish(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for contract in (
            "workflow_dispatch:", "github.ref == 'refs/heads/main'", "contents: write",
            "tools/build_release.ps1", "RELEASE_ID=", "upload_url",
            "Invoke-RestMethod", "-InFile", "Get-FileHash", "Invoke-WebRequest",
            ".upload-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT", ".zip.sha256",
            "tools/ci/verify_release_qualification.py", "Upload and verify release assets",
            "Publish verified release",
        ):
            self.assertIn(contract, workflow)
        self.assertIn("git/matching-refs/tags/$tag", workflow)
        self.assertIn('"refs/tags/$tag"', workflow)
        self.assertIn("git/ref/tags/$tag", workflow)
        self.assertIn('$release.tag_name -notlike "untagged-*"', workflow)
        self.assertIn('tag_name = $env:TAG', workflow)
        self.assertIn('target_commitish = $env:GITHUB_SHA', workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertNotIn("--hostname uploads.github.com", workflow)
        self.assertNotIn("releases/tags/$env:TAG", workflow)
        self.assertLess(workflow.index("Invoke-RestMethod"), workflow.index("Remove-ReleaseAsset $existing[0].id"))
        self.assertIn("group: failovarr-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_release_workflow_promotes_verified_prereleases_without_rebuilding(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for contract in (
            "RELEASE_OPERATION=promote", "Verify published pre-release assets for promotion",
            "Promotion source must contain exactly the ZIP and checksum assets",
            "Published ZIP checksum does not match the checksum asset",
            "Could not promote pre-release $env:TAG", "prerelease = $false",
        ):
            self.assertIn(contract, workflow)
        self.assertIn("if: env.RELEASE_OPERATION == 'publish'", workflow)
        self.assertIn("if: env.RELEASE_OPERATION == 'promote'", workflow)
        self.assertLess(
            workflow.index("Verify published pre-release assets for promotion"),
            workflow.index("Could not promote pre-release $env:TAG"),
        )

    def test_workflows_use_node24_actions_and_quote_markdown_summaries_safely(self):
        checkout_pin = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        setup_python_pin = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
        old_checkout_pin = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
        old_python_pin = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / ".github" / "workflows").glob("*.yml")
        }
        for content in workflows.values():
            self.assertNotIn(old_checkout_pin, content)
            self.assertNotIn(old_python_pin, content)
        for name in ("release.yml", "test.yml"):
            self.assertIn(checkout_pin, workflows[name])
        self.assertEqual(workflows["release.yml"].count(checkout_pin), 1)
        self.assertEqual(workflows["test.yml"].count(checkout_pin), 5)
        self.assertIn(setup_python_pin, workflows["test.yml"])
        self.assertEqual(workflows["test.yml"].count(setup_python_pin), 2)
        self.assertIn("tools/ci/classify_changes.py", workflows["test.yml"])
        self.assertIn("classify test impact", workflows["test.yml"])
        self.assertNotIn("ready_for_review", workflows["test.yml"])

    def test_canonical_english_wiki_pages_and_community_readme_exist(self):
        wiki = ROOT / "docs" / "wiki"
        self.assertEqual({path.name for path in wiki.glob("*.md")}, WIKI_PAGES)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Development Preview", readme)
        self.assertIn("Getting Started", readme)
        self.assertIn("docs/wiki/Getting-Started.md", readme)
        self.assertIn("Work in Progress", readme)
        self.assertIn("AI-assisted vibe coding", readme)

    def test_wiki_publisher_uses_local_gh_auth_without_stored_secrets(self):
        publisher = (ROOT / "tools" / "publish_wiki.ps1").read_text(encoding="utf-8")
        self.assertIn("gh auth status --hostname github.com", publisher)
        self.assertIn("gh auth token --hostname github.com", publisher)
        self.assertIn("https://github.com/${Repository}.wiki.git", publisher)
        self.assertIn("$managedPages", publisher)
        self.assertIn("git diff --quiet", publisher)
        self.assertIn("GIT_CONFIG_VALUE_0", publisher)
        self.assertIn("$workingTree", publisher)
        self.assertIn("$null -ne $workingTree", publisher)
        self.assertNotIn("WIKI_TOKEN", publisher)
        self.assertNotIn("secrets.", publisher)
        self.assertFalse((ROOT / ".github" / "workflows" / "sync-wiki.yml").exists())
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("publish_wiki.ps1", contributing)


if __name__ == "__main__":
    unittest.main()
