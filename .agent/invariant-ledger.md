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
- **Status:** FIXED
- **Root cause:** `symbol_ref` gate prevented UsageContexts with `symbol_ref=None` from
  enriching symbols. This happens when analyzers extract string-based handler references
  (e.g., Django URL patterns like `path('users/', 'views.user_list')`) where the target
  symbol is in a different file not yet analyzed.
- **Fix:** Added deferred resolution phase `resolve_deferred_symbol_refs()` that runs
  BEFORE enrichment. After all analyzers complete, we have the complete symbol table.
  The function:
  1. Builds a `NameResolver` from all symbols (by name and qualified_name)
  2. For each UsageContext with `symbol_ref=None`, extracts resolution hints from metadata
  3. Uses multi-strategy lookup: exact match → suffix match → path hint disambiguation
  4. Updates `symbol_ref` directly on the UsageContext when resolved
- **Architecture decision:** Chose deferred resolution (Option 2) over two-pass analysis
  or refactoring analyzers because it:
  - Has access to complete symbol table (enables cross-file resolution)
  - Leverages existing `NameResolver` infrastructure
  - Single point of resolution logic (DRY)
  - Minimal disruption to existing code
- **Regression tests:**
  - `test_framework_patterns.py::TestResolveDeferredSymbolRefs` (17 tests covering
    exact match, suffix match, path hint, ambiguous, multiple metadata keys)
  - `test_framework_patterns.py::TestEnrichSymbolsWithUsageContexts::test_inv002_fallback_resolution_by_view_name`

## INV-003: Python Nested Decorated Function Extraction
- **Statement:** Decorated nested functions must be extracted for framework pattern matching
- **Status:** FIXED
- **Root cause:** `analyze/py.py:1259` had `if node.col_offset == 0:` which filtered out all
  nested functions. This was intended to skip methods (already processed via class body
  iteration), but also skipped legitimate nested functions like FastAPI route handlers
  inside factory functions:
  ```python
  def get_router():
      router = APIRouter()
      @router.get("/items")  # This was NOT extracted
      def list_items(): ...
  ```
- **Fix:** Added `processed_functions` set to track already-extracted methods, then modified
  the condition to extract:
  1. Top-level functions (col_offset == 0) - always extract
  2. Nested functions with decorators - extract for framework patterns
  Also extended to handle `AsyncFunctionDef` for both methods and nested functions.
- **Verification:** Checked analogous patterns in other analyzers:
  - JS/TS: Already handles arrow functions in callbacks via position-based lookup
  - Ruby: Handles blocks properly for Sinatra/Rails patterns
  - Go: Handles `func_literal` (anonymous functions) properly
  - Issue was Python-specific due to the `col_offset` heuristic
- **Regression tests:**
  - `test_python_ast_analysis.py::TestNestedFunctionExtraction` (4 tests)

## INV-004: Template for New Invariants
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]
