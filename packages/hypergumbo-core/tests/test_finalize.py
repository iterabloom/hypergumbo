# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the finalize stage (run-lifecycle:F1, ADR-0043 §6/§6.1).

Covers the orchestrator spine, the hard ordering rules R2 (recompute-after-stamp) and R3
(referential-integrity-last) as white-box call-order guards, each sub-step in isolation, the
shallow-frozen ``FinalizedMap`` contract, and idempotency. These are pure core tests: the
sub-steps that matter operate on ``analysis_runs`` dicts + ``Limits`` + ``behavior_map``, so
empty symbol/edge lists suffice (the internal path-rewriting of ``_relativize_ir_paths`` is
covered by ``test_cli_relativize_paths.py``; the end-to-end §8 round-trip over a real analyzer
substrate lives in ``hypergumbo-lang-mainstream``).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hypergumbo_core.finalize import (
    FinalizeContext,
    FinalizedMap,
    _derive_dst_ref_from_id,
    _finalize_commit_dicts,
    _finalize_compute_visibility,
    _finalize_edge_resolution,
    _finalize_re_relativize,
    _finalize_recompute_run_signature,
    _finalize_referential_integrity,
    _finalize_repo_fingerprint,
    _finalize_skipped_into_limits,
    _finalize_stamp_run_lifecycle,
    _violation_sort_key,
    finalize,
)
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, _compute_run_signature
from hypergumbo_core.limits import Limits
from hypergumbo_core.pass_metadata import PassMeta, PassMetadataLookup
from hypergumbo_core.spec_validator import ValidationViolation, validate_ir

_TC = {"name": "python", "version": "3.11"}


def _ar(pass_id: str, **over) -> dict:
    """An AnalysisRun.to_dict()-shaped dict (note the key is 'pass', not 'pass_id')."""
    d = {
        "pass": pass_id,
        "version": "9.9.9",
        "config_fingerprint": "sha256:cfg",
        "toolchain": dict(_TC),
        "run_signature": "sha256:STALE_PLACEHOLDER",
        "repo_fingerprint": None,
        "pass_version": "",
        "files_skipped": 0,
    }
    d.update(over)
    return d


def _ctx(tmp_path: Path, **over) -> FinalizeContext:
    kwargs = {
        "symbols": [],
        "edges": [],
        "usage_contexts": [],
        "analysis_runs": [],
        "behavior_map": {},
        "limits": Limits(),
        "repo_root": tmp_path,
        "pass_metadata": PassMetadataLookup({}),
    }
    kwargs.update(over)
    return FinalizeContext(**kwargs)


# --- Sub-step 3: recompute_run_signature (META-hufaz headline) --------------------------
def test_recompute_run_signature_rehashes_from_final_fields(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("python")])
    _finalize_recompute_run_signature(ctx)
    expected = _compute_run_signature("python", "9.9.9", "sha256:cfg", _TC)
    assert ctx.analysis_runs[0]["run_signature"] == expected
    assert ctx.analysis_runs[0]["run_signature"] != "sha256:STALE_PLACEHOLDER"


def test_recompute_reads_pass_key_not_pass_id(tmp_path: Path) -> None:
    # The dict has no "pass_id" key — reading it would KeyError. This pins the footgun.
    run = _ar("go")
    assert "pass_id" not in run
    ctx = _ctx(tmp_path, analysis_runs=[run])
    _finalize_recompute_run_signature(ctx)  # must not raise
    assert ctx.analysis_runs[0]["run_signature"].startswith("sha256:")


# --- Sub-step 2: stamp_run_lifecycle (pass_version backfill, WI-mipul) ------------------
def test_stamp_backfills_empty_pass_version(tmp_path: Path) -> None:
    pm = PassMetadataLookup({"go": PassMeta("mod.go", _TC, "sha256:realpv")})
    ctx = _ctx(tmp_path, analysis_runs=[_ar("go", pass_version="")], pass_metadata=pm)
    _finalize_stamp_run_lifecycle(ctx)
    assert ctx.analysis_runs[0]["pass_version"] == "sha256:realpv"


def test_stamp_keeps_present_pass_version(tmp_path: Path) -> None:
    pm = PassMetadataLookup({"go": PassMeta("mod.go", _TC, "sha256:realpv")})
    ctx = _ctx(tmp_path, analysis_runs=[_ar("go", pass_version="sha256:already")], pass_metadata=pm)
    _finalize_stamp_run_lifecycle(ctx)
    assert ctx.analysis_runs[0]["pass_version"] == "sha256:already"


def test_stamp_no_metadata_leaves_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("unknown", pass_version="")])
    _finalize_stamp_run_lifecycle(ctx)  # pass_metadata.get -> None
    assert ctx.analysis_runs[0]["pass_version"] == ""


def test_stamp_empty_metadata_pass_version_leaves_empty(tmp_path: Path) -> None:
    pm = PassMetadataLookup({"go": PassMeta("mod.go", _TC, "")})
    ctx = _ctx(tmp_path, analysis_runs=[_ar("go", pass_version="")], pass_metadata=pm)
    _finalize_stamp_run_lifecycle(ctx)
    assert ctx.analysis_runs[0]["pass_version"] == ""


# --- Sub-step 4: repo_fingerprint -------------------------------------------------------
def test_repo_fingerprint_stamps_none_only(tmp_path: Path) -> None:
    runs = [_ar("a", repo_fingerprint=None), _ar("b", repo_fingerprint="sha256:preset")]
    ctx = _ctx(tmp_path, analysis_runs=runs)
    _finalize_repo_fingerprint(ctx)
    assert ctx.repo_fingerprint  # computed and stashed for _freeze
    assert ctx.analysis_runs[0]["repo_fingerprint"] == ctx.repo_fingerprint
    assert ctx.analysis_runs[1]["repo_fingerprint"] == "sha256:preset"


# --- Sub-step 6: skipped_into_limits ----------------------------------------------------
def test_skipped_into_limits_sets_reason(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("a", files_skipped=3)])
    _finalize_skipped_into_limits(ctx)
    assert ctx.limits.partial_results_reason == "some files skipped during analysis"
    assert ctx.behavior_map["limits"] == ctx.limits.to_dict()


def test_skipped_into_limits_does_not_clobber_crash_reason(tmp_path: Path) -> None:
    limits = Limits()
    limits.partial_results_reason = "pass X crashed"
    ctx = _ctx(tmp_path, analysis_runs=[_ar("a", files_skipped=3)], limits=limits)
    _finalize_skipped_into_limits(ctx)
    assert ctx.limits.partial_results_reason == "pass X crashed"


# --- Sub-step 6: skipped_languages population (WI-nihir) --------------------------------
def test_skipped_languages_populated_for_detected_unanalyzed_language(
    tmp_path: Path,
) -> None:
    # go is DETECTED by the profile but no analyzer pass ran for it (e.g. its
    # grammar failed to load) -> it must surface in limits.skipped_languages;
    # python is analyzed (a run exists) so it must not. Before WI-nihir the
    # add_skipped_language setter had zero callers and this was always [].
    ctx = _ctx(
        tmp_path,
        behavior_map={
            "profile": {"languages": {"go": {"files": 2}, "python": {"files": 5}}}
        },
        analysis_runs=[_ar("python")],
    )
    _finalize_skipped_into_limits(ctx)
    assert ctx.behavior_map["limits"]["skipped_languages"] == ["go"]


def test_skipped_languages_empty_when_all_detected_languages_analyzed(
    tmp_path: Path,
) -> None:
    ctx = _ctx(
        tmp_path,
        behavior_map={"profile": {"languages": {"python": {"files": 5}}}},
        analysis_runs=[_ar("python")],
    )
    _finalize_skipped_into_limits(ctx)
    assert ctx.behavior_map["limits"]["skipped_languages"] == []


def test_skipped_languages_excludes_config_only_languages(tmp_path: Path) -> None:
    # json is config-only (no code analyzer by design) so it is never "skipped";
    # haskell is a code language the profile detected but no run covered -> it IS
    # skipped. Proves the config-language filter without masking a real skip.
    ctx = _ctx(
        tmp_path,
        behavior_map={
            "profile": {"languages": {"json": {"files": 3}, "haskell": {"files": 2}}}
        },
        analysis_runs=[],
    )
    _finalize_skipped_into_limits(ctx)
    assert ctx.behavior_map["limits"]["skipped_languages"] == ["haskell"]


# --- Sub-step 8: commit_dicts -----------------------------------------------------------
class _FakeRecord:
    def __init__(self, d: dict) -> None:
        self._d = d

    def to_dict(self) -> dict:
        return self._d


def test_commit_dicts_writes_reconciled_view(tmp_path: Path) -> None:
    runs = [_ar("a")]
    ctx = _ctx(
        tmp_path,
        symbols=[_FakeRecord({"id": "n1"})],
        edges=[_FakeRecord({"src": "n1", "dst": "n2"})],
        usage_contexts=[_FakeRecord({"id": "uc1"})],
        analysis_runs=runs,
    )
    _finalize_commit_dicts(ctx)
    assert ctx.behavior_map["nodes"] == [{"id": "n1"}]
    assert ctx.behavior_map["edges"] == [{"src": "n1", "dst": "n2"}]
    assert ctx.behavior_map["usage_contexts"] == [{"id": "uc1"}]
    assert ctx.behavior_map["analysis_runs"] is runs


# --- Sub-step 1: re-relativize backstop -------------------------------------------------
def test_re_relativize_backstop_is_noop_on_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _finalize_re_relativize(ctx)  # must not raise on empty lists


# --- Sub-step 10: referential_integrity (validate_ir lift) ------------------------------
def test_referential_integrity_extends_violations(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("a")])
    _finalize_referential_integrity(ctx)
    assert ctx.violations == validate_ir([], [], ctx.analysis_runs)


# --- Orchestrator: full run, frozen handle, idempotency --------------------------------
def test_finalize_returns_frozen_reconciled_map(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("python"), _ar("go", files_skipped=1)])
    result = finalize(ctx)
    assert isinstance(result, FinalizedMap)
    assert result.behavior_map is ctx.behavior_map
    assert "validation_report" in ctx.behavior_map
    assert ctx.behavior_map["nodes"] == []
    assert ctx.behavior_map["analysis_runs"] is ctx.analysis_runs
    assert result.repo_fingerprint == ctx.repo_fingerprint
    # every AR's run_signature is now self-consistent (META-hufaz)
    for run in ctx.analysis_runs:
        assert run["run_signature"] == _compute_run_signature(
            run["pass"], run["version"], run["config_fingerprint"], run["toolchain"]
        )


def test_finalizedmap_is_frozen(tmp_path: Path) -> None:
    result = finalize(_ctx(tmp_path, analysis_runs=[_ar("a")]))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.repo_fingerprint = "x"  # type: ignore[misc]


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("python", files_skipped=2)])
    finalize(ctx)
    import copy

    first = copy.deepcopy(ctx.behavior_map)
    finalize(ctx)  # second pass over the already-reconciled ctx
    assert ctx.behavior_map == first


# --- R2 / R3 white-box ordering guards --------------------------------------------------
_SUBSTEPS = [
    "_finalize_re_relativize",
    "_finalize_stamp_run_lifecycle",
    "_finalize_recompute_run_signature",
    "_finalize_repo_fingerprint",
    "_finalize_skipped_into_limits",
    "_finalize_edge_resolution",
    "_finalize_compute_visibility",  # INV-jusot (7b, before commit)
    "_finalize_commit_dicts",
    "_finalize_referential_integrity",
]


def test_substeps_list_matches_module_functions() -> None:
    """``_SUBSTEPS`` must enumerate exactly the ``_finalize_*`` functions DEFINED in the module.

    Pins the *defined* set against the hand-maintained list: a re-introduced sub-step missing
    from ``_SUBSTEPS`` (e.g. a resurrected declared-fields or confidence slot — both removed
    because their work lands in the validator/producers, not finalize) or a stale ``_SUBSTEPS``
    entry with no definition fails here. The complementary ``test_finalize_calls_each_substep_once`` ties the
    ``finalize()`` *body* (the order contract) to ``_SUBSTEPS`` — together they keep all three
    hand-maintained lists (definitions, ``_SUBSTEPS``, call sites) in lockstep.
    """
    import hypergumbo_core.finalize as F

    module_substeps = {name for name in dir(F) if name.startswith("_finalize_")}
    assert module_substeps == set(_SUBSTEPS)


def _record_order(monkeypatch) -> list[str]:
    import hypergumbo_core.finalize as F

    calls: list[str] = []
    for name in _SUBSTEPS:
        monkeypatch.setattr(F, name, lambda ctx, n=name: calls.append(n))
    return calls


def test_r2_recompute_strictly_after_stamp(monkeypatch, tmp_path: Path) -> None:
    calls = _record_order(monkeypatch)
    finalize(_ctx(tmp_path))
    assert calls.index("_finalize_stamp_run_lifecycle") < calls.index(
        "_finalize_recompute_run_signature"
    )


def test_r3_referential_integrity_is_last_substep(monkeypatch, tmp_path: Path) -> None:
    calls = _record_order(monkeypatch)
    finalize(_ctx(tmp_path))
    assert calls[-1] == "_finalize_referential_integrity"


def test_finalize_calls_each_substep_once(monkeypatch, tmp_path: Path) -> None:
    """The ``finalize()`` body must call exactly the registered sub-steps, each once.

    Ties the body (the actual order contract) to ``_SUBSTEPS`` so a *half-removal* — dropping a
    sub-step's call while leaving its definition + ``_SUBSTEPS`` entry — fails here rather than
    surviving as a silent never-invoked function (caught only as a coverage miss). Multiset
    equality, not sequence equality: it pins membership + count without over-constraining the
    R8 order-free remainder (only R2/R3 are hard orderings, pinned by the guards above).
    """
    calls = _record_order(monkeypatch)
    finalize(_ctx(tmp_path))
    assert sorted(calls) == sorted(_SUBSTEPS)


# --- Violation sort determinism (ADR-0043 §6: "violations sorted before the report") ----
def test_violation_sort_key_is_total_and_class_primary() -> None:
    a = ValidationViolation(severity="error", validator_class="id_format", message="z")
    b = ValidationViolation(severity="error", validator_class="axis_conformance", message="a")
    c = ValidationViolation(severity="warning", validator_class="axis_conformance", message="a")
    ordered = sorted([a, b, c], key=_violation_sort_key)
    # validator_class is the primary key (axis_conformance < id_format) …
    assert [v.validator_class for v in ordered] == [
        "axis_conformance", "axis_conformance", "id_format",
    ]
    # … then severity within a class (error < warning).
    assert ordered[0] is b and ordered[1] is c


def test_finalize_emits_sorted_violations(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_runs=[_ar("a")])
    finalize(ctx)
    assert ctx.violations == sorted(ctx.violations, key=_violation_sort_key)


# --- Sub-step 7: edge-resolution verdict (ADR-0037 rulings 1/2/5) -----------------------
def _span() -> Span:
    return Span(start_line=1, end_line=1, start_col=0, end_col=0)


def _node(node_id: str, kind: str, *, language: str | None = "python", name: str = "n") -> Symbol:
    """A minimal Symbol. external_symbol placeholders carry language=None by convention."""
    return Symbol(
        id=node_id,
        name=name,
        kind=kind,
        language=(None if kind == "external_symbol" else language),
        path="m.py",
        span=_span(),
        origin=["p"],
        origin_run_id="r1",
    )


def _redge(dst: str, *, is_resolved: bool = True, dst_ref=None, src: str = "src:1") -> Edge:
    return Edge.create(
        src=src,
        dst=dst,
        edge_type="calls",
        line=1,
        origin="p",
        origin_run_id="r1",
        is_resolved=is_resolved,
        dst_ref=dst_ref,
    )


def test_edge_resolution_first_party_sets_resolved(tmp_path: Path) -> None:
    """dst is a real (non-external_symbol) node ⇒ is_resolved=True, dst_ref cleared."""
    node = _node("m.py:1-1:f:function", "function")
    # Producer wrongly stamped False + a dst_ref; the verdict overrides to first-party.
    edge = _redge("m.py:1-1:f:function", is_resolved=False, dst_ref=ExternalRef("python", "m", "f"))
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is True
    assert edge.dst_ref is None


def test_edge_resolution_external_placeholder_sets_unresolved(tmp_path: Path) -> None:
    """dst is an external_symbol placeholder ⇒ is_resolved=False, dst_ref derived (WI-kukuk/WI-zuhon)."""
    node = _node("python:os.path:0-0:join:attribute", "external_symbol", name="join")
    edge = _redge("python:os.path:0-0:join:attribute", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is False
    assert edge.dst_ref == ExternalRef(lang="python", module_path="os.path", name="join")


def test_edge_resolution_preserves_producer_dst_ref(tmp_path: Path) -> None:
    """An external edge that already carries a dst_ref keeps it (the 987-import case)."""
    node = _node("python:requests:0-0:get:external_symbol", "external_symbol", name="get")
    ref = ExternalRef(lang="python", module_path="requests", name="requests.get")
    edge = _redge("python:requests:0-0:get:external_symbol", is_resolved=True, dst_ref=ref)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is False
    assert edge.dst_ref is ref  # not overwritten


def test_edge_resolution_dangling_dst_unidentified(tmp_path: Path) -> None:
    """dst absent from the node set with a malformed id ⇒ is_resolved=False, dst_ref None (dangling cell)."""
    edge = _redge("short:id", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is False
    assert edge.dst_ref is None


def test_edge_resolution_overrides_producer_advisory(tmp_path: Path) -> None:
    """The 4,507-edge WI-kukuk contradiction: producer is_resolved=True on an external
    placeholder is flipped to False. This is the structural close."""
    node = _node("c:libfoo:0-0:foo:unresolved", "external_symbol", name="foo")
    edge = _redge("c:libfoo:0-0:foo:unresolved", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is False
    assert edge.dst_ref == ExternalRef(lang="c", module_path="libfoo", name="foo")


def test_edge_resolution_is_idempotent(tmp_path: Path) -> None:
    node = _node("python:os.path:0-0:join:attribute", "external_symbol", name="join")
    edge = _redge("python:os.path:0-0:join:attribute", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    first = (edge.is_resolved, edge.dst_ref)
    _finalize_edge_resolution(ctx)
    assert (edge.is_resolved, edge.dst_ref) == first


def test_edge_resolution_runs_before_commit_and_validate(monkeypatch, tmp_path: Path) -> None:
    """Order guard: the verdict sub-step runs before commit (so the serialized edges array
    reflects it) and before referential-integrity (so the FK predicate validates it)."""
    calls = _record_order(monkeypatch)
    finalize(_ctx(tmp_path))
    assert calls.index("_finalize_edge_resolution") < calls.index("_finalize_commit_dicts")
    assert calls.index("_finalize_edge_resolution") < calls.index(
        "_finalize_referential_integrity"
    )


def test_finalize_serializes_edge_resolution_verdict(tmp_path: Path) -> None:
    """End-to-end: the committed behavior_map["edges"] carries the verdict, not the
    producer-stamped advisory value (proves placement-before-commit)."""
    node = _node("python:os.path:0-0:join:attribute", "external_symbol", name="join")
    edge = _redge("python:os.path:0-0:join:attribute", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge], analysis_runs=[_ar("python")])
    finalize(ctx)
    committed = ctx.behavior_map["edges"][0]
    assert committed["is_resolved"] is False
    assert committed["dst_ref"] == {"lang": "python", "module_path": "os.path", "name": "join"}


# --- _derive_dst_ref_from_id backstop ---------------------------------------------------
def test_derive_dst_ref_from_id_module_attr() -> None:
    ref = _derive_dst_ref_from_id("python:os.path:0-0:os.path.join:attribute")
    assert ref == ExternalRef(lang="python", module_path="os.path", name="os.path.join")


def test_derive_dst_ref_from_id_malformed_returns_none() -> None:
    assert _derive_dst_ref_from_id("short:id") is None


def test_derive_dst_ref_from_id_external_sentinel_returns_none() -> None:
    """The "external" module sentinel is unidentified — never fabricate a path (WI-huzuv)."""
    assert _derive_dst_ref_from_id("go:external:0-0:unknownFunc:unresolved") is None


def test_edge_resolution_external_sentinel_leaves_dst_ref_none(tmp_path: Path) -> None:
    """An external placeholder whose module is the "external" sentinel is the
    unidentified-reference cell: is_resolved=False, dst_ref=None."""
    node = _node("go:external:0-0:unknownFunc:unresolved", "external_symbol", name="unknownFunc")
    edge = _redge("go:external:0-0:unknownFunc:unresolved", is_resolved=True, dst_ref=None)
    ctx = _ctx(tmp_path, symbols=[node], edges=[edge])
    _finalize_edge_resolution(ctx)
    assert edge.is_resolved is False
    assert edge.dst_ref is None


# --- Sub-step 7b: visibility fold (INV-jusot) -------------------------------------------
def _sym(name: str, *, language: str = "python", modifiers=None, meta=None) -> Symbol:
    return Symbol(
        id=f"{language}:m.py:1-1:{name}:function",
        name=name,
        kind="function",
        language=language,
        path="m.py",
        span=Span(1, 1, 0, 0),
        origin="python",
        origin_run_id="uuid:test",
        modifiers=list(modifiers or []),
        meta=meta,
    )


def test_finalize_compute_visibility_language_modifier(tmp_path: Path) -> None:
    s = _sym("x", language="rust", modifiers=["pub"])
    ctx = _ctx(tmp_path, symbols=[s])
    _finalize_compute_visibility(ctx)
    assert s.visibility == "public"
    assert s.meta["visibility_signal"] == "language_modifier"


def test_finalize_compute_visibility_python_underscore(tmp_path: Path) -> None:
    s = _sym("_helper")
    ctx = _ctx(tmp_path, symbols=[s])
    _finalize_compute_visibility(ctx)
    assert s.visibility == "private"
    assert s.meta["visibility_signal"] == "name_convention"


def test_finalize_compute_visibility_default_public(tmp_path: Path) -> None:
    s = _sym("run")
    ctx = _ctx(tmp_path, symbols=[s])
    _finalize_compute_visibility(ctx)
    assert s.visibility == "public"
    assert s.meta["visibility_signal"] == "default"


def test_finalize_compute_visibility_folds_and_removes_legacy_meta(tmp_path: Path) -> None:
    # Apex/Clojure wrote meta['visibility']; finalize folds it into the field
    # and REMOVES the legacy key (INV-jusot retires it).
    s = _sym("Foo", language="apex", meta={"visibility": "global", "other": 1})
    ctx = _ctx(tmp_path, symbols=[s])
    _finalize_compute_visibility(ctx)
    assert s.visibility == "public"
    assert s.meta["visibility_signal"] == "language_modifier"
    assert "visibility" not in s.meta  # legacy key removed
    assert s.meta["other"] == 1        # other meta untouched


def test_finalize_compute_visibility_handles_none_meta(tmp_path: Path) -> None:
    s = _sym("run", meta=None)
    ctx = _ctx(tmp_path, symbols=[s])
    _finalize_compute_visibility(ctx)
    assert s.visibility == "public"
    assert s.meta == {"visibility_signal": "default"}


# --- Sub-step 7b: INV-jusot follow-up (is_exported reconcile + modifiers strip) ---------
def _sym_ex(name, *, language="python", modifiers=None, is_exported=False, meta=None) -> Symbol:
    return Symbol(
        id=f"{language}:m.py:1-1:{name}:function",
        name=name, kind="function", language=language, path="m.py",
        span=Span(1, 1, 0, 0), origin="python", origin_run_id="uuid:test",
        modifiers=list(modifiers or []), is_exported=is_exported, meta=meta,
    )


def test_finalize_downgrades_is_exported_for_non_public(tmp_path: Path) -> None:
    # The 5-symbol case: path heuristic marked a private _foo exported.
    s = _sym_ex("_helper", is_exported=True)  # visibility -> private (name)
    _finalize_compute_visibility(_ctx(tmp_path, symbols=[s]))
    assert s.visibility == "private"
    assert s.is_exported is False  # downgraded — private cannot be exported


def test_finalize_does_not_upgrade_is_exported_for_public(tmp_path: Path) -> None:
    # A public test function stays non-exported (necessary-not-sufficient):
    # visibility=='public' alone must NOT flip is_exported True.
    s = _sym_ex("test_thing", is_exported=False)  # visibility -> public
    _finalize_compute_visibility(_ctx(tmp_path, symbols=[s]))
    assert s.visibility == "public"
    assert s.is_exported is False  # NOT upgraded


def test_finalize_keeps_is_exported_true_for_public(tmp_path: Path) -> None:
    s = _sym_ex("public_api", is_exported=True)  # visibility -> public
    _finalize_compute_visibility(_ctx(tmp_path, symbols=[s]))
    assert s.visibility == "public"
    assert s.is_exported is True  # public API member, kept


def test_finalize_strips_visibility_modifiers_keeps_others(tmp_path: Path) -> None:
    s = _sym_ex("f", language="java", modifiers=["public", "static", "final"])
    _finalize_compute_visibility(_ctx(tmp_path, symbols=[s]))
    assert s.visibility == "public"
    assert s.modifiers == ["static", "final"]  # visibility term 'public' removed
