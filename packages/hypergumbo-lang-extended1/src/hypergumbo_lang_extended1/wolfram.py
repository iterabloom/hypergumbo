# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wolfram Language analysis pass using tree-sitter-wolfram.

This analyzer uses tree-sitter to parse Wolfram Language files and extract:
- Function definitions (``f[x_] := ...``, and ``f[x_] = ...`` too)
- Variable assignments (Set =), including ones inside a function body, which
  become file-level variables
- Function calls, as edges from the FILE node (not the enclosing function),
  one per callee name per file; a callee found nowhere gets a
  ``wolfram:builtin:0-0:<name>:function`` id
- Import statements (calls to Get, Needs, Import)

Wolfram Language (also known as Mathematica) is a symbolic programming language
used for technical computing, data science, and mathematical modeling.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect function calls and imports

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Wolfram-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Built from source since not on PyPI
- Uses tree-sitter-wolfram grammar (bostick/tree-sitter-wolfram)
- Two-pass allows cross-file resolution
- Wolfram uses [] for function calls, not ()

Wolfram Language Considerations
-------------------------------
- Function definitions use SetDelayed (:=) or Set (=)
- Pattern matching uses underscores (x_) for arguments
- Imports use Get["package`"], Needs["package`"], or <<package`; only the
  three call forms are extracted, not ``<<package```

Known gaps: the left-hand side of a definition (``f[x_] := ...``) is itself a
call node, so every definition also yields a ``calls`` edge to the function it
defines; and ``.m`` files are not analyzed (``*.m`` is not in the file
patterns, and ``find_wolfram_files`` has no caller).
- Package names end with backtick (`)
- Comments use (* ... *)

Definitions and identity (INV-kagib, WI-tusad)
----------------------------------------------
Every definition is its own ``function`` symbol: ``run[x_Integer] := x`` and
``run[x_String] := ...`` are two overloads (Wolfram dispatches on the pattern),
and ``fib[0] = 0`` / ``fib[n_] := ...`` are two rules. Before, a per-file set of
seen names kept only the first. A variable assigned more than once is still one
``variable`` symbol (its first assignment). The tree-sitter grammar does not
parse blank patterns (``x_Integer`` becomes an ``ERROR`` node and ``helper[]``
is not a call at all, so ``helper[] := 2`` was missed), so a definition is
recognised from the TEXT of its left-hand side, ``name[args]``, and the
arguments are split at top-level commas and read as patterns:

* ``x_``, ``x_Integer``, ``x_?NumericQ``: one parameter; what follows the blank
  (the head, a ``?test``) is its type;
* ``x__`` / ``x___`` (and ``opts:OptionsPattern[]``): the variadic parameter;
* ``x_:1`` / ``x_.``: a parameter with a default;
* anything else (``0``, ``"a"``, ``{a_, b_}``): a literal pattern, whose text is
  its type.

The parameters go to ``meta["parameters"]`` and the ADR-0035 producer stable_id
is minted through ``overload_arity.declared_arity_stable_id``: a definition with
any typed or literal argument takes the typed tier, so ``run[x_Integer]`` and
``run[x_String]`` (same arity) get distinct ids that do not depend on which is
written first. Two definitions with the same patterns (a redefinition) tie and
are ordered by the per-file occurrence pass. ``signature`` is the argument list
as written (``[x_Integer]``). ``f[x_][y_] := ...`` (a curried definition) is
read by its first argument group. ``a[[1]] = v`` (a Part assignment) and
``f[x_] /; c := ...`` (a conditioned left-hand side) are not definitions here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import classify_dot_m_file, find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("wolfram")


def find_wolfram_files(repo_root: Path) -> Iterator[Path]:
    """Yield Wolfram files, disambiguating .m files via content heuristics.

    Wolfram uses .wl, .wls, and .nb extensions unambiguously. The .m extension
    is shared with MATLAB and Objective-C, so those files are classified by
    content before inclusion.
    """
    # Unambiguous Wolfram extensions
    yield from find_files(repo_root, ["*.wl", "*.wls", "*.nb"])
    # Ambiguous .m files — only include if classified as Wolfram
    for path in find_files(repo_root, ["*.m"]):
        if classify_dot_m_file(path) == "wolfram":
            yield path


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Wolfram module (used as import edge target)."""
    return f"wolfram:{module_name}:0-0:module:module"


#: ``name[`` at the start of a definition's left-hand side.
_DEF_HEAD_RE = re.compile(r"([A-Za-z$][A-Za-z0-9$`]*)\s*\[")

#: One blank pattern: ``name`` (optional), 1-3 underscores, a head (optional),
#: then whatever follows (``?test``, ``:default``, ``.``). A Wolfram symbol
#: has no underscore (``_`` IS the blank), so names and heads exclude it.
_BLANK_RE = re.compile(
    r"([A-Za-z$][A-Za-z0-9$]*)?(_{1,3})([A-Za-z$][A-Za-z0-9$`]*)?(.*)", re.S,
)

#: ``name:pattern`` (a named pattern that is not a blank, ``opts:OptionsPattern[]``).
_NAMED_PATTERN_RE = re.compile(r"([A-Za-z$][A-Za-z0-9$]*):(?!_)(.+)", re.S)

_BRACKETS = {"[": "]", "{": "}", "(": ")"}


def _split_top_level(text: str) -> Optional[tuple[list[str], int]]:
    """Split the text after a ``[`` at top-level commas up to its matching ``]``.

    Returns the argument texts and the index just past the ``]``, or None when
    the brackets do not balance. Strings are skipped whole.
    """
    args: list[str] = []
    depth: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            close = text.find('"', i + 1)
            if close < 0:
                return None
            current.append(text[i:close + 1])
            i = close + 1
            continue
        if ch in _BRACKETS:
            depth.append(_BRACKETS[ch])
        elif depth and ch == depth[-1]:
            depth.pop()
        elif not depth and ch == "]":
            arg = "".join(current).strip()
            if arg or args:
                args.append(arg)
            return args, i + 1
        elif not depth and ch == ",":
            args.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    return None


def _definition_lhs(lhs: str) -> Optional[tuple[str, list[str]]]:
    """``(name, argument texts)`` when ``lhs`` is ``name[args]`` (or ``name[args][...]``).

    None for anything else: a bare symbol, a Part assignment ``a[[1]]``, a
    conditioned ``f[x_] /; c``, unbalanced text.
    """
    head = _DEF_HEAD_RE.match(lhs)
    if head is None or lhs[head.end():].startswith("["):
        return None
    split = _split_top_level(lhs[head.end():])
    if split is None:
        return None
    args, used = split
    rest = lhs[head.end() + used:]
    while rest.startswith("["):  # curried: f[x_][y_]
        more = _split_top_level(rest[1:])
        if more is None:
            return None
        rest = rest[1 + more[1]:]
    if rest.strip():
        return None
    return head.group(1), args


def _pattern_parameter(arg: str) -> dict[str, Any]:
    """One ``meta["parameters"]`` entry for a Wolfram argument pattern."""
    text = " ".join(arg.split())
    blank = _BLANK_RE.fullmatch(text)
    if blank is not None:
        name, blanks, head, rest = blank.groups()
        default = rest.startswith(":") or rest == "."
        type_text = (head or "") + ("" if default else rest)
        return parameter_entry(
            name or blanks, type_text or None,
            default=default, variadic=len(blanks) > 1,
        )
    named = _NAMED_PATTERN_RE.fullmatch(text)
    if named is not None:
        pattern = named.group(2)
        return parameter_entry(
            named.group(1), pattern,
            variadic=pattern.startswith("OptionsPattern"),
        )
    return parameter_entry(text, text)


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Wolfram file.

    Detects:
    - function: SetDelayed (:=) with call on left side
    - variable: Set (=) with symbol on left side

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []
    seen_variables: set[str] = set()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        meta: dict[str, Any] | None = None,
        signature: Optional[str] = None,
    ) -> None:
        """Add one symbol; a variable only on its first assignment."""
        stable_id: Optional[str] = None
        if kind == "variable":
            if name in seen_variables:
                return
            seen_variables.add(name)
        else:
            stable_id = declared_arity_stable_id(
                kind=kind, lang="wolfram", path=file_path, name=name,
                qualified_name=name, arity=declared_arity(meta),
            )

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = make_symbol_id("wolfram", file_path, start_line, end_line, name, kind)
        # INV-loguk: CC only for callables. Wolfram's grammar has no control-flow
        # node types (If/While/etc. are call nodes), so CC derives solely from
        # short-circuit &&/|| — base 1 for a branchless function. Gated so
        # variable-kind symbols stay None.
        _is_callable = kind == "function"
        sym = Symbol(
            id=sym_id,
            stable_id=stable_id,
            name=name,
            kind=kind,
            language="wolfram",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            cyclomatic_complexity=(
                compute_cyclomatic_complexity(node, "wolfram") if _is_callable else None
            ),
            line_span=(end_line - start_line + 1) if _is_callable else None,
        )
        if meta:
            sym.meta = meta
        symbols.append(sym)

    for node in iter_tree(tree.root_node):
        # A definition or assignment: ``lhs := rhs`` (SetDelayed) or
        # ``lhs = rhs`` (Set), the first such operator among the children.
        if node.type != "binary":
            continue
        op_node = next(
            (c for c in node.children if c.type in (":=", "=")), None,
        )
        if op_node is None:
            continue
        lhs = source[node.start_byte:op_node.start_byte].decode(
            "utf-8", errors="replace",
        ).strip()
        definition = _definition_lhs(lhs)
        if definition is not None:
            func_name, args = definition
            add_symbol(
                node, func_name, "function",
                meta={PARAMETERS_KEY: [_pattern_parameter(a) for a in args]},
                signature="[" + ", ".join(" ".join(a.split()) for a in args) + "]",
            )
        elif node.children[0].type == "symbol" and op_node == node.children[1]:
            name = node_text(node.children[0], source).strip()
            if op_node.type == "=":
                add_symbol(node, name, "variable")
            else:
                # ``f := ...``: a delayed value, called as ``f``.
                add_symbol(node, name, "function", meta={PARAMETERS_KEY: []})

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
) -> list[Edge]:
    """Extract import and call edges from a parsed Wolfram file.

    Detects:
    - imports: Get["package`"], Needs["package`"], Import["file"]
    - calls: function calls like Sin[x], Map[f, list]

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    edges: list[Edge] = []
    file_id = make_file_id("wolfram", file_path)
    seen_calls: set[str] = set()

    for node in iter_tree(tree.root_node):
        # Look for function calls
        if node.type == "call":
            # First child should be the function name
            func_name_node = find_child_by_type(node, "symbol")
            if func_name_node:
                func_name = node_text(func_name_node, source).strip()
                if func_name:
                    # Check for import functions
                    if func_name in ("Get", "Needs", "Import"):
                        # Find the string argument
                        string_node = find_child_by_type(node, "string")
                        if string_node:
                            string_text = node_text(string_node, source).strip()
                            # Remove quotes
                            module_name = string_text.strip('"').strip("'")
                            if module_name:
                                module_id = _make_module_id(module_name)
                                edge = Edge.create(
                                    src=file_id,
                                    dst=module_id,
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="import",
                                )
                                edges.append(edge)
                    else:
                        # Regular function call
                        call_key = f"{file_id}->{func_name}"
                        if call_key not in seen_calls:
                            seen_calls.add(call_key)
                            # Check if it's a known symbol
                            result = resolver.lookup(func_name)
                            if result.found:
                                target_id = result.symbol.id
                            else:
                                # Create synthetic ID for built-in
                                target_id = f"wolfram:builtin:0-0:{func_name}:function"

                            edge = Edge.create(
                                src=file_id,
                                dst=target_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                            )
                            edges.append(edge)

    return edges


class WolframAnalyzer(TreeSitterAnalyzer):
    """Wolfram Language analyzer using tree-sitter-wolfram."""

    lang = "wolfram"
    file_patterns: ClassVar[list[str]] = ["*.wl", "*.wls", "*.nb"]
    grammar_module = "tree_sitter_wolfram"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function and variable symbols from a Wolfram file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a Wolfram file."""
        # Use the resolver that was built from global_symbols
        return _extract_edges_from_file(
            tree, source, rel_path, [], resolver, run.execution_id,
        )


_analyzer = WolframAnalyzer()


def is_wolfram_tree_sitter_available() -> bool:
    """Check if tree-sitter with Wolfram grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("wolfram")
def analyze_wolfram(repo_root: Path) -> AnalysisResult:
    """Analyze Wolfram files in a repository."""
    return _analyzer.analyze(repo_root)
