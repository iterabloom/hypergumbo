# Implementation Status

This document tracks progress against [Spec A (MVP)](docs/hypergumbo-spec.md#spec-a--hypergumbo-mvp).

> **Note:** The spec file also contains "Spec B" which describes a multi-year roadmap. Spec B is not in scope for current development.

## Legend

- [x] Implemented and tested
- [ ] Not yet implemented
- [stub] CLI command exists but is a placeholder

## Week 1: Foundation + IR Layer

| Feature | Status | Notes |
|---------|--------|-------|
| Schema definition (behavior_map view) | [x] | `schema.py` |
| Internal IR classes (Symbol, Edge, AnalysisRun) | [x] | `ir.py` |
| Profile module (language detection) | [x] | `profile.py` |
| File discovery + exclude logic | [x] | `discovery.py` |
| JSON writer (IR → views compilation) | [x] | `cli.py` |
| ID generation (stable_id, shape_id) | [x] | `analyze/py.py` |
| Pass interface and registry | [x] | `catalog.py` - Pass, Pack, Catalog classes |
| Catalog system (catalog.json schema) | [x] | `catalog.py` - get_default_catalog() |
| Capsule Plan (plan.json, validation) | [x] | `plan.py` - generate_plan(), validate_plan() |

## Week 2: Python Analyzer

| Feature | Status | Notes |
|---------|--------|-------|
| Python AST parser → IR emission | [x] | `analyze/py.py` |
| Function/class detection | [x] | |
| Call edges (intra-file) | [x] | |
| Import edges (cross-file) | [x] | `from X import Y` and `import X` emitted as `imports` edges |
| Method call detection (self.method) | [x] | |
| Evidence-type-based confidence | [x] | `meta.evidence_type` on edges |
| Provenance tracking (AnalysisRun) | [x] | `analysis_runs[]` in output |

## Week 3: JS/TS Analyzer (Optional)

| Feature | Status | Notes |
|---------|--------|-------|
| Tree-sitter integration | [x] | `analyze/js_ts.py` |
| JS/TS AST → IR emission | [x] | Functions, classes, methods, getters, setters |
| TypeScript interface detection | [x] | `kind: "interface"` |
| TypeScript type alias detection | [x] | `kind: "type"` |
| TypeScript enum detection | [x] | `kind: "enum"` |
| Arrow function detection | [x] | `const fn = () => {}` |
| Call/import edges | [x] | ES6 imports, require(), function calls |
| Fallback if tree-sitter unavailable | [x] | Returns skipped result with reason |

## Week 4: Slicing + Entrypoints

| Feature | Status | Notes |
|---------|--------|-------|
| Slice module (BFS/DFS on relationships) | [x] | `slice.py` with BFS traversal; includes file-level imports |
| Reverse slice (find callers) | [x] | `--reverse` flag on `hypergumbo slice` finds what calls X |
| Entrypoint detection heuristics | [x] | `entrypoints.py` - FastAPI, Flask, Click, Electron, Django, Express.js, NestJS, Spring Boot, Rails, Phoenix, Go (Gin/Echo/Fiber), Laravel, Rust (Actix-web/Axum/Rocket/Warp), ASP.NET Core, Sinatra, Ktor, Vapor, Plug, Hapi, Fastify, Koa, Grape, Tornado, Aiohttp, Slim, Micronaut, Flutter (runApp, widgets), GraphQL (Apollo Server, Yoga, Mercurius). Test files excluded via `_is_test_file()` helper. |
| Feature generation with query specs | [x] | Stable feature IDs from query |
| Slice IDs and reproducibility | [x] | `sha256(json.dumps(query))` |

## Week 5: Capsule Initialization

| Feature | Status | Notes |
|---------|--------|-------|
| `hypergumbo init` command | [x] | Creates `.hypergumbo/capsule.json` + `capsule_plan.json` |
| Template-based plan generation | [x] | `plan.py` - generates from profile + catalog |
| LLM-assisted plan generation | [x] | `llm_assist.py` - OpenRouter, OpenAI, llm package backends. Interactive setup prompts if no API key configured. Keys stored in `~/.config/hypergumbo/config.json`. *Proof-of-concept; template-based generation currently produces equivalent results.* |
| `hypergumbo catalog` command | [x] | Lists passes and packs |
| `hypergumbo export-capsule` command | [x] | `export.py` - tarball with privacy redactions |

## Sketch Generation (Default Mode)

| Feature | Status | Notes |
|---------|--------|-------|
| Token-budgeted Markdown sketch | [x] | `sketch.py` - ~4 chars/token heuristic with ceiling division for conservative estimates |
| Default CLI mode | [x] | `hypergumbo [path]` runs sketch |
| Token limit flag | [x] | `-t N` / `--tokens N` |
| Language breakdown | [x] | Sorted by LOC percentage |
| Directory structure | [x] | Top-level dirs with type labels |
| Framework detection | [x] | Via profile.py. **Python:** FastAPI, Flask, Django, Starlette, Quart, Sanic, Litestar, Falcon, Bottle, CherryPy, Pyramid, Tornado, Aiohttp, PyTorch, TensorFlow, Keras, JAX, Transformers, spaCy, NLTK, LangChain, LangGraph, LlamaIndex, Haystack, scikit-learn, XGBoost, LightGBM, CatBoost, Optuna, MLflow, WandB, Ray, vLLM, DeepSpeed, PaddlePaddle, OpenAI, Anthropic. **JavaScript/TypeScript:** React, Vue, Angular, Svelte, Solid, Qwik, Preact, Lit, Alpine, htmx, Ember, Next.js, Nuxt, Remix, Astro, Gatsby, SvelteKit, Express, NestJS, Fastify, Koa, Hapi, Adonis, Sails, Hono, Elysia, React Native, Expo, Ionic, Capacitor, NativeScript, Electron, Tauri, Hardhat, Web3.js, ethers.js, Wagmi, Viem. **Rust:** Axum, Actix-web, Rocket, Warp, Tide, Gotham, Poem, Salvo, Tokio, async-std, Serde, Clap, Tauri, Solana/Anchor, Substrate, CosmWasm, ethers-rs, Alloy, Foundry, REVM, Arkworks, Bellman, Halo2, Plonky2/3, SP1, RISC Zero, Jolt, Nova, HyperNova, Zcash, libp2p, curve25519/ed25519, secp256k1. **Go:** Gin, Echo, Fiber, Chi, Gorilla, Buffalo, Revel, Beego, Iris. **PHP:** Laravel, Symfony, CodeIgniter, CakePHP, Yii, Phalcon, Slim. **Java/Kotlin:** Spring Boot, Micronaut, Quarkus, Dropwizard, Vert.x, Javalin, Helidon, Spark, Ktor, Jetpack Compose. **Swift:** Vapor, Kitura, Perfect, SwiftUI. **Scala:** Play, Akka HTTP, http4s, ZIO HTTP, Finatra. **Dart/Flutter:** Flutter SDK, flutter_bloc, Riverpod, Provider, GetX, MobX, Dio, Freezed, go_router, Flame. |
| Section-boundary truncation | [x] | Preserves coherent sections when truncating |
| Source file listings | [x] | Progressive expansion based on budget |
| Entry points section | [x] | CLI, HTTP routes, Electron patterns |
| Key symbols section | [x] | Functions/classes from static analysis |
| Graph centrality ranking | [x] | In-degree centrality orders symbols by importance |
| Test file filtering | [x] | Excludes test files from centrality calculation |
| **Symbol Selection** | | |
| Two-phase selection policy | [x] | Coverage-first phase (33% budget) ensures broad file coverage, then diminishing-returns greedy fill maximizes marginal utility |
| Sum-of-top-K file scoring | [x] | Files ranked by sum of top-3 symbol scores (density metric) rather than single-max centrality |
| Per-file render compression | [x] | Max 5 symbols per file with "… +N more (top score: X.XX)" overflow summary |
| Entrypoint file preservation | [x] | Entry points and their containing files prioritized in Key Symbols section |
| Deterministic output | [x] | Sorted iteration over SOURCE_DIRS ensures reproducible output across runs |

## CLI Commands

| Command | Status | Description |
|---------|--------|-------------|
| `hypergumbo [path] [-t N] [-x]` | [x] | Default sketch mode with optional token budget |
| `hypergumbo sketch [path] [-t N] [-x]` | [x] | Explicit sketch command |
| `-x` / `--exclude-tests` | [x] | Skip test files during analysis (17% faster on large codebases) |
| `hypergumbo --version` | [x] | Print version |
| `hypergumbo init [path]` | [x] | Initialize capsule |
| `hypergumbo run [path] [-x]` | [x] | Run analysis. Supports `-x/--exclude-tests` to filter test files |
| `hypergumbo slice --entry X` | [x] | Produce reduced slice |
| `hypergumbo catalog` | [x] | List passes/packs |
| `hypergumbo export-capsule` | [x] | Export shareable capsule |
| `hypergumbo routes` | [x] | Display API routes (FastAPI, Flask, Django/DRF, Express.js, Koa, Fastify, NestJS, Rails, Axum, Actix-web, Rocket, Gin, Echo, Fiber). Shows HTTP methods, route paths, and handler functions |
| `hypergumbo search <query>` | [x] | Search symbols by name pattern |
| `hypergumbo build-grammars` | [x] | Build Lean/Wolfram grammars from source (tree-sitter) |

## Output Schema Compliance

| Field | Status | Notes |
|-------|--------|-------|
| `schema_version` | [x] | |
| `profile` (languages, frameworks) | [x] | |
| `analysis_runs[]` | [x] | Provenance tracking |
| `nodes[]` with span, stable_id, shape_id | [x] | |
| `edges[]` with id, confidence, meta | [x] | |
| `features[]` | [x] | Via slice command output |
| `metrics` | [x] | `metrics.py` - counts, avg confidence, per-language |
| `limits` | [x] | `limits.py` - failed files, skipped langs, known gaps |

## Schema Validation Tests ("Spec Driven Development")

| Feature | Status | Notes |
|---------|--------|-------|
| Formal JSON Schema | [x] | `docs/schema.json` - JSON Schema Draft 2020-12 |
| Auto-generated schema | [x] | `./scripts/generate-schema` - generates from Python dataclasses |
| Schema CI check | [x] | `./scripts/generate-schema --check` - verifies schema is up-to-date |
| **Validation Tests** | | |
| Empty behavior map validates | [x] | `test_empty_behavior_map_validates` |
| Real analysis output validates | [x] | `test_real_analysis_output_validates` |
| Symbol with all fields validates | [x] | `test_symbol_with_all_fields_validates` |
| Edge with all fields validates | [x] | `test_edge_with_all_fields_validates` |
| AnalysisRun validates | [x] | `test_analysis_run_validates` |
| Invalid edge type fails | [x] | `test_invalid_edge_type_fails_validation` |
| Invalid symbol kind fails | [x] | `test_invalid_symbol_kind_fails_validation` |
| **Schema Sync Tests** | | |
| Schema matches generated | [x] | `test_schema_matches_generated` - runs `generate-schema --check` |
| Schema version matches code | [x] | `test_schema_version_matches_code` - verifies `SCHEMA_VERSION` |
| All edge types in schema | [x] | `test_all_edge_types_in_schema` - checks enum completeness |
| All symbol kinds in schema | [x] | `test_all_symbol_kinds_in_schema` - checks enum completeness |

*Philosophy: Tests are specifications. The JSON Schema is a formal spec that both implementation and tests verify.*

## CI & Automation

| Feature | Status | Notes |
|---------|--------|-------|
| **CI Jobs** | | `.github/workflows/ci.yml` |
| Ruff linting | [x] | `lint` job - pycodestyle, pyflakes, security rules |
| Bandit security | [x] | `lint` job - security-focused static analysis |
| pip-audit | [x] | `audit` job - dependency vulnerability scanning |
| pytest | [x] | `pytest` job - full test suite |
| verify-generated | [x] | Checks schema and architecture docs are fresh |
| **Pre-commit Hooks** | | `.githooks/pre-commit` |
| Ruff check | [x] | Fast linting before commit |
| Bandit check | [x] | Security check before commit |
| Schema freshness | [x] | `./scripts/generate-schema --check` |
| **Auto-generation Scripts** | | |
| generate-schema | [x] | Generates `docs/schema.json` from dataclasses |
| generate-architecture | [x] | Generates `docs/ARCHITECTURE.md` via self-analysis. Features: generation metadata (commit SHA, versions) for drift detection, `tokenize.open()` for encoding safety, proper `.py` suffix removal, includes `__main__.py`, first non-empty docstring line, exact basename matching, manual maintenance note on Key Abstractions. Reads version from `pyproject.toml` (avoids repo vs installed version mismatch), uses `parts[0]` check for tighter module categorization, `--check` mode warns on commit SHA drift. |
| **Edge Filtering Fix** | [x] | Import edges now properly included in `--first-party-only` and `--max-tier` output. Detects file-level edge sources by `:file:` pattern. |

## Analysis Passes

| Language | Parser | Symbols | Edges | Notes |
|----------|--------|---------|-------|-------|
| Python | [x] AST | function, class, method, route | calls, imports, instantiates | Two-pass cross-file resolution. Detects `self.method()`, `ClassName()` instantiation. Methods named with class prefix (`ClassName.methodName`). **src/ layout detection:** Automatically detects PEP 517/518 `src/` layout projects and adjusts module name derivation (e.g., `src/flask/app.py` → `flask.app` instead of `src.flask.app`) for correct cross-file import resolution. **Route detection:** FastAPI (`@app.get`, `@router.post`), Flask (`@app.route`, `@app.get`), Django REST Framework (`@api_view(['GET', 'POST'])`), Django CBV methods (get/post/put/patch/delete), and Django URL patterns (`path()`, `re_path()`, `url()`) set `stable_id` to HTTP method for `routes` command discovery. **Router prefix detection:** `APIRouter(prefix='/api/v1')` and `Blueprint(url_prefix='/api')` prefixes are combined with route paths. |
| HTML | [x] regex | file | script_src | Script tag detection |
| JavaScript | [x] tree-sitter | function, class, method, getter, setter, route | calls, imports, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) handlers set `stable_id` to HTTP method. **Express.js enhancements:** Wrapper patterns (`catchAsync(handler)`), external handlers (`userController.create`), and chained syntax (`router.route('/').get(handler)`) all detected. Optional: `pip install hypergumbo[javascript]` |
| TypeScript | [x] tree-sitter | function, class, method, getter, setter, interface, type, enum, route | calls, imports, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) and NestJS decorators (`@Get()`, `@Post()`) set `stable_id` to HTTP method. **Express.js enhancements:** Wrapper patterns (`catchAsync(handler)`), external handlers (`userController.create`), and chained syntax (`router.route('/').get(handler)`) all detected. Optional: `pip install hypergumbo[javascript]` |
| Svelte | [x] tree-sitter | function, class, method | calls, imports, instantiates | Extracts `<script>` blocks, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]` |
| PHP | [x] tree-sitter | function, class, method | calls, instantiates | Two-pass cross-file resolution. Detects `$this->method()`, `$obj->method()`, `ClassName::method()`, `new ClassName()`. Optional: `pip install hypergumbo[php]`. Excludes `vendor/` by default |
| C | [x] tree-sitter | function, struct, enum, typedef | calls | Two-pass cross-file resolution. Detects function calls, JNI export patterns (`Java_ClassName_methodName`). Optional: `pip install hypergumbo[c]` |
| Java | [x] tree-sitter | class, interface, enum, method, constructor | calls, extends, implements, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `ClassName.method()`, inheritance, `new ClassName()`. Native method detection with `meta.is_native`. **Route detection:** Spring Boot (`@GetMapping`, `@PostMapping`, `@RequestMapping`) sets `stable_id` to HTTP method for `routes` command discovery. Optional: `pip install hypergumbo[java]` |
| Vue | [x] tree-sitter | function, class, method | calls, imports, instantiates | Extracts `<script>` and `<script setup>` blocks from `.vue` SFCs, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]` |
| Elixir | [x] tree-sitter | module, function, macro | calls, imports | Detects `def/defp`, `defmodule`, `use/import/alias`. Two-pass cross-file resolution. Optional: `pip install hypergumbo[elixir]` |
| Rust | [x] tree-sitter | function, struct, enum, trait, method, route | calls, imports | Detects `fn`, `struct`, `enum`, `trait`, `impl` blocks, `use` statements. **Route detection:** Axum `.route("/path", get(handler))` with method chaining, Actix-web/Rocket `#[get("/path")]` attribute macros (handles multi-param attributes). Route symbols have `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[rust]` |
| Go | [x] tree-sitter | function, method, struct, interface, type, route | calls, imports | Detects `func`, methods with receivers, `type X struct/interface`, `import` statements. **Route detection:** Gin/Echo (`r.GET`, `e.POST`), Fiber (`app.Get`, `app.Post`) with lowercase methods. Route symbols have `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[go]` |
| Ruby | [x] tree-sitter | method, class, module, route | calls, imports | Detects `def`, `class`, `module`, `require/require_relative`. **Route detection:** Rails DSL (`get '/path'`, `post '/path'`, `resources :name`) creates route symbols with `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[ruby]` |
| Kotlin | [x] tree-sitter | function, class, object, interface, method | calls, imports | Detects `fun`, `class`, `object`, `interface`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[kotlin]` |
| Swift | [x] tree-sitter | function, class, struct, protocol, enum, method | calls, imports | Detects `func`, `class`, `struct`, `protocol`, `enum`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[swift]` |
| Scala | [x] tree-sitter | function, class, object, trait, method | calls, imports | Detects `def`, `class`, `object`, `trait`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[scala]` |
| Lua | [x] tree-sitter | function, method | calls, imports | Detects `function`, `local function`, method-style `Table:method()`, `require()` imports. Two-pass cross-file resolution. Optional: `pip install hypergumbo[lua]` |
| Haskell | [x] tree-sitter | function, data, class, instance | calls, imports | Detects functions (with/without type signatures), data types, type classes, instances, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[haskell]` |
| Agda | [x] tree-sitter | module, function, data, record | imports | Dependently typed proof assistant. Detects modules, functions (including theorems/lemmas), data types, records, postulates. Two-pass cross-file resolution. Tested on agda-stdlib (18,949 symbols) and PLFA (6,014 symbols). Optional: `pip install tree-sitter-agda` |
| Lean | [x] tree-sitter | function, theorem, structure, inductive, class, instance | imports | Lean 4 theorem prover. Detects defs, theorems, lemmas, structures, inductive types, classes, instances. Two-pass cross-file resolution. Tested on Mathematics in Lean (379 symbols). Built from source via `scripts/build-source-grammars`. |
| Wolfram | [x] tree-sitter | function, variable | calls, imports | Wolfram Language (Mathematica). Detects SetDelayed (:=) function definitions, Set (=) assignments, function calls, Get/Needs/Import statements. Two-pass cross-file resolution. Built from source via `scripts/build-source-grammars`. |
| OCaml | [x] tree-sitter | function, type, module | calls, imports | Detects let bindings (functions), types, modules, `open` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[ocaml]` |
| Solidity | [x] tree-sitter | contract, interface, library, function, constructor, modifier, event | calls, imports | Ethereum smart contracts. Detects contracts, interfaces, libraries, functions, constructors, modifiers, events, and import statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-solidity` |
| C# | [x] tree-sitter | class, interface, struct, enum, method, constructor, property | calls, imports, instantiates | Two-pass cross-file resolution. Detects method calls, `using` directives, `new ClassName()`. Optional: `pip install hypergumbo[csharp]` |
| C++ | [x] tree-sitter | class, struct, enum, function, method | calls, imports, instantiates | Two-pass cross-file resolution. Detects function/method calls, `#include` directives, `new ClassName()`. Handles qualified names (Namespace::Class::method). Optional: `pip install hypergumbo[cpp]` |
| Zig | [x] tree-sitter | function, struct, enum, union, error_set, method, test | calls, imports | Detects `fn`, `struct`, `enum`, `union`, `error` sets, `test` blocks, `@import()` statements. Methods distinguished by `self` parameter. Two-pass cross-file resolution. Optional: `pip install tree-sitter-zig` |
| Groovy | [x] tree-sitter | class, interface, enum, method, function | calls, imports | Detects classes, interfaces, enums, methods, top-level functions (`def`), import statements. Handles `.gradle` build files. Two-pass cross-file resolution. Optional: `pip install tree-sitter-groovy` |
| Julia | [x] tree-sitter | module, function, struct, abstract, macro, const | calls, imports | Detects modules, functions (full and short-form), structs, abstract types, macros, constants, import/using statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-julia` |
| Bash | [x] tree-sitter | function, export, alias | calls, sources | Detects functions (both `function name()` and `name()` styles), exported variables, aliases, source/dot statements. Two-pass cross-file resolution. Optional: `pip install tree-sitter-bash` |
| Objective-C | [x] tree-sitter | class, protocol, method, property | calls, imports | Detects `@interface`, `@implementation`, `@protocol`, methods (`-`/`+`), properties. Message send call resolution `[receiver message]`. Two-pass cross-file resolution. Optional: `pip install tree-sitter-objc` |
| HCL/Terraform | [x] tree-sitter | resource, data, variable, output, module, provider, local | depends_on, imports | Detects Terraform blocks, variable references, resource dependencies, module sources. Two-pass cross-file resolution. Optional: `pip install tree-sitter-hcl` |
| YAML/Ansible | [x] tree-sitter | playbook, task, handler, variable | imports | Detects Ansible playbooks, tasks, handlers, variables from YAML files. Extracts `include_tasks`, `import_tasks`, `include_role`, `import_role` references. Two-pass cross-file resolution. Optional: `pip install tree-sitter-yaml` |
| SQL | [x] tree-sitter | table, view, function, trigger, index, procedure | references | Detects CREATE TABLE, VIEW, FUNCTION, TRIGGER, INDEX statements. Foreign key REFERENCES edges. Two-pass cross-file resolution. Optional: `pip install tree-sitter-sql` |
| Dockerfile | [x] tree-sitter | stage, exposed_port, env_var, build_arg | depends_on, base_image | Detects FROM stages, EXPOSE ports, ENV variables, ARG build args. Multi-stage build dependencies via COPY --from edges. Optional: `pip install tree-sitter-dockerfile` |
| CUDA | [x] tree-sitter | kernel, device_function, host_device_function, function | calls, kernel_launch | Detects `__global__` kernels, `__device__` functions, `__host__ __device__` dual functions. Kernel launch edges for `<<<grid, block>>>` syntax. Optional: `pip install tree-sitter-cuda` |
| Verilog | [x] tree-sitter | module, interface | instantiates | Detects Verilog/SystemVerilog modules, interfaces, module instantiations. Cross-file module resolution. Optional: `pip install tree-sitter-verilog` |
| CMake | [x] tree-sitter | project, library, executable, function, macro, package, subdirectory | links | Detects CMake projects, add_library/add_executable targets, function/macro definitions, find_package, add_subdirectory. Target link dependencies. Optional: `pip install tree-sitter-cmake` |
| Make | [x] tree-sitter | variable, target, pattern_rule, special_target, function, include | depends_on | Detects Makefiles: variables, targets, pattern rules, .PHONY, define blocks, include directives. Prerequisite dependencies. Optional: `pip install tree-sitter-make` |
| VHDL | [x] tree-sitter | entity, architecture, package, component | implements | Detects VHDL hardware designs: entities, architectures, packages, component declarations. Architecture-entity relationships. Optional: `pip install tree-sitter-vhdl` |
| GraphQL | [x] tree-sitter | type, input, interface, enum, scalar, union, directive, fragment, query, mutation, subscription | — | Detects GraphQL schema definitions: object types, input types, interfaces, enums, scalars, unions, directives, fragments, operations. API schema analysis. Optional: `pip install tree-sitter-graphql` |
| Nix | [x] tree-sitter | function, binding, input, derivation | imports | Detects Nix expressions: named functions, let bindings, flake inputs, derivation calls. Import edges for `import` expressions. Optional: `pip install tree-sitter-nix` |
| GLSL | [x] tree-sitter | function, struct, uniform, input, output | calls | Detects OpenGL shaders: functions, structs, uniform/in/out variables. Function call edges. Supports .vert, .frag, .glsl, .geom, .tesc, .tese, .comp files. Optional: `pip install tree-sitter-glsl` |
| Fortran | [x] tree-sitter | module, program, function, subroutine, type | calls, imports | Detects Fortran code: modules, programs, functions, subroutines, derived types. Use statement imports, subroutine call edges. For scientific computing and HPC. Optional: `pip install tree-sitter-fortran` |
| TOML | [x] tree-sitter | table, package, dependency, binary, test, example, benchmark, library, workspace, project | — | Detects TOML configuration files: Cargo.toml (dependencies, bins, tests, examples, benches, libs, workspaces), pyproject.toml (project metadata). For Rust and Python project analysis. Optional: `pip install tree-sitter-toml` |
| CSS | [x] tree-sitter | import, variable, keyframes, media, font_face | imports | Detects CSS stylesheets: @import statements with import edges, CSS variables (--custom-props), @keyframes animations, @media queries, @font-face declarations. For frontend styling analysis. Optional: `pip install tree-sitter-css` |
| WGSL | [x] tree-sitter | function, struct, uniform, storage | calls | Detects WebGPU shaders: entry points (@vertex, @fragment, @compute), structs, uniform/storage bindings with @group/@binding metadata. For WebGPU graphics and compute analysis. Optional: `pip install tree-sitter-language-pack` |
| XML | [x] tree-sitter | module, dependency, activity, service, permission | depends_on | Maven pom.xml: projects, dependencies with groupId/artifactId/version. Android Manifest: activities, services, receivers, providers, permissions, intent-filters. For Java/Android analysis. Optional: `pip install tree-sitter-language-pack` |
| JSON | [x] tree-sitter | package, dependency, devDependency, script, tsconfig, reference, composer_package | depends_on, references | package.json: npm dependencies, scripts. tsconfig.json: TypeScript project references. composer.json: PHP Composer dependencies. For Node.js/PHP analysis. Optional: `pip install tree-sitter-language-pack` |
| R | [x] tree-sitter | function, import, source | calls | Detects R code: function definitions, library/require imports, source() file references. Function call edges. For data science and statistical computing. Optional: `pip install tree-sitter-language-pack` |
| Dart | [x] tree-sitter | class, function, method, constructor, getter, setter, enum, mixin, extension | calls, imports | Detects Dart code: classes, functions, methods (including getters/setters), constructors, enums, mixins, extensions, import statements. For Flutter and Dart web/server development. Optional: `pip install tree-sitter-language-pack` |
| COBOL | [x] tree-sitter | program, paragraph, section, data | calls (perform, call) | Detects COBOL programs: PROGRAM-ID declarations, paragraphs, sections in PROCEDURE DIVISION, data items in DATA DIVISION with level numbers. PERFORM edges for paragraph calls, CALL edges for external program calls. For mainframe and legacy systems. Optional: `pip install tree-sitter-language-pack` |
| LaTeX | [x] tree-sitter | section, label, command, environment | references, includes, imports | Detects LaTeX documents: sections/chapters, labels, custom commands (\\newcommand), custom environments (\\newenvironment). Reference edges for \\ref/\\cite, include edges for \\input/\\include, import edges for \\usepackage. For academic and technical documentation. Optional: `pip install tree-sitter-language-pack` |

## Supply Chain Classification (§8.6)

| Feature | Status | Notes |
|---------|--------|-------|
| `supply_chain.py` module | [x] | File classification by dependency position |
| Tier 4 detection (derived artifacts) | [x] | Path patterns + content heuristics (minification, source maps) |
| Tier 3 detection (external deps) | [x] | `node_modules/`, `vendor/`, etc. |
| Tier 2 detection (internal deps) | [x] | Workspace/monorepo detection from manifests |
| Tier 1 detection (first-party) | [x] | `src/`, `lib/`, `app/` patterns + default |
| Symbol fields (`supply_chain_tier`, `supply_chain_reason`) | [x] | Added to `ir.py` Symbol class |
| Node output (`supply_chain` object) | [x] | `tier`, `tier_name`, `reason` on each node |
| `supply_chain_summary` in output | [x] | File/symbol counts per tier |
| `by_supply_chain_tier` in metrics | [x] | Nodes/edges breakdown by tier |
| CLI `--max-tier` flag | [x] | Filter analysis scope by tier (on `run` command) |
| CLI `--first-party-only` flag | [x] | Shortcut for `--max-tier 1` (on `run` command) |
| Tier-weighted sketch ranking | [x] | First-party symbols prioritized in Key Symbols (2x weight) |
| CLI `--no-first-party-priority` flag | [x] | Disable tier weighting (on `sketch` command) |
| Slice tier filtering | [x] | `--max-tier` stops BFS at tier boundary |
| Capsule plan `supply_chain` config | [x] | `SupplyChainConfig` class with custom patterns for tiers |
| `limits.supply_chain` logging | [x] | `SupplyChainLimits` tracks classification_failures and ambiguous_paths |

## LLM-Friendly Output Modes

| Feature | Status | Notes |
|---------|--------|-------|
| **Compact Mode** | | |
| `--compact` flag on `run` | [x] | Coverage-based truncation with bag-of-words summarization |
| `--coverage` parameter | [x] | Target centrality coverage (0.0-1.0, default: 0.8) |
| `nodes_summary` in output | [x] | Included count/coverage + omitted word frequencies, path patterns, kinds |
| **Tiered Token-Based Output** | | |
| Default tiered files | [x] | Automatically generates `.4k.json`, `.16k.json`, `.64k.json` alongside full output |
| `--tiers` flag | [x] | Custom tier specs (e.g., `"2k,8k,32k"`) |
| `--tiers none` | [x] | Disable tiered output generation |
| `--tiers default` | [x] | Explicit default tiers (4k, 16k, 64k) |
| Token estimation | [x] | ~4 chars/token approximation for JSON |
| Centrality-based selection | [x] | Most important symbols selected first per budget |
| Tiered view format | [x] | `view: "tiered"`, `tier_tokens`, `nodes_summary` with included/omitted |
| Quality filtering | [x] | Excludes non-code kinds (dependency, devDependency, file, target, special_target, project, package, script, event_subscriber, class_selector, id_selector) and test/example paths |
| Test path filtering | [x] | Excludes test files: `/tests/`, `_test.go`, `.spec.ts`, `/testFixtures/`, `/intTest/`, `Tests.java`, etc. |
| Example path filtering | [x] | Excludes example/demo code: `/examples/`, `/demos/`, `/samples/`, `/playground/`, `/tutorial/` |
| Name deduplication | [x] | Prevents duplicate symbol names in tiers (e.g., multiple `push` methods) |

*Design: Full analysis always written to disk. Tiered files provide progressively larger views for LLMs with different context limits. Smaller tiers (4k) fit in most LLMs; larger tiers (64k) provide more detail for capable models.*

*Tested on: Django, Rails, Spring Boot, Laravel, Vue, Gin, Actix-web, Express, FastAPI, Flask. Quality filtering effectively removes 50-70% of test code while preserving core API symbols.*

## Re-export Resolution (§9.6)

Tracks implementation of re-export resolution per language. See [spec §9.6](docs/hypergumbo-spec.md#96-known-analysis-limitations) for details.

| Language | Re-export Pattern | Status | Notes |
|----------|-------------------|--------|-------|
| Python | `__init__.py`: `from .sub import x` | [x] | Aliases also supported |
| JavaScript | `index.js`: `export { x } from './x'` | [x] | Global name matching handles this |
| TypeScript | `index.ts`: `export { x } from './x'` | [x] | Same as JS |
| Rust | `lib.rs`: `pub use mod::item` | [x] | Global name matching handles this |
| Haskell | `module Foo (module Bar) where` | [x] | Global name matching handles this |
| OCaml | `include` in signatures | [x] | Global name matching handles this |
| Scala | `export` clauses (Scala 3) | [x] | Global name matching handles this |
| Elixir | `defdelegate` | [x] | Global name matching handles this |
| Dart | `export 'src/foo.dart'` | [x] | Global name matching handles this |
| Zig | `pub usingnamespace` | [x] | Global name matching handles this |

**Not affected:** Go, C, C++, Java, Kotlin, Swift, Ruby, PHP, Lua

## Cross-Language Linkers

Linkers run automatically as part of `hypergumbo run` after all language analyzers complete.

| Linker | Status | Edge Type | Symbols | Description |
|--------|--------|-----------|---------|-------------|
| JNI | [x] | native_bridge | — | Links Java native methods to C JNI implementations. Parses `Java_Package_Class_Method` naming convention. Runs when both Java and C symbols are present. |
| IPC | [x] | message_send, message_receive | ipc_send, ipc_receive | Detects Electron IPC (`ipcRenderer.send/invoke`, `ipcMain.on/handle`), Web Workers, and `postMessage` patterns. Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Channel stored in `edge.meta.channel` and `symbol.meta.channel`. |
| WebSocket | [x] | websocket_message, websocket_connection | websocket_endpoint, file | Detects Socket.io (`socket.emit`, `socket.on`, `io.emit`), native WebSocket (`new WebSocket`, `ws.send`), Node.js ws package, Django Channels (`channel_layer.send`, `group_send`, `WebsocketConsumer`), and FastAPI/Starlette (`@app.websocket`, `websocket.receive_json`, `websocket.send_json`, `websocket.accept`) patterns. Creates file symbols enabling slice traversal across WebSocket boundaries. Event matching links senders to receivers. Cross-language linking between Python and JavaScript. |
| IPC (Phoenix) | [x] | message_send, message_receive | ipc_send, ipc_receive | Detects Phoenix Channel patterns (`broadcast!`, `push`, `handle_in`) and LiveView patterns (`handle_event`, `push_event`). Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Event matching links senders to receivers. |
| Swift/ObjC | [x] | imports | objc_bridge, selector_ref | Detects Swift/Objective-C interop: `@objc` annotations, NSObject subclasses, `#selector()` references, and `*-Bridging-Header.h` imports. Enables slice traversal across Apple platform language boundaries. |
| gRPC | [x] | grpc_calls | grpc_service, grpc_servicer, grpc_stub, grpc_client, grpc_server | Detects gRPC/Protobuf patterns across Python, Go, Java, TypeScript. Parses `.proto` service definitions, servicer implementations, stub/client usage. Links clients to servers by service name. |
| HTTP | [x] | http_calls | http_client | Links HTTP client calls to server route handlers across languages. Detects `fetch()`, `axios`, `requests`, `httpx`, and OpenAPI-generated TypeScript client (`__request()`) patterns. Matches URLs to route patterns (supports `:id`, `{id}`, `<id>` parameters). Router prefixes (FastAPI `APIRouter`, Flask `Blueprint`) are combined with route paths for accurate matching. Enables full-stack call graph traversal. |
| GraphQL | [x] | graphql_calls | graphql_client | Links GraphQL client queries to schema definitions. Detects `gql` template literals (JS/TS), `gql()` function calls (Python). Extracts operation names and types (query, mutation, subscription). Enables full-stack GraphQL traversal. |
| Message Queue | [x] | message_queue | mq_publisher, mq_subscriber | Links message queue publishers to subscribers across languages. Detects Kafka (`producer.send()`, `consumer.subscribe()`, `@KafkaListener`), RabbitMQ (`basic_publish()`, `basic_consume()`, `sendToQueue()`), AWS SQS (`send_message()`, `receive_message()`), and Redis Pub/Sub (`publish()`, `subscribe()`) patterns. Topic-based matching enables cross-language microservices graph traversal. |
| GraphQL Resolver | [x] | resolver_implements, resolver_for_type | graphql_resolver | Links GraphQL resolver implementations to schema definitions. Detects JavaScript patterns (`Query: { users: () => ... }`), Python Ariadne (`@query.field("users")`), and Python Strawberry (`@strawberry.field`). Enables full-stack GraphQL traversal from client to resolver. |
| Database Query | [x] | query_references | db_query | Links SQL queries in application code to table definitions in SQL schema files. Detects Python (`cursor.execute()`, `db.execute()`, `session.execute(text())`), JavaScript (`db.query()`, `pool.query()`, `knex()`), and Java (`statement.executeQuery()`, `@Query()`) patterns. Extracts table names from SELECT/INSERT/UPDATE/DELETE/JOIN clauses. Cross-language linking enables full-stack database understanding. |
| Event Sourcing | [x] | event_publishes | event_publisher, event_subscriber | Links event publishers to subscribers across languages. Detects JavaScript EventEmitter (`emitter.emit()`, `emitter.on()`), DOM events (`addEventListener()`, `dispatchEvent()`), Django signals (`signal.send()`, `@receiver()`), Python event buses (`EventBus.publish()`, `EventBus.subscribe()`), and Spring events (`applicationEventPublisher.publishEvent()`, `@EventListener`). Topic/event name matching enables cross-language event tracing. |

## Test Infrastructure

| Feature | Status | Notes |
|---------|--------|-------|
| Test escape hatch removal | [x] | ADR 0002: Tests no longer skip when dependencies unavailable. All tree-sitter packages listed in `pyproject.toml`. |
| CI debugging tools | [x] | `./scripts/ci-debug` for Forgejo Actions troubleshooting. Commands: `runs`, `status`, `analyze-deps`. |
| Source-only grammar builds | [x] | `./scripts/build-source-grammars` builds tree-sitter-lean and tree-sitter-wolfram from source in CI. Adds ~30s to CI time. |
| Pytest warning filters | [x] | `pyproject.toml` filters expected test warnings (tree-sitter unavailability from mocked tests, API deprecations). |

## Release Pipeline

| Feature | Status | Notes |
|---------|--------|-------|
| **Workflow** | | `.github/workflows/release.yml` |
| Tag-triggered releases | [x] | Push `v*` tag to trigger release |
| Manual dispatch | [x] | Workflow dispatch with version + dry_run inputs |
| Dry run mode | [x] | Skip PyPI publish and Forgejo release creation |
| **Test Matrix** | | |
| Python 3.10 | [x] | Minimum supported version |
| Python 3.11 | [x] | |
| Python 3.12 | [x] | |
| Python 3.13 | [x] | Latest Python version |
| **Security Audit** | | |
| pip-audit | [x] | Dependency vulnerability scanning (`--skip-editable`) |
| Bandit | [x] | Security linting |
| Safety | [x] | Advisory check (non-blocking) |
| pip-licenses | [x] | License audit, warns on copyleft |
| trufflehog | [x] | Secret scanning |
| **Integration Tests** | | |
| Quick mode | [x] | `./scripts/integration-test --quick` |
| Real repo testing | [x] | Express, Gin, Flask |
| **Build & Publish** | | |
| Wheel build | [x] | `python -m build` |
| Source distribution | [x] | Included in build |
| SHA256 checksums | [x] | `dist/SHA256SUMS` |
| SBOM generation | [x] | CycloneDX format (`dist/sbom.json`) |
| Wheel verification | [x] | `pip install --dry-run` + `twine check` |
| PyPI publish | [x] | Via twine with `PYPI_TOKEN` secret |
| Forgejo release | [x] | Via API with `FORGEJO_TOKEN` secret |
| Changelog extraction | [x] | Auto-extracts version section for release notes |
| Asset upload | [x] | Wheel, tarball, checksums, SBOM |
| **Documentation** | | |
| Release SOP | [x] | `docs/RELEASE_SOP.md` |

---

*Last updated: 2025-12-29*
