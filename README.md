# hypergumbo

Get a quick overview of any codebase, sized to fit your context window.

```bash
pip install git+https://codeberg.org/iterabloom/hypergumbo-experimental.git
hypergumbo .
```

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

## Installation

```bash
pip install git+https://codeberg.org/iterabloom/hypergumbo-experimental.git
```

All language analyzers (Python, JavaScript, TypeScript, PHP, C, Java, Elixir, Rust, Go, Ruby, Kotlin, Swift, Scala) are included by default.

For LLM-assisted plan generation:
```bash
pip install "hypergumbo[llm-assist] @ git+https://codeberg.org/iterabloom/hypergumbo-experimental.git"  # OpenRouter/OpenAI
pip install "hypergumbo[llm-local] @ git+https://codeberg.org/iterabloom/hypergumbo-experimental.git"   # local models via llm
```

## CLI Commands

```bash
hypergumbo [path]            # default: generate Markdown sketch
hypergumbo . -t 1000         # sketch with 1000 token budget
hypergumbo . -x              # exclude test files (faster on large codebases)
hypergumbo run [path]        # full analysis → hypergumbo.results.json
hypergumbo slice --entry X   # extract subgraph from entry point
hypergumbo slice --entry X --reverse  # find all callers of X
hypergumbo init [path]       # initialize .hypergumbo/ capsule
hypergumbo init --assistant llm  # use LLM to generate analysis plan
hypergumbo catalog           # list available analysis passes
hypergumbo export-capsule    # export shareable capsule tarball
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
- **Cross-language edges**: JNI bridges (Java↔C), IPC channels (Electron, Web Workers)

**LLM-assisted init** (`hypergumbo init --assistant llm`) uses an LLM to generate
a customized analysis plan based on your repo's profile. Supports:
- **OpenRouter** (free tier): Set `OPENROUTER_API_KEY` env var
- **OpenAI**: Set `OPENAI_API_KEY` env var
- **Local models**: Install `hypergumbo[llm-local]` for the [llm](https://pypi.org/project/llm/) package

Falls back to template-based generation if LLM is unavailable or fails.

> **Note:** LLM-assisted plan generation is currently proof-of-concept infrastructure.
> With 9 passes and 3 packs in the catalog, template-based generation produces
> equivalent results. This feature will become practical as the catalog expands with
> framework-specific packs and configuration options.

### Supported Languages

| Language | Parser | Symbols | Edges |
|----------|--------|---------|-------|
| Python | AST | function, class, method | calls, imports, instantiates |
| JavaScript | tree-sitter | function, class, method | calls, imports, instantiates |
| TypeScript | tree-sitter | function, class, method, interface, type, enum | calls, imports, instantiates |
| Vue | tree-sitter | function, class, method | calls, imports, instantiates |
| Svelte | tree-sitter | function, class, method | calls, imports, instantiates |
| PHP | tree-sitter | function, class, method | calls, instantiates |
| C | tree-sitter | function, struct, enum, typedef | calls |
| Java | tree-sitter | class, interface, enum, method, constructor | calls, extends, implements, instantiates |
| Elixir | tree-sitter | module, function, macro | calls, imports |
| Rust | tree-sitter | function, struct, enum, trait, method | calls, imports |
| Go | tree-sitter | function, method, struct, interface, type | calls, imports |
| Ruby | tree-sitter | method, class, module | calls, imports |
| Kotlin | tree-sitter | function, class, object, interface, method | calls, imports |
| Swift | tree-sitter | function, class, struct, protocol, enum, method | calls, imports |
| Scala | tree-sitter | function, class, object, trait, method | calls, imports |
| HTML | regex | file | script_src |

### Cross-Language Linkers

Linkers run automatically during `hypergumbo run` to connect symbols across language boundaries:

| Linker | Edge Type | Description |
|--------|-----------|-------------|
| JNI | native_bridge | Links Java `native` methods to C JNI implementations (`Java_Package_Class_Method`) |
| IPC | message_send, message_receive | Detects Electron IPC (`ipcRenderer`/`ipcMain`), Web Workers, `postMessage` patterns |
| WebSocket | websocket_message, websocket_connection | Detects Socket.io, native WebSocket, and ws package patterns. Event matching links senders to receivers. |

## Development

To contribute to hypergumbo:

```bash
git clone https://codeberg.org/iterabloom/hypergumbo-experimental.git
cd hypergumbo-experimental
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./scripts/install-hooks
pytest
```

> **Note:** A few warnings like "C analysis skipped: requires tree-sitter-c" are expected—
> they come from fallback tests that mock dependencies as unavailable. If you see many
> such warnings or actual test failures, double-check that `pip install -e .[dev]` succeeded.

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files
(`CLAUDE.md`, `GEMINI.md`, etc.) are thin adapters that import the AGENTS.md canonical source.

See [STATUS.md](STATUS.md) for implementation progress.
