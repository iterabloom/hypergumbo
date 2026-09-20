# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §14 / WI-lihis: a resolved edge demotes a same-site external stub.

The rule, on synthetic edges from two declared Python producers: an
unresolved edge (``is_resolved=False``, dst an external stub) is demoted —
``rank_score`` scaled by ``SUPERSEDED_STUB_RANK_FACTOR``, never deleted,
``confidence`` untouched — when a RESOLVED edge shares its ``src``, one of
its call lines, its ``edge_type`` and its declared callee name key, and that
resolved edge carries a producer the stub's own ``origin`` lacks. The stamp
``meta.superseded_by`` / ``meta.superseded_by_origin`` records which edge
did it, so a low ``rank_score`` is never an unexplained number. Every
condition has a test that removes it and sees no demotion: a different
callee on the same line is a different call, the same producer's two calls
on one line are not a contradiction, and two stubs never demote each other.
"""
from __future__ import annotations

from typing import Any

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    as_emitted,
    last_segment,
    register_analyzer,
)
from hypergumbo_core.finalize import SUPERSEDED_STUB_RANK_FACTOR, demote_superseded_stubs
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol


def _noop(repo_root: Any) -> AnalysisResult:  # pragma: no cover - never called
    return AnalysisResult(symbols=[], edges=[], run=None)


@pytest.fixture(autouse=True)
def two_producers():
    saved, saved_flag = dict(_registry_mod._ANALYZER_REGISTRY), _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = True
    register_analyzer("python", backend="ast", priority=50,
                      merge=MergeAnchor(name_key=last_segment("."), span_role=SPAN_ROLE_ITEM))(_noop)
    register_analyzer("pyscip", backend="scip", emits_origin="scip", priority=45,
                      languages=["python"],
                      merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN))(_noop)
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved)
    _registry_mod._discovered = saved_flag


CALLER = "python:pkg/a.py:1-9:main:function"
WRAP = "python:pkg/b.py:20-30:Result.wrap:method"
OTHER = "python:pkg/b.py:40-50:Result.other:method"
STUB = "python:<external>:0-0:wrap:external_symbol"


def _symbols() -> list[Symbol]:
    def sym(id_: str, name: str, kind: str, path: str, start: int, end: int) -> Symbol:
        return Symbol(id=id_, name=name, kind=kind, language="python", path=path,
                      span=Span(start, end, 0, 0), origin="python", origin_run_id="r")
    return [
        sym(CALLER, "main", "function", "pkg/a.py", 1, 9),
        sym(WRAP, "Result.wrap", "method", "pkg/b.py", 20, 30),
        sym(OTHER, "Result.other", "method", "pkg/b.py", 40, 50),
    ]


def _edge(dst: str, *, origin: list[str], line: int = 5, resolved: bool, edge_type: str = "calls",
          confidence: float = 0.5, lines: list[int] | None = None) -> Edge:
    edge = Edge.create(src=CALLER, dst=dst, edge_type=edge_type, line=line, origin=origin,
                       evidence_type="ast_call_direct", confidence=confidence, origin_run_id="r")
    edge.is_resolved = resolved
    if not resolved:
        edge.dst_ref = ExternalRef(lang="python", module_path="", name="wrap")
    if lines is not None:
        edge.meta = {**(edge.meta or {}), "call_lines": lines}
    edge.rank_score = confidence
    return edge


class TestTheRule:
    def test_a_resolved_call_from_another_producer_demotes_the_same_site_stub(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, confidence=0.85)
        edges = [stub, resolved]
        assert demote_superseded_stubs(_symbols(), edges) == 1
        assert len(edges) == 2  # never deleted
        assert stub.rank_score == pytest.approx(0.5 * SUPERSEDED_STUB_RANK_FACTOR)
        assert stub.confidence == 0.5  # ADR-0039 ruling 3: never on confidence
        assert stub.meta["superseded_by"] == resolved.id
        assert stub.meta["superseded_by_origin"] == ["scip"]
        assert resolved.rank_score == 0.85 and "superseded_by" not in (resolved.meta or {})

    def test_a_merged_two_producer_edge_also_supersedes(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["python", "scip"], resolved=True, confidence=0.95)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1
        assert stub.meta["superseded_by_origin"] == ["python", "scip"]

    def test_a_shared_call_line_counts_not_only_the_first(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False, line=5, lines=[5, 8])
        resolved = _edge(WRAP, origin=["scip"], resolved=True, line=8)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1


class TestTheCalleeNameIsReadFromItsLosslessHome:
    """INV-difud. The id's name slot is deliberately lossy (ADR-0036 R1) and
    ``meta['callee_name']`` is the lossless home the producer stamps. Reading
    the slot instead makes an escaped name fail the key comparison, so the
    demotion is silently MISSED — a false negative in the direction that leaves
    a wrong answer at equal standing with the right one."""

    #: ``Result::wrap`` after the id's ``:`` -> ``.`` fold. ``last_segment(".")``
    #: reads the escaped form as ``wrap`` and the real name as ``Result::wrap``.
    ESCAPED_TARGET = "python:pkg/b.py:60-70:Result..wrap:method"
    ESCAPED_STUB = "python:<external>:0-0:Result..wrap:external_symbol"

    def _escaped_symbols(self) -> list[Symbol]:
        return _symbols() + [Symbol(
            id=self.ESCAPED_TARGET, name="Result::wrap", kind="method", language="python",
            path="pkg/b.py", span=Span(60, 70, 0, 0), origin="python", origin_run_id="r")]

    def test_an_escaped_name_still_reaches_its_demotion(self) -> None:
        stub = _edge(self.ESCAPED_STUB, origin=["python"], resolved=False)
        stub.dst_ref = None  # WI-huzuv: no ref when the module is the sentinel
        stub.meta = {**(stub.meta or {}), "callee_name": "Result::wrap"}
        resolved = _edge(self.ESCAPED_TARGET, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(self._escaped_symbols(), [stub, resolved]) == 1
        assert stub.meta["superseded_by"] == resolved.id

    def test_without_the_lossless_home_the_lossy_slot_is_still_read(self) -> None:
        """No meta, no ref — the id remains the fallback rather than a raise."""
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.dst_ref = None
        stub.meta = {k: v for k, v in (stub.meta or {}).items() if k != "callee_name"}
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1


class TestWhatDoesNotDemote:
    def test_the_factor_is_the_policys(self) -> None:
        from hypergumbo_core.arbitration import ArbitrationPolicy

        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, confidence=0.85)
        policy = ArbitrationPolicy(superseded_stub_rank_factor=0.25)
        assert demote_superseded_stubs(_symbols(), [stub, resolved], policy=policy) == 1
        assert stub.rank_score == pytest.approx(0.5 * 0.25)

    def test_a_different_callee_on_the_same_line_is_a_different_call(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(OTHER, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0
        assert stub.rank_score == 0.5 and "superseded_by" not in (stub.meta or {})

    def test_the_same_producers_two_calls_on_one_line_are_not_a_contradiction(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["python"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_different_line_does_not_supersede(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False, line=5)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, line=6)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_different_edge_type_does_not_supersede(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True, edge_type="references")
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_two_stubs_never_demote_each_other(self) -> None:
        a = _edge(STUB, origin=["python"], resolved=False)
        b = _edge(STUB, origin=["scip"], resolved=False)
        assert demote_superseded_stubs(_symbols(), [a, b]) == 0

    def test_a_language_with_one_registered_producer_is_untouched(self) -> None:
        _registry_mod._ANALYZER_REGISTRY.pop("pyscip")
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_stub_without_a_dst_ref_reads_the_callee_from_its_id(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.dst_ref = None
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1

    def test_an_unset_rank_score_is_taken_from_confidence(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.rank_score = None
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1
        assert stub.rank_score == pytest.approx(0.5 * SUPERSEDED_STUB_RANK_FACTOR)


class TestOnlyAnAnchoredProducerSupersedes:
    """ADR-0057 §14, ruled 2026-09-20 on measurement (Open question 4).

    The origin condition as first landed tested PASS-ID INEQUALITY and stood in
    for a test on INDEPENDENT OBSERVATION. A single-backend Python run of this
    repository showed how far apart the two are: 419 stubs demoted, every
    superseder a linker, and those 419 superseders were 419 of the 421 edges
    ``inherited-calls-linker`` (329 of 329) and ``method-call-recovery-linker``
    (90 of 92) emitted — each one the linker's own resolution of the very stub
    it superseded, built from that stub's ``src``, ``line`` and parsed callee.
    A refinement of one observation is not a second observation (§13 case 2).
    """

    def test_two_producers_registered_but_only_one_ran_demotes_nothing(self) -> None:
        """THE CONTROL, and unlike its predecessor it can fail.

        ``test_a_language_with_one_registered_producer_is_untouched`` pops
        ``pyscip`` from the REGISTRY, which makes ``merge_participants`` return
        ``[]`` and the pass early-return before the origin gate is ever
        consulted — so it cannot observe the state that produced the 419:
        both producers REGISTERED (the monorepo installs both) and only one
        RUN. This leaves the registry exactly as production has it and varies
        what is in the graph instead.
        """
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["inherited-calls-linker"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_linker_resolution_never_supersedes(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["method-call-recovery-linker"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_a_stub_a_linker_emitted_is_not_superseded_by_an_analyzer(self) -> None:
        """The symmetric half. Both sides must be an anchored producer's edge:
        a linker's stub has no second observer either, whichever direction the
        origin difference runs."""
        stub = _edge(STUB, origin=["inherited-calls-linker"], resolved=False)
        resolved = _edge(WRAP, origin=["python"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_the_declared_origin_spelling_is_accepted(self) -> None:
        """``pyscip`` registers under that NAME and declares
        ``emits_origin="scip"``, because the shared SCIP translation stamps
        that synthetic pass id on every record (ADR-0044). Both spellings name
        the same anchored producer and both are accepted; recognising only the
        registration name would silently disarm the rule for the only backends
        it exists to serve. The declaration is what is read — reading
        ``backend`` instead got the right answer off the wrong field
        (INV-gabak)."""
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1

    def test_the_analyzer_name_spelling_is_accepted(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        resolved = _edge(WRAP, origin=["pyscip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1

    def test_the_survey_case_that_ranked_a_field_over_a_correct_stub(self) -> None:
        """The regression that would have caught the 419 the day they appeared.

        Measured on this repository: ``tree_sitter.Language(tree_sitter_rust.language())``
        at ``test_rust.py:612``. The producer said ``tree_sitter_rust.language``
        and was RIGHT; ``method-call-recovery-linker`` bound the call to
        ``Symbol.language`` — a dataclass FIELD in ``ir.py`` — and §14 then
        halved the correct answer's rank in favour of the fabrication. Nineteen
        of that shape, 28 with a stated module, 45 wrong in all.
        """
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.dst_ref = ExternalRef(lang="python", module_path="tree_sitter_rust", name="wrap")
        resolved = _edge(WRAP, origin=["method-call-recovery-linker"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0
        assert "superseded_by" not in (stub.meta or {})
        assert stub.rank_score == 0.5  # untouched


class TestOnlyAnAbstainingStubIsSuperseded:
    """§15.1 applied to §14: a PARTIAL external key abstains on its module, so
    an in-repo answer FILLS it (§1's abstention rule, §4's fill-when-empty). A
    COMPLETE key STATES one, an in-repo answer CONTRADICTS it, and §11 rules
    that a disagreement on ``dst`` is two edges — a contest, not a
    supersession, and §10 settles a contest only by a cited ``docs/audits/``
    table, of which none exists for external-vs-in-repo.
    """

    def test_a_stub_that_states_a_module_is_contested_not_superseded(self) -> None:
        stub = _edge(STUB, origin=["python"], resolved=False)
        stub.dst_ref = ExternalRef(lang="python", module_path="builtins", name="wrap")
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 0

    def test_an_empty_module_path_states_nothing_and_is_still_superseded(self) -> None:
        """``_edge``'s default. An ``ExternalRef`` with an empty module path
        states nothing either — ``ir.stated_module_of`` is the one reader of
        that fact, shared with §15's fold, so the two rules cannot drift."""
        stub = _edge(STUB, origin=["python"], resolved=False)
        assert stub.dst_ref is not None and stub.dst_ref.module_path == ""
        resolved = _edge(WRAP, origin=["scip"], resolved=True)
        assert demote_superseded_stubs(_symbols(), [stub, resolved]) == 1
