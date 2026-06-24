<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v6.1.0
- Released **schema** is at: v0.14.2

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

> **Looking for the reader-friendly summary?** See **[docs/RELEASE-NOTES-6.X.md](docs/RELEASE-NOTES-6.X.md)** for the audience-organized release notes (CLI users, JSON consumers, per language/framework). This file is the **implementer log**: structured, mechanism-level entries per release.

## [Unreleased]

### Added

- **Python class attributes (incl. dataclass fields) now emit `field`-kind symbols — the final emission-parity F5 strip, fully closing the cross-language field/variable gate (WI-jusus).** py.py emitted module-level variables but no `field`-kind symbols, so class attributes — including dataclass field declarations (`x: int`) in `ir.py`/`datamodels.py` and config constants — had no node in the symbol graph. Class-body `Assign`/`AnnAssign` with `Name` targets now emit `kind="field"` (`Class.attr` name; class-scoped canonical `assemble_stable_id` via the class's file-anchored stable_id; the annotation as `signature`); instance attributes (`self.x = ...` set in methods) are out of scope. **Strips the last `(python, emits_field)` strict-xfail hole** — every `emits_variable`/`emits_field` cell in the emission-parity gate is now a hard lock (the gate's F5 columns are fully closed; eight per-language slices ratcheted them away). Blast-radius: Python is hypergumbo's own self-analysis substrate, so its dataclasses now contribute field nodes. Bakeoff validation pending (WI-jusus).
- **Swift stored properties and top-level `let`/`var` now emit `field`- and `variable`-kind symbols (WI-jusus, emission-parity F5 — Swift slice, a double strip).** The Swift analyzer emitted computed properties (`kind="property"`) but not stored properties or top-level value bindings — both are bare `property_declaration` nodes (no `computed_property` child). A stored property inside a type body now emits `kind="field"`; a top-level (`source_file`-scope) `let`/`var` emits `kind="variable"` (Swift uses one node type for both — the enclosing type discriminates; function-/closure-local bindings are excluded). Both mirror the func/method path: a canonical `make_typed_stable_id`, the type annotation as `signature`, and `public`/`open` → `is_exported`. Computed properties remain `kind="property"`. **Strips both the `(swift, emits_field)` and `(swift, emits_variable)` strict-xfail holes** — and since Swift was the last `emits_variable` hole, **every `emits_variable` cell in the emission-parity gate is now a hard lock**. Blast-radius (adds field + variable symbols across the Swift corpus) — bakeoff validation pending (WI-jusus).
- **Rust struct fields and module-level `const`/`static` now emit `field`- and `variable`-kind symbols (WI-jusus, emission-parity F5 — Rust slice, a double strip).** The Rust analyzer emitted neither: struct fields (Rust's primary data construct) and module constants were both absent from the symbol graph. Named struct fields now emit `kind="field"` (`Struct::field` name, Rust's `::` member convention with the node-id segment collapsing `::`→`.`; tuple structs have positional/unnamed fields, so none); module-level `const`/`static` (top-level or inside a `mod` block — not function-body locals or impl-/trait-associated consts) emit `kind="variable"`. Both mirror the func/method path: a canonical `make_typed_stable_id`, the declared type as `signature`, and `pub` → `is_exported`. **Strips both the `(rust, emits_field)` and `(rust, emits_variable)` strict-xfail holes** — the emission-parity gate's first double strip. Blast-radius (adds field + variable symbols across the Rust corpus) — bakeoff validation pending (WI-jusus).
- **C# fields now emit `field`-kind symbols, and field attributes (DI `[Inject]`, EF Core `[Column]`/`[Key]`) gain a `decorated_by` anchor (WI-jusus, emission-parity F5 — C# slice).** The C# analyzer emitted properties (`kind="property"`) but no `field`-kind symbols, so plain fields and their attributes had no node in the symbol graph. Each `field_declaration` declarator now emits a `kind="field"` Symbol mirroring the `method_declaration` path: class-scoped `qualified_name`, a canonical `make_typed_stable_id`, the declared type as `signature`, and `public` → `is_exported`; multi-declarator fields (`int a, b;`) emit one symbol per name. The field's C# attribute metadata flows through the existing `_extract_attribute_edges` path (C# stores attributes under `meta["annotations"]`), so `[Inject]` / `[Column]` now emit `decorated_by` edges anchored on the field. Properties remain a separate `kind="property"`; the existing `class_field_types` chained-call resolution is preserved. **Strips the `(csharp, emits_field)` strict-xfail hole** from the emission-parity gate. Blast-radius (adds field symbols across the C# corpus) — bakeoff validation pending (WI-jusus).
- **Go struct fields now emit `field`-kind symbols (WI-jusus, emission-parity F5 — Go slice).** The Go analyzer emitted package-level variables but no `field`-kind symbols, so struct fields — Go's primary data-modeling construct — had no node in the symbol graph. Each NAMED struct field now emits a `kind="field"` Symbol mirroring the func/method path: struct-scoped `qualified_name` (`Struct.field`), a canonical `make_typed_stable_id`, the declared type as `signature`, and capitalization-derived `is_exported`. A `field_declaration` naming several fields (`a, b int`) emits one symbol per name; embedded fields carry no field name and remain `base_classes` (not field symbols). **Strips the `(go, emits_field)` strict-xfail hole** from the emission-parity gate (now a hard lock). Blast-radius (adds field symbols across the Go corpus, shifting symbol/centrality/dead-code counts) — bakeoff validation pending (WI-jusus).
- **Java class fields now emit `field`-kind symbols, and field annotations (Spring `@Autowired`/`@Inject` DI, JPA `@Column` ORM) gain a `decorated_by` anchor (WI-jusus, emission-parity F5 — Java slice).** The Java analyzer emitted no `field`-kind symbols, so class fields and constants had no node in the symbol graph and field-level DI/ORM annotations had nothing to attach to. Each `field_declaration` declarator now emits a `kind="field"` Symbol mirroring the `method_declaration` path: class-scoped `qualified_name` (`Class.field`), a canonical `make_typed_stable_id` (same-named fields in different classes/files stay distinct), the declared type as `signature`, and access / `static` / `final` `modifiers` (`public` → `is_exported`). Multi-declarator fields (`int a, b;`) emit one symbol per name. The field's annotation metadata flows through the existing `_extract_annotation_edges` path (kind-agnostic on the decorated side), so `@Autowired` / `@Column` now emit `decorated_by` edges anchored on the field symbol (unresolved-external for non-standard annotations). This **strips the `(java, emits_field)` strict-xfail hole** from the emission-parity gate (now a hard lock — the ratchet's first cross-language strip). Blast-radius (adds field symbols across the Java corpus, shifting symbol/centrality/dead-code counts) — bakeoff validation pending (WI-jusus).
- **The emission-parity gate now locks variable/field-kind emission across languages (WI-jusus, emission-parity F5 — the F1 gate-before-sweep).** `test_emission_parity_matrix.py` (guardrail G2) gained two columns — `emits_variable` and `emits_field` — measuring whether each language analyzer emits a `kind="variable"` / `kind="field"` Symbol for a module-level variable and a class/struct field now present in every per-language fixture. Measured-then-locked: JS/TS emit both (slices 1+2); Python and Go emit the module variable. The remaining `(language, kind)` cells are strict-xfail holes a per-language Wave-3 emitter fix strips — `emits_variable` holes: rust, swift; `emits_field` holes: python, go, java, csharp, rust, swift. A new `COLUMN_APPLICABILITY` map skips inapplicable cells so the gate never asserts a non-existent construct: `emits_variable` is not parametrized for Java/C# (no module-level variables — every value binding is a class member). Establishes the gate that the cross-language variable/field emission sweep ratchets against.
- **TypeScript/JavaScript module-level variable symbols now emit (WI-jusus, emission-parity F5 — slice 2 of 2).** Following slice 1 (class fields), top-level `const`/`let`/`var` declarations now emit `kind="variable"` symbols, so module constants and module state (`const MAX_RECONNECT_DELAY = …`, `let ws`) — previously absent from the symbol graph for every non-Python/Go language — become visible to search, centrality, and io-boundaries. Mirrors the Python analyzer's **module-level-only** contract: bindings emit at program scope (including `export const …`, flagged `is_exported`) but **not** inside function bodies or blocks (`_is_module_level_declaration` gates on the declaration's parent being `program` or a top-level `export_statement`). Identity is the canonical `make_variable_stable_id(language, repo-relative-path, name)` (a same-named const in two files stays distinct; a rare module-scope `var` redeclaration is disambiguated by the within-file collision post-pass, ADR-0035 §3); a TS type annotation becomes the `signature`. Function-valued declarations (`const f = () => …`, incl. the `catchAsync(…)` wrapper) keep their existing `kind="function"` emission — the variable path runs only when the function path did not, so there is no double-emission. **Out of scope (follow-ups):** object/array destructuring patterns (`const { a, b } = …` — the declarator's `name` field is a pattern, not an identifier, so they are skipped), function-body / bare-block locals, and TS ambient `declare const` (type-only). This is the biggest-blast-radius half (adds a node per module binding across the JS/TS corpus) — bakeoff validation pending (WI-jusus).
- **TypeScript/JavaScript class fields now emit `field`-kind symbols, and field decorators (lit `@property`/`@state`) gain a `decorated_by` anchor (WI-jusus, emission-parity F5 — slice 1 of 2).** The JS/TS analyzer emitted **zero** `variable`/`field` symbols (as did every non-Python/Go analyzer), so class fields — `public_field_definition` in TS, `field_definition` in JS — had no node in the symbol graph, and lit's field-level reactive-property decorators had nothing to attach to (only the class-level `@customElement` was captured; a component's `@property`/`@state` reactive contract was entirely absent). Class fields now emit `kind="field"` symbols mirroring the `method_definition` path: class-scoped `qualified_name` (`Class.field`), a canonical `make_typed_stable_id` (so same-named fields in different classes/files stay distinct even when untyped — the identity folds `name` + `qualified_name` + file), the type annotation as `signature`, and `static`/`readonly`/accessibility (`public`/`private`/`protected`) `modifiers`. The field's `meta["decorators"]` flows through the existing `_extract_decorator_edges` path, so `@property`/`@state` now emit `decorated_by` edges anchored on the field symbol (resolving unresolved-external, as the lit decorators are imported). Computed field names (`[Symbol.iterator]`) are skipped — they carry no stable string identity. **Scope:** this slice is JS/TS class fields only; module-level variable symbols (`const`/`let`/`var` bindings → `kind="variable"`) are WI-jusus slice 2, and the remaining non-JS/TS analyzers (Java/Rust/etc. fields) are the broader WI-jusus epic. This is a blast-radius change (adds field symbols across the JS/TS corpus, shifting symbol/centrality/dead-code counts) — bakeoff validation pending.

### Changed

- **`auto-pr` / `do_merge` now auto-recover from Codeberg's DB desync instead of just printing a hint (WI-hubod).** The desync breaks AGit's `refs/for/` proc-receive hook — which does the DB writes that *create* the PR — producing 504 / "fail to run proc-receive hook" on push, and HTTP 405 ("Please try again later") on the merge endpoint; a *plain* branch push (`refs/heads/<branch>`, post-receive only) is resilient and its post-receive recomputes the DB representation, clearing the desync. Two additive, human-approved recoveries build on last entry's self-documenting hint: (1) **push fallback** — when `do_pr`'s AGit push fails, it now falls back to a plain (non-force) branch push + an API PR-create (new `create_pr` in `forgejo-api.sh`), reusing any PR a partial AGit push already created (and re-checking after a non-2xx `create_pr` so a server-side-created PR is reused rather than duplicated by a vPR), before giving up to a vPR; (2) **merge resync-retry** — when the merge endpoint fails on the final attempt with a desync signature (405 / 000 / 5xx / "proc-receive" / "try again later"), `do_merge` does one plain branch push to resync the DB and retries the merge once, verifying success by *either* the PR record *or* git ground truth (`_pr_landed_in_base` — `git merge-base --is-ancestor`), so the "reports-failure-but-succeeds" FF-landed case (where the ref lands but the PR `merged` flag lags) is recognized rather than mis-reported as a failure. The branch pushes are non-force and never target a protected branch. New test seams (`AUTO_PR_SIMULATE_DESYNC`, `AUTO_PR_SIMULATE_DESYNC_FALLBACK`, `AUTOPR_TEST_CREATE_PR`, `AUTOPR_TEST_RESYNC_PUSH`) make every path exercisable offline.
- **Self-documenting Codeberg desync workaround in CI/forge scripts.** When `auto-pr` / `merge-pr` / `ci-debug` hit the failure signatures of Codeberg's recurring "database representation out of synchronization" desync (504 / HTTP 000 / 405 / "remote rejected: same commit" / "fail to run proc-receive hook"), they now print a terse recovery hint via a shared `print_desync_recovery_hint` helper in `forgejo-api.sh`: do one plain branch push (its `post-receive` recomputes the DB representation and clears the desync), then retry. The hint explicitly directs the agent to surface the problem to the human and **never** contact Codeberg admins. Keeps the recipe at the point of failure instead of bloating AGENTS.md.

### Fixed

- **The Java analyzer now emits an `imports` edge for every import declaration — the last imports-emission holdout among mainstream analyzers (INV-gojit).** Java extracted import declarations only into name-resolution dicts (`_extract_imports` and siblings build simple-name → FQN maps for call canonicalization) and never surfaced them as graph edges, so a Java file emitted `edge_types == [calls]` while every other mainstream analyzer (python/js/ts/go/rust/csharp/swift) emitted ≥1 `imports` edge — verified general, not stdlib-specific (zero edges on both `import java.util.List` and `import com.example.helper.Formatter`). The new `_extract_import_edges` emits one `imports` edge per declaration from the file node to a structured `ExternalRef` (aligned with ADR-0037's push to populate `dst_ref`). The module slot carries the **full import specifier** — a Java `import` names a fully-qualified type or static member, not a package, so the specifier as written IS the module path (at parity with Go/C#, whose import edges also carry the full path; the structured `ExternalRef` and the simple name in the name slot are richer than Go/C#'s literal `package`/`namespace`). This matters downstream: `refine_frameworks` prefix-matches import modules against framework patterns, and the full path resolves both package-level patterns (`org.springframework.boot` ← `import org.springframework.boot.X`) and type-level patterns (`android.app.activity` ← exact). regular `import com.example.Foo;` → module `com.example.Foo`/name `Foo`; `import static a.b.C.member;` → module `a.b.C.member`/name `member` (registered `import_static` evidence type); wildcard `import a.b.*;` → module `a.b`/name `*`. Confidence 0.95 at sibling parity; `src` is the `make_file_id` file node (backstopped by the WI-ramuv dangling-endpoint synthesizer). Because Java now emits import edges, Java/Kotlin frameworks also begin participating in `refine_frameworks`' prod-vs-dev demotion (previously exempt via the "languages without import edges" carve-out) — the full-path module representation is what keeps a real Spring app's `spring-boot` confirmed rather than demoted. **Strips the last `(java, edge_imports)` strict-xfail hole** from the emission-parity matrix gate (now a hard lock) — the gap the gate itself surfaced when it landed.
- **Go module-variable emission no longer leaks function-/block-local `var` declarations (INV-sidab).** The symbol walk visits every AST node, so a `var x = …` inside a function or block body (parent `statement_list`) was emitted as `kind="variable"` exactly like a package-level `var` — despite the emit block's own comment claiming "package-level var aliases". Surfaced by the F5 DEEP-bakeoff adversarial ground-truth audit (caddy: a function-local `var handler` in a `_test.go` body classified as a module variable). Emission is now gated on the `var_declaration`'s direct parent being `source_file` (package/file scope); interface-assertion detection (`var _ I = &S{}`) still runs for every var_spec regardless of scope (unchanged). Verified on caddy: go `variable` 98→89 — the 9 dropped are all genuine function-locals (`var body io.Reader = resp.Body` in a method, `var ptr any = rawCfg`, and test-body `handler`/`next`/`w`/`unhealthy`); 0 package-level vars dropped. Pre-existing (predates the WI-jusus F5 Go struct-field slice); same scope-conflation class as the swift INV-lanaz fix.
- **Swift field emission no longer misclassifies method-/init-/closure-local `let`/`var` as type fields (INV-lanaz; regression in the WI-jusus F5 swift slice).** The non-computed `property_declaration` branch decided field-eligibility from `_get_enclosing_type(node)` being truthy — but a local binding inside a *method* of a type also parses as `property_declaration` and *does* have an enclosing type, so it was emitted as `kind="field"` named `Type.local`. A DEEP-bakeoff adversarial ground-truth audit found **383 of 407** emitted swift "fields" on SwiftyJSON were XCTest method-body locals (e.g. `let json = JSON(array)` inside `func testSingleDimensionalArraysGetter()`), with only ~10 genuine stored properties; the 100%-coverage unit tests and the emission-parity matrix gate missed it because their fixtures used only clean stored properties declared directly in a type body. A stored property is now recognized by its **direct** parent being a type-body node (`class_body` for class/struct/actor/extension, `enum_class_body` for enum) — `enclosing_type` is computed only in that case; top-level (`source_file`-parent) bindings stay `kind="variable"`, and method/init/closure-local bindings (parent `statements`) are skipped (the module-level-only contract the other variable emitters already honor). Behavior is otherwise unchanged (the field/variable split, naming, qualified name, and stable_id are identical for genuine stored properties and top-level bindings). Verified on SwiftyJSON: swift `field` 407→19 (all 19 genuine stored properties — the `JSON` struct's `rawArray`/`rawDictionary`/`type`/… plus test-class fixtures and a `CodableModel` struct), `variable` unchanged at 46, and the two dropped `Source/` entries (`let object` in `init(data:)`, `var merged` in `merged(with:)`) confirmed method-locals.
- **The 11 doc/markup/template analyzers now mint `node.id` and `stable_id` together via one shared `make_doc_symbol_ids` helper, ending the drift class WI-rijup exposed and fixing a latent same-name-sibling id collision (INV-dulah DRY refactor).** WI-rijup canonicalized `stable_id` across these analyzers but left each one's near-duplicate per-analyzer `_make_symbol_id` raw-id builder in place, with `stable_id` minted by a *separate* `make_doc_stable_id` call — exactly the two-places-for-one-identity split that let nine of them ship `stable_id == raw id` in the first place. New `make_doc_symbol_ids(language, path, kind, name, start_line, end_line) -> (node_id, stable_id)` in `analyze/base.py` mints both from one argument set (`node_id = "{language}:{path}:{kind}:{start_line}:{name}"`; `stable_id` via the existing `make_doc_stable_id`), and the eleven copies (`rst`, `robot`, `scss`, `vue`, `svelte`, `puppet`, `astro`, `pony`, `twig`, `sparql`, `kdl`) are deleted in favor of it. For the **six line-bearing** analyzers plus `kdl` the `node.id` is **byte-identical** (their builders already emitted `{lang}:{path}:{kind}:{start_line}:{name}` with `line == span.start_line`, verified per call site). For the **four line-less** analyzers (`rst`, `robot`, `pony`, `sparql`) `node.id` **gains the `start_line` segment** — a deliberate change that both moves the id toward the documented grammar and resolves their latent collision (two same-name same-kind siblings in one file, e.g. two `section`s, previously shared a `node.id`). `stable_id` is unchanged at every site. The sparql `uses_vocabulary` edge `dst` now references the prefix node's own (now line-bearing) `id` directly — the prefix Symbol is indexed by name in Pass 1 so Pass 2 resolves it — instead of re-minting a line-less stand-in that would no longer match. The markdown and gitignore analyzers are **not** folded in: they already use a different, more grammar-conformant id shape (`{lang}:{path}:{start-end}:{name}:{kind}`), so adopting this helper would be a slot-reorder breaking change out of this refactor's scope. (Separately noted on INV-dulah: `robot`'s `test_case` node has long carried id/stable_id kind-slot `"test_case"` while `Symbol.kind == "test"` — a pre-existing kind-slot/kind discrepancy this refactor faithfully preserves, not a regression.)
- **Four more `stable_id` producers canonicalized — sparql, kdl, the graphql client-call linker, and the SCIP indexer — closing the residual WI-rijup's grep missed (WI-sojop, INV-hunup).** iter-002 validation of WI-rijup (which fixed 9 doc analyzers) confirmed those moved but surfaced a wider residual: a codebase-wide scope-expansion found four producers still emitting non-canonical `stable_id`. **sparql** (3 sites) and **kdl** carried the exact WI-rijup antipattern (`stable_id=symbol_id` over the raw composite) — two analyzers the original grep didn't sweep; both migrated to `make_doc_stable_id`. The **graphql client-call linker** (`linkers/graphql.py`) self-stamped `stable_id=call.operation_name` on its Class-B (`language=None`, `protocol_origin="graphql"`) `graphql_client` stand-in — both non-canonical *and* collision-prone (GraphQL operation names repeat across files, and bare `operation_name` is exactly the "wrong, colliding value" the `make_synthetic_symbol_identity` chokepoint was built to prevent). Fixed by emitting `stable_id=None` so the existing post-linker `populate_synthetic_class_b_identity` pass stamps a canonical injective id keyed on `(protocol_origin, kind, path, name, occurrence)` — the same path the graphql *resolver* linker already used (Phase 6 PR1); the operation name is preserved in `meta`. The **SCIP indexer** (`scip/index.py`) set `stable_id=sym_info.symbol` (the raw SCIP moniker); since the moniker is a globally-stable identity, it is now hashed via `_short_sha256` to the canonical `sha256:<16hex>` form, with the moniker preserved in `meta["scip_symbol"]`. A parallel investigation (5 read-only facet probes) settled the design hash-to-canonical over document-exempt: `stable_id` is a foreign key with a HARD per-file uniqueness check (unlike `fingerprint`, which is never a key and *can* safely exempt `language=None`); 18+/20 synthetic/external/linker producers already hash; and the SCIP node carries a real language (so a `language=None` exemption wouldn't even cover it). `Symbol.id` is unchanged at every site. iter-002 also confirmed the WI-rijup fix moved for the right reason (pretix 1223→0, chatwoot 12664→0 nonconforming `stable_id`; node counts byte-identical; node.id unchanged; 0 new collisions).
- **Nine doc/markup/template analyzers now emit canonical `sha256:<16hex>` `stable_id`s instead of reusing the raw composite `Symbol.id` (WI-rijup, INV-hunup).** The `rst`, `scss`, `vue`, `svelte`, `puppet`, `robot`, `astro`, `twig` and `pony` analyzers each built a raw composite node id (`{lang}:{path}:{kind}[:{line}]:{name}`) via a per-analyzer `_make_symbol_id` helper and then set `stable_id=<that raw id>` — so every symbol they emitted carried a `stable_id` that violated the documented `^sha256:[0-9a-f]{16}$` contract (`stable_id_scheme: hypergumbo-stableid-v8`). The bug escaped the existing `_check_stable_id_format` validator because self-analysis (the validator's gate substrate) contains no rst/vue/scss/etc. nodes; a DEEP cohort surfaced it as **1,223 nonconforming stable_ids on pretix** (rst directive/target, vue prop/slot, scss mixin) and **12,664 on chatwoot** (vue directive/prop/slot/style_block). Fixed by routing each emit site through the shared `make_doc_stable_id(language, path, kind, name, start_line, end_line)` factory (the same one markdown and gitignore already use — `sha256("doc:{language}:{path}:{kind}:{name}:{start_line}-{end_line}")[:16]`), folding `kind`+span so same-`name` siblings stay distinct. `Symbol.id` (the consumer-visible identifier / edge endpoint) is deliberately **left unchanged** at every site — only `stable_id` is canonicalized; the raw composite still serves as the node id (the node-id-format question is the separate INV-dulah invariant). Pre-existing per-analyzer tests that asserted `id == stable_id` were updated to assert the now-correct divergence (`id` retains the raw composite, `stable_id` matches the canonical regex). Scope discovery: the root-cause grep found exactly these nine `stable_id=<raw-id>` producers (markdown/gitignore were already canonical); the DRY follow-up that mints `id`+`stable_id` together so they cannot drift again — and unifies the three line-less analyzers' (`rst`/`robot`/`pony`) node-id format — is tracked on INV-dulah.
- **JavaScript/TypeScript class inheritance from external/library bases is no longer dropped — `extends LitElement` / `implements OnInit` now emit unresolved-external edges (WI-dutov, emission-parity F4).** The JS/TS inheritance-edge extractor emitted `extends`/`implements` edges only for bases that resolved intra-repo; any external/library base (`extends LitElement`, `extends React.Component`, Angular's `implements OnInit`) was **dropped entirely**, leaving the class an inheritance-graph orphan indistinguishable from a base-less class. When a base resolves to neither a project class nor a project interface, the extractor now mints an **unresolved-external** edge — `{lang}:{module_hint}:0-0:{base}:unresolved`, `is_resolved=False`, with a structured `dst_ref` when the import module is known — mirroring the unresolved-CALL convention (WI-banaf/WI-vurop). The edge type is **precise**: a new `_extract_implements_names` recovers the `implements`-clause origin so an external interface (`implements OnInit`) emits `implements`, not a mislabeled `extends`. The module hint resolves through the file's `named_imports`, then `namespace_imports` and the qualifier of a dotted base (so default imports `import Component from 'preact'` and namespace bases `import * as React; extends React.Component` recover `preact`/`react`, not the `external` sentinel), falling back to the honest `external` sentinel (no `dst_ref`) for non-imported globals. **Aliased** imports (`import { Base as B }`) are re-resolved on their canonical name first, so a local class imported under an alias resolves intra-repo instead of being mislabeled external; an aliased *external* base emits under its canonical export name. A base imported from a **relative** path that fails to resolve is treated as intra-repo (dropped) rather than mislabeled as a library. `_normalize_import_module_hint` now also strips URL/Deno schemes (`https://`, `npm:`, `jsr:`) and sanitizes residual `:` so a URL/Deno base specifier can't corrupt the colon-delimited symbol-id grammar (this also hardens the unresolved-call path that shares the helper). The core inheritance-linker's existing dedup means no duplicate edges. Scoped to the JS/TS analyzer (the highest-value, import-aware locus); the identical external-base drop in the Python/Kotlin/Ruby analyzers and the framework-agnostic core inheritance-linker, and the unhandled TS interface-extends-interface case, are tracked separately (WI-jubag and siblings), as is the eventual chokepoint form that carries import+clause metadata to the linker for all languages.
- **JavaScript/TypeScript route-handler symbols now populate `signature`, closing the last JS-vs-TS signature gap (INV-golap).** INV-golap recorded JS function/method nodes at `signature: None` on 0/10 (TS 25/26), but that asymmetry was **stale for ordinary constructs** — the `_extract_jsts_signature` code path postdated the measuring pass. A comprehensive re-probe across ~18 construct families (function declarations, arrows, async, class/static/async methods, getters/setters, object methods, named/default/const exports, default/rest params) confirmed JS already reached full TS parity for every *extracted* ordinary construct, and a whole-repo self-analysis measured JS 4/4 and TS 31/31 with **zero** null-signature function/method symbols. The re-probe isolated the one genuine residual: Express-style **route-handler pseudo-symbols** were emitted as `kind="function"` without ever calling `_extract_jsts_signature`, so an inline `app.get('/x', (req, res) => …)` handler — a real arrow/function node with `(req, res)` right there — carried `signature=None`. Fixed by adding `function_expression` to `_extract_jsts_signature`'s handled node types (covering anonymous/named function-expression handlers) and calling it at the inline-handler emit site. Genuinely **synthetic** route pseudo-symbols (external handlers passed by reference like `router.get('/u', ctrl.getUsers)`, JSX `<Route>` elements, and `createBrowserRouter` config routes) correctly keep `signature=None` — they have no local function node. A production-path parity regression test (`TestJsTsSignatureParityINVgolap`) locks JS↔TS signature-population equality across the construct matrix so the closure cannot silently regress.
- **CommonJS module bindings reach call-graph parity with ESM; `.mjs/.cjs/.mts/.cts` are now discovered (WI-zavad).** The `require()` emitter recorded the *import* edge but never registered the bound name as an import alias, so member/bare calls on a require-bound module (`const fs = require('fs'); fs.readFileSync(...)`, `const { exec } = require('child_process'); exec(...)`) emitted **zero `calls` edges** — leaving CommonJS Node I/O invisible to the io-boundaries layer (a CJS service file reported zero I/O, output identical to a genuinely I/O-free file). `_extract_namespace_imports`/`_extract_named_imports` now also harvest CommonJS default (`const x = require('m')`) and destructured (`const { a, b: c } = require('m')`) bindings, routing their calls through the same namespace (WI-vurop) and named-import (WI-banaf) unresolved-call paths as ESM, with aliased destructures keyed on the canonical export name. Separately, the file-discovery globs and analyzer `file_patterns` gained `.mjs/.cjs` (JavaScript) and `.mts/.cts` (TypeScript), which were previously never analyzed at all.
- **JavaScript browser/worker global I/O calls now reach the io-boundary catalog (WI-zavad).** The JS io-primitives catalog already listed `caches` (Service Worker CacheStorage), `indexedDB`, and the global `fetch`, but the analyzer never emitted calls for them — so a Service Worker built entirely from top-level `self.addEventListener('fetch', e => caches.open(...))` callbacks produced **zero call edges** (the WI-zavad frozen-substrate reproduction). `caches`/`indexedDB` join `JS_KNOWN_GLOBALS` (member-call path) and bare `fetch()` gets an unresolved-call path keyed on the catalog's `module: fetch, functions: [fetch]` shape; intra-repo/imported `fetch` resolution still takes precedence. Calls in top-level / anonymous-callback scope attribute to the file pseudo-node, so a callback-only Service Worker file now surfaces its CacheStorage and network I/O.
- **JavaScript/TypeScript generator functions and const-bound function expressions now emit function symbols, at parity with ordinary declarations and arrow functions (WI-zavad named function-node slice).** Two symmetric (JS+TS) NAMED extraction holes left whole classes of functions invisible to the call graph / centrality / dead-code / CC / LOC / signature: (1) `function* g() {}` parses as `generator_function_declaration` (or `generator_function` for the expression form), neither of which any symbol-extraction branch matched, so **generators emitted zero function symbols**; (2) `const f = function () {}` binds a `function_expression` (or `function* () {}` → `generator_function`) to a variable, but the `lexical_declaration` emit path only matched `arrow_function` values, so **const-bound function expressions emitted zero symbols**. Both now emit at all four call sites (direct, lexical-declaration incl. the `catchAsync(...)` wrapper, and `export function* …`), with signatures (`_extract_jsts_signature` extended to the generator node types) and typed `stable_id`s. Call-graph parity rides along: `_get_enclosing_function` recognizes generator and function-expression scopes (calls in their bodies attribute to the function symbol, not the file pseudo-node), `_is_shadowed_by_param` recognizes generator param scopes (a generator param shadowing a global no longer emits a false call edge), and `_extract_param_types` feeds typed generator params into in-body method resolution. An adversarial completeness sweep then closed four more generator-parity loci in the same change: exported generators now set `is_exported=True` (export-name collection), surface a `library_export` UsageContext from index files, and inline generator route handlers (the Koa-v1 idiom) emit a route symbol retaining the declared name instead of being silently dropped. The still-deferred sibling — anonymous-callback function-NODE extraction — is the only remaining WI-zavad slice; it shares this slice's HIGH-blast-radius class (adds function symbols corpus-wide) and stays bakeoff-tagged.
- **JavaScript/TypeScript anonymous callbacks and IIFEs now emit function-node symbols — the last WI-zavad slice (emission-parity F2), closing the P0.** Anonymous functions passed as call arguments (`el.addEventListener('fetch', e => …)`, `arr.forEach(cb)`, `p.then(cb)`, and the canonical `new Promise((res, rej) => …)` / `new Observable(cb)` constructor-executor form) and immediately-invoked function expressions (`(function () { … })()` / `(() => { … })()`) previously produced **no symbol at all** — only the file pseudo-node — so a body call inside such a callback attributed to the file (not a function node) and the WS-linker fell back to a file anchor as the edge `src` (the F159.A2-c facet: a Service Worker built entirely from top-level `addEventListener` callbacks emitted zero function nodes). The extractor now emits a `kind="function"` symbol for these idioms, named `_cb_<callee>` (the function/method/constructor the callback is passed to, e.g. `_cb_addEventListener` / `_cb_forEach` / `_cb_Promise`) or `_iife`, carrying `signature`, `cyclomatic_complexity`, `lines_of_code`, `shape_id`, typed `stable_id`, and `meta.anonymous=True`. The call-attribution machinery already resolved callbacks by position (`_get_enclosing_function`), so body calls now attribute to the callback symbol automatically (calls inside `data.forEach((i) => helper())` attribute to `_cb_forEach`, with the enclosing scope reaching it via a companion edge). Because the bare-identifier callback-reference path covers only *named* callbacks, the slice adds a companion **incoming** edge so the new symbols are not dead-code false-positives: a `references`/`callback_argument_reference` edge from the enclosing scope for call-arg callbacks (gated on `meta.anonymous` so it never mis-targets a route-handler or variable-bound arrow occupying the same position), and a `calls`/`ast_call_direct` edge for IIFEs (which run at load). The synthetic symbol `id` folds the start column into its name-slot so two same-callee callbacks on one line (`p.then(a => a, e => e)`) get distinct ids. **Scope (Option 1, documented-idiom):** deliberately bounded to call-argument callbacks (incl. constructor executors) and IIFEs; anonymous arrows in return / ternary / template-substitution position — which have no call-site anchor for a companion edge — are NOT extracted (a separate, explicitly-deferred follow-up). Variable-bound (`const f = () => …`, incl. the `const h = catchAsync((req,res) => …)` wrapper) and route-handler callbacks remain extracted by their existing paths (no double-emission). This is a HIGH-blast-radius change (adds anonymous function symbols corpus-wide, shifting symbol / centrality / dead-code counts) and so is bakeoff-tagged; with it, **WI-zavad (P0) is closed**.
- **C, C++ and bash callables now carry `cyclomatic_complexity` — and C/C++ `lines_of_code` — like the other tree-sitter analyzers (INV-loguk).** The central `symbol_introspection` complexity dispatcher already covered the 10 ADR-0033-Phase-4 languages but returned `None` for any language absent from its decision-point table, and bash/c/cpp never wired it in — so every C/C++/bash function carried a null complexity (and C/C++ functions a null LOC), unusable by dead-code-maybe scoring, supply-chain ranking and sketch centrality for those languages. Added bash/c/cpp decision-point tables (`if`/`elif`/`for`/`while`/`case` for bash; `if`/`for`/`while`/`do`/`switch`-case/ternary + `&&`/`||` for C, plus range-`for` and `catch` for C++) and wired `compute_cyclomatic_complexity` + span-derived `lines_of_code` into all three analyzers' callable emit sites (including bodyless C prototypes, which get CC=1). The dispatcher's two capability axes — signature/docstring (`SUPPORTED_LANGUAGES`) vs complexity (`BRANCH_NODE_TYPES`) — are now explicitly distinct, the latter a superset. The complexity table and walker (`BRANCH_NODE_TYPES`/`SHORT_CIRCUIT_OPS`/`compute_cyclomatic_complexity`) were then **relocated from `hypergumbo-lang-mainstream` into `hypergumbo-core`** (`analyze/cyclomatic.py`) so analyzer packages that cannot import the mainstream module can reach them — `symbol_introspection` re-exports the names for back-compat with the 13 mainstream analyzers that import them. With the dispatcher reachable everywhere, **Solidity** (extended1: `if`/`for`/`while`/`do-while`/`catch`/ternary + `&&`/`||`, gated to function/constructor/modifier so a non-callable contract does not aggregate its whole subtree) and **WGSL** (common: `if`/`for`/`while`/`loop` + each `switch` case arm incl. `default` + `&&`/`||`) callables now carry non-null `cyclomatic_complexity` and `lines_of_code`. A subsequent empirical re-probe then showed INV-loguk's "regardless of language" scope was far larger than the 15 languages wired so far — **~18+ more callable-emitting analyzers still emitted null CC/LOC** — so it remains open. **Slice C batch 1** wires five more (all in `hypergumbo-lang-common`): **Haskell** (`conditional`/`alternative`/`guards`), **OCaml** (`if`/`for`/`while_expression` + `match_case`, which also covers `try/with` handler arms), **Julia** (`if`/`elseif`/`for`/`while`/`catch`/ternary, both full- and short-form `f(x)=…` defs), **Dart** (`if`/`for`/`while`/`do`/`switch`-arms-incl-default/`catch`/ternary + dedicated `logical_and`/`logical_or_expression` short-circuit nodes; methods/getters/setters/constructors gated, walking the `function_body`), and **Erlang** (`cr_clause`/`if_clause`/`receive_after` + `andalso`/`orelse`, aggregating CC across coalesced multi-clause functions). **Slice C batch 2** wires six more (all in `hypergumbo-lang-mainstream`): **Scala** (`if`/`for`/`while`/`do_while_expression` + `case_clause`, covering match arms and try/catch handlers), **Lua** (`if`/`elseif`/`for`/`while`/`repeat` + `and`/`or`), **Perl** (`conditional`/`for`/`loop_statement` + postfix-modifier forms + `&&`/`||`/`//`; `elsif` conservatively uncounted because the grammar gives the named wrapper and the bare keyword token the same node type), **Groovy** (`if`/`for`/`while`/`do`/`switch_label`/`catch`/ternary + `&&`/`||`), **PowerShell** (`if`/`elseif`/`for`/`foreach`/`while`/`do`/`switch_clause`/`catch` + `-and`/`-or`, the latter via a new `logical_expression` member of `_BINARY_EXPR_NODE_TYPES`), and **Objective-C** (`if`/`for`/`while`/`do`/`case`/ternary/`catch` + `&&`/`||`). **Slice C batch 3** wires eight more (all in `hypergumbo-lang-extended1`): **Zig** (`if`/`for`/`while`/`switch_case` + `and`/`or`), **Gleam** (`case_clause` arms + `case_clause_guard` + `&&`/`||` — purely functional, `case` is the only control flow), **Nim** (`elif_branch`/`of_branch`/`except_branch` + `and`/`or` via a new `infix_expression` member of `_BINARY_EXPR_NODE_TYPES`; the primary `if`/`for`/`while`/`case`/`try` keywords are conservatively uncounted because tree-sitter-nim gives the named node and the bare keyword token the same `.type`), **D** (`if`/`for`/`while`/`do`/`case`/ternary + `logical_and`/`logical_or_expression`), **V** (`if_expression`/`for_statement` + `expression_case`/`default_case` + `&&`/`||`), **Hack** (`if`/`for`/`foreach`/`while`/`do`/`switch_case`/`switch_default`/`catch`/ternary + `&&`/`||`/`??`), **Haxe** (`conditional_statement` + `case_statement`; this grammar has no loop node type — loops surface only as a generic `keyword` node, deliberately uncounted to avoid over-counting), and **Odin** (`if`/`else_if_clause`/`for`/`switch_case` + `&&`/`||`). That brings 34 languages to non-null CC/LOC. **Slice C batch 4** wires six more (all in `hypergumbo-lang-common`): **Fortran** (`if`/`elseif`/`do_loop`/`case` + `.and.`/`.or.`; both the counting `do` and `do while` parse to one `do_loop_statement`, so the do-while's inner `while_statement` is excluded), **F#** (`if`/`elif`/`for`/`while_expression` + match `rule` arms), **Elm** (`if_else_expr` flat-chain + `case_of_branch` arms), **PureScript** (`exp_if` + case `alt` arms + `guards`), **MATLAB** (`if`/`elseif`/`for`/`while`/`case`/`otherwise`/`catch` + `&&`/`||` via `boolean_operator`), and **R** (`if`/`for`/`while`/`repeat` + `&&`/`||` via `binary_operator`; R's `switch()` is an ordinary call with no control-flow node, so it is conservatively uncounted). That brings 40 languages to non-null CC/LOC. **Slice C batch 5** wires nine more (all in `hypergumbo-lang-extended1`): **Ada** (`if`/`elsif`/`loop`/`case`-arm; CC computed from the subprogram *body* via a threaded `cc_node`, since the emitted span is the single-line spec), **Pascal** (`if`/`ifElse`/`for`/`while`/`repeat`/`caseCase`), **Pony** (`if_block`/`elseif_block`/`for`/`while`/match-`case_statement` + `and`/`or`), **GDScript** (`if`/`elif`/`for`/`while`/`pattern_section`/ternary/`pattern_guard` + `and`/`or`/`&&`/`||`), **Tcl** (`if`/`elseif`/`while`/`foreach`/`catch` + `&&`/`||` via a new `binop_expr` member of `_BINARY_EXPR_NODE_TYPES`; `for`/`switch` are generic command nodes, conservatively uncounted), **Luau** (`if`/`elseif`/`for`/`while`/`repeat` + `and`/`or`), **Apex** (`if`/`for`/`enhanced_for`/`while`/`do`/`switch_rule`/`catch`/ternary + `&&`/`||`), **Lean** (`if_then_else`/`match_alt` + `&&`/`||` via a new `binary_op` member; experimental grammar can't parse imperative loops, conservatively uncounted), and **Agda** (degenerate grammar — control flow is mixfix identifiers with no decision-point node types, so callables get base CC=1, which is non-null and satisfies the invariant). That brings 49 languages to non-null CC/LOC. **Slice C batch 6** wires eleven more (common/extended1/mainstream): **CUDA**/**GLSL**/**HLSL** (C-family `if`/`for`/`while`/`do`/`case`/ternary + `&&`/`||`), **Nix** (`if_expression` + `&&`/`||`), **SCSS** (`@if`/`@else if`/`@for`/`@while`/`@each`), **Circom** (`if`/`for`/`while`/ternary + `&&`/`||`), **SQL** (`case` expression — plpgsql `$$…$$` bodies don't parse to structured nodes, so an IF/WHILE-heavy plpgsql function is conservatively base CC=1; `LANGUAGE SQL` bodies count), **Jsonnet** (`conditional` + comprehension `forspec`/`ifspec` + `&&`/`||` as dedicated named nodes), **LLVM IR** (`switch`/`select`/`invoke`/`indirectbr`/`callbr`/`catchswitch`; conditional `br` excluded since the grammar can't distinguish it from unconditional `br`, so CC is a conservative undercount), and two degenerate grammars — **Wolfram** (control flow is mixfix `call` nodes with no countable node type, so CC derives solely from `&&`/`||` via a new `infix` member of `_BINARY_EXPR_NODE_TYPES`; base 1 otherwise) and **Assembly** (flat grammar — jumps are opcode-text-only `instruction` nodes, so base CC=1 per label). That brings 60 languages to non-null CC/LOC. **Slice C batch 7** closes the invariant's last gap — the **homoiconic** languages (Lisp family + Elixir), whose control flow is ordinary S-expression / `call` nodes identified by *head-symbol text* (`(if …)` / `(cond …)` / `(when …)`) rather than by node type, so a `BRANCH_NODE_TYPES` entry structurally cannot see them. Added a second, head-symbol walker (`_HOMOICONIC_SPECS` + `_compute_homoiconic_cyclomatic_complexity` in `analyze/cyclomatic.py`, dispatched ahead of the node-type walker) and wired seven analyzers: **Clojure** / **Common Lisp** (`list_lit` forms — CL counts `loop` via its dedicated `loop_macro` node and matches heads case-insensitively), **Scheme** / **Racket** (`list` forms — Racket adds the `for/…` comprehension family), **Elixir** (`call` forms — `if`/`unless`/`cond`/`case`/`with`/`for`/`receive`/`try`; `&&`/`||`/`and`/`or` are `binary_operator` nodes and stay conservatively uncounted, and the analyzer's one-Symbol-per-clause model means no cross-clause aggregation), and the partially-homoiconic **Fennel** (`for`/`each`/`match` are dedicated node types counted under an `is_named` guard) and **Janet** (`if`/`while` dedicated). `and`/`or` and multi-clause `cond`/`case` count once per form (conservative — operand/clause counts are not inspected). That brings **67 languages** to non-null CC/LOC across the Lisp / Elixir tail. Registering the walker's `_COMMONLISP_CALLABLE_KINDS` set surfaced a latent ADR-0027 gap: Common Lisp's `defgeneric` emits `kind="generic"` (via a `kind_map` dict — a literal-grep blind spot) but `generic` was never in the canonical `SYMBOL_KINDS` registry; added it (`generic`, `language_construct` axis) and regenerated `docs/schema.json` + the schema-coverage baseline.
- **INV-loguk closure — CMake + Jupyter residual gaps (the invariant now holds across *every* callable-emitting analyzer).** A closure re-probe over all 86 analyzers that emit a function-like `Symbol.kind` (static audit + per-analyzer empirical triage) found two genuine remaining gaps the language-by-language sweeps had missed, plus six justified-N/A constructs. The two real gaps: **CMake** `function()`/`macro()` blocks — real callables whose bodies carry `if`/`elseif`/`foreach`/`while` — now carry CC/LOC via a node-type-keyed `cmake` entry in `BRANCH_NODE_TYPES` (`if_command`/`elseif_command`/`foreach_command`/`while_command`; `else_command` + the `end*_command` closers excluded; CMake `AND`/`OR` are `unquoted_argument` text, so cmake stays out of `SHORT_CIRCUIT_OPS` like bash); and **Jupyter** notebook code cells — which are Python — now reuse `py.py`'s language-agnostic Python-AST complexity walkers (`_compute_cyclomatic_complexity` / `_compute_lines_of_code`) at both the top-level-function and class-method emit sites. The six **N/A** constructs (documented, not gaps): `make` `define…endef` (tree-sitter-make flattens the whole body to one opaque `raw_text` node — no control-flow AST to count), `play_routes` (single-line route-DSL rows, no body), `rust_scip` (symbols come from the external rust-analyzer SCIP index, no tree-sitter AST), `qml` + `gnuplot` (regex line-scanners, no parser/AST), and `twig` `{% macro %}` (the body's `{% if %}`/`{% for %}` are flat sibling symbols, so the macro node is structurally base-1). With CMake and Jupyter wired, **INV-loguk is satisfied**: every Symbol with a function-like kind carries non-null `cyclomatic_complexity` and `lines_of_code` when a control-flow-bearing source AST is available, regardless of language.
- **Ruby `cyclomatic_complexity` no longer over-counts (INV-loguk correctness).** The shared CC walker counted decision-point nodes purely by `.type` with no named/anonymous distinction, but tree-sitter-ruby emits BOTH a named construct node AND a same-`.type` anonymous keyword token for `if`/`while`/`for`/`case`/etc. — so every such construct inflated CC by +1 (a method with a single `if` reported CC=3 instead of the McCabe-correct 2). Added an `is_named` guard to the walker's branch-node counting so each construct counts exactly once; also switched Ruby's `case` handling to the arms-only convention used by every other language (count each `when` / Ruby-3 `in_clause` arm, not the `case`/`case_match` wrapper). Only Ruby's values change; all other languages' branch sets are named-only, so the guard is a no-op for them (verified across the full per-language CC suite).

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
- **mypy type-check foundation.** A non-strict `[tool.mypy]` config, mypy/types-PyYAML dev deps, and a non-blocking mypy CI job; clears the cheapest valid-type and malformed type-ignore findings at the source.
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
