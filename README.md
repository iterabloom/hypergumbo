# hypergumbo

Local-first CLI that profiles a repo and emits an agent-friendly “repo behavior map” JSON.

Status: MVP in progress.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./scripts/install-hooks
hypergumbo --version
```

## CLI

```bash
hypergumbo init [path]       # creates .hypergumbo/capsule.json with config
hypergumbo run [path]        # analyzes repo and writes hypergumbo.results.json
hypergumbo slice --entry X   # stub: will produce reduced behavior slice
hypergumbo catalog           # stub: will list available passes/packs
hypergumbo export-capsule    # stub: will export shareable capsule
```

## What It Does

`hypergumbo run` analyzes Python files and outputs a behavior map with:
- **Nodes**: Functions and classes with name, kind, location, and unique IDs
- **Edges**: Call relationships between symbols (who calls whom)

### Current Capabilities

**Python Analysis:**
- Function and class detection via AST
- Intra-file call detection (`helper()`)
- Cross-file call detection via imports (`from utils import helper`)
- Relative import resolution (`from ..utils import helper`)
- Method call detection (`self.helper()`)

**Output Format:**
```json
{
  "schema_version": "0.1.0",
  "nodes": [
    {"id": "python:app.py:1-2:helper:function", "name": "helper", "kind": "function", "language": "python", "path": "app.py", "line": 1, "end_line": 2}
  ],
  "edges": [
    {"source": "python:app.py:4-5:main:function", "target": "python:app.py:1-2:helper:function", "kind": "calls", "line": 5}
  ]
}
```

### Not Yet Implemented

See `docs/hypergumbo-spec.md` for the full MVP spec. Key missing pieces:
- Provenance tracking (origin, execution_id, run_signature)
- Confidence scoring and evidence types
- HTML script tag detection
- JS/TS analysis (tree-sitter)
- Capsule infrastructure (plan validation, catalog)
- Profile detection (languages, frameworks)

## Running tests

```bash
pytest
```

## AI-Assisted Development

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files 
(`CLAUDE.md`, etc.) are thin adapters that import the canonical source.
