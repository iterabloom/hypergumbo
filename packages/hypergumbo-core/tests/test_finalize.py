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
    _finalize_commit_dicts,
    _finalize_confidence_aggregates,
    _finalize_declared_fields,
    _finalize_re_relativize,
    _finalize_recompute_run_signature,
    _finalize_referential_integrity,
    _finalize_repo_fingerprint,
    _finalize_skipped_into_limits,
    _finalize_stamp_run_lifecycle,
    _violation_sort_key,
    finalize,
)
from hypergumbo_core.ir import _compute_run_signature
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


# --- Sub-steps 1, 7, 9: backstop + stubs ------------------------------------------------
def test_re_relativize_backstop_is_noop_on_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _finalize_re_relativize(ctx)  # must not raise on empty lists


def test_confidence_stub_is_noop(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    before = dict(ctx.behavior_map)
    assert _finalize_confidence_aggregates(ctx) is None
    assert ctx.behavior_map == before


def test_declared_fields_stub_is_noop(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    before = dict(ctx.behavior_map)
    assert _finalize_declared_fields(ctx) is None
    assert ctx.behavior_map == before


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
    "_finalize_confidence_aggregates",
    "_finalize_commit_dicts",
    "_finalize_declared_fields",
    "_finalize_referential_integrity",
]


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
