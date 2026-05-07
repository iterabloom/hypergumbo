<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes to hypergumbo are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Released **tool** is at: v4.0.0
- Released **schema** is at: v0.4.0

This changelog tracks the **tool version** (package releases). The **schema version** is tracked separately in `schema.py` as `SCHEMA_VERSION`. The schema version changes when `docs/schema.json` has significant updates: breaking changes to the behavior map output format (minor bump) or additions like new type definitions for YAML validation (patch bump).

## [Unreleased]

### Added

- **Canonical `Edge.evidence_type` registry (ADR-0028 Phase 1)**: `packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py` enumerates 218 evidence_type values (207 currently-emitted static literals + 10 enumerable dynamic-emit variants from `websocket.py` f-strings + 1 placeholder `di_binding` for the colon-form `di_resolution.py` emit) classified across three axes — `inference_pathway` (Cluster A canonical, 107 values: pure inference labels), `endpoint_shape` (Clusters B/C/D deprecation candidates, 111 values folding to canonical + `Edge.is_resolved` / `meta["framework_dispatch"]` / `meta["call_construct"]`), and `pending_classification` (long-tail awaiting per-cluster audit-findings docs). Accessors: `all_evidence_type_names()`, `evidence_types_on_axis(axis)`, `find_evidence_type(name)`. ADR-0028 status flips from Draft to Accepted.
- **New sibling field `Edge.is_resolved: bool = True`** (ADR-0028 §"Sibling-field design call-out"): captures the resolution-status property that Cluster B `*_unresolved` evidence types previously smuggled into the inference label. Default `True` (the ~90% case); Phase 3 producers set `False` when folding `*_unresolved` evidence types to their canonical form. JSON serialization emits the field at the top-level `Edge` shape (alongside `confidence`); `from_dict` defaults missing key to `True` for backward compatibility with pre-0.4.2 behavior maps.
- **L1 consumer-side drift linter for `Edge.evidence_type`** (`scripts/check-evidence-type-drift`): mirrors `check-symbol-kind-drift` and `check-edge-type-drift`. AST-walks `packages/`, `scripts/`, and `.agent/` for module-level `*EVIDENCE_TYPE*` set assignments and verifies every value is in the canonical registry. Companion property test at `tests/test_evidence_types.py::test_every_evidence_type_named_set_is_a_subset_of_registry` provides CI gating; pre-commit hook in `.githooks/pre-commit` provides local gating from the same merge moment.
- **L3 producer-side coherence linter** (`packages/hypergumbo-core/src/hypergumbo_core/producer_coherence.py`, script `scripts/check-producer-axis-coherence`): walks `Edge.create(...)` / `Edge(...)` / `Symbol.create(...)` / `Symbol(...)` call sites and verifies that literal-string keyword arguments to `evidence_type`, `kind`, and `edge_type` are in their corresponding canonical registry. F-string emits are surfaced as advisory Phase-3 fold candidates (not strict failures). Closes the L3 producer-introduction gap left open by the L1 consumer-side drift linters across all three sibling axes (`Edge.evidence_type`, `Symbol.kind`, `Edge.edge_type`). Per ADR-0028 §"Path B" (the structural answer to "the registry must actually be authoritative, not just consultable").
- **`docs/concept-axes.md` extends to cover `Edge.evidence_type`**: `scripts/generate-concept-axes` now reads `EDGE_TYPES`, `SYMBOL_KINDS`, AND `EVIDENCE_TYPES`, emitting three top-level field sections.
- **`docs/schema.json` carries `Edge.evidence_type` `x-axis-of-values` annotation**: the registry-derived map documents canonical axis classification per value. Schema enum is intentionally **open** (`type: "string"` without enum constraint) because current production includes dynamic f-string emits that produce values outside the static registry; the closed enum returns at Phase 4b once per-cluster producer migrations land. Same posture applied retroactively to `Symbol.kind` (re-opened from the closed-enum-with-known-gap that ADR-0027 Phase 1 shipped). `Edge.edge_type` enum remains closed — pre-implementation audit confirmed zero f-string emit sites for `edge_type=`.
- **Audit-findings format extends to all three axes** (`audit_findings.py`, WI-runod Wave 1 infrastructure): `_REGISTRIES` now carries an `_AxisRegistry` binding for each declared axis, parameterising the validator's mechanical-check predicates over a per-axis `canonical_axis` (`relationship` for `Edge.edge_type`, `language_construct` for `Symbol.kind`, `inference_pathway` for `Edge.evidence_type`) and `endpoint_axis`. The format previously hard-coded `relationship` as the canonical axis, blocking audit-findings docs for the two new ADR-0027 / ADR-0028 axes.
- **Audit-findings 0003 — Symbol.kind Cluster A canonical language constructs** (WI-kozis, WI-runod Wave 1): `docs/audits/0003-symbol-kind-cluster-a-language-constructs.md` records per-value verdicts for the 50 Cluster A values seeded by ADR-0027 Phase 1. All rows CANONICAL + RESOLVED — first audit-findings doc on the `Symbol.kind` axis.
- **Audit-findings 0004 — Edge.evidence_type Cluster A canonical inference pathways** (WI-nurot, WI-runod Wave 1): `docs/audits/0004-evidence-type-cluster-a-canonical-inference.md` records per-value verdicts for the 110 Cluster A values seeded by ADR-0028 Phase 1. All rows CANONICAL + RESOLVED — first audit-findings doc on the `Edge.evidence_type` axis.
- **Session-start hook prompts about prior-session `agent_notes.json`**: `_append_agent_notes_status` in `.agent/hooks/_shared/session_start_logic.sh` checks for a non-empty `agent_notes.json` at `$HOME/<repo>_lab_notebook/guidance_log/` and appends a one-line prompt to `SESSION_START_MESSAGE` naming both the notes-file age and the last-session age (mtime of `.agent/.last_session_transcript.jsonl`), asking the agent to ask the user whether to load the handoff via `./scripts/agent-notes --show`. Closes the read-side gap in the WI-borur recovery loop: the session-end write rule (append at sign-off) previously had no symmetric fresh-session read trigger, so handoff notes accumulated unread. Hook fires in Cases 1 (mode OFF), 2 (stale PID), and 4 (mode active); skipped in Case 3 (another live session owns the autonomous lock). Notes content is **not** dumped into context unprompted — the agent asks first, preserving the user's option to skip stale handoffs. Tests: `tests/test_session_start_agent_notes.py` (9 tests). Documentation: `recover-state-playbook.md` adds a "Fresh-session read trigger" section.
- **Audit-findings 0005 — Symbol.kind Cluster B file-shape entities** (WI-gajob, WI-runod Wave 1): `docs/audits/0005-symbol-kind-cluster-b-file-shape.md` records per-value verdicts for the 17 Cluster B values currently parked on `pending_classification` (`file`, `library`, `package`, `executable`, `program`, `project`, `module_file`, `component_file`, `npm_package`, `composer_package`, `main_entry`, `library_export`, `export_entry`, `wasm_module`, `wasm_import`, `tsconfig`, `script`). Verdict shape: 6 CANONICAL promotions (build-DSL top-level constructs lift to `language_construct`), 10 FOLD to existing Cluster A canonicals + meta key (framework / ecosystem qualifiers move to `Symbol.meta`), 1 DEPRECATE-NO-FOLD candidate (`tsconfig` is redundant with the v4.0.0 `is_config_file` boolean). All rows ship at `UNRESOLVED` status; Wave 6 of WI-runod schedule is the migration. **No new axis ADR required** — the four-leakage-test pass found no separate-axis pressure that the existing meta-key fold pattern doesn't already absorb.
- **Audit-findings 0013 — Symbol.kind Cluster D framework roles** (WI-habut, WI-runod Wave 5): `docs/audits/0013-symbol-kind-cluster-d-framework-roles.md` records per-value verdicts for the 29 Cluster D values seeded by ADR-0027 Phase 1 (`event_publisher`, `event_subscriber`, `ipc_publisher`/`subscriber`/`caller`/`bridge_caller`/`ipc`, `objc_bridge`, `selector_ref`, `crypto_producer`/`consumer`, `message_sender`/`handler`, `mq_publisher`/`subscriber`, `grpc_server`/`stub`, `websocket_endpoint`/`emitter`/`listener`, `dispatcher`, `graphql_resolver`/`client`, `http_client`, `route_mount`/`route`/`route_include`, `openapi_operation`, `rpc`, `service`). All 29 rows FOLD: 26 to `function`/`method`, 1 (`service`) to `interface`/`class`, 1 (`selector_ref`) to `reference` per audit-findings 0011's `_ref` shape disposition, plus `meta["framework_role"]=<value>`. Status: Mixed — values with active producers ship UNRESOLVED, values with zero literal-grep producers ship PRELIM_RESOLVED. Phase 3 producer migration ships piecewise as per-framework sub-PRs (Wave 5 of WI-runod schedule). Companion to audit-findings 0014 — together they identify the framework-dispatch coordinated fold scope.
- **Audit-findings 0014 — Edge.evidence_type Cluster C framework dispatch** (WI-kagik, WI-runod Wave 5): `docs/audits/0014-evidence-type-cluster-c-framework-dispatch.md` records per-value verdicts for the 65 Cluster C values seeded by ADR-0028 Phase 1 (the framework-specific dispatch cluster: `airflow_framework_dispatch`, `django_orm_dispatch`, `django_signal_receiver`, `django_channels_emit`/`endpoint`, `fastapi_emit`/`endpoint`, `kafka_streams_dispatch`, `phoenix_event_match`, `tauri_invoke`/`emit_listen`, `graphql_operation_match`, `resolver_field_match`/`type_match`, `http_url_match`, `crypto_api_pattern`, `event_name_match`, `yjs_crdt_pattern`, `ipc_pattern_match`/`bridge_call`, `context_bridge_wrapper`, `objc_protocol_dispatch`, `nestjs_module_registration`, `rust_trait_dispatch`, `otp_genserver_dispatch`, `jni_naming_convention`, `pyo3_bridge`, `abi_name_match`, `controller_routes`/`router_routes`, `registry_dispatch`, `middleware_chain`, `vue_component_import`/`event_handler`, `wasm_bindgen_import`/`instantiate`, `go_cobra_dispatch`/`memberlist_delegate`, `view_template`/`implicit_convention`, `table_name_match`, `subprocess_cli_match`, `npm_package_import`, `cuda_kernel_launch`, `script_src`, `ruby_c_extension`/`ffi_attach`/`delegate`, `activerecord_association`, `job_enqueue`, `rails_block_callback`/`callback`, `orm_accessor_pattern`, …). All 65 rows FOLD: 56 to canonical inference + `meta["framework_dispatch"]=<framework_name>`, 9 to canonical inference + `meta["detection_pattern"]=<pattern_shape>` (the partition is per-row: framework-named dispatches carry `framework_dispatch`, pure pattern-detection sites carry `detection_pattern`). Status: Mixed — values with active producers ship UNRESOLVED. Companion to audit-findings 0013.
- **Audit-findings 0007 — Symbol.kind Cluster H domain long-tail** (WI-nupus, WI-runod Wave 1): `docs/audits/0007-symbol-kind-cluster-h-long-tail.md` records per-value verdicts for the 60 Cluster H values currently parked on `pending_classification` (the long tail of documentation, template, configuration, query, hardware-description, theorem-prover, and notebook DSL constructs — `section`/`paragraph`/`code_block`/`diagram` for doc DSLs; CSS at-rules and selectors; HCL/Terraform `resource`/`data`/`local`/`provider`/`output`; Mermaid `participant`/`state`/`node`; GraphQL `fragment`; SPARQL `query`/`prefix`/`base`; LaTeX `command`/`environment`/`label`; VHDL `entity`/`architecture`; Swift `subscript`/`protocol`; QML `signal`/`id`; Vue `style_block`; Svelte `event`/`block`; Twig `block`/`conditional`/`for_loop`; Handlebars `block`/`partial`; …; the registry's `structure` value is excluded from this audit and deferred to Cluster C / WI-rusit). Verdict shape: 53 CANONICAL promotions (top-level constructs in their respective DSLs lift to `language_construct`; the cross-DSL overload pattern reads like `function` or `package` across general-purpose languages, not as a leak), 0 FOLD (no sub-mode/scope qualifier shapes — Cluster G's audit already absorbed the `task`/`python_task`/`addtask` scope-qualifier shape), 7 DEPRECATE-NO-FOLD (4 dead vocabulary — `heading`, `inductive`, `theorem`, `message`; 1 ID-string-only synthetic — `model` only appears in `prisma.py:120`'s `compute_stable_id` / `make_symbol_id` arguments; the actual Symbol emitted has `kind="class"`; 2 boundary-pseudo-symbol kinds deferred pending follow-on ADR — `external_symbol` and `unresolved` smuggle a resolution-status property onto Symbol.kind, mirroring the Cluster B `*_unresolved` evidence_type pattern that ADR-0028 absorbed via `Edge.is_resolved`; final verdict awaits a parallel `Symbol.is_resolved` ADR decision). All rows ship at `UNRESOLVED` status; Wave 6 of WI-runod schedule is the migration. **No new axis ADR required** — the four-leakage-test pass refuted the Cluster D/E shape-leakage hypothesis at the producer level (`signal`, `entity`, `event`, `model` all turned out to be language-keyword emitters or ID-string-only synthetics, not framework-role qualifiers).
- **Audit-findings 0006 — Symbol.kind Cluster G build / config-shape entities** (WI-dubab, WI-runod Wave 1): `docs/audits/0006-symbol-kind-cluster-g-build-config-shape.md` records per-value verdicts for the 24 Cluster G values currently parked on `pending_classification` (`test`, `test_case`, `work_item`, `target`, `special_target`, `recipe`, `env_var`, `build_arg`, `exposed_port`, `stage`, `requirement`, `editable`, `url_requirement`, `setting`, `config`, `derivation`, `dependency`, `devDependency`, `dev-dependency`, `build-dependency`, `addtask`, `python_task`, `task`, `trigger`). Verdict shape: 15 CANONICAL promotions (Dockerfile / Make / Just / Nix / BitBake / SQL / Apex / Meson / RST / requirements.txt / pyproject / package.json / Maven / INI top-level constructs lift to `language_construct`), 5 FOLD to a sibling apex + meta key (`test_case`→`test`, `editable`/`url_requirement`→`requirement`, `devDependency`→`dependency`, `python_task`→`task`), 4 DEPRECATE-NO-FOLD (`config` — Prisma generic block placeholder where the real construct lives in `meta["block_type"]`; `dev-dependency` and `build-dependency` — dead vocabulary with no producer; `work_item` — tracker `Item.kind` value mistakenly registered against `Symbol.kind`). All rows ship at `UNRESOLVED` status; Wave 6 of WI-runod schedule is the migration. **No new axis ADR required** — the four-leakage-test pass confirmed these are language constructs in their respective build / config / data DSLs, not pressure for a separate `Symbol.role` axis.

### Changed

- **`Symbol.kind` Cluster G FOLDs (ADR-0027 §"Phase 3", WI-dubab / WI-runod Wave 6 PR 5)**: 5 framework / scope-qualified Symbol.kind values fold to canonical Cluster A constructs + a meta-key qualifier per audit-findings 0006's verdict trichotomy. Producer migrations: `test_case` → `kind="test"` + `meta["test_dialect"]="robot"` at `robot.py:314` (Robot Framework `*** Test Cases ***`); `editable` → `kind="requirement"` + `meta["install_mode"]="editable"` at `requirements.py:362` (pip `-e` form); `url_requirement` → `kind="requirement"` + `meta["install_source"]="url"` at `requirements.py:281` (pip git+/url form); `devDependency` → `kind="dependency"` + `meta["dependency_scope"]="dev"` via `json_config.py:_process_dependencies` (covers package.json devDependencies and Composer require-dev call sites at lines 693 and 853); `python_task` → `kind="task"` + `meta["task_implementation"]="python"` at `bitbake.py:346` (BitBake `python foo() {...}` task). Symbol IDs preserve the legacy kind literal in the trailing component for stable_id contract continuity. Consumer surfaces unchanged: `EXCLUDED_KINDS` already contains the canonical `dependency` (no predicate change needed for devDependency post-fold); `_DEPENDENCY_KINDS` in `cli.py` likewise. Audit-findings 0006 verdict-table rows for these 5 values advance UNRESOLVED → PRELIM_RESOLVED with diagnostic tests rewritten to assert `axis == "endpoint_shape"`. Index in `docs/audits/README.md` updated to "Mixed (15 RESOLVED, 9 PRELIM_RESOLVED)" — Cluster G is fully resolved, no UNRESOLVED rows remain. Registry entries for all 5 values move from `pending_classification` to `endpoint_shape` in `symbol_kinds.py` with rewritten descriptions naming fold target + migration PR. `SCHEMA_VERSION` patch-bumps `0.5.5` → `0.5.6` (5 `x-axis-of-values` axis reclassifications in `docs/schema.json`).
- **`Symbol.kind` Cluster G/H DEPRECATE-NO-FOLDs + theorem/inductive/message reclassification (ADR-0027 §"Phase 3", WI-runod Wave 6 PR 4)**: Closes 9 of the 16 remaining Wave 6 rows. **Cluster G** (4 DEPRECATE-NO-FOLDs advanced UNRESOLVED → PRELIM_RESOLVED): `config` (Prisma generic block placeholder; producer at `prisma.py:179` rewrites to emit `kind="block"` — canonical AXIS_LANGUAGE_CONSTRUCT — instead, with the real construct already in `meta["block_type"]`); `dev-dependency`, `build-dependency`, `work_item` (dead vocabulary, no producer). **Cluster H** (5 rows resolved): `heading` (DEPRECATE-NO-FOLD; advanced PRELIM_RESOLVED — markdown emits `kind="section"`, no producer for `heading`); `model` (DEPRECATE-NO-FOLD; advanced PRELIM_RESOLVED — ID-string-only synthetic at `prisma.py:120`, the actual emitted Symbol uses `kind="class"`); plus 3 **CANONICAL reclassifications** of `theorem`, `inductive`, `message` (originally tagged DEPRECATE-NO-FOLD by audit-findings 0007 §"Diagnostic findings #4" but those verdicts were a literal-grep blind-spot miss — all three have real producers via `add_symbol(node, name, "<kind>")` / `_make_proto_symbol(..., "<kind>", ...)` indirection). Producers: Lean theorems and lemmas at `lean.py:222,231` plus TLA+ theorems at `tlaplus.py:207` (`theorem`); Lean inductive type declarations at `lean.py:247` (`inductive`); Protobuf message declarations at `proto.py:260` (`message`). All three values move from `pending_classification` to `language_construct` in `symbol_kinds.py`; audit-findings 0007 verdict rows reclassified DEPRECATE-NO-FOLD → CANONICAL with status RESOLVED; the §"Diagnostic findings #4 — Dead vocabulary" section is rewritten and gains a "Reclassification correction (Wave 6 PR 4)" sub-section explaining the blind-spot family (same family as WI-nubuv extension A surfaced for assignment-form / f-string producers). The 2 boundary-pseudo-symbol kinds (`external_symbol`, `unresolved`) remain UNRESOLVED pending the parallel `Symbol.is_resolved` ADR decision per audit-findings 0007 §"Diagnostic findings #3". Index in `docs/audits/README.md` updated to "Mixed (15 RESOLVED, 4 PRELIM_RESOLVED, 5 UNRESOLVED)" for 0006 and "Mixed (56 RESOLVED, 2 PRELIM_RESOLVED, 2 UNRESOLVED)" for 0007. `SCHEMA_VERSION` patch-bumps `0.5.4` → `0.5.5` (9 `x-axis-of-values` axis reclassifications in `docs/schema.json`).
- **`Symbol.kind` Cluster B FOLDs (ADR-0027 §"Phase 3", WI-gajob / WI-runod Wave 6 PR 3)**: 11 framework / ecosystem-qualified file/package/export/module Symbol.kind values fold to canonical Cluster A constructs + a meta-key qualifier per audit-findings 0005's verdict trichotomy. **10 FOLDs** with producer migrations: `module_file` → `kind="file"` + `meta["module_system"]` ('esm' / 'commonjs', resolved from file extension) at `js_module.py:763`; `component_file` → `kind="file"` + `meta["component_framework"]="vue"` at `vue_component.py:195`; `npm_package` → `kind="package"` + `meta["package_ecosystem"]="npm"` at `js_module.py:819`; `composer_package` → `kind="package"` + `meta["package_ecosystem"]="composer"` at `json_config.py:814`; `main_entry` → `kind="file"` + `meta["entry_role"]="main"` at `json_config.py:415`; `export_entry` → `kind="export"` + `meta["export_source"]="package_exports_map"` at `json_config.py:529,586` (×2); `script` → `kind="file"` + `meta["entry_role"]="script"` at `json_config.py:250` and `toml_config.py:579`; `wasm_module` → `kind="module"` + `meta["compilation_target"]="wasm"` at `wasm_bindgen.py:400`; `wasm_import` → `kind="import"` + `meta["compilation_target"]="wasm"` at `wasm_bindgen.py:266` (note: the fold target `import` is itself a deprecation-window kind per audit-findings 0010 — wasm_bindgen requires the synthetic node for slicer BFS continuity, so this is the one Cluster B FOLD where the target is also on AXIS_ENDPOINT_SHAPE). **1 DEPRECATE-NO-FOLD**: `tsconfig` drops the kind specialisation at `json_config.py:735` and emits `kind="file"` + `is_config_file=True` + `meta["config_format"]="tsconfig"` instead — the v4.0.0 `is_config_file` boolean already records the same fact at a different point in the schema. **1 vacuous migration**: `library_export` is registered on `Symbol.kind` but only emitted as `UsageContext.kind` (`js_ts.py:1930,1950`) — a separate axis. The Symbol.kind registry entry was a Phase-1 seed error; the entry moves to AXIS_ENDPOINT_SHAPE for symmetry with the rest of the Cluster B fold cohort. **Consumer dual-shape work**: `route_handler.py:is_component` extends to match post-fold `kind="file"` + `meta["module_system"]` alongside the legacy `kind="module_file"` literal; `cli.py:_is_noise` extends to catch post-fold `kind="file"` + `meta["entry_role"]="script"` alongside the legacy `kind="script"` literal. `selection.filters.EXCLUDED_KINDS` needs no predicate change because the fold targets (`file`, `package`) are already in the set; the legacy literals (`script`, `npm_package`, `module_file`) stay through the Phase 4a deprecation window. Symbol IDs preserve the legacy kind literal in the trailing component (e.g., `javascript:src/utils.js:module_file:1:utils`) for stable_id contract continuity. Audit-findings 0005 verdict-table rows for these 11 values advance UNRESOLVED → PRELIM_RESOLVED with diagnostic tests rewritten to assert `axis == "endpoint_shape"`. Index in `docs/audits/README.md` updated to "Mixed (6 RESOLVED, 11 PRELIM_RESOLVED)". Registry entries for all 11 values move from `pending_classification` to `endpoint_shape` in `symbol_kinds.py` with rewritten descriptions naming the fold target and migration PR. `SCHEMA_VERSION` patch-bumps `0.5.3` → `0.5.4` (11 `x-axis-of-values` axis reclassifications in `docs/schema.json`).
- **`Symbol.kind` Clusters G + H canonical promotions (ADR-0027 §"Phase 3", WI-dubab / WI-nupus / WI-runod Wave 6 PR 2)**: 68 values move from `pending_classification` to `language_construct` in the registry. Cluster G (15): `target`, `special_target`, `recipe`, `env_var`, `build_arg`, `exposed_port`, `stage`, `derivation`, `addtask`, `task`, `trigger`, `test`, `requirement`, `dependency`, `setting` — top-level constructs in Make / Just / Dockerfile / Nix / BitBake / SQL / pyproject / package.json / Maven build-DSLs. Cluster H (53): `section`, `paragraph`, `code_block`, `diagram`, `plot`, `yield`, `for_loop`, `conditional`, `block`, `prefix`, `base`, `query`, `entry`, `entity`, `architecture`, `participant`, `state`, `fragment`, `partial`, `provider`, `local`, `style_block`, `permission`, `keyframes`, `media`, `font_face`, `class_selector`, `id_selector`, `rule_set`, `subdirectory`, `table`, `table_array`, `link`, `label`, `command`, `environment`, `binding`, `id`, `source`, `port`, `output`, `input`, `value`, `pattern`, `subscript`, `signal`, `data`, `resource`, `event`, `protocol`, `index`, `node`, `playbook` — top-level constructs in markdown / Mermaid / GraphQL / SPARQL / Twig / Vue / Svelte / HCL / Terraform / Handlebars / VHDL / Swift / QML / RST / LaTeX / TOML / Ansible domain DSLs. Per audit-findings 0006 §"verdict shape" and 0007 §"verdict shape", these are language-level constructs in their own tongue (the four-leakage-test pass refuted the Cluster D/E shape-leakage hypothesis at the producer level for the `signal` / `entity` / `event` / `model` candidates — they're language-keyword emitters, not framework-role qualifiers). No producer code change required (only registry axis classification changes). Audit-findings 0006 verdict-table rows for the 15 G CANONICAL values + 0007 for the 53 H CANONICAL values advance UNRESOLVED → RESOLVED with diagnostic tests rewritten to assert `axis == "language_construct"`. Index in `docs/audits/README.md` updated to "Mixed (15 RESOLVED, 9 UNRESOLVED)" for 0006 and "Mixed (53 RESOLVED, 7 UNRESOLVED)" for 0007. The remaining 9 G rows (5 FOLD + 4 DEPRECATE-NO-FOLD) and 7 H rows (all DEPRECATE-NO-FOLD) need producer migrations and ship in subsequent Wave 6 PRs. `SCHEMA_VERSION` patch-bumps `0.5.2` → `0.5.3` (68 `x-axis-of-values` axis reclassifications in `docs/schema.json`).
- **`Symbol.kind` Cluster B canonical promotions (ADR-0027 §"Phase 3", WI-gajob / WI-runod Wave 6 PR 1)**: Six values move from `pending_classification` to `language_construct` in the registry: `file`, `library`, `package`, `executable`, `program`, `project`. Per audit-findings 0005 §"1. Build-DSL top-level constructs are language constructs in their own tongue", these are top-level declarations in CMake / Meson / COBOL / Pascal / Fortran / VHDL / Cargo / json_config — language-level constructs in their own tongue. Test 4 (mechanism vs. category) confirms each names a *category* of construct, not a *mechanism* qualifier. No producer code change required (the producers already emit these literal kinds; only the registry's axis classification changes). Audit-findings 0005 verdict-table rows for these six values advance UNRESOLVED → RESOLVED with diagnostic tests rewritten to assert `axis == "language_construct"`. Index in `docs/audits/README.md` updated to "Mixed (6 RESOLVED, 11 UNRESOLVED)". The remaining 11 rows (10 FOLDs + 1 DEPRECATE-NO-FOLD `tsconfig`) need producer migrations and ship in subsequent Wave 6 PRs. `SCHEMA_VERSION` patch-bumps `0.5.1` → `0.5.2` (six `x-axis-of-values` axis reclassifications in `docs/schema.json`).
- **`Symbol.kind` Phase-2 consumer migration slice 2 (ADR-0027 §"Phase 2", WI-jukav slice 2)**: `selection.filters` gains `is_excluded_kind(kind, meta=None)` — a dual-shape predicate forward-compatible with ADR-0027 §"Phase 3" Wave 5 framework_role fold. Pre-fold producers emit `Symbol.kind=<legacy_role>` (e.g. `"event_subscriber"`); post-fold producers emit `Symbol.kind` in `{function, method}` + `Symbol.meta["framework_role"]=<legacy_role>`. The predicate matches either shape, so the exclusion filter survives the producer-side fold without inflating selection / compact output with framework-emitted synthetics. Three consumer sites migrate from `kind not in EXCLUDED_KINDS` set membership to `not is_excluded_kind(kind, meta)`: `compact.py:_render_compact_view` (×2), `cli.py:_emit_centrality_table`. Slice 2 verdict for `containment.{CONTAINABLE_KINDS, CONTAINER_KINDS}`: no dual-shape predicate needed — the post-fold canonicals (`method` for Cluster D `rpc`; `interface`/`class` for `service`) are already in those sets, and Wave-5 framework-linker producers don't reach this filter (their synthetic-node names lack class-qualifier separators, so `_extract_parent_name` skips them). Audit comments updated to record the verdict. Mirrors the slice 1 dual-shape idiom (`linkers/registry._is_synthetic_node`) introduced in PR #3562. 8 new tests at `tests/test_selection_filters.py::TestIsExcludedKind` exercise both shapes plus the false-positive control paths (bare `method`/`function` not excluded; non-`{function, method}` kinds with role meta not excluded).
- **L3 producer-coherence linter gains assignment-form trace (WI-nubuv extension A)**: `producer_coherence.py`'s `Symbol(kind=…)` / `Edge.create(evidence_type=…)` / `Edge.create(edge_type=…)` matcher now resolves keyword-argument `Name` references to candidate literals by walking back simple assignment patterns within the enclosing function — direct literal (`name = "literal"`), ternary (`name = "a" if cond else "b"`), and if/else assignment chains. Conservative posture: any unresolvable RHS in scope poisons the resolution to silent-skip, so function parameters, dict-lookups, and computed values stay out of the strict gate. Closes the WI-nitil (assignment-form gRPC/MQ) and phoenix_ipc.py (f-string-form Phoenix Channels) family of producer leaks at lint time rather than retroactive bakeoff sweep. Surfaced 18 latent leaks on landing; all registered AXIS_PENDING in their respective registries (`symbol_kinds.py`: `binary`, `benchmark`, `example`, `workspace`, `handler`, `pattern_rule`, `helper`, `uniform`, `varying`, `private`, `storage`, `workgroup`; `evidence_types.py`: `cffi_call`, `ctypes_call`, `alias_resolution`, `import_resolution`, `ast_call_method`) or as canonical `AXIS_RELATIONSHIP` (`edge_types.py`: `constrains` for pip `-c` constraint files). Each carries the cluster-X audit pointer for follow-on classification. Tests: 11 new fixtures at `tests/test_producer_coherence.py` exercise the catch path (literal/ternary/if-else chain → strict) and the false-positive control path (parameter / nested-function-scope-isolated / unresolvable-RHS → silent). Per WI-nubuv §"Recommended order" — extension B (f-string strict mode) and C (ban-the-variable-form structural backstop) remain deferred.
- **`SCHEMA_VERSION` patch-bumps `0.5.0` → `0.5.1`** (WI-nubuv ext A): 18 additive canonical registry entries widen the `Symbol.kind` / `Edge.evidence_type` / `Edge.edge_type` enums in `docs/schema.json`. All existing values remain valid; no consumer-visible breaking change.
- **Audit-findings 0013 + 0014 row advance UNRESOLVED → PRELIM_RESOLVED (Wave 5 producer-migration completion)**: with the WI-nitil assignment-form correction (gRPC + MQ) and the phoenix_ipc.py f-string fold (selfh PR #174) closing the two known producer-coherence-linter blind spots, Phase 3 producer migration is empirically complete for both audit-findings 0013 (Cluster D framework_role, 33 rows) and 0014 (Cluster C framework_dispatch, 65 rows). All 28 + 65 UNRESOLVED rows advance to PRELIM_RESOLVED; doc Status headers advance from "Mixed" to "All rows PRELIM_RESOLVED" with a one-paragraph provenance summary referencing the six Wave 5 PRs + the WI-nitil and phoenix_ipc follow-ons. Per-row registry deprecation tagging is Phase 4a (already shipped as selfh PR #173, WI-tikuh / WI-jubak); registry pruning is Phase 4b (WI-porim / WI-butol), gated on per-cluster bakeoff validation.
- **`linkers/phoenix_ipc.py` Symbol.kind f-string fold (ADR-0027 Phase 3 follow-on)**: phoenix_ipc.py:279 emitted `kind=f"ipc_{pattern.type}"` (expanding to `ipc_send` / `ipc_receive`) inside a `Symbol(...)` constructor call — a producer that Wave 5 missed because the L3 producer-coherence linter only catches literal-form kwargs. Same family of miss as WI-nitil corrected for assignment-form gRPC/MQ producers. Folds to canonical `kind="function"` + `meta["framework_role"]=f"ipc_{pattern.type}"`, mirroring the linkers/ipc.py shape. Two BRANCHES tests updated from `s.kind == "ipc_send"|"ipc_receive"` to the post-fold `(s.meta or {}).get("framework_role") == ...` shape. With this site closed, audit-findings 0013 §"Diagnostic findings" producer-state empirically matches "all in-scope rows have either zero live producers or producers that emit only post-fold canonical kinds".
- **`SCHEMA_VERSION` minor-bumps `0.4.2` → `0.5.0`** (ADR-0027 / ADR-0028 §"Phase 4a", WI-tikuh / WI-jubak): announces the additive deprecation of every `endpoint_shape` value on the `Symbol.kind` and `Edge.evidence_type` axes. `scripts/generate-schema` now emits an `x-deprecated` annotation on `#/$defs/Symbol/properties/kind` (50 entries) and on `#/$defs/Edge/properties/meta/properties/evidence_type` (111 entries), mirroring the pre-existing `Edge.type` Phase 4a annotation shape (25 entries, ADR-0023). Values stay valid in the open schema for the deprecation window — the schema enum constraint is not added, so this is a purely additive change. Per-value migration guidance lives in the registry's `SymbolKindSpec.description` / `EvidenceTypeSpec.description`. Phase 4b (registry pruning + closed enum return) ships piecewise as each Phase 3 cluster's `awaits_bakeoff_validation` tag clears, per WI-porim / WI-butol.
- **Audit-findings 0010 Status header advances from "Mixed" to "All rows PRELIM_RESOLVED"**: cosmetic documentation polish following the WI-kunag PR. With sub-case (b) `import` / `include` / `extends` rows now PRELIM_RESOLVED alongside sub-case (a) and the earlier sub-case (b) advances, every row in the verdict table is at PRELIM_RESOLVED and the prose top-of-file Status field is updated to match. Row-level statuses (which the audit-findings format check enforces) are unchanged; only the prose summary is reworded.
- **`Symbol.kind` Cluster E sub-case (b) shape-3 companion-Edge redesign (audit-findings 0010, WI-kunag)**: Four shape-3 producers (no companion Edge) advance from UNRESOLVED to PRELIM_RESOLVED by introducing a companion Edge so the syntactic relationship keeps representation in the graph. `r_lang.py:215` (R `library()` / `require()` package imports) emits an `imports` Edge with src=`make_file_id("r", path)` and a 5-part dangling package id; `astro.py:209` (Astro frontmatter `import …` syntax) emits an `imports` Edge with src=`make_file_id("astro", path)` and a 5-part dangling file id (distinct from the existing component_ref `imports` Edge — frontmatter-form carries `meta.import_name`, component-ref-form carries `meta.component_name`); `make.py:282` (makefile `include` directive) emits an `includes` Edge with src=`make_file_id("make", path)` and a 5-part dangling included-file id; `blade.py` (Blade `@extends`) emits an `extends_template` Edge with src=`make_file_id("blade", path)` and a 5-part dangling parent-template id. All four producers' per-import / per-include / per-extends Symbols are dropped. Audit-findings 0010 §"Verdicts" `import` / `include` / `extends` rows advance from UNRESOLVED to PRELIM_RESOLVED. Tests refactored from Symbol-side to Edge-side assertions.
- **`Pattern.framework_role` field replaces the `Symbol.kind` backward-compat shim (WI-fojun)**: Wave 5 PR #6 introduced a backward-compat shim in `Pattern.matches()` (lines 396-401, 423-428) that fell back to `meta["framework_role"]` when the configured `symbol_kind` regex didn't match `Symbol.kind`. The shim let existing framework YAMLs keep their `symbol_kind: "^route$"` rules working without per-YAML migration, but it overloaded `symbol_kind` semantics — the field nominally targets a symbol's syntactic construct (`function`, `class`, `interface`) yet the shim quietly redirected it to a framework-participation property. WI-fojun adds a dedicated `framework_role: str | None = None` field on `Pattern` with its own compiled-regex matcher, migrates the four YAML rules that referenced folded role names (`framework_role: "^route$"` in `laravel.yaml`, `phoenix.yaml`, `rails.yaml`, `sinatra.yaml`), and removes the shim so `symbol_kind` and `framework_role` are now disjoint. Both fields can coexist on a single Pattern (AND condition) when needed. New result key `matched_framework_role` (parallel to `matched_symbol_kind`) is set when the field matches. ADR-0027 §"Phase 3" Cluster D fold-residue layer at the YAML pattern surface; closes the WI-fojun follow-on noted in CHANGELOG entry for Wave 5 PR #6.
- **`Symbol.kind` Cluster D gRPC + message-queue framework-role fold (audit-findings 0013 correction, WI-nitil)**: Wave 5 follow-on. The original audit-findings 0013 literal-grep diagnostic (`grep -rn 'kind=["\047]<value>["\047]'`) only matched kwarg-form producers and missed assignment-form producers (`kind = "<value>" if … else "<value>"` followed by `Symbol(kind=kind, …)`). Broader-grep re-sweep at 2026-05-06 found four such producers: `linkers/grpc.py:660,663` for `grpc_stub` / `grpc_server` (also unregistered values `grpc_service` / `grpc_servicer` / `grpc_client` emitted by the same block) and `linkers/message_queue.py:410` for `mq_publisher` / `mq_subscriber`. All seven gRPC and MQ kinds now fold to canonical Cluster A constructs: `grpc_service` → `kind="interface"`, `grpc_servicer` / `grpc_server` → `kind="class"`, `grpc_stub` / `grpc_client` / `mq_publisher` / `mq_subscriber` → `kind="function"`, all with `meta["framework_role"]=<value>`. Symbol IDs preserve the framework-role disambiguator for stable identity. Internal `linkers/grpc.py` consumer checks (`if sym.kind == "grpc_service"`, `if sym.kind in ("grpc_server", "grpc_servicer")`) migrate to `meta.framework_role` lookups. Three previously-unregistered values (`grpc_service`, `grpc_servicer`, `grpc_client`) added to `symbol_kinds.py` on `AXIS_ENDPOINT_SHAPE` to satisfy the audit-findings <-> registry consistency check; they stay through the Phase 4a deprecation window. `cli.py:_count_related_endpoint_kinds` extends to look at `meta.framework_role` first (post-fold shape) and fall back to `kind` (legacy unfolded shape) so the routes-not-found fallback hint counts correctly across both shapes. Audit-findings 0013 §"Diagnostic findings" #3 corrected: the zero-producer placeholder list shrinks from nine values to five (`ipc_subscriber`, `websocket_emitter`, `websocket_listener`, `rpc`, `service` re-verified). Diagnostic-test patterns updated to the assignment-aware `kind\s*=\s*["\047]<value>["\047]` form.
- **`Symbol.kind` Cluster D framework-role + `Edge.evidence_type` Cluster C framework-dispatch coordinated fold (ADR-0027/0028 §"Phase 3", WI-habut/WI-kagik Wave 5)**: Six PRs ship the producer migration end-to-end. ~28 `Symbol.kind` framework-role values fold to `kind="function"` (or `"method"`/`"interface"`/`"class"`/`"reference"` per audit-findings 0013 verdict) + `meta["framework_role"]=<value>`; ~75 `Edge.evidence_type` framework-dispatch values fold to canonical inference (`ast_call_direct`, `ast_decorator`, `ast_import`, `naming_convention`) + `meta["framework_dispatch"]=<framework_name>` or `meta["detection_pattern"]=<pattern_name>` per audit-findings 0014 partition. PR breakdown:
    - **PR #1** (Codeberg #3572, commit 2e4721c59): websocket / tauri / grpc / openapi families. `linkers/websocket.py` f-string emit (`{pattern_type}_emit`/`_endpoint` × 5 × 2 = 10 evidence_type rows) folds to `ast_call_direct` + structured `meta["framework_dispatch"]={pattern→framework}`. `linkers/tauri_ipc.py` (4 framework_role kinds + 3 framework_dispatch evidence_types). `linkers/grpc.py` (4 evidence_types — Symbol.kind="route" deferred to PR #6). `linkers/openapi.py` (1 framework_role + 2 framework_dispatch).
    - **PR #2** (selfh #162, commit 6b015da2d): graphql / http / crypto / ipc / objc families. `linkers/{graphql,graphql_resolver,http,crypto_flow,ipc,swift_objc}.py` migrate ~9 framework_role kinds + ~7 framework_dispatch evidence_types. `selector_ref` folds to `kind="reference"` per audit-findings 0013 §"Diagnostic findings" #2.
    - **PR #3** (selfh #163, commit cc26061): event-dispatch / Go-route_mount families. `linkers/{event_sourcing,message_dispatch,yjs_crdt,annotation_convention}.py` + `hypergumbo_lang_mainstream/{go.py,play_routes.py}` migrate ~5 framework_role kinds + ~5 framework_dispatch evidence_types. `event_sourcing.py` introduces a stable variable rename (`framework_role` literal flows from id construction into both meta dict and consumer indexes).
    - **PR #4** (selfh #164): Ruby / Django framework_dispatch fold. 10 evidence_type values across 5 producer files (`linkers/{django_orm_dispatch,orm,ruby_ffi}.py`, `hypergumbo_lang_mainstream/{ruby,py}.py`); 17 emit sites total. `django_signal_receiver` folds to `ast_decorator` (the only Cluster C ast_decorator row in PR #4); the rest fold to `ast_call_direct` + `framework_dispatch` per row (`activerecord_association`, `job_enqueue`, `rails_block_callback`, `rails_callback`, `ruby_delegate`, `ruby_ffi_attach`, `ruby_c_extension`, `orm_accessor`, `django_orm`, `django_signal`).
    - **PR #5** (selfh #165, commit f052795fb / e40940c94): single-row dispatch modules batched. 27 evidence_type values across 25 producer files (`linkers/{airflow_framework_dispatch,kafka_streams_dispatch,jackson_dispatch,phoenix_ipc,rust_trait_dispatch,otp,jni,pyffi,solidity_abi,controller_routes,router_routes,decorator_dispatch,middleware_chain,di_resolution,vue_component,vue_template_method,wasm_bindgen,go_cobra,go_memberlist,view_template,database_query,subprocess_cli,js_module}.py`, `hypergumbo_lang_common/cuda.py`, `hypergumbo_lang_mainstream/{html,js_ts}.py`). 6 detection_pattern rows (`abi_name_match`, `phoenix_event_match`, `jni_naming_convention`, `implicit_convention`, `table_name_match`, `subprocess_cli_match`) fold to `naming_convention` / `ast_call_direct` + `meta["detection_pattern"]`. Side-effect: cuda.py's non-kernel branch previously emitted unregistered literal `cuda_call` via ternary (hidden from L3 by f-string-style branch); folded to `ast_call_direct` (no meta).
    - **PR #6** (selfh #166, commit 2c6f57dc6): `Symbol.kind="route"` sweep. The highest-blast-radius value in Cluster D — 17 source files emit kind=route, 14 production consumers filter on it, ~330 sites total when test fixtures + filters are included. Producers fold to `kind="function"` + `meta["framework_role"]="route"` (the `make_symbol_id(kind="route", …)` calls retained for stable identity — the ID's `:route` suffix preserves identity across migration). Consumer migration replaces `s.kind == "route"` with `(s.meta or {}).get("framework_role") == "route"` across `cli.py`, `entrypoints.py`, `framework_patterns.py`, `linkers/{grpc,http,openapi,route_handler}.py`, `js_ts.py`. `route_handler.py`'s three `is_handler` helpers gain a defensive guard excluding folded routes from handler resolution. `framework_patterns.py` Pattern.matches() also extended: `symbol_kind` regex now matches against `meta["framework_role"]` as a fallback, keeping existing framework YAML rules (`symbol_kind: ^route$` in phoenix.yaml / falcon.yaml / yesod.yaml / library-exports.yaml / …) working without per-YAML migration. Removing the shim is tracked as WI-fojun (P3 todo_soft).
  All Wave 5 PRs ship at PRELIM_RESOLVED on the producer-migration layer of audit-findings 0013/0014. Per-row registry deprecation tagging is Phase 4a (WI-jubak / WI-tikuh); registry pruning is Phase 4b (WI-porim / WI-butol). PRs #2-#6 shipped via CI failover (Codeberg PR-creation API was returning HTTP 500 throughout the session); selfh #162-166 await disengage repatriation when Codeberg recovers.
- **`Edge.evidence_type` Cluster D apex/peer call-construct fold (ADR-0028 §"Phase 3", WI-nibis Wave 4)**: 28 call-construct evidence_type peer values (`function_call`, `method_call`, `function_application`, `pipe_call`, `local_call`, `remote_call`, `constructor_call`, `object_creation`, `method_call_field_chain`, `method_call_recovery`, `method_call_typed`, `method_call_type_inferred`, `method_group`, `bare_method_call`, `receiver_call`, `typed_field_call`, `typed_receiver_call`, `stdlib_method_call`, `external_receiver_call`, `ambiguous_method_call`, `chained_return_type_call`, `cross_file_call`, `cross_file_message_send`, `function_application_external`, `remote_call_external`, `unexported_method_call`, `macro_body_call`, `call`) collapse to canonical apex `evidence_type="ast_call"` + structured `meta["call_construct"]` (with secondary keys `meta["receiver"]`, `meta["resolution_quality"]`, `meta["visibility"]` per the audit-findings rationale). The lone non-`ast_call` apex is `cross_file_message_send`, which folds to `evidence_type="message_send"` + `meta["call_construct"]="cross_file"` (`message_send` is the existing Cluster A canonical for Objective-C / Smalltalk-style message-passing). 89 emit sites across 26 producer files migrated: `bash.py`, `clojure.py`, `commonlisp.py`, `cpp.py`, `csharp.py`, `dart.py`, `elixir.py`, `elm.py`, `erlang.py`, `fsharp.py`, `go.py`, `groovy.py`, `haskell.py`, `julia.py`, `kotlin.py`, `lua.py`, `objc.py`, `ocaml.py`, `perl.py`, `py.py`, `ruby.py`, `rust.py`, `scala.py`, `swift.py`, `wolfram.py`, `linkers/method_call_recovery.py`. Apex name resolution: `ast_call` chosen over `function_call` (ADR-0028 Open Question 1) for symmetry with the `ast_*` family. The 28 peer values stay in the registry on AXIS_ENDPOINT_SHAPE through the Phase 4a deprecation window per ADR-0028 §"Phase 4". Audit-findings 0012 records the per-row FOLD verdicts (`docs/audits/0012-evidence-type-cluster-d-call-construct.md`); all rows ship at `PRELIM_RESOLVED`. Tests: 11 test files updated from `evidence_type == "<peer>"` assertions to the new `(evidence_type, meta["call_construct"], …)` shape.
- **`Symbol.kind` Cluster F component refs DEPRECATE-NO-FOLD (ADR-0027 §"Phase 3", WI-mihiz Wave 4)**: Three component_ref Symbol producers (`vue.py`, `svelte.py`, `astro.py`) drop their per-reference Symbol emission and re-route the companion `imports` Edge `src` from the dropped Symbol id to `make_file_id(<lang>, rel_path)`. Per-reference metadata (`directives` / `events` / `client_directive` / `attributes` / `has_slot_attr`) plus the new `component_name` and `source_path` keys move from Symbol.meta to Edge.meta. Edges emit unconditionally — when there's no companion import statement (unresolved local component reference), dst falls back to a 5-part dangling component id (`{lang}:component:{Name}:0-0:{Name}:component`) so the relationship keeps representation in the graph. The `vue_component` linker's source-path lookup gains a `meta["source_path"]` fallback to handle file-id `src` values introduced by this refactor; pre-refactor edges still resolve via the existing `symbol_path_map`. Audit-findings 0011 (`docs/audits/0011-symbol-kind-cluster-f-component-refs.md`) records the per-row verdicts: `component_ref` ships at PRELIM_RESOLVED with DEPRECATE-NO-FOLD (the original ADR-0027 fold target `reference` was deprecated by audit-findings 0010 sub-case (b); this audit applies the parallel disposition); `component`/`slot`/`prop`/`view` are CANONICAL + RESOLVED on `language_construct`; `component_file` is deferred to audit-findings 0005 Cluster B. Tests: 9 test files refactored from Symbol-side to Edge-side assertions.
- **`Symbol.kind` Cluster E sub-case (b) shape-2 edge-endpoint redesign (ADR-0027 §"Phase 3", WI-zarov Wave 4 PR 2)**: Six remaining shape-2 producer Symbol emissions dropped after re-routing the corresponding Edge endpoints off the dropped Symbol id. `json_config.py` (tsconfig project references): `references` Edge dst now uses a 5-part dangling tsconfig file id; `reference_path` moves from Symbol.meta to Edge.meta. `puppet.py` (manifest includes), `scss.py` (mixin includes), `twig.py` (×3 sites: `{% extends %}`, `{% include %}`, `{{ include() }}`): edges re-route src to the analyzer's file id via `make_file_id()`; per-shape meta (`class_name` / `mixin_name` / `template`) moves to Edge.meta; the function-form `{{ include() }}` is distinguished from the directive form via `meta['form']='function'`. Edges are emitted unconditionally — when the target isn't in the local registry (unresolved class/mixin), dst falls back to a 5-part dangling id (handled by IR boundary materialization) so unresolved-include cases keep their representation in the graph. `reference` registry entry advances from UNRESOLVED to PRELIM_RESOLVED in audit-findings 0010. PR 2 re-inspection also corrects the original shape-2 classification of `astro.py:209` to shape-3 (the imports Edge in astro.py:332 belongs to the separate component_ref Symbol, not the import Symbol). Three rows (`import`, `include`, `extends`) remain UNRESOLVED — each with one residual shape-3 producer (r_lang.py / make.py / blade.py) deferred. Tests: 8 test files refactored from Symbol-side to Edge-side assertions.
- **`Symbol.kind` Cluster E sub-case (a) call_site reclassification + sub-case (b) clean drops (ADR-0027 §"Phase 3", WI-zarov Wave 4)**: `call_site` added to `symbol_kinds.py` on `AXIS_LANGUAGE_CONSTRUCT` as a new Cluster A canonical naming the call-expression syntactic construct (distinct from the relationship captured by an Edge of `edge_type='calls'`). Four sub-case (a) producers reclassify their Symbol kind to `call_site` with the prior specialisation moved to `meta['call_kind']`: `linkers/subprocess_cli.py` (`subprocess_call` → `call_site` + `call_kind='subprocess'`), `linkers/database_query.py` (`db_query` → `call_site` + `call_kind='db_query'`), `linkers/solidity_abi.py` (`abi_call` → `call_site` + `call_kind='abi'`), `hypergumbo_lang_extended1/twig.py` (`function_call` → `call_site` + `call_kind='function'`). Three sub-case (b) producer Symbol emissions dropped (the relationship already lives on the corresponding Edge whose endpoints are independent of the dropped Symbol): `hypergumbo_lang_mainstream/css.py` (per-import Symbol — `imports` Edge stays with `dst=raw_path`), `hypergumbo_lang_extended1/jsonnet.py` (per-import Symbol — `imports` Edge stays with `src=jsonnet:{file}, dst=jsonnet:import:{path}`), `hypergumbo_lang_extended1/bitbake.py` (per-class inherit Symbol — `inherits` Edge stays with `src=bitbake:{file}, dst=bitbake:class:{cls}`). `inherit` registry entry moves from `language_construct` to `endpoint_shape`. Audit-findings 0010 records the per-row verdicts (`docs/audits/0010-symbol-kind-cluster-e-edge-label-kinds.md`); 7 rows ship at PRELIM_RESOLVED (sub-case (a) + sub-case (b) clean-drop bitbake `inherit` + `call`/`read`/`write` zero-producer), 5 rows at UNRESOLVED with shape-classified per-producer rationale (`reference`, `import`, `include`, `extends` partial migration; sub-case (b) edge-endpoint-dependent and edge-absent producers deferred to follow-on PRs). Tests: `test_subprocess_linker.py`, `test_database_query_linker.py`, `test_solidity_abi_linker.py`, `test_twig.py` updated to assert `call_site` + `call_kind` meta; `test_css_analyzer.py`, `BRANCHES_test_css.py`, `test_jsonnet.py`, `test_bitbake.py` refactored from Symbol-side to Edge-side assertions for the dropped values.
- **`Symbol.kind` Cluster C apex/peer registry classification (ADR-0027 §"Phase 3", WI-rusit Wave 4)**: Four apex/peer registry entries (`fn`, `var`, `proc`, `structure`) move from `language_construct` / `pending_classification` to `endpoint_shape` to reflect that producer migration is structurally complete (no analyzer emits the peer values; an empirical grep confirms canonical apex emits only). Audit-findings 0009 records the per-row FOLD verdicts (`docs/audits/0009-symbol-kind-cluster-c-apex-peer.md`); all rows ship at PRELIM_RESOLVED. The peer entries remain in the registry through the Phase 4a deprecation window per ADR-0027 §"Phase 4". No producer code change, no consumer code change — this is a registry classification update plus the audit-findings doc that catches up with a producer state that historically converged on canonical apexes (the peer labels persist only in stable_id / symbol_id strings, opaque to consumers).
- **`Edge.evidence_type` Phase-3 Cluster B canary producer fold (ADR-0028 §"Phase 3", WI-nunal Wave 3)**: 18 `*_unresolved` / `unresolved_*` evidence_type emit sites across 11 producer files migrated to `evidence_type=<canonical>` + `Edge.is_resolved=False`. First production write of the new `is_resolved` field per ADR-0028 §"Sibling-field design call-out" — validates the schema round-trip + L3 producer-coherence linter behavior on real producer code. Two new Cluster A canonical entries added to `evidence_types.py` (`grpc_stub_resolution`, `luajit_ffi_lookup`) for emit sites that had no prior canonical inference label. Migrated files: `analyze/base.py`, `linkers/{lua_ffi,ruby_ffi,grpc}.py`, `hypergumbo_lang_common/elixir.py`, `hypergumbo_lang_mainstream/{csharp,rust,py,go,kotlin,js_ts,java}.py`. The 18 Cluster B values stay in the registry on AXIS_ENDPOINT_SHAPE through the Phase 4a deprecation window per ADR-0028 §"Phase 4". Audit-findings 0008 records the per-row FOLD verdicts (`docs/audits/0008-evidence-type-cluster-b-resolution-status.md`); all rows ship at `PRELIM_RESOLVED`.
- **`Symbol.kind` Phase-2 consumer migration begins (ADR-0027 §"Phase 2", WI-jukav slice 1)**: `linkers/registry.SYNTHETIC_KINDS` consumer migrates to a dual-shape predicate `_is_synthetic_node(sym)` that recognises both the pre-Wave-5 emit shape (Cluster D framework_role on `Symbol.kind`) and the post-Wave-5 shape (`Symbol.kind="function"|"method"` + `Symbol.meta["framework_role"]=<value>`). The consumer's `if sym.kind not in SYNTHETIC_KINDS` test is replaced by `if not _is_synthetic_node(sym)`; existing callers of `SYNTHETIC_KINDS` continue to work (the frozenset still exports the role-name vocabulary). Two new tests at `tests/test_linker_registry.py::TestEnclosureLinker` exercise the post-Phase-3 emit shape and a non-synthetic-role rejection path. Companion forward-compatibility audit comments now annotate every `Symbol.kind` consumer surface listed in WI-jukav's description (ranking, slice, sketch, entrypoints, selection/filters, cli, linkers/{registry,containment,vue_template_method,js_module}, and `hypergumbo_lang_mainstream/js_ts.py`) with their per-value cluster classification and Phase 3 risk verdict; the at-risk surfaces (`containment.CONTAINABLE_KINDS`, `containment.CONTAINER_KINDS`, `selection.EXCLUDED_KINDS`) carry explicit follow-on pointers to subsequent WI-jukav slices.
- **`SCHEMA_VERSION` patch-bumps `0.4.1` → `0.4.2`**: additive new field (`Edge.is_resolved`) plus schema relaxation for two existing axes (`Symbol.kind` and new `Edge.evidence_type` both ship open enums until per-cluster Phase 4b producer migrations land). All changes are widening — no validation that previously passed will now fail.

### Added (continued)

- **Canonical `Symbol.kind` registry (ADR-0027 Phase 1)**: `packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py` enumerates every emitted `Symbol.kind` value (192 entries) classified across three axes — `language_construct` (Cluster A: pure syntactic constructs, ~50 values), `endpoint_shape` (Clusters D/E plus `component_ref`: deprecation candidates, ~40 values, fold to canonical + `Symbol.meta["framework_role"]` or producer-side drop), and `pending_classification` (Clusters B/C/G/H: file-shape, apex/peer overloads, build-config, long-tail domain vocabulary, ~100 values awaiting per-cluster audit-findings docs). Accessors mirror the `edge_types.py` shape: `all_symbol_kind_names()`, `symbol_kinds_on_axis(axis)`, `find_symbol_kind(name)`. ADR-0027 status flips from Draft to Accepted on Phase 1 land.
- **Pre-commit + CI drift linter for `Symbol.kind`** (`scripts/check-symbol-kind-drift`): AST-walks `packages/`, `scripts/`, and `.agent/` for module-level `*KIND*` set assignments and verifies every value is in the canonical registry. Companion property test at `packages/hypergumbo-core/tests/test_symbol_kinds.py::test_every_symbol_kind_named_set_is_a_subset_of_registry` provides CI-time gating; the pre-commit hook in `.githooks/pre-commit` provides local gating from the same merge moment.
- **`docs/concept-axes.md` extends to cover `Symbol.kind`**: `scripts/generate-concept-axes` now reads both `EDGE_TYPES` and `SYMBOL_KINDS`, emitting separate `Edge.type` and `Symbol.kind` axis sections with per-value descriptions. Re-runnable; CI freshness gate via `--check`.
- **`docs/schema.json` derives `Symbol.kind` enum from the registry**: replaces the prior hardcoded enum (which had silent drift — `contract`, `kernel`, `device_function`, `handler`, etc. were listed but no longer emitted by any analyzer) with `sorted(spec.name for spec in SYMBOL_KINDS)`. Per-value axis classification carried under the `x-axis-of-values` extension keyword, parallel to the existing `Edge.type` integration. `SCHEMA_VERSION` patch-bumps `0.4.0` → `0.4.1`.
- **`axis_drift.find_drift` accepts `excluded_target_names`**: lets callers skip target names that share the filter substring but live on a different axis. ADR-0027's `KIND` filter excludes `PROTOCOL_KINDS` and `BRIDGE_KINDS` in `edge_types.py` (vocabularies for `Edge.meta['protocol']` / `Edge.meta['bridge_kind']`, not Symbol.kind sets).

### Changed

- **Inheritance linker annotates simple-name fallback edges per INV-zuhub**: when `_resolve_target_symbol` falls back to deterministic-by-sorted-ID disambiguation (multiple cross-file candidates, no same-file precision match), the resulting `extends` / `implements` edge now carries `confidence=0.5` and `meta["disambiguation_fallback"]=True`. Single-candidate and same-file resolutions remain at `confidence=0.95` with no flag. Provides downstream consumers (slice ranking, dead-code analysis, supply-chain tier classification) a structured signal to filter the fallback population from the precision-resolved one. First worked example of INV-zuhub conformance — pattern is portable to the kafka_streams_dispatch and Go interface_dispatch sites listed under the invariant's "Concrete known violations" table.

### Fixed

- **Jackson dispatch linker recognizes JPA `@Entity` / `@MappedSuperclass` / `@Embeddable`** (WI-sokaz / UAT BUG-02): the Spring Data JPA + Spring MVC pattern routinely Jackson-serializes JPA-mapped types as REST response bodies, but `jackson_dispatch` was only triggering on Jackson, JAX-B, and Spring-binding annotations — not the JPA persistence annotations. On spring-petclinic the linker emitted exactly 1 edge (for a JAX-B `@XmlRootElement`-marked class) while the 6 `@Entity` JPA classes (Owner, Pet, Visit, Vet, Specialty, PetType) that dominate the serialization surface produced zero edges. `Entity`, `MappedSuperclass`, and `Embeddable` join the class-level annotation set; bean-convention accessors on JPA classes now receive `dispatches_to` edges as expected.
- **Inheritance linker enforces cross-language gating + Rust kind discipline** (WI-zozuz / UAT BUG-03): the centralized `inheritance.py` linker now (a) drops candidates whose `language` differs from the child symbol's before resolution — Python `class FooModule(nn.Module)` no longer emits a spurious `implements` edge to a Rust `Module` trait, eliminating the cross-language pollution that surfaced 31 bogus Python→Rust-trait edges in candle — and (b) refuses to fall back to `class`/`struct` candidates when the child is a Rust `struct`/`enum`, since Rust permits no struct→struct inheritance. Bridge linkers (PyO3, cffi, wasm_bindgen, jni) remain the sanctioned path for legitimate cross-language conformance edges. Property tests cover both layers plus a regression guard that legitimate same-language resolution still succeeds when a same-named symbol exists in another language.
- **Framework-dispatch linkers traverse transitive base-class chains** (WI-halat / UAT BUG-01): `airflow_framework_dispatch` and `django_orm_dispatch` now walk `extends`/`implements` edges to discover ancestors whose `meta.base_classes` names a framework base, fixing the dominant real-world pattern where projects extend an intermediate (`AlloyDBWriteBaseOperator(BaseOperator)`, `Order(LoggedModel)` / `LoggedModel(models.Model)`, `HierarkeyForm(forms.Form)` / custom subclass) rather than the framework class directly. UAT v4.0.0 round 04 (airflow) and round 05 (pretix) saw 0/9 and 0/6 transitive cases respectively under the prior direct-only matcher. New shared helper `linkers/_transitive_bases.py` (BFS with cycle guard) is the single source of truth for the walk; both linkers now consume it. Direct-base detection is unchanged.

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

- **Linker subcategory vocabulary restored** (ADR-0003-ext): Protocol / Bridge / Framework / Infrastructure subcategory taxonomy is now first-class. Every linker module docstring declares its subcategory; `docs/LINKERS.md` enumerates all 45 linkers with a Subcategory column.
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

- **Stop hook relaxed on CONVERGED bakeoffs** (WI-bibul): guidance now leads with `tracker ready` instead of requiring reflect/aggregate when bakeoff is converged.
- **Bakeoff-deep hub-collision warning** (WI-gapom): `pick_reverse_slice_seeds` warns on seeds with `prod_in_degree > 1000`.
- **`io-boundaries` defaults to production-only** (WI-sifif): test chains excluded by default (was 78% noise). `--include-tests` opts back in.
- **Adaptive hop limit removed from slice**: 3-10 hop limit replaced by `max_files` (100) and hub pruning (50). `--max-hops` still available for explicit control.

### Added

#### Developer experience

- **`auto-pr --tracker-id`** (WI-mokak): on merge, appends a discussion entry to the referenced tracker item citing the PR number and dev SHA.
- **`bakeoff-map` script**: walks bakeoff artifacts and emits a chronological map of sessions with convergence verdicts, pipeline-stage completion, and anomalies.
- **`tracker-path-linter` V1** (WI-sihih): verifies file-path tokens in tracker items resolve to real files. Stale references carry fuzzy-match suggestions.
- **`audit-stale-timestamps` V1** (WI-sofop): checks agent state files for embedded-timestamp drift (e.g. `last_completed_utc` vs file mtime).

#### Slice telemetry

- **Forward-dataflow admission-rule telemetry and option 2 evaluation** (WI-hukoh): `SliceResult.admission_stats` records per-rule counters for edges admitted/rejected during forward dataflow BFS. Telemetry across 4 repos (~188k edges) shows zero additional edges from option 2 — option 1 (writer-source admission) remains canonical. Re-evaluation trigger in ADR-0015 §6.1.

#### Linkers (Framework subcategory)

- **`go_memberlist` linker** (WI-lojuf): `dispatches_to` edges from `memberlist.Create` to the 12 canonical delegate methods (`NotifyMsg`, `GetBroadcasts`, `LocalState`, etc.). Used by alertmanager, consul, nomad, serf, vault.
- **`go_cobra` linker** (WI-gohad): `dispatches_to` edges from `cobra.Command{…}` struct literals to handler functions in `Run`/`RunE`/`PreRun`/`PostRun` and `Persistent*` variants. Used by kubectl, helm, hugo, prometheus, terraform, docker. Package-level `var cmd = &cobra.Command{…}` declarations (WI-lihih) now emit edges from the var symbol when no enclosing function exists.

#### Behavior map

- **`hypergumbo dead-code-maybe` subcommand** (WI-fisam): finds production callables unreachable from entrypoints via BFS over `calls`, `dispatches_to`, `routes_to`, and `wraps` edges. Configurable seed sets (`--seeds {entrypoints,tests,exports,all}`), text/JSON output, `--min-confidence` filtering, ranked by LOC. Cross-language string collision signal (WI-pimig) detects missing linker edges; FFI-signature auto-flag (WI-hadap) boosts FFI-marked candidates; `--exclude-exports` filter (WI-zafab) completes the three-filter set.
- **`Symbol.is_exported` across 5 languages** (WI-zimum, WI-gipag, WI-nimug, WI-fuhav, WI-rupum): new boolean marking public-API callables. Go capitalized identifiers, Rust `pub`/`pub(crate)`, `public` modifier (Phase 1); Python `__all__` / leading-underscore (Phase 2); TS/JS `export` statements; Kotlin extension functions; Scala secondary constructors. `--seeds exports` treats exports as reachability seeds. Drops dead-code false-positive rates 70-83% on Python framework libraries.
- **Generated-code detection and centrality demotion** (WI-tizij, WI-pofin, WI-vubad, WI-sozah): `is_generated` flag on files/symbols detects OpenAPI models, protobuf stubs, K8s code-gen, go-swagger output (`api/v2/restapi/`, `api/v2/models/`, fingerprint files), and `openapi-gen/` directories. Content-based header scanning (`// @generated`, `// Code generated … DO NOT EDIT.`) in the first 4 KiB of 36 text-like extensions. Generated code receives 95% centrality penalty, and `dead-code-maybe` unconditionally drops any candidate whose file is flagged generated (WI-jifup).
- **Test file classification** (WI-rigun-patuz, WI-gifuz): `is_test` decoupled from supply-chain tier as independent axis. Co-located test files (`_test.go`, `.test.js`, `.spec.ts`) classified as tier 1 instead of tier 2.
- **Return-type registry for chained receiver resolution** (WI-kuroj / INV-dihos Phase 1, WI-vadin): `method_return_types` populated during Pass 1 for Go and Java. Enables `x := e.Query(); x.Rows()` resolution via the registry. Inline chained calls like `e.NewQuery().Exec()` resolve at confidence 0.75.
- **Go build-tag-gated alternate definitions** (WI-potun): `//go:build` directives emit `build_tag_alternative_of` edges between same-named symbols in mutually exclusive files.
- **Event-sourcing linker expansion** (WI-zadat): extends event detection to Guava EventBus, generic Java event bus, Go channel-based events, and Go event bus method calls.
- **Go closure wrapper edges** (WI-nikul): route registrations through closure wrappers (e.g. `wrapAgent(api.query)`) emit `wraps` edges. Covers Gin/Echo/Fiber and Gorilla mux/stdlib.
- **Import-based framework validation**: manifest-detected frameworks cross-referenced against import edges. Test-only or unimported frameworks reclassified as `dev_frameworks`.
- **Go tier 2/3 classification via go.mod** (WI-vovuk): unresolved Go external references classified using `go.mod` — direct deps tier 2, indirect/stdlib tier 3. Language-agnostic `DependencyManifest` enables future extension.
- **Gradle multi-project workspace detection** (WI-zizuf): `detect_package_roots()` now parses `settings.gradle` / `settings.gradle.kts` `include` directives. Gradle subprojects are classified as workspace members, fixing degenerate tier distribution on Gradle monorepos like Kafka.
- **Orchestration hub floor for symbol ranking**: functions with out-degree ≥ 20 get a minimum effective in-degree of `sqrt(out_degree) * 0.8`, preventing orchestration hubs (main, run, app) from being buried by within-file dampening.
- **Event edge type weights**: `event_subscribes`/`event_publishes` raised to 0.8 (was 0.5). `dispatches_to` added at 0.6.

#### Language analyzers

- **TLA+** (WI-jurin, WI-ludil): tree-sitter analyzer for `.tla` formal specification files. Extracts module, operator, constant, variable, theorem, and assumption symbols. EXTENDS/INSTANCE as `imports`, cross-references as `references`.

#### Dataflow library_patterns expansions

- **Python AST wiring** (WI-hivud): `python.yaml` ships `library_patterns` for common mutating/reading methods. `annotate_dataflow_ast` now consumes these as a per-language fallback for Python's AST analyzer.
- **Python serialization + file-position primitives** (WI-fogis): 14 patterns — `json.dump`/`pickle.dump`/`yaml.dump` as write, `json.load`/`pickle.load`/`yaml.load` as read, `.seek` as mutate, `.truncate` as write.
- **Cross-language library_patterns** (WI-vinub): name-based access_mode heuristics for Java (25 patterns), JS/TS (23 each), C# (24), and Kotlin (17). Enables `access_mode` annotation for dataflow slicing in these languages.
- **Go state-mutating verbs** (WI-supih): `.Expire`, `.GC`, `.Truncate`, `.Drop`, `.Init`, `.Reload` tagged `access_mode=write`.

#### Training data pipeline

- **Per-session transcript sync** (ADR-0018 amendment, INV-filaj): concurrent sessions now write to isolated files keyed by `session_id` instead of racing on shared state. Session-end rotation atomically promotes files into `.last_*`/`.second_to_last_*` slots. Cursor exempted via sibling check; injection-history sidecar tracks playbook events.
- **v0 corpus cohort backfill** (WI-gigil): `backfill-training-data-cohort-tags.py` writes a sidecar with per-entry `infra_sha`, `playbook_registry_sha`, `main_llm_presumed`, and playbook counts. Re-runnable, non-destructive.
- **Per-entry cohort metadata** (WI-tatuh / INV-gajap): `log_training_example` now writes `pipeline_version`, `infra_sha`, `playbook_registry_sha`, `main_llm`, `vendor`, `vendor_version`, and `scoring_model` on every entry. Distribution shifts discoverable from the corpus alone.
- **Multi-vendor interjection normalization** (WI-nadud): `filter-transcript.py` emits `normalized_user_interjection` rows for user interjections across Claude Code, Codex CLI, and OpenHands. `pipeline_version` bumped to v2.

#### CLI & infrastructure

- **`hypergumbo config <lang>`** (WI-siran): shows all per-language configuration (dataflow patterns, IO primitives, function summaries) in one view. Supports `--format json|yaml|text`.
- **smart-test flock guard** (WI-sinap): concurrent invocations prevented via `flock`. Second invocation exits immediately naming the holding PID.
- **`auto-pr` resilience** (WI-kugob, WI-bahuf, WI-miriz): `list`/`status` detect and `prune` removes stale vPR entries. Already-merged push rejections handled gracefully. New `.git/AUTOPR_LAST_RESULT.json` sentinel records outcome on every exit.
- **`merge-pr close <PR>`** (WI-vonis): close a PR without merging, with optional `--reason` audit-trail comment.
- **Bakeoff-deep integration tests** (WI-gutan): 13 tests covering `init → cohort → cycle → iter-NNN/` end-to-end.

#### Dead-code prospector: polyglot-only filter (WI-zafab)

- `dead-code-prospector-run.py` skips monoglot repos (fewer than 2 languages with ≥10 files each). `--include-monoglot` bypasses.

#### Go encoding/serialization callback entrypoints (WI-pimig Phase 1)

- Go marshal/unmarshal methods (`MarshalJSON`, `UnmarshalYAML`, etc.) detected as `serialization_callback` entrypoints via `go-encoding-callbacks.yaml`. Previously invisible to the call graph.

#### Broker / server lifecycle entrypoint heuristics (WI-nazir)

- Three new naming-tier patterns detect JVM broker lifecycle methods (`*Server.startup/start/run/shutdown`, `*Apis.handle*/process*/dispatch*`, `*Acceptor.run`) as `CONTROLLER` entrypoints. Surfaces the broker request-dispatch surface on Kafka and similar services.

### Fixed

#### Java analyzer

- **Short-name collision** (WI-fuhaj): local classes with names colliding with library classes (e.g. `Logger` POJO vs slf4j `Logger`) no longer absorb cross-file calls. Eliminated 2057+ bogus edges on Kafka.

#### Hook test infrastructure (INV-pofam)

- Fixed silent failures in `.githooks/test_hooks.sh` (stale PID from command-substitution subshell). Wired into CI as a `hook-tests` job.

#### Dataflow annotation preservation

- **`access_mode`/`dest_access_mode` preserved through 4 linkers** (INV-forim): `event_sourcing`, `ipc`, `websocket`, and `message_queue` linkers were overwriting the meta dict, stripping dataflow fields. Fix: pass metadata via `Edge.create` kwarg.

#### Agent state recovery

- **Delete vestigial `.agent/last_stop_check.json`** (INV-jofaf facet 1): removed stale file left after migration to guidance_log.
- **Split stop-hook state file** (INV-jofaf facet 2): split into `stop_hook_state.json` (hook-written) and `agent_notes.json` (agent-written via `scripts/agent-notes`).

#### IO boundaries

- **Go logging reclassified**: `fmt.Print*`, `log.*`, `log/slog.*` moved from `ipc_send` to `logging`. Eliminates 134 false-positive IPC chains on alertmanager. `os.Stdout`/`os.Stderr` remain `ipc_send`.

#### Go analyzer

- **Receiver-type guard for interface_dispatch** (WI-jopar): calls on external/stdlib receivers no longer dispatch to local interface methods of the same name. Eliminated 13 spurious edges on alertmanager.
- **Cross-file struct method aggregation** (WI-hobuk): structural interface matcher now aggregates `struct_method_sets` per package directory. Methods in sibling files within the same package are no longer dropped.
- **Cross-package struct collision** (INV-zomuk): struct method sets keyed by short name caused merging across packages. Fix: iterate per-file. `dispatches_to` edges 3 → 19 on alertmanager.
- **Structural interface arity matching**: satisfaction check now verifies parameter and return counts, not just method names. Removes 463 false `dispatches_to` edges on alertmanager.
- **Cross-package interface dispatch resolution**: cross-package interface fields (e.g. `stage notify.Stage`) now strip package prefix before method lookup.
- **Route resolver receiver-method shadow** (INV-kunam): handler `api.query` (lowercase receiver) couldn't match symbol `API.query` (uppercase type). Fix: prefer same-file candidates via `symbols_by_short_name` index.

#### Symbol resolution

- **Go promoted-method interface satisfaction** (WI-tudib): structural interface matcher traverses embedding chains. Promoted methods included in satisfaction check.
- **Type hierarchy per-language gate** (WI-sukav): `extends` edges in Go, C++, Rust, C# no longer emit `dispatches_to` (composition, not inheritance). Eliminated false edges in reverse slices.
- **Type hierarchy concrete→concrete fan-out**: same-named concrete types across packages no longer produce false `dispatches_to` edges. 70% of alertmanager's 459 edges were false positives.
- **`ListNameResolver` path-hint false positives** (INV-popup): path hints require segment-level suffix matching instead of substring.
- **`library_patterns` YAML never applied** (INV-halar): `scan_library_patterns` had no callers — wired into `annotate_dataflow`. Alertmanager `access_mode='write'` edges: 0 → 274.

#### Slice

- **Forward dataflow admits downstream reads** (WI-saful): read edges downstream of writers now admitted as one-hop terminals in forward slices, per ADR-0015 §6.
- **Reverse-slice filename collision**: reverse slices now write to `slice.<name>.reverse.json` to avoid overwriting forward slices.

#### Profile & sketch

- **Profile LOC always zero in behavior map**: `hypergumbo run` now populates per-language LOC in the profile. Previously LOC was only backfilled in the sketch path.
- **False positive `cargo test` in sketch**: ambiguous test framework patterns (e.g. `#[test]`) now scoped to their language's file extensions.

#### Bakeoff signals

- **`bakeoff-deep init` recency check** (WI-kumub): warns before creating a new session when a recent one (< 7 days) matches the same pool and code hash.
- **`bakeoff-deep compare` metric ranking** (WI-lazat): dynamically ranks metrics by mean absolute delta instead of using a hardcoded set.
- **`LOW_DATAFLOW_SLICE_RATIO` false alarm** (WI-tutob): suppressed when `slice_access_mode_coverage ≥ 50%` (denominator growth was inflating the metric).
- **Tier slice byte-identical artifacts** (WI-puvab): tier slices use explicit non-test entry instead of `--entry auto --exclude-tests`, which eliminated all entries in test-dominated repos.
- **`cross_language_io_pct` false WARN**: gated on FFI bridge edges; no longer fires on HTTP-connected polyglot repos.

#### CI debug

- **Null statuses on freshly-pushed PR head**: `ci-debug` crashed when `commits/{sha}/status` returned `"statuses": null`. Fix: treat null and missing the same way.
- **Job log fetch 404s on Codeberg**: `fetch_job_log()` now selects log path by Forgejo version (`/logs` vs `/attempt/1/logs`).

#### Hooks

- **Stop hook hash recording throttle**: 150-second pause between hash recordings prevents the circuit breaker from tripping during background sub-agent waits.

#### Other

- **`loop-toggle` accepts uppercase mode arguments**: case-insensitive dispatch via `${var,,}`.
- **Flaky auto-run tests** (WI-miguf): stale cache state from prior sessions could short-circuit the auto-run check. Fix: autouse `isolate_hypergumbo_cache` fixture redirects `XDG_CACHE_HOME` per test.

### Documentation

- **ADR-0006 augmented with Return-Type Registry Pre-Pass** (INV-dihos): adds source 5 ("return-type chaining via global registry") to §"Type Inference Sources" with rollout plan.
- **Stash safety rule for `.ci/affected-tests.txt`**: added to AGENTS.md and smart-test playbook. Reset the file before `git stash pop` to avoid merge conflicts.
- **Bakeoff iteration vs. new session clarification**: artifacts guide explains session/cohort/iteration nesting and the `cycle` vs `init` rule.
- **Dogfooding playbook IR class names corrected**: `IRNode`/`IREdge` → `Symbol`, `Edge`, `Span`, `AnalysisRun`.

## [2.5.1] - 2026-04-05

## [2.5.0] - 2026-04-04

### Added

#### Go qualified-type parameter tracking

- **Qualified type propagation**: Function parameters and struct fields with package-qualified types (e.g. `client *http.Client`) now carry full module hints through to unresolved edges and field chain access. IO boundary detection can now classify `http.Client.Do()` as `net_send` and chained patterns like `n.client.Do(req)` — previously blocked by `ambiguous_names` guard due to missing module context.
- **Interface dispatch narrowing** (WI-doval): `var n Notifier = &DiscordNotifier{}` now tracks the concrete type, eliminating spurious `dispatches_to` edges.

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

- **Ruby FFI** (WI-valiv): `attach_function` to external libraries emits `ruby:C_ffi:0-0:<name>:unresolved` edges, redirected to C catalog for IO tagging.
- **Python FFI** (WI-dokum): `ctypes.CDLL(None)` and `ffi.dlopen(None)` emit `python:C_stdlib:0-0:<name>:unresolved` edges. Repo-local C symbols still produce resolved edges when available.

#### Dataflow access mode patterns

- **Rust** (`rust.yaml`): 44 method-name heuristics (write/read/delete). Previously all Rust call edges had no access_mode.
- **Go** (`go.yaml`): 30 regex patterns (15 write, 15 read) for mutating method calls (WI-satuv).
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

- **Library export boost** (1.4×): `library_export`-tagged symbols in the entrypoints section are boosted in rslice seed scoring. Ensures reverse slices answer "who calls this library's public API?" (WI-bosik).
- **Architectural concept boost** (1.3×): Middleware, controller, application, and model symbols boosted over pure hub nodes (OutputBuffer.append, Iterator.next).

#### I/O boundary catalog additions

- **C**: `fclose`, `fflush`, `fseek`, `rewind`, `ungetc`, `ftell` (stdio lifecycle). `tmpfile`, `tmpnam`, `mkstemp`, `mkdtemp`, `mkostemp`, `mkstemps` (temp files).
- **Go**: `http.Transport.RoundTrip` (net_send). `golang.org/x/sys/execabs.Command` (subprocess). `testing.T.TempDir`/`testing.B.TempDir` (fs_write). `log`/`log/slog` families (ipc_send). `crypto/tls` Dial/Client and `net/smtp` NewClient/Dial/SendMail (net_send). Removes 6 false positives (`bytes.Buffer.WriteString`, `strings.Builder.WriteString`, `kingpin.Command()`).

#### Other

- `sketch --require-section`: force specific sections into output regardless of token budget.

### Fixed

#### FFI IO boundary tracing (INV-kagob, INV-zogun)

- All 6 FFI linkers (cgo, JNI, PyFFI, N-API, Lua FFI, Ruby FFI) now annotate bridge edges with `access_mode=write, dest_access_mode=read`. Validated: chai2010/cgo 0→38 annotated edges.
- `cgo_bridge` and `ffi_bridge` added to IO boundary tag and trace sets. IO chains now cross Go→C and Python→Rust boundaries (go-sqlite3: 116 edges, polars: 5,617 edges previously had zero IO metadata).
- FFI catalog redirect: `go:C:` pseudo-namespace from cgo redirected to C catalog. Validated: chai2010/cgo 0→7 IO edges.

#### Dataflow slice quality (INV-jahov)

- **Position-aware access_mode**: Tree-sitter child field names distinguish LHS (write) from RHS (read) in assignments. Python AST reclassifies call edges on assignment lines as "read". `returns` YAML section now loaded (was silently dropped). Net effect: dataflow slices are tighter than structural slices — forward follows write/mutate, reverse follows read.

#### Java annotation and route fixes (INV-nimik, WI-tojuz)

- JAX-RS `@Path(value="/foo")` kwargs extraction (was only checking positional args). Same fix for Micronaut. Generic return type extraction (`Response<User>` → `Response`) for subresource locator detection.
- Empty route paths normalized to `"/"` in stable IDs and materialized symbols.
- `in`, `out`, `err` added to Java `ambiguous_names` (20 false positives in keycloak from JPA `CriteriaBuilder.in()`).

#### I/O boundary detection

- **Ambiguous name filtering for 10 catalogs** (INV-tapat): Go, Rust, Python, Java, C, JavaScript, Erlang, Haskell, Objective-C. Measured: polars net_send 285→89 (69% reduction). JavaScript `remove`/`rename` added (8 false `fs_write` chains eliminated in keycloak).
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

- **Gate timing race** (INV-rahib): `PR_PENDING` gate now created before push (was after), closing a window where tracker sync could advance dev mid-flight. Added re-check before push and proactive fetch+rebase after CI poll.
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

- **Stale-PR audit failover** (WI-soboh): Now respects `CI_FAILOVER_ACTIVE`, querying selfh instead of origin.
- **Circuit breaker**: Fixed TOCTOU race (two `tail` reads) and off-by-one (current stop counted toward threshold). Mechanically runs `loop-toggle off` on trip.
- **Pre-push failover verification** (INV-bifud): Blocks pushes to origin during failover.

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

**YAML-Driven Analysis (ADR-0003)**
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
  release includes all ADR-0003 features. Users should upgrade from v0.9.0 to v0.9.1.

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
| 0.9.1   | 2026-01-09 | ADR-0003 implementation (was missing in 0.9.0)               |
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
