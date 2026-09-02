# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ratuv: a production function whose only callers live in test modules.

WHAT THIS EXISTS TO PREVENT. Six ADR-0017 artifacts were closed ``done`` on "N
tests, 100% coverage" while having no production caller — ``infer_summary``,
``is_field_tainted``, ``select_ddg_targets`` and peers. Every reference to them
was PROSE: an ADR section, a sibling docstring, a test docstring. Each parsed,
linted, and sat at 100% coverage held there by its own tests. This module is the
executable trigger that prose could not be.

THE QUESTION IS ABOUT CALLERS, NOT SEEDS, AND THAT DISTINCTION IS THE ITEM'S
WHOLE HISTORY. ``dead-code-maybe`` asks "is this reachable from the production
seed set", and the seeds are entrypoints PLUS exported public API — so a public
function with zero in-repo callers can never be flagged, which is exactly the
shape the ADR-0017 family has. Measured on that question the gate caught 3 of
its own 6 motivating examples and would have fired ~1,747 times on day one; the
owner refused it. Asking instead "does every DIRECT CALLER of this symbol live
in a test module" catches all of them and is immune to the seeding.

FUNCTIONS ONLY, AND THE REASON IS MEASURED RATHER THAN STYLISTIC. The cohort
splits 585 functions / 204 methods on the self-tree, and the method half cannot
be adjudicated today: a method's identity in the call graph is its SHORT name,
so ``Store.add`` is indistinguishable from ``set.add`` and ``Pattern.matches``
scores hundreds of hits off ``re``'s ``.matches``. Deciding a method needs
receiver typing, which is INV-linub's open work. Including them would put ~100
unadjudicable rows in a frozen baseline, and *"a rubber-stamped ratchet is worse
than none"* is this item's governing constraint. Methods are therefore counted
and reported, never gated.

THE DEMOTION ARM, AND WHY IT IS NOT OPTIONAL. Three reference mechanisms are
invisible to a ``calls``/``instantiates`` edge set, and each produced a
FALSE POSITIVE on live production code in the first measurement:

    ``p_slice.set_defaults(func=cmd_slice)``   a VALUE BINDING, not a call —
                                              and ``cmd_slice`` is the entry
                                              point of ``hypergumbo slice``
    ``@register_linker(...)``                 decorator APPLICATION
    a plain cross-module reference            the call graph missed the edge

Demoting them costs one AST pass over production source and removes 53 of 585
functions. Without it the gate fires on user-facing CLI commands, which is
disqualifying however good the rest of the signal is.

AST, NOT GREP, AND THAT CHOICE HAS ALREADY PAID. A grep proxy once scored
``infer_summary`` as having one production reference; the reference was a
COMMENT in ``taint.py`` reading "``infer_summary`` still has zero production
callers". Prose about deadness counted as evidence of life. This module collects
``ast.Name`` loads and ``ast.Attribute`` accesses, so a comment cannot vote.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .axis_meta_keys import call_family_edge_types
from .paths import is_test_file

#: Node kinds this gate adjudicates. Deliberately NOT every callable kind —
#: see the module docstring on why methods are counted but never gated.
GATED_KINDS: frozenset[str] = frozenset({"function"})

#: Counted and reported so the excluded population stays visible, rather than
#: silently dropping out of a number a reader would take for the whole cohort.
REPORTED_KINDS: frozenset[str] = frozenset({"function", "method"})

#: Keyword arguments whose VALUE being a bare name is a dispatch binding rather
#: than a call. ``func`` is argparse's own (``set_defaults(func=cmd_x)``); the
#: rest are the same shape under other names.
_DISPATCH_KEYWORDS: frozenset[str] = frozenset(
    {"func", "handler", "callback", "default"}
)

DEMOTION_DISPATCH = "dispatch_binding"
DEMOTION_DECORATOR = "decorator_application"
DEMOTION_REGISTERED = "carries_a_decorator"
DEMOTION_CROSS_MODULE = "production_reference"


@dataclass(frozen=True)
class TestOnlySymbol:
    """One production symbol whose every direct caller is a test module."""

    #: Not a pytest test class. The name is the domain term and worth keeping,
    #: so the collection opt-out is declared rather than the name bent around
    #: a test runner's prefix convention.
    __test__ = False

    symbol_id: str
    name: str
    kind: str
    path: str

    @property
    def key(self) -> str:
        """The BASELINE key, which deliberately omits the line span.

        A symbol id carries its span (``…:11826-12011:main:function``), so an
        id-keyed baseline would churn on every edit that moves a line — a
        diff nobody could read, which is how a ratchet becomes a rubber stamp.
        ``path::name`` is stable under line movement and changes only on a
        rename or a move, both of which SHOULD be re-adjudicated.
        """
        return f"{self.path}::{self.name}"


@dataclass
class ProductionReferences:
    """Where production source mentions a bare name, by mechanism.

    Built by parsing, never by grepping — see the module docstring.
    """

    loads: dict[str, set[str]] = field(default_factory=dict)
    decorated: dict[str, set[str]] = field(default_factory=dict)
    dispatch_bound: dict[str, set[str]] = field(default_factory=dict)
    #: Functions that CARRY a decorator. Distinct from ``decorated``, which
    #: indexes the decorator's own name — and confusing the two was a real
    #: bug: ``@register_linker`` put ``register_linker`` in ``decorated`` and
    #: left ``link_decorator_dispatch``, the registered function, gated.
    carries_decorator: dict[str, set[str]] = field(default_factory=dict)


def _index(store: dict[str, set[str]], name: str, where: str) -> None:
    store.setdefault(name, set()).add(where)


def collect_production_references(
    files: Iterable[Path], root: Path,
) -> ProductionReferences:
    """Parse production modules and index every non-call reference mechanism.

    A file that does not parse is SKIPPED rather than failing the run: this is
    a lint over a tree that may legitimately contain a file mid-edit, and the
    conservative direction for a gate that can DELETE work from a baseline is
    to see fewer references, not to crash.
    """
    refs = ProductionReferences()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    named = getattr(target, "id", None) or getattr(
                        target, "attr", None,
                    )
                    if named:
                        _index(refs.decorated, named, rel)
                        _index(refs.carries_decorator, node.name, rel)
            elif isinstance(node, ast.keyword):
                if node.arg in _DISPATCH_KEYWORDS and isinstance(
                    node.value, ast.Name,
                ):
                    _index(refs.dispatch_bound, node.value.id, rel)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                _index(refs.loads, node.id, rel)
            elif isinstance(node, ast.Attribute) and isinstance(
                node.ctx, ast.Load,
            ):
                _index(refs.loads, node.attr, rel)
    return refs


def production_source_files(root: Path) -> list[Path]:
    """Every shipped module: ``packages/*/src/**/*.py``, sorted for determinism."""
    return sorted(root.glob("packages/*/src/**/*.py"))


def _is_production(path: str) -> bool:
    return path.startswith("packages/") and "/src/" in path


def find_test_only_symbols(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> list[TestOnlySymbol]:
    """Production callables whose every direct caller is a test module.

    A symbol with ZERO callers is NOT in this cohort. That is the load-bearing
    difference from ``dead-code-maybe``: a registry-dispatched analyzer method
    has no direct callers at all, so dynamic dispatch separates itself into the
    zero-caller population instead of drowning this one.
    """
    call_types = call_family_edge_types()
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        etype = edge.get("type") or edge.get("edge_type")
        if etype not in call_types:
            continue
        dst, src = edge.get("dst"), edge.get("src")
        if dst and src:
            incoming.setdefault(dst, []).append(src)

    found: list[TestOnlySymbol] = []
    for node in nodes:
        if node.get("kind") not in REPORTED_KINDS:
            continue
        path = node.get("path") or ""
        if not _is_production(path) or is_test_file(path):
            continue
        callers = [
            by_id[c] for c in incoming.get(node["id"], []) if c in by_id
        ]
        if not callers:
            continue
        if all(is_test_file(c.get("path") or "") for c in callers):
            found.append(TestOnlySymbol(
                symbol_id=node["id"], name=node.get("name") or "",
                kind=node["kind"], path=path,
            ))
    return sorted(found, key=lambda s: s.key)


def demotion_reason(
    symbol: TestOnlySymbol, refs: ProductionReferences,
) -> str | None:
    """Why this symbol is reachable by something the call graph cannot see.

    ``None`` means no such mechanism was found — the symbol stays in the gated
    cohort. Only the last name component is consulted, which is exactly why
    methods are not gated: for them it is ambiguous by construction.

    SAME-MODULE REFERENCES COUNT, and the first cut excluded them. A ``def``
    is not a ``Load``, so the only way a module mentions its own function is a
    real reference — ``cli.py`` passes ``cli_mod._install_rust_analyzer_…`` as
    a value one screen above the definition, which is production use the call
    graph does not model. The one thing this admits is a recursive dead
    function demoting itself; that is a false NEGATIVE, which is the safe
    direction for a gate that can block CI.
    """
    bare = symbol.name.rsplit(".", 1)[-1]
    if refs.dispatch_bound.get(bare):
        return DEMOTION_DISPATCH
    if refs.decorated.get(bare):
        return DEMOTION_DECORATOR
    if refs.carries_decorator.get(bare):
        return DEMOTION_REGISTERED
    if refs.loads.get(bare):
        return DEMOTION_CROSS_MODULE
    return None


def gated_cohort(
    symbols: Sequence[TestOnlySymbol], refs: ProductionReferences,
) -> tuple[list[TestOnlySymbol], dict[str, int]]:
    """The cohort the ratchet acts on, plus a census of what was set aside."""
    kept: list[TestOnlySymbol] = []
    census: dict[str, int] = {}
    for symbol in symbols:
        if symbol.kind not in GATED_KINDS:
            census["not_gated_kind"] = census.get("not_gated_kind", 0) + 1
            continue
        reason = demotion_reason(symbol, refs)
        if reason:
            census[reason] = census.get(reason, 0) + 1
            continue
        kept.append(symbol)
    return kept, census


def load_baseline(path: Path) -> set[str]:
    """Baseline keys, or an EMPTY set when the file does not exist.

    Absent is not zero-and-clean: the caller treats a missing baseline as an
    infrastructure condition, not as "everything is new".
    """
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("keys") or [])


def baseline_document(symbols: Sequence[TestOnlySymbol]) -> dict[str, Any]:
    """The on-disk baseline. Sorted so a regeneration diffs line by line."""
    return {
        "_comment": (
            "WI-ratuv shrink-only ratchet. Each key is a production FUNCTION "
            "whose only callers are test modules. This list may only SHRINK: "
            "adding a key requires wiring the function to production or "
            "retiring it. Regenerate with "
            "scripts/check-test-only-reachability --write-baseline."
        ),
        "keys": sorted({s.key for s in symbols}),
    }


def compare_to_baseline(
    symbols: Sequence[TestOnlySymbol], baseline: set[str],
) -> tuple[list[str], list[str]]:
    """``(newly test-only, no longer test-only)`` — the ratchet's verdict.

    Shrink-only: the first list failing the gate is the whole contract. The
    second is reported so a drained entry can be removed from the baseline
    rather than accumulating as dead weight.
    """
    current = {s.key for s in symbols}
    return sorted(current - baseline), sorted(baseline - current)
