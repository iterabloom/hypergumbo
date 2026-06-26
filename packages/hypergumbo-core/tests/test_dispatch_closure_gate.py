# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-pohik closure gate: per-dispatch-family reachability regression guard.

INV-pohik ("the call graph doesn't traverse dispatch-primitive patterns")
names THREE dispatch families that, before their structural fixes, produced a
*reachability island*: a dispatched target with zero traversable in-edges, so
the ``dead-code-maybe`` BFS (which follows only ``{calls, dispatches_to,
wraps}`` — ``cli._REACHABILITY_EDGE_TYPES``) falsely flagged it dead. The three
families are:

1. **route -> handler** (dispatch:F1) — a route symbol now emits a traversable
   ``dispatches_to`` edge to its handler (``meta["dispatch_kind"] == "route"``).
2. **closure-factory** (dispatch:F8 PR-A) — a Python function that returns one
   of its own directly-nested closures now emits ``dispatches_to``
   factory -> closure (``meta["dispatch_kind"] == "closure_factory"``).
3. **descriptor-protocol** (``@property`` / ``@staticmethod`` / ``@classmethod``
   / ``__get__``-style attribute dispatch) — see the scope note on
   ``TestDescriptorProtocolFamilyIsVacuousOnCorpus`` below.

This module is the *gate*: a single property test per family asserting that the
dispatched target is REACHABLE from its dispatcher over the production
reachability edge set. Each assertion is built on a SYNTHETIC fixture (a
tmp_path repo or hand-constructed symbols/edges) rather than a brittle absolute
self-corpus count — the plan (``dispatch_f8_plan_06252026.md`` Increment 3)
explicitly warns that the ``16/16 route features`` / ``FP <= 40%`` self-
thresholds are corpus-drift-brittle. The synthetic fixtures are the load-
bearing assertions; they would catch a REGRESSION in any family's traversal
regardless of how the self-corpus drifts.

The shared helper :func:`_reachable_over_production_edges` mirrors
``cli.cmd_dead_code_maybe`` exactly: it builds the call graph from the SAME
``{calls, dispatches_to, wraps}`` edge-type set and runs the SAME
``cli._bfs_reachable``. If that constant changes (e.g. a family's edge type is
dropped from the reachability set), this gate goes red.

The optional self-analysis ratchet (``test_self_analysis_dispatch_signals``) is
guarded behind ``HYPERGUMBO_RUN_SELF_ANALYSIS`` (cf. ``test_orphan_node_audit``)
so corpus drift can never break the normal CI run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import _bfs_reachable, run_behavior_map
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.route_handler import link_routes_to_handlers

# The production reachability edge set. Kept in lockstep with
# ``cli.cmd_dead_code_maybe``'s ``_REACHABILITY_EDGE_TYPES``; the
# ``test_reachability_edge_set_matches_production`` guard below fails loudly if
# the two ever drift apart, so the gate can never silently assert reachability
# over a stale edge set.
_REACHABILITY_EDGE_TYPES = {"calls", "dispatches_to", "wraps"}


def _repo_root() -> Path:
    """Locate the repository root by walking up to the ``.git`` directory.

    Robust to how deeply the package is nested (a hardcoded ``parents[N]`` is
    fragile — it points at ``/home/<user>`` in this layout, not the repo). Used
    only by the corpus-scanning tests below.
    """
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / ".git").exists():
            return current
        if current.parent == current:  # pragma: no cover - filesystem root guard
            break
        current = current.parent
    raise RuntimeError(  # pragma: no cover - only if run outside a git checkout
        "could not locate repo root (.git not found walking up from test file)"
    )


def _reachable_over_production_edges(
    seed_ids: set[str], edges: list[dict[str, Any]]
) -> set[str]:
    """Return ids reachable from *seed_ids*, mirroring ``cmd_dead_code_maybe``.

    Builds the call graph from exactly the ``{calls, dispatches_to, wraps}``
    edge types the dead-code BFS uses, then delegates to the production
    ``_bfs_reachable``. *edges* are plain dicts (``behavior_map["edges"]``
    shape: ``{"src", "dst", "type", ...}``).
    """
    call_graph: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") in _REACHABILITY_EDGE_TYPES:
            src = edge.get("src", "")
            dst = edge.get("dst", "")
            if src and dst:
                call_graph.setdefault(src, []).append(dst)
    return _bfs_reachable(seed_ids, call_graph)


def _edges_to_dicts(edges: list[Edge]) -> list[dict[str, Any]]:
    """Project ``Edge`` objects onto the behavior-map edge-dict shape."""
    return [{"src": e.src, "dst": e.dst, "type": e.edge_type} for e in edges]


def test_reachability_edge_set_matches_production() -> None:
    """The gate's edge set must equal ``cmd_dead_code_maybe``'s.

    The reachability constant lives inside ``cmd_dead_code_maybe`` as a local,
    so it cannot be imported directly. This guard reads the function source and
    asserts the literal set used by the dead-code BFS is exactly the one this
    gate asserts reachability over — pinning the two together structurally so a
    future edit to one without the other turns this test red.
    """
    import inspect

    from hypergumbo_core import cli

    src = inspect.getsource(cli.cmd_dead_code_maybe)
    assert '_REACHABILITY_EDGE_TYPES = {"calls", "dispatches_to", "wraps"}' in src, (
        "cmd_dead_code_maybe's reachability edge set changed; update this gate's "
        "_REACHABILITY_EDGE_TYPES (and the per-family assertions) to match."
    )
    assert _REACHABILITY_EDGE_TYPES == {"calls", "dispatches_to", "wraps"}


class TestRouteHandlerFamilyReachable:
    """dispatch:F1 — a route's handler is reachable from the route symbol.

    Synthetic fixture: one route symbol carrying ``controller_action`` metadata
    plus its handler method, fed through the real ``route_handler`` linker. We
    assert the linker emits a ``dispatches_to`` route -> handler edge AND that
    the handler is reachable from the route over the production edge set. A
    regression that drops the route dispatch edge (or re-types it off the
    reachability set) makes the handler unreachable and this test red.
    """

    def _route_and_handler(self) -> tuple[Symbol, Symbol]:
        route = Symbol(
            id="ruby:/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="function",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "users#index",
                "framework_role": "route",
            },
            origin="ruby",
            origin_run_id="test-run",
        )
        handler = Symbol(
            id="ruby:/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"class": "UsersController"},
            origin="ruby",
            origin_run_id="test-run",
        )
        return route, handler

    def test_handler_is_reachable_from_route(self) -> None:
        route, handler = self._route_and_handler()
        result = link_routes_to_handlers([route, handler], [])

        # The linker must emit a traversable route -> handler dispatch edge.
        dispatch = [
            e
            for e in result.edges
            if e.edge_type == "dispatches_to"
            and (e.meta or {}).get("dispatch_kind") == "route"
            and e.src == route.id
            and e.dst == handler.id
        ]
        assert len(dispatch) == 1, (
            "route_handler linker emitted no route->handler dispatches_to edge: "
            f"{[(e.src, e.dst, e.edge_type, e.meta) for e in result.edges]}"
        )

        # ... and that edge must make the handler reachable from the route over
        # the SAME edge set the dead-code BFS uses.
        reachable = _reachable_over_production_edges(
            {route.id}, _edges_to_dicts(result.edges)
        )
        assert handler.id in reachable, (
            "INV-pohik regression: route's handler is not reachable from the "
            "route over {calls, dispatches_to, wraps} — the route->handler "
            "dispatch is an island."
        )


class TestClosureFactoryFamilyReachable:
    """dispatch:F8 PR-A — a returned nested closure is reachable from its factory.

    Synthetic fixture: the canonical ``register_*``-style decorator factory
    (``def register(...): def decorator(func): ...; return decorator``) written
    to a tmp_path repo and run through the FULL production pipeline
    (``run_behavior_map`` → analyzer + linkers + edge collection). We assert the
    analyzer emits the ``closure_factory`` ``dispatches_to`` edge AND that the
    nested ``decorator`` closure is reachable from the ``register`` factory over
    the production edge set — exactly as ``dead-code-maybe`` would traverse it.
    """

    FACTORY_SRC = (
        "REGISTRY = []\n"
        "\n"
        "\n"
        "def register(name):\n"
        "    def decorator(func):\n"
        "        REGISTRY.append((name, func))\n"
        "        return func\n"
        "    return decorator\n"
        "\n"
        "\n"
        '@register("a")\n'
        "def alpha():\n"
        "    return 1\n"
    )

    def test_closure_is_reachable_from_factory(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "reg.py").write_text(self.FACTORY_SRC)
        out = tmp_path / "out.json"
        run_behavior_map(
            repo_root=repo, out_path=out, include_sketch_precomputed=False
        )
        bm = json.loads(out.read_text())
        nodes = bm["nodes"]
        edges = bm["edges"]

        factory = [
            n
            for n in nodes
            if n["name"].split(".")[-1] == "register" and n["kind"] == "function"
        ]
        closure = [
            n
            for n in nodes
            if n["name"].split(".")[-1] == "decorator" and n["kind"] == "function"
        ]
        assert len(factory) == 1, f"expected one register factory, got {factory}"
        assert len(closure) == 1, f"expected one decorator closure, got {closure}"
        factory_id = factory[0]["id"]
        closure_id = closure[0]["id"]

        # The analyzer must emit the closure-factory dispatch edge.
        cf_edges = [
            e
            for e in edges
            if e["type"] == "dispatches_to"
            and (e.get("meta") or {}).get("dispatch_kind") == "closure_factory"
            and e["src"] == factory_id
            and e["dst"] == closure_id
        ]
        assert len(cf_edges) == 1, (
            "no closure_factory dispatches_to edge factory->closure: "
            f"{[(e['src'], e['dst'], e['type'], e.get('meta')) for e in edges if e['type'] == 'dispatches_to']}"
        )

        # ... and the nested closure must be reachable from the factory.
        reachable = _reachable_over_production_edges({factory_id}, edges)
        assert closure_id in reachable, (
            "INV-pohik regression: returned nested closure is not reachable "
            "from its factory over {calls, dispatches_to, wraps} — the closure "
            "is an island and dead-code-maybe would falsely flag it dead."
        )


class TestDescriptorProtocolFamilyIsVacuousOnCorpus:
    """dispatch:F3 / descriptor-protocol leg — DOCUMENTED-VACUOUS by construction.

    SCOPE NOTE (STEP-1 finding, 2026-06-26 dogfood, evidence preserved here so
    the gate is self-explaining):

    The descriptor-protocol leg of INV-pohik has **no real reachability false-
    positive on the current corpus**, on two independent grounds:

    1. There are ZERO descriptor dunders in the analyzed source —
       ``grep -rn 'def __get__|def __set__|def __set_name__' packages/`` returns
       nothing. So the literal descriptor protocol (a class implementing
       ``__get__``/``__set__``) does not occur and cannot be mis-traversed.

    2. The decorator-based descriptor forms that DO occur
       (``@property`` / ``@staticmethod`` / ``@classmethod``) are NOT a distinct
       reachability gap. Under ``dead-code-maybe --seeds all`` on this repo,
       descriptor-decorated methods were flagged dead at ~60% — but UNdecorated
       plain methods were flagged dead at a comparable ~53%. The decorator is
       not load-bearing in the deadness: both are dead for the SAME general
       reason — intra-method ``obj.method()`` / ``obj.prop`` references resolve
       to ``external:...:unresolved`` ids rather than the method's real id (a
       general method-call-resolution gap, NOT a descriptor-protocol-specific
       traversal bug). A synthetic probe confirmed ``@property`` reads, a
       ``@staticmethod`` call, a ``@classmethod`` call, and a plain method call
       all resolve to unresolved external ids identically.

    Therefore we do NOT invent a speculative descriptor-protocol code path with
    no validating case (the plan and the closure-evidence discipline both forbid
    claiming "fixed" on absent evidence). Instead this test pins the family by
    *construction*: it asserts the property below — IF a descriptor-decorated
    method is reachable via a normal ``calls``/``dispatches_to`` edge, the
    reachability BFS reaches it through that edge exactly as it would for a plain
    method (i.e. the decorator does not break or special-case traversal). This is
    the only descriptor claim the corpus actually supports.
    """

    def test_decorator_does_not_break_reachability(self) -> None:
        """A property/staticmethod/classmethod target is reachable iff a normal
        reachability edge points at it — the decorator is traversal-transparent.

        Hand-built fixture: a caller with explicit ``calls`` edges into a
        ``@property`` getter, a ``@staticmethod``, a ``@classmethod`` and a
        plain method. Because the decorator carries no reachability-relevant
        semantics in the BFS, all four targets must be reachable from the caller
        identically. This documents the family's contract (descriptor dispatch
        rides the same ``calls`` edges as any method) without asserting a
        production fix that the corpus does not exhibit.
        """
        caller_id = "python:/m.py:1-9:caller:function"
        targets = {
            "color": "python:/m.py:2-3:Widget.color:method",      # @property
            "make": "python:/m.py:4-5:Widget.make:method",        # @staticmethod
            "build": "python:/m.py:6-7:Widget.build:method",      # @classmethod
            "plain": "python:/m.py:8-9:Widget.plain:method",      # undecorated
        }
        edges = [
            {"src": caller_id, "dst": tid, "type": "calls"}
            for tid in targets.values()
        ]
        reachable = _reachable_over_production_edges({caller_id}, edges)
        for label, tid in targets.items():
            assert tid in reachable, (
                f"descriptor-family target {label!r} unreachable despite a "
                "calls edge — the BFS must treat decorated targets identically "
                "to plain methods."
            )

    def test_no_descriptor_dunders_in_corpus(self) -> None:
        """Pin STEP-1 ground 1: zero ``__get__``/``__set__``/``__set_name__``
        descriptor dunders in the analyzed source tree.

        If a future change introduces a real descriptor protocol implementation,
        this test goes red — a deliberate trip-wire prompting a re-evaluation of
        whether the descriptor leg has become a real (non-vacuous) gap that
        needs its own synthetic reachability fixture or an analyzer fix.
        """
        packages = _repo_root() / "packages"
        dunders = ("def __get__", "def __set__", "def __set_name__")
        hits: list[str] = []
        for py in packages.rglob("*.py"):
            if "/tests/" in py.as_posix() or py.name.startswith("test_"):
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for dunder in dunders:
                if dunder in text:
                    hits.append(f"{py}: {dunder}")
        assert hits == [], (
            "descriptor dunders appeared in the corpus — the descriptor-protocol "
            "leg of INV-pohik may no longer be vacuous; add a real reachability "
            f"fixture/fix and re-scope this gate. Hits: {hits}"
        )


@pytest.mark.skipif(
    not os.environ.get("HYPERGUMBO_RUN_SELF_ANALYSIS"),
    reason="set HYPERGUMBO_RUN_SELF_ANALYSIS=1 to enable self-analysis ratchet",
)
def test_self_analysis_dispatch_signals(tmp_path: Path) -> None:
    """Env-gated self-analysis ratchet (NOT in the normal CI run).

    Corpus-coupled, generous thresholds — guarded behind
    ``HYPERGUMBO_RUN_SELF_ANALYSIS`` (cf. ``test_orphan_node_audit``) so drift
    can never break CI. Asserts two coarse facts that would regress if any
    dispatch family's traversal broke wholesale:

    * every route symbol that carries handler metadata has at least one
      outbound ``dispatches_to`` edge (route dispatch is wired end-to-end —
      F1's deliverable); and
    * the production-seed dead-code rate stays below a generous ceiling.

    The thresholds are intentionally loose; the synthetic family tests above are
    the precise regression guards. The route check explicitly skips (rather than
    passing vacuously) when the self-corpus has no resolvable route symbols, so
    a corpus with no HTTP routes never produces a meaningless green.
    """
    out = tmp_path / "self-analysis.json"
    run_behavior_map(
        repo_root=_repo_root(), out_path=out, include_sketch_precomputed=False
    )
    bm = json.loads(out.read_text())
    edges = bm["edges"]
    nodes = bm["nodes"]

    # Route dispatch must be wired: every route symbol whose handler the
    # route-handler linker could resolve has an outbound dispatches_to edge.
    # ``run_behavior_map`` does not populate ``behavior_map["features"]`` (that
    # is the feature-slice command path), so we assert against route SYMBOLS
    # directly. A route symbol is one with ``meta["framework_role"] == "route"``.
    srcs_with_dispatch = {
        e["src"] for e in edges if e["type"] == "dispatches_to"
    }
    route_syms = [
        n for n in nodes if (n.get("meta") or {}).get("framework_role") == "route"
    ]
    if not route_syms:
        pytest.skip("self-corpus has no route symbols; route leg is vacuous here")
    routes_without_dispatch = [
        n["id"] for n in route_syms if n["id"] not in srcs_with_dispatch
    ]
    # Generous: a route may legitimately lack a resolvable handler in-tree, so
    # we only require that SOME route dispatch exists (a wholesale collapse —
    # zero route->handler edges despite N route symbols — is the regression we
    # guard against), not that every single route resolves.
    assert len(routes_without_dispatch) < len(route_syms), (
        f"all {len(route_syms)} route symbols lack an outbound dispatches_to "
        "edge — route dispatch traversal collapsed wholesale (F1 regression)."
    )

    # Production-seed dead-code rate below a generous ceiling.
    production = [
        n
        for n in nodes
        if n.get("language") == "python"
        and n.get("kind") in ("function", "method")
    ]
    prod_ids = {n["id"] for n in production}
    exported = {
        n["id"]
        for n in production
        if (n.get("supply_chain") or {}).get("is_exported")
    }
    reachable = _reachable_over_production_edges(exported or prod_ids, edges)
    dead = [pid for pid in prod_ids if pid not in reachable]
    rate = len(dead) / len(prod_ids) if prod_ids else 0.0
    # Generous ceiling: the synthetic tests are the precise guard; this only
    # catches a wholesale traversal collapse.
    assert rate < 0.90, (
        f"production-seed dead-code rate {rate:.0%} exceeds the generous 90% "
        "ceiling — a dispatch family's traversal may have collapsed."
    )
