# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-karud: a taint finding may claim only what the analysis adjudicated.

WHAT WAS WRONG. ``propagate_taint_structural`` and ``propagate_taint_ddg``
emitted one ``TaintFlowFinding`` per (source call site, sink call site) pair
reachable in the call graph, and each such finding says "primitive P1 reached
primitive P2". For a flow the data-dependence walk actually confirmed
(``analysis_method == "ddg"``) that is an earned claim. For every other flow it
is not: inclusion rested on call-graph reachability alone, so the honest
statement is "symbol S reads {P1..Pn} and reaches zone Z via {Q1..Qm}" — one
fact, not n x m of them.

The cost was measured before this was written. On six repos (caddy, sops,
poetry, mitmproxy, knex, express) verify-claims reported **359 flows describing
78 (source_symbol, claim) situations — 4.60x**, with 281 of 359 rows (78%)
being additional rows about a situation already reported; caddy's
``cmd/commandfuncs.go cmdRun`` alone emitted 76. Two multipliers, not one:

* **2.87x pairs** — the |sources| x |sinks| product within one symbol. It
  scales with the CATALOGUE, not the code: adding a seventh browser global
  adds rows to a repo whose source did not change.
* **1.60x duplicate paths** — distinct call-graph routes to the SAME primitive
  pair are distinct rows, because ``_flow_identity`` keys on ``path``.

A fix keyed only on the pair product leaves the second, which is why the
grouping below keys on neither primitive.

WHY ADJUDICATED FLOWS ARE LEFT ALONE. 6 of the 359 census flows are ``ddg``.
Collapsing them would erase precision that a walk actually earned, for 1.7% of
the volume. They pass through as singleton situations.

WHAT IS DELIBERATELY IN THE GROUPING KEY. ``sanitized`` (a sanitized and an
unsanitized flow are different facts and the consumer filters on it) and
``source_boundary`` (WI-vazal split net_recv / ipc_recv / db_read inside the
one ``untrusted_input`` label precisely so "a request body reached the
database" and "a row read from the database reached the database" stay
separable; merging them would undo that). ``taint_label``, ``source_symbol``
and ``sink_zone`` are the situation itself.

WHY COLLAPSING BEFORE THE CONSUMER'S FILTERS IS SAFE. Every filter in
``verify_taint_claim`` — the label/zone ``constrained`` filter, the
``sanitized`` filter, and ``_source_scope``'s production exclusion (which reads
``source_symbol`` and nothing else) — tests a field that is IN the grouping
key. So each filter's verdict is constant across a group, and no group can be
half-included. That is asserted below rather than asserted-by-inspection.
"""

from __future__ import annotations

from hypergumbo_core.taint import (
    TaintFlowFinding,
    TaintSink,
    TaintSource,
    collapse_unadjudicated_flows,
    propagate_taint_structural,
)
from hypergumbo_core.cfg import select_ddg_targets
from hypergumbo_core.verify_claims import (
    Claim,
    TaintFlowConstraint,
    verify_taint_claim,
)


def _edge(src: str, dst: str) -> dict:
    return {
        "src": src, "dst": dst, "type": "calls",
        "is_resolved": not dst.endswith(":unresolved"),
    }


HANDLER = "py:a.py:1-20:handler:function"

_SOURCES = [
    TaintSource(taint_label="untrusted_input", module="socket",
                name="recv_into", kind="function"),
    TaintSource(taint_label="untrusted_input", module="urllib.request",
                name="urlopen", kind="function"),
    TaintSource(taint_label="untrusted_input", module="http.client",
                name="getresponse", kind="function"),
]
_SINKS = [
    TaintSink(zone="host_fs", trust_level="untrusted", module="os",
              name="remove", kind="function"),
    TaintSink(zone="host_fs", trust_level="untrusted", module="shutil",
              name="rmtree", kind="function"),
    TaintSink(zone="host_fs", trust_level="untrusted", module="os",
              name="truncate", kind="function"),
]


def _n_by_n_edges(n_sources: int, n_sinks: int) -> list[dict]:
    edges = []
    for s in _SOURCES[:n_sources]:
        edges.append(_edge(HANDLER, f"py:{s.module}:0-0:{s.name}:unresolved"))
    for k in _SINKS[:n_sinks]:
        edges.append(_edge(HANDLER, f"py:{k.module}:0-0:{k.name}:unresolved"))
    return edges


def _make_finding(**kw: object) -> TaintFlowFinding:
    base: dict[str, object] = {
        "taint_label": "untrusted_input",
        "source_symbol": HANDLER,
        "source_primitive": "recv_into",
        "source_module": "socket",
        "sink_symbol": "py:os:0-0:remove:unresolved",
        "sink_primitive": "remove",
        "sink_module": "os",
        "sink_zone": "host_fs",
        "sanitized": False,
        "confidence": "approximate",
        "analysis_method": "structural",
        "path": [HANDLER],
    }
    base.update(kw)
    return TaintFlowFinding(**base)  # type: ignore[arg-type]


class TestTheRepro:
    """The item's own synthetic case, measured on the pre-fix tree."""

    def test_three_sources_and_three_sinks_are_one_situation_not_nine(
        self,
    ) -> None:
        """Measured before the fix: **9 findings**. The code is one function
        reading three tainted values and calling three deleting primitives;
        which value reaches which primitive was never checked, so nine
        independent pair claims are eight claims more than the analysis has.
        """
        findings = propagate_taint_structural(
            _n_by_n_edges(3, 3), _SOURCES, _SINKS, [],
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.source_primitives == (
            "http.client.getresponse", "socket.recv_into",
            "urllib.request.urlopen",
        )
        assert f.sink_primitives == (
            "os.remove", "os.truncate", "shutil.rmtree",
        )
        assert f.collapsed_flow_count == 9

    def test_it_stops_scaling_with_the_catalogue(self) -> None:
        """1x1 -> 1, 2x2 -> 1, 3x3 -> 1. Pre-fix the same three fixtures
        measured 1, 4 and 9 — the product this item filed."""
        counts = [
            len(propagate_taint_structural(
                _n_by_n_edges(n, n), _SOURCES[:n], _SINKS[:n], [],
            ))
            for n in (1, 2, 3)
        ]
        assert counts == [1, 1, 1]

    def test_two_routes_to_the_same_pair_are_one_finding(self) -> None:
        """The 1.60x multiplier nobody had named. Pre-fix: **2 findings**,
        identical on every field except ``path``, because ``_flow_identity``
        keys on the path. ``path`` is documented as ONE witness route, not the
        route set, so a second witness is not a second fact."""
        a = "py:a.py:30-35:helper_a:function"
        b = "py:a.py:40-45:helper_b:function"
        edges = [
            _edge(HANDLER, "py:socket:0-0:recv_into:unresolved"),
            _edge(HANDLER, a), _edge(HANDLER, b),
            _edge(a, "py:os:0-0:remove:unresolved"),
            _edge(b, "py:os:0-0:remove:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, _SOURCES[:1], _SINKS[:1], [],
        )
        assert len(findings) == 1
        assert findings[0].collapsed_flow_count == 2


class TestWhatIsNotCollapsed:
    """The groups that must stay apart, each for a named reason."""

    def test_an_adjudicated_flow_keeps_its_pair_claim(self) -> None:
        """``ddg`` means the walk confirmed a reaching-def chain from the
        variable the source defines to a use at the sink call site. That IS a
        pair claim, and it is 1.7% of census volume — collapsing it would
        trade earned precision for almost no noise reduction."""
        out = collapse_unadjudicated_flows([
            _make_finding(analysis_method="ddg", confidence="precise"),
            _make_finding(analysis_method="ddg", confidence="precise",
                          sink_primitive="rmtree", sink_module="shutil",
                          sink_symbol="py:shutil:0-0:rmtree:unresolved"),
        ])
        assert len(out) == 2
        assert all(f.collapsed_flow_count == 1 for f in out)

    def test_an_adjudicated_flow_does_not_absorb_an_unadjudicated_one(
        self,
    ) -> None:
        """Same situation, one arm adjudicated and one not. Merging them would
        let a ``ddg_mixed`` row inherit a ``precise`` label, or the reverse."""
        out = collapse_unadjudicated_flows([
            _make_finding(analysis_method="ddg", confidence="precise"),
            _make_finding(analysis_method="ddg_mixed"),
            _make_finding(analysis_method="structural"),
        ])
        assert len(out) == 3

    def test_sanitized_and_unsanitized_do_not_merge(self) -> None:
        """A sanitized flow is EXCLUDED from the violation set. If the two
        merged, one group would have to be counted both ways."""
        out = collapse_unadjudicated_flows([
            _make_finding(sanitized=False),
            _make_finding(sanitized=True,
                          sanitized_by=("cryptography.fernet.Fernet.encrypt",)),
        ])
        assert len(out) == 2
        assert {f.sanitized for f in out} == {True, False}

    def test_two_source_boundaries_do_not_merge(self) -> None:
        """WI-vazal: three boundaries collapse into the one label
        ``untrusted_input``, and it kept the boundary so "a request body
        reached the database" and "a row read from the database reached the
        database" stay separable. Grouping across the boundary would undo
        exactly that."""
        out = collapse_unadjudicated_flows([
            _make_finding(source_boundary="net_recv"),
            _make_finding(source_boundary="db_read",
                          source_primitive="fetchone", source_module="sqlite3"),
        ])
        assert len(out) == 2
        assert {f.source_boundary for f in out} == {"net_recv", "db_read"}

    def test_two_source_symbols_do_not_merge(self) -> None:
        """The situation is per SYMBOL — that is the unit a reader acts on
        ("is this function a problem?")."""
        out = collapse_unadjudicated_flows([
            _make_finding(source_symbol=HANDLER),
            _make_finding(source_symbol="py:b.py:1-9:other:function"),
        ])
        assert len(out) == 2

    def test_two_sink_zones_do_not_merge(self) -> None:
        """The zone is half the claim being verified."""
        out = collapse_unadjudicated_flows([
            _make_finding(sink_zone="host_fs"),
            _make_finding(sink_zone="network", sink_primitive="send",
                          sink_module="socket"),
        ])
        assert len(out) == 2


class TestTheRecordStaysVerifiable:
    """Clause (a1): a reader must be able to confirm the match by lookup."""

    def test_primitives_are_module_qualified(self) -> None:
        """``remove`` alone is not checkable against a catalogue; ``os.remove``
        is. WI-joruv's point, applied to the set form: the emitted SYMBOL
        frequently does not carry the module a catalog entry declared."""
        out = collapse_unadjudicated_flows([_make_finding()])
        assert out[0].source_primitives == ("socket.recv_into",)
        assert out[0].sink_primitives == ("os.remove",)

    def test_a_primitive_with_no_module_stays_bare(self) -> None:
        """A YAML-declared entry may have no module. ``.remove`` would be a
        lookup key that matches nothing."""
        out = collapse_unadjudicated_flows(
            [_make_finding(source_module="", sink_module="")],
        )
        assert out[0].source_primitives == ("recv_into",)
        assert out[0].sink_primitives == ("remove",)

    def test_the_sink_symbols_travel(self) -> None:
        """"Reaches zone Z via {Q1..Qm}" is unactionable without the symbols
        the primitives were called from."""
        out = collapse_unadjudicated_flows([
            _make_finding(sink_symbol="py:os:0-0:remove:unresolved"),
            _make_finding(sink_symbol="py:shutil:0-0:rmtree:unresolved",
                          sink_primitive="rmtree", sink_module="shutil"),
        ])
        assert out[0].sink_symbols == (
            "py:os:0-0:remove:unresolved",
            "py:shutil:0-0:rmtree:unresolved",
        )

    def test_the_witness_scalar_is_always_a_member_of_its_tuple(self) -> None:
        """ONE FACT, TWO HOMES. The scalars survive as a stable single-row
        anchor (the ``path`` witness belongs to one of the pairs), so they must
        never disagree with the authoritative tuple."""
        findings = propagate_taint_structural(
            _n_by_n_edges(3, 3), _SOURCES, _SINKS, [],
        )
        for f in findings:
            assert f.source_module + "." + f.source_primitive in \
                f.source_primitives
            assert f.sink_module + "." + f.sink_primitive in f.sink_primitives
            assert f.sink_symbol in f.sink_symbols

    def test_a_directly_constructed_finding_has_populated_tuples(self) -> None:
        """The tuples default to ``()``. A dataclass default that reads as
        "no primitives" would make every hand-built finding silently empty to
        the consumers that now read the tuple — so they are derived in
        ``__post_init__`` instead of left to the caller."""
        f = TaintFlowFinding(
            taint_label="untrusted_input", source_symbol=HANDLER,
            source_primitive="recv_into", sink_symbol="py:os:0-0:remove:x",
            sink_primitive="remove", sink_zone="host_fs", sanitized=False,
            confidence="approximate", analysis_method="structural",
        )
        assert f.source_primitives == ("recv_into",)
        assert f.sink_primitives == ("remove",)
        assert f.sink_symbols == ("py:os:0-0:remove:x",)
        assert f.collapsed_flow_count == 1

    def test_the_serialized_form_carries_the_sets(self) -> None:
        """``to_dict`` is the record a consumer that never touches the
        dataclass sees. Emitting only the witness scalars there would put the
        over-claim back."""
        out = collapse_unadjudicated_flows([
            _make_finding(),
            _make_finding(sink_primitive="rmtree", sink_module="shutil",
                          sink_symbol="py:shutil:0-0:rmtree:unresolved"),
        ])
        d = out[0].to_dict()
        assert d["sink_primitives"] == ["os.remove", "shutil.rmtree"]
        assert d["source_primitives"] == ["socket.recv_into"]
        assert d["collapsed_flow_count"] == 2


class TestTheConsumerReportsBothNumbers:
    """The pair count is not hidden — it is disclosed beside the situation
    count. A reader who wants "how many source->sink pairs" must still be able
    to get it, or this fix would be trading one silent number for another."""

    def test_the_verdict_counts_situations_and_discloses_pairs(self) -> None:
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = propagate_taint_structural(
            _n_by_n_edges(3, 3), _SOURCES, _SINKS, [],
        )
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert "9 source" in verdict.details

    def test_a_one_pair_verdict_does_not_grow_a_clause(self) -> None:
        """The single-pair case is the common one; it must stay terse."""
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        verdict = verify_taint_claim(claim, [_make_finding()])
        assert verdict.verdict == "violated"
        assert "source->sink pair" not in verdict.details

    def test_a_long_set_is_capped_in_the_prose_and_whole_in_the_row(
        self,
    ) -> None:
        """caddy's ``cmdRun`` names four sink primitives on one claim and 32
        pairs on another, so this branch is production behaviour rather than a
        defensive one. The prose caps and says how many it dropped; the
        structured row is uncapped, because a consumer triaging
        programmatically must not silently see three of five."""
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [
            _make_finding(sink_primitive=name, sink_module="os",
                          sink_symbol=f"py:os:0-0:{name}:unresolved")
            for name in ("remove", "truncate", "rename", "chmod", "mkdir")
        ]
        verdict = verify_taint_claim(claim, collapse_unadjudicated_flows(
            findings,
        ))
        assert "(+2 more)" in verdict.details
        assert verdict.evidence[0]["sink_primitives"] == [
            "os.chmod", "os.mkdir", "os.remove", "os.rename", "os.truncate",
        ]

    def test_the_evidence_row_names_the_sets(self) -> None:
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = propagate_taint_structural(
            _n_by_n_edges(3, 3), _SOURCES, _SINKS, [],
        )
        row = verify_taint_claim(claim, findings).evidence[0]
        assert row["sink_primitives"] == [
            "os.remove", "os.truncate", "shutil.rmtree",
        ]
        assert row["collapsed_flow_count"] == 9


class TestTheVerdictIsPreserved:
    """A shape change that flipped a verdict would be a different fix."""

    def test_collapsing_never_changes_whether_a_violation_exists(self) -> None:
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        raw = [
            _make_finding(),
            _make_finding(sink_primitive="rmtree", sink_module="shutil"),
        ]
        assert verify_taint_claim(claim, raw).verdict == \
            verify_taint_claim(claim, collapse_unadjudicated_flows(raw)).verdict

    def test_a_group_is_never_half_excluded_by_the_production_filter(
        self,
    ) -> None:
        """``_source_scope`` reads ``source_symbol``, which is IN the grouping
        key — so production and test flows can never land in one group. This
        is the property that makes collapsing-before-filtering safe, and it is
        asserted rather than reasoned about."""
        out = collapse_unadjudicated_flows([
            _make_finding(source_symbol="py:app/handler.py:1-9:h:function"),
            _make_finding(source_symbol="py:tests/test_h.py:1-9:t:function"),
        ])
        assert len(out) == 2
        claim = Claim(
            id="TF-1", text="no untrusted_input to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input",
                prohibited_sink_zone="host_fs",
            ),
        )
        verdict = verify_taint_claim(claim, out)
        assert verdict.evidence_count == 1
        assert sum(verdict.excluded_flows.values()) == 1


class TestEveryReaderOfTheWitnessScalarMoved:
    """A field whose meaning changed leaves stale readers behind (L8)."""

    def test_the_ddg_target_selector_reads_every_sink_symbol(self) -> None:
        """``select_ddg_targets`` picked DDG targets off ``finding.sink_symbol``
        — the witness. After the collapse a finding stands for every sink
        symbol it reached, so selecting on the witness would silently drop the
        rest of the group from data-flow coverage, shrinking the analysis that
        decides whether the next run can adjudicate the pair at all.

        Honest scope: this function has NO production caller today (tests
        only), so this is a latent reader, not an observed regression.
        """
        collapsed = collapse_unadjudicated_flows([
            _make_finding(sink_symbol="py:os:0-0:remove:unresolved"),
            _make_finding(sink_symbol="py:shutil:0-0:rmtree:unresolved",
                          sink_primitive="rmtree", sink_module="shutil"),
        ])
        result = select_ddg_targets(edges=[], taint_findings=collapsed)
        assert "py:os:0-0:remove:unresolved" in result.taint_relevant
        assert "py:shutil:0-0:rmtree:unresolved" in result.taint_relevant


class TestTheCollapseDoesNotMutateItsInput:
    """It is a public function; a caller that keeps its list must keep it."""

    def test_the_originals_are_untouched(self) -> None:
        a = _make_finding()
        b = _make_finding(sink_primitive="rmtree", sink_module="shutil",
                          sink_symbol="py:shutil:0-0:rmtree:unresolved")
        out = collapse_unadjudicated_flows([a, b])
        assert out[0] is not a
        assert a.collapsed_flow_count == 1
        assert a.sink_primitives == ("os.remove",)
        assert b.sink_primitives == ("shutil.rmtree",)


class TestTheResidualThisDoesNotClose:
    """What is still true after this ships, stated so it is not re-derived."""

    def test_a_collapsed_finding_still_reports_one_witness_path(self) -> None:
        """``path`` is one route among several and this does not change that.
        A consumer needing every route still has no field that gives it."""
        a = "py:a.py:30-35:helper_a:function"
        b = "py:a.py:40-45:helper_b:function"
        edges = [
            _edge(HANDLER, "py:socket:0-0:recv_into:unresolved"),
            _edge(HANDLER, a), _edge(HANDLER, b),
            _edge(a, "py:os:0-0:remove:unresolved"),
            _edge(b, "py:os:0-0:remove:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, _SOURCES[:1], _SINKS[:1], [],
        )
        assert len(findings[0].path) == 2
        assert findings[0].collapsed_flow_count == 2

    def test_which_pair_is_real_is_still_not_decided(self) -> None:
        """This makes the CLAIM honest; it does not make the analysis
        precise. Clause (a2) is still unmet for every unadjudicated flow, and
        removal remains unsound here because the call graph emits one edge per
        (caller, callee) — so ``line`` is *a* line, not every line."""
        findings = propagate_taint_structural(
            _n_by_n_edges(3, 3), _SOURCES, _SINKS, [],
        )
        assert findings[0].analysis_method == "structural"
        assert findings[0].confidence == "approximate"
