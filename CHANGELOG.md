# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.1.0
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

- **MCP framework pattern detection**: TypeScript (8 patterns) and Python (10 patterns) for Model Context Protocol tool/resource/prompt registration. Framework detection via `@modelcontextprotocol/sdk` (JS) and `mcp`/`fastmcp` (Python) in manifests.
- **Circom language support**: Tree-sitter analyzer for `.circom` zero-knowledge circuit files (built from source). Extracts templates, functions, signals with visibility, and main components. Call edges for template instantiation, import edges for includes. Entrypoint detection for both library and application repos.
- **`--max-file-bytes` CLI argument**: Skips files exceeding the specified size during `run`. Useful for skipping minified JS, huge HTML, and generated files that slow analysis.
- **Sketch harmonic budget allocation**: `--with-source` now uses harmonic weighting (rank *i* gets budget·(1/*i*)/H_n tokens) instead of elbow+median truncation, giving top-ranked files proportionally more depth.
- **Solidity call graph**: Inheritance, override, and emit edges. Visibility modifiers for entrypoint detection. `using Library for Type` resolution (`x.add(y)` → `SafeMath.add`).
- **Rust**: `implements` edges from `impl Trait for Struct`. Turbofish/fully-qualified generic call resolution (`PublicParams::<E1,E2>::setup()`). Generic trait method blocklist (33 names like `.into()`, `.clone()`) to prevent false-positive in-degree. `#[cfg(test)]` module inheritance for correct production slice exclusion. Unresolved trait impl edges: when a trait definition isn't in the analyzed files (e.g., tonic gRPC traits from generated .proto), a lower-confidence (0.70) implements edge is created to an unresolved target. Standard library traits (Clone, Debug, etc.) are excluded via blocklist.
- **Build target linker**: Connects manifest-declared build targets (Cargo `[[bin]]`, npm `bin`) to their `main()` functions. Forward slices from Cargo binary entrypoints now traverse into actual application code instead of being dead ends. Previously, `defines_target` edges pointed to bare file paths that the slicer couldn't follow.
- **npm `bin` defines_target edges**: JSON config analyzer now emits `defines_target` edges for npm `bin` entries (both object and string forms), mirroring Cargo `[[bin]]` behavior. Enables the build-target linker to connect npm CLI entrypoints to their source files.
- **Centrality cross-file degree weighting**: Within-file edges contribute 0.3x to in-degree, cross-file edges contribute 1.0×. Prevents local variables referenced many times within a single file (e.g., `pSpec` with 96 in-degree in ArkLib) from outranking architecturally important symbols referenced across many files.
- **Annotation-based test detection in rslice seed selection**: Bakeoff seed selection now uses node annotations (`#[test]`, `#[cfg(test)]`, `@Test`, etc.) in addition to file-path patterns to identify test nodes. Fixes Rust repos (jolt, sp1) where test functions in non-test files (e.g., `src/checking.rs`) were selected as rslice seeds and inflating production in-degree.
- **Slicing**: `node_tiers` field propagates each node's supply chain tier into slice output, enabling tier-based filtering without the full behavior map.
- **Formal methods**: `references` edges in Agda (pattern clause bodies and type signatures) and Lean (def/theorem/lemma bodies). Agda type signature scanning (confidence 0.75) connects types referenced only in signatures, reducing orphan rates. Record field extraction (kind=field) with type signatures.
- **Stapler framework route synthesis**: `doXxx` → POST /xxx, `getXxx` → GET /xxx convention-based route_path and http_method metadata for Jenkins/Stapler handler methods.
- **Jakarta CDI `@Produces` binding detection**: DI linker recognizes CDI producer methods for interface-to-implementation resolution.
- **Java inferred return type for Object-returning methods**: When a Java method declares `Object` as its return type but the body only contains `return new X(...)` statements (all the same concrete type), the analyzer infers the concrete return type and stores it as `inferred_return_type` in symbol metadata. This enables JAX-RS subresource locator path composition for methods like keycloak's `OIDCLoginProtocolService.token()` which returns `Object` but constructs `TokenEndpoint`.
- **FastAPI APIRouter prefix composition**: Routes registered on a prefixed `APIRouter(prefix="/v2")` now have the prefix composed with the route path. Handles `add_api_route()` calls and `@router.get()` decorators. Resolves prefixes from string literals, same-file constants, and cross-file imported constants. Fixes kserve's invisible V2/V1/OpenAI routes (19 missing routes in bakeoff-reflect assessment).
- **Go anonymous closure route detection**: Routes registered with anonymous function handlers (`r.Get("/path", func(w http.ResponseWriter, r *http.Request) {...})`) are now detected. Previously only named handler functions produced route symbols. Named handlers are still preferred over closures when both are present (e.g., `r.GET("/path", middleware, func(){...})` → uses `middleware`). Fixes alertmanager's missing health/ready/reload endpoints (10 routes from bakeoff-reflect assessment).
- **Elixir guard clause function extraction**: Multi-clause functions with `when` guards (e.g., `def humanize(atom) when is_atom(atom)`) now appear as symbol nodes. Tree-sitter wraps guarded heads in a `binary_operator` node that was previously unhandled. Fixes Phoenix.Naming.humanize being entirely absent from the node graph.
- **Elixir pipe operator call edges**: Idiomatic pipe calls without parentheses (`data |> func`) now create call edges. Tree-sitter parses the pipe's right-hand side as an identifier rather than a call node. Fixes missing edges for `defp` functions called via pipe chains (e.g., Phoenix.Digester.fixup_sourcemaps).
- **Elixir stdlib function exclusion**: Bare calls to Kernel/stdlib functions (inspect, to_string, length, etc.) no longer resolve to project-defined functions with the same name in other modules. Local same-module definitions still shadow correctly. Fixes Phoenix.Socket.Message.inspect having 158 false in-degree edges from Kernel.inspect calls.
- **Flask-RESTful `add_resource` route detection**: `api.add_resource(TodoList, '/todos')` now emits UsageContext records with `route_path` metadata. Supports multiple URL paths, attribute-style class references (`views.TodoList`), and APIRouter prefix composition. Fixes flask-restful repo's missing routes from bakeoff-reflect assessment.
- **Rails inline `on: :member` / `on: :collection` route detection**: Routes like `get :setup, on: :member` and `get :active, on: :collection` inside resource blocks are now detected. Previously only `member do...end` block syntax was supported. Fixes 26 missing routes in postal bakeoff assessment.
- **Rails `only:` / `except:` action filters for resources**: `resources :users, except: [:destroy]` and `resources :posts, only: [:index, :show]` now filter the generated RESTful routes. Applies to both plural `resources` and singular `resource`. Previously all 7 (or 6) standard routes were always generated regardless of filters.
- **Go variable-based router group prefix composition**: Gin/Echo/Fiber-style group patterns (`api := r.Group("/api"); api.GET("/users", handler)`) now compose the group prefix into route paths. Nested groups also compose correctly (`v1 := api.Group("/v1")` → `/api/v1/...`). Previously only closure-based groups (Macaron/Chi) were supported. Fixes 20 routes missing `/api` prefix in gin-realworld bakeoff assessment.
- **Manifest build-target extraction for 11 languages**: Single DRY module (`manifest_targets.py`) extracts `defines_target` edges from: Gradle `build.gradle(.kts)` mainClass, C# `.csproj` StartupObject, Dart `pubspec.yaml` executables, Swift `Package.swift` executableTarget, Haskell `.cabal` executable stanzas, Elixir `mix.exs` escript main_module, Ruby `*.gemspec` executables, Scala `build.sbt` mainClass, OCaml `dune` executable stanzas, Zig `build.zig` addExecutable, Nim `*.nimble` bin field. Uses regex/text parsing (no tree-sitter grammars required). Combined with existing Cargo/npm/pyproject.toml/Maven support, build-target entry point detection now covers 15 ecosystems.
- **Top-level call edge attribution (6 languages)**: JS/TS, Bash, PHP, Perl, and PowerShell analyzers now create `<module:filename>` symbols and attribute top-level function calls to them, matching Python's existing pattern. Previously, calls outside any function body were silently dropped because `_get_enclosing_function()` returned None. socketio-chat-example: 0→30 edges.
- **Rust `Self::` call resolution**: `Self::method()` calls inside `impl` blocks now resolve to the actual type (e.g., `Self::process()` → `Server::process()`). Uses `_get_impl_target()` to walk up to the enclosing `impl_item` and extract the concrete type name.
- **Rust async spawn call graph detection**: `tokio::spawn()`, `rayon::spawn()`, and `async_std::task::spawn()` patterns now create edges to the spawned task instead of to `spawn()` itself. Bare function identifiers passed as arguments (e.g., `tokio::spawn(my_task)`) produce `async_spawn` evidence-type edges.
- **Rust macro body call detection**: Function calls inside macro bodies (`tokio::select!`, `assert!`, `println!`, etc.) are now detected. Tree-sitter parses macro bodies as flat `token_tree` nodes, making call expressions invisible. A new `_extract_macro_call_names()` heuristic pattern-matches token sequences for simple calls (`foo()`), scoped calls (`Foo::bar()`), `Self::method()`, and `self.method()`. Creates `macro_body_call` edges (confidence 0.75). Fixes the #1 gap in DEEP bakeoff: `CoreDns::run` → `process_message` inside `tokio::select!` in aardvark-dns.
- **Adaptive forward slice hop limit**: `--max-hops` default is now adaptive based on graph size instead of fixed at 3. Small graphs (≤200 nodes) get 10 hops, medium (≤500) get 7, large (≤2000) get 5, and very large (>2000) stay at 3. This fixes aardvark-dns where the call chain from `main()` to `process_message` is 7 hops deep but the slice stopped at depth 3.

### Fixed

- **JS/TS and Rust logging method blocklists**: Added `log`, `warn`, `error`, `info`, `debug`, `trace` to `JS_BUILTIN_METHODS` and `_RUST_GENERIC_TRAIT_METHODS`. Also added `output`, `status`, `spawn` to Rust blocklist. Prevents false-positive call edges from `this.logger.warn()` resolving to test file methods (14% FP rate in apollo-server) and `Command::output()` resolving to test utilities (ripgrep).
- **NestJS `app.get(Service)` false positive route suppression**: Calls like `app.get(AppService)` and `app.get(DYNAMIC_TOKEN)` in NestJS test files are no longer misidentified as Express route registrations. Express route calls require a string path argument; DI lookups without a path are now skipped. Fixes 15 false positive route symbols in nest bakeoff assessment.
- **Go single-arg `.Get()` false positive route suppression**: Calls like `cache.Get("/key")`, `field.Tag.Get("/metric")`, `Header.Get("/Authorization")` are no longer misidentified as HTTP route registrations. Real route registrations always have a handler argument; single-arg `.Get()` calls on maps, caches, and headers are now skipped. Fixes 119 false positive route UsageContexts in jaeger bakeoff assessment.
- **Test entrypoints no longer inflated by connectivity boost**: Connectivity boost is now skipped for `TEST_FUNCTION` entrypoints and any entrypoint in a test file. Previously, test functions with outgoing edges got boosted from 0.08 (after 90% test penalty) to ~0.33, passing the MIN_ENTRYPOINT_CONFIDENCE filter and flooding `entries.txt` with hundreds of test entries. Self-analysis showed 186 test functions at 0.33 confidence diluting 8 real entrypoints.
- **Pure sink dampening for utility helpers with docstrings**: The trivial sink dampening now has a relaxed LOC threshold (20 lines) for pure sinks (out_degree == 0). Previously, utility helpers like `node_text` (in=496, out=0, 11 lines including docstring) and `find_child_by_type` (in=291, out=0, 16 lines) escaped dampening because their span exceeded the strict max_loc=5 threshold. Pure sinks call nothing — even with docstrings making them 10-20 lines, they're leaf helpers, not architectural functions.
- **Hub pruning exempts `dispatches_to` edges**: Hub pruning now counts only non-dispatch edges toward the threshold. Registry dispatch sites (e.g., `run_all_analyzers` with 139 `dispatches_to` edges) were being hub-pruned at depth ≥ 2, causing forward slices to miss all registered handlers. When a node IS hub-pruned for having too many `calls` edges, its `dispatches_to` edges are still followed.
- **Dispatch site dedup removed**: The decorator dispatch linker no longer deduplicates same-name dispatch sites. When `run_all_analyzers` exists in both `registry.py` and `all_analyzers.py`, both get `dispatches_to` edges to handlers. Previously only the shortest-path instance got edges, but the BFS may reach the other instance, causing all dispatch targets to be missed.
- **Python pyproject.toml script entry points**: `[project.scripts]` and `[project.gui-scripts]` entries now emit `defines_target` edges. Entry points like `my-cli = "mypackage.cli:main"` produce edges to `mypackage/cli.py`, enabling the build-target linker to connect script symbols to their target functions. The build-target linker now respects `target_function` metadata to link to the specific named function instead of always searching for `main()`.
- **Maven pom.xml `<mainClass>` extraction**: `<mainClass>` elements anywhere in the pom.xml (maven-jar-plugin, exec-maven-plugin, Spring Boot plugin) now produce `defines_target` edges. Class names like `com.example.Main` are converted to file paths (`com/example/Main.java`) for the build-target linker.
- **TypeScript abstract class support**: The JS/TS analyzer now handles `abstract class` declarations (`abstract_class_declaration` node type). Previously abstract classes like `LocalTrack`, `BaseHandler` were skipped entirely — no class symbol, no qualified method names, no inheritance edges. Also handles parenthesized extends expressions (`class Room extends (EventEmitter as new () => TypedEmitter<T>)`) by unwrapping the cast to extract the base class name.
- **Event subscriber→method edges for forward slice traversal**: The event sourcing linker now creates `event_subscribes` edges from `event_subscriber` nodes to their enclosing method. This enables forward slices to traverse through event-driven architectures: `emitting_method → event_publisher → event_publishes → event_subscriber → event_subscribes → handler_method`. Previously, forward slices dead-ended at subscriber nodes because `uses` edges went in the wrong direction (method → subscriber).
- **Reverse slice `contains` edge filtering**: Reverse BFS no longer follows `contains` edges. Previously, reverse-slicing from a method would traverse up to its parent class via `contains`, then find callers of the class itself — producing false positives (functions that instantiate the class but never call the target method). `extends`/`implements` edges are still followed in reverse (useful for "who inherits this?" queries).
- **Go structural interface matching**: The Go analyzer now detects implicit interface satisfaction without explicit `var _ Interface = &Struct{}` assertions. Compares interface method sets with struct method sets — if a struct has all methods an interface declares, an `implements` edge is created. This is how Go's type system works (structural typing). Previously, alertmanager had only 1 `implements` edge despite 24 interfaces; now all satisfying structs are connected.
- **Go interface method symbols for `dispatches_to` edges**: The Go analyzer now extracts method symbols from interface definitions (e.g., `Notifier.Notify` from `type Notifier interface { Notify(msg string) error }`). These symbols enable the type hierarchy linker to create `dispatches_to` edges from interface methods to concrete struct methods. Previously, Go interfaces had `implements` edges but no method-level dispatch — callers of `Notifier.Notify()` couldn't be connected to `EmailNotifier.Notify()`.
- **Linker pipeline accumulation**: Earlier linkers' output edges and symbols are now visible to later linkers in the pipeline. Previously, the inheritance linker (priority 15) created `implements` edges from `base_classes` metadata, but the type hierarchy linker (priority 60) couldn't see them because the context wasn't updated between runs. This blocked `dispatches_to` edge creation for Go, Rust, and any language relying on linker-produced inheritance edges. alertmanager: 0 → 64 dispatches_to edges; prometheus: 0 → 390.
- **Route concept deduplication**: Usage-based framework matching (Phase 3) now deduplicates concepts against definition-based matches (Phase 1). Previously, Go route handlers matched by both decorator patterns AND UsageContext patterns got duplicate `{concept: route, path: /status}` entries. alertmanager's `V1DeprecationRouter.deprecationHandler` had 14 concepts (7 unique routes × 2 duplicates each); now has 7.
- **Forward slice follows `dispatches_to` edges**: Forward slices now traverse `dispatches_to` edges from interface methods to concrete implementations. Previously, these edges were excluded as "structural," causing forward slices to dead-end at every interface call site. The hub_threshold parameter handles fan-out for interfaces with many implementations. alertmanager forward slice from main() now reaches concrete notifier implementations through the Notifier interface.
- **Symbol ranking excludes low-confidence edges**: The `symbols` command now passes `min_edge_confidence=0.5` to `rank_symbols()`, matching the display filter. Previously, name-collision false positives (confidence ~0.49) inflated in-degree for common Go method names like `.labels()`, `.rules()`, causing `memSeries.labels` to rank #1 in prometheus despite having only 1 real edge (94 false positives at 0.49 confidence).
- **`--entry auto` prefers main functions over route handlers**: The auto-entry selection now applies a 2x boost to `MAIN_FUNCTION` and `CLI_MAIN` entrypoint kinds. Previously, route handlers with more outgoing edges (e.g., V1DeprecationRouter.deprecationHandler with 7 route-node edges) were selected over `main()` (2 edges), producing empty tier-filtered slices because the handler was a dead end.
- **FP primitive utility dampening**: `map`, `filter`, `reduce`, `forEach`, `flatMap`, `fold`, `foldl`, `foldr`, `zip`, `concat`, `apply` are now recognized as utility symbols and dampened in rankings. Elm's `map` function (71 in-degree) was dominating alertmanager rankings over architecturally important symbols like `Integration.Notify`.
- **Sibling implementation ranking dampening**: When 6+ methods share the same name (e.g., 19 `Notifier.Notify` variants in alertmanager — one per notification channel), the top 3 by score keep full weight and the rest get 0.15x dampening. Previously, all 19 occupied top-20 positions even after common-method-name dampening (0.53x); now 2-3 representative implementations appear alongside other architecturally important symbols.
- **Go chained field access resolution**: The Go analyzer now resolves chained field method calls like `r.integration.Notify()` to `Integration.Notify` via the `class_field_types` registry. Struct definitions populate a field-to-type mapping (e.g., `RetryStage.integration → Integration`), which is aggregated across files and used during edge extraction to create `typed_field_call` edges (confidence 0.88). Handles pointer types (`*Metrics`), qualified types (`pkg.Cache`), multi-level chains, and cross-file resolution. Previously these calls produced unresolved or ambiguous edges.
- **Go constructor return type inference**: Variable type inference from `NewXxx()` constructor calls following Go naming convention. `s := NewServer()` infers `s` has type `Server`; `d := pkg.NewDispatcher()` infers `d` has type `Dispatcher`. Enables `typed_receiver_call` edges for methods called on constructor results (e.g., `go d.Run()` → `Dispatcher.Run`). Previously these calls were unresolved because `_type_from_rhs` only handled composite literals (`&Server{}`, `Server{}`).
- **Per-file in-degree capping in centrality**: Each unique source file can contribute at most 5 edges worth of in-degree to any target symbol. Prevents utility functions called many times from few files (e.g., `createYAMLNode` with 46 calls from `openapi_examples.go`) from outranking architecturally important symbols called from many different files.
- **Route deduplication scoped to file**: The `routes` command now deduplicates by (method, path, file) instead of (method, path). Routes from different files with the same method+path are preserved — they represent different registrations (e.g. v1 deprecation vs v2 go-swagger handlers). Previously, alertmanager v2 API routes were hidden because v1 deprecation routes consumed the dedup slot. Also fixes `GET /status` being hidden when a concept-enriched v1 handler consumed the slot before the v2 materialized route.
- **Helper/utility file dampening in centrality**: Symbols from files named `*_helpers.*`, `*_utils.*`, `*_util.*`, `helpers.*`, `utils.*`, or `util.*` are dampened in centrality rankings (×0.1). Fixes `createYAMLNode`, `responsesWithErrorExamples`, `integerSchema` from `openapi_helpers.go` dominating prometheus rankings despite being schema-generation boilerplate. Also adds `now()` as a utility time-accessor pattern.
- **Go-swagger handler wiring resolution**: The Go analyzer now detects go-swagger `XxxHandler = pkg.XxxHandlerFunc(api.realHandler)` assignment patterns and cross-references them with handler cache route registrations. Route symbols from `initHandlerCache()` previously had `handler_name` pointing to constructors (e.g., `alert.NewGetAlerts`) that don't exist as symbols; now they point to actual implementation methods (e.g., `API.getAlertsHandler`). Enables the route-handler linker to create `routes_to` edges connecting API routes to their handler implementations.
- **Annotation-aware test exclusion in compact views and rankings**: Compact/tiered views, symbol rankings, and entrypoint filtering now use `is_test_node(path, meta)` instead of path-only `is_test_path(path)`. This catches Rust `#[cfg(test)]` module contents, Java `@Test` methods, and C# `[TestMethod]`/`[Fact]` code that lives in production file paths. Fixes test infrastructure like `StubClient::new` (in-degree 52) dominating centrality rankings in codex-acp despite being inside `#[cfg(test)] mod tests`.
- **Tiered view boundary node exclusion**: Compact/tiered views (hg.4k.json, hg.16k.json) now exclude boundary nodes (external_symbol, tier=3) that exist in all_symbols for slice traversal but should not appear in output. Previously, when no high-confidence entrypoints existed, boundary nodes with high in-degree from imports dominated the selection, producing tiered views with only external_symbol nodes and no first-party code.
- **Infrastructure-path library exports dampened**: Library exports from telemetry, metrics, logging, tracing, observability, and internal directories are now excluded from connectivity boost when semantic entrypoints exist. Fixes gemini-cli where 77/111 entrypoints were telemetry exports — the additive connectivity boost (+0.25) was rescuing demoted exports (0.08) back above the 0.10 threshold. After fix: 34 entrypoints, 0 telemetry exports.
- **Diagnostic test-route filter**: Route linkage diagnostic now excludes routes defined in test files. Routes in test files (e.g., bun's 109 Express compatibility test routes) use inline anonymous handlers that can't be linked, inflating the denominator and producing false ROUTES_WEAKLY_LINKED flags.
- **Compact mode seed budget for large repos**: Adaptive entrypoint cap reduces forced seeds when entrypoints far exceed max_symbols (e.g., keycloak with 500 entrypoints in 100-node compact). Cap drops from max_symbols/2 to max_symbols/3 when entrypoints > max_symbols. Cross-cutting endpoint budget shares the same 1/3 cap. Eliminates singletons (keycloak had 19/100 disconnected nodes) by reserving more budget for frontier expansion bridge nodes.
- **Java annotation positional argument extraction**: The Java analyzer now extracts non-string positional arguments from annotations (constant references like `@Path(JaxrsResource.ACCOUNTS_PATH)`, concatenation like `@Path("/{id:" + PATTERN + "}")`). Previously, only string literal arguments were captured — constant references and expressions resulted in empty `args: []`, causing JAX-RS `@Path` route paths to be lost. killbill: 287 routes now have method+path metadata vs only 6 before.
- **JAX-RS method-level `@Path` combination**: Route concepts now combine class-level `@Path` with method-level `@Path` for annotation-based frameworks. `@Path("/users")` on class + `@Path("/{id}")` on method + `@GET` now produces route path `/users/{id}`. Previously, the method `@Path` was stored as a separate `resource_path` concept and not combined into the route path. Also normalizes paths with leading slashes when no parent class path exists.
- **Library export demotion skips example routes**: Semantic entrypoints from example/utility directories (e.g., `examples/rpc/api.ts`) no longer trigger library_export demotion. Library repos with example HTTP routes keep their core exports (e.g., Room class) at full confidence instead of being demoted to 0.075.
- **JS/TS built-in method blocklist**: Method name fallback resolution now skips 60+ built-in method names (`get`, `set`, `forEach`, `push`, `has`, `delete`, `map`, `filter`, etc.). Previously, `items.forEach(cb)` would create a false edge to a user-defined class like TTLMap that defines `forEach`, inflating its in-degree and corrupting centrality rankings.
- **Lean import resolution**: Intra-repo import edges now resolve to file node IDs (`lean:Lib/Utils.lean:1-1:file:file`) instead of dangling module IDs (`lean:Lib.Utils:0-0:module:module`). Fixes 440+ disconnected import edges in repos like ArkLib where all intra-repo imports were unreachable.
- **Solidity call resolution**: `super.method()`, `this.method()`, typed member access (`IERC20(addr).transfer()`), and function overload resolution by position.
- **Rust call resolution**: Same-module method preference via `path_hint` with `soft_hint` mode — single cross-crate candidates kept with reduced confidence instead of rejected. Derive-macro methods dampened in centrality ranking. Module-qualified calls (`mod::Type::method()`) now resolve by stripping module prefixes — previously blocked by ambiguity guard when multiple types shared a method name like `new`.
- **Utility directory false positives**: `dev/`, `utils/`, `tools/`, `bin/` now only match as utility at project root, not inside source roots (e.g., `src/dev/gates.rs` was incorrectly excluded from production slices).
- **Absolute symbol paths**: Normalized to relative in analysis and behavior map output. Fixes tier misclassification across Java, TypeScript, Kotlin, HTML, C, Objective-C, Swift, and three linkers.
- **Test/utility classification**: `fv/`, `harnesses/` as test directories; `build.rs` as utility; `tests.rs`/`testonly.rs` as co-located test files; `bench/`/`benches/` excluded from production slices; dependency symbols classified as tier 3.
- **Protobuf/gRPC codegen**: `.serde.rs`, `.pb.go`, `_pb2.py`, `_pb2_grpc.py` classified as derived (tier 4) and excluded from behavior maps. Reduces orphan rate inflation (penumbra: 5750 nodes removed, orphan rate 21% → ~9%).
- **JSON output reproducibility**: All JSON output now uses sorted keys for deterministic ordering across runs.
- **ASM register name filtering**: Indirect calls through CPU registers (`call rax`, `call r0`) no longer create false external call edges. Covers x86 (rax-r15, eax-ebp, ax-sp) and ARM (r0-r7, lr, pc, ip, fp) registers with case-insensitive matching.
- **Java framework detection from auxiliary Gradle files**: Multi-module Gradle projects (like Apache Kafka) that declare dependencies in `gradle/dependencies.gradle` or similar `gradle/*.gradle(.kts)` files now have their frameworks correctly detected. Previously only `build.gradle` and `build.gradle.kts` were scanned, causing JAX-RS and other frameworks to go undetected when their dependencies were declared in auxiliary Gradle configuration files.
- **Java nested class name collision guard**: `new Properties()` no longer resolves to `Log4jConfiguration.Properties` (a nested class) when the caller is in a different file. In Java, bare inner class names are only valid inside the outer class's file. This eliminates false edges from JDK/library class name collisions (e.g., `Properties`, `Logger`) with project-internal nested classes.
- **Elixir import-gated bare call resolution**: Bare function calls (e.g., `create("users")`) no longer resolve to cross-module functions unless the target module is explicitly imported with `import MyApp.Sites`. Previously, any project function with the same name would match regardless of imports, creating false edges (82 false `create` edges in plausible-analytics bakeoff). Local same-module calls and module-qualified calls (`MyApp.Sites.create()`) are unaffected.
- **TOML symbol IDs**: Changed from opaque sha256 hashes (`toml:sha256:...`) to location-based format (`toml:path:start-end:name:kind`), making compact output node IDs resolvable by `slice --entry`.
- **Java inherited method resolution via extends chain**: Bare `method()` and `this.method()` calls now resolve to parent class methods when the method isn't defined in the current class. Walks the extends chain up to 10 levels. killbill's `AccountResource.createAccount` → `JaxRsResourceBase.verifyNonNullOrEmpty` is now captured as a call edge (evidence: `ast_call_inherited`, confidence 0.90).
- **Phoenix LiveView route → module linking**: LIVE routes now resolve to their LiveView module when no per-action function exists. LiveView modules handle actions via `handle_params/3`, not separate functions, so `HomeLive.page` won't exist. The route-handler linker falls back to matching the module by name suffix. Fixes 51 orphaned LIVE routes in livebook bakeoff assessment.

### Changed

- Migrated all `Edge()` constructor calls to `Edge.create()` for consistent edge_key generation.

## [2.1.0] - 2026-03-01

### Added

#### Linkers

- **DI resolution linker**: Creates `di_resolves` edges from interface methods to DI-bound implementations. Supports Guice, Spring `@Bean`, ASP.NET Core DI, NestJS/Angular, InversifyJS, Python injector, Kotlin Koin, and Java SPI with heuristic fallbacks. Edges are followed by forward BFS — correct for DI-heavy codebases.
- **HTTP linker: Ruby, Java, AngularJS, jQuery clients**: Detects HTTP client calls in Ruby (RestClient, HTTParty, Faraday, Net::HTTP), Java (RestTemplate, Retrofit), AngularJS `$http`, and jQuery `$.ajax`/`$.get`/`$.post`. Creates cross-language `http_calls` edges to server route handlers.
- **JS/TS module resolution**: Resolves imports via relative paths (extension/index probing), `tsconfig`/`jsconfig`/`vite.config` path aliases, and monorepo tsconfig discovery.
- **Vue linkers**: Template-method linker connects event handlers to `<script>` symbols; component linker resolves import paths to `.vue` files.
- **FFI (5 languages)**: Cross-language call linking to C/C++ from Python (ctypes/cffi/PyO3), Ruby (FFI gem, C extensions), Go (Cgo), Node.js (N-API), and Lua (LuaJIT ffi).
- **ORM query, containment, Rails view template linkers**: Django/SQLAlchemy call-to-model linking; `contains` edges across 15 languages; convention-based controller-to-view linking (ERB, Haml, Slim, Jbuilder).

#### Frameworks

- **JAX-RS subresource locator path chaining**: Propagates `@Path` prefixes through locator chains with cycle detection.
- **Stapler (Jenkins)**: `@WebMethod`, `@RequirePOST`, `doXxx()`/`getXxx()` conventions. Auto-detected from `org.kohsuke.stapler`.
- **Google Guice + Jakarta CDI**: Guice DI annotations, `AbstractModule`, EventBus `@Subscribe`. Jakarta CDI scoping, `@Produces`, `@Interceptor`, `@Alternative`.
- **Rails**: Lifecycle/controller callbacks, Wisper pub/sub, scheduled tasks/Rack middleware entrypoints, namespace-aware route extraction.
- **Django & Flask**: Template tags/filters, signal receivers, Jinja2/Blinker/Flask-RESTful patterns.
- **Kafka Connect, XORM, FastAPI named routers, Express Controller.route()**: Streaming connector entrypoints, Go ORM detection, named `APIRouter` matching, config-object route registration.
- **Framework detection for 16 languages** (Haskell, Clojure, R, Lua, C++, Erlang, F#, Kotlin, C#, Dart, Julia, OCaml, Nim, Zig, D, Groovy). **Test framework patterns for 16 languages** (Elixir, Scala, Dart, Clojure, Haskell, Erlang, F#, Ruby, Julia, OCaml, Lua, R, Nim, Zig, D, Groovy). Main function detection for 7 more (D, Nim, Zig, V, Odin, Gleam, Haxe).
- **Test/utility file classification**: Test dirs as tier 2 with 90% penalty; `t/`, `test-*.c`, root-only `spec/` patterns. `dev/`, `contrib/`, `hack/`, `devel*` as utility. Removed `public/` from DEFAULT_EXCLUDES.

#### Analyzers

- **Clojure UsageContext**: Enables YAML-driven Ring/Compojure route detection.
- **JS/TS callback + middleware edges**: Function-as-argument `references` edges, Express `middleware_chain` edges, object literal and Ruby hash literal function references.
- **Assembly language**: Tree-sitter analyzer for `.s`/`.asm`/`.S` with cross-file call resolution.

#### Analysis core — Centrality & ranking

- **Bidirectional centrality**: `in_degree * (1 + ln(1 + out_degree))` rewards connectors over sinks. Hub in-degree saturation above 100.
- **Four dampening mechanisms**: Trivial sinks (≤1 out, ≤5 LOC), common method names (10+ symbols), utility symbols (Logger, `*Exception`, etc.), and pure sinks — all get 70–90% reduction in both `rank_symbols()` and `symbols` output.
- **Edge confidence filtering**: Edges <0.5 confidence excluded from centrality and degree computation. Import edges excluded by default. Documentation kinds and migration paths excluded/de-weighted.

#### Analysis core — Slices

- **Hub pruning depth-1 exemption**: Fixes "main → run()" patterns where orchestrators were hub-pruned.
- **`--exclude-imports` flag**: Call-graph-only slices (up to 64% noise reduction). **`--hub-threshold N`** (default 50). **Node depth tracking** in `SliceResult.node_depths`. Forward slices skip structural edges; reverse slices downweight test callers; class/interface entries auto-expand.

#### Analysis core — Entrypoints

- **Scaled cap** (base 50, max 500) with confidence threshold (0.10) and count cap (50).
- **library_export demotion**: 90% penalty when semantic entrypoints exist. Language dominance ranking for polyglot repos.
- **New detectors**: C `cmd_*` functions, Java/Kotlin/Rust library exports, C forward declaration dedup.
- **Tier classification**: Fuzz/benchmark dirs as tier 2; generated route symbols promoted to tier 2.

#### Analysis core — Call resolution

- **Go**: Module path resolution via `go.mod`, chained-call ambiguity guard, stdlib method guard (50+ methods), route handler unwrapping, route path validation, var alias extraction, struct embedding + interface assertion detection, Chi `Del()` and Go-swagger route detection.
- **Rust**: Suffix index splits on `::`, scoped calls prefer full qualified names, span-based enclosing function disambiguation.
- **C/C++**: Function pointer callback edges, dispatch table `dispatches_to`/`uses_dispatch_table` edges, declaration/definition deduplication with edge remapping.
- **Cross-language**: Unified suffix index for all separators (`.`, `::`, `#`, `\`, `:`) across 10+ languages. Ambiguous method scaling (`1/sqrt(N)`); ListNameResolver returns unresolved at threshold.

#### Analysis core — Other

- **Docstring extraction** (103/105 analyzers): First-line doc summaries in `Symbol.docstring`.
- **Typed stable_id (ADR-0014 Phase 3)**: Per-language signature normalization and typed hashing for 12 analyzers.
- **Decorator/annotation edges** (Python, TS, Java, C#, Rust), **return type tracking** (6 languages), **Go route mount detection**, **inheritance linker struct support**. Edge deduplication fixed for `None`-keyed edges.

#### Sketch, Supply chain, CLI

- **Sketch**: Exclude 9 lock files from config section. **Supply chain**: Maven multi-module workspace detection.
- **CLI**: Secret scanning via gitleaks, extras/cache management subcommands, redesigned bakeoff tooling (numeric scores, trajectory, orphan recovery, idea ingestion, artifact compression, domain-scored seed selection).

#### Documentation & Testing

- Scoped smart-test coverage, per-package checks, CI auto-retry, `ci-debug logs`.

### Changed

#### Language analyzers

- **Elixir/OTP**: GenServer dispatch, 11 behaviour callbacks, `live` routes, multi-clause edges, cross-file resolution.
- **C/C++**: Enclosing-function fix for duplicate names, definition-only struct/enum extraction. C++ adds template calls, pointer/reference returns, stack construction.
- **Go**: Function-scoped type tracking, unified ambiguity guard (all selector types), unexported method guard, builtin filter, receiver disambiguation, self-call resolution, route linking (Gin/Echo/Fiber/Chi/Gorilla), Group prefix composition, HTTP client detection, `lines_of_code`.
- **Ruby**: Class methods, `.new`→`#initialize`, namespaced receivers, job enqueue/callback/delegate/association edges, ambiguity guard, ListNameResolver.
- **Rust**: ListNameResolver with ambiguity threshold; 3+ candidates → no edge. `lines_of_code` populated.
- **JS/TS, PHP, Java, Lua, D**: Method ambiguity guards, inherited method fallback, require-alias resolution, import disambiguation improvements.

#### Algorithms & output

- **Slices**: Skip structural edges forward, downweight test callers reverse, `--exclude-tests` preserves inheritance. **Entrypoints**: Transitive scoring, connectivity fallback, test demotion, `--entry auto` filter support. **Default exclusions**: Doc/config nodes, CSS variables, npm/TS types, SCSS.
- **Output**: Tiered view overhaul (budget enforcement, connectivity-aware selection). Route improvements (`-x`, `kind=route`, Django/Rails format fixes). Symbols sorted by per-symbol degree. Derived/minified excluded by default; `--max-files` raised to 50.
- **Deps**: Embeddings optional. All deps pinned `~=X.Y.Z`.

#### CI, agent governance, internal

- smart-test improvements, infra-only PR skip, shared Forgejo API lib, parallel coverage, retry-aware `merge-pr`.
- Three-way stop hook, post-compaction recovery, pre-push hook, fail-closed tracker, fork workflow hardening.
- Standardized pass IDs via `make_pass_id()`. Generalized symbol identity (ADR-0014): location `id`, signature `stable_id`, CST `shape_id`. Tracker `update --note` and unread message warning.

### Fixed

#### JS/TS

- **Cross-package false positives**: Comprehensive guard on all edge paths (direct/namespace/method/callback/object-field/shorthand) using import disambiguation, same-package preference, and npm boundary checks. Built-in name guard (`Number`, `String`, `parseInt`, etc.). Parameter shadowing respects lexical scoping in Promises/closures. `npm_package` symbols correctly tier 3.

#### Go

- Vendored SDKs classified as tier 3. Method ambiguity threshold lowered to 2. Route handler from last non-string arg. Test functions require `_test.go` suffix. Same-package method resolution fixed.

#### Java/Kotlin/Scala

- `main()` patterns match qualified names. Import-aware class name disambiguation. Field access receiver extraction. Integration test path detection.

#### Other languages

- **Rust**: Built-in attribute guard (45 names); impl method name extraction. **Clojure**: `test-*` requires `test/` dir. **D**: `.d` file disambiguation vs GCC deps. **Rails**: Route-to-controller reverse suffix matching. **Kotlin/C#/Scala/Python**: Chained member access resolution.

#### Framework detection

- False positive guards for GraphQL (requires server packages), Dropwizard (requires `-core`/`-jersey`), handler naming (requires HTTP-context dir). Route path prefix inheritance (Spring Boot, JAX-RS, Micronaut, ASP.NET). Pattern `base_class` no longer falls through to kind-only matching. Word-boundary regex. Micronaut field fix.

#### Graph & output quality

- Tiered view budget compliance (was 177× over) with connectivity-preserving shrink. Dangling edges after tier filtering. WebSocket N×M explosion. Event symbol ID format. Supply chain tier deserialization. Route-handler linking (Rails suffix, Django view_name, Phoenix concept, Ruby hash rockets). Vue/C/C++ analyzer deduplication. Name-collision fan-out → single best match. Cross-language containment filtering. Language-proportional sketch seeding. Route symbol entrypoint promotion. Spurious TS warning. Minified file skip. smart-test scoped mode. ListNameResolver full-path disambiguation.

### Removed

- **Bootstrap mode in CI**: Stable hypergumbo includes `slice --files`, so smart-test always generates proper manifests.


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
- **Bakeoff diagnostic false positive reduction**: `NO_CALL_EDGES` now requires ≥3 function/method symbols (repos with 0-2 functions can't have meaningful call edges). `EXPECTED_ROUTES_BUT_FOUND_0` removed overly broad "web" keyword match (caught webtunnel, webpack, webrtc); now requires name keywords like "api", "server", "http", "rest" OR evidence of route edges/framework detection.

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
