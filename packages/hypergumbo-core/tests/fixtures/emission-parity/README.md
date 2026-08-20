<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# emission-parity fixtures (G2)

Injected, single-file-per-language fixtures driving the per-(language,
field/edge-type) **emission-parity gate** —
`packages/hypergumbo-core/tests/test_emission_parity_matrix.py`
(strategy item `emission-parity:F1`, guardrail **G2**).

## Why injected, uniform fixtures

`WI-rubip` (a load-bearing P3) records the methodological trap: a parity matrix
derived from a *real* substrate at small N cannot distinguish an **analyzer
gap** ("the analyzer never emits this field") from a **construct-absent**
artifact ("this repo happens to contain no such construct"). An empty cell is
ambiguous.

These fixtures remove the ambiguity. **Every** language fixture deliberately
contains the **same** construct set, so every matrix cell is *applicable* to
every language. An empty cell therefore means the analyzer did not emit the
field for a construct that is provably present — i.e. a real analyzer gap, never
construct-absence.

## The uniform construct set

Each `<language>/` fixture contains one source file exercising, idiomatically
for that language:

| Construct | Exercises (matrix column) |
|---|---|
| A module/package `import` | `edge_imports` |
| A public, **documented** callable (a free function or static method, language-appropriate) with a **3-branch** body (cyclomatic complexity 4) that **calls** a helper | `signature`, `docstring`, `complexity_nontrivial`, `edge_calls` |
| A helper callable (the callee) | — |
| A class with a method (nested name) | `qualified_name` |
| A language-appropriate **export** of the public surface | `is_exported` |
| An entrypoint idiom (`__main__` guard / `main()` / top-level run) | `entrypoint_concept` |
| A module/package-level **variable** (where the language has one) | `emits_variable` |
| A **class/struct field** (a value member of a type) | `emits_field` |
| An **enumerated type with named members** (where the language has one) | `emits_enum_members` |
| An **abstract type with member signatures** — interface / protocol / trait | `emits_abstract_members` |

Per-language shape differs under the same construct: Java/C# have no top-level
functions, so their `process`/`helper` are static methods inside a class; C#
emits the raw `<summary>...</summary>` XML-doc form as the `docstring` value
(not a stripped summary — that is the analyzer's current behaviour, not a bug).

`emits_variable` / `emits_field` (WI-jusus, emission-parity F5) are the
value-binding kind-emission cells. Two per-language shape notes: **(1)** Java and
C# have **no module/package-level variables** — every value binding is a class
member — so `emits_variable` is *not applicable* there and is not a matrix cell
(`COLUMN_APPLICABILITY` in the test); their value-binding parity is measured by
`emits_field` alone. **(2)** Go's module variable uses `var MaxItems = 5` (the
form the analyzer emits — a package `const` is not emitted as a `variable`); Rust
adds a separate `Config` struct for the field because `Service` is a unit struct.

`emits_enum_members` / `emits_abstract_members` (WI-duguk) are the
container-member cells: does the analyzer emit a Symbol for each *member* of a
container it already emits? They measure **span nesting**, not `contains` edges
— this gate runs one analyzer in isolation and `contains` is minted downstream
by the containment linker, whereas the measured defect is that the member
symbol is never emitted at all (a missing node, not a missing edge). Three
per-language applicability notes: **(1)** Python's enumerated type and its
`Protocol` are both *classes*, so they would be scored through class-member
emission that `emits_field` already locks — neither is a cell. **(2)**
JavaScript has neither construct (both are TypeScript-only). **(3)** Go has no
enumerated type; its idiom is a `const` block whose members are siblings of the
type rather than nested in its body, so only `emits_abstract_members` applies.

The branchy function is named `process` in every fixture and has exactly three
`if` statements, so an analyzer that computes McCabe complexity reports `4` and
one that hardcodes/omits it reports `1` (or `None`) — this is the controlled
construct that falsifies `WI-litil`'s "cyclomatic_complexity uniformly 1"
suspicion per language.

## Live dataclasses, not serialized JSON

The gate runs each analyzer in-process via
`hypergumbo_core.analyze.registry.run_analyzer(name, fixture_dir)` and inspects
the returned **live `Symbol`/`Edge` dataclass instances**. This is deliberate:
several declared fields are *relocated* during JSON serialization (e.g.
`Symbol.is_exported` → `supply_chain.is_exported`), so a JSON-derived probe
reports them as "100% None" even when the analyzer populates them. Reading live
dataclasses measures **analyzer emission**, which is what parity is about; the
serialization relocation is a separate (schema) concern.

## Fixture → analyzer mapping

There is no separate `typescript` analyzer — the `javascript` analyzer handles
`['javascript', 'typescript', 'vue', 'svelte']`. The `typescript/` fixture
therefore exercises the **same** analyzer over TypeScript syntax, giving the
js-vs-ts contrast `INV-golap` is about. All eight analyzers in the matrix are
`availability="core"` (no optional tree-sitter grammar wheel required), so the
gate runs deterministically per-PR.

## What this gate does NOT establish

- **It does not resolve `INV-golap`.** The `signature` column uses function
  declarations plus an exported arrow function (`compute`), both of which the
  JavaScript/TypeScript analyzer *does* sign — so `signature` is a healthy hard
  lock for js/ts here. `INV-golap`'s 2/5 finding is about a different idiom
  (anonymous/callback functions in real code) that this fixture does not
  contain; a green `signature` cell here does **not** mean `INV-golap` is fixed.
- **Presence semantics, not full population.** Each cell asserts the analyzer
  emits the field for *at least one* applicable symbol/edge of the fixture, not
  that it populates every symbol or populates the value *correctly*. The gate
  catches total-absence parity gaps and their regressions; it is not a
  correctness oracle for field values.

## Adding a language

1. Add `<language>/<file>` with the full uniform construct set above.
2. Add the `<language> -> <analyzer-name>` row to `FIXTURE_ANALYZER` in the
   gate test.
3. Run the gate. Cells the analyzer emits become hard locks automatically; for
   any genuine, tracker-documented gap add a `KNOWN_HOLES` entry (a strict
   `xfail`) citing the item — never silently drop the cell.
