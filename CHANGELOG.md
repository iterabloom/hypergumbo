# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.0.2
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

#### Language & framework support

- **Python FFI linker (ctypes/cffi/PyO3)**: New cross-language linker that connects Python code calling C/C++ functions via `ctypes.CDLL`/`ctypes.cdll.LoadLibrary` and `cffi` `ffi.dlopen()` to the corresponding C/C++ function symbols, creating `ffi_bridge` edges. Also detects Rust functions annotated with PyO3 `#[pyfunction]`/`#[pymethods]` and matches them to unresolved Python call edges. Activates when both Python and C/C++/Rust files are present. Three evidence types: `ctypes_call`, `cffi_call`, `pyo3_bridge`.
- **ORM query linker**: Detects Django ORM (`Model.objects.filter/get/all/...`) and Flask-SQLAlchemy (`Model.query.filter_by/first/...`) query patterns in Python source files and creates `model_reference` edges from the calling function to the Model class symbol. This increases in-degree centrality of Model classes, fixing under-ranking (e.g., Django Model was degree 5 / rank 2156; now gains edges from every view that queries it).
- **Java method invocation fix**: Fixed call graph extraction for Java method invocations with `field_access` receivers (e.g., `this.repo.findById()`, `svc.process()`). The analyzer now uses tree-sitter field names (`name`, `object`) instead of scanning children for identifiers, and tracks class field types for type inference. Previously, most Java method calls produced no call edges because `field_access` nodes were not handled.
- **Chained member access call resolution (Kotlin, C#, Scala, Python)**: Fixed call graph extraction for `this.field.method()` / `self.field.method()` patterns across four languages. This was the same structural issue as the Java fix — analyzers only checked for simple identifier receivers, missing nested member/navigation/field expressions. Kotlin now tracks constructor parameter types and resolves `this.svc.process()`. C# now tracks field declaration types and handles `this._repo.Save()`. Scala now extracts method names from `field_expression` nodes (previously took the receiver name instead of the method name, producing zero call edges for all method calls). Python now pre-collects class field types from `__init__` (both typed parameter assignments `self.svc = svc` where `svc: Service` and constructor calls `self.repo = Repository()`) and resolves `self.field.method()` calls.
- **JS/TS import-path disambiguation (INV-013)**: When multiple files define the same class name (e.g., NestJS monorepos with duplicate `CatsService` in different apps), the analyzer now uses `import { Foo } from './module'` paths to disambiguate. Applies to `this.property.method()` (Case 1b) and `variable.method()` (Case 3). Previously, the resolver picked whichever symbol was last processed (effectively arbitrary).
- **JS/TS monorepo enclosing function fix**: Fixed `_get_enclosing_function` to use position-based symbol lookup instead of name-based `global_symbols` lookup. In monorepos with duplicate class names, `global_symbols` only keeps one symbol per name, so `_get_enclosing_function` returned `None` for all but one file — causing most call edges to be silently dropped. On NestJS repo: before, 1 of 9 `CatsController.create` methods produced call edges; now all 9 do.
- **Cgo linker (Go-C interop)**: New cross-language linker that resolves Go `C.funcName()` calls (via `import "C"`) to their C/C++ function implementations. The Go analyzer creates unresolved edges with pattern `go:C:0-0:{name}:unresolved` for cgo calls; the linker matches these to C/C++ function symbols by name and creates `cgo_bridge` edges. Activates when both Go and C (or C++) files are present.
- **Framework detection for 16 additional languages**: Haskell (servant, scotty), Clojure (ring-compojure, pedestal), R (shiny, plumber), Lua (openresty, lapis, love2d), C++ (qt), Erlang (cowboy), F# (giraffe, saturn, suave), Kotlin (Ktor, Exposed, Koin, Kodein), C# (ASP.NET Core, Blazor, Entity Framework, SignalR), Dart (Shelf, Aqueduct, Angel, Dart Frog, Serverpod), Julia (Genie, Oxygen, HTTP.jl, Mux), OCaml (Dream, Opium, Cohttp, Eliom), Nim (Jester, Prologue, Karax, Mummy), Zig (zap, http.zig, zig-network), D (vibe.d, Hunt, DiamondMVC), Groovy (Grails, Ratpack, Micronaut). Includes YAML pattern files for symbol enrichment (routes, handlers) where applicable.
- **Test framework patterns for 16 additional languages**: Elixir (ExUnit), Scala (ScalaTest/MUnit/Specs2), Dart (test), Clojure (clojure.test), Haskell (HSpec/Tasty/QuickCheck), Erlang (EUnit/Common Test), F# (Expecto/NUnit/xUnit), Ruby (RSpec), Julia (Test), OCaml (OUnit/Alcotest), Lua (busted/luaunit), R (testthat), Nim (unittest), Zig (built-in), D (unittest), Groovy (Spock/JUnit).
- **Main function entrypoint detection for 7 more languages**: D, Nim, Zig, V, Odin, Gleam, Haxe.
- **Route path prefix inheritance across frameworks**: Fixed `prefix_from_parent` for Spring Boot, JAX-RS, Micronaut, and ASP.NET. Class-level route annotations (e.g., `@RequestMapping("/owners/{ownerId}")`, `@Path("/api/users")`, `@Controller("/api")`, `[Route("api/users")]`) now correctly combine with method-level route annotations. Also fixed Spring Boot `@RequestMapping` positional arg extraction (`args[0]|kwargs.value`).
- **Go route-handler linking**: Gin, Echo, Fiber, Chi routes are now linked to handler functions via `handler_name` metadata, resolving both simple identifiers (`listUsers`) and package-qualified names (`handlers.GetAPI`).
- **Go HTTP client detection**: `net/http` calls (`http.Get`, `http.Post`, `http.Head`, `http.NewRequest`, `http.NewRequestWithContext`) enable cross-language linking from Go clients to route handlers in other backends.
- **Django framework patterns**: Template tag/filter patterns (`@register.simple_tag`, `@register.inclusion_tag`, `@register.filter`) and signal receiver edge detection (`@receiver(signal)` → `signal_receiver` edges).
- **Flask framework patterns**: Jinja2 template customizations (`@app.template_filter/global/test`), Blinker signal handlers (`@signal.connect` for `request_started`, `request_finished`, etc.), and Flask-RESTful support (`api.add_resource()`, `Resource` base class, `fields.*` serializers, `reqparse.RequestParser`).

#### Analysis core

- **Return type tracking (Python, TypeScript, Java, Kotlin, C#, Dart)**: Variable type inference now handles function return type annotations across all six typed languages. When a function/method has a return type annotation and its result is assigned to a variable, subsequent method calls on that variable resolve to the return type's class methods. Python: `def get_client() -> Client` tracks from `stub = get_client()`. TypeScript: `function getClient(): Client` tracks from `const client = getClient()`. Java: `Client createClient()` tracks from `Client c = f.createClient()`. Kotlin: `fun getClient(): ServiceClient` tracks from `val client = getClient()`. C#: `ServiceClient GetClient()` tracks from `var client = factory.GetClient()`. Dart: `ServiceClient getClient()` tracks from `var client = getClient()`. Previously, only direct constructor calls enabled type inference. Only simple (non-generic) return types are tracked. C# also gained custom return type recognition in method signatures (e.g., `ServiceClient` return types were previously omitted from signatures).
- **Reverse slice class expansion**: When reverse-slicing from a class/interface entry point (e.g., `--reverse --entry OwnerRepository`), the slicer now automatically expands the starting set to include all member methods discovered via `contains` edges. Previously, class-level reverse slices returned empty results because call edges connect methods to methods, not classes. Now `--reverse --entry OwnerRepository` finds callers of `findById`, `search`, etc. Expansion applies to class, interface, module, struct, trait, and enum container kinds.
- **Library export detection**: Public symbols are now detected as `LIBRARY_EXPORT` entrypoints in Go (uppercase functions/types/interfaces/structs), Elixir (modules and public `def` functions), and Python (`__init__.py` public symbols and re-exports like `from .routing import APIRouter`). Enables auto-slicing for library repos that lack `main()` or HTTP routes. Supporting pattern features: `symbol_path` (file-path matching), `modifiers` (positive filter), `modifiers_exclude` (negative filter).
- **Containment linker**: Creates `contains` edges from class/interface symbols to their method/getter/setter symbols across all 15 supported languages using naming conventions (`.`, `#`, `::`). Handles nested classes and struct/trait/enum containers. Previously, methods were orphaned in the graph.
- **Containment linker name collision fix**: When multiple classes share the same name (e.g., Django has 238 classes named `Model` — 1 real + 237 test stubs), the linker now prefers the class in the same file as the method. Previously, the last class encountered won, causing methods to be linked to the wrong parent (e.g., `Model.save` in `base.py` was linked to a test `Model` in `test_ordinary_fields.py`).
- **D method extraction with qualified names**: Functions inside D struct/class/interface bodies now extract as `kind="method"` with qualified names (e.g., `Searcher.search`), enabling the containment linker.
- **Non-code node exclusion**: Documentation/config nodes (markdown, TOML, INI), CSS structural nodes, and config-metadata nodes are excluded by default. Use `--include-docs` to include them.
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
- **Exclude derived/minified files by default**: Tier 4 symbols excluded from output. Use `--max-tier 4` to include them.
- **Increase default `--max-files` for slice from 20 to 50**: Previous default was too restrictive for large codebases.
- **Django route method accuracy**: `path()`/`re_path()`/`url()` no longer hardcode `GET`. Django routing doesn't specify methods.
- **TypeScript decorator kind filtering**: Decorator resolution rejects class/interface/type symbols as targets. Non-function matches produce unresolved edges (confidence 0.50).

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

### Fixed

- **`--entry auto` respects `--exclude-tests` and `--max-tier`**: Filters now apply to auto-entry selection, not just `--list-entries`.
- **Tiered view token budget compliance**: Was exceeding budget by up to 177x. Force-includes now sorted by centrality and capped. Non-essential fields stripped.
- **Supply chain tier deserialization**: Cached nodes always had tier=1 due to flat-vs-nested key mismatch. Fixed via `Symbol.from_dict()`.
- **Route-handler linking for same-name symbols**: Route symbols could overwrite handler functions in the lookup dict. Now preserves function/method/class symbols. Gin-realworld linking: 0% → 100%.
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
