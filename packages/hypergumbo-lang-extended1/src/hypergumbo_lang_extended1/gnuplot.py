# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gnuplot script analyzer using regex patterns.

Gnuplot is a command-line plotting program. Scripts (.gnuplot, .gp, .plt)
define plotting commands, functions, and variable assignments.

How It Works
------------
Regex-based extraction (no tree-sitter grammar available on PyPI):
1. Find all Gnuplot files
2. Extract function definitions (f(x) = ...)
3. Extract variable assignments (var = expr)
4. Extract plot/splot commands as symbols
5. Extract load/call directives as edges

Symbols Extracted
-----------------
- **function**: Named functions (f(x) = x**2)
- **variable**: Variable assignments (a = 3.14)
- **plot**: Plot commands (plot, splot, replot)

Why This Design
---------------
- Gnuplot is widely used in scientific computing and publications
- Function definitions and variables reveal computation structure
- Plot commands are the key outputs of Gnuplot scripts
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

PASS_ID = make_pass_id("gnuplot")

# function_name(args) = expression
_FUNC_RE = re.compile(r"^(\w+)\s*\([^)]*\)\s*=\s*(.+)", re.MULTILINE)
# variable = value (not a function, no parens before =)
_VAR_RE = re.compile(r"^(\w+)\s*=\s*(?!\s*\()(.+)", re.MULTILINE)
# plot/splot/replot commands
_PLOT_RE = re.compile(r"^((?:re)?[sp]?plot)\b", re.MULTILINE)


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"gnuplot:{path}:{line}:{name}:{kind}"


def find_gnuplot_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Gnuplot script files."""
    yield from find_files(
        repo_root, ["*.gnuplot", "*.gp", "*.plt"], max_files=max_files
    )


@register_analyzer("gnuplot", supports_max_files=True)
def analyze_gnuplot(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze Gnuplot scripts for functions, variables, and plot commands."""
    symbols: list[Symbol] = []

    for gp_file in find_gnuplot_files(repo_root, max_files=max_files):
        try:
            content = gp_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(gp_file)
        lines = content.splitlines()

        # Track which lines have functions (so we skip them for variable detection)
        func_lines: set[int] = set()

        for line_num, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or not stripped:
                continue

            m = _FUNC_RE.match(stripped)
            if m:
                name = m.group(1)
                func_lines.add(line_num)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, name, "function"),
                    name=name,
                    kind="function",
                    language="gnuplot",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))
                continue

            m = _VAR_RE.match(stripped)
            if m and line_num not in func_lines:
                name = m.group(1)
                # Skip gnuplot builtins/commands that look like assignments
                if name not in {"set", "unset", "reset", "load", "call",
                                "print", "pause", "if", "else", "do", "while"}:
                    symbols.append(Symbol(
                        id=_make_symbol_id(path_str, line_num, name, "variable"),
                        name=name,
                        kind="variable",
                        language="gnuplot",
                        path=path_str,
                        span=Span(start_line=line_num, end_line=line_num,
                                  start_col=0, end_col=len(line)),
                        origin=PASS_ID,
                        origin_run_id="",
                    ))
                continue

            m = _PLOT_RE.match(stripped)
            if m:
                cmd = m.group(1)
                symbols.append(Symbol(
                    id=_make_symbol_id(path_str, line_num, cmd, "plot"),
                    name=cmd,
                    kind="plot",
                    language="gnuplot",
                    path=path_str,
                    span=Span(start_line=line_num, end_line=line_num,
                              start_col=0, end_col=len(line)),
                    origin=PASS_ID,
                    origin_run_id="",
                ))

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
