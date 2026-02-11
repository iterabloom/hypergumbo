# ADR-0010: Modular Packages and Smart Testing

## Status

Implemented

## Context

The hypergumbo test suite has grown to 5700+ tests across 108 language analyzers. CI runs on resource-constrained runners (`codeberg-small-lazy`, limited to `-n 2` parallelism) are timing out or taking too long. We need faster feedback without:

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
      linkers/           # All cross-language linkers
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

        # Parse to find Full Test Suite failures
        RESULT=$(echo "$STATUS_JSON" | python3 -c '
        import json, sys, re
        d = json.load(sys.stdin)
        full_suite = [s for s in d.get("statuses", [])
                      if "Full Test Suite" in s.get("context", "")]
        failed = [s for s in full_suite if s.get("status") == "failure"]
        if failed:
            url = failed[0].get("target_url", "")
            match = re.search(r"/runs/(\d+)/", url)
            run_id = match.group(1) if match else "unknown"
            print(f"{run_id}|failure")
        elif full_suite:
            print("ok|success")
        else:
            print("none|unknown")
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
      run: pytest $(cat .ci/affected-tests.txt) --cov=packages --cov-fail-under=100
```

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
1. All subsequent PRs fail fast-ci unless title starts with `fix(job-XXXXX): `
2. This creates audit trail in commit history
3. Forces immediate attention to broken trunk
4. No weaseling allowed - exact prefix required

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

Note: The implementation is more permissive than originally specified. It allows any `fix(job-*)` pattern, not just the exact failing job ID. This is intentional to avoid race conditions when multiple full-suite runs fail.

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
