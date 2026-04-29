# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/lib/pool_utils.py``.

Covers ``is_repo_root`` (the heuristic that distinguishes a single repo from
a collection-of-repos), ``iter_pool_repos`` (bounded one-level recursion
through a pool root), and ``resolve_repo_path`` (explicit-name lookup with
optional one-level descent).
"""
import os
import sys
from pathlib import Path

import pytest

# scripts/lib/ isn't a package — inject the path so we can import.
_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
import pool_utils  # noqa: E402


# ---------------------------------------------------------------------------
# is_repo_root
# ---------------------------------------------------------------------------


def test_is_repo_root_with_git_dir(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert pool_utils.is_repo_root(str(repo)) is True


def test_is_repo_root_with_source_file(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hi')\n")
    assert pool_utils.is_repo_root(str(repo)) is True


def test_is_repo_root_with_manifest_file(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\n")
    assert pool_utils.is_repo_root(str(repo)) is True


def test_is_repo_root_with_root_manifest_name(tmp_path):
    """Bare manifest names without an extension (e.g., ``Makefile``)."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "Makefile").write_text("all:\n\techo hi\n")
    assert pool_utils.is_repo_root(str(repo)) is True


def test_is_repo_root_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert pool_utils.is_repo_root(str(empty)) is False


def test_is_repo_root_collection_of_repos(tmp_path):
    """A directory containing only subdirectories looks like a collection."""
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "repo-a").mkdir()
    (coll / "repo-b").mkdir()
    assert pool_utils.is_repo_root(str(coll)) is False


def test_is_repo_root_nonexistent_path(tmp_path):
    assert pool_utils.is_repo_root(str(tmp_path / "nope")) is False


def test_is_repo_root_path_is_a_file(tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x\n")
    assert pool_utils.is_repo_root(str(f)) is False


def test_is_repo_root_permission_error(monkeypatch, tmp_path):
    """When scandir raises PermissionError, default to 'not a repo'."""
    repo = tmp_path / "noaccess"
    repo.mkdir()

    real_scandir = os.scandir

    def fake_scandir(p):
        if str(p) == str(repo):
            raise PermissionError("denied")
        return real_scandir(p)

    monkeypatch.setattr(pool_utils.os, "scandir", fake_scandir)
    assert pool_utils.is_repo_root(str(repo)) is False


def test_is_repo_root_ignores_subdirs_in_top_level_scan(tmp_path):
    """``Makefile`` inside a sub-directory should not register as a repo
    indicator on the parent — only top-level files count."""
    parent = tmp_path / "parent"
    parent.mkdir()
    sub = parent / "sub"
    sub.mkdir()
    (sub / "Makefile").write_text("all:\n")
    assert pool_utils.is_repo_root(str(parent)) is False


# ---------------------------------------------------------------------------
# iter_pool_repos
# ---------------------------------------------------------------------------


def _mk_repo(parent, name):
    repo = parent / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _mk_collection_with_repos(parent, name, repo_names):
    coll = parent / name
    coll.mkdir()
    for r in repo_names:
        _mk_repo(coll, r)
    return coll


def test_iter_pool_repos_only_direct_repos(tmp_path):
    _mk_repo(tmp_path, "alpha")
    _mk_repo(tmp_path, "beta")
    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(tmp_path)))
    assert names == ["alpha", "beta"]


def test_iter_pool_repos_only_collections(tmp_path):
    _mk_collection_with_repos(tmp_path, "coll1", ["a", "b"])
    _mk_collection_with_repos(tmp_path, "coll2", ["c"])
    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(tmp_path)))
    assert names == ["a", "b", "c"]


def test_iter_pool_repos_mixed(tmp_path):
    _mk_repo(tmp_path, "direct1")
    _mk_collection_with_repos(tmp_path, "coll1", ["nested1", "nested2"])
    _mk_repo(tmp_path, "direct2")
    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(tmp_path)))
    assert names == ["direct1", "direct2", "nested1", "nested2"]


def test_iter_pool_repos_skips_hidden_top_level(tmp_path):
    _mk_repo(tmp_path, "visible")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / ".git").mkdir()
    names = [e.name for e in pool_utils.iter_pool_repos(str(tmp_path))]
    assert names == ["visible"]


def test_iter_pool_repos_skips_hidden_inside_collections(tmp_path):
    coll = tmp_path / "coll"
    coll.mkdir()
    _mk_repo(coll, "real")
    hidden = coll / ".cache"
    hidden.mkdir()
    (hidden / ".git").mkdir()
    names = [e.name for e in pool_utils.iter_pool_repos(str(tmp_path))]
    assert names == ["real"]


def test_iter_pool_repos_skips_top_level_files(tmp_path):
    _mk_repo(tmp_path, "areal")
    (tmp_path / "loose-file.txt").write_text("hi\n")
    names = [e.name for e in pool_utils.iter_pool_repos(str(tmp_path))]
    assert names == ["areal"]


def test_iter_pool_repos_skips_nondir_inside_collections(tmp_path):
    coll = tmp_path / "coll"
    coll.mkdir()
    _mk_repo(coll, "areal")
    (coll / "loose-file.txt").write_text("hi\n")
    names = [e.name for e in pool_utils.iter_pool_repos(str(tmp_path))]
    assert names == ["areal"]


def test_iter_pool_repos_max_depth_zero_no_recursion(tmp_path):
    _mk_repo(tmp_path, "direct")
    _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    names = sorted(
        e.name for e in pool_utils.iter_pool_repos(str(tmp_path), max_depth=0)
    )
    # `coll` doesn't look like a repo and we don't recurse — only `direct` shows.
    assert names == ["direct"]


def test_iter_pool_repos_nonexistent_pool(tmp_path):
    names = list(pool_utils.iter_pool_repos(str(tmp_path / "nope")))
    assert names == []


def test_iter_pool_repos_permission_error_at_pool_root(monkeypatch, tmp_path):
    real_scandir = os.scandir

    def fake_scandir(p):
        if str(p) == str(tmp_path):
            raise PermissionError("nope")
        return real_scandir(p)

    monkeypatch.setattr(pool_utils.os, "scandir", fake_scandir)
    assert list(pool_utils.iter_pool_repos(str(tmp_path))) == []


def test_iter_pool_repos_permission_error_inside_collection(monkeypatch, tmp_path):
    """If a collection's scandir fails, skip that collection but keep going."""
    _mk_repo(tmp_path, "good_direct")
    bad_coll = tmp_path / "bad_coll"
    bad_coll.mkdir()
    _mk_collection_with_repos(tmp_path, "good_coll", ["nested"])

    real_scandir = os.scandir

    def fake_scandir(p):
        if str(p) == str(bad_coll):
            raise PermissionError("locked")
        return real_scandir(p)

    monkeypatch.setattr(pool_utils.os, "scandir", fake_scandir)
    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(tmp_path)))
    assert names == ["good_direct", "nested"]


def test_iter_pool_repos_via_symlink_top_level(tmp_path):
    """Top-level symlink to a real repo dir should be followed transparently."""
    real_repo = tmp_path / "elsewhere" / "repo"
    real_repo.mkdir(parents=True)
    (real_repo / ".git").mkdir()

    pool = tmp_path / "pool"
    pool.mkdir()
    os.symlink(real_repo, pool / "linked-repo")

    names = [e.name for e in pool_utils.iter_pool_repos(str(pool))]
    assert names == ["linked-repo"]


def test_iter_pool_repos_two_levels_deep(tmp_path):
    """Repos nested two levels deep (collection-of-collections) are yielded
    when max_depth allows it. Mirrors the ``~/ALL_REPOS/plazaflow_deep_repos/
    cohort_*/repo`` structure."""
    outer = tmp_path / "outer_collection"
    outer.mkdir()
    inner = outer / "cohort_X"
    inner.mkdir()
    _mk_repo(inner, "deep_repo")
    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(tmp_path)))
    assert names == ["deep_repo"]


def test_iter_pool_repos_max_depth_one_skips_two_levels(tmp_path):
    """With max_depth=1, repos nested two levels deep are NOT yielded."""
    outer = tmp_path / "outer_collection"
    outer.mkdir()
    inner = outer / "cohort_X"
    inner.mkdir()
    _mk_repo(inner, "deep_repo")
    names = list(
        pool_utils.iter_pool_repos(str(tmp_path), max_depth=1)
    )
    assert names == []


def test_iter_pool_repos_dedup_via_realpath(tmp_path):
    """A flat symlink and the underlying nested real directory should not
    both yield the same repo. Mirrors the plazaflow_deep_repos structure
    where ``firecracker -> cohort7_compute/firecracker``."""
    pool = tmp_path / "pool"
    pool.mkdir()
    cohort = pool / "cohort_X"
    cohort.mkdir()
    real_repo = _mk_repo(cohort, "shared_repo")
    # Flat symlink at pool root pointing to the same physical repo.
    os.symlink(real_repo, pool / "shared_repo")

    yielded = list(pool_utils.iter_pool_repos(str(pool)))
    # Should yield exactly one entry — either the symlink or the real
    # path, but never both.
    assert len(yielded) == 1
    assert yielded[0].name == "shared_repo"


def test_iter_pool_repos_via_symlink_to_collection(tmp_path):
    """Top-level symlink to a collection should be descended into."""
    real_coll = tmp_path / "elsewhere" / "coll"
    real_coll.mkdir(parents=True)
    _mk_repo(real_coll, "innerA")
    _mk_repo(real_coll, "innerB")

    pool = tmp_path / "pool"
    pool.mkdir()
    os.symlink(real_coll, pool / "linked-coll")

    names = sorted(e.name for e in pool_utils.iter_pool_repos(str(pool)))
    assert names == ["innerA", "innerB"]


# ---------------------------------------------------------------------------
# resolve_repo_path
# ---------------------------------------------------------------------------


def test_resolve_repo_path_direct_hit(tmp_path):
    repo = _mk_repo(tmp_path, "direct")
    got = pool_utils.resolve_repo_path(str(tmp_path), "direct")
    assert got == str(repo)


def test_resolve_repo_path_one_level_deep(tmp_path):
    coll = _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    got = pool_utils.resolve_repo_path(str(tmp_path), "nested")
    assert got == str(coll / "nested")


def test_resolve_repo_path_with_slash_uses_direct_only(tmp_path):
    coll = _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    # Slash-bearing name resolves directly.
    got = pool_utils.resolve_repo_path(str(tmp_path), "coll/nested")
    assert got == str(coll / "nested")


def test_resolve_repo_path_with_slash_no_descent_when_missing(tmp_path):
    _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    # Slash present but path doesn't exist — don't descend.
    got = pool_utils.resolve_repo_path(str(tmp_path), "wrong/nested")
    assert got is None


def test_resolve_repo_path_not_found(tmp_path):
    _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    assert pool_utils.resolve_repo_path(str(tmp_path), "missing") is None


def test_resolve_repo_path_max_depth_zero(tmp_path):
    _mk_collection_with_repos(tmp_path, "coll", ["nested"])
    got = pool_utils.resolve_repo_path(str(tmp_path), "nested", max_depth=0)
    assert got is None


def test_resolve_repo_path_two_levels_deep(tmp_path):
    """Resolve a target nested two levels deep (collection-of-collections).
    Mirrors the ``plazaflow_deep_repos/cohort_X/repo`` shape."""
    outer = tmp_path / "outer_collection"
    outer.mkdir()
    inner = outer / "cohort_X"
    inner.mkdir()
    repo = _mk_repo(inner, "deep_target")
    got = pool_utils.resolve_repo_path(str(tmp_path), "deep_target")
    assert got == str(repo)


def test_resolve_repo_path_skips_repo_root_when_descending(tmp_path):
    """If a top-level entry is itself a repo, don't descend into it for an
    explicit name lookup — the caller would never put repo names inside repos."""
    direct = _mk_repo(tmp_path, "direct_repo")
    # Place a directory named "target" inside direct_repo. We should NOT find
    # it via descent, because direct_repo is recognized as a repo root.
    (direct / "target").mkdir()
    (direct / "target" / ".git").mkdir()
    assert pool_utils.resolve_repo_path(str(tmp_path), "target") is None


def test_resolve_repo_path_skips_hidden_during_descent(tmp_path):
    """Hidden top-level entries are not descended into during resolve."""
    hidden = tmp_path / ".cache"
    hidden.mkdir()
    _mk_repo(hidden, "target")  # would match if we descended hidden dirs
    _mk_collection_with_repos(tmp_path, "real_coll", ["other"])
    assert pool_utils.resolve_repo_path(str(tmp_path), "target") is None


def test_resolve_repo_path_skips_loose_files_during_descent(tmp_path):
    """Loose files at the pool root must not crash resolve descent.

    Uses a target that doesn't exist anywhere so the descent loop has to
    iterate every top-level entry (including the loose file), exercising
    the non-directory skip branch regardless of scandir ordering.
    """
    (tmp_path / "loose.txt").write_text("hi\n")
    _mk_collection_with_repos(tmp_path, "coll", ["other"])
    assert pool_utils.resolve_repo_path(str(tmp_path), "missing") is None


def test_resolve_repo_path_permission_error_during_descent(monkeypatch, tmp_path):
    _mk_collection_with_repos(tmp_path, "good_coll", ["target"])
    # Force scandir on the pool root to raise — should surface as None.
    real_scandir = os.scandir

    def fake_scandir(p):
        if str(p) == str(tmp_path):
            raise PermissionError("locked")
        return real_scandir(p)

    monkeypatch.setattr(pool_utils.os, "scandir", fake_scandir)
    assert pool_utils.resolve_repo_path(str(tmp_path), "target") is None
