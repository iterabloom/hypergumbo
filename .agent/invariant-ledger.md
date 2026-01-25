# Invariant Ledger

This ledger tracks discovered invariants, their status, and regression tests.
See [ADR-0008](../docs/adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for governance context.

## INV-001: Call Attribution Completeness
- **Statement:** Every emitted `calls` edge has a non-null caller symbol
- **Status:** PARTIALLY ADDRESSED
- **Root cause:** JS/TS arrow function special-case early-return in `_get_enclosing_function()`
- **Fix:** Position-based lookup in `_get_enclosing_function()` for arrow functions
- **Limitation:** JS/TS only; Kotlin/Scala lambdas still vulnerable
- **Regression tests:** `test_js_ts.py::TestCallbackCallAttribution`

## INV-002: Usage-to-Concept Flow
- **Statement:** Usage patterns extracted by analyzers become concepts on nodes
- **Status:** UNFIXED
- **Root cause:** `symbol_ref` gate at `framework_patterns.py:992-993`
- **Workarounds:**
  - Rails: Direct Symbol creation (bypasses UsageContext flow)
  - Library exports: Set `symbol_ref` when name resolves
- **Affected frameworks:** Rails, Django string views, any string-based handler reference
- **Regression tests:** `test_ruby.py::test_rails_routes` (tests workaround, not fix)

## INV-003: Template for New Invariants
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]
