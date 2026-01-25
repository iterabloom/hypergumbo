# Stop Reflection Protocol

Before stopping, complete this checklist:

## 1. Current State
- [ ] What CRITICAL/HIGH signals remain from last bakeoff run?
- [ ] What was the last change made?

## 2. Invariant Check
For each remaining signal, state the violated invariant:
> "In this system, X must always be true because Y depends on it."

## 3. Structural vs Workaround
For the last change made:
- [ ] Does it bypass a problematic code path, or fix/remove that path?
- [ ] If bypass: what is the root cause, and when will it be fixed?

## 4. Scope Expansion
- [ ] Same language, different construct?
- [ ] Different language, same pattern?
- [ ] Different pipeline stage?

## 5. Decision
- [ ] If root cause unfixed and analogous issues exist: **DO NOT STOP** — fix the root cause
- [ ] If root cause fixed or truly isolated: document in invariant ledger, then stop

## Current Root Causes (Known)
- INV-001: JS/TS arrow function attribution (Kotlin/Scala lambdas still vulnerable)
- INV-002: `symbol_ref` gate partially addressed with name-based fallback
