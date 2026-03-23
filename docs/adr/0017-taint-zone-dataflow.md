<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0017: Taint-Zone Dataflow Analysis

Date: 2026-03-22
Status: Proposed

## Context

### Spec non-goal evolution

The hypergumbo spec (§2) lists as a non-goal: "No deep type-resolution / interprocedural dataflow correctness guarantees." That statement was written when hypergumbo was a broad-coverage structural analysis tool and before the implications of PlazaFlow's trust-boundary verification needs were understood.

This ADR does not invalidate that non-goal — it narrows its scope. Deep dataflow analysis is available for languages with contributed def/use extractors (Rust first, then TypeScript, Python, etc. as demand arises). The other 100+ languages supported by hypergumbo continue to use structural analysis with no dataflow correctness claims. As more users adopt hypergumbo for their own use cases and contribute extractors, the set of languages with deep analysis will grow incrementally. The non-goal is amended from "we never do this" to "we don't attempt this for all languages; extractors add it where demand justifies it."

When this ADR is accepted, `docs/hypergumbo-spec.md` §2 should be updated to reflect this evolution.

### Where ADR-0015 and ADR-0016 stop

ADR-0015 (dataflow access modes) classifies edges as read/write/mutate/delete by matching AST node types to YAML-driven patterns. ADR-0016 (I/O boundary analysis) traces backward through the **call graph** from I/O primitives to entry points, producing I/O chains that show which entry points can structurally reach which I/O calls.

Both operate at the **symbol level** — the finest granularity is "function A calls function B." Neither tracks what happens inside a function: which variables carry which values, which branches guard which calls, whether data from parameter X actually flows to call argument Y.

This means:

1. **IO boundary chains include false positives.** If `handle_request()` calls both `encrypt(data)` and `relay.send(blob)`, the call graph says `handle_request` reaches a `net_send` boundary. It cannot say whether the data reaching `relay.send` is the encrypted blob or the original plaintext — those are two different variables flowing through two different paths inside the same function.

2. **`verify-claims` checks structural reachability, not data flow.** The claim "relays never see plaintext" is verified by checking whether any call path from a plaintext source reaches a relay send function. But a function can call both `encrypt()` and `relay.send()` without the plaintext flowing to the send — the call path exists, the data path does not.

3. **Slicing is BFS on the call graph.** `slice.py` does breadth-first traversal with hop limits. Forward slice from symbol X returns everything connected within N hops, regardless of whether X's data actually flows there. Reverse slice is the same in the other direction. There is no narrowing based on actual data dependencies.

### The motivating application: PlazaFlow

PlazaFlow (the target application for this analysis) is a decentralized spatial collaboration platform with explicit trust boundaries defined in its specification:

| Zone | Trust level | What it must never see |
|------|------------|----------------------|
| Client | Trusted | (no restrictions) |
| Nearby peers | Semi-trusted | Other peers' IPs in private mode |
| Relays | Untrusted | Plaintext document content |
| Compute host | Untrusted | Decrypted content on host disk |

PlazaFlow's security model (§15 of its spec) makes specific claims:

- "Relays cannot read encrypted CRDT content"
- "Decrypted content is never written to the host filesystem"
- "Ephemeral data like awareness and speech must never be persisted"
- "Transitive key propagation is contained to one hop"

These are **dataflow properties**, not structural reachability properties. Verifying them requires knowing which specific values flow from which sources to which sinks — through variable assignments, function arguments, return values, and branches.

PlazaFlow is polyglot: TypeScript (browser client), Rust (relay, WASM modules, FUSE daemon), with data crossing language boundaries via WASM bindgen, Tauri IPC, and virtio-vsock. Hypergumbo already links these boundaries (wasm_bindgen linker, tauri_ipc linker). What's missing is the ability to track taint through the code on each side of those bridges.

### Validation before PlazaFlow exists

PlazaFlow is a specification, not a codebase. The taint catalogs in §2 (CRDT reads, relay communication, vsock channels) and the function count estimates in §1c (35-75 Rust functions) are designed against a specification, not validated against running code. Phase 1b's precision measurement (§9) requires a real codebase to measure against.

**Validation strategy for each phase:**

- **Phase 1 (structural taint flow).** Validated against **synthetic polyglot fixtures**: small hand-written programs with known taint paths, sanitizers, and violations. These fixtures exercise the catalog matching, BFS traversal, and dominance-based sanitizer checking without requiring PlazaFlow. Additionally, validate against **open-source Rust+TS projects** with known security boundaries (e.g., projects using `tauri`, `wasm-bindgen`, or `aes-gcm` crates). Phase 1 is testable and shippable without PlazaFlow.

- **Phase 1b (precision measurement).** If PlazaFlow code is not yet available, run precision measurement against an **alternative polyglot project** with explicit trust boundaries. Candidate criteria: uses Rust+TypeScript, has IPC/WASM boundaries, has at least one documented "data X must not reach component Y" security property. If no suitable open-source project exists, construct a **PlazaFlow-like reference implementation** (~500-1,000 lines) with the same architecture (CRDT reads → encryption → relay send, with intentional violations for testing). This reference impl doubles as a regression test for later phases.

- **Phases 2-4 (DDG, summaries, cross-language).** Validated against the same fixtures plus hypergumbo's own Python codebase (once a Python extractor exists). The Python extractor has the advantage of being testable against code we control: hypergumbo itself uses filesystem IO, subprocess calls, and has clear data-flow patterns.

**PlazaFlow-specific catalogs are project-local, not built-in.** The CRDT, relay, and vsock taint catalogs in §2 are examples of project-specific configuration, not built-in catalogs shipped with hypergumbo. Built-in catalogs cover general patterns (crypto decryption → plaintext, filesystem writes, network sends). PlazaFlow catalogs are validated when PlazaFlow code exists; the infrastructure they plug into is validated independently.

### What "dataflow analysis" means here

Not full interprocedural dataflow with alias analysis and pointer tracking. Instead, a practical middle ground:

1. **Intraprocedural reaching definitions** — within a single function, which assignments can reach which uses? This is the classic Dragon Book algorithm: build a control flow graph, compute gen/kill sets per basic block, iterate to fixpoint.

2. **Function summaries** — "argument 0 of this function flows to its return value" or "argument 1 flows to argument 0 of the call to `send()` inside this function." Summaries are either inferred from intraprocedural analysis or declared in YAML for stdlib/framework functions.

3. **Taint propagation** — label certain sources (decryption return values, CRDT reads, key derivations) with taint tags, then propagate those tags through reaching definitions and function summaries to see if they reach prohibited sinks (relay send, host filesystem write, persistent storage).

This is the same layered approach used by [Joern](https://joern.io/) (joernio/joern, Apache-2.0), an open-source code analysis platform that generates Code Property Graphs (CPGs) combining AST, control flow, and data dependence in a single queryable graph. Joern's `dataflowengineoss` module (~4,600 lines of Scala) implements the reaching-definitions-based dataflow engine, and its CFG construction code (~850 lines of Scala across `CfgCreator.scala` and the `Cfg.scala` data structures) implements the fringe-based CFG construction algorithm.

The key insight from studying Joern's architecture: the dataflow solver itself is small and language-agnostic (~75 lines for the core worklist algorithm). The CFG builder is also language-agnostic — all 12 of Joern's language frontends (C/C++, Java, JS/TS, Python, Go, Ruby, PHP, Kotlin, C#, Swift, Jimple/JVM bytecode, Ghidra for binaries) share one CFG builder with zero language-specific overrides. Language differences are handled entirely at the AST construction layer, before CFG building begins.

### Build natively with pluggable extractors

Studying Joern reveals a key architectural insight: the *infrastructure* for intraprocedural analysis is small and language-agnostic. The CFG builder is ~850 lines shared across all 12 frontends with zero language-specific overrides. The reaching-def solver is ~75 lines. What makes each frontend large (2,000–18,000 lines) is the *def/use extraction* — identifying which variables are defined and used at each AST node. This is the genuinely per-language work, and it varies by language complexity: Joern's JS frontend is ~6,200 lines (handling destructuring, optional chaining, spread, computed properties, class fields, decorators, and JSX), while simpler languages like Go need ~3,200 lines.

The native approach builds this infrastructure in Python on top of hypergumbo's existing tree-sitter AST layer:

| Layer | Approach | Effort |
|-------|----------|--------|
| CFG builder (language-parameterized) | Fringe-based recursive algorithm + semantic hooks, ~700-1,000 lines Python | Build once |
| Reaching-def solver (language-parameterized) | Worklist fixpoint + gen/kill + definition numbering, ~100-150 lines Python | Build once |
| Def/use extractors (per-language) | Pluggable Python modules, ~200-1,000 lines each | Contributed over time |
| YAML CFG node mappings (per-language) | Maps tree-sitter node types to control-flow categories | Contributed alongside extractors |
| Function summaries | Inferred from DDG or declared in YAML | Builds incrementally |
| Cross-language taint propagation | Via hypergumbo's 44 existing linker types | Already built |
| Taint catalogs + claim verification | YAML-driven, project-specific | Contributed per project |
| Structural fallback | Call-graph BFS; always available for languages without extractors | Already built (ADR-0016) |

**Why not use Joern as a runtime dependency?** Three reasons. First, Joern lacks a Rust frontend, and 3 of 4 PlazaFlow taint-flow claims have Rust-side sinks — the Rust gap would undermine the tool's core value proposition for its first target project. Second, an external JVM dependency (JDK 21+, ~150 MB tooling, subprocess coordination, GraphSON format versioning) conflicts with hypergumbo's local-first, no-external-tools-required design. Third, Joern covers 12 languages; hypergumbo analyzes 119. The native approach with pluggable extractors can expand to any language someone cares about, one extractor at a time.

**The accretion model.** Def/use extractors and taint catalogs are contributed over time, the same way hypergumbo already grows: IO primitive catalogs (YAML), dataflow patterns (YAML), framework patterns (YAML), and language analyzers (Python modules) are all contributed artifacts that make the system smarter for specific languages and frameworks. Taint analysis follows the same pattern. LLMs can assist at development time — generating draft extractors by studying a language's tree-sitter grammar, or generating taint catalogs by studying a framework's API — but hypergumbo itself never invokes an LLM. Every contributed artifact is reviewed, tested to 100% coverage, and committed. The system remains fully deterministic and local-first at runtime.

**Rust is the first extractor** because PlazaFlow is the motivating use case. Rust's syntax is more regular than TypeScript's (no destructuring spread, no optional chaining, no computed properties, no hoisting), but has its own complexities: ownership/borrowing semantics (`&mut` aliases), nested `match` patterns with guards and `ref`/`ref mut` bindings, the `?` operator's dual control flow (Ok path defines a variable, Err path invokes `From::from` and early-returns), and macro invocations that tree-sitter sees pre-expansion. The def/use extractor is estimated at 800-1,300 lines (see §1c for phased scope). Additional extractors (TypeScript, Python, Go) follow as demand arises.

**Alternative considered: Python first.** Python has pragmatic advantages as a first extractor target: (1) existing infrastructure — `annotate_dataflow_ast()` in `dataflow.py` already performs Python-specific AST analysis that could be extended to def/use extraction; (2) testable corpus — hypergumbo's own codebase is Python, providing immediate validation without depending on an external project; (3) broader utility — Python is the most common language for security-sensitive web applications (Django, Flask), giving the taint system wider applicability from day one; (4) simpler semantics — no borrow checker, no `?` operator, no ownership, yielding a genuinely ~200-400 line extractor. The tradeoff: if PlazaFlow code becomes available during Phase 2, a Python extractor cannot validate the Rust-side taint claims (TF-001, TF-002, TF-004) that motivate the ADR. **Decision: Rust first remains the plan** because the ADR's value proposition is tied to verifying PlazaFlow's trust boundaries — but if PlazaFlow code is delayed significantly (>3 months past Phase 1), the Python extractor should be built first to validate the infrastructure against a real codebase (hypergumbo itself) and demonstrate value to users with Python-heavy projects.

## Decision

### 1. Native intraprocedural dataflow (pluggable extractors)

Build intraprocedural dataflow analysis natively in Python, using hypergumbo's existing tree-sitter AST infrastructure. The architecture separates language-parameterized shared infrastructure (CFG builder with semantic hooks, reaching-def solver) from per-language pluggable modules (def/use extractors). No external tool dependencies.

#### 1a. CFG data structures and builder

The CFG builder is language-parameterized. It takes a tree-sitter AST and YAML-driven node mappings (§1d) and produces a control flow graph per function. "Language-parameterized" (not "language-agnostic") reflects the reality that while the builder contains no hardcoded language names, it does implement a bounded set of semantic hooks (see below) that handle control-flow constructs beyond the standard conditional/loop/break/continue/return categories.

```python
@dataclass
class BasicBlock:
    """A straight-line sequence of statements with no internal branches."""
    id: str                          # e.g., "bb_0", "bb_1"
    symbol_id: str                   # Enclosing function's Symbol.id
    statements: list[CfgStatement]   # Ordered statements in this block
    successors: list[CfgEdge]        # Outgoing control flow edges

@dataclass
class CfgStatement:
    """A single statement or expression within a basic block."""
    line: int
    col: int
    node_type: str          # tree-sitter node type (normalized)
    code_snippet: str       # Source text (truncated)
    defines: list[str]      # Variables assigned/written by this statement
    uses: list[str]         # Variables read by this statement
    call_target: str | None # If this is a call, the resolved Symbol.id (format: {lang}:{file}:{start}-{end}:{name}:{kind})

@dataclass
class CfgEdge:
    """A control flow edge between basic blocks."""
    target_block: str       # Target BasicBlock.id
    edge_type: Literal["always", "true", "false", "case", "exception"]

@dataclass
class FunctionCfg:
    """Complete CFG for one function."""
    symbol_id: str
    entry_block: str
    exit_block: str
    blocks: dict[str, BasicBlock]
```

The builder uses the fringe-based recursive algorithm (same algorithm as Joern's `CfgCreator.scala`):

1. Walk the AST for a function body.
2. For each AST subtree, produce a partial CFG with an entry node, finalized edges, and a **fringe** (pending outgoing edges whose destination is not yet known).
3. Sequential composition: connect A's fringe to B's entry.
4. Conditional (`if/else`): split the fringe into true/false edges; merge branches' fringes.
5. Loops (`while/for`): back-edge from body's fringe to condition entry.
6. `break`/`continue`: collect with nesting level; resolve when enclosing loop is processed.
7. `return`/`throw`: wire to function exit block; produce empty fringe.
8. `try`/`catch`/`finally`: try body's fringe connects to all catch entries (overapproximation).
9. `switch`/`match`: edges from scrutinee to each case entry.

Estimated size: ~700-1,000 lines of Python. Language differences are handled primarily by YAML node mappings (§1d), with a bounded set of semantic hooks in the builder for constructs that go beyond standard control-flow categories.

**Architectural deviation from Joern: normalization layer.** Joern's `CfgCreator` operates on a *normalized* Code Property Graph where all 12 language frontends have already flattened language-specific AST differences into a uniform node vocabulary (`ControlStructure`, `Call`, `Identifier`, `FieldAccess`, etc.). The per-language normalization work happens in the frontend (2,000-18,000 lines per language), *before* CFG building begins. Hypergumbo does not have this normalization layer — it operates directly on tree-sitter ASTs, where node type names differ per language (`if_expression` in Rust, `if_statement` in Python, `IfStatement` in TypeScript). The YAML CFG node mappings (§1d) serve as hypergumbo's normalization mechanism: they map language-specific tree-sitter node types to generic control-flow categories that the builder understands. This means:

- **YAML mappings bear structural weight.** They perform the role that Joern's per-language frontends perform — mapping language-specific syntax to a common vocabulary. Incomplete or incorrect mappings cause silent CFG errors (missing edges, collapsed blocks).
- **Unmapped node types.** When the builder encounters a tree-sitter node type not present in the language's YAML mapping, it treats the node as a **sequential statement** (no control-flow branching). This is the safe overapproximation: the statement's def/use effects are captured by the extractor (§1c), but any control flow hidden inside the unmapped construct is flattened to straight-line execution. A warning is emitted at `DEBUG` level listing unmapped node types encountered, enabling contributors to identify mapping gaps. Tests should include a "coverage check" that verifies all control-flow node types in a language's tree-sitter grammar are present in the YAML mapping.
- **Tree-sitter grammar versioning.** Tree-sitter grammars evolve; node type names can change between versions (e.g., a grammar update might rename `if_expression` to `if_expr`). Each YAML mapping should document the grammar version it targets (the `grammar` field in §1d already names the grammar package). When a grammar update breaks a mapping, tests fail deterministically (unmapped nodes → missing CFG edges → wrong reaching-def results). This is preferable to silent degradation.

**Semantic hooks for non-standard control flow.** Some language constructs cannot be reduced to the standard conditional/loop/break/continue/return categories via YAML mapping alone. The CFG builder implements a bounded set of semantic hooks that YAML mappings can invoke:

| Hook | Semantics | Used by |
|------|-----------|---------|
| `early_return_on_error` | Dual control flow: Ok path continues and defines a variable; Err path exits function | Rust `?` operator |
| `context_manager` | Entry call before body; exit call after body (including exceptional paths) | Python `with` |
| `deferred_execution` | Statement body executes at function exit, after all subsequent statements | Go `defer` |

These hooks are **not** language-specific code — they are generic control-flow patterns that multiple languages may use (e.g., Swift's `try`/`throws` could reuse `early_return_on_error`). The YAML mapping selects which hook applies to a given node type; the builder implements the hook's control-flow semantics once. This keeps the builder language-parameterized rather than language-agnostic: it knows about a finite set of control-flow patterns, but not about any specific language.

#### 1b. Reaching-def solver

Port of Joern's `DataFlowSolver` (~75 lines of Scala). The core worklist algorithm is ~50 lines of Python, but the complete module including definition numbering, gen/kill set computation, reverse postorder traversal, and DDG edge generation is estimated at ~100-150 lines:

1. **Number the definitions.** Each assignment gets a unique index. Represent sets as Python `int` bitsets (arbitrary precision).
2. **Compute gen/kill per block.** A definition is *generated* when a variable is assigned; it *kills* prior definitions of the same variable.
3. **Iterate to fixpoint** using a worklist in reverse postorder. `out[n] = gen[n] ∪ (in[n] \ kill[n])`. `in[n] = ∪ out[predecessors]`.
4. **Generate DDG edges** from each reaching definition to its use sites.

Per-function bail-out at 4,000 definitions (same threshold as Joern's `ReachingDefPass` default). Functions exceeding this fall back to structural analysis.

#### 1c. Pluggable def/use extractors

The CFG builder needs to know which variables each AST node defines and uses. This is the language-specific part — each language has different syntax for assignments, pattern matching, destructuring, etc. Def/use extractors are pluggable Python modules, one per language:

```python
class DefUseExtractor(Protocol):
    """Extracts variable definitions and uses from tree-sitter AST nodes."""
    language: str

    def extract(
        self,
        node: tree_sitter.Node,
        source: bytes,
    ) -> DefUseResult:
        """Return variables defined and used by this AST node."""
        ...

@dataclass
class DefUseResult:
    """Variables defined and used by a single AST statement."""
    defines: list[str]     # Variables assigned/bound by this node
    uses: list[str]        # Variables read by this node
```

Extractors are registered via decorator, following the same pattern as `@register_analyzer`:

```python
@register_def_use_extractor("rust")
class RustDefUseExtractor:
    language = "rust"

    def extract(self, node: tree_sitter.Node, source: bytes) -> DefUseResult:
        # Handle: let bindings, assignments, pattern matching,
        # for loops, closures, ? operator, etc.
        ...
```

**Name resolution and scope tracking.** The `defines` and `uses` lists contain variable names as strings. Correct def/use extraction requires distinguishing variables at different scopes: in Rust, `let x = 1; let x = x + 1;` involves two distinct definitions of `x`, and `self.field = val` requires knowing that `self` is a parameter. The existing language analyzers (e.g., `rust.py` at 1,867 lines) already have scope analysis, type registry, and call resolution infrastructure. Def/use extractors should import and build on this per-language infrastructure rather than duplicating it. The `DefUseExtractor` protocol is intentionally minimal — extractors are free to use whatever per-language analysis they need internally, including the existing analyzer's scope tracker. This coupling is expected and acceptable: a Rust def/use extractor naturally depends on Rust-specific scope analysis, just as a Python def/use extractor would build on Python's `ast` module (which `annotate_dataflow_ast()` in `dataflow.py` already uses).

**Rust is the first extractor.** PlazaFlow's Rust code is the motivating use case. Three of four taint-flow claims (TF-001, TF-002, TF-004) have Rust-side sinks. The concrete scope is bounded:

| Rust Component | Functions needing DDG | Taint role |
|---------------|----------------------|-----------|
| Tauri IPC handlers | ~20-40 | Receive plaintext from TS frontend via IPC; must not write to host fs or forward to relay |
| vsock bridge (host side) | ~10-20 | Feeds plaintext CRDT data into guest VM (approved path); must not leak to host fs |
| WASM crypto module | ~5-15 | `decrypt()` output is taint source; must return to TS via wasm-bindgen, not stored locally |
| Relay binary | ~0 | Handles only ciphertext; never imports decryption keys. Structural analysis sufficient |

Total: **35-75 functions** needing Rust DDG analysis. The relay binary is a red herring — it doesn't have decryption keys, so no plaintext taint source can exist there.

**Rust def/use patterns to handle:**

| Pattern | Defines | Uses | Complexity |
|---------|---------|------|-----------|
| `let x = expr` | x | expr | Simple |
| `let (a, b) = expr` | a, b | expr | Moderate (recursive patterns) |
| `let Struct { f1, f2 } = expr` | f1, f2 | expr | Moderate |
| `if let Some(x) = expr` / `match` | x per arm | expr | Moderate (nested, guards) |
| `x = expr` | x | expr | Simple (must be `mut`) |
| `x.field = expr` | mutates x | expr | Simple |
| `x?` | Ok value | x | Hard (dual control flow: Ok defines x, Err invokes `From::from` and early-returns) |
| `let y = &mut x; *y = val` | mutates x via y | val | Hard (borrow alias tracking) |
| `for x in iter` | x | iter | Simple |
| `\|x, y\| expr` | x, y | captured vars + expr | Moderate (capture analysis) |
| `macro!(args)` | unknown | args | Hard (pre-expansion AST; conservative: all args may flow to return) |
| `return expr` | — | expr | Simple |

**Phased extractor scope.** The Rust patterns above span a wide complexity range. Rather than attempting all patterns in Phase 2, the extractor is built in two increments:

- **Phase 2 (core extractor, ~500-800 lines):** Handles patterns marked Simple and Moderate: `let` bindings, tuple/struct destructuring, `if let`/`match`, reassignment, field writes, `for` loops, closures (conservative capture), and `return`. These cover the majority of data-flow patterns in typical Rust functions. The `?` operator is handled structurally: the CFG builder creates the dual control-flow edges (Ok path continues, Err path exits) via the YAML mapping (§1d), but the def/use extractor treats the Ok-side binding as a simple `let` define.
- **Phase 2b (hard patterns, ~300-500 lines additional):** Borrow alias tracking (`let y = &mut x; *y = val` → mutates x), `ref`/`ref mut` bindings in `match` arms, and macro invocation analysis (beyond conservative overapproximation). These are deferred because they require mini alias analysis infrastructure that the core extractor does not need. Phase 2b is prioritized based on Phase 2 precision measurement: if borrow aliases are a significant source of false negatives in PlazaFlow's Rust code, they move up; if not, they remain low priority.

**Estimated total size: 800-1,300 lines** (core + hard patterns combined). Rust's syntax is more regular than TypeScript's — no destructuring spread, optional chaining, computed properties, or hoisting — but has its own complexities that justify the higher estimate compared to the initial 500-800 range. For comparison, Joern lacks a Rust frontend entirely, and its closest analog (Go at 3,204 lines) has simpler semantics (no borrow checker, no `?` operator, no ownership). The existing `rust.py` analyzer (1,867 lines) already has tree-sitter traversal, function parameter extraction, type registry, and call resolution infrastructure that the extractor builds on.

**Languages without an extractor** fall back to structural taint flow (call-graph BFS with dominance-based sanitizer checking). The system is always usable; extractors add intraprocedural precision.

**Targeted analysis.** DDG analysis is not invoked on the entire codebase. Hypergumbo uses its behavior map to select targeted functions:

- **IO-critical functions**: functions on paths between entry points and IO primitives (identified by `io_boundary.py`)
- **Taint-relevant functions**: functions that handle data sources or sinks named in taint catalogs (§2)
- **Claim-relevant functions**: when `verify-claims` is invoked, functions on paths relevant to the claims being checked

Estimated coverage: 2-10% of functions in a typical codebase. Budget cap: 500 functions (configurable via `--max-ddg-targets`), prioritized by centrality rank.

#### 1d. Per-language CFG node mappings (YAML-driven)

YAML files map tree-sitter node types to generic control-flow categories. The CFG builder consumes these mappings — it does not contain any language-specific code.

```yaml
# cfg_nodes/rust.yaml
language: rust
grammar: tree-sitter-rust

conditional:
  - node_type: if_expression
    condition_child: condition
    true_child: consequence
    false_child: alternative
  - node_type: match_expression
    scrutinee_child: value
    arms_child: body

loop:
  - node_type: loop_expression
    body_child: body
    infinite: true
  - node_type: while_expression
    condition_child: condition
    body_child: body
  - node_type: for_expression
    body_child: body

break_statement: [break_expression]
continue_statement: [continue_expression]
return_statement: [return_expression]

early_return:
  - node_type: try_expression       # the ? operator
    semantics: return_on_err
    ok_defines: true
    err_edge: exit

try_catch: []                        # Rust uses Result/?, not try/catch
```

Each language with a def/use extractor also needs a CFG node mapping YAML. These are small (~30-60 lines per language) and follow the same YAML-driven pattern as `io_primitives/` and `dataflow_patterns/`.

#### 1e. Accretion model

The taint analysis system grows by accretion of contributed code and configuration — the same way hypergumbo already grows. Every extension is a committed, tested, deterministic artifact:

| Artifact type | Format | How to contribute | Example |
|--------------|--------|-------------------|---------|
| Def/use extractor | Python module (~200-1,000 lines) | Implement `DefUseExtractor` protocol, register via decorator | `rust_def_use.py` |
| CFG node mapping | YAML (~30-60 lines) | Map tree-sitter node types to control-flow categories | `cfg_nodes/rust.yaml` |
| Taint catalog | YAML (~20-50 lines) | Define sources/sinks/sanitizers for a domain | `taint_sources/crypto.yaml` |
| Function summary | YAML (~10-30 lines per function) | Declare param-to-return flow for stdlib/framework functions | `function_summaries/rust_stdlib.yaml` |
| Security claims | YAML (~5-15 lines per claim) | Project-specific taint-flow assertions | `security-claims.yaml` |

**LLM-assisted development.** Contributors can use LLMs to generate draft artifacts — a def/use extractor by studying a language's tree-sitter grammar, taint catalogs by studying a framework's API, function summaries by studying library documentation. The generated artifact is then reviewed by a human, tested to 100% coverage, and committed. Hypergumbo itself never invokes an LLM. Joern's language frontends serve as reference implementations that inform extractor design (e.g., studying Joern's `gosrc2cpg` frontend to understand which Go AST patterns need def/use handling), but Joern is not a runtime dependency.

**Expansion path:** Rust first (PlazaFlow need), then TypeScript (PlazaFlow primary, ~600-1,000 lines due to destructuring/spread/optional chaining), then Python (existing `annotate_dataflow_ast()` in `dataflow.py` to build on), then Go, Java, etc. as demand arises.

#### 1f. Performance constraints

- **Per-function bail-out.** 4,000-definition threshold (same as Joern's default). Functions exceeding this fall back to structural analysis.
- **Memory.** Python `int` as arbitrary-precision bitsets for reaching-def sets (with the 4K-definition cap, bitsets are small). The solver uses a **streaming strategy**: compute DDG per function, extract the function summary, then free the per-function bitset data before moving to the next function. Peak bitset memory is bounded to a single function's analysis at a time, not all 500 targeted functions simultaneously. Hypergumbo's existing memory pressure guard (`limits.py`) accounts for DDG computation overhead alongside analyzer memory.
- **Parse caching.** A dict mapping `(file_path, language)` → tree-sitter `Tree` avoids re-parsing files already parsed in Pass 1. Trees are immutable; no invalidation needed within a single run.
- **Overall overhead.** Estimated 10-30 seconds for native DDG computation on a typical targeted set (50-200 functions). No JVM startup, no subprocess coordination, no disk I/O for intermediate formats. Computation is pure Python operating on in-memory tree-sitter ASTs.

**Module placement.** CFG builder and reaching-def solver live in a new `cfg.py` module in `hypergumbo-core`. Def/use extractors live alongside their language analyzers (e.g., `rust_def_use.py` in `hypergumbo-lang-mainstream`). Taint catalogs, propagation, and the solver live in a new `taint.py` module in `hypergumbo-core`. All modules reference `Symbol` and `Edge` objects by ID (string) to maintain loose coupling.

### 2. Taint catalogs (YAML-driven)

Extend the IO primitive catalog pattern (ADR-0016) to a general taint source/sink/sanitizer system.

**General-purpose architecture, PlazaFlow as first client.** The taint catalog system is project-agnostic: any project can define its own taint sources, sinks, and sanitizers by writing YAML files. Hypergumbo ships with built-in catalogs for common patterns (crypto, filesystem, network — similar to the existing IO primitive catalogs). The PlazaFlow-specific catalogs below (CRDT content, relay communication, vsock channels) demonstrate project-specific customization that a user would provide alongside their `security-claims.yaml`. The `verify-claims` command loads both built-in and project-local catalogs, with project-local entries taking precedence.

#### 2a. Taint source catalogs

```yaml
# taint_sources/crypto.yaml
description: "Cryptographic decryption outputs — data is now plaintext"
taint_label: plaintext

sources:
  typescript:
    - module: crypto
      functions: [decrypt, subtle.decrypt]
      return_tainted: true
    - module: "@noble/ciphers"
      functions: ["*.decrypt"]
      return_tainted: true

  rust:
    - module: aes_gcm
      methods: [Aes256Gcm::decrypt, Aes256Gcm::decrypt_in_place]
      return_tainted: true
    - module: ring::aead
      methods: [OpeningKey::open_in_place]
      return_tainted: true
      note: "Decrypts in-place; the buffer argument becomes tainted"
      argument_tainted: [0]
```

```yaml
# taint_sources/crdt.yaml
description: "CRDT reads — data is collaboration content"
taint_label: crdt_content

sources:
  typescript:
    - module: yjs
      methods: [Y.Map.get, Y.Map.toJSON, Y.Array.get, Y.Array.toArray, Y.Text.toString, Y.Doc.getMap, Y.Doc.getArray, Y.Doc.getText]
      return_tainted: true
    - module: "@blocksuite/store"
      methods: [Doc.getBlockById, Page.root]
      return_tainted: true
```

```yaml
# taint_sources/key_material.yaml
description: "Cryptographic key material"
taint_label: key_material

sources:
  typescript:
    - module: crypto
      functions: [subtle.generateKey, subtle.importKey, subtle.deriveKey, subtle.deriveBits]
      return_tainted: true
    - property_access: "*.hash"       # URL fragment as key material (property read, not a function call)
      read_tainted: true             # The value read from this property is tainted
      note: "PlazaFlow uses URL fragments to pass key material; the def/use extractor treats this property access as a taint source when the receiver matches a URL-typed variable"

  rust:
    - module: hkdf
      methods: [Hkdf::expand, Hkdf::new]
      return_tainted: true
```

#### 2b. Taint sink catalogs

```yaml
# taint_sinks/relay_communication.yaml
description: "Data sent to untrusted relays"
zone: relay
trust_level: untrusted

sinks:
  typescript:
    - module: ws
      methods: [WebSocket.send]
    - pattern: "relay*.send"
    - pattern: "relay*.write"

  rust:
    - module: tungstenite
      methods: [WebSocket::send, WebSocket::write]
```

```yaml
# taint_sinks/host_filesystem.yaml
description: "Writes to the compute host filesystem (not guest VM)"
zone: host_fs
trust_level: untrusted

sinks:
  typescript:
    - module: fs
      functions: [writeFile, writeFileSync, appendFile, createWriteStream]
    - module: fs/promises
      functions: [writeFile, appendFile]

  rust:
    - module: std::fs
      functions: [write, File::create, File::write_all, OpenOptions::open]
```

#### 2c. Sanitizer catalogs

```yaml
# taint_sanitizers/encryption.yaml
description: "Encryption converts plaintext to ciphertext"
transforms:
  - input_taint: plaintext
    output_taint: ciphertext          # Ciphertext is safe for relay zone
    functions:
      typescript:
        - crypto.subtle.encrypt
        - "Aes256Gcm.encrypt"
      rust:
        - aes_gcm::Aes256Gcm::encrypt
        - ring::aead::SealingKey::seal_in_place

  - input_taint: key_material
    output_taint: derived_key         # HKDF output is a derived key, not raw material
    functions:
      typescript:
        - crypto.subtle.deriveKey
        - crypto.subtle.deriveBits
      rust:
        - hkdf::Hkdf::expand
```

```yaml
# taint_sanitizers/vsock.yaml
description: "virtio-vsock is the approved channel for plaintext to VM guest"
transforms:
  - input_taint: plaintext
    output_taint: guest_local         # Data is now inside the VM — no longer host-visible
    functions:
      rust:
        - vsock::VsockStream::write
        - vsock::VsockStream::write_all
```

### 3. Taint propagation

#### 3a. On native DDG (primary path)

When a function has been analyzed by the native CFG builder + reaching-def solver (i.e., a def/use extractor exists for its language), taint propagation is a forward graph walk on the computed DDG edges:

1. **Identify taint sources.** For each DDG-analyzed function, match call sites against the taint source catalogs (§2a). If a call site's callee is a taint source, mark the variables receiving its return value with the corresponding taint label.
2. **Propagate through DDG edges.** Walk forward: if variable `v` is tainted at statement S, and a DDG edge connects S's definition of `v` to a use of `v` at statement T, then T inherits the taint.
3. **At call sites, apply function summaries (§4).** If the callee has a summary (inferred or declared), propagate taint through the call according to the summary's param-to-return and param-to-call mappings.
4. **At sanitizer calls (§2c), transform the taint label** (e.g., `plaintext` → `ciphertext`).
5. **If tainted data reaches a sink (§2b), record a taint-flow finding.**

#### 3b. Structural fallback (no extractor for the language)

When a language lacks a def/use extractor, or a specific function exceeds the 4,000-definition bail-out threshold, taint propagation falls back to call-graph BFS — the Phase 1 structural approach:

1. Build a directed graph of call edges from taint sources to taint sinks.
2. For each source→sink path, check whether any sanitizer appears on the path.
3. If no sanitizer exists on *any* path from source to sink, report a violation.

This is strictly less precise than DDG-based analysis (it cannot distinguish between two variables in the same function), but it catches the most common class of violation: missing sanitizers on entire call paths.

**Dominance-based sanitizer checking.** The structural fallback uses a two-phase BFS to check sanitizer coverage: (1) compute the set of nodes reachable from the taint source *without* passing through any sanitizer function, (2) check whether any taint sink is in that set. This correctly handles cases where some paths are sanitized and others are not — a single unsanitized path from source to sink is a violation even if other paths are sanitized.

#### 3c. Mixed-coverage analysis

When some functions on a taint path have DDG data and others do not (due to missing extractors for some languages or per-function bail-out), the solver uses a **best-effort** strategy:

- DDG-analyzed segments provide variable-level precision.
- Non-DDG segments are bridged with structural reachability.
- The verdict reflects the weakest link: if a **critical path segment** (defined in §3d) lacks DDG coverage, the verdict is "Inconclusive (no DDG)" rather than "Confirmed" or "Violated."
- If the structural analysis alone is sufficient to confirm or violate (e.g., no call path exists at all, or the path clearly lacks a sanitizer), the verdict is reported at structural confidence without requiring DDG coverage.

#### 3d. Critical path segments

A function on a taint path is a **critical segment** if imprecision at that function could change the verdict. Specifically:

| Category | Why it's critical |
|----------|------------------|
| **(a) Source function** — contains the taint source | Imprecision here means we may mis-identify what is tainted |
| **(b) Sink function** — contains the taint sink | Imprecision here means we cannot distinguish tainted from untainted data reaching the sink |
| **(c) Sanitizer function** — calls a sanitizer | Imprecision means we cannot confirm whether the tainted variable is the one being sanitized |
| **(d) Fan-out function** — receives both tainted and untainted data and passes them to different callees | Structural analysis cannot distinguish which callee receives the tainted argument (e.g., function F receives one tainted and one untainted argument and passes them to different call targets) |

Functions that merely **pass data through** (call a callee with tainted arguments, return a tainted value) are **not** critical — structural reachability suffices for pass-through, because the function summary (§4) captures whether arguments flow to returns regardless of DDG coverage.

**Practical implication:** If the source function and the sink function both have DDG coverage, the verdict can be "Confirmed" or "Violated" even if intermediate pass-through functions lack DDG data.

### 4. Function summaries

#### 4a. Inferred summaries

After DDG analysis (via the native CFG builder and reaching-def solver), each analyzed function yields a summary:

```python
@dataclass
class FunctionSummary:
    """How a function propagates taint between its interface points."""
    symbol_id: str
    param_to_return: dict[int, bool]     # Does param N flow to return value?
    param_to_param: dict[tuple[int,int], bool]  # Does param N flow to out-param M?
    param_to_calls: dict[int, list[CallArgFlow]]  # Does param N flow to a call arg?
    return_sources: list[int]            # Which params contribute to return?
```

**`param_to_param` is not deferred.** TypeScript object mutation (`function init(config) { config.ready = true; }`) and Rust `&mut` parameter mutation are common in PlazaFlow's stack. The native DDG captures these flows — the summary inference logic must extract them. For languages without DDG data, `param_to_param` is conservatively assumed `True` for all mutable/reference parameters.

**Inference algorithm.** Given a function's DDG (set of definition→use edges computed by the reaching-def solver):

1. **`param_to_return`:** For each parameter P, walk forward through DDG edges from P's definition site. If any path reaches a use site inside a `return` statement, record `param_to_return[P] = True`.
2. **`param_to_calls`:** For each parameter P, walk forward through DDG edges. If any path reaches a use site that is an argument at position K of a call to function F, record a `CallArgFlow(callee=F, arg_index=K)` in `param_to_calls[P]`.
3. **`param_to_param`:** For each parameter P, walk forward through DDG edges. If any path reaches a use site that is a write/mutate of another parameter M (direct assignment, field write, or method call on M), record `param_to_param[(P, M)] = True`. In Rust, `&mut` parameters and `self` receivers are the primary targets; in TypeScript, any object parameter is a candidate.
4. **`return_sources`:** The inverse of `param_to_return` — which parameter indices contribute to the return value. Computed as a byproduct of step 1.

All walks are bounded by the function's DDG — no interprocedural chasing. If a function has no DDG (extractor unavailable or bail-out), the summary is conservative: `param_to_return[N] = True` for all N, `param_to_param[(N, M)] = True` for all mutable/reference parameter pairs.

These summaries are the bridge between intraprocedural and interprocedural analysis. When the taint solver encounters a call, it looks up the callee's summary to determine how taint propagates through the call.

#### 4b. Declared summaries (YAML)

For stdlib and framework functions whose source is not analyzed, summaries are declared in YAML:

```yaml
# function_summaries/typescript_stdlib.yaml
summaries:
  - function: "Buffer.from"
    param_to_return: {0: true}          # Input data flows to output buffer

  - function: "JSON.stringify"
    param_to_return: {0: true}          # Object flows to string representation

  - function: "crypto.subtle.encrypt"
    param_to_return: {1: true}          # Plaintext (arg 1) flows to ciphertext return
    sanitizes: {1: {from: plaintext, to: ciphertext}}

  - function: "Array.prototype.map"
    param_to_return: {0: true}          # Array elements flow to output
    callback: # Structured callback flow (replaces boolean callback_propagates)
      param_index: 1                    # Which outer arg is the callback (map's arg 1)
      caller_to_callback_args:          # How outer args map to callback params
        0: [0]                          # Outer arg 0 (array elements) → callback param 0
      callback_return_to_outer_return: true  # Callback return value flows to outer return

  - function: "Array.prototype.forEach"
    param_to_return: {}                 # forEach returns undefined
    callback:
      param_index: 1
      caller_to_callback_args:
        0: [0]                          # Array elements → callback param 0
      callback_return_to_outer_return: false  # forEach discards callback return

  - function: "Promise.prototype.then"
    param_to_return: {}                 # .then() returns a new Promise, not the callback return directly
    callback:
      param_index: 0                    # onFulfilled callback
      caller_to_callback_args:
        self: [0]                       # Resolved value of the promise → callback param 0
      callback_return_to_outer_return: true  # Callback return becomes new promise's resolved value

  - function: "console.log"
    param_to_return: {}                 # Sink — nothing flows out
    side_effect: true
```

```yaml
# function_summaries/rust_stdlib.yaml
summaries:
  - function: "Vec::push"
    param_to_self: {0: true}            # Argument flows into the Vec
    mutates_self: true

  - function: "String::from"
    param_to_return: {0: true}

  - function: "std::fs::write"
    param_to_return: {}
    side_effect: true                   # Sink — writes to filesystem
```

### 5. Cross-language taint propagation

Hypergumbo's existing linkers create edges across language boundaries: `wasm_bindgen`, `tauri_ipc`, `napi`, `grpc`, `pyffi`. Taint propagation extends through these edges using the function-summary mechanism:

1. A WASM-exported function has an inferred summary on the Rust side (from its DDG analysis).
2. The `wasm_bindgen` linker edge connects a TypeScript call to the Rust function.
3. When the taint solver encounters the call on the TS side, it looks up the Rust function's summary to determine if the tainted TS argument flows to the Rust return value or to a Rust-side sink.

No new linkers are needed — existing linker edges provide the call connectivity, and function summaries (§4) provide the argument-to-parameter data-flow mapping. However, this means every cross-language bridge function needs either an inferred summary (from DDG analysis of the callee's source) or a declared summary (YAML). Bridge functions without summaries receive the default conservative summary (all parameters flow to return), which is sound but may produce false positives.

**Rust has full DDG coverage.** Since Rust has a native def/use extractor (§1c), both sides of TypeScript↔Rust bridges (WASM bindgen, Tauri IPC) have DDG precision. Taint flowing from TypeScript CRDT reads through WASM exports into Rust relay code — or from Rust crypto outputs back through wasm-bindgen to TypeScript — is tracked with variable-level accuracy on both sides of the bridge.

**Serialization boundary semantics.** Existing linker edges model call connectivity, not data serialization. When data crosses an IPC bridge (Tauri IPC, gRPC, WASM), it is typically serialized (JSON, protobuf, wasm-bindgen encoding) on one side and deserialized on the other. The taint solver treats all IPC bridge boundaries as **taint-transparent by default**: the deserialized value on the receiving side inherits the taint labels of the serialized value on the sending side. This is the sound overapproximation — serialization does not sanitize data, it only changes its encoding. If a specific serialization step *does* sanitize (e.g., a custom serializer that strips sensitive fields), it can be modeled as a sanitizer in the taint catalog (§2c). The default transparency avoids requiring declared summaries for every serialization/deserialization function pair.

**Caveat: serialization functions still need function summaries for intraprocedural taint flow.** Bridge-level transparency handles taint propagation *across* the linker edge, but the taint solver also needs to track taint *to* the serialization call within the sending function. For example, in `const payload = JSON.stringify(plaintextData); ipcRenderer.send("channel", payload);`, the solver must know that `JSON.stringify` propagates taint from arg 0 to its return value — otherwise taint is lost at the `stringify` call before it reaches the bridge. This means every serialization library used at an IPC boundary requires a declared summary in §4b. The §4b examples include `JSON.stringify` and `Buffer.from`, but this is an ongoing catalog maintenance burden: each new serialization library (msgpack, protobuf-ts, serde_json, etc.) requires a summary entry. Phase 3 should include a survey of serialization libraries used at IPC boundaries in the target codebase and ensure summaries exist for all of them.

**Mitigation: default-conservative summaries for unknown functions.** To reduce the catalog maintenance burden, the taint solver applies a default summary for functions without an explicit YAML declaration: all parameters are assumed to flow to the return value (`param_to_return[N] = True` for all N). This is the sound overapproximation — taint is never silently lost at an undeclared function call. The cost is potential false positives (an undeclared function that discards its input would still propagate taint through it), but this is preferable to false negatives (silently dropping taint). Additionally, a heuristic name-matching rule treats functions whose names match common serialization patterns (`serialize`, `stringify`, `encode`, `marshal`, `to_json`, `to_bytes`, `into_bytes`, `to_string`) as `param_to_return: {0: true}` with high confidence. Explicit YAML summaries override both defaults. This means the system works out-of-the-box for common serialization libraries without requiring per-library catalog entries, while still allowing precise summaries for edge cases.

For the virtio-vsock bridge (PlazaFlow-specific): the vsock channel is modeled as a sanitizer (§2c) that transforms `plaintext` → `guest_local`, reflecting that data crossing into the VM guest is no longer on the host filesystem but is visible inside the VM.

### 6. Enhanced `verify-claims`

Extend `verify-claims` to support taint-flow claims alongside the existing structural claims:

```yaml
# security-claims.yaml
claims:
  # Existing structural claims (ADR-0016) still work:
  - id: SC-001
    text: "No subprocess calls exist in the relay module"
    constraint:
      boundary: subprocess
      must_not_exist:
        in_files: ["relay/**"]

  # New taint-flow claims:
  - id: TF-001
    text: "Relays never see plaintext document content"
    taint_flow:
      source_taint: plaintext
      prohibited_sink_zone: relay
      allowed_sanitizers: [encrypt_content, encrypt_navigation, encrypt_attribution]

  - id: TF-002
    text: "Decrypted content is never written to the host filesystem"
    taint_flow:
      source_taint: plaintext
      prohibited_sink_zone: host_fs
      allowed_sanitizers: [vsock_channel]  # Plaintext may reach VM guest via vsock (modeled as sanitizer: plaintext → guest_local)
      note: "vsock is the approved channel for plaintext to VM guest; see taint_sanitizers/vsock.yaml"

  # NOTE: `allowed_paths` and `exceptions` (e.g., function_pattern matching,
  # requires_flag, severity overrides) are deferred to a future phase.
  # Phase 1 supports: source_taint, prohibited_sink_zone, allowed_sanitizers, note.
  # The three core claim fields already cover TF-001 through TF-004.
  # allowed_paths/exceptions add PlazaFlow-specific complexity that should be
  # validated against real usage before committing to the schema.

  - id: TF-003
    text: "Ephemeral awareness data is never persisted"
    taint_flow:
      source_taint: ephemeral
      prohibited_sink_zone: persistent_storage
      note: "Awareness state should flow only through WebRTC, never to relay snapshots"

  - id: TF-004
    text: "Fragment key material does not leak beyond HKDF derivation"
    taint_flow:
      source_taint: key_material
      prohibited_sink_zone: [relay, host_fs, net_send]
      allowed_sanitizers: [hkdf_derive]
      note: "Derived keys (content/nav/attribution DEKs) are allowed to flow; raw fragment key is not"
```

Verdicts become more precise:

| Verdict | Meaning |
|---------|---------|
| **Confirmed** | No taint-flow path exists from source to prohibited sink |
| **Confirmed (sanitized)** | Paths exist, but all pass through an allowed sanitizer |
| **Violated** | Taint flows from source to sink without sanitization — with exact path |
| **Inconclusive (no DDG)** | A critical path segment (source, sink, or sanitizer function — see §3c) lacks DDG coverage (language not supported, bail-out, or budget cap) |
| **Inconclusive (opaque)** | Path crosses an opaque boundary (native code without source) |

**Degradation policy for mixed-coverage paths.** When a taint path passes through both DDG-analyzed functions and non-DDG functions (due to language support gaps, bail-out, or budget cap), the solver uses a **best-effort** strategy: structural reachability (ADR-0016) fills gaps in the DDG. The verdict is "Inconclusive (no DDG)" only if the gap is on a *critical segment* of the path — a function containing the taint source, the taint sink, or a sanitizer call (see §3c for the full definition). Pass-through functions that merely forward data do not trigger "Inconclusive" because their function summaries (§4) capture argument-to-return flow regardless of DDG coverage. If the structural analysis alone is sufficient to confirm or violate (e.g., no call path exists at all, or the path clearly lacks a sanitizer), the verdict is reported at structural confidence without requiring DDG coverage. This prevents a single unsupported function from downgrading every claim to "Inconclusive."

### 7. Scope boundaries

#### 7a. Included capabilities (with precision limits)

- **Field-sensitivity lite (Phase 2).** While full alias analysis is excluded (see §7b), *direct* field access on tainted objects is common enough — especially in CRDT-heavy code — that ignoring it produces unacceptable false positive rates. Phase 2 includes a limited form of field sensitivity: taint propagates through `.` member access and method returns on tainted receivers. Specifically:
  - If `x` is tainted, then `x.field`, `x.method()`, and `x[key]` inherit `x`'s taint.
  - If `obj.field = tainted_value`, then `obj.field` is tainted but `obj.other_field` is not (field-level granularity for direct writes).
  - Aliased references (`y = x; y.field`) propagate taint through the assignment chain (covered by reaching definitions), but indirect aliasing (`container.get("key")` returning the same object as a different `container.get("key")` call) is not tracked.

  This covers the CRDT method-chain pattern that motivates the ADR:
  ```typescript
  const map = doc.getMap("blocks");     // tainted: crdt_content (source catalog)
  const block = map.get(blockId);       // tainted: method return on tainted receiver
  relay.send(JSON.stringify(block));     // violation: tainted data reaches relay sink
  ```
  Without field-sensitivity lite, the solver would either (a) conservatively taint everything touched by any tainted object (too many false positives) or (b) lose taint at field accesses (false negatives — unacceptable for a security tool). This middle ground handles direct access patterns without requiring pointer analysis.

#### 7b. Excluded capabilities

- **Full alias analysis.** If `x = secret; y = x; z = y`, the solver tracks this through reaching definitions. But if `obj.field = secret` and later `other_ref.field` accesses the same object via a different reference, the solver does not track that — tracking aliased references requires pointer analysis, which is out of scope.

- **Path sensitivity.** The solver does not track which branch conditions hold at each point. It knows that `encrypt()` is on one path and `send()` is on another, but it does not prove they are mutually exclusive. Overapproximation (false positives) is preferred over underapproximation (missed violations).

- **Whole-program analysis.** The solver is intraprocedural + function summaries. It does not build a whole-program DDG. This bounds both cost and complexity.

- **Dynamic dispatch precision.** If a method call resolves to multiple candidates (virtual dispatch), all candidates' summaries are unioned. This is sound (no missed flows) but imprecise (may report flows that cannot actually happen).

- **Def/use extractors for all 119 languages.** Extractors exist only for languages where someone has contributed one (Rust first, then TypeScript, Python, etc.). Languages without an extractor fall back to structural analysis (ADR-0016 call-graph tracing). The accretion model (§1e) provides a clear path for expanding coverage as demand arises.

- **Closure capture taint.** If a tainted value is captured by a closure and the closure is passed to a higher-order function like `Array.map()`, the map output should inherit taint. Declared function summaries (§4b) express structured callback flow via the `callback` schema — which outer arguments map to which callback parameters, and whether the callback return flows to the outer return. This handles *argument-mediated* taint flow through higher-order functions (e.g., tainted array elements flowing through `map`'s callback). However, *capture-mediated* taint flow — where the closure captures a tainted variable from the enclosing scope and uses it in the callback body — requires analyzing the closure's capture set: which variables from the enclosing scope does the closure reference? Initial approach: conservative overapproximation. If any captured variable is tainted, treat the closure's return value as tainted. This may produce false positives (e.g., a closure captures both a tainted value and an untainted value, but only returns the untainted one). Precise closure capture analysis is a future refinement.

## Consequences

### Positive

- **`verify-claims` becomes evidence-backed.** Security claims about PlazaFlow's encryption, key isolation, and data separation can be checked against actual variable-level data flow, not just call-graph reachability.
- **Slicing improves.** The DDG edges enable data-flow-aware slicing: "show me everything that the return value of `decrypt()` actually flows to" instead of "show me everything 3 hops away."
- **IO boundary analysis becomes precise.** False positives where a function calls both `encrypt()` and `send()` are eliminated — only paths where plaintext data actually reaches the send are flagged.
- **YAML-driven extensibility.** New taint types, sources, sinks, and sanitizers are added by writing YAML, not code. Same pattern as IO primitives and dataflow patterns.
- **No external dependencies.** Pure Python implementation — no JVM, no subprocess coordination, no external tool versioning. `pip install hypergumbo` gets the full taint analysis capability for languages with extractors.
- **Full Rust coverage from day one.** Rust has a native def/use extractor, closing the gap for PlazaFlow's three Rust-side taint claims (TF-001, TF-002, TF-004). Both sides of TypeScript↔Rust bridges have DDG precision.
- **Accretion model.** The system grows as contributors add def/use extractors, taint catalogs, and function summaries — the same way hypergumbo already grows with IO primitive catalogs, dataflow patterns, and language analyzers. LLMs can assist at development time (generating draft extractors and catalogs), keeping the barrier to contribution low while the runtime stays fully deterministic.
- **Project-agnostic infrastructure.** While PlazaFlow is the first client and motivating use case, the entire infrastructure (CFG builder, reaching-def solver, taint propagation engine, function summaries, `verify-claims` extensions) is project-agnostic. PlazaFlow-specific artifacts are limited to project-local taint catalogs and claim YAML files. If PlazaFlow's architecture changes, only those project-local YAML files need updating — the core infrastructure, built-in catalogs, and def/use extractors are unaffected.

### Negative

- **Native infrastructure maintenance.** The CFG builder (~700-1,000 lines including semantic hooks), reaching-def solver (~100-150 lines), and tree-sitter normalization mappings (YAML per language) become code hypergumbo maintains. Risk is low for the algorithms — these are well-understood with stable implementations (the same algorithms Joern uses, ported to Python). The YAML mappings and semantic hooks carry ongoing maintenance cost: tree-sitter grammar updates may require mapping updates (see §1a normalization discussion), and new languages may occasionally require new semantic hooks (though the existing set covers the most common non-standard control-flow patterns).
- **Per-language extractor effort.** Each new language needs a def/use extractor (~200-1,000 lines depending on language complexity). Languages without extractors fall back to structural analysis. This is incremental investment, not upfront cost — extractors are contributed as demand arises.
- **Serialization summary maintenance burden.** Every IPC boundary in a polyglot app involves serialization, and each serialization library requires a declared function summary (§4b) to avoid losing taint at the serialization call before it reaches the bridge. Each new serialization library adopted by a target project requires a new summary entry, making this an ongoing catalog maintenance cost that scales with the number of IPC boundaries and serialization formats in use.
- **Overapproximation.** Without full alias analysis or path sensitivity, the solver will report some false positives. Field-sensitivity lite (§7) reduces the most common false positive sources (method chains on tainted objects), but indirect aliasing and branch-condition interactions will still produce noise. This is acceptable for a security-oriented tool (false positives are preferable to false negatives), but users should expect some noise. See §9 for the precision measurement plan.
- **Increased test surface.** CFG builder, reaching-def solver, def/use extractors, taint propagation, function summaries, and cross-language flow each need thorough testing. 100% coverage requirement applies. See §8 (Testing Strategy) for approach.

### Relationship to existing ADRs

| ADR | Relationship |
|-----|-------------|
| ADR-0012 (multi-pass) | Native DDG computation is a new pass in the multi-pass pipeline (between Pass 1 analysis and taint propagation) |
| ADR-0015 (dataflow access modes) | Taint labels extend `Edge.meta` alongside existing `access_mode`. See interaction details below. |
| ADR-0016 (IO boundaries) | Taint-zone analysis replaces call-graph BFS with DDG-backed tracing for languages with extractors; structural tracing remains the fallback |

#### Interaction with ADR-0015 `access_mode` metadata

ADR-0015's `access_mode` field (read/write/mutate/delete) classifies what an edge *does*. Taint labels classify what data an edge *carries*. These are complementary, not competing:

- **`access_mode` informs taint propagation direction.** A `write` edge from function A to shared state S means A's taint can flow *into* S. A `read` edge from S to function B means S's taint flows *into* B. The taint solver should consult `access_mode` when propagating taint through edges that have it, using the same directional logic as the existing dataflow-aware slicer in `slice.py` (forward: follow write/mutate; reverse: follow read).
- **Taint labels are stored in `Edge.meta` alongside `access_mode`.** New keys: `taint_labels` (list of active taint tags on this edge), `taint_sanitized_by` (sanitizer that transformed taint on this edge, if any). These do not conflict with existing `access_mode`, `dest_access_mode`, or `channel` keys.
- **The dataflow-aware slicer (`--dataflow` flag) and taint analysis are complementary.** A future `--taint` slice flag could filter to edges carrying specific taint labels, analogous to how `--dataflow` filters by `access_mode`. This is not in scope for this ADR but is a natural extension.
- **Edges without `access_mode` are taint-propagated conservatively.** If an edge lacks ADR-0015 metadata (e.g., a plain `calls` edge from a language without dataflow YAML), the taint solver treats it as a potential propagation path in both directions. This matches the existing graceful-degradation behavior in `slice.py`.
- **Precedence rule: DDG edges supersede `access_mode` annotations for taint propagation.** When a function has DDG coverage (a def/use extractor produced reaching-def edges), the taint solver uses DDG edges for intraprocedural propagation and ignores `access_mode` annotations on call edges *within* that function — the DDG provides strictly more precise information. `access_mode` annotations remain authoritative for (a) functions without DDG coverage (structural fallback), (b) edges between functions (interprocedural call edges where `access_mode` informs propagation direction), and (c) the dataflow-aware slicer (`--dataflow` flag), which is independent of taint analysis. This mirrors the existing precedence pattern in `dataflow.py` (line 231-233) where linker-provided annotations override automatic annotations — the more precise source wins.

## Phased Implementation

| Phase | Scope | Enables | Estimated effort |
|-------|-------|---------|-----------------|
| 1 | Taint catalogs + structural taint flow | `verify-claims` with taint-flow claims checked via call-graph BFS with dominance-based sanitizer checking. Immediately catches "no sanitizer on any call path" violations. See note below. | 2-3 weeks |
| 1b | Precision measurement (see §9 for validation targets) | Baseline false positive rate for structural taint flow. Informs Phase 2 prioritization. See §9. | 1 week |
| 2 | Native CFG builder + reaching-def solver + Rust def/use extractor (core patterns) + field-sensitivity lite (§7) | Variable-level taint tracking within Rust functions for Simple/Moderate patterns. False positive reduction at Rust-side sinks. | 4-6 weeks |
| 2b | Rust hard patterns: borrow aliases, `ref`/`ref mut`, macro analysis | Full Rust def/use coverage. Prioritized by Phase 2 precision measurement. | 2-3 weeks |
| 3 | Function summaries (inferred from DDG + YAML-declared) + TypeScript def/use extractor | Interprocedural taint flow. Cross-function data tracking. DDG-backed analysis for both PlazaFlow primary languages. | 4-5 weeks |
| 4 | Cross-language taint propagation via existing linkers | End-to-end taint flow through WASM/IPC/FFI bridges. Full PlazaFlow verification with DDG precision on both sides. | 3-4 weeks |

**Phase 1 is the MVP; Phase 1b is the decision gate for further investment.** Phase 1 is not an experiment — it is a standalone, shippable capability. Without any intraprocedural analysis, Phase 1 extends `verify-claims` to reason about taint labels on call-graph paths. For example, the claim "relays never see plaintext" becomes: "is there any call path from a `plaintext` source to a `relay` sink that does not pass through an `encrypt` sanitizer?" This is strictly more powerful than the current `must_not_exist`/`max_chains` constraints and can catch real violations — such as a code path that sends data to a relay without encrypting it first. Phase 1 cannot eliminate false positives *within* a function (that requires Phase 2), but it catches missing sanitizers on entire call paths, which is the most common class of security bug. Phase 1b measurement determines whether to *invest further in intraprocedural precision*, not whether Phase 1 itself was worthwhile. Phase 1b should categorize false positives by *where* in the taint path the imprecision occurs (source function, intermediate, sink function) to inform whether Phase 2 (Rust extractor for sink-side precision) or Phase 3 (TypeScript extractor for source-side precision) delivers more value first.

Phase 2 builds the language-parameterized shared infrastructure (CFG builder with semantic hooks, reaching-def solver) and the first def/use extractor (Rust core patterns). The infrastructure is ~800-1,150 lines of Python implementing well-understood algorithms (same as Joern's `CfgCreator` and `DataFlowSolver`, ported to Python, plus semantic hooks for non-standard control flow and gen/kill computation). The Rust core extractor is ~500-800 lines handling Simple and Moderate patterns (see §1c for phased scope); hard patterns (borrow aliases, `ref`/`ref mut`, macro analysis) are deferred to Phase 2b (~300-500 additional lines). The main risk is extractor correctness — edge cases in pattern matching, the `?` operator, and nested destructuring need thorough testing. Phase 2 includes at least 10 test programs covering straight-line flow, branching, loops, function calls, sanitizers, and field access. Phase 2b adds tests for borrow-based mutation and macro invocation patterns.

Phase 3 adds the TypeScript extractor (~600-1,000 lines, the most complex due to destructuring and optional chaining) and function summaries. With both Rust and TypeScript extractors, all four PlazaFlow taint claims have DDG precision on both source and sink sides.

**Total estimated effort: 16-22 weeks.** These are floor-to-ceiling estimates; the lower bound assumes few surprises in extractor development, the upper bound accounts for the YAML mapping maintenance, semantic hook edge cases, and name-resolution coupling discussed in §1a and §1c. Phase 2b is optional if Phase 2 precision measurement shows borrow aliases are not a significant false negative source. Additional extractors (Python, Go, Java, etc.) are contributed via the accretion model (§1e) as demand arises — each is an incremental ~200-1,000 line addition, not a phase of the core plan. If PlazaFlow code is delayed >3 months past Phase 1, a Python extractor (~200-400 lines, building on existing `annotate_dataflow_ast()`) should be built first to validate infrastructure against hypergumbo's own codebase (see "Alternative considered: Python first" in Context).

### 8. Testing strategy

Hypergumbo requires 100% test coverage. The native architecture introduces several new subsystems that need structured testing:

- **Structural taint flow (Phase 1).** End-to-end tests with synthetic call graphs verifying dominance-based sanitizer checking. Test cases: (a) all paths sanitized → confirmed, (b) one unsanitized path among sanitized paths → violated, (c) no paths exist → confirmed, (d) source and sink in same function → violation detected structurally. Taint catalog loading and matching tested against the existing IO primitive catalog test patterns.

- **CFG builder (Phase 2).** Unit tests verifying CFG structure for small programs (5-20 lines each). Test cases per control-flow construct: sequential, if/else, while/for loops, break/continue, return, try/catch, switch/match. For each test, verify entry/exit blocks, edge types, and statement ordering against hand-computed expected CFGs. YAML CFG node mappings tested for each supported language.

- **Reaching-def solver (Phase 2).** Unit tests verifying fixpoint computation on hand-crafted CFGs. Test cases: (a) single straight-line block, (b) diamond branch (definition on one branch only), (c) loop with redefinition, (d) multiple definitions of the same variable. Verify gen/kill sets and final reaching-def edges.

- **Def/use extractors (Phase 2+).** Each extractor tested independently with small source files. At least 10 test programs per language, covering: simple bindings, pattern destructuring, reassignment, field access, method calls, loop variables, closures, and early return (`?` for Rust). Tests verify that `extract()` returns the correct `defines` and `uses` lists for each AST node.
  - **Graceful degradation:** Tests verify that languages without extractors fall back to structural taint flow without errors.

- **Taint propagation (Phase 2).** End-to-end tests from taint source to sink, including sanitizer transforms. Test both "taint reaches sink" (violation) and "taint is sanitized before sink" (confirmed) paths. Use pre-computed DDG data (from the native solver on small test programs) to test DDG-backed propagation.

- **Function summaries (Phase 3).** Test both inferred summaries (verify that the inferred summary from DDG data matches hand-computed expectations) and declared YAML summaries (verify that the solver consults them at call sites).

- **Cross-language flow (Phase 4).** Integration tests with small polyglot fixtures (TS + Rust via WASM, TS + Rust via Tauri IPC) verifying taint propagation across linker edges. **Fixture strategy:** Tests should use *synthetic linker edges* (manually constructed `Edge` objects with the correct `edge_type` and `meta` fields) rather than requiring actual WASM compilation or Tauri IPC setup in CI. This validates that the taint solver correctly propagates through linker edges without introducing heavy build dependencies.

- **Property-based tests (all phases).** Per AGENTS.md, tests verify invariants rather than exact output. Property tests complement the example-based tests above:
  - **CFG builder:** Every CFG has exactly one entry and one exit block. Every block is reachable from entry. The exit block is reachable from every non-dead-code block. No block has zero successors unless it is the exit block. Break/continue edges resolve to enclosing loop boundaries.
  - **Reaching-def solver:** Gen and kill sets per block are disjoint. Fixpoint terminates in at most N iterations (where N = number of blocks). The number of reaching definitions at any point ≤ total definitions in the function.
  - **Taint propagation:** If every source→sink path passes through an allowed sanitizer, the verdict is never "Violated." Removing a sanitizer from any source→sink path produces a "Violated" verdict. Adding a taint source never reduces the set of reported violations (monotonicity).

### 9. Precision measurement plan

The false positive rate (Open Question #1) is an empirical question that must be answered by measurement, not speculation. The phased implementation provides natural measurement points:

**Phase 1b measurement (structural taint flow):**

*If PlazaFlow code is available:*
1. Run structural taint-flow `verify-claims` on PlazaFlow with the four claims from §6 (TF-001 through TF-004).
2. Record all reported violations.
3. Manually classify each violation as true positive (genuine data-flow concern) or false positive (structural path exists but data does not actually flow).
4. Compute precision = true positives / (true positives + false positives).
5. Document findings in the lab notebook.

*If PlazaFlow code is not yet available:*
1. Construct a PlazaFlow-like reference implementation (~500-1,000 lines of Rust+TypeScript) with the same trust-boundary architecture: CRDT reads → encryption → relay send, with intentional violations (e.g., an unencrypted relay send path) and intentional non-violations (e.g., encrypted data reaching relay). See "Validation before PlazaFlow exists" in Context.
2. Define claims against the reference implementation and run measurement as above.
3. Additionally, run the built-in crypto/filesystem taint catalogs against 2-3 open-source Rust+TypeScript projects with known security properties to measure precision on real-world code.
4. Document findings and note that these are proxy measurements; re-measure against PlazaFlow when available.

**Decision gate:** If Phase 1 precision is >80% (fewer than 1 in 5 findings are false positives), Phases 2-4 are lower priority — structural analysis is already useful. If precision is <50%, Phase 2 is high priority — intraprocedural analysis is needed to make the tool trustworthy. Between 50-80%, proceed with Phase 2 but consider whether the investment is justified for non-PlazaFlow use cases. Note: these thresholds are heuristics, not empirically grounded — adjust based on the actual distribution of findings.

**Phase 2 measurement (native DDG-backed taint flow):**
1. Re-run the same claims on PlazaFlow with DDG-backed taint analysis (Rust extractor for sink-side functions).
2. Compare violation counts to Phase 1b baseline.
3. Compute: (a) how many Phase 1 false positives were eliminated by DDG precision, (b) whether any new findings appeared (functions where intraprocedural analysis reveals flows that structural analysis missed due to edge-level granularity), (c) extractor accuracy (what percentage of targeted functions produced correct DDG results, verified by manual inspection of a sample).
4. Report the precision improvement as a concrete metric.

This measurement plan turns Open Question #1 from a speculation exercise into a data-driven decision.

## Resolved Questions

1. **Taint label lattice.** Resolved: flat tags with explicit sanitizer transforms. A partial-order lattice (`plaintext` > `ciphertext` > `untainted`) was considered but rejected. The sanitizer transform model (§2c) is more expressive: it can model asymmetric transformations like `plaintext → ciphertext` (safe for relay) vs. `plaintext → guest_local` (safe for host but visible in VM) that a linear lattice cannot represent. Flat tags also avoid migration risk — introducing a lattice later would require rewriting every taint catalog and claim file. The claim YAML schema (§6) references taint labels by name; the sanitizer catalogs (§2c) define explicit transforms between them. No partial ordering is needed.

2. **Memory for native DDG computation.** Resolved: streaming strategy adopted in §1f. The solver processes one function at a time (compute DDG → extract summary → free bitsets), bounding peak memory to a single function's analysis rather than all 500 targeted functions simultaneously. Hypergumbo's existing memory pressure guard accounts for DDG computation overhead.

## Open Questions

1. **False positive budget.** How many false positives are acceptable before users stop trusting the tool? This is an empirical question answered by the precision measurement plan (§9). Phase 1b provides the baseline; Phase 2 measurement shows improvement.

2. **Extractor coverage prioritization.** Which languages get def/use extractors after Rust and TypeScript? Candidates: Python (existing `annotate_dataflow_ast()` to build on), Go (popular in target repos, relatively regular syntax), Java (large ecosystem). Prioritization should be informed by demand from taint catalog users — if someone writes SQL injection catalogs for Django, they'll want a Python extractor.

## Acknowledgments

This ADR's architecture is directly informed by studying [Joern](https://joern.io/) (joernio/joern, Apache-2.0), an open-source code analysis platform that generates Code Property Graphs combining AST, control flow, and data dependence. Joern is not a runtime dependency — hypergumbo builds its own native intraprocedural analysis — but Joern's architecture directly inspired the design, and its language frontends serve as reference implementations for writing def/use extractors.

Key architectural insights from Joern that shaped this ADR:

- **Language-agnostic CFG construction** (`CfgCreator.scala` + `Cfg.scala`, ~850 lines combined): One fringe-based builder shared by all 12 language frontends, with zero language-specific overrides. This demonstrated that CFG construction is a solved, language-agnostic problem — the per-language effort concentrates in def/use extraction (2,000-18,000 lines per frontend). The native CFG builder (§1a) ports this same algorithm to Python as a language-parameterized builder: Joern achieves language-agnosticity through AST normalization in its frontends; hypergumbo achieves language-parameterization through YAML mappings and a bounded set of semantic hooks.
- **Compact dataflow solver** (`DataFlowSolver`, 75 lines): The forward/backward worklist algorithm for reaching definitions. The native solver (§1b) is a direct port of this algorithm.
- **Declarative function summaries** (`Semantics` / `FullNameSemantics`): The concept of YAML-declared taint propagation rules for stdlib/framework functions (§4b).
- **Per-function bail-out** (`ReachingDefPass`): The 4,000-definition threshold as a practical complexity bound (adopted in §1f).
- **Def/use extraction complexity**: Joern's per-language frontends (e.g., `jssrc2cpg` at 6,181 lines for JS/TS, `gosrc2cpg` at 3,204 lines for Go) document exactly which AST patterns require def/use handling. These serve as reference implementations when contributors write pluggable extractors for hypergumbo (§1c).

The reaching-definitions algorithm itself is from:

- Aho, A.V., Lam, M.S., Sethi, R., Ullman, J.D. (2006). *Compilers: Principles, Techniques, and Tools* (2nd ed.), Chapter 9: "Machine-Independent Optimizations" — reaching definitions, gen/kill sets, worklist fixpoint algorithm.

## References

- Joern — The Bug Hunter's Workbench. https://joern.io/ — Source: https://github.com/joernio/joern (Apache-2.0)
- Yamaguchi, F., Golde, N., Arp, D., Rieck, K. (2014). "Modeling and Discovering Vulnerabilities with Code Property Graphs." *IEEE Symposium on Security and Privacy.* — The paper introducing Code Property Graphs.
- Aho, A.V., Lam, M.S., Sethi, R., Ullman, J.D. (2006). *Compilers: Principles, Techniques, and Tools* (2nd ed.), Chapter 9: "Machine-Independent Optimizations" — reaching definitions, gen/kill, worklist algorithm.

