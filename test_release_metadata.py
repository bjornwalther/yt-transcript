"""Guards against release metadata drifting apart (version fields, registry entry)."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_server_json_matches_the_package():
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    project = _pyproject()
    assert server["version"] == project["version"]
    (package,) = server["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == project["name"]
    assert package["version"] == project["version"]


def test_changelog_documents_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    assert versions[0] == _pyproject()["version"], "newest CHANGELOG entry must be the current version"


def test_smithery_starts_a_script_that_exists():
    smithery = (ROOT / "smithery.yaml").read_text(encoding="utf-8")
    scripts = _pyproject()["scripts"]
    (command,) = re.findall(r"args:\s*\['([^']+)'\]", smithery)
    assert command in scripts


def test_readme_install_command_is_a_real_script():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '"args": ["ytfetch-mcp"]' in readme
    assert "ytfetch-mcp" in _pyproject()["scripts"]


def test_server_reports_the_installed_package_version():
    from yt_transcript import package_version
    import mcp_server
    assert mcp_server.server.create_initialization_options().server_version == package_version()
