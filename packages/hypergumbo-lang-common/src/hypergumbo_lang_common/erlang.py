# SPDX-License-Identifier: AGPL-3.0-or-later
"""Erlang analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Erlang files and extract:
- Module definitions (-module)
- Function definitions (fun_decl)
- Record definitions (-record)
- Macro definitions (-define)
- Behaviours as a TYPE relationship (WI-hidit, the shape WI-vitas gave
  Elixir). ``-callback area() -> integer().`` is a symbol named ``area/0``
  (kind ``function``, ``modifiers=["abstract"]``, ``meta["optional"]`` when
  ``-optional_callbacks`` lists it), kept out of call resolution because it
  has no body. ``-behaviour(X)`` (or ``-behavior``) emits ``implements`` from
  the module to every in-repo module named X, else to
  ``erlang:external:0-0:X:unresolved``, and each of X's callbacks
  ``dispatches_to`` this module's function of the same ``name/arity``. The
  five OTP behaviours additionally root their callbacks from
  ``OTP_BEHAVIOUR_CALLBACKS`` (module -> callback ``dispatches_to``), since
  OTP's own ``-callback`` declarations are not in the tree. The directive
  also keeps its ``imports`` edge (evidence ``behaviour``)
- Type declarations (-type). ``-spec`` is NOT extracted — no dispatch
  branch matches a spec node.
- Function call relationships, including synthetic edges to external
  callees so a remote call still reaches I/O-boundary matching
- Logger macro calls: in a file that includes a ``logger.hrl`` header, each
  of the eight OTP ``?LOG_<LEVEL>(...)`` macros becomes a ``calls`` edge from
  the enclosing function to ``erlang:logger:0-0:<level>:function`` (evidence
  ``macro_expansion``, an inference, since tree-sitter expands no macros), so
  logging call sites can match catalogue rows
- Import statements (-import)

Erlang's multi-clause functions are coalesced: consecutive ``fun_decl``
clauses for the same name/arity become ONE symbol, with cyclomatic
complexity summed across the clauses and the line span covering all of
them. Emitting a symbol per clause would triple-count a idiomatic
pattern-matched function.

Function and macro identity (WI-tusad). A function's stable_id is the
ADR-0035 producer formula, minted at construction through
``overload_arity.declared_arity_stable_id`` from ``name/arity`` and the
``meta["arity"]`` count, the same inputs ``callback_stable_id`` hashes for a
``-callback`` (which adds the ``abstract`` decorator, so a callback and a
same-named function in one module stay apart). The scope chain is the file:
the compiler binds one module to one file. A ``-define(NAME(A, B), ...)``
macro records ``A, B`` in ``meta["parameters"]``, so ``?MAX`` and
``?MAX(X)`` (Erlang allows both) hash apart; a macro without an argument
list takes the zero-parameter formula. Before this both kinds rode the
backstop floor, and a same-named pair in one file (``-ifdef``/``-else``
alternatives) was left null; such a pair is now a same-scope tie the
per-file occurrence pass orders. A ``-record(name, ...)`` mints the same
formula over its name with no arity (record names are unique per module).

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

import re

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Iterator, Optional

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
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    ARITY_KEY, PARAMETERS_KEY, declared_arity, declared_arity_stable_id,
    parameter_entry,
)
from hypergumbo_core.behaviours import (
    CALLBACK_MODIFIER,
    behaviour_implements_edge,
    callback_dispatch_edge,
    callback_stable_id,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("erlang")

# OTP behaviour callback mappings.
# When a module declares -behaviour(gen_server), these callbacks may be
# implemented. The OTP framework invokes them at runtime; without edges
# they appear as disconnected orphans in the call graph.
# Keys are behaviour names (atoms), values are lists of callback function names.
OTP_BEHAVIOUR_CALLBACKS: dict[str, list[str]] = {
    "gen_server": [
        "init", "handle_call", "handle_cast", "handle_info",
        "terminate", "code_change", "handle_continue",
    ],
    "supervisor": ["init"],
    "gen_statem": [
        "init", "callback_mode", "handle_event",
        "terminate", "code_change",
    ],
    "gen_event": [
        "init", "handle_event", "handle_call", "handle_info",
        "terminate", "code_change",
    ],
    "application": ["start", "stop"],
}


def _is_callback(symbol: Symbol) -> bool:
    """Is ``symbol`` a ``-callback`` declaration rather than a function?"""
    return CALLBACK_MODIFIER in symbol.modifiers


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


def _optional_callbacks(root: "tree_sitter.Node", source: bytes) -> set[str]:
    """``name/arity`` of every callback ``-optional_callbacks([...])`` lists.

    The attribute names callbacks declared elsewhere in the module; it
    declares none itself, so it marks symbols rather than minting them.
    """
    names: set[str] = set()
    for node in root.children:
        if node.type != "optional_callbacks_attribute":
            continue
        for fa in node.children:
            atom = find_child_by_type(fa, "atom") if fa.type == "fa" else None
            arity = find_child_by_type(fa, "arity") if atom is not None else None
            count = find_child_by_type(arity, "integer") if arity is not None else None
            if atom is not None and count is not None:
                names.add(f"{node_text(atom, source)}/{node_text(count, source)}")
    return names


def _callback_symbol(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    run_id: str,
    optional_callbacks: set[str],
) -> Optional[Symbol]:
    """The symbol a ``-callback name(Args) -> T.`` declares, or None.

    Named ``name/arity`` like every Erlang function, which is what lets a
    callback be matched to its implementation exactly. Both spellings parse
    to the same node -- ``-callback f() -> t().`` and ``-callback(f() -> t()).``
    -- and a multi-clause spec (``(a()) -> x; (b()) -> y``) is one callback:
    every clause has the same arity. The signature is the declaration
    surface after the name, verbatim (ADR-0058).

    The stable_id is minted here (``callback_stable_id``), not left to the
    backstop floor, where it would collide with a same-named ``area/0``
    defined in the behaviour module itself and null both.
    """
    atom = find_child_by_type(node, "atom")
    sigs = [c for c in node.children if c.type == "type_sig"]
    if atom is None or not sigs:  # pragma: no cover - grammar always gives both
        return None
    name = node_text(atom, source)
    arity = _arity_of(sigs[0])
    full_name = f"{name}/{arity}"
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    meta: dict[str, object] = {"arity": arity, "base_name": name}
    if full_name in optional_callbacks:
        meta["optional"] = True
    return Symbol(
        id=make_symbol_id(
            "erlang", file_path, start_line, end_line, full_name, "function",
        ),
        stable_id=callback_stable_id("erlang", file_path, full_name, arity),
        name=full_name,
        kind="function",
        language="erlang",
        path=file_path,
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        ),
        origin=PASS_ID,
        origin_run_id=run_id,
        meta=meta,
        signature=source[sigs[0].start_byte:sigs[-1].end_byte].decode(
            "utf-8", errors="replace",
        ),
        modifiers=[CALLBACK_MODIFIER],
    )


def _macro_meta(
    macro_lhs: "tree_sitter.Node", source: bytes,
) -> Optional[dict[str, object]]:
    """``meta["parameters"]`` of ``-define(NAME(A, B), ...)``; None for ``-define(NAME, ...)``.

    ``NAME()`` gives the empty list. A macro without an argument list has no
    parameters to record, so it gets no meta (see the module docstring).
    """
    var_args = find_child_by_type(macro_lhs, "var_args")
    if var_args is None:
        return None
    return {
        PARAMETERS_KEY: [
            parameter_entry(node_text(arg, source))
            for arg in var_args.children if arg.type == "var"
        ],
    }


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
    - callback (behaviour callbacks, ``name/arity`` with ``abstract``)
    - type_alias (types)
    """
    symbols: list[Symbol] = []
    module_name = ""
    optional_callbacks = _optional_callbacks(tree.root_node, source)

    # Track pending function for clause coalescing.  Tree-sitter Erlang
    # emits a separate fun_decl per clause (e.g. fib(0)->0; fib(1)->1;
    # fib(N)->… produces 3 fun_decl nodes).  We coalesce consecutive
    # fun_decl nodes with the same name/arity into a single symbol whose
    # span covers all clauses.
    pending_func: dict | None = None  # {name, arity, start_line, end_line, ...}

    def _flush_pending_func() -> None:
        """Emit the coalesced function symbol from pending_func."""
        nonlocal pending_func
        if pending_func is None:
            return
        pf = pending_func
        pending_func = None
        span = Span(
            start_line=pf["start_line"],
            end_line=pf["end_line"],
            start_col=pf["start_col"],
            end_col=pf["end_col"],
        )
        full_name = f"{pf['func_name']}/{pf['arity']}"
        sym_id = make_symbol_id(
            "erlang", file_path, pf["start_line"], pf["end_line"],
            full_name, "function",
        )
        # INV-loguk: aggregate CC across all coalesced clauses. The first
        # clause is the implicit entry path (base 1); each clause contributes
        # its own decision points (its per-clause CC minus its own base 1).
        cc = 1
        for clause_node in pf["clause_nodes"]:
            cc += compute_cyclomatic_complexity(clause_node, "erlang") - 1
        meta: dict[str, object] = {
            ARITY_KEY: pf["arity"], "base_name": pf["func_name"],
        }
        symbols.append(Symbol(
            id=sym_id,
            stable_id=declared_arity_stable_id(
                kind="function", lang="erlang", path=file_path,
                name=full_name, qualified_name=full_name,
                arity=declared_arity(meta),
            ),
            name=full_name,
            kind="function",
            language="erlang",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            meta=meta,
            signature=pf["signature"],
            cyclomatic_complexity=cc,
            line_span=pf["end_line"] - pf["start_line"] + 1,
        ))

    for node in tree.root_node.children:
        # Module definition
        if node.type == "module_attribute":
            _flush_pending_func()
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

        # Function definition — coalesce consecutive clauses
        elif node.type == "fun_decl":
            clause = find_child_by_type(node, "function_clause")
            if clause:
                atom = find_child_by_type(clause, "atom")
                if atom:
                    func_name = node_text(atom, source)
                    arity = _arity_of(clause)

                    clause_start = node.start_point[0] + 1
                    clause_end = node.end_point[0] + 1

                    # Same function as pending? Extend span.
                    if (
                        pending_func is not None
                        and pending_func["func_name"] == func_name
                        and pending_func["arity"] == arity
                    ):
                        pending_func["end_line"] = clause_end
                        pending_func["end_col"] = node.end_point[1]
                        # INV-loguk: collect each clause's fun_decl node so the
                        # coalesced symbol's CC aggregates every clause's branches.
                        pending_func["clause_nodes"].append(node)
                    else:
                        # Different function — flush previous, start new
                        _flush_pending_func()
                        pending_func = {
                            "func_name": func_name,
                            "arity": arity,
                            "start_line": clause_start,
                            "end_line": clause_end,
                            "start_col": node.start_point[1],
                            "end_col": node.end_point[1],
                            "signature": _extract_erlang_signature(
                                clause, source,
                            ),
                            "clause_nodes": [node],
                        }
            continue

        # Record definition
        elif node.type == "record_decl":
            _flush_pending_func()
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
                    stable_id=declared_arity_stable_id(
                        kind="record", lang="erlang", path=file_path,
                        name=record_name, qualified_name=record_name, arity=None,
                    ),
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
            _flush_pending_func()
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
                    macro_meta = _macro_meta(macro_lhs, source)
                    symbols.append(Symbol(
                        id=sym_id,
                        stable_id=declared_arity_stable_id(
                            kind="macro", lang="erlang", path=file_path,
                            name=macro_name, qualified_name=macro_name,
                            arity=declared_arity(macro_meta),
                        ),
                        name=macro_name,
                        kind="macro",
                        language="erlang",
                        path=file_path,
                        span=span,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta=macro_meta,
                    ))

        # Behaviour callback declaration (WI-hidit)
        elif node.type == "callback":
            _flush_pending_func()
            callback = _callback_symbol(
                node, source, file_path, run_id, optional_callbacks,
            )
            if callback is not None:
                symbols.append(callback)

        # Type alias
        elif node.type == "type_alias":
            _flush_pending_func()
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

    # Flush any remaining pending function at end of file
    _flush_pending_func()

    return symbols, module_name


#: OTP's eight logger levels, as declared in ``kernel/include/logger.hrl``,
#: mapped to the ``logger:`` function each expands to (INV-zihor).
#:
#: EIGHT NAMES, NOT A ``?LOG_`` PREFIX. rabbitmq also defines ``?LOG_DIR`` (a
#: directory string), ``?LOG_PREFIX`` (a prefix string) and ``?LOG_EXCH_NAME``
#: (a binary); a prefix match would mint logging sinks out of string literals.
#:
#: Widening this set is the unsafe direction, so a ninth entry needs the same
#: evidence these eight have: measured across rabbitmq, emqx, ejabberd,
#: vernemq, rebar3 and cowboy, none of these names is EVER redefined, while
#: general function-like macro names collide in more than one file at 5-21%
#: per repository. That asymmetry is the whole argument for handling these by
#: name and refusing to generalise into a preprocessor on the cheap.
OTP_LOGGER_LEVEL_MACROS: Final[dict[str, str]] = {
    "LOG_EMERGENCY": "emergency",
    "LOG_ALERT": "alert",
    "LOG_CRITICAL": "critical",
    "LOG_ERROR": "error",
    "LOG_WARNING": "warning",
    "LOG_NOTICE": "notice",
    "LOG_INFO": "info",
    "LOG_DEBUG": "debug",
}

#: A file only receives OTP's macros by including a header named
#: ``logger.hrl``. Gating on the INCLUDE rather than on the name is what makes
#: this sound instead of merely probable, and it is measured: across rabbitmq,
#: emqx, ejabberd and vernemq this covers 2267 of 2267 OTP macro sites, where
#: the stricter ``include_lib("kernel/include/logger.hrl")`` covers 2266 --
#: ejabberd reaches kernel's header through its OWN same-named one, which is
#: one level of indirection and exactly the case a literal match misses.
_LOGGER_HRL_INCLUDE = re.compile(
    r'-include(?:_lib)?\(\s*"[^"]*logger\.hrl"\s*\)'
)


def _arity_of(node: "tree_sitter.Node") -> int:
    """Count the arguments of a function clause or a call.

    Both a ``function_clause`` and a local ``call`` carry their arguments in
    an ``expr_args`` child, so one counter serves the declaration and the
    call site -- which is what lets a call be matched to a declaration by
    name AND arity, the way Erlang itself does.
    """
    args = find_child_by_type(node, "expr_args")
    if args is None:  # pragma: no cover - defensive for malformed AST
        # The grammar gives every function_clause and local call an
        # expr_args, even ``f()``; none was found across ejabberd and rebar3
        # or in truncated input. Same guard as _extract_erlang_signature.
        return 0
    return sum(1 for c in args.children if c.type not in ("(", ")", ","))


def _get_enclosing_function_erlang(
    node: "tree_sitter.Node",
    source: bytes,
    functions: dict[str, list[Symbol]],
) -> Symbol | None:
    """Find the function whose declaration encloses ``node``.

    The lookup key is ``name/arity``, the symbol's own name, read off the
    enclosing ``fun_decl``. INV-mozas: it used to be the bare atom, which
    every arity registers under (``base_name``), so a call inside
    ``get_timeout/0`` was anchored to whichever arity registered last --
    a source attributed to a function that does not contain it. Erlang
    rejects two same-name/arity functions in one module, and consecutive
    clauses coalesce into one symbol under that name, so a call in any clause
    finds the coalesced function. The key is NOT unique per file, though:
    ``-ifdef``/``-else`` defines the same name/arity once per branch, and
    tree-sitter parses unpreprocessed source, so both are symbols. Of the
    candidates, the one whose span contains the declaration is the answer
    (ejabberd_app.erl: 21 calls anchored to the ``-else`` stub otherwise).
    """
    current = node.parent
    while current is not None:
        if current.type == "fun_decl":
            clause = find_child_by_type(current, "function_clause")
            if clause:
                atom = find_child_by_type(clause, "atom")
                if atom:
                    full_name = f"{node_text(atom, source)}/{_arity_of(clause)}"
                    line = current.start_point[0] + 1
                    return next(
                        (s for s in functions.get(full_name, ())
                         if s.span is not None
                         and s.span.start_line <= line <= s.span.end_line),
                        None,
                    )
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
    module_symbols: dict[str, list[Symbol]] | None = None,
    callbacks_by_module: dict[str, list[Symbol]] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Erlang file.

    Detects:
    - Function calls (call nodes with remote or local)
    - Behaviour implementation (-behaviour), see
      ``_extract_behaviour_callback_edges``
    - Import statements (-import)

    A ``-callback`` symbol is left out of every lookup here: it has no body,
    so it neither encloses a call nor answers one (WI-hidit).
    """
    edges: list[Edge] = []
    callables = [s for s in file_symbols if not _is_callback(s)]
    file_id = make_file_id("erlang", file_path)

    # INV-zihor: whether THIS file can see OTP's ?LOG_* macros. Computed once
    # per file rather than per node -- the answer cannot vary within a file.
    logger_macros_visible = bool(_LOGGER_HRL_INCLUDE.search(source.decode(
        "utf-8", errors="replace")))

    # Every function of this file under its name/arity. A list, because
    # -ifdef/-else can define one name/arity twice (see the enclosing lookup).
    functions: dict[str, list[Symbol]] = {}
    for s in callables:
        if s.kind == "function":
            functions.setdefault(s.name, []).append(s)

    # Build local symbol map (name -> symbol)
    local_symbols = {s.name: s for s in callables}
    # Also map base_name for function lookup
    for s in callables:
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
                )
                edges.append(edge)

        elif node.type == "macro_call_expr" and logger_macros_visible:
            # INV-zihor: ?LOG_DEBUG("...", [Secret]) IS a logging call, but
            # tree-sitter parses source rather than preprocessed source, so it
            # produced no edge at all and no catalogue row could match it. On
            # rabbitmq that hid 1,910 of 2,093 logging call sites.
            #
            # EXPANDED HERE, NOT IN THE CATALOGUE, because the catalogue can
            # only match a call the analyzer emitted -- and there was none. The
            # item's own scope note says as much: no row can fix this.
            macro_var = find_child_by_type(node, "var")
            level = OTP_LOGGER_LEVEL_MACROS.get(
                node_text(macro_var, source) if macro_var else "",
            )
            caller = _get_enclosing_function_erlang(
                node, source, functions,
            )
            if level and caller:
                edges.append(Edge.create(
                    src=caller.id,
                    dst=f"erlang:logger:0-0:{level}:function",
                    edge_type="calls",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    # NOT ``ast_call``. tree-sitter expanded nothing; this edge
                    # is an inference from a macro name plus an include, and a
                    # consumer telling a seen call from an inferred one reads
                    # exactly this field.
                    #
                    # NO ``call_construct``. No call construct was parsed at the
                    # site, and "it came from a macro" is already stated by
                    # ``evidence_type`` above; a ``macro`` construct restated
                    # the pathway on a second axis (WI-dapap).
                    evidence_type="macro_expansion",
                ))

        elif node.type == "call":
            # Function call
            caller = _get_enclosing_function_erlang(node, source, functions)
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
                                    evidence_type="ast_call",
                                    confidence=confidence,
                                    meta={"call_construct": "remote"},
                                )
                                edges.append(edge)
                            else:
                                # Unresolved remote call — create edge to
                                # synthetic external node so I/O boundary
                                # tagging can match stdlib calls (file:read_file,
                                # gen_tcp:send, ets:insert, etc.).
                                ext_id = f"erlang:{mod_name}:0-0:{func_name}:function"
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=ext_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="ast_call",
                                    # INV-tadup: NOT "remote_external". The suffix encoded
                                    # whether the resolver found the callee, which is
                                    # already recoverable from ``dst`` (a synthetic
                                    # external node id vs an in-repo symbol id). The
                                    # construct is the same either way: ``mod:func(args)``.
                                    meta={"call_construct": "remote"},
                                )
                                edges.append(edge)
                else:
                    # Local call: function(args)
                    atom = find_child_by_type(node, "atom")
                    if atom:
                        func_name = node_text(atom, source)
                        # A local function is its name AND arity. The bare
                        # name is whichever arity registered last, so keying
                        # on it made get_timeout() inside get_timeout/2 a
                        # self-loop (INV-mozas's wrong-callee symptom).
                        callee = local_symbols.get(
                            f"{func_name}/{_arity_of(node)}",
                        )
                        if callee is not None:
                            confidence = 0.90
                        else:
                            # ADR-0007: Use import module as path_hint
                            path_hint = None
                            if import_aliases and func_name in import_aliases:
                                path_hint = import_aliases[func_name]
                            lookup_result = resolver.lookup(
                                func_name, path_hint=path_hint,
                            )
                            if lookup_result.found and lookup_result.symbol:
                                callee = lookup_result.symbol
                                confidence = 0.85 * lookup_result.confidence
                            else:
                                callee = None
                        if callee is not None:
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="ast_call",
                                confidence=confidence,
                                meta={"call_construct": "local"},
                            )
                            edges.append(edge)

    # Behaviour edges: OTP callback rooting (-behaviour(gen_server) →
    # dispatches_to → init/1) and the type relationship (WI-hidit).
    callback_edges = _extract_behaviour_callback_edges(
        tree, source, callables, run_id,
        module_symbols or {}, callbacks_by_module or {},
    )
    edges.extend(callback_edges)

    return edges


def _extract_behaviour_callback_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_symbols: list[Symbol],
    run_id: str,
    module_symbols: dict[str, list[Symbol]],
    callbacks_by_module: dict[str, list[Symbol]],
) -> list[Edge]:
    """Extract the edges each ``-behaviour(X)`` implies, once per X.

    1. OTP CALLBACK ROOTING. For X in ``OTP_BEHAVIOUR_CALLBACKS``
       (gen_server, supervisor, ...), the OTP framework invokes specific
       callback functions (init, handle_call, ...), so the module
       ``dispatches_to`` each one it defines. Without these edges the
       callbacks are disconnected orphans in the graph.
    2. THE TYPE RELATIONSHIP (WI-hidit, the Erlang half of WI-vitas):
       ``implements`` from the module to X -- every in-repo module named X,
       else ``erlang:external:0-0:X:unresolved`` -- and ``dispatches_to``
       from each of X's ``-callback`` symbols to this module's function of
       the SAME ``name/arity``. Erlang names a function by name and arity,
       so the match is exact: ``area/1`` does not implement ``area/0``.

       OTP behaviours get the ``implements`` edge too, to their external
       target. ``-behaviour(gen_server)`` IS a type relationship to a library
       module, and stating it is what lets ``owner_ancestry`` read
       ``gen_server`` as the module's library ancestor -- the same edge
       Elixir emits for ``use GenServer``. The OTP table roots the callbacks
       because OTP's ``-callback`` declarations are not in the tree.

       Every in-repo module of that name, not one: Erlang module names are
       global, so a repository that defines X twice (a test double, a
       per-platform copy) has two candidate behaviours and the build picks
       one. A module naming itself is skipped, as in Elixir.

    A file with no ``-module`` (a ``.hrl``) has no module to attribute the
    directive to and emits nothing.
    """
    edges: list[Edge] = []

    # Find module symbol (source of callback edges)
    module_sym = None
    for s in file_symbols:
        if s.kind == "module":
            module_sym = s
            break

    if module_sym is None:
        return edges

    # Build lookups: base_name -> functions (the OTP table names callbacks
    # without arity) and name/arity -> functions (a -callback's own name).
    # A list each, because -ifdef/-else can define one name/arity twice.
    func_by_base_name: dict[str, list[Symbol]] = {}
    func_by_name: dict[str, list[Symbol]] = {}
    for s in file_symbols:
        if s.kind == "function" and s.meta:
            func_by_name.setdefault(s.name, []).append(s)
            base = s.meta.get("base_name")
            if base:
                func_by_base_name.setdefault(base, []).append(s)

    seen: set[str] = set()
    for node in iter_tree(tree.root_node):
        if node.type != "behaviour_attribute":
            continue

        atom = find_child_by_type(node, "atom")
        if not atom:  # pragma: no cover - behaviour_attribute always has atom
            continue

        behaviour_name = node_text(atom, source)
        if behaviour_name in seen:
            continue
        seen.add(behaviour_name)

        for callback_name in OTP_BEHAVIOUR_CALLBACKS.get(behaviour_name, []):
            for func_sym in func_by_base_name.get(callback_name, []):
                edges.append(Edge.create(
                    src=module_sym.id,
                    dst=func_sym.id,
                    edge_type="dispatches_to",
                    line=func_sym.span.start_line if func_sym.span else 0,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="behaviour_callback",
                    meta={"mechanism": "callback"},
                ))

        if behaviour_name == module_sym.name:
            continue
        line = node.start_point[0] + 1
        targets = module_symbols.get(behaviour_name, [])
        target_ids: list[Optional[str]] = [t.id for t in targets] or [None]
        for target_id in target_ids:
            edges.append(behaviour_implements_edge(
                src=module_sym.id, lang="erlang", behaviour=behaviour_name,
                target_id=target_id, line=line, origin=PASS_ID, run_id=run_id,
            ))
        for callback in callbacks_by_module.get(behaviour_name, []):
            for impl in func_by_name.get(callback.name, []):
                edges.append(callback_dispatch_edge(
                    callback_id=callback.id, impl_id=impl.id, line=line,
                    origin=PASS_ID, run_id=run_id,
                ))

    return edges


class ErlangAnalyzer(TreeSitterAnalyzer):
    """Erlang language analyzer using tree-sitter-language-pack.

    Handles Erlang-specific registration: functions are indexed by full name
    (name/arity), base name, and module-prefixed name for remote call resolution.
    """

    lang = "erlang"
    file_patterns: ClassVar[list[str]] = ["*.erl", "*.hrl"]
    language_pack_name = "erlang"
    create_file_symbols = True

    # Track module names per file for cross-file resolution
    _module_registry: dict[str, str]  # module_name -> file_path
    _file_module_names: dict[str, str]  # rel_path -> module_name
    # WI-hidit: behaviour resolution. Filled by ``register_symbol`` (run over
    # every file before Pass 2), read by Pass 2's behaviour edges.
    _module_symbols: dict[str, list[Symbol]]  # module_name -> module symbols
    _callbacks_by_module: dict[str, list[Symbol]]  # module_name -> callbacks

    def __init__(self) -> None:
        self._module_registry = {}
        self._file_module_names = {}
        self._module_symbols = {}
        self._callbacks_by_module = {}

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

        # Build symbol_by_name with base_name mapping for local call resolution.
        # A -callback is not callable, so it is not a name a call resolves to.
        for sym in file_symbols:
            if _is_callback(sym):
                continue
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
        global_symbols: dict[str, Symbol],
    ) -> None:
        """Register symbol with Erlang-specific indexing.

        Functions are indexed by full name (name/arity), base name, and
        module-prefixed base name for remote call resolution.

        A ``-callback`` (WI-hidit) is filed under its module in
        ``_callbacks_by_module`` and NOWHERE in ``global_symbols``: under any
        of those keys ``shape:area()`` -- an undef error in Erlang -- or a bare
        ``area()`` would resolve to a function with no body. Modules are also
        indexed by name for ``-behaviour`` resolution; ``global_symbols``
        cannot serve, because a function whose base name equals a module's
        name overwrites the module there.
        """
        module_name = self._file_module_names.get(symbol.path, "")
        if _is_callback(symbol):
            if module_name:
                self._callbacks_by_module.setdefault(module_name, []).append(symbol)
            return
        if symbol.kind == "module":
            self._module_symbols.setdefault(symbol.name, []).append(symbol)
        global_symbols[symbol.name] = symbol
        if symbol.kind == "function" and symbol.meta:
            base_name = symbol.meta.get("base_name", "")
            if base_name:
                global_symbols[base_name] = symbol
                if module_name:
                    global_symbols[f"{module_name}:{base_name}"] = symbol

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict[str, Symbol],
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract call and import edges from an Erlang file."""
        # Rebuild local symbols with base_name mapping needed by edge extractor
        # The full list, not the name-keyed dict: -ifdef/-else defines one
        # name/arity twice and the dict keeps only the last (INV-mozas).
        file_symbols = self.file_symbols(local_symbols)
        return _extract_edges_from_file(
            tree, source, rel_path, file_symbols,
            resolver, self._module_registry, run.execution_id,
            import_aliases=import_aliases,
            module_symbols=self._module_symbols,
            callbacks_by_module=self._callbacks_by_module,
        )

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Reset per-run state and delegate to base class."""
        self._module_registry = {}
        self._file_module_names = {}
        self._module_symbols = {}
        self._callbacks_by_module = {}
        return super().analyze(repo_root, max_files)


_analyzer = ErlangAnalyzer()


def is_erlang_tree_sitter_available() -> bool:
    """Check if tree-sitter with Erlang grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("erlang")
def analyze_erlang(repo_root: Path) -> AnalysisResult:
    """Analyze Erlang files in a repository."""
    return _analyzer.analyze(repo_root)
