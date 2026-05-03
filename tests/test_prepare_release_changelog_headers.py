# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression test: ``scripts/prepare-release`` must refresh BOTH CHANGELOG.md
header lines (tool version + schema version), not just the tool one.

Background
----------
CHANGELOG.md opens with two parallel lines:

    - Released **tool** is at: vX.Y.Z
    - Released **schema** is at: vA.B.C

``prepare-release`` originally rewrote only the first line. The second
drifted silently across releases — by the time v4.0.0 shipped, the schema
header still read ``v0.2.2`` while ``schema.py`` was at ``0.4.0`` (skipping
v3.0.0's bump to 0.2.4 *and* this cycle's bump to 0.4.0).

Fix: ``prepare-release`` now reads ``SCHEMA_VERSION`` from
``packages/hypergumbo-core/src/hypergumbo_core/schema.py`` at release time
and rewrites the schema header line in the same sed pass that handles the
tool header line. This test asserts the structural invariant.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE_RELEASE = REPO_ROOT / "scripts" / "prepare-release"


def test_prepare_release_refreshes_tool_header_line() -> None:
    """``prepare-release`` references the tool-version header line."""
    text = PREPARE_RELEASE.read_text()
    assert "Released **tool** is at" in text, (
        "scripts/prepare-release no longer references the tool-version "
        "header line — it will drift silently across releases."
    )


def test_prepare_release_refreshes_schema_header_line() -> None:
    """``prepare-release`` must refresh the schema-version header line.

    The header is rewritten from ``SCHEMA_VERSION`` in
    ``packages/hypergumbo-core/src/hypergumbo_core/schema.py``. This test
    asserts only that the script references both halves (the header line
    name and the source file); it does not pin the exact mechanism (sed /
    python / etc.) so future refactors are free.
    """
    text = PREPARE_RELEASE.read_text()
    assert "Released **schema** is at" in text, (
        "scripts/prepare-release does not refresh the schema-version "
        "header line — it will drift silently across releases (the bug "
        "that left v4.0.0 shipping with 'Released schema is at: v0.2.2' "
        "while the source was at 0.4.0)."
    )
    assert "schema.py" in text or "SCHEMA_VERSION" in text, (
        "scripts/prepare-release does not source SCHEMA_VERSION from "
        "schema.py — without that read, the schema header refresh has "
        "no source of truth."
    )
