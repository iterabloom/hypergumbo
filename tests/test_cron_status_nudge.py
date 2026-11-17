# SPDX-License-Identifier: AGPL-3.0-or-later
"""A red scheduled run reaches a reader at session start (WI-lapof, WI-zutub).

Unless something puts a scheduled run's verdict in front of the agent, a red
step can sit unnoticed across days of sessions that merge past it (history:
INV-fugus; pipelines 6453 and 6480 through the 10.0.0 release). The first
nudge called ``ci-debug cron-status`` and printed nothing whenever that failed,
which it did in every hook environment (no virtualenv on PATH) and on every
cold call (41 s against a 5 s hook timeout).

The nudge now reads the verdict the scheduled runs publish to the ``badges``
branch, from ``origin/badges``, with local git. These tests build that shape
for real: a bare repository standing in for GitHub, a publisher pushing
verdicts to its ``badges`` branch, and a reader repository whose ``origin`` is
the bare one. WI-zutub's close condition is the two subprocess tests run under
``env -i HOME=<tmp> PATH=/usr/bin:/bin``: the RED line, and the "not checked"
line when the ref is missing.
"""
# covers: .agent/hooks/_shared/cron_status_nudge.py, .agent/hooks/_shared/session_start_logic.sh
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Optional, Union

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NUDGE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "cron_status_nudge.py"
SESSION_START_LOGIC = REPO_ROOT / ".agent" / "hooks" / "_shared" / "session_start_logic.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
C_GREEN = "1111111111111111111111111111111111111111"
C_RED1 = "2222222222222222222222222222222222222222"
C_RED2 = "3333333333333333333333333333333333333333"
C_RED3 = "4444444444444444444444444444444444444444"
FULL = ["prepare-git", "test-all-packages", "test-agent-infra"]


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("cron_status_nudge", str(NUDGE_PATH))
    spec = importlib.util.spec_from_loader("cron_status_nudge", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cron_status_nudge"] = module
    loader.exec_module(module)
    return module


nudge = _load()


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV})
    return proc.stdout.strip()


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verdict(workflow: str = "full-suite", *, commit: str = C_RED1, red: tuple[str, ...] = (),
            steps: list[str] = FULL, leg: Optional[str] = None,
            finished: Optional[float] = None) -> dict:
    return {
        "schema": 1, "workflow": workflow, "leg": leg, "commit": commit, "pipeline": 1,
        "url": None, "finished_at": _stamp(time.time() - 3600 if finished is None else finished),
        "status": "failed" if red else "passed",
        "steps": [{"name": s, "result": "not_passed" if s in red else "passed"} for s in steps],
        "coverage": None,
    }


def path_of(v: dict) -> str:
    return f"scheduled/{v['workflow']}-{v['leg']}.json" if v["leg"] else f"scheduled/{v['workflow']}.json"


class Badges:
    """A bare 'GitHub', a publisher of the badges branch, and a reader repo."""

    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "origin.git"
        _git("init", "-q", "--bare", str(self.origin))
        self.pub = tmp_path / "pub"
        _git("init", "-q", str(self.pub))
        _git("checkout", "-q", "--orphan", "badges", cwd=self.pub)
        self.root = tmp_path / "repo"
        _git("init", "-q", str(self.root))
        _git("remote", "add", "origin", str(self.origin), cwd=self.root)
        self.tracker_log = tmp_path / "tracker.log"

    def publish(self, *verdicts: dict, raw: Optional[dict[str, str]] = None) -> None:
        files = {path_of(v): json.dumps(v) for v in verdicts}
        files.update(raw or {})
        for name, text in files.items():
            (self.pub / name).parent.mkdir(parents=True, exist_ok=True)
            (self.pub / name).write_text(text)
            badge = self.pub / name.replace(".json", "-badge.json")
            badge.write_text('{"schemaVersion": 1}')
        _git("add", ".", cwd=self.pub)
        _git("commit", "-q", "-m", "verdict", cwd=self.pub)
        _git("push", "-q", str(self.origin), "badges", cwd=self.pub)

    def fetch(self) -> None:
        _git("fetch", "-q", "origin", "badges", cwd=self.root)

    def tracker(self, items: Optional[list[dict]] = None, *, script: Optional[str] = None) -> None:
        """A stand-in ``scripts/tracker`` that logs each call and prints ``items``."""
        (self.root / "scripts").mkdir(exist_ok=True)
        data = self.root / "items.json"
        data.write_text(json.dumps(items or []))
        stub = self.root / "scripts" / "tracker"
        stub.write_text(script or (
            f"#!/bin/sh\necho \"$*\" >> '{self.tracker_log}'\ncat '{data}'\n"))
        stub.chmod(0o755)

    def line(self, *, now: Optional[float] = None, refreshed: bool = True) -> str:
        return nudge.compute_line(self.root, now=time.time() if now is None else now,
                                  refreshed=refreshed)


@pytest.fixture
def badges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Badges:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HG_SKIP_CRON_NUDGE", raising=False)
    b = Badges(tmp_path)
    b.tracker([])
    return b


def _both_green(b: Badges) -> None:
    b.publish(verdict(commit=C_GREEN), verdict("nightly", leg="3.10", commit=C_GREEN,
                                              steps=["test-matrix"]))


def _hook_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), "PATH": "/usr/bin:/bin"}


def _hook_python() -> str:
    """The interpreter a hook's ``python3`` resolves to without a virtualenv."""
    return shutil.which("python3", path="/usr/bin:/bin") or sys.executable


# ── WI-zutub's close condition: a hook-like environment ─────────────────────

def test_a_plain_environment_sees_the_red_line(badges: Badges, tmp_path: Path) -> None:
    badges.publish(verdict(commit=C_RED1, red=("test-agent-infra",)),
                   verdict("nightly", leg="3.10", commit=C_GREEN, steps=["test-matrix"]))
    proc = subprocess.run(
        ["env", "-i", *(f"{k}={v}" for k, v in _hook_env(tmp_path).items()),
         _hook_python(), str(NUDGE_PATH), str(badges.root)],
        capture_output=True, text=True, check=False, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.startswith("scheduled run RED: full-suite (`test-agent-infra`) "
                                  f"not passed at {C_RED1[:8]}"), proc.stdout
    assert "not refreshed" not in proc.stdout
    assert proc.stdout.count("\n") == 1


def test_a_plain_environment_without_the_ref_sees_not_checked(badges: Badges, tmp_path: Path) -> None:
    proc = subprocess.run(
        ["env", "-i", *(f"{k}={v}" for k, v in _hook_env(tmp_path).items()),
         _hook_python(), str(NUDGE_PATH), str(badges.root)],
        capture_output=True, text=True, check=False, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.startswith("Scheduled-run status not checked: no origin/badges ref"), proc.stdout
    assert "origin/badges not refreshed" in proc.stdout
    assert proc.stdout.rstrip().endswith("Run `./scripts/ci-debug cron-status`.")


# ── what it says ────────────────────────────────────────────────────────────

def test_a_red_verdict_names_steps_commit_age_and_first_red(badges: Badges) -> None:
    now = time.time()
    badges.publish(verdict(commit=C_GREEN, finished=now - 30 * 3600),
                   verdict("nightly", leg="3.11", commit=C_GREEN, steps=["test-matrix"]))
    badges.publish(verdict(commit=C_RED1, red=("test-agent-infra",), finished=now - 18 * 3600))
    badges.publish(verdict(commit=C_RED2, red=("test-agent-infra", "test-all-packages"),
                           finished=now - 5 * 3600))
    badges.fetch()
    line = badges.line(now=now)
    assert line.startswith(
        "scheduled run RED: full-suite (`test-all-packages`, `test-agent-infra`) "
        f"not passed at {C_RED2[:8]}, verdict 5 h old, first red at {C_RED1[:8]}; "
        "fix it or file a tracker row naming the step and commit"), line
    assert "\n" not in line and "prepare-git" not in line


def test_the_walk_stops_where_the_red_steps_change(badges: Badges) -> None:
    badges.publish(verdict(commit=C_RED1, red=("prepare-git",)),
                   verdict("nightly", leg="3.11", commit=C_GREEN, steps=["test-matrix"]))
    badges.publish(verdict(commit=C_RED2, red=("test-agent-infra",)))
    badges.fetch()
    assert f"first red at {C_RED2[:8]}" in badges.line()


def test_a_walk_that_never_leaves_red_does_not_guess(badges: Badges) -> None:
    badges.publish(verdict(commit=C_RED1, red=("test-agent-infra",)),
                   verdict("nightly", leg="3.11", commit=C_GREEN, steps=["test-matrix"]))
    badges.publish(verdict(commit=C_RED2, red=("test-agent-infra",)))
    badges.fetch()
    line = badges.line()
    assert ("first-red commit not determined (red in all 2 verdicts read, oldest at "
            f"{C_RED1[:8]})") in line
    assert "first red at" not in line


def test_the_walk_reads_at_most_max_history_versions(badges: Badges, monkeypatch) -> None:
    monkeypatch.setattr(nudge, "MAX_HISTORY", 2)
    badges.publish(verdict(commit=C_GREEN), verdict("nightly", leg="3.11", commit=C_GREEN,
                                                    steps=["test-matrix"]))
    for commit in (C_RED1, C_RED2, C_RED3):
        badges.publish(verdict(commit=commit, red=("test-agent-infra",)))
    badges.fetch()
    assert f"red in all 2 verdicts read, oldest at {C_RED2[:8]}" in badges.line()


def test_an_unreadable_older_version_is_not_a_guess(badges: Badges) -> None:
    badges.publish(raw={"scheduled/full-suite.json": "{broken"})
    badges.publish(verdict(commit=C_RED2, red=("test-agent-infra",)),
                   verdict("nightly", leg="3.11", commit=C_GREEN, steps=["test-matrix"]))
    badges.fetch()
    line = badges.line()
    assert "first-red commit not determined (version " in line and "unreadable)" in line


def test_a_failed_fetch_does_not_hide_a_red_verdict(badges: Badges, capsys) -> None:
    badges.publish(verdict(commit=C_RED1, red=("test-all-packages",)),
                   verdict("nightly", leg="3.10", commit=C_GREEN, steps=["test-matrix"]))
    badges.fetch()
    _git("remote", "set-url", "origin", str(badges.root.parent / "gone.git"), cwd=badges.root)
    assert nudge.main([str(badges.root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("scheduled run RED: full-suite (`test-all-packages`)"), out
    assert out.rstrip().endswith("; origin/badges not refreshed")


def test_main_refreshes_the_ref_before_reading(badges: Badges, capsys) -> None:
    _both_green(badges)
    badges.fetch()
    badges.publish(verdict(commit=C_RED1, red=("prepare-git",)))
    assert nudge.main([str(badges.root)]) == 0
    assert "(`prepare-git`)" in capsys.readouterr().out


def test_a_green_fresh_verdict_is_silent(badges: Badges, capsys) -> None:
    _both_green(badges)
    assert nudge.main([str(badges.root)]) == 0
    assert capsys.readouterr().out == ""
    assert not badges.tracker_log.exists(), "the tracker is consulted only when something is red"


#: The owner's allowances, written out here so that a changed table fails a
#: test instead of silently moving the test along with it.
ALLOWANCES = {"full-suite": 26, "nightly": 50}


def test_the_allowance_table_is_exactly_the_ruled_one() -> None:
    assert nudge.ALLOWANCE_HOURS == ALLOWANCES


@pytest.mark.parametrize(("workflow", "allowance"), [("full-suite", 26), ("nightly", 50)])
def test_a_verdict_older_than_its_allowance_speaks(badges: Badges, workflow: str,
                                                   allowance: int) -> None:
    now = time.time()
    leg = "3.12" if workflow == "nightly" else None
    others = [w for w in ALLOWANCES if w != workflow]
    fresh = [verdict(w, commit=C_GREEN, finished=now - 3600) for w in others]
    badges.publish(verdict(workflow, leg=leg, commit=C_GREEN,
                           finished=now - (allowance - 1) * 3600), *fresh)
    badges.fetch()
    assert badges.line(now=now) == ""
    later = now + 2 * 3600
    fresh_again = [verdict(w, commit=C_GREEN, finished=later - 3600) for w in others]
    badges.publish(*fresh_again)
    badges.fetch()
    label = f"{workflow} {leg}" if leg else workflow
    assert badges.line(now=later) == (
        f"{label}: newest verdict is {allowance + 1} h old: the scheduled run has not published since")


def test_no_verdict_for_a_workflow_is_not_checked(badges: Badges) -> None:
    badges.publish(verdict(commit=C_GREEN))
    badges.fetch()
    assert badges.line() == ("Scheduled-run status not checked: no verdict for nightly on "
                             "origin/badges. Run `./scripts/ci-debug cron-status`.")


def test_a_ref_without_verdicts_is_not_checked(badges: Badges) -> None:
    badges.publish(raw={"coverage.json": "{}"})
    badges.fetch()
    assert badges.line().startswith(
        "Scheduled-run status not checked: no verdict file under scheduled/ on origin/badges.")


#: A file that is not a verdict: its text, or the fields that spoil a good one.
#: The good verdict is built INSIDE the test. ``verdict()`` stamps the current
#: time, and a value made while the module is imported becomes part of the
#: test's id: xdist workers that import the module in different seconds then
#: collect different ids and the whole run stops ("Different tests were
#: collected between gw7 and gw6").
UNPARSEABLE: dict[str, Union[str, dict]] = {
    "not JSON": "{broken",
    "another schema": '{"schema": 2}',
    "fields missing": '{"schema": 1, "workflow": "x"}',
    "finished_at is not a time": {"finished_at": "yesterday"},
    "a step without a name": {"steps": [{"result": "passed"}]},
}


@pytest.mark.parametrize("case", list(UNPARSEABLE))
def test_a_verdict_that_does_not_parse_is_not_checked(badges: Badges, case: str) -> None:
    spoiled = UNPARSEABLE[case]
    text = spoiled if isinstance(spoiled, str) else json.dumps({**verdict(), **spoiled})
    _both_green(badges)
    badges.publish(raw={"scheduled/full-suite.json": text})
    badges.fetch()
    line = badges.line()
    assert line.startswith("Scheduled-run status not checked: "
                           "origin/badges:scheduled/full-suite.json does not parse"), line
    assert "no verdict for" not in line


def test_an_unexpected_exception_is_reported_not_swallowed(badges: Badges, capsys,
                                                           monkeypatch) -> None:
    def boom(*_a, **_k):
        raise KeyError("x")
    monkeypatch.setattr(nudge, "compute_line", boom)
    assert nudge.main([str(badges.root)]) == 0
    assert capsys.readouterr().out == (
        "Scheduled-run status not checked: unexpected KeyError. "
        "Run `./scripts/ci-debug cron-status`.\n")


@pytest.mark.parametrize("var", ["CI", "HG_SKIP_CRON_NUDGE"])
def test_ci_and_opt_out_are_silent_without_a_fetch(badges: Badges, capsys, monkeypatch,
                                                   var: str) -> None:
    def no_fetch(_root):
        raise AssertionError("fetched")
    monkeypatch.setattr(nudge, "refresh", no_fetch)
    monkeypatch.setenv(var, "1")
    assert nudge.main([str(badges.root)]) == 0
    assert capsys.readouterr().out == ""


def test_a_hanging_fetch_is_cut_off(badges: Badges, monkeypatch) -> None:
    """The fetch is bounded: a remote that never answers costs FETCH_TIMEOUT."""
    hang = badges.root.parent / "hang"
    hang.write_text("#!/bin/sh\nsleep 30\n")
    hang.chmod(0o755)
    _git("remote", "set-url", "origin", "ext::" + str(hang), cwd=badges.root)
    _git("config", "protocol.ext.allow", "always", cwd=badges.root)
    monkeypatch.setattr(nudge, "FETCH_TIMEOUT", 0.5)
    start = time.monotonic()
    assert nudge.refresh(badges.root) is False
    assert time.monotonic() - start < 5


# ── suppression through the tracker ────────────────────────────────────────

def _red(badges: Badges) -> None:
    badges.publish(verdict(commit=C_GREEN), verdict("nightly", leg="3.11", commit=C_GREEN,
                                                    steps=["test-matrix"]))
    badges.publish(verdict(commit=C_RED1, red=("test-agent-infra", "test-all-packages")))
    badges.fetch()


def test_an_open_item_naming_step_and_commit_suppresses_that_step(badges: Badges) -> None:
    _red(badges)
    badges.tracker([{"status": "violated", "title": "cron red: test-agent-infra",
                     "description": f"failed at {C_RED1[:8]}"}])
    line = badges.line()
    assert "(`test-all-packages`)" in line and "test-agent-infra" not in line
    assert badges.tracker_log.read_text().split() == ["--json", "list"]


def test_every_step_filed_is_silent(badges: Badges) -> None:
    _red(badges)
    badges.tracker([{"status": "todo_hard", "title": "test-agent-infra and test-all-packages",
                     "description": C_RED1[:8]}])
    assert badges.line() == ""


@pytest.mark.parametrize("status", sorted(nudge._RESOLVED))
def test_a_resolved_item_does_not_suppress(badges: Badges, status: str) -> None:
    _red(badges)
    badges.tracker([{"status": status, "title": "test-agent-infra", "description": C_RED1[:8]}])
    assert "`test-agent-infra`" in badges.line()


def test_an_item_naming_only_the_step_does_not_suppress(badges: Badges) -> None:
    _red(badges)
    badges.tracker([{"status": "violated", "title": "test-agent-infra flaky",
                     "description": "at 99999999"}])
    assert "`test-agent-infra`" in badges.line()


def test_an_item_naming_the_first_red_commit_suppresses(badges: Badges) -> None:
    _red(badges)
    badges.publish(verdict(commit=C_RED2, red=("test-agent-infra",)))
    badges.fetch()
    badges.tracker([{"status": "violated", "title": "test-agent-infra",
                     "description": f"red since {C_RED1[:8]}"}])
    assert badges.line() == ""


@pytest.mark.parametrize("script", [
    "#!/bin/sh\nexit 1\n",
    "#!/bin/sh\necho 'not json'\n",
    "#!/bin/sh\necho '{\"a\": 1}'\n",
    "#!/bin/sh\nsleep 30\n",
])
def test_a_tracker_that_cannot_be_consulted_does_not_hide_red(badges: Badges, script: str,
                                                               monkeypatch) -> None:
    monkeypatch.setattr(nudge, "TRACKER_TIMEOUT", 0.5)
    _red(badges)
    badges.tracker(script=script)
    start = time.monotonic()
    line = badges.line()
    assert time.monotonic() - start < 10
    assert "(`test-all-packages`, `test-agent-infra`)" in line
    assert "(tracker not consulted)" in line


def test_a_spent_budget_skips_the_tracker_and_still_prints_red(badges: Badges) -> None:
    """The hook that runs this has 5 s for everything, and prints nothing at
    all when it runs out. A slow fetch must cost the tracker its turn, never
    the line."""
    _red(badges)
    badges.tracker([{"status": "todo_hard", "title": "test-agent-infra and test-all-packages",
                     "description": C_RED1[:8]}])
    line = nudge.compute_line(badges.root, now=time.time(), refreshed=True,
                              deadline=time.monotonic())
    assert "(`test-all-packages`, `test-agent-infra`)" in line
    assert "(tracker not consulted)" in line
    assert not badges.tracker_log.exists()


def test_a_budget_with_room_consults_the_tracker(badges: Badges) -> None:
    _red(badges)
    badges.tracker([{"status": "todo_hard", "title": "test-agent-infra and test-all-packages",
                     "description": C_RED1[:8]}])
    assert nudge.compute_line(badges.root, now=time.time(), refreshed=True,
                              deadline=time.monotonic() + 30) == ""


def test_no_time_left_starts_no_tracker(badges: Badges, monkeypatch) -> None:
    badges.tracker([])
    def started(*_args, **_kwargs):
        raise AssertionError("a process was started")
    monkeypatch.setattr(nudge, "_bounded", started)
    assert nudge.open_items(badges.root, timeout=0.0) is None
    assert nudge.open_items(badges.root, timeout=-1.0) is None


def test_the_budget_starts_before_the_fetch(badges: Badges, monkeypatch, capsys) -> None:
    """The fetch spends the budget too: the deadline is set before it runs."""
    seen: dict[str, float] = {}
    def slow_refresh(_root: Path) -> bool:
        seen["fetch_started"] = time.monotonic()
        time.sleep(0.3)
        return True
    def fake_line(_root: Path, *, now: float, refreshed: bool, deadline: Optional[float] = None) -> str:
        assert deadline is not None
        seen["deadline"] = deadline
        return ""
    monkeypatch.setattr(nudge, "refresh", slow_refresh)
    monkeypatch.setattr(nudge, "compute_line", fake_line)
    assert nudge.main([str(badges.root)]) == 0
    assert 0 < seen["deadline"] - seen["fetch_started"] <= nudge.TOTAL_BUDGET + 0.05


def test_the_worst_case_fits_the_session_start_hook() -> None:
    """Fetch timed out, then the tracker used the rest: under the hook's 5 s
    (.claude/settings.json) with room for the hook's other work."""
    assert nudge.FETCH_TIMEOUT < nudge.TOTAL_BUDGET <= 4.0
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    timeouts = [h["timeout"] for group in settings["hooks"]["SessionStart"] for h in group["hooks"]]
    assert timeouts and all(nudge.TOTAL_BUDGET + 1.0 <= t for t in timeouts)


def test_the_tracker_runs_with_the_repository_virtualenv_on_path(badges: Badges, tmp_path: Path) -> None:
    """``scripts/tracker`` finds its CLI on PATH; a hook has no activated
    virtualenv, so the nudge puts ``<root>/.venv/bin`` first."""
    _red(badges)
    venv_bin = badges.root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    cli = venv_bin / "hypergumbo-tracker"
    cli.write_text("#!/bin/sh\necho '[{\"status\": \"violated\", \"title\": "
                   f"\"test-agent-infra test-all-packages\", \"description\": \"{C_RED1[:8]}\"}}]'\n")
    cli.chmod(0o755)
    badges.tracker(script="#!/bin/sh\nexec hypergumbo-tracker \"$@\"\n")
    proc = subprocess.run(
        ["env", "-i", *(f"{k}={v}" for k, v in _hook_env(tmp_path).items()),
         _hook_python(), str(NUDGE_PATH), str(badges.root)],
        capture_output=True, text=True, check=False, timeout=30)
    assert proc.returncode == 0 and proc.stdout == ""


# ── wiring ─────────────────────────────────────────────────────────────────

def test_session_start_invokes_the_nudge_in_every_mode_branch() -> None:
    """A nudge wired into one mode-state silently does not apply to the mode
    you happen to be in, so session start must call it from every mode
    branch, exactly as often as the concept-audit cadence nudge."""
    text = SESSION_START_LOGIC.read_text(encoding="utf-8")

    def calls(name: str) -> int:
        return sum(text.count(f"\n{pad}{name}") for pad in ("", "    ", "        "))

    assert calls("_append_cron_status") >= 4
    assert calls("_append_cron_status") == calls("_append_concept_audit_cadence")
    assert "cron_status_nudge.py" in text


def test_session_start_appends_what_the_nudge_prints(tmp_path: Path) -> None:
    """``_append_cron_status`` passes the nudge's one line through as is."""
    shared = tmp_path / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    shutil.copy(SESSION_START_LOGIC, shared / "session_start_logic.sh")
    (shared / "cron_status_nudge.py").write_text("print('Scheduled-run status not checked: x.')\n")
    # Only the helper: sourcing the whole file runs the session-start logic.
    script = (f'REPO_ROOT="{tmp_path}"; '
              f"eval \"$(sed -n '/^_append_cron_status()/,/^}}/p' '{shared}/session_start_logic.sh')\"; "
              'SESSION_START_MESSAGE=""; _append_cron_status; printf "%s" "$SESSION_START_MESSAGE"')
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                          env={**os.environ, "REPO_ROOT": str(tmp_path)}, timeout=30)
    assert proc.stdout.endswith("Scheduled-run status not checked: x."), (proc.stdout, proc.stderr)
