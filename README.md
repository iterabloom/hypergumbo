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
- **Edges**: Call relationships between functions (who calls whom)

Example output:
```json
{
  "schema_version": "0.1.0",
  "nodes": [
    {"id": "python:app.py:1-2:helper:function", "name": "helper", "kind": "function", ...},
    {"id": "python:app.py:4-5:main:function", "name": "main", "kind": "function", ...}
  ],
  "edges": [
    {"source": "python:app.py:4-5:main:function", "target": "python:app.py:1-2:helper:function", "kind": "calls", "line": 5}
  ]
}
```

## Running tests

```bash
pytest
```

## AI-Assisted Development

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files 
(`CLAUDE.md`, etc.) are thin adapters that import the canonical source.
