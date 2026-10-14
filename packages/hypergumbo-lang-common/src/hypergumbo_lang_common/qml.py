# SPDX-License-Identifier: AGPL-3.0-or-later
"""QML (Qt Modeling Language) analyzer using regex patterns.

QML is a declarative UI language used with Qt. Files (.qml) define
UI components with properties, signals, functions, and nested elements.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .qml files
2. Scan each file once, line by line. The first pattern that matches a line
   wins, in this order: component definition (root element type), property
   declaration, signal declaration, JavaScript function, id declaration

Symbols Extracted
-----------------
- **component**: Root QML type (Rectangle, Item, ApplicationWindow)
- **property**: Property declarations (property int width: 100)
- **signal**: Signal declarations (signal clicked(int x, int y))
- **function**: JavaScript functions (function doSomething() {})
- **id**: Element identifiers (id: myButton)

Identity (WI-tusad)
-------------------
Every symbol mints the ADR-0035 producer stable_id with
``overload_arity.declared_arity_stable_id`` (no arity: a QML object holds one
function, signal or property of a name), qualified by the chain of object
declarations enclosing it. The scan tracks that chain by brace depth: a line
opening ``Type {`` (or ``prop: Type {``) pushes an element, labelled ``Type``
(``prop:Type`` for a property-valued object), with ``#id`` appended once its
``id:`` line is seen; the closing brace pops it. So
``run`` in the root ``Item`` and ``run`` in a nested ``Rectangle`` hash as
``Item.run`` and ``Item.Rectangle.run``. Before, nothing computed a stable_id
and every symbol rode the backstop floor key (kind, file, bare name), which
those two share, so both were null. Known gaps: braces inside a multi-line
``/* */`` comment or template literal unbalance the count, and two sibling
elements of one type without ids tie (the per-file split pass numbers them).

Why This Design
---------------
- QML is the primary UI language for Qt/KDE applications
- Component hierarchy reveals UI structure
- Properties and signals define the component interface
- Functions contain business logic embedded in UI
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import declared_arity_stable_id

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


# An object declaration opening on this line: ``Type {`` or ``prop: Type {``.
_ELEMENT_RE = re.compile(r"^\s*(?:([\w.]+)\s*:\s*)?([A-Z]\w*(?:\.\w+)*)\s*\{")
# String literals and a trailing ``//`` comment, blanked before braces are counted.
_NOT_CODE_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|/\*.*?\*/|//.*$")


class _LinePattern(NamedTuple):
    """A member line pattern and the kind it declares."""

    kind: str
    regex: "re.Pattern[str]"


# Tried in order after the component pattern; the first match wins. Spelled
# ``kind="..."`` so a scan for the kinds a module emits finds them.
_MEMBER_PATTERNS = (
    _LinePattern(kind="property", regex=_PROPERTY_RE),
    _LinePattern(kind="signal", regex=_SIGNAL_RE),
    _LinePattern(kind="function", regex=_FUNCTION_RE),
    _LinePattern(kind="id", regex=_ID_RE),
)


@dataclass
class _Element:
    """One open object declaration of the scan (WI-tusad scope chain)."""

    type_name: str
    prop: Optional[str]
    open_depth: int
    element_id: Optional[str] = None

    @property
    def label(self) -> str:
        base = f"{self.prop}:{self.type_name}" if self.prop else self.type_name
        return f"{base}#{self.element_id}" if self.element_id else base


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"qml:{path}:{line}-{line}:{name}:{kind}"


def find_qml_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all QML files."""
    yield from find_files(repo_root, ["*.qml"], max_files=max_files)


def _match_symbol(line: str) -> Optional[tuple[str, str]]:
    """``(kind, name)`` of the symbol a line declares, or ``None``.

    The first pattern that matches wins: component (root element type),
    property, signal, JavaScript function, id.
    """
    m = _COMPONENT_RE.match(line)
    if m:
        # Skip import statements that happen to match
        return ("component", m.group(1)) if m.group(1) != "import" else None
    for candidate in _MEMBER_PATTERNS:
        m = candidate.regex.match(line)
        if m:
            return candidate.kind, m.group(1)
    return None


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
        rel_path = str(qml_file.relative_to(repo_root))
        # (symbol, the elements enclosing it); ids are minted once the file is
        # scanned, because an element's ``id:`` line may follow its members.
        scoped: list[tuple[Symbol, tuple[_Element, ...]]] = []
        open_elements: list[_Element] = []
        depth = 0

        for line_num, line in enumerate(content.splitlines(), 1):
            match = _match_symbol(line)
            if match is not None:
                kind, name = match
                if kind == "id" and open_elements:
                    open_elements[-1].element_id = name
                scoped.append((Symbol(
                    id=_make_symbol_id(path_str, line_num, name, kind),
                    name=name,
                    kind=kind,
                    language="qml",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ), tuple(open_elements)))
            element = _ELEMENT_RE.match(line)
            if element is not None:
                open_elements.append(_Element(element.group(2), element.group(1), depth))
            code = _NOT_CODE_RE.sub("", line)
            depth += code.count("{") - code.count("}")
            while open_elements and depth <= open_elements[-1].open_depth:
                open_elements.pop()

        for sym, chain in scoped:
            sym.stable_id = declared_arity_stable_id(
                kind=sym.kind, lang="qml", path=rel_path, name=sym.name,
                qualified_name=".".join((*(e.label for e in chain), sym.name)),
                arity=None,
            )
            symbols.append(sym)

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
