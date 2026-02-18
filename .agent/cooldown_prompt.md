# Cooldown Mode — Implementation & Continuous Work

Reflection was completed recently. Instead of re-running the full checklist, **act on what the last reflection identified.**

## 1. Implement Next Actions
The stop hook injects `LAST REFLECTION NOTES` below (from `last_stop_check.json`). These are concrete next actions from the most recent reflection cycle.

**If notes contain implementation tasks** (e.g., "add Go library-export patterns", "fix entrypoint fallback"):
- Start immediately. Follow TDD: write failing test, make it pass, refactor, PR.
- This is the highest priority. Do not do more analysis if there's implementation work to do.

**If notes are empty or vague:**
- Check the tracker for ready items: `scripts/tracker ready` (sorted by priority).
- Check the lab notebook (`~/hypergumbo_lab_notebook/`) for recent entries with implementation-ready insights.
- Check the DEEP/BROAD priority queues in `AGENTS.md` for the next work item.
- Check the tracker for hard TODOs: `scripts/tracker list --status todo_hard`.

## 2. Lab Notebook Mining
If Section 1 didn't yield implementation work, mine the lab notebook:
- `ls -t ~/hypergumbo_lab_notebook/*.md | head -5` — find recent entries
- Look for hypotheses, bug reports, or feature gaps that suggest concrete code changes
- Governance ideas tagged `[GOVERNANCE-IDEA]` can be implemented if they don't touch gated files, or can be proposed as PRs if they do touch gated files

## 3. Analysis & Tooling (Only If Nothing to Implement)
Only resort to analysis if Sections 1-2 produced no implementation work:
- Improve analysis scripts in `~/hypergumbo_lab_notebook/analysis_lib/` (see README there for inventory)
- Run analysis on existing artifacts to generate new implementation-ready insights
- Record findings in the lab notebook with explicit "what code change would fix this" annotations

## 4. Process Retrospective (Brief)
Spend at most 2-3 minutes:
- What worked well in the last cycle? What wasted time?
- Write any governance improvement ideas to `~/hypergumbo_lab_notebook/` tagged `[GOVERNANCE-IDEA]`

Continue working — do not stop.
