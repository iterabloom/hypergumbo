<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
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

### Iteration vs. New Session

The session directory layout has two levels of nesting that map to two
different concepts:

```
deep-20260207-044500/        # session  (one experiment / one line of inquiry)
├── state.json
└── out/
    └── cohort-001/          # cohort   (one set of repos)
        ├── iter-001/        # iteration (one run of that cohort)
        ├── iter-002/        # iteration (re-run after a fix)
        └── iter-003/
```

**Use `init` only when starting a new line of inquiry.** Examples of
when to start a new session:
- Different cohort being investigated
- Different set of questions (e.g., switching from "do linkers detect
  X?" to "are slices useful for refactoring?")
- Significant time gap (days+) between investigations
- A clean baseline before a release

**For validation runs on the same cohort, skip `init` and call `cycle`
(or `run`) directly.** The bakeoff CLI auto-discovers the most recent
session and increments the iteration counter, creating `iter-002/`
inside the existing session. This is the *intended* workflow for the
classic fix-iterate loop:

```bash
# Day starts: investigate with a fresh session
./scripts/bakeoff-deep init --pool ~/repos
./scripts/bakeoff-deep cohort --repos repo-a,repo-b,repo-c
./scripts/bakeoff-deep cycle                  # produces iter-001
# ... fix something ...
./scripts/bakeoff-deep cycle                  # produces iter-002 in the SAME session
# ... fix again ...
./scripts/bakeoff-deep cycle                  # produces iter-003 in the SAME session
```

**Why this matters:** the bakeoff-process-health-audit playbook flags
"sessions with iteration: 1 and recurring concerns" as a process
failure. Sessions are how we measure convergence over time; if every
re-run is a fresh `iter-001` in a new session, the convergence trend
is invisible. The state.json `iteration` counter and the per-iter
output directories are designed to capture this trajectory — use them.

**Anti-pattern that wasted artifacts on 2026-02-07 through 2026-02-13:**
calling `init` between every cohort change created 20+ near-empty
nested session directories before the auto-discovery rule was added
(see `bakeoff_artifacts_nesting_cleanup_02182026.md`). The current
auto-discovery prevents nesting but does not prevent the simpler error
of starting a new top-level session for every iteration. Resist the
muscle memory of "always init before doing anything" — for iteration
within an investigation, just call `cycle`.

**When in doubt:** check `./scripts/bakeoff-deep status` (or `bakeoff-broad
status`). If the session it picks up looks like the right line of
inquiry, you don't need a new `init`.
