# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo cache commands."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.cli import cmd_cache_status, cmd_cache_clear


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
