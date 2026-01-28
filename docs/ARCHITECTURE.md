# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: 9f2897e11fa0
  hypergumbo: 1.2.0
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **151** Python modules (107 analyzers, 18 linkers)
- **2593** symbols (functions, classes, methods)
- **10695** edges by type:
  - calls: 6222
  - imports: 2590
  - instantiates: 1730
  - uses: 82
  - message_queue: 39
  - event_publishes: 26
  - other: 6

## Sketch (hypergumbo on hypergumbo)

```markdown
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (91%), Markdown (6%), Yaml (2%)
436 files    (266 non-test + 170 test)
~172,600 LOC (~89,108 non-test + ~83,492 test)

## Structure

hypergumbo/
├── .pytest_cache
│   ├── .gitignore
│   └── [and 3 other items]
├── .venv
│   ├── lib
│   │   └── python3.12
│   │       └── site-packages
│   │           ├── coverage
│   │           │   ├── htmlfiles
│   │           │   │   ├── style.scss
│   │           │   │   └── [and 6 other items]
│   │           │   └── [and 47 other items]
│   │           └── [and 335 other items]
│   └── [and 5 other items]
├── docs
│   ├── future
│   │   └── registry-factory-vision.md
│   └── [and 21 other items]
├── node_modules
│   ├── bats
│   │   ├── README.md
│   │   └── [and 8 other items]
│   └── [and 2 other items]
├── scripts
│   ├── compute_probe_embeddings.py
│   ├── hypergumbo_diag.py
│   └── [and 20 other items]
├── src
│   └── hypergumbo
│       ├── ir.py
│       └── [and 28 other items]
├── tests
│   ├── test_sketch.py
│   └── [and 169 other items]
├── README.md
├── package.json
├── pyproject.toml
└── [and 21 other items]

## Frameworks

- pytest
- pytorch
- transformers

## Tests

171 test files · pytest, unittest

*~91% estimated coverage (1937/2130 functions called by tests)*

## Configuration

pyproject.toml: name: hypergumbo; version: 1.2.0; license: { text =
LICENSE: AGPL

--- Additional context (semantic) ---
[docs/schema.json]
  > }, "required": [ "schema_version",
  > "entity", "architecture", "package",
  > "grpc_stub", "grpc_client", "grpc_server",
  > "type": "string", "description": "Programming language" },
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
  > [build-system] requires = ["hatchling>=1.24"]
  > "Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only", ]
  > "tree-sitter>=0.21", "tree-sitter-javascript>=0.21", "tree-sitter-typescript>=0.21",
  > "tree-sitter-ruby>=0.21", "tree-sitter-kotlin>=1.0", "tree-sitter-swift>=0.0.1",

## Entry Points

- `main` (Python main()) — `scripts/hypergumbo_diag.py`
- `main` (Python main()) — `scripts/compute_probe_embeddings.py`
- `hypergumbo` (Python CLI: hypergumbo) — `pyproject.toml`
- `<module:hypergumbo_diag.py>` (Python script (if __name__ == '__main__')) — `scripts/hypergumbo_diag.py`
- `<module:compute_probe_embeddings.py>` (Python script (if __name__ == '__main__')) — `scripts/compute_probe_embeddings.py`
- `main` (Python main()) — `src/hypergumbo/cli.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `src/hypergumbo/__main__.py`

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
- `LookupResult` (Python @dataclass) — `src/hypergumbo/symbol_resolution.py`
- `Entrypoint` (Python @dataclass) — `src/hypergumbo/entrypoints.py`
- `FileClassification` (Python @dataclass) — `src/hypergumbo/supply_chain.py`
- `EventPattern` (Python @dataclass) — `src/hypergumbo/linkers/event_sourcing.py`
- `GrpcPattern` (Python @dataclass) — `src/hypergumbo/linkers/grpc.py`
- `UsageContext` (Python @dataclass) — `src/hypergumbo/ir.py`
- `WebSocketPattern` (Python @dataclass) — `src/hypergumbo/linkers/websocket.py`
- `LinkerActivation` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `AnalysisResult` (Python @dataclass) — `src/hypergumbo/analyze/base.py`
- `DataModel` (Python @dataclass) — `src/hypergumbo/datamodels.py`
- `Limits` (Python @dataclass) — `src/hypergumbo/limits.py`
- `FileAnalysis` (Python @dataclass) — `src/hypergumbo/analyze/base.py`
- `IncludedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `OmittedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `SupplyChainConfig` (Python @dataclass) — `src/hypergumbo/supply_chain.py`
- `CompactConfig` (Python @dataclass) — `src/hypergumbo/compact.py`
- `PhpAnalysisResult` (Python @dataclass) — `src/hypergumbo/analyze/php.py`
- `SketchStats` (Python @dataclass) — `src/hypergumbo/sketch.py`
- `SliceQuery` (Python @dataclass) — `src/hypergumbo/slice.py`
- `GrammarSpec` (Python @dataclass) — `src/hypergumbo/build_grammars.py`
- `RegisteredLinker` (Python @dataclass) — `src/hypergumbo/linkers/registry.py`
- `Catalog` (Python @dataclass) — `src/hypergumbo/catalog.py`
- `Pattern` (Python @dataclass) — `src/hypergumbo/framework_patterns.py`
- `UsagePatternSpec` (Python @dataclass) — `src/hypergumbo/framework_patterns.py`
- ... and 161 more data models

## Source Files

- `src/hypergumbo/schema.py`
- `src/hypergumbo/user_config.py`
- `src/hypergumbo/symbol_resolution.py`
- `src/hypergumbo/limits.py`
- `src/hypergumbo/catalog.py`
- `src/hypergumbo/ranking.py`
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
- `src/hypergumbo/paths.py`
- `src/hypergumbo/profile.py`
- `src/hypergumbo/taxonomy.py`
- `src/hypergumbo/__init__.py`
- `src/hypergumbo/ir.py`
- `src/hypergumbo/supply_chain.py`
- `src/hypergumbo/analyze/haskell.py`
- `src/hypergumbo/analyze/svelte.py`
- `src/hypergumbo/analyze/latex.py`
- `src/hypergumbo/analyze/fortran.py`
- `src/hypergumbo/analyze/csharp.py`
- `src/hypergumbo/analyze/sql.py`
- `src/hypergumbo/analyze/capnp.py`
- `src/hypergumbo/analyze/v_lang.py`
- `src/hypergumbo/analyze/groovy.py`
- ... and 299 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `src/hypergumbo/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `Edge.create(cls, src: str, dst: str, edge_type: str, line: int, origin…` (method)
- `Edge` (class) — A relationship between two symbols (e.g., function calls).

### `src/hypergumbo/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.

### `src/hypergumbo/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).
- `NameResolver.lookup(self, name: str, allow_suffix: bool=…, path_hint: str | No…` (method)

### `src/hypergumbo/analyze/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

### `src/hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `src/hypergumbo/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.

### `src/hypergumbo/analyze/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `src/hypergumbo/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.

### `src/hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `src/hypergumbo/analyze/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.

### `src/hypergumbo/analyze/ruby.py`
- `analyze_ruby(repo_root: Path) -> RubyAnalysisResult` (function) — Analyze all Ruby files in a repository.

### `src/hypergumbo/analyze/php.py`
- `analyze_php(repo_root: Path) -> PhpAnalysisResult` (function) — Analyze all PHP files in a repository.

### `pyproject.toml`
- `build-system` (table)

(... and 2526 more symbols across 155 other files)

## Additional Files

- `README.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/schema.json`
- `docs/adr/0007-import-tracking-for-call-resolution.md`
- `src/hypergumbo/frameworks/play.yaml`
- `docs/governance-case-critiques.md`
- `src/hypergumbo/frameworks/akka-http.yaml`
- `CHANGELOG.md`
- `src/hypergumbo/frameworks/library-exports.yaml`
- `src/hypergumbo/frameworks/micronaut.yaml`
- `src/hypergumbo/frameworks/tornado.yaml`
- `docs/future/registry-factory-vision.md`
- `src/hypergumbo/frameworks/hapi.yaml`
- ... and 74 more files

## Source Files Content

------------------- START of src/hypergumbo/schema.py ------
```
"""Schema versioning and behavior map factory.

This module defines the output schema version and provides a factory
for creating empty behavior map structures with all required fields.

Version Distinction
-------------------
**SCHEMA_VERSION vs Tool Version:**

- **SCHEMA_VERSION** (defined here): The schema documentation version, embedded
  in every JSON output as `schema_version`. It increments for significant changes
  to `docs/schema.json`, which is a **unified schema** containing both behavior map
  output definitions AND framework pattern types for YAML validation. Breaking
  changes to output format bump minor; additions like new type definitions for
  YAML patterns bump patch. Consumers can use this to check compatibility.

- **__version__** (in __init__.py): The tool/package version. This increments
  with every release (new analyzers, bug fixes, performance improvements,
  CLI changes, etc.). It does NOT indicate output format changes.

These versions evolve independently. The tool can have many releases while
the schema stays stable if the output format doesn't change.

How It Works
------------
The behavior map is the primary output format for hypergumbo analysis.
This module defines several versioned schemes:

- **schema_version**: Overall format version (breaking changes increment minor)
- **confidence_model**: How confidence scores are computed
- **stable_id_scheme**: How stable_id hashes are generated
- **shape_id_scheme**: How shape_id (structure) hashes are generated
- **repo_fingerprint_scheme**: How repo state is fingerprinted for caching

new_behavior_map() returns an empty structure with all top-level fields
initialized, ensuring consistent output even for empty analyses.

Why This Design
---------------
- Explicit versioning enables consumers to detect format changes
- Scheme identifiers let consumers know how to interpret computed IDs
- Factory function ensures all required fields are present
- Separating schema from IR keeps output format concerns isolated

Related Files
-------------
This module works with two other components to provide schema infrastructure:

**This file (schema.py)** - Runtime constants and factory
- Defines SCHEMA_VERSION and scheme identifiers
- Provides new_behavior_map() factory for output generation
- Used at runtime when hypergumbo generates JSON output

**scripts/generate-schema** - Documentation generator
- Generates docs/schema.json from Python dataclasses
- Imports SCHEMA_VERSION from here to embed in the JSON Schema
- Run at dev time; pre-commit hooks verify it stays in sync

**docs/schema.json** - Unified formal schema
- Formal JSON Schema for external validation and IDE autocompletion
- Contains BOTH behavior map output definitions AND framework pattern
  types (Pattern, FrameworkPatternDef) for YAML validation
- Auto-generated; do not edit directly
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

SCHEMA_VERSION = "0.2.1"
CONFIDENCE_MODEL = "hypergumbo-evidence-v1"
STABLE_ID_SCHEME = "hypergumbo-stableid-v1"
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v1"
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v1"


def _now_iso_utc() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_behavior_map() -> Dict[str, Any]:
    """
    Construct an empty behavior_map view with all required top-level fields.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "confidence_model": CONFIDENCE_MODEL,
        "stable_id_scheme": STABLE_ID_SCHEME,
        "shape_id_scheme": SHAPE_ID_SCHEME,
        "repo_fingerprint_scheme": REPO_FINGERPRINT_SCHEME,
        "view": "behavior_map",
        "generated_at": _now_iso_utc(),
        "analysis_incomplete": False,
        "analysis_runs": [],
        "profile": {},
        "nodes": [],
        "edges": [],
        "usage_contexts": [],
        "features": [],
        "metrics": {},
        "limits": {},
        "entrypoints": [],
    }
```
------------------- END of src/hypergumbo/schema.py --------


## Additional Files Content


[hypergumbo sketch] Generated 5
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/44016d7b06f1ebae/hypergumbo.results.16k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/44016d7b06f1ebae/hypergumbo.results.4k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/44016d7b06f1ebae/hypergumbo.results.64k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/44016d7b06f1ebae/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/44016d7b06f1ebae/sketch.4000.withsource.md
  Output: stdout
  Embeddings cached: /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/embeddings
```

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
│  Output: 2593 Symbols + 10695 Edges + UsageContexts             │
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
│  17 linkers: HTTP, gRPC, GraphQL, WebSocket, IPC, JNI, etc.     │
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
| `Symbol` | class | 521 | ir.py |
| `Span` | class | 509 | ir.py |
| `iter_tree` | function | 213 | base.py |
| `Edge.create` | method | 206 | ir.py |
| `AnalysisRun` | class | 190 | ir.py |
| `Edge` | class | 181 | ir.py |
| `find_files` | function | 161 | discovery.py |
| `node_text` | function | 147 | base.py |
| `NameResolver` | class | 123 | symbol_resolution.py |

## Module Reference

### Core

- **`build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`catalog`**: Catalog of available analysis passes.
- **`compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`datamodels`**: Data model detection for code analysis.
- **`discovery`**: File discovery with exclude patterns.
- **`entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-0003 v0.8.x).
- **`ir`**: Internal Representation (IR) for code analysis.
- **`limits`**: Limits tracking for behavior map output.
- **`metrics`**: Metrics computation for behavior map output.
- **`paths`**: Centralized path handling utilities for hypergumbo.
- **`profile`**: Repo profile detection - language and framework heuristics.
- **`ranking`**: Symbol and file ranking utilities for hypergumbo output.
- **`selection.filters`**: Path classification and symbol kind filtering for selection.
- **`selection.language_proportional`**: Language-proportional symbol selection utilities.
- **`selection.token_budget`**: Token estimation and budget management for LLM-aware output.
- **`sketch_embeddings`**: Embedding-based utilities for sketch generation.
- **`slice`**: Graph slicing for LLM context extraction.
- **`supply_chain`**: Supply chain classification for code analysis.
- **`symbol_resolution`**: Unified symbol resolution with pluggable matching strategies.
- **`taxonomy`**: File taxonomy classification (ADR-0004).
- **`user_config`**: User configuration management for hypergumbo.

### Analyzers

- **`analyze.ada`**: Ada analysis pass using tree-sitter.
- **`analyze.agda`**: Agda analysis pass using tree-sitter-agda.
- **`analyze.all_analyzers`**: Consolidated analyzer registry for cli.py.
- **`analyze.apex`**: Apex language analyzer.
- **`analyze.astro`**: Astro component analyzer using tree-sitter.
- **`analyze.base`**: Base classes and utilities for language analyzers.
- **`analyze.bash`**: Bash/shell script analyzer using tree-sitter.
- **`analyze.bibtex`**: BibTeX bibliography analyzer using tree-sitter.
- **`analyze.bitbake`**: BitBake analyzer using tree-sitter.
- **`analyze.c`**: C analysis pass using tree-sitter-c.
- **`analyze.capnp`**: Cap'n Proto analysis pass using tree-sitter.
- **`analyze.clojure`**: Clojure analysis pass using tree-sitter.
- **`analyze.cmake`**: CMake analysis pass using tree-sitter-cmake.
- **`analyze.cobol`**: COBOL analyzer using tree-sitter.
- **`analyze.commonlisp`**: Common Lisp analysis pass using tree-sitter.
- **`analyze.cpp`**: C++ analysis pass using tree-sitter-cpp.
- **`analyze.csharp`**: C# analysis pass using tree-sitter-c-sharp.
- **`analyze.css`**: CSS stylesheet analysis using tree-sitter-css.
- **`analyze.cuda`**: CUDA analysis pass using tree-sitter-cuda.
- **`analyze.d_lang`**: D language analysis pass using tree-sitter.
- **`analyze.dart`**: Dart/Flutter analysis pass using tree-sitter.
- **`analyze.dockerfile`**: Dockerfile analysis pass using tree-sitter-dockerfile.
- **`analyze.elixir`**: Elixir analysis pass using tree-sitter-elixir.
- **`analyze.elm`**: Elm analysis pass using tree-sitter.
- **`analyze.erlang`**: Erlang analysis pass using tree-sitter.
- **`analyze.fennel`**: Fennel language analyzer using tree-sitter.
- **`analyze.fish`**: Fish shell analysis pass using tree-sitter.
- **`analyze.fortran`**: Fortran analysis pass using tree-sitter-fortran.
- **`analyze.fsharp`**: F# analysis pass using tree-sitter.
- **`analyze.gdscript`**: GDScript (Godot) analysis pass using tree-sitter.
- **`analyze.gitignore`**: Gitignore file analyzer using tree-sitter.
- **`analyze.gleam`**: Gleam language analyzer using tree-sitter.
- **`analyze.glsl`**: GLSL shader analysis pass using tree-sitter-glsl.
- **`analyze.go`**: Go analysis pass using tree-sitter-go.
- **`analyze.graphql`**: GraphQL schema analysis pass using tree-sitter-graphql.
- **`analyze.groovy`**: Groovy analysis pass using tree-sitter-groovy.
- **`analyze.hack`**: Hack language analyzer.
- **`analyze.haskell`**: Haskell analysis pass using tree-sitter-haskell.
- **`analyze.haxe`**: Haxe language analyzer using tree-sitter.
- **`analyze.hcl`**: HCL/Terraform analyzer using tree-sitter.
- **`analyze.hlsl`**: HLSL (DirectX shader) analysis pass using tree-sitter.
- **`analyze.html`**: HTML script tag analysis pass.
- **`analyze.ini`**: INI configuration file analyzer using tree-sitter.
- **`analyze.janet`**: Janet language analyzer using tree-sitter.
- **`analyze.java`**: Java analysis pass using tree-sitter-java.
- **`analyze.js_ts`**: JavaScript/TypeScript/Svelte analysis pass using tree-sitter.
- **`analyze.json_config`**: JSON configuration analysis pass using tree-sitter-json.
- **`analyze.jsonnet`**: Jsonnet configuration language analyzer.
- **`analyze.julia`**: Julia analysis pass using tree-sitter-julia.
- **`analyze.kdl`**: KDL (KDL Document Language) configuration analyzer using tree-sitter.
- **`analyze.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`analyze.latex`**: LaTeX analyzer using tree-sitter.
- **`analyze.lean`**: Lean 4 analysis pass using tree-sitter-lean.
- **`analyze.llvm_ir`**: LLVM IR analysis pass using tree-sitter.
- **`analyze.lua`**: Lua analysis pass using tree-sitter-lua.
- **`analyze.luau`**: Luau language analyzer.
- **`analyze.make`**: Makefile analysis pass using tree-sitter-make.
- **`analyze.markdown`**: Markdown documentation analyzer using tree-sitter.
- **`analyze.matlab`**: MATLAB language analyzer using tree-sitter.
- **`analyze.meson`**: Meson build system analyzer using tree-sitter.
- **`analyze.nim`**: Nim language analysis pass using tree-sitter.
- **`analyze.nix`**: Nix expression analysis pass using tree-sitter-nix.
- **`analyze.objc`**: Objective-C analyzer using tree-sitter.
- **`analyze.ocaml`**: OCaml analysis pass using tree-sitter-ocaml.
- **`analyze.odin`**: Odin language analyzer using tree-sitter.
- **`analyze.pascal`**: Pascal language analyzer using tree-sitter.
- **`analyze.perl`**: Perl analysis pass using tree-sitter.
- **`analyze.php`**: PHP analysis pass using tree-sitter-php.
- **`analyze.pony`**: Pony language analyzer using tree-sitter.
- **`analyze.powershell`**: PowerShell analysis pass using tree-sitter.
- **`analyze.prisma`**: Prisma schema analyzer using tree-sitter.
- **`analyze.properties`**: Java properties file analyzer using tree-sitter.
- **`analyze.proto`**: Protocol Buffers (Proto) analysis pass using tree-sitter.
- **`analyze.puppet`**: Puppet manifest analyzer using tree-sitter.
- **`analyze.purescript`**: PureScript language analyzer using tree-sitter.
- **`analyze.py`**: Python AST analysis pass.
- **`analyze.r_lang`**: R language analysis pass using tree-sitter.
- **`analyze.racket`**: Racket language analyzer using tree-sitter.
- **`analyze.registry`**: Analyzer registry for dynamic dispatch.
- **`analyze.requirements`**: Python requirements.txt analyzer using tree-sitter.
- **`analyze.robot`**: Robot Framework analyzer using tree-sitter.
- **`analyze.rst`**: reStructuredText analyzer using tree-sitter.
- **`analyze.ruby`**: Ruby analysis pass using tree-sitter-ruby.
- **`analyze.rust`**: Rust analysis pass using tree-sitter-rust.
- **`analyze.scala`**: Scala analysis pass using tree-sitter-scala.
- **`analyze.scheme`**: Scheme language analyzer using tree-sitter.
- **`analyze.scss`**: SCSS/Sass stylesheet analyzer using tree-sitter.
- **`analyze.smithy`**: Smithy API definition language analyzer.
- **`analyze.solidity`**: Solidity analysis pass using tree-sitter-solidity.
- **`analyze.sparql`**: SPARQL query analyzer using tree-sitter.
- **`analyze.sql`**: SQL schema analysis pass using tree-sitter-sql.
- **`analyze.starlark`**: Starlark (Bazel/Buck) analysis pass using tree-sitter.
- **`analyze.svelte`**: Svelte component analyzer using tree-sitter.
- **`analyze.swift`**: Swift analysis pass using tree-sitter-swift.
- **`analyze.tcl`**: Tcl language analyzer using tree-sitter.
- **`analyze.thrift`**: Apache Thrift analysis pass using tree-sitter.
- **`analyze.toml_config`**: TOML configuration file analyzer using tree-sitter-toml.
- **`analyze.twig`**: Twig template analyzer using tree-sitter.
- **`analyze.v_lang`**: V language analyzer using tree-sitter.
- **`analyze.verilog`**: Verilog/SystemVerilog analysis pass using tree-sitter-verilog.
- **`analyze.vhdl`**: VHDL analysis pass using tree-sitter-vhdl.
- **`analyze.vue`**: Vue.js component analyzer using tree-sitter.
- **`analyze.wgsl`**: WGSL (WebGPU Shading Language) analysis pass using tree-sitter-wgsl.
- **`analyze.wolfram`**: Wolfram Language analysis pass using tree-sitter-wolfram.
- **`analyze.xml_config`**: XML configuration analysis pass using tree-sitter-xml.
- **`analyze.yaml_ansible`**: YAML/Ansible analyzer using tree-sitter.
- **`analyze.zig`**: Zig language analyzer using tree-sitter.

### Linkers

- **`linkers.database_query`**: Database query linker for detecting SQL queries in application code.
- **`linkers.dependency`**: Dependency linker for connecting manifest dependencies to code impo...
- **`linkers.event_sourcing`**: Event sourcing linker for detecting event publishers and subscribers.
- **`linkers.graphql`**: GraphQL client-schema linker for detecting cross-file GraphQL calls.
- **`linkers.graphql_resolver`**: GraphQL resolver linker for detecting resolver implementations.
- **`linkers.grpc`**: gRPC/Protobuf linker for detecting RPC communication patterns.
- **`linkers.http`**: HTTP client-server linker for detecting cross-language API calls.
- **`linkers.ipc`**: IPC linker for detecting inter-process communication patterns.
- **`linkers.jni`**: JNI linker for connecting Java native methods to C/C++ implementati...
- **`linkers.message_queue`**: Message queue linker for detecting pub/sub communication patterns.
- **`linkers.openapi`**: OpenAPI/Swagger linker for detecting API schema to handler connecti...
- **`linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
- **`linkers.registry`**: Linker registry for dynamic dispatch.
- **`linkers.route_handler`**: Route-handler linker for connecting routes to their handler functions.
- **`linkers.subprocess_cli`**: Subprocess-to-CLI linker for detecting cross-process CLI invocations.
- **`linkers.swift_objc`**: Swift/Objective-C bridging linker.
- **`linkers.type_hierarchy`**: Type hierarchy linker for polymorphic dispatch resolution.
- **`linkers.websocket`**: WebSocket linker for detecting WebSocket communication patterns.

### CLI & I/O

- **`__main__`**: (no docstring)
- **`cli`**: Command-line interface for hypergumbo.
- **`schema`**: Schema versioning and behavior map factory.
- **`sketch`**: Token-budgeted Markdown sketch generation.

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

1. Create `src/hypergumbo/analyze/<language>.py`
2. Implement `analyze(root: Path) -> AnalysisResult`
3. Return symbols and edges following IR conventions
4. Add tests in `tests/test_<language>_analyzer.py`
5. Register in `catalog.py` if needed

## Adding a New Linker

1. Create `src/hypergumbo/linkers/<name>.py`
2. Implement `link_<name>(root: Path) -> LinkResult`
3. Match patterns across existing symbols
4. Create cross-language edges
5. Add tests in `tests/test_<name>_linker.py`

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
1. Create `src/hypergumbo/frameworks/<framework>.yaml`
2. Add `linkers:` section to enable relevant linkers
3. Add tests in `tests/test_framework_patterns.py`

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