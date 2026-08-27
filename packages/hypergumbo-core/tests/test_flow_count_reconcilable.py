# SPDX-License-Identifier: AGPL-3.0-or-later
"""``collapsed_flow_count`` must be reconcilable against the call sites it
counts (INV-kakad).

WHAT THE NUMBER ACTUALLY IS, established by live repro on the three
repositories the item names. It is the count of (SOURCE CALL SITE, SINK CALL
SITE) pairs where the sink site is REACHABLE from the source site's caller —
``sink_node in reachable`` in :func:`propagate_taint_structural`. It is not a
count of value flows, and it is not the product of the distinct primitive
*names* the record prints.

WHY THREE INDEPENDENT REFUTERS COULD NOT RECONCILE IT. The record emitted the
multiplier beside ``source_primitives`` / ``sink_primitives`` — DISTINCT NAMES —
and nothing about site multiplicity. So two rows with identical primitives
legitimately carry different counts, and the record cannot say why::

    shellcheck  3 rows at 1 source name x 1 sink name -> 1
                1 row  at 1 source name x 1 sink name -> 2   <- unexplainable

The 2 is correct. Traced to the edges: ``striptests`` has ONE env read (at the
file node) and TWO ``redirect.>`` call sites — the file node, and ``sponge``,
which is reachable from it. One source site x two reachable sink sites = 2.
The sink CALLEE is identical for both, and the callee is all the record kept,
so the second site was invisible.

kamaraflow's 8 reconciles from names alone (2 sources x 4 sinks) and ArkLib's 6
does not (1 x 3); the difference is site multiplicity in both directions.

THE FIX IS DISCLOSURE, NOT ARITHMETIC. No double-counting was found:
``source_callers`` is built one entry per EDGE and edges are deduplicated on
``(src, dst, edge_type)`` (INV-vukiv), so a site cannot enter twice. Every pair
traced corresponds to a distinct (source edge, sink edge) with a real
reachability path. What was missing is the sink CALL SITE — the caller node —
which this module pins into the emitted finding so the multiplier can be
checked against the graph.

WHAT THIS DOES NOT CLAIM. The item's "inflated in at least one direction" is
NOT established and this file does not assert it. Separately, and not fixed
here: for ``structural`` the pair test is pure call-graph reachability with no
check that a value crosses, so a rate computed over this count is a rate over
reachability pairs rather than over value-flow claims. That is an argument
about what the ROW denominator means, tracked on the item, not a defect in the
propagator — ADR-0017 defines ``structural`` as exactly this approximation.
"""

from hypergumbo_core.taint import (
    TaintSanitizer,
    TaintSink,
    TaintSource,
    propagate_taint_structural,
)

SOURCES = [TaintSource(
    taint_label="plaintext", module="crypto", name="decrypt", kind="function",
)]
SINKS = [TaintSink(
    zone="relay", trust_level="untrusted", module="net", name="send",
    kind="function",
)]
SANITIZERS: list[TaintSanitizer] = []

_FILE = "bash:striptests:1-1:file:file"
_FN = "bash:striptests:7-11:sponge:function"
_SRC_CALLEE = "python:external:0-0:decrypt:unresolved"
_SINK_CALLEE = "python:external:0-0:send:unresolved"


def _edge(src: str, dst: str, line: int) -> dict:
    return {"src": src, "dst": dst, "is_resolved": False,
            "type": "calls", "line": line}


def _striptests_edges() -> list[dict]:
    """The shape traced live in shellcheck: ONE source read at the file node,
    and the SAME sink callee invoked from two different caller sites, the
    second reachable from the first."""
    return [
        _edge(_FILE, _SRC_CALLEE, 1),    # the one env read
        _edge(_FILE, _SINK_CALLEE, 2),   # sink site 1 — the file node itself
        _edge(_FILE, _FN, 3),            # ... which reaches `sponge` ...
        _edge(_FN, _SINK_CALLEE, 4),     # sink site 2 — inside `sponge`
    ]


class TestTheMultiplierIsCheckableAgainstTheGraph:

    def test_two_sites_on_one_callee_are_two_pairs(self) -> None:
        """The count itself is correct and stays correct — this is the
        non-vacuity guard for everything below. ``propagate_taint_structural``
        collapses internally, so it returns ONE situation carrying the pair
        count, which is exactly the shipped shape the refuters read."""
        findings = propagate_taint_structural(
            _striptests_edges(), SOURCES, SINKS, SANITIZERS,
        )
        assert len(findings) == 1
        assert findings[0].collapsed_flow_count == 2

    def test_the_sink_call_sites_are_recorded(self) -> None:
        """THE DEFECT. Before this, both findings carried the same
        ``sink_symbols`` (one callee) and nothing distinguished the two sites,
        so a reader holding the record could not get from 1 source name x 1
        sink name to 2."""
        collapsed = propagate_taint_structural(
            _striptests_edges(), SOURCES, SINKS, SANITIZERS,
        )[0]
        assert collapsed.sink_call_sites == (
            (_FILE, _SINK_CALLEE), (_FN, _SINK_CALLEE),
        )

    def test_the_count_reconciles_from_the_record_alone(self) -> None:
        """The invariant, stated as the arithmetic a reader can now perform:
        the multiplier never exceeds |source primitives| x |sink call sites|,
        and reachability explains any shortfall."""
        collapsed = propagate_taint_structural(
            _striptests_edges(), SOURCES, SINKS, SANITIZERS,
        )[0]
        bound = len(collapsed.source_primitives) * len(collapsed.sink_call_sites)
        assert collapsed.collapsed_flow_count <= bound
        assert collapsed.collapsed_flow_count == 2 and bound == 2

    def test_the_sites_survive_serialization(self) -> None:
        """A consumer reads the record, not the dataclass."""
        collapsed = propagate_taint_structural(
            _striptests_edges(), SOURCES, SINKS, SANITIZERS,
        )[0]
        assert collapsed.to_dict()["sink_call_sites"] == [
            [_FILE, _SINK_CALLEE], [_FN, _SINK_CALLEE],
        ]

    def test_one_caller_calling_many_sinks_is_many_sites(self) -> None:
        """THE SHAPE THE FIRST FIX GOT WRONG, kept as its own case.

        kamaraflow's ``train_script`` calls FOUR different sinks from ONE
        caller, so a ``sink_call_sites`` that recorded the CALLER alone
        collapsed four sites to one and left ``collapsed_flow_count=8``
        unreconcilable against a bound of 2. Recording the (caller, callee)
        PAIR is what makes both multiplicities visible — and only the live
        re-run on the three repositories caught it, because every unit test
        written for the striptests shape passed either way.
        """
        second_sink = "python:external:0-0:send2:unresolved"
        sinks = SINKS + [TaintSink(
            zone="relay", trust_level="untrusted", module="net",
            name="send2", kind="function",
        )]
        findings = propagate_taint_structural(
            [_edge(_FILE, _SRC_CALLEE, 1),
             _edge(_FILE, _SINK_CALLEE, 2),
             _edge(_FILE, second_sink, 3)],
            SOURCES, sinks, SANITIZERS,
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.collapsed_flow_count == 2
        # Sorted, and ``send2`` precedes ``send`` because '2' < ':' — the
        # union is order-stable, not insertion-ordered.
        assert f.sink_call_sites == (
            (_FILE, second_sink), (_FILE, _SINK_CALLEE),
        )
        assert f.collapsed_flow_count <= (
            len(f.source_primitives) * len(f.sink_call_sites)
        )

    def test_an_uncollapsed_finding_records_its_own_site(self) -> None:
        """One source, one sink, one site — the singleton case must still name
        the site rather than leaving the field empty, or "no sites recorded"
        and "one site" become the same reading."""
        raw = propagate_taint_structural(
            [_edge(_FILE, _SRC_CALLEE, 1), _edge(_FILE, _SINK_CALLEE, 2)],
            SOURCES, SINKS, SANITIZERS,
        )
        assert len(raw) == 1
        assert raw[0].sink_call_sites == ((_FILE, _SINK_CALLEE),)
        assert raw[0].collapsed_flow_count == 1
