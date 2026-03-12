<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Hypergumbo Capsule System (Historical Design)

> **Note**: This document archives the original capsule system design from the Spec A architecture. The capsule system was designed under the assumption that users would need custom analyzers composed from building blocks. In practice, the general-purpose analyzer works well enough that custom composition isn't needed.
>
> **Current status**: The `init` command creates capsule files, but `run` ignores them. The capsule system is vestigial.
>
> **Future vision**: Registry and factory concepts moved to [Spec B](../hypergumbo-spec.md#spec-b).

## Original Design Intent

The capsule system was designed for a world where:
- Every repo needs a bespoke analyzer
- Users would compose passes/packs/rules via `init`
- `run` would execute that custom configuration
- A registry would let people share their custom analyzers

## Runner Interface (Not Implemented)

hypergumbo was intended to execute capsules through a runner abstraction selected by `capsule.json.format`.

Pseudo-interface:
```python
class CapsuleRunner(Protocol):
    def run(self, capsule_manifest: dict, repo_root: Path, out_path: Path) -> None:
        ...
```

## Analyzer Capsule Design

`.hypergumbo/` structure:

### `capsule.json` — Manifest

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

**Manifest fields**:
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

**Format types** (only `python_script` was ever used):
- `python_script` — Single file, minimal deps
- `toolchain_bundle` — Bundled with language server (never implemented)
- `container` — Docker/OCI image (never implemented)
- `daemon` — Long-running process (never implemented)

### Security Model (Never Enforced)

**Security fields**:
- `trust`: `"local_only"` (default), `"shared_unsigned"`, `"signed"`
- `network`: `"deny"` (default), `"allow"`
- `sandbox`: `"none"`, `"recommended"` (default), `"required"`

**validation_mode** (optional, default: `"strict"`):
- `"strict"`: Unknown passes/packs result in error
- `"permissive"`: Unknown components skipped with warning

**Validation enforcement (never implemented):**
```python
if validation_mode == "permissive" and trust != "local_only":
    raise SecurityError(
        "Permissive validation requires trust=local_only. "
        "For shared/registry capsules, use validation_mode=strict."
    )
```

**Planned sandboxing (never implemented):**
- `network: "deny"` → Soft enforcement only (code review)
- `sandbox: "recommended"` → Best-effort isolation
- Process isolation via subprocess
- Container-based execution for shared capsules

### `capsule_plan.json` — Composition Plan

Validated JSON selecting from pre-approved building blocks:
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
- `passes[]`: Core analyzers to run
- `packs[]`: Framework-specific feature bundles
- `rules[]`: Declarative patterns (entrypoints, excludes)
- `features[]`: Pre-computed slice queries

### `analyzer.py` — Stable Runner (Never Implemented)

Fixed script (same for all capsules) that would:
1. Load and validate `capsule_plan.json`
2. Orchestrate pass execution per plan
3. Compile IR → views
4. Write output files

### `catalog.json` — Building Block Registry

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

## Why It Wasn't Needed

The capsule system assumed:
1. Users need custom analyzers tailored to their specific repo
2. Composition from building blocks is necessary
3. A registry for sharing custom analyzers would be valuable

In practice:
1. `hypergumbo run .` works well for most repos out of the box
2. 67 language analyzers + 14 linkers + 37 framework patterns cover common cases
3. Nobody runs `init` first; they just run `run` directly
4. The "factory" was unnecessary when the "default product" is sufficient

## Vestigial Code

The following modules exist but are effectively unused:
- `plan.py` — Generates capsule_plan.json (called by `init`, ignored by `run`)
- `llm_assist.py` — LLM-assisted plan generation (proof of concept)
- `export.py` — Privacy-safe capsule export
- `catalog.py` — Pass/pack availability checking

The `init` command still works and creates these files, but they don't affect analysis.
