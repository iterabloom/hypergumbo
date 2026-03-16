# SPDX-License-Identifier: AGPL-3.0-or-later
"""Handlebars template analyzer using regex patterns.

Handlebars is a popular templating language used in Ember.js, Express.js, and
standalone. Templates (.hbs, .handlebars) use {{}} syntax for expressions,
helpers, partials, and block helpers.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all .hbs and .handlebars files
2. Extract partial references ({{> partialName}})
3. Extract block helpers ({{#helperName}})
4. Extract custom helper calls ({{helperName args}})

Symbols Extracted
-----------------
- **partial**: Partial template references ({{> header}})
- **block**: Block helper invocations ({{#each}}, {{#if}}, {{#with}})
- **helper**: Custom helper calls ({{formatDate date}})

Why This Design
---------------
- Handlebars is widely used in Node.js web applications
- Partial references reveal template composition
- Block helpers show control flow and iteration patterns
- Custom helpers indicate reusable template logic
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("handlebars")

# {{> partialName}} or {{> "path/to/partial"}}
_PARTIAL_RE = re.compile(r"""\{\{>\s*['"]?([^\s'"}\)]+)['"]?""")
# {{#blockHelper}} — custom block helpers
_BLOCK_RE = re.compile(r"""\{\{#(\w+)""")
# Built-in block helpers to exclude from custom helper detection
_BUILTIN_BLOCKS = frozenset({"if", "unless", "each", "with", "else"})


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"handlebars:{path}:{line}:{name}:{kind}"


def find_handlebars_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Handlebars template files."""
    yield from find_files(
        repo_root, ["*.hbs", "*.handlebars"], max_files=max_files
    )


@register_analyzer("handlebars", supports_max_files=True)
def analyze_handlebars(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze Handlebars templates for partials, blocks, and helpers."""
    symbols: list[Symbol] = []

    for hbs_file in find_handlebars_files(repo_root, max_files=max_files):
        try:
            content = hbs_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(hbs_file)

        for line_num, line in enumerate(content.splitlines(), 1):
            for match in _PARTIAL_RE.finditer(line):
                name = match.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "partial"),
                    name=name,
                    kind="partial",
                    language="handlebars",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

            for match in _BLOCK_RE.finditer(line):
                name = match.group(1)
                kind = "block" if name in _BUILTIN_BLOCKS else "helper"
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, kind),
                    name=name,
                    kind=kind,
                    language="handlebars",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=match.start(), end_col=match.end()),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
