### Bakeoff Artifacts

Both `scripts/bakeoff-broad` and `scripts/bakeoff-deep` store artifacts in a canonical default location:

```
~/hypergumbo_lab_notebook/bakeoff_artifacts/
├── broad-20260206-183000/   # bakeoff session (timestamped)
│   ├── state.json
│   ├── cohorts/
│   ├── out/
│   ├── diag/
│   └── reflect/            # LLM assessment prompts and results
├── deep-20260206-190000/    # bakeoff-deep session (timestamped)
│   ├── state.json
│   ├── cohorts/
│   ├── out/
│   ├── diag/
│   └── reflect/            # LLM assessment prompts and results
└── ...
```

Key design decisions:
- **`init` creates timestamped session directories** — prior bakeoff artifacts are never overwritten
- **Subsequent commands auto-discover the latest session** — no need to remember the full path
- **Every subcommand prints the resolved workdir** — always visible which session is active
- **Env vars still work for overrides:** `BAKEOFF_WORKDIR` (broad) and `BAKEOFF_FEATURES_WORKDIR` (deep)
- **Artifacts persist across sessions** — mine them before running new bakeoffs
