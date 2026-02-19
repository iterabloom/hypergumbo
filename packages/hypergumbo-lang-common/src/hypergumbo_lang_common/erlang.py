"""Erlang analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Erlang files and extract:
- Module definitions (-module)
- Function definitions (fun_decl)
- Record definitions (-record)
- Macro definitions (-define)
- Behaviour implementations (-behaviour)
- Type specifications (-spec, -type)
- Function call relationships
- Import statements (-import)

If tree-sitter with Erlang support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract modules, functions, records, macros, types with signatures
2. Pass 2: Extract call edges and import edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Erlang-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (language_pack_name)
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Erlang-Specific Considerations
------------------------------
- Erlang runs on the BEAM VM (same as Elixir)
- Functions are identified by name/arity (e.g., hello/1)
- Modules use -module(name) attribute
- Exports declared with -export([func/arity, ...])
- Behaviours define OTP patterns (gen_server, supervisor, etc.)
- Records are like structs with named fields
- Pattern matching is pervasive
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

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
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "erlang-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_erlang_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Erlang files in the repository."""
    yield from find_files(repo_root, ["*.erl", "*.hrl"])




def _extract_import_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases from Erlang -import statements (ADR-0007).

    Parses -import(module, [func/arity, ...]) and returns a mapping of
    function_name -> module_name for path hint resolution.

    Example:
        -import(lists, [map/2, filter/2]).
        => {"map": "lists", "filter": "lists"}

    Returns:
        Dict mapping function base name → module name for path_hint resolution.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "import_attribute":
            # First atom child is the module name
            module_atom = find_child_by_type(node, "atom")
            if not module_atom:
                continue  # pragma: no cover - defensive

            module_name = node_text(module_atom, source)

            # Find all fa (function/arity) nodes
            for child in node.children:
                if child.type == "fa":
                    # fa has atom (function name) child
                    func_atom = find_child_by_type(child, "atom")
                    if func_atom:
                        func_name = node_text(func_atom, source)
                        aliases[func_name] = module_name

    return aliases


def _extract_erlang_signature(
    clause: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_clause node.

    Returns signature in format: (Param1, Param2, {tuple, param})
    Erlang uses pattern matching, so params can be complex patterns.
    """
    args = find_child_by_type(clause, "expr_args")
    if args is None:  # pragma: no cover - defensive for malformed AST
        return "()"

    params: list[str] = []
    for child in args.children:
        if child.type in ("(", ")", ","):
            continue
        # Extract the parameter text directly
        param_text = node_text(child, source).strip()
        if param_text:
            params.append(param_text)

    return f"({', '.join(params)})"


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], str]:
    """Extract all symbols from a parsed Erlang file.

    Returns (symbols, module_name).

    Detects:
    - module_attribute (module name)
    - fun_decl (functions)
    - record_decl (records)
    - pp_define (macros)
    - behaviour_attribute (behaviours)
    - spec (type specs)
    - type_alias (types)
    """
    symbols: list[Symbol] = []
    module_name = ""

    for node in tree.root_node.children:
        # Module definition
        if node.type == "module_attribute":
            atom = find_child_by_type(node, "atom")
            if atom:
                module_name = node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("erlang", file_path, start_line, end_line, module_name, "module")
                symbols.append(Symbol(
                    id=sym_id,
                    name=module_name,
                    kind="module",
                    language="erlang",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Function definition
        elif node.type == "fun_decl":
            # Get function name from first function_clause
            clause = find_child_by_type(node, "function_clause")
            if clause:
                atom = find_child_by_type(clause, "atom")
                if atom:
                    func_name = node_text(atom, source)
                    # Count arguments for arity
                    args = find_child_by_type(clause, "expr_args")
                    arity = 0
                    if args:
                        # Count children that are not punctuation
                        for child in args.children:
                            if child.type not in ("(", ")", ","):
                                arity += 1

                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    # Include arity in name (Erlang convention)
                    full_name = f"{func_name}/{arity}"
                    sym_id = make_symbol_id("erlang", file_path, start_line, end_line, full_name, "function")
                    symbols.append(Symbol(
                        id=sym_id,
                        name=full_name,
                        kind="function",
                        language="erlang",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta={"arity": arity, "base_name": func_name},
                        signature=_extract_erlang_signature(clause, source),
                    ))

        # Record definition
        elif node.type == "record_decl":
            # Record name is an atom after 'record'
            atom = find_child_by_type(node, "atom")
            if atom:
                record_name = node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("erlang", file_path, start_line, end_line, record_name, "record")
                symbols.append(Symbol(
                    id=sym_id,
                    name=record_name,
                    kind="record",
                    language="erlang",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

        # Macro definition
        elif node.type == "pp_define":
            # Macro name from macro_lhs
            macro_lhs = find_child_by_type(node, "macro_lhs")
            if macro_lhs:
                var = find_child_by_type(macro_lhs, "var")
                if var:
                    macro_name = node_text(var, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = make_symbol_id("erlang", file_path, start_line, end_line, macro_name, "macro")
                    symbols.append(Symbol(
                        id=sym_id,
                        name=macro_name,
                        kind="macro",
                        language="erlang",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

        # Type alias
        elif node.type == "type_alias":
            # Type name is inside type_name node: type_name -> atom
            type_name_node = find_child_by_type(node, "type_name")
            atom = None
            if type_name_node:
                atom = find_child_by_type(type_name_node, "atom")
            if atom:
                type_name = node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = make_symbol_id("erlang", file_path, start_line, end_line, type_name, "type")
                symbols.append(Symbol(
                    id=sym_id,
                    name=type_name,
                    kind="type",
                    language="erlang",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                ))

    return symbols, module_name


def _get_enclosing_function_erlang(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Walk up parent chain to find enclosing function."""
    current = node.parent
    while current is not None:
        if current.type == "fun_decl":
            clause = find_child_by_type(current, "function_clause")
            if clause:
                atom = find_child_by_type(clause, "atom")
                if atom:
                    func_name = node_text(atom, source)
                    sym = local_symbols.get(func_name)
                    if sym:
                        return sym
        current = current.parent
    return None


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    module_registry: dict[str, str],  # module_name -> file_path
    run_id: str,
    import_aliases: dict[str, str] | None = None,  # func -> module (ADR-0007)
) -> list[Edge]:
    """Extract call and import edges from a parsed Erlang file.

    Detects:
    - Function calls (call nodes with remote or local)
    - Behaviour implementation (-behaviour)
    - Import statements (-import)
    """
    edges: list[Edge] = []
    file_id = make_file_id("erlang", file_path)

    # Build local symbol map (name -> symbol)
    local_symbols = {s.name: s for s in file_symbols}
    # Also map base_name for function lookup
    for s in file_symbols:
        if s.kind == "function" and s.meta:
            base = s.meta.get("base_name")
            if base:
                local_symbols[base] = s

    for node in iter_tree(tree.root_node):
        if node.type == "behaviour_attribute":
            # -behaviour(gen_server)
            atom = find_child_by_type(node, "atom")
            if atom:
                behaviour_name = node_text(atom, source)
                # Create import edge to behaviour module
                module_id = f"erlang:{behaviour_name}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="behaviour",
                    confidence=0.95,
                )
                edges.append(edge)

        elif node.type == "import_attribute":
            # -import(module, [func/arity, ...])
            # First atom is module name
            atoms = [c for c in node.children if c.type == "atom"]
            if atoms:
                module_name = node_text(atoms[0], source)
                module_id = f"erlang:{module_name}:0-0:module:module"
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

        elif node.type == "call":
            # Function call
            caller = _get_enclosing_function_erlang(node, source, local_symbols)
            if caller:
                remote = find_child_by_type(node, "remote")
                if remote:
                    # Remote call: module:function(args)
                    remote_module = find_child_by_type(remote, "remote_module")
                    if remote_module:
                        mod_atom = find_child_by_type(remote_module, "atom")
                        func_atom = None
                        # Find the function name (second atom in remote)
                        for child in remote.children:
                            if child.type == "atom":
                                func_atom = child
                        if mod_atom and func_atom:
                            mod_name = node_text(mod_atom, source)
                            func_name = node_text(func_atom, source)
                            # Try to find in registry
                            full_name = f"{mod_name}:{func_name}"
                            lookup_result = resolver.lookup(full_name)
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
                                    evidence_type="remote_call",
                                    confidence=confidence,
                                )
                                edges.append(edge)
                else:
                    # Local call: function(args)
                    atom = find_child_by_type(node, "atom")
                    if atom:
                        func_name = node_text(atom, source)
                        # ADR-0007: Use import module as path_hint if available
                        path_hint = None
                        if import_aliases and func_name in import_aliases:
                            path_hint = import_aliases[func_name]
                        lookup_result = resolver.lookup(func_name, path_hint=path_hint)
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
                                evidence_type="local_call",
                                confidence=confidence,
                            )
                            edges.append(edge)

    return edges


class ErlangAnalyzer(TreeSitterAnalyzer):
    """Erlang language analyzer using tree-sitter-language-pack.

    Handles Erlang-specific registration: functions are indexed by full name
    (name/arity), base name, and module-prefixed name for remote call resolution.
    """

    lang = "erlang"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.erl", "*.hrl"]
    language_pack_name = "erlang"
    create_file_symbols = True

    # Track module names per file for cross-file resolution
    _module_registry: dict[str, str]  # module_name -> file_path
    _file_module_names: dict[str, str]  # rel_path -> module_name

    def __init__(self) -> None:
        self._module_registry = {}
        self._file_module_names = {}

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract modules, functions, records, macros, types from an Erlang file."""
        analysis = FileAnalysis()

        file_symbols, module_name = _extract_symbols_from_file(
            tree, source, rel_path, run.execution_id,
        )
        analysis.symbols.extend(file_symbols)

        # Store module name for this file (used in register_symbol)
        if module_name:
            self._file_module_names[rel_path] = module_name
            self._module_registry[module_name] = rel_path

        # Build symbol_by_name with base_name mapping for local call resolution
        for sym in file_symbols:
            analysis.symbol_by_name[sym.name] = sym
            if sym.kind == "function" and sym.meta:
                base = sym.meta.get("base_name")
                if base:
                    analysis.symbol_by_name[base] = sym

        return analysis

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract Erlang -import aliases for path hint resolution (ADR-0007)."""
        return _extract_import_aliases(tree.root_node, source)

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol with Erlang-specific indexing.

        Functions are indexed by full name (name/arity), base name, and
        module-prefixed base name for remote call resolution.
        """
        global_symbols[symbol.name] = symbol
        if symbol.kind == "function" and symbol.meta:
            base_name = symbol.meta.get("base_name", "")
            if base_name:
                global_symbols[base_name] = symbol
                module_name = self._file_module_names.get(symbol.path, "")
                if module_name:
                    global_symbols[f"{module_name}:{base_name}"] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract call and import edges from an Erlang file."""
        # Rebuild local symbols with base_name mapping needed by edge extractor
        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, self._module_registry, run.execution_id,
            import_aliases=import_aliases,
        )

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Reset per-run state and delegate to base class."""
        self._module_registry = {}
        self._file_module_names = {}
        return super().analyze(repo_root, max_files)


_analyzer = ErlangAnalyzer()


def is_erlang_tree_sitter_available() -> bool:
    """Check if tree-sitter with Erlang grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("erlang")
def analyze_erlang(repo_root: Path) -> AnalysisResult:
    """Analyze Erlang files in a repository."""
    return _analyzer.analyze(repo_root)
