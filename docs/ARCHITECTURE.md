# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: 3a6e77a4d6c5
  hypergumbo: 2.0.1
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **152** Python modules (3 analyzers, 19 linkers)
- **10037** symbols (functions, classes, methods)
- **26739** edges by type:
  - calls: 16104
  - imports: 5778
  - instantiates: 4307
  - uses: 311
  - message_queue: 152
  - event_publishes: 42
  - other: 45

## Sketch (hypergumbo on hypergumbo)

````markdown
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (89%), Markdown (6%), Yaml (4%)
492 files    (322 non-test + 170 test)
~185,874 LOC (~96,048 non-test + ~89,826 test)

## Structure

hypergumbo/
├── .agent
│   ├── stop_reflect.md
│   └── [and 3 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 5 other items]
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
│   │   │   └── [and 62 other items]
│   │   ├── pyproject.toml
│   │   └── [and 1 other items]
│   └── [and 3 other items]
├── scripts
│   ├── hypergumbo_diag.py
│   └── [and 24 other items]
├── .gitignore
├── ALLOWED_WEBSITES.md
├── README.md
├── package.json
├── pyproject.toml
└── [and 22 other items]

## Frameworks

- pytest
- transformers

## Tests

170 test files · pytest, unittest

*~90% estimated coverage (1960/2173 functions called by tests)*

## Configuration

LICENSE: AGPL

--- Additional context (semantic) ---
[docs/schema.json]
  > }, "required": [ "schema_version",
  > "entity", "architecture", "package",
  > "grpc_stub", "grpc_client", "grpc_server",
  > "type": "string", "description": "Programming language" },
  > "internal_dep", "external_dep", "derived"
  > }, "required": [ "tier",
  > "kind", "language", "path",
  > "implements", "references", "depends_on",
  > "graphql_calls", "message_queue", "query_references",
  > }, "version": { "type": "string",
  > "type": "string", "description": "Primary language for this framework" },
  > }, "description": "List of patterns to match" },
  > }, "description": "Linkers that should be activated when this framework is detected" }
  > ], "additionalProperties": false }


[package.json]
  > { "devDependencies": { "bats": "^1.13.0"
  > "devDependencies": { "bats": "^1.13.0" }


[pyproject.toml]
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml # This file provides pytest, ruff, bandit, and coverage configuration

## Entry Points

- `hypergumbo` (Python CLI: hypergumbo) — `packages/hypergumbo-core/pyproject.toml`
- `hypergumbo` (Python CLI: hypergumbo) — `packages/hypergumbo/pyproject.toml`
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo-core/src/hypergumbo_core/__main__.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo/src/hypergumbo/__main__.py`
- `main` (Python main()) — `scripts/hypergumbo_diag.py`
- `main` (Python main()) — `scripts/compute_probe_embeddings.py`
- `<module:hypergumbo_diag.py>` (Python script (if __name__ == '__main__')) — `scripts/hypergumbo_diag.py`
- `<module:compute_probe_embeddings.py>` (Python script (if __name__ == '__main__')) — `scripts/compute_probe_embeddings.py`

## Data Models

- `Symbol` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Span` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `AnalysisRun` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Edge` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `LanguageSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`
- `Pass` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `LinkerContext` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerRequirement` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LookupResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `Entrypoint` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py`
- `FileClassification` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `EventPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`
- `GrpcPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/grpc.py`
- `UsageContext` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `WebSocketPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py`
- `LinkerActivation` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `AnalysisResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `DataModel` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`
- `Limits` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/limits.py`
- `FileAnalysis` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `IncludedSummary` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `OmittedSummary` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `SupplyChainConfig` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `CompactConfig` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `PhpAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py`
- `SketchStats` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `SliceQuery` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `GrammarSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/build_grammars.py`
- `RegisteredLinker` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `SliceResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `Catalog` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- ... and 162 more data models

## Source Files

- `scripts/hypergumbo_diag.py`
- `scripts/compute_probe_embeddings.py`
- `packages/hypergumbo-core/tests/test_ipc_linker.py`
- `packages/hypergumbo-core/tests/test_supply_chain.py`
- `packages/hypergumbo-core/tests/test_entrypoints.py`
- `packages/hypergumbo-core/tests/test_compact.py`
- `packages/hypergumbo-core/tests/test_websocket.py`
- `packages/hypergumbo-core/tests/test_message_queue_linker.py`
- `packages/hypergumbo-core/tests/test_linker_filtering.py`
- `packages/hypergumbo-core/tests/test_behavior_map_schema.py`
- `packages/hypergumbo-core/tests/test_symbol_resolution.py`
- `packages/hypergumbo-core/tests/test_route_handler_linker.py`
- `packages/hypergumbo-core/tests/test_sketch_sanity.py`
- `packages/hypergumbo-core/tests/test_run_behavior_map.py`
- `packages/hypergumbo-core/tests/test_catalog.py`
- `packages/hypergumbo-core/tests/test_ir.py`
- `packages/hypergumbo-core/tests/test_cli_symbols.py`
- `packages/hypergumbo-core/tests/test_fastapi_patterns.py`
- `packages/hypergumbo-core/tests/test_dependency_linker.py`
- `packages/hypergumbo-core/tests/test_datamodels.py`
- `packages/hypergumbo-core/tests/test_max_tier.py`
- `packages/hypergumbo-core/tests/test_slice_tier_filter.py`
- `packages/hypergumbo-core/tests/test_file_excludes.py`
- `packages/hypergumbo-core/tests/test_graphql_linker.py`
- `packages/hypergumbo-core/tests/test_selection_token_budget.py`
- `packages/hypergumbo-core/tests/test_http_linker.py`
- `packages/hypergumbo-core/tests/test_limits.py`
- `packages/hypergumbo-core/tests/test_event_sourcing_linker.py`
- `packages/hypergumbo-core/tests/test_stable_shape_ids.py`
- `packages/hypergumbo-core/tests/test_profile.py`
- ... and 309 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `Edge.create(cls, src: str, dst: str, edge_type: str, line: int, origin…` (method)
- `Edge` (class) — A relationship between two symbols (e.g., function calls).

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py`
- `AnalyzerSpec` (class) — Specification for an analyzer in the registry.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.

### `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `packages/hypergumbo-core/pyproject.toml`
- `build-system` (table)

(... and 2606 more symbols across 171 other files)

## Additional Files

- `README.md`
- `CHANGELOG.md`
- `docs/schema.json`
- `docs/adr/0009-feature-focused-bakeoff.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/library-exports.yaml`
- `docs/adr/0001-portable-agent-instructions.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/play.yaml`
- `docs/adr/0007-import-tracking-for-call-resolution.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/akka-http.yaml`
- `docs/governance-case-critiques.md`
- ... and 126 more files

## Source Files Content

------------------- START of packages/hypergumbo-core/tests/test_behavior_map_schema.py 
```
from hypergumbo_core.schema import new_behavior_map, SCHEMA_VERSION


def test_new_behavior_map_has_required_top_level_fields():
    bm = new_behavior_map()

    # Fixed identifiers
    assert bm["schema_version"] == SCHEMA_VERSION
    assert bm["view"] == "behavior_map"
    assert bm["confidence_model"] == "hypergumbo-evidence-v1"
    assert bm["stable_id_scheme"] == "hypergumbo-stableid-v1"
    assert bm["shape_id_scheme"] == "hypergumbo-shapeid-v1"
    assert bm["repo_fingerprint_scheme"] == "hypergumbo-repofp-v1"

    # Basic structure
    assert bm["analysis_incomplete"] is False
    assert isinstance(bm["analysis_runs"], list)
    assert isinstance(bm["profile"], dict)
    assert isinstance(bm["nodes"], list)
    assert isinstance(bm["edges"], list)
    assert isinstance(bm["features"], list)
    assert isinstance(bm["metrics"], dict)
    assert isinstance(bm["limits"], dict)
    assert isinstance(bm["entrypoints"], list)
    assert "generated_at" in bm
```
------------------- END of packages/hypergumbo-core/tests/test_behavior_map_schema.py 

------------------- START of packages/hypergumbo-core/tests/test_main_module.py 
```
"""Tests for __main__.py module (python -m hypergumbo invocation)."""
import runpy
import sys

import pytest


def test_main_module_entry_point(tmp_path, monkeypatch):
    """Test running python -m hypergumbo via runpy."""
    # Set up argv to run a simple command
    out_file = tmp_path / "out.json"
    monkeypatch.setattr(
        sys, "argv", ["hypergumbo", "run", str(tmp_path), "--out", str(out_file)]
    )

    # runpy.run_module will execute __main__.py
    # It raises SystemExit on completion
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("hypergumbo", run_name="__main__")

    assert exc.value.code == 0
    assert out_file.exists()
```
------------------- END of packages/hypergumbo-core/tests/test_main_module.py 

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/linkers/__init__.py 
```
"""Cross-language linkers for hypergumbo.

Linkers create edges between symbols from different language analyzers,
enabling cross-language call graph construction.
"""
```
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/linkers/__init__.py 

------------------- START of packages/hypergumbo/src/hypergumbo/__main__.py 
```
"""Allow running hypergumbo as a module: python -m hypergumbo."""
from hypergumbo_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```
------------------- END of packages/hypergumbo/src/hypergumbo/__main__.py 


## Additional Files Content


[hypergumbo sketch] Generated 5
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/8e45735a2eb68548/hypergumbo.results.16k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/8e45735a2eb68548/hypergumbo.results.4k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/8e45735a2eb68548/hypergumbo.results.64k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/8e45735a2eb68548/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/8e45735a2eb68548/sketch.4000.withsource.md
  Output: stdout
  Embeddings cached: /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/embeddings
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
│  Output: 10037 Symbols + 26739 Edges + UsageContexts            │
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
│  │  - 37 framework YAML files (fastapi, django, etc.)      │    │
│  └─────────────────────────────────────────────────────────┘    │
│  Output: Symbols enriched with meta.concepts                    │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                          LINKERS                                │
│  Cross-language edge creation                                   │
│  Match via meta.concepts (route paths, gRPC services, etc.)     │
│  18 linkers: HTTP, gRPC, GraphQL, WebSocket, IPC, JNI, etc.     │
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

These symbols have the highest in-degree (most referenced by other symbols):

| Symbol | Kind | In-Degree | Location |
|--------|------|-----------|----------|
| `Span` | class | 1487 | ir.py |
| `Symbol` | class | 1366 | ir.py |
| `clear_pattern_cache` | function | 429 | framework_patterns.py |
| `load_framework_patterns` | function | 371 | framework_patterns.py |
| `Edge.create` | method | 283 | ir.py |
| `analyze_javascript` | function | 276 | js_ts.py |
| `run_behavior_map` | function | 272 | cli.py |
| `match_patterns` | function | 270 | framework_patterns.py |
| `AnalysisRun` | class | 270 | ir.py |
| `find_files` | function | 263 | discovery.py |
| `Edge` | class | 234 | ir.py |
| `iter_tree` | function | 217 | base.py |
| `AnalysisRun.create` | method | 170 | ir.py |
| `analyze_java` | function | 162 | java.py |
| `node_text` | function | 152 | base.py |

## Module Reference

### Core

- **`hypergumbo_core.build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`hypergumbo_core.catalog`**: Catalog of available analysis passes.
- **`hypergumbo_core.compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`hypergumbo_core.datamodels`**: Data model detection for code analysis.
- **`hypergumbo_core.discovery`**: File discovery with exclude patterns.
- **`hypergumbo_core.entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`hypergumbo_core.framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-0003 v0.8.x).
- **`hypergumbo_core.ir`**: Internal Representation (IR) for code analysis.
- **`hypergumbo_core.limits`**: Limits tracking for behavior map output.
- **`hypergumbo_core.metrics`**: Metrics computation for behavior map output.
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
- **`hypergumbo_core.user_config`**: User configuration management for hypergumbo.
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

### Analyzers

- **`hypergumbo_core.analyze.all_analyzers`**: Consolidated analyzer registry with plugin discovery for cli.py.
- **`hypergumbo_core.analyze.base`**: Base classes and utilities for language analyzers.
- **`hypergumbo_core.analyze.registry`**: Analyzer registry for dynamic dispatch.

### Linkers

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
- **`hypergumbo_core.linkers.message_queue`**: Message queue linker for detecting pub/sub communication patterns.
- **`hypergumbo_core.linkers.openapi`**: OpenAPI/Swagger linker for detecting API schema to handler connecti...
- **`hypergumbo_core.linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
- **`hypergumbo_core.linkers.registry`**: Linker registry for dynamic dispatch.
- **`hypergumbo_core.linkers.route_handler`**: Route-handler linker for connecting routes to their handler functions.
- **`hypergumbo_core.linkers.subprocess_cli`**: Subprocess-to-CLI linker for detecting cross-process CLI invocations.
- **`hypergumbo_core.linkers.swift_objc`**: Swift/Objective-C bridging linker.
- **`hypergumbo_core.linkers.type_hierarchy`**: Type hierarchy linker for polymorphic dispatch resolution.
- **`hypergumbo_core.linkers.websocket`**: WebSocket linker for detecting WebSocket communication patterns.

### CLI & I/O

- **`hypergumbo_core.__main__`**: (no docstring)
- **`hypergumbo_core.cli`**: Command-line interface for hypergumbo.
- **`hypergumbo_core.schema`**: Schema versioning and behavior map factory.
- **`hypergumbo_core.sketch`**: Token-budgeted Markdown sketch generation.

## Key Abstractions

> **Note:** This section is manually maintained. Update if IR classes change.

### Symbol (`ir.py`)
Represents a code entity (function, class, method, etc.) with:
- `id`: Unique identifier within the analysis
- `name`: Human-readable name
- `kind`: Type of symbol (function, class, method, etc.)
- `path`: File path
- `span`: Location in source (start/end line/column)
- `stable_id`: Cross-run stable identifier
- `supply_chain`: Object with `tier` (1-4), `tier_name`, and `reason`

### Edge (`ir.py`)
Represents a relationship between symbols:
- `src`, `dst`: Source and destination symbol IDs
- `type`: Relationship type (calls, imports, instantiates, etc.)
- `confidence`: 0.0-1.0 confidence score
- `meta.evidence_type`: How the edge was detected

### AnalysisRun (`ir.py`)
Provenance tracking for reproducibility:
- `pass`: Which analyzer produced this data
- `execution_id`: Unique run identifier
- `duration_ms`: Analysis time
- `files_analyzed`: Count of processed files

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
2. Implement `link_<name>(root: Path) -> LinkResult`
3. Match patterns across existing symbols
4. Create cross-language edges
5. Add tests in `packages/hypergumbo-core/tests/test_<name>_linker.py`

## Pattern System Architecture (ADR-0003)

> **Note:** This section is manually maintained. See `docs/adr/0003-architectural-analysis-and-revision-plan.md` for the full design rationale.

ADR-0003 introduced a layered architecture for framework-aware analysis:

```
Source Files
     │
     ▼
┌─────────────────┐
│    Analyzers    │  Extract language-level metadata only
│   (py.py, etc.) │  - Decorators, annotations, base classes
└─────────────────┘  - No framework-specific interpretation
     │
     ▼
┌─────────────────────────┐
│  PATTERN SYSTEM         │  Data-driven symbol enrichment
│  (framework_patterns.py)│  - Definition: decorators, base classes
│                         │  - Usage: UsageContext (v1.1.x)
│                         │  - Output: meta.concepts
│                         │
│  Convention patterns:   │  Always loaded (language-agnostic):
│  - main-functions       │  main() entrypoints
│  - test-frameworks      │  test function detection
│  - language-conventions │  CUDA/WGSL/COBOL/LaTeX/Starlark
│  - config-conventions   │  NPM/Maven/Cargo dependencies
│                         │
│  Framework patterns:    │  Loaded when framework detected:
│  - fastapi, django, etc │  37 framework YAML files
└─────────────────────────┘
     │
     ▼
┌─────────────────┐
│    Linkers      │  Cross-language edge creation
│   (http.py, etc)│  - Match via meta.concepts
└─────────────────┘
```

### Key Components

- **`framework_patterns.py`**: Loads and applies YAML pattern files
- **`frameworks/*.yaml`**: 41 pattern files (4 convention + 37 framework)
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

*Generated by `./scripts/generate-architecture` using hypergumbo self-analysis.*