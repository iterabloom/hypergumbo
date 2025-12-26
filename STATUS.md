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
| Entrypoint detection heuristics | [x] | `entrypoints.py` - FastAPI, Flask, Click, Electron, Django, Express.js, NestJS, Spring Boot, Rails, Phoenix, Go (Gin/Echo/Fiber), Laravel, Rust (Actix-web/Axum/Rocket/Warp), ASP.NET Core, Sinatra, Ktor, Vapor, Plug, Hapi, Fastify, Koa, Grape, Tornado, Aiohttp, Slim, Micronaut. Test files excluded via `_is_test_file()` helper. |
| Feature generation with query specs | [x] | Stable feature IDs from query |
| Slice IDs and reproducibility | [x] | `sha256(json.dumps(query))` |

## Week 5: Capsule Initialization

| Feature | Status | Notes |
|---------|--------|-------|
| `hypergumbo init` command | [x] | Creates `.hypergumbo/capsule.json` + `capsule_plan.json` |
| Template-based plan generation | [x] | `plan.py` - generates from profile + catalog |
| LLM-assisted plan generation | [x] | `llm_assist.py` - OpenRouter, OpenAI, llm package backends. *Proof-of-concept; template-based generation currently produces equivalent results.* |
| `hypergumbo catalog` command | [x] | Lists passes and packs |
| `hypergumbo export-capsule` command | [x] | `export.py` - tarball with privacy redactions |

## Sketch Generation (Default Mode)

| Feature | Status | Notes |
|---------|--------|-------|
| Token-budgeted Markdown sketch | [x] | `sketch.py` - ~4 chars/token heuristic |
| Default CLI mode | [x] | `hypergumbo [path]` runs sketch |
| Token limit flag | [x] | `-t N` / `--tokens N` |
| Language breakdown | [x] | Sorted by LOC percentage |
| Directory structure | [x] | Top-level dirs with type labels |
| Framework detection | [x] | Via profile.py. **Python:** FastAPI, Flask, Django, PyTorch, TensorFlow, Keras, Transformers, LangChain, LlamaIndex, Haystack, scikit-learn, MLflow, OpenAI, Anthropic. **JavaScript:** React, Vue, Angular, Express, NestJS, Next.js, Nuxt, Svelte. **Rust:** Axum, Actix-web, Tokio, Solana/Anchor, Substrate, ethers-rs, Arkworks, Halo2, Plonky2/3, SP1, RISC Zero, Nova, Zcash, libp2p. |
| Section-boundary truncation | [x] | Preserves coherent sections when truncating |
| Source file listings | [x] | Progressive expansion based on budget |
| Entry points section | [x] | CLI, HTTP routes, Electron patterns |
| Key symbols section | [x] | Functions/classes from static analysis |
| Graph centrality ranking | [x] | In-degree centrality orders symbols by importance |
| Test file filtering | [x] | Excludes test files from centrality calculation |

## CLI Commands

| Command | Status | Description |
|---------|--------|-------------|
| `hypergumbo [path] [-t N] [-x]` | [x] | Default sketch mode with optional token budget |
| `hypergumbo sketch [path] [-t N] [-x]` | [x] | Explicit sketch command |
| `-x` / `--exclude-tests` | [x] | Skip test files during analysis (17% faster on large codebases) |
| `hypergumbo --version` | [x] | Print version |
| `hypergumbo init [path]` | [x] | Initialize capsule |
| `hypergumbo run [path]` | [x] | Run analysis |
| `hypergumbo slice --entry X` | [x] | Produce reduced slice |
| `hypergumbo catalog` | [x] | List passes/packs |
| `hypergumbo export-capsule` | [x] | Export shareable capsule |
| `hypergumbo routes` | [x] | Display API routes (FastAPI, Flask, Django/DRF, Express.js, Koa, Fastify, NestJS, Rails). Shows HTTP methods, route paths, and handler functions |
| `hypergumbo search <query>` | [x] | Search symbols by name pattern |

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

## Analysis Passes

| Language | Parser | Symbols | Edges | Notes |
|----------|--------|---------|-------|-------|
| Python | [x] AST | function, class, method | calls, imports, instantiates | Two-pass cross-file resolution. Detects `self.method()`, `ClassName()` instantiation. Methods named with class prefix (`ClassName.methodName`). **Route detection:** FastAPI (`@app.get`, `@router.post`), Flask (`@app.route`, `@app.get`), Django REST Framework (`@api_view(['GET', 'POST'])`), and Django CBV methods (get/post/put/patch/delete) set `stable_id` to HTTP method for `routes` command discovery. |
| HTML | [x] regex | file | script_src | Script tag detection |
| JavaScript | [x] tree-sitter | function, class, method, getter, setter | calls, imports, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) handlers set `stable_id` to HTTP method. Optional: `pip install hypergumbo[javascript]` |
| TypeScript | [x] tree-sitter | function, class, method, getter, setter, interface, type, enum | calls, imports, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `obj.method()`, `new ClassName()`. **Route detection:** Express.js, Koa, Fastify (`app.get`, `router.post`) and NestJS decorators (`@Get()`, `@Post()`) set `stable_id` to HTTP method. Optional: `pip install hypergumbo[javascript]` |
| Svelte | [x] tree-sitter | function, class, method | calls, imports, instantiates | Extracts `<script>` blocks, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]` |
| PHP | [x] tree-sitter | function, class, method | calls, instantiates | Two-pass cross-file resolution. Detects `$this->method()`, `$obj->method()`, `ClassName::method()`, `new ClassName()`. Optional: `pip install hypergumbo[php]`. Excludes `vendor/` by default |
| C | [x] tree-sitter | function, struct, enum, typedef | calls | Two-pass cross-file resolution. Detects function calls, JNI export patterns (`Java_ClassName_methodName`). Optional: `pip install hypergumbo[c]` |
| Java | [x] tree-sitter | class, interface, enum, method, constructor | calls, extends, implements, instantiates | Two-pass cross-file resolution. Detects `this.method()`, `ClassName.method()`, inheritance, `new ClassName()`. Native method detection with `meta.is_native`. Optional: `pip install hypergumbo[java]` |
| Vue | [x] tree-sitter | function, class, method | calls, imports, instantiates | Extracts `<script>` and `<script setup>` blocks from `.vue` SFCs, adjusts line numbers. Two-pass cross-file resolution. Optional: `pip install hypergumbo[javascript]` |
| Elixir | [x] tree-sitter | module, function, macro | calls, imports | Detects `def/defp`, `defmodule`, `use/import/alias`. Two-pass cross-file resolution. Optional: `pip install hypergumbo[elixir]` |
| Rust | [x] tree-sitter | function, struct, enum, trait, method | calls, imports | Detects `fn`, `struct`, `enum`, `trait`, `impl` blocks, `use` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[rust]` |
| Go | [x] tree-sitter | function, method, struct, interface, type | calls, imports | Detects `func`, methods with receivers, `type X struct/interface`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[go]` |
| Ruby | [x] tree-sitter | method, class, module, route | calls, imports | Detects `def`, `class`, `module`, `require/require_relative`. **Route detection:** Rails DSL (`get '/path'`, `post '/path'`, `resources :name`) creates route symbols with `stable_id` = HTTP method. Two-pass cross-file resolution. Optional: `pip install hypergumbo[ruby]` |
| Kotlin | [x] tree-sitter | function, class, object, interface, method | calls, imports | Detects `fun`, `class`, `object`, `interface`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[kotlin]` |
| Swift | [x] tree-sitter | function, class, struct, protocol, enum, method | calls, imports | Detects `func`, `class`, `struct`, `protocol`, `enum`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[swift]` |
| Scala | [x] tree-sitter | function, class, object, trait, method | calls, imports | Detects `def`, `class`, `object`, `trait`, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[scala]` |
| Lua | [x] tree-sitter | function, method | calls, imports | Detects `function`, `local function`, method-style `Table:method()`, `require()` imports. Two-pass cross-file resolution. Optional: `pip install hypergumbo[lua]` |
| Haskell | [x] tree-sitter | function, data, class, instance | calls, imports | Detects functions (with/without type signatures), data types, type classes, instances, `import` statements. Two-pass cross-file resolution. Optional: `pip install hypergumbo[haskell]` |
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

## Cross-Language Linkers

Linkers run automatically as part of `hypergumbo run` after all language analyzers complete.

| Linker | Status | Edge Type | Symbols | Description |
|--------|--------|-----------|---------|-------------|
| JNI | [x] | native_bridge | — | Links Java native methods to C JNI implementations. Parses `Java_Package_Class_Method` naming convention. Runs when both Java and C symbols are present. |
| IPC | [x] | message_send, message_receive | ipc_send, ipc_receive | Detects Electron IPC (`ipcRenderer.send/invoke`, `ipcMain.on/handle`), Web Workers, and `postMessage` patterns. Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Channel stored in `edge.meta.channel` and `symbol.meta.channel`. |
| WebSocket | [x] | websocket_message, websocket_connection | websocket_endpoint, file | Detects Socket.io (`socket.emit`, `socket.on`, `io.emit`), native WebSocket (`new WebSocket`, `ws.send`), and Node.js ws package patterns. Creates file symbols enabling slice traversal across WebSocket boundaries. Event matching links senders to receivers. |
| IPC (Phoenix) | [x] | message_send, message_receive | ipc_send, ipc_receive | Detects Phoenix Channel patterns (`broadcast!`, `push`, `handle_in`) and LiveView patterns (`handle_event`, `push_event`). Creates symbols for each endpoint enabling slice traversal across IPC boundaries. Event matching links senders to receivers. |
| Swift/ObjC | [x] | imports | objc_bridge, selector_ref | Detects Swift/Objective-C interop: `@objc` annotations, NSObject subclasses, `#selector()` references, and `*-Bridging-Header.h` imports. Enables slice traversal across Apple platform language boundaries. |
| gRPC | [x] | grpc_calls | grpc_service, grpc_servicer, grpc_stub, grpc_client, grpc_server | Detects gRPC/Protobuf patterns across Python, Go, Java, TypeScript. Parses `.proto` service definitions, servicer implementations, stub/client usage. Links clients to servers by service name. |

---

*Last updated: 2025-12-26*
