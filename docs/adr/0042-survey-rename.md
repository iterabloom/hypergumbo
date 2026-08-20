<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0042: Survey Rename — One Artifact Name, One Concept, One Verb

- Status: **Accepted**
- Date: 2026-06-10
- Supersedes: —
- Superseded by: —
- Related: ADR-0030 (PROV Vocabulary Mapping for Behavior Map Provenance — the concept it maps provenance for is renamed here; the mapping itself is untouched), ADR-0033 (Spec-vs-Data Validator Stage — the shared loader this ADR's constant feeds carries the schema_version warn-first gate), ADR-0035–0041 (sibling rulings from the same 2026-06-10 design-interview batch: 0035 stable-id v6 identity contract, 0036 node.id grammar v2, 0037 edge resolution semantics, 0038 access_mode contract, 0039 confidence separation, 0040 evidence-field descope, 0041 supply-chain tier purity — this ADR is the only one in the batch that changes no schema bytes; filename and vocabulary only).

Decided by the project owner via design interview on 2026-06-10, reviewing evidence from the verified 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, survey-rename-campaign family: 10 items, confirmed / analysis_sound). This ADR records the ruling; it is not a proposal.

## Context

### Four names for one artifact

hypergumbo's primary output — the JSON envelope containing symbols, edges, entrypoints, and provenance — circulates under four names, none of which was ever the subject of a naming decision:

1. **`hypergumbo.results.json`** — the CLI default. `_discover_input_file` (`cli.py::_discover_input_file`) searches the results cache and then the repo root for this basename; `cmd_run` writes it to `~/.cache/hypergumbo/<fingerprint>/results/<state_hash>/hypergumbo.results.json` (`cli.py`); roughly nine `--input` help strings repeat it.
2. **`hg.json`** — docs and bakeoff shorthand, which `find_behavior_map` (`survey_io.py::find_behavior_map`) hardcodes as a *different* resolver default. Two discovery resolvers, two default basenames, no shared constant.
3. **`bm.json`** — the test/dogfood substrate convention. 52 open tracker items reference it (grep-measured 2026-06-10; the count drifted from 17 earlier in the campaign window — counts in this family rot continuously).
4. **`behavior_map.json`** — legacy from the original Dec-2025 MVP spec. It survives as the name of the view in the spec's v0.x stability commitment ("No breaking changes to `behavior_map.json` view within v0.x series", docs/hypergumbo-spec.md §Appendix C).

Per INV-firuh's root-cause record: the names accumulated organically — `behavior_map.json` from the MVP spec, `hg.json` as docs-shorthand drift, `bm.json` as substrate convention, `hypergumbo.results.json` as pre-release CLI polish. No single naming decision was ever authored. The rename *is* the consolidation.

The blast surface beyond core: ~65 references across `scripts/` (bakeoff-broad, bakeoff-deep, the reflect pipelines, analyze-artifacts, diagnostics — re-grepped at 66 on 2026-06-10), plus README, spec, USE-CASES.md, playbooks, and the 52 tracker items.

### Why "survey"

The design interview applied a four-property rubric to candidate names — does the name connote **methodology** (a deliberate procedure was followed), **evidence** (the output is grounded in observations), **comprehensiveness** (the whole territory was covered), and **dignity** (the name reads professionally in docs and at the shell)? Each existing name fails at least one: `results` is methodology-free and generic; `hg`/`bm` are opaque initialisms with no dignity; `behavior_map` is closest but is the longest candidate and cannot serve as a verb. **Survey** carries all four — a survey is a methodical, evidence-based, comprehensive examination — and works as a verb, enabling noun-verb-artifact consistency: `hypergumbo survey` produces `survey.json`.

## Decision

### 1. Canonical artifact name: `survey.json`

One constant, `CANONICAL_SURVEY_FILENAME = "survey.json"`, becomes the single source of truth for the artifact basename. The four circulating names become a **declared legacy alias list** (`hypergumbo.results.json`, `hg.json`, `bm.json`, `behavior_map.json`) resolved by the shared substrate loader (§4). Resolution semantics:

- The canonical name always wins; aliases are consulted, in declared order, only when the canonical file is absent (plain-before-`.gz` tie-break preserved from `find_behavior_map`).
- Every alias hit emits a deprecation warning to stderr naming the alias found and the canonical name.
- Deprecation window: **one minor version.** Aliases warn for the duration of the minor release in which the rename ships, then the alias list is removed in the following minor release.

### 2. Concept rename: "behavior map" → "survey"

The concept name for the JSON envelope changes from "behavior map" to "survey" across README, `docs/hypergumbo-spec.md`, USE-CASES.md, and the rest of the documentation tree (the campaign's S4 sweep). Code-level names (`behavior_map_io`, `load_behavior_map`, `find_behavior_map`, `run_behavior_map`, and relatives) migrate to `survey`-based names, with one hard sequencing rule: **the import shim lands first.** The repo runs on an editable install, so an in-flight bakeoff would import the renamed modules mid-run; old module and function names must remain importable (re-exporting from the new homes, warning on import) before any rename PR merges. The shim lives for the same one-minor-version window as the filename aliases.

The `.agent/` playbook portion of the docs sweep splits into a separate human-approved PR (governance files; cannot self-merge).

**The schema does not change.** This ruling is filename and vocabulary only: no field is added, removed, or retyped; `SCHEMA_VERSION` (0.14.1 at time of writing, `schema.py::SCHEMA_VERSION`) is not bumped by this ADR. The spec's v0.x stability commitment is unaffected — the clause in spec §Appendix C is rephrased to name the survey view, and the commitment itself carries over verbatim.

### 3. Verb rename: `hypergumbo survey` (the WI-rital decision)

`hypergumbo survey` becomes the primary verb producing `survey.json`. `hypergumbo run` (`cli.py`) remains a **deprecated alias for one minor version** — fully functional, warning on use — then is removed. This was tracked as a separate user-gated decision (WI-rital); the project owner folded it into this ADR per the interview. The deciding argument is noun-verb-artifact consistency: *survey* produces *survey.json*, and the docs read naturally ("survey the repo") instead of permanently reading "run produces a survey."

### 4. Migration mechanics

- **One constant + declared alias list** (§1), defined once in core and imported everywhere — no resolver, script, or help string hardcodes a basename again.
- **The two divergent discovery resolvers merge.** `_discover_input_file` (`cli.py::_discover_input_file`, cache-then-repo-root search for `hypergumbo.results.json`) and `find_behavior_map` (`survey_io.py::find_behavior_map`, directory search for `hg.json`) collapse into the single shared substrate loader being built in the same campaign — the Wave-4 joint deliverable of survey:F2 (constant + aliases + merged resolver, this ADR) and cli-input:F3 (typed SubstrateError, dict-root check, view discriminator, schema_version warn-first gate; under the INV-fabov umbrella). One loader, consumed by every CLI subcommand and bakeoff/analysis script.
- **The ~65-reference `scripts/` surface is in scope.** It was owned by no campaign member when the analysis ran; it is assigned to the loader-consumption sweep.
- **The 52 affected open tracker items are re-grepped at sweep time.** Counts in this family drift (17 → 52 within the campaign window); per the family's own discipline, every sweep re-greps before editing rather than trusting recorded counts.

## Migration plan

The campaign chain (S1→S8, from the strategy doc's Wave plan), in order:

1. **S1 — this ADR** (closes WI-virib's authoring scope; records the WI-rital verb decision).
2. **S2/S3 — constant + merged resolver + import shim.** Shim first (§2 sequencing rule); then `CANONICAL_SURVEY_FILENAME`, the alias list, and the merged loader land with cli-input:F3.
3. **S2 (CLI) — default output and verb.** `hypergumbo survey` registered as primary; `run` aliased with warning; default output basename and the ~9 `--input` help strings switch to `survey.json` (WI-vatuf).
4. **S4 — concept-rename docs sweep.** README/spec/USE-CASES.md/docs; spec ~1857 stability clause rephrased; `.agent/` portion as a separate human-approved PR.
5. **S5–S7 — test fixtures and scripts.** `bm.json` test/dogfood convention and the `scripts/` surface move to the constant.
6. **S8 — tracker sweep.** Re-grep, then update active tracker item descriptions.
7. **Window close (one minor version later):** alias list, import shim, and `run` alias removed.

INV-firuh flips violated → satisfied when S1–S8 land; the verb removal at window close does not gate the flip.

## Consequences

### Positive

- **One name.** The four-way ambiguity — including two resolvers with different defaults silently looking for different files — is eliminated structurally, not by convention.
- **Noun-verb-artifact coherence.** `hypergumbo survey` → `survey.json`; docs and shell sessions read naturally.
- **The naming upgrade pays for the consolidation.** The migration cost is owed regardless of which name wins; choosing `survey` buys methodology/evidence/comprehensiveness/dignity connotations and the verb for the same price.
- **The constant feeds the Wave-4 loader.** The shared substrate loader gets its canonical-name input from this ADR instead of inventing a fifth name.

### Negative

- **A breaking change after the window.** Scripts invoking `hypergumbo run` or globbing the old basenames break one minor version after the rename ships. Accepted deliberately: the output contract is explicitly pre-1.0 (schema v0.x series), which permits this break given a warning window.
- **Wide, shallow churn.** ~65 `scripts/` references, the docs tree, test fixtures, and 52 tracker items. Mechanical, but real review surface.
- **Editable-install hazard during migration.** Mitigated by the shim-first sequencing rule; an ordering mistake here breaks in-flight bakeoffs mid-run.
- **Stale counts are guaranteed.** The 52-item and 65-ref figures recorded here will be wrong at sweep time; the re-grep discipline is mandatory, not advisory.

### Neutral / acknowledged

- **Schema bytes unchanged.** Consumers parsing the JSON see no difference; only where the file lives by default, what it is called, and what the docs call the concept.
- **Historical records keep their vocabulary.** Dated decision documents (e.g., ADR-0030's title) describe the concept as named at the time; the S4 sweep targets present-tense documentation, per INV-firuh's scope of CLI defaults, internal code, test fixtures, docs, and *active* tracker item descriptions.

## Alternatives considered

1. **`behavior_map.json` as canonical.** The seniority candidate (original MVP spec name). Rejected: longest of the four, not verb-capable (`hypergumbo behavior-map` does not read), so it pays the full consolidation cost without the naming upgrade.
2. **Keep `hypergumbo.results.json`.** Zero-migration for the CLI default. Rejected: clunkiest of the four in docs and at the shell, generic ("results" of what, by what method?), and forecloses verb consistency permanently.
3. **Both verbs permanently (`run` and `survey` as co-equal aliases).** Rejected: a permanent double alias surface — two verbs in help output, docs, and muscle memory forever — for a tool whose pre-1.0 output contract explicitly allowed this break with a window.
4. **Verb stays `run`; only the filename changes.** Rejected: the docs then permanently read "run produces a survey," re-creating the noun-verb mismatch this ADR exists to end.

## Tracker items

- `WI-virib-tukon-gapal-sagal-dozon-sajun-juzag-hopub` — "survey-rename: author ADR for the rename decision." This ADR is its deliverable; the authoring scope closes on merge.
- `WI-rital-lovor-rarak-dajuh-kiram-fujag-hisob-bizik` — "survey-rename: decide whether to also rename verb `hypergumbo run` → `hypergumbo survey`." Decision recorded in §3: yes, with a one-minor-version deprecated alias.
- `INV-firuh-kumud-kujan-fujid-fovuf-fagoz-totud-podog` — umbrella invariant ("canonical artifact name is survey.json across CLI, code, tests, docs, tracker"). Violated at time of writing; flips to satisfied when S1–S8 land.
- `WI-vatuf-sukur-nikih-sazog-dazok-kasos-ninut-rijor` — "survey-rename: change CLI default output filename to survey.json." Gated on this ADR (WI-virib `isbefore` it); executes §3's CLI portion.
- `INV-fabov-kapit-mupit-tigoz-lurol-mulus-jivik-kajun` — cli-input META (unvalidated CLI/config inputs). Its F3 fix is the joint-build partner for the shared substrate loader that consumes `CANONICAL_SURVEY_FILENAME`.

## References

- Strategy doc: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` — survey-rename-campaign family verdict, the two-resolver finding, the 65-ref / 52-item measurements, the S1→S8 chain, and shared seam (c) (the substrate loader).
- Raw analysis: `~/hypergumbo_lab_notebook/correctness_strategy_06102026_full_workflow_result.json`.
- Code: `packages/hypergumbo-core/src/hypergumbo_core/cli.py::_discover_input_file` (`_discover_input_file`, default `hypergumbo.results.json`); `packages/hypergumbo-core/src/hypergumbo_core/survey_io.py::find_behavior_map` (default `hg.json`; re-exported by the `behavior_map_io` shim); `cli.py` (`run` subparser); `schema.py::SCHEMA_VERSION` (unchanged by this ADR).
- Spec: `docs/hypergumbo-spec.md` §Appendix C — the v0.x stability commitment whose wording (not substance) updates in the S4 sweep.
