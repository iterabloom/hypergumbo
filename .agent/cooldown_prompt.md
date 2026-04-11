<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Cooldown Mode — Implementation & Continuous Work

## 1. Process Retrospective (Brief)
- What worked well in the last cycle? What wasted time?
- Write any governance improvement ideas to `~/hypergumbo_lab_notebook/` tagged `[GOVERNANCE-IDEA]`

## 2. Implement Next Actions
Below you will find `LAST REFLECTION NOTES` (from `agent_notes.json`) and `IS THE BAKEOFF STATUS CONVERGED? WHAT TO DO IF NOT`. Use these ideas/notes to decide what to do next.

**If notes contain implementation tasks**:
- Check the tracker for any preexisting relevant entries to reopen and amend, or add a new item to the tracker. If you don't already see one, include a reminder to follow TDD: write failing test, make it pass, refactor, PR.

**If notes are empty or vague:**
- Check the lab notebook (`~/hypergumbo_lab_notebook/`) for recent entries with implementation-ready insights that can be used to update the tracker.
- Check the DEEP/BROAD priority queues in `AGENTS.md` for the next work item.

## 3. Lab Notebook Mining
If the above didn't yield implementation work, mine the lab notebook:
- `ls -t ~/hypergumbo_lab_notebook/*.md | head -10` — find recent entries
- Look for hypotheses, bug reports, or feature gaps that suggest concrete code changes
- Governance ideas tagged `[GOVERNANCE-IDEA]` should be added to the tracker with status `needs_human_review`

## 4. Analysis & Tooling
- Improve analysis scripts in `~/hypergumbo_lab_notebook/analysis_lib/` (see README there for inventory)
- Run analysis on existing artifacts to generate new implementation-ready insights
- Record findings in the lab notebook with explicit "what code change would fix this" annotations

