# SPDX-License-Identifier: MPL-2.0
"""SVG path detection in discussion messages (ADR-0020).

Scans discussion message text for file paths ending in ``.svg``,
optionally checks existence on disk, and returns the paths for
inline preview rendering.

The detection uses a simple regex that matches path-like substrings
ending in ``.svg``.  It does not attempt to resolve relative paths or
handle URL-encoded characters — discussion messages use plain file paths.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches file paths ending in .svg: sequences of path characters
# (alphanumeric, hyphens, underscores, dots, slashes) ending with .svg.
# Requires at least one slash or dot-prefix to distinguish from bare words.
_SVG_PATH_RE = re.compile(
    r'(?<!\w)'                    # not preceded by a word char
    r'((?:[\w./-]+/)?'           # optional directory prefix (must have /)
    r'[\w.-]+\.svg)'             # filename ending in .svg
    r'(?!\w)'                     # not followed by a word char
)


def extract_svg_paths(text: str) -> list[str]:
    """Extract file paths ending in .svg from message text.

    Returns paths in the order they appear.  Paths must contain at least
    one directory separator (``/``) or start with ``.`` to avoid matching
    bare words like "foo.svg" in a sentence about SVG files.

    Args:
        text: The discussion message text.

    Returns:
        List of SVG file path strings found in the text.
    """
    matches = _SVG_PATH_RE.findall(text)
    # Filter: must contain '/' to be a real path (not just "file.svg")
    return [m for m in matches if "/" in m or m.startswith(".")]


def filter_existing_svg_paths(paths: list[str]) -> list[str]:
    """Filter SVG paths to those that exist on the filesystem.

    Args:
        paths: List of SVG file path strings.

    Returns:
        Subset of paths that exist as files.
    """
    return [p for p in paths if Path(p).is_file()]


def find_svg_paths_in_message(text: str) -> list[str]:
    """Extract SVG paths from text and filter to existing files.

    Convenience function combining extraction and existence check.

    Args:
        text: The discussion message text.

    Returns:
        List of SVG file paths found in text that exist on disk.
    """
    paths = extract_svg_paths(text)
    return filter_existing_svg_paths(paths)
