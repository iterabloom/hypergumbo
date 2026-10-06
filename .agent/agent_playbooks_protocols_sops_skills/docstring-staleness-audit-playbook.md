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

The script implements four sub-checks:

```bash
scripts/check-docstring-drift                 # co-change scan (default)
scripts/check-docstring-drift --tracker-refs  # WI/INV/BUG status cross-ref
scripts/check-docstring-drift --phase-markers # "not yet", "Slice B", etc.
scripts/check-docstring-drift --registry-refs # names the registries lack
scripts/check-docstring-drift --all           # all four
scripts/check-docstring-drift --json          # machine-readable
scripts/check-docstring-drift --rolling 30    # + 30 least-recently-reviewed files
```

`--rolling` is not a fifth drift signal. It selects files the signals
cannot reach; see "The candidate pool" below for why every audit uses it.

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

- **Registry cross-reference** (`--registry-refs`). See the next
  section: it exists because the other three cannot see its defect
  class. Reported only; it never changes the exit code.

### What change-keyed scans CANNOT see, and the registry cross-reference

Co-change, tracker-refs and phase-markers all key on *change*: blame
dates, tracker status, marker words. None of them can find **a docstring
naming an edge type or symbol kind that no longer exists**. Such a
docstring may never have aged relative to its body, so co-change cannot
see it by construction.

This is not hypothetical: it is the defect family that dominated the
2026-08-18 audit. Nine edge types and one symbol kind, retired by the
ADR-0023 §6 and ADR-0027 folds, survived *only* in the prose claiming
them — and one had escaped into the published `docs/schema.json`, so
JSON consumers were being told about edge types the tool cannot emit.
Correcting the flagged files surfaced 21 further sites in nine files
the scan never flagged and cannot flag.

`--registry-refs` covers this class. It reads every comment and every
string literal that contains whitespace (docstrings at any level, plus
description strings such as a `MetaKeySpec`'s, the path that leaked into
`schema.json`). In that prose it finds names presented as vocabulary:
`X edges`, `edge type is X`, `kind="X"`, `evidence_type="X"`. It reports
each name that is in none of the live registries (edge types, symbol
kinds, evidence types, meta keys, io-boundary kinds, all read through
their `all_*_names()` resolvers). A hit whose sentence explains a fold
("folded onto", "retired", "previously", "dropped", ...) is listed under
**Fold-explained** instead of flagged. Read that list too: the
suppression is a regex, not a judgement.

**Measured (WI-sipuk, 2026-10-01).** Pre-fold tree (`22418804f4^`,
the 63 retired-name lines that commit removed, in 19 files): the check
flags 18 of the 19 files and 36 of the 63 lines. None of the 63 is
suppressed as fold-explanation. Live tree: 16 flagged in 11 files, of
which:
- 7 are real retired-vocabulary drift (`renders`, `enqueues` and
  `crdt_publishes` named as live edge types).
- 4 are a real code/registry disagreement: the docstring is right and
  the linkers emit evidence types the registry lacks.
- 5 are false positives: a function name before "edge endpoints",
  two `UsageContext(kind="call")` mentions (a different record's
  `kind`), a fold explained only in the *next* sentence, and an
  illustrative placeholder name.

All 24 fold-explained hits on the live tree are genuine fold
explanations. **Not measured:** precision on any repository other than
this one, and the 130 pre-fold-tree hits outside the 63 (most name
values absent from today's registry, but none was adjudicated).

**What it still misses, so keep the grep for these shapes:**
- Arrow bullets (`- producer.send(...) -> message_publish`). An arrow
  context added 231 hits on the live tree for 17 more audit lines, all
  in files already flagged, so it was left out.
- Definition lists (`- **links_to**: Links from ...`). This is the one
  audit file it misses.
- A bare retired name with no underscore and no quoting ("create an
  enqueues edge").
- Code. A literal value in code is the producer-coherence gates' job.

```bash
# For a name you know was folded or retired, confirm it survives ONLY
# where prose explains the fold.
grep -rn '<retired_name>' packages/*/src/
```

Verify a hit before rewriting it: check the actual `edge_type=` /
`kind=` construction site, and check the registry. In the 2026-08-18
audit every consumer already read the new field and only the comments
were stale, so the fix was documentation, not code. On 2026-10-01 the
lua_ffi / napi hits went the other way: the prose was right and the
registry was missing the values. Confirm which side is wrong each time
rather than assuming it.

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

  **But CHECK is not DISMISS — the artifact explains why a file APPEARS,
  not whether it has drifted.** The 2026-09-04 run treated `linkers/ipc.py`
  as the known artifact and audited it anyway. The module docstring had
  four real drifts, the largest being a promise the code never keeps:
  it advertised `worker.postMessage(data) -> event_publishes`, while
  `POSTMESSAGE_PATTERN` sets `"channel": ""` and the edge loop does
  `if not channel: continue` — no edge and no symbol, ever. Had the
  artifact been used to skip the file, that finding would have been
  missed twice running.

### The candidate pool: the flags PLUS a rolling read

The flags do not separate drifted files from accurate ones. Drift is
close to everywhere this audit has looked (calibration table below):

- **Flagged files:** 57-91% drifted across runs.
- **Unflagged files with the most body commits:** 26/28 (2026-09-25b),
  14/15 (2026-10-05).
- **Unflagged, code-changed files:** 31/35 (2026-09-28, reconstructed).
- **Unflagged files no audit had read, sampled at random** (2026-10-06;
  seed 20261006, stratified by package, median 2 body commits since the
  docstring): **19/30 drifted (95% CI 46-78%), 14/30 with a wrong
  reference or a false claim.** That is a lower bound; see Step 3.
  Extrapolated to that 215-file population: about 136 drifted files
  (CI 99-168).

So the scan's true-positive rate is close to the base rate, which makes it
a weak filter, and a low flagged count says nothing about the tree outside
it. The pool is therefore the flagged set PLUS a rolling read:

```bash
scripts/check-docstring-drift --rolling 30
```

`--rolling N` lists the N files whose last recorded review in
`.ci/docstring-review-ledger.json` is oldest, never-reviewed first (oldest
docstring first), excluding the flagged set and generated code. At 30 per
audit, the 185 scanned files with no recorded review on 2026-10-06 take
about six audits. Choose N for the review budget, not for the flag count.

**Record every file you read, matches included,** in the fix PR:

```bash
scripts/check-docstring-drift --record-review <file> [<file> ...] --run 2026-10-06
```

A file read and found accurate leaves no commit behind. Without its ledger
row the next audit reads it again, and a never-read file waits another
round. The ledger records reviews; it does not infer them, because blame
cannot tell an audit fix from a mass stamp (Step 2). `--record-review`
validates every path before writing any. Prune the rows that `--rolling`'s
header lists as having no scanned file (renames, deletions).

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

Two more from the 2026-10-06 sample:

- **A first-pass "matches" is a lower bound on drift, not a clearance.**
  Tell reviewers they are measuring a rate, not hunting, and to be as
  willing to answer "matches" as "drift". Then re-review a few "matches"
  blind, with a fresh agent and no hint. In that run a fresh agent found
  drift in 2 of 5 files the first pass had cleared. One of them hid a
  reproduced code defect: `coverage_census`'s lazy-greedy test selection
  picks a worse set under grouped fixed costs (INV-jupaf).
- **Send every finding to a verifier that defaults to REJECT and reads the
  code itself.** In that run 36 of 39 findings held, 3 were rejected, and
  4 changed severity. A claim that something is NOT wired gets the
  stricter check in "Wiring that never happened" below.

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
- **Ruff rejects ambiguous Unicode in docstrings (RUF002) and in
  COMMENTS (RUF003).** Writing a multiplication sign (`×`) in new
  docstring prose fails the pre-commit gate; use `x`. The comment twin
  fires on the same class of character — the 2026-09-04 run tripped
  RUF003 on a set-union sign (`∪`) in a new `axis_meta_keys.py` comment;
  write "plus". Em-dashes are fine and used throughout.
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
2026-05-15 and have not changed since. These runs measure them. The
unflagged columns are the control the flagged rate needs: a flagged rate
means something only if it beats the rate outside the flags.

| Run | Flagged | Flagged with drift | Unflagged read (how chosen) | Unflagged with drift | PRs |
|---|---:|---:|---|---:|---|
| 2026-05-15 | 28 | 21 (75%) | — | — | #3749 (A+B+D), #3751, #3752 (C) |
| 2026-06-19 | 70 | 55 of 79 read (70%)¹ | 9 (tracker-ref hits) | ¹ | #4226 (A+B+D), 21532da5dc (C) |
| 2026-07-27 | 75 | not measured² | 29 (hand-picked) | 24 (83%)² | — |
| 2026-08-18 | 45 | 41 (**91%**) | — | — | #414 (A+B+D), #416 (C) |
| 2026-09-04 | 24 | 18 (75%) | 7 (found by the registry pass)³ | 7³ | #776 (A+B+D), C bundle |
| 2026-09-25 | 44 | 39 (89%) | — | — | #1197 (A+B+D), #1199 (C) |
| 2026-09-25b | 5 | 0 (all 5 fixed that morning) | 28 (most body commits) | 26 (93%) | 9bb5bd2c23, 5e524f0545 |
| 2026-09-28 | 28 | 16 of 23 read (70%)⁴ | 35 (code changed since last audit)⁴ | 31 (89%)⁴ | #1285 (A+B+D), #1286 (C) |
| 2026-10-05 | 19 | 16 (84%) | 15 (most body commits) | 14 (93%) | #1479 (A+B+D), #1481 (C), #1488 (re-scan tail) |
| 2026-10-06 | — | — | **30 (seeded random, never read)** | **19 (≥63%)**⁵ | #1492 |

¹ The pool was the 70 flagged plus 9 tracker-ref-only files; the notebook
records 55 drifted files across all 79, not split by source.
² The 29 were picked by hand toward likely drift, not from the flags,
so neither rate is comparable to the others.
³ Selected BECAUSE they had a defect, so not a rate.
⁴ Reconstructed from the saved scan output and the notebook's list of
11 accurate files. 5 flagged files were 09-25-verified and not re-read.
The unflagged split assumes the two flagged files outside the 21
"flagged-and-unreviewed" were inside the 37 code-changed ones.
⁵ A lower bound: a blind re-review found drift in 2 of 5 first-pass
"matches", and 6 other "matches" were not re-read. 14/30 carry a
wrong reference or a false claim.

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

### A third family the scan cannot see: `file:line` citation rot

Found 2026-09-04, twice in one day, and it belongs beside the retired-vocabulary
family above for the same structural reason. A registry that cites source it does
not own by `file:line` — `MODULE_KEY_NOTIONS` in `module_key_axis.py` is the known
instance — rots whenever ANY line is inserted above the cited one. That is not a
docstring edit and moves no blame date, so no arm of `check-docstring-drift` can
see it.

Both failures in that run were self-inflicted by the audit's own neighbourhood:
three citations rotted from the receiver-typing PRs' code movement (swift/objc),
and a fourth rotted from a pure MODULE-DOCSTRING EDIT to `cpp.py` — prose, in a
different package, nowhere near the anchor. Worse, no test-selection arm runs the
guarding test when the *cited* file changes, so the first breakage sat red on `dev`
for a day until the audit happened to run a full suite.

**So: after any bundle that changes line counts, re-run the citation check before
pushing.** It is one loop:

```python
from pathlib import Path
from hypergumbo_core.module_key_axis import MODULE_KEY_NOTIONS
for n in MODULE_KEY_NOTIONS:
    for s in n.emission_sites:
        lines = (Path('.') / s.path).read_text().splitlines()
        if s.anchor not in lines[s.line - 1]:
            print(n.name, s.path, s.line,
                  "-> now at", [i + 1 for i, l in enumerate(lines) if s.anchor in l])
```

Tracked as INV-fogul (make the anchor authoritative and regenerate the line, the
way `test_cli_docs_prose_gate.py` already regenerates its flag matrix).

### A fourth family: wiring that never happened — check it carefully

Found 2026-10-06 in a random sample of files no audit had read. A docstring
describes an integration the code never made: a class "adopted by the
analyzers" that no analyzer has ever constructed (`ImportScope`), an HTTP view
"with WebAuthn, password, session and duress authentication" whose routes check
nothing and import none of those modules, route markers "wired to Scala
controller methods" by a linker that has no branch for them, a fingerprint "used
by the circuit breaker" that the circuit breaker never calls. No co-change ever
touches these files because nothing changed: the wiring was planned, the module
landed, the connecting step did not. Every later author reads the claim as true
because nothing nearby contradicts it.

**This is the easiest family to get wrong in the other direction.** "Nothing
calls X" is a claim about the whole repository, and wiring often happens in a
way nobody expected: a registry, a decorator, an entry point, a dispatch table,
a shell hook calling a CLI subcommand, an import done for its side effect, a
name built from a string. A reviewer who greps the obvious name and finds
nothing has not shown the wiring is absent. Before you write "not wired",
"unused" or "never adopted" into a docstring or a tracker item, do ALL of these
and cite what you checked:

1. **Ask the call graph first: `hypergumbo survey .` once, then `hypergumbo
   explain <Name>`** for each piece of the claimed wiring (the class, its
   constructor, the function, the CLI handler). Read every inbound section, not
   just "Called by": `Instantiated by`, `Imported by`, `Referenced by` (CLI
   dispatch tables show up here), `Dispatched-to by` (the linker registry;
   not checked for the analyzer registry), and import-time registration
   calls. Split the callers into tests and production code: a symbol whose only callers are under `tests/` is
   the signature of this family. Measured on this repo 2026-10-06 (survey 4.5
   min, 291 MB): it found the decorator-registry dispatch of a linker
   (`Dispatched-to by run_all_linkers`), CLI dispatch-table references
   (`Referenced by main`), import-time `register_ddg_language` calls, and a
   same-module wrapper (`hash_todos_safe`) the reviewer had not mentioned.
2. **Then cover what the call graph does not see.** Measured the same day:
   `stop_logic.sh` running `scripts/tracker guidance` does NOT appear as a caller
   of `_cmd_guidance` — a shell, hook or CI step invoking a CLI subcommand is
   invisible to it. Grep, with both spellings (`hash-todos` and `hash_todos`):
   `.agent/hooks/`, `scripts/`, `.github/` and `.woodpecker/` workflows,
   `[project.entry-points]` in every `packages/*/pyproject.toml` (the
   language packages reach the core only through the `hypergumbo.analyzers`
   group, so an analyzer module has no importer in core by design),
   YAML/TOML/JSON config, and string-built lookups (`getattr`, `importlib`,
   `__import__`, registry keys). Also check the other packages: wiring may
   live one package over.
3. **Disambiguate the name.** `hash_todos` is both `stop_hook.hash_todos` and
   `TrackerSet.hash_todos`; a hit on one says nothing about the other. `explain`
   lists same-named symbols separately — make sure you are reading the one the
   docstring means.
4. **Check history: `git log -S<Name> -- packages/`.** If the wiring existed
   and was removed, the finding is "removed in <sha>", a different fix (maybe a
   regression) from "never landed".
5. **If the claim is about behavior, run it.** An absent edge in the graph is
   not proof — the graph has blind spots, and you are claiming one is not
   hiding the wiring. Route resolution, authentication, a hook's effect: build
   the smallest input and observe (the Play-routes finding was confirmed by a
   three-route fixture plus a full `survey` showing zero `dispatches_to` edges;
   the serve-auth finding by a request that reached the handler with no
   credentials, AND by checking that the shipped entry point never passes the
   handlers a TrackerSet, so they answer 503 — which changed the finding from
   "exposed" to "latent").
6. **Word the result as a dated, scoped observation**, not a universal: "no
   production caller as of <sha> (hypergumbo explain; grep of hooks, scripts,
   workflows, entry points)". File the code side as a tracker item: an
   unwired module is usually a code finding, not only a docstring one.

The Step 4 verifier must apply the same list to any reviewer claim of
this shape; default to REJECT a "never wired" claim whose evidence is a single
grep.

The 2026-09-04 run reviewed 24 files and found 18 with real drift, plus 7
files the scan flagged NONE of (the registry pass). Its notebook entry is at
`~/<repo>_lab_notebook/staleness_audit_09042026.md`.

The 2026-10-05 run reviewed 41 files: the 19 flagged (16 with real drift),
15 unflagged files with the most body commits since their docstring (14), and
the 7 the post-merge re-scan still flagged (7). Those 7 were all `[days]`-only
and all floored by the Phase-3 `TreeSitterAnalyzer` migration commit, and 4 of
them had reproduced code bugs. So a `[days]`-only row whose floor is a mass
migration means "nobody has read this docstring since the migration", which is
worth reviewing, not weak evidence to skip. The review filed 14 tracker items
for 16 code defects, among them one family: an analyzer finding a call's
enclosing callable by name through the repo-wide resolver (WI-kosar). Its
notebook entry is at `~/<repo>_lab_notebook/staleness_audit_10052026.md`.

The 2026-10-06 follow-up read no flagged files at all: it was the random
sample described in "The candidate pool". It fixed 19 docstrings (#1492),
added `--rolling` and the review ledger (#1494), and filed four code items:
INV-jupaf (lazy greedy), INV-nabas (Play routes never resolve, reproduced),
INV-pamum (repo-fingerprint algorithm changed without a scheme bump) and
WI-hopip (the tracker server's write routes have no authentication, latent
until a TrackerSet is wired). The notebook entry is the "Follow-up" section of
`~/<repo>_lab_notebook/staleness_audit_10052026.md`.

**A caveat to carry into any future run: the flagged count measures the
scan, not the tree.** The two most consequential findings of the
2026-08-18 audit — the `schema.json` leak and the 21-site retired
vocabulary residue — were invisible to the three change-keyed sub-checks by
construction (see "What change-keyed scans CANNOT see"). A low flagged count is
evidence about co-change drift only. Do not report it as evidence that
the docstrings are accurate. The 2026-10-06 random sample measured the
point directly: unflagged files no audit had read drifted at about the
rate flagged files do.
