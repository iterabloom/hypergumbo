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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect imports and references

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Agda-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
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

PASS_ID = make_pass_id("agda")


def find_agda_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Agda files in the repository."""
    yield from find_files(repo_root, ["*.agda", "*.lagda", "*.lagda.md"])


def _make_module_id(module_name: str) -> str:
    """Generate ID for an Agda module (used as import edge target)."""
    return f"agda:{module_name}:0-0:module:module"


def _get_function_name_from_lhs(lhs_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from function lhs.

    In Agda, function signatures look like:
        double : Nat -> Nat

    Where 'lhs' contains either:
    - A 'function_name' child (for type signatures)
    - An 'atom' child as first element (for pattern clauses)
    """
    # Try function_name first (type signature)
    fn_name = find_child_by_type(lhs_node, "function_name")
    if fn_name:
        return node_text(fn_name, source).strip()

    # Try first atom (pattern clause like "double zero = zero")
    for child in lhs_node.children:  # pragma: no cover - pattern clause case
        if child.type == "atom":
            text = node_text(child, source).strip()
            # Skip if it looks like a pattern (contains parens)
            if "(" not in text:
                return text
            break

    return ""  # pragma: no cover - defensive fallback


def _is_type_signature(rhs_node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if this function node is a type signature (starts with :)."""
    text = node_text(rhs_node, source).strip()
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
    sig_text = node_text(rhs_node, source).strip()
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
        sym_id = make_symbol_id("agda", file_path, start_line, end_line, name, kind)
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
            name_node = find_child_by_type(node, "module_name")
            if name_node:
                # Get the qid inside module_name
                qid = find_child_by_type(name_node, "qid")
                if qid:
                    name = node_text(qid, source).strip()
                else:  # pragma: no cover - fallback when no qid
                    name = node_text(name_node, source).strip()
                add_symbol(node, name, "module")

        elif node.type == "function":
            # Function declaration (type signature or pattern clause)
            lhs = find_child_by_type(node, "lhs")
            rhs = find_child_by_type(node, "rhs")
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
            name_node = find_child_by_type(node, "data_name")
            if name_node:
                name = node_text(name_node, source).strip()
                add_symbol(node, name, "data")

        elif node.type == "record":
            # Record type definition
            name_node = find_child_by_type(node, "record_name")
            if name_node:
                name = node_text(name_node, source).strip()
                add_symbol(node, name, "record")

    return symbols


def _extract_renamings(
    directive_node: "tree_sitter.Node",
    source: bytes,
    module_name: str,
) -> dict[str, str]:
    """Extract renaming aliases from an import directive.

    Agda renaming syntax:
        open import Data.List renaming (map to listMap; filter to listFilter)

    The import_directive node contains:
    - renaming keyword
    - ( ... ) with pairs of "original_name to new_name"

    Returns dict mapping alias (new_name) to qualified path (module.original).
    """
    aliases: dict[str, str] = {}

    for child in directive_node.children:
        if child.type == "renaming":
            # This is a renaming node inside import_directive
            # Structure: id (original), 'to', id (alias)
            ids = [c for c in child.children if c.type == "id"]
            if len(ids) >= 2:
                original_name = node_text(ids[0], source).strip()
                alias_name = node_text(ids[1], source).strip()
                # Map alias to qualified path: Module.original_name
                aliases[alias_name] = f"{module_name}.{original_name}"

    return aliases


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
) -> tuple[list[Edge], dict[str, str]]:
    """Extract import and reference edges from a parsed Agda file.

    Detects:
    - import: Import statements (open import, import)

    Returns (edges, import_aliases) where import_aliases maps renamed
    symbols to their qualified module paths for path_hint resolution.
    """
    edges: list[Edge] = []
    import_aliases: dict[str, str] = {}
    file_id = make_file_id("agda", file_path)

    for node in iter_tree(tree.root_node):
        if node.type == "open":
            # open import ... statement
            import_node = find_child_by_type(node, "import")
            if import_node:
                module_name_node = find_child_by_type(import_node, "module_name")
                if module_name_node:
                    module_name = node_text(module_name_node, source).strip()
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

                    # Extract renaming aliases from import_directive
                    directive = find_child_by_type(node, "import_directive")
                    if directive:
                        renamings = _extract_renamings(directive, source, module_name)
                        import_aliases.update(renamings)

        elif node.type == "import":
            # Plain import statement (not inside open)
            # Check parent is not 'open'
            if node.parent and node.parent.type != "open":
                module_name_node = find_child_by_type(node, "module_name")
                if module_name_node:
                    module_name = node_text(module_name_node, source).strip()
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

    # Reference edges: scan pattern clauses (function with RHS starting with =)
    # for qid references to defined symbols.
    seen_ref_pairs: set[tuple[str, str]] = set()
    for node in iter_tree(tree.root_node):
        if node.type != "function":
            continue
        rhs = find_child_by_type(node, "rhs")
        if not rhs:
            continue  # pragma: no cover
        rhs_text = node_text(rhs, source).strip()
        if not rhs_text.startswith("="):
            continue  # Type signature, not pattern clause

        # Determine the enclosing function name (first atom/qid in LHS)
        lhs = find_child_by_type(node, "lhs")
        if not lhs:
            continue  # pragma: no cover
        enclosing_name = ""
        for child in lhs.children:
            if child.type == "atom":
                qid_node = find_child_by_type(child, "qid")
                if qid_node:
                    enclosing_name = node_text(qid_node, source).strip()
                    break
        if not enclosing_name:
            continue  # pragma: no cover

        # Resolve enclosing function to get its ID
        enclosing_result = resolver.lookup(enclosing_name)
        if not enclosing_result.symbol:
            continue

        # Scan all qid nodes in the RHS for references
        for rhs_child in iter_tree(rhs):
            if rhs_child.type != "qid":
                continue
            ref_name = node_text(rhs_child, source).strip()
            if not ref_name or ref_name == enclosing_name:
                continue  # Skip self-references and empty names

            ref_result = resolver.lookup(ref_name)
            if ref_result.symbol:
                pair = (enclosing_result.symbol.id, ref_result.symbol.id)
                if pair not in seen_ref_pairs:
                    seen_ref_pairs.add(pair)
                    edge = Edge.create(
                        src=enclosing_result.symbol.id,
                        dst=ref_result.symbol.id,
                        edge_type="references",
                        line=rhs_child.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        confidence=0.80,
                    )
                    edges.append(edge)

    return edges, import_aliases


class AgdaAnalyzer(TreeSitterAnalyzer):
    """Agda language analyzer using tree-sitter-agda."""

    lang = "agda"
    file_patterns: ClassVar[list[str]] = ["*.agda", "*.lagda", "*.lagda.md"]
    grammar_module = "tree_sitter_agda"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, function, data, and record symbols from an Agda file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Agda import renaming aliases.

        Agda renaming syntax:
            open import Data.List renaming (map to listMap)
        """
        aliases: dict[str, str] = {}
        for node in iter_tree(tree.root_node):
            if node.type == "open":
                import_node = find_child_by_type(node, "import")
                if import_node:
                    module_name_node = find_child_by_type(import_node, "module_name")
                    if module_name_node:
                        module_name = node_text(module_name_node, source).strip()
                        directive = find_child_by_type(node, "import_directive")
                        if directive:
                            renamings = _extract_renamings(directive, source, module_name)
                            aliases.update(renamings)
        return aliases

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import edges from an Agda file."""
        edges, _aliases = _extract_edges_from_file(
            tree, source, rel_path, [], resolver, run.execution_id,
        )
        return edges


_analyzer = AgdaAnalyzer()


def is_agda_tree_sitter_available() -> bool:
    """Check if tree-sitter with Agda grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("agda")
def analyze_agda(repo_root: Path) -> AnalysisResult:
    """Analyze Agda files in a repository."""
    return _analyzer.analyze(repo_root)
