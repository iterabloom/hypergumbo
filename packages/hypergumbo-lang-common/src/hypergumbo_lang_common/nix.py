# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nix expression analysis pass using tree-sitter-nix.

This analyzer uses tree-sitter to parse Nix files and extract:
- Function definitions (named lambdas)
- Let bindings and attribute set bindings
- Flake inputs
- Derivation declarations
- Import expressions

If tree-sitter-nix is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Nix-specific extraction logic.

1. Check if tree-sitter-nix is available
2. If not available, return skipped result (not an error)
3. Parse all .nix files
4. Extract bindings, functions, derivations
5. Create imports edges for import expressions

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-nix package for grammar
- Nix-specific: functions, derivations, flake inputs are first-class
- Useful for analyzing NixOS configurations and Nix packages
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("nix")


def find_nix_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Nix files in the repository."""
    yield from find_files(repo_root, ["*.nix"])


def is_nix_tree_sitter_available() -> bool:
    """Check if tree-sitter with Nix grammar is available."""
    return _analyzer._check_grammar_available()


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract identifier from a node."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _get_attrpath_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get the first identifier from an attrpath node."""
    for child in node.children:
        if child.type == "attrpath":
            for grandchild in child.children:
                if grandchild.type == "identifier":
                    return node_text(grandchild, source)
    return None  # pragma: no cover


def _is_function_body(node: "tree_sitter.Node") -> bool:
    """Check if node is a function expression (lambda)."""
    return node.type == "function_expression"


def _extract_nix_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a Nix function_expression node.

    Nix has two function styles:
    - Simple lambda: x: y: body -> (x, y)
    - Formals (attrset pattern): { a, b, c ? 0 }: body -> { a, b, c }

    Returns signature string or None.
    """
    if node.type != "function_expression":
        return None  # pragma: no cover

    params: list[str] = []
    has_formals = False
    current = node

    # Walk through potentially curried functions
    while current and current.type == "function_expression":
        for child in current.children:
            if child.type == "formals":
                # Attrset pattern: { a, b, c ? 0 }
                has_formals = True
                for formal_child in child.children:
                    if formal_child.type == "formal":
                        # Get the identifier from the formal
                        for fc in formal_child.children:
                            if fc.type == "identifier":
                                params.append(node_text(fc, source))
                                break
                # Don't recurse into body for formals style
                break
            elif child.type == "identifier":
                # Simple lambda: x: body
                params.append(node_text(child, source))

        # Check if body is another function_expression (curried)
        body = None
        for child in current.children:
            if child.type == "function_expression":
                body = child
                break
            elif child.type not in ("identifier", "formals", ":"):
                # Found actual body, stop
                break

        if has_formals or body is None:
            break
        current = body

    if has_formals:
        return "{ " + ", ".join(params) + " }"
    elif params:
        return "(" + ", ".join(params) + ")"
    return "()"  # pragma: no cover - edge case


def _is_derivation_call(node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if node is a derivation-creating call."""
    if node.type != "apply_expression":
        return False

    # Look for mkDerivation, mkShell, buildPythonPackage, etc.
    text = node_text(node, source)
    derivation_funcs = [
        "mkDerivation", "mkShell", "buildPythonPackage", "buildGoModule",
        "buildRustPackage", "buildNpmPackage", "buildPythonApplication",
    ]
    return any(func in text for func in derivation_funcs)


def _get_derivation_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a derivation attrset argument."""
    # Look for the attrset argument and find name = "..."
    for child in node.children:
        if child.type == "attrset_expression":
            for grandchild in child.children:
                if grandchild.type == "binding_set":
                    for binding in grandchild.children:
                        if binding.type == "binding":
                            name = _get_attrpath_name(binding, source)
                            if name == "name" or name == "pname":
                                # Get the string value
                                for val in binding.children:
                                    if val.type == "string_expression":
                                        for frag in val.children:
                                            if frag.type == "string_fragment":
                                                return node_text(frag, source)
    return None


def _find_import_target(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find import target from apply_expression."""
    text = node_text(node, source).strip()
    if not text.startswith("import"):
        return None  # pragma: no cover

    # Extract what comes after import
    # Common patterns: import <nixpkgs>, import ./path.nix, import nixpkgs
    for child in node.children:
        if child.type == "apply_expression":
            # Nested apply: import <nixpkgs> {}
            return _find_import_target(child, source)
        elif child.type == "spath_expression":
            return node_text(child, source)  # <nixpkgs>
        elif child.type == "path_expression":
            return node_text(child, source)  # ./path.nix
        elif child.type == "variable_expression":
            var_name = _get_identifier(child, source)
            if var_name and var_name != "import":
                return var_name
    return None  # pragma: no cover


def _is_in_inputs_block(node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if node is inside a flake inputs block by walking up the parent chain."""
    current = node.parent
    while current:
        if current.type == "binding":
            name = _get_attrpath_name(current, source)
            if name == "inputs":
                return True
        current = current.parent
    return False  # pragma: no cover - defensive


def _find_enclosing_function_nix(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function Symbol by walking up parents.

    In Nix, functions are defined via bindings with function_expression values.
    """
    current = node.parent
    while current is not None:
        if current.type == "binding":
            # Check if this binding defines a function
            name = _get_attrpath_name(current, source)
            if name:
                sym = local_symbols.get(name)
                if sym and sym.kind == "function":
                    return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_call_target_name_nix(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the target name from an apply_expression (function call).

    Nix function application: `funcName arg` or `funcName arg1 arg2`
    The first child of apply_expression is the function being called.
    """
    # In nested apply like `f a b`, the structure is:
    # apply_expression: (apply_expression: f a) b
    # We want to find the innermost function name
    current = node
    while current.type == "apply_expression":
        first_child = None
        for child in current.children:
            if child.is_named:
                first_child = child
                break
        if first_child is None:
            return None  # pragma: no cover - defensive
        if first_child.type == "apply_expression":
            current = first_child
        elif first_child.type == "variable_expression":
            # Found the function name
            return _get_identifier(first_child, source)
        elif first_child.type == "select_expression":  # pragma: no cover - Nix AST edge case
            # e.g., pkgs.mkShell - get the last identifier
            last_ident = None
            for child in first_child.children:
                if child.type == "identifier":
                    last_ident = node_text(child, source)
            return last_ident
        else:
            return None  # pragma: no cover - defensive
    return None  # pragma: no cover - defensive


def _extract_nix_symbols(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    run_id: str,
) -> tuple[list[Symbol], dict[str, Symbol]]:
    """Extract symbols from Nix AST tree (pass 1).

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        run_id: Execution ID for provenance

    Returns:
        Tuple of (symbols list, function symbol registry)
    """
    symbols: list[Symbol] = []
    symbol_registry: dict[str, Symbol] = {}

    for node in iter_tree(root):
        # Process bindings
        if node.type == "binding":
            name = _get_attrpath_name(node, source)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Check if we're inside an inputs block
                in_inputs = _is_in_inputs_block(node, source)

                # Determine kind based on context and value
                kind = "binding"
                value_node = None
                for child in node.children:
                    # Skip attrpath and anonymous nodes (like '=')
                    if child.is_named and child.type != "attrpath":
                        value_node = child
                        break

                if value_node and _is_function_body(value_node):
                    kind = "function"
                elif in_inputs:
                    kind = "input"
                elif value_node and _is_derivation_call(value_node, source):
                    kind = "derivation"
                    # Try to get derivation name
                    drv_name = _get_derivation_name(value_node, source)
                    if drv_name:
                        name = drv_name

                symbol_id = make_symbol_id("nix", rel_path, start_line, end_line, name, kind)

                # Extract signature for functions
                signature = None
                if kind == "function" and value_node:
                    signature = _extract_nix_signature(value_node, source)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind=kind,
                    name=name,
                    path=rel_path,
                    language="nix",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                )
                symbols.append(sym)
                # Register functions for call resolution
                if kind == "function":
                    symbol_registry[name] = sym

        # Detect top-level function (module/overlay pattern)
        elif node.type == "function_expression" and node.parent and node.parent.type == "source_code":
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            # Use file basename as function name for top-level functions
            name = Path(rel_path).stem
            symbol_id = make_symbol_id("nix", rel_path, start_line, end_line, name, "function")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                canonical_name=name,
                fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                kind="function",
                name=name,
                path=rel_path,
                language="nix",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                signature=_extract_nix_signature(node, source),
            )
            symbols.append(sym)
            symbol_registry[name] = sym

    return symbols, symbol_registry


def _extract_nix_edges(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    local_symbols: dict[str, Symbol],
    resolver: "NameResolver",
) -> list[Edge]:
    """Extract edges from Nix AST tree (pass 2).

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        local_symbols: Local symbol registry for finding enclosing functions
        resolver: NameResolver for callee resolution

    Returns:
        List of extracted edges
    """
    edges: list[Edge] = []

    for node in iter_tree(root):
        if node.type == "apply_expression":
            text = node_text(node, source)
            # Handle import expressions
            if text.strip().startswith("import"):
                target = _find_import_target(node, source)
                if target:
                    start_line = node.start_point[0] + 1
                    src_id = f"nix:{rel_path}:{start_line}:import"
                    dst_id = f"nix:external:{target}"

                    edge = Edge.create(
                        src=src_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=start_line,
                        confidence=0.80,
                        origin=PASS_ID,
                        evidence_type="static",
                    )
                    edges.append(edge)
            else:
                # Handle function calls
                target_name = _get_call_target_name_nix(node, source)
                if target_name:
                    caller = _find_enclosing_function_nix(node, source, local_symbols)
                    if caller:
                        # Use resolver for callee resolution
                        lookup_result = resolver.lookup(target_name)
                        if lookup_result.found and lookup_result.symbol:
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            # External/builtin function
                            dst_id = f"nix:external:{target_name}:function"
                            confidence = 0.70

                        edges.append(Edge.create(
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            evidence_type="static",
                        ))

    return edges


class NixAnalyzer(TreeSitterAnalyzer):
    """Nix expression analyzer using tree-sitter-nix."""

    lang = "nix"
    file_patterns: ClassVar[list[str]] = ["*.nix"]
    grammar_module = "tree_sitter_nix"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Nix symbols from a single file."""
        analysis = FileAnalysis()

        symbols, symbol_registry = _extract_nix_symbols(
            tree.root_node, source, rel_path, run.execution_id,
        )

        analysis.symbols.extend(symbols)
        # Populate symbol_by_name with function symbols for edge resolution
        analysis.symbol_by_name.update(symbol_registry)

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Only register function symbols for cross-file call resolution."""
        if symbol.kind == "function":
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a Nix file."""
        return _extract_nix_edges(
            tree.root_node, source, rel_path, local_symbols, resolver,
        )


_analyzer = NixAnalyzer()


@register_analyzer("nix")
def analyze_nix_files(repo_root: Path) -> AnalysisResult:
    """Analyze Nix files in the repository.

    Uses two-pass analysis:
    - Pass 1: Extract all symbols from all files
    - Pass 2: Extract edges (imports + calls) using NameResolver

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult with symbols and edges
    """
    return _analyzer.analyze(repo_root)
