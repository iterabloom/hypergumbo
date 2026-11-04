# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tier filter is ONE function, shared by ``run_survey`` and the per-PR
claims-coverage guard (WI-jizam).

It used to be an inline block of ``run_survey``. The guard, which exists to
give the cron's coverage answer per PR, could not run it, so a generated
``_pb2.py`` the pipeline drops as DERIVED stayed "analysed" for the guard and
the cross-package call into it -- the cron's ``hypergumbo_core.scip._generated.scip_pb2``
on dev acf67df4cb -- was suppressed as first-party instead of named
(INV-hotib). These pin the block's three behaviours at the function, so the
two callers cannot drift: a DERIVED file leaves with every edge touching it,
``--max-tier 4`` keeps everything, and a route-bearing DERIVED symbol is
promoted rather than dropped.
"""
from __future__ import annotations

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.limits import Limits
from hypergumbo_core.tier_filter import apply_tier_filter


def _sym(path: str, name: str, kind: str, tier: int, line: int = 1) -> Symbol:
    sym = Symbol(
        id=f"python:{path}:{line}-{line + 1}:{name}:{kind}",
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=Span(start_line=line, end_line=line + 1, start_col=0, end_col=0),
        origin="python",
        origin_run_id="uuid:test",
    )
    sym.supply_chain_tier = tier
    return sym


def _edge(src: str, dst: str, edge_type: str = "calls") -> Edge:
    return Edge.create(
        src=src, dst=dst, edge_type=edge_type, line=1,
        origin="python", origin_run_id="uuid:test", confidence=0.9,
    )


class TestApplyTierFilter:
    def test_a_derived_file_leaves_with_every_edge_touching_it(self) -> None:
        app_file = _sym("src/app.py", "file", "file", 1)
        app_fn = _sym("src/app.py", "f", "function", 1, line=3)
        gen_file = _sym("src/gen_pb2.py", "file", "file", 4)
        gen_cls = _sym("src/gen_pb2.py", "Index", "class", 4, line=3)
        external = "python:fakemod:0-0:run:unresolved"
        edges = [
            _edge(app_fn.id, gen_cls.id),            # into the dropped file: goes
            _edge(gen_cls.id, app_fn.id),            # out of the dropped file: goes
            _edge(app_fn.id, external),              # dst never had a node: kept
            _edge(app_file.id, app_fn.id, "contains"),
        ]
        limits = Limits()
        symbols, kept = apply_tier_filter(
            [app_file, app_fn, gen_file, gen_cls], edges, None, limits,
        )
        assert {s.id for s in symbols} == {app_file.id, app_fn.id}
        assert [(e.src, e.dst) for e in kept] == [
            (app_fn.id, external), (app_file.id, app_fn.id),
        ]
        assert limits.tier_filtered_files == ["src/gen_pb2.py"]
        assert limits.max_tier_applied == 3

    def test_max_tier_four_keeps_everything_and_records_nothing(self) -> None:
        gen_file = _sym("src/gen_pb2.py", "file", "file", 4)
        gen_cls = _sym("src/gen_pb2.py", "Index", "class", 4, line=3)
        edges = [_edge(gen_file.id, gen_cls.id, "contains")]
        limits = Limits()
        symbols, kept = apply_tier_filter([gen_file, gen_cls], edges, 4, limits)
        assert symbols == [gen_file, gen_cls]
        assert kept == edges
        assert limits.tier_filtered_files == []
        assert limits.max_tier_applied is None

    def test_a_route_bearing_derived_symbol_is_promoted_not_dropped(self) -> None:
        by_role = _sym("src/gen_api.py", "handler", "function", 4)
        by_role.meta = {"framework_role": "route"}
        by_concept = _sym("src/gen_api.py", "other", "function", 4, line=5)
        by_concept.meta = {"concepts": [{"concept": "route"}]}
        plain = _sym("src/gen_api.py", "helper", "function", 4, line=9)
        symbols, _kept = apply_tier_filter(
            [by_role, by_concept, plain], [], None, Limits(),
        )
        assert symbols == [by_role, by_concept]
        for promoted in symbols:
            assert promoted.supply_chain_tier == 1
            assert promoted.supply_chain_reason == "route promoted from derived"
            assert promoted.supply_chain_classified is True
