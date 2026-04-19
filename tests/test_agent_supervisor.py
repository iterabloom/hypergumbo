# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/agent-supervisor`` (WI-rofuv + WI-nusor).

Isolates the supervisor from the real tmux, real processes, and the real
filesystem by:

* Injecting a mock ``runner`` callable in place of the thin
  ``run_subprocess`` seam.
* Using temp directories for state + repo root.
* Stubbing ``time.time`` / ``time.monotonic`` / ``time.sleep`` where
  needed for time-dependent logic.

The test surface is organized by concern: decision-matrix (pure function),
state-dir bookkeeping (rate limit + respawn log + meta), pane-delta
tracking, the replacement sequence (WI-nusor), the poll loop, the status
report, and the CLI dispatch.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT_REAL / "scripts" / "agent-supervisor"


# The script has no `.py` extension and is not in sys.path as a package.
# Import it directly via importlib so we can call its functions in-process
# (much faster than subprocess, and enables mocking). We pass an explicit
# SourceFileLoader because spec_from_file_location returns None for files
# that don't match a known suffix (no `.py`).
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
    """Imported once per test module; each test reaches into it."""
    return _load_module()


# --- Fixtures for supervisor instances ---


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal fake repo containing autonomous_intent.txt and the shared
    cleanup scripts at the paths the supervisor expects."""
    root = tmp_path / "repo"
    root.mkdir()
    shared = root / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    # Write simple pass-through stubs so invoke_shared_cleanup can run
    # them without exploding. Real scripts are tested elsewhere.
    for name in ("kill-transcript-sync.sh", "rotate-on-session-end.sh"):
        p = shared / name
        p.write_text("#!/bin/bash\n# stub for tests\nexit 0\n")
        p.chmod(0o755)
    return root


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


class MockRunner:
    """Accumulate `runner` invocations and reply with canned results."""

    def __init__(self, responses: dict[tuple, tuple[int, str, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        key = tuple(cmd)
        rc, out, err = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=out, stderr=err)


# --- Autouse: disable the seed-bootstrap pane-ready wait in existing
# tests. The seed-specific tests exercise the wait helper directly with
# explicit arguments; everything else just needs spawn_fresh() to not
# hang on a real-time capture-pane poll loop.


@pytest.fixture(autouse=True)
def _disable_pane_ready_wait(asv, monkeypatch) -> None:
    monkeypatch.setattr(asv, "PANE_READY_MAX_WAIT_SEC", 0.0)


# --- Decision matrix: pure function, parametrize the truth table ---


class TestDecisionMatrix:
    @pytest.fixture
    def decide(self, asv):
        return asv.decide_action

    def test_off_intent_always_noop(self, decide) -> None:
        assert decide(intent="OFF", has_session=True, clients_attached=False,
                      cli_alive=True, pane_frozen=True) == "noop"
        assert decide(intent="OFF", has_session=False, clients_attached=False,
                      cli_alive=None, pane_frozen=False) == "noop"

    def test_no_session_spawns(self, decide) -> None:
        assert decide(intent="DEEP", has_session=False, clients_attached=False,
                      cli_alive=None, pane_frozen=False) == "spawn"

    def test_clients_attached_is_noop(self, decide) -> None:
        """Human is watching — hands off."""
        assert decide(intent="BROAD", has_session=True, clients_attached=True,
                      cli_alive=True, pane_frozen=True) == "noop"

    def test_cli_dead_replaces(self, decide) -> None:
        assert decide(intent="DEEP", has_session=True, clients_attached=False,
                      cli_alive=False, pane_frozen=False) == "replace"

    def test_frozen_pane_replaces(self, decide) -> None:
        assert decide(intent="DEEP", has_session=True, clients_attached=False,
                      cli_alive=True, pane_frozen=True) == "replace"

    def test_working_session_noop(self, decide) -> None:
        assert decide(intent="BROAD", has_session=True, clients_attached=False,
                      cli_alive=True, pane_frozen=False) == "noop"


# --- tmux_session_exists / wait_for_session_gone (CLI liveness proxy) ---


class TestTmuxSessionExists:
    def test_rc_zero_is_alive(self, asv) -> None:
        runner = MockRunner({
            ("tmux", "has-session", "-t", "sess"): (0, "", ""),
        })
        assert asv.tmux_session_exists("sess", runner) is True

    def test_rc_nonzero_is_gone(self, asv) -> None:
        runner = MockRunner({
            ("tmux", "has-session", "-t", "sess"): (1, "", "no session"),
        })
        assert asv.tmux_session_exists("sess", runner) is False


class TestWaitForSessionGone:
    def test_returns_true_when_session_disappears(self, asv, monkeypatch) -> None:
        """Session is alive on the first poll, gone on the second — helper
        should return True before the deadline."""
        results = iter([True, False, False])
        monkeypatch.setattr(
            asv, "tmux_session_exists",
            lambda s, runner=None: next(results),
        )
        clock = [0.0]
        def monotonic():
            t = clock[0]
            clock[0] += 0.5
            return t
        assert asv.wait_for_session_gone(
            "sess",
            timeout_sec=10.0,
            runner=lambda cmd: None,
            sleep_fn=lambda s: None,
            now_fn=monotonic,
        ) is True

    def test_returns_false_on_timeout(self, asv, monkeypatch) -> None:
        """Session alive on every poll, deadline reached → False."""
        monkeypatch.setattr(
            asv, "tmux_session_exists",
            lambda s, runner=None: True,
        )
        clock = [0.0]
        def monotonic():
            t = clock[0]
            clock[0] += 5.0
            return t
        assert asv.wait_for_session_gone(
            "sess",
            timeout_sec=3.0,
            runner=lambda cmd: None,
            sleep_fn=lambda s: None,
            now_fn=monotonic,
        ) is False

    def test_resolves_helper_at_call_time(self, asv, monkeypatch) -> None:
        """Monkeypatching ``tmux_session_exists`` must take effect even
        though ``wait_for_session_gone`` was imported at module load."""
        calls = []
        def stub(s, runner=None):
            calls.append(s)
            return False
        monkeypatch.setattr(asv, "tmux_session_exists", stub)
        assert asv.wait_for_session_gone(
            "sess",
            timeout_sec=10.0,
            runner=lambda cmd: None,
            sleep_fn=lambda s: None,
            now_fn=lambda: 0.0,
        ) is True
        assert calls == ["sess"]


# --- Intent reading ---


class TestReadIntent:
    def test_missing_file_defaults_off(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert sup.read_intent() == "OFF"

    @pytest.mark.parametrize("raw,expected", [
        ("OFF\n", "OFF"),
        ("broad", "BROAD"),
        ("Deep", "DEEP"),
        (" DEEP \n", "DEEP"),
        ("garbage", "OFF"),
        ("", "OFF"),
    ])
    def test_normalizes_intent(self, asv, state_dir, fake_repo, raw, expected) -> None:
        (fake_repo / "autonomous_intent.txt").write_text(raw)
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert sup.read_intent() == expected


# --- Rate limit ---


class TestRateLimit:
    def test_empty_state_is_under_limit(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        ok, count = sup.check_rate_limit()
        assert ok is True and count == 0

    def test_record_spawn_increments_count(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup.record_spawn()
        ok, count = sup.check_rate_limit()
        assert ok is True and count == 1

    def test_over_cap_returns_false(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        for _ in range(asv.RATE_LIMIT_MAX_PER_24H):
            sup.record_spawn()
        ok, count = sup.check_rate_limit()
        assert ok is False
        assert count == asv.RATE_LIMIT_MAX_PER_24H

    def test_stale_entries_pruned(self, asv, state_dir, fake_repo) -> None:
        """Entries older than 24h must not count toward the cap."""
        now_container = [time.time()]
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo,
            now_fn=lambda: now_container[0],
        )
        for _ in range(asv.RATE_LIMIT_MAX_PER_24H):
            sup.record_spawn()
        # Fast-forward 25 hours.
        now_container[0] += 25 * 3600
        ok, count = sup.check_rate_limit()
        assert ok is True and count == 0

    def test_corrupt_rate_limit_file_is_tolerated(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        (state_dir / "rate_limit.json").write_text("{not json at all")
        ok, count = sup.check_rate_limit()
        assert ok is True and count == 0

    def test_reset_rate_limit_clears_count_and_logs(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        for _ in range(asv.RATE_LIMIT_MAX_PER_24H):
            sup.record_spawn()
        ok_before, count_before = sup.check_rate_limit()
        assert ok_before is False and count_before == asv.RATE_LIMIT_MAX_PER_24H
        sup.reset_rate_limit()
        ok_after, count_after = sup.check_rate_limit()
        assert ok_after is True and count_after == 0
        assert not (state_dir / "rate_limit.json").exists()
        log_text = (state_dir / "respawn_log.log").read_text()
        assert "rate limit manually reset" in log_text

    def test_reset_rate_limit_when_file_absent_is_noop(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert not (state_dir / "rate_limit.json").exists()
        sup.reset_rate_limit()  # Must not raise.
        ok, count = sup.check_rate_limit()
        assert ok is True and count == 0


# --- Respawn log ---


class TestRespawnLog:
    def test_log_respawn_appends(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup.log_respawn("first")
        sup.log_respawn("second")
        text = (state_dir / "respawn_log.log").read_text()
        assert "first" in text and "second" in text
        # Must be two lines.
        assert len([L for L in text.splitlines() if L.strip()]) == 2


# --- Meta file ---


class TestMetaFile:
    def test_write_then_read(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        meta = {"session_id": "hypergumbo-session-foo", "vendor": "claude-code", "start_utc": "2026-01-01T00:00:00Z"}
        sup.write_meta("hypergumbo-session-foo", meta)
        assert sup.read_meta("hypergumbo-session-foo") == meta

    def test_missing_meta_is_none(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert sup.read_meta("hypergumbo-session-nosuch") is None

    def test_corrupt_meta_is_none(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        (state_dir / "hypergumbo-session-bad.meta.json").write_text("not json")
        assert sup.read_meta("hypergumbo-session-bad") is None


# --- Reserved prefix discipline ---


class TestReservedPrefix:
    def test_only_matching_prefix_returned(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (
                0, "hypergumbo-session-a\nmy-dev\nhypergumbo-session-b\n", "",
            )
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        assert sup.owned_sessions() == ["hypergumbo-session-a", "hypergumbo-session-b"]

    def test_no_sessions_returns_empty(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (1, "", "no server running"),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        assert sup.owned_sessions() == []


# --- Pane-delta tracking ---


class TestPaneDelta:
    def _make_sup(self, asv, state_dir, fake_repo, pane_bytes_seq):
        """Returns a supervisor whose pane-capture returns items from
        ``pane_bytes_seq`` in order."""
        it = iter(pane_bytes_seq)

        def runner(cmd):
            if cmd[:2] == ["tmux", "capture-pane"]:
                try:
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="x" * next(it), stderr="",
                    )
                except StopIteration:
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        now = [1000.0]
        return asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            now_fn=lambda: now[0],
        ), now

    def test_first_observation_treated_as_changed(self, asv, state_dir, fake_repo) -> None:
        sup, _ = self._make_sup(asv, state_dir, fake_repo, [100])
        assert sup.observe_pane("hypergumbo-session-x") is True

    def test_unchanged_second_observation_reports_false(self, asv, state_dir, fake_repo) -> None:
        sup, now = self._make_sup(asv, state_dir, fake_repo, [100, 100])
        sup.observe_pane("hypergumbo-session-x")
        now[0] += 60
        assert sup.observe_pane("hypergumbo-session-x") is False

    def test_changed_bytes_reports_true(self, asv, state_dir, fake_repo) -> None:
        sup, now = self._make_sup(asv, state_dir, fake_repo, [100, 150])
        sup.observe_pane("hypergumbo-session-x")
        now[0] += 60
        assert sup.observe_pane("hypergumbo-session-x") is True

    def test_pane_frozen_for_grows_when_unchanged(self, asv, state_dir, fake_repo) -> None:
        sup, now = self._make_sup(asv, state_dir, fake_repo, [100, 100, 100])
        sup.observe_pane("hypergumbo-session-x")
        now[0] += 900  # 15 min
        sup.observe_pane("hypergumbo-session-x")
        assert sup.pane_frozen_for("hypergumbo-session-x") >= 900

    def test_capture_failure_preserves_prior_observation(self, asv, state_dir, fake_repo) -> None:
        """When a tmux capture-pane call fails, we must keep the prior
        observation rather than nuking the history (which would reset
        the frozen timer)."""
        sup, now = self._make_sup(asv, state_dir, fake_repo, [100])  # second sample errors
        sup.observe_pane("hypergumbo-session-x")
        prior = sup._pane_history["hypergumbo-session-x"].last_change_utc
        now[0] += 60
        sup.observe_pane("hypergumbo-session-x")  # runner returns rc=1
        assert sup._pane_history["hypergumbo-session-x"].last_change_utc == prior


# --- Lock acquisition ---


class TestLock:
    def test_acquire_and_release(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup.acquire_lock()
        assert (state_dir / "supervisor.lock").exists()
        sup.release_lock()

    def test_second_acquire_rejected(self, asv, state_dir, fake_repo) -> None:
        sup1 = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup2 = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        sup1.acquire_lock()
        with pytest.raises(RuntimeError, match="another supervisor"):
            sup2.acquire_lock()
        sup1.release_lock()


# --- Stop sentinel ---


class TestStopSentinel:
    def test_stop_requested_reads_sentinel(self, asv, state_dir, fake_repo) -> None:
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        assert sup.stop_requested() is False
        (state_dir / "supervisor.stop-sentinel").touch()
        assert sup.stop_requested() is True

    def test_run_exits_when_sentinel_present(self, asv, state_dir, fake_repo) -> None:
        """Run loop must exit within one iteration when the sentinel is
        set. Tests the interaction between stop_requested() and run()."""
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo)
        (state_dir / "supervisor.stop-sentinel").touch()
        # Would loop forever if stop_requested didn't short-circuit;
        # the test's timeout (pytest default) plus the immediate-exit
        # contract guard against that.
        sup.run(interval_sec=999999)  # Should return immediately.
        # Sentinel was consumed.
        assert not (state_dir / "supervisor.stop-sentinel").exists()


# --- Spawn fresh ---


class TestSpawnFresh:
    def test_spawn_fresh_writes_meta_and_logs(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            now_fn=lambda: 1700000000.0,
        )
        name = sup.spawn_fresh(vendor="claude-code")
        assert name is not None and name.startswith(asv.RESERVED_PREFIX)
        assert any(c[:2] == ["tmux", "new-session"] for c in runner.calls)
        assert (state_dir / f"{name}.meta.json").exists()
        text = (state_dir / "respawn_log.log").read_text()
        assert f"spawned {name}" in text

    def test_spawn_rate_limited(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        for _ in range(asv.RATE_LIMIT_MAX_PER_24H):
            sup.record_spawn()
        assert sup.spawn_fresh() is None
        # No tmux new-session call.
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)
        # Rate-limit log entry.
        assert "rate-limit" in (state_dir / "respawn_log.log").read_text()

    def test_spawn_unknown_vendor_skips(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        assert sup.spawn_fresh(vendor="neverheardof") is None
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)

    def test_spawn_passes_hypergumbo_respawn_env(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.spawn_fresh()
        new_session_call = next(c for c in runner.calls if c[:2] == ["tmux", "new-session"])
        # -e HYPERGUMBO_RESPAWN=1 should appear in the command.
        assert "HYPERGUMBO_RESPAWN=1" in new_session_call


# --- Replacement sequence (WI-nusor) ---


class TestReplacementSequence:
    def test_graceful_exit_path(self, asv, state_dir, fake_repo, monkeypatch) -> None:
        """CLI exits cleanly within timeout → no hard-kill, no shared
        cleanup invocations."""
        runner = MockRunner()
        # Simulate tmux session disappearing immediately after /quit.
        monkeypatch.setattr(asv, "tmux_session_exists", lambda s, runner=None: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        meta = {"session_id": "hypergumbo-session-old", "vendor": "claude-code"}
        sup.write_meta("hypergumbo-session-old", meta)
        sup.replace_session("hypergumbo-session-old", meta)
        # Must send exit keystroke.
        assert any(c[:2] == ["tmux", "send-keys"] for c in runner.calls)
        # Must NOT hard-kill.
        assert not any(c[:2] == ["tmux", "kill-session"] for c in runner.calls)
        # Must have spawned a new session.
        assert any(c[:2] == ["tmux", "new-session"] for c in runner.calls)

    def test_hard_kill_fallback(self, asv, state_dir, fake_repo, monkeypatch) -> None:
        """tmux session refuses to go away → hard-kill + shared cleanup scripts."""
        runner = MockRunner()
        # Session stays alive throughout.
        monkeypatch.setattr(asv, "tmux_session_exists", lambda s, runner=None: True)
        # Monotonic clock advances past timeout on each call.
        clock = [0.0]
        def monotonic():
            clock[0] += 60
            return clock[0]
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=monotonic,
        )
        meta = {"session_id": "hypergumbo-session-hung", "vendor": "claude-code"}
        sup.write_meta("hypergumbo-session-hung", meta)
        sup.replace_session("hypergumbo-session-hung", meta)
        # Must hard-kill.
        assert any(c[:2] == ["tmux", "kill-session"] for c in runner.calls)
        # Must invoke the shared cleanup scripts.
        kill_script_called = any(
            ".agent/hooks/_shared/kill-transcript-sync.sh" in " ".join(c)
            for c in runner.calls
        )
        rotate_script_called = any(
            ".agent/hooks/_shared/rotate-on-session-end.sh" in " ".join(c)
            for c in runner.calls
        )
        assert kill_script_called, f"kill-transcript-sync.sh not called; calls={runner.calls}"
        assert rotate_script_called, f"rotate-on-session-end.sh not called; calls={runner.calls}"
        # Fallback logged.
        log_text = (state_dir / "respawn_log.log").read_text()
        assert "forced-kill fallback" in log_text

    def test_unknown_vendor_uses_default(self, asv, state_dir, fake_repo, monkeypatch) -> None:
        """replace_session with no meta.vendor must not crash; uses the
        default vendor's exit keystroke."""
        runner = MockRunner()
        monkeypatch.setattr(asv, "tmux_session_exists", lambda s, runner=None: False)
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None, monotonic_fn=lambda: 0.0,
        )
        sup.replace_session("hypergumbo-session-x", meta=None)
        # No crash; default vendor's keystroke sent.
        assert any(c[:2] == ["tmux", "send-keys"] for c in runner.calls)


# --- Poll loop ---


class TestPollLoop:
    def test_intent_off_does_nothing(self, asv, state_dir, fake_repo) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("OFF\n")
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.poll_once()
        # No tmux calls at all.
        assert all(c[0] != "tmux" for c in runner.calls)

    def test_empty_sessions_spawns(self, asv, state_dir, fake_repo) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (1, "", "no server"),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.poll_once()
        assert any(c[:2] == ["tmux", "new-session"] for c in runner.calls)

    def test_attached_session_untouched(self, asv, state_dir, fake_repo) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("BROAD\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (0, "hypergumbo-session-x\n", ""),
            ("tmux", "list-clients", "-t", "hypergumbo-session-x"): (0, "/dev/pts/0 x\n", ""),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.write_meta("hypergumbo-session-x", {"vendor": "claude-code"})
        sup.poll_once()
        # No replacement activity.
        assert not any(c[:2] == ["tmux", "send-keys"] for c in runner.calls)
        assert not any(c[:2] == ["tmux", "new-session"] for c in runner.calls)


# --- Status report ---


class TestStatusReport:
    def test_report_shape(self, asv, state_dir, fake_repo) -> None:
        (fake_repo / "autonomous_intent.txt").write_text("DEEP\n")
        runner = MockRunner({
            ("tmux", "list-sessions", "-F", "#S"): (0, "hypergumbo-session-x\n", ""),
            ("tmux", "list-clients", "-t", "hypergumbo-session-x"): (1, "", ""),
            ("tmux", "capture-pane", "-t", "hypergumbo-session-x", "-p"): (0, "xxx", ""),
        })
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.write_meta("hypergumbo-session-x", {"vendor": "claude-code"})
        report = sup.status_report()
        assert report["intent"] == "DEEP"
        assert report["rate_limit"]["max_per_24h"] == asv.RATE_LIMIT_MAX_PER_24H
        assert len(report["sessions"]) == 1
        s = report["sessions"][0]
        assert s["session"] == "hypergumbo-session-x"
        assert s["clients_attached"] is False
        assert s["pane_bytes"] == 3
        assert s["meta"]["vendor"] == "claude-code"


# --- CLI dispatch (subprocess-level smoke test) ---


class TestCLIDispatch:
    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert "run" in result.stdout and "status" in result.stdout

    def test_stop_creates_sentinel_when_supervisor_live(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        # Plant a lock file whose pid is the test process itself — guaranteed
        # alive for the duration of the assertion.
        (state / "supervisor.lock").write_text(f"{os.getpid()}\n")
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "stop"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        sentinel = state / "supervisor.stop-sentinel"
        assert sentinel.exists()
        assert "stop sentinel written" in result.stdout

    def test_stop_is_noop_when_no_lock_file(self, tmp_path: Path) -> None:
        """Running `stop` with no supervisor ever started must NOT arm the
        stop sentinel — otherwise the next `run` invocation consumes it and
        exits immediately, which is the failure mode this fix addresses."""
        state = tmp_path / "state"
        state.mkdir()
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "stop"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert not (state / "supervisor.stop-sentinel").exists()
        assert "nothing to stop" in result.stdout

    def test_stop_cleans_stale_lock_and_sentinel(self, tmp_path: Path) -> None:
        """A lock file whose pid is dead is stale — delete it, delete any
        leftover stop-sentinel too, and don't arm a fresh one."""
        state = tmp_path / "state"
        state.mkdir()
        # PID 1 is always alive on Linux (init); we need a guaranteed-DEAD
        # pid. 2**31 - 1 is above PID_MAX_LIMIT on every realistic system.
        (state / "supervisor.lock").write_text(f"{2**31 - 1}\n")
        (state / "supervisor.stop-sentinel").touch()
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "stop"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert not (state / "supervisor.lock").exists()
        assert not (state / "supervisor.stop-sentinel").exists()
        assert "nothing to stop" in result.stdout
        assert "cleaned" in result.stdout

    def test_stop_treats_malformed_lock_as_dead(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        (state / "supervisor.lock").write_text("not-a-pid\n")
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "stop"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert not (state / "supervisor.lock").exists()
        assert not (state / "supervisor.stop-sentinel").exists()
        assert "nothing to stop" in result.stdout

    def test_debugging_reset_rate_limit_zeroes_count(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        (state / "rate_limit.json").write_text(
            json.dumps({"spawns": [time.time()] * 8})
        )
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        env["AGENT_SUPERVISOR_REPO_ROOT"] = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "debugging-reset-rate-limit"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert "rate limit cleared" in result.stdout
        assert not (state / "rate_limit.json").exists()

    def test_debugging_reset_rate_limit_noop_when_absent(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(state)
        env["AGENT_SUPERVISOR_REPO_ROOT"] = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "debugging-reset-rate-limit"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        assert "rate limit cleared" in result.stdout

    def test_status_prints_json(self, tmp_path: Path) -> None:
        env = os.environ.copy()
        env["AGENT_SUPERVISOR_STATE_DIR"] = str(tmp_path / "state")
        env["AGENT_SUPERVISOR_REPO_ROOT"] = str(tmp_path / "repo")
        (tmp_path / "repo").mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "status"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "intent" in data
        assert data["intent"] == "OFF"


# --- Pane-readiness wait (vendor-agnostic seed bootstrap) ---


class TestPaneReady:
    """Exercise ``wait_for_pane_ready`` directly with explicit args so the
    module-level ``PANE_READY_MAX_WAIT_SEC=0.0`` autouse monkeypatch
    doesn't short-circuit the logic we're trying to test."""

    def test_stable_content_above_min_bytes_returns_true(self, asv) -> None:
        """Identical capture-pane output across consecutive polls, content
        at or above the byte floor, returns True."""
        content = "x" * 100
        def runner(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=content, stderr="")
        ready = asv.wait_for_pane_ready(
            "sess",
            min_bytes=32,
            quiet_samples=3,
            max_wait_sec=5.0,
            runner=runner,
            sleep_fn=lambda s: None,
            monotonic_fn=lambda: 0.0,  # Constant — deadline never reached while stable.
        )
        assert ready is True

    def test_content_keeps_changing_times_out(self, asv) -> None:
        """Pane content changes on every poll → stability never achieved →
        returns False when the deadline passes."""
        counter = [0]
        def runner(cmd):
            counter[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=f"output-{counter[0]}-" + "x" * 50,  # Always >= min_bytes, always different.
                stderr="",
            )
        clock = [0.0]
        def monotonic():
            t = clock[0]
            clock[0] += 1.0
            return t
        ready = asv.wait_for_pane_ready(
            "sess",
            min_bytes=32,
            quiet_samples=3,
            max_wait_sec=5.0,
            runner=runner,
            sleep_fn=lambda s: None,
            monotonic_fn=monotonic,
        )
        assert ready is False

    def test_below_min_bytes_never_ready(self, asv) -> None:
        """Stable pane content that is below ``min_bytes`` is treated as
        "banner still drawing" rather than "ready" — prevents mistaking
        an empty pane sampled twice for a settled prompt."""
        def runner(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="hi", stderr="")
        clock = [0.0]
        def monotonic():
            t = clock[0]
            clock[0] += 1.0
            return t
        ready = asv.wait_for_pane_ready(
            "sess",
            min_bytes=32,
            quiet_samples=3,
            max_wait_sec=3.0,
            runner=runner,
            sleep_fn=lambda s: None,
            monotonic_fn=monotonic,
        )
        assert ready is False

    def test_capture_failure_treated_as_empty(self, asv) -> None:
        """If ``tmux capture-pane`` returns non-zero, the content is
        treated as empty string (length 0) — never satisfies min_bytes."""
        def runner(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="session not found",
            )
        clock = [0.0]
        def monotonic():
            t = clock[0]
            clock[0] += 1.0
            return t
        ready = asv.wait_for_pane_ready(
            "sess",
            min_bytes=1,  # Even a single byte would be enough, but we get 0.
            quiet_samples=2,
            max_wait_sec=3.0,
            runner=runner,
            sleep_fn=lambda s: None,
            monotonic_fn=monotonic,
        )
        assert ready is False

    def test_supervisor_wrapper_skips_when_disabled(self, asv, state_dir, fake_repo) -> None:
        """``Supervisor._wait_for_pane_ready`` returns False immediately
        when ``PANE_READY_MAX_WAIT_SEC`` is non-positive, without calling
        ``tmux capture-pane``. This is the seam the autouse fixture uses
        to keep existing tests fast."""
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        # Autouse fixture has already set PANE_READY_MAX_WAIT_SEC = 0.0.
        assert sup._wait_for_pane_ready("whatever") is False
        assert not any(c[:2] == ["tmux", "capture-pane"] for c in runner.calls)

    def test_supervisor_wrapper_delegates_when_enabled(
        self, asv, state_dir, fake_repo, monkeypatch,
    ) -> None:
        """When the module constant is non-zero, the wrapper delegates to
        ``wait_for_pane_ready`` using the supervisor's injected clocks
        and runner."""
        monkeypatch.setattr(asv, "PANE_READY_MAX_WAIT_SEC", 5.0)
        content = "y" * 100
        runner = MockRunner({
            ("tmux", "capture-pane", "-t", "sess", "-p"): (0, content, ""),
        })
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            sleep_fn=lambda s: None,
            monotonic_fn=lambda: 0.0,  # Constant → loop only exits via stability.
        )
        assert sup._wait_for_pane_ready("sess") is True
        assert any(c[:2] == ["tmux", "capture-pane"] for c in runner.calls)


# --- Seed-prompt bootstrap on spawn ---


class TestSpawnSeedPrompt:
    def test_spawn_fresh_sends_seed_after_new_session(self, asv, state_dir, fake_repo) -> None:
        """Right after ``tmux new-session``, a ``tmux send-keys`` call
        must deliver the default seed prompt to kick off the first
        model turn."""
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        name = sup.spawn_fresh(vendor="claude-code")
        assert name is not None
        new_idx = next(
            i for i, c in enumerate(runner.calls) if c[:2] == ["tmux", "new-session"]
        )
        send_idx = next(
            (i for i, c in enumerate(runner.calls) if c[:2] == ["tmux", "send-keys"]),
            None,
        )
        assert send_idx is not None, f"no send-keys call after spawn; calls={runner.calls}"
        assert send_idx > new_idx
        seed_call = runner.calls[send_idx]
        assert asv.DEFAULT_SEED_PROMPT in seed_call
        # Target the fresh session specifically, not some other pane.
        assert f"{name}:0" in seed_call

    def test_seed_send_targets_fresh_session_only(self, asv, state_dir, fake_repo) -> None:
        """Multiple spawns in sequence each seed their own pane, not the
        prior one's — guards against a copy-paste bug that reuses the
        same session name."""
        runner = MockRunner()
        sup = asv.Supervisor(
            state_dir=state_dir, repo_root=fake_repo, runner=runner,
            now_fn=lambda: 1700000000.0,
        )
        name_a = sup.spawn_fresh(vendor="claude-code")
        # Force a different timestamp so the second spawn gets a distinct name.
        sup.now_fn = lambda: 1700000001.0
        name_b = sup.spawn_fresh(vendor="claude-code")
        assert name_a is not None and name_b is not None and name_a != name_b
        send_targets = [
            c[c.index("-t") + 1]
            for c in runner.calls
            if c[:2] == ["tmux", "send-keys"] and "-t" in c
        ]
        assert f"{name_a}:0" in send_targets
        assert f"{name_b}:0" in send_targets

    def test_seed_logged_in_respawn_log(self, asv, state_dir, fake_repo) -> None:
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        name = sup.spawn_fresh(vendor="claude-code")
        log_text = (state_dir / "respawn_log.log").read_text()
        assert f"seeded {name}" in log_text
        assert asv.DEFAULT_SEED_PROMPT in log_text

    def test_auto_paused_spawn_does_not_seed(self, asv, state_dir, fake_repo) -> None:
        """When ``spawn_fresh`` is blocked by the auto-pause sentinel it
        returns None without calling tmux; there must be no stray
        send-keys either (otherwise we'd send keystrokes to a session
        that was never created)."""
        runner = MockRunner()
        sup = asv.Supervisor(state_dir=state_dir, repo_root=fake_repo, runner=runner)
        sup.set_auto_paused("test")
        assert sup.spawn_fresh() is None
        assert not any(c[:2] == ["tmux", "send-keys"] for c in runner.calls)


# --- tmux_send_line helper ---


class TestTmuxSendLine:
    def test_send_line_targets_pane_zero_with_enter(self, asv) -> None:
        calls = []
        def runner(cmd):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        asv.tmux_send_line("my-session", "hello world", runner=runner)
        assert calls == [["tmux", "send-keys", "-t", "my-session:0", "hello world", "Enter"]]
