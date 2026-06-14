# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8 closure: finalize emits one reconciled view (run-lifecycle:F1, ADR-0043 §6/§8).

This is the end-to-end closure evidence for META-jalur (+ META-hufaz, WI-mipul): after the
finalize stage, every emitted AnalysisRun is internally consistent and the projections
preserve that one view. It lives in hypergumbo-lang-mainstream because it drives
``run_behavior_map`` over real Python fixtures (the analyzer must be present at runtime — the
same reason ``test_phase_de_reorder.py`` lives here). The per-sub-step mechanics and the
``finalize`` idempotency property are unit-tested in ``hypergumbo-core/tests/test_finalize.py``.

The load-bearing assertion is META-hufaz closure: each AR's ``run_signature`` re-derives from
its OWN final ``pass``/``version``/``config_fingerprint``/``toolchain`` — which only holds
because finalize sub-step 3 re-hashes after the analyzers stamped their final fields (before
F1, the create-time placeholder signature persisted).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import _compute_run_signature

FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/app.py": (
        "from pkg.svc import Service\n"
        "from pkg import thing_pb2\n"
        "\n"
        "def handler():\n"
        "    return Service().run()\n"
    ),
    "pkg/svc.py": (
        "class Service:\n"
        "    def run(self):\n"
        "        return self.stop()\n"
        "\n"
        "    def stop(self):\n"
        "        return 1\n"
    ),
    "pkg/thing_pb2.py": (
        "from google.protobuf import message as _message\n"
        "class Thing(_message.Message):\n"
        "    pass\n"
    ),
}


def _run(tmp_path: Path, **kw) -> tuple[dict, str]:
    for rel, content in FIXTURE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    out = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out, include_sketch_precomputed=False,
        budgets="none", **kw,
    )
    return json.loads(out.read_text()), str(tmp_path)


class TestReconciledView:
    """§8 — one internally-consistent view after finalize."""

    def test_run_signature_self_consistent(self, tmp_path: Path) -> None:
        # META-hufaz closure: every AR's run_signature re-derives from its FINAL fields.
        data, _ = _run(tmp_path)
        runs = data["analysis_runs"]
        assert runs, "no analysis_runs emitted — fixture did not analyze"
        for run in runs:
            assert run["run_signature"] == _compute_run_signature(
                run["pass"], run["version"], run["config_fingerprint"], run["toolchain"]
            ), f"stale run_signature on pass {run['pass']!r} (META-hufaz not closed)"

    def test_single_repo_fingerprint_across_runs(self, tmp_path: Path) -> None:
        data, _ = _run(tmp_path)
        fps = {run["repo_fingerprint"] for run in data["analysis_runs"]}
        assert len(fps) == 1 and None not in fps, f"repo_fingerprint not reconciled: {fps}"

    def test_pass_version_backfilled_everywhere(self, tmp_path: Path) -> None:
        # WI-mipul closure: no AR (incl. the override-analyze passes) leaves pass_version empty.
        data, _ = _run(tmp_path)
        empty = [run["pass"] for run in data["analysis_runs"] if not run.get("pass_version")]
        assert not empty, f"ARs with empty pass_version (backfill missed): {empty}"

    def test_no_absolute_paths(self, tmp_path: Path) -> None:
        # C4: the re-relativize backstop leaves no absolute path in the emitted artifact.
        data, prefix = _run(tmp_path)
        offenders = [n["id"] for n in data["nodes"] if prefix in n["id"]]
        offenders += [e["src"] for e in data["edges"] if prefix in e["src"]]
        offenders += [e["dst"] for e in data["edges"] if prefix in e["dst"]]
        assert not offenders, f"absolute paths survived finalize: {offenders[:5]}"

    def test_validation_report_well_formed(self, tmp_path: Path) -> None:
        data, _ = _run(tmp_path)
        report = data.get("validation_report")
        assert report is not None and "violations_by_class" in report
        assert sum(report["violations_by_class"].values()) == len(report["violations"])

    def test_every_emitted_pass_id_resolves_in_pass_metadata(self, tmp_path: Path) -> None:
        # Empirical keying guard: every pass_id that actually reaches an emitted AnalysisRun
        # must be a key in build_pass_metadata() — catches any analyzer/linker whose emitted
        # pass_id diverges from how the lookup keys it (the view_template PASS_ID class).
        from hypergumbo_core.pass_metadata import build_pass_metadata

        data, _ = _run(tmp_path)
        lookup = build_pass_metadata()
        missing = [r["pass"] for r in data["analysis_runs"] if lookup.get(r["pass"]) is None]
        assert not missing, f"emitted pass_ids not covered by pass_metadata: {missing}"


class TestBudgetTierShape:
    """A budget-tier is a shrunk projection — it must not carry the full-substrate report."""

    def test_budget_tier_omits_validation_report(self, tmp_path: Path) -> None:
        for rel, content in FIXTURE.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        out = tmp_path / "out.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out, include_sketch_precomputed=False, budgets="4k",
        )
        main = json.loads(out.read_text())
        assert "validation_report" in main, "main output should keep validation_report"
        tier = tmp_path / "out.4k.json"
        assert tier.exists(), "budget-tier file not generated"
        tier_data = json.loads(tier.read_text())
        assert "validation_report" not in tier_data, (
            "budget-tier file must not carry the full-substrate validation_report"
        )


class TestProjectionPreservesView:
    """The compact projection (still post-finalize in F1) preserves the reconciled view."""

    def test_compact_preserves_validation_report_and_consistency(self, tmp_path: Path) -> None:
        data, _ = _run(tmp_path, compact=True)
        # finalize wrote validation_report BEFORE compact; format_compact_behavior_map does
        # dict(behavior_map), so it must survive the rebind.
        assert data.get("validation_report") is not None, (
            "validation_report dropped by compact projection"
        )
        assert "limits" in data
        for run in data["analysis_runs"]:
            assert run["run_signature"] == _compute_run_signature(
                run["pass"], run["version"], run["config_fingerprint"], run["toolchain"]
            )
