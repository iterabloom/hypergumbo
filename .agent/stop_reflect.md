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
```
**When in doubt, use `todo_hard`** — the circuit breaker prevents death spirals, so err on the side of taking things seriously.

## 5. Decision
- If root cause is unfixed (even partially) and analogous issues might exist: **DO NOT STOP** — fix the root cause or investigate further
- If root cause is fixed or truly isolated: update the tracker item to `done`, then decide your next action.

**Next action selection (in priority order):**
1. **Implementation-ready insights:** Check the lab notebook (`ls -t ~/hypergumbo_lab_notebook/*.md | head -5`) for recent entries that identify concrete code changes. If found, implement them (TDD).
2. **DEEP/BROAD priority queue:** Check `AGENTS.md` for the next item in the current mode's priority queue.
3. **Bakeoff or artifact analysis:** Only if 1-2 yielded nothing actionable.

**When you write `notes` in Section 8:** Be specific and implementation-oriented. Not "investigate brake feel" but "add **service-access point patterns** to the maintenance checklist (the shop's checklist file) — **adjusters/grease fittings/test ports on non-sealed, externally accessible assemblies** should be automatically recognized as **ROUTINE_SERVICE entry points**." The notes field is injected into the cooldown prompt, so future-you will act on exactly what you write.

## 6. Artifact Analysis (If Needed)
Use analysis when you need data to inform an implementation decision, not as a destination in itself. Every analysis session should end with a concrete "what to implement" conclusion written into either the lab notebook or `last_stop_check.json` notes.

Analysis toolkit (see `~/hypergumbo_lab_notebook/analysis_lib/README.md` for current inventory):
- `./scripts/bakeoff-reflect <path> --cycle N` — qualitative assessment
- `scripts/hypergumbo_diag.py` — comprehensive diagnostic report
- `scripts/analyze-artifacts` — catalog, summary, routes, concepts, edges, gaps
- `~/hypergumbo_lab_notebook/analysis_lib/` — 18+ reusable analysis scripts (run `ls ~/hypergumbo_lab_notebook/analysis_lib/[0-9]*.py` for current list)

If analysis reveals concerns, investigate the root cause and implement the fix before stopping.

## 7. Design Quality Meta-Reflection
Consider the last few changes made:

- **Hardcoded vs YAML:** Is there anything hardcoded in Python that would be more appropriate as a YAML config? Framework patterns should live in `src/hypergumbo/frameworks/*.yaml`. Language conventions should be declarative where possible. If you added a new pattern check, could it be expressed as YAML instead?

- **Invariant Consolidation:** Are there any invariants in the tracker that should be combined into a single, more principled/general invariant? Look for invariants that share a root cause or could be expressed as a single more abstract principle. Use `scripts/tracker list --kind invariant` to review.

## 8. Commit and Timestamp
- Run `git status` — are there uncommitted changes?
- If yes: commit with sign-off (`git commit -s`) and run `./scripts/auto-pr` to push
- If `auto-pr` is blocked (PR_PENDING exists or remote unavailable), note the state and continue
- Record reflection completion with recovery state:
  ```bash
  python3 -c "
import json, subprocess, datetime, pathlib

branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()

# Determine last PR state
pr_pending = pathlib.Path('.git/PR_PENDING')
if pr_pending.exists():
    last_pr = int(pr_pending.read_text().strip().split()[-1]) if pr_pending.read_text().strip() else 0
    last_pr_state = 'pending'
else:
    last_pr = 0       # Agent fills in the PR number that just merged, or 0
    last_pr_state = 'none'

# Count pending work items from structured tracker
def tracker_count(flag):
    try:
        return int(subprocess.check_output(
            ['scripts/tracker', 'count-todos', flag], text=True
        ).strip())
    except Exception:
        return 0

pending_hard_todos = tracker_count('--hard')
pending_soft_todos = tracker_count('--soft')

# Preserve guidance_file from previous stop hook run if present
existing_state = {}
state_path = pathlib.Path('.agent/last_stop_check.json')
if state_path.exists():
    try:
        existing_state = json.loads(state_path.read_text())
    except Exception:
        pass

state = {
    'last_completed_utc': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'branch': branch,
    'last_pr': last_pr,
    'last_pr_state': last_pr_state,
    'pending_hard_todos': pending_hard_todos,
    'pending_soft_todos': pending_soft_todos,
    'notes': '',  # Agent fills in: specific implementation task(s) for cooldown to act on. Be concrete: 'add X pattern to Y file' not 'investigate X'
}
if 'guidance_file' in existing_state:
    state['guidance_file'] = existing_state['guidance_file']
pathlib.Path('.agent/last_stop_check.json').write_text(json.dumps(state, indent=2) + '\n')
  "
  ```
  **Important:** Before running, update `last_pr` and `notes` in the script with actual values. The `notes` field is critical — it gets injected into the cooldown prompt so the next cycle knows what to implement. Write specific, actionable implementation tasks, not vague observations.
