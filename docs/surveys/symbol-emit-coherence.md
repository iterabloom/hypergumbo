<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: Symbol Emit-Site Coherence (2026-05-30)

- Status: Mixed — Dimension 1 mostly RESOLVED; Dimensions 2 & 3 ongoing
- Date: 2026-05-30
- Informed (Dimension 1, RESOLVED): the analyzer-side language-tag drifts for `objective-c` and `ansible` (PR #3984); the linker-side non-catalog `protobuf` emit (PR #3986).
- Tracks (UNRESOLVED, awaiting ADR-0031 migration): `unknown`, `wasm`, `openapi` linker-side sentinels.
- Methodology: `~/hypergumbo_lab_notebook/symbol-emit-coherence-audit-playbook.md` (the playbook that produced this survey; it's a lab-notebook artifact, not under `docs/`). Companion artifact: the linker-language-provenance supplement at `~/hypergumbo_lab_notebook/audit-supplement-linker-language-provenance-05302026.md` (integrated as §"Supplement").

## Why this is a survey, not an audit-findings document

The `docs/audits/` directory is reserved for axis-conformance audit-findings documents — per-value verdicts from the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy applied to a declared registry-backed axis (`Edge.edge_type`, `Symbol.kind`, `Edge.evidence_type`). Per `docs/audits/README.md` §"Scope": "Audits with different verdict shapes ... should propose a sibling format rather than shoehorn into this one." Per `docs/adr/README.md` bucket rubric: descriptive snapshots and catalog-style inventories live at `docs/surveys/<topic>.md`.

This survey extends past the trichotomy in three ways:
- Dimension 1 (catalog conformance on `Symbol.language`) does produce per-value verdicts, but the language axis is catalog-derived lightweight per ADR-0024 §4 — not one of the three heavyweight registry-backed axes the strict audit-findings format validates against.
- Dimension 2 (format conformance for `Symbol.id` / `stable_id`) is a diagnostic-pattern report, not a vocabulary verdict.
- Dimension 3 (per-language field-population parity) is a coverage matrix, not a vocabulary verdict.

The supplement's three-policy linker-language-provenance findings are also diagnostic-shape, not verdict-shape. The whole document is therefore a descriptive snapshot that informed decisions (the three actions cited in the front-matter), rather than per-value rulings under existing law. `docs/surveys/` is the correct home.

## Context

The audit covered all `Symbol(...)` constructor calls in `packages/*/src/` (analyzers, linkers, core). 450 total emit sites across 22 linker files plus the analyzer package suite. The motivation: WI-zadot's 2026-05-28 dogfood finding flagged synthetic linker stand-ins (kafka:/redis:/ws:/Mutation./Query.) being tagged with semantic-axis values that didn't quite fit. The audit was the systematic look at whether WI-zadot is a singleton or a pattern.

It is a pattern. The audit found:
- 6 non-catalog `Symbol.language` values across 15 emit sites (Dimension 1)
- 7 `Symbol.id` f-string emit sites in 6 linkers using a non-canonical `<path>::<role>::<line>` shape vs the documented `<lang>:<path>:<span>:<name>:<kind>` 5-colon canonical format (Dimension 2)
- Systematic field-population gaps: Python is the only analyzer that populates most optional `Symbol` fields (cyclomatic_complexity, docstring, signature) at >0%; ~80 other language partitions leave them all empty (Dimension 3)

The follow-up supplement classified the 39 linker `Symbol(...)` emit sites with `language=` kwargs by provenance and surfaced three distinct unwritten policies for choosing the `language=` value on synthetic stand-ins (INHERIT-EMITTER / LITERAL-HOST / LITERAL-SENTINEL).

## Methodology

The audit is **static AST analysis** of `Symbol(...)` constructor calls. Three dimensions:

1. **Catalog conformance** (`Symbol.language`, `Symbol.kind`): check whether literal-string emit values appear in `all_known_languages()` / `all_symbol_kind_names()`. Non-members are flagged.
2. **Format conformance** (`Symbol.id`, `Symbol.stable_id`): classify construction as canonical-factory / f-string / variable / other. F-string templates are captured for shape comparison against the documented 5-segment canonical form.
3. **Per-language parity** (optional fields): partition emit sites by literal `language=` value (or analyzer-package fallback) and report a coverage matrix for optional Symbol fields (`signature`, `cyclomatic_complexity`, `lines_of_code`, `canonical_name`, `modifiers`, `docstring`, `stable_id`, `fingerprint`, `is_exported`).

The supplement adds a fourth dimension specifically for linkers: **language= provenance classification** — for each linker `Symbol(...)` emit site, classify the `language=` argument's AST node as LITERAL / PATTERN_VAR / HELPER_CALL / SYMBOL_ATTR / VAR (with backward-trace for VAR cases).

Scripts: `/tmp/audit_symbol_emit_coherence.py` (main audit), `/tmp/audit_linker_language_provenance.py` (supplement). Both stdlib-only; ~200 lines each.

The audit is **static code-pattern counting**, not dynamic node counting. The same code pattern in a loop emits many runtime nodes; tracker items reporting dynamic counts (e.g., INV-sadiv's "218 nodes," INV-hunup's "176 nodes") will be much larger than the static emit-site counts here. Compare audit findings to tracker items as "audit identifies code locations, tracker items quantify runtime impact."

## Dimension 1 — Catalog conformance findings

The standard `## Verdicts` block + audit_verdicts YAML schema is **not used here.** That format requires one of the three registry-backed axes (`Edge.edge_type`, `Symbol.kind`, `Edge.evidence_type`); the language axis is catalog-derived lightweight per ADR-0024 §4 and doesn't have the heavyweight registry the property test validates against. Per the README's §"Scope" carve-out, this audit uses a descriptive sibling format for the per-value findings:

| Value | Verdict shape | Status | Rationale |
|---|---|---|---|
| `objective-c` | FOLD → `objc` | RESOLVED | Analyzer-side drift. `objc.py` registered as `@register_analyzer("objc")` but emitted `Symbol.language="objective-c"` at 4 sites, forcing translation tables in `fingerprint.py` and `io_boundary.py` to bridge to the `"objc"` canonical form used by Symbol IDs, edge prefixes, file classification, and linker activation. PR #3984 (2026-05-30) collapsed all 4 emits to `"objc"`, deleted both translation tables, retired two regression tests pinning the workaround as canonical, and updated test fixtures. Catalog now sees consistent `"objc"` everywhere. Diagnostic test: `grep -rn 'language="objective-c"' packages/hypergumbo-lang-mainstream/src/` is expected to be empty. |
| `ansible` | CANONICAL (catalog updated) | RESOLVED | Analyzer-side drift: `yaml_ansible.py` registered as `@register_analyzer("yaml_ansible")` but emitted `Symbol.language="ansible"` at 4 sites, leaving `"ansible"` out of `all_known_languages()` while the actual emit value was the semantically correct one. PR #3984 (2026-05-30) added `languages=["ansible"]` kwarg to the registration, so the catalog now recognizes the emitted value. Analyzer pipeline identity (registration name, `PASS_ID`, depends-on references) remains `"yaml_ansible"`. The value `"ansible"` itself is canonical — it always belonged on the language axis; the catalog was incomplete. Diagnostic test: `"ansible" in all_known_languages()` is expected `True`. |
| `protobuf` | FOLD → `proto` | RESOLVED | Linker-side outlier. The proto analyzer at `packages/hypergumbo-lang-common/src/hypergumbo_lang_common/proto.py:371` registers `@register_analyzer("proto")` and emits `Symbol.language="proto"` for `.proto` file declarations. The gRPC linker was emitting `Symbol.language="protobuf"` at `grpc.py:230` (GrpcPattern construction for proto-file scans) and `grpc.py:944` (Route synthetic emit) — non-catalog values for the same conceptual content. PR #3986 (2026-05-31) collapsed both to `"proto"`. Framework-axis usage (`LinkerActivation(frameworks=["grpc", "protobuf"])` at `grpc.py:1162` and parallel sites in `profile.py` / `linker_activation.py` tests) is unaffected; that's a separate vocabulary. Diagnostic test: `grep -rn 'language="protobuf"' packages/hypergumbo-core/src/hypergumbo_core/linkers/grpc.py` is expected to be empty. |
| `unknown` | FOLD → `protocol_origin="annotation"` | UNRESOLVED | Linker-side sentinel for "this synthetic stand-in has no source language" (`annotation_convention.py`, 4 sites). ADR-0031 (Draft, 2026-05-30) prescribes the longer-term reshape: synthetic stand-ins for annotation directives migrate to `language=None` + `protocol_origin="annotation"` + `discovery_language=<host>`. The fold target here is the `protocol_origin` typed field that ADR-0031 introduces, not a language-axis canonical value — `"unknown"` doesn't fold to another language; it folds off the language axis entirely. Status remains UNRESOLVED pending the Phase 1 producer migration in ADR-0031. |
| `wasm` | FOLD → `protocol_origin="wasm"` | UNRESOLVED | Linker-side sentinel for compiled WASM module identifiers (`wasm_bindgen.py:399`, 1 site). The compiled `.wasm` binary doesn't have a source language in the user's codebase — could have been compiled from Rust, C, AssemblyScript, anything. ADR-0031 (Draft) migrates this to `language=None` + `protocol_origin="wasm"` + `discovery_language=None` (the wasm module isn't anchored to a particular host file). Status UNRESOLVED pending Phase 1 producer migration. |
| `openapi` | FOLD → `protocol_origin="openapi"` | UNRESOLVED | Linker-side sentinel for synthetic Symbols derived from OpenAPI YAML/JSON spec files (`openapi.py:327`, 1 site). OpenAPI isn't a real source language in hypergumbo's analyzer set; the spec files are parsed as YAML or JSON by those analyzers, and `openapi.py` is a linker that derives route/operation synthetics from them. ADR-0031 (Draft) migrates to `language=None` + `protocol_origin="openapi"` + `discovery_language=<spec file language>`. Status UNRESOLVED pending Phase 1 producer migration. |

The `Symbol.kind` axis was also checked in Dimension 1 and is **clean** — 0 non-catalog values across 389 literal-kind emit sites. ADR-0027's Phase 1–4 enforcement work is doing its job. No verdicts needed.

## Diagnostic findings

### Dimension 2 — Format conformance (`Symbol.id` and `Symbol.stable_id`)

`Symbol.id` construction:
- Built via canonical factory (`make_symbol_id`, `_make_symbol_id`, `make_file_id`, `_make_file_id`, `_canonical_external_id`): **199** sites (lower bound — the audit can't trace variable-assigned factory results through the static AST, so the actual factory-mediated emit count is much higher).
- Built via f-string with a `{path}::{role}::{line}` shape (not the canonical 5-colon `{lang}:{path}:{span}:{name}:{kind}` form): **7** sites, in 6 linkers — `database_query.py:350`, `subprocess_cli.py:323`, `message_queue.py:416`, `graphql_resolver.py:429`, `http.py:1323`, `graphql.py:207`, plus `event_sourcing.py:664` which uses a canonical 5-segment shape via f-string instead of via the factory. These match the targets named in INV-sadiv (linker call_site nodes use '::' separator + path-prefix ID schema).
- Built via variable / attribute / non-factory call: **244** sites (upper bound on non-canonical — many are factory results passed through a variable assignment that the audit doesn't statically trace back).

`Symbol.stable_id` construction:
- Built via canonical `make_*_stable_id` factory: only **3** direct-call sites (lower bound; variables hide most factory usage from the static walker).
- Built via other (`call`, `var`, `f_string`, `attr`, `literal_str`, `literal_none`): **278** sites.
- Missing (no `stable_id` kwarg passed): **169** sites.

Cross-reference: INV-sadiv (218 dynamic nodes affected per bm.json inspection), INV-dulah (12 nodes escape the documented `lang:path:span:name:kind` format), INV-hunup (stable_id has 5+ formats; 176 non-canonical nodes), INV-kovob (canonical_name dual-mode), INV-fogum (TOML fingerprints without `hgfp1:` prefix). All five are open under the INV-kurup META ("Identifier-bearing fields emit non-canonical formats from several emission paths"). The audit's static counts and these tracker items' dynamic counts agree on the shape of the problem; they differ in scale because static code-pattern counts undercount runtime-emitted nodes.

The Symbol.id format is officially **dual-shape** per commit `8b61a681bd docs+test: clarify Symbol.id dual-shape and enforce WI-davan invariant`. The 5-colon canonical form is dominant; some IDs legitimately use different shapes (Rust `::` namespacing, npm packages with missing segments, route IDs with embedded colons). The dual-shape framing is documented; INV-dulah catalogs the specific exceptions.

### Dimension 3 — Per-language field-population parity

The audit's full per-language matrix has 118 partitions. Headline findings:

- **`cyclomatic_complexity`**: populated only by the `python` partition (3/9 sites, 33%). 0% across the other 117 partitions.
- **`docstring`**: same shape — Python 3/9 (33%), 0% elsewhere.
- **`is_exported`**: Python 3/9 (33%); sparse elsewhere (Scala 1/7, Kotlin 1/3, Haskell 1/1).
- **`lines_of_code`**: Python 3/9 (33%), Go 5/12 (42%), Rust 4/6 (67%), Bash 1/4 (25%). Otherwise 0%.
- **`signature`**: ranges from 0% to 100% across partitions with no clear pattern. Vue 5/6 (83%), Robot 5/5 (100%), Twig 5/5 (100%), Pony 4/4 (100%), but Python only 2/9 (22%) and most C-family languages at 17-33%.

The `canonical_name` and `fingerprint` fields show an **inverse pattern**: config-language analyzers (JSON 10/10, CMake 7/7, CSS 6/6, SQL 6/6, TOML 5/5, XML 4/4, VHDL 4/4, GLSL/WGSL 3/3, Make 3/3) populate them at 100%, while code-language analyzers (Python, Go, Rust, Java, JS/TS, C#, Ruby, PHP) populate them at 0% or near-0%. Investigation showed this isn't drift — it's *different semantic uses of the same field name across producer categories*:

- **`canonical_name`** has at least three distinct uses:
  - Config-analyzer use: local entity name (`project_name`, `var_name`, etc.) — single token, often duplicating `Symbol.name`.
  - Linker-synthetic use: human-readable display string (`"invoke('save_data')"`, `"WASM module: ..."`, `"@hg:publishes channel"`).
  - Code-analyzer use (per `linkers/containment.py:217,275` docs, but unimplemented): fully-qualified dotted name (`"hello.HelloService.BidiHello"`).
- **`fingerprint`** has two distinct algorithms:
  - Producer-side (config analyzers + `wasm_bindgen.py:404`): `hashlib.sha256(source[start:end])[:16]` — raw bytes including comments, 16-char truncation, no scheme prefix.
  - Centralized post-pass (`fingerprint.py:stamp_symbol_fingerprints` via WI-fanun): structural AST/tree-sitter walk filtering comments, `hgfp1:`-prefixed full hash. The post-pass explicitly skips Symbols with non-None fingerprint (`fingerprint.py:60-64`), so the producer-side format wins for config-derived Symbols.

Cross-reference: INV-jahiv (META: analyzer parity — declared Symbol/Edge fields populated unevenly across languages), INV-loguk (non-Python cyclomatic_complexity/LOC), INV-golap (JS signature 0/10). The canonical_name/fingerprint findings overlap with INV-kovob (canonical_name format dual-mode — noted as dual-mode in the tracker, but the audit found three uses).

## Supplement: linker `language=` provenance

A focused follow-up restricted scope to the 22 linker files. Of the 39 emit sites with `language=` kwargs, the value's AST-level provenance classifies as:

| Category | Count |
|---|---:|
| LITERAL (hardcoded string) | 22 |
| PATTERN_VAR (`pattern.language`, `call.language`, etc.) | 7 |
| HELPER_CALL (`_language_for_file(...)`, `_get_language(...)`) | 3 |
| VAR (variable; backward-traced individually) | 7 |
| SYMBOL_ATTR | 0 |

After backward-tracing the 7 VARs, the actual provenance distribution is:
- **LITERAL (unconditional or conditional hardcode):** 26
- **INHERIT-EMITTER (PATTERN_VAR + HELPER_CALL + ID-prefix-parsing VAR):** 13

### Three policies, made visible

The 39 emit sites cluster into three distinct unwritten policies for choosing the `language=` value on synthetic linker stand-ins:

1. **INHERIT-EMITTER** (10 linkers, 13 sites). The discovering/calling file's language. A Kafka topic discovered in `producer.py` gets `language="python"`; the same topic discovered in `consumer.java` gets `language="java"`. Same logical entity, two stand-ins with different language tags.
2. **LITERAL-HOST** (8 linkers, ~13 sites). Hardcoded to the protocol's traditional host language regardless of where found. `ipc.py` always emits `"javascript"`; `phoenix_ipc.py` always `"elixir"`; `subprocess_cli.py` always `"python"`.
3. **LITERAL-SENTINEL** (4 linkers, 8 sites). Synthetic values not in the catalog: `"unknown"`, `"wasm"`, `"protobuf"`, `"openapi"`. The four already-captured Dimension 1 verdicts above.

Three policies, no governing principle, no documentation of which fits when. Each linker author made a local call.

### Two mixed-policy linkers

Three linkers ship multiple policies in one file:
- `grpc.py`: INHERIT-EMITTER at line 734 (`pattern.language`) + LITERAL-SENTINEL at line 944 (`"protobuf"`, now resolved to `"proto"` per the Dimension 1 verdicts above).
- `ipc.py`: HELPER_CALL at lines 649, 752 (`_get_language(...)`) + LITERAL-HOST at line 509 (`"javascript"`).
- `wasm_bindgen.py`: LITERAL `"typescript"` at line 269 + LITERAL-SENTINEL `"wasm"` at line 399.

The per-emit-site policy choice (not per-linker) is the actual unit of variation. 39 sites, 3 policies, no rule.

### Cross-language detection: a hidden load-bearing consumer of INHERIT-EMITTER

A consumer-side check found that four linkers depend on the INHERIT-EMITTER discovery-language encoding load-bearingly:

| Site | What it does |
|---|---|
| `event_sourcing.py:753` | `is_cross_language = pub.language != sub.language` → sets edge `cross_language` meta |
| `database_query.py:434` | Same shape |
| `message_queue.py:497-505` | Same shape, **plus** confidence adjustment `-0.1` when cross-language |
| `graphql_resolver.py:504,530` | Same shape, two comparison sites |

These consumers aren't passively reading `Symbol.language`; they're deliberately using the INHERIT-EMITTER policy's encoding of "discovery context" to derive cross-language edge metadata. The field is overloaded with two semantics — nominally "source language of declaration," operationally "host language where the synthetic was discovered" — and the cross-language detectors read the second.

This is precisely the INV-numat pattern ("vocabulary fields mix axes") manifested concretely on the language field.

### What the supplement adds to Dimension 1's picture

Dimension 1 caught the **sentinel-inventing branch** of the WI-zadot-shape problem (the 4 LITERAL-SENTINEL linkers, captured as Verdicts rows above). The supplement made visible the **emitter-language-inheriting branch** (the 10 INHERIT-EMITTER linkers, 13 emit sites) which is structurally invisible to Dimension 1's catalog-conformance check because those linkers emit catalog-valid values for the wrong semantic. Same shape of question, different policy answer, no static check fires.

The supplement also surfaced the **hardcode-host-language branch** (the 8 LITERAL-HOST linkers, ~13 sites). Catalog-conformant (the hardcoded values are real catalog languages) but wrong in multi-language stacks (Tauri pairs JS with Rust; `ipc.py`'s always-`"javascript"` is wrong for the Rust side of a Tauri IPC).

## Migration impact

Concrete actions taken in response to this audit:

- **PR #3984** (2026-05-30, `refactor: harmonize objc + ansible language tags to catalog-conformant values`): closed the analyzer-side `objective-c` → `objc` drift and the analyzer-side `yaml_ansible` registration vs `ansible` emit gap. 17 files, +163/-63. Cleanup-shaped: removed translation tables in `fingerprint.py` and `io_boundary.py`; collapsed the `frozenset({"objective-c", "objc"})` in `sketch.py`; retired two "despite mismatch" regression tests; updated test fixtures.

- **PR #3986** (2026-05-31, `refactor(grpc): collapse Symbol.language="protobuf" to "proto"`): collapsed the linker-side `protobuf` outlier at `grpc.py:230` and `grpc.py:944`. 3 files, +16/-6. Zero test churn (no test asserted `language="protobuf"` on Symbols). Framework-axis usage unchanged.

- **ADR-0031** (Status: Draft, 2026-05-30, `docs/adr/0031-symbol-language-reshape.md`): prescribes the longer-term reshape covering the remaining UNRESOLVED Dimension 1 verdicts (`unknown`, `wasm`, `openapi`) plus the supplement's three-policy fragmentation. Adds two typed Symbol fields (`discovery_language: Optional[str]`, `protocol_origin: Optional[str]`) and relaxes `Symbol.language: str` to `Optional[str]`. The 4 cross-language-detection consumer sites migrate from `sym.language` to `sym.discovery_language`. Phase 0–4 not yet executed.

Dimensions 2 and 3 are NOT closed by these PRs / this ADR:

- **Dimension 2** (format conformance) tracks under the **INV-kurup META cluster** — five active children (INV-sadiv, INV-dulah, INV-hunup, INV-kovob, INV-fogum) plus the parent META — and the **INV-tazaj META cluster** (stable_id uniqueness, with INV-bazij at P0). An active fix campaign has shipped ~10 ID-format fixes in the last 3 months (per `git log --grep="stable_id\|symbol_id"`). No single PR/ADR closes the cluster; the existing tracker work is the home for this dimension's resolution.

- **Dimension 3** (per-language field-population parity) is documented in `~/hypergumbo_lab_notebook/symbol-field-population-plan-05302026.md` — a 4-phase, 30-40 PR plan to bring Go + Rust + 8 additional languages (TypeScript, JavaScript, Java, C#, Ruby, PHP, Kotlin, Swift) up to ≥90% population for the seven in-scope optional fields. Closes INV-jahiv (META: analyzer parity), INV-loguk (LOC / cyclomatic), INV-golap (JS signature) for these 10 languages when fully executed. Not yet started.

- **The canonical_name / fingerprint divergence** (within Dimension 3) is a separate desire-paths cluster (3 uses of canonical_name, 2 algorithms for fingerprint). Captured in conversation but not yet in a lab-notebook doc or ADR. ADR-0031 explicitly excludes it from scope. A separate audit-findings document or ADR would be the right home; not yet filed.

## Related

- **Originating dogfood finding:** WI-zadot-hasuh-mazos-tuhib-konor-zosog-gosuv-javup (ITEM-8b4eb5, dogfood pass 10 2026-05-28). The Symbol.language= half of WI-zadot's complaint maps to the four LITERAL-SENTINEL Dimension 1 verdicts above (three RESOLVED, one collapsed; remaining three deferred to ADR-0031). The kind=function half was closed separately by ADR-0027 Phase 3.
- **Adjacent tracker clusters:**
  - INV-numat META — vocabulary fields mix axes. Parent of the cross-language-detection overloaded-semantic finding.
  - INV-kurup META — identifier-bearing fields emit non-canonical formats. Parent of the Dimension 2 sub-findings.
  - INV-jahiv META — analyzer parity. Parent of the Dimension 3 per-language gaps.
  - INV-sugat super-META — no spec-vs-data validator stage. Encompasses all three METAs above as a shape of architectural absence.
  - INV-tofun — separately closed by PR #3984 (analyzer-side hardcode for .ts files).
- **Decision documents:**
  - ADR-0024 (Axis Declaration Template) — the §"Fold-residue discipline" promotion-gating rule used in ADR-0031's design.
  - ADR-0027 (Symbol.kind axis) — sibling axis whose Phase 3 fold pattern ADR-0031 mirrors.
  - ADR-0028 (Edge.evidence_type axis) — another sibling using the canonical-value + typed-field pattern.
  - ADR-0031 (Symbol.language reshape, Draft) — addresses the UNRESOLVED Dimension 1 rows and the supplement's three-policy fragmentation.
- **Lab-notebook companions** (descriptive; not under `docs/`):
  - `symbol-emit-coherence-audit-playbook.md` — the methodology this audit instantiates.
  - `audit-supplement-linker-language-provenance-05302026.md` — the original supplement, now integrated as §"Supplement" above.
  - `three-policies-pros-cons-05302026.md` — per-policy trade-off analysis informing ADR-0031.
  - `symbol-field-population-plan-05302026.md` — the Dimension 3 migration plan.
