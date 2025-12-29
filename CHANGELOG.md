# Changelog

All notable changes to hypergumbo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> **Version Note:** This changelog tracks the **tool version** (package releases).
> The **schema version** (output format) is tracked separately in `schema.py` as
> `SCHEMA_VERSION`. Currently: tool v0.5.0, schema v0.1.0. The schema version only
> changes when the JSON output format has breaking changes.

## [Unreleased]

## [0.6.0] - 2025-12-29

### Added
- Lean 4 analyzer (theorem prover support)
- Wolfram Language analyzer (Mathematica support)
- Agda analyzer (dependently typed proof assistant)
- `scripts/build-source-grammars` for building experimental tree-sitter grammars
- `scripts/contribute` for fork-based contributor workflow
- `docs/EXPERIMENTAL_GRAMMARS.md` wishlist of domain-specific languages
- `docs/GOVERNANCE.md` contributor trust model and release policies
- `docs/MAINTAINER_AGENT_SPEC.md` specification for automated PR processing
- Release automation: `scripts/release-check`, `scripts/release`, `scripts/integration-test`, `scripts/bump-version`
- Extended release CI workflow with multi-Python/multi-platform testing
- Contributor Mode documentation in AGENTS.md
- Sketch improvements: two-phase symbol selection, per-file render compression, entrypoint preservation
- Deterministic sketch output (sorted SOURCE_DIRS iteration)
- Conservative token estimation using ceiling division

### Changed
- CI now builds tree-sitter-lean and tree-sitter-wolfram from source (~30s overhead)
- Test files use real parsing instead of mocks where grammars are available

## [0.5.0] - 2025-12-26

Initial public release with comprehensive static analysis capabilities.

### Core Features
- **Sketch generation**: Token-budgeted Markdown overview of any codebase
- **Full analysis**: JSON behavior map with symbols, edges, and provenance
- **Slice extraction**: BFS/DFS subgraph extraction from entry points
- **Route discovery**: HTTP route listing for web frameworks
- **Symbol search**: Pattern-based symbol search

### Language Analyzers (32 languages)

#### Application Languages
- Python (AST-based, no dependencies)
- JavaScript/TypeScript (tree-sitter)
- Java, C#, Go, Rust, Ruby, PHP
- Swift, Kotlin, Scala, Elixir
- Lua, Haskell, OCaml, Julia, R

#### Systems Languages
- C, C++, Zig, Objective-C
- CUDA, Fortran

#### Smart Contracts
- Solidity

#### Hardware Description
- Verilog, VHDL, GLSL, WGSL

#### Infrastructure/Config
- Terraform/HCL, Dockerfile, CMake, Make
- Nix, Bash, YAML/Ansible

#### Data/Schema
- SQL, GraphQL, JSON, TOML, XML, CSS

#### Frontend Frameworks
- Vue, Svelte (script block extraction)

### Cross-Language Linkers (12 linkers)
- **JNI**: Java native methods ↔ C implementations
- **IPC**: Electron IPC, Web Workers, postMessage
- **WebSocket**: Socket.io, native WebSocket, Django Channels, FastAPI WebSocket
- **Phoenix**: Phoenix Channels and LiveView
- **Swift/ObjC**: @objc annotations, #selector, bridging headers
- **gRPC**: Protobuf services, stubs, servicer implementations
- **HTTP**: fetch/axios/requests → route handlers (URL pattern matching)
- **GraphQL**: gql queries/mutations → schema definitions
- **GraphQL Resolver**: Resolver implementations → schema types
- **Message Queue**: Kafka, RabbitMQ, SQS, Redis Pub/Sub
- **Database Query**: SQL in app code → table definitions
- **Event Sourcing**: EventEmitter, Django signals, Spring events

### Route Detection
- Python: FastAPI, Flask, Django, Django REST Framework, Tornado, Aiohttp
- JavaScript: Express, Koa, Fastify, NestJS, Hapi
- Ruby: Rails, Sinatra, Grape
- Go: Gin, Echo, Fiber
- Rust: Axum, Actix-web, Rocket
- Java: Spring Boot, JAX-RS
- PHP: Laravel
- C#: ASP.NET Core
- Elixir: Phoenix

### Framework Detection
- Python: FastAPI, Flask, Django, pytest, PyTorch, TensorFlow, Keras, Transformers, LangChain, LlamaIndex, scikit-learn, MLflow, OpenAI, Anthropic
- JavaScript: React, Vue, Angular, Express, NestJS, Next.js, Nuxt, Svelte
- Rust: Axum, Actix-web, Tokio, Solana/Anchor, Substrate, ethers-rs, Arkworks, Halo2, Plonky2/3, SP1, RISC Zero, Nova, Zcash, libp2p

### Entry Point Detection
- CLI: Python Click/Typer/argparse, Node.js bin scripts
- Web: FastAPI, Flask, Django, Express, NestJS, Rails, Phoenix, Spring Boot, and 25+ more frameworks
- Desktop: Electron main/renderer
- GraphQL: Apollo Server, Yoga, Mercurius

### Supply Chain Classification
- Tier 1: First-party code
- Tier 2: Internal dependencies (workspace packages)
- Tier 3: External dependencies (node_modules, vendor)
- Tier 4: Derived artifacts (minified, generated)

### CLI Commands
- `hypergumbo [path]` - Generate Markdown sketch
- `hypergumbo run [path]` - Full analysis to JSON
- `hypergumbo slice --entry X` - Extract subgraph
- `hypergumbo slice --entry X --reverse` - Find callers
- `hypergumbo routes [path]` - List HTTP routes
- `hypergumbo search <query>` - Search symbols
- `hypergumbo init [path]` - Initialize capsule
- `hypergumbo catalog` - List available passes
- `hypergumbo export-capsule` - Export shareable capsule

### CLI Flags
- `-t N` / `--tokens N` - Token budget for sketch
- `-x` / `--exclude-tests` - Skip test files (17% faster)
- `--first-party-only` - Analyze only first-party code
- `--max-tier N` - Limit by supply chain tier
- `--no-first-party-priority` - Disable tier weighting in symbols

### Output Schema
- `schema_version`: Versioned output format
- `profile`: Languages, frameworks, LOC
- `nodes[]`: Symbols with spans, stable IDs, supply chain info
- `edges[]`: Relationships with confidence scores and evidence
- `analysis_runs[]`: Provenance tracking
- `metrics`: Aggregate statistics
- `limits`: Known gaps and failures

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| 0.5.0 | 2025-12-26 | Initial release: 32 analyzers, 12 linkers, sketch generation |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...HEAD
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
