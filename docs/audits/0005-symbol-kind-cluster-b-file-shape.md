<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0005: Symbol.kind Cluster B — File-Shape and Package-Shape Entities

- Date: 2026-05-05
- Status: All rows UNRESOLVED at filing (Phase 3 producer migrations + registry updates pending)
- Closes: WI-gajob-hibod-talop-lofik-valuv-tumak-bifad-kopod (Cluster B file-shape entities: separate-axis vs canonical, ADR-0027 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Second audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster A canonical).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Phase 3" Cluster B identifies ~17 `Symbol.kind` values that represent file-shape, package-shape, and project-shape entities — `file`, `library`, `package`, `executable`, `program`, `project`, `module_file`, `component_file`, `npm_package`, `composer_package`, `main_entry`, `library_export`, `export_entry`, `wasm_module`, `wasm_import`, `tsconfig`, `script`. All are currently parked on `pending_classification` per the ADR-0027 Phase 1 registry seeding.

The cluster-B framing question (per WI-gajob): are these *language constructs* in the same sense as Cluster A (function, class, method, struct, …), or are they a different conceptual axis (e.g., a Symbol.shape or Symbol.role axis recording the file/build-artifact role of a non-syntactic entity)?

This audit answers: **mostly yes for build-DSL top-level constructs, mostly fold-to-canonical for framework-qualified file representations**. No new axis is required. The verdicts below partition the 17 values into:

- **CANONICAL promotions** (6 values): values that are genuinely language-level constructs in their respective DSLs (CMake / Meson / build-system tongues; COBOL / Pascal / Fortran source); they belong on `language_construct` after registry update.
- **FOLD to existing Cluster A canonical + meta key** (10 values): values that are framework / ecosystem qualifiers on an underlying file / package / export / import / module; the qualifier moves to `Symbol.meta["framework_role"]` or a parallel meta key, and the kind drops to the canonical Cluster A construct.
- **DEPRECATE-NO-FOLD candidates** (1 value, advisory): `tsconfig` is a single-purpose marker for a single file path; the producer should drop the kind specialisation and synthesise the same data via `is_config_file=True` already shipped at v4.0.0 plus `meta["config_format"]="tsconfig"`.

All rows ship at `UNRESOLVED` status because Phase 3 producer migrations + registry updates have not landed. Wave 6 of the [WI-runod](../../.agent/tracker/) cross-axis schedule covers the migration; this document is the precondition for that wave.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test diagnostic procedure are defined in [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). This document applies that methodology to the Cluster B subset of `Symbol.kind` values.

The `diagnostic_test` field for each row is a one-line Python invocation that asserts the value is currently present on the `pending_classification` axis (the Phase 1 seed home). The `expect: exit_code:0` shape lets a future audit runner verify the row's claim that the value is still pending migration.

## Diagnostic findings

Three distinct shapes surfaced during the four-leakage-test pass:

### 1. Build-DSL top-level constructs are language constructs in their own tongue

Six values (`file`, `library`, `package`, `executable`, `program`, `project`) are emitted from build-system or DSL analyzers — CMake, Meson, COBOL, Pascal, Fortran, VHDL — for declarations that are genuinely top-level constructs in the source language at hand. CMake's `add_library(...)`, COBOL's `PROGRAM-ID`, Fortran's `PROGRAM`, Meson's `project(...)` are language-level keywords / declarations the analyzer parses out of source AST.

Test 4 (mechanism vs. category) confirms each: the value names a *category* of source-language construct, not a *mechanism* qualifier. Test 1 (property derivability) does *not* fire — none of these is derivable from another field. Test 2 (apex/peer overloading) does fire mildly for `package` (CMake's `find_package` vs VHDL's `package` keyword vs JS analyzer's package-declaration synthesis), but the same name across three constructs reads as "the same general concept across three languages," not as a leak — the cross-language overload is the same shape as `function` appearing in every language.

These six values get verdict **CANONICAL** with the migration action being: lift them from `pending_classification` to `language_construct` in the registry. The CMake / Meson / Pascal / COBOL / Fortran analyzers continue emitting unchanged.

### 2. Framework-qualified file representations are file + meta-key

Ten values (`module_file`, `component_file`, `npm_package`, `composer_package`, `main_entry`, `library_export`, `export_entry`, `wasm_module`, `wasm_import`, `script`) emit at file-shape positions but each carries a *framework / ecosystem* qualifier baked into the kind name. The JS analyzer emits `kind="module_file"` for what is structurally a `kind="file"` with `meta["module_system"]="esm"`. The JSON config analyzer emits `kind="npm_package"` for what is structurally `kind="package"` (or `kind="library"`) with `meta["package_ecosystem"]="npm"`. The wasm-bindgen linker emits `kind="wasm_module"` for what is structurally `kind="module"` with `meta["compilation_target"]="wasm"`.

Test 1 (property derivability) fires on every one: each value's framework qualifier is derivable as the difference between the value's name and its underlying canonical. Test 4 (mechanism vs. category) reinforces — the framework / ecosystem is a *mechanism* by which the underlying construct is realised, not a different category of construct.

These ten values get verdict **FOLD** with the underlying Cluster A canonical as the fold target and the framework qualifier moved to `meta`. This mirrors ADR-0023's fold pattern for `Edge.edge_type` ecosystem qualifiers (e.g., `tauri_invoke` → `dispatches_to` + `meta["bridge_kind"]="tauri"`).

### 3. Single-purpose file markers are role flags, not kinds

One value (`tsconfig`) is a single-purpose marker for a single file path (`tsconfig.json`). It exists because the analyzer wanted to surface tsconfig.json as a structurally-distinct symbol. The `is_config_file=True` boolean shipped at v4.0.0 already records the same fact at a different point in the schema — `tsconfig` as a kind label is redundant once the boolean shipped.

Verdict: **DEPRECATE-NO-FOLD** (advisory). The producer rewrites to drop the kind specialisation; the consumer queries `is_config_file=True AND path.endswith("tsconfig.json")` (or `meta["config_format"]="tsconfig"` if a per-format selector is needed downstream).

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: file
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"file\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "File is the top-level container construct. Boundary-node and make_file_id machinery treat it as load-bearing; ranking.py / sketch.py reference it. Promote to language_construct in registry."
  - value: library
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"library\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CMake add_library / Robot Framework Library imports declare a top-level library construct in the build-DSL. Promote to language_construct."
  - value: package
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"package\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "VHDL package keyword is a language construct; CMake find_package and json_config package-declaration synthesis name the same concept across DSLs. Cross-language overload reads like function/class. Promote to language_construct."
  - value: executable
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"executable\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CMake add_executable declares an executable target — genuine build-DSL top-level construct. Promote to language_construct."
  - value: program
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"program\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "COBOL PROGRAM-ID, Pascal program keyword, Fortran PROGRAM are language-level top-level constructs. Promote to language_construct."
  - value: project
    verdict: CANONICAL
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"project\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Meson project() and CMake project() are top-level build-DSL constructs. Promote to language_construct."
  - value: module_file
    verdict: FOLD
    fold_target: file
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"module_file\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "JS module-file marker. Structurally a kind=file with module-resolution metadata. Fold to file + meta['module_system']='esm' (or 'commonjs')."
  - value: component_file
    verdict: FOLD
    fold_target: file
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"component_file\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Vue / Svelte / Astro single-file component. Structurally a kind=file with framework metadata. Fold to file + meta['component_framework']='vue' (or 'svelte', etc.)."
  - value: npm_package
    verdict: FOLD
    fold_target: package
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"npm_package\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "npm-ecosystem qualifier on package. Fold to package + meta['package_ecosystem']='npm'."
  - value: composer_package
    verdict: FOLD
    fold_target: package
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"composer_package\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "PHP Composer-ecosystem qualifier on package. Fold to package + meta['package_ecosystem']='composer'."
  - value: main_entry
    verdict: FOLD
    fold_target: file
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"main_entry\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "package.json 'main' field — declares the role of a file (entry point), not a category. Fold to file + meta['entry_role']='main'."
  - value: library_export
    verdict: FOLD
    fold_target: export
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"library_export\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "JS/TS library-level export declaration. The 'library' qualifier is a scope marker; the construct is export. Fold to export + meta['export_scope']='library'."
  - value: export_entry
    verdict: FOLD
    fold_target: export
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"export_entry\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "package.json exports-map entry. Structurally an export with package.json metadata. Fold to export + meta['export_source']='package_exports_map'."
  - value: wasm_module
    verdict: FOLD
    fold_target: module
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"wasm_module\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "wasm-bindgen-emitted wasm module. Structurally a module with wasm-compilation metadata. Fold to module + meta['compilation_target']='wasm'."
  - value: wasm_import
    verdict: FOLD
    fold_target: import
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"wasm_import\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "wasm-bindgen import declaration. Structurally an import with wasm-target metadata. Fold to import + meta['compilation_target']='wasm'."
  - value: script
    verdict: FOLD
    fold_target: file
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"script\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "pyproject.toml [project.scripts] / package.json scripts entries — file with executable-script role. Fold to file + meta['entry_role']='script'."
  - value: tsconfig
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"tsconfig\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Single-purpose marker for tsconfig.json — redundant with the v4.0.0 is_config_file boolean. Producer drops the kind specialisation; consumer queries is_config_file + path.endswith('tsconfig.json') or meta['config_format']='tsconfig'."
```

## Migration impact

Wave 6 of the [WI-runod](../../.agent/tracker/) cross-axis Phase 3 sequencing schedule covers the actual producer migration. Sketched here for context:

1. **Registry update (mechanical, doc-only PR):** lift the six CANONICAL values (`file`, `library`, `package`, `executable`, `program`, `project`) from `AXIS_PENDING` to `AXIS_LANGUAGE_CONSTRUCT` in `symbol_kinds.py`. Update the row statuses in this audit-findings doc from `UNRESOLVED` → `RESOLVED` in the same PR.

2. **Per-producer FOLD migration (one PR per producer surface):** the ten FOLD values shipped from the producer-side, mirroring ADR-0023's per-family migration shape. Each producer (js_module.py, vue_component.py, json_config.py, wasm_bindgen.py, etc.) folds the specialised kind to its Cluster A canonical and routes the qualifier to `Symbol.meta`. Per-PR `awaits_bakeoff_validation` tag.

3. **DEPRECATE-NO-FOLD migration (single small PR):** `json_config.py:735` rewrites the tsconfig.json producer to drop `kind="tsconfig"` and rely on the existing `is_config_file=True` boolean. The audit-findings row moves to RESOLVED once the registry pruning lands.

The `meta` keys introduced by Wave 6 (`module_system`, `component_framework`, `package_ecosystem`, `entry_role`, `export_scope`, `export_source`, `compilation_target`, `config_format`) are candidates for ADR-0029's `axis_meta_keys.py` registry (Wave 9, WI-vusot).

## Related

- [ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct](../adr/0027-symbol-kind-language-construct-only.md) — the originating axis declaration. §"Phase 3" Cluster B is the scope this audit covers.
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — §"Family-audit verdict methodology" defines the verdict trichotomy applied here; §"Fold-residue discipline" rule 3 is the recurrence-promotion threshold the meta keys named here will trigger.
- [Audit-findings 0003](0003-symbol-kind-cluster-a-language-constructs.md) — sibling Cluster A audit on the same axis. The CANONICAL promotions named here will join 0003's seed once Wave 6 ships.
- [`docs/audits/README.md`](README.md) — format spec.
- WI-runod (cross-axis Phase 3 sequencing schedule) — this document is Wave 1 in the schedule; Wave 6 acts on its verdicts.
- WI-vusot (axis_meta_keys.py parallel registry, Wave 9) — consumes the meta keys listed in §"Migration impact" once Wave 6 ships.
