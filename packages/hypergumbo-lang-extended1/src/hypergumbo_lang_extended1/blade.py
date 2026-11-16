# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blade template analyzer using regex patterns.

Laravel Blade is a templating engine for PHP. Blade files (.blade.php) mix
HTML with directives like @section, @yield, @component, @extends, and
control flow (@if, @foreach, @while).

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .blade.php files
2. Mark the text Blade does not compile as directives (:func:`_uncompiled`):
   ``{{-- comments --}}``, ``@verbatim ... @endverbatim``,
   ``@php ... @endphp`` and ``<?php ... ?>``. A region whose end never
   comes is not one (Blade leaves an unclosed ``{{--`` or ``@verbatim`` as
   text and compiles what follows), so it holds nothing back
3. Walk the directives of the whole file in source order (``@name`` not
   after a word character or a second ``@``: ``@@yield`` is Blade's escape,
   ``x@yield`` an e-mail address), reading each argument list to its
   balanced closing parenthesis, across lines and past nested brackets and
   quoted strings (:func:`_arguments`), and tracking the block sections
   and components open around each directive
4. Extract @section('name'), @component and @yield directives as symbols
5. Extract @extends directives as edges

Symbols Extracted
-----------------
- **section**: Named content sections (@section('name'))
- **component**: Component references (@component('name'), with or
  without a data argument)
- **yield**: Yield points for layout inheritance (@yield('name'), with or
  without a default)

Edges Extracted
---------------
- **extends**: layout inheritance — emitted from @extends('name'), with or
  without a data argument.
  Per ADR-0027 Cluster E sub-case (b) (audit-findings 0010), the per-@extends
  Symbol kind was dropped in favour of this companion Edge so the
  layout-inheritance relationship keeps representation in the graph. ``src``
  is the blade file id; ``dst`` is a 6-part dangling id naming the parent
  template (the local Blade scan does not enumerate layouts).

Why This Design
---------------
- Blade is the default templating engine for Laravel (PHP)
- Section/yield patterns reveal template inheritance hierarchy
- Component references show composition relationships
- Identity (ADR-0035, WI-tusad): sections, yields and components mint
  ``overload_arity.scoped_stable_id`` over their name, scoped by the block
  sections and components open around them (INV-punig, scheme v9). A
  directive's first argument names it when it is a quoted string; the
  arguments after it (a ``@yield`` default, ``@component`` or ``@extends``
  data) do not enter the id. A section opened by a one-argument
  ``@section(...)`` (``@section('t', 'v')`` is inline: it is not emitted
  and opens nothing) closes at ``@endsection``, ``@stop``, ``@show``,
  ``@overwrite`` or ``@append``, a ``@component`` at ``@endcomponent``. A
  section or component whose name is not a literal (``@component($name)``)
  emits nothing and adds no link to the chain, but is still open, so its
  closer ends it and not the block around it. A closer with no open block
  of its kind is ignored, and a block never closed stays open to the end
  of the file. ``@yield('inner')`` in ``@section('a')`` and in
  ``@section('b')`` are two identities, and deleting the first section
  moves nothing. The same name twice in one block is a same-scope tie,
  which the per-file split pass orders. Control-flow directives (``@if``,
  ``@foreach``, ...), ``@slot``, ``@push`` and ``@once`` are not scopes
"""
from __future__ import annotations

import bisect
import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, make_file_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.paths import to_relative_path

PASS_ID = make_pass_id("blade")

# A directive: @name, not after a word character or a second @ (@@ escapes).
_DIRECTIVE_RE = re.compile(r"(?<![\w@])@(\w+)")
# The opening parenthesis of a directive's arguments, on the directive's line
_ARGS_OPEN_RE = re.compile(r"[ \t]*\(")
# A first argument that names the directive: one quoted string
_NAME_ARG_RE = re.compile(r"""\s*['"]([^'"]+)['"]\s*""")
# Text Blade does not compile as directives, by its opening marker. An
# @php or @verbatim opener is a whole word; ``@php(...)`` is the inline form.
_UNCOMPILED_RE = re.compile(
    r"\{\{--|(?<![\w@])@verbatim\b|(?<![\w@])@php\b(?![ \t]*\()|<\?(?:php\b|=)")
_UNCOMPILED_END = {"{{--": "--}}", "@verbatim": "@endverbatim", "@php": "@endphp"}
#: The directives that name a symbol or an edge
_NAMED = frozenset({"section", "yield", "extends", "component"})
#: Which open block each closer ends.
_CLOSES = {"endcomponent": "component", "endsection": "section", "stop": "section",
           "show": "section", "overwrite": "section", "append": "section"}
_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _uncompiled(content: str) -> list[tuple[int, int]]:
    """The ``[start, end)`` regions of ``content`` Blade compiles no
    directive in, in order. A region whose end marker never comes is none."""
    regions: list[tuple[int, int]] = []
    pos = 0
    while True:
        m = _UNCOMPILED_RE.search(content, pos)
        if m is None:
            return regions
        end_marker = _UNCOMPILED_END.get(m.group(0), "?>")
        end = content.find(end_marker, m.end())
        if end < 0:
            pos = m.end()
            continue
        regions.append((m.start(), end + len(end_marker)))
        pos = end + len(end_marker)


def _arguments(content: str, open_at: int) -> tuple[list[str], int] | None:
    """The top-level comma-separated arguments of the list whose ``(`` is
    at ``open_at``, and the offset after its ``)``; None when it never
    closes. Quoted strings and nested brackets do not end or split it."""
    args: list[str] = []
    depth = 0
    start = open_at + 1
    i = start
    while i < len(content):
        ch = content[i]
        if ch in "'\"":
            close = i + 1
            while close < len(content) and content[close] != ch:
                close += 2 if content[close] == "\\" else 1
            i = close
        elif ch in _PAIRS:
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                if ch == ")":
                    args.append(content[start:i])
                    return args, i + 1
            else:
                depth -= 1
        elif ch == "," and depth == 0:
            args.append(content[start:i])
            start = i + 1
        i += 1
    return None


def _directives(content: str) -> Iterator[tuple[str, list[str] | None, int, int]]:
    """``(name, arguments or None, start, end)`` for each named directive and
    block closer outside the uncompiled regions, in source order.

    Every directive's argument list is consumed whole, as Blade consumes it,
    so an ``@yield`` inside another directive's arguments (a string in
    ``@php($x = '@yield(...)')``) is not a directive. A named directive
    whose argument list never closes is none."""
    regions = _uncompiled(content)
    starts = [start for start, _ in regions]
    pos = 0
    while True:
        m = _DIRECTIVE_RE.search(content, pos)
        if m is None:
            return
        index = bisect.bisect_right(starts, m.start()) - 1
        if index >= 0 and m.start() < regions[index][1]:
            pos = regions[index][1]
            continue
        pos = m.end()
        paren = _ARGS_OPEN_RE.match(content, m.end())
        parsed = _arguments(content, paren.end() - 1) if paren else None
        if parsed is not None:
            pos = parsed[1]
        name = m.group(1)
        if name in _CLOSES:
            yield name, None, m.start(), m.end()
        elif name in _NAMED and parsed is not None:
            yield name, parsed[0], m.start(), parsed[1]


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"blade:{path}:{line}-{line}:{name}:{kind}"


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
    # WI-higap: create run so Edge constructions can stamp origin_run_id.
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for blade_file in find_blade_files(repo_root, max_files=max_files):
        try:
            content = blade_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(blade_file)
        rel_path = to_relative_path(blade_file, repo_root)
        # Line starts as str.splitlines counts lines, so lines number as before
        lines = content.splitlines(keepends=True)
        line_starts = [0]
        for text in lines:
            line_starts.append(line_starts[-1] + len(text))

        # Open block sections and components, outermost first: (kind, name);
        # the name is None for one whose name is not a literal.
        open_blocks: list[tuple[str, str | None]] = []

        for directive, args, start, end in _directives(content):
            if args is None:
                kind = _CLOSES[directive]
                for i in range(len(open_blocks) - 1, -1, -1):
                    if open_blocks[i][0] == kind:
                        del open_blocks[i:]
                        break
                continue
            named = _NAME_ARG_RE.fullmatch(args[0])
            name = named.group(1) if named else None
            if directive == "section" and len(args) > 1:
                continue  # @section('t', 'v') is inline: no symbol, no block
            if name is None:
                if directive in ("section", "component"):
                    open_blocks.append((directive, None))
                continue
            index = bisect.bisect_right(line_starts, start) - 1
            line_num = index + 1
            if directive == "extends":
                # ADR-0027 Cluster E sub-case (b) shape 3 (audit-findings
                # 0010, WI-kunag): drop the per-extends Symbol and emit a
                # companion ``extends`` Edge so the layout-inheritance
                # relationship keeps representation. src is the blade file
                # id; dst is a 6-part dangling id (Blade @extends always
                # references an external template by name, no local
                # registry of layouts at this analyzer scope).
                edges.append(Edge.create(
                    src=make_file_id("blade", path_str),
                    dst=f"blade:template:{name}:0-0:{name}:template",
                    edge_type="extends",
                    line=line_num,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="extends",
                    meta={"template": name, "ref_construct": "template"},
                ))
                continue
            line_end = line_starts[index] + len(lines[index].rstrip("\r\n"))
            symbols.append(Symbol(
                id=_make_symbol_id(path_str, line_num, name, directive),
                stable_id=scoped_stable_id(
                    kind=directive, lang="blade", path=rel_path, name=name,
                    scope=[block for _, block in open_blocks if block is not None],
                ),
                name=name,
                kind=directive,
                language="blade",
                path=path_str,
                # A span stays on its directive's line (the node id names one)
                span=Span(start_line=line_num, end_line=line_num,
                          start_col=start - line_starts[index],
                          end_col=min(end, line_end) - line_starts[index]),
                origin=PASS_ID,
                origin_run_id="",
            ))
            if directive in ("section", "component"):
                open_blocks.append((directive, name))

    return AnalysisResult(symbols=symbols, edges=edges, usage_contexts=[], run=run)
