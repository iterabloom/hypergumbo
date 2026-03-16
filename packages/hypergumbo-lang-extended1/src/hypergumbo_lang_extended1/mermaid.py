# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mermaid diagram analyzer using regex patterns.

Mermaid is a diagramming language that renders diagrams from text definitions.
Files (.mmd, .mermaid) define flowcharts, sequence diagrams, class diagrams,
state diagrams, and more.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .mmd and .mermaid files
2. Detect diagram type from first directive (flowchart, sequenceDiagram, etc.)
3. Extract node definitions and named entities
4. Extract relationships/edges between nodes

Symbols Extracted
-----------------
- **diagram**: The diagram type declaration (flowchart, classDiagram, etc.)
- **node**: Named nodes in flowcharts and graphs (A[Label])
- **participant**: Sequence diagram participants

Why This Design
---------------
- Mermaid is widely used in documentation (GitHub, GitLab render it natively)
- Diagram types reveal project documentation patterns
- Node extraction helps index documented architecture
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("mermaid")

# Diagram type declarations
_DIAGRAM_TYPE_RE = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram"
    r"|erDiagram|gantt|pie|gitGraph|journey|mindmap"
    r"|timeline|quadrantChart|sankey|xychart|block|packet"
    r"|kanban|architecture)\b",
    re.MULTILINE,
)

# Node definitions in flowcharts: A[Label], B(Label), C{Label}, D((Label))
_NODE_RE = re.compile(r"^\s*(\w+)\s*[\[\(\{>]", re.MULTILINE)
# Sequence diagram participants: participant Name or actor Name
_PARTICIPANT_RE = re.compile(r"^\s*(?:participant|actor)\s+(\w+)", re.MULTILINE)
# Class diagram class definitions: class ClassName
_CLASS_RE = re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)
# State diagram states: state "Description" as alias
_STATE_RE = re.compile(r"""^\s*state\s+["']([^"']+)["']\s+as\s+(\w+)""", re.MULTILINE)


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"mermaid:{path}:{line}:{name}:{kind}"


def find_mermaid_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Mermaid diagram files."""
    yield from find_files(
        repo_root, ["*.mmd", "*.mermaid"], max_files=max_files
    )


@register_analyzer("mermaid", supports_max_files=True)
def analyze_mermaid(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze Mermaid diagram files for diagram types and node definitions."""
    symbols: list[Symbol] = []

    for mmd_file in find_mermaid_files(repo_root, max_files=max_files):
        try:
            content = mmd_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(mmd_file)

        for line_num, line in enumerate(content.splitlines(), 1):
            m = _DIAGRAM_TYPE_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "diagram"),
                    name=name,
                    kind="diagram",
                    language="mermaid",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _PARTICIPANT_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "participant"),
                    name=name,
                    kind="participant",
                    language="mermaid",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _CLASS_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "class"),
                    name=name,
                    kind="class",
                    language="mermaid",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _STATE_RE.match(line)
            if m:
                name = m.group(2)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "state"),
                    name=name,
                    kind="state",
                    language="mermaid",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _NODE_RE.match(line)
            if m:
                name = m.group(1)
                # Skip common keywords
                if name not in {"end", "style", "click", "linkStyle",
                                "classDef", "class", "subgraph", "direction"}:
                    symbols.append(Symbol(
                        id=_make_symbol_id(path_str, line_num, name, "node"),
                        name=name,
                        kind="node",
                        language="mermaid",
                        path=path_str,
                        span=Span(start_line=line_num, end_line=line_num,
                                  start_col=0, end_col=len(line)),
                        origin=PASS_ID,
                        origin_run_id="",
                    ))

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
