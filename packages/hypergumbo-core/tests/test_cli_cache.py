# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo cache commands."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.cli import cmd_cache_status, cmd_cache_clear
from hypergumbo_core.schema import READ_VIEW_SCHEMA_VERSION


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


class TestCacheStatus:
    """Tests for cache status command."""

    def test_cache_status_empty_cache(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Status reports zero when cache dir doesn't exist."""
        args = FakeArgs()
        args.quiet = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=tmp_path / "cache"):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "0 entries" in captured.out or "empty" in captured.out.lower()

    def test_cache_status_with_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Status shows count and size when cache has entries."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create some cache entries
        for i in range(3):
            entry = cache_dir / f"fingerprint_{i}"
            entry.mkdir()
            (entry / "embeddings").mkdir()
            (entry / "results").mkdir()
            # Write some data to make size non-zero
            (entry / "embeddings" / "data.json").write_text('{"data": "test"}')

        args = FakeArgs()
        args.quiet = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "3" in captured.out  # 3 entries

    def test_cache_status_quiet_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Quiet mode suppresses output."""
        args = FakeArgs()
        args.quiet = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=tmp_path / "cache"):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_cache_status_json_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """WI-dulul: --format json on a nonexistent cache emits a zeroed envelope."""
        import json
        args = FakeArgs()
        args.quiet = False
        args.format = "json"
        with patch("hypergumbo_core.cli._get_cache_base", return_value=tmp_path / "nope"):
            assert cmd_cache_status(args) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["schema_version"] == READ_VIEW_SCHEMA_VERSION
        assert data["view"] == "cache_status"
        assert data["total_entries"] == 0
        assert data["total_size_bytes"] == 0
        assert data["entries"] == []

    def test_cache_status_json_with_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """WI-dulul: --format json emits per-repo breakdown + totals."""
        import json
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        for i in range(2):
            entry = cache_dir / f"fingerprint_{i}"
            entry.mkdir()
            (entry / "embeddings").mkdir()
            (entry / "results").mkdir()
            (entry / "embeddings" / "data.json").write_text('{"data": "test"}')
        args = FakeArgs()
        args.quiet = False
        args.format = "json"
        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            assert cmd_cache_status(args) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["view"] == "cache_status"
        assert data["total_entries"] == 2
        assert data["total_size_bytes"] > 0
        assert len(data["entries"]) == 2
        assert all(
            "fingerprint" in e and "size_bytes" in e and "entry_count" in e
            and "age_days" in e
            for e in data["entries"]
        )

    def test_cache_status_empty_existing_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Status reports zero entries when cache dir exists but is empty."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()  # Exists but has no entries

        args = FakeArgs()
        args.quiet = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Entries: 0" in captured.out


class TestCacheClear:
    """Tests for cache clear command."""

    def test_cache_clear_removes_all(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Clear removes all cache entries."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create cache entries
        for i in range(2):
            entry = cache_dir / f"fingerprint_{i}"
            entry.mkdir()
            (entry / "data.json").write_text("{}")

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # Cache dir itself should still exist, but be empty
        assert cache_dir.exists()
        assert list(cache_dir.iterdir()) == []

    def test_cache_clear_dry_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry run shows what would be deleted without deleting."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        entry = cache_dir / "fingerprint_1"
        entry.mkdir()
        (entry / "data.json").write_text("{}")

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # Entry should still exist
        assert entry.exists()
        captured = capsys.readouterr()
        assert "dry run" in captured.out.lower() or "would" in captured.out.lower()

    def test_cache_clear_older_than(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Clear with older_than only removes old entries."""
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create an "old" entry (set mtime to 10 days ago)
        old_entry = cache_dir / "old_fingerprint"
        old_entry.mkdir()
        old_time = time.time() - (10 * 24 * 60 * 60)  # 10 days ago
        os.utime(old_entry, (old_time, old_time))

        # Create a "new" entry (current time)
        new_entry = cache_dir / "new_fingerprint"
        new_entry.mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = 7  # days
        args.dry_run = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # Old entry should be deleted
        assert not old_entry.exists()
        # New entry should remain
        assert new_entry.exists()

    def test_cache_clear_nonexistent_cache(self, tmp_path: Path) -> None:
        """Clear handles missing cache dir gracefully."""
        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=tmp_path / "nonexistent"):
            result = cmd_cache_clear(args)

        assert result == 0

    def test_cache_clear_older_than_no_old_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Clear with older_than when all entries are newer than threshold."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create only "new" entries (current time)
        new_entry = cache_dir / "new_fingerprint"
        new_entry.mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = 7  # days
        args.dry_run = False

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # Entry should remain (it's new)
        assert new_entry.exists()
        captured = capsys.readouterr()
        assert "No entries older than 7 days found" in captured.out


class TestCacheHelpers:
    """Tests for cache helper functions."""

    def test_get_cache_base_returns_path(self) -> None:
        """_get_cache_base returns a Path under .cache/hypergumbo."""
        from hypergumbo_core.cli import _get_cache_base

        result = _get_cache_base()

        assert isinstance(result, Path)
        assert result.name == "hypergumbo"
        assert ".cache" in str(result) or "XDG_CACHE_HOME" in os.environ

    def test_format_size_bytes(self) -> None:
        """_format_size formats small sizes correctly."""
        from hypergumbo_core.cli import _format_size

        assert _format_size(500) == "500.0 B"
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_get_dir_size_empty_dir(self, tmp_path: Path) -> None:
        """_get_dir_size returns 0 for empty directory."""
        from hypergumbo_core.cli import _get_dir_size

        assert _get_dir_size(tmp_path) == 0

    def test_get_dir_size_with_files(self, tmp_path: Path) -> None:
        """_get_dir_size sums file sizes."""
        from hypergumbo_core.cli import _get_dir_size

        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world!")

        size = _get_dir_size(tmp_path)
        assert size == 11  # 5 + 6 bytes


class TestCacheIntegration:
    """Integration tests for cache commands via main()."""

    def test_cache_status_via_main(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Test cache-status command via main entry point."""
        from hypergumbo_core.cli import main

        with patch("hypergumbo_core.cli._get_cache_base", return_value=tmp_path / "cache"):
            result = main(["cache-status"])

        assert result == 0

    def test_cache_clear_via_main(self, tmp_path: Path) -> None:
        """Test cache-clear command via main entry point."""
        from hypergumbo_core.cli import main

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = main(["cache-clear"])

        assert result == 0


class TestHonkThreshold:
    """INV-padum: cache honk-threshold (retention with loud warning)."""

    def test_default_threshold_is_one_gb(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env var is unset, threshold defaults to 1.0 GiB in bytes."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.delenv("HYPERGUMBO_CACHE_HONK_GB", raising=False)

        assert _get_honk_threshold_bytes() == 1.0 * (1024 ** 3)

    def test_env_var_overrides_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HYPERGUMBO_CACHE_HONK_GB overrides the default."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "2.5")

        assert _get_honk_threshold_bytes() == 2.5 * (1024 ** 3)

    def test_env_zero_silences(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A value of 0 silences the honk entirely."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")

        assert _get_honk_threshold_bytes() is None

    def test_env_off_silences(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Symbolic 'off' silences the honk for users who can't set 0."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "off")

        assert _get_honk_threshold_bytes() is None

    def test_negative_threshold_silences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative values silence (no meaningful negative threshold)."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "-1")

        assert _get_honk_threshold_bytes() is None

    def test_malformed_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed env var falls back to default with a warning, not a crash."""
        from hypergumbo_core.cli import _get_honk_threshold_bytes

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "garbage")

        with pytest.warns(UserWarning, match="HYPERGUMBO_CACHE_HONK_GB"):
            value = _get_honk_threshold_bytes()
        assert value == 1.0 * (1024 ** 3)

    def test_honk_fires_when_threshold_exceeded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Honk emits to stderr when cache size exceeds threshold."""
        from hypergumbo_core.cli import _maybe_honk_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        repo = cache_dir / "abc123"
        repo.mkdir()
        (repo / "blob").write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0.001")  # 1 MB threshold

        _maybe_honk_cache(cache_dir)

        captured = capsys.readouterr()
        assert "HG cache is" in captured.err
        assert "threshold" in captured.err
        assert "HYPERGUMBO_CACHE_HONK_GB" in captured.err

    def test_honk_silent_below_threshold(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Honk does not fire when cache is under threshold."""
        from hypergumbo_core.cli import _maybe_honk_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "small").mkdir()
        (cache_dir / "small" / "x").write_text("tiny")

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "1.0")

        _maybe_honk_cache(cache_dir)

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_honk_silent_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When threshold is 0, honk never fires even on huge caches."""
        from hypergumbo_core.cli import _maybe_honk_cache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        repo = cache_dir / "abc"
        repo.mkdir()
        (repo / "big").write_bytes(b"x" * (5 * 1024 * 1024))

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")

        _maybe_honk_cache(cache_dir)

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_honk_silent_when_cache_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Honk is a no-op when cache directory does not yet exist."""
        from hypergumbo_core.cli import _maybe_honk_cache

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0.0001")

        _maybe_honk_cache(tmp_path / "nonexistent")

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_honk_in_cache_status(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`cache-status` emits honk to stderr when over threshold."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        repo = cache_dir / "abc"
        repo.mkdir()
        (repo / "x").write_bytes(b"y" * (3 * 1024 * 1024))

        args = FakeArgs()
        args.quiet = False
        args.per_repo = False

        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0.001")
        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "HG cache is" in captured.err


class TestPerRepoBreakdown:
    """INV-padum: cache-status --per-repo surfaces per-repo cache footprint."""

    def test_per_repo_lists_each_repo(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--per-repo prints one line per repo subdirectory."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        for name in ("alpha", "beta", "gamma"):
            repo = cache_dir / name
            (repo / "results" / "s1").mkdir(parents=True)
            (repo / "results" / "s2").mkdir(parents=True)
            (repo / "data").write_text(name * 10)

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out
        assert "gamma" in captured.out

    def test_per_repo_sorted_by_size_desc(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-repo lines are sorted with the largest consumer first."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "small" / "results").mkdir(parents=True)
        (cache_dir / "small" / "data").write_text("tiny")
        (cache_dir / "huge" / "results").mkdir(parents=True)
        (cache_dir / "huge" / "data").write_bytes(b"x" * 100_000)

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            cmd_cache_status(args)

        out = capsys.readouterr().out
        # 'huge' line must precede 'small' line
        assert out.index("huge") < out.index("small")

    def test_per_repo_counts_state_subdirs(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-repo entry count = number of state-hash subdirs under results/."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        repo = cache_dir / "abc"
        for i in range(5):
            (repo / "results" / f"state{i}").mkdir(parents=True)

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            cmd_cache_status(args)

        out = capsys.readouterr().out
        assert "5" in out  # 5 entries

    def test_per_repo_skips_files(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Top-level files (not dirs) under cache_dir are skipped."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "stray.txt").write_text("not a repo dir")
        (cache_dir / "abc" / "results").mkdir(parents=True)

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            cmd_cache_status(args)

        out = capsys.readouterr().out
        assert "stray.txt" not in out

    def test_per_repo_handles_missing_results_dir(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A repo subdir without results/ reports 0 entries (not a crash)."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "abc").mkdir()  # no results/ subdir

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "abc" in out

    def test_per_repo_no_repos_prints_none(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--per-repo prints '(none)' when no repo subdirs exist."""
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "loose.txt").write_text("not a repo")  # files only, no dirs

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_status(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "(none)" in out

    def test_per_repo_age_label_one_day_and_older(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Age labels: '1 day ago' (exactly 1d) vs 'N days ago' (>=2d)."""
        import time
        monkeypatch.setenv("HYPERGUMBO_CACHE_HONK_GB", "0")
        cache_dir = tmp_path / "cache"
        (cache_dir / "yesterday" / "results").mkdir(parents=True)
        (cache_dir / "weekold" / "results").mkdir(parents=True)
        # Set mtimes
        now = time.time()
        one_day_ago = now - (1.2 * 86400)  # ~1 day
        seven_days_ago = now - (7.0 * 86400)
        os.utime(cache_dir / "yesterday", (one_day_ago, one_day_ago))
        os.utime(cache_dir / "weekold", (seven_days_ago, seven_days_ago))

        args = FakeArgs()
        args.quiet = False
        args.per_repo = True

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            cmd_cache_status(args)

        out = capsys.readouterr().out
        assert "1 day ago" in out
        assert "7 days ago" in out


class TestCacheClearKeepLatest:
    """INV-padum: cache-clear --repo --keep-latest N."""

    def test_keep_latest_keeps_n_most_recent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--keep-latest N keeps the N most recent state-hash subdirs."""
        import time
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        results = repo / "results"
        results.mkdir(parents=True)

        # Create 5 state dirs with monotonically increasing mtimes
        base = time.time() - 1000
        for i in range(5):
            state = results / f"state{i}"
            state.mkdir()
            os.utime(state, (base + i, base + i))

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "abc"
        args.keep_latest = 2

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        remaining = sorted(d.name for d in results.iterdir())
        assert remaining == ["state3", "state4"]

    def test_keep_latest_dry_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--keep-latest with --dry-run reports without deleting."""
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        results = repo / "results"
        results.mkdir(parents=True)
        for i in range(3):
            (results / f"state{i}").mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = True
        args.repo = "abc"
        args.keep_latest = 1

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # Nothing deleted
        assert len(list(results.iterdir())) == 3
        out = capsys.readouterr().out
        assert "would" in out.lower() or "dry" in out.lower()

    def test_keep_latest_zero_clears_all(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--keep-latest 0 deletes every state entry in the repo."""
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        results = repo / "results"
        results.mkdir(parents=True)
        for i in range(3):
            (results / f"state{i}").mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "abc"
        args.keep_latest = 0

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        assert list(results.iterdir()) == []

    def test_repo_without_keep_latest_clears_whole_repo(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--repo without --keep-latest clears the entire repo subdir."""
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        (repo / "results" / "state0").mkdir(parents=True)
        (repo / "embeddings").mkdir()
        (cache_dir / "other").mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "abc"
        args.keep_latest = None

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        assert not repo.exists()
        # Other repos untouched
        assert (cache_dir / "other").exists()

    @pytest.mark.parametrize(
        "hostile_repo",
        ["/ABSOLUTE/VICTIM", "../../VICTIM", "sub/dir", "..", "a/../../VICTIM"],
        ids=["absolute", "traversal", "separator", "dotdot", "mixed"],
    )
    def test_repo_flag_cannot_name_a_path_outside_the_cache(
        self, tmp_path: Path, hostile_repo: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--repo`` names a cache subdirectory, never a filesystem path.

        VERIFIED DEFECT: ``cache-clear --repo /home/you/thesis`` recursively
        deleted that directory and printed ``Deleted repo … (36.0 B)`` as
        though it were routine cache eviction. ``cache_dir / repo`` discards
        the left operand when ``repo`` is absolute; ``..`` traverses out; and
        the only downstream check was ``is_dir()``, which is existence, not
        containment. Every other subcommand's ``--repo``-shaped flag takes a
        path, so ``cache-clear --repo "$REPO"`` is a natural thing to script.

        The safety-zone check in ``cache_rmtree`` is the backstop and is
        tested separately; this asserts the CLI refuses at the boundary with
        a usable error instead of surfacing an internal exception.
        """
        cache_dir = tmp_path / "cache"
        (cache_dir / "abc").mkdir(parents=True)
        victim = tmp_path / "VICTIM"
        victim.mkdir()
        (victim / "thesis.txt").write_text("six months of work")

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = hostile_repo
        args.keep_latest = None

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result != 0, "a rejected --repo must not report success"
        assert (victim / "thesis.txt").read_text() == "six months of work"
        assert (cache_dir / "abc").exists()
        err = capsys.readouterr().err
        assert "internal error" not in err.lower(), (
            "refusing a bad --repo is not a crash; it needs a usable message"
        )

    def test_repo_unknown_returns_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--repo with an unknown fingerprint is a no-op (not an error)."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "does-not-exist"
        args.keep_latest = None

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0

    def test_keep_latest_without_repo_is_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--keep-latest requires --repo; without it, exits non-zero."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = None
        args.keep_latest = 3

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result != 0
        captured = capsys.readouterr()
        assert "--repo" in captured.err or "--repo" in captured.out

    def test_keep_latest_handles_missing_results_dir(
        self, tmp_path: Path
    ) -> None:
        """--keep-latest in a repo without results/ is a no-op."""
        cache_dir = tmp_path / "cache"
        (cache_dir / "abc").mkdir(parents=True)  # no results/

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "abc"
        args.keep_latest = 3

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        # repo dir is preserved (results was missing, nothing to delete)
        assert (cache_dir / "abc").exists()

    def test_repo_dry_run_preserves_subtree(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--repo --dry-run reports the would-delete size without deleting."""
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        (repo / "results" / "state0").mkdir(parents=True)
        (repo / "embeddings").mkdir()
        (repo / "data").write_text("payload")

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = True
        args.repo = "abc"
        args.keep_latest = None

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        assert repo.exists()
        out = capsys.readouterr().out
        assert "would" in out.lower() or "dry" in out.lower()
        assert "abc" in out

    def test_keep_latest_when_count_at_or_below_keep(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--keep-latest N with N >= current count is a no-op."""
        cache_dir = tmp_path / "cache"
        repo = cache_dir / "abc"
        results = repo / "results"
        results.mkdir(parents=True)
        (results / "state0").mkdir()
        (results / "state1").mkdir()

        args = FakeArgs()
        args.quiet = False
        args.older_than = None
        args.dry_run = False
        args.repo = "abc"
        args.keep_latest = 5

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = cmd_cache_clear(args)

        assert result == 0
        assert sorted(d.name for d in results.iterdir()) == ["state0", "state1"]
        out = capsys.readouterr().out
        assert "nothing to prune" in out


class TestCacheCommandWiring:
    """Argparse wiring for the new flags."""

    def test_cache_status_per_repo_flag_parsed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`cache-status --per-repo` parses and runs."""
        from hypergumbo_core.cli import main

        cache_dir = tmp_path / "cache"
        (cache_dir / "abc" / "results").mkdir(parents=True)

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = main(["cache-status", "--per-repo"])

        assert result == 0

    def test_cache_clear_repo_keep_latest_flags_parsed(
        self, tmp_path: Path
    ) -> None:
        """`cache-clear --repo X --keep-latest 1` parses and runs."""
        from hypergumbo_core.cli import main

        cache_dir = tmp_path / "cache"
        (cache_dir / "abc" / "results" / "s1").mkdir(parents=True)
        (cache_dir / "abc" / "results" / "s2").mkdir(parents=True)

        with patch("hypergumbo_core.cli._get_cache_base", return_value=cache_dir):
            result = main(
                ["cache-clear", "--repo", "abc", "--keep-latest", "1"]
            )

        assert result == 0
