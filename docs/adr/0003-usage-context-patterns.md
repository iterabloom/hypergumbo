<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0003 Extension: Usage Context Patterns

## Status
Proposed

## Relationship to ADR-0003

This document extends [ADR-0003: Architectural Analysis and Revision Plan](0003-architectural-analysis-and-revision-plan.md). It addresses a gap identified in the main ADR's framework concept table (section 1.4):

| Concept | FastAPI | Express | Spring | Django |
|---------|---------|---------|--------|--------|
| Route | `@app.get()` | `app.get()` | `@GetMapping` | `urlpatterns` |

Note that FastAPI and Spring use **decorators/annotations** (definition-based), while Express and Django use **function calls** (usage-based). The current YAML pattern system only handles the former. This document proposes a unified model that handles both.

## Context

ADR-0003 established that framework semantics should be externalized from analyzers into YAML pattern files. The initial implementation supports **definition-based patterns** that match against symbol metadata (what ADR-0003 calls "rich metadata": decorators, base classes, annotations, parameters).

However, many frameworks express semantics through **how symbols are used**, not how they're defined:

| Pattern Type | Example | Current Support |
|--------------|---------|-----------------|
| Decorator-based | `@app.get("/users")` | ✅ Supported |
| Call-based | `path("/users/", handler)` | ❌ Hardcoded |
| Data-driven | `{:get handler}` | ❌ Not supported |
| File-based | `pages/api/users.js` | ❌ Not supported |

An initial proposal (0003-call-patterns-extension.md) addressed call-based patterns specifically. This document generalizes that approach to a unified model covering all usage-based patterns.

## The Fundamental Insight

A symbol's framework semantics can be derived from **context**:

1. **Definition context** — How the symbol is defined (decorators, base classes)
2. **Usage context** — How the symbol is used (calls, data structures, exports)

The current pattern system handles (1). This proposal extends it to handle (2) in a unified way.

## Survey of Framework Patterns

### By Pattern Category

#### 1. Decorator/Annotation-Based (Definition Context)
```python
# Python/FastAPI
@app.get("/users")
def list_users(): ...

# Java/Spring
@GetMapping("/users")
public List<User> listUsers() { ... }

# TypeScript/NestJS
@Get("/users")
listUsers(): User[] { ... }
```
**Status**: ✅ Current YAML patterns handle this via `decorator`, `annotation`, `base_class` matching.

#### 2. Call-Based (Usage Context)
```python
# Python/Django
path("/users/", views.list_users)

# JavaScript/Express
app.get("/users", listUsers);

# Go/Gin
r.GET("/users", listUsers)

# Java/Javalin
app.get("/users", this::listUsers);
```
**Characteristic**: Symbol is passed as an argument to a route-registering function.

#### 3. Data-Driven (Usage Context)
```clojure
;; Clojure/Reitit
(def routes
  ["/api"
   ["/users" {:get list-users
              :post create-user}]])
```
```javascript
// JavaScript/Hapi
server.route({
    method: 'GET',
    path: '/users',
    handler: listUsers
});
```
**Characteristic**: Symbol appears as a value in a data structure that defines routes.

#### 4. File-Based (Usage Context)
```
# Next.js (pages router)
pages/api/users.js → exports default → /api/users

# Next.js (app router)
app/api/users/route.js → exports GET, POST → /api/users
```
**Characteristic**: File path determines route; exports determine handlers.

#### 5. Block/DSL-Based (Usage Context)
```ruby
# Ruby/Sinatra
get '/users' do
  # handler code
end

# Elixir/Phoenix
get "/users", UserController, :index
```
**Characteristic**: Handler is an inline block or referenced via module/action atoms.

### By Language

| Language | Frameworks | Primary Pattern Type |
|----------|------------|---------------------|
| Python | FastAPI, Flask | Decorator |
| Python | Django | Call |
| Java | Spring, JAX-RS | Annotation |
| Java | Vert.x, Javalin, Spark | Call |
| JavaScript | NestJS | Decorator |
| JavaScript | Express, Koa, Fastify | Call |
| JavaScript | Hapi | Data (config object) |
| JavaScript | Next.js | File + Export |
| Go | Gin, Echo, Chi, Fiber | Call |
| Ruby | Rails, Sinatra | Call/Block DSL |
| Elixir | Phoenix | Macro/Call DSL |
| Clojure | Compojure | Macro |
| Clojure | Reitit | Data |
| Rust | Actix, Rocket | Attribute macro |
| Rust | Axum | Call (builder) |

## Proposed Solution: Unified Usage Context

### Core Abstraction

All non-definition patterns share a common structure: **a symbol is used in a context that provides semantic meaning**.

```python
@dataclass
class UsageContext:
    """A context that gives semantic meaning to a symbol through its usage."""

    id: str
    kind: Literal["call", "data_value", "export", "macro"]
    context_name: str      # Function name, var name, file path, macro name
    symbol_ref: str        # ID of the symbol being contextualized
    position: str          # Arg position, map key, export name
    metadata: dict         # Context-specific structured data
    path: str              # File where this usage occurs
    span: Span             # Source location
```

### Mapping Frameworks to Usage Contexts

#### Django (Call Context)
```python
# Source: path("/users/", views.list_users)
UsageContext(
    kind="call",
    context_name="path",
    symbol_ref="python:views.py:10-15:list_users:function",
    position="args[1]",
    metadata={
        "args": ["/users/", "views.list_users"],
        "kwargs": {}
    },
)
```

#### Express (Call Context)
```javascript
// Source: app.get("/users", listUsers)
UsageContext(
    kind="call",
    context_name="app.get",
    symbol_ref="javascript:handlers.js:5-10:listUsers:function",
    position="args[1]",
    metadata={
        "args": ["/users", "listUsers"],
        "receiver": "app"
    },
)
```

#### Clojure Reitit (Data Value Context)
```clojure
;; Source: ["/users" {:get list-users}]
UsageContext(
    kind="data_value",
    context_name="routes",  # The var being defined
    symbol_ref="clojure:handlers.clj:5-8:list-users:function",
    position=":get",        # The map key
    metadata={
        "enclosing_vector": ["/users", {...}],
        "path_element": "/users"
    },
)
```

#### Hapi (Call + Nested Data Context)
```javascript
// Source: server.route({ method: 'GET', path: '/users', handler: listUsers })
UsageContext(
    kind="call",
    context_name="server.route",
    symbol_ref="javascript:handlers.js:5-10:listUsers:function",
    position="args[0].handler",  # Path within the argument
    metadata={
        "args": [{"method": "GET", "path": "/users", "handler": "listUsers"}]
    },
)
```

#### Next.js (Export Context)
```javascript
// Source: pages/api/users.js with `export default handler`
UsageContext(
    kind="export",
    context_name="pages/api/users.js",
    symbol_ref="javascript:pages/api/users.js:3-10:handler:function",
    position="default",
    metadata={
        "file_path": "pages/api/users.js",
        "export_type": "default"
    },
)
```

#### Ruby Sinatra (Call + Block Context)
```ruby
# Source: get '/users' do ... end
UsageContext(
    kind="call",
    context_name="get",
    symbol_ref="ruby:app.rb:5-10:<block>:block",  # Synthetic ID for block
    position="block",
    metadata={
        "args": ["/users"],
        "has_block": True
    },
)
```

### Extended YAML Pattern Syntax

```yaml
# Pattern matching against usage contexts
patterns:
  - concept: route
    usage:
      kind: call                              # Context type
      name: "^(path|re_path|url)$"           # Regex on context_name
      position: "args[1]"                     # Where symbol appears
    extract:
      path: "metadata.args[0]"               # How to get route path
      method: "literal:GET"                   # How to get HTTP method
```

### Complete Framework Examples

#### django.yaml
```yaml
id: django
language: python

patterns:
  # Existing: decorator-based (DRF)
  - concept: route
    decorator: "^api_view$"
    extract_method: "kwargs.methods"

  # New: call-based (URL patterns)
  - concept: route
    usage:
      kind: call
      name: "^(path|re_path|url)$"
      position: "args[1]"
    extract:
      path: "metadata.args[0]"
      method: "literal:GET"  # Django allows all methods by default
```

#### express.yaml
```yaml
id: express
language: javascript

patterns:
  - concept: route
    usage:
      kind: call
      name: "^(app|router)\\.(get|post|put|delete|patch|head|options)$"
      position: "args[-1]"  # Handler is last argument
    extract:
      path: "metadata.args[0]"
      method: "context_name | split:'.' | last | uppercase"
```

#### reitit.yaml
```yaml
id: reitit
language: clojure

patterns:
  - concept: route
    usage:
      kind: data_value
      name: ".*routes.*"  # Var name pattern
      position: "^:(get|post|put|delete|patch|head|options)$"
    extract:
      path: "metadata.path_element"
      method: "position | strip:':' | uppercase"
```

#### hapi.yaml
```yaml
id: hapi
language: javascript

patterns:
  - concept: route
    usage:
      kind: call
      name: "^server\\.route$"
      position: "args[0].handler"
    extract:
      path: "metadata.args[0].path"
      method: "metadata.args[0].method | uppercase"
```

#### nextjs.yaml
```yaml
id: nextjs
language: javascript

patterns:
  # Pages router
  - concept: route
    usage:
      kind: export
      name: "^pages/(.+)\\.(js|ts)x?$"
      position: "default"
    extract:
      path:
        source: "context_name"
        transform:
          - strip_prefix: "pages"
          - strip_suffix: "\\.(js|ts)x?$"
          - replace: ["/index$", ""]
          - replace: ["\\[([^\\]]+)\\]", ":$1"]
      method: "literal:ALL"

  # App router
  - concept: route
    usage:
      kind: export
      name: "^app/(.+)/route\\.(js|ts)$"
      position: "^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)$"
    extract:
      path:
        source: "context_name"
        transform:
          - strip_prefix: "app"
          - strip_suffix: "/route\\.(js|ts)$"
          - replace: ["\\[([^\\]]+)\\]", ":$1"]
      method: "position"
```

#### sinatra.yaml
```yaml
id: sinatra
language: ruby

patterns:
  - concept: route
    usage:
      kind: call
      name: "^(get|post|put|delete|patch|head|options)$"
      position: "block"
    extract:
      path: "metadata.args[0]"
      method: "context_name | uppercase"
    creates_symbol: true  # Block becomes a route symbol
```

### Extraction DSL

The `extract` section uses a mini-DSL for value extraction and transformation:

```yaml
extract:
  # Simple path into metadata
  path: "metadata.args[0]"

  # Literal value
  method: "literal:GET"

  # Pipeline of transformations
  method: "context_name | split:'.' | last | uppercase"

  # Complex transformation
  path:
    source: "context_name"
    transform:
      - strip_prefix: "pages/"
      - strip_suffix: "\\.(js|ts)$"
      - replace: ["\\[(.+?)\\]", ":$1"]
```

**Available transformations:**
- `uppercase`, `lowercase` — Case conversion
- `split:'.'` — Split on delimiter, returns list
- `first`, `last`, `[n]` — List indexing
- `strip:':'` — Remove characters
- `strip_prefix`, `strip_suffix` — Remove by pattern
- `replace: [pattern, replacement]` — Regex replacement
- `default:value` — Fallback if null
- `literal:value` — Constant value

### Pattern Matching Algorithm

```python
def enrich_with_usage_patterns(
    symbols: list[Symbol],
    usage_contexts: list[UsageContext],
    pattern_defs: list[FrameworkPatternDef],
) -> list[Symbol]:
    """Enrich symbols based on usage context patterns."""

    # Build reverse index: symbol_id → [UsageContext]
    symbol_to_contexts: dict[str, list[UsageContext]] = defaultdict(list)
    for ctx in usage_contexts:
        symbol_to_contexts[ctx.symbol_ref].append(ctx)

    # Match patterns
    for symbol in symbols:
        contexts = symbol_to_contexts.get(symbol.id, [])
        for ctx in contexts:
            for pattern_def in pattern_defs:
                for pattern in pattern_def.patterns:
                    if pattern.usage and pattern.matches_usage(ctx):
                        extracted = pattern.extract_values(ctx)
                        enrich_symbol(symbol, pattern.concept, extracted)

    return symbols
```

### Analyzer Requirements

Each analyzer needs to emit `UsageContext` records. The work required varies:

| Language | Call Contexts | Data Contexts | Export Contexts |
|----------|---------------|---------------|-----------------|
| Python | Extend edge detection | N/A | Add export tracking |
| JavaScript | Extend edge detection | For Hapi | Add export tracking |
| Go | Extend edge detection | N/A | N/A |
| Ruby | Add block tracking | N/A | N/A |
| Clojure | Already have | Add data literal tracking | N/A |
| Elixir | Add macro expansion | N/A | N/A |

**Key insight**: Most analyzers already detect calls for edge creation. Emitting `UsageContext` requires capturing additional metadata (arguments, positions) that's currently discarded.

## Limitations and Edge Cases

The unified model assumes **static, single-language, code-based pattern matching**. This section documents cases that challenge or break the model, their severity, and potential extensions.

### Fundamental Limitations (Out of Scope)

These cases cannot be addressed by static analysis and should be acknowledged as out of scope:

#### 1. Dynamic/Runtime Registration

```python
# Routes from config/database - not in source code
for endpoint in load_config()['endpoints']:
    app.add_route(endpoint['path'], getattr(handlers, endpoint['handler']))
```

```javascript
// Handler selected at runtime
app.get('/users', handlers[process.env.HANDLER_NAME]);
```

**Why it breaks**: There's no static symbol reference. The handler is determined at runtime from a string, config file, or environment variable. The reverse index `symbol → [UsageContext]` cannot be built.

**Status**: Out of scope. Static analysis cannot capture runtime decisions.

#### 2. Runtime Metaprogramming

```python
# Python exec/eval
exec(f"app.get('/users', {handler_name})")
```

```ruby
# Ruby eval
eval("get '/users' do #{handler_code} end")
```

**Why it breaks**: Code is constructed and executed at runtime. The AST doesn't contain the route definition.

**Status**: Out of scope. Would require runtime tracing, not static analysis.

---

### Moderate Challenges (Addressable with Extensions)

These cases can be addressed with extensions to the model:

#### 3. Higher-Order Functions / Wrapper Chains

```javascript
// Handler buried in wrapper chain
app.get('/users', authenticate(rateLimit(cache(listUsers))));
```

```python
# Decorator stacking on the call side
path('/users/', login_required(cache_response(views.list_users)))
```

**Challenge**: The UsageContext captures the outer call, but the actual handler symbol is nested inside.

**Potential extension**: Add an `unwrap` directive to patterns:

```yaml
patterns:
  - concept: route
    usage:
      kind: call
      name: "^app\\.(get|post)$"
      position: "args[-1]"
      unwrap:  # Recursively unwrap until we find a symbol reference
        - "^(authenticate|rateLimit|cache|withAuth)$"
    extract:
      path: "metadata.args[0]"
```

#### 4. Convention Over Configuration

```ruby
# Rails - routes inferred from naming
class UsersController < ApplicationController
  def index; end   # → GET /users (by convention)
  def show; end    # → GET /users/:id (by convention)
end
```

**Challenge**: No explicit registration—the route exists purely by naming convention.

**Potential extension**: Add `naming_convention` as a fourth context type:

```yaml
patterns:
  - concept: route
    naming_convention:
      class_pattern: "^(.+)Controller$"
      method_names: ["index", "show", "new", "create", "edit", "update", "destroy"]
      framework: rails
    extract:
      path: "class_name | capture_group:1 | pluralize | lowercase"
      method: "method_name | rails_action_to_http_method"
```

This recognizes that some frameworks use **naming context** rather than definition or usage context.

#### 5. String-Based Handler References

```python
# Django - string instead of symbol
urlpatterns = [
    path('users/', 'myapp.views.list_users'),  # String, not function
]
```

**Challenge**: No symbol reference to index—just a string that needs resolution.

**Potential extension**: Add a resolution phase before pattern matching:

```yaml
patterns:
  - concept: route
    usage:
      kind: call
      name: "^path$"
      position: "args[1]"
      resolve_string: true  # Resolve string to symbol before matching
    extract:
      path: "metadata.args[0]"
```

The analyzer would emit UsageContext with `symbol_ref: null` and `string_ref: "myapp.views.list_users"`. A resolution phase would attempt to resolve the string to a symbol ID.

#### 6. Multi-File Route Composition

```python
# blueprints/users.py
bp = Blueprint('users')
@bp.route('/list')
def list_users(): ...

# main.py
app.register_blueprint(bp, url_prefix='/api/v1/users')
```

**Challenge**: The final route `/api/v1/users/list` requires composing prefixes across files.

**Potential extension**: Track blueprint/router identity and compose paths:

```yaml
patterns:
  # Step 1: Capture blueprint routes
  - concept: blueprint_route
    decorator: "^bp\\.route$"
    extract:
      partial_path: "args[0]"
      blueprint_var: "decorator_receiver"

  # Step 2: Capture blueprint registration
  - concept: route
    usage:
      kind: call
      name: "^register_blueprint$"
      position: "args[0]"
    compose:
      path: "kwargs.url_prefix + referenced_symbol.partial_path"
```

This requires a two-phase approach: capture partial routes, then compose on registration.

#### 7. Macro Expansion

```elixir
# Phoenix - macro that expands to multiple routes
resources "/users", UserController
```

**Challenge**: One source line expands to 7+ routes. Pre- vs post-expansion tradeoff.

**Potential extension**: Allow patterns to specify expansion:

```yaml
patterns:
  - concept: route
    usage:
      kind: macro
      name: "^resources$"
      expand: true  # Match post-expansion
    extract:
      # Pattern applied to each expanded route
      path: "expanded.path"
      method: "expanded.method"
```

Alternatively, treat `resources` as a single pattern that generates multiple enrichments:

```yaml
patterns:
  - concept: resource_routes
    usage:
      kind: macro
      name: "^resources$"
    generates:
      - { action: "index",   method: "GET",    path_suffix: "" }
      - { action: "show",    method: "GET",    path_suffix: "/:id" }
      - { action: "new",     method: "GET",    path_suffix: "/new" }
      - { action: "create",  method: "POST",   path_suffix: "" }
      - { action: "edit",    method: "GET",    path_suffix: "/:id/edit" }
      - { action: "update",  method: "PUT",    path_suffix: "/:id" }
      - { action: "destroy", method: "DELETE", path_suffix: "/:id" }
```

---

### Significant Challenges (Require Architectural Extension)

These cases require extensions beyond the current model's scope:

#### 8. Configuration Files (Non-Code)

```yaml
# OpenAPI / Swagger
paths:
  /users:
    get:
      operationId: listUsers
      x-handler: myapp.handlers.list_users
```

**Challenge**: Not code—requires different parsers and schemas.

**Potential extension**: Add `config` as a context source:

```yaml
# openapi.yaml (framework pattern file)
patterns:
  - concept: route
    config:
      format: openapi
      file_pattern: "openapi.yaml|swagger.yaml"
      match: "$.paths.*.*"  # JSONPath
    extract:
      path: "parent_key"      # /users
      method: "key"           # get
      handler: "value.operationId | resolve_symbol"
```

This requires:
- Config file parsers (YAML, JSON, XML, TOML)
- Query languages per format (JSONPath, XPath)
- Symbol resolution from strings

**Status**: Feasible but significant scope expansion. Could be a separate phase.

#### 9. Cross-Language / Infrastructure-as-Code

```hcl
# Terraform - AWS API Gateway
resource "aws_api_gateway_integration" "users" {
  http_method = "GET"
  uri = aws_lambda_function.list_users.invoke_arn
}
```

**Challenge**: Route defined in HCL, handler in Python. Different languages, different analysis domains.

**Potential extension**: This is essentially the linker's job—cross-language edge creation. The pattern system could emit partial route info:

```yaml
# terraform.yaml
patterns:
  - concept: api_gateway_route
    config:
      format: hcl
      resource_type: "aws_api_gateway_integration"
    extract:
      path: "resource.path_part"  # Requires traversing resource graph
      method: "resource.http_method"
      handler_ref: "resource.uri"  # ARN to be resolved by linker
```

The linker would then match `handler_ref` (Lambda ARN) to the actual handler symbol.

**Status**: Partially addressable. Config extraction + linker resolution.

#### 10. Schema-First / Generated Code

```protobuf
// user.proto
service UserService {
  rpc ListUsers (Empty) returns (UserList);
}
```

**Challenge**: The "real" route is in the proto, not the generated or implementation code.

**Potential extension**: Treat schema files as first-class sources:

```yaml
# grpc.yaml
patterns:
  - concept: grpc_route
    schema:
      format: protobuf
      match: "service.*.rpc.*"
    extract:
      service: "parent.name"
      method: "name"
      path: "service_name + '/' + method_name"  # gRPC path convention
    links_to:
      implementation: "{service}Servicer.{method}"  # Symbol pattern to find
```

**Status**: The gRPC linker already does some of this. Could be generalized.

---

### Summary: Model Boundaries

| Challenge | Severity | Status |
|-----------|----------|--------|
| Dynamic/runtime registration | Fundamental | Out of scope |
| Runtime metaprogramming | Fundamental | Out of scope |
| Higher-order wrappers | Moderate | Addressable (unwrap directive) |
| Convention over configuration | Moderate | Addressable (naming_convention context) |
| String-based references | Moderate | Addressable (resolution phase) |
| Multi-file composition | Moderate | Addressable (compose directive) |
| Macro expansion | Moderate | Addressable (expand/generates) |
| Configuration files | Significant | Future extension (config context) |
| Cross-language IaC | Significant | Partial (config + linker) |
| Schema-first codegen | Significant | Partial (schema context + linker) |

### Design Principle: Graceful Degradation

When the model encounters patterns it cannot fully analyze, it should:

1. **Extract what it can**: If we can identify the handler but not the path, enrich with `path: null`
2. **Emit warnings**: "Route pattern detected but path could not be statically determined"
3. **Provide hints**: "Consider adding explicit route registration for better analysis"

This allows partial analysis rather than all-or-nothing failure.

## Alignment with ADR-0003 Data Flow

The main ADR defines a data flow pipeline (section 4). Here's how UsageContext fits in:

```
SOURCE FILES
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         ANALYZERS                                │
│                    (Pure language, NO configuration)             │
├─────────────────────────────────────────────────────────────────┤
│  Existing outputs:                                               │
│    - Symbols (functions, classes, methods)                       │
│    - Edges (calls, imports, extends)                             │
│    - Rich metadata (decorators, base_classes, parameters)        │
│                                                                  │
│  NEW output:                                                     │
│    - UsageContexts (call sites, data references, exports)        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
     │
     │  Symbols + edges + rich metadata + usage contexts
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FRAMEWORK_PATTERNS                          │
│                  (Configured by frameworks)                      │
├─────────────────────────────────────────────────────────────────┤
│  Existing: Definition-based matching                             │
│    - Match decorators, base_classes, annotations                 │
│    - Enrich symbols with concept metadata                        │
│                                                                  │
│  NEW: Usage-based matching                                       │
│    - Build reverse index: symbol → [UsageContext]                │
│    - Match usage patterns (call, data_value, export)             │
│    - Enrich symbols based on how they're used                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
  (rest of pipeline unchanged: LINKERS → ENTRY_KINDS → outputs)
```

### Key Points of Alignment

1. **Analyzers remain pure**: UsageContext capture is language-level (call sites, exports), not framework-level. Analyzers don't know what `path()` means—they just record that a symbol was passed to a function called `path`.

2. **FRAMEWORK_PATTERNS remains data-driven**: The new `usage:` pattern syntax is still YAML, not code. Adding Clojure Reitit support means adding `reitit.yaml`, not editing Python.

3. **Rich metadata extended, not replaced**: ADR-0003's "rich metadata" (decorators, base classes, parameters) is definition context. UsageContext adds usage context. Both feed into FRAMEWORK_PATTERNS.

4. **Semantic entry detection preserved**: Entry kinds still derive from enriched symbol metadata, not path heuristics. A function enriched via usage-based route matching is treated identically to one enriched via decorator matching.

## Consequences

### Positive

1. **Unified model**: One abstraction handles calls, data, exports, blocks
2. **Pure analyzers**: Framework logic moves entirely to YAML
3. **Extensibility**: New frameworks added via YAML alone
4. **Completeness**: Covers the major framework paradigms across languages
5. **Handler-centric**: Actual handler symbols get enriched, not synthetic route symbols

### Negative

1. **Analyzer changes**: All analyzers need to emit UsageContext records
2. **IR growth**: More data structures to serialize/store
3. **Pattern complexity**: YAML syntax is richer (usage, extract DSL)
4. **Extraction DSL**: New mini-language to learn and maintain

### Neutral

1. **Two-phase enrichment**: Definition patterns, then usage patterns
2. **Backwards compatible**: Existing decorator patterns continue to work
3. **Incremental adoption**: Can add usage patterns per-framework over time

## Migration Path

### Phase 1: Infrastructure
1. Add `UsageContext` to IR (`ir.py`)
2. Extend `AnalysisResult` with `usage_contexts: list[UsageContext]`
3. Add usage pattern support to `Pattern` class
4. Implement extraction DSL

### Phase 2: Python + Django
1. Update Python analyzer to emit call contexts
2. Add usage patterns to `django.yaml`
3. Remove hardcoded Django detection from `py.py`
4. Verify with existing tests

### Phase 3: JavaScript + Express/Hapi/Next.js
1. Update JS/TS analyzer for call and export contexts
2. Add usage patterns to framework YAML files
3. Remove hardcoded Express detection

### Phase 4: Other Languages
1. Go (Gin, Echo, Chi)
2. Ruby (Rails, Sinatra)
3. Clojure (Reitit)
4. Elixir (Phoenix)

## Open Questions

1. **Inline handlers**: For blocks/lambdas, do we create synthetic symbols or treat the call site as the symbol?

2. **Performance**: Building the reverse index is O(contexts). Acceptable for most repos, but may need optimization for very large codebases.

3. **Macro expansion**: For Elixir/Clojure, should we match pre- or post-expansion? Post-expansion is more uniform but loses source fidelity.

4. **Partial extraction**: If path extraction succeeds but method fails, do we still enrich? Probably yes, with method=None.

## Appendix: Comparison with Initial Proposal

The initial `0003-call-patterns-extension.md` proposed `CallSite` specifically for call-based patterns. This document generalizes:

| Aspect | Initial Proposal | This Proposal |
|--------|------------------|---------------|
| IR Type | `CallSite` | `UsageContext` (general) |
| Scope | Call-based frameworks | All usage-based frameworks |
| Django | ✅ | ✅ |
| Express | ✅ | ✅ |
| Reitit | ❌ | ✅ |
| Next.js | ❌ | ✅ |
| Hapi | Partial | ✅ |
| Sinatra | Partial | ✅ |

The unified model subsumes the initial proposal while extending to cover the full spectrum of framework patterns.
