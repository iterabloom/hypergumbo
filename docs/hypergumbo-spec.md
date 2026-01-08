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
* `pipx install hypergumbo` (primary)
* `pip install hypergumbo` (secondary)
* `pip install hypergumbo[javascript]` (optional JS/TS/Svelte support via tree-sitter)
* `pip install hypergumbo[php]` (optional PHP support via tree-sitter)
* `pip install hypergumbo[c]` (optional C support via tree-sitter)
* `pip install hypergumbo[java]` (optional Java support via tree-sitter)
* `pip install hypergumbo[llm-assist]` (optional OpenAI/OpenRouter support for plan generation)
* `pip install hypergumbo[llm-local]` (optional local LLM support via llm package)
* `pip install hypergumbo-pack-go` / `hypergumbo-pack-rust` (future language packs)

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
  - python-ast-v1 (core): Python AST parser
  - html-pattern-v1 (core): HTML script tag parser  
  - javascript-ts-v1 (extra: hypergumbo[javascript]): JS/TS via tree-sitter

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

### Key principle
Initialization may use language detection; **analysis execution requires no network or API keys** (by default). The capsule should be deterministic and reproducible given the same repo state.

## 4) Supported stacks (MVP)
* 🟩 **Python** (best-effort call edges via `ast`)
* 🟩 **JS/TS** (best-effort parsing via tree-sitter; **optional dependency**: `pip install hypergumbo[javascript]`)
* 🟩 **Svelte** (extracts `<script>` blocks and analyzes as JS/TS with line number adjustment; **optional dependency**: `pip install hypergumbo[javascript]`)
* 🟩 **PHP** (best-effort parsing via tree-sitter; **optional dependency**: `pip install hypergumbo[php]`)
* 🟩 **C** (best-effort parsing via tree-sitter; **optional dependency**: `pip install hypergumbo[c]`)
* 🟩 **Java** (best-effort parsing via tree-sitter; **optional dependency**: `pip install hypergumbo[java]`)
* 🟩 **HTML** (script tag/linking edges only; always available, limited to pattern matching)
> The analyzer is "best-effort, explicitly limited," but produces consistent structures.

### Dependency strategy
* **Core package**: Python + HTML (AST + regex only, zero compilation)
* **JavaScript extra**: `pip install hypergumbo[javascript]` adds tree-sitter runtime + JS/TS/Svelte grammar (pre-built wheels for Linux x64, macOS arm64/x64, Windows x64)
* **PHP extra**: `pip install hypergumbo[php]` adds tree-sitter PHP grammar
* **C extra**: `pip install hypergumbo[c]` adds tree-sitter C grammar
* **Java extra**: `pip install hypergumbo[java]` adds tree-sitter Java grammar
* Core includes Python/HTML analyzers.
* Optional: tree-sitter runtime for JS/TS/Svelte, PHP, C, and Java.
* Optional: installable **language packs** for future languages (e.g., `pip install hypergumbo-pack-go`, `hypergumbo-pack-rust`). Language packs are data-only packages containing queries + metadata with minimal code.
* **Spec B consideration**: Some tree-sitter grammars exist on GitHub but lack PyPI packages (e.g., Lean, LaTeX, COBOL). Contributing these to PyPI or building a "parser backend" abstraction layer could enable support for formal proof languages, academic documentation, and legacy enterprise systems.
* Fallback: if extras/packs aren't installed, those languages are skipped with explicit limits.
* **Fallback**: If extras unavailable, analysis uses only core languages (Python/HTML); other files logged in `limits.skipped_languages[]`
### De-risking strategy
* **Week 0a (Days 1-5)**: Validate tree-sitter packaging on target platforms, prototype Capsule Plan validation
* **Week 0b (Days 6-10)**: Test LLM plan generation if including in MVP, integration checks
* **Gate Week 1**: Only proceed if tree-sitter wheels build successfully on 3/4 platforms or fallback degradation path is acceptable
* **Contingency**: If tree-sitter proves problematic, ship Python-only MVP and defer JS/TS to v0.1.1

## 5) Architecture (local-only)
### Core packages
* `hypergumbo/cli.py` — CLI entrypoint and command handlers
* `hypergumbo/profile.py` — language/framework detection (cheap heuristics)
* `hypergumbo/ir.py` — Internal representation: Symbol, Edge, Span, AnalysisRun classes
* `hypergumbo/schema.py` — JSON schema versioning and behavior map factory
* `hypergumbo/discovery.py` — file finding with exclude patterns
* `hypergumbo/catalog.py` — Pass/Pack registry, availability checking
* `hypergumbo/plan.py` — `capsule_plan.json` schema + validator + generator
* `hypergumbo/llm_assist.py` — optional LLM-assisted plan generator (OpenRouter, OpenAI, local llm)
* `hypergumbo/sketch.py` — token-budgeted Markdown summary generation
* `hypergumbo/entrypoints.py` — entry point detection heuristics (routes, CLI, Electron)
* `hypergumbo/slice.py` — graph slicing for context extraction
* `hypergumbo/metrics.py` — analysis statistics computation
* `hypergumbo/limits.py` — error tracking and analysis gaps
* `hypergumbo/export.py` — privacy-safe capsule export
* `hypergumbo/supply_chain.py` — file classification by dependency position (tier 1-4)
* `hypergumbo/analyze/py.py` — Python AST parser
* `hypergumbo/analyze/js_ts.py` — JS/TS/Svelte parser via tree-sitter
* `hypergumbo/analyze/php.py` — PHP parser via tree-sitter
* `hypergumbo/analyze/c.py` — C parser via tree-sitter
* `hypergumbo/analyze/java.py` — Java parser via tree-sitter
* `hypergumbo/analyze/html.py` — HTML script tag parser
* `hypergumbo/linkers/jni.py` — JNI boundary detection (Java↔C)
* `hypergumbo/linkers/ipc.py` — IPC message channel detection

### Runner interface (execution by capsule format)
hypergumbo executes capsules through a runner abstraction selected by `capsule.json.format`.

Pseudo-interface:
```python
class CapsuleRunner(Protocol):
    def run(self, capsule_manifest: dict, repo_root: Path, out_path: Path) -> None:
        ...
```

### IR Layer (internal, not exported in v0.1)
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

**MVP ships 9 language passes:**
* 🟩 `python-ast-v1` — Python AST parser
* 🟩 `javascript-ts-v1` — Tree-sitter JS/TS/Svelte/Vue (optional)
* 🟩 `php-ts-v1` — Tree-sitter PHP (optional)
* 🟩 `c-ts-v1` — Tree-sitter C (optional)
* 🟩 `java-ts-v1` — Tree-sitter Java (optional)
* 🟩 `elixir-ts-v1` — Tree-sitter Elixir (optional)
* 🟩 `rust-ts-v1` — Tree-sitter Rust (optional)
* 🟩 `html-pattern-v1` — HTML script tag parser

**MVP ships 2 cross-language linkers:**
* 🟩 `jni-linker-v1` — Java↔C native method matching
* 🟩 `ipc-linker-v1` — Message channel matching:
  - Electron IPC (`ipcRenderer.send/invoke`, `ipcMain.on/handle`)
  - Socket.io (`socket.emit`, `socket.on`, `io.emit`)
  - Phoenix Channels (`broadcast!`, `push`, `handle_in`)
  - Web Workers (`postMessage`, `onmessage`)

**Design principle:** Future language expansion happens via **packs** (installable packages like `hypergumbo-pack-go`).

### Analyzer capsule

`.hypergumbo/` structure:

#### `capsule.json` — Manifest
Declares execution requirements and security policy:
```json
{
  "format": "python_script",
  "version": "0.1.0",
  "validation_mode": "strict",
  "requires": {
    "runtime": "python>=3.10",
    "toolchains": [],
    "hypergumbo_schema": "0.1.0"
  },
  "entrypoint": "analyzer.py",
  "args": ["run", "--plan", "capsule_plan.json"],
  "inputs": {
    "repo_root": "${REPO_ROOT}",
    "plan_path": "capsule_plan.json",
    "config_path": ".hypergumbo/config.json"
  },
  "outputs": [
    {"path": "hypergumbo.results.json", "view": "behavior_map"}
  ],
  "resources": {
    "cpu_seconds": 300,
    "memory_mb": 2048,
    "disk_mb": 500
  },
  "deterministic": true,
  "trust": "local_only",
  "network": "deny",
  "sandbox": "recommended",
  "generator": {
    "mode": "template",
    "version": "hypergumbo-0.1.0",
    "plan_hash": "sha256:abc123..."
  }
}
```

**New manifest fields**:
- `entrypoint`, `args`: How to invoke the analyzer
- `inputs`: Expected input paths/variables
- `outputs`: What files are produced and their view types
- `resources`: Execution limits (for sandboxing)
- `validation_mode`: How to handle unknown passes/packs
- `generator`: Provenance of how this capsule was created
  - `mode`: `"template"` (default), `"llm_assisted"`, or `"manual"`
  - `version`: hypergumbo version that created it
  - `plan_hash`: Fingerprint of capsule_plan.json
  - `model`: (optional) LLM model if mode=llm_assisted
  - `prompt_hash`: (optional) Hash of prompt used

**Format types** (MVP implements `python_script` only):
- 🟩 `python_script` — Single file, minimal deps (MVP)
- 🟪 `toolchain_bundle` — Bundled with language server (future)
- 🟪 `container` — Docker/OCI image (future)
- 🟪 `daemon` — Long-running process (future)

**Runner dispatch contract (v0.1.0):**
- The hypergumbo CLI MUST select the execution runner based on `capsule.json.format`.
- v0.1.0 implements:
  - `python_script`: executed via the subprocess runner (see Security/Enforcement notes)
- Future formats (`toolchain_bundle`, `container`, `daemon`) will be additional runner implementations behind the same dispatch interface.

**Security fields**:
- `trust`: `"local_only"` (default), `"shared_unsigned"`, `"signed"` (future)
- `network`: `"deny"` (default), `"allow"` (requires explicit opt-in)
- `sandbox`: `"none"`, `"recommended"` (default), `"required"` (future)

**validation_mode** (optional, default: `"strict"`):
- `"strict"`: Unknown passes/packs in capsule_plan.json result in error. Only components in catalog.json can be used. **This is the default** and required for `trust != "local_only"`.
- `"permissive"`: Unknown components are skipped with a warning. Only allowed when `trust: "local_only"`. Use case: Forward compatibility when testing capsules from newer hypergumbo versions.

**Validation enforcement:**
```python
if validation_mode == "permissive" and trust != "local_only":
    raise SecurityError(
        "Permissive validation requires trust=local_only. "
        "For shared/registry capsules, use validation_mode=strict."
    )
```

**Enforcement in MVP (v0.1.0):**
`python_script` format has **limited sandboxing**:
* `network: "deny"` → **Soft enforcement only**
  - No network calls in hypergumbo code itself
  - Cannot prevent passes from using `urllib`, `requests`, etc.
  - Mitigation: Code review for built-in passes, community review for shared capsules
  - Future: OS-level network blocking (Linux: network namespaces, macOS: network extension, Windows: firewall rules)
* `sandbox: "recommended"` → **Best-effort isolation**
  - Environment variable isolation (separate `os.environ`)
  - Working directory isolation (`chdir` to temp dir)
  - Subprocess denial for untrusted passes (if `trust != "local_only"`)
  - Resource limits (memory, CPU time) via `resource` module (Unix only)
  - **Does NOT provide** filesystem isolation, capability restrictions, or syscall filtering
* **Process isolation (v0.1.0):**
  - `python_script` format capsules run in a separate subprocess by default.
  - The parent CLI passes only explicit inputs (repo_root, plan_path, config_path) and collects outputs from declared paths.
  - The CLI may apply OS-level resource limits to the subprocess (Unix best-effort via `resource`).
  - This is a stepping stone to `container` execution; it is not a full sandbox.
* `sandbox: "required"` → **Not supported in v0.1.0**
  - Future: Container-based execution
  - If capsule requires `sandbox: "required"`, runner exits with error

**Important:** In v0.1.0, `python_script` capsules are executed in a **separate OS process** (a subprocess) launched by the CLI. This improves determinism, resource limiting, and future sandbox compatibility, but it is **still not a complete security boundary**:
- The subprocess runs on the same machine, with the user's filesystem access.
- Network blocking is still best-effort unless enforced by the OS/container.
- Untrusted capsules from unknown sources remain unsafe unless executed under a real sandbox (future: container runner).

**Rule:** Only run untrusted or shared capsules when `sandbox` can be enforced (future versions) or when the user explicitly accepts the risk and is in `trust: local_only`.

**Security model progression:**
- **v0.1 (MVP):** Local trust only, soft sandbox
- **v0.2 (future):** Container execution for shared capsules
- **Future:** Full sandbox with verified isolation

#### `capsule_plan.json` — Composition Plan
Validated JSON selecting from pre-approved building blocks (passes/packs/rules/features):
```json
{
  "version": "0.1.0",
  "passes": [
    {
      "id": "python-ast-v1",
      "enabled": true,
      "config": {
        "parse_decorators": true,
        "infer_types_from_defaults": false
      }
    },
    {
      "id": "javascript-ts-v1",
      "enabled": true,
      "requires": ["hypergumbo[javascript]"],
      "config": {
        "jsx": true,
        "tsx": true
      }
    }
  ],
  "packs": [
    {
      "id": "python-fastapi",
      "enabled": true,
      "config": {
        "route_patterns": ["@app.get", "@app.post", "@router.get"],
        "async_handlers": true
      }
    }
  ],
  "rules": [
    {
      "type": "entrypoint_pattern",
      "pattern": "if __name__ == '__main__':",
      "label": "cli_entry"
    },
    {
      "type": "exclude_pattern",
      "glob": "**/*_test.py",
      "reason": "test files"
    }
  ],
  "features": [
    {
      "id": "auth-flow",
      "query": {
        "method": "bfs",
        "entrypoint": "fastapi_route:/api/login",
        "hops": 3,
        "max_files": 20
      }
    }
  ]
}
```

**Plan sections**:
- `passes[]`: Core analyzers to run (syntax, type checking, etc.)
- `packs[]`: Framework-specific feature bundles
- `rules[]`: Declarative patterns (entrypoints, excludes, aliases)
- `features[]`: Pre-computed slice queries

**Validation**: Plan is validated against `catalog.json` schema before execution.

#### `analyzer.py` — Stable Runner
Fixed script (same for all capsules) that is invoked by the selected runner (v0.1: subprocess runner) and:
1. Loads and validates `capsule_plan.json`
2. Orchestrates pass execution per plan
3. Compiles IR → views
4. Writes output files declared in `capsule.json.outputs`
**This file is identical across all repos**; customization happens in the plan.

#### `catalog.json` — Building Block Registry
Shipped with hypergumbo, describes available components:
```json
{
  "version": "0.1.0",
  "passes": [
    {
      "id": "python-ast-v1",
      "name": "Python AST Parser",
      "version": "hypergumbo-0.1.0",
      "capabilities": ["python"],
      "requires": {"runtime": "python>=3.10"},
      "evidence_types": ["ast_call_direct", "ast_call_method", "import_static"],
      "config_schema": {
        "parse_decorators": {"type": "boolean", "default": true}
      }
    }
  ],
  "packs": [
    {
      "id": "python-fastapi",
      "name": "FastAPI Pattern Pack",
      "version": "hypergumbo-0.1.0",
      "requires": {"passes": ["python-ast-v1"]},
      "config_schema": {
        "route_patterns": {"type": "array", "items": {"type": "string"}}
      }
    }
  ],
  "confidence_model": "hypergumbo-evidence-v1"
}
```
Users don't edit this file; it's updated via `pip install --upgrade hypergumbo`.

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

### Confidence model versioning
The confidence calculation algorithm is **versioned independently** of the schema to allow refinement without breaking compatibility.
**Contract**:
- Schema version `0.1.0` is compatible with confidence models `hypergumbo-evidence-v1` through `hypergumbo-evidence-v1.9`
- Confidence model v2 requires schema v0.2 or later
- Changes to base scores or adjustment weights increment minor version (v1.0 → v1.1)
- New evidence types can be added in minor versions; unknown types default to 0.30
This prevents confidence score refinements from forcing schema migrations.

### Confidence Scoring Contract
**Contract boundary:** The `confidence_model` field (not schema version) governs calculation semantics.
**Schema responsibilities:**
- Defines structure: `confidence` field exists (float 0.0-1.0)
- Defines supporting fields: `meta.evidence_type`, `meta.evidence_lang`
- Types and presence guarantees
**Confidence model responsibilities:**
- Defines calculation: `(lang, evidence_type, context) → confidence score`
- Maps evidence types to base scores
- Defines adjustment rules
**Versioning:**
- **Schema bump** (0.1.0 → 0.2.0): Structure changes (new fields, removed fields, type changes)
- **Confidence model minor bump** (v1.0 → v1.1): Score refinements, new evidence types, weight adjustments
- **Confidence model major bump** (v1.x → v2.0): Incompatible calculation algorithm, requires schema v0.2+
**Forward compatibility rules:**
1. Consumers MUST ignore unknown `evidence_type` values (default to 0.30)
2. Consumers MUST accept confidence model minor versions (e.g., code expecting v1.0 can read v1.5)
3. Consumers SHOULD validate confidence model major version matches expected range
4. Schema version and confidence model version are independent
**Example:**
- Schema v0.1.0 is compatible with confidence models `hypergumbo-evidence-v1.0` through `hypergumbo-evidence-v1.99`
- Schema v0.2.0 is compatible with confidence models `hypergumbo-evidence-v1.0` through `hypergumbo-evidence-v2.99`
- Confidence model v2.0 requires schema v0.2 or later
**Multiple-evidence rule (v0.1.0):**
- If `meta.evidence[]` is present, the edge’s top-level `confidence` SHOULD be computed from the primary evidence record (typically max confidence), and the chosen primary record should be mirrored into `meta.evidence_type/evidence_lang/evidence_spans`.
- Consumers that understand `meta.evidence[]` MAY recompute confidence using their own aggregation strategy (e.g., max, weighted max by analyzer trust), but MUST remain compatible with the published `confidence_model` semantics for single-evidence fields.

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
    "pass": "javascript-ts-v1",
    "reason": "requires hypergumbo[javascript] extra not installed"
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
    
    This is governed by confidence_model version, not schema version.
    Changes to this calculation increment confidence_model minor version.
    
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

## 8.7) Entrypoint Detection Improvements (Design)

> **Note:** This section describes the current state and interim mitigations. The long-term architectural direction is defined in [ADR-0003](adr/0003-architectural-analysis-and-revision-plan.md), which supersedes the "Future" subsections below.

Entrypoint detection uses path-based heuristics to identify HTTP handlers, CLI mains, and other entry sources for slicing. These heuristics are fast but prone to false positives when naming conventions collide across frameworks.

### Current State: Path-Based Heuristics (v0.6.x)

Each detector matches file paths and names:
- `_is_express_route_file`: matches `routes/` directory or `routes.js/ts`
- `_is_tornado_handler_file`: matches `*_handler.py` or `handlers/` directory
- `_is_micronaut_controller_file`: matches `*Client.java` or `client/` directory

**Problems:**
1. **Naming collision**: React Router uses `routes/*.tsx`, Express uses `routes/*.js` — same directory, different frameworks
2. **Overly broad patterns**: `*Client.java` matches gRPC clients, Redis clients, SDK clients — not just Micronaut HTTP clients
3. **No content verification**: Detection ignores actual file contents (imports, annotations)

### Implemented Mitigations (v0.6.x)

**Exclusion patterns** for known false positives:
- Express: Exclude `.tsx/.jsx` files (React components)
- Hapi/Koa: Same exclusion for React file-based routing
- Micronaut: Exclude `*ServiceClient`, `*GrpcClient`, `*RpcClient` (gRPC stub wrappers)
- Tornado: Exclude `*_error_handler.py`, `*_signal_handler.py`, etc. (non-web handlers)
- GraphQL: Exclude `*dns-resolver*`, `*dependency-resolver*`, etc. (non-GraphQL resolvers)

This is a whack-a-mole approach that reduces false positives but doesn't address the root cause: path heuristics cannot distinguish frameworks that share naming conventions.

### Future: Semantic Entry Detection (ADR-0003)

The long-term solution replaces path heuristics with **semantic entry detection** based on enriched symbol metadata. See [ADR-0003](adr/0003-architectural-analysis-and-revision-plan.md) for the full design.

**Key insight:** Entry kinds (routes, tasks, commands) are framework-afforded concepts that should be detected from symbol metadata, not file paths.

**Architecture overview:**
```
ANALYZERS (pure language, no framework knowledge)
  → Capture symbols + rich metadata (decorators, base classes, parameters)

FRAMEWORK_PATTERNS (configured by detected frameworks)
  → Match patterns against symbol metadata
  → Enrich symbols with concept metadata (route, task, model, etc.)

ENTRY_KINDS (semantic detection)
  → Query enriched metadata: if "route" in sym.concepts → Entry(kind="route")
  → High confidence (0.95) from semantic match
  → No path heuristics, no false positives
```

**Example: React Router false positive eliminated:**
```
Current (path heuristics):
  File: frontend/src/routes/login.tsx
  Path matches: /routes/*.tsx
  Result: Flagged as Express route ❌

Future (semantic detection):
  File: frontend/src/routes/login.tsx
  FRAMEWORK_PATTERNS: No Express patterns matched (no app.get decorator)
  Symbol concepts: {} (empty)
  Result: Not flagged ✅
```

**Benefits over ContentVerifier approach:**
- No additional file I/O — metadata already captured by analyzers
- Framework patterns are data (YAML), not code
- Entry detection becomes trivial: just query `sym.concepts`

### Scoring for Auto-Slice Entry Selection

**Scoring function** (applies to both current and future approaches):
```python
score = confidence * (1 + log(1 + outgoing_edges))
```
This prefers well-connected entries, producing richer slices.

### Migration Path

See [ADR-0003 §5.2](adr/0003-architectural-analysis-and-revision-plan.md#52-migration-path) for the authoritative migration plan.

| Version | Focus | Entry Detection Impact |
|---------|-------|------------------------|
| **v0.6.x** | Path heuristics + exclusions | Current state |
| **v0.7.x** | Foundation: metadata enrichment, `--frameworks` flag | Analyzers capture richer metadata |
| **v0.8.x** | FRAMEWORK_PATTERNS phase (YAML-driven) | Symbols enriched with concept metadata |
| **v0.9.x** | Semantic entry detection | `entrypoints.py` queries enriched metadata; path heuristics deprecated (retained only for `main()` fallback) |
| **v1.0.x** | Complete extraction | All frameworks as YAML; all analyzers pure |

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

* 🟩 **Behavior**: If pass requires unavailable extra (e.g., tree-sitter), skip pass
* 🟩 **Output**: Add to `analysis_runs[].skipped_passes[]`:
  ```json
  {
    "pass": "javascript-ts-v1",
    "reason": "requires hypergumbo[javascript]"
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

## 10) Milestones (MVP + buffer)
**Total timeline: 9 weeks** (2-week de-risking + 5 weeks core + 2 weeks buffer)

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Week 0 (de-risking) | 2 weeks | Week 0 |
| Week 1-5 (core development) | 5 weeks | Week 5 |
| Week 6-7 (buffer + polish) | 2 weeks | Week 7 |
| Week 8-9 (extended buffer) | 2 weeks | **Week 9** |

### Week 0: De-risking (2 weeks, dedicated time)
**Goals**: Validate high-risk components before committing to Week 1.
#### Week 0a (Days 1-5): Tree-sitter + Capsule validation
**Tree-sitter packaging** (Days 1-4):
* **Day 1-2: Wheel availability audit**
  - Check PyPI for pre-built wheels: `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`
  - For each: Linux x64, macOS arm64/x64, Windows x64, Linux arm64
  - Document: Which platforms have pre-built wheels? Which require source build?
* **Day 3-4: Source build testing**
  - Spin up VMs/containers for platforms lacking wheels (Windows x64, Linux arm64)
  - Attempt `pip install tree-sitter-javascript` from source
  - Measure: Time to build, success rate, error messages if missing C compiler
  - Test: Does warning message help users install missing dependencies?
**Capsule Plan validation** (Days 5-6):
* Minimal JSON schema validator implementation
* Test composition: select passes/packs from small catalog
* Verify plan → execution pipeline works
* validating tree-sitter runtime + one pack install path
* validating plan/catalog/pack schema pipeline end-to-end
#### Week 0b (Days 6-10): LLM testing + Integration
**LLM plan generation (Days 7-9):
* Prototype prompt engineering: repo profile → capsule plan
* Test with 3-5 sample repos (FastAPI, Electron, React)
* Measure: Does generated plan parse? Does it select reasonable passes?
* Cost test: Measure token usage, estimate per-repo cost
**Integration + Decision Gate** (Day 10):
* Run all prototypes together
* Document findings in ADR (Architecture Decision Record)
* Generate decision report: Go/No-Go for Week 1

**Decision gates:**
1. Tree-sitter packaging: Success on 3/4 platforms (Linux, macOS x2, Windows), OR Python-only fallback accepted
2. Capsule Plan: Validation works, composition pipeline functional
3. LLM: Generated plans valid >80% of time, cost <$0.10/repo, OR defer to template-only
**If gates fail**: Adjust Week 1 scope (e.g., skip LLM, defer JS/TS to v0.1.1).
### Week 1: Foundation + IR layer + Composition system
* Schema definition (behavior_map view with execution_id, run_signature, evidence_lang, evidence_spans, origin_run_signature)
* Internal IR classes (Symbol with revised stable_id + shape_id, AnalysisRun with execution_id + run_signature + repo_fingerprint)
* Pass interface and registry
* **Catalog system**: catalog.json schema, building block descriptors
* **Capsule Plan**: plan.json schema, validator, compiler
* Profile module (language detection)
* File discovery + exclude logic
* JSON writer (IR → views compilation)
* ID generation (signature-based stable_id + shape_id)
* **Tests:** schema validation, ID stability (survives refactors), plan validation, catalog loading
### Week 2: Python analyzer + evidence-based confidence
* Python AST parser → IR emission (implements Pass interface)
* Definitions (functions, classes, modules)
* Call edges (best-effort AST-based)
* Import edges
* Evidence-type-based confidence (deterministic algorithm)
* Provenance tracking (AnalysisRun with toolchain capture, execution_id, run_signature)
* **Tests:** Python parsing, evidence confidence determinism, provenance, toolchain capture, execution_id/run_signature hashing
### Week 3: JS/TS analyzer (optional extra)
* Tree-sitter integration (as `hypergumbo[javascript]` extra)
* JS/TS AST → IR emission (implements Pass interface)
* Best-effort call/import edges with evidence types
* Fallback behavior if tree-sitter unavailable
* **Tests:** JS parsing, graceful degradation
* **Risk mitigation:** Pre-build wheels validated in Week 0
### Week 4: Slicing + entrypoints + security defaults
* Slice module (BFS/DFS on IR relationships)
* Entrypoint detection heuristics (FastAPI, Flask, Express, Electron)
* Feature generation with query specs
* Slice IDs and reproducibility
* Security manifest defaults in capsule.json (validation_mode: strict, trust: local_only)
* **Tests:** slice correctness, entrypoint detection, query reproduction, security defaults
### Week 5: Capsule initialization + Factory
* `hypergumbo init` command with `--assistant` flag
* Template-based plan generation (default)
* **Optional LLM-assisted plan generation** (if Week 0 validated):
  - Prompt engineering: profile → plan
  - Plan validation against catalog
  - Fallback to template if LLM fails
* Capability detection (language profiling → pack selection)
* `hypergumbo catalog` command
* `hypergumbo export-capsule --shareable` command (privacy-safe export)
* Security defaults in manifest + plan
* Capsule validation (manifest + plan + runner compatibility)
* **Tests:** init with template, init with LLM (if available), catalog display, plan validation, security defaults, shareable export
### Week 6-7: Buffer + polish
* Documentation (README, architecture diagrams, evidence type catalog)
* Packaging fixes (any remaining tree-sitter issues)
* First real-world test repo (not a fixture)
* Performance profiling + caching validation
* Schema documentation + examples with all new fields
* Regression test suite finalization
* Release preparation
### Week 8-9: Extended buffer (if needed)
* Reserve for unexpected issues
* Additional real-world testing
* Performance optimization
* Quantitative metrics collection setup (for Spec A validation)


## 11) Key risks (MVP)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Tree-sitter install hell (platform-specific builds) | **Medium** | Medium | **Week 0 de-risking validates approach**; ship as optional extra: `pip install hypergumbo[javascript]`; pre-build wheels for common platforms; document source build fallback; contingency: Python-only MVP |
| "Best-effort" feels broken to users | Medium | High | Over-communicate in docs; show diffs with/without types; publish benchmark results showing quality scores; machine-readable evidence types enable transparency |
| Users skip `init` step | Medium | Medium | `hypergumbo run` auto-generates default capsule if missing; warns when using auto-generated capsule |
| Capsule becomes stale after updates | Medium | Medium | Include `generated_at` + version check; `hypergumbo run` warns if capsule older than 30 days or version mismatch |
| ID collisions in edge cases | Low | Medium | Append content hash to location-based ID if collision detected; log warning |
| Confidence algorithm feels arbitrary | Medium | Medium | Document algorithm clearly as "heuristic baseline (to be validated)"; ship examples; allow `--confidence-threshold` override; machine-readable evidence types show reasoning |
| Timeline slips due to packaging | Medium | Medium | **Week 0 buffer explicitly for this**; start tree-sitter packaging experiments before Week 1 |
| Schema changes break early adopters | Low | High | Semantic versioning from day 1; maintain v0.1 compatibility per support policy; publish migration guides; forward compatibility contract in Appendix E |
| Evidence types incomplete (edge cases not covered) | Medium | Low | Document "unknown" evidence type (confidence 0.30); collect telemetry on which types are hit; expand matrix in v0.2 |
| LLM plan generation unreliable | Medium | Medium | Template-based plans work without LLM; LLM is enhancement, not requirement; validate in Week 0 before committing |
| Capsule Plan composition too rigid | Low | Medium | Allow manual editing of plan.json; validation warns but doesn't reject unknown passes (permissive mode, local_only) |
| stable_id doesn't survive refactors in untyped code | Medium | Low | Document limitation clearly; upgrade to interface-based stable_id when types added; shape_id provides alternative |

## 12) Success criteria
### Technical
* 🟩 Analyzes Python repo (100 files) in <10 seconds
* 🟩 Generates valid behavior map JSON 100% of runs
* 🟩 Stable node IDs (same code → same IDs across runs)
* 🟩 **Stable IDs survive refactors**: Renamed/moved functions retain stable_id (when signature unchanged)
* 🟩 Capsule runs without network/API keys (unless --assistant llm used for init)
* 🟩 Catalog displays all available building blocks
* 🟩 Template-based plans work without LLM
* 🟩 Shareable capsules export without leaking repo structure

### Adoption (measured over 3 months post-launch)
* ⬜ 5+ agent projects using output (OR 3+ with detailed case studies)
* ⬜ 100+ repos analyzed
* ⬜ 3+ community-contributed capsule plans or packs published

### Agent validation (objective metrics)

**Measured during 3-month validation period after v0.1.0 ships:**

**Metric 1: Token reduction**
* ⬜ Measure: Tokens in hypergumbo slice vs. naïve approach (full files)
* Method: A/B test on 50 edit tasks across 3+ agents
* Target: >30% reduction (median)
* Collection: Agents log token counts, submit anonymized data

**Metric 2: Edit correctness**
* ⬜ Measure: Human evaluation of agent-generated edits
* Method: Blind review of 50 edits (25 with hypergumbo, 25 without)
* Target: ≥80% correct with hypergumbo (same or better than baseline)
* Evaluators: 2 independent developers (not project team)

**Metric 3: Hallucination rate**
* ⬜ Measure: Fabricated symbols/calls in agent output
* Method: Parse agent responses, check if symbols exist in hypergumbo nodes
* Target: <20% hallucination rate (vs. >40% without hypergumbo baseline)
* Collection: Automated parsing of agent logs

**Metric 4: Error cases (qualitative → quantitative)**
* ⬜ Measure: Documented cases where AST-only analysis failed
* Collection: GitHub issues, agent developer reports, user feedback
* Target: 20+ specific cases with reproduction steps
* Analysis: Categorize by type (false positive, false negative, missing edge)

**Pre-registration:**
Protocol published at https://hypergumbo.iterabloom.com/eval/a before data collection starts.
Prevents cherry-picking results.

**Use:** These metrics feed into decision whether to proceed with advanced capabilities.

### Benchmark validation (if research continues)
* ⬜ **Precision**: >0.85 on call graph edges (ground truth from 20 hand-verified repos)
* ⬜ **Recall**: >0.70 on detectable edges (AST-visible calls, not dynamic dispatch)
* ⬜ **Confidence calibration**: Edges with confidence >0.9 have <5% false positive rate

Pre-register evaluation protocol at https://hypergumbo.iterabloom.com/eval before collecting results.

### Quality
* 🟩 Zero crashes on 50+ real-world repos
* ⬜ Documentation clarity: new user can run analysis in <10 minutes

## 13) Spec A Validation (Prerequisite for Future Enhancements)

**Timeline:** 3 months after v0.1.0 ships

**Decision meeting:** Review evidence, decide whether to invest in advanced capabilities

**Quantitative requirements (all must pass):**
* ⬜ Agent adoption: 5+ named projects using hypergumbo in production
  - OR: 3+ projects with detailed case studies (>500 words each)
* ⬜ Quality improvement: >20% improvement vs. no-hypergumbo baseline
  - Measured via: Token reduction, OR edit correctness, OR hallucination reduction
  - A/B test with ≥50 tasks
* ⬜ Stability: <5% crash rate on 100+ repos
  - Measured via: CI runs, user reports, agent telemetry
* ⬜ Market signal: 10+ requests for features requiring higher-fidelity analysis
  - Logged in: GitHub issues, agent developer discussions, design partner feedback

**Qualitative requirements (2 of 3 must pass):**
* ⬜ Agent developers: "hypergumbo is critical, we'd pay for upgrades"
  - Survey or direct quotes from 3+ organizations
* ⬜ Design partners: 3+ orgs pledge engineering time for co-development
  - Written commitment (not just verbal interest)
* ⬜ Specific gaps: 20+ documented cases where AST analysis failed
  - With reproduction steps, expected behavior, impact assessment

**Funding secured:**
* ⬜ Research budget: 4-6 engineers × 6-8 months
* ⬜ Commitment: If research succeeds, funding for full development available

**Decision outcomes:**
1. **Go:** All quantitative + 2/3 qualitative + funding secured → Start research phase
2. **Prototype:** Partial evidence + limited funding → 2-month limited prototype
3. **Defer:** Evidence marginal → Wait 3 more months, re-evaluate
4. **Focus on iteration:** Evidence weak or absent → Improve current capabilities (more languages, better performance)

**No-go triggers (any of these → don't start research):**
* **Would HALT:** Spec A has fundamental adoption blockers (agents abandon after trying)
* **Would HALT:** Competition ships full typed analysis first (market opportunity lost)
* **Would HALT:** Team capacity unavailable (engineers exhausted, need break)
* **Would HALT:** No demonstrated need for higher fidelity (AST analysis "good enough")

**Decision makers:**
* Founder (final decision)
* 3+ design partners (advisory votes)
* 1 independent technical advisor (advisory)

**Documentation:**
* Decision recorded in ADR (Architecture Decision Record)
* Published summary: "Why we are/aren't building advanced capabilities"
* If going forward: Timeline and milestones confirmed

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
* **Versioned confidence**: Independent confidence model versioning prevents breaking changes

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

**4. Confidence model:**
- `confidence_model` field governs calculation (not schema version)
- Consumers MUST tolerate unknown `confidence_model` minor versions
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

**4. Incompatible confidence model:**
- Example: confidence_model v2.0 uses different evidence types, incompatible with v1.x

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

A multi-phase code understanding platform that produces typed IR + behavior maps, an agent context router for token-efficient editing, and an optional registry for sharing analyzers — built incrementally with explicit validation gates after Spec A proves market fit.

*All of Spec B is future work requiring design validation. See §1 Prerequisites.*

## 1) Goals and Prerequisites

### Prerequisites from Spec A

**Spec B depends on Spec A architectural decisions.** The following must be present in Spec A v0.1.0 or later:

**Required schema fields:**
- `execution_id` and `run_signature` (split from original single identifier)
- `repo_fingerprint` in `analysis_runs[]`
- `stable_id` (interface identity, not implementation)
- `shape_id` (optional, implementation fingerprint)
- `origin_run_signature` (optional, for grouping)
- `analysis_incomplete` flag (top-level)
- `skipped_passes[]` in `analysis_runs[]`
- `partial_results_reason` in `limits`

**Required manifest fields:**
- `validation_mode: strict|permissive`
- Security fields: `trust`, `network`, `sandbox`

**Required confidence model contract:**
- `confidence_model` version governs calculation (not schema version)
- Unknown `evidence_type` defaults to 0.30
- Consumers tolerate confidence model minor version increments

**Required compatibility guarantees:**
- Unknown field tolerance (consumers ignore new fields)
- Immutable ID formats (location-based IDs stable)
- `behavior_map.json` view remains stable

**Validation checkpoint:** Before starting B0, verify Spec A has shipped with all required fields. If not, coordinate timing or update Spec A first to avoid breaking changes.

### Prerequisites (must be met before starting any B phase)

* **Spec A (MVP) shipped and validated** with real agent workflows
  * **Quantitative validation:** 5+ agents report >20% improvement in code understanding vs. no hypergumbo
  * **Qualitative validation:** Agent developers confirm behavior graphs reduce hallucination
* **Demonstrated need** for higher-fidelity analysis (A's best-effort insufficient)
  * **Evidence required:** 20+ specific cases where AST-only edges caused agent errors
  * **Benchmark suite:** Precision/recall metrics showing gap between AST and typed analysis
* **Design partners committed** to co-development (3-5 organizations)
  * **Commitment defined:** Dedicated engineering time, real repos, feedback SLAs, written agreements
* **Engineering capacity**: 3-5 engineers available for 12+ months
* **Funding secured** for phased development (42-52 month total timeline)

### Decision Framework for Starting Each Phase

Each phase has explicit go/no-go gates. **Do not proceed without meeting prerequisites.**

---

#### Gate 0: Spec A → B0 Decision

**Timing:** 3 months after Spec A v0.1.0 ships

**Decision makers:**
* Founder (final decision)
* 3+ design partners (advisory votes)
* 1 independent technical advisor (advisory)

**Quantitative requirements (all must pass):**
* Agent adoption: 5+ named projects using hypergumbo
  - OR: 3+ projects with detailed case studies (>500 words each)
* Quality improvement: >20% improvement vs. no-hypergumbo baseline
  - Token reduction, OR edit correctness, OR hallucination reduction
  - A/B test with ≥50 tasks
* Stability: <5% crash rate on 100+ repos
* Market signal: 10+ requests for higher-fidelity analysis features

**Qualitative requirements (2 of 3 must pass):**
* Agent developers: "hypergumbo is critical, we'd pay for upgrades"
* Design partners: 3+ orgs commit to B0 co-development (written agreements)
* Error database: 20+ cases where AST analysis failed (with reproduction steps)

**Decision outcomes:**
1. **Go:** All quant + 2/3 qual + funding → Start B0 (8 months)
2. **B0-lite:** Partial evidence → 2-month prototype (TypeScript only, 1 engineer)
3. **Defer:** Evidence marginal → Wait 3 months, re-evaluate
4. **No-go:** Focus on Spec A iteration (more languages, better performance)

**No-go triggers:**
* **Would HALT:** Agents abandon Spec A after trying (fundamental flaw)
* **Would HALT:** Competition ships typed analysis first
* **Would HALT:** Team exhausted, needs break
* **Would HALT:** No demonstrated need for higher fidelity

---

#### Gate 1: B0 → B1 Decision

**Timing:** After B0 Month 8 (evaluation complete)

**Prerequisites (all must pass):**
* TypeScript prototype: >0.80 precision on benchmark
* Context Router: >30% token reduction in agent A/B test
* HTTP linker: <20% FP rate with annotations
* Independent evaluation: Report confirms results
* Design partners: 3+ still committed for B1 (15 months)

**Decision outcomes:**
1. **Go:** All gates passed → Start B1 (15 months, 3-5 engineers)
2. **Pivot:** Some prototypes failed → Adjust B1 scope
   - Example: Skip TypeScript if precision <0.80, focus on Python types only
   - Example: Skip HTTP linker if FP rate >30%, defer to B1.5
3. **Stop:** Multiple prototypes failed → Return to Spec A, iterate

**Pivot decision matrix:**

| TypeScript | Context Router | HTTP Linker | Decision |
|------------|---------------|-------------|----------|
| >0.80 | >30% | <20% FP | **Full B1** (all features) |
| >0.80 | >30% | **Would HALT:** >30% FP | **B1 minus HTTP** (defer linker) |
| >0.80 | **Would HALT:** <30% | any | **B1 without smart context** (basic slicing only) |
| **Would HALT:** <0.80 | any | any | **Investigate why** 2-month deep-dive, decide: Fix or pivot to Python-only |
| **Would HALT:** <0.60 | **Would HALT:** <20% | **Would HALT:** >40% FP | **Stop B entirely** (fundamental approach flawed) |

---

#### Gate 2: B1 → B1.5 Decision

**Timing:** 6 months after B1 ships (B1 in production)

**Prerequisites (all must pass):**
* B1 stable: 6+ months in production, <2% crash rate
* Agent demand: 5+ requests for "smarter context" features (logged)
* Evidence B1 insufficient: Quantitative gap demonstrated
  - Example: Agents still have >X% hallucination rate, analysis shows better slicing would help
  - Example: 30+ edit tasks failed due to missing dataflow info
* Design partners: 3+ orgs commit to B1.5 co-development (18 months)
* B0 dataflow prototype: Showed >0.7 precision (look back at B0 results)

**Decision outcomes:**
1. **Go:** All gates passed → Start B1.5 (18 months)
2. **Agent-guided slicing:** Demand exists but dataflow risky → Simpler alternative
   - Agents specify hops/filters via DSL
   - Tool executes precisely (no inference, no NP-hard problems)
   - Example: Agent says "show me callees of X within 2 hops, excluding tests"
   - Lower risk, still provides value, 6-month delivery
3. **Skip:** B1 slicing "good enough" → Focus elsewhere (more languages, registry, performance)

**If skipping B1.5:**
* Not a failure (B1 still valuable)
* B1 continues as stable product
* Resources redirect to B2 or Spec A improvements

---

#### Gate 3: B1/B1.5 → B2 Decision

**Timing:** After B1 (or B1.5) ships + 3 months production use

**Prerequisites (all must pass):**
* **B0 reusability validation passed:** Analyzers reusable >70% of time (see B0 Month 5-6 test)
  - **Critical:** If B0 showed analyzers too bespoke, B2 full registry is not justified
* Community activity: 100+ repos analyzed, 20+ custom capsules created
* Sharing behavior: 5+ users informally sharing capsules (GitHub gists, etc.)
* Demand: 10+ requests for "find analyzer for X" (logged)

**Decision outcomes:**

| B0 Reusability | Community Activity | Decision |
|----------------|-------------------|----------|
| >70% success | Active (100+ repos) | **Full B2 registry** (10-12 months) |
| 50-70% | Active | **Lightweight GitHub repo** (1 month) |
| <50% | any | **No registry** (analyzers too bespoke, not worth infrastructure) |
| any | **Would HALT:** Low activity | **Wait 6 months** (registry premature, no demand yet) |

**Lightweight alternative (if <70% reusability):**
* GitHub repo: `hypergumbo-community/capsule-examples`
* Curated examples for common stacks
* Community PRs welcome
* No server infrastructure
* Effort: 1 month vs 10-12 months

---

### Decision Meeting Process

**All gates use this process:**
1. **Evidence packet** (prepared 1 week before meeting):
   - Metrics summary (quantitative results)
   - Qualitative feedback (design partner quotes, agent developer surveys)
   - Risk assessment (what could go wrong in next phase?)
   - Recommendation (PM/tech lead proposal)
2. **Meeting** (2 hours):
   - Present evidence (30 min)
   - Discussion (60 min)
   - Vote (30 min)
   - Options: Go / No-go / Pivot / Defer
3. **Documentation** (ADR - Architecture Decision Record):
   - Decision outcome
   - Rationale (why this choice?)
   - Dissenting opinions (if any)
   - Next steps (timeline, milestones)
   - Published publicly (transparency)
**Commitment:** Follow the process. If gate fails, **do not proceed.**
### Objectives
* 🟪 **High-fidelity IR** across common stacks (typed call graph + optional dataflow + cross-language edges).
* 🟪 **Agent-grade context provider** ("give me the smallest set of code + invariants to safely edit X").
* 🟪 **Optional analyzer registry** (capsules + rule packs + fingerprints + benchmarks).
* 🟪 **Amortize LLM cost** via nearest-neighbor reuse + retrieval-augmented generation.
* 🟪 **Local-first privacy**, optional opt-in sharing of sanitized metadata only.

## 2) Non-goals
* "Prove programs correct" in the formal methods sense.
* Full support for every language; focus on dominant stacks with pluggable packs.
* Real-time collaboration or social features (comments, likes, follows).
* Hosted SaaS offering or marketplace monetization (registry is free/open).
* IDE integration (LSP server) or autonomous code editing.

### Don't build in B1
* **Would HALT:** Full incremental daemon** (use simple file-change → re-analyze)
* **Would HALT:** Formal verification** or proof generation
* **Would HALT:** Code generation** or autonomous editing
* **Would HALT:** Real-time collaboration** features
* **Would HALT:** IDE integration** (LSP server)
* **Would HALT:** Support for >5 languages** (focus on TypeScript, Python, Go, Rust, Java)
* **Would HALT:** Natural language query parsing** (B1.5 only)
* **Would HALT:** Dataflow slicing** (B1.5 only, with strict timeouts)
* **Would HALT:** Impact prediction** ("what could break" inference — B1.5 only)
* **Would HALT:** SQL/Protobuf/GraphQL linkers** (defer to B1.5 or later)
### Don't build in B2
* **Would HALT:** Social features** (comments, likes, follows)
* **Would HALT:** Marketplace monetization** (paid capsules)
* **Would HALT:** Hosted analysis** (SaaS offering)
* **Would HALT:** Training custom models** per-repo
* **Would HALT:** Complex recommendation engines** (simple similarity search only)
* **Would HALT:** Real-time capsule updates** (periodic refresh is sufficient)

## 3) System architecture
### 3.1 Core: Multi-pass analysis engine
#### Frontends (parsers / symbolizers)
* 🟩 **tree-sitter** for universal syntax (inherited from Spec A)
* 🟪 **Language-native engines** (optional, high-fidelity):
  * 🟪 **TypeScript**: `tsserver` (type checker + language service)
  * 🟪 **Python**: `pyright` or `mypy` (type inference)
  * 🟪 **Rust**: `rust-analyzer` (full semantic analysis)
  * 🟪 **Go**: `gopls` (language server)
  * 🟪 **JVM**: Eclipse JDT (Java), Kotlin analysis tooling
#### Runner types and execution models
Different analyzers require different execution environments. The capsule manifest declares required runner type.

🟩 **`python_script` (Spec A, B1)**:
- Single Python file, minimal dependencies
- Runs in same Python environment as hypergumbo CLI
- Fastest, simplest, most portable
- **Limitations**: Can't bundle complex toolchains

🟪 **`toolchain_bundle` (B1)**:
- Ships with language server or type checker
- Example: tsserver, pyright, gopls bundled as subprocess
- Heavier (100MB+ downloads), but high fidelity
- **Security**: Runs in restricted sandbox, no network by default

🟪 **`container_image` (B2)**:
- OCI/Docker image with full environment
- Maximum isolation and reproducibility
- **Security**: Runs in container runtime (Docker/Podman)
- **Limitations**: Requires container runtime installed
- **Use case**: CI environments (GitHub Actions, GitLab CI)
- **Not recommended**: Local development (too heavy)

🟪 **`daemon_process` (Future, not in current specs)**:
- Long-running incremental analysis
- Watches file changes, maintains indexed state
- Example: Language server protocol (LSP) integration
- **Note**: Incremental analysis is 18+ month project, not on current roadmap

**Manifest declares runner type**:
```json
{
  "format": "toolchain_bundle",
  "runtime_image": "hypergumbo/typescript-analyzer:v1.2.3",
  // ...
}
```

**hypergumbo CLI selects appropriate runner** based on format field.

#### IR builder
* **Typed symbol table** + cross-ref index (extends Spec A IR)
* **Call graph** with resolution quality scores (0.0–1.0)
* **Optional layers**:
  * Control-flow graph (CFG) — **optional, time-boxed**
  * Dataflow facts (reaching definitions, taint tracking) — **optional, strict timeouts required**
**Design principle:** Dataflow/CFG are **opt-in capabilities** with explicit partial-results flags, NOT core requirements for B1. Absence of dataflow should not block B1 shipping.

#### IR vs Views architecture
**Core principle:** Strong separation between internal representation and public outputs (inherited from Spec A, extended in B).
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

**Why this matters:**
* Spec A parsers can coexist with Spec B strong analyzers
* Mixed-fidelity analysis: AST edges (0.7 confidence) + typed edges (0.95 confidence) in same graph
* Public view schemas evolve slowly; IR can evolve rapidly

#### Cross-language linkers
* 🟩 **HTTP**: route patterns ↔ client calls ↔ handlers (server-side route detection done in Spec A)
  * Example: FastAPI `@app.get("/users/{id}")` ↔ Axios `GET /users/123`
* 🟩 **IPC (Electron)**: main ↔ renderer message channels (done in Spec A)
  * Example: `ipcMain.on("login")` ↔ `ipcRenderer.send("login")`
* 🟪 **SQL**: query ↔ table/column mapping (best-effort, annotation-assisted)
  * Example: `SELECT * FROM users WHERE id = ?` ↔ `users` table schema
* 🟪 **Protobuf/gRPC**: service defs ↔ server impl ↔ client stubs
* 🟪 **GraphQL/OpenAPI**: schema ↔ resolvers ↔ clients

**Implementation strategy:** Start with **annotation-assisted** linking (developer hints) before attempting full inference.

```python
# Annotation example (Python → SQL)
@hypergumbo.sql_query(table="users", columns=["id", "email"])
def get_user_by_email(email: str):
    return db.execute("SELECT id, email FROM users WHERE email = ?", email)
```

**Design principle:** Treat linkers as **passes** (same interface as language parsers) for architectural consistency with Spec A.

### 3.2 Agent Context Router

**Caution:** COMPLEXITY WARNING - This subsection describes research-hard capabilities

Full Context Router as specified here is delivered in **B1.5** (separate from B1).
B0 validates feasibility, B1 delivers basic slicing only, B1.5 delivers full capability.

Do not commit resources to B1.5 until B0 research phase shows >0.7 precision on prototypes.

#### Query interface

"I want to change behavior X in Y context"

#### Pipeline (phased delivery)

1. **Retrieve** relevant nodes/flows from IR
   * Entry: symbol name, file path, route pattern, or natural language description
   * **B1 scope:** Symbol/file/route only (no natural language)
   * **B1.5 scope:** Natural language queries via embedding similarity
  
2. **Slice** on:
   * Call graph (forward/backward) — **B1**
   * Dataflow (tainted data paths) — **B1.5 only, after validation**
   * Schema ties (database columns, API contracts) — **B1.5**
   * Tests referencing the area — **B1**
   * Configuration/deployment ties — **B1.5**
   * **Supply chain tier boundaries** (from Spec A §8.6) — **B1**
     - First-party code (tier 1) always included
     - External deps (tier 3) only when edges cross the boundary
     - Tier boundaries as natural "blast radius" limits
  
3. **Assemble context bundle**:
   * Minimal code excerpts (only changed + affected) — **B1**
   * Invariants/contracts (types, tests, assertions) — **B1**
   * "What could break" checklist — **B1.5 only, requires whole-program analysis**
  
4. **Validator pass** (not in B1 or B1.5):
   * Requires fast incremental re-analysis (separate 18+ month project)
   * Rerun analyzers on changed files
   * Regression slice comparisons (before/after diff)
   * Invariant checks (type errors, test failures)

**Token budget optimization (B1 scope):**
- Simple truncation: BFS until token limit hit
- Deterministic ordering: Sort by (edge confidence, distance from entry)
- No ML-based ranking (defer to B1.5)

**🟪 Future B1.5:**
- 🟪 Learned relevance models
- 🟪 Agent feedback loop (which code was actually edited?)
- 🟪 **Embedding-based context expansion**: Use embedding selections as a scaffold, then expand context around them as token budget allows. At line level: grow ±N lines around selected chunks. At word level: grow ±N words around selected phrases. No recomputation of embeddings needed—just expand around already-selected items. Applicable to Key Symbols (once embeddings are used there) and config extraction.
- 🟪 **Coverage report parsing for test summary**: Parse coverage reports (coverage.xml, lcov.info, etc.) to extract coverage percentages for the sketch test summary section. E.g., "103 test files · pytest · 85% coverage". Requires detecting and parsing various coverage report formats without executing tests (static parsing of existing reports).
- 🟪 **Multi-language documentation summarization**: Detect and collapse multi-language documentation directories (e.g., `docs/de/`, `docs/en/`, `docs/es/`) into a single summary line like "Documentation in 12 languages (de, en, es, fr, ...)". Reduces noise in sketches for documentation-heavy projects where translated docs dominate the file count. Detection via parallel directory structures with language codes (ISO 639-1) or common patterns like `docs/{lang}/` or `{lang}/docs/`.

#### Complexity warning
* **Natural language query parsing** (step 1) requires embedding models + semantic search
* **Dataflow slicing** (step 2) is NP-hard in general; even heuristic solutions are multi-month research
* **Impact prediction** (step 3) requires whole-program analysis with test coverage correlation
* **Validator pass** (step 4) needs incremental re-analysis (not on B roadmap)

**Mitigation strategy:**
* **B0 (8 months):** Prototype simple query → slice with BFS/DFS only
* **B1:** Ship basic context assembly without dataflow or impact prediction
* **B1.5 (18 months):** Full Context Router with dataflow + impact as separate project

**Gate B1.5 on:** B0 prototypes showing >0.7 precision AND agent validation proving value

### 3.3 Social layer: Registry + meta-analysis

**Caution:** VALUE VALIDATION REQUIRED

Registry (B2) assumes:
1. Analyzers cluster into generic profiles (work across similar repos)
2. Community contribution will be significant (>50 contributors)
3. Similarity search provides value over manual selection

**Gate B2 on:**
* **B0 validation completed:** Repo clustering shows similarity >0.7 for 50%+ of pairs in same category, AND analyzer reusability test shows >70% success rate
* B1 shipped + validated for 6+ months
* Evidence that analyzers ARE shareable (not all bespoke)
* Demand signals: 10+ requests for "find me an analyzer for X"

**Alternative if validation fails:** GitHub repo of example capsules (no infrastructure)

#### Artifacts stored
* **Analyzer capsules** (code or container image)
* **Rule packs** (linter rules, invariant checkers)
* **Schema versions** supported (compatibility matrix)
* **Behavioral fingerprint** from benchmark runs
#### Repo profiles
* **Language mix**: percentages, framework signals
* **Architectural features**: endpoint count, database access patterns, IPC usage
* **No source code** uploaded (privacy-preserving)
#### Similarity search
* **Nearest-neighbor** on profile vectors + analyzer fingerprints
* **Use case**: "Your repo looks like 50 others; here's an analyzer that worked well for them"

#### Trust and provenance
**Phased approach:**

**Phase 1 (B2 alpha/beta): Centralized trust**
* hypergumbo team runs registry + signing authority
* Analyzers submitted for review + benchmark
* Approved analyzers get signed by central key
* Clients trust the central registry

**Phase 2 (B2 production): Sigstore/transparency log**
* Analyzers signed by author's key
* Signatures recorded in append-only transparency log (Rekor)
* Benchmarks + reviews published alongside
* Clients verify signatures + check community ratings

**Future (optional): Web-of-trust**
* Users build their own trust networks
* Analyzer authors sign with PGP
* Curators vouch for quality via signatures
* No central authority
#### Governance
* **Signing / provenance**: track who created/modified each capsule
* **Trust score**: benchmark stability + adoption signals
* **Compatibility matrix**: which schema versions supported

## 4) "Factory" evolution
Generation becomes retrieval-augmented:
1. **Profile repo** → get nearest-neighbor analyzers/rulepacks from registry
2. **Compose starter analyzer** from proven parts (not full LLM generation)
3. **Use LLM only to**:
   * Adapt deltas for repo-specific patterns
   * Create missing cross-language linkers
   * Generate summaries for domain-specific flows
4. **Run benchmark suite** + local tests → self-repair loop until stable
5. **Optionally publish** to registry (with sanitized metadata only)

**Key difference from Spec A:** Amortizes LLM cost via reuse. Most repos get 90% working analyzer from registry; LLM only fills 10% gaps.

## 5) Output contracts

### Core IR (protobuf or stable JSON)

Versioned schema, not exported by default. Used internally by analyzers and registry.

### Views

#### 🟩 behavior_map.json (Spec A compatible)
* Same schema as Spec A v0.1
* Maintained for backward compatibility
* Enhanced with higher-fidelity edges when available (via higher confidence scores, new evidence types)

#### 🟪 ir_export.json (full detail)
* Complete symbol table
* Typed edges with resolution provenance
* Dataflow facts (optional)
* Cross-language links

#### 🟪 context_bundle.json (agent-optimized)
* Minimal code excerpts for a query
* Invariant checklist
* "Impact zones" (what could break) — **B1.5 only**
* Token-budget optimized

#### 🟪 sarif.json (CI integration)
* SARIF 2.1 compatible
* Findings from rule packs
* Integration with GitHub Code Scanning, GitLab SAST

### Flow specs

Named, traceable feature flows:
* "Signup pipeline": route → handler → validation → database → email notification
* "Payment settlement flow": API call → queue → worker → payment gateway → webhook

Each flow:
* Entry/exit points
* All nodes/edges in path
* Invariants (expected types, error handling)
* Tests covering the flow

## 6) Privacy & security

### Local analysis by default
* All analysis runs locally
* No data leaves machine without explicit opt-in

### Opt-in upload (B2 privacy model)

**Default: No individual profiles uploaded**

Registry similarity uses **k-anonymity approach** (not individual repo profiles):

**Client-side:**
1. Analyze repo locally → compute feature profile
   ```python
   profile = {
     "languages": {"python": 0.75, "javascript": 0.20, "css": 0.05},
     "frameworks": ["fastapi", "sqlalchemy"],
     "endpoint_count_bucket": "20-50",  # Binned, not exact
     "avg_complexity": 2.3
   }
   ```

2. Download aggregate cluster centroids from registry:
   ```python
   clusters = registry.get_clusters()  # 10-20 centroids, each representing 100+ repos
   [
     {"id": "cluster-7", "centroid": {...}, "count": 237},
     {"id": "cluster-12", "centroid": {...}, "count": 412},
   ]
   ```

3. Find nearest centroid locally:
   ```python
   nearest = find_nearest_cluster(profile, clusters)
   # Sends only cluster ID to registry, not profile
   ```

4. Request analyzers for cluster:
   ```python
   analyzers = registry.get_analyzers_for_cluster("cluster-7")
   # Server returns: Analyzers that worked well for 100+ repos in this cluster
   # Server never sees individual repo profile
   ```

**Result:** Registry only sees cluster membership (7 out of 20 clusters), not full profile.

**Privacy properties:**
* Server learns: "User's repo is similar to cluster-7" (shares with 100+ other repos)
* Server does NOT learn: Language percentages, exact endpoint count, specific frameworks
* k-anonymity: k=100+ (minimum cluster size)
* Re-identification risk: Low (1 in 100+)

---

**Opt-in personalized recommendations (enterprise customers):**

For users who want better recommendations:

**Explicit consent flow:**
```
hypergumbo registry login
> This will upload your repo profile for personalized analyzer recommendations.
> Profile includes: language mix, framework signals, aggregate metrics.
> Profile excludes: source code, symbol names, file paths.
> Uploaded data stored for 90 days, deleted after.
> Continue? [y/N]
```

**What's uploaded (with consent):**
* Language percentages (exact, not binned)
* Framework signals (detected libraries)
* Aggregate counts (nodes, edges, endpoints)
* **Would HALT:** NOT uploaded: Source code, symbol names, directory structure, file paths

**Server-side:**
* Compute similarity to all public analyzers
* Return top 10 matches (personalized ranking)
* Store profile for 90 days (enables "your past queries")
* After 90 days: Delete profile (GDPR compliance)

**Use case:**
* Enterprise with internal registry (trust is higher)
* Power users who want best recommendations
* Acceptable trade-off: Privacy for quality

---

**Self-hosted registry (maximum privacy):**

Organizations can run registry on-premise:

**Deployment:**
```bash
docker-compose up -d  # Postgres + MinIO + web server
hypergumbo registry init --self-hosted http://localhost:8080
```

**All data stays internal:**
* No data sent to external servers
* Full control over profiles, analyzers, benchmarks
* Suitable for: Security-sensitive orgs, regulated industries

---

**Do NOT claim differential privacy** unless:
* Formal ε/δ guarantees provided
* Noise calibrated to sensitivity analysis
* Privacy budget tracked and published
* Peer-reviewed implementation

**Current approach (k-anonymity) is honest:**
* No false guarantees
* Re-identification risk acknowledged (but low with k>100)
* Stronger than no privacy, weaker than formal DP

### Analyzer execution sandboxing
* **Containers** (Docker/Podman) for untrusted capsules from registry
* **Restricted runner** (seccomp/AppArmor profiles) for Python scripts
* **Signed artifacts** for registry distribution (prevent tampering)

### Security review process
* Registry capsules undergo automated security scan
* Dependency audit (no known CVEs)
* Resource limits (memory, CPU, disk)
* Network access restrictions (default: deny all outbound)

## 7) Scale & performance

### Incremental analysis: Not on roadmap

**Status**: Deferred indefinitely (not in B1, B1.5, or B2).

**Why it's hard** (18+ months minimum):
- Dependency tracking (which symbols affect which)
- Invalidation propagation (change X → re-analyze Y, Z)
- Cross-file type inference updates
- Symbol table consistency across versions

**Evidence**: TypeScript incremental took ~3 years, rust-analyzer ~2 years.

**Current mitigation**:
- **Cached slices**: Pre-compute common features, store in `.hypergumbo/cache/`
- **Symbol index**: O(1) lookup for "find definition of X"
- **Partial re-analysis**: Re-analyze changed files + direct importers only
- **Accept latency**: Full analysis on deep queries is OK with progress bars

**Future consideration**: If B1.5 Context Router shows need for <2s queries on large repos, reconsider incremental as separate research project (not part of B1.5).

**Don't wait for incremental to ship B1 or B1.5.**

### Cached slices
* **Stable slice IDs** for features (based on query spec, as in Spec A)
* **Fast "what changed" diffing**: compare slice node sets across commits
* **Pre-computed slices** for common queries (can be stored in registry)
### Performance targets
* **Small repo** (<100 files): <10 seconds (same as Spec A)
* **Medium repo** (~500 files): <60 seconds (2× Spec A due to type checking)
* **Large repo** (2000+ files): <5 minutes full analysis
* **Context router query** (B1): <2 seconds for typical slice assembly
* **Complex dataflow queries** (B1.5): <30 seconds acceptable (relax requirement for research-hard features)

## 8) Milestones — PHASED APPROACH

### CRITICAL: Do not start any phase until prerequisites are met

Each phase below lists explicit prerequisites. Starting without validation creates sunk-cost risk.

**General rule**: If a prerequisite is not met, either:
1. Achieve the prerequisite (don't skip), OR
2. Re-scope the phase to remove dependency, OR
3. Cancel/defer the phase

---

### **B0: Research & Validation Phase (8 months, 2-3 engineers + contractors)**
**Goal:** Validate that the hard parts of B are actually feasible before committing to B1.

#### Prerequisites
* Spec A validated: 
  - 5+ agent adoptions confirmed (named projects, not anonymous)
  - Quantitative improvement: >20% reduction in "code not found" errors (agent logs)
  - 100+ repos analyzed without crashes
* Funding secured: Budget for 8 months (4-6 engineers)
* Benchmark repos collected:
  - 20+ TypeScript repos with ground-truth call graphs
  - 10+ full-stack repos (Python backend + JS frontend)
  - 10+ Electron apps
* Design partner pipeline: 5+ orgs in discussions, 3+ committed in writing

**Validation checkpoint**: If Spec A adoption <5 agents OR no quantitative improvement shown, investigate why before starting B0.

#### Months -2 to 0: Benchmark Preparation (before engineering starts)

**Goal:** Create ground truth datasets before prototype engineering begins.

**Hiring (Month -2):**
* 2 developers
* Skills: TypeScript + Python experience, attention to detail

**Benchmark creation (Months -2 to -1):**

**TypeScript repos (20 total):**
* Criteria: 500-5000 LOC, active maintenance, diverse patterns
* For each repo:
  - Clone and build successfully
  - Select 10 call edges manually (trace through code)
  - Document: Caller file:line, callee file:line, edge type (direct call, method call, etc.)
  - Time: 8-12 hours per repo
* Deliverable: `benchmarks/typescript/repos.json` with ground truth

**Full-stack repos (10 total):**
* Criteria: Python backend + JS/TS frontend, HTTP APIs
* For each repo:
  - Identify 5 route→handler pairs (backend endpoints)
  - Identify 5 client→route calls (frontend API calls)
  - Document expected cross-language links
  - Time: 10-15 hours per repo
* Deliverable: `benchmarks/fullstack/repos.json`

**Electron apps (10 total):**
* Criteria: Main + renderer processes, IPC usage
* For each repo:
  - Identify 3 main→renderer IPC pairs (`ipcMain.on` ↔ `ipcRenderer.send`)
  - Identify 3 renderer→main pairs
  - Document expected IPC links
  - Time: 6-10 hours per repo
* Deliverable: `benchmarks/electron/repos.json`

**Pre-registration (Month -1):**
* Publish evaluation protocol: https://hypergumbo.iterabloom.com/eval/b0
* Include:
  - Test repo names (no changes after publication)
  - Metrics definitions (precision, recall formulas)
  - Thresholds (>0.80 precision goal)
  - Baseline (Spec A AST-only results on same repos)
* Lock protocol (no edits after data collection starts)

**Independent evaluators (Month 0):**
* Hire 2 developers (not project team) to run evaluation
* Provide protocol, scripts, benchmarks
* Task: Execute protocol, verify results, write report

**Deliverables before B0 Month 1:**
* 40 repos with ground truth (TypeScript, full-stack, Electron)
* Evaluation protocol pre-registered publicly
* Independent evaluators contracted
* Baseline results (Spec A on benchmark repos)

**Total effort:** 360-520 person-hours  
**Critical path:** 2-3 months

**If skipped:** B0 engineers create benchmarks ad-hoc during prototyping → Biased evaluation, cherry-picked results, untrustworthy metrics

#### Month 1-2: TypeScript Integration Prototype
* Integrate `tsserver` as subprocess (or via tree-sitter with types)
* Extract typed call graph from 10 test repos
* Measure precision vs AST-only (baseline from Spec A)
* **Decision gate:** >0.80 precision on typed edges, OR clear path to achieve

**Phase 1 (Week 1): Happy path**
* Small repo (10-20 .ts files, <500 LOC each)
* Full type annotations
* Measure: Cold start time, per-file analysis time, memory usage

**Phase 2 (Week 2): Real-world repos**
* Large repo (500+ .ts files, 50K+ LOC)
* Mixed quality (some files lack types, some use `any`)
* Measure: Same metrics, see where it breaks

**Phase 3 (Week 3-4): Edge cases**
* Monorepo (multiple tsconfig.json files)
* Missing dependencies (node_modules incomplete)
* Version skew (user has old tsserver)
* Timeout behavior (kill subprocess after 30s)

**Success criteria for B1:**
* Precision >0.80 on happy path (well-typed repos)
* Graceful degradation: Falls back to AST when tsserver fails
* Performance: <2 minutes for 500-file repo (90th percentile)
* Memory: <4GB for large repos
* **Caution:** If any fail: Re-scope B1 (maybe TypeScript-only, skip Python types)

**If prototype fails completely:**
* Fallback: Use tree-sitter + type annotation extraction (no full type checking)
* Lower fidelity (can't resolve `import {X} from Y`) but still better than AST-only
* Defer tsserver integration to B1.5 (with more time/resources)

#### Month 3-4: Context Router Prototype
* Simple query → slice implementation (no natural language, no dataflow)
* BFS/DFS with hop limits on typed graph
* Token counting + bundle assembly
* **Test with 3 agents:** Does minimal context actually improve edit quality?
* **Metrics:** Token reduction, edit correctness, agent developer feedback
* **Decision gate:** >30% token reduction with same/better correctness

#### Month 5-6: HTTP Linker Prototype + Registry Reusability Test

**HTTP linker (Weeks 1-2):**
* Annotation-assisted + simple inference
* Test on 10 full-stack repos
* Measure false positive rate
* **Decision gate:** <20% false positive rate with annotation hints

**Registry clustering validation (Weeks 1-2, parallel)**

**Goal:** Test whether analyzers cluster into generic profiles.

**Repo collection:**
* 30 FastAPI repos (10 small <100 files, 10 medium 100-500, 10 large 500-2000)
* 20 Electron repos (balanced mix)
* 20 React/Next.js repos

**Feature extraction:**
```python
profile = {
  "languages": {"python": 0.73, "javascript": 0.18, "css": 0.09},  # percentages
  "frameworks": ["fastapi", "sqlalchemy", "react"],  # detected signals
  "endpoint_count": 47,  # HTTP routes
  "db_tables": 12,  # database access patterns
  "ipc_channels": 0,  # Electron IPC (if applicable)
  "component_count": 23,  # React components
  "route_depth": 2.3,  # avg nesting of routes
}
```

**Similarity calculation:**
* Compute feature vectors (normalize percentages, binary encode frameworks, standardize counts)
* Pairwise cosine similarity for all repos in same category
* Generate similarity matrix + cluster dendrogram

**Success criteria (quantitative):**
* Within-cluster similarity >0.7 for 50%+ of pairs
* Between-cluster similarity <0.4 (distinct clusters exist)
* Cluster membership predictable from profile (>80% accuracy with k-means)

**Registry reusability test (Weeks 3-4)**

**Goal:** Test whether analyzers are actually reusable across similar repos.

**Test procedure:**
1. Select 3 clusters with high similarity (e.g., "FastAPI + SQLAlchemy + React" repos)
2. For each cluster:
   - Pick reference repo (Repo A)
   - Hand-tune capsule plan for Repo A (goal: >0.85 precision)
   - Apply same capsule to 5 other repos in cluster (Repos B, C, D, E, F)
3. Measure:
   - **Success rate:** Does it run without errors? (% repos)
   - **Edit distance:** How many changes to capsule_plan.json needed? (line diffs)
   - **Feature quality:** Precision/recall on ground truth (if available)
   - **Manual effort:** Hours to make it work (subjective but logged)

**Example results table:**

| Cluster | Reference Repo | Reuse Success Rate | Avg Edits Needed | Avg Precision | Manual Effort |
|---------|---------------|-------------------|------------------|---------------|---------------|
| FastAPI-SQL-React | repo-a | 4/5 (80%) | 3 lines | 0.78 | 30 min |
| Electron-IPC | repo-x | 2/5 (40%) | 15 lines | 0.62 | 2 hours |
| Next.js-Vercel | repo-y | 5/5 (100%) | 0 lines | 0.82 | 0 min |

**Decision criteria:**

| Success Rate | Edits Needed | Feature Quality | Decision |
|--------------|--------------|-----------------|----------|
| >70% | <5 lines | >0.7 precision | **Build full registry (B2)** – Analyzers are reusable |
| 50-70% | 5-15 lines | 0.5-0.7 | **Caution:** **Borderline** – Build lightweight GitHub repo, not full infra |
| <50% | >15 lines | <0.5 | **Would HALT:** Skip registry** – Analyzers too bespoke, manual authoring only |

**Lightweight alternative (if borderline or fail):**
* Create GitHub repo: `hypergumbo-community/capsule-examples`
* Organized by framework: `fastapi/`, `flask/`, `electron/`, `nextjs/`, etc.
* README: "These are starting points, expect 5-15 line customizations"
* Community PRs welcome
* **Effort:** 1 month (vs 9-12 for full registry)
* **Value:** 70% of benefit (proven patterns) without infrastructure burden

**Deliverable:**
* Similarity matrices (heatmaps, dendrograms)
* Reusability test results (table above)
* Decision recommendation: Full registry, lightweight, or skip
* Technical report (10-15 pages) with methodology and findings

**If validation fails:** Save 11 months by not building full B2 registry. Use lightweight GitHub alternative instead.

#### Month 7-8: Integration + Decision Report
* Integrate all prototypes
* Run full evaluation protocol (independent evaluators)
* Generate decision report
* **Deliverable:** Go/No-Go recommendation for B1

#### B0 Evaluation Protocol (Month 6-8)
**Critical:** Define how we'll measure success **before** running experiments (prevents cherry-picking).

**Pre-registration:**
1. Publish evaluation protocol at https://hypergumbo.iterabloom.com/eval/b0
2. Specify: Test repos (names), metrics (formulas), thresholds (numbers)
3. Commit to protocol (no changes after data collection starts)

**TypeScript evaluation:**
- Test set: 20 TypeScript repos (names listed in protocol)
- Ground truth: Hand-verified call graphs for 10 entry points per repo
- Metrics: Precision, recall, F1 score
- Baseline: Spec A's AST-only analysis on same repos
- Threshold: >0.80 precision (if lower, investigate why)

**Context Router evaluation:**
- Test set: 50 edit tasks across 3 agents (HumanEval-style, but for code editing)
- Metrics: Token count, correctness (human eval), task completion time
- Baseline: Naïve slicing (include full files)
- Threshold: >30% token reduction with same/better correctness

**HTTP linker evaluation:**
- Test set: 10 full-stack repos (FastAPI + React/Vue, known route→handler pairs)
- Ground truth: Hand-verified links
- Metrics: Precision (FP rate), recall
- Variants: Annotation-assisted vs. pure inference
- Threshold: <20% FP rate with annotations

**Independent evaluators:** Hire 2 external developers (not project team) to run protocol and verify results.

**Report template:** Results, analysis, decision recommendation (go/no-go for B1).

#### Success Criteria (gates for B1)
* TypeScript typed edges: >0.80 precision vs Spec A baseline
* Context Router: >30% token reduction validated by 3+ agents (A/B tested)
* HTTP linker: <20% false positive rate (annotation-assisted)
* Registry validation: Similarity >0.7 for 50%+ of repo pairs AND reusability >70%
* All prototypes completed within 8 months (±2 weeks acceptable)
* Team retention: 2/3 engineers commit to B1 (continuity)

**If any criterion fails:**
* TypeScript precision <0.80 → Investigate why; may need different approach (tsserver integration harder than expected?)
* Token reduction <30% → Re-evaluate Context Router value; maybe agents don't need it?
* HTTP linker FP >20% → Annotation-first only, defer inference to B1.5
* Registry similarity <0.5 OR reusability <50% → Cancel B2, use lightweight alternative
* Timeline slip >1 month → Re-estimate B1 (risk of underestimation)

**Go/No-Go decision meeting**: After Month 8, stakeholders review report, decide whether to proceed to B1.

**If B0 fails:** Pivot to "expose richer IR, let agents do slicing" (simpler model)

---

### **B1: Typed IR + Basic Linkers (15 months, 3-5 engineers)**
**Prerequisites:**
* B0 completed successfully (all gates passed)
* Design partners committed for full B1 duration (3+ orgs, written agreements)
* Engineering team hired/allocated (3-5 engineers available for 15 months)
* Funding secured for 15-month project

#### Phase 1: Typed IR Foundation (months 1-5)
* Internal IR schema v2 (typed symbols, resolved calls)
* TypeScript analyzer using `tsserver` (subprocess orchestration, 4 weeks for this)
* Python analyzer using `pyright` (opt-in, subprocess)
* Quality scoring framework (evidence + resolution confidence)
* Mixed-fidelity graph merging (AST 0.7 + typed 0.95 edges)
* IR export view (full detail, protobuf or JSON)
* **Tests:** Schema validation, merging correctness, precision benchmarks
* **Deliverable:** IR export view + backward-compatible behavior_map.json

**Milestone gate:** >0.85 precision on TypeScript test suite (100 repos)

#### Phase 2: Cross-Language Linkers (months 5-15)
**Months 5-8: HTTP linker (annotation-first, simple inference)**

**In scope:**
* **String literal matching:**
  ```python
  # Backend
  @app.get("/users/{id}")
  def get_user(id): ...
  
  # Frontend
  fetch("/users/123")  # Match!
  ```
* **Simple path variables:**
  - Backend: `/users/{id}`, `/posts/{post_id}/comments/{comment_id}`
  - Frontend: `/users/${userId}`, template literals only
  - Matching: Structural equivalence (variable names can differ)
* **HTTP method matching:**
  - Backend: `@app.get`, `@app.post`, `app.route(..., methods=["POST"])`
  - Frontend: `fetch(..., {method: "POST"})`, `axios.get(...)`, `$.ajax({type: "GET"})`
* **Framework support:**
  - Python: FastAPI, Flask (decorators and function-based routes)
  - JavaScript: Express (`app.get`, `router.post`), Next.js (file-based routing)
  - Client: fetch, axios, XMLHttpRequest, jQuery AJAX
* **Annotation overrides:**
  ```python
  @hypergumbo.http_route("/api/users", methods=["GET", "POST"])
  def handle_users():
      # Explicit route when decorator is dynamic
      ...
  ```
* **Confidence scoring:**
  - Exact match (literal + method): 0.85
  - Path match with method mismatch: 0.50
  - Annotation-provided: 0.95

**Out of scope (defer to B1.5 or later):**
* **Would HALT:** Constant propagation:**
  ```python
  BASE_URL = "/api/v1"
  @app.get(f"{BASE_URL}/users")  # Would need dataflow to resolve BASE_URL
  ```
* **Would HALT:** Dynamic route construction:**
  ```python
  for entity in ["users", "posts"]:
      app.route(f"/{entity}", ...)(handler)  # Loop-generated routes
  ```
* **Would HALT:** Middleware/proxy rewriting:**
  ```python
  app.use("/api", proxy("http://backend:5000"))  # Path transformations
  ```
* **Would HALT:** GraphQL, tRPC, gRPC:**
  - Different paradigm (schema-based, not route-based)
  - Defer to dedicated linkers in B1.5 or B2

**Month 6 checkpoint (decision gate):**

Measure on 10 full-stack repos (from benchmark suite):
- **Precision:** TP / (TP + FP) - Are matched links correct?
- **Recall:** TP / (TP + FN) - Are we finding all links?
- **False positive rate:** FP / (FP + TN)

**Thresholds:**
* Precision >0.80 with annotations: Continue
* Precision >0.60 without annotations (inference): Continue
* **Caution:** FP rate 20-30%: Document limitations, ship anyway
* **Would HALT:** FP rate >30%: **Trigger fallback**

**Fallback options (if Month 6 fails):**

**Option 1: Annotation-only (no inference)**
* Ship only `@hypergumbo.http_route` annotation support
* Inference mode disabled (or emits confidence <0.5)
* Document: "Requires annotations for precision >0.80"
* Provide tooling: `hypergumbo link-suggest` shows candidate matches, user confirms
* **Timeline impact:** Save 2 months (skip inference implementation)

**Option 2: Defer to B1.5**
* HTTP matching actually requires dataflow (resolving `BASE_URL + "/users"`)
* Skip in B1, revisit in B1.5 when dataflow analysis available
* Document in B1 release notes: "HTTP linking coming in B1.5"

**Option 3: Accept higher FP rate**
* Ship inference with confidence <0.60 for fuzzy matches
* Document clearly: "Inference mode has ~25% false positive rate; use annotations for critical links"
* Allow `--min-confidence=0.8` flag to filter out inference-only edges
* **User choice:** See all matches (noisy) or high-confidence only (sparse)

**Decision:** End of Month 8. If FP rate still >30% and no path to fix, choose Option 1 (annotation-only) or Option 2 (defer).

**Tests:**
* Unit tests: 50 route patterns (FastAPI, Flask, Express, Next.js)
* Integration tests: 10 full-stack repos from benchmark
* Precision/recall measured against ground truth
* False positive analysis (manual review of 100 matches)

**Months 9-12: IPC linker (Electron focus)**
* Main ↔ renderer message channels
* `ipcMain.on` ↔ `ipcRenderer.send` matching
* Event name normalization
* **Tests:** Precision on 10 Electron apps

**Deliverable:** Multi-language microservice repos fully analyzed

**Milestone gate:** Successfully link 3/3 design partner repos (HTTP + IPC)

**DEFERRED to B1.5: Inter-Process Call Detection**

Beyond HTTP and Electron IPC, microservice architectures use many inter-process communication patterns. These are deferred to B1.5 due to complexity and dataflow requirements.

**Message Queue Linkers:**
* **Redis pub/sub:**
  ```python
  # Publisher (service A)
  redis.publish("user.created", json.dumps(user))

  # Subscriber (service B)
  pubsub.subscribe("user.created")  # Match!
  ```
  - Detection: Match channel name strings
  - Confidence: 0.80 (string literal), 0.50 (variable)
  - Frameworks: redis-py, ioredis, node-redis

* **RabbitMQ/AMQP:**
  ```python
  channel.basic_publish(exchange='', routing_key='task_queue', body=msg)
  channel.basic_consume(queue='task_queue', on_message_callback=callback)
  ```
  - Detection: Match routing_key/queue names
  - Support: pika (Python), amqplib (Node.js)

* **Kafka:**
  ```python
  producer.send('user-events', value=event)
  consumer.subscribe(['user-events'])
  ```
  - Detection: Match topic names
  - Support: kafka-python, kafkajs

**RPC Linkers:**
* **gRPC/Protobuf:**
  - Parse `.proto` files for service definitions
  - Match `stub.MethodName()` calls to service implementations
  - Requires: Protobuf parser, service registry
  - Confidence: 0.95 (schema-validated)

* **JSON-RPC / XML-RPC:**
  ```python
  client.call("user.get", params={"id": 123})
  @rpc_method("user.get")
  def get_user(id): ...
  ```
  - Detection: Match method name strings
  - Lower confidence (0.60) due to dynamic nature

**Schema-Based Linkers:**
* **GraphQL:**
  ```javascript
  // Client
  query GetUser { user(id: 1) { name } }

  // Server resolver
  const resolvers = { Query: { user: (_, {id}) => db.getUser(id) } }
  ```
  - Parse `.graphql` schema files
  - Match query/mutation names to resolver implementations
  - Requires: GraphQL parser, schema introspection

* **SQL/Database:**
  ```python
  # Writer (service A)
  db.execute("INSERT INTO events ...")

  # Reader (service B)
  db.execute("SELECT * FROM events WHERE ...")
  ```
  - Detection: Shared table access patterns
  - Very low confidence (0.30) without schema context
  - Requires: SQL parser, schema tracking

**WebSocket Linkers:**
```javascript
// Server
ws.on('message', (data) => { if (data.type === 'chat') ... })

// Client
ws.send(JSON.stringify({type: 'chat', text: '...'}))
```
- Detection: Match message type/event fields
- Requires: JSON structure inference
- Confidence: 0.50 (heuristic matching)

**Why defer to B1.5:**
1. Many patterns require dataflow analysis (resolving variable values)
2. Schema parsers needed (Protobuf, GraphQL, SQL)
3. Lower confidence without type information
4. Each pattern needs dedicated detection + matching logic

**B1.5 Implementation priority:**
1. gRPC/Protobuf (high value, schema-validated = high confidence)
2. Message queues (common in microservices)
3. GraphQL (growing adoption)
4. WebSocket (case-by-case)
5. SQL-mediated (lowest priority, very noisy)

#### Phase 3: Basic Context Assembly (months 10-12, parallel with Phase 2)

**SCOPE: Query → Slice only** (no natural language, no dataflow, no impact)

**Deliverables**:
* Query DSL (symbol/file/route-based)
  - Example: `{"entrypoint": "function:myapp.auth.login", "hops": 3, "max_files": 20}`
* Slice assembly from typed graph (BFS/DFS)
* Context bundle view (minimal code + tests)
* Token budget optimization (target: <8K tokens for simple edits)
  - Simple truncation: BFS until token limit hit
  - Deterministic ordering: Sort by (edge confidence, distance from entry)
  - No ML-based ranking (defer to B1.5)
* Agent SDK (Python + JavaScript clients)

**NOT in B1 (deferred to B1.5)**:
* **Would HALT:** Natural language query parsing ("show me the auth flow")
* **Would HALT:** Dataflow slicing (taint tracking, reaching definitions)
* **Would HALT:** Impact zone prediction ("what could break if I change this")
* **Would HALT:** Invariant inference from tests
* **Would HALT:** Validator pass (re-run analysis after edits)

**Why defer**: These features require research (months 5-9 in B1.5 estimate); including in B1 would extend timeline to 18+ months.

**Validation with agents**: Even basic slicing should provide value. If agents report "not useful without natural language queries," that signals B1.5 is necessary.

**Tests**: Bundle correctness, token counting, reproducibility, deterministic ordering

**Milestone gate:** 3+ agents integrated, report >30% token reduction vs naïve approach

#### Phase 4: Validation + Hardening (month 13-15)
* Benchmark suite expansion (precision/recall tracking)
* Performance optimization (lazy loading, caching)
* CI integration (GitHub Actions, GitLab CI)
* SARIF output view (code scanning format)
* Documentation (API docs, migration guide from Spec A)
* **Deliverable:** Production-ready B1 release

**Milestone gate:** Zero regressions vs Spec A on compatibility suite

#### Success Criteria (B1)
* >0.85 precision on typed call graphs (TypeScript + Python)
* HTTP + IPC linkers: >0.80 precision on design partner repos
* Basic context assembly: >30% token reduction validated by agents (A/B tested)
* 100+ repos analyzed with B1 (upgrades from Spec A)
* Zero breaking changes to Spec A compatibility

**Total B1 timeline: 15 months**

**Parallelization note**: Phase 2-3 overlap requires 4+ engineers. If only 3 engineers available, timeline extends to 18 months.

### **Decision Point: Do We Need B1.5?**
**Prerequisites for starting B1.5**:
* B1 shipped and stable (6+ months in production)
* Evidence B1's basic slicing is insufficient:
  - Agent developers request "smarter context" (specific feature requests logged)
  - Quantitative gap: Agents with B1 still have >X% hallucination rate, and analysis shows better slicing would help
  - Design partners willing to co-develop B1.5 features (3+ committed)
* Funding secured for 18-month project
* Engineering capacity: 3-4 engineers available
* B0 dataflow prototype showed feasibility (>0.7 precision on known taint flows)

**Alternative if B1.5 not needed**:
* Agents report B1 slicing is "good enough"
* Focus on other priorities (more language support, performance, registry)
* Defer advanced context routing indefinitely

**Alternative if B1.5 needed but risky**:
* **Agent-guided slicing**: Agents specify hops/filters via DSL, tool doesn't infer
* Simpler, lower risk, still provides value
* Example: Agent says "show me callees of X within 2 hops, excluding tests," tool executes precisely

**Decision meeting**: After B1 is 6 months in production, review evidence and decide.

### **B1.5: Smart Context Router (18 months, 3-4 engineers)**
**Caution:** COMPLEXITY WARNING - This is research-heavy. Even with B0 prototype validation, dataflow slicing in production (handling large codebases, timeouts, edge cases) commonly takes 12+ months. Budget 18 months to avoid schedule pressure.

**Prerequisites:**
* B1 shipped and stable (6+ months in production)
* Evidence that basic slicing is insufficient (agents request smarter context)
* Funding secured for research-heavy project
* B0 dataflow prototype showed >0.7 precision
#### Phase 1: Query Intelligence (months 1-4)
* Natural language query → graph query translation
* Embedding-based similarity search (symbol/file lookup)
* Query spec compilation + validation
* **Tests:** Query parsing accuracy on 100 natural language examples

#### Phase 2: Dataflow Slicing (months 5-9)
**Caution:** HIGHEST COMPLEXITY COMPONENT
* Reaching definitions analysis (with timeouts)
* Taint tracking (sources → sinks)
* Partial results when timeout hit
* Explicit "slicing_incomplete" flags in output
* **Tests:** Precision on known taint flows, timeout behavior

#### Month 6 Checkpoint: Dataflow Feasibility Gate
**Caution:** CRITICAL DECISION POINT - Dataflow analysis is NP-hard. Even heuristics may not scale.
**Measure on 20 mid-sized repos (500-2000 files):**
**1. Timeout rate:**
* Query: "Find all data flows from user input to database query" (typical taint tracking)
* Timeout threshold: 30 seconds per query
* Measure: % of queries that timeout
* **Thresholds:**
  - <10%: On track, continue development
  - 10-30%: **Caution:** Warning zone, optimize algorithm aggressively
  - 30-50%: 🔶 Concerning, evaluate if fixable in 2 months
  - >50%: 🚨 **Trigger fallback evaluation**
**2. False positive rate (among non-timed-out queries):**
* Compare dataflow results to ground truth (hand-verified taint flows)
* False positive: Dataflow claims A→B link but manual analysis shows no path
* **Thresholds:**
  - <15%: Excellent, continue
  - 15-30%: Acceptable, document limitations
  - 30-40%: **Caution:** Marginal, investigate if improvements possible
  - >40%: 🚨 **Trigger fallback evaluation**
**3. Memory usage:**
* Measure peak memory during dataflow queries
* **Thresholds:**
  - <2GB median: Good
  - 2-8GB median: Acceptable with warnings ("dataflow queries require 8GB RAM")
  - 8-16GB median: **Caution:** High, limits usability (many dev machines <16GB)
  - >16GB median: 🚨 **Trigger fallback evaluation**

**Fallback trigger conditions (Month 6):**

**Immediate fallback (any of these):**
* Timeout rate >50%
* False positive rate >40%
* Memory usage >16GB (90th percentile)

**2-month fix attempt (any of these):**
* Timeout rate 30-50%
* FP rate 30-40%
* Memory 8-16GB but with clear optimization path

**Continue (all of these):**
* Timeout <30%
* FP rate <30%
* Memory <8GB median

**Fallback Decision Matrix:**

| Scenario | Timeout | FP Rate | Memory | Decision |
|----------|---------|---------|--------|----------|
| Best case | <10% | <15% | <2GB | Continue to Month 9 |
| Acceptable | 10-30% | 15-30% | 2-8GB | Continue, optimize |
| Fixable | 30-50% | 30-40% | 8-16GB | 🔶 2-month push, re-evaluate Month 8 |
| Fallback A | >50% | any | any | **Would HALT:** Syntax-only slicing** |
| Fallback B | any | >40% | any | **Would HALT:** Annotation-required** |
| Fallback C | any | any | >16GB | **Would HALT:** LLM-assisted guessing** |

### Fallback A: Syntax-Only Slicing (if timeouts unacceptable)

**What we ship:**
* B1.5 without dataflow analysis
* Rename to "B1.1" (incremental update, not full B1.5)
* "Smart context" uses call graph reachability only (no taint tracking)
* Impact prediction uses AST dependencies (imports, calls) not data flows

**Limitations we document:**
* "What could break" predictions are conservative (may over-include code)
* Taint tracking not available ("cannot trace password from input to database")
* Agent recommendation: Use for structural queries, not data flow queries

**Timeline impact:**
* Save 6 months (Month 7-12 now unnecessary)
* Ship B1.1 instead of B1.5
* Revisit dataflow in 2+ years when research advances (or never)

**Value retained:**
* Natural language queries still work (symbol/file lookup)
* Call graph slicing still valuable
* Impact zones still useful (conservative is safe)

### Fallback B: Annotation-Required Dataflow (if false positives unacceptable)

**What we ship:**
* Dataflow analysis only for annotated code
* Developers explicitly mark sensitive flows:
  ```python
  @hypergumbo.data_flow(
      sources=["request.user_id", "request.session"],
      sinks=["db.query", "cache.set"]
  )
  def get_user_profile(request):
      user_id = request.user_id  # Source
      profile = db.query("SELECT * FROM profiles WHERE id = ?", user_id)  # Sink
      return profile
  ```
* Tool validates annotations (syntactic checks)
* Trusts developer (no inference, no false positives)

**Effort reduction:**
* No need to solve NP-hard whole-program analysis
* Precision guaranteed (annotations are ground truth)
* Simpler implementation (3 months vs 9 months)

**Developer burden:**
* Must annotate sensitive code (10-50 annotations per medium repo?)
* Learning curve (what counts as "source" vs "sink"?)
* Maintenance (keep annotations up-to-date)

**When to use:**
* Security-critical applications (taint tracking for XSS, SQL injection)
* Teams with engineering discipline (maintain annotations)
* Prefer precision over coverage

### Fallback C: LLM-Assisted Slicing (if all else fails)

**What we ship:**
* Use LLM to predict "what code might be relevant" for a query
* Validate predictions with call graph (prune hallucinations)
* Hybrid: LLM guesses, static analysis verifies

**Algorithm:**
```python
def llm_assisted_slice(query: str, ir: AnalysisIR) -> List[NodeID]:
    # Step 1: LLM predicts relevant symbols
    prompt = f"""
    Given this query: "{query}"
    And this symbol table: {ir.symbols.names}
    Which symbols are likely relevant? Return as JSON list.
    """
    llm_predictions = call_llm(prompt)
    
    # Step 2: Validate predictions exist in IR
    valid_nodes = [n for n in llm_predictions if n in ir.symbols]
    
    # Step 3: Expand via call graph (prune hallucinations)
    slice = bfs_expand(valid_nodes, hops=3)
    
    return slice
```

**Pros:**
* Works even when dataflow intractable
* Handles natural language queries well
* Lower precision than true dataflow, but better than pure syntax

**Cons:**
* Costs $0.10-0.50 per query (LLM API calls)
* False positive rate ~25% (LLM guesses wrong)
* Requires API key (not offline-by-default)
* Non-deterministic (same query → different results)

**When to use:**
* Dataflow failed (too slow, too imprecise, too memory-hungry)
* Users willing to pay per-query cost
* Exploratory queries (not safety-critical)

**No fallback (cancel B1.5):**

If all fallbacks unacceptable:
* Return to B1 (basic context assembly)
* Document: "Advanced context routing deferred pending research advances"
* Agent users continue using B1 features (typed IR, basic slicing)
* Revisit in 2+ years (or if competitor solves it first)

**Value of B1 even without B1.5:**
* Typed call graphs (precision >0.85)
* Cross-language HTTP/IPC links
* Basic context assembly (token reduction >30%)
* Foundation for future capabilities

**This is why B1.5 is separate phase:** If it fails, B1 remains valuable.

#### Phase 3: Impact Prediction (months 10-12)
* Downstream caller analysis (who uses this symbol)
* Schema change detection (API surface changes)
* Test corpus correlation (which tests exercise this code)
* "What could break" checklist generation
* **Tests:** Recall on known breaking changes

#### Phase 4: Advanced Context Assembly (months 13-15)
* Invariant inference from test assertions
* Multi-file edit planning (ordered dependency updates)
* Token budget optimization with impact awareness
* **Tests:** End-to-end agent workflows

#### Month 16-18: Validation + hardening
* Polish, documentation, performance optimization

#### Deliverables
* Context Router API (natural language + graph queries)
* Dataflow slicing with explicit limits
* Impact prediction engine
* Advanced context bundle view
* Agent SDK with full capabilities

#### Success Criteria (B1.5)
* Query parsing: >0.80 accuracy on natural language → graph queries
* Dataflow slicing: >0.70 precision on taint tracking (where not timed out)
* Impact prediction: >0.60 recall on breaking change detection
* Token reduction: >50% vs naïve approach (maintained from B1)
* Agent A/B test: B1.5 advanced slicing vs. B1 basic slicing
  - Measured: Tasks requiring dataflow (e.g., "trace this tainted input"), token efficiency, correctness
  - Target: >50% token reduction on dataflow-relevant tasks, >85% correctness
  - Sample size: 30 tasks where dataflow matters (pre-selected)
  - Survey (qualitative): "Would you pay for B1.5 features?" >60% yes

**Total B1.5 timeline: 18 months**

### **B2: Registry + Social Layer (10-12 months OR 1 month, 2-3 engineers OR 1)**

**Prerequisites:**
* B1 shipped and stable (6+ months in production)
* B1.5 shipped OR validation shows basic slicing is sufficient
* **Registry validation completed in B0** showing:
  - Repo clustering: Similarity >0.7 for 50%+ of pairs in same category
  - Analyzer reusability: >70% success rate when applying capsules across similar repos
* Demonstrated demand: 10+ requests for "find analyzer for X" logged
* Community adoption: 100+ repos analyzed, 20+ custom capsule plans created
* Sharing activity: 5+ users informally sharing capsules via GitHub gists/repos

**If registry validation from B0 failed** (repos don't cluster OR reusability <50%):
* **Would HALT:** Don't build full registry (9-12 months wasted)
* Instead: Lightweight alternative (see below)

**Lightweight alternative** (1 month effort):
* GitHub repo: `hypergumbo-examples`
* Curated capsule plans for common stacks
* README with "choose your stack" guide
* Community can PR new examples
* No server infrastructure, no search, no benchmarks

**Only proceed with full B2 if B0 validation passed.**

#### Phase 1: Registry Infrastructure (months 1-3)
* Capsule/rule pack storage (S3 + CDN)
* Metadata database (Postgres: profiles, fingerprints, benchmarks)
* Search API (similarity scoring via vector embeddings)
* Upload/download API (authenticated)
* **Deliverable:** Alpha registry (invite-only, 50 users)

**Milestone gate:** 20+ capsules uploaded, search returns relevant results

#### Phase 2: Trust System (months 4-6)
* Signing infrastructure (Sigstore integration)
* Benchmark automation + fingerprinting
* Security scanning (dependency audit, resource limits)
* Compatibility matrix (schema versions)
* **Deliverable:** Beta registry (public read, curated write)

**Milestone gate:** Zero malicious capsules in review queue, 100% signed artifacts

#### Phase 3: Discovery + Governance (months 7-9)
* Similarity search (repo profile → analyzer recommendations)
* K-anonymity privacy model (cluster-based recommendations)
* Community ratings + reviews (verified download only)
* Benchmark leaderboards (precision, performance)
* Download stats + adoption tracking
* **Deliverable:** Public registry (open submission with review)

**Milestone gate:** 500+ downloads/week, 4.0+ avg rating on top capsules

#### Phase 4: Privacy + Federation (months 10-12)
* Self-hosted registry option (Docker Compose deployment)
* Federation protocol (multiple registries, trust delegation)
* Enterprise features (private capsules, audit logs)
* **Deliverable:** Enterprise-ready registry

**Milestone gate:** 3+ self-hosted deployments, federation working between instances

#### Success Criteria (B2)
* 500+ capsules in registry
* Similarity search: >0.80 precision on manual eval (recommendations are relevant)
* 100% of public capsules signed and benchmarked
* Zero security incidents (malware, data leaks)
* Capsule quality (automated):
  - Benchmark pass rate: >90% of capsules pass standard benchmark suite
  - Error rate: <5% of downloads result in crashes/failures
  - Security: 0 capsules with known CVEs or malware flags
* User satisfaction (manual eval):
  - Survey: "Did downloaded capsule work for your repo?" >70% yes
  - Survey: "Was it better than writing from scratch?" >60% yes
  - Repeat usage: >40% of users who download once, download again within 30 days
* 1000+ weekly downloads
* 50+ contributors (capsule authors)

**OR (if lightweight GitHub alternative):**
* 50+ example capsules in repo
* 100+ stars, 20+ contributors
* Active community discussions (10+ issues/month)

**Total B2 timeline: 10-12 months** (OR 1 month for lightweight GitHub alternative)

### **TOTAL B TIMELINE SUMMARY**

| Phase | Duration | Team Size | Prerequisites | **Risk Level** | **Cost Estimate** |
|-------|----------|-----------|---------------|----------------|-------------------|
| B0 (Research) | 8 months (2mo prep + 6mo engineering) | 2-3 engineers + contractors | Spec A validated | **Medium** (prototypes may fail) |  |
| B1 (Typed IR) | 15 months | 3-5 engineers | B0 gates passed | **Medium** (cross-lang linkers hard) |   |
| B1.5 (Smart Context Router) | **18 months** (firm, not 15) | 3-4 engineers | B1 shipped + demand validated | **High** (dataflow is research-hard) |   |
| B2 (Registry) | 10-12 months OR 1 month | 2-3 engineers OR 1 | B1/B1.5 shipped + **B0 reusability validated** | **Low** (if validation done) |   |

**Timeline scenarios:**

| Scenario | Path | Duration | Cost |
|----------|------|----------|------|
| **Minimum (with fallbacks)** | B0 → B1 → B2-lite | 8 + 15 + 1 = **24 months** |   |
| **Realistic (B1.5 succeeds)** | B0 → B1 → B1.5 → B2-lite | 8 + 15 + 18 + 1 = **42 months** |   |
| **Full vision** | B0 → B1 → B1.5 → B2-full | 8 + 15 + 18 + 11 = **52 months** |   |
| **Conservative (2-eng team)** | Same as realistic but extended | **60 months** | Same cost, longer time |

**Recommendation:** Don't promise delivery <48 months for full vision. Plan for 52 months, celebrate if done in 42.

### Team Size Impact on Timeline

**Same scope, different team sizes:**

| Team Size | B0 | B1 | B1.5 | B2-lite | **Total** | **Why** |
|-----------|----|----|------|---------|-----------|---------|
| 2 engineers | 8mo | 20mo | 24mo | 1mo | **53 months** | Less parallelization, more context switching |
| 3 engineers | 8mo | 15mo | 18mo | 1mo | **42 months** | Balanced (prototyping + implementation) |
| 4 engineers | 8mo | 12mo | 16mo | 1mo | **37 months** | Good parallelization (3 tracks in B1) |
| 5 engineers | 8mo | 12mo | 15mo | 1mo | **36 months** | Diminishing returns (coordination overhead) |

**Recommended:** 3-4 engineers (sweet spot for coordination vs. parallelization)

**Coordination overhead:**
* 2 engineers: ~5% overhead (rare sync needed)
* 3 engineers: ~10% overhead (weekly syncs)
* 4 engineers: ~15% overhead (daily standups, more PRs)
* 5 engineers: ~20% overhead (coordination becomes significant)

**Parallelization limits:**
* B0: 2-3 engineers (prototypes are independent)
* B1 Phase 1-2: 3-5 engineers (IR + TypeScript + Python + HTTP linker)
* B1 Phase 3-4: 2-3 engineers (context assembly + validation)
* B1.5: 3-4 engineers (NL query + dataflow + impact + assembly)
* B2: 2-3 engineers (infra + trust + discovery + privacy)

**If forced to 2 engineers:**
* B1 extends to 20 months (serialize TypeScript, Python, HTTP linker)
* B1.5 extends to 24 months (dataflow is not parallelizable)
* Total: 53 months (~4.5 years)
* Recommendation: Don't commit to B1.5 with 2 engineers (risk of burnout)

**If blessed with 5 engineers:**
* Only saves 6 months total (coordination overhead offsets parallelization)
* Not worth hiring 2 more people
* Better: 3 engineers + 1 PM + 1 designer (UX for agent integrations)

## 9) Key risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **B0 prototypes fail to validate feasibility** | Medium | **Critical** | Explicit decision gates after each prototype; prepared to pivot to simpler model if Context Router proves too hard |
| **Context Router complexity underestimated** | **High** | **Critical** | Moved to separate B1.5 phase; B1 ships without it; gate B1.5 on B0 validation + agent demand; fallback: agent-guided slicing |
| **Cross-language linkers take 2-3× longer than planned** | **High** | High | Expanded timeline to 8 months for HTTP+IPC only; defer SQL/Protobuf/GraphQL to B1.5; annotation-first approach; alternative paths defined |
| IR drift across language tools (tsserver, pyright versions) | High | Critical | Version-lock tools as optional deps; treat as plugins not core; quarterly upgrade cycle with regression tests |
| Cross-language linkers too brittle (false positives) | High | Medium | Start with annotation-based hints, not inference; require developer confirmation; track precision metrics; alternative paths if FP >20% |
| Registry spam/malware | Medium | High | Require benchmarks + security scan from day 1; sandboxing mandatory for untrusted capsules; signing/provenance |
| **Registry value not validated (analyzers too bespoke)** | Medium | High | **B0 validation in Month 5-6**; verify analyzers cluster AND reusable; have GitHub alternative ready |
| Agent context quality unmeasurable | Medium | High | Pre-register eval protocols; A/B testing; objective metrics (token reduction, correctness, hallucination rate) |
| **B1.5 timeline slips due to dataflow complexity** | **High** | High | B0 prototype validates feasibility first; strict timeouts + partial results acceptable; explicit incomplete flags; fallback plans |
| B1 timeline slips due to tsserver/pyright integration | High | Medium | B0 prototypes de-risk this; allocate 2× time for tool integration; have fallback (ship without Python types) |
| **Design partners drop out mid-project** | Medium | **Critical** | Identify 5 partners, need only 3 to commit; if <3 remain, pause and recruit more; contractual commitments (not just verbal); quarterly check-ins on satisfaction |
| Privacy breach (sanitization fails) | Low | **Critical** | K-anonymity approach (no individual profiles); opt-in only for personalized; security audits |

## 10) Success criteria

### B0 (Research Phase)

**Technical:**
* TypeScript typed edges: precision >0.80 vs AST baseline
* Context Router prototype: >30% token reduction in agent tests (A/B tested)
* HTTP linker prototype: <20% false positive rate
* Registry validation: Repo similarity >0.7 for 50%+ of pairs AND reusability >70%
* All 3 prototypes completed on schedule (8 months including prep)

**Decision:**
* Go/No-Go report approved by stakeholders
* Realistic B1 timeline and budget agreed upon

### B1 (Typed IR + Basic Linkers)

**Technical:**
* >0.85 precision on typed call graphs (TypeScript, Python)
* Mixed-fidelity graphs merge without conflicts
* HTTP linker: >0.80 precision on design partner repos
* IPC linker: >0.80 precision on Electron apps
* Basic context assembly: <8K tokens for simple edits

**Adoption:**
* 3+ agents integrated with context assembly API
* 100+ repos analyzed with B1 (upgrades from Spec A)
* 5+ design partner case studies published
* Agent A/B test: Same tasks with B1 slicing vs. naïve approach (full files)
  - Measured: Token count, edit correctness (human eval), hallucination rate
  - Target: >30% token reduction, >80% correctness (maintained), <20% hallucination rate (vs. >40% baseline)
  - Sample size: 50 tasks across 3 agents
  - Pre-register protocol at https://hypergumbo.iterabloom.com/eval/b1

**Quality:**
* Zero breaking changes to Spec A compatibility
* <10% performance regression vs Spec A on small repos

### B1.5 (Smart Context Router)

**Technical:**
* Natural language query parsing: >0.80 accuracy
* Dataflow slicing: >0.70 precision (where not timed out)
* Impact prediction: >0.60 recall on breaking changes
* Token reduction: >50% vs naïve approach

**Adoption:**
* 5+ agents using advanced context features
* Agent A/B test: B1.5 advanced slicing vs. B1 basic slicing (pre-registered metrics)

**Quality:**
* Explicit partial-results flags work correctly (timeout behavior)
* <5 minutes for complex queries (90th percentile)

### B2 (Registry)

**Technical:**
* 500+ capsules in registry
* Similarity search: >0.80 precision on manual eval
* 100% of public capsules signed and benchmarked
* Zero security incidents (malware, data leaks)

**Adoption:**
* 1000+ weekly downloads from registry
* 50+ contributors (capsule authors)
* 3+ self-hosted registry deployments

**Quality:**
* Capsule quality metrics (automated: >90% pass benchmarks, <5% error rate, 0 CVEs)
* User satisfaction surveys (>70% "it worked", >60% "better than scratch")
* 95% uptime SLA for hosted registry

**OR (if lightweight GitHub alternative):**
* 50+ example capsules in repo
* 100+ stars, 20+ contributors
* Active community discussions (10+ issues/month)

## Appendix A: Comparison to Spec A

| Aspect | Spec A (MVP) | Spec B (Full) |
|--------|--------------|---------------|
| **Timeline** | 9 weeks | 24-52 months (phased) |
| **Phases** | Single release | B0 (8mo) → B1 (15mo) → B1.5 (18mo) → B2 (10-12mo) |
| **Team size** | 1-2 engineers | 2-5 engineers (phased) |
| **Analysis fidelity** | Best-effort AST | Typed + dataflow (B1.5) |
| **Languages** | Python, JS/TS, HTML | +TypeScript (typed), Python (typed), later: Go, Rust |
| **Cross-language** | None | HTTP, IPC (B1); SQL, Protobuf, GraphQL (B1.5) |
| **Context router** | Manual slicing (BFS/DFS) | Basic assembly (B1) → Smart routing (B1.5) |
| **Registry** | None | Full social layer (B2) or lightweight GitHub alt |
| **Execution model** | `python_script` only | Multiple (script, toolchain_bundle, container, daemon) |
| **Privacy** | Local-only | Local + opt-in k-anonymity sharing (B2) |
| **Incremental** | Full re-analysis | Full re-analysis (same, defer incremental) |
| **Trust model** | Local trust | Sigstore + transparency log (B2) |
| **Prerequisites** | None | Spec A validated + 3-6 months production usage |
| **Decision gates** | 2 (Week 0 gates) | 4 (after B0, after B1-P1, after B1, before B2) |

## Appendix B: Evolution path from Spec A

### What's preserved
* **behavior_map.json schema**: Backward compatible
* **Node ID format**: Location-based (stable)
* **Capsule concept**: Extended, not replaced
* **Slicing primitives**: Same query specs
* **Confidence model**: Enhanced, not changed (via versioning)
* **Provenance fields**: execution_id, run_signature, origin_run_id
### What's enhanced
* **IR layer**: Exposed as `ir_export.json` view
* **Parsers**: AST parsers augmented with type checkers
* **Edges**: Mixed fidelity (0.7 AST + 0.95 typed in same graph)
* **Execution formats**: `python_script` + `toolchain_bundle` + `container`
### What's added
* **Cross-language linkers**: HTTP, IPC, SQL
* **Context router**: Query → minimal bundle (B1 basic, B1.5 advanced)
* **Registry**: Search, similarity, trust (B2)
* **SARIF output**: CI integration
* **Benchmark framework**: Quality metrics
### Migration path
1. **Spec A users upgrade to B1 Phase 1**: Install tsserver/pyright (optional), get higher-fidelity edges
2. **B1 Phase 3**: Start using context router for agent workflows
3. **B1.5**: Adopt advanced context features (if needed)
4. **B2 Phase 1**: Optionally publish capsules to registry
5. **B2 Phase 3**: Discover and reuse community capsules

**No breaking changes.** Spec A capsules continue working in B environment.

## Appendix C: Technology choices

### IR storage
* **Format**: Protocol Buffers (fast, versioned, language-neutral)
* **Fallback**: JSON (if protobuf adds friction)
### Registry backend
**Hosted (managed service)**:
* **Storage**: S3 (capsules/artifacts) + CloudFront (CDN)
* **Database**: PostgreSQL (metadata, profiles, benchmarks) + pgvector (similarity search)
* **Search**: Vector similarity via pgvector

**Self-hosting options**:
**Simple (SQLite + files)**:
* Storage: Local filesystem (capsules in `./storage/capsules/`)
* Database: SQLite (metadata in `./storage/registry.db`)
* Search: Full-text search via SQLite FTS (no vector similarity)
* Deployment: Docker Compose (3 services: web, worker, nginx)
* **Limitations**: No CDN (slower downloads), no vector search (simpler recommendations)
* **Suitable for**: Teams <50, internal capsule sharing

**Production (MinIO + Postgres)**:
* Storage: MinIO (S3-compatible, self-hosted)
* Database: PostgreSQL + pgvector
* Search: Vector similarity via pgvector
* Deployment: Kubernetes (complex) or Docker Compose (moderate)
* **Suitable for**: Orgs >50, enterprise deployments

**Important:** Kubernetes-based stack is **not** "simple Docker Compose." It's complex infrastructure.
### Signing/provenance
* **Primary**: Sigstore (Cosign for signing, Rekor for transparency log)
* **Fallback**: PGP/GPG (web-of-trust model)
### Containers
* **Runtime**: Docker or Podman
* **Format**: OCI images (industry standard)
### Language servers (B1)
* **TypeScript**: `tsserver` (official)
* **Python**: `pyright` (Microsoft, actively maintained)
* **Go**: `gopls` (official)
* **Rust**: `rust-analyzer` (official)
* **Java**: Eclipse JDT
### Orchestration
* **B1**: Simple subprocess management
* **B2**: Docker Compose (simple) or Kubernetes (production)

## Appendix D: Future Testing Enhancements

### LLM Integration Tests

Add optional integration tests that make real API calls to LLM providers (OpenRouter, OpenAI) to validate the `llm_assist` module end-to-end. These tests would:

* Use `@pytest.mark.integration` marker
* Skip automatically when API keys are not set
* Run only on explicit request (`pytest -m integration`)
* Use a dedicated test API key with rate limiting awareness
* Catch environment-specific issues (e.g., proxy configuration, API changes)

**Rationale:** Unit tests with mocks provide full coverage but cannot catch issues like the httpx/IPv6 CIDR proxy bug discovered during manual testing. Integration tests would provide additional confidence for real-world deployments.
