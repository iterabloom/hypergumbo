# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/generate-security-md.

Verifies determinism (byte-identical output on consecutive runs) and
that the splice preserves the vulnerability-reporting content below
the sentinel block.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-security-md"


def _load_script_module():
    loader = importlib.machinery.SourceFileLoader(
        "generate_security_md_under_test", str(SCRIPT_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_check_mode_passes_on_committed_state() -> None:
    """When SECURITY.md matches the YAML, --check exits 0."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--check"],
        capture_output=True, text=True, check=False,
    )
    # If this fails, the SECURITY.md committed in the repo is out of sync
    # with docs/hypergumbo.claims.yaml — regenerate via the script.
    assert result.returncode == 0, (
        f"SECURITY.md drifted from claims YAML.\nstderr: {result.stderr}"
    )


def test_first_time_insert_creates_sentinel_block(tmp_path: Path) -> None:
    """When SECURITY.md has no sentinel markers yet, the script
    inserts the generated block after the H1 title."""
    mod = _load_script_module()

    fresh = (
        "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
        "# Security Policy\n"
        "\n"
        "## Reporting a Vulnerability\n"
        "\n"
        "Email security@example.com.\n"
    )
    generated_block = mod._render_full_generated_block({
        "claims": [],
    })
    spliced = mod._splice_into_security_md(fresh, generated_block)
    assert mod.BEGIN_MARKER in spliced
    assert mod.END_MARKER in spliced
    # Vulnerability-reporting content preserved.
    assert "Email security@example.com." in spliced
    # The H1 title remains.
    assert "# Security Policy" in spliced


def test_splice_replaces_existing_sentinel_block(tmp_path: Path) -> None:
    """When SECURITY.md already has a sentinel block, the script
    replaces it without touching surrounding content."""
    mod = _load_script_module()
    existing = (
        "# Security Policy\n\n"
        f"{mod.BEGIN_MARKER}\nOLD CONTENT\n{mod.END_MARKER}\n\n"
        "## Reporting\n\nEmail us.\n"
    )
    new_block = mod._render_full_generated_block({
        "claims": [{
            "id": "demo-claim",
            "text": "demo claim text",
            "constraint": {
                "taint_flow": {
                    "source_taint": "demo_source",
                    "prohibited_sink_zone": "demo_zone",
                },
            },
        }],
    })
    spliced = mod._splice_into_security_md(existing, new_block)
    assert "OLD CONTENT" not in spliced
    assert "demo-claim" in spliced
    # Surrounding content preserved.
    assert "## Reporting" in spliced
    assert "Email us." in spliced


def test_renders_with_no_claims(tmp_path: Path) -> None:
    """Generator survives an empty claims list (defensive)."""
    mod = _load_script_module()
    block = mod._render_full_generated_block({"claims": []})
    assert "(no claims declared)" in block
    assert mod.BEGIN_MARKER in block
    assert mod.END_MARKER in block


def test_idempotent_when_no_changes(tmp_path: Path, monkeypatch) -> None:
    """Running the script twice produces byte-identical SECURITY.md
    (and second run reports `(no changes)`)."""
    # Sandbox: copy the claims + SECURITY.md into tmp_path so we don't
    # touch the real repo state during the test.
    mod = _load_script_module()
    sec_path = tmp_path / "SECURITY.md"
    sec_path.write_text(
        "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->\n"
        "# Security Policy\n\n## Reporting\n\nEmail us.\n",
        encoding="utf-8",
    )
    claims_path = tmp_path / "claims.yaml"
    claims_path.write_text(
        "claims:\n"
        "  - id: c1\n"
        "    text: example\n"
        "    constraint:\n"
        "      taint_flow:\n"
        "        source_taint: src\n"
        "        prohibited_sink_zone: zone\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "SECURITY_PATH", sec_path)
    monkeypatch.setattr(mod, "CLAIMS_PATH", claims_path)
    monkeypatch.setattr(sys, "argv", ["generate-security-md"])
    rc1 = mod.main()
    assert rc1 == 0
    first = sec_path.read_text(encoding="utf-8")
    rc2 = mod.main()
    assert rc2 == 0
    second = sec_path.read_text(encoding="utf-8")
    assert first == second


def test_check_mode_detects_drift(tmp_path: Path, monkeypatch) -> None:
    """When the YAML has changed but SECURITY.md hasn't, --check fails."""
    mod = _load_script_module()
    sec_path = tmp_path / "SECURITY.md"
    sec_path.write_text(
        "# Security Policy\n\nminimal\n", encoding="utf-8",
    )
    claims_path = tmp_path / "claims.yaml"
    claims_path.write_text(
        "claims:\n"
        "  - id: c1\n"
        "    text: example\n"
        "    constraint: {taint_flow: {source_taint: s, prohibited_sink_zone: z}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "SECURITY_PATH", sec_path)
    monkeypatch.setattr(mod, "CLAIMS_PATH", claims_path)
    monkeypatch.setattr(sys, "argv", ["generate-security-md", "--check"])
    rc = mod.main()
    assert rc == 1


def test_missing_claims_yaml_errors(tmp_path: Path, monkeypatch) -> None:
    mod = _load_script_module()
    monkeypatch.setattr(mod, "CLAIMS_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(sys, "argv", ["generate-security-md"])
    rc = mod.main()
    assert rc == 1


def test_missing_security_md_errors(tmp_path: Path, monkeypatch) -> None:
    mod = _load_script_module()
    claims_path = tmp_path / "c.yaml"
    claims_path.write_text("claims: []\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CLAIMS_PATH", claims_path)
    monkeypatch.setattr(mod, "SECURITY_PATH", tmp_path / "missing.md")
    monkeypatch.setattr(sys, "argv", ["generate-security-md"])
    rc = mod.main()
    assert rc == 1
