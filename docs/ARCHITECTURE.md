# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: 448ce9dc075d
  hypergumbo: 1.0.0
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **114** Python modules (70 analyzers, 16 linkers)
- **1827** symbols (functions, classes, methods)
- **6624** edges by type:
  - calls: 3301
  - imports: 2022
  - instantiates: 1151
  - uses: 79
  - message_queue: 39
  - event_publishes: 26
  - other: 6

## Sketch (hypergumbo on hypergumbo)

```markdown
# src

## Overview
Python (95%), Yaml (5%)
160 files    (160 non-test +   0 test)
~57,348 LOC (~57,348 non-test + ~     0 test)

## Structure

```
src/
└── hypergumbo
    ├── cli.py
    ├── ir.py
    └── [and 29 other items]
```

## Tests

No test files detected

## Entry Points

- `main` (Python main()) — `hypergumbo/cli.py`

## Data Models

- `Symbol` (Python @dataclass) — `hypergumbo/ir.py`
- `Span` (Python @dataclass) — `hypergumbo/ir.py`
- `Edge` (Python @dataclass) — `hypergumbo/ir.py`
- `AnalysisRun` (Python @dataclass) — `hypergumbo/ir.py`
- `LanguageSpec` (Python @dataclass) — `hypergumbo/taxonomy.py`
- `Pass` (Python @dataclass) — `hypergumbo/catalog.py`
- `LinkerResult` (Python @dataclass) — `hypergumbo/linkers/registry.py`
- `LinkerRequirement` (Python @dataclass) — `hypergumbo/linkers/registry.py`
- `LinkerContext` (Python @dataclass) — `hypergumbo/linkers/registry.py`
- `Entrypoint` (Python @dataclass) — `hypergumbo/entrypoints.py`
- `EventPattern` (Python @dataclass) — `hypergumbo/linkers/event_sourcing.py`
- `FileClassification` (Python @dataclass) — `hypergumbo/supply_chain.py`
- `GrpcPattern` (Python @dataclass) — `hypergumbo/linkers/grpc.py`
- `WebSocketPattern` (Python @dataclass) — `hypergumbo/linkers/websocket.py`
- ... and 168 more data models

## Source Files

- `hypergumbo/schema.py`
- `hypergumbo/user_config.py`
- `hypergumbo/limits.py`
- `hypergumbo/catalog.py`
- `hypergumbo/ranking.py`
- `hypergumbo/export.py`
- `hypergumbo/sketch.py`
- `hypergumbo/discovery.py`
- `hypergumbo/_embedding_data.py`
- `hypergumbo/cli.py`
- `hypergumbo/metrics.py`
- `hypergumbo/compact.py`
- `hypergumbo/framework_patterns.py`
- `hypergumbo/datamodels.py`
- `hypergumbo/slice.py`
- `hypergumbo/entrypoints.py`
- `hypergumbo/build_grammars.py`
- `hypergumbo/__main__.py`
- `hypergumbo/sketch_embeddings.py`
- `hypergumbo/llm_assist.py`
- `hypergumbo/paths.py`
- `hypergumbo/profile.py`
- `hypergumbo/plan.py`
- `hypergumbo/taxonomy.py`
- ... and 95 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `hypergumbo/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.

### `hypergumbo/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) ★ — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.

### `hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `hypergumbo/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.

### `hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `hypergumbo/linkers/registry.py`
- `LinkerResult` (class) — Result from running a linker.

### `hypergumbo/analyze/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.

### `hypergumbo/analyze/rust.py`
- `_node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text for a tree-sitter node.

### `hypergumbo/sketch.py`
- `_section_header(title: str, exclude_tests: bool=…) -> str` (function) — Generate a section header with optional [IGNORING TESTS] marker.

### `hypergumbo/analyze/py.py`
- `_format_annotation(node: ast.expr) -> str` (function) — Format a type annotation node to a readable string.

(... and 1691 more symbols across 98 other files)

The following symbols, for brevity shown only once above, would have appeared multiple times:
- `_node_text` - we omitted 4 appearances of `_node_text`
- `_find_child_by_type` - we omitted 4 appearances

## Additional Files

- `hypergumbo/frameworks/micronaut.yaml`
- `hypergumbo/frameworks/nestjs.yaml`
- `hypergumbo/frameworks/cli-rust.yaml`
- `hypergumbo/frameworks/sinatra.yaml`
- `hypergumbo/frameworks/fastify.yaml`
- `hypergumbo/frameworks/flask.yaml`
- `hypergumbo/frameworks/vapor.yaml`
- `hypergumbo/frameworks/express.yaml`
- `hypergumbo/frameworks/ktor.yaml`
- `hypergumbo/frameworks/hapi.yaml`
- `hypergumbo/frameworks/cli-js.yaml`
- `hypergumbo/frameworks/nextjs.yaml`
- `hypergumbo/frameworks/android.yaml`
- `hypergumbo/frameworks/go-web.yaml`
- `hypergumbo/frameworks/graphql.yaml`
- `hypergumbo/frameworks/laravel.yaml`
- `hypergumbo/frameworks/rust-web.yaml`
- `hypergumbo/frameworks/phoenix.yaml`
- `hypergumbo/frameworks/jax-rs.yaml`
- `hypergumbo/frameworks/django.yaml`
- `hypergumbo/frameworks/slim.yaml`
- `hypergumbo/frameworks/rails.yaml`
- `hypergumbo/frameworks/cli-go.yaml`
- `hypergumbo/frameworks/tornado.yaml`
- `hypergumbo/frameworks/plug.yaml`
- `hypergumbo/frameworks/cli.yaml`
- `hypergumbo/frameworks/main-functions.yaml`
- `hypergumbo/frameworks/electron.yaml`
- `hypergumbo/frameworks/celery.yaml`
- `hypergumbo/frameworks/config-conventions.yaml`
- `hypergumbo/frameworks/fastapi.yaml`
- `hypergumbo/frameworks/spring-boot.yaml`
- `hypergumbo/frameworks/grape.yaml`
- `hypergumbo/frameworks/aiohttp.yaml`
- `hypergumbo/frameworks/cli-ruby.yaml`
- `hypergumbo/frameworks/graphql-ruby.yaml`
- `hypergumbo/frameworks/koa.yaml`
- `hypergumbo/frameworks/test-frameworks.yaml`
- `hypergumbo/frameworks/aspnet.yaml`
- `hypergumbo/frameworks/language-conventions.yaml`
- ... and 1 more files


[hypergumbo sketch] Generated 1, Using 4 cached
  [cached] /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/results/f76508288abfcafb/hypergumbo.results.16k.json
  [cached] /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/results/f76508288abfcafb/hypergumbo.results.4k.json
  [cached] /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/results/f76508288abfcafb/hypergumbo.results.64k.json
  [cached] /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/results/f76508288abfcafb/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/results/f76508288abfcafb/sketch.1500.md
  Output: stdout
  Embeddings cached: /home/jgstern_agent/.cache/hypergumbo/38e77d3fcdc6f084/embeddings
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
│  Output: 1827 Symbols + 6624 Edges + UsageContexts            │
│  Rich metadata: decorators, base_classes, parameters            │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                       PATTERN SYSTEM                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Convention Patterns (always loaded):                    │    │
│  │  - main-functions.yaml: main() entrypoints              │    │
│  │  - test-frameworks.yaml: test function detection        │    │
│  │  - language-conventions.yaml: CUDA/WGSL/COBOL/etc.      │    │
│  │  - config-conventions.yaml: NPM/Maven/Cargo deps        │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Framework Patterns (loaded when framework detected):    │    │
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
| `Symbol` | class | 342 | ir.py |
| `Span` | class | 339 | ir.py |
| `iter_tree` | function | 174 | base.py |
| `find_files` | function | 161 | discovery.py |
| `node_text` | function | 136 | base.py |
| `Edge` | class | 136 | ir.py |
| `AnalysisRun` | class | 96 | ir.py |
| `LanguageSpec` | class | 75 | taxonomy.py |
| `Pass` | class | 66 | catalog.py |

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