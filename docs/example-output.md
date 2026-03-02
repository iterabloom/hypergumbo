# Example: hypergumbo 2.1.0 analyzing itself (run on Google Colab Terminal)

```
/content# pip install hypergumbo
Collecting hypergumbo
  Downloading hypergumbo-2.1.0-py3-none-any.whl.metadata (10 kB)
Collecting hypergumbo-core==2.1.0 (from hypergumbo)
  Downloading hypergumbo_core-2.1.0-py3-none-any.whl.metadata (2.2 kB)
```

[...takes ~ 1 minute; bunch of log messages omitted for brevity...]

```
Successfully installed hypergumbo-2.1.0 hypergumbo-core-2.1.0 hypergumbo-lang-common-2.1.0 hypergumbo-lang-extended1-2.1.0 hypergumbo-lang-mainstream-2.1.0 rich-14.3.3 tree-sitter-0.25.2 tree-sitter-agda-1.3.3 tree-sitter-bash-0.25.1 tree-sitter-c-0.24.1 tree-sitter-c-sharp-0.23.1 tree-sitter-cmake-0.7.2.post1 tree-sitter-commonlisp-0.4.1 tree-sitter-cpp-0.23.4 tree-sitter-css-0.25.0 tree-sitter-cuda-0.21.1 tree-sitter-dockerfile-0.2.0 tree-sitter-embedded-template-0.25.0 tree-sitter-fortran-0.5.1 tree-sitter-glsl-0.2.0 tree-sitter-go-0.25.0 tree-sitter-graphql-0.1.0 tree-sitter-groovy-0.1.2 tree-sitter-haskell-0.23.1 tree-sitter-hcl-1.2.0 tree-sitter-html-0.23.2 tree-sitter-java-0.23.5 tree-sitter-javascript-0.25.0 tree-sitter-json-0.24.8 tree-sitter-julia-0.23.1 tree-sitter-kotlin-1.1.0 tree-sitter-language-pack-0.13.0 tree-sitter-llvm-1.1.0 tree-sitter-lua-0.4.1 tree-sitter-make-1.1.1 tree-sitter-nix-0.1.0 tree-sitter-objc-3.0.2 tree-sitter-ocaml-0.24.2 tree-sitter-odin-1.3.0 tree-sitter-php-0.24.1 tree-sitter-robot-1.1.2 tree-sitter-ruby-0.23.1 tree-sitter-rust-0.24.0 tree-sitter-scala-0.24.0 tree-sitter-solidity-1.2.13 tree-sitter-sql-0.3.11 tree-sitter-swift-0.0.1 tree-sitter-toml-0.7.0 tree-sitter-typescript-0.23.2 tree-sitter-verilog-1.0.3 tree-sitter-vhdl-1.3.1 tree-sitter-xml-0.7.0 tree-sitter-yaml-0.7.2 tree-sitter-zig-1.1.2
/content# 
/content# hypergumbo add-extras
```

[above `hypergumbo add-extras` step is only necessary if your repos of interest might contain Lean 4 or Wolfram Language; or if you want Gitleaks to attempt to detect accidental secret leaks; or if you want embeddings for arguably-better sketches]

```
Building tree-sitter grammars from source...
Build directory: /tmp/ts-grammar-build
```

[...takes 1~5 minutes depending on whether you already have `transformers` and `pytorch`; bunch of log messages omitted for brevity...]

```
Verifying installation...
tree-sitter-lean: <capsule object "tree_sitter.Language" at 0x7a9f2f6dbe10>
tree-sitter-wolfram: <capsule object "tree_sitter.Language" at 0x7c26bfc03db0>

=== Gitleaks ===
Installing gitleaks for secret scanning...
  Downloading gitleaks_8.30.0_linux_x64.tar.gz...
  Installed to /root/.local/bin/gitleaks
  Done!

=== Embeddings ===
Embeddings already installed (sentence-transformers 5.2.3). Skipping.

=== Summary ===
All extras installed. Run 'hypergumbo remove-extras' to uninstall.
/content# 
/content# hypergumbo hypergumbo/
[ 55%] Running linkers... ETA 29s    /usr/local/lib/python3.12/dist-packages/hypergumbo_core/cli.py:4053: UserWarning: Detected 13 shell file(s) but no analyzer is available for Shell.
```

[...takes ~ 9.5 minutes; harmless CUDA library conflict messages omitted for brevity...]

```
[100%] Complete in 364.7s
ℹ️  Secret scan complete (best-effort, not exhaustive).
# hypergumbo

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

## Overview
Python (91%), Markdown (4%), Yaml (3%)
728 files    (383 non-test + 345 test)
~320,798 LOC (~129,172 non-test + ~191,626 test)

## Structure

` ` `
hypergumbo/
├── .agent
│   ├── last_stop_check.json
│   └── [and 6 other items]
├── .gitea
│   ├── SQUASH_TEMPLATE.md
│   └── [and 1 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 9 other items]
├── docs
│   ├── CACHE.md
│   └── [and 22 other items]
├── packages
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── analyze
│   │   │       │   ├── base.py
│   │   │       │   └── [and 3 other items]
│   │   │       ├── __main__.py
│   │   │       ├── cli.py
│   │   │       ├── ir.py
│   │   │       └── [and 26 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 94 other items]
│   │   └── [and 2 other items]
│   ├── hypergumbo-tracker
│   │   ├── src
│   │   │   └── hypergumbo_tracker
│   │   │       ├── cli.py
│   │   │       └── [and 13 other items]
│   │   └── [and 5 other items]
│   └── [and 4 other items]
├── scripts
│   ├── lib
│   │   └── forgejo-api.sh
│   └── [and 33 other items]
├── tests
│   ├── test_bakeoff_features_reflect.py
│   └── [and 2 other items]
├── conftest.py
├── pyproject.toml
├── setup.py
└── [and 21 other items]
` ` `

## Frameworks

- pytest
- pytorch
- transformers

## Tests

345 test files · cargo test, pytest, unittest

*~95% estimated coverage (2693/2847 functions called by tests)*

## Configuration

` ` `
LICENSE: AGPL

--- Additional context (semantic) ---

[packages/hypergumbo-core/pyproject.toml]
  > [project] name = "hypergumbo-core" version = "2.1.0"
  > # YAML parsing for framework patterns "pyyaml~=6.0.3", ]
  > [project.optional-dependencies] dev = [


[packages/hypergumbo-lang-common/pyproject.toml]
  > [build-system] requires = ["hatchling>=1.24"]
  > "Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only", ]

[packages/hypergumbo-lang-mainstream/pyproject.toml]
  > "tree-sitter-typescript~=0.23.2", "tree-sitter-php~=0.24.1", "tree-sitter-c~=0.24.1",

[packages/hypergumbo-tracker/pyproject.toml]
  > ] dependencies = [ "ruamel.yaml>=0.18",
  > dev = [ "pytest>=8.0,<10", "pytest-cov~=7.0.0",
  > filterwarnings = [] pythonpath = ["tests"]


[packages/hypergumbo/pyproject.toml]
  > [project] name = "hypergumbo" version = "2.1.0"


[pyproject.toml]
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml

[setup.py]
  > "ERROR: This repository is a monorepo. The root is not an installable package.\n" "\n" "For development setup:\n"
` ` `

## Entry Points

### CLI & Scripts

- `main` (Python main()) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo-core/src/hypergumbo_core/__main__.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo/src/hypergumbo/__main__.py`
- `main` (Python main()) — `scripts/hypergumbo_diag.py`
- `<module:hypergumbo_diag.py>` (Python script (if __name__ == '__main__')) — `scripts/hypergumbo_diag.py`
- `<module:compute_probe_embeddings.py>` (Python script (if __name__ == '__main__')) — `scripts/compute_probe_embeddings.py`
- `main` (Python main()) — `scripts/compute_probe_embeddings.py`


## Data Models

`packages/hypergumbo-core/src/hypergumbo_core/ir.py`:
  - `Symbol` (Python @dataclass)
  - `Span` (Python @dataclass)
  - `AnalysisRun` (Python @dataclass)
  - `Edge` (Python @dataclass)
  - `UsageContext` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`:
  - `FileAnalysis` (Python @dataclass)
  - `AnalysisResult` (Python @dataclass)
  - `ArityFlags` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`:
  - `LinkerResult` (Python @dataclass)
  - `LinkerRequirement` (Python @dataclass)
  - `LinkerContext` (Python @dataclass)
  - `LinkerActivation` (Python @dataclass)
  - `RegisteredLinker` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`:
  - `CheckResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`:
  - `LookupResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/limits.py`:
  - `Limits` (Python @dataclass)
  - `FailedFile` (Python @dataclass)
  - `AmbiguousPath` (Python @dataclass)
  - `ClassificationFailure` (Python @dataclass)
  - `SupplyChainLimits` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`:
  - `DataModel` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`:
  - `ValidationResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`:
  - `Pattern` (Python @dataclass)
  - `UsagePatternSpec` (Python @dataclass)
  - `FrameworkPatternDef` (Python @dataclass)
  - `DeferredResolutionStats` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py`:
  - `Entrypoint` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`:
  - `RegisteredAnalyzer` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/catalog.py`:
  - `Catalog` (Python @dataclass)
  - `Pass` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/profile.py`:
  - `LanguageStats` (Python @dataclass)
  - `RepoProfile` (Python @dataclass)
  - `FrameworkSpec` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/sketch.py`:
  - `SketchStats` (Python @dataclass)
  - `_TestAnalysis` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/slice.py`:
  - `SliceResult` (Python @dataclass)
  - `SliceQuery` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`:
  - `EventPattern` (Python @dataclass)
  - `EventSourcingLinkResult` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`:
  - `FieldSchema` (Python @dataclass)
  - `CompiledItem` (Python @dataclass)
  - `DiscussionEntry` (Python @dataclass)
  - `KindConfig` (Python @dataclass)
  - `TrackerConfig` (Python @dataclass)
  - `UpdateOp` (Python @dataclass)
  - `CreateOp` (Python @dataclass)
  - `DemoteOp` (Python @dataclass)
  - `DiscussClearOp` (Python @dataclass)
  - `DiscussOp` (Python @dataclass)
  - `DiscussSummarizeOp` (Python @dataclass)
  - `LockOp` (Python @dataclass)
  - `PromoteOp` (Python @dataclass)
  - `ReconcileOp` (Python @dataclass)
  - `StealthOp` (Python @dataclass)
  - `UnlockOp` (Python @dataclass)
  - `UnstealthOp` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/compact.py`:
  - `CompactConfig` (Python @dataclass)
  - `IncludedSummary` (Python @dataclass)
  - `OmittedSummary` (Python @dataclass)
  - `CompactResult` (Python @dataclass)
  - `ConnectivityResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/graphql.py`:
  - `GraphQLClientCall` (Python @dataclass)
  - `GraphQLLinkResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`:
  - `SupplyChainConfig` (Python @dataclass)
  - `FileClassification` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/embeddings.py`:
  - `EmbeddingDuplicateResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/build_grammars.py`:
  - `GrammarSpec` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`:
  - `PreflightResult` (Python @dataclass)
  - `SyncResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/gitleaks.py`:
  - `SecretFinding` (Python @dataclass)
`packages/hypergumbo-core/tests/test_tree_sitter_analyzer.py`:
  - `MockNode` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/grpc.py`:
  - `GrpcPattern` (Python @dataclass)
  - `GrpcLinkResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`:
  - `HttpClientCall` (Python @dataclass)
  - `HttpLinkResult` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/migration.py`:
  - `ParsedItem` (Python @dataclass)
  - `MigrationResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/subprocess_cli.py`:
  - `SubprocessCall` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/di_resolution.py`:
  - `DIBinding` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py`:
  - `LanguageSpec` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/database_query.py`:
  - `DatabaseQueryPattern` (Python @dataclass)
  - `DatabaseQueryLinkResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/partial_install_warnings.py`:
  - `PartialInstallWarning` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/ranking.py`:
  - `CentralityResult` (Python @dataclass)
  - `RankedFile` (Python @dataclass)
  - `RankedSymbol` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/graphql_resolver.py`:
  - `ResolverPattern` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/websocket.py`:
  - `WebSocketPattern` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/openapi.py`:
  - `OpenApiOperation` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/dependency.py`:
  - `DependencyLinkResult` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`:
  - `_ParsedFile` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py`:
  - `FileAnalysis` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/objc.py`:
  - `FileAnalysis` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py`:
  - `FileAnalysis` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py`:
  - `FileAnalysis` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/ipc.py`:
  - `IpcLinkResult` (Python @dataclass)
  - `IpcPattern` (Python @dataclass)
- ... and 34 more data models

## Source Files

- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `packages/hypergumbo-core/src/hypergumbo_core/ranking.py`
- `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `packages/hypergumbo-core/src/hypergumbo_core/paths.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/ada.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`
- `packages/hypergumbo-core/src/hypergumbo_core/sketch_embeddings.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/json_config.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/d_lang.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/apex.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/astro.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py`
- `packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/nim.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/rst.py`
- `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/robot.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/clojure.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/racket.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/vue.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/puppet.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/purescript.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/xml_config.py`
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/r_lang.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py`
- ... and 500 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.
- `make_pass_id(name: str) -> str` (function) — Return the canonical pass ID for an analyzer or linker.
  (... +1 more, top score: 0.33)

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `TreeSitterAnalyzer` (class) ★ — Base class for tree-sitter-based language analyzers.
- `node_text(node: 'tree_sitter.Node', source: bytes) -> str` (function) — Extract text content for a tree-sitter node.
- `find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find the first child node of a given type.
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) — Iterate over all nodes in a tree-sitter tree without recursion.
- `make_symbol_id(lang: str, path: str, start_line: int, end_line: int, name…` (function) — Generate a location-based symbol ID.
  (... +1 more, top score: 0.37)

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) ★ — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.
- `register_linker(name: str, priority: int=…, description: str=…, requiremen…` (function) — Decorator to register a linker function.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `register_analyzer(name: str, priority: int=…, requires_symbols: list[str] | …` (function) — Decorator to register an analyzer function.

### `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `compile_ops(ops: list[dict[str, Any]], item_id: str=…) -> CompiledItem` (function) — Compile an op log (list of op dicts) into a CompiledItem.
- `_parse_ops_file(filepath: Path) -> list[dict[str, Any]]` (function) — Parse an ops file using PyYAML CSafeLoader (fast C extension).

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `TrackerSet` (class) — Multi-tier unified view over canonical, workspace, and stealth Stores.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `resolve_actor(agent_patterns: list[str] | None=…) -> tuple[str, str]` (function) — Resolve the current OS user to (by, actor) tuple.

### `packages/hypergumbo-core/src/hypergumbo_core/profile.py`
- `_read_all_manifest_files(repo_root: Path, filename: str, max_depth: int=…) -> str` (function) — Read all manifest files with given name, recursively.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py`
- `Cache` (class) — SQLite read cache for a single tier's Store.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`
- `main(argv: list[str] | None=…) -> None` (function) — Primary CLI entry point.

(... and 3269 more symbols across 201 other files)

## Additional Files

- `README.md`
- `CONTRIBUTING.md`
- `docs/GOVERNANCE.md`
- `packages/hypergumbo-core/README.md`
- `packages/hypergumbo-tracker/README.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/LANGUAGES.md`
- `docs/future/roadmap-details.md`
- `docs/MIGRATION-2.0.md`
- `docs/LINKERS.md`
- `packages/hypergumbo-lang-mainstream/README.md`
- `CHANGELOG.md`
- `docs/adr/0003-call-patterns-extension.md`
- `docs/future/registry-factory-vision.md`
- `packages/hypergumbo/README.md`
- ... and 140 more files

## Source Files Content

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py 
` ` `
"""Base classes and utilities for language analyzers.

This module provides shared infrastructure for all language analyzers,
eliminating duplication across 65+ analyzer files.

Shared Components
-----------------
- **AnalysisResult**: Universal result type returned by all analyzers
- **FileAnalysis**: Intermediate per-file analysis result
- **Tree-sitter helpers**: node_text, find_child_by_type, find_child_by_field
- **ID generation**: make_symbol_id, make_file_id
- **Availability checking**: is_grammar_available

Why This Design
---------------
Previously, each analyzer duplicated these components. This led to:
- 65+ copies of identical dataclasses
- Inconsistent helper implementations
- High maintenance burden when adding new analyzers

Now, analyzers import from this module and focus only on
language-specific parsing logic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re as _re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, Optional

from ..discovery import find_files
from ..ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol, UsageContext, make_pass_id
from ..symbol_resolution import NameResolver

if TYPE_CHECKING:
    import tree_sitter


@dataclass
class AnalysisResult:
    """Universal result type for all language analyzers.

    This replaces the per-language XxxAnalysisResult dataclasses
    (GoAnalysisResult, RustAnalysisResult, etc.) which were all identical.

    Attributes:
        symbols: List of detected symbols (functions, classes, etc.)
        edges: List of relationships between symbols (calls, imports, etc.)
        usage_contexts: List of usage contexts for call-based pattern matching (v1.1.x)
        run: Provenance tracking for the analysis pass
        skipped: Whether the analysis was skipped (e.g., missing dependency)
        skip_reason: Human-readable reason for skipping
    """

[...truncated...]
` ` `
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py 

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/ir.py 
` ` `
"""Internal Representation (IR) for code analysis.

Parsers emit Symbol and Edge objects to this IR layer. The IR is then
compiled to output views (e.g., behavior_map JSON).

Key IR Classes
--------------
- **Span**: Source location with line/column info
- **AnalysisRun**: Provenance for an analysis pass execution, including
  run_signature for cache keying and repo_fingerprint for invalidation
- **Symbol**: Code elements (functions, classes) with location, identity hashes
  (stable_id, shape_id), and quality scores
- **Edge**: Relationships between symbols with confidence, evidence tracking,
  and edge_key for deduplication across passes

Provenance Fields
-----------------
- execution_id: Unique per run (uuid)
- run_signature: Deterministic hash of (pass_id, version, config_fingerprint, toolchain)
- repo_fingerprint: Hash of git state for cache invalidation
- origin_run_signature: Links nodes/edges to their creating run's signature
"""
import hashlib
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from . import __version__

PASS_VERSION: str = __version__
"""Canonical pass version derived from the package version.

All analyzers and linkers use this as their version string, ensuring
cache signatures correctly invalidate on release.  Single source of truth.
"""


def make_pass_id(name: str) -> str:
    """Return the canonical pass ID for an analyzer or linker.

    Analyzers: ``make_pass_id("go")`` → ``"go-v1"``
    Linkers:   ``make_pass_id("containment-linker")`` → ``"containment-linker-v1"``

    The ``-v1`` suffix is backend-neutral and provides an escape hatch
    for future versioning if an analyzer's output format changes.
    """
    return f"{name}-v1"


@dataclass
class Span:
    """Source code location with line and column info."""

    start_line: int
    end_line: int
    start_col: int
    end_col: int

[...truncated...]
` ` `
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/ir.py 

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/discovery.py 
` ` `
"""File discovery with exclude patterns and ambiguous extension disambiguation.

Provides shared utilities for finding source files across the repository while
respecting exclude patterns. Also provides content-based classification for
ambiguous file extensions:

- ``.m`` files: shared by Objective-C, MATLAB, and Wolfram.
  ``classify_dot_m_file`` reads file content and uses syntactic heuristics.
- ``.d`` files: shared by D programming language and GCC Makefile dependency
  files (generated by ``gcc -MMD``). ``classify_dot_d_file`` distinguishes
  D source from dependency files by checking for D keywords vs make rules.
"""
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Iterator

# Global file size limit. Set via set_max_file_bytes() before running
# analyzers; find_files() reads this when no explicit max_file_bytes
# is passed. Reset to None after use.
_global_max_file_bytes: int | None = None


def set_max_file_bytes(limit: int | None) -> None:
    """Set the global max file bytes limit for find_files().

    Called by the CLI before running analyzers so that all callers of
    find_files() respect the limit without needing individual changes.
    """
    global _global_max_file_bytes
    _global_max_file_bytes = limit

[...truncated...]
` ` `
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/discovery.py 

------------------- START of packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py 
` ` `
"""Analyzer registry for decorator-based dynamic dispatch.

This module provides the canonical registration system for language analyzers,
mirroring the proven pattern from linkers/registry.py. Analyzers self-register
via the @register_analyzer decorator at import time; entry-point-based plugin
discovery triggers the imports.

How It Works
------------
1. Each analyzer module decorates its entry function with @register_analyzer()
2. Language packages export ANALYZER_MODULES lists via entry-points
3. ensure_discovered() loads entry-points, imports listed modules (triggering decorators)
4. run_all_analyzers() / get_all_analyzers() iterate the populated registry

Why This Design
---------------
- Self-registration: the analyzer file is self-describing (decorator on the function)
- Plugin extensibility: entry-points enable external language packages
- Rich metadata: priority, supports_max_files, capture_symbols_as, requires_symbols
- Consistency: mirrors the linker registry pattern (ADR-0012 Step 1)

Usage
-----
In an analyzer module:

    from hypergumbo_core.analyze.registry import register_analyzer

    @register_analyzer("go", priority=50)
    def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        ...

Discovery happens automatically via entry-points when ensure_discovered() is called.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .base import AnalysisResult

logger = logging.getLogger(__name__)

# Type alias for analyzer functions
AnalyzerFunc = Callable[..., AnalysisResult]


@dataclass
class RegisteredAnalyzer:
    """Metadata for a registered analyzer.

[...truncated...]
` ` `
------------------- END of packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py 

------------------- START of packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py 
` ` `
# SPDX-License-Identifier: MPL-2.0
"""Store for the hypergumbo tracker — YAML I/O, compile, CRUD, Lamport clock.

This module provides the core storage operations for the tracker:

- **YAML serialization:** Writes ops using ruamel.yaml (round-trip-safe,
  canonical field ordering, double-quoted strings for safety). Reads ops
  using PyYAML's CSafeLoader (~5x faster C extension).
- **Nonce-on-every-line:** Post-processes serialized YAML to append
  `  # <nonce>` inline comments on every non-empty line, making each line
  globally unique for merge=union correctness.
- **Proquint IDs:** Content-hash IDs using SHA-256 + proquint encoding.
  Same logical item → same ID, natural deduplication.
- **compile():** Pure function that folds an op log into current item state.
  LWW for scalars, accumulation for sets, Lamport-clock-ordered.
- **Lamport clock:** Cross-branch causal ordering via git cat-file --batch.
- **SimHash:** Fast locality-sensitive fingerprinting for near-duplicate
  detection on add().
- **CRUD:** add(), update(), discuss(), lock/unlock methods with advisory
  file locking (flock) around appends.
- **Prefix matching:** Resolve unambiguous ID prefixes to full IDs.
- **ready/list:** Filtered, sorted item queries.

The Store operates on a single tier directory (no TrackerSet, no cache —
those come in later PRs).

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import secrets
import subprocess  # nosec B404
import warnings
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any

import proquint
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    TrackerConfig,
    load_config,
    resolve_actor,
)

[...truncated...]
` ` `
------------------- END of packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py 


## Additional Files Content

------------------- START of README.md ---------------------
` ` ` `
# hypergumbo

[![CI](https://codeberg.org/iterabloom/hypergumbo/badges/workflows/ci.yml/badge.svg?branch=dev)](https://codeberg.org/iterabloom/hypergumbo/actions)
[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![Coverage](https://img.shields.io/endpoint?url=https://codeberg.org/iterabloom/hypergumbo/raw/branch/badges/coverage.json)](https://codeberg.org/iterabloom/hypergumbo)

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase.

```bash
pip install hypergumbo
```

> Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing.

> Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://codeberg.org/iterabloom/hypergumbo/src/branch/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

```bash
git clone https://codeberg.org/iterabloom/hypergumbo
hypergumbo hypergumbo/
```

Output:

```bash
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (88%), Markdown (6%), Yaml (4%)
495 files    (321 non-test + 174 test)
~186,506 LOC (~96,384 non-test + ~90,122 test)

[...truncated...]
` ` ` `
------------------- END of README.md -----------------------

                     How Representative Is This Sketch?                     
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 8,000t ┃ 32,000t ┃ 128,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Entry Points             │    41% │    100% │     100% │ confidence mass │
│ Data Models              │    76% │    100% │     100% │ confidence mass │
│ Source Files             │    38% │     67% │     100% │ symbol mass     │
│ Key Symbols              │    17% │     25% │      33% │ symbol mass     │
│ Additional Files         │    17% │     28% │      28% │ symbol mass     │
│ Source Files Content     │    18% │     18% │      25% │ symbol mass     │
│ Additional Files Content │   6.7% │    7.8% │      17% │ symbol mass     │
└──────────────────────────┴────────┴─────────┴──────────┴─────────────────┘

hypergumbo also created comparison sketches temporarily:
  4x budget (32,000t):  /tmp/hypergumbo_sketch_compare/sketch.32000.withsource.md
  16x budget (128,000t): /tmp/hypergumbo_sketch_compare/sketch.128000.withsource.md

To preserve them to cache:
  cp /tmp/hypergumbo_sketch_compare/sketch.32000.withsource.md /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/sketch.32000.withsource.md
  cp /tmp/hypergumbo_sketch_compare/sketch.128000.withsource.md /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/sketch.128000.withsource.md


[hypergumbo sketch] Generated 5
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/hypergumbo.results.16k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/hypergumbo.results.4k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/hypergumbo.results.64k.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/hypergumbo.results.json
  /root/.cache/hypergumbo/50c4fa3a8ce63a5e/results/c85373f05a0e4ffb/sketch.8000.withsource.md
  Output: stdout
  Embeddings cached: /root/.cache/hypergumbo/50c4fa3a8ce63a5e/embeddings
```