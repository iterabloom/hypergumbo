# hypergumbo

Get a quick overview of any codebase, sized to fit your context window.

```bash
pip install git+https://codeberg.org/iterabloom/hypergumbo.git
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
pip install git+https://codeberg.org/iterabloom/hypergumbo.git
```

For JavaScript/TypeScript analysis (requires tree-sitter):
```bash
pip install "hypergumbo[javascript] @ git+https://codeberg.org/iterabloom/hypergumbo.git"
```

For LLM-assisted plan generation (OpenRouter/OpenAI):
```bash
pip install "hypergumbo[llm-assist] @ git+https://codeberg.org/iterabloom/hypergumbo.git"
```

For local LLM models via Simon Willison's llm package:
```bash
pip install "hypergumbo[llm-local] @ git+https://codeberg.org/iterabloom/hypergumbo.git"
```

## CLI Commands

```bash
hypergumbo [path]            # default: generate Markdown sketch
hypergumbo . -t 1000         # sketch with 1000 token budget
hypergumbo run [path]        # full analysis → hypergumbo.results.json
hypergumbo slice --entry X   # extract subgraph from entry point
hypergumbo init [path]       # initialize .hypergumbo/ capsule
hypergumbo init --assistant llm  # use LLM to generate analysis plan
hypergumbo catalog           # list available analysis passes
hypergumbo export-capsule    # export shareable capsule tarball
```

## What It Does

**Default mode** (`hypergumbo .`) generates a Markdown sketch with:
- Language breakdown and LOC count
- Directory structure with labels
- Framework detection
- Key symbols ranked by graph centrality (★ = most called)
- Entry points (CLI, HTTP routes, etc.)

**Full analysis** (`hypergumbo run`) outputs a JSON behavior map with:
- **Nodes**: Functions, classes, methods, interfaces with location and stable IDs
- **Edges**: Call and import relationships between symbols

**LLM-assisted init** (`hypergumbo init --assistant llm`) uses an LLM to generate
a customized analysis plan based on your repo's profile. Supports:
- **OpenRouter** (free tier): Set `OPENROUTER_API_KEY` env var
- **OpenAI**: Set `OPENAI_API_KEY` env var
- **Local models**: Install `hypergumbo[llm-local]` for Simon Willison's llm package

Falls back to template-based generation if LLM is unavailable or fails.

### Supported Languages

| Language | Parser | Symbols | Edges |
|----------|--------|---------|-------|
| Python | AST | function, class | calls, imports |
| JavaScript | tree-sitter | function, class, method | calls, imports |
| TypeScript | tree-sitter | function, class, interface, type, enum | calls, imports |
| HTML | regex | file | script_src |

## Development

To contribute to hypergumbo:

```bash
git clone https://codeberg.org/iterabloom/hypergumbo.git
cd hypergumbo
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./scripts/install-hooks
pytest
```

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files
(`CLAUDE.md`, `GEMINI.md`, etc.) are thin adapters that import the AGENTS.md canonical source.

See [STATUS.md](STATUS.md) for implementation progress.
