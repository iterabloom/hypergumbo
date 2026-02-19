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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect imports and references

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Lean-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "lean-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_lean_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Lean files in the repository."""
    yield from find_files(repo_root, ["*.lean"])


def _make_module_id(module_name: str) -> str:
    """Generate ID for a Lean module (used as import edge target)."""
    return f"lean:{module_name}:0-0:module:module"


def _get_identifier_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract the identifier text from a node or its children."""
    if node.type == "identifier":
        return node_text(node, source).strip()
    # Look for identifier child
    id_node = find_child_by_type(node, "identifier")  # pragma: no cover
    if id_node:  # pragma: no cover
        return node_text(id_node, source).strip()  # pragma: no cover
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
            binders_text = node_text(child, source).strip()
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
            type_text = node_text(child, source).strip()
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
        sym_id = make_symbol_id("lean", file_path, start_line, end_line, name, kind)
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
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "function", signature=sig)

                elif child.type == "theorem":
                    # theorem name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "theorem", signature=sig)

                elif child.type == "lemma":  # pragma: no cover - similar to theorem
                    # lemma name ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            sig = _extract_lean_signature(child, source)
                            add_symbol(child, name, "theorem", {"is_lemma": True}, signature=sig)

                elif child.type == "structure":
                    # structure name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "structure")

                elif child.type == "inductive":
                    # inductive name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "inductive")

                elif child.type == "class":  # pragma: no cover - class detection
                    # class name where ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "class")

                elif child.type == "instance":  # pragma: no cover - instance detection
                    # instance name : ...
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        name = _get_identifier_text(id_node, source)
                        if name:
                            add_symbol(child, name, "instance")

                elif child.type == "abbrev":  # pragma: no cover - abbrev detection
                    # abbrev name ...
                    id_node = find_child_by_type(child, "identifier")
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
    resolver: "NameResolver",
    run_id: str,
) -> list[Edge]:
    """Extract import edges from a parsed Lean file.

    Detects:
    - import: Import statements (import Module.Name)
    """
    edges: list[Edge] = []
    file_id = make_file_id("lean", file_path)

    for node in iter_tree(tree.root_node):
        # Look for import statements at module level
        # Lean imports look like: import Mathlib.Data.Nat.Basic
        # In the tree: import node with identifier child containing the dotted path
        if node.type == "import":
            # Find the identifier child which contains the module path
            id_node = find_child_by_type(node, "identifier")
            if id_node:
                # The identifier contains the full dotted path
                module_name = node_text(id_node, source).strip()
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


class LeanAnalyzer(TreeSitterAnalyzer):
    """Lean 4 language analyzer using tree-sitter-lean."""

    lang = "lean"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.lean"]
    grammar_module = "tree_sitter_lean"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract definition, theorem, structure, and inductive symbols."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import edges from a Lean file."""
        return _extract_edges_from_file(
            tree, source, rel_path, [], resolver, run.execution_id,
        )


_analyzer = LeanAnalyzer()


def is_lean_tree_sitter_available() -> bool:
    """Check if tree-sitter with Lean grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("lean")
def analyze_lean(repo_root: Path) -> AnalysisResult:
    """Analyze Lean files in a repository."""
    return _analyzer.analyze(repo_root)
