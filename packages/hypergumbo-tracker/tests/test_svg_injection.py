# SPDX-License-Identifier: MPL-2.0
"""Tests for cell-to-SVG coordinate mapping and annotation injection (ADR-0020).

Covers:
- Cell coordinate to SVG pixel mapping using Rich's constants
- SVG annotation group generation from annotation lists
- Injection of annotation group into existing SVG content
- Label text sanitization during SVG generation
"""

from __future__ import annotations

import pytest

from hypergumbo_tracker.annotations import (
    ArrowAnnotation,
    LabelAnnotation,
    RectAnnotation,
)


class TestCellToSvgMapping:
    """Tests for cell coordinate to SVG pixel mapping."""

    def test_origin_maps_to_padding(self) -> None:
        """Cell (0, 0) maps to padding offsets."""
        from hypergumbo_tracker.svg_injection import cell_to_svg

        x, y = cell_to_svg(0, 0)
        assert x == 8.0  # padding_left
        assert y == 40.0  # padding_top

    def test_cell_col_maps_with_char_width(self) -> None:
        """Cell column maps via char_width = 20 * 0.61 = 12.2."""
        from hypergumbo_tracker.svg_injection import cell_to_svg

        x, y = cell_to_svg(10, 0)
        assert x == pytest.approx(8.0 + 10 * 12.2)
        assert y == 40.0

    def test_cell_row_maps_with_line_height(self) -> None:
        """Cell row maps via line_height = 20 * 1.22 = 24.4."""
        from hypergumbo_tracker.svg_injection import cell_to_svg

        x, y = cell_to_svg(0, 5)
        assert x == 8.0
        assert y == pytest.approx(40.0 + 5 * 24.4)

    def test_arbitrary_cell(self) -> None:
        """Arbitrary cell maps correctly."""
        from hypergumbo_tracker.svg_injection import cell_to_svg

        x, y = cell_to_svg(3, 7)
        assert x == pytest.approx(8.0 + 3 * 12.2)
        assert y == pytest.approx(40.0 + 7 * 24.4)


class TestAnnotationSvgGeneration:
    """Tests for generating SVG elements from annotations."""

    def test_rect_generates_svg_rect(self) -> None:
        """RectAnnotation produces an SVG <rect> element."""
        from hypergumbo_tracker.svg_injection import annotation_to_svg_element

        rect = RectAnnotation(cell_x1=5, cell_y1=2, cell_x2=20, cell_y2=5)
        svg = annotation_to_svg_element(rect)
        assert "<rect" in svg
        assert 'fill="none"' in svg
        assert 'stroke="#ff3333"' in svg
        assert 'stroke-width="2"' in svg

    def test_arrow_generates_svg_line_with_marker(self) -> None:
        """ArrowAnnotation produces an SVG <line> element."""
        from hypergumbo_tracker.svg_injection import annotation_to_svg_element

        arrow = ArrowAnnotation(from_x=0, from_y=0, to_x=10, to_y=5)
        svg = annotation_to_svg_element(arrow)
        assert "<line" in svg
        assert 'stroke="#ff3333"' in svg
        assert 'marker-end' in svg

    def test_label_generates_svg_text(self) -> None:
        """LabelAnnotation produces an SVG <text> element."""
        from hypergumbo_tracker.svg_injection import annotation_to_svg_element

        label = LabelAnnotation(cell_x=5, cell_y=10, text="Bug here")
        svg = annotation_to_svg_element(label)
        assert "<text" in svg
        assert "Bug here" in svg
        assert 'fill="#ff3333"' in svg

    def test_label_sanitizes_text(self) -> None:
        """LabelAnnotation text is XML-sanitized in SVG output."""
        from hypergumbo_tracker.svg_injection import annotation_to_svg_element

        label = LabelAnnotation(cell_x=0, cell_y=0, text='<script>alert("xss")</script>')
        svg = annotation_to_svg_element(label)
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg


class TestAnnotationGroupGeneration:
    """Tests for generating an SVG annotation group."""

    def test_empty_annotations_returns_empty_group(self) -> None:
        """No annotations produces a minimal group."""
        from hypergumbo_tracker.svg_injection import annotations_to_svg_group

        group = annotations_to_svg_group([])
        assert '<g class="annotations">' in group
        assert "</g>" in group

    def test_group_contains_all_annotations(self) -> None:
        """Group includes SVG for each annotation."""
        from hypergumbo_tracker.svg_injection import annotations_to_svg_group

        annotations = [
            RectAnnotation(cell_x1=0, cell_y1=0, cell_x2=5, cell_y2=5),
            LabelAnnotation(cell_x=2, cell_y=3, text="test"),
        ]
        group = annotations_to_svg_group(annotations)
        assert "<rect" in group
        assert "<text" in group

    def test_group_includes_arrow_marker_def(self) -> None:
        """When arrows are present, group includes arrowhead marker def."""
        from hypergumbo_tracker.svg_injection import annotations_to_svg_group

        annotations = [ArrowAnnotation(from_x=0, from_y=0, to_x=5, to_y=5)]
        group = annotations_to_svg_group(annotations)
        assert "<defs>" in group
        assert "<marker" in group
        assert "arrowhead" in group


class TestSvgInjection:
    """Tests for injecting annotation group into existing SVG."""

    def test_inject_into_svg(self) -> None:
        """Annotation group is inserted before closing </svg> tag."""
        from hypergumbo_tracker.svg_injection import inject_annotations_into_svg

        original_svg = '<svg width="100" height="100"><rect/></svg>'
        annotations = [RectAnnotation(cell_x1=0, cell_y1=0, cell_x2=5, cell_y2=5)]
        result = inject_annotations_into_svg(original_svg, annotations)
        assert '<g class="annotations">' in result
        assert result.endswith("</svg>")
        assert result.index('<g class="annotations">') < result.index("</svg>")

    def test_inject_preserves_original_content(self) -> None:
        """Original SVG content is preserved."""
        from hypergumbo_tracker.svg_injection import inject_annotations_into_svg

        original_svg = '<svg><rect id="original"/></svg>'
        result = inject_annotations_into_svg(original_svg, [])
        assert '<rect id="original"/>' in result

    def test_inject_no_closing_svg_raises(self) -> None:
        """SVG without closing tag raises ValueError."""
        from hypergumbo_tracker.svg_injection import inject_annotations_into_svg

        with pytest.raises(ValueError, match=r"No closing </svg>"):
            inject_annotations_into_svg("<svg><rect/>", [])
