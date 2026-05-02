<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## CI Debugging Protocol
When CI fails but tests pass locally, use `./scripts/ci-debug`:

```bash
# List recent CI runs (shows status, commit SHA)
./scripts/ci-debug runs

# Check status of current commit
./scripts/ci-debug status

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
- **`ci.yml`**: Fast per-PR check (smart-test on changed packages). Gates merge.
- **`full-suite.yml`**: Periodic validation (twice daily at 01:00 and 13:00 UTC + manual dispatch). Runs all packages in parallel. Does NOT trigger on push to dev — with 20+ merges/day and singleton concurrency, the queue never clears. Stop-the-line fires from scheduled runs, so there may be a delay between a breaking merge and the andon cord — this is expected, not a bug.
- **`nightly.yml`**: Runs at 5:30 AM UTC. Multi-Python matrix (3.10–3.13) and integration tests. Sets commit statuses (`nightly/test-matrix`, `nightly/integration-tests`) so release.yml can skip them when the release SHA was already covered. `ci-debug status` works for nightly runs too.
- **`release.yml`**: Triggered by version tag push or manual dispatch. Security audit is a hard gate before publish. Test-matrix and integration-tests are deferred: if nightly already covered the SHA they're skipped, otherwise they run post-publish as verification.

**Common root causes**:
- **Missing dependencies**: Analyzer uses a package not listed in `pyproject.toml`
- **Version mismatch**: CI has different package versions than local
- **Platform differences**: Some packages don't have wheels for CI's platform

**Dependency verification**:
- Use `./scripts/ci-debug analyze-deps` to compare imports vs pyproject.toml
- Use `pip index versions tree-sitter-<lang>` to verify package exists on PyPI

**The escape hatch policy** (see ADR 0002):
- Tests assume dependencies work; they do NOT skip when dependencies fail
- If a dependency breaks upstream, pin to a known-good version in `pyproject.toml`
- Document the pin with a comment
- Never hide failures with pytest.skip() patterns
