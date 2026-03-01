"""Solidity analysis pass using tree-sitter-solidity.

This analyzer uses tree-sitter to parse Solidity smart contract files and extract:
- Contract declarations
- Interface declarations
- Library declarations
- Function definitions
- Constructor definitions
- Modifier definitions
- Event definitions
- Function call relationships
- Import relationships

If tree-sitter with Solidity support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Parse all files, extract all symbols into global registry
- Pass 2: Detect calls, imports, and resolve against global symbol registry

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Solidity-specific
extraction logic.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-solidity package for grammar (direct grammar module)
- Two-pass allows cross-file call resolution
- Solidity-specific: contracts, modifiers, events are first-class symbols
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
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

PASS_ID = make_pass_id("solidity")


def find_solidity_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Solidity files in the repository."""
    yield from find_files(repo_root, ["*.sol"])


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_contract(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing contract/interface/library name."""
    current = node.parent
    while current is not None:
        if current.type in ("contract_declaration", "interface_declaration", "library_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_solidity(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    symbols_by_span: Optional[dict[tuple[int, int], Symbol]] = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/constructor/modifier.

    Uses position-based matching (symbols_by_span) when available to correctly
    handle Solidity function overloading. Without it, overloaded functions
    (same name, different parameters) would resolve to the last-registered
    overload because symbol_by_name can only hold one entry per name.
    """
    current = node.parent
    while current is not None:
        if current.type in ("function_definition", "constructor_definition", "modifier_definition"):
            start_line = current.start_point[0] + 1
            end_line = current.end_point[0] + 1
            # Prefer position-based match (handles overloads correctly)
            if symbols_by_span is not None:
                sym = symbols_by_span.get((start_line, end_line))
                if sym:
                    return sym
            # Fall back to name-based lookup (when symbols_by_span not provided)
            if current.type == "function_definition":  # pragma: no cover - position match preferred
                name_node = _find_child_by_field(current, "name")
                if name_node:
                    func_name = node_text(name_node, source)
                    sym = local_symbols.get(func_name) or global_symbols.get(func_name)
                    if sym:
                        return sym
            elif current.type == "constructor_definition":  # pragma: no cover - constructor calls rare
                sym = local_symbols.get("constructor") or global_symbols.get("constructor")
                if sym:
                    return sym
            elif current.type == "modifier_definition":  # pragma: no cover - modifier calls rare
                name_node = _find_child_by_field(current, "name")
                if name_node:
                    mod_name = node_text(name_node, source)
                    sym = local_symbols.get(mod_name) or global_symbols.get(mod_name)
                    if sym:
                        return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_solidity_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Solidity function definition.

    Solidity syntax: function name(type1 param1, type2 param2) returns (type3)
    Returns signature like "(address to, uint256 amount) returns (bool)".
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        # Parameters are direct children with type "parameter"
        if child.type == "parameter":
            param_text = node_text(child, source).strip()
            if param_text:
                params.append(param_text)
        elif child.type == "return_type_definition":
            # Return type definition: returns (type)
            return_type = node_text(child, source).strip()

    sig = "(" + ", ".join(params) + ")"
    if return_type:
        sig += f" {return_type}"
    return sig


def _extract_import_aliases(
    node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str, dict[str, str]]:
    """Extract import path and alias mappings from an import directive.

    Solidity import patterns:
    - import "file.sol";                      -> path, no aliases
    - import * as Alias from "file.sol";      -> path, {Alias: path}
    - import {X as Y} from "file.sol";        -> path, {Y: path}
    - import {X, Y as Z} from "file.sol";     -> path, {Z: path}

    Returns (import_path, {alias: import_path}).
    """
    import_path = ""
    aliases: dict[str, str] = {}

    # Find the import path (string node)
    string_node = find_child_by_type(node, "string")
    if string_node:
        import_path = node_text(string_node, source).strip('"\'')

    if not import_path:
        return "", {}  # pragma: no cover - defensive

    # Look for alias patterns
    children = list(node.children)
    i = 0
    while i < len(children):
        child = children[i]
        if child.type == "as" and i + 1 < len(children):
            # The next identifier is the alias
            next_child = children[i + 1]
            if next_child.type == "identifier":
                alias = node_text(next_child, source)
                aliases[alias] = import_path
        i += 1

    return import_path, aliases


def _extract_visibility_modifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract visibility and state mutability modifiers from a Solidity function.

    Solidity functions can have:
    - Visibility: public, external, internal, private
    - State mutability: view, pure, payable

    These are direct children of function_definition nodes with types
    'visibility' and 'state_mutability' in the tree-sitter-solidity grammar.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "visibility":
            modifiers.append(node_text(child, source))
        elif child.type == "state_mutability":
            modifiers.append(node_text(child, source))
    return modifiers


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    run_id: str, analysis: FileAnalysis,
) -> None:
    """Extract symbols from a single Solidity file's parse tree."""
    def add_symbol(
        name: str,
        kind: str,
        node: "tree_sitter.Node",
        prefix: str = "",
        signature: Optional[str] = None,
        modifiers: Optional[list[str]] = None,
    ) -> Symbol:
        """Helper to create and register a symbol."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        full_name = f"{prefix}.{name}" if prefix else name

        symbol = Symbol(
            id=make_symbol_id("solidity", file_path, start_line, end_line, full_name, kind),
            name=full_name,
            kind=kind,
            language="solidity",
            path=file_path,
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            modifiers=modifiers or [],
        )
        analysis.symbols.append(symbol)
        analysis.symbol_by_name[name] = symbol
        analysis.symbol_by_name[full_name] = symbol
        return symbol

    for node in iter_tree(tree.root_node):
        # Contract declaration
        if node.type == "contract_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                contract_name = node_text(name_node, source)
                add_symbol(contract_name, "contract", node)

        # Interface declaration
        elif node.type == "interface_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                interface_name = node_text(name_node, source)
                add_symbol(interface_name, "interface", node)

        # Library declaration
        elif node.type == "library_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                lib_name = node_text(name_node, source)
                add_symbol(lib_name, "library", node)

        # Function definition
        elif node.type == "function_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                signature = _extract_solidity_signature(node, source)
                modifiers = _extract_visibility_modifiers(node, source)
                add_symbol(func_name, "function", node, current_contract, signature=signature, modifiers=modifiers)

        # Constructor definition
        elif node.type == "constructor_definition":
            current_contract = _get_enclosing_contract(node, source) or ""
            add_symbol("constructor", "constructor", node, current_contract)

        # Modifier definition
        elif node.type == "modifier_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                mod_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(mod_name, "modifier", node, current_contract)

        # Event definition
        elif node.type == "event_definition":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                event_name = node_text(name_node, source)
                current_contract = _get_enclosing_contract(node, source) or ""
                add_symbol(event_name, "event", node, current_contract)


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree", source: bytes, file_path: str,
    local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
    run_id: str, resolver: NameResolver,
    all_local_symbols: Optional[list[Symbol]] = None,
) -> tuple[list[Edge], dict[str, str]]:
    """Extract edges (calls, imports) from a Solidity file's parse tree.

    Returns (edges, import_aliases) where import_aliases maps alias names
    to import paths for path_hint resolution.

    The all_local_symbols parameter provides the full list of symbols for
    position-based lookup, needed to correctly resolve overloaded functions
    (where symbol_by_name would only return the last-registered overload).
    """
    edges: list[Edge] = []
    import_aliases: dict[str, str] = {}
    file_id = make_file_id("solidity", file_path)

    # Build position-based symbol index for overload-safe enclosing function lookup.
    # Maps (start_line, end_line) -> Symbol for functions/constructors/modifiers.
    symbols_by_span: dict[tuple[int, int], Symbol] = {}
    if all_local_symbols:
        for sym in all_local_symbols:
            if sym.kind in ("function", "constructor", "modifier"):
                symbols_by_span[(sym.span.start_line, sym.span.end_line)] = sym

    # Collect `using Library for Type` directives per contract.
    # Maps contract_name -> set of library names, used to resolve
    # implicit library calls like x.add(y) -> SafeMath.add(x, y).
    using_libraries: dict[str, set[str]] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "using_directive":
            contract_name = _get_enclosing_contract(node, source) or ""
            # Extract library name from type_alias child (using Lib for Type)
            for child in node.children:
                if child.type == "type_alias":
                    id_node = find_child_by_type(child, "identifier")
                    if id_node:
                        lib_name = node_text(id_node, source)
                        using_libraries.setdefault(contract_name, set()).add(lib_name)

    # First pass: extract import aliases
    for node in iter_tree(tree.root_node):
            import_path, aliases = _extract_import_aliases(node, source)
            if import_path:
                edge = Edge.create(
                    src=file_id,
                    dst=import_path,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                edges.append(edge)
                import_aliases.update(aliases)

    # Second pass: extract inheritance and call edges
    for node in iter_tree(tree.root_node):
        # Contract/interface inheritance: contract A is B, C { ... }
        if node.type in ("contract_declaration", "interface_declaration"):
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                child_name = node_text(name_node, source)
                child_sym = local_symbols.get(child_name) or global_symbols.get(child_name)
                if child_sym:
                    for child in node.children:
                        if child.type == "inheritance_specifier":
                            parent_type_node = find_child_by_type(child, "user_defined_type")
                            if parent_type_node:
                                parent_name = node_text(parent_type_node, source)
                                parent_sym = local_symbols.get(parent_name) or global_symbols.get(parent_name)
                                if parent_sym:
                                    edge = Edge.create(
                                        src=child_sym.id,
                                        dst=parent_sym.id,
                                        edge_type="inherits",
                                        line=child.start_point[0] + 1,
                                        confidence=0.95,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    )
                                    edges.append(edge)

        # Function call
        elif node.type == "call_expression":
            func_node = _find_child_by_field(node, "function")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols,
                symbols_by_span=symbols_by_span,
            )
            if func_node and current_function:
                call_name = node_text(func_node, source)
                # Strip super./this. prefix — these resolve to the same
                # contract's methods (super dispatches to parent, this
                # dispatches to the current contract via external call).
                if call_name.startswith(("super.", "this.")):
                    call_name = call_name.split(".", 1)[1]
                # Try to resolve the called function - local first
                target = local_symbols.get(call_name)
                if target:
                    edge = Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="calls",
                        line=node.start_point[0] + 1,
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)
                else:
                    # Get path_hint from import aliases if available
                    path_hint = import_aliases.get(call_name)
                    # Try global symbols via resolver with path_hint
                    lookup_result = resolver.lookup(call_name, path_hint=path_hint)
                    if lookup_result.found and lookup_result.symbol is not None:  # pragma: no cover - suffix fallback
                        edge = Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.90 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                        edges.append(edge)
                    elif "." in call_name:
                        # Member access fallback: IERC20(token).transfer(...)
                        # or contract.method() — try the method name after the
                        # last dot against local/global symbols.
                        method_name = call_name.rsplit(".", 1)[1]
                        # Prefer library-qualified lookup via using directives.
                        # This correctly resolves x.add(y) -> SafeMath.add
                        # when 'using SafeMath for uint256' is declared.
                        enclosing_contract = _get_enclosing_contract(
                            node, source,
                        ) or ""
                        member_target = None
                        for lib_name in using_libraries.get(enclosing_contract, ()):
                            qualified = f"{lib_name}.{method_name}"
                            candidate = (
                                local_symbols.get(qualified)
                                or global_symbols.get(qualified)
                            )
                            if candidate and candidate.kind == "function":
                                member_target = candidate
                                break
                        if not member_target:
                            member_target = (
                                local_symbols.get(method_name)
                                or global_symbols.get(method_name)
                            )
                        if member_target:
                            edge = Edge.create(
                                src=current_function.id,
                                dst=member_target.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.60,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            )
                            edges.append(edge)

        # Emit statement: emit Transfer(...) → emits edge to event definition
        elif node.type == "emit_statement":
            # emit_statement children: "emit", expression (event name), "(", args, ")"
            event_name_node = find_child_by_type(node, "expression")
            current_function = _get_enclosing_function_solidity(
                node, source, local_symbols, global_symbols,
                symbols_by_span=symbols_by_span,
            )
            if event_name_node and current_function:
                event_name = node_text(event_name_node, source)
                event_sym = local_symbols.get(event_name) or global_symbols.get(event_name)
                if event_sym and event_sym.kind == "event":
                    edge = Edge.create(
                        src=current_function.id,
                        dst=event_sym.id,
                        edge_type="emits",
                        line=node.start_point[0] + 1,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)

    # Third pass: override edges.
    # For each contract that inherits from parents, connect child functions
    # to parent functions with the same unqualified name.
    for node in iter_tree(tree.root_node):
        if node.type not in ("contract_declaration", "interface_declaration"):
            continue
        name_node = find_child_by_type(node, "identifier")
        if not name_node:
            continue  # pragma: no cover - defensive
        child_contract = node_text(name_node, source)

        # Collect parent contract names from inheritance_specifier
        parent_names: list[str] = []
        for child in node.children:
            if child.type == "inheritance_specifier":
                parent_type_node = find_child_by_type(child, "user_defined_type")
                if parent_type_node:
                    parent_names.append(node_text(parent_type_node, source))

        if not parent_names:
            continue

        # Collect functions defined in this contract
        child_functions: dict[str, Symbol] = {}
        for sym in (all_local_symbols or []):
            if sym.kind == "function" and sym.name.startswith(f"{child_contract}."):
                unqualified = sym.name[len(child_contract) + 1:]
                child_functions[unqualified] = sym

        # Create override edges for matching parent functions
        for func_name, child_sym in child_functions.items():
            for parent_name in parent_names:
                parent_qualified = f"{parent_name}.{func_name}"
                parent_sym = (
                    local_symbols.get(parent_qualified)
                    or global_symbols.get(parent_qualified)
                )
                if parent_sym and parent_sym.kind == "function":
                    edge = Edge.create(
                        src=child_sym.id,
                        dst=parent_sym.id,
                        edge_type="overrides",
                        line=child_sym.span.start_line,
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)

    return edges, import_aliases


# ---------------------------------------------------------------------------
# SolidityAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class SolidityAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Solidity smart contract files using TreeSitterAnalyzer base class.

    Stores per-file symbol lists between Pass 1 and Pass 2 to enable
    position-based enclosing function resolution. This is needed because
    Solidity supports function overloading (same name, different params),
    and the name-based symbol_by_name dict can only hold one entry per name.
    """

    lang = "solidity"
    file_patterns: ClassVar[list[str]] = ["*.sol"]
    grammar_module = "tree_sitter_solidity"

    def __init__(self) -> None:
        super().__init__()
        self._file_symbols: dict[str, list[Symbol]] = {}

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Solidity file."""
        analysis = FileAnalysis()
        _extract_symbols_from_tree(
            tree, source, str(file_path), run.execution_id, analysis,
        )
        # Store full symbol list for position-based lookup in Pass 2
        self._file_symbols[str(file_path)] = list(analysis.symbols)
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and import edges from a single Solidity file."""
        all_local = self._file_symbols.get(str(file_path))
        edges, _aliases = _extract_edges_from_tree(
            tree, source, str(file_path),
            local_symbols, global_symbols,
            run.execution_id, resolver,
            all_local_symbols=all_local,
        )
        return edges


_analyzer = SolidityAnalyzer()


def is_solidity_tree_sitter_available() -> bool:
    """Check if tree-sitter with Solidity grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("solidity")
def analyze_solidity(repo_root: Path) -> AnalysisResult:
    """Analyze Solidity files in a repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols, edges, and analysis run info.
    """
    return _analyzer.analyze(repo_root)
