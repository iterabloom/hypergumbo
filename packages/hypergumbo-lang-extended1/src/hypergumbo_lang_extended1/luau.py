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
- variable: Local variable declarations

Edge Types
----------
- calls: Function calls from one symbol to another
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer

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


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def find_luau_files(repo_root: Path) -> list[Path]:
    """Find all Luau (.luau) files in the repository.

    Only processes .luau files (Roblox's typed Lua variant).
    Vanilla .lua files are handled exclusively by the lua-v1 analyzer
    to avoid duplicate symbol extraction.
    """
    return sorted(find_files(repo_root, ["*.luau"]))


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


def _extract_function(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer",
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
            stable_id=analyzer.compute_stable_id(node, kind="function"),
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
        )
        analysis.symbols.append(sym)
        analysis.node_for_symbol[sym.id] = node
        analysis.symbol_by_name[sym.name] = sym


def _extract_type(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "LuauAnalyzer",
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
            stable_id=analyzer.compute_stable_id(node, kind="type"),
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
    analyzer: "LuauAnalyzer",
) -> None:
    """Extract a variable declaration."""
    # Only extract top-level module tables (e.g., local MyModule = {})
    # Skip simple local variables
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
                                    stable_id=analyzer.compute_stable_id(node, kind="variable"),
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
    analyzer: "LuauAnalyzer",
) -> None:
    """Recursively extract symbols from a syntax tree."""
    if node.type == "function_declaration":
        _extract_function(node, rel_path, analysis, analyzer)
    elif node.type == "type_definition":
        _extract_type(node, rel_path, analysis, analyzer)
    elif node.type == "variable_declaration":
        _extract_variable(node, rel_path, analysis, analyzer)

    # Recurse into children
    for child in node.children:
        _extract_symbols_recursive(child, rel_path, analysis, analyzer)


def _extract_function_call(
    node: "tree_sitter.Node", rel_path: str, current_function: Optional[str],
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Extract a function call edge."""
    call_name = None

    for child in node.children:
        if child.type == "identifier":
            call_name = _get_node_text(child)
            break
        elif child.type == "dot_index_expression":
            # Module.method or object.method
            call_name = _get_node_text(child)
            break
        elif child.type == "method_index_expression":
            # object:method
            call_name = _get_node_text(child)
            break

    if call_name:
        # Skip built-in functions
        base_name = call_name.split(".")[0] if "." in call_name else call_name
        base_name = base_name.split(":")[0] if ":" in base_name else base_name
        method_name = call_name.split(".")[-1] if "." in call_name else call_name
        method_name = method_name.split(":")[-1] if ":" in method_name else method_name

        if base_name in LUAU_BUILTINS or method_name in LUAU_BUILTINS:
            return

        # Determine source
        if current_function:
            src_id = symbol_registry.get(
                current_function, f"luau:{rel_path}:file"
            )
        else:  # pragma: no cover
            # Module-level call (rare in Luau)
            src_id = f"luau:{rel_path}:file"

        # Try to resolve the callee
        dst_id = symbol_registry.get(call_name)
        if not dst_id:
            # Try short name
            short_name = call_name.split(".")[-1] if "." in call_name else call_name
            short_name = short_name.split(":")[-1] if ":" in short_name else short_name
            dst_id = symbol_registry.get(short_name)

        if dst_id:
            confidence = 1.0
            dst = dst_id
        else:
            confidence = 0.6
            # WI-bagon: 2-part 'unresolved:{name}' was malformed and fell
            # through ir._parse_dangling_id, producing boundary nodes with
            # language='unresolved' (sentinel leak; INV-nodij violation
            # observed on alertmanager). Use the established mainstream
            # 5-part shape (see csharp/rust/java/py) so the boundary node
            # carries language='luau'.
            dst = f"luau:unresolved:0-0:{call_name}:unresolved"

        edge = Edge.create(
            src=src_id,
            dst=dst,
            edge_type="calls",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="tree_sitter",
            confidence=confidence,
            evidence_lang="luau",
        )
        edges.append(edge)


def _extract_calls_recursive(
    node: "tree_sitter.Node", rel_path: str, current_function: Optional[str],
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Extract function calls from a code block."""
    if node.type == "function_call":
        _extract_function_call(
            node, rel_path, current_function, symbol_registry, run_id, edges,
        )

    for child in node.children:
        _extract_calls_recursive(
            child, rel_path, current_function, symbol_registry, run_id, edges,
        )


def _extract_edges_recursive(
    node: "tree_sitter.Node", rel_path: str, current_function: Optional[str],
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Recursively extract edges from a syntax tree."""
    if node.type == "function_declaration":
        # Track current function context
        name = None
        for child in node.children:
            if child.type == "identifier" and name is None:
                name = _get_node_text(child)
                break
            elif child.type == "dot_index_expression":
                name = _get_node_text(child)
                break
            elif child.type == "method_index_expression":
                name = _get_node_text(child)
                break

        if name:
            # Extract calls from function body
            for child in node.children:
                if child.type == "block":
                    _extract_calls_recursive(
                        child, rel_path, name, symbol_registry, run_id, edges,
                    )
            return

    elif node.type == "function_call":  # pragma: no cover
        # Top-level function calls (rare in Luau, usually wrapped)
        _extract_function_call(
            node, rel_path, current_function, symbol_registry, run_id, edges,
        )

    # Recurse into children
    for child in node.children:
        _extract_edges_recursive(
            child, rel_path, current_function, symbol_registry, run_id, edges,
        )


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
        _extract_symbols_recursive(tree.root_node, rel_path, analysis, self)
        return analysis

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups
        across ``.`` and ``:`` separators.
        """
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single Luau file."""
        edges: list[Edge] = []

        # Build symbol registry (name -> id) from global symbols
        symbol_registry: dict[str, str] = {}
        for sym_name, sym in global_symbols.items():
            symbol_registry[sym_name] = sym.id

        _extract_edges_recursive(
            tree.root_node, rel_path, None, symbol_registry, run.execution_id, edges,
        )
        return edges


_analyzer = LuauAnalyzer()


def is_luau_tree_sitter_available() -> bool:
    """Check if tree-sitter-luau is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("luau")
def analyze_luau(repo_root: Path) -> AnalysisResult:
    """Analyze Luau files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
