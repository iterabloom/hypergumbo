<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0058: The Callable-Signature Axis

Status: Accepted
Date: 2026-09-21
Related: [ADR-0024](0024-axis-declaration-template.md) (the template this
instantiates, and which named this failure mode in advance),
[ADR-0051](0051-module-key-axis.md) (the same defect one field over; this
follows its retire-and-declare move and its structural-policy shape),
[ADR-0023](0023-edge-type-relationship-not-endpoints.md) (whose endpoint cut
this reuses), [ADR-0033](0033-spec-vs-data-validator-stage.md) §1 (why the
linter accepted the false declaration)

## Context

`Symbol.signature` holds the text of a callable's declaration. It is read by
the sketch renderer, serialised by `to_dict`, and — this is the problem —
parsed by nine consumers that branch on what they find inside it.

### The declaration that existed was false

```python
signature: Optional[str] = None  # axis: free-text — callable signature string
                                 # in source-language grammar; consumers
                                 # display, never branch on the value itself.
```

Nine shipped consumers branch on the value itself: six parse a return type
across five languages, two count parameters for overload selection, one reads
a field's declared type. The static linter accepted the declaration because a
`free-text` justification is required to be **present**, not **true**
(ADR-0051; ADR-0033 §1 gives `free-text` "no value check; the justification
was the gate at source-write time").

This is ADR-0051's defect one field over, and the resemblance is not
incidental — `ExternalRef.module_path`'s retired declaration read *"consumers
display/lookup, never branch on the value itself"*, almost verbatim. ADR-0051
answered by retiring the axis and declaring a real one rather than rewording
the justification, because rewording would leave a declaration contradicting
its own category: ADR-0024 defines `free-text` as an open-ended payload **no
consumer branches on**.

### ADR-0024 predicted this by name

The justification requirement exists for precisely this failure. ADR-0024 open
question 2, on why `free-text` alone carries one:

> it's the only category whose "this is the right call" claim isn't anchored
> elsewhere (named axes have a registry; `identity` has a uniqueness
> invariant; `bounded-enum` has the docstring listing), so it would otherwise
> be the natural can-kicker.

A false `free-text` justification is the designed-for failure of the scheme,
not a surprise in it. What the scheme lacks is re-checking: the gate fires at
source-write time and nothing re-reads it as consumers accumulate. That is a
gap in enforcement, and §"Enforcement" below closes it for this field.

### The slot carries two notions

Measured over a survey of this repository (52,983 symbols), 38,573 carry a
signature:

| notion | slots | share |
|---|---|---|
| callable surface — `(self) -> int`, `fn new(cfg: &Config) -> Self` | 36,662 | 95.05% |
| **bare value type on a non-callable** — `int`, `list[Edge]`, `&'static str` | **1,911** | **4.95%** |

The 1,911 are 1,886 `field` symbols and 25 `variable` symbols — python 1,817,
rust 49, typescript 35, then single digits in java, swift, csharp, go and
solidity. A field has no signature. What it has is a declared type, and the
slot is answering a question the symbol did not ask.

The producer side says so itself. `symbol_introspection`, the centralised
signature dispatcher, states that it "does not extend signature/docstring to
non-callable Symbols (vars, type aliases, fields)" — and Python, which is not
one of that module's ten languages, does it anyway through its own path.

### The facts inside already have homes, and Python is the outlier

This is what makes the ruling actionable rather than cosmetic. Every fact the
nine parsers go after is already declared somewhere that ships:

| Fact | Declared home | Populated by |
|---|---|---|
| return type | `FileAnalysis.method_return_types` → `_method_return_type_registry` | go, rust, swift, objc |
| return type (per-Symbol) | `meta["return_type"]` / `meta["inferred_return_type"]` | java, luau, apex |
| parameter arity | `meta["parameters"]` / `meta["params"]` | 15 analyzers, **including `py.py`** |
| field / variable type | `FileAnalysis.class_field_types` → `_field_type_registry` | csharp, cpp |

`inferred_return_type`'s own registry description names Python as an intended
producer: *"Set by the limited type-inference passes (Python
`typing.get_type_hints`, etc.)."*

And `py.py` cites the home while parsing the string, 5,240 lines above the
parse site:

> `THE CONCEPT'S HOME IS FileAnalysis.method_return_types (INV-dihos /
> WI-kuroj), the language-neutral return-type registry Go and Rust already
> populate from parsed signatures.`

The principle is not new here either. It is ADR-0023's cut, which ADR-0051 §2
reuses — properties of an endpoint are queried from the endpoint, not smuggled
into the label — and ADR-0024's Fold-residue rule 1 verbatim: *"If a property
is queryable from `src` or `dst`, query the endpoint instead."* WI-lalot makes
the same argument in `py.py` in three words: **deriving beats enumerating**.

## Decision

Declare the **callable-signature axis**. Registry:
`hypergumbo_core.signature_axis`. Property tests:
`packages/hypergumbo-core/tests/test_signature_axis.py`.
`callable-signature` is wired into `_known_axes()`, and `Symbol.signature` now
declares it.

### 1. Axiom

> `Symbol.signature` preserves the **declaration surface** of a callable,
> verbatim, in the source language's own grammar, for **display**. Every fact
> inside it — return type, parameter arity, a field's declared type — is a
> property of the symbol and is read from its **declared home**, never parsed
> back out of this string.

Falsifiable against a consumer: does it display the value, or read into it?

### 2. Two notions, two sections

| Notion | Section | |
|---|---|---|
| `callable_surface` | `declaration_surface` | the parameter-and-return text of a callable; the only notion the field's name describes |
| `value_type` | `foreign_fact` | the declared type of a field or variable; real, wanted, and homed in `class_field_types` |

`pending_classification` exists and is empty. Axiom-conformance is **derived**
from the section, never stored per-notion — a stored flag beside the section
would be one fact in two homes, the shape this axis exists to remove. An
undeclared name derives to *not* conformant, because a notion nobody argued
cannot have been argued to satisfy the axiom, and defaulting to True is the
direction that manufactures a false all-clear.

### 3. Structural policy, not a value registry

Signature strings cannot be enumerated — every callable in every grammar is a
legal value — so there is no membership set to check. This follows
`qualified_name_axis` and `module_key_axis` under ADR-0024 §4's "use judgment"
carveout: a module-level declaration plus accessors, and the `_known_axes()`
resolver returns the axis's **notions** rather than legal field values.

### 4. Enforcement: the parser set is CLOSED

ADR-0024 makes enforcement mandatory, and ADR-0051 had to ship without a drift
linter because there was nothing enumerable for the AST walker to check. There
is here, and it is not the values — it is the **consumers**.

`find_undeclared_value_parsers` walks `packages/*/src` for reads of
`.signature` whose value flows into a call, and fails on any site not in
`LEGACY_VALUE_PARSERS`. That tuple holds the nine, each cited by `file:line`
plus an anchor string and each naming which fact it is after; a companion test
asserts every citation still says what it claimed, because a rotted citation
sends the next reader to a line that now means something else (ADR-0051 §5).

This is the owner's 2026-09-21 ruling, mechanised: **the nine are
grandfathered; a tenth is a decision.** The gate is where that decision gets
made, rather than where it gets noticed afterwards.

Two limits are stated rather than papered over. A value bound to a local is
beyond a static walk's reach, so the gate catches the **handoff** — which is
what the citations record. And a keyword argument named `signature` is treated
as a carry (`Symbol(signature=sym.signature)`, the SCIP round-trip at
`translate.py:164`), so a parser called as `_extract(signature=sym.signature)`
would be invisible. That form does not occur in the tree; it is a known hole,
written down so the next reader does not mistake silence for coverage.

The `getattr(sym, "signature", None)` form **is** matched, because
`jackson_dispatch` uses it and a plain attribute walk cannot see it — the
blind spot INV-lafid records for the io-boundary drift linter, avoided here by
having been looked for.

## Consequences

### Positive

- The declaration stops contradicting the code, and the next reader who
  obeys it is not blocked by a false instruction. That is not hypothetical:
  obeying it would have blocked WI-fihun's cure outright.
- "Where do I read it instead?" has a table answer, not a research task.
- A tenth parser cannot land quietly. The failure mode that produced this ADR
  — accumulate consumers under a declaration nobody re-reads — is now a red
  test rather than an archaeology exercise.

### Negative

**Nothing an analyzer emits changes, and the nine keep parsing.** This
declares and closes; it does not migrate. Migration is ADR-0024 step 7 and is
filed separately as the per-language producer port, Python first — it is the
largest consumer (36,256 callable surfaces, 1,817 value types) and the only
one of the four home-populating languages' peers that writes none of them.
Until that lands, the axiom describes what the field should hold while 4.95%
of shipped slots do not. A documented gap rather than a fixed one — the same
posture ADR-0051 took, for the same reason.

**The `value_type` notion has no producer-side gate.** Nothing stops an
analyzer stamping a field's type into `signature` tomorrow. The consumer side
is closed; the producer side is enforced only by this document and by
`symbol_introspection`'s scope note.

### Open questions

1. **Is `value_type` a fold or a deprecate-no-fold?** ADR-0024's trichotomy
   wants a verdict and this ADR does not give one. FOLD (move the fact to
   `class_field_types`, stop writing `signature` on non-callables) is the
   likely answer, but it is a producer migration across at least eight
   languages and the verdict belongs in the audit-findings document that
   migration produces, not here.
2. **The sibling field is sound, and that is the argument for sweeping the
   rest.** `Symbol.docstring` carries a near-identical justification —
   "consumers display/log/hash, never branch on the value itself" — and it is
   **true**: all seven reads in `packages/*/src` are display (`sketch.py`
   4310 / 5422 / 6652), serialisation (`ir.py:711`) or *presence* checks
   (`base.py` 2080 / 4087, `is None`). A presence check is not a value
   branch. So the clause is not inherently rotten; it goes false when
   consumers accumulate and nobody re-reads it, which is a re-checking gap
   rather than a wording problem — and it is why the gate in §4 checks
   consumers rather than the comment. ADR-0051 left the same question open
   about `ExternalRef.name` and it is **still open**; with 21 `free-text`
   declarations across the three core dataclasses and two now known to have
   gone false, the remaining 18 want one sweep rather than a fourth
   one-at-a-time instance. **No row moves on this note**; the sweep is filed
   separately.
3. **Should the closed-parser-set mechanism generalise?** It is the first
   enforcement in this project that gates *consumers* rather than *values*,
   and it exists because this axis has no enumerable value set. Other
   structural-policy axes (`qualified-name`, `module-key`) have the same
   shape and the same missing linter. Revisit if a third one wants it.
