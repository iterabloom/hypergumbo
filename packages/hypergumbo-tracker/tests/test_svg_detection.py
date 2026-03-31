# SPDX-License-Identifier: MPL-2.0
"""Tests for SVG path detection in discussion messages (ADR-0020).

Detects file paths ending in .svg within discussion message text,
checks existence on disk, and returns the paths for inline preview.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSvgPathExtraction:
    """Tests for extracting SVG file paths from text."""

    def test_detect_screenshot_path(self) -> None:
        """Detects .agent/screenshots/*.svg paths."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = "Fix the layout issue highlighted in .agent/screenshots/INV-foo-20260330-1422.svg"
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/INV-foo-20260330-1422.svg"]

    def test_detect_multiple_paths(self) -> None:
        """Detects multiple SVG paths in one message."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = (
            "See .agent/screenshots/a.svg and also .agent/screenshots/b.svg for context"
        )
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/a.svg", ".agent/screenshots/b.svg"]

    def test_no_svg_paths(self) -> None:
        """Returns empty list when no SVG paths found."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = "No screenshots here, just regular text about the bug."
        paths = extract_svg_paths(text)
        assert paths == []

    def test_detect_arbitrary_svg_path(self) -> None:
        """Detects SVG paths not in .agent/screenshots/."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = "Check docs/diagrams/architecture.svg for the design."
        paths = extract_svg_paths(text)
        assert paths == ["docs/diagrams/architecture.svg"]

    def test_ignores_svg_in_middle_of_word(self) -> None:
        """Does not match .svg embedded in non-path text."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = "The SVG format is great"
        paths = extract_svg_paths(text)
        assert paths == []

    def test_path_with_hyphens_and_dots(self) -> None:
        """Handles paths with hyphens, dots, and underscores."""
        from hypergumbo_tracker.svg_detection import extract_svg_paths

        text = "See .agent/screenshots/WI-foo-bar_baz.20260331.svg"
        paths = extract_svg_paths(text)
        assert paths == [".agent/screenshots/WI-foo-bar_baz.20260331.svg"]


class TestSvgPathValidation:
    """Tests for filtering SVG paths to those that exist on disk."""

    def test_filter_existing_paths(self, tmp_path: Path) -> None:
        """Only returns paths that exist on disk."""
        from hypergumbo_tracker.svg_detection import filter_existing_svg_paths

        # Create a real SVG file
        screenshots = tmp_path / ".agent" / "screenshots"
        screenshots.mkdir(parents=True)
        (screenshots / "real.svg").write_text("<svg/>")

        paths = [
            str(screenshots / "real.svg"),
            str(screenshots / "missing.svg"),
        ]
        existing = filter_existing_svg_paths(paths)
        assert existing == [str(screenshots / "real.svg")]

    def test_empty_input(self) -> None:
        """Empty input returns empty output."""
        from hypergumbo_tracker.svg_detection import filter_existing_svg_paths

        assert filter_existing_svg_paths([]) == []


class TestSvgPathsInMessage:
    """Integration: extract + filter in one call."""

    def test_find_existing_svg_in_message(self, tmp_path: Path) -> None:
        """End-to-end: extract paths from text, filter to existing files."""
        from hypergumbo_tracker.svg_detection import find_svg_paths_in_message

        screenshots = tmp_path / ".agent" / "screenshots"
        screenshots.mkdir(parents=True)
        svg_file = screenshots / "INV-foo-20260330-1422.svg"
        svg_file.write_text("<svg/>")

        text = f"See {svg_file} for the bug"
        found = find_svg_paths_in_message(text)
        assert found == [str(svg_file)]

    def test_no_existing_files(self) -> None:
        """Returns empty when no SVG files exist."""
        from hypergumbo_tracker.svg_detection import find_svg_paths_in_message

        text = "See /nonexistent/path/screenshot.svg"
        found = find_svg_paths_in_message(text)
        assert found == []
