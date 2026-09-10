# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nested-pool repo resolution for ``scripts/bakeoff-deep`` (WI-havit).

WI-favav fixed ONE shape of this bug: cohort SELECTION resolves a repo living one
level down in a collection subdir (``ALL_REPOS/<collection>/<repo>``) via
``pool_utils.resolve_repo_path``, while a downstream consumer re-derived the path
with a bare ``os.path.join(pool_path, repo_name)`` and missed it. The resolver was
then applied to only TWO of its call sites and missed in three more -- WI-havit.

Observed 2026-09-10: ``bakeoff-deep cohort --repos django`` selected the repo and
wrote its correct absolute path into the cohort metadata; ``bakeoff-deep run`` then
discarded that path, rebuilt ``<pool>/django``, and died in ``get_dir_size`` with
FileNotFoundError. The reflect sites are worse in kind -- they do not crash, they
hand a reflect agent a source path that does not exist, so the pass reads nothing
and reports quietly.

This file is named for the script it covers so the top-level test map selects it
when that script changes (``tests/test_top_level_test_map.py``); a single
family-wide file was unreachable from every source but one. The enumeration guard
below is deliberately family-wide in EVERY copy, so touching any one script re-runs
the check across all of them -- that is what a third partial fix would trip over.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
SCRIPT = "bakeoff-deep"
FAMILY = ["bakeoff-deep", "bakeoff-deep-reflect", "bakeoff-broad", "bakeoff-broad-reflect"]


def _load(script_name: str):
    """Import a hyphenated script as a module, registered before exec."""
    mod_name = script_name.replace("-", "_")
    loader = importlib.machinery.SourceFileLoader(mod_name, str(SCRIPTS / script_name))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register BEFORE exec_module
    loader.exec_module(mod)
    return mod


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()


def test_nested_collection_repo_resolves(tmp_path: Path) -> None:
    """The shape that broke the run: a repo one level down in a collection dir."""
    _make_repo(tmp_path / "whole_bunch_of_repos" / "django")
    resolved = _load(SCRIPT)._pool_repo_path(str(tmp_path), "django")
    assert Path(resolved) == tmp_path / "whole_bunch_of_repos" / "django"


def test_top_level_repo_resolves(tmp_path: Path) -> None:
    _make_repo(tmp_path / "toprepo")
    resolved = _load(SCRIPT)._pool_repo_path(str(tmp_path), "toprepo")
    assert Path(resolved) == tmp_path / "toprepo"


def test_missing_repo_falls_back_to_naive_join(tmp_path: Path) -> None:
    """A genuinely-absent repo keeps the caller's existing FileNotFoundError path.

    Returning None instead would push the failure downstream and turn a loud
    crash into a confusing one.
    """
    resolved = _load(SCRIPT)._pool_repo_path(str(tmp_path), "ghost")
    assert Path(resolved) == tmp_path / "ghost"


_NAIVE = re.compile(r"os\.path\.join\(\s*pool_path\s*,\s*repo_name\s*\)")


@pytest.mark.parametrize("script", FAMILY)
def test_no_naive_pool_join_survives(script: str) -> None:
    """Family-wide: no naive join outside ``_pool_repo_path``'s own fallback."""
    source = (SCRIPTS / script).read_text()
    parts = source.split("def _pool_repo_path", 1)
    assert len(parts) == 2, f"{script} has no _pool_repo_path helper"
    before, after = parts
    tail = after.split("\ndef ", 1)
    rest = tail[1] if len(tail) == 2 else ""
    for chunk, where in ((before, "before the helper"), (rest, "after the helper")):
        for line in chunk.splitlines():
            if _NAIVE.search(line) and not line.lstrip().startswith(("#", '"', "'", "*")):
                pytest.fail(f"{script}: naive pool join {where}: {line.strip()}")
