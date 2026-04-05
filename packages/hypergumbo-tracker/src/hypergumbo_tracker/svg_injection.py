# SPDX-License-Identifier: MPL-2.0
"""Cell-to-SVG coordinate mapping and annotation injection (ADR-0020).

Maps Textual cell coordinates to SVG pixel coordinates using Rich's export
constants, generates SVG annotation elements, and injects them into existing
SVG screenshots.

Rich's ``Console.export_svg()`` uses these constants (Rich 13.x):
- ``char_height = 20``
- ``font_aspect_ratio = 0.61`` → ``char_width = 12.2``
- ``line_height = char_height * 1.22 = 24.4``
- ``padding_top = 40`` (title bar height)
- ``padding_left = 8``

The cell-to-SVG transform is:
    svg_x = cell_col * char_width + padding_left
    svg_y = cell_row * line_height + padding_top
"""

from __future__ import annotations

from hypergumbo_tracker.annotations import (
    Annotation,
    ArrowAnnotation,
    RectAnnotation,
    sanitize_label_text,
)

# Rich SVG export constants (Rich 13.x, Textual 0.x+)
_CHAR_HEIGHT = 20.0
_FONT_ASPECT_RATIO = 0.61
_CHAR_WIDTH = _CHAR_HEIGHT * _FONT_ASPECT_RATIO  # 12.2
_LINE_HEIGHT = _CHAR_HEIGHT * 1.22  # 24.4
_PADDING_LEFT = 8.0
_PADDING_TOP = 40.0


def cell_to_svg(cell_col: int, cell_row: int) -> tuple[float, float]:
    """Map a Textual cell coordinate to SVG pixel coordinates.

    Args:
        cell_col: Column (x) in the Textual character grid.
        cell_row: Row (y) in the Textual character grid.

    Returns:
        (svg_x, svg_y) in SVG pixel coordinates.
    """
    svg_x = cell_col * _CHAR_WIDTH + _PADDING_LEFT
    svg_y = cell_row * _LINE_HEIGHT + _PADDING_TOP
    return svg_x, svg_y


def annotation_to_svg_element(annotation: Annotation) -> str:
    """Generate an SVG element string for a single annotation.

    Dispatches on annotation type to produce the appropriate SVG element
    (<rect>, <line>, or <text>).

    Args:
        annotation: A RectAnnotation, ArrowAnnotation, or LabelAnnotation.

    Returns:
        An SVG element string.
    """
    if isinstance(annotation, RectAnnotation):
        x1, y1 = cell_to_svg(annotation.cell_x1, annotation.cell_y1)
        x2, y2 = cell_to_svg(annotation.cell_x2, annotation.cell_y2)
        w = x2 - x1
        h = y2 - y1
        return (
            f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="none" stroke="{annotation.color}" stroke-width="2" '
            f'stroke-dasharray="4,2" rx="3"/>'
        )
    elif isinstance(annotation, ArrowAnnotation):
        x1, y1 = cell_to_svg(annotation.from_x, annotation.from_y)
        x2, y2 = cell_to_svg(annotation.to_x, annotation.to_y)
        return (
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{annotation.color}" stroke-width="2" '
            f'marker-end="url(#arrowhead)"/>'
        )
    else:
        # LabelAnnotation
        x, y = cell_to_svg(annotation.cell_x, annotation.cell_y)
        safe_text = sanitize_label_text(annotation.text)
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" '
            f'fill="{annotation.color}" '
            f'font-family="Fira Code,monospace" font-size="14" '
            f'font-weight="bold">{safe_text}</text>'
        )


_ARROWHEAD_MARKER = (
    '<defs>'
    '<marker id="arrowhead" markerWidth="10" markerHeight="7" '
    'refX="10" refY="3.5" orient="auto">'
    '<polygon points="0 0, 10 3.5, 0 7" fill="#ff3333"/>'
    '</marker>'
    '</defs>'
)


def annotations_to_svg_group(annotations: list[Annotation]) -> str:
    """Generate an SVG ``<g>`` group containing all annotation elements.

    If any arrow annotations are present, includes an arrowhead marker
    definition.

    Args:
        annotations: List of annotation dataclass instances.

    Returns:
        An SVG ``<g class="annotations">...</g>`` string.
    """
    parts = ['<g class="annotations">']

    has_arrows = any(isinstance(a, ArrowAnnotation) for a in annotations)
    if has_arrows:
        parts.append(_ARROWHEAD_MARKER)

    for ann in annotations:
        parts.append(annotation_to_svg_element(ann))

    parts.append("</g>")
    return "\n".join(parts)


def inject_annotations_into_svg(
    svg_content: str,
    annotations: list[Annotation],
) -> str:
    """Inject annotation elements into an existing SVG screenshot.

    Inserts a ``<g class="annotations">`` group immediately before the
    closing ``</svg>`` tag.

    Args:
        svg_content: The original SVG string.
        annotations: Annotations to inject.

    Returns:
        The SVG string with annotations injected.

    Raises:
        ValueError: If the SVG has no closing ``</svg>`` tag.
    """
    close_idx = svg_content.rfind("</svg>")
    if close_idx < 0:
        raise ValueError("No closing </svg> tag found in SVG content")

    group = annotations_to_svg_group(annotations)
    return svg_content[:close_idx] + group + "\n" + svg_content[close_idx:]
