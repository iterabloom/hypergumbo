# SPDX-License-Identifier: AGPL-3.0-or-later
"""Just (justfile) analyzer using regex patterns.

Just is a command runner (alternative to Make). Justfiles define recipes
(tasks) with dependencies, variables, and shell commands.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find justfile and *.just files
2. Extract recipe definitions (recipe_name: deps)
3. Extract variable assignments (var := value)
4. Extract recipe dependencies as edges

Symbols Extracted
-----------------
- **recipe**: Named tasks/recipes (build, test, deploy)
- **variable**: Variable assignments (version := "1.0")
- **alias**: Recipe aliases (alias b := build)

Edges Extracted
---------------
- **depends_on**: Recipe dependencies (test: build → test depends_on build)

Why This Design
---------------
- Just is increasingly popular as a simpler Make alternative
- Recipe definitions are the primary unit of organization
- Dependency edges reveal task execution order
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("just")

# recipe_name arg1 arg2: dep1 dep2
# Recipes start at column 0, with an optional list of params and deps
_RECIPE_RE = re.compile(r"^([a-zA-Z_][\w-]*)\s*(?:[^:]*)?:", re.MULTILINE)
# variable := value or variable = value
_VAR_RE = re.compile(r"^([a-zA-Z_]\w*)\s*:?=\s*(.+)", re.MULTILINE)
# alias name := recipe
_ALIAS_RE = re.compile(r"^alias\s+(\w+)\s*:=\s*(\w+)", re.MULTILINE)
# Lines starting with @ or whitespace are recipe body, not definitions
_BODY_LINE_RE = re.compile(r"^[\s@#]")


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"just:{path}:{line}:{name}:{kind}"


def find_just_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all justfiles."""
    yield from find_files(
        repo_root, ["justfile", "Justfile", ".justfile", "*.just"],
        max_files=max_files,
    )


@register_analyzer("just", supports_max_files=True)
def analyze_just(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze justfiles for recipes, variables, and dependencies."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for just_file in find_just_files(repo_root, max_files=max_files):
        try:
            content = just_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(just_file)
        recipe_symbols: dict[str, Symbol] = {}

        for line_num, line in enumerate(content.splitlines(), 1):
            if _BODY_LINE_RE.match(line) or not line.strip():
                continue

            # Check alias first (alias name := recipe)
            m = _ALIAS_RE.match(line)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "alias"),
                    name=name,
                    kind="alias",
                    language="just",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            # Check variable assignment (name := value) before recipe
            # because := contains : which would match recipe pattern
            m = _VAR_RE.match(line)
            if m:
                name = m.group(1)
                if name not in {"set", "export", "import", "mod", "alias"}:
                    symbols.append(Symbol(
                        id=_make_symbol_id(path_str, line_num, name, "variable"),
                        name=name,
                        kind="variable",
                        language="just",
                        path=path_str,
                        span=Span(start_line=line_num, end_line=line_num,
                                  start_col=0, end_col=len(line)),
                        origin=PASS_ID,
                        origin_run_id="",
                    ))
                continue

            # Check recipe definition (name: deps)
            m = _RECIPE_RE.match(line)
            if m:
                name = m.group(1)
                # Skip keywords that look like recipes
                if name in {"set", "export", "import", "mod", "alias"}:
                    continue
                sym = Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "recipe"),
                    name=name,
                    kind="recipe",
                    language="just",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                )
                symbols.append(sym)
                recipe_symbols[name] = sym

                # Extract dependencies (after the colon)
                colon_pos = line.find(":")
                if colon_pos >= 0:
                    deps_str = line[colon_pos + 1:].strip()
                    for dep in deps_str.split():
                        dep = dep.strip()
                        if dep and dep in recipe_symbols:
                            edges.append(Edge.create(
                                src=sym.id,
                                dst=recipe_symbols[dep].id,
                                edge_type="depends_on",
                                line=line_num,
                                origin=PASS_ID,
                                evidence_type="recipe_dependency",
                            ))

    return AnalysisResult(symbols=symbols, edges=edges, usage_contexts=[])
