<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### Running Tests (smart-test)

**IMPORTANT:** Always use `pytest` (aliased to `smart-test`) for running tests. Do NOT bypass it with:
- `command pytest`
- `python -m pytest`
- `.venv/bin/pytest`
- `SMART_TEST_ACTIVE=1 pytest`

These escape hatches exist for debugging but waste tokens by producing ~4000 lines of raw output. **They also bypass the mypy gate below, which is the one reason not to use them that is not about token cost.**

### Gate 0: the mypy strict ratchet, and why it forbids testing

`smart-test` runs `scripts/check-mypy-ratchet --mode=strict` **first**, before slicing and before pytest. Outcomes:

| ratchet exit | meaning | smart-test |
|---|---|---|
| 0 | no code's error count grew | prints `✓ mypy ratchet clean`, continues |
| 1 | a regression, **or** an instrument mismatch (baseline measured with a different mypy, so the counts are not comparable) | prints the breach and **exits 1 without running pytest** |
| 2 | infrastructure (mypy missing, no source roots) | warns and continues — an infra failure is not a type regression, per the ratchet's own documented contract |

**The rule that matters: you may not run tests until it is clean, even if you did not cause the regression.** "Not my change" is not an exit. The ratchet is shrink-only, so whoever observes the breach owns it. This clause is the whole point of the gate rather than a stern flourish — the surface drifted **672 → 682 across sixty commits** touching 24 source files precisely because every agent in turn could see a breach it had not authored and move on. If you inherited it, you are the one who found it: fix it, and say so in your PR.

**Do not route around it.** Not with direct `pytest` / `python -m pytest`, and not with `HG_SKIP_MYPY_GATE=1`, which exists solely so the ratchet's own tests can run without recursion. **Do not "fix" a breach by raising `.ci/mypy-strict-baseline.json`** — a shrink-only baseline may only ratchet DOWN, and raising it to accept a breach ratifies the drift and destroys the only record of what the surface used to be (L33).

**Honest limit.** The gate lives in `smart-test`, which is what the `pytest` alias invokes, so it covers the normal path. A direct `python -m pytest` goes around it; there the enforcement is this text and the banner, not a mechanism.

**Why it had to be added.** INV-zogud's ratchet was documented in AGENTS.md and the Wave-5 notebooks as "BLOCKING, instrument-pinned to mypy 2.1.x", and on the pipeline that actually gates merges it was neither. `auto-pr` polls exactly one CI context — `ci/woodpecker/pr/woodpecker` — and merges on it; that step ran `--mode=warning` *and* carried `failure: ignore`, so it could not fail a pipeline by either route, while the GitHub Actions `--mode=strict` job and its `ci-complete` gate were never consulted by the merge path. Nothing ran mypy locally either: not `.githooks/pre-commit`, not `pre-push`, not `auto-pr`, not the pre-commit checklist. All three invocations are now strict and the pin is `mypy~=2.1.0` everywhere; `tests/test_smart_test_mypy_gate.py` asserts each of those independently, because two of the three edits are silent on their own.

**smart-test provides:**
1. **Compact summary** - Only shows test result, failures, and coverage gaps (~20 lines vs ~4000)
2. **Full log saved** - Complete output in `.ci/pytest-output.log` for debugging
3. **Affected test selection** - Runs only tests that depend on changed files (when stable hypergumbo is installed). A changed `.py` file whose code is unchanged (comments and docstrings only) skips the import graph: its tests come from recorded evidence, the manifest carries them too, and its coverage is not gated, locally or in CI (ADR-0010 §"What the Manifest Carries", ADR-0011). `--no-prose-rule` turns this off for one run.

**Usage:**
```bash
pytest                      # Compact summary (default)
pytest --raw                # Stream full output (rare, for debugging)
pytest --full               # Run all tests, not just affected
pytest --verbose            # Show test selection reasoning
```

**The compact summary shows:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❌ ================ 5770 passed, 98 warnings in 317.89s =================

COVERAGE:
packages/.../cli.py      1378      7    99%   805-807, 811, 844, 876, 914
packages/.../bash.py      191      1    99%   78
TOTAL                   33003     10    99%
FAIL Required test coverage of 100% not reached. Total coverage: 99.97%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Full output: .ci/pytest-output.log
Targeted run (14 test files, 3 changed sources). Full suite runs in CI twice daily at 01:00 and 13:00 UTC (not post-merge); running it now would be wasteful.
```

The Missing column shows exact line numbers to fix. **Do NOT re-run tests** just to find missing lines. The "Targeted run" message means smart-test already validated 100% coverage for your changed files — do NOT follow up with `pytest --full`.

### Where the selection comes from: a stored graph

The affected-test selection walks a graph of the repository backwards from the changed files. Analysing the repository to get that graph takes about five and a half minutes; walking a graph takes a fraction of a second. So `smart-test` keeps the graph of every analysis it makes (as rows in the selection index, about 5 MB each) and **selects from a stored graph whenever one may answer**. Measured on the day it went live: a manifest run that had to analyse took 345 s, the next one took 14 s.

It prints which graph answered, and the manifest header carries the same line:

```
[smart-test] selecting from stored graph 1 of fd5448d16, 0 commit(s) old: 11 seed file(s), 0 of them not this change's, reaching 0 test file(s) this change does not; 205 file(s) named (--fresh-graph analyses the repository instead)
```

**What is seeded.** Every changed file of your change, plus every file that differs between the graph's tree and yours (other people's merged work since the graph was made, and files that were uncommitted when it was made). That is what makes an older graph safe to walk, and it means **an older graph selects more tests than your change alone needs**: the line says how many files are "not this change's" and how many test files only they reach.

**When it analyses instead** (it says which, and then runs the slice as before):

| reason printed | meaning |
|---|---|
| `no usable stored graph` | none is stored for the installed analyzer build, or the newest is more than 93 commits old, or git no longer has its tree |
| `... was added since the stored graph and no other changed file mentions it` | a new file the graph has no node for, which nothing else in the change names (in practice: a tree of fixtures a test reads by scanning a directory) |
| `... an added conftest.py ...` | a new `conftest.py` applies to every test under its directory without anything naming it |
| `... reach N test file(s) this change does not (limit 150)` | the graph is so far behind that a fresh analysis is cheaper than the extra tests |

A fresh analysis leaves a graph of your tree, so the next run on the same branch is fast again. `--fresh-graph` forces one. CI never selects: it reads the committed manifest.

**The graph that answers exactly, made in the background.** A graph of your branch's *baseline* commit (its merge-base with `dev`) differs from your tree by your own change and nothing else, so it selects exactly: no surplus tests, no refusal. Every `smart-test` run therefore starts, for after it has ended, an analysis of that commit in a checkout of its own (`coverage-select graph-rebuild`), unless that graph is stored already. You do not manage it:

- it waits for the run to end and a minute more, runs one at a time, and does not start while a test run holds the checkout or less than 6 GB of memory is free;
- **a `smart-test` that starts stops it** (`[smart-test] stopped a graph rebuild ...`) and starts it again when it ends, so it never runs beside tests;
- it works in `~/.cache/hypergumbo-graph-rebuild/` (override: `HG_GRAPH_REBUILD_DIR`), removes its checkout and its survey when done, and writes one line per outcome to `rebuild.log` there.

If runs follow one another without a few idle minutes (an agent's pre-PR chain), it never gets to finish and the first selection on a new baseline analyses in the foreground as before.

**In a git worktree the same graphs answer.** The selection index is one file for every checkout on the machine, and `smart-test` runs the installed hypergumbo **without the run's `PYTHONPATH`**: a worktree run pins `PYTHONPATH` to its own `packages/*/src`, and handed on it would make the installed command import the code under test, under an analyzer build name no stored graph has. (Measured for one commit: the installed build's graph and the tree build's graph name the same test files for 2,618 seed sets.) To slice with a tree's own code on purpose, give `smart-test` a `HOME` whose `.local/bin/hypergumbo` is a wrapper that sets `PYTHONPATH` itself.

**The check that stays on.** Every time a fresh analysis does run, `smart-test` also records what the stored graph would have selected (`~/hypergumbo_lab_notebook/stale_graph_observations.jsonl`; `scripts/coverage-select stale-report` prints the verdict). A line `STORED GRAPH LOST n TEST FILE(S)` means the stored graph missed a test the fresh analysis found and your change did not edit. **That is a stop:** file it, and use `--fresh-graph` until it is understood.

**Looking at the stored graphs:** `scripts/coverage-select graph-list`.

### The wall-time line

Every local run that reaches pytest ends its test phase with one line: its wall time against the **60 s local target** (owner ruling, 2026-11-17). Under the target it gives the total and its parts; over it, by how much, the parts largest first, and the slowest single test, which bounds wall time whatever the worker count. It is reported and gates nothing. The clock stops when the tests end (the coverage gate and the fold into the selection index come after), and the line says so. Example: `smart-test: 15 s to the end of the tests (local target 60 s): the tests 10 s, the rest 4 s, the type gate 1 s (49 tests, 15 test-seconds)`. "The rest" is what the timed parts leave of the total (change detection, narrowing, writing the manifest); "test-seconds" is the tests' summed duration, which exceeds their wall time by about the number of workers.

### Coverage narrowing, and the tests it never drops

After the graph has named the test files a change reaches, smart-test drops the ones whose recorded coverage shows they never run the changed code (`--no-narrow` keeps them all). The manifest is written after this step, so merge CI runs the narrowed set.

A test file is never dropped while coverage cannot speak for one of its tests. That is a test whose **own call** ran no measured code: it works through a subprocess (`scripts/`, the CLI) or in code outside the measured packages. Rows from a test's setup and teardown do not count, because a fixture can run for every test; the root `conftest.py` has one, and while its row counted, every test looked measured and narrowing dropped subprocess-driven test files for changes to the code they run. The list of such tests is rewritten only by a full run (`smart-test --full`), which prints its size and the size before (`full run: coverage cannot speak for N test(s) ...; M before this run`). **A sharp fall in that number is a defect, not an improvement.**

**Narrowing refuses when import-time code changed**, because code that runs while a file is imported runs under no test's context and leaves no row for the tests that depend on it. Import-time code is a module body, a class body, **and any function a run saw execute under no test's context**: `TABLE = build()` in a module body runs `build` at import, and a test that reads `TABLE` never runs `build` itself. The index marks such functions when a run sees them (113 of 6,515 at the time of writing); a partial run only adds marks, a full run marks afresh. A function that newly starts to run at import is unmarked until a run that loads the calling module is folded. The marks also feed the import graph that selects for an import-time change: an import written inside a marked function runs when its file loads, so it counts as an import of that file (the Haskell and Jupyter analyzers load `io_boundary` that way). An import inside a function that does not run at import still does not count.

### Shared fixtures: every test that uses one is paired with its code

A module, class or session fixture runs once per worker, inside the setup of whichever test needs it first. Labelled with the running test, its code belonged to that one test, and a change to that code selected one of the tests that depend on it. The recorder plugin (`scripts/pytest_plugins/pytest_source_reads.py`, on in every local run) now runs such a fixture, both halves, under a coverage context of its own (`|shared|fixture|<key>`) and records which tests used it and which shared fixtures were built inside it. `coverage-select update` gives the rows of that context to every test that used it. On a full run (and whenever something is off) it prints one line: `shared work: N fixture context(s) with measured code; M test(s) given their rows; K context(s) no test is recorded using`. K above zero means a worker's record is missing; that code is then stored as run under no test, and narrowing treats a change to it like a change to import-time code. Not covered: a value a function keeps for later callers (a cache), and state an object keeps after a shared fixture returned it.

### Child processes: a test that starts one that can run Python is never narrowed

Coverage does not measure a child process. A test that runs some measured code itself and also starts `python`, a shell or `git` has rows in the index, and none for what the child ran. Its silence about a changed function is therefore not evidence, and no narrowing drops it: the file-level narrowing keeps its file, and the by-statement selector selects it whole wherever it reached a changed function (`Selection.cannot_speak` names every such test, so a caller knows which tests the selector says nothing about).

The recorder plugin already writes every process a test starts, and `selection_index.coverage_cannot_speak_for` reads that record. It also holds a test with **no read record** (it failed in the recorded run, its worker died, or the record is of another recorder version), because what that test started is not known. An index with no read record at all narrows nothing.

**Exemptions are by program name**, in `hypergumbo_core/started_programs.py`, each with its reason: `gitleaks` (a Go binary that scans text) and a few programs that only print or wait. `git` is not exempt, in this repository or another, because it runs hooks; a `git` run in a scratch repository is recorded as `git-elsewhere`. A full run prints how many tests are held and by which programs (`never narrowed, besides the unmeasured: ...`).

Measuring coverage inside the children was tried and not chosen (it costs 1.3 to 4 times a child's run time and loses a child's data without an error in three cases).

### The by-statement selector (recording only)

The selection index records which statements of a function each test ran. A second selector uses that: for a changed function it picks the tests that ran a statement the change touches, not every test that reached the function (`hypergumbo_core/statement_diff.py` says which old statements a change makes a test answer for; `statement_select.py` reads the records). **It selects nothing yet.** Every run that reaches pytest stores, in its row of `~/hypergumbo_lab_notebook/run_records.jsonl` under `by_statement`, what it would have selected and the one result that counts against it:

- `failed_not_selected`: tests that reached a changed function, **failed**, and would have been left out. It has to stay empty over 30 runs that had such a failed test before the selector may select.
- `failed_reaching_a_narrowed_block`: the failed tests the selector *could* have left out (they reached a function it answered `by statement`). Only a run where this is not empty says anything about the selector.
- `failed_coverage_cannot_speak_for`: failed tests the selector does not narrow (unmeasured, starting a program that can run Python, or with no read record). One of these outside the selection is a test the selector says nothing about, not a test it spared.
- `files_not_judged`: changed files the selector has no rule for (anything that is not package source).

**`scripts/coverage-select statement-bar` counts the bar** from the run records and exits 0 only when it is met. A run counts when a failed test was at risk, the index knew every changed function, **and the selector left tests out** (where it selects every test that reached the function there is nothing it could have missed); the same state of the tree counts once; run kinds are listed apart (a run given test files by name only tried tests someone had chosen). A run whose change touches the selector's own code (`selection_log.SELECTOR_FILES`) is not counted either way: its row was written by the code under change, or by a mutation control of it. One run with a failed test left out reads `MISSED`, and the selector stays off. **A clean count is necessary, not sufficient.** It cannot see a failed test the index does not pair with the changed function at all, and work done once and shared leaves most of its dependents unpaired: a memoised function (only its first caller runs it), a module- or session-scoped fixture (it runs in one test's setup), a subprocess. A planted-fault campaign of 2026-11-18 passed this count (39 runs, 781 tests left out, none failed) and still found 13 failing tests in 3 of 93 runs that selecting test by test would have left out. The selector therefore stays recording-only.
- `answers`: how each changed function was answered. `by statement` is the selector working. `no record of the block as it was: every test that reached it` means the index holds nothing of the function as it was at the base of the change. The index keeps the statement records of a function's last 8 superseded versions (a run that stores a new version copies the old one's records aside first), so a change that has already been run is still compared with its base. What it cannot supply is a version no folded run ever ran: **only a run that selects for itself, or `--full`, is folded into the index; a run given test files by name is not**, so a base that was only ever tested by named runs reads this way. `the index has never seen this block` (counted in `unknown_blocks`) means no selection is possible for the change at all.

### Always Commit `.ci/affected-tests.txt`

The file `.ci/affected-tests.txt` is generated by `smart-test` and **must be committed** with every PR. CI reads this committed manifest to decide which tests to run — without it, CI would either fall back to the full suite (slow) or have to install hypergumbo just to compute the affected set (also slow). The manifest carries a `Mode:` field (`targeted` or `full-suite`) so CI can display appropriate messaging.

Even when only the timestamp comment changes, commit it anyway. Distinguishing "real" changes from timestamp-only changes isn't worth the extra logic, and CI may depend on the file being fresh.

**Stash hazard:** Because `smart-test` regenerates this file on every run, running tests between `git stash` and `git stash pop` will modify it, causing a merge conflict that blocks the pop. Before restoring a stash, always reset the file first: `git checkout -- "$(git rev-parse --show-toplevel)/.ci/affected-tests.txt"`. This is harmless — the next test run will regenerate it anyway.
