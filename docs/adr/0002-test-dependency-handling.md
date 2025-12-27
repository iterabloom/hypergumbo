# 2. Test Dependency Handling

Date: 2025-12-27
Status: Accepted

## Context

Hypergumbo depends on numerous tree-sitter grammar packages for language analysis (tree-sitter-language-pack, tree-sitter-rust, tree-sitter-go, etc.). These are native packages that require compilation, though modern releases publish pre-compiled wheels for all major platforms.

The test suite historically used "escape hatches" - mechanisms to skip tests when grammar packages were unavailable. Three inconsistent patterns existed:

1. **Module-level skipif**: `pytestmark = pytest.mark.skipif(not is_available(), ...)`
2. **Per-test skipif**: `@pytest.mark.skipif(not AVAILABLE, ...)`
3. **Runtime skip**: `if result.skipped: pytest.skip(...)`

The rationale was resilience: if a grammar failed to load, tests would skip rather than fail.

## Decision

We will **remove all escape hatches** from the test suite. Tests assume dependencies work. If a dependency breaks:

1. CI fails loudly
2. We pin to the last known good version (e.g., `tree-sitter-language-pack==0.13.0`)
3. We document the pin with a comment explaining why
4. CI returns to green

Graceful degradation behavior (analyzers returning `skipped=True` when grammars are unavailable) is tested via mocking, not by actually running with missing dependencies.

### Rationale

1. **End users don't run tests.** They `pip install hypergumbo` and get pre-compiled wheels. The test suite exists for CI and contributors, both of whom should have complete, working environments.

2. **Escape hatches hide problems.** "247 passed, 23 skipped" is ambiguous. Did those tests skip intentionally or because something is broken? Silent degradation can persist unnoticed for months.

3. **Pinning is explicit.** When upstream breaks, pinning creates a clear, documented record. The friction of having to pin forces acknowledgment and eventual resolution.

4. **Simplicity.** Three inconsistent patterns add cognitive overhead. "Tests assume deps work" is one simple rule.

5. **100% means 100%.** Our coverage mandate requires all code paths be tested. Skipped tests create ambiguity about what's actually covered.

## Consequences

### Positive

* **Clear CI signal.** Green means everything works. Red means something is broken.
* **No hidden failures.** Problems surface immediately rather than hiding behind skips.
* **Simpler test code.** No availability checks, no conditional skips, no multiple patterns.
* **Explicit dependency management.** Version pins document known issues.

### Negative

* **Upstream breakage blocks CI.** If a grammar package releases a broken version, PRs are blocked until we pin. This is typically a 5-minute fix.
* **No partial test runs.** Contributors must have all dependencies installed. This is already the expectation given they're in `[project.dependencies]`.

## References

* All tree-sitter packages are listed in `pyproject.toml` under `[project.dependencies]`
* Graceful degradation is tested via mocking (e.g., `monkeypatch.setattr(dart, "is_dart_tree_sitter_available", lambda: False)`)
