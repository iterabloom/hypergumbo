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
- [ ] **IMPORTANT:** Prefer mining existing artifacts over running new bakeoffs — large repos take significant time and there are likely enough artifacts already
- [ ] Run qualitative reflection first:
  ```bash
  ./scripts/bakeoff-reflect /tmp/bakeoff_session/out/cohort-001/iter-001 --cycle N
  ```
  - This generates "needs work" vs "doing something special" insights
  - Run it REPEATEDLY with different cohorts — value is in the variation
  - It surfaces concerns (NO_CALL_EDGES, LOW_RESOLUTION, LOW_CROSS_FILE)
  - It highlights strengths (STRONG_CROSS_FILE, RICH_EDGE_TYPES, HIGH_RESOLUTION)
  - It asks open-ended questions that change each run to explore the problem space
- [ ] For deeper quantitative analysis, use:
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
- [ ] Look for patterns: gaps in detection, edge types, cross-language linking, concept coverage
- [ ] If analysis reveals concerns, investigate the root cause before stopping

## 7. Design Quality Meta-Reflection
Consider the last few changes made:

- [ ] **Hardcoded vs YAML:** Is there anything hardcoded in Python that would be more appropriate as a YAML config?
  - Framework patterns should live in `src/hypergumbo/frameworks/*.yaml`, not hardcoded in analyzers
  - Language conventions (main functions, entrypoints) should be declarative where possible
  - If you added a new pattern check, could it be expressed as YAML instead?

- [ ] **Invariant Consolidation:** Are there any invariants in the ledger that should be combined into a single, more principled/general invariant?
  - Look for invariants that share a common root cause
  - Look for invariants that could be expressed as a single more abstract principle
  - Example: Multiple "missing edge" invariants might generalize to "every metadata reference must become a traversable edge"

## 8. Commit Check
- [ ] Run `git status` — are there uncommitted changes?
- [ ] If yes: commit with sign-off (`git commit -s`) and run `./scripts/auto-pr` to push
- [ ] If `auto-pr` is blocked (PR_PENDING exists or remote unavailable), note the state and continue
