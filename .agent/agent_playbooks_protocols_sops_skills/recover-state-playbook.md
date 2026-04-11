<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Post-Compaction State Recovery
When context has been compressed, you may have lost awareness of in-progress work.

**Recover state** (INV-jofaf facet 2 split: hook-owned state and agent-owned notes live in separate files, each with exactly one writer):
```bash
cat ~/hypergumbo_lab_notebook/guidance_log/stop_hook_state.json 2>/dev/null
cat ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json 2>/dev/null
```

The two files together record: hook-maintained fields (current_branch, last_completed_utc, guidance_file, bakeoff_convergence, bakeoff_session_path, bakeoff_session_type) in stop_hook_state.json, and the agent-authored free-text notes field in agent_notes.json. Use both to orient yourself before starting new work.

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
