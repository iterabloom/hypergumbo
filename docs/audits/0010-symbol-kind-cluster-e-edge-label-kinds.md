<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0010: Symbol.kind Cluster 27E — Edge Labels Masquerading as Kinds

- Date: 2026-05-05 (filed); 2026-05-06 (WI-kunag — three rows advance to PRELIM_RESOLVED); 2026-05-07 (indirection-aware re-audit — `import` and `reference` reclassified DEPRECATE-NO-FOLD → CANONICAL)
- Status: All RESOLVED — `import` and `reference` reclassified CANONICAL with status RESOLVED on 2026-05-07 after the indirection-aware re-audit surfaced new Wave-introduced producers (wasm_bindgen.py:266 for `import`, swift_objc.py:167 for `reference`). The remaining 10 rows subsequently advanced PRELIM_RESOLVED → RESOLVED at SCHEMA_VERSION 0.6.0 when the `endpoint_shape` parking axis was retired and the 71 values removed from `SYMBOL_KINDS` (ADR-0027 §"Phase 4" closure).
- Closes (partial): WI-zarov-nosin-fokum-vofom-kazum-kinir-lijof-lihud (Cluster 27E, ADR-0027 Phase 3) — sub-case (a) closed; sub-case (b) partial. Follow-on tracker items file the deferred per-producer work.
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Sixth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster 27A canonical), 0005 (Cluster 27B file-shape), 0006 (Cluster 27G build/config), 0007 (Cluster 27H domain long-tail), and 0009 (Cluster 27C apex/peer).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Detailed analysis: per-cluster fold targets" Cluster 27E is the edge-label-leakage cluster of `Symbol.kind`: values that name a relationship rather than a syntactic construct, and whose meaning duplicates a sibling `Edge.edge_type`. The 12 values seeded by ADR-0027 Phase 1 split into two sub-cases:

- **Sub-case (a) — Call-site reclassification.** `call`, `function_call`, `subprocess_call`, `db_query`, `abi_call` name source-language call expressions. The call expression is itself a syntactic construct (an AST node) and remains worth representing as a Symbol — but on a canonical kind name, not on the edge-label-shaped values currently used. Fold target: a new `call_site` canonical on `AXIS_LANGUAGE_CONSTRUCT`. The dual representation (Symbol-of-kind-`call_site` + Edge-of-edge_type-`calls`) lets consumers list all call sites in a file without walking edges.
- **Sub-case (b) — Edge duplicates.** `read`, `write`, `reference`, `import`, `inherit`, `include`, `extends` name relationships that are already captured by sibling `Edge` records (`reads`, `writes`, `references`, `imports`, `inherits`, `includes_*`, `extends_template`, etc.). The Symbol emission duplicates the relationship. Fold target: drop the Symbol; the Edge's structural representation suffices.

## Methodology

Per [ADR-0027 §"Phase 3" Cluster 27E](../adr/0027-symbol-kind-language-construct-only.md). Test 3 (construct vs. relationship) from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) is the load-bearing test for this cluster: each Cluster 27E value either names a relationship (`read`, `import`, `extends`) — fully captured by the sibling Edge — or names a use-site whose value is a thin re-statement of the dst kind (`db_query`, `abi_call` are call expressions whose semantic specialisation lives in metadata or in the dst).

The audit's empirical scope is `Symbol(kind=...)` emissions only. Some Cluster 27E value names also appear on internal-to-linker dataclasses (`UsageContext.kind`, `YjsSite.kind`, `CryptoSite.kind`, `DispatchSite.kind`); those are different fields on different types and are not in scope for the `Symbol.kind` axis. The producer count below is the count of `Symbol(kind="<value>", ...)` constructions.

## Diagnostic findings

### 1. Three Cluster 27E values have zero `Symbol.kind` producers

`call`, `read`, `write` are present in the registry on `AXIS_ENDPOINT_SHAPE` but no producer emits them via `Symbol(kind=...)`. The grep matches all live on different fields: `UsageContext.kind="call"` (in `ir.py`, `analyze/base.py`-derived analyzers, ~16 sites) and `YjsSite/CryptoSite/DispatchSite.kind="read"|"write"` (in three pub/sub linkers). None of those constructions feed into `Symbol.kind` — the linkers emit downstream Symbols with semantic kinds like `crypto_producer`, `message_handler`, `event_publisher`. The registry entries are therefore stale for the `Symbol.kind` axis and DEPRECATE-NO-FOLD applies trivially.

This mirrors audit-findings 0009 §"Diagnostic findings" #1: a value can be in the registry while every producer has organically migrated away. The Phase 4b prerequisite (no producer remains) is satisfied at filing.

### 2. Sub-case (b) "drop entirely" requires per-producer edge-endpoint analysis

ADR-0027 Cluster 27E sub-case (b)'s prescription — "drop entirely at the producer because the relationship is on the Edge" — assumes the corresponding Edge can stand alone after the Symbol drop. Inspection of the 13 `Symbol(kind="<value>", ...)` sites for the seven sub-case (b) values reveals three distinct producer shapes:

1. **Edge-independent.** The producer emits both a Symbol and an Edge whose endpoints reference *other* Symbols (file/scope/external boundary nodes), not the Symbol being dropped. Cleanly dropping the Symbol leaves the Edge intact. Three sites exhibit this shape: `bitbake.py:264` (inherit), `css.py:155` (import), `jsonnet.py:212` (import).
2. **Edge-endpoint dependency.** The producer emits a Symbol *and* an Edge whose `src` (or, in one case, `dst`) is the Symbol's id. Dropping the Symbol orphans the Edge. Five sites: `json_config.py:762` (reference), `puppet.py:410` (include), `scss.py:378` (include), `twig.py:166`/`twig.py:228`/`twig.py:198` (include×2 + extends).
3. **Edge-absent.** The producer emits a Symbol with no companion Edge; dropping the Symbol erases the language-level construct (the import / extends / include directive) from the behaviour map. Four sites: `r_lang.py:215` (import — no companion Edge for R `library()` imports), `astro.py:209` (import — note: the astro analyzer does emit an `imports` Edge nearby in `astro.py:332`, but that Edge is for the separate component_ref Symbol (Cluster 27F), not for the import Symbol; this audit's PR 2 re-inspection corrects the original shape-2 classification of this site to shape-3), `make.py:282` (include), `blade.py:265` (extends).

Per-shape verdict applicability:

- Shape (1) sites can ship the FOLD verdict in this PR (PRELIM_RESOLVED).
- Shape (2) sites need an edge-endpoint refactor (re-route the Edge `src` to a containing file/scope Symbol) before the Symbol drop becomes safe. Verdict deferred per-site, status UNRESOLVED, work tracked in WI-zarov follow-on (PR 2 in the WI's discussion).
- Shape (3) sites need a redesign decision (introduce an Edge to carry the relationship before dropping the Symbol — `directives` edge type? `imports` boundary edge?). Verdict deferred per-site, status UNRESOLVED, work tracked in a separate follow-on item filed alongside PR 2.

### 3. Audit-findings 0003 supersession: 4 rows reclassified from Cluster 27A to Cluster 27E

`import`, `include`, `inherit`, `extends` were initially filed in audit-findings 0003 as Cluster 27A CANONICAL + RESOLVED on the `language_construct` axis. ADR-0027 §"Detailed analysis" line 159 places these four values in Cluster 27E sub-case (b) (DEPRECATE-NO-FOLD: the relationship lives on the corresponding Edge). This audit correctly reclassifies them.

The 4 rows are removed from audit-findings 0003 in this PR (they would otherwise carry contradictory dual verdicts). `inherit`'s registry placement also moves from `language_construct` to `endpoint_shape` to match the Cluster 27E classification (this PR ships the producer drop, so `inherit` is at PRELIM_RESOLVED). The other three (`import`, `include`, `extends`) keep their current `language_construct` placement under the UNRESOLVED status (which the lifecycle table accepts on any axis); they will move to `endpoint_shape` in follow-on PRs as their producers migrate.

### 4. The `call_site` canonical is new vocabulary, not a peer of `call`

`call_site` is added to the registry on `AXIS_LANGUAGE_CONSTRUCT` in this PR. It is not an apex/peer fold of `call` (Cluster 27C) — `call`'s sole emit site, `UsageContext.kind="call"`, is on a different axis (`UsageContext.kind`, currently a 4-value Literal at `ir.py:611`). The two are independent populations sharing a name; the audit doc for `UsageContext.kind` (a separate axis declaration ADR if one is filed) governs that field. For `Symbol.kind`, `call_site` is a fresh canonical naming the call-expression syntactic construct.

The four sub-case (a) FOLD targets (`function_call`, `subprocess_call`, `db_query`, `abi_call`) all migrate to `call_site` in this PR. `db_query` and `abi_call` carry a meta key on the migrated Symbol (`meta["framework_role"]="db_query"` / `meta["abi_target"]=...`) so the framework specialisation that was previously encoded in the kind label moves to where ADR-0023 §"Detailed analysis" filed it for `Edge.edge_type` analogues — on metadata.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: call
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"call\"' | grep -v 'test_\\|symbol_kinds.py\\|UsageContext\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Zero Symbol.kind=call producers; the value lives only on UsageContext.kind (a different axis on a different field). The registry entry is stale for the Symbol.kind axis. Phase 4b removal pending."
  - value: function_call
    verdict: FOLD
    fold_target: call_site
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]function_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Twig function_call site (twig.py:471) reclassified to call_site in this PR. Sole producer."
  - value: subprocess_call
    verdict: FOLD
    fold_target: call_site
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]subprocess_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "subprocess_cli linker (subprocess_cli.py:323) reclassified to call_site + meta['call_kind']='subprocess'. Sole producer."
  - value: db_query
    verdict: FOLD
    fold_target: call_site
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]db_query[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "database_query linker (database_query.py:350) reclassified to call_site + meta['call_kind']='db_query'. Sole producer. Framework specialisation moves from kind label to meta key per the ADR-0023 shape-on-meta discipline."
  - value: abi_call
    verdict: FOLD
    fold_target: call_site
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]abi_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "solidity_abi linker (solidity_abi.py:206) reclassified to call_site + meta['call_kind']='abi'. Sole producer."
  - value: read
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"read\"' | grep -v 'test_\\|symbol_kinds.py\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Zero Symbol.kind=read producers; matches in yjs_crdt/crypto_flow/message_dispatch are on linker-internal dataclass fields (YjsSite.kind, CryptoSite.kind, DispatchSite.kind). The registry entry is stale for the Symbol.kind axis. Phase 4b removal pending."
  - value: write
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"write\"' | grep -v 'test_\\|symbol_kinds.py\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Symmetric counterpart of read. Zero Symbol.kind=write producers; the registry entry is stale. Phase 4b removal pending."
  - value: reference
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"reference\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Use-site reference; the canonical fold target for Objective-C selector_ref per audit-findings 0011's _ref shape disposition (Wave 5 framework-role fold). The original DEPRECATE-NO-FOLD verdict was correct for the json_config.py shape-2 producer it inventoried, but the indirection-aware re-audit on 2026-05-07 surfaced swift_objc.py:167 (selector_ref → kind=\"reference\" + meta[\"framework_role\"]=\"selector_ref\") as a real Wave 5-introduced producer. Reclassified DEPRECATE-NO-FOLD → CANONICAL with status RESOLVED — promoted to language_construct in symbol_kinds.py."
  - value: import
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"import\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Top-level wasm-bindgen FFI import declaration. The original DEPRECATE-NO-FOLD verdict was correct for the css.py / jsonnet.py / astro.py / r_lang.py producers it inventoried (those Symbol emissions dropped across PR 1, PR 2, WI-kunag), but the indirection-aware re-audit on 2026-05-07 surfaced wasm_bindgen.py:266 as a new producer introduced by Wave 6 PR 3 (wasm_import → kind=\"import\" + meta[\"compilation_target\"]=\"wasm\"). The wasm-bindgen `import` is a real top-level construct in its source DSL, retained because the slicer BFS needs the synthetic boundary node for continuity. Reclassified DEPRECATE-NO-FOLD → CANONICAL with status RESOLVED — promoted to language_construct in symbol_kinds.py."
  - value: inherit
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]inherit[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the inherits Edge captures the relationship; no replacement Symbol kind. Sole producer (bitbake.py:264) is shape (1) edge-independent: the inherits Edge has src=bitbake:{file}, dst=bitbake:class:{cls} — both endpoints are independent of the dropped Symbol. Symbol drop ships in this PR; registry entry moved from language_construct to endpoint_shape. Tests refactored to verify the inherits Edge instead."
  - value: include
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]include[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the include-family Edges (includes_class, uses_mixin, includes_template, includes) capture the relationship; no replacement Symbol kind. Five producers across migration waves: puppet.py (shape 2, dropped in PR 2 — includes_class Edge re-routed src to manifest file id, dst made unconditional via dangling class id when class isn't in local registry), scss.py (shape 2, dropped in PR 2 — uses_mixin Edge re-routed src to stylesheet file id, dst made unconditional via dangling mixin id), twig.py:166 + twig.py:255 (shape 2, both dropped in PR 2 — includes_template Edge re-routed src to template file id; the {{ include() }} function-call form distinguished via meta['form']='function'), make.py (shape 3 — WI-kunag introduces a companion `includes` Edge with src=make_file_id(\"make\", path) and a 5-part dangling included-file id, so makefile include directives keep representation in the graph). All five producers' Symbol emissions dropped. Status advances 2026-05-06 (WI-kunag) from UNRESOLVED to PRELIM_RESOLVED."
  - value: extends
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]extends[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the extends_template Edge captures the relationship; no replacement Symbol kind. Two producers: twig.py (shape 2, dropped in PR 2 — extends_template Edge re-routed src to template file id) and blade.py (shape 3 — WI-kunag introduces a companion `extends_template` Edge with src=make_file_id(\"blade\", path) and a 5-part dangling parent-template id, so @extends directives keep representation in the graph). Both producers' Symbol emissions dropped. Status advances 2026-05-06 (WI-kunag) from UNRESOLVED to PRELIM_RESOLVED."
```

## Migration impact

- **Producer-side this PR:** seven producer migrations.
  - Sub-case (a): `twig.py:471`, `subprocess_cli.py:323`, `database_query.py:350`, `solidity_abi.py:206` — reclassify `kind` to `call_site`; `db_query` / `abi_call` / `subprocess_call` carry the prior specialisation in `meta["call_kind"]`.
  - Sub-case (b) shape (1) drops: `css.py:155`, `jsonnet.py:212`, `bitbake.py:264` — Symbol emission removed; companion Edge unchanged.
- **Registry-side this PR:** add `call_site` on `AXIS_LANGUAGE_CONSTRUCT` (Cluster 27A canonical). Move sub-case (a) values (`function_call`, `subprocess_call`, `db_query`, `abi_call`) and `inherit` from `endpoint_shape` to remain on `endpoint_shape` through the Phase 4a deprecation window — the audit-findings status field tracks the migration state, not the registry axis. (The registry-side prune is a Phase 4b operation gated on bakeoff validation.)
- **Schema-side:** open enum on `Symbol.kind` accommodates the additive `call_site` addition. Per ADR-0027 §"Phase 4", no SCHEMA_VERSION bump for Phase 3 fold work.
- **Consumer-side:** no immediate change. Tests asserting `kind="function_call|subprocess_call|db_query|abi_call|inherit"` migrate (test files updated in this PR). One test (`test_jsonnet.py:123`) updates to verify the `imports` Edge instead of the dropped Symbol.
- **Test-side:** ~7 test files updated. Diagnostic-test assertions in `tests/test_audit_findings.py` continue to pass (every row's status agrees with registry presence).
- **Producer-side deferred:** 8 sites for sub-case (b) shapes (2) and (3) — `json_config.py`, `r_lang.py`, `astro.py`, `puppet.py`, `scss.py`, `make.py`, `twig.py` (×3), `blade.py`. Tracked in WI-zarov follow-on PRs.

## Re-audit (2026-05-07) — Indirection-aware producer trace

The Fundamental Concept Audit playbook §"Step 4.5 — Indirection-aware
producer trace" was added on 2026-05-07 (companion methodology
hardening to this re-audit). It requires checking five producer-emit
shapes — literal kwarg, helper-call positional/kwarg, assignment-form-
to-Name, f-string interpolation, dict-subscript-target — for every
verdict asserting producer existence or non-existence. This section
records the systematic re-application of those five greps to every
DEPRECATE-NO-FOLD row in this audit-findings doc and across the other
DEPRECATE-NO-FOLD rows in the broader audit-findings corpus.

**Methodology.** For each of the 19 DEPRECATE-NO-FOLD-flagged values
across audit-findings 0005 / 0006 / 0007 / 0010 / 0012 — namely
`build-dependency`, `call`, `component_ref`, `config`, `dev-dependency`,
`event_subscribes`, `extends`, `heading`, `import`, `include`,
`inherit`, `message_receive`, `model`, `read`, `reference`, `tsconfig`,
`unresolved`, `work_item`, `write` — run grep across `packages/` and
`scripts/` for each of:

1. `kind=\"<value>\"` and `evidence_type=\"<value>\"` (literal kwarg)
2. `add_symbol(\\*, \"<value>\")`, `_make_*_symbol(\\*, \"<value>\")`,
   `make_symbol_id(\\*, \"<value>\")` (helper-call positional)
3. `kind = \"<value>\"` followed by `Symbol(kind=…)` (assignment-form
   to Name — auto-checked by the WI-viluk regression-guard added in
   the same merge series)
4. `kind=f\"…<value>…\"` and `f\"…<value>…\"` (f-string interpolation)
5. `kinds[\"<key>\"] = \"<value>\"` (dict-subscript-target assignment)

**Results.** Two leaks surfaced; both reclassified in this PR:

- **`reference` — `swift_objc.py:167`** (Wave 5 framework-role fold).
  Audit-findings 0011's `_ref` shape disposition folded `selector_ref`
  to canonical `kind="reference"` + `meta["framework_role"]="selector_ref"`.
  The original audit-findings 0010 sub-case (b) drop verdict was
  correct for the json_config.py shape-2 producer it inventoried (the
  tsconfig case, separately resolved by Wave 6 PR 3) but the Wave 5
  fold added a different real producer that the literal-grep at the
  time did not surface. Reclassified DEPRECATE-NO-FOLD → CANONICAL
  with status RESOLVED; `reference` moves to AXIS_LANGUAGE_CONSTRUCT
  in `symbol_kinds.py`.
- **`import` — `wasm_bindgen.py:266`** (Wave 6 PR 3 wasm_import fold).
  The original sub-case (b) drop verdict was correct for the four
  producers it inventoried (css.py, jsonnet.py, astro.py, r_lang.py —
  all dropped per their respective Symbol-emission removals). Wave 6
  PR 3 then added a *new* producer for a different purpose:
  `wasm_import → kind="import" + meta["compilation_target"]="wasm"` at
  `wasm_bindgen.py:266` — the slicer BFS needs the synthetic boundary
  node for continuity, so the post-fold form had to retain `import`
  as a Symbol.kind value. The wasm-bindgen `import` is a real top-
  level construct in its source DSL, not a relabel of the imports
  Edge. Reclassified DEPRECATE-NO-FOLD → CANONICAL with status
  RESOLVED; `import` moves to AXIS_LANGUAGE_CONSTRUCT in
  `symbol_kinds.py`.

**False-positive analysis.** The other 17 DEPRECATE-NO-FOLD values
were verified clean. The grep noise broke down as:

- **ID-construction tail tokens.** `model` (`prisma.py:119`
  `make_symbol_id(..., "model")`), `tsconfig` (`json_config.py:748`
  `_make_symbol_id(..., "tsconfig")`), `unresolved`
  (`pyffi.py:333` / `ruby_ffi.py:231` `f"{prefix}{name}:unresolved"`):
  the trailing token in symbol_id / dst_id format strings preserves
  stable_id contracts after a fold; the actual `Symbol(kind=…)`
  emission uses the fold target. Each audit-findings doc explicitly
  notes this pattern in its prose.
- **Linker-internal dataclass fields.** `read` / `write` at
  `dataflow.py` (`result[node_type] = "read"|"write"`) and at
  `crypto_flow.py` / `yjs_crdt.py` (`CryptoSite(kind="read"|"write")` /
  `YjsSite(kind="read"|"write")`): different classes' `kind` fields,
  not `Symbol.kind` producers. Audit-findings 0010 explicitly notes
  this.
- **Tracker `Item.kind` field.** `work_item` at
  `screenshot_save.py:78` and `migration.py:560`
  (`ts.add(kind="work_item")` / `ParsedItem(kind="work_item")`):
  tracker-package internal API, not `Symbol.kind`. Audit-findings 0006
  explicitly notes this.
- **Edge dst-id format-string components.** `tsconfig` at
  `json_config.py:786` (`f"json:{ref_path}:1-1:{name}:tsconfig"`) and
  `unresolved` (above): the 5-part dangling-id format
  (`lang:path:line:name:kind`) carries a kind-shaped trailing token
  that the IR boundary materializer reads to construct an
  `external_symbol` Symbol. The downstream Symbol emission uses
  `kind="external_symbol"` per Wave 6 PR 6, not the trailing token
  literally. Not a producer leak.
- **Prose strings.** `call`/`config`/`extends`/`heading`/`include`/
  `model`/`read`/`reference`/`write` appear in error messages,
  docstrings, comments, and label prose. None reach a producer
  call site.
- **Dict-subscript values not flowing to producers.** `dataflow.py`
  assigns `result[node_type] = "read"|"write"` for dataflow access-
  mode tracking, with no downstream `Symbol(kind=...)` emit consuming
  the dict.

The five-shape grep itself is documented in this PR's CHANGELOG entry
and in the playbook §"Step 4.5". The literal-kwarg + assignment-form
shapes are now also enforced at every-commit time by the WI-viluk
regression-guard property test
(`tests/test_audit_findings.py::test_live_tree_deprecate_no_fold_has_zero_producers`).
The remaining three shapes — helper-call, f-string, dict-subscript —
require manual grep per playbook until WI-nubuv ext B / ext C land
the structural backstops.

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` axis this audit applies. §"Detailed analysis" Cluster 27E and §"Phase 3" Cluster 27E are the load-bearing references.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0027 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the precedent for "shape on meta, not on the label" (§"Detailed analysis"). Sub-case (a) carries the same discipline for `Symbol.kind`.
- Audit-findings 0009 — Cluster 27C precedent for "registry stays on endpoint_shape during the Phase 4a window".
- WI-runod cross-axis schedule — Wave 4 of which this PR closes one (mostly) item.
