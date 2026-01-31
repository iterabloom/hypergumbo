# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v2.0.0
- Released **schema** is at: v0.2.1

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

## [2.0.0] - 2026-01-31

### Changed

- **Modular package structure (ADR-0010)**: Restructured hypergumbo from a single monolithic package into 5 modular packages:
  - `hypergumbo-core`: Core infrastructure (CLI, IR, slice, sketch, linkers)
  - `hypergumbo-lang-mainstream`: Popular languages (Python, JS/TS, Java, Go, Rust, etc.)
  - `hypergumbo-lang-common`: Domain-specific languages (Haskell, Elixir, GraphQL, etc.)
  - `hypergumbo-lang-extended1`: Specialized languages (Zig, Agda, Solidity, etc.)
  - `hypergumbo`: Meta-package that installs all of the above

  **Breaking change:** Import paths changed from `hypergumbo.*` to `hypergumbo_core.*` for core modules and `hypergumbo_lang_*.*` for language analyzers. See `docs/MIGRATION-2.0.md` for migration guide. CLI usage is unchanged.

### Added

- **Smart test selection (ADR-0010)**: Implemented manifest-driven test selection for CI. The `scripts/smart-test` script uses hypergumbo's reverse-slice to identify affected tests from changed files, generating `.ci/affected-tests.txt` for CI validation. CI now runs only affected tests when manifest is valid, with sanity checks and fallback to full suite. Includes stop-the-line protocol to block PRs when full suite is broken (bypass with `fix(job-XXXXX):` title prefix).
- **Two-tier CI system**: Added `full-suite.yml` workflow with singleton concurrency for nightly validation. Fast CI uses manifest-based test selection, full suite runs as lazy singleton after dev merges.
- **Falcon framework support**: Added patterns for the Falcon bare-metal Python API framework. Detects responder methods (`on_get`, `on_post`, `on_put`, `on_delete`, `on_patch`), WebSocket handlers (`on_websocket`), hooks (`@falcon.before`, `@falcon.after`), and Resource base classes. Unlike decorator-based frameworks, Falcon uses method naming conventions for route handlers.
- **Quart framework support**: Added patterns for the Quart async Python web framework (Flask's async reimplementation). Detects route decorators (`@app.get`, `@app.post`, `@app.route`), WebSocket handlers (`@app.websocket`), middleware (`@app.before_request`, `@app.after_request`), error handlers (`@app.errorhandler`), and async lifecycle hooks (`@app.before_serving`, `@app.after_serving`). Enables route and handler detection for async Flask-compatible applications.
- **Sanic framework support**: Added patterns for the Sanic async Python web framework. Detects route decorators (`@app.get`, `@app.post`, `@app.route`), WebSocket handlers (`@app.websocket`), middleware (`@app.on_request`, `@app.on_response`), error handlers (`@app.exception`), lifecycle hooks (`@app.before_server_start`, `@app.after_server_stop`), and signal handlers (`@app.signal`). Enables route and handler detection for async Python APIs built with Sanic.
- **Nex framework support**: Added patterns for the Nex minimalist Elixir web framework. Detects `use Nex` page/API handlers, lifecycle hooks (`mount`, `render`), and HTTP method handlers (`get`, `post`, `put`, `delete`, `patch`). Enables handler detection for Nex-based applications using file-based routing.
- **Pyramid framework support**: Added patterns for the Pyramid Python web framework. Detects `@view_config` route decorators, `@view_defaults` class decorators, error handlers (`@notfound_view_config`, `@forbidden_view_config`, `@exception_view_config`), event subscribers (`@subscriber`), and SQLAlchemy model base classes. Enables route detection for Pyramid-based applications.
- **Bottle framework support**: Added patterns for the Bottle Python micro-framework. Detects route decorators (`@app.get`, `@app.route`, `@route`), hooks (`@app.hook`), and error handlers (`@app.error`).
- **JAX-RS implementation aliases**: Dropwizard, Jersey, and RESTEasy frameworks now use JAX-RS patterns automatically. This enables route detection for Java REST APIs using these frameworks.
- **Flask-Appbuilder framework support**: Added patterns for Flask-Appbuilder (used by Apache Superset). Detects `@expose` route decorators, auth decorators (`@has_access`, `@protect`), and base classes (`BaseView`, `ModelRestApi`, `ModelView`). Enables route detection for enterprise Flask applications built on Flask-Appbuilder.
- **Litestar framework support**: Added patterns for the Litestar (formerly Starlite) async Python ASGI framework. Detects standalone route decorators (`@get`, `@post`, `@put`, `@delete`, `@patch`), WebSocket handlers (`@websocket`, `@websocket_listener`), middleware (`@before_request`, `@after_request`), exception handlers (`@exception_handler`), and Controller base classes. Enables route detection for high-performance async Python APIs built with Litestar.
- **Symfony framework support**: Added patterns for the Symfony PHP framework. Detects controllers (extends `AbstractController`), forms (extends `AbstractType`), console commands (extends `Command`), event subscribers (`EventSubscriberInterface`), Doctrine repositories (`ServiceEntityRepository`, `EntityRepository`), validators (`Constraint`, `ConstraintValidator`), security voters (`Voter`), Twig extensions, fixtures, message handlers, and more. Enables component detection for enterprise PHP applications built with Symfony.
- **Quarkus framework support**: Added patterns for the Quarkus cloud-native Java framework. Detects Panache entities/repositories, scheduled tasks (`@Scheduled`), reactive markers (`@Blocking`, `@NonBlocking`), MicroProfile health checks (`@Liveness`, `@Readiness`), REST clients (`@RegisterRestClient`), config mappings (`@ConfigMapping`), reactive messaging (`@Incoming`, `@Outgoing`), and native build annotations. Complements existing JAX-RS patterns for REST endpoints.
- **Nuxt framework support**: Added patterns for the Nuxt Vue.js meta-framework. Detects page metadata (`definePageMeta`), server handlers (`defineEventHandler`), middleware (`defineNuxtRouteMiddleware`), plugins (`defineNuxtPlugin`), data fetching composables (`useAsyncData`, `useFetch`), state management (`useState`), routing (`useRouter`, `navigateTo`), SEO helpers (`useHead`, `useSeoMeta`), and error handling. Enables component and route detection for Vue applications built with Nuxt.
- **Remix framework support**: Added patterns for the Remix React meta-framework. Detects data loading (`loader`, `clientLoader`), mutations (`action`, `clientAction`), metadata (`meta`), stylesheets (`links`), headers, route config (`handle`, `shouldRevalidate`), error boundaries (`ErrorBoundary`), and hooks (`useLoaderData`, `useActionData`, `useFetcher`, `useSubmit`). Enables route and data flow detection for React applications built with Remix.
- **SvelteKit framework support**: Added patterns for the SvelteKit Svelte meta-framework. Detects data loading (`load`), form actions (`actions`), server hooks (`handle`, `handleFetch`), error handling (`handleError`, `error`), navigation (`goto`, `invalidate`, `redirect`), stores (`page`, `navigating`), and form utilities (`enhance`, `applyAction`). Enables route and data flow detection for Svelte applications built with SvelteKit.
- **Hanami framework support**: Added patterns for the Hanami Ruby framework. Detects actions (`Hanami::Action`), repositories (`Hanami::Repository`), entities (`Hanami::Entity`), interactors (`Hanami::Interactor`), views (`Hanami::View`), validations (`Dry::Validation::Contract`), and configuration (`Hanami::Application`). Enables clean architecture detection for Ruby applications built with Hanami.
- **Feathers.js framework support**: Added patterns for the Feathers real-time microservices framework. Detects services (`Service`, adapter classes), hooks (`authenticate`, `authorize`, `validateSchema`), channels (`channels`, `publish`), authentication (`AuthenticationService`, JWT/Local/OAuth strategies), error handling (`FeathersError` classes), and transport providers (`express`, `socketio`). Enables service and real-time detection for Node.js microservices.
- **Masonite framework support**: Added patterns for the Python Masonite framework. Detects controllers (extends `Controller`), models (extends `Model`), commands (extends `Command`), providers (extends `Provider`), validators (extends `Validator`), and route calls (`Route.get`, `Route.post`, `Route.resource`, `Route.api`). Enables route and component detection for Laravel-inspired Python applications.
- **AdonisJS framework support**: Added patterns for the Node.js AdonisJS framework. Detects controllers by naming convention (`*Controller` classes), route decorators (`@Get`, `@Post`, `@Put`, `@Delete`), middleware (`@Middleware`), dependency injection (`@bind`, `@inject`), models (extends `BaseModel`), commands (extends `BaseCommand`), and route calls (`Route.get`, `Route.post`). Enables route detection for Laravel-inspired Node.js applications.
- **Roda framework support**: Added patterns for the Ruby Roda web framework. Detects application class (extends `Roda`), route definitions (`route` block), HTTP method handlers (`r.get`, `r.post`, `r.put`, `r.delete`), path matching (`r.on`, `r.is`, `r.root`), plugins (`plugin` calls), and response helpers. Enables routing tree detection for Ruby applications using Roda's unique path-matching DSL.
- **Javalin framework support**: Added patterns for the lightweight Java/Kotlin Javalin web framework. Detects route handlers (`app.get`, `app.post`, `app.put`, `app.delete`, `app.patch`), WebSocket handlers (`app.ws`), middleware (`app.before`, `app.after`), exception handlers (`app.exception`), error handlers (`app.error`), SSE handlers (`app.sse`), CRUD handlers (`app.crud`), and route groups. Enables route detection for lightweight Java/Kotlin APIs built with Javalin.
- **Scalatra framework support**: Added patterns for the Sinatra-like Scala web framework Scalatra. Detects servlets (extends `ScalatraServlet`, `ScalatraFilter`), route handlers (`get`, `post`, `put`, `delete`, `patch`), middleware (`before`, `after`), error handlers (`error`, `notFound`), and JSON support traits (`JacksonJsonSupport`). Enables route detection for Scala web applications built with Scalatra.
- **Http4k framework support**: Added patterns for the functional Kotlin Http4k HTTP toolkit. Detects route bindings (`bind` calls), handlers (extends `HttpHandler`), routers (`RoutingHttpHandler`, `routes` calls), middleware (extends `Filter`, `ServerFilter`, `ClientFilter`), WebSocket handlers (`websockets`), server setup (`asServer`), and lenses for type-safe parameter extraction. Enables route detection for functional Kotlin HTTP applications.
- **http4s framework support**: Added patterns for the purely functional Scala http4s HTTP library. Detects `HttpRoutes` and `HttpRoutes.of` for route definitions, `BlazeServerBuilder`/`EmberServerBuilder` for server setup, middleware (`Logger`, `AuthMiddleware`, `CORS`, `GZip`), `EntityDecoder`/`EntityEncoder` for body parsing, `WebSocketBuilder` for WebSockets, and `IOApp` application trait. Enables route detection for functional Scala HTTP applications.
- **Vert.x framework support**: Added patterns for the reactive JVM Vert.x toolkit. Detects `Router` creation, route definitions (`router.get`, `router.post`, etc.), handlers (`.handler`, `.blockingHandler`), `AbstractVerticle` base class, EventBus messaging (`consumer`, `send`, `publish`), WebSocket/SockJS handlers, middleware (BodyHandler, SessionHandler, CorsHandler, AuthHandler), and server creation. Enables route and verticle detection for reactive Java/Kotlin applications.
- **Restify framework support**: Added patterns for the Node.js Restify REST API framework. Detects server creation (`restify.createServer`), route handlers (`server.get`, `server.post`, `server.put`, `server.del`, `server.patch`), middleware (`server.pre`, `server.use`), plugins (bodyParser, queryParser, CORS, throttle, requestLogger), error handlers, and HTTP clients (JSON/String clients). Enables route detection for Node.js REST APIs built with Restify.
- **CodeIgniter framework support**: Added patterns for the lightweight PHP CodeIgniter MVC framework. Detects controllers (extends `BaseController`, `CI_Controller`), models (extends `Model`, `CI_Model`), routes (`$routes->get`, `$routes->post`, `$routes->resource`), filters (implements `FilterInterface`), migrations, seeders, commands, and view loading. Supports both CI4 and CI3 legacy patterns.
- **Lumen framework support**: Added patterns for the Laravel Lumen micro-framework. Detects controllers (extends `Controller`), routes via `$router->get/post/put/delete` and `$app->get/post`, route groups, middleware registration, service providers, models (Eloquent), event listeners, jobs, commands, and exception handlers. Enables route detection for PHP microservices and APIs built with Lumen.
- **Padrino framework support**: Added patterns for the Ruby Padrino web framework (built on Sinatra). Detects application class (extends `Padrino::Application`), controllers, routes (`get`, `post`, `put`, `delete`, `patch`), before/after filters, helpers, mailers, models (ActiveRecord, Sequel, DataMapper, Mongoid), admin interface, and error handlers. Enables route detection for Ruby applications built with Padrino.
- **CakePHP framework support**: Added patterns for the PHP CakePHP RAD framework. Detects controllers (extends `Controller`, `AppController`), Table models (database layer), Entity classes (data objects), routes (`$routes->connect`), route groups (`scope`, `prefix`), middleware (implements `MiddlewareInterface`), components, behaviors, helpers, commands (Shell and Command), migrations, seeders, cells, forms, plugins, event listeners, mailers, fixtures, and test cases. Enables component detection for PHP applications built with CakePHP.
- **Yii framework support**: Added patterns for the high-performance PHP Yii framework. Detects controllers, ActiveRecord models, widgets, modules, standalone actions, behaviors, validators, migrations, console commands, asset bundles, action filters, access control, authentication (IdentityInterface, User), cache components, queue jobs, REST controllers (ActiveController), URL rules, and test fixtures. Enables component detection for PHP applications built with Yii.
- **Laminas framework support**: Added patterns for the PHP Laminas (formerly Zend) enterprise component library. Detects MVC controllers (`AbstractActionController`, `AbstractRestfulController`), forms, fieldsets, input filters, validators, filters, PSR-15 middleware and request handlers, table gateways (data layer), hydrators, modules, event listeners, service factories, view helpers, controller plugins, authentication/authorization (ACL, RBAC), cache adapters, session containers, mail, logging, routing, and view models. Enables component detection for PHP enterprise applications built with Laminas.
- **FuelPHP framework support**: Added patterns for the PHP FuelPHP HMVC framework. Detects controllers (`Controller`, `Controller_Rest`, `Controller_Template`, `Controller_Hybrid`), models (`Model`, `Model_Crud`, `Orm\Model` variants), tasks (CLI commands), viewmodels (presenter pattern), migrations, validation, fieldsets, auth drivers, cache handlers, database connections, packages, modules, observers, email drivers, and parsers. Enables component detection for PHP applications built with FuelPHP.
- **Ring/Compojure framework support**: Added patterns for the Clojure Ring HTTP abstraction and Compojure routing library. Detects `defroutes` and `routes` combinators, HTTP method macros (`GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`, `ANY`), `context` for route groups, Ring `wrap-*` middleware pattern, `not-found` error handlers, response helpers, and Component/Mount lifecycle hooks. Enables route detection for Clojure web applications.
- **Pedestal framework support**: Added patterns for the Clojure Pedestal web framework. Detects route definitions (`defroutes`, `table-routes`, `expand-routes`), interceptors (`definterceptor`, `interceptor`, `on-request`, `on-response`), body parsers (`body-params`, `json-body`, `transit-body`, `edn-body`), content negotiation, server lifecycle (`create-server`, `start`, `stop`), SSE (Server-Sent Events), and WebSocket handlers. Enables interceptor-based middleware detection for Clojure applications.
- **Servant framework support**: Added patterns for the Haskell Servant type-safe API library. Detects server functions (`serve`, `run`, `runSettings`), `Handler` type for handlers, `hoistServer` for natural transformations, documentation helpers (`serveWithDocs`, swagger/openapi), error responses (`err404`, `throwError`, `catchError`), WAI Application/Middleware types, and authentication helpers. Enables handler detection for type-safe Haskell APIs built with Servant.
- **Scotty framework support**: Added patterns for the Haskell Scotty micro-framework (Sinatra-inspired). Detects `scotty`/`scottyT`/`scottyOpts` application starters, route handlers (`get`, `post`, `put`, `delete`, `patch`, `options`), `middleware` function, response helpers (`text`, `html`, `json`, `file`), parameter extraction (`param`, `formParam`, `queryParam`), error handling (`raise`, `rescue`, `defaultHandler`), and control flow (`redirect`, `next`, `finish`). Enables route detection for simple Haskell web applications.
- **Utility file entrypoint penalty**: Entrypoints in utility directories (docs, examples, scripts, tools, benchmarks) now receive a 50% confidence penalty. Reduces noise from documentation and example code in entrypoint detection.
- **Test file weighting for slice ranking**: Added `test_weight` parameter to `rank_slice_nodes()` for downweighting test file nodes in slice rankings. Production code now ranks higher than test code when analyzing reverse slices.

### Fixed

- **Linker duplicate edge elimination**: Added edge deduplication after linkers run. The event-sourcing linker could create duplicate edges when matching publisher-subscriber pairs. Example: killbill repo went from 25494 edges (472 duplicates) to 25022 unique edges.

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
