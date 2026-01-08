# Hypergumbo Architectural Analysis — January 7, 2026 (updated 21:30)

## Summary

Hypergumbo's architecture grew organically, resulting in some incoherent abstractions and disconnected data flows. This document establishes a principled taxonomy and identifies concrete architectural improvements.

**Key insights:**
1. Languages, dialects, and frameworks are distinct concepts requiring different treatment
2. Analyzers should be pure language processors; framework knowledge belongs elsewhere
3. Framework detection should influence behavior, not just be stored
4. Linkers have different activation conditions (framework, language-pair, protocol)
5. Frameworks afford multiple concept types (routes, models, tasks, etc.)—routes are just one

**Key architectural decisions:**
6. **Pure analyzers:** Analyzers extract symbols + edges + rich metadata; NO framework knowledge
7. **Separate FRAMEWORK_PATTERNS phase:** Enriches symbols with framework concept metadata
8. **Semantic entry detection:** Entry kinds derived from enriched metadata, not path heuristics
9. **Data-driven patterns:** Framework patterns defined as data (YAML), not code
10. Default: auto-detect frameworks based on detected languages; user can override

---

## 1. The Fundamental Taxonomy

### 1.1 Languages

**Definition:** A programming language with its own grammar that requires a dedicated tree-sitter parser.

**Examples:** Python, JavaScript, Go, Rust, Java, C++

**Treatment:** Each language gets an analyzer module (`analyze/py.py`, `analyze/go.py`).

### 1.2 Dialects

**Definition:** A syntax extension to a base language that introduces grammar constructs the base parser cannot handle.

**Examples:**
- CUDA (C++ with `<<<blocks, threads>>>` kernel launch syntax)
- TypeScript (JavaScript with type annotations)
- JSX (JavaScript with XML literals)
- Objective-C (C with message-passing syntax)

**Treatment:** Each dialect gets its own analyzer module with its own tree-sitter grammar. Dialects are *not* frameworks—they require separate parsers.

**Key distinction:** If tree-sitter-X can't parse it, it's a dialect, not a framework.

### 1.3 Frameworks

**Definition:** A library or ecosystem that uses the host language's existing syntax but introduces conventions, patterns, and APIs that benefit from specialized detection.

**Examples:**
- **Python:** FastAPI, Flask, Django, Celery
- **JavaScript:** Express, Koa, React, Next.js
- **Java:** Spring, Micronaut
- **Elixir:** Phoenix

**Treatment:** Frameworks are detected by `profile.py` and configure:
- Which patterns the FRAMEWORK_PATTERNS phase applies (routes, models, tasks, etc.)
- Which linkers to activate
- Which entry kinds to detect (routes, tasks, commands, handlers)

Note: Analyzers are NOT configured by frameworks—they are pure language processors that capture rich metadata. Framework-specific detection happens in a separate phase.

**Key distinction:** If tree-sitter-X can parse it, it's a framework (or just a library).

### 1.4 Framework-Afforded Concepts

**Principle:** Frameworks afford concepts that don't exist at the language level.

No programming language has routes, models, middleware, or tasks as syntax. These are conventions provided by frameworks. Routes are just one example of a broader taxonomy of framework-afforded concepts.

**A taxonomy of framework-afforded concepts:**
```
Framework
│
├── Request/Response Layer
│   ├── Routes           @app.get("/path"), @GetMapping
│   ├── Middleware       app.use(), @middleware
│   ├── WebSocket        @socket.on(), channels
│   └── GraphQL          @resolver, type Query
│
├── Data Layer
│   ├── Models/Entities  class User(Model), @Entity
│   ├── Validators       class UserSchema(BaseModel), @Valid
│   ├── Migrations       alembic, django migrations
│   └── Repositories     @Repository, BaseRepository
│
├── Async Layer
│   ├── Event handlers   @on("event"), EventEmitter
│   ├── Background tasks @task, @job, workers
│   ├── Message consumers@consumer, @subscriber
│   └── Scheduled jobs   @cron, @scheduled
│
├── UI Layer
│   ├── Templates        {% block %}, <%= %>
│   ├── Components       @Component, function Component()
│   └── Lifecycle hooks  useEffect, onCreate, mounted()
│
└── Infrastructure Layer
    ├── DI bindings      @Bean, @Injectable, Depends()
    ├── CLI commands     @command, management commands
    └── Configuration    @ConfigurationProperties, settings
```

**Examples across frameworks:**

| Concept | FastAPI | Express | Spring | Django |
|---------|---------|---------|--------|--------|
| Route | `@app.get()` | `app.get()` | `@GetMapping` | `urlpatterns` |
| Middleware | `Depends()` | `app.use()` | `@Filter` | `MIDDLEWARE` |
| Model | Pydantic | Mongoose | `@Entity` | `models.Model` |
| Task | Celery `@task` | Bull | `@Async` | Celery `@task` |
| DI | `Depends()` | - | `@Autowired` | - |

**Implication:** Detection of *any* framework-afforded concept should only happen when the framework is specified.

```
ANALYZERS (always, pure language):
  → Extract symbols (functions, classes)
  → Extract syntax-visible edges (calls, imports)
  → Capture rich metadata (decorators + args, base classes, imports)
  → NO framework-specific logic

FRAMEWORK_PATTERNS (only when framework specified):
  → Examine symbol metadata
  → Apply framework-specific patterns
  → Enrich symbols with concept metadata:
    - route: {path: "/users", method: "GET"}
    - model: {orm: "sqlalchemy"}
    - task: {queue: "celery"}
```

This eliminates false positives and makes detection efficient—we only check for patterns relevant to detected/specified frameworks. It also cleanly separates concerns: analyzers know about languages, FRAMEWORK_PATTERNS knows about frameworks.

### 1.5 How Categories Map to Processing

```
┌─────────────────────────────────────────────────────────────────┐
│                        SOURCE CODE                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  LANGUAGES          DIALECTS              FRAMEWORKS            │
│  (own grammar)      (extended grammar)    (host grammar)        │
│                                                                 │
│  Python             CUDA (C++)            FastAPI (Python)      │
│  JavaScript         TypeScript (JS)       Express (JavaScript)  │
│  C++                JSX (JavaScript)      Spring (Java)         │
│  Go                 Objective-C (C)       Phoenix (Elixir)      │
│  Java               ...                   gRPC (multi-language) │
│  ...                                      GraphQL (multi-lang)  │
│                                                                 │
│  ↓                  ↓                     ↓                     │
│  Analyzer           Analyzer              Detection +           │
│  (dedicated)        (dedicated)           Configuration         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Analyzer vs Linker: The Principled Distinction

### 2.1 The Core Principle

**Analyzer:** Extracts edges visible in a single parse tree.
**Linker:** Creates edges by pattern-matching across multiple symbols.
```
Can you see the edge in a single parse tree?
├── Yes → Analyzer's responsibility
└── No  → Linker's responsibility
```

### 2.2 What Analyzers Do (Revised Architecture)

**Principle:** Analyzers are pure language processors with NO framework knowledge.
Analyzers parse source files and extract:
1. **Symbols:** Functions, classes, methods, variables, types
2. **Syntax-visible edges:**
   - `calls`: Function A calls function B (call expression in AST)
   - `imports`: Module A imports module B (import statement in AST)
   - `extends`: Class A extends class B (inheritance in AST)
   - `instantiates`: Code creates instance of class (constructor call in AST)
3. **Rich metadata** (enables downstream framework detection):
   - All decorators with their arguments
   - Base classes for each class
   - Import aliases (for resolving qualified names)
   - Function parameter types/annotations

**Analyzer structure (revised):**
```
┌─────────────────────────────────────────────────────────────────┐
│                      LANGUAGE ANALYZER                          │
│                   (Pure language, no frameworks)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Symbol extraction:                                             │
│    - Functions, classes, methods, variables                     │
│    - With RICH METADATA:                                        │
│      - decorators: [{"name": "app.get", "args": ["/users"]}]    │
│      - base_classes: ["BaseModel", "Generic[T]"]                │
│      - parameters: [{"name": "db", "type": "Session"}]          │
│                                                                 │
│  Edge extraction:                                               │
│    - calls, imports, extends, instantiates                      │
│    - All syntax-visible relationships                           │
│                                                                 │
│  NO framework-specific logic:                                   │
│    - Doesn't know what FastAPI is                               │
│    - Doesn't know what a "route" is                             │
│    - Just captures everything the AST contains                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```
**Key insight:** By capturing rich metadata, analyzers enable FRAMEWORK_PATTERNS to detect concepts without re-parsing the AST. The decorator `{"name": "app.get", "args": ["/users"]}` is just data to the analyzer; FRAMEWORK_PATTERNS knows it means "FastAPI route to /users".

### 2.3 What Linkers Do

Linkers examine the symbol graph and create edges by:
1. **String matching:** HTTP client `"/api/users"` → server route `"/api/users"`
2. **Naming conventions:** gRPC stub `UserServiceStub` → proto `service UserService`
3. **Semantic inference:** Event emit `"user.created"` → handler `on("user.created")`

### 2.4 Linker Subcategories

#### Protocol Linkers (framework-agnostic)
Match on protocol semantics, regardless of framework.

| Linker | Pattern | Activation |
|--------|---------|------------|
| `http.py` | URL path matching | Always |
| `websocket.py` | WS endpoint matching | Always |
| `database_query.py` | SQL table/column matching | Always |
| `message_queue.py` | Topic/channel matching | Always |
| `event_sourcing.py` | Event name matching | Always |
#### Bridge Linkers (language-pair-specific)
Connect symbols across language boundaries via FFI conventions.

| Linker | Bridge | Activation |
|--------|--------|------------|
| `jni.py` | Java ↔ C/C++ | Both languages present |
| `swift_objc.py` | Swift ↔ Objective-C | Both languages present |

#### Framework Linkers (framework-specific)
Implement framework-specific conventions.

| Linker | Framework | Activation |
|--------|-----------|------------|
| `grpc.py` | gRPC/Protobuf | Framework detected |
| `graphql.py` | GraphQL | Framework detected |
| `graphql_resolver.py` | GraphQL | Framework detected |
| `phoenix_ipc.py` | Phoenix | Framework detected |

### 2.5 Proposed Linker Activation Model
```python
@dataclass
class LinkerActivation:
    """Conditions under which a linker should run."""
    always: bool = False                    # Run unconditionally
    frameworks: list[str] = field(default_factory=list)  # Run if any detected
    language_pairs: list[tuple[str, str]] = field(default_factory=list)  # Run if both present
    protocols: list[str] = field(default_factory=list)   # Future: protocol detection
```

### 2.6 The FRAMEWORK_PATTERNS Phase (New)

**Purpose:** Enrich symbols with framework-specific concept metadata by examining the rich metadata captured by analyzers.
**Position in pipeline:** After analyzers, before linkers.
```
┌─────────────────────────────────────────────────────────────────┐
│                     FRAMEWORK_PATTERNS                          │
│                  (Configured by detected frameworks)            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Input: Symbols with rich metadata from analyzers               │
│                                                                 │
│  Processing:                                                    │
│    - Match symbol metadata against framework patterns           │
│    - Patterns are DATA (YAML/JSON), not code                    │
│                                                                 │
│  Output: Enriched symbols with concept metadata                 │
│                                                                 │
│  Example:                                                       │
│    Input symbol:                                                │
│      name: "get_users"                                          │
│      decorators: [{"name": "app.get", "args": ["/users"]}]      │
│                                                                 │
│    FastAPI pattern matches → adds metadata:                     │
│      concepts:                                                  │
│        route: {path: "/users", method: "GET", framework: "fastapi"}│
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Data-driven patterns:**
```yaml
# frameworks/fastapi.yaml
id: fastapi
language: python

patterns:
  routes:
    - decorator_match: "*.get|*.post|*.put|*.delete|*.patch"
      extract:
        path: decorator.args[0]
        method: decorator.name.split('.')[-1]

  validators:
    - base_class_match: "pydantic.BaseModel|BaseModel"
      metadata:
        validator: true
        framework: fastapi

  middleware:
    - parameter_type_match: "Depends"
      metadata:
        dependency_injection: true
```

**Benefits of this architecture:**
1. **Adding a new framework = adding a YAML file**, not editing Python code
2. **Analyzers are simpler** - just capture everything, no framework logic
3. **Patterns are testable in isolation** - don't need to run full parser
4. **Clear separation of concerns** - languages vs frameworks

### 2.7 Semantic Entry Detection (New)

**The problem with path heuristics:**
```python
# Current approach (entrypoints.py) - PROBLEMATIC
if "/routes/" in path and path.endswith(".ts"):
    return Entrypoint(kind="express_route", confidence=0.85)  # False positive!
```
This causes false positives (React Router files flagged as Express routes).

**The solution: semantic detection from enriched metadata:**
```python
# Proposed approach - uses FRAMEWORK_PATTERNS output
def detect_entry_kinds(symbols):
    entries = []
    for sym in symbols:
        # Check enriched metadata, not file paths
        if "route" in sym.concepts:
            entries.append(Entry(symbol=sym, kind="route", confidence=0.95))
        if "task" in sym.concepts:
            entries.append(Entry(symbol=sym, kind="task", confidence=0.95))
        if "cli_command" in sym.concepts:
            entries.append(Entry(symbol=sym, kind="command", confidence=0.95))
    return entries
```

**Why this is better:**

| Aspect | Path heuristics | Semantic detection |
|--------|-----------------|-------------------|
| Detection basis | File path patterns | Symbol metadata |
| Confidence | Guessed from path | Inherited from pattern match |
| False positives | Common (path collisions) | Rare (semantic match) |
| Framework-aware | No | Yes (metadata includes framework) |

**Example: React Router false positive eliminated:**
```
Path heuristics:
  File: frontend/src/routes/login.tsx
  Path matches: /routes/*.tsx
  Result: Flagged as Express route ❌

Semantic detection:
  File: frontend/src/routes/login.tsx
  FRAMEWORK_PATTERNS: No Express patterns matched (no app.get decorator)
  Symbol concepts: {} (empty)
  Result: Not flagged ✅
```

---

## 3. Framework as First-Class Concept

### 3.1 Current State (Broken)
```
profile.py detects:
├── languages → Used by catalog to suggest passes
└── frameworks → UNUSED (stored in output JSON only)
```
Framework detection exists but doesn't influence behavior.

### 3.2 Desired State
```
profile.py detects:
├── languages → Select which ANALYZERS run (pure language, no configuration)
└── frameworks → Configure:
                 ├── FRAMEWORK_PATTERNS (which pattern files to apply)
                 ├── LINKERS (which linkers to activate)
                 └── (Entry kinds derived automatically from enriched metadata)
```
**Key difference:** Analyzers are NOT configured. They always capture maximum metadata. Configuration only affects FRAMEWORK_PATTERNS and LINKERS.

### 3.3 Proposed Framework Model
```python
@dataclass
class Framework:
    """A framework that hypergumbo understands."""
    id: str                           # "fastapi", "grpc", "phoenix"
    languages: list[str]              # ["python"] or ["python", "go", "java"] for gRPC

    # Detection
    manifest_markers: dict[str, list[str]]  # {"pyproject.toml": ["fastapi"]}
    file_markers: list[str]           # ["**/routes/**/*.py"]

    # Framework-afforded concept patterns (organized by layer)
    patterns: FrameworkPatterns

    # Cross-cutting
    linkers: list[str]                # ["grpc", "http"]
    entrypoint_kinds: list[str]       # ["route", "task", "command"]

    # Efficiency hints
    relevant_paths: list[str]         # Where to focus analysis
    irrelevant_paths: list[str]       # Where to skip


@dataclass
class FrameworkPatterns:
    """Patterns for detecting framework-afforded concepts."""

    # Request/Response layer
    routes: list[Pattern] = field(default_factory=list)
    middleware: list[Pattern] = field(default_factory=list)
    websocket_handlers: list[Pattern] = field(default_factory=list)
    graphql_resolvers: list[Pattern] = field(default_factory=list)

    # Data layer
    models: list[Pattern] = field(default_factory=list)
    validators: list[Pattern] = field(default_factory=list)
    repositories: list[Pattern] = field(default_factory=list)

    # Async layer
    event_handlers: list[Pattern] = field(default_factory=list)
    background_tasks: list[Pattern] = field(default_factory=list)
    message_consumers: list[Pattern] = field(default_factory=list)
    scheduled_jobs: list[Pattern] = field(default_factory=list)

    # UI layer
    components: list[Pattern] = field(default_factory=list)
    lifecycle_hooks: list[Pattern] = field(default_factory=list)

    # Infrastructure layer
    di_bindings: list[Pattern] = field(default_factory=list)
    cli_commands: list[Pattern] = field(default_factory=list)
    configuration: list[Pattern] = field(default_factory=list)
```

**Example framework definition:**
```python
FASTAPI = Framework(
    id="fastapi",
    languages=["python"],
    manifest_markers={"pyproject.toml": ["fastapi"], "requirements.txt": ["fastapi"]},
    patterns=FrameworkPatterns(
        routes=[
            Pattern(decorator="app.get", extract_path=True),
            Pattern(decorator="router.post", extract_path=True),
        ],
        middleware=[
            Pattern(decorator="app.middleware"),
            Pattern(function_arg="Depends"),
        ],
        validators=[
            Pattern(base_class="BaseModel"),
        ],
    ),
    linkers=["http"],
    entrypoint_kinds=["route"],
)

CELERY = Framework(
    id="celery",
    languages=["python"],
    manifest_markers={"pyproject.toml": ["celery"]},
    patterns=FrameworkPatterns(
        background_tasks=[
            Pattern(decorator="app.task"),
            Pattern(decorator="shared_task"),
        ],
        scheduled_jobs=[
            Pattern(decorator="periodic_task"),
        ],
    ),
    linkers=["message_queue"],
    entrypoint_kinds=["task"],
)
```

### 3.4 Cross-Language Frameworks

Some frameworks span multiple languages:

| Framework    | Languages                       | Communication      |
| ------------ | ------------------------------- | ------------------ |
| gRPC         | Proto + Python/Go/Java/C++/...  | IDL → stubs        |
| GraphQL      | GraphQL + any resolver language | Schema → resolvers |
| Thrift       | Thrift + multiple languages     | IDL → stubs        |
| JNI          | Java + C/C++                    | FFI bridge         |
| React Native | JavaScript + Swift/Kotlin       | Platform bridge    |
| Electron     | JavaScript + Node.js            | IPC                |
This is why linkers exist—they're the cross-language counterpart to analyzers.

---

## 4. Data Flow

```
SOURCE FILES
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         PROFILE                                 │
│  1. Detect languages/dialects (by file extension)               │
│  2. Detect frameworks (by manifest markers, scoped to languages)│
└─────────────────────────────────────────────────────────────────┘
     │
     │             ┌─────────────────────────────────────────────┐
     │             │           USER OVERRIDE (optional)          │
     │             │  --frameworks=fastapi,grpc  (explicit)      │
     │             │  --frameworks=none          (base only)     │
     │             │  --frameworks=all           (exhaustive)    │
     │             └─────────────────────────────────────────────┘
     │                                │
     ├── languages ──────────────────┐│
     │                               ││
     └── frameworks (detected/override)
                                     ││
     ▼                               ▼▼
┌──────────────────────────────────────────────────────────────────┐
│                          ANALYZERS                               │
│                    (Pure language, NO configuration)             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Always capture maximum information:                             │
│  - Symbols (functions, classes, methods)                         │
│  - Edges (calls, imports, extends, instantiates)                 │
│  - Rich metadata:                                                │
│    - decorators: [{"name": "app.get", "args": ["/users"]}]       │
│    - base_classes: ["BaseModel"]                                 │
│    - parameters: [{"name": "db", "type": "Session"}]             │
│                                                                  │
│  NO framework knowledge - just capture everything from AST       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
     │
     │  Symbols + edges + rich metadata
     ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FRAMEWORK_PATTERNS                          │
│                  (Configured by frameworks)                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Examines symbol metadata, applies framework patterns            │
│  Patterns are DATA (YAML), not code                              │
│                                                                  │
│  Enriches symbols with concept metadata:                         │
│  - route: {path, method}    - model: {orm, table}                │
│  - task: {queue, async}     - hook: {lifecycle}                  │
│                                                                  │
│  If --frameworks=none: skip (no enrichment)                      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
     │
     │  Enriched symbols
     ▼
┌──────────────────────────────────────────────────────────────────┐
│                          LINKERS                                 │
│                (Activated by framework/language)                 │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Creates edges across:                                           │
│  - Languages (JNI, Swift/ObjC)                                   │
│  - Services (HTTP, gRPC)                                         │
│  - Protocols (MQ, WebSocket)                                     │
│                                                                  │
│  Uses enriched symbol metadata (e.g., route paths for HTTP)      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
     │
     │  Enriched symbols + cross-boundary edges
     ▼
┌──────────────────────────────────────────────────────────────────┐
│                        ENTRY_KINDS                               │
│                    (Semantic detection)                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Queries enriched symbol metadata, NOT path heuristics           │
│                                                                  │
│  if "route" in sym.concepts → Entry(kind="route")                │
│  if "task" in sym.concepts  → Entry(kind="task")                 │
│                                                                  │
│  High confidence (0.95) from semantic match                      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│     SKETCH       │     │       RUN        │     │      SLICE       │
│    (Markdown)    │     │     (JSON)       │     │   (subgraph)     │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

**Key insights:**
1. **Analyzers are unconfigured** - they always capture maximum metadata, enabling downstream flexibility
2. **FRAMEWORK_PATTERNS is data-driven** - adding frameworks means adding YAML, not code
3. **LINKERS depend on enriched metadata** - protocol linkers need route/task info from FRAMEWORK_PATTERNS
4. **Entry detection is semantic** - uses enriched metadata, not path heuristics
5. **Framework detection scoped to languages** - only check FastAPI if Python detected

---

## 5. Current Gaps and Migration Path

### 5.1 Identified Gaps

| Gap | Current State | Desired State (Revised Architecture) |
|-----|---------------|--------------------------------------|
| Framework detection unused | Stored in JSON only | Configures FRAMEWORK_PATTERNS + linkers |
| Analyzers have framework code | Route patterns baked into analyzers | Pure analyzers with rich metadata capture |
| Only routes detected | Route patterns only | All framework-afforded concepts (models, tasks, etc.) |
| Patterns are code | Framework logic in Python | Framework patterns as DATA (YAML) |
| Packs are vestigial | Never auto-selected | Remove or repurpose as framework bundles |
| Linkers unconditional | All linkers always run | Activation conditions |
| Entrypoints use path heuristics | Guessing from file paths | Semantic detection from enriched metadata |
| `entrypoints.py` misnamed | Path-based heuristics | Becomes thin wrapper over FRAMEWORK_PATTERNS output |

### 5.2 Migration Path

**v0.6.x (current):**
- Monolithic analyzers with framework patterns baked in
- Vestigial packs
- Framework detection stored but unused
- Exclusion-based false positive fixes (whack-a-mole)
- Path-based entrypoint heuristics

**v0.7.x (foundation):** *[Low Risk]*
1. Document the revised architecture in ARCHITECTURE.md
2. Enrich analyzer metadata capture - decorators with args, base classes, parameter types
3. Add `--frameworks` flag with `none/all/explicit` options
4. Add linker activation conditions (framework, language-pair)
5. Remove or deprecate Packs

**v0.8.x (separation):** *[Moderate Risk]*
6. Create framework pattern YAML schema - define structure for data-driven patterns
7. Implement FRAMEWORK_PATTERNS phase - reads YAML, matches against symbol metadata
8. Extract FastAPI patterns as first YAML file (proof of concept)
9. Remove FastAPI detection from py.py - analyzer becomes pure
10. Add framework → linker mapping (only run gRPC linker if gRPC detected)

**v0.9.x (semantic entry detection):** *[Moderate Risk]*
11. Implement semantic entry detection - query enriched metadata, not paths
12. Refactor `entrypoints.py` - becomes thin wrapper over FRAMEWORK_PATTERNS output
13. Deprecate path heuristics - retain only for `main()` fallback
14. Validate with bakeoff - verify React Router false positives eliminated

**v1.0.x (complete):** *[Higher Risk]*
15. Extract all framework patterns to YAML - Express, Spring, Django, etc.
16. All analyzers become pure - no framework knowledge
17. Expand concept taxonomy - models, tasks, hooks, DI, etc.
18. Plugin architecture for custom framework patterns

---

## 6. Design Decisions

### 6.1 Pure Analyzers + Separate FRAMEWORK_PATTERNS Phase

**Decision:** Analyzers are pure language processors with NO framework knowledge. Framework concept detection happens in a separate FRAMEWORK_PATTERNS phase.

**Rationale:**
- Separation of concerns: analyzers know languages, FRAMEWORK_PATTERNS knows frameworks
- Adding a new framework = adding a YAML file, not editing Python code
- Patterns are testable in isolation without running full parsers
- Analyzers become simpler and more maintainable

**Architecture:**
```
ANALYZERS (pure language, always):
  → Symbols + syntax-visible edges
  → Rich metadata (decorators, base classes, parameters)
  → NO framework knowledge

FRAMEWORK_PATTERNS (configured by detected frameworks):
  → Examines symbol metadata
  → Applies framework patterns (DATA, not code)
  → Enriches symbols with concept metadata
```

**The taxonomy of framework-afforded concepts:**
- **Request/Response:** Routes, middleware, WebSocket handlers, GraphQL resolvers
- **Data:** Models, validators, repositories, migrations
- **Async:** Event handlers, background tasks, message consumers, scheduled jobs
- **UI:** Components, lifecycle hooks, templates
- **Infrastructure:** DI bindings, CLI commands, configuration

### 6.2 Framework Specification: User Hints with Smart Defaults

**Decision:** Default is auto-detect based on detected languages. User can override.
**Default behavior:** Assume the user doesn't know what a "framework" is. Detect languages/dialects, then check for frameworks that are plausible given those languages.
```
Detected: Python files
  → Check for: FastAPI, Flask, Django, Celery, etc.
  → Found: fastapi in pyproject.toml
  → Activate: FastAPI patterns (routes, middleware, validators)

Detected: TypeScript files
  → Check for: Express, Koa, Next.js, etc.
  → Found: express in package.json
  → Activate: Express patterns (routes, middleware, WebSocket handlers)
```

**User overrides:**

| Flag | Behavior |
|------|----------|
| `--frameworks=fastapi,grpc` | Use exactly these frameworks |
| `--frameworks=none` | No frameworks; base analysis only (symbols + edges, no routes) |
| `--frameworks=all` | Check all framework patterns (exhaustive mode) |
| *(no flag)* | Auto-detect based on detected languages (default) |
**Failure reporting:**
- If explicit and nothing found → Report: "You specified FastAPI but no routes detected"
- If auto-detect finds nothing → That's fine; proceed with base analysis
- If `--frameworks=all` → Exhaustive check is intentional; false negatives imply under- or mis-specified YAML files in `frameworks/` directory.

### 6.3 Multi-Framework Repos: Not a Problem

**Decision:** Detect all, run all, union results.
**Rationale:** This is straightforward, not a conundrum.
```
Repo has: FastAPI + Celery + gRPC

Action:
  - Detect all three
  - Run patterns for all three
  - Run linkers for all three
  - Union the results
```
Subdirectory scoping (different frameworks in different dirs) is premature optimization. Just detect at repo level and let patterns match where they match.

### 6.4 Framework Versions: Distinct If Patterns Conflict

**Decision:** Treat versions as distinct frameworks only if patterns conflict.
**Rationale:** Pragmatic. If patterns don't conflict, bundle them.
```
Flask example:
  flask-1.x:  @app.route("/path", methods=["GET"])
  flask-2.x:  @app.get("/path")

  These don't conflict → "Flask" checks both patterns
  Split only if false positives emerge
```
Version detection from manifests is complex and probably not worth it unless patterns actually conflict.

### 6.5 Resolved Questions
1. **Where is the boundary between analyzer and linker?**
   - **Resolved:** Analyzers capture syntax-visible data (symbols, edges, metadata). FRAMEWORK_PATTERNS enriches symbols with concept metadata. Linkers create cross-boundary edges.
   - Framework concept detection is NEITHER in analyzers NOR in linkers—it's in FRAMEWORK_PATTERNS.
2. **Should there be a "framework pack" that bundles analyzer config + linkers?**
   - **Resolved:** In the new architecture, a "framework" IS essentially a pack: it specifies which pattern YAML to use, which linkers to activate, and which entry kinds result.
   - The Framework dataclass (section 3.3) replaces the vestigial Pack abstraction with something coherent.
3. **Where should framework patterns live?**
   - **Resolved:** Framework patterns are DATA (YAML files), not code. They live in a `frameworks/` directory and are loaded/applied by the FRAMEWORK_PATTERNS phase.
4. **How to eliminate entrypoint false positives?**
   - **Resolved:** Semantic entry detection. Entry kinds are derived from enriched symbol metadata, not path heuristics. If FRAMEWORK_PATTERNS didn't enrich a symbol with concept metadata, it's not an entry point of that kind.

---

## Appendix A: Module Inventory (Current → Future)

### Analyzers (68 total)
One per language/dialect. Located in `src/hypergumbo/analyze/`.

**Current state:** Framework patterns baked in.
**Future state:** Pure language processors with rich metadata capture.

Key examples:
- `py.py` - Python
  - Current: includes FastAPI/Flask/Django patterns
  - Future: pure language, captures decorators/base classes
- `js_ts.py` - JavaScript/TypeScript
  - Current: includes Express/Koa/React patterns
  - Future: pure language, captures all AST metadata
- `go.py` - Go (already relatively pure)
- `java.py` - Java
  - Current: includes Spring/Micronaut patterns
  - Future: pure language, captures annotations/inheritance
- `cuda.py` - CUDA (dialect of C++)
- `proto.py` - Protocol Buffers

### Linkers (14 total)
Cross-boundary edge creation. Located in `src/hypergumbo/linkers/`.
- Protocol: `http.py`, `websocket.py`, `message_queue.py`, `event_sourcing.py`, `ipc.py`, `database_query.py`
- Bridge: `jni.py`, `swift_objc.py`
- Framework: `grpc.py`, `graphql.py`, `graphql_resolver.py`, `phoenix_ipc.py`
- Other: `dependency.py`
### Core Modules
- `profile.py` - Language and framework detection
- `catalog.py` - Pass/Pack registry (vestigial; to be deprecated)
- `entrypoints.py` - Entry point detection
  - Current: path-based heuristics
  - Future: semantic detection from enriched metadata
- `ir.py` - Symbol, Edge, AnalysisRun data structures
- `discovery.py` - File discovery with excludes
- `slice.py` - Graph slicing for context extraction
- `sketch.py` - Markdown generation
### New Modules (Proposed)
- `framework_patterns.py` - FRAMEWORK_PATTERNS phase implementation
  - Loads framework YAML files
  - Matches patterns against symbol metadata
  - Enriches symbols with concept metadata
- `frameworks/*.yaml` - Data-driven framework pattern definitions
  - `fastapi.yaml`, `express.yaml`, `spring.yaml`, etc.

---

## Appendix B: Glossary

### Core Concepts

| Term | Definition |
|------|------------|
| **Language** | Programming language with own grammar (Python, Go) |
| **Dialect** | Syntax extension requiring own grammar (CUDA, TypeScript) |
| **Framework** | Library/ecosystem using host grammar (FastAPI, Express) |
| **Analyzer** | Module that parses one language/dialect; extracts symbols, edges, and rich metadata. In revised architecture: pure language, NO framework knowledge |
| **Linker** | Module that creates edges by pattern-matching across symbols |
| **FRAMEWORK_PATTERNS** | Phase that enriches symbols with framework concept metadata by matching patterns against symbol metadata |
| **Symbol** | Code entity: function, class, method, variable |
| **Edge** | Relationship between symbols: calls, imports, extends |
| **Rich metadata** | Decorator names/args, base classes, parameter types—captured by analyzers to enable downstream pattern matching |
| **Semantic entry detection** | Deriving entry kinds from enriched symbol metadata rather than path heuristics |

### Framework-Afforded Concepts

| Term | Definition |
|------|------------|
| **Framework-afforded concept** | A pattern/convention provided by a framework, not the language |
| **Route** | HTTP path → handler mapping (FastAPI `@app.get`, Express `app.get`) |
| **Model/Entity** | ORM or data class (Django `models.Model`, SQLAlchemy, `@Entity`) |
| **Middleware** | Request/response interceptor (Express `app.use`, FastAPI `Depends`) |
| **Background task** | Async job (Celery `@task`, Sidekiq worker) |
| **Event handler** | Callback for events (Socket.IO `on()`, EventEmitter) |
| **Lifecycle hook** | Framework callback (React `useEffect`, Android `onCreate`) |
| **DI binding** | Dependency injection wiring (Spring `@Bean`, Angular `@Injectable`) |
| **CLI command** | Framework command pattern (Django management commands) |
| **Validator** | Data validation schema (Pydantic `BaseModel`, Zod) |

### Concept Layers

| Layer | Concepts |
|-------|----------|
| **Request/Response** | Routes, middleware, WebSocket handlers, GraphQL resolvers |
| **Data** | Models, validators, repositories, migrations |
| **Async** | Event handlers, background tasks, message consumers, scheduled jobs |
| **UI** | Components, lifecycle hooks, templates |
| **Infrastructure** | DI bindings, CLI commands, configuration |

### Legacy/Vestigial Terms

| Term | Definition |
|------|------------|
| **Pass** | Named analyzer unit in catalog (may be deprecated) |
| **Pack** | Bundle of passes (vestigial; to be removed or repurposed) |
| **Entrypoint** | Where execution begins—being generalized to all framework-afforded entry kinds |

---

*The key architectural insight is that framework knowledge should be externalized from analyzers into a separate, data-driven FRAMEWORK_PATTERNS phase. This enables adding frameworks via YAML files rather than code changes, and eliminates false positives through semantic entry detection.*
