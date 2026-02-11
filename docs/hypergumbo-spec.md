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

## 0) One-sentence summary
A local-first CLI that helps developers and AI agents understand an unfamiliar codebase by analyzing its structure and emitting a **repo behavior map**—a JSON graph of symbols, call edges, routes, and framework patterns with confidence scores and provenance tracking.

## 1) Goals
* 🟩 **Internal IR with views**: Parsers emit to an internal representation; public outputs are compiled views (enables future typed passes without breaking schema).
* 🟩 **Provenance tracking**: Every node/edge records which analyzer pass created it, with unique execution identifiers enabling quality assessment and mixed-fidelity analysis.
* 🟩 **Machine-readable provenance**: All confidence scores and edge evidence captured in structured fields, not just human-readable strings, enabling programmatic filtering and multi-pass merging.
* 🟩 **Agent-ready output**: deterministic JSON graph + "feature slices" so an agent can fetch only relevant code.
* 🟩 **Fast iteration**: simple architecture, small dependency surface, fixtures-driven tests.
* 🟩 **Local-first execution**: analysis runs offline by default (no network, no API keys required).

For goals that were considered and rejected, see [Appendix D](#appendix-d-capsule-system-history).

## 2) Non-goals
* No deep type-resolution / interprocedural dataflow correctness guarantees.
* No accounts, ratings, or social features.
* No automatic PR fixing, no code editing, no CI annotations beyond "export JSON."
* No attempt to support every language *deeply*—broad coverage via tree-sitter (104 languages), deep call-graph extraction for a smaller set. See [§4 Supported stacks](#4-supported-stacks).
* No incremental analysis daemon (full re-analysis is acceptable).
* No LLM-generated analyzer code.

## 3) User experience (CLI)

**Key principle:** Analysis execution requires no network or API keys (by default). Output is deterministic and reproducible given the same repo state. See [Appendix B](#appendix-b-telemetry--privacy) for the full privacy and telemetry policy.

### Install
* `pipx install hypergumbo` (primary, includes all 104 language analyzers)
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

Estimates test coverage by analyzing which functions are called by tests. Uses static analysis only (no code execution). Language agnostic.

**Features:**
* **Hot spots:** Functions called by many tests (potential redundancy)
* **Cold spots:** Functions not called by any tests (need coverage)

**Filtering options:**
* `--min-tests N`: Only show functions called by at least N tests
* `--max-tests N`: Only show functions called by at most N tests (0 = untested only)
* `--top N`: Limit output to top N hot/cold spots

**Example output (text format):**
```
Test Coverage Estimate
======================
Total functions: 150
Tested: 120 (80.0%)
Untested: 30
Total test functions: 45

Hot Spots (highest test density - tests per LOC)
------------------------------------------------
  5.00 t/LOC  ( 15 tests,   3 LOC)  utils.py:10-12   helper()
  0.60 t/LOC  ( 12 tests,  20 LOC)  core.py:50-69    validate()

Cold Spots (untested - need coverage)
-------------------------------------
    0 tests  core.py:100-150  process()  [50 LOC, complexity: 8]
```

**Note:** Hot spots are ranked by test density (tests/LOC), not raw test count. This surfaces small utility functions that are disproportionately tested relative to their size.

**Use case:** Quickly identify which parts of your codebase may need more test coverage, without running any tests.

## 4) Supported stacks

Hypergumbo supports 104 languages via tree-sitter grammars. All are included in the base package.

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

### Core packages

**CLI & orchestration:**
* `cli.py` — CLI entrypoint and command handlers
* `profile.py` — language/framework detection
* `discovery.py` — file finding with exclude patterns

**Internal representation:**
* `ir.py` — Symbol, Edge, Span, AnalysisRun classes
* `schema.py` — JSON schema versioning and behavior map factory

**Analysis pipeline:**
* `sketch.py` — token-budgeted Markdown summary generation
* `entrypoints.py` — YAML-driven entrypoint detection (ADR-0003)
* `slice.py` — graph slicing for context extraction
* `metrics.py` — analysis statistics computation
* `limits.py` — error tracking and analysis gaps
* `supply_chain.py` — file classification by dependency tier (1-4)
* `symbol_resolution.py` — shared cross-file call resolution (NameResolver, SymbolResolver)

**Language analyzers (examples):**
* `analyze/py.py` — Python AST parser
* `analyze/js_ts.py` — JS/TS/Svelte parser via tree-sitter
* `analyze/java.py` — Java parser via tree-sitter

**Cross-language linkers (examples):**
* `linkers/jni.py` — JNI boundary detection (Java↔C)
* `linkers/ipc.py` — IPC message channel detection

**Discovery & catalog:**
* `catalog.py` — Pass availability checking (used by `catalog` command)

Parsers emit to a shared internal representation; see [§6 Internal Representation](#6-internal-representation) for the IR definition and field semantics.

### Analysis pipeline (two-tier model)

The analysis pipeline has two tiers reflecting different information needs:

**Tier 1 — Language analyzers (independent producers):**
Each analyzer is a plain function registered via `AnalyzerSpec` and discovered through Python entry-points (see [ADR-0010](adr/0010-modular-packages-and-smart-testing.md)):
```python
@dataclass
class AnalysisResult:
    symbols: list[Symbol]
    edges: list[Edge]
    usage_contexts: list[UsageContext]
    run: AnalysisRun | None
    skipped: bool = False

# Analyzer function signature (all 100+ analyzers follow this)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    ...
```
Analyzers are embarrassingly parallel — each scans the repo independently and returns a bag of symbols and edges. They do not see each other's output.

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

## 6) Internal Representation

Parsers emit to `AnalysisIR`:
```python
@dataclass
class Symbol:
    id: str                    # location-based identifier
    stable_id: Optional[str]   # semantic identity hash (signature-based)
    shape_id: Optional[str]    # structural implementation fingerprint
    canonical_name: str
    fingerprint: str           # content hash
    kind: str                  # function, class, module, etc.
    name: str
    path: str
    language: str
    span: Span
    origin: str                # which pass created this
    origin_run_id: str         # references AnalysisRun.execution_id
    supply_chain_tier: int     # 1=first_party, 2=internal_dep, 3=external_dep, 4=derived
    supply_chain_reason: str   # classification rationale (e.g., "matches ^src/")
    # Note: In JSON output (§9 Output formats), these flat fields are compiled
    # into a nested supply_chain object with a derived tier_name field.
    origin_run_signature: Optional[str]  # references AnalysisRun.run_signature (for grouping)
    quality: QualityScore

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

### Identity field semantics

* `id` (location-based): `{lang}:{file}:{start_line}-{end_line}:{name}:{kind}`
  - Changes when code moves to different file/line
  - Purpose: Reproducible slicing, deterministic diffs
* `stable_id` (semantic, optional): Interface identity (signature-based), **not implementation identity**
  - **For typed languages or annotated Python**: `sha256({kind}:{normalized_signature}:{visibility}:{containing_module_stable_id})`
    - `normalized_signature`: Canonical type signature (param types, return type, type params)
    - `visibility`: public, private, protected (if language has concept)
    - `containing_module_stable_id`: Recursive stable_id of parent module/class
    - **Excludes**: Implementation details, docstrings, comments
  - **For untyped code**: `sha256({kind}:{parameter_count}:{arity_flags}:{decorator_presence}:{containing_module_stable_id})`
    - `arity_flags`: has_defaults, has_varargs, has_kwargs (structural signature info)
    - `decorator_presence`: Sorted list of decorator names (e.g., `["property", "staticmethod"]`)
    - **Excludes**: Source hash, canonical name (survives renames)
  - Purpose: Track symbols across refactors (renames, moves, documentation changes)
  - **Does NOT change** when: Renaming, moving between files, changing implementation, adding comments
  - **DOES change** when: Signature changes (param types, arity), visibility changes, decorators added/removed
* `shape_id` (optional): Structural implementation fingerprint
  - `sha256(ast_structure)` excluding literals/identifiers
  - Purpose: Detect structural changes (control flow, nesting) without caring about variable names
  - Use case: "Implementation changed but signature stayed same"
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

Public outputs are **compiled views** from this IR — the IR defines the canonical data model, and each view selects and reshapes fields for its audience. See [§9 Output formats](#9-output-formats) for the available views and their serialization details.

**Design principle:** Strong passes (tsserver, pyright) added later will enhance the IR without breaking existing views.

## 7) Cross-Language Edge Detection

Hypergumbo provides **best-effort cross-language edge detection** for common integration patterns. These are AST-based heuristics with string literal matching, not type-resolved or dataflow analysis.

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

**Confidence scoring:**
* Pattern-matched (naming convention): 0.80
* Annotation-confirmed (`@hypergumbo.jni_impl`): 0.95

**Limitations:**
* Does not resolve JNI calls through reflection
* Does not track `JNI_OnLoad` dynamic registration
* Does not handle inner classes (mangling includes `$`)
* Logs unmatched natives in `limits.unresolved_jni[]`

### IPC/Message Channel Detection

Detects message send/receive patterns across process boundaries using string literal matching on channel/event names.

**Supported patterns:**

| Framework | Send Pattern | Receive Pattern | Evidence Type |
|-----------|-------------|-----------------|---------------|
| Electron | `ipcRenderer.send("channel")` | `ipcMain.on("channel")` | `ipc_electron` |
| Electron | `ipcMain.handle("channel")` | `ipcRenderer.invoke("channel")` | `ipc_electron` |
| WebSocket | `ws.send({type: "X"})` | `ws.on("message", ...)` with type check | `ipc_websocket` |
| Guacamole | `tunnel.sendMessage("opcode", ...)` | `oninstruction` handlers | `ipc_guacamole` |
| Node EventEmitter | `emitter.emit("event")` | `emitter.on("event")` | `ipc_eventemitter` |

**Detection algorithm:**
1. Parse AST for known send/receive function patterns
2. Extract channel/event name from string literal argument
3. Build index of all senders and receivers by channel name
4. Match senders to receivers with same channel name
5. Emit `message_send` edge (caller → channel) and `message_receive` edge (channel → handler)

**Confidence scoring:**
* String literal channel name match: 0.85
* Variable/computed channel name: 0.50 (best-effort, name extracted if simple)
* Template literal with interpolation: 0.40 (partial match)
* Annotation-provided (`@hypergumbo.ipc_channel("name")`): 0.95

**Limitations:**
* Dynamic channel names require annotation hints
* Complex message routing (middleware, proxies) not traced
* Does not validate message schema compatibility
* Logs unmatched patterns in `limits.unresolved_ipc[]`

### HTTP Endpoint Detection (Server-side only)

Detects HTTP route definitions for entrypoint detection.

**Supported frameworks:**

| Framework | Pattern | Example |
|-----------|---------|---------|
| FastAPI | `@app.get("/path")` | `@app.get("/users/{id}")` |
| Flask | `@app.route("/path")` | `@app.route("/login", methods=["POST"])` |
| Express | `app.get("/path", handler)` | `router.post("/api/users", createUser)` |
| Java Servlet | `@WebServlet("/path")` | `@WebServlet("/api/session")` |
| JAX-RS | `@Path("/path")` | `@GET @Path("/users/{id}")` |
| Spring MVC | `@RequestMapping("/path")` | `@PostMapping("/api/login")` |

**Detection output:**
* Symbol kind: `route` or `endpoint`
* Symbol name: HTTP method + path (e.g., `GET /users/{id}`)
* Used by entrypoint detection for slicing

**Client-side linking:**
Cross-language client→server matching (e.g., `fetch("/api/users")` → Flask handler) is not yet implemented. See [§19 Future Work](#19-future-work).

### Language-specific notes for cross-language linking

The C analyzer detects JNI export patterns (`JNIEXPORT`, `JNICALL`, `Java_*` naming) and the Java analyzer detects `native` method declarations, both feeding into the JNI linker above. For full per-language analyzer capabilities, see [LANGUAGES.md](LANGUAGES.md).

### limits.cross_language — tracking unresolved links

Cross-language linkers log unresolved patterns for debugging:

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

## 8) Entrypoint Detection

Entrypoint detection identifies HTTP handlers, CLI mains, background tasks, and other entry sources for slicing. Detection is **YAML-driven** via the framework patterns system.

### Architecture

```
ANALYZERS (pure language, no framework knowledge)
  → Capture symbols + rich metadata (decorators, base classes, parameters)
  → Capture UsageContext for call-based patterns (route registrations, etc.)

PATTERN SYSTEM (87 YAML files: 5 convention + 82 framework)
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
| **Convention** | Always | Language-agnostic patterns | main-functions, test-frameworks, language-conventions, config-conventions |
| **Framework** | When detected | Framework-specific patterns | fastapi, django, express, spring-boot |

**Convention patterns (5 files):**
- `main-functions.yaml`: main() entrypoints across 10+ languages
- `test-frameworks.yaml`: Test function detection (pytest, JUnit, xUnit, etc.)
- `language-conventions.yaml`: CUDA kernels, WGSL shaders, COBOL programs, LaTeX structure, Starlark rules
- `config-conventions.yaml`: NPM/Maven/Cargo dependencies, Android components, TypeScript references
- `library-exports.yaml`: Library entry point detection via exports from index files (JS/TS)

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

These tiers apply to entrypoint detection. For edge confidence scoring, see [§12 Confidence calculation](#confidence-calculation-deterministic-algorithm).

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

## 9) Output formats

Hypergumbo produces two output formats from the same analysis pipeline:

- **Behavior Map JSON** (`hypergumbo run`): Full structured graph written to a file. Designed for programmatic consumption by agents and tooling.
- **Sketch** (default mode): Token-budgeted Markdown summary written to stdout. Designed for pasting into LLM chat interfaces.

Both are compiled views of the internal representation defined in [§6](#6-internal-representation).

### Behavior Map JSON

Single file: `hypergumbo.results.json`

#### Top-level structure
```json
{
  "schema_version": "0.2.1",
  "confidence_model": "hypergumbo-evidence-v1",
  "stable_id_scheme": "hypergumbo-stableid-v1",
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

#### JSON Schema (Auto-Generated)

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

#### Confidence scoring

The `confidence` field on edges (0.0-1.0) indicates detection reliability. The `confidence_model` field (`hypergumbo-evidence-v1`) identifies the scoring algorithm. Consumers should treat unknown evidence types as 0.30 confidence.

For the deterministic scoring algorithm (language-specific base scores, evidence types, contextual adjustments), see [§12 Confidence calculation](#confidence-calculation-deterministic-algorithm).

#### analysis_runs[] — provenance tracking

Each entry records provenance for one analyzer pass. Field semantics are defined in [§6 Internal Representation](#6-internal-representation); see `docs/schema.json` for the full field list.

**Output-specific note:** The IR field `pass_id` is serialized as `pass` in JSON output.

**skipped_passes** (array, optional): Lists passes that could not run (e.g., `{"pass": "lean-ts-v1", "reason": "tree-sitter-lean grammar not available"}`). Each entry includes pass ID and reason.

#### profile — repo characteristics

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

**LOC definition:** Lines of code counts non-empty lines in files matching language extensions. Lock files (poetry.lock, package-lock.json, etc.) are excluded. See [§14 File Role Classification](#file-role-classification) for the proposed taxonomy that would also exclude pure data files from LOC counts.

#### nodes[] — definitions, files, endpoints

Field semantics (`id`, `stable_id`, `shape_id`, `fingerprint`, `origin`, `quality`, etc.) are defined in [§6 Internal Representation](#6-internal-representation). See `docs/schema.json` for the full field list. This section documents output-specific serialization rules.

**Presence rule:** `stable_id`, `shape_id`, and `origin_run_signature` keys MUST be present on every node. If unavailable, they MUST be set to `null` (not omitted). This supports forward-compatible consumers without forcing every pass to compute every field.

**supply_chain** (object, required): Compiled from the IR's flat `supply_chain_tier` and `supply_chain_reason` fields into a nested object with an added `tier_name` field (e.g., `first_party`, `internal_dep`), computed from the numeric `tier` at serialization time. See [§14 Supply Chain Classification](#14-supply-chain-classification) for tier definitions.

```json
"supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "matches ^src/"}
```

**Node kinds:** `file`, `module`, `function` (function/method), `class`, `endpoint` (HTTP route, IPC handler, CLI entrypoint).

#### edges[] — relationships

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

**Evidence types** (machine-readable, see [§12](#confidence-calculation-deterministic-algorithm) for scoring algorithm):
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
* `defines` — definition relationship
* `renders` — template rendering
* `loads_script` — script tag src
* `implements` — class implements interface (Java, TypeScript)
* `extends` — class extends base class
* `native_bridge` — Java native method → C implementation (JNI)
* `message_send` — sends IPC/protocol message
* `message_receive` — handles IPC/protocol message
* `instantiates` — class instantiation (constructor call)
* `manual` — user-annotated

#### features[] — named slices

Each feature contains `id`, `name`, `entry_nodes[]`, `node_ids[]`, `edge_ids[]`, a `query` object (method, entrypoint, hops, max_files, exclude_tests), `limits_hit[]`, and `summary`. See `docs/schema.json` for the full structure.

**Feature ID:** Stable identifier based on query spec: `sha256(json.dumps(query, sort_keys=True))`. Same query on same code → same feature ID → enables diff across commits.

#### metrics — optional counts

Aggregate statistics: `total_nodes`, `total_edges`, `avg_confidence`, per-language breakdowns (`languages.*`), and per-tier breakdowns (`by_supply_chain_tier.*`). Each breakdown includes `nodes` and `edges` counts.

#### supply_chain_summary — classification overview

Per-tier file and symbol counts (`first_party`, `internal_dep`, `external_dep`), plus a `derived_skipped` object listing files excluded from analysis. `derived_skipped.paths` is capped at 10 entries; full list available via `--verbose`.

#### limits — explicit gaps

Documents what the analysis *didn't* capture. Key arrays:
- `not_captured[]`: Categories of constructs not analyzed (e.g., dynamic imports, eval, complex decorators)
- `truncated_files[]`: Files skipped due to size, with path, size, and reason
- `skipped_languages[]`: Languages with unavailable grammars
- `failed_files[]`: Files that caused parse errors, with path, reason, and analyzer ID

**partial_results_reason** (string, optional): Present only when `analysis_incomplete: true`. Human-readable explanation (e.g., `"Timeout: Analysis exceeded 300 seconds"`, `"User interrupted: Ctrl-C received"`).

### Sketch (Markdown)

Markdown output to stdout (not a file). This is the default output mode. Designed for pasting into LLM chat interfaces. See [ADR-0005](adr/0005-sketch-budget-allocation.md) for detailed budget allocation and section composition.

**Section order (in priority for truncation):**

| # | Section | Purpose |
|---|---------|---------|
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

## 10) Slicing behavior

### Entry sources

* 🟩 **Detected endpoints** (FastAPI/Flask/Express heuristics):
  * `@app.route`, `@app.get`, `app.get(`, `app.post(`
* 🟩 **Electron main/renderer hints**:
  * File names: `main.js`, `renderer.js`, `preload.js`
  * IPC patterns: `ipcMain.on`, `ipcRenderer.send`
* 🟩 **CLI entrypoints**:
  * Python: `if __name__ == "__main__"`
  * JavaScript: `process.argv` parsing patterns

### Slicing algorithm

* 🟩 **Method**: BFS or DFS on relationships
* 🟩 **Limits**:
  * Hop limit (default: 3)
  * File count limit (default: 20)
  * Configurable via `--max-hops`, `--max-files`
* 🟩 **Edge filtering**: Optionally exclude tests, exclude low-confidence edges

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
  "max_files": 20,
  "exclude_tests": true
}
```

Feature comparison across commits: same query → compare `node_ids`/`edge_ids` to detect changes.

Supply chain tiers add two additional slicing capabilities: **tier filtering** (`--max-tier`) stops traversal at dependency boundaries, and **reverse slice class expansion** auto-includes member methods when slicing from a class entry. See [§14 Supply Chain Classification](#14-supply-chain-classification) for details.

## 11) Analysis guardrails

### Exclude patterns

* `--exclude` supports gitignore-like globs
* Implementation: `fnmatch` (upgrade to `pathspec` later if needed)
* Default excludes:
  * `node_modules/`, `venv/`, `dist/`, `build/`
  * `*.min.js`, `*.bundle.js`
  * `.git/`, `__pycache__/`

### File size limits

* `--max-file-bytes` default: 2MB
* Especially important for HTML/minified JS
* Truncated files logged in `limits.truncated_files[]`

## 12) Confidence scoring

### Confidence calculation (deterministic algorithm)

**Evidence scoring** (stable contract)

Deterministic mapping from structured evidence → confidence score.

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
* Stable sort of nodes/edges for reproducible diffs
* Sort keys:
  * Nodes: `(language, path, start_line, name)`
  * Edges: `(src, dst, type)`
* Enables meaningful `git diff` of output files

## 14) Supply Chain Classification

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

#### 4. First-party detection (tier 1)

**Explicit first-party patterns:**
```
src/, lib/, app/, pkg/
cmd/, internal/          # Go conventions
crates/*/src/            # Rust workspace source
packages/*/src/          # JS monorepo source dirs
```

**Default rule:** If no other tier matches, classify as tier 1 (first-party). This ensures unknown directories are analyzed rather than skipped.

### CLI Integration

#### Analysis scope flags

🟩 Implemented:

```bash
# Default: analyze tiers 1-3, skip tier 4 (derived)
hypergumbo run .

# First-party only (fast, focused)
hypergumbo run . --first-party-only
# Equivalent to: --max-tier 1

# Explicit tier control (default is 3)
hypergumbo run . --max-tier 3
```

#### Reverse slice class expansion

🟩 Implemented:

When reverse-slicing from a class/interface entry, the slicer auto-expands the BFS starting set to include all member methods (via `contains` edges). This enables `--reverse --entry OwnerRepository` to find callers of `findById`, `search`, etc. Applies to class, interface, module, struct, trait, and enum containers.

#### Slice tier filtering

🟩 Implemented:

```bash
# Slice stops at first-party boundary
hypergumbo slice --entry main --max-tier 1

# Slice includes internal deps but not external
hypergumbo slice --entry main --max-tier 2

# Default: slice can traverse into external deps
hypergumbo slice --entry main --max-tier 3
```

#### Sketch prioritization

🟩 Implemented:

The `--no-first-party-priority` flag disables tier-based weighting for Key Symbols ranking:

```bash
# Key Symbols prioritizes first-party (default)
hypergumbo sketch .

# Disable tier weighting (raw centrality)
hypergumbo sketch . --no-first-party-priority
```

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

#### Additional Files ranking

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

#### Slicing behavior

When `--max-tier N` is specified, BFS traversal stops at tier boundaries:

```python
def should_traverse(edge: Edge, target: Symbol, max_tier: int) -> bool:
    if target.supply_chain.tier > max_tier:
        return False  # Don't cross into lower tier
    return True
```

**Use case:** "Show me everything my code calls, but don't trace into lodash internals."

### Limitations

**What supply chain classification does NOT do:**

1. **Resolve transitive dependencies**: Classification is based on file location, not the full dependency graph. A file in `node_modules/a/` that imports from `node_modules/b/` doesn't affect tier assignment.

2. **Detect vendored copies**: If you copy `lodash.js` into `src/utils/lodash.js`, it's classified as tier 1 (first-party).

3. **Understand build pipelines**: Classification doesn't know that `dist/app.js` was built from `src/app.ts`. It relies on path conventions and content heuristics.

4. **Handle unconventional structures**: Projects with unusual layouts (e.g., source in root, deps in `lib/`) may be misclassified.

**Logged in limits:**
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

### File Role Classification

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

## 15) Testing & quality bar

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
* 🟩 Small repo (<100 files): <5 seconds end-to-end
* 🟩 Medium repo (~500 files): <30 seconds
* 🟩 Caching: second run on unchanged repo <2 seconds (see docs/CACHE.md)

## 16) Error handling

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
* 🟩 **Output**: Add to `analysis_runs[].skipped_passes[]`:
  ```json
  {
    "pass": "lean-ts-v1",
    "reason": "tree-sitter-lean grammar not available"
  }
  ```

### Analyzer crashes

* 🟩 **Behavior**: Catch exception, log stack trace to `.hypergumbo/error.log`, continue
* 🟩 **Output**: Set `analysis_incomplete: true` in top-level, add to `warnings[]`

### Partial results guarantee

* 🟩 All output is valid JSON even if analysis is incomplete. See [`analysis_incomplete`](#top-level-structure) in [§9 Output formats](#9-output-formats) for field semantics.

## 17) Known limitations

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

## 18) Autonomous Governance (ADR-0008)

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

## 19) Future Work

This section collects capabilities that are designed but not yet implemented. Pursue when there's clear demand beyond what the current system provides. The architecture is designed to support these enhancements without breaking changes: the IR and identity fields ([§6](#6-internal-representation)) enable cross-refactor tracking and multi-pass merging, and the schema compatibility contract ([Appendix C](#appendix-c-schema-compatibility-contract)) defines what can change in minor vs. major versions. See also [Registry & Factory Vision](future/registry-factory-vision.md) for speculative ideas about sharing analyzers.

| Item | Horizon | Depends on |
|------|---------|------------|
| AST-based type inference improvements | Near-term | — |
| Additional linkers | Near-term | — |
| Additional output views | Near-term | — |
| Testing & CI enhancements | Near-term | CI infrastructure |
| Multi-fidelity analysis | Medium-term | Language server integration |
| Agent context router | Medium-term | Multi-fidelity analysis |
| Incremental analysis | Not on roadmap | — |

### Multi-fidelity analysis *(medium-term)*

The long-term vision is a multi-fidelity code understanding platform that produces typed IR and an agent context router for token-efficient editing.

**Language-native engines** (optional, high-fidelity frontends):
* 🟪 **TypeScript**: `tsserver` (type checker + language service)
* 🟪 **Python**: `pyright` or `mypy` (type inference)
* 🟪 **Rust**: `rust-analyzer` (full semantic analysis)
* 🟪 **Go**: `gopls` (language server)
* 🟪 **JVM**: Eclipse JDT (Java), Kotlin analysis tooling

These would produce typed call graphs, enabling mixed-fidelity analysis: AST edges (0.7 confidence) + typed edges (0.95 confidence) in the same graph.

**Runner types** for high-fidelity analyzers:
* 🟪 **`toolchain_bundle`** — Ships with language server (100MB+ downloads, high fidelity)
* 🟪 **`container_image`** — OCI/Docker image for maximum isolation
* 🟪 **`daemon_process`** — Long-running incremental analysis (research-hard, defer indefinitely)

**IR extensions:**
* Typed symbol table + cross-ref index (extends current IR)
* Control-flow graph (CFG) — opt-in with explicit partial-results flags
* Dataflow facts (reaching definitions, taint tracking) — opt-in, not a core requirement

**Technology choices:**
* IR storage: Protocol Buffers (fast, versioned, language-neutral); JSON fallback if protobuf adds friction

### AST-based type inference improvements *(near-term)*

The current system implements constructor tracking, parameter tracking, field type tracking, return type tracking, and type hierarchy dispatch (see ADR-0006 and CHANGELOG.md). Supported in Python, Java, Kotlin, TypeScript, C#, Dart, and Scala (method name only).

Remaining improvements (without requiring language servers):

| Feature | Value | Effort | Notes |
|---------|-------|--------|-------|
| **Method-scoped tracking** | Low-Medium | Medium | Current file-scoped tracking can cause false positives when same variable name used in different methods. Low priority since collisions are rare. |
| **Generic handling** | High | High | Track `List<User>` to infer that `.get()` returns `User`. High complexity (type parameter binding, variance). Defer until simpler features are done. |

### Agent context router *(medium-term)*

Query interface: "I want to change behavior X in Y context"

**Pipeline:**

1. **Retrieve** relevant nodes/flows from IR
   * Entry: symbol name, file path, route pattern
   * 🟪 Natural language queries via embedding similarity

2. **Slice** on:
   * Call graph (forward/backward) — 🟩 already implemented
   * 🟪 Dataflow (tainted data paths)
   * 🟪 Schema ties (database columns, API contracts)
   * Tests referencing the area — 🟩 already implemented
   * 🟪 Configuration/deployment ties
   * Supply chain tier boundaries — 🟩 already implemented

3. **Assemble context bundle**:
   * Minimal code excerpts (only changed + affected)
   * Invariants/contracts (types, tests, assertions)
   * 🟪 "What could break" checklist (requires whole-program analysis)

Token budget optimization: BFS until token limit hit, sorted by (edge confidence, distance from entry).

**Potential enhancements:**
* 🟪 Learned relevance models
* 🟪 Agent feedback loop (which code was actually edited?)
* 🟪 Embedding-based context expansion
* 🟪 Coverage report parsing for test summary
* 🟪 Multi-language documentation summarization

### Additional output views *(near-term)*

Beyond `behavior_map.json` (the current output), future views compiled from the IR:

* 🟪 **ir_export.json** — Complete symbol table, typed edges with resolution provenance, dataflow facts, cross-language links
* 🟪 **context_bundle.json** — Agent-optimized: minimal code excerpts for a query, invariant checklist, "impact zones" (what could break), token-budget optimized
* 🟪 **sarif.json** — SARIF 2.1 compatible findings for integration with GitHub Code Scanning, GitLab SAST
* 🟪 **Flow specs** — Named, traceable feature flows (e.g., "Signup pipeline": route → handler → validation → database → email notification). Each flow includes entry/exit points, all nodes/edges in path, invariants, and tests covering the flow.

### Additional linkers *(near-term)*

* 🟪 **Constant propagation** for dynamic routes (`BASE_URL + "/users"`)
* 🟪 **Middleware/proxy rewriting** detection

### Incremental analysis *(not on roadmap)*

This is an 18+ month effort minimum:
- Dependency tracking (which symbols affect which)
- Invalidation propagation (change X → re-analyze Y, Z)
- Cross-file type inference updates

TypeScript incremental took ~3 years, rust-analyzer ~2 years.

**Current mitigation:**
- Cached slices: Pre-compute common features
- Symbol index: O(1) lookup for "find definition of X"
- Partial re-analysis: Re-analyze changed files + direct importers only
- Accept latency: Full analysis on deep queries is OK with progress bars

### Scope and constraints

**Performance targets (with high-fidelity analysis):**

* **Small repo** (<100 files): <10 seconds
* **Medium repo** (~500 files): <60 seconds (2x current due to type checking)
* **Large repo** (2000+ files): <5 minutes full analysis
* **Context router query**: <2 seconds for typical slice assembly

**Complexity acknowledgment:**

* **Natural language query parsing** requires embedding models + semantic search
* **Dataflow slicing** is NP-hard in general; even heuristic solutions are multi-month research
* **Impact prediction** requires whole-program analysis with test coverage correlation

If dataflow proves infeasible, agent-guided slicing (agents specify hops/filters via DSL) is a simpler alternative.

**Non-goals for future work:**

* "Prove programs correct" in the formal methods sense
* Real-time collaboration or social features
* Hosted SaaS offering or marketplace monetization
* IDE integration (LSP server) or autonomous code editing

### Testing & CI enhancements *(near-term)*

**🟪 Longitudinal analysis ("slow thinking"):**

Property tests provide immediate pass/fail feedback ("fast thinking"). But some insights only emerge from patterns across many CI runs:
- Did node count suddenly drop 40%? (regression)
- Is edge detection improving over time? (progress)
- How does analysis time scale with repo size? (performance)

Concept: "Nonjudgmental fixtures"—run analysis on a real repo without asserting correctness, just observing metrics:

```python
def test_observatory(capsys):
    """Emit metrics for longitudinal analysis. No assertions on correctness."""
    result = analyze(Path("tests/fixtures/medium-repo"))
    print(json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "commit": os.environ.get("CI_COMMIT_SHA"),
        "nodes": len(result.nodes),
        "edges": len(result.edges),
        "nodes_by_kind": dict(Counter(n.kind for n in result.nodes)),
    }))
    assert validate_schema(result)  # Only hard check: didn't crash, valid schema
```

Infrastructure needed: persistent storage for metrics across CI runs, aggregation/visualization tooling, and anomaly detection (alert on significant changes). This is a fundamentally different paradigm than pytest's immediate feedback.

**Integration tests:** Add optional integration tests that validate end-to-end behavior:

* Use `@pytest.mark.integration` marker
* Skip automatically when dependencies are not available
* Run only on explicit request (`pytest -m integration`)
* Catch environment-specific issues

## Appendix A: Release Lifecycle & Support

This appendix covers **process**: release lifecycle, support windows, and deprecation timelines. For the technical contract governing output schema stability, see [Appendix C](#appendix-c-schema-compatibility-contract).

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
* **Breaking changes only in MAJOR bumps** with 6-month migration period. See [Appendix C](#appendix-c-schema-compatibility-contract) for the technical definition of what constitutes a breaking change.

### Support windows

* **Current version**: Full support (bugs, features, security)
* **Previous MINOR**: Security fixes only, 12 months after next MINOR release
* **Previous MAJOR**: Security fixes only, 18 months after next MAJOR release
* **Unmaintained**: Versions >18 months old receive no updates

### Deprecation process

1. **Announce**: 6 months before removal, add deprecation warnings
2. **Document**: Migration guide published
3. **Support**: Old version maintained per support windows
4. **Remove**: After support window expires

### Example timeline

See `CHANGELOG.md` for the authoritative release history. The policy illustrated:

* When a new MAJOR version ships, the previous MAJOR enters "previous major" support (18-month clock starts)
* 18 months after the new MAJOR release, the old MAJOR becomes unsupported

## Appendix B: Telemetry & Privacy

### Default: Zero telemetry

By default, hypergumbo **sends no data** anywhere. All analysis is local-only.

### Opt-in crash reporting

Enable via `hypergumbo config --telemetry=on` or `hypergumbo_TELEMETRY=1` environment variable.

**What is collected** (only if opted in):
* Crash stack traces (sanitized: no code, no symbol names, no file paths)
* Performance metrics (file counts, timings, memory usage)
* Feature usage (which commands run, which flags used)
* Anonymized session ID (random UUID, not linked to identity)

**What is NEVER collected**:
* Source code
* Symbol names (function/class/variable names)
* File paths or directory names
* API keys or credentials
* IP addresses (beyond what HTTPS inherently reveals)

### Data retention

* Crash reports: 90 days
* Aggregated metrics: 2 years
* No raw session data retained beyond 30 days

### Third-party services
* If enabled, telemetry sent to Sentry (crash reporting) or similar
* Subject to their privacy policies (links provided in docs)

### Transparency
* Telemetry code is open source (can audit exactly what's sent)
* Privacy policy published at https://hypergumbo.iterabloom.com/privacy
* Opt-in status shown in `hypergumbo config --show`

## Appendix C: Schema Compatibility Contract

This appendix defines the **technical contract** for output consumers: which fields are immutable, how consumers must handle unknown fields, and what constitutes a breaking change. For release process, support windows, and deprecation timelines, see [Appendix A](#appendix-a-release-lifecycle--support).

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

### Testing Requirements

**Current test suite MUST** (see [§15 Testing & quality bar](#15-testing--quality-bar) for details):
- Verify structural invariants via property-based tests (valid IDs, confidence ranges, schema compliance, no dangling edge references)
- Validate against JSON Schema (automated validation)
- Test ID stability (same code → same IDs deterministically)
- Test deterministic ordering (sort keys defined, reproducible output)

**Future test suite MUST additionally:**
- Ensure current outputs pass future schema validation (with unknown field tolerance)
- Test mixed-fidelity graphs (AST edges + future typed edges coexist)
- Test view compilation (same IR → multiple views including behavior_map)

### Migration Path

**User upgrades hypergumbo CLI:**
```bash
pip install --upgrade hypergumbo

# Just works - no reinitialization needed
hypergumbo run  # Output compatible with existing tooling
```

**User upgrades output consumers (agents, tooling):**
1. Agents consuming `behavior_map.json` don't need changes
2. New fields under `meta.*` are optionally used (if agent wants higher fidelity)
3. Schema validation passes (new fields ignored by old consumers)
4. Agents can check `confidence_model` version, warn if too new

**Deprecation process (if ever needed):**
See [Appendix A](#appendix-a-release-lifecycle--support) for the deprecation process and support windows.

### Compatibility Testing

**Before releasing new major versions:**
- Run prior-version output through new parsers (ensure parsing succeeds)
- Run new output through prior-version consumers (ensure unknown fields ignored)
- Version compatibility matrix published

**Commitment:** No breaking changes to `behavior_map.json` view within v0.x series.

## Appendix D: Capsule System History

The original design included a "capsule" abstraction for composing custom analyzers from building blocks. The idea was that users would run `hypergumbo init` to create a capsule configuration, then `hypergumbo run` would execute analysis according to that configuration.

In practice, the general-purpose analyzer worked well enough that custom composition wasn't needed. The capsule system was never used:
- `init` created capsule files, but `run` ignored them
- The `Pack` concept for bundling passes was deprecated
- LLM-assisted plan generation was a proof of concept that added complexity without value

**Removed in v1.0.0:**
- `init` and `export-capsule` commands
- `plan.py`, `llm_assist.py`, `export.py` modules
- `Pack` class from catalog (framework-specific behavior handled by linker activation conditions and `--frameworks` flag)

**For historical reference**, see [history/capsule-system-v1.md](history/capsule-system-v1.md).

**Other archived v1.0 materials:** The original development milestones (Week 0-9 planning) have been archived to [history/planning-v1.md](history/planning-v1.md). Original success criteria and validation gates have been archived to [history/validation-gates-v1.md](history/validation-gates-v1.md).
