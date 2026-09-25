<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: scoping the stdlib `module_completeness` audit (WI-lutuh)

**Date.** 2026-08-24. **Tree.** `dev @dfe3ba8b3e`.
**Asked for.** Three things: (1) the real DENOMINATOR — stdlib modules the
corpus actually REACHES, not the size of each stdlib; (2) the EVIDENCE BAR for
one dated `module_completeness` entry; (3) WHICH LANGUAGES PAY OFF FIRST.
**Artifacts.** `~/hypergumbo_lab_notebook/lutuh_scope_08242026/` — sample,
sweep script, per-repo caches and verdicts, `raw.json`, and the scoring scripts.

## Why this is a survey, not an audit-findings doc

Per `docs/audits/README.md` §Scope, `docs/audits/` records per-value verdicts on
a **declared axis** in the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy.
This document's values are stdlib *modules* and its output is a scoping
recommendation, not axis verdicts. Same carve-out as
`python-stdlib-module-io-enumeration.md`, which it extends.

## Method, and its honest limits

475 corpus repos were classified by top-level manifest (`go.mod`, `Cargo.toml`,
`package.json`, …), then a **language-stratified sample of 55** was run through
the shipped `verify-claims` and each repo's uncatalogued-module list recomputed
from its own survey using the gate's own functions
(`_uncatalogued_external_modules`, `_module_from_symbol_path`) rather than a
reimplementation.

**42 of 55 produced a survey. 13 did not — 12 hit the 420 s timeout and 1 was
killed.** The excluded set is not random: it is the LARGEST repos (v8,
qemu-edk2, opensearch, …), which reach MORE modules, not fewer. **Every count
below is therefore a LOWER BOUND.**

**A module string can be reached by more than one language** (vmaf reaches
`math` from both cpp and python) and the gate's `unknown` is a `set[str]` with
no language attached, so language attribution is a reconstruction: a module is
credited to every language whose edges reached it. 71 of 3,905 entries (1.8%)
are multi-language.

## The property that governs everything below

`BoundaryCoverage.qualifying_only` is `not unknown` — verified in code at
`verify_claims.py:2960`, not taken from a docstring. The uncatalogued list must
be **exactly empty** for a verdict to stop being withheld.

So **the payoff unit is a REPO REACHING ZERO, not a module enumerated.**
Enumerating 90% of the stdlib modules a repo reaches buys nothing at all. The
`tomllib` addendum to the python survey is the same property observed from the
other side: one call into one unenumerated module moved all 18 self-claims from
`confirmed_with_caveats` to `inconclusive`.

## Deliverable (1) — the denominator

Distinct stdlib modules REACHED by the sample, per language. Lower bounds.

| lang | repos | stdlib modules reached | third-party | producer artifact |
|---|---:|---:|---:|---:|
| objc | 4 | 127 ‡ | 249 ‡ | 0 |
| haskell | 4 | 69 ‡ | 38 ‡ | 0 |
| go | 3 | 53 | 97 | 1 |
| python | 16 | 53 | 141 | 0 |
| erlang | 4 | 38 ‡ | 158 ‡ | 0 |
| java | 3 | 38 ‡ | 71 ‡ | 0 |
| rust | 5 | 33 | 52 | 0 |
| elixir | 2 | 32 ‡ | 72 ‡ | 0 |
| cpp | 6 | 29 | 4 | **629** † |
| javascript | 13 | 13 | 98 | 0 |
| scala | 3 | 13 ‡ | 92 ‡ | 0 |
| swift | 4 | 2 ‡ | 417 ‡ | 0 |

† cpp's 629 "modules" are comma-joined `#include` lists, not module names —
**INV-zimud**. See "the cpp blocker" below.

‡ **UNRELIABLE — see "where the classifier fails" below.** Only `python`,
`javascript`, `go` and `rust` have a principled stdlib test.

The denominator is **tens of modules per language, not thousands**. Rust — the
language WI-lutuh was filed about — reaches **33**. That is a tractable audit.

### The cpp blocker, and its already-satisfied sibling

629 of cpp's gate entries are not module names at all. The path slot of a C++
external dst is the calling file's ENTIRE comma-joined `#include` list:

```
cpp:string,sys/socket.h,ws2tcpip.h:0-0:get_socket_address:external_symbol
```

Measured: 19,273 of 81,711 c+cpp external dsts (23.6%) carry such a name —
libzmq 21.7%, plasma-desktop 40.3%, shaka-packager 29.5%. **C is clean at 0%**
in all three C repos sampled, so this is cpp-specific. No `module_completeness`
entry can ever match a synthetic name like that.

**This is the residual of INV-funuf, not a rediscovery of it.** INV-funuf
(`satisfied`, P1) covered the CLASSIFICATION path — `lookup_with_module` — and
its fix, `io_boundary._module_hint_candidates`, does split the disjunction and
strip header suffixes. Its statement holds and it stays satisfied. But that
function has exactly ONE call site in the tree (`io_boundary.py:916`), and the
COVERAGE GATE does not use it: `_uncatalogued_external_modules` hands the raw
string straight to `module_io_is_enumerated`. Filed as **INV-zimud** (P1) with
a distinct statement — the same producer contract, the second unserved consumer.

## Deliverable (2) — the evidence bar

Extracted from the only worked precedent (`python.yaml`'s 76 entries and
`python-stdlib-module-io-enumeration.md`). Schema:

```yaml
- module: ast
  completeness: complete
  retrieved: '2026-08-15'
  notes: 'Parses source STRINGS; ast.parse never opens a path.'
```

1. **Total-surface rule.** `complete` only if EVERY I/O surface the module
   exposes is absent or already carries a catalogue row — the whole public
   surface, not the functions this repo happens to call.
2. **Exact matching.** `urllib` does not vouch for `urllib.request`; `pathlib`
   does not vouch for `pathlib.Path`. One line per audited module.
3. **File-object rule.** Taking a caller-opened object is not I/O
   (`json.load(fp)`, `tomllib.load(fp)`); opening a path is (`gzip.open`,
   `ET.parse`).
4. **Vocabulary traps.** `logging` covers stdout/stderr, so a module that merely
   PRINTS does I/O (this disqualified `argparse`, `warnings`). `env_read` covers
   `os.environ`/`sys.argv` (this disqualified `os.path`, via `expanduser`).
5. **Refuse by default.** The gate's wrong answer is a FALSE ALL-CLEAR on a
   security claim.

**The bar is not met by reading.** The python audit's first draft was written
from knowledge of the modules and **five of its 32 verdicts were wrong** —
`pathlib`, `typing` (`reveal_type` → stderr), `base64` (`main` opens files),
`shlex` (reads `sys.stdin`), `contextlib` (`chdir`). A **16% error rate for
expert reading unaided, on the auditor's best language, with every error on the
false-all-clear side.** They were caught by a mechanical probe over each
module's own-defined public callables.

**This is what makes deliverable (3) a toolchain question**, because the probe
is Python-specific (`importlib` + `inspect.getsource`) and does not port:

| mechanism available locally | languages |
|---|---|
| runtime introspection (the precedent's method) | **python** (done), **javascript** — `require('module').builtinModules`, 68 builtins, exports enumerable |
| source available locally | **rust** — `rust-src` installed under the toolchain sysroot; **c/cpp** — headers + gcc |
| **nothing local, offline** | go, java, kotlin, swift, scala, haskell, elixir, erlang, objc |

`objc` is the extreme case: its "stdlib" is Foundation/Cocoa, Apple frameworks
that do not exist on this platform at all.

## Deliverable (3) — which languages pay off first

### The finding that reframes the question

Whole-repo flippability, 42 analysed repos. A repo is flippable by WI-lutuh
**alone** only if its list is entirely stdlib — `io_primitives` stays
stdlib-scoped by standing ruling, so any third-party entry needs a per-project
overlay as well.

| bucket | repos |
|---|---:|
| NEEDS_OVERLAY_TOO (third-party / global present) | **28** |
| BLOCKED_BY_ARTIFACT (producer defect first) | **10** |
| ALREADY_ZERO | 2 |
| **STDLIB_ONLY — WI-lutuh alone flips it** | **2** |

Restricted to the four languages with a reliable classifier (10 eligible
repos): 7 need an overlay, 2 already zero, **1** is stdlib-only.

**Completing the stdlib audit for every language would take roughly 1 corpus
repo in 10 to a clean verdict.** WI-lutuh is necessary and is not sufficient;
the dominant blocker is third-party coverage, which the shipped catalogue
cannot address by design.

### The control that proves it

**Python is the one language whose audit is DONE** — 76 `module_completeness`
entries. If enumeration were sufficient, python repos would be at zero. Across
16 sampled repos python contributes **243 uncatalogued entries: 75 stdlib and
168 third-party**, and only **4 of 16** are stdlib-only — and those four
(`ejabberd`, `fuse-overlayfs`, `git`, `notation`) are repos where python is
incidental, contributing 1–9 entries. The real python projects are
third-party-dominated: h5py 27, polis 54, vmaf 37.

**53 distinct stdlib modules remain unenumerated after the 76-entry audit**,
including `enum`, `string`, `operator`, `decimal`, `codecs`, `errno`, `glob`,
`struct`, `traceback`, `queue`, `threading`, `socket`, `ssl`, `subprocess.Popen`.
Some are the *slot-mismatch family* the python survey already filed as a defect
rather than catalogue work (`sys.stdout.buffer`, `io.StringIO`, `queue.Queue`,
`threading.Event`, `typing.Dict`) — attribute and type paths a module-keyed
entry cannot reach.

### The curve — partial work pays zero

Enumerate the K most widely-reached stdlib modules; how many stdlib-only repos
reach zero?

```
  python   K=5:0/4  K=10:2/4  K=20:3/4  K=40:3/4  K=53:4/4   (distinct=53)
  rust     K=5:0/1  K=10:0/1  K=20:1/1  K=40:1/1  K=33:1/1   (distinct=33)
  erlang   K=5:0/1  K=10:0/1  K=20:1/1  K=40:1/1  K=38:1/1   (distinct=38)
  objc     K=5:1/1  K=10:1/1  K=20:1/1  K=40:1/1  K=127:1/1  (distinct=127)
```

The knife-edge is visible: rust and erlang buy **nothing** at K=10 and
everything at K=20–33.

### Where the classifier fails, and why that is itself a finding

For **java, swift, objc** — and partly **scala** — the module slot frequently
carries a **bare type name**, not a module path:

```
  java    System                      alongside javax.xml.parsers.DocumentBuilderFactory
  swift   Bundle, String, Bool, $0    ($0 is a CLOSURE SHORTHAND, not a module)
  objc    NSData, NSError, NSLock     Foundation CLASSES
  scala   cats.parse.Parser.char      a METHOD path
```

Swift's variable-name-in-module-slot half is already filed as **INV-kotob**
(`violated`, P3) — this survey confirms it at corpus scale rather than
rediscovering it.

Hand-maintained stdlib name sets got these wrong on the first pass —
`erl_parse`, `gb_trees`, `beam_lib`, `System`, `Bundle` were all misfiled as
third-party — which is why those rows are marked ‡ rather than quietly
included. Type-granularity entries ARE legitimate in the schema (python
declares `re.Match`, `sqlite3.Connection`, `typing.Any`), but objc reaching 127
distinct such names with no module grouping is a different proposition from
rust's 33 modules, and swift's `$0` is not adjudicable at all.

### Recommended order

Ranked by (flippable repos) ÷ (modules to enumerate), gated by whether the
evidence bar can be met at all on this platform.

| rank | language | corpus repos | modules | bar meetable? | note |
|---|---|---:|---:|---|---|
| 1 | **rust** | 83 | **33** | **yes** — `rust-src` local | WI-lutuh's own subject; smallest real denominator of the big three |
| 2 | **javascript** | 92 | **13** | **yes** — node builtins | cheapest denominator; but 0/13 repos stdlib-only, and `document`/`process` globals are a separate class |
| 3 | **python** | 36 | 53 left | yes — done before | long tail; several entries are the slot-mismatch defect, not catalogue work |
| 4 | **go** | 88 | 53 | **no toolchain** | largest presence, but the bar needs GOROOT source |
| — | **cpp** | 18 | — | **BLOCKED** | INV-zimud: the gate never expands the comma-joined `#include` disjunction. Do not start here |
| — | java/swift/objc/scala | 52 | — | **decide first** | module slot carries TYPES; `module_completeness` may be the wrong instrument |

## What this survey does NOT do

**It does not decide the order.** It is a scoping input; the ranking above is a
recommendation with its inputs shown.

**It does not measure the third-party path.** The 28 NEEDS_OVERLAY_TOO repos
are counted, not costed. Whether per-project overlays are a viable answer at
corpus scale is unmeasured here.

**It does not re-audit python's 76 entries.**

**Its denominators are lower bounds** — the 13 largest sampled repos are
missing, and 4 of 14 languages carry an unreliable classifier.
