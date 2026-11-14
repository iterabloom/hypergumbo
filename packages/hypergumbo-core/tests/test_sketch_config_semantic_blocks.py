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

from pathlib import Path

import pytest

from hypergumbo_core import sketch
from hypergumbo_core.sketch import (
    ConfigExtractionMode,
    _extract_config_info,
    _is_config_heading,
    _new_semantic_blocks,
)


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


HYBRID_LINES = [
    "go.mod: module: example.com/m; go: 1.22",
    "LICENSE: Apache",
    "",
    "--- Additional context (semantic) ---",
    "[Dockerfile]",
    '  > ARG ARCH="amd64"',
    "",
    "[ui/app/Makefile]",
    "  > .PHONY: build",
    "  > .PHONY: lint",
    "",
    "[ui/app/elm.json]",
    '  > "type": "application"',
]


def test_the_section_text_has_one_blank_line_between_files(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lines are rebuilt once more before they are printed, a file at a
    time, so each gets a fair share of the characters. That pass added its
    own separator after blocks that still carried theirs (a doubled blank
    line in 8 of 17 sketches measured), and took only a name with a dot in
    it for a heading, so ``[Dockerfile]`` and ``[Makefile]`` were folded
    into the file above them."""
    monkeypatch.setattr(sketch, "_extract_config_hybrid", lambda *_a, **_k: list(HYBRID_LINES))
    text = _extract_config_info(tmp_path, mode=ConfigExtractionMode.HYBRID)
    assert "\n\n\n" not in text and not text.endswith("\n")
    assert text.split("\n") == [
        "go.mod: module: example.com/m; go: 1.22",
        "LICENSE: Apache",
        "",
        "--- Additional context (semantic) ---",
        "",
        "[Dockerfile]",
        '  > ARG ARCH="amd64"',
        "",
        "[ui/app/Makefile]",
        "  > .PHONY: build",
        "  > .PHONY: lint",
        "",
        "[ui/app/elm.json]",
        '  > "type": "application"',
    ]


def test_a_file_without_a_dot_in_its_name_gets_its_own_share(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With little room, every file keeps its heading: three sections, not
    two with the Dockerfile's lines counted against the file above."""
    monkeypatch.setattr(sketch, "_extract_config_hybrid", lambda *_a, **_k: list(HYBRID_LINES))
    text = _extract_config_info(tmp_path, max_chars=260, mode=ConfigExtractionMode.HYBRID)
    for heading in ("[Dockerfile]", "[ui/app/Makefile]", "[ui/app/elm.json]"):
        assert heading in text.split("\n")


@pytest.mark.parametrize(("line", "heading"), [
    ("[Dockerfile]", True), ("[packages/core/pyproject.toml]", True), ("  [Makefile]  ", True),
    ('  > [tool.ruff] target-version = "py310"', False), ("[]", False), ("", False),
    ("go.mod: module: example.com/m", False), ("[unclosed", False),
])
def test_is_config_heading(line: str, heading: bool) -> None:
    assert _is_config_heading(line) is heading
