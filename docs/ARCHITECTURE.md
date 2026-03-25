<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Sections marked with (auto) are computed at generation time; unmarked sections are curated.
> Run `./scripts/generate-architecture` to update.

hypergumbo is a local-first CLI that analyzes source code and emits
behavior maps — JSON graphs of symbols, call edges, framework patterns,
and cross-language links. It supports three output modes: a Markdown sketch
for human reading, a full JSON behavior map for tooling, and graph slices
for focused LLM context.

## Self-Analysis Summary (auto)

hypergumbo analyzed its own source code and found:
- **205** Python modules (117 analyzers, 43 linkers, 27 core, 4 CLI, 14 tracker)
- **3652** symbols (functions, classes, methods)
- **38013** edges by type:
  - calls: 22925
  - imports: 7550
  - instantiates: 5316
  - contains: 1008
  - dispatches_to: 514
  - decorated_by: 266
  - other: 434

## Package Architecture

The codebase is a Python monorepo with six packages arranged in a strict
dependency hierarchy. The separation enforces layering: language analyzers
depend on core but not on each other, and the tracker is fully independent.

```
                      hypergumbo (meta-package)
                    /       |       \
                   v        v        v
  lang-mainstream   lang-common   lang-extended1
  (36 analyzers)   (38 analyzers)   (40 analyzers)
                   \       |       /
                    v      v      v
                   hypergumbo-core
           (IR, CLI, linkers, patterns,
            ranking, discovery, sketch)

  hypergumbo-tracker  (independent)
  (governance TUI, op-log store,
   CRDT-like merge, SQLite cache)
```

| Package | Role |
|---------|------|
| **hypergumbo-core** | IR types (`Symbol`, `Edge`, `Span`), CLI, analysis base classes, 43 linkers, 99 YAML pattern files, sketch/slice output, supply chain classification, symbol resolution, ranking |
| **hypergumbo-lang-mainstream** | 36 tree-sitter analyzers for widely-used languages (Python, JS/TS, Java, Go, Rust, C/C++, Ruby, PHP, C#, Kotlin, Swift, Scala, etc.) |
| **hypergumbo-lang-common** | 38 analyzers for domain-specific and functional languages (Haskell, Elixir, OCaml, Dart, Julia, CUDA, GraphQL, HCL, etc.) |
| **hypergumbo-lang-extended1** | 40 analyzers for specialized languages (Zig, Odin, Solidity, Verilog, VHDL, Agda, Lean, Wolfram, etc.) |
| **hypergumbo** | Meta-package that installs core + all language packages |
| **hypergumbo-tracker** | Standalone governance tool with TUI, YAML-backed op-log store, Lamport-clock ordering, and optional embedding-based dedup |

## Analysis Pipeline

The CLI's `run_behavior_map()` function orchestrates the full pipeline:

```
Source Files
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        1. DISCOVERY                             │
│  Find files, classify into supply chain tiers (1-4),            │
│  apply .gitignore + exclude patterns                            │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         2. PROFILE                              │
│  Detect languages (by file extension)                           │
│  Detect frameworks (by manifest markers, scoped to languages)   │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        3. ANALYZERS                             │
│  Per-language tree-sitter parsing (two-pass architecture):      │
│    Pass 1: Extract symbols from AST nodes                       │
│    Pass 2: Resolve calls/imports against global symbol registry │
│  Output: 3652 Symbols + 38013 Edges + UsageContexts             │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     4. PATTERN ENRICHMENT                       │
│  YAML-driven pattern matching (ADR-0003):                       │
│  - 7 convention patterns (always loaded)                        │
│  - 92 framework patterns (loaded when framework detected)       │
│  Output: Symbols enriched with meta.concepts                    │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        5. LINKERS                               │
│  Cross-language/cross-file edge creation                        │
│  Match via meta.concepts (route paths, gRPC services, etc.)     │
│  43 linkers: HTTP, gRPC, GraphQL, WebSocket, IPC, JNI, etc.     │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     6. RANKING & OUTPUT                         │
│  Centrality scoring, supply chain tier filtering,               │
│  token-budget-aware truncation (compact mode)                   │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │  sketch  │   │   run    │   │  slice   │
    │ Markdown │   │   JSON   │   │ subgraph │
    └──────────┘   └──────────┘   └──────────┘
```

## Output Modes

| Mode | Format | Purpose |
|------|--------|---------|
| **`sketch`** | Markdown | Token-budgeted overview of repo structure and key symbols. Designed for human reading and LLM context windows. Default output mode. |
| **`run`** | JSON | Full behavior map with all symbols, edges, and metadata. Used by downstream tools, CI checks, and the tracker. |
| **`slice`** | JSON | Subgraph extraction from an entry point (forward or reverse). Produces focused dependency/dependents view for a specific symbol. |

## Supply Chain Tiers

Files are classified into tiers at discovery time (before analysis)
to prioritize first-party code and filter noise:

| Tier | Name | Examples |
|------|------|----------|
| **1** | First-party | Project source code (`src/`, `lib/`, `app/`) |
| **2** | Internal dep | Monorepo packages, examples, tests |
| **3** | External dep | `node_modules/`, `vendor/`, vendored SDKs |
| **4** | Derived | Build artifacts, transpiled output (`dist/`, `build/`, `*.min.js`) |

Classification uses first-match-wins: derived artifacts (tier 4) are checked first,
then external deps (tier 3), then internal (tier 2), and remaining files default to
first-party (tier 1). The `--first-party-only` flag restricts analysis to tier 1.

## Tree-sitter Infrastructure

All language analyzers inherit from `TreeSitterAnalyzer` (`analyze/base.py`),
which provides a universal two-pass architecture:

1. **Pass 1 (symbol extraction):** Parse each file with tree-sitter, walk the AST,
   extract symbols (functions, classes, methods, etc.) with metadata
2. **Pass 2 (edge resolution):** Re-walk ASTs, resolve calls and imports against
   the global symbol registry built in pass 1

Grammars come from two sources:
- **PyPI packages** (most languages): installed via `pip` as `tree-sitter-<lang>` dependencies
- **Build-from-source** (Lean, Wolfram, etc.): compiled by `scripts/build-source-grammars`
  when the PyPI grammar is unavailable or incompatible

## Symbol Resolution

Linkers match symbols across files and languages using `NameResolver`
(`symbol_resolution.py`), which provides tiered matching with confidence levels:

| Strategy | Confidence | When used |
|----------|-----------|-----------|
| Exact | 1.0 | Direct key match in symbol registry |
| Path hint | 0.90 | Disambiguate using import path (Go-style) |
| Suffix | 0.85 | Match `MyClass.doWork` for lookup `doWork` |
| Ambiguous | 0.70 | Multiple candidates, picked heuristically |

The suffix index is built lazily on first fuzzy lookup, not upfront.
Confidence scores propagate to edges, enabling downstream consumers to
filter by reliability.

## Key Abstractions

### Span (`ir.py`)
Source code location with line and column info.

### AnalysisRun (`ir.py`)
Provenance tracking for an analysis pass execution.

### Symbol (`ir.py`)
A code symbol (function, class, etc.) detected by analysis.
- `id`: Location-based identifier in format {lang}:{file}:{start}-{end}:{name}:{kind}
- `name`: The symbol's name (e.g., function name, class name)
- `kind`: Type of symbol (function, class, etc.)
- `language`: Programming language (python, javascript, etc.)
- `path`: File path where the symbol is defined
- `span`: Source location with lines and columns
- `origin`: Which analysis pass created this symbol
- `origin_run_id`: Unique execution ID of the analysis run
- `origin_run_signature`: Run signature for grouping by analyzer config
- `stable_id`: Semantic identity hash (survives renames/moves)
- `shape_id`: Structural implementation fingerprint
- `canonical_name`: Fully qualified name (e.g., 'mymodule.MyClass.method')
- `fingerprint`: Content hash of source bytes (sha256)
- `quality`: Score and reason dict for quality assessment
- `meta`: Optional metadata dict for language-specific information
- `supply_chain_tier`: Position in dependency graph (1=first_party, 2=internal_dep,

### Edge (`ir.py`)
A relationship between two symbols (e.g., function calls).
- `id`: Unique identifier for this edge instance
- `edge_key`: Canonical identity for deduplication across passes
- `src`: ID of the source symbol (e.g., the caller)
- `dst`: ID of the target symbol (e.g., the callee)
- `edge_type`: Type of relationship (calls, imports, inherits, etc.)
- `line`: Line number where the relationship occurs
- `confidence`: Confidence score (0.0-1.0)
- `origin`: Which analysis pass created this edge
- `origin_run_id`: Unique execution ID of the analysis run
- `origin_run_signature`: Run signature for grouping
- `evidence_type`: Type of evidence (e.g., ast_call_direct)
- `evidence_lang`: Language for confidence scoring
- `evidence_spans`: Structured locations of evidence
- `quality`: Score and reason dict for quality assessment
- `meta`: Optional metadata dict. Dataflow edges (ADR-0015) store access_mode, dest_access_mode, and channel here.


## Most-Connected Symbols (auto)

These symbols have the highest bidirectional centrality
(`score = in_degree * (1 + ln(1 + out_degree))`):

| Symbol | Kind | Score | Location |
|--------|------|-------|----------|
| `Symbol` | class | 4047.2 | ir.py |
| `Span` | class | 3483.7 | ir.py |
| `run_behavior_map` | function | 2264.0 | cli.py |
| `TrackerApp` | class | 1647.2 | tui.py |
| `LinkerContext` | class | 1140.1 | registry.py |
| `load_framework_patterns` | function | 1137.4 | framework_patterns.py |
| `main` | function | 1032.8 | cli.py |
| `TreeSitterAnalyzer` | class | 851.1 | base.py |
| `find_files` | function | 782.8 | discovery.py |
| `Store` | class | 760.4 | store.py |
| `clear_pattern_cache` | function | 620.0 | framework_patterns.py |
| `UsageContext.create` | method | 574.6 | ir.py |
| `Edge` | class | 565.6 | ir.py |
| `Store.add` | method | 535.4 | store.py |
| `node_text` | function | 522.0 | base.py |

## Pattern System

### Key Components

- **`framework_patterns.py`**: Loads and applies YAML pattern files
- **`frameworks/*.yaml`**: 99 pattern files (7 convention + 92 framework)
- **`meta.concepts`**: List of matched concepts (single source of truth)
- **`meta.decorators`/`meta.annotations`**: Raw metadata for pattern matching

### meta.concepts Structure

Enriched symbols have a `meta.concepts` list:

```json
{
  "meta": {
    "concepts": [
      {"concept": "route", "path": "/users", "method": "GET", "framework": "fastapi"},
      {"concept": "test_function", "framework": "test-frameworks"}
    ]
  }
}
```

Linkers and entrypoint detection use `meta.concepts` exclusively.

### Adding a New Pattern

**Framework pattern** (loaded when framework detected):
1. Create `packages/hypergumbo-core/src/hypergumbo_core/frameworks/<framework>.yaml`
2. Add `linkers:` section to enable relevant linkers
3. Add tests in `packages/hypergumbo-core/tests/test_framework_patterns.py`

**Convention pattern** (always loaded):
1. Edit existing convention file or create new one
2. Use `language: multi` for cross-language patterns
3. Add to `enrich_symbols()` load list in `framework_patterns.py`

Example patterns:

**Definition-based (decorators/annotations):**
```yaml
id: myframework
language: python

patterns:
  - concept: route
    decorator: "^myapp\\.(get|post|put|delete)$"
    extract_path: "args[0]"
    extract_method: "decorator_suffix"
```

**Convention-based (symbol_name + symbol_kind + language):**
```yaml
id: test-frameworks
language: multi

patterns:
  - concept: test_function
    symbol_name: "^test_"
    symbol_kind: "^function$"
    language: "^python$"
```

## Tracker Subsystem

The `hypergumbo-tracker` package is an independent governance tool for
managing work items, invariants, and project state. Key design choices:

- **Op-log store:** Items are append-only YAML op logs, not mutable records.
  `compile()` folds the log into current state (LWW for scalars, accumulation for sets).
- **Lamport clock:** Cross-branch causal ordering via git object timestamps.
- **Multi-tier views:** `TrackerSet` provides a unified view over canonical,
  workspace, and stealth stores.
- **SQLite cache:** Read-hot path uses a SQLite cache (`Cache`) that rebuilds
  from the op-log on invalidation.
- **Proquint IDs:** Content-hash IDs (SHA-256 + proquint encoding) for
  natural deduplication — same logical item always gets the same ID.
- **TUI:** Textual-based terminal UI (`TrackerApp`) for interactive browsing,
  filtering, and discussion.

## Scripts & Tooling (auto)

The `scripts/` directory contains operational tooling. Descriptions are extracted from each script's header comment or docstring.

### CI & Release

| Script | Description |
|--------|-------------|
| `auto-pr` | Push branch, poll CI, merge PR (with vPR queue for offline resilience) |
| `ci-debug` | CI Debug Helper - Fetch and analyze Forgejo/Gitea Actions run logs |
| `ci-failover` | Switch CI targeting between Codeberg and self-hosted Forgejo |
| `merge-pr` | Focused recovery script for merging existing PRs |
| `prepare-release` | Prepare a release for human approval. |
| `release` | Release script - creates a new release. |
| `release-check` | Pre-release validation script for multi-package hypergumbo. |
| `tag-release` | Create and push a signed release tag. |

### Testing & Quality

| Script | Description |
|--------|-------------|
| `check-package-coverage` | Verify each package achieves 100% coverage in isolation |
| `integration-test` | Integration tests against real-world repositories. |
| `license-headers` | Check and repair SPDX license headers on source files in the repository. |
| `smart-test` | Run only tests affected by changed files |
| `validate-agents.sh` | (no description) |

### Development

| Script | Description |
|--------|-------------|
| `build-source-grammars` | Build tree-sitter grammars that are not available on PyPI. |
| `bump-version` | Version bump script for multi-package hypergumbo. |
| `dev-install` | Dev install for hypergumbo monorepo. |
| `install-embeddings` | Install embedding dependencies for hypergumbo. |
| `install-hooks` | (no description) |
| `loop-toggle` | Toggle autonomous mode on/off with support for multiple modes |
| `stop-hook-preview` | stop-hook-preview — Dry-run stop hook simulator. |
| `tracker` | Thin wrapper for hypergumbo-tracker CLI. |
| `tracker-textconv` | Git textconv driver for hypergumbo-tracker .ops files. |
| `uninstall-embeddings` | Uninstall embedding dependencies from hypergumbo. |

### Analysis & Bakeoff

| Script | Description |
|--------|-------------|
| `analyze-artifacts` | Analyze hypergumbo bakeoff artifacts to extract insights. |
| `bakeoff-broad` | bakeoff-broad - Automated hypergumbo bakeoff runner and analyzer |
| `bakeoff-deep` | bakeoff-deep - Feature-focused hypergumbo bakeoff runner |
| `bakeoff-deep-reflect` | bakeoff-deep-reflect - LLM-driven qualitative assessment of hypergumbo outputs |
| `bakeoff-broad-reflect` | bakeoff-broad-reflect - LLM-driven parse correctness assessment for BROAD bakeoff |
| `changelog-date-bucket` | Rewrite CHANGELOG.md's "## [Unreleased]" into date-grouped subheadings. |
| `compute_probe_embeddings.py` | Compute probe embeddings for sketch_embeddings.py. |
| `generate-architecture` | Generate ARCHITECTURE.md using hypergumbo to analyze itself. |
| `generate-schema` | Generate JSON Schema from hypergumbo's Python dataclasses. |
| `hypergumbo_diag.py` | hypergumbo_diag.py - Unified diagnostic script for hypergumbo bakeoff analysis |
| `test-leaderboard` | Test timing leaderboard — tracks per-test durations across runs. |

### Contribution

| Script | Description |
|--------|-------------|
| `contribute` | Contributor workflow script for fork-based development. |
| `list-my-prs` | List open PRs for this repository |

## Adding a New Analyzer

1. Create analyzer in the appropriate lang package:
   - Mainstream: `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/<language>.py`
   - Common: `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/<language>.py`
   - Extended1: `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/<language>.py`
2. Implement `analyze(root: Path) -> AnalysisResult`
3. Return symbols and edges following IR conventions
4. Add tests in the package's `tests/` directory
5. Register in the package's `__init__.py` ANALYZER_SPECS

## Adding a New Linker

1. Create `packages/hypergumbo-core/src/hypergumbo_core/linkers/<name>.py`
2. Register using the decorator pattern:

```python
from .registry import register_linker, LinkerContext, LinkerResult

@register_linker("ipc", priority=50)
def link_ipc(ctx: LinkerContext) -> LinkerResult:
repo_root = ctx.repo_root
# ... do linking ...
return LinkerResult(symbols=symbols, edges=edges, run=run)
```

3. Add tests in `packages/hypergumbo-core/tests/test_<name>_linker.py`

## Module Reference (auto)

### Core

- **`hypergumbo_core.build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`hypergumbo_core.catalog`**: Catalog of available analysis passes.
- **`hypergumbo_core.compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`hypergumbo_core.dataflow`**: YAML-driven dataflow classification for edges (ADR-0015).
- **`hypergumbo_core.datamodels`**: Data model detection for code analysis.
- **`hypergumbo_core.discovery`**: File discovery with exclude patterns, locale handling, and extensio...
- **`hypergumbo_core.entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`hypergumbo_core.framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-0003).
- **`hypergumbo_core.gitleaks`**: Gitleaks integration for secret scanning.
- **`hypergumbo_core.io_boundary`**: I/O boundary analysis — catalog loading and edge matching (ADR-0016).
- **`hypergumbo_core.ir`**: Internal Representation (IR) for code analysis.
- **`hypergumbo_core.limits`**: Limits tracking for behavior map output.
- **`hypergumbo_core.metrics`**: Metrics computation for behavior map output.
- **`hypergumbo_core.partial_install_warnings`**: Runtime warnings for partial installations (ADR-0010 Item 8).
- **`hypergumbo_core.paths`**: Centralized path handling utilities for hypergumbo.
- **`hypergumbo_core.profile`**: Repo profile detection - language and framework heuristics.
- **`hypergumbo_core.ranking`**: Symbol and file ranking utilities for hypergumbo output.
- **`hypergumbo_core.selection.filters`**: Path classification and symbol kind filtering for selection.
- **`hypergumbo_core.selection.language_proportional`**: Language-proportional symbol selection utilities.
- **`hypergumbo_core.selection.token_budget`**: Token estimation and budget management for LLM-aware output.
- **`hypergumbo_core.sketch_embeddings`**: Embedding-based utilities for sketch generation.
- **`hypergumbo_core.slice`**: Graph slicing for LLM context extraction.
- **`hypergumbo_core.supply_chain`**: Supply chain classification for code analysis.
- **`hypergumbo_core.symbol_resolution`**: Unified symbol resolution with pluggable matching strategies.
- **`hypergumbo_core.taxonomy`**: File taxonomy classification (ADR-0004).
- **`hypergumbo_core.test_masking`**: Slow test masking for smart-test.
- **`hypergumbo_core.verify_claims`**: Security claim verification against I/O boundary maps (ADR-0016 Pha...

### Analyzers

- **`hypergumbo_core.analyze.all_analyzers`**: Facade for analyzer dispatch — delegates to the decorator-based reg...
- **`hypergumbo_core.analyze.base`**: Base classes and utilities for language analyzers.
- **`hypergumbo_core.analyze.registry`**: Analyzer registry for decorator-based dynamic dispatch.
- **`hypergumbo_lang_mainstream.bash`**: Bash/shell script analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.c`**: C analysis pass using tree-sitter-c.
- **`hypergumbo_lang_mainstream.cmake`**: CMake analysis pass using tree-sitter-cmake.
- **`hypergumbo_lang_mainstream.cpp`**: C++ analysis pass using tree-sitter-cpp.
- **`hypergumbo_lang_mainstream.csharp`**: C# analysis pass using tree-sitter-c-sharp.
- **`hypergumbo_lang_mainstream.css`**: CSS stylesheet analysis using tree-sitter-css.
- **`hypergumbo_lang_mainstream.dockerfile`**: Dockerfile analysis pass using tree-sitter-dockerfile.
- **`hypergumbo_lang_mainstream.gitignore`**: Gitignore file analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.go`**: Go analysis pass using tree-sitter-go.
- **`hypergumbo_lang_mainstream.groovy`**: Groovy analysis pass using tree-sitter-groovy.
- **`hypergumbo_lang_mainstream.html`**: HTML script tag analysis pass.
- **`hypergumbo_lang_mainstream.ini`**: INI configuration file analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.java`**: Java analysis pass using tree-sitter-java.
- **`hypergumbo_lang_mainstream.js_ts`**: JavaScript/TypeScript/Svelte analysis pass using tree-sitter.
- **`hypergumbo_lang_mainstream.json_config`**: JSON configuration analysis pass using tree-sitter-json.
- **`hypergumbo_lang_mainstream.jupyter`**: Jupyter notebook (.ipynb) analyzer.
- **`hypergumbo_lang_mainstream.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`hypergumbo_lang_mainstream.lua`**: Lua analysis pass using tree-sitter-lua.
- **`hypergumbo_lang_mainstream.make`**: Makefile analysis pass using tree-sitter-make.
- **`hypergumbo_lang_mainstream.manifest_targets`**: Build-target extraction from language-specific manifest files.
- **`hypergumbo_lang_mainstream.markdown`**: Markdown documentation analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.objc`**: Objective-C analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.perl`**: Perl analysis pass using tree-sitter.
- **`hypergumbo_lang_mainstream.php`**: PHP analysis pass using tree-sitter-php.
- **`hypergumbo_lang_mainstream.powershell`**: PowerShell analysis pass using tree-sitter.
- **`hypergumbo_lang_mainstream.properties`**: Java properties file analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.py`**: Python AST analysis pass.
- **`hypergumbo_lang_mainstream.requirements`**: Python requirements.txt analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.ruby`**: Ruby analysis pass using tree-sitter-ruby.
- **`hypergumbo_lang_mainstream.rust`**: Rust analysis pass using tree-sitter-rust.
- **`hypergumbo_lang_mainstream.scala`**: Scala analysis pass using tree-sitter-scala.
- **`hypergumbo_lang_mainstream.sql`**: SQL schema analysis pass using tree-sitter-sql.
- **`hypergumbo_lang_mainstream.swift`**: Swift analysis pass using tree-sitter-swift.
- **`hypergumbo_lang_mainstream.toml_config`**: TOML configuration file analyzer using tree-sitter-toml.
- **`hypergumbo_lang_mainstream.xml_config`**: XML configuration analysis pass using tree-sitter-xml.
- **`hypergumbo_lang_mainstream.yaml_ansible`**: YAML/Ansible analyzer using tree-sitter.
- **`hypergumbo_lang_common.astro`**: Astro component analyzer using tree-sitter.
- **`hypergumbo_lang_common.clojure`**: Clojure analysis pass using tree-sitter.
- **`hypergumbo_lang_common.commonlisp`**: Common Lisp analysis pass using tree-sitter.
- **`hypergumbo_lang_common.cuda`**: CUDA analysis pass using tree-sitter-cuda.
- **`hypergumbo_lang_common.dart`**: Dart/Flutter analysis pass using tree-sitter.
- **`hypergumbo_lang_common.elixir`**: Elixir analysis pass using tree-sitter-elixir.
- **`hypergumbo_lang_common.elm`**: Elm analysis pass using tree-sitter.
- **`hypergumbo_lang_common.erlang`**: Erlang analysis pass using tree-sitter.
- **`hypergumbo_lang_common.fortran`**: Fortran analysis pass using tree-sitter-fortran.
- **`hypergumbo_lang_common.fsharp`**: F# analysis pass using tree-sitter.
- **`hypergumbo_lang_common.glsl`**: GLSL shader analysis pass using tree-sitter-glsl.
- **`hypergumbo_lang_common.graphql`**: GraphQL schema analysis pass using tree-sitter-graphql.
- **`hypergumbo_lang_common.handlebars`**: Handlebars template analyzer using regex patterns.
- **`hypergumbo_lang_common.haskell`**: Haskell analysis pass using tree-sitter-haskell.
- **`hypergumbo_lang_common.hcl`**: HCL/Terraform analyzer using tree-sitter.
- **`hypergumbo_lang_common.hlsl`**: HLSL (DirectX shader) analysis pass using tree-sitter.
- **`hypergumbo_lang_common.julia`**: Julia analysis pass using tree-sitter-julia.
- **`hypergumbo_lang_common.just`**: Just (justfile) analyzer using regex patterns.
- **`hypergumbo_lang_common.latex`**: LaTeX analyzer using tree-sitter.
- **`hypergumbo_lang_common.matlab`**: MATLAB language analyzer using tree-sitter.
- **`hypergumbo_lang_common.meson`**: Meson build system analyzer using tree-sitter.
- **`hypergumbo_lang_common.nix`**: Nix expression analysis pass using tree-sitter-nix.
- **`hypergumbo_lang_common.ocaml`**: OCaml analysis pass using tree-sitter-ocaml.
- **`hypergumbo_lang_common.proto`**: Protocol Buffers (Proto) analysis pass using tree-sitter.
- **`hypergumbo_lang_common.puppet`**: Puppet manifest analyzer using tree-sitter.
- **`hypergumbo_lang_common.purescript`**: PureScript language analyzer using tree-sitter.
- **`hypergumbo_lang_common.qml`**: QML (Qt Modeling Language) analyzer using regex patterns.
- **`hypergumbo_lang_common.r_lang`**: R language analysis pass using tree-sitter.
- **`hypergumbo_lang_common.racket`**: Racket language analyzer using tree-sitter.
- **`hypergumbo_lang_common.robot`**: Robot Framework analyzer using tree-sitter.
- **`hypergumbo_lang_common.rst`**: reStructuredText analyzer using tree-sitter.
- **`hypergumbo_lang_common.scheme`**: Scheme language analyzer using tree-sitter.
- **`hypergumbo_lang_common.scss`**: SCSS/Sass stylesheet analyzer using tree-sitter.
- **`hypergumbo_lang_common.starlark`**: Starlark (Bazel/Buck) analysis pass using tree-sitter.
- **`hypergumbo_lang_common.svelte`**: Svelte component analyzer using tree-sitter.
- **`hypergumbo_lang_common.thrift`**: Apache Thrift analysis pass using tree-sitter.
- **`hypergumbo_lang_common.vue`**: Vue.js component analyzer using tree-sitter.
- **`hypergumbo_lang_common.wgsl`**: WGSL (WebGPU Shading Language) analysis pass using tree-sitter-wgsl.
- **`hypergumbo_lang_extended1.ada`**: Ada analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.agda`**: Agda analysis pass using tree-sitter-agda.
- **`hypergumbo_lang_extended1.apex`**: Apex language analyzer.
- **`hypergumbo_lang_extended1.asm`**: Assembly language analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.bibtex`**: BibTeX bibliography analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.bitbake`**: BitBake analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.blade`**: Blade template analyzer using regex patterns.
- **`hypergumbo_lang_extended1.capnp`**: Cap'n Proto analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.circom`**: Circom analysis pass using tree-sitter-circom.
- **`hypergumbo_lang_extended1.cobol`**: COBOL analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.d_lang`**: D language analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.fennel`**: Fennel language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.fish`**: Fish shell analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.gdscript`**: GDScript (Godot) analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.gleam`**: Gleam language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.gnuplot`**: Gnuplot script analyzer using regex patterns.
- **`hypergumbo_lang_extended1.hack`**: Hack language analyzer.
- **`hypergumbo_lang_extended1.haxe`**: Haxe language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.janet`**: Janet language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.jsonnet`**: Jsonnet configuration language analyzer.
- **`hypergumbo_lang_extended1.kdl`**: KDL (KDL Document Language) configuration analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.lean`**: Lean 4 analysis pass using tree-sitter-lean.
- **`hypergumbo_lang_extended1.llvm_ir`**: LLVM IR analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.luau`**: Luau language analyzer.
- **`hypergumbo_lang_extended1.mermaid`**: Mermaid diagram analyzer using regex patterns.
- **`hypergumbo_lang_extended1.nim`**: Nim language analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.odin`**: Odin language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.pascal`**: Pascal language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.pony`**: Pony language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.prisma`**: Prisma schema analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.smithy`**: Smithy API definition language analyzer.
- **`hypergumbo_lang_extended1.solidity`**: Solidity analysis pass using tree-sitter-solidity.
- **`hypergumbo_lang_extended1.sparql`**: SPARQL query analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.tcl`**: Tcl language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.twig`**: Twig template analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.v_lang`**: V language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.verilog`**: Verilog/SystemVerilog analysis pass using tree-sitter-verilog.
- **`hypergumbo_lang_extended1.vhdl`**: VHDL analysis pass using tree-sitter-vhdl.
- **`hypergumbo_lang_extended1.wolfram`**: Wolfram Language analysis pass using tree-sitter-wolfram.
- **`hypergumbo_lang_extended1.zig`**: Zig language analyzer using tree-sitter.

### Linkers

- **`hypergumbo_core.linkers.annotation_convention`**: Annotation convention linker for developer-provided pub/sub and dis...
- **`hypergumbo_core.linkers.build_target`**: Build target linker for connecting manifest entries to main() funct...
- **`hypergumbo_core.linkers.cgo`**: Cgo linker for connecting Go C function calls to C/C++ implementati...
- **`hypergumbo_core.linkers.containment`**: Containment linker for creating `contains` edges between containers...
- **`hypergumbo_core.linkers.crypto_flow`**: Crypto-flow linker for detecting encryption/decryption boundary cro...
- **`hypergumbo_core.linkers.database_query`**: Database query linker for detecting SQL queries in application code.
- **`hypergumbo_core.linkers.decorator_dispatch`**: Decorator dispatch linker for registry-based dynamic call resolution.
- **`hypergumbo_core.linkers.dependency`**: Dependency linker for connecting manifest dependencies to code impo...
- **`hypergumbo_core.linkers.di_resolution`**: Multi-language dependency injection resolution linker.
- **`hypergumbo_core.linkers.event_sourcing`**: Event sourcing linker for detecting event publishers and subscribers.
- **`hypergumbo_core.linkers.graphql`**: GraphQL client-schema linker for detecting cross-file GraphQL calls.
- **`hypergumbo_core.linkers.graphql_resolver`**: GraphQL resolver linker for detecting resolver implementations.
- **`hypergumbo_core.linkers.grpc`**: gRPC/Protobuf linker for detecting RPC communication patterns.
- **`hypergumbo_core.linkers.http`**: HTTP client-server linker for detecting cross-language API calls.
- **`hypergumbo_core.linkers.inheritance`**: Inheritance linker for creating extends/implements edges.
- **`hypergumbo_core.linkers.ipc`**: IPC linker for detecting inter-process communication patterns.
- **`hypergumbo_core.linkers.jni`**: JNI linker for connecting Java native methods to C/C++/Rust impleme...
- **`hypergumbo_core.linkers.js_module`**: JS/TS module resolution linker for cross-file import edges.
- **`hypergumbo_core.linkers.lua_ffi`**: Lua FFI linker for connecting LuaJIT FFI calls to C function implem...
- **`hypergumbo_core.linkers.message_dispatch`**: Message dispatch linker for typed wire protocol message patterns.
- **`hypergumbo_core.linkers.message_queue`**: Message queue linker for detecting pub/sub communication patterns.
- **`hypergumbo_core.linkers.middleware_chain`**: Middleware chain linker for connecting consecutive middleware funct...
- **`hypergumbo_core.linkers.napi`**: Node.js N-API linker for connecting JavaScript/TypeScript calls to ...
- **`hypergumbo_core.linkers.openapi`**: OpenAPI/Swagger linker for detecting API schema to handler connecti...
- **`hypergumbo_core.linkers.orm`**: ORM query linker for detecting ORM model references in application ...
- **`hypergumbo_core.linkers.otp`**: OTP GenServer dispatch linker for Elixir.
- **`hypergumbo_core.linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
- **`hypergumbo_core.linkers.pyffi`**: Python FFI linker for connecting Python ctypes/cffi calls to C/C++ ...
- **`hypergumbo_core.linkers.react_component`**: React component linker for detecting JSX composition edges.
- **`hypergumbo_core.linkers.registry`**: Linker registry for dynamic dispatch.
- **`hypergumbo_core.linkers.route_handler`**: Route-handler linker for connecting routes to their handler functions.
- **`hypergumbo_core.linkers.ruby_ffi`**: Ruby FFI linker for connecting Ruby FFI gem calls and C extension r...
- **`hypergumbo_core.linkers.solidity_abi`**: Solidity ABI bridge linker for connecting TS/JS contract calls to S...
- **`hypergumbo_core.linkers.subprocess_cli`**: Subprocess-to-CLI linker for detecting cross-process CLI invocations.
- **`hypergumbo_core.linkers.swift_objc`**: Swift/Objective-C bridging linker.
- **`hypergumbo_core.linkers.tauri_ipc`**: Tauri IPC linker for connecting TypeScript/JavaScript invoke() call...
- **`hypergumbo_core.linkers.type_hierarchy`**: Type hierarchy linker for polymorphic dispatch resolution.
- **`hypergumbo_core.linkers.view_template`**: View template linker for connecting controller actions to rendered ...
- **`hypergumbo_core.linkers.vue_component`**: Vue component linker for resolving cross-file component imports.
- **`hypergumbo_core.linkers.vue_template_method`**: Vue template-method linker for connecting event handlers to script ...
- **`hypergumbo_core.linkers.wasm_bindgen`**: wasm_bindgen linker for connecting JS/TS imports to Rust #[wasm_bin...
- **`hypergumbo_core.linkers.websocket`**: WebSocket linker for detecting WebSocket communication patterns.
- **`hypergumbo_core.linkers.yjs_crdt`**: Yjs/CRDT reactive linker for detecting pub/sub patterns in Yjs-base...

### CLI & I/O

- **`hypergumbo_core.__main__`**: (no docstring)
- **`hypergumbo_core.cli`**: Command-line interface for hypergumbo.
- **`hypergumbo_core.schema`**: Schema versioning and behavior map factory.
- **`hypergumbo_core.sketch`**: Token-budgeted Markdown sketch generation.

### Tracker

- **`hypergumbo_tracker.cache`**: SQLite per-tier read cache for the hypergumbo tracker.
- **`hypergumbo_tracker.cli`**: CLI entry points for hypergumbo-tracker.
- **`hypergumbo_tracker.configure`**: Interactive CLI config editor for hypergumbo tracker.
- **`hypergumbo_tracker.embeddings`**: Tier 2 embedding-based near-duplicate detection for the hypergumbo ...
- **`hypergumbo_tracker.migration`**: Migration from markdown governance files to YAML tracker ops.
- **`hypergumbo_tracker.models`**: Data model for the hypergumbo tracker.
- **`hypergumbo_tracker.setup`**: Idempotent setup wizard for the hypergumbo tracker.
- **`hypergumbo_tracker.stop_hook`**: Stop hook helpers for the hypergumbo tracker.
- **`hypergumbo_tracker.store`**: Store for the hypergumbo tracker — YAML I/O, compile, CRUD, Lamport...
- **`hypergumbo_tracker.sync`**: Streamlined PR workflow for tracker-only changes.
- **`hypergumbo_tracker.sync_log`**: Always-on file logging for tracker sync operations.
- **`hypergumbo_tracker.trackerset`**: TrackerSet: multi-tier unified view over canonical, workspace, and ...
- **`hypergumbo_tracker.tui`**: Textual TUI for the hypergumbo tracker.
- **`hypergumbo_tracker.validation`**: Op log and cross-file validation for the hypergumbo tracker.

---

<!--
GENERATION METADATA (for drift detection):
  commit: 6272e420d6b6
  hypergumbo: 2.3.0
  python: 3.12.3
-->