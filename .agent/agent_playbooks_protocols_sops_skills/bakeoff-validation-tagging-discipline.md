<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Bakeoff Validation Tagging Discipline

## When to use

At **PR merge time**, whenever the PR description or its tracker item's discussion contains a quantitative bakeoff-improvement claim. Also relevant when configuring the stop-hook automation knobs or reasoning about the reflect-aggregate auto-strip mechanism.

This playbook is **the trigger half** of the validation lifecycle. The drain half — what to do when the queue is full enough to process — lives in `process-validation-queue-with-bakeoffs-and-uat.md`. Two distinct cadences (per-PR vs per-session), two distinct audiences (any PR-merging agent vs an agent committed to a backlog session).

## The rule

Any PR whose description or tracker discussion contains a quantitative bakeoff-improvement claim must receive the `awaits_bakeoff_validation` tag on its tracker item at merge time. The tag stays until a later DEEP-mode bakeoff cycle reproduces the claimed metric movement.

Lifecycle:

- **`moved`** (a later DEEP cycle confirms the claimed movement): strip the tag via a resolution discussion that links the cohort where it was validated.
- **`no_move`** (the cycle does not reproduce the movement): file a regression sub-item parent-linked to the original. **The regression sub-item does NOT inherit the `awaits_bakeoff_validation` tag** — regressions are their own work, not new entries on the validation queue.
- **`inconclusive`**: keep the tag, add a discussion noting what would unblock (more cohort coverage, a different modality, etc.).

## What counts as a quantitative bakeoff claim

Apply the tag when any of these verb-forms appears in the PR description or tracker discussion:

- "should improve X by N%"
- "expected FP reduction of N"
- "N dead → alive" or "N alive → dead"
- "NN% reduction" / "NN% improvement"
- "below threshold X" (any numeric threshold)
- "newly-consumed concept" (asserting a concept flips from inert → live)
- raw candidate-count deltas attributed to the change

## What does NOT count

- Qualitative claims ("handles the case", "covers the pattern")
- Coverage / test-count deltas
- Performance micro-benchmarks unrelated to the bakeoff corpus

## Authoritative running list

```bash
scripts/tracker list --tag awaits_bakeoff_validation
```

This is the **single source of truth** for pending bakeoff validations. It supersedes any earlier hand-maintained pattern. If a list elsewhere disagrees with this command's output, the command wins.

## Stop-hook surfacing

The stop hook surfaces the tag automatically: when the count of tag-bearing items in a blocking status reaches `threshold` **AND** the most recent DEEP bakeoff cycle's `state.json` is older than `stale_cycle_hours`, an `## AWAITS_BAKEOFF_VALIDATION BACKLOG` section is appended to the active guidance file pointing at `./scripts/bakeoff-deep cycle`.

Both knobs live under `stop_hook.awaits_bakeoff_validation_nudge` in `.agent/tracker/config.yaml`:

| Knob | Default | Meaning |
|---|---|---|
| `threshold` | `5` | Minimum tag-bearing count (in blocking status) to fire the nudge |
| `stale_cycle_hours` | `72` | DEEP cycle is "stale enough" to warrant a new run after this many hours |

Worker: `.agent/hooks/_shared/awaits_bakeoff_nudge.py`.

## Integration with `bakeoff-deep-reflect aggregate`

At aggregation time the reflect pass cross-references active `awaits_bakeoff_validation` items against the cohort's diagnostic output, injecting a per-claim question into the reflect prompt with three possible verdicts: `moved` / `no_move` / `inconclusive`.

- On `moved` the tag is auto-stripped with evidence linking the aggregation cohort.
- On `no_move` a regression sub-item is created (parent-linked, untagged — see lifecycle above).
- On `inconclusive` the tag stays and the aggregation note records what would unblock.

The aggregation glue is separate implementation work. **The discipline rule is in force independently of that tooling** — if the auto-injection is offline or hasn't shipped for a particular cohort, manual tagging at PR merge and manual stripping post-cycle are still the governing behavior.

## Drain procedure

When the queue is large enough to warrant a dedicated session (~15+ items, or a human surfaces it), follow the seven-phase routine plus directed UAT-bakeoff path in `process-validation-queue-with-bakeoffs-and-uat.md`. That's a separate playbook with separate trigger conditions; this one's job is finished once the tag is correctly applied.
