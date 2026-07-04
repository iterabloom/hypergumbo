# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/bakeoff-broad ``_pool_repo_path``.

Regression for the WI-favav parity gap: cohort SELECTION resolved a repo living
one level down in a collection subdir (``ALL_REPOS/<collection>/<repo>``) via
``pool_utils.resolve_repo_path``, but the RUN reconstructed the path with a bare
``os.path.join(pool_path, repo_name)`` — so it looked for the repo at the pool
top level, missed it, and ``get_dir_size`` raised ``FileNotFoundError`` (observed
on ``ALL_REPOS/whole_bunch_of_repos/webtunnel``). ``_pool_repo_path`` centralizes
the resolver so both paths agree.

The script is imported as a module (despite the hyphen) — no bakeoff run needed.
"""

from __future__ import annotations

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


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()


def test_nested_collection_repo_resolves(bbroad, tmp_path: Path) -> None:
    # A repo one level down in a collection subdir (the shape that broke the run).
    _make_repo(tmp_path / "whole_bunch_of_repos" / "webtunnel")
    resolved = bbroad._pool_repo_path(str(tmp_path), "webtunnel")
    assert Path(resolved) == tmp_path / "whole_bunch_of_repos" / "webtunnel"


def test_top_level_repo_resolves(bbroad, tmp_path: Path) -> None:
    _make_repo(tmp_path / "toprepo")
    resolved = bbroad._pool_repo_path(str(tmp_path), "toprepo")
    assert Path(resolved) == tmp_path / "toprepo"


def test_missing_repo_falls_back_to_naive_join(bbroad, tmp_path: Path) -> None:
    # A genuinely-absent repo returns the naive join (so the caller's existing
    # get_dir_size FileNotFoundError path fires, rather than passing None down).
    resolved = bbroad._pool_repo_path(str(tmp_path), "ghost")
    assert Path(resolved) == tmp_path / "ghost"
