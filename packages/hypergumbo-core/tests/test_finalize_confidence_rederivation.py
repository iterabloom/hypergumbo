# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fazim: derived confidence must follow finalize's resolution verdict, not the
producer's advisory flag.

WHAT WAS WRONG. Two features disagreed about who decides ``is_resolved``.

* ADR-0039 / confidence:F1 derives ``Edge.confidence`` from
  ``(evidence_type, is_resolved)`` **at construction time**, inside ``Edge.create``
  (``ir.py``), and stamps ``confidence_source="evidence_derived"``.
* ADR-0037 ruling 1 makes the producer's value **advisory** — ``Edge.create``
  defaults ``is_resolved=True`` and ``ir.py``'s own field docstring says "the
  producer-time value … is ADVISORY; the finalize edge-resolution sub-step's
  verdict is what serializes."

``_finalize_edge_resolution`` duly flips the flag to ``False`` for every edge whose
dst turns out to be an ``external_symbol`` placeholder — and never re-derives the
confidence that was computed from the now-superseded value. ``finalize.py``'s own
header even records "``Edge.confidence`` untouched" as deliberate, a note that
predates confidence being conditioned on ``is_resolved`` at all.

So an unresolved call ships at the RESOLVED base: ``ast_call`` at **0.85** where the
verdict says **0.40**, ``ast_call_direct`` at 0.85 where it says 0.50.

MEASURED on four repos, both languages, running production's own
``create_boundary_nodes`` + ``_finalize_edge_resolution``: caddy 6,167,
mitmproxy 8,880, poetry 4,074, alertmanager 3,922 — **23,043 edges**. It is not a
Go producer bug: any producer that takes ``Edge.create``'s default and whose dst
resolves external hits it, which is why the fix is at the finalize chokepoint
rather than at ``go.py``'s emit site.

WHY THE NULL LOOKED LIKE ZERO TWICE. A probe run *after* finalize finds no edge
with ``is_resolved=True`` at an external dst — finalize defines that predicate
away, so the measurement is tautological. A probe run *before* finalize finds none
either, because the flag has not flipped yet. The defect is only visible by running
the resolution sub-step and then comparing confidence against the verdict. Both
tautologies were reached in this project before the real number was.

DIRECTION: strictly downward, which is the deleting direction for anything banded
on confidence. ``slice``'s ``min_confidence`` defaults to 0.0 (no effect unless a
caller opts in), but ``receiver_blind_magnets`` bands at 0.80, so edges corrected
to 0.40/0.50 leave its window. That is the intended reading — the detector looks
for *high-confidence* edges that bound wrongly, and an edge the graph now says is
unresolved is no longer high-confidence — but it is a real output change and is
measured and published rather than assumed.

ONLY ``evidence_derived`` EDGES ARE TOUCHED. ADR-0039 is explicit that a producer
passing an explicit confidence keeps it; re-deriving those would overwrite a
deliberate producer judgement with a table lookup.
"""

from pathlib import Path

import pytest

from hypergumbo_core.confidence import derive_confidence
from hypergumbo_core.finalize import FinalizeContext, _finalize_edge_resolution
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.limits import Limits
from hypergumbo_core.pass_metadata import PassMetadataLookup


def _ctx(tmp_path: Path, symbols: list, edges: list) -> FinalizeContext:
    return FinalizeContext(
        symbols=symbols, edges=edges, usage_contexts=[], analysis_runs=[],
        behavior_map={}, limits=Limits(), repo_root=tmp_path,
        pass_metadata=PassMetadataLookup({}),
    )


def _external(sym_id: str) -> Symbol:
    return Symbol(
        id=sym_id, name=sym_id.split(":")[-2], kind="external_symbol",
        path="", language="go", span=None,
    )


def _inrepo(sym_id: str) -> Symbol:
    return Symbol(
        id=sym_id, name=sym_id.split(":")[-2], kind="function",
        path="a.go", language="go", span=Span(5, 9, 0, 0),
    )


def _call_edge(dst: str, evidence_type: str = "ast_call") -> Edge:
    """An edge as a producer emits it: no explicit confidence, default is_resolved."""
    return Edge.create(
        src="go:a:1-2:caller:function", dst=dst, edge_type="calls",
        line=1, evidence_type=evidence_type, origin="test", origin_run_id="r",
    )


class TestDerivedConfidenceFollowsTheVerdict:
    def test_external_dst_is_rederived_downward(self, tmp_path: Path) -> None:
        """The defect: created at the resolved base, flipped to unresolved, never re-derived."""
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = _call_edge(dst)
        assert edge.confidence == derive_confidence("ast_call", is_resolved=True), (
            "precondition: the producer default derives the RESOLVED base"
        )
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.is_resolved is False
        assert edge.confidence == derive_confidence("ast_call", is_resolved=False)

    def test_ast_call_direct_uses_its_own_unresolved_base(self, tmp_path: Path) -> None:
        """Not a constant: each evidence_type has its own pair (0.85/0.50 here)."""
        dst = "go:external:0-0:Open:unresolved"
        edge = _call_edge(dst, evidence_type="ast_call_direct")
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence == derive_confidence("ast_call_direct", is_resolved=False)

    def test_in_repo_dst_keeps_the_resolved_base(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL — a resolved edge must be untouched, or the test above
        passes just as well when re-derivation is applied indiscriminately."""
        dst = "go:a:5-9:callee:function"
        edge = _call_edge(dst)
        _finalize_edge_resolution(_ctx(tmp_path, [_inrepo(dst)], [edge]))
        assert edge.is_resolved is True
        assert edge.confidence == derive_confidence("ast_call", is_resolved=True)

    def test_evidence_type_with_no_resolution_split_is_unchanged(
        self, tmp_path: Path,
    ) -> None:
        """``ast_import`` derives 0.95 either way — re-derivation must be a no-op,
        not an accidental rewrite."""
        dst = "go:external:0-0:fmt:unresolved"
        edge = _call_edge(dst, evidence_type="ast_import")
        before = edge.confidence
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence == before == derive_confidence("ast_import", is_resolved=False)


class TestProducerJudgementIsNotOverwritten:
    def test_explicit_producer_confidence_survives(self, tmp_path: Path) -> None:
        """ADR-0039: a producer that passes an explicit confidence keeps it.

        Re-deriving these would replace a deliberate judgement with a table lookup —
        and would silently move any producer that had reasoned about its own edge.
        """
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = Edge.create(
            src="go:a:1-2:caller:function", dst=dst, edge_type="calls", line=1,
            evidence_type="ast_call", origin="test", origin_run_id="r",
            confidence=0.72,
        )
        assert edge.confidence_source != "evidence_derived"
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence == pytest.approx(0.72)

    def test_confidence_source_stays_evidence_derived(self, tmp_path: Path) -> None:
        """Re-derivation keeps the provenance discriminator — the value is still
        derived, just from the correct input."""
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = _call_edge(dst)
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence_source == "evidence_derived"


class TestIdempotence:
    def test_running_the_substep_twice_is_stable(self, tmp_path: Path) -> None:
        """finalize sub-steps run inside an orchestrator that may re-enter; a
        re-derivation that ratchets on each pass would drift the whole graph."""
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = _call_edge(dst)
        ctx = _ctx(tmp_path, [_external(dst)], [edge])
        _finalize_edge_resolution(ctx)
        once = edge.confidence
        _finalize_edge_resolution(ctx)
        assert edge.confidence == once


class TestBranchesThatMustNotFire:
    """Coverage for the two guards, each with a reason to exist."""

    def test_unseeded_evidence_type_is_left_alone(self, tmp_path: Path) -> None:
        """``derive_confidence`` returns None for a pathway it has no seed for.

        The historical 0.85 fallback is then the edge's real value and must not be
        rewritten to something the table never claimed.
        """
        dst = "go:external:0-0:Thing:unresolved"
        edge = _call_edge(dst, evidence_type="totally_unseeded_pathway")
        assert derive_confidence("totally_unseeded_pathway", is_resolved=False) is None
        before = edge.confidence
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence == before

    def test_diverged_rank_score_is_preserved(self, tmp_path: Path) -> None:
        """ADR-0039 ruling 3: once a producer relocates a ranking adjustment onto
        ``rank_score`` it no longer mirrors, and correcting confidence must not
        discard that adjustment."""
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = _call_edge(dst)
        edge.rank_score = 0.91
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.confidence == derive_confidence("ast_call", is_resolved=False)
        assert edge.rank_score == pytest.approx(0.91)

    def test_mirroring_rank_score_follows_the_correction(self, tmp_path: Path) -> None:
        dst = "go:external:0-0:WriteFile:unresolved"
        edge = _call_edge(dst)
        assert edge.rank_score == edge.confidence
        _finalize_edge_resolution(_ctx(tmp_path, [_external(dst)], [edge]))
        assert edge.rank_score == edge.confidence == derive_confidence(
            "ast_call", is_resolved=False,
        )
