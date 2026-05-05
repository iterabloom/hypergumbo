<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Post-Compaction State Recovery
When context has been compressed, you may have lost awareness of in-progress work.

**Recover state** (INV-jofaf facet 2 split: hook-owned state and agent-owned notes live in separate files, each with exactly one writer):
```bash
cat ~/hypergumbo_lab_notebook/guidance_log/stop_hook_state.json 2>/dev/null
cat ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json 2>/dev/null
```

The two files together record: hook-maintained fields (current_branch, last_completed_utc, guidance_file, bakeoff_convergence, bakeoff_session_path, bakeoff_session_type) in stop_hook_state.json, and the agent-authored free-text notes field in agent_notes.json. Use both to orient yourself before starting new work.

**Maintained-field list for `stop_hook_state.json`** (WI-joriv write discipline). These are the ONLY keys the stop hook will preserve on every write:

| Field | Writer | Meaning |
| --- | --- | --- |
| `guidance_file` | stop_logic.sh Path 1 | Pointer to the most recent stop-hook guidance markdown file. |
| `bakeoff_convergence` | stop_logic.sh | One-line `CONVERGED …` or `NEEDS_WORK …` summary computed from the latest bakeoff `state.json`. |
| `bakeoff_session_path` | stop_logic.sh | Absolute path to the latest bakeoff session directory. |
| `bakeoff_session_type` | stop_logic.sh | `broad` or `deep`. |
| `current_branch` | stop_logic.sh | The git branch the hook saw when it fired. |
| `last_completed_utc` | stop_logic.sh Path 3 | Timestamp set at the end of a full reflection (>= 30 min after the prior one). Used to gate the cooldown→reflection transition. |

**Any key NOT in this list is silently dropped on the next write.** This is by design — it prevents zombie fields from forgotten migrations or ad-hoc writers (such as the five stale keys `last_pr`, `last_pr_num`, `last_pr_state`, `pending_hard_todos`, `pending_soft_todos` cleaned up on 2026-04-18). If you need to add a new field, edit both the `jq` extract form in `.agent/hooks/_shared/stop_logic.sh` AND this table in the same PR. Don't try to tack on an `. + {new_field: $v}` shortcut — the write-discipline filter will drop it on the very next hook fire.

If the stop_hook_state.json contains a `guidance_file` field, read that file for the most recent stop hook guidance (TODO details, circuit breaker status).

If stop_hook_state.json contains `bakeoff_session_path` and `bakeoff_session_type`, these identify the most recent bakeoff session. Use the session path to resume work on the correct session (e.g., `./scripts/bakeoff-broad status --workdir <path>` or `./scripts/bakeoff-deep status --workdir <path>`).

**Keep notes fresh** (post-INV-jofaf-facet-2): update agent_notes.json via the dedicated tool, which physically cannot touch hook-owned fields. After key milestones:
- After a PR merge: record what was merged and what's next
- After a bakeoff completes: record findings and next steps
- After tracker item status changes: record what was resolved and why
- After hitting an obstacle: record what's blocked and alternative approaches

```bash
# Replace notes
scripts/agent-notes --set "Merged PR #NNNN (feat X). Next: WI-yyyy."

# Append to existing notes on a new line
scripts/agent-notes --append "Follow-up observation after the merge."

# Print current notes
scripts/agent-notes --show

# Clear notes (after they are no longer relevant)
scripts/agent-notes --clear
```

### Session-end agent-notes refresh (write side of the recovery loop)

The bullet list above gives the rule for *during* a session. This sub-section names the symmetric obligation at *session end* — the asymmetric absence of which was the root cause filed as WI-borur (write-side gap surfaced in session_retrospective_04262026_1911.md Finding 7 and session_retrospective_04262026_1736.md Finding 3).

Before signing off — i.e., when you have completed all in-flight work, no todos remain, and no auto-pr is pending — append a one-paragraph summary to `agent_notes.json` capturing what the next session needs to know:

```bash
scripts/agent-notes --append "<one or two sentences>"
```

What to include (in priority order):

1. **Open invariant violations or P1+ defects you discovered.** Reference the tracker ID. Example: `"WI-nutin filed P1 — TRACKER_SYNC_PENDING marker leaks on SIGKILL; needs flock(2) refactor."`
2. **Status changes on previously-tracked invariants.** Example: `"INV-rahib re-opened from satisfied → needs_human_review after PR #3392/#3393 regression."`
3. **Cross-cutting context the tracker doesn't capture cleanly.** Example: `"Bakeoff workdir is now deep-20260425-210357; prior pointer in stop_hook_state.json was stale by 17 days."`
4. **Any ad-hoc shell state the next session would otherwise re-derive.** Example: `"TRACKER_SYNC_PENDING removed manually at 05:52; root cause filed as WI-nutin."`

Keep it under ~5 lines. The notes file is a working hand-off, not a log; verbose entries dilute signal. The retro file (lab notebook) is where detailed analysis goes. Treat this step as part of "stopping" — the *during*-session bullet list above stays useful, but a fresh notes write at sign-off is what makes the next session's `cat agent_notes.json` actually informative rather than n-hours-stale.

### Fresh-session read trigger (the symmetric read-side hook)

The session-end write rule above has a counterpart at the **other end** of the recovery loop: the session-start hook reads the same file. When `_append_agent_notes_status` (in `.agent/hooks/_shared/session_start_logic.sh`) sees a non-empty `agent_notes.json`, it appends a one-line prompt to `SESSION_START_MESSAGE` naming both ages and asking the agent to ask the user about loading the notes via `./scripts/agent-notes --show`:

> agent_notes.json was last updated **26m ago**, last session ended **2h ago**. Ask the user whether to load the prior session's handoff via `./scripts/agent-notes --show` before starting work.

Two timestamps are reported because they answer two different questions: the **notes age** tells the user whether the handoff is fresh enough to be relevant; the **last-session age** tells the user how stale "last" actually is (a notes file might be 26m old because the agent appended near the end of a long session that ended 2h ago — the gap matters).

The hook **does not** dump notes content into the system reminder unprompted — that would inject potentially-stale handoff text into every fresh session whether the user wants it or not. Asking first preserves the user's option to skip a notes file that's no longer relevant (e.g., they've already read it offline, or the prior session's plan got superseded).

The prompt fires when:
- `jq` is available on `PATH` (the helper needs it to parse the JSON safely);
- The notes file exists at `$HOME/<repo_name>_lab_notebook/guidance_log/agent_notes.json`; and
- The `notes` field is non-empty after stripping whitespace (so an `--append`-with-empty-string corner case doesn't fire spurious prompts).

It's appended on top of the autonomous-mode prompt in Cases 1 (OFF), 2 (stale PID), and 4 (mode active); skipped in Case 3 (another live session owns the lock — adding text there would interrupt that session). Tests live at `tests/test_session_start_agent_notes.py`.

### Doctrine note: write-side and read-side both required

WI-borur originally surfaced as the *write*-side gap (no agent appended at session end, so the next session's recovery had nothing to read). Closing that gap mechanically turned the *read*-side into the new gap (the agent now writes, but a fresh session has no automatic trigger to read). The two halves only close the recovery loop together — either alone is just one writer or one reader talking to a void.

DO NOT write to stop_hook_state.json directly. That file is hook-owned — last_completed_utc, guidance_file, and bakeoff fields are maintained by stop_logic.sh automatically. Writing to it manually would reintroduce the facet-1 failure mode where agent-edited timestamps drift from reality.

Also check for pending work items:
```bash
./scripts/tracker ready
```

**smart-test reminder:** Always use `pytest` (aliased to `smart-test`) for running tests. NEVER use `python -m pytest`, `.venv/bin/pytest`, or `command pytest` — these bypass smart-test and produce ~4000 lines of raw output instead of the compact ~20-line summary.
