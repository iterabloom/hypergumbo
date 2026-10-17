# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release workflow attaches every asset, or fails saying which are missing.

``v7.0.0`` (HTTP 400) and ``v9.1.0`` (HTTP 404) were each left as a public
GitHub Release missing a file, because one transient error from GitHub's
upload host aborted a single ``gh release create <tag> <assets...>`` after
the Release was already created, and nothing checked the result (WI-hasol).
The same ``v9.1.0`` run also showed the SBOM inventoried the build
environment (``twine``, ``cyclonedx-bom``; no hypergumbo package) because
nothing had installed the built wheels before ``cyclonedx-py environment``.

These pins hold the shape of the fix in ``.github/workflows/release.yml``:
the Release is created with no assets and only if absent, each asset is
uploaded on its own with ``--clobber`` inside a retry loop, the attached set
is verified by name, the SBOM is built from the wheels installed into a
scratch environment, and the SBOM step runs before the checksums so
``SHA256SUMS`` covers it.
"""

# covers: .github/workflows/release.yml, docs/sops/RELEASE_SOP.md
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _step(name: str) -> str:
    """The text of the named step, up to the next ``- name:``."""
    text = WORKFLOW.read_text()
    m = re.search(rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)", text, re.M | re.S)
    assert m, f"step {name!r} not found"
    return m.group(1)


def _step_index(name: str) -> int:
    text = WORKFLOW.read_text()
    i = text.find(f"      - name: {name}\n")
    assert i >= 0, name
    return i


def test_sbom_is_generated_before_checksums_so_sha256sums_covers_it():
    assert _step_index("Generate SBOM") < _step_index("Generate checksums")
    assert "sha256sum *" in _step("Generate checksums")


def test_sbom_describes_the_installed_wheels_not_the_build_environment():
    step = _step("Generate SBOM")
    assert re.search(r"pip\"? install .*--find-links dist/ dist/\*\.whl", step), "built wheels are not installed"
    assert re.search(r'cyclonedx-py environment "\$SBOM_ENV/bin/python"', step), "cyclonedx-py is not pointed at the scratch env"
    assert "hypergumbo-core" in step, "the SBOM is not checked for a hypergumbo component"
    assert "rm -f dist/sbom.json" in step, "a bad SBOM is not dropped"


def test_release_is_created_without_assets_and_only_when_absent():
    step = _step("Create GitHub Release")
    create = [ln for ln in step.splitlines()
              if "gh release create" in ln and not ln.strip().startswith("#")]
    assert len(create) == 1, create
    assert "ASSETS" not in create[0] and "dist/" not in create[0], "assets must not ride on `gh release create`"
    assert re.search(r'if gh release view "\$TAG" >/dev/null 2>&1; then', step), "creation is not idempotent"


def test_each_asset_is_uploaded_with_clobber_inside_a_retry_loop():
    step = _step("Create GitHub Release")
    assert re.search(r'for asset in "\$\{ASSETS\[@\]\}"; do\s*\n\s*for attempt in', step), "no per-asset retry loop"
    assert re.search(r'gh release upload "\$TAG" "\$asset" --clobber', step), "upload is not per-asset with --clobber"
    assert "sleep" in step


def test_attached_assets_are_verified_by_name_and_a_gap_fails_the_job():
    step = _step("Create GitHub Release")
    assert "--json assets --jq '.assets[].name'" in step
    assert "missing+=" in step and "exit 1" in step
    assert "::error::" in step


def test_sop_describes_the_same_procedure():
    sop = (REPO_ROOT / "docs" / "sops" / "RELEASE_SOP.md").read_text()
    assert "gh release upload --clobber" in sop
    assert "verifies by name" in sop
    assert "SHA256SUMS for every artifact, the SBOM included" in sop
