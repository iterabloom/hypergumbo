# 6. Variable Type Inference for Method Call Resolution

Date: 2025-01-21
Updated: 2026-02-20
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
4. **Return type annotations**: `def get_db() -> Database` → return value has type `Database`

All four sources are now implemented in multiple analyzers (see table below).

## Decision

### Current Implementation

Nine analyzers implement variable type inference. The original six (Python, Java, Kotlin, TypeScript, C#, Dart) now support return type tracking and several support field tracking. Three additional analyzers (Go, Ruby, Lua) have been added since the original decision.

| Analyzer | Constructor | Parameter | Field | Return | Scope |
|----------|:-----------:|:---------:|:-----:|:------:|:------|
| Python   | ✅ | ✅ | ✅ (`__init__` field scanning, `py.py:1904-1942`) | ✅ (`py.py:118`, `1648-1661`) | Method-scoped (`py.py:1944-1960`) |
| Java     | ✅ | ✅ | ✅ (`field_declaration`, `java.py:1060-1070`) | ✅ (`java.py:231`, `1197-1210`) | File-scoped |
| Kotlin   | ✅ | ✅ | Partial (constructor params = fields, `kotlin.py:529-541`) | ✅ (`kotlin.py:158`, `702-720`) | File-scoped |
| TypeScript | ✅ | ✅ | ❌ | ✅ (`js_ts.py:526`, `2514-2528`) | File-scoped |
| C#       | ✅ | ✅ | ✅ (`field_declaration`, `csharp.py:865-889`) | ✅ (`csharp.py:302`, `327-362`) | File-scoped |
| Dart     | ✅ | ✅ | ❌ | ✅ (`dart.py:157`, `539-601`) | File-scoped |
| Go       | ✅ (short var, var spec, `go.py:593-635`) | ✅ (`go.py:638-671`) | ❌ | ❌ | File-scoped |
| Ruby     | ✅ (pattern-based: `.new`, `.find`, `.create`, `ruby.py:1973-2017`) | ❌ (no type annotations) | ❌ | ❌ | File-scoped |
| Lua      | ✅ (pattern-based: `MyClass:new()`, `lua.py:235-295`) | ❌ (no type annotations) | ❌ | ❌ | File-scoped |

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
3. ~~**Add return type tracking**~~ — Done for Python, Java, Kotlin, TypeScript, C#, Dart
4. **Consider method-scoped tracking for non-Python analyzers** (if false positives become a problem)
5. **Add Rust type tracking** (medium effort, good value for trait-based dispatch)
6. **Preserve generic type parameters** (needed for ADR-0014 typed `stable_id` tier; currently stripped)
7. **Extract visibility modifiers** (needed for ADR-0014 typed `stable_id` tier; not currently tracked)

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
