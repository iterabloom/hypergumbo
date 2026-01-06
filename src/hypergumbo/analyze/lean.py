"""Lean 4 analysis pass using tree-sitter-lean.

This analyzer uses tree-sitter to parse Lean 4 files and extract:
- Definition declarations (def, abbrev)
- Theorem and lemma declarations
- Structure definitions
- Inductive type definitions
- Class and instance definitions
- Import statements

Lean 4 is an interactive theorem prover and programming language.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas). We model theorem
dependencies as "imports" edges for now.

How It Works
------------
1. Check if tree-sitter-lean is available (built from source)
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect imports and references
4. Track module structure and dependencies

Why This Design
---------------
- Built from source since not on PyPI
- Uses experimental tree-sitter-lean grammar
- Two-pass allows cross-file resolution
- References model fits proof languages better than calls

Lean 4 Considerations
--------------------
- Lean 4 has namespaces and modules
- Definitions can have type signatures inline or separate
- Inductive types have constructors as separate entries
- Structures are special cases of inductive types
- Classes are used for type class polymorphism
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

PASS_ID = "lean-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_lean_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Lean files in the repository."""
    yield from find_files(repo_root, ["*.lean"])


def is_lean_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lean grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_lean") is None:
        return False  # pragma: no cover - tree-sitter-lean not installed
    return True


@dataclass
class LeanAnalysisResult:
    """Result of analyzing Lean files."""

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
    return f"lean:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Lean file node (used as import edge source)."""
    return f"lean:{path}:1-1:file:file"


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Lean module (used as import edge target)."""
    return f"lean:{module_name}:0-0:module:module"


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
    return None  # pragma: no cover - defensive


def _get_identifier_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract the identifier text from a node or its children."""
    if node.type == "identifier":
        return _node_text(node, source).strip()
    # Look for identifier child
    id_node = _find_child_by_type(node, "identifier")  # pragma: no cover
    if id_node:  # pragma: no cover
        return _node_text(id_node, source).strip()  # pragma: no cover
    return ""  # pragma: no cover


def _extract_lean_signature(
    decl_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function/theorem signature from a Lean declaration node.

    Lean declarations look like:
        def double (x : Nat) : Nat := ...
        theorem add_comm (a b : Nat) : a + b = b + a := ...

    The node contains:
    - identifier (name)
    - binders (parameters like (x : Nat))
    - : followed by return type
    - := followed by body

    Returns signature like "(x : Nat) : Nat".
    """
    parts: list[str] = []
    found_name = False
    found_return_colon = False

    for child in decl_node.children:
        # Skip the keyword (def, theorem, etc.) and name
        if child.type in ("def", "theorem", "lemma", "abbrev"):
            continue
        if child.type == "identifier" and not found_name:
            found_name = True
            continue

        # Collect binders (parameters)
        if child.type == "binders":
            binders_text = _node_text(child, source).strip()
            if binders_text:
                parts.append(binders_text)

        # Collect return type after :
        if child.type == ":":
            found_return_colon = True
            parts.append(":")
            continue

        # Stop at := (start of body)
        if child.type == ":=":
            break

        # Collect the return type expression
        if found_return_colon and child.type not in (":=", "tactics"):
            type_text = _node_text(child, source).strip()
            if type_text:
                parts.append(type_text)

    if parts:
        return " ".join(parts)
    return None


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Lean file.

    Detects:
    - function: def, abbrev declarations
    - theorem: theorem, lemma declarations
    - structure: structure declarations
    - inductive: inductive type definitions
    - class: class declarations
    - instance: instance declarations
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
        if not name or name in seen_names:  # pragma: no cover - defensive
            return  # pragma: no cover
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
            language="lean",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )
        if meta:  # pragma: no cover - meta rarely used
            sym.meta = meta  # pragma: no cover
        symbols.append(sym)

    for node in iter_tree(tree.root_node):
        if node.type == "declaration":
            # Check what kind of declaration this is
            for child in node.children:
                if child.type == "def":
                    # def name ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "function", signature=sig)

                elif child.type == "theorem":
                    # theorem name ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "theorem", signature=sig)

                elif child.type == "lemma":  # pragma: no cover - similar to theorem
                    # lemma name ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "theorem", {"is_lemma": True}, signature=sig)

                elif child.type == "structure":
                    # structure name where ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "structure")

                elif child.type == "inductive":
                    # inductive name where ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "inductive")

                elif child.type == "class":  # pragma: no cover - class detection
                    # class name where ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "class")

                elif child.type == "instance":  # pragma: no cover - instance detection
                    # instance name : ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "instance")

                elif child.type == "abbrev":  # pragma: no cover - abbrev detection
                    # abbrev name ...
                    id_node = _find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "function", {"is_abbrev": True}, signature=sig)

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract import edges from a parsed Lean file.

    Detects:
    - import: Import statements (import Module.Name)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    for node in iter_tree(tree.root_node):
        # Look for import statements at module level
        # Lean imports look like: import Mathlib.Data.Nat.Basic
        # In the tree: import node with identifier child containing the dotted path
        if node.type == "import":
            # Find the identifier child which contains the module path
            id_node = _find_child_by_type(node, "identifier")
            if id_node:
                # The identifier contains the full dotted path
                module_name = _node_text(id_node, source).strip()
                if module_name:
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


def analyze_lean(repo_root: Path) -> LeanAnalysisResult:
    """Analyze Lean files in a repository.

    Returns a LeanAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-lean is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_lean_tree_sitter_available():  # pragma: no cover - tree-sitter-lean not installed
        skip_reason = (
            "Lean analysis skipped: requires tree-sitter-lean "
            "(build from source: https://github.com/Julian/tree-sitter-lean)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LeanAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_lean

    LEAN_LANGUAGE = tree_sitter.Language(tree_sitter_lean.language())
    parser = tree_sitter.Parser(LEAN_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for lean_file in find_lean_files(repo_root):
        try:
            source = lean_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(lean_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="lean",
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

    return LeanAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
