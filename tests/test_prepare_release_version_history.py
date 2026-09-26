# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral test: ``scripts/prepare-release`` writes a true schema line and
maintains CHANGELOG.md's Version History table and link definitions.

Background
----------
Two defects shipped in the 8.1.0 CHANGELOG:

* ``- Released **schema** is at: vdid not say why``. The script read
  ``SCHEMA_VERSION`` with ``sed 's/.*"\\(.*\\)".*/\\1/'``, which keeps the LAST
  quoted string on the line, and schema.py's ``SCHEMA_VERSION`` line carries a
  long trailing comment full of quoted phrases.
* The Version History table stopped at 2.1.0 for eighteen releases, and its
  compare links still pointed at Codeberg. Nothing maintained either one.

Like ``test_prepare_release_notes_promotion.py``, these tests extract the
shipped blocks verbatim and run them, so a guard that cannot fire is told
apart from one that can. Each promotion case checks that the file actually
changed; each refusal case checks the exit code and that the file was left
alone.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE_RELEASE = REPO_ROOT / "scripts" / "prepare-release"
SCHEMA_PY = REPO_ROOT / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core" / "schema.py"

_HISTORY_START = "# Version History table and link definitions at the bottom of CHANGELOG.md."
_HISTORY_END = "# Tracker CHANGELOG (only when --tracker is given)"


def _script() -> str:
    return PREPARE_RELEASE.read_text(encoding="utf-8")


def _history_block() -> str:
    text = _script()
    start = text.find(_HISTORY_START)
    end = text.find(_HISTORY_END, start)
    assert start != -1 and end != -1, "Version History block not found in prepare-release."
    block = text[start:end]
    assert "Version History" in block and "[Unreleased]" in block
    return block


def _schema_block() -> str:
    """The SCHEMA_VERSION read plus its guard, verbatim."""
    lines = _script().splitlines()
    start = next(i for i, line in enumerate(lines) if "SCHEMA_VERSION=$(grep" in line)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start : end + 1])


def _run(tmp_path: Path, body: str, **env: str) -> subprocess.CompletedProcess:
    assigns = [f"{k}='{v}'" for k, v in env.items()]
    harness = "\n".join(
        ["set -uo pipefail", "RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''", "DRY_RUN=false"]
        + assigns + [body, "exit 0"]
    )
    return subprocess.run(["bash", "-c", harness], cwd=tmp_path, capture_output=True, text=True)


# --- SCHEMA_VERSION --------------------------------------------------------


def _read_schema(tmp_path: Path, schema_py: Path) -> subprocess.CompletedProcess:
    return _run(tmp_path, _schema_block() + '\necho "SCHEMA=$SCHEMA_VERSION"', SCHEMA_PY=str(schema_py))


def test_schema_read_matches_the_real_constant(tmp_path: Path) -> None:
    from hypergumbo_core.schema import SCHEMA_VERSION

    result = _read_schema(tmp_path, SCHEMA_PY)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"SCHEMA={SCHEMA_VERSION}" in result.stdout


def test_schema_read_takes_the_first_quoted_string(tmp_path: Path) -> None:
    """The 8.1.0 shape: quoted phrases in the trailing comment."""
    fake = tmp_path / "schema.py"
    fake.write_text('SCHEMA_VERSION = "1.2.3"  # a pass that "did not say why"\n')
    result = _read_schema(tmp_path, fake)
    assert result.returncode == 0, result.stdout
    assert "SCHEMA=1.2.3" in result.stdout


def test_a_schema_value_that_is_not_a_version_stops_the_release(tmp_path: Path) -> None:
    fake = tmp_path / "schema.py"
    fake.write_text("SCHEMA_VERSION = get_version()\n")
    result = _read_schema(tmp_path, fake)
    assert result.returncode == 1, result.stdout
    assert "SCHEMA=" not in result.stdout


# --- Version History ---------------------------------------------------------

_BASE = "https://github.com/example/proj"


def _changelog(first_row: str) -> str:
    return (
        "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n---\n\n"
        "## Version History\n\n"
        "| Version | Date       | Highlights |\n"
        "| ------- | ---------- | ---------- |\n"
        f"{first_row}"
        "| 1.0.0   | 2026-01-01 | First |\n\n"
        f"[Unreleased]: {_BASE}/compare/v1.0.0...HEAD\n"
        f"[1.0.0]: {_BASE}/releases/tag/v1.0.0\n"
    )


def _run_history(tmp_path: Path) -> subprocess.CompletedProcess:
    return _run(tmp_path, _history_block(), VERSION="1.1.0", TODAY="2026-09-01", PREV_TAG="v1.0.0")


def test_unreleased_row_is_promoted_and_links_are_written(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    before = _changelog("| Unreleased | —          | New things |\n")
    changelog.write_text(before)

    result = _run_history(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    after = changelog.read_text()
    assert after != before
    assert re.search(r"^\| 1\.1\.0 \| 2026-09-01 \| New things \|$", after, re.M), after
    assert "| Unreleased" not in after
    assert f"[Unreleased]: {_BASE}/compare/v1.1.0...HEAD\n" in after
    assert f"[1.1.0]: {_BASE}/compare/v1.0.0...v1.1.0\n" in after
    assert f"[1.0.0]: {_BASE}/releases/tag/v1.0.0" in after, "older links must survive"


def test_no_row_for_the_version_stops_the_release(tmp_path: Path) -> None:
    """The 2.2.0 → 8.1.0 case: eighteen releases, no row, no complaint."""
    changelog = tmp_path / "CHANGELOG.md"
    before = _changelog("")
    changelog.write_text(before)

    result = _run_history(tmp_path)

    assert result.returncode == 1, result.stdout
    assert "no row for 1.1.0" in result.stdout
    assert changelog.read_text() == before


def test_an_existing_row_and_link_are_left_alone(tmp_path: Path) -> None:
    """Re-running after a partial release must not duplicate anything."""
    changelog = tmp_path / "CHANGELOG.md"
    before = _changelog("| 1.1.0   | 2026-09-01 | New things |\n").replace(
        f"[1.0.0]: {_BASE}", f"[1.1.0]: {_BASE}/compare/v1.0.0...v1.1.0\n[1.0.0]: {_BASE}"
    )
    changelog.write_text(before)

    result = _run_history(tmp_path)

    assert result.returncode == 0, result.stdout
    assert changelog.read_text() == before


@pytest.mark.parametrize("line", ["- Released **schema** is at: v", "- Released **tool** is at: v"])
def test_the_live_changelog_states_real_versions(line: str) -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(line)}(\S+)$", text, re.M)
    assert m and re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)), m and m.group(0)


def test_the_live_version_history_covers_every_released_heading() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    table = text[text.index("## Version History"):]
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
    missing = [v for v in headings if not re.search(rf"^\| {re.escape(v)}\s*\|", table, re.M)]
    assert not missing, f"Version History has no row for {missing}"
