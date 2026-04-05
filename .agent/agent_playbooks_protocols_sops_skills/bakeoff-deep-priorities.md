### DEEP Mode Priority Queue:
When in DEEP mode, focus on feature quality rather than coverage breadth:
1. **Reflect on bakeoff results:** After each cycle, run `./scripts/bakeoff-deep-reflect` then `./scripts/bakeoff-deep-reflect aggregate` to assess developer usefulness. This IS the mode's core feedback loop — reflecting on whether outputs help developers is the entire point of DEEP mode. Do not skip it. (`cycle` now includes reflect automatically; use `--skip-reflect` for fast iteration only.)
2. **Aggregate across sessions:** Run `./scripts/bakeoff-deep-reflect aggregate --all` and `./scripts/bakeoff-deep compare <A> <B>` to track improvement trajectories. If `./scripts/bakeoff-deep status` shows unaggregated assessments, aggregate before starting new work.
3. **Slice quality:** Does forward slice capture actual dependencies?
4. **Reverse slice:** Does it correctly identify callers?
5. **Supply chain tiers:** Is tier classification accurate for monorepos?
6. **Centrality ranking:** Do top-ranked symbols match developer intuition?
7. **Linkers:** polyglot repos are common and challenging for new developers; they are an opportunity for hypergumbo to shine

**Pipeline overlap guidance:** Same as BROAD mode — reflect agents only read artifacts, so you can overlap reflect with the next cohort's `run`. See BROAD mode guidance above for sequential vs overlapped workflows.

**When blocked:** Aggregate prior sessions (`./scripts/bakeoff-deep-reflect aggregate`), compare sessions (`./scripts/bakeoff-deep compare <A> <B>`), or update lab notebook.

DEEP mode scripts:
```bash
# Initialize and run feature bakeoff (no --workdir needed — uses canonical default)
./scripts/bakeoff-deep init --pool ~/repos
# → Creates ~/hypergumbo_lab_notebook/bakeoff_artifacts/deep-YYYYMMDD-HHMMSS/

# Auto-select cohort by size/complexity
./scripts/bakeoff-deep cohort --count 4 --min-size 20 --max-size 200

# Or use explicit repos (for curriculum-based workflows)
./scripts/bakeoff-deep cohort --repos repo-a,repo-b,repo-c

./scripts/bakeoff-deep run               # Current cohort only
./scripts/bakeoff-deep run --all         # All unanalyzed cohorts (batch)
./scripts/bakeoff-deep run --some 3      # Up to 3 unanalyzed cohorts
./scripts/bakeoff-deep diagnose          # Latest cohort only
./scripts/bakeoff-deep diagnose --all    # All cohorts in session
./scripts/bakeoff-deep diagnose --some 3 # Latest 3 cohorts

# Full cycle: run + diagnose + reflect
./scripts/bakeoff-deep cycle                 # Current cohort
./scripts/bakeoff-deep cycle --all           # Batch: run + diagnose + reflect all
./scripts/bakeoff-deep cycle --skip-reflect  # Fast iteration: run + diagnose only

# Session introspection
./scripts/bakeoff-deep status           # Per-cohort breakdown: output/diagnose/reflect status
./scripts/bakeoff-deep active           # Machine-friendly key=value (for stop hooks)
./scripts/bakeoff-deep compare A B      # Side-by-side metric/score deltas between sessions

# LLM-driven qualitative assessment
./scripts/bakeoff-deep-reflect              # Generate assessment prompts (latest cohort)
./scripts/bakeoff-deep-reflect reflect --all  # Generate prompts for all cohorts
./scripts/bakeoff-deep-reflect aggregate    # Synthesize findings across repos
```

**Batch workflow (multi-cohort curriculum):**
```bash
# Option A: cycle --all (sequential, simpler)
./scripts/bakeoff-deep cycle --all
./scripts/bakeoff-deep-reflect aggregate

# Option B: manual pipeline (allows overlap between cohorts)
./scripts/bakeoff-deep run --all
./scripts/bakeoff-deep diagnose --all
./scripts/bakeoff-deep-reflect reflect --all
# [complete assessments — sequential or parallel]
./scripts/bakeoff-deep-reflect aggregate
```

**Introspection subcommands:**
- **`status`**: Shows per-cohort breakdown (output, diagnostics, LLM assessment status) and overall verdict summary. Use to see what's done and what remains in a session.
- **`active`**: Machine-friendly key=value output for stop hooks. Prints workdir, session name, cohort counts, convergence, and worst-repo details. Exit 1 if no session.
- **`compare <A> <B>`**: Side-by-side comparison of two sessions. Shows per-repo metric deltas (nodes, edges, orphan rate, tier1%, avg slice nodes), verdict changes, and LLM score differences. Sessions can be full paths or dir names under `bakeoff_artifacts/`.

**Curricula:** Pre-planned cohort sequences live in `~/hypergumbo_lab_notebook/curricula/`.
Check there before auto-selecting — if a curriculum exists for the current work, follow its
cohort commands in order. See ADR-0009 §2b for the curriculum concept.
