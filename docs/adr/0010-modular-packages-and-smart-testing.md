<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0010: Modular Packages and Smart Testing

Status: Implemented

## Context

The hypergumbo test suite has grown to 5700+ tests across 108 language analyzers. CI runs on a self-hosted runner (limited to `-n 2` parallelism) were timing out or taking too long. We need faster feedback without:

1. Abusing the CI compute allocation
2. Compromising on 100% test coverage
3. Reducing test quality

Additionally, cross-module dependencies are implicit. A change to an extended language analyzer could theoretically affect mainstream language behavior through shared linkers, framework patterns, or registry interactions.

## Decision

We will restructure hypergumbo into 4 packages and implement a two-tier testing system: fast local smart-testing that dogfoods hypergumbo's call graph, and manifest-driven CI with lazy full-suite validation.

### Package Structure

```
packages/
  hypergumbo-core/
    src/hypergumbo_core/
      cli.py
      sketch.py
      slice.py
      ranking.py
      profile.py
      supply_chain.py
      linkers/           # Tier 2 edge-recovery passes (Protocol / Bridge / Framework / Infrastructure subcategories per ADR-3bbb)
      framework_patterns.yaml
      analyze/
        base.py
        registry.py      # Plugin registration system
    tests/
    pyproject.toml

  hypergumbo-lang-mainstream/
    src/hypergumbo_lang_mainstream/
      py.py
      js_ts.py
      java.py
      c.py
      cpp.py
      csharp.py
      go.py
      rust.py
      ruby.py
      php.py
      swift.py
      kotlin.py
    tests/
    pyproject.toml

  hypergumbo-lang-common/
    src/hypergumbo_lang_common/
      scala.py
      bash.py
      sql.py
      html.py
      css.py
      dockerfile.py
      lua.py
      perl.py
      haskell.py
      ocaml.py
      elixir.py
      erlang.py
      clojure.py
      fsharp.py
      # ... configs, markup, functional languages
    tests/
    pyproject.toml

  hypergumbo-lang-extended1/
    src/hypergumbo_lang_extended1/
      zig.py
      odin.py
      nim.py
      agda.py
      lean.py
      cobol.py
      apex.py
      solidity.py
      verilog.py
      vhdl.py
      # ... systems, HDL, languages that are not ubiquitous
    tests/
    pyproject.toml
```

### Language Groupings Rationale

| Package        | Languages                                                                                    | Rationale                                  |
| -------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------ |
| **mainstream** | Python, JS/TS, Java, C, C++, C#, Go, Rust, Ruby, PHP, Swift, Kotlin                          | "Every company uses at least one of these" |
| **common**     | Scala, Bash, SQL, HTML, CSS, Lua, Perl, Haskell, OCaml, Elixir, Erlang, Clojure, F#, configs | "Popular in specific domains"              |
| **extended1**  | Zig, Odin, Nim, Agda, Lean, COBOL, Apex, Solidity, Verilog, VHDL, etc.                       | "Specialized/not yet ubiquitous"           |

These three groups should contain (plus or minus 5) an equal number of languages. The `extended1` suffix leaves room for future `extended2`, etc. as language support grows.

### Runtime Warnings (Diagnostic Aid)

For development and debugging (e.g., testing a single module in isolation), hypergumbo warns about:

1. **Unanalyzed files:**
   ```
   Warning: Detected 15 .zig files but Zig analyzer not installed.
   Install hypergumbo-lang-extended1 for Zig support:
     pip install hypergumbo-lang-extended1
   ```

2. **Partial linker requirements:**
   ```
   Warning: JNI linker found 8 Java native methods but 0 C JNI functions.
   Is hypergumbo-lang-mainstream installed?
   ```

This uses the existing `LinkerRequirement` system in `linkers/registry.py`, extended with package-aware messaging.

### Smart Local Testing

A new `scripts/smart-test` script dogfoods hypergumbo to determine affected tests:

```bash
#!/bin/bash
# 1. Detect changed files via git diff
CHANGED=$(git diff --name-only $(git merge-base HEAD origin/dev)..HEAD)

# 2. Run STABLE hypergumbo (last release) to get call graph
#    Bootstrap safety: never use the version being tested
#    Stable version installed separately via: pipx install hypergumbo==X.Y.Z
STABLE_HYPERGUMBO="$HOME/.local/bin/hypergumbo"

# 3. Find files affected by changes (inherently does reverse dependency lookup)
$STABLE_HYPERGUMBO slice --files "$CHANGED" --output .ci/affected-tests.txt

# 4. Run only affected tests
pytest $(cat .ci/affected-tests.txt) --cov=src

# 5. Kick off full suite in background (singleton)
LOCK=".git/FULL_SUITE_RUNNING"
if [[ ! -f "$LOCK" ]]; then
    echo $$ > "$LOCK"
    (
        pytest --full -n 2 --cov=src --cov-fail-under=100
        rm -f "$LOCK"
        [[ $? -ne 0 ]] && notify-send "Full suite failed"
    ) &
    echo "Full suite started in background"
else
    echo "Full suite already running, skipped"
fi
```

### pytest Alias in venv

To ensure LLMs and developers with muscle memory get smart behavior by default, `scripts/install-hooks` injects an alias into the venv activate script:

```bash
# Added to .venv/bin/activate by install-hooks
alias pytest='./scripts/smart-test'  # smart-test alias
```

After running `./scripts/install-hooks`, developers must re-source the venv to enable the alias:

```bash
source .venv/bin/activate  # reload to enable pytest alias
pytest                      # now runs smart-test (affected tests only)
pytest --full               # runs complete test suite
command pytest              # bypasses alias, runs real pytest
```

The `smart-test` script passes through all pytest flags (e.g., `-x`, `-q`, `-n auto`) to pytest, so muscle memory works normally.

### Bootstrap Safety

Using hypergumbo to test hypergumbo could pose a bootstrap paradox. Mitigations:

1. **Always use last stable release** for test selection, never the version under test
2. **Full suite fallback** for changes to core analysis:
   - `sketch.py` (call graph construction)
   - `slice.py` (dependency traversal)
   - `analyze/all_analyzers.py` (analyzer dispatch; see [ADR-0012](0012-pass-unification-and-multi-fidelity.md) for the relationship between `all_analyzers.py` and `analyze/registry.py`)

### CI Architecture

#### Fast CI (Blocking)

```yaml
fast-ci:
  steps:
    - name: Check full suite status
      run: |
        # Use commit status API (works on both GitHub and Forgejo)
        # Note: The workflow runs API requires authentication that isn't
        # available in PR workflows. The commit status API works
        # unauthenticated for public repos.
        API_BASE="https://codeberg.org/api/v1/repos/${{ github.repository }}"
        BASE_SHA="${{ github.event.pull_request.base.sha }}"

        # Fetch commit status (no auth needed for public repos)
        STATUS_JSON=$(curl -s "$API_BASE/commits/$BASE_SHA/status")

        # Check only the aggregate job — it is the authoritative verdict.
        # Individual jobs like test-core may fail due to runner
        # timeouts while their retries succeed.
        RESULT=$(echo "$STATUS_JSON" | python3 -c '
        import json, sys, re
        d = json.load(sys.stdin)
        statuses = d.get("statuses", [])
        aggregate = [s for s in statuses
                     if "Full Test Suite / aggregate" in s.get("context", "")]
        if not aggregate:
            full_suite = [s for s in statuses
                          if "Full Test Suite" in s.get("context", "")]
            if not full_suite:
                print("none|unknown")
            else:
                print("none|pending")
            sys.exit(0)
        agg = aggregate[0]
        agg_status = agg.get("status", "unknown")
        if agg_status == "failure":
            url = agg.get("target_url", "")
            match = re.search(r"/runs/(\d+)/", url)
            run_id = match.group(1) if match else "unknown"
            print(f"{run_id}|failure")
        elif agg_status == "success":
            print("ok|success")
        else:
            print("none|pending")
        ')

        RUN_ID=$(echo "$RESULT" | cut -d'|' -f1)
        STATUS=$(echo "$RESULT" | cut -d'|' -f2)

        if [[ "$STATUS" == "failure" ]]; then
          REQUIRED_PREFIX="fix(job-$RUN_ID):"
          if [[ "${{ github.event.pull_request.title }}" != "$REQUIRED_PREFIX"* ]] && \
             [[ "${{ github.event.pull_request.title }}" != fix\(job-* ]]; then
            echo "❌ Full suite broken (job-$RUN_ID)"
            echo "   To submit a fix, PR title MUST start with:"
            echo "   $REQUIRED_PREFIX <description of fix>"
            exit 1
          fi
        fi

    - name: Sanity check manifest
      run: |
        # Verify locally-generated manifest isn't gaming the system
        MANIFEST=".ci/affected-tests.txt"
        CHANGED_MODULES=$(git diff --name-only $BASE..$HEAD | grep "^packages/" | cut -d/ -f2 | sort -u)

        for mod in $CHANGED_MODULES; do
          case $mod in
            hypergumbo-core)
              MIN_TESTS=20 ;;
            hypergumbo-lang-*)
              MIN_TESTS=10 ;;
            hypergumbo)
              MIN_TESTS=0 ;;  # Meta-package has no tests
            *)
              MIN_TESTS=5 ;;
          esac

          ACTUAL=$(grep -c "test_${mod}" "$MANIFEST" || echo 0)
          if [[ $ACTUAL -lt $MIN_TESTS ]]; then
            echo "❌ Manifest has $ACTUAL tests for $mod, expected ≥$MIN_TESTS"
            echo "   Falling back to module-based detection"
            # Run full module tests instead
            exit 1
          fi
        done

    - name: Run affected tests
      run: pytest --rootdir=. $(cat .ci/affected-tests.txt) --cov=packages --cov-fail-under=100
```

#### What the Manifest Carries

CI does not run `scripts/smart-test`; it runs the committed `.ci/affected-tests.txt`. So whatever `smart-test` writes there is the merge gate's selection, and two selections are kept apart in `scripts/smart-test` (pinned by `packages/hypergumbo-core/tests/test_smart_test_manifest_split.py`):

- **The manifest** carries the static slice of the changed files plus its declarative augmentations (conftest owners, coverage-only dependencies, and the like), narrowed by coverage and then widened by the read record and the import closure (all three below).
- **The local run** may differ for this machine only: coverage-directed selection adds tests to it. The addition never reaches the manifest.

**Coverage narrowing reaches the manifest, and is on by default.** `coverage-select narrow` removes a selected test file only when all three hold: the local coverage index has rows for it and no unmeasured test; its latest run in this checkout measured every package, the changed source files among them, and left rows for each of its tests; and it was never observed executing any block of a changed file. It never removes a changed file in the run set, nor a test the prose-only rule selected, and it refuses outright, keeping the whole selection, when the change touches import-time code (a module or class body), adds or deletes a file, or names a coverable source file the index has never seen.

- Narrowing runs above the manifest snapshot, so merge CI runs the narrowed set; the header line `# Narrowed: N` appears whenever it removed anything.
- `--no-narrow` turns it off for one run. It is off when `CI` is set: CI has no index and only consumes the manifest.
- The Woodpecker full-suite cron never reads the manifest, so it is never narrowed and stays the backstop for a test the index wrongly judged irrelevant.
- Expect it to act on a minority of changes: on 200 recent commits changing package source, about four in five change import-time code and are refused.
- The index records what each run measured (its checkout, the source files in coverage's own file list, and whether `smart-test` declared that every package was measured). Folding a run replaces a test's recorded edges only into the files that run measured, so a run that measured part of the source, or ran in another checkout, removes nothing it could not see.
- A test's having no row on a file is evidence that it does not run the file only when the test's latest run measured that file as part of a run that measured every package. A partial run does not vouch even for the files it measured: a new path from measured code into unmeasured code leaves no row. The local targeted run therefore measures every package ([ADR-0011](0011-scoped-coverage-and-green-baseline.md)).
- An index written before runs were recorded holds no latest run for any test, so no test file in it is dropped until it has run again; `coverage-select narrow` prints how many files it kept for that reason.
- Each fold removes the rows of checkouts that no longer exist on disk.
- Each test-to-block row also records which statements of the block the test ran, as a bitmap with one bit per statement. Statements are numbered by the same tree the block digest hashes (`hypergumbo_core.block_hash`), so a comment, a blank line or code added above the block moves no number, and a record made from other code is never read: a fold that sees a block's digest change drops that block's records (the rows stay), and a reader compares the stored digest with the source in front of it first. A row with no record, a row recorded as running none of the block's statements itself (an import-time block), and a row with statements are three different states. Test selection does not read the records; `coverage-select line-tests FILE:LINE` answers which tests ran a line and `coverage-select line-records` says how much of the index is recorded.

**The read record adds the tests that read a changed file from disk.** A test that checks source files by opening them (a gate that walks every analyzer, a test that greps a module) imports none of them, so the import graph does not select it when one of those files changes, and it has no coverage row on the file either. The read record kept for the prose-only rule (below) says which tests read which files; for every changed `.py` file whose code changed, `coverage-select readers` names the test files recorded reading it (`hypergumbo_core.prose_only.tests_reading`) and `smart-test` adds them.

- It runs above the manifest snapshot and after narrowing, so merge CI runs the additions and narrowing cannot remove them; the header line `# Read-record tests: N added` appears whenever it added anything.
- It only adds, so it uses the record as it stands: a stale record costs one unneeded test, a missing one leaves the selection as it was.
- Not covered: a read made by a process the test starts, which the recorder cannot see.
- `--no-read-union` turns it off for one run. It is off when `CI` is set.

**The import closure adds the tests that load a file whose import-time code changed.** The static slice is a reverse slice from the symbols of the changed files, so it follows call edges: it lists a module that imports a changed file, and reaches a test of that module only through a call into the changed file. A module that reads a constant of the changed file, a value it computed at import, or (in the 9.0.0 slice; 9.2.0 follows that read) an attribute of its module object has no such call, so its tests were left out (one constant changed in `schema.py`: 252 test files selected, and the one that fails was not among them). For every changed source file whose import-time code changed (a module or class body, the same test narrowing refuses on), `coverage-select import-closure` names every test file whose imports reach it through any number of files (`hypergumbo_core.import_closure`) and `smart-test` adds them.

- An import counts when it runs as its file is imported: at module level or in a class body, under `try` or `if` there, and in a decorator or a parameter default. An import inside a function body counts only in a file outside the source roots (a test, a `conftest.py`, a test helper), which is where a test or a fixture imports what it is about to use. Also counted: the packages above an imported module, `importlib.import_module` and `__import__` with a literal name, and for a test the `conftest.py` files above it.
- An import inside a function body of a source module (a file under `packages/<pkg>/src`) does not count. The root `conftest.py` imports `hypergumbo_core.analyze.registry` inside a fixture, which runs `hypergumbo_core/__init__.py`; that imports `ir`, `ir` imports `catalog` inside a function, and `catalog` imports `cli` inside a function. Counting those two would put everything `cli` imports under every test file: one changed constant in `schema.py` would select all 1,423 test files. It selects 314, the failing test of the case above among them.
- A known miss follows from that rule: a test that reaches the changed file only through a function-level import in a source module is not added. The smart-test bed holds the shape (`bedcore/reads_lazily.py` imports `limits` inside a function; `test_reads_lazily.py` fails when the constant changes and is not selected), and `tests/test_smart_test_bed.py` asserts it. How many test files of this repository are in that position for some change is not measured. The twice-daily full suite runs them.
- Not counted: the body of `if TYPE_CHECKING:`, and an import whose name is computed at runtime (an entry point, a registry of dotted strings). A test that reaches a file only that way is not added.
- A changed function body does not take the closure; the slice and the coverage record select for it. Not covered as a result: a function that another module calls while that module is being imported. A test of that module is not added when the function's body changes.
- A deleted source file takes it: the slice has no symbol left to start from, and the tests still importing the file are where the dangling import fails.
- It runs above the manifest snapshot and after narrowing, so merge CI runs the additions; the header line `# Import-closure tests: N added` appears whenever it added anything.
- It reads the working tree and needs no recorded evidence, so `CI` does not switch it off. `--no-import-union` turns it off for one run.
- Cost, replayed with the shipped module over the 90 pull requests that changed package source between 2026-10-30 and 2026-11-14: 9.8% more selected test time, 44 of the 90 selecting more, and a median change selecting 56% of the suite's test time, up from 52%. Counting function-level imports in source modules as well would cost 25.3% and a median of 68%. Module-level imports alone, everywhere, would cost 5.3% and leave out a test that imports inside its own function.

**Two recorders ride on every local run and change nothing about it.** Each appends to a log beside the selection index (`hypergumbo_core.selection_log`), each is off when `CI` is set, in a smart-test started by a test, and in target mode, and neither can fail the run.

- **The stale-graph shadow** (`--no-stale-shadow` turns it off). The reverse slice analyses the whole repository before it walks, and the analysis is nearly all of the time selection takes; the same walk on a graph already on disk takes seconds. Whenever a fresh slice is computed, the walk is repeated on the newest older graph of the same analyzer build, from the same changed source files, and both answers are stored in `stale_graph_observations.jsonl` with the older graph's commit and its age in commits. The result that counts against a stored graph is a test file the fresh walk names, the older graph's walk lacks, and the change did not itself edit: an edited or new test file always runs. A run with nothing to compare (the slice answered from a cached survey, no older graph is registered, the second walk failed) is stored with its reason and counts for nothing. `coverage-select stale-report` prints the verdict: thirty distinct commits compared and no such test file. No selection reads a stored graph.
- **The run record** (`--no-run-record` turns it off). Every run that reaches pytest, explicit-path runs included, appends to `run_records.jsonl` the commit, the merge-base, the files that differ from it, the test files that ran, the id of every test that did not pass, the exit status and how the tests were chosen (`selected`, `explicit` or `full`). When the tree holds uncommitted changes, the change itself is stored once under its digest in `run_records.d/`, because a tree that was never committed cannot be read back. A selector written later is judged by replaying it against these runs: did it select the tests that failed. A `--raw` run leaves no record.

**One exception: prose-only files.** A changed `.py` file is prose-only when its AST with docstrings removed, and its machine-read comments (`# pragma:`, `# covers:`, `# type:`, `# noqa` ...), equal the base's (`hypergumbo_core.prose_only.classify`). Such a file never enters the import-graph slice. Its tests come from recorded evidence instead (`select_for_prose`: the tests recorded reading the file, reading a changed docstring or its line numbers, or starting a process the recorder cannot see, plus any test file with no usable record), and those tests are what the manifest carries for it.

- The decision is per file, over the branch's diff against its base: in a mixed change the files whose code changed get the import-graph slice, and the manifest is the union of the two lists.
- The header line `# Prose-only files: N` appears whenever N > 0, so the exception is never silent.
- The rule gives way to the import graph for every file when more than 10% of test files lack a usable record, and `--no-prose-rule` turns it off for one run.
- The evidence is recorded on a developer's machine; the rule is off when `CI` is set, and CI only consumes the manifest.

The coverage side of the same exception is in [ADR-0011](0011-scoped-coverage-and-green-baseline.md).

#### Pytest Rootdir Pinning

**Invariant:** CI must invoke pytest with `--rootdir=.` to ensure the repo root is used regardless of which packages appear in the manifest.

**Why:** Manifest paths are repo-root-relative (`packages/hypergumbo-tracker/tests/test_tui.py`). When all selected tests belong to a single package, pytest's rootdir detection algorithm finds that package's `pyproject.toml` and uses the package subdirectory as rootdir. The repo-root-relative paths then fail to resolve, producing `0 items collected`.

Multi-package manifests avoid this by accident: the shared prefix (`packages/`) has no `pyproject.toml`, so pytest falls back to the repo root. Single-package PRs break silently.

**Fix:** Add `--rootdir=.` to the CI pytest invocation. This is a one-line change in `ci.yml` that makes the behavior explicit rather than relying on pytest's heuristic.

**Discovery:** 2026-04-02. Five consecutive CI failures on tracker-only PRs before the rootdir interaction was identified. Workaround was padding manifests with a cross-package test file — fragile and incorrect.

#### Full Suite (Lazy, Singleton, Non-blocking)

```yaml
full-suite:
  # Triggered after fast-ci passes, but non-blocking for PR merge
  concurrency:
    group: full-suite-singleton
    cancel-in-progress: false  # Don't cancel, just skip if running

  steps:
    - name: Run complete test suite
      run: |
        pytest packages/*/tests -n 2 --cov=packages --cov-fail-under=100
```

The `concurrency` group with `cancel-in-progress: false` ensures only one full suite runs at a time. Additional triggers queue but get deduplicated.

#### Stop-the-Line Protocol

When full suite fails:
1. All subsequent PRs fail fast-ci unless title starts with `fix(job-`; the failing run's own prefix `fix(job-XXXXX): ` is the one CI prints
2. This creates audit trail in commit history
3. Forces immediate attention to broken trunk
4. No weaseling allowed - the `fix(job-` prefix is required. Any job id is accepted, not only the failing run's, to avoid race conditions when multiple full-suite runs fail

Valid:
```
fix(job-12345): repair frobnitz null check
```

Invalid:
```
fix: repair frobnitz (job-12345)
feat: new thing (notwithstanding job-12345)
job-12345 is not my problem
```

### Reading Coverage Output

When coverage fails, missing lines appear in the pytest output in this format:
```
packages/.../file.py    191      1    99%   78
                        ^^^      ^    ^^^   ^^
                        stmts  miss  cover  MISSING LINES
```

The final column shows missing line numbers (e.g., `78` or `162-170, 180`). No separate tool is needed - just read the pytest output carefully, focusing on the coverage table at the end.

### Cross-Module Interaction Safety

Linkers operate on `LinkerContext.symbols` - the unified symbol graph from all installed analyzers. They don't import specific language modules. This means:

1. **Polyglot repos work:** Install core + mainstream + extended1, analyze Python + Zig repo, linkers see unified graph
2. **Graceful degradation:** Missing analyzer = files not analyzed, linkers work on what's present
3. **No cross-module test dependencies:** Language tests use synthetic symbols for linker integration

The lazy full-suite run catches any subtle cross-module regressions that smart testing might miss.

### Release Pipeline Changes

With 4 modules + 1 meta-package, the release pipeline must change:

**Version Synchronization:**
All 5 packages share the same version number. The meta-package pins exact versions:

```toml
# packages/hypergumbo/pyproject.toml (meta-package)
[project]
name = "hypergumbo"
version = "2.0.0"
dependencies = [
    "hypergumbo-core==2.0.0",
    "hypergumbo-lang-mainstream==2.0.0",
    "hypergumbo-lang-common==2.0.0",
    "hypergumbo-lang-extended1==2.0.0",
]
```

**Script Changes:**

| Script | Change |
|--------|--------|
| `prepare-release` | Update 5 pyproject.toml files, verify sync |
| `release-check` | Verify all 5 packages have matching versions |
| `bump-version` | Bump all 5 in lockstep |

**CI Release Workflow:**
```yaml
release:
  steps:
    # Publish in dependency order
    - run: twine upload packages/hypergumbo-core/dist/*
    - run: twine upload packages/hypergumbo-lang-mainstream/dist/*
    - run: twine upload packages/hypergumbo-lang-common/dist/*
    - run: twine upload packages/hypergumbo-lang-extended1/dist/*
    # Meta-package last (depends on all others)
    - run: twine upload packages/hypergumbo/dist/*
```

**Tagging:**
Single tag per release (`v2.0.0`), not per-package tags. All packages release together.

**PyPI Migration Considerations:**

The current `hypergumbo` package exists on PyPI. Transitioning to a meta-package requires care:

1. **Name reservation:** Register `hypergumbo-core`, `hypergumbo-lang-mainstream`, `hypergumbo-lang-common`, `hypergumbo-lang-extended1` on PyPI before the 2.0 release to prevent squatting.

2. **Upgrade path:** Users with `hypergumbo==1.x` must be able to `pip install --upgrade hypergumbo` and get 2.0 meta-package. The meta-package replaces the monolith; it doesn't conflict.

3. **Import path changes:** Clean break, no shims. This keeps the meta-package simple (just dependencies, no code).
   ```python
   # 1.x
   from hypergumbo.analyze.py import analyze_python

   # 2.x
   from hypergumbo_lang_mainstream.py import analyze_python
   ```
   Migration guide must document all import path changes.

4. **TestPyPI first:** Upload all 5 packages to TestPyPI and verify:
   - `pip install -i https://test.pypi.org/simple/ hypergumbo` pulls all 4 subpackages
   - Upgrade from 1.x works
   - No dependency resolution conflicts

5. **Don't yank 1.x:** Existing users may have version pins. Old versions stay available.

6. **Version floor:** Meta-package 2.0 should probably `Requires-Python: >=3.10` (same as current).

**CHANGELOG:**
Single unified CHANGELOG, not per-package. Sections can note which module a change affects:
```markdown
## [2.1.0] - 2026-03-15
### Added
- [lang-extended1] Zig framework detection for http.zig
- [core] New linker for WebTransport
```

### Validation Plan

**Status: VALIDATED (2026-02-02)**

The stop-the-line protocol was validated through the following tests:

**Test 1: Intentional Full Suite Failure** ✅
- Commit 4626a5d added `test_stop_the_line_validation.py` that passes locally but fails in full-suite
- Full suite failed as expected (run 1702)
- Failure was recorded in commit status API

**Test 2: Stop-the-Line Blocks PRs** ✅
- PR #785 (without escape hatch) was blocked by stop-the-line
- Error message correctly indicated the failing run ID

**Test 3: Fix PR Allowed Through** ✅
- PR #785 with title `fix(job-2508296): ...` passed stop-the-line
- Escape hatch pattern `fix(job-*)` correctly matched

**Test 4: Normal Operation Resumes** ✅ (pending PR #786 merge)
- PR #786 removes the intentional failure
- After merge, full suite will pass and stop-the-line will allow all PRs

**Test 5: Title Format**
The current implementation accepts:
- `fix(job-XXXXX): description` - exact match
- `fix(job-*` - any job ID (intentionally flexible)

Note: the check accepts any `fix(job-*)` pattern, not just the exact failing job ID, deliberately, to avoid race conditions when multiple full-suite runs fail.

## Consequences

### Positive
1. **Faster local feedback:** Smart testing runs only affected tests (~90% reduction)
2. **Faster CI:** Module-based parallelism (~70% reduction in wall-clock time)
3. **Architectural clarity:** Explicit module boundaries, documented dependencies
4. **Dogfooding:** Using hypergumbo to improve hypergumbo development

### Negative
1. **Migration effort:** Significant refactor to restructure into packages
2. **Packaging complexity:** 4 pyproject.toml files to maintain
3. **Bootstrap risk:** Smart testing depends on stable hypergumbo being correct
4. **Manifest gaming:** Malicious contributors could try to omit tests (mitigated by sanity checks)

### Neutral
1. **Release coordination:** Packages versioned together for simplicity
2. **Transparent to users:** `pip install hypergumbo` installs all 4 packages; modularization is internal DX, not user-facing UX

## Migration Path
1. Reserve package names on PyPI (`hypergumbo-core`, `hypergumbo-lang-*`)
2. Create `packages/` directory structure
3. Move source files, update imports
4. Create per-package pyproject.toml with dependencies
5. Update release scripts (`prepare-release`, `release-check`, `bump-version`)
6. Update CI workflow with module detection and stop-the-line protocol
7. Implement `scripts/smart-test` and pytest wrapper
8. Add runtime warnings for partial installs
9. Create `hypergumbo` meta-package (dependencies only, no code)
10. Write migration guide documenting import path changes
11. Test full release flow on TestPyPI
12. Execute Validation Plan (all 5 tests must pass)
13. Release as 2.0.0

## Implementation Notes

### Forgejo/Codeberg Compatibility (2026-02)

The original ADR specified using `gh api` to query workflow runs. This is GitHub-specific and doesn't work on Forgejo/Codeberg. Key learnings:

1. **Workflow Runs API requires authentication:** The `/repos/{owner}/{repo}/actions/runs` endpoint returns empty without a token. PR workflows can't access repository secrets for security reasons.

2. **Commit Status API works unauthenticated:** The `/repos/{owner}/{repo}/commits/{sha}/status` endpoint returns full data for public repos without authentication. This is the recommended approach.

3. **PR workflows use PR branch code:** When a PR runs, CI uses the workflow file from the PR branch (via merge commit), not the base branch. This enables bootstrapping but also means stop-the-line fixes must include the escape hatch title.

4. **Escape hatch pattern:** The pattern `fix(job-*)` allows any job ID, not just the specific failing one. This is intentional - it's more flexible and avoids race conditions when multiple full-suite runs fail.

### Validation Completed

The stop-the-line protocol was validated on 2026-02-02:
- PR #784: Test PR to verify blocking (closed without merge)
- PR #785: Fixed Forgejo API compatibility using commit status API
- PR #786: Removed intentional test failure after validation

## References
- ADR-0009: Feature bakeoff (dogfooding precedent)
- Toyota Production System: Andon cord / stop-the-line
- pytest-xdist: Parallel test execution
- Trunk-based development: https://trunkbaseddevelopment.com/
