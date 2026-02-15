# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.0.2
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

#### Language & framework support

- **JS/TS module resolution linker**: New linker that resolves the 9,192 unresolved JS/TS import edges (raw import paths like `javascript:./utils:0-0:module:module`) to actual file symbols. For relative imports (`./foo`, `../bar`), probes file extensions (.js, .ts, .jsx, .tsx, .mjs, .mts, .vue, .json) and directory index files. Creates `module_file` symbols for resolved targets and `npm_package` symbols for bare/scoped npm imports. Creates `imports_module` edges (file→module dependency) and `module_exports` edges (module→function/method/class reachability). Supports **path alias resolution** from `tsconfig.json`/`jsconfig.json` (`compilerOptions.paths` + `baseUrl`, follows `extends` chains) and `vite.config.ts/js` (`resolve.alias`). **Monorepo-aware**: recursively discovers tsconfig.json files in subdirectories (e.g., `packages/frontend/tsconfig.json`) and uses the nearest ancestor config for each source file. On GrowthBook, this resolves 5,334+ `@/*` tsconfig imports across monorepo packages; on Chatwoot, 2,963 Vite alias imports (eliminated 8 phantom npm_package nodes, orphan rate 16.6% → 12.9%). On Chatwoot: +3,178 module_file symbols, +89 npm_package symbols, +9,031 imports_module edges, +5,218 module_exports edges. JS orphans reduced from 3,023 to 775 (74% reduction). Total code orphans: 31.3% → 17.2%.
- **Vue template-method linker**: New linker connecting Vue template event handlers (`@click="handleDelete"`, `v-on:input="validate"`) to their corresponding method/function symbols in the same `.vue` file's `<script>` section. Creates `template_calls` edges with `vue_event_handler` evidence (confidence 0.90). The Vue analyzer now extracts `handler_expression` metadata from event directives. Handles path normalization between Vue (relative) and JS/TS (absolute) symbol paths. On Chatwoot, addresses the primary gap behind ~3,139 orphan JS symbols in `.vue` files.
- **Python FFI linker (ctypes/cffi/PyO3)**: New cross-language linker that connects Python code calling C/C++ functions via `ctypes.CDLL`/`ctypes.cdll.LoadLibrary` and `cffi` `ffi.dlopen()` to the corresponding C/C++ function symbols, creating `ffi_bridge` edges. Also detects Rust functions annotated with PyO3 `#[pyfunction]`/`#[pymethods]` and matches them to unresolved Python call edges. Activates when both Python and C/C++/Rust files are present. Three evidence types: `ctypes_call`, `cffi_call`, `pyo3_bridge`.
- **Ruby FFI linker**: New cross-language linker for Ruby-C/C++ interop. Detects Ruby FFI gem patterns (`extend FFI::Library` + `attach_function :name, ...`) and C extension registration patterns (`rb_define_method`, `rb_define_module_function`, `rb_define_singleton_method`). Creates `ffi_bridge` edges with evidence types `ruby_ffi_attach` and `ruby_c_extension`. Activates when both Ruby and C/C++ files are present.
- **ORM query linker**: Detects Django ORM (`Model.objects.filter/get/all/...`) and Flask-SQLAlchemy (`Model.query.filter_by/first/...`) query patterns in Python source files and creates `model_reference` edges from the calling function to the Model class symbol. This increases in-degree centrality of Model classes, fixing under-ranking (e.g., Django Model was degree 5 / rank 2156; now gains edges from every view that queries it).
- **Go same-package method resolution fix**: Fixed a bug where package-qualified calls like `bug.AddComment()` (where `bug` is an import alias) were incorrectly resolved to a local method with the same short name (e.g., `BugCache.AddComment` in the same file). The local-first symbol check now skips when an import path hint is present, allowing the global resolver to use the import path for correct disambiguation. Identified via DEEP bakeoff-features-reflect (git-bug: `bug.AddComment()` mis-resolved to `BugCache.AddComment`).
- **Go generic interface assertion detection**: Interface-implementation assertions with Go generics (`var _ Cache[string] = &StringCache{}`, `var _ SubCache[A, B, C] = &Impl{}`, `var _ pkg.Interface[T] = &Impl{}`) are now correctly detected. Previously, the `generic_type` tree-sitter node type was unhandled, causing all generic interface assertions to be silently ignored. Extracts the base type name from the `generic_type` node (supports both simple and qualified base types). Identified via DEEP bakeoff-features-reflect (git-bug: 38/100 interface assertions missed due to generics).
- **ListNameResolver full-path disambiguation**: The resolver now tries the full import path as a disambiguation suffix, not just progressively shorter segments. For short paths like `"entities/bug"`, the last segment `"bug"` may match multiple candidates (e.g., `entities/bug/file.go` and `cache/bug_cache.go`), but the full path `"entities/bug"` uniquely identifies the correct target.
- **Java inherited method fallback**: When a type-inferred method call can't resolve the specific method (e.g., `repo.save()` where `save` is inherited from `JpaRepository` and not defined in user code), the analyzer now creates an edge to the type's class/interface symbol (confidence 0.70, evidence `ast_call_inherited_method`). This captures the controller→repository dependency for Spring Data JPA methods like `save()`, `findById()`, `findAll()`, `deleteById()` that are inherited from framework base classes. Previously, these calls produced zero edges because the method wasn't declared in user code. Identified via DEEP bakeoff-features-reflect (spring-petclinic assessment: JPA repository inherited methods missing — single highest-impact gap).
- **Java method invocation fix**: Fixed call graph extraction for Java method invocations with `field_access` receivers (e.g., `this.repo.findById()`, `svc.process()`). The analyzer now uses tree-sitter field names (`name`, `object`) instead of scanning children for identifiers, and tracks class field types for type inference. Previously, most Java method calls produced no call edges because `field_access` nodes were not handled.
- **Chained member access call resolution (Kotlin, C#, Scala, Python)**: Fixed call graph extraction for `this.field.method()` / `self.field.method()` patterns across four languages. This was the same structural issue as the Java fix — analyzers only checked for simple identifier receivers, missing nested member/navigation/field expressions. Kotlin now tracks constructor parameter types and resolves `this.svc.process()`. C# now tracks field declaration types and handles `this._repo.Save()`. Scala now extracts method names from `field_expression` nodes (previously took the receiver name instead of the method name, producing zero call edges for all method calls). Python now pre-collects class field types from `__init__` (both typed parameter assignments `self.svc = svc` where `svc: Service` and constructor calls `self.repo = Repository()`) and resolves `self.field.method()` calls.
- **JS/TS import-path disambiguation (INV-013)**: When multiple files define the same class name (e.g., NestJS monorepos with duplicate `CatsService` in different apps), the analyzer now uses `import { Foo } from './module'` paths to disambiguate. Applies to `this.property.method()` (Case 1b) and `variable.method()` (Case 3). Previously, the resolver picked whichever symbol was last processed (effectively arbitrary).
- **JS/TS monorepo enclosing function fix**: Fixed `_get_enclosing_function` to use position-based symbol lookup instead of name-based `global_symbols` lookup. In monorepos with duplicate class names, `global_symbols` only keeps one symbol per name, so `_get_enclosing_function` returned `None` for all but one file — causing most call edges to be silently dropped. On NestJS repo: before, 1 of 9 `CatsController.create` methods produced call edges; now all 9 do.
- **Cgo linker (Go-C interop)**: New cross-language linker that resolves Go `C.funcName()` calls (via `import "C"`) to their C/C++ function implementations. The Go analyzer creates unresolved edges with pattern `go:C:0-0:{name}:unresolved` for cgo calls; the linker matches these to C/C++ function symbols by name and creates `cgo_bridge` edges. Activates when both Go and C (or C++) files are present.
- **Framework detection for 16 additional languages**: Haskell (servant, scotty), Clojure (ring-compojure, pedestal), R (shiny, plumber), Lua (openresty, lapis, love2d), C++ (qt), Erlang (cowboy), F# (giraffe, saturn, suave), Kotlin (Ktor, Exposed, Koin, Kodein), C# (ASP.NET Core, Blazor, Entity Framework, SignalR), Dart (Shelf, Aqueduct, Angel, Dart Frog, Serverpod), Julia (Genie, Oxygen, HTTP.jl, Mux), OCaml (Dream, Opium, Cohttp, Eliom), Nim (Jester, Prologue, Karax, Mummy), Zig (zap, http.zig, zig-network), D (vibe.d, Hunt, DiamondMVC), Groovy (Grails, Ratpack, Micronaut). Includes YAML pattern files for symbol enrichment (routes, handlers) where applicable.
- **Test framework patterns for 16 additional languages**: Elixir (ExUnit), Scala (ScalaTest/MUnit/Specs2), Dart (test), Clojure (clojure.test), Haskell (HSpec/Tasty/QuickCheck), Erlang (EUnit/Common Test), F# (Expecto/NUnit/xUnit), Ruby (RSpec), Julia (Test), OCaml (OUnit/Alcotest), Lua (busted/luaunit), R (testthat), Nim (unittest), Zig (built-in), D (unittest), Groovy (Spock/JUnit).
- **Main function entrypoint detection for 7 more languages**: D, Nim, Zig, V, Odin, Gleam, Haxe.
- **Route path prefix inheritance across frameworks**: Fixed `prefix_from_parent` for Spring Boot, JAX-RS, Micronaut, and ASP.NET. Class-level route annotations (e.g., `@RequestMapping("/owners/{ownerId}")`, `@Path("/api/users")`, `@Controller("/api")`, `[Route("api/users")]`) now correctly combine with method-level route annotations. Also fixed Spring Boot `@RequestMapping` positional arg extraction (`args[0]|kwargs.value`).
- **OTP GenServer dispatch linker (Elixir)**: New linker that connects GenServer.call/cast call sites to their handle_call/handle_cast handler functions. OTP's runtime message dispatch is invisible to static analysis, leaving handler functions orphaned (Livebook: 311 handlers with zero incoming edges, 43.3% orphan rate; Plausible: 39.6% orphan). Three matching strategies: (1) same-module variable target — `GenServer.call(pid, :msg)` links to `handle_call` in the same module (confidence 0.85), (2) `__MODULE__` — explicit self-reference (0.90), (3) cross-module — `GenServer.call(MyApp.Server, :msg)` resolves to `MyApp.Server.handle_call` (0.80). Deduplicates edges per caller→handler pair. Links to all handler clauses when a module has multiple `handle_call` definitions.
- **OTP/Phoenix behaviour callback detection (Elixir)**: When a module declares `use GenServer`, `use Phoenix.LiveView`, `use Supervisor`, `use Phoenix.LiveComponent`, `use Plug.Builder`, etc., its callback functions (`init`, `handle_call`, `mount`, `handle_event`, `render`, `update`, etc.) now receive `invokes_callback` edges from the module symbol (confidence 0.90). Covers 11 behaviours (GenServer, Supervisor, Agent, Task, Phoenix.LiveView, Phoenix.LiveComponent, Phoenix.Component, Plug, Plug.Builder, Plug.Router) with their expected callbacks. Previously, these framework-invoked functions appeared as orphans. On Livebook: addresses 736+ orphaned callback functions (468 LiveView + 268 OTP lifecycle/optional).
- **Phoenix LiveView `live` route detection (Elixir)**: The `live` macro in Phoenix routers (`live "/dashboard", DashboardLive` and `live "/users/:id", UserLive.Show, :show`) is now detected as a route. Creates route symbols with `http_method=LIVE` and links to the LiveView module's `mount` callback via the route-handler linker. LiveView routes without an explicit action default to `action="mount"`. Handles dotted module names like `UserLive.Show`. Also adds a YAML usage pattern for the `live` macro in `phoenix.yaml`. Identified via DEEP bakeoff-features-reflect: Livebook had ~40 invisible LiveView routes (the primary navigation structure) while only 11 traditional HTTP routes were detected.
- **Elixir multi-clause function edge resolution**: Call edges and `invokes_callback` edges now target ALL clauses of a multi-clause Elixir function, not just the last one. Elixir functions with pattern matching (e.g., multiple `handle_call` clauses) produce separate symbol nodes per clause, but the previous single-value lookup (`symbol_by_name`) used last-writer-wins, causing N-1 clauses to be orphaned. New multi-value indices (`symbols_by_name` per file, `global_symbols_multi` globally) preserve all clauses and create edges to each. Covers same-file calls, cross-file calls, and behaviour callbacks. On Livebook: 1,919 of 2,686 Elixir orphans (71.4%) were from multi-clause functions with 332 functions having only some clauses linked.
- **Java array initializer annotation path extraction**: Framework pattern path extraction now unwraps single-element Java array initializers. `@GetMapping({ "/vets" })` uses annotation syntax that the Java analyzer parses as `args: [["/vets"]]` — a list inside the args list. The `_extract_single_value` method now detects single-element lists and unwraps them. Also handles `kwargs` case: `@RequestMapping(value = {"/api"})`. Previously, the path was stringified as `"['/vets']"` and the route appeared without a usable path. Identified via DEEP bakeoff cohort 041 (spring-petclinic: `VetController.showResourcesVetList` route missing its `/vets` path).
- **Ruby `.new` constructor resolution**: `SomeClass.new` now correctly resolves to `SomeClass#initialize` instead of matching a method named `new` on the class. In Ruby, `.new` is the class-level allocator that delegates to `#initialize`. A Rails controller `def new` is an instance method (action), not the constructor. Previously, `RoutesController.new(params)` matched `RoutesController#new` (the controller action), creating 130 false in-edges on postal's `RoutesController`. Now emits `constructor_call` evidence (confidence 0.90) when `#initialize` exists; no edge otherwise. Identified via DEEP bakeoff-features-reflect cohort 041 (postal: RoutesController#new ranked #4 with 130 false in-edges).
- **Ruby variable-receiver false positive fix**: Calls with variable receivers (e.g., `user.account`, `obj.process`) no longer fall through to bare-name global symbol lookup, which previously matched an arbitrary method with the same short name from an unrelated class. Now, only bare calls (no receiver) use the global resolver fallback. Constant-receiver calls (`User.find`, `MyModule.method`) continue to resolve via `_try_receiver_call`. Identified via DEEP bakeoff-features-reflect: chatwoot had widespread false positive edges from Ruby method name collisions (e.g., `user.account` matching `Billing#account`).
- **Rails callback edge detection (Ruby)**: `before_action`, `after_action`, `around_action` (and legacy `before_filter`/`after_filter`/`around_filter`) class-level declarations now create `invokes_callback` edges from the controller class symbol to the callback method symbol (confidence 0.90). Callback methods are resolved in the same class, same file, or cross-file via global symbol registry (handles inherited callbacks from `ApplicationController`). Previously, callback methods like `authenticate_user!` appeared as orphans because no explicit call referenced them. Identified via DEEP bakeoff analysis of Chatwoot Ruby orphans (858 orphans, many from unlinked callbacks).
- **Gorilla mux route detection (Go)**: Added route detection for Gorilla mux, one of Go's most widely used HTTP routers. Detects two patterns: (1) simple `router.HandleFunc("/path", handler)` / `router.Handle("/path", handler)` calls, and (2) builder chain patterns like `router.Path("/path").Methods("GET").Handler(handler)` and `router.PathPrefix("/static/").Handler(fileServer)`. Handler extraction supports simple identifiers, package-qualified names (`handlers.GetAPI`), and call expressions (`httpapi.NewHandler(env)`). HTTP method defaults to `ANY` unless `.Methods("GET")` is specified in the chain.
- **Go route-handler linking**: Gin, Echo, Fiber, Chi routes are now linked to handler functions via `handler_name` metadata, resolving both simple identifiers (`listUsers`) and package-qualified names (`handlers.GetAPI`).
- **Go HTTP client detection**: `net/http` calls (`http.Get`, `http.Post`, `http.Head`, `http.NewRequest`, `http.NewRequestWithContext`) enable cross-language linking from Go clients to route handlers in other backends.
- **Django framework patterns**: Template tag/filter patterns (`@register.simple_tag`, `@register.inclusion_tag`, `@register.filter`) and signal receiver edge detection (`@receiver(signal)` → `signal_receiver` edges).
- **Flask framework patterns**: Jinja2 template customizations (`@app.template_filter/global/test`), Blinker signal handlers (`@signal.connect` for `request_started`, `request_finished`, etc.), and Flask-RESTful support (`api.add_resource()`, `Resource` base class, `fields.*` serializers, `reqparse.RequestParser`).

- **Elixir cross-file module-qualified call linking**: The Elixir analyzer now detects and resolves module-qualified calls like `Helper.greet()` and `App.Services.UserService.find()` across files. Previously, only bare function calls (`greet()`) within the same file produced call edges; dot-call syntax was ignored, resulting in high orphan rates (53.9% on Livebook). Resolution uses four strategies in priority order: (1) direct qualified name lookup in global symbols (confidence 0.90), (2) alias hint resolution from `alias` declarations (0.85), (3) NameResolver suffix matching with module path hint (0.75), (4) unresolved edge fallback for external modules (0.50). Identified via DEEP 30-repo bakeoff (Livebook: 1.8% cross-file ratio, 53.9% orphan rate).
- **Ruby receiver-qualified call linking**: The Ruby analyzer now detects method calls with explicit receivers — `User.find(id)`, `ActiveRecord::Base.connection`, and similar constant-receiver patterns. When a call has a class/constant receiver, the analyzer builds a qualified name (`ClassName#method` or `ClassName.method`) and resolves it in global symbols (confidence 0.85), with a NameResolver fallback for namespaced classes (0.75). Previously, only the bare method name was used for resolution, which was ambiguous when multiple classes defined methods with the same name. Identified via DEEP 30-repo bakeoff (Chatwoot: 1.0 E/N ratio, 61.1% orphan rate).

- **Test file tier classification**: Test directories (`tests/`, `test/`, `__tests__/`, `spec/`) and test files (`_test.go`, `.test.js`, `.spec.ts`, `_spec.rb`) are now classified as tier 2 (internal_dep) instead of defaulting to tier 1 (first_party). This applies across all languages and takes priority over first-party path patterns so that `src/test/java/` and `pkg/handler_test.go` are correctly classified. Previously, the near-100% tier1 rate on 27/28 repos in the DEEP bakeoff was partly caused by test files being counted as production code.

- **Test function entrypoint detection**: Test functions detected by `test-frameworks.yaml` patterns (pytest `test_*`, JUnit `@Test`, Go `Test*`/`Benchmark*`/`Example*`, RSpec `describe`/`it`, etc.) are now registered as `TEST_FUNCTION` entrypoints (confidence 0.80). The existing 90% test-file confidence penalty ensures they don't dominate `--entry auto` selections. Previously, test framework concepts were detected but not mapped to entrypoints, causing the bakeoff's test entrypoint ratio to always report 0%. Also fixed the `bakeoff-features` diagnostic script which was checking a nonexistent `file` field on entrypoint dicts instead of the `kind` field.

- **C++ template function call detection**: The C++ analyzer now detects and resolves template function calls like `process<int>(42)`, template method calls on objects like `obj.get<T>()`, and qualified template calls like `NS::create<Widget>()`. Previously, template function calls produced `template_function` AST nodes that `get_callee_name()` didn't handle, causing all template calls to be orphaned. Also fixes extraction of functions returning pointers (`T* make()`) and references (`T& get()`) — these wrapped `function_declarator` inside `pointer_declarator`/`reference_declarator`, causing the symbol to be silently skipped. The 55.8% orphan rate on Falco (C++ repo) was partly caused by missing template calls and pointer-returning functions.
- **C++ stack object construction detection**: Stack-allocated objects like `Widget w;`, `Config c(1, 2);`, `Widget w{};`, and `Widget{42}` now produce `instantiates` edges (evidence type `stack_construction`, confidence 0.85). Previously, only explicit `new` expressions were detected. Stack construction is ubiquitous in C++ (RAII pattern) and its absence was a major contributor to high orphan rates. Also handles namespace-qualified types (`ui::Button btn;`). The class symbol is correctly identified even when constructors overwrite the class name in the symbol dict.
- **Ruby/Rails route detection false positive reduction**: Route detection is now restricted by file context. Test directories (`spec/`, `test/`, `tests/`, `features/`) are skipped entirely — `get '/path'` and `post '/path'` in test files are HTTP test helpers, not route definitions. Non-route app files only produce route symbols for Sinatra-style routes (with `do` blocks). Route definition files (`routes.rb`, `config/routes/*.rb`) continue to detect all route patterns. Previously, every Ruby file was scanned for HTTP method calls, causing massive false positives in large Rails apps (Chatwoot: 2467/2499 route nodes orphaned at 99%).
- **Vue component linker**: New cross-file linker that resolves `imports_component` edges from the Vue analyzer. The Vue analyzer creates edges with raw import paths (e.g., `./Header.vue`, `@/components/Modal.vue`) as destinations; this linker resolves those paths to actual `.vue` files on disk and creates proper symbol-to-symbol edges. Also creates `component_file` symbols representing each `.vue` file. Handles relative imports, subdirectory imports, `@` alias resolution (tries `src/`, `app/`, repo root), and extension-less imports. Previously, all Vue component references were 100% orphaned because edges pointed to raw strings instead of Symbol IDs.

#### Tracker TUI

- **Textual 7.x upgrade**: Bumped `textual` dependency from `~=3.0` to `~=7.5`. Adapts to the v6.0.0 breaking change: `Static.renderable` → `Static.content`.
- **Standard two-pane layout (ADR-0013 PR 6b)**: The `tracker tui` command now supports the standard tier (60x20 – 120x38) with a two-pane layout: left panel shows a DataTable or Tree view, right panel shows detail for the highlighted item. Cursor movement auto-updates the detail panel. Tree toggle (`t`) switches between table and parent-child tree view. Filter input (`f`/Escape) narrows items by title, status, tags, or kind. Dynamic resize preserves selection across compact↔standard↔too-small transitions. Compact layout (40x16 – 59x19) continues to use stacked detail (Enter/Esc toggle).
- **Wide layout tier (ADR-0013 PR 6c)**: Terminals wider than 120x38 now show extra DataTable columns (created, updated, conflict indicator), longer proquint IDs, a split right panel with activity log below detail, and a filter status indicator. Dynamic resize transitions between standard↔wide preserve selection state.
- **Snapshot tests (ADR-0013 PR 6d)**: Visual regression tests using `pytest-textual-snapshot` for all 8 TUI scenarios: compact list, compact+status, compact detail, standard two-pane, standard tree, wide layout, filter panel, too-small. SVG baselines in `tests/__snapshots__/` detect unintended rendering changes.

#### Analysis core

- **Edge deduplication by relationship (`edge_key`)**: The output graph now contains at most one edge per (src, dst, edge_type) relationship, matching the standard call graph model used by LLVM, Doxygen, and Code Intelligence platforms. Previously, multiple call sites from the same function to the same target (e.g., 24 calls from `InitialSchema#up` → `Provisioner#create_table` in postal) produced 24 separate edges because dedup used line-sensitive `edge.id`. Now deduplicates by `edge_key` (which excludes line number). The first occurrence is preserved. This eliminates false centrality inflation from repeated calls and reduces noise in reverse slices. New `deduplicate_edges()` utility in `ir.py` shared by both `cli.py` and `sketch.py`.
- **Skip structural edges in forward slices**: Forward slice BFS no longer traverses `extends`, `implements`, or `contains` edges. These structural relationships caused BFS explosion: `extends`/`implements` fanned out through shared ancestors (all controllers sharing ApplicationController); `contains` fanned out to ALL member methods when a class was reached (forward-slicing through Owner pulled in getAddress, setCity, etc. alongside the targeted getPet). When the slice entry point IS a container type (class/interface/struct/etc.), class expansion seeds the BFS with member methods so they are still reachable. Reverse slices still follow all three edge types. Identified via DEEP bakeoff-features-reflect (spring-petclinic assessment: `contains` edges pulled in all sibling methods of reached classes; Chatwoot assessment: `extends` explosion through ApplicationController).
- **Default hub pruning for slices (`hub_threshold=50`)**: Slice BFS now prunes nodes with >50 outgoing (forward) or incoming (reverse) edges by default. Hub nodes are included in the result but not traversed through, preventing BFS explosion through extreme utility nodes (e.g., `apply_operation` with 4375 out-degree in Livebook). This addresses HIGH_LIMIT_HIT_FREQUENCY in DEEP bakeoff (100% on Chatwoot/Livebook, 85.7% on git-bug). The threshold affects only the top ~1% of nodes by degree. Entry nodes are always expanded. Use `--hub-threshold 0` to disable.
- **Fix name-collision edge fanout (JS/TS, PHP)**: When a method call cannot be resolved via type information (Case 4 bare name fallback), the analyzer now emits a single edge to the best-match candidate instead of fanning out to ALL methods with the same name. In monorepos like NestJS (30+ classes with `create()` methods), the old behavior created N spurious edges per call site, corrupting reverse slices and inflating centrality rankings. Identified via DEEP bakeoff-features-reflect assessment (cohort 7, iteration 3).
- **Preserve inheritance edges in test-edge filtering**: When ranking symbols with `--exclude-tests` / `-x`, `extends` and `implements` edges from test files are now preserved. These structural edges reflect the architectural importance of the target (base class / interface), not the importance of the test. Previously, Django `Model` (with 237 test subclasses) was catastrophically underranked because all test `extends` edges were filtered out. Identified via DEEP bakeoff-features-reflect (Django assessment: Model degree 5, rank 2156).
- **Aggressive test entrypoint demotion**: Test file entrypoint confidence penalty increased from 50% to 90% (multiplier 0.5 → 0.1). In repos like DMD where 98% of `main()` functions are in test files, the old 50% penalty left test entrypoints at 0.40 confidence — high enough to dominate auto-slicing selections. The new 90% penalty pushes them to 0.08, well below any production entrypoint. Identified via DEEP bakeoff-features-reflect (DMD assessment: 98% test mains).
- **D import-scope disambiguation**: Bare D function calls now prefer symbols from imported modules over identically-named symbols in unrelated files. The D analyzer extracts imported module paths and passes them as `path_hints` to the `NameResolver`, which uses them to disambiguate when multiple files define the same function name. Also registers symbols with module-qualified names (e.g., `errors.error`) alongside bare names, enabling suffix matching. Fixes DMD bakeoff issue where `error()` calls in `main.d` resolved to a test file's `error()` instead of the imported `dmd.errors.error()`.
- **D import edge resolution**: D `import` statements now resolve to actual module symbols when the imported module exists in the analyzed repository. Previously all D import edges used unresolved `d:?:module_name:module` format. Now internal imports (e.g., `import dmd.lexer;`) resolve to the actual module symbol's ID, creating real file-to-file edges in the dependency graph. External/standard library imports remain unresolved. Resolved imports use confidence 0.95 (up from 0.9 for unresolved).
- **Transitive entrypoint connectivity boost**: Entrypoint confidence scoring now uses one-hop transitive out-degree instead of just direct out-degree. When `main()` delegates to a high-connectivity function like `tryMain()` (178 edges), the entrypoint gets credit for the callee's reach. Entrypoints tied at the same confidence are further sorted by effective out-degree as a tiebreaker. This ensures the "real" compiler main ranks above utility/build-tool mains that happen to call many small functions directly. Also expands utility directory detection to include `vcbuild/` and `cmake/`.
- **Fix extends edge name collision (Python, JS/TS, Ruby, Kotlin, Java)**: When multiple classes share the same name (e.g., Django has 238 classes named `Model`), `extends`/`implements` edges now resolve to the correct base class using import-aware disambiguation instead of last-writer-wins. Priority: (1) same-file class, (2) import-path match (Python: `from X import Y`; JS/TS: `import { Y } from './X'`; Ruby: `require_relative 'path'`; Kotlin/Java: `import com.example.Y`), (3) deterministic fallback (sorted by ID). Previously, ALL 2376 Django `extends Model` edges pointed to a single test stub in `test_relative_fields.py`; now they resolve to `django.db.models.base.Model`.
- **Bidirectional centrality ranking**: Symbol importance now uses `in_degree * (1 + ln(1 + out_degree))` instead of pure in-degree. This rewards *connectors* (symbols with both incoming and outgoing edges, like QuerySet, Model, View) over *pure sinks* (high in-degree but zero out-degree, like exception classes and utility decorators). In Django, `cached_property` (in=365, out~1) drops from #1 while `QuerySet` (in=128, out~50) rises. Applied consistently in both `rank_symbols()` (for `.hg.json` output) and `cmd_symbols()` (for `symbols` CLI command). Identified via DEEP bakeoff-features-reflect (all 3 repos: utility/exception nodes dominated top symbols).
- **Slice hub node pruning (`--hub-threshold`)**: New `SliceQuery` parameter and CLI flag `--hub-threshold N` that prevents BFS expansion through high-degree nodes. When a node's outgoing edge count (forward slice) or incoming edge count (reverse slice) exceeds the threshold, the node is included in the slice result but its edges are not traversed. This prevents forward slice explosion through framework utility functions (e.g., `isNil`, `isUndefined` in NestJS) that connect to hundreds of unrelated files. Reports `hub_pruned` in `limits_hit`. Identified via DEEP bakeoff-features-reflect (NestJS assessment: 100% file limit hit rate, 317 nodes from single controller).
- **Return type tracking (Python, TypeScript, Java, Kotlin, C#, Dart)**: Variable type inference now handles function return type annotations across all six typed languages. When a function/method has a return type annotation and its result is assigned to a variable, subsequent method calls on that variable resolve to the return type's class methods. Python: `def get_client() -> Client` tracks from `stub = get_client()`. TypeScript: `function getClient(): Client` tracks from `const client = getClient()`. Java: `Client createClient()` tracks from `Client c = f.createClient()`. Kotlin: `fun getClient(): ServiceClient` tracks from `val client = getClient()`. C#: `ServiceClient GetClient()` tracks from `var client = factory.GetClient()`. Dart: `ServiceClient getClient()` tracks from `var client = getClient()`. Previously, only direct constructor calls enabled type inference. Only simple (non-generic) return types are tracked. C# also gained custom return type recognition in method signatures (e.g., `ServiceClient` return types were previously omitted from signatures).
- **Slice class expansion (bidirectional)**: When slicing from a class/interface entry point (forward or reverse), the slicer now automatically expands the starting set to include all member methods discovered via `contains` edges. For reverse slices, this finds callers of member methods (e.g., `--reverse --entry OwnerRepository` finds callers of `findById`, `search`). For forward slices, this is needed because `contains` edges are excluded from forward BFS, so without expansion a class entry would not reach its own methods. Expansion applies to class, interface, module, struct, trait, and enum container kinds.
- **Library export detection**: Public symbols are now detected as `LIBRARY_EXPORT` entrypoints in Go (uppercase functions/types/interfaces/structs), Elixir (modules and public `def` functions), and Python (`__init__.py` public symbols and re-exports like `from .routing import APIRouter`). Enables auto-slicing for library repos that lack `main()` or HTTP routes. Supporting pattern features: `symbol_path` (file-path matching), `modifiers` (positive filter), `modifiers_exclude` (negative filter).
- **Exclude type/interface from orphan rate**: TypeScript `type` aliases and `interface` declarations are removed from the bakeoff `_CODE_NODE_KINDS` set. These are type-level declarations, not callable code, and were 100% orphaned on GrowthBook (2,133 nodes), inflating the orphan rate from ~23% to 39%. Same pattern as the directive/scalar exclusion for Vue.
- **Containment linker**: Creates `contains` edges from class/interface symbols to their method/getter/setter symbols across all 15 supported languages using naming conventions (`.`, `#`, `::`). Handles nested classes and struct/trait/enum containers. Previously, methods were orphaned in the graph.
- **Containment linker name collision fix**: When multiple classes share the same name (e.g., Django has 238 classes named `Model` — 1 real + 237 test stubs), the linker now prefers the class in the same file as the method. Previously, the last class encountered won, causing methods to be linked to the wrong parent (e.g., `Model.save` in `base.py` was linked to a test `Model` in `test_ordinary_fields.py`).
- **D method extraction with qualified names**: Functions inside D struct/class/interface bodies now extract as `kind="method"` with qualified names (e.g., `Searcher.search`), enabling the containment linker.
- **Non-code node exclusion**: Documentation/config nodes (markdown, TOML, INI), CSS structural nodes, and config-metadata nodes are excluded by default. Use `--include-docs` to include them.
- **CSS variable/at-rule exclusion**: CSS custom properties (`--main-color`), `@keyframes`, `@media`, and `@font-face` declarations are now excluded from behavior map output via `EXCLUDED_KINDS`. On spring-petclinic, 1,185 CSS variable nodes (81% of all 1,488 nodes) had zero edges and inflated centrality Gini to 90.7%. The `symbols` CLI command also now filters out all `EXCLUDED_KINDS` entries. SCSS `$variable` nodes use the same `variable` kind and are excluded for the same reason.
- **npm_package and module_file exclusion**: External npm dependency symbols (`vue`, `@mui/material`, etc.) and synthetic JS/TS module resolution nodes are now excluded from behavior map output via `EXCLUDED_KINDS`. On chatwoot, `vue` had in-degree 589; on git-bug, `@mui/material` had in-degree 92 — inflating centrality rankings without architectural value.
- **Ruby class method (singleton_method) extraction**: `def self.method_name` definitions are now extracted as symbols with `ClassName.method_name` qualified names (dot separator). Previously, tree-sitter-ruby's `singleton_method` node type was not handled, causing all class methods to be invisible. Rails service objects (`Service.call`, `Builder.perform!`) commonly use this pattern. Calls inside class methods also now produce call edges (fixed `_get_enclosing_method` to check both `method` and `singleton_method` nodes).
- **Ruby namespaced receiver call resolution**: Method calls with fully-qualified receivers like `Voice::InboundCallBuilder.perform!()` now resolve correctly. Previously, only the rightmost segment of a `scope_resolution` receiver was extracted (e.g., `InboundCallBuilder`), causing lookups to fail when the class was defined with inline namespace syntax (`class Voice::InboundCallBuilder`). Now extracts the full namespace and falls back to the short name.
- **Ruby chained constructor call resolution**: Method calls on newly constructed objects (`Service.new(args).perform`) now resolve to the class's instance methods. When a `call` node is the receiver (i.e., the result of another method call), the analyzer now looks through to the inner call's receiver to extract the class name. This enables edge creation for the standard Rails service object pattern.
- **Ruby/Rails job enqueue edge detection**: `SomeJob.perform_later(args)`, `SomeJob.perform_async(args)`, and chained `SomeJob.set(wait: 1.hour).perform_later(args)` calls now create `enqueues` edges from the calling method to the job's `perform` instance method (confidence 0.90). Also handles `perform_in` and `perform_at`. When the `perform` method is not found in the codebase, falls back to targeting the job class symbol (0.85) or an unresolved edge (0.70). Handles namespace-qualified jobs (`Processing::BatchJob.perform_later`). Previously, these async dispatch boundaries were invisible in the behavior map — background jobs appeared as orphans because no edge connected the enqueue call site to the job's execution. Identified via DEEP bakeoff-features-reflect (Chatwoot assessment: Sidekiq/ActiveJob enqueues edges missing).
- **Rails model lifecycle callbacks**: Model callbacks (`before_save`, `after_create`, `around_update`, `before_destroy`, `after_commit`, `validate`, etc.) now create `invokes_callback` edges from the model class to the callback method, same as controller callbacks. Previously only controller callbacks (`before_action`, `after_action`) were detected. On Chatwoot, models like `Conversation` use `before_save :set_lock_event`, `after_create :notify_conversation_creation`, and `validate :validate_contact_inbox` — all now visible in the behavior map.
- **Block-style Rails callback detection**: Callbacks with `do...end` blocks or brace blocks now create `invokes_callback` edges for method calls within the block body (confidence 0.85, evidence `rails_block_callback`). For example, `after_commit do; provision_database; end` creates an edge from the model class to `provision_database`. Supports both parenthesized calls (`method()`) and bare calls (`method`). Previously only named-method callbacks (`after_commit :method_name`) were detected — block-style callbacks were completely invisible. Postal's `Server.after_create` provisions the message_db via a block callback, which was the single most critical missing edge for that codebase. Identified via DEEP bakeoff-features-reflect (Postal assessment: block callbacks missing).
- **Ruby delegate macro edge detection**: `delegate :method_name, to: :association` declarations now create `delegates_to` edges from the declaring class to the target method on the associated class (confidence 0.85). Supports multiple delegated methods (`delegate :name, :email, to: :user`) and resolves the `to:` target by PascalCasing the association name. When the target class exists but the method is not found, or the target class is missing entirely, creates unresolved edges (0.65). Special targets like `:class` are skipped. Previously, `delegate` macros were invisible — a developer refactoring `Account#auto_resolve_after` could not discover that `Conversation` depends on it via delegation. Identified via DEEP bakeoff-features-reflect (Chatwoot assessment: delegate macro edges missing).
- **Module containment edges**: The containment linker now treats `module` as a container kind, creating `contains` edges from modules to their nested classes, methods, and sub-modules. Ruby modules like `Postal::HTTP` now get `Postal contains HTTP` edges. Without this, modules with `::` separators in their names were orphaned — the 171 orphan module nodes in Postal were partly caused by missing module→class containment. Also benefits Elixir modules.
- **ActiveRecord association edge detection**: Rails model declarations `has_many`, `belongs_to`, `has_one`, and `has_and_belongs_to_many` now create `association` edges from the declaring model class to the target model class (confidence 0.90). Target class is inferred by singularizing the association name and converting to PascalCase (`:comments` → `Comment`, `:categories` → `Category`), with support for explicit `class_name:` overrides (`has_many :messages, class_name: "ChatMessage"`). When the target class is not found in the codebase (e.g., defined in a gem), creates an unresolved edge (0.70). Handles common English pluralization patterns (ies→y, sses→ss, ses→s). Previously, model-to-model relationships were invisible in the behavior map — Rails developers couldn't see data flow between models. Identified via DEEP bakeoff-features-reflect (Chatwoot assessment: ActiveRecord associations missing).
- **Go interface-implementation assertion detection**: Compile-time interface assertions (`var _ Interface = &Struct{}` and `var _ Interface = (*Struct)(nil)`) are now detected and produce `base_classes` metadata on struct symbols. The inheritance linker converts these into `implements` edges, making interface satisfaction visible in the behavior map. Supports local interfaces (`Reader`), qualified interfaces (`io.Reader`), and qualified composite literals (`&pkg.Struct{}`). Assertions across files are resolved within the same analysis pass.
- **Inheritance linker struct support**: The inheritance linker now processes `struct` kind symbols (in addition to `class` and `interface`). Previously, Go structs with `base_classes` metadata were silently skipped by both `_build_symbol_maps` (which only indexed `class` and `interface` kinds) and `_create_inheritance_edges` (which filtered out non-class/interface symbols). This caused 0 `implements`/`extends` edges for Go despite correct `base_classes` metadata. Discovered via DEEP bakeoff on git-bug: 63 structs had `base_classes` populated but zero inheritance edges were created.
- **Connectivity-based entrypoint fallback**: When no YAML patterns produce entrypoints, falls back to the most-connected callable symbols (at most 5, ranked by out-degree, confidence 0.50). Prevents `--entry auto` from hard-failing on repos with no pattern matches.
- **Decorator/annotation edge detection (INV-012)**: Decorator/annotation applications create `decorated_by` edges, making registration patterns visible in centrality and slices. Supported in Python, TypeScript, Java, C#, and Rust.

#### CLI & developer tooling

- **Secret scanning with gitleaks**: `hypergumbo sketch` scans output for potential secrets before display. Install with `hypergumbo install-gitleaks`. Opt out with `--no-secret-scan`.
- **CLI extras management**: Subcommands for optional dependencies: `add-extras`, `remove-extras`, `install-embeddings`, `uninstall-embeddings`, `uninstall-gitleaks`. Also `scripts/install-embeddings` and `scripts/uninstall-embeddings` for standalone use.
- **Cache management**: `hypergumbo cache-status` and `hypergumbo cache-clear` for managing `~/.cache/hypergumbo/`. Supports `--older-than N` and `--dry-run`.
- **`list-my-prs` script**: Lists open PRs authored by the current user.

#### Testing & CI infrastructure

- **Scoped coverage for smart-test (ADR-0011)**: Enforces 100% coverage only for changed source files, using `last-green-sha` from CI as baseline. Enables fast feedback (~45 tests in <1s vs 5700+).
- **Per-package coverage check**: `scripts/check-package-coverage` verifies each package achieves 100% coverage in isolation (mimicking CI).
- **Test placement guidelines**: Documentation in AGENTS.md on why tests must be in the same package as the code they cover.
- **CI telemetry in `auto-pr` polling**: Prints one-line job status every 3rd pass (e.g., `[89s] ✅lint ⏳pytest ⏳ci-complete`). On failure, fetches the last 30 lines of the failed job's log. Zero extra API calls during polling.
- **`ci-debug logs` subcommand**: `ci-debug logs [job] [sha]` fetches plain-text CI job logs without opening the web UI.
- **`auto-pr` manifest regeneration**: Regenerates `.ci/affected-tests.txt` before pushing and amends the commit if it changed.

### Changed

#### Output quality

- **Tiered view overhaul**: Three improvements to `format_tiered_behavior_map`: (1) budget enforcement on full output (nodes + edges + entrypoints) — previously exceeded budget by up to 2.7x; (2) connectivity-aware node selection starting from entrypoints and expanding via frontier, replacing centrality-only selection that produced disconnected subgraphs; (3) confidence-filtered force-includes (>= 0.5, capped to half capacity), preventing low-confidence test entrypoints from crowding out bridge nodes.
- **Self-loop edge filtering**: Filtered from output, adjacency lists, and connectivity scoring. Removes wasted token budget and inflated centrality.
- **Symbol ranking by individual degree**: `hypergumbo symbols` sorts by per-symbol degree instead of file-total-degree.
- **Route test filtering**: `hypergumbo routes` supports `-x`/`--exclude-tests` to filter test-file routes (consistent with `sketch`, `slice`, `explain`, `symbols`).
- **Routes command shows kind=route symbols**: `hypergumbo routes` now displays route symbols created directly by analyzers (kind="route") in addition to concept-enriched routes. Route path and HTTP method are read from `meta.route_path` and `meta.http_method` as fallback. Previously, Gorilla mux routes were invisible in `hypergumbo routes` output despite being present in the behavior map.
- **Exclude derived/minified files by default**: Tier 4 symbols excluded from output. Use `--max-tier 4` to include them.
- **Increase default `--max-files` for slice from 20 to 50**: Previous default was too restrictive for large codebases.
- **Django route method accuracy**: `path()`/`re_path()`/`url()` no longer hardcode `GET`. Django routing doesn't specify methods.
- **TypeScript decorator kind filtering**: Decorator resolution rejects class/interface/type symbols as targets. Non-function matches produce unresolved edges (confidence 0.50).
- **Vue analyzer deduplication fix**: Removed method and computed property extraction from the Vue analyzer. The JS/TS tree-sitter analyzer already processes `.vue` `<script>` sections with full precision, so the Vue analyzer's regex-based extraction created duplicate symbols (1,093 orphaned `language="vue"` method symbols on Chatwoot, 54% of remaining orphans). The Vue analyzer now focuses exclusively on Vue-specific constructs: component refs, directives, slots, props, and style blocks.
- **C/C++ `.h` file deduplication**: C analyzer now skips `.h` files when C++ files exist in the repo (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.hxx`). Both C and C++ analyzers processed `.h` files independently, creating 2x symbols. On Falco (C/C++ repo): 44/50 `.h` files were duplicated, C orphan rate was 92.1%. The C++ analyzer's tree-sitter-cpp grammar handles `.h` files correctly; the C analyzer now yields only `.c` files in mixed repos. Pure-C repos (no C++ files) are unaffected.
- **C/C++ test file tier classification**: Added `unit_tests/` directory pattern and `test_*.{cpp,cc,cxx,c,h,hpp}` file prefix pattern to supply chain tier classification. C++ test files using GTest conventions (e.g., `unit_tests/test_parser.cpp`) are now classified as tier 2 (internal_dep) instead of defaulting to tier 1 (first_party). Previously, Falco showed 100% tier1 because its `unit_tests/` directory wasn't matched.
- **Framework detection word-boundary matching**: Framework detection now uses word-boundary regex matching instead of simple substring matching on manifest file content. Previously, `"bottle" in content` matched `"bottleneck"`, causing false Bottle framework detection. On Superset, this caused 618/814 (76%) of detected routes to be `@mock.patch()` decorators misidentified as Bottle's bare `@patch` route decorator. Also removed Bottle's bare decorator patterns (`@get`, `@post`, `@put`, `@delete`, `@patch` without app prefix) which are inherently ambiguous with common Python names like `unittest.mock.patch`.
- **Fix Micronaut framework patterns to use decorator field**: Micronaut YAML patterns used `annotation:` field (which reads `meta["annotations"]`) but the Java analyzer stores all annotations in `meta["decorators"]`. Changed all `annotation:` to `decorator:` and fixed extraction paths (`annotation_value` → `args[0]`, `annotation_name` → `decorator_name_upper`). Tests were passing with artificial `meta["annotations"]` data that didn't match actual Java analyzer output. Rust and C# frameworks correctly use `annotation:` since their analyzers store in `meta["annotations"]`.

#### Dependencies & configuration

- **Embeddings now optional**: sentence-transformers (~2GB with PyTorch) no longer installed by default. Enable with `pip install hypergumbo[embeddings]`.
- **Pinned dependencies**: All dependencies use `~=X.Y.Z` (compatible release) instead of `>=X.Y`.

#### Testing & CI infrastructure

- **CI test manifest improvements**: Manifest now includes `CHANGED_SOURCE_FILES` section and `Mode:` field (`targeted`/`full-suite`). Display shows header and count instead of truncated file list. Terminology changed from "affected" to "selected."
- **Infrastructure-only PRs skip pytest**: PRs changing only scripts, YAML, or config skip pytest in `ci.yml`. Full suite still runs after merge.
- **smart-test detects all change sources**: Committed, staged, AND unstaged changes. Previously only committed.
- **Branch coverage in smart-test**: Uses `--cov-branch` locally. Branch coverage tests in separate `BRANCHES_*.py` files.
- **Consistent coverage config in full-suite.yml**: All four test jobs check for sentence-transformers and use `.coveragerc.no-embeddings` when unavailable.
- **CI worker count from cgroup CPU quota**: Reads `/sys/fs/cgroup/cpu.max` instead of `runner.name` (which Forgejo doesn't populate). Falls back to `nproc` when unlimited.
- **CI post-step timeout cap**: `SEGMENT_DOWNLOAD_TIMEOUT_MINS: "1"` on all setup-python steps.
- **Shared Forgejo API library**: Extracted duplicated API logic from `auto-pr`, `ci-debug`, and `contribute` into `scripts/lib/forgejo-api.sh`. Adds safe JSON parsing, CI polling with timeout, PR deduplication, and `ci-complete` holdout detection.
- **`merge-pr` recovery script**: Merges existing PRs with optional `--wait-for-ci` polling when `auto-pr` fails.
- **Parallel `check-package-coverage`**: Packages run in parallel by default. Use `--serial` for debugging.

#### Agent governance

- **Three-way stop hook logic**: Replaces unconditional blocking with decision logic: TODO blocking, cooldown, or full reflection.
- **Post-compaction state recovery**: `.agent/last_stop_check.json` captures branch, last PR, pending TODOs, and notes for recovering context.
- **Pre-push hook for protected branches**: Blocks direct pushes to `dev` and `main` locally.
- **Bakeoff stable artifact paths**: Timestamped session directories under `~/hypergumbo_lab_notebook/bakeoff_artifacts/`. Prior artifacts never overwritten. Convergence status in stop-hook prompts.
- **Deeper bakeoff slices**: `--max-hops 5` (from 3) with adjusted coverage thresholds.
- **Updated documentation counts**: Corrected analyzer (67→104), linker (15→18), framework pattern (37→82), convention pattern (4→5) counts across docs.
- **Remove legacy grep fallback from stop hook (ADR-0013 PR 7)**: The stop hook now uses only the structured tracker CLI (`scripts/tracker count-todos`, `hash-todos`, `guidance`) for TODO counting, circuit breaker hashing, and guidance generation. The legacy grep-based fallback that read `**TODO!**`/`**TODO**` markers from markdown files has been removed. Fail-closed: if the tracker CLI is present but fails, the stop hook blocks. Deprecation notices added to `.agent/invariant-ledger.md`.

### Fixed

- **`--entry auto` respects `--exclude-tests` and `--max-tier`**: Filters now apply to auto-entry selection, not just `--list-entries`.
- **Tiered view token budget compliance**: Was exceeding budget by up to 177x. Force-includes now sorted by centrality and capped. Non-essential fields stripped.
- **Supply chain tier deserialization**: Cached nodes always had tier=1 due to flat-vs-nested key mismatch. Fixed via `Symbol.from_dict()`.
- **Route-handler linking for same-name symbols**: Route symbols could overwrite handler functions in the lookup dict. Now preserves function/method/class symbols. Gin-realworld linking: 0% → 100%.
- **Rails route-handler suffix matching**: Routes with short controller names (e.g., `users#index`) now resolve to deeply namespaced controller methods (e.g., `Api::V1::Accounts::UsersController#index`) via suffix matching. Exact matches still take priority. Previously, Chatwoot had 911/943 routes unlinked due to namespace mismatch.
- **Rails singular resource double-s pluralization**: `resource :audit_logs` no longer generates `audit_logss#show` — names already ending in "s" are not re-pluralized.
- **Django route-handler linking**: Added `view_name` support and fixed `.as_view()` class name extraction for CBVs.
- **Rust impl method names**: Reference (`&'a M`) and generic (`Writer<'a, M, W>`) types now extract only the base identifier.
- **Phoenix route entrypoint detection**: Route symbols now get the "route" concept via `symbol_kind` pattern.
- **Ruby hash rocket route syntax**: `"path" => "controller#action"` now recognized alongside `to:` syntax.
- **httpx IPv6 CIDR proxy workaround**: Sanitizes `NO_PROXY` during model init to avoid httpx IPv6 CIDR parsing bug.
- **Dangling edge dst after tier filtering**: Edges pointing to tier-filtered nodes no longer create dangling references.
- **WebSocket generic event edge explosion**: Generic `send_text()`/`ws.send()` no longer create NxM combinatorial edges. Named event patterns (Socket.io, Django Channels) unaffected.
- **smart-test scoped mode**: No longer fails when total project coverage is below 100%.
- **bakeoff-features slicing**: Removed `--exclude-utility` from forward slices; test-file entrypoints filtered before slicing to avoid empty results.
- **CI pip cache timeout**: Disabled `cache: 'pip'` in setup-python. Local runners couldn't reach cache server (~8 min timeout per job).
- **CI worker count always 2 on local runner**: `runner.name` not populated in Forgejo. Replaced with cgroup CPU detection.
- **CI log retrieval for Codeberg**: REST API `/actions/jobs` returns 404 on Forgejo v14. Rewritten to use web route with `/attempt/1/logs`.

### Removed

- **Bootstrap mode in CI**: Stable hypergumbo now includes `slice --files`, so smart-test always generates proper manifests.

## [2.0.2] - 2026-02-01

### Changed

- **Default token budget increased to 8000**: Ensures Source Files Content section has sufficient budget to include production files. Use `-t` flag to override.

### Fixed

- **Density score path normalization**: Fixed path mismatch where cached absolute paths weren't normalized to relative paths, causing files to sort arbitrarily instead of by density.

## [2.0.1] - 2026-01-31

### Added

- **`--files` flag for slice command**: Enables smart test selection by finding all files that depend on changed code. Usage: `hypergumbo slice --files changed.txt --output affected.txt`. This reads a list of changed file paths and performs reverse dependency analysis to identify affected test files. Used by `scripts/smart-test` to generate manifests for CI.

### Fixed

- **CI manifest validation**: CI now properly filters comment lines from manifests and detects bootstrap mode (when manifest indicates full suite is required due to missing stable hypergumbo).

## [2.0.0] - 2026-01-31

### Changed

- **Modular package structure (ADR-0010)**: Restructured from a single package into 5 modular packages: `hypergumbo-core` (CLI, IR, slice, sketch, linkers), `hypergumbo-lang-mainstream` (Python, JS/TS, Java, Go, Rust, etc.), `hypergumbo-lang-common` (Haskell, Elixir, GraphQL, etc.), `hypergumbo-lang-extended1` (Zig, Agda, Solidity, etc.), and `hypergumbo` (meta-package). **Breaking change:** import paths changed from `hypergumbo.*` to `hypergumbo_core.*` / `hypergumbo_lang_*.*`. CLI usage is unchanged. See `docs/MIGRATION-2.0.md`.

### Added

- **Smart test selection (ADR-0010)**: `smart-test` uses hypergumbo's reverse-slice to run only affected tests from changed files, generating `.ci/affected-tests.txt` for CI. Includes stop-the-line protocol (bypass with `fix(job-XXXXX):` title prefix).
- **Two-tier CI system**: Fast CI uses manifest-based test selection; `full-suite.yml` runs as lazy singleton after dev merges.
- **Framework pattern detection for 30+ frameworks** across 10 ecosystems. Each framework gets route, handler, middleware, and component detection via YAML patterns. See `docs/FRAMEWORKS.md` for per-framework details.
  - **Python (8):** Falcon, Quart, Sanic, Pyramid, Bottle, Litestar, Masonite, Flask-Appbuilder
  - **PHP (7):** Symfony, CodeIgniter, Lumen, CakePHP, Yii, Laminas, FuelPHP
  - **Java/JVM (3):** Quarkus, Javalin, Vert.x; plus JAX-RS aliases for Dropwizard, Jersey, RESTEasy
  - **Kotlin (1):** Http4k
  - **Scala (2):** Scalatra, http4s
  - **Node.js (5):** Nuxt, Remix, SvelteKit, Feathers.js, AdonisJS, Restify
  - **Ruby (3):** Hanami, Roda, Padrino
  - **Clojure (2):** Ring/Compojure, Pedestal
  - **Haskell (2):** Servant, Scotty
  - **Elixir (1):** Nex
- **Utility file entrypoint penalty**: Entrypoints in utility directories (docs, examples, scripts, tools, benchmarks) receive a 50% confidence penalty.
- **Test file weighting for slice ranking**: `rank_slice_nodes()` now downweights test file nodes so production code ranks higher in reverse slices.

### Fixed

- **TypeScript constructor injection resolution (INV-013)**: `this.property.method()` calls now resolve when the property is a constructor-injected dependency (e.g., NestJS `constructor(private catsService: CatsService)`). Forward slices from controllers now include service layer calls.
- **Linker duplicate edge elimination**: Edge deduplication after linkers run prevents duplicates from the event-sourcing linker (e.g., killbill: 25494 → 25022 edges).

## [1.3.1] - 2026-01-29

### Added

- **C++ test framework patterns**: Google Test (`TEST`, `TEST_F`, `TEST_P`) and Catch2 (`TEST_CASE`, `SCENARIO`) macros now detected as `test_function` concepts. Reduces orphan function count in C++ test codebases.
- **go-restful framework support**: Added patterns for the go-restful framework (used by Kubernetes). Detects `.To()` method calls as route handlers and `restful.WebService` base class. Improves framework detection for Kubernetes-style Go APIs using the fluent RouteBuilder pattern.
- **HTTP client patterns for JavaScript/TypeScript**: Added patterns to detect frontend API calls for cross-language linking. Detects fetch(), axios, ky, got, and superagent HTTP clients as `http_client` concept. Enables future route-client linker to connect frontend API calls to backend route handlers in polyglot repos.
- **JAX-RS framework detection**: Added detection for JAX-RS (`javax.ws.rs`, `jakarta.ws.rs`), Jersey, RESTEasy, and Swagger dependencies in Java projects. Enables pattern enrichment for Java REST APIs using JAX-RS annotations (`@GET`, `@POST`, `@Path`, etc.).

### Fixed

- **F# analyzer Forth file disambiguation**: The F# analyzer now detects and skips Forth files that share the `.fs` extension (Open Firmware Forth, GForth). Prevents analyzer hangs on repositories like qemu-slof that contain Forth code with `.fs` extension. Detection uses content heuristics (backslash comments, Forth keywords like `VALUE`, `CONSTANT`, `:` word definitions).

- **Ruby analyzer duplicate edge elimination**: Fixed duplicate edges being created for the same call site when an identifier was both processed as part of a `call` node and separately as a bare `identifier`. Now skips identifiers that are children of call-related nodes, reducing edge count noise by 10-30% in Ruby codebases.

- **Bakeoff GraphQL false positive**: Fixed `EXPECTED_ROUTES_BUT_FOUND_0` false positive for GraphQL frameworks (apollo-server, etc.) that don't use traditional HTTP routes. Repos with "graphql" or "apollo" in name are now excluded from route expectations.

- **GraphQL entrypoint detection**: Updated GraphQL framework patterns (graphql.yaml, graphql-python.yaml, graphql-ruby.yaml) to use `graphql_resolver` and `graphql_schema` concept names, enabling proper entrypoint detection for GraphQL resolvers in JavaScript/TypeScript, Python, and Ruby codebases.

- **Duplicate edge elimination in analysis pipeline**: Added edge deduplication by ID after analyzer runs complete. Some analyzers (e.g., Ruby) could produce duplicate edges with identical IDs; these are now filtered out before writing the behavior map. Example: postal repo went from 3220 edges (114 duplicates) to 3097 unique edges.

- **Ruby analyzer method field extraction**: Fixed root cause of duplicate edges in Ruby analyzer. The code was finding the first identifier child of call nodes, which for `receiver.method` calls like `data.chop` would incorrectly identify "data" (receiver) instead of "chop" (method). Now uses tree-sitter's `child_by_field_name("method")` to correctly extract the method name.

## [1.3.0] - 2026-01-29

### Added

- **Centralized inheritance linker**: New `linkers/inheritance.py` creates `extends`/`implements` edges from `base_classes` metadata across ALL languages, eliminating duplicate edge-creation logic in individual analyzers.

### Fixed

- **Python/JS/TS inheritance edges (INV-008)**: Classes with `base_classes` metadata now create `extends` and `implements` edges to base classes/interfaces defined in the repo. This enables the type hierarchy linker to create `dispatches_to` edges for polymorphic dispatch.
- **Ruby/Kotlin inheritance edges (INV-009)**: Ruby and Kotlin analyzers now extract inheritance information.
- **Swift/C++/Objective-C/Apex base_classes extraction**: Completes META-001 (Metadata Must Become Graph Structure) at 100%. All 13 languages with class inheritance now extract `base_classes` metadata:
  - Swift: class/struct/protocol inheritance and protocol conformance
  - C++: class/struct inheritance with qualified names (std::exception)
  - Objective-C: superclass + protocol conformance
  - Apex: extends + implements clauses

## [1.2.1] - 2026-01-29

### Summary

Major expansion: **37 new analyzers** across languages, templates, config formats, and build systems. New **route-handler** and **type hierarchy** linkers improve web framework and OO codebase navigation. CLI gains `compact` subcommand. Multiple bug fixes for edge uniqueness, entrypoint detection, and crash resilience.

### Added

#### CLI
- **`compact`**: Post-process behavior maps into compact form. Options: `--input`, `--out`, `--max-symbols`, `--coverage`, `--no-connectivity`.

#### Analyzers: Frontend & templates
- **Twig**: blocks/extends/includes/macros; `extends_template` / `includes_template` edges.
- **SCSS/Sass**: variables/mixins/functions/rules; `uses_mixin` edges.
- **Svelte**: imports, slots, events, control flow; `imports_component` edges.
- **Vue SFC**: directives/slots/methods/props; two-pass import resolution.
- **Astro**: frontmatter, imports, slots, client directives; two-pass import resolution.

#### Analyzers: Programming languages (16)
- **Odin**: procedures/structs/enums/unions; imports + cross-file calls.
- **Gleam**: functions/types/aliases; visibility + signatures; imports + calls.
- **V**: functions/structs/enums/interfaces; visibility + signatures; imports + calls.
- **MATLAB**: functions/classes/methods/properties; signatures + cross-file calls.
- **Tcl/Tk**: procedures/namespaces; call edges (filters built-ins).
- **Scheme**: defs + recursive calls; filters special forms (`.scm/.ss/.sld/.sls`).
- **Racket**: defs/structs + recursive calls; `struct`/`module+` (`.rkt/.rktl/.rktd`).
- **Janet**: defs + recursive calls; filters special forms.
- **Fennel**: defs + recursive calls; compiles to Lua.
- **Pascal**: programs/units/functions/procs; case-insensitive calls (`.pas/.pp/.dpr/.lpr`).
- **Haxe**: classes/interfaces/functions; visibility/static; qualified calls.
- **PureScript**: modules/functions/types/classes/instances; qualified calls.
- **Hack**: classes/traits/functions/methods; visibility/static (`.hack/.hh`).
- **Apex**: classes/triggers/methods/fields; visibility/override; qualified calls.
- **Luau**: typed functions + types; qualified calls (`.luau/.lua`).
- **Pony**: actors/classes; reference capabilities; cross-file calls.

#### Analyzers: Data, schema & DSLs (5)
- **KDL**: nodes/sections; arguments/properties; nested hierarchies.
- **Prisma**: models/enums/datasources/generators; `@relation` edges.
- **Smithy**: services/operations/shapes; namespace-qualified names; type refs.
- **SPARQL**: PREFIX/BASE + queries; `uses_vocabulary` edges.
- **Jsonnet**: locals/methods/fields; imports + calls.

#### Analyzers: Build systems & DevOps (4)
- **Meson**: projects/targets/custom targets; deps + subdir includes.
- **BitBake**: recipe vars, inherit, tasks; DEPENDS/RDEPENDS edges.
- **Robot Framework**: keywords/tests/vars; cross-file keyword invocation.
- **Puppet**: classes/defined types/resources; parameter extraction.

#### Analyzers: Docs & config files (7)
- **BibTeX**: bibliography entries, citation keys, authors/years/titles.
- **Markdown**: headings/code blocks/links; `links_to` edges.
- **RST**: sections/directives/refs; toctree/include + cross-doc refs.
- **requirements.txt**: constraints, VCS/URL/editable; `-r/-c` includes.
- **.properties**: key/value + domain categorization; masks secrets.
- **.gitignore**: pattern classification + domain categories.
- **INI/CFG**: sections/settings + domain categorization; masks secrets.

#### Linkers (2)
- **Route-handler linker**: Creates `routes_to` edges from route symbols to handler functions. Supports Rails, Phoenix, Laravel, and Express metadata formats.
- **Type hierarchy linker**: Creates `dispatches_to` edges for polymorphic dispatch. Connects interface/parent methods to concrete implementations (valuable for DI-heavy codebases).

#### Entrypoint detection
- **Manifest-based**: `package.json "bin"`, `pyproject.toml [project.scripts]`, `Cargo.toml [[bin]]` detected with 0.99 confidence.
- **Naming-based**: Classes named `*Controller`, `*Handler`, `*Service` detected with 0.70 confidence (heuristic fallback).
- **Structural**: Python `if __name__ == "__main__"` detected with 0.85 confidence.

#### Framework route extraction
- **Rails**: `resources`/`resource` macros emit individual route symbols for all RESTful actions.
- **Phoenix**: Elixir analyzer creates route symbols with controller/action metadata.
- **Laravel**: PHP analyzer creates route symbols including `Route::resource()` expansion.

#### Quality & governance
- **Meta-invariants**: Introduced three high-level quality principles that unify specific bug fixes:
  - META-001: Metadata Must Become Graph Structure (90%) — semantic relationships in metadata must become traversable edges
  - META-002: Extraction Completeness (95%) — symbols in source code must be extracted for analysis
  - META-003: Data Integrity (100%) — graph elements must have valid, unique identifiers
- **Invariant ledger**: Tracks discovered invariants, root causes, fixes, and regression tests (`.agent/invariant-ledger.md`).

### Fixed

#### Crashes & robustness
- **JSON manifests**: No longer crash when `package.json`/`composer.json` top-level is non-object.
- **Ruby analyzer**: Prevent self-referential call edges.

#### Graph quality (INV-002 through INV-006)
- **INV-006**: Rails `resources`/`resource` macros now infer `controller_action` metadata for route-handler linking.
- **INV-005**: Edge IDs include line number, ensuring uniqueness for multiple calls to same target.
- **INV-004**: Routes get `routes_to` edges to handler functions (metadata now converted to traversable edges).
- **INV-002**: Deferred resolution for cross-file handler references (Django URL patterns, Express routes, etc.).

#### Python analyzer
- **Nested functions**: Extract decorated nested functions (FastAPI router factory pattern).
- **Main guard**: `if __name__ == "__main__"` uses correct concept format for entrypoint detection.
- **Django**: Empty path URL patterns (`path('')`) now correctly detected as routes.

#### Entrypoint detection
- **cargo_binary**: YAML pattern now matches `kind="binary"` (actual analyzer output).
- **HTTP linker**: Falls back to direct `meta.route_path`/`meta.http_method` when concept metadata unavailable.

#### Symbol resolution
- **INV-007**: Go import path resolution now correctly disambiguates when multiple files define the same symbol (e.g., generated protobuf files). `ListNameResolver` tries progressively shorter path suffixes and falls back to deterministic ordering.

## [1.1.0] - 2026-01-24

> Note: This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v1.0.0. Hopefully our next release will be hiccup-free and actually publish to PyPI.

### Removed

- **Vestigial capsule system** (ADR cleanup)
  - Removed `init` and `export-capsule` commands (marked vestigial in spec)
  - Removed `plan.py`, `llm_assist.py`, `export.py` modules
  - Removed deprecated `Pack` class from catalog (packs replaced by linker activation conditions)
  - Removed `llm-assist` and `llm-local` optional dependencies from pyproject.toml

### Added

**YAML-Driven Analysis (ADR-0003)**
- Main function detection via `main-functions.yaml` for 10 languages (Go, Java, Python, C, C++, Rust, C#, Kotlin, Swift, Dart)
- Test function detection via `test-frameworks.yaml` for 10+ frameworks (pytest, JUnit, RSpec, etc.)
- Language conventions: CUDA kernels, WGSL shaders, COBOL, LaTeX, Starlark (`language-conventions.yaml`)
- Config conventions: NPM, Maven, Android, Cargo, Poetry, TypeScript (`config-conventions.yaml`)
- Pattern system extended with `symbol_name`, `language`, and `prefix_from_parent` fields
- Framework pattern types added to `docs/schema.json` for YAML validation
- YAML linting via `yamllint` in pre-commit hooks
- Play Framework patterns for Scala (`play.yaml`): controllers, Action blocks, WebSocket handlers
- Akka HTTP patterns for Scala (`akka-http.yaml`): route directives, method handlers, WebSocket, auth
- Library export detection (`library-exports.yaml`): Detects exports from index files (index.ts/js/jsx/tsx) as library entry points for JS/TS libraries
- Naming conventions (`naming-conventions.yaml`): Heuristic patterns for `*Controller`, `*Handler`, `*Service` classes (0.70 confidence fallback tier)

**New Commands & Flags**
- `hypergumbo test-coverage`: Static coverage estimation via call graph analysis
- `-x/--exclude-tests`: Exclude test files from sketch sections
- `--progress`: Show ETA during sketch generation
- `--readme-debug`: Debug README extraction algorithm
- `--help --all`: Show all subcommand help at once
- `slice --flat`: Output simple `{nodes, edges}` format for external tools (implies `--inline`)

**Sketch Improvements**
- Source code included by default (`--no-source` to disable)
- "How Representative Is This Sketch?" table showing coverage per section
- README-first hybrid ranking for Additional Files (round-robin: linked/similar/central)
- Multi-format README link extraction (Markdown, Org-mode, RST, AsciiDoc)
- Embedding-based README description extraction with pre-computed probes
- Estimated coverage in Tests section (e.g., "~35% estimated coverage")
- Separate test/non-test LOC breakdown in Overview

**Analyzer Improvements**
- Shared SymbolResolver framework for cross-file resolution (45+ analyzers)
- Parameter type inference for Python, Java, Kotlin, TypeScript
- Common Lisp analyzer (`.lisp`, `.lsp`, `.cl`, `.asd`)
- LLVM IR analyzer (`.ll` files)
- ADR-0004: File taxonomy with `FileRole` enum and 75+ language specs
- ADR-0007: Import tracking for cross-file call resolution
  - Phase 1 complete: JS/TS, Kotlin bug fixes
  - Phase 2 complete: Rust, C#, Ruby, Elixir, Swift, PHP, Scala, Dart
  - Phase 3A complete: Ada, Agda, Clojure, C++, D, Elm, Erlang, F#, Fortran, Groovy, Haskell, Julia, Nim, OCaml, R, Solidity, Starlark, Zig (18 done; Lean blocked by grammar, VHDL has no aliasing)

**CLI Ergonomics**
- Auto-run analysis for query commands when no cached results exist
- Auto-discovery of cached results from `~/.cache/hypergumbo/`
- Slice path suffix matching (`--entry src/main.go` matches full paths)
- Symbol-specific slice output naming (`slice.main.json`)
- Artifact location reporting and summary after `hypergumbo run`
- Forge URL resolution for README links (GitHub/GitLab/Codeberg)

### Changed
- **auto-pr uses fast-forward merge by default**: Preserves commit bodies and DCO. Prompts to rebase if diverged. `--squash` available as emergency fallback (uses git notes).
- **Schema version 0.2.1**: Added framework pattern types to `docs/schema.json`
- Section headers renamed: "Source Content" → "Source Files Content", etc.
- Overview always shows test/non-test breakdown; Tests section always present
- Additional Files excludes boilerplate (LICENSE, .gitignore, CODEOWNERS)
- CI skips expensive jobs for docs-only PRs
- pytest-xdist for parallel tests (`pytest -n auto`)

### Fixed

**Git Notes Recovery**
- Restored 193 orphaned commit bodies via git notes (squash-merged Jan 9-22 2026). View: `git log --show-notes`

**Compact Mode**
- Edge filter changed from OR to AND (was wasting 99%+ on dangling edges)
- Entrypoints filtered to resolvable IDs (fixes "No entrypoints detected")
- Force-include entrypoints in selection (preserves semantic anchors)
- Connectivity-aware selection using greedy frontier algorithm (4x more edges)
- Entrypoints capped to `max_symbols // 2` to leave room for bridge nodes

**Sketch Output**
- File content truncation accounts for markers (~130 chars overhead); files end with newline
- `-x` flag correctly counts non-code repos
- Unified test detection between Overview and Tests sections
- Added `tests.py` and `*_spec.rb` to test detection
- Structure section: tree format with `-x`, shows all root directories, handles flat repos
- Representativeness table shows with `-x` and correct budget for small sketches
- Additional Files representativeness uses mention centrality
- Elevator pitch truncation respects sentence boundaries
- Embedding-based README extraction handles soft line breaks

**Call Graph**
- C/C++ analyzers prefer definitions over declarations (fixes coverage estimation)
- NestJS route paths combine controller + method via `prefix_from_parent`
- NestJS routes normalize to start with `/` (fixes `[GET] test` → `[GET] /test`)
- Framework aliases: Go web frameworks (gin, chi, echo, fiber) now load `go-web.yaml`; Rust web frameworks (axum, actix-web, rocket, warp) now load `rust-web.yaml`
- Python: submodule imports resolve (`from app import crud; crud.func()`)
- Python: imported class method calls resolve (`from X import Class; Class.method()`)

**Entrypoints**
- `slice --list-entries` now respects `--exclude-tests` and `--max-tier` filters

**Other**
- `explain --with-source` output ordering (callers/callees grouped with sources)
- Minimum chunk size for license files in semantic search
- Removed misleading "Coverage requires execution" message

## [1.0.0] - 2026-01-12 (not released to PyPI)

> **Note:** This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v0.9.1.

Major focus on memory optimization, framework detection improvements,
and completing the migration to YAML-driven semantic analysis.

### Fixed
- **Memory optimization for large repos:** Reduced peak memory from ~11GB to ~2.1GB (80%
  reduction) for repositories like tensorflow. Uses streaming JSON output and aggressive
  cleanup of intermediate data structures.
- **Android framework detection:** Now detects Android via `android {}` blocks in build.gradle,
  AndroidManifest.xml presence, and gradle plugin dependencies.
- **JSON serialization of Python literals:** Complex numbers and bytes literals no longer cause errors.
- **`--frameworks all` and explicit lists:** Now bypass dependency scanning, enabling pattern
  matching even when manifests are in subdirectories.
- **Express route detection:** Fixed case-sensitive HTTP method comparison.
- **Slice command:** Now runs all language analyzers, not just Python/HTML.

### Added
- **Recursive manifest scanning:** Scans up to 3 levels deep for dependency manifests (monorepo support).
- **Ruby/Elixir framework detection:** Gemfile and mix.exs scanning for Rails, Phoenix, etc.
- **Usage-based pattern matching:** Route detection for call-based frameworks (Django `path()`,
  Express `app.get()`, Rails route DSL, Go Gin, etc.) via YAML patterns.
- **12 new framework YAML patterns:** ktor, vapor, plug, fastify, grape, tornado, aiohttp,
  slim, micronaut, graphql, electron, cli.

### Changed
- **Entrypoint detection now 100% YAML-driven:** Removed 26 legacy detection functions (~1,700 lines).

## [0.9.1] - 2026-01-09

### Fixed
- **Incomplete v0.9.0 release:** v0.9.0 was accidentally built from the wrong branch. This
  release includes all ADR-0003 features. Users should upgrade from v0.9.0 to v0.9.1.

## [0.9.0] - 2026-01-09 (INCOMPLETE RELEASE)

> **Warning:** This release was built from the wrong branch. Please use v0.9.1 instead.

### Changed (Breaking)
- **Schema version 0.2.0:** New `entrypoints` field added to behavior map output.

### Added
- **`--frameworks` flag:** Control framework detection (`none`, `all`, `fastapi,celery`, or auto-detect).
- **Entrypoints in JSON output:** Detected entrypoints now persisted in output with stable IDs.
- **Smart JSON detection in slice command:** `.json` files auto-detected as `--input`.
- **Connectivity-based entrypoint ranking:** Entrypoints ranked by graph connectivity for better `--entry auto`.
- **Linker activation conditions:** Linkers now have structured activation criteria (always, frameworks, language_pairs).
- **Rich metadata extraction:** Decorators/annotations with args/kwargs for Python, JS/TS, Java, C#.
- **YAML-driven framework patterns:** Data-driven symbol enrichment via `src/hypergumbo/frameworks/*.yaml`.
  - Initial patterns for: FastAPI, Flask, Django, Express, NestJS, Spring Boot, Rails, Phoenix,
    Laravel, Go web frameworks (Gin/Echo/Fiber/Chi), Rust web frameworks (Actix/Rocket),
    ASP.NET Core, Hapi, Koa, Celery, and more.
  - See `docs/ARCHITECTURE.md` for the full pattern inventory.
- **Semantic entry detection:** Entrypoint detection via concept metadata (highest priority, 0.95 confidence).
- **HTTP linker concept support:** Extracts route info from concept metadata.

### Changed
- **Python analyzer purified:** Route detection moved from analyzer to YAML patterns.

### Deprecated
- **Packs:** Framework-specific analysis now uses `--frameworks` flag instead of packs.
- **Path-based entrypoint heuristics:** Prefer semantic detection via YAML patterns.
- **Analyzer-level route detection:** Route detection moving to YAML patterns (1.0.x migration).

## [0.6.9] - 2026-01-07

### Added
- **Connectivity-aware auto-slicing:** `--entry auto` prefers well-connected entrypoints.
- **Improved slice traversal:** Synthetic linker nodes connected via `uses` edges.
- **Stronger cross-file call resolution:** Module-qualified calls and lightweight type inference.
- **Linker diagnostics:** `LinkerRequirement` checks and registry pattern for linker execution.
- **Variable-based linker matching:** URLs/event names in variables detected (lower confidence).

### Fixed
- **Route detection false positives:** Excluded `fetchMock.get()`, `axios.post()`, etc. from Express routes.
- **Entrypoint false positives:** Excluded React file-routing, non-web handlers, DNS resolvers, etc.

### Changed
- **Linker consolidation:** All linkers migrated to `@register_linker` registry pattern.

## [0.6.0] - 2025-12-29

### Added
- **New analyzers:** Lean 4 (theorem prover), Wolfram Language (Mathematica), Agda (proof assistant).
- **Build-from-source grammars:** `scripts/build-source-grammars` for experimental tree-sitter grammars.
- **Contributor workflow:** `scripts/contribute` for fork-based contributions.
- **Release automation:** `scripts/release-check`, `scripts/release`, `scripts/integration-test`.
- **Sketch improvements:** Two-phase symbol selection, per-file compression, deterministic output.

See `docs/ARCHITECTURE.md` for the full language/framework support matrix.

## [0.5.0] - 2025-12-26

Initial public release with comprehensive static analysis capabilities.

### Core Commands
- `hypergumbo [path]` - Token-budgeted Markdown sketch
- `hypergumbo run [path]` - Full JSON behavior map
- `hypergumbo slice --entry X` - BFS/DFS subgraph extraction
- `hypergumbo routes [path]` - HTTP route listing
- `hypergumbo search <query>` - Symbol search

### Analysis Capabilities
- **32 language analyzers:** Python (AST), Java, Rust, Go, JavaScript, TypeScript, C, C++, C#,
  Ruby, PHP, Swift, Kotlin, Scala, Haskell, OCaml, Elixir, Lua, Zig, Solidity, Julia, Groovy,
  SQL, CUDA, Verilog, VHDL, GLSL, WGSL, Fortran, Bash, and more. See `docs/ARCHITECTURE.md`.
- **12 cross-language linkers:** HTTP, WebSocket, Message Queue (Kafka/RabbitMQ/SQS/Redis),
  GraphQL, gRPC, Database Query, Event Sourcing, IPC (Electron/WebWorker), JNI, Swift-ObjC,
  Phoenix Channels.
- **Framework detection:** 100+ frameworks across Python, JavaScript, Rust, Go, PHP, Java, etc.
- **Supply chain classification:** Tier 1-4 (first-party, internal deps, external deps, derived).

### Output Schema
- `schema_version`, `profile`, `nodes[]`, `edges[]`, `analysis_runs[]`, `metrics`, `limits`
- Symbols include spans, stable IDs, supply chain tier, and optional metrics.

---

## Version History

| Version | Date       | Highlights                                                   |
| ------- | ---------- | ------------------------------------------------------------ |
| 1.0.0   | 2026-01-12 | Memory optimization (80% reduction), 100% YAML-driven entrypoints |
| 0.9.1   | 2026-01-09 | ADR-0003 implementation (was missing in 0.9.0)               |
| 0.9.0   | 2026-01-09 | Schema 0.2.0, --frameworks flag, YAML patterns (incomplete)  |
| 0.6.9   | 2026-01-07 | Fewer false positives, richer slice traversal                |
| 0.6.0   | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation            |
| 0.5.0   | 2025-12-26 | Initial release: 32 analyzers, 12 linkers                    |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.0.0...HEAD
[1.0.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.1...v1.0.0
[0.9.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.0...v0.9.1
[0.9.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.9...v0.9.0
[0.6.9]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...v0.6.9
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
