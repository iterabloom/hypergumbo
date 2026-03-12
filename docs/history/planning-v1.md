<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Hypergumbo v1.0 Development Plan (Historical)

> **Note**: This document is a historical archive of the original development plan for Hypergumbo v1.0. The implementation is now complete with 67 language analyzers, 14 cross-language linkers, and 37 framework pattern files. See the main spec for current implementation status.

**Original timeline: 9 weeks** (2-week de-risking + 5 weeks core + 2 weeks buffer)

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Week 0 (de-risking) | 2 weeks | Week 0 |
| Week 1-5 (core development) | 5 weeks | Week 5 |
| Week 6-7 (buffer + polish) | 2 weeks | Week 7 |
| Week 8-9 (extended buffer) | 2 weeks | **Week 9** |

## Week 0: De-risking (2 weeks, dedicated time)
**Goals**: Validate high-risk components before committing to Week 1.
### Week 0a (Days 1-5): Tree-sitter + Capsule validation
**Tree-sitter packaging** (Days 1-4):
* **Day 1-2: Wheel availability audit**
  - Check PyPI for pre-built wheels: `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`
  - For each: Linux x64, macOS arm64/x64, Windows x64, Linux arm64
  - Document: Which platforms have pre-built wheels? Which require source build?
* **Day 3-4: Source build testing**
  - Spin up VMs/containers for platforms lacking wheels (Windows x64, Linux arm64)
  - Attempt `pip install tree-sitter-javascript` from source
  - Measure: Time to build, success rate, error messages if missing C compiler
  - Test: Does warning message help users install missing dependencies?
**Capsule Plan validation** (Days 5-6):
* Minimal JSON schema validator implementation
* Test composition: select passes/packs from small catalog
* Verify plan → execution pipeline works
* validating tree-sitter runtime + one pack install path
* validating plan/catalog/pack schema pipeline end-to-end
### Week 0b (Days 6-10): LLM testing + Integration
**LLM plan generation (Days 7-9):
* Prototype prompt engineering: repo profile → capsule plan
* Test with 3-5 sample repos (FastAPI, Electron, React)
* Measure: Does generated plan parse? Does it select reasonable passes?
* Cost test: Measure token usage, estimate per-repo cost
**Integration + Decision Gate** (Day 10):
* Run all prototypes together
* Document findings in ADR (Architecture Decision Record)
* Generate decision report: Go/No-Go for Week 1

**Decision gates:**
1. Tree-sitter packaging: Success on 3/4 platforms (Linux, macOS x2, Windows), OR Python-only fallback accepted
2. Capsule Plan: Validation works, composition pipeline functional
3. LLM: Generated plans valid >80% of time, cost <$0.10/repo, OR defer to template-only
**If gates fail**: Adjust Week 1 scope (e.g., skip LLM, defer JS/TS to v0.1.1).
## Week 1: Foundation + IR layer + Composition system
* Schema definition (behavior_map view with execution_id, run_signature, evidence_lang, evidence_spans, origin_run_signature)
* Internal IR classes (Symbol with revised stable_id + shape_id, AnalysisRun with execution_id + run_signature + repo_fingerprint)
* Pass interface and registry
* **Catalog system**: catalog.json schema, building block descriptors
* **Capsule Plan**: plan.json schema, validator, compiler
* Profile module (language detection)
* File discovery + exclude logic
* JSON writer (IR → views compilation)
* ID generation (signature-based stable_id + shape_id)
* **Tests:** schema validation, ID stability (survives refactors), plan validation, catalog loading
## Week 2: Python analyzer + evidence-based confidence
* Python AST parser → IR emission (implements Pass interface)
* Definitions (functions, classes, modules)
* Call edges (best-effort AST-based)
* Import edges
* Evidence-type-based confidence (deterministic algorithm)
* Provenance tracking (AnalysisRun with toolchain capture, execution_id, run_signature)
* **Tests:** Python parsing, evidence confidence determinism, provenance, toolchain capture, execution_id/run_signature hashing
## Week 3: JS/TS analyzer
* Tree-sitter integration (now bundled as standard dependency)
* JS/TS AST → IR emission (implements Pass interface)
* Best-effort call/import edges with evidence types
* Fallback behavior if tree-sitter unavailable
* **Tests:** JS parsing, graceful degradation
## Week 4: Slicing + entrypoints + security defaults
* Slice module (BFS/DFS on IR relationships)
* Entrypoint detection heuristics (FastAPI, Flask, Express, Electron)
* Feature generation with query specs
* Slice IDs and reproducibility
* Security manifest defaults in capsule.json (validation_mode: strict, trust: local_only)
* **Tests:** slice correctness, entrypoint detection, query reproduction, security defaults
## Week 5: Capsule initialization + Factory
* `hypergumbo init` command with `--assistant` flag
* Template-based plan generation (default)
* **Optional LLM-assisted plan generation** (if Week 0 validated):
  - Prompt engineering: profile → plan
  - Plan validation against catalog
  - Fallback to template if LLM fails
* Capability detection (language profiling → pack selection)
* `hypergumbo catalog` command
* `hypergumbo export-capsule --shareable` command (privacy-safe export)
* Security defaults in manifest + plan
* Capsule validation (manifest + plan + runner compatibility)
* **Tests:** init with template, init with LLM (if available), catalog display, plan validation, security defaults, shareable export
## Week 6-7: Buffer + polish
* Documentation (README, architecture diagrams, evidence type catalog)
* Packaging fixes (any remaining tree-sitter issues)
* First real-world test repo (not a fixture)
* Performance profiling + caching validation
* Schema documentation + examples with all new fields
* Regression test suite finalization
* Release preparation
## Week 8-9: Extended buffer (if needed)
* Reserve for unexpected issues
* Additional real-world testing
* Performance optimization
* Quantitative metrics collection setup (for Spec A validation)
