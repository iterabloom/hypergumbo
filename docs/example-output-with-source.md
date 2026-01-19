# Example: hypergumbo --with-source

This is the full terminal output of running `hypergumbo hypergumbo/ --with-source` (with default 4000 token budget).

The `--with-source` flag appends actual source code content for the most important files, useful when you want an LLM to understand implementation details.

Note: With `--with-source`, the token budget is divided between the overview sections and actual source code. At 4,000 tokens, you get a few small files. Use `-t 16000` or higher to include more source code.

---

```bash
(.venv) jgstern_agent@agent-vm-16:~$ hypergumbo hypergumbo/ --with-source
[100%] Complete in 71.7s            ETA 10s
[100%] Complete in 168.5s           TA 1s
# hypergumbo

hypergumbo hypergumbo is a local-first CLI that generates behavior maps and sketches from source code. Helps developers and LLMs quickly understand a codebase. > Requires Python 3.10+. Intel Mac users: Some tree-sitter packages lack x86_64 wheels.

## Overview
Python (90%), Markdown (6%), Yaml (2%)
335 files    (201 non-test + 134 test)
~130,729 LOC (~66,566 non-test + ~64,163 test)

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
- ... and 218 more files

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

### `src/hypergumbo/discovery.py`
- `find_files(repo_root: Path, patterns: list[str], excludes: list[str] …` (function) — Find files matching patterns while respecting exclude rules.

### `src/hypergumbo/analyze/js_ts.py`
- `analyze_javascript(repo_root: Path, max_files: int | None=…) -> JsAnalysisRes…` (function) — Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

### `src/hypergumbo/linkers/registry.py`
- `LinkerContext` (class) — Context passed to all linkers.

### `src/hypergumbo/taxonomy.py`
- `LanguageSpec` (class) — Specification for a language/file type.

### `src/hypergumbo/analyze/java.py`
- `analyze_java(repo_root: Path) -> JavaAnalysisResult` (function) — Analyze all Java files in a repository.

### `src/hypergumbo/catalog.py`
- `Pass` (class) — An analysis pass that can be applied to source code.

### `src/hypergumbo/sketch.py`
- `ConfigExtractionMode` (class) — Mode for extracting config file content.

### `src/hypergumbo/analyze/rust.py`
- `analyze_rust(repo_root: Path) -> RustAnalysisResult` (function) — Analyze all Rust files in a repository.

### `src/hypergumbo/analyze/julia.py`
- `_find_child_by_type(node: 'tree_sitter.Node', type_name: str) -> Optional['tre…` (function) — Find first child of given type.

### `src/hypergumbo/analyze/dart.py`
- `analyze_dart(repo_root: Path) -> DartAnalysisResult` (function) — Analyze Dart files in a repository.

### `src/hypergumbo/analyze/elixir.py`
- `analyze_elixir(repo_root: Path) -> ElixirAnalysisResult` (function) — Analyze all Elixir files in a repository.

### `src/hypergumbo/analyze/ruby.py`
- `analyze_ruby(repo_root: Path) -> RubyAnalysisResult` (function) — Analyze all Ruby files in a repository.

### `src/hypergumbo/analyze/php.py`
- `analyze_php(repo_root: Path) -> PhpAnalysisResult` (function) — Analyze all PHP files in a repository.

### `src/hypergumbo/analyze/go.py`
- `analyze_go(repo_root: Path, max_files: int | None=…) -> AnalysisResult` (function) — Analyze all Go files in a repository.

### `src/hypergumbo/analyze/c.py`
- `analyze_c(repo_root: Path) -> CAnalysisResult` (function) — Analyze all C files in a repository.

(... and 1726 more symbols across 111 other files)

## Additional Files

- `README.md`
- `docs/GOVERNANCE.md`
- `docs/LANGUAGES.md`
- `docs/adr/0001-portable-agent-instructions.md`
- `docs/example-output.md`
- `docs/history/planning-v1.md`
- `docs/LINKERS.md`
- `src/hypergumbo/frameworks/micronaut.yaml`
- `docs/example-output-with-source.md`
- `docs/adr/0005-sketch-budget-allocation.md`
- `docs/adr/0003-call-patterns-extension.md`
- `docs/future/registry-factory-vision.md`
- `docs/ARCHITECTURE.md`
- `AGENTS.md`
- `docs/schema.json`
- ... and 60 more files

## Source Files Content

------------------- START of src/hypergumbo/schema.py ------
` ` `
"""Schema versioning and behavior map factory.

This module defines the output schema version and provides a factory
for creating empty behavior map structures with all required fields.

Version Distinction
-------------------
**SCHEMA_VERSION vs Tool Version:**

- **SCHEMA_VERSION** (defined here): The output format version, embedded in
  every JSON output as `schema_version`. It only increments when there are
  breaking changes to the output schema (new required fields, changed field
  types, removed fields, etc.). Consumers can use this to check compatibility
  with their parsers.

- **__version__** (in __init__.py): The tool/package version. This increments
  with every release (new analyzers, bug fixes, performance improvements,
  CLI changes, etc.). It does NOT indicate output format changes.

These versions evolve independently. The tool can have many releases while
the schema stays stable if the output format doesn't change.

How It Works
------------
The behavior map is the primary output format for hypergumbo analysis.
This module defines several versioned schemes:

- **schema_version**: Overall format version (breaking changes increment minor)
- **confidence_model**: How confidence scores are computed
- **stable_id_scheme**: How stable_id hashes are generated
- **shape_id_scheme**: How shape_id (structure) hashes are generated
- **repo_fingerprint_scheme**: How repo state is fingerprinted for caching

new_behavior_map() returns an empty structure with all top-level fields
initialized, ensuring consistent output even for empty analyses.

Why This Design
---------------
- Explicit versioning enables consumers to detect format changes
- Scheme identifiers let consumers know how to interpret computed IDs
- Factory function ensures all required fields are present
- Separating schema from IR keeps output format concerns isolated
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

SCHEMA_VERSION = "0.2.0"
CONFIDENCE_MODEL = "hypergumbo-evidence-v1"
STABLE_ID_SCHEME = "hypergumbo-stableid-v1"
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v1"
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v1"


def _now_iso_utc() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_behavior_map() -> Dict[str, Any]:
    """
    Construct an empty behavior_map view with all required top-level fields.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "confidence_model": CONFIDENCE_MODEL,
        "stable_id_scheme": STABLE_ID_SCHEME,
        "shape_id_scheme": SHAPE_ID_SCHEME,
        "repo_fingerprint_scheme": REPO_FINGERPRINT_SCHEME,
        "view": "behavior_map",
        "generated_at": _now_iso_utc(),
        "analysis_incomplete": False,
        "analysis_runs": [],
        "profile": {},
        "nodes": [],
        "edges": [],
        "usage_contexts": [],
        "features": [],
        "metrics": {},
        "limits": {},
        "entrypoints": [],
    }
` ` `
------------------- END of src/hypergumbo/schema.py --------

------------------- START of src/hypergumbo/__init__.py ----
` ` `
"""Hypergumbo: Local-first repo behavior map generator.

This package provides static analysis tools for generating behavior maps
from source code repositories.

Version Note
------------
- **__version__**: The tool/package version. This version tracks CLI features,
  analyzer additions, and bug fixes. Updated with each release.

- **SCHEMA_VERSION** (in schema.py): The output format version. This version
  tracks breaking changes to the JSON output schema. Consumers should check
  schema_version in output to ensure compatibility.

These versions are independent. The schema version only changes when the output
format has breaking changes, while the tool version changes with any release.
"""
__all__ = ["__version__"]
__version__ = "1.0.0"
` ` `
------------------- END of src/hypergumbo/__init__.py ------

------------------- START of src/hypergumbo/linkers/__init__.py
` ` `
"""Cross-language linkers for hypergumbo.

Linkers create edges between symbols from different language analyzers,
enabling cross-language call graph construction.
"""
` ` `
------------------- END of src/hypergumbo/linkers/__init__.py


## Additional Files Content

                    How Representative Is This Sketch?
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 4,000t ┃ 16,000t ┃ 64,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Data Models              │    19% │     85% │    100% │ confidence mass │
│ Source Files             │    45% │     95% │    100% │ symbol mass     │
│ Key Symbols              │    25% │     36% │     45% │ symbol mass     │
│ Additional Files         │    44% │     45% │     45% │ symbol mass     │
│ Source Files Content     │   0.0% │    2.0% │     11% │ symbol mass     │
│ Additional Files Content │      - │     13% │     44% │ symbol mass     │
└──────────────────────────┴────────┴─────────┴─────────┴─────────────────┘

hypergumbo also created comparison sketches temporarily:
  4x budget (16,000t):  /tmp/hypergumbo_sketch_compare/sketch.16000.withsource.md
  16x budget (64,000t): /tmp/hypergumbo_sketch_compare/sketch.64000.withsource.md

To preserve them to cache:
  cp /tmp/hypergumbo_sketch_compare/sketch.16000.withsource.md /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/sketch.16000.withsource.md
  cp /tmp/hypergumbo_sketch_compare/sketch.64000.withsource.md /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/sketch.64000.withsource.md


[hypergumbo sketch] Generated 5
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/hypergumbo.results.16k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/hypergumbo.results.4k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/hypergumbo.results.64k.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/hypergumbo.results.json
  /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/results/feb0ab2c78be3ade/sketch.4000.withsource.md
  Output: stdout
  Embeddings cached: /home/jgstern_agent/.cache/hypergumbo/126efff9e65fd2d7/embeddings
(.venv) jgstern_agent@agent-vm-16:~$
```

