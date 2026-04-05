# SPDX-License-Identifier: MPL-2.0
"""Tests for TUI screenshot annotation data model (ADR-0020).

Covers:
- Dataclass construction for Rect, Arrow, Label annotations
- JSON serialization (to_dict) and deserialization (from_dict)
- Tagged union round-tripping through JSON
- XML sanitization of label text for SVG injection
- Screenshot path generation from item ID and timestamp
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest


class TestAnnotationDataModel:
    """Tests for Rect/Arrow/Label annotation dataclasses."""

    def test_rect_annotation_defaults(self) -> None:
        """RectAnnotation has default red color."""
        from hypergumbo_tracker.annotations import RectAnnotation

        rect = RectAnnotation(cell_x1=5, cell_y1=10, cell_x2=20, cell_y2=15)
        assert rect.cell_x1 == 5
        assert rect.cell_y1 == 10
        assert rect.cell_x2 == 20
        assert rect.cell_y2 == 15
        assert rect.color == "#ff3333"

    def test_arrow_annotation_defaults(self) -> None:
        """ArrowAnnotation has default red color."""
        from hypergumbo_tracker.annotations import ArrowAnnotation

        arrow = ArrowAnnotation(from_x=0, from_y=0, to_x=10, to_y=5)
        assert arrow.from_x == 0
        assert arrow.to_x == 10
        assert arrow.color == "#ff3333"

    def test_label_annotation_defaults(self) -> None:
        """LabelAnnotation has default red color."""
        from hypergumbo_tracker.annotations import LabelAnnotation

        label = LabelAnnotation(cell_x=5, cell_y=10, text="Bug here")
        assert label.text == "Bug here"
        assert label.color == "#ff3333"

    def test_custom_color(self) -> None:
        """Annotations accept custom colors."""
        from hypergumbo_tracker.annotations import RectAnnotation

        rect = RectAnnotation(cell_x1=0, cell_y1=0, cell_x2=5, cell_y2=5, color="#00ff00")
        assert rect.color == "#00ff00"


class TestAnnotationSerialization:
    """Tests for JSON serialization/deserialization of annotations."""

    def test_rect_to_dict(self) -> None:
        """RectAnnotation serializes to tagged dict."""
        from hypergumbo_tracker.annotations import RectAnnotation

        rect = RectAnnotation(cell_x1=5, cell_y1=10, cell_x2=20, cell_y2=15)
        d = rect.to_dict()
        assert d["kind"] == "rect"
        assert d["cell_x1"] == 5
        assert d["cell_y1"] == 10
        assert d["cell_x2"] == 20
        assert d["cell_y2"] == 15
        assert d["color"] == "#ff3333"

    def test_arrow_to_dict(self) -> None:
        """ArrowAnnotation serializes to tagged dict."""
        from hypergumbo_tracker.annotations import ArrowAnnotation

        arrow = ArrowAnnotation(from_x=0, from_y=0, to_x=10, to_y=5)
        d = arrow.to_dict()
        assert d["kind"] == "arrow"
        assert d["from_x"] == 0
        assert d["to_x"] == 10

    def test_label_to_dict(self) -> None:
        """LabelAnnotation serializes to tagged dict."""
        from hypergumbo_tracker.annotations import LabelAnnotation

        label = LabelAnnotation(cell_x=5, cell_y=10, text="Bug here")
        d = label.to_dict()
        assert d["kind"] == "label"
        assert d["text"] == "Bug here"

    def test_rect_from_dict(self) -> None:
        """RectAnnotation deserializes from tagged dict."""
        from hypergumbo_tracker.annotations import annotation_from_dict, RectAnnotation

        d = {"kind": "rect", "cell_x1": 5, "cell_y1": 10, "cell_x2": 20, "cell_y2": 15, "color": "#ff3333"}
        ann = annotation_from_dict(d)
        assert isinstance(ann, RectAnnotation)
        assert ann.cell_x1 == 5

    def test_arrow_from_dict(self) -> None:
        """ArrowAnnotation deserializes from tagged dict."""
        from hypergumbo_tracker.annotations import annotation_from_dict, ArrowAnnotation

        d = {"kind": "arrow", "from_x": 0, "from_y": 0, "to_x": 10, "to_y": 5, "color": "#ff3333"}
        ann = annotation_from_dict(d)
        assert isinstance(ann, ArrowAnnotation)
        assert ann.to_x == 10

    def test_label_from_dict(self) -> None:
        """LabelAnnotation deserializes from tagged dict."""
        from hypergumbo_tracker.annotations import annotation_from_dict, LabelAnnotation

        d = {"kind": "label", "cell_x": 5, "cell_y": 10, "text": "Bug", "color": "#ff3333"}
        ann = annotation_from_dict(d)
        assert isinstance(ann, LabelAnnotation)
        assert ann.text == "Bug"

    def test_unknown_kind_raises(self) -> None:
        """Unknown annotation kind raises ValueError."""
        from hypergumbo_tracker.annotations import annotation_from_dict

        with pytest.raises(ValueError, match=r"Unknown annotation kind"):
            annotation_from_dict({"kind": "circle"})

    def test_round_trip_through_json(self) -> None:
        """Annotations survive JSON serialization round-trip."""
        from hypergumbo_tracker.annotations import (
            RectAnnotation, ArrowAnnotation, LabelAnnotation,
            annotation_from_dict,
        )

        annotations = [
            RectAnnotation(cell_x1=1, cell_y1=2, cell_x2=3, cell_y2=4),
            ArrowAnnotation(from_x=5, from_y=6, to_x=7, to_y=8),
            LabelAnnotation(cell_x=9, cell_y=10, text="Hello"),
        ]
        serialized = json.dumps([a.to_dict() for a in annotations])
        deserialized = [annotation_from_dict(d) for d in json.loads(serialized)]
        assert deserialized == annotations


class TestXmlSanitization:
    """Tests for XML sanitization of label text before SVG injection."""

    def test_sanitize_angle_brackets(self) -> None:
        """Angle brackets are escaped for XML."""
        from hypergumbo_tracker.annotations import sanitize_label_text

        assert sanitize_label_text("<script>alert('xss')</script>") == (
            "&lt;script&gt;alert(&apos;xss&apos;)&lt;/script&gt;"
        )

    def test_sanitize_ampersand(self) -> None:
        """Ampersands are escaped for XML."""
        from hypergumbo_tracker.annotations import sanitize_label_text

        assert sanitize_label_text("A & B") == "A &amp; B"

    def test_sanitize_quotes(self) -> None:
        """Quotes are escaped for XML."""
        from hypergumbo_tracker.annotations import sanitize_label_text

        assert sanitize_label_text('He said "hello"') == "He said &quot;hello&quot;"

    def test_sanitize_plain_text_unchanged(self) -> None:
        """Plain text without special characters passes through unchanged."""
        from hypergumbo_tracker.annotations import sanitize_label_text

        assert sanitize_label_text("Bug here") == "Bug here"


class TestScreenshotPath:
    """Tests for screenshot path generation."""

    def test_screenshot_path_format(self) -> None:
        """Screenshot paths follow the ADR-0020 naming convention."""
        from hypergumbo_tracker.annotations import screenshot_path

        ts = datetime(2026, 3, 30, 14, 22, 33)
        path = screenshot_path("INV-foo", ts)
        assert str(path) == ".agent/screenshots/INV-foo-20260330-142233.svg"

    def test_screenshot_path_returns_pathlib_path(self) -> None:
        """screenshot_path returns a Path object."""
        from hypergumbo_tracker.annotations import screenshot_path
        from pathlib import Path

        ts = datetime(2026, 1, 1, 0, 0, 0)
        result = screenshot_path("WI-bar", ts)
        assert isinstance(result, Path)
