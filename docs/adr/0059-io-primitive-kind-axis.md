<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0059: The I/O-Primitive-Kind Axis

Status: Accepted
Date: 2026-09-23
Related: [ADR-0024](0024-axis-declaration-template.md) (the template this
instantiates), [ADR-0050](0050-io-boundary-axis.md) and
[ADR-0051](0051-module-key-axis.md) (the two other axes on `IoPrimitive`; this
axiom is stated against ADR-0051's module key), [ADR-0016](0016-io-boundary-analysis.md)
(which introduced the catalogues and their `functions:` / `methods:` sections),
[ADR-0047](0047-catalogue-scope-and-user-visible-homes.md) (overlay authors, who write these
rows without reading the analyzers).

## Context

`IoPrimitive.kind` takes its value from the YAML section a catalogue row sits
under: `functions:`, `methods:` or `attributes:`. Nothing ever said what those
sections assert. The field was declared `# axis: bounded-enum`, and its
docstring read "Either 'function' or 'method'", although the loader produces
three values (WI-fazad).

The 2026-09-23 fundamental concept audit found three rules stated in the
catalogues' own notes:

| Reading | Stated in |
|---|---|
| **what the callee IS** (declared as a member of a type or not) | `rust.yaml` ("catalogued as a method because that is what it IS"), INV-pimir's statement |
| **whether a value supplies the callee's owner** | `cpp.yaml` ("Static member functions, hence function-kind"), `scala.yaml` (companion factories have "no value receiver"), `python.yaml` (classmethods, INV-nular / INV-fugus), WI-komun |
| **what the language's analyzer stamps at the call site** | `java.yaml` ("java's analyzer stamps call_construct=method"), `kotlin.yaml` |

Scala holds two of these in one merged catalogue. Its own `Source.fromFile` is
keyed by the second rule, and the `java.nio.file.Files` statics it inherits
through `_CATALOG_PARENTS` are keyed by the third.

**Every consumer that branches on the value needs the second rule, and says so
in its own docstring.**
- `gate_named_entry`: "a method-kind primitive needs a module hint."
- `untyped_receiver_sites`: "A function-kind primitive is not reached through a
  receiver at all."
- `analyzer_disclosure`: the method-kind denylist "governs the instance-method
  call path."
- `method_starved_modules`: route 2 treats function-kind names as matchable
  without a receiver stamp.

**The cost was measured on 21 fresh surveys, not theorised.**
- JS `process` was reported "structurally invisible" on 6 of 9 JS-bearing
  repositories, on calls the classifier had matched (INV-dihun). This was the
  fourth recurrence of INV-fugus's contradiction.
- Keying rows to one analyzer's stamp is also what makes the fix order matter.
  Re-kinding the Java statics loses 41 classifications through a second place
  that joins the construct stamp against the kind (`lookup_with_module`'s
  INV-nizom arm).
- Sixteen Swift constructor rows are unreachable today, and most of their
  boundaries are wrong. The unreachable kind had hidden the wrong boundaries.

The owner convened a three-seat panel (axis-design purist, verdict-soundness
adjudicator, polyglot catalogue author). The seats worked independently and
read-only, and all three chose the second rule. All three also moved the
audit's "at the call site" wording onto the row, for the reason in §1. The owner
ratified the panel's wording the same day. The full record is on INV-zikab.

## Decision

Declare the **io-primitive-kind axis** with ADR-0024's four artifacts:
- **Registry and helpers:** `hypergumbo_core.io_primitive_kinds`.
- **Drift check:** `find_kind_literal_drift`, run by `scripts/check-io-boundary-drift`.
- **Property tests:** `tests/test_io_primitive_kinds.py`, plus the python
  conformance sweeps in `tests/test_associated_fn_kinds.py`.
- **Declaration:** `io-primitive-kind` is wired into `_known_axes()`, and
  `IoPrimitive.kind` declares it. `TaintSource.kind` and `TaintSink.kind`
  are copies of it and carry the same comment.

### 1. Axiom

> A row's kind says how the primitive is reached from the row's own `module`:
> **`method`** iff it is called on an **instance** of `module`;
> **`function`** iff it is called on `module` **itself** (a namespace, package,
> type, companion object or named global) or has no owner;
> **`attribute`** iff it is **read** on `module`, not called.

**Decision procedure.** A contributor applies this from the language's own
documentation, without running any analyzer:
- If `module.name(...)`, in the language's own qualifier syntax
  (`module(...)` for a constructor), is a valid call as written, the row is a
  function.
- If a value of type `module` must precede `.name`, it is a method.
- If it is read rather than called, it is an attribute.
- If none of these holds, the `module` string is wrong.

Every language marks the answer in its own documentation:
- Javadoc says `static`.
- ObjC writes `+` for a class method and `-` for an instance method.
- Go declares a receiver clause.
- Rust writes `self`.
- Apple's docs label "Type Method", "Instance Method" and "Initializer".
- Scala documents each `object` on its own page.

**Stated against the row, not the call site.** ADR-0051 §1 says the sibling
field, the module key, "is not a property of the **call site**". A kind defined
in call-site terms would reintroduce that leak one field over.
`FileManager.default.fileExists(...)` and `let fm = …; fm.fileExists(...)`
reach the same row in two ways. The row is one fact, and it is a method row
keyed on `FileManager`.

**Never a property of an analyzer.** `_CATALOG_PARENTS` gives kotlin and scala
java's rows, and typescript loads the javascript YAML. A row keyed to one
analyzer's stamp is wrong for every analyzer that inherits it. The WI-komun →
WI-kikar sequence was that failure: an analyzer changed, and a row's kind
became wrong without the row being touched.

**Named globals and module-level objects.** This ADR does not rule on
ADR-0051's pending `global_object` notion (`process`, `window`,
`ctypes.cdll`). It rules only that a primitive called on such an owner *by
name* is a function, because `process.on(...)` is a valid call as written.

### 2. Consumers go through three predicates

- `reached_through_an_instance(kind)`
- `called_on_a_named_owner(kind)`
- `read_not_called(kind)`

Producers use `kind_for_yaml_section` or the `KIND_*` constants. The 18 literal
sites in `io_boundary.py`, `verify_claims.py`, `analyzer_disclosure.py` and
`taint.py` were converted with no behaviour change. Named for the axiom, each
call site now reads as what it assumes. The INV-nizom arm now reads "drop rows
**called on a named owner** when the stamp says `method`". That is precisely the
construct-as-receiver-evidence assumption INV-pimir records.

### 3. A shrink-only ledger of the rows the axiom rejects

`KNOWN_NONCONFORMING_ROWS` lists 91 shipped rows that break the axiom and are
not re-kinded here. Each names the tracker item that blocks it:
- **34 Java statics:** INV-pimir, WI-kilap.
- **11 Scala `object` members:** WI-kilap.
- **Scala `Process.apply`:** WI-narij.
- **16 Swift constructors:** INV-gujoh.
- **3 Swift statics, 21 ObjC class methods and 5 `kotlin.io.FilesKt` rows:**
  WI-ziviv. No kind fits the FilesKt rows, because their module string is wrong.

The test fails on an entry whose row has been fixed or removed, and pins the
size. So the ledger can only shrink, and a new exception is a visible diff. It
records non-conformance; it does not approve it.

**This ADR moves no row.** The order is measured, and it is recorded on INV-zikab:
1. The unconstrained rows went first (INV-dihun, PR #1155).
2. The Swift boundaries are adjudicated before their kinds.
3. The INV-nizom arm stops reading `method` as receiver evidence before the
   Java statics move.
4. The Kotlin and Scala qualifier drop (WI-kilap) is fixed before those rows
   lose their boundary-scoped disclosure.

## Consequences

### Positive

- One sentence decides a row, and a reviewer can check it against the language's
  documentation. Overlay authors (ADR-0047) no longer have to know what each
  analyzer emits.
- A kind literal in the four consumer modules fails pre-commit and CI. The next
  consumer has to say, in the axiom's terms, which fact it relies on.
- An unregistered section key (`method:` for `methods:`) fails a test. Before
  this, the loader skipped it and its names vanished.

### Negative, and stated plainly

**Conformance is checked mechanically for two languages only.**
- **Python:** `inspect.getattr_static` over every method row whose module
  resolves to a class or an object.
- **Swift:** an UpperCamelCase name under `methods:` is a constructor and must
  be in the ledger.

For every other language the ledger is the only record. A new non-conforming
Java or ObjC row passes every test, because the suite has no JVM, Swift or ObjC
toolchain. A per-language docs-derived check is the natural next step. It is not
built here.

**The literal scanner covers four modules and one access shape.** It catches a
kind literal compared with `x.kind` or `getattr(x, "kind", …)`, a literal
membership test, and a `kind=` literal passed to `IoPrimitive`, `TaintSource`
or `TaintSink`. It does not catch `kind == "method"` on a bare local name, or a
consumer in a fifth module. Both limits are stated in its controls.

### What would reopen this

- After the INV-nizom arm stops reading the stamp, a 21-survey A/B still shows
  the Java re-kind losing classifications. That would mean some consumer
  genuinely needs the analyzer's stamp copied into the row.
- A row is reached both ways (`Path.read_text(p)` unbound, `obj.staticMethod()`)
  on at least 1% of its matched call sites. That would need a fourth value or
  two fields.
- A consumer is shown to need "member of a type" regardless of whether the
  member is static.
