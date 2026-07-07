# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer-side axis-coherence linter for Edge / Symbol constructors.

Phase 1 of the axis-declaration ADRs (ADR-0023 / ADR-0027 / ADR-0028)
ships an L1 consumer-side drift linter that catches hardcoded
``frozenset({...})`` / ``set`` literals at module level whose values
escape their canonical registry. That gate alone leaves the **L3
producer-side** path open: any new linker can ship
``Edge.create(evidence_type="my_brand_new_label")`` and the consumer-
side AST walker never sees the new value (it's a function-call argument,
not a module-level set assignment).

This module closes the L3 gap. It walks ``Edge.create(...)`` /
``Edge(...)`` / ``Symbol.create(...)`` / ``Symbol(...)`` call sites
across the package source tree and verifies that **literal-string**
keyword arguments to axis-bearing parameters are in the corresponding
canonical registry. F-string emits and module-constant references are
classified as **advisories** (Phase-3 fold candidates per the parent
ADR), not strict failures — they're the existing producer-side leak
shape that Phase 3's per-cluster migration normalizes.

The check is field-agnostic: callers parameterize it with the
``constructor_names`` to match (e.g. ``{"Edge", "Edge.create"}``), the
``keyword_arg`` to inspect (``"evidence_type"``, ``"kind"``,
``"edge_type"``), and the ``registry_names`` frozenset. The wrapper at
``scripts/check-producer-axis-coherence`` invokes it for all three
sibling axes uniformly.

Why this lives next to ``axis_drift.py``: the two halves of axis
enforcement (consumer-side L1, producer-side L3) share the same AST-
walking pattern and registry-discipline philosophy, so colocating them
in the same package keeps the field-agnostic infrastructure together.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Iterator, Literal


FStringMode = Literal["advisory", "expand", "strict"]
"""How the linter treats f-string producer call sites.

- ``"advisory"`` (default for the base function) — surface every f-string
  emit as an advisory, never as a strict violation. The current behaviour
  before WI-nubuv ext B.
- ``"expand"`` — try to expand each f-string into a candidate frozenset by
  resolving every ``FormattedValue`` segment through the existing
  function-local Name walker. If expansion succeeds, classify the
  candidates exactly like a literal/literals branch (silent if all are
  registry members, strict per offending value otherwise). If any segment
  can't be statically resolved, fall back to the advisory path.
- ``"strict"`` — same expansion attempt, but unexpandable f-strings are
  promoted to strict violations rather than advisories. Use for axes where
  every producer should be either a literal kwarg or an enumerable
  combination.
"""


VariableFormMode = Literal["silent", "advisory", "strict"]
"""How the linter treats unresolvable Name / call-result kwargs (ext C).

- ``"silent"`` (default) — current behaviour: a kwarg whose value can't
  be statically resolved (function parameter, for-loop unpack target,
  function-call return, dict-subscript lookup, etc.) is silently skipped
  on the assumption that a runtime-coherence check will catch it later.
- ``"advisory"`` — same as silent for gating purposes, but the unresolved
  site is surfaced as an advisory so reviewers can audit producer-side
  hygiene without blocking the commit.
- ``"strict"`` — every unresolvable Name promotes to a strict violation.
  Banning the variable-form structurally per ADR-0028 §"Phase 1" eliminates
  the four blind-spot shapes catalogued in WI-nubuv (literal-kwarg,
  assignment-form-to-Name, f-string, dict-subscript-target). Opt-in
  per-axis; the wrappers in this module default to ``"silent"`` because
  the live tree still has legitimate dynamic emits (for-loop unpacks,
  dict-derived values) that would need refactor before strict can land
  axis-wide.
"""


DEFAULT_SEARCH_ROOTS: Final[tuple[str, ...]] = (
    "packages",
    "scripts",
    ".agent",
)
"""Default repo-relative directories scanned for producer call sites.

Mirrors :data:`hypergumbo_core.axis_drift.DEFAULT_SEARCH_ROOTS` so the
two enforcement passes scan the same surface.
"""

DEFAULT_EXCLUDED_PATH_SUBSTRINGS: Final[tuple[str, ...]] = (
    "/tests/",
)
"""Path-substring filters: any matching path is silently skipped.

Test files legitimately construct ``Edge.create(...)`` /
``Symbol.create(...)`` with synthetic values to exercise edge cases. The
producer-side discipline applies to production producers only.
"""


@dataclass(frozen=True)
class ProducerCoherenceResult:
    """Outcome of a single producer-coherence scan.

    Attributes:
        strict_violations: file:line entries where a literal-string
            keyword argument is not in the registry. These FAIL the
            check (exit 1).
        advisory_dynamic_emits: file:line entries where the keyword
            argument is an f-string or unresolvable variable. These do
            NOT fail the check; they are surfaced for Phase 3 fold
            attention.
    """

    strict_violations: tuple[str, ...]
    advisory_dynamic_emits: tuple[str, ...]


def _resolve_module_constant(
    name: str, tree: ast.Module,
) -> str | None:
    """Resolve a module-level ``NAME = "literal"`` assignment in *tree*.

    Returns the literal string if found and unambiguous, else ``None``.
    Only considers top-level (module-body) assignments; class- and
    function-scoped assignments are out of scope.
    """
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name,
            ):
                target_name = node.targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name,
        ):
            target_name = node.target.id
            value = node.value
        if target_name == name and value is not None:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


_FuncScope = ast.FunctionDef | ast.AsyncFunctionDef


def _iter_same_scope_assignments(node: ast.AST) -> Iterator[ast.Assign | ast.AnnAssign]:
    """Yield Assign/AnnAssign nodes in *node*'s lexical scope.

    Descends through compound control-flow statements (``if``, ``for``,
    ``while``, ``try``, ``with``) but stops at nested function, class,
    and lambda boundaries — those introduce new scopes and their
    assignments do not bind the same name in the parent scope.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (
            ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
        )):
            continue
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            yield child
        yield from _iter_same_scope_assignments(child)


def _resolve_simple_rhs(value: ast.expr) -> frozenset[str] | None:
    """Try to resolve a RHS expression to a frozenset of string literals.

    Recognized shapes (extension A scope, refined by WI-nubuv):

    - ``"literal"`` — single string constant.
    - Non-string ``ast.Constant`` (``None``, integer, bool, …) — returns
      an **empty frozenset**, NOT ``None``. The assignment binds a
      non-string value to the name, so it contributes no string
      candidate, but it must not poison the walker the way a truly
      unresolvable RHS does. This fix lets ``edge_type = None`` (the
      sentinel pre-assignment before real string assignments at e.g.
      ``linkers/inheritance.py:209``) coexist with later
      ``edge_type = "implements" / "extends"`` assignments without the
      None sentinel masking the resolvable string set.
    - ``"a" if cond else "b"`` — ternary with both branches resolvable
      (recursively, so nested ternaries also work). Now also handles
      inline ternaries at the kwarg site itself via :func:`_classify_value`.

    Returns ``None`` for any other shape (function call, arithmetic,
    Name reference, dict subscript, etc.). Conservative on purpose: a
    later extension B/C can broaden this without changing the contract.
    """
    if isinstance(value, ast.Constant):
        if isinstance(value.value, str):
            return frozenset({value.value})
        return frozenset()
    if isinstance(value, ast.IfExp):
        body_set = _resolve_simple_rhs(value.body)
        else_set = _resolve_simple_rhs(value.orelse)
        if body_set is None or else_set is None:
            return None
        return body_set | else_set
    return None


def _resolve_function_local(
    name: str, func_node: _FuncScope,
) -> frozenset[str] | None:
    """Resolve a function-local name to its candidate literal values.

    Walks every Assign / AnnAssign in *func_node*'s scope (excluding
    nested function and class bodies) that targets *name*. If every
    such assignment's RHS resolves via :func:`_resolve_simple_rhs`,
    returns the union of all candidate literals. Otherwise — or if no
    matching assignment exists — returns ``None``, signalling the
    caller to keep its existing silent-skip behaviour.

    The conservative posture (any unresolvable assignment poisons the
    whole resolution) keeps false positives out of the L3 gate: a
    variable that might be reassigned to anything at runtime cannot
    be statically gated.
    """
    candidates: set[str] = set()
    found_target = False
    for assign in _iter_same_scope_assignments(func_node):
        if isinstance(assign, ast.Assign):
            targets: list[ast.expr] = list(assign.targets)
            value: ast.expr = assign.value
        else:  # ast.AnnAssign
            # ``label: str`` (no RHS) is a type-annotation declaration
            # that creates no binding. Skip — only ``label: str = "x"``
            # contributes a candidate value.
            if assign.value is None:
                continue
            targets = [assign.target]
            value = assign.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                found_target = True
                resolved = _resolve_simple_rhs(value)
                if resolved is None:
                    return None
                candidates |= resolved
    if not found_target:
        return None
    return frozenset(candidates)


def _matches_constructor(call_node: ast.Call, constructor_names: frozenset[str]) -> bool:
    """Return True iff *call_node*'s callable matches any name in *constructor_names*."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id in constructor_names
    if isinstance(func, ast.Attribute):
        # Match on the bare attribute name (e.g. ``create`` for
        # ``Edge.create``), and on the dotted ``obj.attr`` form for
        # callers that wrote ``Edge.create``.
        if func.attr in constructor_names:
            return True
        if isinstance(func.value, ast.Name):
            dotted = f"{func.value.id}.{func.attr}"
            if dotted in constructor_names:
                return True
    return False


def _find_keyword(call_node: ast.Call, keyword_arg: str) -> ast.keyword | None:
    """Return the ``keyword`` node for *keyword_arg* on *call_node*, or None."""
    for kw in call_node.keywords:
        if kw.arg == keyword_arg:
            return kw
    return None


_FSTRING_EXPANSION_CAP: Final[int] = 32
"""Maximum number of candidate strings :func:`_expand_fstring` will materialize.

Guards against combinatorial explosion when an f-string has multiple
FormattedValue segments whose Name resolutions produce large sets. If
the Cartesian product would exceed this cap mid-expansion, expansion
returns ``None`` (treated as unexpandable for the chosen f-string mode).
"""


def _fstring_literal_prefix(value: ast.JoinedStr) -> str:
    """Return the contiguous literal-prefix portion of an f-string.

    Stops at the first non-Constant segment so the prefix reflects the
    static portion of the format spec. Used both by the advisory branch
    (so reviewers can see which Phase-3 cluster the site belongs to) and
    by the strict-mode unexpandable-violation message.
    """
    prefix_parts: list[str] = []
    for v in value.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            prefix_parts.append(v.value)
        else:
            break
    return "".join(prefix_parts)


def _expand_fstring(
    value: ast.JoinedStr, func_scope: _FuncScope | None,
) -> frozenset[str] | None:
    """WI-nubuv ext B: try to expand an f-string to candidate strings.

    Walks ``value.values`` (alternating ``ast.Constant`` literal segments
    and ``ast.FormattedValue`` interpolation segments) and tries to
    resolve every FormattedValue's inner expression via
    :func:`_resolve_function_local` (Name) or :func:`_resolve_simple_rhs`
    (inline ternary / non-string-constant). The Cartesian product of all
    segment candidate sets is the expansion result.

    Returns ``None`` if:

    - Any FormattedValue's inner expression is not a Name with a
      function-local resolution to a non-empty set of string literals.
    - The combinatorial product would exceed :data:`_FSTRING_EXPANSION_CAP`.
    - A FormattedValue carries a format-spec (``f"{x:>5}"``) or
      conversion (``f"{x!r}"``) — those mutate the string in ways the
      walker does not model.

    Callers handle ``None`` based on their :class:`FStringMode` choice:
    advisory path falls through to the existing advisory advisory branch;
    expand path falls back to advisory; strict path promotes to a strict
    violation flagged as ``"fstring_unexpandable"``.
    """
    parts: list[frozenset[str]] = []
    for seg in value.values:
        if isinstance(seg, ast.Constant) and isinstance(seg.value, str):
            parts.append(frozenset({seg.value}))
            continue
        if isinstance(seg, ast.FormattedValue):
            if seg.format_spec is not None or seg.conversion != -1:
                return None
            inner = seg.value
            resolved: frozenset[str] | None = None
            if isinstance(inner, ast.Name) and func_scope is not None:
                resolved = _resolve_function_local(inner.id, func_scope)
            elif isinstance(inner, (ast.Constant, ast.IfExp)):
                resolved = _resolve_simple_rhs(inner)
            if resolved is None or not resolved:
                return None
            parts.append(resolved)
            continue
        return None  # pragma: no cover - defensive (only Constant/FormattedValue appear in JoinedStr.values)

    candidates: set[str] = {""}
    for part in parts:
        new_cands: set[str] = set()
        for c in candidates:
            for p in part:
                new_cands.add(c + p)
                if len(new_cands) > _FSTRING_EXPANSION_CAP:
                    return None
        candidates = new_cands
    return frozenset(candidates)


def _classify_value(
    value: ast.expr,
    tree: ast.Module,
    func_scope: _FuncScope | None,
    *,
    fstring_mode: FStringMode = "advisory",
) -> tuple[str, str | frozenset[str] | None]:
    """Classify a keyword argument's value expression.

    Returns ``(category, payload)`` where category is one of:

    - ``"literal"`` — payload is the resolved literal string.
    - ``"literals"`` — payload is a frozenset of candidate literal
      strings (WI-nubuv ext A: function-local single-literal,
      ternary, and if/else assignment chains; ext B: f-string
      expansion in ``"expand"`` / ``"strict"`` mode).
    - ``"fstring"`` — payload is the literal-prefix portion of the
      f-string when one exists, else ``""`` (advisory mode, or
      expand-mode fallback when expansion fails).
    - ``"fstring_unexpandable"`` — payload is the literal prefix.
      ONLY emitted in ``fstring_mode="strict"`` when expansion fails.
    - ``"unresolvable"`` — payload is a short description (e.g.
      ``"Name(other_var)"``) for the advisory.

    Resolution order for ``ast.Name``: function-local assignments
    first (Python LEGB scoping), then module-level constants. This
    matches Python's runtime behaviour — a function-local rebinding
    shadows the module-level constant.

    WI-nubuv refinements:

    - Inline ``ast.IfExp`` ternaries (``kind="a" if cond else "b"``) at
      the kwarg site itself now resolve via :func:`_resolve_simple_rhs`,
      not just when bound to a function-local name.
    - F-string handling forks on *fstring_mode* (see :class:`FStringMode`).
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return ("literal", value.value)
    if isinstance(value, ast.JoinedStr):
        if fstring_mode in ("expand", "strict"):
            expanded = _expand_fstring(value, func_scope)
            if expanded is not None:
                if len(expanded) == 1:
                    return ("literal", next(iter(expanded)))
                return ("literals", expanded)
            if fstring_mode == "strict":
                return ("fstring_unexpandable", _fstring_literal_prefix(value))
        return ("fstring", _fstring_literal_prefix(value))
    if isinstance(value, ast.IfExp):
        resolved = _resolve_simple_rhs(value)
        if resolved is not None and resolved:
            if len(resolved) == 1:
                return ("literal", next(iter(resolved)))
            return ("literals", resolved)
        return ("unresolvable", "IfExp")
    if isinstance(value, ast.Name):
        if func_scope is not None:
            local = _resolve_function_local(value.id, func_scope)
            if local is not None and local:
                if len(local) == 1:
                    return ("literal", next(iter(local)))
                return ("literals", local)
        resolved = _resolve_module_constant(value.id, tree)
        if resolved is not None:
            return ("literal", resolved)
        return ("unresolvable", f"Name({value.id})")
    return ("unresolvable", type(value).__name__)


def _walk_calls_with_scope(
    node: ast.AST,
    func_scope: _FuncScope | None,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
) -> Iterator[tuple[ast.Call, _FuncScope | None]]:
    """Recursively yield matching Call nodes paired with their enclosing function.

    Tracks the innermost enclosing FunctionDef / AsyncFunctionDef as a
    scope handle for downstream :func:`_resolve_function_local`
    lookups. ClassDef bodies do NOT update the scope (their bare body
    is class-level, not a function); methods inside them re-enter
    function scope when their FunctionDef is visited.
    """
    if (
        isinstance(node, ast.Call)
        and _matches_constructor(node, constructor_names)
        and _find_keyword(node, keyword_arg) is not None
    ):
        yield node, func_scope

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        new_scope: _FuncScope | None = node
    else:
        new_scope = func_scope

    for child in ast.iter_child_nodes(node):
        yield from _walk_calls_with_scope(
            child, new_scope,
            constructor_names=constructor_names,
            keyword_arg=keyword_arg,
        )


def _iter_producer_call_sites(
    path: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
) -> Iterator[tuple[int, ast.expr, ast.Module, _FuncScope | None]]:
    """Yield ``(lineno, value_node, module_tree, enclosing_func)`` per match.

    Walks every ``ast.Call`` node in *path*, surfaces the keyword's
    value expression, and tracks the innermost enclosing function
    scope so :func:`_classify_value` can resolve function-local name
    references. Files that fail to read or parse are silently skipped
    (best-effort, same posture as
    :func:`axis_drift.iter_axis_set_assignments`).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover
        return

    for call_node, func_scope in _walk_calls_with_scope(
        tree, None,
        constructor_names=constructor_names,
        keyword_arg=keyword_arg,
    ):
        kw = _find_keyword(call_node, keyword_arg)
        # _walk_calls_with_scope filtered to calls that have this
        # keyword, so kw is never None here.
        assert kw is not None  # pragma: no cover
        yield call_node.lineno, kw.value, tree, func_scope


# --- WI-zipis: transitive helper-sink descent + positional binding ---
#
# The direct path above only sees ``Symbol(kind="x")`` / ``Edge.create(
# evidence_type="x")`` with the axis value as a KEYWORD literal. Producers
# routinely wrap emission in a module-local helper and pass the axis value
# POSITIONALLY (the proto ``rpc``/``service`` shape):
#
#     def _make_proto_symbol(..., name, kind, ...):   # kind is param #6
#         return Symbol(id=..., name=name, kind=kind, ...)
#     _make_proto_symbol(..., service_name, "service")
#
# Neither the keyword-only matcher (``kind`` here is a Name param, which
# classifies as ``unresolvable`` -> silent) nor the constructor-only walker
# (never descends into ``_make_proto_symbol``) ever sees ``"service"``, so
# the axis reports a false clean bill. The functions below discover, by a
# module-local fixpoint, every helper whose parameter flows into a known
# sink, then bind the positional/keyword argument at each helper call site.


def _positional_param_names(func: _FuncScope) -> list[str]:
    """Return *func*'s positionally-bindable parameter names, in order.

    ``posonlyargs`` then ``args`` — the slots a caller can fill by
    position. Keyword-only params are excluded (they can never be bound
    positionally, so they carry no positional index).
    """
    return [a.arg for a in func.args.posonlyargs] + [a.arg for a in func.args.args]


def _arg_for_sink(
    call: ast.Call, param_name: str, positional_index: int | None,
) -> ast.expr | None:
    """Return the argument expression *call* binds to a sink parameter.

    Prefers the keyword form (``param_name=<expr>``); falls back to the
    positional slot at *positional_index* when the callee's signature
    gives one. A ``*args`` splat anywhere at or before the positional
    slot makes index-based binding unsound, so that case returns
    ``None`` (skip) rather than mis-binding a later argument. ``None`` is
    also returned when neither form supplies the argument (default-valued
    param omitted at the call site).
    """
    for kw in call.keywords:
        if kw.arg == param_name:
            return kw.value
    if positional_index is not None:
        if any(
            isinstance(a, ast.Starred)
            for a in call.args[: positional_index + 1]
        ):
            return None
        if len(call.args) > positional_index:
            return call.args[positional_index]
    return None


def _call_sink_param(
    call: ast.Call,
    constructor_names: frozenset[str],
    sink_param: str,
    helper_sinks: dict[str, tuple[str, int]],
) -> tuple[str, int | None] | None:
    """Return the ``(param_name, positional_index)`` *call* targets, or None.

    A call is a sink if its callee is (a) a discovered helper sink — a
    bare ``Name`` in *helper_sinks* — or (b) a matched constructor
    (delegated to :func:`_matches_constructor`, which handles the bare
    and dotted forms). Constructors bind their axis value by keyword, so
    their positional index is ``None``; discovered helpers carry the
    index of their sink parameter.
    """
    func = call.func
    if isinstance(func, ast.Name):
        hs = helper_sinks.get(func.id)
        if hs is not None:
            return hs
    if _matches_constructor(call, constructor_names):
        return (sink_param, None)
    return None


def _discover_helper_sinks(
    tree: ast.Module,
    constructor_names: frozenset[str],
    sink_param: str,
) -> dict[str, tuple[str, int]]:
    """Fixpoint over the module's functions to find transitive sinks.

    A function ``F`` with parameter ``p`` is a *sink* for the
    ``(constructor_names, sink_param)`` axis if, somewhere in ``F``'s body,
    it passes ``p`` into a known sink's slot — a constructor's
    ``sink_param`` keyword, or an already-discovered helper's sink
    parameter (positionally or by keyword). Iterating to a fixpoint
    captures multi-hop indirection (``_outer`` -> ``_inner`` ->
    ``Symbol(kind=)``).

    Discovery walks **every** function def in the module (``ast.walk``),
    not just module-level ones, because the dominant emit-helper shape
    across analyzers is a **nested closure** — ``def make_symbol(...): ...
    Symbol(kind=kind)`` defined inside the analyzer's ``analyze(...)``
    function (thrift, ocaml, dart, haskell, solidity, and ~half a dozen
    more). A module-level-only scan is blind to all of them and silently
    undercounts. (Cross-*module* helper sinks do not exist for this axis:
    every Symbol-emitting helper is analyzer-local; the only cross-module
    ``kind``-taking helper, ``make_symbol_id``, builds the id string, not
    ``Symbol.kind``.) Sinks are keyed by function name; a same-name
    collision within one file keeps the last def — acceptable for a
    detection sweep that errs toward surfacing more.

    Returns ``{helper_name: (sink_param_name, positional_index)}``.
    Constructors are excluded from the result — their call sites are
    already covered by the direct path, so surfacing them here would
    double-report.
    """
    funcs: dict[str, _FuncScope] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node

    helper_sinks: dict[str, tuple[str, int]] = {}
    changed = True
    while changed:
        changed = False
        for fname, fdef in funcs.items():
            if fname in helper_sinks:
                continue
            pnames = _positional_param_names(fdef)
            for call in ast.walk(fdef):
                if not isinstance(call, ast.Call):
                    continue
                target = _call_sink_param(
                    call, constructor_names, sink_param, helper_sinks,
                )
                if target is None:
                    continue
                arg = _arg_for_sink(call, target[0], target[1])
                if isinstance(arg, ast.Name) and arg.id in pnames:
                    helper_sinks[fname] = (arg.id, pnames.index(arg.id))
                    changed = True
                    break
    return helper_sinks


def _walk_helper_calls(
    node: ast.AST,
    func_scope: _FuncScope | None,
    *,
    helper_sinks: dict[str, tuple[str, int]],
    tree: ast.Module,
) -> Iterator[tuple[int, ast.expr, ast.Module, _FuncScope | None]]:
    """Yield ``(lineno, sink_arg, tree, enclosing_func)`` per helper call.

    Mirrors :func:`_walk_calls_with_scope` but matches discovered helper
    sink functions (bare ``Name`` calls) and extracts the argument bound
    to each helper's sink parameter. Scope tracking lets
    :func:`_classify_value` resolve a caller-local Name passed into the
    helper.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        hs = helper_sinks.get(node.func.id)
        if hs is not None:
            arg = _arg_for_sink(node, hs[0], hs[1])
            if arg is not None:
                yield node.lineno, arg, tree, func_scope

    new_scope: _FuncScope | None = (
        node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else func_scope
    )
    for child in ast.iter_child_nodes(node):
        yield from _walk_helper_calls(
            child, new_scope, helper_sinks=helper_sinks, tree=tree,
        )


def _iter_helper_producer_call_sites(
    path: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
) -> Iterator[tuple[int, ast.expr, ast.Module, _FuncScope | None]]:
    """Yield helper-routed producer sites in *path* (WI-zipis descent).

    Parses the module, discovers its transitive helper sinks for the
    ``(constructor_names, keyword_arg)`` axis, and yields every call site
    that routes a value into one of them. Files that fail to read or
    parse are silently skipped, matching :func:`_iter_producer_call_sites`.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):  # pragma: no cover
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover
        return
    helper_sinks = _discover_helper_sinks(tree, constructor_names, keyword_arg)
    if not helper_sinks:
        return
    yield from _walk_helper_calls(
        tree, None, helper_sinks=helper_sinks, tree=tree,
    )


def _report_site(
    rel: Path | str,
    lineno: int,
    value_node: ast.expr,
    tree: ast.Module,
    func_scope: _FuncScope | None,
    *,
    keyword_arg: str,
    registry_names: frozenset[str],
    fstring_mode: FStringMode,
    variable_form_mode: VariableFormMode,
    strict: list[str],
    advisory: list[str],
) -> None:
    """Classify one producer site and append to *strict* / *advisory*.

    Shared by the direct-constructor loop and the WI-zipis helper-descent
    loop so both report identically.
    """
    category, payload = _classify_value(
        value_node, tree, func_scope, fstring_mode=fstring_mode,
    )
    if category == "literal":
        if payload not in registry_names:
            strict.append(
                f"{rel}:{lineno} ({keyword_arg}={payload!r}): "
                f"not in canonical registry"
            )
    elif category == "literals":
        assert isinstance(payload, frozenset)
        bad = sorted(v for v in payload if v not in registry_names)
        for v in bad:
            strict.append(
                f"{rel}:{lineno} ({keyword_arg}={v!r} via "
                f"function-local assignment or f-string "
                f"expansion): not in canonical registry"
            )
    elif category == "fstring":
        advisory.append(
            f"{rel}:{lineno} ({keyword_arg}=f-string"
            f"{f' prefix={payload!r}' if payload else ''}): "
            f"Phase-3 fold candidate"
        )
    elif category == "fstring_unexpandable":
        strict.append(
            f"{rel}:{lineno} ({keyword_arg}=f-string"
            f"{f' prefix={payload!r}' if payload else ''}): "
            f"unexpandable in fstring_mode='strict'"
        )
    elif category == "unresolvable":
        if variable_form_mode == "strict":
            strict.append(
                f"{rel}:{lineno} ({keyword_arg}=<{payload}>): "
                f"variable form banned in "
                f"variable_form_mode='strict'"
            )
        elif variable_form_mode == "advisory":
            advisory.append(
                f"{rel}:{lineno} ({keyword_arg}=<{payload}>): "
                f"variable-form producer (ext C advisory)"
            )
        # "silent" -> skip per docstring contract.


def find_producer_coherence_violations(
    repo_root: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
    registry_names: frozenset[str],
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
    fstring_mode: FStringMode = "advisory",
    variable_form_mode: VariableFormMode = "silent",
    descend_helpers: bool = False,
) -> ProducerCoherenceResult:
    """Scan producer call sites; return strict and advisory entries.

    For each ``ast.Call`` under each *search_roots* directory whose
    callable matches *constructor_names* and which carries *keyword_arg*
    as a keyword argument:

    - **Literal value not in registry** → strict violation. The PR
      cannot ship until the value is registered in the canonical axis
      list (or removed from the producer site).
    - **F-string value** → advisory (default), expanded-and-checked
      (``fstring_mode="expand"``), or strict-if-unexpandable
      (``fstring_mode="strict"``). See :class:`FStringMode`.
    - **Unresolvable name** (function param, for-loop unpack, dict
      lookup, function-call return, …) → silent by default. With
      ``variable_form_mode="advisory"`` or ``"strict"``, every
      unresolvable site surfaces as an advisory or strict violation
      respectively (ext C structural backstop).

    *excluded_path_substrings* defaults to ``("/tests/",)`` because
    test files legitimately construct synthetic axis values to exercise
    the dataclass edge-case paths.
    """
    excluded_tuple = tuple(excluded_path_substrings)
    strict: list[str] = []
    advisory: list[str] = []

    for root_name in search_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in root.rglob("*.py"):
            py_str = str(py_file)
            if any(sub in py_str for sub in excluded_tuple):
                continue
            try:
                rel: Path | str = py_file.relative_to(repo_root)
            except ValueError:  # pragma: no cover
                rel = py_file

            for lineno, value_node, tree, func_scope in _iter_producer_call_sites(
                py_file,
                constructor_names=constructor_names,
                keyword_arg=keyword_arg,
            ):
                _report_site(
                    rel, lineno, value_node, tree, func_scope,
                    keyword_arg=keyword_arg,
                    registry_names=registry_names,
                    fstring_mode=fstring_mode,
                    variable_form_mode=variable_form_mode,
                    strict=strict, advisory=advisory,
                )

            # WI-zipis: transitive helper-sink descent (positional +
            # keyword binding through module-local emission helpers). Opt-in
            # so the established direct-path gate is unchanged by default.
            if descend_helpers:
                for lineno, value_node, tree, func_scope in (
                    _iter_helper_producer_call_sites(
                        py_file,
                        constructor_names=constructor_names,
                        keyword_arg=keyword_arg,
                    )
                ):
                    _report_site(
                        rel, lineno, value_node, tree, func_scope,
                        keyword_arg=keyword_arg,
                        registry_names=registry_names,
                        fstring_mode=fstring_mode,
                        variable_form_mode=variable_form_mode,
                        strict=strict, advisory=advisory,
                    )

    return ProducerCoherenceResult(
        strict_violations=tuple(strict),
        advisory_dynamic_emits=tuple(advisory),
    )


def find_evidence_type_producer_violations(
    repo_root: Path,
    *,
    fstring_mode: FStringMode = "expand",
    variable_form_mode: VariableFormMode = "silent",
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Edge.evidence_type``.

    Defaults to ``fstring_mode="expand"`` per WI-nubuv ext B: the only
    live producer f-string for this axis is
    ``linkers/inheritance.py:258`` (``f"ast_{edge_type}"``), and the
    expansion via function-local ``edge_type`` resolution yields
    ``{ast_extends, ast_implements}`` — both canonical members of the
    AXIS_INFERENCE_PATHWAY registry. Expansion mode silently accepts the
    benign site without forcing every f-string axis-wide to strict.
    """
    from hypergumbo_core.evidence_types import all_evidence_type_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=all_evidence_type_names(),
        fstring_mode=fstring_mode,
        variable_form_mode=variable_form_mode,
    )


def find_symbol_kind_producer_violations(
    repo_root: Path,
    *,
    fstring_mode: FStringMode = "expand",
    variable_form_mode: VariableFormMode = "silent",
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Symbol.kind``. Defaults match
    :func:`find_evidence_type_producer_violations`."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Symbol", "Symbol.create"}),
        keyword_arg="kind",
        registry_names=all_symbol_kind_names(),
        fstring_mode=fstring_mode,
        variable_form_mode=variable_form_mode,
    )


def find_edge_type_producer_violations(
    repo_root: Path,
    *,
    fstring_mode: FStringMode = "expand",
    variable_form_mode: VariableFormMode = "silent",
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Edge.edge_type``. Defaults match
    :func:`find_evidence_type_producer_violations`."""
    from hypergumbo_core.edge_types import all_edge_type_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="edge_type",
        registry_names=all_edge_type_names(),
        fstring_mode=fstring_mode,
        variable_form_mode=variable_form_mode,
    )


def find_emitted_literal_values(
    repo_root: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
) -> dict[str, tuple[str, ...]]:
    """Collect every literal value emitted at producer call sites.

    Returns a mapping ``{literal_value: (file:line, ...)}`` indexing all
    sites under *search_roots* where a ``constructor_names`` call carries
    *keyword_arg* with a statically-resolvable literal string. The
    classification reuses :func:`_classify_value` so the emit set covers
    both the literal-kwarg shape and the assignment-form-to-Name shape
    (function-local single literal, ternary, if/else chain via
    :func:`_resolve_function_local`; module-level constants via
    :func:`_resolve_module_constant`).

    Distinct from :func:`find_producer_coherence_violations`: that function
    *gates* — given a registry, it returns the values that escape it. This
    function *enumerates* — it returns every emitted value regardless of
    registry membership. Audit-findings consumers (the
    DEPRECATE-NO-FOLD-zero-producer property test) need the enumeration
    so they can assert "no producer emits this value", which is the
    inverse of the gate predicate.

    **Coverage gap.** Four indirection shapes catalogued in the
    Fundamental Concept Audit playbook §"Step 4.5":

    - literal kwarg: covered.
    - assignment-form to Name: covered (extension A — function-local
      single literal, ternary, if/else chain).
    - f-string interpolation: covered when the f-string's
      FormattedValue segments resolve via extension A's walker
      (WI-nubuv ext B; uses ``fstring_mode="expand"``). Unexpandable
      f-strings still don't contribute literal candidates.
    - helper-call positional/kwarg (no — the walker only descends
      *constructor_names*, not arbitrary helpers like ``add_symbol``).
    - dict-subscript-target (no — ext C scope; banning the variable
      form structurally is the corresponding gate, not enumeration).

    Any DEPRECATE-NO-FOLD verdict that ships through one of the
    uncovered shapes will not be flagged by callers using this map; the
    playbook's per-value manual grep at audit-write time is the
    compensating control.
    """
    excluded_tuple = tuple(excluded_path_substrings)
    emit_sites: dict[str, list[str]] = {}

    for root_name in search_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in root.rglob("*.py"):
            py_str = str(py_file)
            if any(sub in py_str for sub in excluded_tuple):
                continue
            for lineno, value_node, tree, func_scope in _iter_producer_call_sites(
                py_file,
                constructor_names=constructor_names,
                keyword_arg=keyword_arg,
            ):
                try:
                    rel = py_file.relative_to(repo_root)
                except ValueError:  # pragma: no cover
                    rel = py_file
                category, payload = _classify_value(
                    value_node, tree, func_scope, fstring_mode="expand",
                )
                if category == "literal":
                    assert isinstance(payload, str)
                    emit_sites.setdefault(payload, []).append(f"{rel}:{lineno}")
                elif category == "literals":
                    assert isinstance(payload, frozenset)
                    for v in payload:
                        emit_sites.setdefault(v, []).append(f"{rel}:{lineno}")
                # fstring (advisory fallback) + unresolvable: do not
                # contribute literal values.

    return {k: tuple(v) for k, v in emit_sites.items()}


def find_emitted_symbol_kinds(repo_root: Path) -> dict[str, tuple[str, ...]]:
    """Enumerate ``Symbol.kind`` literal emit sites; see :func:`find_emitted_literal_values`."""
    return find_emitted_literal_values(
        repo_root,
        constructor_names=frozenset({"Symbol", "Symbol.create"}),
        keyword_arg="kind",
    )


def find_emitted_evidence_types(repo_root: Path) -> dict[str, tuple[str, ...]]:
    """Enumerate ``Edge.evidence_type`` literal emit sites; see :func:`find_emitted_literal_values`."""
    return find_emitted_literal_values(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
    )


def find_emitted_edge_types(repo_root: Path) -> dict[str, tuple[str, ...]]:
    """Enumerate ``Edge.edge_type`` literal emit sites; see :func:`find_emitted_literal_values`."""
    return find_emitted_literal_values(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="edge_type",
    )
