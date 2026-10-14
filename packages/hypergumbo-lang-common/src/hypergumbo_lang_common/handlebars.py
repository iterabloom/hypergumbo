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
3. Extract block helpers ({{#helperName}}), splitting built-ins from custom
   block helpers (non-block {{helperName args}} calls are not extracted)

Symbols Extracted
-----------------
- **partial**: Partial template references ({{> header}})
- **block**: Block helper invocations ({{#each}}, {{#if}}, {{#with}})
- **helper**: Custom block helper invocations ({{#formatDate date}})

Why This Design
---------------
- Handlebars is widely used in Node.js web applications
- Partial references reveal template composition
- Block helpers show control flow and iteration patterns
- Custom helpers indicate reusable template logic

Identity (ADR-0035, WI-tusad)
-----------------------------
Every symbol mints ``overload_arity.scoped_stable_id`` with the block helpers
open around it as the scope chain, each labelled by its opening tag's
content (``if user``, ``each items``); a block's own label is the last link
of its chain. ``{{> header}}`` inside ``{{#if user}}`` and again inside
``{{#if admin}}`` are two symbols that the floor key ``(kind, path, name)``
nulled. The same tag twice in one block (two ``{{> header}}``) remains a
same-scope tie. Blocks are tracked by their ``{{#name}}`` / ``{{/name}}``
tags across lines (:func:`_scopes_at`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.paths import to_relative_path

PASS_ID = make_pass_id("handlebars")

# {{> partialName}} or {{> "path/to/partial"}}
_PARTIAL_RE = re.compile(r"""\{\{>\s*['"]?([^\s'"}\)]+)['"]?""")
# {{#blockHelper}} — custom block helpers
_BLOCK_RE = re.compile(r"""\{\{#(\w+)""")
# Built-in block helpers to exclude from custom helper detection
_BUILTIN_BLOCKS = frozenset({"if", "unless", "each", "with", "else"})
# {{#name args}} opens a block, {{/name}} closes it (scope tracking only)
_OPEN_RE = re.compile(r"""\{\{#(\w+)([^}]*)\}\}""")
_CLOSE_RE = re.compile(r"""\{\{~?/(\w+)""")


def _scopes_at(
    line: str, stack: list[tuple[str, str]],
) -> dict[int, tuple[str, ...]]:
    """The open-block labels at each tag of ``line``, updating ``stack``.

    ``stack`` holds ``(name, label)`` for every block open at the start of the
    line and is left holding those open at its end. Returns, for the column of
    every partial and block tag, the chain of labels open there; a block's
    own label ends its chain. A closing tag pops back to the innermost open
    block of its name (an unmatched one is ignored).
    """
    events: list[tuple[int, str, str, str]] = []
    for m in _PARTIAL_RE.finditer(line):
        events.append((m.start(), "partial", "", ""))
    for m in _OPEN_RE.finditer(line):
        events.append((m.start(), "open", m.group(1),
                       " ".join(f"{m.group(1)}{m.group(2)}".split())))
    for m in _CLOSE_RE.finditer(line):
        events.append((m.start(), "close", m.group(1), ""))
    chains: dict[int, tuple[str, ...]] = {}
    for col, event, name, label in sorted(events):
        if event == "open":
            stack.append((name, label))
        elif event == "close":
            names = [n for n, _ in stack]
            if name in names:
                del stack[len(names) - 1 - names[::-1].index(name):]
            continue
        chains[col] = tuple(lab for _, lab in stack)
    return chains


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"handlebars:{path}:{line}-{line}:{name}:{kind}"


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
        rel_path = to_relative_path(hbs_file, repo_root)
        open_blocks: list[tuple[str, str]] = []

        for line_num, line in enumerate(content.splitlines(), 1):
            chains = _scopes_at(line, open_blocks)
            for match in _PARTIAL_RE.finditer(line):
                name = match.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "partial"),
                    stable_id=scoped_stable_id(
                        kind="partial", lang="handlebars", path=rel_path, name=name,
                        scope=chains[match.start()],
                    ),
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
                    stable_id=scoped_stable_id(
                        kind=kind, lang="handlebars", path=rel_path, name=name,
                        scope=chains.get(match.start(), ()),
                    ),
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
