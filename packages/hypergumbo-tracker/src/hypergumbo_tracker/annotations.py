# SPDX-License-Identifier: MPL-2.0
"""Annotation data model for TUI screenshot annotations (ADR-0020).

Provides dataclasses for three annotation types (Rect, Arrow, Label) with
tagged JSON serialization for round-tripping through discussion ops.  Also
provides XML sanitization for label text before SVG injection, and a helper
for generating screenshot paths from item IDs.

Design rationale:
- Discriminated union approach: each annotation kind has its own dataclass
  with fields appropriate to its geometry.  The ``kind`` tag in the serialized
  dict disambiguates during deserialization.
- Coordinates are in Textual cell units (not SVG pixels).  The cell-to-SVG
  mapping is a linear transform applied at injection time (see ADR-0020 §1).
- XML sanitization is a separate function (not baked into LabelAnnotation)
  because it is a rendering concern applied at SVG injection time, not at
  annotation creation time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Annotation dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=True)
class RectAnnotation:
    """A rectangle highlight annotation in cell coordinates."""

    cell_x1: int
    cell_y1: int
    cell_x2: int
    cell_y2: int
    color: str = "#ff3333"

    def to_dict(self) -> dict[str, object]:
        """Serialize to a tagged dict for JSON storage."""
        return {
            "kind": "rect",
            "cell_x1": self.cell_x1,
            "cell_y1": self.cell_y1,
            "cell_x2": self.cell_x2,
            "cell_y2": self.cell_y2,
            "color": self.color,
        }


@dataclass(frozen=True, eq=True)
class ArrowAnnotation:
    """An arrow annotation in cell coordinates."""

    from_x: int
    from_y: int
    to_x: int
    to_y: int
    color: str = "#ff3333"

    def to_dict(self) -> dict[str, object]:
        """Serialize to a tagged dict for JSON storage."""
        return {
            "kind": "arrow",
            "from_x": self.from_x,
            "from_y": self.from_y,
            "to_x": self.to_x,
            "to_y": self.to_y,
            "color": self.color,
        }


@dataclass(frozen=True, eq=True)
class LabelAnnotation:
    """A text label annotation in cell coordinates."""

    cell_x: int
    cell_y: int
    text: str
    color: str = "#ff3333"

    def to_dict(self) -> dict[str, object]:
        """Serialize to a tagged dict for JSON storage."""
        return {
            "kind": "label",
            "cell_x": self.cell_x,
            "cell_y": self.cell_y,
            "text": self.text,
            "color": self.color,
        }


Annotation = RectAnnotation | ArrowAnnotation | LabelAnnotation


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------

_ANNOTATION_KINDS: dict[str, type[Annotation]] = {
    "rect": RectAnnotation,
    "arrow": ArrowAnnotation,
    "label": LabelAnnotation,
}


def annotation_from_dict(data: dict[str, object]) -> Annotation:
    """Deserialize an annotation from a tagged dict.

    The ``kind`` field selects the dataclass; remaining fields are passed
    as keyword arguments.

    Raises:
        ValueError: If the ``kind`` field is missing or unrecognized.
    """
    kind = data.get("kind")
    cls = _ANNOTATION_KINDS.get(kind)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"Unknown annotation kind: {kind!r}")
    fields = {k: v for k, v in data.items() if k != "kind"}
    return cls(**fields)


# ---------------------------------------------------------------------------
# XML sanitization
# ---------------------------------------------------------------------------

def sanitize_label_text(text: str) -> str:
    """Escape XML-significant characters in label text for SVG injection.

    Characters ``<``, ``>``, ``&``, ``"``, ``'`` are replaced with their
    XML entity equivalents.  This prevents malformed SVGs from label text
    containing markup-significant characters (ADR-0020 §1).
    """
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# Screenshot path helper
# ---------------------------------------------------------------------------

def screenshot_path(item_id: str, timestamp: datetime) -> Path:
    """Generate the screenshot file path for a tracker item.

    Follows the ADR-0020 naming convention:
    ``.agent/screenshots/<item-id>-<YYYYMMDD-HHMMSS>.svg``

    Args:
        item_id: The tracker item ID (e.g., "INV-foo").
        timestamp: When the screenshot was taken.

    Returns:
        A Path relative to the repository root.
    """
    ts_str = timestamp.strftime("%Y%m%d-%H%M%S")
    return Path(f".agent/screenshots/{item_id}-{ts_str}.svg")
