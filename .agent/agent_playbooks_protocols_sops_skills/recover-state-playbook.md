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

DO NOT write to stop_hook_state.json directly. That file is hook-owned — last_completed_utc, guidance_file, and bakeoff fields are maintained by stop_logic.sh automatically. Writing to it manually would reintroduce the facet-1 failure mode where agent-edited timestamps drift from reality.

Also check for pending work items:
```bash
./scripts/tracker ready
```

**smart-test reminder:** Always use `pytest` (aliased to `smart-test`) for running tests. NEVER use `python -m pytest`, `.venv/bin/pytest`, or `command pytest` — these bypass smart-test and produce ~4000 lines of raw output instead of the compact ~20-line summary.
