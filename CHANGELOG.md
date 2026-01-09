# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v0.9.0
- Released **schema** is at: v0.2.0

This changelog tracks the **tool version** (package releases). The **schema version** (output format) is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version only changes when the JSON output format has breaking changes.

## [Unreleased]

## [0.9.0] - 2026-01-09

### Changed (Breaking)
- **Schema version 0.2.0:** The output schema version bumped from 0.1.0 to 0.2.0.
  - New `entrypoints` field added to behavior map output. This field contains detected
    entrypoints computed from symbols, persisting them for downstream consumers.

### Added
- **Entrypoints in JSON output:** The behavior map now includes an `entrypoints` array
  containing all detected entrypoints with their stable IDs, paths, and metadata.
  Previously entrypoints were computed dynamically by the slice command but not persisted.
- **Smart JSON detection in slice command:** When passing a `.json` file as the positional
  argument to `hypergumbo slice`, it's now automatically treated as `--input` rather than
  a repo path. This fixes confusing behavior where `hypergumbo slice results.json <symbol>`
  would fail silently. The `--input` flag still takes precedence if explicitly provided.
- **Connectivity-based entrypoint ranking:** Entrypoints are now ranked by graph connectivity
  in addition to detection confidence. An entrypoint with many outgoing edges (calls to other
  functions) is considered more "interesting" and ranks higher. This makes `--entry auto`
  automatically select the most connected entrypoint, improving slice quality for codebases
  with many entrypoints (e.g., test suites with many `main` functions).
- **`--frameworks` flag (ADR-0003 Item 3):** Control framework detection with new CLI option:
  - `--frameworks=none`: Skip framework detection entirely (base analysis only)
  - `--frameworks=all`: Check all known framework patterns for detected languages
  - `--frameworks=fastapi,celery`: Only check specified frameworks (explicit mode)
  - Default (no flag): Auto-detect based on detected languages (existing behavior)
  - Output includes `framework_mode` field indicating which mode was used
- **Linker activation conditions (ADR-0003 Item 4):** Linkers now have structured activation criteria:
  - `always`: Protocol linkers (HTTP, WebSocket, MQ) always run
  - `frameworks`: Framework linkers (gRPC, GraphQL) only run when framework detected
  - `language_pairs`: Language-pair linkers (JNI, Swift-ObjC) only run when both languages present
  - New `LinkerActivation` dataclass and `should_run_linker()` helper in registry
- **Rich metadata extraction (ADR-0003 Phase 1-5):** Analyzers now capture rich metadata:
  - **Python:** Full decorator info with args/kwargs, base classes, and structured parameters
  - **JavaScript/TypeScript:** Decorator extraction with arguments (e.g., `@Controller('/users')` → `{"name": "Controller", "args": ["/users"], "kwargs": {}}`), base class extraction from `extends`/`implements` clauses including generic types (e.g., `extends Repository<User>` → `["Repository<User>"]`)
  - **Java:** Full annotation info with args/kwargs (e.g., `@Table(name = "users")` → `{"name": "Table", "args": [], "kwargs": {"name": "users"}}`), base class extraction from `extends`/`implements` clauses including generic types. Supports integer, float, boolean, string, and array annotation values.
- This metadata enables the future FRAMEWORK_PATTERNS phase for semantic entry detection per ADR-0003.
- **FRAMEWORK_PATTERNS phase (ADR-0003 Items 6-8):** Data-driven symbol enrichment:
  - New `framework_patterns.py` module with `Pattern`, `FrameworkPatternDef` dataclasses
  - Framework patterns defined in YAML files (`src/hypergumbo/frameworks/*.yaml`)
  - `enrich_symbols()` matches patterns against symbol metadata and adds concept annotations
  - Symbols gain `meta.concepts` list with framework-specific semantics (route, model, dependency, etc.)
  - FastAPI patterns YAML (`fastapi.yaml`) with patterns for:
    - Route decorators: `@app.get("/path")`, `@router.post("/path")`, etc.
    - Pydantic models: `class Item(BaseModel)`
    - Dependency injection: `Depends(get_db)`, `BackgroundTasks`
  - Flask patterns YAML (`flask.yaml`) with patterns for:
    - Flask 2.0+ route shortcuts: `@app.get("/path")`, `@bp.post("/path")`
    - Classic routes: `@app.route("/path", methods=["GET"])`
    - Blueprint routes: `@blueprint.route("/path")`
    - Request hooks: `@app.before_request`, `@app.after_request`, `@app.teardown_request`
    - Error handlers: `@app.errorhandler(404)`
    - Flask-RESTful Resources: `class UserResource(Resource)`
    - Flask-WTF forms: `class LoginForm(FlaskForm)`
    - Flask-SQLAlchemy models: `class User(db.Model)`
  - NestJS patterns YAML (`nestjs.yaml`) with patterns for:
    - HTTP route decorators: `@Get()`, `@Post()`, `@Put()`, `@Delete()`, etc. with path extraction
    - Controllers: `@Controller('users')`
    - Services: `@Injectable()`
    - Modules: `@Module()`
    - Guards: `@UseGuards(AuthGuard)`
    - Interceptors: `@UseInterceptors(LoggingInterceptor)`
    - Pipes: `@UsePipes(ValidationPipe)`
    - Microservice event handlers: `@EventPattern()`, `@MessagePattern()`
    - WebSocket gateway: `@WebSocketGateway()`, `@SubscribeMessage()`
  - Spring Boot patterns YAML (`spring.yaml`) with patterns for:
    - HTTP route annotations: `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping` with path extraction
    - Generic mapping: `@RequestMapping`
    - Controllers: `@RestController`, `@Controller`
    - Services: `@Service`
    - Repositories: `@Repository`
    - Components: `@Component`
    - Configuration: `@Configuration`, `@Bean`
    - Entity models: `@Entity`, `@Table`
    - Exception handlers: `@ExceptionHandler`
    - Scheduled tasks: `@Scheduled`
    - Event listeners: `@EventListener`
    - Async methods: `@Async`
  - Django patterns YAML (`django.yaml`) with patterns for:
    - Django REST Framework: `@api_view(['GET', 'POST'])` route decorator
    - DRF class-based views: `APIView`, `ViewSet`, `ModelViewSet`, `GenericViewSet`
    - DRF serializers: `Serializer`, `ModelSerializer`, `HyperlinkedModelSerializer`
    - Django generic views: `View`, `TemplateView`, `ListView`, `DetailView`, `CreateView`, `UpdateView`, `DeleteView`, etc.
    - Django models: `Model` base class
    - Django forms: `Form`, `ModelForm` base classes
    - Django admin: `@admin.register`, `ModelAdmin`, `TabularInline`, `StackedInline`
    - Django signals: `@receiver` decorator
    - Django management commands: `BaseCommand`
    - Django middleware: `MiddlewareMixin`
    - DRF permissions: `BasePermission`, `IsAuthenticated`, `IsAdminUser`, `AllowAny`
    - Django filters (django-filter): `FilterSet`, `Filter`
    - Celery tasks: `@shared_task`, `@task`, `@celery.task`
  - Express.js patterns YAML (`express.yaml`) with patterns for:
    - Route handlers: `app.get()`, `app.post()`, `app.put()`, `app.delete()`, `app.patch()`, etc.
    - Router patterns: `router.get()`, `router.post()`, etc.
    - Router route chaining: `router.route('/path')`
    - Middleware: `app.use()`, `router.use()`
    - Param middleware: `app.param()`, `router.param()`
    - Passport.js strategies: `LocalStrategy`, `JwtStrategy`, `OAuth2Strategy`, etc.
    - Common middleware: `helmet`, `cors`, `morgan`, `bodyParser`, etc.
  - Celery patterns YAML (`celery.yaml`) with patterns for:
    - Task decorators: `@shared_task`, `@task`, `@app.task`, `@celery.task`
    - Periodic tasks: `@periodic_task` (Celery Beat)
    - Task signals: `@task_prerun.connect`, `@task_success.connect`, `@task_failure.connect`, etc.
    - Worker signals: `@worker_ready.connect`, `@worker_init.connect`, etc.
    - Custom Task base classes
  - Ruby on Rails patterns YAML (`rails.yaml`) with patterns for:
    - Controllers: `ApplicationController`, `ActionController::Base`, `ActionController::API`
    - Models: `ApplicationRecord`, `ActiveRecord::Base`
    - Jobs: `ApplicationJob`, `ActiveJob::Base`
    - Mailers: `ApplicationMailer`, `ActionMailer::Base`
    - Channels (Action Cable): `ApplicationCable::Channel`, `ActionCable::Channel::Base`
    - Serializers: `ActiveModel::Serializer`, `ApplicationSerializer`
    - Pundit policies: `ApplicationPolicy`
    - Draper decorators: `Draper::Decorator`, `ApplicationDecorator`
    - Sidekiq workers: `Sidekiq::Worker`
  - Phoenix (Elixir) patterns YAML (`phoenix.yaml`) with patterns for:
    - Controllers: `Phoenix.Controller`, `MyAppWeb :controller`
    - LiveView: `Phoenix.LiveView`, `Phoenix.LiveComponent`
    - Channels: `Phoenix.Channel` (WebSocket handlers)
    - Ecto models: `Ecto.Schema`
    - Plugs: `Plug.Builder`, `Plug.Router` (middleware)
    - GenServer/Agent/Task: Elixir OTP patterns
    - Oban workers: Background job processing
    - Absinthe: GraphQL schema and resolvers
  - Laravel (PHP) patterns YAML (`laravel.yaml`) with patterns for:
    - Controllers: `Controller` base class
    - Eloquent models: `Model` base class
    - Form requests: `FormRequest` validation
    - Mailables: `Mailable` for emails
    - Artisan commands: `Command` for CLI
    - JSON resources: `JsonResource`, `ResourceCollection`
    - Service providers: `ServiceProvider`
    - Notifications: `Notification`
    - Livewire components: `Livewire\Component`
    - Jobs, events, listeners, policies, seeders
  - Rust web framework patterns YAML (`rust-web.yaml`) with patterns for:
    - Actix-web routes: `#[actix_web::get("/path")]`, `#[actix_web::post("/path")]`, etc.
    - Rocket routes: `#[rocket::get("/path")]`, `#[rocket::post("/path")]`, etc.
    - Diesel ORM models: `#[derive(Queryable)]`, `#[derive(Insertable)]`
    - SeaORM models: `#[derive(DeriveEntityModel)]`
    - Tokio async tasks: `tokio::spawn`
  - Go web framework patterns YAML (`go-web.yaml`) with patterns for:
    - Gin routes: `router.GET()`, `r.POST()`, etc.
    - Echo routes: `e.GET()`, `echo.POST()`, etc.
    - Fiber routes: `app.Get()`, `fiber.Post()`, etc.
    - Chi routes: `r.Get()`, `mux.Delete()`, etc.
    - net/http: `http.HandleFunc()`
    - Middleware: `router.Use()`, `e.Use()`, etc.
    - GORM models: `gorm.Model` embedding
    - gRPC service and client definitions
  - Hapi.js patterns YAML (`hapi.yaml`) with patterns for:
    - Server routes: `server.route({ method, path, handler })`
    - Plugin registration: `server.register()`
    - Extension points: `server.ext()`
    - Auth strategies: `server.auth.strategy()`
    - Joi validation: `Joi.object()`, `Joi.string()`, etc.
    - Boom error responses: `Boom.badRequest()`, `Boom.unauthorized()`, etc.
  - Koa.js patterns YAML (`koa.yaml`) with patterns for:
    - Router routes: `router.get()`, `router.post()`, etc. (via koa-router/@koa/router)
    - Global middleware: `app.use()`
    - Router middleware: `router.use()`, `router.param()`
    - Passport auth: `passport.authenticate()`, `passport.use()`
    - JWT middleware: `jwt()`
    - Common middleware: `helmet`, `cors`, `logger`, `compress`
  - ASP.NET Core patterns YAML (`aspnet.yaml`) with patterns for:
    - HTTP route attributes: `[HttpGet]`, `[HttpPost]`, `[HttpPut]`, `[HttpDelete]`, `[HttpPatch]`
    - Route attribute: `[Route("path")]`
    - Controllers: `[ApiController]`, `[Controller]`
    - Authorization: `[Authorize]`, `[AllowAnonymous]`
    - Validation: `[Required]`, `[StringLength]`, `[Range]`, etc.
    - Entity Framework: `[Table]`, `[Key]`, `[Column]`, etc.
    - SignalR hubs via base class: `Hub`
  - **C# analyzer annotation extraction:** C# analyzer now captures full attribute metadata:
    - Extracts all C# attributes (not just HTTP routes) into `meta.annotations`
    - Supports positional arguments: `[Route("/users")]` → `args: ["/users"]`
    - Supports named arguments: `[Route("path", Name = "GetUsers")]` → `kwargs: {"Name": "GetUsers"}`
    - Supports qualified names: `[System.Serializable]` → `name: "System.Serializable"`
    - Class-level attribute extraction for `[ApiController]`, `[Authorize]`, etc.
    - Enables FRAMEWORK_PATTERNS phase for ASP.NET Core semantic detection
  - New extraction methods for Java annotations:
    - `annotation_prefix`: Extracts HTTP method from regex capture group (e.g., `@GetMapping` → `GET`)
    - `annotation_name_upper`: Uses annotation name directly as method
  - New extraction method `decorator_name_upper` for NestJS-style decorators where the decorator name IS the HTTP method
  - Pattern matching extracts HTTP method from decorator suffix and path from args
- **Linker conditional execution (ADR-0003 Item 10):** Linkers now respect activation conditions:
  - `run_all_linkers()` filters linkers based on detected frameworks and languages
  - LinkerContext extended with `detected_frameworks` and `detected_languages` fields
  - gRPC linker only runs when grpc/protobuf framework detected
  - GraphQL linker only runs when graphql framework detected
  - Phoenix IPC linker only runs when phoenix framework detected
  - JNI linker only runs when both Java and C/C++ present
  - Swift-ObjC linker only runs when both Swift and Objective-C present
  - Protocol linkers (HTTP, WebSocket, MQ) always run (user-controlled opt-out via `--linkers`)
- **HTTP linker concept metadata support:** HTTP linker now extracts route info from concept metadata:
  - Prefers `meta.concepts[].path` and `meta.concepts[].method` (FRAMEWORK_PATTERNS phase)
  - Falls back to legacy `meta.route_path` and `meta.http_method` when no route concept
  - Enables route linking for symbols enriched by framework pattern YAML files
- **Routes command concept metadata support:** The `hypergumbo routes` command now detects routes via:
  - Concept metadata (`meta.concepts[].concept == "route"`) from FRAMEWORK_PATTERNS phase
  - Falls back to legacy HTTP method stable_id detection
- **Semantic entry detection (ADR-0003 Item 11):** Entrypoint detection now uses enriched metadata:
  - New `_detect_from_concepts()` function checks `meta.concepts` for route concepts
  - Semantic detection has highest priority (runs before path-based heuristics)
  - High confidence (0.95) since based on actual decorator/pattern matching
  - Eliminates false positives (e.g., React Router files without route concepts won't be flagged)
  - Fallback to path-based heuristics for frameworks without YAML patterns

### Changed
- **Python analyzer purified (ADR-0003 Item 9):** Route detection removed from Python analyzer:
  - Route detection now happens in FRAMEWORK_PATTERNS phase via YAML pattern files
  - Analyzer continues to extract rich decorator metadata (`meta.decorators`) for pattern matching
  - Removed `_detect_route_decorator()`, `_extract_router_prefixes()`, `_combine_prefix_and_path()` functions
  - All functions now use hash-based `stable_id` (route methods no longer set as stable_id)
  - Router prefix combination removed (can be implemented in FRAMEWORK_PATTERNS later)
  - Django CBV HTTP method detection retained (method name based, not decorator-based)
  - Django URL pattern detection retained (different detection mechanism)

### Deprecated
- **Packs (ADR-0003 Item 5):** The `Pack` and `PackConfig` classes now emit deprecation warnings. Framework-specific analysis is now handled by linker activation conditions rather than packs. Existing code using packs will continue to work but should be migrated to use the new `--frameworks` flag instead.
- **Path-based entrypoint heuristics (ADR-0003 Item 13):** All path-based entrypoint detection functions now emit deprecation warnings:
  - Detection based on file paths/naming patterns (e.g., `routes/`, `*Controller.ts`) is deprecated
  - Prefer semantic detection via concept metadata from FRAMEWORK_PATTERNS phase
  - Warnings fire once per framework per session to avoid spam
  - Non-deprecated methods: semantic detection (`_detect_from_concepts`), decorator detection (`_detect_http_routes`), CLI detection (`_detect_cli_entrypoints`)
  - Migration path: Create framework YAML pattern files and use `--frameworks` flag
- **Analyzer-level route detection (ADR-0003 v1.0.x):** All analyzer-level framework route detection now emits deprecation warnings:
  - **Java:** Spring Boot (`@GetMapping`, `@PostMapping`, etc.) and JAX-RS (`@GET`, `@POST`, etc.)
  - **Python:** Django URL patterns (`path()`, `re_path()`, `url()`)
  - **C#:** ASP.NET Core attributes (`[HttpGet]`, `[HttpPost]`, etc.)
  - **Rust:** Axum (`.route("/path", get(handler))`) and Actix-web (`#[get("/path")]`)
  - **Ruby:** Rails route DSL (`get`, `post`, `resources`)
  - **PHP:** Laravel routes (`Route::get()`, `Route::post()`, etc.)
  - **JavaScript/TypeScript:** Express-style routes (`app.get()`, `router.post()`, etc.)
  - Warnings fire once per framework per session to avoid spam
  - Migration path: Use framework YAML patterns (--frameworks) for semantic detection

## [0.6.9] - 2026-01-07

### Added
- **Connectivity-aware auto-slicing:** `hypergumbo slice --entry auto` now prefers well-connected entrypoints (confidence weighted by outgoing edges) for richer, more useful slices.
- **Improved slice traversal across linker boundaries:** synthetic linker nodes (e.g., gRPC stubs, MQ publishers, websocket endpoints) are now connected to their enclosing function/method via `uses` edges, powered by a new `find_enclosing_symbol()` helper.
- **Python module pseudo-nodes:** script-style files with module-level executable code now get `<module:...>` nodes so linkers can attach synthetic nodes even when no enclosing function exists.
- **Stronger cross-file call resolution + lightweight type inference:** analyzers for Python, JS/TS, Java, and Kotlin gained module-qualified call resolution and constructor-only type inference to better resolve calls like `module.func()`, `alias.Class()`, `ClassName.method()`, and `var.method()` when `var` is assigned from `new ClassName(...)` (or equivalent).
- **Linker diagnostics + registry pattern:** introduced `LinkerRequirement`/requirement checks and expanded the `@register_linker` + `run_all_linkers()` registry flow for standardized linker execution and clearer “why no edges?” diagnostics.
- **Variable-based linker matching:** HTTP and event-sourcing linkers now detect URLs/event names stored in variables (lower confidence than literals) and annotate matches with `url_type` / `event_type`.
- **Symbol modifiers metadata:** symbols now include a `modifiers` field (e.g., `public`, `static`, `native`), with Java extraction implemented.
- **Entrypoint language filtering:** CLI entrypoint detection now avoids shader-language `main` false positives (GLSL/HLSL/WGSL).
- **Tooling:** `find-uncovered` now auto-runs tests when needed, warns on stale coverage, and uses a visible `coverage-report.txt` cache.

### Fixed
- **JavaScript/TypeScript route detection false positives (ADR-0003):** Fixed `fetchMock.get()`, `axios.post()`, and similar test/HTTP client calls being incorrectly detected as Express routes. Route detection now validates that the receiver is a known router object (`app`, `router`, `express`, `server`, `fastify`, `koa`) before matching HTTP method calls. This eliminates false positives from test mocking libraries like `fetch-mock` and HTTP clients.
- **Entrypoint false positives reduced across frameworks:**
  - Excluded `.tsx`/`.jsx` React file-routing paths from Express/Hapi/Koa route detection.
  - Avoided Tornado "handler" false positives by excluding common non-web handler filename patterns.
  - Tightened GraphQL server detection by excluding `.tsx`/`.jsx` and common non-resolver "*resolver*" filename patterns (e.g., DNS/promise/dependency/path/module resolvers).
  - Avoided Micronaut HTTP false positives by excluding common gRPC/RPC client class naming patterns.

### Changed
- **Linker implementation consolidation:** migrated remaining linkers to the registry-based `@register_linker` approach and removed legacy explicit linker calls from `cli.py`, enabling dynamic linker discovery and simpler orchestration.

### Documentation
- Updated the spec’s section 8.7 to align the migration guidance with ADR-0003 (plan for a significant refactoring) and replaced the prior ContentVerifier API proposal with a semantic entry detection direction.


## [0.6.0] - 2025-12-29

### Added
- Lean 4 analyzer (theorem prover support)
- Wolfram Language analyzer (Mathematica support)
- Agda analyzer (dependently typed proof assistant)
- `scripts/build-source-grammars` for building experimental tree-sitter grammars
- `scripts/contribute` for fork-based contributor workflow
- `docs/EXPERIMENTAL_GRAMMARS.md` wishlist of domain-specific languages
- `docs/GOVERNANCE.md` contributor trust model and release policies
- `docs/MAINTAINER_AGENT_SPEC.md` specification for automated PR processing
- Release automation: `scripts/release-check`, `scripts/release`, `scripts/integration-test`, `scripts/bump-version`
- Extended release CI workflow with multi-Python/multi-platform testing
- Contributor Mode documentation in AGENTS.md
- Sketch improvements: two-phase symbol selection, per-file render compression, entrypoint preservation
- Deterministic sketch output (sorted SOURCE_DIRS iteration)
- Conservative token estimation using ceiling division

**From STATUS.md (development tracking):**
- **Python** (AST): function, class, method, route. Two-pass cross-file resolution. Detects `self.method()`, `ClassName()` instantiation. Methods named with class prefix (`ClassName.methodName`). **Metrics:** `cyclomatic_complexity` (McCabe: decision points + 1) and `lines_of_code` computed per symbol. **src/ layout detection:** Automatically detects PEP 517/518 `src/` layout projects and adjusts module name derivation (e.g., `src/flask/app.py` → `flask.app` instead of `src.flask.app`) for correct cross-file import resolution. **Route detection:** FastAPI (`@app.get`, `@router.post`), Flask (`@app.route`, `@app.get`), Django REST Framework (`@api_view(['GET', 'POST'])`), Django CBV methods (get/post/put/patch/delete), and Django URL patterns (`path()`, `re_path()`, `url()`) set `stable_id` to HTTP method for `routes` command discovery. **Router prefix detection:** `APIRouter(prefix='/api/v1')` and `Blueprint(url_prefix='/api')` prefixes are combined with route paths.
- **Lean** (tree-sitter): function, theorem, structure, inductive, class, instance. Lean 4 theorem prover. Detects defs, theorems, lemmas, structures, inductive types, classes, instances. Two-pass cross-file resolution. Tested on Mathematics in Lean (379 symbols). Built from source via `scripts/build-source-grammars`.
- **Wolfram** (tree-sitter): function, variable. Wolfram Language (Mathematica). Detects SetDelayed (:=) function definitions, Set (=) assignments, function calls, Get/Needs/Import statements. Two-pass cross-file resolution. Built from source via `scripts/build-source-grammars`.
- **Agda** (tree-sitter): module, function, data, record. Dependently typed proof assistant. Detects modules, functions (including theorems/lemmas), data types, records, postulates. Two-pass cross-file resolution. Tested on agda-stdlib (18,949 symbols) and PLFA (6,014 symbols). Optional: `pip install tree-sitter-agda`
- **COBOL** (tree-sitter): program, paragraph, section, data. Detects COBOL programs: PROGRAM-ID declarations, paragraphs, sections in PROCEDURE DIVISION, data items in DATA DIVISION with level numbers. PERFORM edges for paragraph calls, CALL edges for external program calls. For mainframe and legacy systems. Optional: `pip install tree-sitter-language-pack`
- **LaTeX** (tree-sitter): section, label, command, environment. Detects LaTeX documents: sections/chapters, labels, custom commands (\\newcommand), custom environments (\\newenvironment). Reference edges for \\ref/\\cite, include edges for \\input/\\include, import edges for \\usepackage. For academic and technical documentation. Optional: `pip install tree-sitter-language-pack`
- **Dart** (tree-sitter): class, function, method, constructor, getter, setter, enum, mixin, extension. Detects Dart code: classes, functions, methods (including getters/setters), constructors, enums, mixins, extensions, import statements. For Flutter and Dart web/server development. Optional: `pip install tree-sitter-language-pack`
- **JavaScript** (tree-sitter): function, class, method, getter, setter, route. Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) handlers set `stable_id` to HTTP method. **Express.js enhancements:** Wrapper patterns (`catchAsync(handler)`), external handlers (`userController.create`), and chained syntax (`router.route('/').get(handler)`) all detected. Optional: `pip install hypergumbo[javascript]`
- **TypeScript** (tree-sitter): function, class, method, getter, setter, interface, type, enum, route. Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) and NestJS decorators (`@Get()`, `@Post()`) set `stable_id` to HTTP method. **Express.js enhancements:** Wrapper patterns (`catchAsync(handler)`), external handlers (`userController.create`), and chained syntax (`router.route('/').get(handler)`) all detected. Optional: `pip install hypergumbo[javascript]`
- `hypergumbo explain <symbol>`: Show symbol details with callers/callees, complexity, LOC
- `-e/--exclude`: Custom exclude patterns for `sketch` and `run` (repeatable)
- `hypergumbo build-grammars`: Build Lean/Wolfram grammars from source (tree-sitter)
- `hypergumbo run [path] [-x]`: Run analysis. Supports `-x/--exclude-tests` to filter test files
- Tag-triggered releases: Push `v*` tag to trigger release
- Manual dispatch: Workflow dispatch with version + dry_run inputs
- Dry run mode: Skip PyPI publish and Forgejo release creation
- Python 3.10: Minimum supported version
- Python 3.11
- Python 3.12
- Python 3.13: Latest Python version
- pip-audit: Dependency vulnerability scanning (`--skip-editable`)
- Bandit: Security linting
- Safety: Advisory check (non-blocking)
- pip-licenses: License audit, warns on copyleft
- trufflehog: Secret scanning
- Quick mode: `./scripts/integration-test --quick`
- Real repo testing: Express, Gin, Flask
- Wheel build: `python -m build`
- Source distribution: Included in build
- SHA256 checksums: `dist/SHA256SUMS`
- SBOM generation: CycloneDX format (`dist/sbom.json`)
- Wheel verification: `pip install --dry-run` + `twine check`
- PyPI publish: Via twine with `PYPI_TOKEN` secret
- Forgejo release: Via API with `FORGEJO_TOKEN` secret
- Changelog extraction: Auto-extracts version section for release notes
- Asset upload: Wheel, tarball, checksums, SBOM
- Release SOP: `docs/RELEASE_SOP.md`
- Pytest warning filters: `pyproject.toml` filters expected test warnings (tree-sitter unavailability from mocked tests, API deprecations).
- Source-only grammar builds: `./scripts/build-source-grammars` builds tree-sitter-lean and tree-sitter-wolfram from source in CI. Adds ~30s to CI time.
- Test escape hatch removal: ADR 0002: Tests no longer skip when dependencies unavailable. All tree-sitter packages listed in `pyproject.toml`.
- CI debugging tools: `./scripts/ci-debug` for Forgejo Actions troubleshooting. Commands: `runs`, `status`, `analyze-deps`.
- Quality filtering: Excludes non-code kinds (dependency, devDependency, file, target, special_target, project, package, script, event_subscriber, class_selector, id_selector) and test/example paths
- Test path filtering: Excludes test files: `/tests/`, `_test.go`, `.spec.ts`, `/testFixtures/`, `/intTest/`, `Tests.java`, etc.
- Example path filtering: Excludes example/demo code: `/examples/`, `/demos/`, `/samples/`, `/playground/`, `/tutorial/`
- Name deduplication: Prevents duplicate symbol names in tiers (e.g., multiple `push` methods)
- `--compact` flag on `run`: Coverage-based truncation with bag-of-words summarization
- `--coverage` parameter: Target centrality coverage (0.0-1.0, default: 0.8)
- `nodes_summary` in output: Included count/coverage + omitted word frequencies, path patterns, kinds
- Default tiered files: Automatically generates `.4k.json`, `.16k.json`, `.64k.json` alongside full output
- `--tiers` flag: Custom tier specs (e.g., `"2k,8k,32k"`)
- `--tiers none`: Disable tiered output generation
- `--tiers default`: Explicit default tiers (4k, 16k, 64k)
- Token estimation: ~4 chars/token approximation for JSON
- Centrality-based selection: Most important symbols selected first per budget
- Tiered view format: `view: "tiered"`, `tier_tokens`, `nodes_summary` with included/omitted
- Directory structure: Top-level dirs with type labels. Filters excluded dirs (node_modules, __pycache__, etc.)
- Framework detection: Via profile.py. **Python:** FastAPI, Flask, Django, Starlette, Quart, Sanic, Litestar, Falcon, Bottle, CherryPy, Pyramid, Tornado, Aiohttp, PyTorch, TensorFlow, Keras, JAX, Transformers, spaCy, NLTK, LangChain, LangGraph, LlamaIndex, Haystack, scikit-learn, XGBoost, LightGBM, CatBoost, Optuna, MLflow, WandB, Ray, vLLM, DeepSpeed, PaddlePaddle, OpenAI, Anthropic. **JavaScript/TypeScript:** React, Vue, Angular, Svelte, Solid, Qwik, Preact, Lit, Alpine, htmx, Ember, Next.js, Nuxt, Remix, Astro, Gatsby, SvelteKit, Express, NestJS, Fastify, Koa, Hapi, Adonis, Sails, Hono, Elysia, React Native, Expo, Ionic, Capacitor, NativeScript, Electron, Tauri, Hardhat, Web3.js, ethers.js, Wagmi, Viem. **Rust:** Axum, Actix-web, Rocket, Warp, Tide, Gotham, Poem, Salvo, Tokio, async-std, Serde, Clap, Tauri, Solana/Anchor, Substrate, CosmWasm, ethers-rs, Alloy, Foundry, REVM, Arkworks, Bellman, Halo2, Plonky2/3, SP1, RISC Zero, Jolt, Nova, HyperNova, Zcash, libp2p, curve25519/ed25519, secp256k1. **Go:** Gin, Echo, Fiber, Chi, Gorilla, Buffalo, Revel, Beego, Iris. **PHP:** Laravel, Symfony, CodeIgniter, CakePHP, Yii, Phalcon, Slim. **Java/Kotlin:** Spring Boot, Micronaut, Quarkus, Dropwizard, Vert.x, Javalin, Helidon, Spark, Ktor, Jetpack Compose. **Swift:** Vapor, Kitura, Perfect, SwiftUI. **Scala:** Play, Akka HTTP, http4s, ZIO HTTP, Finatra. **Dart/Flutter:** Flutter SDK, flutter_bloc, Riverpod, Provider, GetX, MobX, Dio, Freezed, go_router, Flame.
- `nodes[]` with span, stable_id, shape_id: Includes `cyclomatic_complexity` and `lines_of_code` for Python symbols
- LLM-assisted plan generation: `llm_assist.py` - OpenRouter, OpenAI, llm package backends. Interactive setup prompts if no API key configured. Keys stored in `~/.config/hypergumbo/config.json`. *Proof-of-concept; template-based generation currently produces equivalent results.*
- Entrypoint detection heuristics: `entrypoints.py` - FastAPI, Flask, Click, Electron, Django, Express.js, NestJS, Spring Boot, Rails, Phoenix, Go (Gin/Echo/Fiber), Laravel, Rust (Actix-web/Axum/Rocket/Warp), ASP.NET Core, Sinatra, Ktor, Vapor, Plug, Hapi, Fastify, Koa, Grape, Tornado, Aiohttp, Slim, Micronaut, Flutter (runApp, widgets), GraphQL (Apollo Server, Yoga, Mercurius). Test files excluded via `_is_test_file()` helper.

### Changed
- CI now builds tree-sitter-lean and tree-sitter-wolfram from source (~30s overhead)
- Test files use real parsing instead of mocks where grammars are available

## [0.5.0] - 2025-12-26

Initial public release with comprehensive static analysis capabilities.

### Core Features
- **Sketch generation**: Token-budgeted Markdown overview of any codebase
- **Full analysis**: JSON behavior map with symbols, edges, and provenance
- **Slice extraction**: BFS/DFS subgraph extraction from entry points
- **Route discovery**: HTTP route listing for web frameworks
- **Symbol search**: Pattern-based symbol search

### Language Analyzers
- **Java** (tree-sitter): class, interface, enum, method, constructor. Two-pass cross-file resolution. Detects `this.method()`, `ClassName.method()`, inheritance, `new ClassName()`. Native method detection with `meta.is_native`. **Route detection:** Spring Boot (`@GetMapping`, `@PostMapping`, `@RequestMapping`) sets `stable_id` to HTTP method for `routes` command discovery. Optional: `pip install hypergumbo[java]`
- **Rust** (tree-sitter): function, struct, enum, trait, method, route. Detects `fn`, `struct`, `enum`, `trait`, `impl` blocks, `use` statements. **Route detection:** Axum `.route("/path", get(handler))` with method chaining, Actix-web/Rocket `#[get("/path")]` attribute macros (handles multi-param attributes). Route symbols have `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[rust]`
- **Go** (tree-sitter): function, method, struct, interface, type, route. Detects `func`, methods with receivers, `type X struct/interface`, `import` statements. **Route detection:** Gin/Echo (`r.GET`, `e.POST`), Fiber (`app.Get`, `app.Post`) with lowercase methods. Route symbols have `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[go]`
- **WGSL** (tree-sitter): function, struct, uniform, storage. Detects WebGPU shaders: entry points (@vertex, @fragment, @compute), structs, uniform/storage bindings with @group/@binding metadata. For WebGPU graphics and compute analysis. Optional: `pip install tree-sitter-language-pack`
- **XML** (tree-sitter): module, dependency, activity, service, permission. Maven pom.xml: projects, dependencies with groupId/artifactId/version. Android Manifest: activities, services, receivers, providers, permissions, intent-filters. For Java/Android analysis. Optional: `pip install tree-sitter-language-pack`
- **JSON** (tree-sitter): package, dependency, devDependency, script, tsconfig, reference, composer_package. package.json: npm dependencies, scripts. tsconfig.json: TypeScript project references. composer.json: PHP Composer dependencies. For Node.js/PHP analysis. Optional: `pip install tree-sitter-language-pack`
- **R** (tree-sitter): function, import, source. Detects R code: function definitions, library/require imports, source() file references. Function call edges. For data science and statistical computing. Optional: `pip install tree-sitter-language-pack`
- **Ruby** (tree-sitter): method, class, module, route. Detects `def`, `class`, `module`, `require/require_relative`. **Route detection:** Rails DSL (`get '/path'`, `post '/path'`, `resources :name`) creates route symbols with `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[ruby]`
- **TOML** (tree-sitter): table, package, dependency, binary, test, example, benchmark, library, workspace, project. Detects TOML configuration files: Cargo.toml (dependencies, bins, tests, examples, benches, libs, workspaces), pyproject.toml (project metadata). For Rust and Python project analysis. Optional: `pip install tree-sitter-toml`
- **CSS** (tree-sitter): import, variable, keyframes, media, font_face. Detects CSS stylesheets: @import statements with import edges, CSS variables (--custom-props), @keyframes animations, @media queries, @font-face declarations. For frontend styling analysis. Optional: `pip install tree-sitter-css`
- **VHDL** (tree-sitter): entity, architecture, package, component. Detects VHDL hardware designs: entities, architectures, packages, component declarations. Architecture-entity relationships. Optional: `pip install tree-sitter-vhdl`
- **GraphQL** (tree-sitter): type, input, interface, enum, scalar, union, directive, fragment, query, mutation, subscription. Detects GraphQL schema definitions: object types, input types, interfaces, enums, scalars, unions, directives, fragments, operations. API schema analysis. Optional: `pip install tree-sitter-graphql`
- **Nix** (tree-sitter): function, binding, input, derivation. Detects Nix expressions: named functions, let bindings, flake inputs, derivation calls. Import edges for `import` expressions. Optional: `pip install tree-sitter-nix`
- **GLSL** (tree-sitter): function, struct, uniform, input, output. Detects OpenGL shaders: functions, structs, uniform/in/out variables. Function call edges. Supports .vert, .frag, .glsl, .geom, .tesc, .tese, .comp files. Optional: `pip install tree-sitter-glsl`
- **Fortran** (tree-sitter): module, program, function, subroutine, type. Detects Fortran code: modules, programs, functions, subroutines, derived types. Use statement imports, subroutine call edges. For scientific computing and HPC. Optional: `pip install tree-sitter-fortran`
- **SQL** (tree-sitter): table, view, function, trigger, index, procedure. Detects CREATE TABLE, VIEW, FUNCTION, TRIGGER, INDEX statements. Foreign key REFERENCES edges. Two-pass cross-file resolution. Optional: `pip install tree-sitter-sql`
- **Dockerfile** (tree-sitter): stage, exposed_port, env_var, build_arg. Detects FROM stages, EXPOSE ports, ENV variables, ARG build args. Multi-stage build dependencies via COPY --from edges. Optional: `pip install tree-sitter-dockerfile`
- **CUDA** (tree-sitter): kernel, device_function, host_device_function, function. Detects `__global__` kernels, `__device__` functions, `__host__ __device__` dual functions. Kernel launch edges for `<<<grid, block>>>` syntax. Optional: `pip install tree-sitter-cuda`
- **Verilog** (tree-sitter): module, interface. Detects Verilog/SystemVerilog modules, interfaces, module instantiations. Cross-file module resolution. Optional: `pip install tree-sitter-verilog`
- **CMake** (tree-sitter): project, library, executable, function, macro, package, subdirectory. Detects CMake projects, add_library/add_executable targets, function/macro definitions, find_package, add_subdirectory. Target link dependencies. Optional: `pip install tree-sitter-cmake`
- **Make** (tree-sitter): variable, target, pattern_rule, special_target, function, include. Detects Makefiles: variables, targets, pattern rules, .PHONY, define blocks, include directives. Prerequisite dependencies. Optional: `pip install tree-sitter-make`
- **YAML/Ansible** (tree-sitter): playbook, task, handler, variable. Detects Ansible playbooks, tasks, handlers, variables from YAML files. Extracts `include_tasks`, `import_tasks`, `include_role`, `import_role` references. Two-pass cross-file resolution. Optional: `pip install tree-sitter-yaml`
- **Bash** (tree-sitter): function, export, alias. Detects functions (both `function name()` and `name()` styles), exported variables, aliases, source/dot statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-bash`
- **Objective-C** (tree-sitter): class, protocol, method, property. Detects `@interface`, `@implementation`, `@protocol`, methods (`-`/`+`), properties. Message send call resolution `[receiver message]`. Two-pass cross-file resolution. Optional: `pip install tree-sitter-objc`
- **HCL/Terraform** (tree-sitter): resource, data, variable, output, module, provider, local. Detects Terraform blocks, variable references, resource dependencies, module sources. Two-pass cross-file resolution. Optional: `pip install tree-sitter-hcl`
- **Groovy** (tree-sitter): class, interface, enum, method, function. Detects classes, interfaces, enums, methods, top-level functions (`def`), import statements. Handles `.gradle` build files. Two-pass cross-file resolution. Optional: `pip install tree-sitter-groovy`
- **Julia** (tree-sitter): module, function, struct, abstract, macro, const. Detects modules, functions (full and short-form), structs, abstract types, macros, constants, import/using statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-julia`
- **Zig** (tree-sitter): function, struct, enum, union, error_set, method, test. Detects `fn`, `struct`, `enum`, `union`, `error` sets, `test` blocks, `@import()` statements. Methods distinguished by `self` parameter. Two-pass cross-file resolution. Optional: `pip install tree-sitter-zig`
- **Solidity** (tree-sitter): contract, interface, library, function, constructor, modifier, event. Ethereum smart contracts. Detects contracts, interfaces, libraries, functions, constructors, modifiers, events, and import statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-solidity`
- **C#** (tree-sitter): class, interface, struct, enum, method, constructor, property. Two-pass cross-file resolution. Detects method calls, `using` directives, `new ClassName()`. Optional: `pip install hypergumbo[csharp]`
- **C++** (tree-sitter): class, struct, enum, function, method. Two-pass cross-file resolution. Detects function/method calls, `#include` directives, `new ClassName()`. Handles qualified names (Namespace::Class::method). Optional: `pip install hypergumbo[cpp]`
- **OCaml** (tree-sitter): function, type, module. Detects let bindings (functions), types, modules, `open` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[ocaml]`
- **Scala** (tree-sitter): function, class, object, trait, method. Detects `def`, `class`, `object`, `trait`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[scala]`
- **Lua** (tree-sitter): function, method. Detects `function`, `local function`, method-style `Table:method()`, `require()` imports. Two-pass cross-file resolution. Optional: `pip install hypergumbo[lua]`
- **Haskell** (tree-sitter): function, data, class, instance. Detects functions (with/without type signatures), data types, type classes, instances, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[haskell]`
- **Kotlin** (tree-sitter): function, class, object, interface, method. Detects `fun`, `class`, `object`, `interface`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[kotlin]`
- **Swift** (tree-sitter): function, class, struct, protocol, enum, method. Detects `func`, `class`, `struct`, `protocol`, `enum`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[swift]`
- **Vue** (tree-sitter): function, class, method. Extracts `<script>` and `<script setup>` blocks from `.vue` SFCs, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]`
- **Elixir** (tree-sitter): module, function, macro. Detects `def/defp`, `defmodule`, `use/import/alias`. Two-pass cross-file resolution. Optional: `pip install hypergumbo[elixir]`
- **C** (tree-sitter): function, struct, enum, typedef. Two-pass cross-file resolution. Detects function calls, JNI export patterns (`Java_ClassName_methodName`). Optional: `pip install hypergumbo[c]`
- **Svelte** (tree-sitter): function, class, method. Extracts `<script>` blocks, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]`
- **PHP** (tree-sitter): function, class, method. Two-pass cross-file resolution. Detects `$this->method()`, `$obj->method()`, `ClassName::method()`, `new ClassName()`. Optional: `pip install hypergumbo[php]`. Excludes `vendor/` by default
- **HTML** (regex): file. Script tag detection

### Cross-Language Linkers
- **WebSocket linker**: Detects Socket.io (`socket.emit`, `socket.on`, `io.emit`), native WebSocket (`new WebSocket`, `ws.send`), Node.js ws package, Django Channels (`channel_layer.send`, `group_send`, `WebsocketConsumer`), and FastAPI/Starlette (`@app.websocket`, `websocket.receive_json`, `websocket.send_json`, `websocket.accept`) patterns. Creates file symbols enabling slice traversal across WebSocket boundaries. Event matching links senders to receivers. Cross-language linking between Python and JavaScript.
- **HTTP linker**: Links HTTP client calls to server route handlers across languages. Detects `fetch()`, `axios`, `requests`, `httpx`, and OpenAPI-generated TypeScript client (`__request()`) patterns. Matches URLs to route patterns (supports `:id`, `{id}`, `<id>` parameters). Router prefixes (FastAPI `APIRouter`, Flask `Blueprint`) are combined with route paths for accurate matching. Enables full-stack call graph traversal.
- **Message Queue linker**: Links message queue publishers to subscribers across languages. Detects Kafka (`producer.send()`, `consumer.subscribe()`, `@KafkaListener`), RabbitMQ (`basic_publish()`, `basic_consume()`, `sendToQueue()`), AWS SQS (`send_message()`, `receive_message()`), and Redis Pub/Sub (`publish()`, `subscribe()`) patterns. Topic-based matching enables cross-language microservices graph traversal.
- **GraphQL Resolver linker**: Links GraphQL resolver implementations to schema definitions. Detects JavaScript patterns (`Query: { users: () => ... }`), Python Ariadne (`@query.field("users")`), and Python Strawberry (`@strawberry.field`). Enables full-stack GraphQL traversal from client to resolver.
- **Database Query linker**: Links SQL queries in application code to table definitions in SQL schema files. Detects Python (`cursor.execute()`, `db.execute()`, `session.execute(text())`), JavaScript (`db.query()`, `pool.query()`, `knex()`), and Java (`statement.executeQuery()`, `@Query()`) patterns. Extracts table names from SELECT/INSERT/UPDATE/DELETE/JOIN clauses. Cross-language linking enables full-stack database understanding.
- **Event Sourcing linker**: Links event publishers to subscribers across languages. Detects JavaScript EventEmitter (`emitter.emit()`, `emitter.on()`), DOM events (`addEventListener()`, `dispatchEvent()`), Django signals (`signal.send()`, `@receiver()`), Python event buses (`EventBus.publish()`, `EventBus.subscribe()`), and Spring events (`applicationEventPublisher.publishEvent()`, `@EventListener`). Topic/event name matching enables cross-language event tracing.
- **GraphQL linker**: Links GraphQL client queries to schema definitions. Detects `gql` template literals (JS/TS), `gql()` function calls (Python). Extracts operation names and types (query, mutation, subscription). Enables full-stack GraphQL traversal.
- **gRPC linker**: Detects gRPC/Protobuf patterns across Python, Go, Java, TypeScript. Parses `.proto` service definitions, servicer implementations, stub/client usage. Links clients to servers by service name.
- **Swift/ObjC linker**: Detects Swift/Objective-C interop: `@objc` annotations, NSObject subclasses, `#selector()` references, and `*-Bridging-Header.h` imports. Enables slice traversal across Apple platform language boundaries.
- **IPC (Phoenix) linker**: Detects Phoenix Channel patterns (`broadcast!`, `push`, `handle_in`) and LiveView patterns (`handle_event`, `push_event`). Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Event matching links senders to receivers.
- **JNI linker**: Links Java native methods to C JNI implementations. Parses `Java_Package_Class_Method` naming convention. Runs when both Java and C symbols are present.
- **IPC linker**: Detects Electron IPC (`ipcRenderer.send/invoke`, `ipcMain.on/handle`), Web Workers, and `postMessage` patterns. Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Channel stored in `edge.meta.channel` and `symbol.meta.channel`.

### Route Detection
- Python: FastAPI, Flask, Django, Django REST Framework, Tornado, Aiohttp
- JavaScript: Express, Koa, Fastify, NestJS, Hapi
- Ruby: Rails, Sinatra, Grape
- Go: Gin, Echo, Fiber
- Rust: Axum, Actix-web, Rocket
- Java: Spring Boot, JAX-RS
- PHP: Laravel
- C#: ASP.NET Core
- Elixir: Phoenix

### Framework Detection
- Python: FastAPI, Flask, Django, pytest, PyTorch, TensorFlow, Keras, Transformers, LangChain, LlamaIndex, scikit-learn, MLflow, OpenAI, Anthropic
- JavaScript: React, Vue, Angular, Express, NestJS, Next.js, Nuxt, Svelte
- Rust: Axum, Actix-web, Tokio, Solana/Anchor, Substrate, ethers-rs, Arkworks, Halo2, Plonky2/3, SP1, RISC Zero, Nova, Zcash, libp2p

### Entry Point Detection
- CLI: Python Click/Typer/argparse, Node.js bin scripts
- Web: FastAPI, Flask, Django, Express, NestJS, Rails, Phoenix, Spring Boot, and 25+ more frameworks
- Desktop: Electron main/renderer
- GraphQL: Apollo Server, Yoga, Mercurius

### Supply Chain Classification
- Tier 1: First-party code
- Tier 2: Internal dependencies (workspace packages)
- Tier 3: External dependencies (node_modules, vendor)
- Tier 4: Derived artifacts (minified, generated)

### CLI Commands
- `hypergumbo [path]` - Generate Markdown sketch
- `hypergumbo run [path]` - Full analysis to JSON
- `hypergumbo slice --entry X` - Extract subgraph
- `hypergumbo slice --entry X --reverse` - Find callers
- `hypergumbo routes [path]` - List HTTP routes
- `hypergumbo search <query>` - Search symbols
- `hypergumbo init [path]` - Initialize capsule
- `hypergumbo catalog` - List available passes
- `hypergumbo export-capsule` - Export shareable capsule

### CLI Flags
- `-t N` / `--tokens N` - Token budget for sketch
- `-x` / `--exclude-tests` - Skip test files (17% faster)
- `--first-party-only` - Analyze only first-party code
- `--max-tier N` - Limit by supply chain tier
- `--no-first-party-priority` - Disable tier weighting in symbols

### Output Schema
- `schema_version`: Versioned output format
- `profile`: Languages, frameworks, LOC
- `nodes[]`: Symbols with spans, stable IDs, supply chain info
- `edges[]`: Relationships with confidence scores and evidence
- `analysis_runs[]`: Provenance tracking
- `metrics`: Aggregate statistics
- `limits`: Known gaps and failures

---

## Version History

| Version | Date       | Highlights                                                   |
| ------- | ---------- | ------------------------------------------------------------ |
| 0.6.9   | 2026-01-07 | Fewer entrypoint false positives; richer slices across linker boundaries; migration guidance aligned to ADR-0003 |
| 0.6.0   | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation            |
| 0.5.0   | 2025-12-26 | Initial release: 32 analyzers, 12 linkers, sketch generation |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.9...HEAD
[0.6.9]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...v0.6.9
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
