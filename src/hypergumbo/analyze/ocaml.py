"""OCaml analysis pass using tree-sitter-ocaml.

This analyzer uses tree-sitter to parse OCaml files and extract:
- Function declarations (let bindings)
- Type definitions (type declarations)
- Module definitions
- Open statements (imports)
- Function call relationships

If tree-sitter with OCaml support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-ocaml is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and open statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ocaml package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

OCaml-Specific Considerations
-----------------------------
- OCaml has `let` bindings for function definitions
- Types are defined with `type` keyword
- Modules are defined with `module X = struct ... end`
- `open Module` brings module into scope
- Function application uses whitespace (no parens needed)
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

PASS_ID = "ocaml-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_ocaml_files(repo_root: Path) -> Iterator[Path]:
    """Yield all OCaml files in the repository."""
    yield from find_files(repo_root, ["*.ml"])


def is_ocaml_tree_sitter_available() -> bool:
    """Check if tree-sitter with OCaml grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_ocaml") is None:
        return False  # pragma: no cover - tree-sitter-ocaml not installed
    return True


@dataclass
class OCamlAnalysisResult:
    """Result of analyzing OCaml files."""

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
    return f"ocaml:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an OCaml file node (used as import edge source)."""
    return f"ocaml:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - child not found


def _get_let_binding_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from let_binding node."""
    value_name = _find_child_by_type(node, "value_name")
    if value_name:
        return _node_text(value_name, source)
    return ""  # pragma: no cover - fallback for unparseable


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type name from type_binding node."""
    type_constructor = _find_child_by_type(node, "type_constructor")
    if type_constructor:
        return _node_text(type_constructor, source)
    return ""  # pragma: no cover


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module name from module_binding node."""
    module_name = _find_child_by_type(node, "module_name")
    if module_name:
        return _node_text(module_name, source)
    return ""  # pragma: no cover


def _get_open_module_path(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module path from open_module node."""
    module_path = _find_child_by_type(node, "module_path")
    if module_path:
        return _node_text(module_path, source)
    return ""  # pragma: no cover


def _extract_ocaml_signature(
    let_binding: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from an OCaml let binding.

    OCaml function syntax: let name param1 param2 = body
    Returns signature string like "(param1, param2)" or None.
    """
    params: list[str] = []
    found_name = False

    for child in let_binding.children:
        if child.type == "value_name" and not found_name:
            found_name = True
            continue
        if not found_name:  # pragma: no cover - value_name is always first
            continue  # pragma: no cover

        # Stop at = sign (start of function body)
        if child.type == "=":
            break

        # Collect parameter nodes
        if child.type == "parameter":
            # Extract the value_pattern from inside the parameter
            param_text = _node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "parenthesized_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x, y) or (x : int)
            params.append(_node_text(child, source))  # pragma: no cover
        elif child.type == "typed_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x : int)
            params.append(_node_text(child, source))  # pragma: no cover
        elif child.type == "unit_pattern":  # pragma: no cover - rare pattern
            # Pattern like ()
            params.append("()")  # pragma: no cover
        elif child.type == "wildcard_pattern":  # pragma: no cover - rare pattern
            # Pattern like _
            params.append("_")  # pragma: no cover

    if params:
        return "(" + ", ".join(params) + ")"
    return None  # No params = value, not function


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed OCaml file.

    Detects:
    - value_definition: let bindings (functions)
    - type_definition: type declarations
    - module_definition: module declarations
    """
    symbols: list[Symbol] = []
    seen_names: set[str] = set()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        signature: Optional[str] = None,
    ) -> None:
        """Add a symbol if not already seen."""
        if not name or name in seen_names:
            return  # pragma: no cover - skip empty/duplicate names
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
        symbols.append(Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language="ocaml",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        ))

    for node in iter_tree(tree.root_node):
        if node.type == "value_definition":
            # Let binding - function definition
            let_binding = _find_child_by_type(node, "let_binding")
            if let_binding:
                name = _get_let_binding_name(let_binding, source)
                if name:
                    # Extract signature
                    signature = _extract_ocaml_signature(let_binding, source)
                    add_symbol(node, name, "function", signature=signature)

        elif node.type == "type_definition":
            # Type definition
            type_binding = _find_child_by_type(node, "type_binding")
            if type_binding:
                name = _get_type_name(type_binding, source)
                if name:
                    add_symbol(node, name, "type")

        elif node.type == "module_definition":
            # Module definition
            module_binding = _find_child_by_type(node, "module_binding")
            if module_binding:
                name = _get_module_name(module_binding, source)
                if name:
                    add_symbol(node, name, "module")

    return symbols


def _find_enclosing_ocaml_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbol_registry: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the function that contains this node by walking up the parent chain."""
    current = node.parent
    while current:
        if current.type == "value_definition":
            let_binding = _find_child_by_type(current, "let_binding")
            if let_binding:
                name = _get_let_binding_name(let_binding, source)
                # Check local symbols first, then global
                if name in local_symbols:
                    return local_symbols[name]
                if name in global_symbol_registry:  # pragma: no cover - cross-file case
                    return global_symbol_registry[name]  # pragma: no cover
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    global_symbol_registry: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed OCaml file.

    Detects:
    - open_module: Import statements
    - application_expression: Function application (calls)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

    # Build local symbol map for this file (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}

    for node in iter_tree(tree.root_node):
        if node.type == "open_module":
            # Open statement (import)
            module_path = _get_open_module_path(node, source)
            if module_path:
                module_id = f"ocaml:{module_path}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="open",
                    confidence=0.95,
                )
                edges.append(edge)

        elif node.type == "application_expression":
            # Function application
            # First child that's a value_path contains the function name
            if node.children:
                first_child = node.children[0]
                callee_name = None

                if first_child.type == "value_path":
                    value_name = _find_child_by_type(first_child, "value_name")
                    if value_name:
                        callee_name = _node_text(value_name, source)

                if callee_name and callee_name not in ("print_int", "print_string", "print_endline", "print_newline"):
                    # Find the caller (enclosing function)
                    caller = _find_enclosing_ocaml_function(node, source, local_symbols, global_symbol_registry)
                    if caller:
                        # Try to resolve callee
                        callee = local_symbols.get(callee_name) or global_symbol_registry.get(callee_name)
                        if callee:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application",
                                confidence=0.85,
                            )
                            edges.append(edge)
                        else:
                            # Unresolved call - create edge to unknown target
                            unresolved_id = f"ocaml:?:0-0:{callee_name}:function"
                            edge = Edge.create(
                                src=caller.id,
                                dst=unresolved_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application",
                                confidence=0.50,
                            )
                            edges.append(edge)

    return edges


def analyze_ocaml(repo_root: Path) -> OCamlAnalysisResult:
    """Analyze OCaml files in a repository.

    Returns an OCamlAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ocaml is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_ocaml_tree_sitter_available():
        skip_reason = (
            "OCaml analysis skipped: requires tree-sitter-ocaml "
            "(pip install tree-sitter-ocaml)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return OCamlAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    import tree_sitter_ocaml

    OCAML_LANGUAGE = tree_sitter.Language(tree_sitter_ocaml.language_ocaml())
    parser = tree_sitter.Parser(OCAML_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for ml_file in find_ocaml_files(repo_root):
        try:
            source = ml_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(ml_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="ocaml",
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

    return OCamlAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
