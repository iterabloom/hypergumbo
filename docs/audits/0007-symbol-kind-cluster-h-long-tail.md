<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0007: Symbol.kind Cluster H — Domain-Specific Long Tail

- Date: 2026-05-05
- Status: All rows resolved — 57 CANONICAL rows RESOLVED (53 via Wave 6 PR 2 registry promotion + 3 via Wave 6 PR 4 reclassification of `theorem`/`inductive`/`message` + 1 via Wave 6 PR 6 reclassification of `external_symbol`); 3 DEPRECATE-NO-FOLD rows PRELIM_RESOLVED (`heading`, `model` via Wave 6 PR 4; `unresolved` via Wave 6 PR 6).
- Closes: WI-nupus-fovor-rataf-momub-natit-hihir-bufas-bukih (Cluster H domain long-tail per-value audit, ADR-0027 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Fourth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md), companion to audit-findings 0003 (Cluster A canonical), 0005 (Cluster B file-shape), and 0006 (Cluster G build/config-shape).

## Context

[ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) §"Phase 3" Cluster H is the long tail of `Symbol.kind` values currently parked on `pending_classification` — domain-vocabulary nouns, control-flow constructs, and per-language synthetic kinds emitted by the documentation, template, configuration, query, hardware-description, theorem-prover, and notebook analyzers in `packages/hypergumbo-lang-*`. The ADR predicted (§"Risks") this would be the longest audit in the series and the one most likely to surface separate-axis pressure.

The registry's Cluster H section seeds 61 values. One of them — `structure` — is explicitly noted in the registry as a Cluster C apex/peer fold target (Cluster C audit, WI-rusit per the [WI-runod](../../.agent/tracker/) cross-axis schedule). It is excluded from this audit and left to the cluster-C verdict. The remaining **60 values** are: `section`, `paragraph`, `heading`, `code_block`, `diagram`, `plot`, `yield`, `for_loop`, `conditional`, `block`, `prefix`, `base`, `query`, `entry`, `entity`, `architecture`, `participant`, `state`, `model`, `fragment`, `partial`, `provider`, `local`, `style_block`, `permission`, `keyframes`, `media`, `font_face`, `class_selector`, `id_selector`, `rule_set`, `subdirectory`, `table`, `table_array`, `link`, `label`, `command`, `environment`, `binding`, `id`, `source`, `port`, `output`, `input`, `value`, `pattern`, `subscript`, `signal`, `message`, `data`, `resource`, `event`, `protocol`, `index`, `node`, `inductive`, `theorem`, `playbook`, `external_symbol`, `unresolved`.

The cluster-H framing question (per WI-nupus and ADR-0027 §"Risks"): are these *language constructs* in their respective DSLs (CSS, SQL, HCL/Terraform, Mermaid, GraphQL, LaTeX, Markdown, BibTeX, gnuplot, Vue, Svelte, Twig, Handlebars, Blade, QML, VHDL, SPARQL, Nix, COBOL, Swift, Objective-C, Elm, F#, R, Ansible YAML, gitignore, …), or do a non-trivial subset leak Cluster D / E shape (`*_handler` framework qualifiers, `*_call` relationship-shaped kinds), warranting per-value FOLD verdicts or a separate axis ADR?

This audit answers (post Wave 6 PR 4 + PR 6 reclassifications — see §"Diagnostic findings" #3 and #4): **57 CANONICAL** (each is a top-level construct in the source language at hand, including the IR pipeline's own pseudo-DSL — the cross-DSL overload pattern reads like `function` or `package` across general-purpose languages, not as a leak), **0 FOLD** (no sub-mode/scope qualifier shapes surfaced once Cluster G's audit absorbed the `task`/`python_task`/`addtask` scope-qualifier shape), and **3 DEPRECATE-NO-FOLD** (1 dead vocabulary with no producer at all — `heading`; 1 ID-string-only synthetic — `model`; 1 registry seed error — `unresolved`, where the producer-side trace at Wave 6 PR 6 showed no `Symbol(kind='unresolved')` emission exists).

All rows ship at `UNRESOLVED` status because Phase 3 producer migrations + registry updates have not landed. Wave 6 of the WI-runod cross-axis schedule covers the migration; this document is the precondition for that wave.

**Scope decision (no new ADR).** Per ADR-0027 §"Phase 3" Cluster H, an alternate path was per-language sub-axes (`Symbol.css_construct`, `Symbol.template_construct`, `Symbol.shader_construct`, …). After running the four leakage tests on each of the 60 values, that path is rejected: every CANONICAL value names a *category* of source-language construct in its own DSL, and the cross-language overload is the same shape as `function` appearing in every general-purpose language. No new axis ADR is filed.

The "Cluster D/E shape leakage" handoff hypothesis (handler / signal / model / entity reading as framework qualifiers rather than constructs) was tested and **largely refuted at the producer level**:

- `signal` is emitted by `qml.py` for QML's `signal` keyword — a top-level QML language declaration, not a `*_signal` framework-role qualifier. **CANONICAL.**
- `entity` is emitted by `vhdl.py` for VHDL's `entity` keyword (a hardware-interface declaration), not a `*_entity` framework-role qualifier. **CANONICAL.**
- `model` is emitted only as a `kind=` argument to `compute_stable_id` and `make_symbol_id` in `prisma.py:120` — both are *ID-construction* call sites; the actual `Symbol(kind=…)` field at the same site is `kind="class"` (line 122). **DEPRECATE-NO-FOLD on the Symbol.kind axis** because no Symbol is ever emitted with `kind="model"`. (The `model` token persists in the symbol_id format string for stable identity; this audit does not change that.)
- `event` is emitted by `svelte.py` for Svelte's `on:event` event-dispatch declaration (not a `*_event` framework qualifier). **CANONICAL.** (Note: the related Cluster D values `event_publisher`, `event_subscriber`, `event_handler` are already on `endpoint_shape` and queued for Wave 5 framework-dispatch fold per ADR-0027.)
- `handler` does not appear in the registry on `pending_classification` — it's emitted at non-Symbol surfaces and is being absorbed by Wave 5's framework-dispatch fold. Out of scope here.

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the four-leakage-test diagnostic procedure are defined in [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). This document applies that methodology to the Cluster H subset of `Symbol.kind` values.

The `diagnostic_test` field for each row is a one-line Python invocation that asserts the value is currently present on the `pending_classification` axis (the Phase 1 seed home). The `expect: exit_code:0` shape lets a future audit runner verify the row's claim that the value is still pending migration.

## Diagnostic findings

Five distinct shapes surfaced during the four-leakage-test pass.

### 1. Documentation-language top-level constructs

Eleven values are emitted from documentation-language analyzers — Markdown, RST, LaTeX, BibTeX, COBOL (the data-section/paragraph DSL), gnuplot, KDL, Blade, INI — for declarations that are top-level structural constructs in the source documentation language at hand: `section`, `paragraph`, `heading`, `code_block`, `link`, `diagram`, `plot`, `entry`, `command`, `environment`, `label`. Markdown's fenced code block, RST's section header, LaTeX's `\newcommand` / `\begin{env}` / `\label{}`, BibTeX's `@article{...}`, COBOL paragraph, gnuplot's `plot` command, INI's section header, Blade's `@yield`, Mermaid's diagram declaration are language-level keywords / declarations the analyzer parses out of source AST.

Cross-DSL overload fires mildly on `section` (Markdown, RST, LaTeX, INI, KDL, COBOL, Blade — seven producers for the same general "structural section" concept) and on `link` (Markdown vs YAML anchor variants), but the overload is "the same general concept across documentation DSLs," same shape as `function` across general-purpose languages. Test 1 (property derivability) does not fire — none is derivable from another field. Test 2 (apex/peer overloading) is not the right shape: these are *cross-language overloads*, not in-language synonyms. Test 3 (construct vs. relationship) and Test 4 (mechanism vs. category) do not fire.

`heading` is the exception: registry-present but no producer emits `kind="heading"` (Markdown headings emit as `kind="section"`). Verdict: **DEPRECATE-NO-FOLD** for `heading`. The other ten get **CANONICAL**.

### 2. Source-language top-level constructs across hardware, template, query, theorem, hosting DSLs

Most of the remaining cluster — VHDL, Swift, Objective-C, Elm, F#, R, GraphQL, SPARQL, Vue, Svelte, Twig, Handlebars, QML, Nix, gitignore, Ansible YAML, Mermaid (the diagram-component subtree), CMake (the directory subtree), CSS, SCSS, SQL, TOML, HCL/Terraform, Puppet, COBOL data-division, XML config — emits values that are top-level constructs in the source-language at hand:

- VHDL: `entity` (hardware-interface declaration), `architecture` (entity-implementation block).
- Swift: `protocol` (Swift `protocol Foo { … }`), `subscript` (Swift `subscript(idx: Int) -> T`).
- Objective-C: `protocol` (`@protocol Foo { … }`).
- Elm: `port` (Elm `port` declaration for FFI).
- F#: `value` (`let foo = …` value binding — F#'s primary value-introduction construct).
- R: `source` (R's `source()` directive symbol — top-level reference shape).
- GraphQL: `fragment` (`fragment Foo on Type { … }` declaration).
- SPARQL: `query` (named SPARQL query block), `prefix` (`PREFIX foo: …` declaration), `base` (`BASE …` declaration).
- Vue: `style_block` (`<style scoped> … </style>` block).
- Svelte: `block` (Svelte block expression like `{#if}…{/if}`), `event` (Svelte `on:event` declaration).
- Twig: `block` (`{% block … %}`), `conditional` (`{% if %}`), `for_loop` (`{% for %}`).
- Handlebars: `block` (`{{#each}}`), `partial` (`{{> name}}` partial reference declaration).
- QML: `signal` (QML `signal foo()` declaration), `id` (QML `id: foo` property).
- Nix: `binding` (Nix `let bind = …` binding), `input` (Nix function input parameter).
- gitignore: `pattern` (gitignore pattern entry).
- Ansible YAML: `playbook` (top-level playbook declaration).
- Mermaid: `participant` (sequence-diagram participant), `state` (state-diagram state), `node` (flowchart node).
- CMake: `subdirectory` (`add_subdirectory()` directive).
- CSS / SCSS: `keyframes` (`@keyframes`), `media` (`@media`), `font_face` (`@font-face`), `class_selector` (`.foo`), `id_selector` (`#foo`), `rule_set` (full rule set).
- SQL / TOML: `table` (CREATE TABLE / TOML `[table]`), `table_array` (TOML `[[arr]]`), `index` (SQL CREATE INDEX).
- HCL / Terraform: `resource` (`resource "type" "name" { … }`), `data` (`data` block), `local` (`locals` block), `provider` (`provider` block), `output` (`output` block).
- Puppet: `node` (Puppet node block), `resource` (Puppet resource declaration).
- COBOL: `data` (DATA DIVISION).
- GLSL: `input` (`in` qualifier), `output` (`out` qualifier).
- XML config (Android manifest etc.): `permission`.
- Robot: `resource` (Robot Framework resource file).
- Blade (extending the doc-language group): `yield` (`@yield('section')`).
- LaTeX: `command`, `environment`, `label` (already in doc-language group).
- gnuplot: `plot` (already in doc-language group).

Test 4 (mechanism vs. category) does not fire — each value names a *category* of source-language construct in the DSL. Test 1 (property derivability) does not fire — the value is not derivable from another field. Test 2 (apex/peer overloading) fires for `block` (Handlebars, Svelte, Twig — three template-language analyzers each emit `block` for their "block expression" construct, which has cross-DSL but not in-DSL overload), but the same name reads as the same general concept across templating DSLs. Test 3 (construct vs. relationship) does not fire.

These 41 values get verdict **CANONICAL**.

### 3. Boundary-pseudo-symbol kinds

Two values (`external_symbol`, `unresolved`) appear in the registry as pipeline-layer pseudo-categories. Wave 6 PR 6 traced both end-to-end through producers and consumers, and the trace produced a different verdict than the original audit predicted. The corrected trace:

- `external_symbol` — single producer at `ir.py:959` (`create_boundary_nodes`). Materializes one boundary Symbol per (language, key_path, name, kind) group of dangling edge endpoints. The kind is hardcoded to `"external_symbol"` regardless of what `_parse_dangling_id` extracts from the trailing slot of the dangling IDs.
- `unresolved` — **no producer.** The string `"unresolved"` appears only as: (1) a trailing token in dangling-edge dst IDs created by `make_unresolved_call_edge` at `analyze/base.py:401` (format: `{lang}:{module_hint}:0-0:{callee_name}:unresolved`); (2) a `_parse_dangling_id` fallback at `ir.py:805` for malformed IDs (defensive code, never reaches the materializer's kind slot); (3) the path-slot sentinel in rust.py's trait dangling IDs (`make_symbol_id("rust", "unresolved", ...)` — `"unresolved"` is in the path slot, not the kind slot). The IR boundary materializer always emits `kind="external_symbol"`, never `kind="unresolved"`.

**Consumer query pattern.** No consumer in the codebase reads `Symbol.kind == "external_symbol"` (or `"unresolved"`) as a discriminator. The boundary-status check is centralized in `is_external_boundary(sym)` (`ir.py:657`) which reads `meta["external_boundary"]=True`. Compact and CLI noise filters use that helper; runtime-coherence partitioning includes the kind in a tuple key but doesn't condition on the literal value. The kind values are labels, not load-bearing classifiers.

**ADR-0028 adjacency: not applicable.** ADR-0028 introduced `Edge.is_resolved` because the resolution-status property leaked into ~10 paired `*_resolved` / `*_unresolved` evidence types across 4+ producer modules. That accumulation pressure motivated promotion to a sibling field per ADR-0024 §"Fold-residue discipline" rule 3 (recurrence threshold: ≥3 distinct values OR ≥2 producer modules). The Symbol.kind situation does not meet that threshold: 1 producer, 0 consumer reads, 0 paired-suffix variants. The original audit's framing ("`kind="unresolved"` smuggles resolution-status onto Symbol.kind") was wrong on the facts — `Symbol(kind="unresolved")` has no producer. A `Symbol.is_resolved` sibling field would also be semantically distinct from `Edge.is_resolved` (the former asks "is this Symbol record a placeholder?"; the latter asks "did the dst lookup succeed?") — sharing a name without sharing semantics would be more confusing than clarifying.

**Verdict (Wave 6 PR 6, replacing the original deferred verdict):**

- `external_symbol` — **CANONICAL**, status RESOLVED. Promoted to `language_construct` in `symbol_kinds.py` as a pipeline-DSL top-level construct, parallel to other Cluster H domain-DSL constructs (`playbook`, `participant`, `fragment`). Consumer behavior is unchanged because consumers never queried the kind value.
- `unresolved` — **DEPRECATE-NO-FOLD**, status PRELIM_RESOLVED. Registry seed error (no producer); advanced to `endpoint_shape` for symmetry with the rest of Cluster H's vacuous DEPRECATE-NO-FOLD entries. The previously-anticipated `Symbol.is_resolved` follow-on ADR is **withdrawn** — the trace showed there is no leak to absorb. If a future situation surfaces multiple Symbol-side resolution-status producers, that's the signal to revisit; until then, ADR-0028's `Edge.is_resolved` covers the only resolution-status surface that exists.

### 4. Dead vocabulary

One value (`heading`) is present in the `SymbolKindSpec` registry but **no analyzer or linker emits it** as Symbol.kind — placeholder vocabulary that never landed a producer:

- `heading` is presumably for Markdown headings, but Markdown emits `section` (not `heading`) at `markdown.py:198` for those.

(`model` joins this group via a slightly different shape — see #5 below.)

Test 1-4 are vacuous because there is no producer to test against. The value is pure registry-clutter.

Verdict: **DEPRECATE-NO-FOLD**. The registry entry advances to `endpoint_shape` (Wave 6 PR 4) and stays through the Phase 4a deprecation window. Pruning ships in the Phase 4b registry-cleanup PR.

#### Reclassification correction (Wave 6 PR 4)

The original audit listed `inductive`, `theorem`, and `message` alongside `heading` as "dead vocabulary." This was a literal-grep blind-spot miss: the Coq / Lean / TLA+ / Protobuf analyzers emit these kinds, but via an indirection (helper `add_symbol(node, name, "<kind>")` or `_make_proto_symbol(..., "<kind>", ...)`) that the original audit's `grep -rn 'kind=["\047]<value>["\047]'` pattern did not catch. This is the same blind-spot family as WI-nubuv extension A surfaced for assignment-form / f-string producers in 2026-05.

Wave 6 PR 4 reclassifies all three values as **CANONICAL** — top-level constructs in their respective DSLs:

- `inductive` — Lean inductive type declaration (`lean.py:247`)
- `theorem` — Lean theorems / lemmas (`lean.py:222,231`) + TLA+ theorems (`tlaplus.py:207`)
- `message` — Protobuf `message` declaration (`proto.py:260` via `_make_proto_symbol`)

The cross-DSL overload pattern (Lean `theorem` vs TLA+ `theorem`) reads like the cross-language `function` overload — same general concept across multiple theorem-prover tongues, not a leak. Promoted to `language_construct` in `symbol_kinds.py`.

The `message_handler` / `message_sender` peer endpoint_shape kinds remain on a separate surface (Cluster D framework_role fold per audit-findings 0013); promoting `message` does not affect them.

### 5. ID-string-only synthetic kinds

One value (`model`) appears in the registry but is emitted only at `prisma.py:120` as the `kind=` argument to `compute_stable_id(node, kind="model")` and `make_symbol_id(…, name, "model")` — both are *ID-construction* call sites. The actual `Symbol(kind=…)` field at the same emit site (line 122) is `kind="class"` — the prisma analyzer folds Prisma `model` blocks to the canonical Cluster A `class` kind for downstream consumption.

The `model` token therefore persists in the *symbol_id format string* (the persisted ID has `…:Foo:model` as its last components) for stable identity / round-trip purposes, but no Symbol is ever emitted with `Symbol.kind == "model"`.

Test 1 (property derivability) fires hard: `model` as a Symbol.kind value is fully derivable as "anything emitted by prisma.py at a model_block node" — but more concretely, no Symbol with `kind="model"` exists in the live tree; the registry entry is shadowing the actual emission.

Verdict: **DEPRECATE-NO-FOLD** on the Symbol.kind axis. The `prisma.py` producer continues using `"model"` in its ID-construction path unchanged (the symbol_id format is a separate stability contract from the Symbol.kind axis). The `SymbolKindSpec("model", …)` row is removed from `symbol_kinds.py` in the Wave 6 registry-cleanup PR. This pattern (ID-string-only synthetic kind shadowing the emitted Symbol.kind) is worth documenting as an audit failure mode for future Cluster N audits — it is structurally identical to dead vocabulary but harder to detect because the value *is* present at producer call sites, just not on the Symbol field.

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: section
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"section\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Top-level structural construct in seven documentation/config DSLs (Markdown, RST, LaTeX, INI, KDL, COBOL, Blade). Cross-DSL overload like function/package. Promote to language_construct."
  - value: paragraph
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"paragraph\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "COBOL paragraph is a language-level construct between section and statement in the COBOL hierarchy. Promote to language_construct."
  - value: code_block
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"code_block\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Markdown fenced code block is a top-level Markdown structural construct. Promote to language_construct."
  - value: diagram
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"diagram\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Mermaid diagram declaration is the top-level Mermaid construct. Promote to language_construct."
  - value: plot
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"plot\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "gnuplot 'plot' command is a top-level gnuplot construct. Promote to language_construct."
  - value: yield
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"yield\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Blade @yield('section') is a top-level Blade template directive. Promote to language_construct."
  - value: for_loop
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"for_loop\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Twig {% for %} block is a top-level Twig control-flow construct (Twig is a template DSL where control-flow blocks are first-class structural elements, not statements). Promote to language_construct."
  - value: conditional
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"conditional\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Twig {% if %} block is a top-level Twig control-flow construct (template DSL). Promote to language_construct."
  - value: block
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"block\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Block expression in three template DSLs (Handlebars {{#each}}, Svelte {#if}, Twig {% block %}). Cross-DSL overload like function. Promote to language_construct."
  - value: prefix
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"prefix\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SPARQL PREFIX declaration is a top-level SPARQL construct. Promote to language_construct."
  - value: base
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"base\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SPARQL BASE declaration is a top-level SPARQL construct. Promote to language_construct."
  - value: query
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"query\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SPARQL named query block is a top-level SPARQL construct. Promote to language_construct."
  - value: entry
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"entry\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "BibTeX @article{...} / @book{...} entry is the top-level BibTeX construct. Promote to language_construct."
  - value: entity
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"entity\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "VHDL entity keyword declares a hardware-interface — a top-level VHDL construct. NOT a Cluster D framework qualifier (despite handoff hypothesis). Promote to language_construct."
  - value: architecture
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"architecture\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "VHDL architecture keyword declares an entity-implementation block — a top-level VHDL construct. Promote to language_construct."
  - value: participant
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"participant\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Mermaid sequence-diagram participant declaration is a top-level Mermaid sequence-diagram construct. Promote to language_construct."
  - value: state
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"state\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Mermaid state-diagram state declaration is a top-level Mermaid construct. Promote to language_construct."
  - value: fragment
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"fragment\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "GraphQL fragment definition is a top-level GraphQL construct. Promote to language_construct."
  - value: partial
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"partial\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Handlebars {{> name}} partial-reference declaration is a top-level Handlebars template construct. Promote to language_construct."
  - value: provider
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"provider\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "HCL/Terraform 'provider' block is a top-level Terraform construct. Promote to language_construct."
  - value: local
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"local\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "HCL/Terraform 'locals' block declares a local-value binding — a top-level Terraform construct. Promote to language_construct."
  - value: style_block
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"style_block\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Vue <style scoped>...</style> block is a top-level Vue SFC construct. Promote to language_construct."
  - value: permission
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"permission\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "XML config (Android manifest etc.) permission declaration is a top-level construct in those manifest DSLs. Promote to language_construct."
  - value: keyframes
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"keyframes\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CSS @keyframes at-rule is a top-level CSS construct. Promote to language_construct."
  - value: media
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"media\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CSS @media at-rule is a top-level CSS construct. Promote to language_construct."
  - value: font_face
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"font_face\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CSS @font-face at-rule is a top-level CSS construct. Promote to language_construct."
  - value: class_selector
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"class_selector\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CSS class selector (.foo) is a top-level CSS construct. Promote to language_construct."
  - value: id_selector
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"id_selector\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CSS id selector (#foo) is a top-level CSS construct. Promote to language_construct."
  - value: rule_set
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"rule_set\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SCSS / shader rule-set is a top-level CSS-family construct. Promote to language_construct."
  - value: subdirectory
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"subdirectory\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "CMake add_subdirectory() directive declares a CMake subdirectory inclusion — a top-level CMake construct. Promote to language_construct."
  - value: table
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"table\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SQL CREATE TABLE and TOML [table] are top-level constructs in their respective DSLs. Cross-DSL overload like package. Promote to language_construct."
  - value: table_array
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"table_array\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "TOML [[arr]] table-array is a top-level TOML construct. Promote to language_construct."
  - value: link
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"link\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Markdown link inline construct is a top-level Markdown reference construct. Promote to language_construct."
  - value: label
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"label\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "LaTeX \\label{} is a top-level LaTeX cross-reference construct. Promote to language_construct."
  - value: command
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"command\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "LaTeX \\newcommand declaration is a top-level LaTeX construct. Promote to language_construct."
  - value: environment
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"environment\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "LaTeX \\begin{env}...\\end{env} environment is a top-level LaTeX construct. Promote to language_construct."
  - value: binding
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"binding\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Nix let binding / attribute binding is a top-level Nix construct. Promote to language_construct."
  - value: id
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"id\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "QML 'id: foo' property declaration is a QML language-level identity declaration. Promote to language_construct."
  - value: source
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"source\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "R source() directive symbol is a top-level R reference shape (analogous to Python import). Promote to language_construct."
  - value: port
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"port\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Elm 'port' declaration for FFI is a top-level Elm construct. Promote to language_construct."
  - value: output
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"output\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "HCL/Terraform 'output' block + GLSL 'out' qualifier. Cross-DSL overload like function. Promote to language_construct."
  - value: input
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"input\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "GLSL 'in' qualifier + Nix function input parameter. Cross-DSL overload like function. Promote to language_construct."
  - value: value
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"value\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "F# 'let foo = ...' value binding — F#'s primary value-introduction construct (analogous to variable in other languages). Promote to language_construct."
  - value: pattern
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"pattern\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "gitignore pattern entry is a top-level gitignore-DSL construct. Promote to language_construct."
  - value: subscript
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"subscript\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Swift subscript keyword declares a subscript operator overload — a top-level Swift construct. Promote to language_construct."
  - value: signal
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"signal\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "QML 'signal foo()' declaration is a top-level QML language construct. NOT a Cluster D framework qualifier (despite handoff hypothesis — the producer is a real language keyword). Promote to language_construct."
  - value: data
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"data\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "HCL/Terraform 'data' block + COBOL DATA division. Cross-DSL overload like package. Promote to language_construct."
  - value: resource
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"resource\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "HCL/Terraform 'resource' block + Puppet resource declaration + Robot Framework resource file. Cross-DSL overload. Promote to language_construct."
  - value: event
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"event\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Svelte on:event event-dispatch declaration is a top-level Svelte construct. NOT a Cluster D framework qualifier (the related event_publisher / event_subscriber / event_handler endpoint-shape values are already on endpoint_shape and out-of-scope here). Promote to language_construct."
  - value: protocol
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"protocol\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Swift 'protocol' / Objective-C '@protocol' declaration is a top-level construct. Cross-DSL overload like class. Promote to language_construct."
  - value: index
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"index\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "SQL CREATE INDEX is a top-level SQL construct. Promote to language_construct."
  - value: node
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"node\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Mermaid flowchart node + Puppet node block. Cross-DSL overload like function. Promote to language_construct."
  - value: playbook
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"playbook\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Ansible YAML playbook declaration is a top-level Ansible-DSL construct. Promote to language_construct."
  - value: heading
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"heading\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dead vocabulary — registry-present but no producer emits kind='heading' (Markdown headings emit as kind='section' at markdown.py:198). Registry entry advanced to endpoint_shape in Wave 6 PR 4."
  - value: inductive
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"inductive\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Lean ``inductive`` type declaration is a top-level Lean-language construct. Reclassified Wave 6 PR 4 — original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss. lean.py:247 emits via add_symbol(child, name, 'inductive') indirection. Promoted to language_construct."
  - value: theorem
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"theorem\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Theorem-prover top-level construct: Lean theorems and lemmas at lean.py:222,231 plus TLA+ theorems at tlaplus.py:207. Reclassified Wave 6 PR 4 — original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss. Both producers emit via add_symbol(..., 'theorem') indirection. Promoted to language_construct."
  - value: message
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"message\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Protobuf ``message`` declaration is a top-level Protobuf-language construct. Reclassified Wave 6 PR 4 — original DEPRECATE-NO-FOLD verdict was a literal-grep blind-spot miss. proto.py:260 emits via _make_proto_symbol(..., 'message', ...) indirection. The peer message-dispatch endpoint_shape kinds (message_handler / message_sender) cover a different surface and are unaffected. Promoted to language_construct."
  - value: model
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"model\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "ID-string-only synthetic — prisma.py:120 passes kind='model' to compute_stable_id / make_symbol_id but emits the actual Symbol with kind='class' at line 122. No Symbol.kind='model' is ever emitted. Registry entry advanced to endpoint_shape in Wave 6 PR 4."
  - value: external_symbol
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"external_symbol\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "IR-pipeline boundary pseudo-symbol (single producer at ir.py:959 via create_boundary_nodes). Reclassified Wave 6 PR 6 — re-trace showed (1) no consumer reads kind=='external_symbol' as a discriminator (consumers query is_external_boundary(sym) which checks meta['external_boundary']), (2) the kind is a label not a load-bearing classifier, and (3) ADR-0024 §'Fold-residue discipline' rule 3's recurrence threshold (≥3 distinct values OR ≥2 producer modules) is not met (1 producer, 0 consumer reads). Promoted to language_construct as a pipeline-DSL top-level construct, parallel to other Cluster H domain-DSL constructs (playbook, participant, fragment)."
  - value: unresolved
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"unresolved\" and s.axis == \"endpoint_shape\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Registry seed error per Wave 6 PR 6 trace — no Symbol(kind='unresolved') producer exists. The string 'unresolved' appears only as a trailing token in dangling-edge dst IDs created by analyze/base.py:make_unresolved_call_edge ({lang}:{module_hint}:0-0:{name}:unresolved); that attribute is captured by Edge.is_resolved=False per ADR-0028, not as a Symbol.kind value. The IR boundary materializer (ir.py:954-967) always emits kind='external_symbol' regardless of what _parse_dangling_id extracts from the trailing slot. Registry entry advanced to endpoint_shape in Wave 6 PR 6 (vacuous producer migration — no producer to drop)."
```

## Migration impact

Wave 6 of the [WI-runod](../../.agent/tracker/) cross-axis Phase 3 sequencing schedule covers the actual producer migration. Sketched here for context:

1. **Registry update (mechanical, doc-only PR):** lift the 52 CANONICAL values from `AXIS_PENDING` to `AXIS_LANGUAGE_CONSTRUCT` in `symbol_kinds.py`. Drop the 5 dead-vocabulary / ID-string-only entries (`heading`, `inductive`, `theorem`, `message`, `model`). Update the row statuses in this audit-findings doc from `UNRESOLVED` → `RESOLVED` in the same PR.

2. **Boundary-pseudo-symbol resolution (deferred PR pending follow-on ADR):** the two `external_symbol` / `unresolved` rows do not migrate in Wave 6. They wait on a follow-on ADR (parallel to ADR-0028) that decides whether `Symbol` deserves an `is_resolved` sibling field for boundary-symbol resolution status. Once that ADR lands, this audit's two DEPRECATE-NO-FOLD rows resolve to either FOLD (if the sibling field ships) or CANONICAL (if it doesn't). Tracked at the parent ADR-0027.

3. **No per-producer FOLD migration in this cluster.** Unlike Cluster B (10 FOLD rows) or Cluster G (5 FOLD rows), no Cluster H value carries a sub-mode / scope / framework qualifier that needs to fold to a sibling apex. The cluster is empirically clean once the dead vocabulary and ID-string-only synthetic are pruned.

This is the largest single-cluster registry promotion in the ADR-0027 series (52 of ~100 originally-pending values). After Wave 6, only Cluster C (apex/peer overloads, WI-rusit) and the deferred boundary-pseudo rows remain on `pending_classification`; Clusters D/E continue to migrate per Waves 4-5.

## Related

- [ADR-0027: Symbol.kind Names the Source-Language Syntactic Construct](../adr/0027-symbol-kind-language-construct-only.md) — the originating axis declaration. §"Phase 3" Cluster H is the scope this audit covers.
- [ADR-0028: Edge.evidence_type Names the Inference Pathway](../adr/0028-evidence-type-inference-pathway-only.md) — the Edge.is_resolved sibling-field pattern referenced in §"Diagnostic findings" #5 for the boundary-pseudo-symbol resolution-status question.
- [ADR-0024: Axis Declaration Template](../adr/0024-axis-declaration-template.md) — §"Family-audit verdict methodology" defines the verdict trichotomy applied here; §"Fold-residue discipline" rule 3 defines the recurrence-promotion threshold the (intentionally empty) Wave 6 meta keys would otherwise trigger.
- [Audit-findings 0003](0003-symbol-kind-cluster-a-language-constructs.md) — sibling Cluster A audit on the same axis. The CANONICAL promotions named here will join 0003's seed once Wave 6 ships.
- [Audit-findings 0005](0005-symbol-kind-cluster-b-file-shape.md) — sibling Cluster B audit; same fold-pattern shape (CANONICAL + DEPRECATE-NO-FOLD) but with a non-trivial FOLD subset. Cluster H surfaced no FOLD rows — reflective of the long-tail values being mostly first-class language constructs in their respective DSLs rather than framework-qualified variants.
- [Audit-findings 0006](0006-symbol-kind-cluster-g-build-config-shape.md) — sibling Cluster G audit; absorbed several values originally listed in Cluster H scope (`task`, `python_task`, `addtask`).
- [`docs/audits/README.md`](README.md) — format spec.
- WI-runod (cross-axis Phase 3 sequencing schedule) — this document is Wave 1 in the schedule; Wave 6 acts on its verdicts.
- WI-rusit (Cluster C apex/peer fold, Wave 4) — covers the `structure` value (apex/peer of `struct`) explicitly excluded from this audit's scope.
