# SPDX-License-Identifier: AGPL-3.0-or-later
"""``bakeoff-broad cohort --dry-run`` lists the cohort it would select.

The dry-run listing prints one line per repo from the fields bakeoff-broad's
``RepoInfo`` carries (name, size, languages). It must not read fields that only
bakeoff-deep's ``RepoInfo`` has (``file_count``, ``complexity_score``): the
listing then raises ``AttributeError`` instead of printing. A dry run also
saves nothing, so the session's cohort number is unchanged afterwards.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def bbroad():
    """Import scripts/bakeoff-broad as a module despite the hyphen."""
    script_path = str(Path(__file__).parent.parent / "scripts" / "bakeoff-broad")
    loader = importlib.machinery.SourceFileLoader("bakeoff_broad", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_broad", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def session(bbroad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh session over a one-repo pool; returns the session workdir."""
    pool = tmp_path / "pool"
    repo = pool / "tinyrepo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    monkeypatch.delenv("BAKEOFF_POOL", raising=False)
    monkeypatch.delenv("BAKEOFF_WORKDIR", raising=False)
    work = tmp_path / "work"
    rc = bbroad.cmd_init(argparse.Namespace(
        pool=str(pool), workdir=str(work), resume=False, force=False))
    assert rc == 0
    sessions = sorted(work.glob("broad-*"))
    assert len(sessions) == 1
    return sessions[0]


def test_dry_run_with_explicit_repos_lists_the_cohort(
    bbroad, session: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = bbroad.load_state(str(session)).cohort_number
    rc = bbroad.cmd_cohort(argparse.Namespace(
        workdir=str(session), dry_run=True, repos="tinyrepo", count=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "[DRY RUN]" in out
    assert "tinyrepo" in out
    assert "MB" in out
    assert bbroad.load_state(str(session)).cohort_number == before
