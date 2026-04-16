<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# hypergumbo

[![CI](https://codeberg.org/iterabloom/hypergumbo/badges/workflows/ci.yml/badge.svg?branch=dev)](https://codeberg.org/iterabloom/hypergumbo/actions)
[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![Coverage](https://img.shields.io/endpoint?url=https://codeberg.org/iterabloom/hypergumbo/raw/branch/badges/coverage.json)](https://codeberg.org/iterabloom/hypergumbo)

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase.

```bash
pip install hypergumbo
```

> Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing.

> Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

```bash
git clone https://codeberg.org/iterabloom/hypergumbo
hypergumbo hypergumbo/
```

Output:

```bash
# hypergumbo

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

## Overview
Python (91%), Markdown (4%), Yaml (3%)
728 files    (383 non-test + 345 test)
~320,798 LOC (~129,172 non-test + ~191,626 test)

## Structure

` ` `
hypergumbo/
├── .agent
│   └── [and 6 other items]
├── .gitea
│   ├── SQUASH_TEMPLATE.md
│   └── [and 1 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 9 other items]
├── docs
│   ├── CACHE.md
│   └── [and 22 other items]
├── packages
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── analyze
│   │   │       │   ├── base.py
│   │   │       │   └── [and 3 other items]
│   │   │       ├── __main__.py
│   │   │       ├── cli.py
│   │   │       ├── ir.py
│   │   │       └── [and 26 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 94 other items]
│   │   └── [and 2 other items]
│   ├── hypergumbo-tracker
│   │   ├── src
│   │   │   └── hypergumbo_tracker
│   │   │       ├── cli.py
│   │   │       └── [and 13 other items]
│   │   └── [and 5 other items]
│   └── [and 4 other items]
├── scripts
│   ├── lib
│   │   └── forgejo-api.sh
│   └── [and 33 other items]
├── tests
│   ├── test_bakeoff_deep_reflect.py
│   └── [and 2 other items]
├── conftest.py
├── pyproject.toml
├── setup.py
└── [and 21 other items]
` ` `

## Frameworks

- pytest
- pytorch
- transformers

## Tests

345 test files · cargo test, pytest, unittest

*~95% estimated coverage (2693/2847 functions called by tests)*

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
hypergumbo io-boundaries       # Find all I/O (filesystem, network, subprocess, env)
hypergumbo verify-claims ...   # Verify security claims against analysis
hypergumbo routes [path]       # List HTTP routes
hypergumbo search <query>      # Search symbols
hypergumbo symbols [path]      # Browse symbols with connectivity
hypergumbo explain <symbol>    # Detailed symbol info
hypergumbo test-coverage       # Analyze test coverage (transitive)
hypergumbo catalog             # List analysis passes
```

Useful flags:
```bash
hypergumbo . -x                # exclude test files (cleaner output)
hypergumbo . --no-source       # omit source code (included by default)
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

- **Language analyzers**: Python, JS/TS, Java, Rust, Go, C/C++, and [many more](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LANGUAGES.md)
- **Linkers**: Tier 2 edge-recovery passes across four subcategories — Protocol (HTTP, WebSocket, message queues, SQL), Bridge (JNI, wasm_bindgen, Tauri IPC, language-pair FFI), Framework (gRPC, GraphQL, React components, DI resolution, ORM), Infrastructure (containment, inheritance, module imports). [Full catalogue](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LINKERS.md).
- **Framework patterns**: FastAPI, Django, Rails, Spring Boot, Phoenix, Express, and [many more](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/FRAMEWORKS.md)
- **I/O boundary detection**: Maps every call chain that reaches the filesystem, network, subprocesses, or environment — across FFI boundaries
- **Taint-flow analysis**: Traces data from sensitive sources (crypto keys, plaintext) to sinks (filesystem, network), with sanitizer awareness
- **Supply chain tiers**: Classifies code as first-party, internal, external, or derived for dependency-aware analysis

## How It Works

1. **Profile**: Scan the repo for languages, file counts, LOC
2. **Analyze**: Run language-specific analyzers to extract symbols and edges
3. **Link**: Connect symbols across language boundaries (JS fetch → Python route)
4. **Enrich**: Detect frameworks via YAML pattern matching
5. **Classify**: Assign supply chain tiers (first-party, internal, external, derived)
6. **Trace I/O**: Map call chains to I/O boundaries; run taint-flow analysis
7. **Output**: Generate Markdown sketch or JSON behavior map

### The Internal Representation

All analyzers produce the same IR types:

- **Symbol**: A code element (function, class, method) with name, location, and stable ID
- **Edge**: A relationship between symbols (calls, imports, extends, implements)
- **Span**: Source location (file, line, column)

This uniform IR is what allows all language analyzers and linkers (Protocol / Bridge / Framework / Infrastructure — see [ADR-0003-ext](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/adr/0003-linker-subcategory-restoration.md)) to work together coherently.

## Architecture

```
packages/
├── hypergumbo-core/           # CLI, IR, slice, sketch, linkers
│   └── src/hypergumbo_core/
│       ├── cli.py             # Entry point
│       ├── ir.py              # Symbol, Edge, Span
│       ├── sketch.py          # Token-budgeted Markdown
│       ├── slice.py           # Subgraph extraction
│       ├── linkers/           # Tier 2 edge-recovery passes (Protocol/Bridge/Framework/Infrastructure)
│       └── frameworks/        # Framework detection (YAML patterns)
├── hypergumbo-lang-mainstream/  # Python, JS, Java, Go, Rust, etc.
├── hypergumbo-lang-common/      # Haskell, Elixir, GraphQL, etc.
├── hypergumbo-lang-extended1/   # Zig, Solidity, Agda, etc.
├── hypergumbo-tracker/           # Structured work tracker for agent governance (MPL-2.0)
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
./scripts/dev-install
source .venv/bin/activate  # reload to enable pytest alias
pytest                      # runs smart-test (affected tests only)
```

`dev-install` installs all packages, git hooks, and the pytest/smart-test wrapper. 100% test coverage required.

See [CONTRIBUTING.md](CONTRIBUTING.md) for PR workflow (including fork-based workflow for external contributors), smart test selection setup, and coverage requirements. Agent instructions live in [AGENTS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/AGENTS.md).

## Links

- [docs/USE-CASES.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/USE-CASES.md) — Practical workflows and examples
- [CHANGELOG.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/CHANGELOG.md) — Implementation history
- [docs/LANGUAGES.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LANGUAGES.md) — Supported languages
- [docs/LINKERS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/LINKERS.md) — Linkers catalogue (Protocol / Bridge / Framework / Infrastructure)
- [docs/FRAMEWORKS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/FRAMEWORKS.md) — Framework patterns
- [docs/hypergumbo-spec.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/hypergumbo-spec.md) — Detailed specification
- [docs/CITATIONS.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/CITATIONS.md) — Paper citations for embedding models
- [docs/CACHE.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/CACHE.md) — Caching architecture
- [SECURITY.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/SECURITY.md) — Vulnerability reporting
- [hypergumbo-tracker README](packages/hypergumbo-tracker/README.md) — Standalone tracker for AI agent governance

## License

[AGPL-3.0-or-later](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/LICENSE)

![Hypergumbo logo](https://codeberg.org/iterabloom/hypergumbo/raw/branch/dev/docs/hypergumbo%20FINAL%20halfres.jpg)

