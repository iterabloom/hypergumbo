# hypergumbo

[![CI](https://codeberg.org/iterabloom/hypergumbo/badges/workflows/ci.yml/badge.svg?branch=dev)](https://codeberg.org/iterabloom/hypergumbo/actions)
[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![Coverage](https://img.shields.io/endpoint?url=https://codeberg.org/iterabloom/hypergumbo/raw/branch/badges/coverage.json)](https://codeberg.org/iterabloom/hypergumbo)

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase.

```bash
pip install hypergumbo
```

> Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

```bash
git clone https://codeberg.org/iterabloom/hypergumbo
hypergumbo hypergumbo/
```

Output:

```bash
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (88%), Markdown (6%), Yaml (4%)
495 files    (321 non-test + 174 test)
~186,506 LOC (~96,384 non-test + ~90,122 test)

## Structure

` ` `
hypergumbo/
├── .agent
│   ├── stop_reflect.md
│   └── [and 3 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 7 other items]
├── docs
│   ├── future
│   │   └── registry-factory-vision.md
│   └── [and 23 other items]
├── packages
│   ├── hypergumbo
│   │   ├── pyproject.toml
│   │   └── [and 2 other items]
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── cli.py
│   │   │       ├── ir.py
│   │   │       └── [and 27 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 63 other items]
│   │   ├── pyproject.toml
│   │   └── [and 1 other items]
│   └── [and 3 other items]
├── scripts
│   ├── hypergumbo_diag.py
│   └── [and 24 other items]
├── .gitignore
├── ALLOWED_WEBSITES.md
├── README.md
├── conftest.py
├── pyproject.toml
└── [and 18 other items]
` ` `

## Frameworks

- pytest
- transformers

## Tests

174 test files · pytest, unittest

*~90% estimated coverage (1960/2179 functions called by tests)*

## Configuration
[...]
```

**[See full example output](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/example-output.md)**

Use `-t` to control the token budget:
```bash
hypergumbo . -t 1000   # brief overview (structure only)
hypergumbo . -t 4000   # good balance for most LLMs
hypergumbo . -t 8000   # detailed with many symbols
```

## Two Outputs

**Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected).

**Behavior map** (`hypergumbo run`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## CLI Commands

```bash
hypergumbo [path]              # Markdown sketch (default)
hypergumbo run [path]          # Full JSON behavior map
hypergumbo slice --entry X     # Subgraph from entry point
hypergumbo routes [path]       # List HTTP routes
hypergumbo search <query>      # Search symbols
hypergumbo symbols [path]      # Browse symbols with connectivity
hypergumbo explain <symbol>    # Detailed symbol info
hypergumbo test-coverage       # Analyze test coverage (transitive)
hypergumbo catalog             # List analysis passes
```

Useful flags:
```bash
hypergumbo . -x                # exclude test files (faster)
hypergumbo . --with-source     # append full source code
hypergumbo . --no-progress     # hide progress indicator (on by default)
hypergumbo --help --all        # comprehensive help for all commands
```

Results are automatically cached in `~/.cache/hypergumbo/`. Just run:
```bash
hypergumbo .    # auto-runs analysis if no cache exists, then generates sketch
```

The cache auto-invalidates when source files change. See [docs/CACHE.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/CACHE.md) for details.

See `hypergumbo --help` for all options.

## What It Understands

- **67 language analyzers**: Python, JS/TS, Java, Rust, Go, C/C++, and many more ([full list](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LANGUAGES.md))
- **15 cross-language linkers**: JNI, HTTP, WebSocket, gRPC, GraphQL, message queues ([full list](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LINKERS.md))
- **37 framework patterns**: FastAPI, Django, Rails, Spring Boot, Phoenix, Express, etc.

## How It Works

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

This uniform IR is what allows 67 language analyzers and 15 cross-language linkers to work together coherently.

## Architecture

```
packages/
├── hypergumbo-core/           # CLI, IR, slice, sketch, linkers
│   └── src/hypergumbo_core/
│       ├── cli.py             # Entry point
│       ├── ir.py              # Symbol, Edge, Span
│       ├── sketch.py          # Token-budgeted Markdown
│       ├── slice.py           # Subgraph extraction
│       ├── linkers/           # 15 cross-language linkers
│       └── frameworks/        # 37 YAML patterns
├── hypergumbo-lang-mainstream/  # Python, JS, Java, Go, Rust, etc.
├── hypergumbo-lang-common/      # Haskell, Elixir, GraphQL, etc.
├── hypergumbo-lang-extended1/   # Zig, Solidity, Agda, etc.
└── hypergumbo/                  # Meta-package (installs all above)
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
source .venv/bin/activate  # reload to enable pytest alias
pytest                      # runs smart-test (affected tests only)
```

After `install-hooks`, `pytest` is aliased to `./scripts/smart-test`, which uses hypergumbo's own call graph to run only tests affected by your changes. Use `pytest --full` or `command pytest` for the complete suite.

### Smart Test Selection

When you run `install-hooks`, you may see:

```
⚠️  No stable hypergumbo found
   smart-test will fall back to running full test suite
```

This means smart-test can't compute affected tests, so it runs everything (~10 min). To enable fast test selection (~30 sec for small changes), install a stable hypergumbo release **outside** your dev venv:

```bash
pipx install hypergumbo          # recommended
# or: python3 -m pip install --user hypergumbo
```

This is a bootstrap safety measure: we use a known-good release to analyze the code under development, avoiding the "testing hypergumbo with itself" paradox. See [ADR-0010](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/adr/0010-modular-packages-and-smart-testing.md) for details.

100% test coverage required. All agent instructions live in [AGENTS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/AGENTS.md). Vendor-specific files (`CLAUDE.md`, `GEMINI.md`, etc.) are thin adapters that import the canonical source.

## Links

- [docs/USE-CASES.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/USE-CASES.md) — Practical workflows and examples
- [CHANGELOG.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/CHANGELOG.md) — Implementation history
- [docs/LANGUAGES.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LANGUAGES.md) — All 67 supported languages
- [docs/LINKERS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LINKERS.md) — All 15 cross-language linkers
- [docs/hypergumbo-spec.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/hypergumbo-spec.md) — Detailed specification
- [docs/CITATIONS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/CITATIONS.md) — Paper citations for embedding models
- [docs/CACHE.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/CACHE.md) — Caching architecture
- [SECURITY.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/SECURITY.md) — Vulnerability reporting

## License

[AGPL-3.0-or-later](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/LICENSE)

![Hypergumbo logo](https://codeberg.org/iterabloom/hypergumbo/raw/branch/dev/docs/hypergumbo%20FINAL%20halfres.jpg)

