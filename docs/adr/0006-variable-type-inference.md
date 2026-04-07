<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 6. Variable Type Inference for Method Call Resolution

Date: 2025-01-21
Updated: 2026-04-07
Status: Accepted

## Context

When analyzing code like:

```python
def handler(db: Database):
    db.save(item)      # Which save() is this?
    db.commit()        # Which commit() is this?
```

The analyzer needs to determine that `db.save()` calls `Database.save`, not some other
`save` method. This requires **type inference** - tracking the type of the variable `db`.

### The Problem

Method calls on variables (`obj.method()`) are ambiguous without knowing `obj`'s type.
In a codebase with multiple classes that have `save()` methods, we need to know which
one is being called to produce accurate call graphs.

### Type Inference Sources

Variable types can be inferred from:

1. **Constructor assignments**: `db = Database()` → `db` has type `Database`
2. **Parameter annotations**: `def f(db: Database)` → `db` has type `Database`
3. **Field declarations**: `self.db: Database` → `self.db` has type `Database`
4. **Return type annotations** (local): `def get_db() -> Database` → return value of *this* function has type `Database`
5. **Return-type chaining** (global, INV-dihos): `qry := engine.NewInstantQuery(...)` → look up `QueryEngine.NewInstantQuery`'s declared return type in a *global registry* and assign that type to `qry`. This is the missing source surfaced by INV-dihos: source 4 only tracks the return type of the function being analyzed; source 5 tracks the return type of every other function so that *callers* can chain through it. Without source 5, every method call result gets `var_type = unknown` and the receiver chain breaks.

Sources 1–4 are implemented in multiple analyzers (see table below). Source 5 is the subject of the **Return-Type Registry Pre-Pass** section below — it requires a cross-cutting architectural addition.

## Decision

### Current Implementation

Nine analyzers implement variable type inference. The original six (Python, Java, Kotlin, TypeScript, C#, Dart) now support return type tracking and several support field tracking. Three additional analyzers (Go, Ruby, Lua) have been added since the original decision.

| Analyzer | Constructor | Parameter | Field | Return | Chained | Scope |
|----------|:-----------:|:---------:|:-----:|:------:|:-------:|:------|
| Python   | ✅ | ✅ | ✅ (`__init__` field scanning, `py.py:1904-1942`) | ✅ (`py.py:118`, `1648-1661`) | ❌ (Phase 4) | Method-scoped (`py.py:1944-1960`) |
| Java     | ✅ | ✅ | ✅ (`field_declaration`, `java.py:1060-1070`) | ✅ (`java.py:231`, `1197-1210`) | ❌ (Phase 1) | File-scoped |
| Kotlin   | ✅ | ✅ | Partial (constructor params = fields, `kotlin.py:529-541`) | ✅ (`kotlin.py:158`, `702-720`) | ❌ (Phase 2) | File-scoped |
| TypeScript | ✅ | ✅ | ❌ | ✅ (`js_ts.py:526`, `2514-2528`) | ❌ (n/a, no INV-dihos signal yet) | File-scoped |
| C#       | ✅ | ✅ | ✅ (`field_declaration`, `csharp.py:865-889`) | ✅ (`csharp.py:302`, `327-362`) | ❌ (Phase 2) | File-scoped |
| Dart     | ✅ | ✅ | ❌ | ✅ (`dart.py:157`, `539-601`) | ❌ (n/a, no INV-dihos signal yet) | File-scoped |
| Go       | ✅ (short var, var spec, `go.py:593-635`) | ✅ (`go.py:638-671`) | ❌ | ❌ | ❌ (Phase 1) | File-scoped |
| Ruby     | ✅ (pattern-based: `.new`, `.find`, `.create`, `ruby.py:1973-2017`) | ❌ (no type annotations) | ❌ | ❌ | n/a (no annotations to register) | File-scoped |
| Lua      | ✅ (pattern-based: `MyClass:new()`, `lua.py:235-295`) | ❌ (no type annotations) | ❌ | ❌ | n/a (no annotations to register) | File-scoped |

The **Chained** column tracks INV-dihos source 5: "this analyzer's `var_types` consults a global function-signature registry when assigning the result of a method call." A `❌` means the analyzer name-guesses the var type from the method name (or gives up); a `(Phase N)` annotation places the analyzer in the rollout plan in §Future Work. Languages with no return-type annotations at all (Ruby, Lua) are marked `n/a` because there is no source data to register; the same is true for fully-dynamic Python without PEP 484 annotations, but type-annotated Python is tracked by Phase 4.

All use the same core pattern:

```python
# Data structure
var_types: dict[str, str] = {}  # variable_name -> class_name

# Populated from constructors
if is_constructor_call(node):
    var_types[var_name] = class_name

# Populated from parameters
for param_name, param_type in extract_param_types(func_node):
    var_types[param_name] = param_type

# Used for method resolution
if receiver_name in var_types:
    class_name = var_types[receiver_name]
    target = f"{class_name}.{method_name}"
```

### Scope Handling

Most analyzers use **file-scoped** tracking — a single `var_types` dict per file:

```python
def method_a():
    client = Client()     # var_types["client"] = "Client"
    client.send()         # ✓ Correctly resolved

def method_b(other: str):
    client.receive()      # ⚠️ Also resolved to Client.receive (might be wrong!)
```

This can cause false positives when different methods use the same variable name for
different types. However:

- Variable name collisions across methods are rare in practice
- False positives (extra edges) are preferable to false negatives (missing edges)

**Exception: Python is method-scoped.** Each function gets a fresh `param_types` dict
(seeded from parameter annotations + class field types) passed to `process_code_block()`.
The dict does not leak across function boundaries (`py.py:1944-1960`).

### Return-Type Registry Pre-Pass (INV-dihos)

The variable-type inference described above tracks four sources, but it
treats every method call result as opaque: when an analyzer sees `qry :=
engine.NewInstantQuery(...)` it cannot ask "what does `NewInstantQuery`
return?" because each analyzer only knows about the function it is
*currently* analyzing. The receiver chain breaks at the first
intermediate method call, even when that method has a perfectly clean
return type annotation in the source.

INV-dihos surfaced this on prometheus: the slice from
`cmd/promtool/main.go:queryRange` failed to walk into PromQL's
`Engine.exec` → `evalSampleVector` chain because every `qry := ng.NewInstantQuery(...)`
or `res := qry.Exec(...)` call left `var_types["qry"]` and
`var_types["res"]` unset. A scan of all eight statically-typed
analyzers (Java, Go, Kotlin, C#, Rust, C++, Scala, Swift) confirmed
that **none of them** consult one another's return type annotations —
the gap is structural, not Go-specific.

#### Architecture

The fix is a **cross-cutting pre-pass** that builds a global
function-signature registry before any analyzer runs its var_types
inference, then exposes the registry to every analyzer's existing
inference loop. Conceptually:

```
                              ┌──────────────────────────────────┐
                              │  signatures.py (new)             │
                              │                                  │
        AST trees ─────────►  │  build_signature_registry(trees) │ ─────► function_signatures: dict
                              │    (per-language extractors)     │           {symbol_id: ReturnType}
                              └──────────────────────────────────┘
                                                                              │
                                                                              ▼
        ┌─────────────────────────────────────────────────────────────────────┐
        │  Each analyzer's _extract_edges:                                    │
        │    var_types = {}  # constructor / param / field / return rules    │
        │    for call_expression where lhs := receiver.method(...):          │
        │       if (receiver_type := var_types.get(receiver)):                │
        │          if rt := function_signatures.get(f"{receiver_type}.{m}"): │
        │             var_types[lhs] = rt   # ← THE NEW INFERENCE PATH       │
        └─────────────────────────────────────────────────────────────────────┘
```

The registry is built once per `AnalysisRun`, attached to the run
context, and consumed by every analyzer that opts in. It is **purely
additive**: existing var_types rules continue to fire first, and the
registry only resolves the case "receiver type is known but the
chained call's return type was previously unknown."

#### Two-tier YAML + Python design

Following the precedent set by `dataflow.yaml` + `dataflow.py`, signature
extraction is split into a declarative tier and an escape-hatch tier:

**Tier 1 — YAML positional rules** (~10–30 lines per language). Each
language analyzer ships a `signatures` section in its existing
`<lang>.yaml` config:

```yaml
# go.yaml
signatures:
  function_node_types:
    - function_declaration
    - method_declaration
  receiver_field: receiver         # for method_declaration only
  name_field: name
  result_field: result             # tree-sitter-go's "result" child
  generic_strip_pattern: '<.*>'    # strip Foo[T] → Foo

# java.yaml
signatures:
  function_node_types:
    - method_declaration
    - constructor_declaration
  receiver_field: null             # methods belong to enclosing class_declaration
  name_field: name
  return_type_field: type          # tree-sitter-java's "type" child
  generic_strip_pattern: '<.*>'
```

This declarative tier is sufficient for the ~70% of method declarations
that have a single, named return type expressed in a syntactically
canonical position.

**Tier 2 — Python escape hatches** (~30–50 lines per language) live
beside the existing `signatures.py` module and handle the structural
edge cases that YAML can't express:

| Language | Edge case | Example |
|----------|-----------|---------|
| Go       | Tuple return — pick the non-error result | `func (qe *QueryEngine) NewInstantQuery(...) (promql.Query, error)` |
| Go       | Cross-package qualifier — must resolve to bare type | `promql.Query` → register as `Query` (matches symbol storage) |
| Go       | Receiver binding — `(qe *QueryEngine) M()` registers as `QueryEngine.M`, not bare `M` | INV-dihos's exact failure mode |
| Rust     | `Result<T, E>` / `Option<T>` unwrapping | `fn parse() -> Result<Query, Error>` → `Query` |
| Rust     | `impl Trait` opacity | `fn new() -> impl Iterator<Item=Foo>` → leave unregistered |
| Java     | Generic erasure already covered by `generic_strip_pattern` | `List<Item>` → `List` |
| Kotlin   | Nullable suffix stripping | `Foo?` → `Foo` |
| C#      | `async Task<T>` / `ValueTask<T>` unwrapping | `Task<Query>` → `Query` |
| C++      | Pointer/reference stripping | `Query*` → `Query`, `Query&` → `Query` |
| C++      | Template specialization handling | `vector<Query>` → `vector` (consistent with §Limitations) |

Edge cases that cannot be unambiguously resolved leave the entry
unregistered, falling back to the existing name-guessing behavior. This
is the same fail-open posture used by `annotate_dataflow`'s YAML
fallback (see WI-hivud, INV-halar).

#### Receiver binding rule

The single most important Tier 2 rule is **receiver binding**: a method
declaration's signature must register under
`{ReceiverType}.{MethodName}`, not bare `{MethodName}`. Otherwise the
registry collapses two methods named `Get` from unrelated types into a
single ambiguous entry. Concretely:

```go
// go.py signature extraction (pseudocode)
func register(decl):
    name = decl.name
    if decl.receiver is not None:
        recv_type = strip_pointer_and_package(decl.receiver.type)  # *promql.Engine → Engine
        key = f"{recv_type}.{name}"
    else:
        key = name  # free function
    function_signatures[key] = strip_error_tuple(decl.result_type)
```

The Tier 1 YAML supplies `receiver_field: receiver`; the Tier 2 Python
applies pointer/package stripping. Same architectural split as
dataflow.

#### Cross-linker check

The registry is consumed by analyzers (var_types fill phase), not by
linkers — but two existing linkers carry related state:

- **type_hierarchy.py** builds `methods_by_class` to support
  `dispatches_to` edges. This index is *post-analysis* (it consumes
  symbols, not AST), so it cannot itself contribute to the signature
  registry. It is, however, the canonical source of truth for "which
  type does this method belong to" — when a method-receiver type is
  ambiguous in the registry, we can fall back to type_hierarchy's
  receiver→class index. Listed as a Phase 5 follow-on.
- **annotate_dataflow** does *not* read return types today; it only
  applies positional rules from `assignments`/`returns`. Once the
  registry exists, dataflow.py could optionally consult it to refine
  `access_mode` annotations on chained method calls (e.g.,
  `cache.Get(k).Set(v)` → if `Get` returns a `*Entry` and `Set` is
  defined on `Entry`, the second call's receiver type is known). This
  is Phase 5 cross-linker work, not part of the initial rollout.

#### Phase rollout

The cross-language nature of the gap means a per-language fix is
shipped one phase at a time, with bakeoff validation between phases:

| Phase | Languages | Why now | Bakeoff signal |
|-------|-----------|---------|----------------|
| **1** | Java + Go | Highest-value statically-typed languages with the most repos in the deep-bakeoff pool. Both already track receiver types in `var_types`. INV-dihos was discovered on Go (prometheus); Java has a parallel gap on Spring repos. | INV-dihos directly. |
| **2** | Kotlin + C# | Existing `var_types` infrastructure with parameter and return tracking. Smallest delta after Phase 1 since the Tier 2 escape hatches (nullable, async unwrapping) are well-understood. | Spotted on kserve (Kotlin) and aspnet (C#) deep-bakeoff repos. |
| **3** | Rust + C++ + Scala | No existing `var_types` infrastructure to extend. Each requires a parameter/constructor extractor first (see §Analyzers Without Type Tracking) before signature registration is meaningful. Largest delta. | Surfaced by alertmanager Rust workspace and any LLVM-style C++ repo. |
| **4** | Type-annotated Python | PEP 484 annotations are well-defined; `py.py` already extracts return type annotations for the *current* function (`py.py:1648`). Promote to global registry. Untyped Python remains a `n/a`. | Spotted on kserve (mixed-typed Python). |
| **5** *(follow-on)* | Cross-linker integration | type_hierarchy.py fallback for ambiguous receivers; dataflow.py refinement for chained method-call `access_mode`. | Optional, only after Phases 1–4 are validated. |

Each phase ships as its own PR, adds regression tests against the
specific repo that surfaced the gap, and updates this ADR's
implementation table to flip the Chained column for the affected
analyzer(s).

### Analyzers Without Type Tracking

| Analyzer | Should Add? | Complexity | Value | Notes |
|----------|-------------|------------|-------|-------|
| **Rust** | ⚠️ Maybe | Medium | Medium | Trait-based; `impl Trait` return types are opaque. Valuable for concrete type calls. |
| **C/C++** | ⚠️ Maybe | High | Low-Medium | Pointers vs references vs values; virtual methods; templates; C has no methods |
| **Swift** | ⚠️ Maybe | Medium | Medium | Protocol-based, similar to Go interfaces |
| **PHP** | ⚠️ Maybe | Low-Medium | Medium | Has type hints since PHP 7; straightforward extraction |
| **Scala** | ⚠️ Maybe | Medium | Medium | Rich type system with implicits |
| **OCaml** | ❌ No | N/A | Low | Functional paradigm, pattern matching not methods |

**Note:** Go was previously listed here as "Maybe" but now has full type inference (short var declarations, var specs, function parameters). Ruby and Lua were added with constructor-based pattern matching (no type annotations available in those languages).

## Consequences

### Benefits

1. **More complete call graphs**: Method calls on typed parameters now produce edges
2. **Better for DI patterns**: FastAPI, Spring, Dagger, ASP.NET Core all use typed parameters
3. **Improved slice accuracy**: Transitive dependencies through typed parameters are now tracked

### Limitations

1. **Mostly file-scoped**: Python is method-scoped; all others use a single file-level dict (potential for false positives)
2. **Only first-party types**: External library types (Pydantic, SQLAlchemy) won't resolve
3. **No inheritance awareness**: `SubClass` parameters won't resolve `ParentClass.method()`
4. **Generics stripped**: `List<T>` → `List` (adequate for method resolution but insufficient for typed `stable_id`; see ADR-0014)

### Future Work

1. ~~**Consider Go/Rust support**~~ — Go done; Rust remains a candidate
2. ~~**Add field type tracking**~~ — Done for Python, Java, C#, partial for Kotlin
3. ~~**Add return type tracking**~~ — Done for Python, Java, Kotlin, TypeScript, C#, Dart (local — return type of *this* function)
4. **Return-Type Registry Pre-Pass — Phase 1: Java + Go** (INV-dihos): build the global signature registry and wire it into both analyzers' `var_types` resolution loop. Add `signatures` YAML sections to `java.yaml` and `go.yaml`, plus Tier 2 Python escape hatches in a new `signatures.py` module for tuple/error unwrapping (Go) and constructor handling (Java). Validation: forward slice from `cmd/promtool/main.go:queryRange` should reach `promql.Engine.exec`; bakeoff repo: prometheus.
5. **Return-Type Registry Pre-Pass — Phase 2: Kotlin + C#**: extend the registry to two more analyzers with existing var_types infrastructure. Tier 2 escape hatches: Kotlin nullable suffix (`Foo?` → `Foo`); C# `Task<T>` / `ValueTask<T>` async unwrapping. Bakeoff repos: kserve (Kotlin), aspnet (C#).
6. **Return-Type Registry Pre-Pass — Phase 3: Rust + C++ + Scala**: requires bootstrapping `var_types` infrastructure for these analyzers first (see §Analyzers Without Type Tracking). Tier 2 escape hatches: Rust `Result<T, E>` / `Option<T>` unwrapping and `impl Trait` opacity; C++ pointer/reference stripping; Scala implicits left as future work.
7. **Return-Type Registry Pre-Pass — Phase 4: Type-annotated Python**: PEP 484 makes this tractable for code that uses type hints. Untyped Python remains `n/a`. The existing local return-type extraction in `py.py:1648` is promoted to a global registry entry.
8. **Return-Type Registry Pre-Pass — Phase 5 (follow-on): cross-linker integration**: type_hierarchy.py fallback for ambiguous receiver types in the registry; dataflow.py refinement for chained method-call `access_mode` annotations. Optional, only after Phases 1–4 are validated against deep-bakeoff metrics.
9. **Consider method-scoped tracking for non-Python analyzers** (if false positives become a problem)
10. **Add Rust type tracking** (medium effort, good value for trait-based dispatch) — superseded by Phase 3 above; this entry is kept for the constructor/parameter portion which is a prerequisite for Phase 3.
11. **Preserve generic type parameters** (needed for ADR-0014 typed `stable_id` tier; currently stripped)
12. **Extract visibility modifiers** (needed for ADR-0014 typed `stable_id` tier; not currently tracked)

## Implementation Pattern

For adding type tracking to a new analyzer:

```python
def _extract_param_types(node, source: bytes) -> dict[str, str]:
    """Extract parameter name -> type mapping from function declaration."""
    param_types = {}

    # Language-specific: find parameters node
    params_node = find_parameters(node)

    for param in params_node.children:
        # Language-specific: extract name and type
        param_name = extract_param_name(param, source)
        param_type = extract_param_type(param, source)

        if param_name and param_type:
            # Strip generics: List<T> -> List
            if "<" in param_type:
                param_type = param_type.split("<")[0]
            param_types[param_name] = param_type

    return param_types

def _extract_edges(...):
    var_types: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Track from function declarations
        if node.type in FUNCTION_TYPES:
            param_types = _extract_param_types(node, source)
            var_types.update(param_types)

        # Track from constructor calls
        elif node.type == "new_expression":
            # ... existing constructor tracking ...

        # Use for method resolution
        elif node.type == "call_expression":
            if receiver in var_types:
                class_name = var_types[receiver]
                # ... resolve to class_name.method ...
```

## References

- PR #485: Python class method calls and parameter type inference
- PR #486: Java, Kotlin, TypeScript parameter type inference
- PR #488: C# and Dart parameter type inference
- gRPC stub pattern: The original motivation for constructor-based tracking
- ADR-0014: Generalized Symbol Identity — typed `stable_id` tier depends on this infrastructure
- ADR-0015: Dataflow access modes — same two-tier YAML+Python design pattern (`dataflow.yaml` + `dataflow.py`) is the precedent for the proposed `signatures.yaml` + `signatures.py` split.
- INV-dihos: original tracker item — "Go receiver-type inference must follow method-call return types". Scope expanded to all eight statically-typed analyzers in the agent reply 2026-04-07.
- WI-hukoh: forward-dataflow option (2)/(3) decision — both items concern "edge metadata at link time vs node-state at BFS time" tradeoffs and share the same dest-type-resolution machinery.
