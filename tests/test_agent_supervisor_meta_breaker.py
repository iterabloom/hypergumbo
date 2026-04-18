# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-mujuk meta-circuit-breaker.

Three pieces (per the tracker item):

1. Chain-length tracking in meta.json (scaffolding).
2. No-progress failure classification based on pane-byte delta (scaffolding).
3. Consecutive-failure kill switch that writes ``supervisor.auto-paused``
   after N chained no-progress failures (the actual signal).

Plus the new ``resume`` subcommand that clears the sentinel.

Isolated from real tmux / real processes via the same mock-runner pattern
``tests/test_agent_supervisor.py`` uses.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT_REAL / "scripts" / "agent-supervisor"


def _load_module() -> types.ModuleType:
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("agent_supervisor_mod", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("agent_supervisor_mod", loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def asv() -> types.ModuleType:
    return _load_module()


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    shared = root / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    for name in ("kill-transcript-sync.sh", "rotate-on-session-end.sh"):
        p = shared / name
        p.write_text("#!/bin/bash\nexit 0\n")
        p.chmod(0o755)
    return root


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


class MockRunner:
    """Matches the helper in test_agent_supervisor.py."""

    def __init__(self, responses: dict[tuple, tuple[int, str, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        key = tuple(cmd)
        rc, out, err = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=out, stderr=err)


def _pane_bytes(n: int) -> tuple[int, str, str]:
    """Build a canned capture-pane response of ``n`` bytes of stdout."""
    return (0, "x" * n, "")


# --- Chain-length tracking (scaffolding piece 1) ---


class TestChainLength:
    def test_root_spawn_starts_chain_at_one(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        name = sup.spawn_fresh(vendor="claude-code")
        assert name is not None
        meta = sup.read_meta(name)
        assert meta["chain_length"] == 1
        assert meta["consecutive_no_progress"] == 0
        assert meta["replaces"] is None

    def test_replaced_session_records_predecessor_pointer(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """After a replace, the new session's meta must point back at the
        session it replaced, with chain_length = prior + 1."""
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "hypergumbo-session-old", "-p"):
                _pane_bytes(2048),  # progress replacement (> threshold)
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        old_meta = {
            "session_id": "hypergumbo-session-old",
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": 3,
            "consecutive_no_progress": 0,
        }
        sup.write_meta("hypergumbo-session-old", old_meta)
        sup.replace_session("hypergumbo-session-old", old_meta)

        owned = [s for s in runner.calls if s[:2] == ["tmux", "new-session"]]
        assert len(owned) == 1
        # The new session name is in the new-session command at index -1.
        new_session_name = owned[0][-2] if owned[0][-1].startswith("claude") else owned[0][-1]
        # Actually, the cmd format is: tmux new-session -d -s <name> -e KEY=VAL <cli>.
        # Find the -s value.
        cmd = owned[0]
        s_idx = cmd.index("-s")
        new_name = cmd[s_idx + 1]
        new_meta = sup.read_meta(new_name)
        assert new_meta is not None
        assert new_meta["replaces"] == "hypergumbo-session-old"
        assert new_meta["chain_length"] == 4  # prior 3 + 1

    def test_progress_replacement_resets_consecutive_counter(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """A chain carrying 2 no-progress failures, followed by a PROGRESS
        replacement (enough pane bytes), must emit a fresh session whose
        consecutive_no_progress resets to 0."""
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "hypergumbo-session-old", "-p"):
                _pane_bytes(4096),  # progress replacement
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        old_meta = {
            "session_id": "hypergumbo-session-old",
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": 4,
            "consecutive_no_progress": 2,
        }
        sup.write_meta("hypergumbo-session-old", old_meta)
        sup.replace_session("hypergumbo-session-old", old_meta)
        # Find the spawned session name.
        new_cmd = next(c for c in runner.calls if c[:2] == ["tmux", "new-session"])
        new_name = new_cmd[new_cmd.index("-s") + 1]
        assert sup.read_meta(new_name)["consecutive_no_progress"] == 0

    def test_no_progress_replacement_increments_consecutive_counter(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "hypergumbo-session-old", "-p"):
                _pane_bytes(100),  # no-progress (< 512)
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        old_meta = {
            "session_id": "hypergumbo-session-old",
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": 2,
            "consecutive_no_progress": 1,
        }
        sup.write_meta("hypergumbo-session-old", old_meta)
        sup.replace_session("hypergumbo-session-old", old_meta)
        new_cmd = next(c for c in runner.calls if c[:2] == ["tmux", "new-session"])
        new_name = new_cmd[new_cmd.index("-s") + 1]
        assert sup.read_meta(new_name)["consecutive_no_progress"] == 2


# --- No-progress classification (scaffolding piece 2) ---


class TestNoProgressClassification:
    @pytest.mark.parametrize("pane_bytes,expected_consec", [
        (0, 1),        # empty pane → no-progress
        (100, 1),      # tiny pane → no-progress
        (512, 1),      # exactly at threshold → no-progress (≤)
        (513, 0),      # just over → progress (resets counter)
        (4096, 0),     # comfortably over → progress
    ])
    def test_threshold_classification(
        self, asv, state_dir, fake_repo, monkeypatch, pane_bytes, expected_consec,
    ) -> None:
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "hypergumbo-session-x", "-p"):
                _pane_bytes(pane_bytes),
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        meta = {
            "session_id": "hypergumbo-session-x",
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": 1,
            "consecutive_no_progress": 0,
        }
        sup.write_meta("hypergumbo-session-x", meta)
        sup.replace_session("hypergumbo-session-x", meta)
        new_cmd = next(c for c in runner.calls if c[:2] == ["tmux", "new-session"])
        new_name = new_cmd[new_cmd.index("-s") + 1]
        assert sup.read_meta(new_name)["consecutive_no_progress"] == expected_consec

    def test_capture_failure_treated_as_zero_bytes(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """If tmux capture-pane returns non-zero (e.g., session already
        dead), we treat current bytes as 0 — which is ≤ threshold, so
        classifies as no-progress. This is safe: a session we can't read
        from definitely isn't making forward progress."""
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "hypergumbo-session-x", "-p"):
                (1, "", "session not found"),
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        meta = {
            "session_id": "hypergumbo-session-x",
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": 1,
            "consecutive_no_progress": 0,
        }
        sup.write_meta("hypergumbo-session-x", meta)
        sup.replace_session("hypergumbo-session-x", meta)
        new_cmd = next(c for c in runner.calls if c[:2] == ["tmux", "new-session"])
        new_name = new_cmd[new_cmd.index("-s") + 1]
        # Can't-read-pane → classified as no-progress (1, not 0).
        assert sup.read_meta(new_name)["consecutive_no_progress"] == 1


# --- Kill switch (the actual signal) ---


class TestKillSwitch:
    def _replace_with(self, asv, state_dir, fake_repo, monkeypatch,
                     prior_consecutive: int, pane_bytes: int) -> asv.Supervisor:
        """Set up a supervisor with a session that has prior_consecutive
        no-progress failures on its chain, then replace it with the given
        pane-bytes count. Returns the supervisor for further inspection."""
        session = "hypergumbo-session-tail"
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", session, "-p"): _pane_bytes(pane_bytes),
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        meta = {
            "session_id": session,
            "cli_pid": 9999,
            "vendor": "claude-code",
            "chain_length": prior_consecutive + 1,
            "consecutive_no_progress": prior_consecutive,
        }
        sup.write_meta(session, meta)
        sup.replace_session(session, meta)
        sup.__test_runner = runner  # Attach for test inspection.
        return sup

    def test_fires_on_fifth_consecutive_no_progress(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        sup = self._replace_with(
            asv, state_dir, fake_repo, monkeypatch,
            prior_consecutive=4, pane_bytes=100,  # 5th no-progress
        )
        assert sup.auto_paused() is True
        # No new session spawned.
        assert not any(c[:2] == ["tmux", "new-session"] for c in sup.__test_runner.calls)
        # Log includes AUTO-PAUSED.
        log_text = (sup.state_dir / "respawn_log.log").read_text()
        assert "AUTO-PAUSED" in log_text
        assert "5 consecutive no-progress" in log_text

    def test_does_not_fire_on_fourth(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        sup = self._replace_with(
            asv, state_dir, fake_repo, monkeypatch,
            prior_consecutive=3, pane_bytes=100,  # 4th no-progress — not yet
        )
        assert sup.auto_paused() is False
        # Replacement WAS spawned.
        assert any(c[:2] == ["tmux", "new-session"] for c in sup.__test_runner.calls)

    def test_progress_replacement_resets_chain_and_does_not_fire(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """4 prior no-progress + a progress replacement → counter resets
        to 0, new session spawns, kill switch does NOT fire."""
        sup = self._replace_with(
            asv, state_dir, fake_repo, monkeypatch,
            prior_consecutive=4, pane_bytes=4096,  # progress replacement
        )
        assert sup.auto_paused() is False
        new_cmd = next(c for c in sup.__test_runner.calls if c[:2] == ["tmux", "new-session"])
        new_name = new_cmd[new_cmd.index("-s") + 1]
        assert sup.read_meta(new_name)["consecutive_no_progress"] == 0

    def test_time_agnostic(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """Kill switch fires on the 5th consecutive no-progress failure
        regardless of how much simulated time passed between them."""
        session = "hypergumbo-session-slow"
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", session, "-p"): _pane_bytes(100),
        })
        monkeypatch.setattr(asv, "pid_alive", lambda pid: False)
        # Fast-forward the clock by 24h between each replacement — this
        # should NOT affect the kill switch, which keys off the chain
        # counter, not wall-clock.
        clock = [1_000_000.0]
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
            now_fn=lambda: clock[0],
        )
        meta = {
            "session_id": session, "cli_pid": 9999, "vendor": "claude-code",
            "chain_length": 5, "consecutive_no_progress": 4,
        }
        sup.write_meta(session, meta)
        clock[0] += 24 * 3600  # 1 day passes
        sup.replace_session(session, meta)
        assert sup.auto_paused() is True

    def test_auto_paused_blocks_future_spawns(
        self, asv, state_dir, fake_repo,
    ) -> None:
        """Once the kill switch fires, ``spawn_fresh`` refuses to spawn
        until the sentinel is cleared."""
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.set_auto_paused("test")
        assert sup.spawn_fresh() is None
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)
        # Respawn log records the suppression.
        log = (state_dir / "respawn_log.log").read_text()
        assert "auto-paused; skipping spawn" in log

    def test_poll_loop_does_not_spawn_while_auto_paused(
        self, asv, state_dir, fake_repo,
    ) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (1, "", "no server"),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.set_auto_paused("unit test")
        sup.poll_once()
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)

    def test_autonomous_intent_off_short_circuits_before_kill_switch(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """OFF intent must short-circuit BEFORE any meta-breaker logic so
        we don't burn unnecessary work while the operator has explicitly
        disabled autonomous mode."""
        (fake_repo / "autonomous_intent.txt").write_text("OFF\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (1, "", "no server"),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.poll_once()
        # No tmux calls at all — OFF came first.
        assert all(c[0] != "tmux" for c in runner.calls)


# --- Clear / resume ---


class TestClearAutoPaused:
    def test_clear_auto_paused(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup.set_auto_paused("test reason")
        assert sup.auto_paused() is True
        sup.clear_auto_paused()
        assert sup.auto_paused() is False

    def test_clear_when_not_paused_is_noop(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert sup.auto_paused() is False
        sup.clear_auto_paused()  # Must not raise.
        assert sup.auto_paused() is False

    def test_resume_subcommand_clears_sentinel(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        (state / "supervisor.auto-paused").write_text("fired\n")
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        env["AGENT_SUPERVISOR_REPO_ROOT"] = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "resume"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert "auto-pause cleared" in result.stdout
        assert not (state / "supervisor.auto-paused").exists()

    def test_resume_subcommand_noop_when_not_paused(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        env["AGENT_SUPERVISOR_REPO_ROOT"] = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "resume"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert "not auto-paused" in result.stdout


# --- status report surfaces chain state ---


class TestStatusReport:
    def test_status_includes_auto_paused_flag(
        self, asv, state_dir, fake_repo,
    ) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        report = sup.status_report()
        assert report["auto_paused"] is False
        sup.set_auto_paused("unit test")
        report = sup.status_report()
        assert report["auto_paused"] is True

    def test_status_surfaces_chain_state_per_session(
        self, asv, state_dir, fake_repo,
    ) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (0, "hypergumbo-session-x\n", ""),
            ("tmux", "list-clients", "-t", "hypergumbo-session-x"): (1, "", ""),
            ("tmux", "capture-pane", "-t", "hypergumbo-session-x", "-p"): _pane_bytes(42),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.write_meta("hypergumbo-session-x", {
            "cli_pid": 111, "vendor": "claude-code",
            "chain_length": 4, "consecutive_no_progress": 3,
            "replaces": "hypergumbo-session-old",
        })
        report = sup.status_report()
        assert len(report["sessions"]) == 1
        s = report["sessions"][0]
        assert s["chain_length"] == 4
        assert s["consecutive_no_progress"] == 3
        assert s["replaces"] == "hypergumbo-session-old"
        assert report["kill_switch_threshold"] == asv.CONSECUTIVE_NO_PROGRESS_KILL_SWITCH


# --- Attached-client precedence (human escape hatch) ---


class TestAttachedClientPrecedence:
    def test_attached_client_prevents_replacement_regardless_of_chain(
        self, asv, state_dir, fake_repo,
    ) -> None:
        """Even a session with 4 prior no-progress failures must NOT be
        replaced while a human is attached. The kill switch lives inside
        replace_session(), so if replacement is gated out by the client
        check, the kill switch never gets a chance to fire — by design."""
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (0, "hypergumbo-session-x\n", ""),
            ("tmux", "list-clients", "-t", "hypergumbo-session-x"): (0, "/dev/pts/0\n", ""),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.write_meta("hypergumbo-session-x", {
            "cli_pid": 9999, "vendor": "claude-code",
            "chain_length": 5, "consecutive_no_progress": 4,
        })
        sup.poll_once()
        # No send-keys (no replacement), no kill-session, no new-session.
        assert not any(c[:2] == ["tmux", "send-keys"] for c in runner.calls)
        assert not any(c[:2] == ["tmux", "kill-session"] for c in runner.calls)
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)
        assert sup.auto_paused() is False
