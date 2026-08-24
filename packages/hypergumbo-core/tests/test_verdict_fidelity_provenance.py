# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lagod: a verdict must say AT WHAT ANALYSIS FIDELITY it was reached.

THE SECOND HALF OF THE OWNER'S BAR. The 2026-08-23 ruling rewrote INV-fibis /
INV-nuhun to "a clean verdict must NAME WHAT IT COULD NOT EXAMINE, AND AT WHAT
ANALYSIS FIDELITY". The first half shipped across three PRs. This is the
second, and until it exists neither item can close.

WHAT WAS MISSING, read in code rather than assumed: ``Verdict`` carried eleven
fields and none of them named the analyzer that produced the edges the verdict
rests on, while ``BoundaryCoverage`` modelled blind spots that are every one a
property of the EDGE SET. Measured consequence: two runs over one Rust crate
differing ONLY in backend produced a BYTE-IDENTICAL verdict block, while arm B
carried 10 ``origin=scip`` nodes and 6 scip edges and arm A carried zero.

The raw material was always there and unread: ``Edge.origin`` is a list of pass
IDs and ``Edge.__post_init__`` HARD-RAISES on an empty one, so every edge in
the map can say which pass made it. ``verify_claims`` read ``src`` / ``dst`` /
``type`` and never ``origin``.

TWO DISTINCT FACTS, kept apart on purpose:

* WHAT RAN — the pass IDs behind this verdict's call edges, per language. A
  statement about this run.
* WHAT COULD HAVE RUN — a higher-fidelity backend that is INSTALLED on this
  machine and was not used. A statement about the machine, and the one that
  makes "rust-analyzer installed but not enabled" distinguishable from "no
  such backend exists", which the ruling explicitly requires because a reader
  would act differently on them.

``analysis_methods`` ALREADY EXISTS AND IS NOT THIS. It records how the taint
walk REASONED (``ddg`` / ``ddg_mixed`` / ``structural``), not which analyzer
produced the edges: a ``structural`` verdict and a tree-sitter verdict are
different facts, and quietly widening that field into this one would put two
concepts under one name.
"""
from __future__ import annotations

from hypergumbo_core.io_boundary import (
    BoundaryMap,
    IoBoundaryCatalog,
    IoPrimitive,
)
from hypergumbo_core import cli
from hypergumbo_core.verify_claims import (
    CAVEAT_HIGHER_FIDELITY_AVAILABLE,
    Claim,
    TaintFlowConstraint,
    analysis_fidelity,
    compute_boundary_coverage,
    passes_that_ran,
    verify_claim,
    verify_taint_claim,
)


def _catalog(language: str) -> IoBoundaryCatalog:
    return IoBoundaryCatalog(
        language=language,
        primitives=[IoPrimitive(boundary="net_send", module="net.Socket",
                                name="write", kind="method")],
        stdlib_modules=frozenset({"net"}),
        module_completeness={"net": "2026-08-23"},
    )


def _claim() -> Claim:
    return Claim(id="C", text="t", constraint_boundary="net_send",
                 constraint_must_not_exist=True)


def _edge(language: str, origin, line: int = 2) -> dict:
    return {
        "src": f"{language}:app/x:1-3:f:function",
        "dst": f"{language}:net:0-0:Socket:external_symbol",
        "type": "calls", "line": line, "origin": origin,
    }


class TestWhatRan:
    """The pass IDs behind this verdict's call edges, per language."""

    def test_it_reports_the_pass_that_produced_the_edges(self) -> None:
        got = analysis_fidelity([_edge("rust", ["rust"])], {"rust": _catalog("rust")})
        assert got == {"rust": ["rust"]}

    def test_two_fidelities_over_one_language_are_both_named(self) -> None:
        """ADR-0012's design is COEXISTENCE, not replacement — tree-sitter and
        SCIP edges live side by side in one run — so this is the normal case
        for a Rust repo with the backend on, not an edge case."""
        got = analysis_fidelity(
            [_edge("rust", ["rust"]), _edge("rust", ["scip"], line=3)],
            {"rust": _catalog("rust")},
        )
        assert got == {"rust": ["rust", "scip"]}

    def test_a_scalar_origin_is_accepted(self) -> None:
        """``Edge.origin`` normalises to a list, but a hand-written or older
        map can carry the scalar form (INV-jidat migration), and a verdict that
        crashed on one would be worse than one that could not report it."""
        assert analysis_fidelity(
            [_edge("rust", "rust")], {"rust": _catalog("rust")},
        ) == {"rust": ["rust"]}

    def test_an_edge_with_no_origin_is_reported_as_unattributed(self) -> None:
        """FAIL-LOUD, not fail-silent. Dropping an unattributed edge would let
        a verdict claim a fidelity for edges that never declared one."""
        assert analysis_fidelity(
            [_edge("rust", [])], {"rust": _catalog("rust")},
        ) == {"rust": ["unattributed"]}

    def test_only_call_edges_count(self) -> None:
        """A ``contains`` edge produced by the containment linker says nothing
        about the fidelity of the CALL structure the verdict rests on."""
        edges = [_edge("rust", ["rust"])]
        edges.append({**_edge("rust", ["containment-linker"], line=9),
                      "type": "contains"})
        assert analysis_fidelity(edges, {"rust": _catalog("rust")}) == {
            "rust": ["rust"],
        }

    def test_it_rides_the_verdict(self) -> None:
        coverage = compute_boundary_coverage(
            [_edge("rust", ["rust"])], {"rust"}, {"rust": _catalog("rust")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.analysis_fidelity == {"rust": ["rust"]}
        assert verdict.to_dict()["analysis_fidelity"] == {"rust": ["rust"]}


class TestWhatCouldHaveRun:
    """Installed-but-off must be distinguishable from absent."""

    def test_installed_but_unused_raises_the_caveat(self) -> None:
        coverage = compute_boundary_coverage(
            [_edge("rust", ["rust"])], {"rust"}, {"rust": _catalog("rust")},
            higher_fidelity_available={"rust": "rust-analyzer"},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_HIGHER_FIDELITY_AVAILABLE)
        assert "rust-analyzer" in cav["detail"]
        assert cav["entries"] == ["rust"]

    def test_installed_AND_used_raises_nothing(self) -> None:
        """CONTROL. The caveat is about an unused option, not about the
        existence of one — otherwise turning the backend ON would leave the
        verdict looking just as qualified, and nobody would turn it on."""
        coverage = compute_boundary_coverage(
            [_edge("rust", ["scip"])], {"rust"}, {"rust": _catalog("rust")},
            higher_fidelity_available={"rust": "rust-analyzer"},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_HIGHER_FIDELITY_AVAILABLE not in kinds, kinds

    def test_a_backend_that_ran_but_made_no_call_edges_raises_nothing(
        self,
    ) -> None:
        """FOUND BY MEASURING, NOT BY REASONING, and it is why "did it run" is
        answered from a different population than "what produced the calls".

        With rust-analyzer ON, the SCIP pass emitted ``references`` and
        ``contains`` and no external ``calls``. Keyed on the CALL-edge fidelity
        map, the caveat fired and told the reader to enable a backend that was
        already on and had printed its own "backend ACTIVE" banner in the same
        run. ``analysis_fidelity`` correctly still reports only the parser that
        produced the call structure.
        """
        edges = [
            _edge("rust", ["rust"]),
            {**_edge("rust", ["scip"], line=7), "type": "references"},
        ]
        coverage = compute_boundary_coverage(
            edges, {"rust"}, {"rust": _catalog("rust")},
            higher_fidelity_available={"rust": "rust-analyzer"},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_HIGHER_FIDELITY_AVAILABLE not in kinds, kinds
        assert verdict.analysis_fidelity == {"rust": ["rust"]}

    def test_not_installed_raises_nothing(self) -> None:
        """SECOND CONTROL, the other axis: a machine without the binary gets no
        caveat, because there is nothing the reader could have done."""
        coverage = compute_boundary_coverage(
            [_edge("rust", ["rust"])], {"rust"}, {"rust": _catalog("rust")},
            higher_fidelity_available={},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_HIGHER_FIDELITY_AVAILABLE not in kinds, kinds

    def test_a_language_absent_from_the_repo_raises_nothing(self) -> None:
        coverage = compute_boundary_coverage(
            [_edge("go", ["go"])], {"go"}, {"go": _catalog("go")},
            higher_fidelity_available={"rust": "rust-analyzer"},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_HIGHER_FIDELITY_AVAILABLE not in kinds, kinds


class TestPassesThatRan:
    """The "did it run at all" population, kept separate from the call-edge one."""

    def test_it_reads_every_edge_type_not_just_calls(self) -> None:
        edges = [
            _edge("rust", ["rust"]),
            {**_edge("rust", ["scip"], line=7), "type": "references"},
        ]
        assert passes_that_ran(edges) == {"rust", "scip"}

    def test_a_scalar_origin_is_accepted_here_too(self) -> None:
        """Same INV-jidat migration shape as its sibling: a hand-written or
        older map can carry the scalar form, and crashing on one would be
        worse than not reporting it."""
        assert passes_that_ran([_edge("rust", "scip")]) == {"scip"}

    def test_an_edge_with_no_origin_contributes_nothing(self) -> None:
        assert passes_that_ran([_edge("rust", [])]) == set()


class TestTheTaintArm:
    def test_it_carries_the_higher_fidelity_caveat(self) -> None:
        """INV-nuhun's asymmetry again: a run that credits its fidelity on the
        boundary arm and stays silent on the taint arm is the defect that item
        names."""
        coverage = compute_boundary_coverage(
            [_edge("rust", ["rust"])], {"rust"}, {"rust": _catalog("rust")},
            higher_fidelity_available={"rust": "rust-analyzer"},
        )
        claim = Claim(
            id="T", text="t",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="host_secret", prohibited_sink_zone="network",
            ),
        )
        verdict = verify_taint_claim(claim, [], coverage=coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_HIGHER_FIDELITY_AVAILABLE in kinds, kinds
        assert verdict.analysis_fidelity == {"rust": ["rust"]}


class TestTheCliDerivesAvailability:
    """"Is the binary on this machine" is a fact about the MACHINE, so it is
    derived at the CLI and passed in — a pure function of the graph must not
    start shelling out."""

    def test_a_repo_with_no_rust_asks_nothing(self, monkeypatch) -> None:
        """CHEAPEST CHECK FIRST, and it is not just an optimisation: probing
        for the binary runs it (``--version``), and doing that for a repository
        with no Rust in it is work no answer depends on."""
        called = []
        monkeypatch.setattr(
            cli, "is_rust_analyzer_available",
            lambda: called.append(1) or True,
        )
        assert cli._higher_fidelity_backends_available({"python"}) == {}
        assert called == []

    def test_rust_present_and_backend_installed(self, monkeypatch) -> None:
        monkeypatch.setattr(cli, "is_rust_analyzer_available", lambda: True)
        assert cli._higher_fidelity_backends_available({"rust"}) == {
            "rust": "rust-analyzer",
        }

    def test_rust_present_and_backend_absent(self, monkeypatch) -> None:
        """CONTROL: nothing to suggest on a machine without the binary."""
        monkeypatch.setattr(cli, "is_rust_analyzer_available", lambda: False)
        assert cli._higher_fidelity_backends_available({"rust"}) == {}


class TestTheEnvelope:
    def test_the_version_moved(self) -> None:
        """The envelope gains a per-verdict key, so the version moves — the
        rule this module's own header states: the version moves when the
        ENVELOPE moves."""
        from hypergumbo_core.verify_claims import VERIFY_CLAIMS_SCHEMA_VERSION
        assert VERIFY_CLAIMS_SCHEMA_VERSION == "2.2"
