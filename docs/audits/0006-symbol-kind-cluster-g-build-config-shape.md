<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0006: Symbol.kind Cluster G — Build / Config-Shape Entities

- Date: 2026-05-05
- Status: Mixed — 15 CANONICAL rows RESOLVED via WI-runod Wave 6 PR 2 registry promotion; 9 FOLD/DEPRECATE-NO-FOLD rows remain UNRESOLVED pending subsequent Wave 6 PRs (5 FOLD producer migrations: `test_case`/`editable`/`url_requirement`/`devDependency`/`python_task`; 4 DEPRECATE-NO-FOLD: `config`/`dev-dependency`/`build-dependency`/`work_item`).
- Closes: WI-dubab-karur-vihak-majiv-dijug-pafot-vipuk-holod (Cluster G build/config-shape entities: separate-axis or demote, ADR-0027 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Third audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster A canonical) and 0005 (Cluster B file-shape).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Phase 3" Cluster G identifies build / config-shape `Symbol.kind` values currently parked on `pending_classification`. The tracker scope expanded the original ADR list to **24 values**: `test`, `test_case`, `work_item`, `target`, `special_target`, `recipe`, `env_var`, `build_arg`, `exposed_port`, `stage`, `requirement`, `editable`, `url_requirement`, `setting`, `config`, `derivation`, `dependency`, `devDependency`, `dev-dependency`, `build-dependency`, `addtask`, `python_task`, `task`, `trigger`.

The cluster-G framing question (per WI-dubab and ADR-0027 §"Phase 3" Cluster G): are these *language constructs* in their respective build / config DSLs (CMake, Make, Just, Dockerfile, Nix, BitBake, requirements.txt, pyproject.toml, package.json, INI, Prisma, SQL, …), or are they better represented on a *separate axis* (a `Symbol.role` / `Symbol.config_shape` sibling), or *demoted entirely* (a Makefile target as a SymbolID with no language-level kind)?

This audit answers: **mostly CANONICAL — these are genuine constructs in their respective build / config / data DSLs, no new axis required.** A minority FOLD to a sibling apex with the qualifier moved to `meta`; three rows are DEPRECATE-NO-FOLD. The verdicts below partition the 24 values into:

- **CANONICAL promotions** (15 values): values that are top-level constructs in their respective build / config / data DSLs (Dockerfile ENV/ARG/EXPOSE/FROM, Make `.PHONY`, Just recipe, Nix derivation, BitBake addtask/task, SQL/Apex trigger, Meson `target`, …); they belong on `language_construct` after registry update.
- **FOLD to existing Cluster G canonical + meta key** (5 values): values that are sub-mode / scope / implementation qualifiers on an underlying canonical construct; the qualifier moves to `Symbol.meta` and the kind drops to the sibling apex. (`test_case` → `test`; `editable`, `url_requirement` → `requirement`; `devDependency` → `dependency`; `python_task` → `task`.)
- **DEPRECATE-NO-FOLD** (4 values): three are dead vocabulary in the registry with no producer (`dev-dependency`, `build-dependency`); one (`config`) is a generic Prisma DSL-block placeholder where the real construct already lives in `meta["block_type"]`; one (`work_item`) is the tracker's own `Item.kind` value that should not appear in the `Symbol.kind` registry at all.

All rows ship at `UNRESOLVED` status because Phase 3 producer migrations + registry updates have not landed. Wave 6 of the [WI-runod](../../.agent/tracker/) cross-axis schedule covers the migration; this document is the precondition for that wave.

**Scope decision (no new ADR).** Per ADR-0027 §"Phase 3" Cluster G, an alternate path was to declare a separate `Symbol.role` / `Symbol.config_shape` axis for these values. After running the four leakage tests on each, that path is rejected: the build / config DSLs are *programming languages in their own right*, with their own top-level constructs that the analyzer parses out of source AST. The cross-DSL overload pattern (Meson `target`, RST `target`; SQL `trigger`, Apex `trigger`; Nix `derivation`, BitBake `addtask`) reads as the same shape as `function` or `package` appearing in every general-purpose language — not as a leak. No new axis ADR is filed; the canonical promotions land via registry update in the Wave 6 producer-migration PR series.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test diagnostic procedure are defined in [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). This document applies that methodology to the Cluster G subset of `Symbol.kind` values.

The `diagnostic_test` field for each row is a one-line Python invocation that asserts the value is currently present on the `pending_classification` axis (the Phase 1 seed home). The `expect: exit_code:0` shape lets a future audit runner verify the row's claim that the value is still pending migration.

## Diagnostic findings

Five distinct shapes surfaced during the four-leakage-test pass:

### 1. Build-DSL top-level constructs are language constructs in their own tongue

Most Cluster G values (15 of 24) are emitted from build / config / data DSL analyzers — Dockerfile, Make, Just, Meson, Nix, BitBake, SQL, Apex, requirements.txt, pyproject.toml, package.json, Maven `pom.xml`, Robot Framework, INI — for declarations that are genuinely top-level constructs in the source language at hand. Dockerfile's `ENV FOO=bar`, Make's `.PHONY:`, Just's `recipe:`, Nix's `mkDerivation { … }`, BitBake's `addtask` directive, SQL's `CREATE TRIGGER`, Apex's `trigger Foo on Bar`, Meson's `executable()` / `library()` / `custom_target()` are language-level keywords / declarations the analyzer parses out of source AST.

Test 4 (mechanism vs. category) confirms each: the value names a *category* of source-language construct, not a *mechanism* qualifier. Test 1 (property derivability) does not fire — none is derivable from another field. Test 2 (apex/peer overloading) fires mildly for `target` (Meson build target vs. RST hyperlink target) and `trigger` (SQL trigger vs. Apex trigger), but the cross-DSL overload reads as "the same general concept across DSLs" — the same shape as `function` appearing in every language. Test 3 (construct vs. relationship) does not fire.

These 15 values get verdict **CANONICAL** with the migration action being: lift them from `pending_classification` to `language_construct` in the registry. The producer analyzers continue emitting unchanged.

### 2. Sub-mode / scope qualifiers are apex + meta

Five values (`test_case`, `editable`, `url_requirement`, `devDependency`, `python_task`) emit at the same construct position as a sibling Cluster G canonical, with a *sub-mode*, *scope*, or *implementation* qualifier baked into the kind name:

- `test_case` (Robot Framework `*** Test Cases ***` entries) is the same concept as `test` (Cargo `[[test]]` table, Zig `test "…" { … }`); Robot's vocabulary happens to read "test case." Apex selection: `test` is the canonical noun; `test_case` folds to `test`.
- `editable` (`pip install -e .`) and `url_requirement` (`git+https://…` form in `requirements.txt`) are the same construct as `requirement` (the plain `pkg==1.0` line) under different *install modes* / *install sources*. Apex: `requirement`; the qualifier moves to `meta["install_mode"]="editable"` or `meta["install_source"]="url"`.
- `devDependency` (`package.json` `devDependencies` map + Composer `require-dev`) is the same construct as `dependency` (the `dependencies` map + Composer `require`) under a different *dependency scope*. Apex: `dependency`; the qualifier moves to `meta["dependency_scope"]="dev"`. (Production scope is the implicit default.)
- `python_task` (BitBake `python foo() { … }` task definition) is the same construct as `task` (BitBake shell-style `foo() { … }` task) under a different *task implementation*. Apex: `task`; the qualifier moves to `meta["task_implementation"]="python"`.

Test 1 (property derivability) fires on every one: each value's qualifier is derivable as the difference between the value's name and its underlying canonical. Test 4 (mechanism vs. category) reinforces — the install-mode / dependency-scope / task-implementation is a *mechanism* by which the underlying construct is realised, not a different category of construct.

These five values get verdict **FOLD** with the underlying Cluster G apex as the fold target and the qualifier moved to `meta`. This mirrors the Cluster B fold pattern for ecosystem qualifiers (`npm_package` → `package` + `meta["package_ecosystem"]="npm"`).

Note on `addtask`: BitBake's `addtask foo before bar after baz` is a *directive that registers a task* — it is structurally distinct from `task` (which *defines* a task body). It's a separate construct in BitBake syntax, not a sub-mode of `task`. **CANONICAL.**

### 3. Generic DSL-block placeholders shadow the real construct in meta

One value (`config`) is emitted by the Prisma analyzer for ALL Prisma schema blocks — `generator`, `datasource`, `model`, `enum`, `view`, `type` — with the actual block type carried in `meta["block_type"]`. This is the *opposite* of the apex/peer pattern: instead of multiple kind labels for one concept, one kind label flattens multiple distinct concepts.

The real construct is already named in `meta["block_type"]`. The `config` value adds no information; it actively obscures the construct. Test 1 (property derivability) fires hard: `config` is fully derivable from "this came from a Prisma analyzer" — and the meaningful classification is in meta.

Verdict: **DEPRECATE-NO-FOLD**. The `prisma.py` producer rewrites to emit `kind=block_type` directly (matching the existing meta key value). Consumers that today filter on `kind="config"` instead query `language="prisma"` (or the per-block-type kind, after the rewrite). Some block_type values (`model`, `enum`, `type`, `view`) are already canonical Cluster A constructs; others (`generator`, `datasource`) are Prisma-specific top-level constructs that may need their own registry entries.

### 4. Dead vocabulary

Two values (`dev-dependency`, `build-dependency`) are present in the `SymbolKindSpec` registry but **no analyzer or linker emits them**. They are a vocabulary placeholder — possibly added in anticipation of a JSON-config or PEP-621 dependency-scope expansion that never landed, or for symmetry with `devDependency`'s spelling.

Test 1 / 2 / 3 / 4 are vacuous because there is no producer to test against. The values are pure registry-clutter.

Verdict: **DEPRECATE-NO-FOLD**. The registry entries are removed in the Wave 6 registry-cleanup PR; no producer change required. Defensive consumer enumerations that include these strings (e.g., `cli.py:6066`'s `("dependency", "devDependency", "dev-dependency", "build-dependency")` filter) are simplified to drop the dead entries (after `devDependency` folds to `dependency` per shape #2 above, the filter further collapses to a single `"dependency"` membership test).

### 5. Tracker `Item.kind` is not a `Symbol.kind`

One value (`work_item`) appears in `SYMBOL_KINDS` but is emitted exclusively by the **hypergumbo-tracker** package's own data model — `migration.py` and `screenshot_save.py` create `Item` rows with `kind="work_item"`. The tracker's `Item.kind` is a different schema field on a different dataclass; it never flows into a `Symbol`.

Test 1-4 are not the right diagnostic shape here. The leak is one type-system step lower: a kind value defined for *one* container (`Symbol`) was pasted into another container's (`Item`) vocabulary — or the registry was expanded to cover both containers as if they shared an axis when they don't.

Verdict: **DEPRECATE-NO-FOLD** on the `Symbol.kind` registry side. The tracker's `Item.kind="work_item"` continues unchanged — different schema, different axis, not in scope of this ADR. The `SymbolKindSpec("work_item", …)` row is removed from `symbol_kinds.py` in the Wave 6 registry-cleanup PR. (A follow-up audit may declare a parallel `Item.kind` axis under ADR-0024's seven-step workflow if the tracker's vocabulary grows enough to need one; that is out of scope for ADR-0027.)

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: target
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"target\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Meson custom_target/vcs_tag and RST hyperlink-target are top-level constructs in their respective DSLs. Cross-DSL overload reads like function/package. Promote to language_construct."
  - value: special_target
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"special_target\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Make .PHONY / .SUFFIXES / .DEFAULT etc. are language-level keywords in Makefile syntax — distinct from regular targets. Promote to language_construct."
  - value: recipe
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"recipe\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Justfile recipe (name: deps) is the top-level Just language construct. Promote to language_construct."
  - value: env_var
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"env_var\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dockerfile ENV directive declares an environment variable — a top-level Dockerfile construct. Promote to language_construct."
  - value: build_arg
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"build_arg\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dockerfile ARG directive declares a build-time argument — a top-level Dockerfile construct. Promote to language_construct."
  - value: exposed_port
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"exposed_port\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dockerfile EXPOSE directive declares a network port the container intends to listen on — a top-level Dockerfile construct. Promote to language_construct."
  - value: stage
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"stage\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dockerfile FROM ... AS <stage> declares a multi-stage build stage — a top-level Dockerfile construct. Promote to language_construct."
  - value: derivation
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"derivation\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Nix mkDerivation / stdenv.mkDerivation is the core Nix-language construct for declaring a buildable artifact. Promote to language_construct."
  - value: addtask
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"addtask\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "BitBake addtask directive registers a task in the build graph — structurally distinct from defining a task body. Promote to language_construct."
  - value: task
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"task\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "BitBake task definition (foo() { ... } shell-style body) is the canonical task construct. Apex selection: 'task' over 'python_task' (which folds to task + meta). Promote to language_construct."
  - value: trigger
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"trigger\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SQL CREATE TRIGGER and Apex 'trigger Foo on Bar' are top-level constructs in their respective DSLs. Cross-DSL overload reads like function. Promote to language_construct."
  - value: test
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"test\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Zig 'test \"name\" { ... }' is a top-level Zig language keyword; Cargo [[test]] table is a top-level pyproject/Cargo build-DSL section. Apex selection: 'test' over 'test_case' (which folds to test). Promote to language_construct."
  - value: requirement
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"requirement\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "requirements.txt entry is the apex pip requirement construct. 'editable' and 'url_requirement' fold here under install-mode / install-source meta. Promote to language_construct."
  - value: dependency
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"dependency\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "pyproject.toml [project.dependencies], package.json dependencies, Composer require, Maven <dependency> — cross-ecosystem the same concept (a runtime dependency declaration). Apex: 'dependency'. 'devDependency' folds here under dependency_scope meta. Promote to language_construct."
  - value: setting
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"setting\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "INI file key=value pair. INI is the DSL; 'setting' is the natural name for the leaf construct. Promote to language_construct."
  - value: test_case
    verdict: FOLD
    fold_target: test
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"test_case\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Robot Framework *** Test Cases *** entries name the same concept as Zig/Cargo 'test' under a different DSL vocabulary. Apex selection: 'test'. Fold to test + meta['test_dialect']='robot'."
  - value: editable
    verdict: FOLD
    fold_target: requirement
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"editable\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "pip install -e form. Same construct as a plain requirement entry, with editable install mode. Fold to requirement + meta['install_mode']='editable'."
  - value: url_requirement
    verdict: FOLD
    fold_target: requirement
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"url_requirement\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "URL-form requirement (git+https://..., direct URL). Same construct as a plain requirement entry, with URL install source. Fold to requirement + meta['install_source']='url'."
  - value: devDependency
    verdict: FOLD
    fold_target: dependency
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"devDependency\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "package.json devDependencies + Composer require-dev. Same construct as a production dependency, with dev scope qualifier. Fold to dependency + meta['dependency_scope']='dev'."
  - value: python_task
    verdict: FOLD
    fold_target: task
    status: UNRESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"python_task\" and s.axis == \"pending_classification\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "BitBake 'python foo() { ... }' task. Same construct as a shell-style task, with Python implementation. Fold to task + meta['task_implementation']='python'."
  - value: config
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"config\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Prisma analyzer emits kind='config' for ALL Prisma schema blocks (generator, datasource, model, enum, view, type) with the real construct in meta['block_type']. Producer rewrites to emit kind=block_type directly; consumers query language='prisma' or the per-block-type kind."
  - value: dev-dependency
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"dev-dependency\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dead vocabulary — present in the registry but no analyzer or linker emits it. Drop the SymbolKindSpec entry; simplify defensive consumer enumerations (cli.py:6066) accordingly."
  - value: build-dependency
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"build-dependency\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dead vocabulary — present in the registry but no analyzer or linker emits it. Drop the SymbolKindSpec entry; simplify defensive consumer enumerations (cli.py:6066) accordingly."
  - value: work_item
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"work_item\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Tracker's Item.kind value, not a Symbol.kind. Emitted by hypergumbo_tracker.migration / screenshot_save on Item rows, never on Symbol. Drop the SymbolKindSpec entry; tracker continues using Item.kind='work_item' on its own schema unchanged."
```

## Migration impact

Wave 6 of the [WI-runod](../../.agent/tracker/) cross-axis Phase 3 sequencing schedule covers the actual producer migration. Sketched here for context:

1. **Registry update (mechanical, doc-only PR):** lift the 15 CANONICAL values (`target`, `special_target`, `recipe`, `env_var`, `build_arg`, `exposed_port`, `stage`, `derivation`, `addtask`, `task`, `trigger`, `test`, `requirement`, `dependency`, `setting`) from `AXIS_PENDING` to `AXIS_LANGUAGE_CONSTRUCT` in `symbol_kinds.py`. Drop the four DEPRECATE-NO-FOLD registry entries (`dev-dependency`, `build-dependency`, `work_item`, and `config` — the latter after the prisma producer rewrite ships). Update the row statuses in this audit-findings doc from `UNRESOLVED` → `RESOLVED` in the same PR.

2. **Per-producer FOLD migration (one PR per producer surface):** the five FOLD values shipped from the producer side, mirroring ADR-0023's per-family migration shape. Each producer folds the qualified kind to its sibling apex and routes the qualifier to `Symbol.meta`:
   - `robot.py` — `test_case` → `test` + `meta["test_dialect"]="robot"`
   - `requirements.py` — `editable` → `requirement` + `meta["install_mode"]="editable"`; `url_requirement` → `requirement` + `meta["install_source"]="url"`
   - `json_config.py` — `devDependency` → `dependency` + `meta["dependency_scope"]="dev"` (both package.json + Composer require-dev call sites)
   - `bitbake.py` — `python_task` → `task` + `meta["task_implementation"]="python"`

   Per-PR `awaits_bakeoff_validation` tag where the change crosses the centrality / slice / sketch surfaces.

3. **DEPRECATE-NO-FOLD migrations (two small PRs):**
   - `prisma.py:179` rewrites the producer to emit `kind=block_type` directly (matching the existing `meta["block_type"]` value), dropping `kind="config"`. The audit-findings row moves to RESOLVED once the registry pruning lands. Some of the resulting block_type values (`model`, `enum`, `type`, `view`) already exist in the Cluster A registry; `generator` and `datasource` may need new registry entries on `language_construct` (they are top-level Prisma constructs).
   - The two dead-vocabulary entries (`dev-dependency`, `build-dependency`) and the cross-schema entry (`work_item`) ship in the same registry-cleanup PR with `cli.py:6066`'s defensive enumeration simplified accordingly.

The `meta` keys introduced by Wave 6 (`test_dialect`, `install_mode`, `install_source`, `dependency_scope`, `task_implementation`) are candidates for ADR-0029's `axis_meta_keys.py` registry (Wave 9, WI-vusot).

## Related

- [ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct](../adr/0027-symbol-kind-language-construct-only.md) — the originating axis declaration. §"Phase 3" Cluster G is the scope this audit covers.
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — §"Family-audit verdict methodology" defines the verdict trichotomy applied here; §"Fold-residue discipline" rule 3 is the recurrence-promotion threshold the meta keys named here will trigger.
- [Audit-findings 0003](0003-symbol-kind-cluster-a-language-constructs.md) — sibling Cluster A audit on the same axis. The CANONICAL promotions named here will join 0003's seed once Wave 6 ships.
- [Audit-findings 0005](0005-symbol-kind-cluster-b-file-shape.md) — sibling Cluster B audit; the FOLD-with-qualifier-to-meta pattern used here mirrors 0005's framework-qualified-file-representation pattern.
- [`docs/audits/README.md`](README.md) — format spec.
- WI-runod (cross-axis Phase 3 sequencing schedule) — this document is Wave 1 in the schedule; Wave 6 acts on its verdicts.
- WI-vusot (axis_meta_keys.py parallel registry, Wave 9) — consumes the meta keys listed in §"Migration impact" once Wave 6 ships.
