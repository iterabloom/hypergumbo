"""Hack language analyzer.

This module analyzes Hack files (.hack, .php with <?hh header) using tree-sitter.
Hack is a statically-typed programming language developed by Meta (Facebook)
that runs on HHVM and is a dialect of PHP.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (classes, interfaces, traits, functions, methods)
- Pass 2: Extract edges (function calls, method calls, static calls)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Hack-specific extraction
logic.

Symbol Types
------------
- class: Class definitions
- interface: Interface definitions
- trait: Trait definitions
- function: Standalone functions
- method: Class/interface/trait methods
- namespace: Namespace declarations

Edge Types
----------
- calls: Function/method invocations
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

PASS_ID = make_pass_id("hack")

# Built-in Hack functions to filter from edges
HACK_BUILTINS = frozenset({
    # PHP/Hack built-ins
    "echo", "print", "var_dump", "print_r", "die", "exit",
    "isset", "empty", "unset", "array", "list",
    "strlen", "substr", "strpos", "str_replace", "strtolower", "strtoupper",
    "count", "sizeof", "array_merge", "array_push", "array_pop",
    "array_keys", "array_values", "array_map", "array_filter",
    "in_array", "array_search", "sort", "asort", "ksort",
    "json_encode", "json_decode", "serialize", "unserialize",
    "file_get_contents", "file_put_contents", "fopen", "fclose", "fread", "fwrite",
    "is_null", "is_array", "is_string", "is_int", "is_float", "is_bool", "is_object",
    "intval", "floatval", "strval", "boolval",
    "preg_match", "preg_replace", "preg_split",
    "explode", "implode", "trim", "ltrim", "rtrim",
    "time", "date", "strtotime", "mktime",
    "sprintf", "printf", "sscanf",
    "class_exists", "method_exists", "function_exists",
    "get_class", "get_parent_class", "instanceof",
    "throw", "new", "clone",
    # Hack-specific
    "invariant", "invariant_violation", "invariant_callback_register",
    "vec", "dict", "keyset", "tuple", "shape",
    "HH\\vec", "HH\\dict", "HH\\keyset",
    # Common superglobals
    "$this", "$_GET", "$_POST", "$_REQUEST", "$_SESSION", "$_COOKIE",
    "$_SERVER", "$_ENV", "$_FILES", "$GLOBALS",
})


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def find_hack_files(repo_root: Path) -> list[Path]:
    """Find all Hack files in the repository."""
    patterns = ["**/*.hack", "**/*.hh"]
    files = []
    for pattern in patterns:
        files.extend(find_files(repo_root, [pattern]))

    # Also check .php files for <?hh header
    for php_file in find_files(repo_root, ["*.php"]):
        try:
            content = php_file.read_text(errors="ignore")[:50]
            if content.startswith("<?hh"):
                files.append(php_file)
        except Exception:  # pragma: no cover
            pass

    return sorted(set(files))


def _is_builtin(name: str) -> bool:
    """Check if a name is a built-in function."""
    clean_name = name.split("->")[-1].split("::")[-1]
    if clean_name in HACK_BUILTINS:
        return True
    if name in HACK_BUILTINS:  # pragma: no cover
        return True
    if name.startswith("$this"):
        if "->" in name:
            return False
        return True  # pragma: no cover - $this alone isn't a call_expression
    return False


def _extract_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a parameters node."""
    params = []
    for child in node.children:
        if child.type == "parameter":
            for subchild in child.children:
                if subchild.type == "variable":
                    params.append(_get_node_text(subchild))
    return params


def _get_qualified_name(name: str, current_namespace: Optional[str]) -> str:
    """Get the fully qualified name including namespace."""
    if current_namespace:
        return f"{current_namespace}\\{name}"
    return name


def _extract_method_symbol(
    node: "tree_sitter.Node", rel_path: str, current_class: str,
    analyzer: "HackAnalyzer",
) -> Optional[Symbol]:
    """Extract a method declaration."""
    name = None
    visibility = "public"
    is_static = False
    params: list[str] = []
    return_type = None

    for child in node.children:
        if child.type == "visibility_modifier":
            visibility = _get_node_text(child)
        elif child.type == "static_modifier":
            is_static = True
        elif child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_params(child)
        elif child.type == "type_specifier":
            return_type = _get_node_text(child)

    if name and current_class:
        qualified_name = f"{current_class}::{name}"
        signature = f"{visibility} function {name}({', '.join(params)})"
        if return_type:
            signature += f": {return_type}"

        return Symbol(
            id=make_symbol_id("hack", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, "method"),
            stable_id=analyzer.compute_stable_id(node, kind="method"),
            name=qualified_name,
            kind="method",
            language="hack",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            signature=signature,
            meta={
                "visibility": visibility,
                "static": is_static,
                "param_count": len(params),
                "class": current_class,
            },
        )
    return None  # pragma: no cover


def _extract_members(
    node: "tree_sitter.Node", rel_path: str, current_class: str,
    analysis: FileAnalysis, analyzer: "HackAnalyzer",
) -> None:
    """Extract method declarations from a member_declarations node."""
    for child in node.children:
        if child.type == "method_declaration":
            sym = _extract_method_symbol(child, rel_path, current_class, analyzer)
            if sym:
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = child
                analysis.symbol_by_name[sym.name] = sym


def _extract_class_like(
    node: "tree_sitter.Node", rel_path: str, kind: str,
    current_namespace: Optional[str], analysis: FileAnalysis,
    analyzer: "HackAnalyzer",
) -> None:
    """Extract a class, interface, or trait declaration."""
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
            break

    if name:
        qualified_name = _get_qualified_name(name, current_namespace)

        sym = Symbol(
            id=make_symbol_id("hack", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, kind),
            stable_id=analyzer.compute_stable_id(node, kind=kind),
            name=qualified_name,
            kind=kind,
            language="hack",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
        )
        analysis.symbols.append(sym)
        analysis.node_for_symbol[sym.id] = node
        analysis.symbol_by_name[sym.name] = sym

        # Extract methods
        for child in node.children:
            if child.type == "member_declarations":
                _extract_members(child, rel_path, qualified_name, analysis, analyzer)


def _extract_symbols_recursive(
    node: "tree_sitter.Node", rel_path: str,
    current_namespace: Optional[str], analysis: FileAnalysis,
    analyzer: "HackAnalyzer",
) -> None:
    """Recursively extract symbols from a syntax tree."""
    if node.type == "namespace_declaration":
        for child in node.children:
            if child.type == "qualified_identifier":
                ns_name = _get_node_text(child)
                sym = Symbol(
                    id=make_symbol_id("hack", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, ns_name, "namespace"),
                    stable_id=analyzer.compute_stable_id(node, kind="namespace"),
                    name=ns_name,
                    kind="namespace",
                    language="hack",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[sym.name] = sym
                # Update namespace for children - store in import_aliases as context
                analysis.import_aliases["__namespace__"] = ns_name
                break

    elif node.type == "class_declaration":
        ns = analysis.import_aliases.get("__namespace__")
        _extract_class_like(node, rel_path, "class", ns, analysis, analyzer)

    elif node.type == "interface_declaration":
        ns = analysis.import_aliases.get("__namespace__")
        _extract_class_like(node, rel_path, "interface", ns, analysis, analyzer)

    elif node.type == "trait_declaration":
        ns = analysis.import_aliases.get("__namespace__")
        _extract_class_like(node, rel_path, "trait", ns, analysis, analyzer)

    elif node.type == "function_declaration":
        name = None
        params: list[str] = []
        return_type = None

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "parameters":
                params = _extract_params(child)
            elif child.type == "type_specifier":
                return_type = _get_node_text(child)

        if name:
            ns = analysis.import_aliases.get("__namespace__")
            qualified_name = _get_qualified_name(name, ns)
            signature = f"function {name}({', '.join(params)})"
            if return_type:
                signature += f": {return_type}"

            sym = Symbol(
                id=make_symbol_id("hack", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, "fn"),
                stable_id=analyzer.compute_stable_id(node, kind="fn"),
                name=qualified_name,
                kind="function",
                language="hack",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=signature,
                meta={"param_count": len(params)},
            )
            analysis.symbols.append(sym)
            analysis.node_for_symbol[sym.id] = node
            analysis.symbol_by_name[sym.name] = sym

    for child in node.children:
        _extract_symbols_recursive(child, rel_path, current_namespace, analysis, analyzer)


def _find_enclosing_method(node: "tree_sitter.Node") -> Optional[str]:
    """Find the name of the enclosing method."""
    current = node.parent
    while current:
        if current.type == "method_declaration":
            for child in current.children:
                if child.type == "identifier":
                    return _get_node_text(child)
        current = current.parent
    return None  # pragma: no cover


def _resolve_call(
    call_name: str, symbol_registry: dict[str, str],
    current_class: Optional[str], current_namespace: Optional[str],
) -> Optional[str]:
    """Resolve a call name to a symbol ID."""
    if call_name in symbol_registry:
        return symbol_registry[call_name]

    if call_name.startswith("$this->"):
        method_name = call_name.split("->")[-1]
        if current_class:
            qualified = f"{current_class}::{method_name}"
            if qualified in symbol_registry:
                return symbol_registry[qualified]

    if "::" in call_name:  # pragma: no cover
        if current_namespace:
            qualified = f"{current_namespace}\\{call_name}"
            if qualified in symbol_registry:
                return symbol_registry[qualified]
        if call_name in symbol_registry:
            return symbol_registry[call_name]

    if current_namespace and "\\" not in call_name:  # pragma: no cover
        qualified = f"{current_namespace}\\{call_name}"
        if qualified in symbol_registry:
            return symbol_registry[qualified]

    return None


def _extract_edges_recursive(
    node: "tree_sitter.Node", rel_path: str,
    current_namespace: Optional[str], current_class: Optional[str],
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Recursively extract edges from a syntax tree."""
    if node.type == "namespace_declaration":
        for child in node.children:
            if child.type == "qualified_identifier":
                current_namespace = _get_node_text(child)
                break

    elif node.type in ("class_declaration", "interface_declaration", "trait_declaration"):
        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                old_class = current_class
                current_class = _get_qualified_name(name, current_namespace)
                for subchild in node.children:
                    _extract_edges_recursive(
                        subchild, rel_path, current_namespace, current_class,
                        symbol_registry, run_id, edges,
                    )
                current_class = old_class
                return

    elif node.type == "call_expression":
        call_name = None
        line = node.start_point[0] + 1

        for child in node.children:
            if child.type == "qualified_identifier":
                call_name = _get_node_text(child)
                break
            elif child.type == "selection_expression":
                call_name = _get_node_text(child)
                break
            elif child.type == "scoped_identifier":
                call_name = _get_node_text(child)
                break

        if call_name and not _is_builtin(call_name):
            callee_id = _resolve_call(call_name, symbol_registry, current_class, current_namespace)

            if callee_id:
                confidence = 1.0
                dst = callee_id
            else:
                confidence = 0.6
                dst = f"unresolved:{call_name}"

            caller_id = f"hack:{rel_path}:file"
            if current_class:
                method_name = _find_enclosing_method(node)
                if method_name:
                    caller_id = symbol_registry.get(
                        f"{current_class}::{method_name}",
                        caller_id
                    )

            edge = Edge.create(
                src=caller_id,
                dst=dst,
                edge_type="calls",
                line=line,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="tree_sitter",
                confidence=confidence,
                evidence_lang="hack",
            )
            edges.append(edge)

    for child in node.children:
        _extract_edges_recursive(
            child, rel_path, current_namespace, current_class,
            symbol_registry, run_id, edges,
        )


# ---------------------------------------------------------------------------
# HackAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class HackAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Hack source files using TreeSitterAnalyzer base class."""

    lang = "hack"
    file_patterns: ClassVar[list[str]] = ["*.hack", "*.hh"]
    language_pack_name = "hack"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Hack file."""
        analysis = FileAnalysis()
        _extract_symbols_recursive(tree.root_node, rel_path, None, analysis, self)
        return analysis

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol by name and short name for cross-file resolution."""
        global_symbols[symbol.name] = symbol
        short_name = symbol.name.split("\\")[-1]
        if short_name not in global_symbols:
            global_symbols[short_name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single Hack file."""
        edges: list[Edge] = []

        # Build symbol registry (name -> id) from global symbols
        symbol_registry: dict[str, str] = {}
        for sym_name, sym in global_symbols.items():
            symbol_registry[sym_name] = sym.id

        # Get namespace from import_aliases context
        current_namespace = import_aliases.get("__namespace__")

        _extract_edges_recursive(
            tree.root_node, rel_path, current_namespace, None,
            symbol_registry, run.execution_id, edges,
        )
        return edges


_analyzer = HackAnalyzer()


def is_hack_tree_sitter_available() -> bool:
    """Check if tree-sitter-hack is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("hack")
def analyze_hack(repo_root: Path) -> AnalysisResult:
    """Analyze Hack files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
