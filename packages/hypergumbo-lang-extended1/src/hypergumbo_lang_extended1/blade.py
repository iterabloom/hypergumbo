# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blade template analyzer using regex patterns.

Laravel Blade is a templating engine for PHP. Blade files (.blade.php) mix
HTML with directives like @section, @yield, @component, @extends, and
control flow (@if, @foreach, @while).

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .blade.php files
2. Walk the directives of each line in source order, tracking the block
   sections and components open around each one
3. Extract @section('name'), @component and @yield directives as symbols
4. Extract @extends directives as edges

Symbols Extracted
-----------------
- **section**: Named content sections (@section('name'))
- **component**: Component references (@component('name'))
- **yield**: Yield points for layout inheritance (@yield('name'))

Edges Extracted
---------------
- **extends**: layout inheritance — emitted from @extends('name').
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
  sections and components open around them (INV-punig, scheme v9): a section
  opened by ``@section('name')`` (the one-argument form; ``@section('t',
  'v')`` is inline and is not emitted) closes at ``@endsection``, ``@stop``,
  ``@show``, ``@overwrite`` or ``@append``, a ``@component`` at
  ``@endcomponent``. ``@yield('inner')`` in ``@section('a')`` and in
  ``@section('b')`` are two identities, and deleting the first section moves
  nothing. The same name twice in one block is a same-scope tie, which the
  per-file split pass orders
"""
from __future__ import annotations

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

# Blade directives, in source order: a named directive (group 1, its name in
# group 2) or a block closer (group 3).
_DIRECTIVE_RE = re.compile(
    r"""@(section|yield|extends|component)\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|@(endsection|stop|show|overwrite|append|endcomponent)\b"""
)
#: Which open block each closer ends.
_CLOSES = {"endcomponent": "component"}  # every other closer ends a section


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

        # Open block sections and components, outermost first: (kind, name).
        open_blocks: list[tuple[str, str]] = []

        for line_num, line in enumerate(content.splitlines(), 1):
            for match in _DIRECTIVE_RE.finditer(line):
                closer = match.group(3)
                if closer:
                    kind = _CLOSES.get(closer, "section")
                    for i in range(len(open_blocks) - 1, -1, -1):
                        if open_blocks[i][0] == kind:
                            del open_blocks[i:]
                            break
                    continue
                directive, name = match.group(1), match.group(2)
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
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, directive),
                    stable_id=scoped_stable_id(
                        kind=directive, lang="blade", path=rel_path, name=name,
                        scope=[block_name for _, block_name in open_blocks],
                    ),
                    name=name,
                    kind=directive,
                    language="blade",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                if directive in ("section", "component"):
                    open_blocks.append((directive, name))

    return AnalysisResult(symbols=symbols, edges=edges, usage_contexts=[], run=run)
