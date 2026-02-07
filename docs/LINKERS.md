# Cross-Language Linkers

Hypergumbo includes 19 linkers that connect symbols across language boundaries. Linkers run automatically during `hypergumbo run` after all language analyzers complete.

## Linker Table

| Linker | Description |
|--------|-------------|
| JNI | Java `native` methods ↔ C JNI implementations |
| IPC | Electron IPC, Web Workers, `postMessage` patterns |
| WebSocket | Socket.io, native WebSocket, Django Channels, FastAPI WebSocket |
| Phoenix | Phoenix Channels (`broadcast!`, `push`, `handle_in`) and LiveView |
| Swift/ObjC | `@objc` annotations, `#selector()`, bridging headers |
| gRPC | Protobuf services, stubs, and servicer implementations |
| HTTP | `fetch()`, `axios`, `requests` → route handlers (URL pattern matching) |
| GraphQL | `gql` queries/mutations → schema definitions |
| GraphQL Resolver | Resolver implementations → schema type definitions |
| OpenAPI | OpenAPI/Swagger specs → route handlers (path/operationId matching) |
| Message Queue | Kafka, RabbitMQ, SQS, Redis Pub/Sub topic matching |
| Database Query | SQL in app code → table definitions in schema files |
| Event Sourcing | EventEmitter, Django signals, Spring events |
| Dependency | Manifest dependencies (Cargo.toml, pyproject.toml) → code imports |
| Subprocess | `subprocess.run()` → CLI command handlers (Click, Typer, argparse) |
| Containment | Class/interface symbols → method/getter/setter symbols (`contains` edges) |
| Inheritance | `base_classes` metadata → `extends`/`implements` edges across all languages |
| Route Handler | Route symbols → handler functions (Rails, Phoenix, Laravel, Express, Django) |
| Type Hierarchy | Interface/abstract methods → concrete implementations (`dispatches_to` edges) |

## How Linkers Work

Linkers operate on the combined output of all language analyzers:

1. **Receive**: All symbols and edges from all analyzers
2. **Match**: Find patterns that indicate cross-language communication
3. **Create edges**: Add new edges connecting symbols across language boundaries
4. **Tag provenance**: Each edge records which linker created it

For example, the HTTP linker:
- Scans for `fetch("/api/users")` calls in JavaScript
- Scans for `@app.get("/api/users")` route handlers in Python
- Creates an edge: `js_fetch_call → python_route_handler`

## Why Linkers Matter

Modern applications rarely stay within one language. A typical web app might have:
- TypeScript frontend calling Python API endpoints (HTTP linker)
- Python backend publishing to Redis (Message Queue linker)
- Java service with C performance-critical code (JNI linker)

Without linkers, you'd see isolated subgraphs per language. Linkers reveal the actual data flow across the system.

## Adding a New Linker

See `packages/hypergumbo-core/src/hypergumbo_core/linkers/` for examples. Linkers register via decorator:

```python
from hypergumbo_core.linkers.registry import register_linker, LinkerContext, LinkerResult

@register_linker("myprotocol", priority=50)
def link_myprotocol(ctx: LinkerContext) -> LinkerResult:
    # Find cross-language patterns, return new edges
    ...
```

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
