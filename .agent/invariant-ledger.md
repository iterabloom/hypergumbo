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

## INV-004: Route-to-Handler Edge Completeness
- **Statement:** Routes should have edges to their handler functions when handler info is available
- **Status:** FIXED
- **Root cause:** Analyzers store handler information in metadata (e.g., `controller_action`,
  `view_name`) but no edges are created from routes to handlers. Analysis showed only 15.8%
  of routes across bakeoff artifacts had outgoing edges:
  - Ruby: `analyze/ruby.py:392` sets `symbol_ref=None` with comment "Route DSL doesn't
    reference a handler symbol directly" - but `controller_action` IS stored in metadata
  - Elixir: `analyze/elixir.py:403` sets `symbol_ref=None` with comment "Router DSL
    doesn't directly reference symbols"
- **Fix:** Created `linkers/route_handler.py` that:
  1. Finds route symbols with handler metadata (`controller_action`, `controller`+`action`)
  2. Resolves handler references to actual method/function symbols using name matching
  3. Creates `routes_to` edges (0.9 confidence) connecting routes to handlers
  Supported frameworks:
  - Rails: `controller_action = "users#index"` → `UsersController#index`
  - Phoenix: `controller` + `action` fields → `Controller.action`
- **Regression tests:**
  - `tests/test_route_handler_linker.py::TestRouteHandlerLinker` (13 tests)
  - `tests/test_route_handler_linker.py::TestLinkerEntryPoint` (2 tests)

## INV-005: Edge ID Uniqueness
- **Statement:** Edge IDs must be unique because they serve as primary keys for edge lookup
- **Status:** FIXED
- **Root cause:** `ir.py:345` Edge.create() generated ID from `src:dst:edge_type` only, not including
  line number. Multiple calls from the same function to the same target at different lines got
  identical IDs. Analysis of postal artifact showed 3584 duplicate IDs out of 5844 edges (61%).
- **Fix:** Changed edge ID hash to include line number: `f"{src}:{dst}:{edge_type}:{line}"`.
  The `edge_key` field remains unchanged (excludes line) for deduplication across passes.
- **Regression tests:**
  - `tests/test_ir.py::test_edge_id_unique_per_line`

## INV-006: Rails Resource Route Handler Resolution
- **Statement:** Rails resource routes should have handler metadata for route-handler linking
- **Status:** FIXED (with full RESTful expansion)
- **Root cause:** Ruby analyzer only extracted `controller_action` from explicit `to: "controller#action"`
  syntax. `resources :users` and `resource :profile` macros didn't get controller_action metadata.
- **Original fix:** Inferred `controller_action` for resources/resource routes to enable linking.
- **Enhanced fix (v2):** Full RESTful route expansion:
  - `resources :users` → 7 route symbols (index, show, new, create, edit, update, destroy)
  - `resource :profile` → 6 route symbols (show, new, create, edit, update, destroy - no index)
  Each route symbol has correct HTTP method, path, and controller_action, enabling the
  route-handler linker to connect to ALL controller actions, not just index.
- **Limitation:** None - all standard RESTful actions are now linked.
- **Regression tests:**
  - `tests/test_ruby.py::TestRailsUsageContext::test_rails_resources_route`
  - `tests/test_ruby.py::TestRailsUsageContext::test_rails_resource_singular`
  - `tests/test_ruby.py::TestRailsRouteSymbols::test_route_symbols_for_resources_macro`
  - `tests/test_ruby.py::TestRailsRouteSymbols::test_route_symbols_for_resource_singular`

---

## Meta-Invariants (Consolidated Principles)

Meta-invariants are broad principles that unify specific invariants. Because they are
high-level, their status is expressed as a percentage indicating confidence they are upheld.

**Status values for meta-invariants:**
- `100%` — Based upon EXTENSIVE checking, fully upheld across ALL reasonably conceivable cases
- `<100%` (e.g., `80%`) — Partially upheld; known gaps exist. OR: as an extensive audit has yet to be attempted, unseen gaps might conceivably exist.
- `TBD` — Not yet assessed; needs research

### META-001: Metadata Must Become Graph Structure
> "Semantic relationships expressed in metadata must become traversable graph structure."

- **Status:** 100%
- **Notes:**
  - **DONE (create edges from base_classes):** Java, JS/TS, Python, Ruby, Kotlin, C#, Scala, PHP, Groovy, Swift, C++, Objective-C, Apex
  - All 13 languages with class inheritance now extract `base_classes` metadata
  - The centralized inheritance linker (`linkers/inheritance.py`) creates extends/implements edges for all languages

**Unified by:**
- INV-002 (usage patterns → concepts on nodes)
- INV-004 (route metadata → handler edges)
- INV-006 (resources macro → route symbols with controller_action)
- INV-008 (base_classes → extends/implements edges for Python/JS/TS)
- INV-009 (base_classes → extends/implements edges for Ruby/Kotlin)
- INV-010 (Django view_name → routes_to edges)

**Implication:** When an analyzer stores relationship information in metadata (view_name,
controller_action, etc.), there should be a corresponding linker or enrichment phase that
converts that metadata into edges or concepts. Metadata alone is not traversable.

### META-002: Extraction Completeness
> "Symbols that exist in source code must be extracted for analysis."

- **Status:** 100%
- **Notes:** Based on EXTENSIVE checking across 25+ repos in 5 bakeoff cohorts:
  - Known cases (INV-001, INV-003) are fixed
  - Lambda/closure call attribution audited for Go, Java, Rust, C#, Kotlin, Scala - all
    use implicit walk-up that correctly attributes calls to enclosing methods
  - C++ test patterns (Google Test, Catch2): Added to test-frameworks.yaml
  - C header declarations: Verified as correct (declarations don't have call edges)
  - Spec repos (NO_CALL_EDGES): Verified as correct (declarative code, no callable functions)
  - Go repos with external deps (LOW_RESOLUTION): Verified as correct (external calls unresolvable)
  - Language metrics (Python, Ruby, Rust, TypeScript, C, C++, Go): All healthy
  - Theoretical exotic constructs (metaprogrammed code, eval-generated functions) not
    found in any analyzed repos; would require specific failing repos to investigate further

**Unified by:**
- INV-001 (call edges must have caller symbols - implies callers are extracted)
- INV-003 (nested decorated functions must be extracted)

**Implication:** Special cases (nested functions, lambdas, callbacks) should not silently
skip symbol extraction. If code can be called, it must be extractable.

### META-003: Data Integrity
> "Graph elements must have valid, unique identifiers for reliable lookup."

- **Status:** 100%
- **Notes:** INV-005 fixed edge ID uniqueness. No known remaining issues. Symbol IDs
  appear stable across runs.

**Unified by:**
- INV-005 (edge IDs must be unique)

**Implication:** ID generation must include all disambiguating information (source, target,
type, AND location).

---

## INV-007: Go Import Path Resolution
- **Statement:** When multiple Go files define the same symbol (same package name, same function),
  call resolution must use the import path to pick the correct target file
- **Status:** ✅ FIXED
- **Root cause:** `symbol_resolution.py:ListNameResolver.lookup()` only checked the last directory
  segment of the import path (e.g., "genproto") which matched multiple candidates. When no unique
  match was found, it returned the first candidate, which was non-deterministic across Python versions.
- **Fix:** Enhanced `ListNameResolver.lookup()` to:
  1. Try progressively shorter suffixes of the import path (e.g., "zzz_correct/genproto", "genproto")
  2. Return when exactly one candidate matches a suffix
  3. Sort candidates deterministically (by path) when falling back to ambiguous resolution
- **Regression tests:**
  - `tests/test_go.py::TestGoImportPathResolution::test_resolves_call_to_correct_file_by_import_path`

## INV-008: Base Classes Metadata to Extends Edges
- **Statement:** Class symbols with `base_classes` metadata must create `extends` or `implements`
  edges to base classes/interfaces that exist in the analyzed codebase
- **Status:** ✅ FIXED
- **Root cause:** Python and JS/TS analyzers extracted `base_classes` into metadata but did not
  create edges. The type hierarchy linker requires `extends`/`implements` edges to build
  inheritance maps for polymorphic dispatch. Java was already creating these edges.
- **Fix:** Added `_extract_inheritance_edges()` function to both Python and JS/TS analyzers:
  - `analyze/py.py`: Creates `extends` edges after symbol collection in Pass 2
  - `analyze/js_ts.py`: Creates `extends`/`implements` edges, distinguishing classes from interfaces
  - Edges only created for base classes that exist in the analyzed codebase (not external packages)
  - Generic type parameters stripped (e.g., `Repository<User>` → `Repository`)
- **Regression tests:**
  - `tests/test_python_ast_analysis.py::TestPythonInheritanceEdges` (4 tests)
  - `tests/test_js_ts.py::TestJsTsInheritanceEdges` (4 tests)

## INV-009: Ruby/Kotlin Base Classes Metadata to Extends Edges
- **Statement:** Ruby and Kotlin class symbols with inheritance must create `extends` or `implements`
  edges to base classes/interfaces that exist in the analyzed codebase
- **Status:** ✅ FIXED
- **Root cause:** Ruby and Kotlin analyzers did not extract `base_classes` metadata or create edges.
  This gap was identified as part of META-001 scope expansion after INV-008.
- **Fix:** Added inheritance extraction to both Ruby and Kotlin analyzers:
  - `analyze/ruby.py`: Extracts superclass from `superclass` AST node, creates `extends` edges
  - `analyze/kotlin.py`: Extracts from `delegation_specifiers`, creates `extends` for classes and
    `implements` for interfaces
  - Ruby handles qualified names like `ActiveRecord::Base` → matches `Base` class
  - Kotlin handles multiple inheritance (class + multiple interfaces)
- **Regression tests:**
  - `tests/test_ruby.py::TestRubyInheritanceEdges` (5 tests)
  - `tests/test_kotlin.py::TestKotlinInheritanceEdges` (6 tests)

## INV-010: Django Route-Handler Linking
- **Statement:** Django routes with view_name metadata must create `routes_to` edges to their handler functions
- **Status:** ✅ FIXED
- **Root cause:** Route-handler linker (`linkers/route_handler.py`) only supported Rails, Laravel, Phoenix, and Express
  frameworks. Django routes with `view_name` metadata were not linked to their handlers.
- **Fix:** Added `_resolve_django_handler()` function that resolves view_name to handler symbols:
  - Simple names: `view_name="list_users"` → function
  - CBV: `view_name="UserListView"` → class
  - Module-qualified: `view_name="accounts.views.list_accounts"` → last segment
- **Impact:** Feature bakeoff showed 884 orphan routes in Django. These can now be linked.
- **Regression tests:**
  - `tests/test_route_handler_linker.py::TestDjangoViewNameLinking` (4 tests)

## INV-011: 100% Branch Coverage
- **Statement:** All code paths must be exercised by tests, verified by pytest-cov `--cov-branch`
- **Status:** ❌ UNFIXED (98% - ~1700 missing branch partials)
- **Root cause:** Branch coverage was not tracked; only line coverage was required.
  Many conditional branches (early returns, error paths, None checks, exception handlers)
  are executed but only one branch direction is tested.
- **Fix:** Infrastructure complete:
  - `scripts/smart-test` now uses `--cov-branch` flag
  - `pyproject.toml` configured to collect `BRANCHES_*.py` test files
  - BRANCHES test files created for 7 analyzers
- **Remaining work:** Create BRANCHES_*.py test files to cover remaining analyzers.
  Completed (as of 2026-02-04):
  - `py.py`: BRANCHES_test_python_ast_analysis.py (12 tests)
  - `js_ts.py`: BRANCHES_test_js_ts.py (10 tests)
  - `php.py`: BRANCHES_test_php.py (12 tests)
  - `rust.py`: BRANCHES_test_rust.py (11 tests)
  - `java.py`: BRANCHES_test_java.py (12 tests)
  - `csharp.py`: BRANCHES_test_csharp.py (22 tests)
  - `ruby.py`: BRANCHES_test_ruby.py (22 tests)
  - `kotlin.py`: BRANCHES_test_kotlin.py (20 tests)
  - `go.py`: BRANCHES_test_go.py (38 tests)
  - `scala.py`: BRANCHES_test_scala.py (27 tests)
  - `swift.py`: BRANCHES_test_swift.py (24 tests)
  - `cpp.py`: BRANCHES_test_cpp.py (26 tests)
  - `c.py`: BRANCHES_test_c.py (25 tests)
  - `elixir.py`: BRANCHES_test_elixir.py (20 tests) [hypergumbo-lang-common]
  - `dart.py`: BRANCHES_test_dart.py (28 tests) [hypergumbo-lang-common]
  - `graphql.py`: BRANCHES_test_graphql.py (14 tests) [hypergumbo-lang-common]
  - `hcl.py`: BRANCHES_test_hcl.py (27 tests) [hypergumbo-lang-common]
  - `julia.py`: BRANCHES_test_julia.py (30 tests) [hypergumbo-lang-common]
  - `proto.py`: BRANCHES_test_proto.py (22 tests) [hypergumbo-lang-common]
  - `ocaml.py`: BRANCHES_test_ocaml.py (21 tests) [hypergumbo-lang-common]
  - `fsharp.py`: BRANCHES_test_fsharp.py (21 tests) [hypergumbo-lang-common]
  - `clojure.py`: BRANCHES_test_clojure.py (23 tests) [hypergumbo-lang-common]
  - `erlang.py`: BRANCHES_test_erlang.py (17 tests) [hypergumbo-lang-common]
  - `haskell.py`: BRANCHES_test_haskell.py (18 tests) [hypergumbo-lang-common]
  Remaining high-priority mainstream analyzers: none (all 13 covered)
  Common analyzers started: 11 (24 remaining in hypergumbo-lang-common)
- **Strategy:**
  - Testable edge cases: Write tests for reachable branches (dict edge cases, unusual decorator forms, etc.)
  - Defensive code: Mark truly unreachable guards with `# pragma: no cover`
  - Focus on branches that affect correctness
- **Regression tests:**
  - All tests must pass with `pytest --cov-branch --cov-fail-under=100`

---

## META-004: Testing Discipline
> "Tests must exercise all code paths to verify correctness and prevent regressions."

- **Status:** 98%
- **Notes:**
  - Line coverage: 100% ✅
  - Branch coverage: 98% (~1500 missing partials)
  - BRANCHES test files created (13 mainstream analyzers + 11 common):
    - `BRANCHES_test_python_ast_analysis.py` (12 tests)
    - `BRANCHES_test_js_ts.py` (10 tests)
    - `BRANCHES_test_php.py` (12 tests)
    - `BRANCHES_test_rust.py` (11 tests)
    - `BRANCHES_test_java.py` (12 tests)
    - `BRANCHES_test_csharp.py` (22 tests)
    - `BRANCHES_test_ruby.py` (22 tests)
    - `BRANCHES_test_kotlin.py` (20 tests)
    - `BRANCHES_test_go.py` (38 tests)
    - `BRANCHES_test_scala.py` (27 tests)
    - `BRANCHES_test_swift.py` (24 tests)
    - `BRANCHES_test_cpp.py` (26 tests)
    - `BRANCHES_test_c.py` (25 tests)
    - `BRANCHES_test_elixir.py` (20 tests) [common]
    - `BRANCHES_test_dart.py` (28 tests) [common]
    - `BRANCHES_test_graphql.py` (14 tests) [common]
    - `BRANCHES_test_hcl.py` (27 tests) [common]
    - `BRANCHES_test_julia.py` (30 tests) [common]
    - `BRANCHES_test_proto.py` (22 tests) [common]
    - `BRANCHES_test_ocaml.py` (21 tests) [common]
    - `BRANCHES_test_fsharp.py` (21 tests) [common]
    - `BRANCHES_test_clojure.py` (23 tests) [common]
    - `BRANCHES_test_erlang.py` (17 tests) [common]
    - `BRANCHES_test_haskell.py` (18 tests) [common]
  - Total: 502 branch coverage tests across 24 analyzers
  - Target: 100% branch coverage

**Unified by:**
- INV-011 (100% branch coverage requirement)

**Implication:** Defensive code paths (error handlers, None checks, early returns)
must be tested explicitly. Untested branches may harbor bugs or become stale.
Branch coverage tests go in `BRANCHES_*.py` files for separate CI management.

---

## INV-XXX: Template for New Invariants
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED | TBD]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]

## META-00X: Template for New Meta-Invariants
> "[Broad principle statement]"

- **Status:** [100% | <100% (e.g., 80%) | TBD]
- **Notes:** [Assessment details, known gaps, research needed]

**Unified by:**
- [List of specific INV-xxx that this meta-invariant unifies]

**Implication:** [What this means for development practices]
- **Regression tests:** [Test names]
