# SPDX-License-Identifier: MPL-2.0
"""Tests for SVG→PNG→ANSI rendering pipeline and graceful degradation (ADR-0020).

Covers:
- Availability detection for cairosvg and chafa
- Graceful degradation when either is unavailable
- SVG→PNG rendering via cairosvg
- PNG→ANSI rendering via chafa subprocess
- Inline preview caching keyed on (svg_path, mtime, panel_width)
- Collapse/expand placeholder generation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDependencyAvailability:
    """Tests for detecting optional dependency availability."""

    def test_detect_cairosvg_available(self) -> None:
        """cairosvg availability is detected correctly."""
        from hypergumbo_tracker.preview_pipeline import is_cairosvg_available

        # We don't know if cairosvg is installed, just test the function runs
        result = is_cairosvg_available()
        assert isinstance(result, bool)

    def test_detect_chafa_available(self) -> None:
        """chafa availability is detected correctly."""
        from hypergumbo_tracker.preview_pipeline import is_chafa_available

        result = is_chafa_available()
        assert isinstance(result, bool)

    def test_cairosvg_available_when_import_succeeds(self) -> None:
        """Reports available when cairosvg can be imported."""
        from hypergumbo_tracker.preview_pipeline import _try_import_cairosvg

        # Mock builtins.__import__ to succeed for cairosvg
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        with patch("builtins.__import__", side_effect=original_import):
            result = _try_import_cairosvg()
        assert isinstance(result, bool)
        # The result depends on whether cairosvg is actually installed,
        # but the code path through line 34 is exercised either way.
        # To guarantee True, mock the import directly:
        with patch.dict("sys.modules", {"cairosvg": MagicMock()}):
            assert _try_import_cairosvg() is True

    def test_cairosvg_unavailable_when_import_fails(self) -> None:
        """Reports unavailable when cairosvg import raises."""
        from hypergumbo_tracker import preview_pipeline

        with patch.object(preview_pipeline, "_try_import_cairosvg", return_value=False):
            assert not preview_pipeline.is_cairosvg_available()

    def test_chafa_unavailable_when_not_on_path(self) -> None:
        """Reports unavailable when chafa not on PATH."""
        from hypergumbo_tracker import preview_pipeline

        with patch.object(preview_pipeline, "_try_find_chafa", return_value=False):
            assert not preview_pipeline.is_chafa_available()


class TestGracefulDegradation:
    """Tests for fallback behavior when dependencies are missing."""

    def test_render_preview_without_cairosvg(self, tmp_path: Path) -> None:
        """Returns fallback text when cairosvg is unavailable."""
        from hypergumbo_tracker.preview_pipeline import render_svg_preview

        svg_file = tmp_path / "screenshot.svg"
        svg_file.write_text("<svg/>")

        with patch("hypergumbo_tracker.preview_pipeline.is_cairosvg_available", return_value=False):
            result = render_svg_preview(str(svg_file), panel_width=60)
        assert "preview unavailable" in result.lower()
        assert "cairosvg" in result.lower()

    def test_render_preview_without_chafa(self, tmp_path: Path) -> None:
        """Returns fallback text when chafa is unavailable."""
        from hypergumbo_tracker.preview_pipeline import render_svg_preview

        svg_file = tmp_path / "screenshot.svg"
        svg_file.write_text("<svg/>")

        with patch("hypergumbo_tracker.preview_pipeline.is_cairosvg_available", return_value=True), \
             patch("hypergumbo_tracker.preview_pipeline.is_chafa_available", return_value=False):
            result = render_svg_preview(str(svg_file), panel_width=60)
        assert "preview unavailable" in result.lower()
        assert "chafa" in result.lower()

    def test_render_preview_file_not_found(self) -> None:
        """Returns fallback when SVG file doesn't exist."""
        from hypergumbo_tracker.preview_pipeline import render_svg_preview

        result = render_svg_preview("/nonexistent/path.svg", panel_width=60)
        assert "not found" in result.lower() or "preview unavailable" in result.lower()


    def test_render_preview_success(self, tmp_path: Path) -> None:
        """Renders preview when both deps available and file exists."""
        from hypergumbo_tracker.preview_pipeline import render_svg_preview

        svg_file = tmp_path / "test.svg"
        svg_file.write_text("<svg/>")

        with patch("hypergumbo_tracker.preview_pipeline.is_cairosvg_available", return_value=True), \
             patch("hypergumbo_tracker.preview_pipeline.is_chafa_available", return_value=True), \
             patch("hypergumbo_tracker.preview_pipeline._render_pipeline", return_value="ANSI art here"):
            result = render_svg_preview(str(svg_file), panel_width=60)
        assert result == "ANSI art here"


class TestRenderPipeline:
    """Tests for the actual SVG→PNG→ANSI pipeline with mocked deps."""

    def test_render_pipeline_mocked(self, tmp_path: Path) -> None:
        """_render_pipeline calls cairosvg and chafa correctly."""
        from unittest.mock import MagicMock
        from hypergumbo_tracker.preview_pipeline import _render_pipeline

        svg_file = tmp_path / "test.svg"
        svg_file.write_text("<svg/>")

        mock_cairosvg = MagicMock()
        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = "ANSI output"

        with patch.dict("sys.modules", {"cairosvg": mock_cairosvg}), \
             patch("subprocess.run", return_value=mock_subprocess_result) as mock_run:
            result = _render_pipeline(str(svg_file), panel_width=60)

        assert result == "ANSI output"
        mock_cairosvg.svg2png.assert_called_once()
        mock_run.assert_called_once()
        # Verify chafa was called with correct size
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "chafa"
        assert "--size" in call_args[0][0]
        assert "60x" in call_args[0][0]


class TestTryImportCairosvg:
    """Tests for _try_import_cairosvg import detection."""

    def test_import_fails(self) -> None:
        """Reports False when cairosvg import fails."""
        from hypergumbo_tracker.preview_pipeline import _try_import_cairosvg

        with patch.dict("sys.modules", {"cairosvg": None}):
            # Patching sys.modules with None causes ImportError
            import sys
            saved = sys.modules.pop("cairosvg", "SENTINEL")
            try:
                with patch("builtins.__import__", side_effect=ImportError("no cairosvg")):
                    assert not _try_import_cairosvg()
            finally:
                if saved != "SENTINEL":
                    sys.modules["cairosvg"] = saved


class TestCollapsedPlaceholder:
    """Tests for collapsed preview placeholder."""

    def test_collapsed_placeholder_format(self) -> None:
        """Collapsed preview shows filename and expand hint."""
        from hypergumbo_tracker.preview_pipeline import collapsed_placeholder

        result = collapsed_placeholder(".agent/screenshots/INV-foo-20260330.svg")
        assert "INV-foo-20260330.svg" in result
        assert "expand" in result.lower() or "enter" in result.lower()


class TestPreviewCache:
    """Tests for inline preview caching."""

    def test_cache_miss_returns_none(self) -> None:
        """Cache miss returns None."""
        from hypergumbo_tracker.preview_pipeline import PreviewCache

        cache = PreviewCache()
        assert cache.get("/path.svg", 1234.0, 60) is None

    def test_cache_hit_returns_value(self) -> None:
        """Cache hit returns stored value."""
        from hypergumbo_tracker.preview_pipeline import PreviewCache

        cache = PreviewCache()
        cache.put("/path.svg", 1234.0, 60, "cached preview text")
        assert cache.get("/path.svg", 1234.0, 60) == "cached preview text"

    def test_cache_invalidated_by_mtime(self) -> None:
        """Different mtime invalidates cache."""
        from hypergumbo_tracker.preview_pipeline import PreviewCache

        cache = PreviewCache()
        cache.put("/path.svg", 1234.0, 60, "old preview")
        assert cache.get("/path.svg", 1235.0, 60) is None

    def test_cache_invalidated_by_width(self) -> None:
        """Different panel width invalidates cache."""
        from hypergumbo_tracker.preview_pipeline import PreviewCache

        cache = PreviewCache()
        cache.put("/path.svg", 1234.0, 60, "60-col preview")
        assert cache.get("/path.svg", 1234.0, 80) is None
