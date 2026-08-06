<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Hypergumbo Spec

Status: living document.

## Implementation Status Legend

| Icon | Status | Meaning |
|------|--------|---------|
| ⬜ | todo | Planned, not yet started |
| 🟨 | in progress | Currently being worked on |
| 🟧 | blocked | Blocked on external dependency or decision |
| 🟩 | done | Implemented and tested |
| 🟪 | needs design | Requires more design thinking before implementation |
| ⬛ | won't do | Decided against / out of scope |

*Use `grep "🟨"` to find in-progress items, etc.*

## Table of Contents

| § | Title |
|---|-------|
| 0 | [One-sentence summary](#0-one-sentence-summary) |
| 1 | [Goals](#1-goals) |
| 2 | [Non-goals](#2-non-goals) |
| 3 | [User experience (CLI)](#3-user-experience-cli) |
| 4 | [Supported stacks](#4-supported-stacks) |
| 5 | [Architecture](#5-architecture) |
| 6 | [Internal representation](#6-internal-representation) |
| 7 | [Linkers](#7-linkers) |
| 8 | [Entrypoint detection](#8-entrypoint-detection) |
| 9 | [Behavior map JSON](#9-behavior-map-json) |
| 10 | [Sketch output](#10-sketch-output) |
| 11 | [Slicing behavior](#11-slicing-behavior) |
| 12 | [Confidence scoring](#12-confidence-scoring) |
| 13 | [Output reproducibility](#13-output-reproducibility) |
| 14 | [Supply chain classification](#14-supply-chain-classification) |
| 15 | [File role classification](#15-file-role-classification) |
| 16 | [Testing & quality bar](#16-testing--quality-bar) |
| 17 | [Error handling](#17-error-handling) |
| 18 | [Known limitations](#18-known-limitations) |
| 19 | [Autonomous governance](#19-autonomous-governance-adr-0008) |
| 20 | [Future work](#20-future-work) |
| A | [Release lifecycle & support](#appendix-a-release-lifecycle--support) |
| B | [Telemetry & privacy](#appendix-b-telemetry--privacy) |
| C | [Schema compatibility contract](#appendix-c-schema-compatibility-contract) |
| D | [Capsule system history](#appendix-d-capsule-system-history) |

## 0) One-sentence summary
A local-first CLI that helps developers and AI agents understand an unfamiliar codebase by analyzing its structure and emitting a **repo survey**—a JSON graph of symbols, call edges, routes, and framework patterns with confidence scores and provenance tracking.

## 1) Goals
* 🟩 **Internal IR with views**: Parsers emit to an internal representation; public outputs are compiled views (enables future typed passes without breaking schema).
* 🟩 **Provenance tracking**: Every node/edge records which analyzer pass created it, with unique execution identifiers enabling quality assessment and mixed-fidelity analysis.
* 🟩 **Machine-readable provenance**: Confidence scores and edge evidence use structured fields (not human-readable strings). This enables programmatic filtering and multi-pass merging.
* 🟩 **Agent-ready output**: deterministic JSON graph + "feature slices" so an agent can fetch only relevant code.
* 🟩 **Fast iteration**: simple architecture, small dependency surface, fixtures-driven tests.
* 🟩 **Local-first execution**: analysis runs offline by default (no network, no API keys required).

For goals that were considered and rejected, see [Appendix D](#appendix-d-capsule-system-history).

## 2) Non-goals
* No deep type-resolution / interprocedural dataflow correctness guarantees for all languages. Per ADR-0017, structural taint-flow analysis (call-graph BFS, labeled `approximate`) is available for all languages; DDG-backed precision extractors are added per-language where demand justifies it.
* No accounts, ratings, or social features.
* No automatic PR fixing, no code editing, no CI annotations beyond "export JSON."
* No attempt to support every language *deeply*—broad coverage via tree-sitter (100+ languages; see [LANGUAGES.md](LANGUAGES.md)), deep call-graph extraction for a smaller set. See [§4 Supported stacks](#4-supported-stacks).
* No incremental analysis daemon (full re-analysis is acceptable).
* No LLM-generated analyzer code.

## 3) User experience (CLI)

**Key principle:** Analysis execution requires no network or API keys (by default). Output is reproducible at the **L2** level — the semantic graph (nodes, edges, `stable_id`s, `run_signature`) is stable given the same repo state; per-run metadata (`execution_id`, wall-clock timestamps, and the `analysis_runs[]` ordering) is not byte-identical (see the `reproducibility_context` block). See [Appendix B](#appendix-b-telemetry--privacy) for the full privacy and telemetry policy.

### Install
* `pipx install hypergumbo` (primary, includes all language analyzers)
* `pip install hypergumbo` (secondary)
* `pip install hypergumbo[embeddings]` (optional embedding-based config extraction)
* `pipx install 'hypergumbo[rust-analyzer]'` (optional SCIP-backed Rust analysis via rust-analyzer; required for `--backend rust-analyzer` to actually engage)

### Commands

🟩 **`hypergumbo [path] [-t tokens]`** (default mode)
Generates a token-budgeted Markdown sketch to stdout. Optimized for pasting into LLM chat interfaces.
* If no subcommand is given, assumes sketch mode.
* `-t N` limits output to approximately N tokens.
* `--with-source` appends full source file contents after the sketch (ordered by symbol importance density, skips files under 5 LOC)

🟩 **`hypergumbo explain <symbol> [--with-source] [-t tokens] [-x] [--provenance] [--language L] [--file SUFFIX] [--first] [--limit N]`**
Shows detailed info about a symbol (function, class, etc.) and its callers/callees.
* Always shows `Origin:` line with the pass(es) that created the symbol (PROV wasAttributedTo)
* Caller/callee lines annotated with edge type (e.g., `[imported_call]`, `[calls]`)
* `--provenance` shows derivation chains per edge: resolves `Edge.derived_from` IDs to `name (kind)` pairs (PROV wasDerivedFrom). See ADR-0030 for the PROV vocabulary mapping.
* `--with-source` shows source code for the symbol, callers, and callees:
  - Symbol source shown first
  - "Called by" list, then caller sources (ordered by in-degree descending)
  - "Calls" list, then callee sources (ordered by in-degree descending)
  - Module-level calls show only the single call line
  - Deduplicates when same symbol appears as both caller and callee
* `-t N` limits source output to approximately N tokens. When budget exceeded, omits sources one-at-a-time in priority order: module-level first, then ascending in-degree (least important first)
* `-x` excludes callers/callees from test files
* A name matching symbols in more than one file is **ambiguous**: `explain` errors and lists the candidates on stderr (matching `slice`'s policy, INV-nogof), instead of silently dumping every match. `--language L` / `--file SUFFIX` narrow the match pool to disambiguate; `--first` accepts the top match; `--limit N` caps how many sections print for non-ambiguous same-file duplicates (WI-nanut)
* Registered-but-unstyled edge types surface their registry description; an edge type registered nowhere is labelled `unrecognized edge type '<type>'`; a substrate whose `schema_version` differs from this build prints a field-presence drift warning (WI-dazob)

🟩 **`hypergumbo survey [path] [--out survey.json]`**
Analyzes the repo and emits a survey. No initialization required—works directly on any repo.

🟩 **`hypergumbo slice --entry <symbol|file|route> [--out slice.<entry>.json]`**
Produces a reduced subgraph suitable for LLM context. Default output filename includes a sanitized entry name to prevent overwrites when slicing different symbols.

🟩 **`hypergumbo catalog [--show-all]`**
Shows available language analyzers and which ones are suggested for the current repo. Useful for discovering what hypergumbo can analyze.

🟩 **`hypergumbo test-coverage [path] [--format text|json]`**
Estimates test coverage via static analysis (no code execution). Reports hot spots (functions called by many tests, ranked by tests/LOC) and cold spots (untested functions). Filter with `--min-tests`, `--max-tests`, `--top`. The candidate universe is the *production functions* — non-test function/method symbols, excluding ADR-0031 synthetic linker stand-ins — the same `production_callables` denominator `dead-code-maybe` uses (`dead = production - reachable`).

🟩 **`hypergumbo io-boundaries [path] [--format text|json]`** (ADR-0016)
Identifies call edges that reach I/O primitives (filesystem, network, subprocess, environment) and groups them by boundary type. Loads a cached survey or auto-runs analysis if needed. Supports 16 languages (14 dedicated catalogs + 2 via aliases) with 150+ framework IO entries. When entrypoints are available, traces backward from IO edges to show which entrypoints can reach each IO operation. Output format is `--format text|json` (default `text`), the canonical read-view spelling shared with `routes` / `catalog` / `config` / `cache-status` / `dead-code-maybe` / `test-coverage`; the historical `--json` boolean is kept as a back-compat alias (WI-kitud).

🟩 **`hypergumbo verify-claims --claims <file> [--format text|json]`** (ADR-0016, ADR-0017)
Verifies security claims against the IO boundary map and taint-flow analysis. Supports boundary constraints (e.g., "no network I/O", "max 3 filesystem chains") and taint-flow constraints (e.g., "plaintext data must not reach host_fs zone"). Claims are specified in YAML format (`hypergumbo verify-claims --help` documents the shape). The claims file is validated up front: a malformed YAML, an unexpected root/claim/constraint shape, an unknown field name, or a `constraint.boundary` outside the io-boundaries vocabulary produces a clear error rather than a traceback or a silent false "confirmed". A `must_not_exist` / `max_chains` boundary claim is only `confirmed` when the I/O analysis could actually have seen the I/O: if the analysis produced no call edges at all, or a supported language was analyzed but produced none (so its I/O is invisible), the verdict is `inconclusive` rather than a false clean (WI-kajil / INV-bitig). Exit codes: `0` = all confirmed; `1` = at least one violated; `2` = at least one inconclusive, or the claims file failed validation. Output format is `--format text|json` (default `text`), the canonical read-view spelling; the historical `--json` boolean is kept as a back-compat alias (WI-kitud). Useful for CI enforcement of IO and data-flow security policies.

Taint-flow analysis operates at two precision levels depending on language support:
* **Structural** (all languages): Call-graph BFS with dominance-based sanitizer checking. Catches missing sanitizers on entire call paths. Findings labeled `confidence: approximate`. **A sanitizer called in the same function as the source is invisible to this pass and always will be** — a call graph records no order between two calls sharing a caller, so it cannot distinguish encrypt-then-write from write-then-encrypt (WI-fasub). The DDG pass honours that shape; every language without a def/use extractor does not, and the limit is published per run in `dataflow_coverage.sanitizer_scope`.
* **DDG-backed** — **IMPLEMENTED, CONFIRM-ONLY.** Variable-level taint tracking within a function via intraprocedural reaching definitions (ADR-0017 §3a). The walk runs where the source function has def/use coverage and the sink is called in that same function; it **raises a finding's confidence and never removes one**, so which flows are *reported* is still decided by call-graph BFS. That is the difference between corroborating a flow and adjudicating it, and it is emitted rather than left to the reader: every `verify-claims` run carries `dataflow_coverage.inclusion_decided_by`, which reads `call_graph_reachability` for as long as this remains true. Def/use extractors are registered and production-reachable for Python, Go, Rust, TypeScript and JavaScript. `confidence: precise` / `analysis_method: ddg` mean the walk **actually confirmed a data dependence** — not merely that both endpoints had DDG coverage, which is the unearned reading INV-sadah was filed for; `ddg_mixed` means the walk ran without confirming, and `structural` means no reaching-def data existed for the flow's source function so no walk was possible (see ADR-0017 §3c–3e).

🟩 **`hypergumbo repeat-finder [path] [--format text|json] [--min-complexity N] [--include-tests] [--limit N]`** (ADR-0014, ADR-0035 §1)
Finds structural clones — refactoring leads. Groups symbols by `(language, shape_id)`: a cluster of ≥2 nodes is a set of structurally-identical implementations (same control-flow/nesting skeleton, differing only in identifiers and literals), within-language per ADR-0014. This is the consumer that activates `shape_id`'s one non-redundant capability over `fingerprint` — duplicate-code / extract-helper detection (see the `shape_id` Purpose in [§6 Identity field semantics](#identity-field-semantics)). Trivial clusters (shared cyclomatic complexity below `--min-complexity`, default 2 — a straight-line stub is not a refactoring lead) are dropped; only production clones (≥2 non-test members) are the headline, with test-only clone clusters (parametrized tests are structurally identical by design) counted as a labeled disclosure bucket shown via `--include-tests`. Clusters rank by duplication burden (member count × representative LOC). `--min-complexity 1` includes straight-line clones. Output format is `--format text|json` (default `text`), the canonical read-view spelling; the JSON envelope is `{schema_version, view: "repeat_finder", summary, clusters}` sharing `READ_VIEW_SCHEMA_VERSION`.

### Analysis options

These options apply to all analysis commands (`run`, `slice`, and default sketch mode).

🟩 **`--exclude PATTERN`**
Gitignore-style glob patterns for paths to skip. Uses `fnmatch` matching.
* Default excludes: `node_modules/`, `venv/`, `dist/`, `build/`, `*.min.js`, `*.bundle.js`, `.git/`, `__pycache__/`

🟩 **`--max-file-bytes N`** (default: no limit)
Skip files exceeding this size. Particularly useful for HTML and minified JavaScript.
* Skipped files logged in `limits.truncated_files[]`

🟩 **`--first-party-only`**
Analyze only first-party code (tier 1). Equivalent to `--max-tier 1`.

🟩 **`--max-tier N`** (default: 3)
Control which supply chain tiers are included in analysis. See [§14](#14-supply-chain-classification) for tier definitions.

🟩 **`--no-first-party-priority`**
Disable tier-based weighting in Key Symbols ranking (use raw centrality instead).

### Environment variables

🟩 hypergumbo reads a small set of optional environment variables (defaults listed):

| Variable | Default | Effect |
|---|---|---|
| `HYPERGUMBO_CACHE_HONK_GB` | `1.0` | Results-cache size warning threshold (GiB). When the cache exceeds it, `cache-status` and `run` emit a loud stderr warning naming the top consumer and the prune commands. Set to `0` / `off` / `none` / `false` to silence. There is no automatic eviction at any size — the user owns the prune decision (INV-padum). |
| `HYPERGUMBO_RUST_ANALYZER` | unset | Set to `1` to opt into the SCIP-backed rust-analyzer backend (equivalent to `--backend rust-analyzer`); falls through to the tree-sitter `rust.py` analyzer when unavailable. |
| `HYPERGUMBO_MIN_MEMORY_MB` | `512` | Minimum available system memory (MB) below which an in-progress analysis aborts *between files* with `MemoryPressureError` rather than risking the OOM killer or swap thrash (Linux `/proc/meminfo`; no-op on other platforms). Set to `0` to disable the check entirely. |
| `HYPERGUMBO_VERBOSE` | unset | Set (to any value) to emit `[embed] …` progress logging to stderr during embedding-based config extraction. |
| `HF_HUB_OFFLINE` | managed | hypergumbo sets this to `1` automatically while loading a *cached* embedding model, so runtime CLI subcommands make no outbound HuggingFace Hub requests (the `runtime-cli-no-network` claim). `local_files_only=True` alone does not stop HF Hub's metadata API, the xet cache-freshness ping, or the `transformers` safetensors background thread — only `HF_HUB_OFFLINE=1` does. The one-time first-install model download restores your prior setting so it can fetch. You may also export `HF_HUB_OFFLINE=1` yourself to force offline mode globally. |

## 4) Supported stacks

Hypergumbo supports 100+ languages via tree-sitter grammars (see [LANGUAGES.md](LANGUAGES.md) for the full list). All are included in the base package.

**Primary languages (full symbol/edge extraction):**
* 🟩 **Python** (AST-based, full call edges)
* 🟩 **JavaScript/TypeScript** (tree-sitter, includes Svelte/Vue script block extraction)
* 🟩 **Java** (tree-sitter, Spring Boot framework patterns)
* 🟩 **Go** (tree-sitter, Gin/Echo/Chi/Fiber patterns)
* 🟩 **Rust** (tree-sitter, Actix/Axum/Rocket patterns)
* 🟩 **Ruby** (tree-sitter, Rails/Sinatra patterns)
* 🟩 **Elixir** (tree-sitter, Phoenix patterns)
* 🟩 **PHP** (tree-sitter, Laravel patterns)
* 🟩 **C/C++** (tree-sitter)
* 🟩 **C#** (tree-sitter, ASP.NET Core patterns)
* 🟩 **Kotlin/Scala/Swift** (tree-sitter)

**Additional languages (symbol extraction):**
* 🟩 Bash, Clojure, Dart, Elm, Erlang, F#, Fortran, Haskell, Julia, Lua, Nim, OCaml, Perl, QML, R, Zig, and many more — see [LANGUAGES.md](LANGUAGES.md) for the full list

**Notebook formats:**
* 🟩 Jupyter Notebook (.ipynb) — extracts Python code cells, strips magics, AST-based analysis (Tier 2)

**Templating:**
* 🟩 Blade (Laravel), Handlebars

**Diagram/plotting:**
* 🟩 Mermaid, Gnuplot

**Configuration/data formats:**
* 🟩 JSON, YAML, TOML, XML, HCL/Terraform, Dockerfile, Makefile, Just, CMake, SQL, GraphQL, Protobuf, Thrift

**Markup:**
* 🟩 HTML (script tag extraction), CSS, LaTeX, Markdown

### Dependency strategy
* **All-in-one package**: `pip install hypergumbo` includes Python AST + tree-sitter grammars for all supported languages as standard dependencies (see [LANGUAGES.md](LANGUAGES.md) for the full list)
* **Grammar sources**: Most grammars are installed as individual PyPI packages (e.g., `tree-sitter-javascript`). A subset (Elixir, COBOL, Dart, LaTeX, R) come from `tree-sitter-language-pack`.
* **Build-from-source grammars**: Lean, Wolfram, Circom built from source in CI for languages lacking PyPI packages
* **Fallback**: If a specific grammar fails to load, that language is skipped with explicit `limits.skipped_languages[]` logging
* **Optional extras**:
  - `[embeddings]`: sentence-transformers for embedding-based config extraction

### Build strategy
* Tree-sitter grammars with PyPI wheels are installed directly as dependencies
* Grammars without PyPI packages (Lean, Wolfram, Circom) are built from source in CI (`scripts/build-source-grammars`)
* 100% test coverage required; analyzers gracefully skip when grammars unavailable

## 5) Architecture

For a file-by-file map of modules, see [ARCHITECTURE.md](ARCHITECTURE.md) (auto-generated by running hypergumbo on itself; run `./scripts/generate-architecture` to update).

### Analysis pipeline (two-tier model)

The analysis pipeline has two tiers reflecting different information needs.

**Terminology:** A *pass* is any analysis component that reads code and produces IR output. An *analyzer* is a Tier 1 pass that extracts symbols and edges for a single language. A *linker* is a Tier 2 pass that creates cross-language or cross-component relationships. The `pass_id` field in `AnalysisRun` uses the generic term.

**Tier 1 — Language analyzers (independent producers):**
Each analyzer is a plain function registered via the `@register_analyzer` decorator and discovered through Python entry-points (see [ADR-0010](adr/0010-modular-packages-and-smart-testing.md), [ADR-0012](adr/0012-pass-unification-and-multi-fidelity.md)):
```python
from hypergumbo_core.analyze.registry import register_analyzer

@register_analyzer("go", priority=50)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    ...
```
Each analyzer returns an `AnalysisResult` containing symbols, edges, and usage contexts — the data types defined in [§6 Internal representation](#6-internal-representation). Analyzers are embarrassingly parallel — each scans the repo independently and returns a bag of symbols and edges. They do not see each other's output.

**Tier 2 — Linkers and enrichment (context-dependent refiners):**
After all analyzers run, the orchestrator (`run_survey`) collects the unified symbol graph and runs post-processing:
1. Deferred symbol reference resolution (cross-file call targets)
2. Framework pattern enrichment (YAML-driven concept metadata)
3. Linkers (registered via `@register_linker` decorator, receiving `LinkerContext` with the full symbol graph; see [LINKERS.md](LINKERS.md) for the full list, grouped by subcategory — Protocol, Bridge, Framework, Infrastructure — per [ADR-3bbb](adr/3bbb-linker-subcategory-restoration.md))
4. Entrypoint detection

Linkers use a decorator-based registry (`linkers/registry.py`) and receive the accumulated analysis state:
```python
@register_linker("jni", requires=["c", "java"])
def link_jni(ctx: LinkerContext) -> LinkerResult:
    ...
```
🟪 Spec example above is outdated; code uses `activation=LinkerActivation(language_pairs=[("java","c"),("java","cpp"),("java","rust")])` and `requirements=list[LinkerRequirement]` instead of `requires=list[str]` (registered name is `jni-linker`, not `jni`).

**🟪 Design target — Unified pass interface:**
For multi-fidelity analysis (e.g., a pyright pass refining AST-extracted edges with type-resolved information), both tiers would converge on a single interface where passes receive the IR and return deltas:
```python
class AnalysisPass(Protocol):
    id: str              # e.g., "python" (post-INV-morag-PR-2; legacy "-v1" suffix removed)
    version: str         # the bare tool version, e.g. "7.0.0" (NOT prefixed — see Version fields)
    capabilities: list[str]  # e.g., ["python"]

    def run(self, ir: AnalysisIR, files: list[Path], config: Config) -> IRDelta: ...
```
This would allow a type-resolution pass to slot in between Tier 1 and Tier 2, reading AST-produced symbols and upgrading their edge confidences. Tier 1 analyzers would receive an empty IR (they remain independent); Tier 2 refiners would receive the accumulated graph. The orchestrator becomes generic — just iterating passes in priority order. See [ADR-0012](adr/0012-pass-unification-and-multi-fidelity.md) for the design rationale and migration path.

For the full catalog of language analyzers, linkers, and framework pattern files, see [LANGUAGES.md](LANGUAGES.md), [LINKERS.md](LINKERS.md), and [FRAMEWORKS.md](FRAMEWORKS.md).

## 6) Internal representation

Parsers emit to `AnalysisIR`:
```python
@dataclass
class Symbol:
    id: str                    # location-based identifier
    stable_id: Optional[str]   # semantic identity hash (signature-based)
    shape_id: Optional[str]    # structural implementation fingerprint
    # canonical_name removed per ADR-0032; superseded by the typed sibling fields below
    display_label: Optional[str]   # human-readable label
    qualified_name: Optional[str]  # scope-qualified name
    visibility: Optional[str]      # INV-jusot: one canonical level (public/private/protected/internal/package), computed in finalize; signal in meta['visibility_signal']
    fingerprint: str           # 🟪 code: Optional[str] = None
    kind: str                  # language construct only (function/class/module/method/...): per ADR-0027 the kind axis names the source-language syntactic construct; framework-role / dispatch / entrypoint facts live in meta (see Multi-value field axes below)
    name: str
    path: str
    language: str
    span: Span
    origin: list[str]          # which passes contributed to this (INV-jidat; was str pre-0.10.0)
    origin_run_id: str         # references AnalysisRun.execution_id
    supply_chain_tier: int     # 1=first_party, 2=internal_dep, 3=external_dep, 4=derived
    supply_chain_reason: str   # classification rationale (e.g., "matches ^src/")
    # Note: In JSON output (§9 Behavior map JSON), these flat fields are compiled
    # into a nested supply_chain object with a derived tier_name field.
    quality: QualityScore      # 🟪 QualityScore not defined; code: Optional[Dict[str, Any]] = None

@dataclass
class AnalysisRun:
    execution_id: str          # unique per run (uuid or hash of run_signature + started_at + repo_fingerprint)
    run_signature: str         # deterministic: hash of (pass_id, version, config_fingerprint, toolchain)
    repo_fingerprint: str      # hash of (git_head + dirty_files) or hash of (file_list + content_hashes)
    pass_id: str               # e.g., "python" (serialized as "pass" in JSON output).
                               # INV-morag PR 2: legacy "-v1" / "-ts-v1" suffix removed.
                               # Pass identity is now a stable opaque name; backend
                               # (ast / tree-sitter / pattern) lives on the Pass
                               # catalog entry's "backend" field, and per-pass
                               # versioning lives in pass_version below.
    version: str               # the bare tool version, e.g. "7.0.0" (NOT prefixed — see Version fields)
    pass_version: str          # INV-morag option A: code-hash of the pass module
                               # (sha256:<hex>) — real per-pass version. INV-morag
                               # PR 2 (this version) populated this field at every
                               # registration site automatically via the decorator.
    toolchain: Dict            # {"name": "python", "version": "3.11.0"}
    config_fingerprint: str    # sha256 of effective config
    files_analyzed: int
    files_skipped: int
    skipped_passes: List[Dict] # legacy per-run mirror (unpopulated); pass-level skips live in limits.skipped_passes
    warnings: List[str]
    started_at: str
    duration_ms: int

@dataclass
class AnalysisIR:
    runs: List[AnalysisRun]           # provenance: which passes ran
    symbols: List[Symbol]              # definitions (funcs, classes, etc)
    references: List[Reference]        # use sites
    relationships: List[Relationship]  # typed edges with confidence + rank_score
```
🟪 `AnalysisIR`, `Reference`, `Relationship` are spec names; code uses `AnalysisResult`, `Symbol`, `Edge`.

🟩 The serialized `analysis_runs[]` array is sorted by ascending `started_at`, ties broken by `pass` id (WI-haguz), so within a run consumers see passes in chronological completion order. It is **not** byte-stable across runs: every entry stamps a fresh `execution_id` (a per-run `uuid:` value) plus wall-clock `started_at`/`duration_ms`, and because the sort key is wall-clock, even the pass *ordering* is not reproducible run-to-run (WI-haguz removed the old random dict-order; it did not make the order reproducible). Only the L2 semantic content (nodes, edges, `stable_id`s, `run_signature`) is reproducible — see the `reproducibility_context` block (§ below).

### Multi-value field axes

Per ADR-0024 the multi-value type fields on the core dataclasses each declare an axis (axiom + consumer pattern + enforcement) so analyzers, linkers, and downstream consumers share a canonical vocabulary instead of free-form strings. Three axes are currently declared:

* **`Edge.edge_type`** — ADR-0023 (Accepted). Axiom: "names the relationship that produced the edge." Three sections: `relationship` (canonical), `endpoint_shape` (deprecation candidates folding to canonical + meta), `pending_classification`. Registry at `packages/hypergumbo-core/src/hypergumbo_core/edge_types.py`; per-axis view at [`docs/concept-axes.md`](concept-axes.md). Schema enum + per-value `x-axis-of-values` annotation generated from the registry. (Closed enum; no f-string emit sites in production producers, so the enum is authoritative.)
* **`Symbol.kind`** — ADR-0027 (Accepted; Phases 1–4 complete through `SCHEMA_VERSION` 0.6.0 — **endpoint_shape closure shipped**, all 71 deprecated values removed from `SYMBOL_KINDS` per audit-findings 0005/0006/0007/0009/0010/0011/0013). Axiom: "names the source-language syntactic construct the symbol represents; properties derivable from edges or framework metadata are queried from those structures rather than smuggled into the kind label." Current registry: 152 values across `language_construct` (138 — Cluster A canonical plus the Clusters 27B/27C/27G/27H values promoted from `pending_classification` during the closure, plus later per-construct registrations such as the GraphQL `scalar` sibling WI-zigih's dict-indirection gate surfaced) and `pending_classification` (14 — long-tail remainder awaiting per-cluster classification). `endpoint_shape` retained only as a back-compat import alias; not in `VALID_AXES`. Registry at `packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py`; per-axis view at [`docs/concept-axes.md`](concept-axes.md); rename tables for downstream JSON consumers at [`docs/MIGRATION-6.0-CONCEPT-AXES.md`](MIGRATION-6.0-CONCEPT-AXES.md). Drift gate (CI property test + pre-commit linter `scripts/check-symbol-kind-drift`) catches consumer-side hardcoded `*KIND*` sets that drift from the registry. Schema posture: `type: "string"` + `x-axis-of-values` map (open enum) because production includes dynamic `kind=f"ipc_{...}"` emits at `ipc.py` / `phoenix_ipc.py` that produce values outside the static registry.
* **`Edge.evidence_type`** — ADR-0028 (Accepted; Phases 1–4 complete through `SCHEMA_VERSION` 0.7.0 — **endpoint_shape closure shipped**, all 111 deprecated values removed from `EVIDENCE_TYPES` per audit-findings 0008/0012/0014). Axiom: "names the inference pathway by which the analyzer concluded this edge exists; properties of the dst's resolvedness, of the framework's dispatch convention, or of the call-construct's surface form are queried from siblings (`Edge.is_resolved`) or `Edge.meta`, not smuggled into the evidence label." Current registry: 125 values across `inference_pathway` (114 — Cluster A canonical plus the AXIS_PENDING additions that landed at 0.7.1) and `pending_classification` (11 — long-tail awaiting classification). `endpoint_shape` retained only as a back-compat import alias; not in `VALID_AXES`. Registry at `packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py`; per-axis view at [`docs/concept-axes.md`](concept-axes.md); rename tables at [`docs/MIGRATION-6.0-CONCEPT-AXES.md`](MIGRATION-6.0-CONCEPT-AXES.md). Sibling field `Edge.is_resolved: bool = True` ships with the registry. Drift gate (CI property test + pre-commit linter `scripts/check-evidence-type-drift`) catches consumer-side `*EVIDENCE_TYPE*` set drift. Schema posture: open enum (same as Symbol.kind, for the same reason — production has a dynamic f-string `evidence_type` emit at `inheritance.py` (`evidence_type=f"ast_{edge_type}"`), so a value outside the static registry can appear).

**L3 producer-side enforcement (ADR-0028 §"Path B").** All three sibling axes share a producer-side coherence linter at `packages/hypergumbo-core/src/hypergumbo_core/producer_coherence.py` (script: `scripts/check-producer-axis-coherence`). It walks `Edge.create(...)` / `Edge(...)` / `Symbol.create(...)` / `Symbol(...)` call sites and verifies that literal-string keyword arguments to axis-bearing parameters (`evidence_type`, `kind`, `edge_type`) are in their canonical registry. F-string arguments are surfaced as advisory Phase-3 fold candidates rather than strict failures. This closes the L3 producer-introduction gap that the L1 consumer-side drift linters by themselves leave open.

Future axis declarations (possible `supply_chain.tier`, `Edge.meta` key vocabularies if any meta-key promotes per ADR-0024 fold-residue rule 3) follow the same template; the same `axis_drift.find_drift` and `producer_coherence.find_producer_coherence_violations` infrastructure power their drift gates.

**Adding a new multi-value field.** The contributor workflow for declaring a new axis (open ADR; land four artifacts — registry module, drift linter, property test, by-axis view) and the filing convention for axis-conformance verdicts (audit-findings file at `docs/audits/<NN>-<topic>.md`, NOT a new ADR) are documented in [AGENTS.md](../AGENTS.md) under "Axis declaration for multi-value fields" and "ADR vs audit-findings filing convention", with the canonical template in [ADR-0024](adr/0024-axis-declaration-template.md).

### Identity field semantics

* `id` (location-based): `{lang}:{file}:{start_line}-{end_line}:{name}:{kind}` for symbols defined in source files; `{lang}:{module_hint}:0-0:{name}:{kind}` for synthetic nodes that have no real source location (external module references, unresolved import targets, boundary placeholders).
  - **The span field discriminates which shape applies.** A real span (`{start_line}-{end_line}` with non-zero values, plus the whole-file sentinel `1-1`) means slot 2 is a *repo-relative file path* — preserved literally, including hyphens like `packages/hypergumbo-core/...`; path segments are not coerced into language-level identifiers, and a path segment that happens to be a valid identifier in the language is incidental, not contractual. The sentinel span `0-0` means slot 2 is a *dotted-module-form qualifier* in the language's import vocabulary (e.g. `python:hypergumbo_core.taxonomy:0-0:LANGUAGE_ALIASES:symbol`). The two shapes never collide because no real symbol carries a `0-0` span.
  - A `0-0`-span ID whose slot-2 qualifier contains filesystem segments (`packages.`, `.src.`) or invalid-identifier characters (e.g. hyphens in Python module qualifiers) indicates an analyzer bug: the producer fell through from "resolve to a real source root" to "stringify the file path as if it were a module name." This shape leaked through monorepo `packages/<pkg>/src/<mod>/` layouts before WI-davan was fixed.
  - Changes when code moves to different file/line (file-path shape) or when import resolution changes (module-hint shape)
  - Purpose: Reproducible slicing, deterministic diffs
* `stable_id` (semantic, optional): Interface identity (signature-based), **not implementation identity**
  - 🟨 **For typed languages or annotated Python**: `sha256({kind}:{normalized_signature}:{visibility}:{decorators}:{containing_stable_id}:{name}:{qualified_name})` (per ADR-0035 §1, `name` and `qualified_name` are now in the basis so distinct-named same-signature symbols no longer collapse)
    - `normalized_signature`: Canonical type signature (param types, return type, type params), normalized per-language (strip FQN prefixes, normalize generic type params by position: `T,U` → `$0,$1`). Normalization is language-scoped — cross-language collision is structurally prevented by `containing_module_stable_id`. A cross-language canonical mapping table may be layered on top if a use case emerges (see ADR-0014 §3). Four normalization families: types-first (Java, C#, Dart, Groovy), names-first (Kotlin, Scala, Swift, Rust, TS, Python), PHP-specific, Go-specific.
    - `visibility`: public, private, protected (if language has concept)
    - `decorators`: Sorted, comma-joined decorator/annotation names
    - `containing_module_stable_id`: Recursive stable_id of parent module/class
    - **Excludes**: Implementation details, docstrings, comments
    - Implemented for 12 analyzers — **methods and constructors only**: Java, C#, Kotlin, Scala, Swift, Rust, Go, PHP, Groovy, JS/TS, Python, Dart. Top-level functions, fields, and class/interface symbols on these analyzers fall back to the untyped tier below. Coverage roadmap in ADR-0014 status line.
  - 🟩 **For untyped code**: `sha256({kind}:{parameter_count}:{arity_flags}:{decorator_presence}:{containing_stable_id}:{name}:{qualified_name}:{occurrence_index})` (per ADR-0035 §1, `name`, `qualified_name`, and `occurrence_index` are in the basis)
    - `arity_flags`: has_defaults, has_varargs, has_kwargs (structural signature info)
    - `decorator_presence`: Sorted list of decorator names (e.g., `["property", "staticmethod"]`)
    - **Excludes**: Source hash, docstrings, comments. (Note: `name`/`qualified_name` ARE included since v5 — `stable_id` changes on rename; see ADR-0035 §2.)
  - Purpose: Unambiguous within-run graph foreign key (unique-within-run per ADR-0035 §1). Track symbols across documentation/body changes; rename/move tracking is delegated to `fingerprint` and `shape_id`.
  - **Does NOT change** when: changing implementation/body, adding comments, reformatting, line drift, edits elsewhere in the file
  - **DOES change** when: Signature changes (param types, arity), visibility changes, decorators added/removed; renames (of the symbol, its container, or the file); file/container moves (ADR-0035 §1/§4)
* 🟨 `shape_id` (optional): Structural implementation fingerprint
  - `sha256(ast_structure)` excluding literals/identifiers
  - Purpose: Detect structural changes (control flow, nesting) without caring about variable names
  - Use case: "Implementation changed but signature stayed same"
  - 🟩 Python: implemented via `_compute_shape_id()` using Python's `ast` module
  - 🟩 Tree-sitter languages: implemented via generic CST walker in `TreeSitterAnalyzer.compute_shape_id()`. Two wiring paths: (1) analyzers that populate `node_for_symbol` get automatic shape_id computation in the base class `analyze()` method, (2) analyzers that call `compute_shape_id(node)` directly at symbol construction time.
  - Coverage: ~41 of ~70 code-language analyzers — 20 mainstream (via `node_for_symbol`, direct `compute_shape_id()`, or Python's `ast` override) + 19 extended1 + 1 common (HLSL). Remaining gaps are niche languages (13 extended1 without, 10 common without, Dart not yet wired). See ADR-0014 status line for the live coverage table.
  - `None` when: (a) the node is a **synthetic node with no source body to hash** — e.g. a framework route registration (`starlette:<handler>`, `kind="function"` but representing a path→handler *mapping*, not a code body — its real handler carries the shape_id), an `external_symbol` (no in-repo source), or any node whose declaring construct is not a hashable code structure; or (b) the node's language/analyzer is outside the coverage set above; or (c) the symbol kind is non-structural (no meaningful control-flow skeleton). A null `shape_id` is therefore **not** by itself a defect — it may be an honest "no body to hash" (as for `fingerprint`, see below) *regardless of the kind label*, so a consumer must treat null as "not clusterable" rather than infer a gap. (WI-lutob closed the real code-body gaps: C#/Solidity/WGSL `class`/`function`/`method`/`struct`/`interface`/`enum` now populate it.)
* `fingerprint` (structural content hash): `hgfp2:` + `sha256(subtree_walk)[:16]`
  - Hash of the symbol's parse subtree — shape + identifiers + literals — computed by the central post-pass in `hypergumbo_core/fingerprint.py`, which parses each file once and hashes the subtree covering the symbol's span (scheme tag declared top-level as `symbol_fingerprint_scheme`)
  - **Does NOT change** when: blank lines, indentation choices, or comments change (whitespace/comment-invariant — deliberately NOT a raw `sha256(source_bytes)`)
  - **DOES change** when: an identifier is renamed, a literal's value changes, or structure changes
  - Purpose: Detect meaningful modifications; duplicate-code detection; per-symbol cache invalidation
  - `None` when: the language has no grammar in the language pack (regex-only analyzers), the span has no parseable content, or the located subtree contains parse errors — an honest null, never a degenerate constant (enforced by the spec validator's fingerprint-degeneracy umbrella check). Synthetic boundary / `external_symbol` nodes (span `0-0`, no in-repo source bytes) are `None` for the same reason — there is no content to hash (WI-lisog facet b)
  - **Single producer, single shape.** The central post-pass solely owns this field for source-code nodes (`language is not None`): it recomputes any non-canonical value an analyzer may have stamped — a producer-side bare-hex `sha256(source[start:end])[:16]` leak — so only the canonical `hgfp2:` form (or `None`) ever reaches the output (WI-lisog). The **one** documented second shape is an identity-derived bare 16-hex fingerprint on `language is None` Class-B synthetic stand-ins (e.g. protocol-linker synthesized nodes): they have no source to hash, so the producer stamps `sha256(symbol_id)[:16]` as a stable identity marker and the post-pass preserves it (it is not a source-content hash and carries no `hgfp2:` prefix by design)

**Route and entry-point stable_id variants** (ADR-0014 §4). Symbols that name framework endpoints rather than language constructs use distinct, smaller-input hash bases. Per ADR-0035 §3/§4, route and entry identity now fold in the declaring file (and language for routes), so the same logical route or entry in different files or languages is distinct:

* **HTTP route symbols**: minted as `sha256("route:{method}:{path}")`, but the shipped (post-pass-widened) identity is `route_site:{language}:{normalize_path(rel_path)}:{route:{method}:{path}}` — the bare key collided cross-file/cross-language (146 collisions), so `widen_route_stable_ids` re-keys route nodes per ADR-0035 §3 (v8).
* **Entry-point symbols**: `sha256("entry:{entry_type}:{name}:{path}")` — e.g., `("cli_command", "migrate")`, `("main_function", "main")`. The declaring file `path` was folded in per ADR-0035 §4 to keep file-scoped entry names (e.g. shader `main`) distinct across files.

Route and entry ids fold in the declaring file (and language for routes) per ADR-0035 §3/§4, so the same logical route/entry in different files or languages is now distinct.

**Example**:
```python
# Original:
def authenticate(username: str, password: str) -> User:
    ...

# After rename and move:
# File: auth_service.py → user_auth.py
# Function: authenticate → verify_credentials
# stable_id CHANGES (name + file are in the hash) — per ADR-0035 §2
# fingerprint stays the same if the body is unchanged, so the rename is
#   detectable by joining on fingerprint across versions
# id changes (file and name changed)
# shape_id stays the same if control flow unchanged
```

### Identity and provenance scheme versioning

The exact algorithms for identity and provenance fields are governed by scheme identifiers in the output:
* `stable_id_scheme`: identifies the algorithm/normalization used to compute `stable_id`. Current value: `hypergumbo-stableid-v8`. The scheme has shipped eight values (`v1 → v2 → v3 → v4 → v5 → v6 → v7 → v8`); each transition changed every affected `stable_id` value with no in-place migration, so outputs from an older hypergumbo must be re-analyzed against the current binary before they can be compared. See [§ Algorithm-identification fields and `stable_id_scheme` version history](#algorithm-identification-fields-and-stable_id_scheme-version-history) for the per-transition record (driver, hash-basis change, and collision impact).
* `shape_id_scheme`: identifies the algorithm used to compute `shape_id`. Current value: `hypergumbo-shapeid-v3`. v3 (WI-linon) folds the symbol kind (`class`/`method`/`function`) and the concrete AST node type into the Python shape hash, so structurally-trivial symbols of different kinds no longer share a `shape_id` — a module-level function vs a class method (both `ast.FunctionDef`, with `self` absent from the body), and — via the node type — an `async def` vs a `class` (`ast.AsyncFunctionDef` previously mis-branched into the ClassDef path). Every Python `shape_id` value changed relative to v2; the tree-sitter path (other languages) is unchanged but the identifier is global, so it bumps per the mandate below.
* `repo_fingerprint_scheme`: identifies the algorithm used to compute `analysis_runs[].repo_fingerprint`

Any change that would alter computed values MUST bump the corresponding scheme identifier.

### Provenance field semantics

* `execution_id`: Unique identifier for this specific analysis run
  - Format: `uuid:` prefix for UUID v4, or `sha256:` for deterministic hash
  - Purpose: Track which specific run produced which nodes/edges
  - Enables: Correlation to repo snapshots, multi-run comparison
* `run_signature`: Deterministic fingerprint of analyzer configuration
  - Hash of (pass_id, version, config_fingerprint, toolchain)
  - Same pass + version + config + toolchain → same signature
  - Purpose: Cache keying, grouping results by analyzer version
* `repo_fingerprint`: Hash identifying the code snapshot analyzed
  - Git repos: `sha256(git_head + sorted([(path, sha256(content_bytes)) for each dirty file]))`
    - `git_head`: HEAD commit hash
    - `dirty file`: tracked file whose working tree content differs from HEAD OR untracked file included in analysis
    - Purpose: ensures repo_fingerprint changes when dirty file contents change, not just when paths change
  - Non-git: `sha256(sorted([(path, content_hash) for all files]))`
  - **Field rendering (WI-bosog, `repofp-v2`):** the AnalysisRun field carries the `sha256:` scheme prefix over the full 64-hex digest (`sha256:<64hex>`), uniform with the sibling identity fields `run_signature` / `config_fingerprint` (`sha256:<16hex>`). The bare (un-prefixed) digest is retained only where the value doubles as the colon-free analysis-cache path segment.
  - Purpose: Cache invalidation, provenance tracking

### Output views

Public outputs are **compiled views** from this IR — the IR defines the canonical data model, and each view selects and reshapes fields for its audience. See [§9](#9-behavior-map-json) and [§10](#10-sketch-output) for the available views and their serialization details.

**Design principle:** Strong passes (tsserver, pyright) added later will enhance the IR without breaking existing views.

## 7) Linkers

Hypergumbo provides **best-effort edge recovery** for patterns that language analyzers cannot see statically. These are AST-based heuristics with string literal matching and metadata comparison, not type-resolved or dataflow analysis. Linkers fall into four subcategories per [ADR-3bbb](adr/3bbb-linker-subcategory-restoration.md): Protocol (framework-agnostic pattern matching), Bridge (language-pair-specific FFI conventions), Framework (framework-specific dispatch), and Infrastructure (graph-structural utilities). This section specifies three linkers in detail — JNI (Bridge), IPC (Protocol), HTTP client-server (Protocol); for the full catalog of 57 linkers grouped by subcategory, see [LINKERS.md](LINKERS.md).

### JNI Boundary Detection (Java ↔ C)

Detects native method declarations in Java and matches them to C implementations via naming conventions.

**Java side detection:**
```java
public class GuacamoleSession {
    public native void processFrame(byte[] data);
}
```

**C side detection (matched by naming convention):**
```c
JNIEXPORT void JNICALL Java_GuacamoleSession_processFrame(
    JNIEnv *env, jobject obj, jbyteArray data)
```

**Detection rules:**
1. Find Java methods with `native` modifier
2. Find C functions matching `Java_{ClassName}_{methodName}` pattern (mangled names)
3. Emit canonical `calls` edge with `meta["bridge_kind"] = "native"` from Java method → C function (post WI-mifor-vabul Phase 3 bridge/FFI; pre-fold edge_type was `native_bridge`)

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* Pattern-matched (naming convention): 0.95
* 🟪 Annotation-confirmed (`@hypergumbo.jni_impl`): 0.98

**Limitations:**
* Does not resolve JNI calls through reflection
* Does not track `JNI_OnLoad` dynamic registration
* Does not handle inner classes (mangling includes `$`)
* ⬜ Logs unmatched natives in `limits.unresolved_jni[]` (field not yet implemented; see §7 limits.cross_language)

### IPC/Message Channel Detection

Detects message send/receive patterns across process boundaries using string literal matching on channel/event names.

**Supported patterns:**

Per ADR-0023 / audit-findings 0002, all five patterns emit the same canonical edge type `event_publishes`; the framework-specific dispatch fact lives in `meta["channel_kind"]` (the per-pattern edge_type values `ipc_calls` / `ipc_event` / `message_send` / `message_receive` / `websocket_message` / `message_queue` were folded into `event_publishes` per ADR-0023 / audit-findings 0002).

| Framework | Send Pattern | Receive Pattern | meta["channel_kind"] |
|-----------|-------------|-----------------|----------------------|
| Electron | `ipcRenderer.send("channel")` | `ipcMain.on("channel")` | `ipc` |
| Electron | `ipcMain.handle("channel")` | `ipcRenderer.invoke("channel")` | `ipc` |
| WebSocket | `ws.send({type: "X"})` | `ws.on("message", ...)` with type check | `websocket` |
| ⬜ Guacamole | `tunnel.sendMessage("opcode", ...)` | `oninstruction` handlers | `ipc` |
| Node EventEmitter | `emitter.emit("event")` | `emitter.on("event")` | (handled by `event_sourcing.py`, not the IPC linker; emits canonical `event_publishes` with no `channel_kind`) |

**Detection algorithm:**
1. Parse AST for known send/receive function patterns
2. Extract channel/event name from string literal argument
3. Build index of all senders and receivers by channel name
4. Match senders to receivers with same channel name
5. Emit canonical `event_publishes` edge with `meta["channel_kind"] = "ipc"` (or `"websocket"`, `"queue"`) from caller → handler (post WI-hahap-farid Phase 3 IPC; pre-fold edge_type was `message_send`). The converse `message_receive` edge is no longer emitted (DEPRECATE-NO-FOLD per audit-findings 0002 — recoverable by walking `event_publishes` with the receiver as `dst`).

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* String literal channel name match: 0.85
* 🟪 Variable/computed channel name: 0.50 (code uses flat 0.65 for all non-literal matches; no variable/template distinction)
* 🟪 Template literal with interpolation: 0.40 (see above)
* 🟪 Annotation-provided (`@hypergumbo.ipc_channel("name")`): 0.95

**Limitations:**
* Dynamic channel names require annotation hints
* Complex message routing (middleware, proxies) not traced
* Does not validate message schema compatibility
* ⬜ Logs unmatched patterns in `limits.unresolved_ipc[]` (field not yet implemented; see §7 limits.cross_language)

### HTTP client-server linking

🟩 The HTTP linker (`linkers/http.py`) matches client HTTP calls to server route handlers across languages. Route detection itself is handled by the YAML-driven pattern system (see [§8 Entrypoint detection](#8-entrypoint-detection) and [FRAMEWORKS.md](FRAMEWORKS.md)); this linker creates cross-language edges from client call sites to the detected server routes.

**Supported client patterns:**

| Language | Libraries | Example |
|----------|-----------|---------|
| JS/TS | `fetch`, `axios`, AngularJS `$http`, jQuery `$.ajax`/`$.get`/`$.post`, OpenAPI-generated clients | `fetch("/api/users")` |
| Python | `requests`, `httpx` | `requests.get("/api/users")` |
| Ruby | `RestClient`, `HTTParty`, `Faraday`, `Net::HTTP` | `RestClient.get("/api/users")` |
| Java | Spring `RestTemplate`, Retrofit annotations | `restTemplate.getForObject("/api/users", ...)` |
| Go | `net/http` | `http.Get("http://host/api/users")` |

**Matching algorithm:**
1. Collect server route symbols detected by the pattern system (see [§8](#8-entrypoint-detection))
2. Scan client code for HTTP call patterns with URL arguments
3. Match by HTTP method and URL path, with support for parameterized paths (`:id`, `{id}`, `<id>`)
4. Emit canonical `calls` edge with `meta["protocol"] = "http"` from client call site to matching route handler (post WI-vumum-juvil; pre-fold edge_type was `http_calls`)

**Confidence scoring** (see [§12](#12-confidence-scoring) for the full confidence model):
* Literal URL match: 0.90
* Variable/computed URL: 0.65

### DI resolution linking

🟩 The DI resolution linker (`linkers/di_resolution.py`) creates `di_resolves` edges from interface methods to their DI-bound implementation methods. Unlike `dispatches_to` (which is structural and excluded from forward slices to prevent fan-out explosion), `di_resolves` edges are followed by forward BFS — correct for DI-heavy codebases where the binding narrows to one high-confidence implementation.

**Supported DI frameworks:**
- Java/Kotlin/Scala: Guice `bind(X.class).to(Y.class)`, `@Provides` methods, `@ImplementedBy` annotations, Spring `@Bean` methods
- C#: ASP.NET Core `services.AddScoped<I, C>()` / `AddTransient` / `AddSingleton`
- TypeScript: NestJS/Angular `{ provide: X, useClass: Y }`, InversifyJS `container.bind<I>().to(C)`
- Python: `binder.bind(I, to=C)` (injector library)
- Kotlin: Koin `single<I> { Impl() }`
- Java SPI: `META-INF/services/` files

**Resolution cascade** (highest-confidence wins):
1. Explicit framework binding (Guice bind/provides/Spring/C#/NestJS/Inversify/Koin/Python injector): 0.90
2. Guice `@ImplementedBy` annotation: 0.85
3. Java SPI `META-INF/services/` file: 0.85
4. Naming convention (`DefaultX` or `XImpl`): 0.75
5. Single implementation of interface: 0.70

Edges are created at method level (interface method → implementation method with matching short name), not at class level.

### Language-specific notes for cross-language linking

The C analyzer detects JNI export patterns (`JNIEXPORT`, `JNICALL`, `Java_*` naming) and the Java analyzer detects `native` method declarations, both feeding into the JNI linker above. For full per-language analyzer capabilities, see [LANGUAGES.md](LANGUAGES.md).

### limits.cross_language — tracking unresolved links

🟪 Linkers log unresolved patterns for debugging (not yet implemented — `limits` dataclass has no `cross_language`, `unresolved_jni`, or `unresolved_ipc` fields):

```json
{
  "limits": {
    "cross_language": {
      "unresolved_jni": [
        {
          "java_method": "com.example.Native.processData",
          "expected_c_name": "Java_com_example_Native_processData",
          "reason": "no_matching_c_function"
        }
      ],
      "unresolved_ipc": [
        {
          "channel": "user.login",
          "senders": ["src/client/auth.js:45"],
          "receivers": [],
          "reason": "no_receiver_found"
        }
      ]
    }
  }
}
```

## 8) Entrypoint detection

Entrypoint detection identifies HTTP handlers, CLI mains, background tasks, and other entry sources for slicing. Detection is **YAML-driven** via the framework patterns system.

### Architecture

```
ANALYZERS (pure language, no framework knowledge)
  → Capture symbols + rich metadata (decorators, base classes, parameters)
  → Capture UsageContext for call-based patterns (route registrations, etc.)

PATTERN SYSTEM (YAML files: convention + framework)
  → Match patterns against symbol metadata and usage contexts
  → Enrich symbols with concept metadata (route, task, model, etc.)

ENTRYPOINTS (semantic detection)
  → Query enriched metadata: if "route" in sym.concepts → Entry(kind="route")
  → High confidence (0.95) from semantic match
```

**Key insight:** Entry kinds (routes, tasks, commands) are framework-afforded concepts detected from symbol metadata, not file paths.

### meta.concepts Structure

Enriched symbols have a `meta.concepts` list that serves as the **single source of truth** for semantic metadata:

```json
{
  "meta": {
    "concepts": [
      {"concept": "route", "path": "/users", "method": "GET", "framework": "fastapi"},
      {"concept": "test_function", "framework": "test-frameworks"},
      {"concept": "main_entrypoint", "framework": "main-functions"}
    ]
  }
}
```

**Fields:**
- `concept`: Semantic type (route, model, task, test_function, main_entrypoint, etc.)
- `framework`: Which pattern file matched (fastapi, test-frameworks, main-functions, etc.)
- Additional fields vary by concept type (e.g., `path`, `method` for routes)

**Path normalization:** Route paths are normalized to always start with `/` for consistent matching (e.g., `users` → `/users`).

Linkers and entrypoint detection query `meta.concepts` exclusively.

### Convention Patterns vs Framework Patterns

The pattern system has two categories:

| Category | When Loaded | Purpose | Examples |
|----------|-------------|---------|----------|
| **Convention** | Always | Language-agnostic patterns | main-functions, test-frameworks, naming-conventions, language-conventions, config-conventions, library-exports, logging-conventions, go-encoding-callbacks, node-http |
| **Framework** | When detected | Framework-specific patterns | fastapi, django, express, spring-boot |

**Convention patterns (9 files):**
- `main-functions.yaml`: main() entrypoints across 10+ languages
- `test-frameworks.yaml`: Test function detection (pytest, JUnit, xUnit, etc.)
- `language-conventions.yaml`: CUDA kernels, WGSL shaders, COBOL programs, LaTeX structure, Starlark rules
- `config-conventions.yaml`: NPM/Maven/Cargo dependencies, Android components, TypeScript references
- `library-exports.yaml`: Library entry point detection (JS/TS index exports, Python __init__.py, Go uppercase, Java/Kotlin public, Elixir public, Rust pub)
- `naming-conventions.yaml`: Heuristic entrypoints by naming pattern (`*Controller`, `*Handler`, `*Service`)
- `logging-conventions.yaml`: Logger classes/factory methods/log bridges (used by ranking dampening)
- `go-encoding-callbacks.yaml`: Go `MarshalJSON`/`UnmarshalYAML` and similar encoding callbacks
- `node-http.yaml`: Bare-Node `http.createServer` / Apollo `startStandaloneServer`

**Framework patterns:** Loaded only when the framework is detected in profile. See [FRAMEWORKS.md](FRAMEWORKS.md) for the full list; YAML source is in `packages/hypergumbo-core/src/hypergumbo_core/frameworks/`.

### Pattern Types

The framework pattern system supports multiple detection strategies:

| Pattern Type | Example Frameworks | Detection Method |
|--------------|-------------------|------------------|
| **Decorator-based** | FastAPI, Flask, NestJS, Spring Boot | Match `@app.get`, `@Controller` decorators |
| **Call-based** | Django, Express, Go Gin/Echo | Capture `path("/url", view)` via UsageContext |
| **DSL-based** | Rails, Sinatra, Phoenix | Parse `get '/path' do` blocks |
| **File-based** | Next.js, Nuxt | Infer routes from `pages/`, `app/` paths |
| **Export-based** | JS/TS libraries | Detect exports from `index.ts/js` as library entrypoints |

**Path inheritance (v1.3.x):** Patterns can use `prefix_from_parent` to inherit path prefixes from parent concepts. For example, NestJS route handlers use `prefix_from_parent: "controller"` to combine `@Controller('/users')` prefix with `@Get(':id')` path into `/users/:id`.

See [ADR-3aaa](adr/3aaa-architectural-analysis-and-revision-plan.md) for the design rationale and [UsageContext extension](adr/3ccc-usage-context-patterns.md) for call-based framework support.

### Entrypoint Confidence Tiers

These tiers apply to entrypoint detection. For edge confidence scoring, see [§12 Confidence scoring](#12-confidence-scoring).

Confidence scores reflect detection reliability, enabling meaningful ordering in sketch output:

| Tier | Confidence | Detection Method | Examples |
|------|------------|------------------|----------|
| 🟩 **Declared** | 0.99 | Manifest files | `pyproject.toml [project.scripts]`, `package.json "bin"`, `Cargo.toml [[bin]]` |
| 🟩 **Decorator/Annotation** | 0.95 | Explicit code markers | `@app.route`, `@click.command`, `@Controller`, `@RequestMapping` |
| 🟩 **Structural** | 0.85 | Strong conventions | `if __name__ == "__main__"`, class extends `Activity` |
| 🟩 **Naming** | 0.70 | Heuristic patterns | Class named `*Controller`, `*Handler`, `*Service` without annotations |

All four confidence tiers are now implemented. Naming-based detection serves as a fallback when no framework-specific patterns match.

### Scoring for Auto-Slice Entry Selection

When multiple entrypoints exist, scoring selects the most useful ones:

```python
score = confidence * (1 + log(1 + outgoing_edges)) * kind_boost
```

where `kind_boost` is 2.0 for main/CLI-main entrypoints (the canonical application root) and 1.0 otherwise. This prefers well-connected entries, producing richer slices.

## 9) Behavior map JSON

The survey is a JSON file produced by `hypergumbo survey`. It is a compiled view of the IR (see [§6 Output views](#output-views)) designed for programmatic consumption by agents and tooling. Field *semantics* (`id`, `stable_id`, `origin`, etc.) are defined once in [§6 Internal representation](#6-internal-representation) and not repeated here; this section covers serialization rules and output-specific fields.

Single file: `survey.json`

### Top-level structure
```json
{
  "schema_version": "0.14.4",
  "confidence_model": "hypergumbo-evidence-v2.0",
  "stable_id_scheme": "hypergumbo-stableid-v8",
  "shape_id_scheme": "hypergumbo-shapeid-v3",
  "repo_fingerprint_scheme": "hypergumbo-repofp-v2",
  "view": "behavior_map",
  "generated_at": "2026-01-15T10:30:00Z",
  "analysis_incomplete": false,
  "analysis_runs": [],
  "profile": {},
  "nodes": [],
  "edges": [],
  "features": [],
  "metrics": {},
  "limits": {}
}
```
Code also emits `usage_contexts`, `entrypoints` (documented below), and `sketch_precomputed` (internal cache artifact — not part of the public schema; consumers should not depend on its presence).

### JSON Schema (Auto-Generated)

A formal JSON Schema is available at `docs/schema.json`. This schema is **auto-generated** from the Python dataclasses in `packages/hypergumbo-core/src/hypergumbo_core/ir.py` to ensure it stays in sync with the implementation.

**Regenerate with:** `./scripts/generate-schema`

**Verify in CI with:** `./scripts/generate-schema --check`

The schema follows JSON Schema Draft 2020-12 and can be used for:
- Validating hypergumbo output files
- IDE autocompletion for consumers
- Documentation in a standard format

**DRY Principle:** The Python dataclasses (`Symbol`, `Edge`, `Span`, `AnalysisRun`) are the single source of truth. The JSON Schema and this spec document the *meaning* of fields; the dataclasses define the *structure*.

**Scheme identifiers** (`stable_id_scheme`, `shape_id_scheme`, `repo_fingerprint_scheme`): Identify the algorithms used to compute their respective fields. See [§6 Identity and provenance scheme versioning](#identity-and-provenance-scheme-versioning) for definitions and the versioning mandate.

**analysis_incomplete** (boolean, default: false):
- Set to `true` if analysis terminated early due to errors, timeouts, or resource limits
- When true, output is valid JSON but may be missing nodes/edges
- Check `limits.partial_results_reason` for details
- Agents should decide whether partial results are sufficient for their use case

### Confidence scoring

The `confidence` field on edges (0.0-1.0) indicates detection reliability. The `confidence_model` field (`hypergumbo-evidence-v2.0`) identifies the scoring algorithm. See [§12 Confidence scoring](#12-confidence-scoring) for the full confidence model and [Appendix C](#appendix-c-schema-compatibility-contract) for consumer obligations.

### analysis_runs[] — provenance tracking

Each entry records provenance for one analyzer pass. Field semantics are defined in [§6 Internal representation](#6-internal-representation); see `docs/schema.json` for the full field list.

**Output-specific note:** The IR field `pass_id` is serialized as `pass` in JSON output.

**skipped_passes** (array, optional; per-run): a legacy field mirroring the top-level `limits.skipped_passes` shape. Pass-level skips never appear here — a skipped pass never ran, so it has no `analysis_runs[]` record — so this per-run field has no current producer and is omitted when empty (INV-virik). For skip provenance, read the authoritative `limits.skipped_passes` (documented under [§9 limits — explicit gaps](#limits--explicit-gaps)). (INV-nihug.)

**pass_version** (string, INV-morag option A): real per-pass version derived from `sha256(inspect.getsource(<pass module>))`. Replaces the fake `-v1` suffix that previously lived inside `pass_id` with a value that actually changes when the pass implementation changes. INV-morag PR 2 propagated non-empty values to every registration site automatically via the `@register_analyzer` / `@register_linker` decorators and dropped the `-v1` / `-ts-v1` suffix from `pass_id` entirely.

**pass_id format (INV-morag PR 2):** the catalog ID and the runtime `pass_id` now come from the same source — the analyzer/linker's `@register_*` decorator name — and never carry a `-v1` / `-ts-v1` / `-ast-v1` suffix. Backend identity (ast vs tree-sitter vs pattern) lives in the `Pass.backend` catalog field, not in the ID. The `scripts/check-pass-id-agreement` CI gate asserts this invariant.

### reproducibility_context — what's captured, what's explicitly not

Top-level block introduced in INV-morag (option B) that documents the level of reproducibility this survey asserts. Reproducibility is a spectrum, not a yes/no claim; this block captures the L2 level (direct dependencies + runtime identity) and explicitly disclaims higher levels.

```json
{
  "reproducibility_context": {
    "level": "L2",
    "captured": {
      "hypergumbo_version": "2.0.2",
      "python_version": "3.12.3",
      "python_implementation": "CPython",
      "tree_sitter_version": "0.21.0",
      "grammars": {
        "tree-sitter-go": "0.23.4",
        "tree-sitter-language-pack:nim": "0.13.0"
      }
    },
    "not_captured": [
      "Transitive Python package versions (only direct deps...)",
      "OS version, kernel, libc, locale, timezone, environment variables.",
      "Hardware (CPU model, microcode, ...). Floating-point determinism..."
    ],
    "implications": "Behavior maps with matching pass_versions, ..."
  }
}
```

**Grammars captured are those actually used.** `captured.grammars` lists only the tree-sitter grammars whose analyzer pass produced ≥1 node (WI-fonod), pruned at the finalize chokepoint — grammars for detected-but-empty languages, and every installed-but-unexercised grammar, are dropped, and a repo analyzed only by ast-based analyzers (python) carries no `grammars` key at all. Pack-backed grammars appear as distinct `tree-sitter-language-pack:<lang>` entries at the shared pack version (WI-givad; the pack exposes no per-grammar version). (`analyzer_identity`'s cache key still folds in every installed grammar — that surface is deliberately install-scoped, not run-scoped.)

**Levels:** L0 (source content), L1 (pass logic via `AnalysisRun.pass_version`), L2 (direct deps — captured here), L3 (transitive deps), L4 (OS / libc), L5 (hardware). Hypergumbo commits to L2 and disclaims L3-L5.

**`not_captured` here is the REPRODUCIBILITY disclaimer, and is a different field from `limits.not_captured`** (WI-latip / WI-tubim). The two share a name under different parents and their contents are disjoint: this one lists determinism factors hypergumbo does not record (transitive deps, OS/libc/locale, hardware and floating-point), i.e. the L3-L5 levels disclaimed above; `limits.not_captured` lists *analysis-coverage* categories static analysis never captures anywhere (dynamic imports, eval, complex decorators) and is a universal static disclaimer identical for every repo. Both are declared in `docs/schema.json` with distinct descriptions. The shared name is documented rather than renamed: each is correct in its own block, both have consumers keyed to their current path, and a rename would be a breaking change purchasing no disambiguation the parent does not already provide — the same verdict as `edge_key` vs `stable_id` (WI-niboh) and the `sha256:` surface shared by `stable_id`/`shape_id` (WI-tisar).

**Cache correctness:** any change to a captured field invalidates the run signature. Diffs not explained by captured fields suggest a not_captured factor — file as a tracker item if isolatable.

**Consumer guidance:** when comparing two surveys, attribute differences along this priority: (1) `pass_version` change → analyzer logic changed; (2) `grammars[*]` change → grammar upgrade; (3) `tree_sitter_version` / `python_version` change → runtime upgrade; (4) unexplained → likely a not_captured factor.

### profile — repo characteristics

```json
{
  "languages": {
    "python": {"files": 42, "loc": 15230},
    "javascript": {"files": 18, "loc": 8420}
  },
  "frameworks": ["fastapi", "react"]
}
```

**File-count definition:** `languages[L].files` is the count of files the language-L analyzer would enumerate on this repo. When an analyzer registers a canonical `find_files` callable, that callable's output is the count — so extensionless shebang scripts (e.g., `.githooks/pre-commit`, `scripts/auto-pr`) appear in `languages.bash.files`, matching `analysis_runs[bash].files_analyzed`. Otherwise the count falls back to the language's extension globs (e.g., `*.py` / `*.pyi` for Python).

**LOC definition:** Lines of code counts non-empty lines in files matching language extensions — the SLOC convention shared with `cloc` and `tokei`'s "code" tally. Whitespace-only lines are excluded; comments are NOT stripped. Expect the number to run ~10-20% lower than raw `wc -l` for typical source files (the gap is blank lines). Lock files (poetry.lock, package-lock.json, etc.) are excluded. See [§15 File role classification](#15-file-role-classification) for the proposed taxonomy that would also exclude pure data files from LOC counts.

This per-file `loc` is the **authoritative** lines-of-code figure, counted once per file. It is distinct from the per-symbol `line_span` field on `nodes[]` (a physical `end_line - start_line + 1` span, blank/comment lines included, attached to each function/class/method). Because nested symbols overlap — a method span sits inside its class span sits inside its file span — `line_span` is **not summable**: `Σ(node.line_span)` overcounts physical lines (empirically ~1.66× the per-file total) and is not a meaningful quantity. Use `profile.languages[L].loc` for repo/language LOC; use `line_span` only per symbol (centrality dampening, test-density, dead-code ranking, slicing). (WI-palon.)

### nodes[] — definitions, files, endpoints

Field semantics (`id`, `stable_id`, `shape_id`, `fingerprint`, `origin`, `quality`, etc.) are defined in [§6 Internal representation](#6-internal-representation). See `docs/schema.json` for the full field list. This section documents output-specific serialization rules.

**Presence rule:** `stable_id` and `shape_id` keys MUST be present on every node. If unavailable, they MUST be set to `null` (not omitted). This supports forward-compatible consumers without forcing every pass to compute every field.

**signature** (string, optional — functions/methods): a human-readable *display* rendering of the parameter list and return type, e.g. `(a: int, b: str='hello') -> None`. Default values are shown verbatim (bounded per value so a pathological default cannot blow up the line — over-long or unparseable defaults render as `…`); for a rare over-length signature the parameter list is truncated with a `…)` marker while the **return type is preserved** (WI-hopiz). This display string is distinct from the structural signature that feeds `stable_id` (param count / arity flags — see [§6](#6-internal-representation)), so its width never affects identity.

**supply_chain** (object, required): Compiled from the IR's flat `supply_chain_tier` and `supply_chain_reason` fields into a nested object with an added `tier_name` field (e.g., `first_party`, `internal_dep`), computed from the numeric `tier` at serialization time. `Symbol.to_dict()` also **relocates** five top-level boolean flags into this object: `is_test_file`, `is_example_file`, `is_config_file`, and `is_generated_file` (file-role classifications, each independent of `tier`), plus `is_exported` (whether the symbol is part of the package's public API). See [§14 Supply chain classification](#14-supply-chain-classification) for tier definitions.

```json
"supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "matches ^src/", "is_test_file": false, "is_example_file": false, "is_config_file": false, "is_generated_file": false, "is_exported": true}
```

**Node kinds:** `file`, `module`, `function` (function/method), `class`, plus the rest of the canonical language-construct vocabulary (`method`, `interface`, `struct`, `enum`, `trait`, `contract`, …). Per ADR-0027, `Symbol.kind` names the source-language syntactic construct **only**; framework-endpoint participation (HTTP route, IPC handler, CLI entrypoint, etc.) is queried from `entrypoints[]` (see [§8](#8-entrypoint-detection)) and from `meta.concepts` on the node, not from `kind` itself. The pre-closure `endpoint` kind was retired in the SCHEMA_VERSION 0.6.0 fold (audit-findings 0009).

**Node.meta (`Symbol.meta`) fields.** Analyzer- and linker-emitted attributes of a node whose canonical `Symbol.kind` names only the source-language construct (ADR-0027). The registered vocabulary is the `symbol_meta` axis in `axis_meta_keys.py` (47 keys); language analyzers may additionally stamp unregistered per-language keys (sustained use of one is an ADR-0024 promotion signal). Values are strings unless noted; boolean-flag keys are present only when true. The dominant semantic key, `meta.concepts`, is documented separately at [§ meta.concepts Structure](#metaconcepts-structure).

*Framework role & routing:*
- `framework_role` (optional): framework-specific role of a generic-kind symbol (e.g. `event_publisher`, `route`, `graphql_resolver`); stamped by analyzers and framework-dispatch linkers (audit-findings 0013 fold residue).
- `route_path` (optional): URL path a `framework_role == "route"` handler registers (e.g. `/users`).
- `http_method` (optional): HTTP method of a route marker (`GET`, `POST`, `ANY`); some producers also carry transport sentinels (`WS`, `LIVE`, `RPC`) that `routes.route_of` lifts into `route_protocol`.
- `route_framework` (optional): framework name of a route marker (`flask`, `rails`); additive home read by `routes.route_of` (direct emission deferred, INV-vokak).
- `route_protocol` (optional): transport of a route endpoint (`http` | `websocket` | …), split from `http_method` (producer migration deferred, INV-tibap).
- `is_class_based_view` (optional, bool): Django class-based-view marker; set by the Django dispatch linker.

*External-boundary & supply-chain provenance:*
- `reference_syntax` (optional): use-site reference syntax of an `external_symbol` boundary node (`unresolved` / `attribute` / `module` / `namespace`; ADR-0036).
- `external_boundary` (optional, bool): `True` on synthetic boundary nodes minted for unresolved-but-referenced names.
- `ecosystem` (optional): distribution provenance of a tier-3 boundary dependency — `stdlib` vs `third_party` (ADR-0041 §3); absent when the language has no enumerated stdlib.
- `directness` (optional): manifest declaration relationship of an external dependency — `direct` / `transitive` / `undeclared` (ADR-0041 §2).

*File / build shape (synthetic file / package / dependency nodes):*
- `module_system` (optional): `esm` / `commonjs` (JS/TS).
- `component_framework` (optional): single-file-component framework (`vue`, `svelte`, `astro`).
- `package_ecosystem` (optional): package-manager registry on package nodes (`npm`, `composer`) — distinct from the `ecosystem` provenance key above.
- `entry_role` (optional): entry-point role on file nodes (`main`, `script`).
- `dependency_scope` (optional): dependency scope (`dev`).
- `install_mode` / `install_source` (optional): requirement install mode (`editable`) / source (`url`).
- `config_format` (optional): config-shape format (`tsconfig`).
- `task_implementation` (optional): task implementation language.
- `test_dialect` (optional): test-framework dialect (`robot`).
- `block_type` (optional): sub-classification of a generic `block` node (`datasource`, `generator`).

*Declaration & visibility shape:*
- `base_classes` / `parent_base_classes` (optional, list): direct / transitive ancestor base-class names (the latter from the `type_hierarchy` linker).
- `decorators` / `annotations` (optional, list): Python-style decorators / Java-Kotlin annotations (kept distinct).
- `visibility_signal` (optional): which signal set the typed `Symbol.visibility` — `language_modifier` / `name_convention` / `default` (INV-jusot).
- `exported` (optional, bool): exported from its file (ES `export`, Go capitalized name).
- `export_scope` (optional): finer export visibility (`module` / `package` / `public`) — **reserved; not currently emitted by any producer.**
- `export_source` (optional): originating source for re-exported symbols (resolves ES re-export chains).
- `override` / `virtual` / `abstract` / `static` (optional, bool): method/class modifiers (`static` affects call resolution — no implicit receiver).
- `is_local` / `is_recursive` / `is_native` (optional, bool): locally-scoped / recursive / native-bridge (JNI, N-API) markers.

*Signature & language annotations:*
- `parameters` (optional, list): structured parameter list (name/type/default); `params` is the short-form name-only variant.
- `return_type` / `inferred_return_type` (optional): declared / inferred return type.
- `display_name` (optional): human-readable name overriding the default.
- `scope` (optional): lexical scope qualifier (`workgroup`, `thread_local`), distinct from `visibility` / `static`.
- `import_path` (optional): source-language import path when it differs from the identifier.
- `documentation` (optional): docstring / leading-comment text.

*SCIP index round-trip (`scip/`):*
- `symbol_roles` (optional, int bitset): SCIP symbol-role bitset.
- `scip_kind` (optional): SCIP-native kind label kept alongside canonical `Symbol.kind`.

*Escape hatch:*
- `tags` (optional, list): free-form annotations not yet promoted to a named key.

### edges[] — relationships

Each edge carries `id`, `edge_key`, `type`, `src`, `dst`, `confidence` (evidence-derived detection reliability), `confidence_source`, `rank_score` (ranking prominence), provenance fields (`origin`, `origin_run_id`, `derived_from`), and a `meta` object with structured evidence. See `docs/schema.json` for the full field list.

**origin (INV-jidat):** `origin: list[str]` records which pass IDs contributed to this Edge (or Symbol), ordered chronologically. Single-element lists are the common case; multi-element lists support multi-pass attribution. Schema-breaking change from scalar string (SCHEMA_VERSION 0.10.0). `from_dict()` auto-normalizes legacy scalar JSON to single-element list for backward compatibility.

**derived_from (INV-rukor):** `derived_from: list[str] | null` records which Symbol (or Edge) IDs the producer consumed to construct this Edge. Populated by linkers (always non-null); null for analyzer-originated edges whose derivation is the AST itself. Enables answering "why does this edge exist?" without re-reading linker source code.

**Evidence provenance:** Each edge's `meta` carries the primary inference record via `meta.evidence_type` (the inference pathway, ADR-0028) and `meta.evidence_lang` (the source language, central-stamped at `Edge.create` per ADR-0040). (The `meta.evidence[]` multi-pass accumulator and `meta.evidence_spans` were descoped per ADR-0040 — never populated by any producer.)

**edge_key:**
- `edge_key` is a canonical identity used to deduplicate/merge multiple observations of the “same” relationship across passes.
- Format: `edgekey:sha256:<hash>`
- Recommended hash inputs (deterministic):
  - `type`
  - `src` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
  - `dst` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
- `id` remains a unique identifier for this edge record instance.

**Meta fields**:
- `evidence_lang` (optional): Language used for confidence scoring. Defaults to `src` node's language if omitted. Required for cross-language edges (HTTP, IPC) where src/dst languages differ.
- `protocol` (optional): Wire protocol for cross-language linker edges — `"http"`, `"grpc"`, `"graphql"`, etc. Set by Protocol-subcategory linkers (see [§7](#7-linkers)).
- `bridge_kind` (optional): FFI/bridge mechanism for Bridge-subcategory linker edges — `"native"` (JNI), `"wasm_bindgen"`, `"tauri_ipc"`, etc.
- `channel_kind` (optional): Channel discriminator for `event_publishes` edges — `"ipc"`, `"websocket"`, `"queue"`, `"message_bus"`, `"crdt"`. See [§7 IPC/Message Channel Detection](#ipcmessage-channel-detection).
- `framework_dispatch` (optional): The dispatch convention that produced the edge — a framework-specific convention (`"django_orm"`, `"django_third_party"`, `"phoenix_view"`) or a framework-agnostic dispatch mechanism (`"registry_dispatch"`, `"npm_package"`). One coherent "which dispatch convention recovered this edge" axis (ADR-0028 Cluster 28C), **not** a bare framework name (INV-junid); distinct from `detection_pattern` (pattern-shape match heuristics — how an edge was string/name/URL-matched). Replaces the per-framework `evidence_type` peers retired in ADR-0028 (audit-findings 0008/0012).
- `call_construct` / `receiver` / `resolution_quality` (optional): For Cluster-27C call-shape facts on `ast_call`-family edges. `call_construct` names the source-language call construct under `ast_call` (e.g. `method` / `function` / `pipe` / `application`); `receiver` classifies the call-site receiver — a per-language fold-residue label emitted only by analyzers whose call syntax carries receiver flavor (Ruby / Go / C# / Rust / C++), so it is absent on corpora that lack those languages (e.g. a pure-Python tree); the complete current producer vocabulary is `bare` / `external` / `constant_external` / `stdlib` / `typed_field` / `typed_var` / `field_chain` / `generic` (no consumer branches on it, and it mixes a resolution class with the `field_chain` expression shape — see `MetaKeySpec` for the re-evaluation trigger); `resolution_quality` is a pathway-quality label (e.g. `recovery` / `ambiguous`) orthogonal to `Edge.is_resolved`.
- `access_mode` / `data_direction` (optional, ADR-0038): Data-flow facts on data-carrying edges. `access_mode` classifies the source node's access at the edge — `read` / `write` / `mutate`. It is **applicable only to** the four data-carrying edge types `calls` / `references` / `module_attr_ref` / `event_publishes`; on the other edge types (structural relationships such as `contains` / `extends` / `imports` / `instantiates`) the question **does not arise** and the key is absent by design — so a 0% population rate on those types is intended N/A, not a coverage gap (INV-tibob; ADR-0038 ruling 2). The applicability matrix is the schema of record in `MetaKeySpec` (`axis_meta_keys.py`; resolvers `access_mode_applicable_edge_types()` / `access_mode_na_edge_types()` / `is_access_mode_not_applicable()`), which distinguishes "None = missing data on an applicable type, fix the emitter" from "None = does not arise on an N/A type". `data_direction` is the ADR-0038 bridge-constant direction key (the former `dest_access_mode` sibling was removed per ADR-0038 ruling 3).
- `io_boundary` (optional, ADR-0016): IO category a call reaches, drawn from the controlled vocabulary `fs_read` / `fs_write` / `net_send` / `net_recv` / `ipc_send` / `ipc_recv` / `env_read` / `env_write` / `subprocess` / `browser_storage_read` / `browser_storage_write` / `db_read` / `db_write` / `process_send` / `logging` (with `external_potential` and `command_launch` as known boundaries). The source of truth is `io_boundary.CATALOG_BOUNDARY_TYPES`. **Derived at consumer time, not persisted by producers — with one command-mediated exception:** the survey's catalog `calls` edges carry no `io_boundary` key; that classification is computed on demand from the `io_primitives/*.yaml` catalogs by `io_boundary.compute_boundary_map`, which the canonical surface `hypergumbo io-boundaries` invokes (see [§11 Slicing behavior](#11-slicing-behavior)). The exception is `command_launch` (WI-javoh): a command-mediated language like bash has no data-I/O catalog to match at consumer time — only the analyzer knows the shell grammar well enough to tell a program launch (`curl`, `git`) from a builtin (`echo`, `cd`) or an in-tree function call — so the bash producer *prestamps* `meta["io_boundary"] = "command_launch"` on each launch edge (`is_resolved=False`, an `ExternalRef` dst; deduped per caller/command), and the consumer-time aggregation loop picks it up structurally. This is the ADR-0016 impl-note ruling-A shape ("command-mediated languages populate the subprocess boundary by emitting unresolved external-command edges — not via an io_primitives data-I/O catalog"). The emitted/derivable vocabulary is exactly `io_boundary.KNOWN_IO_BOUNDARIES` (the 15 `CATALOG_BOUNDARY_TYPES` above + `external_potential` + `command_launch`); `hypergumbo io-boundaries` and `verify-claims` boundary constraints accept these and nothing else. `unknown_dynamic` is **reserved** by [ADR-0016](adr/0016-io-boundary-analysis.md) for runtime-computed call targets (`getattr(obj, name)()`) but is **not currently emitted** by any producer and is **not** in `KNOWN_IO_BOUNDARIES` — it is documented here only as the ADR-sanctioned name should that classification later be implemented (WI-datoz). (WI-fakuv / WI-puvun: the earlier "stamped on every call edge" contract was never realized in any producer — 0 of 110,533 self-corpus edges carried the key — so the consumer-time CLI, not a persisted field, is canonical for catalog boundaries. The key still appears on the *derived* edges that consumer-time views emit, and on `command_launch` producer edges per the exception above.)
- `io_primitive` (optional, ADR-0016): Fully-qualified primitive name (e.g., `"pathlib.Path.read_text"`) when `io_boundary` is set. Used for fine-grained provenance — exactly which built-in primitive a derived boundary funnels through. Like `io_boundary`, it is computed at consumer time, not persisted on the survey's edges.
- `taint_labels` (optional, ADR-0017): List of active taint tags on this edge (e.g., `["plaintext", "host_secret"]`). Populated only when `verify-claims` runs taint propagation.
- `taint_sanitized_by` (optional, ADR-0017): Name of the sanitizer that transformed taint on this edge (e.g., `"aes_gcm_encrypt"`). Present only when a sanitizer-categorized call sits on this edge.

**Evidence types** (machine-readable, see [§12](#12-confidence-scoring) for scoring algorithm). Per ADR-0028, `Edge.evidence_type` names the **inference pathway** by which the analyzer concluded the edge exists — not the framework dispatch, not the resolution status, not the call surface form. Resolution status is read from `Edge.is_resolved` (sibling field); framework dispatch is read from `meta["framework_dispatch"]`; call-construct facts are read from `meta["call_construct"]` / `meta["receiver"]` / `meta["resolution_quality"]`.

* `ast_call` — Call resolved from AST surface form (canonical for what was previously `ast_call_direct` / `ast_call_method` / `ast_getattr_call`; the syntactic shape moves into `meta["call_construct"]`)
* `ast_call_direct` — Direct function call in AST (retained for back-compat where the call_construct is unambiguous)
* `import_static` — Static import statement
* `import_dynamic` — Dynamic import (importlib, require with variable)
* `script_src` — HTML script tag src attribute
* `script_inline` — Inline script content
* `naming_convention` — Edge inferred from filesystem/string conventions rather than from a call site (view-template linker family, JNI native-method matching, …)

The axis is open: the registry at `packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py` is the live source of truth. See [§6 Multi-value field axes](#multi-value-field-axes).

**quality (`{score, reason}`) was REMOVED in schema 0.20.0** (ADR-0039 ruling 4; WI-humok / WI-riguh). It carried zero independent signal: `quality.score` was `round(clamp(confidence), 3)` (verified on 110,533/110,533 corpus edges) and `quality.reason` encoded the emitter mechanism, not a confidence tier. Read `confidence` + `confidence_source` + `is_resolved` (and `rank_score` for ranking prominence) instead. `Edge.from_dict` still tolerates a legacy `quality` key on pre-0.20.0 cached artifacts (it is ignored, not resurrected).

**Edge types** — per ADR-0023, `Edge.edge_type` names the **relationship** that produced the edge (canonical: `calls`, `imports`, `renders`, `event_publishes`, …), not the endpoint shape. Evidence-type and meta-key facts are queried separately (see Evidence types above and Meta fields above):

* `calls` — function/method invocation
* `imports` — module/symbol import
* `defines_target` — definition relationship
* ✅ `renders` — template rendering (Rails / Django / Phoenix / Spring MVC / Laravel Blade controllers → view templates)
* `implements` — class implements interface (Java, TypeScript, Go via `var _ Interface = &Struct{}`)
* `extends` — class extends base class. External/stdlib bases (`Enum`, `Exception`, `argparse.ArgumentParser`, Kotlin/Ruby library superclasses, …) are represented as unresolved-external edges (`is_resolved=False`, an `external_symbol` boundary dst), not dropped — recovered uniformly for every OO language by the framework-agnostic inheritance-linker chokepoint (WI-jubag Approach C) when the per-analyzer resolver leaves a base unresolved
* JNI Java native method → C implementation: canonical `calls` + `meta["bridge_kind"] = "native"` (see [§7 JNI bridge detection](#java-jni-cross-language-detection)); pre-WI-mifor-vabul this was a distinct edge_type `native_bridge`, retained as a deprecated registry entry until Phase 4b'
* IPC send (Electron / WebSocket / EventEmitter / message queue): canonical `event_publishes` + `meta["channel_kind"]` in `{"ipc", "websocket", "queue", "message_bus", "crdt"}` (see [§7 IPC/Message Channel Detection](#ipcmessage-channel-detection)); pre-WI-hahap-farid this was a distinct edge_type `message_send`, retained as a deprecated registry entry until Phase 4b'
* IPC receive: **dropped** (DEPRECATE-NO-FOLD per audit-findings 0002) — the forward `event_publishes` already captures the relationship; pre-WI-hahap-farid this was a distinct edge_type `message_receive`
* `instantiates` — class instantiation (constructor call)
* HTTP client→server: canonical `calls` + `meta["protocol"] = "http"` (see [§7 HTTP client-server linking](#http-client-server-linking)); pre-WI-vumum-juvil this was a distinct edge_type `http_calls`, retained as a deprecated registry entry until Phase 4b'
* ⬜ `manual` — user-annotated (not implemented)

### features[] — named slices

Each feature contains `id`, `name`, `entry_nodes[]`, `node_ids[]`, `edge_ids[]`, a `query` object, `limits_hit[]`, and — when non-empty — `node_depths`, `node_tiers`, and `admission_stats` (per-node BFS depth/tier maps and, for dataflow slices, edge-admission counters). The `query` object echoes the slice spec that produced the feature: `method`, `entrypoint`, `hops`, `max_files`, `exclude_tests`, `exclude_utility`, and `reverse` are always present, and `min_confidence`, `max_tier`, `language`, `hub_threshold`, `exclude_imports`, `pass_through_kinds`, and `dataflow` are echoed only when set to a non-default value (so the feature is reproducible from its JSON and its `id` hash stays stable). See `docs/schema.json` for the full structure.

**Feature ID:** Stable identifier based on query spec: `sha256(json.dumps(query, sort_keys=True))`. Same query on same code → same feature ID → enables diff across commits.

**Index, not content (WI-bujim).** The `node_ids[]` and `edge_ids[]` arrays are graph-ID pointers into the top-level `nodes[]` and `edges[]` arrays, NOT inline denormalized symbols/edges. This keeps the survey agent-readable in a single file without duplicating slice content. Full denormalized slice payloads (with `nodes`, `edges`, and `meta` keys per feature) are written separately as `slice.handler.<METHOD>.<path>.json` files under the `<out-stem>.slices/` subdirectory, plus a `slice.handler.index.json` companion. Consumers wanting just the discovery view read `features[]`; consumers wanting per-handler portability read the individual slice files.

**Compact-view re-projection (INV-titid).** Because the pointers index the same view's `nodes[]`/`edges[]`, the `compact` **and tiered** views re-project `features[]` onto their budget-limited selection rather than copying them wholesale: each surviving feature's `entry_nodes[]`/`node_ids[]`/`edge_ids[]` (and `node_depths`/`node_tiers`) are filtered to the retained sets, and a feature whose every entry node was pruned is dropped — mirroring how the views filter `entrypoints[]`. The index-pointer invariant therefore holds in *every* view: a feature's id pointers always resolve within that view's own `nodes[]`/`edges[]` (no dangling references). (WI-pohuf: the tiered view previously copied `features[]` wholesale — frequently the single largest field, e.g. ~25k tokens on a monorepo — which kept a small-budget tier far over budget because the shrink loop only removes nodes; re-projecting it is what lets a 4k/16k tier fit more than ~1 symbol.)

**Producer (default).** `hypergumbo survey` populates `features[]` from `_emit_handler_slices`: one entry per detected route handler, forward-slice with `exclude_tests=True` / `exclude_imports=True` / `hub_threshold=50`. Handlers over the cap (default 25) do not contribute to `features[]` but still appear in the index file with `emitted=False` so consumers can re-derive on demand. A framework route *marker* (`meta.framework_role == "route"`) whose forward slice recovers nothing beyond itself is likewise excluded from `features[]` as a content-free twin of its concept-handler feature (WI-rijop); its per-slice file and index entry are still written. Other entrypoint kinds (CLI, main, websocket, etc.) are not yet wired in but follow the same shape when added.

### entrypoints[] — detected entry points

🟩 Pre-computed, confidence-ranked array of execution entry points (HTTP routes, CLI commands, main functions, lifecycle hooks, etc.). Each entry references a node in the graph.

```json
{
  "entrypoints": [
    {
      "symbol_id": "python:src/app.py:10-25:get_users:function",
      "kind": "http_route",
      "confidence": 0.95,
      "label": "HTTP GET /users",
      "meta": {
        "id": "entrypoint:sha256:1a2b3c4d5e6f7a8b",
        "source": "concept_detector",
        "evidence_type": "framework_pattern"
      }
    }
  ]
}
```

**Fields:**
- `symbol_id`: Reference to a node (matches `nodes[].id`)
- `kind`: Entry point type (`http_route`, `cli_command`, `main_function`, `background_task`, `websocket_handler`, `library_export`, `shell_script`, `html_entry`, `script_module`, `connectivity_based`, etc.). The canonical, complete list is the `EntrypointKind` catalog, exposed as the lightweight catalog-derived `entrypoint-kind` axis via `entrypoints.all_known_entrypoint_kinds()` (WI-pupiz) — the single source the schema `Entrypoint.kind` enumeration is generated from. A *known* kind can be dark on a given corpus when no triggering construct is present (e.g. `cli_command` for argparse CLIs is a detection gap tracked under WI-lubap; `background_task` / `connectivity_based` are absent on a repo with no async-task decorators / where concept-based entrypoints were already found). The `shell_script` kind (INV-tajap) marks bash/sh executable scripts; emitted on the file-kind Symbol whenever the bash analyzer parses a file (every parsed bash file qualifies — `find_bash_files` already requires either a shebang or `.sh`/`.bash` extension). The `html_entry` kind (INV-tajap) marks SPA roots / convention-named `index.html` files; emitted on the file-kind Symbol when the filename is `index.html` (case-insensitive). The `script_module` kind (INV-tajap) marks TS/JS standalone-script files — file-kind Symbols in `typescript` / `javascript` that have **no inbound `imports` edges** (nobody imports the file) **and at least one outbound `calls` edge** (the file does work at module load). Unlike `shell_script` / `html_entry`, this rule requires the full edge set, so the detection runs in `detect_entrypoints` itself instead of via an analyzer-stamped `meta.concepts` entry. The `main_guard` kind (WI-tuvun) marks a Python module-level `if __name__ == "__main__":` script guard with no separate `main()` function — a **file**-target entrypoint, distinct from the **function**-target `main_function`, so `kind` alone disambiguates the target type (previously both were `main_function` and only the free-text label distinguished the file-vs-function target). When a script has both a `def main()` and the guard, the function-level `main_function` is canonical and supersedes the redundant same-path `main_guard` (INV-hosuh dedup). The `websocket_handler` kind (WI-kuvig) covers WebSocket endpoints — both definition-based handlers (NestJS gateways, Phoenix channels, etc., via a `websocket_handler` concept) and path-registered WebSocket routes (Starlette `WebSocketRoute`, whose minted route symbol carries the synthetic `meta.http_method == "WS"`); a route whose method is the `WS` marker classifies as `websocket_handler`, **not** `http_route` + `method=WS`, and the `WS` value is retained on the node's `meta.http_method` for backward-compat. Path-bearing WS routes use a `"WS <path>"` label (parallel to `"HTTP <method> <path>"`) so the handler-concept entrypoint and the route-symbol entrypoint for one route dedup to one.
- `confidence`: Detection confidence (0.0–1.0), reflecting pattern strength, penalties for test/vendor code, and connectivity boost
- `label`: Human-readable description
- `meta`: Provenance dict mirroring `Edge.meta` (WI-rukam) so a consumer can interpret an entrypoint's `confidence` the way it interprets an edge's. Keys (registered on the `entrypoint_meta` axis in `axis_meta_keys.py`):
  - `id`: stable content-hash identity `entrypoint:sha256:<16hex>` of (kind, symbol_id, label) — always present, auto-stamped. Confidence is **not** part of the identity (it is a ranking value, not a record key). Mirrors `Edge.id`.
  - `source`: producer pass that emitted the record — `concept_detector` (YAML-concept matches), `connectivity_fallback` (the no-patterns-matched top-N-by-out-degree fallback), or `script_module_detector` (the edge-set-dependent TS/JS standalone-script rule). Mirrors `Edge.origin`.
  - `evidence_type`: the inference pathway — `manifest_declared`, `framework_pattern`, `structural`, `language_convention`, `naming_heuristic`, `connectivity_heuristic`. It is a **coarser grouping than the confidence bases, NOT a 1:1 mapping** (WI-sohov/WI-hofav): the base is chosen per *detector*, and several detectors share one `evidence_type` at different bases. Measured on the self-corpus, three of the four emitted pathways carry two bases each — `framework_pattern` {0.95, 0.90}, `structural` {0.85, 0.80}, `language_convention` {0.80, 0.75} — and only `manifest_declared` {0.99} is single-valued. Consumers must read `confidence` directly and must not infer it from `evidence_type`, or the reverse. A separate vocabulary from the edge inference-pathway registry (`evidence_types.py`); entrypoint detection methods do not overlap edge inference pathways. Mirrors `Edge.evidence_type`.

**Confidence tiers** (see [§8](#8-entrypoint-detection) and [§12](#12-confidence-scoring)):
- 0.99: Manifest-declared (package.json `bin`, Cargo.toml `[[bin]]`, pyproject.toml `[project.scripts]`) — `manifest_declared`
- 0.95: Framework patterns (decorators, base classes; routes, websocket handlers) — `framework_pattern`
- 0.90: Framework patterns whose match is a *shape* rather than a registration — forms, serializers — `framework_pattern`
- 0.85: Structural (Python `if __name__ == "__main__"`, shebang scripts, HTML entries) — `structural`
- 0.80: Language conventions (`main()` function) and standalone script modules — `language_convention` / `structural`
- 0.75: **Library exports** — a module's public surface, inferred from the export convention rather than from any call site — `language_convention`
- 0.70: Naming heuristics (`*Controller`, `*Handler`, `cmd_*`) — `naming_heuristic`
- 0.50: Connectivity-based fallback (top-N most-connected callables when no patterns match) — `connectivity_heuristic`

The 0.90 and 0.75 rows were absent from this table until WI-logad, which is
worth noting because 0.75 is not a rare corner: `library_export` is the single
most common entrypoint kind on hypergumbo's own corpus (45 of 118, 38%), so the
documented table omitted the base that the plurality of entrypoints actually
carry. The 0.70 and 0.50 rows are real but unexercised on this corpus — they are
producer constants (`naming_heuristic`, `connectivity_heuristic`), not
deletions-in-waiting.

**Penalties and boosts apply to `rank_score`, not to `confidence`** — the move [ADR-0039](adr/0039-confidence-separation.md) described as "slated" has shipped (WI-lutad / WI-dojor). `confidence` is now pure detection reliability and carries only the base above; `rank_score` starts from that base and then takes the ranking adjustments: test files ×0.1, vendor/external deps at tier ≥ 3 ×0.3, utility files ×0.5, plus an additive connectivity boost of `min(0.25, log(1 + out_edges) / 10)`. Deliberately demoted entries — build wrappers, vendored exports, infrastructure-path exports — are excluded from that boost, because an additive boost otherwise undoes a multiplicative demotion and the entry climbs back over the `MIN_ENTRYPOINT_CONFIDENCE` floor. Both fields are emitted on every entrypoint (measured: 118 of 118 carry `rank_score`, spanning 0.345–1.000, while `confidence` holds the discrete bases). This resolves the contradiction WI-sohov filed between the discrete-tier and continuous descriptions of the same field: they were describing what are now two different fields.

**Sorting:** Ranked by `rank_score` (highest first), not by `confidence` — ranking prominence is what ordering is for, and the two diverge whenever a penalty or boost applies (ADR-0039).

**Not redundant with nodes:** While nodes carry `meta.concepts` metadata from framework pattern matching, the `entrypoints` array provides pre-computed confidence (with penalties and boosts), ranking, and labeled kinds. Consumers would otherwise need to iterate all nodes, check concepts, apply scoring logic, and sort. Used by sketch generation, slicing, and compact output.

### usage_contexts[] — framework pattern evidence

🟩 Intermediate representation of how symbols are *used* (as opposed to how they are *defined*). Each entry records a call site, data value, export, or macro invocation that gives semantic meaning to a symbol through its usage context. See [ADR-3ccc](adr/3ccc-usage-context-patterns.md) for design rationale.

```json
{
  "usage_contexts": [
    {
      "id": "usage:sha256:<16hex>",
      "kind": "call",
      "context_name": "app.get",
      "symbol_ref": "javascript:src/routes.js:10-25:listUsers:function",
      "position": "args[1]",
      "metadata": {"http_method": "GET", "route_path": "/users"},
      "path": "src/app.js",
      "span": {"start_line": 5, "end_line": 5, "start_col": 0, "end_col": 40}
    }
  ]
}
```

**Fields:**
- `id`: Unique identifier
- `kind`: Context type (`call`, `data_value`, `export`, `macro`)
- `context_name`: Function/variable/file name where usage occurs
- `symbol_ref`: ID of the symbol being used (may be null if unresolved)
- `position`: Where in the context the symbol appears (e.g., `args[1]`, `:get`, `default`)
- `metadata`: Context-specific data (e.g., `http_method`, `route_path`)
- `path`: File where usage occurs
- `span`: Source location

**Role in the pipeline:** Usage contexts feed into framework pattern matching (`enrich_symbols()`), which adds concepts to symbols, which in turn drive entrypoint detection and linker behavior. Most consumers should use the enriched `nodes` and `entrypoints` rather than processing `usage_contexts` directly.

**Slim node projection (WI-pohuf).** The compact and tiered views emit a **slim per-node projection**, not the full survey node: each node carries only `id`, `name`, `qualified_name`, `kind`, `language`, `path`, `span`, `signature`, `docstring` (null-valued optionals omitted) plus the annotated `centrality`. The identity hashes (`stable_id`/`shape_id`/`fingerprint`), provenance (`origin`/`origin_run_id`), the `supply_chain` block, and `meta` are dropped — they cost ~250 tokens/node and, at small budgets, forced the shrink loop to trim a tier down to a single symbol. Consumers needing identity/provenance/supply-chain detail read the full survey.

**Stripped from compact/tiered views** to reduce payload size, alongside `sketch_precomputed` (an internal cache artifact). The two budget-limited projections differ deliberately on provenance/quality signals: the more aggressive **tiered** view additionally drops `analysis_runs` and the `validation_report`, whereas the **compact** view *preserves* those two (finalize provenance + quality signals, per ADR-0033/ADR-0043) while still dropping `usage_contexts` and `sketch_precomputed`.

### metrics — optional counts

Aggregate statistics: `total_nodes`, `total_edges`, `total_files`, `avg_confidence`, per-language breakdowns (`languages.*`), and per-tier breakdowns (`by_supply_chain_tier.*`). Each per-tier breakdown includes `nodes`, `edges`, and `edges_incident` counts. `edges` counts each edge once by its **source** node's tier, so the per-tier `edges` sum reconciles to the source-resolved edge total; because external-dependency tiers (2/3) are graph **sinks**, not sources, their `edges` typically reads ~0. `edges_incident` (WI-modom) counts an edge once per **distinct endpoint tier** (either-endpoint), exposing each tier's actual graph contribution (a tier-3 dependency referenced by N edges shows N incident, not 0); it double-counts cross-tier edges by design and so does **not** sum to `total_edges`. `by_supply_chain_tier` enumerates only the tiers present in the **analyzed** node set — in practice the analyzed tiers `first_party` / `internal_dep` / `external_dep` (1-3). Tier 4 (`derived`) is excluded from analysis ([§14](#14-supply-chain-classification)), emits no nodes, and so never appears here; it surfaces solely as [`supply_chain_summary.derived_skipped`](#supply_chain_summary--classification-overview). The two summary surfaces therefore carry **different tier sets by design** (WI-nibul) — an always-empty `derived` bucket here would be a structurally-always-0 field, which ADR-0040 forbids (cf. the identical rationale for `derived_skipped` carrying `{files, paths}` rather than `{files, symbols}`).

**Per-language attribution (`languages.<lang>`).** Each node is bucketed by `node.language`, falling back to its `discovery_language` when `node.language` is `None`: ADR-0031 Class-B synthetic stand-ins (linker-emitted protocol/host symbols) carry no primary language and are attributed to their host discovery language so cross-language metrics stay meaningful; a node with neither falls to `unknown`. Consequently `languages.<lang>.nodes` counts both native-`<lang>` nodes and these heuristically-attributed synthetic nodes, so its value can exceed the number of nodes whose `node.language == <lang>`. `languages.<lang>.edges` counts each edge once by its source node's so-attributed language.

**Relationship to `profile.languages` (WI-ziror).** `metrics.languages` and `profile.languages` are **complementary views over different populations**, so their key-sets legitimately differ — do not expect them to match or to be joinable by language key. `profile.languages` is a **file inventory** (`{files, loc}`) keyed by the language whose analyzer *enumerates* each file (extension / `find_files`); `metrics.languages` is an **extracted-graph inventory** (`{nodes, edges}`) keyed by each symbol's analyzer-assigned `node.language`. Three structural reasons the sets diverge: (1) a language with files but no graph nodes appears only in `profile` — e.g. `markdown`, whose symbols feed `sketch_precomputed` centrality rather than the main `nodes[]`; (2) a **content-detected specialization** appears under its *extension* language in `profile` and its *analyzer-detected* language in `metrics` — e.g. an Ansible playbook is a `.yaml` file counted under `yaml` in `profile` but emits `ansible` symbols in `metrics`; (3) synthetic linker stand-ins with no source language appear only in `metrics`, bucketed as `unknown`. The two surfaces are intentionally not unified: forcing agreement would either erase the analyzer's finer classification or require the pre-parse file scan to parse every file.

**`total_files`** is the canonical "how many files in this repo?" answer: `sum(profile.languages[L].files)`. It agrees with `profile`'s per-language counts (which in turn agree with `analysis_runs[L].files_analyzed` for languages whose analyzer registered a canonical `find_files`).

**`metrics.debug`** carries three non-canonical file counts for introspection:
- `unique_paths_in_analysis` — distinct `node.path` values across every symbol kind. Excludes files that contributed no symbols (binary, unparseable, unsupported language), so it under-counts repos with many file types relative to `total_files`.
- `analyzed_file_symbols` — count of `nodes[*]` with `kind == "file"`. Only analyzers that emit file pseudo-nodes contribute, so this can lag the true file count if some language analyzers don't yet stamp them.
- `profile_files_sum` — the sum of `profile.languages[*].files`. Counts a file once per language that claims it, so a file two analyzers both enumerate is counted twice; it therefore over-counts relative to `unique_paths_in_analysis`, which de-duplicates by path. Emitted since the profile-based `total_files` landed and documented here from WI-mikin.

When the headline `total_files` is computed without a profile (e.g., by callers outside the full `run_survey` pipeline), it falls back to `unique_paths_in_analysis` for backward compatibility — the same value still rides in `metrics.debug` so consumers can tell which definition they're looking at.

### supply_chain_summary — classification overview

Per-tier file and symbol counts (`first_party`, `internal_dep`, `external_dep`), plus a `derived_skipped` object listing files excluded from analysis. Each tier's `files` counts distinct paths of that tier's **`kind=="file"` nodes only** (not distinct paths across all tier nodes — a symbol-level count would fold in function paths and the `<external>` sentinel path of `external_symbol` nodes, inflating the count with phantom "files"; WI-mutuv); `symbols` counts every node of the tier regardless of kind. `derived_skipped.paths` is capped at 10 entries; full list available via `--verbose`. Unlike the analyzed tiers, `derived_skipped` intentionally carries `{files, paths}` rather than `{files, symbols}`: tier-4 derived artifacts are excluded from analysis entirely (§14), so they emit no symbols and a `symbols` count would be a structurally-always-0 field carrying no signal (cf. ADR-0040's declared-⇒-populated discipline). In its place `paths` is a genuinely different quantity — a capped sample of *which* derived files were skipped, a diagnostic for spotting a source file misclassified as derived — and per ADR-0039 ("a new quantity gets a new name") it is named for what it is rather than forced into the sibling shape. `derived_skipped` is thus an exclusion-disclosure bucket, not a peer tier: its `files` counts files *excluded from* analysis, not files *classified into* a tier (WI-gabos). The `external_dep` tier carries an `ecosystem` sub-object counting tier-3 symbols by provenance class (`stdlib` / `third_party` / `unknown`), per the ADR-0041 §3 ecosystem axis.

### limits — explicit gaps

Documents what the analysis *didn't* capture. Key arrays:
- `not_captured[]`: A **universal static disclaimer** — the fixed set of construct categories static analysis never captures anywhere (e.g., dynamic imports, eval, complex decorators). Identical for every repo; NOT a per-repo measurement of constructs this repo contains-but-skipped. (Distinct from `reproducibility_context.not_captured`, which lists reproducibility factors.)
- `truncated_files[]`: Files skipped due to size, with path, size, and reason
- `skipped_languages[]`: Languages with unavailable grammars
- `failed_files[]`: Files that caused parse errors, with path, reason, and analyzer ID
- `skipped_passes[]`: the single authoritative record of **pass-level** skips — an analyzer pass that produced no `analysis_runs[]` entry. Every registered analyzer resolves to exactly one of an AR **or** a skip record (WI-didil — completeness across the analyzer catalog); the reasons emitted are: missing optional dependencies (e.g., `{"pass": "lean", "reason": "tree-sitter-lean grammar not available"}`); `no files matched` — either the WI-jadig file-presence pre-filter (an analyzer whose declared languages all have `files == 0` in `profile.languages` is short-circuited at the dispatcher, saving the wall-clock cost of opening a parser for a pass with no input) **or** an analyzer whose declared language is absent from the taxonomy (so the pre-filter cannot see its file count and dispatches it defensively) that then finds no matching files, recorded `{"pass": "<lang>", "reason": "no files matched"}` either way; an opt-in backend that stayed off, e.g. `{"pass": "rust_analyzer", "reason": "rust-analyzer backend not enabled"}` (distinguished from `no files matched` precisely because the repo may contain the language's files — the backend simply did not run); and a contained pass crash, recorded as `{"pass": "<lang>", "reason": "crashed: <ExcType>: <msg>"}` (§17). Non-analyzer passes (linkers, synthesis) are **not** enumerated here — a linker with no applicable targets is a correct no-op, not a "pass that did not run"; only the analyzer catalog carries the AR-or-skip completeness contract. Because a skipped pass produces no `analysis_runs[]` entry, this top-level list — not the per-run `analysis_runs[].skipped_passes` — is where pass-level skips live; it is always present (empty list = "no pass was skipped"). (INV-nihug.)

**partial_results_reason** (string, optional): Human-readable explanation of why results are partial (e.g., `"Timeout: Analysis exceeded 300 seconds"`, `"one or more passes crashed; results are partial"`, `"some files skipped during analysis"`). Present whenever any partiality was recorded; **omitted when there is none** (WI-tamop).

**This is NOT tied to `analysis_incomplete` (WI-zafid).** This field previously read "present only when `analysis_incomplete: true`", which contradicted the 🟩 analyzer-crash behaviour documented under [§17](#17-error-handling) — that path prescribes setting `partial_results_reason` while stating `analysis_incomplete` is *not* set. The crash rule is the correct one and the constraint here was the defect: `analysis_incomplete` means the analysis **terminated early** (§ analysis_incomplete), whereas every fail-open drop — a crashed pass, an unparseable file, an oversize file — leaves the run to complete over everything else. So partial results are routinely produced *without* early termination, and a consumer must read this field rather than infer completeness from the flag alone. The per-channel detail lives in `limits.failed_files` / `limits.truncated_files` / `limits.skipped_passes` and `analysis_runs[].files_skipped`.

**max_tier_applied** (integer, optional): The supply-chain tier ceiling that was in effect for this run, recorded when a `--max-tier N` filter was applied (e.g. `--max-tier 1` restricts analysis to first-party code). `null`/absent when no tier filter was applied and every tier was analyzed. See [§14](#14-supply-chain-classification) for the tier definitions and [§3](#3-user-experience-cli) for the `--max-tier` flag. (`analyzer_version` — the other scalar recorded in this block — is documented under [Version fields: three independent axes](#version-fields-three-independent-axes); it carries a `hypergumbo-` prefix and is a tool/package version, not a schema version.)

## 10) Sketch output

Markdown output to stdout (not a file). This is the default output mode. Designed for pasting into LLM chat interfaces. See [ADR-0005](adr/0005-sketch-budget-allocation.md) for detailed budget allocation and section composition.

**Section order (in priority for truncation):**

| Order | Section | Purpose |
|-------|---------|---------|
| 1 | 🟩 Header | Title, description |
| 2 | 🟩 Overview | Language breakdown, file counts, LOC |
| 3 | 🟩 Structure | Tree built from important files |
| 4 | 🟩 Frameworks | Detected frameworks/libraries |
| 5 | 🟩 Tests | Test file count, frameworks, coverage estimate |
| 6 | 🟩 Configuration | Config file excerpts (heuristic + semantic) |
| 7 | 🟩 Entry Points | CLI commands, HTTP routes |
| 8 | 🟩 Data Models | ORM models, entities, core data structures |
| 9 | 🟩 Source Files | File listing by importance density |
| 10 | 🟩 Key Symbols | Functions, classes, types with centrality |
| 11 | 🟩 Additional Files | README-first + hybrid round-robin |
| 12 | 🟩 Source Content | Actual code (--with-source only) |
| 13 | 🟩 Additional File Content | Code for semantic picks (--with-source only) |

**Token budget:** `-t N` truncates at section boundaries, preserving higher-priority sections. With `--with-source`, budget shifts from file listings to actual source code.

### Additional Files selection

The Additional Files section uses a README-first hybrid ranking algorithm:

1. **README always first:** The project's README is placed first. If it exceeds the token budget, it's truncated and no other files are included.

2. **Round-robin selection:** Remaining files are selected by cycling through three sources:
   - README-linked files (internal links extracted from the README)
   - Similarity-ranked files (semantic similarity to project description)
   - Centrality-ranked files (symbol mention frequency)

3. **Multi-format link extraction:** Supports Markdown (inline + reference-style), Org-mode, RST, and AsciiDoc link syntaxes. Resolves relative paths, absolute paths, and forge URLs (GitHub/GitLab/Codeberg).

4. **Dynamic truncation:** When budget is limited, files are truncated based on median token count of already-selected files, with a 500-token floor.

**Example round-robin order:**
```
1. README.md (always first)
2. CONTRIBUTING.md (linked from README)
3. docs/overview.md (similarity-ranked)
4. config.yaml (centrality-ranked)
5. INSTALL.md (linked from README)
6. docs/api.md (similarity-ranked)
...
```

**Example:**
```markdown
# minetest-wasm

## Overview
C++ (82%), Lua (12%), CMake (6%)
847 files (712 non-test + 135 test)
~120,000 LOC (~105,000 non-test + ~15,000 test)

## Structure
/path/to/minetest-wasm/
├── CMakeLists.txt
├── src
│   ├── main.cpp
│   └── [and 234 other items]
├── builtin
│   └── [and 45 other items]
└── [and 12 other items]

## Frameworks
- cmake
- lua

## Entry Points
- `main` (Cli main) — src/main.cpp
- `Client::Client` (Constructor) — src/client/client.cpp
```

For a complete real-world example (install, run, and full JSON output), see [example-output.md](example-output.md).

## 11) Slicing behavior

Entry sources (HTTP routes, CLI mains, IPC handlers, etc.) are detected by the pattern system; see [§8 Entrypoint detection](#8-entrypoint-detection).

### Slicing algorithm

🟩 BFS traversal on the call graph from entry nodes, bounded by file count limit (`--max-files`, default 100) and hub pruning (threshold 50). No hop limit is imposed by default; `--max-hops` is available for explicit user control. Edges can be filtered by confidence threshold or test exclusion.

### Dataflow slicing (ADR-0015)

🟩 `--dataflow` flag restricts BFS to data-dependency chains. Access modes (`read`, `write`, `mutate`, `delete`) are stamped automatically by Tier 1 (YAML-driven AST classification for 104 tree-sitter analyzers + Python `ast` module) and explicitly by Tier 2 (16 linkers). YAML patterns shipped for 20 languages (Python, JavaScript, TypeScript, Rust, Go, Java, Kotlin, C, C++, C#, Dart, Elixir, Erlang, Haskell, Lua, Perl, PHP, Ruby, Scala, Swift).

**Forward-slice admission rule (ADR-0015 §6, "option 1"):** an edge enters the forward slice when **any** of the three rules below holds. The rules are ordered for clarity, not precedence — `OR` semantics apply.

1. **Writer-source.** Edge originates from a node whose `access_mode` is `write` or `mutate` (the writer "sources" data downstream).
2. **One-hop downstream-read terminal.** Edge terminates at a read site one hop downstream of an admitted writer — captures the immediate read of newly-written state and stops there to avoid the whole-program closure.
3. **Graceful degradation.** Edges with no `access_mode` annotation are admitted unconditionally so that linker coverage gaps don't silently truncate slices.

Option 2 (symmetric `dst_mode` OR-check) was evaluated and deferred per ADR-0015 §6.1 on empirical evidence (the former `SliceResult.admission_stats.would_admit_dst_reader` telemetry counter measured zero additional admissions on the audit corpus); option 3 (full dataflow) is deferred indefinitely. That telemetry counter has since been **removed** (ADR-0038 ruling 3 / vocab F4 PR2 deleted the `dest_access_mode` edge-meta key it read); re-evaluating option 2 would require re-deriving destination access from AST role per ADR-0038 ruling 1, not the retired sibling field.

Reverse slices follow read edges (no per-edge admission rule — every read edge backward is admitted).

### Slice identity and reproducibility

Each feature gets a stable `id` based on its query specification:

```python
slice_id = "sha256:" + sha256(json.dumps(query.to_dict(), sort_keys=True)).hexdigest()
```

Query format enables exact reproduction:

```json
{
  "method": "bfs",
  "entrypoint": "fastapi_route:/api/login",
  "hops": 3,
  "max_files": 50,
  "exclude_tests": true
}
```

Feature comparison across commits: same query → compare `node_ids`/`edge_ids` to detect changes.

### Tier filtering

🟩 The `--max-tier` flag (defined in [§3](#3-user-experience-cli); tier definitions in [§14](#14-supply-chain-classification)) adds tier-based traversal boundaries to slicing: BFS traversal skips nodes whose supply chain tier exceeds the specified value. For example, `--max-tier 1` constrains the slice to first-party code only.

### Reverse slice class expansion

🟩 When reverse-slicing from a class/interface entry (e.g., `--reverse --entry OwnerRepository`), the slicer auto-expands the BFS starting set to include all member methods (via `contains` edges). This enables finding callers of `findById`, `search`, etc. Applies to class, interface, module, struct, trait, enum, and file containers.

**Scope: expansion is only as complete as member emission (WI-duguk).** The slicer expands all seven container kinds unconditionally, but the expansion has an effect only where the *analyzer* emitted the container's members as symbols — no member symbol means no `contains` edge to follow, and the reverse slice returns the container alone. That is indistinguishable, to a consumer, from "this type has no users". The property is locked per-`(language, container-kind)` by the G2 emission-parity matrix (`packages/hypergumbo-core/tests/test_emission_parity_matrix.py`, columns `emits_enum_members` / `emits_abstract_members`). **No vacuous cells remain.** Every gated `(language, container-kind)` pair emits its members, and both columns are hard locks. Measured on the drain: `slice --entry Color --reverse` returns 4 nodes where it returned 1 (rust, typescript, csharp), 5 where it returned 2 (java), and `--entry Drawable` returns 4 where it returned 2 (rust, typescript, swift).

Note that member emission is **necessary but not sufficient**: the container's `kind` must also appear in `linkers.containment.CONTAINER_KINDS` (so the `contains` edge is minted at all) and in the slicer's own container set (so an entry of that kind is expanded). `protocol` was in neither, so Swift protocol containment was structurally impossible no matter what the analyzer emitted — the emission-parity columns measure one analyzer in isolation and cannot see that.

### Taint-flow analysis (ADR-0017)

🟩 Taint-flow analysis tracks labeled data (e.g., `plaintext`, `key_material`) from sources through the call graph to sinks, checking whether prohibited flows exist and whether sanitizers (e.g., encryption) intervene. It is the engine behind `verify-claims` taint-flow constraints.

**Taint catalogs.** The source, sink, and sanitizer sets used by `verify-claims` come from two layers:

* **Auto-derived from `io_primitives/*.yaml`.** Each IO primitive in a write-side category becomes a taint sink at `trust_level: untrusted` in a matching trust zone: `fs_write` → `host_fs`, `net_send` → `network`, `subprocess` → `subprocess` (WI-bibuk: split from `host_fs` so legitimate `subprocess.run` calls don't surface as host_fs violations), `env_write` → `host_env`, `ipc_send` → `ipc`, `browser_storage_write` → `browser_storage`, `db_write` → `database`, `process_send` → `ipc`, `logging` → `logging`. Each IO primitive in a read-side sensitive category becomes a taint source: `env_read` → label `host_secret`, `net_recv` → label `untrusted_input`, `ipc_recv` → label `untrusted_input`, `db_read` → label `untrusted_input`. The read-side categories `fs_read` and `browser_storage_read` are intentionally absent from the auto-source map — sensitivity of a file or browser-storage read depends on what is stored, so the default is quiet and project-local catalogs (see `taint_sources/`) can opt in entries relevant to the threat model. The auto-derivation covers every language with an io_primitives catalog (Python, Rust, Go, Java, JavaScript/TypeScript, and the rest), and includes attribute-kind primitives such as `os.environ`, `sys.argv`, `process.env`, `os.Stdout`, and `System.out` via `module_attr_ref` edges emitted by the Python AST analyzer (WI-guhok) and, for tree-sitter languages, the `emit_module_attribute_refs` helper wired into Go / JS / TypeScript / Java analyzers (WI-lozug PRs 1–3). Tree-sitter Rust (`std::env::consts::OS`) and C (bare `stdout` / `stderr` / `stdin` identifiers) are pending: Rust's scoped-path grammar needs a cross-language helper extension tracked as WI-vipur (also covering C++ `std::cout` / `std::cerr` / `std::cin`, Groovy, Kotlin); C's bare-identifier stdio globals need either an io_boundary identifier-reference matching mode or a c.yaml shape change, tracked as WI-gotuv. The `rust-analyzer` optional backend is unaffected by the tree-sitter gap and continues to resolve Rust semantics fully.
* **YAML-declared domain-specific labels and sanitizer transforms.** Files under `taint_sources/`, `taint_sanitizers/` alongside `taint.py` carry entries the auto-layer cannot express (built-in sinks are auto-derived only — there is no `taint_sinks/` directory) — cryptographic labels (`plaintext` from decryption, `key_material` from key generation in `taint_sources/crypto.yaml` and `taint_sources/key_material.yaml`) and sanitizer transforms (encryption: `plaintext` → `ciphertext`, `key_material` → `derived_key` in `taint_sanitizers/encryption.yaml`). YAML entries that match an auto-derived entry by `(module, name, kind)` replace the auto default — this is the mechanism by which a user catalog can swap a sink into a sanitizer, raise `trust_level` for a sink that is safe in context, or introduce a new trust zone for a project-specific sink.

**Trust-zone layering.** The trust-zone vocabulary has two layers:

* **Built-in (8 zones, auto-derived).** Sourced from `AUTO_SINK_ZONE_MAP` in `taint.py`, derived from `io_primitives/*.yaml` boundary categories: `host_fs` (from `fs_write`), `subprocess` (from `subprocess`), `network` (from `net_send`), `host_env` (from `env_write`), `ipc` (from `ipc_send`; `process_send` also maps to `ipc`), `browser_storage` (from `browser_storage_write`), `database` (from `db_write`), `logging` (from `logging`). These are the only zones a freshly-installed `hypergumbo verify-claims` knows about with no project configuration.
* **Project-local (open set).** Projects contribute additional zones through YAML files passed via `--taint-sinks` or `extra_catalogs:`. Two worked examples ship with the repo: PlazaFlow uses `relay`, `compute_host`, `persistent_storage` (see ADR-0017 §"PlazaFlow context"); hypergumbo's own self-claims use `dev_zone`, `install_artifact`, `tmp_artifact`, `user_cache`, `user_out` (see `docs/hypergumbo-self-catalog/`). User-defined zones are first-class — `verify-claims` constraints reference them identically to built-in zones.

**Built-in taint labels:** `host_secret`, `untrusted_input`, `plaintext`, `key_material`, `ciphertext`, `derived_key`.

🟩 **Analysis scope: production sources by default, exclusions disclosed (WI-bifob).** A taint-flow verdict is a **disjunction** over its flows, so a single flow is enough to hold a claim at `violated` and no precision improvement elsewhere can move it. Flows whose **source** lives in test, fixture or migration code are therefore excluded from taint-claim verdicts by default: a test that opens a listener is not a network-exposure finding about the product, and a migration that writes to the database is not an untrusted-input finding. Classification is on the source side — where data *enters* — because a test reaching a real network primitive is still a test doing its job, while a production function reaching a primitive inside a test helper is still a production flow. The predicates are `paths.is_test_file` (the broad test-OR-support predicate, also used for entrypoint ranking and slice filtering) and `paths.is_migration_file` (Django `**/migrations/`, Rails `db/migrate/`, `alembic/versions/`, Flyway `db/migration/`; `versions/` matches only under `alembic/` and `migrate`/`migration` only under `db/`, since those words are too common to claim outright). Exclusions are never silent: every verdict carries an `excluded_flows` bucket keyed by reason (`test_sourced` / `migration_sourced`), reported on `confirmed` verdicts as well as `violated` ones, and `--include-non-production-sources` restores the previous every-source behavior. This is the [D7](adr/) shape — a production default plus labelled disclosure buckets — rather than a filter, because a silent drop makes the tool quieter without making it more honest. Measured on the 9-repo validation cohort: 2 of 18 violated claims rested entirely on one test-sourced flow each and became `confirmed`; evidence rows fell 354 → 270 while 692 flow-claim pairs were excluded, the gap being claims that remain over the `_MAX_EVIDENCE_ROWS` cap where removing flows changes *which* rows are shown rather than how many.

🟩 **Flow origin is reported, not folded into the label (WI-vazal).** The auto-derivation map `AUTO_SOURCE_LABEL_MAP` is many-to-one: `net_recv`, `ipc_recv` and `db_read` all become the label `untrusted_input`. That collapse is lossy in a way that matters on ORM-backed applications, where database-read-to-database-write flows dominate — all 140 flows on the validation cohort's largest violated claim are of that shape. `TaintSource` and `TaintFlowFinding` therefore carry `source_boundary` (the originating `io_boundary` category, empty for YAML-declared sources), every taint verdict carries a `flow_origins` count keyed by it, and every evidence row carries it for drill-down. **The taint label is deliberately unchanged**: giving database reads their own label would silently alter what every claim already written against `untrusted_input` means — measured, it would flip 3 of 16 violated cohort claims to `confirmed` with no disclosure — whereas reporting the split changes zero verdicts and zero evidence rows. `declared` is the bucket for sources with no io_primitives boundary.

**Verdict vocabulary (ADR-0017 §6).** `verify-claims` reports each taint-flow constraint with one of five verdicts:

| Verdict | Meaning |
|---------|---------|
| **Confirmed** | No taint-flow path exists from source to prohibited sink. |
| **Confirmed (sanitized)** | Paths exist, but every source→sink path passes through an allowed sanitizer. |
| **Violated** | Taint flows from source to sink without sanitization. The verdict carries per-flow drill-down evidence (WI-kikis): `details` renders up to five *distinct* flows with their source/sink symbol IDs and a `via N hop(s)` path indicator (deduplicated on the full flow identity, so identical-looking rows collapse and the honest total-vs-distinct count is shown), and the `--format json` envelope adds a bounded structured `evidence` array (`source_symbol` / `sink_symbol` / primitives / `path`, ≤100 distinct flows) for programmatic triage even at high evidence counts. |
| **Inconclusive (no DDG)** | *Specified, never emitted.* Intended for a critical path segment (source, sink, or sanitizer function — see ADR-0017 §3c) lacking DDG coverage. `verify_taint_claim` does not produce this verdict; the confidence a finding carries reaches no consumer. |
| **Inconclusive (opaque)** | Path crosses an opaque boundary (native code without source). |

Pass-through functions without DDG coverage do **not** force "Inconclusive" — their function summaries (above) carry argument-to-return flow regardless of intraprocedural coverage. "Inconclusive (no DDG)" is reserved for gaps on *critical* path segments. CLI exit code is 1 on any `Violated` verdict; `Inconclusive` verdicts exit 0 but are surfaced in the report.

**IO-boundary reporting (ADR-0016).** `hypergumbo io-boundaries --boundary <category>` reports call edges that reach the named IO boundary (`fs_read`, `net_send`, …), with `--by-file` and `--primitive` filters — the canonical, whole-graph view. Boundary categories match the `meta["io_boundary"]` vocabulary documented in [§9](#9-behavior-map-json). `hypergumbo slice --entry <X> --io-boundary <category>` is the slice-scoped twin: it filters an entry's slice to the edges that reach the named boundary, classifying them ephemerally at slice time (the same consumer-time derivation — no persisted field). An unknown category exits 2 with the valid list. **Headline count (WI-huhit/WI-foduh).** The `--json` envelope's `total_io_edges` is the **real/verified I/O surface** — the chain count across confirmed boundary categories, **excluding** the disclosed-only buckets `external_potential` (receiver-unresolved calls — on self-analysis ~96% builtin method noise like `append`/`get`/`split`, not real I/O) and `command_launch` (WI-javoh: bash program launches — a real subprocess crossing, but an invocation-count multiplicity that would swamp the curated stdlib subprocess entries if summed into the headline). Each disclosed-only bucket is surfaced separately: `external_potential_edges` and `command_launch_edges`. Text and JSON report the **same canonical count**, and text discloses the suppressed counts. The envelope is pinned by `IO_BOUNDARIES_SCHEMA_VERSION` (`2.1` since the `command_launch` cohort was added; `2.0` redefined the headline to exclude `external_potential`; the `1.0` headline included external_potential and over-counted the surface ~28×). INV-pubom (amended 2026-06-30): the unfiltered, filtered, and text paths all agree on this definition. The `_DISCLOSED_ONLY_BOUNDARIES` frozenset in `io_boundary.py` is the single source of truth for which buckets are held out of the headline. **High-risk marker.** Each boundary/primitive additionally carries a display-only flag — `high_risk` per chain and `has_high_risk` per boundary in the `--json` envelope, ` *** HIGH RISK ***` / ` [HIGH RISK]` in text. It is a narrow triage cue scoped to `subprocess` (launching an external program is arbitrary code execution — the one boundary with a clean "always risky" invariant, completeness-ratcheted per language), **not** a net/fs risk taxonomy: destructive-filesystem and network-egress risk are carried by the taint source/sink model (auto-derived from the `io_primitives` write-side categories; [ADR-0017](adr/0017-taint-zone-dataflow.md) §2b) and, for network, by supply-chain `dst_tier`. `io_boundary.HIGH_RISK_PRIMITIVES` is subprocess-only, and `verify-claims` does not consume `high_risk`. **Catalog completeness disclosure (WI-najil).** Each `io_primitives/<language>.yaml` carries a `status` field — `complete` (the stdlib I/O surface is enumerated, with `stdlib_provenance.source_url` pointing at the official docs, validated at load time) or `in_progress` (partial, no provenance required). Because an `in_progress` catalog's zero-match outcome is otherwise indistinguishable from a genuine "no I/O in this code", every consumer that queries the catalog — `hypergumbo io-boundaries`, `verify-claims`, and `slice --io-boundary` — emits a one-line stderr warning per queried `in_progress` language (`io_primitives catalog for '<lang>' is in_progress — io-boundary results for '<lang>' may be incomplete`); the shared helper is `io_boundary.in_progress_languages`. Unsupported languages (no catalog file) carry the separate `is_supported=False` signal (INV-javam), not this warning. Catalogs currently `complete`: python, rust, erlang; the remaining eleven (c, cpp, elixir, go, haskell, java, javascript, kotlin, objc, scala, swift) are `in_progress`.

**Project-local catalog extension (WI-votan).** `verify-claims` accepts three repeatable flags — `--taint-sources PATH`, `--taint-sinks PATH`, `--taint-sanitizers PATH` — where each PATH is either a single YAML file or a directory of YAMLs (globbed as `*.yaml` in sorted order). The claims YAML may carry the same paths under a top-level `extra_catalogs:` key with `sources:` / `sinks:` / `sanitizers:` sub-lists; entries given relative paths resolve against the claims-file directory so a repo can keep its extra catalogs beside the claims document. The public helper `load_full_taint_catalog(extra_source_paths, extra_sink_paths, extra_sanitizer_paths, *, cli_source_paths, cli_sink_paths, cli_sanitizer_paths)` stacks four layers: (1) auto-derived from `io_primitives/*.yaml`, (2) built-in YAML shipped in `taint_sources/` / `taint_sanitizers/`, (3) claims-file extras, (4) CLI extras. Per INV-hukug, layers 3 and 4 are distinct, so a CLI flag replaces a claims-file entry on a matching `(module, name, kind)`. User source and sink entries whose `(module, name, kind)` triple matches a lower-layer entry replace it; user sanitizer entries concatenate. `verify-claims` prints a one-line summary to stderr when any extra catalog paths were loaded so users know their override took effect. This is the supported way for a repo to raise `trust_level` on a sink that is safe in context, declare a sanitizer so tainted data can legitimately reach a sink, add a domain-specific taint source label, or introduce a new trust zone beyond the built-in `host_fs`, `subprocess`, `network`, `host_env`, `ipc`, `browser_storage`, `database`, `logging`.

**Def/use extractors.** Intraprocedural dataflow precision requires a per-language def/use extractor — a pluggable Python module that identifies which variables each AST node defines and uses. Extractors are registered via `@register_def_use_extractor` and feed the language-parameterized CFG builder and reaching-def solver (ADR-0017 §1a–1c). Languages with extractors get variable-level (`precise`) taint tracking; languages without fall back to call-graph BFS (`approximate`). Current extractors: Python, Rust (including borrow alias tracking and `ref`/`ref mut` patterns), TypeScript, Go, and **JavaScript** — all registered and force-imported on the `verify-claims` path. JavaScript is served by the TypeScript extractor registered a second time under its own key, with `cfg_nodes/typescript.yaml` reached through `cfg._CFG_MAPPING_ALIASES` rather than copied to a `javascript.yaml`, because that file's own header already asserts the two share tree-sitter node types (WI-nonad). It matters more than the TypeScript registration it rides on: TypeScript carries 6 catalogued sources and **zero** sinks, JavaScript carries 50 and **83**. CFG node mappings (YAML) exist for those languages **and Java**, but `cfg_nodes/java.yaml` declares no `atomic_statement` and no Java extractor is registered, so Java's mapping alone buys nothing: its 69 catalogued sinks are adjudicated by call reachability like any uncovered language.

🟩 **The coverage scope is published as data, not prose (INV-karud clause a3).** Which languages can be data-flow adjudicated is a fact that decays — this paragraph has been wrong twice — so `verify-claims` computes it at runtime and emits it. Every run carries a `dataflow_coverage` block (envelope `1.7`; rendered on the text surface too) with one row per analyzed language carrying its catalog counts and four independent capability bits — `cfg_mapping`, `atomic_statement`, `def_use_extractor`, `ddg_spec` — plus `dataflow_capable` (their conjunction) and `blockers` (the missing ones, so the row says what to fix). Four bits rather than one flag because each is individually sufficient to keep the machinery silently inert. The block also states two things no per-run measurement can: `inclusion_decided_by` (`call_graph_reachability`, because §3a is confirm-only) and `coverage_granularity` (`language` — capability is **not** a per-function completeness claim; `cfg_nodes/go.yaml` self-documents that `if err := ...` initializers are invisible to def/use, so a capable language still holds functions the analysis cannot see into). Alongside it, `findings_by_analysis_method` counts every finding the propagators produced, which is a deliberately different denominator from a verdict's own `analysis_methods` — one describes the analysis, the other describes a claim.

**Function summaries.** *Implemented, no production consumer* (measured 2026-08-02). The intent is that when taint crosses a function call boundary, the solver consults a function summary to determine how arguments map to return values; no taint code path calls `infer_summary` or `load_function_summaries` today. Summaries are either inferred automatically from DDG analysis or declared in YAML for stdlib/framework functions whose source is not analyzed. Declared summaries live in `function_summaries/` (currently `rust_stdlib.yaml` and `typescript_stdlib.yaml`) and support `param_to_return`, `param_to_self`, `mutates_self`, `side_effect`, `sanitizes`, and structured `callback` flow for higher-order functions. Functions without an explicit summary receive the conservative default: all parameters flow to the return value.

**Cross-language propagation.** Taint propagates through call-shaped edge types including direct calls and cross-language bridges. Post-Phase-3 (WI-mifor-vabul / WI-hahap-farid / WI-vumum-juvil), bridge / IPC / protocol-call edges all emit as canonical `calls` with the mechanism in `meta` (`bridge_kind` for FFI bridges, `protocol` for IPC/HTTP/gRPC/GraphQL); the taint configuration (`TAINT_CALL_EDGE_TYPES`) keeps `calls` plus `module_attr_ref` and the pending-classification `implements_rpc`. IPC boundaries are taint-transparent by default — serialization does not sanitize.

**Field-sensitivity lite.** *Implemented, no production consumer* (`is_field_tainted`, measured 2026-08-02). The intent: if `x` is tainted, `x.field` and `x.method()` inherit the taint. If `obj.field = tainted_value`, then `obj.field` is tainted but `obj.other_field` is not. Indirect aliasing (same object via different references) is not tracked.

## 12) Confidence scoring

Hypergumbo assigns confidence scores (0.0–1.0) in three independent categories. The scores quantify detection reliability — how certain hypergumbo is that a detected relationship or entrypoint is real, not a false positive. The three categories are independent: an edge originating from a high-confidence entrypoint does not inherit that entrypoint's confidence score, and analyzer-produced edges and linker-produced edges use different scoring logic.

### Three confidence categories

| Category | What it scores | Scoring basis | Score range | Defined in |
|----------|---------------|---------------|-------------|------------|
| **Analyzer edge confidence** | Intra-language relationships (calls, imports) | 🟩 Derived from the inference pathway (`evidence_type`) via the registry `base_confidence` table, `is_resolved`-conditioned for call types ([ADR-0039](adr/0039-confidence-separation.md) / WI-nurun) | 0.30–0.95 | [Below](#edge-confidence-analyzer-evidence) |
| **Linker edge confidence** | Linker-recovered relationships across all four subcategories — Bridge/Protocol examples include JNI, IPC, HTTP | Match quality (literal vs. dynamic, naming convention vs. annotation) | 0.40–0.95 | [Below](#edge-confidence-linker-edges), details in [§7](#7-linkers) |
| **Entrypoint confidence** | Whether a symbol is an entry point | Detection method (manifest, decorator, convention, naming) | 0.70–0.99 | [Below](#entrypoint-confidence-tiers), details in [§8](#8-entrypoint-detection) |

All scores use the same 0.0–1.0 scale and the same semantic contract: higher means more certain. The `confidence_model` field in output (`hypergumbo-evidence-v2.0`) identifies the scoring algorithm version — `v2` marks the deterministic evidence→confidence derivation now in effect for analyzer edges ([ADR-0039](adr/0039-confidence-separation.md) / WI-nurun): the base score is a registry lookup keyed on `evidence_type`, `is_resolved`-conditioned for call types. Unregistered/unseeded pathways and dynamically-computed sites fall back to the caller's value; consumers should read `confidence` purely as detection reliability on the 0.0–1.0 scale.

### Edge confidence: analyzer evidence

🟩 **Implemented (ADR-0039 / WI-nurun).** `Edge.confidence` for
analyzer-produced edges is *derived* from the inference pathway, not hardcoded
per emitter: `Edge.create` looks up the edge's `evidence_type` in the registry
`base_confidence` table (`evidence_types.py`) via
`derive_confidence(evidence_type, is_resolved=…)`. The two multimodal call
types (`ast_call` / `ast_call_direct`) are `is_resolved`-conditioned (a
name-resolved call scores higher than an unresolved one); all other pathways
are single-valued. Each base is the **edge-weighted modal** confidence the
pathway's producers historically hardcoded, so the migration that dropped the
~259 literal `confidence=` sites preserved the dominant cohort and collapsed
per-emitter outliers to the single canonical value (the INV-suvil fix).

**Bands.** Base confidences live in the analyzer/linker band **0.30–0.95**;
1.0 is a reserved ceiling (a detection method is never *certain*). The
ceiling-breach cohort is now **closed**: `naming_convention` was seeded at 0.85
by ADR-0039 ruling 1, and WI-radim seeded the two meson pathways
(`build_dependency`, `subdir_include`) at 0.95 with their literals dropped, so
all three derive. The fourth site, a jsonnet import edge, carried the generic
mechanism-type `evidence_type="tree_sitter"` and was relabelled to the
semantically-correct (already-seeded) `import`. One residual reserved-1.0 emit
remains — the jsonnet *calls* edge, whose confidence arrives through a computed
variable — tracked as WI-rafut.

**The per-pathway values are generated, not transcribed here.** The full
`base_confidence` projection — every seeded pathway with its derived value, the
two `is_resolved`-conditioned call pathways, and the explicit unseeded list —
lives in [`docs/concept-axes.md`](concept-axes.md#derived-confidence--base_confidence-projection-adr-0039),
emitted directly from `evidence_types.EVIDENCE_TYPES` and kept honest by the
`generate-concept-axes --check` freshness gate (WI-limom). This section owns the
*model*; that table owns the *values*, so the two cannot drift.

**Not yet derived (retain explicit `confidence=`):** sites that *compute*
confidence dynamically (linker match-strength scores), and the pathways
registered without a seeded `base_confidence` — `derive_confidence` returns
`None` for those, so the producer keeps its literal and `confidence_source`
stays `emitter_constant`. Only literal hardcoded constants on seeded inference
pathways were migrated. The `type_hierarchy` linker no longer retains an
explicit dynamic confidence: ADR-0039 ruling 3 relocated its `1/√N` fan-out
dampener + test penalty to `rank_score`, so its detection confidence now derives
the flat in-band 0.85 `type_hierarchy` base.

**Confidence provenance & ranking separation (ADR-0039 rulings 2 & 3, implemented).**
Every `Edge` carries a `confidence_source` discriminator — `evidence_derived` (the
value came through `derive_confidence`), `emitter_constant` (a declared hardcoded
producer value, incl. the unseeded 0.85 fallback), or `composite` (still fuses a
ranking adjustment) — so the migration off per-emitter constants is machine-readable.
Post-detection **ranking** adjustments (the type-hierarchy fan-out dampener, the
entrypoint penalties/demotions/degree-boosts) no longer contaminate `confidence`;
they accumulate on a sibling `Edge.rank_score` / `Entrypoint.rank_score` field
(ranking prominence, 0.0–1.0, initialized from `confidence`). Ranking consumers
(centrality edge-filter `filter_edges_for_ranking`, entrypoint sort +
`MIN_ENTRYPOINT_CONFIDENCE` filter, sketch entrypoint ordering, slice auto-entry)
key on `rank_score`; `confidence` is now purely detection reliability.

### Edge confidence: linker edges

Linkers (Tier 2 passes — all four subcategories) produce their own confidence scores based on match quality. These scores are independent of the analyzer-evidence scoring above. See [§7 Linkers](#7-linkers) for detection rules and limitations.

| Linker | Match type | Confidence |
|--------|-----------|------------|
| **JNI** | Naming convention (`Java_{Class}_{method}`) | 0.95 |
| **JNI** | 🟪 Annotation-confirmed | 0.98 |
| **IPC** | String literal channel name match | 0.85 |
| **IPC** | 🟪 Variable/computed channel name | 0.50 (code uses flat 0.65) |
| **IPC** | 🟪 Template literal with interpolation | 0.40 (no variable/template distinction in code) |
| **IPC** | 🟪 Annotation-provided | 0.95 |
| **HTTP** | Literal URL match | 0.90 |
| **HTTP** | Variable/computed URL | 0.65 |

**Pattern:** Across all linkers, literal/static matches score higher than dynamic/computed ones, and annotation-confirmed matches approach 0.95. This reflects the inherent uncertainty of heuristic string matching.

### Entrypoint confidence tiers

Entrypoint confidence scores how reliably a symbol was identified as an entry point (route, CLI main, task handler, etc.). This is independent of edge confidence — a function detected as a route at 0.95 confidence may have call edges at any confidence level. Per ADR-0039 ruling 3, entrypoint `confidence` is now **pure detection reliability** (the construction-time tier); the ranking penalties, library-export demotion, and connectivity boosts that used to move it off-tier live on `Entrypoint.rank_score`, which the entrypoint ordering and the `MIN_ENTRYPOINT_CONFIDENCE` filter key on.

See [§8 Entrypoint detection](#8-entrypoint-detection) for the detection architecture and the full tier table. The four standard tiers span [0.70, 0.99]: Declared (0.99), Decorator/Annotation (0.95), Structural (0.85), Naming (0.70). Two deliberate low-confidence kinds sit **below** that band by design and are not among the four tiers: `connectivity_based` (0.50 — a last-resort fallback used only when no concept-based entrypoint is found; §8 lists it as the `connectivity_heuristic` tier) and frontend-route suppression (0.05, filtered out by `MIN_ENTRYPOINT_CONFIDENCE`).

## 13) Output reproducibility

Reproducibility has two dimensions: **caching** ensures that re-running analysis on unchanged code returns the same result quickly, and **deterministic ordering** ensures that output is diffable across runs.

### Caching
* Location: `~/.cache/hypergumbo/` (XDG-compliant; respects `XDG_CACHE_HOME` if set)
* Cache structure:
  ```
  ~/.cache/hypergumbo/
  └── <repo-fingerprint>/
      ├── embeddings/
      │   └── embed_<file-content-hash>.npy
      └── results/
          └── <state-hash>/
              └── <analyzer-identity>/
                  └── survey.json
  ```
* Keying strategy:
  * **Repo fingerprint** (stable identity): hash of remote origin URL + first commit SHA (git repos) or absolute path (non-git). Shared across checkouts of the same repo.
  * **State hash** (point-in-time snapshot): hash of HEAD SHA + diff of tracked changes + untracked source file metadata. Changes when any file is modified.
  * **Analyzer identity** (WI-panih): keys on `__version__` + a content hash of the installed `hypergumbo_*` packages, so stable and dev-editable installs don't poison each other's results.
  * **Embedding cache**: keyed by individual file content hash; shared across all repo states since embeddings depend only on file content.
* Cache invalidation: state hash changes when any analyzed file content changes (including dirty git files)
* Cache format: JSON for analysis results, NumPy `.npy` for embeddings
* Management:
  * `hypergumbo cache-status [--per-repo]` — total size, age range, and (with `--per-repo`) per-repo size / entry-count / last-used breakdown sorted by size desc.
  * `hypergumbo cache-clear [--older-than N] [--dry-run] [--repo FINGERPRINT [--keep-latest N]]` — `--repo` restricts deletion to one repo's subtree; `--repo` + `--keep-latest N` keeps the N newest state-hash entries under `<repo>/results/` and prunes the rest.
* Lifecycle policy (INV-padum): **honk-threshold-with-retention.** No automatic eviction at any size. When total cache size exceeds the configured threshold (default 1.0 GiB), `cache-status` and `hypergumbo survey` emit a loud stderr warning naming the top consumer and the prune commands. Configure or silence via the `HYPERGUMBO_CACHE_HONK_GB` environment variable (set to `0` / `off` / `none` / `false` to silence). The user owns the prune decision; hypergumbo never throws away cache entries unprompted.

### Deterministic ordering

Output ordering is deterministic (same input → same output) and optimized for consumption priority.

* 🟩 **Default: centrality-ranked** — Nodes sorted by centrality score (most important first). Edges sorted by source node centrality. This ordering is deterministic given the same input graph and optimizes for LLM context windows and human scanning. Used in JSON output and sketch output.
* 🟩 **`compact` default: connected core (connectivity-aware)** — `compact` is the exception, and deliberately so. Its output is consumed as a **graph** (induced subgraph edges are emitted alongside the nodes), and a centrality-ranked prefix of a graph is frequently not connected: measured across eight real behavior maps, centrality-ranked crops carried **42% isolated nodes at a 25-symbol budget** and grew *more* fragmented as the budget rose (12.6 → 42.1 components from K=25 to K=400), where the connected core stays flat at 3–4 components and ≤8.5% isolated. `sketch` remains centrality-ranked because it is a **reading** surface, not a graph — so the two surfaces disagree on their top symbols **by design**, and that disagreement is the correct behavior rather than a defect. Pass `--no-connectivity` for a centrality-ranked `compact` selection matching the sketch.
  * **Emitted order under the connected core** is the selection stream, not a centrality sort: a fixed budget-independent seed order (entrypoints by confidence, then cross-cutting endpoints by edge count) interleaved 1:2 with greedy bridge picks. This is what makes `--max-symbols` **containment-monotone** — the node list at a smaller budget is an ordered prefix of the list at a larger one, so growing the budget never drops a symbol it previously showed you.
* 🟩 **JSON key-level reproducibility** — all `json.dump`/`json.dumps` output calls use `sort_keys=True` for reproducible diffs at the key level.
* 🟪 **`--sort-order` option** — Future flag to allow alternative orderings (e.g., `--sort-order alphabetical` with sort keys: nodes by `(language, path, start_line, name)`, edges by `(src, dst, type)`) for users who prefer diffability over importance ranking.

## 14) Supply chain classification

Hypergumbo classifies files by their position in the project's dependency graph. This enables focused analysis (first-party code prioritized in results) and noise reduction (derived artifacts excluded from analysis entirely).

### Motivation

Static analysis of modern codebases faces a fundamental signal-to-noise problem: the code a developer wrote is often mixed with code they imported, bundled, or generated. A webpack bundle contains both application logic and lodash internals. A monorepo contains both project packages and vendored forks.

Without supply chain awareness, analysis results are polluted:
- Key Symbols rankings dominated by utility functions from bundled dependencies
- Edge counts inflated by calls within third-party libraries
- Sketch output filled with framework internals rather than application logic

The solution is not to exclude dependencies entirely—sometimes tracing into them is valuable—but to **classify** code by its position in the supply chain and let users control their viewport.

### Tiers

Code is classified into four tiers based on its relationship to the project:

| Tier | Name | Description | Examples | Status |
|------|------|-------------|----------|--------|
| 1 | `first_party` | Project's own source code (including its tests) | `src/`, `lib/`, `app/` | 🟩 |
| 2 | `internal_dep` | Org-internal *dependency* packages: config-declared `internal_package_roots`, **and** a workspace-sibling *dependency declaration* (a monorepo package that another workspace member lists as a dependency). Workspace member *source files* stay tier 1. | Local forks; `hypergumbo-core` listed in a sibling package's `pyproject.toml` | 🟩 |
| 3 | `external_dep` | **All** third-party code — direct, transitive, undeclared, and stdlib alike | `node_modules/lodash/`, `vendor/`, declared PyPI deps, `os`/`json` | 🟩 |
| 4 | `derived` | Build artifacts, transpiled/bundled output | `dist/`, `*.min.js`, source-mapped files | 🟩 |

**Tier names supply-chain distance and nothing else (ADR-0041 §1).** A *declared*
(direct) third-party package is **not** promoted to tier 2 — distance, not
declaration status, is what `tier` records, and a declared PyPI package is no
closer to the project than an undeclared one. The direct/transitive/undeclared
*declaration relationship* lives on the registered `directness` meta key
(`direct` / `transitive` / `undeclared`); the stdlib-vs-third_party *provenance*
distinction lives on the registered `ecosystem` axis (ADR-0041 §3). Both are
stamped once, at classification time, on boundary/dependency nodes.

(History: ADR-0041 §1 retired the *manifest* direct-dependency → tier-2
mapping. File classification independently still routed some in-repo non-source
*role* files — examples, documentation bundles, notebooks, fuzz/bench harnesses
— to tier 2 (INV-naduh); these are the project's own code (distance 0) and now
classify as **tier 1** with their role on the `is_example` / `is_test` / reason
axis, not by tier. Tier 2 has **two** producers: config-declared
`internal_package_roots`, and a workspace-sibling *dependency declaration* — a
`kind=dependency` symbol naming an in-repo package that another workspace member
lists as a dependency (INV-nuzas / ADR-0041 §1 tier table + D8a; this is the
"populate tier 2 with the workspace packages" end-state ADR-0041 §1 anticipated
for the workspace-resolution fix, not a re-admission of the retired *third-party*
direct-dependency → tier-2 mapping). Workspace member *source* stays tier 1, and
in-repo generated *routes* promote from tier 4 to tier 1, not tier 2.)

**Default behavior:**
- Tiers 1-3: Analyzed, with tier used for ranking/filtering
- Tier 4: Excluded from analysis entirely (pure noise)

**Design principle:** Analyze the canonical source, skip derived artifacts. If both `src/app.ts` and `dist/app.js` exist, analyze the TypeScript (tier 1), skip the transpiled JavaScript (tier 4).

### Classification Algorithm

🟩 Classification happens at discovery time, before analysis. Signals are checked in order; first match wins.

#### 1. Derived artifact detection (tier 4)

Checked first because derived files should never be analyzed.

**Path patterns:**
```
dist/, build/, out/, target/
.next/, .nuxt/, .output/, .svelte-kit/
*.min.js, *.min.css, *.bundle.js, *.compiled.js
*.pyc, *.pyo, __pycache__/
```

**Content heuristics** (checked if path inconclusive):
```python
def is_likely_derived(path: Path) -> bool:
    content = path.read_text()
    lines = content.splitlines()

    # Heuristic 1: Average line length > 150 chars (minified)
    if len(content) / max(len(lines), 1) > 150:
        return True

    # Heuristic 2: Source map reference in last 3 lines
    tail = '\n'.join(lines[-3:])
    if re.search(r'//[#@]\s*sourceMappingURL=', tail):
        return True

    # Heuristic 3: Generator header in first 5 lines
    head = '\n'.join(lines[:5])
    if re.search(r'(Generated by|@generated|DO NOT EDIT)', head, re.I):
        return True

    return False
```

**Rationale for thresholds:**
- 150 chars/line: GitHub Linguist uses 110; hypergumbo uses 150 to reduce false positives on legitimately long lines (e.g., data URIs, long strings). Minified code typically has 1000+ chars/line.
- Source map check: Presence of `sourceMappingURL` is a strong signal that this file was generated from another source.
- Generator header: Many tools (protoc, swagger-codegen, etc.) add header comments.

#### 2. External dependency detection (tier 3)

**Path patterns:**
```
node_modules/
vendor/              # PHP (Composer), Go (historical)
third_party/
Pods/, Carthage/     # iOS
.yarn/cache/
_vendor/             # Hugo
```

**Package name extraction:**
For `node_modules/`, the package name is extracted for metadata:
```python
def extract_package_name(rel_path: str) -> str | None:
    if 'node_modules/' not in rel_path:
        return None
    parts = rel_path.split('node_modules/')[-1].split('/')
    if parts[0].startswith('@'):
        return '/'.join(parts[:2])  # @scope/package
    return parts[0]
```

#### 3. Internal dependency detection (tier 2)

Tier 2 (`internal_dep`) has **two** producers, both representing an org-internal
*dependency* (never the project's own source, which is tier 1):

1. **Config-declared `internal_package_roots`** — org-internal dependency
   packages (local forks, vendored-but-internal libs). No *path* heuristic
   produces tier 2 for source files: workspace member source is tier 1, and
   in-repo role files carry their role on a flag/reason (ADR-0041 §1 / INV-naduh).
2. **Workspace-sibling dependency declarations** — a `kind=dependency` symbol
   whose PEP 503-normalized name matches an in-repo package's distribution name
   (`collect_workspace_package_names` reads every `pyproject.toml`'s
   `[project].name` / `[tool.poetry].name`). A monorepo sibling that another
   workspace package lists as a dependency is a workspace-**internal** dependency
   (tier 2), not a third-party external (tier 3) — INV-nuzas / ADR-0041 §1 (tier
   table + the anticipated "populate tier 2 with the workspace packages"
   end-state) / D8a. This is orthogonal to the retired *third-party*
   direct-dependency → tier-2 mapping (§1): a genuine third-party declaration
   stays tier 3. Python `pyproject.toml`-scoped; Cargo/npm sibling analogues are
   a documented follow-up (still tier 3).

**Config-declared internal package roots:**
```yaml
# capsule plan supply_chain config
internal_package_roots: ["custom_packages/shared", "libs/common"]
```
Files under a declared root are tier 2 (`internal_dep`).

**Workspace / monorepo detection → tier 1.** Workspace configuration files
(npm/yarn/pnpm `workspaces`, Cargo `[workspace] members`, Maven `<modules>`,
Python monorepo path-deps) are read to detect package roots, but their member
files classify as **tier 1** (the workspace IS the project), with `is_test=True`
on co-located tests (INV-tisid):
```json
// package.json — members of packages/* and apps/* are tier 1
{ "workspaces": ["packages/*", "apps/*"] }
```

**Role files → tier 1, role on a flag (not tier).** Example/demo, documentation,
notebook, and fuzz/bench paths classify as **tier 1** (the project's own code),
with the role carried by `is_example_file` / `is_test_file` / the reason string —
independent of `supply_chain_tier` (INV-naduh / INV-tisid / ADR-0041 §1):
```
examples/, demos/, samples/, tutorials/     # → tier 1, is_example_file=True
tests/, test/, __tests__/, spec/            # → tier 1, is_test_file=True
_test.go, .test.js, .spec.ts, _spec.rb     # → tier 1, is_test_file=True
fuzz/, fuzzing/, fuzz_targets/              # → tier 1 (fuzz/bench role in reason)
benches/, benchmarks/, benchmark/           # → tier 1 (fuzz/bench role in reason)
*.ipynb                                     # → tier 1 (notebook role in reason)
Documentation.docc/ bundles                 # → tier 1 (documentation role in reason)
```

#### 4. First-party detection (tier 1)

**Explicit first-party patterns:**
```
src/, lib/, app/, pkg/
cmd/, internal/          # Go conventions
crates/*/src/            # Rust workspace source
packages/*/src/          # JS monorepo source dirs
```

**Default rule:** If no other tier matches, classify as tier 1 (first-party). This ensures unknown directories are analyzed rather than skipped.

### Impact on Analysis

#### Sketch Key Symbols ranking

Without supply chain awareness, centrality-based ranking can be dominated by utility functions from bundled dependencies.

**Tier-weighted ranking:**
```python
TIER_WEIGHTS = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.0}

def weighted_score(symbol: Symbol, centrality: float) -> float:
    tier = symbol.supply_chain.tier
    weight = TIER_WEIGHTS.get(tier, 1.0)
    return centrality * weight
```

This ensures first-party symbols appear first even when third-party utilities have higher raw centrality.

### Limitations

**What supply chain classification does NOT do:**

1. **Resolve transitive dependencies**: Classification is based on file location, not the full dependency graph. A file in `node_modules/a/` that imports from `node_modules/b/` doesn't affect tier assignment. 🟩 **Boundary-node directness:** Boundary nodes (unresolved external references) are **all** tier 3 (external) — declaration status no longer influences tier (ADR-0041 §1). When dependency-manifest data is available, the declaration relationship is recorded on the `directness` meta key instead: `direct` (declared in a project manifest), `transitive` (in the manifest but not declared direct), or `undeclared` (imported but declared nowhere — also where stdlib lands). Supported manifests: Go (`go.mod` — direct vs indirect), Java/Kotlin (`build.gradle`, `build.gradle.kts`, `pom.xml` — groupId-based prefix matching), Python (`pyproject.toml`). The language-agnostic `DependencyManifest` infrastructure supports future extension to npm and Cargo manifests. 🟩 **Boundary-node ecosystem (ADR-0041 §3):** orthogonally, each tier-3 boundary node is also stamped with an `ecosystem` meta key (`stdlib` vs `third_party`) when the language has an enumerated stdlib catalog — sourced from the single-source `io_boundary` stdlib catalog (the same one the io-boundary closed-world gates use), so a vuln in a `third_party` dependency (pin/update the package) is distinguishable from a `stdlib` one (upgrade the runtime).

2. **Detect vendored copies**: If you copy `lodash.js` into `src/utils/lodash.js`, it's classified as tier 1 (first-party).

3. **Understand build pipelines**: Classification doesn't know that `dist/app.js` was built from `src/app.ts`. It relies on path conventions and content heuristics.

4. **Handle unconventional structures**: Projects with unusual layouts (e.g., source in root, deps in `lib/`) may be misclassified.

**Logged in limits:**
🟩 `classification_failures` is populated in production by `Limits.add_classification_failure` from the classify-symbols pass on the "outside repo" fallback (INV-virik).
🟪 `ambiguous_paths`: `add_ambiguous_path` exists but has no production caller, so that array is always empty in output.
```json
{
  "limits": {
    "supply_chain": {
      "classification_failures": [],
      "ambiguous_paths": [
        {"path": "lib/vendor/custom.py", "assigned": 1, "note": "could be tier 2 or 3"}
      ]
    }
  }
}
```

## 15) File role classification

Supply chain **tiers** answer "where does this file come from?" (provenance). A complementary dimension, **file roles**, answers "what is this file for?" (purpose). See [ADR-0004](adr/0004-file-taxonomy.md) for the design rationale.

| Role | Description | Examples |
|------|-------------|----------|
| `ANALYZABLE` | Has symbols to extract | Python, JavaScript, Go |
| `CONFIG` | Parameterizes behavior | package.json, pyproject.toml, YAML configs |
| `DOCUMENTATION` | Human-readable instructions | Markdown, RST |
| `DATA` | Raw information, not instructions | JSON datasets, CSV fixtures |

**What counts as "code" for LOC purposes:**

```
CODE = ANALYZABLE + CONFIG + DOCUMENTATION
```

This treats documentation as code (the instructions happen to be in natural language) while excluding pure data files. A 34K-line JSON pricing dataset is not "code" even though it's text.

**Disambiguation for ambiguous extensions:**

JSON files require filename-level classification:
- `package.json`, `tsconfig.json` → CONFIG
- `**/fixtures/*.json`, `*_data.json` → DATA
- Large files (>100KB) → likely DATA
- Default → CONFIG (conservative)

**Composed decisions:**

Tier and Role compose for analysis decisions:

| Decision | Tier constraint | Role constraint |
|----------|----------------|-----------------|
| Count in LOC | Tiers 1-2 | CODE roles |
| Extract symbols | Tiers 1-2 | ANALYZABLE only |
| Additional Files | Tiers 1-2 | CONFIG + DOCUMENTATION |

**Status:** 🟩 Implemented (ADR-0004). The `taxonomy.py` module provides the unified file classification system with `FileRole` enum and `LanguageSpec` dataclass for 86 languages.

## 16) Testing & quality bar

**Related ADRs.** [ADR-0002](adr/0002-test-dependency-handling.md) defines the test-dependency policy: tests assume their dependencies work and may not use `pytest.mark.skipif` or module-level skip as escape hatches; missing dependencies are a build failure, not a test skip. [ADR-0011](adr/0011-scoped-coverage-and-green-baseline.md) defines the 100%-coverage rule with the green-baseline `last-green-sha.txt` marker so coverage is enforced *scoped to changed source files* against the last passing build. [ADR-0009](adr/0009-feature-focused-bakeoff.md) defines the BROAD / DEEP bakeoff regime that evaluates hypergumbo against real-world repos beyond fixture-based unit tests.

### Test fixtures

* 🟩 Small controlled fixtures in `tests/fixtures/*` for property testing
* 🟩 Synthetic code samples with known structure (e.g., "3 functions" → expect 3 function nodes)

### Property-based testing (current approach)

**Rationale:** Golden file testing assumes we know the "correct" output a priori. For complex real-world repos, this is infeasible—we can't manually verify every node and edge. Instead, we verify *invariants* that must hold regardless of the specific output.

**Invariants tested:**
- 🟩 Every edge's source/target references a valid node ID
- 🟩 Confidence scores are in range [0.0, 1.0]
- 🟩 Every symbol has a non-empty name
- 🟩 Output matches the JSON schema
- 🟩 Analysis completes without errors
- 🟩 Determinism: same input → same **semantic** output (nodes/edges/`stable_id`s/`run_signature` reproduce at L2; per-run `execution_id`/timestamps and `analysis_runs[]` order are not byte-identical)

**Benefits:**
- No need to know "correct" answer upfront
- Tests remain valid as analysis improves
- Catches structural bugs (dangling references, invalid values)

### Unit tests
* 🟩 Parsing to nodes/edges (per language)
* 🟩 Stability of IDs across runs (same code → same IDs)
* 🟩 **ID stability across body edits**: `stable_id` is STABLE across body edits, comment/docstring changes, and line drift, but CHANGES on rename, signature change, or file move
* 🟩 **Rename/move detection**: verified via `fingerprint`/`shape_id` continuity (same `fingerprint` + new name across versions = detectable rename)
* 🟩 ID collision handling (multiple definitions at same location)
* 🟩 Fingerprint changes when code changes
* 🟩 Slicing correctness (known entry → expected subgraph)
* 🟩 Exclude behavior (respects patterns)
* 🟩 Confidence calculation determinism
* 🟩 Provenance tracking (correct origin fields, execution_id/run_signature hashing)
* 🟩 IR → view compilation correctness
* 🟩 **Catalog loading**: passes discovered correctly, schema validation

### Schema validation tests
* 🟩 Output validates against published JSON Schema
* 🟩 Forward compatibility: v0.1 output readable by v0.2+ (if backward compatible)
* 🟩 Required field presence (execution_id, run_signature, evidence_lang)
* 🟩 ID format conformance (both `id` and `stable_id` when present)
* 🟩 Evidence type presence in all edges
* 🟩 Toolchain capture in analysis_runs

### Smoke test
* 🟩 `hypergumbo survey` on a fixture repo yields valid JSON schema
* 🟩 All expected nodes/edges present
* 🟩 No crashes, warnings logged appropriately
* 🟩 `hypergumbo catalog` displays available passes

### Performance benchmarks
* Small repo (<100 files): <5 seconds end-to-end
* Medium repo (~500 files): <30 seconds
* Caching: second run on unchanged repo <2 seconds (see docs/CACHE.md)

## 17) Error handling

### Parse errors

* 🟩 **Behavior**: Log warning, skip file, continue analysis
* 🟩 **Output**: Add to `limits.failed_files[]`:
  ```json
  {
    "path": "malformed.py",
    "reason": "SyntaxError: invalid syntax (line 42)",
    "analyzer": "python"
  }
  ```

### Circular imports

* 🟩 **Behavior**: Detect cycle, log warning, break at arbitrary point
* 🟩 **Output**: Add to `warnings[]` in `analysis_runs[]`

### Missing dependencies

* 🟩 **Behavior**: If pass requires unavailable grammar (e.g., tree-sitter), skip pass
* 🟩 **Output**: Add to `limits.skipped_passes[]` (see [§9 limits — explicit gaps](#limits--explicit-gaps) for field format — pass-level skips live in top-level `limits`, not the per-run record)

### Analyzer crashes

* 🟩 **Behavior**: Catch the exception per-pass, record a `limits.skipped_passes[]` entry with reason `crashed: <ExcType>: <msg>`, set top-level `partial_results_reason`, and continue (fail-open, WI-madal L3). No stack-trace file is written.
* 🟩 **Output**: `limits.skipped_passes[]` entry + `partial_results_reason`; `analysis_incomplete` is not set and no entry is added to `warnings[]`.

### File size limits

* 🟩 **Behavior**: Skip files exceeding `--max-file-bytes`, continue analysis. Applied globally via `set_max_file_bytes()` so all analyzers using `find_files()` respect the limit.
* 🟩 **Output**: `limits.truncated_files[]` populated via global `on_file_skipped` callback. `run_all_analyzers()` sets a global callback in `discovery.py` that calls `limits.add_truncated_file()` for every file skipped due to size limits. No analyzer modifications required.

### Partial results guarantee

* 🟩 All output is valid JSON even if analysis is incomplete. See [`analysis_incomplete` in §9](#9-behavior-map-json) for field semantics.

## 18) Known limitations

This section documents known limitations and risks of the current analysis system. See `CHANGELOG.md` for per-language implementation status.

### Summary

| Limitation | Impact | Notes |
|------------|--------|-------|
| Best-effort analysis | Medium | AST-based analysis cannot resolve all calls (dynamic dispatch, reflection, eval). Confidence scores communicate uncertainty; machine-readable evidence types enable transparency. |
| Re-export resolution incomplete | Low | Imports through re-exporting modules may not fully resolve. See [details below](#re-export-resolution). |
| Import tracking partial | Low | ~30 analyzers now use import tracking for cross-file disambiguation (ADR-0007 Phase 1, 2, and 3A High+Medium complete); long-tail niche languages still pending. See [details below](#import-tracking-for-disambiguation). |
| ID collisions in edge cases | Low | Location-based IDs can collide for identically-named symbols at same line. Content hash appended if collision detected. |
| Confidence scores are heuristics | Medium | Scores are calibrated heuristics, not ground truth. Evidence types show reasoning; `--confidence-threshold` allows filtering. |
| Schema changes may break consumers | Medium | Semantic versioning from day 1; schema compatibility contract in [Appendix C](#appendix-c-schema-compatibility-contract); migration guides for breaking changes. |
| stable_id limited in untyped code | Low | Without type annotations, stable_id uses arity-based hashing which may change on signature changes. shape_id provides structural alternative. |

### Re-export Resolution

Many languages support re-exporting symbols from submodules through a package's public interface:

```python
# mypackage/__init__.py
from .submodule import helper  # Re-export

# main.py
from mypackage import helper   # Consumer imports from package
helper()                       # Call should resolve to submodule.helper
```

When unresolved, call edges may point to placeholder IDs instead of real symbols. Slicing still works but may miss connections through re-exported symbols.

**Affected languages:** Python, JavaScript/TypeScript, Rust, Haskell, OCaml, Scala, Elixir, Dart, Zig

**Not affected:** Go (package namespace sharing), C/C++ (headers), Java (direct class imports)

**Workaround:** Use fully-qualified imports when precise resolution is critical.

### Import Tracking for Disambiguation

Cross-file call resolution benefits from tracking import statements to disambiguate which module a qualified call refers to:

```python
from app import crud    # Track: crud refers to app.crud
crud.create_user()      # Resolve: app.crud.create_user()
```

Per ADR-0007, import tracking has rolled out broadly: Phase 1 (JS/TS, Kotlin), Phase 2 (Rust, C#, Ruby, Elixir, Swift, PHP, Scala, Dart), and Phase 3A High+Medium (13 + 7 analyzers) are all shipped — ~30 analyzers in total. Python and Go were already correct; Java was already partial and is now improved. Remaining gaps are long-tail niche languages. See [ADR-0007](adr/0007-import-tracking-for-call-resolution.md) for the live phase table.

## 19) Autonomous governance (ADR-0008)

🟩 Hypergumbo includes a vendor-agnostic governance system for AI agent contributors working in autonomous mode. This enforces structural thinking before stopping work, preventing "workaround" fixes that bypass root causes.

### Components

| Component | Path | Purpose |
|-----------|------|---------|
| Stop reflection prompt | `.agent/stop_reflect.md` | Checklist agents must complete before stopping |
| Structured tracker | `.agent/tracker/` | Tracks invariants, work items, and meta-invariants (ADR-0013) |
| Loop sentinel | `.agent/LOOP` | Sentinel file; use `./scripts/loop-toggle` to control |
| Hook adapters | `.agent/hooks/*/` | Per-tool adapter scripts (Claude Code, Gemini CLI, Cursor, Codex CLI) |

### How It Works

1. **Autonomous mode gate:** Hooks only engage when `AUTONOMOUS_MODE.txt` contains an enabling value — TRUE, BROAD, or DEEP (any non-OFF value)
2. **Loop sentinel:** Agents check for `.agent/LOOP`; if present, reflection is required before stopping
3. **Reflection protocol:** Agents complete a structured checklist (invariant identification, structural vs. workaround analysis, scope expansion)
4. **Structured tracker:** Discovered invariants and work items are managed via `scripts/tracker` with status, priority, discussion threads, and regression tests

### Hook Adapters

Each AI coding tool has a different hook mechanism. Adapter scripts provide a consistent interface:

- **Claude Code:** `.agent/hooks/claude-code/stop.sh` (Stop hook with JSON response)
- **Gemini CLI:** `.agent/hooks/gemini-cli/after-agent.sh` (AfterAgent hook)
- **Cursor:** `.agent/hooks/cursor/stop.sh` (stop hook with ASK output)
- **Codex CLI:** `.agent/hooks/codex-cli/notify.sh` (notification only; limited enforcement)

Transcript-aware playbook injection (lazy loading of the per-task playbook into the agent's context) and per-turn heartbeat / respawn supervision are layered on top of these adapters per [ADR-0018](adr/0018-transcript-sync-and-playbook-injection.md).

### Empirical evaluation: bakeoff loops

The structural / coverage / 100% tests in [§16](#16-testing--quality-bar) verify analyzer correctness on fixtures; they don't measure usefulness on real codebases. Two complementary bakeoff loops fill that gap per [ADR-0009](adr/0009-feature-focused-bakeoff.md): **BROAD** mode (`scripts/bakeoff-broad`) iterates linker / framework / call-graph coverage on a wide cohort to find missed edges; **DEEP** mode (`scripts/bakeoff-deep`) evaluates feature usefulness — slice quality, supply-chain-tier accuracy, centrality ranking — on larger repos. Both loops produce reflective artifacts that feed back into tracker items and analyzer/linker improvements.

### Structured Tracker (ADR-0013)

🟩 The markdown-based governance files (invariant ledger, work items) are superseded by a YAML-backed structured tracker ([ADR-0013](adr/0013-structured-tracker.md)). The tracker provides append-only op-logs that are git-merge-safe, causally ordered via Lamport clocks, and support field-level access control. It ships as an independent package (`hypergumbo-tracker`, licensed MPL-2.0) usable in any project — see [the tracker README](../packages/hypergumbo-tracker/README.md) for standalone adoption.

Key capabilities beyond the markdown predecessor:
- **Three visibility tiers:** canonical (shared), workspace (fork-local), stealth (gitignored)
- **Actor-based authority:** `os.getuid()` distinguishes agents from humans; locks, stealth, and discussion clearing are human-only
- **Stop hook integration:** `count-todos`, `hash-todos`, and `guidance` commands replace grep-based TODO scanning
- **TUI:** Interactive terminal interface for human oversight
- **Fork workflow:** `fork-setup` scopes the stop hook to workspace items, so upstream canonical items don't block contributor agents

## 20) Future work

This section collects capabilities that are designed but not yet implemented. The architecture supports these enhancements without breaking changes: the IR ([§6](#6-internal-representation)) enables multi-pass merging, and the schema contract ([Appendix C](#appendix-c-schema-compatibility-contract)) defines what can change in minor vs. major versions.

For detailed designs, see [roadmap-details.md](future/roadmap-details.md) and [Registry & Factory Vision](future/registry-factory-vision.md).

| Item | Horizon | Status |
|------|---------|--------|
| Return-Type Registry pre-pass (INV-dihos) | Near-term | Cross-cutting global registry so `var_types` can chain method-call results through the registered return type of the callee. Five-phase rollout: Phase 1 Java + Go, Phase 2 Kotlin + C#, Phase 3 Rust + C++ + Scala, Phase 4 typed Python, Phase 5 cross-linker (type_hierarchy.py / dataflow.py). See [ADR-0006](adr/0006-variable-type-inference.md). |
| Additional linkers | Near-term | 🟩 Constant propagation for dynamic routes (Python). 🟩 Middleware chain linker (same-file chaining). 🟪 Proxy detection. |
| Additional output views | Near-term | 🟪 `ir_export.json`, `context_bundle.json`, `sarif.json`, flow specs. |
| Testing & CI enhancements | Near-term | 🟪 Longitudinal analysis, integration test markers. |
| Multi-fidelity analysis | Medium-term | 🟩 rust-analyzer SCIP backend shipped (opt-in via `HYPERGUMBO_RUST_ANALYZER=1` or `--backend rust-analyzer`); falls through to tree-sitter `rust.py` when unavailable. 🟪 tsserver, pyright, gopls, JDT backends. Mixed-fidelity graphs. |
| Agent context router | Medium-term | 🟪 Query → slice → context bundle pipeline. Builds on existing slicing. |
| Incremental analysis | Not on roadmap | 18+ month effort. Current mitigation: caching, partial re-analysis. |

Non-goals for future work are consolidated in [§2 Non-goals](#2-non-goals).

## Appendix A: Release lifecycle & support

For the technical contract governing output schema stability, see [Appendix C](#appendix-c-schema-compatibility-contract).

### Semantic versioning

* **Schema versions**: `MAJOR.MINOR.PATCH`
  - MAJOR: Breaking changes (old outputs unreadable)
  - MINOR: Backward-compatible additions (new fields, new views)
  - PATCH: Bug fixes, no schema changes
* **Confidence model versions**: `hypergumbo-evidence-vMAJOR.MINOR`
  - MAJOR: Incompatible changes (requires new schema)
  - MINOR: Refinements (new evidence types, score adjustments)
  - The emitted value carried a bare `v2` until WI-huhin, which did not match this grammar and left MINOR unexpressible — so ADR-0039's refinement (new evidence types, exactly what MINOR is for) had no way to signal itself, and the next refinement would have had to choose between a misleading MAJOR bump and silence. `hypergumbo-evidence-v2.0` is the first *conforming* rendering of the **same** model, not a refinement of `v2`; MAJOR is unchanged and no scoring behaviour differs.

### Compatibility guarantees

* **v0.1 outputs readable by v0.2+** if v0.2 is backward-compatible (MINOR bump)
* **v1.0 outputs readable by v1.x** for all v1.x (MAJOR version promises stability)
* **Breaking changes only in MAJOR bumps.** See [Appendix C](#appendix-c-schema-compatibility-contract) for the technical definition of what constitutes a breaking change.

## Appendix B: Telemetry & privacy

This appendix is the canonical privacy policy for hypergumbo.

### Current policy: zero telemetry

Hypergumbo **sends no data** anywhere. All analysis is local-only. There is no telemetry infrastructure, no crash reporting service, and no opt-in toggle. Nothing is collected.

**What is NEVER collected**:
* Source code
* Symbol names (function/class/variable names)
* File paths or directory names
* API keys or credentials
* IP addresses

### 🟪 Planned: opt-in crash reporting

The following describes a telemetry system that is not yet implemented. If and when it is built, the commitments above will remain the default, and telemetry will require explicit opt-in.

**Planned opt-in mechanism:** `hypergumbo config --telemetry=on` or `HYPERGUMBO_TELEMETRY=1` environment variable.

**What would be collected** (only if opted in):
* Crash stack traces (sanitized: no code, no symbol names, no file paths)
* Performance metrics (file counts, timings, memory usage)
* Feature usage (which commands run, which flags used)
* Anonymized session ID (random UUID, not linked to identity)

**Planned data retention:**
* Crash reports: 90 days
* Aggregated metrics: 2 years
* No raw session data retained beyond 30 days

**Third-party services:**
* If enabled, telemetry would be sent to Sentry (crash reporting) or similar
* Subject to their privacy policies (links provided in docs)

**Transparency commitments** (apply now and after telemetry is built):
* All telemetry code will be open source (auditable)
* Opt-in status will be shown in `hypergumbo config --show`

## Appendix C: Schema compatibility contract

This appendix defines the **technical contract** for output consumers: which fields are immutable, how consumers must handle unknown fields, and what constitutes a breaking change. For versioning policy, see [Appendix A](#appendix-a-release-lifecycle--support).

### Immutable Contracts (MUST NOT change without major version bump)

**1. Node/edge IDs:**
- Location-based `id` format: `{lang}:{file}:{line}-{line}:{name}:{kind}`
- IDs are deterministic (same code → same IDs across runs)
- Stable even if node/edge order changes (sorting defined in spec)

**2. Core fields (cannot remove or change type):**
- Top-level: `schema_version`, `view`, `generated_at`
- `analysis_runs[]`: Array of objects, each with `execution_id`, `pass`, `version`
- `nodes[]`: Array of objects, each with `id`, `kind`, `name`, `path`, `language`
- `edges[]`: Array of objects, each with `id`, `src`, `dst`, `type`
- `features[]`: Array of objects, each with `id`, `name`, `entry_nodes[]`

**3. Provenance fields:**
- `nodes[].origin`: **List of strings**, the pass IDs that contributed to this node,
  ordered chronologically (`list[str]`, INV-jidat; schema-breaking change from a scalar
  string at SCHEMA_VERSION 0.10.0). Single-element lists are the common case;
  `from_dict()` normalizes a legacy scalar to a one-element list. See
  [§6 Internal representation](#6-internal-representation) for the canonical definition —
  this appendix previously described it as a scalar, contradicting §6 (WI-vuton).
- `nodes[].origin_run_id`: String, references `analysis_runs[].execution_id`
- `edges[].origin`, `edges[].origin_run_id`: Same semantics
- `analysis_runs[].run_signature`: Deterministic fingerprint of pass configuration

**4. Confidence scoring:**
- `confidence_model` field identifies the scoring algorithm (`hypergumbo-evidence-v2.0`)
- `confidence` is detection reliability (0.0–1.0); there is no normative default for unknown `evidence_type` (the deterministic evidence→confidence model is **implemented** for seeded pathways — [ADR-0039](adr/0039-confidence-separation.md) / WI-nurun; unseeded/dynamically-computed sites fall back to the caller's value). Ranking prominence lives in the sibling `rank_score` field, and `confidence_source` records how each `confidence` was produced.

### Extensible Contracts (can add in minor versions)

**1. Unknown field tolerance:**
- Consumers MUST ignore unknown top-level fields
- Consumers MUST ignore unknown fields in `nodes[]`, `edges[]`, `features[]`, `analysis_runs[]`
- Consumers MUST ignore unknown keys under `meta.*`
- Reason: Allows future additions like `meta.resolution_confidence`, `nodes[].shape_id`, etc.

**2. Optional fields:**
- Any field marked "optional" can be absent
- Consumers MUST handle absence gracefully (default value or skip)
- Examples: `stable_id`, `shape_id`
**Key presence vs. value presence:**
- Some fields may be semantically optional but SHOULD be present as keys with `null` values to reduce consumer branching.
- Producers SHOULD include these keys with `null` when unknown:
  - `nodes[].stable_id`
  - `nodes[].shape_id`
- Consumers MUST accept either form (missing key OR null), but producers prefer `null` keys for stability.

**3. View types:**
- New view types can be added: `ir_export`, `context_bundle`, `sarif`
- `view: "behavior_map"` schema remains stable across v0.x, v1.x
- Consumers check `view` field, skip unknown views

**Consumer-side substrate loading (`--input`).** Every read subcommand that consumes a `--input` survey routes through one strict loader (`survey_io.load_substrate`), which enforces four guards so a bad input fails loudly instead of silently (INV-sozop / WI-jukah / INV-gapib / WI-marul):
- **Parse:** malformed or empty JSON, or a non-object root, raises a typed `SubstrateError` → clean exit code `2` + stderr message (was an unhandled traceback).
- **Shape:** a dict lacking the structural `nodes` key is rejected → exit `2` (was a silent `rc=0` "No X found", because a `nodes`-less dict reads as an empty map).
- **View discriminator:** a document whose `view` field is *present and different* from the consumer's expected view (e.g. feeding a `slice`/`tiered` projection to a behavior-map consumer) is rejected → exit `2`. A *missing* `view` is accepted (legacy/minimal maps omit it — the guard rejects a *wrong* view, not an absent one).
- **Version (warn-first):** an absent or mismatched `schema_version` prints a stderr `Warning:` but still loads — an older/newer map is usually still readable, so rejecting would be over-strict. (The `nodes` key, not `schema_version`, is the hard-required structural discriminator; `schema_version` is a compatibility *signal*.)

`load_behavior_map` remains the permissive low-level reader for auxiliary artifacts (e.g. compact outputs) that are not the main survey.

### Breaking Changes (only in major version bumps)

**1. Changing field semantics without renaming:**
- Example: Redefining `stable_id` formula (requires major bump)
- Example: Changing `confidence` from 0.0-1.0 to 0-100 scale

**2. Removing required fields:**
- Example: Removing `nodes[].path` (would break path-based tooling)

**3. Changing ID formats:**
- Example: Switching from `python:file.py:10-15:func:function` to `sha256:abc123`

**4. Changing confidence semantics:**
- Example: Redefining what evidence types mean or changing the scoring algorithm

### Version fields: three independent axes

Output carries version numbers along **three deliberately independent axes** (WI-bobog / WI-romup). Consumers must not conflate them, and producers must not consolidate them onto one number or rename the wire fields (each has consumers, so a rename is a breaking change):

1. **Top-level format version** — `schema_version` (currently `0.14.4`, the `SCHEMA_VERSION` constant). The version of the survey (`view: "behavior_map"`) JSON format. Breaking output changes bump minor.
2. **Tool/package version** — `__version__`, surfaced under **three** names that are **not** schema versions: `reproducibility_context.hypergumbo_version` (bare, e.g. `7.0.0`), `analysis_runs[].version` (bare, e.g. `7.0.0`), and `limits.analyzer_version` (prefixed, `hypergumbo-<__version__>`). Increments every release; says nothing about output format. **Only `limits.analyzer_version` carries the `hypergumbo-` prefix**, and that is deliberate — it is the one surface that names the *producing tool* rather than reporting the version of a record the tool is already the author of. Consumers joining these for an equality check must strip the prefix from `analyzer_version`; they are the same underlying `__version__` (WI-tinul, which recorded the three-way disagreement and the failed join).
3. **Per-view / per-sub-schema versions** — several JSON surfaces version their own wire shape independently of axes 1–2:
   - The CLI **read-view** envelopes (`routes`, `test-coverage`, `config`, `catalog`, `cache-status`, `dead-code-maybe`, `repeat-finder`) share `READ_VIEW_SCHEMA_VERSION` (currently `0.1.0`), a single placeholder until one view needs to evolve independently — at which point it promotes to its own named constant.
   - `io-boundaries` carries `IO_BOUNDARIES_SCHEMA_VERSION` (`2.1`), `verify-claims` carries `VERIFY_CLAIMS_SCHEMA_VERSION` (`1.8`), and the embedded `validation_report` block carries `VALIDATION_REPORT_SCHEMA_VERSION` (`0.3`) — each with its own changelog. A change to one of these bumps only that surface's version, **not** the top-level `schema_version`. (So `validation_report.schema_version` legitimately differs from the enclosing map's `schema_version` — they are different schemas.)

### Algorithm-identification fields and `stable_id_scheme` version history

`stable_id_scheme`, `shape_id_scheme`, and `repo_fingerprint_scheme` (top-level fields in the survey) identify the algorithm used to compute the corresponding hash. They are **not** schema-version fields; the schema-version is `schema_version` and changes independently.

* Current value: `stable_id_scheme = "hypergumbo-stableid-v8"`.

`stable_id_scheme` has shipped eight values. Every bump changed the hash basis, so **every affected `stable_id` value differs from the prior scheme's value for the same symbol** even when nothing semantic changed; there is no in-place migration, and a consumer holding a cache produced under an older scheme must re-analyze against the current binary before comparing. The per-transition record (each entry names its driver commit + ADR/invariant ref + date, the hash-basis delta, and the measured collision impact where recorded):

* **v1 → v2** — *ADR-0014 §5; commit `ee680e6712`, 2026-02-20.* Added `containing_module_stable_id` to the typed-tier hash basis so that structurally-identical methods in different classes (e.g. `Foo.process` and `Bar.process`) stop colliding. Every Python `stable_id` value changed.
* **v2 → v3** — *INV-fusus / ADR-0014 §5a; commit `9f943e55fc`, 2026-05-18.* For Python `ClassDef` nodes only, folded a `class_body_sig` component (sorted method names, sorted field names, sorted base names) into the hash. This addressed a 91% same-module collision rate on self-analysis (26,742 of 29,367 nodes sat in a collision group). All Python class and method `stable_id` values changed.
* **v3 → v4** — *INV-zudob; commit `2d62c818bf`, 2026-05-24.* Folded file identity (`make_file_stable_id("python", repo_relative_path)`) into Python top-level `stable_id`s at the three call sites (`ClassDef`, the untyped-function fallback, and the typed-function path) that had previously passed an empty containing id and thereby erased module identity. This reduced the cross-module class collision rate from 18.94% to 1.76% on self-analysis. File identity propagates one hop down into each method's hash automatically.
* **v4 → v5** — *INV-bazij (Phase 6 PR3); commit `ea0154e54f`, 2026-06-01.* Added `name` and `qualified_name` as hash inputs, threaded through ~30 analyzer call sites across every language package. This addressed a 60.2% baseline collision rate (20,517 of 34,108 symbols). **Contract change:** from v5 onward `stable_id` means *structural identity within a `(qualified_name, module_path)` scope* — it survives body edits but **not** a rename or a file move. The earlier ADR-0014 "survives renames and file moves" promise is retired; rename/move tracking is now the job of the content hashes (`fingerprint` / `shape_id`), per [ADR-0035](adr/0035-stable-id-v6-identity-contract.md) §2. The old `(stable_id, canonical_name)` disambiguation escape hatch is dead — `canonical_name` no longer exists (removed by ADR-0032).
* **v5 → v6** — *[ADR-0035](adr/0035-stable-id-v6-identity-contract.md) (stable-id v6 identity contract); 2026-06-15.* The producer formula is unified onto a single `assemble_stable_id` (the Python analyzer's divergent local hasher is folded in) and now hashes the **full enclosing scope chain** — enclosing classes → enclosing functions → name — so two same-local-name symbols in distinct scopes never collide (WI-gitun: function-local `class Args` in distinct functions; a `class Mock` inside methods of distinct classes). The Python class `body_sig` component is **dropped** — it churned the class id on every member add/remove (violating "survives body edits"); structural identity is `shape_id`'s job. An `occurrence_index` slot is added for within-scope ties (`0` in this bump; the slot exists so populating it later needs no further bump). The same-shape kind factories gain path-anchoring (`make_module`/`make_interface`/`make_type`/`make_entry` fold the declaring file path — ADR-0035 §4 — and `make_dependency` folds the declaring **manifest** path so a package declared in N manifests becomes N nodes — WI-titiz). `make_typed_stable_id` gains mandatory `name`/`qualified_name` (WI-zitod). **Every Python `stable_id`, and most other languages' values, changed.** Contract (unchanged from v5's direction, sharpened): v6 survives line drift and body edits, churns on file move + rename; move-tracking stays with `fingerprint`/`shape_id`.
* **v6 → v7** — *WI-bokab (extends ADR-0035 §4 file-anchoring to the tree-sitter producers; closes the corpus limb of INV-tazaj); 2026-06-16.* The v3→v4 INV-zudob fix had folded file identity into the **Python AST** path only. The ~46 tree-sitter analyzers carried scope solely in `qualified_name` and passed an **empty `containing_stable_id`**, so two same-`(kind, name, qualified_name)` symbols in different files collided cross-file (confirmed cross-language: Go `main`/`init`, Rust `project_root`, TS `createMockClient`, bash `usage()`). v7 folds `make_file_stable_id(lang, normalize_path(rel_path))` into the `containing_stable_id` slot of the two shared producer entrypoints — `compute_stable_id` (untyped tier) and `make_typed_stable_id` (typed tier) — at **every** producer call site (a `file_stable_id=` argument; an explicit non-empty containing always wins). The `rust.py` ↔ `rust_scip.py` byte-for-byte SCIP-parity contract (WI-zakub) is preserved by threading the identical repo-relative anchor on both backends. **Every tree-sitter-language `stable_id` value changed; Python (`py.py`) values are unchanged (already file-anchored since v4).** Measured: cross-file corpus collisions on a Rust/Go/TS/bash sample dropped to 0. Contract unchanged from v6 (survives body edits + line drift; churns on file move + rename).
* **v7 → v8** — *WI-gokiv / WI-napoh / WI-bolup ([ADR-0035](adr/0035-stable-id-v6-identity-contract.md) §3 amendment; closes INV-tazaj's corpus limb); 2026-06-16.* The Wave-2 identity gate (27-repo corpus + self-tree) found v7's per-file gate clean but the corpus rate not ~0: 149 cross-file collisions, all synthetic/linker-stamped nodes the file-identity train had not reached. v8 folds file identity into the three residual classes. **(1) routes** — a post-pass `widen_route_stable_ids` folds the declaring `{language}:{normalize_path(rel_path)}` onto route nodes, since `make_route_stable_id` keyed `route:{method}:{path}` file-blind (the same `(method, path)` across files/languages collided — express's 33 example apps, gin-Go + express-JS); route stays LOGICAL *in spirit* (within-app dedup is the route materializer's job, keyed on meta, not stable_id). **(2) http/sql `call_site`** — a new `make_site_stable_id(protocol_origin, rel_path, target)` factory (`site:` namespace) path-anchors these SITE-axis kinds at mint, which had borrowed the LOGICAL `make_route`/`make_protocol` factories. **(3) CBV HTTP-verb methods** — the `py.py` branch that keyed `kind="method"` declarations via `make_route_stable_id(verb, class_name)` is deleted so they flow through the file-anchored method path. **Every `route`, http/sql `call_site`, and CBV-verb-method `stable_id` value changed; analyzer value-pin tests are unchanged (the route producers' provisional 2-arg id is folded only in the full-pipeline post-pass).** Contract unchanged from v6/v7.

v8 is the **current shipped value**. This per-transition history is maintained as the precondition for each bump (never bump a scheme onto an undocumented chain).

Bumping a scheme identifier is not by itself a major-version event — the output format is unchanged, only the value distribution. Consumers that store `stable_id` values across analyses must check the scheme identifier and bail or re-analyze when it differs from the cached one.

### Future Additions (examples of backward-compatible changes)

**Possible in future minor versions:**
- `run_signature` refinements (additional hash inputs)
- `stable_id` improvements (better heuristics for untyped code)
- `meta.resolution_confidence` (optional, from type checkers)
- `ir_export.json` view (new view type)
- New `evidence_type` values
- New `kind` values for nodes

**Commitment:** No breaking changes to the survey view within the v0.x series. (The on-disk `view` discriminator value stays `"behavior_map"` for schema compatibility — only the concept name and default filename became "survey"/`survey.json` per ADR-0042; consumers parsing the JSON see no change.)

## Appendix D: Capsule system history

The capsule system (custom analyzer composition via `hypergumbo init`) was removed in v1.0.0. See [history/capsule-system-v1.md](history/capsule-system-v1.md) for details. Other archived v1.0 materials: [history/planning-v1.md](history/planning-v1.md), [history/validation-gates-v1.md](history/validation-gates-v1.md).
