"""The manifest is load-bearing for both Home Assistant and HACS."""

import json
import re
from pathlib import Path

INTEGRATION_DIR = Path("custom_components/hermes_conversation")
HACS_REQUIRED_KEYS = (
    "domain",
    "documentation",
    "issue_tracker",
    "codeowners",
    "name",
    "version",
)


def _manifest() -> dict:
    return json.loads((INTEGRATION_DIR / "manifest.json").read_text())


def test_domain_matches_directory_name():
    """hassfest rejects a domain that differs from its directory."""
    assert _manifest()["domain"] == INTEGRATION_DIR.name


def test_hacs_required_keys_present():
    manifest = _manifest()
    assert not [key for key in HACS_REQUIRED_KEYS if key not in manifest]


def test_manifest_key_order():
    """hassfest requires domain, name, then alphabetical order."""
    keys = list(_manifest())
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_version_matches_pyproject():
    """HACS reads the manifest version; releases must not drift from it."""
    pyproject = Path("pyproject.toml").read_text()
    assert f'version = "{_manifest()["version"]}"' in pyproject


def test_translations_match_strings():
    """strings.json is the source; en.json must mirror it."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    english = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert strings == english


def _walk(node, path=""):
    """Yield every (path, string) leaf in a translations tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, str):
        yield path, node


def test_translations_contain_no_markup():
    """hassfest rejects strings that look like HTML, e.g. a bare <profile>."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    offenders = [
        path for path, text in _walk(strings) if re.search(r"<[^>\s][^>]*>", text)
    ]
    assert not offenders
