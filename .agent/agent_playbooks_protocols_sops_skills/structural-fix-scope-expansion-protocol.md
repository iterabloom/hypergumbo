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
  2. **Hook enforcement:** `todo_hard`, `todo_soft`, and `violated` items block the stop hook (queried via `scripts/tracker count-todos`) and surface via `scripts/tracker ready`. Circuit breaker: 5 firings with no file changes in sentinel dirs → approve.
  3. **Act or deprioritize:** Either fix the item or set it to lowest priority (P4) with a justification note.
  4. **Track to completion:** When done, update the item's status to `done`/`holding`/etc with a PR reference.
