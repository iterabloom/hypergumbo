# hypergumbo

Get a quick overview of any codebase, sized to fit your context window.

**Requires Python 3.10+**

```bash
pip install hypergumbo              # from PyPI
pip install git+https://codeberg.org/iterabloom/hypergumbo.git  # from source
hypergumbo .
```

**Intel Mac users:** Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](docs/INTEL_MAC.md) for a Docker-based workaround.

Output:
```markdown
# my-project

## Overview
Python (72%), TypeScript (18%), Markdown (10%) · 84 files · ~12,400 LOC

## Structure
- `src/` — Source code
- `tests/` — Tests
- `docs/` — Documentation

## Frameworks
- fastapi
- pytest

## Key Symbols
### `src/api/routes.py`
- `create_user` (function) ★
- `get_user` (function) ★
...
```

Use `-t` to control the token budget:
```bash
hypergumbo . -t 500   # concise overview
hypergumbo . -t 2000  # include symbols and entry points
```

## CLI Commands

```bash
hypergumbo [path]            # default: generate Markdown sketch
hypergumbo . -t 1000         # sketch with 1000 token budget
hypergumbo . -x              # exclude test files (faster on large codebases)
hypergumbo run [path]        # full analysis → hypergumbo.results.json
hypergumbo slice --entry X   # extract subgraph from entry point
hypergumbo slice --entry X --reverse  # find all callers of X
hypergumbo routes [path]     # list HTTP routes (FastAPI, Flask, Express, etc.)
hypergumbo search <query>    # search symbols by name pattern
hypergumbo init [path]       # initialize .hypergumbo/ capsule
hypergumbo init --assistant llm  # use LLM to generate analysis plan
hypergumbo catalog           # list available analysis passes
hypergumbo export-capsule    # export shareable capsule tarball
hypergumbo build-grammars    # build Lean/Wolfram grammars from source
```

## What It Does

**Default mode** (`hypergumbo .`) generates a Markdown sketch with:
- Language breakdown and LOC count
- Directory structure with labels
- Framework detection
- Key symbols ranked by graph centrality (★ = most called)
- Entry points (CLI, HTTP routes, etc.)

**Full analysis** (`hypergumbo run`) outputs a JSON behavior map with:
- **Nodes**: Functions, classes, methods, interfaces with location and stable IDs
- **Edges**: Relationships between symbols (calls, imports, instantiates, extends, implements)
- **Cross-language edges**: 13 linkers connect symbols across language boundaries (see table below)

**LLM-assisted init** (`hypergumbo init --assistant llm`) demonstrates LLM integration
patterns but provides no practical advantage over the default template-based approach.
Since analyzers are language-level (not framework-level), both methods select the same
passes. This feature exists as a technical scaffold showing how to integrate OpenRouter,
OpenAI, or local models via the [llm](https://pypi.org/project/llm/) package. It may be
removed in a future release.

### Supported Languages (54 Analyzers)

| Category | Languages |
|----------|-----------|
| **Application** | Python, JavaScript, TypeScript, Java, C#, F#, Go, Rust, Ruby, PHP, Perl, Swift, Kotlin, Scala, Groovy, Clojure, Erlang, Elixir, Lua, Haskell, OCaml, Julia, R, Dart |
| **Systems** | C, C++, Zig, Objective-C, CUDA, Fortran |
| **Smart Contracts** | Solidity |
| **Hardware** | Verilog, VHDL, GLSL, WGSL |
| **Infrastructure** | Terraform/HCL, Dockerfile, CMake, Make, Nix, Bash, YAML/Ansible |
| **Data/Schema** | SQL, GraphQL, JSON, TOML, XML, CSS |
| **Frontend** | Elm, Vue, Svelte, HTML |
| **Proof/Formal** | Agda, Lean*, Wolfram* |
| **Legacy/Academic** | COBOL, LaTeX |

\* Lean and Wolfram require building tree-sitter grammars from source (not yet on PyPI).
Run `hypergumbo build-grammars` to enable these analyzers.

All analyzers detect symbols and edges (calls, imports, instantiates, extends, implements). See [CHANGELOG.md](CHANGELOG.md) for details.

### Cross-Language Linkers (13 Linkers)

Linkers run automatically during `hypergumbo run` to connect symbols across language boundaries:

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
| Message Queue | Kafka, RabbitMQ, SQS, Redis Pub/Sub topic matching |
| Database Query | SQL in app code → table definitions in schema files |
| Event Sourcing | EventEmitter, Django signals, Spring events |
| Dependency | Manifest dependencies (Cargo.toml, pyproject.toml) → code imports |

## Development

To contribute to hypergumbo:

```bash
git clone https://codeberg.org/iterabloom/hypergumbo.git
cd hypergumbo
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./scripts/install-hooks
hypergumbo build-grammars  # optional: enables Lean and Wolfram analyzers
pytest
```

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files
(`CLAUDE.md`, `GEMINI.md`, etc.) are thin adapters that import the AGENTS.md canonical source.

See [CHANGELOG.md](CHANGELOG.md) for implementation progress.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

[AGPL-3.0-or-later](LICENSE)

![Hypergumbo logo](docs/hypergumbo%20FINAL%20halfres.jpg)


