# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jupar: comparison-sketch artifacts must not accumulate in /tmp.

Before this fix, `cmd_sketch` wrote two comparison-budget sketches
(4x and 16x of the user budget) to
``/tmp/hypergumbo_sketch_compare/sketch.<tokens>.withsource.md``.
The filename was keyed only on token count, so different repos
race-overwrote each other; nothing ever cleaned up the directory;
and the user-facing message told users to ``cp`` files into their
own cache directory (a UX leak).

The fix moves the comparison sketches into ``cache_dir`` (the
per-repo, per-state results cache) so they:

- live next to the main sketch under the user's own cache directory,
- are isolated per repo (no cross-repo collision),
- get cleaned up by ``cache-clear`` like every other cache entry,
- INV-padum's honk threshold sees them.

These tests pin the invariant that no ``/tmp/hypergumbo_sketch_compare/``
directory is created during a sketch run.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _tmp_sketch_compare_dir() -> Path:
    """Path the legacy producer used."""
    import tempfile
    return Path(tempfile.gettempdir()) / "hypergumbo_sketch_compare"


@pytest.fixture
def isolated_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Redirect ``tempfile.gettempdir()`` so we can prove the producer
    doesn't write to it.

    Pointing ``TMPDIR`` at ``tmp_path / _tmp`` means any legacy code that
    writes to ``Path(tempfile.gettempdir()) / "hypergumbo_sketch_compare"``
    lands inside the per-test sandbox, and the test can assert that path
    was *not* created.
    """
    tmp_root = tmp_path / "_tmp"
    tmp_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_root))
    # tempfile caches gettempdir() on first call; clear so the env var
    # actually takes effect this session.
    import tempfile
    tempfile.tempdir = None
    return tmp_root


def test_sketch_run_does_not_create_tmp_sketch_compare_dir(
    tmp_path: Path,
    isolated_tmpdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `hypergumbo sketch` run does not create /tmp/hypergumbo_sketch_compare/."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")

    legacy_dir = isolated_tmpdir / "hypergumbo_sketch_compare"

    result = main(["sketch", str(repo), "-t", "100"])

    assert result == 0
    assert not legacy_dir.exists(), (
        f"Legacy /tmp directory {legacy_dir} should not be created; "
        "comparison sketches now live in cache_dir."
    )


def test_sketch_run_writes_comparison_sketches_to_cache(
    tmp_path: Path,
    isolated_tmpdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comparison-budget sketches land in cache_dir, alongside the main sketch."""
    cache_root = tmp_path / "xdg_cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")

    result = main(["sketch", str(repo), "-t", "100"])

    assert result == 0

    # Locate the per-repo, per-state results dir under XDG_CACHE_HOME.
    cache_root_hg = cache_root / "hypergumbo"
    assert cache_root_hg.exists()
    # Each repo's "results" tree contains the cached sketches.
    sketches = list(cache_root_hg.rglob("sketch.*.md"))
    assert sketches, f"No cached sketches under {cache_root_hg}"
    tokens_with_sketch = {
        int(s.name.split(".")[1]) for s in sketches if s.name.split(".")[1].isdigit()
    }
    # Should include both 4x (400) and 16x (1600) budgets.
    assert 400 in tokens_with_sketch
    assert 1600 in tokens_with_sketch


def test_sketch_message_does_not_suggest_cp_from_tmp(
    tmp_path: Path,
    isolated_tmpdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The UX message no longer instructs the user to ``cp`` from /tmp."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")

    result = main(["sketch", str(repo), "-t", "100"])

    assert result == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # No more "cp /tmp/hypergumbo_sketch_compare/..." suggestions.
    assert "/tmp/hypergumbo_sketch_compare" not in combined
    assert "  cp " not in combined  # cp-suggestion lines start with two spaces


def test_legacy_tmp_dir_is_cleaned_up_if_present(
    tmp_path: Path,
    isolated_tmpdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-shot migration: a stale legacy /tmp/hypergumbo_sketch_compare/ is
    removed when the producer next runs (so existing accumulations drain)."""
    monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
    legacy_dir = isolated_tmpdir / "hypergumbo_sketch_compare"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "sketch.32000.withsource.md").write_text("stale\n")
    (legacy_dir / "sketch.8000.withsource.md").write_text("also stale\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")

    result = main(["sketch", str(repo), "-t", "100"])

    assert result == 0
    assert not legacy_dir.exists(), (
        f"Stale legacy dir {legacy_dir} should be cleaned up on first run."
    )
