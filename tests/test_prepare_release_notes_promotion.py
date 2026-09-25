# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral test: ``scripts/prepare-release`` must promote the user-facing
release-notes file, and must refuse to release without one.

Background
----------
``prepare-release`` promoted ``CHANGELOG.md``'s ``[Unreleased]`` section on
every release because the script did it. Nothing did the same for
``docs/RELEASE-NOTES-<MAJOR>.X.md``, so that file drifted with no gate:

* 6.1.0 shipped with its notes stranded under ``## Unreleased``.
* 7.0.0 shipped with no notes written at all.
* 8.0.0 shipped with no ``RELEASE-NOTES-8.X.md`` in the tree,

while ``README.md`` went on pointing readers at the 6.x file — three major
versions stale. One fact (what shipped) kept two homes; only the home with a
script attached stayed current.

Why this test executes the block instead of grepping for it
------------------------------------------------------------
A test that asserts ``"RELEASE-NOTES" in script_text`` passes against a block
that is dead, misspelled, or unreachable — the same failure mode as the
sibling ``test_prepare_release_changelog_headers.py`` tests, which pin only
that certain strings appear. This test extracts the shipped block verbatim
and runs it, so a guard that cannot fire is distinguishable from one that
can. Each case carries a positive control: the promotion case asserts the
file's bytes actually changed, and the refusal cases assert a non-zero exit
*and* that the file was left alone.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE_RELEASE = REPO_ROOT / "scripts" / "prepare-release"

_BLOCK_START = "# User-facing release notes, one file per major version line."
_DIVIDER = re.compile(r"^# ─+$", re.MULTILINE)


def _shipped_block() -> str:
    """Return the release-notes promotion block, verbatim, from the script.

    Anchored on the block's own opening comment and terminated by the next
    box-drawing divider (the script's section separator). If the block is
    ever renamed or removed, extraction fails loudly here rather than
    silently testing an empty string.
    """
    text = PREPARE_RELEASE.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, (
        "Could not find the release-notes promotion block in "
        "scripts/prepare-release. If it was renamed, update the anchor; if "
        "it was deleted, the release-notes file has no promotion gate again."
    )
    tail = _DIVIDER.search(text, start)
    assert tail is not None, "No section divider terminates the block."
    block = text[start : tail.start()]
    assert "RELEASE_NOTES=" in block, "Extracted block is not the right one."
    return block


def _run_block(tmp_path: Path, version: str = "9.0.0") -> subprocess.CompletedProcess:
    """Execute the shipped block against ``tmp_path`` as the repo root."""
    harness = "\n".join(
        [
            "set -uo pipefail",
            "RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''",
            f"VERSION='{version}'",
            "TODAY='2026-09-01'",
            "DRY_RUN=false",
            _shipped_block(),
            "exit 0",
        ]
    )
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def notes_dir(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    return tmp_path / "docs"


def test_unreleased_section_is_promoted_to_the_version(
    tmp_path: Path, notes_dir: Path
) -> None:
    """The whole point: ``## Unreleased`` becomes ``## <version> — <date>``."""
    notes = notes_dir / "RELEASE-NOTES-9.X.md"
    before = "# Release notes\n\n## Unreleased\n\n- a thing shipped\n"
    notes.write_text(before, encoding="utf-8")

    result = _run_block(tmp_path)

    assert result.returncode == 0, result.stderr
    after = notes.read_text(encoding="utf-8")
    # Positive control: the block actually rewrote the file.
    assert after != before, (
        "The block exited 0 without changing the file — a guard that cannot "
        "be shown to fire is indistinguishable from one that matches nothing."
    )
    assert "## 9.0.0 — 2026-09-01" in after
    assert "## Unreleased" not in after
    assert "- a thing shipped" in after, "Promotion must not eat the content."


def test_missing_notes_file_stops_the_release(tmp_path: Path, notes_dir: Path) -> None:
    """A new major line with no notes file is a hard stop, not a warning.

    This is the case that actually happened at 8.0.0: the file did not exist,
    nothing complained, and the release shipped.
    """
    result = _run_block(tmp_path)

    assert result.returncode == 1, (
        "prepare-release continued past a missing release-notes file. That is "
        "exactly how 8.0.0 shipped with README pointing at the 6.x notes.\n"
        f"stdout={result.stdout!r}"
    )
    assert "RELEASE-NOTES-9.X.md" in result.stdout
    assert not (notes_dir / "RELEASE-NOTES-9.X.md").exists()


def test_notes_file_without_an_unreleased_section_stops_the_release(
    tmp_path: Path, notes_dir: Path
) -> None:
    """A file that exists but has nothing written for this version is a stop.

    This is the 7.0.0 case: the notes file was present, but the section for
    the version being cut had never been written.
    """
    notes = notes_dir / "RELEASE-NOTES-9.X.md"
    before = "# Release notes\n\n## 8.9.0 — 2026-08-01\n\n- old news\n"
    notes.write_text(before, encoding="utf-8")

    result = _run_block(tmp_path)

    assert result.returncode == 1, (
        "prepare-release accepted a notes file with no section for the "
        f"version being released.\nstdout={result.stdout!r}"
    )
    assert notes.read_text(encoding="utf-8") == before, "File must be untouched."


def test_already_promoted_notes_file_is_accepted_unchanged(
    tmp_path: Path, notes_dir: Path
) -> None:
    """Re-running against an already-written section is a no-op, not a failure.

    ``prepare-release`` is not re-runnable mid-abort in general, but this
    particular gate must not be the thing that blocks a retry.
    """
    notes = notes_dir / "RELEASE-NOTES-9.X.md"
    before = "# Release notes\n\n## 9.0.0 — 2026-09-01\n\n- written already\n"
    notes.write_text(before, encoding="utf-8")

    result = _run_block(tmp_path)

    assert result.returncode == 0, result.stdout
    assert notes.read_text(encoding="utf-8") == before


def test_dry_run_reports_without_writing(tmp_path: Path, notes_dir: Path) -> None:
    """``--dry-run`` must describe the promotion without performing it."""
    notes = notes_dir / "RELEASE-NOTES-9.X.md"
    before = "# Release notes\n\n## Unreleased\n\n- a thing shipped\n"
    notes.write_text(before, encoding="utf-8")

    harness = "\n".join(
        [
            "set -uo pipefail",
            "RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''",
            "VERSION='9.0.0'",
            "TODAY='2026-09-01'",
            "DRY_RUN=true",
            _shipped_block(),
            "exit 0",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", harness], cwd=tmp_path, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert notes.read_text(encoding="utf-8") == before, "dry-run wrote to the file."
    assert "Would promote" in result.stdout


def test_major_line_is_derived_from_the_version(tmp_path: Path, notes_dir: Path) -> None:
    """The file it looks for tracks the major version, not a hardcoded name.

    Without this, the gate would keep pointing at whichever line was current
    when it was written — the drift it exists to prevent.
    """
    (notes_dir / "RELEASE-NOTES-9.X.md").write_text(
        "## Unreleased\n", encoding="utf-8"
    )
    result = _run_block(tmp_path, version="12.3.4")

    assert result.returncode == 1
    assert "RELEASE-NOTES-12.X.md" in result.stdout, (
        "The gate did not derive the major-version line from VERSION."
    )
