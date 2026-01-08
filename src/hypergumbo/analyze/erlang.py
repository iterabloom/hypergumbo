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
1. Check if tree-sitter-language-pack (erlang) is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for grammar (erlang)
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

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "erlang-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_erlang_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Erlang files in the repository."""
    yield from find_files(repo_root, ["*.erl", "*.hrl"])


def is_erlang_tree_sitter_available() -> bool:
    """Check if tree-sitter with Erlang grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("erlang")
        return True
    except Exception:  # pragma: no cover - erlang not supported
        return False


@dataclass
class ErlangAnalysisResult:
    """Result of analyzing Erlang files."""

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
    module_name: str  # Erlang module name from -module attribute


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"erlang:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Erlang file node (used as import edge source)."""
    return f"erlang:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive fallback


def _extract_erlang_signature(
    clause: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a function_clause node.

    Returns signature in format: (Param1, Param2, {tuple, param})
    Erlang uses pattern matching, so params can be complex patterns.
    """
    args = _find_child_by_type(clause, "expr_args")
    if args is None:  # pragma: no cover - defensive for malformed AST
        return "()"

    params: list[str] = []
    for child in args.children:
        if child.type in ("(", ")", ","):
            continue
        # Extract the parameter text directly
        param_text = _node_text(child, source).strip()
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
            atom = _find_child_by_type(node, "atom")
            if atom:
                module_name = _node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, module_name, "module")
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
            clause = _find_child_by_type(node, "function_clause")
            if clause:
                atom = _find_child_by_type(clause, "atom")
                if atom:
                    func_name = _node_text(atom, source)
                    # Count arguments for arity
                    args = _find_child_by_type(clause, "expr_args")
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
                    sym_id = _make_symbol_id(file_path, start_line, end_line, full_name, "function")
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
            atom = _find_child_by_type(node, "atom")
            if atom:
                record_name = _node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, record_name, "record")
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
            macro_lhs = _find_child_by_type(node, "macro_lhs")
            if macro_lhs:
                var = _find_child_by_type(macro_lhs, "var")
                if var:
                    macro_name = _node_text(var, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    span = Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    )
                    sym_id = _make_symbol_id(file_path, start_line, end_line, macro_name, "macro")
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
            type_name_node = _find_child_by_type(node, "type_name")
            atom = None
            if type_name_node:
                atom = _find_child_by_type(type_name_node, "atom")
            if atom:
                type_name = _node_text(atom, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                span = Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                sym_id = _make_symbol_id(file_path, start_line, end_line, type_name, "type")
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
            clause = _find_child_by_type(current, "function_clause")
            if clause:
                atom = _find_child_by_type(clause, "atom")
                if atom:
                    func_name = _node_text(atom, source)
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
    global_symbol_registry: dict[str, Symbol],
    module_registry: dict[str, str],  # module_name -> file_path
    run_id: str,
) -> list[Edge]:
    """Extract call and import edges from a parsed Erlang file.

    Detects:
    - Function calls (call nodes with remote or local)
    - Behaviour implementation (-behaviour)
    - Import statements (-import)
    """
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

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
            atom = _find_child_by_type(node, "atom")
            if atom:
                behaviour_name = _node_text(atom, source)
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
                module_name = _node_text(atoms[0], source)
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
                remote = _find_child_by_type(node, "remote")
                if remote:
                    # Remote call: module:function(args)
                    remote_module = _find_child_by_type(remote, "remote_module")
                    if remote_module:
                        mod_atom = _find_child_by_type(remote_module, "atom")
                        func_atom = None
                        # Find the function name (second atom in remote)
                        for child in remote.children:
                            if child.type == "atom":
                                func_atom = child
                        if mod_atom and func_atom:
                            mod_name = _node_text(mod_atom, source)
                            func_name = _node_text(func_atom, source)
                            # Try to find in registry
                            full_name = f"{mod_name}:{func_name}"
                            callee = global_symbol_registry.get(full_name)
                            if callee:
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="remote_call",
                                    confidence=0.85,
                                )
                                edges.append(edge)
                else:
                    # Local call: function(args)
                    atom = _find_child_by_type(node, "atom")
                    if atom:
                        func_name = _node_text(atom, source)
                        callee = local_symbols.get(func_name) or global_symbol_registry.get(func_name)
                        if callee:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="local_call",
                                confidence=0.85,
                            )
                            edges.append(edge)

    return edges


def analyze_erlang(repo_root: Path) -> ErlangAnalysisResult:
    """Analyze Erlang files in a repository.

    Returns an ErlangAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_erlang_tree_sitter_available():  # pragma: no cover - tested via mock
        skip_reason = (
            "Erlang analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ErlangAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("erlang")
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    module_registry: dict[str, str] = {}  # module_name -> file_path
    files_analyzed = 0

    for erl_file in find_erlang_files(repo_root):
        try:
            source = erl_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(erl_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="erlang",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols, module_name = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Register module
        if module_name:
            module_registry[module_name] = rel_path

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym
            # Also register with module prefix for remote calls
            if module_name and sym.kind == "function":
                base_name = sym.meta.get("base_name", "") if sym.meta else ""
                if base_name:
                    global_symbol_registry[f"{module_name}:{base_name}"] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            module_name=module_name,
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
            module_registry,
            run_id,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return ErlangAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
