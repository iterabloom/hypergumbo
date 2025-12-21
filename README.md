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

## AI-Assisted Development

All agent instructions live in [AGENTS.md](AGENTS.md). Vendor-specific files 
(`CLAUDE.md`, etc.) are thin adapters that import the canonical source.


# TODO: Branch Protection Rules (Repository Settings) ("Safe Trunk Based Development")
1.  **Require status checks to pass:** Checked. (Select `pytest` CI job).
2.  **Require branches to be up to date:** Checked. (Prevents logical conflicts that git auto-merge misses).
3.  **Require review:** **Unchecked** (or set to 0 approvals).
    *   This allows you (or your agent) to merge immediately upon a Green build.
