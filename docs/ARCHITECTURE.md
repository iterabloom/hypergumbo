# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: 7adfcdc07367
  hypergumbo: 0.6.0
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **105** Python modules (68 analyzers, 14 linkers)
- **1638** symbols (functions, classes, methods)
- **5966** edges (calls, imports, instantiates)

## Sketch (hypergumbo on hypergumbo)

```markdown
# src

## Overview
Python (100%) · 109 files · ~45,031 LOC

## Structure

- `hypergumbo/`

## Domain Vocabulary

*Key terms: symbols, symbol, line, source, sitter, files, edges, find, cover, pragma, edge, extract*

## Source Files

- `hypergumbo/schema.py`
- `hypergumbo/user_config.py`
- `hypergumbo/limits.py`
- `hypergumbo/catalog.py`
- `hypergumbo/ranking.py`
- `hypergumbo/export.py`
- `hypergumbo/sketch.py`
- `hypergumbo/discovery.py`
- `hypergumbo/cli.py`
- `hypergumbo/metrics.py`
- `hypergumbo/compact.py`
- `hypergumbo/slice.py`
- `hypergumbo/entrypoints.py`
- `hypergumbo/build_grammars.py`
- `hypergumbo/__main__.py`
- `hypergumbo/llm_assist.py`
- `hypergumbo/profile.py`
- `hypergumbo/plan.py`
- `hypergumbo/__init__.py`
- `hypergumbo/ir.py`
- `hypergumbo/supply_chain.py`
- `hypergumbo/analyze/haskell.py`
- `hypergumbo/analyze/latex.py`
- `hypergumbo/analyze/fortran.py`
- `hypergumbo/analyze/csharp.py`
- `hypergumbo/analyze/sql.py`
- `hypergumbo/analyze/capnp.py`
- `hypergumbo/analyze/groovy.py`
- `hypergumbo/analyze/registry.py`
- `hypergumbo/analyze/xml_config.py`
- ... and 79 more files

## Entry Points

- `main` (CLI main) — `hypergumbo/cli.py`

## Key Symbols

*★ = centrality ≥ 50% of max*

### `hypergumbo/ir.py`
- `Span` (class) ★ — Source code location with line and column info.
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).

### `hypergumbo/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.

### `hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `hypergumbo/analyze/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.
- `_node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text for a tree-sitter node.

### `hypergumbo/entrypoints.py`
- `Entrypoint` (class) — A detected entrypoint in the codebase.
- `_get_filename(path: str) -> str` (function) — Extract filename from path.

### `hypergumbo/analyze/rust.py`
- `_find_child_by_field(node: 'tree_sitter.Node', field_name: str) -> Optional['tr…` (function) — Find child by field name.

### `hypergumbo/analyze/js_ts.py`
- `_make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind…` (function) — Generate location-based ID.

### `hypergumbo/linkers/registry.py`
- `LinkerResult` (class) — Result from running a linker.

(... and 1499 more symbols across 87 other files)

The following symbols, for brevity shown only once above, would have appeared multiple times:
- `_node_text` - we omitted 8 appearances of `_node_text`
- `_find_child_by_type` - we omitted 5 appearances

## All Files

- `hypergumbo/__init__.py`
- `hypergumbo/__main__.py`
- `hypergumbo/analyze/__init__.py`
- `hypergumbo/analyze/ada.py`
- `hypergumbo/analyze/agda.py`
- `hypergumbo/analyze/all_analyzers.py`
- `hypergumbo/analyze/base.py`
- `hypergumbo/analyze/bash.py`
- `hypergumbo/analyze/c.py`
- `hypergumbo/analyze/capnp.py`
- `hypergumbo/analyze/clojure.py`
- `hypergumbo/analyze/cmake.py`
- `hypergumbo/analyze/cobol.py`
- `hypergumbo/analyze/cpp.py`
- `hypergumbo/analyze/csharp.py`
- `hypergumbo/analyze/css.py`
- `hypergumbo/analyze/cuda.py`
- `hypergumbo/analyze/d_lang.py`
- `hypergumbo/analyze/dart.py`
- `hypergumbo/analyze/dockerfile.py`
- `hypergumbo/analyze/elixir.py`
- `hypergumbo/analyze/elm.py`
- `hypergumbo/analyze/erlang.py`
- `hypergumbo/analyze/fish.py`
- `hypergumbo/analyze/fortran.py`
- `hypergumbo/analyze/fsharp.py`
- `hypergumbo/analyze/gdscript.py`
- `hypergumbo/analyze/glsl.py`
- `hypergumbo/analyze/go.py`
- `hypergumbo/analyze/graphql.py`
- `hypergumbo/analyze/groovy.py`
- `hypergumbo/analyze/haskell.py`
- `hypergumbo/analyze/hcl.py`
- `hypergumbo/analyze/hlsl.py`
- `hypergumbo/analyze/html.py`
- `hypergumbo/analyze/java.py`
- `hypergumbo/analyze/js_ts.py`
- `hypergumbo/analyze/json_config.py`
- `hypergumbo/analyze/julia.py`
- `hypergumbo/analyze/kotlin.py`
- `hypergumbo/analyze/latex.py`
- `hypergumbo/analyze/lean.py`
- `hypergumbo/analyze/lua.py`
- `hypergumbo/analyze/make.py`
- `hypergumbo/analyze/nim.py`
- `hypergumbo/analyze/nix.py`
- `hypergumbo/analyze/objc.py`
- `hypergumbo/analyze/ocaml.py`
- `hypergumbo/analyze/perl.py`
- `hypergumbo/analyze/php.py`
- `hypergumbo/analyze/powershell.py`
- `hypergumbo/analyze/proto.py`
- `hypergumbo/analyze/py.py`
- `hypergumbo/analyze/r_lang.py`
- `hypergumbo/analyze/registry.py`
- `hypergumbo/analyze/ruby.py`
- `hypergumbo/analyze/rust.py`
- `hypergumbo/analyze/scala.py`
- ... and 51 more files
```

## Data Flow

```
Source Files
     │
     ▼
┌─────────────┐     ┌─────────────┐
│  discovery  │────▶│   profile   │  Detect languages, frameworks
└─────────────┘     └─────────────┘
     │                    │
     ▼                    ▼
┌─────────────┐     ┌─────────────┐
│  analyzers  │────▶│     IR      │  1638 Symbols + 5966 Edges
└─────────────┘     └─────────────┘
     │                    │
     ▼                    ▼
┌─────────────┐     ┌─────────────┐
│   linkers   │────▶│   merged    │  Cross-language edges
└─────────────┘     └─────────────┘
                          │
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
| `Symbol` | class | 333 | ir.py |
| `Span` | class | 322 | ir.py |
| `iter_tree` | function | 160 | base.py |
| `find_files` | function | 147 | discovery.py |
| `Edge` | class | 127 | ir.py |
| `node_text` | function | 98 | base.py |
| `AnalysisRun` | class | 92 | ir.py |
| `Pass` | class | 66 | catalog.py |
| `_find_child_by_type` | function | 30 | julia.py |

## Module Reference

### Core

- **`build_grammars`**: Build tree-sitter grammars from source for languages not available ...
- **`catalog`**: Catalog of available analysis passes and packs.
- **`compact`**: Compact output mode with coverage-based truncation and residual sum...
- **`discovery`**: File discovery with exclude patterns.
- **`entrypoints`**: Entrypoint detection heuristics for code analysis.
- **`ir`**: Internal Representation (IR) for code analysis.
- **`limits`**: Limits tracking for behavior map output.
- **`llm_assist`**: LLM-assisted capsule plan generation.
- **`metrics`**: Metrics computation for behavior map output.
- **`profile`**: Repo profile detection - language and framework heuristics.
- **`ranking`**: Symbol and file ranking utilities for hypergumbo output.
- **`selection.filters`**: Path classification and symbol kind filtering for selection.
- **`selection.language_proportional`**: Language-proportional symbol selection utilities.
- **`selection.token_budget`**: Token estimation and budget management for LLM-aware output.
- **`slice`**: Graph slicing for LLM context extraction.
- **`supply_chain`**: Supply chain classification for code analysis.
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
- **`linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
- **`linkers.registry`**: Linker registry for dynamic dispatch.
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

---

*Generated by `./scripts/generate-architecture` using hypergumbo self-analysis.*