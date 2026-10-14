# SPDX-License-Identifier: AGPL-3.0-or-later
"""A deferred crossing qualifies the TAINT verdicts its shadowed boundary feeds.

INV-fogum. ADR-0049 ruling 2 clause 3 says a deferred-crossing call site (a
server launch, a listener, a composed-but-unevaluated query) must qualify the
clean verdicts over the data boundary it shadows. WI-nosah wired that into the
BOUNDARY arm only. Reproduced on the shipped CLI at dev ba9277f1cf, in ONE
invocation over this Go program::

    type app struct{}
    func (app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
        os.WriteFile("/var/data/"+r.URL.Path, []byte("x"), 0600)
    }
    func main() { http.ListenAndServe(":8080", app{}) }

    untrusted-input-no-host-fs   confirmed               caveats []
    no-net-recv                  confirmed_with_caveats  [deferred_crossing]

The boundary verdict says request data arrives where it cannot look; the taint
verdict, two lines later, certifies that no network input reaches the
filesystem, over a handler that writes to a request-chosen path. That is
INV-nuhun's defect (the two arms disagree about one call in one run) for this
disclosure.

THE SCOPE RIDES ON THE TAINT ARM'S OWN KEY, as INV-nuhun's did: a boundary B's
shadow qualifies a taint claim exactly when ``AUTO_SOURCE_LABEL_MAP[B]`` is the
claim's source label. ``net_recv`` and ``db_read`` both derive
``untrusted_input``, so a Django app with a launch and a composed QuerySet owes
the reader TWO disclosures, one per boundary, and they must not fold into one
sentence that names only whichever came first.

REAL CATALOGUES, CAPTURED EDGE SHAPES, as in the boundary arm's file
(``test_deferred_crossing_boundary.py``). Each qualifying test has a control
that removes the shadow and must change the answer; a qualifying test with no
such control would pass over a taint arm that never reads the shadow at all.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import hypergumbo_core.io_boundary as io_boundary
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP, TaintFlowFinding
from hypergumbo_core.verify_claims import (
    Claim,
    TaintFlowConstraint,
    _ARM_TAINT,
    _deferred_crossing_caveat,
    _merge_caveat,
    compute_boundary_coverage,
    verify_taint_claim,
)

#: Captured from ``hypergumbo survey`` over a Go accept loop (the boundary
#: arm's ``LISTEN_EDGE``).
GO_LISTEN_EDGE = {
    "src": "go:server.go:27-42:serve:function",
    "dst": "go:net:0-0:Listen:external_symbol",
    "type": "calls",
    "line": 29,
}
#: Captured from ``hypergumbo survey`` over a Django fixture (the boundary
#: arm's ``COMPOSE_EDGE``).
PY_COMPOSE_EDGE = {
    "src": "python:app.py:8-9:purge:function",
    "dst": "python:django.db.models:0-0:filter:external_symbol",
    "type": "calls",
    "line": 9,
}


def _taint_claim(label: str = "untrusted_input", zone: str = "host_fs") -> Claim:
    return Claim(
        id="T", text="t",
        constraint_taint_flow=TaintFlowConstraint(
            source_taint=label, prohibited_sink_zone=zone,
        ),
    )


def _coverage(edges, catalogs):
    return compute_boundary_coverage(edges, set(catalogs), catalogs)


def _go_without_deferrals():
    cat = load_catalog("go")
    assert any(p.boundary == "net_listen" for p in cat.primitives)
    return replace(cat, primitives=[
        replace(p, boundary="net_recv") if p.boundary == "net_listen" else p
        for p in cat.primitives
    ])


def _deferred(verdict) -> list[dict]:
    return [c for c in verdict.caveats if c["kind"] == "deferred_crossing"]


class TestTheTaintArmReadsTheShadow:

    def test_a_listener_qualifies_a_clean_untrusted_input_verdict(self) -> None:
        """The filed repro's shape: no flow, a listener, a clean claim."""
        coverage = _coverage([GO_LISTEN_EDGE], {"go": load_catalog("go")})
        assert coverage.deferred_crossing_sites == {"net_recv": ["net.Listen"]}, (
            "reach: the shadow must be populated or every assertion below is vacuous"
        )
        verdict = verify_taint_claim(_taint_claim(), [], coverage=coverage)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        [cav] = _deferred(verdict)
        assert cav["boundary"] == "net_recv"
        assert cav["arm"] == _ARM_TAINT
        assert cav["entries"] == ["net.Listen"]
        assert "net.Listen" in cav["detail"]

    def test_control_without_the_shadow_the_same_run_is_a_bare_confirmed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING. With the shadow map emptied, the
        same edge and the same claim come back plain ``confirmed``. If this ever
        stops differing from the test above, the taint arm has stopped reading
        the shadow and INV-fogum is back."""
        monkeypatch.setattr(io_boundary, "DEFERRED_CROSSING_SHADOWS", {})
        coverage = _coverage([GO_LISTEN_EDGE], {"go": load_catalog("go")})
        verdict = verify_taint_claim(_taint_claim(), [], coverage=coverage)
        assert verdict.verdict == "confirmed", verdict.verdict
        assert _deferred(verdict) == []

    def test_before_the_retag_the_listener_shadowed_nothing(self) -> None:
        coverage = _coverage([GO_LISTEN_EDGE], {"go": _go_without_deferrals()})
        verdict = verify_taint_claim(_taint_claim(), [], coverage=coverage)
        assert _deferred(verdict) == []

    def test_the_database_twin_qualifies_it_too(self) -> None:
        """``db_compose`` shadows ``db_read``, which also derives
        ``untrusted_input``: a composed QuerySet read elsewhere is data of this
        label arriving where no call site names it."""
        coverage = _coverage([PY_COMPOSE_EDGE], {"python": load_catalog("python")})
        assert coverage.deferred_crossing_sites.get("db_read"), "reach"
        verdict = verify_taint_claim(_taint_claim(), [], coverage=coverage)
        [cav] = _deferred(verdict)
        assert cav["boundary"] == "db_read"
        assert cav["entries"] == coverage.deferred_crossing_sites["db_read"]

    def test_two_shadowed_boundaries_give_two_disclosures(self) -> None:
        """One label, two boundaries. Folding them into one caveat would keep
        the first boundary's name over the second's sites, a sentence that is
        wrong about what it lists."""
        coverage = _coverage(
            [GO_LISTEN_EDGE, PY_COMPOSE_EDGE],
            {"go": load_catalog("go"), "python": load_catalog("python")},
        )
        assert set(coverage.deferred_crossing_sites) == {"db_read", "net_recv"}
        verdict = verify_taint_claim(_taint_claim(), [], coverage=coverage)
        by_boundary = {c["boundary"]: c for c in _deferred(verdict)}
        assert set(by_boundary) == {"db_read", "net_recv"}
        assert by_boundary["net_recv"]["entries"] == ["net.Listen"]
        assert by_boundary["db_read"]["entries"] == (
            coverage.deferred_crossing_sites["db_read"]
        )


class TestTheScopeDoesNotWiden:

    @pytest.mark.parametrize("label", sorted(
        {"host_secret", "host_description", "plaintext"}
        - {AUTO_SOURCE_LABEL_MAP["net_recv"]}
    ))
    def test_a_label_the_shadowed_boundary_does_not_derive_is_untouched(
        self, label: str,
    ) -> None:
        """A listener says nothing about whether a secret reaches the
        filesystem; qualifying that claim would be the over-withholding ADR-0049
        clause 3 refuses."""
        coverage = _coverage([GO_LISTEN_EDGE], {"go": load_catalog("go")})
        verdict = verify_taint_claim(_taint_claim(label), [], coverage=coverage)
        assert _deferred(verdict) == []

    def test_a_violated_verdict_does_not_acquire_it(self) -> None:
        """Coverage gates the ALL-CLEAR and nothing else: a found flow is
        evidence regardless of what else arrived unseen."""
        finding = TaintFlowFinding(
            taint_label="untrusted_input", source_symbol="s:1",
            source_primitive="net.Conn.Read", sink_symbol="d:1",
            sink_primitive="os.WriteFile", sink_zone="host_fs",
            sanitized=False, confidence="approximate",
            analysis_method="structural",
        )
        coverage = _coverage([GO_LISTEN_EDGE], {"go": load_catalog("go")})
        verdict = verify_taint_claim(_taint_claim(), [finding], coverage=coverage)
        assert verdict.verdict == "violated", verdict.verdict
        assert _deferred(verdict) == []

    def test_no_coverage_adds_no_disclosure(self) -> None:
        verdict = verify_taint_claim(_taint_claim(), [], coverage=None)
        assert verdict.verdict == "confirmed"


class TestTheMergedSentenceStaysTrue:
    """``_merge_caveat`` folds a caveat into one of the same kind. Its sentence
    quotes a count and the sites, so a fold that widens ``entries`` must
    re-render, and a fold across two BOUNDARIES must not happen at all."""

    def test_a_same_boundary_fold_re_renders_the_count(self) -> None:
        first = _deferred_crossing_caveat("net_recv", ["net.Listen"])
        merged = _merge_caveat(
            [first], _deferred_crossing_caveat("net_recv", ["net/http.Serve"]),
        )
        [cav] = merged
        assert cav["entries"] == ["net.Listen", "net/http.Serve"]
        assert cav["detail"].startswith("2 call site(s)")

    def test_the_fold_keeps_the_arm(self) -> None:
        first = _deferred_crossing_caveat("net_recv", ["a.b"], arm=_ARM_TAINT)
        [cav] = _merge_caveat(
            [first], _deferred_crossing_caveat("net_recv", ["c.d"], arm=_ARM_TAINT),
        )
        assert cav["arm"] == _ARM_TAINT
        assert cav == _deferred_crossing_caveat("net_recv", ["a.b", "c.d"], arm=_ARM_TAINT)

    def test_two_boundaries_stay_two_caveats(self) -> None:
        merged = _merge_caveat(
            [_deferred_crossing_caveat("net_recv", ["net.Listen"])],
            _deferred_crossing_caveat("db_read", ["django.db.models.filter"]),
        )
        assert [c["boundary"] for c in merged] == ["net_recv", "db_read"]

    def test_the_taint_sentence_does_not_speak_of_a_boundary_result(self) -> None:
        """The boundary arm's sentence ends "a clean net_recv result does not
        cover it". Under a taint claim no net_recv result was asked for; the
        sentence must say what the TAINT verdict could not see."""
        cav = _deferred_crossing_caveat("net_recv", ["net.Listen"], arm=_ARM_TAINT)
        assert "clean net_recv result" not in cav["detail"]
        assert "taint" in cav["detail"]
