# ADR-0014: Generalized Symbol Identity (stable_id / shape_id)

Date: 2026-02-20
Status: Proposed

## Context

The spec (§6, "Identity field semantics") defines `stable_id` and `shape_id` as universal identity fields on every symbol, designed to track symbols across refactors and detect structural changes respectively. In practice, only the Python analyzer computes real signature-based hashes. The remaining 100+ analyzers fall into one of three incomplete categories, each with different problems.

### Current state across analyzers

**Category 1: None (majority).** Java, C, C++, C#, Rust, Go, Ruby, PHP, Swift, Kotlin, Scala, Bash, and most tree-sitter-based analyzers leave both `stable_id` and `shape_id` at the dataclass default of `None`.

**Category 2: Location-based strings (27 analyzers).** Many analyzers set `stable_id` to a path-based string like `{lang}:{rel_path}:{name}:{kind}`. These survive line shifts (no line numbers) but not renames — they are not signature-based. Two analyzers (Hack, Smithy) use a reversed field order `{lang}:{rel_path}:{kind}:{name}`, creating a silent format inconsistency.

**Category 3: Semantic strings with collisions (5 analyzers).** Route and entry-point code in Go, JS/TS, and WGSL sets `stable_id` to the HTTP method or entry type:

| Analyzer | `stable_id` value | Collision scope |
|----------|-------------------|-----------------|
| Go Gin/Echo (`go.py:1186`) | `http_method.lower()` (e.g., `"get"`) | All GET routes in a file |
| Go Gorilla mux (`go.py:1231`) | `"any"` (hardcoded) | All HandleFunc routes |
| JS/TS Express (`js_ts.py:1978`) | `http_method` (e.g., `"GET"`) | All GET routes in a file |
| JS/TS NestJS (`js_ts.py:2211`) | `http_method` or `None` | All same-method handlers |
| WGSL (`wgsl.py:232`) | `entry_type` (e.g., `"vertex"`) | All vertex shaders |
| Python Django CBV (`py.py:1441-1443`) | `item.name.upper()` (e.g., `"GET"`) | All same-method CBV handlers |

This is a spec violation. The spec defines `stable_id` as semantic identity — a unique fingerprint. `GET /users` and `GET /posts` currently produce identical `stable_id="GET"`. The `id` field disambiguates (it includes file, line, and handler name), but `stable_id` is the field designed for cross-refactor tracking.

**Category 4: Python-only real hashes.** Only `py.py` computes both `stable_id` (signature hash via `_compute_stable_id`, line 836) and `shape_id` (AST structure hash via `_compute_shape_id`, line 894). No other analyzer computes `shape_id` at all.

### Implementation gap: containing_module_stable_id

The spec includes `{containing_module_stable_id}` in the hash formula for both tiers. The Python implementation omits it. This means `Foo.process(x)` and `Bar.process(x)` produce the same `stable_id` — a consumer tracking `Foo.process()` cannot distinguish it from an unrelated method with the same signature. This directly undermines the "track across refactors" use case and is a prerequisite for generalization, not a follow-up.

### Grammar version stability

All 46 PyPI grammar dependencies use pessimistic version constraints (`~=X.Y.Z`), providing reasonable stability. However, build-from-source grammars (Lean, Wolfram) use `git clone --depth 1` from HEAD (`scripts/build-source-grammars:105`) with no commit pinning. `shape_id` values for these languages are not reproducible across builds.

The spec provides scheme identifiers (`STABLE_ID_SCHEME`, `SHAPE_ID_SCHEME` in `schema.py:72-73`) for detecting algorithm changes, but a grammar patch release could silently change CST structure without triggering a scheme bump.

### Why this matters now

These issues are latent — nothing downstream currently relies heavily on `stable_id` or `shape_id` for routes or cross-language matching. But the spec promises these fields as universal, and extending the current incomplete implementations to more languages would multiply existing problems rather than converge toward the spec's intent.

## Decision

### 1. shape_id: Generic tree-sitter CST walker in TreeSitterAnalyzer

Add a `compute_shape_id(node)` method to `TreeSitterAnalyzer` that walks any tree-sitter CST, strips identifier/literal/comment nodes, and hashes the structural skeleton.

**Filtering strategy:** Skip these node categories:
- Anonymous nodes (punctuation: type strings starting with `"`)
- Comment nodes (`"comment"`, `"line_comment"`, `"block_comment"`)
- Error/missing nodes (`"ERROR"`, `"MISSING"`)
- Identifier and literal leaf nodes: replace with just their type name (matching Python's `ast`-based approach of stripping names and values while preserving structural position)

**Implementation:** Build on the existing `iter_tree()` utility (`base.py:200-222`), which provides non-recursive depth-first traversal (required for deeply nested files that exceed Python's recursion limit).

**Python keeps its `ast`-based override.** Python's `ast` module produces a cleaner, more semantically precise hash — no formatting noise, no intermediate wrapper nodes. A tree-sitter CST walker would produce *different* hashes for the same Python code. Keeping Python's `_compute_shape_id()` and using tree-sitter for everything else preserves existing hash stability for Python consumers. This is an intentional divergence, not technical debt.

### 2. stable_id untyped tier: classify_parameter_flags() hook

Add a `classify_parameter_flags(params_node) -> ArityFlags` hook method to `TreeSitterAnalyzer`. The base class provides a default implementation that counts children and checks for common node type patterns. Languages with unusual parameter models override the hook.

**ArityFlags** is a lightweight dataclass:
```python
@dataclass(frozen=True)
class ArityFlags:
    param_count: int
    has_defaults: bool
    has_varargs: bool
    has_kwargs: bool
```

**Language-specific node type mappings** (non-exhaustive):

| Concept | Java (ts) | JS/TS (ts) | Ruby (ts) | Go (ts) | Rust (ts) |
|---------|-----------|------------|-----------|---------|-----------|
| Varargs | `spread_parameter` | `rest_pattern` | `splat_parameter` | N/A | N/A |
| Kwargs | N/A | N/A | `hash_splat_parameter` | N/A | N/A |
| Defaults | N/A | `assignment_pattern` | `optional_parameter` | N/A | N/A |

Languages where concepts don't exist (Go has no defaults, Rust has no varargs) set those flags to `False` — the hash still differentiates on `param_count` and `kind`.

The base-class `compute_stable_id()` method computes:
```
sha256({kind}:{param_count}:{arity_flags}:{decorator_names_sorted}:{containing_stable_id})
```

This matches the spec's untyped tier formula. Analyzers can override the entire method if their language needs fundamentally different identity semantics.

### 3. stable_id typed tier: design principles (deferred)

The typed tier (`sha256({kind}:{normalized_signature}:{visibility}:{containing_module_stable_id})`) requires extracting type signatures from tree-sitter CSTs for Java, Go, TypeScript, Rust, C#, Kotlin, and Dart. This intersects with ADR-0006 (Variable Type Inference), which already tracks type extraction capabilities per language.

**Design principles for future implementation:**
- Normalize generic type parameters by position, not name (`T` and `U` → `$0` and `$1`)
- Include return types where the language has them
- Exclude implementation details (method bodies, default values)
- Prefer the typed tier when type information is available; fall back to untyped

Implementation is deferred until the untyped tier is proven across multiple languages.

### 4. Route and entry-point stable_id

Route symbols use a distinct identity scheme:
```
sha256("route:{method}:{path}")
```

Entry-point symbols (like WGSL shader stages) use:
```
sha256("entry:{entry_type}:{name}")
```

This resolves the collision problem: `GET /users` and `GET /posts` produce different hashes. The `name` component in entry-point identity prevents collision between same-type entry points.

Analyzers currently setting `stable_id` to bare HTTP methods (Go, JS/TS Express, NestJS, Python Django CBV) or entry types (WGSL) must be updated to use these formulas.

### 5. containing_module_stable_id: recursive resolution

The `containing_stable_id` component is computed recursively: a method's `stable_id` includes its class's `stable_id`, which includes its module's `stable_id`. Top-level functions (no containing module) use an empty string for this component.

**Fix Python first.** The current Python implementation (`py.py:836-877`) omits `containing_module_stable_id`. This must be corrected before the base-class design is finalized, because the base class should be modeled on a known-correct implementation.

**Hash stability impact:** Adding `containing_module_stable_id` to Python's formula changes all existing `stable_id` values. This requires bumping `STABLE_ID_SCHEME` from `hypergumbo-stableid-v1` to `hypergumbo-stableid-v2`.

### 6. Grammar version stability contract

**Pin build-from-source grammars to specific commits.** Replace `git clone --depth 1` in `scripts/build-source-grammars` with `git clone` + `git checkout <commit-sha>`. Track the commit SHA in a version file or as constants in the script. This is actionable immediately, independent of this ADR.

**Document the consumer contract:**
- `shape_id` is stable within a pinned grammar version
- Upgrading grammars may change hashes
- Consumers should treat `(shape_id_scheme, tool_version)` as the full identity context, not `shape_id` alone
- The scheme identifier bumps when the *algorithm* changes; grammar upgrades are tracked by tool version

**No cross-version stability tests.** Testing hash identity across grammar versions is impractical and not worth the maintenance burden. The scheme identifier is the correct mechanism for consumers.

## Implementation Order

**Phase 0 — Immediate fixes (independent, no design decisions):**
- Pin build-from-source grammars to specific commits
- Fix route/entry-point `stable_id` collisions (Go, JS/TS, WGSL, Python Django CBV)
- Fix Hack/Smithy `stable_id` field order inconsistency
- Add `containing_module_stable_id` to Python's `_compute_stable_id()`
- Bump `STABLE_ID_SCHEME` to `v2`

**Phase 1 — shape_id generalization:**
- Implement generic CST walker in `TreeSitterAnalyzer`
- Wire into all tree-sitter-based analyzers (default behavior, no per-analyzer code needed)
- Python retains `ast`-based override

**Phase 2 — stable_id untyped tier generalization:**
- Implement `ArityFlags` and `classify_parameter_flags()` hook
- Implement base-class `compute_stable_id()` with the full formula
- Add per-language parameter flag overrides where needed
- Retire location-based `stable_id` strings in the 27 analyzers that use them

**Phase 3 — stable_id typed tier (deferred):**
- Depends on ADR-0006 maturity (type extraction per language)
- Implement per-language `normalize_signature()` methods
- Upgrade analyzers that have sufficient type extraction

## Consequences

### Positive

1. **Spec compliance:** `stable_id` and `shape_id` become universal rather than Python-only
2. **Route identity correctness:** Eliminates the collision bug where same-method routes are indistinguishable
3. **Clone detection:** `shape_id` across all tree-sitter languages enables cross-language structural similarity
4. **Refactor tracking:** `stable_id` with `containing_module` correctly distinguishes methods with identical signatures in different classes
5. **Reproducibility:** Pinned grammars ensure consistent `shape_id` values across builds

### Negative

1. **Hash instability for Python consumers:** Adding `containing_module_stable_id` and fixing route collisions changes existing `stable_id` values. Mitigated by scheme version bump.
2. **Per-language maintenance:** `classify_parameter_flags()` overrides need updating when tree-sitter grammars change parameter node types. Estimated at ~30 lines per language.
3. **Location-based stable_id retirement:** 27 analyzers currently emit location-based `stable_id` values. Replacing them with signature hashes changes consumer behavior. Mitigated by the Phase 2 ordering — this happens after the base-class design is proven.

### Neutral

1. **Two shape_id implementations coexist:** Python uses `ast`, everything else uses tree-sitter. These produce different hashes for the same Python code. This is acceptable because `shape_id` is compared within-language, not cross-language.
2. **Untyped tier is the floor, not the ceiling:** Languages with rich type systems will eventually produce higher-quality `stable_id` values via the typed tier, but the untyped tier provides a useful baseline immediately.

## Relationship to Other ADRs

- **ADR-0006** (Variable Type Inference): Type extraction capabilities per language are a prerequisite for the typed `stable_id` tier (Phase 3). The untyped tier (Phases 1-2) is independent.
- **ADR-0012** (Pass Unification): If `stable_id`/`shape_id` computation moves to base-class methods, it integrates naturally with the unified pass interface. The identity computation happens within each analyzer's `run()` method, not as a separate pass.
- **ADR-0010** (Modular Packages): Per-language `classify_parameter_flags()` overrides live in the language packages, following the existing package boundary conventions.

## References

| What | Where |
|------|-------|
| Spec §6 identity field semantics | `docs/hypergumbo-spec.md:269-344` |
| Symbol class (`stable_id`, `shape_id` fields) | `ir.py:226-227` |
| Scheme version constants | `schema.py:72-73` |
| Python `_compute_stable_id()` | `py.py:836-877` |
| Python `_compute_shape_id()` / `_ast_structure()` | `py.py:880-910` |
| Python Django CBV override | `py.py:1441-1443` |
| `iter_tree()` utility | `base.py:200-222` |
| Go route `stable_id` (Gin/Echo) | `go.py:1186` |
| Go route `stable_id` (Gorilla) | `go.py:1231` |
| JS/TS Express route `stable_id` | `js_ts.py:1978` |
| JS/TS NestJS route `stable_id` | `js_ts.py:2211` |
| WGSL entry-point `stable_id` | `wgsl.py:232` |
| Hack reversed field order | `hack.py:89` |
| Smithy reversed field order | `smithy.py:62` |
| Build-from-source grammars | `scripts/build-source-grammars:105` |
| Lab notebook analysis | `~/hypergumbo_lab_notebook/notebookjournal_02202026_stable_shape_id_analysis.md` |
