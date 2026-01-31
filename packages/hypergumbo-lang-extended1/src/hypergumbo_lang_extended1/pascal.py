"""Pascal language analyzer using tree-sitter.

This module provides static analysis for Pascal source code, extracting symbols
(procedures, functions, programs, units) and edges (calls).

Pascal is a classic imperative and procedural programming language designed for
teaching structured programming. It's still widely used through Delphi, Free Pascal,
and Lazarus IDE. Modern Object Pascal supports object-oriented programming.

Implementation approach:
- Uses tree-sitter-language-pack for Pascal grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles both program and unit structures
- Extracts procedures, functions, and their call relationships

Key constructs extracted:
- program ... - main program definition
- unit ... - module/library definition
- function name(args): type - function definitions
- procedure name(args) - procedure definitions
- name(args) - procedure/function calls
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "pascal.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class PascalAnalysisResult:
    """Result of analyzing Pascal files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_pascal_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Pascal support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("pascal")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_pascal_files(root: Path) -> Iterator[Path]:
    """Find all Pascal files in the given directory."""
    extensions = ("*.pas", "*.pp", "*.dpr", "*.lpr")
    for ext in extensions:
        for path in root.rglob(ext):
            if path.is_file():
                yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Pascal symbol."""
    rel_path = path.relative_to(repo_root)
    return f"pascal:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier child of a node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_proc_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the name of a procedure/function from a defProc node."""
    # defProc contains declProc which has the identifier
    for child in node.children:
        if child.type == "declProc":
            return _get_identifier(child)
    return None  # pragma: no cover


def _get_proc_kind(node: "tree_sitter.Node") -> str:
    """Get whether this is a function or procedure."""
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type == "kFunction":
                    return "function"
                elif subchild.type == "kProcedure":
                    return "procedure"
    return "procedure"  # default  # pragma: no cover


def _get_proc_params(node: "tree_sitter.Node") -> list[str]:
    """Get parameter names from a defProc node."""
    params = []
    for child in node.children:
        if child.type == "declProc":
            for subchild in child.children:
                if subchild.type == "declArgs":
                    for arg in subchild.children:
                        if arg.type == "declArg":
                            # Get identifiers from declArg (can be multiple: A, B: Integer)
                            for arg_child in arg.children:
                                if arg_child.type == "identifier":
                                    params.append(_get_node_text(arg_child))
    return params


def _get_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Get the return type of a function from a defProc node."""
    for child in node.children:
        if child.type == "declProc":
            # Find typeref after the colon (for function return type)
            found_colon = False
            for subchild in child.children:
                if subchild.type == ":":
                    found_colon = True
                elif found_colon and subchild.type == "typeref":
                    return _get_identifier(subchild)
    return None


def _get_call_name(node: "tree_sitter.Node") -> Optional[str]:
    """Get the function/procedure name from an exprCall node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


class PascalAnalyzer:
    """Analyzer for Pascal source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> PascalAnalysisResult:
        """Analyze all Pascal files in the repository."""
        if not is_pascal_tree_sitter_available():
            warnings.warn(
                "Pascal analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return PascalAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("pascal")
        pascal_files = list(find_pascal_files(self.repo_root))

        if not pascal_files:
            return PascalAnalysisResult()

        # Pass 1: Collect all symbols
        for path in pascal_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry (lowercase for case-insensitive matching)
        for sym in self.symbols:
            self._symbol_registry[sym.name.lower()] = sym.id

        # Pass 2: Extract edges
        for path in pascal_files:
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
            toolchain={"name": "pascal", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return PascalAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "program":
            # Program definition
            name = _get_identifier(node)
            if name is None:
                # Try moduleName
                for child in node.children:
                    if child.type == "moduleName":
                        name = _get_node_text(child)
                        break
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "program"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "program"),
                    name=name,
                    kind="program",
                    language="pascal",
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

        elif node.type == "unit":
            # Unit definition
            name = None
            for child in node.children:
                if child.type == "moduleName":
                    name = _get_identifier(child)
                    if name is None:  # pragma: no cover
                        name = _get_node_text(child)
                    break
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "unit"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "unit"),
                    name=name,
                    kind="module",
                    language="pascal",
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

        elif node.type == "defProc":
            # Function or procedure definition
            name = _get_proc_name(node)
            if name:
                kind = _get_proc_kind(node)
                params = _get_proc_params(node)
                return_type = _get_return_type(node)
                rel_path = str(path.relative_to(self.repo_root))

                # Build signature
                if kind == "function":
                    signature = f"function {name}({', '.join(params)}): {return_type or 'unknown'}"
                else:
                    signature = f"procedure {name}({', '.join(params)})"

                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="pascal",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"param_count": len(params), "proc_kind": kind},
                )
                self.symbols.append(sym)
            return  # Don't recurse into function bodies for symbol extraction

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        call_name = None
        call_node = node

        if node.type == "exprCall":
            # Function/procedure call with arguments
            call_name = _get_call_name(node)
        elif node.type == "statement":
            # Procedure call without arguments (just identifier)
            # Check if this is a simple identifier statement (not an assignment or exprCall)
            children = [c for c in node.children if c.type not in (";",)]
            if len(children) == 1 and children[0].type == "identifier":
                call_name = _get_node_text(children[0])
                call_node = children[0]

        if call_name:
            # Skip built-in procedures
            builtins = {
                # I/O
                "write", "writeln", "read", "readln", "readkey",
                # Memory
                "new", "dispose", "getmem", "freemem", "setlength",
                # String
                "length", "copy", "delete", "insert", "pos", "concat",
                "uppercase", "lowercase", "trim", "stringreplace",
                # Math
                "inc", "dec", "abs", "sqr", "sqrt", "sin", "cos", "tan",
                "exp", "ln", "log", "power", "round", "trunc", "frac",
                "random", "randomize",
                # Conversion
                "ord", "chr", "inttostr", "strtoint", "floattostr",
                "strtofloat", "formatfloat", "format",
                # System
                "halt", "exit", "break", "continue", "sleep",
                "assigned", "sizeof", "typeof", "high", "low",
                # File
                "assign", "reset", "rewrite", "append", "close",
                "eof", "eoln", "fileexists",
                # Array
                "fillchar", "move",
            }

            if call_name.lower() not in builtins:
                caller_id = self._find_enclosing_function(node, path)
                if caller_id:
                    callee_id = self._symbol_registry.get(call_name.lower())
                    confidence = 1.0 if callee_id else 0.6
                    if callee_id is None:
                        callee_id = f"pascal:unresolved:{call_name}"

                    line = call_node.start_point[0] + 1
                    edge = Edge.create(
                        src=caller_id,
                        dst=callee_id,
                        edge_type="calls",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=self._run_id,
                        evidence_type="ast_call_direct",
                        confidence=confidence,
                        evidence_lang="pascal",
                    )
                    self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _find_enclosing_function(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing function/procedure for a node."""
        current = node.parent
        while current is not None:
            if current.type == "defProc":
                name = _get_proc_name(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_pascal(repo_root: Path) -> PascalAnalysisResult:
    """Analyze Pascal source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        PascalAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = PascalAnalyzer(repo_root)
    return analyzer.analyze()
