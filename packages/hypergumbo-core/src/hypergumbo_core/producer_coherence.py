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
    value: ast.expr, tree: ast.Module,
) -> tuple[str, str | None]:
    """Classify a keyword argument's value expression.

    Returns ``(category, payload)`` where category is one of:

    - ``"literal"`` — payload is the resolved literal string.
    - ``"fstring"`` — payload is the literal-prefix portion of the
      f-string when one exists, else ``""``.
    - ``"unresolvable"`` — payload is a short description (e.g.
      ``"Name(other_var)"``) for the advisory.
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
        resolved = _resolve_module_constant(value.id, tree)
        if resolved is not None:
            return ("literal", resolved)
        return ("unresolvable", f"Name({value.id})")
    return ("unresolvable", type(value).__name__)


def _iter_producer_call_sites(
    path: Path,
    *,
    constructor_names: frozenset[str],
    keyword_arg: str,
) -> Iterator[tuple[int, ast.expr, ast.Module]]:
    """Yield ``(lineno, value_node, module_tree)`` for each matching call site.

    Walks every ``ast.Call`` node in *path* and surfaces the keyword's
    value expression for downstream classification. Files that fail to
    read or parse are silently skipped (best-effort, same posture as
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

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _matches_constructor(node, constructor_names):
            continue
        kw = _find_keyword(node, keyword_arg)
        if kw is None:
            continue
        yield node.lineno, kw.value, tree


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
            for lineno, value_node, tree in _iter_producer_call_sites(
                py_file,
                constructor_names=constructor_names,
                keyword_arg=keyword_arg,
            ):
                try:
                    rel = py_file.relative_to(repo_root)
                except ValueError:  # pragma: no cover
                    rel = py_file
                category, payload = _classify_value(value_node, tree)
                if category == "literal":
                    if payload not in registry_names:
                        strict.append(
                            f"{rel}:{lineno} ({keyword_arg}={payload!r}): "
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
