# Architecture

> **Auto-generated** by running hypergumbo on itself.
> Run `./scripts/generate-architecture` to update.

<!--
GENERATION METADATA (for drift detection):
  commit: f19b6eb83f82
  hypergumbo: 0.5.0
  python: 3.12.3
-->

## Self-Analysis Summary

hypergumbo analyzed its own source code and found:
- **72** Python modules (43 analyzers, 13 linkers)
- **954** symbols (functions, classes, methods)
- **3631** edges (calls, imports, instantiates)

## Sketch (hypergumbo on hypergumbo)

```markdown
# src

## Overview
Python (100%) · 75 files · ~28,436 LOC

## Structure

- `hypergumbo/`

## Source Files

- `hypergumbo/schema.py`
- `hypergumbo/limits.py`
- `hypergumbo/catalog.py`
- `hypergumbo/export.py`
- `hypergumbo/sketch.py`
- `hypergumbo/discovery.py`
- `hypergumbo/cli.py`
- `hypergumbo/metrics.py`
- `hypergumbo/slice.py`
- `hypergumbo/entrypoints.py`
- `hypergumbo/__main__.py`
- `hypergumbo/llm_assist.py`
- `hypergumbo/profile.py`
- `hypergumbo/plan.py`
- `hypergumbo/__init__.py`
- `hypergumbo/ir.py`
- `hypergumbo/supply_chain.py`
- `hypergumbo/analyze/haskell.py`
- `hypergumbo/analyze/fortran.py`
- `hypergumbo/analyze/csharp.py`
- `hypergumbo/analyze/sql.py`
- `hypergumbo/analyze/groovy.py`
- `hypergumbo/analyze/xml_config.py`
- `hypergumbo/analyze/css.py`
- `hypergumbo/analyze/bash.py`
- `hypergumbo/analyze/cmake.py`
- `hypergumbo/analyze/nix.py`
- `hypergumbo/analyze/cuda.py`
- `hypergumbo/analyze/solidity.py`
- `hypergumbo/analyze/java.py`
- ... and 45 more files

## Entry Points

- `main` (CLI main) — `hypergumbo/cli.py`

## Key Symbols

*★ = centrality ≥ 50% of max*

### `hypergumbo/ir.py`
- `Span` (class) ★
- `Symbol` (class) ★
- `Edge` (class)
- `_compute_edge_key` (function)
- `_compute_run_signature` (function)
  *… +2 more (top score: 0.01)*

### `hypergumbo/entrypoints.py`
- `Entrypoint` (class)
- `_get_filename` (function)
- `detect_entrypoints` (function)
- `_get_decorators` (function)
- `_detect_aiohttp_views` (function)
  *… +3 more (top score: 0.01)*

### `hypergumbo/discovery.py`
- `find_files` (function)
- `is_excluded` (function)

### `hypergumbo/analyze/rust.py`
- `_node_text` (function)
- `_find_child_by_field` (function)
- `_find_child_by_type` (function)
- `_make_symbol_id` (function)
- `RustAnalysisResult` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/js_ts.py`
- `_node_text` (function)
- `_make_symbol_id` (function)
- `_find_name_in_children` (function)
- `_extract_symbols` (function)
- `_extract_edges` (function)
  *… +6 more (top score: 0.04)*

### `hypergumbo/analyze/julia.py`
- `_find_child_by_type` (function)
- `_node_text` (function)
- `_make_symbol_id` (function)
- `JuliaAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/catalog.py`
- `Pass` (class)
- `Pack` (class)
- `get_default_catalog` (function)
- `is_available` (function)
- `Catalog` (class)

### `hypergumbo/analyze/cpp.py`
- `_find_child_by_type` (function)
- `_node_text` (function)
- `_make_symbol_id` (function)
- `CppAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/go.py`
- `_node_text` (function)
- `_find_child_by_field` (function)
- `_make_symbol_id` (function)
- `GoAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/zig.py`
- `_find_child_by_type` (function)
- `_node_text` (function)
- `ZigAnalysisResult` (class)
- `_make_symbol_id` (function)
- `_get_function_name` (function)
  *… +4 more (top score: 0.02)*

### `hypergumbo/analyze/groovy.py`
- `_find_child_by_type` (function)
- `_node_text` (function)
- `_make_symbol_id` (function)
- `GroovyAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.01)*

### `hypergumbo/analyze/java.py`
- `_node_text` (function)
- `_get_class_name` (function)
- `_make_symbol_id` (function)
- `JavaAnalysisResult` (class)
- `_get_method_name` (function)
  *… +5 more (top score: 0.02)*

### `hypergumbo/analyze/sql.py`
- `_node_text` (function)
- `_find_child_by_type` (function)
- `_make_symbol_id` (function)
- `SQLAnalysisResult` (class)
- `_extract_table_name` (function)
  *… +4 more (top score: 0.02)*

### `hypergumbo/analyze/csharp.py`
- `_node_text` (function)
- `_find_child_by_type` (function)
- `_make_symbol_id` (function)
- `CSharpAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/ruby.py`
- `_node_text` (function)
- `_find_child_by_field` (function)
- `_make_symbol_id` (function)
- `RubyAnalysisResult` (class)
- `_find_child_by_type` (function)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/hcl.py`
- `_find_child_by_type` (function)
- `_make_symbol_id` (function)
- `_node_text` (function)
- `HCLAnalysisResult` (class)
- `_extract_block_info` (function)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/json_config.py`
- `_find_object_key` (function)
- `_get_string_content` (function)
- `_make_symbol_id` (function)
- `_process_dependencies` (function)
- `JSONAnalysisResult` (class)
  *… +4 more (top score: 0.03)*

### `hypergumbo/analyze/css.py`
- `_get_node_text` (function)
- `_make_symbol_id` (function)
- `_process_css_node` (function)
- `CSSAnalysisResult` (class)
- `_extract_font_family` (function)
  *… +3 more (top score: 0.01)*

### `hypergumbo/analyze/scala.py`
- `_find_child_by_type` (function)
- `_node_text` (function)
- `_make_symbol_id` (function)
- `ScalaAnalysisResult` (class)
- `FileAnalysis` (class)
  *… +3 more (top score: 0.02)*

### `hypergumbo/analyze/php.py`
- `_node_text` (function)
- `_find_name_in_children` (function)
- `_make_symbol_id` (function)
- `PhpAnalysisResult` (class)
- `_extract_edges` (function)
  *… +3 more (top score: 0.02)*

### `hypergumbo/cli.py`
- `_classify_symbols` (function)
- `_compute_supply_chain_summary` (function)
- `_edge_from_dict` (function)
- `_node_from_dict` (function)
- `build_parser` (function)
  *… +1 more (top score: 0.01)*

*… and 711 more symbols across 50 other files*
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
│  analyzers  │────▶│     IR      │  954 Symbols + 3631 Edges
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
| `Symbol` | class | 252 | ir.py |
| `Span` | class | 247 | ir.py |
| `find_files` | function | 104 | discovery.py |
| `Edge` | class | 80 | ir.py |
| `AnalysisRun` | class | 56 | ir.py |
| `Pass` | class | 30 | catalog.py |
| `Entrypoint` | class | 29 | entrypoints.py |
| `_get_filename` | function | 24 | entrypoints.py |

## Module Reference

### Core

- **`catalog`**: Catalog of available analysis passes and packs.
- **`discovery`**: File discovery with exclude patterns.
- **`entrypoints`**: Entrypoint detection heuristics for code analysis.
- **`ir`**: Internal Representation (IR) for code analysis.
- **`limits`**: Limits tracking for behavior map output.
- **`llm_assist`**: LLM-assisted capsule plan generation.
- **`metrics`**: Metrics computation for behavior map output.
- **`profile`**: Repo profile detection - language and framework heuristics.
- **`slice`**: Graph slicing for LLM context extraction.
- **`supply_chain`**: Supply chain classification for code analysis.

### Analyzers

- **`analyze.bash`**: Bash/shell script analyzer using tree-sitter.
- **`analyze.c`**: C analysis pass using tree-sitter-c.
- **`analyze.cmake`**: CMake analysis pass using tree-sitter-cmake.
- **`analyze.cpp`**: C++ analysis pass using tree-sitter-cpp.
- **`analyze.csharp`**: C# analysis pass using tree-sitter-c-sharp.
- **`analyze.css`**: CSS stylesheet analysis using tree-sitter-css.
- **`analyze.cuda`**: CUDA analysis pass using tree-sitter-cuda.
- **`analyze.dockerfile`**: Dockerfile analysis pass using tree-sitter-dockerfile.
- **`analyze.elixir`**: Elixir analysis pass using tree-sitter-elixir.
- **`analyze.fortran`**: Fortran analysis pass using tree-sitter-fortran.
- **`analyze.glsl`**: GLSL shader analysis pass using tree-sitter-glsl.
- **`analyze.go`**: Go analysis pass using tree-sitter-go.
- **`analyze.graphql`**: GraphQL schema analysis pass using tree-sitter-graphql.
- **`analyze.groovy`**: Groovy analysis pass using tree-sitter-groovy.
- **`analyze.haskell`**: Haskell analysis pass using tree-sitter-haskell.
- **`analyze.hcl`**: HCL/Terraform analyzer using tree-sitter.
- **`analyze.html`**: HTML script tag analysis pass.
- **`analyze.java`**: Java analysis pass using tree-sitter-java.
- **`analyze.js_ts`**: JavaScript/TypeScript/Svelte analysis pass using tree-sitter.
- **`analyze.json_config`**: JSON configuration analysis pass using tree-sitter-json.
- **`analyze.julia`**: Julia analysis pass using tree-sitter-julia.
- **`analyze.kotlin`**: Kotlin analysis pass using tree-sitter-kotlin.
- **`analyze.lua`**: Lua analysis pass using tree-sitter-lua.
- **`analyze.make`**: Makefile analysis pass using tree-sitter-make.
- **`analyze.nix`**: Nix expression analysis pass using tree-sitter-nix.
- **`analyze.objc`**: Objective-C analyzer using tree-sitter.
- **`analyze.ocaml`**: OCaml analysis pass using tree-sitter-ocaml.
- **`analyze.php`**: PHP analysis pass using tree-sitter-php.
- **`analyze.py`**: Python AST analysis pass.
- **`analyze.r_lang`**: R language analysis pass using tree-sitter.
- **`analyze.ruby`**: Ruby analysis pass using tree-sitter-ruby.
- **`analyze.rust`**: Rust analysis pass using tree-sitter-rust.
- **`analyze.scala`**: Scala analysis pass using tree-sitter-scala.
- **`analyze.solidity`**: Solidity analysis pass using tree-sitter-solidity.
- **`analyze.sql`**: SQL schema analysis pass using tree-sitter-sql.
- **`analyze.swift`**: Swift analysis pass using tree-sitter-swift.
- **`analyze.toml_config`**: TOML configuration file analyzer using tree-sitter-toml.
- **`analyze.verilog`**: Verilog/SystemVerilog analysis pass using tree-sitter-verilog.
- **`analyze.vhdl`**: VHDL analysis pass using tree-sitter-vhdl.
- **`analyze.wgsl`**: WGSL (WebGPU Shading Language) analysis pass using tree-sitter-wgsl.
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
- **`linkers.jni`**: JNI linker for connecting Java native methods to C implementations.
- **`linkers.message_queue`**: Message queue linker for detecting pub/sub communication patterns.
- **`linkers.phoenix_ipc`**: Phoenix Channels IPC linker for detecting Elixir IPC patterns.
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