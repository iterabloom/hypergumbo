"""Fortran analysis pass using tree-sitter-fortran.

This analyzer uses tree-sitter to parse Fortran files and extract:
- Module definitions
- Program definitions
- Function definitions
- Subroutine definitions
- Derived type definitions
- Use statements (imports)
- Subroutine/function calls

If tree-sitter-fortran is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-fortran is available
2. If not available, return skipped result (not an error)
3. Parse all .f, .f90, .f95, .f03, .f08 files
4. Extract modules, programs, functions, subroutines, types
5. Create imports edges for use statements
6. Create calls edges for subroutine calls

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-fortran package for grammar
- Fortran-specific: modules, subroutines, types are first-class
- Important for scientific computing, HPC, and legacy codebases
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "fortran-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Fortran file extensions
FORTRAN_EXTENSIONS = ["*.f", "*.f90", "*.f95", "*.f03", "*.f08", "*.F", "*.F90", "*.F95", "*.F03", "*.F08", "*.for", "*.fpp"]


def find_fortran_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Fortran files in the repository."""
    yield from find_files(repo_root, FORTRAN_EXTENSIONS)


def is_fortran_tree_sitter_available() -> bool:
    """Check if tree-sitter with Fortran grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_fortran") is None:
        return False  # pragma: no cover
    return True


@dataclass
class FortranAnalysisResult:
    """Result of analyzing Fortran files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"fortran:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source).lower()
    return None  # pragma: no cover


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract type name from derived_type_definition."""
    for child in node.children:
        if child.type == "derived_type_statement":
            for grandchild in child.children:
                if grandchild.type == "type_name":
                    return _node_text(grandchild, source).lower()
    return None  # pragma: no cover


def _get_statement_name(node: "tree_sitter.Node", source: bytes, stmt_type: str) -> Optional[str]:
    """Extract name from a statement node (function_statement, subroutine_statement, etc.)."""
    for child in node.children:
        if child.type == stmt_type:
            for grandchild in child.children:
                if grandchild.type == "name":
                    return _node_text(grandchild, source).lower()
    return None


def _process_fortran_tree(
    node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    symbol_registry: dict[str, str],
    current_symbol: Optional[str] = None,
) -> None:
    """Process Fortran AST tree to extract symbols and edges.

    Args:
        node: Tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        symbol_registry: Registry mapping symbol names to IDs
        current_symbol: ID of current enclosing symbol (for call edges)
    """
    # Module definitions
    if node.type == "module":
        name = None
        for child in node.children:
            if child.type == "module_statement":
                name = _get_name(child, source)
                break

        if name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "module")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="module",
                name=name,
                path=rel_path,
                language="fortran",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            symbols.append(sym)
            symbol_registry[name] = symbol_id

            # Process children with this module as current
            for child in node.children:
                _process_fortran_tree(child, source, rel_path, symbols, edges, symbol_registry, symbol_id)
            return

    # Program definitions
    elif node.type == "program":
        name = None
        for child in node.children:
            if child.type == "program_statement":
                name = _get_name(child, source)
                break

        if name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "program")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="program",
                name=name,
                path=rel_path,
                language="fortran",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            symbols.append(sym)
            symbol_registry[name] = symbol_id

            # Process children with this program as current
            for child in node.children:
                _process_fortran_tree(child, source, rel_path, symbols, edges, symbol_registry, symbol_id)
            return

    # Function definitions
    elif node.type == "function":
        name = _get_statement_name(node, source, "function_statement")
        if name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "function")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="function",
                name=name,
                path=rel_path,
                language="fortran",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            symbols.append(sym)
            symbol_registry[name] = symbol_id

            # Process children with this function as current
            for child in node.children:
                _process_fortran_tree(child, source, rel_path, symbols, edges, symbol_registry, symbol_id)
            return

    # Subroutine definitions
    elif node.type == "subroutine":
        name = _get_statement_name(node, source, "subroutine_statement")
        if name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "subroutine")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="subroutine",
                name=name,
                path=rel_path,
                language="fortran",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            symbols.append(sym)
            symbol_registry[name] = symbol_id

            # Process children with this subroutine as current
            for child in node.children:
                _process_fortran_tree(child, source, rel_path, symbols, edges, symbol_registry, symbol_id)
            return

    # Derived type definitions
    elif node.type == "derived_type_definition":
        name = _get_type_name(node, source)
        if name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "type")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="type",
                name=name,
                path=rel_path,
                language="fortran",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            symbols.append(sym)
            symbol_registry[name] = symbol_id

    # Use statements (imports)
    elif node.type == "use_statement":
        mod_name = None
        for child in node.children:
            if child.type == "module_name":
                mod_name = _node_text(child, source).lower()
                break

        if mod_name and current_symbol:
            start_line = node.start_point[0] + 1
            dst_id = symbol_registry.get(mod_name, f"fortran:external:{mod_name}")

            edge = Edge(
                id=_make_edge_id(current_symbol, dst_id, "imports"),
                src=current_symbol,
                dst=dst_id,
                edge_type="imports",
                line=start_line,
                confidence=0.90 if mod_name in symbol_registry else 0.70,
                origin=PASS_ID,
                evidence_type="static",
            )
            edges.append(edge)

    # Subroutine calls
    elif node.type == "subroutine_call":
        call_name = None
        for child in node.children:
            if child.type == "identifier":
                call_name = _node_text(child, source).lower()
                break

        if call_name and current_symbol:
            start_line = node.start_point[0] + 1
            dst_id = symbol_registry.get(call_name, f"fortran:external:{call_name}")

            edge = Edge(
                id=_make_edge_id(current_symbol, dst_id, "calls"),
                src=current_symbol,
                dst=dst_id,
                edge_type="calls",
                line=start_line,
                confidence=0.90 if call_name in symbol_registry else 0.70,
                origin=PASS_ID,
                evidence_type="static",
            )
            edges.append(edge)

    # Recurse into children
    for child in node.children:
        _process_fortran_tree(child, source, rel_path, symbols, edges, symbol_registry, current_symbol)


def analyze_fortran_files(repo_root: Path) -> FortranAnalysisResult:
    """Analyze Fortran files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        FortranAnalysisResult with symbols and edges
    """
    if not is_fortran_tree_sitter_available():  # pragma: no cover
        return FortranAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-fortran not installed (pip install tree-sitter-fortran)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_fortran

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Symbol registry for cross-file resolution: name -> symbol_id
    symbol_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_fortran.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Fortran parser: {e}")
        return FortranAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    fortran_files = list(find_fortran_files(repo_root))

    for fortran_path in fortran_files:
        try:
            rel_path = str(fortran_path.relative_to(repo_root))
            source = fortran_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_fortran_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                symbol_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {fortran_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return FortranAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
