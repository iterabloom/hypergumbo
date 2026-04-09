# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/backfill-training-data-cohort-tags.py.

Covers:
  - get_commit_timeline: timeline construction and timezone stripping
  - resolve_sha_at_timestamp: binary search, boundary conditions
  - count_playbooks_at_sha: regex counting via git show
  - extract_prompt_playbook_count: prompt regex extraction
  - backfill (end-to-end): sidecar generation and idempotence
  - Real v0-window SHA boundary validation (test #4 from WI-gigil)
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Import the script as a module
# ---------------------------------------------------------------------------

def _import_script(name: str, filename: str):
    """Import a Python file from scripts/ as a module."""
    script_path = str(
        Path(__file__).parent.parent / "scripts" / filename
    )
    loader = importlib.machinery.SourceFileLoader(name, script_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bf_mod():
    """Import backfill script as a module."""
    return _import_script(
        "backfill_cohort_tags",
        "backfill-training-data-cohort-tags.py",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_corpus(path: Path, entries: list[dict]) -> Path:
    """Write a JSONL corpus file and return its path."""
    corpus = path / "corpus.jsonl"
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    corpus.write_text("\n".join(lines) + "\n")
    return corpus


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a file matching INFRA_PATH."""
    repo = tmp_path / "repo"
    repo.mkdir()
    infra_dir = repo / ".agent" / "hooks" / "_shared"
    infra_dir.mkdir(parents=True)

    subprocess.run(
        ["git", "init"], cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True, check=True,
    )
    return repo


def _commit_infra(repo: Path, content: str, message: str, date: str) -> str:
    """Write content to INFRA_PATH, commit with a specific date, return SHA."""
    infra_file = repo / ".agent" / "hooks" / "_shared" / "on_transcript_change.py"
    infra_file.write_text(content)
    subprocess.run(
        ["git", "add", "."], cwd=str(repo), capture_output=True, check=True,
    )
    env = {
        **subprocess.os.environ,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo), capture_output=True, check=True, env=env,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# Playbook-style file content for count testing
PLAYBOOKS_14 = 'PLAYBOOKS = [\n' + ''.join(
    f'    ("playbook-{i:02d}",\n     "path",\n     "summary"),\n'
    for i in range(14)
) + ']\n'

PLAYBOOKS_17 = 'PLAYBOOKS = [\n' + ''.join(
    f'    ("playbook-{i:02d}",\n     "path",\n     "summary"),\n'
    for i in range(17)
) + ']\n'

PLAYBOOKS_19 = 'PLAYBOOKS = [\n' + ''.join(
    f'    ("playbook-{i:02d}",\n     "path",\n     "summary"),\n'
    for i in range(19)
) + ']\n'


# ---------------------------------------------------------------------------
# get_commit_timeline
# ---------------------------------------------------------------------------


class TestGetCommitTimeline:
    """get_commit_timeline returns (naive_timestamp, sha) pairs."""

    def test_returns_timeline_for_tracked_file(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        repo = _make_git_repo(tmp_path)
        sha1 = _commit_infra(
            repo, "v1", "first", "2026-04-03T01:00:00-04:00",
        )
        sha2 = _commit_infra(
            repo, "v2", "second", "2026-04-04T02:00:00-04:00",
        )

        timeline = bf_mod.get_commit_timeline(
            str(repo),
            ".agent/hooks/_shared/on_transcript_change.py",
        )
        assert len(timeline) == 2
        # Timestamps stripped of timezone
        assert timeline[0] == ("2026-04-03T01:00:00", sha1)
        assert timeline[1] == ("2026-04-04T02:00:00", sha2)

    def test_returns_empty_for_untracked_file(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        repo = _make_git_repo(tmp_path)
        timeline = bf_mod.get_commit_timeline(str(repo), "nonexistent.py")
        assert timeline == []

    def test_returns_empty_for_non_git_dir(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        timeline = bf_mod.get_commit_timeline(str(tmp_path), "anything.py")
        assert timeline == []


# ---------------------------------------------------------------------------
# resolve_sha_at_timestamp
# ---------------------------------------------------------------------------


class TestResolveShaAtTimestamp:
    """resolve_sha_at_timestamp uses binary search on the timeline."""

    TIMELINE = [
        ("2026-04-03T01:00:00", "aaa"),
        ("2026-04-04T16:00:00", "bbb"),
        ("2026-04-05T16:00:00", "ccc"),
    ]
    TS_ONLY = [t for t, _ in TIMELINE]

    def test_before_first_commit(self, bf_mod) -> None:
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-02T23:59:59",
        )
        assert sha == ""

    def test_exactly_at_first_commit(self, bf_mod) -> None:
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-03T01:00:00",
        )
        assert sha == "aaa"

    def test_between_commits(self, bf_mod) -> None:
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-04T18:00:00",
        )
        assert sha == "bbb"

    def test_after_last_commit(self, bf_mod) -> None:
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-10T00:00:00",
        )
        assert sha == "ccc"

    def test_handles_fractional_seconds(self, bf_mod) -> None:
        """Entry timestamps include microseconds; comparison uses first 19 chars."""
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-04T15:59:59.999999",
        )
        assert sha == "aaa"

    def test_exactly_at_boundary(self, bf_mod) -> None:
        """Entry at exact commit second resolves to that commit."""
        sha = bf_mod.resolve_sha_at_timestamp(
            self.TIMELINE, self.TS_ONLY, "2026-04-05T16:00:00.123456",
        )
        assert sha == "ccc"


# ---------------------------------------------------------------------------
# count_playbooks_at_sha
# ---------------------------------------------------------------------------


class TestCountPlaybooksAtSha:
    """count_playbooks_at_sha counts PLAYBOOKS tuples via git show."""

    def test_counts_14_playbooks(self, bf_mod, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        sha = _commit_infra(repo, PLAYBOOKS_14, "14 pbs", "2026-04-03T01:00:00")
        assert bf_mod.count_playbooks_at_sha(str(repo), sha) == 14

    def test_counts_19_playbooks(self, bf_mod, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        sha = _commit_infra(repo, PLAYBOOKS_19, "19 pbs", "2026-04-05T20:00:00")
        assert bf_mod.count_playbooks_at_sha(str(repo), sha) == 19

    def test_returns_negative_one_for_bad_sha(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        repo = _make_git_repo(tmp_path)
        _commit_infra(repo, "x", "init", "2026-04-03T01:00:00")
        assert bf_mod.count_playbooks_at_sha(str(repo), "0" * 40) == -1

    def test_handles_oserror(self, bf_mod) -> None:
        with mock.patch("subprocess.run", side_effect=OSError("no git")):
            assert bf_mod.count_playbooks_at_sha("/fake", "abc") == -1


# ---------------------------------------------------------------------------
# extract_prompt_playbook_count
# ---------------------------------------------------------------------------


class TestExtractPromptPlaybookCount:
    """extract_prompt_playbook_count parses the count from prompt text."""

    def test_extracts_count(self, bf_mod) -> None:
        entry = {
            "step": "sparse_selection",
            "messages": [{"role": "user", "content": "Below are 14 guidance documents"}],
        }
        assert bf_mod.extract_prompt_playbook_count(entry) == 14

    def test_extracts_19(self, bf_mod) -> None:
        entry = {
            "step": "sparse_selection",
            "messages": [{"role": "user", "content": "Below are 19 guidance documents. Select"}],
        }
        assert bf_mod.extract_prompt_playbook_count(entry) == 19

    def test_returns_negative_one_for_goal_distillation(self, bf_mod) -> None:
        entry = {
            "step": "goal_distillation",
            "messages": [{"role": "user", "content": "Below are 14 guidance documents"}],
        }
        assert bf_mod.extract_prompt_playbook_count(entry) == -1

    def test_returns_negative_one_for_missing_messages(self, bf_mod) -> None:
        entry = {"step": "sparse_selection"}
        assert bf_mod.extract_prompt_playbook_count(entry) == -1

    def test_returns_negative_one_for_no_match(self, bf_mod) -> None:
        entry = {
            "step": "sparse_selection",
            "messages": [{"role": "user", "content": "no count here"}],
        }
        assert bf_mod.extract_prompt_playbook_count(entry) == -1


# ---------------------------------------------------------------------------
# backfill (end-to-end)
# ---------------------------------------------------------------------------


class TestBackfillEndToEnd:
    """End-to-end tests for the backfill function."""

    def test_every_entry_gets_sidecar_row(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        repo = _make_git_repo(tmp_path)
        _commit_infra(
            repo, PLAYBOOKS_14, "init", "2026-04-03T01:00:00",
        )

        entries = [
            {"timestamp": "2026-04-03T02:00:00.123", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
            {"timestamp": "2026-04-03T03:00:00.456", "step": "sparse_selection",
             "model": "m", "messages": [
                 {"role": "user", "content": "Below are 14 guidance documents"},
                 {"role": "assistant", "content": "none"}],
             "event_id": "e1"},
        ]
        corpus = _make_corpus(tmp_path, entries)
        output = tmp_path / "sidecar.jsonl"

        count = bf_mod.backfill(
            str(corpus), str(output), str(repo), verbose=False,
        )
        assert count == 2

        rows = [json.loads(l) for l in output.read_text().strip().splitlines()]
        assert len(rows) == 2
        assert rows[0]["entry_index"] == 0
        assert rows[1]["entry_index"] == 1
        assert rows[0]["main_llm_presumed"] == "claude-opus-4-6"
        assert rows[1]["playbook_count_in_prompt"] == 14
        assert rows[0]["playbook_count_in_prompt"] == -1  # goal_distillation

    def test_idempotence(self, bf_mod, tmp_path: Path) -> None:
        """Running twice produces identical sidecar output."""
        repo = _make_git_repo(tmp_path)
        _commit_infra(
            repo, PLAYBOOKS_14, "init", "2026-04-03T01:00:00",
        )

        entries = [
            {"timestamp": "2026-04-03T05:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
        ]
        corpus = _make_corpus(tmp_path, entries)
        out1 = tmp_path / "sidecar1.jsonl"
        out2 = tmp_path / "sidecar2.jsonl"

        bf_mod.backfill(str(corpus), str(out1), str(repo), verbose=False)
        bf_mod.backfill(str(corpus), str(out2), str(repo), verbose=False)

        assert out1.read_text() == out2.read_text()

    def test_sha_boundary_resolution(self, bf_mod, tmp_path: Path) -> None:
        """Entries straddling a commit boundary resolve to the correct SHA."""
        repo = _make_git_repo(tmp_path)
        sha1 = _commit_infra(
            repo, PLAYBOOKS_14, "14 pbs", "2026-04-03T01:00:00",
        )
        sha2 = _commit_infra(
            repo, PLAYBOOKS_17, "17 pbs", "2026-04-04T18:00:00",
        )

        entries = [
            # Before sha2: should resolve to sha1
            {"timestamp": "2026-04-04T17:59:59.999999",
             "step": "goal_distillation", "model": "m",
             "messages": [{"role": "user", "content": "p"},
                          {"role": "assistant", "content": "r"}]},
            # After sha2: should resolve to sha2
            {"timestamp": "2026-04-04T18:00:01.000000",
             "step": "goal_distillation", "model": "m",
             "messages": [{"role": "user", "content": "p"},
                          {"role": "assistant", "content": "r"}]},
        ]
        corpus = _make_corpus(tmp_path, entries)
        output = tmp_path / "sidecar.jsonl"

        bf_mod.backfill(str(corpus), str(output), str(repo), verbose=False)

        rows = [json.loads(l) for l in output.read_text().strip().splitlines()]
        assert rows[0]["infra_sha"] == sha1
        assert rows[0]["playbook_count_actual"] == 14
        assert rows[1]["infra_sha"] == sha2
        assert rows[1]["playbook_count_actual"] == 17

    def test_multiple_boundary_transitions(
        self, bf_mod, tmp_path: Path,
    ) -> None:
        """Three-commit timeline with entries spanning all boundaries."""
        repo = _make_git_repo(tmp_path)
        sha1 = _commit_infra(
            repo, PLAYBOOKS_14, "14 pbs", "2026-04-03T01:00:00",
        )
        sha2 = _commit_infra(
            repo, PLAYBOOKS_17, "17 pbs", "2026-04-04T18:00:00",
        )
        sha3 = _commit_infra(
            repo, PLAYBOOKS_19, "19 pbs", "2026-04-05T20:00:00",
        )

        entries = [
            {"timestamp": "2026-04-03T12:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
            {"timestamp": "2026-04-05T00:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
            {"timestamp": "2026-04-06T00:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
        ]
        corpus = _make_corpus(tmp_path, entries)
        output = tmp_path / "sidecar.jsonl"

        bf_mod.backfill(str(corpus), str(output), str(repo), verbose=False)

        rows = [json.loads(l) for l in output.read_text().strip().splitlines()]
        assert rows[0]["infra_sha"] == sha1
        assert rows[0]["playbook_count_actual"] == 14
        assert rows[1]["infra_sha"] == sha2
        assert rows[1]["playbook_count_actual"] == 17
        assert rows[2]["infra_sha"] == sha3
        assert rows[2]["playbook_count_actual"] == 19

    def test_entry_before_any_commit(self, bf_mod, tmp_path: Path) -> None:
        """Entry timestamp before any commit gets empty SHA."""
        repo = _make_git_repo(tmp_path)
        _commit_infra(
            repo, PLAYBOOKS_14, "init", "2026-04-05T00:00:00",
        )

        entries = [
            {"timestamp": "2026-04-03T00:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
        ]
        corpus = _make_corpus(tmp_path, entries)
        output = tmp_path / "sidecar.jsonl"

        bf_mod.backfill(str(corpus), str(output), str(repo), verbose=False)

        rows = [json.loads(l) for l in output.read_text().strip().splitlines()]
        assert rows[0]["infra_sha"] == ""
        assert rows[0]["infra_sha_short"] == ""

    def test_custom_main_llm(self, bf_mod, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        _commit_infra(repo, "x", "init", "2026-04-03T01:00:00")

        entries = [
            {"timestamp": "2026-04-03T05:00:00", "step": "goal_distillation",
             "model": "m", "messages": [{"role": "user", "content": "p"},
                                         {"role": "assistant", "content": "r"}]},
        ]
        corpus = _make_corpus(tmp_path, entries)
        output = tmp_path / "sidecar.jsonl"

        bf_mod.backfill(
            str(corpus), str(output), str(repo),
            main_llm_presumed="claude-sonnet-4-5",
            verbose=False,
        )

        rows = [json.loads(l) for l in output.read_text().strip().splitlines()]
        assert rows[0]["main_llm_presumed"] == "claude-sonnet-4-5"

    def test_empty_corpus(self, bf_mod, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        _commit_infra(repo, "x", "init", "2026-04-03T01:00:00")

        corpus = tmp_path / "empty.jsonl"
        corpus.write_text("")
        output = tmp_path / "sidecar.jsonl"

        count = bf_mod.backfill(
            str(corpus), str(output), str(repo), verbose=False,
        )
        assert count == 0
        assert output.read_text() == ""


# ---------------------------------------------------------------------------
# Real v0-window SHA boundary validation (WI-gigil test #4)
# ---------------------------------------------------------------------------


class TestRealV0WindowBoundaries:
    """Validate SHA resolution against real commits from the v0 window.

    These tests use the actual hypergumbo repo's git history.  They
    verify that timestamps straddling the known commit boundaries
    (75596a415, 704548fe1, 7310783e1, 9ac06cc88) resolve correctly.
    """

    REPO_ROOT = str(Path(__file__).parent.parent)

    # Known commit SHAs and their author timestamps (from git log)
    # 75596a415 — 2026-04-03T01:56:55  (initial sparse-selection pipeline)
    # 704548fe1 — 2026-04-05T16:32:43  (dynamic playbook count)
    # 7310783e1 — 2026-04-08T12:57:57  (injection-history sidecar)
    # 9ac06cc88 — 2026-04-08T15:58:46  (per-session isolation)

    def test_timeline_includes_known_shas(self, bf_mod) -> None:
        """The timeline includes the four boundary commits."""
        timeline = bf_mod.get_commit_timeline(self.REPO_ROOT, bf_mod.INFRA_PATH)
        shas = {sha for _, sha in timeline}
        for short in ["75596a4153d4", "704548fe1e90", "7310783e1b26", "9ac06cc88a00"]:
            matches = [s for s in shas if s.startswith(short)]
            assert matches, f"SHA starting with {short} not found in timeline"

    def test_entry_before_75596a_resolves_to_earlier(self, bf_mod) -> None:
        """Entry before the initial sparse-selection commit."""
        timeline = bf_mod.get_commit_timeline(self.REPO_ROOT, bf_mod.INFRA_PATH)
        ts_only = [t for t, _ in timeline]
        sha = bf_mod.resolve_sha_at_timestamp(
            timeline, ts_only, "2026-04-03T01:56:54",
        )
        # Should resolve to a commit before 75596a415
        assert not sha.startswith("75596a4153d4")

    def test_entry_at_75596a_resolves_correctly(self, bf_mod) -> None:
        """Entry at the 75596a415 boundary resolves to that commit."""
        timeline = bf_mod.get_commit_timeline(self.REPO_ROOT, bf_mod.INFRA_PATH)
        ts_only = [t for t, _ in timeline]
        sha = bf_mod.resolve_sha_at_timestamp(
            timeline, ts_only, "2026-04-03T01:56:55",
        )
        assert sha.startswith("75596a4153d4")

    def test_entry_between_704548_and_7310783(self, bf_mod) -> None:
        """Entry between dynamic-count and injection-history commits."""
        timeline = bf_mod.get_commit_timeline(self.REPO_ROOT, bf_mod.INFRA_PATH)
        ts_only = [t for t, _ in timeline]
        # Between 704548fe1 (2026-04-05T16:32:43) and next commit
        sha = bf_mod.resolve_sha_at_timestamp(
            timeline, ts_only, "2026-04-06T00:00:00",
        )
        # Could be 704548fe1 or a commit after it but before 7310783e1
        # The key invariant: it must NOT be 7310783e1 (which is Apr 8)
        assert not sha.startswith("7310783e1b26")

    def test_entry_after_9ac06cc_resolves_to_it_or_later(self, bf_mod) -> None:
        """Entry after per-session isolation commit."""
        timeline = bf_mod.get_commit_timeline(self.REPO_ROOT, bf_mod.INFRA_PATH)
        ts_only = [t for t, _ in timeline]
        sha = bf_mod.resolve_sha_at_timestamp(
            timeline, ts_only, "2026-04-08T16:00:00",
        )
        # Should be 9ac06cc88 or a later commit
        idx = next(
            i for i, (_, s) in enumerate(timeline)
            if s.startswith("9ac06cc88a00")
        )
        resolved_idx = next(
            i for i, (_, s) in enumerate(timeline) if s == sha
        )
        assert resolved_idx >= idx
