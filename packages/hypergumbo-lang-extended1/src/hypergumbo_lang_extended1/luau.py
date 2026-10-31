# SPDX-License-Identifier: AGPL-3.0-or-later
"""Luau language analyzer.

This module analyzes Luau files (.luau only) using tree-sitter. Luau is
Roblox's typed Lua variant used for game development on the Roblox platform.
It extends Lua with a gradual type system. Vanilla .lua files are handled
by the lua-v1 analyzer to avoid duplicate symbol extraction.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (functions, types, variables)
- Pass 2: Extract edges (function calls)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Luau-specific extraction
logic.

Symbol Types
------------
- function: Local and module function definitions
- type: Type definitions (type and export type)
- variable: ``local`` declarations whose name starts with an uppercase letter
  (meant for module tables such as ``local MyModule = {}``); the value is
  not checked, and declarations nested inside functions are included too

Every stable_id hashes the functions enclosing the symbol
(``_luau_scope_slot``; INV-fabih), empty at top level.

Edge Types
----------
- calls: Function calls from one symbol to another

Call resolution
---------------
- A call is dropped when either its first or its last ``.`` / ``:``
  segment is in ``LUAU_BUILTINS`` (Lua builtins, ``string``/``table``/
  ``math`` members, Roblox globals and services).
- The caller is the innermost ``function_declaration`` containing the call,
  found by POSITION in this file (``symbols_at`` / ``enclosing_declared_symbol``
  over ``file_symbols()``; WI-jusah, the WI-kosar family). Before, it was the
  OUTERMOST named function, looked up by name in a registry built from the
  run-wide symbol table, which keeps one symbol per name: two files each
  defining ``local function helper`` had one file's calls credited to the
  other's, and a nested function's calls went to the function around it. An
  anonymous ``function () ... end`` has no symbol, so its calls belong to the
  named function around it; a call in no function comes from
  ``luau:<path>:file``.
- The callee is looked up by its full text (``Module.fn``), then by its
  short name (the last ``.`` / ``:`` segment), each time in THIS file's
  symbols before the run-wide table (confidence 1.0 either way): a
  ``local function`` is file-scoped, so another file's same-named one must
  not win while this file has its own.
- An unmatched call is still emitted, to
  ``luau:unresolved:0-0:<name>:unresolved`` with confidence 0.6. All call
  edges carry evidence_type ``tree_sitter``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    ancestor_labels,
    enclosing_declared_symbol,
    iter_tree,
    make_symbol_id,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import nested_qualified_name
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("luau")

# Built-in Luau/Roblox functions and services to filter out
LUAU_BUILTINS = frozenset({
    # Lua builtins
    "print", "error", "warn", "assert", "type", "typeof", "tonumber", "tostring",
    "pairs", "ipairs", "next", "select", "unpack", "rawget", "rawset", "rawequal",
    "setmetatable", "getmetatable", "pcall", "xpcall", "require", "loadstring",
    # Table functions
    "table", "insert", "remove", "sort", "concat", "find", "clear", "clone",
    "move", "freeze", "isfrozen",
    # String functions
    "string", "format", "sub", "match", "gsub", "gmatch", "lower",
    "upper", "len", "rep", "char", "byte", "reverse", "split", "pack",
    # Math functions
    "math", "abs", "acos", "asin", "atan", "atan2", "ceil", "cos", "cosh",
    "deg", "exp", "floor", "fmod", "frexp", "ldexp", "log", "log10", "max",
    "min", "modf", "pow", "rad", "random", "randomseed", "sin", "sinh",
    "sqrt", "tan", "tanh", "clamp", "sign", "noise", "round",
    # Coroutine
    "coroutine", "create", "resume", "yield", "status", "wrap", "running",
    # OS
    "os", "time", "date", "difftime", "clock",
    # Debug
    "debug", "traceback", "info", "profilebegin", "profileend", "setmemorycategory",
    # Task
    "task", "spawn", "delay", "defer", "wait", "cancel", "synchronize", "desynchronize",
    # Roblox globals
    "game", "workspace", "script", "plugin", "Instance", "Enum", "Vector2",
    "Vector3", "CFrame", "Color3", "BrickColor", "UDim", "UDim2", "Ray",
    "Rect", "Region3", "TweenInfo", "NumberSequence", "ColorSequence",
    "NumberRange", "Font", "tick", "elapsedTime",
    "settings", "UserSettings", "Stats", "shared", "_G",
    # Common service getters
    "GetService", "FindFirstChild", "WaitForChild", "GetChildren", "GetDescendants",
    "Clone", "Destroy", "new", "Connect", "Disconnect", "Fire", "Wait",
})



def find_luau_files(repo_root: Path) -> list[Path]:
    """Find all Luau (.luau) files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.

    Only processes .luau files (Roblox's typed Lua variant).
    Vanilla .lua files are handled exclusively by the lua-v1 analyzer
    to avoid duplicate symbol extraction.
    """
    return sorted(_analyzer._find_source_files(repo_root))


def _extract_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a parameters node."""
    params: list[str] = []
    for child in node.children:
        if child.type == "parameter":
            param_name = None
            param_type = None
            for subchild in child.children:
                if subchild.type == "identifier":
                    param_name = _get_node_text(subchild)
                elif subchild.type == ":" and param_type is None:
                    pass
                elif param_name is not None and subchild.type not in ("(", ")", ",", ":"):
                    # Type annotation
                    type_text = _get_node_text(subchild)
                    if type_text:
                        param_type = type_text
            if param_name:
                if param_type:
                    params.append(f"{param_name}: {param_type}")
                else:
                    params.append(param_name)
    return params


def _luau_scope_slot(node: "tree_sitter.Node", name: str) -> str:
    """The qualified-name slot a Luau stable_id hashes: the functions enclosing
    ``node`` (INV-fabih; every Luau symbol hashed an empty one, so a local
    ``inner`` in functions ``a`` and ``b`` tied), ``""`` at top level."""
    def label(n: "tree_sitter.Node") -> Optional[str]:
        if n.type != "function_declaration":
            return None
        return next((
            _get_node_text(c) for c in n.children
            if c.type in ("identifier", "dot_index_expression", "method_index_expression")
        ), None)
    return nested_qualified_name(ancestor_labels(node, label), name)


def _extract_function(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer", file_anchor: str,
) -> None:
    """Extract a function definition."""
    name = None
    is_local = False
    params: list[str] = []
    return_type = None

    for child in node.children:
        if child.type == "local":
            is_local = True
        elif child.type == "identifier" and name is None:
            name = _get_node_text(child)
        elif child.type == "dot_index_expression":
            # Module.method style
            name = _get_node_text(child)
        elif child.type == "method_index_expression":
            # Module:method style
            name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_params(child)
        elif child.type == ":" and return_type is None:
            # Next identifier is return type
            pass
        elif child.type == "identifier" and name is not None:  # pragma: no cover
            # This could be the return type (complex to hit in tests)
            return_type = _get_node_text(child)

    if name:
        meta: dict[str, object] = {}
        if is_local:
            meta["local"] = True
        if params:
            meta["params"] = params
        if return_type:  # pragma: no cover
            meta["return_type"] = return_type

        signature = f"{name}({', '.join(params)})"

        sym = Symbol(
            id=make_symbol_id("luau", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "function"),
            stable_id=analyzer.compute_stable_id(
                node, kind="function", name=name,
                qualified_name=_luau_scope_slot(node, name), file_stable_id=file_anchor,
            ),
            name=name,
            kind="function",
            language="luau",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            signature=signature,
            meta=meta if meta else {},
            cyclomatic_complexity=compute_cyclomatic_complexity(node, "luau"),
            line_span=node.end_point[0] - node.start_point[0] + 1,
        )
        analysis.symbols.append(sym)
        analysis.node_for_symbol[sym.id] = node
        analysis.symbol_by_name[sym.name] = sym


def _extract_type(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer", file_anchor: str,
) -> None:
    """Extract a type definition."""
    name = None
    is_exported = False

    for child in node.children:
        if child.type == "export":
            is_exported = True
        elif child.type == "identifier":
            name = _get_node_text(child)
            break

    if name:
        meta: dict[str, object] = {}
        if is_exported:
            meta["exported"] = True

        sym = Symbol(
            id=make_symbol_id("luau", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "type"),
            stable_id=analyzer.compute_stable_id(
                node, kind="type", name=name,
                qualified_name=_luau_scope_slot(node, name), file_stable_id=file_anchor,
            ),
            name=name,
            kind="type",
            language="luau",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta if meta else {},
        )
        analysis.symbols.append(sym)
        analysis.node_for_symbol[sym.id] = node
        analysis.symbol_by_name[sym.name] = sym


def _extract_variable(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer", file_anchor: str,
) -> None:
    """Extract a variable declaration."""
    # Target module tables (e.g., local MyModule = {}) by name only: skip
    # lowercase locals. Depth is not checked -- the recursive walker calls
    # this for nested declarations as well.
    for child in node.children:
        if child.type == "assignment_statement":
            for subchild in child.children:
                if subchild.type == "variable_list":
                    for var in subchild.children:
                        if var.type == "identifier":
                            name = _get_node_text(var)
                            # Only extract if it looks like a module (capital letter)
                            if name and name[0].isupper():
                                sym = Symbol(
                                    id=make_symbol_id("luau", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "variable"),
                                    stable_id=analyzer.compute_stable_id(
                                        node, kind="variable", name=name,
                                        qualified_name=_luau_scope_slot(node, name),
                                        file_stable_id=file_anchor,
                                    ),
                                    name=name,
                                    kind="variable",
                                    language="luau",
                                    path=rel_path,
                                    span=Span(
                                        start_line=node.start_point[0] + 1,
                                        end_line=node.end_point[0] + 1,
                                        start_col=node.start_point[1],
                                        end_col=node.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    meta={"local": True},
                                )
                                analysis.symbols.append(sym)
                                analysis.node_for_symbol[sym.id] = node
                                analysis.symbol_by_name[sym.name] = sym


def _extract_symbols_recursive(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer", file_anchor: str,
) -> None:
    """Recursively extract symbols from a syntax tree."""
    if node.type == "function_declaration":
        _extract_function(node, rel_path, analysis, analyzer, file_anchor)
    elif node.type == "type_definition":
        _extract_type(node, rel_path, analysis, analyzer, file_anchor)
    elif node.type == "variable_declaration":
        _extract_variable(node, rel_path, analysis, analyzer, file_anchor)

    # Recurse into children
    for child in node.children:
        _extract_symbols_recursive(child, rel_path, analysis, analyzer, file_anchor)


# A call's caller is the nearest enclosing declaration of these types that has
# a symbol. An anonymous ``function_definition`` has none, so a call inside a
# callback is credited to the named function around it.
_CALLER_NODE_TYPES = frozenset({"function_declaration"})


def _first_segment(call_name: str) -> str:
    """``Mod.sub:fn`` -> ``Mod``: the text before the first ``.`` / ``:``."""
    return call_name.split(".")[0].split(":")[0]


def _last_segment(call_name: str) -> str:
    """``Mod.sub:fn`` -> ``fn``: the text after the last ``.`` / ``:``."""
    return call_name.split(".")[-1].split(":")[-1]


def _call_name(node: "tree_sitter.Node") -> Optional[str]:
    """The callee text of a ``function_call``: ``fn``, ``Mod.fn`` or ``obj:fn``."""
    for child in node.children:
        if child.type in ("identifier", "dot_index_expression", "method_index_expression"):
            return _get_node_text(child)
    return None


def _resolve_callee(
    call_name: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """The symbol a call names, THIS file's first (WI-jusah).

    The full text (``Mod.fn``) is tried before the short name (``fn``), and at
    each step this file's definition before the run-wide registry, which keeps
    one symbol per name (the last file registered). A ``local function`` is
    file-scoped, so a same-named one elsewhere in the run is never the right
    answer while this file has its own; and a same-file short name must not
    pre-empt another file's exact ``Mod.fn``.
    """
    for key in dict.fromkeys((call_name, _last_segment(call_name))):
        for table in (local_symbols, global_symbols):
            sym = table.get(key)
            if sym is not None:
                return sym
    return None


def _extract_call_edges(
    root: "tree_sitter.Node", rel_path: str, decl_index: SymbolsAt,
    local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """One ``calls`` edge per non-builtin ``function_call`` in the tree.

    The caller is the innermost ``function_declaration`` containing the call,
    found by POSITION (``enclosing_declared_symbol`` over ``decl_index``), never
    by its name (WI-jusah, WI-kosar family): a name lookup in the run-wide
    registry handed back whichever same-named function registered last, and a
    nested function's calls went to the outer one. A call in no function comes
    from the file.
    """
    edges: list[Edge] = []
    for node in iter_tree(root):
        if node.type != "function_call":
            continue
        call_name = _call_name(node)
        if not call_name:
            continue
        if (
            _first_segment(call_name) in LUAU_BUILTINS
            or _last_segment(call_name) in LUAU_BUILTINS
        ):
            continue

        caller = enclosing_declared_symbol(node, decl_index, _CALLER_NODE_TYPES)
        src_id = caller.id if caller is not None else f"luau:{rel_path}:file"

        callee = _resolve_callee(call_name, local_symbols, global_symbols)
        if callee is not None:
            confidence = 1.0
            dst = callee.id
        else:
            confidence = 0.6
            # WI-bagon: 2-part 'unresolved:{name}' was malformed and fell
            # through ir._parse_dangling_id, producing boundary nodes with
            # language='unresolved' (sentinel leak; INV-nodij violation
            # observed on alertmanager). Use the established mainstream
            # 5-part shape (see csharp/rust/java/py) so the boundary node
            # carries language='luau'.
            dst = f"luau:unresolved:0-0:{call_name}:unresolved"

        edges.append(Edge.create(
            src=src_id,
            dst=dst,
            edge_type="calls",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="tree_sitter",
            confidence=confidence,
            evidence_lang="luau",
        ))
    return edges


# ---------------------------------------------------------------------------
# LuauAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class LuauAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Luau files using TreeSitterAnalyzer base class."""

    lang = "luau"
    file_patterns: ClassVar[list[str]] = ["*.luau"]
    language_pack_name = "luau"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Luau file."""
        analysis = FileAnalysis()
        # WI-bokab (v7): file-identity anchor for this file's symbols. ``rel_path`` is
        # the repo-relative path supplied by the base class. Folded into
        # compute_stable_id's containing slot so same-(kind, name) symbols in different
        # files hash distinctly (the luau producer carries scope in qualified_name and
        # passes no enclosing-scope containing today, so the fold is purely additive).
        file_anchor = self._file_anchor(rel_path)
        _extract_symbols_recursive(tree.root_node, rel_path, analysis, self, file_anchor)
        return analysis

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict[str, Symbol],
    ) -> None:
        """Register symbol by qualified name only.

        Last registration wins on a name clash. Pass 2 never consults the
        ``NameResolver``: a short-name lookup (``fn`` for ``Mod:fn``) is
        its own second probe of this table, after this file's symbols
        (``_resolve_callee``).
        """
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single Luau file.

        Callers are matched by position over every symbol of this file
        (``file_symbols``); callees by name, this file's first.
        """
        return _extract_call_edges(
            tree.root_node, rel_path,
            symbols_at(self.file_symbols(local_symbols)),
            local_symbols, global_symbols, run.execution_id,
        )


_analyzer = LuauAnalyzer()


def is_luau_tree_sitter_available() -> bool:
    """Check if tree-sitter-luau is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("luau", language_state="no_taxonomy_spec")  # WI-futin
def analyze_luau(repo_root: Path) -> AnalysisResult:
    """Analyze Luau files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
