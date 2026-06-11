<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Closure-Evidence Discipline Playbook (WI-dafun)

**Rule, in one line:** when you resolve (`satisfied` / `done`) a tracker item
whose *statement* describes runtime/user-facing **behavior**, the resolution
rationale MUST cite **behavioral evidence** — never a proxy metric alone.

This guard exists because a green internal-validator count was once accepted as
proof that user-facing behavior was fixed, and it wasn't.

## The failure this prevents

`INV-nufob` was false-satisfied on 2026-06-01: it was closed on a *proxy
metric* ("self-analysis at 0 `verdict_enum` violations") plus an *adjacency
claim* (credited the sibling `INV-mofih` `ClaimVerdict.inconclusive` fix), while
its actual behavioral invariant — the `verify-claims --taint-sources /
--taint-sinks / --taint-sanitizers` loader must **error**, not silently
confirm, on bad input — stayed **violated** until the v6.0.0 step-1
re-verification caught it (fixed in PR #4152). A sibling-closure audit over all
resolved items found the proxy-closure *pattern* recurs (a small number of pure
proxy closures; `INV-nufob` was the only one that hid a live regression). The
root cause: nothing required that a behavioral-invariant closure cite
behavioral evidence. This is the same false-all-clear pathology as `META-fimas`
/ `validator-false-all-clear`.

## What counts

**Behavioral statement** (the item is in scope for this rule): the statement
describes a runtime/user-facing observable — a CLI exit code, stdout/stderr,
a "silently accepts/rejects/confirms" claim, an error/traceback, or a named
subcommand's behavior.

**Behavioral evidence** (REQUIRED in the closure rationale): at least one of —
- a **live repro**: the command you ran plus the observed result (exit code /
  `rc=2`, the stderr line, the stdout shape), ideally in a fenced code block;
- a **production-path test**: a test that exercises the real user-facing code
  path (not just an internal helper) and the assertion it now makes;
- a **before→after ratio** on the real artifact (`4170/5886 → 0/5886`);
- a documented **static code audit** of the production path when a runtime
  repro is genuinely impossible — stated as such, with the file:line trace.

**Proxy metric** (may SUPPLEMENT, never SUBSTITUTE): an internal validator
count or class — "N violations", "validator clean", "self-analysis at 0",
"`spec_validator` reports 0", "`verdict_enum` validator enforces the branch" —
or an **adjacency claim** ("closed by `<sibling>`'s fix"). These describe the
machinery, not the behavior. A green validator can be green because the
validator doesn't check the thing that's broken (the `INV-nufob` case exactly).

## The procedure at closure time

1. **Re-run the item's OWN filed repro.** Most behavioral items were filed with
   a concrete repro (a command + the bad observed behavior). Run it again. If
   the bad behavior is gone, paste the new observed result into the `--note`.
   If it isn't, the item is not done.
2. **Write the rationale with behavioral evidence first**, proxy metrics second
   (if at all). `scripts/tracker update WI-foo --status satisfied --note "Repro:
   <cmd> now exits rc=2 with '<stderr>' (was: silently confirmed). PR #NNNN.
   [optional] validator also clean."`
3. **Never** close a behavioral item on `validator clean` / `closed by sibling`
   alone.

## Enforcement: ADVISORY, forward-only

Per the human design decision (2026-06-09), this is **advisory**, not a hard
gate:

- **No hard pre-commit/CI gate** on `tracker update --status satisfied`. The
  detector is heuristic — its first pass over-flagged (11 candidates) because it
  required a `hypergumbo` prefix before subcommand names; the corrected pass
  (bare subcommand names) lands near the true set. A hard gate would block
  legitimate closures on false positives.
- **Forward-only.** The rule and the script apply to *future* closures. Do NOT
  trigger a retroactive behavioral re-probe of the whole resolved corpus — the
  proxy-closure-shaped subset was already audited (a few pure-proxy closures,
  all benign except `INV-nufob`, which is fixed).

## The soft-check: `scripts/audit-closure-evidence`

An on-demand script that re-runs the detector over the **committed** tracker
corpus (read via `scripts/tracker --json list`, never the raw `.ops` logs) and
reports candidates for human-reviewed re-probe. It **never mutates** anything.
Fold it into the periodic tracker-hygiene/dedup sweep.

A candidate is a **resolved** item with a **behavioral statement** that has an
agent discussion entry citing a **proxy signal** but **no behavioral evidence
in that same entry**. Expect false positives (e.g. an item whose deliverable
*was* a validator legitimately cites that validator) — human review is required
before any status change. The detector regexes (proxy / behavioral /
behavioral-statement signal lists) live at the top of the script and are the
single source of truth; the corrected, no-`hypergumbo`-prefix subcommand
matching is the calibrated version — keep it.

The detector LOGIC is pinned by `tests/test_audit_closure_evidence.py` (the
`INV-nufob` proxy pattern flags; a repro-citing closure does not; a
non-behavioral statement does not; only agent entries on resolved items count).
