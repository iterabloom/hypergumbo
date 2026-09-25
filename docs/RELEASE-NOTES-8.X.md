<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Release notes — hypergumbo 8.x

This file is the user-facing view of what's in each 8.x release. The
[CHANGELOG.md](../CHANGELOG.md) remains the implementer-facing log
(every Added / Changed / Fixed entry, every internal refactor, every
test pin). When you upgrade, read here first; consult the changelog
if you need the implementation detail behind a given change.

One file exists per major version line. Previous lines:
[7.x](RELEASE-NOTES-7.X.md) · [6.x](RELEASE-NOTES-6.X.md) ·
[5.x](RELEASE-NOTES-5.X.md).

---

## TL;DR

**8.0.0 makes `verify-claims` say only what it actually checked.** Five
channels through which a claim could come back `confirmed` without the tool
having looked are closed, and a fourth verdict — `confirmed_with_caveats`,
**exit code 3** — now carries the cases that used to pass silently.

**Three things will break a working setup. Act on these:**

1. **A CI gate written `verify-claims … || exit 1` now fails where it passed.**
   Exit code 3 is new. Decide deliberately whether a caveated verdict should
   gate your pipeline, and branch on the code rather than on truthiness.
2. **`VERIFY_CLAIMS_SCHEMA_VERSION` 1.1 → 2.0** — the one non-additive bump
   this cycle. A consumer that assumed three verdict values will meet a fourth.
3. **`Edge.quality` is gone**, its one-version deprecation window having
   elapsed. Read `confidence` + `confidence_source` + `is_resolved` instead.

**Two fixes you want even if you change nothing else:**

- **Analysing a repository executed code from that repository.**
  `hypergumbo io-boundaries <hostile-repo>` ran an attacker-supplied program
  **6 times, as the invoking user, at exit 0, silently.** Upgrade.
- **`hypergumbo .` crashed and wrote a zero-byte sketch whenever the embedding
  weights were absent** — the documented quick-start was the one command that
  required the network. It no longer does.

**And two you will notice immediately:** a cold `survey` drops **517.7s →
220.0s**, and the results cache is now **bounded** (default 5 GiB, evicted
least-recently-used and zipped rather than deleted) — it was measured at
**5.3 GB in 27 entries for a single repository**, all created the same day.

`SCHEMA_VERSION` advances 0.19.0 → 0.20.1.

**8.1.0 makes the call graph and the I/O boundaries more exact, and makes
records say what they do not know.** No output field is removed or renamed,
but four things change what you will see:

1. **`is_exported` can be `null`** (schema 0.20.12), meaning "not measured".
   About ninety analyzers have no exportedness rule and had reported every
   symbol as not exported. A reader that tests truthiness is unaffected; a
   reader that tells `false` from absent must now handle `null`.
2. **`dead-code-maybe` reports far less dead code**: 41.4% → 7.5% of this
   repository in the default view. Re-baseline anything tuned to the old
   numbers.
3. **More clean `verify-claims` runs exit 3.** A clean verdict now names the
   receivers it could not type, and that qualifies it. A gate that branches on
   the exit code, as 8.0.0 asked, needs no change.
4. **Some ids change**: Haskell functions (their span now covers every
   equation) and Solidity contract members (now `method`). Regenerate; do not
   diff across the boundary.

Also in 8.1.0: calls are credited to the function that contains them in 25
analyzers that had used a same-named one; method calls reach the I/O catalogue
through typed receivers in ten languages; an opt-in second Python backend
(scip-python) is merged with the first into one record per declaration; and
taint's useful precision rises **24.1% → 30.9%**.

`SCHEMA_VERSION` advances 0.20.1 → 0.20.13.

---

## Unreleased

> `SCHEMA_VERSION` 0.20.1 → 0.20.13, the `verify-claims --json` envelope
> (`VERIFY_CLAIMS_SCHEMA_VERSION`) 2.0 → 2.4, and
> `DEAD_CODE_MAYBE_SCHEMA_VERSION` 0.2.0 → 0.3.0. Every step is additive or
> widening for a reader of 8.0.0 output; see "For JSON consumers" for the one
> exception, which concerns enum values rather than fields.

### At a glance

- **A call is credited to the function that contains it.** 25 analyzers found
  a call's enclosing function by name, so overloads, redefinitions and
  same-named methods in different classes took each other's calls. They now
  use the declaration's position, and calls outside their caller's span go to
  zero (D 15,126, Elixir 7,166, Kotlin 2,014). A test fails on any analyzer
  that still anchors by name.
- **Method calls reach the I/O catalogue through typed receivers, in ten
  languages.** A call on a receiver the repository describes (an instance
  field, a parameter, a declared return type, a chained call) used to stop at
  the `external` sentinel. The Django ORM is typed end to end (+2,943 edges on
  pretix, 0 lost).
- **Two producers for one language give one record (ADR-0057).** scip-python
  is an opt-in second Python backend (`--backend scip-python`); it never runs
  the analysed code. When two backends see one declaration, their records
  merge into one node or edge, with new `attribution` and `alternatives`
  fields saying which producer said what. Agreement between them raises
  confidence to 0.95 (`confidence_source="corroborated"`).
  **`hypergumbo backend-agreement`** reports where they agree.
- **A call that only opens or registers a network crossing is no longer
  treated as the crossing (ADR-0049).** Starting a server or dialling a
  connection creates no taint source; the new `net_listen` boundary discloses
  it instead. Together with the fixes below, taint's **useful precision rises
  24.1% → 30.9%** (`docs/measurements/0012`).
- **Records say what they don't know.** `is_exported` can be `null`; a pass
  that produced nothing says why (`silence_reason`); a linker declares which
  passes it reads (`depends_on`) and what each edge was derived from
  (`derived_from`, where `[]` means "consumed no graph record").
- **User and project configuration (ADR-0045).** `config.toml` and
  `<repo>/.hypergumbo.toml` can add I/O primitives, and
  `hypergumbo init-catalogs` creates the user catalogue directories. Community
  catalogue overlays now ship in the wheel and load by default, announced on
  stderr; their rows cannot vouch for a verdict.

### For `verify-claims` users

- **More clean verdicts come back caveated at exit 3.** A clean verdict now
  names what it could not see: receivers the analysis could not type (with a
  count, a denominator and the method names), receivers of unknown scope, and
  languages that emit no external instance-method calls. It also says how much
  of the catalogue behind it is unverified. A corpus hunt over 32,593 files
  found 90 scopes reaching real I/O through an untyped receiver where the
  8.0.0 caveat fired on none. These disclosures qualify a verdict rather than
  withhold it, and a repository whose receivers are all typed still gets bare
  `confirmed` at exit 0.
- **A verdict reports the fidelity it was reached at** (envelope 2.2),
  telling a backend that is installed but disabled from one that is missing,
  and **whether the data-flow walk ran**, so `flows_removed_by_walk: 0` can be
  read. Each finding names the analysis that produced it (`analysis_method`)
  and what the walk concluded (`walk_verdict`, `walk_blocked_by`).
- **An unadjudicated flow is reported once per situation** (envelope 2.1):
  six repositories had reported 359 flows for 78 situations.
- **A catalogue row can declare that an argument only names the resource**
  (envelope 2.4). Tainted data choosing *which* file `os.Chmod` acts on still
  counts as a correct finding, but not a useful one.
- **Wrong answers fixed:**
  - `subprocess.Popen(tainted)` verified clean while `subprocess.run(tainted)`
    verified violated, because construction edges could not be walked. They
    now can.
  - One catalogue mistake had sent all 18 of hypergumbo's own self-claims to
    `inconclusive`.
  - An unresolved bare-name call no longer installs a sanitizer that is not
    there (a bare Java `doFinal(p)` had deleted findings). Every language now
    needs positive evidence of the receiver.
  - A sink that provably cannot receive the tainted value (a call passing
    only constants) no longer reports a flow: 24 of 34 adjudicated false
    positives were sinks taking no arguments at all.
  - Taint reaches JavaScript callbacks registered with `addEventListener`,
    `http.createServer` or `process.on`.
  - `verify-claims` no longer crashes with `RecursionError` on deeply nested
    code, where it had exited 1, the violated-claim code.
- **[`docs/VERIFY-CLAIMS-SCOPE.md`](VERIFY-CLAIMS-SCOPE.md) publishes what
  `verify-claims` cannot see**: every caveat kind, the exit-code contract, and
  the limits of a clean verdict.

### For JSON consumers

- **`is_exported` is `true`, `false` or `null`** (0.20.12). `null` means no
  analyzer measured it; only a positive input fills the field.
- **New fields, all additive:**
  - `Edge.meta` gains `call_arg_shape`, `callee_name`, the bash per-call-site
    keys and `io_target_kind` (0.20.2–0.20.5).
  - `TaintFlowFinding` gains `walk_verdict` and `walk_blocked_by` (0.20.6).
  - `AnalysisRun.silence_reason` and `limits.skipped_passes[].silence_reason`
    say why a pass was silent, on one closed axis (0.20.7–0.20.10). An absent
    reason means *not applicable*; `unreported` means *cannot determine*.
    (A `skip_reason_code` key existed briefly in development and never
    shipped.)
  - `Symbol` and `Edge` gain `attribution` and `alternatives`, present only
    when two producers' records were merged, so a one-producer artifact is
    byte-identical to before (0.20.11).
  - `derived_from` may be `[]` (0.20.13).
  - `dead-code-maybe` gains `cross_language_demoted` (its schema 0.3.0).
- **Enum values that moved** — the one non-additive change:
  - `call_construct` loses `remote_external` (Erlang) and
    `application_external` (Haskell), which differed from their unsuffixed
    siblings only by whether the callee resolved; read `dst`.
  - `call_construct: chained_return_type` (Go) moves to `resolution_quality`.
  - A C# method group moves from `call_construct` to `ref_construct`.
  - The I/O boundary `env_read` splits off `host_info_read`, so host and
    identity reads, and clock reads, no longer count as secrets.
- **Ids that change**: a Haskell function's span now covers all its equations
  (its id and `line_span` change), and Solidity contract members are `method`.
  **Ruby `Klass.new` now emits `instantiates`**, like the other eight
  analyzers that emit it, instead of `calls`. A name mentioned in JSON, YAML,
  TOML, XML or HTML no longer counts as cross-language dispatch for
  `dead-code-maybe`.

### For specific languages

- **Call edges that were missing entirely**: Python bare builtins and calls on
  an external-typed field; Go calls under a package-level `var` (every cobra
  `Run:` handler); Haskell zero-argument IO actions; Erlang `?LOG_*` macros;
  Rust grouped `use` lists; calls inside a Nim exported proc (nitter 648 →
  2,460 call edges). Swift and C# properties are back in the default map.
- **Wrong targets fixed**: in Scala and Kotlin an explicit import now outranks
  a same-named project symbol; a Nim call resolves only to a declaration its
  module can see (1,555 false edges gone); Elixir gives the same answer every
  run (surveys had differed by 101 edges) and no longer binds qualified calls
  by bare name; a Java wildcard import no longer turned `System` into
  `java.io.System`; method-call recovery no longer overrides a producer that
  named the module (precision 51% → 73%).
- **The GraphQL linkers emit edges** (apollo-server 0 → 128).
- **Rust**: the rust-analyzer backend's records are accounted for (1,308
  validation violations → 2); function-local `let` bindings are no longer
  nodes; symbol kinds come from the producer's declaration, so the two Rust
  backends agree on kind for 133 of 148 paired records.
- **Parsers**: tree-sitter-swift 0.0.1 → 0.7.3 (parses `#if` inside a type
  body); Objective-C files that fail to parse go 74 → 38.
- **The I/O catalogues** are corrected for direction and family in all
  fifteen languages. Highlights: a C socket call is classified by its address
  family, so an `AF_UNIX` channel is not network egress; standard input is
  `ipc_recv` everywhere; a request builder (`urllib.request.Request`,
  `http.NewRequest`) is no longer the crossing, the call that sends it is;
  Go's `http.DefaultClient` and friends reach their boundary. The shipped
  catalogues are stdlib-only.

### Security and runtime safety

- **`--backend tree-sitter` now actually disables the rust-analyzer backend**,
  which runs the analysed crate's `build.rs`. With
  the environment variable exported, opting out for one untrusted repository
  had still run it. A repository's own `.hypergumbo.toml` cannot grant itself
  that backend; `hypergumbo trust-backend` grants it per repository.
- **Two of hypergumbo's own filesystem writes** (`gitleaks.py` and the
  rust-analyzer probe) now go through the safety-zone wrapper its claims
  assert, as does `backend-agreement --out`.
- **A survey that raises no longer leaves its file index behind.**

### Performance

- **pretix's Python analysis goes 1700 s → 95 s** (17.9×, byte-identical
  output): the symbol suffix index is built once per registry instead of once
  per resolver.

### Known limitations

- **Taint precision remains the headline problem.** Useful precision is
  30.9% on the measured 16-repository frame; treat taint findings as leads to
  adjudicate. Widening the walk to construction edges was measured on its own
  and added 35 flows, 1 of them a true positive (`docs/measurements/0003`).
- **`untrusted_input` does not tell far-side-*authored* values from
  far-side-*chosen* ones**; this is declared, not fixed.
- **JavaScript arrow functions and function expressions do not reach the
  data-flow graph** (class methods now do), and C# accessors carry no calls.
- **Dart constructors' helpers** are a declared gap in the dead-code walk.

---

## 8.0.0 — 2026-08-20

### At a glance

- **`verify-claims` gains a fourth verdict and a declared denominator.** A
  claim held up by the analysed repository's *own* sanitizer, or clean except
  at named opaque launch sites, now returns `confirmed_with_caveats` with a
  structured `caveats` list — instead of an rc-0 pass indistinguishable from an
  earned one. Separately, a claims file now declares its own scope
  (`analysis_scope: shipped_artifact`), derived from packaging metadata: 154,505
  → 36,230 edges considered, and **81 opaque launch sites → 2**.
- **Verdicts disclose what they rested on.** `catalog_provenance` reports
  whether a catalogue came from the CLI or travelled with the repository under
  analysis; `dataflow_coverage` and `sanitizer_scope` state which languages have
  data-flow machinery wired, so a zero reads as "not expressible here" rather
  than "nothing found"; findings report per-flow `analysis_method` and disclose
  sanitized and excluded flows instead of pruning them into silence.
- **You can teach it your dependencies' I/O.** `--io-primitives` and a
  claims-file `extra_catalogs.io_primitives` key merge your rows into the
  shipped catalogue — Python taint sinks 113 → 172.
- **Four more languages reach data-flow.** Go, JavaScript, Rust and TypeScript
  gain def/use extraction (caddy builds 28,716 DDG edges over 1,635 symbols).
- **The cache stops growing without bound**, and eviction is a soft delete.
- **Cold `survey` is 2.4× faster** (517.7s → 220.0s).

### Breaking changes

- **`confirmed_with_caveats` returns exit code 3.** Any wrapper that treats
  non-zero as failure, or that enumerated the three prior verdicts, needs a
  decision. The qualification is raised only where it discriminates: a
  claim resting on a *shipped*-catalogue sanitizer still earns plain
  `confirmed`.
- **`VERIFY_CLAIMS_SCHEMA_VERSION` 1.1 → 2.0.**
- **`Edge.quality` is removed.** It carried zero independent signal:
  `quality.score` equalled rounded `confidence` on all 110,533 corpus edges.
- **`Symbol.span` is now `Optional[Span]`.** A span-less symbol serializes as
  a schema-legal `null` rather than a fabricated zero span. Consumers that
  assumed a span is always present must handle the null.
- **`status: complete` on an I/O catalogue is renamed
  `status: provenance_declared`.** The validator accepted `complete` while
  counting zero rows, so the word asserted a coverage nothing checked.

### Security and runtime safety

- **Analysing a repository executed code from that repository.** Runtime
  subcommands shelled out to `git` with the working directory inside the target
  repo; three independent vectors were demonstrated on `git status` alone.
  Because the attacker names the filter driver, no deny-list closes this — the
  fix is to not run `git status` at all. Its result fed only a cache-key digest,
  which is now taken from the working tree directly.
- **Runtime filesystem and subprocess use is declared through safety-zone
  wrappers.** A new `repo_inspection` zone declares the runtime's git, gitleaks
  and rust-analyzer probe use: unsanitized `runtime-cli-no-host-fs` flows
  **370 → 0**, subprocess flows **33 → 0**.
- **Cache eviction wrote to `host_fs` unsanitized**, because `safety_zones`
  shipped six cache wrappers and no *rename* of any zone — so publishing a
  `.partial` archive under its final name, and moving an evicted entry to a
  scratch name, were bare for want of anything to call. A new `cache_rename`
  guards **both** endpoints, which is the one way a rename's guard differs in
  shape from every single-path wrapper: an in-zone source can still deposit
  bytes anywhere on the host. Unsanitized flows 2 → 0; sanitized 85 → 87 —
  the flows are neutralised, not dropped.
- **`hypergumbo .` no longer requires the network.** `_has_sentence_transformers()`
  caught only `ImportError`, answering "is the library importable" when the
  question was "are the weights on disk". SECURITY.md is now generated from the
  tool's own verdicts.

### Cache lifecycle

- **The results cache is bounded.** `HYPERGUMBO_CACHE_MAX_GB` (default 5.0 GiB,
  `0` disables) evicts least-recently-used entries. The state hash is
  whole-tree, so an actively-edited repository missed on nearly every run and
  nothing ever removed an entry — measured at 5.3 GB in 27 entries for one repo.
- **Eviction zips rather than destroys** — 6% of the original size at 16× — with
  independent caps per artifact class (`HYPERGUMBO_SOFT_DELETE_SURVEYS_GB`,
  `HYPERGUMBO_SOFT_DELETE_SKETCHES_GB`), because the cache lives under `$HOME`.
- **What it refuses to touch is the substance of the feature.** Only whole
  entries matching the layout the tool itself writes; never a repository's
  newest entry; never one used within the last hour; never through a symlink
  leaving the cache zone. `cache-status` only ever *previews* the eviction set.

### For JSON consumers

- **`SCHEMA_VERSION` 0.19.0 → 0.20.1.**
- **`DEAD_CODE_MAYBE_SCHEMA_VERSION` 0.1.0 → 0.2.0** — `dead-code-maybe`
  candidates carry a per-item `reachability` field (1,747 of 2,107 are
  test-only), and the view moves off the shared `READ_VIEW_SCHEMA_VERSION`.
- **`docs/schema.json` is generated** from the registered meta-key vocabulary
  (`Edge.meta` 2 → 34, `Symbol.meta` 0 → 47), with `profile` reconciled to what
  the producer actually emits.
- **Four fields documented as non-nullable are optional in code and schema
  alike** — `language`, `span`, `fingerprint`, `quality` — and the spec now says
  so. `dst_ref` and `Symbol.cyclomatic_complexity` are documented for the first
  time, taking undocumented schema properties 2 → 0.

### Correctness — analysis you can rely on

- **A claim may only be confirmed over calls that were actually examined.**
  Examination moves from module-level name recognition to the individual call
  site: recognizing a module as stdlib, or holding *some* rows for it, no longer
  stands in for having adjudicated the call in front of you.
- **A clean verdict now requires that the analysis could have looked.** A
  language emitting call edges but shipping no I/O catalogue cannot support one;
  a taint claim needs **both** ends of a flow rather than either; a
  method-shaped catalogue meeting an analyzer that emits no method calls is
  inconclusive; a repo-supplied row can no longer replace the shipped row that
  held a claim's only evidence.
- **A mode-decided primitive gets its mode from the analyzer.** `io_mode` was
  stamped at exactly one site in the tree, so C's `fopen(path, "w")` classified
  as a **read** — an examined negative for the boundary actually crossed.
- **A directory is a virtualenv by content, not by name.** Excluding on bare
  names had dropped 427 real source files across 39 repositories.
- **Attribute taint sources can start a flow at all** — all five tree-sitter
  analyzers anchored the source to the *file* while the sink anchored to the
  function. TypeScript derives taint sinks at all (0 → 83); Groovy goes 0/0 →
  45 sources / 69 sinks. The DDG forward walk actually runs, so the `precise`
  label is earned rather than asserted (flows 149 → 193).

### For specific languages

- **Receiver typing reaches Python, Go, Java and C++.** Python types receivers
  from constructors, annotated parameters and allowlisted derivations, each
  gated on a positive import binding — trusting bare constructor names would
  have destroyed 61.5% of printed boundaries. Go derives the real package
  identifier from `/vN` import paths, removing 1,293 spurious module slots.
- **Python** emits an external-module call edge for an attribute chain rooted at
  a local (2,155 new call edges on kserve), resolves read-write `@property`
  getters, and captures annotated `self` fields.
- **Container members emit across nine more languages** — enum members,
  variants, cases and constants, behind a parity matrix. **PHP's interface,
  trait and enum containers emit** with owner-qualified members (1 of 4 → 4 of
  4); TypeScript records `abstract`; C++ emits pure virtual methods.
- **The shell's own writes and reads are visible** — bash redirection had
  produced zero edges.
- **Go** stops stamping `is_resolved=True` at four emit sites whose destination
  reads `unresolved`.

### Performance

- **The Python fingerprint locator was quadratic in file size.** The per-symbol
  step re-walked the whole module to find the smallest node covering a span,
  costing `symbols(file) × nodes(file)` — **324.3s of a 517.7s cold run, 62.6%
  of wall** — in a post-pass carrying no `AnalysisRun` card, which is why the
  time had only ever been reachable by subtracting pass durations from the wall
  clock. Bisecting a per-tree index takes it to **22.8s, 14.2×**, with semantics
  preserved exactly and 1 of 42,543 fingerprints changed.
- **The subprocess-CLI linker parsed every Python file four times per survey.**
  Sharing one memoized walk takes 4,488 parses and 16.0s to 1,123 and 3.7s;
  a cold analysis drops 162.4s → 139.1s.
- **`--minimal`** lets the ten commands that auto-run an analysis decline side
  outputs they never read — a caller who typed `slice --files` also paid for
  three budget-tier previews, up to 25 handler slices, and sketch
  pre-computation. Note that `survey` does **not** accept `--minimal`.

### Known limitations

- **Taint precision is measured, and it is the headline problem.** The first
  in-repo measurement of taint precision on real repositories puts it at
  **~41%**. The instruments now live in `docs/measurements/`. Treat taint
  findings as leads to adjudicate, not as a verdict.
- **`check-meta-write-discipline` reports 82 of 87 meta keys as `unaudited`.**
  That is deliberate — the default is the honest one — but it is visible debt,
  not a clean bill.
- **Six documented ADR-0017 precision capabilities do not run** and are no
  longer documented as if they did.

### Where to read more

- [CHANGELOG.md](../CHANGELOG.md) — the mechanism-level entry behind every line
  above.
- [docs/measurements/](measurements/) — the precision and performance
  instruments, including the taint-precision measurement.
- [docs/adr/](adr/) — the decisions. ADR-0016 §4 specifies the verdict
  vocabulary this release completes.
