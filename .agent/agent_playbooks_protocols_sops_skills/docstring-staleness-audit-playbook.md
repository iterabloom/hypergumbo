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

  This check over-fires in two known shapes, both benign — budget review
  time for them rather than treating the count as a finding count.
  (a) **Extensibility notes**: "future PRs may extend the registry to
  Kotlin" beside a closed ID is a correct forward-looking note, not a
  stale pendingness claim; most hits are this. (b) **Mechanism words
  colliding with status words**: the pending-framing tokens are matched
  anywhere in the window, so prose describing what the *code* does —
  "under INV-fahub a bare call ... is **deferred** instead", naming the
  `defer_bare_method_call` mechanism — reads as a deferred *item*. Do
  not reword correct prose to dodge the heuristic; the mechanism's name
  is the right word.

- **Phase-marker grep.** Plain regex over docstrings + comments for
  "Two-pass", "Phase 2b", "Slice B", "not yet implemented", "deferred
  to", "arrives in", "will ship", "TODO", "FIXME". Highest signal
  when intersected with closed tracker IDs from the previous check.

### What the scan CANNOT see (run this grep too)

All three sub-checks key on *change* — blame dates, tracker status,
marker words. None of them reads the vocabulary registries, so none can
find **a docstring naming an edge type or symbol kind that no longer
exists**. Such a docstring may never have aged relative to its body, so
it is invisible to co-change by construction.

This is not hypothetical: it is the defect family that dominated the
2026-08-18 audit. Nine edge types and one symbol kind, retired by the
ADR-0023 §6 and ADR-0027 folds, survived *only* in the prose claiming
them — and one had escaped into the published `docs/schema.json`, so
JSON consumers were being told about edge types the tool cannot emit.
Correcting the flagged files surfaced 21 further sites in nine files
the scan never flagged and cannot flag.

Until `WI-sipuk` lands a registry cross-reference sub-check, do this
manually as a fourth pass — it is one grep per retired name:

```bash
# For each name in edge_types.EDGE_TYPES / symbol_kinds that was ever
# folded or retired, confirm it survives ONLY where prose explains the
# fold. Anything else is a docstring teaching a vocabulary that is gone.
grep -rn '<retired_name>' packages/*/src/
```

Verify a hit before rewriting it: check the actual `edge_type=` /
`kind=` construction site, and check the registry. In that audit every
consumer already read the new field and only the comments were stale —
so the fix was documentation, not code. Confirm that each time rather
than assuming it.

### Step 2 — git-blame co-change rank (the script's default mode)

This is the same as step 1's co-change check but worth calling out
separately: the ranked output is the candidate pool for semantic
review. **Four** known measurement artifacts to watch for:

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

- **Calendar decay in the day-delta arm (fixed, do not reintroduce).**
  `delta_days = ds_age - body_age` is only meaningful while the
  docstring's date can still move. Where a mass commit PINNED it, delta
  grows with wall clock alone and the `>= 90` threshold degenerates into
  "was this touched recently". Measured: the flagged count went 28
  (2026-05-15) → 87 (2026-08-18) **with nothing having drifted**, driven
  by three mass commits that pin docstring blame dates (the ADR-0010
  reorg, the Phase-3/4 `TreeSitterAnalyzer` migrations, the ADR-3bbb
  subcategory stamp). The scan now applies the day arm ONLY where the
  floor commit is particular to the file, and leads with
  `body_commits_since_ds` — commits, not days. Read the `[commits]` arm
  as the signal and treat `[days]`-only rows as weaker evidence.

  **Do not rebuild the "sweep-skipper"** that tries to detect and skip
  past mass-stamp commits. It was built and refuted: the ADR-3bbb stamp
  owns a median 1 line (2%) of the docstrings it floors, a genuine
  staleness-audit fix owns 2 (4%), so no threshold on size or file-count
  separates them — and 5 of 9 detected "sweeps" ARE prior audit fix
  commits, so any such rule makes each audit blind the next one to
  exactly what it just verified. Pinned by
  `tests/test_check_docstring_drift.py`.

- **Function-docstring edits re-flag the file.** The scan's "body"
  region is everything outside the *module* docstring — which includes
  every function and class docstring in the file. So editing a function
  docstring moves `body_age` while `ds` stays put, and the file appears
  as fresh drift on the next run. Observed directly: `linkers/ipc.py`
  was newly flagged by the 2026-08-18 post-audit re-run because that
  audit had corrected a function docstring inside it while (correctly)
  leaving an accurate module docstring alone. **A newly-flagged file is
  not evidence of drift — check whether the previous run's own fix
  produced the flag.**

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
Concurrency is capped (20 in the 2026-08-18 run); excess launches are
REJECTED, not queued, so send them in waves and relaunch as slots free.
Budget real wall-clock: 45 agents over four waves took ~35 minutes, not
the 1–2 minutes an unthrottled fan-out would suggest. A big file
(`py.py`, `cli.py`) can take a single agent 4+ minutes on its own.

Two prompt details that materially changed answer quality in that run:
tell the agent to read the file **IN FULL** (several analyzers exceed
3,000 lines and sampling produces confident wrong answers), and tell it
to **quote the contradicting code with line numbers**. The line numbers
are what make Step 4 verifiable instead of a second round of searching.

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
- **Never split a module docstring's FIRST line.** `generate-architecture`
  publishes that line verbatim as the module's one-line summary in
  `docs/ARCHITECTURE.md`. Wrapping a long opening sentence onto a second
  line truncates the published summary mid-clause — which is the exact
  defect this audit exists to remove, reintroduced by the fix. It
  happened in the 2026-08-18 run (`rust_scip.py` published as "…ADR-0014
  §3 as"). Keep the summary one line; put the elaboration in the body.
  Cheap guard over every file you touched:

  ```bash
  python3 - $(git diff --name-only -- '*.py') <<'EOF'
  import ast, sys
  for path in sys.argv[1:]:
      doc = ast.get_docstring(ast.parse(open(path).read()), clean=False)
      if doc and not doc.strip().splitlines()[0].strip().endswith('.'):
          print("first line incomplete:", path)
  EOF
  ```
- **Ruff rejects ambiguous Unicode in docstrings (RUF002).** Writing a
  multiplication sign (`×`) in new prose fails the pre-commit gate; use
  `x`. Em-dashes are fine and used throughout.
- **Grep for verification, not `grep | head`.** Truncating the verifying
  grep is how the 2026-08-18 run reported a retired-name sweep as
  complete when 21 sites remained across 13 files. Count the hits, then
  read them.

## Cadence guidance

V1 cadence: invoke on demand at the moments listed above. If we
discover that drift accumulates predictably (e.g., always within
~3 weeks of a major ADR closure), promote to a scheduled hook or a
periodic CI job. Until then, on-demand keeps human attention on
real signal.

## Reference: calibration runs

The thresholds `--window=120` and `--min-delta=90` were calibrated on
2026-05-15 and have not changed since. Two runs measure them:

| Run | Flagged | Real drift | TP rate | PRs |
|---|---:|---:|---:|---|
| 2026-05-15 | 28 | 21 | 75% | #3749 (A+B+D), #3751, #3752 (C) |
| 2026-08-18 | 45 | 41 | **91%** | #414 (A+B+D), #416 (C) |

The 2026-05-15 run also produced #3751 as a side-effect (widening the
`verify-generated` window 5 → 15 commits). Its notebook entry is at
`~/<repo>_lab_notebook/stratum6_staleness_audit_05152026.md`.

The 2026-08-18 run reviewed 45 files and found 41 with real drift —
134 findings (A=4, B=54, C=63, D=15). Its notebook entry is at
`~/<repo>_lab_notebook/staleness_audit_08182026_2100.md`. Note that
its 45-file pool did NOT include the 15 files a same-day earlier audit
had just fixed: correcting a module docstring resets `ds`, so a fixed
file drops out of the pool on its own. Post-merge re-run: **45 → 6
flagged, with the clock-independent `[commits]` arm at zero**, and all
six survivors verified as true negatives.

**A caveat to carry into any future run: the flagged count measures the
scan, not the tree.** The two most consequential findings of the
2026-08-18 audit — the `schema.json` leak and the 21-site retired
vocabulary residue — were invisible to all three sub-checks by
construction (see "What the scan CANNOT see"). A low flagged count is
evidence about co-change drift only. Do not report it as evidence that
the docstrings are accurate.
