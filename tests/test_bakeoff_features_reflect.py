"""Tests for scripts/bakeoff-features-reflect.

These tests verify the mechanical parts of the bakeoff-features-reflect script:
workdir resolution, prompt file placement, prompt content structure,
numeric scoring extraction, trajectory computation, and aggregation logic.

All tests use tmp_path fixtures with fake session structures — no real
hypergumbo runs are needed. The script is imported as a module for
direct function calls.
"""

import importlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(scope="module")
def bakeoff_features_reflect():
    """Import bakeoff-features-reflect as a module despite the hyphen."""
    script_path = str(
        Path(__file__).parent.parent / "scripts" / "bakeoff-features-reflect"
    )
    loader = importlib.machinery.SourceFileLoader(
        "bakeoff_features_reflect", script_path
    )
    spec = importlib.util.spec_from_loader("bakeoff_features_reflect", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def deep_session(tmp_path):
    """Create a fake deep bakeoff session directory structure.

    Returns (session_dir, state) where session_dir is the path to the
    deep-* directory and state is the state.json content as a dict.
    """
    session_dir = tmp_path / "deep-20260218-150000"
    session_dir.mkdir()

    state = {
        "session_id": "deep-test",
        "pool_path": str(tmp_path / "repos"),
        "workdir": str(session_dir),
        "current_cohort": ["repo-a", "repo-b"],
        "cohort_number": 2,
        "iteration": 1,
        "tested_repos": [],
        "created_at": "2026-02-18T15:00:00",
        "updated_at": "2026-02-18T15:00:00",
    }
    (session_dir / "state.json").write_text(json.dumps(state))

    # Create out directories with fake artifacts
    for repo in ["repo-a", "repo-b"]:
        repo_out = session_dir / "out" / "cohort-002" / "iter-001" / repo
        repo_out.mkdir(parents=True)
        (repo_out / "hg.json").write_text(
            json.dumps({"nodes": [{"id": f"node:{repo}/main.py:func"}], "edges": []})
        )
        (repo_out / "symbols.txt").write_text("func  10  5\n")
        (repo_out / "routes.txt").write_text("")
        (repo_out / "entries.txt").write_text("main.py:func\n")
        (repo_out / "slice.0.json").write_text(json.dumps({"nodes": [], "edges": []}))

    # Create repo directories (pool)
    for repo in ["repo-a", "repo-b"]:
        (tmp_path / "repos" / repo).mkdir(parents=True)

    return session_dir, state


def _make_deep_assessment(repo_name, verdict="PARTIALLY_USEFUL", scores=None):
    """Create a fake DEEP assessment dict.

    If scores is provided, it should be a dict with keys:
    refactoring_score, new_feature_score, understanding_score.
    """
    assessment = {
        "repo": repo_name,
        "timestamp": "2026-02-18T15:00:00",
        "refactoring": {
            "target": "src/main.py:process",
            "slice_useful": True,
            "reverse_slice_useful": False,
            "missing": ["callback chain not shown"],
            "noise": ["test utilities included"],
            "notes": "Slice captures direct deps but misses indirect.",
        },
        "new_feature": {
            "scenario": "add new API endpoint",
            "entrypoint_helpful": True,
            "pattern_discoverable": True,
            "tier_filtering_helpful": False,
            "missing": ["middleware pattern not shown"],
            "notes": "Entrypoints found relevant controllers.",
        },
        "understanding": {
            "centrality_accurate": True,
            "architecture_visible": False,
            "cross_file_edges_meaningful": True,
            "missing": ["module boundaries not visible"],
            "noise": ["utility functions dominate centrality"],
            "notes": "Top symbols are real, but architecture unclear.",
        },
        "overall_verdict": verdict,
        "confidence": "HIGH",
        "improvement_ideas": ["Add middleware chain tracing"],
        "questions": ["Why are indirect deps missed in slice?"],
    }

    if scores:
        assessment["task_scores"] = scores

    return assessment


class TestResolveWorkdir:
    """Test workdir resolution logic for DEEP sessions."""

    def test_finds_latest_deep_session(self, tmp_path, bakeoff_features_reflect):
        """Given a directory with deep-* sessions, resolve to the latest."""
        (tmp_path / "deep-20260101-000000").mkdir()
        (tmp_path / "deep-20260101-000000" / "state.json").write_text("{}")
        (tmp_path / "deep-20260218-150000").mkdir()
        (tmp_path / "deep-20260218-150000" / "state.json").write_text("{}")

        result = bakeoff_features_reflect.resolve_workdir(str(tmp_path))
        assert result.endswith("deep-20260218-150000")

    def test_direct_workdir_with_state_json(self, tmp_path, bakeoff_features_reflect):
        """If the path itself has state.json, use it directly."""
        (tmp_path / "state.json").write_text("{}")
        result = bakeoff_features_reflect.resolve_workdir(str(tmp_path))
        assert result == str(tmp_path)

    def test_env_var_override(self, tmp_path, bakeoff_features_reflect):
        """BAKEOFF_FEATURES_WORKDIR env var overrides default."""
        session = tmp_path / "deep-20260218-150000"
        session.mkdir()
        (session / "state.json").write_text("{}")

        with mock.patch.dict(
            os.environ, {"BAKEOFF_FEATURES_WORKDIR": str(tmp_path)}
        ):
            result = bakeoff_features_reflect.resolve_workdir(None)
        assert result.endswith("deep-20260218-150000")


class TestPromptGeneration:
    """Test prompt file placement and content."""

    def test_creates_prompt_files(self, deep_session, bakeoff_features_reflect):
        """Prompt files are created at the correct paths."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        assert (reflect_dir / "repo-a.prompt.md").exists()
        assert (reflect_dir / "repo-b.prompt.md").exists()

    def test_prompt_contains_repo_name(self, deep_session, bakeoff_features_reflect):
        """Each prompt mentions its target repo."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "repo-a" in prompt

    def test_prompt_contains_all_task_headings(
        self, deep_session, bakeoff_features_reflect
    ):
        """Each prompt contains all 3 developer assessment task headings."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "### Task A: Refactoring Assessment" in prompt
        assert "### Task B: New Feature Implementation" in prompt
        assert "### Task C: Codebase Understanding" in prompt

    def test_prompt_contains_scoring_rubric(
        self, deep_session, bakeoff_features_reflect
    ):
        """Prompt includes the numeric scoring rubric (0-100)."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "task_scores:" in prompt
        assert "refactoring_score:" in prompt
        assert "new_feature_score:" in prompt
        assert "understanding_score:" in prompt
        assert "0-100" in prompt

    def test_prompt_yaml_template_includes_trajectory(
        self, deep_session, bakeoff_features_reflect
    ):
        """Prompt YAML template includes the trajectory field."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "trajectory:" in prompt
        assert "IMPROVING" in prompt

    def test_prompt_contains_artifact_listing(
        self, deep_session, bakeoff_features_reflect
    ):
        """Prompt lists the available artifacts."""
        session_dir, state = deep_session
        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "hg.json" in prompt
        assert "symbols.txt" in prompt

    def test_skips_repos_without_output(
        self, deep_session, bakeoff_features_reflect, capsys
    ):
        """Repos without output directories are skipped gracefully."""
        session_dir, state = deep_session
        state["current_cohort"].append("repo-c")
        (session_dir / "state.json").write_text(json.dumps(state))

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        captured = capsys.readouterr()
        assert "[repo-c] Skipped" in captured.out


class TestPriorContextInjection:
    """Test that prior assessment findings are injected into new prompts."""

    def test_prior_context_injected_when_available(
        self, deep_session, bakeoff_features_reflect
    ):
        """When a prior assessment exists for the same repo, it's included."""
        session_dir, state = deep_session

        # Create a prior cohort's assessment for repo-a
        prior_reflect = session_dir / "reflect" / "cohort-001" / "iter-001"
        prior_reflect.mkdir(parents=True)

        prior_assessment = _make_deep_assessment(
            "repo-a",
            verdict="NOT_USEFUL",
            scores={
                "refactoring_score": 30,
                "new_feature_score": 20,
                "understanding_score": 40,
            },
        )
        (prior_reflect / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": prior_assessment})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "Prior Assessment" in prompt
        assert "NOT_USEFUL" in prompt

    def test_no_prior_context_when_first_cohort(
        self, deep_session, bakeoff_features_reflect
    ):
        """First cohort has no prior context — prompt should not mention it."""
        session_dir, state = deep_session
        # Set cohort to 1 (first)
        state["cohort_number"] = 1
        (session_dir / "state.json").write_text(json.dumps(state))

        # Recreate output dirs for cohort-001
        for repo in ["repo-a", "repo-b"]:
            repo_out = session_dir / "out" / "cohort-001" / "iter-001" / repo
            repo_out.mkdir(parents=True, exist_ok=True)
            (repo_out / "hg.json").write_text(json.dumps({"nodes": [], "edges": []}))
            (repo_out / "symbols.txt").write_text("func  10  5\n")

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-001" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "Prior Assessment" not in prompt

    def test_prior_context_not_injected_for_new_repo(
        self, deep_session, bakeoff_features_reflect
    ):
        """Repos not in the prior cohort don't get prior context."""
        session_dir, state = deep_session

        # Create prior cohort with different repos
        prior_reflect = session_dir / "reflect" / "cohort-001" / "iter-001"
        prior_reflect.mkdir(parents=True)

        prior_assessment = _make_deep_assessment("repo-x", verdict="USEFUL")
        (prior_reflect / "repo-x.assessment.yaml").write_text(
            json.dumps({"developer_assessment": prior_assessment})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        bakeoff_features_reflect.cmd_reflect(args)

        prompt = (
            session_dir / "reflect" / "cohort-002" / "iter-001" / "repo-a.prompt.md"
        ).read_text()
        assert "Prior Assessment" not in prompt


class TestAggregation:
    """Test aggregation of DEEP assessment YAML files."""

    def test_verdict_counts(self, bakeoff_features_reflect):
        """Aggregation correctly counts verdicts."""
        assessments = [
            _make_deep_assessment("repo-a", "USEFUL"),
            _make_deep_assessment("repo-b", "PARTIALLY_USEFUL"),
            _make_deep_assessment("repo-c", "NOT_USEFUL"),
        ]
        summary = bakeoff_features_reflect.aggregate_assessments(assessments)
        assert summary["verdicts"]["USEFUL"] == 1
        assert summary["verdicts"]["PARTIALLY_USEFUL"] == 1
        assert summary["verdicts"]["NOT_USEFUL"] == 1

    def test_deduplicates_ideas(self, bakeoff_features_reflect):
        """Duplicate improvement ideas are counted, not duplicated."""
        a1 = _make_deep_assessment("repo-a")
        a2 = _make_deep_assessment("repo-b")
        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        ideas = {item["idea"]: item["count"] for item in summary["improvement_ideas"]}
        assert ideas["Add middleware chain tracing"] == 2

    def test_collects_missing_items(self, bakeoff_features_reflect):
        """Missing items from all 3 tasks are collected."""
        a1 = _make_deep_assessment("repo-a")
        summary = bakeoff_features_reflect.aggregate_assessments([a1])
        missing = [item["issue"] for item in summary["common_missing"]]
        assert "callback chain not shown" in missing
        assert "middleware pattern not shown" in missing
        assert "module boundaries not visible" in missing

    def test_collects_noise_items(self, bakeoff_features_reflect):
        """Noise items from all tasks are collected."""
        a1 = _make_deep_assessment("repo-a")
        summary = bakeoff_features_reflect.aggregate_assessments([a1])
        noise = [item["issue"] for item in summary["common_noise"]]
        assert "test utilities included" in noise
        assert "utility functions dominate centrality" in noise

    def test_handles_empty_list(self, bakeoff_features_reflect):
        """Aggregation with empty assessment list works."""
        summary = bakeoff_features_reflect.aggregate_assessments([])
        assert summary["total_repos"] == 0
        assert summary["verdicts"]["USEFUL"] == 0

    def test_handles_none_entries(self, bakeoff_features_reflect):
        """None entries in the assessment list are skipped."""
        assessments = [None, _make_deep_assessment("repo-a"), None]
        summary = bakeoff_features_reflect.aggregate_assessments(assessments)
        assert summary["total_repos"] == 3
        assert summary["verdicts"]["PARTIALLY_USEFUL"] == 1


class TestScoreAggregation:
    """Test numeric score aggregation and statistics."""

    def test_score_statistics_computed(self, bakeoff_features_reflect):
        """When assessments have task_scores, statistics are computed."""
        a1 = _make_deep_assessment(
            "repo-a",
            scores={
                "refactoring_score": 60,
                "new_feature_score": 50,
                "understanding_score": 70,
            },
        )
        a2 = _make_deep_assessment(
            "repo-b",
            scores={
                "refactoring_score": 40,
                "new_feature_score": 30,
                "understanding_score": 50,
            },
        )

        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        assert "score_statistics" in summary

        stats = summary["score_statistics"]
        # Repo-a overall = mean(60,50,70) = 60.0
        # Repo-b overall = mean(40,30,50) = 40.0
        assert stats["mean_score"] == pytest.approx(50.0)
        assert stats["min_score"] == pytest.approx(40.0)
        assert stats["max_score"] == pytest.approx(60.0)
        assert "per_task" in stats

    def test_per_task_statistics(self, bakeoff_features_reflect):
        """Per-task statistics are computed across repos."""
        a1 = _make_deep_assessment(
            "repo-a",
            scores={
                "refactoring_score": 80,
                "new_feature_score": 60,
                "understanding_score": 70,
            },
        )
        a2 = _make_deep_assessment(
            "repo-b",
            scores={
                "refactoring_score": 60,
                "new_feature_score": 40,
                "understanding_score": 50,
            },
        )

        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        per_task = summary["score_statistics"]["per_task"]

        assert per_task["refactoring"]["mean"] == pytest.approx(70.0)
        assert per_task["new_feature"]["mean"] == pytest.approx(50.0)
        assert per_task["understanding"]["mean"] == pytest.approx(60.0)

    def test_no_scores_graceful(self, bakeoff_features_reflect):
        """When no assessments have scores, score_statistics is absent."""
        a1 = _make_deep_assessment("repo-a")
        a2 = _make_deep_assessment("repo-b")
        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        assert "score_statistics" not in summary

    def test_mixed_scores_partial(self, bakeoff_features_reflect):
        """When some assessments have scores and others don't, only scored ones count."""
        a1 = _make_deep_assessment(
            "repo-a",
            scores={
                "refactoring_score": 60,
                "new_feature_score": 50,
                "understanding_score": 70,
            },
        )
        a2 = _make_deep_assessment("repo-b")  # No scores

        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        assert "score_statistics" in summary
        stats = summary["score_statistics"]
        assert stats["scored_repos"] == 1
        assert stats["mean_score"] == pytest.approx(60.0)

    def test_individual_repo_scores_listed(self, bakeoff_features_reflect):
        """Individual repo scores are listed in the summary for transparency."""
        a1 = _make_deep_assessment(
            "repo-a",
            scores={
                "refactoring_score": 60,
                "new_feature_score": 50,
                "understanding_score": 70,
            },
        )
        a2 = _make_deep_assessment(
            "repo-b",
            scores={
                "refactoring_score": 40,
                "new_feature_score": 30,
                "understanding_score": 50,
            },
        )

        summary = bakeoff_features_reflect.aggregate_assessments([a1, a2])
        repos = summary["score_statistics"]["repos"]
        assert len(repos) == 2
        assert repos[0]["repo"] == "repo-a"
        assert repos[0]["overall_score"] == pytest.approx(60.0)


class TestTrajectoryComputation:
    """Test trajectory analysis comparing current vs prior cohort scores."""

    def test_trajectory_computed_with_prior(self, bakeoff_features_reflect):
        """compute_trajectory returns IMPROVING/STABLE/REGRESSING."""
        current_scores = {"mean_score": 65.0, "per_task": {
            "refactoring": {"mean": 70.0},
            "new_feature": {"mean": 55.0},
            "understanding": {"mean": 70.0},
        }}
        prior_scores = {"mean_score": 50.0, "per_task": {
            "refactoring": {"mean": 45.0},
            "new_feature": {"mean": 50.0},
            "understanding": {"mean": 55.0},
        }}

        trajectory = bakeoff_features_reflect.compute_trajectory(
            current_scores, prior_scores
        )
        assert trajectory["trend"] == "IMPROVING"
        assert trajectory["delta"] == pytest.approx(15.0)
        assert "prior_mean_score" in trajectory

    def test_trajectory_regressing(self, bakeoff_features_reflect):
        """Trajectory is REGRESSING when scores drop significantly."""
        current = {"mean_score": 40.0, "per_task": {}}
        prior = {"mean_score": 60.0, "per_task": {}}

        trajectory = bakeoff_features_reflect.compute_trajectory(current, prior)
        assert trajectory["trend"] == "REGRESSING"
        assert trajectory["delta"] == pytest.approx(-20.0)

    def test_trajectory_stable(self, bakeoff_features_reflect):
        """Trajectory is STABLE when scores are within threshold."""
        current = {"mean_score": 52.0, "per_task": {}}
        prior = {"mean_score": 50.0, "per_task": {}}

        trajectory = bakeoff_features_reflect.compute_trajectory(current, prior)
        assert trajectory["trend"] == "STABLE"

    def test_trajectory_none_without_prior(self, bakeoff_features_reflect):
        """compute_trajectory returns None when no prior scores exist."""
        current = {"mean_score": 60.0, "per_task": {}}
        trajectory = bakeoff_features_reflect.compute_trajectory(current, None)
        assert trajectory is None


class TestFindPriorAssessment:
    """Test finding prior assessments for a repo across cohort directories."""

    def test_finds_prior_in_previous_cohort(
        self, deep_session, bakeoff_features_reflect
    ):
        """Finds assessment from the most recent prior cohort."""
        session_dir, state = deep_session

        prior_reflect = session_dir / "reflect" / "cohort-001" / "iter-001"
        prior_reflect.mkdir(parents=True)

        prior = {"developer_assessment": _make_deep_assessment("repo-a")}
        (prior_reflect / "repo-a.assessment.yaml").write_text(json.dumps(prior))

        result = bakeoff_features_reflect.find_prior_assessment(
            str(session_dir), "repo-a", cohort_number=2
        )
        assert result is not None
        assert result["repo"] == "repo-a"

    def test_returns_none_for_first_cohort(
        self, deep_session, bakeoff_features_reflect
    ):
        """No prior for cohort 1."""
        session_dir, state = deep_session
        result = bakeoff_features_reflect.find_prior_assessment(
            str(session_dir), "repo-a", cohort_number=1
        )
        assert result is None

    def test_returns_none_when_no_prior_exists(
        self, deep_session, bakeoff_features_reflect
    ):
        """Returns None when prior cohort has no assessment for this repo."""
        session_dir, state = deep_session
        result = bakeoff_features_reflect.find_prior_assessment(
            str(session_dir), "repo-a", cohort_number=2
        )
        assert result is None


class TestFindUnaggregated:
    """Test detection of cohort/iter dirs with assessments but no summary."""

    def test_finds_unaggregated_cohort(
        self, deep_session, bakeoff_features_reflect
    ):
        """Detects a cohort with assessments but no summary.yaml."""
        session_dir, state = deep_session

        # Create assessments without summary.yaml
        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)
        a = _make_deep_assessment("repo-a")
        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a})
        )

        result = bakeoff_features_reflect.find_unaggregated(str(session_dir))
        assert len(result) == 1
        assert result[0]["cohort"] == 1
        assert result[0]["iteration"] == 1
        assert result[0]["assessment_count"] == 1

    def test_ignores_aggregated_cohort(
        self, deep_session, bakeoff_features_reflect
    ):
        """Cohorts with summary.yaml are not returned."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)
        a = _make_deep_assessment("repo-a")
        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a})
        )
        (reflect_dir / "summary.yaml").write_text("total_repos: 1\n")

        result = bakeoff_features_reflect.find_unaggregated(str(session_dir))
        assert len(result) == 0

    def test_returns_empty_when_no_reflect_dir(
        self, deep_session, bakeoff_features_reflect
    ):
        """Returns empty list when no reflect/ directory exists."""
        session_dir, state = deep_session
        result = bakeoff_features_reflect.find_unaggregated(str(session_dir))
        assert result == []

    def test_finds_multiple_unaggregated(
        self, deep_session, bakeoff_features_reflect
    ):
        """Finds multiple unaggregated cohort/iter combos."""
        session_dir, state = deep_session

        for cohort in [1, 2]:
            reflect_dir = (
                session_dir / "reflect" / f"cohort-{cohort:03d}" / "iter-001"
            )
            reflect_dir.mkdir(parents=True)
            a = _make_deep_assessment(f"repo-{cohort}")
            (reflect_dir / f"repo-{cohort}.assessment.yaml").write_text(
                json.dumps({"developer_assessment": a})
            )

        result = bakeoff_features_reflect.find_unaggregated(str(session_dir))
        assert len(result) == 2


class TestCmdCheck:
    """Test the check-unaggregated command."""

    def test_check_finds_unaggregated(
        self, deep_session, bakeoff_features_reflect, capsys
    ):
        """check command reports unaggregated cohorts."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)
        a = _make_deep_assessment("repo-a")
        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.auto_aggregate = False

        result = bakeoff_features_reflect.cmd_check(args)
        assert result == 1  # Exit code 1 = unaggregated found

        captured = capsys.readouterr()
        assert "cohort-001/iter-001" in captured.out
        assert "1 assessment" in captured.out

    def test_check_clean_returns_0(
        self, deep_session, bakeoff_features_reflect, capsys
    ):
        """check command returns 0 when everything is aggregated."""
        session_dir, state = deep_session

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.auto_aggregate = False

        result = bakeoff_features_reflect.cmd_check(args)
        assert result == 0

    def test_check_auto_aggregate(
        self, deep_session, bakeoff_features_reflect
    ):
        """check --auto-aggregate triggers aggregation."""
        session_dir, state = deep_session

        # Create unaggregated cohort
        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)
        a = _make_deep_assessment("repo-a")
        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a})
        )

        # Need a state.json that matches cohort 1
        state["cohort_number"] = 1
        (session_dir / "state.json").write_text(json.dumps(state))

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.auto_aggregate = True

        result = bakeoff_features_reflect.cmd_check(args)
        assert result == 0

        # Summary should now exist
        assert (reflect_dir / "summary.yaml").exists()


class TestCmdAggregate:
    """Test the full aggregate command."""

    def test_aggregate_writes_summary_yaml(
        self, deep_session, bakeoff_features_reflect
    ):
        """Full aggregate command writes summary.yaml with scores."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = _make_deep_assessment(
            "repo-a",
            verdict="USEFUL",
            scores={
                "refactoring_score": 80,
                "new_feature_score": 70,
                "understanding_score": 75,
            },
        )
        a2 = _make_deep_assessment(
            "repo-b",
            verdict="PARTIALLY_USEFUL",
            scores={
                "refactoring_score": 50,
                "new_feature_score": 40,
                "understanding_score": 55,
            },
        )

        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a1})
        )
        (reflect_dir / "repo-b.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a2})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_features_reflect.cmd_aggregate(args)
        assert result == 0

        summary_path = reflect_dir / "summary.yaml"
        assert summary_path.exists()

    def test_aggregate_no_assessments_returns_1(
        self, deep_session, bakeoff_features_reflect
    ):
        """Aggregate with no assessments returns exit code 1."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_features_reflect.cmd_aggregate(args)
        assert result == 1

    def test_aggregate_backward_compat_no_scores(
        self, deep_session, bakeoff_features_reflect
    ):
        """Old assessments without task_scores still aggregate correctly."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = _make_deep_assessment("repo-a", "PARTIALLY_USEFUL")
        a2 = _make_deep_assessment("repo-b", "USEFUL")

        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a1})
        )
        (reflect_dir / "repo-b.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a2})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_features_reflect.cmd_aggregate(args)
        assert result == 0

    def test_aggregate_with_trajectory(
        self, deep_session, bakeoff_features_reflect
    ):
        """Aggregate includes trajectory when prior summary exists."""
        session_dir, state = deep_session

        # Create prior cohort summary with scores
        prior_reflect = session_dir / "reflect" / "cohort-001" / "iter-001"
        prior_reflect.mkdir(parents=True)

        import yaml

        prior_summary = {
            "total_repos": 2,
            "verdicts": {"USEFUL": 0, "PARTIALLY_USEFUL": 2, "NOT_USEFUL": 0},
            "score_statistics": {
                "mean_score": 45.0,
                "per_task": {
                    "refactoring": {"mean": 40.0},
                    "new_feature": {"mean": 45.0},
                    "understanding": {"mean": 50.0},
                },
            },
            "cohort": 1,
            "iteration": 1,
        }
        (prior_reflect / "summary.yaml").write_text(
            yaml.dump(prior_summary, default_flow_style=False)
        )

        # Create current cohort assessments with higher scores
        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = _make_deep_assessment(
            "repo-a",
            scores={
                "refactoring_score": 70,
                "new_feature_score": 60,
                "understanding_score": 65,
            },
        )
        a2 = _make_deep_assessment(
            "repo-b",
            scores={
                "refactoring_score": 60,
                "new_feature_score": 55,
                "understanding_score": 60,
            },
        )

        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a1})
        )
        (reflect_dir / "repo-b.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a2})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None

        result = bakeoff_features_reflect.cmd_aggregate(args)
        assert result == 0

        summary_path = reflect_dir / "summary.yaml"
        summary = yaml.safe_load(summary_path.read_text())
        assert "trajectory" in summary
        assert summary["trajectory"]["trend"] == "IMPROVING"


class TestNormalizeIdea:
    """Tests for improvement idea normalization."""

    def test_lowercases(self, bakeoff_features_reflect):
        assert bakeoff_features_reflect._normalize_idea("ADD Middleware") == "add middleware"

    def test_strips_whitespace(self, bakeoff_features_reflect):
        assert bakeoff_features_reflect._normalize_idea("  idea  ") == "idea"

    def test_truncates_at_80_chars(self, bakeoff_features_reflect):
        long_idea = "A" * 100
        result = bakeoff_features_reflect._normalize_idea(long_idea)
        assert len(result) == 80


class TestListStealthTitles:
    """Tests for _list_stealth_titles dedup helper."""

    def test_returns_set_of_lowercase_titles(self, bakeoff_features_reflect):
        items = [
            {"id": "WI-abc", "title": "Add Middleware Tracing"},
            {"id": "WI-def", "title": "Improve Slice Quality"},
        ]
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=json.dumps(items),
            )
            titles = bakeoff_features_reflect._list_stealth_titles("/fake/tracker")

        assert titles == {"add middleware tracing", "improve slice quality"}

    def test_returns_empty_on_failure(self, bakeoff_features_reflect):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="")
            titles = bakeoff_features_reflect._list_stealth_titles("/fake/tracker")

        assert titles == set()

    def test_returns_empty_on_bad_json(self, bakeoff_features_reflect):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="not-json")
            titles = bakeoff_features_reflect._list_stealth_titles("/fake/tracker")

        assert titles == set()


class TestIngestImprovementIdeas:
    """Tests for improvement idea ingestion into tracker."""

    def test_creates_items_above_threshold(self, bakeoff_features_reflect):
        """Creates tracker items for ideas with count >= min_count."""
        summary = {
            "improvement_ideas": [
                {"idea": "Add middleware chain tracing", "count": 3},
                {"idea": "Improve reverse slice", "count": 1},
            ]
        }

        with mock.patch("subprocess.run") as mock_run:
            # First call: list stealth items (empty)
            # Subsequent calls: add items
            mock_run.side_effect = [
                mock.Mock(returncode=0, stdout="[]"),  # list
                mock.Mock(returncode=0, stdout="WI-new-item\n"),  # add
            ]

            created = bakeoff_features_reflect.ingest_improvement_ideas(
                summary, min_count=2, tracker_script="/fake/tracker"
            )

        assert len(created) == 1
        assert created[0]["idea"] == "Add middleware chain tracing"
        assert created[0]["id"] == "WI-new-item"

        # Verify the add call has correct args
        add_call = mock_run.call_args_list[1]
        add_args = add_call[0][0]
        assert "--tier" in add_args
        tier_idx = add_args.index("--tier")
        assert add_args[tier_idx + 1] == "stealth"
        assert "--tag" in add_args

    def test_skips_existing_items(self, bakeoff_features_reflect):
        """Does not create items that already exist in stealth."""
        summary = {
            "improvement_ideas": [
                {"idea": "Add middleware chain tracing", "count": 3},
            ]
        }

        existing = [{"id": "WI-old", "title": "Add middleware chain tracing"}]

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout=json.dumps(existing)
            )

            created = bakeoff_features_reflect.ingest_improvement_ideas(
                summary, min_count=1, tracker_script="/fake/tracker"
            )

        assert len(created) == 0
        # Only one call (list), no add call
        assert mock_run.call_count == 1

    def test_empty_ideas_returns_empty(self, bakeoff_features_reflect):
        """Returns empty list when no improvement ideas."""
        created = bakeoff_features_reflect.ingest_improvement_ideas(
            {}, tracker_script="/fake/tracker"
        )
        assert created == []

    def test_all_below_threshold(self, bakeoff_features_reflect):
        """Returns empty when all ideas are below threshold."""
        summary = {
            "improvement_ideas": [
                {"idea": "Low-frequency idea", "count": 1},
            ]
        }
        created = bakeoff_features_reflect.ingest_improvement_ideas(
            summary, min_count=2, tracker_script="/fake/tracker"
        )
        assert created == []

    def test_dry_run_does_not_call_tracker(self, bakeoff_features_reflect):
        """Dry run reports ideas without calling tracker add."""
        summary = {
            "improvement_ideas": [
                {"idea": "Add middleware chain tracing", "count": 3},
            ]
        }

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="[]")

            created = bakeoff_features_reflect.ingest_improvement_ideas(
                summary, min_count=1, tracker_script="/fake/tracker",
                dry_run=True,
            )

        assert len(created) == 1
        assert created[0]["id"] == "(dry-run)"
        # Only the list call, no add calls
        assert mock_run.call_count == 1

    def test_handles_tracker_add_failure(self, bakeoff_features_reflect):
        """Continues gracefully when tracker add fails."""
        summary = {
            "improvement_ideas": [
                {"idea": "Idea that fails", "count": 2},
                {"idea": "Idea that succeeds", "count": 2},
            ]
        }

        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                mock.Mock(returncode=0, stdout="[]"),  # list
                mock.Mock(returncode=1, stdout="", stderr="error"),  # add fails
                mock.Mock(returncode=0, stdout="WI-ok\n"),  # add succeeds
            ]

            created = bakeoff_features_reflect.ingest_improvement_ideas(
                summary, min_count=1, tracker_script="/fake/tracker"
            )

        assert len(created) == 1
        assert created[0]["id"] == "WI-ok"

    def test_title_truncation(self, bakeoff_features_reflect):
        """Long ideas get truncated to 80 chars in title."""
        long_idea = "A" * 120
        summary = {
            "improvement_ideas": [
                {"idea": long_idea, "count": 2},
            ]
        }

        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                mock.Mock(returncode=0, stdout="[]"),  # list
                mock.Mock(returncode=0, stdout="WI-new\n"),  # add
            ]

            created = bakeoff_features_reflect.ingest_improvement_ideas(
                summary, min_count=1, tracker_script="/fake/tracker"
            )

        assert len(created) == 1
        # Verify title was truncated in the add call
        add_call = mock_run.call_args_list[1]
        add_args = add_call[0][0]
        title_idx = add_args.index("--title")
        assert len(add_args[title_idx + 1]) == 80


class TestCmdIngest:
    """Tests for the ingest subcommand."""

    def test_ingest_reads_summary_and_creates_items(
        self, tmp_path, bakeoff_features_reflect
    ):
        """cmd_ingest reads summary.yaml and calls ingest_improvement_ideas."""
        import yaml

        # Create session structure
        session_dir = tmp_path / "deep-20260218-150000"
        session_dir.mkdir()
        state = {
            "session_id": "test",
            "workdir": str(session_dir),
            "current_cohort": ["repo-a"],
            "cohort_number": 1,
            "iteration": 1,
        }
        (session_dir / "state.json").write_text(json.dumps(state))

        reflect_dir = session_dir / "reflect" / "cohort-001" / "iter-001"
        reflect_dir.mkdir(parents=True)

        summary = {
            "total_repos": 2,
            "improvement_ideas": [
                {"idea": "Add middleware tracing", "count": 3},
            ],
        }
        (reflect_dir / "summary.yaml").write_text(
            yaml.dump(summary, default_flow_style=False)
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None
        args.min_count = 1
        args.dry_run = True

        result = bakeoff_features_reflect.cmd_ingest(args)
        assert result == 0

    def test_ingest_fails_without_summary(self, tmp_path, bakeoff_features_reflect):
        """cmd_ingest fails gracefully when no summary.yaml exists."""
        session_dir = tmp_path / "deep-20260218-150000"
        session_dir.mkdir()
        state = {
            "session_id": "test",
            "workdir": str(session_dir),
            "current_cohort": ["repo-a"],
            "cohort_number": 1,
            "iteration": 1,
        }
        (session_dir / "state.json").write_text(json.dumps(state))

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None
        args.min_count = 2
        args.dry_run = False

        result = bakeoff_features_reflect.cmd_ingest(args)
        assert result == 1


class TestAggregateWithIngest:
    """Test the --ingest flag on aggregate command."""

    def test_aggregate_ingest_calls_ingestion(
        self, deep_session, bakeoff_features_reflect
    ):
        """cmd_aggregate with --ingest creates stealth tracker items."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = _make_deep_assessment("repo-a", "USEFUL")
        a2 = _make_deep_assessment("repo-b", "PARTIALLY_USEFUL")
        # Both have "Add middleware chain tracing" as improvement_idea

        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a1})
        )
        (reflect_dir / "repo-b.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a2})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None
        args.ingest = True

        with mock.patch.object(
            bakeoff_features_reflect,
            "ingest_improvement_ideas",
            return_value=[{"idea": "test", "id": "WI-test"}],
        ) as mock_ingest:
            result = bakeoff_features_reflect.cmd_aggregate(args)

        assert result == 0
        mock_ingest.assert_called_once()
        call_args = mock_ingest.call_args
        summary = call_args[0][0]
        assert "improvement_ideas" in summary

    def test_aggregate_no_ingest_by_default(
        self, deep_session, bakeoff_features_reflect
    ):
        """cmd_aggregate without --ingest does not call ingestion."""
        session_dir, state = deep_session

        reflect_dir = session_dir / "reflect" / "cohort-002" / "iter-001"
        reflect_dir.mkdir(parents=True)

        a1 = _make_deep_assessment("repo-a", "USEFUL")
        (reflect_dir / "repo-a.assessment.yaml").write_text(
            json.dumps({"developer_assessment": a1})
        )

        args = mock.Mock()
        args.workdir = str(session_dir)
        args.iteration = None
        args.ingest = False

        with mock.patch.object(
            bakeoff_features_reflect,
            "ingest_improvement_ideas",
        ) as mock_ingest:
            result = bakeoff_features_reflect.cmd_aggregate(args)

        assert result == 0
        mock_ingest.assert_not_called()
