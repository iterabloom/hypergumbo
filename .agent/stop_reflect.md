# Stop Reflection Protocol

Before stopping, complete this checklist:

## 1. Current State
- [ ] What CRITICAL/HIGH signals remain from last bakeoff run?
- [ ] What was the last change made?

## 2. Invariant Check
For each remaining signal (for the last change made), state the violated invariant:
> "In this system, X must always be true because Y depends on it."
Also check the file (`cat .agent/invariant-ledger.md 2>/dev/null | grep -E -A5 'Status: (❌( UNFIXED)?|UNFIXED|PARTIALLY ADDRESSED)' || true`) and make sure it is up-to-date.

## 3. Structural vs Workaround
For the last change made:
- [ ] Does it bypass a problematic code path, or fix/remove that path?
- [ ] If bypass: what is the root cause, and when will it be fixed?

## 4. Scope Expansion
- [ ] Same language, different construct?
- [ ] Different language, same pattern?
- [ ] Different pipeline stage?

## 5. Decision
- [ ] If root cause unfixed (even partially) and analogous issues might exist: **DO NOT STOP** — fix the root cause or investigate further
- [ ] If root cause fixed or truly isolated: document in invariant ledger (`.agent/invariant-ledger.md`), then take a step back and think about the best thing to do from a big-picture software quality perspective. Strongly consider activating or reactivating the bakeoff loop using `scripts/bakeoff`, `scripts/bakeoff-reflect`, and `scripts/hypergumbo_diag.py` (as detailed in Parts 2 & 3 of `docs/governance-case-critiques.md`).

## 6. Artifact Analysis
- [ ] Have existing bakeoff artifacts been analyzed (not just generated)?
- [ ] Analysis tools available:
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
- [ ] Add new analysis scripts to `analysis_lib/` as needed (follow naming: `NN_short_name.py`)
- [ ] Prefer mining existing data over running more bakeoffs — large repos take significant time
- [ ] Look for patterns: gaps in detection, edge types, cross-language linking, concept coverage

## 7. Commit Check
- [ ] Run `git status` — are there uncommitted changes?
- [ ] If yes: commit with sign-off (`git commit -s`) and run `./scripts/auto-pr` to push
- [ ] If `auto-pr` is blocked (PR_PENDING exists or remote unavailable), note the state and continue
