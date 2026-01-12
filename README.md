# hypergumbo

A local-first CLI that generates behavior maps from source code. Helps developers and LLMs quickly understand any codebase.

**Requires Python 3.10+**

```bash
pip install hypergumbo
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

## Frameworks
- fastapi
- pytest

## Key Symbols
### `src/api/routes.py`
- `create_user` (function) ★
- `get_user` (function) ★
```

Use `-t` to control the token budget:
```bash
hypergumbo . -t 500   # concise
hypergumbo . -t 2000  # include symbols and entry points
```

## How It Works

Hypergumbo builds understanding through a pipeline:

```
┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐    ┌────────┐
│ Profile │ →  │ Analyze  │ →  │  Link   │ →  │ Enrich   │ →  │ Output │
└─────────┘    └──────────┘    └─────────┘    └──────────┘    └────────┘
```

1. **Profile**: Scan the repo for languages, file counts, LOC
2. **Analyze**: Run language-specific analyzers to extract symbols and edges
3. **Link**: Connect symbols across language boundaries (JS fetch → Python route)
4. **Enrich**: Detect frameworks via YAML pattern matching
5. **Output**: Generate Markdown sketch or JSON behavior map

### The Internal Representation

All analyzers produce the same IR types:

- **Symbol**: A code element (function, class, method) with name, location, and stable ID
- **Edge**: A relationship between symbols (calls, imports, extends, implements)
- **Span**: Source location (file, line, column)

This uniform IR is what allows 67 language analyzers and 14 cross-language linkers to work together coherently.

### Two Outputs

**Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected).

**Behavior map** (`hypergumbo run`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## CLI Commands

```bash
hypergumbo [path]              # Markdown sketch (default)
hypergumbo run [path]          # Full JSON behavior map
hypergumbo slice --entry X     # Subgraph from entry point
hypergumbo routes [path]       # List HTTP routes
hypergumbo search <query>      # Search symbols
hypergumbo test-coverage       # Analyze test coverage
hypergumbo catalog             # List analysis passes
```

See `hypergumbo --help` for all options.

## What It Understands

- **67 language analyzers**: Python, JS/TS, Java, Rust, Go, C/C++, and many more ([full list](docs/LANGUAGES.md))
- **14 cross-language linkers**: JNI, HTTP, WebSocket, gRPC, GraphQL, message queues ([full list](docs/LINKERS.md))
- **37 framework patterns**: FastAPI, Django, Rails, Spring Boot, Phoenix, Express, etc.

## Architecture

```
src/hypergumbo/
├── cli.py              # Entry point, argument parsing
├── profile.py          # Repository scanning (languages, LOC)
├── ir.py               # Internal representation (Symbol, Edge, Span)
├── sketch.py           # Markdown generation with token budgeting
├── ranking.py          # Graph centrality for symbol importance
├── analyze/            # 67 language analyzers
├── linkers/            # 14 cross-language linkers
├── frameworks/         # 37 YAML pattern definitions
└── selection/          # Token budget allocation
```

Key design choices:
- **Registry pattern**: Analyzers and linkers self-register via decorators
- **Two-pass analysis**: First collect symbols, then resolve edges (enables cross-file references)
- **Provenance tracking**: Every edge records which analyzer/linker created it
- **YAML-driven patterns**: Framework detection is declarative, not hardcoded

## Development

```bash
git clone https://codeberg.org/iterabloom/hypergumbo.git
cd hypergumbo
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
./scripts/install-hooks
pytest --cov=src --cov-fail-under=100
```

100% test coverage required. All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files (`CLAUDE.md`, `GEMINI.md`, etc.) are thin adapters that import the canonical source.

## Links

- [docs/USE-CASES.md](docs/USE-CASES.md) — Practical workflows and examples
- [CHANGELOG.md](CHANGELOG.md) — Implementation history
- [docs/LANGUAGES.md](docs/LANGUAGES.md) — All 67 supported languages
- [docs/LINKERS.md](docs/LINKERS.md) — All 14 cross-language linkers
- [docs/hypergumbo-spec.md](docs/hypergumbo-spec.md) — Detailed specification
- [SECURITY.md](SECURITY.md) — Vulnerability reporting

## License

[AGPL-3.0-or-later](LICENSE)

![Hypergumbo logo](docs/hypergumbo%20FINAL%20halfres.jpg)
