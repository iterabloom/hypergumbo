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

**The check that stays on.** Every time a fresh analysis does run, `smart-test` also records what the stored graph would have selected (`~/hypergumbo_lab_notebook/stale_graph_observations.jsonl`; `scripts/coverage-select stale-report` prints the verdict). A line `STORED GRAPH LOST n TEST FILE(S)` means the stored graph missed a test the fresh analysis found and your change did not edit. **That is a stop:** file it, and use `--fresh-graph` until it is understood.

**Looking at the stored graphs:** `scripts/coverage-select graph-list`.

### Always Commit `.ci/affected-tests.txt`

The file `.ci/affected-tests.txt` is generated by `smart-test` and **must be committed** with every PR. CI reads this committed manifest to decide which tests to run — without it, CI would either fall back to the full suite (slow) or have to install hypergumbo just to compute the affected set (also slow). The manifest carries a `Mode:` field (`targeted` or `full-suite`) so CI can display appropriate messaging.

Even when only the timestamp comment changes, commit it anyway. Distinguishing "real" changes from timestamp-only changes isn't worth the extra logic, and CI may depend on the file being fresh.

**Stash hazard:** Because `smart-test` regenerates this file on every run, running tests between `git stash` and `git stash pop` will modify it, causing a merge conflict that blocks the pop. Before restoring a stash, always reset the file first: `git checkout -- "$(git rev-parse --show-toplevel)/.ci/affected-tests.txt"`. This is harmless — the next test run will regenerate it anyway.
