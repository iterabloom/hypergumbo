"""Hack language analyzer.

This module analyzes Hack files (.hack, .php with <?hh header) using tree-sitter.
Hack is a statically-typed programming language developed by Meta (Facebook)
that runs on HHVM and is a dialect of PHP.

How It Works
------------
- Pass 1: Collect symbols (classes, interfaces, traits, functions, methods)
- Pass 2: Extract edges (function calls, method calls, static calls)

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

import time
import uuid as uuid_module
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "hack.tree_sitter"
PASS_VERSION = "0.1.0"

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
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    rel_path = str(path.relative_to(repo_root))
    return f"hack:{rel_path}:{kind}:{name}"


def find_hack_files(repo_root: Path) -> list[Path]:
    """Find all Hack files in the repository."""
    patterns = ["**/*.hack", "**/*.hh"]
    files = []
    for pattern in patterns:
        files.extend(repo_root.glob(pattern))

    # Also check .php files for <?hh header
    for php_file in repo_root.glob("**/*.php"):
        try:
            content = php_file.read_text(errors="ignore")[:50]
            if content.startswith("<?hh"):
                files.append(php_file)
        except Exception:  # pragma: no cover
            pass

    return sorted(set(files))


def is_hack_tree_sitter_available() -> bool:
    """Check if tree-sitter-hack is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("hack")
        return True
    except Exception:  # pragma: no cover
        return False


class HackAnalysisResult:
    """Result of Hack analysis."""

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


class HackAnalyzer:
    """Analyzer for Hack source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_namespace: Optional[str] = None
        self._current_class: Optional[str] = None

    def analyze(self) -> HackAnalysisResult:
        """Analyze all Hack files in the repository."""
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("hack")
        hack_files = find_hack_files(self.repo_root)

        if not hack_files:
            return HackAnalysisResult()

        # Pass 1: Collect all symbols
        for path in hack_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_namespace = None
                self._current_class = None
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id
            # Also register short name for unqualified lookups
            short_name = sym.name.split("\\")[-1]
            if short_name not in self._symbol_registry:
                self._symbol_registry[short_name] = sym.id

        # Pass 2: Extract edges
        for path in hack_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_namespace = None
                self._current_class = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._run_id,
            version=PASS_VERSION,
            toolchain={"name": "hack", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return HackAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _get_qualified_name(self, name: str) -> str:
        """Get the fully qualified name including namespace."""
        if self._current_namespace:
            return f"{self._current_namespace}\\{name}"
        return name

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "namespace_declaration":
            # Namespace declaration
            for child in node.children:
                if child.type == "qualified_identifier":
                    self._current_namespace = _get_node_text(child)
                    rel_path = str(path.relative_to(self.repo_root))
                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, self._current_namespace, "namespace"),
                        stable_id=_make_stable_id(path, self.repo_root, self._current_namespace, "namespace"),
                        name=self._current_namespace,
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
                    self.symbols.append(sym)
                    break

        elif node.type == "class_declaration":
            self._extract_class_like(node, path, "class")

        elif node.type == "interface_declaration":
            self._extract_class_like(node, path, "interface")

        elif node.type == "trait_declaration":
            self._extract_class_like(node, path, "trait")

        elif node.type == "function_declaration":
            # Standalone function
            name = None
            params = []
            return_type = None

            for child in node.children:
                if child.type == "identifier":
                    name = _get_node_text(child)
                elif child.type == "parameters":
                    params = self._extract_params(child)
                elif child.type == "type_specifier":
                    return_type = _get_node_text(child)

            if name:
                qualified_name = self._get_qualified_name(name)
                rel_path = str(path.relative_to(self.repo_root))
                signature = f"function {name}({', '.join(params)})"
                if return_type:
                    signature += f": {return_type}"

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
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
                self.symbols.append(sym)

        # Recurse into children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_class_like(
        self, node: "tree_sitter.Node", path: Path, kind: str
    ) -> None:
        """Extract a class, interface, or trait declaration."""
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break

        if name:
            qualified_name = self._get_qualified_name(name)
            rel_path = str(path.relative_to(self.repo_root))

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, kind),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, kind),
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
            self.symbols.append(sym)

            # Extract methods
            old_class = self._current_class
            self._current_class = qualified_name
            for child in node.children:
                if child.type == "member_declarations":
                    self._extract_members(child, path)
            self._current_class = old_class

    def _extract_members(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract method declarations from a member_declarations node."""
        for child in node.children:
            if child.type == "method_declaration":
                self._extract_method(child, path)

    def _extract_method(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a method declaration."""
        name = None
        visibility = "public"
        is_static = False
        params = []
        return_type = None

        for child in node.children:
            if child.type == "visibility_modifier":
                visibility = _get_node_text(child)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "identifier":
                name = _get_node_text(child)
            elif child.type == "parameters":
                params = self._extract_params(child)
            elif child.type == "type_specifier":
                return_type = _get_node_text(child)

        if name and self._current_class:
            qualified_name = f"{self._current_class}::{name}"
            rel_path = str(path.relative_to(self.repo_root))
            signature = f"{visibility} function {name}({', '.join(params)})"
            if return_type:
                signature += f": {return_type}"

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, "method"),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, "method"),
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
                    "class": self._current_class,
                },
            )
            self.symbols.append(sym)

    def _extract_params(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a parameters node."""
        params = []
        for child in node.children:
            if child.type == "parameter":
                for subchild in child.children:
                    if subchild.type == "variable":
                        params.append(_get_node_text(subchild))
        return params

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "namespace_declaration":
            # Update current namespace for edge extraction
            for child in node.children:
                if child.type == "qualified_identifier":
                    self._current_namespace = _get_node_text(child)
                    break

        elif node.type in ("class_declaration", "interface_declaration", "trait_declaration"):
            # Track current class for method resolution
            for child in node.children:
                if child.type == "identifier":
                    name = _get_node_text(child)
                    old_class = self._current_class
                    self._current_class = self._get_qualified_name(name)
                    for subchild in node.children:
                        self._extract_edges(subchild, path)
                    self._current_class = old_class
                    return

        elif node.type == "call_expression":
            # Function/method call
            call_name = None
            line = node.start_point[0] + 1

            for child in node.children:
                if child.type == "qualified_identifier":
                    # Regular function call: helper(42)
                    call_name = _get_node_text(child)
                    break
                elif child.type == "selection_expression":
                    # Method call: $this->validate($x) or $obj->method()
                    call_name = _get_node_text(child)
                    break
                elif child.type == "scoped_identifier":
                    # Static call: User::find(1)
                    call_name = _get_node_text(child)
                    break

            if call_name and not self._is_builtin(call_name):
                # Try to resolve to a known symbol
                callee_id = self._resolve_call(call_name)

                if callee_id:
                    confidence = 1.0
                    dst = callee_id
                else:
                    confidence = 0.6
                    dst = f"unresolved:{call_name}"

                # Determine caller
                caller_id = f"hack:{path.relative_to(self.repo_root)}:file"
                if self._current_class:
                    # Try to find enclosing method
                    method_name = self._find_enclosing_method(node)
                    if method_name:
                        caller_id = self._symbol_registry.get(
                            f"{self._current_class}::{method_name}",
                            caller_id
                        )

                edge = Edge.create(
                    src=caller_id,
                    dst=dst,
                    edge_type="calls",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=self._run_id,
                    evidence_type="tree_sitter",
                    confidence=confidence,
                    evidence_lang="hack",
                )
                self.edges.append(edge)

        # Recurse into children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_method(self, node: "tree_sitter.Node") -> Optional[str]:
        """Find the name of the enclosing method."""
        current = node.parent
        while current:
            if current.type == "method_declaration":
                for child in current.children:
                    if child.type == "identifier":
                        return _get_node_text(child)
            current = current.parent
        return None  # pragma: no cover

    def _resolve_call(self, call_name: str) -> Optional[str]:
        """Resolve a call name to a symbol ID."""
        # Try direct lookup
        if call_name in self._symbol_registry:
            return self._symbol_registry[call_name]

        # Handle $this->method() calls
        if call_name.startswith("$this->"):
            method_name = call_name.split("->")[-1]
            if self._current_class:
                qualified = f"{self._current_class}::{method_name}"
                if qualified in self._symbol_registry:
                    return self._symbol_registry[qualified]

        # Handle static calls Class::method()
        # Note: Usually resolved via short name registration above
        if "::" in call_name:  # pragma: no cover
            # Try with namespace
            if self._current_namespace:
                qualified = f"{self._current_namespace}\\{call_name}"
                if qualified in self._symbol_registry:
                    return self._symbol_registry[qualified]
            # Try direct
            if call_name in self._symbol_registry:
                return self._symbol_registry[call_name]

        # Try with namespace prefix for unqualified calls
        # Note: Usually resolved via short name registration above
        if self._current_namespace and "\\" not in call_name:  # pragma: no cover
            qualified = f"{self._current_namespace}\\{call_name}"
            if qualified in self._symbol_registry:
                return self._symbol_registry[qualified]

        return None

    def _is_builtin(self, name: str) -> bool:
        """Check if a name is a built-in function."""
        # Clean the name for comparison (extract base name from method/static calls)
        clean_name = name.split("->")[-1].split("::")[-1]
        if clean_name in HACK_BUILTINS:
            return True
        # Defensive: check full name if clean_name didn't match
        if name in HACK_BUILTINS:  # pragma: no cover
            return True
        # Check for $this which is always builtin (but method calls on $this are not)
        if name.startswith("$this"):
            if "->" in name:
                return False
            return True  # pragma: no cover - $this alone isn't a call_expression
        return False


def analyze_hack(repo_root: Path) -> HackAnalysisResult:
    """Analyze Hack files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        HackAnalysisResult containing symbols and edges
    """
    if not is_hack_tree_sitter_available():
        warnings.warn(
            "Hack analysis skipped: tree-sitter-hack not available",
            UserWarning,
            stacklevel=2,
        )
        return HackAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-hack not available",
        )

    analyzer = HackAnalyzer(repo_root)
    return analyzer.analyze()
