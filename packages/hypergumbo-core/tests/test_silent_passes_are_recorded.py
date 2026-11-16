# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pass that ran and found nothing is in analysis_runs, with why (WI-tivif).

Framework-pattern enrichment was not a recorded pass at all. Six orchestrator
post-passes appended their AnalysisRun only inside an ``if <output>:`` guard:
route-materializer, django-cbv-method-expander,
boundary_external_symbol_synthesis, enclosure-linker and
orchestrator_file_symbol_synthesis. So a consumer could not
tell "ran and found nothing" from "never ran", and a falsifier phrased as "every
silent pass-run carries a reason" was satisfiable by the run DISAPPEARING
(the T6 pre-registration note on this item).

producer-merge keeps its no-op behaviour on purpose: ADR-0057 requires a
single-backend run to be byte-identical through the merge pass, so it appends a
run only when it merged something.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

_SILENT_HERE = (
    "route-materializer",
    "django-cbv-method-expander",
    "enclosure-linker",
)


@pytest.fixture(scope="module")
def survey(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("repo")
    (root / "app.py").write_text(
        "def main():\n    print('hi')\n\n\nif __name__ == '__main__':\n    main()\n"
    )
    out = root / "out.json"
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _runs(survey: dict) -> dict[str, dict]:
    return {r["pass"]: r for r in survey["analysis_runs"]}


def test_framework_pattern_enrichment_is_a_recorded_pass(survey: dict) -> None:
    run = _runs(survey)["framework-patterns"]
    counts = survey["metrics"]["framework_patterns"]
    # Every YAML that was loaded is listed, matched or not, so "loaded and
    # found nothing" (0) is told apart from "not loaded" (absent).
    assert "main-functions" in counts and "test-frameworks" in counts
    assert counts["main-functions"] >= 1
    assert all(isinstance(v, int) and v >= 0 for v in counts.values())
    assert not run.get("silence_reason")


@pytest.mark.parametrize("pass_id", _SILENT_HERE)
def test_a_post_pass_that_found_nothing_is_recorded_with_its_reason(
    survey: dict, pass_id: str,
) -> None:
    run = _runs(survey)[pass_id]
    assert run["nodes_emitted"] == 0 and run["edges_emitted"] == 0
    assert run["silence_reason"] == "no_candidate_construct"


def test_the_synthesis_passes_are_recorded_either_way(survey: dict) -> None:
    runs = _runs(survey)
    for pass_id in ("boundary_external_symbol_synthesis", "orchestrator_file_symbol_synthesis"):
        run = runs[pass_id]
        silent = not (run["nodes_emitted"] or run["edges_emitted"])
        assert bool(run.get("silence_reason")) == silent, pass_id
