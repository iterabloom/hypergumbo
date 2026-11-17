# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facts about the installed environment are read once per environment.

``schema._detect_tree_sitter_versions`` parsed the metadata of every installed
distribution on every run to find the tree-sitter grammars: 33 ms of the
100 ms a run on a one-file repository takes
(``~/hypergumbo_lab_notebook/slow_tests_11142026/minrun``). It is now kept by
``installed_env.kept_per_environment``. What is pinned here is when a kept
answer is used and when it is not:

* the key follows the metadata directories on ``sys.path`` (an install, an
  upgrade, a same-version reinstall, a removal) and a changed search path;
* it does NOT follow other files written to a ``sys.path`` entry: under
  pytest the repository root is one, and is written to all the time;
* answers are per slot, and a builder that raises keeps nothing;
* the grammar versions are read once for any number of runs, the caller gets
  its own dictionary, and a stand-in ``distributions`` is never answered with
  the real environment's versions.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from hypergumbo_core import cli, installed_env, schema


@pytest.fixture(autouse=True)
def _nothing_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with nothing kept; the module's own state is put back after."""
    monkeypatch.setattr(installed_env, "_KEPT", None)


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A search path of one empty directory."""
    directory = tmp_path / "site-packages"
    directory.mkdir()
    monkeypatch.setattr(sys, "path", [str(directory)])
    return directory


class _Builder:
    def __init__(self, value: Any = None) -> None:
        self.calls = 0
        self.value = value

    def __call__(self) -> Any:
        self.calls += 1
        return self.value if self.value is not None else {"built": self.calls}


# --- the key ----------------------------------------------------------------


def test_the_key_follows_the_metadata_directories(site: Path) -> None:
    empty = installed_env.environment_key()
    assert empty == ((str(site), ()),)
    info = site / "demo-1.0.dist-info"
    info.mkdir()
    installed = installed_env.environment_key()
    assert installed != empty and installed[0][1][0][0] == "demo-1.0.dist-info"
    # A reinstall of the same version rewrites the directory.
    stamp = info.stat().st_mtime_ns + 1_000_000_000
    os.utime(info, ns=(stamp, stamp))
    reinstalled = installed_env.environment_key()
    assert reinstalled != installed
    (site / "legacy.egg-info").mkdir()
    assert installed_env.environment_key() != reinstalled
    info.rmdir()
    (site / "legacy.egg-info").rmdir()
    assert installed_env.environment_key() == empty


def test_the_key_ignores_everything_else_in_a_search_path_entry(site: Path) -> None:
    (site / "demo-1.0.dist-info").mkdir()
    before = installed_env.environment_key()
    (site / ".coverage.host.123").write_text("x")
    (site / "__pycache__").mkdir()
    (site / "module.py").write_text("x = 1\n")
    stamp = site.stat().st_mtime_ns + 1_000_000_000
    os.utime(site, ns=(stamp, stamp))
    assert installed_env.environment_key() == before


def test_the_key_names_an_entry_it_cannot_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    later = tmp_path / "created-later"
    monkeypatch.setattr(sys, "path", [str(later)])
    assert installed_env.environment_key() == ((str(later), None),)
    later.mkdir()
    assert installed_env.environment_key() == ((str(later), ()),)


def test_an_empty_entry_is_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "here-2.0.dist-info").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [""])
    assert installed_env.environment_key()[0][1][0][0] == "here-2.0.dist-info"


# --- what is kept -------------------------------------------------------------


def test_an_answer_is_built_once_per_environment(site: Path) -> None:
    build = _Builder()
    first = installed_env.kept_per_environment("slot", build)
    assert installed_env.kept_per_environment("slot", build) is first
    assert build.calls == 1
    (site / "demo-1.0.dist-info").mkdir()
    second = installed_env.kept_per_environment("slot", build)
    assert second == {"built": 2} and build.calls == 2
    assert installed_env.kept_per_environment("slot", build) is second


def test_a_changed_search_path_builds_again(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = _Builder()
    installed_env.kept_per_environment("slot", build)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(sys, "path", [str(site), str(other)])
    installed_env.kept_per_environment("slot", build)
    assert build.calls == 2


def test_slots_do_not_share_an_answer(site: Path) -> None:
    one, two = _Builder("one"), _Builder("two")
    assert installed_env.kept_per_environment(("a", 1), one) == "one"
    assert installed_env.kept_per_environment(("a", 2), two) == "two"
    assert installed_env.kept_per_environment(("a", 1), two) == "one"
    assert (one.calls, two.calls) == (1, 1)


def test_a_builder_that_raises_keeps_nothing(site: Path) -> None:
    def broken() -> str:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        installed_env.kept_per_environment("slot", broken)
    assert installed_env.kept_per_environment("slot", lambda: "ok") == "ok"


def test_a_kept_none_is_an_answer(site: Path) -> None:
    calls: list[int] = []

    def build() -> None:
        calls.append(1)

    assert installed_env.kept_per_environment("slot", build) is None
    assert installed_env.kept_per_environment("slot", build) is None
    assert calls == [1]


# --- the grammar versions -----------------------------------------------------


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per walk over the installed distributions."""
    walks: list[int] = []
    real = importlib.metadata.distributions

    def counting(*args: Any, **kwargs: Any) -> Any:
        walks.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "distributions", counting)
    return walks


def test_the_grammar_versions_are_read_once(reads: list[int]) -> None:
    version, grammars = schema._detect_tree_sitter_versions()
    again_version, again_grammars = schema._detect_tree_sitter_versions()
    assert reads == [1]
    assert version == again_version == importlib.metadata.version("tree-sitter")
    assert grammars == again_grammars and grammars, "grammars are installed here"
    assert all(name.startswith("tree-sitter-") for name in grammars)


def test_each_caller_gets_its_own_dictionary(reads: list[int]) -> None:
    _version, grammars = schema._detect_tree_sitter_versions()
    kept = dict(grammars)
    grammars["tree-sitter-added-by-the-caller"] = "0"
    grammars.pop(next(iter(kept)))
    assert schema._detect_tree_sitter_versions()[1] == kept


def test_a_new_distribution_is_seen(
    reads: list[int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production path: something is installed between two runs."""
    _version, before = schema._detect_tree_sitter_versions()
    info = tmp_path / "tree_sitter_wi_demo-9.9.9.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: tree-sitter-wi-demo\nVersion: 9.9.9\n")
    monkeypatch.setattr(sys, "path", [*sys.path, str(tmp_path)])
    _version, after = schema._detect_tree_sitter_versions()
    assert "tree-sitter-wi-demo" not in before
    assert after == {**before, "tree-sitter-wi-demo": "9.9.9"}
    assert reads == [1, 1]


def test_a_stand_in_is_not_answered_with_the_real_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _version, real = schema._detect_tree_sitter_versions()
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: iter(()))
    assert schema._detect_tree_sitter_versions()[1] == {}
    monkeypatch.undo()
    assert schema._detect_tree_sitter_versions()[1] == real


def test_two_runs_read_the_environment_once(tmp_path: Path, reads: list[int]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def main():\n    return 1\n")
    captured = []
    for name in ("one", "two"):
        out = tmp_path / name / "map.json"
        out.parent.mkdir()
        cli.run_behavior_map(repo_root=repo, out_path=out)
        captured.append(json.loads(out.read_text())["reproducibility_context"]["captured"])
    assert captured[0]["tree_sitter_version"], "the run must report the library"
    assert captured[0] == captured[1]
    assert reads == [1]
