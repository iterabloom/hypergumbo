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
- **291** Python modules (133 analyzers, 57 linkers across four subcategories per [ADR-3bbb](adr/3bbb-linker-subcategory-restoration.md) — Protocol 11, Bridge 10, Framework 29, Infrastructure 7; 64 core, 4 CLI, 33 tracker)
- **36447** symbols (functions, classes, methods)
- **123980** edges by type:
  - calls: 64427
  - contains: 33625
  - imports: 11047
  - instantiates: 8341
  - references: 4129
  - module_attr_ref: 1155
  - other: 1256

## Package Architecture

The codebase is a Python monorepo with seven packages arranged in a strict
dependency hierarchy. The separation enforces layering: language analyzers
depend on core but not on each other, and the tracker is fully independent.

```
                       hypergumbo (meta-package)
                /       |       |        \
               v        v       v         v
  lang-mainstream  lang-common  lang-extended1  lang-rust-analyzer
  (45 analyzers)  (38 analyzers)  (41 analyzers)   (5 SCIP backend)
                   \      |      |       /
                    v     v      v      v
                       hypergumbo-core
           (IR, CLI, linkers, patterns,
            ranking, discovery, sketch)

  hypergumbo-tracker  (independent)
  (governance TUI, op-log store,
   CRDT-like merge, SQLite cache)
```

| Package | Role |
|---------|------|
| **hypergumbo-core** | IR types (`Symbol`, `Edge`, `Span`), CLI, analysis base classes, 57 linkers (Protocol / Bridge / Framework / Infrastructure — ADR-3bbb), 107 YAML pattern files, sketch/slice output, supply chain classification, symbol resolution, ranking |
| **hypergumbo-lang-mainstream** | 45 tree-sitter analyzers for widely-used languages (Python, JS/TS, Java, Go, Rust, C/C++, Ruby, PHP, C#, Kotlin, Swift, Scala, etc.) |
| **hypergumbo-lang-common** | 38 analyzers for domain-specific and functional languages (Haskell, Elixir, OCaml, Dart, Julia, CUDA, GraphQL, HCL, etc.) |
| **hypergumbo-lang-extended1** | 41 analyzers for specialized languages (Zig, Odin, Solidity, Verilog, VHDL, Agda, Lean, Wolfram, etc.) |
| **hypergumbo-lang-rust-analyzer** | SCIP-backed Rust analyzer (alternative to the tree-sitter Rust analyzer in `lang-mainstream`; activates with `--backend rust-analyzer`) |
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
│  Output: 36447 Symbols + 123980 Edges + UsageContexts           │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     4. PATTERN ENRICHMENT                       │
│  YAML-driven pattern matching (ADR-3aaa):                       │
│  - 8 convention patterns (always loaded)                        │
│  - 99 framework patterns (loaded when framework detected)       │
│  Output: Symbols enriched with meta.concepts                    │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        5. LINKERS                               │
│  Tier 2 edge recovery (ADR-3bbb — Protocol / Bridge /       │
│  Framework / Infrastructure). Match via meta.concepts and       │
│  symbol metadata across files and language boundaries.          │
│  57 linkers: P11 / B10 / F29 / I7 (HTTP, JNI, gRPC, React, ...) │
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
- `language`: Programming language (python, javascript, etc.). Optional per ADR-0031 — Symbols representing source-code declarations carry the host language; synthetic-stand-in Symbols emitted by linkers for protocol/framework patterns (Kafka topics, WASM modules, IPC channels, etc.) leave this ``None`` and populate ``protocol_origin`` instead. Pre-ADR-0031 emits will continue passing a string for the full ADR-0031 §"Phase 1 Producer migration" window.
- `path`: File path where the symbol is defined
- `span`: Source location with lines and columns
- `origin`: Provenance list (INV-jidat). Each element is a pass ID that contributed to this Symbol's existence, ordered chronologically (originating pass first). Single-element lists are the common case. Auto-normalized from scalar str for backward compat.
- `origin_run_id`: Unique execution ID of the analysis run
- `stable_id`: Structural-identity hash within a (qualified_name, module_path) scope (ADR-0014 amended by Phase 6 PR3 / INV-bazij). Survives BODY edits; does NOT survive rename or move — those are now identity-changing operations. Pre-Phase-6 the field was documented as "survives renames/moves", but on the dogfood corpus that promise produced a 60% collision rate (155 zero-param bash functions in one file shared one ID), so name + qualified_name are now part of the hash inputs.
- `shape_id`: Structural implementation fingerprint
- `fingerprint`: Content hash of source bytes (sha256)
- `quality`: Score and reason dict for quality assessment
- `meta`: Optional metadata dict for language-specific information
- `supply_chain_tier`: Position in dependency graph (1=first_party, 2=internal_dep, 3=external_dep, 4=derived). See §14 of spec.
- `supply_chain_reason`: Why this tier was assigned (e.g., "matches ^src/")
- `is_test_file`: True if the file holds test code. Independent of tier — co-located test files can be tier 1.
- `is_example_file`: True if the file is example/demo/sample/tutorial code. Set when the path matches an EXAMPLE_PATTERN.
- `is_config_file`: True if the file is a dependency/build manifest such as ``pyproject.toml`` / ``package.json`` / ``Cargo.toml``. Within tier 2, ``is_test_file`` / ``is_example_file`` / ``is_config_file`` are mutually exclusive — at most one is True per Symbol.
- `is_generated_file`: True if the file is generated code. Independent of the role flags above.
- `is_exported`: True if the symbol is part of the package's public API.
- `cyclomatic_complexity`: McCabe cyclomatic complexity (decision points + 1). Counts if/elif/else, for, while, except, with, and/or, match/case.
- `line_span`: Physical line span of the symbol body — ``end_line - start_line + 1``, INCLUDING blank and comment lines. This is NOT source-lines-of-code (SLOC); the spec's "lines of code" / file-level SLOC convention lives in ``profile.languages[*].loc``. Renamed from ``lines_of_code`` (WI-bozid) so one term no longer names two different counting conventions.
- `signature`: Function/method signature string, e.g., "(x: int, y: str) -> bool". Only populated for callable symbols (functions, methods). None for classes, etc.
- `docstring`: First-line summary of doc comment (truncated to 80 chars).
- `modifiers`: List of semantic modifiers (e.g., ["native", "public", "static"]). Used by linkers for cross-language matching (e.g., JNI needs 'native').
- `discovery_language`: ADR-0031 typed sibling field. Names the host source language where the linker discovered the pattern that produced this Symbol. Populated by Class-B linker emits (synthetic stand-ins discovered in real source files). ``None`` for real-source declarations emitted by analyzers (``language`` already names that information). Shares the ``language`` axis catalog with ``Symbol.language``; the cross-language-detection consumer sites in ``event_sourcing.py`` / ``database_query.py`` / ``message_queue.py`` / ``graphql_resolver.py`` read this rather than ``language``.
- `protocol_origin`: ADR-0031 typed sibling field. Names the protocol or framework family (kafka, websocket, ipc, wasm, openapi, grpc, graphql, etc.) for synthetic stand-ins emitted by linkers fabricating protocol identity from source patterns. Catalog at :mod:`hypergumbo_core.protocol_origins`. ``None`` for real-source declarations and for synthetic stand-ins that don't belong to a recognized protocol family.
- `display_label`: ADR-0032 typed sibling field. Human-readable expression- form string for UI display in commands like ``cmd_explain``. Populated by linkers fabricating synthetic stand-ins (e.g., ``"invoke('save_data')"``, ``"@hg:publishes channel"``, ``"yjs.write(doc)"``); real-source-declaration Symbols leave this
- ```None``. ``# axis`: free-text`` — consumers display the value; no consumer mechanically branches on it.
- `qualified_name`: ADR-0032 typed sibling field. Fully-qualified name including ancestor containers (e.g. ``module.OuterClass.InnerClass.method``). Replaces the prior ``meta["qualified_name"]`` shape. Per-language separator policy lives in :mod:`hypergumbo_core.qualified_name_axis`; the
- ```# axis`: qualified-name`` classification keys into that catalog. ``None`` for analyzers whose ``name`` already encodes the fully-qualified form, or for languages that haven't declared a separator policy yet.

### Edge (`ir.py`)
A relationship between two symbols (e.g., function calls).
- `id`: Unique identifier for this edge instance
- `edge_key`: Canonical identity for deduplication across passes
- `src`: ID of the source symbol (e.g., the caller)
- `dst`: ID of the target symbol (e.g., the callee)
- `edge_type`: Type of relationship (calls, imports, inherits, etc.)
- `line`: Line number where the relationship occurs
- `confidence`: Confidence score (0.0-1.0)
- `origin`: Pass IDs that contributed to this edge (INV-jidat). Auto-normalized from scalar str.
- `origin_run_id`: Unique execution ID of the analysis run
- `evidence_type`: Type of evidence (e.g., ast_call_direct)
- `evidence_lang`: Language for confidence scoring
- `evidence_spans`: Structured locations of evidence
- `is_resolved`: Whether `dst` is a real, in-repo (first-party) symbol node present in the graph (ADR-0037 ruling 1 — resolution names in-repo-ness, NOT target-identification). External/stdlib targets are materialized as `external_symbol` placeholder nodes and are always `is_resolved=False` even though the dst node exists (present-but-synthetic, not absent). The producer-time value (Edge.create default True) is ADVISORY; the finalize edge-resolution sub-step's verdict is what serializes.
- `dst_ref`: Structured identity for the dst endpoint. Populated on every `is_resolved=False` edge after the finalize edge-resolution sub-step (`None` only for an unidentified dangling reference whose id cannot be parsed); `None` for in-repo (`is_resolved=True`) dsts. Canonical source of truth for external-target identity — the legacy `dst` string is built from the same `ExternalRef`. The fourth cell (`is_resolved=True` + populated `dst_ref`) is never produced (ADR-0037 ruling 1 table).
- `derived_from`: Symbol (or Edge) IDs the producer consumed to construct this Edge (INV-rukor). Populated by linkers; None for analyzer-originated edges.
- `quality`: Score and reason dict for quality assessment
- `meta`: Optional metadata dict. Dataflow edges store access_mode (ADR-0015) and channel here; cross-boundary edges store data_direction (ADR-0038 ruling 3).


## Canonical Vocabularies

Three IR fields are typed by canonical registries with declared axes:

| Field | Registry | Axis | ADR |
|---|---|---|---|
| `Edge.edge_type` | `edge_types.py` | relationship | [ADR-0023](adr/0023-edge-type-relationship-not-endpoints.md) |
| `Symbol.kind` | `symbol_kinds.py` | language construct | [ADR-0027](adr/0027-symbol-kind-language-construct-only.md) |
| `Edge.evidence_type` | `evidence_types.py` | inference pathway | [ADR-0028](adr/0028-evidence-type-inference-pathway-only.md) |

Each axis is declared per [ADR-0024](adr/0024-axis-declaration-template.md)'s four-part template (axis name, axiom, consumer pattern, enforcement). Properties not intrinsic to the axis live elsewhere: endpoint kind on `dst.kind`, framework participation in `Symbol.meta` / `Edge.meta` (key vocabulary in `axis_meta_keys.py`), resolution status in the sibling `Edge.is_resolved` bool.

Four drift linters enforce the discipline statically:

- **L1 axis-drift** (`check-edge-type-drift`, `check-symbol-kind-drift`, `check-evidence-type-drift`) — every literal `*EDGE_TYPE* / *KIND* / *EVIDENCE_TYPE*` set in `packages/` must be a subset of the canonical registry. Runs in pre-commit.
- **L3 producer-coherence** (`check-producer-axis-coherence`) — literal-string keyword arguments at `Edge.create` / `Symbol(...)` construction sites must match the registry; handles inline ternaries, f-strings, and locally-bound names.
- **L4 fallback-coherence** (`check-fallback-coherence`) — any `Edge.create` site declaring `meta["disambiguation_fallback"]=True` must also bind `confidence ≤ 0.5` on the same call.
- **Runtime coherence** (`check-edge-type-runtime-coherence`) — partitions emitted edges by `(src.kind, src.language, dst.kind, dst.language)` to surface producer-side endpoint-shape leakage.

Live by-axis view: [`docs/concept-axes.md`](concept-axes.md). Downstream consumers of the behavior-map JSON: [`docs/migrating-edge-types.md`](migrating-edge-types.md).

## Most-Connected Symbols (auto)

These symbols have the highest bidirectional centrality
(`score = in_degree * (1 + ln(1 + out_degree))`):

| Symbol | Kind | Score | Location |
|--------|------|-------|----------|
| `Symbol` | class | 9212.9 | ir.py |
| `Span` | class | 6204.1 | ir.py |
| `run_behavior_map` | function | 3497.0 | cli.py |
| `write_text` | external_symbol | 3260.0 | <external> |
| `LinkerContext` | class | 3088.7 | registry.py |
| `TrackerApp` | class | 1943.0 | tui.py |
| `load_framework_patterns` | function | 1869.3 | framework_patterns.py |
| `Edge.create` | method | 1813.8 | ir.py |
| `Path` | external_symbol | 1575.0 | <external> |
| `main` | function | 1563.1 | cli.py |
| `clear_pattern_cache` | function | 1336.8 | framework_patterns.py |
| `Edge` | class | 1239.6 | ir.py |
| `append` | external_symbol | 1224.0 | <external> |
| `TreeSitterAnalyzer` | class | 1074.7 | base.py |
| `get` | external_symbol | 1069.0 | <external> |

## Pattern System

### Key Components

- **`framework_patterns.py`**: Loads and applies YAML pattern files
- **`frameworks/*.yaml`**: 107 pattern files (8 convention + 99 framework)
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

## YAML Catalogs (auto)

The `hypergumbo-core` package ships 153 YAML catalog files across 8 directories. Each directory holds a category of analysis data consumed by a specific loader; the registry at `hypergumbo_core.yaml_catalogs` is the canonical index. Run `scripts/yaml-catalog-index` for the same view at the CLI, or `scripts/yaml-catalog-index --check` to verify the registry matches the filesystem.

| Directory | Files | ADR | Loader | Purpose |
|---|---:|---|---|---|
| `frameworks/` | 107 | ADR-3aaa | `hypergumbo_core.framework_patterns` | Framework + convention patterns for symbol enrichment (decorators, annotations, naming conventions). |
| `dataflow_patterns/` | 20 | ADR-0015 | `hypergumbo_core.dataflow` | Per-language dataflow access-mode classification rules. |
| `io_primitives/` | 14 | ADR-0016 | `hypergumbo_core.io_boundary` | Per-language I/O primitive catalog (filesystem, network, subprocess, env, IPC, browser storage). |
| `cfg_nodes/` | 5 | ADR-0017 | `hypergumbo_core.cfg` | Per-language tree-sitter node mappings for the CFG builder. |
| `taint_sources/` | 2 | ADR-0017 | `hypergumbo_core.taint` | Trust-zone source declarations for taint-flow analysis. |
| `taint_sanitizers/` | 1 | ADR-0017 | `hypergumbo_core.taint` | Sanitizer declarations for taint-flow analysis. |
| `function_summaries/` | 2 | ADR-0017 | `hypergumbo_core.function_summaries` | Per-language function summaries (return-type and side-effect annotations consumed by language-config). |
| `url_folding/` | 2 | — | `hypergumbo_core.url_folding` | Per-idiom URL-folding declarations (string interpolation, array join, ...) wiring active route-detector languages to engine functions in hypergumbo_core.url_folding. |

Adding a new catalog category: create the directory, write the loader (or extend an existing one), and register a `CatalogSpec` in `hypergumbo_core.yaml_catalogs.YAML_CATALOGS`. The drift check fails until the registry entry lands.

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
| `merge-pr` | Focused recovery script for merging (or closing) existing PRs |
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
| `loop-toggle` | Toggle autonomous mode on/off with support for multiple modes. |
| `stop-hook-preview` | stop-hook-preview — Dry-run stop hook simulator. |
| `tracker` | Thin wrapper for hypergumbo-tracker CLI. |
| `tracker-textconv` | Git textconv driver for hypergumbo-tracker .ops files. |
| `uninstall-embeddings` | Uninstall embedding dependencies from hypergumbo. |

### Analysis & Bakeoff

| Script | Description |
|--------|-------------|
| `analyze-artifacts` | Analyze hypergumbo bakeoff artifacts to extract insights. |
| `bakeoff-broad` | bakeoff-broad - Automated hypergumbo bakeoff runner and analyzer |
| `bakeoff-broad-reflect` | bakeoff-broad-reflect - LLM-driven parse correctness assessment for BROAD bakeoff |
| `bakeoff-deep` | bakeoff-deep - Feature-focused hypergumbo bakeoff runner |
| `bakeoff-deep-reflect` | bakeoff-deep-reflect - LLM-driven qualitative assessment of hypergumbo outputs |
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

### Other

| Script | Description |
|--------|-------------|
| `agent-notes` | Agent-owned notes CLI for the split state-file design (INV-jofaf facet 2). |
| `agent-supervisor` | scripts/agent-supervisor — hypergumbo tmux session supervisor (WI-razub). |
| `audit-closure-evidence` | audit-closure-evidence — advisory detector for proxy-only closures (WI-dafun). |
| `audit-stale-timestamps` | Audit embedded timestamps in agent state files vs filesystem mtimes. |
| `backfill-training-data-cohort-tags.py` | Backfill cohort metadata for v0 training corpus entries. |
| `bakeoff-map` | bakeoff-map - Chronicle and map hypergumbo bakeoff artifacts. |
| `build-scip-proto` | Regenerate packages/hypergumbo-core/src/hypergumbo_core/scip/_generated/scip_pb2.py |
| `build_combined_trend.py` | Build the cross-tranche combined trend report covering ALL completed dogfooding tranches. |
| `check-audit-findings` | Pre-commit lint: every audit-findings doc under ``docs/audits/`` parses |
| `check-docstring-drift` | Module-docstring drift checker — co-change scan over .py source trees. |
| `check-edge-type-drift` | Pre-commit lint: ``*EDGE_TYPE*`` sets in packages/ must be subsets |
| `check-edge-type-runtime-coherence` | Runtime coherence check for the ADR-0023 edge-type axis. |
| `check-evidence-type-drift` | Pre-commit lint: ``*EVIDENCE_TYPE*`` sets in packages/ must be |
| `check-fallback-coherence` | Pre-commit lint: INV-zuhub fallback-coherence at Edge.create call sites. |
| `check-multi-value-field-axis-declaration` | Pre-commit / CI lint: every str-typed field on a core dataclass |
| `check-pass-id-agreement` | Pre-commit / CI lint: catalog pass IDs agree with runtime pass IDs. |
| `check-producer-axis-coherence` | Pre-commit lint: literal-string keyword arguments to ``Edge.create``, |
| `check-schema-coverage` | Corpus-driven schema-coverage gate (WI-luzuh). |
| `check-self-tree-validation` | Full-suite self-tree validation ratchet gate (WI-jigup). |
| `check-symbol-kind-drift` | Pre-commit lint: ``*KIND*`` sets in packages/ must be subsets of the |
| `concept-audit-record` | Record the completion of a Fundamental Concept Audit. |
| `dead-code-prospector-run.py` | Lightweight one-shot dead-code-maybe prospecting run. |
| `deleak-ledger` | Delete-only de-leaker for the dogfood issue ledger (agent_notes). |
| `finetune-transcript-model` | G-Vendi-guided data selection and finetuning for the local transcript model. |
| `generate-concept-axes` | Generate ``docs/concept-axes.md`` — a human-readable by-axis view |
| `generate-concepts` | Generate docs/CONCEPTS.md — the concept-vocabulary registry. |
| `generate-security-md` | Regenerate the audited-IO-surface section of SECURITY.md. |
| `generate_schema_lib.py` | (no description) |
| `highsev_root_review.py` | High-severity net-new root-review collator (Layer 1 of the root-review helper). |
| `measure-playbook-overlap.py` | Measure read-then-injected playbook overlap (waste signal). |
| `per_package_fallback.py` | Per-package fallback for ``scripts/smart-test``'s test selection. |
| `refresh-stdlib-modules` | Refresh the ``stdlib_modules`` section of an IO-primitive YAML catalog. |
| `tracker-path-linter` | Scan tracker items for stale file-path references. |
| `verify-tracker-pr` | Check if a tracker sync PR's ops data is already |
| `yaml-catalog-index` | Index hypergumbo's YAML catalogs (canonical-registry view). |

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

Per [ADR-3bbb](adr/3bbb-linker-subcategory-restoration.md), the first line of every linker module's docstring must declare its subcategory in the form `"""<Protocol|Bridge|Framework|Infrastructure> linker: <one-line purpose>.`

1. Create `packages/hypergumbo-core/src/hypergumbo_core/linkers/<name>.py`
2. Register using the decorator pattern:

```python
from .registry import register_linker, LinkerContext, LinkerResult

@register_linker(
"ipc",
priority=50,
activation=LinkerActivation(frameworks={"tauri"}),
requirements=LinkerRequirement(...),
)
def link_ipc(ctx: LinkerContext) -> LinkerResult:
repo_root = ctx.repo_root
# ... do linking ...
return LinkerResult(symbols=symbols, edges=edges, run=run)
```

3. Add tests in `packages/hypergumbo-core/tests/test_<name>_linker.py`

## Module Reference (auto)

### Core

- **`hypergumbo_core.analyzer_identity`**: Compute a hash representing the analyzer's identity (WI-panih).
- **`hypergumbo_core.audit_findings`**: Parser and validator for the audit-findings document format.
- **`hypergumbo_core.axis_drift`**: Field-agnostic AST drift detector for axis-bearing canonical regist...
- **`hypergumbo_core.axis_meta_keys`**: Canonical registry of ``Symbol.meta`` and ``Edge.meta`` key names.
- **`hypergumbo_core.behavior_map_io`**: Consumer-side helper for reading hypergumbo behavior maps (WI-mokim).
- **`hypergumbo_core.build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`hypergumbo_core.catalog`**: Catalog of available analysis passes (registry-derived).
- **`hypergumbo_core.cfg`**: Language-parameterized CFG builder using fringe-based recursive alg...
- **`hypergumbo_core.compact`**: Compact output mode: budget-aware symbol selection + residual summa...
- **`hypergumbo_core.confidence`**: Evidence -> confidence derivation (the ADR-0039 detection-reliabili...
- **`hypergumbo_core.dataflow`**: YAML-driven dataflow classification for edges (ADR-0015).
- **`hypergumbo_core.datamodels`**: Data model detection for code analysis.
- **`hypergumbo_core.discovery`**: File discovery with exclude patterns, locale handling, and extensio...
- **`hypergumbo_core.edge_types`**: Canonical registry of edge types in hypergumbo's behavior map.
- **`hypergumbo_core.entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`hypergumbo_core.evidence_types`**: Canonical registry of Edge.evidence_type values in hypergumbo's beh...
- **`hypergumbo_core.fallback_coherence`**: INV-zuhub fallback-coherence linter.
- **`hypergumbo_core.finalize`**: The finalize stage: the single pre-serialization reconcile point (A...
- **`hypergumbo_core.fingerprint`**: Symbol-level content fingerprinting (WI-fanun; context-aware since ...
- **`hypergumbo_core.framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-3aaa).
- **`hypergumbo_core.gitleaks`**: Gitleaks integration for secret scanning.
- **`hypergumbo_core.import_scope`**: Per-file import-binding bookkeeping for language analyzers (WI-tihu...
- **`hypergumbo_core.io_boundary`**: I/O boundary analysis — catalog loading and edge matching (ADR-0016).
- **`hypergumbo_core.ir`**: Internal Representation (IR) for code analysis.
- **`hypergumbo_core.limits`**: Limits tracking for behavior map output.
- **`hypergumbo_core.linkers.registry`**: Linker registry for dynamic dispatch.
- **`hypergumbo_core.metrics`**: Metrics computation for behavior map output.
- **`hypergumbo_core.multi_value_field_axis`**: Multi-value field axis declaration linter (WI-busij).
- **`hypergumbo_core.name_matcher`**: Name-form normalization at matcher boundaries (Level 2 of WI-zigah).
- **`hypergumbo_core.partial_install_warnings`**: Runtime warnings for partial installations (ADR-0010 Item 8).
- **`hypergumbo_core.pass_metadata`**: Per-pass metadata lookup for the finalize stage (run-lifecycle:F1 /...
- **`hypergumbo_core.paths`**: Centralized path handling utilities for hypergumbo.
- **`hypergumbo_core.producer_coherence`**: Producer-side axis-coherence linter for Edge / Symbol constructors.
- **`hypergumbo_core.profile`**: Repo profile detection - language and framework heuristics.
- **`hypergumbo_core.protocol_origins`**: Canonical registry of ``Symbol.protocol_origin`` values (ADR-0031).
- **`hypergumbo_core.qualified_name_axis`**: Per-language separator policy for ``Symbol.qualified_name`` (ADR-00...
- **`hypergumbo_core.ranking`**: Symbol and file ranking utilities for hypergumbo output.
- **`hypergumbo_core.repo_fingerprint`**: Repository fingerprint: spec-defined hash of analyzed code state.
- **`hypergumbo_core.runtime_coherence`**: Runtime corpus-based coherence check for the ADR-0023 edge-type axis.
- **`hypergumbo_core.rust_analyzer_install`**: Installer + availability helpers for the ``rust-analyzer`` binary (...
- **`hypergumbo_core.safety_zones`**: Hypergumbo's internal write wrappers — declare each write's safety ...
- **`hypergumbo_core.scip._generated.scip_pb2`**: Generated protocol buffer code.
- **`hypergumbo_core.scip.calls`**: SCIP ``Occurrence`` → hypergumbo call-edge translation shim (WI-maf...
- **`hypergumbo_core.scip.descriptor`**: Parser for Sourcegraph SCIP symbol strings.
- **`hypergumbo_core.scip.edges`**: SCIP ``Index`` → hypergumbo ``Edge`` translation shim (WI-mafut Pha...
- **`hypergumbo_core.scip.index`**: SCIP ``Index`` → hypergumbo ``Symbol`` translation shim (WI-mafut P...
- **`hypergumbo_core.selection.filters`**: Path classification and symbol kind filtering for selection.
- **`hypergumbo_core.selection.language_proportional`**: Language-proportional symbol selection utilities.
- **`hypergumbo_core.selection.token_budget`**: Token estimation and budget management for LLM-aware output.
- **`hypergumbo_core.sketch_embeddings`**: Embedding-based utilities for sketch generation.
- **`hypergumbo_core.slice`**: Graph slicing for LLM context extraction.
- **`hypergumbo_core.spec_validator`**: Spec-vs-data validator stage (ADR-0033, INV-sugat).
- **`hypergumbo_core.supply_chain`**: Supply chain classification for code analysis.
- **`hypergumbo_core.symbol_indexes`**: Multi-value Symbol-name indexes for analyzers and linkers (WI-sofaf).
- **`hypergumbo_core.symbol_kinds`**: Canonical registry of Symbol.kind values in hypergumbo's behavior map.
- **`hypergumbo_core.symbol_resolution`**: Unified symbol resolution with pluggable matching strategies.
- **`hypergumbo_core.taint`**: Taint catalog loading and taint-flow propagation (ADR-0017 Phases 1...
- **`hypergumbo_core.taint_refine`**: Post-DDG IR refinement pass: resolve unresolved-external method-cal...
- **`hypergumbo_core.taxonomy`**: File taxonomy classification (ADR-0004).
- **`hypergumbo_core.test_masking`**: Slow test masking for smart-test.
- **`hypergumbo_core.validation_ratchet`**: Shrink-only ratchet comparison for validation-report violation matr...
- **`hypergumbo_core.verify_claims`**: Security claim verification against I/O boundary and taint-flow ana...
- **`hypergumbo_core.visibility`**: Canonical visibility axis (INV-jusot).
- **`hypergumbo_core.yaml_catalogs`**: Canonical registry of YAML catalog directories shipped under hyperg...

### Analyzers

- **`hypergumbo_core.analyze.all_analyzers`**: Facade for analyzer dispatch — delegates to the decorator-based reg...
- **`hypergumbo_core.analyze.base`**: Base classes and utilities for language analyzers.
- **`hypergumbo_core.analyze.cyclomatic`**: Grammar-agnostic McCabe cyclomatic-complexity walker + decision-poi...
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
- **`hypergumbo_lang_mainstream.jvm_deps`**: JVM dependency manifest parsing for Gradle and Maven projects.
- **`hypergumbo_lang_mainstream.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`hypergumbo_lang_mainstream.lua`**: Lua analysis pass using tree-sitter-lua.
- **`hypergumbo_lang_mainstream.make`**: Makefile analysis pass using tree-sitter-make.
- **`hypergumbo_lang_mainstream.manifest_targets`**: Build-target extraction from language-specific manifest files.
- **`hypergumbo_lang_mainstream.markdown`**: Markdown documentation analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.objc`**: Objective-C analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.perl`**: Perl analysis pass using tree-sitter.
- **`hypergumbo_lang_mainstream.php`**: PHP analysis pass using tree-sitter-php.
- **`hypergumbo_lang_mainstream.play_routes`**: Play Framework routes file parser.
- **`hypergumbo_lang_mainstream.powershell`**: PowerShell analysis pass using tree-sitter.
- **`hypergumbo_lang_mainstream.properties`**: Java properties file analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.py`**: Python AST analysis pass.
- **`hypergumbo_lang_mainstream.py_def_use`**: Python def/use extractor for intraprocedural dataflow analysis (ADR...
- **`hypergumbo_lang_mainstream.py_deps`**: Python dependency manifest parsing for ``pyproject.toml`` projects.
- **`hypergumbo_lang_mainstream.requirements`**: Python requirements.txt analyzer using tree-sitter.
- **`hypergumbo_lang_mainstream.ruby`**: Ruby analysis pass using tree-sitter-ruby.
- **`hypergumbo_lang_mainstream.rust`**: Rust analysis pass using tree-sitter-rust.
- **`hypergumbo_lang_mainstream.rust_def_use`**: Rust def/use extractor for intraprocedural dataflow analysis (ADR-0...
- **`hypergumbo_lang_mainstream.rust_scip`**: SCIP → rust.py stable_id mapping helper (WI-bajuz, ADR-0014 §3).
- **`hypergumbo_lang_mainstream.scala`**: Scala analysis pass using tree-sitter-scala.
- **`hypergumbo_lang_mainstream.sql`**: SQL schema analysis pass using tree-sitter-sql.
- **`hypergumbo_lang_mainstream.swift`**: Swift analysis pass using tree-sitter-swift.
- **`hypergumbo_lang_mainstream.symbol_introspection`**: Shared per-language dispatchers for Symbol introspection fields.
- **`hypergumbo_lang_mainstream.toml_config`**: TOML configuration file analyzer using tree-sitter-toml.
- **`hypergumbo_lang_mainstream.ts_def_use`**: TypeScript/JavaScript def/use extractor for intraprocedural dataflo...
- **`hypergumbo_lang_mainstream.xml_config`**: XML configuration analysis pass using tree-sitter-xml.
- **`hypergumbo_lang_mainstream.yaml`**: General YAML file-anchor analyzer (INV-babuj).
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
- **`hypergumbo_lang_extended1.tlaplus`**: TLA+ analysis pass using tree-sitter-tlaplus.
- **`hypergumbo_lang_extended1.twig`**: Twig template analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.v_lang`**: V language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.verilog`**: Verilog/SystemVerilog analysis pass using tree-sitter-verilog.
- **`hypergumbo_lang_extended1.vhdl`**: VHDL analysis pass using tree-sitter-vhdl.
- **`hypergumbo_lang_extended1.wolfram`**: Wolfram Language analysis pass using tree-sitter-wolfram.
- **`hypergumbo_lang_extended1.zig`**: Zig language analyzer using tree-sitter.
- **`hypergumbo_lang_rust_analyzer.analyzer`**: Registered analyzer entry point for the SCIP-backed Rust backend (W...
- **`hypergumbo_lang_rust_analyzer.gate`**: Opt-in gate for the SCIP-backed Rust analyzer (WI-duzul Slice C gate).
- **`hypergumbo_lang_rust_analyzer.graceful_degrade`**: Graceful-degrade orchestrator for the SCIP-backed Rust analyzer (WI...
- **`hypergumbo_lang_rust_analyzer.invoke`**: Shell-out wrapper for ``rust-analyzer scip`` (WI-duzul Slice B-first).
- **`hypergumbo_lang_rust_analyzer.translate`**: SCIP bytes → hypergumbo ``(Symbol, Edge)`` translation with rust.py...

### Linkers

- **`hypergumbo_core.linkers.airflow_framework_dispatch`**: Framework linker: Airflow class-based plugin framework dispatch (WI...
- **`hypergumbo_core.linkers.annotation_convention`**: Protocol linker: annotation convention for developer-provided pub/s...
- **`hypergumbo_core.linkers.build_target`**: Infrastructure linker: build target for connecting manifest entries...
- **`hypergumbo_core.linkers.cgo`**: Bridge linker: Cgo for connecting Go C function calls to C/C++ impl...
- **`hypergumbo_core.linkers.containment`**: Infrastructure linker: containment for creating `contains` edges be...
- **`hypergumbo_core.linkers.controller_routes`**: Framework linker: controller → route methods containment.
- **`hypergumbo_core.linkers.crypto_flow`**: Protocol linker: crypto-flow for detecting encryption/decryption bo...
- **`hypergumbo_core.linkers.database_query`**: Protocol linker: database query for detecting SQL queries in applic...
- **`hypergumbo_core.linkers.decorator_dispatch`**: Framework linker: decorator dispatch for registry-based dynamic cal...
- **`hypergumbo_core.linkers.dependency`**: Infrastructure linker: dependency for connecting manifest dependenc...
- **`hypergumbo_core.linkers.di_resolution`**: Framework linker: multi-language dependency injection resolution.
- **`hypergumbo_core.linkers.django_orm_dispatch`**: Framework linker: Django ORM method dispatch (WI-nosug).
- **`hypergumbo_core.linkers.event_sourcing`**: Protocol linker: event sourcing for detecting event publishers and ...
- **`hypergumbo_core.linkers.go_cobra`**: Framework linker: Go spf13/cobra CLI command dispatch.
- **`hypergumbo_core.linkers.go_memberlist`**: Framework linker: Go hashicorp/memberlist cluster delegate callback.
- **`hypergumbo_core.linkers.graphql`**: Framework linker: GraphQL client-schema for detecting cross-file Gr...
- **`hypergumbo_core.linkers.graphql_resolver`**: Framework linker: GraphQL resolver for detecting resolver implement...
- **`hypergumbo_core.linkers.grpc`**: Framework linker: gRPC/Protobuf for detecting RPC communication pat...
- **`hypergumbo_core.linkers.http`**: Protocol linker: HTTP client-server for detecting cross-language AP...
- **`hypergumbo_core.linkers.inheritance`**: Infrastructure linker: inheritance for creating extends/implements/...
- **`hypergumbo_core.linkers.inherited_calls`**: Infrastructure linker: inherited-call resolution via ancestor walking.
- **`hypergumbo_core.linkers.ipc`**: Protocol linker: IPC for detecting inter-process communication patt...
- **`hypergumbo_core.linkers.jackson_dispatch`**: Framework linker: Jackson / JavaBean serialization dispatch (WI-gup...
- **`hypergumbo_core.linkers.jni`**: Bridge linker: JNI for connecting Java native methods to C/C++/Rust...
- **`hypergumbo_core.linkers.js_module`**: Infrastructure linker: JS/TS module resolution for cross-file impor...
- **`hypergumbo_core.linkers.kafka_streams_dispatch`**: Framework linker: Kafka Streams topology-builder dispatch (WI-lisov).
- **`hypergumbo_core.linkers.lua_ffi`**: Bridge linker: Lua FFI for connecting LuaJIT FFI calls to C functio...
- **`hypergumbo_core.linkers.message_dispatch`**: Protocol linker: message dispatch for typed wire protocol message p...
- **`hypergumbo_core.linkers.message_queue`**: Protocol linker: message queue for detecting pub/sub communication ...
- **`hypergumbo_core.linkers.method_call_recovery`**: Protocol linker: method-call recovery (WI-gigoz / Path B').
- **`hypergumbo_core.linkers.middleware_chain`**: Framework linker: middleware chain for connecting consecutive middl...
- **`hypergumbo_core.linkers.napi`**: Bridge linker: Node.js N-API for connecting JavaScript/TypeScript c...
- **`hypergumbo_core.linkers.openapi`**: Framework linker: OpenAPI/Swagger for detecting API schema to handl...
- **`hypergumbo_core.linkers.orm`**: Framework linker: ORM query for detecting ORM model references in a...
- **`hypergumbo_core.linkers.otp`**: Framework linker: OTP GenServer dispatch for Elixir and Erlang.
- **`hypergumbo_core.linkers.phoenix_ipc`**: Framework linker: Phoenix Channels IPC for detecting Elixir IPC pat...
- **`hypergumbo_core.linkers.pyffi`**: Bridge linker: Python FFI for connecting Python ctypes/cffi calls t...
- **`hypergumbo_core.linkers.react_component`**: Framework linker: React component for detecting JSX composition edges.
- **`hypergumbo_core.linkers.route_handler`**: Framework linker: route-handler for connecting routes to their hand...
- **`hypergumbo_core.linkers.router_routes`**: Framework linker: router → route registrations containment.
- **`hypergumbo_core.linkers.ruby_ffi`**: Bridge linker: Ruby FFI for connecting Ruby FFI gem calls and C ext...
- **`hypergumbo_core.linkers.rust_trait_dispatch`**: Framework linker: Rust trait-impl method dispatch (WI-kivut).
- **`hypergumbo_core.linkers.solidity_abi`**: Bridge linker: Solidity ABI bridge for connecting TS/JS contract ca...
- **`hypergumbo_core.linkers.subprocess_cli`**: Protocol linker: subprocess-to-CLI for detecting cross-process CLI ...
- **`hypergumbo_core.linkers.swift_objc`**: Bridge linker: Swift/Objective-C bridging.
- **`hypergumbo_core.linkers.tauri_ipc`**: Bridge linker: Tauri IPC for connecting TypeScript/JavaScript invok...
- **`hypergumbo_core.linkers.type_hierarchy`**: Framework linker: type hierarchy for polymorphic dispatch resolution.
- **`hypergumbo_core.linkers.view_template`**: Framework linker: Rails controller action to view template binding.
- **`hypergumbo_core.linkers.view_template_django`**: Framework linker: Django controller action to view template binding...
- **`hypergumbo_core.linkers.view_template_laravel`**: Framework linker: Laravel Blade view-helper → template binding (WI-...
- **`hypergumbo_core.linkers.view_template_phoenix`**: Framework linker: Phoenix controller action to view template (WI-da...
- **`hypergumbo_core.linkers.view_template_spring`**: Framework linker: Spring MVC view-name → template binding (WI-hogik).
- **`hypergumbo_core.linkers.vue_component`**: Infrastructure linker: Vue component for resolving cross-file compo...
- **`hypergumbo_core.linkers.vue_template_method`**: Framework linker: Vue template-method for connecting event handlers...
- **`hypergumbo_core.linkers.wasm_bindgen`**: Bridge linker: wasm_bindgen for connecting JS/TS imports to Rust #[...
- **`hypergumbo_core.linkers.websocket`**: Protocol linker: WebSocket for detecting WebSocket communication pa...
- **`hypergumbo_core.linkers.yjs_crdt`**: Framework linker: Yjs/CRDT reactive for detecting pub/sub patterns ...

### CLI & I/O

- **`hypergumbo_core.__main__`**: (no docstring)
- **`hypergumbo_core.cli`**: Command-line interface for hypergumbo.
- **`hypergumbo_core.schema`**: Schema versioning and behavior map factory.
- **`hypergumbo_core.sketch`**: Token-budgeted Markdown sketch generation.

### Tracker

- **`hypergumbo_tracker.annotations`**: Annotation data model for TUI screenshot annotations (ADR-0020).
- **`hypergumbo_tracker.cache`**: SQLite per-tier read cache for the hypergumbo tracker.
- **`hypergumbo_tracker.cli`**: CLI entry points for hypergumbo-tracker.
- **`hypergumbo_tracker.configure`**: Interactive CLI config editor for hypergumbo tracker.
- **`hypergumbo_tracker.embeddings`**: Tier 2 embedding-based near-duplicate detection for the hypergumbo ...
- **`hypergumbo_tracker.hotspot_markup`**: Wrap detected item IDs in Textual ``[@click=...]`` action markup.
- **`hypergumbo_tracker.id_matching`**: Detect tracker item IDs embedded in free-text panes (descriptions, ...
- **`hypergumbo_tracker.item_nav_render`**: Assemble the display content for the tracker TUI item-nav modal.
- **`hypergumbo_tracker.journal`**: Out-of-repo write-ahead journal for tracker ops — the durability su...
- **`hypergumbo_tracker.migration`**: Migration from markdown governance files to YAML tracker ops.
- **`hypergumbo_tracker.models`**: Data model for the hypergumbo tracker.
- **`hypergumbo_tracker.nav_history`**: Browser-style navigation history for the tracker TUI's item-nav modal.
- **`hypergumbo_tracker.preview`**: Inline SVG preview for TUI discussion threads (ADR-0020 Part 2).
- **`hypergumbo_tracker.preview_pipeline`**: SVG→PNG→ANSI rendering pipeline with graceful degradation (ADR-0020).
- **`hypergumbo_tracker.race_log`**: Forensic log for transient I/O races on tracker ``.ops`` files.
- **`hypergumbo_tracker.screenshot_save`**: Screenshot save and auto-create tracker item (ADR-0020, WI-rujoz).
- **`hypergumbo_tracker.serve`**: Starlette/uvicorn server for htrac serve (ADR-0019).
- **`hypergumbo_tracker.serve_auth_config`**: Auth config schema for htrac serve (ADR-0019).
- **`hypergumbo_tracker.serve_duress`**: DuressHandler Protocol for htrac serve (ADR-0019).
- **`hypergumbo_tracker.serve_password`**: Bcrypt password verification for htrac serve (ADR-0019).
- **`hypergumbo_tracker.serve_sessions`**: In-memory session management for htrac serve (ADR-0019).
- **`hypergumbo_tracker.serve_webauthn`**: WebAuthn/FIDO2 registration and authentication for htrac serve (ADR...
- **`hypergumbo_tracker.setup`**: Idempotent setup wizard for the hypergumbo tracker.
- **`hypergumbo_tracker.stop_hook`**: Stop hook helpers for the hypergumbo tracker.
- **`hypergumbo_tracker.store`**: Store for the hypergumbo tracker — YAML I/O, compile, CRUD, Lamport...
- **`hypergumbo_tracker.svg_detection`**: SVG path detection in discussion messages (ADR-0020).
- **`hypergumbo_tracker.svg_injection`**: Cell-to-SVG coordinate mapping and annotation injection (ADR-0020).
- **`hypergumbo_tracker.sync`**: Streamlined PR workflow for tracker-only changes.
- **`hypergumbo_tracker.sync_log`**: Always-on file logging for tracker sync operations.
- **`hypergumbo_tracker.tag_catalog`**: Tag catalog: persistent metadata for tracker tags (WI-lifal).
- **`hypergumbo_tracker.trackerset`**: TrackerSet: multi-tier unified view over canonical, workspace, and ...
- **`hypergumbo_tracker.tui`**: Textual TUI for the hypergumbo tracker.
- **`hypergumbo_tracker.validation`**: Op log and cross-file validation for the hypergumbo tracker.

---

<!--
GENERATION METADATA (for drift detection):
  commit: c34afecddda4
  hypergumbo: 6.1.0
  python: 3.12.3
-->