# SPDX-License-Identifier: AGPL-3.0-or-later
"""Git submodules are installed dependency source (WI-tiril, ADR-0004).

HEAD holds only a gitlink for a submodule, so its contents are not
committed: a fresh clone never has them. Detected by evidence -- a path
``.gitmodules`` declares whose HEAD entry is a gitlink (mode 160000) --
skipped by default, disclosed, and analysed as tier 3 with
``--trace-deps <path>``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hypergumbo_core import discovery
from hypergumbo_core.discovery import (
    FileIndex,
    TraceDeps,
    format_installed_deps_disclosure,
    installed_deps_summary,
    match_content_rule,
    set_active_trace_deps,
)


_GIT = shutil.which("git") or "git"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [_GIT, "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "protocol.file.allow=always", "-c", "init.defaultBranch=main",
         *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def superproject(tmp_path: Path) -> Path:
    """A repository with one populated submodule at ``lib/sub``."""
    sub = tmp_path / "subsrc"
    sub.mkdir()
    (sub / "dep.py").write_text("def dep():\n    return 1\n")
    _git(sub, "init", "-q")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-qm", "sub")
    root = tmp_path / "super"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    return 0\n")
    _git(root, "init", "-q")
    _git(root, "submodule", "add", "-q", str(sub), "lib/sub")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "super")
    yield root
    set_active_trace_deps(TraceDeps(), None)


def _clear_caches() -> None:
    for fn in (discovery._match_content_rule, discovery._match_submodule,
               discovery._declared_submodule_paths, discovery._head_entries):
        fn.cache_clear()


def test_populated_submodule_is_claimed(superproject: Path) -> None:
    _clear_caches()
    rule = match_content_rule(superproject / "lib" / "sub")
    assert rule is not None and rule.is_installed_dep
    assert rule.ecosystem == "git submodule"
    # The rule's own marker answers the same question, for callers that treat
    # every ContentRule alike.
    assert rule.marker(superproject / "lib" / "sub")
    assert not rule.marker(superproject / "lib")
    assert match_content_rule(superproject / "lib") is None


def test_skipped_by_default_and_disclosed(superproject: Path) -> None:
    _clear_caches()
    set_active_trace_deps(TraceDeps(), superproject)
    index = FileIndex.build(superproject)
    paths = {p.relative_to(superproject).as_posix() for p in index.match_pattern("*.py")}
    assert paths == {"app.py"}
    summary = installed_deps_summary(index.installed_dep_roots, superproject, TraceDeps())
    assert summary["entries"] == [{
        "path": "lib/sub", "ecosystem": "git submodule", "packages": 1, "traced": [],
    }]
    line = format_installed_deps_disclosure(summary)
    assert line is not None
    assert line.startswith("Installed dependency source not analysed: lib/sub/ (git submodule).")


def test_trace_deps_path_analyses_it_as_tier_3(superproject: Path) -> None:
    from hypergumbo_core.supply_chain import Tier, classify_file

    _clear_caches()
    trace = TraceDeps.parse("lib/sub")
    set_active_trace_deps(trace, superproject)
    index = FileIndex.build(superproject)
    paths = {p.relative_to(superproject).as_posix() for p in index.match_pattern("*.py")}
    assert paths == {"app.py", "lib/sub/dep.py"}
    got = classify_file(superproject / "lib" / "sub" / "dep.py", superproject)
    assert got.tier == Tier.EXTERNAL_DEP
    assert got.package_name == "lib/sub"
    summary = installed_deps_summary(index.installed_dep_roots, superproject, trace)
    assert summary["entries"] == []


def test_unpopulated_submodule_is_a_no_op(superproject: Path, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(superproject), str(clone))
    _clear_caches()
    assert (clone / "lib" / "sub").is_dir()  # git leaves the empty directory
    assert match_content_rule(clone / "lib" / "sub") is None
    set_active_trace_deps(TraceDeps(), clone)
    index = FileIndex.build(clone)
    assert installed_deps_summary(index.installed_dep_roots, clone, TraceDeps())["dirs"] == 0


def test_without_git_a_declared_path_with_a_git_file_is_claimed(tmp_path: Path) -> None:
    # Outside git (an exported tree): .gitmodules entry plus a .git FILE.
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/x"]\n\tpath = vendor/x\n\turl = https://example.invalid/x\n',
    )
    (tmp_path / "vendor" / "x").mkdir(parents=True)
    (tmp_path / "vendor" / "x" / ".git").write_text("gitdir: ../../.git/modules/x\n")
    (tmp_path / "vendor" / "x" / "x.c").write_text("int x;\n")
    _clear_caches()
    rule = match_content_rule(tmp_path / "vendor" / "x")
    assert rule is not None and rule.ecosystem == "git submodule"


def test_an_undeclared_nested_repository_is_not_a_submodule(tmp_path: Path) -> None:
    # A repository cloned inside another, which its .gitmodules does not
    # declare: the search stops at the enclosing work tree.
    outer = tmp_path / "outer"
    nested = outer / "tools" / "other"
    nested.mkdir(parents=True)
    (outer / ".gitmodules").write_text('[submodule "x"]\n\tpath = lib/x\n')
    (nested / "o.py").write_text("x = 1\n")
    _git(outer, "init", "-q")
    _git(nested, "init", "-q")
    _clear_caches()
    assert discovery._submodule_declaration(nested) is None
    assert match_content_rule(nested) is None


def test_declaration_search_stops_when_no_work_tree_encloses_it(tmp_path: Path) -> None:
    # A .git-bearing directory with no enclosing work tree and no active root:
    # the search runs out of ancestors and claims nothing.
    set_active_trace_deps(TraceDeps(), None)
    lone = tmp_path / "a" / "b"
    lone.mkdir(parents=True)
    (lone / ".git").write_text("gitdir: elsewhere\n")
    _clear_caches()
    assert discovery._submodule_declaration(lone) is None
    assert match_content_rule(lone) is None
