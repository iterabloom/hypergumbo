# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-zuhub fallback-coherence linter.

Linker-emitted edges whose target was resolved via simple-name fallback
(no fully-qualified module / namespace / kind disambiguator) must
carry **both**:

1. ``confidence <= 0.5`` on the edge.
2. ``meta["disambiguation_fallback"] = True`` provenance flag.

This module walks ``Edge.create(...)`` / ``Edge(...)`` call sites under
``packages/.../linkers/`` and asserts the contract holds at every site
that sets ``meta["disambiguation_fallback"] = True``. The shape of the
test is conservative: if a call site sets the flag, the confidence on
that same call must be statically resolvable to a value <= 0.5.

What this catches
-----------------
The "set the flag but forgot to lower confidence" failure mode. Both
fields are conditional on the same predicate in the canonical
inheritance.py pattern; an incomplete adoption that flips one without
the other slips through every other gate.

What this does NOT catch
------------------------
The complementary "lowered confidence but forgot the flag" failure
mode. A linker that emits a fallback edge with ``confidence=0.5`` but
no flag is structurally invisible to this static walker — the
confidence value alone is not a reliable fallback marker because some
linkers legitimately emit low-confidence edges for inference-quality
reasons (e.g., name-based dispatch heuristics, type-inference
ambiguity).

The flagless-fallback case is caught by the **per-linker property
tests** of the form ``test_<edge_type>_deterministic_fallback_when_
ambiguous`` (INV-zuhub item 1), which exercise the runtime behaviour
on synthetic fixtures known to trigger a fallback decision.

Why a single canonical key name
-------------------------------
The contract is field-aware: ``disambiguation_fallback`` is the
canonical meta-key spelling registered in
:mod:`hypergumbo_core.axis_meta_keys` per the INV-zuhub statement.
Future ADR-0028-style folds may rename the key to a richer
``resolution_quality`` enum (see ADR-0028 §"Sibling-field design
call-out" intersection discussion on INV-zuhub) — at which point this
module's key name updates in lock-step. The narrow shape of "exactly
one key, named in :mod:`axis_meta_keys`" is intentional: a per-linker
opt-in key would defeat the property test's enforcement purpose.

Why this lives in its own module
--------------------------------
Coherence between two kwargs on the same ``Edge.create`` call is a
distinct concern from :mod:`hypergumbo_core.producer_coherence`
(which gates *axis values* — kind / edge_type / evidence_type — against
their canonical registries) and from :mod:`hypergumbo_core.axis_drift`
(which gates *consumer-side hardcoded sets* against the registries).
The three modules collectively form the L1-through-L3 axis-discipline
ladder; this module is L4 on the same ladder, gating intra-call kwarg
coherence rather than enum drift or producer drift.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Iterator


DEFAULT_LINKER_ROOTS: Final[tuple[str, ...]] = (
    "packages/hypergumbo-core/src/hypergumbo_core/linkers",
)
"""Default repo-relative directories scanned for fallback-coherence.

Narrower than :data:`hypergumbo_core.producer_coherence.DEFAULT_SEARCH_ROOTS`
because the disambiguation_fallback discipline is specifically about
*linker* producers (Tier 2 edge-recovery passes). The full search-roots
list is configurable via the *linker_roots* parameter for callers that
need to extend the scope to language-analyzer producers.
"""

CONSTRUCTOR_NAMES: Final[frozenset[str]] = frozenset({"Edge", "Edge.create"})
"""Edge constructor call-site names this module gates."""

FALLBACK_META_KEY: Final[str] = "disambiguation_fallback"
"""Canonical meta-key name carrying the INV-zuhub provenance flag.

Single source of truth lives in
:mod:`hypergumbo_core.axis_meta_keys`; this module references the key
by string and a property test cross-validates that the same string
appears in the canonical registry.
"""

CONFIDENCE_CEILING: Final[float] = 0.5
"""Maximum allowed confidence when the fallback flag is set.

The number lives here so a single edit changes both the structural
gate and the test suite. Per INV-zuhub statement: "confidence <= 0.5".
"""


@dataclass(frozen=True)
class FallbackCoherenceViolation:
    """A single Edge.create call site that violates the INV-zuhub contract.

    Attributes:
        path: Repo-relative path of the offending source file.
        lineno: Line number of the offending ``Edge.create`` call.
        reason: Short human-readable description (e.g. "confidence > 0.5",
            "confidence not statically resolvable").
    """

    path: str
    lineno: int
    reason: str

    def format(self) -> str:
        """Render the violation as a CI-friendly diagnostic line."""
        return f"{self.path}:{self.lineno}: {self.reason}"


def _matches_constructor(call_node: ast.Call) -> bool:
    """Return True iff *call_node*'s callable is an Edge constructor.

    Mirrors :func:`hypergumbo_core.producer_coherence._matches_constructor`'s
    shape — bare ``Edge(...)`` and dotted ``Edge.create(...)`` both
    qualify; helper-call indirection (``add_edge(...)``) is out of
    scope (callers must descend through helpers manually).
    """
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id in CONSTRUCTOR_NAMES
    if isinstance(func, ast.Attribute):
        if func.attr in CONSTRUCTOR_NAMES:
            return True
        if isinstance(func.value, ast.Name):
            dotted = f"{func.value.id}.{func.attr}"
            if dotted in CONSTRUCTOR_NAMES:
                return True
    return False


def _find_keyword(call_node: ast.Call, name: str) -> ast.keyword | None:
    """Return the ``keyword`` AST node for *name* on *call_node*, or None."""
    for kw in call_node.keywords:
        if kw.arg == name:
            return kw
    return None


def _iter_same_scope_assignments(node: ast.AST) -> Iterator[ast.Assign | ast.AnnAssign]:
    """Yield Assign/AnnAssign nodes in *node*'s lexical scope.

    Descends through compound control-flow statements (``if``, ``for``,
    ``while``, ``try``, ``with``) but stops at nested function, class,
    and lambda boundaries — those introduce new scopes and their
    assignments do not bind the same name in the parent scope. Mirrors
    :func:`hypergumbo_core.producer_coherence._iter_same_scope_assignments`'s
    discipline.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (
            ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
        )):
            continue
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            yield child
        yield from _iter_same_scope_assignments(child)


def _resolve_function_local_assignment(
    name: str, func_scope: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.expr | None:
    """Return the RHS of the single function-local assignment to *name*.

    Returns ``None`` if *name* has zero, multiple distinct, or
    non-simple-assignment bindings in the lexical scope of *func_scope*
    (the canonical inheritance.py shape is a single
    ``confidence = 0.5 if is_fallback else 0.95`` binding; anything
    more complex is out of scope for the static walker).
    """
    last_rhs: ast.expr | None = None
    found_count = 0
    for assign in _iter_same_scope_assignments(func_scope):
        if isinstance(assign, ast.Assign) and len(assign.targets) == 1:
            tgt = assign.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == name:
                last_rhs = assign.value
                found_count += 1
        elif isinstance(assign, ast.AnnAssign) and isinstance(assign.target, ast.Name):
            if assign.target.id == name and assign.value is not None:
                last_rhs = assign.value
                found_count += 1
    if found_count == 1:
        return last_rhs
    return None


def _expr_to_floats(value: ast.expr) -> tuple[float, ...] | None:
    """Try to enumerate the set of float values *value* might take.

    Handles:

    - ``ast.Constant`` with float / int payload.
    - ``ast.IfExp`` (ternary) whose both branches resolve.
    - ``ast.UnaryOp(USub, Constant)`` for negative literals.

    Returns ``None`` for any other shape (Name, Call, etc.) — those are
    structurally unresolvable. A two-element tuple is returned for
    ternary; a one-element tuple for direct literals.
    """
    if isinstance(value, ast.Constant):
        if isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
            return (float(value.value),)
        return None
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        inner = _expr_to_floats(value.operand)
        if inner is None:
            return None
        return tuple(-x for x in inner)
    if isinstance(value, ast.IfExp):
        body = _expr_to_floats(value.body)
        orelse = _expr_to_floats(value.orelse)
        if body is None or orelse is None:
            return None
        return body + orelse
    return None


def _confidence_values(
    kw_value: ast.expr,
    func_scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> tuple[float, ...] | None:
    """Resolve the ``confidence=`` kwarg to its candidate float values.

    Resolution order:

    1. Literal constant or inline ternary — direct read.
    2. Function-local Name — chase through a single assignment to that
       name in the enclosing function and re-classify the RHS.

    Returns ``None`` if no resolution succeeds; the caller treats this
    as "confidence not statically resolvable" and emits a violation
    (the INV-zuhub contract requires static gating — runtime-computed
    confidence cannot be checked without execution).
    """
    direct = _expr_to_floats(kw_value)
    if direct is not None:
        return direct
    if isinstance(kw_value, ast.Name) and func_scope is not None:
        rhs = _resolve_function_local_assignment(kw_value.id, func_scope)
        if rhs is not None:
            return _expr_to_floats(rhs)
    return None


def _dict_has_fallback_flag_set(node: ast.expr) -> bool:
    """Return True iff *node* is a literal dict whose ``disambiguation_fallback``
    entry maps to a literal ``True``."""
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and key.value == FALLBACK_META_KEY
            and isinstance(value, ast.Constant)
            and value.value is True
        ):
            return True
    return False


def _classify_meta(
    kw_value: ast.expr,
    func_scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> tuple[str, ast.expr | None]:
    """Classify how the ``meta=`` kwarg declares the fallback flag.

    Returns a ``(shape, predicate)`` pair where shape is one of:

    - ``"none"`` — the call does not statically declare the flag;
      :data:`predicate` is ``None``. The caller should skip the site
      (nothing to enforce).
    - ``"unconditional"`` — the meta is a literal dict that sets the
      flag unconditionally; predicate is ``None``. The caller enforces
      ``confidence <= 0.5`` across **all** static candidates.
    - ``"conditional"`` — the meta is an ``ast.IfExp`` (inline or
      function-local-Name-resolved) whose True branch is a flag-bearing
      dict; predicate is the IfExp's ``test`` node. The caller enforces
      ``confidence <= 0.5`` only on the True branch IF the confidence
      kwarg is itself a ternary on a matching predicate; otherwise on
      all candidates.

    Recognized resolution paths:

    1. ``meta={"disambiguation_fallback": True, ...}`` → ``"unconditional"``.
    2. ``meta={"disambiguation_fallback": True} if P else None`` →
       ``"conditional"`` with predicate=P.
    3. ``meta=edge_meta`` where edge_meta is a single function-local
       assignment to either of the above shapes → resolved through.
    4. Anything else (dict-merge, helper call, multi-assignment) →
       ``"none"``.
    """
    if _dict_has_fallback_flag_set(kw_value):
        return ("unconditional", None)
    if isinstance(kw_value, ast.IfExp):
        if _dict_has_fallback_flag_set(kw_value.body):
            return ("conditional", kw_value.test)
    if isinstance(kw_value, ast.Name) and func_scope is not None:
        rhs = _resolve_function_local_assignment(kw_value.id, func_scope)
        if rhs is not None:
            return _classify_meta(rhs, func_scope)
    return ("none", None)


def _predicate_matches(a: ast.expr, b: ast.expr) -> bool:
    """Return True iff *a* and *b* are structurally identical AST nodes.

    Used to decide whether the meta's conditional predicate gates the
    same branch as the confidence's conditional predicate. We compare
    via ``ast.dump`` with ``annotate_fields=True`` and
    ``include_attributes=False`` — the latter excludes line / col
    offsets so two identically-spelled predicates at different source
    locations still match. This is the simplest sound predicate-
    equivalence check; anything richer (alpha-equivalence,
    constant-folding) is out of scope and a future-WI concern.
    """
    return ast.dump(a, annotate_fields=True, include_attributes=False) == \
        ast.dump(b, annotate_fields=True, include_attributes=False)


def _confidence_values_for_meta_shape(
    conf_kw_value: ast.expr,
    meta_shape: str,
    meta_predicate: ast.expr | None,
    func_scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> tuple[float, ...] | None:
    """Resolve the ``confidence=`` kwarg to the candidate floats that
    actually correlate with the fallback-flag-set branch.

    Resolution rules:

    - ``meta_shape == "unconditional"``: every candidate must satisfy
      the ceiling, so resolve all candidates.
    - ``meta_shape == "conditional"``: if the confidence is also an
      IfExp (inline or function-local-Name-resolved) with a predicate
      that matches *meta_predicate*, only the True branch's candidates
      matter. Otherwise the confidence is unconditional from the
      walker's perspective — all candidates matter.

    Returns ``None`` for unresolvable shapes (same posture as
    :func:`_confidence_values`).
    """
    if meta_shape == "conditional":
        if isinstance(conf_kw_value, ast.IfExp):
            if meta_predicate is not None and _predicate_matches(
                conf_kw_value.test, meta_predicate,
            ):
                return _expr_to_floats(conf_kw_value.body)
        if isinstance(conf_kw_value, ast.Name) and func_scope is not None:
            rhs = _resolve_function_local_assignment(
                conf_kw_value.id, func_scope,
            )
            if isinstance(rhs, ast.IfExp) and meta_predicate is not None:
                if _predicate_matches(rhs.test, meta_predicate):
                    return _expr_to_floats(rhs.body)
            if rhs is not None:
                return _expr_to_floats(rhs)
    return _confidence_values(conf_kw_value, func_scope)


def _walk_calls(
    node: ast.AST,
    func_scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> Iterator[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """Yield every ``ast.Call`` in *node* with its enclosing FunctionDef.

    Mirrors :func:`hypergumbo_core.producer_coherence._walk_calls_with_scope`
    so the scope-tracking semantics match: nested classes/lambdas don't
    pollute the parent's scope; nested FunctionDef bodies re-enter
    function scope at their own boundary.
    """
    if isinstance(node, ast.Call):
        yield node, func_scope
    new_scope = (
        node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else func_scope
    )
    for child in ast.iter_child_nodes(node):
        yield from _walk_calls(child, new_scope)


def _check_call_site(
    call: ast.Call,
    func_scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
    rel_path: str,
) -> FallbackCoherenceViolation | None:
    """Check a single Edge.create call site; return a violation or None.

    Steps:

    1. Skip if the callable isn't an Edge constructor.
    2. Skip if ``meta=`` is absent or doesn't statically declare the
       fallback flag — the contract has nothing to enforce.
    3. Require ``confidence=`` to be present and statically resolvable
       to a value <= :data:`CONFIDENCE_CEILING`. If unresolvable or
       above the ceiling, emit a violation.
    """
    if not _matches_constructor(call):
        return None
    meta_kw = _find_keyword(call, "meta")
    if meta_kw is None:
        return None
    meta_shape, meta_predicate = _classify_meta(meta_kw.value, func_scope)
    if meta_shape == "none":
        return None
    conf_kw = _find_keyword(call, "confidence")
    if conf_kw is None:
        return FallbackCoherenceViolation(
            path=rel_path,
            lineno=call.lineno,
            reason=(
                f"sets meta[{FALLBACK_META_KEY!r}]=True but does not "
                f"set confidence — INV-zuhub contract requires "
                f"confidence <= {CONFIDENCE_CEILING}"
            ),
        )
    values = _confidence_values_for_meta_shape(
        conf_kw.value, meta_shape, meta_predicate, func_scope,
    )
    if values is None:
        return FallbackCoherenceViolation(
            path=rel_path,
            lineno=call.lineno,
            reason=(
                f"sets meta[{FALLBACK_META_KEY!r}]=True but confidence "
                f"is not statically resolvable to a float (got "
                f"{type(conf_kw.value).__name__}) — INV-zuhub gate "
                f"requires static gating"
            ),
        )
    bad = [v for v in values if v > CONFIDENCE_CEILING]
    if bad:
        return FallbackCoherenceViolation(
            path=rel_path,
            lineno=call.lineno,
            reason=(
                f"sets meta[{FALLBACK_META_KEY!r}]=True but confidence "
                f"candidate(s) {bad} exceed ceiling "
                f"{CONFIDENCE_CEILING} — INV-zuhub violation"
            ),
        )
    return None


def find_fallback_coherence_violations(
    repo_root: Path,
    *,
    linker_roots: Iterable[str] = DEFAULT_LINKER_ROOTS,
) -> tuple[FallbackCoherenceViolation, ...]:
    """Scan linker source files; return INV-zuhub coherence violations.

    For every ``Edge.create(...)`` (or ``Edge(...)``) call site under
    *linker_roots* that statically declares
    ``meta["disambiguation_fallback"] = True``, the *same call* must
    set ``confidence`` to a statically-resolvable value
    <= :data:`CONFIDENCE_CEILING`. Anything else is a violation.

    Files that fail to read or parse are silently skipped (best-effort,
    same posture as
    :func:`hypergumbo_core.axis_drift.iter_axis_set_assignments`).
    """
    violations: list[FallbackCoherenceViolation] = []
    for root_name in linker_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for py_file in sorted(root.rglob("*.py")):
            try:
                source = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):  # pragma: no cover - defensive
                continue
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:  # pragma: no cover - defensive
                continue
            try:
                rel = py_file.relative_to(repo_root)
            except ValueError:  # pragma: no cover - defensive
                rel = py_file
            for call, func_scope in _walk_calls(tree, None):
                violation = _check_call_site(call, func_scope, str(rel))
                if violation is not None:
                    violations.append(violation)
    return tuple(violations)
