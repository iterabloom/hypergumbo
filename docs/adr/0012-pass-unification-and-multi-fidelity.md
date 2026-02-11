# ADR-0012: Pass Unification and Multi-Fidelity Architecture

## Status

Design target (not yet implemented)

## Context

The analysis pipeline currently has two coexisting analyzer registration systems and a two-tier execution model. Understanding how we got here — and where the architecture should go — requires tracing the history.

### The two analyzer registries

On January 5, 2026 (commit `9f74a49`), a single refactoring commit created both systems simultaneously:

1. **`analyze/all_analyzers.py`** — An `AnalyzerSpec` NamedTuple-based system with lazy loading via `importlib.import_module()` at call time. Entry-points-based plugin discovery was added during the monorepo migration (ADR-0010). This system was wired into `cli.py` immediately and has been the active dispatch mechanism ever since.

2. **`analyze/registry.py`** — A decorator-based `@register_analyzer` system modeled on class-based registration with priority ordering and dependency metadata (`requires_symbols`). The commit message described this as "decorator-based registration (future use)." Every public function except the decorator itself was marked `pragma: no cover` from day one.

Only one analyzer (Go) ever adopted the decorator. Go is also registered via `AnalyzerSpec` in its package's entry-points, which is what actually gets called. The `@register_analyzer` decorator on Go populates a dict that nothing reads.

The following day (January 6), `linkers/registry.py` was created, its commit message explicitly stating it was "mirroring the pattern used in `analyze/registry.py`." The linker registry was wired into `cli.py` the same day and had all linkers migrated within 48 hours. It is now actively used by 24 linkers with 710 lines of well-tested code.

### The two-tier execution model

The analysis pipeline in `run_behavior_map()` runs in two tiers:

**Tier 1 — Language analyzers (independent producers):** 100+ analyzer functions, each taking `repo_root` and returning `AnalysisResult` (symbols + edges). They run independently and do not see each other's output. Dispatched via `all_analyzers.py`.

**Tier 2 — Linkers and enrichment (context-dependent refiners):** After all analyzers complete, the orchestrator collects the unified symbol graph and runs: deferred symbol resolution, framework pattern enrichment, 24 cross-language linkers (via `linkers/registry.py`), and entrypoint detection. These passes receive the accumulated state and produce new edges or metadata.

### The spec's `AnalysisPass(Protocol)`

The spec (§5) previously described an `AnalysisPass(Protocol)` interface where all passes receive the IR and return `IRDelta`. This matched neither implementation — it was an aspirational design that read as if it described the current system. The spec has been updated to accurately describe the two-tier model and to mark the unified Protocol as a design target (🟪).

### Why unification matters

The strategic case for a unified pass interface comes from multi-fidelity analysis: the ability for a type-resolution pass (e.g., a Python type checker or TypeScript language server) to refine the AST-extracted symbols and edges with higher-confidence type-resolved information. This requires a pass that can *read* the IR produced by the AST pass and *return deltas* that upgrade specific edges. The current Tier 1 model (independent bags) cannot express this; the Tier 2 model (context-dependent refiners) already can.

#### Evidence that type-resolved analysis matters for LLM context

Research on call graph construction shows a clear precision hierarchy. For Java, Class Hierarchy Analysis achieves median recall of ~0.884 — roughly 12% of real call edges missed (Li et al., ICSE 2020). For Python, dedicated interprocedural analysis (PyCG, ICSE 2021) reports ~99.2% precision but only ~69.9% recall; flow-sensitive type inference (Jarvis) achieves ~84% higher precision and ≥20% higher recall. Pure name-based/AST matching performs worse still, particularly for common method names like `.get()`, `.update()`, or `.run()` that resolve to dozens of candidate targets without type information.

For LLM consumption specifically, **precision matters more than recall.** LLM performance degrades as context length increases — stuffing a context window with false-positive call edges actively harms code understanding. Static analysis integration outperforms RAG for repository-level code completion, and combining static analysis with RAG achieves the best results (34.67/52.05 line exact match for Python/Java, versus 21.35/21.69 with prior best; Liu et al., "STALL+", 2024). Plugging a line-level code dependency graph into existing agent frameworks achieves a 32.8% average relative improvement on SWE-bench (RepoGraph, ICLR 2025).

AST-only analysis tends to be sufficient for architectural maps, simple functional style, and direct imports/calls. Type-resolved analysis clearly matters when polymorphism/interfaces/virtual dispatch dominate (Java, Kotlin, C#, Swift, Rust traits, TS structural typing), when heavily OO code with repetitive method names creates many plausible but wrong AST-matched targets, and when agent planning needs a reliable slice where one missing callee can cause misunderstood side effects.

#### What multi-fidelity would enable that doesn't exist elsewhere

**Graduated fidelity with transparent uncertainty.** No existing tool offers a code graph where AST-derived edges carry lower confidence scores that get upgraded when type resolution confirms them. Both edges coexist: `ast_call_method (0.85)` and `type_resolved_call (0.95)` at the same callsite, with provenance tracking which pass produced each. This maps directly to how AI agents should reason about code — making bolder edits when analysis is high-confidence and more cautious exploration when it isn't.

**Cross-language type anchoring.** Language servers operate within a single language boundary. Hypergumbo's 24 cross-language linkers already bridge those boundaries at the AST level. Adding type-resolved anchors on each side of a cross-language edge (e.g., confirming that a Python function calling a REST endpoint actually matches the TypeScript handler's type signature) would create a level of cross-language assurance that no tool currently provides.

**Token-budgeted, multi-fidelity slicing.** An AI agent could request: "Give me the high-confidence, type-resolved subgraph around this function, plus the medium-confidence AST-heuristic neighborhood two hops out, fitted to 8K tokens." Fewer missing callees means fewer hallucinations about what happens downstream. This graduated context construction aligns with research showing that context quality matters more than context quantity.

#### Existing infrastructure readiness

Hypergumbo's schema was designed for multi-fidelity from day one. The `confidence` field on every edge (0.0-1.0), `evidence_type` distinguishing AST heuristics from other sources, `origin` and `origin_run_id` tracking which pass produced which edge, and `confidence_model` versioning all support mixed-fidelity edges without schema changes. A type-resolution pass would produce `evidence_type: "type_resolved_call"` at 0.95 confidence alongside existing `"ast_call_method"` edges at 0.85, and the existing merging logic, provenance tracking, and slicing infrastructure would handle them naturally.

#### Effort and timing considerations

Integrating a single language server is estimated at months of effort. Language servers are designed for IDE interaction, not batch analysis — they expect persistent connections, file watchers, and incremental updates. Coercing them into a "scan the entire repo and emit resolved types" workflow requires project discovery, process lifecycle management, memory management (some can consume gigabytes on large repos), partial failure handling, and mapping output back onto hypergumbo's symbol graph. Each language server has different APIs, performance characteristics, and failure modes.

Higher-ROI alternatives for the same time investment include: expanding framework pattern coverage, improving AST-heuristic precision (better scope resolution, better dynamic dispatch handling), and building distribution channels that make hypergumbo's output directly consumable by AI coding tools. The consensus is: establish adoption with breadth-and-distribution first, then let user demand drive prioritization of which language server to integrate.

## Decision

The long-term architecture should converge on a unified pass interface. The migration has three steps, each independently valuable:

### Step 1: Unify analyzer registration (decorator-based registry)

Migrate all analyzers from `AnalyzerSpec` (string-based, in `all_analyzers.py`) to `@register_analyzer` (decorator-based, in `analyze/registry.py`), mirroring the pattern already proven by 24 linkers.

**Rationale:**
- Consistency with the linker registry, which uses the same decorator pattern successfully
- Self-registration: the analyzer file is self-describing (you can't write an analyzer without registering it)
- Richer metadata: the decorator registry already has `priority` (execution ordering) and `requires_symbols` (dependency declaration) that `AnalyzerSpec` lacks
- Eliminates 182 lines of dead code and the two-system confusion

**Key engineering challenge:** `AnalyzerSpec` uses lazy loading — modules are imported only when `get_func()` is called. The decorator pattern requires import-time registration. Solutions include lazy module import triggers via entry-points, or accepting the startup cost (which is acceptable for 100+ lightweight analyzer modules that get imported during analysis anyway).

**Scope:** Mechanical refactor touching ~65 analyzer files plus 3 package `__init__.py` files. Low risk, high confidence.

### Step 2: Unify the pass interface (the Protocol)

Once analyzers and linkers share a registration mechanism, unify their function signatures:

```python
class AnalysisPass(Protocol):
    id: str
    version: str
    capabilities: list[str]

    def run(self, ir: AnalysisIR, files: list[Path], config: Config) -> IRDelta: ...
```

Tier 1 analyzers receive an empty IR and populate it. Tier 2 refiners receive the accumulated IR. The orchestrator becomes a generic loop: sort passes by priority, iterate, merge deltas.

**Rationale:** With unified registration (Step 1), the only remaining difference between analyzers and linkers is their function signature. The Protocol resolves this, making the orchestrator fully generic. The cost is mild ceremony (analyzers accept an `ir` parameter they ignore); the payoff is that multi-fidelity passes slot in without new orchestration code.

### Step 3: Multi-fidelity passes

With the unified interface in place, a type-resolution pass slots in at a priority between Tier 1 analyzers and Tier 2 linkers. It reads AST-produced symbols from the IR and returns deltas that upgrade edge confidences. No architecture changes required — it's just another pass.

An alternative or complementary path: build an importer that accepts externally-produced language server index data and maps it onto the IR with appropriate confidence upgrades. This avoids the substantial engineering cost of hosting language servers in-process.

## Consequences

### Positive

1. **Eliminates dead code:** `analyze/registry.py` stops being vestigial and becomes the active system
2. **Consistency:** Analyzers and linkers share the same registration and interface patterns
3. **Multi-fidelity readiness:** The architecture supports type-resolution passes without additional refactoring
4. **Accurate documentation:** The spec, ADRs, and code align on what the dispatch system is

### Negative

1. **Migration effort:** Step 1 is a mechanical but non-trivial refactor (~65 files)
2. **Lazy loading tradeoff:** Must solve the import-time vs. call-time registration tension
3. **Speculative generalization risk:** Steps 2-3 are justified by multi-fidelity, which is a long-term goal with no current user demand

### Neutral

1. **Timing is flexible:** Each step is independently valuable. Step 1 can happen whenever it's convenient; Steps 2-3 should wait for multi-fidelity to be on the roadmap
2. **No user-visible changes:** This is purely internal architecture; the CLI, output format, and behavior are unchanged

## Relationship to Other ADRs

- **ADR-0010** (Modular Packages): Introduced the `AnalyzerSpec` + entry-points system during the monorepo migration. Its bootstrap safety section previously referenced `analyze/registry.py` as the dispatch system; this has been corrected to reference `all_analyzers.py`.
- **ADR-0003** (YAML-driven Framework Patterns): Framework enrichment is a Tier 2 refiner that would become a pass under the unified interface.
- **ADR-0006** (Variable Type Inference): AST-based type inference is a precursor to multi-fidelity; the current heuristics would be refined (not replaced) by language server passes.

## References

- Spec §5 "Architecture" — updated to describe the two-tier model and mark the Protocol as 🟪
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py` — current active dispatch
- `packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py` — decorator registry (currently unused)
- `packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py` — decorator registry (active, 24 linkers)
- Li et al., "Scaling static analyses at Facebook" (ICSE 2020) — call graph precision/recall benchmarks
- PyCG, "Practical Call Graph Generation in Python" (ICSE 2021) — Python call graph analysis
- Liu et al., "STALL+: Boosting LLM-Based Repository-Level Code Completion with Static Analysis" (2024)
- RepoGraph, "Repository-Level Code Completion Through Graph-Aware Context" (ICLR 2025)
