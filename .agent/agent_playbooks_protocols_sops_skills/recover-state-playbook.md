## Post-Compaction State Recovery
When context has been compressed, you may have lost awareness of in-progress work.

**Recover state:**
```bash
cat ~/hypergumbo_lab_notebook/guidance_log/last_stop_check.json 2>/dev/null
```
This file records: current branch (should be `dev` after a clean merge), last PR number/state, pending TODOs (hard/soft), free-text notes about what to do next, and the active bakeoff session. Use it to orient yourself before starting new work.

If the JSON contains a `guidance_file` field, read that file for the most recent stop hook guidance (TODO details, circuit breaker status).

If the JSON contains `bakeoff_session_path` and `bakeoff_session_type`, these identify the most recent bakeoff session. Use the session path to resume work on the correct session (e.g., `./scripts/bakeoff-broad status --workdir <path>` or `./scripts/bakeoff-deep status --workdir <path>`).

**Keep notes fresh:** Update `last_stop_check.json` notes after key milestones, not just at reflection time. This ensures context survives compaction:
- After a PR merge: record what was merged and what's next
- After a bakeoff completes: record findings and next steps
- After tracker item status changes: record what was resolved and why
- After hitting an obstacle: record what's blocked and alternative approaches

```bash
# Update notes (works whether or not the file exists — seeds from {} if missing)
jq -n --arg n "Merged PR #NNNN (feat X). Next: WI-yyyy." \
  --argjson existing "$(cat ~/hypergumbo_lab_notebook/guidance_log/last_stop_check.json 2>/dev/null || echo '{}')" \
  '$existing + {notes: $n}' \
  > /tmp/lsc.json && mv /tmp/lsc.json ~/hypergumbo_lab_notebook/guidance_log/last_stop_check.json
```

Also check for pending work items:
```bash
./scripts/tracker ready
```

**smart-test reminder:** Always use `pytest` (aliased to `smart-test`) for running tests. NEVER use `python -m pytest`, `.venv/bin/pytest`, or `command pytest` — these bypass smart-test and produce ~4000 lines of raw output instead of the compact ~20-line summary.
