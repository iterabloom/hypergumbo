<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: Rust stdlib module I/O enumeration

**Date:** 2026-08-27 · **Status:** Complete — 20 granted, 13 refused, of 33 audited
· **Informed:** WI-lutuh (the grants), WI-bupor / WI-gihos / WI-dadog (the rows
they rest on), WI-dukom (the probe fixes), WI-pavob (the two vocabulary rulings)

## Why this is a survey, not an audit-findings doc

It applies an existing method — the python stdlib enumeration, and the evidence
bar that audit established — to a new scope, and emits a per-module verdict
table. No new decision is taken here. The two decisions this audit depends on
(is a futex a boundary; is a clock read a boundary) were taken on WI-pavob and
are cited, not re-argued.

## Context

`module_completeness: complete` asserts that **every** I/O primitive a module
exposes is catalogued. That is what lets a consumer read silence as an
*examined negative* rather than "none I could see". Rust declared **zero** of
them, so no Rust module could ever be an examined negative and every clean Rust
boundary verdict was withheld — WI-lutuh's statement.

A grant is the dangerous direction. A wrong one manufactures a false all-clear
in a security tool. The bar comes from the python audit, whose unaided first
draft got **5 of 32 verdicts wrong and every error on the false-all-clear
side**. Reading is not the bar.

## The rule applied

- **Total surface, not the subset this repo happens to call.** Catalogue
  presence is not catalogue coverage: `std::fs` sat in the catalogue at roughly
  half its surface while never appearing on any "unexamined module" list.
- **Exact matching.** `std::collections` does not vouch for
  `std::collections::HashMap`. Every audited module is declared on its own line
  and no grant is inferred from any other.
- **Refuse by default.** A module that cannot be resolved is refused, not
  skipped.
- **The boundary test is the owner's:** *is it changing state outside of
  itself* — and where that is contested, WI-pavob's recorded tie-breaker is the
  **data-crossing** reading over the asks-the-kernel-for-something reading.

## The probe, and the seven bugs found in it

A mechanical probe over the installed toolchain's `library/` scans each module
for syscall REACH (`sys::`, `libc::`, `RawFd`, …) and I/O-bearing SURFACE
(`File`, `Command`, `read_dir`, …). It **never grants** — any signal refuses,
and a candidate still needs adjudication with the source in hand.

Seven bugs were found in the probe itself. **Every one produced a plausible
wrong answer and none reported an error**; each was caught by disbelieving an
implausible intermediate.

| # | Bug | Direction |
|---|---|---|
| 1 | A directory module resolved to `mod.rs` alone (`is_file()` preceded `is_dir()`) | false all-clear |
| 2 | Bare `std` resolved to `lib.rs` alone and came back CANDIDATE | false all-clear |
| 3 | Trait methods are not `pub fn`, and a generic list precedes the paren | under-report |
| 4 | By-value receivers (`fn f(mut self)`) read as associated functions — a wrong *kind* is unmatchable | under-report |
| 5 | A module can be a file **and** a directory: `std/src/fs.rs` beside `std/src/fs/tests.rs`, and `is_dir()` fired first, so `std::fs` and `std::process` were adjudicated **against their own test files** | false all-clear |
| 6 | **A type is not a file** — wrong in both directions: too big for `ErrorKind` (refused for a sibling declaration), too small for `SocketAddr` (defined in core, impls in std) | both |
| 7 | `crate::` resolved to `std` regardless of the importing file's crate, so core-resident modules were checked against std's tree | false refusal |

Bugs 6 and 7 are the same shape as the one that made the probe resolve
`SystemTime` to a HermitOS shim: **taking the first grep hit in sort order is
not a resolution.** Platform and target trees (`/src/sys/`, `/src/os/`) are now
excluded generally rather than one prefix per bug, and survivors are ranked by
module-path agreement.

Three of the fixes *narrow* what is scanned, which is the false-all-clear
direction, so each was applied alone and measured across all 33 modules against
15 must-refuse and 6 must-candidate controls. **That control set is why two of
the fixes exist**: scoping a type to its own items alone turned
`std::time::SystemTime` (reaches the clock through `use crate::sys::{.., time}`)
and `std::path::PathBuf` (derefs to `Path`, so `pb.read_dir()` is a PathBuf call
site) into grantable candidates. Both are I/O; both would have been granted.

## The finding the probe could not have made

`std::collections::HashMap` came back with **no signal**, yet `HashMap::new()`
returns `HashMap<K, V, RandomState>`, and `RandomState`'s initialiser calls
`crate::sys::random::hashmap_random_keys` — an OS entropy read. It is invisible
to a text scan of `map.rs` because `RandomState` is defined in another module.

**A default type parameter can carry a boundary.** That is the same shape as
`Deref`: the module's own text is clean and the reach arrives through a name it
imported. The probe's own docstring predicted the class ("a re-export can carry
I/O in from a module whose own text is clean"); this is the instance.

It did **not** cost the grant, and the reason is cross-language consistency
rather than judgement. The shipped python catalogue grants `secrets`, `random`
**and** `uuid` — all three draw on the OS RNG — on the recorded reasoning that
OS-RNG seeding belongs to the row of the module that *performs* it
(`os.urandom`), not to the surface of the module that consumes it. `os.getrandom`
was removed from `env_read` outright with *"a CSPRNG read is not an environment
read under any reading"*, so this vocabulary has **no kind for entropy at all**.
Refusing rust's hash collections on a read python grants would make
`module_completeness` mean different things per language — the failure mode the
axis discipline exists to prevent.

## Verdict: GRANTED — 20 modules

| Module | Evidence |
|---|---|
| `std::borrow::Cow` | clone-on-write value wrapper |
| `std::cmp` | ordering and comparison only |
| `std::collections` | container re-exports; RandomState reach only |
| `std::collections::BTreeSet` | ordered set, no hasher |
| `std::collections::HashMap` | RandomState entropy; python precedent |
| `std::collections::HashSet` | as HashMap |
| `std::collections::hash_map::Entry` | entropy read belongs to map construction |
| `std::ffi::CStr` | borrowed NUL-terminated view |
| `std::io::ErrorKind` | plain enum: **zero** pub fns |
| `std::iter` | lazy adapters over whatever they wrap |
| `std::mem` | size/align/swap/replace |
| `std::net::IpAddr` | naming an address is not contacting it |
| `std::net::SocketAddr` | 7 value accessors; its only std impl is the identity |
| `std::ptr` | `ptr::metadata` is fat-pointer metadata |
| `std::slice` | over-broad net finds nothing |
| `std::sync` | WI-pavob: thread contention moves no data |
| `std::sync::Arc` | atomic refcount |
| `std::sync::Mutex` | WI-pavob |
| `std::sync::mpsc` | intra-process, so not `ipc_recv` |
| `std::time::Duration` | pure arithmetic, defined in core |

## Verdict: REFUSED — 13 modules, and why each earns it

| Module | Why |
|---|---|
| `std` | granting the whole stdlib turns every unmatched call into an examined negative — the single most dangerous permit available here |
| `std::env` | `env_read` / `env_write` surface |
| `std::fs` | the filesystem surface |
| `std::io` | the I/O module |
| `std::io::BufReader` | implements `Read`; delegated I/O is still I/O |
| `std::os::windows::fs` | platform filesystem extensions |
| `std::path` · `std::path::Path` | `canonicalize` / `fs::metadata` / `read_dir` |
| `std::path::PathBuf` | **derefs to `Path`**, inheriting its whole surface |
| `std::process` | subprocess surface |
| `std::thread` | reaches `libc` / `sys` |
| `std::time` · `std::time::SystemTime` | WI-pavob ruled a clock read **is** `host_info_read`, so these need **rows**, not a no-I/O declaration. The rows are WI-tubij's cross-language scope. |

Note on `std::ptr`: it publicly exports `read`, `write`, `copy` and
`read_volatile` — names colliding exactly with I/O verbs. Granting it is
*protective* as well as correct: it makes those calls an examined negative
rather than leaving the spelling to be guessed at.

## What this audit does NOT do

- It does not grant `std`, and no future audit should without a separate
  argument.
- It does not touch third-party crates. Catalogues are stdlib-scoped by standing
  ruling; third-party surfaces belong in overlays.
- It does not settle whether an **entropy read** is a boundary. It records that
  this vocabulary has no kind for one, and follows python's precedent. If a kind
  is ever added, four grants here (`collections`, `HashMap`, `HashSet`,
  `hash_map::Entry`) must be revisited — pinned by a test, not by this sentence.
- It does not enumerate `std::time`'s rows. That is WI-tubij, and its window
  closes when baseline measurements start.
