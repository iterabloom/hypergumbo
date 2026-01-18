# Example: hypergumbo --with-source

This is the full terminal output of running `hypergumbo hypergumbo/ --with-source` (with default 4000 token budget).

The `--with-source` flag appends actual source code content for the most important files, useful when you want an LLM to understand implementation details.

---

# hypergumbo

Two Outputs **Sketch** (`hypergumbo .`) — Token-budgeted Markdown sized for LLM context windows. Ranks symbols by graph centrality (★ = most connected). **Behavior map** (`hypergumbo run`) — Full JSON with all symbols, edges, and provenance tracking. Use this for programmatic analysis.

## Overview
Python (91%), Markdown (6%), Yaml (2%)
334 files    (200 non-test + 134 test)
~130,359 LOC (~66,204 non-test + ~64,155 test)

## Structure

```
hypergumbo/
├── .claude
│   └── settings.local.json
├── .github
│   └── workflows
│       ├── release-mirror.yml
│       └── [and 2 other items]
├── docs
│   ├── hypergumbo-spec.md
│   └── [and 18 other items]
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
└── [and 22 other items]
```

## Frameworks

- openai
- pytest
- pytorch
- transformers

## Tests

135 test files · pytest, unittest

*~92% estimated coverage (1329/1442 functions called by tests)*

## Configuration

```
pyproject.toml: name: hypergumbo; version: 1.0.0; license: { text =
LICENSE: AGPL

--- Additional context (semantic) ---
[docs/schema.json]
  > "type": "string", "description": "Schema version (semver)", "const": "0.2.0"
  > "required": [ "schema_version", "view",

[pyproject.toml]
  > [build-system] requires = ["hatchling>=1.24"]
  > "Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only", ]
```

## Data Models

- `Symbol` (Python @dataclass) — `src/hypergumbo/ir.py`
- `Span` (Python @dataclass) — `src/hypergumbo/ir.py`
- `AnalysisRun` (Python @dataclass) — `src/hypergumbo/ir.py`
- `Edge` (Python @dataclass) — `src/hypergumbo/ir.py`
- ... and 178 more data models

## Source Files

- `src/hypergumbo/schema.py`
- `src/hypergumbo/user_config.py`
- `src/hypergumbo/limits.py`
- ... and 251 more files

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

(... and 1725 more symbols across 111 other files)

## Additional Files

- `README.md`
- `docs/GOVERNANCE.md`
- `docs/LANGUAGES.md`
- ... and 71 more files

## Source Files Content

------------------- START of src/hypergumbo/schema.py ------
```python
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
```
------------------- END of src/hypergumbo/schema.py --------

------------------- START of src/hypergumbo/__init__.py ----
```python
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
```
------------------- END of src/hypergumbo/__init__.py ------

------------------- START of src/hypergumbo/linkers/__init__.py
```python
"""Cross-language linkers for hypergumbo.

Linkers create edges between symbols from different language analyzers,
enabling cross-language call graph construction.
"""
```
------------------- END of src/hypergumbo/linkers/__init__.py


## Additional Files Content

(No additional file content within token budget)

---

## Representativeness Table

```
                    How Representative Is This Sketch?
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Section                  ┃ 4,000t ┃ 16,000t ┃ 64,000t ┃ Metric          ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Data Models              │    20% │     86% │    100% │ confidence mass │
│ Source Files             │    45% │     95% │    100% │ symbol mass     │
│ Key Symbols              │    25% │     36% │     45% │ symbol mass     │
│ Additional Files         │      - │    0.0% │    0.0% │ symbol mass     │
│ Source Files Content     │   0.0% │    2.0% │     10% │ symbol mass     │
│ Additional Files Content │      - │       - │       - │ symbol mass     │
└──────────────────────────┴────────┴─────────┴─────────┴─────────────────┘
```

Note: With `--with-source`, the token budget is divided between the overview sections and actual source code. At 4,000 tokens, you get a few small files. Use `-t 16000` or higher to include more source code.
