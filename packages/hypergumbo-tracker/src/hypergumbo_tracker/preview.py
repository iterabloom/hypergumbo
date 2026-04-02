# SPDX-License-Identifier: MPL-2.0
"""Inline SVG preview for TUI discussion threads (ADR-0020 Part 2).

Renders approximate inline previews of SVG screenshots referenced in
discussion messages.  The pipeline is:

    SVG → PNG (cairosvg) → ANSI text (Chafa) → Rich Text

Both ``cairosvg`` (pip) and ``chafa`` (system binary) are optional.
When either is missing, the module degrades gracefully — callers get
a fallback message instead of a preview.

Caching: ANSI output is cached keyed on ``(svg_path, mtime, width)``.
The cache is an in-memory dict (not persisted) — rebuilds are cheap
since rasterization happens once per unique (file, size) combination.

Why a separate module: preview rendering is a pure display concern with
no tracker domain logic.  Keeping it isolated simplifies testing (mock
the subprocess/import) and avoids polluting the TUI module with
optional-dependency handling.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# In-memory cache: (path_str, mtime_ns, width) → ANSI string
_cache: dict[tuple[str, int, int], str] = {}


def _cairosvg_available() -> bool:
    """Check if cairosvg is importable."""
    try:
        import cairosvg  # noqa: F401
        return True
    except ImportError:
        return False


def _chafa_available() -> bool:
    """Check if the chafa binary is on PATH."""
    return shutil.which("chafa") is not None


def _svg_to_png(svg_path: Path, width_columns: int) -> bytes | None:
    """Convert SVG to PNG bytes via cairosvg."""
    import cairosvg
    return cairosvg.svg2png(
        url=str(svg_path),
        output_width=width_columns * 8,
    )


def render_svg_preview(
    svg_path: Path,
    width_columns: int = 60,
) -> str | None:
    """Render an SVG file as ANSI terminal art.

    Returns the ANSI string on success, or None if the pipeline is
    unavailable (missing cairosvg or chafa).

    Args:
        svg_path: Path to the SVG file.
        width_columns: Target width in terminal columns.

    Returns:
        ANSI-encoded string suitable for ``Text.from_ansi()``, or None.
    """
    if not svg_path.is_file():
        return None

    if not _cairosvg_available() or not _chafa_available():
        return None

    # Check cache
    try:
        mtime_ns = svg_path.stat().st_mtime_ns
    except OSError:
        return None

    cache_key = (str(svg_path), mtime_ns, width_columns)
    if cache_key in _cache:
        return _cache[cache_key]

    # Step 1: SVG → PNG via cairosvg
    try:
        png_data = _svg_to_png(svg_path, width_columns)
    except Exception:
        return None
    if png_data is None:
        return None

    # Step 2: PNG → ANSI via chafa
    try:
        chafa_path = shutil.which("chafa")
        if chafa_path is None:
            return None  # pragma: no cover — checked by _chafa_available
        result = subprocess.run(  # noqa: S603 — chafa_path from shutil.which
            [
                chafa_path,
                "--size", f"{width_columns}x",
                "--format", "symbols",
                "-",
            ],
            input=png_data,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None  # pragma: no cover
        ansi_text = result.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    _cache[cache_key] = ansi_text
    return ansi_text


def clear_cache() -> None:
    """Clear the preview cache (e.g., on panel resize)."""
    _cache.clear()


# Pattern to detect .svg file paths in discussion text
_SVG_PATH_RE = re.compile(
    r"(?:^|\s)(\.agent/screenshots/\S+\.svg|/\S+\.svg)(?:\s|$)",
)


def extract_svg_paths(text: str) -> list[str]:
    """Extract SVG file paths from discussion message text.

    Matches paths ending in ``.svg`` that start with
    ``.agent/screenshots/`` or an absolute path.

    Args:
        text: The discussion message text.

    Returns:
        List of matched SVG path strings.
    """
    return [m.group(1) for m in _SVG_PATH_RE.finditer(text)]


def format_preview_placeholder(svg_path: str) -> str:
    """Format a collapsed preview placeholder line.

    Shows as: [screenshot: filename.svg — press Enter to expand]
    """
    name = Path(svg_path).name
    return f"[screenshot: {name}]"


def format_unavailable_notice(svg_path: str) -> str:
    """Format a notice when preview dependencies are missing."""
    name = Path(svg_path).name
    return f"[screenshot: {name} — preview unavailable (install cairosvg + chafa)]"
