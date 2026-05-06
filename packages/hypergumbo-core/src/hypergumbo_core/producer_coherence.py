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
from typing import Final, Iterable, Iterator


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

    Recognized shapes (extension A scope):

    - ``"literal"`` — single string constant.
    - ``"a" if cond else "b"`` — ternary with both branches resolvable
      to literals (recursively, so nested ternaries also work).

    Returns ``None`` for any other shape (function call, arithmetic,
    Name reference, dict subscript, etc.). Conservative on purpose: a
    later extension B/C can broaden this without changing the contract.
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return frozenset({value.value})
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


def _classify_value(
    value: ast.expr, tree: ast.Module, func_scope: _FuncScope | None,
) -> tuple[str, str | frozenset[str] | None]:
    """Classify a keyword argument's value expression.

    Returns ``(category, payload)`` where category is one of:

    - ``"literal"`` — payload is the resolved literal string.
    - ``"literals"`` — payload is a frozenset of candidate literal
      strings (WI-nubuv ext A: function-local single-literal,
      ternary, and if/else assignment chains).
    - ``"fstring"`` — payload is the literal-prefix portion of the
      f-string when one exists, else ``""``.
    - ``"unresolvable"`` — payload is a short description (e.g.
      ``"Name(other_var)"``) for the advisory.

    Resolution order for ``ast.Name``: function-local assignments
    first (Python LEGB scoping), then module-level constants. This
    matches Python's runtime behaviour — a function-local rebinding
    shadows the module-level constant.
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return ("literal", value.value)
    if isinstance(value, ast.JoinedStr):
        prefix_parts: list[str] = []
        for v in value.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                prefix_parts.append(v.value)
            else:
                break
        return ("fstring", "".join(prefix_parts))
    if isinstance(value, ast.Name):
        if func_scope is not None:
            local = _resolve_function_local(value.id, func_scope)
            if local is not None:
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


def find_producer_coherence_violations(
    repo_root: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
    registry_names: frozenset[str],
    search_roots: Iterable[str] = DEFAULT_SEARCH_ROOTS,
    excluded_path_substrings: Iterable[str] = DEFAULT_EXCLUDED_PATH_SUBSTRINGS,
) -> ProducerCoherenceResult:
    """Scan producer call sites; return strict and advisory entries.

    For each ``ast.Call`` under each *search_roots* directory whose
    callable matches *constructor_names* and which carries *keyword_arg*
    as a keyword argument:

    - **Literal value not in registry** → strict violation. The PR
      cannot ship until the value is registered in the canonical axis
      list (or removed from the producer site).
    - **F-string value** → advisory. The producer is smuggling an axis
      shape through an f-string concatenation; Phase 3's per-cluster
      migration folds it to canonical-label + ``meta`` payload. Reported
      so the discipline is visible without blocking.
    - **Unresolvable name** (function param, local computation) →
      silently skipped. The L3 linter's job is to gate the *static*
      surface; runtime values can only be checked dynamically (a
      future runtime-coherence check, planned per ADR-0023 §3 /
      ADR-0028 §"Phase 1 — Enforcement").

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
            for lineno, value_node, tree, func_scope in _iter_producer_call_sites(
                py_file,
                constructor_names=constructor_names,
                keyword_arg=keyword_arg,
            ):
                try:
                    rel = py_file.relative_to(repo_root)
                except ValueError:  # pragma: no cover
                    rel = py_file
                category, payload = _classify_value(value_node, tree, func_scope)
                if category == "literal":
                    if payload not in registry_names:
                        strict.append(
                            f"{rel}:{lineno} ({keyword_arg}={payload!r}): "
                            f"not in canonical registry"
                        )
                elif category == "literals":
                    # WI-nubuv ext A: multi-literal candidate set
                    # (ternary, if/else chain). Flag every offending
                    # literal so the operator sees which branch is
                    # the unregistered one.
                    assert isinstance(payload, frozenset)
                    bad = sorted(v for v in payload if v not in registry_names)
                    for v in bad:
                        strict.append(
                            f"{rel}:{lineno} ({keyword_arg}={v!r} via "
                            f"function-local assignment): "
                            f"not in canonical registry"
                        )
                elif category == "fstring":
                    advisory.append(
                        f"{rel}:{lineno} ({keyword_arg}=f-string"
                        f"{f' prefix={payload!r}' if payload else ''}): "
                        f"Phase-3 fold candidate"
                    )
                # unresolvable: skip silently per docstring contract.

    return ProducerCoherenceResult(
        strict_violations=tuple(strict),
        advisory_dynamic_emits=tuple(advisory),
    )


def find_evidence_type_producer_violations(
    repo_root: Path,
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Edge.evidence_type``."""
    from hypergumbo_core.evidence_types import all_evidence_type_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=all_evidence_type_names(),
    )


def find_symbol_kind_producer_violations(
    repo_root: Path,
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Symbol.kind``."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Symbol", "Symbol.create"}),
        keyword_arg="kind",
        registry_names=all_symbol_kind_names(),
    )


def find_edge_type_producer_violations(
    repo_root: Path,
) -> ProducerCoherenceResult:
    """L3 wrapper for ``Edge.edge_type``."""
    from hypergumbo_core.edge_types import all_edge_type_names
    return find_producer_coherence_violations(
        repo_root,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="edge_type",
        registry_names=all_edge_type_names(),
    )
