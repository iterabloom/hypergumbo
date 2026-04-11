<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Stop Reflection Protocol

Before stopping, work through each section. Do not skip sections.

## 1. Current State
State what CRITICAL/HIGH signals remain from the last bakeoff run.
State what the last change was and why it was made.

## 2. Invariant Check
For each remaining signal, state the violated invariant:
> "In this system, X must always be true because Y depends on it."

Check the structured tracker for blocking items:
```bash
scripts/tracker count-todos --hard   # todo_hard: investigate deeply, assume structural
scripts/tracker count-todos --soft   # todo_soft: backlog, address or defer freely
scripts/tracker ready | head -10     # actionable items sorted by priority
```

If items show, read details with `scripts/tracker show <ID>`.

Both `todo_hard` and `todo_soft` items block stopping (subject to circuit breaker).

## 3. Structural vs Workaround
For the last change made:
- Does it bypass a problematic code path, or fix/remove that path?
- If bypass: what is the root cause, and when will it be fixed?

## 4. Scope Expansion
Check for structural analogues of the last fix:
- Same language, different construct?
- Different language, same pattern?
- Different pipeline stage?

If analogues exist, create tracker items immediately:
```bash
# Invariant violations, defects, anything potentially structural:
scripts/tracker add invariant --title "..." --status todo_hard --priority N

# Clearly non-defect backlog (CI config, nice-to-haves):
scripts/tracker add work_item --title "..." --status todo_soft --priority N

# Governance proposals, architectural questions, needs human judgment:
scripts/tracker add work_item --title "..." --status needs_human_review --priority N
```
**When in doubt, use `todo_hard`** — the circuit breaker prevents death spirals, so err on the side of taking things too seriously. Use `needs_human_review` for items that genuinely require human decision-making rather than just human review of agent work.

## 5. Decision
- If root cause is unfixed (even partially) and analogous issues might exist: **DO NOT STOP** — fix the root cause or investigate further
- If root cause is fixed or truly isolated: update the tracker item to `done`, then decide your next action.

**Next action selection (in priority order):**
1. **Implementation-ready insights:** Check the lab notebook (`ls -t ~/hypergumbo_lab_notebook/*.md | head -10`) for recent entries that identify concrete code changes. If found, add them to the tracker. Include a reminder to use red-green-refactor/TDD.
2. **DEEP/BROAD priority queue:** Check `AGENTS.md` for the next item in the current mode's priority queue.
3. **Bakeoff or artifact analysis:** Only if 1-2 yielded nothing actionable.

**When you write `notes` in Section 8:** Be specific and implementation-oriented. Not "investigate brake feel" but "add **service-access point patterns** to the maintenance checklist (the shop's checklist file) — **adjusters/grease fittings/test ports on non-sealed, externally accessible assemblies** should be automatically recognized as **ROUTINE_SERVICE entry points**." The notes field is injected into the cooldown prompt, so future-you will act on exactly what you write.

## 6. Artifact Analysis (If Needed)
Use analysis when you need data to inform an implementation decision, not as a destination in itself. Every analysis session should end with a concrete "what to implement" conclusion written into either the lab notebook or the `agent_notes.json` notes field (via `scripts/agent-notes --set` / `--append`).

Analysis toolkit (see `~/hypergumbo_lab_notebook/analysis_lib/README.md` for additional inventory):
- `./scripts/bakeoff-broad-reflect` — BROAD mode: structured LLM-driven parse correctness assessment
- `./scripts/bakeoff-deep-reflect` — DEEP mode: LLM-driven feature usefulness assessment
- `scripts/hypergumbo_diag.py` — comprehensive diagnostic report
- `scripts/analyze-artifacts` — catalog, summary, routes, concepts, edges, gaps
- `~/hypergumbo_lab_notebook/analysis_lib/` — 18+ reusable analysis scripts (run `ls ~/hypergumbo_lab_notebook/analysis_lib/[0-9]*.py` for current list)

If analysis reveals concerns, check the tracker for any preexisting relevant entries to amend, or add a new item to the tracker. Reference the lab notebook as the authoritative analysis, but try to make the tracker entry complete.

## 7. Design Quality Meta-Reflection
Consider the last few changes made:

- **Hardcoded vs YAML:** Is there anything hardcoded in Python that would be more appropriate as a YAML config? Framework patterns should live in `src/hypergumbo/frameworks/*.yaml`. Language conventions should be declarative where possible. If you added a new pattern check, could it be expressed as YAML instead?

- **Invariant Consolidation:** Are there any invariants in the tracker that should be combined into a single, more principled/general invariant? Look for invariants that share a root cause or could be expressed as a single more abstract principle. Use `scripts/tracker list --kind invariant` to review.

## 8. Commit and Record Notes
- Run `git status` — are there uncommitted changes?
- If yes: commit with sign-off (`git commit -s`) and run `./scripts/auto-pr` to push
- If `auto-pr` is blocked (PR_PENDING exists or remote unavailable), note the state and continue
- Record reflection completion by setting the notes field. This is the only
  thing the agent writes at reflection time — `last_completed_utc`,
  `current_branch`, `guidance_file`, and the bakeoff fields are maintained
  automatically by the stop hook (INV-jofaf facet 2). Agents MUST NOT write
  to `stop_hook_state.json` directly:
  ```bash
  scripts/agent-notes --set "Session summary: merged PR #NNNN (feat X). Next: specific implementation task for WI-yyyy — add pattern P to file F. Not 'investigate Y'."
  ```
  **Important:** the notes field is critical — it gets injected into the
  cooldown prompt so the next cycle knows what to implement. Write specific,
  actionable implementation tasks, not vague observations. Use
  `scripts/agent-notes --append` if you want to add to existing notes
  instead of replacing them.
