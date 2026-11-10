# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ziguk: a generator's --check says fresh, stale or could-not-run, never
"stale" for a check that never ran.

The bug was two faults stacked. ``scripts/generate-schema`` put
``REPO_ROOT/src`` on ``sys.path``, the layout before ``packages/``, so it
imported hypergumbo_core only when an installed copy happened to be on the
path. Without the venv it died with ``ModuleNotFoundError``, exit 1, which is
also the generators' "stale" code, and the pre-commit hook printed "Schema is
out of date" over a current schema. These tests pin both halves. The
generators run with ``python -S`` (no site-packages, so no installed package)
to show they need none, and copies run outside the checkout to show that a
failed import exits 2 with the reason on stderr.
"""

# covers: scripts/generate-schema
# covers: scripts/generate-concept-axes
# covers: scripts/generate-concepts
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
GENERATORS = ("generate-schema", "generate-concept-axes", "generate-concepts")


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    # -S: no site-packages, so neither the venv's editable installs nor any
    # system copy of hypergumbo_core can be found by accident.
    return subprocess.run([sys.executable, "-S", str(script), *args],
                          capture_output=True, text=True)


@pytest.mark.parametrize("name", GENERATORS)
def test_the_live_check_passes_with_no_installed_package(name: str) -> None:
    done = _run(SCRIPTS / name, "--check")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("name", ["generate-schema", "generate-concept-axes"])
def test_a_failed_import_exits_2_and_says_why(name: str, tmp_path: Path) -> None:
    """Outside the checkout there is no packages/ to import from."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for helper in (name, "generator_check.py", "generate_schema_lib.py"):
        shutil.copy2(SCRIPTS / helper, scripts / helper)
    done = _run(scripts / name, "--check")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "could not run: ModuleNotFoundError" in done.stderr
    assert "source .venv/bin/activate" in done.stderr
    assert "out of date" not in done.stdout + done.stderr


def test_a_crash_after_import_exits_2(tmp_path: Path) -> None:
    """Reading a directory as the doc raises; that is not a stale doc."""
    done = _run(SCRIPTS / "generate-concepts", "--check", "--out", str(tmp_path))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "could not run: IsADirectoryError" in done.stderr


def test_a_stale_doc_still_exits_1(tmp_path: Path) -> None:
    """The control: the change must not turn a real finding into exit 2."""
    stale = tmp_path / "CONCEPTS.md"
    stale.write_text("not the generated table\n")
    done = _run(SCRIPTS / "generate-concepts", "--check", "--out", str(stale))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "out of date" in done.stderr
