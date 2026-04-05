# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.preview (ADR-0020 Part 2).

Covers the SVG preview rendering pipeline, path extraction from
discussion text, caching, graceful degradation, and placeholder
formatting.

Test strategy:
- Unit tests for extract_svg_paths: regex matching against various
  discussion message patterns
- Unit tests for format_preview_placeholder and format_unavailable_notice
- Mocked tests for render_svg_preview: mock cairosvg and chafa
  availability, mock subprocess.run for chafa invocation
- Cache tests: verify cache hits, misses, and invalidation
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hypergumbo_tracker.preview import (
    _SVG_PATH_RE,
    _cache,
    _cairosvg_available,
    _chafa_available,
    _svg_to_png,
    clear_cache,
    extract_svg_paths,
    format_preview_placeholder,
    format_unavailable_notice,
    render_svg_preview,
)


# ---------------------------------------------------------------------------
# extract_svg_paths
# ---------------------------------------------------------------------------


class TestExtractSvgPaths:
    """Tests for SVG path extraction from discussion text."""

    def test_agent_screenshot_path(self) -> None:
        """Extracts .agent/screenshots/ paths."""
        text = "Fix the layout issue in .agent/screenshots/INV-foo-20260330-1422.svg please"
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/INV-foo-20260330-1422.svg"]

    def test_absolute_path(self) -> None:
        """Extracts absolute SVG paths."""
        text = "See /home/user/screenshots/test.svg for details"
        paths = extract_svg_paths(text)
        assert paths == ["/home/user/screenshots/test.svg"]

    def test_multiple_paths(self) -> None:
        """Extracts multiple SVG paths from one message."""
        text = (
            "Compare .agent/screenshots/before.svg and "
            ".agent/screenshots/after.svg"
        )
        paths = extract_svg_paths(text)
        assert len(paths) == 2

    def test_no_svg_paths(self) -> None:
        """Returns empty list when no SVG paths present."""
        text = "This message has no screenshots"
        assert extract_svg_paths(text) == []

    def test_non_svg_extension_ignored(self) -> None:
        """Non-.svg file extensions are not matched."""
        text = "See .agent/screenshots/image.png for the issue"
        assert extract_svg_paths(text) == []

    def test_path_at_start_of_line(self) -> None:
        """Path at the start of text is matched."""
        text = ".agent/screenshots/test.svg shows the bug"
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/test.svg"]

    def test_path_at_end_of_line(self) -> None:
        """Path at the end of text is matched."""
        text = "See .agent/screenshots/test.svg"
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/test.svg"]


# ---------------------------------------------------------------------------
# format functions
# ---------------------------------------------------------------------------


class TestFormatFunctions:
    """Tests for placeholder and notice formatting."""

    def test_preview_placeholder(self) -> None:
        """Placeholder shows filename only."""
        result = format_preview_placeholder(
            ".agent/screenshots/INV-foo-20260330-1422.svg"
        )
        assert result == "[screenshot: INV-foo-20260330-1422.svg]"

    def test_unavailable_notice(self) -> None:
        """Unavailable notice includes dependency hint."""
        result = format_unavailable_notice(
            ".agent/screenshots/test.svg"
        )
        assert "test.svg" in result
        assert "cairosvg" in result
        assert "chafa" in result


# ---------------------------------------------------------------------------
# render_svg_preview
# ---------------------------------------------------------------------------


class TestRenderSvgPreview:
    """Tests for the SVG→PNG→ANSI rendering pipeline."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_cache()

    @patch("hypergumbo_tracker.preview._chafa_available", return_value=False)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    def test_returns_none_when_chafa_missing(
        self, mock_cairo: Any, mock_chafa: Any, tmp_path: Path,
    ) -> None:
        """Returns None when chafa is not available."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")
        assert render_svg_preview(svg) is None

    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=False)
    def test_returns_none_when_cairosvg_missing(
        self, mock_cairo: Any, mock_chafa: Any, tmp_path: Path,
    ) -> None:
        """Returns None when cairosvg is not available."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")
        assert render_svg_preview(svg) is None

    def test_returns_none_for_nonexistent_file(self) -> None:
        """Returns None for a file that doesn't exist."""
        assert render_svg_preview(Path("/nonexistent/file.svg")) is None

    @patch("hypergumbo_tracker.preview.shutil.which", return_value="/usr/bin/chafa")
    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    @patch("hypergumbo_tracker.preview.subprocess.run")
    @patch("hypergumbo_tracker.preview._svg_to_png")
    def test_successful_render(
        self, mock_svg2png: MagicMock, mock_run: MagicMock,
        mock_cairo: Any, mock_chafa: Any, mock_which: Any,
        tmp_path: Path,
    ) -> None:
        """Successful pipeline returns ANSI string."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg><text>hello</text></svg>")

        mock_svg2png.return_value = b"\x89PNG_FAKE_DATA"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"\x1b[38;2;255;0;0m\xe2\x96\x88\x1b[0m\n",
        )

        result = render_svg_preview(svg)
        assert result is not None
        assert isinstance(result, str)

    @patch("hypergumbo_tracker.preview.shutil.which", return_value="/usr/bin/chafa")
    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    @patch("hypergumbo_tracker.preview.subprocess.run")
    @patch("hypergumbo_tracker.preview._svg_to_png")
    def test_cache_hit(
        self, mock_svg2png: MagicMock, mock_run: MagicMock,
        mock_cairo: Any, mock_chafa: Any, mock_which: Any,
        tmp_path: Path,
    ) -> None:
        """Second call for same file uses cache."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")

        mock_svg2png.return_value = b"\x89PNG"
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"cached_result\n",
        )

        result1 = render_svg_preview(svg)
        result2 = render_svg_preview(svg)

        assert result1 == result2
        # _svg_to_png should only be called once (cache hit on second)
        assert mock_svg2png.call_count == 1

    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    @patch("hypergumbo_tracker.preview._svg_to_png")
    def test_cairosvg_exception_returns_none(
        self, mock_svg2png: MagicMock, mock_cairo: Any, mock_chafa: Any,
        tmp_path: Path,
    ) -> None:
        """Returns None when cairosvg raises an exception."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")

        mock_svg2png.side_effect = RuntimeError("cairo error")
        result = render_svg_preview(svg)
        assert result is None

    @patch("hypergumbo_tracker.preview.shutil.which", return_value="/usr/bin/chafa")
    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    @patch("hypergumbo_tracker.preview.subprocess.run")
    @patch("hypergumbo_tracker.preview._svg_to_png")
    def test_chafa_timeout_returns_none(
        self, mock_svg2png: MagicMock, mock_run: MagicMock,
        mock_cairo: Any, mock_chafa: Any, mock_which: Any,
        tmp_path: Path,
    ) -> None:
        """Returns None when chafa times out."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")

        mock_svg2png.return_value = b"\x89PNG"
        mock_run.side_effect = subprocess.TimeoutExpired("chafa", 10)
        result = render_svg_preview(svg)
        assert result is None

    def test_clear_cache(self, tmp_path: Path) -> None:
        """clear_cache empties the cache dict."""
        _cache[("test", 0, 60)] = "cached"
        assert len(_cache) == 1
        clear_cache()
        assert len(_cache) == 0

    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    def test_stat_error_returns_none(
        self, mock_cairo: Any, mock_chafa: Any, tmp_path: Path,
    ) -> None:
        """Returns None when stat() fails on the SVG file."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")

        original_stat = Path.stat

        call_count = 0

        def failing_stat(self_path: Path, *a: Any, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Let is_file() work (first stat call), fail on the explicit stat()
            if call_count > 1:
                raise OSError("permission denied")
            return original_stat(self_path, *a, **kw)

        with patch.object(Path, "stat", failing_stat):
            result = render_svg_preview(svg)

        assert result is None


# ---------------------------------------------------------------------------
# Availability check and _svg_to_png coverage
# ---------------------------------------------------------------------------


class TestAvailabilityChecks:
    """Tests for _cairosvg_available, _chafa_available, and _svg_to_png."""

    def test_cairosvg_available_when_installed(self) -> None:
        """Returns True when cairosvg is importable (mocked for CI)."""
        import builtins
        original_import = builtins.__import__
        # Always allow cairosvg import (even in CI where it's not installed)
        mock_cairo = MagicMock()

        def allow_cairosvg(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "cairosvg":
                return mock_cairo
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=allow_cairosvg):
            assert _cairosvg_available() is True

    def test_cairosvg_unavailable(self) -> None:
        """Returns False when cairosvg import fails."""
        import builtins
        original_import = builtins.__import__

        def block_cairosvg(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "cairosvg":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=block_cairosvg):
            assert _cairosvg_available() is False

    def test_chafa_available_when_present(self) -> None:
        """Returns True when chafa is on PATH."""
        with patch("hypergumbo_tracker.preview.shutil.which", return_value="/usr/bin/chafa"):
            assert _chafa_available() is True

    def test_chafa_unavailable(self) -> None:
        """Returns False when chafa is not on PATH."""
        with patch("hypergumbo_tracker.preview.shutil.which", return_value=None):
            assert _chafa_available() is False

    def test_svg_to_png_calls_cairosvg(self, tmp_path: Path) -> None:
        """_svg_to_png delegates to cairosvg.svg2png."""
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")

        mock_cairo = MagicMock()
        mock_cairo.svg2png.return_value = b"\x89PNG"
        with patch.dict("sys.modules", {"cairosvg": mock_cairo}):
            # Force re-import inside the function
            result = _svg_to_png(svg, 60)

        assert result == b"\x89PNG"
        mock_cairo.svg2png.assert_called_once()

    @patch("hypergumbo_tracker.preview._svg_to_png", return_value=None)
    @patch("hypergumbo_tracker.preview._chafa_available", return_value=True)
    @patch("hypergumbo_tracker.preview._cairosvg_available", return_value=True)
    def test_render_returns_none_when_svg_to_png_returns_none(
        self, mock_cairo: Any, mock_chafa: Any, mock_png: Any,
        tmp_path: Path,
    ) -> None:
        """render_svg_preview returns None when _svg_to_png returns None."""
        clear_cache()
        svg = tmp_path / "test.svg"
        svg.write_text("<svg></svg>")
        assert render_svg_preview(svg) is None
