<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Release notes — hypergumbo 7.x

This file is the user-facing view of what's in each 7.x release. The
[CHANGELOG.md](../CHANGELOG.md) remains the implementer-facing log
(every Added / Changed / Fixed entry, every internal refactor, every
test pin).

**The 7.x line is closed.** For the current line see
[RELEASE-NOTES-8.X.md](RELEASE-NOTES-8.X.md); for the previous one,
[RELEASE-NOTES-6.X.md](RELEASE-NOTES-6.X.md).

> **These notes were written after the fact**, during the 8.x line, from the
> 7.0.0 CHANGELOG section — the release-notes promotion step was missed at
> 7.0.0's release-cut and again at 6.1.0's. The content is accurate to the
> changelog; what it lacks is anything that was only ever known at release
> time and never written down.

---

## TL;DR

**7.0.0 is the vocabulary release.** The long-running `edge_type`
canonicalization completes, draining `Edge.edge_type` to a single
`relationship` axis and taking the registry **50 → 25 edge types**. Alongside
it, the CLI settles on one output contract and Python call-graph resolution is
overhauled.

**Four things will break a working setup. Act on these:**

1. **A behavior map produced before this release no longer validates** against
   the edge-type enum. Regenerate; do not migrate stored maps by hand.
2. **`verify-claims --json` emits a versioned object, not a bare array.** Any
   consumer indexing the top level breaks.
3. **`SCHEMA_VERSION` jumps 0.14.2 → 0.19.0**, across five intermediate bumps.
4. **Declared third-party dependencies move from tier 2 to tier 3.** If you
   filter or report on supply-chain tiers, your buckets shift. The declaration
   is not lost — it re-emerges as a `directness` stamp.

**One deprecation with a one-version window:** `Edge.quality` is deprecated
here and **removed in 8.0.0**. Read `confidence` + `confidence_source` +
`is_resolved` instead.

**One rename you should adopt now:** `hypergumbo survey` becomes the primary
analysis verb and `survey.json` the default artifact. `hypergumbo run` and the
legacy filenames survive as shims for one minor version.

**And the reason your dead-code report gets shorter:** Python call resolution
gains a real C3-linearization MRO, cross-file `self.method()` and
dependency-injection resolution, re-export and workspace-sibling resolution,
function aliases, and closure reachability — collapsing a large class of
dead-code false positives. The cross-language receiver-misbind funnels are
closed fleet-wide.

---

## 7.0.0 — 2026-07-27

### Breaking changes

- **Edge types 50 → 25; a pre-migration behavior map no longer validates.**
  Each retired value folds to the relationship it actually is, with its
  construct or protocol flavor preserved in `meta`: protocol-call families →
  `calls`, `links_to` / `renders` → `references`, template and mixin →
  `includes` / `extends`. Consumers already read the canonical types, so
  behavior is unchanged apart from the label and the `meta` key. Because
  `implements_rpc` was call-like — traced for taint, I/O, ranking and slice — a
  shared `is_grpc_rpc_implementation` predicate carries that coupling onto its
  folded form.
- **`verify-claims --json` returns an object.** All eight read views now emit a
  `{schema_version, view, …}` envelope from one gateway.
- **Declared third-party dependencies are tier 3, not tier 2 (ADR-0041).**
  Tiers now name *distance only*. The declaration re-emerges as a `directness`
  stamp (`direct` / `transitive` / `undeclared`), and boundary nodes carry an
  `ecosystem` stamp (`stdlib` / `third_party`). In-repo role files (examples,
  docs, fuzz, bench) and generated routes become tier 1, leaving
  `internal_package_roots` the sole tier-2 producer.
- **`SCHEMA_VERSION` 0.14.2 → 0.19.0**, and `io-boundaries`' view schema → 2.0
  (`total_io_edges` now counts only the verified surface).

### Deprecated

- **`Edge.quality` (`{score, reason}`).** It carries zero independent signal:
  `score` mirrors `confidence` on every verification-corpus edge, and `reason`
  encodes the emitter mechanism rather than a confidence tier. Still emitted in
  7.0.0; **removed in 8.0.0**. (`Symbol.quality` is a separate field and was
  not in scope.)
- **`hypergumbo run`, the legacy artifact filenames, and the
  `behavior_map_io` → `survey_io` / `run_behavior_map` → `run_survey`
  renames** all survive as shims for one minor version. The on-disk `view`
  discriminator stays `"behavior_map"` for schema compatibility.

### At a glance

- **One CLI output contract.** All eight read views standardize on
  `--format {text,json}` (`--json` kept as an alias). A new `load_substrate()`
  chokepoint validates every `--input` consumer: malformed JSON, a missing
  `nodes` key, or the wrong `view` exits **rc=2**; a version mismatch warns.
  An unexpected error exits **rc=1** cleanly, with the traceback under
  `--debug`.
- **CLI input validation fails loudly.** Non-directory paths, invalid numeric
  flags, whitespace-only patterns, and unknown `--kind` / `--language` /
  `--require-section` values exit rc=2 with did-you-mean hints.
- **New command: `hypergumbo repeat-finder`** — structural-clone and
  refactoring-lead detection, grouping symbols by `(language, shape_id)` to
  surface copy-paste and extract-helper candidates. Trivial clusters are
  dropped below `--min-complexity`; production clones headline and test-only
  clones are a labeled disclosure bucket.
- **Field- and variable-kind symbols emit across ~20 languages.** Struct,
  class and record members emit `kind="field"` anchored to their type;
  module and top-level bindings emit `kind="variable"`, with declared types in
  `signature` and access modifiers driving `is_exported`. Function-body locals
  are excluded, and fields stay out of call-graph resolution. C#/Java/TS field
  declarations attach DI, ORM and reactive decorators (`@Autowired`,
  `[Column]`, Lit `@property`) as `decorated_by` edges.
- **Confidence becomes evidence-derived (ADR-0039), and ranking moves off it.**
  `Edge.create` derives confidence from `evidence_type` when omitted;
  `Edge.rank_score` / `Entrypoint.rank_score` become the home for ranking
  prominence. Published values are unchanged at this release — `rank_score` is
  initialized from `confidence` — but the two concepts are now separable.
  This ended a reliability inversion: the containment `naming_convention`
  heuristic had hardcoded `confidence=1.0`, above the structurally-certain
  `span_overlap`.

### Python call-graph resolution

- **Inherited-method calls resolve across the class hierarchy.** Dispatch walks
  a true C3 linearization (fixing uneven-depth diamonds), resolves cross-file
  `self.method()` and `self.field.method()` dependency-injection calls via
  enclosing-class and field-type hints, and biases to unresolved when a builtin
  base shadows an in-tree method.
- **Imports, re-exports and submodule reads resolve to real first-party
  nodes** — module-constant attribute reads, non-package facade re-exports,
  imported module-level constants, workspace-sibling imports, and symbols
  defined in `__init__.py`, all of which previously resolved to a phantom
  `external_symbol` twin. Cross-*package* submodule reads remain a tracked
  residual.
- **Scope-shadow, alias and closure gaps close.** Module-level function aliases
  resolve to the real body, closure-factory decorators become reachable via
  `dispatches_to`, and `@property` reads resolve to the in-repo getter. Where a
  name is rebound by closure capture or module-scope reassignment, the retarget
  is withheld: a missed retarget is a safe phantom, a wrong one is not.
- **External and stdlib base classes emit unresolved-external `extends` edges
  instead of being dropped**, for every OO language — generalizing the Python
  and JS/TS fallbacks to Kotlin, Ruby, Java, C#, Scala, PHP and Swift, and
  recovering dotted bases like `argparse.ArgumentParser`. External PascalCase
  constructors (`Path()`, `MagicMock()`) now type as `instantiates`, so that
  edge type records external constructions instead of zero.

### Cross-language call-graph precision

- **Unresolvable-receiver calls no longer misbind to an arbitrary same-named
  internal definition.** A call whose target class could not be resolved fell
  through to a bare short-name lookup and bound to the first same-named method
  — the Scala `copy` / `setTo`, Swift `create` / `delete` / `run` and analogous
  funnels — fabricating thousands of dead-code false-positive magnet edges. A
  shared `defer_bare_method_call` gate, wired into fourteen analyzers, withholds
  a weak short-name bind as an honest unresolved external, while an MRO walker
  recovers the genuinely-inherited call. Free functions, same-class implicit
  `self`, and strong or import-scoped matches still resolve. **The discipline
  is to withhold, never pick-first.**
- **Untyped-receiver magnets are demoted at finalize.** A call like `d.Val()`
  whose receiver type cannot be inferred still collapsed unrelated call sites
  onto one `Owner.method`. Two cleanly-harmful sub-classes — a production →
  test-helper misbind and a stdlib-interface-method shadow (`Close`, `Parse`,
  `Len`) — are demoted to unresolved externals, gated by a validator check that
  shares the exact predicate so gate and demotion cannot drift.
- **New receiver-type-dispatch linker.** One receiver-type-keyed search unifies
  extension methods and UFCS free functions; Kotlin extension-call and D UFCS
  resolution move onto it. Scala trait-linearization and Swift superclass /
  protocol-extension MRO walkers join the `inherited_calls` linker, and a new
  Framework linker recovers Caddy's reflective plugin dispatch.
- **Rust dispatch resolves to the concrete impl.** Enum match-arm bindings
  adopt the variant's field type so `q.run()` reaches `Query::run`, and a
  chained return-position call resolves against the receiver's inferred return
  type so `Cmd::parse().run()` reaches `Cmd::run` — together putting zoxide's
  whole subcommand tree back on the forward slice.

### Views, metrics and honesty signals

- **Compact and tiered views no longer collapse to ~1 symbol at small
  budgets.** A slim `compact_node` projection replaces the full ~24-field
  survey node, and token-budget tiers re-project `features[]` onto the retained
  set rather than copying it wholesale — restoring containment monotonicity.
- **A supply-chain tier drop is now reported.** A DERIVED tier-4 file excluded
  by the default filter records its path in `limits.tier_filtered_files`;
  previously its symbols and edges vanished with no diagnostic —
  `max_tier_applied` said *why*, and nothing said *what*.
- **Dense non-web source files are no longer silently dropped as minified.**
  The average-line-length heuristic now fires only for web-asset extensions, so
  a dense Python data module or a header-less generated protobuf keeps every
  symbol and edge.
- **Manifest CLI entrypoints survive the default-view noise filter**, which had
  treated every `entry_role=script` symbol as noise and erased pyproject
  `[project.scripts]` console-scripts before detection.
- **`reproducibility_context.captured.grammars` reflects grammars actually
  used, not all installed** — it had listed every installed `tree-sitter-*`
  distribution (51 on a typical dev box), ~38 of them for languages that
  produced zero nodes.
- **Reporting lists are present only when non-empty**, and `analysis_runs[]` is
  written in a deterministic `started_at` order.
- **`verify-claims` violated-flow evidence is drillable.** A violated
  taint-flow claim had collapsed thousands of flows into five indistinguishable
  `<source> -> <sink>` rows; the verdict now deduplicates on full per-flow
  identity, renders symbol IDs plus a `via N hop(s)` indicator, discloses the
  true total-versus-distinct count, and attaches a bounded structured
  `evidence` array to the JSON envelope.

### Frameworks, I/O boundaries and routes

- **I/O boundary classification is receiver-verified** — no more untyped
  `.replace()` phantom-matching `Path.replace`. Python stdlib database
  primitives (sqlite3, dbm, shelve) populate `db_read` / `db_write`, and Django
  ORM I/O becomes visible via type-verified `<Model>.objects` and
  `models.Model`-subclass receivers. Third-party ORMs are deliberately
  excluded, as a receiver-inference problem the untyped-method gate correctly
  refuses.
- **Route detection widens.** Route frameworks import-promote on bare imports
  via a curated allowlist; PHP, Scala and Haskell namespaces plus six route
  YAMLs are added; Vapor grouped-builder routes recover 236 endpoints;
  Starlette `WebSocketRoute` classifies as a websocket handler. Build-wrapper
  scripts (`mvnw`, `gradlew`) no longer seed forward slices.
- **Every registered analyzer resolves to an `analysis_runs[]` entry or a
  `limits.skipped_passes[]` record** — an analyzer whose language was absent
  from the taxonomy used to vanish silently on an empty result.
- **Complexity coverage reaches 67 languages** — C, C++, bash, Solidity, WGSL,
  CMake and Jupyter callables gain `cyclomatic_complexity` and `lines_of_code`;
  Ruby no longer double-counts.
- **`Symbol.lines_of_code` is renamed `line_span`** (it is a physical span, not
  SLOC), and `profile.languages[*]` is corrected to a `{files, loc}` shape.
- **`explain` and `sketch` display captured `Symbol.docstring`.** Warm `sketch`
  reads comparison sketches from sidecars (~27s versus 155s).

### Infrastructure

- **Codeberg → GitHub + Woodpecker CI migration.** `origin` is now GitHub; PRs
  and CI run on GitHub plus self-hosted Woodpecker, with Codeberg retained as a
  passive mirror. This is a project-infrastructure change with no effect on the
  published tool, listed here because the CHANGELOG entries for it are
  substantial and are otherwise easy to mistake for product changes.

### Known limitations at 7.0.0

- **Cross-package submodule reads remain unresolved** in Python.
- **Correct-but-unprovable receiver binds are deliberately kept** — the Rust
  trait-dispatch funnel stays in scope as ADR-0012 work rather than being
  demoted.
- **Byte-level reproducibility was overclaimed in three spec sites and is
  corrected here.** The `analysis_runs[]` array is *not* byte-stable: every
  entry stamps a fresh `execution_id` and wall-clock timestamps, and because
  the sort key is wall-clock, even pass *ordering* varies run to run. The
  guarantee is scoped to the L2 semantic graph — nodes, edges, `stable_id`s,
  `run_signature` — which does reproduce. Byte-level reproducibility remains a
  separate opt-in (`--reproducible`).
