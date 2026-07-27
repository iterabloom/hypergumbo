<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Docstring-staleness audit playbook

When the agent suspects module docstrings have drifted from the code
beneath them — typically after a release, after a large architectural
shift (e.g., a new ADR closure), or when running hypergumbo on itself
and seeing inconsistencies between what docstrings claim and what the
survey reports — this playbook codifies a four-step audit that
produces a triaged candidate list and fixes the unambiguous cases via
a single docs-only PR.

## When to invoke

Trigger this audit when at least one of:

- A recent ADR closure renamed concepts, registries, or axis-bearing
  fields (e.g., ADR-0027 / ADR-0028 endpoint_shape closures, the
  ADR-0023 §6 fold). Docstrings citing the old vocabulary are the
  highest-yield targets.
- A schema-version bump (e.g., SCHEMA_VERSION 0.7 → 0.8) added a new
  IR sibling field. Producer-side docstrings often don't update.
- A pass count changed (two-pass → three-pass; analyzer collapsed to
  single-pass). Module docstrings frequently describe pipeline
  position and drift first.
- An entire family of subcommands or APIs was consolidated.
- A periodic check — e.g., monthly, or every N releases.

**When NOT to run:** mid-feature with a dirty tree, while CI is
pending, or while auto-pr is in flight (the audit produces a
multi-file PR; mixing it with other work muddies the diff).

## Four-step methodology

### Step 1 — mechanical grep passes (`scripts/check-docstring-drift`)

The script implements three sub-checks:

```bash
scripts/check-docstring-drift                 # co-change scan (default)
scripts/check-docstring-drift --tracker-refs  # WI/INV/BUG status cross-ref
scripts/check-docstring-drift --phase-markers # "not yet", "Slice B", etc.
scripts/check-docstring-drift --all           # all three
scripts/check-docstring-drift --json          # machine-readable
```

What each sub-check produces:

- **Co-change scan.** For every `.py` file under `packages/*/src/`,
  AST-parse to find the module docstring extent (`node.lineno..end_lineno`
  — explicitly NOT line 1, which is the SPDX header on every file
  and would conflate the SPDX mass-edit with docstring edits). `git
  blame` both regions; flag files where the body has been touched
  within the last `--window` days (default 120) AND the docstring is
  at least `--min-delta` days older (default 90).

- **Tracker-ID cross-ref.** Extract `WI-` / `INV-` / `BUG-` IDs from
  docstrings + comments, query each via `scripts/tracker --json show`,
  flag references to closed (`done` / `satisfied` / `wont_do`) IDs
  that the surrounding context frames as still-pending ("deferred",
  "not yet", "will land", "TODO").

- **Phase-marker grep.** Plain regex over docstrings + comments for
  "Two-pass", "Phase 2b", "Slice B", "not yet implemented", "deferred
  to", "arrives in", "will ship", "TODO", "FIXME". Highest signal
  when intersected with closed tracker IDs from the previous check.

### Step 2 — git-blame co-change rank (the script's default mode)

This is the same as step 1's co-change check but worth calling out
separately: the ranked output is the candidate pool for semantic
review. Two known measurement artifacts to watch for:

- **SPDX-header floor.** If you start the blame range at line 1
  instead of the docstring's actual start line, every file appears
  to have its docstring "last touched" on the date of the SPDX
  enforcement commit. The script handles this correctly; reproduce
  this if rolling a one-off scan.

- **Package-reorg ceiling.** Files moved during a monorepo
  reorganization without `git log --follow` will all show the same
  "creation" date as their reorg commit. This bounds the maximum
  detectable docstring age in the moved scope; the signal "docstring
  not touched since reorg" is still meaningful but reads younger
  than truth.

### Step 3 — parallel sub-agent semantic review

For each file in the candidate pool, spawn a read-only sub-agent
(`general-purpose` — the `Explore` agent only reads excerpts and
will miss content past its read window). Prompt template:

> Staleness audit. Read `<path>`. Compare its top-of-file module
> docstring (the first triple-quoted block) against the current code
> below it. For each substantive claim in the docstring, check
> whether the code still supports it. Flag specifically:
> - Named exports/classes/functions in the docstring that don't
>   exist in the code
> - Behavior/algorithm/ordering descriptions the code no longer
>   matches
> - Lists (supported languages, edge types, ADR numbers, schema
>   versions, frameworks) that have drifted
> - "How it works" or pipeline-position claims contradicted by
>   current code
> - References to renamed/removed things
>
> Quote contradictions. If matches, say "matches" and stop —
> don't pad. Under 200 words. Do not edit.

Launch in parallel (single message with multiple Agent tool uses).
~28 agents × ~2 min each ≈ 1-2 min wall time.

### Step 4 — severity triage + PR batching

Categorize each finding into four severities. The categories drive
PR-batching strategy:

| Severity | Meaning | Example |
|----------|---------|---------|
| **A** | Wrong API / subcommand / CLI reference | "Run `hypergumbo install-foo`" when the command no longer exists |
| **B** | Factually wrong claim about current code | "Two-pass" when code is three-pass; claims feature X is extracted when code explicitly skips it |
| **C** | Silent omission of substantial new functionality | docstring lists 5 sections but code emits 13; mentions only the old API surface |
| **D** | Minor numeric / cosmetic drift | "65+ files" when count is ~127 |

Bundle A+B+D into a single `docs(comments)` PR — they're all small
text edits that *remove* a false claim or fix a number. They're
mechanical, reviewable per-line, and don't require understanding
new functionality.

Bundle C separately as `docs(comments)` C-rewrites — these need
*new prose* describing functionality that wasn't there before, so
each fix takes more thought and per-file scrutiny.

Watch for **false positives from the audit prompts themselves**:
if the sub-agent prompt mentions a recent consolidation, agents
will sometimes over-eagerly flag any related reference. Verify
each finding against the actual codebase before bundling
(particularly: check the CLI registers for current vs deprecated
subcommands).

## Operational notes

- **Don't audit a dirty tree.** Same anti-pattern as the
  fundamental-concept audit cadence. Defer until the working tree
  is clean.
- **Regenerate `docs/ARCHITECTURE.md` before pushing.** The
  `verify-generated` CI gate accepts SHAs within the last 15
  commits (`RECENT_COMMIT_WINDOW`), but a docs PR that doesn't
  touch ARCHITECTURE.md alongside the docstring edits will still
  pass — until enough other PRs land and push the recorded SHA out
  of the window. Proactive regen is one extra `git add`.
- **`auto-pr` self-merge is fine here.** Docs-only PRs aren't
  governance changes (the playbook file itself isn't either —
  only AGENTS.md and `.agent/hooks/**` references to it are).

## Cadence guidance

V1 cadence: invoke on demand at the moments listed above. If we
discover that drift accumulates predictably (e.g., always within
~3 weeks of a major ADR closure), promote to a scheduled hook or a
periodic CI job. Until then, on-demand keeps human attention on
real signal.

## Reference: the 2026-05-15 calibration audit

The thresholds `--window=120` and `--min-delta=90` were calibrated
against an audit on hypergumbo itself on 2026-05-15. That audit
flagged 28 files; semantic review caught 21 real-drift cases
(75% true-positive rate at those thresholds). Findings were
batched into PRs #3749 (A+B+D), #3751 (verify-generated 5→15
window widening, discovered as a side-effect), and #3752 (C). The
notebook entry at `~/<repo>_lab_notebook/stratum6_staleness_audit_05152026.md`
records the full step-by-step.
