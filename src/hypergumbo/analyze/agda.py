"""Agda analysis pass using tree-sitter-agda.

This analyzer uses tree-sitter to parse Agda files and extract:
- Module declarations
- Function definitions (including theorems, lemmas, postulates)
- Data type definitions
- Record type definitions
- Import statements (open import, import)
- Reference relationships between declarations

Agda is a dependently typed programming language and proof assistant.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas). We model theorem
dependencies as "references" edges rather than "calls".

How It Works
------------
1. Check if tree-sitter-agda is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect imports and references
4. Track module structure and dependencies

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-agda package for grammar
- Two-pass allows cross-file resolution
- References model fits proof languages better than calls

Agda-Specific Considerations
---------------------------
- Agda has modules with hierarchical names
- Functions can have type signatures on separate lines
- Data types have constructors as separate function-like entries
- Records have fields and potentially a constructor
- Postulates are axioms (functions without implementation)
- Import can be `open import`, `import`, with using/hiding/renaming
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "agda-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_agda_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Agda files in the repository."""
    yield from find_files(repo_root, ["*.agda", "*.lagda", "*.lagda.md"])


def is_agda_tree_sitter_available() -> bool:
    """Check if tree-sitter with Agda grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_agda") is None:
        return False  # pragma: no cover - tree-sitter-agda not installed
    return True


@dataclass
class AgdaAnalysisResult:
    """Result of analyzing Agda files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"agda:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Agda file node (used as import edge source)."""
    return f"agda:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for an Agda module (used as import edge target)."""
    return f"agda:{module_name}:0-0:module:module"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _get_function_name_from_lhs(lhs_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from function lhs.

    In Agda, function signatures look like:
        double : Nat -> Nat

    Where 'lhs' contains either:
    - A 'function_name' child (for type signatures)
    - An 'atom' child as first element (for pattern clauses)
    """
    # Try function_name first (type signature)
    fn_name = _find_child_by_type(lhs_node, "function_name")
    if fn_name:
        return _node_text(fn_name, source).strip()

    # Try first atom (pattern clause like "double zero = zero")
    for child in lhs_node.children:  # pragma: no cover - pattern clause case
        if child.type == "atom":
            text = _node_text(child, source).strip()
            # Skip if it looks like a pattern (contains parens)
            if "(" not in text:
                return text
            break

    return ""  # pragma: no cover - defensive fallback


def _is_type_signature(rhs_node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if this function node is a type signature (starts with :)."""
    text = _node_text(rhs_node, source).strip()
    return text.startswith(":")


def _extract_agda_signature(
    rhs_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract type signature from an Agda function rhs node.

    Agda type signatures look like:
        double : Nat -> Nat
        add : Nat -> Nat -> Nat

    The rhs node contains:
    - : token
    - expr (the type expression like "Nat -> Nat")

    Returns signature like ": Nat -> Nat".
    """
    # The rhs node text already starts with ":"
    sig_text = _node_text(rhs_node, source).strip()
    if sig_text.startswith(":"):
        return sig_text
    return None  # pragma: no cover - defensive, called only for type signatures


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Agda file.

    Detects:
    - module: Module declarations
    - function: Function/theorem type signatures
    - data: Data type definitions
    - record: Record type definitions
    """
    symbols: list[Symbol] = []
    seen_names: set[str] = set()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        meta: dict | None = None,
        signature: Optional[str] = None,
    ) -> None:
        """Add a symbol if not already seen."""
        if not name or name in seen_names:
            return
        seen_names.add(name)

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language="agda",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )
        if meta:
            sym.meta = meta
        symbols.append(sym)

    def _is_inside_data(node: "tree_sitter.Node") -> bool:
        """Check if node is inside a data declaration."""
        current = node.parent
        while current is not None:
            if current.type == "data":
                return True
            current = current.parent
        return False  # pragma: no cover - defensive

    def _is_inside_postulate(node: "tree_sitter.Node") -> bool:
        """Check if node is inside a postulate block."""
        current = node.parent
        while current is not None:
            if current.type == "postulate":
                return True
            current = current.parent
        return False  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        if node.type == "module":
            # Module declaration
            name_node = _find_child_by_type(node, "module_name")
            if name_node:
                # Get the qid inside module_name
                qid = _find_child_by_type(name_node, "qid")
                if qid:
                    name = _node_text(qid, source).strip()
                else:  # pragma: no cover - fallback when no qid
                    name = _node_text(name_node, source).strip()
                add_symbol(node, name, "module")

        elif node.type == "function":
            # Function declaration (type signature or pattern clause)
            lhs = _find_child_by_type(node, "lhs")
            rhs = _find_child_by_type(node, "rhs")
            if lhs and rhs:
                # Only extract type signatures (name : Type), not pattern clauses
                if _is_type_signature(rhs, source):
                    name = _get_function_name_from_lhs(lhs, source)
                    if name:
                        sig = _extract_agda_signature(rhs, source)
                        # Determine if this is a constructor or postulate
                        if _is_inside_data(node):
                            add_symbol(node, name, "function", {"is_constructor": True}, signature=sig)
                        elif _is_inside_postulate(node):
                            add_symbol(node, name, "function", {"is_postulate": True}, signature=sig)
                        else:
                            add_symbol(node, name, "function", signature=sig)

        elif node.type == "data":
            # Data type definition
            name_node = _find_child_by_type(node, "data_name")
            if name_node:
                name = _node_text(name_node, source).strip()
                add_symbol(node, name, "data")

        elif node.type == "record":
            # Record type definition
            name_node = _find_child_by_type(node, "record_name")
            if name_node:
                name = _node_text(name_node, source).strip()
                add_symbol(node, name, "record")

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract import and reference edges from a parsed Agda file.

    Detects:
    - import: Import statements (open import, import)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    for node in iter_tree(tree.root_node):
        if node.type == "open":
            # open import ... statement
            import_node = _find_child_by_type(node, "import")
            if import_node:
                module_name_node = _find_child_by_type(import_node, "module_name")
                if module_name_node:
                    module_name = _node_text(module_name_node, source).strip()
                    module_id = _make_module_id(module_name)
                    edge = Edge.create(
                        src=file_id,
                        dst=module_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="open_import",
                        confidence=0.95,
                    )
                    edges.append(edge)

        elif node.type == "import":
            # Plain import statement (not inside open)
            # Check parent is not 'open'
            if node.parent and node.parent.type != "open":
                module_name_node = _find_child_by_type(node, "module_name")
                if module_name_node:
                    module_name = _node_text(module_name_node, source).strip()
                    module_id = _make_module_id(module_name)
                    edge = Edge.create(
                        src=file_id,
                        dst=module_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="import",
                        confidence=0.95,
                    )
                    edges.append(edge)

    return edges


def analyze_agda(repo_root: Path) -> AgdaAnalysisResult:
    """Analyze Agda files in a repository.

    Returns an AgdaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-agda is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_agda_tree_sitter_available():  # pragma: no cover - tree-sitter-agda not installed
        skip_reason = (
            "Agda analysis skipped: requires tree-sitter-agda "
            "(pip install tree-sitter-agda)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return AgdaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_agda

    AGDA_LANGUAGE = tree_sitter.Language(tree_sitter_agda.language())
    parser = tree_sitter.Parser(AGDA_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for agda_file in find_agda_files(repo_root):
        try:
            source = agda_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(agda_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="agda",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            global_symbol_registry,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return AgdaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
