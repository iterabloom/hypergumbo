# Hypergumbo: Technical Report and Autonomous Quality System Architecture

**Document Version:** 1.0
**Date:** January 24, 2026
**Audience:** Hypergumbo developers, contributors, and maintainers

---

## Executive Summary

Hypergumbo is a local-first CLI tool for static code analysis that generates machine-readable "behavior maps" of software repositories. It supports 67 programming languages via tree-sitter grammars and produces structured JSON output designed for consumption by AI agents and developer tools.

This report covers three interconnected systems:

1. **Hypergumbo Core** — The static analyzer itself
2. **Bakeoff Infrastructure** — An automated quality assurance framework that tests Hypergumbo against real-world repositories
3. **Autonomous Agent Governance** — An emerging system that enables AI coding agents to continuously improve Hypergumbo using a principled, invariant-based methodology

The project is transitioning from a manually-driven development workflow to an **autonomous continuous improvement system** where AI agents can discover bugs, generalize them into violated invariants, and implement structural fixes—all with minimal human intervention.

### Implementation Status Overview

| System | Status | Notes |
|--------|--------|-------|
| **Hypergumbo Core** | ✅ Implemented | 104 language analyzers, YAML patterns, all CLI commands |
| **Bakeoff Infrastructure** | ✅ Implemented | `scripts/bakeoff` with all subcommands, `bakeoff-reflect`, `hypergumbo_diag.py` |
| **Autonomous Governance** | ✅ Implemented | Hook adapters for Claude Code, Gemini CLI, Cursor, Codex CLI; invariant ledger; `./scripts/loop-toggle` |

> **Reader Note (Jan 28, 2026):** This document was updated to reflect full implementation of the autonomous governance system. All systems are now implemented. See [ADR-0008](adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for the governance design.

---

## Part 1: Hypergumbo Core Architecture

> **✅ IMPLEMENTATION STATUS: EXISTS**
>
> This section accurately describes the implemented Hypergumbo tool as of v1.0.0.

### 1.1 Purpose and Design Philosophy

Hypergumbo analyzes codebases and produces a **behavior map**: a graph representation where:

- **Nodes** represent symbols (functions, classes, modules, routes, endpoints)
- **Edges** represent relationships (calls, imports, instantiates, implements)
- **Entrypoints** identify candidate starting points for exploration (main functions, HTTP routes, CLI commands)

| Design Principle | Implementation |
|------------------|----------------|
| **Local-first** | No network or API keys required; all analysis runs offline |
| **IR-based** | Internal representation compiled to versioned JSON views |
| **Provenance-aware** | Every node/edge tracks which analyzer created it |
| **Agent-optimized** | Deterministic JSON + feature slices for LLM context windows |

### 1.2 Two-Tier Specification

The project is organized into two specifications:

| Spec | Status | Description |
|------|--------|-------------|
| **Spec A (MVP)** | Implemented | AST-based analysis with 104 language support |
| **Spec B (Future)** | Planned | Multi-fidelity analysis with language servers (tsserver, pyright, etc.) |

### 1.3 Identity System

Hypergumbo uses a triple-ID approach for symbol identification:

| ID Type | Purpose | Changes When |
|---------|---------|--------------|
| `id` | Location-based identifier | Code moves to different file/line |
| `stable_id` | Signature-based hash | Interface changes (params, return type) |
| `shape_id` | AST structure hash | Control flow/structure changes |

**Example node ID format:**
```
python:src/auth.py:42-48:login:function
```

### 1.4 Confidence Scoring

Edges include confidence scores (0.0-1.0) based on evidence type:

```python
EVIDENCE_CONFIDENCE_MATRIX = {
    ("python", "ast_call_direct"): 0.95,
    ("python", "ast_call_method"): 0.85,
    ("javascript", "import_static"): 0.95,
    ("html", "script_src"): 0.80,
    # ...
}
```

### 1.5 Supply Chain Classification

Files are classified into four tiers based on their relationship to the project:

| Tier | Name | Description | Default Behavior |
|------|------|-------------|------------------|
| 1 | `first_party` | Project's own source code | Analyzed, prioritized |
| 2 | `internal_dep` | Monorepo packages, local forks | Analyzed |
| 3 | `external_dep` | node_modules, vendor directories | Analyzed |
| 4 | `derived` | Build artifacts, minified files | Excluded |

### 1.6 YAML-Driven Pattern System

Entrypoint and framework detection is driven by 87 YAML pattern files:

- **5 convention patterns:** main-functions, test-frameworks, language-conventions, config-conventions, naming-conventions
- **82 framework patterns:** FastAPI, Django, Express, Rails, Spring Boot, etc.

This separation allows framework-specific behavior to be defined declaratively without modifying analyzer code.

### 1.7 Output Schema

The primary output is `hypergumbo.results.json` (behavior map):

```json
{
  "schema_version": "0.2.1",
  "confidence_model": "hypergumbo-evidence-v1",
  "view": "behavior_map",
  "analysis_runs": [...],
  "profile": {...},
  "nodes": [...],
  "edges": [...],
  "entrypoints": [...],
  "features": [...],
  "metrics": {...},
  "limits": {...}
}
```

### 1.8 CLI Commands

| Command | Purpose |
|---------|---------|
| `hypergumbo [path]` | Token-budgeted Markdown sketch for LLM context |
| `hypergumbo run [path]` | Full JSON behavior map |
| `hypergumbo slice --entry <symbol>` | Extract subgraph from entry point |
| `hypergumbo routes` | List detected HTTP routes |
| `hypergumbo explain <symbol>` | Show symbol details with callers/callees |
| `hypergumbo test-coverage` | Static coverage estimation via call graph |
| `hypergumbo catalog` | Show available language analyzers |

---

## Part 2: Bakeoff Infrastructure

> **✅ IMPLEMENTATION STATUS: EXISTS**
>
> The bakeoff infrastructure described in this section is fully implemented in `scripts/bakeoff`, `scripts/bakeoff-reflect`, and `scripts/hypergumbo_diag.py`. All subcommands (`init`, `cohort`, `run`, `diagnose`, `status`, `issues`, `cycle`, `questions`, `loop`, `scan`) exist and function as described.

### 2.1 Purpose

The **bakeoff** is an automated quality assurance system that tests Hypergumbo against real-world repositories. It implements a "scientific method machine" that:

1. Selects cohorts of repositories for testing
2. Runs Hypergumbo analysis on each repository
3. Diagnoses issues using structured heuristics
4. Tracks convergence across iterations
5. Generates actionable reports

### 2.2 Core Philosophy: Iterate to Convergence

The bakeoff operates on the principle that **a cohort should be tested repeatedly until results stop changing**. This is called convergence.

```
1. Select cohort → run → diagnose → find issues
2. For each issue:
   a. Investigate root cause
   b. Ask: "What analogous issues might exist elsewhere?"
   c. Check those analogous cases
   d. Fix both the specific issue and its siblings
3. Re-run on SAME cohort → diagnose
4. If new issues emerge, repeat step 2
5. If no new CRITICAL/HIGH issues for 2+ iterations → CONVERGED
6. Move to next cohort
```

### 2.3 Bakeoff Scripts

#### `scripts/bakeoff` — Main Orchestrator

| Subcommand | Purpose |
|------------|---------|
| `init` | Initialize session with repo pool and workdir |
| `cohort` | Select next cohort (language-diverse, size-ordered) |
| `run` | Run Hypergumbo on current cohort |
| `diagnose` | Analyze results and flag issues |
| `status` | Check convergence status |
| `cycle` | Full cycle: run + diagnose |
| `loop` | Autonomous loop with auto-progression |

**Issue Severity Levels:**

| Severity | Flag Examples |
|----------|---------------|
| CRITICAL | `NO_CALL_EDGES` |
| HIGH | `EXPECTED_ROUTES_BUT_FOUND_0`, `ENTRYPOINTS_DOMINATED_BY_TESTS` |
| MEDIUM | `ROUTES_WEAKLY_LINKED_TO_HANDLERS`, `LOW_CROSS_FILE_CALL_RESOLUTION` |
| LOW | Various minor quality issues |

#### `scripts/bakeoff-reflect` — Qualitative Analysis

Generates a "special vs needs work" reflection by analyzing behavior map artifacts:

- Computes concerns (NO_CALL_EDGES, LOW_RESOLUTION, etc.)
- Identifies strengths (STRONG_CROSS_FILE, RICH_EDGE_TYPES, etc.)
- Outputs randomized "questions for next cycle" to prompt exploration

#### `scripts/hypergumbo_diag.py` — Deep Diagnostics

Comprehensive diagnostic script that:

- Handles schema variations defensively
- Computes route stats, call resolution stats, cross-file stats
- Scores "best entrypoints" using heuristics
- Re-slices from best entrypoints
- Generates `DIAG_REPORT.md` with dashboards and per-repo analysis

### 2.4 Diagnostic Metrics

| Metric | Meaning | Healthy Range |
|--------|---------|---------------|
| `calls_resolved_pct` | Percentage of call edges with valid targets | >80% |
| `calls_crossfile_pct` | Percentage of resolved calls crossing files | >20% |
| `route_link_pct` | Percentage of routes linked to handlers | >30% |
| `entrypoints_test_pct` | Percentage of entrypoints in test files | <50% |
| `nodes_with_path_pct` | Percentage of nodes with file path metadata | >80% |

### 2.5 Output Structure

```
bakeoff-session/
├── state.json              # Persistent session state
├── cohorts/
│   └── cohort-001/
│       └── metadata.json   # Cohort composition
├── out/
│   └── cohort-001/
│       └── iter-001/
│           └── <repo>/
│               ├── hg.json           # Behavior map
│               ├── routes.txt        # Route listing
│               ├── entrypoints.txt   # Entrypoint listing
│               ├── slice.auto.json   # Auto-sliced subgraph
│               └── symbols.txt       # Top symbols
└── diag/
    └── cohort-001/
        └── iter-001/
            ├── DIAG_REPORT.md
            ├── issues.json
            └── best_entrypoints.tsv
```

---

## Part 3: Case Critiques — Bugs as Invariant Violations

> **✅ IMPLEMENTATION STATUS: HISTORICAL RECORD**
>
> These case critiques describe real bugs that were discovered and sort-of fixed. The fixes are merged and tested and the tests passed. We continued merrily along and in all cases failed to fully appreciate how our "fix" fell short of addressing the fundamentals.

The bakeoff infrastructure has surfaced real bugs that illustrate why the "structural until proven otherwise" methodology is really important to do and also really easy to accidentally not do.

### 3.1 Case Critique: Anonymous Function Call Attribution (JavaScript/TypeScript)

**Symptom:** `socketio-chat-example` had 16 nodes but 0 call edges.

**Investigation:**

The shared helper `_get_enclosing_function()` walked up AST parents and only returned a caller if it hit a *named* function:

```python
def _get_enclosing_function(node, source, symbols):
    while current is not None:
        if current.type in ("function_declaration", "method_declaration"):
            # Only NAMED functions recognized
            ...
        current = current.parent
    return None  # Anonymous functions hit this
```

Calls inside anonymous callbacks (extremely common in JavaScript) were silently dropped:

```javascript
app.get("/", (req, res) => { helper(); });  // helper() call lost
```

**Violated Invariant:** "Every emitted `calls` edge has a non-null caller symbol."

**Scope Expansion:**

| Axis | Check | Result |
|------|-------|--------|
| Same language, different construct | Arrow functions, callbacks, IIFEs | All affected in JS/TS |
| Different language, same pattern | Go closures, Rust closures, Ruby blocks | Already correct (continue walking) |
| Different stage | N/A | Issue was in extraction |

**Why JS/TS was Special:**

JS/TS had **special handling for `arrow_function`** that tried to resolve the arrow itself as a symbol and returned `None` when it wasn't assigned to a variable. Other languages simply continued walking upward.

**Fix Assessment:**

The JS/TS fix is **more structural than the Rails case (see below)** but **not fully general**:

✅ **Good:** Enhances `_get_enclosing_function()` rather than bypassing the call attribution flow
✅ **Good:** Uses a generic position-based lookup mechanism (`symbol_by_position`)
✅ **Good:** Works for all inline JS/TS handlers, not just routes

⚠️ **Limitation:** The fix is JS/TS-specific. Other languages with lambda handlers (Kotlin, Scala, etc.) have the same vulnerability but weren't fixed.

**What was done:**
1. Added `symbol_by_position` parameter to `_get_enclosing_function()`
2. For unassigned arrow functions: try position-based lookup first, then continue walking
3. Thread `symbol_by_position` through the call chain

**Outstanding Technical Debt:**
- Kotlin's `_get_enclosing_function()` only looks for `function_declaration`, not `lambda_literal`
- Same issue likely exists in: Scala, Groovy, and other languages with lambda/closure handlers
- A truly structural fix would extract the `symbol_by_position` pattern into a shared helper used by all analyzers

**Tests Added:** 13 new tests across 7 languages verifying callback/closure call attribution.

### 3.2 Case Critique: Rails Route Detection (Ruby)

**Symptom:** `postal` (Rails mail server) showed "No API routes found" despite having 50+ route definitions.

**Investigation:**

The Ruby analyzer correctly extracted route DSL calls into `usage_contexts`:

```ruby
# routes.rb
resources :domains
get '/api/v1/users' => 'users#index'
```

But `hypergumbo routes` looks for nodes with `meta.concepts` containing `{concept: route}`. No nodes had concepts metadata.

**Root Cause:**

In `framework_patterns.py` Phase 3 (usage-based matching):

```python
for ctx in usage_contexts:
    if not ctx.symbol_ref:
        continue  # THIS SKIPS ALL RAILS ROUTES
```

Rails routes use string references (`'users#index'`) or DSL constructs (`resources :users`), so `symbol_ref` is always `None`.

**Violated Invariant:** "Usage patterns extracted by analyzers become concepts on nodes."

**Scope Expansion:**

| Framework | Pattern Type | Affected? |
|-----------|--------------|-----------|
| Rails | String controller references | YES |
| Django | String view references | YES |
| Express | Anonymous callback handlers | YES |
| Go Gin/Echo | Anonymous handlers | YES |

**What Was Done (Workaround):**

> **⚠️ ANTI-PATTERN WARNING:** The fix described below is a *workaround* that bypasses the problematic code path rather than fixing the root cause. This is an example of what NOT to do, or at minimum, what should be immediately followed by fixing the deeper issue and undoing the workaround.

The Ruby analyzer was changed to create **Symbol objects** for each route DSL call, bypassing the usage context → concept flow:

1. Create symbols with `kind="route"` directly in the analyzer
2. Include metadata: `http_method`, `route_path`, `controller_action`
3. Keep emitting `UsageContext` (but these are still skipped by the gate)

**Result:** Routes match `symbol_kind: "^route$"` in rails.yaml via direct symbol matching, not through usage contexts.

**The `symbol_ref` gate at `framework_patterns.py:992-993` still exists.**

**Proper Workflow (NOT FOLLOWED):**

1. ✅ Identify root cause: `if not ctx.symbol_ref: continue` gate
2. ⚠️ If workaround needed for immediate unblock: implement it
3. ❌ **IMMEDIATELY** fix the root cause (relax/remove the gate)
4. ❌ **THEN** undo the workaround (remove direct symbol creation, let UsageContexts flow through)
5. ❌ Verify the fix works for ALL affected frameworks (Rails, Django, Express, etc.)

**Outstanding Technical Debt:**
- The `symbol_ref` gate remains unfixed
- Other frameworks with string-based handler references (Django string views, etc.) will hit the same problem
- The workaround adds complexity to the Ruby analyzer that shouldn't be necessary

### 3.3 Library Entrypoints (Partially Fixed)

**Symptom:** `hls.js` (TypeScript library) fails slice with "No entrypoints."

**Analysis:**

- It's a library, not an application
- No `main()` function
- No HTTP routes
- The real entrypoints are **exports**

**Status:** ⚠️ **PARTIALLY FIXED in PR #535** (January 2026)

**What was done:**

The fix added `library-exports.yaml` which detects exports from index files and `_extract_library_export_contexts()` in `js_ts.py` which creates UsageContext records for exports:

```python
# For named exports (js_ts.py:1234-1236)
handler_ref = None
if export_name in symbol_by_name:
    handler_ref = symbol_by_name[export_name].id

ctx = UsageContext.create(
    kind="library_export",
    ...
    symbol_ref=handler_ref,  # None if symbol not found
)
```

**Fix Assessment:**

✅ **Good:** Uses the proper UsageContext → concept flow (unlike Rails workaround)
✅ **Good:** Sets `symbol_ref` when the export name can be resolved to a symbol
✅ **Good:** Works for the common case: `export default class Hls {...}`

⚠️ **Limitation:** Still hits the `symbol_ref` gate at `framework_patterns.py:992-993`:

```python
for ctx in usage_contexts:
    if not ctx.symbol_ref:
        continue  # Skips anonymous exports
```

**Cases that work:**
- `export default class Hls {...}` — class name "Hls" is in `symbol_by_name`
- `export function doSomething() {...}` — function name resolved
- `export const foo = ...` — if the symbol exists

**Cases that are still broken:**
- `export default function() {...}` — anonymous, no name → `symbol_ref = None` → skipped
- `export default { ... }` — anonymous object → skipped
- `export { foo } from './other'` — re-export, symbol not in this file's `symbol_by_name` → skipped
- `export * from './other'` — namespace re-export → skipped

**Test Coverage Gap:**

The tests in `test_entrypoints.py` only cover the happy path where we have named exports with resolvable symbols (lines 982-1056). No tests for anonymous exports or re-exports.

**Outstanding Technical Debt:**
- The `symbol_ref` gate remains the root cause (same as Rails routes issue)
- Anonymous exports need symbol creation (similar to anonymous function fix)
- Re-exports would need cross-file symbol resolution or position-based linking
- JS/TS-specific — Python packages, Rust crates, etc. have analogous library export patterns not addressed

### 3.4 Common Thread

All three "fixes" addressed the symptom (routes not showing, calls not attributed, exports not detected) without fixing the gate at `framework_patterns.py:992-993` that causes the symptom:

```python
for ctx in usage_contexts:
    if not ctx.symbol_ref:
        continue  # THE GATE
```

Each fix worked around the gate in a different way:

| Case | Workaround | Why It's Not Structural |
|------|------------|------------------------|
| **Rails routes** | Create Symbol objects directly, bypass UsageContext flow | Adds complexity to analyzer; other string-ref frameworks (Django, etc.) will hit same wall |
| **JS/TS anonymous functions** | Position-based lookup to populate `symbol_ref` | JS/TS-specific; Kotlin/Scala lambdas have same vulnerability |
| **Library exports** | Set `symbol_ref` when name resolves | Anonymous exports and re-exports still blocked |

**The gate remains.** The next framework with string-based handler references, the next language with lambda route handlers, the next library pattern with anonymous exports — all will hit the same wall and require their own workarounds.

**The structural fix** would be to either:
1. Remove or relax the `symbol_ref` gate (allow UsageContexts to flow through and match patterns even without a symbol reference), or
2. Ensure all analyzers create symbols for anonymous constructs that might be referenced

Until one of these happens, we're playing whack-a-mole.

---

## Part 4: The Autonomous Agent Governance System

> **✅ IMPLEMENTATION STATUS: COMPLETE (Jan 28, 2026)**
>
> This system is **fully implemented**:
> - ✅ `AUTONOMOUS_MODE.txt` gate exists and works
> - ✅ `AGENTS.md` documents the methodology
> - ✅ `bakeoff loop` provides automated cycling
> - ✅ Hook-based reflection enforcement via `.agent/hooks/`
> - ✅ Invariant ledger at `.agent/invariant-ledger.md`
> - ✅ Vendor-agnostic hook adapters for Claude Code, Gemini CLI, Cursor, Codex CLI
> - ✅ `./scripts/loop-toggle` for easy on/off control

### 4.1 Vision

The project is building infrastructure to enable AI coding agents (Claude Code, Gemini CLI, Codex CLI, Cursor, etc.) to **continuously improve Hypergumbo** using a principled methodology.

The core insight: **A bug is evidence of a violated invariant.** The agent's job is not to patch symptoms but to:

1. Name the violated invariant
2. Search for all places that invariant is assumed
3. Either prove the invariant holds (bug is exception) or change the system to make it true
4. Add tests that fail if the invariant regresses

> **See Part 3** for case critiques showing what happens when this methodology is known but not followed: symptoms get patched, tests pass, PRs merge — and the violated invariant remains, waiting to manifest again in the next framework or language.

### 4.2 Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: Governor / Stop-Hook System                           │
│  Forces reflection → generalization → falsification loop        │
│  Stop condition: AUTONOMOUS_MODE.txt contains "FALSE"           │
└─────────────────────────────────────────────────────────────────┘
                              ↓ controls
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2: Bakeoff Infrastructure                                │
│  scripts/bakeoff, bakeoff-reflect, hypergumbo_diag.py           │
│  Generates CRITICAL/HIGH signals, tracks convergence            │
└─────────────────────────────────────────────────────────────────┘
                              ↓ operates on
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: Hypergumbo (the analyzer)                             │
│  67 analyzers, YAML patterns, behavior maps                     │
│  The thing being improved                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 Governor Protocol (Stop-Hook Reflection Prompt)

> **✅ IMPLEMENTATION STATUS: COMPLETE (Jan 27, 2026)**
>
> The methodology is documented in `AGENTS.md` and automated enforcement is implemented via hook adapters in `.agent/hooks/`. The reflection prompt at `.agent/stop_reflect.md` is injected when agents try to stop in autonomous mode.

When an AI agent stops, the following reflection protocol is enforced:

```
HARD CONSTRAINTS
- Max cohort size: 8 repos
- Only treat CRITICAL/HIGH signals as "top failing signals"
- Do not ship a narrow patch unless you can explain why it does NOT generalize

STOP CONDITION
Only stop if AUTONOMOUS_MODE.txt contains "FALSE"

STEP-BACK REFLECTION (before any action)
1) Restate current top failing signal(s) from bakeoff (CRITICAL/HIGH only)

2) Convert each failing signal into a candidate violated invariant:
   - Form: "In this system, X must always be true because Y depends on it"

3) Assume structural: for each invariant, list ≥3 analogies across:
   - Same language, different syntax/construct
   - Different language, same underlying pattern
   - Different pipeline stage (extract → match → concept → entrypoint → slice)

4) Attempt to falsify "structural" with checkable evidence:
   a) Run ≥2 ripgrep queries (show query, hit count, 3 representative paths)
   b) For each hit: state what failure WOULD look like
   c) Check for that failure (run bakeoff or construct minimal repro)
   d) Compare at least one other language's implementation (quote lines)
   e) If claiming "isolated": state what guard prevents recurrence

5) Choose the action most architecturally beneficial long-term:
   - Strengthen core representations (symbols/usage_contexts/concepts/edges)
   - Remove fragile special-cases and early-returns
   - Improve cross-language consistency
   - Add regression tests enforcing the invariant

6) Execute the chosen action

AFTER ACTION (close the loop)
- Re-run bakeoff on SAME cohort
- Report whether CRITICAL/HIGH signals disappeared, changed, or moved
- If new CRITICAL/HIGH issues appear, repeat the loop
```

### 4.4 Vendor-Agnostic Control Surface

> **✅ IMPLEMENTATION STATUS: COMPLETE (Jan 27, 2026)**
>
> This system is fully implemented. See [ADR-0008](adr/0008-autonomous-governance-and-vendor-agnostic-hooks.md) for the design rationale.

The governance system works across multiple AI coding tools via adapter scripts in `.agent/hooks/`:

| Tool | Hook Mechanism | Adapter Script | Integration Status |
|------|----------------|----------------|-------------------|
| Claude Code | `Stop` hook | `.agent/hooks/claude-code/stop.sh` | ✅ **IMPLEMENTED** |
| Gemini CLI | `AfterAgent` hook | `.agent/hooks/gemini-cli/after-agent.sh` | ✅ **IMPLEMENTED** |
| Cursor | `stop` hook | `.agent/hooks/cursor/stop.sh` | ✅ **IMPLEMENTED** |
| Codex CLI | Notification hook | `.agent/hooks/codex-cli/notify.sh` | ✅ **IMPLEMENTED** (limited) |

**Control files:**

| File | Status | Purpose |
|------|--------|---------|
| `AUTONOMOUS_MODE.txt` | ✅ **EXISTS** | Contains "TRUE" or "FALSE" to control loop continuation |
| `.agent/stop_reflect.md` | ✅ **EXISTS** | Reflection prompt injected on every stop attempt |
| `.agent/LOOP` | ✅ **EXISTS** | Sentinel file (existence = continue looping) |
| `.agent/invariant-ledger.md` | ✅ **EXISTS** | Tracks discovered invariants and their fix status |

**Convenience script:**

```bash
./scripts/loop-toggle         # Toggle autonomous mode on/off
./scripts/loop-toggle status  # Show current state
```

This manages both `AUTONOMOUS_MODE.txt` and `.agent/LOOP` together.

### 4.5 Invariant Ledger

> **✅ IMPLEMENTATION STATUS: COMPLETE (Jan 28, 2026)**
>
> The invariant ledger exists at `.agent/invariant-ledger.md` and tracks all discovered invariants. As of v1.1.0, all invariants (INV-001 through INV-006) are **FIXED**.

The ledger tracks:

| Invariant | Statement | Status |
|-----------|-----------|--------|
| INV-001 | Every `calls` edge has a non-null caller symbol | ✅ FIXED |
| INV-002 | Usage patterns become concepts on nodes | ✅ FIXED (deferred resolution) |
| INV-003 | Nested decorated functions must be extracted | ✅ FIXED |
| INV-004 | Routes have edges to handler functions | ✅ FIXED (route_handler linker) |
| INV-005 | Edge IDs must be unique | ✅ FIXED |
| INV-006 | Rails resource routes have handler metadata | ✅ FIXED (RESTful expansion) |

The ledger also documents **meta-invariants** — higher-level principles that unify specific invariants:

- **META-001:** Metadata must become graph structure
- **META-002:** Extraction completeness
- **META-003:** Data integrity

### 4.6 Progress Guardrails

To prevent infinite loops or unproductive churn:

| Guardrail | Trigger | Action |
|-----------|---------|--------|
| Stuck detection | Same CRITICAL/HIGH persists for 3 iterations | Stop and ask human |
| Cohort churn prevention | New cohort requested | Only allowed after convergence |
| Loop limit | Per-hook iteration count | Configurable cap (default: 5-10) |

### 4.7 Current State vs Target State

| Dimension | Current State | Target State | Gap |
|-----------|---------------|--------------|-----|
| **Loop closure** | `bakeoff loop` runs cycles; human interprets results | Agent forced to reflect + act + rerun | Need hook-based reflection injection |
| **Bug interpretation** | Methodology in AGENTS.md (voluntary) | Agent assumes structural, must falsify | Need automated enforcement |
| **Memory** | Lab notebook (prose) + CHANGELOG | Invariant ledger + regression tests (code) | Need structured ledger format |
| **Stopping** | `AUTONOMOUS_MODE.txt` exists ✅ | File-based gate works | **DONE** |
| **Vendor lock-in** | Claude Code only | Portable across Claude/Gemini/Codex/Cursor | Need hook adapters per tool |

> **Summary:** The bakeoff infrastructure (Layer 2) is **fully implemented**. The governance layer (Layer 3) has the methodology documented but lacks automated enforcement. The `AUTONOMOUS_MODE.txt` gate works, but reflection prompt injection and invariant tracking are manual.
>
> **Sobering Evidence:** Part 3 demonstrates that even with the methodology documented, we repeatedly shipped workarounds instead of structural fixes. The methodology was known but not followed. This suggests automated enforcement may be necessary — voluntary adherence isn't enough when the workaround is obvious and the structural fix is harder.

---

## Part 5: Quality Metrics and Benchmarks

### 5.1 Analyzer Quality by Language

Based on bakeoff analysis across multiple repositories:

| Language | Edge Density | Calls/Function | Cross-File Resolution | Concept Coverage |
|----------|-------------|----------------|----------------------|------------------|
| Ruby | 4.65 | 6.69 | 78.4% | 3.4% |
| TypeScript | 3.46 | 4.74 | 73.4% | 3.4% |
| C++ | 2.32 | 2.41 | — | 0.1% |
| Rust | 2.19 | 2.64 | 57.0% | 7.9% |
| Python | 2.17 | 1.75 | 24.4% | 6.3% |
| Go | 2.03 | 7.11* | 4.5%** | 7.0% |
| C | 0.92 | 1.52 | — | 2.2% |

*Go's high calls/function includes stdlib calls
**Go's low cross-file % is because most calls are to stdlib (expected)

### 5.2 Performance Targets

| Repo Size | Target Time |
|-----------|-------------|
| Small (<100 files) | <5 seconds |
| Medium (~500 files) | <30 seconds |
| Large (2000+ files) | <5 minutes |
| Cached (unchanged) | <2 seconds |

### 5.3 Memory Optimization

Version 1.0.0 achieved 80% memory reduction for large repositories:

| Repository | Before | After |
|------------|--------|-------|
| tensorflow | ~11 GB | ~2.1 GB |

Implementation: Streaming JSON output + aggressive cleanup of intermediate data structures.

---

## Part 6: Known Limitations

### 6.1 Analysis Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **`symbol_ref` gate** | UsageContexts without symbol references are skipped in framework pattern matching | ✅ **FIXED** — deferred resolution in `resolve_deferred_symbol_refs()` (see INV-002) |
| Re-export resolution incomplete | Imports through re-exporting modules may not resolve | ADR-0007 import tracking (phases 1-3A complete) |
| No type resolution in Spec A | Method calls on untyped variables may not resolve | Planned for Spec B with language servers |
| Dynamic dispatch not captured | Reflection, eval(), dynamic imports missed | Logged in `limits.not_captured[]` |
| No incremental analysis | Full re-analysis on every run | Caching at file level |

> **Note (Updated Jan 28, 2026):** The `symbol_ref` gate issue (INV-002) was fixed via deferred resolution. After all analyzers complete, `resolve_deferred_symbol_refs()` uses the complete symbol table to resolve string-based handler references before enrichment runs.

### 6.2 Bakeoff Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Library entrypoints partially detected | Anonymous exports, re-exports still missed | PR #535 covers named exports; deferred resolution helps but some edge cases remain |
| Firmware/driver entry points | No main(), linker-defined entry | Expected; use `--entry <symbol>` |
| Spec-only repos | No analyzable code | Exclude from cohorts |

---

## Part 7: Developer Workflow

### 7.1 Running Bakeoff Locally

```bash
# Initialize session
./scripts/bakeoff init --pool ~/repos --workdir ~/bakeoff-session

# Select cohort (6 repos, language-diverse)
./scripts/bakeoff cohort --count 6

# Run analysis
./scripts/bakeoff run

# Diagnose issues
./scripts/bakeoff diagnose

# Check convergence status
./scripts/bakeoff status

# Or run full cycle
./scripts/bakeoff cycle

# Autonomous loop (with safeguards)
./scripts/bakeoff loop --max-iterations 10
```

### 7.2 Investigating a CRITICAL/HIGH Issue

1. **Identify the violated invariant:**
   ```
   "In this system, X must always be true because Y depends on it."
   ```

2. **Scope expansion searches:**
   ```bash
   # Find analogous code paths
   rg "_get_enclosing_function" --type py
   rg "if not.*symbol_ref" --type py
   rg "continue.*# skip" --type py
   ```

3. **Cross-language comparison:**
   - Check if Go/Rust/Java/etc. handle the same pattern
   - Quote the relevant code

4. **Minimal reproduction:**
   - Create a test fixture that triggers the failure
   - Verify the test fails before fix, passes after

5. **Structural fix:**
   - Prefer changes to core representations over special-cases
   - Update the invariant ledger
   - Add regression tests

> **⚠️ Workaround Temptation:** It's easy to "fix" the symptom without fixing the cause. Part 3 documents three cases where we did exactly this. Before shipping a fix, ask: "Does this bypass a problematic code path, or does it fix/remove that path?" If bypassing, the fix is a workaround — acceptable as a temporary unblock, but the root cause must be addressed next.

### 7.3 Adding a New Language Analyzer

1. Create `src/hypergumbo/analyze/<lang>.py`
2. Implement symbol extraction using tree-sitter
3. Implement edge extraction (calls, imports, etc.)
4. Add tests in `tests/test_<lang>.py`
5. Run bakeoff with repos in that language
6. Verify no CRITICAL/HIGH issues

### 7.4 Adding a New Framework Pattern

1. Create `src/hypergumbo/frameworks/<framework>.yaml`
2. Define patterns for routes, entrypoints, models, etc.
3. Update framework detection in `profile.py`
4. Test with representative repositories
5. Verify routes and entrypoints are detected

> **Note (Updated Jan 28, 2026):** The `symbol_ref` gate issue is now handled by deferred resolution (`resolve_deferred_symbol_refs()`). When adding framework patterns for string-based handler references (e.g., `'controller#action'`, `'module.handler'`), store resolution hints in the UsageContext metadata (e.g., `view_name`, `controller_action`) and the deferred resolution phase will attempt to resolve them after all analyzers complete.

---

## Part 8: Glossary

| Term | Definition |
|------|------------|
| **Behavior Map** | JSON graph output with nodes, edges, and metadata |
| **Bakeoff** | Automated quality testing against real repositories |
| **Cohort** | A set of repositories tested together |
| **Convergence** | State where repeated testing yields no new CRITICAL/HIGH issues |
| **Concept** | Semantic tag on a node (route, test_function, main_entrypoint, etc.) |
| **Edge Density** | Ratio of edges to nodes in a behavior map |
| **Entrypoint** | Candidate starting point for slicing (main, route, CLI command) |
| **Governor** | The stop-hook system that forces reflection on every agent stop |
| **Invariant** | A property that must always be true in the system |
| **IR** | Internal Representation (compiled to JSON views) |
| **Slice** | Subgraph extracted from an entrypoint |
| **Spec A** | MVP specification (AST-based analysis) |
| **Spec B** | Future specification (multi-fidelity with language servers) |
| **Supply Chain Tier** | Classification of code origin (first-party, internal, external, derived) |
| **`symbol_ref` gate** | The check at `framework_patterns.py:992-993` that skips UsageContexts without a symbol reference. Root cause of multiple "fixed" issues. |
| **Usage Context** | Metadata about how a symbol is used (captured during analysis). Must have `symbol_ref` set to flow through framework pattern matching (see `symbol_ref` gate). |
| **Workaround** | A fix that bypasses a problematic code path rather than fixing it. Acceptable as temporary unblock; requires follow-up to address root cause. |

---

## Appendix A: File Locations

| Path | Purpose |
|------|---------|
| `src/hypergumbo/analyze/*.py` | Language-specific analyzers |
| `src/hypergumbo/frameworks/*.yaml` | Framework pattern definitions |
| `src/hypergumbo/framework_patterns.py` | Pattern matching engine; **contains `symbol_ref` gate at lines 992-993** |
| `src/hypergumbo/ir.py` | Internal representation classes |
| `src/hypergumbo/schema.py` | JSON schema versioning |
| `src/hypergumbo/entrypoints.py` | Entrypoint detection |
| `src/hypergumbo/slice.py` | Graph slicing |
| `scripts/bakeoff` | Bakeoff orchestrator |
| `scripts/bakeoff-reflect` | Qualitative analysis |
| `scripts/hypergumbo_diag.py` | Deep diagnostics |
| `docs/SPEC.md` | Full specification |
| `docs/schema.json` | JSON schema (auto-generated) |
| `CHANGELOG.md` | Version history |

---

## Appendix B: Related Documents

- **ADR-0003:** YAML-driven analysis architecture
- **ADR-0004:** File taxonomy with FileRole enum
- **ADR-0005:** Sketch budget allocation
- **ADR-0006:** AST-based type inference
- **ADR-0007:** Import tracking for cross-file resolution
- **LANGUAGES.md:** Full language support matrix
- **LINKERS.md:** Cross-language linker documentation

---

## Appendix C: Quick Reference — Bakeoff Flags

| Flag | Severity | Meaning | Likely Code Location |
|------|----------|---------|---------------------|
| `NO_CALL_EDGES` | CRITICAL | Nodes exist but no call edges | `analyze/<lang>.py` call extraction; may be anonymous handler issue (see 3.1) |
| `EXPECTED_ROUTES_BUT_FOUND_0` | HIGH | Web framework but no routes | `frameworks/<framework>.yaml`; **often caused by `symbol_ref` gate** (see 3.2, 3.4) |
| `ROUTES_WEAKLY_LINKED_TO_HANDLERS` | MEDIUM | Routes exist but not connected | Route→handler edge creation; **may be `symbol_ref` gate** if handlers are strings/lambdas |
| `ENTRYPOINTS_DOMINATED_BY_TESTS` | HIGH | >50% entrypoints are tests | `entrypoints.py` scoring |
| `AUTO_LIKELY_PICKS_NON_DOMINANT_LANGUAGE` | MEDIUM | Best entrypoint wrong language | Entrypoint ranking |
| `LOW_CROSS_FILE_CALL_RESOLUTION` | MEDIUM | <10% cross-file calls | Symbol resolution |

> **Pattern:** When investigating `EXPECTED_ROUTES_BUT_FOUND_0` or `ROUTES_WEAKLY_LINKED_TO_HANDLERS`, first check whether the framework uses string-based handler references or anonymous callbacks. If so, the `symbol_ref` gate is likely the cause — and any fix that doesn't address the gate is a workaround.
