# Hypergumbo Spec

Status: living document.

## Implementation Status Legend

| Icon | Status | Meaning |
|------|--------|---------|
| ⬜ | todo | Planned, not yet started |
| 🟨 | in progress | Currently being worked on |
| 🟧 | blocked | Blocked on external dependency or decision |
| 🟩 | done | Implemented and tested |
| 🟪 | needs design | Requires more design thinking before implementation |
| ⬛ | won't do | Decided against / out of scope |

*Use `grep "🟨"` to find in-progress items, etc.*

## Table of Contents

| § | Title |
|---|-------|
| 0 | [One-sentence summary](#0-one-sentence-summary) |
| 1 | [Goals](#1-goals) |
| 2 | [Non-goals](#2-non-goals) |
| 3 | [User experience (CLI)](#3-user-experience-cli) |
| 4 | [Supported stacks](#4-supported-stacks) |
| 5 | [Architecture](#5-architecture) |
| 6 | [Internal representation](#6-internal-representation) |
| 7 | [Cross-language linkers](#7-cross-language-linkers) |
| 8 | [Entrypoint detection](#8-entrypoint-detection) |
| 9 | [Behavior map JSON](#9-behavior-map-json) |
| 10 | [Sketch output](#10-sketch-output) |
| 11 | [Slicing behavior](#11-slicing-behavior) |
| 12 | [Confidence scoring](#12-confidence-scoring) |
| 13 | [Output reproducibility](#13-output-reproducibility) |
| 14 | [Supply chain classification](#14-supply-chain-classification) |
| 15 | [File role classification](#15-file-role-classification) |
| 16 | [Testing & quality bar](#16-testing--quality-bar) |
| 17 | [Error handling](#17-error-handling) |
| 18 | [Known limitations](#18-known-limitations) |
| 19 | [Autonomous governance](#19-autonomous-governance-adr-0008) |
| 20 | [Future work](#20-future-work) |
| A | [Release lifecycle & support](#appendix-a-release-lifecycle--support) |
| B | [Telemetry & privacy](#appendix-b-telemetry--privacy) |
| C | [Schema compatibility contract](#appendix-c-schema-compatibility-contract) |
| D | [Capsule system history](#appendix-d-capsule-system-history) |

## 0) One-sentence summary
A local-first CLI that helps developers and AI agents understand an unfamiliar codebase by analyzing its structure and emitting a **repo behavior map**—a JSON graph of symbols, call edges, routes, and framework patterns with confidence scores and provenance tracking.

## 1) Goals
* 🟩 **Internal IR with views**: Parsers emit to an internal representation; public outputs are compiled views (enables future typed passes without breaking schema).
* 🟩 **Provenance tracking**: Every node/edge records which analyzer pass created it, with unique execution identifiers enabling quality assessment and mixed-fidelity analysis.
* 🟩 **Machine-readable provenance**: Confidence scores and edge evidence use structured fields (not human-readable strings). This enables programmatic filtering and multi-pass merging.
* 🟩 **Agent-ready output**: deterministic JSON graph + "feature slices" so an agent can fetch only relevant code.
* 🟩 **Fast iteration**: simple architecture, small dependency surface, fixtures-driven tests.
* 🟩 **Local-first execution**: analysis runs offline by default (no network, no API keys required).

For goals that were considered and rejected, see [Appendix D](#appendix-d-capsule-system-history).

## 2) Non-goals
* No deep type-resolution / interprocedural dataflow correctness guarantees.
* No accounts, ratings, or social features.
* No automatic PR fixing, no code editing, no CI annotations beyond "export JSON."
* No attempt to support every language *deeply*—broad coverage via tree-sitter (100+ languages; see [LANGUAGES.md](LANGUAGES.md)), deep call-graph extraction for a smaller set. See [§4 Supported stacks](#4-supported-stacks).
* No incremental analysis daemon (full re-analysis is acceptable).
* No LLM-generated analyzer code.

## 3) User experience (CLI)

**Key principle:** Analysis execution requires no network or API keys (by default). Output is deterministic and reproducible given the same repo state. See [Appendix B](#appendix-b-telemetry--privacy) for the full privacy and telemetry policy.

### Install
* `pipx install hypergumbo` (primary, includes all language analyzers)
* `pip install hypergumbo` (secondary)
* `pip install hypergumbo[embeddings]` (optional embedding-based config extraction)

### Commands

🟩 **`hypergumbo [path] [-t tokens]`** (default mode)
Generates a token-budgeted Markdown sketch to stdout. Optimized for pasting into LLM chat interfaces.
* If no subcommand is given, assumes sketch mode.
* `-t N` limits output to approximately N tokens.
* `--with-source` appends full source file contents after the sketch (ordered by symbol importance density, skips files under 5 LOC)

🟩 **`hypergumbo explain <symbol> [--with-source] [-t tokens] [-x]`**
Shows detailed info about a symbol (function, class, etc.) and its callers/callees.
* `--with-source` shows source code for the symbol, callers, and callees:
  - Symbol source shown first
  - "Called by" list, then caller sources (ordered by in-degree descending)
  - "Calls" list, then callee sources (ordered by in-degree descending)
  - Module-level calls show only the single call line
  - Deduplicates when same symbol appears as both caller and callee
* `-t N` limits source output to approximately N tokens. When budget exceeded, omits sources one-at-a-time in priority order: module-level first, then ascending in-degree (least important first)
* `-x` excludes callers/callees from test files

🟩 **`hypergumbo run [path] [--out hypergumbo.results.json]`**
Analyzes the repo and emits a behavior map. No initialization required—works directly on any repo.

🟩 **`hypergumbo slice --entry <symbol|file|route> [--out slice.<entry>.json]`**
Produces a reduced subgraph suitable for LLM context. Default output filename includes a sanitized entry name to prevent overwrites when slicing different symbols.

🟩 **`hypergumbo catalog [--show-all]`**
Shows available language analyzers and which ones are suggested for the current repo. Useful for discovering what hypergumbo can analyze.

🟩 **`hypergumbo test-coverage [path] [--format text|json]`**
Estimates test coverage via static analysis (no code execution). Reports hot spots (functions called by many tests, ranked by tests/LOC) and cold spots (untested functions). Filter with `--min-tests`, `--max-tests`, `--top`.

### Analysis options

These options apply to all analysis commands (`run`, `slice`, and default sketch mode).

🟩 **`--exclude PATTERN`**
Gitignore-style glob patterns for paths to skip. Uses `fnmatch` matching.
* Default excludes: `node_modules/`, `venv/`, `dist/`, `build/`, `*.min.js`, `*.bundle.js`, `.git/`, `__pycache__/`

🟩 **`--max-file-bytes N`** (default: no limit)
Skip files exceeding this size. Particularly useful for HTML and minified JavaScript.
* Skipped files logged in `limits.truncated_files[]`

🟩 **`--first-party-only`**
Analyze only first-party code (tier 1). Equivalent to `--max-tier 1`.

🟩 **`--max-tier N`** (default: 3)
Control which supply chain tiers are included in analysis. See [§14](#14-supply-chain-classification) for tier definitions.

🟩 **`--no-first-party-priority`**
Disable tier-based weighting in Key Symbols ranking (use raw centrality instead).

## 4) Supported stacks

Hypergumbo supports 100+ languages via tree-sitter grammars (see [LANGUAGES.md](LANGUAGES.md) for the full list). All are included in the base package.

**Primary languages (full symbol/edge extraction):**
* 🟩 **Python** (AST-based, full call edges)
* 🟩 **JavaScript/TypeScript** (tree-sitter, includes Svelte/Vue script block extraction)
* 🟩 **Java** (tree-sitter, Spring Boot framework patterns)
* 🟩 **Go** (tree-sitter, Gin/Echo/Chi/Fiber patterns)
* 🟩 **Rust** (tree-sitter, Actix/Axum/Rocket patterns)
* 🟩 **Ruby** (tree-sitter, Rails/Sinatra patterns)
* 🟩 **Elixir** (tree-sitter, Phoenix patterns)
* 🟩 **PHP** (tree-sitter, Laravel patterns)
* 🟩 **C/C++** (tree-sitter)
* 🟩 **C#** (tree-sitter, ASP.NET Core patterns)
* 🟩 **Kotlin/Scala/Swift** (tree-sitter)

**Additional languages (symbol extraction):**
* 🟩 Bash, Clojure, Dart, Elm, Erlang, F#, Fortran, Haskell, Julia, Lua, Nim, OCaml, Perl, R, Zig, and many more — see [LANGUAGES.md](LANGUAGES.md) for the full list

**Configuration/data formats:**
* 🟩 JSON, YAML, TOML, XML, HCL/Terraform, Dockerfile, Makefile, CMake, SQL, GraphQL, Protobuf, Thrift

**Markup:**
* 🟩 HTML (script tag extraction), CSS, LaTeX, Markdown

### Dependency strategy
* **All-in-one package**: `pip install hypergumbo` includes Python AST + tree-sitter grammars for all supported languages as standard dependencies (see [LANGUAGES.md](LANGUAGES.md) for the full list)
* **Grammar sources**: Most grammars are installed as individual PyPI packages (e.g., `tree-sitter-javascript`). A subset (Elixir, COBOL, Dart, LaTeX, R) come from `tree-sitter-language-pack`.
* **Build-from-source grammars**: Lean, Wolfram built from source in CI for languages lacking PyPI packages
* **Fallback**: If a specific grammar fails to load, that language is skipped with explicit `limits.skipped_languages[]` logging
* **Optional extras**:
  - `[embeddings]`: sentence-transformers for embedding-based config extraction

### Build strategy
* Tree-sitter grammars with PyPI wheels are installed directly as dependencies
* Grammars without PyPI packages (Lean, Wolfram) are built from source in CI (`scripts/build-source-grammars`)
* 100% test coverage required; analyzers gracefully skip when grammars unavailable

## 5) Architecture

For a file-by-file map of modules, see [ARCHITECTURE.md](ARCHITECTURE.md) (auto-generated by running hypergumbo on itself; run `./scripts/generate-architecture` to update).

### Analysis pipeline (two-tier model)

The analysis pipeline has two tiers reflecting different information needs.

**Terminology:** A *pass* is any analysis component that reads code and produces IR output. An *analyzer* is a Tier 1 pass that extracts symbols and edges for a single language. A *linker* is a Tier 2 pass that creates cross-language or cross-component relationships. The `pass_id` field in `AnalysisRun` uses the generic term.

**Tier 1 — Language analyzers (independent producers):**
Each analyzer is a plain function registered via the `@register_analyzer` decorator and discovered through Python entry-points (see [ADR-0010](adr/0010-modular-packages-and-smart-testing.md), [ADR-0012](adr/0012-pass-unification-and-multi-fidelity.md)):
```python
from hypergumbo_core.analyze.registry import register_analyzer

@register_analyzer("go", priority=50)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    ...
```
Each analyzer returns an `AnalysisResult` containing symbols, edges, and usage contexts — the data types defined in [§6 Internal representation](#6-internal-representation). Analyzers are embarrassingly parallel — each scans the repo independently and returns a bag of symbols and edges. They do not see each other's output.

**Tier 2 — Linkers and enrichment (context-dependent refiners):**
After all analyzers run, the orchestrator (`run_behavior_map`) collects the unified symbol graph and runs post-processing:
1. Deferred symbol reference resolution (cross-file call targets)
2. Framework pattern enrichment (YAML-driven concept metadata)
3. Cross-language linkers (registered via `@register_linker` decorator, receiving `LinkerContext` with the full symbol graph; see [LINKERS.md](LINKERS.md) for the full list)
4. Entrypoint detection

Linkers use a decorator-based registry (`linkers/registry.py`) and receive the accumulated analysis state:
```python
@register_linker("jni", requires=["c", "java"])
def link_jni(ctx: LinkerContext) -> LinkerResult:
    ...
```
🟪 Spec example above is outdated; code uses `activation=LinkerActivation(language_pairs=[("java","c"),("java","cpp")])` and `requirements=list[LinkerRequirement]` instead of `requires=list[str]`.

**🟪 Design target — Unified pass interface:**
For multi-fidelity analysis (e.g., a pyright pass refining AST-extracted edges with type-resolved information), both tiers would converge on a single interface where passes receive the IR and return deltas:
```python
class AnalysisPass(Protocol):
    id: str              # e.g., "python-ast-v1"
    version: str         # e.g., "hypergumbo-0.1.0"
    capabilities: list[str]  # e.g., ["python"]

    def run(self, ir: AnalysisIR, files: list[Path], config: Config) -> IRDelta: ...
```
This would allow a type-resolution pass to slot in between Tier 1 and Tier 2, reading AST-produced symbols and upgrading their edge confidences. Tier 1 analyzers would receive an empty IR (they remain independent); Tier 2 refiners would receive the accumulated graph. The orchestrator becomes generic — just iterating passes in priority order. See [ADR-0012](adr/0012-pass-unification-and-multi-fidelity.md) for the design rationale and migration path.

For the full catalog of language analyzers, cross-language linkers, and framework pattern files, see [LANGUAGES.md](LANGUAGES.md), [LINKERS.md](LINKERS.md), and [FRAMEWORKS.md](FRAMEWORKS.md).

## 6) Internal representation

Parsers emit to `AnalysisIR`:
```python
@dataclass
class Symbol:
    id: str                    # location-based identifier
    stable_id: Optional[str]   # semantic identity hash (signature-based)
    shape_id: Optional[str]    # structural implementation fingerprint
    canonical_name: str        # 🟪 code: Optional[str] = None
    fingerprint: str           # 🟪 code: Optional[str] = None
    kind: str                  # function, class, module, etc.
    name: str
    path: str
    language: str
    span: Span
    origin: str                # which pass created this
    origin_run_id: str         # references AnalysisRun.execution_id
    supply_chain_tier: int     # 1=first_party, 2=internal_dep, 3=external_dep, 4=derived
    supply_chain_reason: str   # classification rationale (e.g., "matches ^src/")
    # Note: In JSON output (§9 Behavior map JSON), these flat fields are compiled
    # into a nested supply_chain object with a derived tier_name field.
    origin_run_signature: Optional[str]  # references AnalysisRun.run_signature (for grouping)
    quality: QualityScore      # 🟪 QualityScore not defined; code: Optional[Dict[str, Any]] = None

@dataclass
class AnalysisRun:
    execution_id: str          # unique per run (uuid or hash of run_signature + started_at + repo_fingerprint)
    run_signature: str         # deterministic: hash of (pass_id, version, config_fingerprint, toolchain)
    repo_fingerprint: str      # hash of (git_head + dirty_files) or hash of (file_list + content_hashes)
    pass_id: str               # e.g., "python-ast-v1" (serialized as "pass" in JSON output)
    version: str               # e.g., "hypergumbo-0.1.0"
    toolchain: Dict            # {"name": "python", "version": "3.11.0"}
    config_fingerprint: str    # sha256 of effective config
    files_analyzed: int
    files_skipped: int
    skipped_passes: List[Dict] # passes that couldn't run
    warnings: List[str]
    started_at: str
    duration_ms: int

@dataclass
class AnalysisIR:
    runs: List[AnalysisRun]           # provenance: which passes ran
    symbols: List[Symbol]              # definitions (funcs, classes, etc)
    references: List[Reference]        # use sites
    relationships: List[Relationship]  # typed edges with quality scores
```
🟪 `AnalysisIR`, `Reference`, `Relationship` are spec names; code uses `AnalysisResult`, `Symbol`, `Edge`.

### Identity field semantics

* `id` (location-based): `{lang}:{file}:{start_line}-{end_line}:{name}:{kind}`
  - Changes when code moves to different file/line
  - Purpose: Reproducible slicing, deterministic diffs
* `stable_id` (semantic, optional): Interface identity (signature-based), **not implementation identity**
  - 🟩 **For typed languages or annotated Python**: `sha256({kind}:{normalized_signature}:{visibility}:{decorators}:{containing_module_stable_id})`
    - `normalized_signature`: Canonical type signature (param types, return type, type params), normalized per-language (strip FQN prefixes, normalize generic type params by position: `T,U` → `$0,$1`). Normalization is language-scoped — cross-language collision is structurally prevented by `containing_module_stable_id`. A cross-language canonical mapping table may be layered on top if a use case emerges (see ADR-0014 §3). Four normalization families: types-first (Java, C#, Dart, Groovy), names-first (Kotlin, Scala, Swift, Rust, TS, Python), PHP-specific, Go-specific.
    - `visibility`: public, private, protected (if language has concept)
    - `decorators`: Sorted, comma-joined decorator/annotation names
    - `containing_module_stable_id`: Recursive stable_id of parent module/class
    - **Excludes**: Implementation details, docstrings, comments
    - Implemented for 12 analyzers: Java, C#, Kotlin, Scala, Swift, Rust, Go, PHP, Groovy, JS/TS, Python, Dart
  - 🟩 **For untyped code**: `sha256({kind}:{parameter_count}:{arity_flags}:{decorator_presence}:{containing_module_stable_id})`
    - `arity_flags`: has_defaults, has_varargs, has_kwargs (structural signature info)
    - `decorator_presence`: Sorted list of decorator names (e.g., `["property", "staticmethod"]`)
    - **Excludes**: Source hash, canonical name (survives renames)
  - Purpose: Track symbols across refactors (renames, moves, documentation changes)
  - **Does NOT change** when: Renaming, moving between files, changing implementation, adding comments
  - **DOES change** when: Signature changes (param types, arity), visibility changes, decorators added/removed
* 🟩 `shape_id` (optional): Structural implementation fingerprint
  - `sha256(ast_structure)` excluding literals/identifiers
  - Purpose: Detect structural changes (control flow, nesting) without caring about variable names
  - Use case: "Implementation changed but signature stayed same"
  - 🟩 Python: implemented via `_compute_shape_id()` using Python's `ast` module
  - 🟩 Tree-sitter languages: implemented via generic CST walker in `TreeSitterAnalyzerBase.compute_shape_id()`. Any analyzer that populates `node_for_symbol` gets automatic shape_id computation. Currently 27 analyzers use this (Rust, Ruby, C#, Swift, Nim, Ada, Pascal, etc.).
* `fingerprint` (content hash): `sha256(source_bytes)`
  - Changes when implementation changes
  - Purpose: Detect modifications

**Example**:
```python
# Original:
def authenticate(username: str, password: str) -> User:
    ...

# After rename and move:
# File: auth_service.py → user_auth.py
# Function: authenticate → verify_credentials
# stable_id stays the same (signature unchanged)
# id changes (file and name changed)
# fingerprint changes if implementation changed
# shape_id changes if control flow changed
```

### Identity and provenance scheme versioning

The exact algorithms for identity and provenance fields are governed by scheme identifiers in the output:
* `stable_id_scheme`: identifies the algorithm/normalization used to compute `stable_id`
* `shape_id_scheme`: identifies the algorithm used to compute `shape_id`
* `repo_fingerprint_scheme`: identifies the algorithm used to compute `analysis_runs[].repo_fingerprint`

Any change that would alter computed values MUST bump the corresponding scheme identifier.

### Provenance field semantics

* `execution_id`: Unique identifier for this specific analysis run
  - Format: `uuid:` prefix for UUID v4, or `sha256:` for deterministic hash
  - Purpose: Track which specific run produced which nodes/edges
  - Enables: Correlation to repo snapshots, multi-run comparison
* `run_signature`: Deterministic fingerprint of analyzer configuration
  - Hash of (pass_id, version, config_fingerprint, toolchain)
  - Same pass + version + config + toolchain → same signature
  - Purpose: Cache keying, grouping results by analyzer version
* `repo_fingerprint`: Hash identifying the code snapshot analyzed
  - Git repos: `sha256(git_head + sorted([(path, sha256(content_bytes)) for each dirty file]))`
    - `git_head`: HEAD commit hash
    - `dirty file`: tracked file whose working tree content differs from HEAD OR untracked file included in analysis
    - Purpose: ensures repo_fingerprint changes when dirty file contents change, not just when paths change
  - Non-git: `sha256(sorted([(path, content_hash) for all files]))`
  - Purpose: Cache invalidation, provenance tracking

### Output views

Public outputs are **compiled views** from this IR — the IR defines the canonical data model, and each view selects and reshapes fields for its audience. See [§9](#9-behavior-map-json) and [§10](#10-sketch-output) for the available views and their serialization details.

**Design principle:** Strong passes (tsserver, pyright) added later will enhance the IR without breaking existing views.

## 7) Cross-language linkers

Hypergumbo provides **best-effort cross-language edge detection** for common integration patterns. These are AST-based heuristics with string literal matching, not type-resolved or dataflow analysis. This section specifies three linkers in detail (JNI, IPC, HTTP client-server); for the full catalog of 20+ cross-language linkers, see [LINKERS.md](LINKERS.md).

### JNI Boundary Detection (Java ↔ C)

Detects native method declarations in Java and matches them to C implementations via naming conventions.

**Java side detection:**
```java
public class GuacamoleSession {
    public native void processFrame(byte[] data);
}
```

**C side detection (matched by naming convention):**
```c
JNIEXPORT void JNICALL Java_GuacamoleSession_processFrame(
    JNIEnv *env, jobject obj, jbyteArray data)
```

**Detection rules:**
1. Find Java methods with `native` modifier
2. Find C functions matching `Java_{ClassName}_{methodName}` pattern (mangled names)
3. Emit `native_bridge` edge from Java method → C function

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* Pattern-matched (naming convention): 0.95
* 🟪 Annotation-confirmed (`@hypergumbo.jni_impl`): 0.98

**Limitations:**
* Does not resolve JNI calls through reflection
* Does not track `JNI_OnLoad` dynamic registration
* Does not handle inner classes (mangling includes `$`)
* ⬜ Logs unmatched natives in `limits.unresolved_jni[]` (field not yet implemented; see §7 limits.cross_language)

### IPC/Message Channel Detection

Detects message send/receive patterns across process boundaries using string literal matching on channel/event names.

**Supported patterns:**

| Framework | Send Pattern | Receive Pattern | Evidence Type |
|-----------|-------------|-----------------|---------------|
| Electron | `ipcRenderer.send("channel")` | `ipcMain.on("channel")` | `ipc_electron` |
| Electron | `ipcMain.handle("channel")` | `ipcRenderer.invoke("channel")` | `ipc_electron` |
| WebSocket | `ws.send({type: "X"})` | `ws.on("message", ...)` with type check | `ipc_websocket` |
| ⬜ Guacamole | `tunnel.sendMessage("opcode", ...)` | `oninstruction` handlers | `ipc_guacamole` |
| Node EventEmitter | `emitter.emit("event")` | `emitter.on("event")` | `ipc_eventemitter` |

**Detection algorithm:**
1. Parse AST for known send/receive function patterns
2. Extract channel/event name from string literal argument
3. Build index of all senders and receivers by channel name
4. Match senders to receivers with same channel name
5. Emit `message_send` edge (caller → channel) and `message_receive` edge (channel → handler)

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* String literal channel name match: 0.85
* 🟪 Variable/computed channel name: 0.50 (code uses flat 0.65 for all non-literal matches; no variable/template distinction)
* 🟪 Template literal with interpolation: 0.40 (see above)
* 🟪 Annotation-provided (`@hypergumbo.ipc_channel("name")`): 0.95

**Limitations:**
* Dynamic channel names require annotation hints
* Complex message routing (middleware, proxies) not traced
* Does not validate message schema compatibility
* ⬜ Logs unmatched patterns in `limits.unresolved_ipc[]` (field not yet implemented; see §7 limits.cross_language)

### HTTP client-server linking

🟩 The HTTP linker (`linkers/http.py`) matches client HTTP calls to server route handlers across languages. Route detection itself is handled by the YAML-driven pattern system (see [§8 Entrypoint detection](#8-entrypoint-detection) and [FRAMEWORKS.md](FRAMEWORKS.md)); this linker creates cross-language edges from client call sites to the detected server routes.

**Supported client patterns:**

| Language | Libraries | Example |
|----------|-----------|---------|
| JS/TS | `fetch`, `axios`, AngularJS `$http`, jQuery `$.ajax`/`$.get`/`$.post`, OpenAPI-generated clients | `fetch("/api/users")` |
| Python | `requests`, `httpx` | `requests.get("/api/users")` |
| Ruby | `RestClient`, `HTTParty`, `Faraday`, `Net::HTTP` | `RestClient.get("/api/users")` |
| Java | Spring `RestTemplate`, Retrofit annotations | `restTemplate.getForObject("/api/users", ...)` |
| Go | `net/http` | `http.Get("http://host/api/users")` |

**Matching algorithm:**
1. Collect server route symbols detected by the pattern system (see [§8](#8-entrypoint-detection))
2. Scan client code for HTTP call patterns with URL arguments
3. Match by HTTP method and URL path, with support for parameterized paths (`:id`, `{id}`, `<id>`)
4. Emit `http_calls` edge from client call site to matching route handler

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* Literal URL match: 0.90
* Variable/computed URL: 0.65

### DI resolution linking

🟩 The DI resolution linker (`linkers/di_resolution.py`) creates `di_resolves` edges from interface methods to their DI-bound implementation methods. Unlike `dispatches_to` (which is structural and excluded from forward slices to prevent fan-out explosion), `di_resolves` edges are followed by forward BFS — correct for DI-heavy codebases where the binding narrows to one high-confidence implementation.

**Supported DI frameworks:**
- Java/Kotlin/Scala: Guice `bind(X.class).to(Y.class)`, `@Provides` methods, `@ImplementedBy` annotations, Spring `@Bean` methods
- C#: ASP.NET Core `services.AddScoped<I, C>()` / `AddTransient` / `AddSingleton`
- TypeScript: NestJS/Angular `{ provide: X, useClass: Y }`, InversifyJS `container.bind<I>().to(C)`
- Python: `binder.bind(I, to=C)` (injector library)
- Kotlin: Koin `single<I> { Impl() }`
- Java SPI: `META-INF/services/` files

**Resolution cascade** (highest-confidence wins):
1. Explicit framework binding (Guice bind/provides/Spring/C#/NestJS/Inversify/Koin/Python injector): 0.90
2. Guice `@ImplementedBy` annotation: 0.85
3. Java SPI `META-INF/services/` file: 0.85
4. Naming convention (`DefaultX` or `XImpl`): 0.75
5. Single implementation of interface: 0.70

Edges are created at method level (interface method → implementation method with matching short name), not at class level.

### Language-specific notes for cross-language linking

The C analyzer detects JNI export patterns (`JNIEXPORT`, `JNICALL`, `Java_*` naming) and the Java analyzer detects `native` method declarations, both feeding into the JNI linker above. For full per-language analyzer capabilities, see [LANGUAGES.md](LANGUAGES.md).

### limits.cross_language — tracking unresolved links

🟪 Cross-language linkers log unresolved patterns for debugging (not yet implemented — `limits` dataclass has no `cross_language`, `unresolved_jni`, or `unresolved_ipc` fields):

```json
{
  "limits": {
    "cross_language": {
      "unresolved_jni": [
        {
          "java_method": "com.example.Native.processData",
          "expected_c_name": "Java_com_example_Native_processData",
          "reason": "no_matching_c_function"
        }
      ],
      "unresolved_ipc": [
        {
          "channel": "user.login",
          "senders": ["src/client/auth.js:45"],
          "receivers": [],
          "reason": "no_receiver_found"
        }
      ]
    }
  }
}
```

## 8) Entrypoint detection

Entrypoint detection identifies HTTP handlers, CLI mains, background tasks, and other entry sources for slicing. Detection is **YAML-driven** via the framework patterns system.

### Architecture

```
ANALYZERS (pure language, no framework knowledge)
  → Capture symbols + rich metadata (decorators, base classes, parameters)
  → Capture UsageContext for call-based patterns (route registrations, etc.)

PATTERN SYSTEM (YAML files: convention + framework)
  → Match patterns against symbol metadata and usage contexts
  → Enrich symbols with concept metadata (route, task, model, etc.)

ENTRYPOINTS (semantic detection)
  → Query enriched metadata: if "route" in sym.concepts → Entry(kind="route")
  → High confidence (0.95) from semantic match
```

**Key insight:** Entry kinds (routes, tasks, commands) are framework-afforded concepts detected from symbol metadata, not file paths.

### meta.concepts Structure

Enriched symbols have a `meta.concepts` list that serves as the **single source of truth** for semantic metadata:

```json
{
  "meta": {
    "concepts": [
      {"concept": "route", "path": "/users", "method": "GET", "framework": "fastapi"},
      {"concept": "test_function", "framework": "test-frameworks"},
      {"concept": "main_entrypoint", "framework": "main-functions"}
    ]
  }
}
```

**Fields:**
- `concept`: Semantic type (route, model, task, test_function, main_entrypoint, etc.)
- `framework`: Which pattern file matched (fastapi, test-frameworks, main-functions, etc.)
- Additional fields vary by concept type (e.g., `path`, `method` for routes)

**Path normalization:** Route paths are normalized to always start with `/` for consistent matching (e.g., `users` → `/users`).

Linkers and entrypoint detection query `meta.concepts` exclusively.

### Convention Patterns vs Framework Patterns

The pattern system has two categories:

| Category | When Loaded | Purpose | Examples |
|----------|-------------|---------|----------|
| **Convention** | Always | Language-agnostic patterns | main-functions, test-frameworks, naming-conventions, language-conventions, config-conventions, library-exports |
| **Framework** | When detected | Framework-specific patterns | fastapi, django, express, spring-boot |

**Convention patterns (6 files):**
- `main-functions.yaml`: main() entrypoints across 10+ languages
- `test-frameworks.yaml`: Test function detection (pytest, JUnit, xUnit, etc.)
- `language-conventions.yaml`: CUDA kernels, WGSL shaders, COBOL programs, LaTeX structure, Starlark rules
- `config-conventions.yaml`: NPM/Maven/Cargo dependencies, Android components, TypeScript references
- `library-exports.yaml`: Library entry point detection (JS/TS index exports, Python __init__.py, Go uppercase, Java/Kotlin public, Elixir public, Rust pub)
- `naming-conventions.yaml`: Heuristic entrypoints by naming pattern (`*Controller`, `*Handler`, `*Service`)

**Framework patterns:** Loaded only when the framework is detected in profile. See [FRAMEWORKS.md](FRAMEWORKS.md) for the full list; YAML source is in `packages/hypergumbo-core/src/hypergumbo_core/frameworks/`.

### Pattern Types

The framework pattern system supports multiple detection strategies:

| Pattern Type | Example Frameworks | Detection Method |
|--------------|-------------------|------------------|
| **Decorator-based** | FastAPI, Flask, NestJS, Spring Boot | Match `@app.get`, `@Controller` decorators |
| **Call-based** | Django, Express, Go Gin/Echo | Capture `path("/url", view)` via UsageContext |
| **DSL-based** | Rails, Sinatra, Phoenix | Parse `get '/path' do` blocks |
| **File-based** | Next.js, Nuxt | Infer routes from `pages/`, `app/` paths |
| **Export-based** | JS/TS libraries | Detect exports from `index.ts/js` as library entrypoints |

**Path inheritance (v1.3.x):** Patterns can use `prefix_from_parent` to inherit path prefixes from parent concepts. For example, NestJS route handlers use `prefix_from_parent: "controller"` to combine `@Controller('/users')` prefix with `@Get(':id')` path into `/users/:id`.

See [ADR-0003](adr/0003-architectural-analysis-and-revision-plan.md) for the design rationale and [UsageContext extension](adr/0003-usage-context-patterns.md) for call-based framework support.

### Entrypoint Confidence Tiers

These tiers apply to entrypoint detection. For edge confidence scoring, see [§12 Confidence scoring](#12-confidence-scoring).

Confidence scores reflect detection reliability, enabling meaningful ordering in sketch output:

| Tier | Confidence | Detection Method | Examples |
|------|------------|------------------|----------|
| 🟩 **Declared** | 0.99 | Manifest files | `pyproject.toml [project.scripts]`, `package.json "bin"`, `Cargo.toml [[bin]]` |
| 🟩 **Decorator/Annotation** | 0.95 | Explicit code markers | `@app.route`, `@click.command`, `@Controller`, `@RequestMapping` |
| 🟩 **Structural** | 0.85 | Strong conventions | `if __name__ == "__main__"`, class extends `Activity` |
| 🟩 **Naming** | 0.70 | Heuristic patterns | Class named `*Controller`, `*Handler`, `*Service` without annotations |

All four confidence tiers are now implemented. Naming-based detection serves as a fallback when no framework-specific patterns match.

### Scoring for Auto-Slice Entry Selection

When multiple entrypoints exist, scoring selects the most useful ones:

```python
score = confidence * (1 + log(1 + outgoing_edges))
```

This prefers well-connected entries, producing richer slices.

## 9) Behavior map JSON

The behavior map is a JSON file produced by `hypergumbo run`. It is a compiled view of the IR (see [§6 Output views](#output-views)) designed for programmatic consumption by agents and tooling. Field *semantics* (`id`, `stable_id`, `origin`, etc.) are defined once in [§6 Internal representation](#6-internal-representation) and not repeated here; this section covers serialization rules and output-specific fields.

Single file: `hypergumbo.results.json`

### Top-level structure
```json
{
  "schema_version": "0.2.1",
  "confidence_model": "hypergumbo-evidence-v1",
  "stable_id_scheme": "hypergumbo-stableid-v2",
  "shape_id_scheme": "hypergumbo-shapeid-v1",
  "repo_fingerprint_scheme": "hypergumbo-repofp-v1",
  "view": "behavior_map",
  "generated_at": "2026-01-15T10:30:00Z",
  "analysis_incomplete": false,
  "analysis_runs": [],
  "profile": {},
  "nodes": [],
  "edges": [],
  "features": [],
  "metrics": {},
  "limits": {}
}
```
Code also emits `usage_contexts`, `entrypoints` (documented below), and `sketch_precomputed` (internal cache artifact — not part of the public schema; consumers should not depend on its presence).

### JSON Schema (Auto-Generated)

A formal JSON Schema is available at `docs/schema.json`. This schema is **auto-generated** from the Python dataclasses in `packages/hypergumbo-core/src/hypergumbo_core/ir.py` to ensure it stays in sync with the implementation.

**Regenerate with:** `./scripts/generate-schema`

**Verify in CI with:** `./scripts/generate-schema --check`

The schema follows JSON Schema Draft 2020-12 and can be used for:
- Validating hypergumbo output files
- IDE autocompletion for consumers
- Documentation in a standard format

**DRY Principle:** The Python dataclasses (`Symbol`, `Edge`, `Span`, `AnalysisRun`) are the single source of truth. The JSON Schema and this spec document the *meaning* of fields; the dataclasses define the *structure*.

**Scheme identifiers** (`stable_id_scheme`, `shape_id_scheme`, `repo_fingerprint_scheme`): Identify the algorithms used to compute their respective fields. See [§6 Identity and provenance scheme versioning](#identity-and-provenance-scheme-versioning) for definitions and the versioning mandate.

**analysis_incomplete** (boolean, default: false):
- Set to `true` if analysis terminated early due to errors, timeouts, or resource limits
- When true, output is valid JSON but may be missing nodes/edges
- Check `limits.partial_results_reason` for details
- Agents should decide whether partial results are sufficient for their use case

### Confidence scoring

The `confidence` field on edges (0.0-1.0) indicates detection reliability. The `confidence_model` field (`hypergumbo-evidence-v1`) identifies the scoring algorithm. See [§12 Confidence scoring](#12-confidence-scoring) for the full confidence model and [Appendix C](#appendix-c-schema-compatibility-contract) for consumer obligations (including the 0.30 default for unknown evidence types).

### analysis_runs[] — provenance tracking

Each entry records provenance for one analyzer pass. Field semantics are defined in [§6 Internal representation](#6-internal-representation); see `docs/schema.json` for the full field list.

**Output-specific note:** The IR field `pass_id` is serialized as `pass` in JSON output.

**skipped_passes** (array, optional): Lists passes that could not run (e.g., `{"pass": "lean-ts-v1", "reason": "tree-sitter-lean grammar not available"}`). Each entry includes pass ID and reason.

### profile — repo characteristics

```json
{
  "languages": {
    "python": {"files": 42, "loc": 15230},
    "javascript": {"files": 18, "loc": 8420}
  },
  "frameworks": ["fastapi", "react"],
  "repo_kind": "web_api"
}
```

**LOC definition:** Lines of code counts non-empty lines in files matching language extensions. Lock files (poetry.lock, package-lock.json, etc.) are excluded. See [§15 File role classification](#15-file-role-classification) for the proposed taxonomy that would also exclude pure data files from LOC counts.

### nodes[] — definitions, files, endpoints

Field semantics (`id`, `stable_id`, `shape_id`, `fingerprint`, `origin`, `quality`, etc.) are defined in [§6 Internal representation](#6-internal-representation). See `docs/schema.json` for the full field list. This section documents output-specific serialization rules.

**Presence rule:** `stable_id`, `shape_id`, and `origin_run_signature` keys MUST be present on every node. If unavailable, they MUST be set to `null` (not omitted). This supports forward-compatible consumers without forcing every pass to compute every field.

**supply_chain** (object, required): Compiled from the IR's flat `supply_chain_tier` and `supply_chain_reason` fields into a nested object with an added `tier_name` field (e.g., `first_party`, `internal_dep`), computed from the numeric `tier` at serialization time. See [§14 Supply chain classification](#14-supply-chain-classification) for tier definitions.

```json
"supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "matches ^src/"}
```

**Node kinds:** `file`, `module`, `function` (function/method), `class`, `endpoint` (HTTP route, IPC handler, CLI entrypoint).

### edges[] — relationships

Each edge carries `id`, `edge_key`, `type`, `src`, `dst`, `confidence`, provenance fields (`origin`, `origin_run_id`, `origin_run_signature`), `quality`, and a `meta` object with structured evidence. See `docs/schema.json` for the full field list.

**Multi-pass evidence (optional):** When multiple analysis passes observe the same relationship, `meta.evidence[]` accumulates their individual observations. The top-level `meta.evidence_type`, `meta.evidence_lang`, and `meta.evidence_spans` always reflect the primary (highest-confidence) record.

**edge_key:**
- `edge_key` is a canonical identity used to deduplicate/merge multiple observations of the “same” relationship across passes.
- Format: `edgekey:sha256:<hash>`
- Recommended hash inputs (deterministic):
  - `type`
  - `src` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
  - `dst` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
- `id` remains a unique identifier for this edge record instance.

**Meta fields**:
- `evidence_lang` (optional): Language used for confidence scoring. Defaults to `src` node's language if omitted. Required for cross-language edges (HTTP, IPC) where src/dst languages differ.
- `evidence_spans[]`: Structured locations of evidence. Each span includes file path and line/column range.

**Evidence types** (machine-readable, see [§12](#12-confidence-scoring) for scoring algorithm):
* `ast_call_direct` — Direct function call in AST
* `ast_call_method` — Method call with receiver
* `ast_getattr_call` — Call via getattr/dynamic lookup
* `import_static` — Static import statement
* `import_dynamic` — Dynamic import (importlib, require with variable)
* `script_src` — HTML script tag src attribute
* `script_inline` — Inline script content

**quality.reason** remains for human debugging but is NOT relied upon for programmatic logic.

**Edge types:**
* `calls` — function/method invocation
* `imports` — module/symbol import
* `defines_target` — definition relationship
* ✅ `renders` — template rendering (Rails controller → view template)
* `script_src` — script tag src attribute
* `implements` — class implements interface (Java, TypeScript, Go via `var _ Interface = &Struct{}`)
* `extends` — class extends base class
* `native_bridge` — Java native method → C implementation (JNI)
* `message_send` — sends IPC/protocol message
* `message_receive` — handles IPC/protocol message
* `instantiates` — class instantiation (constructor call)
* `http_calls` — HTTP client call site to server route handler (see [§7 HTTP client-server linking](#http-client-server-linking))
* ⬜ `manual` — user-annotated (not implemented)

### features[] — named slices

Each feature contains `id`, `name`, `entry_nodes[]`, `node_ids[]`, `edge_ids[]`, a `query` object (method, entrypoint, hops, max_files, exclude_tests), `limits_hit[]`, and `summary`. See `docs/schema.json` for the full structure.

**Feature ID:** Stable identifier based on query spec: `sha256(json.dumps(query, sort_keys=True))`. Same query on same code → same feature ID → enables diff across commits.

### entrypoints[] — detected entry points

🟩 Pre-computed, confidence-ranked array of execution entry points (HTTP routes, CLI commands, main functions, lifecycle hooks, etc.). Each entry references a node in the graph.

```json
{
  "entrypoints": [
    {
      "symbol_id": "python:src/app.py:10-25:get_users:function",
      "kind": "http_route",
      "confidence": 0.95,
      "label": "HTTP GET /users"
    }
  ]
}
```

**Fields:**
- `symbol_id`: Reference to a node (matches `nodes[].id`)
- `kind`: Entry point type (`http_route`, `cli_command`, `main_function`, `background_task`, `websocket_handler`, `library_export`, `connectivity_based`, etc.)
- `confidence`: Detection confidence (0.0–1.0), reflecting pattern strength, penalties for test/vendor code, and connectivity boost
- `label`: Human-readable description

**Confidence tiers** (see [§8](#8-entrypoint-detection) and [§12](#12-confidence-scoring)):
- 0.99: Manifest-declared (package.json `bin`, Cargo.toml `[[bin]]`, pyproject.toml `[project.scripts]`)
- 0.95: Framework patterns (decorators, base classes)
- 0.85: Structural (Python `if __name__ == "__main__"`)
- 0.80: Language conventions (`main()` function)
- 0.70: Naming heuristics (`*Controller`, `*Handler`)
- 0.50: Connectivity-based fallback (top 5 most-connected callables when no patterns match)

Penalties: test files (−50%), vendor/external deps (−70%), utility files (−50%). Connectivity boost: up to +0.25 for entrypoints with many outgoing edges.

**Sorting:** Ranked by confidence (highest first).

**Not redundant with nodes:** While nodes carry `meta.concepts` metadata from framework pattern matching, the `entrypoints` array provides pre-computed confidence (with penalties and boosts), ranking, and labeled kinds. Consumers would otherwise need to iterate all nodes, check concepts, apply scoring logic, and sort. Used by sketch generation, slicing, and compact output.

### usage_contexts[] — framework pattern evidence

🟩 Intermediate representation of how symbols are *used* (as opposed to how they are *defined*). Each entry records a call site, data value, export, or macro invocation that gives semantic meaning to a symbol through its usage context. See [ADR-0003](adr/0003-usage-context-patterns.md) for design rationale.

```json
{
  "usage_contexts": [
    {
      "id": "uc:...",
      "kind": "call",
      "context_name": "app.get",
      "symbol_ref": "javascript:src/routes.js:10-25:listUsers:function",
      "position": "args[1]",
      "metadata": {"http_method": "GET", "route_path": "/users"},
      "path": "src/app.js",
      "span": {"start_line": 5, "end_line": 5, "start_col": 0, "end_col": 40}
    }
  ]
}
```

**Fields:**
- `id`: Unique identifier
- `kind`: Context type (`call`, `data_value`, `export`, `macro`)
- `context_name`: Function/variable/file name where usage occurs
- `symbol_ref`: ID of the symbol being used (may be null if unresolved)
- `position`: Where in the context the symbol appears (e.g., `args[1]`, `:get`, `default`)
- `metadata`: Context-specific data (e.g., `http_method`, `route_path`)
- `path`: File where usage occurs
- `span`: Source location

**Role in the pipeline:** Usage contexts feed into framework pattern matching (`enrich_symbols()`), which adds concepts to symbols, which in turn drive entrypoint detection and linker behavior. Most consumers should use the enriched `nodes` and `entrypoints` rather than processing `usage_contexts` directly.

**Stripped from compact/tiered views** to reduce payload size.

### metrics — optional counts

Aggregate statistics: `total_nodes`, `total_edges`, `avg_confidence`, per-language breakdowns (`languages.*`), and per-tier breakdowns (`by_supply_chain_tier.*`). Each breakdown includes `nodes` and `edges` counts.

### supply_chain_summary — classification overview

Per-tier file and symbol counts (`first_party`, `internal_dep`, `external_dep`), plus a `derived_skipped` object listing files excluded from analysis. `derived_skipped.paths` is capped at 10 entries; full list available via `--verbose`.

### limits — explicit gaps

Documents what the analysis *didn't* capture. Key arrays:
- `not_captured[]`: Categories of constructs not analyzed (e.g., dynamic imports, eval, complex decorators)
- `truncated_files[]`: Files skipped due to size, with path, size, and reason
- `skipped_languages[]`: Languages with unavailable grammars
- `failed_files[]`: Files that caused parse errors, with path, reason, and analyzer ID

**partial_results_reason** (string, optional): Present only when `analysis_incomplete: true`. Human-readable explanation (e.g., `"Timeout: Analysis exceeded 300 seconds"`, `"User interrupted: Ctrl-C received"`).

## 10) Sketch output

Markdown output to stdout (not a file). This is the default output mode. Designed for pasting into LLM chat interfaces. See [ADR-0005](adr/0005-sketch-budget-allocation.md) for detailed budget allocation and section composition.

**Section order (in priority for truncation):**

| Order | Section | Purpose |
|-------|---------|---------|
| 1 | 🟩 Header | Title, description |
| 2 | 🟩 Overview | Language breakdown, file counts, LOC |
| 3 | 🟩 Structure | Tree built from important files |
| 4 | 🟩 Frameworks | Detected frameworks/libraries |
| 5 | 🟩 Tests | Test file count, frameworks, coverage estimate |
| 6 | 🟩 Configuration | Config file excerpts (heuristic + semantic) |
| 7 | 🟩 Entry Points | CLI commands, HTTP routes |
| 8 | 🟩 Data Models | ORM models, entities, core data structures |
| 9 | 🟩 Source Files | File listing by importance density |
| 10 | 🟩 Key Symbols | Functions, classes, types with centrality |
| 11 | 🟩 Additional Files | README-first + hybrid round-robin |
| 12 | 🟩 Source Content | Actual code (--with-source only) |
| 13 | 🟩 Additional File Content | Code for semantic picks (--with-source only) |

**Token budget:** `-t N` truncates at section boundaries, preserving higher-priority sections. With `--with-source`, budget shifts from file listings to actual source code.

### Additional Files selection

The Additional Files section uses a README-first hybrid ranking algorithm:

1. **README always first:** The project's README is placed first. If it exceeds the token budget, it's truncated and no other files are included.

2. **Round-robin selection:** Remaining files are selected by cycling through three sources:
   - README-linked files (internal links extracted from the README)
   - Similarity-ranked files (semantic similarity to project description)
   - Centrality-ranked files (symbol mention frequency)

3. **Multi-format link extraction:** Supports Markdown (inline + reference-style), Org-mode, RST, and AsciiDoc link syntaxes. Resolves relative paths, absolute paths, and forge URLs (GitHub/GitLab/Codeberg).

4. **Dynamic truncation:** When budget is limited, files are truncated based on median token count of already-selected files, with a 500-token floor.

**Example round-robin order:**
```
1. README.md (always first)
2. CONTRIBUTING.md (linked from README)
3. docs/overview.md (similarity-ranked)
4. config.yaml (centrality-ranked)
5. INSTALL.md (linked from README)
6. docs/api.md (similarity-ranked)
...
```

**Example:**
```markdown
# minetest-wasm

## Overview
C++ (82%), Lua (12%), CMake (6%)
847 files (712 non-test + 135 test)
~120,000 LOC (~105,000 non-test + ~15,000 test)

## Structure
/path/to/minetest-wasm/
├── CMakeLists.txt
├── src
│   ├── main.cpp
│   └── [and 234 other items]
├── builtin
│   └── [and 45 other items]
└── [and 12 other items]

## Frameworks
- cmake
- lua

## Entry Points
- `main` (Cli main) — src/main.cpp
- `Client::Client` (Constructor) — src/client/client.cpp
```

For a complete real-world example (install, run, and full JSON output), see [example-output.md](example-output.md).

## 11) Slicing behavior

Entry sources (HTTP routes, CLI mains, IPC handlers, etc.) are detected by the pattern system; see [§8 Entrypoint detection](#8-entrypoint-detection).

### Slicing algorithm

🟩 BFS traversal on the call graph from entry nodes, bounded by hop limit and file count limit. See [§3 Analysis options](#analysis-options) for CLI flags (`--max-hops`, `--max-files`). Edges can be filtered by confidence threshold or test exclusion.

### Slice identity and reproducibility

Each feature gets a stable `id` based on its query specification:

```python
slice_id = sha256(json.dumps(query, sort_keys=True))
```

Query format enables exact reproduction:

```json
{
  "method": "bfs",
  "entrypoint": "fastapi_route:/api/login",
  "hops": 3,
  "max_files": 50,
  "exclude_tests": true
}
```

Feature comparison across commits: same query → compare `node_ids`/`edge_ids` to detect changes.

### Tier filtering

🟩 The `--max-tier` flag (defined in [§3](#3-user-experience-cli); tier definitions in [§14](#14-supply-chain-classification)) adds tier-based traversal boundaries to slicing: BFS traversal skips nodes whose supply chain tier exceeds the specified value. For example, `--max-tier 1` constrains the slice to first-party code only.

### Reverse slice class expansion

🟩 When reverse-slicing from a class/interface entry (e.g., `--reverse --entry OwnerRepository`), the slicer auto-expands the BFS starting set to include all member methods (via `contains` edges). This enables finding callers of `findById`, `search`, etc. Applies to class, interface, module, struct, trait, and enum containers.

## 12) Confidence scoring

Hypergumbo assigns confidence scores (0.0–1.0) in three independent categories. The scores quantify detection reliability — how certain hypergumbo is that a detected relationship or entrypoint is real, not a false positive. The three categories are independent: an edge originating from a high-confidence entrypoint does not inherit that entrypoint's confidence score, and analyzer-produced edges and linker-produced edges use different scoring logic.

### Three confidence categories

| Category | What it scores | Scoring basis | Score range | Defined in |
|----------|---------------|---------------|-------------|------------|
| **Analyzer edge confidence** | Intra-language relationships (calls, imports) | `(language, evidence_type)` matrix + contextual adjustments | 0.30–0.95 | [Below](#edge-confidence-analyzer-evidence) |
| **Linker edge confidence** | Cross-language relationships (JNI, IPC, HTTP) | Match quality (literal vs. dynamic, naming convention vs. annotation) | 0.40–0.95 | [Below](#edge-confidence-cross-language-linkers), details in [§7](#7-cross-language-linkers) |
| **Entrypoint confidence** | Whether a symbol is an entry point | Detection method (manifest, decorator, convention, naming) | 0.70–0.99 | [Below](#entrypoint-confidence-tiers), details in [§8](#8-entrypoint-detection) |

All scores use the same 0.0–1.0 scale and the same semantic contract: higher means more certain. The `confidence_model` field in output (`hypergumbo-evidence-v1`) identifies the scoring algorithm version. Consumers MUST default unknown `evidence_type` values to 0.30 (see [Appendix C](#appendix-c-schema-compatibility-contract)).

### Edge confidence: analyzer evidence

🟪 Deterministic mapping from structured evidence → confidence score. This covers edges produced by language analyzers (Tier 1 passes). Not yet implemented: no `EVIDENCE_CONFIDENCE_MATRIX` lookup, no contextual adjustments (`dynamic_dispatch`, `missing_types`, `has_type_annotation`). Edge default confidence is 0.85 in code (not the 0.30 specified below).

```python
# (language, evidence_type) → base_score
EVIDENCE_CONFIDENCE_MATRIX = {
    ("python", "ast_call_direct"): 0.95,
    ("python", "ast_call_method"): 0.85,
    ("python", "ast_getattr_call"): 0.60,
    ("python", "import_static"): 0.95,
    ("python", "import_dynamic"): 0.40,
    ("javascript", "import_static"): 0.95,
    ("javascript", "require_static"): 0.90,
    ("javascript", "require_dynamic"): 0.40,
    ("html", "script_src"): 0.80,
    ("html", "script_inline"): 0.70,
}

def calculate_evidence_confidence(
    lang: str,
    evidence_type: str,
    context: dict
) -> float:
    """
    Calculate confidence from evidence.

    Args:
        lang: Language (from edge.meta.evidence_lang or src.language)
        evidence_type: From edge.meta.evidence_type
        context: Additional flags (dynamic_dispatch, has_type_annotation, etc.)

    Returns:
        float in [0.0, 1.0]
    """
    base = EVIDENCE_CONFIDENCE_MATRIX.get((lang, evidence_type), 0.30)

    adjustments = 0.0
    if context.get("dynamic_dispatch"):
        adjustments -= 0.1
    if context.get("missing_types"):
        adjustments -= 0.05
    if context.get("has_type_annotation"):
        adjustments += 0.05

    return min(1.0, max(0.0, base + adjustments))
```

**Note**: Base scores are heuristic baselines (to be validated against benchmark suite). New evidence types can be added in minor versions.

### Edge confidence: cross-language linkers

Cross-language linkers (Tier 2 passes) produce their own confidence scores based on match quality. These scores are independent of the analyzer evidence matrix above. See [§7 Cross-language linkers](#7-cross-language-linkers) for detection rules and limitations.

| Linker | Match type | Confidence |
|--------|-----------|------------|
| **JNI** | Naming convention (`Java_{Class}_{method}`) | 0.95 |
| **JNI** | 🟪 Annotation-confirmed | 0.98 |
| **IPC** | String literal channel name match | 0.85 |
| **IPC** | 🟪 Variable/computed channel name | 0.50 (code uses flat 0.65) |
| **IPC** | 🟪 Template literal with interpolation | 0.40 (no variable/template distinction in code) |
| **IPC** | 🟪 Annotation-provided | 0.95 |
| **HTTP** | Literal URL match | 0.90 |
| **HTTP** | Variable/computed URL | 0.65 |

**Pattern:** Across all linkers, literal/static matches score higher than dynamic/computed ones, and annotation-confirmed matches approach 0.95. This reflects the inherent uncertainty of heuristic string matching.

### Entrypoint confidence tiers

Entrypoint confidence scores how reliably a symbol was identified as an entry point (route, CLI main, task handler, etc.). This is independent of edge confidence — a function detected as a route at 0.95 confidence may have call edges at any confidence level.

See [§8 Entrypoint detection](#8-entrypoint-detection) for the detection architecture and the full tier table. The four tiers: Declared (0.99), Decorator/Annotation (0.95), Structural (0.85), Naming (0.70).

## 13) Output reproducibility

Reproducibility has two dimensions: **caching** ensures that re-running analysis on unchanged code returns the same result quickly, and **deterministic ordering** ensures that output is diffable across runs.

### Caching
* Location: `~/.cache/hypergumbo/` (XDG-compliant; respects `XDG_CACHE_HOME` if set)
* Cache structure:
  ```
  ~/.cache/hypergumbo/
  └── <repo-fingerprint>/
      ├── embeddings/
      │   └── embed_<file-content-hash>.npy
      └── results/
          └── <state-hash>/
              └── hypergumbo.results.json
  ```
* Keying strategy:
  * **Repo fingerprint** (stable identity): hash of remote origin URL + first commit SHA (git repos) or absolute path (non-git). Shared across checkouts of the same repo.
  * **State hash** (point-in-time snapshot): hash of HEAD SHA + diff of tracked changes + untracked source file metadata. Changes when any file is modified.
  * **Embedding cache**: keyed by individual file content hash; shared across all repo states since embeddings depend only on file content.
* Cache invalidation: state hash changes when any analyzed file content changes (including dirty git files)
* Cache format: JSON for analysis results, NumPy `.npy` for embeddings
* Management: `hypergumbo cache-status` and `hypergumbo cache-clear [--older-than N] [--dry-run]`

### Deterministic ordering

Output ordering is deterministic (same input → same output) and optimized for consumption priority.

* 🟩 **Default: centrality-ranked** — Nodes sorted by centrality score (most important first). Edges sorted by source node centrality. This ordering is deterministic given the same input graph and optimizes for LLM context windows and human scanning. Used in JSON output, sketch output, and compact/tiered views.
* 🟩 **JSON key-level reproducibility** — all `json.dump`/`json.dumps` output calls use `sort_keys=True` for reproducible diffs at the key level.
* 🟪 **`--sort-order` option** — Future flag to allow alternative orderings (e.g., `--sort-order alphabetical` with sort keys: nodes by `(language, path, start_line, name)`, edges by `(src, dst, type)`) for users who prefer diffability over importance ranking.

## 14) Supply chain classification

Hypergumbo classifies files by their position in the project's dependency graph. This enables focused analysis (first-party code prioritized in results) and noise reduction (derived artifacts excluded from analysis entirely).

### Motivation

Static analysis of modern codebases faces a fundamental signal-to-noise problem: the code a developer wrote is often mixed with code they imported, bundled, or generated. A webpack bundle contains both application logic and lodash internals. A monorepo contains both project packages and vendored forks.

Without supply chain awareness, analysis results are polluted:
- Key Symbols rankings dominated by utility functions from bundled dependencies
- Edge counts inflated by calls within third-party libraries
- Sketch output filled with framework internals rather than application logic

The solution is not to exclude dependencies entirely—sometimes tracing into them is valuable—but to **classify** code by its position in the supply chain and let users control their viewport.

### Tiers

Code is classified into four tiers based on its relationship to the project:

| Tier | Name | Description | Examples | Status |
|------|------|-------------|----------|--------|
| 1 | `first_party` | Project's own source code | `src/`, `lib/`, `app/` | 🟩 |
| 2 | `internal_dep` | Internal libraries, monorepo packages | Workspace packages, local forks | 🟩 |
| 3 | `external_dep` | Third-party dependencies (readable form) | `node_modules/lodash/`, `vendor/` | 🟩 |
| 4 | `derived` | Build artifacts, transpiled/bundled output | `dist/`, `*.min.js`, source-mapped files | 🟩 |

**Default behavior:**
- Tiers 1-3: Analyzed, with tier used for ranking/filtering
- Tier 4: Excluded from analysis entirely (pure noise)

**Design principle:** Analyze the canonical source, skip derived artifacts. If both `src/app.ts` and `dist/app.js` exist, analyze the TypeScript (tier 1), skip the transpiled JavaScript (tier 4).

### Classification Algorithm

🟩 Classification happens at discovery time, before analysis. Signals are checked in order; first match wins.

#### 1. Derived artifact detection (tier 4)

Checked first because derived files should never be analyzed.

**Path patterns:**
```
dist/, build/, out/, target/
.next/, .nuxt/, .output/, .svelte-kit/
*.min.js, *.min.css, *.bundle.js, *.compiled.js
*.pyc, *.pyo, __pycache__/
```

**Content heuristics** (checked if path inconclusive):
```python
def is_likely_derived(path: Path) -> bool:
    content = path.read_text()
    lines = content.splitlines()

    # Heuristic 1: Average line length > 150 chars (minified)
    if len(content) / max(len(lines), 1) > 150:
        return True

    # Heuristic 2: Source map reference in last 3 lines
    tail = '\n'.join(lines[-3:])
    if re.search(r'//[#@]\s*sourceMappingURL=', tail):
        return True

    # Heuristic 3: Generator header in first 5 lines
    head = '\n'.join(lines[:5])
    if re.search(r'(Generated by|@generated|DO NOT EDIT)', head, re.I):
        return True

    return False
```

**Rationale for thresholds:**
- 150 chars/line: GitHub Linguist uses 110; hypergumbo uses 150 to reduce false positives on legitimately long lines (e.g., data URIs, long strings). Minified code typically has 1000+ chars/line.
- Source map check: Presence of `sourceMappingURL` is a strong signal that this file was generated from another source.
- Generator header: Many tools (protoc, swagger-codegen, etc.) add header comments.

#### 2. External dependency detection (tier 3)

**Path patterns:**
```
node_modules/
vendor/              # PHP (Composer), Go (historical)
third_party/
Pods/, Carthage/     # iOS
.yarn/cache/
_vendor/             # Hugo
```

**Package name extraction:**
For `node_modules/`, the package name is extracted for metadata:
```python
def extract_package_name(rel_path: str) -> str | None:
    if 'node_modules/' not in rel_path:
        return None
    parts = rel_path.split('node_modules/')[-1].split('/')
    if parts[0].startswith('@'):
        return '/'.join(parts[:2])  # @scope/package
    return parts[0]
```

#### 3. Internal dependency detection (tier 2)

Detected via workspace/monorepo configuration files.

**npm/yarn/pnpm workspaces:**
```json
// package.json
{
  "workspaces": ["packages/*", "apps/*"]
}
```
Files under matched workspace globs are tier 2.

**Cargo workspaces:**
```toml
# Cargo.toml
[workspace]
members = ["crates/*"]
```

**Python monorepos:**
```toml
# pyproject.toml (using hatch, pdm, or similar)
[tool.hatch.envs.default]
dependencies = ["./packages/core", "./packages/utils"]
```

**Example/demo, test, and fuzz/bench patterns:**
```
examples/, demos/, samples/, tutorials/     # Example/demo code
tests/, test/, __tests__/, spec/            # Test directories
_test.go, .test.js, .spec.ts, _spec.rb     # Test file suffixes
fuzz/, fuzzing/, fuzz_targets/              # Fuzz targets
benches/, benchmarks/, benchmark/           # Benchmarks
```

#### 4. First-party detection (tier 1)

**Explicit first-party patterns:**
```
src/, lib/, app/, pkg/
cmd/, internal/          # Go conventions
crates/*/src/            # Rust workspace source
packages/*/src/          # JS monorepo source dirs
```

**Default rule:** If no other tier matches, classify as tier 1 (first-party). This ensures unknown directories are analyzed rather than skipped.

### Impact on Analysis

#### Sketch Key Symbols ranking

Without supply chain awareness, centrality-based ranking can be dominated by utility functions from bundled dependencies.

**Tier-weighted ranking:**
```python
TIER_WEIGHTS = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.0}

def weighted_score(symbol: Symbol, centrality: float) -> float:
    tier = symbol.supply_chain.tier
    weight = TIER_WEIGHTS.get(tier, 1.0)
    return centrality * weight
```

This ensures first-party symbols appear first even when third-party utilities have higher raw centrality.

### Limitations

**What supply chain classification does NOT do:**

1. **Resolve transitive dependencies**: Classification is based on file location, not the full dependency graph. A file in `node_modules/a/` that imports from `node_modules/b/` doesn't affect tier assignment.

2. **Detect vendored copies**: If you copy `lodash.js` into `src/utils/lodash.js`, it's classified as tier 1 (first-party).

3. **Understand build pipelines**: Classification doesn't know that `dist/app.js` was built from `src/app.ts`. It relies on path conventions and content heuristics.

4. **Handle unconventional structures**: Projects with unusual layouts (e.g., source in root, deps in `lib/`) may be misclassified.

**Logged in limits:**
🟪 Dataclasses and serialization exist (`ClassificationFailure`, `AmbiguousPath`, `SupplyChainLimits`), but no production code populates them — `classify_file()` always succeeds and never records failures or ambiguities. Fields are always empty arrays in output.
```json
{
  "limits": {
    "supply_chain": {
      "classification_failures": [],
      "ambiguous_paths": [
        {"path": "lib/vendor/custom.py", "assigned": 1, "note": "could be tier 2 or 3"}
      ]
    }
  }
}
```

## 15) File role classification

Supply chain **tiers** answer "where does this file come from?" (provenance). A complementary dimension, **file roles**, answers "what is this file for?" (purpose). See [ADR-0004](adr/0004-file-taxonomy.md) for the design rationale.

| Role | Description | Examples |
|------|-------------|----------|
| `ANALYZABLE` | Has symbols to extract | Python, JavaScript, Go |
| `CONFIG` | Parameterizes behavior | package.json, pyproject.toml, YAML configs |
| `DOCUMENTATION` | Human-readable instructions | Markdown, RST |
| `DATA` | Raw information, not instructions | JSON datasets, CSV fixtures |

**What counts as "code" for LOC purposes:**

```
CODE = ANALYZABLE + CONFIG + DOCUMENTATION
```

This treats documentation as code (the instructions happen to be in natural language) while excluding pure data files. A 34K-line JSON pricing dataset is not "code" even though it's text.

**Disambiguation for ambiguous extensions:**

JSON files require filename-level classification:
- `package.json`, `tsconfig.json` → CONFIG
- `**/fixtures/*.json`, `*_data.json` → DATA
- Large files (>100KB) → likely DATA
- Default → CONFIG (conservative)

**Composed decisions:**

Tier and Role compose for analysis decisions:

| Decision | Tier constraint | Role constraint |
|----------|----------------|-----------------|
| Count in LOC | Tiers 1-2 | CODE roles |
| Extract symbols | analysis_tiers | ANALYZABLE only |
| Additional Files | Tiers 1-2 | CONFIG + DOCUMENTATION |

**Status:** 🟩 Implemented (ADR-0004). The `taxonomy.py` module provides the unified file classification system with `FileRole` enum and `LanguageSpec` dataclass for 75+ languages.

## 16) Testing & quality bar

### Test fixtures

* 🟩 Small controlled fixtures in `tests/fixtures/*` for property testing
* 🟩 Synthetic code samples with known structure (e.g., "3 functions" → expect 3 function nodes)

### Property-based testing (current approach)

**Rationale:** Golden file testing assumes we know the "correct" output a priori. For complex real-world repos, this is infeasible—we can't manually verify every node and edge. Instead, we verify *invariants* that must hold regardless of the specific output.

**Invariants tested:**
- 🟩 Every edge's source/target references a valid node ID
- 🟩 Confidence scores are in range [0.0, 1.0]
- 🟩 Every symbol has a non-empty name
- 🟩 Output matches the JSON schema
- 🟩 Analysis completes without errors
- 🟩 Determinism: same input → same output

**Benefits:**
- No need to know "correct" answer upfront
- Tests remain valid as analysis improves
- Catches structural bugs (dangling references, invalid values)

### Unit tests
* 🟩 Parsing to nodes/edges (per language)
* 🟩 Stability of IDs across runs (same code → same IDs)
* 🟩 **ID survival across refactors**: `stable_id` unchanged when code renamed/moved (if signature unchanged)
* 🟩 ID collision handling (multiple definitions at same location)
* 🟩 Fingerprint changes when code changes
* 🟩 Slicing correctness (known entry → expected subgraph)
* 🟩 Exclude behavior (respects patterns)
* 🟩 Confidence calculation determinism
* 🟩 Provenance tracking (correct origin fields, execution_id/run_signature hashing)
* 🟩 IR → view compilation correctness
* 🟩 **Catalog loading**: passes discovered correctly, schema validation

### Schema validation tests
* 🟩 Output validates against published JSON Schema
* 🟩 Forward compatibility: v0.1 output readable by v0.2+ (if backward compatible)
* 🟩 Required field presence (execution_id, run_signature, evidence_lang, evidence_spans)
* 🟩 ID format conformance (both `id` and `stable_id` when present)
* 🟩 Evidence type presence in all edges
* 🟩 Toolchain capture in analysis_runs

### Smoke test
* 🟩 `hypergumbo run` on a fixture repo yields valid JSON schema
* 🟩 All expected nodes/edges present
* 🟩 No crashes, warnings logged appropriately
* 🟩 `hypergumbo catalog` displays available passes

### Performance benchmarks
* Small repo (<100 files): <5 seconds end-to-end
* Medium repo (~500 files): <30 seconds
* Caching: second run on unchanged repo <2 seconds (see docs/CACHE.md)

## 17) Error handling

### Parse errors

* 🟩 **Behavior**: Log warning, skip file, continue analysis
* 🟩 **Output**: Add to `limits.failed_files[]`:
  ```json
  {
    "path": "malformed.py",
    "reason": "SyntaxError: invalid syntax (line 42)",
    "analyzer": "python-ast-v1"
  }
  ```

### Circular imports

* 🟩 **Behavior**: Detect cycle, log warning, break at arbitrary point
* 🟩 **Output**: Add to `warnings[]` in `analysis_runs[]`

### Missing dependencies

* 🟩 **Behavior**: If pass requires unavailable grammar (e.g., tree-sitter), skip pass
* 🟩 **Output**: Add to `analysis_runs[].skipped_passes[]` (see [§9 Behavior map JSON](#9-behavior-map-json) for field format)

### Analyzer crashes

* 🟩 **Behavior**: Catch exception, log stack trace to `.hypergumbo/error.log`, continue
* 🟩 **Output**: Set `analysis_incomplete: true` in top-level, add to `warnings[]`

### File size limits

* 🟩 **Behavior**: Skip files exceeding `--max-file-bytes`, continue analysis. Applied globally via `set_max_file_bytes()` so all analyzers using `find_files()` respect the limit.
* 🟩 **Output**: `limits.truncated_files[]` populated via global `on_file_skipped` callback. `run_all_analyzers()` sets a global callback in `discovery.py` that calls `limits.add_truncated_file()` for every file skipped due to size limits. No analyzer modifications required.

### Partial results guarantee

* 🟩 All output is valid JSON even if analysis is incomplete. See [`analysis_incomplete` in §9](#9-behavior-map-json) for field semantics.

## 18) Known limitations

This section documents known limitations and risks of the current analysis system. See `CHANGELOG.md` for per-language implementation status.

### Summary

| Limitation | Impact | Notes |
|------------|--------|-------|
| Best-effort analysis | Medium | AST-based analysis cannot resolve all calls (dynamic dispatch, reflection, eval). Confidence scores communicate uncertainty; machine-readable evidence types enable transparency. |
| Re-export resolution incomplete | Low | Imports through re-exporting modules may not fully resolve. See [details below](#re-export-resolution). |
| Import tracking partial | Low | Only Python and Go fully utilize import tracking for cross-file disambiguation. See [details below](#import-tracking-for-disambiguation). |
| ID collisions in edge cases | Low | Location-based IDs can collide for identically-named symbols at same line. Content hash appended if collision detected. |
| Confidence scores are heuristics | Medium | Scores are calibrated heuristics, not ground truth. Evidence types show reasoning; `--confidence-threshold` allows filtering. |
| Schema changes may break consumers | Medium | Semantic versioning from day 1; schema compatibility contract in [Appendix C](#appendix-c-schema-compatibility-contract); migration guides for breaking changes. |
| stable_id limited in untyped code | Low | Without type annotations, stable_id uses arity-based hashing which may change on signature changes. shape_id provides structural alternative. |

### Re-export Resolution

Many languages support re-exporting symbols from submodules through a package's public interface:

```python
# mypackage/__init__.py
from .submodule import helper  # Re-export

# main.py
from mypackage import helper   # Consumer imports from package
helper()                       # Call should resolve to submodule.helper
```

When unresolved, call edges may point to placeholder IDs instead of real symbols. Slicing still works but may miss connections through re-exported symbols.

**Affected languages:** Python, JavaScript/TypeScript, Rust, Haskell, OCaml, Scala, Elixir, Dart, Zig

**Not affected:** Go (package namespace sharing), C/C++ (headers), Java (direct class imports)

**Workaround:** Use fully-qualified imports when precise resolution is critical.

### Import Tracking for Disambiguation

Cross-file call resolution benefits from tracking import statements to disambiguate which module a qualified call refers to:

```python
from app import crud    # Track: crud refers to app.crud
crud.create_user()      # Resolve: app.crud.create_user()
```

Currently, only Python and Go fully utilize import tracking for disambiguation. See **ADR-0007** for the roadmap to extend this to 30 additional analyzers with meaningful import semantics.

## 19) Autonomous governance (ADR-0008)

🟩 Hypergumbo includes a vendor-agnostic governance system for AI agent contributors working in autonomous mode. This enforces structural thinking before stopping work, preventing "workaround" fixes that bypass root causes.

### Components

| Component | Path | Purpose |
|-----------|------|---------|
| Stop reflection prompt | `.agent/stop_reflect.md` | Checklist agents must complete before stopping |
| Invariant ledger | `.agent/invariant-ledger.md` | Tracks discovered invariants and their fix status |
| Loop sentinel | `.agent/LOOP` | Sentinel file; use `./scripts/loop-toggle` to control |
| Hook adapters | `.agent/hooks/*/` | Per-tool adapter scripts (Claude Code, Gemini CLI, Cursor, Codex CLI) |

### How It Works

1. **Autonomous mode gate:** Hooks only engage when `AUTONOMOUS_MODE.txt` contains "TRUE"
2. **Loop sentinel:** Agents check for `.agent/LOOP`; if present, reflection is required before stopping
3. **Reflection protocol:** Agents complete a structured checklist (invariant identification, structural vs. workaround analysis, scope expansion)
4. **Invariant ledger:** Discovered invariants are documented with status, root cause, and regression tests

### Hook Adapters

Each AI coding tool has a different hook mechanism. Adapter scripts provide a consistent interface:

- **Claude Code:** `.agent/hooks/claude-code/stop.sh` (Stop hook with JSON response)
- **Gemini CLI:** `.agent/hooks/gemini-cli/after-agent.sh` (AfterAgent hook)
- **Cursor:** `.agent/hooks/cursor/stop.sh` (stop hook with ASK output)
- **Codex CLI:** `.agent/hooks/codex-cli/notify.sh` (notification only; limited enforcement)

### Invariant Status

The invariant ledger (`.agent/invariant-ledger.md`) is the authoritative source for discovered invariants and their current fix status. See [ADR-0008](adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for the full governance design rationale.

### Structured Tracker (ADR-0013)

🟩 The markdown-based governance files (invariant ledger, work items) are superseded by a YAML-backed structured tracker ([ADR-0013](adr/0013-structured-tracker.md)). The tracker provides append-only op-logs that are git-merge-safe, causally ordered via Lamport clocks, and support field-level access control. It ships as an independent package (`hypergumbo-tracker`, licensed MPL-2.0) usable in any project — see [the tracker README](../packages/hypergumbo-tracker/README.md) for standalone adoption.

Key capabilities beyond the markdown predecessor:
- **Three visibility tiers:** canonical (shared), workspace (fork-local), stealth (gitignored)
- **Actor-based authority:** `os.getuid()` distinguishes agents from humans; locks, stealth, and discussion clearing are human-only
- **Stop hook integration:** `count-todos`, `hash-todos`, and `guidance` commands replace grep-based TODO scanning
- **TUI:** Interactive terminal interface for human oversight
- **Fork workflow:** `fork-setup` scopes the stop hook to workspace items, so upstream canonical items don't block contributor agents

## 20) Future work

This section collects capabilities that are designed but not yet implemented. The architecture supports these enhancements without breaking changes: the IR ([§6](#6-internal-representation)) enables multi-pass merging, and the schema contract ([Appendix C](#appendix-c-schema-compatibility-contract)) defines what can change in minor vs. major versions.

For detailed designs, see [roadmap-details.md](future/roadmap-details.md) and [Registry & Factory Vision](future/registry-factory-vision.md).

| Item | Horizon | Status |
|------|---------|--------|
| AST-based type inference improvements | Near-term | Method-scoped tracking (medium effort), generic handling (high effort). See ADR-0006. |
| Additional linkers | Near-term | 🟩 Constant propagation for dynamic routes (Python). 🟩 Middleware chain linker (same-file chaining). 🟪 Proxy detection. |
| Additional output views | Near-term | 🟪 `ir_export.json`, `context_bundle.json`, `sarif.json`, flow specs. |
| Testing & CI enhancements | Near-term | 🟪 Longitudinal analysis, integration test markers. |
| Multi-fidelity analysis | Medium-term | 🟪 Language server backends (tsserver, pyright, rust-analyzer, gopls, JDT). Mixed-fidelity graphs. |
| Agent context router | Medium-term | 🟪 Query → slice → context bundle pipeline. Builds on existing slicing. |
| Incremental analysis | Not on roadmap | 18+ month effort. Current mitigation: caching, partial re-analysis. |

Non-goals for future work are consolidated in [§2 Non-goals](#2-non-goals).

## Appendix A: Release lifecycle & support

For the technical contract governing output schema stability, see [Appendix C](#appendix-c-schema-compatibility-contract).

### Semantic versioning

* **Schema versions**: `MAJOR.MINOR.PATCH`
  - MAJOR: Breaking changes (old outputs unreadable)
  - MINOR: Backward-compatible additions (new fields, new views)
  - PATCH: Bug fixes, no schema changes
* **Confidence model versions**: `hypergumbo-evidence-vMAJOR.MINOR`
  - MAJOR: Incompatible changes (requires new schema)
  - MINOR: Refinements (new evidence types, score adjustments)

### Compatibility guarantees

* **v0.1 outputs readable by v0.2+** if v0.2 is backward-compatible (MINOR bump)
* **v1.0 outputs readable by v1.x** for all v1.x (MAJOR version promises stability)
* **Breaking changes only in MAJOR bumps.** See [Appendix C](#appendix-c-schema-compatibility-contract) for the technical definition of what constitutes a breaking change.

## Appendix B: Telemetry & privacy

This appendix is the canonical privacy policy for hypergumbo.

### Current policy: zero telemetry

Hypergumbo **sends no data** anywhere. All analysis is local-only. There is no telemetry infrastructure, no crash reporting service, and no opt-in toggle. Nothing is collected.

**What is NEVER collected**:
* Source code
* Symbol names (function/class/variable names)
* File paths or directory names
* API keys or credentials
* IP addresses

### 🟪 Planned: opt-in crash reporting

The following describes a telemetry system that is not yet implemented. If and when it is built, the commitments above will remain the default, and telemetry will require explicit opt-in.

**Planned opt-in mechanism:** `hypergumbo config --telemetry=on` or `HYPERGUMBO_TELEMETRY=1` environment variable.

**What would be collected** (only if opted in):
* Crash stack traces (sanitized: no code, no symbol names, no file paths)
* Performance metrics (file counts, timings, memory usage)
* Feature usage (which commands run, which flags used)
* Anonymized session ID (random UUID, not linked to identity)

**Planned data retention:**
* Crash reports: 90 days
* Aggregated metrics: 2 years
* No raw session data retained beyond 30 days

**Third-party services:**
* If enabled, telemetry would be sent to Sentry (crash reporting) or similar
* Subject to their privacy policies (links provided in docs)

**Transparency commitments** (apply now and after telemetry is built):
* All telemetry code will be open source (auditable)
* Opt-in status will be shown in `hypergumbo config --show`

## Appendix C: Schema compatibility contract

This appendix defines the **technical contract** for output consumers: which fields are immutable, how consumers must handle unknown fields, and what constitutes a breaking change. For versioning policy, see [Appendix A](#appendix-a-release-lifecycle--support).

### Immutable Contracts (MUST NOT change without major version bump)

**1. Node/edge IDs:**
- Location-based `id` format: `{lang}:{file}:{line}-{line}:{name}:{kind}`
- IDs are deterministic (same code → same IDs across runs)
- Stable even if node/edge order changes (sorting defined in spec)

**2. Core fields (cannot remove or change type):**
- Top-level: `schema_version`, `view`, `generated_at`
- `analysis_runs[]`: Array of objects, each with `execution_id`, `pass`, `version`
- `nodes[]`: Array of objects, each with `id`, `kind`, `name`, `path`, `language`
- `edges[]`: Array of objects, each with `id`, `src`, `dst`, `type`
- `features[]`: Array of objects, each with `id`, `name`, `entry_nodes[]`

**3. Provenance fields:**
- `nodes[].origin`: String, pass ID that created this node
- `nodes[].origin_run_id`: String, references `analysis_runs[].execution_id`
- `edges[].origin`, `edges[].origin_run_id`: Same semantics
- `analysis_runs[].run_signature`: Deterministic fingerprint of pass configuration

**4. Confidence scoring:**
- `confidence_model` field identifies the scoring algorithm (`hypergumbo-evidence-v1`)
- Consumers MUST default unknown `evidence_type` to 0.30

### Extensible Contracts (can add in minor versions)

**1. Unknown field tolerance:**
- Consumers MUST ignore unknown top-level fields
- Consumers MUST ignore unknown fields in `nodes[]`, `edges[]`, `features[]`, `analysis_runs[]`
- Consumers MUST ignore unknown keys under `meta.*`
- Reason: Allows future additions like `meta.resolution_confidence`, `nodes[].shape_id`, etc.

**2. Optional fields:**
- Any field marked "optional" can be absent
- Consumers MUST handle absence gracefully (default value or skip)
- Examples: `stable_id`, `shape_id`, `origin_run_signature`
**Key presence vs. value presence:**
- Some fields may be semantically optional but SHOULD be present as keys with `null` values to reduce consumer branching.
- For v0.1.0, producers SHOULD include these keys with `null` when unknown:
  - `nodes[].stable_id`
  - `nodes[].shape_id`
  - `nodes[].origin_run_signature`
- Consumers MUST accept either form (missing key OR null), but producers prefer `null` keys for stability.

**3. View types:**
- New view types can be added: `ir_export`, `context_bundle`, `sarif`
- `view: "behavior_map"` schema remains stable across v0.x, v1.x
- Consumers check `view` field, skip unknown views

### Breaking Changes (only in major version bumps)

**1. Changing field semantics without renaming:**
- Example: Redefining `stable_id` formula (requires major bump)
- Example: Changing `confidence` from 0.0-1.0 to 0-100 scale

**2. Removing required fields:**
- Example: Removing `nodes[].path` (would break path-based tooling)

**3. Changing ID formats:**
- Example: Switching from `python:file.py:10-15:func:function` to `sha256:abc123`

**4. Changing confidence semantics:**
- Example: Redefining what evidence types mean or changing the scoring algorithm

### Future Additions (examples of backward-compatible changes)

**Possible in future minor versions:**
- `run_signature` refinements (additional hash inputs)
- `stable_id` improvements (better heuristics for untyped code)
- `meta.resolution_confidence` (optional, from type checkers)
- `ir_export.json` view (new view type)
- New `evidence_type` values
- New `kind` values for nodes

**Commitment:** No breaking changes to `behavior_map.json` view within v0.x series.

## Appendix D: Capsule system history

The capsule system (custom analyzer composition via `hypergumbo init`) was removed in v1.0.0. See [history/capsule-system-v1.md](history/capsule-system-v1.md) for details. Other archived v1.0 materials: [history/planning-v1.md](history/planning-v1.md), [history/validation-gates-v1.md](history/validation-gates-v1.md).
