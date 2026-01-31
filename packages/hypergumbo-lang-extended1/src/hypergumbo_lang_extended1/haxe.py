"""Haxe language analyzer using tree-sitter.

This module provides static analysis for Haxe source code, extracting symbols
(classes, interfaces, functions) and edges (calls, inheritance).

Haxe is a high-level, cross-platform programming language and compiler that
can compile to many target platforms including JavaScript, C++, C#, Java,
Python, Lua, PHP, Flash, and HashLink bytecode.

Implementation approach:
- Uses tree-sitter-language-pack for Haxe grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles classes, interfaces, functions, and method calls

Key constructs extracted:
- class Name { ... } - class definitions
- interface Name { ... } - interface definitions
- function name(args): Type - function definitions
- name(args) - function calls
- obj.method(args) - method calls
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "haxe.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class HaxeAnalysisResult:
    """Result of analyzing Haxe files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_haxe_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Haxe support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("haxe")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_haxe_files(root: Path) -> Iterator[Path]:
    """Find all Haxe files in the given directory."""
    for path in root.rglob("*.hx"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Haxe symbol."""
    rel_path = path.relative_to(repo_root)
    return f"haxe:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a function_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
        elif child.type == "new":
            return "new"
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a function_declaration node."""
    params = []
    for child in node.children:
        if child.type == "function_arg":
            for arg_child in child.children:
                if arg_child.type == "identifier":
                    params.append(_get_node_text(arg_child))
                    break
    return params


def _get_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Get the return type from a function_declaration node."""
    # Find the type node after the closing parenthesis
    found_paren = False
    for child in node.children:
        if child.type == ")":
            found_paren = True
        elif found_paren and child.type == "type":
            # Get the identifier from the type
            for type_child in child.children:
                if type_child.type == "identifier":
                    return _get_node_text(type_child)
            return _get_node_text(child)  # pragma: no cover
    return None


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a node has public visibility."""
    for child in node.children:
        if child.type == "public":
            return True
    return False


def _is_static(node: "tree_sitter.Node") -> bool:
    """Check if a function is static."""
    for child in node.children:
        if child.type == "static":
            return True
    return False


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a call_expression node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
        elif child.type == "member_expression":
            # Get the last identifier (the method name)
            for member_child in reversed(child.children):
                if member_child.type == "identifier":
                    return _get_node_text(member_child)
    return None  # pragma: no cover


class HaxeAnalyzer:
    """Analyzer for Haxe source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> HaxeAnalysisResult:
        """Analyze all Haxe files in the repository."""
        if not is_haxe_tree_sitter_available():
            warnings.warn(
                "Haxe analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return HaxeAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("haxe")
        haxe_files = list(find_haxe_files(self.repo_root))

        if not haxe_files:
            return HaxeAnalysisResult()

        # Pass 1: Collect all symbols
        for path in haxe_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path, None)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in haxe_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "haxe", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return HaxeAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, current_class: Optional[str]
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "class_declaration":
            # Class definition
            name = _get_identifier(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))

                # Check for abstract modifier
                is_abstract = any(c.type == "abstract" for c in node.children)

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "class"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "class"),
                    name=name,
                    kind="class",
                    language="haxe",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_abstract": is_abstract},
                )
                self.symbols.append(sym)

                # Process children with current class context
                for child in node.children:
                    self._extract_symbols(child, path, name)
                return

        elif node.type == "interface_declaration":
            # Interface definition
            name = _get_identifier(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "interface"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "interface"),
                    name=name,
                    kind="interface",
                    language="haxe",
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

                # Process children with interface context
                for child in node.children:
                    self._extract_symbols(child, path, name)
                return

        elif node.type == "function_declaration":
            # Function definition
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                return_type = _get_return_type(node)
                is_public = _is_public(node)
                is_static = _is_static(node)
                rel_path = str(path.relative_to(self.repo_root))

                # Build qualified name if inside a class
                qualified_name = f"{current_class}.{name}" if current_class else name

                # Build signature
                type_str = return_type if return_type else "Void"
                signature = f"function {name}({', '.join(params)}): {type_str}"

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    name=qualified_name,
                    kind="function",
                    language="haxe",
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
                        "param_count": len(params),
                        "is_public": is_public,
                        "is_static": is_static,
                        "class": current_class,
                    },
                )
                self.symbols.append(sym)
            return  # Don't recurse into function bodies for symbol extraction

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path, current_class)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "call_expression":
            # Skip constructor calls (new ClassName())
            has_new = any(c.type == "new" for c in node.children)
            if not has_new:
                call_name = _get_call_name(node)
                if call_name:
                    # Skip built-in functions
                    builtins = {
                        # Standard library
                        "trace", "haxe", "Type",
                        # Math
                        "Math", "abs", "floor", "ceil", "round", "sqrt",
                        "sin", "cos", "tan", "min", "max", "pow", "log",
                        # String
                        "String", "charAt", "charCodeAt", "indexOf",
                        "lastIndexOf", "split", "substr", "substring",
                        "toLowerCase", "toUpperCase", "toString",
                        # Array
                        "Array", "push", "pop", "shift", "unshift",
                        "concat", "join", "slice", "splice", "sort",
                        "reverse", "filter", "map", "length",
                        # Std
                        "Std", "int", "parseFloat", "parseInt", "is",
                        "string", "random",
                        # Lambda
                        "Lambda", "array", "exists", "find",
                    }

                    if call_name not in builtins:
                        caller_id, enclosing_class = self._find_enclosing_context(node, path)
                        if caller_id:
                            # Try qualified name first (ClassName.methodName)
                            callee_id = None
                            if enclosing_class:
                                qualified_name = f"{enclosing_class}.{call_name}"
                                callee_id = self._symbol_registry.get(qualified_name)

                            # Fall back to unqualified name
                            if callee_id is None:
                                callee_id = self._symbol_registry.get(call_name)

                            confidence = 1.0 if callee_id else 0.6
                            if callee_id is None:
                                callee_id = f"haxe:unresolved:{call_name}"

                            line = node.start_point[0] + 1
                            edge = Edge.create(
                                src=caller_id,
                                dst=callee_id,
                                edge_type="calls",
                                line=line,
                                origin=PASS_ID,
                                origin_run_id=self._run_id,
                                evidence_type="ast_call_direct",
                                confidence=confidence,
                                evidence_lang="haxe",
                            )
                            self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_context(
        self, node: "tree_sitter.Node", path: Path
    ) -> tuple[Optional[str], Optional[str]]:
        """Find the enclosing function and class for a node.

        Returns:
            Tuple of (function_id, class_name). function_id is the stable ID
            of the enclosing function, class_name is the name of the enclosing
            class (if any).
        """
        current = node.parent
        func_name = None
        class_name = None

        while current is not None:
            if current.type == "function_declaration":
                if func_name is None:
                    func_name = _get_function_name(current)
            elif current.type in ("class_declaration", "interface_declaration"):
                if class_name is None:
                    class_name = _get_identifier(current)
            current = current.parent

        if func_name:
            qualified_name = f"{class_name}.{func_name}" if class_name else func_name
            func_id = _make_stable_id(path, self.repo_root, qualified_name, "fn")
            return func_id, class_name
        return None, class_name  # pragma: no cover


def analyze_haxe(repo_root: Path) -> HaxeAnalysisResult:
    """Analyze Haxe source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        HaxeAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = HaxeAnalyzer(repo_root)
    return analyzer.analyze()
