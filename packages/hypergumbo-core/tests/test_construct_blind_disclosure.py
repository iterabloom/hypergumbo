# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-zumoz: a catalogued row keyed on a construct the analyzer never emits an
edge for must be DISCLOSED on a clean verdict, not silently left unmatched.

THE DEFECT. ``javascript.yaml`` declares ``WebSocket.onmessage``,
``WebSocket.onclose`` and ``EventSource.onmessage`` as method-kind ``net_recv``
rows. Browser code never CALLS them: ``ws.onmessage = handler`` is a property
ASSIGNMENT, and the analyzer emits a call edge only for a call. So the three
rows -- the browser WebSocket / SSE receive surface short of
``addEventListener`` -- could never classify, and a clean verdict on a
repository whose only receive is ``ws.onmessage = ...`` had nothing to
disclose. Measured 2026-08-28 (the mechanism was masked by INV-misup, the
receiver-typing gap #753 closed) and re-measured 2026-09-06 on the fixed
analyzer: ``addEventListener`` on the same ``new``-constructed receiver reaches
the catalogue and the three assignments still emit nothing.

WHY A DECLARATION AND NOT A ROW DELETION. An inert row costs no precision and
claims no coverage: a call that never classifies is never counted as EXAMINED
(INV-buzab), and javascript's catalogue is ``in_progress`` and grants no
``module_completeness``. What the rows carry is the specification of what
should match once the analyzer emits a registration edge for the assignment --
recall work, held in Phase 6 -- and deleting three correct library facts to
describe an analyzer limitation puts the defect in the wrong artifact. So the
limitation is declared where its siblings are (``analyzer_disclosure``),
dated, with the rows DERIVED against the shipped catalogue at render time: a
deleted row leaves the disclosure on its own, and a declared row the catalogue
does not carry fails a test here.

WHY A THIRD CAVEAT KIND. ``analyzer_method_call_blind`` is a whole construct
the analyzer cannot see (remedy: build the edges); ``analyzer_suppressed_methods``
is named methods it deliberately drops (policy: stays). This is a construct that
is NOT A CALL AT ALL, and the remedy -- emit a registration edge for a handler
assignment -- is a third thing. Collapsing them would suggest one fix for three
problems.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyzer_disclosure import (
    CONSTRUCT_BLIND_ROWS,
    construct_blind_catalogued_sinks,
)
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    IoBoundaryCatalog,
    IoPrimitive,
    load_catalog,
    tag_io_boundaries,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_ANALYZER_CONSTRUCT_BLIND,
    Claim,
    TaintFlowConstraint,
    compute_boundary_coverage,
    verify_claim,
    verify_taint_claim,
)

_THREE = {"WebSocket.onmessage", "WebSocket.onclose", "EventSource.onmessage"}


def _method_keys(catalog: IoBoundaryCatalog) -> set[str]:
    return {
        f"{p.module}.{p.name}" for p in catalog.primitives
        if getattr(p, "kind", None) == "method"
    }


class TestTheDeclaration:
    def test_every_declared_row_is_in_the_shipped_catalogue(self) -> None:
        """A declaration naming a row the catalogue no longer carries is a
        stale claim about nothing; the derivation below would hide it, so it
        is caught here instead."""
        for lang, decl in CONSTRUCT_BLIND_ROWS.items():
            missing = decl.rows - _method_keys(load_catalog(lang))
            assert not missing, (lang, missing)

    def test_every_declaration_carries_a_date_and_evidence(self) -> None:
        for decl in CONSTRUCT_BLIND_ROWS.values():
            assert decl.measured and decl.evidence and decl.construct, decl
            assert decl.rows, decl

    def test_javascript_names_the_three_handler_rows(self) -> None:
        found = construct_blind_catalogued_sinks(
            "javascript", load_catalog("javascript"),
        )
        assert found == _THREE, found

    def test_a_language_with_no_declaration_finds_nothing(self) -> None:
        """CONTROL. Without this, a bug returning every catalogue key would
        pass the test above."""
        assert construct_blind_catalogued_sinks(
            "python", load_catalog("python"),
        ) == set()

    def test_a_declared_row_the_catalogue_does_not_carry_is_not_reported(
        self,
    ) -> None:
        """DERIVED, NOT LISTED: the disclosure follows the catalogue, so a
        user overlay that deletes the rows silences it without editing
        this module."""
        assert construct_blind_catalogued_sinks(
            "javascript", _catalog("javascript", "send"),
        ) == set()


class TestTheDeclarationMatchesTheAnalyzer:
    def test_the_assignments_emit_nothing_and_the_call_emits(
        self, tmp_path: Path,
    ) -> None:
        """THE PIN. When this fails because the assignments started emitting
        an edge, the analyzer gained the construct: flip the javascript entry
        in ``CONSTRUCT_BLIND_ROWS`` in the same change, and measure the new
        findings through the taint path (INV-linub)."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        root = tmp_path / "ws"
        root.mkdir()
        (root / "app.js").write_text(
            "function listen(u) {\n"
            "  const ws = new WebSocket(u);\n"
            "  ws.onmessage = function (ev) { return ev.data; };\n"
            "  ws.onclose = function (ev) { return ev.code; };\n"
            "  ws.addEventListener('message', function (ev) { return ev.data; });\n"
            "  const es = new EventSource(u);\n"
            "  es.onmessage = (ev) => ev.data;\n"
            "}\n",
        )
        edges = analyze_javascript(root).edges
        calls = {e.dst for e in edges if e.edge_type == "calls"}
        assert calls == {"javascript:WebSocket:0-0:addEventListener:unresolved"}, calls
        assert tag_io_boundaries(
            edges, {"javascript": load_catalog("javascript")},
        ) == 1


def _catalog(language: str, *names: str) -> IoBoundaryCatalog:
    """A minimal catalogue whose METHOD-kind ``net_recv`` rows are the given
    ``module.name`` keys (a bare name is put under ``WebSocket``).

    ``module_completeness`` is populated because an UNENUMERATED module trips
    the uncatalogued-module gate and the verdict is withheld before any
    caveat is reached -- which would make these tests pass for the wrong
    reason.
    """
    prims = []
    for key in names:
        module, _, name = key.rpartition(".")
        prims.append(IoPrimitive(
            boundary="net_recv", module=module or "WebSocket",
            name=name, kind="method",
        ))
    return IoBoundaryCatalog(
        language=language,
        primitives=prims,
        stdlib_modules=frozenset({"WebSocket", "EventSource"}),
        module_completeness={"WebSocket": "2026-09-06", "EventSource": "2026-09-06"},
    )


def _claim() -> Claim:
    return Claim(id="C", text="t", constraint_boundary="net_recv",
                 constraint_must_not_exist=True)


def _edges(language: str) -> list[dict]:
    return [{
        "src": f"{language}:app/x:1-3:f:function",
        "dst": f"{language}:WebSocket:0-0:WebSocket:external_symbol",
        "type": "calls", "line": 2,
    }]


class TestTheVerdict:
    def test_a_clean_javascript_verdict_is_not_bare(self) -> None:
        """THE POINT. The three rows are declared unreachable AND catalogued,
        so ``ws.onmessage = h`` emits nothing and the verdict had nothing to
        disclose."""
        coverage = compute_boundary_coverage(
            _edges("javascript"), {"javascript"},
            {"javascript": _catalog("javascript", *sorted(_THREE))},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_ANALYZER_CONSTRUCT_BLIND)
        assert cav["entries"] == ["javascript"]
        for row in _THREE:
            assert row in cav["detail"], cav["detail"]
        assert "property assignment" in cav["detail"]
        assert "2026-09-06" in cav["detail"]

    def test_a_language_with_no_declaration_keeps_a_bare_confirmed(
        self,
    ) -> None:
        """FALSIFIABILITY CONTROL: a caveat that fires on everything is one a
        reader learns to ignore."""
        coverage = compute_boundary_coverage(
            _edges("go"), {"go"},
            {"go": _catalog("go", "WebSocket.onmessage")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_a_declared_row_absent_from_the_catalogue_is_silent(self) -> None:
        """SECOND CONTROL, on the other axis: the declaration names rows, and
        a catalogue without them has nothing hidden."""
        coverage = compute_boundary_coverage(
            _edges("javascript"), {"javascript"},
            {"javascript": _catalog("javascript", "WebSocket.send")},
        )
        verdict = verify_claim(_claim(), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_the_taint_arm_carries_it_too(self) -> None:
        """INV-nuhun's asymmetry: disclosing on the boundary arm and staying
        silent on the taint arm about the SAME unseen construct."""
        coverage = compute_boundary_coverage(
            _edges("javascript"), {"javascript"},
            {"javascript": _catalog("javascript", *sorted(_THREE))},
        )
        claim = Claim(
            id="T", text="t",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="untrusted_input", prohibited_sink_zone="host_fs",
            ),
        )
        verdict = verify_taint_claim(claim, [], coverage=coverage)
        kinds = {c["kind"] for c in verdict.caveats}
        assert CAVEAT_ANALYZER_CONSTRUCT_BLIND in kinds, kinds
