"""Jsonnet configuration language analyzer.

This module analyzes Jsonnet files (.jsonnet, .libsonnet) using tree-sitter.
Jsonnet is a data templating language used for configuration management,
especially with Kubernetes (Ksonnet, Tanka), Grafana, and other systems.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (local functions, local variables, object methods)
- Pass 2: Extract edges (function calls, imports)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Jsonnet-specific extraction
logic.

Symbol Types
------------
- function: Local function definitions (local add(a, b) = ...)
- variable: Local variable bindings (local x = ...)
- method: Object method definitions (greet():: "Hello")
- field: Object field definitions (name: "value")
- import: Import statements (import "file.libsonnet")

Edge Types
----------
- calls: Function/method invocations
- imports: Import relationships between files
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

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

PASS_ID = make_pass_id("jsonnet")

# Built-in Jsonnet functions to filter from edges
JSONNET_BUILTINS = frozenset({
    # Standard library functions
    "std", "self", "super", "$",
    # Type functions
    "type", "isArray", "isBoolean", "isFunction", "isNumber",
    "isObject", "isString", "length",
    # String functions
    "codepoint", "char", "substr", "findSubstr", "startsWith",
    "endsWith", "stripChars", "lstripChars", "rstripChars",
    "split", "splitLimit", "strReplace", "asciiUpper", "asciiLower",
    "stringChars", "format", "escapeStringBash", "escapeStringDollars",
    "escapeStringJson", "escapeStringPython", "escapeStringXml",
    # Numeric functions
    "abs", "sign", "max", "min", "pow", "exp", "log", "exponent",
    "mantissa", "floor", "ceil", "sqrt", "sin", "cos", "tan",
    "asin", "acos", "atan", "round", "mod", "clamp",
    # Array functions
    "makeArray", "count", "find", "map", "mapWithKey", "flatMap",
    "filter", "foldl", "foldr", "range", "repeat", "slice",
    "member", "sort", "uniq", "set", "setInter", "setUnion", "setDiff",
    "setMember", "all", "any", "sum", "avg", "contains", "remove",
    "removeAt", "reverse", "join", "lines", "deepJoin",
    # Object functions
    "get", "objectHas", "objectHasAll", "objectFields",
    "objectFieldsAll", "objectValues", "objectValuesAll",
    "objectKeysValues", "objectKeysValuesAll", "mapWithIndex",
    "prune", "equals", "mergePatch",
    # Encoding functions
    "manifestIni", "manifestPython", "manifestPythonVars",
    "manifestJsonEx", "manifestJson", "manifestYamlDoc",
    "manifestYamlStream", "manifestXmlJsonml", "manifestTomlEx",
    "parseJson", "parseYaml", "encodeUTF8", "decodeUTF8",
    "base64", "base64Decode", "base64DecodeBytes", "md5", "sha1",
    "sha256", "sha512", "sha3",
    # Other
    "assertEqual", "trace", "extVar", "native", "thisFile",
    "id", "objectHasEx",
    # Control
    "error", "assert",
})


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def find_jsonnet_files(repo_root: Path) -> list[Path]:
    """Find all Jsonnet files in the repository."""
    patterns = ["**/*.jsonnet", "**/*.libsonnet"]
    files = []
    for pattern in patterns:
        files.extend(find_files(repo_root, [pattern]))
    return sorted(set(files))


def _is_builtin(name: str) -> bool:
    """Check if a name is a built-in function."""
    base_name = name.split(".")[0]
    return base_name in JSONNET_BUILTINS


def _extract_symbols_recursive(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
    analyzer: "JsonnetAnalyzer",
) -> None:
    """Recursively extract symbols from a syntax tree."""
    if node.type == "bind":
        name = None
        is_function = False
        param_count = 0

        for child in node.children:
            if child.type == "id" and name is None:
                name = _get_node_text(child)
            elif child.type == "(":
                is_function = True
            elif child.type == "params":
                param_count = len([c for c in child.children if c.type == "param"])

        if name:
            kind = "function" if is_function else "variable"

            sym = Symbol(
                id=make_symbol_id("jsonnet", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, kind),
                stable_id=analyzer.compute_stable_id(node, kind=kind),
                name=name,
                kind=kind,
                language="jsonnet",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"param_count": param_count} if is_function else {},
            )
            analysis.symbols.append(sym)
            analysis.node_for_symbol[sym.id] = node
            analysis.symbol_by_name[sym.name] = sym

    elif node.type == "field":
        field_name = None
        has_params = False
        param_count = 0
        is_hidden = False

        for child in node.children:
            if child.type == "fieldname":
                for subchild in child.children:
                    if subchild.type == "id":
                        field_name = _get_node_text(subchild)
                        break
            elif child.type == "params":
                has_params = True
                param_count = len([c for c in child.children if c.type == "param"])
            elif child.type == "::":
                is_hidden = True

        if field_name:
            kind = "method" if has_params else "field"

            sym = Symbol(
                id=make_symbol_id("jsonnet", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, field_name, kind),
                stable_id=analyzer.compute_stable_id(node, kind=kind),
                name=field_name,
                kind=kind,
                language="jsonnet",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={
                    "hidden": is_hidden,
                    **({"param_count": param_count} if has_params else {}),
                },
            )
            analysis.symbols.append(sym)
            analysis.node_for_symbol[sym.id] = node
            analysis.symbol_by_name[sym.name] = sym

    elif node.type == "import":
        import_path = None
        for child in node.children:
            if child.type == "string":
                for subchild in child.children:
                    if subchild.type == "string_content":
                        import_path = _get_node_text(subchild)
                        break

        if import_path:
            sym = Symbol(
                id=make_symbol_id("jsonnet", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, import_path, "import"),
                stable_id=analyzer.compute_stable_id(node, kind="import"),
                name=import_path,
                kind="import",
                language="jsonnet",
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

    for child in node.children:
        _extract_symbols_recursive(child, rel_path, analysis, analyzer)


def _extract_edges_recursive(
    node: "tree_sitter.Node", rel_path: str,
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Recursively extract edges from a syntax tree."""
    if node.type == "functioncall":
        call_name = None
        line = node.start_point[0] + 1

        for child in node.children:
            if child.type == "id":
                call_name = _get_node_text(child)
                break
            elif child.type == "fieldaccess":
                call_name = _get_node_text(child)
                break

        if call_name and not _is_builtin(call_name):
            base_name = call_name.split(".")[-1] if "." in call_name else call_name
            callee_id = symbol_registry.get(base_name)

            if callee_id:
                confidence = 1.0
                dst = callee_id
            else:
                confidence = 0.6
                dst = f"unresolved:{call_name}"

            edge = Edge.create(
                src=f"jsonnet:{rel_path}:file",
                dst=dst,
                edge_type="calls",
                line=line,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="tree_sitter",
                confidence=confidence,
                evidence_lang="jsonnet",
            )
            edges.append(edge)

    elif node.type == "import":
        import_path = None
        for child in node.children:
            if child.type == "string":
                for subchild in child.children:
                    if subchild.type == "string_content":
                        import_path = _get_node_text(subchild)
                        break

        if import_path:
            edge = Edge.create(
                src=f"jsonnet:{rel_path}:file",
                dst=f"jsonnet:import:{import_path}",
                edge_type="imports",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="tree_sitter",
                confidence=1.0,
                evidence_lang="jsonnet",
            )
            edges.append(edge)

    for child in node.children:
        _extract_edges_recursive(child, rel_path, symbol_registry, run_id, edges)


# ---------------------------------------------------------------------------
# JsonnetAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class JsonnetAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Jsonnet configuration files using TreeSitterAnalyzer base class."""

    lang = "jsonnet"
    file_patterns: ClassVar[list[str]] = ["*.jsonnet", "*.libsonnet"]
    language_pack_name = "jsonnet"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Jsonnet file."""
        analysis = FileAnalysis()
        _extract_symbols_recursive(tree.root_node, rel_path, analysis, self)
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a single Jsonnet file."""
        edges: list[Edge] = []

        # Build symbol registry (name -> id) from global symbols
        symbol_registry: dict[str, str] = {}
        for sym_name, sym in global_symbols.items():
            symbol_registry[sym_name] = sym.id

        _extract_edges_recursive(
            tree.root_node, rel_path, symbol_registry, run.execution_id, edges,
        )
        return edges


_analyzer = JsonnetAnalyzer()


def is_jsonnet_tree_sitter_available() -> bool:
    """Check if tree-sitter-jsonnet is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("jsonnet")
def analyze_jsonnet(repo_root: Path) -> AnalysisResult:
    """Analyze Jsonnet files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
