# SPDX-License-Identifier: MPL-2.0
"""Screenshot save and auto-create tracker item (ADR-0020, WI-rujoz).

Saves annotated SVG screenshots to ``.agent/tracker-workspace/screenshots/``
and auto-creates a ``work_item`` with ``needs_human_review`` status.

How It Works
------------
``save_screenshot(ts, svg_content)`` writes the SVG to the screenshots
directory with a timestamped filename. ``save_screenshot_with_item(ts, svg)``
does the same but also creates a tracker item referencing the file.

Why This Design
---------------
- Screenshots go to ``tracker-workspace/`` (not ``.agent/screenshots/``) so
  they inherit the same group ownership (project-dev, 2775) as ops files.
- Auto-created items use ``needs_human_review`` because the screenshot may be
  about any topic — the human decides what to do with it.
- The SVG path is embedded in the item description for discoverability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypergumbo_tracker.trackerset import TrackerSet


def _screenshots_dir(ts: TrackerSet) -> Path:
    """Return the screenshots directory inside tracker-workspace."""
    return ts._tracker_root / "tracker-workspace" / "screenshots"


def save_screenshot(ts: TrackerSet, svg_content: str) -> Path:
    """Save an SVG screenshot to the tracker-workspace screenshots directory.

    Args:
        ts: TrackerSet instance (used for tracker root path).
        svg_content: The SVG content to save.

    Returns:
        Path to the saved SVG file.
    """
    screenshots = _screenshots_dir(ts)
    screenshots.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    filename = f"screenshot-{timestamp}.svg"
    path = screenshots / filename
    path.write_text(svg_content)
    return path


def save_screenshot_with_item(
    ts: TrackerSet,
    svg_content: str,
) -> tuple[Path, str]:
    """Save a screenshot and auto-create a tracker work item.

    The item is created with ``needs_human_review`` status in the workspace
    tier. The description references the saved SVG path.

    Args:
        ts: TrackerSet instance.
        svg_content: The SVG content to save.

    Returns:
        Tuple of (path_to_svg, item_id).
    """
    from hypergumbo_tracker.models import Tier

    path = save_screenshot(ts, svg_content)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    item_id = ts.add(
        kind="work_item",
        title=f"Screenshot {timestamp}",
        tier=Tier.WORKSPACE,
        status="needs_human_review",
        description=f"Auto-created from TUI screenshot annotation.\nFile: {path}",
    )

    return path, item_id
