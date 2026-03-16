# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blade template analyzer using regex patterns.

Laravel Blade is a templating engine for PHP. Blade files (.blade.php) mix
HTML with directives like @section, @yield, @component, @extends, and
control flow (@if, @foreach, @while).

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .blade.php files
2. Extract @section/@endsection blocks as symbols
3. Extract @component/@extends directives as edges
4. Extract @yield/@slot directives as symbols

Symbols Extracted
-----------------
- **section**: Named content sections (@section('name'))
- **component**: Component references (@component('name'))
- **yield**: Yield points for layout inheritance (@yield('name'))
- **extends**: Parent layout reference (@extends('name'))

Why This Design
---------------
- Blade is the default templating engine for Laravel (PHP)
- Section/yield patterns reveal template inheritance hierarchy
- Component references show composition relationships
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("blade")

# Blade directive patterns
_SECTION_RE = re.compile(r"""@section\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_YIELD_RE = re.compile(r"""@yield\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_EXTENDS_RE = re.compile(r"""@extends\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_COMPONENT_RE = re.compile(r"""@component\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"blade:{path}:{line}:{name}:{kind}"


def find_blade_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Blade template files."""
    yield from find_files(repo_root, ["*.blade.php"], max_files=max_files)


@register_analyzer("blade", supports_max_files=True)
def analyze_blade(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze Blade template files for sections, components, and layout inheritance."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for blade_file in find_blade_files(repo_root, max_files=max_files):
        try:
            content = blade_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(blade_file)

        for line_num, line in enumerate(content.splitlines(), 1):
            for match in _SECTION_RE.finditer(line):
                name = match.group(1)
                sym = Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "section"),
                    name=name,
                    kind="section",
                    language="blade",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                )
                symbols.append(sym)

            for match in _YIELD_RE.finditer(line):
                name = match.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "yield"),
                    name=name,
                    kind="yield",
                    language="blade",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

            for match in _EXTENDS_RE.finditer(line):
                name = match.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "extends"),
                    name=name,
                    kind="extends",
                    language="blade",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

            for match in _COMPONENT_RE.finditer(line):
                name = match.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "component"),
                    name=name,
                    kind="component",
                    language="blade",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

    return AnalysisResult(symbols=symbols, edges=edges, usage_contexts=[])
