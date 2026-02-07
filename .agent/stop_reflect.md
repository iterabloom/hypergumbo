# Stop Reflection Protocol

Before stopping, work through each section. Do not skip sections.

## 1. Current State
State what CRITICAL/HIGH signals remain from the last bakeoff run.
State what the last change was and why it was made.

## 2. Invariant Check
For each remaining signal, state the violated invariant:
> "In this system, X must always be true because Y depends on it."

Check the ledger:
```bash
cat .agent/invariant-ledger.md 2>/dev/null | grep -E '^- \*\*Status:\*\* (UNFIXED|PARTIALLY ADDRESSED|TBD|[0-9]+%)' | grep -v '100%' || true
```
This catches:
- Regular invariants: UNFIXED, PARTIALLY ADDRESSED, TBD
- Meta-invariants: Any percentage below 100%

If items show, read the full ledger for context and Notes fields.

Also check for pending generalizations:
```bash
grep -c '^\s*- \*\*TODO\*\*' .agent/invariant-ledger.md 2>/dev/null || echo 0
```
If any `**TODO**` items exist, they are first-class work items — address them or explicitly defer with justification.

## 3. Structural vs Workaround
For the last change made:
- Does it bypass a problematic code path, or fix/remove that path?
- If bypass: what is the root cause, and when will it be fixed?

## 4. Scope Expansion
Check for structural analogues of the last fix:
- Same language, different construct?
- Different language, same pattern?
- Different pipeline stage?

If analogues exist, write `**TODO**` entries to the invariant ledger under the relevant invariant's `Pending Generalizations` field immediately. Use the format:
```
- **TODO** Target: description (est. complexity, value: relative-to-original)
```
These entries are enforced by the stop hook — they become candidate next actions.

## 5. Decision
- If root cause is unfixed (even partially) and analogous issues might exist: **DO NOT STOP** — fix the root cause or investigate further
- If root cause is fixed or truly isolated: document in invariant ledger, then consider the best next action from a big-picture software quality perspective. Strongly consider activating the bakeoff loop or mining existing artifacts.

## 6. Artifact Analysis
Prefer mining existing artifacts over running new bakeoffs — large repos take significant time and there are likely enough artifacts already.

Analysis toolkit (use any combination, all are peers):
- `./scripts/bakeoff-reflect <path> --cycle N` — qualitative "needs work" vs "doing something special" insights; asks diverse open-ended questions that change each run
- `scripts/hypergumbo_diag.py` — comprehensive diagnostic report
- `scripts/analyze-artifacts` — catalog, summary, routes, concepts, edges, gaps
- `~/hypergumbo_lab_notebook/analysis_lib/` — reusable analysis scripts:
  - `01_quality_overview.py` — edge density, call coverage, concepts
  - `02_edge_resolution.py` — cross-file vs same-file vs stdlib
  - `03_language_comparison.py` — compare analyzers across languages
  - `04_entrypoint_analysis.py` — entrypoint quality
  - `05_potential_issues.py` — detect common problems
  - `06_signature_quality.py` — function signature completeness
  - `07_complexity_metrics.py` — cyclomatic complexity distribution
- Add new analysis scripts to `analysis_lib/` as needed (naming: `NN_short_name.py`)

Look for patterns: gaps in detection, edge types, cross-language linking, concept coverage.
If analysis reveals concerns, investigate the root cause before stopping.

## 7. Design Quality Meta-Reflection
Consider the last few changes made:

- **Hardcoded vs YAML:** Is there anything hardcoded in Python that would be more appropriate as a YAML config? Framework patterns should live in `src/hypergumbo/frameworks/*.yaml`. Language conventions should be declarative where possible. If you added a new pattern check, could it be expressed as YAML instead?

- **Invariant Consolidation:** Are there any invariants in the ledger that should be combined into a single, more principled/general invariant? Look for invariants that share a root cause or could be expressed as a single more abstract principle.

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

# Count pending work items from invariant ledger
ledger = pathlib.Path('.agent/invariant-ledger.md')
ledger_text = ledger.read_text() if ledger.exists() else ''
import re
pending_todos = len(re.findall(r'^\s*- \*\*TODO\*\*', ledger_text, re.MULTILINE))
unfixed = len(re.findall(r'^\s*- \*\*Status:\*\* (UNFIXED|PARTIALLY ADDRESSED|TBD)', ledger_text, re.MULTILINE))

state = {
    'last_completed_utc': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'branch': branch,
    'last_pr': last_pr,
    'last_pr_state': last_pr_state,
    'pending_todos': pending_todos,
    'unfixed_invariants': unfixed,
    'notes': ''  # Agent fills in: 1-2 sentences about what to do next
}
pathlib.Path('.agent/last_stop_check.json').write_text(json.dumps(state, indent=2) + '\n')
  "
  ```
  **Important:** Before running, update `last_pr` and `notes` in the script with actual values (the PR number you just merged and a short description of next steps).
