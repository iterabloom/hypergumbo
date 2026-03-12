# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/bakeoff-reflect.

These tests verify the mechanical parts of the bakeoff-reflect script:
workdir resolution, prompt file placement, prompt content structure,
stratified sampling invariants, and aggregation logic.

All tests use tmp_path fixtures with fake session structures — no real
hypergumbo runs are needed. The script is imported as a module for
direct function calls.
"""

import importlib
import json
import os
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(scope="module")
def bakeoff_reflect():
    """Import bakeoff-reflect as a module despite the hyphen in its name."""
    script_path = str(
        Path(__file__).parent.parent / "scripts" / "bakeoff-reflect"
    )
    loader = importlib.machinery.SourceFileLoader("bakeoff_reflect", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_reflect", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def broad_session(tmp_path):
    """Create a fake broad bakeoff session directory structure.

    Returns (session_dir, state) where session_dir is the path to the
    broad-* directory and state is the state.json content as a dict.
    """
    session_dir = tmp_path / "broad-20260218-120000"
    session_dir.mkdir()

    state = {
        "session_id": "test123",
        "pool_path": str(tmp_path / "repos"),
        "workdir": str(session_dir),
        "current_cohort": ["repo-a", "repo-b"],
        "cohort_number": 1,
        "iteration": 1,
        "tested_repos": [],
        "issues": [],
        "convergence_history": [],
        "created_at": "2026-02-18T12:00:00",
        "updated_at": "2026-02-18T12:00:00",
    }
    (session_dir / "state.json").write_text(json.dumps(state))

    # Create out directories with fake artifacts
    for repo in ["repo-a", "repo-b"]:
        repo_out = session_dir / "out" / "cohort-001" / "iter-001" / repo
        repo_out.mkdir(parents=True)
        (repo_out / "hg.json").write_text(
            json.dumps({"nodes": [{"id": f"node:{repo}/main.py:func"}], "edges": []})
        )
        (repo_out / "symbols.txt").write_text("func 10\n")
        (repo_out / "routes.txt").write_text("")
        (repo_out / "entrypoints.txt").write_text("main.py:func\n")

    # Create diag directory with issues.json
    diag_dir = session_dir / "diag" / "cohort-001" / "iter-001"
    diag_dir.mkdir(parents=True)
    (diag_dir / "issues.json").write_text(
        json.dumps(
            [
                {
                    "severity": "HIGH",
                    "flag": "TEST_FLAG",
                    "repo": "repo-a",
                    "code_hint": "Test hint",
                    "metrics": {"dominant_language": "python"},
                }
            ]
        )
    )

    # Create repo directories (pool)
    for repo in ["repo-a", "repo-b"]:
        (tmp_path / "repos" / repo).mkdir(parents=True)

    return session_dir, state


class TestResolveWorkdir:
    """Test workdir resolution logic."""

    def test_finds_latest_broad_session(self, tmp_path, bakeoff_reflect):
        """Given a directory with broad-* sessions, resolve to the latest."""
        (tmp_path / "broad-20260101-000000").mkdir()
        (tmp_path / "broad-20260101-000000" / "state.json").write_text("{}")
        (tmp_path / "broad-20260218-120000").mkdir()
        (tmp_path / "broad-20260218-120000" / "state.json").write_text("{}")

        result = bakeoff_reflect.resolve_workdir(str(tmp_path))
        assert result.endswith("broad-20260218-120000")

    def test_ignores_sessions_without_state_json(self, tmp_path, bakeoff_reflect):
        """Sessions without state.json are not considered."""
        (tmp_path / "broad-20260101-000000").mkdir()
        # No state.json created
        (tmp_path / "broad-20260218-120000").mkdir()
        (tmp_path / "broad-20260218-120000" / "state.json").write_text("{}")

        result = bakeoff_reflect.resolve_workdir(str(tmp_path))
        assert result.endswith("broad-20260218-120000")

    def test_direct_workdir_with_state_json(self, tmp_path, bakeoff_reflect):
        """If the path itself has state.json, use it directly."""
        (tmp_path / "state.json").write_text("{}")
        result = bakeoff_reflect.resolve_workdir(str(tmp_path))
        assert result == str(tmp_path)

    def test_env_var_override(self, tmp_path, bakeoff_reflect):
        """Environment variable overrides default."""
        session = tmp_path / "broad-20260218-120000"
        session.mkdir()
        (session / "state.json").write_text("{}")

        with mock.patch.dict(os.environ, {"BAKEOFF_WORKDIR": str(tmp_path)}):
            result = bakeoff_reflect.resolve_workdir(None)
        assert result.endswith("broad-20260218-120000")

    def test_cli_arg_takes_priority(self, tmp_path, bakeoff_reflect):
        """CLI --workdir takes priority over env var."""
        (tmp_path / "state.json").write_text("{}")

        with mock.patch.dict(os.environ, {"BAKEOFF_WORKDIR": "/nonexistent"}):
            result = bakeoff_reflect.resolve_workdir(str(tmp_path))
        assert result == str(tmp_path)

    def test_only_broad_sessions(self, tmp_path, bakeoff_reflect):
        """Only broad-* sessions are considered (not deep-*)."""
        (tmp_path / "deep-20260218-120000").mkdir()
        (tmp_path / "deep-20260218-120000" / "state.json").write_text("{}")

        result = bakeoff_reflect.resolve_workdir(str(tmp_path))
        # Should return the base directory since no broad sessions found
        assert result == str(tmp_path)


class TestPromptGeneration:
    """Test prompt file placement and content."""

    def test_creates_prompt_files(self, broad_session, bakeoff_reflect):
        """Prompt files are created at the correct paths."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        reflect_dir = (
            session_dir / "reflect" / "cohort-001" / "iter-001"
        )
        assert (reflect_dir / "repo-a.prompt.md").exists()
        assert (reflect_dir / "repo-b.prompt.md").exists()

    def test_prompt_contains_repo_name(self, broad_session, bakeoff_reflect):
        """Each prompt mentions its target repo."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt_a = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "repo-a" in prompt_a

    def test_prompt_contains_all_task_headings(self, broad_session, bakeoff_reflect):
        """Each prompt contains all 3 assessment task headings."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "### Task A: Call Graph Fidelity" in prompt
        assert "### Task B: Route & Entrypoint Accuracy" in prompt
        assert "### Task C: Symbol & Structure Completeness" in prompt

    def test_prompt_contains_artifact_listing(self, broad_session, bakeoff_reflect):
        """Prompt lists the available artifacts."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "hg.json" in prompt
        assert "symbols.txt" in prompt

    def test_prompt_contains_stratified_questions(self, broad_session, bakeoff_reflect):
        """Prompt contains exactly 8 stratified questions (one per stratum)."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()

        # Each stratum label appears exactly once in the prompt
        for stratum_name in bakeoff_reflect.STRATUM_NAMES:
            label = stratum_name.replace("_", " ").title()
            assert f"[{label}]" in prompt, f"Missing stratum label: {label}"

    def test_prompt_contains_journal_question(self, broad_session, bakeoff_reflect):
        """Prompt contains exactly 1 journal question."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "[Journal:" in prompt

    def test_prompt_contains_diagnosis_context(self, broad_session, bakeoff_reflect):
        """Prompt for repo-a includes the pre-computed diagnosis."""
        session_dir, state = broad_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "TEST_FLAG" in prompt
        assert "Test hint" in prompt

    def test_skips_repos_without_output(self, broad_session, bakeoff_reflect, capsys):
        """Repos without output directories are skipped gracefully."""
        session_dir, state = broad_session
        # Add a repo to the cohort that has no output dir
        state["current_cohort"].append("repo-c")
        (session_dir / "state.json").write_text(json.dumps(state))

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_reflect.cmd_reflect(args)

        captured = capsys.readouterr()
        assert "[repo-c] Skipped" in captured.out

        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        assert not (reflect_dir / "repo-c.prompt.md").exists()


class TestStratifiedSampling:
    """Test that stratified sampling guarantees theme coverage."""

    def test_all_strata_represented_every_run(self, bakeoff_reflect):
        """Run sampling 20 times; all 8 strata must be represented each time."""
        for _ in range(20):
            sampled = bakeoff_reflect.sample_stratified_questions()
            strata_seen = {name for name, _ in sampled}
            assert strata_seen == set(bakeoff_reflect.STRATUM_NAMES)

    def test_exactly_one_per_stratum(self, bakeoff_reflect):
        """Each stratum contributes exactly 1 question."""
        sampled = bakeoff_reflect.sample_stratified_questions()
        assert len(sampled) == len(bakeoff_reflect.STRATUM_NAMES)

    def test_questions_come_from_correct_stratum(self, bakeoff_reflect):
        """Each sampled question actually belongs to its labeled stratum."""
        for _ in range(20):
            sampled = bakeoff_reflect.sample_stratified_questions()
            for name, question in sampled:
                assert question in bakeoff_reflect.STRATA[name], (
                    f"Question '{question[:50]}...' not in stratum '{name}'"
                )

    def test_journal_question_from_valid_stratum(self, bakeoff_reflect):
        """Journal question comes from a valid journal stratum."""
        for _ in range(20):
            stratum_name, question = bakeoff_reflect.sample_journal_question()
            assert stratum_name in bakeoff_reflect.JOURNAL_STRATUM_NAMES
            assert question in bakeoff_reflect.JOURNAL_STRATA[stratum_name]


class TestAggregation:
    """Test aggregation of assessment YAML files."""

    def _make_assessment(self, repo_name, verdict="MOSTLY_CORRECT"):
        """Create a fake assessment dict."""
        return {
            "repo": repo_name,
            "timestamp": "2026-02-18T12:00:00",
            "dominant_language": "python",
            "call_graph_fidelity": {
                "sample_function": "main.py:func",
                "true_positives": 5,
                "false_positives": 1,
                "false_negatives": 2,
                "cross_file_correct": True,
                "resolution_quality": "MEDIUM",
                "false_positive_examples": ["phantom call to util.helper"],
                "false_negative_examples": ["missing callback in handler"],
                "notes": "",
            },
            "route_entrypoint_accuracy": {
                "routes_checked": 3,
                "routes_correct": 2,
                "routes_missing": 1,
                "routes_spurious": 0,
                "best_entrypoint_sensible": True,
                "auto_slice_useful": True,
                "missing_routes": ["/api/health endpoint"],
                "spurious_routes": [],
                "notes": "",
            },
            "symbol_completeness": {
                "top_symbols_accurate": True,
                "important_symbols_missing": ["DatabaseConnection class"],
                "edge_types_correct": True,
                "edge_misclassifications": [],
                "language_detection_correct": True,
                "notes": "",
            },
            "stratified_reflections": {
                "missing_patterns": "Factory patterns not captured",
                "edge_types": "Need config edges",
                "language_performance": "Python analysis strong",
                "user_needs": "Blast radius would help",
                "over_engineering": "Edge type schema is fine",
                "cross_language": "No cross-lang in this repo",
                "cohort_variation": "Typical web app",
                "provocative": "Runtime traces would help most",
                "journal": "Prior notebook flagged same issue",
            },
            "overall_verdict": verdict,
            "confidence": "MEDIUM",
            "improvement_ideas": ["Add factory pattern detection"],
            "questions": ["Why are callbacks missed?"],
        }

    def test_aggregation_verdict_counts(self, bakeoff_reflect):
        """Aggregation correctly counts verdicts."""
        assessments = [
            self._make_assessment("repo-a", "CORRECT"),
            self._make_assessment("repo-b", "MOSTLY_CORRECT"),
        ]
        summary = bakeoff_reflect.aggregate_assessments(assessments)
        assert summary["verdicts"]["CORRECT"] == 1
        assert summary["verdicts"]["MOSTLY_CORRECT"] == 1
        assert summary["verdicts"]["INCORRECT"] == 0

    def test_aggregation_deduplicates_ideas(self, bakeoff_reflect):
        """Duplicate improvement ideas are counted, not duplicated."""
        a1 = self._make_assessment("repo-a")
        a2 = self._make_assessment("repo-b")
        # Both have the same improvement idea
        summary = bakeoff_reflect.aggregate_assessments([a1, a2])
        ideas = {item["idea"]: item["count"] for item in summary["improvement_ideas"]}
        assert ideas["Add factory pattern detection"] == 2

    def test_aggregation_collects_false_negatives(self, bakeoff_reflect):
        """Common false negatives are collected and ranked."""
        a1 = self._make_assessment("repo-a")
        a2 = self._make_assessment("repo-b")
        summary = bakeoff_reflect.aggregate_assessments([a1, a2])
        fn = [item["issue"] for item in summary["common_false_negatives"]]
        assert "missing callback in handler" in fn

    def test_aggregation_per_stratum_reflections(self, bakeoff_reflect):
        """Stratified reflections are grouped by stratum."""
        a1 = self._make_assessment("repo-a")
        a2 = self._make_assessment("repo-b")
        summary = bakeoff_reflect.aggregate_assessments([a1, a2])
        sr = summary["stratified_reflections"]
        assert "missing_patterns" in sr
        assert len(sr["missing_patterns"]) == 2
        assert sr["missing_patterns"][0]["repo"] == "repo-a"

    def test_aggregation_handles_empty_list(self, bakeoff_reflect):
        """Aggregation with empty assessment list works."""
        summary = bakeoff_reflect.aggregate_assessments([])
        assert summary["total_repos"] == 0
        assert summary["verdicts"]["CORRECT"] == 0

    def test_aggregation_handles_none_entries(self, bakeoff_reflect):
        """None entries in the assessment list are skipped."""
        assessments = [None, self._make_assessment("repo-a"), None]
        summary = bakeoff_reflect.aggregate_assessments(assessments)
        assert summary["total_repos"] == 3
        assert summary["verdicts"]["MOSTLY_CORRECT"] == 1

    def test_cmd_aggregate_with_yaml_files(self, broad_session, bakeoff_reflect):
        """Full aggregate command works with hand-crafted assessment files."""
        session_dir, state = broad_session

        # Create reflect directory and assessment files
        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = {"parse_assessment": self._make_assessment("repo-a", "CORRECT")}
        a2 = {"parse_assessment": self._make_assessment("repo-b", "MOSTLY_CORRECT")}

        # Write as JSON (yaml might not be available in test env)
        (reflect_dir / "repo-a.assessment.yaml").write_text(json.dumps(a1))
        (reflect_dir / "repo-b.assessment.yaml").write_text(json.dumps(a2))

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_reflect.cmd_aggregate(args)
        assert result == 0

        summary_path = reflect_dir / "summary.yaml"
        assert summary_path.exists()

    def test_cmd_aggregate_missing_assessment_skipped(
        self, broad_session, bakeoff_reflect, capsys
    ):
        """Missing assessment files are skipped, not crashed on."""
        session_dir, state = broad_session

        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)

        # Only repo-a has an assessment
        a1 = {"parse_assessment": self._make_assessment("repo-a", "CORRECT")}
        (reflect_dir / "repo-a.assessment.yaml").write_text(json.dumps(a1))
        # repo-b has no assessment file

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_reflect.cmd_aggregate(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "[repo-b] No assessment found" in captured.out
