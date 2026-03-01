# Future work — detailed designs

This document contains detailed designs for future capabilities listed in [§20 of the spec](../hypergumbo-spec.md#20-future-work). These are not yet implemented; they are preserved here for reference as the project evolves.

## AST-based type inference improvements

The current system implements constructor tracking, parameter tracking, field type tracking, return type tracking, and type hierarchy dispatch (see ADR-0006 and CHANGELOG.md). Supported in Python, Java, Kotlin, TypeScript, C#, Dart, and Scala (method name only).

Remaining improvements (without requiring language servers):

| Feature | Value | Effort | Notes |
|---------|-------|--------|-------|
| **Method-scoped tracking** | Low-Medium | Medium | Current file-scoped tracking can cause false positives when same variable name used in different methods. Low priority since collisions are rare. |
| **Generic handling** | High | High | Track `List<User>` to infer that `.get()` returns `User`. High complexity (type parameter binding, variance). Defer until simpler features are done. |

## Additional linkers

* 🟪 **Constant propagation** for dynamic routes (`BASE_URL + "/users"`)
* 🟪 **Middleware/proxy rewriting** detection

## Additional output views

Beyond `behavior_map.json` (the current output), future views compiled from the IR:

* 🟪 **ir_export.json** — Complete symbol table, typed edges with resolution provenance, dataflow facts, cross-language links
* 🟪 **context_bundle.json** — Agent-optimized: minimal code excerpts for a query, invariant checklist, "impact zones" (what could break), token-budget optimized
* 🟪 **sarif.json** — SARIF 2.1 compatible findings for integration with GitHub Code Scanning, GitLab SAST
* 🟪 **Flow specs** — Named, traceable feature flows (e.g., "Signup pipeline": route → handler → validation → database → email notification). Each flow includes entry/exit points, all nodes/edges in path, invariants, and tests covering the flow.

## Testing & CI enhancements

### Longitudinal analysis ("slow thinking")

Property tests provide immediate pass/fail feedback ("fast thinking"). But some insights only emerge from patterns across many CI runs:
- Did node count suddenly drop 40%? (regression)
- Is edge detection improving over time? (progress)
- How does analysis time scale with repo size? (performance)

Concept: "Nonjudgmental fixtures"—run analysis on a real repo without asserting correctness, just observing metrics:

```python
def test_observatory(capsys):
    """Emit metrics for longitudinal analysis. No assertions on correctness."""
    result = analyze(Path("tests/fixtures/medium-repo"))
    print(json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "commit": os.environ.get("CI_COMMIT_SHA"),
        "nodes": len(result.nodes),
        "edges": len(result.edges),
        "nodes_by_kind": dict(Counter(n.kind for n in result.nodes)),
    }))
    assert validate_schema(result)  # Only hard check: didn't crash, valid schema
```

Infrastructure needed: persistent storage for metrics across CI runs, aggregation/visualization tooling, and anomaly detection (alert on significant changes). This is a fundamentally different paradigm than pytest's immediate feedback.

### Integration tests

Add optional integration tests that validate end-to-end behavior:

* Use `@pytest.mark.integration` marker
* Skip automatically when dependencies are not available
* Run only on explicit request (`pytest -m integration`)
* Catch environment-specific issues

## Multi-fidelity analysis

The long-term vision is a multi-fidelity code understanding platform that produces typed IR and an agent context router for token-efficient editing.

**Language-native engines** (optional, high-fidelity frontends):
* 🟪 **TypeScript**: `tsserver` (type checker + language service)
* 🟪 **Python**: `pyright` or `mypy` (type inference)
* 🟪 **Rust**: `rust-analyzer` (full semantic analysis)
* 🟪 **Go**: `gopls` (language server)
* 🟪 **JVM**: Eclipse JDT (Java), Kotlin analysis tooling

These would produce typed call graphs, enabling mixed-fidelity analysis: AST edges (0.7 confidence) + typed edges (0.95 confidence) in the same graph.

**Runner types** for high-fidelity analyzers:
* 🟪 **`toolchain_bundle`** — Ships with language server (100MB+ downloads, high fidelity)
* 🟪 **`container_image`** — OCI/Docker image for maximum isolation
* 🟪 **`daemon_process`** — Long-running incremental analysis (research-hard, defer indefinitely)

**IR extensions:**
* Typed symbol table + cross-ref index (extends current IR)
* Control-flow graph (CFG) — opt-in with explicit partial-results flags
* Dataflow facts (reaching definitions, taint tracking) — opt-in, not a core requirement

**Technology choices:**
* IR storage: Protocol Buffers (fast, versioned, language-neutral); JSON fallback if protobuf adds friction

## Agent context router

Query interface: "I want to change behavior X in Y context"

The router would build on existing slicing capabilities — call graph traversal, test filtering, and supply chain tier boundaries — and extend them with:

**Pipeline:**

1. **Retrieve** relevant nodes/flows from IR
   * Entry: symbol name, file path, route pattern
   * 🟪 Natural language queries via embedding similarity

2. **Slice** on (beyond existing call graph / test / tier slicing):
   * 🟪 Dataflow (tainted data paths)
   * 🟪 Schema ties (database columns, API contracts)
   * 🟪 Configuration/deployment ties

3. **Assemble context bundle**:
   * Minimal code excerpts (only changed + affected)
   * Invariants/contracts (types, tests, assertions)
   * 🟪 "What could break" checklist (requires whole-program analysis)

Token budget optimization: BFS until token limit hit, sorted by (edge confidence, distance from entry).

**Potential enhancements:**
* 🟪 Learned relevance models
* 🟪 Agent feedback loop (which code was actually edited?)
* 🟪 Embedding-based context expansion
* 🟪 Coverage report parsing for test summary
* 🟪 Multi-language documentation summarization

## Incremental analysis

This is an 18+ month effort minimum:
- Dependency tracking (which symbols affect which)
- Invalidation propagation (change X → re-analyze Y, Z)
- Cross-file type inference updates

TypeScript incremental took ~3 years, rust-analyzer ~2 years.

**Current mitigation:**
- Cached slices: Pre-compute common features
- Symbol index: O(1) lookup for "find definition of X"
- Partial re-analysis: Re-analyze changed files + direct importers only
- Accept latency: Full analysis on deep queries is OK with progress bars

## Constraints and non-goals

**Performance targets (with high-fidelity analysis):**

* **Small repo** (<100 files): <10 seconds
* **Medium repo** (~500 files): <60 seconds (2x current due to type checking)
* **Large repo** (2000+ files): <5 minutes full analysis
* **Context router query**: <2 seconds for typical slice assembly

**Complexity acknowledgment:**

* **Natural language query parsing** requires embedding models + semantic search
* **Dataflow slicing** is NP-hard in general; even heuristic solutions are multi-month research
* **Impact prediction** requires whole-program analysis with test coverage correlation

If dataflow proves infeasible, agent-guided slicing (agents specify hops/filters via DSL) is a simpler alternative.
