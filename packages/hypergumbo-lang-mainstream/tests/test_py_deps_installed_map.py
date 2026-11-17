# SPDX-License-Identifier: AGPL-3.0-or-later
"""The installed-distribution map is built once per environment, not per run.

``py_deps`` turns a declared distribution name into the names it is imported
by (``PyYAML`` -> ``yaml``) by inverting
``importlib.metadata.packages_distributions()``. That call reads the metadata
of every installed distribution: 0.36 s for a development environment of 250
on an idle machine, 0.86 s a call in a loaded test run, and
``parse_python_dependencies`` reached it twice per pipeline run on any tree
with a Python dependency manifest.

The inverted map is now kept. What is pinned here is when it is rebuilt:

* when a ``sys.path`` entry appears or goes, or a distribution's metadata
  directory (``*.dist-info``) in one appears, goes or is rewritten, which is
  what installing, upgrading or removing a distribution does;
* NOT when anything else in a ``sys.path`` entry changes: under pytest the
  repository root is such an entry, and files are written there constantly;
* when ``packages_distributions`` itself is a different function (a test's
  stand-in is never answered with the real environment's map, or the reverse).
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path

import pytest

from hypergumbo_core import installed_env
from hypergumbo_lang_mainstream import py_deps


@pytest.fixture(autouse=True)
def _no_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with nothing kept; the module's own state is put back after."""
    monkeypatch.setattr(installed_env, "_KEPT", None)


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stand in for ``packages_distributions`` and count its calls."""
    calls: list[int] = []

    def fake() -> dict[str, list[str]]:
        calls.append(1)
        return {"yaml": ["PyYAML"], "_yaml": ["PyYAML"], "bs4": ["beautifulsoup4"],
                "sklearn": ["scikit_learn"]}

    monkeypatch.setattr(importlib.metadata, "packages_distributions", fake)
    return calls


def test_the_map_inverts_and_normalizes(resolver: list[int]) -> None:
    built = py_deps._build_dist_to_import_map()
    assert built == {"pyyaml": {"yaml", "_yaml"}, "beautifulsoup4": {"bs4"},
                     "scikit-learn": {"sklearn"}}
    assert py_deps._resolve_import_names({"PyYAML", "Scikit_Learn", "not-installed"}) == {
        "yaml", "_yaml", "sklearn", "not_installed"}


def test_the_environment_is_read_once(resolver: list[int]) -> None:
    first = py_deps._build_dist_to_import_map()
    second = py_deps._build_dist_to_import_map()
    py_deps._resolve_import_names({"PyYAML"})
    assert first == second
    assert len(resolver) == 1


def test_a_caller_cannot_change_the_kept_map(resolver: list[int]) -> None:
    built = py_deps._build_dist_to_import_map()
    with pytest.raises(TypeError):
        built["pyyaml"] = frozenset()  # type: ignore[index]
    with pytest.raises(AttributeError):
        built["pyyaml"].add("x")  # type: ignore[attr-defined]


def test_a_new_distribution_in_a_site_directory_rebuilds_the_map(
    resolver: list[int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(sys, "path", [str(site)])
    py_deps._build_dist_to_import_map()
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 1
    # What an install does: a new <name>.dist-info directory in the site dir.
    info = site / "demo-1.0.dist-info"
    info.mkdir()
    py_deps._build_dist_to_import_map()
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 2
    # A reinstall of the same version rewrites that directory.
    stamp = info.stat().st_mtime_ns + 1_000_000_000
    os.utime(info, ns=(stamp, stamp))
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 3
    # An uninstall removes it.
    info.rmdir()
    py_deps._build_dist_to_import_map()
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 4


def test_other_files_written_to_a_search_path_entry_do_not_rebuild_the_map(
    resolver: list[int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repository root is on ``sys.path`` under pytest, and coverage data,
    caches and outputs are written there all the time."""
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    py_deps._build_dist_to_import_map()
    (tmp_path / ".coverage.host.123").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "module.py").write_text("x = 1\n")
    stamp = tmp_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(tmp_path, ns=(stamp, stamp))
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 1


def test_a_changed_search_path_rebuilds_the_map(
    resolver: list[int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    monkeypatch.setattr(sys, "path", [str(one)])
    py_deps._build_dist_to_import_map()
    monkeypatch.setattr(sys, "path", [str(one), str(two)])
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 2


def test_a_search_path_entry_that_does_not_exist_is_part_of_the_key(
    resolver: list[int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry that is missing now may be created by an install."""
    later = tmp_path / "created-later"
    monkeypatch.setattr(sys, "path", [str(later), ""])
    py_deps._build_dist_to_import_map()
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 1
    later.mkdir()
    py_deps._build_dist_to_import_map()
    assert len(resolver) == 2


def test_another_resolver_is_never_answered_from_the_kept_map(
    resolver: list[int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "pyyaml" in py_deps._build_dist_to_import_map()
    monkeypatch.setattr(importlib.metadata, "packages_distributions",
                        lambda: {"requests": ["requests"]})
    assert py_deps._build_dist_to_import_map() == {"requests": {"requests"}}


def test_the_real_environment_answers() -> None:
    """Reach: with nothing stood in, the map names an installed distribution."""
    built = py_deps._build_dist_to_import_map()
    assert "yaml" in built.get("pyyaml", set())
    assert py_deps._build_dist_to_import_map() is built
