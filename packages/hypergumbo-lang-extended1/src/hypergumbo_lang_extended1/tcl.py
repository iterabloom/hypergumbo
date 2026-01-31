"""Tcl language analyzer using tree-sitter.

This module provides static analysis for Tcl source code, extracting symbols
(procedures, namespaces, variables) and edges (calls).

Tcl (Tool Command Language) is a dynamic scripting language commonly used for
rapid prototyping, scripted applications, GUIs, and testing. It's particularly
prevalent in EDA (Electronic Design Automation) tools.

Implementation approach:
- Uses tree-sitter-language-pack for Tcl grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles Tcl-specific constructs like proc, namespace eval, and command substitution

Key constructs extracted:
- procedure: proc name {args} {body}
- namespace: namespace eval name {body}
- command_substitution: [command args] (function calls)
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "tcl.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class TclAnalysisResult:
    """Result of analyzing Tcl files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_tcl_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Tcl support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("tcl")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_tcl_files(root: Path) -> Iterator[Path]:
    """Find all Tcl files in the given directory."""
    for ext in ("*.tcl", "*.tk"):
        for path in root.rglob(ext):
            if path.is_file():
                yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Tcl symbol."""
    rel_path = path.relative_to(repo_root)
    return f"tcl:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the procedure name from a procedure node."""
    for child in node.children:
        if child.type == "simple_word":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a procedure's arguments."""
    params = []
    for child in node.children:
        if child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "argument":
                    for inner in arg_child.children:
                        if inner.type == "simple_word":
                            params.append(_get_node_text(inner))
    return params


def _get_namespace_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the namespace name from a namespace node."""
    # Look for 'eval' followed by the namespace name in word_list
    for child in node.children:
        if child.type == "word_list":
            found_eval = False
            for word_child in child.children:
                if word_child.type == "simple_word":
                    text = _get_node_text(word_child)
                    if text == "eval":
                        found_eval = True
                    elif found_eval:
                        return text
    return None  # pragma: no cover


def _count_namespace_procs(node: "tree_sitter.Node") -> int:
    """Count procedures in a namespace."""
    count = 0
    for child in node.children:
        if child.type == "word_list":
            for word_child in child.children:
                if word_child.type == "braced_word":
                    for inner in word_child.children:
                        if inner.type == "procedure":
                            count += 1
    return count


class TclAnalyzer:
    """Analyzer for Tcl source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> TclAnalysisResult:
        """Analyze all Tcl files in the repository."""
        if not is_tcl_tree_sitter_available():
            warnings.warn(
                "Tcl analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return TclAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("tcl")
        tcl_files = list(find_tcl_files(self.repo_root))

        if not tcl_files:
            return TclAnalysisResult()

        # Pass 1: Collect all symbols
        for path in tcl_files:
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
        for path in tcl_files:
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
            toolchain={"name": "tcl", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return TclAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, namespace: Optional[str] = None
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "procedure":
            name = _get_proc_name(node)
            if name:
                params = _get_proc_params(node)
                signature = f"proc {name} {{{', '.join(params)}}}"
                rel_path = str(path.relative_to(self.repo_root))

                qualified_name = f"{namespace}::{name}" if namespace else name

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, qualified_name, "proc"),
                    stable_id=_make_stable_id(path, self.repo_root, qualified_name, "proc"),
                    name=name,
                    kind="function",
                    language="tcl",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"namespace": namespace} if namespace else None,
                )
                self.symbols.append(sym)

        elif node.type == "namespace":
            name = _get_namespace_name(node)
            if name:
                proc_count = _count_namespace_procs(node)
                rel_path = str(path.relative_to(self.repo_root))

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "namespace"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "namespace"),
                    name=name,
                    kind="namespace",
                    language="tcl",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"proc_count": proc_count},
                )
                self.symbols.append(sym)

                # Extract procedures within the namespace
                for child in node.children:
                    if child.type == "word_list":
                        for word_child in child.children:
                            if word_child.type == "braced_word":
                                for inner in word_child.children:
                                    self._extract_symbols(inner, path, namespace=name)
                return  # Don't recursively process namespace children again

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path, namespace)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        # Look for command substitutions [command args] and commands
        if node.type in ("command_substitution", "command"):
            caller_id = self._find_enclosing_proc(node, path)
            if caller_id:
                callee_name = self._get_command_name(node)

                if callee_name:
                    # Skip built-in commands
                    builtins = {
                        "puts", "set", "expr", "return", "if", "else", "while",
                        "for", "foreach", "switch", "catch", "try", "proc",
                        "namespace", "package", "source", "uplevel", "upvar",
                        "global", "variable", "incr", "append", "lappend",
                        "list", "lindex", "llength", "lsort", "lsearch",
                        "string", "regexp", "regsub", "split", "join", "format",
                        "scan", "open", "close", "read", "gets", "eof", "flush",
                        "seek", "tell", "file", "glob", "cd", "pwd", "exec",
                        "eval", "after", "update", "vwait", "info", "array",
                        "dict", "clock", "time", "pid", "error", "break",
                        "continue", "rename", "unset", "trace", "interp",
                    }
                    if callee_name in builtins:
                        pass  # Skip built-ins
                    else:
                        callee_id = self._symbol_registry.get(callee_name)
                        confidence = 1.0 if callee_id else 0.6
                        if callee_id is None:
                            callee_id = f"tcl:unresolved:{callee_name}"

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
                            evidence_lang="tcl",
                        )
                        self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _get_command_name(self, node: "tree_sitter.Node") -> Optional[str]:
        """Get the command name from a command or command_substitution node."""
        if node.type == "command_substitution":
            for child in node.children:
                if child.type == "command":
                    return self._get_command_name(child)
        elif node.type == "command":
            for child in node.children:
                if child.type == "simple_word":
                    return _get_node_text(child)
        return None  # pragma: no cover

    def _find_enclosing_proc(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing procedure for a node."""
        current = node.parent
        namespace_name = None
        proc_name = None

        while current is not None:
            if current.type == "namespace":
                if namespace_name is None:
                    namespace_name = _get_namespace_name(current)
            if current.type == "procedure":
                if proc_name is None:
                    proc_name = _get_proc_name(current)
            current = current.parent

        if proc_name:
            qualified_name = f"{namespace_name}::{proc_name}" if namespace_name else proc_name
            return _make_stable_id(path, self.repo_root, qualified_name, "proc")
        return None  # pragma: no cover


def analyze_tcl(repo_root: Path) -> TclAnalysisResult:
    """Analyze Tcl source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        TclAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = TclAnalyzer(repo_root)
    return analyzer.analyze()
