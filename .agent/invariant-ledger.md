# Invariant Ledger

This ledger tracks discovered invariants, their status, and regression tests.
See [ADR-0008](../docs/adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for governance context.

## INV-001: Call Attribution Completeness
- **Statement:** Every emitted `calls` edge has a non-null caller symbol
- **Status:** FIXED
- **Root cause:** JS/TS arrow function special-case early-return in `_get_enclosing_function()`
- **Fix:** Position-based lookup in `_get_enclosing_function()` for arrow functions
- **Verification:** Kotlin and Scala lambdas work correctly - their `_get_enclosing_function()`
  walks up past `lambda_literal` (Kotlin) and `lambda_expression` (Scala) to find the enclosing
  `function_declaration` or `function_definition`.
- **Regression tests:**
  - `test_js_ts.py::TestCallbackCallAttribution`
  - `test_kotlin.py::TestKotlinLambdaCallAttribution` (4 tests)
  - Manual verification for Scala (2026-01-25)

## INV-002: Usage-to-Concept Flow
- **Statement:** Usage patterns extracted by analyzers become concepts on nodes
- **Status:** PARTIALLY ADDRESSED
- **Root cause:** `symbol_ref` gate at `framework_patterns.py:992-993`
- **Fix:** Added name-based fallback resolution using `view_name` from metadata
- **How it works:** When `symbol_ref` is None, the enrichment phase tries to resolve
  the handler by looking up `view_name` from metadata in `symbol_by_name`
- **Limitation:** Only works when `view_name` is present in metadata and the target
  symbol has a matching name. Cross-file resolution depends on analyzer extracting
  view_name and the target symbol being in the same analysis run.
- **Workarounds (still in use):**
  - Rails: Direct Symbol creation (bypasses UsageContext flow)
  - Library exports: Set `symbol_ref` when name resolves
- **Affected frameworks:** Rails, Django string views, any string-based handler reference
- **Regression tests:**
  - `test_framework_patterns.py::TestEnrichSymbolsWithUsageContexts::test_inv002_fallback_resolution_by_view_name`
  - `test_ruby.py::test_rails_routes` (tests workaround, not fix)

## INV-003: Template for New Invariants
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]
