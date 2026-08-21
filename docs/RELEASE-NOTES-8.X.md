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

---

## Unreleased

> Written as the work lands, promoted to a version heading at release-cut by
> `scripts/prepare-release`. `SCHEMA_VERSION` is at 0.20.2 here, one patch
> ahead of released 0.20.1.

### At a glance

- **A command-injection flow was visible or invisible depending on one capital
  letter.** `subprocess.Popen(tainted)` verified **clean** while
  `subprocess.run(tainted)` verified **violated** — because `py.py` types
  `module.Attr()` as `instantiates` when the attribute is PascalCase and
  `calls` otherwise, and the taint walk could not traverse a construction edge
  at all. Two fixtures differing only in the sink's spelling produced
  `violated` / exit 1 and `confirmed_with_caveats` / exit 3, the second
  asserting that no untrusted input reaches the subprocess zone about a
  program in which it plainly does. Construction edges are now traversable.
- **Ruby object creation was omitted from every `instantiates` consumer.**
  Ruby resolved `Klass.new` to `Klass#initialize` and emitted `calls`, while
  the eight other analyzers that emit `instantiates` emitted that — so a
  consumer filtering on `instantiates` silently dropped all Ruby construction.
  Ruby now emits `instantiates`. Its `dst` is deliberately unchanged, still
  the initializer symbol.
- **A taint sink that provably cannot receive the tainted value no longer
  reports a flow.** Taint models a flow as the tainted value reaching the sink
  as an argument or as its receiver, so a call passing only literal constants
  with no value receiver cannot carry one — 24 of 34 adjudicated false
  positives were sinks taking *no arguments at all*
  (`tempfile.TemporaryDirectory()`). This is a proof, not a heuristic, and it
  costs no recall.
- **Two of hypergumbo's own filesystem writes were outside the safety-zone
  discipline its claims assert** — `gitleaks.py` and the rust-analyzer probe
  called `tempfile.TemporaryDirectory()` directly. They were invisible for the
  same reason as everything above, and surfaced the moment construction edges
  became traversable.

### For JSON consumers

- **`SCHEMA_VERSION` 0.20.1 → 0.20.2.** `Edge.meta` gains `call_arg_shape`.
  An addition, hence a patch bump: the key is sparse and opt-in, its absence
  is the conservative reading, and every artifact written before this version
  stays valid and unchanged under it.
- **Four `call_construct` values named a different axis than the field does.**
  `remote_external` (Erlang) and `application_external` (Haskell) differed
  from their unsuffixed siblings only by whether the resolver found the
  callee — recoverable from `dst` already. `chained_return_type` (Go) names
  how a receiver's type was resolved and moves to `resolution_quality`.
- **A C# method group moves from `call_construct` to `ref_construct`.** A
  method group *references* a method without invoking it, and `call_construct`
  is scoped to the call family; `ref_construct` is the reference-family key
  that already lists `references` among its edge types.
- **A meta key's edge scope now lives in `applicable_edge_types`,** the typed
  field ADR-0038 ruling 2 built for it, rather than in a prose sentence.

### Known limitations

- **The construction-edge widening was measured, and the trade is poor.**
  `docs/measurements/0003` runs a delta census — baseline clone at the fix's
  parent commit, one instrument, isolated analysis caches per arm — and finds
  **35 flows added across six repositories, 0 removed, 1 true positive:
  marginal precision 2.9%** against a ~41% baseline. The sink-argument gate
  above lifts that to 16.7%. Precision remains the headline problem; treat
  taint findings as leads to adjudicate.

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
