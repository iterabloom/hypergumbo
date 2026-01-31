"""PureScript language analyzer using tree-sitter.

This module provides static analysis for PureScript source code, extracting symbols
(modules, functions, data types, classes) and edges (calls, imports).

PureScript is a strongly-typed functional programming language that compiles to
JavaScript. It features a powerful type system inspired by Haskell, with support
for type classes, row polymorphism, and algebraic data types.

Implementation approach:
- Uses tree-sitter-language-pack for PureScript grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles modules, functions, data types, type aliases, and type classes

Key constructs extracted:
- module Name where - module definition
- function name :: Type - function with type signature
- data Name = ... - algebraic data types
- type Name = ... - type aliases
- class Name ... where - type classes
- instance ... - class instances
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "purescript.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class PureScriptAnalysisResult:
    """Result of analyzing PureScript files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_purescript_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with PureScript support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("purescript")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_purescript_files(root: Path) -> Iterator[Path]:
    """Find all PureScript files in the given directory."""
    for path in root.rglob("*.purs"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a PureScript symbol."""
    rel_path = path.relative_to(repo_root)
    return f"purescript:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_module_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the module name from a purescript or qualified_module node."""
    for child in node.children:
        if child.type == "qualified_module":
            return _get_node_text(child)
        elif child.type == "module" and child.parent and child.parent.type == "qualified_module":
            return _get_node_text(child)  # pragma: no cover
    return None  # pragma: no cover


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a function or signature node."""
    for child in node.children:
        if child.type == "variable":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_data_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type name from a data or type_alias declaration."""
    # Skip the keyword child (first "type" or "data" in type_alias/data nodes)
    # and find the actual type name
    for child in node.children:
        if child.type in ("type", "data"):
            text = _get_node_text(child)
            if text in ("type", "data"):
                # This is the keyword, skip it
                continue
            return text
    return None  # pragma: no cover


def _get_class_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the class name from a class_declaration."""
    for child in node.children:
        if child.type == "class_head":
            # First word in class_head is the class name
            text = _get_node_text(child)
            return text.split()[0] if text else None
    return None  # pragma: no cover


def _get_type_signature(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type signature from a signature node."""
    for child in node.children:
        if child.type in ("type_infix", "type_apply", "type"):
            return _get_node_text(child)
    return None


def _get_instance_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the instance name from a class_instance node."""
    for child in node.children:
        if child.type == "instance_name":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from an exp_apply node."""
    for child in node.children:
        if child.type == "variable":
            return _get_node_text(child)  # pragma: no cover
        elif child.type == "exp_name":
            # Qualified name like Module.function
            return _get_node_text(child)
    return None  # pragma: no cover


class PureScriptAnalyzer:
    """Analyzer for PureScript source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_module: Optional[str] = None

    def analyze(self) -> PureScriptAnalysisResult:
        """Analyze all PureScript files in the repository."""
        if not is_purescript_tree_sitter_available():
            warnings.warn(
                "PureScript analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return PureScriptAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("purescript")
        ps_files = list(find_purescript_files(self.repo_root))

        if not ps_files:
            return PureScriptAnalysisResult()

        # Pass 1: Collect all symbols
        for path in ps_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_module = None
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in ps_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_module = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "purescript", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return PureScriptAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "purescript":
            # Module declaration
            mod_name = _get_module_name(node)
            if mod_name:
                self._current_module = mod_name
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, mod_name, "module"),
                    stable_id=_make_stable_id(path, self.repo_root, mod_name, "module"),
                    name=mod_name,
                    kind="module",
                    language="purescript",
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

        elif node.type == "function":
            # Function definition
            name = _get_function_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                    name=qualified_name,
                    kind="function",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)
            return  # Don't recurse into function bodies

        elif node.type == "signature":
            # Type signature - we'll use this to enhance function info
            # but create symbol here for standalone signatures
            name = _get_function_name(node)
            type_sig = _get_type_signature(node)
            if name and type_sig:
                # Check if we already have a function with this name
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name
                existing = next((s for s in self.symbols if s.name == qualified_name), None)
                if existing:  # pragma: no cover
                    # Update signature (rare: signature after implementation)
                    existing.signature = type_sig
                else:
                    # Create symbol for signature without implementation
                    rel_path = str(path.relative_to(self.repo_root))
                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                        stable_id=_make_stable_id(path, self.repo_root, qualified_name, "fn"),
                        name=qualified_name,
                        kind="function",
                        language="purescript",
                        path=rel_path,
                        span=Span(
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        signature=type_sig,
                        meta={"module": self._current_module},
                    )
                    self.symbols.append(sym)

        elif node.type == "data":
            # Data type definition
            name = _get_data_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                # Count constructors
                constructors = [c for c in node.children if c.type == "constructor"]

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "type"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "type"),
                    name=qualified_name,
                    kind="type",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"constructor_count": len(constructors), "module": self._current_module},
                )
                self.symbols.append(sym)

        elif node.type == "type_alias":
            # Type alias
            name = _get_data_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "type_alias"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "type_alias"),
                    name=qualified_name,
                    kind="type_alias",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)

        elif node.type == "class_declaration":
            # Type class
            name = _get_class_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "class"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "class"),
                    name=qualified_name,
                    kind="class",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)

        elif node.type == "class_instance":
            # Instance declaration
            name = _get_instance_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                qualified_name = f"{self._current_module}.{name}" if self._current_module else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "instance"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "instance"),
                    name=qualified_name,
                    kind="instance",
                    language="purescript",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"module": self._current_module},
                )
                self.symbols.append(sym)

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "purescript":
            mod_name = _get_module_name(node)
            if mod_name:
                self._current_module = mod_name

        elif node.type == "exp_apply":
            # Function application
            call_name = _get_call_name(node)
            if call_name:
                # Skip built-in functions
                builtins = {
                    # Common functions
                    "log", "pure", "bind", "map", "apply",
                    "show", "eq", "compare", "append",
                    # Effect
                    "liftEffect", "runEffect",
                    # Common operators used as functions
                    "add", "sub", "mul", "div", "mod",
                    "negate", "not", "and", "or",
                }

                if call_name not in builtins:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        # Try qualified name first
                        callee_id = self._symbol_registry.get(call_name)
                        if callee_id is None and self._current_module:
                            qualified = f"{self._current_module}.{call_name}"
                            callee_id = self._symbol_registry.get(qualified)

                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"purescript:unresolved:{call_name}"

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
                            evidence_lang="purescript",
                        )
                        self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_function(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing function for a node."""
        current = node.parent
        while current is not None:
            if current.type == "function":
                name = _get_function_name(current)
                if name:
                    qualified = f"{self._current_module}.{name}" if self._current_module else name
                    return _make_stable_id(path, self.repo_root, qualified, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_purescript(repo_root: Path) -> PureScriptAnalysisResult:
    """Analyze PureScript source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        PureScriptAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = PureScriptAnalyzer(repo_root)
    return analyzer.analyze()
