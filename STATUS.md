# Implementation Status

This document tracks progress against [Spec A (MVP)](docs/hypergumbo-spec.md#spec-a--hypergumbo-mvp).

> **Note:** The spec file also contains "Spec B" which describes a multi-year roadmap. Spec B is not in scope for current development.

## Legend

- [x] Implemented and tested
- [ ] Not yet implemented
- [stub] CLI command exists but is a placeholder

## Week 1: Foundation + IR Layer

| Feature | Status | Notes |
|---------|--------|-------|
| Schema definition (behavior_map view) | [x] | `schema.py` |
| Internal IR classes (Symbol, Edge, AnalysisRun) | [x] | `ir.py` |
| Profile module (language detection) | [x] | `profile.py` |
| File discovery + exclude logic | [x] | `discovery.py` |
| JSON writer (IR → views compilation) | [x] | `cli.py` |
| ID generation (stable_id, shape_id) | [x] | `analyze/py.py` |
| Pass interface and registry | [x] | `catalog.py` - Pass, Pack, Catalog classes |
| Catalog system (catalog.json schema) | [x] | `catalog.py` - get_default_catalog() |
| Capsule Plan (plan.json, validation) | [x] | `plan.py` - generate_plan(), validate_plan() |

## Week 2: Python Analyzer

| Feature | Status | Notes |
|---------|--------|-------|
| Python AST parser → IR emission | [x] | `analyze/py.py` |
| Function/class detection | [x] | |
| Call edges (intra-file) | [x] | |
| Import edges (cross-file) | [x] | Includes relative imports |
| Method call detection (self.method) | [x] | |
| Evidence-type-based confidence | [x] | `meta.evidence_type` on edges |
| Provenance tracking (AnalysisRun) | [x] | `analysis_runs[]` in output |

## Week 3: JS/TS Analyzer (Optional)

| Feature | Status | Notes |
|---------|--------|-------|
| Tree-sitter integration | [x] | `analyze/js_ts.py` |
| JS/TS AST → IR emission | [x] | Functions, classes, methods, getters, setters |
| TypeScript interface detection | [x] | `kind: "interface"` |
| TypeScript type alias detection | [x] | `kind: "type"` |
| TypeScript enum detection | [x] | `kind: "enum"` |
| Arrow function detection | [x] | `const fn = () => {}` |
| Call/import edges | [x] | ES6 imports, require(), function calls |
| Fallback if tree-sitter unavailable | [x] | Returns skipped result with reason |

## Week 4: Slicing + Entrypoints

| Feature | Status | Notes |
|---------|--------|-------|
| Slice module (BFS/DFS on relationships) | [x] | `slice.py` with BFS traversal |
| Entrypoint detection heuristics | [x] | `entrypoints.py` - FastAPI, Flask, Click, Electron |
| Feature generation with query specs | [x] | Stable feature IDs from query |
| Slice IDs and reproducibility | [x] | `sha256(json.dumps(query))` |

## Week 5: Capsule Initialization

| Feature | Status | Notes |
|---------|--------|-------|
| `hypergumbo init` command | [x] | Creates `.hypergumbo/capsule.json` + `capsule_plan.json` |
| Template-based plan generation | [x] | `plan.py` - generates from profile + catalog |
| LLM-assisted plan generation | [ ] | Optional enhancement |
| `hypergumbo catalog` command | [x] | Lists passes and packs |
| `hypergumbo export-capsule` command | [x] | `export.py` - tarball with privacy redactions |

## CLI Commands

| Command | Status | Description |
|---------|--------|-------------|
| `hypergumbo --version` | [x] | Print version |
| `hypergumbo init [path]` | [x] | Initialize capsule |
| `hypergumbo run [path]` | [x] | Run analysis |
| `hypergumbo slice --entry X` | [x] | Produce reduced slice |
| `hypergumbo catalog` | [x] | List passes/packs |
| `hypergumbo export-capsule` | [x] | Export shareable capsule |

## Output Schema Compliance

| Field | Status | Notes |
|-------|--------|-------|
| `schema_version` | [x] | |
| `profile` (languages, frameworks) | [x] | |
| `analysis_runs[]` | [x] | Provenance tracking |
| `nodes[]` with span, stable_id, shape_id | [x] | |
| `edges[]` with id, confidence, meta | [x] | |
| `features[]` | [x] | Via slice command output |
| `metrics` | [x] | `metrics.py` - counts, avg confidence, per-language |
| `limits` | [x] | `limits.py` - failed files, skipped langs, known gaps |

## Analysis Passes

| Language | Parser | Symbols | Edges | Notes |
|----------|--------|---------|-------|-------|
| Python | [x] AST | function, class | calls, imports | Full support |
| HTML | [x] regex | file | script_src | Script tag detection |
| JavaScript | [x] tree-sitter | function, class, method, getter, setter | calls, imports | Optional: `pip install hypergumbo[javascript]` |
| TypeScript | [x] tree-sitter | function, class, method, getter, setter, interface, type, enum | calls, imports | Optional: `pip install hypergumbo[javascript]` |

---

*Last updated: 2025-12-23*
