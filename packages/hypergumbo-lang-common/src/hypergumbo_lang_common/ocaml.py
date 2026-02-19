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
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, file discovery,
and result assembly. This module overrides _create_parser() because
tree-sitter-ocaml uses language_ocaml() instead of the standard language().

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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
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

PASS_ID = "ocaml-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_ocaml_files(repo_root: Path) -> Iterator[Path]:
    """Yield all OCaml files in the repository."""
    yield from find_files(repo_root, ["*.ml"])


def _get_let_binding_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function name from let_binding node."""
    value_name = find_child_by_type(node, "value_name")
    if value_name:
        return node_text(value_name, source)
    return ""  # pragma: no cover - fallback for unparseable


def _get_type_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type name from type_binding node."""
    type_constructor = find_child_by_type(node, "type_constructor")
    if type_constructor:
        return node_text(type_constructor, source)
    return ""  # pragma: no cover


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module name from module_binding node."""
    module_name = find_child_by_type(node, "module_name")
    if module_name:
        return node_text(module_name, source)
    return ""  # pragma: no cover


def _get_open_module_path(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract module path from open_module node."""
    module_path = find_child_by_type(node, "module_path")
    if module_path:
        return node_text(module_path, source)
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
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "parenthesized_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x, y) or (x : int)
            params.append(node_text(child, source))  # pragma: no cover
        elif child.type == "typed_pattern":  # pragma: no cover - rare pattern
            # Pattern like (x : int)
            params.append(node_text(child, source))  # pragma: no cover
        elif child.type == "unit_pattern":  # pragma: no cover - rare pattern
            # Pattern like ()
            params.append("()")  # pragma: no cover
        elif child.type == "wildcard_pattern":  # pragma: no cover - rare pattern
            # Pattern like _
            params.append("_")  # pragma: no cover

    if params:
        return "(" + ", ".join(params) + ")"
    return None  # No params = value, not function


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract all symbols from a parsed OCaml file.

    Detects:
    - value_definition: let bindings (functions)
    - type_definition: type declarations
    - module_definition: module declarations
    """
    analysis = FileAnalysis()
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
        sym_id = make_symbol_id("ocaml", file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language="ocaml",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[name] = sym

    for node in iter_tree(tree.root_node):
        if node.type == "value_definition":
            # Let binding - function definition
            let_binding = find_child_by_type(node, "let_binding")
            if let_binding:
                name = _get_let_binding_name(let_binding, source)
                if name:
                    # Extract signature
                    signature = _extract_ocaml_signature(let_binding, source)
                    add_symbol(node, name, "function", signature=signature)

        elif node.type == "type_definition":
            # Type definition
            type_binding = find_child_by_type(node, "type_binding")
            if type_binding:
                name = _get_type_name(type_binding, source)
                if name:
                    add_symbol(node, name, "type")

        elif node.type == "module_definition":
            # Module definition
            module_binding = find_child_by_type(node, "module_binding")
            if module_binding:
                name = _get_module_name(module_binding, source)
                if name:
                    add_symbol(node, name, "module")

    return analysis


def _find_enclosing_ocaml_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the function that contains this node by walking up the parent chain."""
    current = node.parent
    while current:
        if current.type == "value_definition":
            let_binding = find_child_by_type(current, "let_binding")
            if let_binding:
                name = _get_let_binding_name(let_binding, source)
                if name in local_symbols:
                    return local_symbols[name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_module_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract module aliases for disambiguation.

    In OCaml:
        module L = List -> L maps to List

    Returns a dict mapping alias names to full module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "module_definition":
            continue

        # Find module_binding: module L = List
        binding = find_child_by_type(node, "module_binding")
        if not binding:  # pragma: no cover - defensive for malformed AST
            continue

        # Get alias name (first module_name in binding)
        alias_node = find_child_by_type(binding, "module_name")
        if not alias_node:  # pragma: no cover - defensive for malformed AST
            continue

        alias_name = node_text(alias_node, source)

        # Get original module path (after =)
        module_path = find_child_by_type(binding, "module_path")
        if module_path:
            original = node_text(module_path, source)
            aliases[alias_name] = original

    return aliases


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    resolver: NameResolver,
    run_id: str,
    module_aliases: dict[str, str],
) -> list[Edge]:
    """Extract call and import edges from a parsed OCaml file.

    Args:
        module_aliases: Dict mapping module aliases to full names.

    Detects:
    - open_module: Import statements
    - application_expression: Function application (calls)
    """
    edges: list[Edge] = []
    file_id = make_file_id("ocaml", file_path)

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
                path_hint: Optional[str] = None

                if first_child.type == "value_path":
                    value_name = find_child_by_type(first_child, "value_name")
                    if value_name:
                        callee_name = node_text(value_name, source)

                    # Check for module prefix (L.map -> module_path 'L')
                    module_path_node = find_child_by_type(first_child, "module_path")
                    if module_path_node:
                        module_name_node = find_child_by_type(module_path_node, "module_name")
                        if module_name_node:
                            module_alias = node_text(module_name_node, source)
                            path_hint = module_aliases.get(module_alias)

                if callee_name and callee_name not in ("print_int", "print_string", "print_endline", "print_newline"):
                    # Find the caller (enclosing function)
                    caller = _find_enclosing_ocaml_function(node, source, local_symbols)
                    if caller:
                        # Resolve callee via global resolver
                        lookup_result = resolver.lookup(callee_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_application",
                                confidence=confidence,
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


class OcamlAnalyzer(TreeSitterAnalyzer):
    """OCaml language analyzer using tree-sitter-ocaml.

    Overrides _create_parser() because tree-sitter-ocaml uses language_ocaml()
    instead of the standard language() entry point.
    """

    lang = "ocaml"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.ml"]
    grammar_module = "tree_sitter_ocaml"
    create_file_symbols = True

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create parser using OCaml's non-standard language_ocaml() entry point."""
        import tree_sitter
        import tree_sitter_ocaml

        lang = tree_sitter.Language(tree_sitter_ocaml.language_ocaml())
        return tree_sitter.Parser(lang)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract OCaml symbols from a single file."""
        return _extract_symbols_from_tree(tree, source, rel_path, run.execution_id)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract OCaml module aliases (module L = List)."""
        return _extract_module_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from an OCaml file."""
        return _extract_edges_from_tree(
            tree, source, rel_path, local_symbols,
            resolver, run.execution_id, import_aliases,
        )


_analyzer = OcamlAnalyzer()


def is_ocaml_tree_sitter_available() -> bool:
    """Check if tree-sitter with OCaml grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("ocaml")
def analyze_ocaml(repo_root: Path) -> AnalysisResult:
    """Analyze OCaml files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-ocaml is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
