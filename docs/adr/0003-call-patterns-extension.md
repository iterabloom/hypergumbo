<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0003 Extension: Call-Based Framework Patterns

## Status
Superseded by [0003-usage-context-patterns.md](0003-usage-context-patterns.md)

## Supersession Note

This document proposed extending the YAML pattern system to support call-based patterns (Django `path()`, Express `app.get()`). Through further analysis, we discovered that call-based patterns are just one case of a more general concept: **usage context patterns**.

The unified model in `0003-usage-context-patterns.md` extends this proposal to cover:
- Call-based patterns (Django, Express, Go frameworks)
- Data-driven patterns (Clojure Reitit, Hapi config objects)
- File-based patterns (Next.js, Nuxt)
- Block/DSL-based patterns (Ruby Sinatra, Elixir Phoenix)

The core insight remains the same: a symbol's semantics can be derived from how it's used, not just how it's defined. The unified model provides a single abstraction (`UsageContext`) that handles all these cases.

---

## Original Proposal (Preserved for Reference)

## Context

ADR-0003 established that analyzers should be "pure" (no framework knowledge) and framework semantics should be captured in YAML pattern files. This works well for **decorator-based** frameworks:

```python
# FastAPI: decorator on function → YAML patterns can match
@app.get("/users")
def list_users(): ...
```

However, many frameworks use **call-based** routing where route information is in function calls, not decorators:

```python
# Django: call references handler → current YAML patterns cannot match
path("/users/", views.list_users)
```

```javascript
// Express: call registers handler → current YAML patterns cannot match
app.get("/users", listUsers);
```

The current YAML pattern system only matches against symbol metadata (decorators, base_classes, annotations). It cannot match based on how a symbol is *used* (referenced in calls).

This limitation forces framework-specific logic into analyzers, violating ADR-0003's "pure analyzer" principle. Currently, Django URL detection in `py.py` and Express route detection in `js_ts.py` are hardcoded as special cases.

## Decision

Extend the YAML pattern system to support **call-based patterns** that enrich symbols based on their usage context.

### Core Insight

A symbol's semantics can be derived from:
1. **Its definition** (decorators, base classes) — current system
2. **Its usage context** (how it's referenced in calls) — proposed extension

For Django, the `list_users` function is a route handler not because of its definition, but because it's *passed to* `path()`.

### New IR Type: CallSite

Add a new IR type to capture significant function calls:

```python
@dataclass
class CallArg:
    """An argument in a call site."""
    value: Any  # Literal value or string representation
    symbol_ref: str | None = None  # Symbol ID if arg is a symbol reference
    span: Span | None = None  # Source location of argument

@dataclass
class CallSite:
    """A function call site with argument information.

    Captures calls that might be significant for framework pattern matching.
    """
    id: str
    func_name: str  # Resolved function name (e.g., "path", "app.get")
    path: str  # File path
    span: Span
    args: list[CallArg]  # Positional arguments
    kwargs: dict[str, CallArg]  # Keyword arguments
    caller_id: str | None = None  # Enclosing symbol ID
```

### Extended YAML Pattern Syntax

```yaml
# django.yaml
patterns:
  # Existing: match decorator on symbol (definition-based)
  - concept: route
    decorator: "^api_view$"
    extract_method: "kwargs.methods"

  # NEW: match symbols referenced in specific calls (usage-based)
  - concept: route
    referenced_in_call: "^(path|re_path|url)$"
    arg_position: 1  # Handler is the second argument (0-indexed)
    extract_path: "args[0]"
    extract_method: "literal:GET"  # Django routes default to all methods

# express.yaml
patterns:
  # NEW: call-based route detection
  - concept: route
    referenced_in_call: "^(app|router)\\.(get|post|put|delete|patch)$"
    arg_position: -1  # Handler is last argument
    extract_method: "call_suffix"  # Extract from matched group
    extract_path: "args[0]"
```

### New Pattern Fields

| Field | Description |
|-------|-------------|
| `referenced_in_call` | Regex matching the function name of calls that reference this symbol |
| `arg_position` | Which argument position this symbol appears in (-1 for last) |
| `extract_path` | Path expression to extract route path from call args |
| `extract_method` | How to derive HTTP method (call_suffix, literal:GET, kwargs.method) |
| `call` | Match call sites directly (for creating synthetic symbols, not enrichment) |
| `create_symbol` | If true with `call`, create a new symbol for the call site itself |

### Pattern Matching Algorithm

```python
def enrich_symbols_with_call_patterns(
    symbols: list[Symbol],
    call_sites: list[CallSite],
    pattern_defs: list[FrameworkPatternDef],
) -> list[Symbol]:
    """Enrich symbols based on call-site patterns."""

    # Build reverse index: symbol_id → [CallSite that references it]
    symbol_to_calls: dict[str, list[CallSite]] = defaultdict(list)
    for call in call_sites:
        for arg in call.args:
            if arg.symbol_ref:
                symbol_to_calls[arg.symbol_ref].append(call)

    # For each symbol, check if any referencing call matches a pattern
    for symbol in symbols:
        referencing_calls = symbol_to_calls.get(symbol.id, [])
        for call in referencing_calls:
            for pattern_def in pattern_defs:
                for pattern in pattern_def.patterns:
                    if pattern.referenced_in_call:
                        match = pattern.match_call(call, symbol)
                        if match:
                            enrich_symbol(symbol, match)

    return symbols
```

### Analyzer Changes

Analyzers emit CallSite records for potentially significant calls:

```python
# Python analyzer - emit CallSite for path(), re_path(), url() calls
if func_name in {"path", "re_path", "url"}:
    call_sites.append(CallSite(
        id=_make_call_site_id(...),
        func_name=func_name,
        path=str(py_file),
        span=span,
        args=[
            CallArg(value=route_path, symbol_ref=None),
            CallArg(value=view_name, symbol_ref=view_symbol_id),
        ],
        kwargs={},
        caller_id=enclosing_symbol_id,
    ))
```

Analyzers do NOT need to know which calls are "framework-significant" — they can emit CallSite records for all resolvable calls, and YAML patterns filter for relevance.

### Benefits

1. **Analyzers become truly pure**: No framework-specific logic in analyzers
2. **YAML-extensible**: New call-based frameworks added via YAML alone
3. **Consistent model**: Both decorator-based and call-based frameworks use same pipeline
4. **Handler-centric**: The actual handler function gets enriched, not synthetic symbols
5. **Principled**: Captures the insight that symbol semantics derive from both definition AND usage

### Migration Path

1. Add CallSite IR type to `ir.py`
2. Extend AnalysisResult to include `call_sites: list[CallSite]`
3. Update Pattern class with new fields
4. Update `enrich_symbols` to handle call-based patterns
5. Add call-based patterns to `django.yaml`, `express.yaml`, etc.
6. Update Python analyzer to emit CallSite records (initially for Django patterns)
7. Remove hardcoded Django detection from `py.py`
8. Repeat for JS/TS, Go, Rust analyzers

### Open Questions

1. **Which calls to capture?** All calls would be expensive. Options:
   - Only calls with symbol references as arguments
   - Only calls to known function names (requires analyzer config)
   - All top-level calls (not inside functions)

2. **Synthetic symbols vs enrichment?** For inline handlers like `app.get("/path", (req, res) => {})`, should we:
   - Create a synthetic route symbol (current approach)
   - Enrich the arrow function symbol with route metadata

3. **Performance?** Building reverse index is O(calls × args). Acceptable for most repos.

## Consequences

### Positive
- Framework patterns become truly data-driven
- Easier to add new call-based frameworks
- Cleaner separation between language analysis and framework semantics
- More accurate: handler functions are enriched rather than synthetic symbols

### Negative
- More complex pattern matching logic
- Analyzers must emit CallSite records (larger IR)
- Two-phase enrichment (definition-based, then usage-based)

### Neutral
- Requires updating multiple analyzers
- YAML pattern syntax becomes richer
