<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Agent Supervisor — Operator Guide

`scripts/agent-supervisor` is a long-running daemon that monitors tmux sessions running hypergumbo-aware agent CLIs (Claude Code, Codex CLI, Cursor, Gemini CLI) and replaces a stuck session with a fresh one when your project-level intent says autonomous work is desired but the current session has stopped making progress.

This guide covers the operator workflow. For the design rationale, see tracker item `WI-razub` and the related vendor-contract documentation in [`AGENTS.md` → Vendor Parity for Respawn](../AGENTS.md).

## What the supervisor solves

The stop-hook circuit breaker (5 consecutive no-progress hashes) correctly detects a stagnating autonomous session — but it does so by permitting the session to terminate, which gives a stuck agent a one-way exit out of long-running work. The supervisor closes that loop: when a session tripss the breaker (or crashes, or exits cleanly), the supervisor spawns a fresh CLI with a clean context, seeded with a generic "familiarize yourself with this repo" prompt, so forward-march resumes automatically.

The supervisor's authoritative signal is tmux pane-byte delta over a rolling 15-minute window — "is the pane actually scrolling?" — NOT any file the agent itself writes. Per-session heartbeat files exist (touched by every vendor's per-turn hook) but are telemetry only; they surface in `status` output but are never consulted for spawn/replace decisions.

## Prerequisites

- `tmux` installed on the workstation.
- One or more vendor CLIs installed and on `$PATH`: `claude`, `codex`, `cursor`, `gemini`.
- `python3` (standard library only — no extra dependencies).
- You have run `./scripts/dev-install` in this repo so the hooks and scripts are wired up.

> **Verification status note.** The exit-keystroke for Claude Code is verified. For Codex / Cursor / Gemini the supervisor's table is best-effort and marked `FIXME WI-batob` in both `scripts/agent-supervisor::VENDOR_TABLE` and the AGENTS.md parity table. Before relying on the supervisor to respawn those vendors in production, do the one-time verification step documented in AGENTS.md.

## First-time setup

Two commands per workstation. Run them once and the supervisor owns the lifecycle from then on.

```bash
./scripts/loop-toggle DEEP            # writes autonomous_intent.txt = DEEP
                                      # (also writes AUTONOMOUS_MODE.txt = DEEP
                                      #  for today's session — preserves old UX)

./scripts/agent-supervisor run &      # starts the daemon in background
```

The supervisor creates `~/hypergumbo_lab_notebook/agent-supervisor/` if it doesn't exist. Override the default with `AGENT_SUPERVISOR_STATE_DIR=<path>` if you need the state elsewhere.

Substitute `BROAD` for `DEEP` if you want breadth / linker-coverage work instead of feature-quality work — see [AGENTS.md § Mode Selection](../AGENTS.md).

## Normal operation

Once the supervisor is running, it polls every 60 seconds (tunable via `--interval N`). On each tick it:

1. Reads `autonomous_intent.txt`. If OFF, does nothing.
2. Enumerates tmux sessions whose name starts with `hypergumbo-session-` (reserved prefix — human-managed tmux sessions are never touched).
3. For each such session, checks: is a tmux client attached? is the recorded CLI PID alive? has the pane scrolled in the last 15 minutes?
4. Acts: if no session exists, spawn one. If a session is attached, do nothing (human is watching). If the CLI is dead OR the pane has been frozen for ≥ 15 minutes, run the replacement sequence.

### Watching a live session

The supervisor launches sessions in detached mode. To observe one:

```bash
./scripts/agent-supervisor status      # lists live sessions + pane bytes + heartbeat ages
tmux attach -t hypergumbo-session-<UTC-timestamp>
```

Detach without killing the CLI with `Ctrl-B D`.

**Important:** while you are attached, the supervisor will NOT replace the session even if the pane freezes — an attached client blocks replacement, by design. Detach when you're done watching so the watchdog can do its job.

### Pausing the loop

```bash
./scripts/loop-toggle OFF              # flips intent to OFF (and today's session mode, too)
```

The supervisor continues running but its decision matrix short-circuits on OFF: no spawns, no replacements. Any live CLI finishes its current work and idles. Resume with another `loop-toggle DEEP` / `BROAD`.

Prefer the narrow form if you want to temporarily disable autonomous mode on *just* the currently-running CLI without flipping project intent:

```bash
./scripts/loop-toggle --set-session-mode OFF     # session only; intent stays on
```

### Shutting down for the day

```bash
./scripts/agent-supervisor stop        # writes supervisor.stop-sentinel
```

The running daemon consumes the sentinel on its next poll tick (≤ 60 s) and exits cleanly. Your live CLIs keep running until you close them; the supervisor just stops respawning. Re-arm with another `agent-supervisor run &` whenever you come back.

## Recovering from auto-pause (WI-mujuk meta-circuit-breaker)

If the supervisor detects **5 consecutive no-progress failures on the same chain** — meaning it spawned a session, that session died without rendering anything useful, it spawned another, same result, and this happened five times in a row — it writes `supervisor.auto-paused` into its state dir and stops spawning entirely. Running `agent-supervisor status` will show `"auto_paused": true`.

This is a load-bearing signal, not a rate limit: a persistent bug (broken playbook, corrupt state file, bad env var, session-start hook crashing) would otherwise burn through the 24h rate-limit budget every day forever, invisible from the outside. Auto-pause converts that silent loop into a loud "investigate me" state.

Before clearing the pause, find out what went wrong:

```bash
./scripts/agent-supervisor status | jq '.sessions[] | {session, chain_length, consecutive_no_progress, pane_bytes}'
tail -20 ~/hypergumbo_lab_notebook/agent-supervisor/respawn_log.log
```

The log's last few lines show the chain tail: which sessions died, whether each was classified no-progress (pane ≤ 512 bytes at kill) or progress, and the `AUTO-PAUSED: N consecutive no-progress failures...` entry. Common root causes:

- Session-start hook crashing immediately → CLI dies before printing anything.
- `HYPERGUMBO_RESPAWN=1` branch of `session_start_logic.sh` failing → `loop-toggle` call errors.
- `autonomous_intent.txt` pointing at a mode whose bakeoff directory is missing.
- Vendor CLI not installed / no longer on `$PATH`.

Once you've fixed the underlying issue, resume:

```bash
./scripts/agent-supervisor resume
```

This removes the sentinel and the next poll tick will spawn a fresh chain (`chain_length=1`, `consecutive_no_progress=0`). The respawn log records the operator-driven resume so audits can tell it apart from a cold start.

## `status` output

```bash
./scripts/agent-supervisor status | jq .
```

Returns a JSON object with:

- `intent` — current value of `autonomous_intent.txt`.
- `rate_limit` — rolling 24h spawn count, the cap (default 8), and whether a spawn is currently allowed.
- `sessions[]` — one entry per hypergumbo-prefixed tmux session, with `meta` (the stored session-id / CLI pid / vendor / start UTC / `replaces` / `chain_length` / `consecutive_no_progress`), `clients_attached`, `pane_bytes` (raw scrollback size in bytes), `heartbeat_age_sec` (seconds since the per-turn hooks last touched the heartbeat file), plus top-level `chain_length`, `consecutive_no_progress`, and `replaces` fields lifted out of `meta` for convenience.
- `stop_requested` — true if a stop sentinel is in flight.
- `auto_paused` — true when the WI-mujuk kill switch has fired; clear with `agent-supervisor resume`.
- `kill_switch_threshold` — the value of `CONSECUTIVE_NO_PROGRESS_KILL_SWITCH` (default 5) so tooling can compare against `consecutive_no_progress` without hardcoding the constant.

Use `pane_bytes` + `heartbeat_age_sec` together to debug "is this session actually working?" — if pane bytes haven't grown but the heartbeat is fresh, the CLI is stuck in a tool that's not emitting output. If both are stale, the CLI itself is frozen. And use `consecutive_no_progress` + `chain_length` to tell "this is a fresh chain trying to start" (small, near zero) apart from "this chain is in trouble and close to auto-pause" (approaching `kill_switch_threshold`).

## Edge cases

- **Two supervisors for the same project.** The second `agent-supervisor run` invocation fails `fcntl.flock` acquisition on `supervisor.lock` and exits with "another supervisor is already running". This is the enforced single-instance invariant; don't work around it.
- **You want to run a vendor CLI by hand.** Either launch it in a tmux session whose name does NOT start with `hypergumbo-session-` (the supervisor will ignore it entirely), or `loop-toggle OFF` first and it won't get touched.
- **Rate-limited.** If the supervisor has spawned 8 sessions in the last 24 hours (default soft cap), the next spawn is skipped with a log entry in `respawn_log.log` instead of proceeding. Fix the underlying problem — pounding on the spawn button would indicate a deeper issue.
- **Auto-paused (WI-mujuk).** After 5 consecutive no-progress failures on the same chain, the supervisor writes `supervisor.auto-paused` and stops spawning. See "Recovering from auto-pause" above.
- **Supervisor crashes.** Nothing gets auto-spawned until you restart it with `agent-supervisor run &`. The daemon is not self-restarting by design.
- **Tmux is not installed.** The `run_subprocess` seam returns rc=127 for every tmux call, so `status` works and reports zero sessions. The `run` loop no-ops each tick. Install tmux to unstick.
- **CLI refuses graceful exit.** The supervisor polls `kill -0 <cli_pid>` for 30 seconds after sending the vendor exit keystroke. If the CLI is still alive, it falls back to `tmux kill-session` + direct invocation of `kill-transcript-sync.sh` / `rotate-on-session-end.sh` (the per-session cleanup scripts are already idempotent). An entry appears in `respawn_log.log` as `forced-kill fallback for session <name>`.
- **Human attached during a chain close to auto-pause.** The attached-client check takes precedence over the kill switch — an attached session is never replaced, so its chain can't grow, so auto-pause can't fire on it. This is deliberate: a human watching is a human diagnosing. Detach to let the chain progress to its natural outcome.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `agent-supervisor run` fails with "another supervisor is already running" | flock still held by a supervisor PID | `agent-supervisor status` to confirm, then `ps -fp <pid>` on the PID in `supervisor.lock`; if that PID is dead, remove the lock file and retry |
| Live session not getting replaced despite being stuck | You're attached to it, or the pane has scrolled within 15 min | Detach (`Ctrl-B D`); or wait out the 15-minute frozen window |
| `respawn_log.log` shows repeated "rate-limit reached" | 8 spawns in 24h — usually indicates a loop somewhere upstream | Read the log tail + `agent_notes.json` for a pattern; don't just raise the cap |
| Fresh CLI launches but doesn't enable autonomous mode | `autonomous_intent.txt` is OFF or missing | `loop-toggle --set-intent DEEP` (narrow-write, doesn't touch the current session) |
| Fresh CLI launches but the session-start hook doesn't inject the seed prompt | Vendor's hook file missing or unwired | Verify `.agent/hooks/<vendor>/session-start.sh` exists and sources `_shared/session_start_logic.sh` |
| `status` shows `auto_paused: true` | Kill switch fired after 5 consecutive no-progress failures | See "Recovering from auto-pause" above; investigate log tail, fix root cause, run `agent-supervisor resume` |

## State directory layout

`~/hypergumbo_lab_notebook/agent-supervisor/` (override with `AGENT_SUPERVISOR_STATE_DIR`):

- `supervisor.lock` — flock + pid-file for single-instance enforcement.
- `supervisor.stop-sentinel` — present when a stop is requested; consumed on the next tick.
- `supervisor.auto-paused` — present when the WI-mujuk kill switch has fired; contents are a human-readable reason line; cleared by `agent-supervisor resume`.
- `<session>.meta.json` — written on spawn: session_id, cli_pid, vendor, project_dir, tmux session name, start_utc, `replaces`, `chain_length`, `consecutive_no_progress`.
- `<session>.heartbeat` — touched by the per-turn hooks (telemetry only; never a spawn/replace input).
- `respawn_log.log` — append-only audit of every spawn / replace / rate-limit / auto-pause event.
- `rate_limit.json` — rolling 24h spawn timestamps.

## What the supervisor does NOT do

- **Decide mode.** The human still picks BROAD vs DEEP via `loop-toggle`. The supervisor only mirrors project intent into each spawned session.
- **Self-heal tmux.** If tmux is down, the supervisor waits silently for it to come back.
- **Restart after crash.** No systemd / cron wiring by default — you launch `agent-supervisor run &` manually (or add it to your shell rc).
- **Persist pane history across restarts.** Pane-byte observations are in-memory only; after a supervisor restart, the first tick per session seeds a new observation and the 15-minute frozen clock restarts.
- **Consult the heartbeat.** Heartbeats are for your debugging / retrospective metrics, not the spawn/replace decision. See WI-sipov.

## Deferred follow-ups

These are noted on their tracker items and would extend the supervisor's reach without changing today's contract:

- **Stop-hook + long-running-command heartbeats.** Today the heartbeat is only touched by per-turn hooks. Wrappers like `auto-pr` / `bakeoff-*` / `smart-test` don't yet have a supervisor-exported session_id env var to key their heartbeat touches. (Tracked as a follow-up on WI-sipov.)
- **Codex / Cursor / Gemini exit keystrokes.** Marked `FIXME WI-batob` in the supervisor's `VENDOR_TABLE` and in the AGENTS.md parity table. Claude Code is verified; the others need a one-time "start the CLI in a throwaway tmux, send the keystroke, confirm exit within 30s" verification.

## Related reading

- [AGENTS.md § Vendor Parity for Respawn](../AGENTS.md) — the per-vendor contract table (hook paths, exit keystrokes, CLI invocations).
- [AGENTS.md § Premature Stopping Prevention](../AGENTS.md) — the autonomous-mode framework the supervisor plugs into.
- `scripts/agent-supervisor` — inline design notes in the script's docstring.
- `scripts/loop-toggle --help` — the intent/mode split (`--set-intent` / `--set-session-mode`).
- Tracker item `WI-razub-duluf-nobun-rulit-dapam-jipal-dafud-nahob` — the full design discussion and resolution notes.
