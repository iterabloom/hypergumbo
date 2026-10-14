# SPDX-License-Identifier: AGPL-3.0-or-later
"""Elixir analysis pass using the tree-sitter-language-pack Elixir grammar.

This analyzer uses tree-sitter to parse Elixir files and extract:
- Module declarations (defmodule)
- Function declarations (def/defp) and macro declarations
  (defmacro/defmacrop): ONE symbol per function, i.e. per module, name and
  arity, with every clause of it folded in (``_function_symbol``, WI-tusad;
  the Erlang shape). The name is ``M.f`` without the arity, which sits in
  ``meta["parameters"]``; the stable_id is the ADR-0035 producer formula over
  the module scope chain and that arity
  (``overload_arity.declared_arity_stable_id``), so adding or deleting a
  clause, or a function of another arity, moves no other id
- ``defstruct`` fields as ``field`` symbols owned by the enclosing module
  (``_extract_defstruct_field_names``), with a producer stable_id over that
  module's scope chain; they are data anchors and never enter call
  resolution
- Phoenix route-marker symbols (``_extract_phoenix_routes``), stashed per
  file and appended in ``post_process``. Only a route macro written in a
  router module mints one -- a ``defmodule`` whose body runs ``use
  Phoenix.Router`` or the web-module dispatcher ``use AppWeb, :router``
  (any route macro), or ``use Plug.Router`` (the HTTP verbs) -- and never
  the right side of ``|>``: elsewhere ``get`` / ``put`` / ``live`` are
  plain calls (a cache's ``get("user_1")``, a ConnTest ``conn |>
  get("/pages")``). ``resources`` expands to the routes
  ``mix phx.routes`` lists: eight by default (``:update`` on both PATCH and
  PUT), filtered by ``only:``/``except:``, reshaped by ``singleton: true``
  and ``param:``. Every route's controller is lexically alias-expanded and
  then prefixed with the enclosing ``scope`` aliases, and every path is
  joined onto the enclosing ``scope`` / nested-``resources`` paths, as
  Phoenix does
- Function call relationships, in BOTH module-qualified receiver syntaxes:
  an ``alias`` receiver for an Elixir module (``Logger.error``) and an
  ``atom`` receiver for an Erlang/OTP one (``:ets.insert``). The atom form
  is not a stylistic variant — it is the ONLY way Elixir can call OTP at
  all, so ``:ets``, ``:file``, ``:gen_tcp``, ``:crypto`` and ``:httpc``
  are reachable through no other spelling (WI-kigub). Its module slot is
  emitted WITHOUT the leading colon, matching both the catalogue's rows
  and what the erlang analyzer emits for the same OTP modules. Four other
  receiver shapes the grammar puts in the same position are deliberately
  refused and pinned as such: a variable (``mod.fun()``), a module
  attribute (``@attr.thing()``), a quoted atom (``:"Elixir.Foo".bar()``)
  and an anonymous call (``f.()``, which has no named callee at all).
- Import relationships (use/import). ``alias`` produces NO edge — it is
  collected only as a hint for call disambiguation, in all four spellings:
  ``alias A.B.C``, ``alias A.B.C, as: X``, the MULTI-ALIAS BRACE FORM
  ``alias A.B.{C, D}`` (whose tree is ``arguments -> dot -> [alias, ".",
  tuple]``, so it has no ``alias`` child of ``arguments`` and used to
  register nothing), and by FIRST COMPONENT — ``alias Mix.Tasks.Phx.Gen``
  makes ``Gen.Context.build(...)`` mean
  ``Mix.Tasks.Phx.Gen.Context.build/2``. The last two became load-bearing
  when qualified calls stopped resolving by bare name (WI-kafor): a
  qualified call is resolved by EXACT name only, so an alias that is not
  read is a call that is not resolved.
- A BARE call to a stdlib-named function takes the module its own file's
  ``import`` directives establish, and NOTHING ELSE (WI-jozap). Three ordered
  rules: an ``import M, only: [f: n]`` list that names the function wins; a
  Kernel auto-import (``is_atom``, ``inspect``, ``raise``) gets no module-keyed
  edge, because it is available with no directive at all and is therefore not
  the imported module's function; an importable name (``IO.puts``,
  ``Enum.map``, ``String.split``, ``Logger.error``) takes its owning module
  only when that module is imported. Anything else is DECLINED. This replaced
  ``next(iter(imported_modules))``, which took an arbitrary set member and,
  because Python randomises string hashing per process, made the analyzer
  non-deterministic across runs.
- OTP/Phoenix/WebSocket behaviour callback edges (use GenServer, @behaviour Plug,
  use AppWeb, :live_view, etc.): the module ``dispatches_to`` each framework
  callback it defines, looked up by qualified name only
- Behaviours as a TYPE relationship (WI-vitas). A behaviour is Elixir's
  interface mechanism, and three facts model it:

  * ``@callback name(args) :: ret`` is a symbol on its module: kind
    ``function`` (``macro`` for ``@macrocallback``) with
    ``modifiers=["abstract"]`` -- the same marking cpp and kotlin give an
    abstract member. Its ``signature`` is the declaration surface after the
    name, verbatim (``() :: integer``). It is a DECLARATION, never a call
    target: ``register_symbol`` keeps it out of the call-resolution indexes
    and files it under ``global_symbols["__callbacks__"][module]`` instead,
    because a behaviour does not define its callbacks (``Shape.area()`` is an
    undefined-function error). The ``_inside_typespec`` rule is untouched:
    the typespec BODY still emits no call edge (WI-dokib).
  * ``@behaviour X`` emits ``implements`` from the module to ``X`` (alias
    expanded): the in-repo module when one is defined, else the unresolved
    target ``elixir:external:0-0:X:unresolved`` -- the core inheritance
    linker's external sentinel, with the full module name because an Elixir
    module's dotted name IS its identity. ``use X`` implies a behaviour only
    where X's ``__using__`` is known to set one: for a library X,
    ``USE_IMPLIED_BEHAVIOURS`` (read from each library's source; ``use
    Agent`` sets none); for an in-repo X, the ``@behaviour`` directives in
    the ``quote`` of its ``defmacro __using__`` (``_extract_using_index``,
    gathered in Pass 1 and followed through nested ``use``) -- and, for the
    Phoenix web-module dispatcher (``defmacro __using__(which)`` doing
    ``apply(__MODULE__, which, [])``), ``use AppWeb, :live_view`` reads the
    quote of ``AppWeb``'s public ``def live_view`` the same way (WI-nunar).
    What a ``use`` adopts feeds both this relationship and the framework
    callback table above, so a LiveView written as ``use AppWeb,
    :live_view`` has its ``mount`` / ``render`` rooted. ``@behaviour
    :gen_cycle`` names an ERLANG module by its atom (WI-hidit): X is the atom
    without its colon, never alias-expanded, its target is always the
    unresolved one (this analyzer sees no Erlang symbols; the core BEAM
    behaviour bridge linker resolves it to an in-repo Erlang module), and an
    OTP X roots its callbacks from the Erlang analyzer's
    ``OTP_BEHAVIOUR_CALLBACKS`` (``@behaviour :gen_statem``).
  * each callback ``dispatches_to`` the same-named, same-kind public function
    in each implementing module whose parameters admit the callback's arity (``meta["arity"]``, ``overload_arity.narrow_by_arity``:
    ``@callback f(term)`` is not implemented by an ``f/2`` beside ``f/1``;
    INV-dital), evidence ``behaviour_callback`` -- the
    abstract-member -> implementation shape ``linkers/type_hierarchy`` builds
    for languages with methods. That linker cannot build it here: it indexes
    ``kind == "method"`` only, and an Elixir function is not a method.

  A directive inside ``quote`` belongs to the module that runs ``use`` and
  emits nothing here (the INV-sinah rule), and so does a ``@callback`` there;
  this holds for the callback table too, so the web module is never rooted
  as a LiveView by its own ``def live_view`` quote.
- Protocols (WI-forof). A protocol IS a behaviour -- ``defprotocol`` generates
  a ``@callback`` per function head and ``defimpl`` sets ``@behaviour P`` on
  its module -- so it is modelled with the behaviour machinery above:

  * IDENTITY. ``defprotocol P`` is a module ``P``, nested like ``defmodule``;
    each ``def`` head in it is a callback-shaped symbol (kind ``function``,
    ``modifiers=["abstract"]``, ``meta["arity"]``, ``callback_stable_id``).
    ``defimpl P, for: T`` is the module ``P.T`` -- an ABSOLUTE name, never
    prefixed by an enclosing module -- and owns its defs (``P.T.size``). ``for:
    [A, B]`` is one impl module per target, each owning its own copy of the
    body's defs; no ``for:`` (inside ``defmodule T``) and ``for: __MODULE__``
    take the enclosing module. P and T are alias-expanded against the file's
    ``alias`` directives and the automatic alias a nested ``defmodule`` sets
    (``_scope_aliases``). An impl whose name cannot be read (``for:
    unquote(m)``, a ``quote`` template) owns nothing: no symbol beats a bare or
    mis-owned one. All three definers share ``_enclosing_module_names``, so a
    call in an impl's body outside any ``def`` is anchored on the impl module.
  * EDGES (``_extract_protocol_edges``). ``implements`` ``P.T -> P`` (the
    in-repo protocol, else ``elixir:external:0-0:P:unresolved``) and protocol
    function ``dispatches_to`` impl function by name and arity -- both from
    ``_behaviour_edges``. A protocol function, unlike a behaviour callback,
    IS a call target, by qualified name only: ``Size.size(x)`` is the dispatch.
  * ROOTS. An impl of a protocol the repo does not define (``String.Chars``,
    ``Enumerable``, ``Jason.Encoder``, ``Phoenix.Param``) is called by code
    outside the tree, so its module ``dispatches_to`` each of its public
    functions, as the framework table roots a GenServer's callbacks.
  * NOT MODELLED. ``@derive [Jason.Encoder]`` generates its impl module at
    compile time from the protocol's ``Any`` impl or ``__deriving__``; there is
    no source to own, and the deriving struct gets no ``implements`` edge.
    ``@fallback_to_any true`` needs nothing: a ``P.Any`` impl is an impl like
    any other and already receives the protocol function's dispatch. Calls
    inside a multi-target impl anchor on one target's copy of the enclosing
    def (position lookup keeps the last).

If tree-sitter with Elixir support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect calls, resolve against global registry, extract Phoenix
   routes and OTP behaviour callback edges. A call in no ``def`` (module
   body, ``test`` / ``setup`` blocks) is anchored on its ``defmodule`` /
   ``defprotocol`` / ``defimpl``, else
   the file (``_caller_outside_def``); call-shaped nodes inside ``@spec`` /
   ``@type`` / ``@callback`` are types and emit nothing (``_inside_typespec``).
   A ``def`` / ``defmacro`` / ``defstruct`` inside ``quote`` is a template
   for the module that runs ``use`` and emits no symbol (INV-sinah).
   A bare call reaches the function its ARITY names (WI-rodiz): Elixir
   identifies a function by name and arity, and a function symbol's name
   carries no arity, so each records its parameters in ``meta["parameters"]``
   (a ``\\\\`` default marked, from whichever clause or bodiless head declares
   it) and the call's argument count -- one more for a ``|>`` left side, one
   more for a ``do`` block a keyword list does not absorb -- selects among the
   same-named functions (``overload_arity.narrow_by_arity``). A call no local
   function can take falls through to the import, Kernel and cross-file
   resolutions; ``unquote_splicing`` makes the count unknown and excludes
   nothing. A call in any clause is anchored on the function that clause
   folded into (``_clause_positions``).

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Elixir-specific
extraction logic.

``analyze_elixir`` also attaches the repo's Mix ``DependencyManifest`` to
every non-skipped result (``mix_deps.parse_mix_dependencies``: ``mix.exs``
deps are direct, ``mix.lock`` deps transitive; None when the repo has no Mix
project), so Elixir boundary nodes get a ``directness`` stamp.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Uses tree-sitter-language-pack for grammar (elixir)
- Two-pass allows cross-file call resolution
- Multi-arity lookup via the global_symbols ``__multi__`` index (every
  function of a short or qualified name, each arity)
- Phoenix routes and OTP callbacks integrated into the two-pass pipeline
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, NamedTuple, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    Edge, ExternalRef, Span, Symbol, UsageContext, make_pass_id,
)
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    file_anchor_symbol,
    make_file_id,
    make_route_symbol,
    make_symbol_id,
    node_text,
    symbol_declared_by,
    SymbolsAt,
    symbols_at as index_symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, narrow_by_arity,
    parameter_entry,
)
from hypergumbo_core.behaviours import (
    CALLBACK_MODIFIER,
    behaviour_implements_edge,
    callback_dispatch_edge,
    callback_stable_id,
)
from hypergumbo_lang_common.erlang import OTP_BEHAVIOUR_CALLBACKS

# Phoenix HTTP method macros for route detection
PHOENIX_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# OTP/Phoenix behaviour callback mappings.
# When a module does `use GenServer`, these callbacks may be implemented.
# The framework invokes them; without edges they appear as orphans.
BEHAVIOUR_CALLBACKS: dict[str, list[str]] = {
    "GenServer": [
        "init", "handle_call", "handle_cast", "handle_info",
        "terminate", "code_change", "handle_continue",
    ],
    "Supervisor": ["init"],
    "Agent": ["init"],
    "Task": ["run"],
    "Phoenix.LiveView": [
        "mount", "handle_event", "render", "handle_params",
        "handle_async", "handle_info", "terminate",
    ],
    "Phoenix.LiveComponent": ["mount", "update", "render", "handle_event"],
    "Phoenix.Component": ["render"],
    "Plug": ["init", "call"],
    "Plug.Builder": ["init", "call"],
    "Plug.Router": ["init", "call"],
    "WebSock": ["init", "handle_in", "handle_control", "terminate"],
    "WebSockex": [
        "handle_connect", "handle_frame", "handle_cast",
        "handle_info", "handle_ping", "handle_pong",
        "handle_disconnect", "terminate",
    ],
}

#: ``use X`` -> the behaviour X's ``__using__`` declares on the using module.
#: Read from each library's source (main branches, 2026-10-01), because the
#: callback table above is NOT this map: ``use Plug.Builder`` and
#: ``use Plug.Router`` set ``@behaviour Plug``, not a behaviour of their own,
#: and ``Agent``, ``Task`` and ``Phoenix.Component`` set none (their
#: ``__using__`` defines ``child_spec`` / imports only). ``WebSock`` has no
#: ``__using__`` at all; it is reached by ``@behaviour WebSock``.
USE_IMPLIED_BEHAVIOURS: dict[str, str] = {
    "GenServer": "GenServer",
    "Supervisor": "Supervisor",
    "Phoenix.LiveView": "Phoenix.LiveView",
    "Phoenix.LiveComponent": "Phoenix.LiveComponent",
    "Plug.Builder": "Plug",
    "Plug.Router": "Plug",
    "WebSockex": "WebSockex",
    # ``use Ecto.Repo`` / ``use HTTPoison.Base``: each ``__using__`` quote opens
    # with ``@behaviour <itself>`` (ecto lib/ecto/repo.ex, httpoison
    # lib/httpoison/base.ex) and injects the library's functions into the using
    # module, which is how ``App.Repo.all`` reaches the ``Ecto.Repo`` catalogue
    # row (``hypergumbo_core.owner_ancestry``, WI-gabuv).
    "Ecto.Repo": "Ecto.Repo",
    "HTTPoison.Base": "HTTPoison.Base",
}

#: Module attributes that declare a behaviour's required member, and the
#: symbol kind of the member each one requires (``def`` vs ``defmacro``).
_CALLBACK_ATTRIBUTES: dict[str, str] = {
    "callback": "function",
    "macrocallback": "macro",
}

#: The modifier that marks a callback declaration -- the marking cpp and
#: kotlin give an abstract member, shared with the Erlang analyzer's
#: ``-callback`` (``hypergumbo_core.behaviours``). Nothing else in this
#: analyzer emits it.
_CALLBACK_MODIFIER = CALLBACK_MODIFIER

#: ``global_symbols`` key of the callback index: behaviour module name ->
#: its callback symbols. Mirrors ``__multi__``; callbacks live here INSTEAD of
#: the call-resolution indexes (see ``ElixirAnalyzer.register_symbol``).
_CALLBACKS_KEY = "__callbacks__"

#: ``Symbol.meta`` key of a callback declaration's arity (a ``@callback`` or a
#: protocol function head) -- the key the Erlang analyzer writes on its
#: ``-callback`` symbols and the BEAM behaviour bridge linker reads.
_ARITY_KEY = "arity"

# Elixir Kernel and stdlib functions that are auto-imported into every
# module.  Bare calls to these names (e.g., ``inspect(data)``) should
# NOT resolve to project-defined functions with the same name in other
# modules — they are almost always Kernel/stdlib calls.
#
# Local definitions shadow Kernel, so local_symbols_multi matches are
# still allowed.  Only the cross-file (global_multi / resolver)
# fallback is gated.
#: Kernel and Kernel.SpecialForms names: available bare with NO ``import``
#: directive at all. WI-jozap: membership here is precisely the statement "this
#: is NOT the imported module's function", so a bare call to one of these must
#: never be attributed to a module the file happens to import.
_ELIXIR_KERNEL_AUTO_IMPORTS: frozenset[str] = frozenset({
    # Kernel — auto-imported, always available bare
    "inspect", "to_string", "to_charlist", "is_atom", "is_binary",
    "is_bitstring", "is_boolean", "is_float", "is_function",
    "is_integer", "is_list", "is_map", "is_map_key", "is_nil",
    "is_number", "is_pid", "is_port", "is_reference", "is_tuple",
    "is_struct", "is_exception", "abs", "ceil", "floor", "round",
    "trunc", "div", "rem", "max", "min", "hd", "tl", "length",
    "elem", "put_elem", "tuple_size", "map_size", "bit_size",
    "byte_size", "node", "self", "send", "spawn", "spawn_link",
    "spawn_monitor", "exit", "throw", "raise", "reraise",
    "apply", "function_exported?", "macro_exported?",
    "struct", "struct!", "update_in", "put_in", "get_in",
    "get_and_update_in", "pop_in", "match?", "dbg",
    # Kernel.SpecialForms — technically macros but called bare
    "import", "require", "alias", "use",
})

#: Bare-callable names that DO come from an imported module, mapped to the
#: module that exports them. The ownership was already written down as prose
#: comments beside these names ("# IO — commonly called bare via import") and
#: never used as data; WI-jozap is what that cost. Keyed by name because the
#: name is what a bare call site gives us, and a dict lookup is deterministic
#: where ``next(iter(<set>))`` is not.
_ELIXIR_IMPORTABLE_BARE_FUNCTIONS: dict[str, str] = {
    # IO
    "puts": "IO", "write": "IO", "gets": "IO",
    # Enum
    "map": "Enum", "filter": "Enum", "reduce": "Enum", "each": "Enum",
    "sort": "Enum", "flat_map": "Enum", "find": "Enum", "reject": "Enum",
    "any?": "Enum", "all?": "Enum", "count": "Enum", "zip": "Enum",
    "uniq": "Enum", "chunk_every": "Enum", "group_by": "Enum", "into": "Enum",
    # String
    "split": "String", "join": "String", "trim": "String",
    "replace": "String", "starts_with?": "String", "ends_with?": "String",
    "contains?": "String", "downcase": "String", "upcase": "String",
    # Logger
    "debug": "Logger", "info": "Logger", "warn": "Logger", "error": "Logger",
}

#: The union the rest of this module consults to decide "is this a bare call a
#: project function could own?". Unchanged in membership; only split above.
_ELIXIR_STDLIB_FUNCTIONS: frozenset[str] = (
    _ELIXIR_KERNEL_AUTO_IMPORTS | frozenset(_ELIXIR_IMPORTABLE_BARE_FUNCTIONS)
)

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("elixir")


def find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])




def _symbol_module(name: str) -> str:
    """Extract the module prefix from a qualified Elixir symbol name.

    ``MyApp.Sites.create`` → ``MyApp.Sites``
    ``create`` → ``""``  (bare name, no module)
    """
    dot_pos = name.rfind(".")
    return name[:dot_pos] if dot_pos >= 0 else ""


def _extract_alias_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract alias and import directives for disambiguation.

    In Elixir:
        alias MyApp.Services.UserService -> UserService maps to MyApp.Services.UserService
        alias MyApp.Services.UserService, as: Svc -> Svc maps to MyApp.Services.UserService
        import MyApp.Math -> all functions from MyApp.Math are available

    Returns a dict mapping short names to full module paths.
    """
    return _alias_hints_under(tree.root_node, source)


def _alias_hints_under(root: "tree_sitter.Node", source: bytes) -> dict[str, str]:
    """:func:`_extract_alias_hints` from a root node (no tree object needed)."""
    hints: dict[str, str] = {}

    for node in iter_tree(root):
        if node.type != "call":
            continue

        target = find_child_by_type(node, "identifier")
        if not target:  # pragma: no cover - call nodes always have identifier
            continue

        target_name = node_text(target, source)

        if target_name == "alias":
            args = find_child_by_type(node, "arguments")
            if not args:  # pragma: no cover - alias always has arguments
                continue

            # Find the module being aliased
            module_node = None
            for child in args.children:
                if child.type == "alias":
                    module_node = child
                    break

            if module_node is None:
                # WI-kafor: the MULTI-ALIAS BRACE FORM, `alias Phx.New.{Project,
                # Generator}`, which is ordinary Elixir and produces a different
                # tree: `arguments -> dot -> [alias "Phx.New", ".", tuple]`, so
                # there is no `alias` child of `arguments` to find and the
                # directive used to register NO hint at all.
                #
                # It only became load-bearing when the module-ownership gate
                # below stopped being a substring test. `"Project."` happened to
                # be a substring of `Phx.New.Project.ecto?`, so the old gate let
                # these calls through and the resolver bound them correctly BY
                # ACCIDENT. An exact gate asks whether the project defines
                # `Project`, which it does not -- it defines `Phx.New.Project` --
                # so without this, 44 true first-party edges on phoenix-framework
                # were lost. The gate is right; it needs the aliases to be read.
                for child in args.children:
                    if child.type != "dot":
                        continue
                    prefix_node = find_child_by_type(child, "alias")
                    tuple_node = find_child_by_type(child, "tuple")
                    if prefix_node is None or tuple_node is None:
                        continue
                    prefix = node_text(prefix_node, source)
                    for member in tuple_node.children:
                        if member.type != "alias":
                            continue
                        member_name = node_text(member, source)
                        hints[member_name.rsplit(".", 1)[-1]] = (
                            f"{prefix}.{member_name}"
                        )

            if module_node:
                full_path = node_text(module_node, source)
                # Check for 'as:' option in keywords
                kw_node = find_child_by_type(args, "keywords")
                if kw_node:
                    # Look for as: Alias pattern
                    for pair in kw_node.children:
                        if pair.type == "pair":
                            key_node = find_child_by_type(pair, "keyword")
                            if key_node and node_text(key_node, source).strip().rstrip(":") == "as":
                                value_node = find_child_by_type(pair, "alias")
                                if value_node:
                                    alias_name = node_text(value_node, source)
                                    hints[alias_name] = full_path
                                    break
                    else:
                        # No as: found, use last component of module path
                        short_name = full_path.rsplit(".", 1)[-1]
                        hints[short_name] = full_path
                else:
                    # No keywords, use last component of module path
                    short_name = full_path.rsplit(".", 1)[-1]
                    hints[short_name] = full_path

        elif target_name == "import":
            args = find_child_by_type(node, "arguments")
            if args:
                for child in args.children:
                    if child.type == "alias":
                        full_path = node_text(child, source)
                        # For imports, use the full module name as hint
                        short_name = full_path.rsplit(".", 1)[-1]
                        hints[short_name] = full_path
                        break

    return hints


def _extract_imported_modules(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> set[str]:
    """Extract the set of module names that appear in ``import`` directives.

    In Elixir, ``import MyApp.Sites`` makes all public functions from
    ``MyApp.Sites`` available as bare calls. This set is used to gate
    cross-module bare call resolution: only functions from imported modules
    should be resolved.
    """
    imported: set[str] = set()

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue
        target = find_child_by_type(node, "identifier")
        if not target:  # pragma: no cover
            continue
        if node_text(target, source) != "import":
            continue
        args = find_child_by_type(node, "arguments")
        if args:
            for child in args.children:
                if child.type == "alias":
                    imported.add(node_text(child, source))
                    break

    return imported


def _extract_import_only_bindings(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Map a bare function name to the module whose ``import ..., only:`` names it.

    This is the DIRECT evidence WI-rakul's comment describes ("the explicit
    import directive establishes module context") and which the code it guarded
    never actually read -- it took an arbitrary member of the import SET
    instead. An ``only:`` list names the functions, so the module follows from
    the NAME rather than from iteration order.

    Probing the grammar found five import shapes where the item named one::

        import M                          -> no binding (nothing enumerated)
        import M, only: [f: 1, g: 2]      -> f -> M, g -> M
        import M, only: :macros           -> no binding (not enumerable)
        import M, except: [f: 1]          -> no binding (says what is NOT there)
        (several directives in one file)  -> first in DOCUMENT order wins

    First-wins is a deliberate choice of a DETERMINISTIC rule over a
    set-arbitrary one; two directives importing the same name is a compile
    error in Elixir, so the case is not expected to arise in valid source.
    """
    bindings: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue
        target = find_child_by_type(node, "identifier")
        if target is None or node_text(target, source) != "import":
            continue
        args = find_child_by_type(node, "arguments")
        if args is None:  # pragma: no cover - `import` with no argument
            continue
        alias_node = find_child_by_type(args, "alias")
        if alias_node is None:
            continue
        module = node_text(alias_node, source)
        keywords = find_child_by_type(args, "keywords")
        if keywords is None:
            continue
        for pair in keywords.children:
            if pair.type != "pair":
                continue
            key = find_child_by_type(pair, "keyword")
            if key is None or node_text(key, source).rstrip(": ") != "only":
                continue
            listing = find_child_by_type(pair, "list")
            if listing is None:
                continue          # ``only: :macros`` -- not enumerable
            inner = find_child_by_type(listing, "keywords")
            if inner is None:  # pragma: no cover - ``only: []``
                continue
            for entry in inner.children:
                if entry.type != "pair":
                    continue
                entry_key = find_child_by_type(entry, "keyword")
                if entry_key is None:  # pragma: no cover - defensive
                    continue
                bindings.setdefault(
                    node_text(entry_key, source).rstrip(": ").strip(), module,
                )
    return bindings


def _bare_call_source_module(
    name: str,
    imported_modules: set[str],
    only_bindings: dict[str, str],
) -> Optional[str]:
    """Which module does a bare stdlib-named call belong to? (WI-jozap)

    Three ordered rules and no set iteration anywhere:

    1. an ``only:`` list that NAMES the function wins -- direct evidence;
    2. a Kernel auto-import belongs to Kernel, so it gets NO module-keyed
       edge. Membership of ``_ELIXIR_KERNEL_AUTO_IMPORTS`` is precisely the
       statement "this is not the imported module's function";
    3. an importable name whose OWNING stdlib module is imported takes that
       module.

    Anything else returns ``None`` and no edge is emitted. Declining is the
    withholding direction, which is the safe one: a wrong module here is a
    false edge into a catalogue, and the catalogue is what a security verdict
    is read off.
    """
    bound = only_bindings.get(name)
    if bound is not None:
        return bound
    if name in _ELIXIR_KERNEL_AUTO_IMPORTS:
        return None
    owner = _ELIXIR_IMPORTABLE_BARE_FUNCTIONS.get(name)
    if owner is not None and owner in imported_modules:
        return owner
    return None


#: The calls that define a module (WI-forof). ``defprotocol P`` IS a
#: ``defmodule`` (its name nests the same way); ``defimpl P, for: T`` defines
#: the module ``P.T``, an ABSOLUTE name: Elixir builds it with
#: ``Module.concat(P, T)`` and passes the atom to ``defmodule``, so the
#: enclosing module is not prefixed (a ``defimpl Size, for: Outer`` inside
#: ``defmodule Outer`` is ``Size.Outer``, not ``Outer.Size.Outer``).
_MODULE_DEFINERS: frozenset[str] = frozenset({"defmodule", "defprotocol", "defimpl"})


def _definer_keyword(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """``defmodule`` / ``defprotocol`` / ``defimpl`` for such a call, else None."""
    if node.type != "call":
        return None
    target = find_child_by_type(node, "identifier")
    keyword = node_text(target, source) if target is not None else None
    return keyword if keyword in _MODULE_DEFINERS else None


def _nearest_definer(
    node: "tree_sitter.Node", source: bytes,
) -> Optional["tree_sitter.Node"]:
    """The nearest ancestor call that defines a module, else None."""
    current = node.parent
    while current is not None:
        if _definer_keyword(current, source) is not None:
            return current
        current = current.parent
    return None


def _enclosing_module_names(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[list[str]]:
    """The full name(s) of the module the code at ``node`` belongs to.

    ``[]`` outside any module. Usually one name; several inside ``defimpl P,
    for: [A, B]``, whose body is compiled once per target (``P.A``, ``P.B``).
    An atom-named ``defmodule`` (``defmodule :"x"``) is walked past, as it
    always was. None inside a ``defprotocol`` / ``defimpl`` whose name cannot
    be read (``for: unquote(m)``, no ``for:`` outside any module, a ``quote``
    template): the code there has no knowable module, and a caller emitting a
    symbol must emit none rather than a bare or mis-owned name.
    """
    definer = _nearest_definer(node, source)
    while definer is not None:
        names = _defined_module_names(definer, source)
        if names:
            return names
        if _definer_keyword(definer, source) != "defmodule":
            return None
        definer = _nearest_definer(definer, source)
    return []


def _enclosing_module(node: "tree_sitter.Node", source: bytes) -> str:
    """The (first) module the code at ``node`` belongs to; ``""`` for none."""
    names = _enclosing_module_names(node, source)
    return names[0] if names else ""


def _defined_module_names(definer: "tree_sitter.Node", source: bytes) -> list[str]:
    """The full name(s) of the module(s) a definer call defines; [] if unreadable."""
    if _definer_keyword(definer, source) == "defimpl":
        impl = _impl_modules(definer, source)
        return impl[1] if impl is not None else []
    name = _get_module_name(definer, source)
    if name is None:
        return []
    outer = _enclosing_module_names(definer, source)
    if outer is None:
        return []
    return [f"{outer[0]}.{name}" if outer else name]


def _keyword_value(
    args: "tree_sitter.Node", key: str, source: bytes,
) -> Optional["tree_sitter.Node"]:
    """The value node of ``key:`` in a call's trailing keyword list, else None."""
    keywords = find_child_by_type(args, "keywords")
    if keywords is None:
        return None
    for pair in keywords.children:
        if (pair.type == "pair" and len(pair.children) >= 2
                and node_text(pair.children[0], source).strip().rstrip(":") == key):
            value: "tree_sitter.Node" = pair.children[-1]
            return value
    return None


#: One-entry cache for :func:`_file_alias_hints`, ``[source, hints]``. Module
#: names are read for every def, struct and directive of a file; the alias map
#: they consult inside a ``defimpl`` is the file's, so it is computed once per
#: source buffer rather than once per lookup. Keyed by identity: the cache
#: holds the buffer, so a different file can never share its address.
_FILE_ALIAS_CACHE: list[Any] = [None, {}]


def _file_alias_hints(node: "tree_sitter.Node", source: bytes) -> dict[str, str]:
    """The ``alias`` / ``import`` hints of the file ``node`` is in."""
    if _FILE_ALIAS_CACHE[0] is not source:
        root = node
        while root.parent is not None:
            root = root.parent
        _FILE_ALIAS_CACHE[:] = [source, _alias_hints_under(root, source)]
    hints: dict[str, str] = _FILE_ALIAS_CACHE[1]
    return hints


def _scope_aliases(node: "tree_sitter.Node", source: bytes) -> dict[str, str]:
    """The aliases visible at ``node``: the file's directives, then the
    automatic alias each NESTED module sets in its parent (``defmodule A do
    defmodule B`` makes ``B`` mean ``A.B`` inside ``A``; ``defmodule
    C.D`` nested in ``A`` makes ``C`` mean ``A.C``). Inner scopes win.

    Like the rest of this analyzer it reads a file's ``alias`` directives
    wherever they are written, not only before ``node``.
    """
    hints = dict(_file_alias_hints(node, source))
    scopes: list[tuple[str, "tree_sitter.Node"]] = []
    definer = _nearest_definer(node, source)
    while definer is not None:
        names = (
            _defined_module_names(definer, source)
            if _definer_keyword(definer, source) != "defimpl" else []
        )
        if names:
            scopes.append((names[0], definer))
        definer = _nearest_definer(definer, source)
    for module, definer in reversed(scopes):
        body = find_child_by_type(definer, "do_block")
        for child in body.named_children if body is not None else ():
            if _definer_keyword(child, source) in ("defmodule", "defprotocol"):
                written = _get_module_name(child, source)
                if written is not None:
                    head = written.split(".", 1)[0]
                    hints[head] = f"{module}.{head}"
    return hints


def _module_expression(
    node: "tree_sitter.Node",
    source: bytes,
    scope: Optional[str],
    aliases: dict[str, str],
) -> Optional[str]:
    """The module a written ``defimpl`` operand names, else None.

    An alias (expanded), ``__MODULE__`` (the enclosing module) or
    ``__MODULE__.Sub``. Anything else -- ``unquote(m)``, a variable -- is a
    module only the compiler knows.
    """
    if node.type == "alias":
        return _expand_alias(node_text(node, source), aliases)
    text = node_text(node, source)
    if node.type == "identifier" and text == "__MODULE__":
        return scope
    if node.type == "dot" and scope is not None:
        parts = node.named_children
        if (len(parts) == 2 and node_text(parts[0], source) == "__MODULE__"
                and parts[1].type == "alias"):
            return f"{scope}.{node_text(parts[1], source)}"
    return None


def _impl_modules(
    defimpl: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, list[str]]]:
    """``(protocol, impl module names)`` of a ``defimpl``, or None if unreadable.

    ``defimpl P, for: T`` is ``P.T``; ``for: [A, B]`` is one impl per target;
    no ``for:`` takes the enclosing module (Elixir's ``env.module``). P and T
    are alias-expanded (:func:`_scope_aliases`). A ``defimpl`` inside
    ``quote`` is a template for the module that runs ``use`` (INV-sinah), and
    an unreadable target is dropped rather than guessed.
    """
    if _has_quote_ancestor(defimpl, source):
        return None
    args = find_child_by_type(defimpl, "arguments")
    if args is None or not args.named_children:
        return None
    outer = _enclosing_module_names(defimpl, source)
    scope = outer[0] if outer else None
    aliases = _scope_aliases(defimpl, source)
    protocol = _module_expression(args.named_children[0], source, scope, aliases)
    if protocol is None:
        return None
    written = _keyword_value(args, "for", source)
    if written is None:
        targets: list[Optional[str]] = [scope]
    elif written.type == "list":
        targets = [
            _module_expression(t, source, scope, aliases)
            for t in written.named_children
        ]
    else:
        targets = [_module_expression(written, source, scope, aliases)]
    names = list(dict.fromkeys(f"{protocol}.{t}" for t in targets if t))
    return (protocol, names) if names else None


_DEF_KEYWORDS = ("def", "defp", "defmacro", "defmacrop")

def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    symbols_at: SymbolsAt,
) -> Optional[Symbol]:
    """Find the function whose def clause encloses ``node``.

    Keyed by the declaration's POSITION, not its name. INV-mozas: the key
    used to be the short name, but the name carries no arity, so
    ``get_timeout/0``, ``get_timeout/2`` and a same-named function in another
    module of the same file all share it. The lookup returned whichever
    registered last, anchoring 22-27% of calls on phoenix and plausible to a
    symbol that does not contain them. The enclosing ``def`` node's start
    position identifies its clause exactly, and ``symbols_at`` maps every
    clause's position to the function it folded into (``_clause_positions``).

    A def with no symbol -- one inside ``quote``, suppressed by INV-sinah
    because it is injected into the module that runs ``use`` -- is walked
    past, so the call is anchored to the macro whose text contains it
    (``__using__``), not dropped.
    """
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = find_child_by_type(current, "identifier")
            if target and node_text(target, source) in _DEF_KEYWORDS:
                sym = symbol_declared_by(current, symbols_at)
                if sym is not None:
                    return sym
        current = current.parent
    return None


#: Module attributes whose body is a TYPE, not code: a call-shaped node inside
#: one (``:inet.ip_address()``, ``:supervisor.child_spec()``) is a type
#: reference and must emit no call edge (WI-dokib).
_TYPESPEC_ATTRIBUTES: frozenset[str] = frozenset({
    "spec", "type", "typep", "opaque", "callback", "macrocallback",
})


def _module_attribute_call(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, "tree_sitter.Node"]]:
    """``@name args`` -> ``(name, the attribute's call node)``, else None.

    The one reading of the attribute shape, ``unary_operator("@", call(
    identifier, arguments))``, shared by the typespec guard, the ``@callback``
    symbol and the ``@behaviour`` directive. A bare ``@name`` (an attribute
    READ, whose operand is an ``identifier``) is not a declaration and
    returns None.
    """
    if node.type != "unary_operator" or not node.children:
        return None
    if node_text(node.children[0], source) != "@":
        return None
    operand = node.children[-1]
    if operand.type != "call":
        return None
    target = find_child_by_type(operand, "identifier")
    if target is None:  # pragma: no cover - `@` binds tighter than `.`: `@m.f(1)` is a call OF `@m`, never `@` over a nameless call
        return None
    return node_text(target, source), operand


def _inside_typespec(node: "tree_sitter.Node", source: bytes) -> bool:
    """Is ``node`` inside ``@spec`` / ``@type`` / ``@callback`` (and kin)?"""
    current = node.parent
    while current is not None:
        attribute = _module_attribute_call(current, source)
        if attribute is not None and attribute[0] in _TYPESPEC_ATTRIBUTES:
            return True
        current = current.parent
    return False


def _callback_head(
    attribute_call: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str, int]]:
    """``(name, signature, arity)`` of a ``@callback`` declaration, or None.

    The attribute's argument is the typespec ``head :: return``, optionally
    wrapped as ``head :: return when t: term``. Both wrappers are a
    ``binary_operator`` whose FIRST child leads toward the head, so the head
    is reached by descending first children. It is a ``call`` (``area()``,
    ``scale(s :: t, f :: number)``) or, for a paren-less zero-arity callback,
    a bare ``identifier`` (``simple :: :ok``).

    The signature is the declaration surface after the name, verbatim
    (ADR-0058): the parameter list (``()`` when the source wrote none) and
    the rest of the spec -- ``() :: integer``. The arity is the head's
    parameter count, which the callback's stable_id hashes (WI-hidit).
    """
    args = find_child_by_type(attribute_call, "arguments")
    if args is None or not args.named_children:
        return None
    spec = args.named_children[0]
    head = spec
    while head.type == "binary_operator" and head.children:
        head = head.children[0]
    arity = 0
    if head.type == "identifier":
        name, params = node_text(head, source), "()"
    elif head.type == "call":
        name_node = find_child_by_type(head, "identifier")
        if name_node is None:
            return None
        name = node_text(name_node, source)
        params_node = find_child_by_type(head, "arguments")
        params = node_text(params_node, source) if params_node is not None else "()"
        if params_node is not None:
            arity = sum(1 for c in params_node.named_children if c.type != "comment")
    else:
        return None
    rest = source[head.end_byte:spec.end_byte].decode("utf-8", errors="replace")
    return name, f"{params} {rest.strip()}".strip(), arity


def _callback_symbol(
    node: "tree_sitter.Node", source: bytes, file_path: str, run_id: str,
) -> Optional[Symbol]:
    """The symbol a ``@callback`` / ``@macrocallback`` declares, or None.

    Owned by the enclosing module. Not emitted inside ``quote`` (a template
    for the module that runs ``use``, INV-sinah) or where the nearest module
    is atom-named or absent (no reliable owner) -- the ``defstruct`` rules.
    Its stable_id is minted here (``callback_stable_id``): the backstop floor
    would collide with a same-named ``def`` in the behaviour module itself and
    null both (WI-hidit).
    """
    attribute = _module_attribute_call(node, source)
    if attribute is None or attribute[0] not in _CALLBACK_ATTRIBUTES:
        return None
    if (_has_quote_ancestor(node, source)
            or not _nearest_enclosing_module_resolvable(node, source)):
        return None
    head = _callback_head(attribute[1], source)
    if head is None:
        return None
    name, signature, arity = head
    kind = _CALLBACK_ATTRIBUTES[attribute[0]]
    full_name = f"{_enclosing_module(node, source)}.{name}"
    return _callback_shaped_symbol(
        node, file_path, run_id, full_name, kind, arity, signature,
    )


def _span_of(node: "tree_sitter.Node") -> Span:
    return Span(
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_col=node.start_point[1],
        end_col=node.end_point[1],
    )


def _callback_shaped_symbol(
    node: "tree_sitter.Node",
    file_path: str,
    run_id: str,
    full_name: str,
    kind: str,
    arity: int,
    signature: Optional[str],
) -> Symbol:
    """A required member with no body: a ``@callback`` or a protocol head.

    ``modifiers=["abstract"]``, ``meta["arity"]`` (the BEAM callback's arity
    key, as on an Erlang ``-callback``; ``_behaviour_edges`` matches on it) and
    a stable_id from ``callback_stable_id``.
    """
    span = _span_of(node)
    return Symbol(
        id=make_symbol_id(
            "elixir", file_path, span.start_line, span.end_line, full_name, kind,
        ),
        stable_id=callback_stable_id("elixir", file_path, full_name, arity, kind),
        name=full_name,
        kind=kind,
        language="elixir",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
        meta={_ARITY_KEY: arity},
        modifiers=[_CALLBACK_MODIFIER],
    )


def _caller_outside_def(
    node: "tree_sitter.Node",
    source: bytes,
    symbols_at: SymbolsAt,
    file_anchor: Optional[Symbol],
) -> Optional[Symbol]:
    """The anchor for a call in no ``def``: its module definer, else the file.

    WI-dokib. Module-body code (``for path <- :code.get_path()`` in mix.exs, a
    module attribute's value) and ``test`` / ``setup`` MACRO blocks are inside
    no ``def``, and every call there used to emit nothing: 101 of 225 atom call
    sites on phoenix, and the alias path lost the same sites. A typespec is the
    exception and stays silent: its call-shaped nodes are types.
    """
    if file_anchor is None or _inside_typespec(node, source):
        return None
    current = _nearest_definer(node, source)
    while current is not None:
        sym = symbol_declared_by(current, symbols_at)
        if sym is not None:
            return sym
        current = _nearest_definer(current, source)
    return file_anchor


def _is_def_head(node: "tree_sitter.Node", source: bytes) -> bool:
    """True when ``node`` is the head of a def: ``def foo(a)`` / ``... when g``.

    The grammar parses a head as a ``call`` to the function being defined,
    so without this every clause emitted a call edge to every same-named
    clause from its own first line. A guard (``when is_atom(a)``) and a
    default argument (``a \\\\ build()``) sit INSIDE the head and are real
    calls; only the head node itself is excluded.
    """
    parent = node.parent
    if parent is not None and parent.type == "binary_operator":
        if _unwrap_guard(parent) != node:
            return False
        parent = parent.parent
    if parent is None or parent.type != "arguments":
        return False
    # The only call that sits DIRECTLY in a def's arguments is its head: a
    # one-line body is inside a ``keywords`` pair, a block body in ``do_block``.
    owner = parent.parent
    target = find_child_by_type(owner, "identifier") if owner is not None else None
    return target is not None and node_text(target, source) in _DEF_KEYWORDS


def _get_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from defmodule call."""
    # defmodule has structure: (call target: (identifier "defmodule") arguments: (arguments (alias)))
    args = find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            if child.type == "alias":
                return node_text(child, source)
    return None


def _unwrap_guard(node: "tree_sitter.Node") -> "tree_sitter.Node":
    """Unwrap a guard clause's binary_operator to get the function head.

    When a ``def`` has a ``when`` guard, tree-sitter wraps the head in a
    ``binary_operator`` node: ``func(x) when guard(x)``.  This helper
    returns the left-hand child (the function call) so callers can extract
    the name and signature as if no guard were present.
    """
    if node.type == "binary_operator":
        for child in node.children:
            if child.type in ("call", "identifier"):
                return child
    return node


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from def/defp/defmacro call.

    Handles both plain heads (``def foo(a)``) and guarded heads
    (``def foo(a) when is_atom(a)``).  In the guarded case the head is
    wrapped in a ``binary_operator`` node which ``_unwrap_guard`` strips.
    """
    # def has structure: (call target: (identifier "def") arguments: (arguments (call target: (identifier "func_name") ...)))
    args = find_child_by_type(node, "arguments")
    if args:
        for child in args.children:
            unwrapped = _unwrap_guard(child)
            if unwrapped.type == "call":
                # The function name is the target of this call
                target = find_child_by_type(unwrapped, "identifier")
                if target:
                    return node_text(target, source)
            elif unwrapped.type == "identifier":
                # Simple case: def foo, do: :ok
                return node_text(unwrapped, source)
    return None


def _extract_elixir_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from def/defp/defmacro call.

    Returns signature in format: (param1, param2, keyword: default)
    Elixir is dynamically typed, so no type annotations are included.
    """
    args = find_child_by_type(node, "arguments")
    if args is None:  # pragma: no cover - defensive
        return "()"

    for child in args.children:
        unwrapped = _unwrap_guard(child)
        if unwrapped.type == "call":
            # def foo(a, b) - parameters are in the arguments of the inner call
            inner_args = find_child_by_type(unwrapped, "arguments")
            if inner_args is None:  # pragma: no cover - rare
                return "()"

            params: list[str] = []
            for param in inner_args.children:
                if param.type == "identifier":
                    # Simple positional parameter
                    params.append(node_text(param, source))
                elif param.type == "binary_operator":  # pragma: no cover - default vals
                    # Default value: param \\ default
                    left = None
                    for pc in param.children:
                        if pc.type == "identifier":
                            left = node_text(pc, source)
                            break
                    if left:
                        params.append(f"{left} \\\\ ...")
                elif param.type == "keywords":
                    # Keyword arguments
                    for kw_pair in param.children:
                        if kw_pair.type == "pair":
                            key_node = None
                            for pc in kw_pair.children:
                                if pc.type == "keyword":
                                    key_node = pc
                                    break
                            if key_node:
                                key_text = node_text(key_node, source).rstrip(":")
                                params.append(f"{key_text}: ...")

            return f"({', '.join(params)})"

        elif unwrapped.type == "identifier":
            # Simple case: def foo, do: :ok (no parameters)
            return "()"

    return "()"  # pragma: no cover - defensive


def _def_head_arguments(
    node: "tree_sitter.Node",
) -> Optional[list["tree_sitter.Node"]]:
    """The parameter nodes of a def/defp/defmacro call's head.

    ``def foo(a, b)`` -> ``[a, b]``; ``def foo, do: ..`` -> ``[]``; a guard
    (``def foo(a) when ..``) is unwrapped. None when the head is not a name
    (``def unquote(name)(arg)``) -- unknown, not empty.
    """
    args = find_child_by_type(node, "arguments")
    if args is None:  # pragma: no cover - a def call always has arguments
        return None
    for child in args.children:
        head = _unwrap_guard(child)
        if head.type == "identifier":
            return []
        if head.type == "call":
            inner = find_child_by_type(head, "arguments")
            if inner is None:  # pragma: no cover - defensive, see below
                # Only a head ``_get_function_name`` named reaches here (no
                # name, no symbol: ``def unquote(name)(arg)`` gets neither),
                # and tree-sitter-elixir gives such a call an ``arguments``
                # node; kept so a grammar change reads as unknown, not as [].
                return None
            return [c for c in inner.named_children if c.type != "comment"]
    return None  # pragma: no cover - every named def has a head


def _elixir_parameters(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[list[dict[str, Any]]]:
    """``meta["parameters"]`` of one def clause (WI-rodiz, ADR-0058).

    A parameter is a PATTERN, not a name: an identifier is its own name, and
    any other pattern is recorded by its text (whitespace collapsed, at most
    40 characters). ``b \\\\ 1`` is a defaulted parameter named ``b``.
    """
    params = _def_head_arguments(node)
    if params is None:  # pragma: no cover - only via the defensive returns above
        return None
    entries: list[dict[str, Any]] = []
    for param in params:
        default = (
            param.type == "binary_operator"
            and len(param.children) >= 3
            and param.children[1].type == "\\\\"
        )
        target = param.children[0] if default else param
        text = " ".join(node_text(target, source).split())
        if len(text) > 40:
            text = text[:37] + "..."
        entries.append(parameter_entry(text, default=default))
    return entries


def _is_pipe_rhs(node: "tree_sitter.Node", source: bytes) -> bool:
    """True when ``node`` is the right operand of ``|>``: it gets the left
    side as its first argument.

    Read by FIELD, not position: a comment line between pipe stages is a
    child of the ``binary_operator`` too, so ``children[1]`` is not always
    the operator (livebook's data.ex pipes are commented mid-chain).
    """
    parent = node.parent
    if parent is None or parent.type != "binary_operator":
        return False
    operator = parent.child_by_field_name("operator")
    right = parent.child_by_field_name("right")
    return (
        operator is not None and node_text(operator, source) == "|>"
        and right is not None and right.id == node.id
    )


def _elixir_call_arity(node: "tree_sitter.Node", source: bytes) -> Optional[int]:
    """The arity a bare call ``f(..)`` / ``f a, b`` invokes; None if unknown.

    One more for a pipe's left side. A ``do`` block is the keyword argument
    ``do:``, so it adds one -- unless the call's last argument is already a
    keyword list, which the ``do:`` joins (``f a, k: 1 do .. end`` is
    ``f(a, [k: 1, do: ..])``). ``unquote_splicing`` passes a list of
    unknown length.
    """
    args = find_child_by_type(node, "arguments")
    actual = (
        [c for c in args.named_children if c.type != "comment"]
        if args is not None else []
    )
    for arg in actual:
        if arg.type == "call":
            callee = find_child_by_type(arg, "identifier")
            if callee is not None and node_text(callee, source) == "unquote_splicing":
                return None
    count = len(actual)
    if find_child_by_type(node, "do_block") is not None and not (
        actual and actual[-1].type == "keywords"
    ):
        count += 1
    if _is_pipe_rhs(node, source):
        count += 1
    return count


#: ``Phoenix.Router.Resource``'s action set, in its own order. ``only:`` keeps
#: and ``except:`` drops members of it, so filtered output keeps this order.
_PHOENIX_RESOURCE_ACTIONS = (
    "index", "edit", "new", "show", "create", "update", "delete",
)


def _phoenix_resource_routes(
    path: str, options: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """The ``(verb, path, action)`` routes one ``resources`` call mounts.

    Mirrors ``Phoenix.Router.Resource.build`` and the router's
    ``add_resources``: the action set is filtered by ``only:`` (if present)
    else ``except:``; ``singleton: true`` removes ``:index`` from the default
    set and mounts member actions on the bare path instead of ``/:id``;
    ``param:`` renames the ``id`` segment; ``:update`` is mounted on BOTH
    ``PATCH`` and ``PUT``. An option whose value is not a literal (``only:
    @actions``) arrives here absent, so the unfiltered set is emitted -- a
    statically unreadable filter hides no route.
    """
    singleton = options.get("singleton") is True
    actions = [a for a in _PHOENIX_RESOURCE_ACTIONS
               if not (singleton and a == "index")]
    if isinstance(options.get("only"), list):
        actions = [a for a in actions if a in options["only"]]
    elif isinstance(options.get("except"), list):
        actions = [a for a in actions if a not in options["except"]]
    param = options.get("param")
    member = path if singleton else (
        f"{path.rstrip('/')}/:{param if isinstance(param, str) else 'id'}")
    by_action: dict[str, list[tuple[str, str]]] = {
        "index": [("GET", path)],
        "edit": [("GET", f"{member.rstrip('/')}/edit")],
        "new": [("GET", f"{path.rstrip('/')}/new")],
        "show": [("GET", member)],
        "create": [("POST", path)],
        "update": [("PATCH", member), ("PUT", member)],
        "delete": [("DELETE", member)],
    }
    return [(verb, route, action)
            for action in actions for verb, route in by_action[action]]


def _literal_value(node: "tree_sitter.Node", source: bytes) -> Any:
    """A router option's value when it is a literal, else ``None``.

    Reads the literals Phoenix router options are written with: a string
    (``param: "slug"``), a boolean (``singleton: true``), an atom, an alias,
    and a list of atoms (``only: [:index, :show]``). Anything else -- a
    module attribute, a variable, a function call -- cannot be evaluated
    statically and reads as ``None``.
    """
    text = node_text(node, source)
    if node.type == "string":
        content = find_child_by_type(node, "quoted_content")
        return node_text(content, source) if content is not None else ""
    if node.type == "boolean":
        return text == "true"
    if node.type == "atom":
        return text.lstrip(":")
    if node.type == "alias":
        return text
    if node.type == "list":
        items = [c for c in node.children if c.type not in ("[", "]", ",")]
        if all(c.type == "atom" for c in items):
            return [node_text(c, source).lstrip(":") for c in items]
    return None


def _keyword_options(
    node: "tree_sitter.Node", source: bytes,
) -> dict[str, Any]:
    """The ``key: value`` pairs of a trailing keyword-list argument.

    Accepts both spellings Elixir allows for a keyword argument: the bare
    ``only: [:index]`` (a ``keywords`` node) and the bracketed
    ``[only: [:index]]`` (a ``list`` wrapping one). Values go through
    :func:`_literal_value`, so a non-literal value is recorded as ``None``.
    """
    if node.type == "list":
        node = find_child_by_type(node, "keywords") or node
    options: dict[str, Any] = {}
    if node.type != "keywords":
        return options
    for pair in node.children:
        if pair.type != "pair" or len(pair.children) < 2:
            continue
        key = node_text(pair.children[0], source).strip().rstrip(":").strip()
        options[key] = _literal_value(pair.children[-1], source)
    return options


class _RouterScope(NamedTuple):
    """What the enclosing ``scope`` / ``resources`` blocks impose on a route.

    ``alias`` is the module prefix Phoenix concatenates onto a controller
    (``None`` outside any aliased scope); ``path`` is the list of path
    segments it prepends to the route's own path.
    """

    alias: str | None
    path: tuple[str, ...]


def _path_segments(path: str) -> tuple[str, ...]:
    """``String.split(path, "/", trim: true)``, as Phoenix splits a path."""
    return tuple(segment for segment in path.split("/") if segment)


def _join_route_path(prefix: tuple[str, ...], path: str) -> str:
    """Phoenix's ``join_path``: scope segments plus the route's own segments.

    ``"/" <> Enum.join(scope.path ++ String.split(path, "/", trim: true),
    "/")`` -- so ``scope "/admin"`` + ``live "/"`` is ``/admin`` and a
    trailing slash never survives (``get "/vid/"`` is ``/vid``).
    """
    return "/" + "/".join(prefix + _path_segments(path))


def _phoenix_resource_name(controller: str) -> str:
    """``Phoenix.Naming.resource_name(controller, "Controller")``.

    The module's last segment with a trailing ``Controller`` removed, then
    underscored (``Admin.UserPostController`` -> ``user_post``,
    ``HTTPClientController`` -> ``http_client``). It names the nested
    ``:<name>_id`` segment of a ``resources ... do`` block.
    """
    last = controller.rsplit(".", 1)[-1]
    if last.endswith("Controller"):
        last = last[: -len("Controller")]
    return re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", last,
    ).lower()


def _parse_route_args(
    call: "tree_sitter.Node", source: bytes,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    """``(path, controller, action, options)`` of a route macro call.

    Positional: the path string, the controller / LiveView module as
    written, the action atom (``get``/``post``/.../``live``); a trailing
    keyword list (``only:``, ``singleton:``, ``as:`` ...) at position two or
    later becomes ``options``. Missing pieces are ``None`` / ``{}``.
    """
    route_path = None
    controller = None
    action = None
    options: dict[str, Any] = {}
    args_node = find_child_by_type(call, "arguments")
    if args_node is None:  # pragma: no cover - route macros always take args
        return route_path, controller, action, options
    arg_index = 0
    for child in args_node.children:
        if child.type in ("(", ")", ","):
            continue
        if arg_index == 0:
            # First arg is the path (string)
            if child.type == "string":
                content = find_child_by_type(child, "quoted_content")
                route_path = (
                    node_text(content, source) if content is not None
                    else node_text(child, source).strip('"\''))
        elif arg_index == 1:
            # Second arg is the controller/module (alias, identifier, or
            # dotted alias like UserLive.Show)
            if child.type in ("alias", "dot"):
                controller = node_text(child, source)
            elif child.type == "identifier":  # pragma: no cover
                controller = node_text(child, source)
        elif arg_index == 2 and child.type == "atom":
            # Third arg is the action (atom) of get/post/.../live
            action = node_text(child, source).lstrip(":")
        elif arg_index >= 2 and child.type in ("keywords", "list"):
            # Trailing keyword options (resources' only:/except:/...)
            options = _keyword_options(child, source)
        arg_index += 1
    return route_path, controller, action, options


def _phoenix_scope(
    call: "tree_sitter.Node", source: bytes, outer: _RouterScope,
    alias_hints: dict[str, str],
) -> _RouterScope:
    """The alias and path a ``scope`` call establishes for the routes inside it.

    ``Phoenix.Router.scope`` takes ``(options)``, ``(path, options)``,
    ``(path, alias)`` or ``(path, alias, options)``, with the path given
    positionally or as ``path:`` and the alias positionally or as
    ``alias:``. The path's segments are appended to the outer scope's. The
    alias is lexically expanded against the router's own ``alias``
    directives and joined onto the enclosing scope's alias (``scope "/",
    AppWeb`` + ``scope "/admin", Admin`` -> ``AppWeb.Admin``); ``alias:
    false`` resets it, and a scope that names no alias inherits the outer
    one.
    """
    args = find_child_by_type(call, "arguments")
    if args is None:
        return outer
    alias: Any = None
    path: Any = None
    for child in args.children:
        if child.type == "alias":
            alias = node_text(child, source)
        elif child.type == "string":
            path = _literal_value(child, source)
        elif child.type in ("keywords", "list"):
            options = _keyword_options(child, source)
            alias = options.get("alias", alias)
            path = options.get("path", path)
    segments = outer.path + (
        _path_segments(path) if isinstance(path, str) else ())
    if alias is False:
        return _RouterScope(None, segments)
    if not isinstance(alias, str):
        return _RouterScope(outer.alias, segments)
    alias = _expand_alias(alias, alias_hints)
    return _RouterScope(
        f"{outer.alias}.{alias}" if outer.alias else alias, segments)


def _phoenix_resources_scope(
    call: "tree_sitter.Node", source: bytes, outer: _RouterScope,
) -> _RouterScope:
    """The scope a ``resources ... do`` block opens for its nested routes.

    Phoenix nests them under the resource's member path:
    ``<path>/:<name>_<param>`` (``resources "/users", UserController do``
    -> ``/users/:user_id``), or the bare ``<path>`` for ``singleton: true``.
    ``name:`` overrides the controller-derived name and ``param:`` the
    ``id``. The alias is unchanged.
    """
    route_path, controller, _action, options = _parse_route_args(call, source)
    segments = outer.path + _path_segments(route_path or "")
    if options.get("singleton") is not True:
        name = options.get("name")
        if not isinstance(name, str):
            name = _phoenix_resource_name(controller or "")
        param = options.get("param")
        param = param if isinstance(param, str) else "id"
        segments += (f":{name}_{param}",)
    return _RouterScope(outer.alias, segments)


#: The route macros each router DSL defines. ``Phoenix.Router`` adds
#: ``resources`` and ``live`` (``Phoenix.LiveView.Router``, imported by every
#: generated router) to the HTTP verbs; ``Plug.Router`` has the verbs only.
_ROUTER_MACROS: dict[str, frozenset[str]] = {
    "phoenix": frozenset(PHOENIX_HTTP_METHODS | {"resources", "live"}),
    "plug": frozenset(PHOENIX_HTTP_METHODS),
}


def _module_router_kind(
    defmodule: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """Which router DSL a ``defmodule`` body opts into, else None.

    ``"phoenix"`` for ``use Phoenix.Router`` or the generated web module's
    dispatcher ``use AppWeb, :router`` (its ``def router`` quotes ``use
    Phoenix.Router``, which runs in the module that uses it -- so the
    ``:router`` atom IS the evidence, and the quote in ``AppWeb`` itself
    makes ``AppWeb`` no router); ``"plug"`` for ``use Plug.Router``. Only
    the body's top-level statements are read: a ``use`` inside a ``def``,
    a ``quote`` or a nested ``defmodule`` belongs to some other module.
    """
    body = find_child_by_type(defmodule, "do_block")
    if body is None:
        return None
    for stmt in body.named_children:
        if stmt.type != "call":
            continue
        target = find_child_by_type(stmt, "identifier")
        if target is None or node_text(target, source) != "use":
            continue
        args = find_child_by_type(stmt, "arguments")
        if args is None:  # pragma: no cover - a bare ``use`` does not compile
            continue
        written = [c for c in args.named_children if c.type != "comment"]
        if not written or written[0].type != "alias":
            continue
        module = node_text(written[0], source)
        if module == "Phoenix.Router":
            return "phoenix"
        if module == "Plug.Router":
            return "plug"
        if (len(written) > 1 and written[1].type == "atom"
                and node_text(written[1], source) == ":router"):
            return "phoenix"
    return None


def _iter_router_calls(
    root: "tree_sitter.Node", source: bytes, alias_hints: dict[str, str],
) -> Iterator[tuple["tree_sitter.Node", _RouterScope, str]]:
    """Every ``call`` inside a router module, with the scope in force there.

    Yields ``(call, scope, router_kind)`` where ``router_kind`` is the DSL
    the innermost enclosing ``defmodule`` opts into
    (:func:`_module_router_kind`). A call in no router module is not
    yielded: ``get`` / ``put`` / ``live`` are ordinary function names
    everywhere else (a cache module's ``get("user_1")``, a test's
    ``conn |> get("/pages")``), so the router module is what makes them
    route macros. A pre-order walk (the same order as ``iter_tree``) that
    carries the enclosing ``scope`` alias and path down into each scope's
    ``do`` block (and a ``resources`` call's ``do`` block, which nests under
    the resource's member path), so a route macro knows which module prefix
    Phoenix concatenates onto its controller and which path it prepends. A
    nested ``defmodule`` starts afresh: its own ``use`` decides, and no
    outer scope applies to it.
    """
    stack: list[tuple["tree_sitter.Node", _RouterScope, Optional[str]]] = [
        (root, _RouterScope(None, ()), None)]
    while stack:
        node, scope, router = stack.pop()
        inner = scope
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            name = node_text(target, source) if target is not None else ""
            if name == "defmodule":
                router = _module_router_kind(node, source)
                inner = _RouterScope(None, ())
            elif router is not None:
                yield node, scope, router
                if name == "scope":
                    inner = _phoenix_scope(node, source, scope, alias_hints)
                elif (name == "resources"
                      and find_child_by_type(node, "do_block") is not None):
                    inner = _phoenix_resources_scope(node, source, scope)
        stack.extend((child, inner, router) for child in reversed(node.children))


def _extract_phoenix_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str,
    pass_id: str,
    alias_hints: dict[str, str] | None = None,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Phoenix router DSL calls.

    Only calls inside a router module are read (:func:`_iter_router_calls`),
    and only the macros that module's DSL defines (``_ROUTER_MACROS``: a
    ``use Plug.Router`` module has the HTTP verbs, not ``resources`` /
    ``live``). A call on the right of ``|>`` is never a route macro.

    Detects patterns like:
    - get "/", PageController, :index
    - post "/users", UserController, :create
    - resources "/posts", PostController
    - live "/dashboard", DashboardLive
    - live "/users/:id", UserLive.Show, :show

    The ``live`` macro creates LiveView routes (WebSocket-based server-rendered
    views). These get ``http_method="LIVE"`` to distinguish them from traditional
    HTTP routes.  For handler linking, live routes without an explicit action
    default to ``action="mount"`` (the LiveView entry callback).

    ``resources`` expands through :func:`_phoenix_resource_routes`, honouring
    its trailing keyword options. Every controller / LiveView module is
    expanded against the router's ``alias`` directives and then prefixed with
    the enclosing ``scope`` alias (:func:`_iter_router_calls`), so it names
    the module Phoenix actually dispatches to (``AppWeb.PageController``, not
    ``PageController``) and the route-handler linker can match it exactly
    instead of by suffix. Likewise every path is joined onto the enclosing
    ``scope`` paths and ``resources ... do`` member paths
    (:func:`_join_route_path`): ``scope "/admin"`` + ``get "/users"`` is
    ``/admin/users``, as ``mix phx.routes`` lists it.

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols are route markers (kind="function" +
        meta.framework_role="route"), which enables route-handler linking.
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []
    hints = alias_hints or {}

    for n, scope, router in _iter_router_calls(node, source, hints):
        # Get the function name (get, post, resources, etc.)
        target_node = find_child_by_type(n, "identifier")
        if not target_node:
            continue

        method_name = node_text(target_node, source).lower()

        # A route macro of THIS module's router DSL, written as a macro call:
        # never the right side of ``|>``, which would hand it the conn as
        # its path (``conn |> get("/x")`` is a request helper, even when a
        # router's plug function makes it).
        if (method_name not in _ROUTER_MACROS[router]
                or _is_pipe_rhs(n, source)):
            continue

        route_path, controller, action, options = _parse_route_args(
            n, source)

        if not route_path:  # pragma: no cover
            continue

        # Phoenix expands the written controller against the router's
        # lexical aliases, then concatenates the enclosing scope alias.
        if controller:
            controller = _expand_alias(controller, hints)
            if scope.alias:
                controller = f"{scope.alias}.{controller}"

        # Build metadata
        normalized_path = _join_route_path(scope.path, route_path)
        if method_name in PHOENIX_HTTP_METHODS:
            http_method = method_name.upper()
        elif method_name == "live":
            http_method = "LIVE"
        else:
            http_method = "RESOURCES"
        metadata: dict[str, str | None] = {
            "route_path": normalized_path,
            "http_method": http_method,
        }
        if controller:
            metadata["controller"] = controller
        if method_name == "live" and not action:
            # LiveView routes without explicit action default to "mount"
            # (the entry callback for all LiveView modules)
            action = "mount"
        if action:
            metadata["action"] = action

        # Create UsageContext
        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,  # e.g., "get", "post", "resources"
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,  # Router DSL doesn't directly reference symbols
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route Symbol(s) - enables route-handler linking
        if method_name == "resources":
            restful_routes = _phoenix_resource_routes(normalized_path, options)
            for http_meth, route_pth, act in restful_routes:
                route_symbols.append(make_route_symbol(
                    language="elixir",
                    path=str(file_path),
                    span=span,
                    method=http_meth,
                    route_path=route_pth,
                    origin=pass_id,
                    origin_run_id=run_id,
                    extra_meta={"controller": controller, "action": act},
                ))
        else:
            # Single route (HTTP method or LiveView)
            route_extra: dict[str, str] = {}
            if controller:
                route_extra["controller"] = controller
            if action:
                route_extra["action"] = action
            route_symbols.append(make_route_symbol(
                language="elixir",
                path=str(file_path),
                span=span,
                method=http_method,
                route_path=normalized_path,
                origin=pass_id,
                origin_run_id=run_id,
                extra_meta=route_extra,
            ))

    return contexts, route_symbols


def _first_alias_argument(call: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The first ``alias`` argument of a directive call (``use X`` / ``behaviour X``)."""
    args = find_child_by_type(call, "arguments")
    if args is None:
        return None
    return find_child_by_type(args, "alias")


#: What ``_behaviour_directives`` writes for ``@behaviour unquote(__MODULE__)``,
#: the spelling a ``__using__`` template uses for "the module defining me".
_UNQUOTED_MODULE = "unquote(__MODULE__)"


def _is_unquoted_module(call: "tree_sitter.Node", source: bytes) -> bool:
    """Is the directive's argument ``unquote(__MODULE__)``?"""
    args = find_child_by_type(call, "arguments")
    inner = find_child_by_type(args, "call") if args is not None else None
    return inner is not None and node_text(inner, source) == _UNQUOTED_MODULE


class _Directive(NamedTuple):
    """One ``use X`` / ``@behaviour X`` as ``_behaviour_directives`` reads it."""

    kind: str  # "use" or "behaviour"
    module: str  # written module, or ``_UNQUOTED_MODULE``
    #: ``use AppWeb, :live_view`` / ``use AppWeb, live_view: opts`` ->
    #: ``"live_view"``: the name a web-module dispatcher applies. None for
    #: ``@behaviour`` and for a ``use`` with no atom / keyword second argument.
    which: Optional[str]
    node: "tree_sitter.Node"
    #: ``@behaviour :gen_cycle`` (WI-hidit): ``module`` is an Erlang module's
    #: atom, colon stripped -- its full name, so no alias expands it.
    atom: bool = False


def _directive_module(directive: _Directive, alias_hints: dict[str, str]) -> str:
    """The module a directive names: alias-expanded, unless it is an atom."""
    if directive.atom:
        return directive.module
    return _expand_alias(directive.module, alias_hints)


def _use_dispatch_name(args: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The ``which`` of ``use M, :which`` or ``use M, which: opts``, else None.

    The two spellings the generated web module's two ``__using__`` clauses
    accept (``__using__(which) when is_atom(which)`` and
    ``__using__([{which, opts}])``). Any ``use`` with a second argument gets a
    name -- ``use GenServer, restart: :temporary`` gets ``"restart"`` -- and it
    is harmless there: a name is read only against a module that dispatches
    (``_dispatching_modules``).
    """
    written = [c for c in args.named_children if c.type != "comment"]
    if len(written) < 2:
        return None
    second = written[1]
    if second.type == "atom":
        return _atom_field_name(second, source)
    if second.type == "keywords":
        keys = _keyword_pair_keys(second, source)
        return keys[0] if keys else None  # pragma: no cover - parsed keywords have a pair
    return None


def _behaviour_directives(
    tree: "tree_sitter.Tree", source: bytes,
) -> list[_Directive]:
    """Every ``use X`` and ``@behaviour X`` naming a module, in order.

    The ONE parse of these directives: the framework-callback table, the
    behaviour type relationship and the ``__using__`` index all read it, so
    they cannot disagree about what a file declared. The module is an alias,
    or -- for ``@behaviour`` only -- ``unquote(__MODULE__)``, recorded as
    ``_UNQUOTED_MODULE`` and meaningful only inside a ``quote``, or an atom
    naming an Erlang module (``@behaviour :gen_cycle``, WI-hidit), recorded
    without its colon and flagged ``atom``. Not read: a quoted atom
    (``:"Elixir.Foo"`` is an alias spelled the hard way, never an Erlang
    module), and ``use :mod`` -- ``use`` calls the module's ``__using__``
    MACRO, which an Erlang module cannot define (0 sites on the local corpus).
    """
    directives: list[_Directive] = []
    for node in iter_tree(tree.root_node):
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            if target is None or node_text(target, source) != "use":
                continue
            args = find_child_by_type(node, "arguments")
            alias = find_child_by_type(args, "alias") if args is not None else None
            if args is not None and alias is not None:
                directives.append(_Directive(
                    "use", node_text(alias, source),
                    _use_dispatch_name(args, source), node,
                ))
            continue
        attribute = _module_attribute_call(node, source)
        if attribute is not None and attribute[0] == "behaviour":
            alias = _first_alias_argument(attribute[1])
            if alias is not None:
                directives.append(_Directive(
                    "behaviour", node_text(alias, source), None, node,
                ))
            elif _is_unquoted_module(attribute[1], source):
                directives.append(_Directive(
                    "behaviour", _UNQUOTED_MODULE, None, node,
                ))
            else:
                args = find_child_by_type(attribute[1], "arguments")
                atom = find_child_by_type(args, "atom") if args is not None else None
                if atom is not None:
                    directives.append(_Directive(
                        "behaviour", node_text(atom, source).lstrip(":"),
                        None, node, atom=True,
                    ))
    return directives


#: A ``use`` template. ``(M, None)`` is the ``quote`` in ``M``'s ``defmacro
#: __using__``; ``(M, name)`` is the ``quote`` in ``M``'s public ``def name``,
#: which ``use M, :name`` runs when ``M`` is a web-module dispatcher
#: (``_dispatching_modules``).
_TemplateKey = tuple[str, Optional[str]]

#: ``template -> (behaviours, used)``: what a template declares on its USER --
#: its ``@behaviour`` names, and the ``use`` it performs as ``(module, which)``.
_UsingIndex = dict[_TemplateKey, tuple[list[str], list[_TemplateKey]]]


def _def_keyword(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """``"def"`` / ``"defp"`` / ``"defmacro"`` / ``"defmacrop"`` for a def call."""
    if node.type != "call":
        return None
    target = find_child_by_type(node, "identifier")
    keyword = node_text(target, source) if target is not None else None
    return keyword if keyword in _DEF_KEYWORDS else None


def _is_self_apply(using: "tree_sitter.Node", source: bytes) -> bool:
    """Does this ``defmacro __using__`` dispatch with ``apply(__MODULE__, w, _)``?

    ``w`` must be a name bound in the head (``__using__(which)``,
    ``__using__([{which, opts}])``): ``apply(__MODULE__, :fixed, [])`` runs one
    known function whatever is used, and ``apply(Other, which, [])`` another
    module's.
    """
    head_names = {
        node_text(n, source)
        for param in _def_head_arguments(using) or ()
        for n in iter_tree(param)
        if n.type == "identifier"
    }
    for node in iter_tree(using):
        if node.type != "call":
            continue
        target = find_child_by_type(node, "identifier")
        if target is None or node_text(target, source) != "apply":
            continue
        args = find_child_by_type(node, "arguments")
        written = args.named_children if args is not None else []
        if (len(written) >= 2
                and node_text(written[0], source) == "__MODULE__"
                and written[1].type == "identifier"
                and node_text(written[1], source) in head_names):
            return True
    return False


def _dispatching_modules(tree: "tree_sitter.Tree", source: bytes) -> set[str]:
    """Modules whose ``__using__`` is the Phoenix web-module dispatcher.

    The generator's ``defmacro __using__(which) when is_atom(which), do:
    apply(__MODULE__, which, [])`` (plus plausible's ``__using__([{which,
    opts}])`` clause): ``use AppWeb, :live_view`` runs ``AppWeb.live_view()``
    and injects the ``quote`` it returns. Only a module with such a clause has
    its ``def`` quotes indexed as templates -- in any other module a ``def``
    returning a quote is an ordinary function.
    """
    modules: set[str] = set()
    for node in iter_tree(tree.root_node):
        if (_def_keyword(node, source) == "defmacro"
                and _get_function_name(node, source) == "__using__"
                and not _has_quote_ancestor(node, source)
                and _nearest_enclosing_module_resolvable(node, source)
                and _is_self_apply(node, source)):
            modules.add(_enclosing_module(node, source))
    return modules


def _template_key(
    node: "tree_sitter.Node", source: bytes, dispatchers: set[str],
) -> Optional[_TemplateKey]:
    """The template a directive inside a ``quote`` belongs to, else None.

    ``(M, None)`` when a ``defmacro __using__`` of ``M`` encloses it;
    ``(M, name)`` when its nearest enclosing def is a public ``def name`` of a
    dispatcher ``M`` (a ``defp`` cannot be ``apply``-ed from outside). A
    template with no alias-named module has no owner.
    """
    nearest_def: Optional["tree_sitter.Node"] = None
    current = node.parent
    while current is not None:
        keyword = _def_keyword(current, source)
        if keyword == "defmacro" and _get_function_name(current, source) == "__using__":
            if not _nearest_enclosing_module_resolvable(current, source):
                return None
            return (_enclosing_module(current, source), None)
        if keyword is not None and nearest_def is None:
            nearest_def = current
        current = current.parent
    if (nearest_def is None
            or _def_keyword(nearest_def, source) != "def"
            or _has_quote_ancestor(nearest_def, source)
            or not _nearest_enclosing_module_resolvable(nearest_def, source)):
        return None
    module = _enclosing_module(nearest_def, source)
    name = _get_function_name(nearest_def, source)
    if module not in dispatchers or name is None:
        return None
    return (module, name)


def _extract_using_index(
    tree: "tree_sitter.Tree", source: bytes,
) -> _UsingIndex:
    """What each ``use`` template in this file declares on its USER.

    ``template -> (behaviours, used)`` (``_TemplateKey``): the ``@behaviour``
    and ``use`` directives inside a ``quote`` in ``defmacro __using__``, or in
    a public ``def`` of a web-module dispatcher (WI-nunar: ``use AppWeb,
    :live_view`` runs ``AppWeb.live_view``'s quote). This is how an in-repo
    behaviour is usually adopted -- ``use Plausible.Cache`` runs a
    ``__using__`` whose ``quote`` sets ``@behaviour Plausible.Cache`` -- and
    the in-repo analogue of ``USE_IMPLIED_BEHAVIOURS``. ``unquote(__MODULE__)``
    is the defining module. Names are alias-expanded with the DEFINING file's
    aliases, because an alias inside a ``quote`` is expanded where the quote is
    written. A ``use`` here is followed transitively in Pass 2. NOT followed:
    a quote spliced in with ``unquote(helpers())``.
    """
    index: _UsingIndex = {}
    alias_hints: Optional[dict[str, str]] = None
    dispatchers: Optional[set[str]] = None
    for directive in _behaviour_directives(tree, source):
        if not _has_quote_ancestor(directive.node, source):
            continue
        if dispatchers is None:
            dispatchers = _dispatching_modules(tree, source)
        key = _template_key(directive.node, source, dispatchers)
        if key is None:
            continue
        if alias_hints is None:
            alias_hints = _extract_alias_hints(tree, source)
        behaviours, used = index.setdefault(key, ([], []))
        if directive.module == _UNQUOTED_MODULE:
            behaviours.append(key[0])
        elif directive.kind == "behaviour":
            behaviours.append(_directive_module(directive, alias_hints))
        else:
            used.append(
                (_expand_alias(directive.module, alias_hints), directive.which),
            )
    return index


def _adopted(
    used: str,
    which: Optional[str],
    using_index: _UsingIndex,
    visiting: Optional[set[_TemplateKey]] = None,
) -> tuple[list[str], list[str]]:
    """What ``use <used>[, :which]`` runs in the using module, in order.

    Returns ``(modules, behaviours)``: every module whose ``use`` effectively
    runs there (``used`` itself, then each one its templates ``use``, followed
    transitively: ``use AppWeb, :live_view`` -> ``use Phoenix.LiveView``), and
    the behaviours they declare -- the library table (``USE_IMPLIED_BEHAVIOURS``)
    for each module, plus each in-repo template's ``@behaviour``. Templates
    read for a module: its ``__using__`` quote, and its ``def which`` quote
    when it is a dispatcher. A visited set ends a cycle of templates.
    """
    visiting = set() if visiting is None else visiting
    modules: list[str] = [used]
    behaviours: list[str] = []
    library = USE_IMPLIED_BEHAVIOURS.get(used)
    if library is not None:
        behaviours.append(library)
    keys: list[_TemplateKey] = [(used, None)]
    if which is not None:
        keys.append((used, which))
    for key in keys:
        if key in visiting:
            continue
        visiting.add(key)
        template_behaviours, nested = using_index.get(key, ([], []))
        behaviours.extend(template_behaviours)
        for inner, inner_which in nested:
            inner_modules, inner_behaviours = _adopted(
                inner, inner_which, using_index, visiting,
            )
            modules.extend(inner_modules)
            behaviours.extend(inner_behaviours)
    return list(dict.fromkeys(modules)), list(dict.fromkeys(behaviours))


def _extract_behaviour_callbacks(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Any],
    run_id: str,
    file_symbols_multi: dict[str, list[Symbol]] | None = None,
    global_symbols_multi: dict[str, list[Symbol]] | None = None,
    alias_hints: dict[str, str] | None = None,
    using_index: _UsingIndex | None = None,
) -> list[Edge]:
    """Extract the edges ``use X`` / ``@behaviour X`` imply.

    Each directive is resolved ONCE -- ``@behaviour X`` names X after alias
    expansion; ``use X`` names what X's templates run and declare
    (``_adopted``: the library table, the in-repo ``__using__`` index, and a
    web-module dispatcher's ``def which`` quote) -- and two readings consume
    the result:

    1. The FRAMEWORK CALLBACK TABLE. For every adopted module or behaviour in
       ``BEHAVIOUR_CALLBACKS`` (``use GenServer``, ``use AppWeb, :live_view``
       -> ``Phoenix.LiveView``, ``@behaviour Plug``), the framework invokes
       specific callback functions (init, handle_call, mount, render, ...),
       so the module ``dispatches_to`` each one it implements and they are
       not orphans (``_framework_callback_edges``).
    2. The BEHAVIOUR TYPE RELATIONSHIP (WI-vitas), for every behaviour, in-repo
       or not: ``implements`` and callback -> implementation dispatch
       (``_behaviour_edges``), once per (module, behaviour) pair however many
       directives repeat it.

    Skipped by both: a directive inside ``quote`` (it belongs to the module
    that runs ``use``, INV-sinah -- attributing ``quote do @behaviour Cache
    end`` to ``Cache`` is a self-edge, and a web module's ``def live_view``
    quote does not make the web module a LiveView), one whose nearest module
    has no alias name, and a module naming itself as its behaviour.
    """
    alias_hints = alias_hints or {}
    using_index = using_index or {}
    global_symbols_multi = global_symbols_multi or {}
    callbacks_by_behaviour: dict[str, list[Symbol]] = global_symbols.get(
        _CALLBACKS_KEY, {},
    )
    edges: list[Edge] = []
    seen_behaviours: set[tuple[str, str]] = set()
    seen_callbacks: set[tuple[str, str]] = set()
    for directive in _behaviour_directives(tree, source):
        node = directive.node
        if (directive.module == _UNQUOTED_MODULE
                or _has_quote_ancestor(node, source)
                or not _nearest_enclosing_module_resolvable(node, source)):
            continue
        expanded = _directive_module(directive, alias_hints)
        modules: list[str] = []
        behaviours: list[str] = [expanded]
        if directive.kind == "use":
            modules, behaviours = _adopted(expanded, directive.which, using_index)
        module_name = _enclosing_module(node, source)
        module_sym = file_symbols.get(module_name) or global_symbols.get(module_name)
        if module_sym is None:  # pragma: no cover - a resolvable module always has a symbol
            continue
        line = node.start_point[0] + 1
        for framework in dict.fromkeys(modules + behaviours):
            # An Erlang behaviour (``@behaviour :gen_statem``) roots OTP's
            # callbacks from the Erlang analyzer's table; the two tables cannot
            # collide, an alias being capitalised and an atom not.
            callback_names = (
                BEHAVIOUR_CALLBACKS.get(framework)
                or OTP_BEHAVIOUR_CALLBACKS.get(framework, [])
            )
            edges.extend(_framework_callback_edges(
                module_sym, module_name, callback_names,
                line, file_symbols, global_symbols, file_symbols_multi or {},
                global_symbols_multi, run_id, seen_callbacks,
            ))
        for behaviour in behaviours:
            if behaviour == module_name or (module_sym.id, behaviour) in seen_behaviours:
                continue
            seen_behaviours.add((module_sym.id, behaviour))
            edges.extend(_behaviour_edges(
                module_sym, module_name, behaviour, line,
                global_symbols, callbacks_by_behaviour, global_symbols_multi,
                run_id,
            ))
    return edges


def _framework_callback_edges(
    module_sym: Symbol,
    module_name: str,
    callback_names: list[str],
    line: int,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Any],
    file_symbols_multi: dict[str, list[Symbol]],
    global_symbols_multi: dict[str, list[Symbol]],
    run_id: str,
    seen: set[tuple[str, str]],
) -> list[Edge]:
    """Module -> each implemented framework callback, once per (module, function).

    Looked up by QUALIFIED name only (``<module>.<callback>``). A bare-name
    fallback used to be tried last, and in a file holding several modules it
    rooted ANOTHER module's same-named ``mount`` / ``init`` as this one's
    callback.
    """
    edges: list[Edge] = []
    for callback_name in callback_names:
        qualified = f"{module_name}.{callback_name}"
        callees = (
            file_symbols_multi.get(qualified)
            or global_symbols_multi.get(qualified)
        )
        if not callees:
            single = file_symbols.get(qualified) or global_symbols.get(qualified)
            callees = [single] if single else []
        for callee in callees:
            if (module_sym.id, callee.id) in seen:
                continue
            seen.add((module_sym.id, callee.id))
            edges.append(Edge.create(
                src=module_sym.id,
                dst=callee.id,
                edge_type="dispatches_to",
                line=line,
                evidence_type="behaviour_callback",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={"mechanism": "callback"},
            ))
    return edges


def _behaviour_edges(
    module_sym: Symbol,
    module_name: str,
    behaviour: str,
    line: int,
    global_symbols: dict[str, Any],
    callbacks_by_behaviour: dict[str, list[Symbol]],
    global_symbols_multi: dict[str, list[Symbol]],
    run_id: str,
) -> list[Edge]:
    """The ``implements`` edge and the callback dispatch for one pair.

    The edges themselves come from ``hypergumbo_core.behaviours``, shared
    with the Erlang analyzer and the BEAM behaviour bridge linker.
    """
    target = global_symbols.get(behaviour)
    edges: list[Edge] = [behaviour_implements_edge(
        src=module_sym.id, lang="elixir", behaviour=behaviour,
        target_id=(
            target.id
            if isinstance(target, Symbol) and target.kind == "module" else None
        ),
        line=line, origin=PASS_ID, run_id=run_id,
    )]

    for callback in callbacks_by_behaviour.get(behaviour, ()):
        short_name = callback.name.rsplit(".", 1)[-1]
        arity = (callback.meta or {}).get(_ARITY_KEY)
        candidates = [
            impl for impl in global_symbols_multi.get(f"{module_name}.{short_name}", ())
            if impl.kind == callback.kind and "private" not in impl.modifiers
        ]
        # By name AND arity (INV-dital): ``f/1`` is not implemented by an
        # ``f/2`` beside it. A function of unknown arity is kept.
        for impl in narrow_by_arity(
            candidates, arity if isinstance(arity, int) else None,
        ):
            edges.append(callback_dispatch_edge(
                callback_id=callback.id, impl_id=impl.id, line=line,
                origin=PASS_ID, run_id=run_id,
            ))
    return edges


def _extract_protocol_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_symbols: list[Symbol],
    global_symbols: dict[str, Any],
    global_symbols_multi: dict[str, list[Symbol]],
    run_id: str,
) -> list[Edge]:
    """The edges each ``defimpl`` in this file implies (WI-forof).

    A protocol is a behaviour -- ``defprotocol`` generates a ``@callback``
    per function head and ``defimpl`` sets ``@behaviour P`` on the impl
    module -- so the type relationship is the behaviour's, built by
    ``_behaviour_edges`` from ``hypergumbo_core.behaviours``: ``implements``
    ``P.T -> P`` (the in-repo protocol module, else the unresolved
    ``elixir:external:0-0:P:unresolved``) and each protocol function
    ``dispatches_to`` the impl's same-name, same-arity public functions.

    An impl of a protocol OUTSIDE the repository (``String.Chars``,
    ``Enumerable``, ``Inspect``, ``Jason.Encoder``, ``Phoenix.Param``) has no
    in-repo protocol function to be dispatched from, yet the runtime calls it
    (``to_string/1``, ``Enum.count/1``, ``Jason.encode/1`` dispatch to it). Its
    impl module ``dispatches_to`` each of its public functions instead, the
    way the framework callback table roots a GenServer's callbacks. The roots
    are DERIVED from "implements a protocol the repo does not define", not
    listed per protocol: an impl module exists only to be dispatched to, and
    a protocol's functions are exactly what it defines publicly (helpers are
    ``defp``). An in-repo protocol's impls are reached through the protocol
    function, so their module does not root them.
    """
    callbacks_by_behaviour: dict[str, list[Symbol]] = global_symbols.get(
        _CALLBACKS_KEY, {},
    )
    modules = {s.name: s for s in file_symbols if s.kind == "module"}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for node in iter_tree(tree.root_node):
        if _definer_keyword(node, source) != "defimpl":
            continue
        impl = _impl_modules(node, source)
        if impl is None:
            continue
        protocol, impl_names = impl
        target = global_symbols.get(protocol)
        in_repo = isinstance(target, Symbol) and target.kind == "module"
        line = node.start_point[0] + 1
        for impl_name in impl_names:
            module_sym = modules.get(impl_name)
            if module_sym is None:  # pragma: no cover - every impl name minted a module symbol in Pass 1
                continue
            edges.extend(_behaviour_edges(
                module_sym, impl_name, protocol, line, global_symbols,
                callbacks_by_behaviour, global_symbols_multi, run_id,
            ))
            if in_repo:
                continue
            public = sorted({
                s.name.rsplit(".", 1)[-1] for s in file_symbols
                if s.kind == "function" and _symbol_module(s.name) == impl_name
                and "private" not in s.modifiers
            })
            edges.extend(_framework_callback_edges(
                module_sym, impl_name, public, line, {}, global_symbols, {},
                global_symbols_multi, run_id, seen,
            ))
    return edges


def _atom_field_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Return a field name from an ``atom`` / ``quoted_atom`` node, or None.

    ``:name`` -> ``name`` (leading ``:`` stripped); ``:"weird name"`` -> the
    ``quoted_content`` text.
    """
    if node.type == "atom":
        return node_text(node, source).lstrip(":")
    if node.type == "quoted_atom":
        content = find_child_by_type(node, "quoted_content")
        return node_text(content, source) if content is not None else None  # pragma: no cover - a quoted_atom always has quoted_content
    return None


def _keyword_key(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Return a field key from a ``keyword`` / ``quoted_keyword`` node, or None.

    ``x:`` -> ``x``; ``"weird key":`` -> the ``quoted_content`` text.
    """
    if node.type == "keyword":
        return node_text(node, source).split(":")[0].strip()
    if node.type == "quoted_keyword":
        content = find_child_by_type(node, "quoted_content")
        return node_text(content, source) if content is not None else None  # pragma: no cover - a quoted_keyword always has quoted_content
    return None  # pragma: no cover - a keyword pair's first child is always a keyword / quoted_keyword


def _keyword_pair_keys(keywords_node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Return the KEY of each direct ``pair`` in a ``keywords`` node.

    ``x: 0, y: 0`` -> [``x``, ``y``]. Only each pair's first child (its
    ``keyword``/``quoted_keyword`` key) is a field; a default VALUE that is itself
    a keyword list (``opts: [a: 1]``) nests its ``a:`` under the value, so it is
    not mistaken for a field.
    """
    keys: list[str] = []
    for pair in keywords_node.children:
        if pair.type == "pair" and pair.children:
            key = _keyword_key(pair.children[0], source)
            if key is not None:
                keys.append(key)
    return keys


def _extract_defstruct_field_names(
    defstruct_call: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Return the field names declared by a ``defstruct`` macro call, or [].

    ``defstruct`` takes a single argument, WITH OR WITHOUT surrounding parens.
    Its container is a ``list`` (of atoms and/or tuple/keyword pairs) or
    ``keywords`` (default pairs). Fields come from: a ``list`` ``atom`` /
    ``quoted_atom`` (``[:name]`` / ``[:"weird"]``); a ``keywords`` or in-list
    keyword pair's KEY (``x: 0`` / ``"k": 0`` -> ``x``/``k``, never the default
    value); or a tuple-literal pair (``[{:name, 0}]`` — what ``[name: 0]``
    desugars to) whose first atom is the field. A ``@fields`` module-attribute
    reference (a ``unary_operator``) is dynamic and yields no static fields (a
    fails-safe miss, never a phantom).
    """
    args = find_child_by_type(defstruct_call, "arguments")
    if args is None:  # pragma: no cover - a `defstruct` call always has an arguments node (a bare `defstruct` parses as an identifier, not a call)
        return []
    # NAMED children skip the surrounding `(`/`)` tokens of a parenthesized
    # `defstruct([...])` / `defstruct(k: v)`, so [0] is the real container in
    # both the paren and paren-less forms.
    container = args.named_children[0] if args.named_children else None
    if container is None:  # pragma: no cover - a defstruct with args always has a named container
        return []
    names: list[str] = []
    if container.type == "keywords":
        names.extend(_keyword_pair_keys(container, source))
    elif container.type == "list":
        for child in container.children:
            if child.type in ("atom", "quoted_atom"):
                name = _atom_field_name(child, source)
                if name is not None:
                    names.append(name)
            elif child.type == "keywords":
                names.extend(_keyword_pair_keys(child, source))
            elif child.type == "tuple":
                # `[{:name, 0}]` — the field is the FIRST atom (the key); the
                # value that follows is not.
                for grandchild in child.children:
                    name = _atom_field_name(grandchild, source)
                    if name is not None:
                        names.append(name)
                        break
    # else: `@fields` reference / other dynamic form -> no static fields.
    return names


def _has_quote_ancestor(node: "tree_sitter.Node", source: bytes) -> bool:
    """True if ``node`` is inside a ``quote do … end`` block.

    A ``defstruct`` (or ``def``) inside a ``quote`` is a TEMPLATE injected into
    whatever module later runs ``use``, not a definition on the syntactically
    enclosing module — so its fields must not be attributed to the defining
    module. (The broader analyzer-wide quote-attribution gap — the same is true
    of ``def``/``defmacro`` inside a quote — is tracked separately.)
    """
    current = node.parent
    while current is not None:
        if current.type == "call":
            target = find_child_by_type(current, "identifier")
            if target is not None and node_text(target, source) == "quote":
                return True
        current = current.parent
    return False


def _nearest_enclosing_module_resolvable(
    node: "tree_sitter.Node", source: bytes
) -> bool:
    """True iff the NEAREST enclosing module definer has a resolvable name.

    An atom-named module (``defmodule :"x"``) yields None from ``_get_module_name``
    and is SKIPPED by ``_enclosing_module_names``, so a defstruct nested inside it
    would be mis-attributed to a resolvable ANCESTOR module (a wrong owner).
    Gating on the nearest module's resolvability drops the field instead
    (fails-safe). Also returns False for a defstruct with NO enclosing module
    (top-level — invalid but parseable), so it emits nothing. A
    ``defprotocol`` / ``defimpl`` is a module definer like ``defmodule``.
    """
    definer = _nearest_definer(node, source)
    return definer is not None and bool(_defined_module_names(definer, source))


def _module_symbol(
    node: "tree_sitter.Node", file_path: str, run_id: str, full_name: str,
) -> Symbol:
    """The ``module`` symbol of a ``defmodule`` / ``defprotocol`` / ``defimpl``."""
    span = _span_of(node)
    return Symbol(
        id=make_symbol_id(
            "elixir", file_path, span.start_line, span.end_line, full_name, "module",
        ),
        name=full_name,
        kind="module",
        language="elixir",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
    )


class _FunctionKey(NamedTuple):
    """What makes two ``def`` clauses ONE Elixir function (WI-tusad).

    Elixir identifies a function by module, name and arity, so clauses agreeing
    on all three fold into one symbol; ``kind`` is part of the key because a
    ``def`` and a ``defmacro`` of one name/arity are two definitions (Elixir
    rejects the pair, but they must not merge here). ``arity`` is the TOTAL
    parameter count, defaulted ones included: ``def f(a, b \\\\ 1)`` is the
    one function ``f/2``, also callable as ``f/1``, exactly the grouping the
    compiler uses (a separate ``def f(a)`` beside it is a conflicting
    definition). None when the head is not a plain name list.
    """

    full_name: str
    kind: str
    arity: Optional[int]


class _DefClause(NamedTuple):
    """One ``def`` / ``defp`` / ``defmacro`` / ``defmacrop`` call that defines
    something: its short name, the function each owning module gets, and
    whether it is a protocol head (a callback, not a function body)."""

    func_name: str
    keys: list[_FunctionKey]
    in_protocol: bool


def _def_kind(keyword: str) -> str:
    """``macro`` for ``defmacro`` / ``defmacrop``, else ``function``."""
    return "macro" if keyword.startswith("defmacro") else "function"


def _def_clause(
    node: "tree_sitter.Node", source: bytes, keyword: str,
) -> Optional[_DefClause]:
    """Read a def call; None when it defines no symbol.

    No symbol inside ``quote`` (INV-sinah: a template for the module that runs
    ``use``), for a head that is not a name (``def unquote(n)(a)``), or inside a
    protocol / impl whose module cannot be read: no symbol beats a bare or
    mis-owned one. A ``defimpl P, for: [A, B]`` gives one key per impl module.
    The ONE reading shared by Pass 1 (which folds clauses by key) and Pass 2
    (which maps every clause back to its function, ``_clause_positions``).
    """
    if _has_quote_ancestor(node, source):
        return None
    func_name = _get_function_name(node, source)
    owners = _enclosing_module_names(node, source)
    if not func_name or owners is None:
        return None
    definer = _nearest_definer(node, source)
    in_protocol = (
        definer is not None and keyword == "def"
        and _definer_keyword(definer, source) == "defprotocol"
    )
    kind = _def_kind(keyword)
    params = _def_head_arguments(node)
    arity = len(params) if params is not None else None
    return _DefClause(func_name, [
        _FunctionKey(f"{owner}.{func_name}" if owner else func_name, kind, arity)
        for owner in owners or [""]
    ], in_protocol)


def _function_parameters(
    clauses: list["tree_sitter.Node"], source: bytes,
) -> Optional[list[dict[str, Any]]]:
    """``meta["parameters"]`` of a function folded from ``clauses``.

    Each position is named by the first clause that knows it (a bodiless head's
    plain names, when the function has one) and is defaulted when ANY clause
    defaults it: Elixir allows defaults on one clause only, the head or the
    first, and they apply to every clause, so this is the function's arity
    range (``overload_arity.arity_bounds``).
    """
    per_clause = [
        entries for entries in (_elixir_parameters(c, source) for c in clauses)
        if entries is not None
    ]
    if not per_clause:  # pragma: no cover - only via _elixir_parameters' defensive None
        return None
    return [
        parameter_entry(
            entry["name"],
            default=any(p[i]["default"] for p in per_clause if i < len(p)),
        )
        for i, entry in enumerate(per_clause[0])
    ]


def _function_symbol(
    key: _FunctionKey,
    func_name: str,
    clauses: list[tuple["tree_sitter.Node", str]],
    source: bytes,
    file_path: str,
    run_id: str,
) -> Symbol:
    """The ONE symbol of an Elixir function: every ``(node, keyword)`` clause
    of a name/arity in this file (WI-tusad, the Erlang shape).

    ``def`` / ``defp`` is a ``function``, ``defmacro`` / ``defmacrop`` a
    ``macro``; the ``p`` forms are ``private``. The name stays ``M.f`` (arity
    is in ``meta["parameters"]``): call resolution, the behaviour and protocol
    dispatch, the framework callback table, Phoenix route handlers and the
    catalogue all key on ``M.f``, and ``narrow_by_arity`` picks the arity.

    The span runs from the first clause's start to the last clause's end, and
    the signature is the first clause's. Clauses split by another definition
    are still one function (the compiler warns and compiles them as one), so
    such a span also covers what sits between them. Cyclomatic complexity is
    aggregated as the Erlang analyzer does: one entry path, plus each clause's
    own decision points.

    The stable_id is the ADR-0035 producer formula over the module scope chain
    (``qualified_name`` = ``M.f``) and the declared arity, through the shared
    ``overload_arity.declared_arity_stable_id``. Clause count, order and span
    are not inputs, so adding or deleting a clause, or a function of another
    arity, moves no id. Visibility is not an input either: Elixir has no
    decorators, and ``def`` -> ``defp`` is the same function.
    """
    nodes = [node for node, _keyword in clauses]
    first, last = nodes[0], nodes[-1]
    kind = key.kind
    span = Span(
        start_line=first.start_point[0] + 1,
        end_line=last.end_point[0] + 1,
        start_col=first.start_point[1],
        end_col=last.end_point[1],
    )
    parameters = _function_parameters(nodes, source)
    meta = None if parameters is None else {PARAMETERS_KEY: parameters}
    return Symbol(
        id=make_symbol_id(
            "elixir", file_path, span.start_line, span.end_line,
            key.full_name, kind,
        ),
        stable_id=declared_arity_stable_id(
            kind=kind, lang="elixir", path=file_path, name=func_name,
            qualified_name=key.full_name, arity=declared_arity(meta),
        ),
        name=key.full_name,
        kind=kind,
        language="elixir",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=_extract_elixir_signature(first, source),
        meta=meta,
        modifiers=["private"] if clauses[0][1].endswith("p") else [],
        # INV-loguk: homoiconic head-symbol CC, aggregated over the clauses;
        # LOC from span.
        cyclomatic_complexity=1 + sum(
            cc - 1 for cc in (
                compute_cyclomatic_complexity(node, "elixir") for node in nodes
            ) if cc is not None
        ),
        line_span=span.end_line - span.start_line + 1,
    )


def _clause_positions(
    tree: "tree_sitter.Tree", source: bytes, file_symbols: list[Symbol],
) -> SymbolsAt:
    """Where each def clause starts -> the function symbol it folded into.

    INV-mozas finds a call's enclosing function by the position of the def
    node a walk up reaches (``symbol_declared_by``). A folded function's span
    starts at its FIRST clause, so a call in a later clause would find no
    symbol and fall out to the module. This adds every clause's position,
    re-reading each def call with the same ``_def_clause`` Pass 1 folded by.
    In a multi-target ``defimpl`` the last target's copy wins, as the plain
    position index does.
    """
    by_key: dict[_FunctionKey, Symbol] = {}
    for sym in file_symbols:
        if sym.kind in ("function", "macro") and _CALLBACK_MODIFIER not in sym.modifiers:
            params = (sym.meta or {}).get(PARAMETERS_KEY)
            by_key[_FunctionKey(
                sym.name, sym.kind, len(params) if isinstance(params, list) else None,
            )] = sym
    index: SymbolsAt = {}
    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue
        target = find_child_by_type(node, "identifier")
        keyword = node_text(target, source) if target is not None else None
        if keyword not in _DEF_KEYWORDS:
            continue
        clause = _def_clause(node, source, keyword)
        if clause is None or clause.in_protocol:
            continue
        for key in clause.keys:
            function = by_key.get(key)
            if function is not None:
                index[(node.start_point[0] + 1, node.start_point[1])] = function
    return index


def _protocol_function_symbol(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    run_id: str,
    full_name: str,
) -> Symbol:
    """The declaration a ``def`` head inside ``defprotocol`` makes (WI-forof).

    Callback-shaped (kind ``function``), because it IS one: ``defprotocol``
    generates a ``@callback`` per head. Unlike a behaviour callback it is a
    call target, by qualified name: ``Size.size(x)`` is the protocol's
    dispatch function (``ElixirAnalyzer.register_symbol``).
    """
    params = _def_head_arguments(node)
    arity = len(params) if params is not None else 0
    return _callback_shaped_symbol(
        node, file_path, run_id, full_name, "function", arity,
        _extract_elixir_signature(node, source),
    )


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    protocols: Optional[set[str]] = None,
) -> tuple[list[Symbol], dict[str, Symbol], dict[str, list[Symbol]]]:
    """Extract symbols from a parsed Elixir tree.

    ``protocols``, when given, collects the full name of every ``defprotocol``
    in the file (``ElixirAnalyzer.register_symbol`` reads it).

    The clauses of one name/arity become ONE function symbol
    (``_function_symbol``), placed where its first clause is.

    Returns:
        Tuple of (symbols, symbol_by_name, symbols_by_name).
        symbol_by_name maps short names to the most recent symbol.
        symbols_by_name maps short names to every function (each arity).
    """
    # ``None`` holds a function's place until all of its clauses are seen.
    symbols: list[Optional[Symbol]] = []
    symbol_by_name: dict[str, Symbol] = {}
    symbols_by_name: dict[str, list[Symbol]] = {}
    clauses: dict[_FunctionKey, list[tuple["tree_sitter.Node", str]]] = {}
    pending: dict[int, tuple[_FunctionKey, str]] = {}

    for node in iter_tree(tree.root_node):
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            if target:
                target_name = node_text(target, source)

                if target_name in _MODULE_DEFINERS:
                    # ``defmodule`` / ``defprotocol`` / ``defimpl`` (WI-forof):
                    # one module symbol per name the call defines -- a
                    # ``defimpl P, for: [A, B]`` defines ``P.A`` and ``P.B``.
                    for full_name in _defined_module_names(node, source):
                        symbol = _module_symbol(node, file_path, run_id, full_name)
                        symbols.append(symbol)
                        symbol_by_name[full_name] = symbol
                        if target_name == "defprotocol" and protocols is not None:
                            protocols.add(full_name)

                elif target_name in _DEF_KEYWORDS:
                    # INV-sinah: a def/defmacro inside a `quote` (an `__using__`
                    # template) is injected into the module that runs `use`, not
                    # defined on the defining module — suppressed by
                    # ``_def_clause`` rather than mis-attributed.
                    clause = _def_clause(node, source, target_name)
                    if clause is None:
                        continue
                    for key in clause.keys:
                        if clause.in_protocol:
                            # A protocol's ``def`` head is its callback
                            # (``defprotocol`` generates ``@callback`` for it).
                            symbols.append(_protocol_function_symbol(
                                node, source, file_path, run_id, key.full_name,
                            ))
                            continue
                        # WI-tusad: a clause joins its name/arity's function;
                        # the symbol is built once every clause is known, in
                        # the place of the first.
                        if key not in clauses:
                            clauses[key] = []
                            pending[len(symbols)] = (key, clause.func_name)
                            symbols.append(None)
                        clauses[key].append((node, target_name))

                elif (target_name == "defstruct"
                      and not _has_quote_ancestor(node, source)
                      and _nearest_enclosing_module_resolvable(node, source)):
                    # Struct fields (WI-jusus). An Elixir struct IS its module
                    # (`%User{}`), so the field owner is the enclosing module. A
                    # defstruct inside a `quote` is a template for a DIFFERENT
                    # module (via `use`); one whose nearest enclosing module is
                    # atom-named or absent has no reliable owner — both excluded.
                    owner = _enclosing_module(node, source)
                    field_names = _extract_defstruct_field_names(node, source)
                    if field_names:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        seen_fields: set[str] = set()
                        for field_name in field_names:
                            # Dedup (invalid `[:a, :a]` would otherwise mint two
                            # colliding-id symbols).
                            if field_name in seen_fields:
                                continue
                            seen_fields.add(field_name)
                            full_name = f"{owner}.{field_name}"
                            # A field is a DATA anchor: appended to `symbols` (so
                            # it reaches output/search/centrality) but NOT written
                            # to symbol_by_name/symbols_by_name and skipped by
                            # register_symbol, so it never enters call resolution.
                            symbols.append(Symbol(
                                id=make_symbol_id(
                                    "elixir", file_path, start_line, end_line,
                                    full_name, "field",
                                ),
                                # WI-tusad: the ADR-0035 producer formula over
                                # the owning module's scope chain (a field
                                # takes no parameters).
                                stable_id=declared_arity_stable_id(
                                    kind="field", lang="elixir", path=file_path,
                                    name=field_name, qualified_name=full_name,
                                    arity=None,
                                ),
                                name=full_name,
                                kind="field",
                                language="elixir",
                                path=file_path,
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=node.start_point[1],
                                    end_col=node.end_point[1],
                                ),
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            ))

        elif node.type == "unary_operator":
            # WI-vitas: a ``@callback`` is a behaviour's required member. Like a
            # field it is appended to ``symbols`` only -- NOT to symbol_by_name /
            # symbols_by_name -- so it never becomes a local call target.
            callback = _callback_symbol(node, source, file_path, run_id)
            if callback is not None:
                symbols.append(callback)

    for position, (key, func_name) in pending.items():
        function = _function_symbol(
            key, func_name, clauses[key], source, file_path, run_id,
        )
        symbols[position] = function
        # By short name for local calls.
        symbol_by_name[func_name] = function
        if function.kind == "function":
            # Multi-arity index: every function with this short name.
            symbols_by_name.setdefault(func_name, []).append(function)

    return [s for s in symbols if s is not None], symbol_by_name, symbols_by_name


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Any],
    run_id: str,
    resolver: "NameResolver",
    alias_hints: dict[str, str] | None = None,
    local_symbols_multi: dict[str, list[Symbol]] | None = None,
    global_symbols_multi: dict[str, list[Symbol]] | None = None,
    imported_modules: set[str] | None = None,
    import_only_bindings: dict[str, str] | None = None,
    file_symbols: list[Symbol] | None = None,
) -> list[Edge]:
    """Extract call and import edges from a parsed Elixir tree.

    Args:
        alias_hints: Optional dict mapping short names to full module paths for disambiguation.
        import_only_bindings: Function name -> module, from the ``only:``
            lists of ``import`` directives (WI-jozap). Direct evidence of which
            module a bare call belongs to; outranks the default ownership
            table.
        file_symbols: Every symbol of this file (INV-mozas). The enclosing
            def is found by position among these; ``local_symbols`` keeps one
            symbol per short name and so cannot tell arities apart.
        imported_modules: Set of module names from ``import`` directives. When
            provided, bare cross-module calls are only resolved if the target
            function's module was imported. This prevents false edges from
            name collisions across unrelated modules.
    """
    if alias_hints is None:  # pragma: no cover - defensive default
        alias_hints = {}

    edges: list[Edge] = []
    file_id = make_file_id("elixir", file_path)
    file_anchor = file_anchor_symbol("elixir", file_path, PASS_ID, run_id)
    # Every symbol of this file, by position. ``file_symbols`` is the whole
    # list (TreeSitterAnalyzer.file_symbols); without it -- a direct call --
    # the name-keyed dict plus the multi-arity index is the best available.
    known = (
        file_symbols if file_symbols is not None else
        [*local_symbols.values(),
         *(s for syms in (local_symbols_multi or {}).values() for s in syms)]
    )
    # A folded function is found from ANY of its clauses (WI-tusad).
    symbols_at = {
        **_clause_positions(tree, source, known),
        **index_symbols_at(known, file_path),
    }

    for node in iter_tree(tree.root_node):
        if node.type == "call":
            target = find_child_by_type(node, "identifier")
            if target:
                target_name = node_text(target, source)

                # Detect use/import/alias directives
                if target_name == "use":
                    args = find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="use_directive",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

                elif target_name == "import":
                    args = find_child_by_type(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type == "alias":
                                module_name = node_text(child, source)
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"elixir:{module_name}:0-0:module:module",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                    evidence_type="import_directive",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                ))

                # Detect function calls within a function body
                elif (target_name not in (*_DEF_KEYWORDS, *_MODULE_DEFINERS)
                      and not _is_def_head(node, source)):
                    current_function = _get_enclosing_function(
                        node, source, symbols_at,
                    ) or _caller_outside_def(node, source, symbols_at, file_anchor)
                    if current_function is not None:
                        # Check the local file for every function with this
                        # name, then keep the ones the call's arity reaches
                        # (WI-rodiz). A name defined locally at another arity only
                        # is not a local call: it falls through below.
                        call_arity = _elixir_call_arity(node, source)
                        named_multi = local_symbols_multi.get(target_name) if local_symbols_multi else None
                        local_multi = (
                            narrow_by_arity(named_multi, call_arity)
                            if named_multi else None
                        )
                        if local_multi:
                            for callee in local_multi:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    meta={"call_construct": "function"},
                                ))
                        # Fallback: single-match local lookup (modules, macros)
                        elif not named_multi and target_name in local_symbols:  # pragma: no cover — defensive; multi index covers all symbols
                            callee = local_symbols[target_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function"},
                            ))
                        # WI-rakul: a bare ``name(...)`` whose module the
                        # file's ``import`` directives actually establish —
                        # surface an unresolved edge keyed to that module so
                        # downstream linkers / io-boundaries can match.
                        #
                        # WI-jozap: WHICH module is decided by
                        # ``_bare_call_source_module``, not by
                        # ``next(iter(imported_modules))``. That took an
                        # arbitrary member of a set, and Python randomises
                        # string hashing per process, so the same code over the
                        # same repository did not produce the same answer twice
                        # (101 edges and up to 25 nodes moved between seeds on
                        # phoenix-framework). Ordering the set would have fixed
                        # determinism and kept a wrong answer: measured over
                        # phoenix-framework and livebook, ALL 653 edges this
                        # branch emitted were Kernel auto-imports —
                        # ``Plug.Conn.inspect``, ``Ecto.Changeset.raise`` —
                        # which no ``import`` directive establishes at all.
                        elif (
                            target_name in _ELIXIR_STDLIB_FUNCTIONS
                            and imported_modules
                            and (source_module := _bare_call_source_module(
                                target_name, imported_modules,
                                import_only_bindings or {},
                            )) is not None
                        ):
                            ext_ref = ExternalRef(
                                lang="elixir",
                                module_path=source_module,
                                name=target_name,
                            )
                            dst_id = (
                                f"elixir:{source_module}:0-0:{target_name}:unresolved"
                            )
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call_direct",
                                is_resolved=False,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                meta={"call_construct": "function", "binding": "explicit_import"},
                                dst_ref=ext_ref,
                            ))
                        # Cross-file: multi-arity global lookup, then resolver.
                        # Skip for Kernel/stdlib names — bare ``inspect(x)`` is
                        # almost always Kernel.inspect, not a project function.
                        elif target_name not in _ELIXIR_STDLIB_FUNCTIONS:
                            global_multi = global_symbols_multi.get(target_name) if global_symbols_multi else None
                            if global_multi:
                                # Gate on imports: if imported_modules is
                                # available, only allow targets from imported
                                # modules. Bare calls in Elixir require an
                                # ``import`` directive for cross-module access.
                                if imported_modules is not None:
                                    global_multi = [
                                        s for s in global_multi
                                        if _symbol_module(s.name) in imported_modules
                                    ]
                                global_multi = narrow_by_arity(
                                    global_multi, call_arity,
                                )
                                for callee in global_multi:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        meta={"call_construct": "function"},
                                    ))
                            else:  # pragma: no cover — multi-index has same keys as resolver
                                # Use alias hints for disambiguation
                                path_hint = alias_hints.get(target_name)
                                lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                                if lookup_result.found and lookup_result.symbol is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        confidence=0.80 * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        meta={"call_construct": "function"},
                                    ))

            # Module-qualified calls: Helper.greet(), App.Services.UserService.find()
            # AST: call -> dot -> (alias, ".", identifier)
            else:
                dot_node = find_child_by_type(node, "dot")
                if dot_node:
                    _handle_dot_call(
                        node, dot_node, source, symbols_at,
                        global_symbols, resolver, alias_hints, edges, run_id,
                        file_anchor=file_anchor,
                    )

        # Pipe operator: ``data |> func`` (no parens) is a binary_operator
        # whose right child is an identifier, not a call.  When parens are
        # present (``data |> func()``), tree-sitter emits a call node that
        # the block above already handles.
        elif node.type == "binary_operator":
            children = node.children
            # binary_operator has [left, operator, right]
            if len(children) >= 3 and node_text(children[1], source) == "|>":
                rhs = children[2]
                if rhs.type == "identifier":
                    func_name = node_text(rhs, source)
                    current_function = _get_enclosing_function(
                        node, source, symbols_at,
                    ) or _caller_outside_def(node, source, symbols_at, file_anchor)
                    if current_function is not None:
                        # ``data |> func``: a call of func/1 (WI-rodiz).
                        local_multi = local_symbols_multi.get(func_name) if local_symbols_multi else None
                        if local_multi:
                            local_multi = narrow_by_arity(local_multi, 1)
                        if local_multi:
                            for callee in local_multi:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="ast_call",
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    meta={"call_construct": "pipe"},
                                ))
                        elif func_name not in _ELIXIR_STDLIB_FUNCTIONS:
                            global_multi = global_symbols_multi.get(func_name) if global_symbols_multi else None
                            if global_multi:
                                if imported_modules is not None:
                                    global_multi = [
                                        s for s in global_multi
                                        if _symbol_module(s.name) in imported_modules
                                    ]
                                for callee in narrow_by_arity(global_multi, 1):
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        meta={"call_construct": "pipe"},
                                    ))
                # Note: ``data |> Mod.func`` (module-qualified pipe) does NOT
                # produce a bare ``dot`` rhs — tree-sitter wraps it in a
                # ``call`` node, which iter_tree visits separately.  So this
                # branch is handled by the normal call/dot path above.

    return edges


def _expand_alias(module_name: str, alias_hints: dict[str, str]) -> str:
    """Resolve a written module against the file's ``alias`` directives.

    An alias binds the FIRST COMPONENT, not the whole name:
    ``alias Mix.Tasks.Phx.Gen`` makes ``Gen`` mean ``Mix.Tasks.Phx.Gen`` AND
    makes ``Gen.Context`` mean ``Mix.Tasks.Phx.Gen.Context``. Looking the
    whole written name up in the hint map answers the first case and misses
    the second, which on phoenix-framework is 29 real first-party calls
    (``Gen.Context.build``, ``Gen.Schema.*``, ``Gen.Notifier.*``).

    That miss was invisible while module ownership was a substring test --
    ``"Gen.Context."`` is a substring of ``Mix.Tasks.Phx.Gen.Context.build``,
    so the old gate opened and the resolver bound the call correctly by
    accident. Once ownership is an exact question, the expansion has to be
    right for the answer to be.
    """
    if module_name in alias_hints:
        return alias_hints[module_name]
    head, _, rest = module_name.partition(".")
    if rest and head in alias_hints:
        return f"{alias_hints[head]}.{rest}"
    return module_name


def _handle_dot_call(
    call_node: "tree_sitter.Node",
    dot_node: "tree_sitter.Node",
    source: bytes,
    symbols_at: SymbolsAt,
    global_symbols: dict[str, Any],
    resolver: "NameResolver",
    alias_hints: dict[str, str],
    edges: list[Edge],
    run_id: str,
    evidence_type: str = "ast_call",
    file_anchor: Optional[Symbol] = None,
) -> None:
    """Handle module-qualified calls like Helper.greet() or App.Module.func().

    Extracts the module alias and function name from a dot node, then resolves
    the function using the module-qualified name (e.g., "Helper.greet") against
    the global symbol registry.  The *evidence_type* parameter allows callers
    (such as the pipe-operator handler) to supply a more specific tag.
    """
    # Extract the receiver and function name from the dot node.
    #
    # WI-kigub: the receiver is an ``alias`` for an Elixir module
    # (``Logger.error``) but an ``atom`` for an Erlang/OTP one
    # (``:ets.insert``), and the atom form is the ONLY way Elixir can reach
    # OTP at all. Requiring an ``alias`` here returned before emitting
    # anything, so every OTP module a project touches -- ``:ets``, ``:file``,
    # ``:gen_tcp``, ``:crypto``, ``:httpc`` -- produced no call edge of any
    # kind. Shapes the grammar puts here that are still refused, deliberately:
    # a variable receiver (``mod.fun()``), a module attribute
    # (``@attr.thing()``), a quoted atom (``:"Elixir.Foo".bar()``) and an
    # anonymous call (``f.()``, which has no named callee at all).
    alias_node = find_child_by_type(dot_node, "alias")
    atom_node = find_child_by_type(dot_node, "atom")
    func_id_node = find_child_by_type(dot_node, "identifier")
    if func_id_node is None:
        return

    func_name = node_text(func_id_node, source)

    # Find the enclosing function (caller)
    current_function = _get_enclosing_function(
        call_node, source, symbols_at,
    ) or _caller_outside_def(call_node, source, symbols_at, file_anchor)
    if current_function is None:
        return

    if alias_node is None:
        # Nested rather than folded into one compound guard so the atom is
        # NARROWED here: `alias_node is None and atom_node is None` does not
        # tell mypy that reaching this branch means `atom_node` is a Node.
        if atom_node is None:
            return
        # An atom module is an OTP module BY CONSTRUCTION, so it goes straight
        # to the unresolved fallback and never touches the first-party
        # resolution branches below. That is a correctness requirement, not a
        # shortcut: those branches end in a ``any(module_dot in k ...)``
        # SUBSTRING test, and ``"ets."`` is a substring of any project key such
        # as ``Foo.Sockets.insert``, so routing atoms through them would bind
        # ``:ets.insert`` to a first-party function.
        #
        # The leading colon is STRIPPED. ``_module_matches`` is component-aware
        # and does no colon stripping, so a module slot of ``:ets`` matches the
        # catalogue's ``ets`` row not at all -- the edge would exist and reach
        # nothing. Stripping is also the cross-language parity answer: the
        # erlang analyzer, calling these same OTP modules, emits
        # ``erlang:ets:0-0:insert:function``.
        _emit_unresolved_dot_edge(
            call_node, current_function,
            node_text(atom_node, source).lstrip(":"), func_name,
            edges, run_id,
        )
        return

    module_name = node_text(alias_node, source)

    # Build the fully-qualified function name: Module.func_name
    qualified_name = f"{module_name}.{func_name}"

    # Try to resolve the qualified name in global symbols
    if qualified_name in global_symbols:
        callee = global_symbols[qualified_name]
        edges.append(Edge.create(
            src=current_function.id,
            dst=callee.id,
            edge_type="calls",
            line=call_node.start_point[0] + 1,
            evidence_type=evidence_type,
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run_id,
        ))
        return

    # Try with alias hints: if module_name is an alias, resolve to full path
    expanded_module = _expand_alias(module_name, alias_hints)
    if expanded_module != module_name:
        full_qualified = f"{expanded_module}.{func_name}"
        if full_qualified in global_symbols:
            callee = global_symbols[full_qualified]
            edges.append(Edge.create(
                src=current_function.id,
                dst=callee.id,
                edge_type="calls",
                line=call_node.start_point[0] + 1,
                evidence_type=evidence_type,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run_id,
            ))
            return

    # NO BARE-NAME RESOLUTION FOR A QUALIFIED CALL (WI-kafor).
    #
    # What stood here asked whether the project plausibly owned the written
    # module -- as a SUBSTRING test, `any(f"{module}." in k for k in
    # global_symbols)`, though the comment described a component test -- and
    # then let `resolver.lookup(func_name, path_hint=...)` bind by BARE NAME.
    # On phoenix-framework that produced 202 false edges: `Logger.error(...)`
    # landing on an `error/1` inside an EEx GENERATOR TEMPLATE, `Path.join(...)`
    # on a Phoenix Channel's `join/3` in another template, `Mix.raise(...)` on a
    # test controller's `raise/1`, and `Logger.info(...)` on a **defp**, which
    # Elixir does not permit to be called from another module at all.
    #
    # Tightening the gate to an EXACT module-ownership question was tried and
    # measured first. It is necessary and it is not sufficient: with the gate
    # exact, every edge the bare-name lookup still produced on the whole corpus
    # was STILL wrong -- `Phx.New.Umbrella.render()` to a template's `render`,
    # `Router.call()` to an unrelated test helper's `call`. The gate decides
    # WHICH modules may pass; the defect is the bare-name bind itself, so that
    # is what goes.
    #
    # A qualified call now resolves ONLY by name: `Module.func` directly, or
    # through the alias expansion above, both of which are exact. Anything else
    # falls to the unresolved edge below, which names the right module and lets
    # the catalogue and the linkers do their work -- and that is where the
    # recall came from: `Logger.error` / `Logger.info` reach the Logger
    # catalogue rows for the first time.
    #
    # KNOWN CONSEQUENCE, filed rather than special-cased: a function reached
    # only through `defdelegate` (`Phoenix.CodeReloader.sync/0`) registers no
    # symbol under its own module, so it now goes unresolved instead of binding
    # to the delegate target's module -- which was the wrong module anyway.
    # Fallback: create an unresolved edge for cross-module calls
    # This allows linkers to match across files/languages.
    # WI-rakul: when ``module_name`` is an alias (``alias String, as: S``
    # → ``S.upcase``), surface the underlying module in the dst so
    # downstream consumers see ``String`` not ``S``. Populate dst_ref
    # with the canonical (module, name).
    _emit_unresolved_dot_edge(
        call_node, current_function, expanded_module, func_name, edges, run_id,
    )


def _emit_unresolved_dot_edge(
    call_node: "tree_sitter.Node",
    current_function: Symbol,
    canonical_module: str,
    func_name: str,
    edges: list[Edge],
    run_id: str,
) -> None:
    """Emit the cross-module fallback edge for a dot call.

    Shared by the alias path (``Logger.error``, after alias expansion) and the
    atom path (``:ets.insert``, after colon stripping) so the two cannot drift
    apart in destination shape -- the id format, the ``ast_call_direct``
    evidence tag and the structured ``dst_ref`` are what the boundary engine
    and the linkers read, and they must be identical whichever receiver syntax
    the source used.
    """
    edges.append(Edge.create(
        src=current_function.id,
        dst=f"elixir:{canonical_module}:0-0:{func_name}:unresolved",
        edge_type="calls",
        line=call_node.start_point[0] + 1,
        evidence_type="ast_call_direct",
        is_resolved=False,
        origin=PASS_ID,
        origin_run_id=run_id,
        dst_ref=ExternalRef(
            lang="elixir", module_path=canonical_module, name=func_name,
        ),
    ))


class ElixirAnalyzer(TreeSitterAnalyzer):
    """Elixir language analyzer using tree-sitter-language-pack.

    Handles multi-clause functions (one symbol per name/arity, WI-tusad),
    Phoenix routes, OTP/Phoenix behaviour callbacks, and
    module-qualified calls with alias resolution.

    The multi-arity index is stored in global_symbols under a ``__multi__``
    key: every function of one name, each arity, for arity-narrowed resolution.
    """

    lang = "elixir"
    file_patterns: ClassVar[list[str]] = ["*.ex", "*.exs"]
    #: ``template -> (behaviours, used)`` its ``use`` templates declare,
    #: filled in Pass 1 (``_extract_using_index``) and reset per ``analyze()``.
    _using_index: _UsingIndex
    #: Full names of every ``defprotocol`` in the run, filled in Pass 1 and
    #: read by ``register_symbol`` (WI-forof); reset per ``analyze()``.
    _protocols: set[str]

    def __init__(self) -> None:
        super().__init__()
        self._using_index = {}
        self._protocols = set()
    language_pack_name = "elixir"
    create_file_symbols = False

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict[str, Any],
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups.
        Also builds a multi-arity index under ``__multi__`` key: one name,
        every arity (``f/1`` and ``f/2`` are two symbols named ``M.f``). The
        ``__multi__`` index keeps both short and qualified name keys because
        call sites use bare function names (e.g., ``handle_call``) to look up
        every arity of ``MyApp.Server.handle_call``.

        A ``defstruct`` ``field`` (WI-jusus) is skipped: it is a data anchor,
        never a call target, and its qualified name (``User.name``) would
        otherwise suffix-match a bare ``name(…)`` call and mint a wrong ``calls``
        edge. It still reaches output via ``analysis.symbols``.

        A ``@callback`` declaration (WI-vitas) is filed under
        ``global_symbols["__callbacks__"][<behaviour module>]`` and NOWHERE
        else: in ``__multi__`` its short name would make every bare ``area()``
        in the file, and every importer's, a call of a function that has no
        body; under its qualified name ``Shape.area()`` -- an undefined-function
        error in Elixir -- would resolve to it. Pass 2 reads the index to link
        each callback to its implementations.

        A protocol function head (WI-forof) is filed in the same index -- it is
        the protocol's callback -- and, because a protocol function IS
        callable, also under its qualified name (never its short one).
        """
        if symbol.kind == "field":
            return
        if _CALLBACK_MODIFIER in symbol.modifiers:
            callbacks: dict[str, list[Symbol]] = global_symbols.setdefault(
                _CALLBACKS_KEY, {},
            )
            module = _symbol_module(symbol.name)
            callbacks.setdefault(module, []).append(symbol)
            if module in self._protocols:
                # A protocol function (WI-forof) IS called -- ``Size.size(x)``
                # is the dispatch -- so it is also a call target, by its
                # qualified name only: a bare ``size(...)`` names a local
                # function, never the bodiless head.
                global_symbols[symbol.name] = symbol
                multi_q: dict[str, list[Symbol]] = global_symbols.setdefault(
                    "__multi__", {},
                )
                multi_q.setdefault(symbol.name, []).append(symbol)
            return
        global_symbols[symbol.name] = symbol
        short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
        multi: dict[str, list[Symbol]] = global_symbols.setdefault("__multi__", {})
        multi.setdefault(short_name, []).append(symbol)
        multi.setdefault(symbol.name, []).append(symbol)

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from an Elixir file."""
        # Stash run_id for extract_usage_contexts_from_file
        self._current_run_id = run.execution_id
        analysis = FileAnalysis()
        symbols, symbol_by_name, _symbols_by_name = _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id, protocols=self._protocols,
        )
        analysis.symbols = symbols
        analysis.symbol_by_name = symbol_by_name
        # WI-vitas: what this file's ``__using__`` templates declare on their
        # users, gathered in Pass 1 so a ``use`` in ANY file can read it.
        self._using_index.update(_extract_using_index(tree, source))
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract alias hints for disambiguation."""
        return _extract_alias_hints(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Any],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call, import, module-qualified call, and behaviour callback edges."""
        global_multi: dict[str, list[Symbol]] | None = global_symbols.get("__multi__")

        # Reconstruct local multi from global multi filtered to this file.
        # We filter by path (not by local_symbols IDs) because local_symbols
        # is a dict keyed by name, so a name defined at several arities
        # (handle_call/2, handle_call/3) has one entry. Filtering by path
        # captures every arity.
        local_symbols_multi: dict[str, list[Symbol]] | None = None
        if global_multi:
            local_symbols_multi = {}
            for name, sym_list in global_multi.items():
                local_matches = [s for s in sym_list if s.path == rel_path]
                if local_matches:
                    local_symbols_multi[name] = local_matches

        file_imported_modules = _extract_imported_modules(tree, source)
        file_only_bindings = _extract_import_only_bindings(tree, source)

        edges = _extract_edges_from_tree(
            tree, source, rel_path, local_symbols, global_symbols,
            run.execution_id, resolver,
            alias_hints=import_aliases,
            local_symbols_multi=local_symbols_multi,
            global_symbols_multi=global_multi,
            imported_modules=file_imported_modules,
            import_only_bindings=file_only_bindings,
            file_symbols=self.file_symbols(local_symbols),
        )

        # Behaviour edges: the framework callback table (use GenServer, ...)
        # and every behaviour's implements + callback dispatch (WI-vitas).
        callback_edges = _extract_behaviour_callbacks(
            tree, source, file_path, local_symbols, global_symbols,
            run.execution_id,
            file_symbols_multi=local_symbols_multi,
            global_symbols_multi=global_multi,
            alias_hints=import_aliases,
            using_index=self._using_index,
        )
        edges.extend(callback_edges)
        # Protocols: implements + protocol-function dispatch (WI-forof).
        edges.extend(_extract_protocol_edges(
            tree, source, self.file_symbols(local_symbols), global_symbols,
            global_multi or {}, run.execution_id,
        ))

        return edges

    def extract_usage_contexts_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract Phoenix route usage contexts and stash route symbols."""
        run_id = getattr(self, "_current_run_id", "")
        alias_hints = _extract_alias_hints(tree, source)
        usage_contexts, route_symbols = _extract_phoenix_routes(
            tree.root_node, source, file_path, symbol_by_name,
            # WI-zugob: NOT ``self.pass_id`` — that attribute defaults to ""
            # on TreeSitterAnalyzer (the effective id is resolved downstream as
            # ``self.pass_id or make_pass_id(self.lang)``), so threading it here
            # stamped every Phoenix route marker with an empty Symbol.origin.
            run_id, PASS_ID,
            alias_hints=alias_hints,
        )
        # Stash route symbols to be added in post_process
        self._pending_route_symbols.extend(route_symbols)
        return usage_contexts

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Add stashed route symbols to the final result."""
        symbols.extend(self._pending_route_symbols)
        return symbols, edges, usage_contexts

    def analyze(
        self,
        repo_root: Path,
        max_files: int | None = None,
    ) -> AnalysisResult:
        """Override to initialize pending route symbols before analysis."""
        self._pending_route_symbols: list[Symbol] = []
        self._using_index = {}
        self._protocols = set()
        self._current_run_id = ""
        return super().analyze(repo_root, max_files=max_files)


_analyzer = ElixirAnalyzer()


def is_elixir_tree_sitter_available() -> bool:
    """Check if tree-sitter with Elixir grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("elixir")
def analyze_elixir(repo_root: Path) -> AnalysisResult:
    """Analyze all Elixir files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-elixir is not available, returns a skipped result.

    A non-skipped result carries the repo's Mix ``DependencyManifest``
    (``mix_deps.parse_mix_dependencies``: mix.exs deps direct, mix.lock
    transitive) so Elixir boundary nodes get a ``directness`` stamp
    (WI-juzaj). None when the repo has no Mix project.
    """
    result = _analyzer.analyze(repo_root)
    if not result.skipped:
        from hypergumbo_lang_common.mix_deps import parse_mix_dependencies

        manifest = parse_mix_dependencies(repo_root)
        result.dependency_manifest = manifest if manifest.scoped else None
    return result
