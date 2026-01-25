# Example: hypergumbo analyzing itself

This is the full terminal output of running `hypergumbo hypergumbo/` (with default 4000 token budget).

---

```bash
(.venv) jgstern_agent@agent-vm-16:~$ hypergumbo ~/hypergumbo
[100%] Complete in 71.9s            ETA 10s
[100%] Complete in 160.7s           TA 1s
# hypergumbo

Two Outputs **Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected). **Behavior map** (`hypergumbo run`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## Overview
Python (91%), Markdown (6%), Yaml (2%)
335 files    (201 non-test + 134 test)
~130,574 LOC (~66,411 non-test + ~64,163 test)

## Structure

` ` `
hypergumbo/
├── .github
│   └── workflows
│       ├── release-mirror.yml
│       └── [and 2 other items]
├── docs
│   ├── hypergumbo-spec.md
│   └── [and 20 other items]
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
└── [and 20 other items]
` ` `

## Frameworks

- openai
- pytest
- pytorch
- transformers

## Tests

135 test files · pytest, unittest

*~92% estimated coverage (1329/1442 functions called by tests)*

## Configuration

` ` `
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
` ` `

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
- ... and 149 more data models

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
- `src/hypergumbo/analyze/py.py`
- `src/hypergumbo/analyze/html.py`
- ... and 195 more files

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
- `_find_name_in_children(node: 'tree_sitter.Node', source: bytes) -> Optional[str]` (function) — Find identifier name in node's children.
- `_make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind…` (function) — Generate location-based ID.

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
- `_extract_config_info(repo_root: Path, max_chars: int=…, mode: ConfigExtractionM…` (function) — Extract key metadata from config files via extractive summarization.
- `_section_header(title: str, exclude_tests: bool=…) -> str` (function) — Generate a section header with optional [IGNORING TESTS] marker.
- `_extract_readme_description_heuristic(readme_path: Path, max_chars: int=…) -> Optional[str]` (function) — Extract description from README using heuristic parsing.

### `src/hypergumbo/analyze/rust.py`
- `analyze_rust(repo_root: Path) -> RustAnalysisResult` (function) — Analyze all Rust files in a repository.
- `_node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text for a tree-sitter node.
- `_find_child_by_field(node: 'tree_sitter.Node', field_name: str) -> Optional['tr…` (function) — Find child by field name.

### `src/hypergumbo/analyze/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.
- `analyze_julia(repo_root: Path) -> JuliaAnalysisResult` (function) — Analyze all Julia files in a repository.

### `src/hypergumbo/analyze/dart.py`
- `analyze_dart(repo_root: Path) -> DartAnalysisResult` (function) — Analyze Dart files in a repository.

### `src/hypergumbo/analyze/elixir.py`
- `analyze_elixir(repo_root: Path) -> ElixirAnalysisResult` (function) — Analyze all Elixir files in a repository.

### `src/hypergumbo/analyze/ruby.py`
- `analyze_ruby(repo_root: Path) -> RubyAnalysisResult` (function) — Analyze all Ruby files in a repository.

### `src/hypergumbo/analyze/php.py`
- `analyze_php(repo_root: Path) -> PhpAnalysisResult` (function) — Analyze all PHP files in a repository.

### `src/hypergumbo/analyze/groovy.py`
- `analyze_groovy(repo_root: Path) -> GroovyAnalysisResult` (function) — Analyze all Groovy files in a repository.

### `src/hypergumbo/analyze/kotlin.py`
- `analyze_kotlin(repo_root: Path) -> KotlinAnalysisResult` (function) — Analyze all Kotlin files in a repository.

### `src/hypergumbo/cli.py`
- `main(argv=…) -> int` (function)
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.
- `_print_output_summary(command: str, artifacts: list[Path] | None=…, stdout_outpu…` (function) — Print consistent output summary at end of command execution.

### `src/hypergumbo/analyze/go.py`
- `analyze_go(repo_root: Path, max_files: int | None=…) -> AnalysisResult` (function) — Analyze all Go files in a repository.

### `src/hypergumbo/analyze/c.py`
- `analyze_c(repo_root: Path) -> CAnalysisResult` (function) — Analyze all C files in a repository.

(... and 1695 more symbols across 108 other files)

The following symbols, for brevity shown only once above, would have appeared multiple times:
- `_node_text` - we omitted 7 appearances of `_node_text`
- `_find_child_by_type` - we omitted 4 appearances

## Additional Files

- `README.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/LANGUAGES.md`
- `docs/GOVERNANCE.md`
- `docs/example-output.md`
- `docs/future/registry-factory-vision.md`
- `docs/LINKERS.md`
- `AGENTS.md`
- `docs/example-output-with-source.md`
- `src/hypergumbo/frameworks/micronaut.yaml`
- `docs/adr/0003-call-patterns-extension.md`
- `docs/MAINTAINER_AGENT_SPEC.md`
- `docs/history/planning-v1.md`
- `pyproject.toml`
- `docs/ARCHITECTURE.md`
- `CHANGELOG.md`
- `docs/schema.json`
- `CONTRIBUTING.md`
- `docs/adr/0005-sketch-budget-allocation.md`
- `docs/USE-CASES.md`
- `docs/adr/0003-usage-context-patterns.md`
- `docs/adr/0004-file-taxonomy.md`
- `docs/adr/0003-architectural-analysis-and-revision-plan.md`
- `docs/RELEASE_SOP.md`
- `ALLOWED_WEBSITES.md`
- `docs/history/capsule-system-v1.md`
- `docs/hypergumbo-spec.md`
- `docs/EXPERIMENTAL_GRAMMARS.md`
- `src/hypergumbo/frameworks/tornado.yaml`
- `docs/CACHE.md`
- `docs/adr/0002-test-dependency-handling.md`
- `docs/history/validation-gates-v1.md`
- `src/hypergumbo/frameworks/hapi.yaml`
- `src/hypergumbo/frameworks/cli-rust.yaml`
- `src/hypergumbo/frameworks/cli.yaml`
- `src/hypergumbo/frameworks/fastify.yaml`
- `src/hypergumbo/frameworks/electron.yaml`
- `src/hypergumbo/frameworks/vapor.yaml`
- `src/hypergumbo/frameworks/nestjs.yaml`
- `src/hypergumbo/frameworks/ktor.yaml`
- `src/hypergumbo/frameworks/sinatra.yaml`
- `src/hypergumbo/frameworks/cli-js.yaml`
- `src/hypergumbo/frameworks/flask.yaml`
- `src/hypergumbo/frameworks/android.yaml`
- `src/hypergumbo/frameworks/express.yaml`
- `docs/CITATIONS.md`
- `src/hypergumbo/frameworks/nextjs.yaml`
- `src/hypergumbo/frameworks/graphql.yaml`
- `src/hypergumbo/frameworks/go-web.yaml`
- `src/hypergumbo/frameworks/rust-web.yaml`
- `src/hypergumbo/frameworks/rails.yaml`
- `src/hypergumbo/frameworks/jax-rs.yaml`
- `src/hypergumbo/frameworks/laravel.yaml`
- `src/hypergumbo/frameworks/slim.yaml`
- `src/hypergumbo/frameworks/phoenix.yaml`
- `src/hypergumbo/frameworks/cli-go.yaml`
- `src/hypergumbo/frameworks/django.yaml`
- `src/hypergumbo/frameworks/plug.yaml`
- `docs/CODE_OF_CONDUCT.md`
- `src/hypergumbo/frameworks/celery.yaml`
- `CLAUDE.md`
- `src/hypergumbo/frameworks/fastapi.yaml`
- `package.json`
- `docs/agents/tool-compatibility.md`
- `GEMINI.md`
- `src/hypergumbo/frameworks/grape.yaml`
- `SECURITY.md`
- `src/hypergumbo/frameworks/cli-ruby.yaml`
- `docs/INTEL_MAC.md`
- `src/hypergumbo/frameworks/koa.yaml`
- `src/hypergumbo/frameworks/spring-boot.yaml`
- `src/hypergumbo/frameworks/aiohttp.yaml`
- `src/hypergumbo/frameworks/graphql-ruby.yaml`
- `src/hypergumbo/frameworks/aspnet.yaml`
- `src/hypergumbo/frameworks/graphql-python.yaml`

                How Representative Is This Sketch?
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section          ┃ 4,000t ┃ 16,000t ┃ 64,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Data Models      │    19% │     85% │    100% │ confidence mass │
│ Source Files     │    57% │    100% │    100% │ symbol mass     │
│ Key Symbols      │    32% │     42% │     45% │ symbol mass     │
│ Additional Files │    39% │     39% │     39% │ symbol mass     │
└──────────────────┴────────┴─────────┴─────────┴─────────────────┘

hypergumbo also created comparison sketches temporarily:
  4x budget (16,000t):  /tmp/hypergumbo_sketch_compare/sketch.16000.md
  16x budget (64,000t): /tmp/hypergumbo_sketch_compare/sketch.64000.md

To preserve them to cache:
  cp /tmp/hypergumbo_sketch_compare/sketch.16000.md /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/sketch.16000.md
  cp /tmp/hypergumbo_sketch_compare/sketch.64000.md /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/sketch.64000.md


[hypergumbo sketch] Generated 5
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/hypergumbo.results.16k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/hypergumbo.results.4k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/hypergumbo.results.64k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/6aa5d8763a3cd294/sketch.4000.md
  Output: stdout
  Embeddings cached: /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/embeddings
(.venv) jgstern_agent@agent-vm-16:~$
```
