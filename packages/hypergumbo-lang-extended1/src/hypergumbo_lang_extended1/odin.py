"""Odin language analyzer using tree-sitter.

This module provides static analysis for Odin source code, extracting symbols
(procedures, structs, enums, unions) and edges (imports, calls).

Odin is a general-purpose systems programming language designed as an alternative
to C. It emphasizes simplicity, readability, and metaprogramming capabilities.

Implementation approach:
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
  with cross-file resolution using the symbol table from pass 1.
- Uses tree-sitter-odin grammar for parsing
- Handles Odin-specific constructs like package declarations, structs, procedures

Odin grammar key patterns:
- procedure_declaration: name :: proc(...) { body }
- struct_declaration: Name :: struct { fields... }
- enum_declaration: Name :: enum { variants... }
- union_declaration: Name :: union { variants... }
- import_declaration: import "package:module"
- call_expression: func(args) or obj.method(args)
- member_expression: module.symbol
"""

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "odin.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class OdinAnalysisResult:
    """Result of analyzing Odin files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_odin_tree_sitter_available() -> bool:
    """Check if tree-sitter and tree-sitter-odin are available."""
    ts_spec = importlib.util.find_spec("tree_sitter")
    if ts_spec is None:
        return False

    odin_spec = importlib.util.find_spec("tree_sitter_odin")
    if odin_spec is None:
        return False

    return True


def find_odin_files(root: Path) -> Iterator[Path]:
    """Find all Odin files in the given directory."""
    for path in root.rglob("*.odin"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for an Odin symbol."""
    rel_path = path.relative_to(repo_root)
    return f"odin:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_string_content(node: "tree_sitter.Node") -> Optional[str]:
    """Extract string content from a string node."""
    for child in node.children:
        if child.type == "string_content":
            return _get_node_text(child)
    return None  # pragma: no cover


def _extract_procedure_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a procedure node."""
    params = []
    for child in node.children:
        if child.type == "procedure":
            for proc_child in child.children:
                if proc_child.type == "parameters":
                    for param_child in proc_child.children:
                        if param_child.type == "parameter":
                            for param_part in param_child.children:
                                if param_part.type == "identifier":
                                    params.append(_get_node_text(param_part))
                                    break
    return params


def _extract_procedure_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a procedure node."""
    for child in node.children:
        if child.type == "procedure":
            saw_arrow = False
            for proc_child in child.children:
                if proc_child.type == "->":
                    saw_arrow = True
                elif saw_arrow and proc_child.type == "type":
                    # Get the type identifier
                    for type_child in proc_child.children:
                        if type_child.type == "identifier":
                            return _get_node_text(type_child)
                    return _get_node_text(proc_child)  # pragma: no cover
    return None


class OdinAnalyzer:
    """Analyzer for Odin source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""  # Set during analysis

    def analyze(self) -> OdinAnalysisResult:
        """Analyze all Odin files in the repository."""
        if not is_odin_tree_sitter_available():
            warnings.warn(
                "Odin analysis skipped: tree-sitter-odin not available",
                UserWarning,
                stacklevel=2,
            )
            return OdinAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-odin grammar not available",
            )

        import uuid as uuid_module
        import tree_sitter
        import tree_sitter_odin

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        # Set up parser
        lang = tree_sitter.Language(tree_sitter_odin.language())
        parser = tree_sitter.Parser(lang)

        odin_files = list(find_odin_files(self.repo_root))

        if not odin_files:
            return OdinAnalysisResult()

        # Pass 1: Collect symbols from all files
        for path in odin_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path, content)
            except Exception:  # pragma: no cover
                # Skip files that can't be parsed
                pass

        # Build symbol registry from symbols
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id

        # Pass 2: Extract edges with cross-file resolution
        for path in odin_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path, content)
            except Exception:  # pragma: no cover
                # Skip files that can't be parsed
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "odin", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return OdinAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "procedure_declaration":
            name = _get_identifier(node)
            if name:
                params = _extract_procedure_params(node)
                return_type = _extract_procedure_return_type(node)
                signature = f"proc({', '.join(params)})"
                if return_type:
                    signature += f" -> {return_type}"

                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "proc"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "proc"),
                    name=name,
                    kind="function",
                    language="odin",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                )
                self.symbols.append(sym)

        elif node.type == "struct_declaration":
            name = _get_identifier(node)
            if name:
                # Count fields
                field_count = sum(1 for c in node.children if c.type == "field")
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "struct"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "struct"),
                    name=name,
                    kind="class",
                    language="odin",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"field_count": field_count},
                )
                self.symbols.append(sym)

        elif node.type == "enum_declaration":
            name = _get_identifier(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "enum"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "enum"),
                    name=name,
                    kind="enum",
                    language="odin",
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

        elif node.type == "union_declaration":
            name = _get_identifier(node)
            if name:
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "union"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "union"),
                    name=name,
                    kind="class",
                    language="odin",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_union": True},
                )
                self.symbols.append(sym)

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path, content)

    def _extract_edges(
        self, node: "tree_sitter.Node", path: Path, content: bytes
    ) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "import_declaration":
            # Extract import path
            for child in node.children:
                if child.type == "string":
                    import_path = _get_string_content(child)
                    if import_path:
                        # Create import edge
                        rel_path = str(path.relative_to(self.repo_root))
                        line = node.start_point[0] + 1
                        edge = Edge.create(
                            src=f"file:{rel_path}",
                            dst=f"odin:import:{import_path}",
                            edge_type="imports",
                            line=line,
                            origin=PASS_ID,
                            origin_run_id=self._run_id,
                            evidence_type="ast_import",
                            confidence=1.0,
                            evidence_lang="odin",
                        )
                        self.edges.append(edge)

        elif node.type == "call_expression":
            # Find the enclosing function
            caller_id = self._find_enclosing_function(node, path)
            if caller_id:
                # Get the callee name
                callee_name = None
                for child in node.children:
                    if child.type == "identifier":
                        callee_name = _get_node_text(child)
                        break

                if callee_name:
                    # Try to resolve the callee
                    callee_id = self._symbol_registry.get(callee_name)
                    confidence = 1.0 if callee_id else 0.6
                    if callee_id is None:
                        callee_id = f"odin:unresolved:{callee_name}"

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
                        evidence_lang="odin",
                    )
                    self.edges.append(edge)

        elif node.type == "member_expression":
            # Handle qualified calls like fmt.println
            # Check if this is a call (look at parent or sibling)
            parent = node.parent
            if parent and parent.type == "member_expression":
                # This is the left side of a member access, the call is elsewhere
                pass  # pragma: no cover
            else:
                # Check for call_expression as next sibling pattern
                # member_expression -> identifier . call_expression
                call_child = None
                for child in node.children:
                    if child.type == "call_expression":
                        call_child = child
                        break

                if call_child:
                    caller_id = self._find_enclosing_function(node, path)
                    if caller_id:
                        # Get module and function name
                        module_name = None
                        func_name = None
                        for child in node.children:
                            if child.type == "identifier":
                                if module_name is None:
                                    module_name = _get_node_text(child)
                                else:
                                    func_name = _get_node_text(child)  # pragma: no cover
                        for child in call_child.children:
                            if child.type == "identifier":
                                func_name = _get_node_text(child)
                                break

                        if module_name and func_name:
                            qualified_name = f"{module_name}.{func_name}"
                            callee_id = f"odin:external:{qualified_name}"

                            line = node.start_point[0] + 1
                            edge = Edge.create(
                                src=caller_id,
                                dst=callee_id,
                                edge_type="calls",
                                line=line,
                                origin=PASS_ID,
                                origin_run_id=self._run_id,
                                evidence_type="ast_call_method",
                                confidence=0.8,
                                evidence_lang="odin",
                            )
                            self.edges.append(edge)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path, content)

    def _find_enclosing_function(
        self, node: "tree_sitter.Node", path: Path
    ) -> Optional[str]:
        """Find the enclosing function for a node."""
        current = node.parent
        while current is not None:
            if current.type == "procedure_declaration":
                name = _get_identifier(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "proc")
            current = current.parent
        return None  # pragma: no cover


def analyze_odin(repo_root: Path) -> OdinAnalysisResult:
    """Analyze Odin source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        OdinAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = OdinAnalyzer(repo_root)
    return analyzer.analyze()
