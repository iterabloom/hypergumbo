# hypergumbo

Get a quick overview of any codebase, sized to fit your context window.

```bash
pip install hypergumbo
hypergumbo .
```

Output:
```markdown
# my-project

## Overview
Python (72%), TypeScript (18%), Markdown (10%) · 84 files · ~12,400 LOC

## Structure
- `src/` — Source code
- `tests/` — Tests
- `docs/` — Documentation

## Frameworks
- fastapi
- pytest

## Key Symbols
### `src/api/routes.py`
- `create_user` (function) ★
- `get_user` (function) ★
...
```

Use `-t` to control the token budget:
```bash
hypergumbo . -t 500   # concise overview
hypergumbo . -t 2000  # include symbols and entry points
```

## Installation

```bash
pip install hypergumbo
```

For JavaScript/TypeScript analysis:
```bash
pip install hypergumbo[javascript]
```

## CLI Commands

```bash
hypergumbo [path]            # default: generate Markdown sketch
hypergumbo . -t 1000         # sketch with 1000 token budget
hypergumbo run [path]        # full analysis → hypergumbo.results.json
hypergumbo slice --entry X   # extract subgraph from entry point
hypergumbo init [path]       # initialize .hypergumbo/ capsule
hypergumbo catalog           # list available analysis passes
hypergumbo export-capsule    # export shareable capsule tarball
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
- TypeScript interface, type alias, and enum detection
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
