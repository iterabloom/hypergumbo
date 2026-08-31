# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lipis: a source that provably reads nothing must not mint taint.

The sink side already refuses a call site that hands its value to nothing
(``> /dev/null``, INV-nular/INV-kosur). A SOURCE site that takes its value
from nothing is the same argument pointed the other way, and nothing asked it.

THE MEASURED CASE. ``go.yaml`` files ``bufio.{NewScanner,NewReader}`` as
``ipc_recv`` — which is in ``AUTO_SOURCE_LABEL_MAP``, so every call site mints
``untrusted_input`` — on the note *"When wrapping os.Stdin"*, a condition no
catalogue row can enforce because the row sees the callee and the answer is in
the ARGUMENT. Measured on the shipped CLI before the fix::

    func leakBuffer(s string) {
        sc := bufio.NewScanner(strings.NewReader(s))
        sc.Scan()
        exec.Command("sh", "-c", sc.Text())
    }

    untrusted-input-no-subprocess   violated
    1 flow [untrusted_input confidence: PRECISE] [origins: 1 ipc_recv]

A fully-confident false positive: the DDG confirmed a real data dependence, and
the *label on the source* was the lie. Confidence measures the walk, not the
catalogue, so a precise verdict over a wrong source reads as the strongest kind
of finding the tool emits.

WHY THE POPULATION MATTERS MORE THAN THIS FIXTURE. Of the 83 bare-local
``bufio.New*`` sites whose origin the shipped reaching-def solver resolves
across the ADR-0049 cohort's Go repositories: **63 wrap an ``os.Open`` handle**
(``fs_read``, deliberately not a taint source), 3 an HTTP body, 1 a buffer, and
**zero wrap ``os.Stdin``**. The row's own stated condition holds nowhere in
that population.

WHAT THIS DOES NOT FIX, and the docstring says it rather than implying
completeness: a bare local is 64.8% of the measured sites, its boundary is the
ORIGIN of a variable, and that is a dataflow question. It stamps nothing and is
untouched. INV-zumin's ruling forbids resolving it by emitting BOTH boundaries,
so the abstention stays an abstention.
"""

from unittest import mock

from hypergumbo_core import io_boundary, taint
from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    AUTO_SOURCE_LABEL_MAP,
    TaintSink,
    TaintSource,
    propagate_taint_structural,
)

_CALLER = "go:m.go:1-9:leak:function"

_SOURCE = TaintSource(
    taint_label="untrusted_input", module="bufio", name="NewScanner",
    kind="function", source_boundary="ipc_recv",
)
_SINK = TaintSink(
    zone="subprocess", trust_level="untrusted", module="os/exec",
    name="Command", kind="function",
)


def _edges(target_kind: "str | None") -> list[dict[str, object]]:
    meta: dict[str, object] = {"call_construct": "method"}
    if target_kind is not None:
        meta["io_target_kind"] = target_kind
    return [
        {
            "src": _CALLER,
            "dst": "go:bufio:0-0:NewScanner:external_symbol",
            "type": "calls", "is_resolved": False, "line": 3, "meta": meta,
        },
        {
            "src": _CALLER,
            "dst": "go:os/exec:0-0:Command:external_symbol",
            "type": "calls", "is_resolved": False, "line": 5,
            "meta": {"call_construct": "method"},
        },
    ]


def _flows(target_kind: "str | None") -> list:
    return list(propagate_taint_structural(
        _edges(target_kind), [_SOURCE], [_SINK], [], language="go",
    ))


def test_an_in_memory_reader_mints_no_source() -> None:
    """``bufio.NewScanner(strings.NewReader(s))`` reads nothing external."""
    assert _flows("in_memory") == []


def test_stdin_still_mints_a_source() -> None:
    """FAIL-CLOSED CONTROL. The row's own stated case must survive.

    This is the one shape the catalogue note was written for, and a change in
    the removal direction that silenced it would be trading a false positive
    for a false negative on a security analysis.
    """
    flows = _flows("std_stream")
    assert len(flows) == 1
    assert flows[0].taint_label == "untrusted_input"


def test_an_unstamped_site_is_untouched() -> None:
    """SECOND FAIL-CLOSED CONTROL, and the 64.8% case.

    A bare local stamps nothing, because its boundary is the origin of a
    variable. Absence of the key must classify exactly as before — it is the
    state of every Go edge in every behavior map written before this key was
    stamped, and a gate whose default silenced findings would be a
    false-negative generator.
    """
    assert len(_flows(None)) == 1


def test_a_discarding_target_also_refuses_on_the_source_side() -> None:
    """The vocabulary is SHARED with the sink side, not copied.

    ``null_device`` is the sink side's member; asking it here proves both
    directions read one set rather than two that can drift.
    """
    assert _flows("null_device") == []


def test_a_file_handle_mints_no_source() -> None:
    """WI-lipis's SECOND deliverable, and the LARGER of its two false-source
    populations: 63 of the 83 resolved bare-local sites wrap an ``os.Open``
    handle, against the 19.1% in-memory bucket the first deliverable's estimate
    counted."""
    assert _flows("host_path") == []


def test_the_file_case_is_refused_FOR_ITS_BOUNDARY_not_for_crossing_nothing(
) -> None:
    """THE DISCRIMINATING ASSERTION for the generalisation, and the reason a
    second gate was not added beside the first.

    ``host_path`` is NOT in the non-crossing set and must not be: reading a
    file is a real crossing, and the sink side depends on that (a ``host_path``
    write is exactly what ``fs_write`` claims count). The source side refuses
    it anyway, because the boundary that crossing carries -- ``fs_read`` -- is
    absent from ``AUTO_SOURCE_LABEL_MAP`` by design. A gate that conflated the
    two questions would have had to either call a file read "no crossing"
    (breaking every fs claim) or keep minting from it (the defect).
    """
    assert not io_boundary.target_kinds_cross_no_boundary(("host_path",))
    known, boundary = io_boundary.read_boundary_for_target_kind("host_path")
    assert (known, boundary) == (True, "fs_read")
    assert boundary not in AUTO_SOURCE_LABEL_MAP
    assert _flows("host_path") == []


def test_a_site_the_vocabulary_does_not_know_still_mints() -> None:
    """THIRD FAIL-CLOSED CONTROL. ``unresolved`` is a real read to a place the
    analyzer cannot name, so the catalogue row decides and the finding stays.
    An unknown kind must never be read as a known-harmless one."""
    assert io_boundary.read_boundary_for_target_kind("unresolved") == (
        False, None,
    )
    assert len(_flows("unresolved")) == 1


def test_one_minting_site_among_several_keeps_the_flow() -> None:
    """INV-vukiv, on the source side. A collapsed edge whose sites disagree --
    one wrapping a file, one wrapping stdin -- must keep minting. Silencing a
    real receive on the strength of a DIFFERENT call site is the
    false-negative trade INV-vukiv's collapse rule exists to refuse."""
    edges = _edges(None)
    edges[0]["meta"] = {
        "call_construct": "method",
        "io_target_kind_values": ["host_path", "std_stream"],
    }
    flows = list(propagate_taint_structural(
        edges, [_SOURCE], [_SINK], [], language="go",
    ))
    assert len(flows) == 1


def test_widening_the_vocabulary_moves_the_source_side_too() -> None:
    """THE GATE THAT CATCHES THE NEXT AUTHOR.

    Every test above pins a fixed value, so all of them would still pass if
    someone re-implemented the source clause against its own literal set. This
    widens the vocabulary at its single home and asserts the source arm follows
    it to a value it did not know about — with the unpatched assertion after,
    so the arms are proven to differ rather than merely agree.
    """
    widened = frozenset(io_boundary._NON_CROSSING_TARGET_KINDS | {"std_stream"})
    with mock.patch.object(io_boundary, "_NON_CROSSING_TARGET_KINDS", widened):
        assert _flows("std_stream") == []
    assert len(_flows("std_stream")) == 1


def test_both_propagators_consult_the_source_predicate() -> None:
    """One question, one home — the ddg arm must not drift from the structural.

    Recording the consultation rather than driving the ddg arm end to end,
    because a synthetic Go DDG here would test the fixture, not the gate.
    """
    seen: list[str] = []
    real = taint._source_call_can_mint_taint

    def recorder(edge: dict) -> bool:
        seen.append(str(edge.get("dst")))
        return real(edge)

    edges = _edges("std_stream")
    with mock.patch.object(taint, "_source_call_can_mint_taint", recorder):
        seen.clear()
        propagate_taint_structural(
            edges, [_SOURCE], [_SINK], [], language="go",
        )
        structural_saw = list(seen)
        seen.clear()
        taint.propagate_taint_ddg(
            _ddg_edge(), edges, [_SOURCE], [_SINK], [],
            ddg_symbols={_CALLER}, language="go",
        )
        ddg_saw = list(seen)

    assert "go:bufio:0-0:NewScanner:external_symbol" in structural_saw
    assert "go:bufio:0-0:NewScanner:external_symbol" in ddg_saw


def _ddg_edge() -> list:
    """One synthetic DdgEdge: ``propagate_taint_ddg`` returns early on none."""
    return [DdgEdge(
        variable="sc", def_block="b0", def_line=3,
        use_block="b0", use_line=5, symbol_id=_CALLER,
    )]
