"""Fennel language analyzer using tree-sitter.

This module provides static analysis for Fennel source code, extracting symbols
(functions, variables) and edges (calls).

Fennel is a lisp that compiles to Lua. It combines Lua's simplicity, speed, and
reach with a rich macro system and expressive syntax. It's commonly used for
game development and embedded scripting.

Implementation approach:
- Uses tree-sitter-language-pack for Fennel grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Fennel-specific constructs like fn, local, and list calls

Key constructs extracted:
- (fn name [args] body) - function definitions
- (local name value) - variable definitions
- (name args) - function calls (lists)
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "fennel.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class FennelAnalysisResult:
    """Result of analyzing Fennel files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_fennel_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Fennel support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("fennel")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_fennel_files(root: Path) -> Iterator[Path]:
    """Find all Fennel files in the given directory."""
    for path in root.rglob("*.fnl"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Fennel symbol."""
    rel_path = path.relative_to(repo_root)
    return f"fennel:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_function_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from an fn node."""
    for child in node.children:
        if child.type == "symbol":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_function_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameters from an fn node."""
    params = []
    for child in node.children:
        if child.type == "parameters":
            for param_child in child.children:
                if param_child.type == "binding":
                    for binding_child in param_child.children:
                        if binding_child.type == "symbol":
                            params.append(_get_node_text(binding_child))
    return params


def _get_local_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the variable name from a local node."""
    for child in node.children:
        if child.type == "binding":
            for binding_child in child.children:
                if binding_child.type == "symbol":
                    return _get_node_text(binding_child)
    return None  # pragma: no cover


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function name from a list call."""
    children = [c for c in node.children if c.type not in ("(", ")")]
    if children and children[0].type == "symbol":
        return _get_node_text(children[0])
    return None  # pragma: no cover


class FennelAnalyzer:
    """Analyzer for Fennel source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> FennelAnalysisResult:
        """Analyze all Fennel files in the repository."""
        if not is_fennel_tree_sitter_available():
            warnings.warn(
                "Fennel analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return FennelAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("fennel")
        fennel_files = list(find_fennel_files(self.repo_root))

        if not fennel_files:
            return FennelAnalysisResult()

        # Pass 1: Collect all symbols
        for path in fennel_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges
        for path in fennel_files:
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
            toolchain={"name": "fennel", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return FennelAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "fn":
            # Function definition: (fn name [args] body)
            name = _get_function_name(node)
            if name:
                params = _get_function_params(node)
                signature = f"(fn {name} [{' '.join(params)}] ...)"
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="fennel",
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
            return  # Don't process children of function definitions

        elif node.type == "local":
            # Variable definition: (local name value)
            name = _get_local_name(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "var"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "var"),
                    name=name,
                    kind="variable",
                    language="fennel",
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
            return  # Don't process children of variable definitions

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        # Look for function calls (list expressions)
        if node.type == "list":
            call_name = _get_call_name(node)
            if call_name:
                # Skip special forms and built-ins
                special_forms = {
                    "fn", "lambda", "λ", "local", "let", "if", "when", "unless",
                    "do", "while", "for", "each", "collect", "icollect", "fcollect",
                    "accumulate", "faccumulate", "match", "case", "case-try",
                    "or", "and", "not", "set", "tset", "global", "var",
                    "import-macros", "require-macros", "include", "eval-compiler",
                    "macros", "macro", "quote", "hashfn", "#", "partial",
                    "pick-values", "pick-args", "lua", "macrodebug", "assert-compile",
                    "doto", "->", "->>", "-?>", "-?>>", "?.", "with-open",
                }
                builtins = {
                    "+", "-", "*", "/", "%", "^", "..", "=", "<", ">", "<=", ">=",
                    "~=", "#", "length", "type", "tonumber", "tostring",
                    "print", "error", "assert", "pairs", "ipairs", "next",
                    "select", "unpack", "table.insert", "table.remove",
                    "table.concat", "table.sort", "table.pack", "table.unpack",
                    "string.format", "string.sub", "string.len", "string.find",
                    "string.match", "string.gsub", "string.rep", "string.byte",
                    "string.char", "string.lower", "string.upper",
                    "math.abs", "math.floor", "math.ceil", "math.sqrt",
                    "math.sin", "math.cos", "math.min", "math.max",
                    "pcall", "xpcall", "rawget", "rawset", "rawequal",
                    "setmetatable", "getmetatable", "collectgarbage",
                    "require", "loadfile", "dofile", "io.open", "io.read", "io.write",
                }

                if call_name not in special_forms and call_name not in builtins:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        callee_id = self._symbol_registry.get(call_name)
                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"fennel:unresolved:{call_name}"

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
                            evidence_lang="fennel",
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
            if current.type == "fn":
                name = _get_function_name(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_fennel(repo_root: Path) -> FennelAnalysisResult:
    """Analyze Fennel source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        FennelAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = FennelAnalyzer(repo_root)
    return analyzer.analyze()
