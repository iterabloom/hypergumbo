# 6. Variable Type Inference for Method Call Resolution

Date: 2025-01-21
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

Currently, hypergumbo implements #1 (constructor) and #2 (parameters). Sources #3 and #4
are not yet implemented.

## Decision

### Current Implementation

Six analyzers implement variable type inference:

| Analyzer | Constructor Tracking | Parameter Tracking | Field Tracking | Return Tracking |
|----------|---------------------|-------------------|----------------|-----------------|
| Python   | ✅ | ✅ | ❌ | ❌ |
| Java     | ✅ | ✅ | ❌ | ❌ |
| Kotlin   | ✅ | ✅ | ❌ | ❌ |
| TypeScript | ✅ | ✅ | ❌ | ❌ |
| C#       | ✅ | ✅ | ❌ | ❌ |
| Dart     | ✅ | ✅ | ❌ | ❌ |

All four use the same pattern:

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

The current implementation is **file-scoped**, not method-scoped:

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
- Method-scoped tracking would require significant refactoring of the AST traversal

### Analyzers Without Type Tracking

Four analyzers do not currently implement type tracking:

| Analyzer | Should Add? | Complexity | Value | Notes |
|----------|-------------|------------|-------|-------|
| **Go** | ⚠️ Maybe | Medium | Medium | Interface-based, implicit satisfaction |
| **Rust** | ⚠️ Maybe | Medium | Medium | Trait-based, similar to Go |
| **C/C++** | ⚠️ Maybe | High | Low-Medium | Pointers complicate things; C has no methods |
| **OCaml** | ❌ No | N/A | Low | Functional paradigm, pattern matching not methods |

**Note:** C# and Dart were added in PR #488, following the same pattern as Java/Kotlin.

#### Why Go and Rust are more complex

**Go:**
- Interfaces are satisfied implicitly (no `implements` keyword)
- A `Database` interface could have multiple implementations
- Would need interface → implementation mapping
- Still valuable for struct method calls

**Rust:**
- Trait bounds add complexity
- `impl Trait` return types are opaque
- Ownership/borrowing affects method resolution
- Still valuable for concrete type calls

#### Why C++ is complex

- Pointers vs references vs values: `Client*`, `Client&`, `Client`
- Virtual methods and inheritance hierarchies
- Templates add another dimension
- C code (without methods) mixed with C++ code

#### Why OCaml is not applicable

- Functional paradigm with pattern matching
- No `obj.method()` patterns to resolve
- Type inference is the compiler's job, not the analyzer's

## Consequences

### Benefits

1. **More complete call graphs**: Method calls on typed parameters now produce edges
2. **Better for DI patterns**: FastAPI, Spring, Dagger, ASP.NET Core all use typed parameters
3. **Improved slice accuracy**: Transitive dependencies through typed parameters are now tracked

### Limitations

1. **File-scoped, not method-scoped**: Potential for false positives
2. **Only first-party types**: External library types (Pydantic, SQLAlchemy) won't resolve
3. **No inheritance awareness**: `SubClass` parameters won't resolve `ParentClass.method()`

### Future Work

1. **Consider Go/Rust support** (medium effort, good value)
2. **Add field type tracking** (`self.db: Database`)
3. **Add return type tracking** (`def get_db() -> Database`)
4. **Consider method-scoped tracking** (if false positives become a problem)

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
