# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.0.2
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

#### Linkers

- **JS/TS module resolution**: Resolves import edges to file symbols via relative imports (extension/index probing), `tsconfig.json`/`jsconfig.json`/`vite.config` path aliases, and monorepo tsconfig discovery.
- **Vue**: Template-method linker connects event handlers (`@click`, `v-on:input`) to `<script>` symbols; component linker resolves import paths to `.vue` files on disk.
- **FFI (5 languages)**: Python (ctypes/cffi/PyO3), Ruby (FFI gem, C extensions), Go (Cgo), Node.js (N-API/node-addon-api), and Lua (LuaJIT ffi.C/ffi.load) cross-language call linking to C/C++ symbols.
- **ORM query**: Detects Django ORM and Flask-SQLAlchemy query patterns, linking calling functions to Model classes.
- **Containment**: Creates `contains` edges from classes/interfaces/modules/services to their methods/RPCs/nested members across 15 languages. Handles nested classes and struct/trait/enum containers.
- **Rails view template**: Convention-based linking from controller actions to view templates (`UsersController#show` → `app/views/users/show.html.erb`). Supports ERB, Haml, Slim, Jbuilder. Handles namespaced controllers.

#### Analyzers

- **Assembly language**: Tree-sitter-based analyzer for `.s`/`.asm`/`.S` files. Extracts labels as function/variable symbols, detects call instructions with cross-file resolution.

#### Analysis core

- **Slice node depth tracking**: `SliceResult.node_depths` records BFS hop distance for each node (0 for entries, 1 for direct callees, etc.), enabling LLMs to distinguish 1-hop vs 8-hop dependencies in slice output.
- **C forward declaration entrypoint dedup**: Functions appearing as both declaration (`.h`) and definition (`.c`) no longer produce duplicate entrypoints. Declarations get `modifiers=["declaration"]`; entrypoint detection prefers definitions when both exist.
- **Docstring extraction (103/105 analyzers)**: Extracts first-line doc comment summaries into `Symbol.docstring` using position-based tree-sitter node lookup. Covers all analyzers except HTML (no code symbols) and JSON (no comment syntax). Mainstream overriders (Go, Java, JS/TS, Kotlin, PHP) harmonized to a single `populate_docstrings_from_tree()` call per file.
- **Inheritance linker struct support**: Go structs with `base_classes` metadata now produce `implements`/`extends` edges.
- **Decorator/annotation edge detection**: Decorator applications create edges in Python, TypeScript, Java, C#, and Rust.
- **Return type tracking**: Variable type inference handles function return type annotations in Python, TypeScript, Java, Kotlin, C#, and Dart (simple, non-generic types).
- **Go route mount detection**: `r.Mount("/api/v1", handler)` calls create `route_mount` symbols with `mount_prefix` and `handler_ref` metadata, plus a `calls` edge from the enclosing function to the handler. Enables downstream composition of full URL paths (e.g., `Mount("/api/v1", apiRoutes())` + `GET /users` → `GET /api/v1/users`). Common in Chi-based Go web apps like Forgejo/Gitea.
- **C dispatch table edge detection**: Static array initializers with function pointer references (e.g., `static struct cmd commands[] = { {"add", cmd_add}, ... }`) produce `dispatches_to` edges. Functions that reference dispatch table variables (e.g., `get_builtin` accessing `commands[]`) produce `uses_dispatch_table` edges, completing the chain from caller through lookup function to dispatched targets. Common in C projects like git, nginx, Linux kernel.
- **Java/Kotlin library export detection**: Public interfaces, classes, and enums detected as library_export entrypoints. Java classes/interfaces/enums now extract access modifiers (public, abstract, etc.). Fixes library entrypoint detection for Java projects like Apache Iceberg where 99.9% of entrypoints were test functions due to no production-code library export patterns.
- **Go interface-implementation assertion detection**: Compile-time assertions (`var _ Interface = &Struct{}`) produce inheritance edges.
- **Go struct embedding detection**: Embedded struct fields (anonymous fields like `type Service struct { *Logger; Base; sync.Mutex }`) now populate `base_classes` metadata, enabling the inheritance linker to create `extends`/`implements` edges. Supports simple, pointer, qualified (`pkg.Type`), and generic (`Cache[K, V]`) embeddings. Combined with assertion detection for comprehensive Go interface satisfaction coverage.
- **Trivial sink dampening**: Symbols with out_degree ≤ 1 AND lines_of_code ≤ 5 get 90% centrality reduction. Prevents trivial accessors/stubs (Timer.Duration, noopMetric.Inc, CheckError, syncTask.name) from dominating rankings through raw in-degree alone. Addresses recurring centrality noise findings from bakeoff cohorts 16-17.
- **Typed stable_id tier (ADR-0014 Phase 3)**: Per-language `normalize_signature()` functions strip parameter names, FQN prefixes, and normalize generics. `make_typed_stable_id()` hashes `sha256(kind:normalized_sig:visibility:decorators:containing_stable_id)` for 12 analyzers: Java, C#, Kotlin, Scala, Swift, Rust, Go, PHP, Groovy, JS/TS, Python, Dart. Four normalization families: types-first (Java/C#/Dart/Groovy), names-first (Kotlin/Scala/Swift/Rust/TS/Python), PHP-specific, Go-specific.

#### Language & framework support

- **Framework detection for 16 languages**: Haskell, Clojure, R, Lua, C++, Erlang, F#, Kotlin, C#, Dart, Julia, OCaml, Nim, Zig, D, Groovy.
- **Test framework patterns for 16 languages**: Elixir, Scala, Dart, Clojure, Haskell, Erlang, F#, Ruby, Julia, OCaml, Lua, R, Nim, Zig, D, Groovy.
- **Main function entrypoint detection for 7 more languages**: D, Nim, Zig, V, Odin, Gleam, Haxe.
- **Rails entrypoint patterns**: Scheduled tasks, custom job base classes (`BaseJob`), and Rack middleware now detected as entrypoints.
- **Rails namespace-aware route extraction**: `namespace :admin do resources :users end` correctly generates `admin/users#index` controller_action and `/admin/users` URL paths. Supports nested namespaces and `scope module:`.
- **Django & Flask framework patterns**: Django template tags/filters and signal receivers; Flask Jinja2 customizations, Blinker signals, and Flask-RESTful support.
- **Test classification**: Test directories/files classified as tier 2; test functions registered as entrypoints with 90% penalty to avoid dominating `--entry auto`.
- **Kafka Connect framework patterns**: SinkTask, SourceTask, SinkConnector, and SourceConnector base class patterns detected as controller entrypoints. Framework detection from `connect-api` in Maven/Gradle builds. Enables entrypoint detection for streaming data connectors (e.g., Apache Iceberg's IcebergSinkTask, Debezium connectors).
- **XORM framework detection**: XORM ORM (used by Forgejo/Gitea) detected via `xorm.io/xorm` in `go.mod`. Engine operation patterns (`Get`, `Find`, `Insert`, `Update`, `Delete`, etc.) matched as repository concepts.

#### CLI & developer tooling

- **Secret scanning with gitleaks**: `hypergumbo sketch` scans output for potential secrets. Install with `hypergumbo install-gitleaks`. Opt out with `--no-secret-scan`.
- **CLI extras management**: Subcommands for optional dependencies: `add-extras`, `remove-extras`, `install-embeddings`, `uninstall-embeddings`, `uninstall-gitleaks`.
- **Cache management**: `hypergumbo cache-status` and `hypergumbo cache-clear` for managing `~/.cache/hypergumbo/`.
- **`bakeoff-reflect` redesign**: Structured two-phase LLM assessment pipeline for BROAD bakeoff.
- **DEEP bakeoff reverse-slice seed selection**: Reverse slices now use domain-scored symbols (in-degree weighted by out-degree ratio) instead of raw in-degree. Pure sinks like `die()` (in=1296, out=0), `strbuf_release()` (in=1202, out=1), and `GetEngine()` (in=1087, out=3) are demoted in favor of domain functions like `parse_options` (in=248, out=9) and `start_command` (in=82, out=27) that produce more useful reverse slices. Improved `_TEST_PATH_RE` to cover JVM conventions (Test*.java, *IT.java, src/integration/, src/test/) and Go unittest dirs.
- **C `cmd_*` CLI command entrypoint detection**: Functions matching `cmd_<name>` in C files are detected as CLI_COMMAND entrypoints via naming convention (confidence 0.80). Covers git, systemd, busybox, and similar C projects that use function pointer dispatch tables to map string names to `cmd_*` handlers.
- **Application library_export demotion**: When a repo has real semantic entrypoints (HTTP routes, CLI commands, main functions, controllers), library_export entries receive a 90% confidence penalty. Fixes the "forgejo problem" where 7,474 Go uppercase-symbol exports drowned out 772 meaningful HTTP routes. Pure libraries (no semantic entrypoints) keep full library_export confidence.
- **DEEP bakeoff reverse-slice parameters**: Reverse slices now use `--hub-threshold 200 --max-files 500` (vs 50/200 for forward slices). Fixes the hub pruning paradox where intermediate hub nodes trapped reverse traversal within a single layer (e.g., models/) preventing cross-layer discovery into routers/ and services/.
- **`--exclude-imports` slice flag**: Excludes import/module edges from both traversal and output, producing call-graph-only slices. Reduces edge noise by up to 64% in large codebases where file-level package imports dominate over function-level call edges. Used by default in DEEP bakeoff forward slices.
- **Test file detection for `t/` and `test-*.c`**: The `t/` directory (C/Perl convention: git, Perl core) and `test-*.c` filename pattern (hyphen-separated) are now recognized as test files. Git's 200+ test helpers in `t/helper/` are now properly classified as test code.
- **Import edge exclusion from centrality**: `rank_symbols()` now excludes `imports` and `imports_module` edges from centrality computation by default. Import edges represent file-level visibility, not call relationships — being imported widely doesn't make a symbol architecturally significant. Surfaces domain-relevant symbols (Router, QuerySet) over widely-imported utilities (string helpers, type aliases).
- **Utility symbol dampening in ranking**: Symbols with infrastructure-utility names (Logger, Clock, Metrics, empty, size, toString, `__repr__`, ErrNotFound) get 90% centrality reduction via `apply_utility_symbol_weights()`. Supplements hub saturation (which only caps in-degree) with name-based detection. Addresses INV-mahap: DefaultClock.Now (in-degree 474) no longer dominates rankings.
- **Entrypoint confidence threshold and count cap**: `detect_entrypoints()` now filters out entries below MIN_ENTRYPOINT_CONFIDENCE (0.10) and caps results at MAX_ENTRYPOINT_COUNT (50). Eliminates the "forgejo flood" where 3100+ library_export entries at 0.075 confidence appeared in `--list-entries` output. Test-file entries (0.08 after 90% penalty) are also filtered.
- **JS/TS object literal function references**: Object properties with bare identifier values (`{onClick: handleClick}`) now produce `references` edges with evidence type `object_field_reference`. Handles both regular pair syntax and shorthand properties (`{handleClick}`). Covers React callback props, Express config, event handler patterns.
- **Ruby hash literal function references**: Hash fields with identifier values (`{on_success: my_callback}`) now produce `references` edges with evidence type `hash_field_reference`. Covers Ruby callback patterns in Rack, Sinatra, and custom event systems.
- **Edge confidence filtering in centrality**: `rank_symbols()` now accepts `min_edge_confidence` parameter (default 0.0, CLI uses 0.5) to exclude low-confidence inferred edges from centrality computation. Fixes method name collision artifacts: DirLocker.Lock (255 false in-degree from unrelated `.Lock()` calls), MemoryCache.get (228 false from `.get()` calls), Cookie.setValue (201 false from React `useState` callbacks). Surfaces domain-relevant symbols by filtering `ast_method_inferred` edges with confidence <0.5.
- **Edge confidence filtering in symbols command**: `hypergumbo symbols` now excludes edges with confidence <0.5 from degree computation, matching the centrality ranking filter. Prevents method name collision artifacts (e.g., DirLocker.Lock showing 255 in-degree) from appearing in `symbols.txt` output.
- **Go stdlib interface method guard**: Method calls like `x.Lock()`, `x.Close()`, `x.Read()` with unknown receiver type now produce `stdlib_method_call` unresolved edges (confidence 0.45) instead of resolving to the only repo-defined method with that name. Fixes the DirLocker.Lock scenario: 255 false in-degree edges from `sync.Mutex.Lock()` calls were all resolving to the single repo-defined `DirLocker.Lock`. Guards 40+ methods from `sync`, `io`, `fmt`, `sort`, `context`, `encoding`, `net/http`, and `database/sql` interfaces.
- **Go route handler wrapper unwrapping**: Route registrations like `r.Post("/read", api.ready(api.remoteRead))` now correctly attribute the route to `api.remoteRead` (the inner handler) instead of `api.ready` (the wrapper). Detects selector_expression arguments inside call_expression wrappers while preserving constructor patterns like `httpapi.NewHandler(env)` where plain identifiers are config args, not handlers.
- **Sink dampening in symbols ranking**: `hypergumbo symbols` now dampens the effective centrality of pure sink symbols — those with high in-degree but zero/near-zero out-degree (e.g., `noopMetric.Inc` in=109 out=0, `Timer.Duration` in=98 out=1). The dampening factor ranges from 0.3 (pure sink) to 1.0 (out/in >= 0.33), preventing trivial stubs from dominating the top-10 over genuine architectural hubs like `evaluator.eval` (in=10 out=78).
- **Go Chi `r.Del()` route detection**: Chi router's `Del()` shorthand for `Delete()` is now recognized as a DELETE route. Previously, `r.Del("/series", dropSeries)` was invisible in `routes.txt`. Adds `_GO_METHOD_ALIASES` normalization map for framework-specific HTTP method shorthands.
- **Go module path resolution**: Cross-package calls in Go repos now resolve correctly by reading `go.mod` to strip the module path prefix from import paths before suffix matching. Previously, an import like `github.com/example/trivy/pkg/log` would fail suffix matching because the domain prefix (`github.com/example/`) was never in the file path. Now the resolver operates on repo-relative paths (`pkg/log`), dramatically reducing unresolved edges in large Go repos (trivy: 63% → expected significant reduction). Also improves the `ListNameResolver` to track narrowed candidate sets during suffix matching, so that path hints that narrow from e.g. 10 → 2 candidates avoid the ambiguity threshold.
- **Go route path validation**: Route detection now requires the first string argument to start with `/`, preventing K8s client API calls like `client.Get(ctx, "my-app", opts)` from being misidentified as HTTP routes. This eliminates ~74 false positive routes in argo-cd (out of 108 total detected routes).
- **Bakeoff test path detection fix**: Fixed `_TEST_PATH_RE` in `bakeoff-features` to match Go `_test.go` files without requiring a `/` prefix. Previously, `scrape/helpers_test.go` was not detected as a test file, causing test helpers like `newTestScrapeLoop` to be selected as reverse slice seeds.

#### Tracker

- **Tier 2 embedding-based near-duplicate detection**: `validate --deep-similar` uses dense embeddings (ONNX ModernBERT) to detect semantically similar items. Requires `pip install hypergumbo-tracker[dedup]`.
- **SQLite read cache**: Read operations consult a per-tier cache, avoiding YAML reparse on every invocation.
- **Positional alias persistence**: `:N` aliases from `list`/`ready` output persist across CLI invocations.
- **Auto-sync reminder**: Every successful tracker CLI command prints a one-line stderr reminder showing pending line count and auto-sync threshold, preventing agents from manually pushing tracker changes.
- **Auto-sync threshold increase**: Default threshold raised from 20 to 40 lines to reduce CI queue pressure from frequent small syncs.

#### Tracker TUI

- **Textual 7.x upgrade**: Bumped from `~=3.0` to `~=7.5`.
- **Three responsive layout tiers**: Compact (40x16+), standard (60x20+, two-pane), wide (120x38+). Dynamic resize preserves selection.
- **Write keybindings**: 8 modal dialogs for discuss, move, new, edit, set parent, edit dependencies, lock/unlock.
- **Snapshot tests**: SVG-based visual regression tests for all layout tiers.

#### Documentation

- **Tracker quick-start guide**: Covers install, deployment, core concepts, agent/human workflows, stop hook integration, and fork workflow.

#### Testing & CI infrastructure

- **Scoped coverage for smart-test (ADR-0011)**: Enforces 100% coverage only for changed source files. Enables fast feedback (~45 tests in <1s vs 5700+).
- **Per-package coverage check**: `scripts/check-package-coverage` verifies each package achieves 100% coverage in isolation.
- **CI auto-retry for heavy test jobs**: Jobs exceeding Codeberg's 5-minute runner cap automatically retry on a self-hosted runner. Covers both full-suite (per-package) and PR CI (pytest) jobs.
- **`ci-debug logs` subcommand**: Fetches plain-text CI job logs without the web UI.

### Changed

#### Language analyzer improvements

- **Elixir/OTP**: GenServer dispatch linking (`call`/`cast` → `handle_call`/`handle_cast`), behaviour callback detection (11 behaviours including Phoenix LiveView), `live` route detection, multi-clause function edge targeting, and cross-file module-qualified call resolution.
- **C**: Fix enclosing-function lookup for files with duplicate function names. When multiple files define the same function (e.g. `cmd_main` in git's per-binary entry points), all definitions now produce outgoing call edges, not just the one that wins the global symbol registry. Struct/enum symbols now extracted only from definitions (with bodies), not from type references (`struct stat sb;`) or forward declarations. Reduced orphan struct/enum symbols from ~28K to ~7K on git.
- **Go**: Function-scoped variable type tracking (fixes false-positive method edges when same variable name reused across functions), ambiguous method call guard (3+ candidates → unresolved edge instead of arbitrary pick), unexported method visibility guard (lowercase method calls without known receiver type produce unresolved edges instead of false-positive cross-package resolution), builtin type/function filter (skips `string(x)`, `int(x)`, `len(x)`, `make(...)` etc. from call resolution — eliminates false positives like `recalcRequest.string` with 577 spurious callers), qualified enclosing-function attribution for same-name methods, function-reference-as-argument call edges, receiver-type method disambiguation, method receiver self-call resolution (e.g. `s.Close()` inside `func (s *Server) Cleanup()` resolves to `Server.Close`), route-handler linking (Gin, Echo, Fiber, Chi, Gorilla mux), route Group prefix composition (closure-based `Group("/api", func() { GET("/users", h) })` → `/api/users`, handles nested groups), and HTTP client detection for cross-language linking.
- **PHP**: Ambiguity guard for method calls — 3+ classes defining the same method name produces no false-positive resolved edge (AMB-METHOD invariant).
- **JavaScript/TypeScript**: Ambiguity guard for method calls — 3+ classes defining the same method name produces no false-positive resolved edge (AMB-METHOD invariant).
- **Java**: Inherited method fallback (edges to class symbol when method unresolvable) and array initializer annotation path unwrapping.
- **Ruby**: Class method (`def self.method`) extraction, `.new` → `#initialize` resolution, namespaced receiver resolution, chained constructor calls, job enqueue edge detection (`perform_later`/`perform_async`), Rails callback edges (controller actions, model lifecycle, block-style), delegate macro edges, ActiveRecord association edges, receiver-type tracking for variable-receiver calls, method parameter filtering, and ambiguity guard for bare method calls (3+ classes → no false-positive edge, same-class calls still resolve).
- **Lua**: Colon-syntax method calls confidence reduced to 0.40x (from 0.85x) to reflect ambiguity. Single-assignment type tracking for typed receivers (0.80x). `require`-alias call resolution linking `X.method()` to symbols in the required module. Dot-index function declaration detection (`function _M.process()`).
- **C++**: Template function call detection, pointer/reference return type extraction, stack object construction detection, and struct/enum definition-only extraction (same fix as C).
- **D**: Import-scope disambiguation, import edge resolution to actual module symbols, and method extraction with qualified names inside struct/class/interface bodies.

#### Analysis algorithms

- **Edge deduplication**: At most one edge per (src, dst, edge_type), eliminating false centrality inflation from repeated calls.
- **Bidirectional centrality ranking**: Uses `in_degree * (1 + ln(1 + out_degree))` instead of pure in-degree, rewarding connectors over pure sinks.
- **Hub in-degree saturation**: Symbols above 100 in-degree get saturated to `threshold + ln(1 + in_degree − threshold)`. Prevents infrastructure utilities from dominating centrality rankings.
- **Ambiguous method confidence scaling**: Scales as `1/sqrt(N)` instead of flat 0.70 for N matching symbols. Two candidates: ~0.71; fifty candidates: ~0.14.
- **ListNameResolver ambiguity threshold**: New `ambiguity_threshold` parameter. When candidate count meets or exceeds threshold and no `path_hint` disambiguates, returns unresolved result instead of picking an arbitrary candidate. Protects Go, Ruby, PHP, and JS/TS method calls against false-positive edges from common method names (Get, Set, Close, String).
- **Go method ambiguity guard unified**: The guard now covers ALL selector expressions regardless of operand type — chained calls (`getWriter().Close()`), field access (`resp.Body.Close()`), and index expressions (`items[0].Close()`). Previously only fired for simple identifier operands (`x.Close()`). Go's `ListNameResolver` now uses `ambiguity_threshold=3` as defense-in-depth. Fixes inflated centrality for common interface methods (Close, String, Name, Error) in large Go codebases.
- **Slice improvements**: Forward slices skip structural edges (`extends`/`implements`/`contains`/`dispatches_to`). New `--hub-threshold N` flag (default 50). Class/interface entry points auto-expand to include member methods. Reverse slices now downweight test file callers (0.1× multiplier) so production callers rank higher, matching the 90% entrypoint demotion.
- **Entrypoint improvements**: Transitive out-degree scoring, connectivity-based fallback, library export detection (Go, Elixir, Python), aggressive test demotion, `--entry auto` respects `--exclude-tests`/`--max-tier`. Language dominance ranking: in a 95% C codebase, C main() now ranks above Python scripts regardless of Python's higher connectivity. Connectivity boost scaled by `0.5 + 0.5 * (lang_count/max_lang_count)`.
- **Test edge filtering**: `--exclude-tests` now preserves `extends`/`implements` edges from test files.
- **Default exclusions**: Documentation/config nodes, CSS variables/at-rules, npm_package/module_file symbols, TypeScript type/interface declarations, and SCSS variables excluded from output by default.

#### Output quality

- **Tiered view overhaul**: Budget enforcement on full output, connectivity-aware node selection from entrypoints, and confidence-filtered force-includes.
- **Self-loop edge filtering**: Removed from output, adjacency lists, and connectivity scoring.
- **Route improvements**: `hypergumbo routes` supports `-x`/`--exclude-tests` and shows `kind=route` symbols. Django routes no longer hardcode `GET`. Rails routes display `controller#action` instead of raw symbol names.
- **Migration noise de-weighting**: Symbols in migration paths get 0.1x centrality multiplier.
- **Symbol ranking**: `hypergumbo symbols` sorts by per-symbol degree instead of file-total-degree.
- **Exclude derived/minified files by default**: Tier 4 symbols excluded. Use `--max-tier 4` to include.
- **Increase default `--max-files` for slice**: 20 → 50.
- **TypeScript decorator kind filtering**: Rejects class/interface/type symbols as decorator targets.
- **C/C++ test file tier classification**: `unit_tests/` and `test_*.{cpp,cc,cxx,c,h,hpp}` patterns classified as tier 2.

#### Dependencies & configuration

- **Embeddings now optional**: sentence-transformers (~2GB with PyTorch) no longer installed by default. Enable with `pip install hypergumbo[embeddings]`.
- **Pinned dependencies**: All dependencies use `~=X.Y.Z` (compatible release) instead of `>=X.Y`.

#### Testing & CI infrastructure

- **smart-test improvements**: Detects committed, staged, and unstaged changes. Uses `--cov-branch` locally. Manifest includes `Mode:` field.
- **Infrastructure-only PRs skip pytest**: PRs changing only scripts, YAML, or config skip pytest in CI.
- **Shared Forgejo API library**: Extracted duplicated logic from `auto-pr`, `ci-debug`, and `contribute` into `scripts/lib/forgejo-api.sh`.
- **`merge-pr` improvements**: Retry-aware CI check — when aggregate status is "failure" but `ci-complete` (gate job) succeeded, recognizes that retries handled the failure.
- **Parallel `check-package-coverage`**: Packages run in parallel by default.

#### Agent governance

- **Three-way stop hook logic**: TODO blocking, cooldown, or full reflection.
- **Post-compaction state recovery**: `.agent/last_stop_check.json` captures context for recovering after compression.
- **Pre-push hook**: Blocks direct pushes to `dev`/`main`. Warns on workspace tracker files pushed to upstream.
- **Remove legacy grep fallback from stop hook**: Uses only structured tracker CLI. Fail-closed on tracker errors.
- **Fork workflow hardening**: End-to-end fork workflow test and documentation.
- **Bakeoff improvements**: Stable timestamped artifact paths, deeper slices (`--max-hops 5`).

#### Internal

- **Standardized pass IDs and versions**: All analyzers and linkers derive `PASS_ID` via `make_pass_id()` (canonical format: `{name}-v1`) and `PASS_VERSION` from the package version. Eliminates 4 competing ID formats and 4 stale version strings across ~105 files. `TreeSitterAnalyzer` subclasses auto-derive both from `lang`. Affects the `origin` field in output symbols (e.g., `bitbake.tree_sitter` → `bitbake-v1`).
- **Generalized symbol identity (ADR-0014 Phases 0–2)**: Symbol `id` is now location-based (`{lang}:{path}:{start}-{end}:{name}:{kind}`) and `stable_id` is signature-based (`sha256:...` hash of kind, arity, decorators, containing scope). `shape_id` provides structural fingerprinting via CST skeleton hashing. Converted all 27 language analyzers from location-based `_make_stable_id()` strings to the new `compute_stable_id()` / `make_symbol_id()` system. Zero legacy ID functions remain.

### Fixed

- **Go method ambiguity threshold lowered to 2**: When 2+ types define the same method name (e.g., `Close()`, `Error()`, `String()`) and the receiver type is unknown, produces an unresolved edge instead of arbitrarily picking one implementation. Previously threshold was 3, allowing 2-candidate collisions to generate false-positive edges that inflated centrality rankings.
- **Go route handler vs middleware**: Route handler name now extracted from the last non-string argument instead of the first. Go frameworks pass middleware before the handler (`r.GET("/path", mw1, mw2, handler)`); previously the first middleware was incorrectly reported as the handler.
- **Dropwizard framework false positive**: `io.dropwizard.metrics:metrics-core` (standalone metrics library) no longer triggers Dropwizard REST framework detection. Detection now requires `dropwizard-core` or `dropwizard-jersey` artifact names.
- **Chained member access call resolution**: `this.field.method()` / `self.field.method()` call graph extraction in Kotlin, C#, Scala, and Python.
- **Route path prefix inheritance**: Class-level route annotations now correctly combine with method-level annotations in Spring Boot, JAX-RS, Micronaut, and ASP.NET.
- **Cross-language containment filtering**: Containment linker now filters by language when matching containers to members. Prefers same-file, then same-language; refuses to cross language boundaries.
- **Event symbol ID format**: Now uses standard `{language}:{path}:{line}-{line}:{name}:{kind}` format instead of malformed `{path}::{kind}::{line}`.
- **Language-proportional sketch seeding**: When a dominant language's entrypoint has no outgoing edges, injects highest-degree nodes from underrepresented languages as seeds.
- **Route symbol entrypoint promotion**: Symbols with `kind="route"` now promoted to HTTP_ROUTE entrypoints. Previously only concept-enriched routes were detected.
- **Rails acronym-aware controller resolution**: Case-insensitive fallback for acronym inflections (`ip_pool_rules` → `IPPoolRulesController`).
- **Analyzer deduplication**: Vue analyzer no longer double-extracts `.vue` scripts; C/C++ analyzers properly handle `.h` file ownership.
- **Framework detection word-boundary matching**: Uses word-boundary regex instead of substring matching.
- **Micronaut framework patterns**: Fixed YAML patterns to use correct field names.
- **Go same-package method resolution**: Package-qualified calls no longer incorrectly resolve to local methods.
- **ListNameResolver full-path disambiguation**: Tries full import path as disambiguation suffix.
- **Java field_access receiver call extraction**: Fixed `this.repo.findById()` patterns using tree-sitter field names and class field type tracking.
- **JS/TS resolution fixes**: Import-path disambiguation for duplicate class names; position-based enclosing function lookup replacing name-based lookup.
- **Ruby false positive fixes**: Variable-receiver calls no longer fall through to bare-name global lookup; route detection restricted by file context.
- **Name-collision edge fixes**: Bare-name fallback emits single best match instead of fan-out (JS/TS, PHP); import-aware disambiguation for `extends` edges; containment linker prefers same-file class.
- **Tiered view token budget compliance**: Was exceeding budget by up to 177x. Force-includes now capped and sorted.
- **Tiered view shrink loop preserves connectivity**: Shrink loop considers local edge degree when choosing removal victims — disconnected singletons removed before nodes that contribute edges.
- **Java integration test path detection**: `is_test_path` now catches Gradle/Maven integration test source sets (`/src/integration/`).
- **Supply chain tier deserialization**: Cached nodes always had tier=1 due to key mismatch.
- **Route-handler linking fixes**: Route symbols no longer overwrite handler functions. Rails suffix matching for namespaced controllers; Django `view_name` support and `.as_view()` extraction; Phoenix route "route" concept; Ruby hash rocket syntax.
- **Rust impl method names**: Reference and generic types now extract only the base identifier.
- **Dangling edge dst after tier filtering**: Edges pointing to tier-filtered nodes no longer create dangling references.
- **WebSocket generic event edge explosion**: Generic `send_text()`/`ws.send()` no longer create NxM combinatorial edges.
- **smart-test scoped mode**: No longer fails when total project coverage is below 100%.
- **Go test/benchmark/example function classification**: Now requires `_test.go` file suffix. Previously, production functions like `TestPullRequest` in `services/pull/pull.go` (Forgejo) were falsely classified as test functions because the pattern only checked the `Test[A-Z]` name prefix. Go's test toolchain requires test functions to be in `*_test.go` files.
- **D file misclassification**: `.d` files are now disambiguated between D language source and GCC Makefile dependency files (`gcc -MMD` output). Content heuristics detect D source patterns (module/import declarations, class/struct/interface) vs GCC dependency patterns (`target.o: prerequisite`). Prevents false D language analysis of build artifacts.
- **Handler naming convention false positives**: `handler_by_name` pattern now requires file path to be in an HTTP-context directory (routers/, handlers/, api/, web/, controllers/, endpoints/, servlets/, resources/). Previously, any class ending in "Handler" was classified as an HTTP handler — `PartitionStatsHandler` in Iceberg's core module, `OAuth2RefreshCredentialsHandler`, logging handlers, etc.
- **Pattern matching base_class fall-through**: When a pattern specifies `base_class` (or `decorator`, `annotation`, `parameter_type`) and the symbol doesn't match, the pattern no longer falls through to symbol_kind-only matching. Previously, a pattern like `{base_class: "^SinkTask$", symbol_kind: "^class$"}` would match ANY class if the base class didn't match, because the symbol_kind-only fallback path didn't check for unmatched specific fields.

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
