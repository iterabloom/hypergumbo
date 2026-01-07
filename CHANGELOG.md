# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v0.6.0
- Released **schema** is at: v0.1.0

This changelog tracks the **tool version** (package releases). The **schema version** (output format) is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version only changes when the JSON output format has breaking changes.

## [Unreleased]

### 2026-01-07 02:00

#### Cross-Language Linkers
- **Complete linker registry migration**: Migrated all remaining linkers to the `@register_linker` pattern: websocket (priority 50), phoenix_ipc (priority 40), swift_objc (priority 30), message_queue (priority 55), event_sourcing (priority 55), database_query (priority 70). Database query linker uses `LinkerRequirement` to declare dependency on table symbols. Removed ~60 lines of legacy explicit linker calls from cli.py - all linkers now run via `run_all_linkers()`.

### 2026-01-07 00:30

#### Cross-Language Linkers
- **Variable detection for HTTP/event linkers**: Extended HTTP linker to detect URLs in variables (`fetch(API_URL)`, `axios.get(config.apiUrl)`, `requests.get(url_var)`) and event sourcing linker to detect event names in variables (`emitter.emit(EVENT_NAME)`, `EventBus.publish(event_var)`). Variable matches have lower confidence (0.65) than literal matches (0.85-0.9). Added `url_type`/`event_type` fields to track match type.
- **Linker registry migration**: Migrated HTTP, GraphQL, GraphQL resolver, and dependency linkers to the `@register_linker` pattern. Linkers now use `LinkerContext` for inputs and declare requirements for diagnostics. Reduces boilerplate in cli.py and enables dynamic linker discovery.

### 2026-01-06 15:00

#### Analysis & IR
- **Symbol modifiers field**: Added `modifiers` field to Symbol for semantic attributes (`native`, `public`, `static`, etc.). Java analyzer extracts modifiers from method/constructor declarations.
- **Entrypoint language filtering**: CLI entrypoint detection (`main`, `cli`, etc.) now filters by language - excludes shader languages (GLSL/HLSL/WGSL) where `main` is a shader entry point, not a program entry point.

#### Cross-Language Linkers
- **Linker contracts system**: Added `LinkerRequirement` dataclass and `check_linker_requirements()` for diagnostics. Linkers can declare what they need, and users can see why a linker produced no edges.
- **JNI linker registry integration**: Refactored JNI linker to use registry pattern with declared requirements (`java_native_methods`, `c_jni_functions`).

#### Tooling
- **find-uncovered improvements**: Auto-runs tests when `--lines` used without prior data, warns when coverage data is stale, renamed cache to visible `coverage-report.txt`.

### 2026-01-06 09:00

#### Refactoring
- **Linker registry infrastructure**: Added `linkers/registry.py` with registration system for cross-language linkers, mirroring the pattern used in `analyze/registry.py`. Includes `LinkerContext` (unified inputs), `LinkerResult` (standardized output), `@register_linker` decorator, and `run_all_linkers()` for loop-based dispatch. **Note**: This is purely additive infrastructure - existing linkers work exactly as before. The registry establishes the pattern for future consolidation of the ~150 lines of explicit linker calls in cli.py, but no migration has been done yet. Linkers can be migrated incrementally in future PRs.
- **Language-proportional selection module**: Extracted language-proportional symbol selection from sketch.py into shared `selection/language_proportional.py`. Updated compact.py to use language-proportional selection by default (`CompactConfig.language_proportional=True`). This ensures multi-language projects get balanced representation across languages rather than being dominated by verbose languages with more symbols.
- **Selection module consolidation**: Created `selection/` package with shared utilities:
  - `filters.py`: Path classification (`is_test_path`, `is_example_path`) and symbol filtering (`EXCLUDED_KINDS`)
  - `token_budget.py`: Token estimation (`estimate_tokens`, `truncate_to_tokens`, `parse_tier_spec`)
  - `language_proportional.py`: Language-stratified selection (`group_symbols_by_language`, `allocate_language_budget`)

### 2026-01-05 20:00

#### Refactoring
- **Iterative tree traversal migration**: Migrated all 64 tree-sitter analyzers from recursive to iterative traversal using `iter_tree()` from `base.py`. This prevents `RecursionError` on deeply nested ASTs (Python's ~1000-level recursion limit was exceeded on TensorFlow's codebase). Key changes:
  - Added `iter_tree()` generator in `base.py` for stack-based pre-order traversal
  - Converted `walk(node)` recursive functions to `for node in iter_tree(root)` loops
  - Added parent-walking helpers (e.g., `_get_enclosing_class()`) that walk `node.parent`
  - Fixed bug where `id(node)` != `id(node.parent)` (tree-sitter returns new Python objects) - changed to byte-position keys `(node.start_byte, node.end_byte)`

### 2026-01-05 16:00

#### Refactoring
- **Analyzer registry consolidation**: Replaced 65+ individual analyzer imports in cli.py with a single `all_analyzers.py` registry. Reduced `cli.py` from 2590 to 1705 lines (-885 lines, 34% reduction).
- **Shared base classes**: Created `analyze/base.py` with shared `AnalysisResult`, `FileAnalysis`, and tree-sitter helper functions (`node_text`, `find_child_by_type`, `find_child_by_field`, `is_grammar_available`, `make_symbol_id`).
- **Go analyzer migration**: Refactored Go analyzer to use shared base classes as pilot, demonstrating the pattern for other analyzers.
- **Lazy loading for testability**: ANALYZERS list uses lazy module imports via `get_func()` to enable test patching at the source module level.

### 2026-01-05 09:00

#### Analysis Passes
- **HLSL** (tree-sitter): function, struct, variable. Detects DirectX HLSL shaders: function definitions (vertex/pixel/compute shaders) with signatures, struct definitions (input/output structures), constant buffer declarations (cbuffer), resource declarations (Texture, Sampler, Buffer). Essential for DirectX game development. Complements GLSL/WGSL shader analyzers. Optional: `pip install tree-sitter-language-pack`
- **Ada** (tree-sitter): package, function, procedure, type, constant. Detects Ada safety-critical code: package specs/bodies, functions/procedures with signatures, record types, constants, with-clause imports. Ada is used in aerospace, defense, medical devices, and embedded systems. Optional: `pip install tree-sitter-language-pack`
- **D** (tree-sitter): module, function, struct, class, interface. Detects D systems programming code: module declarations, function definitions with signatures, struct/class/interface definitions, import statements. D is a modern C++ alternative combining low-level control with modern features. Optional: `pip install tree-sitter-language-pack`
- **Nim** (tree-sitter): function, method, type. Detects Nim code: proc/func/method definitions with signatures, type definitions (objects, enums), import statements. Nim combines Python-like syntax with systems programming power, compiling to C/C++/JavaScript. Optional: `pip install tree-sitter-language-pack`

### 2026-01-05 08:00

#### Analysis Passes
- **GDScript** (tree-sitter): function, variable, signal, class. Detects Godot game engine scripts: functions with signatures, class variables, signals, class_name and inner classes. Function signatures show typed/untyped parameters and return types. preload/load imports for scene/script references. For Godot game development. Optional: `pip install tree-sitter-language-pack`
- **Starlark** (tree-sitter): function, target, variable. Detects Bazel/Buck build files: function definitions with signatures, build targets (py_binary, cc_library, etc.) with rule type in meta, variable assignments. Load statements create import edges. Target deps create dependency edges. For build system analysis. Optional: `pip install tree-sitter-language-pack`
- **Fish** (tree-sitter): function, alias, variable. Detects Fish shell scripts: function definitions with argument signatures, alias declarations, global variable assignments (set -g/-gx/-U). Source statements create import edges. Function calls tracked within function bodies. Complements Bash analyzer for shell configuration. Optional: `pip install tree-sitter-language-pack`

### 2026-01-05 07:00

#### Analysis Passes
- **Thrift** (tree-sitter): service, function, struct, enum, typedef, const. Detects Apache Thrift IDL: services, RPC functions with signatures, structs, enums, typedefs, constants, includes. Function signatures show parameters and return types. Complements Thrift-based microservices analysis. Optional: `pip install tree-sitter-language-pack`
- **Cap'n Proto** (tree-sitter): struct, interface, method, enum, const. Detects Cap'n Proto IDL: structs, interfaces (RPC services), methods with signatures, enums, constants, imports. Method signatures show parameters and return types. Supports nested structs. Complements Proto/Thrift for microservices analysis. Optional: `pip install tree-sitter-language-pack`
- **PowerShell** (tree-sitter): function, filter, workflow. Detects PowerShell scripts: functions with verb-noun naming, filters, workflows. Function signatures show parameters with types and defaults. Import-Module and using module imports. Command call edges. For Windows/Azure automation and DevOps. Optional: `pip install tree-sitter-language-pack`

### 2026-01-05 06:00

#### Analysis Passes
- **Proto** (tree-sitter): service, rpc, message, enum. Detects Protocol Buffers: services (gRPC), RPC methods with request/response types, messages, enums, imports. RPC signatures show request/response types including streaming. Complements gRPC linker for full stack tracing. Optional: `pip install tree-sitter-language-pack`

#### Sketch & Signatures
- Language-proportional selection: Proportional symbol allocation by language for multi-language projects (enabled by default; disable with `--no-language-proportional`)

### 2026-01-05 03:00

#### Sketch & Signatures
- Minimum Key Symbols guarantee: Always includes at least 5 symbols even with tight budget
- Multi-language test detection: Detects Swift (Tests/, *Tests.swift), Go (*_test.go), Java/Kotlin (*Test.java/kt), Rust (*_test.rs) in addition to Python/JS patterns
- Framework-specific coverage hints: Test summary section suggests appropriate coverage tool (jest --coverage, go test -cover, mvn test jacoco:report, etc.) instead of always suggesting pytest

### 2026-01-05 00:00

#### Cross-Language Linkers
- **Dependency linker**: Links manifest dependencies (Cargo.toml, pyproject.toml) to code import statements. Matches package names to imports with naming convention handling (e.g., Rust hyphens → underscores). Enables traceability from code usage back to manifest declarations.

### 2026-01-04 15:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for Clojure defn forms (e.g., `[x, y]`)
- Vector params: Handles Clojure vector syntax for parameters
- Signature extraction: Extracts signatures for OCaml let bindings (e.g., `(x, y)`)
- Multi-param functions: Handles curried parameter style
- Signature extraction: Extracts signatures for Solidity functions (e.g., `(address to, uint256 amount) returns (bool)`)
- Return types: Displays return type definition
- Signature extraction: Extracts signatures for CUDA kernels/functions (e.g., `(int *a, int *b) int`)
- Return types: Displays non-void return types
- Signature extraction: Extracts signatures for query/mutation variable definitions (e.g., `($id: ID!)`)
- Variable definitions: Handles typed GraphQL variables
- Signature extraction: Extracts signatures for Wolfram function patterns (e.g., `[x_, y_]`)
- Pattern matching: Handles Wolfram's pattern-matching argument syntax
- Signature extraction: Extracts signatures for Haskell functions (e.g., `:: Int -> Int -> Int`)
- Two-pass resolution: Collects type signatures first, then associates with function definitions
- Signature extraction: Extracts signatures for Dart functions (e.g., `(int x, int y) int`)
- Optional/named params: Handles optional and named parameters with defaults
- Signature extraction: Extracts signatures for Lean definitions (e.g., `(n : Nat) : Nat`)
- Theorem support: Handles theorem/lemma declarations with proof types
- Signature extraction: Extracts signatures for Agda functions (e.g., `: Nat -> Nat`)
- Postulate support: Handles postulate declarations
- Constructor support: Extracts signatures for data constructors

### 2026-01-04 14:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for CMake functions/macros (e.g., `(ARG1, ARG2, ARG3)`)
- Function/macro support: Handles both `function()` and `macro()` commands
- Signature extraction: Extracts signatures for Nix functions (lambdas and formals)
- Simple lambdas: Curried params (e.g., `(x, y)` for `x: y: body`)
- Formals patterns: Attrset patterns (e.g., `{ name, greeting }` for `{ name, greeting }: body`)
- Top-level functions: Uses file basename for module/overlay functions

### 2026-01-04 13:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for SQL functions (e.g., `(price DECIMAL, qty INT) RETURNS DECIMAL`)
- Return type: Displays return type after RETURNS keyword
- Signature extraction: Extracts signatures for Bash functions (always `()` since Bash uses positional args)

### 2026-01-04 06:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for Zig functions (e.g., `(x: i32, y: i32) i32`)
- Signature extraction: Extracts signatures for Ruby methods (e.g., `(param, optional = ..., &block)`)
- Keyword parameters: Handles keyword args with `:` suffix
- Signature extraction: Extracts signatures for Elixir functions (e.g., `(param1, param2)`)
- Pattern matching: Handles pattern-matched parameters
- Signature extraction: Extracts signatures for Erlang functions (e.g., `(Param1, Param2)`)
- Pattern matching: Handles Erlang pattern-matched parameters
- Signature extraction: Extracts signatures for Perl subs (e.g., `()` for traditional subs)
- Signature extraction: Extracts signatures for Lua functions (e.g., `(x, y)`)
- Method syntax: Handles method-style `Table:method` definitions
- Signature extraction: Extracts signatures for Groovy methods (e.g., `(String name, int age)`)
- Signature extraction: Extracts signatures for Elm functions (e.g., `(x, y)`)
- Signature extraction: Extracts signatures for R functions (e.g., `(x, y)`)
- Default values: Handles default values (e.g., `greeting = ...`)
- Signature extraction: Extracts signatures for GLSL functions (e.g., `(float x, float y) float`)
- Signature extraction: Extracts signatures for WGSL functions (e.g., `(x: f32, y: f32) -> f32`)
- Return type: Displays return type with `->` arrow syntax

### 2026-01-04 05:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for C# methods (e.g., `(int x, string name): void`)
- Signature extraction: Extracts signatures for Swift functions (e.g., `(x: Int, name: String) -> Void`)
- External/internal params: Handles Swift's external/internal param naming (e.g., `_ x` becomes just `x`)
- Signature extraction: Extracts signatures for Kotlin functions (e.g., `(x: Int, name: String): String`)
- Unit handling: Omits Unit return types for cleaner display
- Signature extraction: Extracts signatures for Scala methods (e.g., `(x: Int, y: Int): Int`)
- Unit handling: Omits Unit return types for cleaner display
- Signature extraction: Extracts signatures for PHP methods (e.g., `(int $x, string $name): void`)
- Signature extraction: Extracts signatures for Objective-C methods (e.g., `(int x, int y): int`)
- Signature extraction: Extracts signatures for Fortran functions/subroutines (e.g., `(x, y): integer`)
- Subroutine handling: Subroutines show params without return type
- Signature extraction: Extracts signatures for F# functions (e.g., `(x: int, y: int): int`)
- Unit handling: Handles unit parameter patterns
- Signature extraction: Extracts signatures for Julia functions (e.g., `(x::Int, y::Int)::Int`)
- Short-form functions: Handles short-form `f(x) = expr` syntax

### 2026-01-04 04:00

#### Sketch & Signatures
- Signature extraction: Extracts signatures for C functions (e.g., `(int x, char* name) int`)
- Pointer types: Handles pointer params and returns (e.g., `char*`, `int**`)
- Void handling: Omits void return types for cleaner display
- Signature extraction: Extracts signatures for C++ functions (e.g., `(const std::string& name) int`)
- Reference types: Handles reference params (e.g., `const T&`)
- Qualified types: Handles `std::string`, `::global::Type`
- Signature extraction: Extracts signatures for Java methods (e.g., `(String name, int age) User`)
- Constructor signatures: Handles constructors (no return type)
- Generic types: Handles `List<String>`, `Map<K, V>`
- Array types: Handles `String[]`, both before and after param name
- Varargs: Handles `Object... args` spread syntax

### 2026-01-04 03:00

#### Sketch & Signatures
- Signature extraction: Extracts function signatures for Rust functions/methods (e.g., `(x: i32, y: String) -> bool`)
- Self parameters: Handles `&self`, `&mut self`, `self` receiver patterns
- Return types: Displays return type arrows (e.g., `-> Result<T, E>`)
- Signature extraction: Extracts function signatures for Go functions/methods (e.g., `(x int, y string) error`)
- Multiple returns: Handles Go multiple return values (e.g., `(int, error)`)
- Named returns: Supports named return values (e.g., `(result int, err error)`)
- Parameter grouping: Collapses same-type params (e.g., `a, b int`)
- Signature extraction: Extracts signatures for TS/JS functions (e.g., `(x: number, y: string): boolean`)
- Type annotations: Displays TypeScript type annotations
- Optional params: Shows optional parameters (e.g., `name?: string`)
- Default values: Shows defaults as ellipsis (e.g., `count = ...`)
- Rest parameters: Handles `...args` spread syntax
- Arrow functions: Extracts signatures from arrow function expressions

### 2026-01-01 23:00

#### Added
- Embedding mode config extraction now uses diminishing returns and intra-file diversity
  - Prevents any single file (e.g., LICENSE) from dominating the config section
  - Prioritizes diverse, relevant content across multiple config files
  - Uses same diminishing returns formula as symbol selection (marginal = score / (1 + α * picks))
  - Diversity mechanism penalizes chunks similar to already-selected chunks from the same file

### 2025-12-30 21:00

#### Analysis Passes
- **Perl** (tree-sitter): module, function. Detects Perl code: packages (`package`), subroutines (`sub`), `use` statements, `require` expressions. Method calls via arrow operator (`$obj->method`). Two-pass cross-file resolution with qualified names. For legacy systems and text processing. Optional: `pip install tree-sitter-language-pack`

### 2025-12-30 15:00

#### Analysis Passes
- **F#** (tree-sitter): module, function, value, record, union. Detects F# code: modules (including nested), functions (`let`), values, record types, discriminated unions, `open` statements. Two-pass cross-file resolution. For .NET functional-first development. Optional: `pip install tree-sitter-language-pack`

### 2025-12-30 14:00

#### Analysis Passes
- **Erlang** (tree-sitter): module, function, record, macro, type. Detects Erlang code: -module, fun_decl (functions with arity), -record, -define (macros), -type. Function calls (local and remote with module:function syntax), -behaviour and -import edges. Two-pass cross-file resolution. For BEAM VM distributed systems (RabbitMQ, CouchDB). Optional: `pip install tree-sitter-language-pack`
- **Elm** (tree-sitter): module, function, type, port. Detects Elm code: module declarations, value/function declarations, type aliases, custom types (union types), port declarations (JS interop). Import edges from import clauses. Two-pass cross-file resolution. For functional web frontend development. Optional: `pip install tree-sitter-language-pack`

### 2025-12-30 05:00

#### Analysis Passes
- **Clojure** (tree-sitter): module, function, variable, macro, protocol, record, multimethod. Detects Clojure code: ns (namespaces), defn/defn- (functions), def (variables), defmacro, defprotocol, defrecord, defmulti. Require/import edges from ns :require. Function call edges. Two-pass cross-file resolution. For JVM functional programming. Optional: `pip install tree-sitter-language-pack`

### 2025-12-30 02:00

#### Sketch & Signatures
- README extraction: Extracts first descriptive paragraph from README.md/rst/txt, skips badges/images/HTML
- Title subtitle fallback: Falls back to title subtitle (e.g., "Project: Description here") if no paragraph found
- Docstring extraction: Extracts first-line docstrings for Python functions/classes in Key Symbols
- Symbol annotation: Displays docstring as `— Description` after symbol name
- Signature extraction: Extracts function signatures (parameters + return types) for Python functions/methods
- Type annotations: Displays typed parameters and return types (e.g., `func(x: int) -> str`)
- Complex types: Handles `List[T]`, `Dict[K,V]`, `Optional[T]`, `X | Y` unions
- Default values: Shows defaults as ellipsis (e.g., `config=…`)
- *args/**kwargs: Properly formats varargs (e.g., `(*args, **kwargs)`)
- Async functions: Extracts signatures from `async def` functions
- Vocabulary extraction: Extracts domain-specific terms from source code identifiers
- Term filtering: Filters out common programming terms and testing vocabulary
- Compound word splitting: Splits camelCase, PascalCase, and snake_case identifiers
- Format: Displays as "Key terms: term1, term2, ..." for quick domain understanding

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

| Version | Date | Highlights |
|---------|------|------------|
| 0.6.0 | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation |
| 0.5.0 | 2025-12-26 | Initial release: 32 analyzers, 12 linkers, sketch generation |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...HEAD
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
