"""Static public-repository contract checks run by the package suite."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "failovarr" / "plugin.json"


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == "Failovarr"
    assert manifest["version"] == "0.7.0"
    assert manifest["repo_url"] == "https://github.com/nilleiz/failovarr"
    assert "failovarr-{version}.zip" in manifest["source_url"]
    assert manifest["help_url"] == "https://github.com/nilleiz/failovarr/wiki"
    assert (ROOT / "failovarr" / "logo.png").is_file()

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in ("Work in Progress", "Development Preview", "AI-assisted vibe coding", "Getting Started"):
        assert required in readme, required

    required_docs = (
        "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "docs/FEATURES.md",
        "docs/testing/TEST-MATRIX.md", "docs/REGISTRY-READINESS.md",
    )
    for relative in required_docs:
        assert (ROOT / relative).is_file(), relative

    features = tomllib.loads((ROOT / "docs" / "features.toml").read_text(encoding="utf-8"))["feature"]
    statuses = {item["status"] for item in features}
    assert statuses == {"implemented_verified", "implemented_incomplete", "planned"}
    ids = [item["id"] for item in features]
    assert len(ids) == len(set(ids))

    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    for required in ("Affected feature IDs", "Coverage matrix reviewed", "Tests added, removed"):
        assert required in template, required

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and path.suffix in {".md", ".yml", ".py", ".json", ".ps1"}
    )
    # Keep the complete private host identifiers out of this file itself while
    # still detecting them if they ever appear in a public product file.
    for forbidden in ("ubuntu" + "docker.fritz.box", "smarthome" + "hub.fritz.box"):
        assert forbidden not in public_text, forbidden
    assert not re.search(r"192\\.168\\.0\\.(?:180|181|182|183|184|185)\\b", public_text)


if __name__ == "__main__":
    main()
