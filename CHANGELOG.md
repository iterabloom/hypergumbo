<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v6.1.0
- Released **schema** is at: v0.14.2

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

> **Looking for the reader-friendly summary?** See **[docs/RELEASE-NOTES-6.X.md](docs/RELEASE-NOTES-6.X.md)** for the audience-organized release notes (CLI users, JSON consumers, per language/framework). This file is the **implementer log**: structured, mechanism-level entries per release.

## [Unreleased]

### Summary

This cycle hardens the analysis substrate and the CLI contract. Two large threads dominate. **Symbol-kind emission parity**: field- and variable-kind `Symbol`s now emit across ~20 languages (Go, Rust, Java, C#, Kotlin, Swift, Scala, Dart, Python, TS/JS, Elixir, Julia, Fortran, Zig, Nim, D, F#, Solidity, and more), locked by a shrink-only emission-parity gate. **Python call-graph resolution** is overhauled — C3-linearization MRO for inherited methods, cross-file `self.method()` / `self.field.method()` (dependency-injection) resolution, module-constant / re-export / workspace-sibling resolution to real first-party nodes, function aliases, and closure-factory reachability — collapsing a large class of dead-code false positives.

Alongside: the CLI standardizes on `--format {text,json}` across all read views behind one schema-envelope gateway, with strict input validation (loud `rc=2` / `rc=1` errors) and self-describing `compact` / `tiered` projections; confidence becomes evidence-derived from a 75-pathway table (ADR-0039); supply-chain tiers are redefined as pure distance with `directness` / `ecosystem` stamps and rigorous test / role / workspace-file classification (ADR-0041); and edge/vocabulary axes are canonicalized and de-overloaded (ADR-0023/0027/0028), including a dedicated `route_protocol` axis (ADR-0031). The ADR-0023 `endpoint_shape`→`relationship` fold is **completed** this cycle — the last 22 long-tail values are folded to canonical relationships + `meta` and the `endpoint_shape` axis of `Edge.edge_type` is drained to empty. SCHEMA_VERSION advances 0.14.2 → 0.17.0.

### Added

#### Symbol-kind emission parity — field/variable symbols across ~20 languages

- **Field- and variable-kind `Symbol`s now emit across the language fleet.** Struct/class/record/object members emit `kind="field"` anchored to their type, and module/top-level bindings emit `kind="variable"`: Go, Rust, Java, C#, Kotlin, Swift, Scala, Dart, Python (class attributes incl. dataclass fields), TypeScript/JavaScript, Elixir (`defstruct`), Julia (struct members + module assignments), Fortran, Zig, Nim, D, F# (records; module `let` reclassified `value`→`variable`), Solidity (contract state variables), PHP (class/interface/trait/enum properties and constants as `field`; a top-level `const` as `variable`), C++ (class/struct member fields as `field` with access-specifier-derived visibility — struct default public, class default private — and top-level/namespace variables as `variable`; member-function declarations, nested types, and function prototypes are filtered out, and function-body locals excluded via a module-scope gate), and Ruby (class/module-body constants and `@@` class variables as `field`, top-level constants as `variable`, and `attr_accessor`/`attr_reader`/`attr_writer` attributes as `field`; instance variables and members assigned inside method bodies are deferred). Declared types populate `signature` and access modifiers drive `is_exported`; function-body locals are excluded, and fields are kept out of call-graph resolution to avoid spurious edges.
- **Field annotations become `decorated_by` anchors.** C#, Java, and TypeScript/JavaScript field declarations now attach DI/ORM/reactive decorators (`[Inject]`, `[Column]`, Spring `@Autowired`, JPA, Lit `@property`/`@state`) as `decorated_by` edges on the field symbol.
- **Emission-parity gate.** A per-`(language, construct)` matrix test locks `emits_variable`/`emits_field` coverage as a shrink-only ratchet, skipping constructs a language lacks (e.g. Java/C# have no module-level variables).
- **Identity-field parity guard (`shape_id`).** The emission-parity matrix gains a `shape_id` column — its first *identity*-field cell — locking structural-shape emission for the eight core analyzers as a hard-lock ratchet. It closes the blind spot that let a `csharp shape_id=None` regression (`CSharpAnalyzer.analyze()` overriding the pipeline and bypassing the base-class auto-stamp loop) slip past every standing test until a point-in-time cross-language cohort caught it. Scoped to mainstream coverage (per spec §6, `shape_id` is optional at ~41/70 analyzers); the niche solidity/wgsl gaps remain separately-tracked coverage work.

#### CLI read-view contract

- **All eight read views standardize on `--format {text,json}`.** `io-boundaries` and `verify-claims` adopt the canonical flag (default `text`, `--json` kept as a back-compat alias); `cache-status`, `catalog`, and `routes` gain `--format json`, emitting `{schema_version, view, …}` envelopes for programmatic use.
- **Strict `--input` substrate loading.** A new `load_substrate()` chokepoint validates every `--input` consumer: malformed JSON, a missing `nodes` key, or a wrong `view` raise `SubstrateError` (`rc=2`); schema-version mismatches warn.
- **`explain` disambiguation flags.** `--language`, `--file`, `--first`, and `--limit` disambiguate matches in multi-target repos; unrecognized edge types are flagged and registered edge-type descriptions surfaced.
- **`sketch --no-comparison-sketches`** opts out of the 4×/16× comparison sketches and representativeness table for scripted/batch runs.

#### `repeat-finder` — structural-clone / refactoring-lead detection (WI-vogij)

- **New `hypergumbo repeat-finder` read-view surfaces structural clones as refactoring leads.** It groups symbols by `(language, shape_id)` — a cluster of ≥2 nodes is a set of structurally-identical implementations (same control-flow/nesting skeleton, differing only in identifiers and literals), within-language per ADR-0014. This activates `shape_id`'s one non-redundant capability over `fingerprint` (ADR-0035 §1, spec §367): clustering copy-paste / extract-helper candidates. `--format text` (default) prints ranked refactoring-lead blocks; `--format json` emits a `{schema_version, view: "repeat_finder", summary, clusters}` envelope.
- **Signal-dense defaults.** Trivial clusters (shared cyclomatic complexity below `--min-complexity`, default 2 — a straight-line stub is not a refactoring lead) are dropped; only production clones (≥2 non-test members) are the headline, with test-only clone clusters (parametrized tests are structurally identical by design) counted as a labeled disclosure bucket shown via `--include-tests`. Clusters rank by duplication burden (member count × representative LOC). `--min-complexity 1` includes straight-line clones; `--limit` bounds text output.

#### Survey rename (ADR-0042)

- **One canonical artifact name + a merged discovery resolver (survey-rename S2/S3, internal, no behavior change).** `behavior_map_io` now defines the single canonical output filename `CANONICAL_SURVEY_FILENAME = "survey.json"` plus the four historical aliases (`hypergumbo.results.json`, `hg.json`, `bm.json`, `behavior_map.json`) and a `find_survey_in_dir()` primitive that discovers any of them (canonical-first, plain-over-`.gz`, name-major). The CLI's `_discover_input_file` is rewired onto it, widening auto-discovery to the canonical name and every legacy alias while still finding today's `hypergumbo.results.json` — no user-facing change. This is the shim-first foundation the rest of the ADR-0042 rename (CLI default filename, `survey` verb, docs/tests/tracker sweeps) builds on.
- **`hypergumbo survey` is the primary analysis verb; the default output is `survey.json` (survey-rename S2-CLI, WI-vatuf).** `hypergumbo survey <repo>` produces `survey.json`. The default cache-write basename and every internal cache-reader (sketch warm-cache auto-discovery, slow-test masking's newest-map walk) move to the canonical name, resolving any legacy alias on read so pre-rename caches keep working; all `--input` help strings and usage examples now name `survey.json` / `hypergumbo survey`. **`hypergumbo run` remains a fully-functional deprecated alias** that prints a one-line stderr deprecation warning naming `survey`; per the ADR-0042 one-minor-version window it (and the legacy filename aliases) are removed in the following minor release.
- **Legacy survey filenames are accepted on read with a deprecation warning (survey-rename, WI-didif).** Every `--input` consumer routes through `load_substrate`, which now warns to stderr when the loaded file uses a legacy basename (`hypergumbo.results.json` / `hg.json` / `bm.json` / `behavior_map.json`, incl. their `.gz` forms) — naming the alias and the canonical `survey.json` — while still loading it. The aliases stay accepted for one minor version, then are removed at window-close (ADR-0042).
- **`behavior_map_io` module renamed to `survey_io` (survey-rename, WI-kisoj).** The consumer-side substrate loader (`load_substrate`, `load_behavior_map`, `find_survey_in_dir`, `find_behavior_map`, the filename constants, `SubstrateError`) now lives in `hypergumbo_core.survey_io`. `hypergumbo_core.behavior_map_io` survives as a deprecation shim that re-exports the same names and warns on import (per ADR-0042's shim-first sequencing rule for the editable install), and is removed at window-close. Internal src, the loader test, and the bakeoff/analysis scripts migrated to the new import path. The `cli.run_behavior_map` analysis function is likewise renamed to `cli.run_survey`, with `run_behavior_map` kept as a module-level alias (same object, identical signature) for the deprecation window so existing call-sites resolve without a mass rename.
- **Docs concept rename "behavior map" → "survey" (survey-rename, WI-rivuj + WI-zaviv).** README, the spec, USE-CASES, and the present-tense docs now call the JSON output artifact a **survey** (produced by `hypergumbo survey` → `survey.json`); the spec's v0.x stability commitment is rephrased to name the survey view. The on-disk `view` discriminator value stays `"behavior_map"` for schema compatibility (consumers parsing the JSON see no change). Historical/migration docs keep their original vocabulary; the `.agent/` playbook sweep is a separate governance-approved change.

#### Route detection & entrypoints

- **Manifest-silent route promotion.** Route frameworks (Flask, Express, FastAPI, Sinatra, …) now import-promote on bare exact imports without a manifest, fixing dead-code route-monoculture in manifest-silent apps via a curated allowlist.
- **Consolidated route accessor.** A new `routes` module centralizes route detection behind `route_of()` / `is_route()` (marker precedence over YAML concept; `WS` normalized to a `protocol` field, ADR-0027).
- **`EntrypointKind` becomes a catalog-derived axis** via `all_known_entrypoint_kinds()`, and entrypoint records gain a `meta` provenance dict (`id`, `source`, `evidence_type`) mirroring `Edge.meta`. SCHEMA_VERSION 0.14.2 → 0.14.3.

#### Linker resolution

- **Receiver-type-dispatch linker — shared substrate for extension/UFCS calls (INV-vigaf).** A new Infrastructure linker `linkers/receiver_type_dispatch.py` resolves unresolved `x.foo()` calls carrying a `receiver_type_hint` to an **extension method** (`meta.extension_receiver`) or a **UFCS free function** (`meta.ufcs_receiver_type`) whose declared receiver / first-parameter type matches — one receiver-type-keyed candidate search unifying the two non-hierarchy call forms, distinct from the ancestor-walking `inherited_calls` linker. Language-scoped (INV-milud) and ambiguity-gated (an ambiguous same-type/same-name hint withholds rather than misbinds, per INV-fahub). A new `ast_call_ufcs` evidence_type (confidence 0.8, seeded like `ast_call_extension`) names UFCS resolutions; `edge_type` stays `calls` (ADR-0023). The linker runs but is inert on current output until analyzers stamp the receiver-type hints (the Kotlin extension-resolution migration off the analyzer and the D/UFCS facet that follow) — no output change this release.
- **Kotlin extension-call resolution moved into the shared linker (INV-vigaf, WI-lodij).** Kotlin's analyzer no longer emits the resolved `ast_call_extension` edge for `receiver.extFn()` itself; it emits an unresolved call carrying `receiver_type_hint`, and the `receiver_type_dispatch` linker emits the resolved edge — bringing Kotlin's extension resolution under the same analyzer→linker contract as inherited calls (INV-nilud analogue) and validating the substrate on a working baseline. The analyzer retains a local extension lookup solely to track the extension's return type for chained navigation calls (a pass-2 concern the linker can't feed back), so recall is unchanged. The linker gains generic base-name normalization (`List<Int>` ↔ `List<String>` match on `List`), preserving Kotlin's prior generic matching for any language routing through it.
- **D UFCS receiver gating + linker recovery (INV-vigaf, WI-situj).** D's analyzer misbound an unresolvable-receiver call `thing.exists()` to an arbitrary same-named free function `exists()` at 0.85. The gate now withholds **any non-module value receiver** — a parameter, local, loop variable, or field (`_is_module_receiver` distinguishes a UFCS value prefix from a genuine imported-module prefix, which still resolves) — emitting the call unresolved with a `receiver_type_hint` when the value's type is known (a typed parameter) or none otherwise. Real-repro validation (dub) drove this: the original parameter-only gate left the dominant funnel open on locals/loop vars (`l.startsWith()`, `x.toString()`); the broadened gate cuts the resolved-internal bare `exists`/`startsWith`/`toString` binds 107 → 21 and eliminates the `startsWith`←19-files and `toString`←25-files magnets. Free functions are stamped with `meta.ufcs_receiver_type` (their first-parameter type), so the `receiver_type_dispatch` linker recovers the real UFCS target (`x.foo()` ≡ `foo(x)`) as an `ast_call_ufcs` edge. A follow-up closes the last dub funnel: a free-function-form call (`exists(x)`, no receiver) bound by short name to a **nested-local** function — one defined inside another function's body, not callable by bare name from another scope. Such nested functions are now marked and excluded from the global resolver index (like `field`/`variable` symbols), so they stay output symbols but are never a cross-scope call target; the dub `exists` magnet (~9 cross-file binds → the nested `compilers/utils.d` `exists`) drops to 0.
- **Scala + Swift inherited-method resolution (MRO walkers).** The `inherited_calls` linker gains a Scala `_walk_linearization` walker (right-to-left BFS approximating trait linearization — rightmost mixin wins, superclass last) and a Swift `_walk_left_to_right` walker (left-to-right pre-order DFS — single superclass chain, then protocol extensions in declaration order), registered for `scala`/`swift`. These recover Step-2 (inherited-method) calls on a typed receiver — `receiver.inheritedMethod()` where the method lives on an ancestor/mixed-in trait — completing the recall story for the Scala/Swift receiver-gating facets (which already resolve the direct-method Step-1). External-base shadowing (INV-guviv) stays Python-scoped; the new walkers resolve in-tree ancestors.
- **Subprocess→CLI joins expand.** The linker now joins Python Fire class methods and argparse subcommands to their handlers, not just decorator-registered commands.
- **Python nested-function resolution walks the full LEGB scope chain** via a new `ScopeStack`, resolving calls from grandparent-or-higher enclosures (additive; fires only after local/import misses).
- **External base classes emit `extends` edges (Python).** A Python class whose base is external/stdlib (`Enum`, `Exception`, `Protocol`, `ABC`, …) now emits an unresolved-external `extends` edge to a boundary placeholder instead of dropping the relationship by omission — so type-hierarchy / "what is this a subclass of" queries no longer answer "nothing" for the ~1-in-4 Python classes whose bases are all external. Confidence stays evidence-derived (`ast_extends` → 0.95, same as a resolved extends); `is_resolved=False` carries the unresolved target. An in-tree guard keeps a not-yet-extracted in-tree base from leaking as a workspace-prefixed phantom (INV-nuzas), and aliased in-tree bases re-resolve to their real class; dotted/qualified bases (`argparse.X`) are deferred to the core-linker chokepoint. (WI-jubag Python slice; mirrors the landed JS/TS A2 fallback.)
- **External constructors type as `instantiates` (Python).** A call to an imported external name that is PascalCase — `Path()`, `MagicMock()`, `argparse.ArgumentParser()`, … (Python's strong class-naming convention) — now emits an `instantiates` edge (`evidence_type="ast_new"`, `is_resolved=False`) instead of being misfiled as a plain `calls` edge, so `instantiates` records external constructions rather than zero. snake_case callables (`join()`, `os.getcwd()`) stay `calls`. Covers the bare-name and `module.ClassName()` forms. (WI-jubag instantiates half.)
- **Caddy module-registry dispatch linker (WI-zizig).** New Framework-subcategory linker that recovers Caddy's plugin dispatch: modules register at `init` via `caddy.RegisterModule(T{})` and are driven reflectively by string ID through `LoadModuleByID`, so their handler methods (`Provision`/`Validate`/`ServeHTTP`/`Start`/`Stop`/`Cleanup`) have zero static incoming edges and the forward slice from `main()` misses them. The linker identifies a Caddy module as a Go struct owning a `CaddyModule() ModuleInfo` marker method and emits `dispatches_to` edges from that **marker method** (the one node the static graph already reaches, via `init → RegisterModule → Module.CaddyModule → T.CaddyModule`) to each handler — putting the handlers back on a forward slice. The register→load string-ID coupling is resolved from JSON at runtime and is not statically recoverable, so specific `LoadModule` sites are deliberately not matched (the same tractable structural strategy as the Kafka Streams / decorator dispatch linkers). Residuals: module-namespace-specific handlers (`NewEncoder`, `Match`) beyond the universal interface hooks, and cross-package `init → RegisterModule` resolution (a separate Go analyzer gap) on which full main-reachability depends.

#### Symbol identity, introspection & axes

- **Symbol ids use real kind-slots (ADR-0036).** Route-materializer, event-sourcing, boundary, and package/config symbol ids now carry real kind-slots (`function`, `external_symbol`, `package`, `file`) instead of fold-source or role values, with framework role moved to `meta`. Boundary identity becomes `(language, path, name)` so the same external reached via different syntax dedupes (~1,399 ids change); the rest are identity-neutral and clear residual `id_format` validator warnings.
- **`Symbol.visibility`** — one computed canonical visibility level (language modifier > leading-underscore convention > public default) replacing the asymmetric `modifiers` encoding. SCHEMA_VERSION 0.14.3 → 0.14.4.
- **Producer-side axis-coherence linter deepened.** It now descends module-local and nested-closure emit helpers and binds positional arguments, surfacing 12 previously-invisible unregistered `Symbol.kind` emissions; the gate becomes a shrink-only ratchet against a committed baseline. Seven language constructs are registered and `structure` folds to `struct` (ADR-0027).
- **Bash external programs disclosed as a `command_launch` io-boundary cohort** — deduped per caller+command and excluded from `total_io_edges` (io-boundaries view schema 2.0 → 2.1, ADR-0016).
- **General YAML file-anchor nodes** emit per generic `.yaml`/`.yml` file (coexisting with `yaml_ansible`), giving 173 YAML anchors on the self-corpus.
- **`slice --entry <X> --io-boundary <category>`** filters a slice to the edges that reach that I/O boundary.

#### Confidence derivation (ADR-0039)

- **Evidence→confidence groundwork lands.** A central `derive_confidence(evidence_type, is_resolved=…)` reader is seeded for single-valued pathways, the spec validator flags any `Edge.confidence` outside its evidence-derived band (advisory), and confidence:F1 conditions resolved vs. unresolved multimodal call types. (The producer migration that consumes this is under Changed.)
- **`Edge.confidence_source` + `Edge.rank_score` / `Entrypoint.rank_score` — the confidence-separation field substrate (ADR-0039 rulings 2 & 3).** `confidence_source` (bounded-enum `evidence_derived`/`emitter_constant`/`composite`) makes the migration off hardcoded per-emitter constants machine-readable: `Edge.create` stamps `evidence_derived` when the value came through `derive_confidence`, `emitter_constant` for an explicit producer constant (incl. the unseeded 0.85 fallback), and a producer may declare `composite` while its ranking migration is pending. `rank_score` is the ranking-prominence home the ranking adjustments (fan-out dampener, entrypoint penalties/boosts) will relocate onto (ruling 3); it initializes from `confidence` so **published confidence and ranking are unchanged** until the per-producer relocation PRs land. A guard (`confidence.find_constant_confidence_violations`) flags any emitter shipping one constant across >100 edges without declaring it, and the axis-conformance validator range-checks `confidence_source`. SCHEMA_VERSION 0.14.4 → 0.14.5 (additive).

#### Framework provenance (WI-napuj)

- **`profile.framework_evidence` traces each declared framework to the nodes that import it.** `profile.frameworks` listed framework names with no graph-level anchor — a consumer reading "frameworks: starlette" could not find *where* starlette was used. `refine_frameworks` already joins each framework's import-module patterns to the importing source symbols during its promote/demote pass; that join is now kept rather than discarded, as `profile.framework_evidence: {framework: [node_id, …]}` (prod, non-test importers, language-gated the same way promotion is, so Python `http.client` is not counted as Julia `http` evidence). A manifest framework whose modules never appear in an import edge (e.g. a JS lib on a repo with unavailable JS/TS extraction) is absent rather than keyed to an empty list — "evidence where the graph supports it" (INV-virik omit-when-empty). Additive; `profile` is permissively schema'd, so no SCHEMA_VERSION bump. INV-numat member.

### Changed

#### Edge-type vocabulary — endpoint_shape fold-tail COMPLETE, axis drained (schema 0.17.0)

- **The 21 long-tail `endpoint_shape` `Edge.edge_type` values are pruned from the registry (WI-pumav consolidated Phase-4b, ADR-0023 §6 fold complete).** After the 4-lens ruling pass (audit-findings 0017) producer-migrated all of them to canonical relationships + `meta` discriminators across Batches 1a–7, this PR removes their dead registry entries. The `endpoint_shape` axis of `Edge.edge_type` is now **empty** — the fold declared by ADR-0023 §6 is complete (the companion `Symbol.kind` / `Edge.evidence_type` endpoint_shape registries were already closed). **`SCHEMA_VERSION` 0.16.0 → 0.17.0** (minor, per the ADR's Phase-4b removal contract); the total `Edge.edge_type` registry drops 50 → 29 (25 `relationship` + 4 `pending_classification`). Regenerated `docs/schema.json` + `docs/concept-axes.md`. `axis_meta_keys.py` registers the new `meta['ref_construct']` values (`markdown_link` / `rdf_vocabulary` / `association` / `build_tag_alternative` / `view_render` / `template` / `puppet_class` / `sass_mixin` / `dockerfile_stage` / `puppet_require` / `puppet_notify` / `crypto`), the new `meta['mechanism']` values (`callback` / `kernel_launch` / `template`), and the new `meta['refresh']` key. This unblocks the WI-pusuv access_mode census (its gate was the `endpoint_shape` values that are now drained).

#### Edge-type vocabulary — resolver/OpenAPI/RPC family folded, `pending_classification` drained

- **Three `pending_classification` resolver/OpenAPI values producer-migrated to canonical relationships (WI-sumik / WI-pusuv Option B, audit-findings 0016, ADR-0023 Phase 3).** `resolver_implements` (GraphQL resolver → schema field — impl→contract) now emits `edge_type="implements"` + `meta['protocol']='graphql'`; `resolver_for_type` (GraphQL `@Resolver(() => Type)` declaration-time association) and `openapi_implements` (OpenAPI spec operation → route handler, direction-preserving per audit-findings 0016 finding 2) now emit `edge_type="references"` + `meta['ref_construct']` (`graphql_resolver_type` / `openapi_operation`). Prior `framework_dispatch` / `type_name` / `field_name` / path meta preserved. Phase-3 producer migration (dead registry entries pending the consolidated prune; no `SCHEMA_VERSION` change). `axis_meta_keys.py` registers the two new `ref_construct` values and generalizes the `protocol` key description to `implements` edges. The consumer-coupled `implements_rpc` member folds in a follow-on PR; the registry prune (draining the `pending_classification` axis to empty) is the consolidated Phase-4b PR that follows.
- **`implements_rpc` folded to `implements` + `meta['protocol']='grpc'`, preserving its call-like consumer coupling (WI-sumik / WI-pusuv Option B, audit-findings 0016 finding 3, ADR-0023 Phase 3).** A Go method on a struct embedding `UnimplementedXxxServer` literally IS a Go interface implementation (impl→contract), so `grpc.py` now emits `edge_type="implements"` instead of `edge_type="implements_rpc"`. Unlike a plain structural `implements` edge, `implements_rpc` was **call-like** — traced for taint propagation, I/O-boundary reachability and per-language I/O coverage, ranked at call weight (1.0), and forward-traversable in slices. To avoid silently demoting gRPC reachability, a single shared predicate `edge_types.is_grpc_rpc_implementation(edge_type, meta)` recognizes the folded form (`implements` + `protocol=grpc`) and is composed into every one of those consumers (`taint`, `io_boundary`, `verify_claims`, `ranking`, `slice`) — so the coupling transfers **without** wholesale-including every structural `implements` edge. Behavioral evidence: new tests assert taint crosses the folded edge (and a plain `implements` does not), the I/O reverse-graph traces it, and it ranks 1.0 not 0.5. Phase-3 producer migration (dead registry entry pending the consolidated prune; no `SCHEMA_VERSION` change).

#### Edge-type vocabulary — protocol-call family pruned (schema 0.15.0)

- **`http_calls` / `grpc_calls` / `graphql_calls` removed from the edge-type registry (WI-hirud, ADR-0023 Phase 4b′).** These three `endpoint_shape` values were producer-migrated to canonical `calls` + `meta['protocol']` in WI-vumum-juvil; their now-dead registry entries (and the `x-deprecated` schema annotations) are pruned. No producer emitted them and every consumer set already reads canonical `calls`, so runtime behavior is unchanged — but a behavior map produced *before* the WI-vumum-juvil migration that still carries these edge types will no longer validate against the enum. **`SCHEMA_VERSION` 0.14.6 → 0.15.0** (minor bump per the ADR's Phase-4b removal contract). The `endpoint_shape` registry section drops 25 → 22.
- **`script_src` removed from the edge-type registry (WI-pumav Batch 0, ADR-0023 Phase 4b′).** The first long-tail `endpoint_shape` value to be pruned. It was already producer-migrated under INV-vavat — `html.py` emits canonical `references` + `meta['ref_construct']='script_src'` and no producer emits a `script_src` edge type — so its dead registry entry (and `x-deprecated` schema annotation) is pruned. Runtime behavior is unchanged; pruning it also discharges the WI-pusuv access_mode-census coupling that was deferred on it in `axis_meta_keys.py`. **`SCHEMA_VERSION` 0.15.0 → 0.16.0** (minor bump per the ADR's Phase-4b removal contract). The `endpoint_shape` registry section drops 22 → 21.
- **Four long-tail `endpoint_shape` edge types producer-migrated to canonical `references` (WI-pumav Batch 1a, ADR-0023 Phase 3).** `links_to` (Markdown), `uses_vocabulary` (SPARQL), `association` (Ruby ActiveRecord), and `build_tag_alternative_of` (Go build-tag alternates) now emit `edge_type="references"` with the construct flavor in `meta['ref_construct']` (`markdown_link` / `rdf_vocabulary` / `association` / `build_tag_alternative`); Ruby associations keep their existing `meta['framework_dispatch']='activerecord_association'`. Per the finalized audit-findings 0017 verdicts (the 4-lens ruling pass). This is the Phase-3 producer migration; the `x-deprecated` registry entries stay through the consolidated Phase-4b prune (no `SCHEMA_VERSION` change yet). Consumers already read canonical `references` (the strict drift linter bars any consumer reference to the old `endpoint_shape` values), so behavior is unchanged apart from the edge-type label + `ref_construct` meta.
- **`renders` producer-migrated to canonical `references` (WI-pumav Batch 1b, ADR-0023 Phase 3).** The shared view-template linker (`_view_template_core.py`, through which the Django / Laravel / Phoenix / Spring view-template linkers all emit) now emits `edge_type="references"` + `meta['ref_construct']='view_render'` alongside the kept `meta['detection_pattern']`, instead of `edge_type="renders"`. Phase-3 producer migration (registry entry pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change).
- **Four `endpoint_shape` template/mixin edge types folded to `includes` / `extends` (WI-pumav Batch 2, ADR-0023 Phase 3).** `includes_template` (Twig), `includes_class` (Puppet), and `uses_mixin` (Sass/SCSS) now emit `edge_type="includes"`; `extends_template` (Twig + Blade) now emits `edge_type="extends"` — each with the source construct in `meta['ref_construct']` (`template` / `puppet_class` / `sass_mixin`) and its prior meta (`template`/`class_name`/`mixin_name`/`form`) preserved. Phase-3 producer migration (registry entries pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change). Matches the canonical `includes` docstring, which already names mixins/includes across LaTeX/RST/Meson/Make/Ruby.
- **`crypto_flow` folded to `data_flows_to` (WI-pumav Batch 7, ADR-0023 Phase 3).** The crypto-flow linker (crypto write→read pairs) now emits `edge_type="data_flows_to"` + `meta['ref_construct']='crypto'` instead of `edge_type="crypto_flow"`, keeping its first-class `data_direction`/`channel` fields and `meta['detection_pattern']='crypto_api'`. This routes crypto flows onto the canonical ADR-0015 dataflow relationship (ADR-0038-adjacent — the direction lives in `data_direction`, not the type name). The `ranking.py` weight moved `crypto_flow: 0.8` → `data_flows_to: 0.8`, preserving the weight (the crypto linker is `data_flows_to`'s only producer). Phase-3 producer migration (registry entry pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change). **This completes the Phase-3 migration of all 21 long-tail `endpoint_shape` values (plus the earlier `script_src` prune) — the consolidated Phase-4b registry prune follows.**
- **`contains_routes` folded to `contains` (WI-pumav Batch 5, ADR-0023 Phase 3).** The controller-routes linker (controller→route span-enclosure) now emits `edge_type="contains"` (keeping `meta['framework_dispatch']='controller_routes'`); the `route` concept stays queryable from the dst node. Phase-3 producer migration (registry entry pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change).
- **Two `endpoint_shape` dispatch edge types folded to `dispatches_to` (WI-pumav Batch 4b, ADR-0023 Phase 3).** `invokes_callback` (Elixir/Erlang behaviour callbacks + Rails `before_action`/block callbacks, `meta['mechanism']='callback'`) and `signal_receiver` (Django `@receiver`, keeping `meta['framework_dispatch']='django_signal'`) now emit `edge_type="dispatches_to"`. Folding `signal_receiver` to `dispatches_to` (which is in the dead-code reachability set) keeps `@receiver`-decorated handlers reachable from their signal — the correctness reason the 4-lens ruling chose it over `event_publishes` (which would have stranded them as false dead-code positives). Phase-3 producer migration (registry entries pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change).
- **Four `endpoint_shape` call-family edge types folded to `calls` (WI-pumav Batch 4, ADR-0023 Phase 3).** `abi_call` (Solidity ABI, `meta['call_kind']='abi'` — not `protocol`, which is a closed wire-protocol enum), `caller_invokes` (Tauri IPC wrapper, `meta['protocol']='ipc'`), `kernel_launch` (CUDA kernel launch, `meta['mechanism']='kernel_launch'`), and `template_calls` (Vue template→method, `meta['mechanism']='template'`) now emit `edge_type="calls"`, each keeping its prior `framework_dispatch`/`detection_pattern` meta. Being genuine calls, these edges now participate in the `calls` consumer surfaces (taint / io-boundary / dead-code reachability, ranking weight 1.0) — the intended promotion. Phase-3 producer migration (registry entries pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change). CUDA's `edge_type = "kernel_launch" if is_kernel_launch else "calls"` conditional collapses to `"calls"` with the launch flavor in meta.
- **Four `endpoint_shape` dependency/ordering edge types folded to `depends_on` (WI-pumav Batch 3, ADR-0023 Phase 3).** `depends` (requirements.txt / BitBake — a pure `depends_on` synonym), `requires_resource` (Puppet `require`, `meta['ref_construct']='puppet_require'`), `base_image` (Dockerfile intra-file `FROM ... AS` stage dependency, `dockerfile_stage`), and `notifies_resource` (Puppet `notify`, `puppet_notify` + `meta['refresh']=true`) now emit `edge_type="depends_on"`. `base_image` unifies with its sibling `COPY --from` stage edge (already `depends_on`); Puppet `require`/`notify` fold onto the dependency graph with the refresh side-effect preserved in meta. Phase-3 producer migration (registry entries pending the consolidated Phase-4b prune; no `SCHEMA_VERSION` change). These edges pick up `depends_on`'s ranking weight (0.2, down from the 0.5 default) — a config/build-ordering edge is correctly weaker than a call.

#### Edge-type fold-tail audits recorded (docs)

- **Audit-findings 0016 (resolver/OpenAPI/RPC family) and 0017 (endpoint-shape long-tail) filed (WI-sumik, WI-pumav).** The two dormant per-value verdict passes for the endpoint_shape→relationship fold tail (ADR-0023 §6). Both find every value FOLD (zero CANONICAL): 0016 folds the four `pending_classification` resolver values onto `implements`/`references` (with an `openapi_implements` direction wrinkle and an `implements_rpc` consumer-coupling caveat); 0017 records CANONICAL-vs-FOLD verdicts + a proposed per-PR migration batching for the 22 long-tail values (flagging three docstring-disagreements, two target-ambiguous values, and one borderline-CANONICAL). Verdict recording only — the FOLD migrations (beyond `script_src` Batch 0) remain tracked follow-on work under WI-kivip.

#### Meta-layer honesty

- **Per-run and limits reporting lists are present only when non-empty (INV-virik).** `analysis_runs[].{skipped_passes, failed_files, warnings}`, `limits.{failed_files, skipped_languages, truncated_files}`, and `limits.supply_chain.{classification_failures, ambiguous_paths}` are now omitted when empty instead of serialized as an always-`[]` list on every record — so their **absence** honestly reads as "nothing to report" rather than a hollow empty list that reads as "clean" on a pipeline-health consumer. An empty `limits.supply_chain` therefore serializes as `{}`. Matches the existing `partial_results_reason` / `max_tier_applied` omit-when-empty precedent; the fields were already optional in the schema (no version bump). `limits.skipped_passes` (the populated provenance surface) and `limits.test_files_excluded` stay always-present.
- **`nodes[].quality` is omitted when null (INV-nuzal).** The node-level `quality` field has no producer (unlike `edge.quality`, it is not derived from confidence) and serialized as a universally-`null` key on every node — schema bloat that predicts no per-record variation. It now follows the INV-virik omit-when-empty pattern: emitted only if a producer sets it, absent otherwise. This closes the last residual of INV-nuzal (`edge.provenance` was already dropped and `edge.quality` is derived). The field was already optional in the schema (no version bump); its schema description now states it is unpopulated rather than implying it carries data.
- **`resolution_quality='type_inferred'` no longer mislabels the give-up branch (WI-javus).** Python's unresolved method-call fallback stamped `meta.resolution_quality='type_inferred'` *unconditionally* — including on the branch whose own comment reads "type cannot be inferred" (an untyped / duck receiver, INV-fahub-biased to unresolved). On pretix that mislabeled ~62% of the `type_inferred` population (7920 edges): no receiver hint, a bare `python:external:` placeholder dst, yet claiming a successful type inference — so a consumer branching on the string read the give-up case as a success. The label is now *derived* from whether the receiver-hint chain actually established a type: `self` (enclosing class), an annotated/constructed var, or a bare local class name keep `type_inferred`; the give-up fall-through carries **no** `resolution_quality` (the field names the resolution pathway per spec §903, and here there was none). pretix give-up mislabels 7920 → 0; every remaining `type_inferred` carries a real inference signal. The field was already optional (no version bump).

#### Confidence derivation (ADR-0039)

- **Analyzer edge `confidence` is now derived from the inference pathway, not hardcoded.** The derivation table grew from 10 to 75 pathways in one authoritative `_CONFIDENCE_SEEDS`; `Edge.create` derives from `evidence_type` when `confidence=` is omitted; and 218 analyzer sites dropped their hardcoded values, so all 9,638 seeded analyzer edges now carry the derived value. Linker edges keep explicit confidences (a separate match-quality model). `confidence_model` v1 → v2.
- **Containment `naming_convention` edges now derive in-band confidence, ending the reliability inversion (ADR-0039 ruling 1; WI-vakuh, WI-lutad).** The containment linker's Phase-1 name-parse heuristic hardcoded `confidence=1.0` on ~18,388 `contains` edges (16.6% of all edges) — above the 0.95 linker ceiling, and *outranking* the structurally-certain `span_overlap` (0.90), so a consumer trusting confidence treated the error-prone heuristic as gold. `naming_convention` is now seeded at **0.85** in the registry (below `span_overlap`, in-band) and the producer derives it instead of the literal, so those edges carry 0.85 with `confidence_source=evidence_derived` and reliability ordering is restored (certain span_overlap 0.90 > heuristic naming_convention 0.85).
- **type-hierarchy `dispatches_to` confidence is separated from its ranking dampener (ADR-0039 rulings 1 & 3; WI-botif).** The type-hierarchy linker fused two quantities into `confidence`: a `1/√N` fan-out dampener (WI-kabom) and a 0.30 test-file penalty (WI-supok), driving published confidence as low as ~0.094 — 4× below the documented 0.40 linker floor on 313 edges. A dispatch to an override is a real relationship, so `confidence` now derives the flat in-band **0.85** `type_hierarchy` base (`confidence_source=evidence_derived`), and the dampener + penalty relocate **intact** to the new `rank_score`. The ranking filter (`filter_edges_for_ranking`, invoked at `min_edge_confidence=0.5`) re-keys from `confidence` to `rank_score`, so high-fan-out dispatch edges are still demoted out of centrality — the WI-kabom signal survives unchanged while confidence stops undershooting the floor.
- **Spec §12 reconciled to the implemented confidence model (ADR-0039 ruling 5 Stage B, prose).** The confidence-scoring section no longer lists `naming_convention` among the 1.0-emitting pathways (now derived), no longer calls the `type_hierarchy` dampener "not yet derived" (now on `rank_score`), and no longer says `confidence_source` / `rank_score` "remain later stages" (implemented); it now documents both new fields and notes that entrypoint `confidence` is pure detection reliability with the `connectivity_based` (0.50) / frontend (0.05) kinds deliberately below the 4-tier floor. A stale Appendix-C line that called the model "planned, not implemented" (contradicting §12's "Implemented") is corrected. The generated per-evidence-type table + freshness gate is deferred (follow-up filed).
- **Entrypoint `confidence` is now pure detection reliability; ranking adjustments move to `Entrypoint.rank_score` (ADR-0039 ruling 3; WI-lutad, WI-dojor).** Every entrypoint ranking adjustment — the test (×0.1) / utility (×0.5) / vendor (×0.3) / nested-Go-cmd (×0.5) penalties, the library-export demotion (×0.1, the "forgejo" fix), and the in-degree (cap +0.35, WI-dojor) and out-degree (cap +0.25) connectivity boosts — used to mutate `confidence` in place, moving nearly every published value off its documented tier (only 2 of 104 landed on a tier; 13 breached the 0.99 ceiling, 81 fell below the 0.70 floor). They now accumulate on `rank_score` (initialized from confidence, so the ranking signal is unchanged), and the entrypoint sort, the `MIN_ENTRYPOINT_CONFIDENCE` filter, the sketch entrypoint sorts, and the slice auto-entry / `--min-confidence` seed selection all re-key to `rank_score`. Published entrypoint `confidence` is now the construction-tier detection reliability (declared 0.99 / framework 0.95 / concept 0.90 / convention 0.80 …; the deliberate low-confidence `connectivity_based` 0.50 and frontend-suppression 0.05 kinds sit below the 4-tier floor by design).

#### CLI read-view contract

- **Read-view JSON single-sources one envelope and one version.** ~6 inlined envelope sites now flow through `add_schema_envelope()`, and six views single-source `READ_VIEW_SCHEMA_VERSION`; the spec documents the three independent version axes (format, tool, per-view). `verify-claims --json` now emits a versioned `{schema_version, view, verdicts, …}` object instead of a bare array (breaking for array-parsing consumers).
- **`explain` errors on an ambiguous symbol name** instead of silently iterating every match, via the shared ambiguity check `slice` already used (escapable with `--first` / `--language` / `--file`).
- **`io_primitives` catalog completeness is now disclosed (WI-najil).** The `io-boundaries`, `verify-claims`, and `slice --io-boundary` consumers each emit a one-line stderr warning when a query targets a language whose `io_primitives/<lang>.yaml` ships `status: in_progress` — so a zero-match result for that language is not silently read as "no I/O in this code". Eleven of the fourteen catalogs are `in_progress` (only python/rust/erlang are `complete`); the shared, tested helper is `io_boundary.in_progress_languages`, and the catalog `status` field is now documented in the spec. Unsupported languages keep the separate `is_supported=False` signal (INV-javam).

#### Projection views

- **`compact` defaults to centrality-ranked selection** (most-important-first, matching `sketch`) instead of connectivity-aware; `--connectivity` opts into the old behavior (design ruling D12).

#### Edge semantics & risk vocabulary

- **`access_mode` gains a declared per-edge-type applicability matrix** — the classifier no longer stamps spurious values on ~6,006 edges where access is meaningless. FFI-bridge direction-smuggling moves off `access_mode`/`dest_access_mode` onto a new `data_direction` meta key (`src_to_dst` / `dst_to_src` / `bidirectional`, ADR-0038).
- **`high_risk` narrowed to a display-only `subprocess` marker.** Destructive-filesystem and network-outbound entries leave `HIGH_RISK_PRIMITIVES` — that risk is the taint model's job (ADR-0016).
- **`Symbol.origin` synthesis values declared legitimate synthetic pass-ids** (their synthesizers now emit real `AnalysisRun`s), retiring a proposed `synthesis_mechanism` field-split (ADR-0044).

#### Metrics & introspection

- **`metrics.languages.<lang>.files` is now populated (WI-ninaj).** The per-language dict carried `{nodes, edges}` but never a `files` count, so `metrics.languages.<lang>.files` read `0`/absent on every language despite a non-zero `metrics.total_files`. It now counts file-kind nodes per language (`== count(node.kind=='file' AND node.language==lang)`), node-derived and consistent with `total_files` (the distinct node-path count) rather than the over-counting `profile.languages` sum. The per-language `files` sum reconciles to the total file-kind node count.
- **`metrics.total_files` no longer counts the `<external>` sentinel as a file (INV-mozaf).** The distinct-node-path count folded in the single `<external>` placeholder path carried by `external_symbol` boundary nodes, inflating `total_files` by exactly 1 on any repo with external references (the last residual of the 296→1 accounting-gap fix). It now excludes the sentinel, so `total_files` equals the count of real path-bearing source files. `debug.unique_paths_in_analysis` is corrected in lockstep.
- **`io-boundaries` `total_io_edges` redefined to the verified surface** — it excludes `external_potential` (which over-counted ~28×), disclosed separately as `external_potential_edges`; text and JSON now agree (io-boundaries view schema 1.0 → 2.0).
- **`is_exported` now requires public `visibility`** and `modifiers` drops its visibility terms, so consumers read exportedness and visibility from one field each without per-language polarity logic.
- **`sketch_precomputed.centrality_scores` renamed `additional_file_centrality_scores`** to reflect that it measures additional-file symbol-mention frequency, not graph centrality (internal cache; no public-schema change).

#### Dev workflow

- **`auto-pr` / `merge-pr` recover from Codeberg DB desync.** A branch-push fallback and a merge resync-retry recover from AGit `refs/for/` hook failures, verifying success by PR record or git ground truth; the failure points also print a terse recovery recipe.

### Deprecated

- **`Edge.quality` (`{score, reason}`) is deprecated (ADR-0039 ruling 4; WI-humok / WI-riguh).** It carries zero independent signal — `quality.score` is `round(clamp(confidence), 3)` on 110,533/110,533 verification-corpus edges and `quality.reason` encodes the emitter mechanism, not a confidence tier. The `deprecated` annotation lands in `docs/schema.json` now (SCHEMA_VERSION 0.14.5 → 0.14.6); the field is still emitted for this release and will be removed the next, per the one-version deprecation window. Read `confidence` + `confidence_source` + `is_resolved` instead. (`Symbol.quality` is a separate field, not in scope.)

### Documentation

- **Documented three intentional schema divergences, closing INV-nihug / WI-gabos / WI-palon (INV-luhur children).** All three are correct-by-design, not bugs; the spec/schema now say so explicitly rather than leaving readers to infer or (in one case) mis-routing them:
  - **`skipped_passes` two-channel semantics (INV-nihug).** `limits.skipped_passes` is the single authoritative record of pass-level skips (a pass that never ran — no files matched, missing grammar, or crashed); the per-run `analysis_runs[].skipped_passes` is an unpopulated legacy mirror (a skipped pass has no `analysis_runs[]` entry to attach to). Corrected the spec §9/§17 prose and the schema description, which previously routed readers to the per-run field for outputs that actually land in `limits`.
  - **`supply_chain_summary.derived_skipped` carries `{files, paths}` not `{files, symbols}` (WI-gabos).** Tier-4 derived artifacts are excluded from analysis and emit no symbols, so a `symbols` count would be a structurally-always-0 field (cf. ADR-0040); `paths` (a capped sample of *which* files were skipped) is a distinct quantity named per ADR-0039. Documented in spec §14.
  - **`profile.languages[L].loc` vs `node.line_span` (WI-palon).** `profile.loc` is the authoritative per-file SLOC (counted once); `node.line_span` is a per-symbol span that overlaps across nesting and is not summable (`Σ` ≈ 1.66× the file total). Documented in spec §profile.
- **Corrected identity-field documentation drift (identity-vocabulary concept audit, 2026-07-15).** A Fundamental Concept Audit of the ~15 `# axis: identity` fields found the axis conceptually clean but with stale/circular docstrings on the structural-identity family:
  - **`Symbol.fingerprint` docstring was factually wrong** — it read "Content hash of source bytes (sha256)"; the producer (`fingerprint.py`, `hgfp2:` scheme) computes a whitespace/comment-invariant *structural parse-subtree* hash. The "source bytes" text described the ADR-0032-demolished Format-1 scheme. Fixed to match the (already-correct) schema.
  - **`Symbol.shape_id` was defined circularly** as "Structural implementation fingerprint" (its sibling's name); the docstring + schema now state the distinguishing rule — shape_id **strips** identifiers/literals (within-language skeleton), fingerprint **keeps** them — and note shape_id is a strict coarsening of fingerprint with no current internal consumer.
  - **`Symbol.id` schema description** now states its location-addressedness (churns on move/rename), symmetric with `stable_id`'s caveat, pointing readers to `stable_id` (edits-surviving) and `fingerprint` (rename-tracking).
  - **`Edge.derived_from`** docstring now flags that it is provenance (PROV wasDerivedFrom), not identity-of-the-edge — it carries `# axis: identity` only because it holds identity *references*, and does not participate in `edge_key`/dedup.
  - **`AnalysisRun`** docstring now notes that `run_signature` / `repo_fingerprint` / `config_fingerprint` / `pass_version` have no internal readers (serialized for external cache/PROV tooling only), while `execution_id` + the `origin_run_id` FK are load-bearing.
  - **ADR-0032** gained a scheme-drift note: the retained Format-2 hash shipped as `hgfp2:<16-hex>`, not the `hgfp1:`/70-char form the ADR describes (revised after it shipped).
- **Documented three more identity/tier legibility divergences, closing WI-tisar / WI-niboh / WI-nibul (all correct-by-design; the docstrings/schema/spec now say so).**
  - **`stable_id` and `shape_id` share the `sha256:<16hex>` surface (WI-tisar).** The `sha256:` prefix names the hash *algorithm*, not the identity axis; the two are discriminated by field name (and their top-level `stable_id_scheme` / `shape_id_scheme` descriptors), not by an in-value prefix, and their value-spaces are disjoint (0 overlap). Unlike `fingerprint`'s `hgfp2:` *version* tag or the edges' `edge:` / `edgekey:` *namespace* tags, these two need no in-value discriminator because they are always field-qualified. Documented in `ir.py` + the schema descriptions with a consumer caution not to join on bare hash values across the two axes.
  - **`Edge.edge_key` is the Edge counterpart of `Symbol.stable_id` (WI-niboh).** Both endpoints were already documented; added the missing cross-reference — `edge_key` is the structural-identity / cross-pass dedup key (line-insensitive, survives regeneration) while `edge.id` is the per-instance identity, mirroring `Symbol.stable_id` / `Symbol.id`. Named `edge_key` for historical reasons; renaming to `stable_id` would be a breaking schema change not worth the churn.
  - **`metrics.by_supply_chain_tier` omits the `derived` tier that `supply_chain_summary` carries (WI-nibul).** Intentional: `by_supply_chain_tier` enumerates only tiers with ≥1 analyzed node (the analyzed tiers 1-3); tier-4 / derived is excluded from analysis (§14), emits no nodes, and surfaces solely as `supply_chain_summary.derived_skipped`. An always-empty `derived` bucket here would be a structurally-always-0 field (ADR-0040). Documented in spec §metrics + a producer comment in `metrics.py`.
- **Corrected the `edge.meta.io_boundary` axis description to reflect the WI-fakuv / WI-puvun on-demand reframe (INV-lifik investigation).** The `io_boundary` meta-key axis spec claimed the field is "Set by io_boundary catalog," but per the reframe the full value-space (`fs_read` / `net_send` / …) is **consumer-time-derived** by `compute_boundary_map` from the `io_primitives` YAML catalogs and is **not persisted** — only the `io-boundaries` / `verify-claims` / `slice --io-boundary` paths stamp it in memory. What survives into a persisted behavior map is only analyzer producer-hints — currently just bash's `command_launch` (bash has no `io_primitives` catalog, so `bash.py` stamps it directly), which is why the persisted field reads as `command_launch`-only. The axis description now says so, directing consumers who need the full classification to run `compute_boundary_map` rather than read the persisted meta key.
- **Documented the `is_test_file` concept boundary (WI-popok fundamental-concept-audit KEEP verdict).** Two predicates share the name `is_test_file` but answer different questions: the NARROW supply-chain role flag `Symbol.is_test_file` ("is this test *code*?", spec §14 — examples/benches/fuzz carry other roles) vs the BROAD `paths.is_test_file` ranking/scan heuristic ("deprioritize as a non-production entrypoint / filter from a slice?" — also covers mocks/, fakes/, fixtures/, testdata/, benches/, `_test.<any-ext>`, `spec_*`, `test-*`). WI-popok proposed folding the entrypoint detector's use of the second onto the first; a fundamental-concept audit found them distinct-purpose (all four leakage tests pass → KEEP verdict) — the entrypoint predicate is *broader*, not a coarser re-derivation, so a swap would silently stop deprioritizing mock/fixture/benchmark entrypoints and flood the entry list. Documented the boundary at the entrypoint penalty site (`entrypoints.py`), both boundary docstrings (`Symbol.is_test_file` narrow / `paths.is_test_file` broad), and disambiguated the two functions that each called themselves "canonical" (`framework_patterns.py`, `discovery.py`); annotated ADR-0041 §2 in place (its WI-popok illustration reflected the item's original 2026-06-04 framing, since inverted by the code — the directness ruling is unaffected); added a regression guard test that doubles as the executable re-evaluation trigger. Advances the INV-numat drain and removes a false member from META-bifif. No behavior change.
- **Documented the `evidence_type` ⊥ `is_resolved` boundary + added the META-bifif property-test template (WI-molit KEEP verdict).** WI-molit claimed the `ast_call` (98.5% unresolved) vs `ast_call_direct` (8.3%) distinction redundantly encodes `is_resolved` (a "resolution smuggled into the axis" violation). A rigorous 3-lens audit (ADR/governance + spec + exhaustive consumer census) returned **KEEP**: `evidence_type` names the inference *pathway* (ADR-0028 — which explicitly accepts `ast_call_direct` as "a pure inference pathway"), resolution lives solely on `Edge.is_resolved` (ADR-0037), the two labels are distinct call-*construct* pathways (method vs direct) that appear on *both* resolution states, the 98.5%/8.3% split is a benign common-cause correlation (method calls are harder to resolve), and **no consumer reads the label as a resolution proxy** (all read `is_resolved`; the 0.40/0.50 confidence delta is a genuine pathway signal). Added two property tests in `test_evidence_types.py` (the reusable "no-consumer-re-derives" template for the resolution verdict — a general name-purity axiom + data-model orthogonality; the confidence split was already guarded in `test_confidence.py`). WI-molit → `wont_do`; INV-numat drain −1; a second false META-bifif member removed. No production-code change (the axis was already clean). Full audit: `~/hypergumbo_lab_notebook/concept-audit-evidence_type_is_resolved_07172026.md`.
- **Documented the `access_mode` / `data_direction` Edge.meta applicability matrix in spec §9 (closes INV-tibob).** The spec's Edge.meta field catalog previously omitted `access_mode`, leaving its "0% coverage on 12 of 17 edge types" reading as a gap. It is the *declared* applicability (ADR-0038 ruling 2): `access_mode` (`read`/`write`/`mutate`) applies only to the data-carrying edge types (`calls`/`references`/`module_attr_ref`/`event_publishes`); on structural relationships the question does not arise and the key is absent by design. The `MetaKeySpec` (`axis_meta_keys.py`) is the schema of record, distinguishing "None = missing data" from "None = does not apply". Emission was already wired/gated/bakeoff-verified; this closes the documentation residual.
- **Authored the META-bifif "no-consumer-re-derives" umbrella property test.** META-bifif's invariant — consumers honor a granular producer verdict rather than re-deriving a coarser one — spans three verdicts whose per-verdict guards were distributed across test files (resolution in `test_evidence_types.py`, language in `test_spec_validator_smoke.py`, test-code in `test_entrypoints.py`). The umbrella now has a single documented home (`test_evidence_types.py`) stating the contract + the reusable premise-check pattern, with the language-verdict META-bifif assertion (a fabricated `Symbol.language` on a synthetic `protocol_origin` node is flagged), the test-code KEEP-distinct exception made explicit (the entrypoint ranker's broad `paths.is_test_file` is a distinct concept, not a coarser re-derivation — WI-popok), and a completeness guard so the umbrella's three-verdict coverage cannot drift silently. All three verdicts are clean/guarded; the gate flip is the convergence call. (META-jalur's finalize round-trip reconcile test already exists in `test_finalize_roundtrip.py`.)

### Fixed

#### Manifest CLI entrypoints no longer dropped from the default behavior map (WI-papag)

- **pyproject `[project.scripts]` / console-script CLI entrypoints now survive the default-view noise filter and are detected.** The Phase-D noise filter treated *every* `entry_role=script` file symbol as noise, which silently erased entrypoint-bearing console-scripts before `detect_entrypoints` could see them (the ADR-0043 §5 "C3" defect) — e.g. a package whose sole declared CLI command (`mypkg = "mypkg.cli:main"`) vanished from its behavior map. The predicate (extracted from a `run_survey` closure to the unit-testable `noise_filter.is_noise_symbol`) is now subset-refined by `meta["entry_point"]`: a script node that declares a code target (detected as `CLI_COMMAND` @0.99 `manifest_declared`) is **exempt** and survives, while a bare npm `package.json` run-script (`build`/`test`/`lint` — `meta["command"]`, no `entry_point`, never detected as an entrypoint) is still **filtered as noise**. Reconciles audit-findings 0005 (filter npm run-scripts) with ADR-0043 §5 (exempt entrypoint-bearing scripts) by subset — both governance docs now stand, each scoped to its correct population.

#### Cross-language call-graph precision — unresolvable-receiver misbinds (INV-fahub)

- **Scala: an unresolvable-receiver call no longer misbinds to an arbitrary same-named internal def (WI-bihit).** A call whose target class couldn't be resolved previously fell through to a bare short-name lookup and confidently bound to the first same-named method — the `copy`/`setTo` funnel. Real-repro validation (docspell) showed the dominant form is a **bare** call: an implicit-`this` case-class `.copy()`, or a chained-receiver call (`expr.filter(…).map(…)`) whose receiver token is dropped, which suffix-matches an unrelated class's method (a magnet — dozens of files → one `FileCopyTask.copy` / `ColumnOps.setTo` / collection `.map`). The Scala analyzer now withholds every bare call that resolves only to a class-member `method` without scope evidence — a weak short-name *suffix*/ambiguous match, or a same-file `local_symbols` match to a *different* class's method — emitting an honest unresolved `calls` edge (`is_resolved=False`) stamped with the caller's `enclosing_class`. The shared `inherited_calls` **Site-1** walker then recovers the call iff the method is on the enclosing class's linearization (a genuine inherited implicit-`this` call → `ast_call_inherited`), while a cross-class magnet stays external. A same-enclosing-class implicit-`this` call, a free-function/object target, and a present-but-untyped receiver call (annotation-typed vals / constructor params → `receiver_type_hint` for Site-2) still resolve as before. On docspell, cross-class `copy`/`setTo` misbinds drop **242 → 0** and ~3,000 fabricated magnet call-edges (`map`←315 files, `flatMap`←159, …) become honest externals (38 genuine inherited calls recovered by Site-1); conforms to **INV-nilud** / **INV-nogof** (withhold, never pick-first). The magnet is fleet-wide (the shared `NameResolver` suffix match); Swift and D carry the same funnel and follow, then the remaining method-dispatch analyzers.
- **Swift: the same bare→method magnet gating (WI-votar).** As in Scala, the dominant `create`/`delete`/`run` funnel on the real repro (VernissageServer, 302 misbinds) was a **bare** call — a Vapor route builder (`.delete("path", use:)`), implicit-`self`, or a chained receiver — that suffix-matched an unrelated type's method @0.80 (magnet: `delete`←99 files → `ActivityPubClient.delete`, `create`←73, `run`←61). Swift's resolver path now applies the shared `defer_bare_method_call` gate: a bare call resolving only to a class-member `method` on weak suffix/ambiguous evidence is withheld and stamped with the caller's `enclosing_class`, so the `inherited_calls` Site-1 walker recovers it iff the method is inherited by the enclosing type (`ast_call_inherited`) and a cross-type magnet stays external. Typed-receiver calls (`let c = ActivityPubClient(); c.create()`) and function/method-parameter receivers still resolve via the type-qualified path (WI-votar recall recovery). On VernissageServer the magnet is eliminated (302 → 0 high-confidence cross-type misbinds; 23 inherited calls recovered by Site-1).
- **Shared `defer_bare_method_call` helper (INV-fahub, `analyze/base.py`).** The bare-call magnet decision — bind directly to a free function / object / same-enclosing-class method (implicit `this`/`self`), else withhold a weak-suffix cross-class method to the `inherited_calls` Site-1 walker — is extracted next to `make_unresolved_edge` as one shared predicate (generalized with a `separator` param so `Owner::method` languages parse their owner correctly). Scala and Swift both route their bare-call resolution through it; it is the substrate for the fleet-wide rollout below (the magnet lives in the shared `NameResolver` suffix match, so it is a fleet-wide pattern, not a per-language one).
- **Fleet-wide rollout of the magnet gate (INV-fahub).** The bare→method magnet gate is now wired into eleven more analyzers — **go, rust, php, js/ts, csharp, groovy, dart, lua, zig, cpp, objc** — through the shared `defer_bare_method_call` helper. Each analyzer's bare-call `resolver.lookup` path now withholds a weak short-name (suffix / ambiguous) bind to a cross-class `method` — emitting an unresolved edge stamped with the caller's `enclosing_class` rather than a false high-confidence `calls` edge — while free functions/objects, same-enclosing-class implicit-`this`/`self` methods, and strong exact / import-scoped matches still resolve. Per-language scoping is preserved where the resolver path also carries genuine routing evidence (e.g. Go gates only *truly* bare calls, leaving package-qualified `pkg.Foo()` selectors untouched). This removes the shared-`NameResolver` suffix magnet across the method-dispatch fleet.
- **Site-1 MRO walkers for the fleet languages (INV-fahub).** The `inherited_calls` Site-1 walker is now registered for **go, rust, php, javascript, typescript, csharp, cpp, objc**, so a bare inherited implicit-`this`/`self` call the gate defers is *recovered* (resolved to the ancestor's method, `ast_call_inherited`) when that method lives on the enclosing class's MRO — and left honestly external otherwise. Walker choice follows each language's inheritance shape (single-superclass-then-interfaces for php/js/ts/csharp/objc; insertion-order BFS for go struct embedding, rust trait impls, C++ multiple inheritance), reusing the proven walker functions. The language-agnostic `inheritance` linker turns each analyzer's `base_classes` / `implements` metadata into the edges the walkers traverse; **dart/lua/zig are deliberately unregistered** — their analyzers model no class inheritance, so nothing is recoverable there. A parametrized Site-1 test confirms recovery for all eight; on any given repo the walker fires only where a deferred call is *genuinely* inherited (e.g. ghostfolio's deferred TS calls are all cross-class magnets with no concrete inherited target — correctly 0 recovered).
- **Untyped-receiver-method magnet demotion at finalize (INV-fahub, WI-makiz).** The fleet gate above closes the *bare-identifier* call path, but validation on real Go/Rust/D repos surfaced a second funnel the per-analyzer gates miss: an **untyped-receiver method call** (`d.Val()`, `x.Parse()`) whose receiver type can't be inferred resolves — via a separate selector/short-name path — to an arbitrary same-named internal `method`, so dozens of unrelated call sites collapse onto one `Owner.method` and poison its centrality. A single language-agnostic detector (`receiver_blind_magnets.find_receiver_blind_magnets`) now measures these on the reconciled graph, and a new **finalize sub-step (6c, before the ADR-0037 edge-resolution verdict)** demotes the two cleanly-harmful sub-classes to unresolved-external — a **production→test-helper** misbind (a bind to a `method` defined under `testutils/`/`fixtures/`/`mocks/`/`benches/` from a non-test caller — e.g. alertmanager's `Collector.Add`) and a **stdlib-interface-method shadow** (`Close`/`Parse`/`Len`/… on an untyped receiver — e.g. `Template.Parse`) — by redirecting the edge's `dst` to `{lang}:external:0-0:{method}:unresolved` (so the ADR-0037 verdict derives `is_resolved=False`) and stamping `resolution_quality=ambiguous`. Correct-but-unprovable binds are deliberately kept: the snake_case trait-dispatch funnel (Rust `x.next()`→`Red::next`) needs real type resolution (ADR-0012) and pays a recall cost if blindly gated, so it is left resolved; same-module builder binds (`Args.append`) and test→test-helper calls are untouched. Rust `Type::method()` **scoped** calls are also now stamped `receiver=qualified` at the analyzer (the call site named the type), so a correctly-resolved associated-function call is no longer counted as receiver-blind. On the real repro cohort: production magnets drop (alertmanager 41→32, rodio's `benches/` `TestSource::*` misbinds demoted) with **zero collateral** (resolved-call count falls by exactly the demoted-edge count), while the correct-but-unprovable residual is preserved.
- **Rust qualified-receiver marking extended to the survey `method_resolver` fallback (WI-fazaj).** The `receiver=qualified` stamp above covered the analyzer's Strategy-1 scoped-call sites; a `Type::method()` call that instead resolves through the cross-package survey `method_resolver` / bare-name fallback (e.g. rodio `Red::new`←5, `Decoder::try_from`←3) now also carries the stamp, so it is no longer counted as a receiver-blind magnet in the raw `find_receiver_blind_magnets` diagnostic. Cosmetic measurement-precision only — these are correct binds the durable INV-fahub gate already ignored (INV-fahub stays satisfied); the branch carries `# pragma: no cover` because it is reachable only through the full survey pipeline (verified unreachable across 8 isolated `analyze_rust` scenarios — cross-file, nested modules, `use` aliases, generics, `::new` ambiguity, re-exports).
- **Durable INV-fahub gate in `validate_ir` (WI-makiz).** A new corpus-wide `spec_validator` check (`no_harmful_receiver_blind_magnets`, disclosed in the wired-checks manifest) asserts the finalized graph carries **no un-demoted harmful receiver-blind magnet** — sharing the exact `find_harmful_magnets` predicate with the finalize demotion, so the two can never drift. Because the demotion (sub-step 6c) runs before `validate_ir` (sub-step 10), the gate is green on every clean survey (self-corpus: 3645 kept correct-but-unprovable magnets, **0 harmful**), but fires an `error` if the demotion ever runs out of order, a new producer path emits a harmful magnet the pass missed, or the shared detector develops a gap — the standing regression teeth behind the refined INV-fahub criterion (no external/test-helper/stdlib-interface magnet survives; the trait-dispatch residual stays ADR-0012 scope).

#### Analyzer catalog completeness (WI-didil)

- **Every registered analyzer now resolves to an `analysis_runs[]` entry OR a `limits.skipped_passes[]` record — no analyzer silently vanishes.** Analyzers whose declared language is absent from the taxonomy (`matlab`, `meson`, `puppet`, `racket`, `robot`, `scheme`, `scss`) bypass the file-presence pre-filter and are dispatched defensively; with no matching files each returned a bare `run=None` result that the orchestrator chokepoint (`collect_analyzer_result`) silently dropped — recording neither an AR nor a skip (the branch was even mis-marked `# pragma: no cover` "shouldn't happen", though it fires on every run). The chokepoint now records a `{"pass": <name>, "reason": "no files matched"}` skip for an empty `run=None` result, honouring a self-declared `skip_reason` when present. This also closes a latent drop of grammar-unavailable skips whose result carried `run=None`. On the self-corpus the analyzer catalog went from 8 uncovered passes to 0. (The reverse direction — every AR references a known pass — was already enforced by `spec_validator._check_axis_conformance`.) Non-analyzer passes (linkers/synthesis) are out of scope: a linker with no applicable targets is a correct no-op, not a skipped pass.
- **The `rust_analyzer` opt-in SCIP backend self-declares its skip reason** (`"rust-analyzer backend not enabled"` when the gate is off; `"rust-analyzer backend produced no output"` on a WI-nohah fall-through) instead of returning a bare empty result — so it is recorded honestly rather than mislabelled `"no files matched"` (the repo may well contain `.rs` files; the backend simply did not run).

#### Compact / tiered views no longer collapse to ~1 symbol at small budgets (WI-pohuf)

- **The `compact` view and token-budget tiers (`survey.4k.json` / `.16k.json` / `.64k.json`) now emit a slim per-node projection.** They previously serialized the full ~24-field survey node (identity hashes `stable_id`/`shape_id`/`fingerprint`, provenance `origin`/`origin_run_id`, the ~200-byte `supply_chain` block, `meta`, and other internals) at ~250 tokens each, so at a 4k or 16k budget the post-selection shrink loop trimmed the map down to a single symbol. The new `compact_node` projection keeps only the fields a consumer needs to understand and navigate a symbol — `id`, `name`, `qualified_name`, `kind`, `language`, `path`, `span`, `signature`, `docstring` (plus the annotated `centrality`) — dropping identity/provenance/supply-chain internals that live in the full survey. On self-corpus DEEP repos this lifted the 4k/16k/64k node counts from `1/1/59` to `4/31/142` (apollo-server) and `1/1/83` to `5/38/190` (pretix).
- **Token-budget tiers now re-project `features[]` onto the retained node set** (mirroring the compact path), instead of copying the full `features[]` wholesale. That field was frequently the single largest part of a tier — ~25k tokens on apollo-server (a lone feature referencing thousands of nodes) — and, because the shrink loop only removes nodes, it kept the map far over budget no matter how many nodes were trimmed. Re-projecting it (before the shrink loop so the token estimate is accurate, and again after) keeps the tier within budget. This also restores compact/tiered containment monotonicity on the large-symbol repos where it had degraded.

#### Python `shape_id` no longer collides across symbol kinds (WI-linon)

- **Structurally-trivial Python symbols of different kinds no longer share a `shape_id`.** The Python shape hash (`_compute_shape_id`) omitted the symbol kind and mis-branched `async def`: a module-level `def f(): pass` and a class method `def m(self): pass` are both `ast.FunctionDef` (with `self` absent from the body) and hashed identically, and `ast.AsyncFunctionDef` — not being a subclass of `ast.FunctionDef` — fell into the `class` branch, so a docstring-only `async def` shared a `shape_id` with a docstring-only `class` (the observed `sha256:44283d00…` cluster: 16 Exception classes + one async method). The hash now folds the symbol kind (`class`/`method`/`function`) and the concrete AST node type into its prefix, discriminating kind and sync-vs-async while still clustering genuine same-kind structural clones — `shape_id`'s one non-redundant capability over `fingerprint`. Unblocks the structural-clone / refactoring-lead consumer (WI-vogij), which would otherwise emit false clones.
- **`shape_id_scheme` bumped `hypergumbo-shapeid-v2` → `hypergumbo-shapeid-v3`.** Every Python `shape_id` value changed, so the (global) scheme identifier bumps per the spec §6 mandate ("any change that alters computed values bumps the scheme"). `shape_id` is not part of `stable_id`, so identity/`stable_id` values are unaffected; `SCHEMA_VERSION` does not change (the scheme identifier is not a schema-version field). The tree-sitter shape path (other languages) is unchanged.
- **C# body-bearing symbols now carry a `shape_id` (WI-lutob, csharp facet).** `CSharpAnalyzer.analyze()` reimplements the extraction pipeline and never ran the base-class shape_id auto-stamp loop, so every C# `method`/`class`/`struct`/`constructor` shipped `shape_id=None` — the structural-clone key the spec marks done for C# silently did not exist (surfaced by a DEEP cohort parity check, 0/105 on sherpa-csharp). The override now stamps `shape_id` from its already-populated `node_for_symbol` (105/105 on sherpa-csharp; values are new, so no identity churn — `shape_id` is not part of `stable_id`). This broadens `repeat-finder`'s reach to C#. The sibling Solidity/WGSL facets are closed by the next entry.
- **Solidity and WGSL body-bearing symbols now carry a `shape_id` (WI-lutob, closing the item).** Both analyzers never populated `node_for_symbol`, so the base-class auto-stamp loop had nothing to stamp and every Solidity `function`/`constructor`/`contract`/… and WGSL `function`/`struct` shipped `shape_id=None` — invisible to `repeat-finder` (which groups by `(language, shape_id)` and drops None). Contrary to the item's original framing, **no shared/extracted shape_id helper was needed**: both are ordinary `TreeSitterAnalyzer` subclasses that do *not* override `analyze()`, so populating `node_for_symbol` at their symbol-construction sites (Solidity's `add_symbol`; WGSL's function/struct extraction) is all that's required — the inherited base loop then stamps `shape_id` (and any NatSpec/`//` doc comment). No `base.py`/CORE change and no `shape_id` churn on other languages (values are new where they were None; `shape_id` is not part of `stable_id`). This completes `repeat-finder`'s reach across C#/Solidity/WGSL and closes WI-lutob.

#### Python call-graph resolution

- **Inherited-method calls resolve correctly across the class hierarchy.** Python inherited-method dispatch now walks a true C3-linearization MRO (fixing uneven-depth diamonds), resolves cross-file `self.method()` and `self.field.method()` (dependency-injection) calls via enclosing-class and field-type hints threaded through edge metadata, biases to unresolved when a builtin base (`dict`/`list`/…) shadows an in-tree method (ADR-0029), and disambiguates same-short-name classes/fields by threading concrete class and field-type ids — closing a large class of dead-code false positives. (Requires path relativization to also rewrite id-embedding values in `Edge.meta` and nested `Symbol.meta`.)
- **Cross-language dispatch no longer binds to a same-name class in another language** — receiver and type lookups are filtered to the caller's language.
- **Imports and re-exports resolve to real first-party nodes.** Module-constant attribute reads (`cfg.CONFIG.items()`), non-package facade re-exports (`from facade import fn`), imported module-level constants, and in-tree workspace-sibling imports now resolve to the real in-tree symbol instead of a phantom `external_symbol` twin; symbols defined in `__init__.py` resolve under the importable package name (recovering 59 first-party edges).
- **In-tree submodule reads resolve to the submodule's file node (INV-nuzas category B, WI-tanot).** Reading an in-tree subpackage/submodule as a value (`import pkg; … pkg.sub`) — where `sub` is first-party, not a variable/function — now emits a `references` edge to the submodule's file node (via `module_to_file_id`) instead of a workspace-prefixed `external_symbol` phantom. Within-package reads resolve (self-corpus workspace-prefixed externals 18→13); cross-*package* submodule reads still phantom (they need a global cross-package module map — tracked residual).
- **Co-referent module aliases retarget instead of self-shadowing (INV-nuzas / INV-fahub).** A function-local `from pkg import sub as m` naming the SAME in-tree module a sibling scope plain-imports as `m` (the file-scoped `module_imports` shape) was added to the INV-fahub shadow set unconditionally, so the read `m.attr` suppressed its own `module_attr_ref` retarget and minted a workspace-prefixed `external_symbol` phantom. `_collect_local_bindings` now excludes a `from`-import whose absolute (`level == 0`) target equals the same name's `module_imports` binding — a co-referent alias, not a value rebind — so the read resolves to the real in-tree symbol via a `references` edge; a genuine value rebind (`from pkg import CONST as m`) or a later `m = …` reassignment still shadows. Clears the `rust._analyzer` / `prisma._analyzer` self-corpus phantoms; relative co-referent imports stay phantom (a safe miss, never a wrong edge).
- **Scope-shadow gaps in the module-attr / reference retarget closed (WI-luhah, INV-fahub).** The `module_attr_ref` / `references` retargets could mint a confidently-wrong *resolved* edge when a module symbol's name was rebound in a way the immediate-scope shadow guard missed: (1) an enclosing function's param/local captured by closure in a nested read (`def outer(cfg): def inner(): return cfg.CONFIG` — `inner`'s `cfg` is `outer`'s param, not the module alias), and (2) a module-scope rebind that the module-scope caller left unguarded (empty bindings) — a reassignment shadowing an import alias (`import config as cfg; cfg = make_cfg(); cfg.CONFIG`) or an import shadowing a same-named module variable (`X = 1; import mod as X; … X`). Both gaps are now guarded for **both** emit sites: the nested-scope shadow unions the full enclosing-function chain's bound names (`_enclosing_shadow`), and the module-scope callers pass a reassignment-target set (attr retarget) / import-alias set (variable retarget); comprehension for-targets are correctly excluded (Python-3 scoping). All INV-fahub-safe (a missed retarget is a phantom, never a wrong edge). Documented pathological residuals: a `def`/`class`/`except … as` statement name *colliding with an import alias*, and flow-insensitive reassignment ordering.
- **Aliases, closures, and shadowing.** Module-level function aliases (`f = g`) resolve through to the real body; closure-factory decorators' returned inner closures become reachable via a `dispatches_to` edge; and comprehension / lambda / nested-def bindings no longer inherit stale types from a shadowed outer variable.
- **`@property` reads resolve to the in-repo getter**, closing dead-code false positives where getters looked unread.

#### Inheritance linking

- **External/stdlib base classes emit unresolved-external `extends` edges instead of being dropped, for every OO language (WI-jubag Approach C).** The framework-agnostic inheritance-linker chokepoint now represents a base that resolves to no in-tree symbol as an `is_resolved=False` extends edge to an `external_symbol` boundary node (`{lang}:external:0-0:{name}:unresolved`, the INV-nuzas-safe `external` sentinel), evidence-derived confidence 0.95 (ADR-0039). This generalizes the per-analyzer external-base fallbacks (previously Python + JS/TS only) to Kotlin, Ruby, Java, C#, Scala, PHP, Swift, and the rest — all of which silently dropped external bases — and recovers Python **dotted/qualified** bases (`class C(argparse.ArgumentParser)`) that the Python analyzer defers. A base that names an in-tree symbol not extracted as a class, and a dynamic/garbage base name (Ruby `class Foo < Struct.new(:a)`), are still dropped rather than minted as false externals. Ownership split (no double-emission): a per-analyzer fallback owns a class's **bare** external bases (it resolves import aliases to the base's original name, which the chokepoint cannot see), so the chokepoint defers those when the analyzer already emitted an external edge for the class, and adds only the dotted bases the analyzer left behind — plus all external bases for languages with no per-analyzer fallback. (`instantiates` external constructors and retiring the now-redundant per-analyzer inheritance resolvers remain follow-ups.)

#### File classification

- **Dense non-web source files are no longer silently dropped as minified (INV-lukop).** The average-line-length minification heuristic (>150 chars) now fires only for web-asset extensions (JS/CSS/HTML bundles). A dense-but-real source file in another language — a Python data/lookup module, a very long function signature, generated protobuf lacking a `@generated` header — was misclassified as minified, classified tier-4 (derived), and had its **whole file** (every symbol and edge) dropped by the default tier filter with no diagnostic. The `@generated` / sourcemap / webpack heuristics stay universal (they are language-agnostic generation signals).

#### Dependency linking

- **`depends_on_manifest` edges attribute to the importer's own package manifest in monorepos (WI-timon).** When the same dependency name is declared in several package manifests (e.g. `rich` in both `hypergumbo-core` and `hypergumbo-tracker`), the dependency lookup was a global flat `name → Symbol` map, so every importer's edge pointed to whichever manifest was processed last. The lookup is now `name → [Symbol]` and each import edge is attributed to the **nearest enclosing manifest** of the importing file; an importer under no candidate manifest (or unresolvable to a path) falls back deterministically and carries the registered `disambiguation_fallback` flag with confidence 0.5 (INV-zuhub) instead of a confident-but-wrong 0.9.

#### Symbol-kind emission correctness

- **Per-language emission fixes.** Go module-variable emission gates on package scope (no more function-locals); Swift field emission requires a direct type-body parent (SwiftyJSON: 383 of 407 "fields" were method-locals); Julia `const` no longer drops multi-name/typed members or clobbers a same-named function; Elixir `def`s inside `quote` blocks attribute to the `use`-ing module; and Nim now extracts EXPORTED (`*`-marked) procs/funcs/methods/types instead of dropping them.
- **JavaScript/TypeScript coverage.** Generator functions, const-bound function expressions, anonymous callbacks, and IIFEs now emit function symbols (closing dead-code false positives); CommonJS `require()` bindings reach ESM call-graph parity and `.mjs`/`.cjs`/`.mts`/`.cts` are discovered; external/library inheritance edges emit as unresolved-external instead of being dropped; and route-handler pseudo-symbols populate `signature`.
- **Java `imports` edges.** The Java analyzer now emits an `imports` edge for every declaration (the last mainstream-analyzer holdout), populating `dst_ref` and unblocking framework refinement (ADR-0037).
- **C# and Swift member attribution corrected (DEEP dogfood cohort).** Three per-language fixes surfaced by dogfooding on sherpa-csharp / SwiftyJSON: (1) a **user-typed C# property** was named after its return *type*, not the member — `public WaveInfo Info` emitted the symbol `C.WaveInfo` and collapsed two same-typed properties in the name index — because property-name extraction grabbed the first `identifier` child (a user type is itself a preceding `identifier`); it now reads the grammar's `name` field. (This also disproves INV-dakit's filed "expression-bodied properties are not emitted" mechanism — every property shape *does* emit `kind=property`; the real defect was the name.) (2) A cross-file **C# `new X(...)`** landed its `instantiates` edge on the *class* node rather than the constructor, so a reverse-slice on a constructor ("who constructs this?") found zero callers; it now targets the `Type.Type` constructor when one exists, falling back to the class only for implicit default ctors — overloaded ctors currently share one anchor (WI-fagit). (3) **Swift `extension T { … }`** members were demoted to bare file-level symbols — functions lost the `method` kind, and functions / computed-properties / stored-properties / subscripts all lost their `T.` name and qualified-name prefix — because the extended type name is wrapped in a `user_type` node the enclosing-type resolver did not read; it now reads the `name` field, recovering the whole public API of extension-heavy libraries (SwiftyJSON's `rawData`/`rawString`/operators and the flagship `json[...]` subscripts) (WI-kudir).
- **Swift struct-body subscripts attach to their enclosing type (WI-fokag).** The containment linker's `CONTAINABLE_KINDS` set omitted `subscript`, so a correctly-named struct-body subscript (`JSON.subscript(key:)`) received no `contains` edge and looked like an orphaned member of its type — the WI-kudir residual (parent-name extraction already handled the parenthesized `(key:)` suffix; only the kind gate blocked it). `subscript` is now containable. Graph-completeness only: the dead-code BFS does not traverse `contains`, so this does not move the dead-code false-positive rate. (The separate gap where a `json["key"]` subscript-*call* site does not resolve to the subscript node is a Swift-analyzer resolution facet, still open.)
- **Rust enum match-arm dispatch resolves to the concrete impl (WI-kodap).** A local destructured from a tuple-struct enum variant — `match cmd { Cmd::Query(q) => q.run() }`, zoxide's subcommand-dispatch shape — was untyped, so `q.run()` fell to short-name resolution and every arm bound to the same (last-registered) `run`, leaving every concrete handler with 0 incoming calls and stranding the forward slice from `main()` at the dispatch boundary. The binding now adopts the variant's field type (`q: Query`), so `q.run()` resolves to `Query::run`. File-scoped positional binding also covers `if let` / `while let` / destructuring `let`; builtin-typed fields, unit/struct-like variants, and non-enum tuple patterns are skipped. (Overloaded-arity dispatch remains a follow-up.)
- **Rust chained return-position dispatch resolves against the receiver's type (WI-lohup).** A method call whose receiver is itself a call — `Cmd::parse().run()`, the actual `main` entry in zoxide-style CLIs — typed nothing for the receiver (no intermediate variable for the let-binding `var_types` walker), so `.run()` fell to short-name resolution and mis-bound to a same-named sibling. The outer method now resolves against the receiver call's inferred return type (`Cmd::parse()` yields `Cmd`, so `.run()` → `Cmd::run`), reusing the let-binding RHS type-inference helper, so it also covers `obj.foo().bar()` when `foo`'s return type is known. Together with WI-kodap this closes the zoxide symptom end-to-end: the forward slice from `main()` now reaches `Cmd::run` and, through its match arms, the whole subcommand tree.

#### CLI validation & errors

- **Commands validate input and fail loudly instead of silently accepting or emptying.** Non-directory positional paths, negative/invalid numeric flags (`--hub-threshold`, `--max-symbols`, `--coverage`, …), whitespace-only search patterns, and unknown `--kind` / `--language` / `--require-section` values now exit `rc=2` (with did-you-mean hints); `symbols --kind file`/`variable` return matches instead of empty; `config <X>` rejects non-language names; and an unexpected error exits `rc=1` with a clean message (full traceback under `--debug`).
- **Cold-cache auto-analysis returns the map it just wrote.** Commands that auto-run analysis on a cache miss (`slice`, `search`, `explain`, `routes`, `symbols`) re-derived the freshly written map path via a cache-dir hash whose `<state_hash>` segment is a live content hash of the git-dirty file set; when the repo changed during the multi-second run (concurrent tracker/CI writes in the self-analysis + `smart-test` scenario), that segment drifted and the command failed with `Error: Input file not found: None` (and `smart-test` fell back to a pre-commit-rejected full-suite manifest). `_get_or_run_analysis` now returns the path `run_behavior_map` actually wrote (from its returned artifact list, selected by name) instead of re-deriving it (INV-somup).
- **`search` reporting.** The result header reports the true total match count (disclosing truncation), rejects non-positive `--limit`, and no longer squashes high-degree rank columns to `"10…"`.
- **JSON output completeness.** `config` and `dead-code-maybe` JSON now carry the schema envelope, the published `view` field is an enum (not a `const`), and `slice` JSON echoes a non-default `min_confidence` in `feature.query`.
- **`explain --provenance` has a visible effect** and section headers match edge semantics (Called by / Instantiated by / Contained by).
- **`dead-code-maybe` defaults to `--seeds production`** (entrypoints + exported API) instead of the ~89%-false-positive entrypoint-only mode, disclosing `entrypoint_only_dead` and `test_only_reachable` cohorts; it also demotes candidates with cross-language string matches (≥ `--cross-lang-threshold`, default 3) as likely cross-language-dispatch false positives.

#### Projection views (`compact` / `tiered`)

- **Projections now describe themselves.** `compact` / `tiered` recompute their `metrics` from the projected nodes/edges (not full-repo totals), retained nodes carry their `centrality`, both modes report `included_edges_count`, and `entrypoints_summary` / `features_summary` disclose how much of each array was omitted; selection is reproducible across `PYTHONHASHSEED` via sorted tie-breaks.
- **Projection fidelity.** `compact` preserves parallel edges in its connectivity-aware view, strips view-irrelevant `usage_contexts` / `sketch_precomputed` (keeping `analysis_runs` / `validation_report`, ADR-0033/0043), and re-projects feature slices onto the compacted graph; slicing from a function no longer leaks the containing file's imports.

#### Route & framework detection

- **More frameworks import-promote from real namespaces.** PHP (`Illuminate` / `Symfony`, with `\`-separator handling), Scala (Play/akka-http/zio-http), Haskell (Scotty), and six more route YAMLs (flask-restful, masonite, lumen, restify, scalatra, http4k) now promote from import namespaces instead of build coordinates; five frameworks whose detection key diverged from the YAML basename (`nextjs`, `adonisjs`, `aspnet`, `vertx`, `zio-http`) now load — enforced by a structural reachability invariant.
- **Detection corrections.** Spring Boot apps using only `org.springframework.web` annotation imports no longer demote to dev-frameworks; Django signal patterns are gated on Django detection (no more phantom `framework="django"` nodes from bare I/O calls); Lit's common-word decorators (`@property`/`@state`/`@query`) are gated to JS/TS so bare `@property` no longer misfires on Python; and Starlette `WebSocketRoute` classifies as a `websocket_handler` entrypoint, not an HTTP route.
- **Route recovery.** Swift/Vapor grouped-builder route surfaces (method-chained/closure/variable-bound groups) are recovered (236 previously-undetected endpoints); Nim/D external-import edges emit well-formed node ids, restoring jester/karax/vibe.d detection; route→handler dispatch lands as a traversable `dispatches_to` edge (relativization now rewrites id-embedding `Symbol.meta` values); and per-handler slice files no longer collide on disk.
- **Build-wrapper scripts no longer seed forward slices (WI-batit).** Maven/Gradle wrappers (`mvnw`/`gradlew`) are checked-in tier-1 repo-root shell scripts, so they escaped every entrypoint-ranking penalty and their large out-degree boosted their 0.85 base to ~0.98 — outranking real HTTP controllers and becoming the default `slice --entry auto` (and `dead-code`) seed. They're now demoted below the ranking floor (×0.1 with the connectivity boost skipped, the same treatment test-file entrypoints get), so the real API surface wins the auto-seed. A genuine, non-wrapper shell script keeps its full ranking.

#### Symbol identity (stable_id / node.id)

- **Doc/markup/template analyzers canonicalize identity.** Eleven analyzers now mint `node.id` and `stable_id` from one shared helper (line-less analyzers gain a `start_line` segment), nine emit canonical `sha256:<16hex>` stable_ids instead of raw composites (fixing 1,223 nonconforming ids on pretix, 12,664 on chatwoot), and sparql/kdl/graphql/SCIP producers route through canonical factories.
- **Synthesized route markers (go/js/java) are validator-clean** — the id kind-slot round-trips against `Symbol.kind`, `origin_run_id` joins an `AnalysisRun`, and `Symbol.origin` names a registered pass-id (ADR-0027).
- **Container/declaration symbol kinds now carry a `stable_id` (WI-rihob).** The tree-sitter producers construct `class` / `struct` / `enum` / `trait` / `protocol` / `contract` Symbols with a `shape_id=` but no `stable_id=`, so they defaulted to `None` — every TypeScript class (0/4, incl. real app classes) and TS enum, plus the container-kind tail on csharp/rust/swift/solidity/wgsl. The INV-sotiv orchestrator backstop (`populate_kind_stable_ids`) already covered `interface`/`type` but not these six; it now stamps them at the chokepoint via `make_declaration_stable_id` (the same file-scoped `sha256("{kind}:{language}:{path}:{name}")` shape as interface/type, with `kind` threaded in so a class and a same-named struct in one file stay distinct). One place, every analyzer — so cross-version class tracking / rename-survival works for the container kinds the spec's signature-key promises it for. Producer-computed ids (Python classes via `_compute_stable_id`) keep precedence. Callable kinds that were also null (go `method`, solidity `function`/`constructor`) are deliberately left to their producers' *typed* stable_id (a name-only key would collide across overloads) — a separate concern.

#### Edge & vocabulary conformance

- **Evidence types fold to the canonical registry (ADR-0028).** C++ heap-`new` (`ast_new`), Elixir `module_qualified_call` (→`ast_call`), resolved Python method calls (`ast_call_method` → `ast_call` + `meta["call_construct"]`), and Solidity `inherits` / `overrides` / event-emit edges now carry accurate registered `evidence_type`s.
- **Overloaded `edge.meta` keys split to single referents (ADR-0023/0024).** `channel` gains a `channel_kind` discriminator (WebSocket path → `url_path`), `construct` → `ref_construct` (vs. `call_construct`), `call_construct` file-locality → a new `call_locality` key, and OTP `otp_call`/`otp_cast` fold to `dispatches_to` + `meta["mechanism"]`; `cross_language` is dropped as a stored field (derivable from endpoint languages) and `<script src>` migrates to `references` + `meta["construct"]`.
- **Linker `edge.meta` vocabularies registered (WI-mulub).** Ten cross-language / framework keys emitted by their linkers but absent from the `MetaKeySpec` registry are now registered on the `edge_meta` axis, so the writer-contract and registry-drift checks can police them: WebSocket `client_framework`/`server_framework`, message-queue `topic`/`topic_type`/`queue_type`, database-query `query_type`/`table_name`, route `handler_name`, analyzer `receiver_type_hint`, and edge-dedup `referring_paths`.
- **Evidence fields descoped to the populated set (ADR-0040).** `Edge.evidence_lang` is now central-stamped at `Edge.create` from the src id's language slot (it was null on ~100% of edges; it is the `lang` input to the future language-conditioned confidence matrix) — catalog-guarded so a non-canonical src yields `None` rather than a bogus stamp, and never overriding a value a producer passed explicitly. The dead `evidence_spans` field (0/110533 ever populated) and the never-implemented `meta.evidence[]` multi-pass accumulator (spec prose only) are removed. No SCHEMA_VERSION bump — `evidence_spans` was never serialized and `evidence_lang` is additive against its existing optional declaration (WI-kuluh, WI-vozar, WI-gijus).
- **`receiver` documented as a per-language fold-residue key (audit-findings 0012).** The `edge.meta.receiver` classification is emitted only by analyzers whose call syntax carries receiver flavor (Ruby/Go/C#/Rust/C++), so it is absent *by design* on corpora lacking those languages — hypergumbo self-analysis (pure-Python/JS) reads 0, a corpus artifact, not an unpopulated-field defect. Spec §841 and the `MetaKeySpec` entry now state this, enumerate the **complete** current producer vocabulary (`bare`/`external`/`constant_external`/`stdlib`/`typed_field`/`typed_var`/`field_chain`/`generic`) in place of the stale illustrative `typed`, and flag that the key mixes a resolution class with the `field_chain` expression shape — kept as one key (no consumer branches on it) with a written re-evaluation trigger to split into `receiver_class` + `receiver_shape` should one ever need them independently (WI-jokob, WI-lodaz).
- **Route, kind, and language axes.** Route transport protocol (WS/LIVE/RPC) moves off `http_method` to a `route_protocol` axis (ADR-0031); the route framework is single-homed on `route_framework` and `route_of()` unions it instead of dropping it; proto/thrift/smithy `service`/`rpc` fold to `interface`/`method`; Rails view templates register as CONFIG languages; boundary `Symbol.language` normalizes file-paths and labels to a registered language or `None` (openzeppelin: 519→36); and `all_known_languages()` unions taxonomy languages (asciidoc, makefile).
- **Structural & documentation fixes.** `inherits` is treated structurally by slice and ranking via an `INHERITANCE_EDGE_TYPES` registry (Solidity hierarchies no longer leak into forward slices); `if __name__ == "__main__"` classifies as a distinct `main_guard` entrypoint kind; and `depends_on` / `depends_on_manifest` are documented as distinct relationships, not aliases.
- **Linker `pass_id`s conform to the `-linker` suffix convention (WI-nuduv).** Six Tier-2 linkers emitted an `AnalysisRun.pass_id` / `Symbol.origin` / `Edge.origin` value without the shared `-linker` suffix the rest of the linker fleet carries — `decorator-dispatch`, `di-resolution`, `inherited-calls`, `method-call-recovery`, `type-hierarchy`, and the newer `receiver-type-dispatch` (the ratchet added here surfaced the sixth, filed after WI-nuduv). All six now emit `…-linker` (registration name + module `PASS_ID` aligned), so a consumer can substring-filter linker-produced passes uniformly; a `test_pass_metadata` convention ratchet enforces it going forward (the `view_template` per-framework delegates, which emit the shared `view-template-linker` at runtime, are documented exceptions). This shifts those six `origin` strings in output and invalidates cached artifacts naming them. INV-numat member.
- **`framework_dispatch` documented as one coherent dispatch-convention axis; audit-0014 threshold corrected (INV-junid).** The `framework_dispatch` `edge.meta` key was mischaracterized (by the tracker item and audit-findings 0014) as a "framework name" axis leaking mechanism values. A four-lens review (ADR / spec / history / spirit) found it is instead ADR-0028 Cluster 28C's deliberate "framework-dispatch **convention**" fold — one coherent axis whose values are dispatch-convention identifiers (framework-specific compounds like `django_orm` / `kafka_streams` **and** framework-agnostic mechanisms like `registry_dispatch` / `npm_package`), distinct from `detection_pattern` (pattern-shape match heuristics). The `MetaKeySpec`, spec §9, and audit-findings 0014 now say so, and audit-0014's fabricated "≥30 values / ≥30 producers" recurrence-promotion threshold is corrected to ADR-0024 rule 3's real **"≥3 values OR ≥2 producers"** (key→field promotion to a typed `Edge.framework` stays declined per ADR-0028 OQ2; zero production consumers read the key). Docs/registry only — no producer or output change. Resolves INV-junid on its corrected acceptance property, draining the last open child of the INV-numat vocabulary-axis umbrella.

#### Supply-chain tier purity (ADR-0041)

- **Tier now names distance only.** Declared third-party deps drop from tier 2 to tier 3, with the declaration relationship re-emitted as a `directness` stamp (direct/transitive/undeclared); tier-3 boundary nodes carry an `ecosystem` stamp (stdlib vs third_party) and stdlib *submodule* imports (`unittest.mock`, …) stamp `ecosystem=stdlib`; in-repo role files (examples/docs/notebooks/fuzz/bench) and generated routes become tier 1, leaving `internal_package_roots` the sole tier-2 producer.
- **Test-code classification is unified and correct.** Co-located `test_*.py` / `*_test.py` / `test_*.sh` files mark `is_test_file=True`; the four framework-dispatch linkers and fifteen content-scanning linkers consume the canonical test-file vocabulary (no more phantom production edges from test-fixture pattern strings); naming-convention concepts (`*Service`/`*Controller`/`*Handler`) are stripped from test classes; and `cargo_dependency` / `poetry_dependency` are discriminated by manifest path (no more 100% Python false-positives).
- **`supply_chain_summary` schema drops the phantom `by_tier` wrapper.** The schema declared a `by_tier` object level the analyzer never emits — tier keys (`first_party`/`internal_dep`/`external_dep`, plus `derived_skipped`) sit at the top of `supply_chain_summary`, so a schema-driven consumer reading `by_tier.*` got nothing. The schema now declares the actual top-level shape (`additionalProperties` of `{files, symbols, ecosystem, paths}`) (WI-vafid).
- **`supply_chain_summary.<tier>.files` counts only file nodes.** The count previously folded in the distinct paths of *all* tier nodes — function paths and the `<external>` sentinel path of `external_symbol` nodes — inflating tiers with phantom "files"; it now counts distinct `kind=="file"` node paths per tier (`symbols` still counts every node). Documented the derivation rule in the spec (WI-mutuv).
- **Monorepo workspace siblings no longer mislabel as third-party** — each package's distribution name is subtracted from the resolved import set (ADR-0041 D8a). Vendored front-end assets under a first-party root demote to external, and synthetic linker stand-ins no longer pollute dead-code candidates (ADR-0031).
- **Workspace-sibling _dependency declarations_ are tier 2 `internal_dep`, not tier 3 external (INV-nuzas / ADR-0041 D8a).** The prior bullet fixed the imported-boundary-node facet; this fixes its complement — the `kind="dependency"` symbols. `_classify_symbols` stamped every dependency declaration tier-3 external unconditionally, so a monorepo sibling that another workspace package lists as a dependency (`hypergumbo-lang-common` declaring `hypergumbo-core`; the meta-package declaring all siblings — the normal path-dependency pattern) was mis-tiered external, corrupting tier-weighted views (centrality, sketch `first_party_priority`) that treat external as low-relevance. `_classify_symbols` now recognizes in-repo package distribution names (PEP 503-normalized, via `collect_workspace_package_names` reading every `pyproject.toml`'s `[project].name` / `[tool.poetry].name`) and tiers a matching declaration tier-2 (`"workspace-internal dependency declaration"`). Self-analysis: the 9 `hypergumbo-*` sibling declarations flip external→internal (`supply_chain_summary.internal_dep` symbols 0→9, external_dep 1522→1513); the 67 genuine third-party declarations stay tier 3. Python `pyproject.toml`-scoped; Cargo/npm workspace-sibling analogues are a documented cross-language follow-up (still tier 3).
- **Disclosure.** `supply_chain_summary.derived_skipped` enumerates the filtered derived directories, and `metrics.by_supply_chain_tier` gains an `edges_incident` count.

#### I/O boundary classification

- **Receiver-verified classification.** IO-boundary classification now verifies the receiver before matching a method-kind primitive (untyped method calls no longer phantom-match `pathlib.Path.replace` / `read_text`), and constructor-typed locals (`f = open(p)`, `s = socket.socket()`) classify correctly instead of falling to `external_potential`; the `io-boundaries` / `verify-claims` CLI honors `Edge.is_resolved` / `dst_ref` on rehydration (ADR-0028).
- **Coverage.** Bare `open()` emits a `calls` edge to `builtins.open`; `urllib.request.urlretrieve` is flagged high-risk; JavaScript `caches` / `indexedDB` / `fetch` (including Service-Worker callbacks) reach the io-boundary catalog; and `access_mode` no longer mislabels reads as `write` on assignment lines.
- **Python stdlib database primitives (WI-harin).** The `db_read` / `db_write` boundary categories — already in the vocabulary and populated for six other languages — now cover Python's stdlib datastore surface: `sqlite3` (`connect`, cursor `execute` / `fetch*`, connection `commit` / `backup` / `iterdump`), `dbm.open`, and `shelve.open`. The free-function opens (`sqlite3.connect` / `dbm.open` / `shelve.open`) are the reliably-matchable anchors; the DB-API method surface is catalogued for completeness / taint but stays latent until receivers are typed. Third-party ORMs (Django ORM, SQLAlchemy) are deliberately excluded — their calls arrive as bare untyped unresolved method calls that the matcher correctly refuses (INV-tapat), so surfacing them is a receiver-inference problem, not a catalog gap (now addressed for Django by WI-sozoj, below).
- **`net_recv` false positive removed (WI-harin).** `django.core.management.execute_from_command_line` was misclassified `net_recv`; it is a CLI dispatcher (routing to `migrate` / `collectstatic` / `runserver` / …), not a network receive, and third-party framework code out of scope under the strict-stdlib rule — removed.
- **Django ORM database I/O made visible via type-verified receivers (WI-sozoj).** The receiver-inference follow-up to WI-harin: Django ORM calls now classify as `db_read` / `db_write` instead of vanishing (io_tag_rate ~0.5% on Django repos was dominated by invisible ORM I/O). The Python analyzer types two framework-syntax markers and emits a `django.db.models`-module-qualified `calls` edge — the `<Model>.objects.<method>()` Manager convention (reads `filter`/`get`/`all`/…; Manager-position writes `create`/`bulk_create`/`update`/…; the chained receiver previously emitted no edge at all) and a `models.Model`-subclass `self.save()`/`self.delete()`. io-boundary's module-filter path (never the short-name gate) then classifies each via a new **type-verified python.yaml carve-out**: WI-harin's exclusion was a precision rule against short-name matching of *untyped* receivers, so a real-module-keyed, type-verified entry dissolves it (the catalog already carries type-verified third-party entries like `flask.Flask.run` and `javax.persistence`). Because the match requires the typed module hint, no `dict.get()`/`.save()` false positive is possible (INV-tapat/INV-maluk preserved), and `db_write`→database-sink / `db_read`→untrusted-source taint wiring follows for free. Deferred: SQLAlchemy, `instance.save()` on a typed local (return-type inference), and transitive `Model` bases.

#### Complexity & introspection

- **Cyclomatic complexity & LOC reach parity.** C/C++/bash callables (plus Solidity, WGSL, CMake functions/macros, and Jupyter code cells) now carry `cyclomatic_complexity` and `lines_of_code` — 67 languages total — with the complexity tables relocated to core `analyze/cyclomatic.py` (ADR-0033 Phase 4); Ruby CC no longer double-counts `if`/`while`/`for`/`case`.
- **Introspection surfaces.** `explain` and `sketch` now display captured `Symbol.docstring` (module summaries and function intent, 74–90% coverage), Python file nodes capture the module docstring's one-line summary, `Symbol.lines_of_code` is renamed `line_span` (physical span, not SLOC) with `profile.languages[*]` corrected to a `{files, loc}` shape, and private symbols carrying `library_export` are gated by `visibility` so non-importable helpers don't seed reachability.

#### Taint analysis (ADR-0037)

- **Resolution-aware taint.** Taint now reads the resolution verdict from `Edge.is_resolved` (not a dst-string suffix), and sanitizer registration applies the same resolution/kind gate as sources and sinks — so an unresolved call no longer registers a phantom barrier that suppresses a real flow.

#### Sketch

- **Warm-cache & input fidelity.** Warm `hypergumbo sketch` reads the 4×/16× comparison sketches from `.stats.json` sidecars instead of regenerating them (~27s vs 155s on the self-corpus); `sketch --input <map>` summarizes the map's file universe instead of re-walking the repo; and `sketch -t N` no longer dies with exit 141 (SIGPIPE) on large budgets. `additional_file_centrality_scores` is relabeled as an unbounded density (in-degree ÷ file size), not a `[0,1]` score.
- **Binary content no longer leaks into rendered sketches.** A file containing NUL bytes is now omitted with a `[binary content omitted]` placeholder at the single content-render chokepoint (covering both the Additional Files and Source Files sections) instead of being embedded verbatim — a cold-cache `sketch -t 4000` previously emitted 12,792 raw NUL bytes that choke tokenizers and JSON-wrapping consumers (WI-pubar).
- **Removed the consumer-less `sketch_precomputed.vocabulary` cache field.** It had no reader in any package (`compact` strips `sketch_precomputed` entirely) and was resolved by deletion rather than lemmatization, per the Wave-4 typed-precompute direction; the field is dropped from the producer and from the (`x-internal`) schema, with no `SCHEMA_VERSION` bump (INV-padoz).
- **`readme_description` no longer embeds raw `> ` blockquote notes.** The README heuristic skips markdown blockquote callouts (in both the paragraph scan and sentence-completion) instead of concatenating their `> ` prefix into the description — which also removes the mid-blockquote truncation that left the visible text cut mid-sentence (INV-modor).
- **`config_info` license detection aggregates every distinct license.** It now scans root *and* monorepo package `LICENSE` files (depth 1-2, excluding vendored/hidden dirs) and reports all distinct licenses instead of stopping at the first root file — so a dual-licensed monorepo (e.g. AGPL root + MPL sub-package) no longer collapses to AGPL alone (WI-gojuz).

#### Containment & file anchors

- **Every content-bearing path gets a `kind="file"` anchor**, and class-body fields, top-level functions, and module-level variables are rooted at their enclosing class/file via `contains` edges (~8.8k new edges, restoring subgraph closure); `kind="file"` anchors are exempt from the orphan ratchet. Containment now also roots fields for Solidity/Nim/Scala/D container constructs.

#### Limits (honesty signals)

- **`limits.partial_results_reason` now follows its conditional-presence contract.** It is emitted only when the analysis is actually incomplete (a reason is set), not as an always-present empty string — so a consumer can use key presence as the incompleteness signal, per spec §994 (WI-tamop).
- **`limits.test_files_excluded` is now always observable.** It is emitted as `false` when tests were not excluded rather than being omitted, so absence is no longer indistinguishable from "tests included" (WI-miron).
- **`limits.skipped_languages` is now populated.** Its `add_skipped_language` setter had zero callers, so the field was always `[]` even when a detected language had no analyzer (grammar unavailable/unsupported) or its pass crashed. A finalize chokepoint now drains the "detected (profile) minus analyzed (`analysis_runs`)" difference — e.g. a repo with a `Makefile` but no makefile analyzer reports `skipped_languages: ["makefile"]` (WI-nihir).
- **`limits.not_captured` is documented as a universal static disclaimer.** Its module comment, schema description, and spec entry now state it is a fixed list identical for every repo — the construct categories static analysis never captures *anywhere* — not a per-repo measurement of constructs this repo contains-but-skipped (WI-togop).
- **`limits.analysis_depth` removed.** It was a hardcoded `"syntax_only"` constant — never reassigned, false for the semantic maps hypergumbo produces, and read by no consumer. Removed rather than populated: a single map-level depth scalar cannot honestly represent per-language depth, and "is the call graph semantic?" is answerable from the presence of `calls`/`dispatches_to` edges. No SCHEMA_VERSION bump (non-required field) (WI-muzus, WI-zusok).

#### Documentation

- **Spec §9 I/O-boundary contract corrected** — the vocabulary aligns to the actually-emitted set (`unknown_dynamic` flagged reserved-but-unimplemented), and `io_boundary` / `io_primitive` are reframed as consumer-time derived, not producer-stamped.

#### CI & smart-test

- **`smart-test` scopes per-PR selection and coverage to branch-own changes (WI-kalub).** The reverse-slice baseline was the `last-green-sha` marker, whose full-suite write side fires only on an all-packages-exactly-100% run and whose codeberg push no-ops under CI failover — so it routinely sat tens of commits stale, inflating the "changed files" set with already-merged work. That produced two compounding failures: (1) the scoped coverage gate false-red-ed on a genuinely-green branch (`coverage report --fail-under=100` exit 2) because the affected slice under-covers integration-covered drifted files, and it conflated coverage.py's "No data to report" (exit 1) with a real <100% failure; (2) the inflated changed set expanded the slice to ~the entire suite (459/436 test files, 16338/16342 tests, ~15.5 min), erasing the smart-test speedup. `get_baseline` now returns `merge-base(HEAD, <authoritative-dev>)` (failover-aware), the scoped gate distinguishes "No data" from a real <100%, the pass/fail re-derivation word-bounds its `failed` match (it had matched the always-present "N xfailed" summary token), and the scoped-coverage verdict is written into `.ci/pytest-output.log`. Whole-codebase coverage is enforced separately by `nightly.yml` (and full-suite `--cov-fail-under` teeth, companion change). See ADR-0011.
- **Full-suite gains whole-codebase coverage teeth (WI-kalub Step 3).** `full-suite.yml` ran each package's full suite with `--cov` but only *scraped* the total for the badge/marker — the job passed regardless of coverage, so the blocking path (per-PR + full-suite) enforced 100% only on *changed* files and a cross-cutting regression on an *unchanged* file rested entirely on `nightly.yml`. Each per-package job now fails unless its whole-codebase coverage is exactly 100% (reusing the same `[ "$COV" = "100" ]` bar the last-green-marker write already applies), so such a regression reds full-suite. The per-PR `ci.yml` scoped gate also gains the "No data to report" ≠ `<100%` distinction (Step 1c parity). Pre-verified no current coverage debt (all six packages measured at 100% in isolation). (The failover-aware stop-the-line status endpoints that let a full-suite red also *block* PRs under failover land in WI-kalub Step 2, below.)
- **`check-package-coverage` now checks all six packages (WI-kalub Step 4).** The local per-package coverage-parity tool declared `rust-analyzer` in its `PACKAGES` map but omitted it from the default target list and the arg parser, so a bare run silently skipped one of the six packages the full-suite last-green marker requires (and `check-package-coverage rust-analyzer` errored `Unknown argument`). Both are fixed; the default run and by-name selection now cover rust-analyzer.
- **Stop-the-line and full-suite/nightly commit-status calls are failover-aware (WI-kalub Step 2).** Stop-the-line (`ci.yml`) reads the Forgejo auto-generated `Full Test Suite / aggregate` status to decide whether full-suite is broken, but that read — plus full-suite's status write + its "SHA already tested → skip" read, and nightly's status write — hardcoded `https://codeberg.org/api/v1`. Under CI failover the workflows run on the self-hosted Forgejo, whose status lives on *that* server, so the codeberg endpoints were dead reads/writes: stop-the-line silently never blocked and full-suite re-ran already-tested SHAs. All four now target `${{ github.server_url }}/api/v1` (the form ci.yml's "Set commit status" step already used), so — combined with the Step-3 teeth — a whole-codebase coverage regression can also *block* PRs under failover. (`release*.yml` codeberg references are intentionally untouched — releases are canonical-on-codeberg and human-gated.)
- **smart-test no longer false-reds (exit 1) on green runs under `FORCE_COLOR` (WI-kalub — the dev-loop symptom).** In a dev shell that exports `FORCE_COLOR`, pytest colorizes even the redirected `.ci/pytest-output.log`, so the summary line begins with an ANSI escape. `summarize_pytest_output`'s anchored `^=+ …` result-line grep then failed to match and — unguarded, under `set -e + pipefail` — aborted smart-test with exit 1 *after* the tests passed but *before* the coverage gate, on every run regardless of outcome (this is the item's original "exit 1 on fully-green runs" + its "missing green signal in captured output"). The captured pytest run now neutralizes `FORCE_COLOR`/`COLORTERM` (aligning the local dev loop with CI, which has neither), and the result-line grep is `|| true`-guarded as defense in depth. CI was never affected. (The coverage-gate false-red addressed by the branch-own baseline above is the distinct *CI-path* mechanism.)

## [6.1.0] - 2026-06-18

### Added

- **Spec-validator shrink-only ratchet gates across multiple substrates plus a self-tree validation gate.** A per-substrate CI ratchet runs the validator over a four-substrate matrix, and a full-suite-only job runs hypergumbo on its own tree, ratcheting the violation matrix against a committed baseline — violation totals and `runtime_coherence` offender counts may shrink but never grow, replacing ADR-0033's impossible assert-empty aspiration with an honest shrink-only form. The ratchet gained a per-`(validator_class, severity)` dimension so warning-class regressions can no longer hide via signed cancellation.
- **Analyzer emission-parity matrix gate locking per-language field emission.** A standing per-`(language, field/edge-type)` matrix gate locks which `Symbol`/`Edge` fields each language analyzer emits, using uniform fixtures and live-dataclass reads; surfaced a new Java imports parity gap.
- **Docs-vs-argparse gate detecting CLI help/README drift.** Diffs CLI docs against the live argparse parser via three checks (removed-feature denylist, README invocation surface, committed flag-availability matrix).
- **Per-pass productivity counters and an always-on pass timer.** `AnalysisRun` gained `nodes_emitted`/`edges_emitted` fields (schema 0.14.1->0.14.2) and the `duration_ms` timer now starts for every pass, not just the file-walking branch — previously IR-consuming passes reported 0ms while emitting edges and there were no productivity counters; a floor makes 0ms unambiguously mean did-nothing.
- **stable_id v6 closure gate: validator surface + `stable_id_stats` block.** Per-file duplicate stable_id is now a hard error, the corpus-collision umbrella fires at ~0 over an all-Symbols denominator with the None-cohort disclosed, and a new always-present `stable_id_stats` block lands (validation_report schema 0.1->0.2); also fixed two byte-determinism leaks in the collision/fingerprint umbrellas.
- **Validator FK predicates: dangling-endpoint and origin_run_id, plus a wired-checks disclosure manifest.** `build_validation_report` now emits a `wired_checks` manifest (schema 0.2->0.3, pinned by a drift guard) so an unvalidated defect class is visible by absence; added `_check_dangling_endpoint` (flags any non-empty `Edge.src`/`dst` naming no node in the symbol set) and `_check_origin_run_id_fk` (re-derives the `Symbol`/`Edge.origin_run_id` -> `analysis_runs.execution_id` foreign key at validation time).
- **Symbol.id round-trip canary plus GraphQL operation kinds registered.** Advisory `_check_id_roundtrip` parses the canonical id's span/name/kind tokens and flags kind-slot-not-in-registry, kind mismatch, empty name, and bad span ordering; also registered GraphQL mutation/subscription as `language_construct` and the anonymous-operation fallback as `pending_classification`.
- **Class-B synthetic-node identity backstop plus display_label.** A post-linker pass backstop-stamps `stable_id`, `display_label`, and `fingerprint` on Class-B synthetic protocol-synth Symbols their linkers left null (~7 linkers) via an injective chokepoint; validator gains the Class-B `display_label` biconditional. Identity-neutral.
- **mypy strict whole-tree ratchet (INV-zogud ramp).** The `[tool.mypy]` config is now strict + pyright-resonant (Decision D13), and the non-blocking CI mypy job runs a single WHOLE-TREE invocation (`scripts/check-mypy-ratchet`) over every package source root — replacing the per-package loop, which could not follow cross-package imports (the editable install is mypy-invisible) and so mis-measured INV-zogud's `mypy <repo>` target, inventing phantom errors (every analyzer's decorator read as `Any`) and hiding real ones. A shrink-only per-error-code baseline (`.ci/mypy-strict-baseline.json`) makes the surface a ratchet; the job stays non-blocking (`--mode=warning`) until the surface drains, when WI-rabum flips it to `--mode=strict`. En route, the `Symbol`/`Edge.origin` provenance field was widened to `str | List[str]` to match its documented scalar-or-list normalization contract (INV-jidat), resolving ~465 `arg-type` errors at the `ir.py` chokepoint with no runtime or schema change (the JSON schema still emits `array[str]`).
- **Closure-evidence discipline governance guard plus audit script.** Requires behavioral evidence (live repro or production-path test) to resolve behavioral-invariant tracker items — never a proxy metric or adjacency claim; ships a playbook, AGENTS.md essentialization, and an advisory `audit-closure-evidence` script.
- **Self-healing tracker-op recovery via a reference-transaction git hook.** Fires `tracker recover` on every committed ref transaction so worktree-destroying commands (`reset --hard`, `checkout`) auto-restore dropped pending `.ops` from the journal. Idempotent and non-blocking.
- **Decision ADRs recording the design-interview rulings.** ADR-0035–0042 record the rulings that unblock the correctness campaign (stable_id v6, node.id grammar v2, edge-resolution semantics, access_mode, confidence separation, evidence-field descope, supply-chain tier purity, a survey rename), plus ADR-0043 recording the target stage DAG and conflict resolutions for the `run_behavior_map` pipeline.

### Changed

- **Edge resolution semantics: a single edge-finalization verdict for `is_resolved`.** `Edge.is_resolved` now contractually means dst is a real in-repo first-party node; a new `_finalize_edge_resolution` sub-step classifies each dst by node kind and derives both `is_resolved` and `dst_ref` from one verdict, making producer-stamped values advisory, with a validator FK predicate (`is_resolved=True` => `dst.kind != external_symbol`).
- **stable_id scheme bumped v5 -> v8 to close residual corpus collisions.** v6 atomically folds the full enclosing scope chain into a shared `assemble_stable_id` formula (unifying the Python AST and tree-sitter producers), drops the churning Python `body_sig`, adds an occurrence_index slot, path-anchors `make_module`/`interface`/`type`/`entry`/`dependency`, and makes name/qualified_name mandatory on `make_typed_stable_id`. v7 threads `make_file_stable_id` into the `containing_stable_id` slot of the two shared tree-sitter producer entrypoints so same-`(kind,name,qualified_name)` symbols in different files no longer collide cross-file. v8 widens route stable_ids with declaring language+path, moves http/sql call-sites to a path-anchored `site:` factory, and reroutes CBV HTTP-verb methods onto the normal file-anchored path.
- **Single `finalize(ctx)` stage shed two dead Phase-2 stub sub-steps.** The no-op `_finalize_declared_fields` and `_finalize_confidence_aggregates` sub-steps are removed (their work lives elsewhere — writer-contract in `validate_ir`, confidence producer-side, the aggregate as `metrics.avg_confidence`/`sketch.confidence_mass`), leaving zero Phase-2 stub slots; a white-box guard asserts `_SUBSTEPS` matches the actual `_finalize_*` set.
- **Denominator-scope disclosure for non-null exclusion in collision/degeneracy rates.** The stable_id-collision and fingerprint-degeneracy reports now count and disclose records excluded from their non-null denominators, so the reported rate is a biconditional encoding rather than a silently-deflated one.
- **Delete dead producer bare-hex fingerprint sites; add an output-boundary format guard.** Removed 29 dead producer-side bare-hex sha256 fingerprint computations across ~10 tree-sitter analyzers (the central post-pass already overwrote them) and added a `_check_fingerprint_format` validator predicate asserting every non-null fingerprint on a real source node carries the canonical `hgfp2:` prefix.
- **Dev-workflow hardening: auto-pr closure guard, CI re-poll, smart-test reverse-slice/failover.** `auto-pr` aborts merges whose message bare-closes a still-open tracker item and re-polls to confirm CI before merging (avoiding false-failures under concurrent CI); `smart-test` no longer swallows reverse-slice failures and is failover-aware when establishing its baseline.

### Fixed

- **Decorated declarations no longer collapse to a name/signature-stripped fingerprint.** Python decorated def/class fingerprinting degraded to a body-only container hash that dropped name and signature (so identical-bodied decorated symbols collided) because effective-line extension included the decorator while producer spans excluded it; fixed by comparing the fit-check lower bound against the raw declaration-keyword line so decorated declarations are hashed whole.
- **Central post-pass now normalizes producer-side bare-hex fingerprint leaks.** `stamp_symbol_fingerprints` recomputes a non-canonical fingerprint on a real source node in the canonical `hgfp2:` scheme rather than blindly preserving it, so bare-hex from ~10 analyzers never reaches output and copy-pasted bare-hex self-heals.
- **Results cache invalidates on tree-sitter grammar upgrade.** The analyzer-identity cache key hashed only hypergumbo package versions, so a grammar upgrade on an unchanged repo returned a stale cache hit from the old grammar; the key now folds in the tree-sitter library and grammar package versions.
- **play-routes no longer shadows the canonical `PASS_VERSION` with literal "1".** A module-local `PASS_VERSION="1"` decoupled its `run_signature` from the release, making it the lone version outlier in self-analysis; now imports the canonical constant.
- **Circom grammar-unavailable skip-path emits a dict toolchain instead of a str.** The skip-path built `AnalysisRun` with `toolchain` as a bare string where the field is `Dict[str,str]`, crashing `finalize`'s `run_signature` recompute with AttributeError when circom files were present but the grammar was unavailable; now a `{name, version}` dict like every other analyzer.
- **Producer-side `config_fingerprint` for override-analyze / linker / synthesis passes.** `config_fingerprint` was the empty-dict sentinel on most self-analysis runs because only inherited-analyze tree-sitter analyzers self-stamped, collapsing distinct passes onto one cache-keying fingerprint; producer-identity stamping now lands at orchestrator/linker/synthesis chokepoints through one shared primitive, guarded so already-stamped analyzers are untouched.
- **Central `origin_run_id` backstop for direct-constructor analyzers.** Direct-constructor analyzers (toml/json/wgsl/sql) built Symbols leaving `origin_run_id` empty, breaking the node->AnalysisRun join for the manifest/config straggler tail; `collect_analyzer_result` now stamps the run's `execution_id` onto any unstamped Symbol, preserving values a producer already set.
- **Synthetic-node provenance: a real AnalysisRun for both orchestrator-level synthesizers.** File-symbol synthesis and boundary external_symbol synthesis now each emit a real `AnalysisRun` and stamp resolvable `origin_run_id` on minted nodes (previously the empty-string sentinel broke the node->AnalysisRun join for ~2,236 nodes); boundary nodes also gain a registered origin mechanism. Additive and identity-neutral.
- **stable_id v6 identity reconciliation via two post-linker passes.** `split_within_file_stable_id_collisions` re-mints repeated same-target sites with a deterministic `:occ:<n>` suffix, and `dedup_logical_synthetic_identities` collapses message-queue/event topics to one hub node, rewiring edge endpoints onto the survivor (graphql excluded).
- **Boundary synthesis now runs after tier+noise filtering.** Moving boundary-node synthesis to after tier+noise filtering (instead of before) lets a dangling src left by a filtered-out tier-4 DERIVED file be seen and a boundary minted/remapped, closing the residual dangling-source class. Identity-neutral.
- **Python call-ownership resolves by node identity.** Methods are now registered in the collision-immune `func_symbol_by_node_id` (keyed by `id(ast_node)`) instead of the last-write-wins bare-name `symbol_by_name` dict, so caller resolution for calls/instantiates/references edges attributes src to the correct method; a paired guard keeps methods out of `inner_scope` to avoid shadowing nested helpers.
- **Python `qualified_name` now emitted on functions/methods/classes** (previously None for 100% of Python symbols), threading the existing container-qualified name through the `qualified_name=` kwarg; `name=` unchanged, so identity-neutral.
- **markdown/gitignore stable_id canonicalization.** The markdown (section/code_block/link) and gitignore (pattern) analyzers no longer reuse the non-canonical composite `Symbol.id` as their stable_id, routing through a new `make_doc_stable_id` factory that folds kind and span to produce the canonical `sha256:<16hex>` shape. Identity-neutral.
- **Single `finalize` stage re-hashes stale run_signatures and backfills pass_version.** Consolidated scattered pre-serialization finalizers into one ordered `finalize(ctx)` in a new `finalize.py` that re-hashes each `AnalysisRun`'s stale `run_signature` from its final `config_fingerprint`/`toolchain`, backfills empty `pass_version`, and absorbs `_relativize_ir_paths`; the unsound `emission_counts` sub-step was dropped.
- **Tiered `nodes_summary` recomputed from post-shrink arrays to match on-disk output.** `format_tiered_behavior_map` wrote `nodes_summary` from the pre-shrink connectivity selection, so summary counts overstated the arrays actually serialized after the budget-shrink loop pruned them; a new pure `recompute_view_summary` helper re-derives the summary from the final post-shrink arrays.
- **Orchestrator passes now fail open — a crashing analyzer, linker, or unreadable file no longer aborts the run.** Every pass-level crash site in the two orchestrators was unguarded, so any single-pass exception was fatal; all sites are now contained (the crash is recorded pass-level via `Limits.record_crashed_pass()` into `skipped_passes` with a `crashed:` reason and `partial_results_reason`) so remaining passes still run. The Python analyzer read broadens its catch to `OSError` (routing unreadable files into `failed_files`) and the repo-fingerprint content hash returns a sentinel digest on `OSError`.
- **Writer-contract validator now reads dict-shaped AnalysisRuns.** The validator read records with bare `getattr`, but the orchestrator feeds serialized dicts, so the sentinel check silently no-op'd in production while passing object-shaped unit tests; all four record reads now route through the `_read` dict-or-attribute helper.
- **auto-pr advances local dev after a transient-405 merge via a git ground-truth fallback.** Codeberg's merge API intermittently reports a merge as not-accepted when it actually landed, so local dev was never fast-forwarded (the trigger for the tracker-op data-loss chain); a new `_pr_landed_in_base` fallback checks whether the rebased tip is an ancestor of `origin/base`.
- **git checkout now self-heals dropped tracker ops via a post-checkout hook.** `git checkout` retargets HEAD as a symbolic ref and does not fire `reference-transaction`, so checkouts that dropped pending ops were never recovered; a new marker-guarded `post-checkout` hook runs `tracker recover`, and the `reference-transaction` hook now skips recover for constructive merge/pull/rebase/fetch operations. The self-healing hook also no longer fights tracker fast-forward reconciliation: `do_sync` and `auto-pr` set a `tracker-recover-disabled` marker around their git ops (previously the hook restored journalled-uncommitted ops as untracked files, aborting their local fast-forward so local dev perpetually lagged the remote).
- **`resolve_workdir` prefix-isolates bakeoff session auto-discovery.** bakeoff-broad and bakeoff-deep share one artifacts directory and the auto-discover branch took the lexicographically-last name across both prefixes, so `deep-*` always out-sorted `broad-*` regardless of timestamp; each command now filters auto-discovered sessions to its own mode prefix.
- **stable_id scheme-history backfill and ADR/spec corrections.** Backfilled the git-verified v1->v5 scheme transition chain in `docs/hypergumbo-spec.md`, corrected three stale `v2` assertions to v5, and fixed ADR-0014's amendment chain (omitted v4); added per-section supersession tables to partially-superseded ADRs (0014, 0015) and bidirectional supersession declarations on the new ADR headers (0036-0039); removed the fictional `EVIDENCE_CONFIDENCE_MATRIX`/`calculate_evidence_confidence()` and never-honored 0.30 unknown-evidence default from spec §12/Appendix C, documenting the actual per-producer hardcoded confidence (0.85 default, -90% test-entrypoint penalty); and corrected stale docs-prose across surfaces (evidence_type open-enum claim, spec §14 role-flags-not-tier note, `--debug` ripgrep-fallback reference, README `--no-progress` scope, `config --help` disclosure), closing the CLI-help/README-drift umbrella. Documentation only.

## [6.0.0] - 2026-06-10

### Summary

The concept-axis campaign reaches its capstone: the two remaining overloaded `Symbol` string fields split into typed siblings (`language` → `discovery_language` + `protocol_origin`, ADR-0031; `canonical_name` → `display_label` + `qualified_name`, ADR-0032, with `canonical_name` removed one schema version later), and a new end-of-pipeline spec-vs-data validator stage (ADR-0033) plus canonical ID-factory discipline (ADR-0034) enforce the catalogs, vocabularies, and ID formats the campaign established. SCHEMA_VERSION advances 0.5.8 → 0.14.1.

Analysis breadth grows: view-template linking extends from Rails to Django, Phoenix, Spring MVC, and Laravel Blade; structured `Edge.dst_ref` external references land across 18 analyzers; six per-symbol introspection fields (signature, docstring, qualified_name, cyclomatic_complexity, lines_of_code, is_exported) populate across all 10 mainstream-package languages; and per-entry-point safety claims make hypergumbo's own I/O surface machine-verifiable via `verify-claims`.

On the fixes side: mass `stable_id` collisions are resolved (60.2% of self-analysis Symbols shared an ID pre-fix; STABLE_ID_SCHEME v3 → v5), the hand-coded `docs/schema.json` $defs are replaced by dataclass introspection so whole-document validation passes on real linker output, `verify-claims` gains an `inconclusive` verdict and stops silently confirming on blind analyses, bad inputs, or unconstrained claims, the gitleaks secret scan recovers from a silent no-op under gitleaks 8.30+, and cached embedding loads no longer touch the network.

### Added

#### View-template linker family — Rails + Django + Phoenix + Spring + Laravel

Convention-based view-template linking, previously Rails-only, now covers five frameworks via a shared core (`MethodNameStrategy` and `ExplicitStringStrategy` in `_view_template_core.py`).

- **Django** — `render()` calls, `template_name` attributes, and CBV defaults for DetailView/ListView/CreateView/UpdateView/DeleteView/FormView.
- **Phoenix** — 1.x templates, co-located 1.7+ templates, and function-component shapes.
- **Spring MVC** — `@Controller` string returns and `ModelAndView(...)` under Thymeleaf/FreeMarker/Velocity/JSP.
- **Laravel Blade** — `view(...)` and `View::make(...)` with `.blade.php` probing.

#### Structured external-target IR (`Edge.dst_ref`)

New `ExternalRef(lang, module_path, name)` frozen dataclass replaces the legacy colon-delimited `Edge.dst` string for cross-module call references, adopted by 18 analyzers via a shared `ImportScope` abstraction (Python, Java, Go, Elixir, JS/TS, C++, Rust, and Ruby in the inaugural sweep; ten more languages via mechanical-equivalent paths and per-language qualifier hooks). Consumers (io-boundary chain composition, boundary-node creation) prefer `dst_ref` over legacy colon-split heuristics; polyglot call-site coverage tests pin the remaining qualified-call gaps via strict xfail. SCHEMA_VERSION 0.7.2 → 0.8.0.

#### Symbol-field axis decomposition (ADR-0031 + ADR-0032)

Two overloaded `Symbol` string fields each split into a pair of typed siblings, capping a campaign to make every multi-valued field on the core dataclasses carry a single, named axis.

- **`Symbol.language` → `Symbol.discovery_language` + `Symbol.protocol_origin`** (ADR-0031). The legacy field carried both *host language of this file* (the canonical use) and *protocol-family identifier* (smuggled through by ~21 linkers as literal sentinels like `kafka`, `websocket`, `grpc`). Now `discovery_language` carries the host-language semantic and `protocol_origin` the protocol family; `Symbol.language` relaxes to `Optional[str]`, with synthetic stand-ins ("Class B") emitting `language=None, discovery_language=<host>, protocol_origin=<family>` and real-source declarations ("Class A") unchanged. A new `protocol_origins` registry seeds 19 protocol families; the five cross-language-detection consumer sites and `metrics.py` read `discovery_language` directly.

- **`Symbol.canonical_name` → `Symbol.display_label` + `Symbol.qualified_name`** (ADR-0032). The legacy field carried three different things: a redundant duplicate of `name` (10 config analyzers), a UI display string for ~16 linker synthetic stand-ins (e.g. `"invoke('save_data')"`), and an aspirational fully-qualified path (proto / thrift / capnp / xml-config / vhdl). Now `display_label` is the display-only string consumers never branch on, and `qualified_name` is the language-aware FQN governed by a new `qualified_name_axis` catalog of per-language separators (bounded to `{".", "::", "\\"}` with an allowlist gate). Producer migration touches ~44 sites; consumers read `qualified_name or canonical_name` during the deprecation window (the legacy field is removed under §Removed below), and the colliding `meta["qualified_name"]` key is retired atomically with the typed-field promotion.

- **`protocol-origin` and `qualified-name` axes wired** into the static-AST `multi_value_field_axis` linter's known-axis-names dict, so `# axis: protocol-origin` and `# axis: qualified-name` annotations on dataclass fields pass lint without ad-hoc allowlisting.

- **Migration guide.** `docs/MIGRATION-6.0-CONCEPT-AXES.md` Part 7 documents both reshapes — consumer-migration patterns (`sym.discovery_language or sym.language`; `sym.qualified_name or sym.canonical_name`; read `sym.display_label` for synthetic-stand-in display strings), the four new fields per node (typically null for real-source declarations), and `stable_id` impact (~20–30 Class B Symbols' `stable_id`s change because `language=None` hashes differently from a string value).

#### Spec-vs-data validator stage (ADR-0033)

A new end-of-pipeline stage reads the emitted Symbols, Edges, and AnalysisRuns and verifies them against their declared contracts — previously each analyzer and linker wrote its own data with no central enforcement of the catalogs, vocabularies, and cross-field invariants declared elsewhere in the codebase. Five validator classes ship in this release. Violations go to stderr and a new `validation_report` artifact section, but `hypergumbo run` exits 0 regardless — the report is a triage surface, not a build break. Self-analysis reports zero violations across all five classes, but that reflects the inaugural checks' deliberately conservative scope, not a clean bill of health: known gaps that fall below or outside the checks ship as documented limitations (e.g. ~262 substrate Symbols with `language=None` rejected by the non-nullable `docs/schema.json`, and a residual ~0.8% `stable_id` collision rate under the 5% umbrella threshold). The check set widens over subsequent releases.

- **Axis conformance.** Every axis-tagged `str` / `Optional[str]` field on `Symbol` / `Edge` / `AnalysisRun` is checked against its catalog (∪ `{None}` for `Optional`). Covers `Symbol.kind` (symbol-kind catalog), `Symbol.language` / `discovery_language` (language catalog), `Symbol.protocol_origin` (protocol-origin catalog), `Symbol.origin` and `Edge.origin` (per-element pass-id catalog), `Symbol.qualified_name` (per-language separator policy from `qualified_name_axis`), `Edge.edge_type` (edge-type catalog), `Edge.evidence_type` (evidence-type catalog), `Edge.evidence_lang` (language catalog), and `AnalysisRun.pass_id` (pass-id catalog).
- **Writer contract.** Detects fields whose every record carries the producer-side default sentinel (≥ 2 records for signal), surfaced as one umbrella violation per (record-class, field) rather than N per-record copies. Inaugural check covers `AnalysisRun.config_fingerprint` — the canonical case where 84 of 84 runs were collapsing to `sha256(b'{}')` because every analyzer / linker called `AnalysisRun.create(pass_id, version)` with no config arg. The framework is a lazy-resolved table; subsequent writer-contract sweeps register new (class, field) entries against it.
- **Cross-field coherence.** Field-pair invariants the producer pipeline is expected to honor. `Edge.dst_ref ↔ Edge.dst`: populating `dst_ref` requires `dst` to be populated too (the ~34 unmigrated consumer sites still read the legacy colon-delimited form). ADR-0031 Class B coherence: a Symbol must not carry both `language` and `protocol_origin` (file Symbols exempt). ADR-0032 display-label scope: a Class A real-source declaration must not carry `display_label` (which is reserved for synthetic stand-ins).
- **Verdict-enum completeness.** Verdict-emitting dataclasses must document an `inconclusive` (or equivalent "don't know") branch alongside their positive / negative verdicts. Catches the silent-fall-through-to-positive class of bug at the static level. Inaugural registry covers `ClaimVerdict`; future verdict types register here as they are introduced.
- **ID format.** `Symbol.id` is checked against the canonical schema `<language>:<path>:<start>-<end>:<name>:<kind>` and `Symbol.stable_id` against `sha256:<16hex>`; non-conforming values surface tagged with one of ten specific problem categories (e.g. `double_colon_separator`, `raw_hex_no_prefix`). The path-slot regex is intentionally colon-tolerant, so legitimate `::`-bearing module paths like `rust:std::collections::HashMap:0-0:module:module` pass.
- **Stable-ID collision rate** (a sibling cross-field umbrella). The validator groups Symbols by `stable_id`, computes `collided/total`, and emits a single `cross_field` violation when the rate exceeds 5%. One umbrella per run, top-3 collision groups named with sample symbol names. The 5% threshold leaves headroom above the typed-tier-collision floor (same-signature pairs in the same module are by-design) while still catching mass-collision regressions.

#### ID-construction discipline (ADR-0034)

`docs/adr/0034-id-construction-discipline.md` codifies the canonical-factory rule for `Symbol.id` and `Symbol.stable_id`: producers route every ID through the appropriate factory in `analyze/base.py` (`make_symbol_id`, `make_route_stable_id`, `make_entry_stable_id`, new `make_protocol_stable_id(category, *parts)`) rather than constructing f-strings inline. Class B synthetic stand-ins (whose `Symbol.language` is `None`) use the host's `discovery_language` as the canonical-ID language prefix so the canonical schema's first segment stays a real language string the cross-language edge detector can branch on. The ID-format validator class is the runtime enforcement; ADR-0034 is the rationale and reviewer checklist.

Producer migrations landed alongside the validator turn-on:

- **Ad-hoc `{rel_path}::{role}::{line}` path-prefix double-colon form** (six linkers): `http.py` (HTTP call_site Symbols), `database_query.py` (db_query call_sites), `subprocess_cli.py` (subprocess_call call_sites), `message_queue.py` (mq_publisher / mq_subscriber functions), `graphql_resolver.py` (resolver functions), `graphql.py` (graphql_client functions).
- **`websocket.py::_make_symbol_id`** rebuilt on top of `make_symbol_id(...)`. Previously emitted `websocket:{path}:{line}:{event}:{kind}` — non-canonical language prefix (`websocket` is a `protocol_origin`, not a language catalog value) and single-line span (`818` instead of `818-818`). The host file's language now occupies the language slot; the route and role pack into the colon-free name segment with any `:` in the event sanitized to `_`.
- **`make_route_stable_id` and `make_entry_stable_id`** rewired to call `_short_sha256(...)` so they emit the canonical `sha256:<16hex>` shape (23 chars) instead of the raw 64-char hexdigest. Eliminates the `raw_hex_no_prefix` escape category for routes materialised by `framework_patterns.py` and HTTP-client call_site Symbols.
- **`make_protocol_stable_id(category, *parts)`** new factory hashes `(category, parts...)` into the canonical shape. Four protocol linkers migrate off ad-hoc f-strings — `database_query.py` (was `f"{query_type}:{tables}"`), `message_queue.py` (was `f"{queue_type}:{topic}"` — 2-colon when topic contained `:` like SQS ARNs / redis subject patterns), `event_sourcing.py` (was bare `pattern.event_name`), `graphql_resolver.py` (was `f"{type_name}.{field_name}"`). The category prefix protects against cross-linker collisions where two unrelated identity tuples happen to hash the same bare value.
- **Validator-driven cleanup tail** (six producer corrections): Starlette route IDs use `GET /health` instead of `GET:/health` (the `:` broke the 5-segment shape); NPM package IDs gain the missing span slot and switch their `stable_id` to `make_dependency_stable_id`; JSON dependency kinds use the post-fold `dependency` instead of camelCase `devDependency`; Rust impl-method names swap `::` for `.` in the ID name slot only (`Symbol.name` / `qualified_name` keep the native form); and the `decorator-dispatch` / `inherited-calls` linkers fix a registration-vs-runtime PASS_ID mismatch.

#### Per-symbol introspection fields populated across mainstream analyzers

Six `Optional[T]` fields on `Symbol` that the spec validator's writer-contract class had been flagging as universally null are now populated at every declaration emit site across the 10 languages of the `hypergumbo-lang-mainstream` package — Go, Rust, JS, TS, Java, C#, Ruby, PHP, Kotlin, Swift. After this sweep, writer-contract violations across the field × analyzer matrix drop to zero on self-analysis.

- **`lines_of_code: int`** — derived from `span.end_line - span.start_line + 1` per emit site. Synthetic stand-ins with `span=Span(0, 0, ...)` legitimately get `1` (the synthetic occupies one "line" in its conceptual space).
- **`is_exported: bool`** — derived per host language's visibility rule: Go's lexical case (`name[0].isupper()`), Rust / Java / C# explicit access (`"pub"` / `"public"` in modifiers), Kotlin / PHP default-public with opt-out (`private` / `protected` / `internal`), Swift's explicit opt-in (`public` / `open` — default `internal` does not count), Ruby's default-public + lexical-nesting check (top-level / class-body `def`s are exported; methods nested in another `def` are not).
- **`signature: Optional[str]`** and **`docstring: Optional[str]`** — extracted via a new shared dispatcher module `symbol_introspection.py` that routes to per-language helpers already in each analyzer. The dispatcher gates on a `SUPPORTED_LANGUAGES` frozenset; unknown languages return `None`. C# and PHP override `analyze()` and bypass the base-class docstring post-pass, so they call `populate_docstrings_from_tree` explicitly at the end of their `_extract_symbols` to backfill non-callable holders (classes, properties).
- **`qualified_name: Optional[str]`** — derived by walking the file's package / namespace + enclosing class / mod chain and joining via `separator_for_language()` from the `qualified_name_axis` catalog. Never hardcodes the separator. Skipped for variable aliases, TS type aliases, file pseudo-symbols, and route Symbols (URL-shaped, not identifier-shaped). PHP's `App\Service\HelloService::method` form combines the `\` namespace separator with the `::` class-method separator at the canonical join point.
- **`cyclomatic_complexity: Optional[int]`** — McCabe complexity computed by a new shared walker `compute_cyclomatic_complexity(node, language)` against per-language `BRANCH_NODE_TYPES` and `SHORT_CIRCUIT_OPS` sets. Wired into every callable emit site (functions, methods, constructors, arrow functions, lambdas, singleton methods); classes / vars / synthetic route Symbols are not callable bodies and remain `None`. Go's synthesized closure-wrapper Symbol stays `None` (no AST node available).

#### Per-entry-point safety claims and wrapper-function discipline

A per-entry-point taint-flow model distinguishes what each CLI subcommand is allowed to do, verified by `hypergumbo verify-claims`. Key pieces:

- **Claims YAML** (`docs/hypergumbo.claims.yaml`): 18 taint-flow claims. Runtime subcommands cannot reach `host_fs` / `network` / `subprocess` / `install_artifact` / `dev_zone`.
- **Wrapper-function discipline** — zone-tagged wrappers in `safety_zones` for fs-write, mkdir, rmtree, chmod, and unlink primitives.
- **CFG ↔ DDG bridge** — `build_function_cfg → populate_def_use_for_cfg → solve_reaching_defs` now wired end-to-end for Python functions during verification.
- **Post-DDG refinement pass** (`taint_refine.py`) resolves import-rooted method-call receivers, reducing short-name sink overapproximation.
- **`SECURITY.md` generator** — auto-generated from the claims YAML via `scripts/generate-security-md`.

#### Provenance and reproducibility

- **`Edge.derived_from: list[str]`** — every linker-produced Edge records which Symbol IDs were consumed to construct it. Populated across all 55 linker modules.
- **`Pass.depends_on` in Conjunctive Normal Form.** Declares analyzer prerequisites for every linker as outer-AND of inner-OR clauses (e.g., JNI requires "java AND (c OR cpp OR rust)"). Populated across all 57 linkers with static and runtime validators.
- **`AnalysisRun.pass_version` via code-hash.** `compute_pass_version` returns sha256 of the pass module source, replacing the fake `-v1` suffix that bumped on every release regardless of logic changes.
- **`behavior_map["reproducibility_context"]`** captures L2 reproducibility metadata (hypergumbo/Python/tree-sitter/grammar versions) plus an explicit `not_captured` array disclosing what is not recorded (OS, hardware, transitive deps).
- **`hypergumbo explain --provenance`** shows per-edge derivation chains. `explain` now always shows `Origin:` with contributing passes and annotates callers/callees with edge type.

#### New linkers and framework support

- **Inherited-calls linker** (`linkers/inherited_calls.py`) — walks ancestor chains to resolve unresolved `calls` edges. Ships with per-language MRO walkers for Ruby/Groovy and Java. Java's inline parent-chain walk replaced by the centralized linker (5 PRs).
- **Django third-party dispatch linker** — emits `dispatches_to` edges from subclasses of HierarkeyForm, django-filter FilterSet, DRF Serializer family, and Wagtail Page.
- **HTTP route detection — bare-Node + Apollo standalone.** New YAML patterns for `http.createServer` / `https.createServer` and Apollo's `startStandaloneServer` / `runHttpQuery` / `executeHTTPGraphQLRequest`.
- **gRPC — TS client → proto fallback.** Unmatched TS/JS stubs now bind to the proto service Symbol with `is_resolved=False`.
- **Ansible `include_tasks` / `import_tasks` Jinja-templated fan-out.** Two shapes recognized; on fedora-infra/ansible, 191/192 unresolved imports now resolve.

#### IO-boundary improvements

- **Three `external_potential` chain-volume filters**: skip unresolved edges (ADR-0028), closed-world stdlib gating (Python stdlib inaugural), and composition fix for self-prefixed dst names. ~4,500 chains cut on self-analysis.
- **`io-boundaries --json` envelope gains `schema_version`** (IO_BOUNDARIES_SCHEMA_VERSION 1.0).

#### CLI features

- **`hypergumbo run --gzip`** compresses output (~90-95% reduction). `--out` auto-appends `.gz` when the path doesn't already end with it.
- **`hypergumbo run --no-sketch-fan-out`** — explicit named alias for `--budgets none`.
- **`behavior_map["features"]` populated** with spec-shape index entries for detected route handlers. Stable feature IDs enable diff-across-commits.
- **Corpus-driven schema-coverage ratchet gate.** Self-analysis exercises only ~20% of canonical registries. New CI gate runs against a 10-fixture multi-language corpus (~5s) with a shrink-only baseline.

#### Other additions

- **Canonical `Symbol.meta` / `Edge.meta` key registry** (`axis_meta_keys`) — structural sibling of existing kind/type registries with drift detection.
- **Solidity `contract` kind registered canonically** as a top-level construct sibling to `class` / `interface` / `struct`.
- **Solidity / Vyper `modifier` symbol kind registered canonically** in `symbol_kinds.py` under `AXIS_LANGUAGE_CONSTRUCT`. The Solidity analyzer was already emitting `add_symbol(mod_name, "modifier", ...)`; the catalog now recognizes it.
- **CI lint enforcing axis declaration** on every `str`-typed field of core dataclasses (`ir.py`, `datamodels.py`).
- **Intra-file variable reference edges** for Python module-level constants. Functions reading constants now emit `references` edges, reducing orphan variable Symbols.
- **Orphan-node triage.** Orphan rate dropped from 5.5% to 2.0%; ratchet test prevents regression.
- **Canonical dampener stack pinned end-to-end** — four tests catch internal-reorder regressions.
- **RCT-consumer public-API surface pinned** via introspection tests.
- **Bridge linker activation ↔ depends_on drift guard** — property test asserts every Bridge-subcategory linker that declares both `activation.language_pairs` and `depends_on` encodes the same constraint (after language→pass-id resolution for the JS/TS/Vue/Svelte sharing case). Adding an impl language to one declaration but not the other now fails CI rather than silently diverging the gate.
- **HIGH_RISK_PRIMITIVES drift guard, Part 2 (missing-entry direction)** — property test asserts every catalog entry with `boundary=subprocess` is classified in either `HIGH_RISK_PRIMITIVES` or the new `HIGH_RISK_EXEMPTIONS_SUBPROCESS` frozenset, closing the gap Part 1 did not cover. Backfilled 48 missing subprocess-launching primitives across Go, JVM, Node, C/C++, Elixir, Haskell, Swift, Objective-C, and Rust. Exempted 18 wait/signal/PATH-lookup/self-exit entries that are subprocess-boundary for taint tracking but don't represent arbitrary code execution.


### Changed

#### Schema — concept-axis closures

- **SCHEMA_VERSION 0.6.0 → 0.7.0 — `Edge.evidence_type` endpoint_shape closure.** All 111 endpoint_shape values removed: 18 resolution-status leaks → canonical + `Edge.is_resolved=False`; 65 framework-dispatch values → canonical + `meta["framework_dispatch"]`; 28 call-construct peers → apex `ast_call` + `meta["call_construct"]`.
- **SCHEMA_VERSION 0.5.8 → 0.6.0 — `Symbol.kind` endpoint_shape closure.** All 71 endpoint_shape values removed: framework roles → canonical kind + `meta["framework_role"]`; edge labels → `call_site` + `meta["call_kind"]`; file-shape, build-config, and long-tail values fold or drop.
- **CUDA / Android XML canonical-kind folds.** CUDA now emits `kind="function"` + `meta["cuda_execution_space"]`; Android XML emits `kind="component"` + `meta["component_type"]`.
- **Producer-coherence linter extended** — inline ternary resolution, non-string Constant handling, f-string expansion mode, and variable-form backstop. Six new `AXIS_PENDING` values registered; SCHEMA_VERSION 0.7.0 → 0.7.1.
- **`Symbol.origin` and `Edge.origin` changed from `str` to `list[str]`.** Multi-source attribution: when multiple passes contribute, all are credited. SCHEMA_VERSION 0.9.1 → 0.10.0.
- **`origin_run_signature` removed from output schema.** SCHEMA_VERSION 0.10.0 → 0.11.0.
- **SCHEMA_VERSION 0.11.0 → 0.12.0 — Symbol-field axis decomposition.** Caps the combined ADR-0031 (`Symbol.language` → `discovery_language` + `protocol_origin`) and ADR-0032 (`Symbol.canonical_name` → `display_label` + `qualified_name`) closures. Four new dataclass fields land at the typed boundary; `Symbol.language` relaxes `str → Optional[str]` for Class B synthetic stand-ins; `Symbol.canonical_name` is marked deprecated.
- **SCHEMA_VERSION 0.12.0 → 0.13.0 — `Symbol.canonical_name` removed** (breaking; one schema version after the 0.12.0 deprecation). The `qualified_name or canonical_name` fallback at `linkers/containment.py` and `framework_patterns.py` collapses to `qualified_name` alone; consumer migration path is `symbol.qualified_name` / `dict["qualified_name"]`. `from_dict()` silently ignores legacy `canonical_name` keys in pre-removal cached JSON. See §Removed below.

#### Catalog and pass identity

- **`pass_id` suffix dropped; catalog auto-derived from registries.** Breaking JSON-output change. The legacy `-v1` / `-ts-v1` / `-ast-v1` suffixes are removed; `make_pass_id(name) == name`. Backend identity moves to `Pass.backend`; display labels to `Pass.pass_label`. Catalog is now dynamically derived from `_ANALYZER_REGISTRY` + `_LINKER_REGISTRY`.
- **Results cache key includes analyzer identity.** Two different hypergumbo installs analyzing the same tree no longer share a cache entry.
- **`all_known_pass_ids()` extended with built-in pipeline + synthesis-mechanism sets.** Two new frozen sets register pass-id values that the catalog had been missing — `_BUILTIN_PIPELINE_PASS_IDS = {"enclosure-linker"}` covers the synthetic post-pass at `linkers/registry.py` that connects synthetic stand-ins to enclosing functions; `_SYNTHESIS_MECHANISMS = {"inheritance", "orchestrator_file_symbol_synthesis", "scip"}` covers the synthesis-mechanism values currently overloaded onto `Symbol.origin` (their split into a sibling `synthesis_mechanism` field is a future ADR). Until that split lands, the catalog accepts these values as legitimate.
- **Three analyzer-side language-tag drifts harmonized to catalog-registered values.** `objc.py` now emits `"objc"` (was `"objective-c"`), removing three downstream translation-table accommodations; `yaml_ansible.py` registers `"ansible"` as a known language; `grpc.py` proto synthetics emit `"proto"` (was the non-catalog `"protobuf"`). `stable_id` values for objc / proto Symbols change in this release (language is a hash input).

#### Vendored grammars

- **Source-built tree-sitter grammars (lean, wolfram, circom) vendored** under `vendor/tree-sitter-*/`. Eliminates the upstream-force-push failure mode. Both build paths now read directly from the vendor tree (no `git clone`). Each grammar ships its LICENSE and an UPSTREAM file for the re-sync procedure.

#### Linker quality

- **Linker `pass_version` wired through `run_all_linkers`** — `_stamp_pass_version()` centrally stamps each linker's `compute_pass_version` code-hash onto its `AnalysisRun.pass_version`. Previously all linker-created runs had empty `pass_version`. `LinkerContext` gains `create_run()` factory and per-linker identity fields.
- **`AnalysisRun.version` semantic split fixed** — analyzers now pass `version=PASS_VERSION` (package version) and `pass_version=self.pass_version` (code-hash). Previously analyzers put the code-hash in `version`, making `run_signature` semantically incomparable across analyzer vs linker runs.
- **Disambiguation-fallback discipline** — thirteen linkers adopt `confidence ≤ 0.5` + `meta["disambiguation_fallback"]=True` for ambiguous simple-name resolutions. New fallback-coherence linter pins the contract statically.
- **URL-folding logic extracted** from the HTTP linker into a per-idiom YAML + engine substrate (`url_folding/`), preparing for multi-language extension.

#### IO-boundary catalogs

- **stdio → logging reclassification** applied to C, Rust, JavaScript, and Elixir catalogs. Cuts ipc_send false positives on non-Python codebases.
- **Rust and Erlang catalogs promoted to `status: complete`** with `stdlib_provenance` audit trail.
- **Taint auto-mapping coverage gap closed** — `db_write`, `db_read`, `process_send`, and `logging` boundary types now have `AUTO_SINK_ZONE_MAP` / `AUTO_SOURCE_LABEL_MAP` entries. Regression guard test prevents silent gaps when new boundary types are added.
- **HIGH_RISK_PRIMITIVES drift guard** — property test asserts every entry exists in at least one `io_primitives/*.yaml` catalog, preventing phantom entries. Fixed `stdio.popen` → `stdlib.popen` to match the C catalog.

#### Other changes

- **`io-boundaries` hides `external_potential` bucket** from default text output (was drowning per-primitive view). New `--show-external-potential` flag opts back in.
- **Circom analyzer gates on actual `.circom` files** instead of warning whenever the grammar is unavailable. Partial-install TOML warnings suppressed on irrelevant repos.
- **`hypergumbo run --out` help text lists side-output files** (compact-tier previews, handler slices).
- **Ten `git rev-parse` call-sites hardened** against unverified-ref stdout contamination.
- **Framework `Pattern.meta_match` field** re-binds YAML rules to post-fold emission shapes (canonical kind + meta keys).
- **`Symbol.fingerprint` populated** for source-code Symbols via centralized AST/tree-sitter structural hashing. The seven config / data-language analyzers (`cmake.py`, `css.py`, `json_config.py`, `toml_config.py`, `sql.py`, `xml_config.py`, `wasm_bindgen.py`) that had been emitting a producer-side 16-char prefix-less hash now also funnel through this central post-pass, so every Symbol's `fingerprint` is now in canonical `hgfp1:<64-char-sha256>` (Format 2) form. TOML dependency nodes had been the visible drift case (99 nodes per run carried the Format-1 hash).


### Removed

- **`apply_sibling_impl_weights` removed from dampener stack** (8 → 7 stages). A 6-repo audit found zero top-100 movement; the upstream `apply_common_method_name_weights` already handled the same groups.
- **`origin_run_signature` removed from Symbol and Edge** — never stamped by any producer (zero writes across all analyzers and linkers). `from_dict()` silently ignores the key for backward compatibility with pre-removal JSON.
- **`requires_symbols` removed from `RegisteredAnalyzer` and `@register_analyzer`** — a never-passed, never-consumed multi-pass-symbol-consumption stub superseded by `depends_on`, which carries CNF pass-id dependencies that are actually validated.
- **`Symbol.canonical_name` field removed** (breaking). One schema version after the 0.12.0 deprecation window; the field is dropped from the `Symbol` dataclass declaration and the `to_dict` / `from_dict` round-trip at SCHEMA_VERSION 0.13.0, and from the JSON Schema's `#/$defs/Symbol/properties/canonical_name` entry at 0.14.0 (the hand-coded schema had kept it; see §Fixed "Schema-vs-dataclass drift"). Consumers should read `symbol.qualified_name` / `dict["qualified_name"]` instead. `from_dict()` silently ignores legacy `canonical_name` keys in pre-removal cached JSON for backward compatibility. Migration rows in `docs/MIGRATION-6.0-CONCEPT-AXES.md`.


### Fixed

#### CLI

- **`hypergumbo slice` output summary** now reads "Generated N artifact(s)" (was truncated) and duplicate artifact listings across 8 subcommands fixed (operator-precedence bug).
- **`hypergumbo symbols` Kind column** no longer truncates (e.g., "functi…"). Width computed from data.
- **All `--input`-taking subcommands handle `.gz` files.** New shared `load_behavior_map()` routes all 11 consumer sites.
- **`limits.failed_files[]` now actually populated.** Previously always `[]` even when files were dropped. Now records `{path, reason, analyzer}` across 29 producer sites.
- **`remove-extras` now actually uninstalls source-built grammars** (previously no-op'd).
- **`hypergumbo explain Symbol | head`** no longer prints a BrokenPipeError traceback.
- **Display polish** (5 fixes): `--help` metavar dynamically lists all subcommands; `routes` output sorted deterministically within files; `io-boundaries` tier tag moved to primitive header; `explain` summaries print before source dumps; test-density section header no longer mislabels high test usage as "redundant."
- **Sketch progress no longer contaminates captured stderr.** Progress producers now gate on `sys.stderr.isatty()`.
- **Comparison-budget sketches** now write to the results cache instead of accumulating in `/tmp/`. Legacy `/tmp/hypergumbo_sketch_compare/` cleaned up on first run.

#### Identity and dedup

- **Python class `stable_id` collisions fixed.** Class body signature (method names, field names, base names) now folded into the hash. Previously, five `@dataclass` classes in `ir.py` shared one `stable_id`. STABLE_ID_SCHEME bumped to v3.
- **Cross-module `stable_id` collisions fixed.** File identity threaded into top-level class and function `stable_id` computation. Structurally-identical classes in different modules now produce distinct hashes. STABLE_ID_SCHEME bumped to v4.
- **Same-module mass collisions fixed.** `compute_stable_id` hash signatures gain `name` and `qualified_name` segments, threaded through every analyzer call site (~30 analyzer files). Pre-fix self-analysis showed 60.2% of Symbols sharing a `stable_id` with at least one other (20,517 of 34,108) — e.g. 155 zero-parameter bash functions in one file all hashing to a single ID. Trade-off: the contract is rebranded — `stable_id` now means "structural identity within a (qualified_name, module_path) scope; survives BODY edits, NOT rename or move." STABLE_ID_SCHEME bumped to v5. The typed-tier factories are unchanged.
- **Eight Symbol kinds now carry `stable_id`** (variable, module, dependency, export, project, interface, type, file). Previously 6.1% of Symbols had `stable_id=None`. A backstop pass stamps kind-specific values.
- **Three file-id dedup fixes** (websocket, js_module, vue_component linkers). All emitted file Symbols with legacy id shapes that never collided with canonical ids, preventing cross-producer dedup. Each now uses `make_file_id()`.
- **File/module double-representation collapsed.** Python (then JS/TS, Bash, Perl, PHP, PowerShell) no longer emit both `kind="module"` and `kind="file"` for the same path.
- **JS/TS import edges use canonical file Symbol ID as `src`.** Previously every import edge pointed at an orphan node.
- **Websocket linker path normalization.** Absolute paths in file ids prevented dedup against analyzer-emitted repo-relative ids.

#### Analysis correctness

- **Linker synthetic stand-ins in TypeScript files now tagged `typescript`, not `javascript`.** The event-sourcing, database-query, and graphql-resolver linkers hardcoded `language="javascript"` on the intermediate pattern records they scan from `.js`/`.ts` source, ignoring the file extension. After the ADR-0031 Class B migration that hardcode flowed into `Symbol.discovery_language` and the canonical `id`'s first segment, so a stand-in discovered in a `.ts` file was tagged `javascript` — masking real JS↔TS cross-language edges and disagreeing with the language the JS/TS analyzer assigns to real declarations in the same file. All three now infer the tag from the extension via a shared `js_ts_language_from_path` helper (analyzer parity: `.ts`/`.tsx` → `typescript`, else `javascript`), into which the pre-existing correct `ipc.py` copy is folded.
- **SQL `CREATE TABLE` entities no longer dropped by `_NOISE_KINDS` filter.** The `"table"` entry intended to suppress TOML/INI sections also suppressed SQL tables, leaving the database_query linker unable to produce edges. Now language-gated.
- **Solidity import-alias scan no longer misreads `require()` error-message strings as import paths.** `solidity.py::_extract_import_aliases` was being called on every AST node, not only `import_directive` nodes. The helper finds the first `string` child and uses its text as the import path; on a `require(condition, "Not owner")` call (and similar patterns with string-literal arguments), it was falling back to that string. The Solidity analyzer was emitting an `imports` edge with `dst="Not owner"`, which `ir.py:synthesize_file_symbols_for_dangling_edges` then materialized as an `external_symbol` Symbol with `language="Not owner"` and an `id` of the same shape. The loop body now gates on `node.type == "import_directive"`; legitimate imports continue to resolve.
- **JS/TS HTTP/GraphQL server-handler UC extraction.** Framework pattern rules for Node HTTP and Apollo were silently no-ops because the analyzer only emitted UCs for a small bootstrap-names allowlist. New extractor covers the full target set.
- **JS/TS `access_mode` annotation coverage on call edges.** Calls inside `return` / `throw` / `yield` / `await` were unclassified, leaving `--dataflow` slices empty on TypeScript repos. Adds positional rules for those contexts plus expanded `library_patterns` for mutators, ORM verbs, RxJS, EventEmitter, and Promise/Observable readers.
- **Apollo HTTP-entrypoint patterns relocated** from framework-gated `graphql.yaml` to always-loaded `node-http.yaml`, fixing detection on workspace-imported Apollo repos.
- **React Router fixes**: dynamic-path expressions no longer emit false-positive routes; v5 `render` prop recognized.
- **Framework detection: structured manifest parsing.** Previously used substring matching on raw manifest text, causing false positives (`"torch"` from a pytest marker, `"transformers"` as substring of `"sentence-transformers"`). Now uses structural parsers for ~30 manifest formats across all supported ecosystems.
- **Framework detection: layered `requirements/` files and `-r`/`-c` include chains.** Repos with `requirements/base.txt` instead of top-level `requirements.txt` now detect frameworks correctly.
- **Framework `refine_frameworks` promote phase.** Frameworks imported in production code but absent from manifests (workspace monorepos, lockfile-only installs) are now detected. Bare single-token names still require manifest detection to avoid false positives. Cross-ecosystem guard prevents Python stdlib imports from promoting foreign-language frameworks.
- **`materialize_route_symbols` produces per-file route Symbols** for kind=file source concepts. Different files calling the same framework entry point (e.g., multiple Apollo standalone servers) no longer collapse to one route.
- **Java wildcard imports** (`import java.util.*`) now resolve to the source package for class-shaped receivers.
- **Ruby constructor-call `.new` redirect** walks the inheritance chain when the named class doesn't define `#initialize` directly.
- **Rails routes are now distinct entrypoints**, and `dispatch_inherited` handles Ruby's `Class#method` separator.
- **Python nested function defs** emitted as Symbols with qualified names; bare-name calls resolved via scope walk (LEGB rule). Previously ~121 missing Symbols and ~360 missing call edges on self-analysis.
- **Python BOM-prefixed files** no longer silently dropped. Switched to `utf-8-sig` codec.
- **Receiver-type inference extended** to Kotlin (nullable `?` stripping) and C# (`Task<T>` / `ValueTask<T>` unwrapping).
- **N-API template forms and PyO3 `#[pymethods] impl` propagation** expanded for modern node-addon-api and canonical PyO3 crates.
- **WebSocket linker emits cross-language client↔server bridge edges.** Template-string URLs, Starlette `WebSocketRoute`, and cross-language pairing logic added. Self-analysis: 0 → 12 WS bridge edges.
- **Bash function Symbols now populate `lines_of_code`** (previously always `None`).

#### Entrypoint detection

- **Bash/sh scripts** now recognized as entrypoints via `shell_script` concept.
- **`index.html` SPA roots** recognized as entrypoints via `html_entry` concept.
- **TS/JS standalone-script modules** (no inbound imports + has outbound calls) recognized via `script_module` kind. Cumulative impact: 64 → 97 entrypoints on self-analysis (+52%).
- **Main-function dedup** — `detect_entrypoints` no longer emits both a module-level main-guard and a `main()` function entry for the same script.

#### Supply chain and coverage

- **Test directories no longer route to `supply_chain.tier=2` (internal_dep).** Tests are first-party. Previously 99.8% of tier-2 paths on self-analysis were test files.
- **`profile.languages` no longer double-counts** shell scripts under both `bash` and `shell` keys.
- **`profile.languages[L].files` agrees with `analysis_runs[L].files_analyzed`** for languages with custom file finders (e.g., bash extensionless shebang scripts).
- **`metrics.total_files` is now canonical** — equals `len({n.path for n in nodes if n.path})` (node-distinct path count). The legacy profile-language sum (over-counted by ~296 vs node-distinct on self-analysis) now rides in `metrics.debug.profile_files_sum` for introspection.
- **`metrics.by_supply_chain_tier["unknown"]` no longer minted.** Edges whose `src` isn't in `node_id_to_tier` were producing a phantom `unknown: {edges: 23, nodes: 0}` bucket on self-analysis; they're now silently excluded from the per-tier edge count.
- **`total_io_edges` canonical definition codified** in `io_boundary.py` as `sum(len(e.chains) for e in entries.values())` (post-`external_potential` chain count). The pre-external_potential `tagged_count` reference at the unfiltered-serializer site is gone; the filtered path in `cli.py:cmd_io_boundaries` already used the post-chain-sum convention, so both paths now agree.
- **Sketch and `test-coverage` report the same percentage** on identical input. Previously a 34-point discrepancy due to edge-set and test-identification methodology differences.
- **Sketch structure tree no longer renders `<external>` placeholder** as a root-level file.

#### Taint-flow

- **`subprocess` boundary auto-derives its own taint zone** instead of collapsing into `host_fs`. Shelling out to trusted external programs no longer triggers `*-no-host-fs` claims.
- **`Path.mkdir` callsites routed through safety_zones wrappers** — three new wrappers (`cache_mkdir`, `tmp_artifact_mkdir`, `install_artifact_mkdir`).
- **`taint_refine` pins parameter-receiver types** from function-signature annotations. `name: str` → `name.replace(...)` no longer matches `pathlib.Path.replace` as an fs_write sink.

#### Provenance and schema integrity

- **`Edge.origin` / `Edge.origin_run_id` enforced non-empty at construction.** Previously 425 edges had empty provenance. 67 construction sites fixed; `from_dict()` injects a sentinel for legacy JSON.
- **Every Symbol-producing linker now stamps `origin` and `origin_run_id`.** Previously 95 Symbols from 12 linkers had empty provenance.
- **`AnalysisRun.config_fingerprint` consistently populated with per-class fingerprints.** 11 analyzers that had been bypassing the factory method now auto-default via `__post_init__`. Pre-Phase-6 every one of the 84 self-analysis runs carried the literal `sha256:44136fa355b3678a` (sha256 of `{}`) because `AnalysisRun.create(pass_id, version)` was being called with no config arg; the new `TreeSitterAnalyzer._get_config_dict()` + `_stamp_config_fingerprint()` derive a per-analyzer `sha256:<16hex>` fingerprint from class identity + grammar + file-pattern set. Subclasses can override `_get_config_dict()` to thread real per-run config.
- **`AnalysisRun.pass_version` auto-stamped for tree-sitter analyzers.** Mirrors the existing linker-side stamping. `TreeSitterAnalyzer._analyze_body` now auto-stamps `pass_version = compute_pass_version(type(self))` when the subclass hasn't set one explicitly. 44 previously-unstamped tree-sitter analyzer runs now carry a real code-hash.
- **`AnalysisRun.toolchain` reflects the dependency chain that produced the analysis.** New `_extend_toolchain()` extends the default `{name: python, version: <host>}` with `tree_sitter_version`, `grammar_module`, and `grammar_version` (when the grammar package exposes `__version__`). Replaces the prior host-Python-only stamp.
- **`AnalysisRun.warnings` populated on the grammar-unavailable producer path.** `TreeSitterAnalyzer._analyze_body` now explicitly appends the grammar-unavailable skip message to `run.warnings` before calling `warnings.warn` — thread-safe across the analyzer-runner `ThreadPoolExecutor`.
- **`Edge.quality` derived from evidence.** New `_derive_edge_quality()` helper in `ir.py` populates `quality = {score, reason}` from `confidence` / `is_resolved` / `derived_from` when the producer doesn't set it. Reason tags: `high_confidence_direct` (≥ 0.95), `resolved_call_site` ([0.8, 0.95)), `derived_from_linker_evidence`, `medium_confidence`, `low_confidence_fallback` (< 0.5).
- **`Limits.add_classification_failure` now wired up.** Pre-fix the method existed but had no callers, so `Limits.classification_failures` was always empty on disk. `_classify_symbols` now accepts an optional `limits` kwarg, records each "outside repo" classification fall-through with per-path dedup (no N-copies for N symbols on the same un-classifiable path), and is wired from `cli.run_behavior_map`.
- **`AnalysisRun.repo_fingerprint` computed** per the spec algorithm. Previously `None` on 100% of runs.
- **Self-analysis validates against `docs/schema.json`.** Fixed `line=0` on module_exports edges and added missing top-level keys. SCHEMA_VERSION 0.8.0 → 0.9.0.
- **Schema conformance + coverage gates folded** into one ~5s CI step (was 3.5 min).
- **HTTP linker emits `kind="call_site"`** for client call sites (was `kind="function"`, causing dead-code false positives).
- **Orchestrator file-symbol synthesis** no longer stamps absolute paths into `Symbol.name` or hardcodes `span=1-1`.
- **WebSocket linker no longer creates phantom `kind="file"` Symbols** with wrong language and missing `stable_id`.

#### Schema-vs-dataclass drift (SCHEMA_VERSION 0.13.0 → 0.14.1)

The schema generator claimed "auto-generated from Python dataclasses" but hand-coded the core $defs as literal dicts, so dataclass changes never propagated — by 0.13.0 the published schema rejected every real linker-bearing document (262 `language: None is not of type 'string'` errors on the ADR-0031 Class B stand-ins) while CI stayed green on a fixture too small to ever produce one. Fixed at the root:

- **$defs introspected from the dataclasses.** New `scripts/generate_schema_lib.py` derives each $def's property set, JSON types, nullability, and required-ness from `dataclasses.fields()`, merged with curated per-field descriptions and annotations. Generation hard-fails on drift in either direction (stale decoration / undecorated new field), and a round-trip check pins each $def's property set to `to_dict()` output.
- **`Symbol.language` nullable; stale properties corrected.** Class B synthetic stand-ins (`language=None` + `discovery_language` / `protocol_origin`) now validate; the `canonical_name` property removed from the dataclass at 0.13.0 is finally gone from the schema; the four ADR-0031/0032 sibling fields and `AnalysisRun.failed_files` / `pass_version` are declared.
- **Conformance-fixture blindness closed.** A new end-to-end test analyzes a SQL + Python fixture that fires the database-query linker and validates a whole document that actually contains `language=None` nodes — the case the old single-file pure-Python fixture could never produce.
- **Class B stamping canary relocated into the spec validator**, so tolerating `language=None` doesn't silence the under-stamping signal those 262 errors had been carrying: one umbrella cross-field violation per missing identity field (`stable_id`, `fingerprint`, `discovery_language`, non-empty `origin`).
- **Opaque top-level blocks typed; missing keys declared** (0.14.0 → 0.14.1, additive). `limits`, `features[]`, and `metrics` get real definitions (introspected `Limits` / `Feature` / `SliceQuery` $defs plus declared metrics keys), and three always-emitted top-level keys the schema never mentioned — `reproducibility_context`, `symbol_fingerprint_scheme`, `validation_report` — are now present. Each non-dataclass block's property set is pinned to its actual producer by contract tests.
- **`reproducibility_context.implications` fixed** to reference `analysis_runs[].pass_version` (where the per-pass code hashes actually live) instead of `pass_versions`, a key `captured` never carries.

#### Symbol fingerprints — context-aware rewrite (`symbol_fingerprint_scheme` v1 → v2)

The v1 fingerprinter sliced each Symbol's span out of its file and parsed the slice as a standalone document; spans that don't parse out of context degraded silently. v2 parses each file once and hashes the parse subtree covering the span, so span content is always seen in its real syntactic context. Subtree-rooted walks change every emitted value, hence `hgfp1:` → `hgfp2:`.

- **TOML dependency fingerprint collapse fixed (WI-falum, regression vs 5.0.1).** All 76 TOML dependency nodes shared ONE fingerprint: a single-line array element (`"rich~=14.3.2",`) parses standalone to an ERROR tree whose leaf walk drops the content. In file context each dependency hashes its own content; spans pointing at part of a container hash the fully-contained children, and unparseable spans yield `None` — never a shared constant. Also fixed en route: grammars that don't materialize content as leaf nodes (tree-sitter-toml's `string` has only its two quote tokens as children) now contribute the uncovered gap text, whitespace-stripped.
- **Python test-method fingerprints no longer null (WI-lisog facet a).** ~3,911 test methods had `fingerprint=None` because a method embedding a column-0 triple-quoted fixture defeats the `textwrap.dedent` retry. Parsed in file context the method fingerprints fine; the dedent path survives only as the fallback for files that genuinely don't parse.
- **WGSL producer-side bare-hex fingerprints demolished (WI-lisog facet c, 4 emit sites).** `wgsl.py` stamped raw `sha256(bytes)[:16]` with no scheme prefix — a second algorithm and format under the one declared scheme. The central post-pass now solely owns `Symbol.fingerprint`.
- **Fingerprint degeneracy umbrella check** added to the spec validator (`cross_field`): one warning names fingerprint values shared by ≥ 10 distinctly-named symbols, so the WI-falum signature (76 symbols / 67 names / 1 value) can no longer ship invisibly.
- **Spec fingerprint definition corrected (WI-pupij).** The spec claimed `fingerprint` = `sha256(source_bytes)`; the field is and always was a structural hash modulo whitespace/comments. Spec and schema descriptions now state the structural semantics, the scheme prefix, and the null conditions.

#### verify-claims hardening

A campaign closing the silent-false-confirmation class of bug: every path that previously returned `confirmed` (or a raw traceback) without actually checking anything now resolves to a distinct verdict or a clean error.

- **New `inconclusive` verdict for unconstrained claims.** Both `verify_claim` and `verify_taint_claim` fell through to `verdict="confirmed"` when no machine-checkable constraint matched the claim, making "no constraint to check" indistinguishable from "checked and passed." The unconstrained case now resolves to `inconclusive`, with a `?` console icon, a per-verdict summary line, and new CLI exit code `2` for "at least one inconclusive, zero violated." Exit 0 still means all confirmed; 1 still means at least one violated.
- **Blind analyses no longer confirm `must_not_exist` boundary claims.** A zero-chain boundary map could mean "genuinely no I/O" or "the analysis couldn't see the I/O" (no call edges at all, or a supported language producing zero call edges); both confirmed at exit 0 — e.g. a Node+Python service that provably does `http.get` / `fs.readFileSync` / `child_process.exec` got `confirmed` on all its `must_not_exist` claims. A new `BoundaryCoverage` signal, derived from call-edge production per supported language, downgrades the would-be confirmation to `inconclusive` when coverage is incomplete. Coverage never masks a real `violated` verdict: found evidence is positive regardless of blind spots.
- **Taint propagation honors module qualifiers and `ambiguous_names`, ending a false-VIOLATION cascade.** Both propagation passes matched sources/sinks on bare callee name, so every `str.replace` / `dict.replace` call matched the filesystem-write `Path.replace` sink and `sys.stdout.write` mis-routed into the `StreamWriter.write` net-send sink — thousands of false `violated` rows on the project's own self-claims doc. Matching now mirrors `io_boundary`'s module-aware catalog lookup: a callee with a module hint is filtered by module match, and an ambiguous short name (`replace` / `write` / `run` / …) with no usable module hint matches nothing instead of the first entry. On the self-claims doc, violated evidence dropped from 5,975 to 1,266 rows; genuine module-matched flows (`subprocess.run`, `shutil.copy`) are retained — a real chain is never downgraded. (A small residual — `copy` is not yet in `ambiguous_names` — is tracked separately.)
- **CLI `--taint-sources/-sinks/-sanitizers` flags now actually override claims-file `extra_catalogs` entries.** The CLI and claims-file paths were concatenated into one layer with no intra-layer dedup, so a CLI entry matching a claims-declared `(module, name, kind)` triple was *added* as a duplicate rather than *replacing* it — a downstream project narrowing its threat model got a false result. The two are now distinct layers: CLI wins over claims-file for sources/sinks; sanitizers concatenate.
- **Claims files are validated at load time instead of tracebacking or silently confirming.** Malformed YAML, wrong-shape roots, unknown field names (typos like `constrant` were silently dropped into defaults-populated claims), and unknown `constraint.boundary` values (which made `must_not_exist` silently **confirm** against a boundary the analyzer never produces) now all raise a single `ClaimsFileError` → clean stderr message at exit `2`, with a did-you-mean hint for unknown fields. The boundary vocabulary is single-sourced from the io-boundaries catalog; empty claims files still load as zero claims. `verify-claims --help` now documents the claims YAML shape and exit codes.
- **Bad `--taint-*` catalog paths error instead of silently confirming or tracebacking.** The taint block only ran when a claim carried a `taint_flow` constraint, so a bad `--taint-sources` path alongside boundary-only claims was never even resolved — silent "all CONFIRMED" at exit 0. Taint paths are now resolved and validated whenever present (valid-but-unused flags print a warning), and catalog load failures (parse error, wrong-shape sections, invalid `start_at`) surface as a clean `TaintCatalogError` at exit `2` — a broken taint config can never produce a `confirmed` or `violated` verdict.

#### Dead-code analysis

- **`dead-code-maybe` now demotes** view_func-reachable symbols (route handlers, decorator callbacks) and polymorphic-dispatch overrides. Two heuristics: usage_contexts cross-reference and ancestor-chain method matching.

#### Other fixes

- **Secret scan was a silent no-op under gitleaks 8.30+.** gitleaks 8.30 removed the `detect` subcommand and repurposed `--pipe` to scan the working directory instead of stdin, so `scan_content` always returned `[]` — `hypergumbo sketch` printed "Secret scan complete" while live secrets passed through unfiltered. Switched to the `gitleaks stdin` subcommand, with a real-binary regression guard that feeds a known secret through the actual binary; the contract break was invisible to the mocked-subprocess suite that carried line coverage, which is exactly why it shipped.
- **`hypergumbo run` no longer touches the network on cached embedding loads.** Despite `local_files_only=True`, HF Hub's metadata API, the xet freshness ping, and a `transformers` background thread issued outbound requests on every runtime invocation — violating the `runtime-cli-no-network` claim that the generated `SECURITY.md` advertises. `HF_HUB_OFFLINE=1` is now forced *before the first `huggingface_hub` import* (the offline switch freezes at import time), gated on every embedding model already being cached so the one-time first-install download is unaffected. Verified end-to-end with a process-global socket guard. A new spec section documents `HF_HUB_OFFLINE`, `HYPERGUMBO_VERBOSE`, and `HYPERGUMBO_MIN_MEMORY_MB`.
- **`SymbolByName` helper** replaces silent single-value dict overwrite in Verilog (and applicable to Rust, VHDL). Same-named symbols of different kinds no longer collapse to whichever was inserted last.
- **`--backend rust-analyzer` crash diagnostics.** No longer silently falls through to tree-sitter on crash; OOM-kill named explicitly; exit code and stderr tail surfaced; zero-engagement warning added.
- **`scripts/auto-pr` accepts `--title` and `--description` flags** (previously fell through as positional args, mangling 9+ PR titles).
- **`scripts/prepare-release` no longer swallows push failures.**
- **Merge polling re-checks PR state after exhausting retries.** Codeberg occasionally returns HTTP 405 despite successfully processing a merge; the mid-loop `_check_pr_merged` caught this between attempts, but the final attempt fell through to the error path without a last-chance check. `scripts/lib/forgejo-api.sh` now runs one more state probe after the last retry.
- **`is_utility_file` false-positive fixed** — no longer fires on `<pkg>/utils/` at arbitrary depth.
- **Phoenix/Elixir test files classify as tier=1** with `is_test=True` (was tier=2).
- **`yjs_crdt` linker gates on a real Yjs dependency** (was firing on generic Vue/Rails/Express patterns).
- **Blade analyzer enrols on Laravel repos** (`.blade.php` compound suffix was not indexed).
- **`type_hierarchy` dispatches through interface-extends-interface** in Go and C#.
- **Nightly grammar build re-pinned** after upstream force-push; SHAPE_ID_SCHEME bumped to v2.
- **Analyzer dispatch pre-filtered by file presence.** 113 of 133 analyzers were dispatched to repos with zero matching files, consuming ~13% of wall-clock. Now skipped with reason recorded in `limits.skipped_passes`.
- **CI pins `urllib3>=2.7.0`** for CVE-2026-44431 / CVE-2026-44432.
- **`--backend rust-analyzer` install advice** mentions `--force` and `pipx inject`.
- **`yaml_catalogs` registry** loader attribution corrected.
- **Test-infra: HuggingFace model re-downloads** no longer triggered per-test by the cache isolation fixture. Pins `HF_HOME` and `HF_HUB_OFFLINE=1`.
- **Docs fixes**: `verify-claims` README example corrected; LOC metric documented as SLOC convention; audit-findings front-matter aligned with resolved state; framework autoload-by-convention cross-referenced.


### Documentation

- **ADR-0022** status update: by-category drift detection landed; by-language `LanguageProfile` deferred.
- **ADR-0017** implementation note: sinks now derived from `io_primitives/*.yaml`; built-in `taint_sinks/` removed.
- **SCIP generalization vision sketch** added (`docs/future/scip-generalization-vision.md`).
- **`docs/surveys/` directory established** as the third documentation bucket alongside ADRs and audit-findings, with the symbol-emit-coherence audit (catalog conformance, ID-format conformance, per-language field-population parity) as the inaugural survey.
- **Version-line docs renamed for the 6.0.0 release.** `docs/RELEASE-NOTES-5.X.md` → `RELEASE-NOTES-6.X.md` (a stub keeps the PyPI-published 5.x links alive) and `MIGRATION-5.X-CONCEPT-AXES.md` → `MIGRATION-6.0-CONCEPT-AXES.md`, with cross-references updated across the READMEs, spec, and ADRs.

#### Agent process

The autonomous-agent workflow is itself a maintained surface of this repo:

- **Twenty-pass dogfood procedure** — vendor-neutral playbook orchestrating multi-pass dogfooding tranches as sequential sub-agent chunks, structured so discovery stays blind to convergence (a campaign-position-free issue ledger plus a separate orchestrator-only pass→row→severity map). Backed by a delete-only ledger de-leaker (`scripts/deleak-ledger`) and root-review / combined-trend analysis tools (`scripts/highsev_root_review.py`, `scripts/build_combined_trend.py`).
- **Tracker hygiene / dedup / meta-analysis sweep** — human-triggered playbook that clusters open tracker items into root-cause families, flags duplicate/related pairs, and re-verifies resolved statuses (positive evidence required to downgrade).



## [5.0.1] - 2026-05-09

### Fixed

- **`--backend rust-analyzer` no longer silently falls through to tree-sitter when the rustup proxy is broken.** Closes a v5.0.0 partial-fix gap. The defensive backstop shipped in v5.0.0 only checked the integration package; `is_rust_analyzer_available()` was existence-only via `shutil.which`. On a machine where `~/.cargo/bin/rust-analyzer` is a rustup proxy whose `rust-analyzer` component has not been installed (`rustup component add rust-analyzer` was never run, or a system-package-manager rustup install put the proxy on PATH ahead of any real install), the existence check passed, the integration check passed, and `--backend rust-analyzer run` produced byte-identical output to `--backend tree-sitter run` (same `run_signature`, same node count, same toolchain, no warning). `is_rust_analyzer_available()` now smoke-tests the binary with `<binary> --version` (5s timeout, exit-code check); a new parse-time guard `_ensure_rust_analyzer_binary_or_exit()` runs alongside the existing integration-package guard, so the `--backend rust-analyzer` path errors clearly with a pointer to `rustup component add rust-analyzer` instead of degrading silently. `add-extras --check` and `install-rust-analyzer --check` inherit the smoke test, so both report `✗ not installed` for the broken-proxy state instead of a misleading green check.

## [5.0.0] - 2026-05-09

### Summary

The Rust SCIP backend is now usable end-to-end: `pipx install 'hypergumbo[rust-analyzer]'` engages it (the integration package now ships to PyPI), and the CLI errors clearly when the integration is missing instead of silently falling through to tree-sitter. The two extras-management umbrellas collapse into one (`add-extras` / `remove-extras`) with `--check` and `--skip` flags. Correctness fixes land for Rust trait resolution (two paths), Go gRPC server-to-RPC mapping when struct names collide across files, VHDL architecture-of-entity lookups, Rails `.csv.erb` templates, Circom grammar building, and partial-install warnings that fired for inactive linkers. `hypergumbo run` no longer drops handler-slice fan-out next to the main result; it co-locates them under `<out-stem>.slices/`. `hypergumbo symbols` gains column-width controls for narrow-stdout hosts like Colab.

### Changed

- **Extras umbrella collapsed to one pair of subcommands.** `add-extras` / `remove-extras` are now the single umbrella over grammars, gitleaks, embeddings, and rust-analyzer; `install-extras` / `uninstall-extras` are removed. `add-extras` gains `--check` (status table; non-zero exit if anything is missing) and `--skip COMPONENT[,...]`; `remove-extras` gains `--skip`. The `--check` rust-analyzer row now reports `✗ not installed` when the rustup binary is present but the integration package is missing (e.g. a system-package-manager rustup install, or a residual binary after uninstalling the `[rust-analyzer]` extra), instead of a misleadingly green status. **Breaking** for anyone scripting against the old names.

### Added

- **`hypergumbo[rust-analyzer]` install extra + `hypergumbo-lang-rust-analyzer` published to PyPI.** v4.1.0 shipped without the SCIP integration package or an opt-in extra, so `--backend rust-analyzer` had no way to engage. After this release, `pipx install 'hypergumbo[rust-analyzer]'` engages the SCIP backend end-to-end (the extra is pinned in lockstep with the meta-package version, and the integration package is added to the release-workflow build loop). As a defensive backstop for minimal installs, `--backend rust-analyzer` and `install-rust-analyzer` now exit non-zero with a clear message when the integration package is missing instead of silently falling through to tree-sitter; `install-rust-analyzer --check` reports binary and integration-package status as separate lines and exits 1 if either is missing.

- **`hypergumbo symbols` column-width controls.** The Symbol and File columns now default to 60 / 80 chars — about twice what Rich auto-fit picked on narrow non-TTY hosts (e.g. Google Colab, where Rich falls back to ~80 cols and squeezes those columns to ~25–30 chars each). Two new flags: `--col-width N` sets both columns to N (clamped to `[1, 1000]`); `--wrap` switches overflow from ellipsis truncation to character-level fold-wrap. Console width auto-extends when requested widths exceed the detected terminal, so narrow hosts get a horizontally-scrolling table rather than collapsed columns.

- **Smart-test slice-fallback diagnostic file.** When `scripts/smart-test`'s reverse-slice path falls back to full-suite (`slice command failed` or `no test files in slice result`), it writes a diagnostic bundle to `.ci/smart-test-fallback.log` (gitignored, overwritten each fallback) recording the fallback reason, hypergumbo path and version, the slice command + exit code + duration, slice stdout summary (first 50 lines + line count) and stderr, and the changed-files list. A one-line pointer prints to stderr when fallback fires. Motivated by a slice → full-suite fallback that cost ~12.5 minutes and could not be reproduced afterwards.

- **UAT directed-validation playbook gains a Mechanism check.** A new optional pre-commitment field captures one or two falsification probes when a claim names a specific mechanism, plus a Mechanism column on the verdict matrix (`matches` / `mismatch` / `n/a`) and a fourth `moved + Mechanism: mismatch` verdict that strips the validation tag (the public-facing claim is satisfied) and files a `needs_human_review` follow-up to reconcile claim text against linker behavior. Surfaced by a UAT round whose quantitative verdict resolved `moved` but whose claim text described a transitive base-class walk that subsequent investigation falsified (the actual mechanism was a filename convention).

### Fixed

- **`hypergumbo build-grammars` now actually builds Circom.** The Python builder iterated `SOURCE_GRAMMARS`, which only listed Lean and Wolfram, so users hitting `"Circom analysis skipped: tree-sitter-circom grammar not available. Run \`hypergumbo build-grammars\` to build it."` would run the suggested command and see the warning persist. (The shell-script CI/dev path had been building Circom all along.) Added `tree_sitter_circom` to `SOURCE_GRAMMARS`.

- **Partial-install warnings now respect linker activation.** The warning pass iterated diagnostics from every registered linker unconditionally, so e.g. a Rust + Python repo with C/C++ symbols got `"CGO linker found 151 C/C++ implementations but 0 Go cgo calls"` even though the CGO linker (Go ↔ C/C++) would not have run on that tree. Each warning now consults its linker's `should_run(detected_frameworks, detected_languages)` predicate and skips when the linker would not have activated. The gate is bypassed when both detection sets are empty (preserves crafted-diagnostic test fixtures); the dependency linker (always-on) is unaffected.

- **`view-template-linker-v1` now recognizes `.csv.erb` templates.** The Rails template probe handled `.html.erb`, `.html.haml`, `.html.slim`, `.text.erb`, `.text.haml`, and `.json.jbuilder`, but missed `.csv.erb`. CSV-export controller actions had view files at the conventional path receive no `renders` edge. Added `.csv.erb` to the recognized template extensions and language map.

- **Rust `impl Trait for Type` requires the LHS to be a trait.** The impl_item handler accepted any symbol with the trait's name. When a project also defined a non-trait symbol with that name (e.g. a marker `struct Clone;` used as a phantom-type tag) alongside a manual `impl Clone for X` referring to `std::clone::Clone`, the lookup bound to the local struct and emitted a spurious high-confidence `X implements struct-Clone` edge. The handler now requires `kind == "trait"`; non-trait matches fall through to the unresolved-trait branch (which correctly suppresses standard-library trait names).

- **Rust `impl Trait for Type` resolves trait/struct short-name collisions across files.** The guard above only catches collisions when the global-symbol-table overwrite leaves the struct as the survivor (kind check then rejects it). When the struct wins the overwrite the canonical trait is gone from the global table entirely, so the handler falls back to an unresolved-trait edge. On a typical ML framework this misresolved ~63% of `impl Module for X` edges depending on registration order. The Rust analyzer now also populates a kind-segregated multi-value index alongside the existing single-value dict; the impl_item lookup prefers `kind == "trait"` candidates, breaks ties by same-file path then stable id, and refuses to fall back to a struct/enum when no trait exists.

- **`hypergumbo run` co-locates handler-slice fan-out under `<out-stem>.slices/`.** `--out /some/path/foo.json` previously deposited 20–30 `slice.handler.*.json` files directly in `/some/path/`, clobbering prior runs when result files shared a parent directory. They now go to a stem-derived sibling directory: `--out /some/path/foo.json` writes the main result at `/some/path/foo.json` and the slices (plus `slice.handler.index.json`) at `/some/path/foo.slices/`. `--no-handler-slices` is unchanged. When `--out` is omitted, slices land in `<cache_dir>/hypergumbo.results.slices/`.

- **`grpc-linker-v1` Go server method-to-RPC mapping is now file-scoped.** The struct-to-service map was keyed by bare struct short-name, so when multiple Go files declared a struct with the same name — e.g. eight plugin packages each declaring `type service struct { ... api.UnimplementedXxxServer ... }` for a different service — the map overwrote on registration order and whichever file iterated last won the mapping for every other plugin's `service.Create` method. On a real-world repo this misresolved seven service families' `implements_rpc` edges onto a single unrelated RPC family. The map is now keyed by `(file_path, struct_name)` (both the `Unimplemented*Server`-embedding scan and the ttrpc / CSI base-class fallback), so each plugin's methods resolve only against its own file's embedding.

- **VHDL `architecture X of Y` now kind-prefers entity over a same-named package / architecture / component.** The global registry indexed entities, architectures, packages, and components together by lowercased name, single-value, last-write-wins, so an IP-block library with both `package Foo` and `entity Foo` could mis-resolve `architecture Bar of Foo` to the package depending on insertion order. The registry is now multi-value; the lookup picks the entity candidate and falls back to a synthetic external-entity ID at confidence 0.70 when no entity matches.

## [4.1.0] - 2026-05-08

### Summary

Two more concept axes — `Symbol.kind` (192 values, ADR-0027) and `Edge.evidence_type` (218 values, ADR-0028) — instantiate the ADR-0024 axis-declaration template and migrate from Draft to Phase 4a. Producer-side folds collapse ~75 framework-dispatch evidence types to canonical inference + `meta["framework_dispatch"]`, ~28 framework-role symbol kinds to `function`/`method` + `meta["framework_role"]`, ~28 call-construct peers to apex `ast_call`, and 18 `*_unresolved` evidence types to canonical + the new sibling field `Edge.is_resolved`. Phase 4a `x-deprecated` annotations ship for both axes; closed-enum return is gated on per-cluster bakeoff validation. ADR-0027 Phase 3 producer migration is empirically complete: every `Symbol.kind` registry value carries a verdict.

Framework-dispatch and inheritance correctness fixes land across nine linker modules: six dispatch linkers and the Go ttrpc / CSI path in `grpc.py` walk transitive base-class chains; `type_hierarchy` emits skip-level overrides; Django generic CBVs resolve `View` lifecycle methods; `jackson_dispatch` recognizes JPA `@Entity` types as REST response bodies; the `inheritance` linker tightens cross-language gating. `bakeoff-deep` no longer inflates reverse-slice seeds with synthetic dispatch edges.

Internal: per-cluster verdict tables in `docs/audits/` grow to 12 entries; the Fundamental Concept Audit playbook gains an indirection-aware producer-trace step; a regression-guard property test now blocks DEPRECATE-NO-FOLD verdict drift at commit time.

### Added

#### Concept-axis declarations

- **Canonical `Symbol.kind` registry** (ADR-0027 Phase 1, `symbol_kinds.py`): 192 entries classified across `language_construct` (Cluster 27A canonicals, ~50 values), `endpoint_shape` (Clusters 27D/27E + `component_ref`, ~40 values folding to canonical + `meta["framework_role"]` or producer-side drop), and `pending_classification` (Clusters 27B/27C/27G/27H, ~100 values awaiting per-cluster audit-findings). ADR-0027 status flips Draft → Accepted.
- **Canonical `Edge.evidence_type` registry** (ADR-0028 Phase 1, `evidence_types.py`): 218 entries classified across `inference_pathway` (Cluster 28A canonicals, 107 values), `endpoint_shape` (Clusters 28B/28C/28D, 111 values folding to canonical + `Edge.is_resolved` / `meta["framework_dispatch"]` / `meta["call_construct"]`), and `pending_classification`. ADR-0028 status flips Draft → Accepted.
- **`Edge.is_resolved: bool = True` sibling field** (ADR-0028 §"Sibling-field design call-out"): captures the resolution-status property previously smuggled into `*_unresolved` evidence types. Producers set `False` when folding; `from_dict` defaults missing key to `True` for backward compatibility.
- **Pre-commit + CI drift linters for `Symbol.kind` and `Edge.evidence_type`** (`scripts/check-symbol-kind-drift`, `scripts/check-evidence-type-drift`): mirror the existing `check-edge-type-drift` shape. AST-walk `packages/`, `scripts/`, `.agent/` for module-level `*KIND*` / `*EVIDENCE_TYPE*` set assignments and verify every value is in the canonical registry.
- **L3 producer-coherence linter** (`producer_coherence.py`, `scripts/check-producer-axis-coherence`): walks `Edge.create(...)` / `Edge(...)` / `Symbol.create(...)` / `Symbol(...)` call sites and verifies literal-string keyword arguments to `evidence_type` / `kind` / `edge_type` are in the corresponding canonical registry. An assignment-form extension also traces simple assignment-form references (`name = "literal"` plus ternary / if-else) within a function — surfaced 18 latent leaks on landing. F-string emits surface as advisory Phase-3 fold candidates. Closes the producer-introduction gap left by the consumer-side drift linters.
- **`docs/concept-axes.md` extends to all three axes**: `scripts/generate-concept-axes` now reads `EDGE_TYPES`, `SYMBOL_KINDS`, and `EVIDENCE_TYPES`. CI freshness gate via `--check`.
- **`docs/schema.json` carries `x-axis-of-values` annotations on all three fields**. `Symbol.kind` and `Edge.evidence_type` ship as **open** enums (current production includes dynamic f-string emits); closed-enum return is gated on Phase 4b. `Edge.edge_type` remains closed — pre-implementation audit confirmed zero f-string emit sites.
- **`axis_drift.find_drift` accepts `excluded_target_names`**: lets callers skip target names that share the filter substring but live on a different axis (e.g. `PROTOCOL_KINDS` and `BRIDGE_KINDS` are vocabularies for `Edge.meta`, not `Symbol.kind`).

#### Audit-findings docs (per-cluster verdict tables)

The `docs/audits/` series gains 12 new entries: 8 covering `Symbol.kind` Clusters 27A–27H (~201 values, including 50 RESOLVED canonicals in 27A) and 4 covering `Edge.evidence_type` Clusters 28A–28D (~221 values, including 110 RESOLVED canonicals in 28A). Each records per-row CANONICAL / FOLD / DEPRECATE-NO-FOLD verdicts and UNRESOLVED / PRELIM_RESOLVED / RESOLVED statuses.

- **Audit-findings format extended to all three axes** (`audit_findings.py`): `_REGISTRIES` carries an `_AxisRegistry` per axis, parameterising mechanical-check predicates over per-axis `canonical_axis` and `endpoint_axis`. The format previously hard-coded `relationship` as the canonical axis.

#### Audit / regression-guard infrastructure

- **DEPRECATE-NO-FOLD-zero-producer regression guard** (strict, CI-blocking): `audit_findings.find_zero_producer_violations()` enumerates DEPRECATE-NO-FOLD verdicts across the three registered axes and asserts no producer emits the value, with companion enumerators `producer_coherence.find_emitted_{symbol_kinds,evidence_types,edge_types}()`. Catches literal-kwarg and assignment-form-to-Name leaks at every commit; helper-call / f-string / dict-subscript shapes remain manual.
- **README index sync regression guard**: `audit_findings.find_readme_index_drift()` parses `docs/audits/README.md` and asserts the Status column agrees with each doc's verdict YAML row counts. Supports both explicit-count cells (`Mixed (6 RESOLVED, 11 PRELIM_RESOLVED)`) and bare-marker cells (`All RESOLVED`).

#### Methodology hardening

- **Fundamental Concept Audit playbook gains §"Step 4.5 — Indirection-aware producer trace"**: before claiming "no producer", auditors must check five producer-emit shapes (literal kwarg, helper-call positional/kwarg, assignment-form-to-Name, f-string interpolation, dict-subscript-target). A self-test bullet makes the trace mandatory at audit-write time. Motivated by three DEPRECATE-NO-FOLD → CANONICAL reclassifications (`theorem` / `inductive` / `message`) that a literal-grep had missed via `add_symbol(...)` / `_make_proto_symbol(...)` indirection.

#### Hooks & developer experience

- **Session-start hook prompts about prior-session `agent_notes.json`**: a non-empty notes file produces a one-line prompt naming both the notes-file age and the last-session age, asking the agent to ask the user whether to load the handoff via `./scripts/agent-notes --show`. Notes content is not dumped unprompted. When the audit-cadence prompt also fires, the two are marked as separate items.
- **Hook transcript dedup window bumped 100k → 200k tokens**: covers longer reflection sessions before the dedup-suppression heuristic engages.

### Changed

#### Concept-axis migrations (ADR-0027 / ADR-0028 Phase 3)

- **Phase 3 — eight families fold to canonical + meta across the two new axes**:
    - **`Edge.evidence_type` `*_unresolved`** (18 emit sites, 11 producer files): fold to `evidence_type=<canonical>` + new sibling field `Edge.is_resolved=False`. Two new Cluster 28A canonicals (`grpc_stub_resolution`, `luajit_ffi_lookup`) absorb sites without a prior canonical inference label. Audit-findings 0008.
    - **`Edge.evidence_type` framework-dispatch** (~75 values): fold to canonical inference (`ast_call_direct` / `ast_decorator` / `ast_import` / `naming_convention`) + `meta["framework_dispatch"]` or `meta["detection_pattern"]`. Coverage spans websocket / tauri / grpc / openapi / graphql / http / crypto / ipc / objc / event-dispatch / Go route_mount / Ruby / Django / NestJS / Vue and ~25 single-row dispatch modules. Audit-findings 0014.
    - **`Edge.evidence_type` call-construct peers** (28 values, 89 emit sites, 26 producer files): fold to apex `ast_call` + `meta["call_construct"]`. The lone non-`ast_call` apex (`cross_file_message_send`) folds to `message_send` + `meta["call_construct"]="cross_file"`. Audit-findings 0012.
    - **`Symbol.kind` framework-role** (~28 values): fold to `function` / `method` / `interface` / `class` / `reference` + `meta["framework_role"]`. Highest-blast-radius slice is `Symbol.kind="route"` (17 source files, 14 production consumers, ~330 sites). New `Pattern.framework_role` field with its own compiled-regex matcher; four YAML rules (`laravel`, `phoenix`, `rails`, `sinatra`) migrate from `symbol_kind: "^route$"`. The remaining `symbol_kind:` regex rules across `phoenix.yaml`, `falcon.yaml`, `yesod.yaml`, `library-exports.yaml`, etc. continue to match post-fold symbols via a `Pattern.matches()` fallback to `meta["framework_role"]` when the `symbol_kind` regex doesn't match the (now-canonical) `symbol.kind`. The fallback is backward-compat technical debt; the structural fix (migrate the remaining YAMLs to `framework_role:` and remove the shim) is tracked separately. Audit-findings 0013.
    - **`Symbol.kind` Cluster 27E edge-label kinds** (12 values): new canonical `call_site` absorbs subprocess / db_query / abi / twig `function_call` (→ `kind="call_site"` + `meta["call_kind"]`); other values drop the per-reference Symbol because the relationship is already on a companion Edge — 3 clean drops, 6 edge-endpoint redesigns, 4 companion-Edge introductions. Audit-findings 0010.
    - **`Symbol.kind` Cluster 27F component_ref**: vue / svelte / astro drop per-reference Symbols; `imports` edges re-route src to `make_file_id()` and fall back to a 5-part dangling component id when unresolved. DEPRECATE-NO-FOLD verdict (the original fold target `reference` was already deprecated in 0010). Audit-findings 0011.
    - **`Symbol.kind` Clusters 27B / 27G / 27H sweep**: 74 canonical promotions (registry-only); 15 FOLDs with producer migration (e.g. `module_file` → `file` + `module_system`, `npm_package` / `composer_package` → `package` + `package_ecosystem`, `test_case` → `test` + `test_dialect`, `editable` / `url_requirement` → `requirement` + `install_mode` / `install_source`, `devDependency` → `dependency` + `dependency_scope`, `python_task` → `task` + `task_implementation`); 8 DEPRECATE-NO-FOLDs (`tsconfig` subsumed by v4.0.0's `is_config_file`; `config` producer-rewritten by `prisma.py` to `kind="block"` + `meta["block_type"]`; the rest dead vocabulary or registry seed errors); 4 CANONICAL reclassifications (`theorem` / `inductive` / `message` / `external_symbol`). Consumer dual-shape predicates added at `route_handler.is_component` and `cli._is_noise`. Audit-findings 0005 / 0006 / 0007.
    - **`Symbol.kind` Cluster 27C apex/peer**: registry classification updates only (`fn` / `var` / `proc` / `structure`); no producer change. Audit-findings 0009.
- **Phase 2 consumer migration**: dual-shape predicates at `linkers/registry._is_synthetic_node`, `selection.filters.is_excluded_kind`, `route_handler.is_component`, and `cli._is_noise` recognise pre- and post-fold producer shapes so consumer filters survive the producer fold without inflating selection or compact output.
- **Phase 4a `x-deprecated` annotations**: `scripts/generate-schema` emits `x-deprecated` on `#/$defs/Symbol/properties/kind` (50 entries) and `#/$defs/Edge/properties/meta/properties/evidence_type` (111 entries), mirroring the existing `Edge.type` Phase 4a shape. Values stay valid in the open schema for the deprecation window. Phase 4b ships piecewise as each cluster's `awaits_bakeoff_validation` tag clears.
- **Verdict-correctness re-audit**: the Step 4.5 indirection-aware producer trace ran against all 19 DEPRECATE-NO-FOLD values; 2 reclassified to CANONICAL (`reference` from `swift_objc.py`, `import` from `wasm_bindgen.py`), 17 verified clean.

#### Schema versions

- **SCHEMA `0.4.0` → `0.5.8`**: additive only. The `Edge.evidence_type` enum re-opens at ADR-0028 Phase 1 land; subsequent patch bumps absorb per-Wave producer migrations. No validation that previously passed will now fail.

#### Inheritance linker

- **Inheritance linker annotates simple-name fallback edges**: when `_resolve_target_symbol` falls back to deterministic-by-sorted-ID disambiguation (multiple cross-file candidates, no same-file precision match), the resulting `extends` / `implements` edge now carries `confidence=0.5` and `meta["disambiguation_fallback"]=True`. Single-candidate and same-file resolutions remain at `confidence=0.95` with no flag. Lets downstream consumers (slice ranking, dead-code analysis, supply-chain tier classification) filter the fallback population.

#### Bakeoff infrastructure

- **`bakeoff-deep` excludes `dispatches_to` from `pick_reverse_slice_seeds` out-degree counting**: synthetic dispatch edges from interface stubs were inflating reverse-slice seed scores above real domain functions. 16 of 18 `dispatches_to` producers emit synthetic 'menu' relationships; the 2 real-dispatch producers (route_handler, grpc) score via the route and API-handler boosts already.

### Fixed

#### Framework-dispatch correctness

- **Six dispatch linkers and the Go ttrpc / CSI path in `grpc.py` walk transitive base-class chains**: `airflow_framework_dispatch`, `django_orm_dispatch`, `jackson_dispatch`, `kafka_streams_dispatch`, `view_template`, and `react_component` now BFS over `extends` / `implements` edges to discover ancestors whose `meta.base_classes` names a framework base. Fixes the dominant real-world pattern where projects extend an in-tree intermediate rather than the framework class directly (e.g. `AlloyDBWriteBaseOperator(BaseOperator)`, JPA `@Entity` extending `@MappedSuperclass`, Kafka Streams SAM wrappers, `LeafController(ApplicationController)`, project-internal React base components, ttrpc `UserHealth` → `BaseHealthImpl` → `HealthService`). New shared helper `linkers/_transitive_bases.py` (cycle-guarded BFS) is the single source of truth; `collect_transitive_base_names` accepts a `meta_keys` tuple so `kafka_streams` can fold `extends` and `implements` together. Real-world testing on airflow and pretix had previously seen 0/9 and 0/6 transitive cases. The `react_component` change also implements the base-class branch its docstring claimed (the code matched only on PascalCase).
- **Django generic CBVs inherit View lifecycle methods**: `django_orm_dispatch.DJANGO_BASE_METHODS` entries for `ListView` / `DetailView` / `CreateView` / `UpdateView` / `DeleteView` / `TemplateView` now fold in `dispatch`, `setup`, `http_method_not_allowed`, `options`, the seven HTTP verbs, `head`, and `trace`. Django's class hierarchy is external, so the transitive base-class walk above had no in-tree edge — a project class `Foo(ListView)` previously matched only `ListView`'s frozenset and never reached `View`. Pretix had zero `dispatches_to` edges to any `*.dispatch` method graph-wide. New module-level `_VIEW_LIFECYCLE` constant is the single source of truth.
- **`type_hierarchy` linker emits skip-level overrides**: the parent→children map is now closed transitively before edge emission. When `Grandparent.foo` is overridden only in `Grandchild` (intermediate `Parent` doesn't override), the edge `Grandparent.foo → Grandchild.foo` is now emitted; previously `Grandchild` was missing from `parent_to_children[Grandparent]` because the map was one-hop. New `close_parent_to_children_transitively` BFS helper preserves diamond-no-double-emit and direct-override semantics.
- **Jackson dispatch linker recognizes JPA `@Entity` / `@MappedSuperclass` / `@Embeddable`**: the prior matcher triggered only on Jackson, JAX-B, and Spring-binding annotations, missing the Spring Data JPA + Spring MVC pattern that Jackson-serializes JPA-mapped types as REST response bodies. On spring-petclinic, 6 `@Entity` classes (Owner, Pet, Visit, Vet, Specialty, PetType) had produced zero edges; bean-convention accessors now receive `dispatches_to` edges as expected.

#### Cross-language hygiene

- **Inheritance linker enforces cross-language gating + Rust kind discipline**: drops candidates whose `language` differs from the child symbol's before resolution — eliminates 31 bogus Python→Rust-trait edges in candle (e.g. `class FooModule(nn.Module)` no longer matches a Rust `Module` trait); refuses struct/enum candidates when the child is a Rust struct/enum (Rust permits no struct→struct inheritance). Bridge linkers (PyO3, cffi, wasm_bindgen, jni) remain the sanctioned path for genuine cross-language conformance edges.

## [4.0.0] - 2026-05-03

### Summary

**Breaking: 33 deprecated `edge_type` values are removed from the canonical registry** (`SCHEMA_VERSION` 0.3.1 → **0.4.0**). The cohort spans the bridge/FFI, IPC, dispatch/publish, and dst-kind families (e.g. `cgo_bridge`, `ipc_calls`, `routes_to`, `imports_module`); each was folded into a canonical relationship + `meta` key in earlier phases. Downstream consumers: see [`docs/migrating-edge-types.md`](docs/migrating-edge-types.md). The pre-commit drift gate is now `--strict`, so future endpoint_shape regressions fail at commit time.

Two new `Symbol` booleans — `is_example_file` and `is_config_file` — round out the file-role flags. Starlette routes are now detected.

Internal: the audit methodology behind ADR-0023 generalises into ADR-0024 (axis declaration template) and a new `docs/audits/` per-value verdict series; Draft ADRs 0027 / 0028 instantiate the template for `Symbol.kind` and `Edge.evidence_type`.

### Added

#### Concept-axis declarations

- **ADR-0024 — Axis Declaration Template for Multi-Value Fields**: formalises the four-part template (axis name, axiom, consumer pattern, enforcement), the seven-step declaration workflow that ADR-0023 demonstrated concretely, the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy (§"Family-audit verdict methodology"), and the fold-residue discipline (rules for promoting recurring meta keys to dedicated fields). ADR-0023 is reframed as the worked example; future axis-shaped fields instantiate this template. AGENTS.md adds an "Axis declaration for multi-value fields" essentialization in Required Checks.
- **ADR-0027 & ADR-0028 (Drafts) — two more axes instantiate the template**: ADR-0027 names `Symbol.kind` as the source-language syntactic construct (192 values / 8 clusters; framework-participation folds to canonical + `meta["framework_role"]`). ADR-0028 names `Edge.evidence_type` as the inference pathway (210 values / 4 clusters; resolution status promotes to a sibling `Edge.is_resolved: bool`). ADR-0028 is the largest concept-axis migration on the roadmap (~140 production files have `evidence_type` literals).
- **`docs/audits/` document series**: sibling to `docs/adr/` for per-value verdict tables. Format spec at `docs/audits/README.md`; verdict rows carry `value` / `verdict` (CANONICAL | FOLD | DEPRECATE-NO-FOLD) / `fold_target`. Pre-commit gate at `scripts/check-audit-findings`.
- **Fundamental Concept Audit playbook + diagnostic catalog** (`docs/blind-spots.md`): domain-neutral procedure for detecting conceptual leaks via four leakage tests, plus a complementary catalog of four recurring question-shapes (typing axis vs values, assumed input boundaries, silently-load-bearing failure modes, null results read as confirmation). Cadence hook (`.agent/hooks/_shared/check_audit_cadence.py`) prints a soft reminder once 72+ dev commits pass without an audit. Wired into all four supported vendor session-start hooks and the agentic-session-retrospective.

#### Edge-type registry & tooling

- **Canonical edge-type registry** (`hypergumbo_core/edge_types.py`): single source of truth for `Edge.edge_type` values, each annotated with an axis classification (`relationship`, `endpoint_shape`, or `pending_classification`). `scripts/generate-schema` consumes the registry and emits an `x-axis-of-values` JSON Schema extension on `Edge.type`. A property test AST-walks the package source and fails CI if any module-level `*EDGE_TYPE*` set contains an unregistered value. Inaugural population (built up across the cycle through completeness sweeps and ADR-0023-reconciliation fixes) includes 7 newly-named relationship canonicals (`inherits`, `decorated_by`, `includes`, `defines_target`, `data_flows_to`, `module_exports`, `overrides`), 13 already-emitted values that the schema enum had been missing, 18 endpoint_shape candidates seeded for future per-pattern audits, and the four edge types (`imports_component`, `model_reference`, `type_ref`, `renders_component`) named in ADR-0023's deprecation list but previously absent.
- **Human-readable by-axis view** at `docs/concept-axes.md`, regenerated by `scripts/generate-concept-axes` with a pre-commit freshness check.
- **Pre-commit edge-type drift linter** (`scripts/check-edge-type-drift`): catches consumer-side hardcoded `*EDGE_TYPE*` sets that drift from the canonical registry. Runs in `--strict` mode by default — future endpoint_shape regressions fail at commit time. Implementation is field-agnostic (`hypergumbo_core.axis_drift.find_drift(...)`, search scope `packages/` + `scripts/` + `.agent/`) so future axis-bearing fields inherit the pattern per ADR-0024. Surfaced and fixed one phantom-value bug along the way (`bakeoff-deep::_FFI_EDGE_TYPES` referenced `jni_bridge` / `pyffi_bridge`, neither ever emitted).
- **Runtime coherence checker** (`scripts/check-edge-type-runtime-coherence`): the runtime half of ADR-0023's two-layer enforcement — partitions emitted edges by `(src.kind, src.language, dst.kind, dst.language)` and reports partitions where `edge_type` varies. Allow-list at `docs/edge-type-runtime-allowlist.yaml`.
- **`docs/migrating-edge-types.md` — downstream consumer migration guide**: rename table, meta-key vocabulary (`bridge_kind`, `channel_kind`, `mechanism`, `construct`, `dispatch_kind`, `protocol`), worked patterns, and the post-Phase-4b deprecation timeline. "What's NOT migrated yet" is grouped by next-ship: pending-classification (4), protocol-call family (3), long-tail sweep (22).

#### IR additions

- **`is_example_file` and `is_config_file` Symbol booleans**: surface two role flags mirroring `is_test_file` and `is_generated_file`. `is_example_file` fires on `examples/` / `demos/` / `samples/` / `tutorials/`; `is_config_file` fires on dependency/build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.). Within tier 2 the four role flags are mutually exclusive — `is_config` is suppressed when `is_test` or `is_example` already fires. Round-trips through `Symbol.to_dict` / `from_dict`.

#### Frameworks

- **Starlette route extraction**: `Route("/path", handler, methods=[...])` and `WebSocketRoute("/ws", handler)` constructor calls from `starlette.routing` are detected and emitted as `kind="route"` symbols. Matching is import-scoped to avoid false positives from local `Route` classes; handles aliased imports. New `frameworks/starlette.yaml` attaches `concept=route` to handler functions.
- **`hypergumbo routes` empty-result hint**: when no HTTP routes are found, the command now reports related endpoint-shaped node counts (`websocket_endpoint`, `graphql_resolver`, `db_query`, `event_publisher`, `mq_publisher`, `http_client`, `subprocess_call`, …) and points at `hypergumbo run` JSON output and `hypergumbo explain <name>` for inspection.

### Changed

#### Edge-type axis migration (ADR-0023)

- **Phase 4b — 33 deprecated edge_types removed from the registry** (`SCHEMA_VERSION` 0.3.1 → **0.4.0**): the bakeoff-validated cohort across the dst-kind (6), bridge (7), IPC (7), and dispatch/publish (13) families is removed from `EDGE_TYPES`. The 25 sweep additions (protocol-call + long-tail) stay until their producers migrate. Consumer enumerations (`IMPORT_EDGE_TYPES`, `compact.CROSS_CUTTING_EDGE_TYPES`, `ranking.DEFAULT_EDGE_TYPE_WEIGHTS`, `io_boundary._TRACEABLE_EDGE_TYPES`, `taint.TAINT_CALL_EDGE_TYPES`, `cli._REACHABILITY_EDGE_TYPES`, `bakeoff-deep::_CALL_FLOW_EDGE_TYPES`) cleaned up; test fixtures rewritten to canonical shape.
- **Phase 3 — five `endpoint_shape` families folded to canonical + meta** (producers landed earlier this cycle):
    - **bridge/FFI**: `cgo_bridge` / `ffi_bridge` / `napi_bridge` / `wasm_bridge` / `native_bridge` / `bridge_invokes` → `calls + meta["bridge_kind"]`; `wasm_load` → `imports`.
    - **IPC** (Tauri, Electron, Phoenix Channels, WebSocket, message queues): `ipc_calls` → `calls + meta["protocol"]="ipc"`; event variants → `event_publishes + meta["channel_kind"]`; `websocket_connection` → `references + meta["construct"]="websocket_endpoint"`.
    - **publish/dispatch**: `routes_to` → `dispatches_to + meta["dispatch_kind"]="route"` (17 emit sites in 12 linkers, per audit-findings 0001).
    - **protocol-call** (HTTP / gRPC / GraphQL): → `calls + meta["protocol"]`; new `PROTOCOL_KINDS` constant.
    - **dst-kind leakage**: `imports_module` / `imports_component` → `imports`; `model_reference` / `type_ref` / `query_references` → `references`; `renders_component` → `references + meta["construct"]="jsx"`.
    - **DEPRECATE-NO-FOLD drops**: `message_receive` (forward `event_publishes` already captures the relationship) and `event_subscribes` from `event_sourcing.py`.
- **Earlier phases also landed this cycle**:
    - **Phase 4a** — `Edge.type` gained an `x-deprecated` JSON Schema extension listing every endpoint_shape value as a removal candidate.
    - **Phase 2** — new `IMPORT_EDGE_TYPES` predicate replaces hardcoded `{"imports", "imports_module"}` sets at `ranking.py` and `slice.py`; adding the missing `imports_component` entry closes the silent miscategorization of Vue/Svelte/Astro/React component imports (ADR-0023 §1 case 1).
- **ADR-0023 promoted Draft → Accepted**: ADR text now cites landed Phase 1 commits, cross-references ADR-0024, resolves prior Open Questions inline, and reframes the Property test section as three complementary defenses (static / runtime / cadence-hook). §6 plan reshaped from single-event to sequential micro-ships, one family at a time.

#### Audit-findings reclassification & follow-ups

- **ADR-0025 / ADR-0026 reclassified as `docs/audits/0001-dispatch-publish-family.md` / `0002-ipc-family.md`**: both were per-value verdict tables, not architecture decisions. Permanent redirect stubs preserve URL discoverability.
- **`docs/adr/README.md` — bucket rubric** for ADRs vs audit-findings vs surveys, organised around "decision present?". `docs/surveys/` is forward-declared. AGENTS.md "Required Checks" gains a one-paragraph essentialization.
- **Three IR field docstrings clarified** per the 2026-04-30 Adjacent Concept Sweep: `Symbol.origin`, `DataModelKind`, `UsageContext.kind`. Each surfaces the conflated axes and per-field re-evaluation triggers. No behavior change.

#### Governance & playbooks

- **AGENTS.md streamlined**: weasel-word bullets merged; CI Interaction Policy compressed (auto-pr exit-code recovery table moved to `ci-debug-protocol.md`); Bakeoff Validation Discipline split by cadence into per-PR (`bakeoff-validation-tagging-discipline.md`) and per-session-drain (`process-validation-queue-with-bakeoffs-and-uat.md`) playbooks.
- **Cruft-audit playbook introduced**: codifies the two-pass methodology (syntactic grep + semantic read, mediated by interactive interview) and the cruft / trim / not-cruft / doc-consistency taxonomy. First application removed the TRACKER_SYNC_PENDING workaround and stale temporal qualifiers across four playbooks.

#### CI

- **Nightly schedule shifted from 23:00 UTC → 05:30 UTC** (`.github/workflows/nightly.yml`): reduces overlap with daytime work. Doc references in CI-debug protocol and release SOP updated.

### Performance

- **Cross-linker tree-sitter parse cache**: linkers running on the same file now share a single parse via `LinkerContext.parsed_trees` keyed by `(path, language)`. Eliminates ~18,000 redundant parses per `hypergumbo run` on a 750-Python-file repo. Bound through a `contextvars.ContextVar`, so the existing 23 linker call sites need no changes.

### Fixed

- **Linker docstring/comment false positives**: 23 protocol/framework linkers ran their regex pattern detectors directly against raw file bytes, matching their own module docstrings that documented the very patterns they detect. New shared masker `linkers/_text_filters.mask_doc_regions` parses with tree-sitter and replaces comment ranges and Python module-level docstrings with spaces (newlines preserved) before regex matching. On hypergumbo self-analysis, removed 45 false-positive nodes across nine kinds (`event_publisher` -15, `mq_subscriber` -6, `mq_publisher` -5, `event_subscriber` -4, `subprocess_call` -4, `db_query` -4, `http_client` -3, `websocket_endpoint` -2, `graphql_resolver` -2). `annotation_convention` is intentionally exempt because it scans `@hg:` directives inside comments.
- **Stale references in cross-cutting / taint edge-type sets** (surfaced by the new drift property test): `taint.TAINT_CALL_EDGE_TYPES` no longer includes `unresolved_external_call` (an `evidence_type`, not an `edge_type`); `compact.CROSS_CUTTING_EDGE_TYPES` no longer includes `ffi_calls` (the name of a Python local variable inside the FFI linkers, never an emitted edge type). Pure dead-code cleanup.

## [3.0.0] - 2026-04-29

### Summary

**Breaking: `hypergumbo io-boundaries` output has changed.** The I/O catalog now holds only true stdlib primitives; wrapper chains that previously counted under `net_send` / `fs_read` / `fs_write` / `db_*` / `logging` surface through a new `external_potential` boundary, paired with a per-language `status: complete | in_progress` declaration. `--json` gains `boundaries.external_potential` and `dst_classification_unreliable` per chain; schema 0.2.2 → 0.2.4 (additive). See [`docs/MIGRATION-IO-BOUNDARIES.md`](docs/MIGRATION-IO-BOUNDARIES.md) for the migration guide.

### Added

#### IO boundaries

- **`external_potential` bucket**: every edge whose destination is a synthetic tier-3 boundary node now produces a chain carrying `dst_tier`, `dst_tier_name`, and `dst_external_boundary`. Chains from an `in_progress` source language carry `dst_classification_unreliable=True` (text output annotates `[unreliable]`). Replaces the wrapper-catalog-growth treadmill with a first-class "untrusted-territory reach" signal.
- **Per-language catalog `status` and `stdlib_provenance`**: catalogs declare `status: complete | in_progress` (default `complete`) plus a `stdlib_provenance.source_url`. Python is `complete` (3.13 stdlib, cross-checked against `sys.stdlib_module_names`); the other 12 are `in_progress`. Catalogs may also declare `stdlib_other:` for stdlib non-IO symbols that `external_potential` skips. Off-allowlist provenance hostnames are rejected at load time.
- **Attribute-style IO primitives across seven languages**: a new `module_attr_ref` edge type lights up previously-inert YAML catalog entries. Wired in Python (`os.environ`, env_read chain count 3 → 39), Go (`os.Stdout`), JS/TS (`process.env`, `window.*`, `document.*`, `navigator.*`), Java (`System.out`, imported class fields), Rust (`std::env::consts::OS`), C (non-shadowed `stdout` / `stderr` / `stdin`), and C++ (`std::cout` / `std::cerr` / `std::cin`, including aliased namespace use like `namespace fs = std::filesystem;`). Bare `cout` after `using namespace std;` remains out of scope.
- **Python stdio reclassified from `ipc_send` to `logging`**: `sys.stdout` / `sys.stderr` move to a new `logging` block, matching the fix Go's `log` / `log/slog` / `fmt` already received. Eliminated 70 of 77 `ipc_send` false-positives on self-analysis. `sys.stdin` stays in `ipc_recv` — untrusted piped input is a real IPC concern.
- **Python `pyproject.toml` dependency manifest, monorepo-aware**: a new parser walks `repo_root/pyproject.toml` and every `packages/<pkg>/pyproject.toml`, parsing `[project].dependencies`, `[project.optional-dependencies]`, and `[tool.poetry.dependencies]`. Dist-name → import-name resolution (`PyYAML` → `yaml`, `scikit-learn` → `sklearn`) via `importlib.metadata.packages_distributions()`. Wired into the Python analyzer; the manifest-aware allow-list now extends to Python so declared deps classify tier-2 instead of tier-3. On hypergumbo self-analysis, `tree_sitter` / `rich` / `pygments` / `yaml` / `sentence_transformers` / `pytest` / `jsonschema` / `requests` all flip from tier-3 to tier-2.

#### verify-claims

- **Project-local taint catalogs**: repeatable `--taint-sources PATH` / `--taint-sinks PATH` / `--taint-sanitizers PATH` flags accept YAML files or directories (globbed as `*.yaml`). Same paths can live under `extra_catalogs:` in the claims YAML. User entries matching `(module, name, kind)` replace auto-derived/built-in entries; user sanitizers concatenate.

#### Framework detection

- **Name-form normalization at matcher boundaries**: a new `NameMatcher` utility with canonical (alphanumeric + dotted-segment suffix match) and regex (terminal-segment fallback) modes lets a YAML pattern like `^BaseModel$` match `pydantic.BaseModel` from `import pydantic` + `class Foo(pydantic.BaseModel)`; same for annotations (`^RestController$` matches `org.springframework.web.bind.annotation.RestController`) and parameter types (`^Depends$` matches `fastapi.Depends`). Decorator matching stays on raw `re.compile` to avoid double-firing dispatch-set triplets like `^task$` paired with `^(celery|app)\.task$`; a regression test guards the exception. A matcher-boundary discipline lint AST-walks `Pattern.__post_init__` so future analyzers don't regress.

#### Transcript playbook injection

- **Injection output reads as a reference document, not a list of bare ids**: header is now `[Transcript Analysis — N relevant document(s)]`; each block opens with `--- <natural title> — <repo-relative path> ---` (title parsed from the first H1/H2/H3 heading); SPDX comments stripped; a one-line framing hint follows. Empirical motivation: an overlap sweep found 5 of 11 Read calls on playbook files happened *after* the pipeline had already injected the same playbook.

#### Language support

- **Jsonnet registered in `taxonomy.LANGUAGES`** under `FileRole.CONFIG` with `.jsonnet` / `.libsonnet` extensions. The existing jsonnet analyzer was already emitting jsonnet-prefixed dst ids without a registry entry, causing the strict boundary-node validator to misflag synthesized nodes (50 on alertmanager, 63 on prometheus — Grafonnet/Tanka).

#### auto-pr

- **Orphan tracker-sync PR detection**: `auto-pr`'s post-success path warns about open tracker-sync PRs whose `created_at` predates the current run (motivating incident: a sync PR sat orphaned ~3 hours). Mid-cycle sync PRs are intentionally ignored. Warning is best-effort and never affects exit code.
- **Sync-gate inspection helper**: a shared bash helper uses `flock --shared --nonblock` to inspect the tracker sync gate without disturbing the holder, auto-cleans stale lock files, and renders a friendly diagnostic. Wired into queue flush, PR preflight, and the post-merge re-check.

#### Bakeoff infrastructure

- **`--pool` recurses into collection directories**: `bakeoff-broad`, `bakeoff-deep`, and `dead-code-prospector-run.py` share a new `pool_utils` (bounded depth-2 walk + realpath-based dedup), so a catalog whose entries are themselves repo-collections — some flat symlinks, some `cohort_*/repo` real subdirs — works as `--pool` directly. Previously required a flat list of repos. Default `--pool` for `dead-code-prospector-run.py` updated to `~/ALL_REPOS`.

### Changed

- **External boundary nodes survive serialization, carry stable IDs, and stay out of top-N rankings**: synthetic boundary `Symbol`s now serialize into `behavior_map["nodes"]` with `kind="external_symbol"`, `path="<external>"`, `meta.external_boundary=True`, and `supply_chain.tier` populated (3 for most externals; 2 for Go/Java/Kotlin/Python direct deps via `DependencyManifest`). Their `stable_id` and `canonical_name` derive from a sha256 of `(language, path, name, kind)` instead of being null. `kind="file"` pseudo-IDs from `make_file_id`-style import-edge srcs collapse per-language into one canonical `<external>` Symbol; per-file attribution survives via `meta.referring_paths` (capped at 50). The orchestrator synthesises real `kind="file"` Symbols for any remaining dangling endpoints, and a new ranking dampener zeros their centrality so they stay out of top-N. Display surfaces filter via `ir.is_external_boundary()`; `cmd_explain` intentionally surfaces boundary symbols. Schema 0.2.2 → 0.2.4 (additive): `external_symbol` and `file` added to `Symbol.kind`; `Span.start_line` / `end_line` minimum loosened from 1 to 0 for zero-span nodes. On hypergumbo self-analysis: ~37% drop in external-symbol count (2,405 → 1,514), zero null `stable_id`/`canonical_name`, imports edge count rises 2,136 → 9,252.
- **Centrality and dampener pipeline aligned across all selection surfaces**: a new `compute_dampened_centrality` helper is the single source of truth for the "compute_centrality + 8-stage dampener stack" pipeline. Sketch, `select_by_coverage`, `select_by_connectivity`, and `format_tiered_behavior_map` previously called `compute_centrality` with bare defaults and ran 0–3 of the 8 dampeners; they now match `rank_symbols`'s tuned values (`hub_threshold=100`, `within_file_weight=0.3`, `max_per_file_in=5`, `edge_type_weights=DEFAULT_EDGE_TYPE_WEIGHTS`) and the full `tier → noise → utility → common_method → sibling_impl → trivial_sink → generated → file_kind` stack. A 6-repo audit (alertmanager, prometheus, kserve, chatwoot, detekt, django) showed 7–71 of top-100 churn per surface, driven mostly by external symbols and OpenAPI-generated model classes leaking into seed picks. Tagged `awaits_bakeoff_validation`.
- **Taint catalog auto-derivation**: sources/sinks auto-derive from `io_primitives/*.yaml`. Defaults: writes `fs_write`→`host_fs`, `net_send`→`network`, `subprocess`→`host_fs`, `env_write`→`host_env`, `ipc_send`→`ipc`, `browser_storage_write`→`browser_storage`; reads `env_read`→`host_secret`, `net_recv` / `ipc_recv`→`untrusted_input`. Hand-written YAML overrides on `(module, name, kind)`. 419 previously-uncovered primitives now flow through `verify-claims`. Shipped `taint_sinks/host_filesystem.yaml` and `taint_sinks/network_send.yaml` removed (they duplicated io_primitives). New zones `host_env` / `ipc` / `browser_storage` and labels `host_secret` / `untrusted_input`. `module_attr_ref` joins `TAINT_CALL_EDGE_TYPES`.
- **Browser-local storage split out of filesystem categories**: new `browser_storage_write` primitive (`localStorage.setItem` / `sessionStorage.setItem` / `.clear` / `.removeItem`, moved from `javascript.yaml#fs_write`) and new `browser_storage_read` category (`localStorage.getItem` / `sessionStorage.getItem` / `indexedDB.open` / `caches.{open,match,has,keys}`, moved from `fs_read`). Auto-import routes writes to the `browser_storage` zone; reads stay project-local since sensitivity depends on stored content. `document.cookie` stays under `env_read` pending a getter/setter split.
- **Full-suite CI cadence switched to twice-daily** (01:00 / 13:00 UTC) from every-4-hours. Singleton concurrency unchanged.

### Fixed

- **Boundary node ID well-formedness across producers**: a single invariant — boundary-node IDs must be the 5-part `{lang}:{path}:{span}:{name}:{kind}` shape — was being violated by six producers, each leaking raw paths or unresolved markers into the language slot of synthesized boundary nodes. Fixed: Markdown link extraction (URLs landing in language slot, 9 nodes); Vue `imports_component` (raw paths, 871 nodes on chatwoot); `manifest_targets` gradle/csproj `defines_target` (Java path strings, 34 nodes on kafka); bash `sources` edges (8 nodes); TOML `[project.scripts]` `defines_target` (`hypergumbo_core/cli.py` as language); five extended1 analyzers — luau / smithy / hack / jsonnet / apex — emitting 2-part `unresolved:{name}` dsts (39 jsonnet nodes on alertmanager). Each producer now emits a properly-formed 5-part id and stashes the raw path on `edge.meta.target_path`; the `build_target.py` linker reads from meta with a dst fallback. Six remaining extended1 analyzers (robot / racket / purescript / scheme / matlab / prisma) still emit a 3-part shape with malformed name/kind slots — deferred follow-up.
- **Tier 1 dataflow: trailing comments shadowing real-code nodes**: when a real-code node and a trailing comment both started on the same line — `v := compute(x)  // godoc` — the comment overwrote the real-code entry in the line→deepest-node index, leaving the call edge unannotated. Fix is a one-line predicate that skips nodes whose `type` contains `"comment"` (covers `comment` / `line_comment` / `block_comment` / `doc_comment` across every tree-sitter grammar). Empirical Go fixture: calls-edge `access_mode` coverage 43% → 71%. Benefits every tree-sitter language analyzer (Go, Kotlin, Rust, TypeScript, Erlang, Java). Tagged `awaits_bakeoff_validation`.
- **`pyproject.toml` malformed-TOML handling on Python 3.10**: `_load_pyproject` (and the parallel block in the `subprocess_cli` linker) wrapped a tomli fallback parse in `try/except ImportError`, so on 3.10 a `tomli.TOMLDecodeError` (a `ValueError` subclass) escaped the inner handler and was never reached by the outer `(ValueError, OSError)` handler — Python doesn't fall through to sibling except clauses after one fires. Refactored to resolve the loader once, then parse under a single decode-error handler. Surfaced as a 3.10 nightly test failure; 3.11+ was unaffected because tomllib's decode error reached the outer handler directly.
- **Pre-push hook no longer blocks `ci-failover disengage`'s repatriation push**: the failover guard was over-broad — it blocked every push to origin while failover was active, including the AGit feature-branch push (`refs/for/dev/<branch>`) the disengage script uses to open the Codeberg repatriation PR. Disengage was effectively bricked from the moment the guard landed. The hook now honors a `CI_FAILOVER_DISENGAGING=1` env var that the disengage script sets for that one push; direct pushes to protected branches on origin remain blocked.
- **Pre-commit bakeoff-running guard no longer false-positives on argv mentioning `bakeoff`**: the prior `pgrep -f '[s]cripts/bakeoff'` matched any process whose cmdline contained the substring — including `git add scripts/bakeoff-broad …` and heredoc commit messages naming the script. Now `pgrep` is the candidate gate; each PID's `/proc/<pid>/cmdline` is iterated NUL-delimited and only counts when some argv element matches the path-shape regex `^/?([^[:space:]/]+/)*bakeoff-(broad|deep)$`. Bash `-c` script strings (single argv element with embedded spaces) are rejected; real `python3 /path/to/scripts/bakeoff-broad …` invocations match at argv[1].
- **`auto-pr` Scenario B: PR-merged verification gate before close, post-rebase poll-and-merge loop**: timeout-recovery and hung-run paths in `scripts/auto-pr` now consult a pre-Scenario-B gate that checks whether the PR was merged during the timeout (poll endpoint 502'd while the merge actually completed) and falls back to a `mergeable=true` + short-timeout poll retry. When either fires the close-and-repush is skipped — merged → exit success, about-to-merge → fall through to the merge cascade. Post-rebase merge is now a labeled `while` loop with explicit `continue` after re-rebase (cap 3 iterations) instead of a single attempt + `Recovery:` hint.
- **`io-boundaries` CLI dropped leaf-caller roll-ups in the filter pass**: when `primitive_filter` or `exclude_tests` (default true) was set, `cmd_io_boundaries` reconstructed `BoundaryMapEntry` without `leaf_callers` or `entry_points_per_leaf`, so every bakeoff `io-boundaries.txt` artifact showed `chain_count > 0` with `leaf_callers=[]`. The leaf-rollup loop is now a public helper `compute_leaf_rollups`; the CLI lazily builds the reverse graph and recomputes rollups for the surviving chain subset. Tagged `awaits_bakeoff_validation`.
- **Python analyzer — module-level `NAME = ...` not indexed as Symbols**: the symbol-extraction pass walked classes / functions / methods only, so module-level constants (`PASS_VERSION`, `LANGUAGE_ALIASES`, `EXIT_SUCCESS`, …) were absent from `global_symbols`. Any `from <mod> import NAME` for such bindings missed the cross-file lookup and got synthesised as a tier-3 `external_symbol` instead — 151 ALL-CAPS externals on hypergumbo self-analysis. New emitter walks `tree.body` (top-level only) for `ast.Assign` and `ast.AnnAssign` with `Name` targets, including tuple-unpacking; skips `AugAssign` and Subscript / Attribute targets. The CSS-only `variable`-kind exclusion in the noise-filter is now language-conditional.
- **Python analyzer — monorepo `packages/<pkg>/src/<mod>/` layouts misqualified**: the previous helper only inspected `repo_root/src/`, so files under hatch / PDM / Poetry monorepo layouts (and hypergumbo's own `packages/<pkg>/src/`) fell back to a path-shaped qualifier like `packages.hypergumbo-core.src.hypergumbo_core.taxonomy` — invalid Python and not the real importable name. Replaced with a tree-walking source-root detector that picks the deepest matching root for each file. Single-root layouts collapse to the previous behaviour.
- **Python analyzer — dotted-submodule call resolution**: `_process_call` now emits `calls` edges for `from pkg.subpkg import X` + bare `X(...)` (e.g. `urlopen` from `urllib.request`) and `import pkg.subpkg` + `pkg.subpkg.X(...)` (multi-segment chain with an `ast.Attribute` receiver). Both were silently dropped, blocking io-boundaries and taint-flow from matching dotted stdlib primitives (`urllib`, `http.client`, `os.path`, `shutil`, `xml.etree`, `concurrent.futures`, `asyncio.subprocess`).
- **Rails exempt from import-edge framework demotion**: real Rails apps never have explicit `require 'rails'` (Bundler autoloads at boot), so `refine_frameworks` was demoting Rails to `dev_frameworks` and suppressing every `controller` / `route` / `form` / `serializer` concept tag from `rails.yaml`. New `_AUTOLOAD_BY_CONVENTION_FRAMEWORKS` exemption set, currently containing Rails. Sinatra (which IS explicitly required) stays demote-eligible — counter-test added.
- **Transcript-sync watcher doubling on SessionStart re-fires**: vendor sessions emit `session_id` on every lifecycle event (startup, resume, `/clear`, `/compact`); the unconditional launch call lacked a same-SID idempotence guard, so each re-fire stacked a fresh watcher on top of the live one — uniform 2x duplication of every event in 4 of 132 archived sessions. Two-layer fix: a same-SID PID-file kill before the orphan sweep, plus a `pgrep` fallback for watchers whose PID file was lost.
- **Transcript-change hook: TOCTOU race on injection-state file**: the load → decide → save critical section is now wrapped in an advisory `fcntl.flock` on a per-session `.lock` sibling. Before the fix, parallel PostToolUse hooks fired by the agent's parallel tool calls all read the same pre-write state, independently selected the same playbooks, and emitted duplicate injections — measured at a 33% session-wide violation rate across 6 different playbooks, with some duplicates landing 0.4 s apart.
- **IO catalog — `replace` / `rename` added to `ambiguous_names`**: the matcher's short-name fallback was matching every `something.replace(...)` as `pathlib.Path.replace` (a filesystem rename), producing 40+ false-positive `fs_write` chains on self-analysis from string-normalization sites like `name.replace("-", "_")`. Resolved calls with a `pathlib.Path` module hint still tag correctly. `fs_write` chain count: 138 → 98.
- **`sketch_embeddings` loads HuggingFace models offline-first**: a new helper tries `SentenceTransformer(name, local_files_only=True)` first and falls back to a normal load only on `(OSError, ValueError)`. Eliminates the "unauthenticated requests to the HF Hub" warning that fired on every `hypergumbo .` run with the embeddings extra installed.
- **`release.yml` pip-audit CVE-ignore aligned with `ci.yml`**: adds `--ignore-vuln CVE-2025-71176` (pytest 9.0.2 TOCTOU, dev-only transitive via `pytest-textual-snapshot 1.1.0`; single-tenant self-hosted runner). Both gates drop the ignore when `Textualize/pytest-textual-snapshot#24` ships.
- **`ci.yml` / `release.yml` pip-audit ignore for CVE-2026-3219**: pip concatenated-ZIP+tar archive confusion (CVSS 4.6 MEDIUM; AV:L, UI:A, VI:L). Fix is pip 26.1, but that release was not yet on PyPI when the CVE published 2026-04-20; the existing `pip install --upgrade pip` step picks it up automatically once it ships. Zero attack surface on the self-hosted runner.

### Removed

- **Third-party wrappers purged from every I/O catalog**: catalog membership is now strictly "the language ships it" — the previous grandfathered HTTP-client carve-out was a slippery slope. Per-language removals: **Python** — `requests` / `requests.Session`, `aiohttp.ClientSession`, `httpx.Client` / `AsyncClient`. **Java** — Apache Commons IO, Netty, OkHttp, Spring Web (`RestTemplate` / `WebClient`), Apache HttpClient 4/5, Unirest, Retrofit, Spring Data + Hibernate, SLF4J, Log4j 1.x / 2.x, Logback (JDK + Jakarta EE stay). **JavaScript** — npm HTTP clients (`axios`, `node-fetch`, `ky`, `superagent`, `got`, `undici`), Express, Fastify, Koa (Node built-ins and browser globals stay). **Rust** — `tokio::fs`, `tokio::net::*`, `hyper`, `axum`, `actix_web`, `reqwest` (`std::*` stays). **Scala** — fs2, cats-effect, sttp, http4s, akka, pekko, Play, Slick, Doobie, Quill, ScalikeJDBC, Anorm, ReactiveMongo, ZIO, scala-logging (`scala.*` + inherited `java.*` stay). Structural tests iterate every catalog primitive and assert a stdlib module prefix. Dropped chains resurface in `external_potential`.

## [2.7.0] - 2026-04-21

### Added

#### Rust analyzer backend (ADR-0014)

- **SCIP ingestion**: new `hypergumbo_core.scip` module parses Sourcegraph SCIP symbol strings and protobuf indexes (vendored binding, `scripts/build-scip-proto` regenerates at pinned SHA), then translates them to hypergumbo `Symbol`/`Edge`/call-reference objects. Adds `protobuf~=6.33` to hypergumbo-core.
- **`hypergumbo-lang-rust-analyzer` optional package**: shells out to `rust-analyzer scip`, translates the index to IR, and post-processes Rust function stable_ids through a `rust.py` parity helper so tree-sitter + SCIP symbols dedup under a single identity. Three discriminated exceptions cover missing binary / invocation failure / no output; 600 s default timeout.
- **Graceful-degrade orchestration**: `try_analyze_with_rust_analyzer` returns `None` on any failure with deduped fall-through messages. Registered analyzer at priority 45 alongside `rust.py` at 50.
- **CLI + install surface**: new `--backend rust-analyzer` root flag (sets `HYPERGUMBO_RUST_ANALYZER=1`), `install-rust-analyzer` / `uninstall-rust-analyzer` subcommands, and `install-extras` / `uninstall-extras` umbrellas with `--check` status table and `--skip` exclusion.

#### Linkers (Framework subcategory)

- **Controller-routes linker**: `contains_routes` edges from `concept: controller` classes to nested route handlers. Covers NestJS, Spring Boot, ASP.NET, Laravel, Symfony, Phoenix, Micronaut, Ktor, Grails, CakePHP.
- **Router-routes linker**: `registers_routes` edges from `concept: router` symbols to nested route registrations. Covers Phoenix, http4s, http4k, Yesod, giraffe, pedestal, ring-compojure, cowboy, sveltekit/remix/nuxt, vertx, plumber, laminas.
- **Rust trait-impl dispatch linker**: fans `dispatches_to` edges from each trait symbol to every concrete method on implementing structs. Generic-bound / `dyn Trait` call-site resolution deferred.
- **Django ORM dispatch linker**: `dispatches_to` edges from Django subclasses (`Model`, `Manager`, `QuerySet`, `ModelAdmin`, `ModelForm`, `View`, …) to user-defined overrides of framework-called methods.
- **Jackson / JavaBean serialization dispatch linker**: `dispatches_to` edges from annotated Java/Kotlin/Scala classes to bean-convention accessors (`getX`/`setX`/`isX`) and method-level handlers.
- **Airflow dispatch linker**: `dispatches_to` edges from `BaseOperator`/`BaseHook`/`BaseSensor`/`BaseTrigger` subclasses to framework-called lifecycle methods (`execute`, `pre_execute`, `poke`, `on_kill`, …).
- **Kafka Streams dispatch linker**: `dispatches_to` edges from classes implementing any of 17 Kafka Streams callback interfaces (`ValueMapper`, `Transformer`, `Processor`, `Aggregator`, +`*Supplier` forms) to their callback methods.

#### HTTP linker (cross-language)

- **Elm client detection**: HTTP linker scans `*.elm` files for `Utils.Api.<method>` wrappers, `Http.get`/`Http.post` record forms, and `Http.request`, plus indirect `let url = String.join "/" [...]` URL folding.
- **JS/TS backtick template-literal `fetch`/`axios` with module-const folding**: folds backtick URLs against module-scope constants; unresolved `${NAME}` slots map to path parameters with prefix-match fallback.

#### Entrypoints (concept → entrypoint mapping)

- **`error_handler` → `ERROR_HANDLER`** (confidence 0.95): 37 framework YAMLs — fastapi, express, django, aspnet, flask, actix, axum, gin, nestjs, rails, laravel, symfony, phoenix, …
- **`form` → `FORM`** (confidence 0.90): 12 framework YAMLs — Django, Flask-WTF, Laminas, cakephp, laravel, symfony, yii, pyramid, rails, remix, sveltekit, yesod.
- **`serializer` → `SERIALIZER`** (confidence 0.90): 9 frameworks via class-level `base_class` match — DRF, Flask Marshmallow, grape, laravel, litestar, plumber, pyramid, quart, rails.

#### Behavior map

- **Per-handler forward slices from `run`**: emits `slice.handler.<METHOD>.<path>.json` per detected route handler using bakeoff-proven parameters. Capped at 25; `--no-handler-slices` / `--max-handler-slices N` control behavior.
- **Method-call recovery linker**: rewrites `calls→Class` + `unresolved-call(name=foo)` pairs into direct `calls→Class.foo` edges when the class contains a matching child. Language-agnostic.
- **Route materializer dedupes against analyzer-emitted routes**: fixes Django CBV double-counting on pretix (985 → ~500 unique routes).
- **Class-level annotations propagate to methods for `--exclude-annotated`**: helps Spring controllers, Django CBVs, and other class-level-registered frameworks.
- **IO-boundary leaf-caller roll-ups**: `BoundaryMapEntry` gains `leaf_callers` and `entry_points_per_leaf` so shared helpers don't collapse disjoint caller chains.
- **Gradle / Maven dependency manifest for JVM tiers**: new `jvm_deps.py` parses `build.gradle`, `build.gradle.kts`, and `pom.xml`; direct deps → tier 2, unknown → tier 3. Manifest scan skips test-fixture directories (fixes detekt misdetecting `react` from fixture `package.json`).

#### Language support

- **Haskell module exports as dead-code seeds**: parses `module Foo (publicFn, Type(..)) where` headers and marks listed symbols `is_exported=True`.
- **Yesod framework detection + pattern set** (`frameworks/yesod.yaml`): covers `mkYesod`/`parseRoutes` quasi-quoter, Warp runner, `Yesod`/`YesodSubsite` typeclasses, and `<method><Resource>R` handler convention.
- **Kotlin extension-function call-site dispatch**: `receiver.extFn()` emits `calls` edges to the extension definition when receiver type matches. Evidence `ast_call_extension` at confidence 0.80.
- **Unresolved-call edges for bare global JS/TS calls**: `console.log()`, `localStorage.setItem()`, `navigator.sendBeacon()`, `window.fetch()`, `Deno.readFile()`, etc. emit unresolved edges when no import binding shadows them.

#### I/O primitive catalogs

- **TS/JS bare-name and namespace/default imports traced**: emits unresolved-call edges for `import { existsSync }`, `import * as fs`, `import axios`. Verified on create-next-app (0 → 35 boundaries) and apollo-server (7 → 14).
- **JavaScript browser APIs**: WebSocket, EventSource, BroadcastChannel, XMLHttpRequest, localStorage / sessionStorage / indexedDB / caches, ….
- **Java catalog expansion** (~136 → 312 primitives): full JDBC + JPA + Hibernate + Spring Data; SLF4J / Log4j / Logback / JUL; Apache HttpClient, Spring WebClient, Unirest, Retrofit, Commons IO. Kotlin inherits.
- **Elixir catalog** (`io_primitives/elixir.yaml`): stdlib, HTTPoison / Tesla / Req / Finch / Mint / `:httpc`, Phoenix/Plug, Ecto/Postgrex/MyXQL/Redix, GenServer/Oban/Task IPC.
- **Kotlin catalog** (`io_primitives/kotlin.yaml`): previously aliased to `java.yaml` (detekt produced only 1 boundary). Covers `kotlin.io` File/Path, ktor client/server, `android.util.Log`, `kotlin-logging`, Exposed ORM.

#### Stop hook & bakeoff validation

- **Stop-hook nudge for `awaits_bakeoff_validation` backlog**: appends an `## AWAITS_BAKEOFF_VALIDATION BACKLOG` section when tag-bearing items exceed `threshold` and the latest DEEP cycle is older than `stale_cycle_hours` (defaults 5 / 72 h). Configurable under `stop_hook.awaits_bakeoff_validation_nudge`.
- **`awaits_bakeoff_validation` cross-reference in reflect pipeline**: `bakeoff-deep-reflect` injects per-claim prompts and records `moved` / `no_move` / `inconclusive` verdicts. `aggregate --apply-verdicts` executes the tracker mutations (`moved` strips the tag; `no_move` spawns a regression sub-item). Dry-run by default.

#### CI & smart-test

- **`test-agent-infra` full-suite CI job**: new hard-gate job in `full-suite.yml` running the top-level `tests/` directory, closing the 4-hour cadence gap for `scripts/agent-supervisor`, `.agent/hooks/_shared/*.py`, and tracker-sync glue.
- **Per-PR smart-test coverage for top-level infrastructure**: new `top_level_test_map.py` maps changed top-level paths to `tests/test_<basename>.py`, folded into `AFFECTED_TESTS` by `smart-test`.

#### Agent-supervisor

- **`scripts/agent-supervisor` daemon**: Python daemon that monitors reserved-prefix tmux sessions (`hypergumbo-session-*`) and replaces stuck ones (≥ 15 min of no pane-byte delta) with fresh vendor CLIs seeded with `HYPERGUMBO_RESPAWN=1`. Subcommands `run` / `status` / `stop`; single-instance via `fcntl.flock`; state under `~/hypergumbo_lab_notebook/agent-supervisor/`. Rate-limited at 24 spawns / 24 h with auto-shutdown after 20 saturation ticks.
- **Respawn hook surface**: `.agent/hooks/_shared/touch_heartbeat.sh` sourced from every per-turn hook for telemetry; vendor session-start hooks branch on `HYPERGUMBO_RESPAWN` to auto-enable autonomous mode per `autonomous_intent.txt` and emit a seed prompt.
- **Meta-circuit-breaker**: classifies replacements as no-progress (≤ 512 pane bytes) vs progress and auto-pauses after 5 consecutive no-progress failures. `agent-supervisor resume` clears the sentinel.
- **Non-interactive seed-prompt bootstrap**: polls `tmux capture-pane` for content stability (15 s deadline), then injects `"begin"` to trigger the first model turn. Vendor-agnostic.
- **YOLO / bypass-sandbox invocation**: per-vendor flags skip approval prompts (Claude Code `--dangerously-skip-permissions`, Codex `--dangerously-bypass-approvals-and-sandbox`, Cursor `--force`, Gemini `--approval-mode=yolo`). Supervisor should run in a snapshotted VM.
- **Vendor Parity for Respawn table in AGENTS.md**: authoritative per-vendor table (Claude Code, Codex CLI, Cursor, Gemini CLI) covering hook paths, graceful-exit keystroke, and CLI invocation. Claude Code's `/quit` verified; others marked unverified with a documented verification procedure.
- **Operator-affordance fixes**: `stop` no longer ambushes the next `run` (checks `supervisor.lock` pid first); new `debugging-reset-rate-limit` subcommand zeros the 24 h spawn counter.
- **Intent/mode split in `loop-toggle`**: new gitignored `autonomous_intent.txt` records project intent separately from session runtime mode. Stop-hook circuit-breaker trips now deactivate the session without suppressing project intent.

### Changed

- **Linker subcategory vocabulary restored** (ADR-3bbb): Protocol / Bridge / Framework / Infrastructure subcategory taxonomy is now first-class. Every linker module docstring declares its subcategory; `docs/LINKERS.md` enumerates all 45 linkers with a Subcategory column.
- **Stop hook: process-aware pause replaces 150 s blanket sleep**: polls every 3 s (1800 s cap) while `pytest` / `smart-test` / `auto-pr` / `merge-pr` are alive; returns immediately when none. Configurable via `stop_hook.watched_*` keys; `watched_process.py` filters `bash -c` / `sh -c` wrappers and normalises Python version suffixes.
- **Dead-code prospector: 8 → 46 gap categories**: adds language-gated rules (Rust trait impls; Python dunders / Django / Airflow; Go receiver methods / k8s / Cilium; Java JavaBean / Kafka / Spring; TS/JS React / Redux / Superset / Apollo). Reduces `uncategorized` on the 2026-04-11 corpus (92,218 candidates, 11 polyglot repos) from **94.0 % → 43.5 %**.
- **Behavior map node IDs use repo-relative paths**: strips the `repo_root` prefix from every Symbol/Edge/UsageContext path. Paths outside `repo_root` preserved.
- **`generate-concepts` scans Python source for programmatic concept emitters**: catches cases like `py.py` emitting `main_guard` from its AST walker. Ghost count 1 → 0.
- **`generate-concepts` detects variable-name and tuple-membership consumer patterns**: recognises `concept_type in (...)` / `{...}` / `[...]` and `not in`. 30 concepts flip inert → live; coverage moves 7/309/0 → 37/279/1 (live/inert/ghost).
- **`test-coverage` surfaces per-language false-negative caveats**: text output prints the ~20% recall gap and per-language blind spots (Java/Spring MockMvc, Kotlin PSI, Go YAML reflection, Scala macros, Ruby `described_class`, Python `parametrize`, JS/TS `describe.each`, C# `[Theory]`/Moq). JSON gains a structured `caveats` field.
- **Unified path argument across subcommands**: every subcommand accepts both `hypergumbo <cmd> /path` and `hypergumbo <cmd> --path /path`.
- **`routes` excludes test-file routes by default**: 14% of plausible's routes were from tests. `--include-tests` opts back in.

### Fixed

#### CI / build system

- **Argparse sentinel test dropped + nightly retry Node.js ordering**: (1) `test_discuss_rejects_ack_thread_before_message` deleted after Python 3.12/3.13 argparse backtracking changes. (2) `test-matrix-retry` in `nightly.yml` / `release.yml` had `actions/download-artifact@v3` before `Install Node.js`, firing full-suite on every matrix value when any primary failed.
- **`tree-sitter-c-sharp` pin tightened to `~=0.23.5`**: 0.23.5 flattened named-argument nodes and broke detection under the loose `~=0.23.1` pin; `csharp.py` named-arg handling updated.
- **`concurrency.cancel-in-progress` on tracker-ci.yml**: prevents stacked runs on retry (matches `ci.yml` block).
- **Top-level `tests/` drift**: three pre-existing failures surfaced on instrumentation. `test_committed_file_is_up_to_date` now passes `ANALYZER_SRC_DIRS` to `scan_producers`; two `TestLogTrainingExampleCohortMetadata` tests assert `pipeline_version == "v2"`.
- **`release-check` gitleaks noise quieted via `.gitleaks.toml`**: `gitleaks detect --no-git` walks the working tree regardless of `.gitignore`, so local-only agent state (transcripts, injection history, training data, rotation locks) was producing ~395 false positives per scan — mostly the 40-hex SCIP commit SHA matching the `sourcegraph-access-token` rule inside quoted transcript content. New config path-allowlists everything under `.agent/` except the committed subtrees (`agent_playbooks_protocols_sops_skills/`, `hooks/`, `tracker/`, `tracker-workspace/`, `cooldown_prompt.md`, `stop_reflect.md`) plus `__pycache__/`. Also drops scan time from ~1 min / 1.06 GB to ~8 s / 73 MB.
- **`hypergumbo-lang-rust-analyzer` added to `bump-version` and `release-check`**: the package's `pyproject.toml` and `__init__.py` are now bumped alongside the other main packages, and `release-check` includes it in the version-sync audit, build loop, and wheel-install check. Previously `prepare-release 2.7.0` left it pinned at 2.6.0.
- **`release-check` ruff gate cleared for top-level `tests/`**: 7 pre-existing violations (2 × RUF012 class-level fixture constants, 2 × F821 unresolved `"Any"` string annotations on `capsys`, 1 × RUF013 implicit `Optional`, 2 × RUF100 unused `noqa` targeting non-enabled annotation rules) were fixed in place or added to the test per-file-ignore list. `S607` and `RUF012` joined the existing test-scope ignore set; the per-file-ignore stanza now covers both `packages/*/tests/**/*.py` and the repo-root `tests/**/*.py`.
- **`release-check` pytest stage realigned with sibling full-suite runners**: previously ran `pytest --full --cov-fail-under=100 --quiet 2>/dev/null`, routing through the smart-test pytest wrapper — a dev-loop tool whose affected-only selection and targeted-manifest side effect are inappropriate for a release gate (ADR-0010). The three other authoritative full-suite runners (`full-suite.yml`, `nightly.yml`, `release.yml`) all call pytest directly. `release-check` now matches that pattern: `python -m pytest packages/*/tests/ "${COV_PATHS_ALL[@]}" -n auto --cov-fail-under=100`, stderr no longer redirected to `/dev/null`, output captured to a dedicated `.ci/release-check-pytest.log` named in the failure message. Coverage scope extended to `hypergumbo-tracker/src` so every package `bump-version` touches is gated.
- **New `scripts/lib/cov-paths.sh`**: single source of truth for the per-package `--cov=` args needed by authoritative full-suite runners. Sourced by `release-check`; intentionally not sourced by `smart-test` (dev-loop keeps its own coverage policy, e.g., excluding tracker). Adding a new released package now means appending one line to this file instead of editing every gate in parallel.
- **`release-check` no longer false-positive-fails on fresh release branches**: the "Check if up to date with remote" step captured `$(git rev-parse "origin/$CURRENT_BRANCH" 2>/dev/null || echo "none")`. Without `--verify`, `git rev-parse` echoes the input ref to stdout *and* returns non-zero on an unresolvable ref, so `REMOTE` ended up as the multi-line string `"origin/release/vX.Y.Z\nnone"` — failing both the `== "none"` warn branch and the `git merge-base --is-ancestor` warn branch — and blocking the release gate every time `prepare-release` created a brand-new branch. Switched to `git rev-parse --verify "origin/$CURRENT_BRANCH^{commit}"`, which emits nothing and exits non-zero on an unknown ref, so the fallback branch is the only thing the substitution captures.
- **`smart-test` and `prepare-release` no longer die with SIGPIPE (141) on release commits**: both scripts run `set -o pipefail` and both had a `find … | head -1` pipeline that `head` closes after one line, propagating SIGPIPE back to `find`/`sort` and killing the enclosing script with 141 before its real work completes. In `smart-test`'s VERSION_ONLY branch (scripts/smart-test:601, the "one test per affected package" selector for release-commit manifests) the failure meant the targeted manifest was never written, so `auto-pr`'s `elif smart-test --manifest` at scripts/auto-pr:1000 fell through and printed the misleading `⚠️  Manifest generation skipped (no stable hypergumbo?)`. CI's per-PR `pytest` job then rejected the resulting stale manifest with `❌ No valid manifest - cannot run tests`. Fixed: the smart-test site now uses `readarray` into a process-substitution so there is no outer pipeline for pipefail to kill; the prepare-release site (scripts/prepare-release:146, checking whether any tracker ops are pending) uses `find -print -quit` so there is no pipe at all.

#### auto-pr

- **`.ops` backup/restore no longer overwrites concurrent tracker writes**: new `_ops_union_restore_file` helper performs an order-preserving line-level union instead of `cp`-clobbering; restore loop enables `shopt -s dotglob` so dotfile `.ops` paths match.
- **Exit 2 (timeout) soft-retry**: the hung-run retry loop previously fired only on Exit 3. On Exit 2, `auto-pr` now re-polls once with a 300 s timeout and does one close-PR + repush before escalating to Scenario B.

#### Stop hook

- **`stop_hook_state.json` write discipline**: jq merge now starts from an explicit maintained-field extraction instead of `.`, so dropped keys from old migrations no longer linger. Recover-state playbook documents the field table.

#### Analyzers & edges

- **Solidity file-level `using X for Y;` applies inside contracts**: edge extractor now unions contract-scoped with file-level `using_libraries` set.
- **`test-coverage` recognises framework-tagged tests outside test paths**: any function with a `meta.concepts` entry starting `test` is treated as a test. Fixes shellcheck's Template-Haskell `$forAllProperties` case (2214 `prop_*` functions → 0% reported coverage before).
- **`dead-code-maybe` drops generated-file candidates**: any candidate with `supply_chain.is_generated_file=True` is filtered before ranking. Language-agnostic.
- **Django CBV routes expand per declared HTTP method**: `path("/foo/", FooView.as_view())` previously emitted a single `[GET]` route; new `expand_class_based_view_routes` post-pass emits one route per declared method. Out-of-repo view classes stay `[ANY]`.
- **Java `size`/`length`/`copy`/`find` no longer misclassified as `fs_read`**: added to io-boundary `ambiguous_names`.
- **Scala framework detection reads `project/*.scala` and `project/*.sbt`**: SBT meta-build convention keeps real coordinates in `project/Dependencies.scala`. Docspell's http4s imports are now visible to `profile.frameworks`.
- **Laravel `apiResource()` phantom routes eliminated; `.except()` / `.only()` honored**: 5 routes instead of 7 (index/store/show/update/destroy). Koel: ~40 phantom routes eliminated (~19% of 207).

#### CLI

- **`--config-extraction=embedding/hybrid` warns when sentence-transformers is missing**: both modes silently degraded to heuristic. Dispatcher now emits a one-shot stderr notice before falling back.
- **`verify-claims` surfaces languages with no taint-flow catalog**: trivially-passing claims against unanalyzed languages previously gave false security confidence. JSON schema unchanged.
- **`io-boundaries` distinguishes "no I/O" from "language unsupported"**: `IoBoundaryCatalog` gains `is_supported: bool`; JSON output adds `unsupported_languages: []`.
- **Subcommand parser cleanup**: (1) `hypergumbo foobar` prints a `Did you mean: …` via `difflib` instead of silently inserting `sketch`. (2) `--debug` stripped from argv in any position.
- **Embedding-model load quieted**: `_hf_noise.suppress_hf_noise()` runs at `sketch_embeddings` import (before `sentence_transformers` caches env) via `setdefault` so user overrides are preserved.
- **`-e/--exclude` glob normalization**: `ui/`, `ui/**`, `**/ui/**`, `**/ui` behave consistently with bare `ui`. Path-anchored patterns like `cmd/server.go` honored against the relative path.
- **README / markdown heading bleed**: ATX headings in rendered `.md`/`.mdx`/`.markdown`/`.rst` files demoted 2 levels so they don't compete with hypergumbo's H2 structural sections.
- **Token budget validation**: `-t 0` and negative values rejected by argparse on `sketch` and `explain`.
- **Single-file input exits cleanly**: `hypergumbo run` / `sketch` on a file prints a hint and `sys.exit(1)` instead of `NotADirectoryError`.
- **Quieter partial-linker warnings on polyglot repos**: suppress when the only met requirement is a language-file presence check. Alertmanager: 8 warnings → 1.
- **`--require-section` actually works**: fixes `max_tokens <= base_tokens` early-return bypassing section gates. Verified on alertmanager `-t 500`.

### Performance

- **Cached secret-scan results across warm sketch runs**: `scan_content_cached` keys gitleaks output by sha256 (8 entries). Warm `hypergumbo sketch` ≈ `--no-secret-scan` time (~7 s on alertmanager, was ~15 s). Cache invalidates on repo state change.

### Documentation

- **`docs/agent-supervisor.md` operator guide**: net-new user-facing doc covering first-time setup, daily operations, `status` JSON semantics, edge cases, and troubleshooting matrix. Linked from `README.md`.

## [2.6.0] - 2026-04-12

### Changed

- **Stop hook relaxed on CONVERGED bakeoffs**: guidance now leads with `tracker ready` instead of requiring reflect/aggregate when bakeoff is converged.
- **Bakeoff-deep hub-collision warning**: `pick_reverse_slice_seeds` warns on seeds with `prod_in_degree > 1000`.
- **`io-boundaries` defaults to production-only**: test chains excluded by default (was 78% noise). `--include-tests` opts back in.
- **Adaptive hop limit removed from slice**: 3-10 hop limit replaced by `max_files` (100) and hub pruning (50). `--max-hops` still available for explicit control.

### Added

#### Developer experience

- **`auto-pr --tracker-id`**: on merge, appends a discussion entry to the referenced tracker item citing the PR number and dev SHA.
- **`bakeoff-map` script**: walks bakeoff artifacts and emits a chronological map of sessions with convergence verdicts, pipeline-stage completion, and anomalies.
- **`tracker-path-linter` V1**: verifies file-path tokens in tracker items resolve to real files. Stale references carry fuzzy-match suggestions.
- **`audit-stale-timestamps` V1**: checks agent state files for embedded-timestamp drift (e.g. `last_completed_utc` vs file mtime).

#### Slice telemetry

- **Forward-dataflow admission-rule telemetry and option 2 evaluation**: `SliceResult.admission_stats` records per-rule counters for edges admitted/rejected during forward dataflow BFS. Telemetry across 4 repos (~188k edges) shows zero additional edges from option 2 — option 1 (writer-source admission) remains canonical. Re-evaluation trigger in ADR-0015 §6.1.

#### Linkers (Framework subcategory)

- **`go_memberlist` linker**: `dispatches_to` edges from `memberlist.Create` to the 12 canonical delegate methods (`NotifyMsg`, `GetBroadcasts`, `LocalState`, etc.). Used by alertmanager, consul, nomad, serf, vault.
- **`go_cobra` linker**: `dispatches_to` edges from `cobra.Command{…}` struct literals to handler functions in `Run`/`RunE`/`PreRun`/`PostRun` and `Persistent*` variants. Used by kubectl, helm, hugo, prometheus, terraform, docker. Package-level `var cmd = &cobra.Command{…}` declarations now emit edges from the var symbol when no enclosing function exists.

#### Behavior map

- **`hypergumbo dead-code-maybe` subcommand**: finds production callables unreachable from entrypoints via BFS over `calls`, `dispatches_to`, `routes_to`, and `wraps` edges. Configurable seed sets (`--seeds {entrypoints,tests,exports,all}`), text/JSON output, `--min-confidence` filtering, ranked by LOC. Cross-language string collision signal detects missing linker edges; FFI-signature auto-flag boosts FFI-marked candidates; `--exclude-exports` filter completes the three-filter set.
- **`Symbol.is_exported` across 5 languages**: new boolean marking public-API callables. Go capitalized identifiers, Rust `pub`/`pub(crate)`, `public` modifier (Phase 1); Python `__all__` / leading-underscore (Phase 2); TS/JS `export` statements; Kotlin extension functions; Scala secondary constructors. `--seeds exports` treats exports as reachability seeds. Drops dead-code false-positive rates 70-83% on Python framework libraries.
- **Generated-code detection and centrality demotion**: `is_generated` flag on files/symbols detects OpenAPI models, protobuf stubs, K8s code-gen, go-swagger output (`api/v2/restapi/`, `api/v2/models/`, fingerprint files), and `openapi-gen/` directories. Content-based header scanning (`// @generated`, `// Code generated … DO NOT EDIT.`) in the first 4 KiB of 36 text-like extensions. Generated code receives 95% centrality penalty, and `dead-code-maybe` unconditionally drops any candidate whose file is flagged generated.
- **Test file classification**: `is_test` decoupled from supply-chain tier as independent axis. Co-located test files (`_test.go`, `.test.js`, `.spec.ts`) classified as tier 1 instead of tier 2.
- **Return-type registry for chained receiver resolution**: `method_return_types` populated during Pass 1 for Go and Java. Enables `x := e.Query(); x.Rows()` resolution via the registry. Inline chained calls like `e.NewQuery().Exec()` resolve at confidence 0.75.
- **Go build-tag-gated alternate definitions**: `//go:build` directives emit `build_tag_alternative_of` edges between same-named symbols in mutually exclusive files.
- **Event-sourcing linker expansion**: extends event detection to Guava EventBus, generic Java event bus, Go channel-based events, and Go event bus method calls.
- **Go closure wrapper edges**: route registrations through closure wrappers (e.g. `wrapAgent(api.query)`) emit `wraps` edges. Covers Gin/Echo/Fiber and Gorilla mux/stdlib.
- **Import-based framework validation**: manifest-detected frameworks cross-referenced against import edges. Test-only or unimported frameworks reclassified as `dev_frameworks`.
- **Go tier 2/3 classification via go.mod**: unresolved Go external references classified using `go.mod` — direct deps tier 2, indirect/stdlib tier 3. Language-agnostic `DependencyManifest` enables future extension.
- **Gradle multi-project workspace detection**: `detect_package_roots()` now parses `settings.gradle` / `settings.gradle.kts` `include` directives. Gradle subprojects are classified as workspace members, fixing degenerate tier distribution on Gradle monorepos like Kafka.
- **Orchestration hub floor for symbol ranking**: functions with out-degree ≥ 20 get a minimum effective in-degree of `sqrt(out_degree) * 0.8`, preventing orchestration hubs (main, run, app) from being buried by within-file dampening.
- **Event edge type weights**: `event_subscribes`/`event_publishes` raised to 0.8 (was 0.5). `dispatches_to` added at 0.6.

#### Language analyzers

- **TLA+**: tree-sitter analyzer for `.tla` formal specification files. Extracts module, operator, constant, variable, theorem, and assumption symbols. EXTENDS/INSTANCE as `imports`, cross-references as `references`.

#### Dataflow library_patterns expansions

- **Python AST wiring**: `python.yaml` ships `library_patterns` for common mutating/reading methods. `annotate_dataflow_ast` now consumes these as a per-language fallback for Python's AST analyzer.
- **Python serialization + file-position primitives**: 14 patterns — `json.dump`/`pickle.dump`/`yaml.dump` as write, `json.load`/`pickle.load`/`yaml.load` as read, `.seek` as mutate, `.truncate` as write.
- **Cross-language library_patterns**: name-based access_mode heuristics for Java (25 patterns), JS/TS (23 each), C# (24), and Kotlin (17). Enables `access_mode` annotation for dataflow slicing in these languages.
- **Go state-mutating verbs**: `.Expire`, `.GC`, `.Truncate`, `.Drop`, `.Init`, `.Reload` tagged `access_mode=write`.

#### Training data pipeline

- **Per-session transcript sync** (ADR-0018 amendment): concurrent sessions now write to isolated files keyed by `session_id` instead of racing on shared state. Session-end rotation atomically promotes files into `.last_*`/`.second_to_last_*` slots. Cursor exempted via sibling check; injection-history sidecar tracks playbook events.
- **v0 corpus cohort backfill**: `backfill-training-data-cohort-tags.py` writes a sidecar with per-entry `infra_sha`, `playbook_registry_sha`, `main_llm_presumed`, and playbook counts. Re-runnable, non-destructive.
- **Per-entry cohort metadata**: `log_training_example` now writes `pipeline_version`, `infra_sha`, `playbook_registry_sha`, `main_llm`, `vendor`, `vendor_version`, and `scoring_model` on every entry. Distribution shifts discoverable from the corpus alone.
- **Multi-vendor interjection normalization**: `filter-transcript.py` emits `normalized_user_interjection` rows for user interjections across Claude Code, Codex CLI, and OpenHands. `pipeline_version` bumped to v2.

#### CLI & infrastructure

- **`hypergumbo config <lang>`**: shows all per-language configuration (dataflow patterns, IO primitives, function summaries) in one view. Supports `--format json|yaml|text`.
- **smart-test flock guard**: concurrent invocations prevented via `flock`. Second invocation exits immediately naming the holding PID.
- **`auto-pr` resilience**: `list`/`status` detect and `prune` removes stale vPR entries. Already-merged push rejections handled gracefully. New `.git/AUTOPR_LAST_RESULT.json` sentinel records outcome on every exit.
- **`merge-pr close <PR>`**: close a PR without merging, with optional `--reason` audit-trail comment.
- **Bakeoff-deep integration tests**: 13 tests covering `init → cohort → cycle → iter-NNN/` end-to-end.

#### Dead-code prospector: polyglot-only filter

- `dead-code-prospector-run.py` skips monoglot repos (fewer than 2 languages with ≥10 files each). `--include-monoglot` bypasses.

#### Go encoding/serialization callback entrypoints

- Go marshal/unmarshal methods (`MarshalJSON`, `UnmarshalYAML`, etc.) detected as `serialization_callback` entrypoints via `go-encoding-callbacks.yaml`. Previously invisible to the call graph.

#### Broker / server lifecycle entrypoint heuristics

- Three new naming-tier patterns detect JVM broker lifecycle methods (`*Server.startup/start/run/shutdown`, `*Apis.handle*/process*/dispatch*`, `*Acceptor.run`) as `CONTROLLER` entrypoints. Surfaces the broker request-dispatch surface on Kafka and similar services.

### Fixed

#### Java analyzer

- **Short-name collision**: local classes with names colliding with library classes (e.g. `Logger` POJO vs slf4j `Logger`) no longer absorb cross-file calls. Eliminated 2057+ bogus edges on Kafka.

#### Hook test infrastructure

- Fixed silent failures in `.githooks/test_hooks.sh` (stale PID from command-substitution subshell). Wired into CI as a `hook-tests` job.

#### Dataflow annotation preservation

- **`access_mode`/`dest_access_mode` preserved through 4 linkers**: `event_sourcing`, `ipc`, `websocket`, and `message_queue` linkers were overwriting the meta dict, stripping dataflow fields. Fix: pass metadata via `Edge.create` kwarg.

#### Agent state recovery

- **Delete vestigial `.agent/last_stop_check.json`**: removed stale file left after migration to guidance_log.
- **Split stop-hook state file**: split into `stop_hook_state.json` (hook-written) and `agent_notes.json` (agent-written via `scripts/agent-notes`).

#### IO boundaries

- **Go logging reclassified**: `fmt.Print*`, `log.*`, `log/slog.*` moved from `ipc_send` to `logging`. Eliminates 134 false-positive IPC chains on alertmanager. `os.Stdout`/`os.Stderr` remain `ipc_send`.

#### Go analyzer

- **Receiver-type guard for interface_dispatch**: calls on external/stdlib receivers no longer dispatch to local interface methods of the same name. Eliminated 13 spurious edges on alertmanager.
- **Cross-file struct method aggregation**: structural interface matcher now aggregates `struct_method_sets` per package directory. Methods in sibling files within the same package are no longer dropped.
- **Cross-package struct collision**: struct method sets keyed by short name caused merging across packages. Fix: iterate per-file. `dispatches_to` edges 3 → 19 on alertmanager.
- **Structural interface arity matching**: satisfaction check now verifies parameter and return counts, not just method names. Removes 463 false `dispatches_to` edges on alertmanager.
- **Cross-package interface dispatch resolution**: cross-package interface fields (e.g. `stage notify.Stage`) now strip package prefix before method lookup.
- **Route resolver receiver-method shadow**: handler `api.query` (lowercase receiver) couldn't match symbol `API.query` (uppercase type). Fix: prefer same-file candidates via `symbols_by_short_name` index.

#### Symbol resolution

- **Go promoted-method interface satisfaction**: structural interface matcher traverses embedding chains. Promoted methods included in satisfaction check.
- **Type hierarchy per-language gate**: `extends` edges in Go, C++, Rust, C# no longer emit `dispatches_to` (composition, not inheritance). Eliminated false edges in reverse slices.
- **Type hierarchy concrete→concrete fan-out**: same-named concrete types across packages no longer produce false `dispatches_to` edges. 70% of alertmanager's 459 edges were false positives.
- **`ListNameResolver` path-hint false positives**: path hints require segment-level suffix matching instead of substring.
- **`library_patterns` YAML never applied**: `scan_library_patterns` had no callers — wired into `annotate_dataflow`. Alertmanager `access_mode='write'` edges: 0 → 274.

#### Slice

- **Forward dataflow admits downstream reads**: read edges downstream of writers now admitted as one-hop terminals in forward slices, per ADR-0015 §6.
- **Reverse-slice filename collision**: reverse slices now write to `slice.<name>.reverse.json` to avoid overwriting forward slices.

#### Profile & sketch

- **Profile LOC always zero in behavior map**: `hypergumbo run` now populates per-language LOC in the profile. Previously LOC was only backfilled in the sketch path.
- **False positive `cargo test` in sketch**: ambiguous test framework patterns (e.g. `#[test]`) now scoped to their language's file extensions.

#### Bakeoff signals

- **`bakeoff-deep init` recency check**: warns before creating a new session when a recent one (< 7 days) matches the same pool and code hash.
- **`bakeoff-deep compare` metric ranking**: dynamically ranks metrics by mean absolute delta instead of using a hardcoded set.
- **`LOW_DATAFLOW_SLICE_RATIO` false alarm**: suppressed when `slice_access_mode_coverage ≥ 50%` (denominator growth was inflating the metric).
- **Tier slice byte-identical artifacts**: tier slices use explicit non-test entry instead of `--entry auto --exclude-tests`, which eliminated all entries in test-dominated repos.
- **`cross_language_io_pct` false WARN**: gated on FFI bridge edges; no longer fires on HTTP-connected polyglot repos.

#### CI debug

- **Null statuses on freshly-pushed PR head**: `ci-debug` crashed when `commits/{sha}/status` returned `"statuses": null`. Fix: treat null and missing the same way.
- **Job log fetch 404s on Codeberg**: `fetch_job_log()` now selects log path by Forgejo version (`/logs` vs `/attempt/1/logs`).

#### Hooks

- **Stop hook hash recording throttle**: 150-second pause between hash recordings prevents the circuit breaker from tripping during background sub-agent waits.

#### Other

- **`loop-toggle` accepts uppercase mode arguments**: case-insensitive dispatch via `${var,,}`.
- **Flaky auto-run tests**: stale cache state from prior sessions could short-circuit the auto-run check. Fix: autouse `isolate_hypergumbo_cache` fixture redirects `XDG_CACHE_HOME` per test.

### Documentation

- **ADR-0006 augmented with Return-Type Registry Pre-Pass**: adds source 5 ("return-type chaining via global registry") to §"Type Inference Sources" with rollout plan.
- **Stash safety rule for `.ci/affected-tests.txt`**: added to AGENTS.md and smart-test playbook. Reset the file before `git stash pop` to avoid merge conflicts.
- **Bakeoff iteration vs. new session clarification**: artifacts guide explains session/cohort/iteration nesting and the `cycle` vs `init` rule.
- **Dogfooding playbook IR class names corrected**: `IRNode`/`IREdge` → `Symbol`, `Edge`, `Span`, `AnalysisRun`.

## [2.5.1] - 2026-04-05

## [2.5.0] - 2026-04-04

### Added

#### Go qualified-type parameter tracking

- **Qualified type propagation**: Function parameters and struct fields with package-qualified types (e.g. `client *http.Client`) now carry full module hints through to unresolved edges and field chain access. IO boundary detection can now classify `http.Client.Do()` as `net_send` and chained patterns like `n.client.Do(req)` — previously blocked by `ambiguous_names` guard due to missing module context.
- **Interface dispatch narrowing**: `var n Notifier = &DiscordNotifier{}` now tracks the concrete type, eliminating spurious `dispatches_to` edges.

#### Taint-flow analysis (ADR-0017)

- **Structural propagation** (Phase 1): YAML-driven taint catalogs (crypto, key material, fs writes, network sends) for Python, Rust, TS, Go, Java. Call-graph BFS with sanitizer checking. `verify-claims` supports `taint_flow` constraints.
- **Intraprocedural dataflow** (Phase 2): Language-parameterized CFG builder (Rust `?`, Python `with`, Go `defer`). Reaching-def solver with worklist fixpoint. Def/use extractors for Python, Rust, TypeScript. DDG-backed propagation upgrades taint findings from `approximate` to `precise`. Budget-capped target selection (500 functions).
- **Interprocedural propagation** (Phases 3-5): Function summary inference and YAML-declared summaries (TS 12, Rust 11 built-in). Cross-language propagation through 12 linker edge types. Field-sensitivity: `x` tainted → `x.field`/`x[key]` tainted.

#### I/O boundary catalogs

- **Objective-C** (`objc.yaml`): 90+ Foundation/Cocoa primitives (filesystem, networking, Core Data, subprocess, IPC).
- **Scala** (`scala.yaml`): scala.io, cats-effect, ZIO, sttp/http4s/akka-http, fs2, Slick/Doobie/Quill. Inherits Java catalog.
- **Haskell** (`haskell.yaml`): Prelude, System.IO, Network.Socket, System.Process, Data.IORef, Control.Concurrent.
- **Swift** (`swift.yaml`): Foundation IO catalog (FileManager, URLSession, Process, NotificationCenter). 14 server-side primitives (AsyncHTTPClient, NIOSSL, distributed tracing). SwiftNIO channel/file I/O (`NonBlockingFileIO`, `Channel`, `ChannelHandlerContext`). 7 swift-log Logger level methods. Ambiguous names for generic identifiers (write, read, Data, URL).

#### FFI unresolved edges for IO tracing

- **Ruby FFI**: `attach_function` to external libraries emits `ruby:C_ffi:0-0:<name>:unresolved` edges, redirected to C catalog for IO tagging.
- **Python FFI**: `ctypes.CDLL(None)` and `ffi.dlopen(None)` emit `python:C_stdlib:0-0:<name>:unresolved` edges. Repo-local C symbols still produce resolved edges when available.

#### Dataflow access mode patterns

- **Rust** (`rust.yaml`): 44 method-name heuristics (write/read/delete). Previously all Rust call edges had no access_mode.
- **Go** (`go.yaml`): 30 regex patterns (15 write, 15 read) for mutating method calls.
- **Erlang**: Name-based heuristics (get_*/set_*, ETS/Mnesia ops, gen_server call/cast).

#### `io-boundaries` CLI

- Enriched text output: per-primitive counts, call-site locations, entry-point traces, high-risk highlighting.
- New flags: `--by-file`, `--boundary TYPE`, `--primitive NAME`, `--exclude-tests`.
- Enriched JSON: `chains`, `primitive_counts`, `has_high_risk` (backward-compatible).

#### Language analyzers

- **Swift**: Computed property/subscript extraction. Vapor/Hummingbird route extraction (kind="route").
- **Objective-C**: Cocoa/UIKit lifecycle patterns (`cocoa.yaml`). Method `parent_base_classes` propagation.
- **Scala**: Play Framework routes parser. IOApp/ZIOAppDefault/Scalatra entrypoint detection.
- **Haskell**: Typeclass instance `implements` edges. Dataflow access_mode patterns.
- **Erlang**: `gen_server:call/cast` dispatch linking.
- **Go**: Cobra `AddCommand()` command tree detection.

#### Framework & entrypoint detection

- Hummingbird added to Swift framework list.
- SwiftUI App, UIApplicationDelegate/NSApplicationDelegate, UIViewController/NSViewController, ParsableCommand (Swift Argument Parser), and XCTestCase entrypoint patterns (`swiftui.yaml`).
- Hummingbird route/middleware/application patterns (`hummingbird.yaml`).
- Middleware concept (59+ YAML patterns) now mapped to `middleware_handler` entrypoints.
- Haskell `main :: IO ()` and Erlang `main/0`/`start/0` entrypoints.

#### Tier classification

- Swift `.build/` → tier 4. DocC `.docc/` → tier 2 (was tier 1; fixes 33% inflation in TCA).

#### Rust def/use extractor enhancements

- Borrow alias tracking: `let y = &mut x` records `x` as a use of `y`.
- `ref`/`ref mut` patterns in match arms now bind variables correctly.
- Dereference assignments (`*ptr = val`) generate defines for `ptr`.

#### Transcript sync and local model pipeline (ADR-0018)

- **Vendor-agnostic transcript sync**: Background watcher mirrors session transcripts to `.agent/.current_session_transcript.jsonl` (~83% noise filtered). Supports Claude Code, Codex CLI, Gemini CLI, Cursor.
- **LLM-driven playbook injection**: Two-model sparse-selection pipeline rates playbook relevance and injects high-scoring ones into conversation context. Compaction-aware dedup with token-distance window. 14 playbooks extracted from AGENTS.md.
- **G-Vendi finetuning pipeline** (`scripts/finetune-transcript-model`): Diversity-guided data selection (arXiv:2505.20161) for local Qwen2.5-0.5B-Instruct model. Parse-outcome sidecar log for tracking failures.

#### Autonomous mode management

- **Session-start hook**: Prompts for BROAD/DEEP/OFF mode selection when autonomous mode is OFF or has a stale PID. Vendor-agnostic with thin adapters per AI tool.
- **Session-end hook**: Disables autonomous mode (`loop-toggle off`) when the user ends their session. Shared logic in `_shared/session_end_logic.sh`.
- **Circuit breaker reset**: `loop-toggle` now deduplicates the last hash in the stop-hook hash file when activating a mode, preventing stale state from auto-approving stops.

#### CI resilience

- **Stale-pending detection in auto-pr**: `poll_ci()` detects when all CI jobs remain pending after 5 minutes (exit code 3). Auto-pr closes the PR, waits with exponential backoff (2/4/8/16 min), and repushes. Up to 4 retries.
- **Stale-pending detection in tracker sync**: Same mitigation applied to `_poll_ci`/`do_sync` — 90-second timeout, close/wait/repush with up to 2 retries.
- **Tracker sync PR verification**: Stop hook's stale-PR audit calls `verify-tracker-pr` to check safety before recommending close.

#### Reverse slice seed selection

- **Library export boost** (1.4×): `library_export`-tagged symbols in the entrypoints section are boosted in rslice seed scoring. Ensures reverse slices answer "who calls this library's public API?".
- **Architectural concept boost** (1.3×): Middleware, controller, application, and model symbols boosted over pure hub nodes (OutputBuffer.append, Iterator.next).

#### I/O boundary catalog additions

- **C**: `fclose`, `fflush`, `fseek`, `rewind`, `ungetc`, `ftell` (stdio lifecycle). `tmpfile`, `tmpnam`, `mkstemp`, `mkdtemp`, `mkostemp`, `mkstemps` (temp files).
- **Go**: `http.Transport.RoundTrip` (net_send). `golang.org/x/sys/execabs.Command` (subprocess). `testing.T.TempDir`/`testing.B.TempDir` (fs_write). `log`/`log/slog` families (ipc_send). `crypto/tls` Dial/Client and `net/smtp` NewClient/Dial/SendMail (net_send). Removes 6 false positives (`bytes.Buffer.WriteString`, `strings.Builder.WriteString`, `kingpin.Command()`).

#### Other

- `sketch --require-section`: force specific sections into output regardless of token budget.

### Fixed

#### FFI IO boundary tracing

- All 6 FFI linkers (cgo, JNI, PyFFI, N-API, Lua FFI, Ruby FFI) now annotate bridge edges with `access_mode=write, dest_access_mode=read`. Validated: chai2010/cgo 0→38 annotated edges.
- `cgo_bridge` and `ffi_bridge` added to IO boundary tag and trace sets. IO chains now cross Go→C and Python→Rust boundaries (go-sqlite3: 116 edges, polars: 5,617 edges previously had zero IO metadata).
- FFI catalog redirect: `go:C:` pseudo-namespace from cgo redirected to C catalog. Validated: chai2010/cgo 0→7 IO edges.

#### Dataflow slice quality

- **Position-aware access_mode**: Tree-sitter child field names distinguish LHS (write) from RHS (read) in assignments. Python AST reclassifies call edges on assignment lines as "read". `returns` YAML section now loaded (was silently dropped). Net effect: dataflow slices are tighter than structural slices — forward follows write/mutate, reverse follows read.

#### Java annotation and route fixes

- JAX-RS `@Path(value="/foo")` kwargs extraction (was only checking positional args). Same fix for Micronaut. Generic return type extraction (`Response<User>` → `Response`) for subresource locator detection.
- Empty route paths normalized to `"/"` in stable IDs and materialized symbols.
- `in`, `out`, `err` added to Java `ambiguous_names` (20 false positives in keycloak from JPA `CriteriaBuilder.in()`).

#### I/O boundary detection

- **Ambiguous name filtering for 10 catalogs**: Go, Rust, Python, Java, C, JavaScript, Erlang, Haskell, Objective-C. Measured: polars net_send 285→89 (69% reduction). JavaScript `remove`/`rename` added (8 false `fs_write` chains eliminated in keycloak).
- Case-insensitive module matching. ObjC catalog key bridging. Scala fs2/akka ops reclassified from `net_recv` to `fs_read`/`fs_write`. Haskell `external` sentinel for short-name fallback.

#### Symbol resolution

- ObjC selectors include colons (`removeItemAtPath:error:`). Callee extraction handles colon-containing names.
- ObjC `protocol` symbols indexed for `implements` edges in inheritance linker.
- Short-name confidence penalties (single-letter 0.15×, two-letter 0.50×) for Scala and Haskell.
- Scala: 30+ collection/FP names added to `ambiguous_names` blocklist.

#### Swift

- Methods registered by qualified name only (`Type.method`), preventing false call edges from same-name methods.
- ERROR node recovery for declarations broken by preprocessor directives or `_$` identifiers.
- Receiver type tracking from property declarations. Navigation call target walks to method, not receiver.

#### Haskell & Erlang

- Where-clause/let bindings no longer extracted as top-level symbols (fixes 24-31% orphan rate).
- Erlang function clauses with same name/arity coalesced (fixes 47-64% orphan rate).

#### Python

- Unresolved method calls emit `unresolved_variable_method_call` edges (0.40 confidence) instead of being dropped.

#### C dataflow

- `returns` section added to C dataflow YAML (was missing — Go, Java, C++, Rust, Python, TypeScript all had it). Return statement edges now get `access_mode="read"`.

#### auto-pr & tracker sync

- **Gate timing race**: `PR_PENDING` gate now created before push (was after), closing a window where tracker sync could advance dev mid-flight. Added re-check before push and proactive fetch+rebase after CI poll.
- **Variable name bug**: `$PUSH_REMOTE` (undefined uppercase) → `$push_remote`; hardcoded `"dev"` → `$BASE_BRANCH` in hung-run retry.
- **Tracker `pending_sync_lines` failover**: Now checks `.git/CI_FAILOVER_ACTIVE` and prefers `selfh/dev` as diff base. Previously all ops synced via selfh showed as "pending" relative to stale `origin/dev` (e.g., 435 lines when true delta was near zero).

#### CI & release scripts

- **CI rootdir pinning**: Added `--rootdir=.` to CI pytest invocations. When all manifest tests belong to one package, pytest selected the package subdirectory as rootdir, breaking repo-root-relative paths (0 items collected).
- **ci-debug SIGPIPE**: `_find_job_from_log_probe` used `curl | head -1` under `set -o pipefail`, sending SIGPIPE to curl (exit 141). Now uses `curl -r 0-1023` (HTTP range request) instead of piping.
- **ci-debug Forgejo API fallback**: `/actions/runs` endpoint doesn't exist on Forgejo 11.x. Falls back to `/actions/tasks` to discover run numbers, then probes job logs. Transparent to Codeberg (tries `/runs` first).
- **ci-debug ops-exclusion failover**: Fetches `selfh/dev` during failover so ops-exclusion diff matches CI's base SHA.
- **Empty manifest for docs+CI-only PRs**: Generates empty targeted manifest when no Python source files changed.
- **Release: smart-test version handling**: Version-only `__init__.py` diffs now generate targeted manifests (one test per package) instead of falling back to full-suite.
- **Release: branch creation order**: `prepare-release` creates feature branch before committing (was after).
- **Release: tracker flush**: Flushes pending tracker ops before clean-tree check.
- **`requests` upgraded to 2.33.0** for CVE-2026-25645.

#### Hooks

- **Stale-PR audit failover**: Now respects `CI_FAILOVER_ACTIVE`, querying selfh instead of origin.
- **Circuit breaker**: Fixed TOCTOU race (two `tail` reads) and off-by-one (current stop counted toward threshold). Mechanically runs `loop-toggle off` on trip.
- **Pre-push failover verification**: Blocks pushes to origin during failover.

#### Bakeoff threshold tuning

- `io_tag_rate`: Log-linear scaling from 500 nodes (was 10K), `warn_min` 0.1%. `dataflow_slice_ratio`: Skipped when `access_mode_coverage < 30%`. `limit_hit_frequency`: Log-linear boost for 35K+ node repos.
- `tier1_pct`: 100% for single-language library repos. `cross_language_io_pct`: Per-chain source vs catalog language (FFI repos now detected correctly). Polyglot threshold: <5% secondary language ignored.

#### Transcript pipeline (ADR-0018)

- Session state now cleared on session start with session-token self-healing. Poll race condition fixed (state marker written after hook succeeds). Goal injection removed (wasted tokens, risked bias).
- Rating parser: greedy fallback regex removed. Hook wiring gaps fixed for Cursor and Codex CLI. Transcript window reduced from 16K to 8K tokens.

### Changed

- **Bakeoff script rename**: `bakeoff` → `bakeoff-broad`, `bakeoff-features` → `bakeoff-deep`, `bakeoff-reflect` → `bakeoff-broad-reflect`, `bakeoff-features-reflect` → `bakeoff-deep-reflect`. All references updated across AGENTS.md, ADRs, hooks, and scripts.
- **Cooldown prompt restructure**: Process Retrospective promoted to Section 1. Gates discouraging analysis/tooling work during CI waits removed.
- **State file fallbacks removed**: `last_stop_check.json` uses primary location only (`~/hypergumbo_lab_notebook/guidance_log/`). Legacy paths no longer checked.

### Documentation

- **ADR-0017** (Taint-Zone Dataflow Analysis): Proposed and accepted. Python-first extractor ordering.
- **Governance docs**: Autonomous mode management, circuit breaker, and ADR index (`docs/adr/README.md`).
- **Deprecated invariant ledger removed**: Superseded by structured tracker (ADR-0013). References updated.
- **Agent playbooks**: Changelog audit playbook, playbook creation guide, bakeoff artifact guide added.
- **Spec updates**: ADR-0017 taint_flow constraint in §3 verify-claims. Dataflow non-goal narrowed in §2. ADR-0014/0015 synced with implementation state.

## [2.4.0] - 2026-03-21

### Added

#### I/O boundary analysis (ADR-0016)

- **`hypergumbo io-boundaries`**: Identifies call edges reaching I/O primitives (filesystem, network, subprocess, environment) and groups by boundary type. YAML-based catalogs for 10 languages (Python, Rust, JS/TS, Go, C/C++, Java + Kotlin/Scala/Groovy via alias). 60+ framework entries across Netty, Tokio, Express, Flask, and others. Module-qualified matching prevents false positives (e.g., `crypto/rand.Read` no longer matches `net.Conn.Read`).
- **Entry-point reverse tracing**: IO boundary map traces backward from each IO edge through the call graph to find which entrypoints reach each IO call. Follows FFI bridge edges (JNI, NAPI, PyFFI, WASM, gRPC) across language boundaries.
- **`hypergumbo verify-claims`**: Verifies security claims (`must_not_exist`, `max_chains`) against the IO boundary map. YAML input, `--json` output; exit code 1 on violations.

#### Cross-language linkers

- **React Router v6.4+ loader/action linking**, **Electron contextBridge exposure**, **React.lazy() route detection**
- **Yjs sub-document accessors**, **BlockSuite document model linker** (CRDT edges)
- **Crypto-flow linker**: Traces encryption/decryption boundaries across WebCrypto and Rust crypto
- **Message dispatch linker**: Typed wire protocol matching (JS/TS discriminated unions, Rust serde variants)
- **gRPC CSI-style linking**, **Dynamic WASM loading**, **Annotation convention** (`@hg:route`, `@hg:dispatches`)

#### Dataflow (ADR-0015)

- **Expanded dataflow patterns**: Go, Python, Rust, Java, C++ now have 8-12 patterns each (range loops, returns, yields, context managers, match arms). Python ast-module analyzer also expanded.

#### Language analyzers

- **Unresolved-external call edges**: All 30+ analyzers with call resolution now emit `unresolved_external_call` edges for stdlib/third-party calls via shared `make_unresolved_edge()` utility. Previously most analyzers silently discarded these, breaking IO boundary detection for C/Java repos.
- **Go interface dispatch**: Ambiguous method calls resolve to interface method candidates instead of remaining unresolved.
- **C designated initializer function pointers**: `.callback = my_handler` patterns create call edges.
- **Web Audio API framework patterns**

#### Symbol identity (ADR-0014)

- **stable_id**: Hash-based content-addressable identity for C, C++, Ruby, Bash, Perl, PowerShell, Lua, Objective-C, SQL. **shape_id**: Structural fingerprint for Java, Go, JS/TS, Kotlin, PHP and 8 additional analyzers.

#### Analysis core

- **Edge-type-weighted centrality**: Per-type weights for 19 edge types (calls=1.0, imports=0.3, structural=0.1)
- **Runtime memory pressure guard**: Monitors RSS, skips analyzers before OOM
- **Dataflow annotation line index**: ~47% faster Java analysis

### Changed

- **Weighted import inclusion in ranking**: Import edges now included at reduced weight (0.3) instead of excluded entirely. Widely-imported core types rise in rankings while call edges still dominate.
- **Tier classification**: Vendored directories (`third-party/`, `thirdparty/`, `external/`, `deps/`) → tier 3. Workspace package non-test files → tier 1 (was tier 2; fixes deno 3.5% → 89% tier 1).
- **Entrypoint ranking**: Library exports with high in-degree receive confidence boost (+0.35 cap). `microbench/` directories demoted as utility code. C/C++ symbols in `include/` detected as library exports.

### Fixed

- **IO boundary false positives**: Module-qualified matching checks edge module context against catalog entries
- **PyO3 linker**: Matches `#[pyo3(...)]` crate-name annotations; strips `Py` prefix for Python-style name matching (`PyTokenizer::encode` → `Tokenizer.encode`)
- **Dataflow call-edge annotations**: Removed incorrect `calls` section from all 19 dataflow pattern files (was causing forward slices to skip call chains)
- **Test-edge filter**: Phantom source symbol import edges no longer leak through to inflate centrality
- **`rank_files()` consistency**: Now uses same centrality parameters as `rank_symbols()`
- **Erlang local call resolution**: Intra-module calls without explicit module qualification now resolved

### Performance

- **Java symbol import resolution**: O(n*m) → indexed O(1) lookup, ~10x faster on large repos
- **Python global symbol resolution**: O(n) → (path, name) index for O(1) lookup

## [2.3.0] - 2026-03-16

### Added

- **Dataflow access modes (ADR-0015)**: Edges carry optional `access_mode` (`read`/`write`/`mutate`/`delete`), `dest_access_mode`, and `channel` metadata. YAML-driven annotation for 9 languages plus 65 tree-sitter analyzers. `slice --dataflow` follows write→read dependencies.
- **Yjs/CRDT linker**: `crdt_publishes` edges between Yjs writers and observers, plus awareness API.
- **Annotation convention linker**: `@hg:publishes`/`@hg:subscribes` comment annotations create cross-language pub/sub edges.
- **Tauri IPC event linker**: `ipc_event` edges from Rust `window.emit()` to TS `listen()`/`once()`.
- **React Router v6.4+**: `createBrowserRouter` object-based route configs with nested children, `loader_ref`, `action_ref`, and `lazy_import` metadata.
- **Shared file index**: Single `os.walk()` replaces ~80 redundant `rglob()` calls per run (~75% of uncached runtime eliminated).
- **Embedding model cache**: Singleton avoids 2 redundant model loads per run (~9% faster).
- **smart-test ETA**: Estimates wall-clock duration from test timing history before the run starts.
- **Test timing leaderboard**: `scripts/test-leaderboard` tracks per-test durations with rolling windows.

### Fixed

- **`slice --dataflow` reverse mode**: Correctly follows read edges instead of write edges.
- **Solidity ABI linker**: Qualified function names now also indexed by unqualified name.
- **Entrypoint diversity cap**: No single `EntrypointKind` can take more than 40% of slots.

## [2.2.1] - 2026-03-15

### Added

#### Language analyzers

- **Jupyter** (`.ipynb`): Extracts Python symbols and call edges from notebook code cells. Strips IPython magics/shell commands, tracks cross-cell line offsets.
- **Blade** (`.blade.php`), **Gnuplot** (`.gnuplot`, `.gp`, `.plt`), **Handlebars** (`.hbs`), **Just** (`justfile`), **Mermaid** (`.mmd`), **QML** (`.qml`): New regex-based analyzers for templates, build files, diagrams, and Qt components.

### Fixed

- **`slice --files` crash** when `--max-hops` not passed (`int < None` TypeError). Broken since 2.2.0 — caused `smart-test` to silently fall back to full test suite on every run.
- **`dev-install`** now calls `install-hooks` automatically (was a separate manual step).

## [2.2.0] - 2026-03-12

### Added

#### Cross-language linkers

- **Solidity ABI bridge**: `abi_call` edges between TS/JS contract calls (ethers.js, viem) and Solidity function definitions.
- **Tauri IPC**: `ipc_calls` edges between TS/JS `invoke()` calls and Rust `#[tauri::command]` functions. Handles rename overrides, tauri-specta bindings, and plugin patterns.
- **wasm_bindgen**: `wasm_bridge` edges between JS/TS wasm-pack imports and Rust `#[wasm_bindgen]` exports. Handles `js_name` renames and aliased imports.
- **Electron IPC expansion**: Detects `sendSync`, `handleOnce`, `webContents.send` (main-to-renderer), and `ipcRenderer.on`/`once` (renderer-side).
- **React component**: `renders_component` edges from JSX usage (`<Button />`) to component definitions.
- **Decorator dispatch**: `dispatches_to` edges from registry-based dispatch sites to registered handlers, enabling forward slices through plugin patterns.
- **Middleware chain**: `middleware_chain` edges between consecutive middleware symbols. Works with all 58 framework patterns that tag `concept: middleware`.

#### gRPC

- **Proto RPC route detection**: Proto RPC methods produce `kind="route"` symbols using HTTP/2 wire paths, visible in `routes.txt`.
- **Proto-to-Go implementation linkage**: Go methods embedding `UnimplementedXxxServer` are linked to proto RPC routes via `implements_rpc` edges. Also supports ttrpc `RegisterXxxService` patterns.
- **Server-to-service bridge**: `dispatches_to` edges connect server/servicer symbols to proto service definitions. Forward slices now traverse: stub → server → service → route → handler.

#### Route detection

- **React Router JSX**: `<Route path="..." element={<X />} />` produces route symbols with metadata.
- **Go**: Anonymous closure handlers. String concatenation paths (`baseUrl + "/users"`). Variable-based router group prefixes (Gin/Echo/Fiber). Go 1.22+ `http.ServeMux` combined method-path patterns.
- **Python**: Constant propagation for Django `path()`/Flask `add_url_rule()` with string concatenation and cross-file constant references. FastAPI `APIRouter` prefix composition. Flask-RESTful `add_resource()`.
- **Rails**: Inline `on: :member`/`on: :collection` routes. `only:`/`except:` action filters for `resources`.
- **Stapler**: Convention-based `doXxx` → POST, `getXxx` → GET for Jenkins handlers.

#### Language analyzers

- **Rust**: `implements` edges, turbofish/fully-qualified call resolution, generic trait method blocklist, `#[cfg(test)]` module inheritance, unresolved trait impl edges, `Self::method()` resolution, async spawn detection, macro body call detection, module-qualified call resolution.
- **Solidity**: Call graph with inheritance, override, and emit edges. Visibility modifiers. `using Library for Type` resolution.
- **Elixir**: `@behaviour` directive detection, WebSock callbacks, guard clause function extraction, pipe operator call edges, stdlib function exclusion.
- **Go**: Structural interface matching (no explicit assertions needed), interface method symbols, chained field access resolution via `class_field_types` registry, constructor return type inference (`NewXxx()` → `*Xxx`).
- **TypeScript**: Type reference edges (`type_ref`) from type aliases and interfaces. Abstract class support.
- **Java**: Inherited method/field resolution via extends chain. Inferred concrete return type for `Object`-returning methods. Annotation positional argument extraction (constants, concatenation).
- **C++/C#**: Chained field type resolution (`this->field->method()`) via `class_field_types` registry.
- **Circom**: New tree-sitter analyzer for `.circom` zero-knowledge circuits.
- **Formal methods**: Reference edges in Agda and Lean. Library export detection for Lean, Agda, and Wolfram.
- **Ansible**: Include/import edges resolve to file-level node IDs via basename and role name lookup.

#### Entrypoints and build targets

- **Build target linker**: Connects manifest-declared build targets to entry functions across 15 ecosystems (Cargo, npm, pyproject.toml, Maven, Gradle, C#, Dart, Swift, Haskell, Elixir, Ruby, Scala, OCaml, Zig, Nim).
- **package.json `exports`**: Subpath exports produce `export_entry` symbols and `defines_target` edges.
- **React SPA bootstrap**: `createRoot()`, `ReactDOM.render()`, etc. produce `SPA_BOOTSTRAP` entrypoints.
- **Electron main process**: `app.whenReady()` and `app.on('ready')` produce `ELECTRON_MAIN` entrypoints.
- **Top-level call attribution**: JS/TS, Bash, PHP, Perl, PowerShell now attribute module-level calls to a `<module:filename>` symbol.
- **CDI scope-annotated DI binding**: Java classes with `@ApplicationScoped` etc. that implement an interface produce explicit DI binding edges (0.85 confidence).

#### Framework patterns

- **MCP**: 8 TypeScript + 10 Python patterns for tool/resource/prompt registration.
- **Solid.js**: 12 patterns (reactive primitives, stores, context, lifecycle, bootstrap).
- **Lit**: `@customElement`, `@property`/`@state`, `@query`/`@queryAll`/`@queryAsync`, lifecycle hooks.
- **NestJS/TypeGraphQL**: `@Resolver` + `@Query`/`@Mutation`/`@Subscription`/`@ResolveField`. `@Module` providers/controllers.
- **Jakarta CDI `@Produces`**: Producer methods for interface-to-implementation resolution.

#### CLI and output

- **`--max-file-bytes`**: Skips oversized files. Recorded in `limits.truncated_files[]`.
- **`--locale`**: Detects translated doc directories (GitLab/FastAPI conventions). Excludes translations by default.
- **`--group-by-module` (slice)**: Groups inline slice nodes by file path with cross-file edge summary.
- **Sketch harmonic budget**: `--with-source` uses harmonic weighting for proportionally deeper top-ranked files.
- **Parallel execution**: Analyzers run concurrently; same-priority linkers run in parallel.
- **Adaptive slice hop limit**: `--max-hops` default scales with graph size (10 for small graphs, 3 for large).

### Fixed

#### Slicing and graph traversal

- **Forward slice traverses `dispatches_to`**: Slices follow interface methods to concrete implementations instead of dead-ending.
- **Reverse slice ignores `contains`**: No longer follows `contains` edges up to parent classes, eliminating false positives.
- **Event-driven traversal**: `event_subscribes` edges enable forward slices through publisher → subscriber → handler chains.
- **Hub pruning exempts dispatch edges**: `dispatches_to` edges always followed even when `calls` edges are hub-pruned.
- **Pass-through node filtering**: Synthetic IPC event nodes traversed during BFS but excluded from slice output.
- **Linker pipeline accumulation**: Earlier linkers' output now visible to later linkers, unblocking `dispatches_to` creation from linker-produced inheritance edges.
- **Slice `node_tiers`**: Supply chain tier propagated into slice output for tier-based filtering.

#### Cross-language IPC/WASM

- **Synthetic source nodes**: Tauri IPC and wasm_bindgen linkers create Symbol nodes for edge sources, fixing reverse slice traversal through bridges.
- **Tauri specta wrappers**: Both standalone function exports and object-method wrappers (`export const commands = { ... }`) create `caller_invokes` edges from import sites.
- **Electron contextBridge**: `contextBridge.exposeInMainWorld()` preload patterns resolved, creating `bridge_invokes` edges from renderer calls through to main process handlers.

#### Route handler linking

- **Symbol ID resolution**: Routes with full symbol ID `handler_ref` resolve directly instead of failing name-based lookup.
- **JSX component linking**: `<Route element={<Users />} />` links to `class`/`module_file` symbols. Tries React naming suffixes on mismatch.
- **Route deduplication**: Concepts deduplicated across matching phases. Dedup key scoped to (method, path, file) — different files preserved.
- **Go-swagger handler wiring**: Resolves to implementation methods instead of constructors.
- **JAX-RS `@Path` combination**: Class + method `@Path` composed (e.g., `/users/{id}`).
- **Phoenix LiveView**: LIVE routes resolve to LiveView module by name suffix.
- **False positive suppression**: NestJS `app.get(Service)` DI lookups, Go single-arg `.Get()` on caches/headers, and ambiguous SPA bootstrap names (Solid/Svelte/Vue prioritized over React).

#### Ranking and centrality

- **Confidence-based edge filtering**: Rankings exclude edges below 0.5 confidence. Ambiguous resolution scales as `0.70/sqrt(N)`. `dispatches_to` scales as `0.85/sqrt(N)`.
- **Cross-file degree weighting**: Within-file edges contribute 0.3× to in-degree. Per-file cap of 5 edges per target.
- **Dampening**: Utility/helper files (×0.1), FP primitives (`map`/`filter`/`reduce`/etc.), assertion/panic/exit builtins, leaf UI components (`Button`/`Icon`/`Modal`/etc.), pure sinks (out_degree=0, relaxed to 20 LOC), and sibling implementations (6+ same-name methods: top 3 keep full weight, rest ×0.15).
- **Entrypoint selection**: `--entry auto` boosts `MAIN_FUNCTION`/`CLI_MAIN` 2× over route handlers. Connectivity boost skipped for test entrypoints. Telemetry/logging exports excluded from boost. Adaptive seed budget (max_symbols/3) reduces disconnected singletons.

#### Symbol resolution

- **Test-path preference**: Non-test callers prefer production candidates in suffix matching.
- **Method blocklists**: JS/TS (60+ built-ins), Rust (logging + `output`/`status`/`spawn`), C++ (35 STL methods).
- **C++ class qualification**: Inline methods get qualified names (`Parser::Initialize`), with key-based `path_hint` matching.
- **Go local variable exclusion**: Scoped variables tracked and excluded from function reference matching.
- **Java nested class guard**: `new Properties()` no longer resolves to `Log4jConfiguration.Properties` from other files.
- **Elixir import-gated resolution**: Cross-module bare calls require explicit `import` directive.
- **Binary `.ts` skip**: MPEG Transport Stream files (null bytes in first 8KB) skipped.

#### Classification and output

- **Tiered view boundary exclusion**: Compact views exclude external_symbol/tier=3 nodes.
- **Test/utility classification**: `fv/`, `harnesses/` as test dirs; `build.rs` as utility; `bench/`/`benches/` excluded from production slices. `dev/`/`utils/` only match at project root, not inside source roots.
- **Codegen classification**: `.serde.rs`, `.pb.go`, `_pb2.py` as derived (tier 4).
- **Path normalization**: All symbol paths normalized to relative, fixing tier misclassification across 8 languages.
- **TOML symbol IDs**: Location-based format instead of sha256 hashes.
- **JSON reproducibility**: Sorted keys in all JSON output.
- **ASM register filtering**: CPU register names no longer create false external call edges.
- **Annotation-aware test exclusion**: `is_test_node()` checks `#[cfg(test)]`, `@Test`, `[Fact]` annotations, not just file paths.
- **Lean import resolution**: Intra-repo imports resolve to file node IDs instead of dangling module IDs.

### Changed

- Migrated all `Edge()` constructor calls to `Edge.create()` for consistent edge_key generation.

## [2.1.0] - 2026-03-01

### Added

#### Linkers

- **DI resolution linker**: Creates `di_resolves` edges from interface methods to DI-bound implementations. Supports Guice, Spring `@Bean`, ASP.NET Core DI, NestJS/Angular, InversifyJS, Python injector, Kotlin Koin, and Java SPI with heuristic fallbacks. Edges are followed by forward BFS — correct for DI-heavy codebases.
- **HTTP linker: Ruby, Java, AngularJS, jQuery clients**: Detects HTTP client calls in Ruby (RestClient, HTTParty, Faraday, Net::HTTP), Java (RestTemplate, Retrofit), AngularJS `$http`, and jQuery `$.ajax`/`$.get`/`$.post`. Creates cross-language `http_calls` edges to server route handlers.
- **JS/TS module resolution**: Resolves imports via relative paths (extension/index probing), `tsconfig`/`jsconfig`/`vite.config` path aliases, and monorepo tsconfig discovery.
- **Vue linkers**: Template-method linker connects event handlers to `<script>` symbols; component linker resolves import paths to `.vue` files.
- **FFI (5 languages)**: Cross-language call linking to C/C++ from Python (ctypes/cffi/PyO3), Ruby (FFI gem, C extensions), Go (Cgo), Node.js (N-API), and Lua (LuaJIT ffi).
- **ORM query, containment, Rails view template linkers**: Django/SQLAlchemy call-to-model linking; `contains` edges across 15 languages; convention-based controller-to-view linking (ERB, Haml, Slim, Jbuilder).

#### Frameworks

- **JAX-RS subresource locator path chaining**: Propagates `@Path` prefixes through locator chains with cycle detection.
- **Stapler (Jenkins)**: `@WebMethod`, `@RequirePOST`, `doXxx()`/`getXxx()` conventions. Auto-detected from `org.kohsuke.stapler`.
- **Google Guice + Jakarta CDI**: Guice DI annotations, `AbstractModule`, EventBus `@Subscribe`. Jakarta CDI scoping, `@Produces`, `@Interceptor`, `@Alternative`.
- **Rails**: Lifecycle/controller callbacks, Wisper pub/sub, scheduled tasks/Rack middleware entrypoints, namespace-aware route extraction.
- **Django & Flask**: Template tags/filters, signal receivers, Jinja2/Blinker/Flask-RESTful patterns.
- **Kafka Connect, XORM, FastAPI named routers, Express Controller.route()**: Streaming connector entrypoints, Go ORM detection, named `APIRouter` matching, config-object route registration.
- **Framework detection for 16 languages** (Haskell, Clojure, R, Lua, C++, Erlang, F#, Kotlin, C#, Dart, Julia, OCaml, Nim, Zig, D, Groovy). **Test framework patterns for 16 languages** (Elixir, Scala, Dart, Clojure, Haskell, Erlang, F#, Ruby, Julia, OCaml, Lua, R, Nim, Zig, D, Groovy). Main function detection for 7 more (D, Nim, Zig, V, Odin, Gleam, Haxe).
- **Test/utility file classification**: Test dirs as tier 2 with 90% penalty; `t/`, `test-*.c`, root-only `spec/` patterns. `dev/`, `contrib/`, `hack/`, `devel*` as utility. Removed `public/` from DEFAULT_EXCLUDES.

#### Analyzers

- **Clojure UsageContext**: Enables YAML-driven Ring/Compojure route detection.
- **JS/TS callback + middleware edges**: Function-as-argument `references` edges, Express `middleware_chain` edges, object literal and Ruby hash literal function references.
- **Assembly language**: Tree-sitter analyzer for `.s`/`.asm`/`.S` with cross-file call resolution.

#### Analysis core — Centrality & ranking

- **Bidirectional centrality**: `in_degree * (1 + ln(1 + out_degree))` rewards connectors over sinks. Hub in-degree saturation above 100.
- **Four dampening mechanisms**: Trivial sinks (≤1 out, ≤5 LOC), common method names (10+ symbols), utility symbols (Logger, `*Exception`, etc.), and pure sinks — all get 70–90% reduction in both `rank_symbols()` and `symbols` output.
- **Edge confidence filtering**: Edges <0.5 confidence excluded from centrality and degree computation. Import edges excluded by default. Documentation kinds and migration paths excluded/de-weighted.

#### Analysis core — Slices

- **Hub pruning depth-1 exemption**: Fixes "main → run()" patterns where orchestrators were hub-pruned.
- **`--exclude-imports` flag**: Call-graph-only slices (up to 64% noise reduction). **`--hub-threshold N`** (default 50). **Node depth tracking** in `SliceResult.node_depths`. Forward slices skip structural edges; reverse slices downweight test callers; class/interface entries auto-expand.

#### Analysis core — Entrypoints

- **Scaled cap** (base 50, max 500) with confidence threshold (0.10) and count cap (50).
- **library_export demotion**: 90% penalty when semantic entrypoints exist. Language dominance ranking for polyglot repos.
- **New detectors**: C `cmd_*` functions, Java/Kotlin/Rust library exports, C forward declaration dedup.
- **Tier classification**: Fuzz/benchmark dirs as tier 2; generated route symbols promoted to tier 2.

#### Analysis core — Call resolution

- **Go**: Module path resolution via `go.mod`, chained-call ambiguity guard, stdlib method guard (50+ methods), route handler unwrapping, route path validation, var alias extraction, struct embedding + interface assertion detection, Chi `Del()` and Go-swagger route detection.
- **Rust**: Suffix index splits on `::`, scoped calls prefer full qualified names, span-based enclosing function disambiguation.
- **C/C++**: Function pointer callback edges, dispatch table `dispatches_to`/`uses_dispatch_table` edges, declaration/definition deduplication with edge remapping.
- **Cross-language**: Unified suffix index for all separators (`.`, `::`, `#`, `\`, `:`) across 10+ languages. Ambiguous method scaling (`1/sqrt(N)`); ListNameResolver returns unresolved at threshold.

#### Analysis core — Other

- **Docstring extraction** (103/105 analyzers): First-line doc summaries in `Symbol.docstring`.
- **Typed stable_id (ADR-0014 Phase 3)**: Per-language signature normalization and typed hashing for 12 analyzers.
- **Decorator/annotation edges** (Python, TS, Java, C#, Rust), **return type tracking** (6 languages), **Go route mount detection**, **inheritance linker struct support**. Edge deduplication fixed for `None`-keyed edges.

#### Sketch, Supply chain, CLI

- **Sketch**: Exclude 9 lock files from config section. **Supply chain**: Maven multi-module workspace detection.
- **CLI**: Secret scanning via gitleaks, extras/cache management subcommands, redesigned bakeoff tooling (numeric scores, trajectory, orphan recovery, idea ingestion, artifact compression, domain-scored seed selection).

#### Documentation & Testing

- Scoped smart-test coverage, per-package checks, CI auto-retry, `ci-debug logs`.

### Changed

#### Language analyzers

- **Elixir/OTP**: GenServer dispatch, 11 behaviour callbacks, `live` routes, multi-clause edges, cross-file resolution.
- **C/C++**: Enclosing-function fix for duplicate names, definition-only struct/enum extraction. C++ adds template calls, pointer/reference returns, stack construction.
- **Go**: Function-scoped type tracking, unified ambiguity guard (all selector types), unexported method guard, builtin filter, receiver disambiguation, self-call resolution, route linking (Gin/Echo/Fiber/Chi/Gorilla), Group prefix composition, HTTP client detection, `lines_of_code`.
- **Ruby**: Class methods, `.new`→`#initialize`, namespaced receivers, job enqueue/callback/delegate/association edges, ambiguity guard, ListNameResolver.
- **Rust**: ListNameResolver with ambiguity threshold; 3+ candidates → no edge. `lines_of_code` populated.
- **JS/TS, PHP, Java, Lua, D**: Method ambiguity guards, inherited method fallback, require-alias resolution, import disambiguation improvements.

#### Algorithms & output

- **Slices**: Skip structural edges forward, downweight test callers reverse, `--exclude-tests` preserves inheritance. **Entrypoints**: Transitive scoring, connectivity fallback, test demotion, `--entry auto` filter support. **Default exclusions**: Doc/config nodes, CSS variables, npm/TS types, SCSS.
- **Output**: Tiered view overhaul (budget enforcement, connectivity-aware selection). Route improvements (`-x`, `kind=route`, Django/Rails format fixes). Symbols sorted by per-symbol degree. Derived/minified excluded by default; `--max-files` raised to 50.
- **Deps**: Embeddings optional. All deps pinned `~=X.Y.Z`.

#### CI, agent governance, internal

- smart-test improvements, infra-only PR skip, shared Forgejo API lib, parallel coverage, retry-aware `merge-pr`.
- Three-way stop hook, post-compaction recovery, pre-push hook, fail-closed tracker, fork workflow hardening.
- Standardized pass IDs via `make_pass_id()`. Generalized symbol identity (ADR-0014): location `id`, signature `stable_id`, CST `shape_id`.

### Fixed

#### JS/TS

- **Cross-package false positives**: Comprehensive guard on all edge paths (direct/namespace/method/callback/object-field/shorthand) using import disambiguation, same-package preference, and npm boundary checks. Built-in name guard (`Number`, `String`, `parseInt`, etc.). Parameter shadowing respects lexical scoping in Promises/closures. `npm_package` symbols correctly tier 3.

#### Go

- Vendored SDKs classified as tier 3. Method ambiguity threshold lowered to 2. Route handler from last non-string arg. Test functions require `_test.go` suffix. Same-package method resolution fixed.

#### Java/Kotlin/Scala

- `main()` patterns match qualified names. Import-aware class name disambiguation. Field access receiver extraction. Integration test path detection.

#### Other languages

- **Rust**: Built-in attribute guard (45 names); impl method name extraction. **Clojure**: `test-*` requires `test/` dir. **D**: `.d` file disambiguation vs GCC deps. **Rails**: Route-to-controller reverse suffix matching. **Kotlin/C#/Scala/Python**: Chained member access resolution.

#### Framework detection

- False positive guards for GraphQL (requires server packages), Dropwizard (requires `-core`/`-jersey`), handler naming (requires HTTP-context dir). Route path prefix inheritance (Spring Boot, JAX-RS, Micronaut, ASP.NET). Pattern `base_class` no longer falls through to kind-only matching. Word-boundary regex. Micronaut field fix.

#### Graph & output quality

- Tiered view budget compliance (was 177× over) with connectivity-preserving shrink. Dangling edges after tier filtering. WebSocket N×M explosion. Event symbol ID format. Supply chain tier deserialization. Route-handler linking (Rails suffix, Django view_name, Phoenix concept, Ruby hash rockets). Vue/C/C++ analyzer deduplication. Name-collision fan-out → single best match. Cross-language containment filtering. Language-proportional sketch seeding. Route symbol entrypoint promotion. Spurious TS warning. Minified file skip. smart-test scoped mode. ListNameResolver full-path disambiguation.

### Removed

- **Bootstrap mode in CI**: Stable hypergumbo includes `slice --files`, so smart-test always generates proper manifests.


## [2.0.2] - 2026-02-01

### Changed

- **Default token budget increased to 8000**: Ensures Source Files Content section has sufficient budget to include production files. Use `-t` flag to override.

### Fixed

- **Density score path normalization**: Fixed path mismatch where cached absolute paths weren't normalized to relative paths, causing files to sort arbitrarily instead of by density.

## [2.0.1] - 2026-01-31

### Added

- **`--files` flag for slice command**: Enables smart test selection by finding all files that depend on changed code. Usage: `hypergumbo slice --files changed.txt --output affected.txt`. This reads a list of changed file paths and performs reverse dependency analysis to identify affected test files. Used by `scripts/smart-test` to generate manifests for CI.

### Fixed

- **CI manifest validation**: CI now properly filters comment lines from manifests and detects bootstrap mode (when manifest indicates full suite is required due to missing stable hypergumbo).

## [2.0.0] - 2026-01-31

### Changed

- **Modular package structure (ADR-0010)**: Restructured from a single package into 5 modular packages: `hypergumbo-core` (CLI, IR, slice, sketch, linkers), `hypergumbo-lang-mainstream` (Python, JS/TS, Java, Go, Rust, etc.), `hypergumbo-lang-common` (Haskell, Elixir, GraphQL, etc.), `hypergumbo-lang-extended1` (Zig, Agda, Solidity, etc.), and `hypergumbo` (meta-package). **Breaking change:** import paths changed from `hypergumbo.*` to `hypergumbo_core.*` / `hypergumbo_lang_*.*`. CLI usage is unchanged. See `docs/MIGRATION-2.0.md`.

### Added

- **Smart test selection (ADR-0010)**: `smart-test` uses hypergumbo's reverse-slice to run only affected tests from changed files, generating `.ci/affected-tests.txt` for CI. Includes stop-the-line protocol (bypass with `fix(job-XXXXX):` title prefix).
- **Two-tier CI system**: Fast CI uses manifest-based test selection; `full-suite.yml` runs as lazy singleton after dev merges.
- **Framework pattern detection for 30+ frameworks** across 10 ecosystems. Each framework gets route, handler, middleware, and component detection via YAML patterns. See `docs/FRAMEWORKS.md` for per-framework details.
  - **Python (8):** Falcon, Quart, Sanic, Pyramid, Bottle, Litestar, Masonite, Flask-Appbuilder
  - **PHP (7):** Symfony, CodeIgniter, Lumen, CakePHP, Yii, Laminas, FuelPHP
  - **Java/JVM (3):** Quarkus, Javalin, Vert.x; plus JAX-RS aliases for Dropwizard, Jersey, RESTEasy
  - **Kotlin (1):** Http4k
  - **Scala (2):** Scalatra, http4s
  - **Node.js (5):** Nuxt, Remix, SvelteKit, Feathers.js, AdonisJS, Restify
  - **Ruby (3):** Hanami, Roda, Padrino
  - **Clojure (2):** Ring/Compojure, Pedestal
  - **Haskell (2):** Servant, Scotty
  - **Elixir (1):** Nex
- **Utility file entrypoint penalty**: Entrypoints in utility directories (docs, examples, scripts, tools, benchmarks) receive a 50% confidence penalty.
- **Test file weighting for slice ranking**: `rank_slice_nodes()` now downweights test file nodes so production code ranks higher in reverse slices.

### Fixed

- **TypeScript constructor injection resolution (INV-013)**: `this.property.method()` calls now resolve when the property is a constructor-injected dependency (e.g., NestJS `constructor(private catsService: CatsService)`). Forward slices from controllers now include service layer calls.
- **Linker duplicate edge elimination**: Edge deduplication after linkers run prevents duplicates from the event-sourcing linker (e.g., killbill: 25494 → 25022 edges).

## [1.3.1] - 2026-01-29

### Added

- **C++ test framework patterns**: Google Test (`TEST`, `TEST_F`, `TEST_P`) and Catch2 (`TEST_CASE`, `SCENARIO`) macros now detected as `test_function` concepts. Reduces orphan function count in C++ test codebases.
- **go-restful framework support**: Added patterns for the go-restful framework (used by Kubernetes). Detects `.To()` method calls as route handlers and `restful.WebService` base class. Improves framework detection for Kubernetes-style Go APIs using the fluent RouteBuilder pattern.
- **HTTP client patterns for JavaScript/TypeScript**: Added patterns to detect frontend API calls for cross-language linking. Detects fetch(), axios, ky, got, and superagent HTTP clients as `http_client` concept. Enables future route-client linker to connect frontend API calls to backend route handlers in polyglot repos.
- **JAX-RS framework detection**: Added detection for JAX-RS (`javax.ws.rs`, `jakarta.ws.rs`), Jersey, RESTEasy, and Swagger dependencies in Java projects. Enables pattern enrichment for Java REST APIs using JAX-RS annotations (`@GET`, `@POST`, `@Path`, etc.).

### Fixed

- **F# analyzer Forth file disambiguation**: The F# analyzer now detects and skips Forth files that share the `.fs` extension (Open Firmware Forth, GForth). Prevents analyzer hangs on repositories like qemu-slof that contain Forth code with `.fs` extension. Detection uses content heuristics (backslash comments, Forth keywords like `VALUE`, `CONSTANT`, `:` word definitions).

- **Ruby analyzer duplicate edge elimination**: Fixed duplicate edges being created for the same call site when an identifier was both processed as part of a `call` node and separately as a bare `identifier`. Now skips identifiers that are children of call-related nodes, reducing edge count noise by 10-30% in Ruby codebases.

- **Bakeoff GraphQL false positive**: Fixed `EXPECTED_ROUTES_BUT_FOUND_0` false positive for GraphQL frameworks (apollo-server, etc.) that don't use traditional HTTP routes. Repos with "graphql" or "apollo" in name are now excluded from route expectations.
- **Bakeoff diagnostic false positive reduction**: `NO_CALL_EDGES` now requires ≥3 function/method symbols (repos with 0-2 functions can't have meaningful call edges). `EXPECTED_ROUTES_BUT_FOUND_0` removed overly broad "web" keyword match (caught webtunnel, webpack, webrtc); now requires name keywords like "api", "server", "http", "rest" OR evidence of route edges/framework detection.

- **GraphQL entrypoint detection**: Updated GraphQL framework patterns (graphql.yaml, graphql-python.yaml, graphql-ruby.yaml) to use `graphql_resolver` and `graphql_schema` concept names, enabling proper entrypoint detection for GraphQL resolvers in JavaScript/TypeScript, Python, and Ruby codebases.

- **Duplicate edge elimination in analysis pipeline**: Added edge deduplication by ID after analyzer runs complete. Some analyzers (e.g., Ruby) could produce duplicate edges with identical IDs; these are now filtered out before writing the behavior map. Example: postal repo went from 3220 edges (114 duplicates) to 3097 unique edges.

- **Ruby analyzer method field extraction**: Fixed root cause of duplicate edges in Ruby analyzer. The code was finding the first identifier child of call nodes, which for `receiver.method` calls like `data.chop` would incorrectly identify "data" (receiver) instead of "chop" (method). Now uses tree-sitter's `child_by_field_name("method")` to correctly extract the method name.

## [1.3.0] - 2026-01-29

### Added

- **Centralized inheritance linker**: New `linkers/inheritance.py` creates `extends`/`implements` edges from `base_classes` metadata across ALL languages, eliminating duplicate edge-creation logic in individual analyzers.

### Fixed

- **Python/JS/TS inheritance edges (INV-008)**: Classes with `base_classes` metadata now create `extends` and `implements` edges to base classes/interfaces defined in the repo. This enables the type hierarchy linker to create `dispatches_to` edges for polymorphic dispatch.
- **Ruby/Kotlin inheritance edges (INV-009)**: Ruby and Kotlin analyzers now extract inheritance information.
- **Swift/C++/Objective-C/Apex base_classes extraction**: Completes META-001 (Metadata Must Become Graph Structure) at 100%. All 13 languages with class inheritance now extract `base_classes` metadata:
  - Swift: class/struct/protocol inheritance and protocol conformance
  - C++: class/struct inheritance with qualified names (std::exception)
  - Objective-C: superclass + protocol conformance
  - Apex: extends + implements clauses

## [1.2.1] - 2026-01-29

### Summary

Major expansion: **37 new analyzers** across languages, templates, config formats, and build systems. New **route-handler** and **type hierarchy** linkers improve web framework and OO codebase navigation. CLI gains `compact` subcommand. Multiple bug fixes for edge uniqueness, entrypoint detection, and crash resilience.

### Added

#### CLI
- **`compact`**: Post-process behavior maps into compact form. Options: `--input`, `--out`, `--max-symbols`, `--coverage`, `--no-connectivity`.

#### Analyzers: Frontend & templates
- **Twig**: blocks/extends/includes/macros; `extends_template` / `includes_template` edges.
- **SCSS/Sass**: variables/mixins/functions/rules; `uses_mixin` edges.
- **Svelte**: imports, slots, events, control flow; `imports_component` edges.
- **Vue SFC**: directives/slots/methods/props; two-pass import resolution.
- **Astro**: frontmatter, imports, slots, client directives; two-pass import resolution.

#### Analyzers: Programming languages (16)
- **Odin**: procedures/structs/enums/unions; imports + cross-file calls.
- **Gleam**: functions/types/aliases; visibility + signatures; imports + calls.
- **V**: functions/structs/enums/interfaces; visibility + signatures; imports + calls.
- **MATLAB**: functions/classes/methods/properties; signatures + cross-file calls.
- **Tcl/Tk**: procedures/namespaces; call edges (filters built-ins).
- **Scheme**: defs + recursive calls; filters special forms (`.scm/.ss/.sld/.sls`).
- **Racket**: defs/structs + recursive calls; `struct`/`module+` (`.rkt/.rktl/.rktd`).
- **Janet**: defs + recursive calls; filters special forms.
- **Fennel**: defs + recursive calls; compiles to Lua.
- **Pascal**: programs/units/functions/procs; case-insensitive calls (`.pas/.pp/.dpr/.lpr`).
- **Haxe**: classes/interfaces/functions; visibility/static; qualified calls.
- **PureScript**: modules/functions/types/classes/instances; qualified calls.
- **Hack**: classes/traits/functions/methods; visibility/static (`.hack/.hh`).
- **Apex**: classes/triggers/methods/fields; visibility/override; qualified calls.
- **Luau**: typed functions + types; qualified calls (`.luau/.lua`).
- **Pony**: actors/classes; reference capabilities; cross-file calls.

#### Analyzers: Data, schema & DSLs (5)
- **KDL**: nodes/sections; arguments/properties; nested hierarchies.
- **Prisma**: models/enums/datasources/generators; `@relation` edges.
- **Smithy**: services/operations/shapes; namespace-qualified names; type refs.
- **SPARQL**: PREFIX/BASE + queries; `uses_vocabulary` edges.
- **Jsonnet**: locals/methods/fields; imports + calls.

#### Analyzers: Build systems & DevOps (4)
- **Meson**: projects/targets/custom targets; deps + subdir includes.
- **BitBake**: recipe vars, inherit, tasks; DEPENDS/RDEPENDS edges.
- **Robot Framework**: keywords/tests/vars; cross-file keyword invocation.
- **Puppet**: classes/defined types/resources; parameter extraction.

#### Analyzers: Docs & config files (7)
- **BibTeX**: bibliography entries, citation keys, authors/years/titles.
- **Markdown**: headings/code blocks/links; `links_to` edges.
- **RST**: sections/directives/refs; toctree/include + cross-doc refs.
- **requirements.txt**: constraints, VCS/URL/editable; `-r/-c` includes.
- **.properties**: key/value + domain categorization; masks secrets.
- **.gitignore**: pattern classification + domain categories.
- **INI/CFG**: sections/settings + domain categorization; masks secrets.

#### Linkers (2)
- **Route-handler linker**: Creates `routes_to` edges from route symbols to handler functions. Supports Rails, Phoenix, Laravel, and Express metadata formats.
- **Type hierarchy linker**: Creates `dispatches_to` edges for polymorphic dispatch. Connects interface/parent methods to concrete implementations (valuable for DI-heavy codebases).

#### Entrypoint detection
- **Manifest-based**: `package.json "bin"`, `pyproject.toml [project.scripts]`, `Cargo.toml [[bin]]` detected with 0.99 confidence.
- **Naming-based**: Classes named `*Controller`, `*Handler`, `*Service` detected with 0.70 confidence (heuristic fallback).
- **Structural**: Python `if __name__ == "__main__"` detected with 0.85 confidence.

#### Framework route extraction
- **Rails**: `resources`/`resource` macros emit individual route symbols for all RESTful actions.
- **Phoenix**: Elixir analyzer creates route symbols with controller/action metadata.
- **Laravel**: PHP analyzer creates route symbols including `Route::resource()` expansion.

#### Quality & governance
- **Meta-invariants**: Introduced three high-level quality principles that unify specific bug fixes:
  - META-001: Metadata Must Become Graph Structure (90%) — semantic relationships in metadata must become traversable edges
  - META-002: Extraction Completeness (95%) — symbols in source code must be extracted for analysis
  - META-003: Data Integrity (100%) — graph elements must have valid, unique identifiers
- **Invariant ledger**: Tracks discovered invariants, root causes, fixes, and regression tests (`.agent/invariant-ledger.md`).

### Fixed

#### Crashes & robustness
- **JSON manifests**: No longer crash when `package.json`/`composer.json` top-level is non-object.
- **Ruby analyzer**: Prevent self-referential call edges.

#### Graph quality (INV-002 through INV-006)
- **INV-006**: Rails `resources`/`resource` macros now infer `controller_action` metadata for route-handler linking.
- **INV-005**: Edge IDs include line number, ensuring uniqueness for multiple calls to same target.
- **INV-004**: Routes get `routes_to` edges to handler functions (metadata now converted to traversable edges).
- **INV-002**: Deferred resolution for cross-file handler references (Django URL patterns, Express routes, etc.).

#### Python analyzer
- **Nested functions**: Extract decorated nested functions (FastAPI router factory pattern).
- **Main guard**: `if __name__ == "__main__"` uses correct concept format for entrypoint detection.
- **Django**: Empty path URL patterns (`path('')`) now correctly detected as routes.

#### Entrypoint detection
- **cargo_binary**: YAML pattern now matches `kind="binary"` (actual analyzer output).
- **HTTP linker**: Falls back to direct `meta.route_path`/`meta.http_method` when concept metadata unavailable.

#### Symbol resolution
- **INV-007**: Go import path resolution now correctly disambiguates when multiple files define the same symbol (e.g., generated protobuf files). `ListNameResolver` tries progressively shorter path suffixes and falls back to deterministic ordering.

## [1.1.0] - 2026-01-24

> Note: This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v1.0.0. Hopefully our next release will be hiccup-free and actually publish to PyPI.

### Removed

- **Vestigial capsule system** (ADR cleanup)
  - Removed `init` and `export-capsule` commands (marked vestigial in spec)
  - Removed `plan.py`, `llm_assist.py`, `export.py` modules
  - Removed deprecated `Pack` class from catalog (packs replaced by linker activation conditions)
  - Removed `llm-assist` and `llm-local` optional dependencies from pyproject.toml

### Added

**YAML-Driven Analysis (ADR-3aaa)**
- Main function detection via `main-functions.yaml` for 10 languages (Go, Java, Python, C, C++, Rust, C#, Kotlin, Swift, Dart)
- Test function detection via `test-frameworks.yaml` for 10+ frameworks (pytest, JUnit, RSpec, etc.)
- Language conventions: CUDA kernels, WGSL shaders, COBOL, LaTeX, Starlark (`language-conventions.yaml`)
- Config conventions: NPM, Maven, Android, Cargo, Poetry, TypeScript (`config-conventions.yaml`)
- Pattern system extended with `symbol_name`, `language`, and `prefix_from_parent` fields
- Framework pattern types added to `docs/schema.json` for YAML validation
- YAML linting via `yamllint` in pre-commit hooks
- Play Framework patterns for Scala (`play.yaml`): controllers, Action blocks, WebSocket handlers
- Akka HTTP patterns for Scala (`akka-http.yaml`): route directives, method handlers, WebSocket, auth
- Library export detection (`library-exports.yaml`): Detects exports from index files (index.ts/js/jsx/tsx) as library entry points for JS/TS libraries
- Naming conventions (`naming-conventions.yaml`): Heuristic patterns for `*Controller`, `*Handler`, `*Service` classes (0.70 confidence fallback tier)

**New Commands & Flags**
- `hypergumbo test-coverage`: Static coverage estimation via call graph analysis
- `-x/--exclude-tests`: Exclude test files from sketch sections
- `--progress`: Show ETA during sketch generation
- `--readme-debug`: Debug README extraction algorithm
- `--help --all`: Show all subcommand help at once
- `slice --flat`: Output simple `{nodes, edges}` format for external tools (implies `--inline`)

**Sketch Improvements**
- Source code included by default (`--no-source` to disable)
- "How Representative Is This Sketch?" table showing coverage per section
- README-first hybrid ranking for Additional Files (round-robin: linked/similar/central)
- Multi-format README link extraction (Markdown, Org-mode, RST, AsciiDoc)
- Embedding-based README description extraction with pre-computed probes
- Estimated coverage in Tests section (e.g., "~35% estimated coverage")
- Separate test/non-test LOC breakdown in Overview

**Analyzer Improvements**
- Shared SymbolResolver framework for cross-file resolution (45+ analyzers)
- Parameter type inference for Python, Java, Kotlin, TypeScript
- Common Lisp analyzer (`.lisp`, `.lsp`, `.cl`, `.asd`)
- LLVM IR analyzer (`.ll` files)
- ADR-0004: File taxonomy with `FileRole` enum and 75+ language specs
- ADR-0007: Import tracking for cross-file call resolution
  - Phase 1 complete: JS/TS, Kotlin bug fixes
  - Phase 2 complete: Rust, C#, Ruby, Elixir, Swift, PHP, Scala, Dart
  - Phase 3A complete: Ada, Agda, Clojure, C++, D, Elm, Erlang, F#, Fortran, Groovy, Haskell, Julia, Nim, OCaml, R, Solidity, Starlark, Zig (18 done; Lean blocked by grammar, VHDL has no aliasing)

**CLI Ergonomics**
- Auto-run analysis for query commands when no cached results exist
- Auto-discovery of cached results from `~/.cache/hypergumbo/`
- Slice path suffix matching (`--entry src/main.go` matches full paths)
- Symbol-specific slice output naming (`slice.main.json`)
- Artifact location reporting and summary after `hypergumbo run`
- Forge URL resolution for README links (GitHub/GitLab/Codeberg)

### Changed
- **auto-pr uses fast-forward merge by default**: Preserves commit bodies and DCO. Prompts to rebase if diverged. `--squash` available as emergency fallback (uses git notes).
- **Schema version 0.2.1**: Added framework pattern types to `docs/schema.json`
- Section headers renamed: "Source Content" → "Source Files Content", etc.
- Overview always shows test/non-test breakdown; Tests section always present
- Additional Files excludes boilerplate (LICENSE, .gitignore, CODEOWNERS)
- CI skips expensive jobs for docs-only PRs
- pytest-xdist for parallel tests (`pytest -n auto`)

### Fixed

**Git Notes Recovery**
- Restored 193 orphaned commit bodies via git notes (squash-merged Jan 9-22 2026). View: `git log --show-notes`

**Compact Mode**
- Edge filter changed from OR to AND (was wasting 99%+ on dangling edges)
- Entrypoints filtered to resolvable IDs (fixes "No entrypoints detected")
- Force-include entrypoints in selection (preserves semantic anchors)
- Connectivity-aware selection using greedy frontier algorithm (4x more edges)
- Entrypoints capped to `max_symbols // 2` to leave room for bridge nodes

**Sketch Output**
- File content truncation accounts for markers (~130 chars overhead); files end with newline
- `-x` flag correctly counts non-code repos
- Unified test detection between Overview and Tests sections
- Added `tests.py` and `*_spec.rb` to test detection
- Structure section: tree format with `-x`, shows all root directories, handles flat repos
- Representativeness table shows with `-x` and correct budget for small sketches
- Additional Files representativeness uses mention centrality
- Elevator pitch truncation respects sentence boundaries
- Embedding-based README extraction handles soft line breaks

**Call Graph**
- C/C++ analyzers prefer definitions over declarations (fixes coverage estimation)
- NestJS route paths combine controller + method via `prefix_from_parent`
- NestJS routes normalize to start with `/` (fixes `[GET] test` → `[GET] /test`)
- Framework aliases: Go web frameworks (gin, chi, echo, fiber) now load `go-web.yaml`; Rust web frameworks (axum, actix-web, rocket, warp) now load `rust-web.yaml`
- Python: submodule imports resolve (`from app import crud; crud.func()`)
- Python: imported class method calls resolve (`from X import Class; Class.method()`)

**Entrypoints**
- `slice --list-entries` now respects `--exclude-tests` and `--max-tier` filters

**Other**
- `explain --with-source` output ordering (callers/callees grouped with sources)
- Minimum chunk size for license files in semantic search
- Removed misleading "Coverage requires execution" message

## [1.0.0] - 2026-01-12 (not released to PyPI)

> **Note:** This version was tagged in the codebase but never published to PyPI. It marks a milestone with breaking changes relative to v0.9.1.

Major focus on memory optimization, framework detection improvements,
and completing the migration to YAML-driven semantic analysis.

### Fixed
- **Memory optimization for large repos:** Reduced peak memory from ~11GB to ~2.1GB (80%
  reduction) for repositories like tensorflow. Uses streaming JSON output and aggressive
  cleanup of intermediate data structures.
- **Android framework detection:** Now detects Android via `android {}` blocks in build.gradle,
  AndroidManifest.xml presence, and gradle plugin dependencies.
- **JSON serialization of Python literals:** Complex numbers and bytes literals no longer cause errors.
- **`--frameworks all` and explicit lists:** Now bypass dependency scanning, enabling pattern
  matching even when manifests are in subdirectories.
- **Express route detection:** Fixed case-sensitive HTTP method comparison.
- **Slice command:** Now runs all language analyzers, not just Python/HTML.

### Added
- **Recursive manifest scanning:** Scans up to 3 levels deep for dependency manifests (monorepo support).
- **Ruby/Elixir framework detection:** Gemfile and mix.exs scanning for Rails, Phoenix, etc.
- **Usage-based pattern matching:** Route detection for call-based frameworks (Django `path()`,
  Express `app.get()`, Rails route DSL, Go Gin, etc.) via YAML patterns.
- **12 new framework YAML patterns:** ktor, vapor, plug, fastify, grape, tornado, aiohttp,
  slim, micronaut, graphql, electron, cli.

### Changed
- **Entrypoint detection now 100% YAML-driven:** Removed 26 legacy detection functions (~1,700 lines).

## [0.9.1] - 2026-01-09

### Fixed
- **Incomplete v0.9.0 release:** v0.9.0 was accidentally built from the wrong branch. This
  release includes all ADR-3aaa features. Users should upgrade from v0.9.0 to v0.9.1.

## [0.9.0] - 2026-01-09 (INCOMPLETE RELEASE)

> **Warning:** This release was built from the wrong branch. Please use v0.9.1 instead.

### Changed (Breaking)
- **Schema version 0.2.0:** New `entrypoints` field added to behavior map output.

### Added
- **`--frameworks` flag:** Control framework detection (`none`, `all`, `fastapi,celery`, or auto-detect).
- **Entrypoints in JSON output:** Detected entrypoints now persisted in output with stable IDs.
- **Smart JSON detection in slice command:** `.json` files auto-detected as `--input`.
- **Connectivity-based entrypoint ranking:** Entrypoints ranked by graph connectivity for better `--entry auto`.
- **Linker activation conditions:** Linkers now have structured activation criteria (always, frameworks, language_pairs).
- **Rich metadata extraction:** Decorators/annotations with args/kwargs for Python, JS/TS, Java, C#.
- **YAML-driven framework patterns:** Data-driven symbol enrichment via `src/hypergumbo/frameworks/*.yaml`.
  - Initial patterns for: FastAPI, Flask, Django, Express, NestJS, Spring Boot, Rails, Phoenix,
    Laravel, Go web frameworks (Gin/Echo/Fiber/Chi), Rust web frameworks (Actix/Rocket),
    ASP.NET Core, Hapi, Koa, Celery, and more.
  - See `docs/ARCHITECTURE.md` for the full pattern inventory.
- **Semantic entry detection:** Entrypoint detection via concept metadata (highest priority, 0.95 confidence).
- **HTTP linker concept support:** Extracts route info from concept metadata.

### Changed
- **Python analyzer purified:** Route detection moved from analyzer to YAML patterns.

### Deprecated
- **Packs:** Framework-specific analysis now uses `--frameworks` flag instead of packs.
- **Path-based entrypoint heuristics:** Prefer semantic detection via YAML patterns.
- **Analyzer-level route detection:** Route detection moving to YAML patterns (1.0.x migration).

## [0.6.9] - 2026-01-07

### Added
- **Connectivity-aware auto-slicing:** `--entry auto` prefers well-connected entrypoints.
- **Improved slice traversal:** Synthetic linker nodes connected via `uses` edges.
- **Stronger cross-file call resolution:** Module-qualified calls and lightweight type inference.
- **Linker diagnostics:** `LinkerRequirement` checks and registry pattern for linker execution.
- **Variable-based linker matching:** URLs/event names in variables detected (lower confidence).

### Fixed
- **Route detection false positives:** Excluded `fetchMock.get()`, `axios.post()`, etc. from Express routes.
- **Entrypoint false positives:** Excluded React file-routing, non-web handlers, DNS resolvers, etc.

### Changed
- **Linker consolidation:** All linkers migrated to `@register_linker` registry pattern.

## [0.6.0] - 2025-12-29

### Added
- **New analyzers:** Lean 4 (theorem prover), Wolfram Language (Mathematica), Agda (proof assistant).
- **Build-from-source grammars:** `scripts/build-source-grammars` for experimental tree-sitter grammars.
- **Contributor workflow:** `scripts/contribute` for fork-based contributions.
- **Release automation:** `scripts/release-check`, `scripts/release`, `scripts/integration-test`.
- **Sketch improvements:** Two-phase symbol selection, per-file compression, deterministic output.

See `docs/ARCHITECTURE.md` for the full language/framework support matrix.

## [0.5.0] - 2025-12-26

Initial public release with comprehensive static analysis capabilities.

### Core Commands
- `hypergumbo [path]` - Token-budgeted Markdown sketch
- `hypergumbo run [path]` - Full JSON behavior map
- `hypergumbo slice --entry X` - BFS/DFS subgraph extraction
- `hypergumbo routes [path]` - HTTP route listing
- `hypergumbo search <query>` - Symbol search

### Analysis Capabilities
- **32 language analyzers:** Python (AST), Java, Rust, Go, JavaScript, TypeScript, C, C++, C#,
  Ruby, PHP, Swift, Kotlin, Scala, Haskell, OCaml, Elixir, Lua, Zig, Solidity, Julia, Groovy,
  SQL, CUDA, Verilog, VHDL, GLSL, WGSL, Fortran, Bash, and more. See `docs/ARCHITECTURE.md`.
- **12 cross-language linkers:** HTTP, WebSocket, Message Queue (Kafka/RabbitMQ/SQS/Redis),
  GraphQL, gRPC, Database Query, Event Sourcing, IPC (Electron/WebWorker), JNI, Swift-ObjC,
  Phoenix Channels.
- **Framework detection:** 100+ frameworks across Python, JavaScript, Rust, Go, PHP, Java, etc.
- **Supply chain classification:** Tier 1-4 (first-party, internal deps, external deps, derived).

### Output Schema
- `schema_version`, `profile`, `nodes[]`, `edges[]`, `analysis_runs[]`, `metrics`, `limits`
- Symbols include spans, stable IDs, supply chain tier, and optional metrics.

---

## Version History

| Version | Date       | Highlights                                                   |
| ------- | ---------- | ------------------------------------------------------------ |
| 2.1.0   | 2026-03-01 | 9 new linkers (DI, HTTP, FFI, Vue, ORM, etc.), 150+ framework patterns, smart test selection |
| 2.0.2   | 2026-02-01 | Default token budget increased to 8000                       |
| 2.0.1   | 2026-01-31 | `--files` flag for slice (smart test selection support)       |
| 2.0.0   | 2026-01-31 | **Breaking:** modular package structure (5 packages), import paths changed |
| 1.3.1   | 2026-01-29 | C++ test framework patterns, go-restful support              |
| 1.3.0   | 2026-01-29 | Centralized inheritance linker, type hierarchy linker        |
| 1.2.1   | 2026-01-29 | 37 new analyzers, route-handler linker, compact subcommand   |
| 1.1.0   | 2026-01-24 | Breaking changes (not published to PyPI)                     |
| 1.0.0   | 2026-01-12 | Memory optimization (80% reduction), YAML-driven entrypoints (not published to PyPI) |
| 0.9.1   | 2026-01-09 | ADR-3aaa implementation (was missing in 0.9.0)               |
| 0.9.0   | 2026-01-09 | Schema 0.2.0, --frameworks flag, YAML patterns (incomplete)  |
| 0.6.9   | 2026-01-07 | Fewer false positives, richer slice traversal                |
| 0.6.0   | 2025-12-29 | Lean, Wolfram, Agda analyzers; release automation            |
| 0.5.0   | 2025-12-26 | Initial release: 32 analyzers, 12 linkers                    |

[Unreleased]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.1.0...HEAD
[2.1.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.0.2...v2.1.0
[2.0.2]: https://codeberg.org/iterabloom/hypergumbo/compare/v2.0.0...v2.0.2
[2.0.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.2.1...v2.0.0
[1.2.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v1.2.0...v1.2.1
[1.1.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.1...v1.1.0
[0.9.1]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.9.0...v0.9.1
[0.9.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.9...v0.9.0
[0.6.9]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.6.0...v0.6.9
[0.6.0]: https://codeberg.org/iterabloom/hypergumbo/compare/v0.5.0...v0.6.0
[0.5.0]: https://codeberg.org/iterabloom/hypergumbo/releases/tag/v0.5.0
