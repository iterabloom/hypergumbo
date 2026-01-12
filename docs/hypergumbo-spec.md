# Hypergumbo Spec (MVP + Future Phases)

Status: draft, living document.

- Spec A: MVP behavior map + capsules (current focus of this repo).
- Spec B: Multi-phase, Galaxy Brain roadmap (not implemented yet).

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

# Spec A — hypergumbo MVP

## 0) One-sentence summary
A local-first CLI that (1) profiles a repo, (2) composes a **portable analyzer capsule** from pre-approved building blocks (optionally LLM-assisted), and (3) runs that capsule to emit a **repo behavior map** (versioned JSON views from an internal IR) with machine-readable provenance for agent-friendly context.

## 1) Goals
* 🟩 **Internal IR with views**: Parsers emit to an internal representation; public outputs are compiled views (enables future typed passes without breaking schema).
* 🟩 **Provenance tracking**: Every node/edge records which analyzer pass created it, with unique execution identifiers enabling quality assessment and mixed-fidelity analysis.
* 🟩 **Machine-readable provenance**: All confidence scores and edge evidence captured in structured fields, not just human-readable strings, enabling programmatic filtering and multi-pass merging.
* 🟩 **Capsule Plan composition**: `hypergumbo init` generates a validated `capsule_plan.json` selecting from pre-approved passes/packs/rules in a `catalog.json`. LLM may assist with plan generation **(optional)**, but `hypergumbo run` stays deterministic and offline-by-default.
* 🟩 **Portable analyzer artifact**: `.hypergumbo/` (manifest + plan + execution spec) that can be committed/shared without repo code, with security defaults and toolchain versioning.
* 🟩 **Agent-ready output**: deterministic JSON graph + "feature slices" so an agent can fetch only relevant code.
* 🟩 **Fast iteration**: simple architecture, small dependency surface, fixtures-driven tests.
* 🟩 **Local-first execution**: analysis runs offline by default (no network, no API keys required).

## 2) Non-goals (for MVP)
* No deep type-resolution / interprocedural dataflow correctness guarantees.
* No central registry, accounts, ratings, or social features.
* No automatic PR fixing, no code editing, no CI annotations beyond "export JSON."
* No attempt to support *every* language—support a small set well.
* No incremental analysis daemon (full re-analysis is acceptable for MVP).
* No LLM-generated analyzer *code* in MVP. LLM *may* assist with **Capsule Plan** generation (validated JSON selecting pre-approved components) during `hypergumbo init` only. `hypergumbo run` remains offline-by-default.

## 3) User experience (CLI)

### Install
* `pipx install hypergumbo` (primary, includes all 67 language analyzers)
* `pip install hypergumbo` (secondary)
* `pip install hypergumbo[llm-assist]` (optional OpenAI support for plan generation)
* `pip install hypergumbo[llm-local]` (optional local LLM support via llm package)
* `pip install hypergumbo[embeddings]` (optional embedding-based config extraction)

### Commands
🟩 **`hypergumbo [path] [-t tokens]`** (default mode)
Generates a token-budgeted Markdown sketch to stdout. Optimized for pasting into LLM chat interfaces.
* If no subcommand is given, assumes sketch mode.
* `-t N` limits output to approximately N tokens.

🟩 **`hypergumbo init [--capabilities python,javascript] [--assistant template|llm] [--llm-input tier0|tier1|tier2]`**
Creates `.hypergumbo/` containing:
* `capsule.json` (manifest: format version, requirements, capabilities, security defaults)
* `capsule_plan.json` (validated composition plan)
* `catalog.json` (optional to *copy into capsule* only if you want portability of the menu; otherwise it stays in the installed package)
* `analyzer.py` is a **stable runner** that reads the plan (not a generated analyzer script).
* `hypergumbo/runner.py` — Runner abstraction; selects execution strategy by capsule `format` (v0.1 implements subprocess runner for `python_script`)
* `hypergumbo/subprocess_runner.py` — Launches analyzer in a subprocess, applies resource limits, collects outputs
* `.hypergumbo/config.json` (analysis configuration)
* `.hypergumbo/profile.json` (repo profiling results)
* `tier0`: profile metadata only
* `tier1`: allowlisted config files only (package.json, pyproject.toml, etc.)
* `tier2`: “repo sketch” (structure-only summaries, no raw code. Show a preview of exactly what will be sent.)
Auto-detects languages if `--capabilities` not specified.
Sets security defaults: `trust: local_only`, `network: deny`, `sandbox: recommended`, `validation_mode: strict`.
* If `--assistant llm`, generate `capsule_plan.json` using an LLM **but always validate** against the local catalog; on failure, fall back to template plan.
* If `--assistant template` (default), generate the plan without any LLM.

🟩 **`hypergumbo run [path] [--out hypergumbo.results.json]`**
Runs the analyzer capsule on the repo. If no capsule exists, auto-generates a default one with a warning.

🟩 **`hypergumbo slice --entry <symbol|file|route> [--out slice.json]`**
Produces a reduced subgraph suitable for LLM context.

🟩 **`hypergumbo catalog [--show-all]`**
Displays available passes, packs, and rule templates. Use `--show-all` to include optional extras requiring additional dependencies (e.g., tree-sitter language packs).

Example output:
```
Available Passes:
  - python-ast-v1: Python AST parser
  - javascript-ts-v1: JS/TS via tree-sitter
  - java-ts-v1: Java via tree-sitter
  - go-ts-v1: Go via tree-sitter
  ... (67 language passes available)

Available Packs:
  - python-fastapi: FastAPI route detection + call graph
  - electron-app: Main/renderer split + IPC detection
  - react-nextjs: Component tree + route mapping
```

🟩 **`hypergumbo export-capsule --shareable [--out capsule.tar.gz]`**

Exports the analyzer capsule in a privacy-safe format suitable for sharing or publishing to a registry.

**Redactions applied (shareable mode):**
* Strips repo file paths from capsule metadata where present (replaces with placeholders: `<file-1>`, `<file-2>`, etc.)
* Excludes `.hypergumbo/profile.json` (contains repo structure info)
* Excludes `.hypergumbo/cache/` (repo-specific cached results)

* Sanitizes `capsule_plan.json`:
  - Removes `features[]` entirely (feature queries are commonly repo-specific: routes, symbols, internal identifiers)
  - Removes any `rules[]` entries that contain repo-specific selectors, including:
    - literal file paths (non-glob), directory names unique to the repo
    - explicit symbol names or fully-qualified identifiers
    - explicit HTTP routes or IPC channels
  - Preserves only “generic” rules such as standard excludes (e.g., `**/*_test.py`, `node_modules/**`) and size limits
  - Emits a summary of removed items in `SHAREABLE.txt` (counts + categories), not the original values

* Preserves:
  - `capsule.json` (manifest)
  - `capsule_plan.json` (sanitized composition plan; see SHAREABLE redactions)
  - `analyzer.py` (runner script)
  - `catalog.json` (if customized)

**Output format:**
* Tarball containing capsule files
* Includes `SHAREABLE.txt` marker file documenting redactions applied, including:
  - which plan sections were removed (`features`, `repo_specific_rules`)
  - counts of removed entries (no original values)
  - shareable capsule format/version and checksums
* Includes integrity checksums (SHA256SUMS)

**Use case:** Share analyzer configuration without leaking repository structure. Shareable capsules contain no source code, no symbol names, no file paths from your repository.

🟩 **`hypergumbo test-coverage [path] [--format text|json]`**

Estimates test coverage by analyzing which functions are called by tests. Uses static analysis only (no code execution). Language agnostic.

**Features:**
* **Hot spots:** Functions called by many tests (potential redundancy)
* **Cold spots:** Functions not called by any tests (need coverage)

**Filtering options:**
* `--min-tests N`: Only show functions called by at least N tests
* `--max-tests N`: Only show functions called by at most N tests (0 = untested only)
* `--top N`: Limit output to top N hot/cold spots

**Example output (text format):**
```
Test Coverage Estimate
======================
Total functions: 150
Tested: 120 (80.0%)
Untested: 30
Total test functions: 45

Hot Spots (highest test density - tests per LOC)
------------------------------------------------
  5.00 t/LOC  ( 15 tests,   3 LOC)  utils.py:10-12   helper()
  0.60 t/LOC  ( 12 tests,  20 LOC)  core.py:50-69    validate()

Cold Spots (untested - need coverage)
-------------------------------------
    0 tests  core.py:100-150  process()  [50 LOC, complexity: 8]
```

**Note:** Hot spots are ranked by test density (tests/LOC), not raw test count. This surfaces small utility functions that are disproportionately tested relative to their size.

**Use case:** Quickly identify which parts of your codebase may need more test coverage, without running any tests.

### Key principle
Initialization may use language detection; **analysis execution requires no network or API keys** (by default). The capsule should be deterministic and reproducible given the same repo state.

## 4) Supported stacks

Hypergumbo supports 67 languages via tree-sitter grammars. All are included in the base package.

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
* 🟩 Bash, Clojure, Dart, Elm, Erlang, F#, Fortran, Haskell, Julia, Lua, Nim, OCaml, Perl, R, Zig, and 30+ more

**Configuration/data formats:**
* 🟩 JSON, YAML, TOML, XML, HCL/Terraform, Dockerfile, Makefile, CMake, SQL, GraphQL, Protobuf, Thrift

**Markup:**
* 🟩 HTML (script tag extraction), CSS, LaTeX, Markdown

> The analyzer is "best-effort, explicitly limited," but produces consistent structures.

### Dependency strategy
* **All-in-one package**: `pip install hypergumbo` includes Python AST + 40+ tree-sitter grammars as standard dependencies
* **Tree-sitter grammars included**: JavaScript, TypeScript, PHP, C, C++, Java, Go, Rust, Ruby, Kotlin, Swift, Scala, Lua, Haskell, OCaml, Elixir, Dart, LaTeX, R, COBOL, and many more
* **Language pack**: `tree-sitter-language-pack` provides additional grammars (Elixir, COBOL, Dart, LaTeX, R)
* **Build-from-source grammars**: Lean, Wolfram built from source in CI for languages lacking PyPI packages
* **Fallback**: If a specific grammar fails to load, that language is skipped with explicit `limits.skipped_languages[]` logging
* **Optional extras**:
  - `[llm-assist]`: OpenAI for LLM-assisted plan generation
  - `[llm-local]`: Local LLM support via llm package
  - `[embeddings]`: sentence-transformers for embedding-based config extraction
### Build strategy
* Tree-sitter grammars with PyPI wheels are installed directly as dependencies
* Grammars without PyPI packages (Lean, Wolfram) are built from source in CI (`scripts/build-source-grammars`)
* 100% test coverage required; analyzers gracefully skip when grammars unavailable

## 5) Architecture (local-only)

### Core packages

**CLI & orchestration:**
* `cli.py` — CLI entrypoint and command handlers
* `profile.py` — language/framework detection
* `discovery.py` — file finding with exclude patterns

**Internal representation:**
* `ir.py` — Symbol, Edge, Span, AnalysisRun classes
* `schema.py` — JSON schema versioning and behavior map factory

**Analysis pipeline:**
* `sketch.py` — token-budgeted Markdown summary generation
* `entrypoints.py` — YAML-driven entrypoint detection (ADR-0003)
* `slice.py` — graph slicing for context extraction
* `metrics.py` — analysis statistics computation
* `limits.py` — error tracking and analysis gaps
* `supply_chain.py` — file classification by dependency tier (1-4)

**Language analyzers (examples):**
* `analyze/py.py` — Python AST parser
* `analyze/js_ts.py` — JS/TS/Svelte parser via tree-sitter
* `analyze/java.py` — Java parser via tree-sitter

**Cross-language linkers (examples):**
* `linkers/jni.py` — JNI boundary detection (Java↔C)
* `linkers/ipc.py` — IPC message channel detection

**Vestigial modules** (kept for `init` command, not used by `run`):
* `catalog.py` — Pass/pack availability checking
* `plan.py` — capsule_plan.json generation
* `llm_assist.py` — LLM-assisted plan generation (proof of concept)
* `export.py` — capsule export

See [Capsule System History](history/capsule-system-v1.md) for design context.

### IR Layer
Parsers emit to `AnalysisIR`:
```python
@dataclass
class Symbol:
    id: str                    # location-based identifier
    stable_id: Optional[str]   # semantic identity hash (signature-based)
    shape_id: Optional[str]    # structural implementation fingerprint
    canonical_name: str
    fingerprint: str           # content hash
    kind: str                  # function, class, module, etc.
    name: str
    path: str
    language: str
    span: Span
    origin: str                # which pass created this
    origin_run_id: str         # references AnalysisRun.execution_id
    supply_chain_tier: int     # 1=first_party, 2=internal_dep, 3=external_dep, 4=derived
    supply_chain_reason: str   # classification rationale (e.g., "matches ^src/")
    origin_run_signature: Optional[str]  # references AnalysisRun.run_signature (for grouping)
    quality: QualityScore

@dataclass
class AnalysisRun:
    execution_id: str          # unique per run (uuid or hash of run_signature + started_at + repo_fingerprint)
    run_signature: str         # deterministic: hash of (pass_id, version, config_fingerprint, toolchain)
    repo_fingerprint: str      # hash of (git_head + dirty_files) or hash of (file_list + content_hashes)
    pass: str                  # e.g., "python-ast-v1"
    version: str               # e.g., "hypergumbo-0.1.0"
    toolchain: Dict            # {"name": "python", "version": "3.11.0"}
    config_fingerprint: str    # sha256 of effective config
    files_analyzed: int
    files_skipped: int
    skipped_passes: List[Dict] # passes that couldn't run
    warnings: List[str]
    started_at: str
    duration_ms: int

@dataclass
class AnalysisIR:
    runs: List[AnalysisRun]           # provenance: which passes ran
    symbols: List[Symbol]              # definitions (funcs, classes, etc)
    references: List[Reference]        # use sites
    relationships: List[Relationship]  # typed edges with quality scores
```

**Identity field semantics**:
* `id` (location-based): `{lang}:{file}:{start_line}-{end_line}:{name}:{kind}`
  - Changes when code moves to different file/line
  - Purpose: Reproducible slicing, deterministic diffs
* `stable_id` (semantic, optional): Interface identity (signature-based), **not implementation identity**
  - **For typed languages or annotated Python**: `sha256({kind}:{normalized_signature}:{visibility}:{containing_module_stable_id})`
    - `normalized_signature`: Canonical type signature (param types, return type, type params)
    - `visibility`: public, private, protected (if language has concept)
    - `containing_module_stable_id`: Recursive stable_id of parent module/class
    - **Excludes**: Implementation details, docstrings, comments
  - **For untyped code**: `sha256({kind}:{parameter_count}:{arity_flags}:{decorator_presence}:{containing_module_stable_id})`
    - `arity_flags`: has_defaults, has_varargs, has_kwargs (structural signature info)
    - `decorator_presence`: Sorted list of decorator names (e.g., `["property", "staticmethod"]`)
    - **Excludes**: Source hash, canonical name (survives renames)
  - Purpose: Track symbols across refactors (renames, moves, documentation changes)
  - **Does NOT change** when: Renaming, moving between files, changing implementation, adding comments
  - **DOES change** when: Signature changes (param types, arity), visibility changes, decorators added/removed
* `shape_id` (optional): Structural implementation fingerprint
  - `sha256(ast_structure)` excluding literals/identifiers
  - Purpose: Detect structural changes (control flow, nesting) without caring about variable names
  - Use case: "Implementation changed but signature stayed same"
**Scheme versioning note:** The exact algorithms for `stable_id` and `shape_id` are governed by `stable_id_scheme` and `shape_id_scheme` in the output. Any change that would alter computed values MUST bump the corresponding scheme identifier.
* `fingerprint` (content hash): `sha256(source_bytes)`
  - Changes when implementation changes
  - Purpose: Detect modifications

**Example**:
```python
# Original:
def authenticate(username: str, password: str) -> User:
    ...

# After rename and move:
# File: auth_service.py → user_auth.py
# Function: authenticate → verify_credentials
# stable_id stays the same (signature unchanged)
# id changes (file and name changed)
# fingerprint changes if implementation changed
# shape_id changes if control flow changed
```

**Provenance field semantics**:
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
  - Purpose: Cache invalidation, provenance tracking
Public outputs are **compiled views** from this IR:
* 🟩 `behavior_map.json` (v0.1 default)
* 🟩 `sketch` — Token-budgeted Markdown summary for LLM context windows (stdout)
* 🟪 Future: `ir_export.json`, `sarif.json`, `context_bundle.json`

**Design principle:** Strong passes (tsserver, pyright) added later will enhance the IR without breaking the behavior map view.

### Pass interface and registry
Parsers implement a common interface for future multi-pass orchestration:
```python
class AnalysisPass(Protocol):
    """Interface for pluggable analysis passes."""
    
    id: str              # e.g., "python-ast-v1"
    version: str         # e.g., "hypergumbo-0.1.0"
    capabilities: List[str]  # e.g., ["python"]
    
    def run(
        self, 
        ir: AnalysisIR, 
        files: List[Path], 
        config: Config
    ) -> IRDelta:
        """
        Run analysis pass on given files.
        
        Returns:
            IRDelta: New symbols, references, relationships to add to IR
        """
        ...
```

See §4 "Supported stacks" for the full list of 67 language analyzers, 14 cross-language linkers, and 37 framework patterns. Detailed reference: [LANGUAGES.md](LANGUAGES.md), [LINKERS.md](LINKERS.md).

## 6) Output: "Repo Behavior Map" JSON (v0.1)

Single file: `hypergumbo.results.json`

### Top-level structure
```json
{
  "schema_version": "0.1.0",
  "confidence_model": "hypergumbo-evidence-v1",
  "stable_id_scheme": "hypergumbo-stableid-v1",
  "shape_id_scheme": "hypergumbo-shapeid-v1",
  "repo_fingerprint_scheme": "hypergumbo-repofp-v1",
  "view": "behavior_map",
  "generated_at": "2024-01-15T10:30:00Z",
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

### JSON Schema (Auto-Generated)

A formal JSON Schema is available at `docs/schema.json`. This schema is **auto-generated** from the Python dataclasses in `src/hypergumbo/ir.py` to ensure it stays in sync with the implementation.

**Regenerate with:** `./scripts/generate-schema`

**Verify in CI with:** `./scripts/generate-schema --check`

The schema follows JSON Schema Draft 2020-12 and can be used for:
- Validating hypergumbo output files
- IDE autocompletion for consumers
- Documentation in a standard format

**DRY Principle:** The Python dataclasses (`Symbol`, `Edge`, `Span`, `AnalysisRun`) are the single source of truth. The JSON Schema and this spec document the *meaning* of fields; the dataclasses define the *structure*.

**Scheme identifiers (new, v0.1.0):**
- `stable_id_scheme`: identifies the algorithm/normalization used to compute `stable_id`
- `shape_id_scheme`: identifies the algorithm used to compute `shape_id`
- `repo_fingerprint_scheme`: identifies the algorithm used to compute `analysis_runs[].repo_fingerprint`

These fields prevent semantic drift: if an algorithm changes in the future, the scheme string MUST change.

**analysis_incomplete** (boolean, default: false):
- Set to `true` if analysis terminated early due to errors, timeouts, or resource limits
- When true, output is valid JSON but may be missing nodes/edges
- Check `limits.partial_results_reason` for details
- Agents should decide whether partial results are sufficient for their use case

### Confidence scoring

The `confidence` field on edges (0.0-1.0) indicates detection reliability:

| Evidence Type | Base Score | Example |
|---------------|------------|---------|
| `ast_call_direct` | 0.90 | Direct function call in AST |
| `ast_call_method` | 0.85 | Method call on object |
| `import_static` | 0.95 | Static import statement |
| `pattern_match` | 0.80 | Framework pattern (decorator, annotation) |
| `cross_lang_link` | 0.75 | Cross-language boundary (JNI, IPC) |
| `inferred` | 0.60 | Heuristic inference |

The `confidence_model` field (`hypergumbo-evidence-v1`) identifies the scoring algorithm. Consumers should treat unknown evidence types as 0.30 confidence.

### analysis_runs[] — provenance tracking
```json
{
  "execution_id": "uuid:abc-def-789...",
  "run_signature": "sha256:xyz789...",  // (deterministic hash of pass+version+config_fingerprint+toolchain)
  "repo_fingerprint": "sha256:repo123...",  // (deterministic snapshot id)
  "pass": "python-ast-v1",
  "version": "hypergumbo-0.1.0",
  "toolchain": {"name": "python", "version": "3.11.0"},
  "config_fingerprint": "sha256:abc123...",
  "files_analyzed": 42,
  "files_skipped": 1,
  "skipped_passes": [],  // (for requested-but-unavailable components)
  "warnings": ["skipped bundle.min.js (2.1MB exceeds limit)"],
  "started_at": "2024-01-15T10:30:00Z",
  "duration_ms": 1234
}
```

**Field semantics:**
* `execution_id`: Unique identifier for this specific analysis run
  - Format: `uuid:` prefix for UUID v4, or `sha256:` for deterministic hash
  - Used to identify which analysis run produced which nodes/edges
  - Enables multi-pass merging and provenance tracking
* `run_signature`: Deterministic fingerprint of analyzer configuration
  - Hash of (pass_id, version, config_fingerprint, toolchain)
  - Same pass + version + config + toolchain → same signature
  - Used for cache keying and grouping results
* `repo_fingerprint`: Hash identifying the code snapshot analyzed
  - Git repos: `sha256(git_head + sorted([(path, sha256(content_bytes)) for each dirty file]))`
    - Includes the content hash of dirty tracked files and included untracked files to avoid false cache hits.
  - Non-git: `sha256(sorted([(path, content_hash) for all files]))`
  - Enables cache keying and provenance tracking
* `toolchain`: Versions of language runtimes/parsers used (empty `{}` for syntax-only passes)
* `config_fingerprint`: Hash of effective configuration affecting this pass (for cache invalidation)

**skipped_passes** (array, optional):
- List of passes that were requested in capsule_plan.json but could not run
- Each entry includes pass ID and reason
- Example:
```json
"skipped_passes": [
  {
    "pass": "lean-ts-v1",
    "reason": "tree-sitter-lean grammar not available"
  }
]
```

### profile — repo characteristics

```json
{
  "languages": {
    "python": {"files": 42, "loc": 15230},
    "javascript": {"files": 18, "loc": 8420}
  },
  "frameworks": ["fastapi", "react"],
  "repo_kind": "web_api"
}
```

### nodes[] — definitions, files, endpoints

**Node fields:**
```json
{
  "id": "python:src/auth.py:42-48:login:function",
  "stable_id": "sha256:abc123...",
  "shape_id": "sha256:shape456...",
  "canonical_name": "myapp.auth.login",
  "fingerprint": "sha256:def456...",
  "kind": "function",
  "name": "login",
  "path": "src/auth.py",
  "language": "python",
  "span": {
    "start_line": 42,
    "end_line": 48,
    "start_col": 0,
    "end_col": 15
  },
  "origin": "python-ast-v1",
  "origin_run_id": "uuid:abc-def-789...",
  "origin_run_signature": "sha256:xyz789...",
  "quality": {
    "score": 0.9,
    "reason": "AST-based definition, unambiguous scope"
  },
  "supply_chain": {
    "tier": 1,
    "tier_name": "first_party",
    "reason": "matches ^src/"
  }
}
```
**Presence rule (v0.1.0):**
- `stable_id`, `shape_id`, and `origin_run_signature` keys MUST be present on every node.
- If unavailable, they MUST be set to `null` (not omitted).
- This supports forward-compatible consumers and Spec B prerequisites without forcing every pass to compute every field.

**supply_chain** (object, required):
- `tier` (integer, 1-4): Numeric tier for filtering/sorting
- `tier_name` (string): Human-readable name (`first_party`, `internal_dep`, `external_dep`, `derived`)
- `reason` (string): Classification rationale (e.g., "matches ^src/", "detected as minified")
- See §8.6 for classification algorithm and tier definitions.

**origin_run_id**: References `analysis_runs[].execution_id` (unique per run). When present, indicates exactly which analysis run created this node.

**origin_run_signature** (optional): References `analysis_runs[].run_signature` (for grouping nodes by analyzer configuration).

**Node kinds:**
* `file` — source file
* `module` — Python module, JS module
* `function` — function/method
* `class` — class definition
* `endpoint` — HTTP route, IPC handler, CLI entrypoint

### edges[] — relationships

**Edge fields:**
```json
{
  "id": "edge:sha256:def456...",
  "edge_key": "edgekey:sha256:rel_abc123...",
  "type": "calls",
  "src": "python:src/auth.py:42-48:login:function",
  "dst": "python:src/db.py:10-15:query_user:function",
  "confidence": 0.85,
  "origin": "python-ast-v1",
  "origin_run_id": "uuid:abc-def-789...",
  "origin_run_signature": "sha256:xyz789...",
  "quality": {
    "score": 0.85,
    "reason": "Direct AST call"
  },
  "meta": {
    "evidence_type": "ast_call_direct",
    "evidence_lang": "python",
    "evidence_spans": [
      {
        "file": "src/auth.py",
        "span": {"start_line": 45, "end_line": 45, "start_col": 8, "end_col": 24}
      }
    ],
    "evidence": [
      {
        "origin": "python-ast-v1",
        "origin_run_id": "uuid:abc-def-789...",
        "origin_run_signature": "sha256:xyz789...",
        "evidence_type": "ast_call_direct",
        "evidence_lang": "python",
        "evidence_spans": [
          {
            "file": "src/auth.py",
            "span": {"start_line": 45, "end_line": 45, "start_col": 8, "end_col": 24}
          }
        ],
        "confidence": 0.85
      }
    ]
  }
}
```
**edge_key (new, v0.1.0):**
- `edge_key` is a canonical identity used to deduplicate/merge multiple observations of the “same” relationship across passes.
- Format: `edgekey:sha256:<hash>`
- Recommended hash inputs (deterministic):
  - `type`
  - `src` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
  - `dst` (prefer `stable_id` if both src/dst nodes have it, else use `id`)
- `id` remains a unique identifier for this edge record instance.

**meta.evidence[] (optional, v0.1.0):**
- `meta.evidence[]` is an optional array of evidence records. Each record captures one piece of evidence from one analysis run.
- When present, the top-level `meta.evidence_type`, `meta.evidence_lang`, and `meta.evidence_spans` MUST reflect the “primary” evidence (typically the highest-confidence record), to preserve compatibility with v0.1 consumers.
- Mixed-fidelity graphs (future Spec B) SHOULD accumulate evidence in `meta.evidence[]` rather than overwriting provenance.

**New meta fields**:
- `evidence_lang` (optional): Language used for confidence scoring. Defaults to `src` node's language if omitted. Required for cross-language edges (HTTP, IPC) where src/dst languages differ.
- `evidence_spans[]`: Structured locations of evidence. Each span includes file path and line/column range.

**Confidence model (evidence-based):**

Source: `confidence` field, derived from `meta.evidence_type` via deterministic matrix.

**Evidence types** (machine-readable):
* `ast_call_direct` — Direct function call in AST
* `ast_call_method` — Method call with receiver
* `ast_getattr_call` — Call via getattr/dynamic lookup
* `import_static` — Static import statement
* `import_dynamic` — Dynamic import (importlib, require with variable)
* `script_src` — HTML script tag src attribute
* `script_inline` — Inline script content

**quality.reason** remains for human debugging but is NOT relied upon for programmatic logic.

**Edge types:**
* `calls` — function/method invocation
* `imports` — module/symbol import
* `defines` — definition relationship
* `renders` — template rendering
* `loads_script` — script tag src
* `implements` — class implements interface (Java, TypeScript)
* `extends` — class extends base class
* `native_bridge` — Java native method → C implementation (JNI)
* `message_send` — sends IPC/protocol message
* `message_receive` — handles IPC/protocol message
* `instantiates` — class instantiation (constructor call)
* `manual` — user-annotated

### features[] — named slices

**Feature structure:**

```json
{
  "id": "sha256:feature_query_hash...",
  "name": "auth-flow",
  "entry_nodes": ["python:src/auth.py:42-48:login:function"],
  "node_ids": ["python:src/auth.py:42-48:login:function", "..."],
  "edge_ids": ["edge:sha256:def456...", "..."],
  "query": {
    "method": "bfs",
    "entrypoint": "fastapi_route:/api/login",
    "hops": 3,
    "max_files": 20,
    "exclude_tests": true
  },
  "limits_hit": ["hop_limit"],
  "summary": "User authentication flow from FastAPI route to database query"
}
```

**Feature ID:** Stable identifier based on query spec: `sha256(json.dumps(query, sort_keys=True))`

**Query reproducibility:** Same query on same code → same feature ID → enables diff across commits.

### metrics — optional counts

```json
{
  "total_nodes": 523,
  "total_edges": 1847,
  "avg_confidence": 0.82,
  "languages": {
    "python": {"nodes": 320, "edges": 1200},
    "javascript": {"nodes": 203, "edges": 647}
  },
  "by_supply_chain_tier": {
    "first_party": {"nodes": 380, "edges": 1200},
    "internal_dep": {"nodes": 85, "edges": 150},
    "external_dep": {"nodes": 58, "edges": 497}
  }
}
```

### supply_chain_summary — classification overview

```json
{
  "supply_chain_summary": {
    "first_party": {"files": 42, "symbols": 380},
    "internal_dep": {"files": 12, "symbols": 85},
    "external_dep": {"files": 8, "symbols": 58},
    "derived_skipped": {
      "files": 3,
      "paths": ["dist/bundle.js", "build/app.min.js", "out/compiled.js"]
    }
  }
}
```

**derived_skipped.paths**: Capped at 10 entries. Full list available via `--verbose` flag.

### limits — explicit gaps

```json
{
  "not_captured": [
    "dynamic imports (importlib, require with variables)",
    "eval() and exec() calls",
    "decorators with complex logic"
  ],
  "truncated_files": [
    {
      "path": "dist/bundle.min.js",
      "size_bytes": 2100000,
      "reason": "exceeds --max-file-bytes"
    }
  ],
  "skipped_languages": ["go", "rust"],
  "failed_files": [
    {
      "path": "malformed.py",
      "reason": "SyntaxError: invalid syntax (line 42)",
      "analyzer": "python-ast-v1"
    }
  ],
  "partial_results_reason": "",
  "analyzer_version": "hypergumbo-0.1.0",
  "capsule_version": "sha256:abc123...",
  "analysis_depth": "syntax_only"
}
```

**partial_results_reason** (string, optional):
- Present only when `analysis_incomplete: true`
- Human-readable explanation of why analysis did not complete
- Examples:
  - `"Timeout: Analysis exceeded 300 seconds"`
  - `"Resource limit: Memory usage exceeded 2GB"`
  - `"Critical error: catalog.json could not be loaded"`
  - `"User interrupted: Ctrl-C received"`

### sketch — Human/LLM-readable summary

Markdown output to stdout (not a file). Designed for pasting into LLM chat interfaces.

**Contents (in priority order for truncation):**
1. 🟩 Header: repo name, language breakdown, LOC estimate (always included)
2. 🟩 Entry points: detected routes, CLI mains, etc.
3. 🟩 Structure: top-level directory overview
4. 🟩 Build: detected build system (CMake, npm, etc.)
5. 🟩 Dependencies: key frameworks

**Token budget:** `-t N` truncates at section boundaries, preserving higher-priority sections.

**Example:**
```markdown
# minetest-wasm

## Overview
C++ (82%), Lua (12%), CMake (6%) · 847 files · ~120k LOC

## Entry Points
- `src/main.cpp:main()` — Application entry
- `src/client/client.cpp:Client::Client()` — Client initialization

## Structure
- `src/` — Core source
- `builtin/` — Lua built-ins
- `games/` — Game content

## Build
CMake, Emscripten
```

## 7) Slicing behavior (MVP)

### Entry sources

* 🟩 **Detected endpoints** (FastAPI/Flask/Express heuristics):
  * `@app.route`, `@app.get`, `app.get(`, `app.post(`
* 🟩 **Electron main/renderer hints**:
  * File names: `main.js`, `renderer.js`, `preload.js`
  * IPC patterns: `ipcMain.on`, `ipcRenderer.send`
* 🟩 **CLI entrypoints**:
  * Python: `if __name__ == "__main__"`
  * JavaScript: `process.argv` parsing patterns

### Slicing algorithm

* 🟩 **Method**: BFS or DFS on relationships
* 🟩 **Limits**:
  * Hop limit (default: 3)
  * File count limit (default: 20)
  * Configurable via `--max-hops`, `--max-files`
* 🟩 **Edge filtering**: Optionally exclude tests, exclude low-confidence edges

### Slice identity and reproducibility

Each feature gets a stable `id` based on its query specification:

```python
slice_id = sha256(json.dumps(query, sort_keys=True))
```

Query format enables exact reproduction:

```json
{
  "method": "bfs",
  "entrypoint": "fastapi_route:/api/login",
  "hops": 3,
  "max_files": 20,
  "exclude_tests": true
}
```

Feature comparison across commits: same query → compare `node_ids`/`edge_ids` to detect changes.

## 8) Safety + performance guardrails

### Exclude patterns

* `--exclude` supports gitignore-like globs
* MVP implementation: `fnmatch` (upgrade to `pathspec` later if needed)
* Default excludes:
  * `node_modules/`, `venv/`, `dist/`, `build/`
  * `*.min.js`, `*.bundle.js`
  * `.git/`, `__pycache__/`

### File size limits

* `--max-file-bytes` default: 2MB
* Especially important for HTML/minified JS
* Truncated files logged in `limits.truncated_files[]`

### Confidence calculation (deterministic algorithm)

**Evidence scoring** (MVP, stable contract)

Deterministic mapping from structured evidence → confidence score.

```python
# (language, evidence_type) → base_score
EVIDENCE_CONFIDENCE_MATRIX = {
    ("python", "ast_call_direct"): 0.95,
    ("python", "ast_call_method"): 0.85,
    ("python", "ast_getattr_call"): 0.60,
    ("python", "import_static"): 0.95,
    ("python", "import_dynamic"): 0.40,
    ("javascript", "import_static"): 0.95,
    ("javascript", "require_static"): 0.90,
    ("javascript", "require_dynamic"): 0.40,
    ("html", "script_src"): 0.80,
    ("html", "script_inline"): 0.70,
}

def calculate_evidence_confidence(
    lang: str, 
    evidence_type: str, 
    context: dict
) -> float:
    """
    Calculate confidence from evidence.

    Args:
        lang: Language (from edge.meta.evidence_lang or src.language)
        evidence_type: From edge.meta.evidence_type
        context: Additional flags (dynamic_dispatch, has_type_annotation, etc.)
    
    Returns:
        float in [0.0, 1.0]
    """
    base = EVIDENCE_CONFIDENCE_MATRIX.get((lang, evidence_type), 0.30)
    
    adjustments = 0.0
    if context.get("dynamic_dispatch"):
        adjustments -= 0.1
    if context.get("missing_types"):
        adjustments -= 0.05
    if context.get("has_type_annotation"):
        adjustments += 0.05
    
    return min(1.0, max(0.0, base + adjustments))
```

**Note**: Base scores are heuristic baselines (to be validated against benchmark suite). New evidence types can be added in minor versions.

### Caching
* Location: `.hypergumbo/cache/`
* Keying strategy:
  * File-level results: `(content_hash, run_signature)`
  * Full analysis outputs: `(repo_fingerprint, run_signature)` where `repo_fingerprint` changes when any analyzed file content changes (including dirty git files).
* File-level cache stores mapping: `file_path → content_hash → cached_result`
* Cache invalidation: if capsule version changes or repo_fingerprint changes
* Cache format: JSON (simple, debuggable)

### Deterministic ordering
* Stable sort of nodes/edges for reproducible diffs
* Sort keys:
  * Nodes: `(language, path, start_line, name)`
  * Edges: `(src, dst, type)`
* Enables meaningful `git diff` of output files

## 8.5) Cross-Language Edge Detection (MVP)

Spec A provides **best-effort cross-language edge detection** for common integration patterns. These are AST-based heuristics with string literal matching, not type-resolved or dataflow analysis.

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
3. Emit `native_bridge` edge from Java method → C function

**Confidence scoring:**
* Pattern-matched (naming convention): 0.80
* Annotation-confirmed (`@hypergumbo.jni_impl`): 0.95

**Limitations:**
* Does not resolve JNI calls through reflection
* Does not track `JNI_OnLoad` dynamic registration
* Does not handle inner classes (mangling includes `$`)
* Logs unmatched natives in `limits.unresolved_jni[]`

### IPC/Message Channel Detection

Detects message send/receive patterns across process boundaries using string literal matching on channel/event names.

**Supported patterns:**

| Framework | Send Pattern | Receive Pattern | Evidence Type |
|-----------|-------------|-----------------|---------------|
| Electron | `ipcRenderer.send("channel")` | `ipcMain.on("channel")` | `ipc_electron` |
| Electron | `ipcMain.handle("channel")` | `ipcRenderer.invoke("channel")` | `ipc_electron` |
| WebSocket | `ws.send({type: "X"})` | `ws.on("message", ...)` with type check | `ipc_websocket` |
| Guacamole | `tunnel.sendMessage("opcode", ...)` | `oninstruction` handlers | `ipc_guacamole` |
| Node EventEmitter | `emitter.emit("event")` | `emitter.on("event")` | `ipc_eventemitter` |

**Detection algorithm:**
1. Parse AST for known send/receive function patterns
2. Extract channel/event name from string literal argument
3. Build index of all senders and receivers by channel name
4. Match senders to receivers with same channel name
5. Emit `message_send` edge (caller → channel) and `message_receive` edge (channel → handler)

**Confidence scoring:**
* String literal channel name match: 0.85
* Variable/computed channel name: 0.50 (best-effort, name extracted if simple)
* Template literal with interpolation: 0.40 (partial match)
* Annotation-provided (`@hypergumbo.ipc_channel("name")`): 0.95

**Limitations:**
* Dynamic channel names require annotation hints
* Complex message routing (middleware, proxies) not traced
* Does not validate message schema compatibility
* Logs unmatched patterns in `limits.unresolved_ipc[]`

### HTTP Endpoint Detection (Server-side only)

Detects HTTP route definitions for entrypoint detection. Full client→server linking is deferred to Spec B1.

**Supported frameworks:**

| Framework | Pattern | Example |
|-----------|---------|---------|
| FastAPI | `@app.get("/path")` | `@app.get("/users/{id}")` |
| Flask | `@app.route("/path")` | `@app.route("/login", methods=["POST"])` |
| Express | `app.get("/path", handler)` | `router.post("/api/users", createUser)` |
| Java Servlet | `@WebServlet("/path")` | `@WebServlet("/api/session")` |
| JAX-RS | `@Path("/path")` | `@GET @Path("/users/{id}")` |
| Spring MVC | `@RequestMapping("/path")` | `@PostMapping("/api/login")` |

**Detection output:**
* Symbol kind: `route` or `endpoint`
* Symbol name: HTTP method + path (e.g., `GET /users/{id}`)
* Used by entrypoint detection for slicing

**Client-side linking (NOT in MVP):**
Cross-language client→server matching (e.g., `fetch("/api/users")` → Flask handler) is deferred to Spec B1 HTTP linker.

### Language-Specific Detection Notes

**C analyzer detects:**
* Functions, structs, typedefs, enums
* Function calls (direct calls only, not function pointers)
* `#include` edges (file → file)
* JNI export patterns (`JNIEXPORT`, `JNICALL`, `Java_*` naming)
* Macro definitions (as symbols, not expanded)

**Java analyzer detects:**
* Classes, interfaces, enums, annotations
* Methods, constructors, fields
* `implements` edges (class → interface)
* `extends` edges (class → superclass, interface → superinterface)
* `native` method declarations (for JNI linking)
* Annotation detection (`@Override`, `@Deprecated`, servlet/JAX-RS annotations)
* `instantiates` edges (constructor calls)

### limits.cross_language — tracking unresolved links

Cross-language linkers log unresolved patterns for debugging:

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

## 8.6) Supply Chain Classification

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
| 1 | `first_party` | Project's own source code | `src/`, `lib/`, `app/` | 🟩 |
| 2 | `internal_dep` | Internal libraries, monorepo packages | Workspace packages, local forks | 🟩 |
| 3 | `external_dep` | Third-party dependencies (readable form) | `node_modules/lodash/`, `vendor/` | 🟩 |
| 4 | `derived` | Build artifacts, transpiled/bundled output | `dist/`, `*.min.js`, source-mapped files | 🟩 |

**Default behavior:**
- Tiers 1-3: Analyzed, with tier used for ranking/filtering
- Tier 4: Excluded from analysis entirely (pure noise)

**Design principle:** Analyze the canonical source, skip derived artifacts. If both `src/app.ts` and `dist/app.js` exist, analyze the TypeScript (tier 1), skip the transpiled JavaScript (tier 4).

### Classification Algorithm

Classification happens at discovery time, before analysis. Signals are checked in order; first match wins.

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

Detected via workspace/monorepo configuration files.

**npm/yarn/pnpm workspaces:**
```json
// package.json
{
  "workspaces": ["packages/*", "apps/*"]
}
```
Files under matched workspace globs are tier 2.

**Cargo workspaces:**
```toml
# Cargo.toml
[workspace]
members = ["crates/*"]
```

**Python monorepos:**
```toml
# pyproject.toml (using hatch, pdm, or similar)
[tool.hatch.envs.default]
dependencies = ["./packages/core", "./packages/utils"]
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

### CLI Integration

#### Analysis scope flags

```bash
# Default: analyze tiers 1-3, skip tier 4 (derived)
hypergumbo run .

# First-party only (fast, focused)
hypergumbo run . --first-party-only
# Equivalent to: --max-tier 1

# Include readable external dependencies
hypergumbo run . --include-deps
# Equivalent to: --max-tier 3 (default)

# Analyze everything except derived (rarely needed)
hypergumbo run . --max-tier 3
```

#### Slice tier filtering

```bash
# Slice stops at first-party boundary
hypergumbo slice --entry main --max-tier 1

# Slice includes internal deps but not external
hypergumbo slice --entry main --max-tier 2

# Default: slice can traverse into external deps
hypergumbo slice --entry main --max-tier 3
```

#### Sketch prioritization

The `--first-party-priority` flag (default: true) applies tier-based weighting to Key Symbols ranking:

```bash
# Key Symbols prioritizes first-party (default)
hypergumbo sketch .

# Disable tier weighting (raw centrality)
hypergumbo sketch . --no-first-party-priority
```

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

#### Slicing behavior

When `--max-tier N` is specified, BFS traversal stops at tier boundaries:

```python
def should_traverse(edge: Edge, target: Symbol, max_tier: int) -> bool:
    if target.supply_chain.tier > max_tier:
        return False  # Don't cross into lower tier
    return True
```

**Use case:** "Show me everything my code calls, but don't trace into lodash internals."

### Capsule Plan Integration

Supply chain configuration can be customized in `capsule_plan.json`:

```json
{
  "supply_chain": {
    "analysis_tiers": [1, 2, 3],
    "first_party_patterns": ["src/", "lib/", "custom_code/"],
    "derived_patterns": ["dist/", "build/", "generated/"],
    "internal_package_roots": ["packages/core", "packages/shared"]
  }
}
```

**Fields:**
- `analysis_tiers`: Which tiers to include in analysis (default: [1, 2, 3])
- `first_party_patterns`: Additional patterns to classify as tier 1
- `derived_patterns`: Additional patterns to classify as tier 4
- `internal_package_roots`: Explicit internal package paths (supplements auto-detection)

### Limitations

**What supply chain classification does NOT do:**

1. **Resolve transitive dependencies**: Classification is based on file location, not the full dependency graph. A file in `node_modules/a/` that imports from `node_modules/b/` doesn't affect tier assignment.

2. **Detect vendored copies**: If you copy `lodash.js` into `src/utils/lodash.js`, it's classified as tier 1 (first-party). Use `derived_patterns` in capsule plan to exclude.

3. **Understand build pipelines**: Classification doesn't know that `dist/app.js` was built from `src/app.ts`. It relies on path conventions and content heuristics.

4. **Handle unconventional structures**: Projects with unusual layouts (e.g., source in root, deps in `lib/`) need capsule plan customization.

**Logged in limits:**
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

## 8.7) Entrypoint Detection

Entrypoint detection identifies HTTP handlers, CLI mains, background tasks, and other entry sources for slicing. Detection is **YAML-driven** via the framework patterns system.

### Architecture

```
ANALYZERS (pure language, no framework knowledge)
  → Capture symbols + rich metadata (decorators, base classes, parameters)
  → Capture UsageContext for call-based patterns (route registrations, etc.)

FRAMEWORK_PATTERNS (37 YAML files, configured by detected frameworks)
  → Match patterns against symbol metadata and usage contexts
  → Enrich symbols with concept metadata (route, task, model, etc.)

ENTRYPOINTS (semantic detection)
  → Query enriched metadata: if "route" in sym.concepts → Entry(kind="route")
  → High confidence (0.95) from semantic match
```

**Key insight:** Entry kinds (routes, tasks, commands) are framework-afforded concepts detected from symbol metadata, not file paths.

### Pattern Types

The framework pattern system supports multiple detection strategies:

| Pattern Type | Example Frameworks | Detection Method |
|--------------|-------------------|------------------|
| **Decorator-based** | FastAPI, Flask, NestJS, Spring Boot | Match `@app.get`, `@Controller` decorators |
| **Call-based** | Django, Express, Go Gin/Echo | Capture `path("/url", view)` via UsageContext |
| **DSL-based** | Rails, Sinatra, Phoenix | Parse `get '/path' do` blocks |
| **File-based** | Next.js, Nuxt | Infer routes from `pages/`, `app/` paths |

See [ADR-0003](adr/0003-architectural-analysis-and-revision-plan.md) for the design rationale and [UsageContext extension](adr/0003-usage-context-patterns.md) for call-based framework support.

### Scoring for Auto-Slice Entry Selection

When multiple entrypoints exist, scoring selects the most useful ones:

```python
score = confidence * (1 + log(1 + outgoing_edges))
```

This prefers well-connected entries, producing richer slices.

## 9) Testing & quality bar

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
- 🟩 Determinism: same input → same output

**Benefits:**
- No need to know "correct" answer upfront
- Tests remain valid as analysis improves
- Catches structural bugs (dangling references, invalid values)

### 🟪 Future: Longitudinal analysis ("slow thinking")

**Problem:** Property tests provide immediate pass/fail feedback ("fast thinking"). But some insights only emerge from patterns across many CI runs:
- Did node count suddenly drop 40%? (regression)
- Is edge detection improving over time? (progress)
- How does analysis time scale with repo size? (performance)

**Concept:** "Nonjudgmental fixtures"—run analysis on a real repo without asserting correctness, just observing metrics:

```python
def test_observatory(capsys):
    """Emit metrics for longitudinal analysis. No assertions on correctness."""
    result = analyze(Path("tests/fixtures/medium-repo"))
    print(json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "commit": os.environ.get("CI_COMMIT_SHA"),
        "nodes": len(result.nodes),
        "edges": len(result.edges),
        "nodes_by_kind": dict(Counter(n.kind for n in result.nodes)),
    }))
    assert validate_schema(result)  # Only hard check: didn't crash, valid schema
```

**Infrastructure needed (not MVP):**
- Persistent storage for metrics across CI runs
- Aggregation/visualization tooling
- Anomaly detection (alert on significant changes)

This is a fundamentally different paradigm than pytest's immediate feedback. Defer to future work.

### Unit tests
* 🟩 Parsing to nodes/edges (per language)
* 🟩 Stability of IDs across runs (same code → same IDs)
* 🟩 **ID survival across refactors**: `stable_id` unchanged when code renamed/moved (if signature unchanged)
* 🟩 ID collision handling (multiple definitions at same location)
* 🟩 Fingerprint changes when code changes
* 🟩 Slicing correctness (known entry → expected subgraph)
* 🟩 Exclude behavior (respects patterns)
* 🟩 Confidence calculation determinism
* 🟩 Provenance tracking (correct origin fields, execution_id/run_signature hashing)
* 🟩 IR → view compilation correctness
* 🟩 **Capsule Plan validation**: invalid plans rejected, valid plans execute correctly
* 🟩 **Catalog loading**: building blocks (packs) discovered and merged correctly, schema validation
* 🟩 Plan validation: unknown pass/pack rejected in strict mode
* 🟩 Pack schema validation: invalid pack.json rejected
* 🟩 LLM plan output must validate or fall back to template
### Schema validation tests
* 🟩 Output validates against published JSON Schema
* 🟩 Forward compatibility: v0.1 output readable by v0.2+ (if backward compatible)
* 🟩 Required field presence (execution_id, run_signature, evidence_lang, evidence_spans)
* 🟩 ID format conformance (both `id` and `stable_id` when present)
* 🟩 Evidence type presence in all edges
* 🟩 Toolchain capture in analysis_runs
### Smoke test
* 🟩 `hypergumbo init` then `hypergumbo run` on a fixture repo
* 🟩 Yields valid JSON schema
* 🟩 All expected nodes/edges present
* 🟩 No crashes, warnings logged appropriately
* 🟩 `hypergumbo catalog` displays building blocks
### Performance benchmarks
* 🟩 Small repo (<100 files): <5 seconds end-to-end
* 🟩 Medium repo (~500 files): <30 seconds
* ⬜ Caching: second run on unchanged repo <2 seconds

## 9.5) Error handling

### Parse errors

* 🟩 **Behavior**: Log warning, skip file, continue analysis
* 🟩 **Output**: Add to `limits.failed_files[]`:
  ```json
  {
    "path": "malformed.py",
    "reason": "SyntaxError: invalid syntax (line 42)",
    "analyzer": "python-ast-v1"
  }
  ```

### Circular imports

* 🟩 **Behavior**: Detect cycle, log warning, break at arbitrary point
* 🟩 **Output**: Add to `warnings[]` in `analysis_runs[]`

### Missing dependencies

* 🟩 **Behavior**: If pass requires unavailable grammar (e.g., tree-sitter), skip pass
* 🟩 **Output**: Add to `analysis_runs[].skipped_passes[]`:
  ```json
  {
    "pass": "lean-ts-v1",
    "reason": "tree-sitter-lean grammar not available"
  }
  ```

### Analyzer crashes

* 🟩 **Behavior**: Catch exception, log stack trace to `.hypergumbo/error.log`, continue
* 🟩 **Output**: Set `analysis_incomplete: true` in top-level, add to `warnings[]`

### Partial results guarantee

* 🟩 **All output is valid JSON** even if analysis is incomplete
* 🟩 `analysis_incomplete: true` flag signals partial results
* 🟩 `limits.partial_results_reason` documents what went wrong
* 🟩 Agents can decide whether partial results are sufficient

## 9.6) Known Analysis Limitations

This section documents cross-cutting limitations that affect symbol resolution and edge detection across multiple language analyzers. See `CHANGELOG.md` for per-language implementation status.

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

> **Historical note:** The original v1.0 development milestones (Week 0-9 planning) have been archived to [docs/history/planning-v1.md](history/planning-v1.md).

## 10) Known limitations and risks

| Limitation | Impact | Notes |
|------------|--------|-------|
| Best-effort analysis | Medium | AST-based analysis cannot resolve all calls (dynamic dispatch, reflection, eval). Confidence scores communicate uncertainty; machine-readable evidence types enable transparency. |
| ID collisions in edge cases | Low | Location-based IDs can collide for identically-named symbols at same line. Content hash appended if collision detected. |
| Confidence scores are heuristics | Medium | Scores are calibrated heuristics, not ground truth. Evidence types show reasoning; `--confidence-threshold` allows filtering. |
| Schema changes may break consumers | Medium | Semantic versioning from day 1; forward compatibility contract in Appendix E; migration guides for breaking changes. |
| stable_id limited in untyped code | Low | Without type annotations, stable_id uses arity-based hashing which may change on signature changes. shape_id provides structural alternative. |
| Re-export resolution incomplete | Low | Imports through re-exporting modules (e.g., `from package import x` where x is re-exported) may not fully resolve. See §9.6. |

> **Historical note:** Original success criteria and validation gates have been archived to [docs/history/validation-gates-v1.md](history/validation-gates-v1.md). Spec B work will be pursued when there's clear demand for capabilities beyond what Spec A provides.

## Appendix A: Example output

Minimal working example for a tiny FastAPI app:

```json
{
  "schema_version": "0.1.0",
  "confidence_model": "hypergumbo-evidence-v1",
  "view": "behavior_map",
  "generated_at": "2024-01-15T10:30:00Z",
  "analysis_incomplete": false,
  "analysis_runs": [
    {
      "execution_id": "uuid:abc-def-789...",
      "run_signature": "sha256:run1abc...",
      "repo_fingerprint": "sha256:repo123...",
      "pass": "python-ast-v1",
      "version": "hypergumbo-0.1.0",
      "toolchain": {"name": "python", "version": "3.11.0"},
      "config_fingerprint": "sha256:abc123...",
      "files_analyzed": 3,
      "files_skipped": 0,
      "skipped_passes": [],
      "warnings": [],
      "started_at": "2024-01-15T10:30:00Z",
      "duration_ms": 450
    }
  ],
  "profile": {
    "languages": {"python": {"files": 3, "loc": 120}},
    "frameworks": ["fastapi"],
    "repo_kind": "web_api"
  },
  "nodes": [
    {
      "id": "python:main.py:1-50:main:module",
      "stable_id": "sha256:main_module_hash...",
      "canonical_name": "main",
      "fingerprint": "sha256:abc...",
      "kind": "module",
      "name": "main",
      "path": "main.py",
      "language": "python",
      "span": {"start_line": 1, "end_line": 50},
      "origin": "python-ast-v1",
      "origin_run_id": "uuid:abc-def-789...",
      "quality": {"score": 1.0, "reason": "module definition"}
    },
    {
      "id": "python:main.py:10-15:get_user:function",
      "stable_id": "sha256:get_user_sig_hash...",
      "canonical_name": "main.get_user",
      "fingerprint": "sha256:def...",
      "kind": "endpoint",
      "name": "get_user",
      "path": "main.py",
      "language": "python",
      "span": {"start_line": 10, "end_line": 15},
      "origin": "python-ast-v1",
      "origin_run_id": "uuid:abc-def-789...",
      "quality": {"score": 0.95, "reason": "FastAPI route decorator detected"}
    }
  ],
  "edges": [
    {
      "id": "edge:sha256:call1...",
      "type": "calls",
      "src": "python:main.py:10-15:get_user:function",
      "dst": "python:db.py:5-10:query_user:function",
      "confidence": 0.90,
      "origin": "python-ast-v1",
      "origin_run_id": "uuid:abc-def-789...",
      "quality": {"score": 0.90, "reason": "Direct AST call"},
      "meta": {
        "evidence_type": "ast_call_direct",
        "evidence_lang": "python",
        "evidence_spans": [
          {
            "file": "main.py",
            "span": {"start_line": 12, "end_line": 12, "start_col": 8, "end_col": 24}
          }
        ]
      }
    }
  ],
  "features": [
    {
      "id": "sha256:feature1...",
      "name": "get-user-flow",
      "entry_nodes": ["python:main.py:10-15:get_user:function"],
      "node_ids": ["python:main.py:10-15:get_user:function", "python:db.py:5-10:query_user:function"],
      "edge_ids": ["edge:sha256:call1..."],
      "query": {
        "method": "bfs",
        "entrypoint": "fastapi_route:/user/{id}",
        "hops": 2,
        "max_files": 10
      },
      "limits_hit": []
    }
  ],
  "metrics": {
    "total_nodes": 2,
    "total_edges": 1,
    "avg_confidence": 0.90
  },
  "limits": {
    "not_captured": ["dynamic imports"],
    "truncated_files": [],
    "skipped_languages": [],
    "failed_files": [],
    "partial_results_reason": "",
    "analyzer_version": "hypergumbo-0.1.0",
    "capsule_version": "sha256:template-v1...",
    "analysis_depth": "syntax_only"
  }
}
```

## Appendix B: Evolution path to future versions

Spec A is designed to enable future enhancements without breaking changes:

### What's future-proof

* **Internal IR**: Strong analyzers (tsserver, pyright) can enhance the IR
* **View system**: New views (`ir_export`, `context_bundle`) can be added
* **Capsule manifest**: `format` field supports `toolchain_bundle`, `container`, `daemon` modes
* **Provenance**: Already tracks which pass created which nodes/edges via execution_id
* **Versioned schema**: Room for v0.2, v0.3 with migration paths

### What stays the same

* `behavior_map.json` format (backward compatible)
* Location-based node IDs (stable anchor)
* Confidence/quality model (extensible via versioning)
* Slicing primitives (features with query specs)

### What enables future capabilities

**stable_id**:
- Cross-refactor tracking (incremental analysis)
- Symbol identity when code moves (impact zones)

**shape_id**:
- Detect structural changes independent of signature
- Implementation similarity analysis

**evidence_type + confidence layering**:
- Mixed-fidelity graphs (AST edges + typed edges in same IR)
- Analyzer benchmarking (precision by evidence type)
- Agent filtering (show only high-confidence edges)

**Security manifest (trust, network, sandbox)**:
- Future registry can enforce sandboxing for untrusted capsules
- Gradual trust model (local → shared → signed)

**Pass interface**:
- Multi-pass engine extends the registry
- Typed analyzers (tsserver, pyright) are just new passes

**Toolchain capture**:
- Reproducibility requirements
- Registry fingerprinting (which tool versions were used)

**Machine-readable provenance**:
- Critical for merging edges from multiple analyzers
- Enables programmatic quality assessment
- Foundation for context router filtering

**Capsule Plan composition**:
- Enables vast combinatorial space from small building blocks
- LLM-assisted or template-based generation
- Safe (generates data, not code)

### What upgrades in future versions
* Multiple execution formats (not just `python_script`)
* Mixed-fidelity graphs (AST edges + typed edges)
* Cross-language linkers (HTTP, IPC, SQL)
* Context router (agent-optimized bundles)
* Registry (sharing capsules + benchmarks)

## Appendix C: Versioning & Support Policy

### Semantic versioning

* **Schema versions**: `MAJOR.MINOR.PATCH`
  - MAJOR: Breaking changes (old outputs unreadable)
  - MINOR: Backward-compatible additions (new fields, new views)
  - PATCH: Bug fixes, no schema changes
* **Confidence model versions**: `hypergumbo-evidence-vMAJOR.MINOR`
  - MAJOR: Incompatible changes (requires new schema)
  - MINOR: Refinements (new evidence types, score adjustments)
* **Capsule format versions**: Independent, declared in `capsule.json.format_version`

### Compatibility guarantees

* **v0.1 outputs readable by v0.2+** if v0.2 is backward-compatible (MINOR bump)
* **v1.0 outputs readable by v1.x** for all v1.x (MAJOR version promises stability)
* **Breaking changes only in MAJOR bumps** with 6-month migration period

### Support windows

* **Current version**: Full support (bugs, features, security)
* **Previous MINOR**: Security fixes only, 12 months after next MINOR release
* **Previous MAJOR**: Security fixes only, 18 months after next MAJOR release
* **Unmaintained**: Versions >18 months old receive no updates

### Deprecation process

1. **Announce**: 6 months before removal, add deprecation warnings
2. **Document**: Migration guide published
3. **Support**: Old version maintained per support windows
4. **Remove**: After support window expires

### Example timeline

* 2024-01: v0.1.0 ships
* 2024-06: v0.2.0 ships (backward-compatible)
  - v0.1 enters "previous minor" (security only)
* 2025-01: v1.0.0 ships (breaking changes)
  - v0.1 unsupported (>12 months old)
  - v0.2 enters "previous major" (18-month clock starts)
* 2026-07: v0.2 unsupported (18 months after v1.0)

## Appendix D: Telemetry & Privacy

### Default: Zero telemetry

By default, hypergumbo **sends no data** anywhere. All analysis is local-only.

### Opt-in crash reporting

Enable via `hypergumbo config --telemetry=on` or `hypergumbo_TELEMETRY=1` environment variable.

**What is collected** (only if opted in):
* Crash stack traces (sanitized: no code, no symbol names, no file paths)
* Performance metrics (file counts, timings, memory usage)
* Feature usage (which commands run, which flags used)
* Anonymized session ID (random UUID, not linked to identity)

**What is NEVER collected**:
* Source code
* Symbol names (function/class/variable names)
* File paths or directory names
* API keys or credentials
* IP addresses (beyond what HTTPS inherently reveals)

### Data retention

* Crash reports: 90 days
* Aggregated metrics: 2 years
* No raw session data retained beyond 30 days

### Third-party services
* If enabled, telemetry sent to Sentry (crash reporting) or similar
* Subject to their privacy policies (links provided in docs)

### Transparency
* Telemetry code is open source (can audit exactly what's sent)
* Privacy policy published at https://hypergumbo.iterabloom.com/privacy
* Opt-in status shown in `hypergumbo config --show`

### LLM API usage
If using `hypergumbo init --assistant llm`:
* API key required (OpenAI or compatible)
* Repo profile sent to LLM (language stats, framework signals)
* **No source code sent** (only metadata)
* Subject to LLM provider's terms
* Can use local LLM (ollama, etc.) to avoid external API

## Appendix E: Forward Compatibility Contract
This contract ensures Spec A outputs remain valid when future capabilities are added, and enhancements degrade gracefully for Spec A consumers.

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
- `nodes[].origin`: String, pass ID that created this node
- `nodes[].origin_run_id`: String, references `analysis_runs[].execution_id`
- `edges[].origin`, `edges[].origin_run_id`: Same semantics
- `analysis_runs[].run_signature`: Deterministic fingerprint of pass configuration

**4. Confidence scoring:**
- `confidence_model` field identifies the scoring algorithm (`hypergumbo-evidence-v1`)
- Consumers MUST default unknown `evidence_type` to 0.30

### Extensible Contracts (can add in minor versions)

**1. Unknown field tolerance:**
- Consumers MUST ignore unknown top-level fields
- Consumers MUST ignore unknown fields in `nodes[]`, `edges[]`, `features[]`, `analysis_runs[]`
- Consumers MUST ignore unknown keys under `meta.*`
- Reason: Allows future additions like `meta.resolution_confidence`, `nodes[].shape_id`, etc.

**2. Optional fields:**
- Any field marked "optional" can be absent
- Consumers MUST handle absence gracefully (default value or skip)
- Examples: `stable_id`, `shape_id`, `origin_run_signature`
**Key presence vs. value presence (v0.1.0 rule):**
- Some fields may be semantically optional but SHOULD be present as keys with `null` values to reduce consumer branching.
- For v0.1.0, producers SHOULD include these keys with `null` when unknown:
  - `nodes[].stable_id`
  - `nodes[].shape_id`
  - `nodes[].origin_run_signature`
- Consumers MUST accept either form (missing key OR null), but producers prefer `null` keys for stability.

**3. View types:**
- New view types can be added: `ir_export`, `context_bundle`, `sarif`
- `view: "behavior_map"` schema remains stable across v0.x, v1.x
- Consumers check `view` field, skip unknown views

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

### Future Additions (examples of backward-compatible changes)

**Possible in future minor versions:**
- `run_signature` refinements (additional hash inputs)
- `stable_id` improvements (better heuristics for untyped code)
- `meta.resolution_confidence` (optional, from type checkers)
- `ir_export.json` view (new view type)
- New `evidence_type` values
- New `kind` values for nodes

### Testing Requirements

**Spec A test suite MUST:**
- Include golden file regression tests (fixtures → expected JSON)
- Validate against JSON Schema (automated validation)
- Test ID stability (same code → same IDs deterministically)
- Test deterministic ordering (sort keys defined, reproducible output)

**Future test suite MUST:**
- Run Spec A golden files (backward compatibility check)
- Ensure Spec A outputs pass future schema validation (with unknown field tolerance)
- Test mixed-fidelity graphs (Spec A AST edges + future typed edges coexist)
- Test view compilation (same IR → multiple views including behavior_map)

### Migration Path (Spec A → Future)

**User upgrades hypergumbo CLI:**
```bash
# Was using Spec A v0.1.0
pip install --upgrade hypergumbo  # Now future version

# Old capsule still works
hypergumbo run  # Executes existing capsule_plan.json, output compatible

# Optionally regenerate capsule to use new analyzers
hypergumbo init --upgrade  # Gets new capabilities
```

**User upgrades output consumers (agents, tooling):**
1. Agents consuming `behavior_map.json` don't need changes
2. New fields under `meta.*` are optionally used (if agent wants higher fidelity)
3. Schema validation passes (new fields ignored by old consumers)
4. Agents can check `confidence_model` version, warn if too new

**Deprecation process (if ever needed):**
1. Announce 6 months before removal
2. Add deprecation warnings to CLI output
3. Maintain old version per support policy (Appendix C)
4. Provide migration guide
5. Remove only after support window expires

### Compatibility Testing

**Before releasing future versions:**
- Run Spec A v0.1 output through future parsers (ensure parsing succeeds)
- Run future output through Spec A v0.1 consumers (ensure unknown fields ignored)
- Version compatibility matrix published

**Commitment:** No breaking changes to `behavior_map.json` view within v0.x series.

---

# 🟪 Spec B — "Multi-Fidelity Analysis Platform"

## 0) One-sentence summary

A multi-fidelity code understanding platform that produces typed IR and an agent context router for token-efficient editing.

*All of Spec B is future work. Pursue when there's clear demand for capabilities beyond Spec A.*

## 1) Objectives

* 🟪 **High-fidelity IR** — Typed call graphs via language servers (tsserver, pyright, gopls, rust-analyzer)
* 🟪 **Agent context router** — "Give me the smallest set of code + invariants to safely edit X"
* 🟪 **Local-first privacy** — All analysis runs locally, no data leaves machine

See [Registry & Factory Vision](future/registry-factory-vision.md) for speculative ideas about sharing analyzers.

## 2) Non-goals

* "Prove programs correct" in the formal methods sense
* Full support for every language; focus on dominant stacks with pluggable packs
* Real-time collaboration or social features
* Hosted SaaS offering or marketplace monetization
* IDE integration (LSP server) or autonomous code editing

## 3) System architecture

### 3.1 Multi-pass analysis engine

#### Frontends (parsers / symbolizers)

* 🟩 **tree-sitter** for universal syntax (Spec A, 67 languages)
* 🟪 **Language-native engines** (optional, high-fidelity):
  * **TypeScript**: `tsserver` (type checker + language service)
  * **Python**: `pyright` or `mypy` (type inference)
  * **Rust**: `rust-analyzer` (full semantic analysis)
  * **Go**: `gopls` (language server)
  * **JVM**: Eclipse JDT (Java), Kotlin analysis tooling

#### Runner types

Different analyzers require different execution environments:

* 🟩 **`python_script`** — Single Python file, minimal deps (Spec A)
* 🟪 **`toolchain_bundle`** — Ships with language server (100MB+ downloads, high fidelity)
* 🟪 **`container_image`** — OCI/Docker image for maximum isolation
* 🟪 **`daemon_process`** — Long-running incremental analysis (research-hard, defer indefinitely)

#### IR builder

* **Typed symbol table** + cross-ref index (extends Spec A IR)
* **Call graph** with resolution quality scores (0.0–1.0)
* **Optional layers**:
  * Control-flow graph (CFG)
  * Dataflow facts (reaching definitions, taint tracking)

Dataflow/CFG are opt-in with explicit partial-results flags, not core requirements.

#### IR vs Views architecture

```
┌─────────────────────────────────────────┐
│  Language-specific analyzers            │
│  (AST parsers, type engines, LSPs)      │
└──────────────┬──────────────────────────┘
               │ emit to
               ▼
┌─────────────────────────────────────────┐
│  Core IR (internal, versioned)          │
│  • typed symbol table                   │
│  • resolved call graph + quality scores │
│  • dataflow facts (optional)            │
│  • cross-language links                 │
└──────────────┬──────────────────────────┘
               │ compile to
               ▼
┌─────────────────────────────────────────┐
│  Views (public, stable contracts)       │
│  • behavior_map.json (Spec A compat)    │
│  • ir_export.json (full detail)         │
│  • context_bundle.json (agent-ready)    │
│  • sarif.json (CI integration)          │
└─────────────────────────────────────────┘
```

Mixed-fidelity analysis: AST edges (0.7 confidence) + typed edges (0.95 confidence) in same graph.

#### Cross-language linkers

Already implemented in Spec A:
* 🟩 **HTTP**: route patterns ↔ client calls ↔ handlers
* 🟩 **IPC (Electron)**: main ↔ renderer message channels
* 🟩 **SQL**: query ↔ table/column mapping
* 🟩 **Protobuf/gRPC**: service defs ↔ server impl ↔ client stubs
* 🟩 **GraphQL**: schema ↔ resolvers ↔ clients
* 🟩 **OpenAPI**: schema ↔ route handlers
* 🟩 **Message Queue**: Kafka, RabbitMQ, SQS, Redis Pub/Sub
* 🟩 **Event Sourcing**: EventEmitter, Django signals, Spring events

Future linkers (if needed):
* 🟪 **Constant propagation** for dynamic routes (`BASE_URL + "/users"`)
* 🟪 **Middleware/proxy rewriting** detection

### 3.2 Agent Context Router

Query interface: "I want to change behavior X in Y context"

#### Pipeline

1. **Retrieve** relevant nodes/flows from IR
   * Entry: symbol name, file path, route pattern
   * 🟪 Future: natural language queries via embedding similarity

2. **Slice** on:
   * Call graph (forward/backward) — 🟩 done in Spec A
   * 🟪 Dataflow (tainted data paths)
   * 🟪 Schema ties (database columns, API contracts)
   * Tests referencing the area — 🟩 done in Spec A
   * 🟪 Configuration/deployment ties
   * Supply chain tier boundaries — 🟩 done in Spec A

3. **Assemble context bundle**:
   * Minimal code excerpts (only changed + affected)
   * Invariants/contracts (types, tests, assertions)
   * 🟪 "What could break" checklist (requires whole-program analysis)

Token budget optimization: BFS until token limit hit, sorted by (edge confidence, distance from entry).

#### Future enhancements

* 🟪 Learned relevance models
* 🟪 Agent feedback loop (which code was actually edited?)
* 🟪 Embedding-based context expansion
* 🟪 Coverage report parsing for test summary
* 🟪 Multi-language documentation summarization

#### Complexity acknowledgment

* **Natural language query parsing** requires embedding models + semantic search
* **Dataflow slicing** is NP-hard in general; even heuristic solutions are multi-month research
* **Impact prediction** requires whole-program analysis with test coverage correlation

Honest about what's hard. If dataflow proves infeasible, agent-guided slicing (agents specify hops/filters via DSL) is a simpler alternative.

## 4) Output contracts

### Views

#### 🟩 behavior_map.json (Spec A compatible)
* Same schema as Spec A
* Maintained for backward compatibility
* Enhanced with higher-fidelity edges when available

#### 🟪 ir_export.json (full detail)
* Complete symbol table
* Typed edges with resolution provenance
* Dataflow facts (optional)
* Cross-language links

#### 🟪 context_bundle.json (agent-optimized)
* Minimal code excerpts for a query
* Invariant checklist
* "Impact zones" (what could break)
* Token-budget optimized

#### 🟪 sarif.json (CI integration)
* SARIF 2.1 compatible
* Findings from rule packs
* Integration with GitHub Code Scanning, GitLab SAST

### Flow specs

Named, traceable feature flows:
* "Signup pipeline": route → handler → validation → database → email notification
* "Payment settlement flow": API call → queue → worker → payment gateway → webhook

Each flow includes entry/exit points, all nodes/edges in path, invariants, and tests covering the flow.

## 5) Privacy & security

* All analysis runs locally
* No data leaves machine
* No network calls during analysis

## 6) Scale & performance

### Incremental analysis: Not on roadmap

**Why it's hard** (18+ months minimum):
- Dependency tracking (which symbols affect which)
- Invalidation propagation (change X → re-analyze Y, Z)
- Cross-file type inference updates

TypeScript incremental took ~3 years, rust-analyzer ~2 years.

**Current mitigation**:
- Cached slices: Pre-compute common features
- Symbol index: O(1) lookup for "find definition of X"
- Partial re-analysis: Re-analyze changed files + direct importers only
- Accept latency: Full analysis on deep queries is OK with progress bars

### Performance targets

* **Small repo** (<100 files): <10 seconds (same as Spec A)
* **Medium repo** (~500 files): <60 seconds (2× Spec A due to type checking)
* **Large repo** (2000+ files): <5 minutes full analysis
* **Context router query**: <2 seconds for typical slice assembly

## Appendix A: Technology choices

### IR storage
* **Format**: Protocol Buffers (fast, versioned, language-neutral)
* **Fallback**: JSON (if protobuf adds friction)

### Language servers
* **TypeScript**: `tsserver` (official)
* **Python**: `pyright` (Microsoft)
* **Go**: `gopls` (official)
* **Rust**: `rust-analyzer` (official)
* **Java**: Eclipse JDT

## Appendix B: Future Testing Enhancements

### LLM Integration Tests

Add optional integration tests that make real API calls to LLM providers to validate the `llm_assist` module end-to-end:

* Use `@pytest.mark.integration` marker
* Skip automatically when API keys are not set
* Run only on explicit request (`pytest -m integration`)
* Catch environment-specific issues (proxy configuration, API changes)

## Appendix C: Planned Language/DSL Support

Languages and DSLs identified as gaps from industry analysis.

### High Priority (Build-from-source)

| Language | Use Case | Grammar Source |
|----------|----------|----------------|
| **Meson** | Build system (GNOME, QEMU) | tree-sitter-meson |
| **Assembly** | Performance-critical code | tree-sitter-asm |

### Medium Priority (Specialized ecosystems)

| Language/DSL | Use Case |
|--------------|----------|
| **Rego** | OPA/Gatekeeper policy-as-code |
| **Device Tree (DTS)** | Linux kernel hardware descriptions |
| **Kconfig** | Linux kernel configuration |

### Not Planned

| Format | Reason |
|--------|--------|
| **Markdown/RST** | Documentation, not code |
| **Plain text specs** | Not executable code |
