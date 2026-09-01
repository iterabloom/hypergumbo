<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0051: The Module-Key Axis

Status: Accepted
Date: 2026-09-01
Related: [ADR-0024](0024-axis-declaration-template.md) (the template this
instantiates), [ADR-0032](0032-canonical-name-fingerprint-reshape.md) (whose
`qualified_name` axis is the structural-policy precedent this follows),
[ADR-0050](0050-io-boundary-axis.md) (the sibling axis, declared together under
the same owner ruling, taking the heavyweight enumerable shape instead),
[ADR-0023](0023-edge-type-relationship-not-endpoints.md) (whose
endpoint-property cut this reuses)

## Context

Every external call is matched against an I/O-primitive catalogue on a **module
key**: `IoPrimitive.module` on the catalogue side, `ExternalRef.module_path` on
the edge side, paired by `io_boundary._module_matches`. Unlike `Edge.edge_type`
(ADR-0023), `Symbol.kind` (ADR-0027) and `Edge.evidence_type` (ADR-0028), it had
no declared axis — no axiom, no registry, no consumer helper, no drift linter.

### The declaration that existed was false

`ExternalRef.module_path` was not undeclared. It read:

```python
module_path: str  # axis: free-text — module import path in source-language
                  # grammar; consumers display/lookup, never branch on the
                  # value itself.
```

A consumer branches on the value itself. `io_boundary._module_matches` decides
type-vs-sub-package from **orthography** — `longer_raw[shared][:1].isupper()` —
and carries a Swift carve-out that branches on the value being a single token
the catalogue name ends with. The static linter accepted the declaration because
a `free-text` justification is required to be **present**, not **true**.

That distinction matters for what this ADR does: it is not only adding an axis,
it is retiring a wrong one that sat on a line the linter was already reading.

### The heuristic it justified is information-free in five languages

`_module_matches`'s own docstring justifies the capitalisation rule by *Go's*
naming convention ("package names are lowercase, exported type names are
capitalised") while the predicate serves all fifteen catalogues. Where module
names are capitalised the discriminator is constant-true: haskell 100%, swift
97%, objc 95%, elixir 52%, javascript 21%.

### The cost, as filed history rather than principle

Roughly eighteen tracker items reduce to one conflation — INV-linub, INV-zuvib,
INV-hahak, INV-fofoj, INV-januj, INV-kotob, INV-mumov, INV-safig, INV-fokik,
INV-funuf, INV-zimud, INV-papih, INV-dijor, WI-zazul, WI-damir, WI-sugom,
WI-gudak, WI-papar, WI-kamin, WI-monul. They filed as separate analyzer bugs
because no document said what the field was for, so each mismatch read as a
local defect. Two of them turned out to be **one defect in two languages**
(INV-fofoj's java half *is* INV-januj), which is the signature of a missing axis
rather than unrelated bugs.

Measured over 65,187 external refs on a 21-repo, 10-language cold cohort, 7.8%
of slots are not a single module identity. The producer surface agrees: 54
`ExternalRef` construction sites across 20 analyzers, with the local feeding
`module_path` variously named `path_hint`, `module_hint`, `module_name`, `mod`,
`hint`, `ns`, `pkg`, `receiver_name`, `wildcard_module`, and the literal
`"redirect"`. No two analyzers call it the same thing.

The clearest single piece of evidence is four lines of source, not a percentage
— `swift.py:1223`:

```python
gate_path_hint = (
    receiver_type                       # a TYPE
    or import_aliases.get(callee_name)  # a NAMESPACE
    or receiver_hint                    # a receiver VARIABLE
    or "external"                       # a SENTINEL
)
```

One fallback chain, four notions, one slot, no record of which one produced it.

## Decision

Declare the **module-key axis** with ADR-0024's four artifacts. Registry:
`hypergumbo_core.module_key_axis`. Property tests:
`tests/test_module_key_axis.py`. `module-key` is wired into `_known_axes()`, and
`ExternalRef.module_path` now declares it.

### 1. Axiom

> The module key names the **static owner path** of the called symbol — the
> namespace or type in which it is **defined**, spelled in the source
> language's import vocabulary. It is not a property of the **call site**: not
> the receiver's variable name, not a set of candidates, and not a marker for
> the absence of an answer.

### 2. A type is conformant; a receiver variable is not

This is the cut the axiom exists to make, and the one an orthographic heuristic
cannot. The catalogues are full of types — `net.Conn`, `std::fs::File`,
`java.sql.Connection`, `pathlib.Path` — deliberately: a method-shaped primitive
is unaddressable without its owning type, and `IoPrimitive.module`'s docstring
has always said "the module or class path". A type names where the symbol is
**defined**, so it is an owner path.

A receiver **variable** names a local binding at one call site. `resp` is not
where `read` is defined, and the same primitive reached through a
differently-named variable gets a different key. That is ADR-0023's cut reused:
properties of an endpoint are queried from the endpoint, not smuggled into the
label. The receiver's type already has a home in
`Edge.meta["receiver_type_hint"]` — stamped by six analyzers and read by neither
`io_boundary` nor `taint` (WI-monul).

The tell that this is right: `_module_matches` carries an explicit Swift
carve-out (catalogue name ends with hint, never the reverse) whose only purpose
is to tolerate a non-conformant value.

### 3. Six notions, four sections

| Notion | Section | |
|---|---|---|
| `namespace` | `owner_path` | package / module / import path; C headers and JS relative paths included |
| `type` | `owner_path` | the class owning a method-shaped primitive |
| `receiver_variable` | `call_site_property` | the receiver's spelling at one call site |
| `disjunction` | `uncertainty` | cpp's comma-joined `#include` set — a set, not an identity |
| `sentinel` | `uncertainty` | `external`, bash's `redirect` |
| `global_object` | `pending_classification` | `process`, `window`, `navigator` — **unruled** |

`uncertainty` values are non-conformant *as identities* while being the honest
answer to the question. The disjunction is already handled downstream by two
deliberately different quantifiers — `_module_hint_candidates` asks ANY
(INV-funuf), `module_hint_disjuncts` asks ALL (INV-zimud) — which is why the
shape wants its own field rather than removal.

Axiom-conformance is **derived** from the section, never stored per-notion: a
stored flag beside the section would be one fact in two homes, the exact shape
this axis exists to remove. `pending_classification` derives to *not*
conformant, because it means "not yet argued", not "argued and accepted".

### 4. Structural policy, not a value registry

Module names cannot be enumerated, so there is no membership set to check
against. This follows `qualified_name_axis` (ADR-0024 §4's "use judgment"
carveout): a module-level declaration plus accessors, no per-value registry of
legal field values. The resolver wired into `_known_axes()` returns the axis's
**notions**; `_check_field` tests the declared axis *name* for membership and
never the field's values, so this is the same contract `qualified-name` already
relies on. The sibling io-boundary axis takes the heavyweight enumerable shape,
and the two differ for this reason rather than by accident.

### 5. Citations are checked, not trusted

Each notion cites the producer site that motivated it by `file:line` plus an
anchor string, and `test_every_cited_emission_site_still_exists` asserts the
file exists and the line still contains the anchor. A rotted citation is worse
than none: it sends the next reader to a line that now says something else.

## Consequences

### Positive

- The eighteen-item pile becomes legible: each is an axis violation against a
  written sentence rather than a fresh mystery. WI-virav does that sweep.
- WI-zozun can replace `_module_matches`'s orthographic inference with a
  declared discriminator, which dissolves INV-dijor's arm-3 false positive and
  the under-matching in capitalised-module languages **in the same change**,
  rather than trading one for the other.
- A false justification on a core dataclass is now a known failure mode with a
  worked instance.

### Negative

**Nothing an analyzer emits changes.** `external_symbol` node ids embed the
module slot (`rust:external:0-0:File::open`), so normalising the slot's content
changes emitted ids and needs a stable_id scheme bump, which WI-talos DECISION 3
gates behind v9/v10 and two releases. That is ADR-0024 step 7 and is WI-marok.
Until it lands, the axiom describes what the field *should* hold while 7.8% of
shipped slots do not — a documented gap rather than a fixed one.

**No drift linter.** ADR-0024 makes enforcement mandatory, and this axis ships
with a property test only. The AST walker in `axis_drift` checks that a
consumer's hardcoded set is a subset of a registry; with no enumerable value set
there is nothing for it to check. The enforcement that *does* apply — the
`# axis:` declaration lint on core dataclasses — is live and is what the ir.py
change satisfies. Stated as a deviation rather than papered over.

### Open questions

1. **`global_object`.** Unruled, deliberately. The case *for* conformance: you
   do not import `process` in node, so the global's name is how JS's vocabulary
   spells that owner path, and `js_ts.py` maps each to itself in the import map
   for exactly that reason. The case *against*: these name a value rather than a
   definition site. First candidate for a per-value audit; **no row moves** on
   this note.
2. **`ExternalRef.name` carries the same justification clause** that was proven
   false on `module_path` — "consumers display/lookup, never branch on the value
   itself" — while `strip_redundant_module_qualifier` decomposes it with
   `rpartition(".")` and compares component-wise. Filed separately rather than
   folded in; declaring a symbol-name axis is a separate argument.
3. **The by-axis view omits `qualified-name`.** `docs/concept-axes.md` now
   renders four axes including this one, but the other structural-policy axis
   has never appeared there. Noted, not fixed here.
