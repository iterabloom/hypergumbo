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

PASS_ID = "nix-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_nix_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Nix files in the repository."""
    yield from find_files(repo_root, ["*.nix"])


def is_nix_tree_sitter_available() -> bool:
    """Check if tree-sitter with Nix grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_nix") is None:
        return False  # pragma: no cover
    return True


@dataclass
class NixAnalysisResult:
    """Result of analyzing Nix files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"nix:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract identifier from a node."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None  # pragma: no cover


def _get_attrpath_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get the first identifier from an attrpath node."""
    for child in node.children:
        if child.type == "attrpath":
            for grandchild in child.children:
                if grandchild.type == "identifier":
                    return _node_text(grandchild, source)
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
                                params.append(_node_text(fc, source))
                                break
                # Don't recurse into body for formals style
                break
            elif child.type == "identifier":
                # Simple lambda: x: body
                params.append(_node_text(child, source))

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
    text = _node_text(node, source)
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
                                                return _node_text(frag, source)
    return None


def _find_import_target(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find import target from apply_expression."""
    text = _node_text(node, source).strip()
    if not text.startswith("import"):
        return None  # pragma: no cover

    # Extract what comes after import
    # Common patterns: import <nixpkgs>, import ./path.nix, import nixpkgs
    for child in node.children:
        if child.type == "apply_expression":
            # Nested apply: import <nixpkgs> {}
            return _find_import_target(child, source)
        elif child.type == "spath_expression":
            return _node_text(child, source)  # <nixpkgs>
        elif child.type == "path_expression":
            return _node_text(child, source)  # ./path.nix
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


def _process_nix_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    """Process Nix AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
    """
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

                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, kind)

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

        # Detect import expressions
        elif node.type == "apply_expression":
            text = _node_text(node, source)
            if text.strip().startswith("import"):
                target = _find_import_target(node, source)
                if target:
                    start_line = node.start_point[0] + 1
                    src_id = f"nix:{rel_path}:{start_line}:import"
                    dst_id = f"nix:external:{target}"

                    edge = Edge(
                        id=_make_edge_id(src_id, dst_id, "imports"),
                        src=src_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=start_line,
                        confidence=0.80,
                        origin=PASS_ID,
                        evidence_type="static",
                    )
                    edges.append(edge)

        # Detect top-level function (module/overlay pattern)
        elif node.type == "function_expression" and node.parent and node.parent.type == "source_code":
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            # Use file basename as function name for top-level functions
            name = Path(rel_path).stem
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


def analyze_nix_files(repo_root: Path) -> NixAnalysisResult:
    """Analyze Nix files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        NixAnalysisResult with symbols and edges
    """
    if not is_nix_tree_sitter_available():  # pragma: no cover
        return NixAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-nix not installed (pip install tree-sitter-nix)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_nix

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_nix.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Nix parser: {e}")
        return NixAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    nix_files = list(find_nix_files(repo_root))

    for nix_path in nix_files:
        try:
            rel_path = str(nix_path.relative_to(repo_root))
            source = nix_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_nix_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {nix_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return NixAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
