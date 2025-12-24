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
hypergumbo init [path]       # creates .hypergumbo/ with capsule.json and plan
hypergumbo run [path]        # analyzes repo and writes hypergumbo.results.json
hypergumbo slice --entry X   # produces reduced behavior slice from entry point
hypergumbo catalog           # lists available analysis passes and packs
hypergumbo export-capsule    # exports shareable capsule tarball
```

## What It Does

`hypergumbo run` analyzes source files and outputs a behavior map with:
- **Nodes**: Functions, classes, methods, interfaces with name, kind, location, and unique IDs
- **Edges**: Call and import relationships between symbols

### Analysis Capabilities

**Python Analysis:**
- Function and class detection via AST
- Intra-file call detection (`helper()`)
- Cross-file call detection via imports (`from utils import helper`)
- Relative import resolution (`from ..utils import helper`)
- Method call detection (`self.helper()`)

**JavaScript/TypeScript Analysis** (optional):
```bash
pip install hypergumbo[javascript]
```
- Function, class, method, getter, setter detection
- TypeScript interface detection
- Arrow function detection (`const fn = () => {}`)
- ES6 imports (`import { x } from 'module'`)
- CommonJS require (`const x = require('module')`)
- Intra-file function call detection

**HTML Analysis:**
- Script tag detection (`<script src="...">`)
- Creates edges from HTML files to referenced scripts

### Implementation Status

See [STATUS.md](STATUS.md) for detailed progress against the spec.

## Running tests

```bash
pytest
```

## AI-Assisted Development

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files 
(`CLAUDE.md`, etc.) are thin adapters that import the canonical source.
