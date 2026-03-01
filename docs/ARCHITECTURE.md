# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **166** Python modules (108 analyzers, 31 linkers, 23 core, 4 CLI)
- **3230** symbols (functions, classes, methods)
- **30162** edges by type:
  - calls: 17830
  - imports: 6493
  - instantiates: 4112
  - contains: 895
  - dispatches_to: 240
  - decorated_by: 234
  - other: 358

## Sketch (hypergumbo on hypergumbo)

````markdown
# hypergumbo

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

## Overview
Python (90%), Markdown (5%), Yaml (3%)
705 files    (372 non-test + 333 test)
~282,272 LOC (~120,230 non-test + ~162,042 test)

## Structure

hypergumbo/
├── .agent
│   ├── cooldown_prompt.md
│   └── [and 7 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 8 other items]
├── docs
│   ├── FRAMEWORKS.md
│   └── [and 22 other items]
├── packages
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── ir.py
│   │   │       └── [and 29 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 93 other items]
│   │   └── [and 2 other items]
│   ├── hypergumbo-tracker
│   │   ├── src
│   │   │   └── hypergumbo_tracker
│   │   │       ├── cache.py
│   │   │       ├── cli.py
│   │   │       ├── trackerset.py
│   │   │       └── [and 10 other items]
│   │   └── [and 5 other items]
│   └── [and 4 other items]
├── scripts
│   ├── lib
│   │   └── forgejo-api.sh
│   └── [and 32 other items]
├── tests
│   └── test_bakeoff_reflect.py
├── conftest.py
├── pyproject.toml
├── setup.py
└── [and 24 other items]

## Frameworks

- pytest
- pytorch
- transformers

## Tests

333 test files · hypothesis, pytest, unittest

*~93% estimated coverage (2514/2690 functions called by tests)*

## Configuration

LICENSE: AGPL

--- Additional context (semantic) ---

[LICENSE]
  > ================================================================================

[docs/schema.json]
  > }, "required": [ "schema_version",
  > "entity", "architecture", "package",
  > "grpc_stub", "grpc_client", "grpc_server",
  > "type": "string", "description": "Programming language" },
  > "internal_dep", "external_dep", "derived"
  > }, "required": [ "tier",
  > "kind", "language", "path",
  > "graphql_calls", "message_queue", "query_references",

[pyproject.toml]
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml
  > # Packages are defined in packages/*/pyproject.toml # This file provides pytest, ruff, bandit, and coverage configuration
  > [tool.pytest.ini_options] # Include BRANCHES_*.py files for branch coverage tests (default is test_*.py and *_test.py)

[setup.py]
  > raise SystemExit( "\n"
  > "ERROR: This repository is a monorepo. The root is not an installable package.\n" "\n" "For development setup:\n"
  > "\n" "For development setup:\n" "  python3 -m venv .venv && source .venv/bin/activate\n"
  > "For development setup:\n" "  python3 -m venv .venv && source .venv/bin/activate\n" "  ./scripts/dev-install\n"

## Data Models

- `Symbol` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Edge` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Span` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `AnalysisRun` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `LinkerContext` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `AnalysisResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `FileAnalysis` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `LinkerResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerActivation` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `UsageContext` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `LinkerRequirement` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `CheckResult` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`
- `Limits` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/limits.py`
- `TrackerConfig` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `CompiledItem` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `ValidationResult` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`
- `DataModel` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`
- `LookupResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `RepoProfile` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `Pattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `FieldSchema` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `DiscussionEntry` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `Entrypoint` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py`
- `SliceQuery` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `RegisteredAnalyzer` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `LanguageStats` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `SketchStats` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `Catalog` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `SliceResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `UsagePatternSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `CompactConfig` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `KindConfig` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `Pass` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `EventPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`
- `SupplyChainConfig` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `EmbeddingDuplicateResult` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/embeddings.py`
- `GraphQLClientCall` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/graphql.py`
- `PreflightResult` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`
- `RegisteredLinker` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `SyncResult` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`
- `FrameworkPatternDef` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `GrammarSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/build_grammars.py`
- `SecretFinding` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/gitleaks.py`
- `IncludedSummary` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `OmittedSummary` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `GrpcPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/grpc.py`
- `ParsedItem` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/migration.py`
- `SubprocessCall` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/subprocess_cli.py`
- `UpdateOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `CompactResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `CreateOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `FileClassification` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `LanguageSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`
- `DeferredResolutionStats` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `FailedFile` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/limits.py`
- `DatabaseQueryPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/database_query.py`
- `HttpClientCall` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`
- `PartialInstallWarning` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/partial_install_warnings.py`
- `DemoteOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `DiscussClearOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `DiscussOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `DiscussSummarizeOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `LockOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `PromoteOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `ReconcileOp` (Python @dataclass) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- ... and 62 more data models

## Source Files

- `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `packages/hypergumbo-core/src/hypergumbo_core/paths.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `packages/hypergumbo-core/src/hypergumbo_core/limits.py`
- `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/gitignore.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ini.py`
- `packages/hypergumbo-core/tests/test_message_queue_linker.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/properties.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/message_queue.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/bibtex.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`
- `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`
- `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/embeddings.py`
- `packages/hypergumbo-tracker/tests/helpers.py`
- `packages/hypergumbo-core/src/hypergumbo_core/gitleaks.py`
- `packages/hypergumbo-lang-mainstream/tests/test_gitignore.py`
- `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/kdl.py`
- `packages/hypergumbo-lang-mainstream/tests/test_properties.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/markdown.py`
- `packages/hypergumbo-core/src/hypergumbo_core/ranking.py`
- `packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_swift.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py`
- `packages/hypergumbo-core/src/hypergumbo_core/selection/filters.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/requirements.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/rst.py`
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/racket.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/pascal.py`
- `packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_scala.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/database_query.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/scss.py`
- `packages/hypergumbo-tracker/tests/test_stop_hook.py`
- `packages/hypergumbo-lang-mainstream/tests/test_requirements.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/fennel.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/hlsl.py`
- `packages/hypergumbo-lang-common/tests/test_meson.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/puppet.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/scheme.py`
- `packages/hypergumbo-lang-common/tests/BRANCHES_test_elixir.py`
- `packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_cpp.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/gleam.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/janet.py`
- `packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_c.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/meson.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/prisma.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`
- ... and 472 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `make_pass_id(name: str) -> str` (function) — Return the canonical pass ID for an analyzer or linker.
  (... +1 more, top score: 0.25)

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `TreeSitterAnalyzer` (class) ★ — Base class for tree-sitter-based language analyzers.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.
- `find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find the first child node of a given type.
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `AnalysisResult` (class) — Universal result type for all language analyzers.
  (... +2 more, top score: 0.32)

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.
- `register_linker(name: str, priority: int=…, description: str=…, requiremen…` (function) — Decorator to register a linker function.
- `LinkerResult` (class) — Result from running a linker.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `register_analyzer(name: str, priority: int=…, requires_symbols: list[str] | …` (function) — Decorator to register an analyzer function.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).

### `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `_parse_ops_file(filepath: Path) -> list[dict[str, Any]]` (function) — Parse an ops file using PyYAML CSafeLoader (fast C extension).
- `compile_ops(ops: list[dict[str, Any]], item_id: str=…) -> CompiledItem` (function) — Compile an op log (list of op dicts) into a CompiledItem.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `TrackerSet` (class) — Multi-tier unified view over canonical, workspace, and stealth Stores.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `resolve_actor(agent_patterns: list[str] | None=…) -> tuple[str, str]` (function) — Resolve the current OS user to (by, actor) tuple.
- `load_config(config_dir: Path) -> TrackerConfig` (function) — Load tracker config from the given directory.

### `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `_read_all_manifest_files(repo_root: Path, filename: str, max_depth: int=…) -> str` (function) — Read all manifest files with given name, recursively.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py`
- `Cache` (class) — SQLite read cache for a single tier's Store.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py`
- `TrackerApp` (class) — Textual TUI for the hypergumbo tracker.

### `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `generate_sketch(repo_root: Path, max_tokens: Optional[int]=…, exclude_test…` (function) — Generate a token-budgeted Markdown sketch of the repository.

### `packages/hypergumbo-core/src/hypergumbo_core/ranking.py`
- `compute_centrality(symbols: List[Symbol], edges: List[Edge], hub_threshold: i…` (function) — Compute symbol importance using bidirectional centrality.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`
- `main(argv: list[str] | None=…) -> None` (function) — Primary CLI entry point.

(... and 3098 more symbols across 193 other files)

## Additional Files

- `README.md`
- `CONTRIBUTING.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `packages/hypergumbo-core/README.md`
- `packages/hypergumbo-tracker/README.md`
- `docs/governance-case-critiques.md`
- `docs/LANGUAGES.md`
- `docs/future/registry-factory-vision.md`
- `docs/MIGRATION-2.0.md`
- `docs/future/roadmap-details.md`
- `packages/hypergumbo-lang-mainstream/README.md`
- `docs/GOVERNANCE.md`
- `docs/adr/0003-call-patterns-extension.md`
- `docs/adr/0004-file-taxonomy.md`
- `packages/hypergumbo/README.md`
- `docs/LINKERS.md`
- `docs/history/planning-v1.md`
- `docs/FRAMEWORKS.md`
- `docs/ARCHITECTURE.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/remix.yaml`
- `docs/example-output.md`
- ... and 126 more files
````

## Data Flow (ADR-0003)

```
Source Files
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                          PROFILE                                │
│  Detect languages (by file extension)                           │
│  Detect frameworks (by manifest markers, scoped to languages)   │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         ANALYZERS                               │
│  Pure language processors - NO framework knowledge              │
│  Output: 3230 Symbols + 30162 Edges + UsageContexts             │
│  Rich metadata: decorators, base_classes, parameters            │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                       PATTERN SYSTEM                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Convention Patterns (always loaded):                   │    │
│  │  - main-functions.yaml: main() entrypoints              │    │
│  │  - test-frameworks.yaml: test function detection        │    │
│  │  - language-conventions.yaml: CUDA/WGSL/COBOL/etc.      │    │
│  │  - config-conventions.yaml: NPM/Maven/Cargo deps        │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Framework Patterns (loaded when framework detected):   │    │
│  │  - 81 framework YAML files (fastapi, django, etc.)     │    │
│  └─────────────────────────────────────────────────────────┘    │
│  Output: Symbols enriched with meta.concepts                    │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                          LINKERS                                │
│  Cross-language edge creation                                   │
│  Match via meta.concepts (route paths, gRPC services, etc.)     │
│  31 linkers: HTTP, gRPC, GraphQL, WebSocket, IPC, JNI, etc.     │
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

## Most-Connected Symbols

These symbols have the highest bidirectional centrality
(`score = in_degree * (1 + ln(1 + out_degree))`):

| Symbol | Kind | Score | Location |
|--------|------|-------|----------|
| `Symbol` | class | 3178.3 | ir.py |
| `Span` | class | 2755.5 | ir.py |
| `run_behavior_map` | function | 2035.2 | cli.py |
| `TrackerApp` | class | 907.7 | tui.py |
| `load_framework_patterns` | function | 862.5 | framework_patterns.py |
| `LinkerContext` | class | 816.0 | registry.py |
| `TreeSitterAnalyzer` | class | 735.3 | base.py |
| `Edge` | class | 582.3 | ir.py |
| `Store` | class | 498.2 | store.py |
| `main` | function | 484.8 | cli.py |
| `node_text` | function | 473.0 | base.py |
| `UsageContext.create` | method | 470.0 | ir.py |
| `clear_pattern_cache` | function | 464.0 | framework_patterns.py |
| `find_files` | function | 435.1 | discovery.py |
| `generate_sketch` | function | 432.6 | sketch.py |

## Module Reference

### Core

- **`hypergumbo_core.build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`hypergumbo_core.catalog`**: Catalog of available analysis passes.
- **`hypergumbo_core.compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`hypergumbo_core.datamodels`**: Data model detection for code analysis.
- **`hypergumbo_core.discovery`**: File discovery with exclude patterns and .m file disambiguation.
- **`hypergumbo_core.entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`hypergumbo_core.framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-0003).
- **`hypergumbo_core.gitleaks`**: Gitleaks integration for secret scanning.
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
- **`hypergumbo_lang_mainstream.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`hypergumbo_lang_mainstream.lua`**: Lua analysis pass using tree-sitter-lua.
- **`hypergumbo_lang_mainstream.make`**: Makefile analysis pass using tree-sitter-make.
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
- **`hypergumbo_lang_common.haskell`**: Haskell analysis pass using tree-sitter-haskell.
- **`hypergumbo_lang_common.hcl`**: HCL/Terraform analyzer using tree-sitter.
- **`hypergumbo_lang_common.hlsl`**: HLSL (DirectX shader) analysis pass using tree-sitter.
- **`hypergumbo_lang_common.julia`**: Julia analysis pass using tree-sitter-julia.
- **`hypergumbo_lang_common.latex`**: LaTeX analyzer using tree-sitter.
- **`hypergumbo_lang_common.matlab`**: MATLAB language analyzer using tree-sitter.
- **`hypergumbo_lang_common.meson`**: Meson build system analyzer using tree-sitter.
- **`hypergumbo_lang_common.nix`**: Nix expression analysis pass using tree-sitter-nix.
- **`hypergumbo_lang_common.ocaml`**: OCaml analysis pass using tree-sitter-ocaml.
- **`hypergumbo_lang_common.proto`**: Protocol Buffers (Proto) analysis pass using tree-sitter.
- **`hypergumbo_lang_common.puppet`**: Puppet manifest analyzer using tree-sitter.
- **`hypergumbo_lang_common.purescript`**: PureScript language analyzer using tree-sitter.
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
- **`hypergumbo_lang_extended1.capnp`**: Cap'n Proto analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.cobol`**: COBOL analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.d_lang`**: D language analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.fennel`**: Fennel language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.fish`**: Fish shell analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.gdscript`**: GDScript (Godot) analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.gleam`**: Gleam language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.hack`**: Hack language analyzer.
- **`hypergumbo_lang_extended1.haxe`**: Haxe language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.janet`**: Janet language analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.jsonnet`**: Jsonnet configuration language analyzer.
- **`hypergumbo_lang_extended1.kdl`**: KDL (KDL Document Language) configuration analyzer using tree-sitter.
- **`hypergumbo_lang_extended1.lean`**: Lean 4 analysis pass using tree-sitter-lean.
- **`hypergumbo_lang_extended1.llvm_ir`**: LLVM IR analysis pass using tree-sitter.
- **`hypergumbo_lang_extended1.luau`**: Luau language analyzer.
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

- **`hypergumbo_core.linkers.cgo`**: Cgo linker for connecting Go C function calls to C/C++ implementati...
- **`hypergumbo_core.linkers.containment`**: Containment linker for creating `contains` edges between containers...
- **`hypergumbo_core.linkers.database_query`**: Database query linker for detecting SQL queries in application code.
- **`hypergumbo_core.linkers.dependency`**: Dependency linker for connecting manifest dependencies to code impo...
- **`hypergumbo_core.linkers.event_sourcing`**: Event sourcing linker for detecting event publishers and subscribers.
- **`hypergumbo_core.linkers.graphql`**: GraphQL client-schema linker for detecting cross-file GraphQL calls.
- **`hypergumbo_core.linkers.graphql_resolver`**: GraphQL resolver linker for detecting resolver implementations.
- **`hypergumbo_core.linkers.grpc`**: gRPC/Protobuf linker for detecting RPC communication patterns.
- **`hypergumbo_core.linkers.http`**: HTTP client-server linker for detecting cross-language API calls.
- **`hypergumbo_core.linkers.inheritance`**: Inheritance linker for creating extends/implements edges.
- **`hypergumbo_core.linkers.ipc`**: IPC linker for detecting inter-process communication patterns.
- **`hypergumbo_core.linkers.jni`**: JNI linker for connecting Java native methods to C/C++ implementati...
- **`hypergumbo_core.linkers.js_module`**: JS/TS module resolution linker for cross-file import edges.
- **`hypergumbo_core.linkers.lua_ffi`**: Lua FFI linker for connecting LuaJIT FFI calls to C function implem...
- **`hypergumbo_core.linkers.message_queue`**: Message queue linker for detecting pub/sub communication patterns.
- **`hypergumbo_core.linkers.napi`**: Node.js N-API linker for connecting JavaScript/TypeScript calls to ...
- **`hypergumbo_core.linkers.openapi`**: OpenAPI/Swagger linker for detecting API schema to handler connecti...
- **`hypergumbo_core.linkers.orm`**: ORM query linker for detecting ORM model references in application ...
- **`hypergumbo_core.linkers.otp`**: OTP GenServer dispatch linker for Elixir.
- **`hypergumbo_core.linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
- **`hypergumbo_core.linkers.pyffi`**: Python FFI linker for connecting Python ctypes/cffi calls to C/C++ ...
- **`hypergumbo_core.linkers.registry`**: Linker registry for dynamic dispatch.
- **`hypergumbo_core.linkers.route_handler`**: Route-handler linker for connecting routes to their handler functions.
- **`hypergumbo_core.linkers.ruby_ffi`**: Ruby FFI linker for connecting Ruby FFI gem calls and C extension r...
- **`hypergumbo_core.linkers.subprocess_cli`**: Subprocess-to-CLI linker for detecting cross-process CLI invocations.
- **`hypergumbo_core.linkers.swift_objc`**: Swift/Objective-C bridging linker.
- **`hypergumbo_core.linkers.type_hierarchy`**: Type hierarchy linker for polymorphic dispatch resolution.
- **`hypergumbo_core.linkers.view_template`**: View template linker for connecting controller actions to rendered ...
- **`hypergumbo_core.linkers.vue_component`**: Vue component linker for resolving cross-file component imports.
- **`hypergumbo_core.linkers.vue_template_method`**: Vue template-method linker for connecting event handlers to script ...
- **`hypergumbo_core.linkers.websocket`**: WebSocket linker for detecting WebSocket communication patterns.

### CLI & I/O

- **`hypergumbo_core.__main__`**: (no docstring)
- **`hypergumbo_core.cli`**: Command-line interface for hypergumbo.
- **`hypergumbo_core.schema`**: Schema versioning and behavior map factory.
- **`hypergumbo_core.sketch`**: Token-budgeted Markdown sketch generation.

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

## Pattern System

### Key Components

- **`framework_patterns.py`**: Loads and applies YAML pattern files
- **`frameworks/*.yaml`**: 87 pattern files (6 convention + 81 framework)
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

---

<!--
GENERATION METADATA (for drift detection):
  commit: 5a685fd59689
  hypergumbo: 2.0.2
  python: 3.12.3
-->