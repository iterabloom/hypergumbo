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
