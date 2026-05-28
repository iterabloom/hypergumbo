<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Example: hypergumbo analyzing itself

*Sketch captured from `dev` branch on 2026-05-16 (post-5.0.1 / pre-next-5.x). The install / `add-extras` flow below is illustrative — the exact version numbers and dependency list will differ on your machine. To regenerate this example, run `hypergumbo .` from the repo root.*

> **For JSON-level details** (new IR fields like `Edge.dst_ref`, `meta["framework_dispatch"]`, `meta["call_construct"]`, `meta["disambiguation_fallback"]`, `Pattern.meta_match`), the markdown sketch below does NOT surface them — they appear in `hypergumbo run` JSON output. See [RELEASE-NOTES-5.X.md](RELEASE-NOTES-5.X.md) and [MIGRATION-5.X-CONCEPT-AXES.md](MIGRATION-5.X-CONCEPT-AXES.md) for those.

## Install flow (illustrative)

```
/content# pip install hypergumbo
Collecting hypergumbo
  Downloading hypergumbo-X.Y.Z-py3-none-any.whl.metadata (10 kB)
Collecting hypergumbo-core==X.Y.Z (from hypergumbo)
  Downloading hypergumbo_core-X.Y.Z-py3-none-any.whl.metadata (2.2 kB)
```

[...takes ~ 1 minute; bunch of log messages omitted for brevity...]

```
Successfully installed hypergumbo-X.Y.Z hypergumbo-core-X.Y.Z hypergumbo-lang-common-X.Y.Z hypergumbo-lang-extended1-X.Y.Z hypergumbo-lang-mainstream-X.Y.Z rich-* tree-sitter-* tree-sitter-{agda,bash,c,c-sharp,cmake,commonlisp,cpp,css,cuda,dockerfile,embedded-template,fortran,glsl,go,graphql,groovy,haskell,hcl,html,java,javascript,json,julia,kotlin,language-pack,llvm,lua,make,nix,objc,ocaml,odin,php,robot,ruby,rust,scala,solidity,sql,swift,toml,typescript,verilog,vhdl,xml,yaml,zig}-*
/content#
/content# hypergumbo add-extras
```

[the `hypergumbo add-extras` step is only necessary if your repos of interest contain Lean 4 or Wolfram Language; or if you want Gitleaks to detect accidental secret leaks; or if you want embeddings for arguably-better sketches]

```
Building tree-sitter grammars from source...
Build directory: /tmp/ts-grammar-build
```

[...takes 1~5 minutes; bunch of log messages omitted for brevity...]

```
Verifying installation...
tree-sitter-lean: <capsule object "tree_sitter.Language" at 0x...>
tree-sitter-wolfram: <capsule object "tree_sitter.Language" at 0x...>

=== Gitleaks ===
Installing gitleaks for secret scanning...
  Downloading gitleaks_8.30.0_linux_x64.tar.gz...
  Installed to ~/.local/bin/gitleaks
  Done!

=== Embeddings ===
Embeddings already installed (sentence-transformers). Skipping.

=== Summary ===
All extras installed. Run 'hypergumbo remove-extras' to uninstall.
/content#
/content# hypergumbo .
```

[...analysis takes a few minutes for the first run on a multi-MB repo; subsequent runs are cache-served...]

## Sketch output

The output below is the actual sketch hypergumbo produces when run on its own source tree.

----
# hypergumbo

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+.

## Overview
Python (90%), Markdown (5%), Yaml (3%)
1,129 files    (614 non-test + 515 test)
~501,510 LOC (~201,905 non-test + ~299,605 test)

## Structure

```
hypergumbo/
├── .cursor
│   ├── hooks.json
│   └── [and 1 other items]
├── .githooks
│   ├── commit-msg
│   └── [and 9 other items]
├── packages
│   ├── hypergumbo-core
│   │   ├── src
│   │   │   └── hypergumbo_core
│   │   │       ├── cli.py
│   │   │       ├── ir.py
│   │   │       ├── runtime_coherence.py
│   │   │       └── [and 57 other items]
│   │   ├── tests
│   │   │   ├── test_framework_patterns.py
│   │   │   └── [and 167 other items]
│   │   └── [and 2 other items]
│   ├── hypergumbo-tracker
│   │   ├── src
│   │   │   └── hypergumbo_tracker
│   │   │       ├── cli.py
│   │   │       └── [and 32 other items]
│   │   └── [and 6 other items]
│   └── [and 6 other items]
├── scripts
│   ├── lib
│   │   ├── forgejo-api.sh
│   │   └── [and 3 other items]
│   └── [and 62 other items]
├── tests
│   ├── test_agent_supervisor.py
│   └── [and 47 other items]
├── .yamllint.yaml
├── <external>
├── conftest.py
├── pyproject.toml
├── setup.py
└── [and 29 other items]
```

## Frameworks

- lit
- pytest (dev)
- pytorch (dev)
- starlette
- transformers (dev)

## Tests

515 test files · hypothesis, pytest, unittest

*~92% estimated coverage (3705/4016 functions called by tests)*

## Configuration

```
LICENSE: AGPL

--- Additional context (semantic) ---

[packages/htrac-frontend/package.json]
  > { "name": "htrac-frontend", "version": "0.1.0",

[packages/htrac-frontend/tsconfig.json]
  > { "compilerOptions": { "target": "ES2022",

[packages/hypergumbo-core/pyproject.toml]
  > [project] name = "hypergumbo-core" version = "5.0.1"

[packages/hypergumbo-lang-common/pyproject.toml]
  > [project] name = "hypergumbo-lang-common" version = "5.0.1"


[packages/hypergumbo-lang-extended1/pyproject.toml]
  > [project] name = "hypergumbo-lang-extended1" version = "5.0.1"


[packages/hypergumbo-lang-mainstream/pyproject.toml]
  > [project] name = "hypergumbo-lang-mainstream" version = "5.0.1"

[packages/hypergumbo-lang-rust-analyzer/pyproject.toml]
  > [build-system] requires = ["hatchling>=1.24"]

[packages/hypergumbo-tracker/pyproject.toml]
  > ] dependencies = [ "ruamel.yaml>=0.18",

[packages/hypergumbo/pyproject.toml]
  > [project] name = "hypergumbo" version = "5.0.1"


[pyproject.toml]
  > # Root pyproject.toml - shared tool configuration only # Packages are defined in packages/*/pyproject.toml

[setup.py]
  > "ERROR: This repository is a monorepo. The root is not an installable package.\n" "\n" "For development setup:\n"
```

## Entry Points

### CLI & Scripts

- `main` (Python main()) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/runtime_coherence.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo/src/hypergumbo/__main__.py`
- `<module:__main__.py>` (Python script (if __name__ == '__main__')) — `packages/hypergumbo-core/src/hypergumbo_core/__main__.py`
- `<module:compute_probe_embeddings.py>` (Python script (if __name__ == '__main__')) — `scripts/compute_probe_embeddings.py`
- `<module:hypergumbo_diag.py>` (Python script (if __name__ == '__main__')) — `scripts/hypergumbo_diag.py`
- `<module:dead-code-prospector-run.py>` (Python script (if __name__ == '__main__')) — `scripts/dead-code-prospector-run.py`
- `main` (Python main()) — `scripts/hypergumbo_diag.py`
- `main` (Python main()) — `scripts/compute_probe_embeddings.py`
- `main` (Python main()) — `scripts/dead-code-prospector-run.py`
- `main` (Python main()) — `scripts/backfill-training-data-cohort-tags.py`
- `main` (Python main()) — `scripts/per_package_fallback.py`
- `<module:backfill-training-data-cohort-tags.py>` (Python script (if __name__ == '__main__')) — `scripts/backfill-training-data-cohort-tags.py`
- `main` (Python main()) — `scripts/measure-playbook-overlap.py`
- `<module:measure-playbook-overlap.py>` (Python script (if __name__ == '__main__')) — `scripts/measure-playbook-overlap.py`
- `<module:per_package_fallback.py>` (Python script (if __name__ == '__main__')) — `scripts/per_package_fallback.py`

### HTTP Routes

- `_ws_handler` (HTTP WS /ws) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_add_item` (HTTP POST /api/items) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_discuss_item` (HTTP POST /api/items/{item_id}/discuss) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_update_item` (HTTP POST /api/items/{item_id}/update) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_get_item` (HTTP GET /api/items/{item_id}) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_list_items` (HTTP GET /api/items) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_api_ready` (HTTP GET /api/ready) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_health` (HTTP GET /health) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`

### Library API

- `TrackerSet` (Library export: TrackerSet) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `Cache` (Library export: Cache) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py`
- `migrate` (Library export: migrate) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/migration.py`
- `validate_all` (Library export: validate_all) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`
- `validate_ops_file` (Library export: validate_ops_file) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`
- `run_rust_analyzer_scip` (Library export: run_rust_analyzer_scip) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/invoke.py`
- `is_test_path` (Library export: is_test_path) — `packages/hypergumbo-core/src/hypergumbo_core/selection/filters.py`
- `reassign_rust_stable_ids` (Library export: reassign_rust_stable_ids) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/translate.py`
- `parse_scip_symbol` (Library export: parse_scip_symbol) — `packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`
- `should_use_rust_analyzer_backend` (Library export: should_use_rust_analyzer_backend) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/gate.py`
- `translate_scip_to_hg` (Library export: translate_scip_to_hg) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/translate.py`
- `truncate_to_tokens` (Library export: truncate_to_tokens) — `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `select_proportionally` (Library export: select_proportionally) — `packages/hypergumbo-core/src/hypergumbo_core/selection/language_proportional.py`
- `try_analyze_with_rust_analyzer` (Library export: try_analyze_with_rust_analyzer) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/graceful_degrade.py`
- `allocate_language_budget` (Library export: allocate_language_budget) — `packages/hypergumbo-core/src/hypergumbo_core/selection/language_proportional.py`
- `is_example_path` (Library export: is_example_path) — `packages/hypergumbo-core/src/hypergumbo_core/selection/filters.py`
- `is_excluded_kind` (Library export: is_excluded_kind) — `packages/hypergumbo-core/src/hypergumbo_core/selection/filters.py`
- `parse_tier_spec` (Library export: parse_tier_spec) — `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `group_files_by_language` (Library export: group_files_by_language) — `packages/hypergumbo-core/src/hypergumbo_core/selection/language_proportional.py`
- `ScipSymbol` (Library export: ScipSymbol) — `packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`
- `make_pass_id` (Library export: make_pass_id) — `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Tier` (Library export: Tier) — `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `ScipDescriptor` (Library export: ScipDescriptor) — `packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`
- `estimate_json_tokens` (Library export: estimate_json_tokens) — `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `RustAnalyzerNotInstalled` (Library export: RustAnalyzerNotInstalled) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/invoke.py`
- `RustAnalyzerInvocationFailed` (Library export: RustAnalyzerInvocationFailed) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/invoke.py`
- `RustAnalyzerNoOutput` (Library export: RustAnalyzerNoOutput) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/invoke.py`
- `estimate_tokens` (Library export: estimate_tokens) — `packages/hypergumbo-core/src/hypergumbo_core/selection/token_budget.py`
- `group_symbols_by_language` (Library export: group_symbols_by_language) — `packages/hypergumbo-core/src/hypergumbo_core/selection/language_proportional.py`
- `load_function_summaries` (Library export: load_function_summaries) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `FunctionSummary` (Library export: FunctionSummary) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `RustAnalyzerError` (Library export: RustAnalyzerError) — `packages/hypergumbo-lang-rust-analyzer/src/hypergumbo_lang_rust_analyzer/invoke.py`
- `infer_summary` (Library export: infer_summary) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `DescriptorKind` (Library export: DescriptorKind) — `packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`
- `get_summaries_dir` (Library export: get_summaries_dir) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `CallbackFlow` (Library export: CallbackFlow) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `SanitizeEffect` (Library export: SanitizeEffect) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `get_default_summary` (Library export: get_default_summary) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`
- `clear_summary_cache` (Library export: clear_summary_cache) — `packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`


## Data Models

`packages/hypergumbo-core/src/hypergumbo_core/ir.py`:
  - `Symbol` (Python @dataclass)
  - `Span` (Python @dataclass)
  - `Edge` (Python @dataclass)
  - `AnalysisRun` (Python @dataclass)
  - `ExternalRef` (Python @dataclass)
  - `UsageContext` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`:
  - `LinkerContext` (Python @dataclass)
  - `LinkerActivation` (Python @dataclass)
  - `LinkerResult` (Python @dataclass)
  - `LinkerRequirement` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`:
  - `FileAnalysis` (Python @dataclass)
  - `AnalysisResult` (Python @dataclass)
  - `ArityFlags` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`:
  - `DependencyManifest` (Python @dataclass)
  - `FileClassification` (Python @dataclass)
  - `SupplyChainConfig` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/tag_catalog.py`:
  - `TagCatalogEntry` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/nav_history.py`:
  - `NavigationHistory` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/limits.py`:
  - `Limits` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`:
  - `LookupResult` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`:
  - `ValidationResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/slice.py`:
  - `SliceQuery` (Python @dataclass)
  - `SliceResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py`:
  - `IoBoundaryCatalog` (Python @dataclass)
  - `BoundaryMapEntry` (Python @dataclass)
  - `BoundaryMap` (Python @dataclass)
  - `IoChain` (Python @dataclass)
  - `IoPrimitive` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/profile.py`:
  - `RepoProfile` (Python @dataclass)
  - `LanguageStats` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/annotations.py`:
  - `ArrowAnnotation` (Python @dataclass)
  - `RectAnnotation` (Python @dataclass)
  - `LabelAnnotation` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/sketch.py`:
  - `SketchStats` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`:
  - `DataModel` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`:
  - `CompiledItem` (Python @dataclass)
  - `FieldSchema` (Python @dataclass)
  - `TrackerConfig` (Python @dataclass)
  - `DiscussionEntry` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/entrypoints.py`:
  - `Entrypoint` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`:
  - `RegisteredAnalyzer` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/taint.py`:
  - `TaintFlowFinding` (Python @dataclass)
  - `TaintSink` (Python @dataclass)
  - `TaintSource` (Python @dataclass)
  - `TaintSanitizer` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/compact.py`:
  - `CompactConfig` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`:
  - `Pattern` (Python @dataclass)
  - `UsagePatternSpec` (Python @dataclass)
  - `FrameworkPatternDef` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/cfg.py`:
  - `CfgNodeMapping` (Python @dataclass)
  - `DefUseResult` (Python @dataclass)
  - `CfgEdge` (Python @dataclass)
  - `CfgStatement` (Python @dataclass)
  - `_FringeEdge` (Python @dataclass)
  - `_PartialCfg` (Python @dataclass)
  - `BasicBlock` (Python @dataclass)
  - `DdgEdge` (Python @dataclass)
  - `FunctionCfg` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/catalog.py`:
  - `Catalog` (Python @dataclass)
  - `Pass` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/dataflow.py`:
  - `DataflowConfig` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/verify_claims.py`:
  - `TaintFlowConstraint` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/_view_template_core.py`:
  - `TemplateCandidate` (Python @dataclass)
  - `TemplateRenderEmission` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`:
  - `PreflightResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`:
  - `ScipDescriptor` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`:
  - `CheckResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`:
  - `HttpClientCall` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`:
  - `EventPattern` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/import_scope.py`:
  - `CanonicalName` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/graphql.py`:
  - `GraphQLClientCall` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/gitleaks.py`:
  - `SecretFinding` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/embeddings.py`:
  - `EmbeddingDuplicateResult` (Python @dataclass)
- ... and 157 more data models

## Source Files

- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/_text_filters.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py`
- `packages/hypergumbo-core/src/hypergumbo_core/sketch.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py`
- `packages/hypergumbo-core/src/hypergumbo_core/dataflow.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/_transitive_bases.py`
- `packages/hypergumbo-core/src/hypergumbo_core/paths.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py`
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/trackerset.py`
- `packages/hypergumbo-core/src/hypergumbo_core/compact.py`
- `packages/hypergumbo-core/src/hypergumbo_core/sketch_embeddings.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py`
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/_view_template_core.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/tag_catalog.py`
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py`
- `packages/hypergumbo-core/src/hypergumbo_core/cfg.py`
- `packages/hypergumbo-core/src/hypergumbo_core/gitleaks.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py`
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py`
- ... and 792 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `TreeSitterAnalyzer` (class) ★ — Base class for tree-sitter-based language analyzers.
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) ★ — Iterate over all nodes in a tree-sitter tree without recursion.

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Span` (class) ★ — Source code location with line and column info.
- `Edge` (class) — A relationship between two symbols (e.g., function calls).
- `AnalysisRun` (class) — Provenance tracking for an analysis pass execution.

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) ★ — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `register_analyzer(name: str, priority: int=…, supports_max_files: bool=…` (function) — Decorator to register an analyzer function.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `register_linker(name: str, priority: int=…, description: str=…, requiremen…` (function) — Decorator to register a linker function.
- `LinkerContext` (class) — Context passed to all linkers.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py`
- `_parse_ops_file(filepath: Path) -> list[dict[str, Any]]` (function) — Parse an ops file using PyYAML CSafeLoader (fast C extension).

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/_text_filters.py`
- `read_masked_source(file_path: Path, encoding: str=…, errors: str=…, language:…` (function) — Read ``file_path`` and return its content with doc regions masked.

### `packages/hypergumbo-core/src/hypergumbo_core/dataflow.py`
- `annotate_dataflow(edges: List['Edge'], tree: Any, source: bytes, config: Dat…` (function) — Batch-annotate edges with access_mode from AST context (Tier 1).

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`
- `resolve_actor(agent_patterns: list[str] | None=…) -> tuple[str, str]` (function) — Resolve the current OS user to (by, actor) tuple.

### `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `run_behavior_map(repo_root: Path, out_path: Path | None=…, max_tier: int | …` (function) — Run the behavior_map analysis for a repo and write JSON to out_path.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/_transitive_bases.py`
- `collect_transitive_base_names(class_sym: 'Symbol', symbol_by_id: dict[str, 'Symbol'], in…` (function) — Return the raw base-class name strings reachable from ``class_sym``.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py`
- `run_all_analyzers(repo_root: Path, max_files: int | None=…) -> tuple[list[di…` (function) — Run all registered analyzers and collect their results.

### `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py`
- `TrackerApp` (class) — Textual TUI for the hypergumbo tracker.

(... and 6010 more symbols across 327 other files)

## Additional Files

- `README.md`
- `SECURITY.md`
- `CHANGELOG.md`
- `docs/hypergumbo-self-catalog/user_cache_sinks.yaml`
- `CONTRIBUTING.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `packages/hypergumbo-core/README.md`
- `packages/hypergumbo-tracker/README.md`
- `docs/adr/0013-structured-tracker.md`
- `docs/hypergumbo-self-catalog/zone_barrier_sanitizers.yaml`
- ... and 266 more files

## Additional Files Content

------------------- START of README.md ---------------------
````
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### hypergumbo

[![CI](https://codeberg.org/iterabloom/hypergumbo/badges/workflows/ci.yml/badge.svg?branch=dev)](https://codeberg.org/iterabloom/hypergumbo/actions)
[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![Coverage](https://img.shields.io/endpoint?url=https://codeberg.org/iterabloom/hypergumbo/raw/branch/badges/coverage.json)](https://codeberg.org/iterabloom/hypergumbo)

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase.

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
### hypergumbo

hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

> **Audited IO surface.** hypergumbo's own filesystem / network / subprocess activity is enumerated per CLI subcommand category in [SECURITY.md](SECURITY.md), with machine-verifiable claims authored at `docs/hypergumbo.claims.yaml`. Re-run the audit locally via `hypergumbo verify-claims --claims docs/hypergumbo.claims.yaml`. The wrapper-function discipline used to make path-bounded claims structurally verifiable is documented as the recommended pattern for any hypergumbo user (any language) wanting the same precision.

[...truncated...]
````
------------------- END of README.md -----------------------

------------------- START of SECURITY.md -------------------
```
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### Security Policy

<!-- BEGIN generated by scripts/generate-security-md -->

[...truncated...]
```
------------------- END of SECURITY.md ---------------------

------------------- START of CHANGELOG.md ------------------
```
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v5.0.1
- Released **schema** is at: v0.5.8

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

[...truncated...]
```
------------------- END of CHANGELOG.md --------------------

------------------- START of docs/hypergumbo-self-catalog/user_cache_sinks.yaml 
```
# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer-3 project-local catalog — user_cache zone sinks.
#
# Hypergumbo's internal wrappers in safety_zones.py are the canonical
# user_cache write callees. Declaring them here, NOT builtins.open or
# Path.write_text, gives the verify-claims taint pass a sink that is
# distinct from raw fs_write — which keeps host_fs prohibited for
# runtime CLI while user_cache is allowed.
zone: user_cache
trust_level: untrusted

sinks:
  python:
    - module: hypergumbo_core.safety_zones
      functions:
        - cache_write
        - cache_write_bytes
        - cache_save_npy
        - cache_rmtree
```
------------------- END of docs/hypergumbo-self-catalog/user_cache_sinks.yaml 

------------------- START of CONTRIBUTING.md ---------------
```
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### Contributing

We use the **Developer Certificate of Origin (DCO)** instead of a CLA.

#### Sign-off required
All commits must include a `Signed-off-by:` line.
Use `git commit -s` to add this automatically.

[...truncated...]
```
------------------- END of CONTRIBUTING.md -----------------

------------------- START of docs/adr/0001-portable-agent-instructions.md 
```
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### 1. Portable Agent Instructions

Date: 2025-12-20
Status: Accepted

#### Context
We use LLM-assisted development tools (Claude, Gemini, Cursor, etc.). Each tool typically encourages a vendor-specific configuration file (e.g., `CLAUDE.md`, `.cursor/rules`) to define coding standards and workflows.

Allowing these files to evolve independently leads to **instruction drift**: the rules for Claude diverge from the rules for Cursor. Furthermore, relying on vendor-specific formats locks the repository workflow to specific tools, reducing portability for contributors who may prefer different agents.

#### Decision
We will maintain a **canonical, tool-agnostic source of truth** for agent instructions in `AGENTS.md` at the repository root.

Vendor-specific files (`CLAUDE.md`, `GEMINI.md`, etc.) are permitted only as **thin adapters** that import the canonical source. They must not contain independent rules or guidance.

##### Policy Rules
1. **Canonical Source:** `AGENTS.md` is the authority.
2. **Adapters:** Vendor files must only contain import directives (e.g., `@AGENTS.md`).
3. **Guardrails:** Vendor-specific config is allowed only for mechanical enforcement (permissions, file access) where no portable equivalent exists.
4. **CI Enforcement:** We validate via script that adapters remain thin imports.
5. **Workflow:** We follow Trunk-Based Development. Agents should favor small, frequent integrations over long-lived feature branches.

#### Consequences

##### Positive
*   **Single Source of Truth:** Workflow changes only need to happen in one place.
*   **Portability:** New agents can be onboarded simply by adding a one-line adapter.
*   **Drift Prevention:** It is mechanically impossible for one agent to have different coding standards than another.

##### Negative
*   **Context Window Usage:** We force the loading of the full `AGENTS.md` into every session context, which consumes tokens.
*   **Feature Loss:** We cannot leverage unique prompt-engineering features of specific tools (e.g., Cursor's strict "alwaysApply" granular scopes) if they don't have a generic Markdown equivalent.

#### References
*   Implementation details can be found in `scripts/validate-agents.sh` and `AGENTS.md`.
*   Tool compatibility is tracked in `docs/agents/tool-compatibility.md`.
```
------------------- END of docs/adr/0001-portable-agent-instructions.md 

------------------- START of packages/hypergumbo-core/README.md 
````
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### hypergumbo-core

Core infrastructure for hypergumbo repo behavior map generator.

#### What's Included

- **CLI**: Command-line interface (`hypergumbo run`, `hypergumbo sketch`, etc.)
- **IR**: Data structures (Symbol, Edge, Span, AnalysisRun)
- **Analysis Framework**: Base classes and registry for language analyzers
- **Linkers**: Tier 2 edge-recovery across Protocol / Bridge / Framework / Infrastructure subcategories (HTTP, gRPC, IPC, DI, and more — see docs/LINKERS.md)
- **Framework Patterns**: Route and handler detection for 150+ frameworks
- **Slice**: Forward and reverse dependency analysis
- **Sketch**: Token-budgeted codebase overview generation

#### Installation

```bash
### Core only (no language analyzers)
pip install hypergumbo-core

### Full installation (recommended)
pip install hypergumbo
```

#### Usage

```python
from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.sketch import generate_sketch
from hypergumbo_core.slice import forward_slice, reverse_slice
```

#### Documentation

See https://codeberg.org/iterabloom/hypergumbo for full documentation.
````
------------------- END of packages/hypergumbo-core/README.md 
                     How Representative Is This Sketch?                     
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 8,000t ┃ 32,000t ┃ 128,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Entry Points             │    96% │     96% │      96% │ confidence mass │
│ Data Models              │    33% │     45% │      45% │ confidence mass │
│ Source Files             │    22% │     40% │      71% │ symbol mass     │
│ Key Symbols              │   7.8% │     12% │      18% │ symbol mass     │
│ Additional Files         │    16% │     30% │      40% │ symbol mass     │
│ Source Files Content     │      - │    9.5% │      17% │ symbol mass     │
│ Additional Files Content │    15% │   10.0% │      22% │ symbol mass     │
└──────────────────────────┴────────┴─────────┴──────────┴─────────────────┘



[output continues with cache-path artifact list for the generated 8k / 32k / 128k tier files and embeddings]
