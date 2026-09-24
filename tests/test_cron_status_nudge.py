# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lapof: a red cron step reaches a reader at session start.

The self-claims gate sat red for four days in September (INV-fugus) because
nothing put a cron verdict in front of the agent. It happened again on
2026-09-23, when full-suite went red at d31439e6 and was noticed only because a
premise check happened to run ``ci-debug cron-status``. These tests pin the
nudge that closes that gap: it names each failing step, where it first went red,
and stays silent in every case where speaking would be noise or a guess.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
NUDGE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "cron_status_nudge.py"
SESSION_START_LOGIC = REPO_ROOT / ".agent" / "hooks" / "_shared" / "session_start_logic.sh"


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("cron_status_nudge", str(NUDGE_PATH))
    spec = importlib.util.spec_from_loader("cron_status_nudge", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cron_status_nudge"] = module
    loader.exec_module(module)
    return module


nudge = _load()

# The real output of `ci-debug cron-status` on 2026-09-24 (URLs trimmed).
RED = """Most recent cron verdict per workflow (walking origin/dev, up to 40 commits):

  FAIL ci/woodpecker/cron/full-suite: failure
      FAIL failure  d31439e6  2026-09-23T20:53:13-04:00  (6 commit(s) back)
           https://ci.example/repos/1/pipeline/3485/1
           OK   git
           OK   prepare-git
           OK   build-grammars
           FAIL test-all-packages (exit 1)
           OK   self-tree-validation
           OK   self-claims-gate
           FAIL test-agent-infra (exit 1)
  ...  ci/woodpecker/cron/nightly: success (4 matrix legs)
      OK   leg 1: success  afde2549  2026-09-24T00:41:54-04:00  (1 commit(s) back)
           OK   git
           OK   test-matrix
"""

# The verdict before it: the 13:00 UTC firing, green.
GREEN_BEFORE = """Most recent cron verdict per workflow (walking d31439e6^, up to 40 commits):

  OK   ci/woodpecker/cron/full-suite: success
      OK   success  d917e022  2026-09-23T04:32:17-04:00  (9 commit(s) back)
           OK   test-all-packages
           OK   test-agent-infra
"""

# A second red verdict in the same step, one firing earlier.
RED_BEFORE = """Most recent cron verdict per workflow (walking d31439e6^, up to 40 commits):

  FAIL ci/woodpecker/cron/full-suite: failure
      FAIL failure  aaaa1111  2026-09-23T08:53:13-04:00  (3 commit(s) back)
           FAIL test-all-packages (exit 1)
           OK   test-agent-infra
"""

ALL_GREEN = """Most recent cron verdict per workflow (walking origin/dev, up to 40 commits):

  OK   ci/woodpecker/cron/full-suite: success
      OK   success  bbbb2222  2026-09-24T09:00:00-04:00  (1 commit(s) back)
           OK   test-all-packages
"""


def _runner(responses: dict[str, "str | None"], calls: list[list[str]]):
    def run(args: list[str]) -> "str | None":
        calls.append(args)
        return responses.get(args[1] if len(args) > 1 else "HEAD")
    return run


class TestParsing:
    def test_a_red_workflow_names_its_failing_steps(self) -> None:
        [red] = nudge.parse_cron_status(RED)
        assert (red.workflow, red.sha, red.commits_back) == ("full-suite", "d31439e6", 6)
        assert red.steps == ("test-all-packages", "test-agent-infra")

    def test_a_green_or_pending_workflow_is_not_red(self) -> None:
        assert nudge.parse_cron_status(ALL_GREEN) == []
        assert all(r.workflow != "nightly" for r in nudge.parse_cron_status(RED))

    def test_unparseable_output_is_not_red(self) -> None:
        assert nudge.parse_cron_status("") == []
        assert nudge.parse_cron_status("curl: (6) Could not resolve host") == []


class TestFirstRed:
    def test_the_walk_stops_at_the_last_green_verdict(self) -> None:
        [red] = nudge.parse_cron_status(RED)
        calls: list[list[str]] = []
        got = nudge.find_first_red(red, _runner({"d31439e6^": GREEN_BEFORE}, calls), deadline=1e18)
        assert got == "d31439e6"
        assert calls == [["cron-status", "d31439e6^", "40"]]

    def test_the_walk_follows_a_step_that_stays_red(self) -> None:
        [red] = nudge.parse_cron_status(RED)
        calls: list[list[str]] = []
        got = nudge.find_first_red(
            red, _runner({"d31439e6^": RED_BEFORE, "aaaa1111^": GREEN_BEFORE}, calls), deadline=1e18)
        assert got == "aaaa1111"

    def test_an_unreadable_step_back_is_not_a_guess(self) -> None:
        """A failed lookup is unknown, not "first red here"."""
        [red] = nudge.parse_cron_status(RED)
        assert nudge.find_first_red(red, _runner({}, []), deadline=1e18) is None


class TestSuppression:
    def test_an_open_row_naming_the_step_and_commit_silences_it(self) -> None:
        items = [{"status": "violated", "title": "cron red: test-agent-infra",
                  "description": "failed at d31439e6"}]
        assert nudge.named_by_open_row("test-agent-infra", {"d31439e6"}, items)
        assert not nudge.named_by_open_row("test-all-packages", {"d31439e6"}, items)

    def test_a_closed_row_or_another_commit_does_not(self) -> None:
        closed = [{"status": "done", "title": "test-agent-infra red", "description": "d31439e6"}]
        other = [{"status": "todo_hard", "title": "test-agent-infra red", "description": "at 12345678"}]
        assert not nudge.named_by_open_row("test-agent-infra", {"d31439e6"}, closed)
        assert not nudge.named_by_open_row("test-agent-infra", {"d31439e6"}, other)


class TestTheLine:
    def test_it_names_each_unfiled_step_its_commit_and_the_command(self, tmp_path: Path) -> None:
        line = nudge.compute_line(
            _runner({"HEAD": RED, "d31439e6^": GREEN_BEFORE}, []), items=[],
            cache_path=tmp_path / "c.json", now=1000.0)
        assert "test-all-packages" in line and "test-agent-infra" in line
        assert "d31439e6" in line and "full-suite" in line
        assert "./scripts/ci-debug cron-status" in line
        assert "\n" not in line

    def test_it_omits_a_filed_step_and_is_silent_when_all_are_filed(self, tmp_path: Path) -> None:
        one = [{"status": "violated", "title": "test-agent-infra", "description": "d31439e6"}]
        line = nudge.compute_line(_runner({"HEAD": RED, "d31439e6^": GREEN_BEFORE}, []), items=one,
                                  cache_path=tmp_path / "a.json", now=1000.0)
        assert "test-all-packages" in line and "test-agent-infra" not in line
        both = one + [{"status": "todo_hard", "title": "test-all-packages", "description": "d31439e6"}]
        assert nudge.compute_line(_runner({"HEAD": RED, "d31439e6^": GREEN_BEFORE}, []), items=both,
                                  cache_path=tmp_path / "b.json", now=1000.0) == ""

    def test_silent_when_green_or_unreachable(self, tmp_path: Path) -> None:
        assert nudge.compute_line(_runner({"HEAD": ALL_GREEN}, []), items=[],
                                  cache_path=tmp_path / "g.json", now=1.0) == ""
        assert nudge.compute_line(_runner({}, []), items=[],
                                  cache_path=tmp_path / "u.json", now=1.0) == ""

    def test_the_network_half_is_cached_but_suppression_is_not(self, tmp_path: Path) -> None:
        """Re-fetched at most hourly; a row filed in between still silences it."""
        cache = tmp_path / "c.json"
        calls: list[list[str]] = []
        run = _runner({"HEAD": RED, "d31439e6^": GREEN_BEFORE}, calls)
        first = nudge.compute_line(run, items=[], cache_path=cache, now=1000.0)
        n = len(calls)
        again = nudge.compute_line(run, items=[], cache_path=cache, now=1000.0 + 600)
        assert again == first and len(calls) == n
        filed = [{"status": "violated", "title": "test-all-packages test-agent-infra",
                  "description": "d31439e6"}]
        assert nudge.compute_line(run, items=filed, cache_path=cache, now=1000.0 + 700) == ""
        nudge.compute_line(run, items=[], cache_path=cache, now=1000.0 + 3601)
        assert len(calls) > n

    def test_a_corrupt_cache_is_refetched_not_trusted(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        cache.write_text("{not json")
        calls: list[list[str]] = []
        line = nudge.compute_line(_runner({"HEAD": RED, "d31439e6^": GREEN_BEFORE}, calls),
                                  items=[], cache_path=cache, now=5.0)
        assert calls and "test-all-packages" in line
        assert json.loads(cache.read_text())["fetched_at"] == 5.0


class TestTheEntryPoint:
    def test_opt_out_and_ci_are_silent_without_a_network_call(self, monkeypatch, capsys) -> None:
        for var in ("CI", "HG_SKIP_CRON_NUDGE"):
            monkeypatch.setenv(var, "1")
            assert nudge.main([str(REPO_ROOT)]) == 0
            assert capsys.readouterr().out == ""
            monkeypatch.delenv(var)

    def test_any_failure_is_swallowed(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("HG_SKIP_CRON_NUDGE", raising=False)
        assert nudge.main([str(tmp_path / "no-such-repo")]) == 0
        assert capsys.readouterr().out == ""


def test_session_start_invokes_the_nudge_in_every_mode_branch() -> None:
    """A nudge wired into one mode-state silently does not apply to the mode
    you happen to be in. The reason WI-lapap's reader lives at session start,
    and not only at stop, applies unchanged."""
    text = SESSION_START_LOGIC.read_text(encoding="utf-8")

    def calls(name: str) -> int:
        return sum(text.count(f"\n{pad}{name}") for pad in ("", "    ", "        "))

    assert calls("_append_cron_status") >= 4
    assert calls("_append_cron_status") == calls("_append_concept_audit_cadence")
    assert "cron_status_nudge.py" in text
