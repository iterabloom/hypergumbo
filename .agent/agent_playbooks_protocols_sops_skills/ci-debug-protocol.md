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

**CI workflow topology:**
- **`ci.yml`**: Fast per-PR check (smart-test on changed packages). Gates merge.
- **`full-suite.yml`**: Periodic validation (every 4 hours + manual dispatch). Runs all packages in parallel. Does NOT trigger on push to dev — with 20+ merges/day and singleton concurrency, the queue never clears. Stop-the-line fires from scheduled runs, so there may be a delay between a breaking merge and the andon cord — this is expected, not a bug.
- **`nightly.yml`**: Runs at 11 PM UTC. Multi-Python matrix (3.10–3.13) and integration tests. Sets commit statuses (`nightly/test-matrix`, `nightly/integration-tests`) so release.yml can skip them when the release SHA was already covered. `ci-debug status` works for nightly runs too.
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
