# 7. Import Tracking for Cross-File Call Resolution

Date: 2026-01-22
Status: Proposed

## Context

### The Resolution Framework Investment

In January 2026, we invested significant effort to create and deploy a shared symbol resolution framework:

| Commit | Change |
|--------|--------|
| `afc727a` | Created shared `SymbolResolver` for Python cross-file resolution |
| `937594e` | Extended to support all registry formats: `NameResolver`, `ListNameResolver` |
| `291c9c3` | Migrated ALL 44 analyzers to shared `NameResolver` framework |
| `d8f936d` | Migrated remaining analyzers (cobol, fish, gdscript, glsl, wgsl) |

The framework explicitly supports **path hints** for disambiguation:

```python
# From symbol_resolution.py docstring:
# Go: list-valued registry with disambiguation
resolver = ListNameResolver(global_symbols)
result = resolver.lookup("Register", path_hint="grpc")
```

### The Problem

Despite this investment, **only 5 of 67 analyzers track imports for call resolution**:

| Analyzer | Tracks Imports | Uses for Disambiguation | Status |
|----------|:--------------:|:-----------------------:|--------|
| Python (`py.py`) | YES | YES | Fixed in `dfc59db` (PR #503) |
| Go (`go.py`) | YES | YES | Correct |
| JavaScript/TypeScript (`js_ts.py`) | YES | **NO** | Bug |
| Java (`java.py`) | YES | Partial | Different pattern |
| Kotlin (`kotlin.py`) | YES | **NO** | Dead code |

The other **62 analyzers**:
- May emit import **edges** ("File A imports Module B")
- But do **NOT track** imports for call resolution disambiguation
- Use global name lookup without `path_hint`

### Concrete Example: The Python Bug We Just Fixed

PR #503 fixed a bug where `from app import crud` followed by `crud.create_user()` emitted unresolved edges. The fix added "Case 2e" to try submodule lookup:

```python
# Before: Only tried symbol lookup (failed for submodules)
# After: Try symbol lookup, then submodule lookup as fallback
if not callee_symbol:
    submodule_name = f"{module_name}.{original_name}"
    callee_symbol = _lookup_symbol_by_module(
        global_symbols, submodule_name, attr_name, resolver=resolver
    )
```

### Broader Implications

Investigating this bug revealed that JavaScript/TypeScript has the **exact same gap**:

```python
# js_ts.py line 1984-1987
elif obj_name and obj_name in namespace_imports:
    # Has module path in namespace_imports[obj_name] but doesn't use it!
    lookup_result = resolver.lookup(method_name)  # No path_hint!
```

And Kotlin extracts imports but **never uses them**:

```python
# kotlin.py - imports parameter is passed but never referenced
def _extract_edges_from_file(..., imports: dict[str, str], ...):
    # imports is NEVER USED in the function body
```

### Relationship to ADR-0006

ADR-0006 addressed **variable type inference** for method resolution:

```python
# Problem: obj.method() - which class is obj?
db = Database()     # Track: db has type Database
db.save(item)       # Resolve: Database.save()
```

This ADR addresses **import tracking** for module-qualified calls:

```python
# Problem: module.func() - which module?
from app import crud  # Track: crud refers to app.crud
crud.create_user()    # Resolve: app.crud.create_user()
```

These are complementary:
- ADR-0006: Resolves `instance.method()` via type inference
- This ADR: Resolves `module.func()` via import tracking

### Languages That Should Track Imports

Any language with a meaningful import/module system benefits from import tracking:

| Language | Import Syntax | Import Type | Priority |
|----------|---------------|-------------|:--------:|
| TypeScript/JS | `import * as foo from 'bar'` | Namespace | **High** |
| Kotlin | `import com.example.Class` | Class/function | **High** |
| Rust | `use crate::module::func` | Path | High |
| C# | `using Namespace` | Namespace | High |
| Ruby | `require 'foo'` | File | Medium |
| Elixir | `alias MyApp.Services` | Module | Medium |
| Swift | `import Module` | Module | Medium |
| PHP | `use Namespace\Class` | Namespace/Class | Medium |
| Scala | `import pkg.Class` | Package/Class | Medium |
| Dart | `import 'package:foo'` | Package | Medium |

## Decision

### Phase 1: Fix Immediate Bugs (High Priority)

1. **JavaScript/TypeScript**: Pass `namespace_imports[obj_name]` as `path_hint` to resolver
2. **Kotlin**: Use the extracted `imports` dict for call resolution (currently dead code)

### Phase 2: Systematic Improvement (Medium Priority)

For each analyzer in the priority list above:

1. **Extract imports**: Parse import statements and build `alias → module_path` mapping
2. **Track imports**: Store mapping in `FileAnalysis` or equivalent
3. **Use for resolution**: Pass `path_hint` when calling `resolver.lookup()`

### Implementation Pattern

Based on Go's correct implementation (`go.py:468-494`):

```python
@dataclass
class FileAnalysis:
    # ... existing fields ...
    import_aliases: dict[str, str] = field(default_factory=dict)  # alias → import_path

def _extract_import_aliases(tree, source) -> dict[str, str]:
    """Extract import alias → module path mapping."""
    aliases = {}
    for node in iter_tree(tree.root_node):
        if node.type == "import_statement":  # Language-specific
            alias, module_path = parse_import(node, source)
            if alias and module_path:
                aliases[alias] = module_path
    return aliases

def _extract_edges(..., import_aliases: dict[str, str], resolver, ...):
    for node in iter_tree(tree.root_node):
        if is_qualified_call(node):  # e.g., foo.bar()
            receiver, method = parse_call(node, source)

            # Get path hint from imports
            path_hint = import_aliases.get(receiver)

            # Use hint for disambiguation
            lookup_result = resolver.lookup(method, path_hint=path_hint)
```

### Non-Goals

- **Full semantic import resolution**: We don't need to resolve all import semantics (re-exports, star imports, etc.). Path hints provide disambiguation, not completeness.
- **Languages without meaningful imports**: C (`#include` is textual), assembly, etc.
- **Dynamic imports**: `importlib.import_module()`, `require()` with variables

## Consequences

### Positive

1. **Better disambiguation**: When `foo()` exists in multiple modules, import context selects the correct one
2. **Higher confidence scores**: Exact matches via `path_hint` get higher confidence than suffix matching
3. **Fewer false edges**: Without import tracking, we may emit edges to wrong targets
4. **Leverages existing investment**: The `NameResolver.lookup(path_hint=...)` API already exists

### Negative

1. **Per-analyzer work**: Each language has different import syntax; no one-size-fits-all
2. **Increased complexity**: Analyzers must maintain import state during edge extraction
3. **Incomplete coverage**: Some imports can't be statically resolved (dynamic, computed)

### Neutral

1. **No schema changes**: Import tracking is internal; output format unchanged
2. **Backward compatible**: Analyzers without import tracking continue to work (just less precise)

## Implementation Checklist

### Phase 1: Bug Fixes

- [x] **js_ts.py**: Add `path_hint=namespace_imports.get(obj_name)` to resolver calls
- [x] **kotlin.py**: Use `imports` dict in `_extract_edges_from_file()`

### Phase 2: High-Priority Languages

- [x] **rust.py**: Track `use` statements, pass as path hints
- [x] **csharp.py**: Track `using` directives
- [x] **ruby.py**: Track `require`/`require_relative` for disambiguation
- [x] **elixir.py**: Track `alias`/`import`/`use` directives
- [x] **swift.py**: Track `import` statements
- [x] **php.py**: Track `use` statements for namespace disambiguation
- [x] **scala.py**: Track `import` statements
- [x] **dart.py**: Track `import` statements with `show`/`hide`

### Phase 3: Remaining Analyzers (Triaged)

After careful analysis of each language's import semantics, the 37 remaining analyzers
fall into two groups: those where import tracking would improve resolution (static
import semantics with aliasing) and those where it wouldn't help (dynamic or textual).

**Phase 3A: Import tracking MATTERS (static semantics, qualified/aliased imports)**

*High priority - aliased/qualified imports are idiomatic:*

| Analyzer | Import Syntax | Why It Matters | Status |
|----------|---------------|----------------|--------|
| ada.py | `with`, `use`, renames | Package renames: `package TIO renames Ada.Text_IO;` | ✅ Done |
| agda.py | `import`, `open` | Qualified names in dependent type proofs | |
| clojure.py | `require :as` | `(require '[clojure.string :as str])` then `(str/join ...)` | ✅ Done |
| d_lang.py | `import x = y` | `import io = std.stdio;` then `io.writeln()` | ✅ Done |
| elm.py | `import as` | `import Dict as D` then `D.get` | ✅ Done |
| fsharp.py | `module` abbreviations | ML family; `module M = List` then `M.map` | ✅ Done |
| groovy.py | `import` | Same as Java | ✅ Done |
| haskell.py | `import qualified as` | `import qualified Data.Map as M` then `M.lookup` | ✅ Done |
| lean.py | `import` | Qualified names in theorem prover | |
| nim.py | `import as` | `import strutils as su` then `su.strip()` | ✅ Done |
| ocaml.py | `module` aliases | ML family; `module L = List` then `L.map` | ✅ Done |
| solidity.py | `import {X as Y}` | `import {IERC20 as Token} from "..."` | |
| starlark.py | `load()` | `load("//foo:bar.bzl", my_rule="rule")` explicit bindings | ✅ Done |

*Medium priority - qualified imports exist but less central:*

| Analyzer | Import Syntax | Why It Matters | Status |
|----------|---------------|----------------|--------|
| cpp.py | `namespace x = y` | `namespace fs = std::filesystem;` then `fs::exists()` | |
| erlang.py | `-import`, `mod:func` | Module-qualified calls `lists:map()` | |
| fortran.py | `use, only:` | `use linear_algebra, only: solve` | |
| julia.py | `import as` | `import Pkg as P` then `P.add()` | ✅ Done |
| r_lang.py | `pkg::func` | Qualified calls `dplyr::filter()` | |
| vhdl.py | `library`, `use` | `use ieee.std_logic_1164.all;` (Ada-like package system) | |
| zig.py | `@import` | `const std = @import("std");` then `std.debug.print()` | |

**Phase 3B: Import tracking DOESN'T MATTER (dynamic or textual)**

*Dynamic languages - imports return arbitrary runtime values:*

| Analyzer | Import Syntax | Why It Doesn't Help |
|----------|---------------|---------------------|
| commonlisp.py | `use-package` | Imports all symbols; no aliasing |
| gdscript.py | `preload`, `load` | Returns scene/script objects dynamically |
| lua.py | `require` | Returns arbitrary table; can't statically trace |
| nix.py | `import` | Functional but returns arbitrary value |
| perl.py | `use`, `require` | Very dynamic, duck-typed |
| powershell.py | `Import-Module` | Dynamic, duck-typed |
| wolfram.py | `Needs`, `Get` | Dynamic evaluation |

*Textual inclusion - preprocessor pastes text, no namespacing:*

| Analyzer | Import Syntax | Why It Doesn't Help |
|----------|---------------|---------------------|
| c.py | `#include` | Textual paste, no namespaces |
| cobol.py | `COPY` | Textual copybook inclusion |
| cuda.py | `#include` | Same as C |
| objc.py | `#import` | Textual with include guards |

*Shell - dynamic sourcing:*

| Analyzer | Import Syntax | Why It Doesn't Help |
|----------|---------------|---------------------|
| bash.py | `source`, `.` | Executes in current shell; no namespacing |
| fish.py | `source` | Same as bash |

*Shaders - textual or none:*

| Analyzer | Import Syntax | Why It Doesn't Help |
|----------|---------------|---------------------|
| glsl.py | `#include` | Non-standard extension; textual |
| hlsl.py | `#include` | Preprocessor; textual |
| verilog.py | `` `include`` | Preprocessor; textual |
| wgsl.py | None | No import system at all |

**Category C: Config/data formats (N/A - no runtime imports):**

| Analyzer | Notes |
|----------|-------|
| json_config.py | Data format |
| toml_config.py | Data format |
| xml_config.py | Data format |
| yaml_ansible.py | Config/playbooks |
| graphql.py | Schema definition |
| proto.py | Schema definition |
| thrift.py | IDL |
| capnp.py | IDL |
| hcl.py | Config (Terraform) |
| dockerfile.py | Build instructions |
| cmake.py | Build system |
| make.py | Build system |
| css.py | Stylesheets |
| html.py | Markup |
| latex.py | Document markup |
| sql.py | Query language |
| llvm_ir.py | Compiler IR |

**Summary:**
- **Phase 1**: 2 analyzers (JS/TS, Kotlin bug fixes)
- **Phase 2**: 8 analyzers (Rust, C#, Ruby, Elixir, Swift, PHP, Scala, Dart)
- **Phase 3A High**: 13 analyzers (aliased imports idiomatic)
- **Phase 3A Medium**: 7 analyzers (qualified imports exist)
- **Phase 3B**: 17 analyzers (dynamic/textual - no benefit)
- **Category C**: 17 analyzers (config/data - N/A)
- **Already correct**: 2 analyzers (Python, Go)
- **Already partial**: 1 analyzer (Java)

**Total**: 67 analyzers accounted for.

**Actionable work**: Phase 1 (2) + Phase 2 (8) + Phase 3A (20) = **30 analyzers** could benefit.
**No action needed**: Phase 3B (17) + Category C (17) + Already done (3) = **37 analyzers**.

### Verification

For each analyzer:
1. Create test case with two modules defining same function name
2. Import one, call through import alias
3. Verify correct target is resolved (not ambiguous or wrong)

## References

- PR #503: Python submodule import fix (the bug that revealed this gap)
- ADR-0006: Variable type inference for method call resolution
- `symbol_resolution.py`: The shared resolution framework
- Commits: `afc727a`, `937594e`, `291c9c3`, `d8f936d` (resolution framework rollout)
- Spec section 9.6: Known Analysis Limitations (re-export resolution)
