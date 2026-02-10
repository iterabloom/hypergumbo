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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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

- **Pending Generalizations:** None

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

- **Pending Generalizations:** None

### META-003: Data Integrity
> "Graph elements must have valid, unique identifiers for reliable lookup."

- **Status:** 100%
- **Notes:** INV-005 fixed edge ID uniqueness. No known remaining issues. Symbol IDs
  appear stable across runs.

**Unified by:**
- INV-005 (edge IDs must be unique)

**Implication:** ID generation must include all disambiguating information (source, target,
type, AND location).

- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

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
- **Pending Generalizations:** None

## INV-011: Branch Coverage
- **Statement:** Branch coverage should be tracked to catch untested code paths.
- **Status:** ⬛ WON'T DO
- **Resolution (2026-02-07):** Removed `--cov-branch` from `scripts/smart-test`.
  Branch coverage was never enforced in CI (ci.yml, full-suite.yml, release.yml all
  run pytest without `--cov-branch`). It was only added to smart-test locally, which
  created ~1400 BRANCHES_*.py test files chasing the metric. Manual investigation of
  js_ts.py branch partials showed that ~70% of uncovered branches were unreachable
  defensive code (internal call-site contracts, for-loop exhaustion, type narrowing)
  where tests required mock nodes the real code never produces. The ~30% that were
  genuinely useful tested real input diversity (malformed configs, edge-case syntax)
  — but those are better discovered via bakeoff/integration testing than by auditing
  branch partials bottom-up.
  The existing BRANCHES_*.py files remain (some contain useful tests), but no new
  ones should be created to chase branch coverage. Line coverage at 100% is the
  enforced standard. Quality gaps are better found via bakeoff on real repos.
- **Pending Generalizations:** None

---

## META-004: Testing Discipline
> "Tests must exercise all code paths to verify correctness and prevent regressions."

- **Status:** ✅ FIXED (line coverage at 100%, enforced in CI)
- **Notes:**
  - Line coverage: 100% ✅ (enforced in CI via `--cov-fail-under=100`)
  - Branch coverage: deliberately not enforced (see INV-011 resolution)
  - 115+ BRANCHES test files exist from the branch coverage effort; they remain
    in the codebase (some contain genuinely useful edge-case tests) but no new
    ones should be created solely to chase branch partial metrics.

**Implication:** Quality gaps in analysis correctness are better found via bakeoff
on real repos than by auditing branch partials. Line coverage at 100% ensures all
statements are reached; input diversity testing ensures they produce correct results.

- **Pending Generalizations:** None

---

## INV-012: Decorator Edge Detection (All Languages with Decorator Metadata)
- **Statement:** Decorator/annotation applications should create "decorated_by" edges in the call graph
- **Status:** ✅ FIXED (Python, TypeScript, Java, C#, Rust)
- **Root cause:** Analyzers extracted decorator information as metadata (`meta.decorators` or `meta.annotations`)
  but did not create edges between the decorator and the decorated function.
  This meant `@app.get("/users")` resulted in:
  - ✅ Metadata on the function: `decorators: [{name: "app.get", args: ["/users"]}]`
  - ❌ No edge from `app` or `app.get` to the decorated function
  - Result: FastAPI's HTTP method decorators showed 0 in-degree in symbols.txt
- **Discovery:** bakeoff-features-reflect assessment on FastAPI (2026-02-06)
  - "FastAPI.get/post/put etc. not in high-connectivity symbols (0 in-degree)"
  - Decorator registration pattern not visible in call graph
- **Fix (Python):** Added `_resolve_decorator_target()` and `_process_decorators()` functions in
  `py.py:_extract_edges()` that:
  1. Process `decorator_list` on functions/classes during AST walk
  2. Resolve decorator callees (Name, Attribute, Call forms)
  3. Create "decorated_by" edges from decorated symbol to decorator
  4. Handle unresolved decorators with unresolved edges (0.5 confidence)
  Supported patterns:
  - Simple: `@decorator` → resolved to local or imported function
  - With args: `@decorator(args)` → extracts function from Call
  - Method: `@ClassName.method` → resolved via local_symbols short name lookup
  - Stacked: Multiple decorators create multiple edges
- **Fix (TypeScript):** Added `_extract_decorator_edges()` function in `js_ts.py` that:
  1. Iterates symbols with `decorators` metadata
  2. Resolves decorator names to global symbols
  3. Creates "decorated_by" edges (or unresolved edges for unknown decorators)
  Enables visibility of NestJS patterns like @Controller, @Injectable, @Get, etc.
- **Fix (Java):** Added `_extract_annotation_edges()` function in `java.py` that:
  1. Iterates symbols with `annotations` metadata
  2. Resolves annotation names to global symbols (tries both short and qualified names)
  3. Creates "decorated_by" edges (or unresolved edges for unknown annotations)
  Enables visibility of Spring patterns like @Service, @Controller, @GetMapping, etc.
- **Fix (C#):** Added `_extract_attribute_edges()` function in `csharp.py` that:
  1. Iterates symbols with `annotations` metadata
  2. Resolves attribute names to global symbols (handles Attribute suffix convention)
  3. Creates "decorated_by" edges (or unresolved edges for unknown attributes)
  Enables visibility of ASP.NET patterns like [HttpGet], [Route], [Authorize], etc.
- **Fix (Rust):** Added `_extract_attribute_edges()` function in `rust.py` that:
  1. Iterates symbols with `annotations` metadata
  2. Resolves attribute names to global symbols (handles qualified paths like actix_web::get)
  3. Creates "decorated_by" edges for all symbol kinds (functions, structs, enums, traits)
  Enables visibility of Actix/Axum patterns like #[get("/path")], #[derive(Debug)], etc.
- **Regression tests:**
  - `test_decorator_edges.py::TestDecoratorEdges` (6 tests - Python)
  - `test_ts_decorator_edges.py::TestTypeScriptDecoratorEdges` (3 tests - TypeScript)
  - `test_java_annotation_edges.py::TestJavaAnnotationEdges` (3 tests - Java)
  - `test_csharp_attribute_edges.py::TestCSharpAttributeEdges` (3 tests - C#)
  - `test_rust_attribute_edges.py::TestRustAttributeEdges` (7 tests - Rust)
- **Trade-offs:**
  - Pros: Decorators become visible in call graph, better centrality ranking
  - Cons: May create noisy edges for common decorators like `@staticmethod`, `@property`
  - Option: Filter by decorator type or add confidence weighting
- **Related:**
  - INV-003 (nested decorated function extraction) - prerequisite, already FIXED
  - ADR-0003 (rich metadata) - decorators already extracted, just need edges
- **Pending Generalizations:** None

---

## INV-013: TypeScript Constructor Injection Resolution
- **Statement:** Method calls on constructor-injected properties (`this.property.method()`) must resolve to the injected type's methods
- **Status:** ✅ FIXED
- **Root cause:** JS/TS analyzer handled `this.method()` (direct class methods) but not `this.property.method()`
  where property is a constructor parameter like `constructor(private catsService: CatsService)`.
  The analyzer checked `obj_node.type == "this"` but when calling `this.catsService.create()`,
  obj_node is `this.catsService` (member_expression), not `this`.
- **Fix:** Added Case 1b in `_extract_edges()` to handle nested member_expression patterns:
  1. Detect if obj_node is `this.propertyName` (member_expression with `this` and property_identifier)
  2. Look up propertyName in var_types (populated from constructor parameter type annotations)
  3. Resolve `TypeName.methodName` using the type from var_types
  4. Create edge with evidence_type "ast_method_this_property" and 0.90 confidence
- **Discovery:** Feature bakeoff reflection on NestJS repo (2026-02-06):
  - Forward slice from CatsController.create had 0 useful nodes
  - Assessment noted: "misses the actual business logic call to catsService.create()"
- **Impact:** Forward slices from NestJS/Angular controllers now include service layer calls
- **Limitation:** JS files without type annotations (e.g., `constructor(catsService)` in babel example)
  cannot be type-inferred, so `this.catsService.create()` falls through to Case 4 (method name match)
  which produces low-confidence edges to all matching methods. This is inherent to untyped JavaScript.
- **Fix 2 (PR #977):** `_get_enclosing_function` used `global_symbols[full_name]` which only stores
  one symbol per name. In monorepos with 11 files defining `CatsController.create`, only the
  last-processed file's symbol could be found; the other 10 returned `None` → no call edges.
  Fixed by using `symbol_by_position` (keyed by file+line+col) which uniquely identifies every symbol.
  Result: 1/9 → 9/9 controllers produce call edges on NestJS repo.
- **Regression tests:**
  - `test_js_ts.py::TestVariableTypeInference::test_this_property_method_call_nestjs_pattern`
  - `test_js_ts.py::TestVariableTypeInference::test_this_property_disambiguates_via_named_import`
  - `test_js_ts.py::TestVariableTypeInference::test_variable_method_disambiguates_via_named_import`
  - `test_js_ts.py::TestVariableTypeInference::test_import_alias_tracked_in_named_imports`
  - `test_js_ts.py::TestVariableTypeInference::test_disambiguate_non_relative_import_falls_through`
  - `test_js_ts.py::TestVariableTypeInference::test_disambiguate_single_candidate_returns_none`
  - `test_js_ts.py::TestVariableTypeInference::test_disambiguate_no_match_returns_none`
  - `test_js_ts.py::TestVariableTypeInference::test_enclosing_function_found_for_all_duplicate_named_methods`
- **Pending Generalizations:** None

## INV-014: Chained Member Access Call Resolution
- **Statement:** Method calls on object properties (e.g., `this.field.method()`, `self.field.method()`, `obj.field.method()`) must resolve to the correct target method
- **Status:** ✅ FIXED (Java, Kotlin, C#, Scala, JS/TS, Go, Python)
- **Root cause:** Analyzers only checked for simple `identifier` receivers in method invocation/call_expression nodes. Nested member access (navigation_expression, member_access_expression, field_expression, selector_expression, ast.Attribute) was not handled, causing zero call edges for the most common method call pattern in OO languages.
- **Discovery:** Forward slice from `OwnerController.showOwner` in spring-petclinic returned 0 edges despite the method calling `this.owners.findById(ownerId)`.
- **Fix:**
  - **Java** (PR #972): Replaced identifier scanning with `child_by_field_name("name"/"object")`, added field_access handling and class field type tracking
  - **Kotlin** (PR #973): Added constructor parameter type tracking (`class_parameter` → `var_types`), added nested `navigation_expression` handling for `this.property.method()` pattern
  - **C#** (PR #973): Added field declaration type tracking (`field_declaration` → `var_types`, handles both simple and generic types), added nested `member_access_expression` handling for `this._field.Method()` pattern
  - **Scala** (PR #973): Fixed method name extraction from `field_expression` — was taking first identifier (receiver name) instead of last (method name)
  - **Python** (this PR): Added class field type pre-collection from `__init__` (tracks `self.field = param` with type annotations and `self.field = Class()` constructors), added Case 2f in `_process_call` for `self.field.method()` pattern using nested `ast.Attribute`
  - **JS/TS** (INV-013, pre-existing): Already handled nested `member_expression` with `this` for NestJS constructor injection
  - **Go**: Already works — method name extracted from outermost `field_identifier`, resolver matches by name
- **Regression tests:**
  - `test_kotlin.py::TestKotlinThisMethodCalls::test_this_property_method_call_resolved`
  - `test_kotlin.py::TestKotlinThisMethodCalls::test_this_property_method_call_generic_type`
  - `test_csharp.py::TestCSharpTypeInference::test_field_type_inference`
  - `test_csharp.py::TestCSharpTypeInference::test_this_field_method_call`
  - `test_scala.py::TestScalaFunctionCalls::test_detects_method_call_on_object`
  - `test_java.py::TestJavaAnalysis::test_extracts_field_access_method_calls` (PR #972)
  - `test_python_ast_analysis.py::TestVariableMethodCalls::test_self_field_method_call_with_param_type`
  - `test_python_ast_analysis.py::TestVariableMethodCalls::test_self_field_method_call_with_constructor`
- **Pending Generalizations:** None

---

## INV-XXX: Template for New Invariants
- **Statement:** [What must always be true]
- **Status:** [UNFIXED | PARTIALLY ADDRESSED | FIXED | TBD]
- **Root cause:** [File:line or description]
- **Fix:** [What was done]
- **Limitation:** [What's still broken]
- **Regression tests:** [Test names]
- **Pending Generalizations:** None, or entries with markers:
  - `**TODO!**` — invariant/defect work, or anything potentially structural (blocks stopping; when in doubt, use this)
  - `**TODO**` — clearly non-defect backlog (blocks stopping, but agent may defer freely)
  - `**DONE**` with PR reference
  - `**DEFERRED**` with justification

## META-00X: Template for New Meta-Invariants
> "[Broad principle statement]"

- **Status:** [100% | <100% (e.g., 80%) | TBD]
- **Notes:** [Assessment details, known gaps, research needed]

**Unified by:**
- [List of specific INV-xxx that this meta-invariant unifies]

**Implication:** [What this means for development practices]
- **Regression tests:** [Test names]
- **Pending Generalizations:** None, or entries with markers:
  - `**TODO!**` — invariant/defect work, or anything potentially structural (blocks stopping; when in doubt, use this)
  - `**TODO**` — clearly non-defect backlog (blocks stopping, but agent may defer freely)
  - `**DONE**` with PR reference
  - `**DEFERRED**` with justification
