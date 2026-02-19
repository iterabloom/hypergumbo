"""Ada analysis pass using tree-sitter.

Detects:
- Package specifications and bodies
- Function and procedure declarations/implementations
- Type definitions (records, enums, arrays)
- Constants and variables
- With/use clauses (imports)

Ada is a strongly-typed, safety-critical language used in aerospace,
defense, medical devices, and embedded systems where reliability is paramount.
The tree-sitter-ada parser handles .ads (spec), .adb (body), and .ada files.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract all symbols from all files
2. Pass 2: Extract edges (imports + calls) using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Ada-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Ada grammar
- Ada is essential for safety-critical systems (aerospace, defense, medical)
- Supports both specification (.ads) and body (.adb) files
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    node_text,
    find_child_by_type,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("ada")


def find_ada_files(repo_root: Path) -> Iterator[Path]:
    """Find all Ada files in the repository."""
    yield from find_files(repo_root, ["*.ads", "*.adb", "*.ada"])


def _extract_formal_part(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract parameters from formal_part node."""
    formal_part = find_child_by_type(node, "formal_part")
    if formal_part:
        return node_text(formal_part, source)
    return None


def _make_symbol(rel_path: str, run_id: str, node: "tree_sitter.Node", name: str, kind: str,
                 source: bytes, signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"ada:{rel_path}:{start_line}-{end_line}:{name}:{kind}"
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="ada",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        stable_id=f"ada:{rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _process_package_declaration(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a package declaration (spec)."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    pkg_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, pkg_name, "package", source)


def _process_package_body(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a package body."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    pkg_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, pkg_name, "package", source)


def _process_subprogram_declaration(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a function or procedure declaration."""
    func_spec = find_child_by_type(node, "function_specification")
    proc_spec = find_child_by_type(node, "procedure_specification")

    if func_spec:
        return _process_function_spec(source, rel_path, run_id, func_spec)
    elif proc_spec:
        return _process_procedure_spec(source, rel_path, run_id, proc_spec)
    return None  # pragma: no cover - defensive


def _process_subprogram_body(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a function or procedure body (implementation)."""
    func_spec = find_child_by_type(node, "function_specification")
    proc_spec = find_child_by_type(node, "procedure_specification")

    if func_spec:
        return _process_function_spec(source, rel_path, run_id, func_spec)
    elif proc_spec:
        return _process_procedure_spec(source, rel_path, run_id, proc_spec)
    return None  # pragma: no cover - defensive


def _process_function_spec(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a function specification."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    func_name = node_text(name_node, source)

    # Build signature from formal_part and result_profile
    signature_parts = []
    formal_part = _extract_formal_part(node, source)
    if formal_part:
        signature_parts.append(formal_part)

    result = find_child_by_type(node, "result_profile")
    if result:
        signature_parts.append(node_text(result, source))

    signature = " ".join(signature_parts) if signature_parts else None
    return _make_symbol(rel_path, run_id, node, func_name, "function", source, signature=signature)


def _process_procedure_spec(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a procedure specification."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    proc_name = node_text(name_node, source)
    signature = _extract_formal_part(node, source)
    return _make_symbol(rel_path, run_id, node, proc_name, "procedure", source, signature=signature)


def _process_type_declaration(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process a type declaration (record, enum, etc.)."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    type_name = node_text(name_node, source)
    return _make_symbol(rel_path, run_id, node, type_name, "type", source)


def _process_object_declaration(source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node") -> Optional[Symbol]:
    """Process an object declaration (constant or variable)."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    obj_name = node_text(name_node, source)

    # Check if it's a constant
    is_constant = find_child_by_type(node, "constant") is not None
    kind = "constant" if is_constant else "variable"

    return _make_symbol(rel_path, run_id, node, obj_name, kind, source)


def _extract_package_renames(
    tree: "tree_sitter.Tree", source: bytes
) -> dict[str, str]:
    """Extract package renaming declarations for disambiguation.

    In Ada:
        package TIO renames Ada.Text_IO;

    Returns a dict mapping alias names to full package paths.
    """
    renames: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "package_renaming_declaration":
            continue

        alias_name: Optional[str] = None
        full_path: Optional[str] = None

        for child in node.children:
            if child.type == "identifier" and alias_name is None:
                alias_name = node_text(child, source)
            elif child.type == "selected_component":
                full_path = node_text(child, source)

        if alias_name and full_path:
            renames[alias_name] = full_path

    return renames


def _get_call_target_name(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name from a procedure_call_statement or function_call.

    Returns (target_name, receiver) where:
    - target_name is the simple name (last identifier) for resolution
    - receiver is the first part of qualified calls (e.g., "TIO" from "TIO.Put_Line")
    """
    for child in node.children:
        if child.type == "identifier":
            return (node_text(child, source), None)
        elif child.type == "selected_component":
            # Get all identifiers for qualified calls
            identifiers: list[str] = []
            for sub in child.children:
                if sub.type == "identifier":
                    identifiers.append(node_text(sub, source))
            if identifiers:
                # last is target, first is receiver
                target_name = identifiers[-1]
                receiver = identifiers[0] if len(identifiers) > 1 else None
                return (target_name, receiver)
    return (None, None)  # pragma: no cover - defensive


def _find_enclosing_subprogram(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function/procedure Symbol by walking up parents."""
    current = node.parent
    while current is not None:
        if current.type == "subprogram_body":
            # Find the function/procedure name
            func_spec = find_child_by_type(current, "function_specification")
            proc_spec = find_child_by_type(current, "procedure_specification")
            spec = func_spec or proc_spec
            if spec:
                name_node = find_child_by_type(spec, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    sym = local_symbols.get(name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - defensive


class AdaAnalyzer(TreeSitterAnalyzer):
    """Ada language analyzer using tree-sitter-language-pack."""

    lang = "ada"
    file_patterns: ClassVar[list[str]] = ["*.ads", "*.adb", "*.ada"]
    language_pack_name = "ada"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract package, function, procedure, type, and constant symbols."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            sym: Optional[Symbol] = None
            if node.type == "package_declaration":
                sym = _process_package_declaration(source, rel_path, run.execution_id, node)
            elif node.type == "package_body":
                sym = _process_package_body(source, rel_path, run.execution_id, node)
            elif node.type == "subprogram_declaration":
                sym = _process_subprogram_declaration(source, rel_path, run.execution_id, node)
            elif node.type == "subprogram_body":
                sym = _process_subprogram_body(source, rel_path, run.execution_id, node)
            elif node.type == "full_type_declaration":
                sym = _process_type_declaration(source, rel_path, run.execution_id, node)
            elif node.type == "object_declaration":
                sym = _process_object_declaration(source, rel_path, run.execution_id, node)

            if sym:
                analysis.symbols.append(sym)
                if sym.kind in ("function", "procedure"):
                    analysis.symbol_by_name[sym.name] = sym

        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Ada package renaming declarations for disambiguation."""
        return _extract_package_renames(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from an Ada file."""
        edges: list[Edge] = []
        file_stable_id = f"ada:{rel_path}:file:"

        for node in iter_tree(tree.root_node):
            # Process imports (with_clause)
            if node.type == "with_clause":
                for child in node.children:
                    if child.type == "selected_component":
                        import_name = node_text(child, source)
                        edges.append(
                            Edge(
                                id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                                src=file_stable_id,
                                dst=f"ada:?:{import_name}:package",
                                edge_type="imports",
                                line=node.start_point[0] + 1,
                                confidence=0.9,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            )
                        )
                    elif child.type == "identifier":
                        text = node_text(child, source)
                        if text != "with":  # Skip the keyword
                            edges.append(
                                Edge(
                                    id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                                    src=file_stable_id,
                                    dst=f"ada:?:{text}:package",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    confidence=0.9,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                )
                            )

            # Process procedure calls
            elif node.type == "procedure_call_statement":
                target_name, receiver = _get_call_target_name(node, source)
                if target_name:
                    caller = _find_enclosing_subprogram(node, source, local_symbols)
                    if caller:
                        # Get path hint from package renames
                        path_hint = import_aliases.get(receiver) if receiver else None
                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External procedure (e.g., Ada.Text_IO.Put_Line)
                            dst_id = f"ada:external:{target_name}:procedure"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

            # Process function calls
            elif node.type == "function_call":
                target_name, receiver = _get_call_target_name(node, source)
                if target_name:
                    caller = _find_enclosing_subprogram(node, source, local_symbols)
                    if caller:
                        # Get path hint from package renames
                        path_hint = import_aliases.get(receiver) if receiver else None
                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External function
                            dst_id = f"ada:external:{target_name}:function"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:ada:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        return edges


_analyzer = AdaAnalyzer()


def is_ada_tree_sitter_available() -> bool:
    """Check if tree-sitter-ada is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("ada")
def analyze_ada(repo_root: Path) -> AnalysisResult:
    """Analyze Ada files in a repository."""
    return _analyzer.analyze(repo_root)
