# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: fd72350c5707
  hypergumbo: 1.0.0
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **115** Python modules (70 analyzers, 16 linkers)
- **1893** symbols (functions, classes, methods)
- **8218** edges by type:
  - calls: 4712
  - imports: 2085
  - instantiates: 1268
  - uses: 82
  - message_queue: 39
  - event_publishes: 26
  - other: 6

## Sketch (hypergumbo on hypergumbo)

```markdown
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (90%), Markdown (6%), Yaml (2%)
344 files    (208 non-test + 136 test)
~138,746 LOC (~70,571 non-test + ~68,175 test)

## Structure

hypergumbo/
├── .github
│   └── workflows
│       ├── release-mirror.yml
│       └── [and 2 other items]
├── docs
│   ├── hypergumbo-spec.md
│   └── [and 20 other items]
├── scripts
│   ├── compute_probe_embeddings.py
│   └── [and 16 other items]
├── src
│   └── hypergumbo
│       ├── cli.py
│       ├── ir.py
│       └── [and 30 other items]
├── tests
│   ├── test_sketch.py
│   └── [and 135 other items]
├── package.json
├── pyproject.toml
└── [and 20 other items]

## Frameworks

- openai
- pytest
- pytorch
- transformers

## Tests

137 test files · pytest, unittest

*~93% estimated coverage (1401/1509 functions called by tests)*

## Configuration

pyproject.toml: name: hypergumbo; version: 1.0.0; license: { text =
LICENSE: AGPL

--- Additional context (semantic) ---
[docs/schema.json]
  > "type": "string", "description": "Schema version (semver)", "const": "0.2.0"
  > "required": [ "schema_version", "view",
  > "event", "modifier", "library",
  > "websocket_endpoint", "grpc_service", "grpc_servicer",
  > "language": { "type": "string", "description": "Programming language"
  > ], "description": "Fully qualified name" },
  > "first_party", "internal_dep", "external_dep",
  > }, "dst": { "type": "string",
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
  > "tree-sitter>=0.21", "tree-sitter-javascript>=0.21", "tree-sitter-typescript>=0.21",
  > "tree-sitter-ruby>=0.21", "tree-sitter-kotlin>=1.0", "tree-sitter-swift>=0.0.1",

## Entry Points

- `main` (Python main()) — `scripts/compute_probe_embeddings.py`
- `main` (Python main()) — `src/hypergumbo/cli.py`

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
- `IncludedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `LLMResult` (Python @dataclass) — `src/hypergumbo/llm_assist.py`
- `OmittedSummary` (Python @dataclass) — `src/hypergumbo/compact.py`
- `SupplyChainConfig` (Python @dataclass) — `src/hypergumbo/supply_chain.py`
- `PassConfig` (Python @dataclass) — `src/hypergumbo/plan.py`
- `PhpAnalysisResult` (Python @dataclass) — `src/hypergumbo/analyze/php.py`
- `SketchStats` (Python @dataclass) — `src/hypergumbo/sketch.py`
- `Catalog` (Python @dataclass) — `src/hypergumbo/catalog.py`
- `CompactConfig` (Python @dataclass) — `src/hypergumbo/compact.py`
- ... and 151 more data models

## Source Files

- `src/hypergumbo/schema.py`
- `src/hypergumbo/user_config.py`
- `src/hypergumbo/symbol_resolution.py`
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
- `src/hypergumbo/paths.py`
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
- `src/hypergumbo/analyze/sql.py`
- `src/hypergumbo/analyze/capnp.py`
- `src/hypergumbo/analyze/groovy.py`
- `src/hypergumbo/analyze/registry.py`
- `src/hypergumbo/analyze/xml_config.py`
- `src/hypergumbo/analyze/css.py`
- `src/hypergumbo/analyze/proto.py`
- `src/hypergumbo/analyze/powershell.py`
- `src/hypergumbo/analyze/dart.py`
- `src/hypergumbo/analyze/bash.py`
- `src/hypergumbo/analyze/cmake.py`
- `src/hypergumbo/analyze/nix.py`
- `src/hypergumbo/analyze/ada.py`
- `src/hypergumbo/analyze/cuda.py`
- `src/hypergumbo/analyze/solidity.py`
- `src/hypergumbo/analyze/java.py`
- `src/hypergumbo/analyze/scala.py`
- `src/hypergumbo/analyze/llvm_ir.py`
- `src/hypergumbo/analyze/glsl.py`
- `src/hypergumbo/analyze/rust.py`
- `src/hypergumbo/analyze/toml_config.py`
- `src/hypergumbo/analyze/wgsl.py`
- `src/hypergumbo/analyze/r_lang.py`
- `src/hypergumbo/analyze/fish.py`
- `src/hypergumbo/analyze/go.py`
- `src/hypergumbo/analyze/elm.py`
- `src/hypergumbo/analyze/json_config.py`
- ... and 199 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `src/hypergumbo/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
- `Edge.create(cls, src: str, dst: str, edge_type: str, line: int, origin…` (method)
  (... +1 more, top score: 0.38)

### `src/hypergumbo/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.
- `find_child_by_field(node: 'tree_sitter.Node', field_name: str) -> Optional['tr…` (function) — Find a child node by field name.
- `find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find the first child node of a given type.

### `src/hypergumbo/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).
- `NameResolver.lookup(self, name: str, allow_suffix: bool=…, path_hint: str | No…` (method)
- `LookupResult` (class) — Result of a symbol lookup operation.

### `src/hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `src/hypergumbo/analyze/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.
- `_find_name_in_children(node: 'tree_sitter.Node', source: bytes) -> Optional[str]` (function) — Find identifier name in node's children.

### `src/hypergumbo/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.
- `LinkerResult` (class) — Result from running a linker.
- `LinkerRequirement` (class) — A requirement for a linker to produce useful edges.

### `src/hypergumbo/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.
- `is_additional_file_candidate(path: Path) -> bool` (function) — Check if a file is a candidate for Additional Files section.

### `src/hypergumbo/analyze/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `src/hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `src/hypergumbo/sketch.py`
- `ConfigExtractionMode` (class) — Mode for extracting config file content.
- `_extract_config_info(repo_root: Path, max_chars: int=…, mode: ConfigExtractionM…` (function) — Extract key metadata from config files via extractive summarization.
- `_section_header(title: str, exclude_tests: bool=…) -> str` (function) — Generate a section header with optional [IGNORING TESTS] marker.

### `src/hypergumbo/analyze/rust.py`
- `analyze_rust(repo_root: Path) -> RustAnalysisResult` (function) — Analyze all Rust files in a repository.
- `_node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text for a tree-sitter node.
- `_find_child_by_field(node: 'tree_sitter.Node', field_name: str) -> Optional['tr…` (function) — Find child by field name.

### `src/hypergumbo/analyze/dart.py`
- `analyze_dart(repo_root: Path) -> DartAnalysisResult` (function) — Analyze Dart files in a repository.
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.

### `src/hypergumbo/analyze/julia.py`
- `analyze_julia(repo_root: Path) -> JuliaAnalysisResult` (function) — Analyze all Julia files in a repository.

### `src/hypergumbo/analyze/elixir.py`
- `analyze_elixir(repo_root: Path) -> ElixirAnalysisResult` (function) — Analyze all Elixir files in a repository.

### `src/hypergumbo/analyze/ruby.py`
- `analyze_ruby(repo_root: Path) -> RubyAnalysisResult` (function) — Analyze all Ruby files in a repository.

### `src/hypergumbo/analyze/php.py`
- `analyze_php(repo_root: Path) -> PhpAnalysisResult` (function) — Analyze all PHP files in a repository.

### `src/hypergumbo/analyze/kotlin.py`
- `analyze_kotlin(repo_root: Path) -> KotlinAnalysisResult` (function) — Analyze all Kotlin files in a repository.

### `src/hypergumbo/analyze/groovy.py`
- `analyze_groovy(repo_root: Path) -> GroovyAnalysisResult` (function) — Analyze all Groovy files in a repository.

### `src/hypergumbo/cli.py`
- `main(argv=…) -> int` (function)
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.
- `_print_output_summary(command: str, artifacts: list[Path] | None=…, stdout_outpu…` (function) — Print consistent output summary at end of command execution.

(... and 1770 more symbols across 110 other files)

The following symbols, for brevity shown only once above, would have appeared multiple times:
- `_node_text` - we omitted 7 appearances of `_node_text`
- `_find_child_by_type` - we omitted 4 appearances

## Additional Files

- `README.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/LANGUAGES.md`
- `docs/adr/0003-architectural-analysis-and-revision-plan.md`
- `docs/example-output.md`
- `AGENTS.md`
- `docs/LINKERS.md`
- `CONTRIBUTING.md`
- `docs/example-output-with-source.md`
- `src/hypergumbo/frameworks/micronaut.yaml`
- `docs/adr/0003-call-patterns-extension.md`
- `docs/future/registry-factory-vision.md`
- `docs/history/planning-v1.md`
- `docs/GOVERNANCE.md`
- `docs/ARCHITECTURE.md`
- `docs/history/capsule-system-v1.md`
- `docs/schema.json`
- `docs/MAINTAINER_AGENT_SPEC.md`
- `docs/adr/0005-sketch-budget-allocation.md`
- `docs/CACHE.md`
- `docs/adr/0003-usage-context-patterns.md`
- `docs/adr/0006-variable-type-inference.md`
- `docs/USE-CASES.md`
- `docs/history/validation-gates-v1.md`
- `CHANGELOG.md`
- `docs/adr/0004-file-taxonomy.md`
- `ALLOWED_WEBSITES.md`
- `src/hypergumbo/frameworks/cli-rust.yaml`
- `docs/hypergumbo-spec.md`
- `src/hypergumbo/frameworks/fastify.yaml`
- `src/hypergumbo/frameworks/tornado.yaml`
- `docs/adr/0002-test-dependency-handling.md`
- `src/hypergumbo/frameworks/hapi.yaml`
- `src/hypergumbo/frameworks/vapor.yaml`
- `src/hypergumbo/frameworks/cli.yaml`
- `src/hypergumbo/frameworks/ktor.yaml`
- `src/hypergumbo/frameworks/main-functions.yaml`
- `src/hypergumbo/frameworks/cli-js.yaml`
- `src/hypergumbo/frameworks/electron.yaml`
- `pyproject.toml`
- `src/hypergumbo/frameworks/nestjs.yaml`
- `src/hypergumbo/frameworks/android.yaml`
- `docs/RELEASE_SOP.md`
- `docs/CITATIONS.md`
- `src/hypergumbo/frameworks/sinatra.yaml`
- `src/hypergumbo/frameworks/graphql.yaml`
- `src/hypergumbo/frameworks/flask.yaml`
- `src/hypergumbo/frameworks/rust-web.yaml`
- `src/hypergumbo/frameworks/express.yaml`
- `src/hypergumbo/frameworks/go-web.yaml`
- `src/hypergumbo/frameworks/nextjs.yaml`
- `src/hypergumbo/frameworks/jax-rs.yaml`
- `src/hypergumbo/frameworks/rails.yaml`
- `src/hypergumbo/frameworks/slim.yaml`
- `src/hypergumbo/frameworks/laravel.yaml`
- `src/hypergumbo/frameworks/cli-go.yaml`
- `src/hypergumbo/frameworks/phoenix.yaml`
- `src/hypergumbo/frameworks/plug.yaml`
- `src/hypergumbo/frameworks/django.yaml`
- `src/hypergumbo/frameworks/celery.yaml`
- `docs/EXPERIMENTAL_GRAMMARS.md`
- `src/hypergumbo/frameworks/fastapi.yaml`
- `src/hypergumbo/frameworks/language-conventions.yaml`
- `docs/agents/tool-compatibility.md`
- `docs/CODE_OF_CONDUCT.md`
- `src/hypergumbo/frameworks/grape.yaml`
- `CLAUDE.md`
- `src/hypergumbo/frameworks/cli-ruby.yaml`
- `package.json`
- `src/hypergumbo/frameworks/koa.yaml`
- `GEMINI.md`
- `src/hypergumbo/frameworks/aiohttp.yaml`
- `SECURITY.md`
- `src/hypergumbo/frameworks/spring-boot.yaml`
- `docs/INTEL_MAC.md`
- `src/hypergumbo/frameworks/config-conventions.yaml`
- `src/hypergumbo/frameworks/graphql-ruby.yaml`
- `src/hypergumbo/frameworks/test-frameworks.yaml`
- `src/hypergumbo/frameworks/graphql-python.yaml`
- ... and 1 more files


[hypergumbo sketch] Generated 5
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/a2dd49d34c2decec/hypergumbo.results.16k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/a2dd49d34c2decec/hypergumbo.results.4k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/a2dd49d34c2decec/hypergumbo.results.64k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/a2dd49d34c2decec/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/a2dd49d34c2decec/sketch.4000.md
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
│  Output: 1893 Symbols + 8218 Edges + UsageContexts              │
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
│  15 linkers: HTTP, gRPC, GraphQL, WebSocket, IPC, JNI, etc.     │
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
| `Symbol` | class | 343 | ir.py |
| `Span` | class | 339 | ir.py |
| `iter_tree` | function | 188 | base.py |
| `find_files` | function | 161 | discovery.py |
| `Edge.create` | method | 157 | ir.py |
| `node_text` | function | 142 | base.py |
| `Edge` | class | 142 | ir.py |
| `NameResolver` | class | 121 | symbol_resolution.py |
| `AnalysisRun` | class | 96 | ir.py |
| `NameResolver.lookup` | method | 78 | symbol_resolution.py |

## Module Reference

### Core

- **`build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`catalog`**: Catalog of available analysis passes and packs.
- **`compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`datamodels`**: Data model detection for code analysis.
- **`discovery`**: File discovery with exclude patterns.
- **`entrypoints`**: Entrypoint detection for code analysis using YAML-driven pattern ma...
- **`framework_patterns`**: Framework pattern matching for symbol enrichment (ADR-0003 v0.8.x).
- **`ir`**: Internal Representation (IR) for code analysis.
- **`limits`**: Limits tracking for behavior map output.
- **`llm_assist`**: LLM-assisted capsule plan generation.
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
- **`analyze.base`**: Base classes and utilities for language analyzers.
- **`analyze.bash`**: Bash/shell script analyzer using tree-sitter.
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
- **`analyze.fish`**: Fish shell analysis pass using tree-sitter.
- **`analyze.fortran`**: Fortran analysis pass using tree-sitter-fortran.
- **`analyze.fsharp`**: F# analysis pass using tree-sitter.
- **`analyze.gdscript`**: GDScript (Godot) analysis pass using tree-sitter.
- **`analyze.glsl`**: GLSL shader analysis pass using tree-sitter-glsl.
- **`analyze.go`**: Go analysis pass using tree-sitter-go.
- **`analyze.graphql`**: GraphQL schema analysis pass using tree-sitter-graphql.
- **`analyze.groovy`**: Groovy analysis pass using tree-sitter-groovy.
- **`analyze.haskell`**: Haskell analysis pass using tree-sitter-haskell.
- **`analyze.hcl`**: HCL/Terraform analyzer using tree-sitter.
- **`analyze.hlsl`**: HLSL (DirectX shader) analysis pass using tree-sitter.
- **`analyze.html`**: HTML script tag analysis pass.
- **`analyze.java`**: Java analysis pass using tree-sitter-java.
- **`analyze.js_ts`**: JavaScript/TypeScript/Svelte analysis pass using tree-sitter.
- **`analyze.json_config`**: JSON configuration analysis pass using tree-sitter-json.
- **`analyze.julia`**: Julia analysis pass using tree-sitter-julia.
- **`analyze.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`analyze.latex`**: LaTeX analyzer using tree-sitter.
- **`analyze.lean`**: Lean 4 analysis pass using tree-sitter-lean.
- **`analyze.llvm_ir`**: LLVM IR analysis pass using tree-sitter.
- **`analyze.lua`**: Lua analysis pass using tree-sitter-lua.
- **`analyze.make`**: Makefile analysis pass using tree-sitter-make.
- **`analyze.nim`**: Nim language analysis pass using tree-sitter.
- **`analyze.nix`**: Nix expression analysis pass using tree-sitter-nix.
- **`analyze.objc`**: Objective-C analyzer using tree-sitter.
- **`analyze.ocaml`**: OCaml analysis pass using tree-sitter-ocaml.
- **`analyze.perl`**: Perl analysis pass using tree-sitter.
- **`analyze.php`**: PHP analysis pass using tree-sitter-php.
- **`analyze.powershell`**: PowerShell analysis pass using tree-sitter.
- **`analyze.proto`**: Protocol Buffers (Proto) analysis pass using tree-sitter.
- **`analyze.py`**: Python AST analysis pass.
- **`analyze.r_lang`**: R language analysis pass using tree-sitter.
- **`analyze.registry`**: Analyzer registry for dynamic dispatch.
- **`analyze.ruby`**: Ruby analysis pass using tree-sitter-ruby.
- **`analyze.rust`**: Rust analysis pass using tree-sitter-rust.
- **`analyze.scala`**: Scala analysis pass using tree-sitter-scala.
- **`analyze.solidity`**: Solidity analysis pass using tree-sitter-solidity.
- **`analyze.sql`**: SQL schema analysis pass using tree-sitter-sql.
- **`analyze.starlark`**: Starlark (Bazel/Buck) analysis pass using tree-sitter.
- **`analyze.swift`**: Swift analysis pass using tree-sitter-swift.
- **`analyze.thrift`**: Apache Thrift analysis pass using tree-sitter.
- **`analyze.toml_config`**: TOML configuration file analyzer using tree-sitter-toml.
- **`analyze.verilog`**: Verilog/SystemVerilog analysis pass using tree-sitter-verilog.
- **`analyze.vhdl`**: VHDL analysis pass using tree-sitter-vhdl.
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
- **`linkers.subprocess_cli`**: Subprocess-to-CLI linker for detecting cross-process CLI invocations.
- **`linkers.swift_objc`**: Swift/Objective-C bridging linker.
- **`linkers.websocket`**: WebSocket linker for detecting WebSocket communication patterns.

### CLI & I/O

- **`__main__`**: (no docstring)
- **`cli`**: Command-line interface for hypergumbo.
- **`export`**: Export capsule functionality for sharing analyzer configurations.
- **`plan`**: Capsule plan generation and validation.
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