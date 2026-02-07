# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.0.2
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

- **Framework detection for 7 additional languages**: Extended dependency-file scanning to detect frameworks in languages that previously only had YAML patterns:
  - **Haskell**: servant, scotty (via `.cabal` files)
  - **Clojure**: ring-compojure, pedestal (via `deps.edn`, `project.clj`)
  - **R**: shiny, plumber (via `DESCRIPTION` files)
  - **Lua**: openresty, lapis, love2d (via `.rockspec` files)
  - **C++**: qt (via `CMakeLists.txt`, `.pro` files)
  - **Erlang**: cowboy (via `rebar.config`)
  - **F#**: giraffe, saturn, suave (via `.fsproj` files)
- **Framework YAML patterns**: Added pattern files for R Shiny, R Plumber, Lua OpenResty/Lapis, C++ Qt, Erlang Cowboy, and F# Giraffe/Saturn. These enable symbol enrichment (route detection, handler classification) once frameworks are detected.
- **Test framework patterns for 16 additional languages**: Extended `test-frameworks.yaml` to detect test functions in Elixir (ExUnit), Scala (ScalaTest/MUnit/Specs2), Dart (test package), Clojure (clojure.test), Haskell (HSpec/Tasty/QuickCheck), Erlang (EUnit/Common Test), F# (Expecto/NUnit/xUnit), Ruby (RSpec - was missing despite being mentioned), Julia (Test stdlib), OCaml (OUnit/Alcotest), Lua (busted/luaunit), R (testthat), Nim (unittest), Zig (built-in), D (unittest), and Groovy (Spock/JUnit).
- **Library export detection for Go and Elixir**: Go exported symbols (uppercase functions, types, interfaces, structs) and Elixir modules are now detected as `LIBRARY_EXPORT` entrypoints, enabling auto-slicing for library repos that lack main() functions or HTTP routes. Previously, Go/Elixir library repos (e.g., gin, phoenix) produced empty or test-only slices because no entrypoints were detected. Library-exports patterns are now loaded as a convention (like main-functions), not gated on framework detection.
- **Connectivity-based entrypoint fallback**: When no YAML patterns produce entrypoints (e.g., Rust libraries without `pub` tracking, or repos in unsupported languages), the system now falls back to selecting the most-connected callable symbols (functions, methods, constructors) as pseudo-entrypoints. At most 5 fallback entrypoints are selected, ranked by out-degree, with a base confidence of 0.50 (below all concept-based entrypoints). This prevents `--entry auto` from hard-failing on repos with no pattern matches.
- **Containment linker**: New structural linker that creates `contains` edges from class/interface symbols to their method/getter/setter symbols. Works across all 15 supported languages using naming conventions (dot `.`, Ruby `#`, Rust `::`). Also handles nested classes (`Outer.Inner`). Previously, methods were orphaned in the graph — disconnected from their parent classes — inflating orphan rates and hiding class structure from slice traversal.
- **Elixir public function visibility tracking**: The Elixir analyzer now distinguishes `def` (public) from `defp` (private) functions, and `defmacro` from `defmacrop` macros, via the `modifiers` field. Public Elixir functions are now detected as `LIBRARY_EXPORT` entrypoints (alongside existing module-level detection), enabling connectivity-boosted auto-slicing on Elixir libraries like Phoenix.
- **Pattern `modifiers_exclude` filter**: YAML framework patterns now support `modifiers_exclude` to reject symbols with specific modifiers (e.g., `modifiers_exclude: "^private$"` excludes `defp` functions in Elixir). This generalizes to any language with visibility modifiers.
- **Main function entrypoint detection for 7 more languages**: D, Nim, Zig, V, Odin, Gleam, and Haxe `main` functions are now detected as program entrypoints, improving usefulness for codebases in these systems/cross-platform programming languages.
- **Framework detection for 9 more languages**: Extended dependency-file scanning for languages that had test-framework patterns but no application-framework detection:
  - **Kotlin**: Ktor, Exposed, Koin, Kodein (via `build.gradle.kts`)
  - **C#**: ASP.NET Core, Blazor, Entity Framework, SignalR (via `*.csproj`)
  - **Dart**: Shelf, Aqueduct, Angel, Dart Frog, Serverpod (via `pubspec.yaml`)
  - **Julia**: Genie, Oxygen, HTTP.jl, Mux (via `Project.toml`)
  - **OCaml**: Dream, Opium, Cohttp, Eliom (via `dune-project`, `*.opam`)
  - **Nim**: Jester, Prologue, Karax, Mummy (via `*.nimble`)
  - **Zig**: zap, http.zig, zig-network (via `build.zig.zon`)
  - **D**: vibe.d, Hunt, DiamondMVC (via `dub.json`, `dub.sdl`)
  - **Groovy**: Grails, Ratpack, Micronaut (via `build.gradle`)
- **Secret scanning with gitleaks**: `hypergumbo sketch` now scans output for potential secrets before displaying it. This helps prevent accidentally pasting credentials into LLM chat windows. Install gitleaks with `hypergumbo install-gitleaks`. Scans run by default (opt-out with `--no-secret-scan`). Always warns that scanning is best-effort, not exhaustive.
- **Embeddings install/uninstall scripts**: Added `scripts/install-embeddings` and `scripts/uninstall-embeddings` to manage the optional embedding dependencies. Use `--check` to verify installation status, `--model` to pre-download models, and `--all`/`--cache` to clean up PyTorch and model cache.
- **CLI extras management commands**: Added CLI subcommands for managing optional dependencies:
  - `hypergumbo add-extras` - Install all optional extras (grammars, gitleaks, embeddings), skipping already-installed components
  - `hypergumbo remove-extras` - Uninstall gitleaks and embeddings
  - `hypergumbo install-embeddings` - Install embedding dependencies (sentence-transformers)
  - `hypergumbo uninstall-embeddings` - Remove embedding dependencies (with `--all` to also remove PyTorch)
  - `hypergumbo uninstall-gitleaks` - Remove gitleaks binary
- **Scoped coverage for smart-test (ADR-0011)**: smart-test now uses the `last-green-sha` marker from CI as the baseline for comparison, and enforces 100% coverage only for changed source files (not the entire codebase). This enables fast feedback (~45 tests in <1s vs 5700+ tests) while still enforcing coverage for your changes.
- **Per-package coverage check script**: Added `scripts/check-package-coverage` to verify each package achieves 100% coverage when tested in isolation (mimicking CI). Catches cross-package coverage dependencies before pushing.
- **Test placement guidelines**: Added documentation in AGENTS.md explaining why tests must be in the same package as the code they cover, and why subprocess tests don't contribute to pytest-cov coverage.
- **Cache management CLI commands**: Added `hypergumbo cache-status` and `hypergumbo cache-clear` for managing the analysis cache (~/.cache/hypergumbo/). Use `cache-status` to see entry count and total size. Use `cache-clear` with `--older-than N` to remove entries older than N days, or `--dry-run` to preview deletions.
- **Decorator/annotation edge detection (INV-012)**: Decorator and annotation applications now create `decorated_by` edges in the call graph, making decorator-based registration patterns (e.g., `@app.get("/users")`, `@Injectable`, `@GetMapping`) visible in centrality rankings and slices. Supported in 5 languages: Python (decorators), TypeScript (decorators), Java (annotations), C# (attributes), and Rust (attributes).
- **Django template tag and filter patterns**: Added YAML patterns for Django `@register.simple_tag`, `@register.inclusion_tag`, and `@register.filter` decorators. Enriches template customization points with `template_tag` and `template_filter` concepts.
- **Flask Jinja2 template patterns**: Added YAML patterns for Flask/Jinja2 template customizations: `@app.template_filter`, `@app.template_global`, and `@app.template_test`. Mirrors Django template patterns for consistent detection across Python web frameworks.
- **Django signal receiver edge detection**: Functions decorated with `@receiver(signal)` now create `signal_receiver` edges from the signal to the handler function, making Django's signal dispatch visible in the call graph. Supports single signals, multiple signals (`@receiver([sig1, sig2])`), and sender kwargs.
- **Flask-RESTful framework support**: Added dedicated `flask-restful.yaml` patterns for `api.add_resource()` route registration, `Resource` base class detection, `fields.*` serializer types, and `reqparse.RequestParser` request parsing.
- **Flask Blinker signal pattern detection**: Added patterns for Flask's Blinker-based signals (`request_started`, `request_finished`, `got_request_exception`, template rendering signals, app context signals, `message_flashed`). Detects `@signal.connect` handlers and enriches them with the `signal_handler` concept.
- **`list-my-prs` convenience script**: Added `scripts/list-my-prs` to list open PRs authored by the current user. Useful for checking PR status after context compaction.

### Changed

- **Branch coverage enabled in smart-test**: smart-test now uses `--cov-branch` to measure branch coverage locally. This catches untested code paths (if/else branches, loop conditions) that line coverage misses. Branch coverage tests are in separate `BRANCHES_*.py` files for easy management.
- **Embeddings now optional by default**: sentence-transformers (and PyTorch ~2GB) are no longer installed by default. This makes the base install much lighter. To enable embeddings: `pip install hypergumbo[embeddings]` or `./scripts/install-embeddings`. hypergumbo works fine without embeddings (graceful degradation).
- **Pinned all dependencies using compatible release operator**: All dependencies in pyproject.toml files now use `~=X.Y.Z` (compatible release) instead of `>=X.Y`. This allows patch updates while preventing unexpected minor/major version changes that could introduce breaking changes (like the huggingface_hub 0.x → 1.x httpx migration).
- **CI manifest includes changed source files**: The `.ci/affected-tests.txt` manifest now includes a `CHANGED_SOURCE_FILES` section, enabling CI to perform scoped coverage checks without recomputing changed files.
- **Manifest terminology: "affected" → "selected"**: The manifest now uses `SELECTED_TESTS` instead of `AFFECTED_TESTS`, and includes a `Mode:` field (`targeted` or `full-suite`) so CI can display appropriate messaging. "Selected tests" accurately describes what the manifest contains regardless of whether it's a targeted subset or the full suite.
- **Consistent coverage config in full-suite.yml**: All four test jobs (core, mainstream, common, extended) now check for sentence-transformers and use `.coveragerc.no-embeddings` when unavailable. Previously only test-core did this.
- **smart-test detects all change sources**: Now detects committed, staged, AND unstaged changes. Previously only detected committed changes, causing stale manifests when pytest ran before staging.
- **Infrastructure-only PRs skip pytest**: PRs that only change shell scripts, YAML, or config files (no Python source) now skip pytest entirely in `ci.yml`. Full suite still runs via `full-suite.yml` after merge.
- **Clearer CI manifest display**: CI now shows just the manifest header (Mode, Reason) and a count, not a truncated list of test files that looked confusing ("3 files → 171 tests").
- **Three-way stop hook logic**: Replaced unconditional stop-hook blocking with three-way decision logic: (1) pending `**TODO**` markers in the invariant ledger block with a listing of items, (2) reflection completed within 30 minutes triggers a cooldown prompt instead of the full checklist, (3) stale reflection triggers the full checklist. Eliminates the Sisyphean re-firing pattern that consumed 30-55% of autonomous session transcript output.
- **Shared Forgejo API library (`scripts/lib/forgejo-api.sh`)**: Extracted duplicated API/JSON/env logic from `auto-pr`, `ci-debug`, and `contribute` into a shared library. Adds safe JSON parsing that handles HTML error pages gracefully, CI polling with a 40-minute timeout (`--timeout` flag), PR deduplication before creation, `PR_PENDING` cleanup trap on unexpected exit, and `ci-complete` job 5-minute timeout to prevent indefinite pending state.
- **`merge-pr` recovery script**: New `scripts/merge-pr` script for merging existing PRs with optional `--wait-for-ci` polling. Provides a focused recovery path when `auto-pr`'s merge step fails but the PR already exists.
- **Post-compaction state recovery**: Added enriched `.agent/last_stop_check.json` that captures branch, last PR number/state, pending TODOs, unfixed invariants, and free-text notes. Agents can read this file after context compaction to recover awareness of in-progress work without re-running verification commands.
- **Updated stale documentation counts**: Corrected analyzer (67→104), linker (15→18), framework pattern (37→82), and convention pattern (4→5) counts across README, spec, LANGUAGES.md, ARCHITECTURE.md, generate-architecture, governance case critiques, and ADR-0009 checklist. History files left as-is.
- **Pre-push hook for protected branches**: Added `.githooks/pre-push` hook that blocks direct pushes to `dev` and `main` locally, before the remote rejects them. Allows feature branches and `refs/for/dev/` PR refs. Saves time from accidental protected-branch push failures.
- **Bakeoff convergence recognition + stable artifact paths**: Bakeoff scripts (`bakeoff`, `bakeoff-features`, `bakeoff-features-reflect`) now default to `~/hypergumbo_lab_notebook/bakeoff_artifacts/` instead of `.` (cwd). `init` creates timestamped session directories (`broad-YYYYMMDD-HHMMSS/`, `deep-YYYYMMDD-HHMMSS/`) so prior artifacts are never overwritten. Subsequent commands auto-discover the latest session. The stop hook now appends bakeoff convergence status (CONVERGED or NEEDS_WORK) to the reflection/cooldown prompt, preventing redundant bakeoff re-runs.

### Fixed

- **Django route-handler linking**: Added Django `view_name` support to the route-handler linker. Django routes with `view_name` metadata now get `routes_to` edges to their view functions/classes. This reduces orphan routes in Django projects (e.g., Django had 884 orphan routes that now can be linked).
- **Django class-based view URL patterns**: Fixed extraction of view names from Django `path()` calls using `.as_view()` method (e.g., `path('login/', views.LoginView.as_view())`). Previously these were extracted with `view_name=None`. Now correctly extracts the class name (e.g., "LoginView") enabling route-handler linking for CBVs. Tested on Django repo: routes with view_name increased from 0 to 20.
- **Rust impl method names for reference and generic types**: Fixed malformed method names in Rust impl blocks where the type is a reference (`&'a M`) or generic (`Writer<'a, M, W>`). Previously the full type text was used (e.g., `&'a M::line_terminator`), now only the base type identifier is extracted (e.g., `M::line_terminator`, `Writer::write_output`). This cosmetic fix affects ~0.2% of Rust symbols.
- **Phoenix route entrypoint detection**: Phoenix/Elixir route symbols created by the analyzer (kind="route") were not being detected as entrypoints because they lacked the "route" concept needed for entrypoint detection. Added `symbol_kind: "^route$"` pattern to phoenix.yaml to enrich route symbols with the "route" concept, matching the existing pattern in rails.yaml and laravel.yaml.
- **Ruby hash rocket route syntax**: Fixed Rails route extraction for the `"path" => "controller#action"` syntax (hash rocket shorthand). Previously only `to: "controller#action"` was recognized. This enables route-handler linking for routes defined with the shorthand syntax (e.g., `post "persist" => "sessions#persist"`). Also added `match` to HTTP_METHODS for routes using the `match` DSL method.
- **httpx IPv6 CIDR proxy bug workaround**: Fixed embedding model loading failures when `NO_PROXY` environment variable contains IPv6 CIDR notation (e.g., `fd00:200::/40`). httpx 0.25+ has a bug where IPv6 CIDR in NO_PROXY causes `InvalidURL: Invalid port` errors. The workaround temporarily sanitizes NO_PROXY during model initialization.
- **smart-test scoped mode total coverage**: Fixed smart-test scoped mode incorrectly failing when total project coverage was below 100%. Scoped mode should only enforce coverage on changed files, not the entire codebase.
- **bakeoff-features forward slice flags**: Removed `--exclude-utility` from forward slices in `bakeoff-features run`. Entry points may reside in utility directories (e.g., `docs_src/` for FastAPI) but their dependencies are still useful for assessment.
- **Dead code in extract_usage_value "last" transform**: Removed redundant `if " | " in expr:` check inside the "last" transform handler — the outer guard at the top of the pipe processing block already guarantees this condition is true.

### Removed

- **Bootstrap mode in CI**: Removed obsolete bootstrap mode code paths from ci.yml. The stable hypergumbo release now includes `slice --files`, so smart-test can always generate proper manifests.

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
