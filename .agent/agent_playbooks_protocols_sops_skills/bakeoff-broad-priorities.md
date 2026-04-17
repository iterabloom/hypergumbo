<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
### BROAD Mode Priority Queue:
1. **Reflect on bakeoff results:** After each cycle, run `./scripts/bakeoff-broad-reflect` then `./scripts/bakeoff-broad-reflect aggregate` to synthesize findings. This is the primary feedback signal for coverage gaps. (`cycle` now includes reflect automatically; use `--skip-reflect` for fast iteration only.)
2. **Aggregate across sessions:** When prior sessions have reflect data, run `./scripts/bakeoff-broad-reflect aggregate` to surface cross-session trends. **Binary rule on a CONVERGED bakeoff:** if the tracker has any ready items, aggregate is NOT required — prefer tracker work and only return to aggregation after the backlog drains. Aggregation is only the natural next step when the bakeoff is not converged, or when it is converged AND the tracker is empty.
3. **Linkers:** post-call-graph-time edge recovery across four subcategories — Protocol (framework-agnostic pattern matching: HTTP URL / SQL / pub-sub topic / event name), Bridge (language-pair FFI and runtime-bridging conventions: JNI, wasm_bindgen, Tauri IPC, Cgo, pyffi, …), Framework (framework-specific dispatch: decorator registries, DI containers, ORM method dispatch, React JSX composition, middleware chains), and Infrastructure (structural utilities: containment, inheritance, module-import resolution). See [ADR-0003-ext: Linker Subcategory Restoration](../../docs/adr/0003-linker-subcategory-restoration.md) for the subcategory taxonomy and [docs/LINKERS.md](../../docs/LINKERS.md) for the 45-linker catalogue. Prioritise by expected false-positive-reduction volume on the current prospector corpus (INV-nimuj), not by novelty of language pair — within-language Framework-subcategory gaps empirically dominate cross-language gaps by ~10× in dead-code FP volume.
4. **Frameworks** (see `docs/FRAMEWORKS.md` for comprehensive list, 150+ frameworks): Pattern detection is YAML-driven metadata enrichment that tags symbols with concept metadata (`route`, `task`, `middleware`, `model`, …). Feeds Framework-subcategory linkers, which consume the tagged concepts and emit dispatch edges. Each new framework YAML typically pairs with one or more Framework-subcategory linkers.

**Pipeline overlap guidance:** Reflect assessment agents (LLM-driven) only read artifacts and source code — they do NOT invoke hypergumbo. This means you can safely overlap reflect with the next cohort's `run`:

- **Sequential workflow (any agent):** `run → diagnose → reflect → [complete assessments] → aggregate → next cohort`. Simpler, works everywhere.
- **Overlapped workflow (agents with concurrency):** Launch reflect agents for Cohort N, then immediately `run` Cohort N+1 while assessments complete in background. Only `run` needs exclusive access to the editable install. This is where the throughput multiplier lives — a 5-cohort curriculum can overlap all reflect phases.

**When blocked** (CI pending, pre-commit hook gate, `run` in progress): aggregate prior sessions, update lab notebook, investigate diagnostic findings. Use `./scripts/bakeoff-broad status` to find unaggregated assessments, or check for assessment files directly. On a CONVERGED bakeoff with a non-empty tracker backlog, pick a ready item instead — aggregation is not required busywork.

**Iteration vs. new session:** For validation runs on the same cohort (the
classic fix-iterate loop), skip `init` and call `cycle` directly — the CLI
auto-discovers the latest session and increments the iteration counter into
`iter-002/`, `iter-003/`, etc. Only call `init` when starting a *new line of
inquiry* (different cohort, different questions, days+ gap, pre-release
baseline). See `bakeoff-artifacts-guide.md` §"Iteration vs. New Session" for
the full rule and the anti-pattern history.

BROAD mode scripts:
```bash
# Initialize a new bakeoff session (creates timestamped dir in canonical default)
./scripts/bakeoff-broad init --pool ~/repos

# Select next cohort (5 smallest unused repos)
./scripts/bakeoff-broad cohort --count 5

# Or select cohort — explicit repos (for curriculum-based workflows)
./scripts/bakeoff-broad cohort --repos repo-a,repo-b,repo-c

# Run hypergumbo on current cohort
./scripts/bakeoff-broad run
./scripts/bakeoff-broad run --all          # All unanalyzed cohorts (batch)
./scripts/bakeoff-broad run --some 3       # Up to 3 unanalyzed cohorts

# Diagnose and generate issue report
./scripts/bakeoff-broad diagnose
./scripts/bakeoff-broad diagnose --all     # All cohorts in session
./scripts/bakeoff-broad diagnose --some 3  # Latest 3 cohorts

# Full cycle: run + diagnose + reflect
./scripts/bakeoff-broad cycle
./scripts/bakeoff-broad cycle --all        # Batch: run + diagnose + reflect all
./scripts/bakeoff-broad cycle --skip-reflect  # Fast iteration: run + diagnose only

# Session introspection
./scripts/bakeoff-broad status            # Convergence status and cohort breakdown
./scripts/bakeoff-broad issues --format json  # Machine-readable issue list
./scripts/bakeoff-broad questions         # Diagnostic questions for analysis

# LLM-driven qualitative assessment
./scripts/bakeoff-broad-reflect              # Generate assessment prompts (latest cohort)
./scripts/bakeoff-broad-reflect reflect --all  # Generate prompts for all cohorts
./scripts/bakeoff-broad-reflect aggregate    # Synthesize findings across repos
```

**Batch workflow (multi-cohort curriculum):**
```bash
# Option A: cycle --all (sequential, simpler)
./scripts/bakeoff-broad cycle --all        # run + diagnose + reflect for each cohort
./scripts/bakeoff-broad-reflect aggregate  # synthesize after all assessments complete

# Option B: manual pipeline (allows overlap between cohorts)
./scripts/bakeoff-broad run --all          # run all cohorts
./scripts/bakeoff-broad diagnose --all     # diagnose all cohorts
./scripts/bakeoff-broad-reflect reflect --all  # generate all prompts
# [complete assessments — sequential or parallel]
./scripts/bakeoff-broad-reflect aggregate  # synthesize findings
```
