"""Jsonnet configuration language analyzer.

This module analyzes Jsonnet files (.jsonnet, .libsonnet) using tree-sitter.
Jsonnet is a data templating language used for configuration management,
especially with Kubernetes (Ksonnet, Tanka), Grafana, and other systems.

How It Works
------------
- Pass 1: Collect symbols (local functions, local variables, object methods)
- Pass 2: Extract edges (function calls, imports)

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

import time
import uuid as uuid_module
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "jsonnet.tree_sitter"
PASS_VERSION = "0.1.0"

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
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    rel_path = str(path.relative_to(repo_root))
    return f"jsonnet:{rel_path}:{kind}:{name}"


def find_jsonnet_files(repo_root: Path) -> list[Path]:
    """Find all Jsonnet files in the repository."""
    patterns = ["**/*.jsonnet", "**/*.libsonnet"]
    files = []
    for pattern in patterns:
        files.extend(repo_root.glob(pattern))
    return sorted(set(files))


def is_jsonnet_tree_sitter_available() -> bool:
    """Check if tree-sitter-jsonnet is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("jsonnet")
        return True
    except Exception:  # pragma: no cover
        return False


class JsonnetAnalysisResult:
    """Result of Jsonnet analysis."""

    def __init__(
        self,
        symbols: list[Symbol] | None = None,
        edges: list[Edge] | None = None,
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ):
        self.symbols = symbols or []
        self.edges = edges or []
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


class JsonnetAnalyzer:
    """Analyzer for Jsonnet configuration files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_file: Optional[Path] = None
        self._local_functions: set[str] = set()  # Functions in current file

    def analyze(self) -> JsonnetAnalysisResult:
        """Analyze all Jsonnet files in the repository."""
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("jsonnet")
        jsonnet_files = find_jsonnet_files(self.repo_root)

        if not jsonnet_files:
            return JsonnetAnalysisResult()

        # Pass 1: Collect all symbols
        for path in jsonnet_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_file = path
                self._local_functions = set()
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in jsonnet_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_file = path
                # Rebuild local functions set for this file
                self._local_functions = {
                    s.name.split(".")[-1] for s in self.symbols
                    if s.path == str(path.relative_to(self.repo_root)) and s.kind == "function"
                }
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._run_id,
            version=PASS_VERSION,
            toolchain={"name": "jsonnet", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return JsonnetAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "bind":
            # Local binding - could be function or variable
            name = None
            is_function = False
            param_count = 0

            for child in node.children:
                if child.type == "id" and name is None:
                    # Only capture the first id (the function/variable name)
                    name = _get_node_text(child)
                elif child.type == "(":
                    # Has parentheses, so it's a function (even if no params)
                    is_function = True
                elif child.type == "params":
                    param_count = len([c for c in child.children if c.type == "param"])

            if name:
                rel_path = str(path.relative_to(self.repo_root))
                kind = "function" if is_function else "variable"

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, kind),
                    stable_id=_make_stable_id(path, self.repo_root, name, kind),
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
                self.symbols.append(sym)

                if kind == "function":
                    self._local_functions.add(name)

        elif node.type == "field":
            # Object field or method
            field_name = None
            has_params = False
            param_count = 0
            is_hidden = False  # :: vs :

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
                rel_path = str(path.relative_to(self.repo_root))
                kind = "method" if has_params else "field"

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, field_name, kind),
                    stable_id=_make_stable_id(path, self.repo_root, field_name, kind),
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
                self.symbols.append(sym)

        elif node.type == "import":
            # Import statement
            import_path = None
            for child in node.children:
                if child.type == "string":
                    for subchild in child.children:
                        if subchild.type == "string_content":
                            import_path = _get_node_text(subchild)
                            break

            if import_path:
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, import_path, "import"),
                    stable_id=_make_stable_id(path, self.repo_root, import_path, "import"),
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
                self.symbols.append(sym)

        # Recurse into children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "functioncall":
            # Function call
            call_name = None
            line = node.start_point[0] + 1

            for child in node.children:
                if child.type == "id":
                    call_name = _get_node_text(child)
                    break
                elif child.type == "fieldaccess":
                    # Method call like MyClass.compute or self.greet
                    call_name = _get_node_text(child)
                    break

            if call_name and not self._is_builtin(call_name):
                # Try to resolve to a known symbol
                base_name = call_name.split(".")[-1] if "." in call_name else call_name
                callee_id = self._symbol_registry.get(base_name)

                if callee_id:
                    confidence = 1.0
                    dst = callee_id
                else:
                    confidence = 0.6
                    dst = f"unresolved:{call_name}"

                rel_path = str(path.relative_to(self.repo_root))
                edge = Edge.create(
                    src=f"jsonnet:{rel_path}:file",
                    dst=dst,
                    edge_type="calls",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=self._run_id,
                    evidence_type="tree_sitter",
                    confidence=confidence,
                    evidence_lang="jsonnet",
                )
                self.edges.append(edge)

        elif node.type == "import":
            # Import edge
            import_path = None
            for child in node.children:
                if child.type == "string":
                    for subchild in child.children:
                        if subchild.type == "string_content":
                            import_path = _get_node_text(subchild)
                            break

            if import_path:
                rel_path = str(path.relative_to(self.repo_root))
                edge = Edge.create(
                    src=f"jsonnet:{rel_path}:file",
                    dst=f"jsonnet:import:{import_path}",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=self._run_id,
                    evidence_type="tree_sitter",
                    confidence=1.0,
                    evidence_lang="jsonnet",
                )
                self.edges.append(edge)

        # Recurse into children
        for child in node.children:
            self._extract_edges(child, path)

    def _is_builtin(self, name: str) -> bool:
        """Check if a name is a built-in function."""
        # Handle qualified names like std.format
        # Since "std" is in JSONNET_BUILTINS, std.* calls are caught by base_name check
        base_name = name.split(".")[0]
        return base_name in JSONNET_BUILTINS


def analyze_jsonnet(repo_root: Path) -> JsonnetAnalysisResult:
    """Analyze Jsonnet files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        JsonnetAnalysisResult containing symbols and edges
    """
    if not is_jsonnet_tree_sitter_available():
        warnings.warn(
            "Jsonnet analysis skipped: tree-sitter-jsonnet not available",
            UserWarning,
            stacklevel=2,
        )
        return JsonnetAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-jsonnet not available",
        )

    analyzer = JsonnetAnalyzer(repo_root)
    return analyzer.analyze()
