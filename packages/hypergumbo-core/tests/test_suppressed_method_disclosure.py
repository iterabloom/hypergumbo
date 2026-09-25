# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-polad: a call site the analyzer DELIBERATELY drops must still be
disclosed when the name it dropped is one the I/O catalogue calls a sink.

THE DEFECT. rust.py carries a 77-name denylist of ubiquitous method names
(`_RUST_GENERIC_TRAIT_METHODS`). Ten of them are names rust.yaml declares as
I/O sinks — `UdpSocket.send`, `io::Write.write`, `TcpStream.read`,
`Command.spawn` and friends — so `sock.send(payload)` emitted NO EDGE AT ALL
and a clean `net_send` verdict had nothing to disclose. Measured: a crate whose
`exfiltrate()` calls `sock.send(payload)` returned a BARE `confirmed`.

WHY THE OBVIOUS FIX IS THE WRONG ONE, measured rather than assumed. Emitting
the suppressed calls as ordinary unresolved-external edges inflates the graph
with `.clone()` / `.unwrap()` / `.map()` noise: on two real crates that took
total edges +33% and +67%, and the EXTERNAL edge population +115% and +207%.
Paying that on every Rust repository — in centrality, dead-code, slice and
supply-chain output — to make ten names disclosable is not a trade worth
making.

WHY THE DENYLIST ITSELF STAYS. It does two jobs and only one was ever at issue.
It stops a name-only RESOLUTION binding `x.load()` to a project `JoltDevice::load`
(WI-bakak: 22 of 29 false callers), which is correct and untouched. This file
is about the SECOND job it had quietly acquired — suppressing the disclosure.

THE FIX IS A DECLARATION, THE SAME SHAPE AS PR2's. The denylist MOVES to
``analyzer_disclosure`` and rust.py imports it, so the fact has ONE home and the
disclosure cannot drift from the behaviour it describes. The overlap with the
catalogue is computed at render time from the shipped catalogue, so it cannot go
stale either.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyzer_disclosure import (
    SUPPRESSED_METHOD_NAMES,
    suppressed_catalogued_sinks,
)
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    IoBoundaryCatalog,
    IoPrimitive,
    load_catalog,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_ANALYZER_SUPPRESSED_METHODS,
    Claim,
    TaintFlowConstraint,
    compute_boundary_coverage,
    verify_claim,
    verify_taint_claim,
)


class TestTheDenylistHasOneHome:
    def test_the_rust_analyzer_imports_the_declared_set(self) -> None:
        """Not a copy — the SAME object. A restated denylist is a second home
        for one fact, and the second one silently wins (LIVE.md rule 7).
        """
        from hypergumbo_lang_mainstream import rust
        assert (
            rust._RUST_GENERIC_TRAIT_METHODS
            is SUPPRESSED_METHOD_NAMES["rust"]
        )

    def test_the_declared_set_is_not_empty(self) -> None:
        assert len(SUPPRESSED_METHOD_NAMES["rust"]) > 50


class TestTheOverlapWithTheCatalogue:
    def test_it_names_the_catalogued_sinks_the_denylist_hides(self) -> None:
        """The overlap is DERIVED from the shipped catalogue, so adding a
        catalogue row for a denylisted name extends the disclosure without
        anyone remembering to."""
        found = suppressed_catalogued_sinks("rust", load_catalog("rust"))
        assert found >= {"send", "write", "read", "flush"}, found

    def test_a_language_with_no_declared_suppression_finds_nothing(
        self,
    ) -> None:
        """CONTROL. Without this, a bug returning every catalogue name would
        pass the test above."""
        assert suppressed_catalogued_sinks("python", load_catalog("python")) == set()

    def test_only_method_kind_rows_count(self) -> None:
        """A denylisted name matching a FUNCTION-kind row is not hidden by
        this denylist — the denylist governs the instance-method path only,
        which is why `Command::new` still reaches the catalogue and a rust
        subprocess claim still fires `violated`."""
        catalog = load_catalog("rust")
        found = suppressed_catalogued_sinks("rust", catalog)
        method_names = {
            p.name for p in catalog.primitives
            if getattr(p, "kind", None) == "method"
        }
        assert found <= method_names


def _catalog(language: str, sink_name: str) -> IoBoundaryCatalog:
    """A minimal catalogue with one METHOD-kind sink named ``sink_name``.

    ``module_completeness`` is populated because an UNENUMERATED module trips
    the uncatalogued-module gate and the verdict is withheld before any caveat
    is reached — which would make these tests pass for the wrong reason.
    """
    return IoBoundaryCatalog(
        language=language,
        primitives=[IoPrimitive(boundary="net_send", module="net.Socket",
                                name=sink_name, kind="method")],
        stdlib_modules=frozenset({"net"}),
        module_completeness={"net": "2026-08-23"},
    )


def _claim() -> Claim:
    return Claim(id="C", text="t", constraint_boundary="net_send",
                 constraint_must_not_exist=True)


def _edges(language: str) -> list[dict]:
    return [{
        "src": f"{language}:app/x:1-3:f:function",
        "dst": f"{language}:net:0-0:Socket:external_symbol",
        "type": "calls", "line": 2,
    }]


class TestTheVerdict:

    def test_a_clean_rust_verdict_is_not_bare(self) -> None:
        """THE POINT. ``send`` is denylisted AND a catalogued net_send sink, so
        ``sock.send(payload)`` emits nothing and the verdict had nothing to
        disclose. Verified end to end on the shipped CLI as well: a crate whose
        only boundary reach is a denylisted name returns rc 3 naming all nine.
        """
        coverage = compute_boundary_coverage(
            _edges("rust"), {"rust"}, {"rust": _catalog("rust", "send")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_ANALYZER_SUPPRESSED_METHODS)
        assert cav["entries"] == ["rust"]
        assert "send" in cav["detail"]

    def test_a_language_with_no_declared_denylist_keeps_a_bare_confirmed(
        self,
    ) -> None:
        """FALSIFIABILITY CONTROL. Without it the test above is satisfied by a
        caveat that fires on everything, which a reader learns to ignore."""
        coverage = compute_boundary_coverage(
            _edges("go"), {"go"}, {"go": _catalog("go", "Write")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_a_denylisted_name_the_catalogue_does_not_declare_is_silent(
        self,
    ) -> None:
        """SECOND CONTROL, on the other axis. ``clone`` is on the denylist but
        is no catalogue's sink, so suppressing it costs the verdict nothing and
        must not be disclosed. Without this, a bug reporting the whole denylist
        would pass every other test here."""
        coverage = compute_boundary_coverage(
            _edges("rust"), {"rust"},
            {"rust": _catalog("rust", "definitely_not_denylisted")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_the_taint_arm_carries_it_too(self) -> None:
        """INV-nuhun's asymmetry: a run that discloses on the boundary arm and
        stays silent on the taint arm about the SAME unseen call is the defect
        that item names."""
        coverage = compute_boundary_coverage(
            _edges("rust"), {"rust"}, {"rust": _catalog("rust", "send")},
        )
        claim = Claim(
            id="T", text="t",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="host_secret", prohibited_sink_zone="network",
            ),
        )
        verdict = verify_taint_claim(claim, [], coverage=coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_ANALYZER_SUPPRESSED_METHODS in kinds, kinds
