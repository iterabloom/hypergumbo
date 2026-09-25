<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# I/O primitive kind-conformance sweep (INV-nular)

**Question.** INV-nular's invariant is that a primitive's KIND must be checked
against semantics rather than asserted by name. Rust was found to violate it in
seven places, and the cost was not a mislabelled finding — it was a **withheld
verdict on every repository that opens a file**. This sweeps the same question
across every shipped catalogue.

**Scope.** 947 `method`-kind entries across 9 languages. `kind: method` asserts
that a call site spells the primitive `receiver.name(...)`. Where that is false
— the callee is an associated function, a static method, a classmethod, or a
module-level function — two things follow, and the second is the severe one:

1. `io_boundary`'s method-kind gate can never match the call site, so the entry
   is unreachable **by construction**; and
2. `verify_claims.method_starved_modules` sees a resolved call into a
   method-keyed module with no method-construct edge, concludes the analysis
   "did not look", and **withholds the verdict**.

## Method, and what it cannot do

Three tiers, because a mechanical probe exists for only some languages. WI-lutuh's
scoping measured why that matters: the Python stdlib audit's first draft, written
from expert knowledge unaided, got **5 of 32 verdicts wrong (16%), every error on
the false-all-clear side**. Recall is not evidence.

| tier | languages | instrument |
|---|---|---|
| **Mechanically verified** | python, rust | `importlib` + `inspect.getattr_static` (python); `rust-src` + associated-fn semantics (rust) |
| **Flagged, not verified** | javascript | catalogue-internal consistency only |
| **REFUSED** | go, java, kotlin, scala, swift, objc | no toolchain on this platform |

**726 of the 947 entries are in the refused tier.** They are not adjudicated
here and must not be read as clean.

## Tier 1 — mechanically verified

### rust — 7 miskinds, all corrected

Every one is an **associated function** (no receiver) declared `methods:`.

| module | name | was | is |
|---|---|---|---|
| `std::fs::File` | `open` | method | function |
| `std::fs::File` | `create`, `create_new` | method | function |
| `std::net::TcpStream` | `connect` | method | function |
| `std::net::TcpListener` | `bind` | method | function |
| `std::net::UdpSocket` | `bind` | method | function |
| `std::process::Command` | `new` | method | function |

**Measured live** on `encrypted-dns-server` (rust+toml only): the analyzer
resolves the call site perfectly — `rust:std::fs::File:0-0:open:external_symbol`,
module and name both correct — and all 7 generic claims returned `inconclusive`
naming `std::fs::File` structurally invisible. Re-kinding `std::fs::File` alone
dropped the starved list from **two modules to one** on the same binary.

**The fix is a SPLIT, not a move.** `TcpStream::connect` is an associated
function while `TcpStream::write_all` is a method, under the same module.
`OpenOptions` is the sharpest case and is deliberately untouched:
`OpenOptions::new()` is an associated function but `.open(path)` is called on
the builder, so one module legitimately carries both kinds.

### python — 2 miskinds, 95 clean, 46 refused

| module | name | boundary | why |
|---|---|---|---|
| `pathlib.Path` | `cwd` | `host_info_read` | `classmethod` — called on the class |
| `pathlib.Path` | `home` | `host_info_read` | `classmethod` — called on the class |

Less severe than rust's: `pathlib.Path` also carries genuine methods
(`exists`, `read_text`, …), so a repo calling any of those marks the module
satisfied and no withholding occurs. The entries are still unmatchable.

**The 46 refusals are honest, not skipped:** 8 `file.*` pseudo-module entries
(file objects have no importable type), 8 `multiprocessing.Queue`/`Pipe`
(factory functions, not classes), 29 `django.db.models.*` and 1 `flask.Flask.run`
(third-party, not installed). None is counted as OK.

## Tier 2 — javascript, flagged only

`method_starved_modules` **abstains** for JavaScript (INV-gijis: the analyzer
stamps `call_construct` on zero call edges), so neither flag can withhold a
verdict. Both are undocumented, and both are the shape of a miskind:

- **`fs.createWriteStream`** is the sole `method` among **33** `fs`
  function-kind siblings. `fs.createWriteStream(...)` is a module-level call
  like `fs.writeFile(...)`. Consequence if wrong: a missed `fs_write`
  boundary — a false negative, not a withheld verdict.
- **`Deno.{readFile, readTextFile, readDir, stat, lstat}`** are declared BOTH
  function and method, in adjacent entries. The function entry carries a note;
  the method entry carries none. A duplicate declaration is harmless
  belt-and-braces — whichever kind the analyzer emits, one matches — but it is
  undocumented and inconsistent.

Neither is changed here. Both want a JS-semantics check this sweep did not run.

## Tier 3 — REFUSED

| language | method-kind entries |
|---|---:|
| kotlin | 181 |
| scala | 162 |
| java | 139 |
| objc | 122 |
| swift | 97 |
| go | 25 |
| **total refused** | **726** |

No toolchain on this platform, so no mechanical check. **INV-suril already
records the shape in java** — `java.nio.file.Files` static methods catalogued
`kind=method` — which is direct evidence the class extends past rust and python
into at least one refused language. That item and INV-nular are plausibly one
defect in two languages, and this sweep does not settle it.

## A language-agnostic signal for the refused tier

Two checks computable from the catalogues alone, so they reach languages a probe
cannot. **Neither is a verdict** — both are ranked candidate pools.

**Signal 1 — split modules** (both kinds under one module). Often legitimate
(`scala.io.Source`: `fromFile` is a factory, `getLines` is a method; rust's four
corrected splits). It is how rust's defect would have been visible *without
reading Rust*, and it is empty for every refused language — which is itself
informative: those catalogues never split a module, so a partial miskind there
would leave no trace in this signal.

**Signal 2 — cross-language name disagreement.** 97 names carry different kinds
in different languages. Most inspected cases are genuine language differences
(`os.path.exists` is a function in Python, `File.exists` a method in Java). One
row is worth a look in the OPPOSITE direction: `flush` is function-kind for
`python:sys.stdout` / `sys.stderr`, where `sys.stdout.flush()` is a method on
an object. A method declared as a function over-matches rather than under-matches
— the false-positive direction, not this sweep's subject, and not pursued here.

## What this does not support

- **Not a clean bill for 726 entries.** Six languages are unexamined.
- **Not a claim that the rust fix clears rust.** `encrypted-dns-server` still
  returns `inconclusive`, now on `std::path::Path` alone — a repo that never
  calls a `Path` method and touches the module only via `Path::new` and two
  type bounds. That is a separate defect in `method_starved_modules`, filed
  rather than fixed because it *loosens* a withholding gate.
- **The python probe checked one direction only** — method-kind entries that
  cannot take a receiver. Function-kind entries that actually require one were
  not swept.
