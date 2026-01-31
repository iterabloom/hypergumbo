"""V language analyzer using tree-sitter.

This module provides static analysis for V source code, extracting symbols
(functions, structs, enums, interfaces) and edges (imports, calls).

V is a statically typed compiled programming language designed to be simple,
fast, and safe. It aims to be a pragmatic alternative to C with modern features.

Implementation approach:
- Uses tree-sitter-language-pack for V grammar
- Two-pass analysis: First pass collects all symbols, second pass extracts edges
- Handles V-specific constructs like pub visibility, modules, structs, enums

Key constructs extracted:
- function_declaration: fn name(params) return_type { body }
- struct_declaration: struct Name { fields }
- enum_declaration: enum Name { variants }
- interface_declaration: interface Name { methods }
- import_declaration: import module
- call_expression: func(args)
"""

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "v.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class VAnalysisResult:
    """Result of analyzing V files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_v_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with V support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("v")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_v_files(root: Path) -> Iterator[Path]:
    """Find all V files in the given directory."""
    for path in root.rglob("*.v"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a V symbol."""
    rel_path = path.relative_to(repo_root)
    return f"v:{rel_path}:{name}:{kind}"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_type_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the type identifier from a node's children."""
    for child in node.children:
        if child.type == "type_identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _is_public(node: "tree_sitter.Node") -> bool:
    """Check if a declaration is public (has 'pub' keyword)."""
    for child in node.children:
        if child.type == "pub":
            return True
    return False


def _extract_function_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a function declaration."""
    params = []
    for child in node.children:
        if child.type == "parameter_list":
            for param_child in child.children:
                if param_child.type == "parameter_declaration":
                    for param_part in param_child.children:
                        if param_part.type == "identifier":
                            params.append(_get_node_text(param_part))
                            break
    return params


def _extract_return_type(node: "tree_sitter.Node") -> Optional[str]:
    """Extract return type from a function declaration."""
    # Return type comes after parameter_list
    saw_params = False
    for child in node.children:
        if child.type == "parameter_list":
            saw_params = True
        elif saw_params and child.type in ("builtin_type", "type_identifier", "pointer_type"):
            return _get_node_text(child).strip()
        elif child.type == "block":
            break  # Stop at function body
    return None


def _count_struct_fields(node: "tree_sitter.Node") -> int:
    """Count fields in a struct declaration."""
    for child in node.children:
        if child.type == "struct_field_declaration_list":
            count = 0
            for field_child in child.children:
                if field_child.type == "struct_field_declaration":
                    count += 1
            return count
    return 0  # pragma: no cover


def _count_enum_variants(node: "tree_sitter.Node") -> int:
    """Count variants in an enum declaration."""
    for child in node.children:
        if child.type == "enum_member_declaration_list":
            count = 0
            for member_child in child.children:
                if member_child.type == "enum_member":
                    count += 1
            return count
    return 0  # pragma: no cover


class VAnalyzer:
    """Analyzer for V source files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""

    def analyze(self) -> VAnalysisResult:
        """Analyze all V files in the repository."""
        if not is_v_tree_sitter_available():
            warnings.warn(
                "V analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return VAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("v")
        v_files = list(find_v_files(self.repo_root))

        if not v_files:
            return VAnalysisResult()

        # Pass 1: Collect all symbols
        for path in v_files:
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
        for path in v_files:
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
            toolchain={"name": "v", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return VAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "function_declaration":
            name = _get_identifier(node)
            if name:
                params = _extract_function_params(node)
                return_type = _extract_return_type(node)
                is_pub = _is_public(node)

                signature = f"fn({', '.join(params)})"
                if return_type:
                    signature += f" {return_type}"

                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "fn"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "fn"),
                    name=name,
                    kind="function",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"is_public": is_pub},
                )
                self.symbols.append(sym)

        elif node.type == "struct_declaration":
            name = _get_type_identifier(node)
            if name:
                field_count = _count_struct_fields(node)
                is_pub = _is_public(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "struct"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "struct"),
                    name=name,
                    kind="class",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_public": is_pub, "field_count": field_count},
                )
                self.symbols.append(sym)

        elif node.type == "enum_declaration":
            name = _get_type_identifier(node)
            if name:
                variant_count = _count_enum_variants(node)
                is_pub = _is_public(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "enum"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "enum"),
                    name=name,
                    kind="enum",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_public": is_pub, "variant_count": variant_count},
                )
                self.symbols.append(sym)

        elif node.type == "interface_declaration":
            name = _get_type_identifier(node)
            if name:
                is_pub = _is_public(node)
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "interface"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "interface"),
                    name=name,
                    kind="interface",
                    language="v",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta={"is_public": is_pub},
                )
                self.symbols.append(sym)

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "import_declaration":
            # Extract import path
            for child in node.children:
                if child.type == "import_path":
                    import_path = _get_node_text(child)
                    rel_path = str(path.relative_to(self.repo_root))
                    line = node.start_point[0] + 1
                    edge = Edge.create(
                        src=f"file:{rel_path}",
                        dst=f"v:import:{import_path}",
                        edge_type="imports",
                        line=line,
                        origin=PASS_ID,
                        origin_run_id=self._run_id,
                        evidence_type="ast_import",
                        confidence=1.0,
                        evidence_lang="v",
                    )
                    self.edges.append(edge)

        elif node.type == "call_expression":
            caller_id = self._find_enclosing_function(node, path)
            if caller_id:
                # Get the callee
                callee_name = _get_identifier(node)

                if callee_name:
                    callee_id = self._symbol_registry.get(callee_name)
                    confidence = 1.0 if callee_id else 0.6
                    if callee_id is None:
                        callee_id = f"v:unresolved:{callee_name}"

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
                        evidence_lang="v",
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
            if current.type == "function_declaration":
                name = _get_identifier(current)
                if name:
                    return _make_stable_id(path, self.repo_root, name, "fn")
            current = current.parent
        return None  # pragma: no cover


def analyze_v(repo_root: Path) -> VAnalysisResult:
    """Analyze V source files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        VAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = VAnalyzer(repo_root)
    return analyzer.analyze()
