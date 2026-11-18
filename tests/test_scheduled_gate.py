# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merging stops while the last scheduled full run is red (scripts/scheduled-gate).

The rule (owner, 2026-11-17) has three states and three ways through a red
one, and each is a place the gate could silently do the wrong thing: block on
a verdict it cannot read (every merge stops for a broken gate), pass a red run
because the change says some other step's name, lift the block for a fix that
names one of two red steps, or keep an old fix's trailer counting against a
NEW red verdict. Each is pinned against real git repositories: a bare remote
with a ``badges`` branch holding the verdict, and a clone that asks.
"""
# covers: scripts/scheduled-gate
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Optional

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scheduled-gate"
NOW = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("scheduled_gate", str(SCRIPT))
    spec = importlib.util.spec_from_loader("scheduled_gate", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gate = _load()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True).stdout.strip()


class Forge:
    """A bare ``origin`` and a clone of it on ``dev``."""

    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self._badges = tmp_path / "badges"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.work)],
                       check=True, capture_output=True)
        _git(self.work, "checkout", "-q", "-b", "dev")
        self.first = self.commit("base", {"src/a.py": "A = 1\n"}, push=True)

    def commit(self, message: str, files: dict[str, str], *, push: bool = False) -> str:
        for rel, text in files.items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", message)
        if push:
            _git(self.work, "push", "-q", "origin", "HEAD:dev")
            _git(self.work, "fetch", "-q", "origin")
        return _git(self.work, "rev-parse", "HEAD")

    def branch(self, name: str = "change") -> None:
        _git(self.work, "checkout", "-q", "-B", name, "origin/dev")

    def publish(self, content: str) -> None:
        """Put ``content`` at scheduled/full-suite.json on origin's badges branch."""
        if not self._badges.exists():
            subprocess.run(["git", "clone", "-q", str(self.origin), str(self._badges)],
                           check=True, capture_output=True)
            _git(self._badges, "checkout", "-q", "--orphan", "badges")
            _git(self._badges, "rm", "-rfq", "--ignore-unmatch", ".")
        path = self._badges / "scheduled" / "full-suite.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)
        _git(self._badges, "add", "-A")
        _git(self._badges, "commit", "-q", "-m", "verdict")
        _git(self._badges, "push", "-q", "origin", "HEAD:badges")

    def verdict(self, commit: str, *not_passed: str, finished: str = "2026-11-18T01:26:33Z") -> None:
        steps = [{"name": name, "result": "not_passed" if name in not_passed else "passed"}
                 for name in ("build-grammars", "test-all-packages", "self-claims-gate")]
        self.publish(json.dumps({
            "schema": 1, "workflow": "full-suite", "commit": commit,
            "url": "https://ci.example.invalid/pipeline/7", "finished_at": finished,
            "status": "failed" if not_passed else "passed", "steps": steps}))

    def ask(self, capsys, monkeypatch, *extra: str) -> tuple[int, str]:
        monkeypatch.chdir(self.work)
        rc = gate.main(["check", *extra])
        return rc, capsys.readouterr().out.strip()


@pytest.fixture
def forge(tmp_path: Path) -> Forge:
    return Forge(tmp_path)


def test_a_green_run_lets_the_change_merge(forge: Forge, capsys, monkeypatch) -> None:
    forge.verdict(forge.first)
    forge.branch()
    forge.commit("a change", {"src/a.py": "A = 2\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert rc == 0
    assert said.startswith(f"scheduled gate: the full suite passed at {forge.first[:8]} (")


@pytest.mark.parametrize(("content", "why"), [
    (None, "no origin/badges"),
    ("{not json", "scheduled/full-suite.json on origin/badges does not parse"),
    ('{"status": "passed"}', "scheduled/full-suite.json on origin/badges is not a verdict"),
    ('{"status": "exploded", "steps": [], "commit": "abc"}',
     "scheduled/full-suite.json on origin/badges is not a verdict"),
    ("[1, 2]", "scheduled/full-suite.json on origin/badges is not a verdict"),
])
def test_a_verdict_that_cannot_be_read_is_not_known_and_does_not_block(
        forge: Forge, capsys, monkeypatch, content: Optional[str], why: str) -> None:
    """A broken gate must not stop every merge, and must not pass for green."""
    if content is not None:
        forge.publish(content)
    forge.branch()
    forge.commit("a change", {"src/a.py": "A = 2\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert (rc, said) == (0, f"scheduled gate: NOT KNOWN ({why}); not blocking")


def test_a_badges_branch_without_the_file_is_not_known(forge: Forge, capsys, monkeypatch) -> None:
    forge.verdict(forge.first)
    _git(forge._badges, "rm", "-q", "scheduled/full-suite.json")
    (forge._badges / "other.json").write_text("{}")
    _git(forge._badges, "add", "-A")
    _git(forge._badges, "commit", "-q", "-m", "gone")
    _git(forge._badges, "push", "-q", "origin", "HEAD:badges")
    rc, said = forge.ask(capsys, monkeypatch)
    assert (rc, said) == (0, "scheduled gate: NOT KNOWN (no scheduled/full-suite.json on "
                             "origin/badges); not blocking")


def test_a_red_run_stops_a_change_that_names_nothing(forge: Forge, capsys, monkeypatch) -> None:
    forge.verdict(forge.first, "test-all-packages", "self-claims-gate")
    forge.branch()
    forge.commit("an unrelated change", {"src/a.py": "A = 2\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert rc == 3
    lines = said.splitlines()
    assert lines[0].startswith(f"scheduled gate: the full suite is RED at {forge.first[:8]} (")
    assert lines[0].endswith(": test-all-packages, self-claims-gate")
    assert "  https://ci.example.invalid/pipeline/7" in lines
    assert "  MERGING IS STOPPED while the last scheduled full run is red." in lines
    assert "      Fixes-scheduled-run: test-all-packages" in lines
    assert "      Fixes-scheduled-run: self-claims-gate" in lines


@pytest.mark.parametrize(("message", "allowed"), [
    ("fix the suite\n\nFixes-scheduled-run: test-all-packages", True),
    ("fix\n\nfixes-scheduled-run:   test-all-packages  ", True),
    ("fix\n\nFixes-scheduled-run: build-grammars, test-all-packages", True),
    # Another step's name, a mention that is not a trailer, a step that does not exist.
    ("fix\n\nFixes-scheduled-run: build-grammars", False),
    ("this fixes test-all-packages, I think", False),
    ("fix\n\nFixes-scheduled-run: test-all", False),
])
def test_a_change_merges_through_a_red_run_only_by_naming_a_red_step(
        forge: Forge, capsys, monkeypatch, message: str, allowed: bool) -> None:
    forge.verdict(forge.first, "test-all-packages")
    forge.branch()
    forge.commit("first commit of the change", {"src/b.py": "B = 1\n"})
    forge.commit(message, {"src/a.py": "A = 2\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert rc == (0 if allowed else 3), said
    if allowed:
        assert said.endswith("this change names test-all-packages in a Fixes-scheduled-run "
                             "trailer: allowed")


def test_a_change_to_tracker_data_alone_is_not_blocked(forge: Forge, capsys, monkeypatch) -> None:
    forge.verdict(forge.first, "test-all-packages")
    forge.branch()
    forge.commit("tracker: 2 files", {".agent/tracker/.ops/x.ops": "op\n",
                                      ".agent/tracker-workspace/.ops/y.ops": "op\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert rc == 0 and said.endswith("this change touches only tracker data: not blocked")
    # One file elsewhere and it is a change like any other.
    forge.commit("and code", {"src/a.py": "A = 2\n"})
    assert forge.ask(capsys, monkeypatch)[0] == 3
    # A change with no files at all is not "only tracker data".
    forge.branch("empty")
    _git(forge.work, "commit", "-q", "--allow-empty", "-m", "nothing")
    assert forge.ask(capsys, monkeypatch)[0] == 3


def test_a_merged_fix_lifts_the_block_until_the_next_red_verdict(
        forge: Forge, capsys, monkeypatch) -> None:
    """The full suite runs on a schedule: without this, every change waits up
    to twelve hours for the next run after the fix."""
    forge.verdict(forge.first, "test-all-packages", "self-claims-gate")
    # A fix for ONE of the two red steps merges: the other is still red.
    forge.branch("fix-one")
    forge.commit("fix\n\nFixes-scheduled-run: test-all-packages", {"src/a.py": "A = 2\n"}, push=True)
    forge.branch()
    forge.commit("an unrelated change", {"src/c.py": "C = 1\n"})
    assert forge.ask(capsys, monkeypatch)[0] == 3
    # The second fix merges: every red step is named on dev since the verdict.
    forge.branch("fix-two")
    second = forge.commit("fix\n\nFixes-scheduled-run: self-claims-gate",
                          {"src/a.py": "A = 3\n"}, push=True)
    forge.branch()
    forge.commit("an unrelated change", {"src/c.py": "C = 1\n"})
    rc, said = forge.ask(capsys, monkeypatch)
    assert rc == 0, said
    assert said.endswith("commits on origin/dev since then name every red step in a "
                         "Fixes-scheduled-run trailer: not blocked until the next scheduled run says")
    # The next run is red again, at the fixed commit: the old trailers are
    # before it and do not count.
    forge.verdict(second, "test-all-packages")
    assert forge.ask(capsys, monkeypatch)[0] == 3
    # And green lifts it for good.
    forge.verdict(second)
    assert forge.ask(capsys, monkeypatch)[0] == 0


def test_a_red_verdict_for_a_commit_this_clone_does_not_have_stays_a_block(
        forge: Forge, capsys, monkeypatch) -> None:
    forge.verdict("f" * 40, "test-all-packages")
    forge.branch("fix")
    forge.commit("fix\n\nFixes-scheduled-run: test-all-packages", {"src/a.py": "A = 2\n"}, push=True)
    forge.branch()
    forge.commit("an unrelated change", {"src/c.py": "C = 1\n"})
    assert forge.ask(capsys, monkeypatch)[0] == 3, "the range since the verdict cannot be read"


def test_the_ref_is_read_as_it_stands_when_the_fetch_fails_or_is_declined(
        forge: Forge, capsys, monkeypatch, tmp_path: Path) -> None:
    forge.verdict(forge.first, "test-all-packages")
    forge.branch()
    forge.commit("a change", {"src/a.py": "A = 2\n"})
    # Not fetched yet and told not to: the clone has no origin/badges.
    rc, said = forge.ask(capsys, monkeypatch, "--no-fetch")
    assert (rc, said) == (0, "scheduled gate: NOT KNOWN (no origin/badges); not blocking")
    assert forge.ask(capsys, monkeypatch)[0] == 3
    # The remote goes away: the verdict fetched before still stands.
    forge.origin.rename(tmp_path / "gone.git")
    assert forge.ask(capsys, monkeypatch)[0] == 3


def test_outside_a_repository_and_on_a_surprise_it_is_not_known(
        forge: Forge, capsys, monkeypatch, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert gate.main(["check"]) == 0
    assert capsys.readouterr().out.strip() == (
        "scheduled gate: NOT KNOWN (not inside a git repository); not blocking")

    def boom(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        raise RuntimeError("unplanned")

    monkeypatch.setattr(gate, "decide", boom)
    rc, said = forge.ask(capsys, monkeypatch)
    assert (rc, said) == (0, "scheduled gate: NOT KNOWN (RuntimeError: unplanned); not blocking")


@pytest.mark.parametrize(("finished", "said"), [
    ("2026-11-18T02:30:00Z", "under an hour ago"),
    ("2026-11-17T13:00:00Z", "14 h ago"),
    ("yesterday", "age not known"),
])
def test_the_line_says_how_old_the_verdict_is(forge: Forge, finished: str, said: str) -> None:
    forge.verdict(forge.first, finished=finished)
    _git(forge.work, "fetch", "-q", "origin", "badges")
    allowed, line = gate.decide(forge.work, ref="origin/badges", base="origin/dev",
                                head="HEAD", now=NOW)
    assert allowed and line == f"scheduled gate: the full suite passed at {forge.first[:8]} ({said})"


def test_a_git_command_that_cannot_run_reads_as_no_answer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert gate._git(tmp_path, "status") is None


def test_as_a_process_it_exits_3_when_blocked(forge: Forge) -> None:
    forge.verdict(forge.first, "test-all-packages")
    forge.branch()
    forge.commit("a change", {"src/a.py": "A = 2\n"})
    done = subprocess.run([str(SCRIPT), "check"], cwd=forge.work, capture_output=True, text=True)
    assert done.returncode == 3 and "MERGING IS STOPPED" in done.stdout
    assert subprocess.run([str(SCRIPT)], cwd=forge.work, capture_output=True).returncode == 2
