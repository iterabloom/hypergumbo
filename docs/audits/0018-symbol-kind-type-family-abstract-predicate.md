<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Symbol.kind Type Family — the abstract-type predicate

| | |
|---|---|
| **Axis** | `Symbol.kind` (ADR-0027, `language_construct`) |
| **Date** | 2026-07-31 |
| **Trigger** | INV-tihim. Seven language-agnostic consumers were found hand-rolling mutually inconsistent sets of "type-like" kinds, and `linkers/type_hierarchy.py:113` gates virtual dispatch on `child_kind == "interface"` alone — so Rust `trait` and Swift `protocol` fall outside a rule the neighbouring linker gets right. The suspicion was that `class` is apex/peer overloaded (naming both a concrete class and an abstract type), which would mean the vocabulary itself was confused. |
| **Outcome** | **Rejected on axis-correctness; confirmed on enumeration-completeness.** No value on this axis is leaking. Every defect found is a *consumer* that hand-rolls an incomplete grouping the registry cannot express. |
| **Cadence** | Recorded as `type-kind-vocabulary` (9 prior audits; last `identity-vocabulary`, 2026-07-15). |

## Context

`Symbol.kind` carries **138 registered values on exactly one axis**, `language_construct`. There is no sub-grouping: no family attribute, no derived resolver. `symbol_kinds_on_axis("language_construct")` returns all 138, which cannot answer "which kinds are abstract types?"

Contrast `Edge.edge_type`, which carries three axes and whose ADR instructs consumers directly: *"Consumer-side sets that need a subset of edge types should call `edge_types_on_axis(...)` instead of maintaining their own list"* (ADR-0023 §3, Enforcement 1). That instruction has no expressible analogue for `Symbol.kind`, so every consumer needing a subset writes a literal.

This audit tests whether that situation is caused by a confused vocabulary (which would require folding values) or by a missing predicate layer over a sound one.

## Methodology

Fundamental Concept Audit playbook, Steps 2–5. Step 3's four leakage tests were applied to the pair `class` vs `{interface, trait, protocol}`, and to `class` against itself in its abstract and concrete uses. Step 4 was run twice — a manual sweep and an AST walk over every `packages/*/src/**/*.py` set/tuple/list/frozenset literal whose members are *all* registered `Symbol.kind` values and of which at least two are type-like.

Evidence is from a live cross-language run (11 languages, one interface-and-implementor pair each, full pipeline), not from reading producers.

## Step 3 — the four leakage tests

Measured emission, 11 languages:

| language | abstract construct | `kind` | `modifiers` | concrete construct | `kind` |
|---|---|---|---|---|---|
| java | `interface` / `abstract class` | `interface` / `class` | — / `['abstract']` | `class` | `class` |
| csharp | `interface` / `abstract class` | `interface` / `class` | — / `['abstract']` | `class` | `class` |
| php | `interface` / `abstract class` | *(external_symbol)* / `class` | — / `['abstract']` | `class` | `class` |
| scala | `trait` / `abstract class` | `trait` / `class` | — / `['abstract']` | `class` | `class` |
| kotlin | `interface` / `abstract class` | `interface` / `class` | — / `['abstract']` | `class` | `class` |
| typescript | `interface` / `abstract class` | `interface` / `class` | — / **`[]`** | `class` | `class` |
| cpp | pure-virtual `class` | `class` | **`[]`** | `class` | `class` |
| rust | `trait` | `trait` | — | `struct` | `struct` |
| swift | `protocol` | `protocol` | — | `class` | `class` |
| go | `interface` | `interface` | — | `struct` | `struct` |
| python | `Protocol` / `ABC` subclass | `class` | — (derivable from `meta.base_classes`) | `class` | `class` |

1. **Property derivability — NOT leakage.** `interface` / `trait` / `protocol` / `class` name genuinely different source constructs; the distinction is not derivable from properties of what they describe. Consistent with the standing NO-FOLD ruling on the OOP inheritance family.
2. **Apex/peer overloading — NOT leakage. This is the test the audit was run to answer, and it comes back negative.** Every analyzer emits `class` for a class *declaration*, never as "the generic top type." Abstract-ness is carried on a **different field**, `modifiers`, and five of six languages with an `abstract class` construct populate it correctly. `class` plays one role. *(Distinct from audit 0009, which tested apex/peer in the abbreviation-synonym sense — `function`/`fn`, `struct`/`structure`. `class` was not in that scope.)*
3. **Construct vs. relationship — NOT leakage.** All values are construct labels; no relationship label competes for the slot.
4. **Mechanism vs. category — NOT leakage, and this is the affirmative finding.** "Abstract vs concrete" is a *property* of a declaration, not a different kind of declaration — so it belongs in metadata rather than in the type. It is already there, in `modifiers`. The design is correct.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: class
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('class');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 2 (apex/peer) fired NEGATIVE — the test this audit was run to answer. Measured across 11 languages, every analyzer emits `class` for a class declaration and never as a generic top type; abstract-ness is carried on Symbol.modifiers (['abstract'] in java/csharp/php/scala/kotlin) or derived from meta.base_classes (python ABC/Protocol). One value, one role."
  - value: interface
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('interface');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 1 (property derivability) NEGATIVE — a distinct source construct in java/csharp/go/typescript/php/kotlin, not derivable from properties of what it describes. Consistent with the standing NO-FOLD ruling on the OOP inheritance family."
  - value: trait
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('trait');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 1 NEGATIVE. Distinct construct in rust/scala/groovy; a Rust trait is not an interface and not a class. Emitted as kind='trait' by both analyzers in the live probe."
  - value: protocol
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('protocol');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 1 NEGATIVE; reaffirms audit-findings 0007. Distinct construct in swift/solidity. NOTE: this value is CANONICAL in the registry and simultaneously ABSENT from five language-agnostic consumer sets — see Step 4. The defect is consumer-side, not a reason to fold the value."
  - value: struct
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('struct');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 1 NEGATIVE. Distinct construct in rust/go/cpp/csharp. Its abbreviation peer `structure` was already folded by audit-findings 0009; that fold is disjoint from this audit's concrete-vs-abstract question."
  - value: enum
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python -c \"import sys;from hypergumbo_core.symbol_kinds import find_symbol_kind as f;s=f('enum');sys.exit(0 if s and s.axis=='language_construct' else 1)\""
      expect: exit_code:0
    rationale: "Test 1 NEGATIVE. Included in scope because it co-occurs in the hand-rolled type-like sets under audit. Kotlin emits `enum class` as kind='class' with modifiers=['enum'] — a modifier-carried distinction, the same correct pattern as `abstract`, not a leak."
```

Every value on this axis passes all four tests. No FOLD. No DEPRECATE-NO-FOLD. **No ADR-0027 change, and therefore no `stable_id` churn and no v9/v10 scheme gating** — the release-gated risk this audit was run to rule out does not materialize.

## Step 4 — the silent bugs

The manual sweep found 7 sets. The AST walk found **47 sites carrying ≥2 type-like kinds; 26 survive the strict filter (every member a registered kind), spanning 24 distinct vocabularies.** The playbook's expectation that automation beats the manual count held.

Per-language analyzer sets are **legitimately** incomplete — `java.py`'s `{class, enum, interface}` is correct because Java has no traits, and `swift.py`'s `{class, enum, protocol, struct}` is correct because Swift has no interfaces. Only **language-agnostic** sites can be wrong. Among those, `protocol` is omitted from five:

| site | set | missing |
|---|---|---|
| `linkers/type_hierarchy.py:493` | `class, interface, struct, trait` | `protocol` |
| `linkers/inheritance.py:426` | `class, interface, struct, trait` | `protocol` |
| `linkers/_transitive_bases.py:51` | `class, interface, struct, trait` | `protocol` |
| `linkers/di_resolution.py:669` | `class, interface` | `protocol`, `trait` |
| `cli.py:6629` | `class, enum, interface, struct, trait` | `protocol` |

Three language-agnostic sites are complete — `linkers/containment.py:136`, `linkers/inherited_calls.py:890`, `slice.py:569` — and `linkers/inheritance.py:91` carries the correct abstract family `{interface, trait, protocol}`. **`inheritance.py` therefore holds both a correct set and an incorrect one, 335 lines apart.**

**Proven consequence.** Same construct, same shape, one word different:

```
java   interface Shape / class Square implements Shape
   ->  implements: class:Square -> interface:Shape
   ->  dispatches_to: method:Shape.area -> method:Square.area

swift  protocol Shape  / class Square: Shape
   ->  implements: class:Square -> protocol:Shape
   ->  (no dispatches_to)
```

Swift loses interface dispatch entirely, silently, because `type_hierarchy.py:493` never admits `protocol` as a type that can own methods.

**The propagation mechanism is documented in a comment.** `type_hierarchy.py:493` is preceded by *"Struct and trait are included to match the inheritance linker's broader definition of 'type with methods'"* — the set was copied from a sibling, and the copy inherited the sibling's omission along with its contents.

**Recent instance, for the record.** WI-duguk (2026-07-30) added `protocol` to `containment.py` and `slice.py` — two of the seven language-agnostic sites — and did not sweep the rest. That is the per-site-sweep failure mode the project's through-line names, committed one day before this audit, by the agent running it.

## Step 4b — a second, independent gap

`modifiers` is the correct home for abstract-ness, but it is **incompletely populated**:

- **typescript** emits `abstract class Base` as `kind="class"`, `modifiers=[]`.
- **cpp** emits a pure-virtual class as `kind="class"`, `modifiers=[]` — and drops abstract method declarations entirely (`virtual int area() = 0;` and `virtual int perim();` both vanish; only bodied methods survive).

So even a correct predicate cannot classify C++ or TypeScript abstract types until their analyzers populate the modifier. This is a producer gap, orthogonal to the grouping gap, and each drains independently.

## Adjacent audited (Step 5)

| Field | Verdict |
|---|---|
| `Symbol.modifiers` | **Correct home, under-populated.** `abstract` is the right discriminator and lives in the right field. Gap is producer coverage (cpp, typescript), not design. |
| `Symbol.meta.base_classes` | **Correct home.** Python's abstract-ness (`ABC`, `Protocol`) is derivable from it; no separate kind needed. |
| `Edge.edge_type` `extends` vs `implements` | **No leak found.** Both are emitted correctly per language in the 11-language probe. `NO_VIRTUAL_EXTENDS_LANGUAGES` (WI-sukav A1) correctly gates concrete-extends dispatch for go/cpp/rust/csharp; its `child_kind == "interface"` override is not a vocabulary defect but an instance of the same missing predicate. |

## Action

**No deprecation, no fold, no ADR-0027 amendment.** The remedy is a predicate layer, and it is the standard chokepoint shape:

1. Give `SymbolKindSpec` a type-family attribute and add registry-backed resolvers (an abstract-type predicate and a type-like predicate) — the `Symbol.kind` analogue of `edge_types_on_axis()`. This is **not** a new axis declaration under ADR-0024: no new field, no re-classification of any value, only a taxonomy over an already-declared axis.
2. Sweep the five incomplete language-agnostic sites onto it, and change `type_hierarchy.py:113` from `child_kind == "interface"` to the abstract-type predicate.
3. Add a linter that fails when a language-agnostic module hand-rolls a literal set of type-like kinds instead of calling the resolver — so the copy-a-sibling's-set mechanism cannot recur. Per-language analyzer modules are exempt by construction.
4. File the cpp / typescript `abstract` modifier population as its own work, separate from the grouping layer.

## Incidental finding

The cross-language probe surfaced a defect unrelated to this axis: **the Kotlin analyzer emits zero type symbols when a file contains two or more bodied type declarations**, re-emitting the first method as a top-level function. Reproduces at `9f0a163833^`, so it is not a WI-dorop regression. Filed separately; not in this audit's scope.

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — the axis this audit rules under
- [ADR-0023 §3](../adr/0023-edge-type-relationship-not-endpoints.md) — the `edge_types_on_axis()` precedent this proposes to mirror
- [0009](0009-symbol-kind-cluster-c-apex-peer.md) — apex/peer in the abbreviation-synonym sense; disjoint from this audit's Test 2
- [0007](0007-symbol-kind-cluster-h-long-tail.md) — `protocol` CANONICAL, reaffirmed here
- INV-tihim — the item that triggered this audit; unblocked by it
- INV-kobad — "features implemented for one language must propagate to siblings"; this is an instance
