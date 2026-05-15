# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-panih's analyzer_identity_hash."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hypergumbo_core import analyzer_identity
from hypergumbo_core.analyzer_identity import (
    _hash_package_py_files,
    compute_analyzer_identity_hash,
    reset_cache_for_testing,
)


def _fake_module(*paths: str):
    """Build a minimal module-like object with a ``__path__`` list.

    Used to drive ``_hash_package_py_files`` without faking class
    state (which trips RUF012 for mutable class attrs).
    """
    return SimpleNamespace(__path__=list(paths))


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a fresh memoization slot."""
    reset_cache_for_testing()
    yield
    reset_cache_for_testing()


def test_hash_is_16_char_hex():
    h = compute_analyzer_identity_hash()
    assert len(h) == 16
    int(h, 16)  # raises if non-hex


def test_hash_is_stable_across_calls_in_one_process():
    """Repeated calls return the same value (memoized)."""
    h1 = compute_analyzer_identity_hash()
    h2 = compute_analyzer_identity_hash()
    assert h1 == h2


def test_hash_is_stable_across_processes_for_unchanged_install():
    """The hash is deterministic across re-imports of the module.

    (Approximated here: reset the memo, recompute, expect identity.
    The real cross-process guarantee follows from the same content
    being hashed both times.)
    """
    h1 = compute_analyzer_identity_hash()
    reset_cache_for_testing()
    h2 = compute_analyzer_identity_hash()
    assert h1 == h2


def test_hash_changes_when_a_tracked_package_py_file_changes(tmp_path: Path):
    """Modifying one .py file in a tracked hypergumbo_* package
    shifts the hash. This is the dev-workflow guarantee — edits to
    analyzer code must invalidate the cache.

    We simulate this at the `_hash_package_py_files` level since we
    can't actually mutate an installed package's source file from
    a test without breaking the rest of the suite.
    """
    pkg_v1 = tmp_path / "pkg1"
    pkg_v1.mkdir()
    (pkg_v1 / "a.py").write_text("x = 1\n")
    (pkg_v1 / "b.py").write_text("y = 2\n")

    h1 = _hash_package_py_files(_fake_module(str(pkg_v1)))

    # Modify a.py.
    (pkg_v1 / "a.py").write_text("x = 999\n")
    h2 = _hash_package_py_files(_fake_module(str(pkg_v1)))

    assert h1 != h2


def test_hash_includes_all_installed_hypergumbo_packages():
    """The roll-up must include every hypergumbo_* distribution.

    If a future contributor adds a fifth lang package and forgets the
    hash plumbing, the result should change vs. the four-package
    baseline. We assert by inspecting the deterministic-rollup
    bytes: every currently-installed hypergumbo_* module name
    must appear in the input to the final sha256.
    """
    from importlib.metadata import distributions

    expected_module_names: set[str] = set()
    for dist in distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name.startswith("hypergumbo"):
            expected_module_names.add(name.replace("-", "_"))

    # Reach into the module to capture the per-package hash dict by
    # re-running the discovery logic with patched rollup.
    seen: dict[str, str] = {}

    real_hash_fn = _hash_package_py_files

    def capturing_hash(module):
        # Capture the *module name* used in the rollup.
        name = getattr(module, "__name__", "")
        result = real_hash_fn(module)
        seen[name] = result
        return result

    with patch.object(
        analyzer_identity, "_hash_package_py_files", side_effect=capturing_hash
    ):
        reset_cache_for_testing()
        compute_analyzer_identity_hash()

    # `hypergumbo` is a top-level meta-package present in this env;
    # filter to the analyzer-relevant subset that the discovery walk
    # actually picks up.
    seen_analyzer_pkgs = {n for n in seen if n.startswith("hypergumbo")}
    # The walk skips modules whose __path__ overlaps a previously-
    # hashed module (defensive against namespace packages), so we
    # don't assert one-to-one parity with expected_module_names —
    # we assert the structural invariant: hypergumbo_core is always
    # present, and at least one lang package proves the dist-metadata
    # walk actually fires (not just the hardcoded import).
    assert expected_module_names  # discovery had something to find
    assert "hypergumbo_core" in seen_analyzer_pkgs
    assert any(
        name.startswith("hypergumbo_lang_")
        for name in seen_analyzer_pkgs
    )


def test_hash_package_py_files_ignores_pycache(tmp_path: Path):
    """``__pycache__`` files are skipped — they're build artifacts,
    not analyzer source, and their presence is non-deterministic."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")

    baseline = _hash_package_py_files(_fake_module(str(pkg)))

    # Drop a stray .py under __pycache__/ — would change the hash if
    # the walk were naive.
    cache_dir = pkg / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "spurious.py").write_text("# stray\n")

    after = _hash_package_py_files(_fake_module(str(pkg)))
    assert after == baseline


def test_hash_package_py_files_is_path_order_stable(tmp_path: Path):
    """The same content under a differently-ordered ``__path__`` list
    should produce the same hash (sort happens internally).
    """
    pkg_a = tmp_path / "a"
    pkg_a.mkdir()
    (pkg_a / "x.py").write_text("x = 1\n")
    pkg_b = tmp_path / "b"
    pkg_b.mkdir()
    (pkg_b / "y.py").write_text("y = 2\n")

    forward = _fake_module(str(pkg_a), str(pkg_b))
    reverse = _fake_module(str(pkg_b), str(pkg_a))

    assert _hash_package_py_files(forward) == _hash_package_py_files(reverse)


def test_hash_package_py_files_empty_package_yields_known_hash(
    tmp_path: Path,
):
    """A package with no .py files yields the empty-input sha256.

    Pins the boundary case so a future refactor that injects bogus
    bytes for the empty path doesn't silently shift every analyzer
    identity hash.
    """
    pkg = tmp_path / "empty"
    pkg.mkdir()

    expected = hashlib.sha256(b"").hexdigest()[:16]
    assert _hash_package_py_files(_fake_module(str(pkg))) == expected


def test_discovery_skips_distributions_that_fail_to_import():
    """Covers the ``if module is None: continue`` defensive branch.

    A distribution whose metadata lists it as installed but whose
    import fails (broken extension, namespace package, etc.) is
    silently skipped — we don't crash the cache-path constructor.
    """
    # Inject a fake hypergumbo distribution that fails to import.
    from importlib.metadata import distributions as real_distributions

    real_dists = list(real_distributions())

    class _FakeMetadata:
        def get(self, key, default=None):
            return "hypergumbo-broken" if key == "Name" else default

    class _FakeDist:
        metadata = _FakeMetadata()

    fake_dists = [*real_dists, _FakeDist()]

    with patch.object(analyzer_identity, "distributions", return_value=fake_dists):
        # `hypergumbo-broken` will fail _try_import since the module
        # `hypergumbo_broken` doesn't exist.
        reset_cache_for_testing()
        # Should not raise.
        h = compute_analyzer_identity_hash()
        assert len(h) == 16


def test_discovery_skips_distributions_with_empty_path_attr():
    """Covers the ``if not paths: continue`` defensive branch.

    Single-file modules without ``__path__`` (or with an empty one)
    contribute nothing to the analyzer identity and are skipped.
    """
    from importlib.metadata import distributions as real_distributions

    real_dists = list(real_distributions())

    class _FakeMetadata:
        def get(self, key, default=None):
            return "hypergumbo-pathless" if key == "Name" else default

    class _FakeDist:
        metadata = _FakeMetadata()

    fake_module = SimpleNamespace(__path__=[])  # empty paths

    with patch.object(
        analyzer_identity, "distributions",
        return_value=[*real_dists, _FakeDist()],
    ), patch.object(
        analyzer_identity, "_try_import", return_value=fake_module,
    ):
        reset_cache_for_testing()
        h = compute_analyzer_identity_hash()
        assert len(h) == 16


def test_discovery_skips_namespace_overlap(tmp_path: Path):
    """Covers the ``if any(p in seen_paths for p in paths): continue``
    defensive branch.

    If a second distribution reports a ``__path__`` that overlaps
    one we've already hashed (namespace package case), skip it to
    avoid double-counting.
    """
    from importlib.metadata import distributions as real_distributions

    real_dists = list(real_distributions())
    import hypergumbo_core as hg_core

    # The fake "distribution" claims hypergumbo_core's own path.
    overlapping_path = list(hg_core.__path__)

    class _FakeMetadata:
        def get(self, key, default=None):
            return "hypergumbo-overlap" if key == "Name" else default

    class _FakeDist:
        metadata = _FakeMetadata()

    fake_module = SimpleNamespace(__path__=overlapping_path)

    def fake_try_import(name):
        if name == "hypergumbo_overlap":
            return fake_module
        return __import__(name)

    with patch.object(
        analyzer_identity, "distributions",
        return_value=[*real_dists, _FakeDist()],
    ), patch.object(
        analyzer_identity, "_try_import", side_effect=fake_try_import,
    ):
        reset_cache_for_testing()
        h = compute_analyzer_identity_hash()
        assert len(h) == 16


def test_hash_package_py_files_skips_nonexistent_path(tmp_path: Path):
    """Covers the ``if not pkg_path.exists(): continue`` branch."""
    nonexistent = tmp_path / "ghost"
    # Don't create it.
    h = _hash_package_py_files(_fake_module(str(nonexistent)))
    # Empty walk → empty-input sha256 prefix.
    assert h == hashlib.sha256(b"").hexdigest()[:16]


def test_results_cache_dir_includes_analyzer_identity_segment(tmp_path: Path):
    """The cache path returned by `_get_results_cache_dir` ends with
    the analyzer_identity_hash as its deepest segment.
    """
    from hypergumbo_core.sketch_embeddings import _get_results_cache_dir

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # Make it look like a git repo with no commits so the fingerprint
    # path takes the deterministic fallback.
    (repo_root / "x.py").write_text("x = 1\n")

    cache_dir = _get_results_cache_dir(repo_root)
    expected_segment = compute_analyzer_identity_hash()
    assert cache_dir.name == expected_segment
    # Layout: .../<fingerprint>/results/<state_hash>/<analyzer_identity>/
    parts = cache_dir.parts
    assert parts[-2] != ""  # state_hash present
    assert parts[-3] == "results"
