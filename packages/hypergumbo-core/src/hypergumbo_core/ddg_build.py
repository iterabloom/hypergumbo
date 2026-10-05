# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repo-level DDG construction: walk a repo, build a CFG per function, solve.

This is the orchestration layer above :mod:`hypergumbo_core.cfg`. ``cfg.py``
knows how to turn *one* function body into a control-flow graph and solve
reaching definitions; this module knows how to find every function in a
repository, do that to each of them, and aggregate the result into the
``(ddg_edges, ddg_symbols, hints_by_caller)`` triple that taint propagation
consumes.

Why This Module Exists
----------------------
The logic lived in ``cli.py`` as ``_build_python_ddg_for_verify_claims``,
which is two problems in one name. ``cli.py`` scopes itself to "argument
parsing and dispatching to command handlers", and a repo-walking analysis
pipeline is not that — it had grown to ~200 lines inside a 10,000-line
module. More importantly the name encoded a hardcoded *language* and a
single *consumer*, and both were load-bearing: the sole call site passed
the literal string ``"python"`` to ``populate_def_use_for_cfg``, so the
Rust and TypeScript def/use extractors — registered, tested, and covered —
were never invoked by any production path at all.

How It Works
------------
A :class:`LanguageDdgSpec` says how to find functions in one language:
which files to walk, which AST node types are function definitions, and
how to name one. Which files is, by default, every extension
``taxonomy.LANGUAGES`` declares for the language -- the same list discovery
classifies by -- and each file is parsed with the grammar
``taxonomy.grammar_for_path`` names for it, so ``.tsx`` is walked as
TypeScript but parsed with the JSX-capable ``tsx`` grammar (WI-fovus). Specs are registered into a process-global registry, the
same idiom ``cfg.register_def_use_extractor`` uses, because naming a Go
method requires a receiver-type helper that lives in the *language*
package while this module lives in core. Core defines the registry;
language packages register into it; the caller force-imports them.

``build_repo_ddg()`` then does the same thing for every requested
language: glob the files, parse, walk for function nodes, build the CFG,
populate def/use **with that language's extractor**, and solve.

Symbol Identity
---------------
Function symbol ids are minted with :func:`analyze.base.make_symbol_id`,
so they match what the language analyzer emitted for the same function and
``ddg_symbols`` lines up with the structural BFS's node keys. This is the
same string the old Python code hand-formatted, so Python ids are
unchanged by the move.

The match is approximate in one disclosed way, inherited from the original:
class context is not walked for Python, so a method is keyed as
``...:function`` rather than ``...:method``. Go *is* receiver-aware,
because its spec supplies a namer.

Callables Named by Their Binding (WI-mufag)
-------------------------------------------
Some callables carry no name of their own: ``const handler = (req) => {...}``,
``obj.f = function () {}``, an anonymous callback. The analyzer names each
after its BINDING, and in JavaScript it does so half a dozen ways
(``handler``, ``_cb_<callee>@<col>``, ``_iife``, ...). A spec lists those node
types in ``bound_callable_node_types``, and such a node is walked ONLY under
the id the analyzer emitted at exactly its span -- ``analyzer_symbols``,
passed in by the caller that already holds the survey. Re-deriving the names
here would be a second copy of the analyzer's naming that drifts; a node the
analyzer did not name is not walked. Before this, dash.js had 3,981 of 7,592
callable symbols the DDG never walked, and a flow inside one read
``structural`` -- a FALSE ``violated`` where the same body as a declaration
was refuted by the walk.

Bindings That Hold Callables (WI-rovun)
---------------------------------------
Go anchors every call under a package-level ``var`` on the VARIABLE
(INV-nopoh): ``var rootCmd = &cobra.Command{Run: func(cmd, args) {...}}``
is one ``variable`` symbol whose span is the ``var_spec``. That node is a
binding, not a callable -- it has no ``body`` field, and it may hold several
function literals plus initializer code that runs in no literal at all. A spec
says so with two fields: ``bound_anchor_kinds`` admits the analyzer's
``variable`` symbols into :func:`analyzer_symbol_index` (for that language
only), and ``bound_bodies_for`` names the literal bodies. Each body is walked
under the binding's id, and the results MERGE under that id.

The walk may not refute (``forfeit_refutation``) when the binding holds more
than one body or any call outside its bodies. Two literals under one anchor
are two functions to the program, so a value crossing between them is a
cross-function flow the intraprocedural walk cannot follow -- for two
declarations the taint pass never asks. A call outside the bodies (cobra's
``Args: cobra.ExactArgs(1)``, an immediately invoked literal's caller) runs in
no CFG, so an exhausted walk says nothing about it. Confirmation stays
available in both cases: a dependence the walk FINDS is real.

A plain data variable (``var logger = log.New(...)``) is indexed but holds no
body, so it is never walked: the index admits the kind, the hook decides.

Return Statements (INV-komoj)
-----------------------------
Taint follows a value a function RETURNS into that function's callers, and
deciding "this return carries the source's value" needs two facts per
function: where its return statements are, and how far each extends. The CFG
already records return statements, but it is the wrong place to read them:
the builder descends into a nested ``def`` / func literal / closure, so a
closure's ``return`` sits in its enclosing function's CFG, and a statement
carries only its start line. :func:`return_statement_spans` therefore reads the
AST, stopping at every node that opens a callable scope of its own, and
records ``(start line, end line)``. It runs for EVERY walked function, with or
without DDG edges: ``def get(): return os.getenv("K")`` defines nothing, has
no edges, and is the filed instance.

Refinement Hook
---------------
The WI-dilih receiver-hint refinement is genuinely Python-specific — it
reads Python import statements and parameter annotations. It hangs off the
Python spec as an optional callable rather than being hoisted into the
generic loop, so adding a language does not require having an equivalent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, cast

from .analyze.base import make_symbol_id
from .taxonomy import extension_globs, grammar_for_path

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cfg import DdgEdge

# Directories never worth walking. verify-claims should not pay to analyze
# third-party or generated code.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", ".tox", "__pycache__",
    ".ci", "node_modules", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", ".eggs",
})


@dataclass(frozen=True)
class LanguageDdgSpec:
    """How to find and name the functions of one language.

    Attributes:
        language: Language key. Selects the cfg mapping (``cfg_nodes/
            <language>.yaml``) AND the registered def/use extractor, which
            must agree — a spec whose extractor is missing yields CFGs
            with empty defines/uses and therefore zero DDG edges.
        function_node_types: AST node types that introduce a function
            scope. Each is expected to expose ``name`` and ``body`` fields.
        name_for: Optional callable ``(node, source) -> str | None``
            overriding the plain ``name`` field. Go uses this to prepend a
            receiver type so method ids match the analyzer's. ``None`` means
            the node carries no name and is skipped, as a missing ``name``
            field is by the default.
        kind_for: Optional callable ``(node) -> str`` choosing the id's
            kind slot; defaults to ``"function"``.
        refine: Optional callable invoked per function to derive extra
            hints; see the module docstring.
        bound_callable_node_types: AST node types the ANALYZER names after
            their binding (WI-mufag). Walked only under the analyzer's own id
            for the node's exact span; see "Callables Named by Their Binding".
        bound_bodies_for: Optional callable ``(node, source) -> [body, ...]``
            for a bound node that is a BINDING rather than a callable (WI-rovun):
            the bodies of the callables it holds, in source order. ``None``
            means the node is itself the callable and its ``body`` field is
            walked. See "Bindings That Hold Callables".
        bound_anchor_kinds: Symbol kinds, beyond the callable ones, that the
            analyzer anchors such a binding's calls on (Go: ``variable``).
            Admitted into :func:`analyzer_symbol_index` for this language only.
        file_globs: ``rglob`` patterns for source files, or ``None`` (the
            default) for the extensions ``taxonomy.LANGUAGES`` declares for
            ``language`` -- the list discovery classifies files by and the
            language's analyzer reads. A spec that sets it is NARROWING its
            language, and must be able to say why (WI-fovus): a private glob
            is how the JS/TS specs came to walk ``*.js`` / ``*.ts`` only.
    """

    language: str
    function_node_types: frozenset[str]
    name_for: Optional[Callable[[Any, bytes], Optional[str]]] = None
    kind_for: Optional[Callable[[Any], str]] = None
    refine: Optional[Callable[..., dict[tuple[int, str], str]]] = None
    bound_callable_node_types: frozenset[str] = frozenset()
    bound_bodies_for: Optional[Callable[[Any, bytes], list[Any]]] = None
    bound_anchor_kinds: frozenset[str] = frozenset()
    file_globs: Optional[tuple[str, ...]] = None

    def globs(self) -> tuple[str, ...]:
        """The ``rglob`` patterns this spec walks (see ``file_globs``)."""
        if self.file_globs is not None:
            return self.file_globs
        return tuple(extension_globs(self.language))


_CALLABLE_KINDS = frozenset({"function", "method", "getter", "setter"})

#: ``(language, repo-relative path, start line, start column, end line)`` of an
#: analyzer symbol -> its id. 1-based lines, 0-based column, as ``Span`` has them.
AnalyzerSymbolIndex = dict[tuple[str, str, int, int, int], str]


def analyzer_symbol_index(nodes: Sequence[dict[str, Any]]) -> AnalyzerSymbolIndex:
    """Index a survey's CALLABLE symbols by exact span, for the binding-named walk.

    A key two symbols share is dropped rather than resolved by order: with no
    way to tell them apart, walking either under the other's id would be a
    guess, and an unwalked callable keeps the structural arm it had.

    A non-callable kind enters only where its language's spec declares it in
    ``bound_anchor_kinds`` (WI-rovun), so a language whose analyzer emits a
    ``variable`` beside a callable at one span is not given a clash it never
    had. The registry is read at call time: the caller force-imports the
    language packages first, as it must for ``build_repo_ddg`` anyway.
    """
    index: AnalyzerSymbolIndex = {}
    clashes: set[tuple[str, str, int, int, int]] = set()
    for node in nodes:
        kind = node.get("kind")
        if kind not in _CALLABLE_KINDS:
            spec = _DDG_LANGUAGES.get(node.get("language", ""))
            if spec is None or kind not in spec.bound_anchor_kinds:
                continue
        span = node.get("span") or {}
        key = (
            node.get("language", ""), node.get("path", ""),
            span.get("start_line", 0), span.get("start_col", 0), span.get("end_line", 0),
        )
        if key in index:
            clashes.add(key)
        index[key] = node["id"]
    for key in clashes:
        del index[key]
    return index



@dataclass
class RepoDdg:
    """Aggregated DDG for a repository."""

    ddg_edges: list["DdgEdge"] = field(default_factory=list)
    ddg_symbols: set[str] = field(default_factory=set)
    hints_by_caller: dict[str, dict[tuple[int, str], str]] = field(default_factory=dict)
    #: ``symbol_id -> [(line, defines, uses), ...]`` for every statement the
    #: def/use pass annotated.
    #:
    #: WHY THE EDGE SET IS NOT ENOUGH (INV-sadah). A ``DdgEdge`` says "variable
    #: v defined at line D is used at line U". It does NOT say which variable
    #: defined at U inherited v — and when one line defines two variables, the
    #: §3a walk has to know. ``keep = str(server); path = name`` defines both
    #: ``keep`` and ``path`` at the same line, and only the first derives from
    #: the tainted ``server``; the edge set alone is equally consistent with
    #: either. Statement-level ``defines``/``uses`` resolves it, and the CFG
    #: already carries them — ``populate_def_use_for_cfg`` fills them in one
    #: line above where this is collected, and they were simply discarded.
    stmt_defuse: dict[str, list[tuple[int, tuple[str, ...], tuple[str, ...]]]] = field(
        default_factory=dict,
    )
    #: Symbol ids whose §3a walk may NOT return ``False`` (WI-joluk).
    #:
    #: A function lands here when the CFG's recorded statement extents do not
    #: cover every call node in its AST body — the def/use extractor
    #: demonstrably did not see part of it — or when the language declares no
    #: ``call_node_types`` and coverage is unknowable. Both are the same fact
    #: for this purpose: the walk's picture is incomplete, so an exhausted walk
    #: over it is not evidence the flow is absent.
    #:
    #: Populated for every function with DDG edges, INCLUDING the unknowable
    #: case, because the permitting case is the one being enumerated: a
    #: language nobody configured must forfeit rather than silently qualify.
    forfeit_refutation: set[str] = field(default_factory=set)
    #: ``symbol_id -> {variable, ...}`` the def/use extractor did not account
    #: for (INV-lupav clause L4).
    #:
    #: A name lands here when some statement the CFG RECORDED mentions it and
    #: lists it in neither ``defines`` nor ``uses`` — Go's grouped
    #: ``var ( msg = cwd )``, Python's ``c[key] = 1``. Distinct from
    #: ``forfeit_refutation`` in granularity and that is the point: coverage is
    #: a property of the FUNCTION (part of its body was never visited), while
    #: this is a property of a VARIABLE (this one value was mentioned somewhere
    #: the extractor did not read). Forfeiting the whole function for it would
    #: withhold ``False`` from every walk over the function instead of from the
    #: walks that actually carry the unaccounted value.
    #:
    #: Populated alongside ``stmt_defuse``, for functions WITH edges only.
    unaccounted_names: dict[str, frozenset[str]] = field(default_factory=dict)
    #: ``symbol_id -> [(start_line, end_line), ...]`` of the function's OWN
    #: return statements (INV-komoj); see "Return Statements". Populated for
    #: every walked function that has one, edges or not. A function with no
    #: entry has no return statement this walk recognises -- which for an
    #: expression-bodied callable (a Rust tail expression, a JS arrow
    #: ``() => x``) is NOT the same as returning nothing.
    return_spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


_DDG_LANGUAGES: dict[str, LanguageDdgSpec] = {}


def register_ddg_language(spec: LanguageDdgSpec) -> None:
    """Register a language spec for repo-level DDG construction."""
    _DDG_LANGUAGES[spec.language] = spec


def get_ddg_language(language: str) -> Optional[LanguageDdgSpec]:
    """Return the registered spec for a language, or None."""
    return _DDG_LANGUAGES.get(language)


def registered_ddg_languages() -> frozenset[str]:
    """Return the set of languages with a registered DDG spec."""
    return frozenset(_DDG_LANGUAGES)


def clear_ddg_languages() -> None:
    """Clear the registry (for tests)."""
    _DDG_LANGUAGES.clear()


def _function_name(node: Any, source: bytes, spec: LanguageDdgSpec) -> Optional[str]:
    """Resolve the display name for a function node."""
    if spec.name_for is not None:
        return spec.name_for(node, source)
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return source[name_node.start_byte:name_node.end_byte].decode(
        "utf-8", errors="replace",
    )


def _walk_functions(
    node: Any,
    source: bytes,
    spec: LanguageDdgSpec,
    rel_path: str,
    out: RepoDdg,
    deps: dict[str, Any],
    mapping: Any,
    refine_ctx: dict[str, Any],
    analyzer_symbols: Optional[AnalyzerSymbolIndex] = None,
) -> None:
    """Walk an AST collecting per-function DDG edges and refinement hints.

    ITERATIVE, NOT RECURSIVE, AND THAT IS THE WHOLE POINT (INV-gotir). This was
    a per-child recursion, so the Python stack depth WAS the tree-sitter AST
    depth — one frame per level — and CPython's default limit is 1000. Machine
    generated code exceeds that without being pathological: keda's
    ``vendor/go.temporal.io/api/workflowservice/v1/request_response.pb.go``
    (725,832 bytes of generated protobuf) measures an **AST depth of 1171**,
    because one ``const`` is a string built as ``"..." + "..." + "..."`` 1,165
    times and ``+`` is left-associative. ``verify-claims`` aborted with
    ``RecursionError`` and exited 1 with an empty stdout — and exit 1 is also
    what VIOLATED returns, so a CI gate could not tell a crash from a finding.

    THE TRANSFORMATION IS EXACTLY EQUIVALENT, and it is worth saying why rather
    than asserting it: there is no work after the child loop. The recursion
    carried no state a worklist cannot hold, no accumulator unwound on the way
    back up, and no post-order step. Children are pushed REVERSED so ``pop()``
    yields them left to right, preserving pre-order visit order — which matters
    because ``out`` accumulates in visit order and a consumer diffing two runs
    would otherwise see a spurious reordering.

    ``sys.setrecursionlimit`` was rejected: it trades a catchable
    ``RecursionError`` for a C-stack segfault, which is strictly worse for a
    failure mode already indistinguishable from a verdict.

    SCOPE. An AST sweep finds 72 self-recursive child-loop walks in this tree,
    so the shape is a class rather than one bug. A corpus depth census sizes
    the realized exposure narrowly — keda has 7 files at depth >= 900, all
    generated Go under ``vendor/``, while dash.js, caddy and mitmproxy top out
    at 74/37/79 — and ``survey`` over the same input does not crash. So the
    class is filed (INV-gotir) rather than rewritten wholesale, and this is the
    one walk with a measured failure.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in spec.function_node_types:
            name = _function_name(current, source, spec)
            body_node = current.child_by_field_name("body")
            if name is not None and body_node is not None:
                kind = spec.kind_for(current) if spec.kind_for else "function"
                sym_id = make_symbol_id(
                    spec.language, rel_path,
                    current.start_point[0] + 1, current.end_point[0] + 1,
                    name, kind,
                )
                _solve_one_function(
                    current, body_node, source, spec, sym_id, out, deps,
                    mapping, refine_ctx,
                )
        elif current.type in spec.bound_callable_node_types and analyzer_symbols:
            # WI-mufag: the analyzer's id at this exact span, or no walk.
            bound_id = analyzer_symbols.get((
                spec.language, rel_path, current.start_point[0] + 1,
                current.start_point[1], current.end_point[0] + 1,
            ))
            if bound_id is not None:
                _solve_bound_node(
                    current, source, spec, bound_id, out, deps, mapping,
                    refine_ctx,
                )
        stack.extend(reversed(current.children))


def _solve_bound_node(
    node: Any,
    source: bytes,
    spec: LanguageDdgSpec,
    bound_id: str,
    out: RepoDdg,
    deps: dict[str, Any],
    mapping: Any,
    refine_ctx: dict[str, Any],
) -> None:
    """Walk a bound node's body -- or, for a binding, each body it holds.

    See "Bindings That Hold Callables" for why a binding forfeits refutation
    when the walk cannot see all of it. A node that is itself the callable
    (no hook) is gated exactly as a declaration is, by its body's coverage.
    """
    if spec.bound_bodies_for is None:
        body_node = node.child_by_field_name("body")
        bodies = [] if body_node is None else [body_node]
    else:
        bodies = spec.bound_bodies_for(node, source)
    for body_node in bodies:
        _solve_one_function(
            node, body_node, source, spec, bound_id, out, deps, mapping,
            refine_ctx,
        )
    if (
        spec.bound_bodies_for is not None
        and bound_id in out.ddg_symbols
        and _binding_walk_incomplete(node, bodies, mapping)
    ):
        out.forfeit_refutation.add(bound_id)


def _binding_walk_incomplete(node: Any, bodies: list[Any], mapping: Any) -> bool:
    """True when a binding's walk cannot license a refutation (WI-rovun).

    More than one body, or a call anywhere in the binding outside every body.
    Also True when the language declares no ``call_node_types``: coverage
    outside the bodies is then unknowable, and unknowable forfeits -- the same
    default-deny as :func:`cfg.uncovered_semantic_lines`.
    """
    if len(bodies) > 1 or not mapping.call_node_types:
        return True
    call_types = frozenset(mapping.call_node_types)
    walked = {(b.start_byte, b.end_byte, b.type) for b in bodies}
    stack = [node]
    while stack:
        current = stack.pop()
        if (current.start_byte, current.end_byte, current.type) in walked:
            continue
        if current.type in call_types:
            return True
        stack.extend(current.children)
    return False


#: AST node types that open a callable scope of their own, across the grammars
#: with a registered DDG spec: a ``return`` under one of them returns from IT,
#: not from the function being walked. Each spec's own ``function_node_types``
#: and ``bound_callable_node_types`` are added at the call, so this set holds
#: the scopes no spec names -- anonymous callables and class bodies.
_NESTED_SCOPE_NODE_TYPES = frozenset({
    # python
    "function_definition", "lambda", "class_definition",
    # go
    "func_literal",
    # rust
    "closure_expression", "function_item", "impl_item", "trait_item",
    # javascript / typescript
    "arrow_function", "function_expression", "function", "generator_function",
    "function_declaration", "generator_function_declaration",
    "method_definition", "class", "class_declaration",
    # java
    "lambda_expression", "class_body", "method_declaration",
    "constructor_declaration",
})


def return_statement_spans(
    body_node: Any, mapping: Any, spec: LanguageDdgSpec,
) -> list[tuple[int, int]]:
    """``(start_line, end_line)`` of every return statement in THIS body.

    See "Return Statements" for why this reads the AST rather than the CFG.
    Iterative for the reason :func:`_walk_functions` is (INV-gotir). A return
    statement is not descended into: a closure inside ``return func() {..}``
    returns from the closure.
    """
    return_types = frozenset(mapping.return_statements)
    if not return_types:
        return []
    scopes = (
        _NESTED_SCOPE_NODE_TYPES
        | spec.function_node_types
        | spec.bound_callable_node_types
    )
    spans: list[tuple[int, int]] = []
    stack = list(reversed(body_node.children))
    while stack:
        node = stack.pop()
        if node.type in return_types:
            spans.append((node.start_point[0] + 1, node.end_point[0] + 1))
            continue
        if node.type in scopes:
            continue
        stack.extend(reversed(node.children))
    return spans


def _solve_one_function(
    node: Any,
    body_node: Any,
    source: bytes,
    spec: LanguageDdgSpec,
    sym_id: str,
    out: RepoDdg,
    deps: dict[str, Any],
    mapping: Any,
    refine_ctx: dict[str, Any],
) -> None:
    """Build one function's CFG, solve reaching defs, record the result."""
    # INV-komoj: before the solve, and whatever it yields -- the filed helper
    # has no definitions, so no edges, and is still what returns the value.
    spans = return_statement_spans(body_node, mapping, spec)
    if spans:
        out.return_spans.setdefault(sym_id, []).extend(spans)
    try:
        cfg = deps["build_function_cfg"](body_node, source, mapping, sym_id)
        deps["populate_def_use_for_cfg"](cfg, body_node, source, spec.language)
        result = deps["solve_reaching_defs"](cfg)
    except Exception:  # pragma: no cover - defensive; skip this function
        return
    if result.bailed_out:
        return
    if result.ddg_edges:
        out.ddg_edges.extend(result.ddg_edges)
        out.ddg_symbols.add(sym_id)
        # WI-joluk. Computed only for functions that HAVE edges: a function the
        # walk can never run on cannot forfeit anything, and adding it would
        # inflate the set with entries no consumer reads.
        uncovered = deps["uncovered_semantic_lines"](cfg, body_node, source, mapping)
        if uncovered is None or uncovered:
            out.forfeit_refutation.add(sym_id)
        # Collected only alongside edges: a function with no edges cannot be
        # walked, so its statements would be dead weight in the index.
        stmts = [
            (s.line, tuple(s.defines), tuple(s.uses))
            for block in cfg.blocks.values()
            for s in block.statements
            if s.defines or s.uses
        ]
        # EXTEND, never assign: a binding's bodies share one id (WI-rovun), and
        # an assignment kept only the last body's statements while every
        # body's edges stayed in ``ddg_edges``.
        if stmts:
            out.stmt_defuse.setdefault(sym_id, []).extend(stmts)
        # INV-lupav L4. Collected on the same terms as ``stmt_defuse`` and for
        # the same reason: a function with no edges is never walked, and the
        # empty answer this returns for an unpopulated CFG would be a lie about
        # one that was.
        unaccounted = deps["unaccounted_names"](cfg, body_node, source)
        if unaccounted:
            out.unaccounted_names[sym_id] = (
                out.unaccounted_names.get(sym_id, frozenset()) | unaccounted
            )
    if spec.refine is not None:
        hints = spec.refine(
            node=node,
            body_node=body_node,
            source=source,
            ddg_edges=result.ddg_edges,
            **refine_ctx,
        )
        if hints:
            out.hints_by_caller.setdefault(sym_id, {}).update(hints)


def build_repo_ddg(
    repo_root: Path,
    languages: Sequence[str] = ("python",),
    analyzer_symbols: Optional[AnalyzerSymbolIndex] = None,
) -> RepoDdg:
    """Build the aggregated DDG for a repository.

    Args:
        repo_root: Repository root to walk.
        languages: Language keys to process. Unregistered keys and
            languages with no cfg mapping are skipped silently — the
            caller falls back to structural taint propagation, which is
            the pre-existing behaviour when DDG data is unavailable.
        analyzer_symbols: The analyzer's callable symbols by exact span
            (:func:`analyzer_symbol_index`). Without it no binding-named
            callable is walked, which is the behaviour before WI-mufag.

    Returns:
        A :class:`RepoDdg`. Empty when tree-sitter is unavailable.
    """
    out = RepoDdg()
    try:
        import tree_sitter
        from tree_sitter_language_pack import get_language

        from .cfg import (
            build_function_cfg,
            load_cfg_mapping,
            populate_def_use_for_cfg,
            solve_reaching_defs,
            unaccounted_names,
            uncovered_semantic_lines,
        )
    except ImportError:  # pragma: no cover - tree-sitter is a hard dep but defend
        return out

    deps = {
        "build_function_cfg": build_function_cfg,
        "populate_def_use_for_cfg": populate_def_use_for_cfg,
        "solve_reaching_defs": solve_reaching_defs,
        "uncovered_semantic_lines": uncovered_semantic_lines,
        "unaccounted_names": unaccounted_names,
    }

    parsers = _ParserCache(tree_sitter, get_language)
    for language in languages:
        spec = _DDG_LANGUAGES.get(language)
        if spec is None:
            continue
        mapping = load_cfg_mapping(language)
        if mapping is None:  # pragma: no cover - shipped mappings always load
            continue
        _walk_language(
            repo_root, spec, parsers, mapping, out, deps, analyzer_symbols or {},
        )

    return out


class _ParserCache:
    """One tree-sitter parser per GRAMMAR, built on first use.

    A language's files need not share a grammar (WI-fovus): ``.tsx`` is
    TypeScript to the analyzer -- so its functions are walked under the
    ``typescript`` spec, mapping and extractor, and keep the analyzer's ids --
    but the ``typescript`` grammar does not parse JSX. ``taxonomy.grammar_for_path``
    names the dialect, exactly as the JS/TS analyzer's parser choice does.
    """

    def __init__(self, tree_sitter: Any, get_language: Callable[[Any], Any]) -> None:
        self._tree_sitter = tree_sitter
        self._get_language = get_language
        self._parsers: dict[str, Any] = {}

    def get(self, grammar: str) -> Any:
        """The parser for ``grammar``, or ``None`` when the pack lacks it."""
        if grammar not in self._parsers:
            try:
                # ``get_language``'s stub declares a Literal of every grammar
                # name the pack ships; ours is a runtime string. The cast
                # records that rather than widening the stub.
                ts_language = self._get_language(cast(Any, grammar))
            except Exception:
                logger.debug("no tree-sitter grammar %s; skipping its files", grammar)
                self._parsers[grammar] = None
            else:
                self._parsers[grammar] = self._tree_sitter.Parser(ts_language)
        return self._parsers[grammar]


def _spec_files(repo_root: Path, spec: LanguageDdgSpec) -> list[Path]:
    """Every file the spec's globs match, once each, in a stable order.

    Two globs can match one file (``*.ts`` and a caller's ``*.d.ts``), and
    ``rglob`` order is the filesystem's; sorting makes a walk's output order
    independent of both.
    """
    found: set[Path] = set()
    for pattern in spec.globs():
        for path in repo_root.rglob(pattern):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            found.add(path)
    return sorted(found)


def _walk_language(
    repo_root: Path,
    spec: LanguageDdgSpec,
    parsers: _ParserCache,
    mapping: Any,
    out: RepoDdg,
    deps: dict[str, Any],
    analyzer_symbols: AnalyzerSymbolIndex,
) -> None:
    """Walk every source file of one language under ``repo_root``."""
    for path in _spec_files(repo_root, spec):
        parser = parsers.get(grammar_for_path(path, spec.language))
        if parser is None:
            continue
        try:
            source = path.read_bytes()
        except OSError:  # pragma: no cover - defensive
            continue
        tree = parser.parse(source)
        rel_path = path.relative_to(repo_root).as_posix()
        refine_ctx = _refine_context(spec, tree, source)
        _walk_functions(
            tree.root_node, source, spec, rel_path, out, deps, mapping, refine_ctx,
            analyzer_symbols,
        )


def _refine_context(spec: LanguageDdgSpec, tree: Any, source: bytes) -> dict[str, Any]:
    """Build the per-file context the refinement hook needs, if any."""
    if spec.refine is None:
        return {}
    from .taint_refine import extract_python_imports

    module_imports, imports = extract_python_imports(tree.root_node, source)
    return {"module_imports": module_imports, "imports": imports}


def _python_refine(
    *,
    node: Any,
    body_node: Any,
    source: bytes,
    ddg_edges: list["DdgEdge"],
    module_imports: dict[str, str],
    imports: dict[str, tuple[str, str]],
) -> dict[tuple[int, str], str]:
    """WI-dilih receiver-hint refinement for Python.

    WI-dozon: parameter annotations are extracted even when the DDG is
    empty — a short helper like ``return name.replace(...)`` has no
    def-use edges, but the annotation is exactly the signal that pins the
    receiver type.
    """
    from .taint_refine import (
        extract_python_param_annotations,
        extract_python_receiver_hints,
    )

    param_anns = extract_python_param_annotations(node, source, module_imports, imports)
    if not param_anns and not ddg_edges:
        return {}
    return extract_python_receiver_hints(
        body_node, source, module_imports, imports, ddg_edges,
        param_annotations=param_anns,
    )


def _python_enclosing_scope(node: Any) -> Any:
    """The IMMEDIATELY enclosing ``class_definition`` / ``function_definition``.

    Walks past everything that is not itself a scope — ``block``, and
    ``decorated_definition`` for a decorated member — so a decorator does not
    hide the class a method belongs to.
    """
    parent = node.parent
    while parent is not None:
        if parent.type in ("class_definition", "function_definition"):
            return parent
        parent = parent.parent
    return None


def _python_named_scope_prefix(node: Any, source: bytes) -> str:
    """``<immediate scope name>.`` or ``""`` when there is no named scope."""
    scope = _python_enclosing_scope(node)
    if scope is None:
        return ""
    scope_name = scope.child_by_field_name("name")
    if scope_name is None:  # pragma: no cover - both scope types name a child
        return ""
    text = source[scope_name.start_byte:scope_name.end_byte].decode(
        "utf-8", errors="replace",
    )
    return f"{text}."


def _python_function_name(node: Any, source: bytes) -> str:
    """Name a python callable exactly as ``py.py`` names it (WI-ripas).

    py.py prefixes with the IMMEDIATE enclosing scope, one level, whether that
    scope is a class or a function — derived from the analyzer over all four
    shapes rather than assumed:

        top-level function      ``top``
        nested function         ``top.inner``
        method                  ``Outer.meth``
        method of nested class  ``Inner.deep``   (not ``Outer.Inner.deep``)

    Without this the default bare-``name`` fallback stored every method under a
    key the taint walk never constructs, so ``fn_has_ddg`` was false for 68.5%
    of python callables and their findings degraded to ``structural`` by
    construction. Nested FUNCTIONS were mis-keyed by the same fallback.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None:  # pragma: no cover - grammar always supplies a name
        return ""
    name = source[name_node.start_byte:name_node.end_byte].decode(
        "utf-8", errors="replace",
    )
    return _python_named_scope_prefix(node, source) + name


def _python_symbol_kind(node: Any) -> str:
    """``method`` exactly when the immediate enclosing scope is a class.

    A function nested in a function stays ``function`` — which is what py.py
    emits for ``top.inner`` — so this is not "is it nested" but "is its owner a
    class".
    """
    scope = _python_enclosing_scope(node)
    return "method" if scope is not None and scope.type == "class_definition" else "function"


# ``*.py`` only: LANGUAGES also lists ``*.pyi``, and whether walking stub files
# is wanted was not measured when the JS/TS specs moved to the shared list
# (WI-fovus) -- a declared narrowing, not an oversight.
register_ddg_language(LanguageDdgSpec(
    language="python",
    file_globs=("*.py",),
    function_node_types=frozenset({"function_definition"}),
    name_for=_python_function_name,
    kind_for=_python_symbol_kind,
    refine=_python_refine,
))
