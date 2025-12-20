# hypergumbo

Local-first CLI that profiles a repo and emits an agent-friendly “repo behavior map” JSON.

Status: MVP in progress.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
hypergumbo --version
```

## CLI (MVP stub)

Right now the CLI subcommands are wired up as stubs:

```bash
hypergumbo init            # prints planned init params
hypergumbo run             # prints planned run params
hypergumbo catalog         # prints a placeholder catalog header
hypergumbo export-capsule  # prints planned export params
```

They don’t yet perform real analysis; we’re building that incrementally with tests.

## Running tests

```bash
pytest
```
