# SPDX-License-Identifier: AGPL-3.0-or-later
"""Jupyter notebook (.ipynb) analyzer.

Extracts Python symbols from Jupyter notebooks by parsing the JSON structure,
extracting code cells, stripping IPython magics/shell commands, and feeding
the concatenated source to Python's ast module.

How It Works
------------
1. Parse the .ipynb file as JSON (nbformat v4 only)
2. Check kernel language — skip non-Python notebooks
3. Extract code cell sources in document order
4. Preprocess each cell: strip line magics (%), cell magics (%%), shell (!)
   and help (?) commands while preserving line count for accurate spans
5. Concatenate cells with newline separators (a cell that ends in a newline
   is followed by a blank line); spans are lines of the concatenated source
6. Parse concatenated source with ast.parse
7. Walk AST to extract symbols and call edges: functions (nested ones
   included) and classes; a method is emitted once, by its class, as
   ``Class.method``, and also answers to its bare name for ``obj.method()``

Identity (WI-tusad)
-------------------
A function's ``stable_id`` is the Python producer's formula,
``py.python_callable_stable_id``: the normalized signature, decorators and
name-convention visibility, anchored on the notebook file, with the enclosing
classes and functions in the qualified name (``A.predict``,
``outer.helper``). So ``run(x)`` and its redefinition ``run(x, y)`` in a later
cell hold two ids, and deleting an earlier cell moves no other function's id.
A redefinition with the SAME signature in the same scope is a same-scope tie,
which the run's split pass numbers (ADR-0035 §1). A class takes the Python
class formula (``py._compute_stable_id``) over the same chain.

Why This Design
---------------
Notebooks are JSON containers around code cells. Rather than duplicating the
full Python analyzer, this module handles the notebook-specific concerns
(JSON parsing, magic stripping, cell concatenation) and delegates AST analysis
to Python's built-in ast module directly.

Symbols get language="jupyter" (not "python") because notebook code typically
lives outside the project's import namespace. Supply chain classification places
notebooks at Tier 1 (FIRST_PARTY) since INV-naduh — an in-repo notebook is the
project's own code (distance 0); "useful context but not core architecture" is a
role/context signal, not a tier-2 (internal_dep) downgrade.

Limitations
-----------
- Only nbformat v4 is supported (v3 and earlier are skipped with a warning)
- Non-Python kernels (R, Julia, etc.) are skipped
- Cell magics (%%sql, %%bash) replace the entire cell body with blanks
- Cross-file resolution between notebooks and .py modules is not attempted
- LOC counts non-blank lines of the raw .ipynb text (includes JSON structure
  and outputs)
"""
from __future__ import annotations

import ast
import json
import re
import warnings
from pathlib import Path
from typing import Iterator

from hypergumbo_core.analyze.base import AnalysisResult, make_file_stable_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.paths import normalize_path
# INV-loguk: notebook code cells are Python, so reuse py.py's language-agnostic
# Python-AST complexity walkers (the tree-sitter `compute_cyclomatic_complexity`
# dispatcher operates on tree-sitter nodes, not `ast.AST`). Same package.
from hypergumbo_lang_mainstream.py import (
    _compute_cyclomatic_complexity,
    _compute_line_span,
    _compute_stable_id,
    _python_visibility_modifiers,
    python_callable_stable_id,
)

PASS_ID = make_pass_id("jupyter")

# Matches line magics (%cmd), shell commands (!cmd), and help (?name)
# at the start of a line (ignoring leading whitespace).
# Does NOT match % inside strings — we only check line-start position.
_LINE_MAGIC_RE = re.compile(r"^(\s*)(%[a-zA-Z]|![^\s]|\?[a-zA-Z])")

# Matches cell magics (%%cmd) at the very start of cell source.
_CELL_MAGIC_RE = re.compile(r"^%%[a-zA-Z]")

# Python kernel language identifiers (case-insensitive).
_PYTHON_KERNELS = {"python", "python3", "python2", "ipython", "ipython3"}


def extract_code_cells(nb_path: Path) -> list[str]:
    """Extract code cell sources from a Jupyter notebook.

    Args:
        nb_path: Path to the .ipynb file.

    Returns:
        List of code cell source strings. Empty if the notebook can't be
        parsed, uses a non-Python kernel, or has no code cells.
    """
    try:
        data = json.loads(nb_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []

    # Only support nbformat v4
    nbformat = data.get("nbformat", 0)
    if nbformat < 4:
        if nbformat > 0:
            warnings.warn(
                f"Skipping {nb_path.name}: nbformat {nbformat} not supported (need v4+)",
                UserWarning,
                stacklevel=2,
            )
        return []

    # Check kernel language
    metadata = data.get("metadata", {})
    kernel_lang = (
        metadata.get("kernelspec", {}).get("language", "")
        or metadata.get("language_info", {}).get("name", "")
    )
    if kernel_lang.lower() not in _PYTHON_KERNELS:
        return []

    cells = data.get("cells", [])
    result = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        if source.strip():
            result.append(source)
    return result


def preprocess_notebook_source(source: str) -> str:
    """Strip IPython magics and shell commands from notebook source.

    Replaces magic/shell/help lines with blank lines to preserve line counts.
    Cell magics (%%cmd) replace the entire source with blank lines since the
    body is in a different language.

    Args:
        source: Raw source from a code cell.

    Returns:
        Preprocessed source safe for ast.parse.
    """
    lines = source.splitlines(keepends=True)
    if not lines:
        return source

    # Cell magic: entire cell is non-Python
    if _CELL_MAGIC_RE.match(lines[0]):
        return "\n" * len(lines)

    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped and _LINE_MAGIC_RE.match(line):
            # Replace with blank line (preserve newline)
            result.append("\n")
        else:
            result.append(line)
    return "".join(result)


def _find_notebook_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield .ipynb files, excluding checkpoints."""
    yield from find_files(repo_root, ["*.ipynb"], max_files=max_files)


def _make_symbol_id(path: str, line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID for notebook symbols."""
    return f"jupyter:{path}:{line}-{end_line}:{name}:{kind}"


def _analyze_notebook_file(
    nb_path: Path,
    repo_root: Path,
    *,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Analyze a single notebook file.

    Returns (symbols, edges) extracted from the notebook's code cells.
    """
    cells = extract_code_cells(nb_path)
    if not cells:
        return [], []

    # Preprocess and concatenate cells with blank line separators.
    # Track cell boundaries for line offset mapping.
    preprocessed_parts: list[str] = []
    current_line = 1
    cell_offsets: list[int] = []  # start line of each cell in concatenated source

    for cell_source in cells:
        cell_offsets.append(current_line)
        processed = preprocess_notebook_source(cell_source)
        preprocessed_parts.append(processed)
        current_line += processed.count("\n") + 1  # +1 for the separator blank line

    concatenated = "\n".join(preprocessed_parts)

    # Parse the concatenated source
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            tree = ast.parse(concatenated, filename=str(nb_path))
    except SyntaxError:
        return [], []

    # Extract symbols
    symbols: list[Symbol] = []
    symbol_by_name: dict[str, Symbol] = {}
    nb_path_str = str(nb_path)
    try:
        rel_path = str(nb_path.relative_to(repo_root))
    except ValueError:  # pragma: no cover - find_files yields paths under the root
        rel_path = nb_path_str
    file_stable_id = make_file_stable_id("jupyter", normalize_path(rel_path))
    parents: dict[int, ast.AST] = {
        id(child): parent
        for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }

    def scope_chain(node: ast.AST) -> list[str]:
        """Enclosing class and function names, outermost first (py.py's
        ``_enclosing_scope_chain``)."""
        chain: list[str] = []
        current = parents.get(id(node))
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                chain.append(current.name)
            current = parents.get(id(current))
        return chain[::-1]

    def function_symbol(
        node: ast.FunctionDef | ast.AsyncFunctionDef, name: str,
    ) -> Symbol:
        end_line = node.end_lineno or node.lineno
        return Symbol(
            id=_make_symbol_id(nb_path_str, node.lineno, end_line, name, "function"),
            name=name,
            kind="function",
            language="jupyter",
            path=nb_path_str,
            span=Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=node.end_col_offset or 0,
            ),
            origin=PASS_ID,
            origin_run_id="",
            # WI-tusad: the Python producer's formula, on the notebook file.
            stable_id=python_callable_stable_id(
                node, "function", file_stable_id,
                _python_visibility_modifiers(node.name),
                name=node.name,
                qualified_name=".".join(scope_chain(node) + [node.name]),
            ),
            # INV-loguk: Python-AST CC/LOC over the function node.
            cyclomatic_complexity=_compute_cyclomatic_complexity(node),
            line_span=_compute_line_span(node),
        )

    # A method is emitted by its class (as ``Class.m``); the walk reaching
    # its ``def`` later only registers it under the bare name, which is what
    # ``obj.m()`` resolves through.
    methods: dict[int, Symbol] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = methods.get(id(node))
            if method is not None:
                symbol_by_name[node.name] = method
                continue
            # Top-level functions and nested ones; a method is its class's.
            symbol = function_symbol(node, node.name)
            symbols.append(symbol)
            symbol_by_name[node.name] = symbol

        elif isinstance(node, ast.ClassDef):
            class_name = node.name
            end_line = node.end_lineno or node.lineno
            span = Span(
                start_line=node.lineno,
                end_line=end_line,
                start_col=node.col_offset,
                end_col=node.end_col_offset or 0,
            )
            sym_id = _make_symbol_id(nb_path_str, node.lineno, end_line, class_name, "class")
            class_symbol = Symbol(
                id=sym_id,
                name=class_name,
                kind="class",
                language="jupyter",
                path=nb_path_str,
                span=span,
                origin=PASS_ID,
                origin_run_id="",
                # The Python producer's class formula, on the notebook file.
                stable_id=_compute_stable_id(
                    node, containing_stable_id=file_stable_id, name=class_name,
                    qualified_name=".".join(scope_chain(node) + [class_name]),
                ),
            )
            symbols.append(class_symbol)
            symbol_by_name[class_name] = class_symbol

            # Extract methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_name = f"{class_name}.{item.name}"
                    method_symbol = function_symbol(item, method_name)
                    symbols.append(method_symbol)
                    symbol_by_name[method_name] = method_symbol
                    methods[id(item)] = method_symbol

    # Extract call edges
    edges: list[Edge] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller_name = node.name
        # Find the qualified caller name (might be a method)
        caller_sym = symbol_by_name.get(caller_name)
        # Check if this is inside a class by looking for qualified name
        for qname, sym in symbol_by_name.items():
            if "." in qname and qname.endswith(f".{caller_name}"):
                if sym.span and sym.span.start_line == node.lineno:
                    caller_sym = sym
                    caller_name = qname
                    break
        if caller_sym is None:  # pragma: no cover
            continue

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee_name = _extract_call_name(child)
                if callee_name and callee_name in symbol_by_name:
                    callee_sym = symbol_by_name[callee_name]
                    call_line = getattr(child, "lineno", node.lineno)
                    edge = Edge.create(
                        src=caller_sym.id,
                        dst=callee_sym.id,
                        edge_type="calls",
                        line=call_line,
                        origin=PASS_ID,
                        evidence_type="ast_call_direct",
                        origin_run_id=run_id,
                    )
                    edges.append(edge)

    return symbols, edges


def _extract_call_name(call_node: ast.Call) -> str | None:
    """Extract a simple function name from a Call node."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # self.method() or obj.method() — just return the attribute name
        return func.attr
    return None


@register_analyzer("jupyter", supports_max_files=True)
def analyze_jupyter(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze all Jupyter notebooks in a repository.

    Extracts symbols and call edges from .ipynb files containing Python code.

    Args:
        repo_root: Root directory of the repository.
        max_files: Optional limit on number of notebooks to analyze.

    Returns:
        AnalysisResult with symbols, edges, and provenance.
    """
    # WI-higap: create run so Edge constructions can stamp origin_run_id.
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for nb_path in _find_notebook_files(repo_root, max_files=max_files):
        symbols, edges = _analyze_notebook_file(nb_path, repo_root, run_id=run.execution_id)
        all_symbols.extend(symbols)
        all_edges.extend(edges)

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=[],
        run=run,
    )
