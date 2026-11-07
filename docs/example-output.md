<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Example: hypergumbo analyzing itself

*Sketch captured from `dev` branch on 2026-11-07 (post-9.2.0 / pre-10.0). The install / `add-extras` flow below is illustrative — the exact version numbers and dependency list will differ on your machine.*

> **For JSON-level details**, the markdown sketch below does NOT surface them — they appear in `hypergumbo survey` JSON output.

## Install flow

```
/content# pip install hypergumbo
Collecting hypergumbo
  Downloading hypergumbo-9.2.0-py3-none-any.whl.metadata (26 kB)
Collecting hypergumbo-core==9.2.0 (from hypergumbo)
  Downloading hypergumbo_core-9.2.0-py3-none-any.whl.metadata (2.5 kB)

[...bunch of installer log messages omitted for brevity...]

Successfully installed hypergumbo-9.2.0 hypergumbo-core-9.2.0 hypergumbo-lang-common-9.2.0 hypergumbo-lang-extended1-9.2.0 hypergumbo-lang-mainstream-9.2.0 protobuf-6.33.6 pygments-2.20.0 rich-14.3.4 tree-sitter-0.25.2 tree-sitter-agda-1.3.3 tree-sitter-bash-0.25.1 tree-sitter-c-0.24.2 tree-sitter-c-sharp-0.23.5 tree-sitter-cmake-0.7.2.post1 tree-sitter-commonlisp-0.4.1 tree-sitter-cpp-0.23.4 tree-sitter-css-0.25.0 tree-sitter-cuda-0.21.2 tree-sitter-dockerfile-0.2.0 tree-sitter-embedded-template-0.25.0 tree-sitter-fortran-0.5.1 tree-sitter-glsl-0.2.0 tree-sitter-go-0.25.0 tree-sitter-graphql-0.1.0 tree-sitter-groovy-0.1.2 tree-sitter-haskell-0.23.1 tree-sitter-hcl-1.2.0 tree-sitter-html-0.23.2 tree-sitter-java-0.23.5 tree-sitter-javascript-0.25.0 tree-sitter-json-0.24.8 tree-sitter-julia-0.23.1 tree-sitter-kotlin-1.1.0 tree-sitter-language-pack-0.13.0 tree-sitter-llvm-1.1.0 tree-sitter-lua-0.4.1 tree-sitter-make-1.1.1 tree-sitter-nix-0.1.0 tree-sitter-objc-3.0.2 tree-sitter-ocaml-0.24.2 tree-sitter-odin-1.3.0 tree-sitter-php-0.24.1 tree-sitter-robot-1.1.2 tree-sitter-ruby-0.23.1 tree-sitter-rust-0.24.2 tree-sitter-scala-0.24.0 tree-sitter-solidity-1.2.13 tree-sitter-sql-0.3.11 tree-sitter-swift-0.7.4 tree-sitter-tlaplus-1.5.0 tree-sitter-toml-0.7.0 tree-sitter-typescript-0.23.2 tree-sitter-verilog-1.0.3 tree-sitter-vhdl-1.3.1 tree-sitter-xml-0.7.0 tree-sitter-yaml-0.7.2 tree-sitter-zig-1.1.2
/content#
/content# hypergumbo add-extras

[the hypergumbo add-extras step is only necessary if your repos of interest contain Lean 4 or Wolfram Language; or if you want Gitleaks to detect accidental secret leaks; or if you want embeddings for arguably-better sketches]

=== Grammars ===
Building tree-sitter grammars from source...
Build directory: /tmp/ts-grammar-build

[...bunch of installer log messages omitted for brevity...]

Verifying installation...
tree-sitter-lean: <capsule object "tree_sitter.Language" at 0x7f29eec9a750>
tree-sitter-wolfram: <capsule object "tree_sitter.Language" at 0x7d466ccce750>
tree-sitter-circom: <capsule object "tree_sitter.Language" at 0x7f1ee5e16750>

=== Gitleaks ===
Installing gitleaks for secret scanning...
  Downloading gitleaks_8.30.1_linux_x64.tar.gz...
  Installed to /root/.local/bin/gitleaks
  Done!

=== Embeddings ===
Installing embedding dependencies...
  Done!

=== Rust analyzer ===
hypergumbo-lang-rust-analyzer Python integration package is not installed; skipping rust-analyzer rustup binary install. Install via:
  pipx install 'hypergumbo[rust-analyzer]' --force
(or 'pipx inject hypergumbo hypergumbo-lang-rust-analyzer' to add the wrapper to an existing install without reinstalling.)

=== Summary ===
All extras installed. Run 'hypergumbo remove-extras' to uninstall.
/content# 
/content# 
/content# 
/content# 
/content# 
/content# git clone https://github.com/iterabloom/hypergumbo
Cloning into 'hypergumbo'...
remote: Enumerating objects: 96090, done.
remote: Counting objects: 100% (540/540), done.
remote: Compressing objects: 100% (446/446), done.
remote: Total 96090 (delta 198), reused 141 (delta 89), pack-reused 95550 (from 2)
Receiving objects: 100% (96090/96090), 53.92 MiB | 28.10 MiB/s, done.
Resolving deltas: 100% (70413/70413), done.
/content# 
/content# 
/content# 
/content#
```

## Actual sketch command

```
/content# hypergumbo hypergumbo/
[   30s] Running analyzers (27s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           c       edge_source.anchor_in_definitions:100
           python  py._extract_django_usage_contexts:1448
[ 1m00s] Running analyzers (57s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           c       base.iter_tree_with_context:2911
           python  py._compute_cyclomatic_complexity:2389
[ 1m30s] Running analyzers (1m28s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  py._extract_file_analysis:3174
[ 2m00s] Running analyzers (1m58s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  py._module_name_from_path:2542
[ 2m30s] Running analyzers (2m28s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  py._collect_statement_binding_names._walk:7458 (recursion x4)
[ 3m00s] Running analyzers (2m58s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  py._extract_edges:5842
[ 3m30s] Running analyzers (3m28s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  py._extract_edges._collect_local_bindings._walk_scope:5954 (recursion x3)
[ 4m00s] Running analyzers (3m58s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > all_analyzers.run_all_analyzers:465
           python  dataflow.scan_library_patterns:572
[ 4m30s] Running linkers (10s) | main (pyffi-linker): … > registry._run_linker_with_cache > pyffi.pyffi_linker > pyffi.link_pyffi > pyffi._scan_python_file_for_ffi_calls:176
[ 5m00s] Running linkers (40s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > registry.run_all_linkers:1076
           event-sourcing-linker  _text_filters._collect_mask_ranges:280
           graphql-sdl-linker     _text_filters.read_masked_source:399
           message-queue-linker   _text_filters.read_masked_source:399
[ 5m30s] Running linkers (1m10s) | main: … > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey > registry.run_all_linkers:1076
           subprocess-linker  _text_filters._collect_mask_ranges:317
[ 6m01s] Stamping fingerprints (9s) | main: … > fingerprint.stamp_symbol_fingerprints > fingerprint._compute_fingerprint > fingerprint._python_context_fingerprint_cached > fingerprint._PyCoverIndex.__init__:267
[ 6m32s] Stamping fingerprints (41s) | main: … > fingerprint.stamp_symbol_fingerprints > fingerprint._compute_fingerprint > fingerprint._python_context_fingerprint_cached > fingerprint._PyCoverIndex.__init__:267
rust-analyzer is not installed. Installing it gives hypergumbo far more precise Rust type and call-target resolution.
  Install:  rustup component add rust-analyzer
  Then enable per run with --backend rust-analyzer.
  Indexing runs this project's build scripts (build.rs) and proc macros as you — the same trust you extend by opening it in an editor. Enable it only for repositories you trust.
  Not wanted here: hypergumbo trust-backend rust_analyzer /content/hypergumbo --revoke  (records the answer; this notice stops)
Vendored third-party code analysed as tier 3, not counted in the language breakdown: packages/hypergumbo-core/src/hypergumbo_core/vendor/ (c, cpp, json; 13 files). To skip: --exclude <dir>.
[ 7m02s] Pre-computing sketch data: config (6s) | main: … > sketch._extract_config_embedding > sketch._collect_config_content_with_discovery > sketch._discover_config_files_embedding > sketch_embeddings._load_embedding_model:219
[ 7m02s] Pre-computing sketch data: config (6s)WARNING: All log messages before absl::InitializeLog() is called are written to STDERR
I0000 00:00:1794087340.091747   27837 pjrt_api.cc:119] GetPjrtApi was found for TPU at /usr/local/lib/python3.13/dist-packages/libtpu/libtpu.so
I0000 00:00:1794087341.203927   27837 device_rt.cc:166] PjRt runtime initialization deferred for tpu
Successfully renamed PrivateUse1 backend to 'tpu'. Device: device(type='tpu')
Registered Python module for 'tpu'.
Device type: tpu, Device index: default
Initializing TPU distributed runtime
I0000 00:00:1794087341.218428   27837 libtpu_version.cc:63] Setting libtpu version to: 0.0.50
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
[ 7m32s] Pre-computing sketch data: config (36s) | main: … > cli.run_survey > sketch._extract_config_info > sketch._extract_config_hybrid > sketch._extract_config_embedding:1936
[ 8m03s] Pre-computing sketch data: file mentions (8s) | main: … > cli.cmd_sketch > sketch.generate_sketch > sketch._discover_or_survey > cli.run_survey:13353
[warn] 2 axis_conformance validation violation(s); see validation_report in artifact
[warn] 2 id_format validation violation(s); see validation_report in artifact
[passes] 8 of 102 emitted nothing: unreported=5, candidates_unresolved=3; 52 more routine (nothing to find, or opt-in backend off) (per-pass detail in analysis_runs[].silence_reason)
[ 8m33s] Sketch: Loading cached analysis (7s) | main: … > sketch._TreeLoc._measure.<genexpr> > sketch._TreeLoc.__call__ > sketch._TreeLoc._measure > sketch._file_sloc:3060
[ 9m03s] Sketch: Embedding additional files (28s) | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[ 9m33s] Sketch: Embedding additional files (58s) · Embedding files 64/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[10m03s] Sketch: Embedding additional files (1m28s) · Embedding files 128/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[10m33s] Sketch: Embedding additional files (1m58s) · Embedding files 192/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[11m03s] Sketch: Embedding additional files (2m28s) · Embedding files 256/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[11m33s] Sketch: Embedding additional files (2m58s) · Embedding files 320/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
[12m03s] Sketch: Embedding additional files (3m28s) · Embedding files 384/427 | main: … > sketch._generate_sketch_impl > sketch._format_additional_files > sketch._select_additional_files > sketch_embeddings.batch_embed_files:1945
Complete in 12m14s                     
Slowest phases: Running analyzers 4m10s, Sketch: Embedding additional files 3m37s, Running linkers 1m31s
ℹ️  Secret scan complete (best-effort, not exhaustive).
# hypergumbo

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+.

## Overview
Python (89%), Markdown (5%), Yaml (3%), Bash (2%)
2,379 files    (  944 non-test + 1,435 test)
~928,098 LOC (~379,495 non-test + ~548,603 test)

Vendored third-party code analysed as tier 3, not counted in the language breakdown: packages/hypergumbo-core/src/hypergumbo_core/vendor/ (c, cpp, json; 13 files). To skip: --exclude <dir>.

## Structure

` ` `
     hypergumbo/
  8  ├── .ci
  8  │   └── .gitignore
  3k ├── .githooks
304  │   ├── brand-scrub.sh
  2k │   └── [and 9 other items]
813k ├── packages
433k │   ├── hypergumbo-core
168k │   │   ├── src
168k │   │   │   └── hypergumbo_core
  4  │   │   │       ├── __main__.py
 13k │   │   │       ├── cli.py
  2k │   │   │       ├── ir.py
153k │   │   │       └── [and 126 other items]
265k │   │   ├── tests
 18k │   │   │   ├── test_framework_patterns.py
247k │   │   │   └── [and 562 other items]
131  │   │   └── [and 2 other items]
 71k │   ├── hypergumbo-tracker
 23k │   │   ├── src
 23k │   │   │   └── hypergumbo_tracker
  4k │   │   │       ├── cli.py
 20k │   │   │       └── [and 37 other items]
 48k │   │   └── [and 4 other items]
309k │   └── [and 6 other items]
 26k ├── scripts
  3k │   ├── lib
  2k │   │   ├── forgejo-api.sh
994  │   │   └── [and 4 other items]
 23k │   └── [and 69 other items]
 38k ├── tests
894  │   ├── test_agent_supervisor.py
 37k │   └── [and 142 other items]
107  ├── .gitignore
 75  ├── .gitleaks.toml
116  ├── conftest.py
147  ├── pyproject.toml
 12  ├── setup.py
 51k └── [and 12 other items]
` ` `
Counts and LOC cover only files with at least one detected symbol; files with none, such as JSON data or extensionless scripts, are left out.

## Frameworks

- protobuf
- pytest
- starlette

## Tests

1435 test files · cargo test, pytest, unittest

*~63% estimated coverage (4700/7446 functions called by tests)*

## Configuration

` ` `
packages/hypergumbo/pyproject.toml: name: hypergumbo; version: 9.2.0; license: { text =
packages/hypergumbo-core/pyproject.toml: name: hypergumbo-core; version: 9.2.0; license: { text =
packages/hypergumbo-lang-common/pyproject.toml: name: hypergumbo-lang-common; version: 9.2.0; license: { text =
packages/hypergumbo-lang-extended1/pyproject.toml: name: hypergumbo-lang-extended1; version: 9.2.0; license: { text =
packages/hypergumbo-lang-mainstream/pyproject.toml: name: hypergumbo-lang-mainstream; version: 9.2.0; license: { text =
packages/hypergumbo-lang-rust-analyzer/pyproject.toml: name: hypergumbo-lang-rust-analyzer; version: 9.2.0; license: { text =
packages/hypergumbo-lang-scip-python/pyproject.toml: name: hypergumbo-lang-scip-python; version: 9.2.0; license: { text =
packages/hypergumbo-tracker/pyproject.toml: name: hypergumbo-tracker; version: 0.11.0; license: { text =
LICENSE: AGPL, MPL

--- Additional context (semantic) ---
  > # SCIP wire-format decoding (vendored scip_pb2.py imports google.protobuf) "protobuf~=6.33", ]
  > [tool.ruff] target-version = "py310"

  > "Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only", ]
  > line-length = 100 src = ["src", "tests"]

  > "tree-sitter-typescript~=0.23.2", "tree-sitter-php~=0.24.1", "tree-sitter-c~=0.24.1",
  > "tree-sitter-ruby~=0.23.1", "tree-sitter-kotlin~=1.1.0", "tree-sitter-swift~=0.7.4",

  > ] dependencies = [ "ruamel.yaml>=0.18",
  > filterwarnings = [] pythonpath = ["tests"]







` ` `

## Entry Points

### CLI & Scripts

- `main` (Python main()) — Primary CLI entry point.
- `main` (Python main()) — `packages/hypergumbo-core/src/hypergumbo_core/cli.py`
- `packages/hypergumbo-core/src/hypergumbo_core/__main__.py` (Python script (if __name__ == '__main__')) — `packages/hypergumbo-core/src/hypergumbo_core/__main__.py`
- `packages/hypergumbo/src/hypergumbo/__main__.py` (Python script (if __name__ == '__main__')) — Allow running hypergumbo as a module: python -m hypergumbo.
- `main` (Python main()) — CLI entry point. Returns exit code.
- `main` (Python main()) — `packages/hypergumbo-lang-scip-python/recorded/sample_project/pkg/app.py`
- `htrac` (Python CLI: htrac) — `packages/hypergumbo-tracker/pyproject.toml`
- `hypergumbo` (Python CLI: hypergumbo) — `packages/hypergumbo/pyproject.toml`
- `hypergumbo-tracker` (Python CLI: hypergumbo-tracker) — `packages/hypergumbo-tracker/pyproject.toml`
- `hypergumbo-tracker-textconv` (Python CLI: hypergumbo-tracker-textconv) — `packages/hypergumbo-tracker/pyproject.toml`
- `main` (Rust main()) — `packages/hypergumbo-lang-rust-analyzer/recorded/aardvark_dns/crate/src/main.rs`
- `scripts/measure-ctor-root-typing-ab.py` (Python script (if __name__ == '__main__')) — A/B the constructor-ROOT receiver typing at the call-emission site.
- `scripts/measure-module-matches-arm2-ab.py` (Python script (if __name__ == '__main__')) — A/B arm 2 of ``_module_matches``: case VALUE against PARENT AGREEMENT.
- `main` (Python main()) — `scripts/measure-analyzer-scaling.py`
- `main` (Python main()) — `scripts/measure-parse-redundancy.py`
- ... and 31 more

### HTTP Routes

- `_api_add_item` (HTTP route /api/items) — POST /api/items — create a new item.
- `_api_discuss_item` (HTTP route /api/items/{item_id}/discuss) — POST /api/items/{id}/discuss — add a discussion message.
- `_api_update_item` (HTTP route /api/items/{item_id}/update) — POST /api/items/{id}/update — update item fields.
- `_api_get_item` (HTTP route /api/items/{item_id}) — GET /api/items/{id} — get a single item by ID.
- `_api_ready` (HTTP route /api/ready) — GET /api/ready — list actionable items sorted by priority.
- `POST /api/items` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:170-197:_api_add_item:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `_health` (HTTP route /health) — Health check endpoint. Returns server status and PID.
- `POST /api/items/{item_id}/discuss` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:219-236:_api_discuss_item:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `POST /api/items/{item_id}/update` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:200-216:_api_update_item:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `GET /api/items` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:139-145:_api_list_items:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `GET /api/items/{item_id}` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:148-158:_api_get_item:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `GET /api/ready` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:161-167:_api_ready:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`
- `GET /health` → `python:packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py:130-136:_health:function` — `packages/hypergumbo-tracker/src/hypergumbo_tracker/serve.py`

### WebSocket Handlers

- `_ws_handler` (WS /ws) — WebSocket endpoint for real-time tracker state sync.

### Library API

- `TrackerSet` (Library export: TrackerSet) — Multi-tier unified view over canonical, workspace, and stealth Stores.
- `migrate` (Library export: migrate) — Run the full migration pipeline.
- `validate_all` (Library export: validate_all) — Validate all ops files across all tiers.
- `Cache` (Library export: Cache) — SQLite read cache for a single tier's Store.
- `validate_ops_file` (Library export: validate_ops_file) — Validate a single ops file against the tracker config.
- `translate_scip_to_hg` (Library export: translate_scip_to_hg) — Parse *scip_bytes* and return ``(symbols, edges)`` with parity.
- `translate_scip_python_to_hg` (Library export: translate_scip_python_to_hg) — Parse ``scip_bytes`` and return ``(symbols, edges)``.
- `load_function_summaries` (Library export: load_function_summaries) — Load all function summaries from YAML files in a directory.
- `try_analyze_with_rust_analyzer` (Library export: try_analyze_with_rust_analyzer) — Run rust-analyzer + SCIP translate on *workspace* as a :class:`ScipAttempt`.
- `parse_scip_symbol` (Library export: parse_scip_symbol) — Parse a SCIP symbol string into a :class:`ScipSymbol`.
- `truncate_to_tokens` (Library export: truncate_to_tokens) — Truncate text to approximately fit within token budget.
- `reassign_rust_stable_ids` (Library export: reassign_rust_stable_ids) — Return *symbols* with Rust function ``stable_id``s rust.py-compatible.
- `run_rust_analyzer_scip` (Library export: run_rust_analyzer_scip) — Run ``rust-analyzer scip`` on *workspace* and return the SCIP bytes.
- `allocate_language_budget` (Library export: allocate_language_budget) — Allocate symbol budget proportionally by language symbol count.
- ... and 69 more

### Other

- `.githooks/brand-scrub.sh` (Shell script (.githooks/brand-scrub.sh)) — `.githooks/brand-scrub.sh`
- `.githooks/pre-commit` (Shell script (.githooks/pre-commit)) — `.githooks/pre-commit`
- `.githooks/commit-msg` (Shell script (.githooks/commit-msg)) — `.githooks/commit-msg`
- `.githooks/post-checkout` (Shell script (.githooks/post-checkout)) — `.githooks/post-checkout`
- `.githooks/post-merge` (Shell script (.githooks/post-merge)) — `.githooks/post-merge`
- `.githooks/prepare-commit-msg` (Shell script (.githooks/prepare-commit-msg)) — `.githooks/prepare-commit-msg`
- `.githooks/reference-transaction` (Shell script (.githooks/reference-transaction)) — `.githooks/reference-transaction`
- `.githooks/pre-push` (Shell script (.githooks/pre-push)) — `.githooks/pre-push`
- `.githooks/repair-agent-perms.sh` (Shell script (.githooks/repair-agent-perms.sh)) — `.githooks/repair-agent-perms.sh`
- `scripts/auto-pr` (Shell script (scripts/auto-pr)) — `scripts/auto-pr`
- `scripts/lib/forgejo-api.sh` (Shell script (scripts/lib/forgejo-api.sh)) — `scripts/lib/forgejo-api.sh`
- `scripts/smart-test` (Shell script (scripts/smart-test)) — `scripts/smart-test`
- `scripts/ci-debug` (Shell script (scripts/ci-debug)) — `scripts/ci-debug`
- `scripts/loop-toggle` (Shell script (scripts/loop-toggle)) — `scripts/loop-toggle`
- ... and 24 more


## Data Models

`packages/hypergumbo-core/src/hypergumbo_core/ir.py`:
  - `Symbol` (Python @dataclass)
  - `Span` (Python @dataclass)
  - `AnalysisRun` (Python @dataclass)
  - `Edge` (Python @dataclass)
  - `ExternalRef` (Python @dataclass)
  - `UsageContext` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`:
  - `FileAnalysis` (Python @dataclass)
  - `AnalysisResult` (Python @dataclass)
  - `ArityFlags` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`:
  - `LinkerResult` (Python @dataclass)
  - `LinkerContext` (Python @dataclass)
  - `LinkerRequirement` (Python @dataclass)
  - `LinkerActivation` (Python @dataclass)
  - `RegisteredLinker` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/cfg.py`:
  - `DefUseResult` (Python @dataclass)
  - `CfgNodeMapping` (Python @dataclass)
  - `DdgEdge` (Python @dataclass)
  - `CfgStatement` (Python @dataclass)
  - `_PartialCfg` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`:
  - `LookupResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py`:
  - `Pattern` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/taint.py`:
  - `TaintFlowFinding` (Python @dataclass)
  - `TaintCatalog` (Python @dataclass)
  - `TaintSource` (Python @dataclass)
  - `TaintSink` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py`:
  - `IoBoundaryCatalog` (Python @dataclass)
  - `BoundaryMapEntry` (Python @dataclass)
  - `IoChain` (Python @dataclass)
  - `BoundaryMap` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py`:
  - `DependencyManifest` (Python @dataclass)
  - `FileClassification` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/sketch.py`:
  - `SketchStats` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/limits.py`:
  - `Limits` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py`:
  - `CompiledItem` (Python @dataclass)
  - `TrackerConfig` (Python @dataclass)
  - `DiscussionEntry` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`:
  - `RegisteredAnalyzer` (Python @dataclass)
  - `MergeAnchor` (Python @dataclass)
  - `MergeDisjoint` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/ddg_build.py`:
  - `LanguageDdgSpec` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/tag_catalog.py`:
  - `TagCatalogEntry` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/_name_args.py`:
  - `NameArg` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/_view_template_core.py`:
  - `TemplateCandidate` (Python @dataclass)
  - `TemplateRenderEmission` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/slice.py`:
  - `SliceQuery` (Python @dataclass)
  - `SliceResult` (Python @dataclass)
`packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/_pyscope.py`:
  - `ScopeStack` (Python @dataclass)
  - `Scope` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/compact.py`:
  - `CompactConfig` (Python @dataclass)
  - `OmittedSummary` (Python @dataclass)
  - `IncludedSummary` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/discovery.py`:
  - `TraceDeps` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/nav_history.py`:
  - `NavigationHistory` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/validation.py`:
  - `ValidationResult` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/annotations.py`:
  - `ArrowAnnotation` (Python @dataclass)
  - `RectAnnotation` (Python @dataclass)
  - `LabelAnnotation` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/function_summaries/__init__.py`:
  - `FunctionSummary` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/profile.py`:
  - `RepoProfile` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/finalize.py`:
  - `FinalizeContext` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/arbitration.py`:
  - `ArbitrationPolicy` (Python @dataclass)
`scripts/generate_schema_lib.py`:
  - `ClassSpec` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/datamodels.py`:
  - `DataModel` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/event_sourcing.py`:
  - `EventPattern` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/user_config.py`:
  - `LayeredConfig` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/verify_claims.py`:
  - `BoundaryCoverage` (Python @dataclass)
  - `LoadedCatalogueFile` (Python @dataclass)
  - `SinkZoneGap` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/linkers/http.py`:
  - `HttpClientCall` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/overload_arity.py`:
  - `DeclaredArity` (Python @dataclass)
`packages/hypergumbo-tracker/src/hypergumbo_tracker/setup.py`:
  - `CheckResult` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/scip/descriptor.py`:
  - `ScipDescriptor` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/dataflow_scope.py`:
  - `LanguageDataflowScope` (Python @dataclass)
`packages/hypergumbo-core/src/hypergumbo_core/yaml_catalogs.py`:
  - `CatalogSpec` (Python @dataclass)
- ... and 286 more data models

## Source Files

- `packages/hypergumbo-core/src/hypergumbo_core/ir.py` — Internal Representation (IR) for code analysis.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py` — Base classes and utilities for language analyzers.
- `packages/hypergumbo-core/src/hypergumbo_core/discovery.py` — File discovery: excludes, installed-dependency detection, locales, ambiguous ext
- `packages/hypergumbo-core/src/hypergumbo_core/overload_arity.py` — Choose among same-named callables by the call's argument count.
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py` — Linker registry for dynamic dispatch.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/cyclomatic.py` — Grammar-agnostic McCabe cyclomatic-complexity walker + decision-point registry.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py` — Analyzer registry for decorator-based dynamic dispatch.
- `packages/hypergumbo-core/src/hypergumbo_core/cli.py` — Command-line interface for hypergumbo.
- `packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py` — I/O boundary analysis — catalogue, matching, tagging and map (ADR-0016).
- `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py` — Unified symbol resolution with pluggable matching strategies.
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/_text_filters.py` — Infrastructure linker helper: mask docstring/comment regions for regex linkers.
- `packages/hypergumbo-core/src/hypergumbo_core/paths.py` — Centralized path handling utilities for hypergumbo.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py` — Python AST analysis pass.
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/store.py` — Store for the hypergumbo tracker — YAML I/O, compile, CRUD, Lamport clock.
- `packages/hypergumbo-core/src/hypergumbo_core/sketch.py` — Token-budgeted Markdown sketch generation.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py` — JavaScript/TypeScript/Svelte/Vue analysis pass using tree-sitter.
- `packages/hypergumbo-core/src/hypergumbo_core/pass_silence.py` — Canonical pass-silence-reason axis (INV-bikaj / INV-hujog, arc T6).
- `packages/hypergumbo-core/src/hypergumbo_core/taint.py` — Taint catalog loading and taint-flow propagation (ADR-0017 Phases 1-2).
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py` — Java analysis pass using tree-sitter-java.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py` — Go analysis pass using tree-sitter-go.
- `packages/hypergumbo-core/src/hypergumbo_core/verify_claims.py` — Security claim verification against I/O boundary and taint-flow analysis.
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/tui.py` — Textual TUI for the hypergumbo tracker.
- `scripts/lib/cohort_ledger.py` — A cohort ledger: every multi-repo instrument states what it COVERED of what it w
- `packages/hypergumbo-core/src/hypergumbo_core/profile.py` — Repo profile detection - language and framework heuristics.
- `packages/hypergumbo-core/src/hypergumbo_core/dataflow.py` — YAML-driven dataflow classification for edges (ADR-0015).
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py` — C++ analysis pass using tree-sitter-cpp.
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/models.py` — Data model for the hypergumbo tracker.
- `packages/hypergumbo-tracker/src/hypergumbo_tracker/cli.py` — CLI entry points for hypergumbo-tracker.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/c.py` — C analysis pass using tree-sitter-c.
- `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py` — Elixir analysis pass using the tree-sitter-language-pack Elixir grammar.
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py` — Ruby analysis pass using tree-sitter-ruby.
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py` — Analyzer orchestration + the stable analyzer-dispatch import points.
- `scripts/dead-code-prospector-run.py` — Lightweight one-shot dead-code-maybe prospecting run.
- `packages/hypergumbo-core/src/hypergumbo_core/taxonomy.py` — File taxonomy classification (ADR-0004).
- `packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py` — Kotlin analysis pass using tree-sitter-kotlin.
- ... and 1863 more files

## Key Symbols

*★ = centrality ≥ 50% of max*

### `packages/hypergumbo-core/src/hypergumbo_core/ir.py`
- `Symbol` (class) ★ — A code symbol (function, class, etc.) detected by analysis.
- `Edge.create(cls, src: str, dst: str, edge_type: str, line: int, origin: str | List[str]='', origin_run_id: str='', evidence_type: str='ast_call_direct', confidence: float | None=None, confidence_source: str | None=None, rank_score: float |…) -> 'Edge'` (method) ★ — Create an Edge with auto-generated ID and edge_key.
- `Span` (class) ★ — Source code location with line and column info.
- `AnalysisRun.create(cls, pass_id: str, version: str, config_fingerprint: Optional[str]=None, toolchain: Optional[Dict[str, str]]=None, repo_fingerprint: Optional[str]=None, pass_version: str='') -> 'AnalysisRun'` (method) ★ — Create a new AnalysisRun with a unique execution_id.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py`
- `TreeSitterAnalyzer` (class) ★ — Base class for tree-sitter-based language analyzers.
- `iter_tree(root: 'tree_sitter.Node') -> Iterator['tree_sitter.Node']` (function) ★ — Iterate over all nodes in a tree-sitter tree without recursion.

### `packages/hypergumbo-core/src/hypergumbo_core/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] | None=None, max_files: int | None=None, max_file_bytes: int | None=None, on_file_skipped: Callable[, None] | None=None) -> Iterator[Path]` (function) ★ — Find files matching patterns while respecting exclude rules.

### `packages/hypergumbo-core/src/hypergumbo_core/overload_arity.py`
- `declared_arity_stable_id(kind: str, lang: str, path: str, name: str, qualified_name: str, arity: Optional[DeclaredArity], decorators: str='', containing_stable_id: str='', result_type: str='') -> str` (function) — The ADR-0035 producer stable_id of a callable whose arity came from meta.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py`
- `register_linker(name: str, priority: int=50, description: str='', requirements: list[LinkerRequirement] | None=None, activation: LinkerActivation | None=None, pass_label: str='', backend: str='', languages: list[str] | None=Non…) -> Callable[, LinkerFunc]` (function) — Decorator to register a linker function.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/cyclomatic.py`
- `compute_cyclomatic_complexity(node: 'tree_sitter.Node', language: str) -> Optional[int]` (function) ★ — Compute McCabe cyclomatic complexity for a callable subtree.

### `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py`
- `register_analyzer(name: str, priority: int=50, supports_max_files: bool=False, capture_symbols_as: str | None=None, description: str='', pass_label: str='', backend: str='', languages: list[str] | None=None, language_state: str…) -> Callable[, AnalyzerFunc]` (function) — Decorator to register an analyzer function.

### `packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py`
- `NameResolver` (class) — Symbol resolver for string-keyed registries (dict[str, Symbol]).

### `packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py`
- `load_catalog(language: str, overlay_paths: Optional[Sequence[Path]]=None, include_defaults: bool=True) -> IoBoundaryCatalog` (function) — Load the I/O primitive catalog for a language.

### `packages/hypergumbo-core/src/hypergumbo_core/linkers/_text_filters.py`
- `read_masked_source(file_path: Path, encoding: str='utf-8', errors: str='replace', language: Optional[str]=None) -> str` (function) — Read ``file_path`` and return its content with doc regions masked.

### `packages/hypergumbo-core/src/hypergumbo_core/pass_silence.py`
- `silence_reason_for_candidates(candidates: 'Sized') -> str` (function) — A pass body's positive claim about what its own scan found.

### `packages/hypergumbo-lang-rust-analyzer/recorded/aardvark_dns/crate/src/server/serve.rs`
- `parse_resolv_conf(content: &str) -> AardvarkResult<Vec<SocketAddr>>` (function)

(... and 10663 more symbols across 467 other files)

## Additional Files

- `README.md`
- `docs/INTEL_MAC.md`
- `docs/adr/0017-taint-zone-dataflow.md`
- `docs/hypergumbo-self-catalog/user_state_sinks.yaml`
- `SECURITY.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/hypergumbo-self-catalog/user_cache_sinks.yaml`
- `docs/example-output.md`
- ... and 419 more files

## Additional Files Content

------------------- START of README.md ---------------------
` ` ` `
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### hypergumbo

[![PyPI](https://img.shields.io/pypi/v/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)
[![License](https://img.shields.io/pypi/l/hypergumbo.svg)](https://pypi.org/project/hypergumbo/)

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase.

` ` `bash
pip install hypergumbo
` ` `

> Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing.

> Installing on a personal machine? Read [Security and trust](https://github.com/iterabloom/hypergumbo/blob/dev/README.md#security-and-trust-as-of-2026-11) first: what hypergumbo reads, writes and downloads, which commands run other code, and how its claims are checked.

> Intel Mac users: Some tree-sitter packages lack x86_64 wheels. See [docs/INTEL_MAC.md](https://github.com/iterabloom/hypergumbo/blob/dev/docs/INTEL_MAC.md) for a Docker-based workaround.

` ` `bash
git clone https://github.com/iterabloom/hypergumbo
hypergumbo hypergumbo/
` ` `

Output:

` ` `bash
### hypergumbo

hypergumbo is a local-first CLI that generates surveys and sketches from source code. The goal of this project is to efficiently help developers and LLMs understand a codebase. > Requires Python 3.10+. For optional extras (embeddings, gitleaks, grammars), run `hypergumbo add-extras` after installing. > Intel Mac users:

> **Audited IO surface.** hypergumbo's own filesystem / network / subprocess activity is enumerated per CLI subcommand category in [SECURITY.md](SECURITY.md), with machine-verifiable claims authored at `docs/hypergumbo.claims.yaml`. Re-run the audit locally via `hypergumbo verify-claims --claims docs/hypergumbo.claims.yaml`. The wrapper-function discipline used to make path-bounded claims structurally verifiable is documented as the recommended pattern for any hypergumbo user (any language) wanting the same precision.

[...truncated...]
` ` ` `
------------------- END of README.md -----------------------

------------------- START of docs/INTEL_MAC.md -------------
` ` ` `
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### Running hypergumbo on Intel Macs

Some tree-sitter grammar packages lack pre-built wheels for Intel Macs (x86_64 Darwin), causing installation failures. This guide provides a Docker-based workaround that mimics `pipx` behavior.

**Limitation:** Lean and Wolfram analyzers require building grammars from source inside the container, which this setup does not support. All other analyzers work.

#### How It Works

1. A Docker image with hypergumbo pre-installed
2. A shell shim at `~/.local/bin/hypergumbo` that runs the container transparently

#### Installation

##### 1. Build the Docker image (one-time)

` ` `bash
mkdir -p ~/.local/share/hypergumbo-docker
cat > ~/.local/share/hypergumbo-docker/Dockerfile <<'EOF'
FROM python:3.11 AS builder
RUN pip install -U pip wheel
RUN pip wheel --wheel-dir /wheels "hypergumbo==2.0.2"

FROM python:3.11-slim
COPY --from=builder /wheels /wheels
RUN pip install -U pip \
 && pip install --no-index --find-links=/wheels "hypergumbo==2.0.2" \
 && rm -rf /wheels
WORKDIR /work
ENTRYPOINT ["hypergumbo"]
CMD ["--help"]
EOF

docker build -t hypergumbo:2.0.2 ~/.local/share/hypergumbo-docker
` ` `

##### 2. Create the PATH shim

` ` `bash
mkdir -p ~/.local/bin ~/.cache/hypergumbo-docker ~/.config/hypergumbo-docker

cat > ~/.local/bin/hypergumbo <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

IMAGE="${HYPERGUMBO_IMAGE:-hypergumbo:2.0.2}"

### Allocate a TTY only if we're in one
TTY=()
if [[ -t 0 && -t 1 ]]; then TTY=(-it); fi

### Host-side cache/config (writable)
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache/hypergumbo-docker}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config/hypergumbo-docker}"

### Safety: default to read-only repo unless explicitly disabled
WORK_MOUNT_MODE="ro"
if [[ "${HYPERGUMBO_DOCKER_RO:-1}" == "0" ]]; then
  WORK_MOUNT_MODE="rw"
fi

### Make sure cache/config exist
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

### Mount strategy:
### - HOME read-only (so you can point at things in ~)
### - repo (PWD) with chosen rw/ro
### - cache/config writable
### - /Volumes read-only (optional but handy on macOS)
docker run --rm "${TTY[@]}" \
  --user "$(id -u):$(id -g)" \
  --network=none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  -e HOME="$HOME" -e USER="$USER" \
  -e XDG_CACHE_HOME="$XDG_CACHE_HOME" \
  -e XDG_CONFIG_HOME="$XDG_CONFIG_HOME" \
  -v "$HOME":"$HOME:ro" \
  -v "$PWD":"$PWD:$WORK_MOUNT_MODE" \
  -v "$XDG_CACHE_HOME":"$XDG_CACHE_HOME:rw" \
  -v "$XDG_CONFIG_HOME":"$XDG_CONFIG_HOME:rw" \
  -v /Volumes:/Volumes:ro \
  -w "$PWD" \
  "$IMAGE" "$@"
EOF

chmod +x ~/.local/bin/hypergumbo
` ` `

Ensure `~/.local/bin` is in your PATH (add to `~/.zshrc` or `~/.bashrc` if needed):

` ` `bash
export PATH="$HOME/.local/bin:$PATH"
` ` `

#### Usage

` ` `bash
hypergumbo --help
hypergumbo .
hypergumbo /Users/you/some/repo -t 500
` ` `

##### Write-enabled mode

By default, the repo is mounted read-only. For commands that write output files:

` ` `bash
HYPERGUMBO_DOCKER_RO=0 hypergumbo run --out results.json
` ` `

##### Upgrading

To upgrade to a new version, update the version in both the Dockerfile and shim, then rebuild:

` ` `bash
### Edit ~/.local/share/hypergumbo-docker/Dockerfile (change version)
### Edit ~/.local/bin/hypergumbo (change HYPERGUMBO_IMAGE default)
docker build -t hypergumbo:X.Y.Z ~/.local/share/hypergumbo-docker
` ` `

#### Uninstallation

` ` `bash
rm -f ~/.local/bin/hypergumbo
docker rmi -f hypergumbo:2.0.2 2>/dev/null || true
rm -rf ~/.local/share/hypergumbo-docker ~/.cache/hypergumbo-docker ~/.config/hypergumbo-docker
` ` `

#### Troubleshooting

##### "Operation not permitted" mount errors

Docker Desktop requires explicit permission to mount certain paths. Go to **Docker Desktop → Settings → Resources → File Sharing** and add `$HOME` and `/Volumes` to the allowed list.
` ` ` `
------------------- END of docs/INTEL_MAC.md ---------------

                     How Representative Is This Sketch?                     
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 8,000t ┃ 32,000t ┃ 128,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Entry Points             │    33% │     33% │      33% │ confidence mass │
│ Data Models              │    21% │     28% │      28% │ confidence mass │
│ Source Files             │    16% │     29% │      47% │ symbol mass     │
│ Key Symbols              │   3.3% │    6.0% │     8.5% │ symbol mass     │
│ Additional Files         │    24% │     36% │      44% │ symbol mass     │
│ Source Files Content     │      - │    5.1% │      10% │ symbol mass     │
│ Additional Files Content │   8.1% │     30% │      30% │ symbol mass     │
└──────────────────────────┴────────┴─────────┴──────────┴─────────────────┘

hypergumbo also cached comparison sketches:
  4x budget (32,000t):  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/sketch.32000.withsource.md
  16x budget (128,000t): /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/sketch.128000.withsource.md


[hypergumbo sketch] Generated 5 artifact(s)
  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/survey.16k.json
  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/survey.4k.json
  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/survey.64k.json
  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/survey.json
  /root/.cache/hypergumbo/a3e27fb44ac17373/results/3f5d7ab728aa7327/16809fafd50762dc/sketch.8000.withsource.md
  Output: stdout
  Embeddings cached: /root/.cache/hypergumbo/a3e27fb44ac17373/embeddings
```
