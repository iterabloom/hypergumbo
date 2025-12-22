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

## CLI

```bash
hypergumbo init [path]       # creates .hypergumbo/capsule.json with config
hypergumbo run [path]        # writes hypergumbo.results.json (empty behavior map for now)
hypergumbo slice --entry X   # stub: will produce reduced behavior slice
hypergumbo catalog           # stub: will list available passes/packs
hypergumbo export-capsule    # stub: will export shareable capsule
```

`init` and `run` are functional but emit minimal output — real analysis is being built incrementally with tests.

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
