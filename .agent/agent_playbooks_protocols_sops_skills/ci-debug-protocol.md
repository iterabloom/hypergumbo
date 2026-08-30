<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## CI Debugging Protocol
When CI fails but tests pass locally, use `./scripts/ci-debug`:

```bash
# List recent CI runs (shows status, commit SHA)
./scripts/ci-debug runs

# Check status of current commit
./scripts/ci-debug status

# Latest CRON verdict per workflow, whatever commit carries it (INV-bozid).
# `status` renders HEAD ONLY, and a cron status attaches to whichever commit was
# dev's tip when the cron fired — so `status` can report a clean HEAD while
# carrying NO information about the last full-suite or nightly run, in either
# direction. Exit codes are distinct on purpose: 0 all green, 1 a gate FAILED,
# 2 a gate has NO READABLE VERDICT in the window (silence is not green).
./scripts/ci-debug cron-status              # default: origin/dev, 40 commits
./scripts/ci-debug cron-status origin/dev 60

# Analyze tree-sitter dependencies (finds missing packages)
./scripts/ci-debug analyze-deps
```

**`auto-pr` exit-code recovery.** When `./scripts/auto-pr` exits non-zero:

- **Exit 0:** Success — PR merged or vPR queued. If vPR queued, run `./scripts/auto-pr flush` when remote is available.
- **Exit 1:** Failure. Run `./scripts/ci-debug status` to diagnose, fix the issue, then either re-run `./scripts/auto-pr` or `./scripts/merge-pr <PR_NUM> --wait-for-ci`.
- **Exit 2:** Timeout (CI stuck or slow). Try `./scripts/merge-pr <PR_NUM> --wait-for-ci --timeout 3600`, or if CI already passed, `./scripts/merge-pr <PR_NUM>` to merge immediately. If CI remains stuck, follow Scenario B.
- **Exit 3:** Hung (no CI jobs started after 5 min). `auto-pr` already retried with exponential backoff (close PR, wait, repush — up to 4 times). All retries failed, meaning CI runners may be down. Follow Scenario B. Do NOT manually kill processes, clear PR_PENDING, or start new branches.

**Scenario B (CI stuck after timeout).** Do NOT accumulate more changes to git-tracked hypergumbo code. Run `./scripts/ci-debug status` once per hour (manually, not in a loop). When CI recovers, use `./scripts/merge-pr <PR_NUM>` to merge. It is fine to wait.

**CI workflow topology:**
- **`ci.yml`**: Fast per-PR check (smart-test on changed packages). Gates merge. **The self-claim drift gate is NOT here** — it was removed from the per-PR pipeline on 2026-08-30 (owner decision) once it became the critical path at ~14 min, against the 3m49s its move into that pipeline was justified on. A self-claim regression is therefore no longer merge-blocking; it surfaces on the next full-suite cron, up to ~12h later.
- **`full-suite.yml`**: Periodic validation (twice daily at 01:00 and 13:00 UTC + manual dispatch). **Now the ONLY arm of the self-claim drift gate.** Its six steps collapse into one aggregate commit status, so a drift verdict reads as "full-suite is red" and needs a log fetch to attribute — use `ci-debug cron-status` to find the verdict, then `ci-debug logs` to attribute it. If full-suite starts sitting chronically red, that gate needs its own workflow file and its own status context (INV-bozid remedy (a)), or it is masked exactly as it was for 63 commits under INV-bobor. Runs all packages in parallel. Does NOT trigger on push to dev — with 20+ merges/day and singleton concurrency, the queue never clears. Stop-the-line fires from scheduled runs, so there may be a delay between a breaking merge and the andon cord — this is expected, not a bug.
- **`nightly.yml`**: Runs at 5:30 AM UTC. Multi-Python matrix (3.10–3.13) and integration tests, so release.yml can skip them when the release SHA was already covered. **It publishes ONE COMMIT STATUS PER MATRIX LEG** — `ci/woodpecker/cron/nightly/1` … `/4`, and no bare `.../nightly` context. This line previously named the contexts `nightly/test-matrix` / `nightly/integration-tests` and claimed "`ci-debug status` works for nightly runs too"; both were false and are corrected here (INV-bozid). `status` renders HEAD only, and a nightly verdict lands on whatever commit was tip at 5:30 — use **`ci-debug cron-status`**, which finds it wherever it is and names the failing leg. Measured the day that landed: leg 1 was FAILING sixteen commits back and `status` said nothing about it.
- **`release.yml`**: Triggered by version tag push or manual dispatch. Security audit is a hard gate before publish. Test-matrix and integration-tests are deferred: if nightly already covered the SHA they're skipped, otherwise they run post-publish as verification.

**Common root causes**:
- **Missing dependencies**: Analyzer uses a package not listed in `pyproject.toml`
- **Version mismatch**: CI has different package versions than local
- **Platform differences**: Some packages don't have wheels for CI's platform

**Dependency verification**:
- Use `./scripts/ci-debug analyze-deps` to compare imports vs pyproject.toml
- Use `pip index versions tree-sitter-<lang>` to verify package exists on PyPI

**The escape-hatch policy** (no `skipif` / `pytest.skip()` patterns):
- Tests assume dependencies work; they do **NOT** skip when dependencies fail. If a grammar package, tree-sitter wheel, or other runtime dep breaks upstream, CI must fail loudly rather than silently green.
- Recovery procedure when an upstream dep breaks: pin to the last known-good version in `pyproject.toml` (e.g. `tree-sitter-language-pack==0.13.0`), add a comment naming the upstream issue or PR that motivated the pin, and ship the pin in its own PR so CI returns to green.
- Graceful-degradation behavior (analyzers returning `skipped=True` when an optional grammar is unavailable) is tested via **mocking**, never by running the test suite with the grammar genuinely missing.
- Three escape-hatch shapes that must not appear in test files:
  - module-level `pytestmark = pytest.mark.skipif(not is_available(), ...)`
  - per-test `@pytest.mark.skipif(not AVAILABLE, ...)`
  - runtime `if result.skipped: pytest.skip(...)`
- The same logic applies to optional dependency extras (`hypergumbo[rust-analyzer]`, etc.) — write real tests against the installed code path plus a separate mock test that exercises the "extra not installed" branch. Detail in the optional-dependency-testing playbook.
