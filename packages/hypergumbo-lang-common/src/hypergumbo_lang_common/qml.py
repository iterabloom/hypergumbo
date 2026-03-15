# SPDX-License-Identifier: AGPL-3.0-or-later
"""QML (Qt Modeling Language) analyzer using regex patterns.

QML is a declarative UI language used with Qt. Files (.qml) define
UI components with properties, signals, functions, and nested elements.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .qml files
2. Extract component definitions (root element type)
3. Extract property declarations
4. Extract signal declarations
5. Extract JavaScript function definitions
6. Extract id declarations

Symbols Extracted
-----------------
- **component**: Root QML type (Rectangle, Item, ApplicationWindow)
- **property**: Property declarations (property int width: 100)
- **signal**: Signal declarations (signal clicked(int x, int y))
- **function**: JavaScript functions (function doSomething() {})
- **id**: Element identifiers (id: myButton)

Why This Design
---------------
- QML is the primary UI language for Qt/KDE applications
- Component hierarchy reveals UI structure
- Properties and signals define the component interface
- Functions contain business logic embedded in UI
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("qml")

# Root component: starts at col 0, capitalized word followed by {
_COMPONENT_RE = re.compile(r"^([A-Z]\w+(?:\.\w+)*)\s*\{", re.MULTILINE)
# property [type] name: value  or  property alias name: ref
_PROPERTY_RE = re.compile(
    r"^\s+(?:default\s+|required\s+|readonly\s+)*property\s+"
    r"(?:alias|var|bool|int|real|double|string|url|color|date|point|rect|size"
    r"|font|vector[234]d|quaternion|matrix4x4|list|[\w.]+)\s+"
    r"(\w+)",
    re.MULTILINE,
)
# signal name(params)
_SIGNAL_RE = re.compile(r"^\s+signal\s+(\w+)", re.MULTILINE)
# function name(params) { or function name(params) : type {
_FUNCTION_RE = re.compile(r"^\s+function\s+(\w+)\s*\(", re.MULTILINE)
# id: name
_ID_RE = re.compile(r"^\s+id\s*:\s*(\w+)", re.MULTILINE)


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"qml:{path}:{line}:{name}:{kind}"


def find_qml_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all QML files."""
    yield from find_files(repo_root, ["*.qml"], max_files=max_files)


@register_analyzer("qml", supports_max_files=True)
def analyze_qml(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze QML files for components, properties, signals, and functions."""
    symbols: list[Symbol] = []

    for qml_file in find_qml_files(repo_root, max_files=max_files):
        try:
            content = qml_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(qml_file)

        for line_num, line in enumerate(content.splitlines(), 1):
            m = _COMPONENT_RE.match(line)
            if m:
                name = m.group(1)
                # Skip import statements that happen to match
                if name != "import":
                    symbols.append(Symbol(
                        id=_make_symbol_id(path_str, line_num, name, "component"),
                        name=name,
                        kind="component",
                        language="qml",
                        path=path_str,
                        span=Span(start_line=line_num, end_line=line_num,
                                  start_col=0, end_col=len(line)),
                        origin=PASS_ID,
                        origin_run_id="",
                    ))
                continue

            m = _PROPERTY_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "property"),
                    name=name,
                    kind="property",
                    language="qml",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _SIGNAL_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "signal"),
                    name=name,
                    kind="signal",
                    language="qml",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _FUNCTION_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "function"),
                    name=name,
                    kind="function",
                    language="qml",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _ID_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "id"),
                    name=name,
                    kind="id",
                    language="qml",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
