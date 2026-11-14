# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/sketch.py
"""The semantic lines of the sketch Configuration section keep their file.

The section's hybrid extractor adds lines an embedding scorer picked from
each manifest under a ``[path]`` heading, minus the lines that repeat what
the heuristic lines above already say. That test ran on every line alone.
A heading repeats the package name and the manifest name, so headings were
dropped and their quotes kept: quoted lines with no file named, and the
blank separators of dropped blocks left in a pile at the end.
"""

from __future__ import annotations

from hypergumbo_core.sketch import _new_semantic_blocks


SEMANTIC = [
    "[packages/widget-core/pyproject.toml]",
    '  > [project] name = "widget-core" version = "9.2.0"',   # already said
    '  > [tool.ruff] target-version = "py310"',
    "",
    "[packages/widget/pyproject.toml]",
    '  > name = "widget" version = "9.2.0" license = "MIT"',  # already said
    "",
    "",
    "[packages/cli/pyproject.toml]",
    '  > [project.scripts] tool = "cli:main"',
    "",
]
# The words of the heuristic lines above the block, as the extractor takes
# them: "packages/widget-core/pyproject.toml: name: widget-core; version: ...".
KNOWN = {"packages/widget-core/pyproject.toml", "packages/widget/pyproject.toml",
         "widget-core", "widget", "name", "version", "9.2.0", "license"}


def test_the_semantic_block_keeps_a_heading_with_its_quotes() -> None:
    """The defect: each line was tested alone, so every heading (which
    repeats the package name and the manifest name) was dropped, its quotes
    stayed with no file named, and the separators of dropped blocks piled up."""
    kept = _new_semantic_blocks(SEMANTIC, KNOWN)
    assert kept == [
        "[packages/widget-core/pyproject.toml]",
        '  > [tool.ruff] target-version = "py310"',
        "",
        "[packages/cli/pyproject.toml]",
        '  > [project.scripts] tool = "cli:main"',
    ]
    # Reach: the first heading is itself "redundant" by the per-line test.
    assert sum(1 for term in KNOWN if term in SEMANTIC[0].lower()) > 2


def test_semantic_blocks_without_headings_or_content() -> None:
    assert _new_semantic_blocks([], KNOWN) == []
    assert _new_semantic_blocks(["", ""], KNOWN) == []
    assert _new_semantic_blocks(["  > a line with no heading"], set()) == [
        "  > a line with no heading"]
    assert _new_semantic_blocks(["[only/a/heading]"], set()) == []
