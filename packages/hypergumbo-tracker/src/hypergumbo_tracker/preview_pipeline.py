# SPDX-License-Identifier: MPL-2.0
"""SVG→PNG→ANSI rendering pipeline with graceful degradation (ADR-0020).

Renders SVG screenshot files as approximate ANSI-art previews for inline
display in TUI discussion threads.  Degrades gracefully when optional
dependencies (cairosvg, chafa) are unavailable — falls back to a text
placeholder showing the filename.

Pipeline:
    SVG → PNG (cairosvg) → ANSI text (chafa subprocess) → display string

Caching is keyed on (svg_path, mtime, panel_width) to avoid redundant
rasterization.  The cache is invalidated when the SVG is re-annotated
(mtime changes) or the panel width changes significantly.

Both cairosvg and chafa are optional.  The preview feature is a
human-facing display concern — the agent reads SVGs directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Dependency availability checks
# ---------------------------------------------------------------------------

def _try_import_cairosvg() -> bool:
    """Attempt to import cairosvg.  Returns True on success."""
    try:
        import cairosvg  # noqa: F401
        return True
    except ImportError:
        return False


def _try_find_chafa() -> bool:
    """Check if chafa binary is on PATH."""
    return shutil.which("chafa") is not None


def is_cairosvg_available() -> bool:
    """Check if cairosvg is importable."""
    return _try_import_cairosvg()


def is_chafa_available() -> bool:
    """Check if chafa binary is available on PATH."""
    return _try_find_chafa()


# ---------------------------------------------------------------------------
# Collapsed placeholder
# ---------------------------------------------------------------------------

def collapsed_placeholder(svg_path: str) -> str:
    """Generate a collapsed inline preview placeholder.

    Shows the filename with a hint to expand.  Used as the default display
    before the user expands the preview.

    Args:
        svg_path: Path to the SVG file.

    Returns:
        A single-line placeholder string.
    """
    filename = Path(svg_path).name
    return f"[screenshot: {filename} — press Enter to expand]"


# ---------------------------------------------------------------------------
# Preview rendering
# ---------------------------------------------------------------------------

def render_svg_preview(svg_path: str, panel_width: int = 60) -> str:
    """Render an SVG file as ANSI-art text for inline TUI display.

    Falls back to a text placeholder when:
    - The SVG file doesn't exist
    - cairosvg is not installed
    - chafa is not on PATH
    - Rendering fails for any reason

    Args:
        svg_path: Path to the SVG file.
        panel_width: Target width in terminal columns.

    Returns:
        ANSI-art string on success, or a fallback placeholder string.
    """
    filename = Path(svg_path).name

    if not Path(svg_path).is_file():
        return f"[preview unavailable: {filename} not found]"

    if not is_cairosvg_available():
        return f"[preview unavailable: {filename} (cairosvg not installed)]"

    if not is_chafa_available():
        return f"[preview unavailable: {filename} (chafa not found on PATH)]"

    # Both dependencies available — attempt rendering
    try:
        return _render_pipeline(svg_path, panel_width)
    except Exception:  # pragma: no cover — defensive against rendering errors
        return f"[preview unavailable: {filename} (rendering error)]"


def _render_pipeline(svg_path: str, panel_width: int) -> str:
    """Execute the SVG→PNG→ANSI pipeline.

    Args:
        svg_path: Path to the SVG file.
        panel_width: Target width in terminal columns.

    Returns:
        ANSI-art string representation.
    """
    import subprocess  # nosec B404
    import tempfile

    import cairosvg

    # SVG → PNG (in-memory via temp file)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        cairosvg.svg2png(url=svg_path, write_to=tmp.name, output_width=panel_width * 8)

        # PNG → ANSI via chafa
        result = subprocess.run(  # nosec B603, B607  # noqa: S603
            ["chafa", "--size", f"{panel_width}x", tmp.name],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
        )

    if result.returncode != 0:  # pragma: no cover — chafa rarely fails
        filename = Path(svg_path).name
        return f"[preview unavailable: {filename} (chafa error)]"

    return result.stdout


# ---------------------------------------------------------------------------
# Preview cache
# ---------------------------------------------------------------------------

class PreviewCache:
    """Cache for rendered inline previews.

    Keys on (svg_path, mtime, panel_width) — invalidated when any
    component changes.  This is a simple in-memory dict cache with no
    size limit (screenshots are few and previews are small strings).
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, float, int], str] = {}

    def get(self, svg_path: str, mtime: float, panel_width: int) -> str | None:
        """Look up a cached preview.  Returns None on miss."""
        return self._cache.get((svg_path, mtime, panel_width))

    def put(
        self, svg_path: str, mtime: float, panel_width: int, preview: str
    ) -> None:
        """Store a rendered preview in the cache."""
        self._cache[(svg_path, mtime, panel_width)] = preview
