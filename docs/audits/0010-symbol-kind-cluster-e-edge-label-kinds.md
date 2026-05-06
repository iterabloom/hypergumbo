<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0010: Symbol.kind Cluster E — Edge Labels Masquerading as Kinds

- Date: 2026-05-05 (filed); 2026-05-06 (WI-kunag — three rows advance to PRELIM_RESOLVED)
- Status: Mixed — sub-case (a) call_site reclassifications and sub-case (b) DEPRECATE-NO-FOLD-zero-producer rows ship at PRELIM_RESOLVED; sub-case (b) `inherit` PRELIM_RESOLVED at PR 1; sub-case (b) `reference` PRELIM_RESOLVED at PR 2 (json_config edge-endpoint redesign); sub-case (b) `import` / `include` / `extends` PRELIM_RESOLVED at WI-kunag PR (companion `imports` / `includes` / `extends_template` Edges introduced for the four shape-3 producers — astro.py, r_lang.py, make.py, blade.py — so the corresponding syntactic relationships keep representation in the graph after the per-Symbol drop).
- Closes (partial): WI-zarov-nosin-fokum-vofom-kazum-kinir-lijof-lihud (Cluster E, ADR-0027 Phase 3) — sub-case (a) closed; sub-case (b) partial. Follow-on tracker items file the deferred per-producer work.
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Sixth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster A canonical), 0005 (Cluster B file-shape), 0006 (Cluster G build/config), 0007 (Cluster H domain long-tail), and 0009 (Cluster C apex/peer).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Detailed analysis: per-cluster fold targets" Cluster E is the edge-label-leakage cluster of `Symbol.kind`: values that name a relationship rather than a syntactic construct, and whose meaning duplicates a sibling `Edge.edge_type`. The 12 values seeded by ADR-0027 Phase 1 split into two sub-cases:

- **Sub-case (a) — Call-site reclassification.** `call`, `function_call`, `subprocess_call`, `db_query`, `abi_call` name source-language call expressions. The call expression is itself a syntactic construct (an AST node) and remains worth representing as a Symbol — but on a canonical kind name, not on the edge-label-shaped values currently used. Fold target: a new `call_site` canonical on `AXIS_LANGUAGE_CONSTRUCT`. The dual representation (Symbol-of-kind-`call_site` + Edge-of-edge_type-`calls`) lets consumers list all call sites in a file without walking edges.
- **Sub-case (b) — Edge duplicates.** `read`, `write`, `reference`, `import`, `inherit`, `include`, `extends` name relationships that are already captured by sibling `Edge` records (`reads`, `writes`, `references`, `imports`, `inherits`, `includes_*`, `extends_template`, etc.). The Symbol emission duplicates the relationship. Fold target: drop the Symbol; the Edge's structural representation suffices.

## Methodology

Per [ADR-0027 §"Phase 3" Cluster E](../adr/0027-symbol-kind-language-construct-only.md). Test 3 (construct vs. relationship) from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md) is the load-bearing test for this cluster: each Cluster E value either names a relationship (`read`, `import`, `extends`) — fully captured by the sibling Edge — or names a use-site whose value is a thin re-statement of the dst kind (`db_query`, `abi_call` are call expressions whose semantic specialisation lives in metadata or in the dst).

The audit's empirical scope is `Symbol(kind=...)` emissions only. Some Cluster E value names also appear on internal-to-linker dataclasses (`UsageContext.kind`, `YjsSite.kind`, `CryptoSite.kind`, `DispatchSite.kind`); those are different fields on different types and are not in scope for the `Symbol.kind` axis. The producer count below is the count of `Symbol(kind="<value>", ...)` constructions.

## Diagnostic findings

### 1. Three Cluster E values have zero `Symbol.kind` producers

`call`, `read`, `write` are present in the registry on `AXIS_ENDPOINT_SHAPE` but no producer emits them via `Symbol(kind=...)`. The grep matches all live on different fields: `UsageContext.kind="call"` (in `ir.py`, `analyze/base.py`-derived analyzers, ~16 sites) and `YjsSite/CryptoSite/DispatchSite.kind="read"|"write"` (in three pub/sub linkers). None of those constructions feed into `Symbol.kind` — the linkers emit downstream Symbols with semantic kinds like `crypto_producer`, `message_handler`, `event_publisher`. The registry entries are therefore stale for the `Symbol.kind` axis and DEPRECATE-NO-FOLD applies trivially.

This mirrors audit-findings 0009 §"Diagnostic findings" #1: a value can be in the registry while every producer has organically migrated away. The Phase 4b prerequisite (no producer remains) is satisfied at filing.

### 2. Sub-case (b) "drop entirely" requires per-producer edge-endpoint analysis

ADR-0027 Cluster E sub-case (b)'s prescription — "drop entirely at the producer because the relationship is on the Edge" — assumes the corresponding Edge can stand alone after the Symbol drop. Inspection of the 13 `Symbol(kind="<value>", ...)` sites for the seven sub-case (b) values reveals three distinct producer shapes:

1. **Edge-independent.** The producer emits both a Symbol and an Edge whose endpoints reference *other* Symbols (file/scope/external boundary nodes), not the Symbol being dropped. Cleanly dropping the Symbol leaves the Edge intact. Three sites exhibit this shape: `bitbake.py:264` (inherit), `css.py:155` (import), `jsonnet.py:212` (import).
2. **Edge-endpoint dependency.** The producer emits a Symbol *and* an Edge whose `src` (or, in one case, `dst`) is the Symbol's id. Dropping the Symbol orphans the Edge. Five sites: `json_config.py:762` (reference), `puppet.py:410` (include), `scss.py:378` (include), `twig.py:166`/`twig.py:228`/`twig.py:198` (include×2 + extends).
3. **Edge-absent.** The producer emits a Symbol with no companion Edge; dropping the Symbol erases the language-level construct (the import / extends / include directive) from the behaviour map. Four sites: `r_lang.py:215` (import — no companion Edge for R `library()` imports), `astro.py:209` (import — note: the astro analyzer does emit an `imports` Edge nearby in `astro.py:332`, but that Edge is for the separate component_ref Symbol (Cluster F), not for the import Symbol; this audit's PR 2 re-inspection corrects the original shape-2 classification of this site to shape-3), `make.py:282` (include), `blade.py:265` (extends).

Per-shape verdict applicability:

- Shape (1) sites can ship the FOLD verdict in this PR (PRELIM_RESOLVED).
- Shape (2) sites need an edge-endpoint refactor (re-route the Edge `src` to a containing file/scope Symbol) before the Symbol drop becomes safe. Verdict deferred per-site, status UNRESOLVED, work tracked in WI-zarov follow-on (PR 2 in the WI's discussion).
- Shape (3) sites need a redesign decision (introduce an Edge to carry the relationship before dropping the Symbol — `directives` edge type? `imports` boundary edge?). Verdict deferred per-site, status UNRESOLVED, work tracked in a separate follow-on item filed alongside PR 2.

### 3. Audit-findings 0003 supersession: 4 rows reclassified from Cluster A to Cluster E

`import`, `include`, `inherit`, `extends` were initially filed in audit-findings 0003 as Cluster A CANONICAL + RESOLVED on the `language_construct` axis. ADR-0027 §"Detailed analysis" line 159 places these four values in Cluster E sub-case (b) (DEPRECATE-NO-FOLD: the relationship lives on the corresponding Edge). This audit correctly reclassifies them.

The 4 rows are removed from audit-findings 0003 in this PR (they would otherwise carry contradictory dual verdicts). `inherit`'s registry placement also moves from `language_construct` to `endpoint_shape` to match the Cluster E classification (this PR ships the producer drop, so `inherit` is at PRELIM_RESOLVED). The other three (`import`, `include`, `extends`) keep their current `language_construct` placement under the UNRESOLVED status (which the lifecycle table accepts on any axis); they will move to `endpoint_shape` in follow-on PRs as their producers migrate.

### 4. The `call_site` canonical is new vocabulary, not a peer of `call`

`call_site` is added to the registry on `AXIS_LANGUAGE_CONSTRUCT` in this PR. It is not an apex/peer fold of `call` (Cluster C) — `call`'s sole emit site, `UsageContext.kind="call"`, is on a different axis (`UsageContext.kind`, currently a 4-value Literal at `ir.py:611`). The two are independent populations sharing a name; the audit doc for `UsageContext.kind` (a separate axis declaration ADR if one is filed) governs that field. For `Symbol.kind`, `call_site` is a fresh canonical naming the call-expression syntactic construct.

The four sub-case (a) FOLD targets (`function_call`, `subprocess_call`, `db_query`, `abi_call`) all migrate to `call_site` in this PR. `db_query` and `abi_call` carry a meta key on the migrated Symbol (`meta["framework_role"]="db_query"` / `meta["abi_target"]=...`) so the framework specialisation that was previously encoded in the kind label moves to where ADR-0023 §"Detailed analysis" Cluster F filed it for `Edge.edge_type` analogues — on metadata.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: call
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"call\"' | grep -v 'test_\\|symbol_kinds.py\\|UsageContext\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Zero Symbol.kind=call producers; the value lives only on UsageContext.kind (a different axis on a different field). The registry entry is stale for the Symbol.kind axis. Phase 4b removal pending."
  - value: function_call
    verdict: FOLD
    fold_target: call_site
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]function_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Twig function_call site (twig.py:471) reclassified to call_site in this PR. Sole producer."
  - value: subprocess_call
    verdict: FOLD
    fold_target: call_site
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]subprocess_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "subprocess_cli linker (subprocess_cli.py:323) reclassified to call_site + meta['call_kind']='subprocess'. Sole producer."
  - value: db_query
    verdict: FOLD
    fold_target: call_site
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]db_query[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "database_query linker (database_query.py:350) reclassified to call_site + meta['call_kind']='db_query'. Sole producer. Framework specialisation moves from kind label to meta key per the ADR-0023 Cluster F shape-on-meta discipline."
  - value: abi_call
    verdict: FOLD
    fold_target: call_site
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]abi_call[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "solidity_abi linker (solidity_abi.py:206) reclassified to call_site + meta['call_kind']='abi'. Sole producer."
  - value: read
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"read\"' | grep -v 'test_\\|symbol_kinds.py\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Zero Symbol.kind=read producers; matches in yjs_crdt/crypto_flow/message_dispatch are on linker-internal dataclass fields (YjsSite.kind, CryptoSite.kind, DispatchSite.kind). The registry entry is stale for the Symbol.kind axis. Phase 4b removal pending."
  - value: write
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'Symbol(' packages/ --include='*.py' -A 8 | grep -E 'kind=\"write\"' | grep -v 'test_\\|symbol_kinds.py\\|YjsSite\\|CryptoSite\\|DispatchSite'"
      expect: empty
    rationale: "Symmetric counterpart of read. Zero Symbol.kind=write producers; the registry entry is stale. Phase 4b removal pending."
  - value: reference
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]reference[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the references Edge captures the relationship; no replacement Symbol kind. Sole producer (json_config.py) was shape (2) edge-endpoint-dependent (references Edge had dst=symbol_id). PR 2 (WI-zarov shape-2 redesign) re-routes the Edge dst from the dropped Symbol id to a 5-part dangling tsconfig file id (handled by IR boundary materialization), then drops the Symbol. The reference_path moves from Symbol.meta to Edge.meta."
  - value: import
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]import[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the imports Edge captures the relationship; no replacement Symbol kind. Four producers across migration waves: css.py:155 (shape 1, dropped in PR 1), jsonnet.py:212 (shape 1, dropped in PR 1), astro.py:209 (originally shape 2; PR 2 re-inspection corrected to shape 3 — the imports Edge in astro.py:332 belongs to the separate component_ref Symbol (Cluster F), not the import Symbol. WI-kunag introduces a companion frontmatter `imports` Edge with src=make_file_id(\"astro\", path) and a 5-part dangling dst, so the frontmatter `import …` syntactic relationship now keeps representation in the graph alongside the use-site component_ref Edge), r_lang.py:215 (shape 3 — WI-kunag introduces a companion `imports` Edge for library() / require() with src=make_file_id(\"r\", path) and a 5-part dangling package id, so R package imports now keep representation in the graph). All four producers' Symbol emissions dropped. Status advances 2026-05-06 (WI-kunag) from UNRESOLVED to PRELIM_RESOLVED."
  - value: inherit
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]inherit[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the inherits Edge captures the relationship; no replacement Symbol kind. Sole producer (bitbake.py:264) is shape (1) edge-independent: the inherits Edge has src=bitbake:{file}, dst=bitbake:class:{cls} — both endpoints are independent of the dropped Symbol. Symbol drop ships in this PR; registry entry moved from language_construct to endpoint_shape. Tests refactored to verify the inherits Edge instead."
  - value: include
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]include[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the include-family Edges (includes_class, uses_mixin, includes_template, includes) capture the relationship; no replacement Symbol kind. Five producers across migration waves: puppet.py (shape 2, dropped in PR 2 — includes_class Edge re-routed src to manifest file id, dst made unconditional via dangling class id when class isn't in local registry), scss.py (shape 2, dropped in PR 2 — uses_mixin Edge re-routed src to stylesheet file id, dst made unconditional via dangling mixin id), twig.py:166 + twig.py:255 (shape 2, both dropped in PR 2 — includes_template Edge re-routed src to template file id; the {{ include() }} function-call form distinguished via meta['form']='function'), make.py (shape 3 — WI-kunag introduces a companion `includes` Edge with src=make_file_id(\"make\", path) and a 5-part dangling included-file id, so makefile include directives keep representation in the graph). All five producers' Symbol emissions dropped. Status advances 2026-05-06 (WI-kunag) from UNRESOLVED to PRELIM_RESOLVED."
  - value: extends
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "grep -rn '\\bkind=[\"\\047]extends[\"\\047]' packages/ scripts/ | grep -v 'test_\\|symbol_kinds.py'"
      expect: empty
    rationale: "Sub-case (b) drop verdict — the extends_template Edge captures the relationship; no replacement Symbol kind. Two producers: twig.py (shape 2, dropped in PR 2 — extends_template Edge re-routed src to template file id) and blade.py (shape 3 — WI-kunag introduces a companion `extends_template` Edge with src=make_file_id(\"blade\", path) and a 5-part dangling parent-template id, so @extends directives keep representation in the graph). Both producers' Symbol emissions dropped. Status advances 2026-05-06 (WI-kunag) from UNRESOLVED to PRELIM_RESOLVED."
```

## Migration impact

- **Producer-side this PR:** seven producer migrations.
  - Sub-case (a): `twig.py:471`, `subprocess_cli.py:323`, `database_query.py:350`, `solidity_abi.py:206` — reclassify `kind` to `call_site`; `db_query` / `abi_call` / `subprocess_call` carry the prior specialisation in `meta["call_kind"]`.
  - Sub-case (b) shape (1) drops: `css.py:155`, `jsonnet.py:212`, `bitbake.py:264` — Symbol emission removed; companion Edge unchanged.
- **Registry-side this PR:** add `call_site` on `AXIS_LANGUAGE_CONSTRUCT` (Cluster A canonical). Move sub-case (a) values (`function_call`, `subprocess_call`, `db_query`, `abi_call`) and `inherit` from `endpoint_shape` to remain on `endpoint_shape` through the Phase 4a deprecation window — the audit-findings status field tracks the migration state, not the registry axis. (The registry-side prune is a Phase 4b operation gated on bakeoff validation.)
- **Schema-side:** open enum on `Symbol.kind` accommodates the additive `call_site` addition. Per ADR-0027 §"Phase 4", no SCHEMA_VERSION bump for Phase 3 fold work.
- **Consumer-side:** no immediate change. Tests asserting `kind="function_call|subprocess_call|db_query|abi_call|inherit"` migrate (test files updated in this PR). One test (`test_jsonnet.py:123`) updates to verify the `imports` Edge instead of the dropped Symbol.
- **Test-side:** ~7 test files updated. Diagnostic-test assertions in `tests/test_audit_findings.py` continue to pass (every row's status agrees with registry presence).
- **Producer-side deferred:** 8 sites for sub-case (b) shapes (2) and (3) — `json_config.py`, `r_lang.py`, `astro.py`, `puppet.py`, `scss.py`, `make.py`, `twig.py` (×3), `blade.py`. Tracked in WI-zarov follow-on PRs.

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` axis this audit applies. §"Detailed analysis" Cluster E and §"Phase 3" Cluster E are the load-bearing references.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0027 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the precedent for "shape on meta, not on the label" (§"Detailed analysis" Cluster F). Sub-case (a) carries the same discipline for `Symbol.kind`.
- Audit-findings 0009 — Cluster C precedent for "registry stays on endpoint_shape during the Phase 4a window".
- WI-runod cross-axis schedule — Wave 4 of which this PR closes one (mostly) item.
