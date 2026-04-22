<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Linkers

Linkers are Tier 2 passes that recover edges the language analyzers could not see statically. They run automatically during `hypergumbo run` after all analyzers complete, consuming the combined symbol graph and emitting new edges.

## Subcategories

Per [ADR-0003 §2.4](adr/0003-architectural-analysis-and-revision-plan.md) and the extension in [ADR-0003-ext: Linker Subcategory Restoration](adr/0003-linker-subcategory-restoration.md), every linker falls into one of four subcategories:

- **Protocol** — framework-agnostic pattern matching (URL paths, SQL table names, message topics, event names, developer annotations). Activates regardless of detected frameworks.
- **Bridge** — language-pair-specific FFI or runtime-bridging conventions. Activates when both languages of the pair are present (e.g., JNI activates when Java + C are both detected).
- **Framework** — framework-specific dispatch conventions (DI containers, decorator registries, ORM method dispatch, UI-component composition). Activates when the relevant framework is detected.
- **Infrastructure** — graph-structural utilities that populate skeletal relationships (`contains`, `extends`, `depends_on_manifest`, module-import resolution) for other linkers and downstream queries to consume. Not dispatch recovery.

Prioritisation of linker investment follows `INV-nimuj`: rank by expected false-positive reduction on the current prospector corpus, not by novelty of language pair.

## Linker Catalogue

| Linker | Subcategory | Description |
|--------|-------------|-------------|
| annotation_convention | Protocol | Developer-provided comment directives (`@hg:dispatches`, `@hg:publishes`, `@hg:subscribes`) for cases where pattern-matching alone cannot recover the edge. |
| build_target | Infrastructure | Build manifest files (`Dockerfile`, `Makefile`, entry scripts) → `main()` function or equivalent entry point. |
| cgo | Bridge | Go `C.funcName()` calls via `import "C"` pseudo-package ↔ C/C++ function implementations. |
| containment | Infrastructure | Class / interface symbols → their member method / getter / setter symbols via `contains` edges. Consumed by slice, method-call-recovery, and other linkers that assume structural containment. |
| crypto_flow | Protocol | Cryptographic API call sites (WebCrypto in JS/TS, `aes-gcm` in Rust, similar in others) connected by key-material and ciphertext flow. |
| database_query | Protocol | SQL queries embedded in application code (Python, JavaScript, Java) ↔ table definitions in schema files. |
| decorator_dispatch | Framework | Decorator-based registries (`@register_analyzer("go")`, Flask `@app.route`, Click `@command`) → dispatch call sites that iterate the registry. |
| dependency | Infrastructure | Manifest dependency declarations (`Cargo.toml`, `pyproject.toml`, `package.json`) → code imports that consume them. |
| di_resolution | Framework | Interface methods → DI-bound implementation methods via `di_resolves` edges. Supports Guice, Spring `@Bean`, ASP.NET Core DI, NestJS/Angular, InversifyJS, Koin, Python `injector`, Java SPI. Heuristic fallbacks for single-impl and naming conventions. |
| event_sourcing | Protocol | Event publishers (`EventEmitter.emit`, Django signals, Spring `@EventListener`) → subscribers by event name. |
| go_cobra | Framework | Cobra `cobra.Command{Run: handler}` struct-literal dispatch → `RunE`/`PreRunE` handler functions. |
| go_memberlist | Framework | HashiCorp `memberlist.Create(delegate)` call sites → `NodeMeta` / `NotifyJoin` / etc. delegate callback methods. |
| graphql | Framework | `gql` client queries / mutations → GraphQL schema type definitions. |
| graphql_resolver | Framework | Resolver implementations (JS, Python) → GraphQL schema type / field definitions. |
| grpc | Framework | Protobuf services, generated stubs, and servicer implementations across languages. Cross-language in use, but framework-specific by protocol. |
| http | Protocol | `fetch()`, `axios`, `requests`, `http.Get`, `RestTemplate`, etc. HTTP client calls → server route handlers via URL pattern matching. |
| inheritance | Infrastructure | `base_classes` analyzer metadata → `extends` / `implements` edges. Shared across all languages. |
| ipc | Protocol | Electron `ipcRenderer` / `ipcMain`, Web Workers, `postMessage` patterns. Publisher → handler by channel name. |
| jni | Bridge | Java `native` method declarations ↔ C / C++ / Rust JNI function implementations (`Java_ClassName_methodName` naming convention). |
| js_module | Infrastructure | JS / TS `import` path → resolved file. Creates `module_file` symbols and cross-file edge chains. |
| lua_ffi | Bridge | LuaJIT FFI (`ffi.C.funcname`, `ffi.load`) ↔ C function implementations. |
| message_dispatch | Protocol | Typed wire-protocol messages (JS/TS object discriminators, Rust `#[serde(tag)]` enums) — senders ↔ handlers by message-type name. |
| message_queue | Protocol | Kafka, RabbitMQ, SQS, Redis Pub/Sub — publishers ↔ subscribers by topic / queue name. |
| method_call_recovery | Protocol | Recovers `Class().method()` chained-call edges at the IR level. Language-agnostic post-analysis rewrite consuming the `unresolved-call` convention emitted by JS/TS, Java, Kotlin, Python, and Go analyzers. |
| middleware_chain | Framework | Consecutive middleware symbols in the same file, ordered by source line. Works with 58+ middleware-concept-tagged framework patterns (Flask, Django, FastAPI, Go, Rails, etc.). |
| napi | Bridge | JavaScript / TypeScript calls ↔ C / C++ addon functions registered via Node-API or node-addon-api. |
| openapi | Framework | OpenAPI / Swagger spec files → route handlers in the same application code via operationId / path matching. |
| orm | Framework | ORM query patterns (Django, Flask-SQLAlchemy, ActiveRecord) → `Model` symbols via `model_reference` edges. |
| otp | Framework | Elixir / Erlang `GenServer.call` / `GenServer.cast` call sites → `handle_call` / `handle_cast` handler functions. |
| phoenix_ipc | Framework | Phoenix Channels (`broadcast!`, `push`, `handle_in`) and LiveView event dispatch in Elixir. |
| pyffi | Bridge | Python `ctypes` / `cffi` calls, PyO3 bindings ↔ C / C++ / Rust function implementations. |
| react_component | Framework | JSX `<ComponentName />` usage → component definitions via `renders_component` edges. |
| route_handler | Framework | Route symbols → handler functions using framework-specific resolution (Rails, Phoenix, Laravel, Express, Django). |
| ruby_ffi | Bridge | Ruby `FFI.attach_function` / `rb_define_method` C extension registrations ↔ C / C++ function implementations. |
| solidity_abi | Bridge | TypeScript / JavaScript contract calls via `ethers.js` / `viem` ↔ Solidity function implementations via ABI function-name matching. |
| subprocess_cli | Protocol | `subprocess.run` / `subprocess.call` / `Popen` invocations (Python) → CLI command entry points (Click, Typer, argparse) in the same repository. |
| swift_objc | Bridge | Swift ↔ Objective-C interop via `@objc` annotations, `NSObject` subclasses, `#selector()`, bridging headers. |
| tauri_ipc | Bridge | TypeScript / JavaScript `invoke()` call sites ↔ Rust functions annotated with `#[tauri::command]`. |
| type_hierarchy | Framework | Interface / abstract-class methods → concrete implementations via `dispatches_to` edges. Polymorphic dispatch resolution. |
| view_template | Framework | Rails-style controller / action → rendered template by convention. |
| vue_component | Infrastructure | Vue `import` paths → `.vue` component files, establishing composition edges. |
| vue_template_method | Framework | Vue template event-handler directives → script methods via `handler_expression` metadata matching within the same file. |
| wasm_bindgen | Bridge | JavaScript / TypeScript imports from a wasm-pack `pkg/` directory ↔ Rust functions annotated with `#[wasm_bindgen]`. |
| websocket | Protocol | Socket.io, native WebSocket, Django Channels, FastAPI WebSocket — senders ↔ receivers by event name. |
| yjs_crdt | Framework | Yjs shared-type reactive data flow — writers → observers via `crdt_publishes` edges. |

**Count:** 45 linkers — Protocol 13, Bridge 10, Framework 16, Infrastructure 6.

Subcategory assignments above are the initial baseline per ADR-0003-ext Appendix B; borderline cases (e.g., `grpc` is framework-specific in protocol but cross-language in use) are documented in that ADR's appendix and will be refined as the subcategory vocabulary matures.

## How Linkers Work

Linkers operate on the combined output of all language analyzers:

1. **Receive** the `LinkerContext` (symbols, edges, captured_symbols) from all Tier 1 analyzers.
2. **Match** using the linker's specific recovery strategy (regex on call sites, metadata comparison, structural traversal, manifest parsing, etc.).
3. **Emit** new `Edge` objects with subcategory-appropriate evidence types (`http_url_match`, `jni_naming_convention`, `di_resolves`, `dispatches_to`, etc.).
4. **Tag provenance** — each edge records its `origin` (which linker) and `origin_run_id` so downstream slice / explain / dead-code passes can distinguish analyzer-emitted from linker-recovered edges.

For example, the HTTP linker (Protocol subcategory):
- Scans for `fetch("/api/users")` calls in JavaScript.
- Scans for `@app.get("/api/users")` route handlers in Python (or equivalent in other languages via the YAML framework-pattern system).
- Emits an `http_calls` edge from the client call site symbol to the server handler symbol.

## Why Linkers Matter

Modern applications use dispatch mechanisms that analyzers cannot see statically:

- **Cross-language boundaries** — TypeScript frontend calling Python API endpoints (HTTP — Protocol subcategory), Java service calling C performance-critical code (JNI — Bridge subcategory), TypeScript UI sending IPC to a Rust back-end (Tauri IPC — Bridge subcategory).
- **Within-language framework dispatch** — a Spring controller resolved via `@Autowired` (DI Resolution — Framework subcategory), a Django URL configuration routing to a view (Route Handler — Framework subcategory), a React component mounted via JSX (React Component — Framework subcategory), a Kafka Streams topology dispatching to a `ValueMapper.apply` implementation at runtime.
- **Graph-structural relationships** — class-to-method containment (Containment — Infrastructure), inheritance hierarchies (Inheritance — Infrastructure), module import resolution (js_module — Infrastructure).

Without these edges, a slice or dead-code analysis sees isolated subgraphs (per language, per framework, per dispatch mechanism) and over-reports false-positive dead code. The within-language framework-dispatch subcategories are empirically the dominant false-positive driver — see WI-tubot's 2026-04-11 prospector run for the volume distribution, and INV-nimuj for the prioritisation implication.

## Adding a New Linker

See `packages/hypergumbo-core/src/hypergumbo_core/linkers/` for examples. Two requirements:

1. **Declare the subcategory in the module docstring.** The opening sentence of the file's top-level docstring must be of the form:
   ```python
   """Protocol linker: <one-line purpose>.

   <rest of docstring...>
   """
   ```
   One of `Protocol`, `Bridge`, `Framework`, or `Infrastructure`.

2. **Register via decorator.** Activation conditions follow the subcategory:
   ```python
   from hypergumbo_core.linkers.registry import register_linker, LinkerContext, LinkerResult, LinkerActivation

   @register_linker(
       "myprotocol",
       priority=50,
       activation=LinkerActivation(always=True),  # Protocol: always; Bridge: language_pairs=[...]; Framework: frameworks=[...]
   )
   def link_myprotocol(ctx: LinkerContext) -> LinkerResult:
       # Recover edges, return as LinkerResult(edges=..., symbols=...)
       ...
   ```

3. **Add a row to the table above.** Cite any framework or language pair the linker targets.

## Future Work

### Subprocess Linker Extensions

The subprocess linker currently supports Python. Future extensions:

| Language | Patterns | Project Detection |
|----------|----------|-------------------|
| JavaScript | `child_process.spawn`, `execa`, `shelljs` | `package.json` bin |
| Go | `exec.Command`, `os.Exec` | `go.mod` module name |
| Rust | `std::process::Command` | `Cargo.toml [[bin]]` |
| Ruby | `system()`, `Open3`, backticks | Gemspec executables |

### Coverage Estimation Optimization

The transitive BFS for test coverage estimation currently builds an adjacency list on each call. For very large repos (>100K functions), consider:

- Caching the adjacency list if edges haven't changed
- Using integer IDs instead of string IDs for memory efficiency
- Providing a streaming/incremental mode for huge monorepos

### Framework-Subcategory Linker Pipeline

`INV-nimuj` ranks linker investment by false-positive-reduction volume. Current highest-value follow-ups in the Framework subcategory (see tracker): `WI-gupah` (Jackson / JavaBean serialisation reflection), `WI-nutav` (Airflow Hook / Sensor / Trigger / Operator dispatch), `WI-lisov` (Kafka Streams topology-builder). Each is a Framework-subcategory linker in the ADR-0003-ext sense — the dispatch is framework-injected within one language, even where the downstream effect crosses a language boundary.
