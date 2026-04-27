<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
- **Structural Fix Protocol:** When fixing a bakeoff signal or bug:
  1. **Assume structural:** The bug likely affects multiple languages/frameworks/stages
  2. **Name the invariant:** "In this system, X must always be true because Y depends on it"
  3. **Scope expansion:** Check same-language-different-construct, different-language-same-pattern, different-pipeline-stage
  4. **Distinguish fix from workaround:** Does your change bypass a problematic code path, or fix/remove it?
  5. **If workaround:** Create a tracker item (`scripts/tracker add --kind invariant ...`) with status `violated`, then fix the root cause
(Background: ADR-0008)
- **Scope Expansion Commitment Protocol:** When a structural fix identifies analogous issues in other languages, constructs, or pipeline stages:
  1. **Create tracker items immediately** using `scripts/tracker add`:
     - `violated` — invariant violations, anything structural; use for items of kind `invariant` or `meta_invariant`. Investigate deeply, assume structural.
     - `todo_hard` — defects, *potential* invariant violations, anything potentially structural. **When in doubt, use this.** The circuit breaker prevents death spirals, so err on the side of taking things too seriously. Use for items of kind `work_item`. Investigate deeply, assume structural.
     - `todo_soft` — clearly non-defect backlog (CI config, test coverage, nice-to-haves, scope expansion work from the Commitment Protocol). For `work_item`-kind items. Address freely.
     - `needs_human_review` — governance proposals, architectural questions, or anything requiring human judgment. Does NOT block stopping. For any kind of item. Do not work on these (other than to update their data using `scripts/tracker`) — they await human triage.
  2. **Hook enforcement:** `todo_hard`, `todo_soft`, `violated`, and `pending_validation` items block the stop hook (queried via `scripts/tracker count-todos`) and surface via `scripts/tracker ready`. Circuit breaker: 5 firings with no file changes in sentinel dirs → approve.
  3. **Act or deprioritize:** Either fix the item or set it to lowest priority (P4) with a justification note.
  4. **Track to completion:** When done, update the item's status with a PR reference. For invariants: `satisfied` (confirmed with evidence), `pending_validation` (fix merged, awaiting bakeoff). For work items: `done`. Do NOT use `holding` — it is deprecated.

  *See also "When NOT to file a new tracker item" below — the default to-file bias has three exceptions worth recognizing.*

### When NOT to file a new tracker item

The Scope Expansion Commitment Protocol pushes hard toward filing items for analogous issues across languages / constructs / pipeline stages. That bias is correct *as a default*, but it has three failure modes you should recognize and route around. (We discovered these failure modes after an agent filed a fresh INV item for what turned out to be a regression of an already-tracked invariant, fragmenting the discussion thread the human was actively reading.)

1. **Existing-coverage check.** Before filing, run `scripts/tracker tags` to see the current tag vocabulary, then `scripts/tracker list --tag <tag>` or `scripts/tracker list --kind <invariant|work_item>` and spot-check titles. If an existing item already covers the surface, prefer `tracker discuss <ID>` with a regression note rather than a new item. New items fragment the discussion thread the human is reading.

2. **Conversation-in-progress check.** If the human is actively engaged in the conversation that surfaced the concern, ask before filing. The default response shape "I'll file a tracker item for that" can be exactly wrong when the human's intent was "let's discuss this and decide together what to do." Cheap fix: surface the proposed item title plus a one-line rationale and ask "file this as `<ID-class>`?" before invoking `tracker add`.

3. **Property-of-existing-invariant check.** A new failure mode that is structurally a *property* of an already-tracked invariant should be filed as a `tracker discuss` regression note on the parent invariant, not as a new INV. The aggregate-of-properties view of an invariant is what makes it useful for cross-session pattern recognition; splitting the regression into a sibling item destroys that aggregation.

The default remains "when in doubt, use `todo_hard`." This addition only narrows the default for three specific shapes that empirically produce cargo-cult filings.
