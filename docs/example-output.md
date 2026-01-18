# Example: hypergumbo analyzing itself

This is the full terminal output of running `hypergumbo hypergumbo/` (with default 4000 token budget).

```
# hypergumbo

Two Outputs **Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected). **Behavior map** (`hypergumbo run`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## Overview
Python (91%), Markdown (6%), Yaml (2%)
334 files    (200 non-test + 134 test)
~130,359 LOC (~66,204 non-test + ~64,155 test)

## Structure

```
hypergumbo/
├── .claude
│   └── settings.local.json
├── .github
│   └── workflows
│       ├── release-mirror.yml
│       └── [and 2 other items]
├── docs
│   ├── hypergumbo-spec.md
│   └── [and 18 other items]
├── scripts
│   ├── auto-pr
│   └── [and 16 other items]
├── src
│   └── hypergumbo
│       ├── ir.py
│       └── [and 29 other items]
├── tests
│   ├── test_sketch.py
│   └── [and 133 other items]
├── package.json
├── pyproject.toml
└── [and 22 other items]
```

## Frameworks

- openai
- pytest
- pytorch
- transformers

## Tests

135 test files · pytest, unittest

*~92% estimated coverage (1329/1442 functions called by tests)*

## Configuration

```
pyproject.toml: name: hypergumbo; version: 1.0.0; license: { text =
LICENSE: AGPL

--- Additional context (semantic) ---
[docs/schema.json]
  > "type": "string", "description": "Schema version (semver)", "const": "0.2.0"
  > "required": [ "schema_version", "view",
  > "websocket_endpoint", "grpc_service", "grpc_servicer",
  > "language": { "type": "string", "description": "Programming language"
  > "implements", "references", "depends_on",
  > ], "description": "Quality assessment" },
  > }, "version": { "type": "string",
  > }, "version": { "type": "string"
  > "pass", "version" ]


[package.json]
  > { "devDependencies": { "bats": "^1.13.0"
  > "devDependencies": { "bats": "^1.13.0" }


[pyproject.toml]
  > [build-system] requires = ["hatchling>=1.24"]
  > "Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only", ]
  > "tree-sitter-sql>=0.3", "tree-sitter-toml>=0.6", "tree-sitter-verilog>=1.0",
  > [project.optional-dependencies] dev = [

[slice.json]
  > { "schema_version": "0.2.0", "view": "slice",
  > "hops": 3, "max_files": 20, "exclude_tests": false,
  > "kind": "class", "language": "python", "path": "/home/jgstern_agent/hypergumbo/src/hypergumbo/linkers/registry.py",
  > ], "edges": [ {
```

## Data Models

- `Symbol` (Python @dataclass) — `src/hypergumbo/ir.py`
- `Span` (Python @dataclass) — `src/hypergumbo/ir.py`
- `AnalysisRun` (Python @dataclass) — `src/hypergumbo/ir.py`
- `Edge` (Python @dataclass) — `src/hypergumbo/ir.py`
- `LanguageSpec` (Python @dataclass) — `src/hypergumbo/taxonomy.py`
- `Pass` (Python @dataclass) — `src/hypergumbo/catalog.py`
- `LinkerContext` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `LinkerResult` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `LinkerRequirement` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `Entrypoint` (Python @dataclass) — `src/hypergumbo/entrypoints.py`
- `FileClassification` (Python @dataclass) — `src/hypergumbo/supply_chain.py`
- `EventPattern` (Python @dataclass) — `src/hypergumbo/linkers/event_sourcing.py`
- `LLMConfig` (Python @dataclass) — `src/hypergumbo/llm_assist.py`
- `GrpcPattern` (Python @dataclass) — `src/hypergumbo/linkers/grpc.py`
- `UsageContext` (Python @dataclass) — `src/hypergumbo/ir.py`
- `WebSocketPattern` (Python @dataclass) — `src/hypergumbo/linkers/websocket.py`
- `Pack` (Python @dataclass) — `src/hypergumbo/catalog.py`
- `AnalysisResult` (Python @dataclass) — `src/hypergumbo/analyze/base.py`
- `DataModel` (Python @dataclass) — `src/hypergumbo/datamodels.py`
- `Limits` (Python @dataclass) — `src/hypergumbo/limits.py`
- `LinkerActivation` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `RepoProfile` (Python @dataclass) — `src/hypergumbo/profile.py`
- `FileAnalysis` (Python @dataclass) — `src/hypergumbo/analyze/base.py`
- `CapsulePlan` (Python @dataclass) — `src/hypergumbo/plan.py`
- `LLMResult` (Python @dataclass) — `src/hypergumbo/llm_assist.py`
- `SupplyChainConfig` (Python @dataclass) — `src/hypergumbo/supply_chain.py`
- `IncludedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `OmittedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `PassConfig` (Python @dataclass) — `src/hypergumbo/plan.py`
- `PhpAnalysisResult` (Python @dataclass) — `src/hypergumbo/analyze/php.py`
- `SketchStats` (Python @dataclass) — `src/hypergumbo/sketch.py`
- `Catalog` (Python @dataclass) — `src/hypergumbo/catalog.py`
- `PackConfig` (Python @dataclass) — `src/hypergumbo/plan.py`
- `Rule` (Python @dataclass) — `src/hypergumbo/plan.py`
- `SliceQuery` (Python @dataclass) — `src/hypergumbo/slice.py`
- ... and 147 more data models

## Source Files

- `src/hypergumbo/schema.py`
- `src/hypergumbo/user_config.py`
- `src/hypergumbo/limits.py`
- `src/hypergumbo/catalog.py`
- `src/hypergumbo/ranking.py`
- `src/hypergumbo/export.py`
- `src/hypergumbo/sketch.py`
- `src/hypergumbo/discovery.py`
- `src/hypergumbo/_embedding_data.py`
- `src/hypergumbo/cli.py`
- `src/hypergumbo/metrics.py`
- `src/hypergumbo/compact.py`
- `src/hypergumbo/framework_patterns.py`
- `src/hypergumbo/datamodels.py`
- `src/hypergumbo/slice.py`
- `src/hypergumbo/entrypoints.py`
- `src/hypergumbo/build_grammars.py`
- `src/hypergumbo/__main__.py`
- `src/hypergumbo/sketch_embeddings.py`
- `src/hypergumbo/llm_assist.py`
- `src/hypergumbo/profile.py`
- `src/hypergumbo/plan.py`
- `src/hypergumbo/taxonomy.py`
- `src/hypergumbo/__init__.py`
- `src/hypergumbo/ir.py`
- `src/hypergumbo/supply_chain.py`
- `src/hypergumbo/analyze/haskell.py`
- `src/hypergumbo/analyze/latex.py`
- `src/hypergumbo/analyze/fortran.py`
- `src/hypergumbo/analyze/csharp.py`
- ... and 224 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `src/hypergumbo/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).

### `src/hypergumbo/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.
- `find_child_by_field(node: 'tree_sitter.Node', field_name: str) -> Optional['tr…` (function) — Find a child node by field name.
- `find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find the first child node of a given type.

### `src/hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `src/hypergumbo/analyze/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

### `src/hypergumbo/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.
- `LinkerResult` (class) — Result from running a linker.
- `LinkerRequirement` (class) — A requirement for a linker to produce useful edges.
- `register_linker(name: str, priority: int=…, description: str=…, requiremen…` (function) — Decorator to register a linker function.

### `src/hypergumbo/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.
- `is_additional_file_candidate(path: Path) -> bool` (function) — Check if a file is a candidate for Additional Files section.

### `src/hypergumbo/analyze/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `src/hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.
- `Pack` (class) — A bundle of passes for a specific use case.

### `src/hypergumbo/sketch.py`
- `ConfigExtractionMode` (class) — Mode for extracting config file content.

### `src/hypergumbo/cli.py`
- `main(argv=…) -> int` (function)
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.

(... and 1694 more symbols across 108 other files)

## Additional Files

- `README.md`
- `docs/GOVERNANCE.md`
- `docs/LANGUAGES.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/LINKERS.md`
- `docs/adr/0005-sketch-budget-allocation.md`
- `CHANGELOG.md`
- `AGENTS.md`
- ... and 66 more files
```

## Representativeness Table

At the end of execution, hypergumbo displays how representative the sketch is at different token budgets:

```
                How Representative Is This Sketch?
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section          ┃ 4,000t ┃ 16,000t ┃ 64,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Data Models      │    20% │     86% │    100% │ confidence mass │
│ Source Files     │    58% │    100% │    100% │ symbol mass     │
│ Key Symbols      │    32% │     41% │     45% │ symbol mass     │
│ Additional Files │   0.0% │    0.0% │    0.0% │ symbol mass     │
└──────────────────┴────────┴─────────┴─────────┴─────────────────┘
```

This table helps you understand what fraction of the codebase's importance is captured at each budget level.
