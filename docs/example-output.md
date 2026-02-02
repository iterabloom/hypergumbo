# Example: hypergumbo analyzing itself (run on Google Colab Terminal)

```
/content# pip install hypergumbo
Collecting hypergumbo
  Downloading hypergumbo-2.0.2-py3-none-any.whl.metadata (9.9 kB)
Collecting hypergumbo-core==2.0.2 (from hypergumbo)
  Downloading hypergumbo_core-2.0.2-py3-none-any.whl.metadata (2.2 kB)
```

\[...takes ~ 1 minute; bunch of log messages omitted for brevity...\]

```
Successfully installed hypergumbo-2.0.2 hypergumbo-core-2.0.2 hypergumbo-lang-common-2.0.2 hypergumbo-lang-extended1-2.0.2 hypergumbo-lang-mainstream-2.0.2 tree-sitter-0.25.2 tree-sitter-agda-1.3.3 tree-sitter-bash-0.25.1 tree-sitter-c-0.24.1 tree-sitter-c-sharp-0.23.1 tree-sitter-cmake-0.7.2.post1 tree-sitter-commonlisp-0.4.1 tree-sitter-cpp-0.23.4 tree-sitter-css-0.25.0 tree-sitter-cuda-0.21.1 tree-sitter-dockerfile-0.2.0 tree-sitter-embedded-template-0.25.0 tree-sitter-fortran-0.5.1 tree-sitter-glsl-0.2.0 tree-sitter-go-0.25.0 tree-sitter-graphql-0.1.0 tree-sitter-groovy-0.1.2 tree-sitter-haskell-0.23.1 tree-sitter-hcl-1.2.0 tree-sitter-html-0.23.2 tree-sitter-java-0.23.5 tree-sitter-javascript-0.25.0 tree-sitter-json-0.24.8 tree-sitter-julia-0.23.1 tree-sitter-kotlin-1.1.0 tree-sitter-language-pack-0.13.0 tree-sitter-llvm-1.1.0 tree-sitter-lua-0.4.1 tree-sitter-make-1.1.1 tree-sitter-nix-0.1.0 tree-sitter-objc-3.0.2 tree-sitter-ocaml-0.24.2 tree-sitter-odin-1.3.0 tree-sitter-php-0.24.1 tree-sitter-robot-1.1.2 tree-sitter-ruby-0.23.1 tree-sitter-rust-0.24.0 tree-sitter-scala-0.24.0 tree-sitter-solidity-1.2.13 tree-sitter-sql-0.3.11 tree-sitter-swift-0.0.1 tree-sitter-toml-0.7.0 tree-sitter-typescript-0.23.2 tree-sitter-verilog-1.0.3 tree-sitter-vhdl-1.3.1 tree-sitter-xml-0.7.0 tree-sitter-yaml-0.7.2 tree-sitter-zig-1.1.2
/content# 
/content# hypergumbo build-grammars
```

\[above `hypergumbo build-grammars` step is only necessary if your repos of interest might contain Lean 4 or Wolfram Language\]

```
Building tree-sitter grammars from source...
Build directory: /tmp/ts-grammar-build
```

\[...takes ~ 1 minute; bunch of log messages omitted for brevity...\]

```
Verifying installation...
tree-sitter-lean: <capsule object "tree_sitter.Language" at 0x78ddccc4bc60>
tree-sitter-wolfram: <capsule object "tree_sitter.Language" at 0x7e6d52bd7d20>
/content# 
/content# git clone https://codeberg.org/iterabloom/hypergumbo
Cloning into 'hypergumbo'...
remote: Enumerating objects: 9176, done.
remote: Counting objects: 100% (9176/9176), done.
remote: Compressing objects: 100% (4369/4369), done.
remote: Total 9176 (delta 6141), reused 6367 (delta 4039), pack-reused 0 (from 0)
Receiving objects: 100% (9176/9176), 5.18 MiB | 4.83 MiB/s, done.
Resolving deltas: 100% (6141/6141), done.
/content# 
/content# hypergumbo hypergumbo/
[ 80%] Pre-computing sketch data... ETA 9s    2026-02-02 00:20:11.397001: I external/local_xla/xla/tsl/cuda/cudart_stub.cc:32] Could not find cuda drivers on your machine, GPU will not be used.
2026-02-02 00:20:11.404734: I external/local_xla/xla/tsl/cuda/cudart_stub.cc:32] Could not find cuda drivers on your machine, GPU will not be used.
```

\[...takes ~ 6.5 minutes; harmless CUDA library conflict messages omitted for brevity...\]

```
[100%] Complete in 371.8s            ETA 2s      
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (88%), Markdown (6%), Yaml (4%)
495 files    (321 non-test + 174 test)
~186,506 LOC (~96,384 non-test + ~90,122 test)

## Structure

` ` `
hypergumbo/
├── .agent
│   ├── stop_reflect.md
│   └── [and 3 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 7 other items]
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
│   │   │   └── [and 63 other items]
│   │   ├── pyproject.toml
│   │   └── [and 1 other items]
│   └── [and 3 other items]
├── scripts
│   ├── hypergumbo_diag.py
│   └── [and 24 other items]
├── .gitignore
├── ALLOWED_WEBSITES.md
├── README.md
├── conftest.py
├── pyproject.toml
└── [and 18 other items]
` ` `

## Frameworks

- pytest
- transformers

## Tests

174 test files · pytest, unittest

*~90% estimated coverage (1960/2179 functions called by tests)*

## Configuration

` ` `
LICENSE: AGPL

--- Additional context (semantic) ---

[pyproject.toml]
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml # This file provides pytest, ruff, bandit, and coverage configuration
  > # Packages are defined in packages/*/pyproject.toml # This file provides pytest, ruff, bandit, and coverage configuration
  > [tool.pytest.ini_options] # Filter expected warnings from tests
  > [tool.pytest.ini_options] # Filter expected warnings from tests filterwarnings = [
  > filterwarnings = [ # tree-sitter unavailability in fallback tests "ignore:tree-sitter-.*not available:UserWarning",
  > # tree-sitter unavailability in fallback tests "ignore:tree-sitter-.*not available:UserWarning", "ignore:.*analysis skipped.*requires tree-sitter:UserWarning",
  > "ignore:.*analysis skipped.*requires tree-sitter:UserWarning", # tree-sitter deprecation warnings (older grammar packages) "ignore:int argument support is deprecated:DeprecationWarning",
  > # tree-sitter deprecation warnings (older grammar packages) "ignore:int argument support is deprecated:DeprecationWarning", ]
  > [tool.coverage.run] # Omit optional modules that require extra dependencies
  > [tool.coverage.run] # Omit optional modules that require extra dependencies omit = [
` ` `

## Entry Points

- `hypergumbo` (Python CLI: hypergumbo) — `packages/hypergumbo/pyproject.toml`
- `hypergumbo` (Python CLI: hypergumbo) — `packages/hypergumbo-core/pyproject.toml`
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo/src/hypergumbo/__main__.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo-core/src/hypergumbo_core/__main__.py`
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
- `Pattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `UsagePatternSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `FrameworkPatternDef` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`
- `DatabaseQueryPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/database_query.py`
- `HttpClientCall` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`
- `ResolverPattern` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/graphql_resolver.py`
- `CompactResult` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `CAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py`
- `ElixirAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py`
- `KotlinAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py`
- `RubyAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py`
- `RustAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py`
- `ScalaAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py`
- `SwiftAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py`
- `LanguageStats` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `AnsibleAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/yaml_ansible.py`
- `BashAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py`
- `CMakeAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cmake.py`
- `CudaAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/cuda.py`
- `DockerfileAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/dockerfile.py`
- `FortranAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/fortran.py`
- `FrameworkSpec` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `GLSLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/glsl.py`
- `GraphQLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/graphql.py`
- `HCLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/hcl.py`
- `HaxeAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/haxe.py`
- `JSONAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/json_config.py`
- `MakeAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/make.py`
- `MesonAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/meson.py`
- `NixAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/nix.py`
- `ObjCAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/objc.py`
- `PascalAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/pascal.py`
- `PureScriptAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/purescript.py`
- `RAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/r_lang.py`
- `SQLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/sql.py`
- `SubprocessCall` (Python @dataclass) — `packages/hypergumbo-core/src/hypergumbo_core/linkers/subprocess_cli.py`
- `VHDLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/vhdl.py`
- `VerilogAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/verilog.py`
- `WGSLAnalysisResult` (Python @dataclass) — `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/wgsl.py`
- `XMLAnalysisResult` (Python @dataclass) —
- `packages/hypergumbo-core/src/hypergumbo_core/limits.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/message_queue.py`
- `packages/hypergumbo-core/src/hypergumbo_core/slice.py`
- `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `packages/hypergumbo-core/tests/test_message_queue_linker.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py`
- `packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`
- `packages/hypergumbo-core/src/hypergumbo_core/user_config.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ini.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/jni.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/gitignore.py`
- `packages/hypergumbo-core/src/hypergumbo_core/ranking.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py`
- `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py`
- `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/properties.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py`
- `packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py`
- `packages/hypergumbo-core/src/hypergumbo_core/selection/language_proportional.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/ipc.py`
- `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/julia.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/smithy.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/objc.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/apex.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/kdl.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/dart.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/hcl.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/database_query.py`
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/meson.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/bibtex.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/gleam.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/ada.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/solidity.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/v_lang.py`
- `packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/haskell.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/scheme.py`
- `packages/hypergumbo-core/src/hypergumbo_core/build_grammars.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/agda.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/racket.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/yaml_ansible.py`
- ... and 279 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `Edge.create(cls, src: str, dst: str, edge_type: str, line: int, origin…` (method)
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
  (... +1 more, top score: 0.27)

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).
- `NameResolver.lookup(self, name: str, allow_suffix: bool=…, path_hint: str | No…` (method)

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.
- `LinkerResult` (class) — Result from running a linker.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py`
- `AnalyzerSpec` (class) — Specification for an analyzer in the registry.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.

### `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `ConfigExtractionMode` (class) — Mode for extracting config file content.
- `_extract_config_info(repo_root: Path, max_chars: int=…, mode: ConfigExtractionM…` (function) — Extract key metadata from config files via extractive summarization.

### `packages/hypergumbo-core/src/hypergumbo_core/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py`
- `analyze_rust(repo_root: Path) -> RustAnalysisResult` (function) — Analyze all Rust files in a repository.
- `_node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text for a tree-sitter node.

### `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py`
- `analyze_php(repo_root: Path) -> PhpAnalysisResult` (function) — Analyze all PHP files in a repository.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py`
- `analyze_ruby(repo_root: Path) -> RubyAnalysisResult` (function) — Analyze all Ruby files in a repository.

### `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/dart.py`
- `analyze_dart(repo_root: Path) -> DartAnalysisResult` (function) — Analyze Dart files in a repository.

### `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py`
- `analyze_elixir(repo_root: Path) -> ElixirAnalysisResult` (function) — Analyze all Elixir files in a repository.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py`
- `analyze_groovy(repo_root: Path) -> GroovyAnalysisResult` (function) — Analyze all Groovy files in a repository.

### `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py`
- `analyze_kotlin(repo_root: Path) -> KotlinAnalysisResult` (function) — Analyze all Kotlin files in a repository.

### `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `main(argv=…
- `docs/schema.json`
- `docs/history/capsule-system-v1.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/library-exports.yaml`
- `docs/GOVERNANCE.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/play.yaml`
- `docs/adr/0004-file-taxonomy.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/akka-http.yaml`
- `docs/future/registry-factory-vision.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/scalatra.yaml`
- `CHANGELOG.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/main-functions.yaml`
- `AGENTS.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/electron.yaml`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/micronaut.yaml`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/tornado.yaml`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/remix.yaml`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/hapi.yaml`
- `packages/hypergumbo/README.md`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/cli.yaml`
- `packages/hypergumbo-core/src/hypergumbo_core/frameworks/quarkus.yaml`
- ... and 113 more files

## Source Files Content

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/discovery.py 
` ` `
"""File discovery with exclude patterns."""
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

# Default exclude patterns (gitignore-style)
DEFAUL

def find_files(
    repo_root: Path,
    patterns: list[str],
    excludes: list[str] | None = None,
    max_files: int | None = None,
) -> Iterator[Path]:
    """Find files matching patterns while respecting exclude rules.

    Args:
        repo_root: The repository root to search from
        patterns: List of glob patterns to match (e.g., ["*.py", "*.pyi"])
        excludes: List of exclude patterns (default: DEFAULT_EXCLUDES)
        max_files: Maximum number of files to return (None = unlimited)

    Yields:
        Paths to files matching the patterns that are not excluded.
    """
    count = 0
    for pattern in patterns:
        for path in repo_root.rglob(pattern):
            if max_files is not None and count >= max_files:
                return
            if not is_excluded(path, repo_root, excludes):
                yield path
                count += 1
` ` `
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/discovery.py 

------------------- START of packages/hypergumbo/src/hypergumbo/__main__.py 
` ` `
"""Allow running hypergumbo as a module: python -m hypergumbo."""
from hypergumbo_core.cli im
------------------- START of README.md ---------------------
` ` `
# hypergumbo

[![CI](https://codeberg.org/iterabloom/hypergumbo/badges/workflows/ci.yml/badge.svg?branch=dev)](https://codeberg.org/iterabloom/hypergumbo/actions)
[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![Coverage](https://img.shields.io/endpoint?url=https://codeberg.org/iterabloom/hypergumbo/raw/branch/badges/coverage.json)](https://codeberg.org/iterabloom/hypergumbo)

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase.

` ` `bash
pip install hypergumbo
` ` `

> Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

` ` `bash
git clone https://codeberg.org/iterabloom/hypergumbo
hypergumbo hypergumbo/
` ` `

Output:

` ` `bash
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

[...truncated...]
` ` `
------------------- END of README.md -----------------------

                     How Representative Is This Sketch?                     
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 8,000t ┃ 32,000t ┃ 128,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Entry Points             │   100% │    100% │     100% │ confidence mass │
│ Data Models              │    38% │    100% │     100% │ confidence mass │
│ Source Files             │    64% │    100% │     100% │ symbol mass     │
│ Key Symbols              │    28% │     38% │      42% │ symbol mass     │
│ Additional Files         │    18% │     48% │      48% │ symbol mass     │
│ Source Files Content     │   1.3% │     22% │      39% │ symbol mass     │
│ Additional Files Content │    14% │     16% │      18% │ symbol mass     │
└──────────────────────────┴────────┴─────────┴──────────┴─────────────────┘

hypergumbo also created comparison sketches temporarily:
  4x budget (32,000t):  /tmp/hypergumbo_sketch_compare/sketch.32000.withsource.md
  16x budget (128,000t): /tmp/hypergumbo_sketch_compare/sketch.128000.withsource.md

To preserve them to cache:
  cp /tmp/hypergumbo_sketch_compare/sketch.32000.withsource.md /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/sketch.32000.withsource.md
  cp /tmp/hypergumbo_sketch_compare/sketch.128000.withsource.md /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/sketch.128000.withsource.md


[hypergumbo sketch] Generated 5
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/hypergumbo.results.16k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/hypergumbo.results.4k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/hypergumbo.results.64k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/hypergumbo.results.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/7cc380096e73eb92/sketch.8000.withsource.md
  Output: stdout
  Embeddings cached: /root/.cache/hypergumbo/50c4fa3a8ce63a5e/embeddings
/content# 
```