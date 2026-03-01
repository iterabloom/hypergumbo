# ADR-0011: Scoped Coverage and Green Baseline Tracking

## Status

Implemented

## Context

With ADR-0010's smart-test running only affected tests, coverage enforcement became problematic:

1. **Coverage scope mismatch:** Running 2 test files that depend on `py.py` can't achieve 100% coverage for the entire `hypergumbo-lang-mainstream` package (9406 lines). The tests might cover 100% of `py.py` specifically, but only 30% of the whole package.

2. **Baseline instability:** Comparing against `merge-base` with dev works, but if a coverage gap slips through (e.g., cross-package coverage issues), the baseline itself may not represent a 100% coverage state.

3. **CI/local divergence:** CI tests each package in isolation. Local runs test all packages together. This led to cross-package coverage dependencies that passed locally but failed in CI.

The original plan (from earlier discussion) was:
- Track a "last green coverage" marker - the most recent commit where CI achieved 100%
- Use that marker as the baseline for comparison
- Measure 100% coverage for only the changed source files, not the entire codebase

## Decision

We implement a two-part system:

### Part 1: CI Tracks "Last Green" Commits

When the full suite achieves 100% coverage for all 4 packages, CI writes the commit SHA to a marker file on the `badges` branch:

```yaml
# In full-suite.yml aggregate job
- name: Update coverage badge and marker
  if: steps.results.outputs.conclusion == 'success'
  run: |
    # Check if all packages have 100% coverage
    if [ "$COV_CORE" = "100" ] && [ "$COV_MAINSTREAM" = "100" ] && \
       [ "$COV_COMMON" = "100" ] && [ "$COV_EXTENDED" = "100" ]; then
      echo "${{ github.sha }}" > /tmp/last-green-sha.txt
    fi

    # Push to badges branch
    git checkout badges
    cp /tmp/last-green-sha.txt last-green-sha.txt
    git add last-green-sha.txt
    git commit -m "Update last-green-sha marker"
    git push origin badges
```

The `badges` branch is an orphan branch containing only:
- `coverage.json` - Badge data for shields.io
- `last-green-sha.txt` - The SHA of the last commit where all packages hit 100%

### Part 2: smart-test Uses the Marker and Scopes Coverage

When running affected tests, smart-test:

1. **Fetches the baseline from badges branch:**
   ```bash
   get_baseline() {
       git fetch origin badges:refs/remotes/origin/badges 2>/dev/null
       last_green=$(git show origin/badges:last-green-sha.txt 2>/dev/null)
       if [[ -n "$last_green" ]]; then
           echo "$last_green"
       else
           # Fall back to merge-base
           git merge-base HEAD origin/dev
       fi
   }
   ```

2. **Detects changes from all sources:**
   ```bash
   # Three sources of changes:
   # 1. Committed: git diff BASELINE..HEAD
   # 2. Staged: git diff --cached (added but not committed)
   # 3. Unstaged: git diff (modified but not added)
   COMMITTED=$(git diff --name-only "$BASELINE"..HEAD)
   STAGED=$(git diff --name-only --cached)
   UNSTAGED=$(git diff --name-only)
   CHANGED_FILES=$(printf "%s\n%s\n%s" "$COMMITTED" "$STAGED" "$UNSTAGED" | sort -u)
   ```
   This ensures smart-test sees changes regardless of when pytest runs in the workflow (before or after staging).

3. **Scopes coverage to affected packages:**
   ```bash
   # Extract unique package source directories containing changed files
   pkg_dirs=$(echo "$CHANGED_FILES" |
              grep -E '^packages/.*/src/.*\.py$' |
              sed 's|^\(packages/[^/]*/src\)/.*|\1|' |
              sort -u)

   # Build --cov arguments for just those packages
   for pkg_dir in $pkg_dirs; do
       COV_PATHS+=("--cov=$pkg_dir")
   done
   ```

4. **Suppresses misleading package-level coverage:**
   ```bash
   # Suppress pytest-cov terminal report (misleading % when running subset of tests)
   COV_PATHS+=("--cov-report=")
   ```

5. **Enforces 100% for changed files only:**
   ```bash
   # After tests pass, check coverage for just the changed source files
   CHANGED_SOURCE_FILES=$(echo "$CHANGED_FILES" | grep -E '^packages/.*/src/.*\.py$')
   INCLUDE_PATTERN=$(echo "$CHANGED_SOURCE_FILES" | tr '\n' ',')

   coverage report --include="$INCLUDE_PATTERN" --fail-under=100
   ```

6. **Writes changed source files to manifest:**
   The manifest (`.ci/affected-tests.txt`) includes a `CHANGED_SOURCE_FILES` section so CI can perform the same scoped coverage check without recomputing changed files.

### Part 3: CI Uses the Manifest for Scoped Coverage

The `ci.yml` workflow reads the manifest and performs the same scoped coverage check:

1. Reads `CHANGED_SOURCE_FILES` from the manifest
2. Sanity-checks against `git diff` (warns if mismatch, falls back to PR diff)
3. **Skips pytest entirely for infrastructure-only PRs** (no Python source files changed)
4. Runs affected tests with coverage collection (if Python files changed)
5. Checks 100% coverage for changed source files only

This ensures the same coverage rules apply both locally and on CI.

### Part 4: Infrastructure-Only PRs Skip pytest

When a PR changes only shell scripts, YAML, or config files (no Python source):
- There's no coverage to verify (no Python code changed)
- Running 5000+ tests wastes CI time and risks timeouts
- `full-suite.yml` runs after merge anyway as a safety net

CI gives a "provisional pass":
```
✅ No Python source files changed - skipping pytest
   Changes are to infrastructure/config files only.
   Full suite will run via full-suite.yml after merge.
```

This matches the intended architecture:
- `ci.yml`: Fast validation (affected tests or skip if no Python)
- `full-suite.yml`: Comprehensive validation after merge

### Coverage Modes

| Mode | Test Scope | Coverage Scope | Threshold |
|------|------------|----------------|-----------|
| `pytest` (affected-only) | Tests depending on changed files | Changed source files only | 100% |
| `pytest --full` | All tests | All packages | 100% |

### Per-Package Isolation Check

A new script `scripts/check-package-coverage` validates each package achieves 100% when tested in isolation (mimicking CI):

```bash
./scripts/check-package-coverage           # Check all packages
./scripts/check-package-coverage core      # Check only hypergumbo-core
./scripts/check-package-coverage --quick   # Skip extended1 (faster)
```

This catches cross-package coverage dependencies before pushing.

## Consequences

### Positive

1. **Fast feedback with enforcement:** 183 tests in 20s vs 5700+ in 2min, while still enforcing 100% for changed code
2. **Stable baseline:** Comparisons are against a known 100% state, not just any merge-base
3. **Clear error messages:** When coverage fails, shows exactly which lines are missing in the changed files
4. **CI/local parity:** `check-package-coverage` catches isolation issues locally

### Negative

1. **badges branch complexity:** Requires maintaining an orphan branch for markers
2. **Network dependency:** Fetching the marker requires network access (falls back gracefully)
3. **Two-step coverage check:** pytest runs first, then separate `coverage report` command

### Neutral

1. **Full suite still required before merge:** Scoped coverage catches most issues, but full suite via CI remains the final gate
2. **Marker lag:** If CI is slow, the marker may be a few commits behind

## Example Output

```
[smart-test] Using last-green-sha marker: 5718a11e1429664a0d5c39f42d2eb04cc2b200d1
[smart-test] Changed files since 5718a11e1429664a0d5c39f42d2eb04cc2b200d1:
  packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
[smart-test] Found 2 affected test files
[smart-test] Scoping coverage to 1 package(s) containing 1 changed source file(s)

Running 2 affected test files with scoped coverage
  (enforcing 100% coverage for changed source files only)
Running tests... (output saved to .ci/pytest-output.log)

✅ 183 passed, 59 warnings in 19.91s

Checking 100% coverage for changed source files...
packages/.../py.py  770  0  100%
✅ All changed source files are at 100% coverage
```

## Test Placement Guidelines

To prevent cross-package coverage issues:

1. **Tests belong with the code they cover:** If a test imports from `hypergumbo_lang_mainstream`, it belongs in `packages/hypergumbo-lang-mainstream/tests/`

2. **Subprocess tests don't contribute to coverage:** Tests using `subprocess.run([sys.executable, "-m", "hypergumbo", ...])` don't contribute to pytest-cov. Call functions directly when possible.

3. **Verify before pushing:** Run `./scripts/check-package-coverage` to catch issues locally.

See AGENTS.md "Test Placement Guidelines" for full documentation.

## Implementation Notes

### CI Consistency Improvements

As part of this work, we also fixed inconsistencies in the CI workflows:

1. **Consistent `.coveragerc.no-embeddings` usage:** All four test jobs in `full-suite.yml` (test-core, test-mainstream, test-common, test-extended) now check for sentence-transformers availability and use the no-embeddings coverage config when unavailable. Previously only test-core did this.

2. **Bootstrap mode removal:** The bootstrap mode code paths in `ci.yml` were removed. These existed for the period when stable hypergumbo lacked the `slice --files` flag. Since stable now includes this feature, the bootstrap fallbacks are obsolete.

## References

- ADR-0010: Modular Packages and Smart Testing
- coverage.py: `--include` and `--fail-under` options
- pytest-cov: Coverage plugin for pytest
