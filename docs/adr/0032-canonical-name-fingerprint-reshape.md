<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0032: canonical_name and fingerprint Reshape — display_label and qualified_name Typed Fields; Format 1 Fingerprint Demolition

- Status: **Accepted**
- Date: 2026-05-31
- Supersedes: —
- Superseded by: —
- Related: ADR-0014 (Generalized Symbol Identity — `fingerprint` / `stable_id` / `shape_id` governance; this ADR does NOT change identity-field shapes), ADR-0024 (Axis Declaration Template — promotion math for typed-sibling fields), ADR-0027 (Symbol.kind axis — sibling axis reshape this ADR mirrors), ADR-0031 (Symbol.language reshape — same shape applied to a different field; this ADR ships in the combined 0.12.0 release), ADR-0033 (Spec-vs-Data Validator Stage — defers runtime axis-enforcement on the new fields to that ADR's Phase 3 PR1); tracker items INV-kovob (canonical_name format dual-mode — actually three-mode per this analysis), INV-fogum (TOML fingerprints without `hgfp1:` prefix — auto-closed when producer-side Format 1 is removed), INV-kurup (META: identifier-bearing fields emit non-canonical formats; this ADR resolves the canonical_name and fingerprint expressions); lab-notebook source: `~/hypergumbo_lab_notebook/canonical-name-fingerprint-desire-paths-05312026.md`.

## Context

The 2026-05-30 symbol-emit-coherence survey + 2026-05-31 desire-paths analysis found that two `Symbol` fields are populated with multiple structurally distinct kinds of values across the codebase:

### `canonical_name` — three uses pretending to be one field

| Use | Producers | Value shape |
|---|---|---|
| 1. Config-analyzer "duplicate of `name`" | cmake / css / json_config / toml_config / sql / xml_config / dockerfile / make / manifest_targets / powershell (10 analyzers) | `canonical_name=name` (same string already passed to `name=`) |
| 2. Linker-synthetic "display label" | tauri_ipc / crypto_flow / wasm_bindgen / annotation_convention / message_dispatch / yjs_crdt (~15 emit sites across 6 linkers) | Expression-form description: `"invoke('save_data')"`, `"@hg:publishes channel"`, `"crypto.user-events"`, `"dispatch.send(user-events)"` |
| 3. Code-analyzer "fully-qualified name" (documented intent, not implemented) | Aspirational only — `linkers/containment.py:217,275` test docstrings show the intended semantic; **zero** code analyzers populate `canonical_name=` at construction time today | `"hello.HelloService.BidiHello"` (dotted scope chain) |

Three distinct semantics. One field name. No documentation of which fits when.

### `fingerprint` — two coexisting algorithms

- **Format 1 — producer-side raw-bytes hash.** Used by config analyzers (cmake / css / json_config / toml_config / sql / xml_config) and one linker (wasm_bindgen). Algorithm: `hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16]`. Output: 16-char hex, no scheme prefix. Hashes raw bytes including comments and whitespace.
- **Format 2 — centralized structural hash via WI-fanun.** Computed by `stamp_symbol_fingerprints` in `packages/hypergumbo-core/src/hypergumbo_core/fingerprint.py`. Walks the Symbol's source span via tree-sitter, builds a structural hash (node types + identifier text + literal values), filters comment nodes, tags with `hgfp1:` prefix. Output: 70-char `hgfp1:<64-char-sha256>`. The post-pass skips Symbols that already carry a non-None fingerprint, so Format 1 "wins" by precedence for producers that emit it; Format 2 fills in everywhere else.

INV-fogum tracks the format gap specifically: TOML dependency nodes carry Format 1 (no `hgfp1:` prefix), 99 nodes at runtime.

### ADR-0024 promotion-gating math

ADR-0024 §"Fold-residue discipline" prescribes: *when a meta key would recur with ≥3 distinct values OR ≥2 producer modules, promote it to a sibling typed field on the parent dataclass rather than use a meta key.*

For the linker-synthetic "display label" semantic:
- **Distinct values:** ≥10 (each linker emits its own expression-form vocabulary).
- **Producer modules:** ≥6 linker files.

Both thresholds passed by wide margin → typed field.

For the code-analyzer "fully-qualified name" semantic:
- **Distinct values:** would be ≥10 once populated (one per scoped code declaration, of which there are thousands).
- **Producer modules:** ~10 code analyzers (per the parallel symbol-field-population plan).

Both thresholds passed. Typed field.

For the fingerprint format question:
- Format 1 is strictly inferior to Format 2 for the use case the field exists for (change detection). A whitespace-only or comment-only change to a TOML dependency declaration produces a different raw-bytes Format-1 hash but no semantic change. The centralized walk correctly ignores those. Format 1 is not a "policy" worth promoting; it's a path to demolish.

## Decision

### Three coordinated `Symbol` changes

**Add `Symbol.display_label: Optional[str]` typed field.**

The field's documented semantic: *human-readable expression-form representation for UI display.* Populated by linkers fabricating synthetic stand-ins; the value is the syntactic context in which the entity is invoked or registered. Real-source-declaration Symbols leave it `None`.

Axis: `free-text — UI display only`. The field is not branched on by consumers; it's a display string. The justification (UI display, no consumer branching) is the ADR-0024 §"Open question Q1" answer for why a registry-backed axis is not needed.

**Add `Symbol.qualified_name: Optional[str]` typed field.**

The field's documented semantic: *the fully-qualified dotted/scoped identifier for cross-file/cross-module reference.* Populated by code analyzers (Phase 4 of the campaign per the parallel symbol-field-population plan). Linker-synthetic and config-analyzer Symbols leave it `None`.

Axis: lightweight new `qualified-name` axis declared via ADR-0024 §4 carveout. The catalog is a per-language separator policy enumeration (see "Per-language separator policy" below) rather than an enumerable value set.

**Remove `canonical_name=` from the 10 config analyzers; migrate ~15 linker sites to `display_label=`; deprecate `Symbol.canonical_name` for removal one major version later.**

After Phase 1 + 2 producer migration, no producer populates `canonical_name` for either Use 1 (config redundancy) or Use 2 (linker display label). Use 3 (code-analyzer FQN) was never implemented; its place is taken by `qualified_name`. The field has no remaining users and is deprecated.

### Fingerprint Format 1 demolition

Drop producer-side `fingerprint=hashlib.sha256(...)[:16]` from analyzers that have tree-sitter support and that the central post-pass can fingerprint: **cmake / css / json_config / toml_config / sql / xml_config / wasm_bindgen**. The central post-pass `stamp_symbol_fingerprints` (`fingerprint.py`) then populates Format 2 (`hgfp1:` prefixed) for these Symbols.

For producers in languages without tree-sitter grammar packs (where the central walker returns `None` — bash, ansible, hcl, ini, gitignore, dockerfile-via-text, regex-only analyzers): leave `fingerprint=None`. This is consistent absence rather than a Format-1 fallback. Consumers reading `Symbol.fingerprint` already handle `None` (the field is `Optional[str]`).

This closes **INV-fogum** automatically. After Phase 2 PR2 lands, the TOML 99 dependency nodes carry the `hgfp1:` prefix; the format gap is gone.

### Per-language separator policy (qualified_name axis)

The `qualified-name` axis is lightweight per ADR-0024 §4. The "catalog" is a per-language separator declaration, enumerated in `qualified_name_axis.py`:

| Language | Separator | Example |
|---|---|---|
| Python | `.` | `hypergumbo_core.cli.run_behavior_map` |
| Go | `.` | `main.HelloService.BidiHello` |
| Rust | `::` | `hypergumbo_core::cli::run_behavior_map` |
| Java | `.` | `com.example.HelloService.BidiHello` |
| C# | `.` | `Example.HelloService.BidiHello` |
| TypeScript | `.` | `module.HelloService.bidiHello` |
| JavaScript | `.` | `module.HelloService.bidiHello` |
| Kotlin | `.` | `com.example.HelloService.bidiHello` |
| Swift | `.` | `Example.HelloService.bidiHello` |
| Ruby | `::` | `Example::HelloService::BidiHello` |
| PHP | `\` | `Example\HelloService::bidiHello` |
| C++ | `::` | `Example::HelloService::BidiHello` |
| Elixir | `.` | `Example.HelloService.bidi_hello` |

When ADR-0033's Phase 3 PR1 (axis-conformance validator) lands, the validator checks `Symbol.qualified_name` matches the per-language separator policy. Mismatch is an axis-conformance violation.

### Consumer migration

Anywhere consumers read `Symbol.canonical_name` (audit found zero in production code; `cmd_explain` and any future UI code are candidates), update per intent:

- For display purposes: read `Symbol.display_label or Symbol.name`.
- For cross-file/cross-module reference: read `Symbol.qualified_name or Symbol.name`.

`cmd_explain` formatters update to display `display_label` when present, falling back to `name`.

### Runtime enforcement (deferred to ADR-0033)

This ADR introduces the typed fields and the axis declarations. The runtime check that emitted values conform to the axes is **deferred to ADR-0033's Phase 3 PR1 (axis-conformance validator class)**. ADR-0033 enumerates this deferral explicitly.

## Migration plan

Five phases within the broader campaign (per `~/.claude/plans/happy-swimming-sketch.md`, Phase 2):

### Phase 2 PR1 — Schema additions + qualified_name axis (dormant)

- Add `Symbol.display_label: Optional[str]` and `Symbol.qualified_name: Optional[str]` to the dataclass with `# axis:` annotations.
- Land `packages/hypergumbo-core/src/hypergumbo_core/qualified_name_axis.py` with the per-language separator catalog.
- Property tests at `packages/hypergumbo-core/tests/test_qualified_name_axis.py`.
- No producer changes yet — the new fields are dormant.

### Phase 2 PR2 — Producer migration

Three sub-classes combined in one PR (the per-file diffs are small; batching them keeps the consumer-coordination window short):

- **(a) Linker `display_label` migration.** ~15 linker-synthetic emit sites move from `canonical_name=<expression>` to `display_label=<expression>` (tauri_ipc / crypto_flow / wasm_bindgen / annotation_convention / message_dispatch / yjs_crdt and any others surfaced by the survey).
- **(b) Config-analyzer `canonical_name` drop.** The 10 config analyzers (cmake / css / json_config / toml_config / sql / xml_config / dockerfile / make / manifest_targets / powershell) drop `canonical_name=` entirely — the value was a redundant duplicate of `name=`.
- **(c) Fingerprint Format 1 demolition.** The 7 producers (cmake / css / json_config / toml_config / sql / xml_config / wasm_bindgen) drop producer-side `fingerprint=hashlib.sha256(source[start_byte:end_byte]).hexdigest()[:16]`. The central post-pass populates Format 2 for these Symbols on the next run.

### Phase 2 PR3 — Combined consumer migration + SCHEMA_VERSION 0.12.0

Combined release PR for both ADR-0031 and ADR-0032 reshapes:

- Update `cmd_explain` and any other readers of `canonical_name` to read `display_label` / `qualified_name` per intent.
- Bump `SCHEMA_VERSION` 0.11.0 → 0.12.0 covering both reshapes.
- Single `docs/MIGRATION-5.X-CONCEPT-AXES.md` entry covering both ADR-0031's language reshape AND ADR-0032's canonical_name / fingerprint reshape.
- Mark `Symbol.canonical_name` deprecated (removal one major version later per Phase 6 PR4).

### Phase 4 PR4 — qualified_name population

Per the parallel symbol-field-population plan, code analyzers (Go / Rust / TS / JS / Java / C# / Ruby / PHP / Kotlin / Swift) gain enclosing-scope tracking and emit `qualified_name=` on every Symbol they create. Lands as a single PR per the campaign plan.

### Phase 6 PR4 — Remove `Symbol.canonical_name`

One major version after Phase 2 PR3's deprecation. The field has no remaining users. Dataclass field removed; tests updated; migration guide records the removal.

## Stable_id impact

`canonical_name` and `fingerprint` are NOT inputs to the 10 `stable_id` factories at `analyze/base.py:513-640` (those use `language`, `path`, `name`, `kind`). **Stable_ids do not change across this ADR's migration.** This is in deliberate contrast to ADR-0031's combined 0.12.0 release, where `Symbol.language=None` for ~20-30 Class B synthetic stand-ins does change those Symbols' stable_ids (per ADR-0031 Phase 3).

Fingerprint *values* change for Format-1-producing Symbols when Phase 2 PR2 lands, because Format 1 (raw-bytes hash) and Format 2 (`hgfp1:` structural hash) produce different strings. But `Symbol.fingerprint` itself isn't a stable_id input, so cross-version stable_id pinning isn't affected — only consumers reading `Symbol.fingerprint` directly see the value change. Documented in the combined `MIGRATION-5.X-CONCEPT-AXES.md` entry.

## Consequences

### Positive

- **Closes INV-kovob.** The three-mode `canonical_name` divergence resolves into two typed fields each carrying one explicit semantic, plus a deprecated `canonical_name` slated for removal.
- **Closes INV-fogum.** Producer-side Format 1 is demolished; the centralized post-pass populates `hgfp1:` form for all previously-Format-1 Symbols.
- **Addresses INV-kurup's canonical_name and fingerprint expressions.** Two of the META's instance gaps are resolved by this ADR; the remaining identifier-format expressions are addressed by ADR-0034 (ID-construction discipline).
- **Aligns with ADR-0031's shape.** The campaign's combined 0.12.0 release ships two structurally-similar reshapes (language axis + canonical_name/fingerprint axes) under one migration entry, reducing consumer migration overhead.
- **`display_label` and `qualified_name` are independently axable.** Future consumers branching on `display_label` would be a misuse (it's UI-only); future consumers branching on `qualified_name` (e.g., for cross-language reference resolution) have a documented, statically-checked surface.

### Negative

- **Two new typed fields on `Symbol`.** The dataclass goes from 28 (post-ADR-0031) to 30 fields. Each new field is an axis for future drift; the axis-declaration discipline applies.
- **One new lightweight axis module.** `qualified_name_axis.py` joins `protocol_origins.py`, `symbol_kinds.py`, `evidence_types.py`, `axis_meta_keys.py` as the fifth ADR-0024 registry module.
- **Fingerprint value changes break checksum-based pinning** for the 99 TOML dependency nodes + the other ~6 config-analyzer node sets that were carrying Format 1. Consumers using `Symbol.fingerprint` for change detection see those values shift to `hgfp1:` form. Mitigation: the `hgfp1:` form is strictly better (structural, ignores whitespace/comments) — consumers should already prefer it.
- **The 10 config analyzers' tests need updating.** Tests that asserted `Symbol.canonical_name == name` now assert `canonical_name is None` (post-migration); tests that asserted Format-1 fingerprint shapes update to Format-2.

### Neutral / acknowledged

- **`Symbol.language` reshape is NOT addressed here.** ADR-0031 owns that. The two ADRs ship in the same SCHEMA_VERSION window but are distinct decisions.
- **The runtime axis-enforcement gap stays open until ADR-0033 Phase 3 PR1 lands.** Same situation as ADR-0031 — both decisions defer their runtime check to ADR-0033. This is intentional and explicit in both ADRs.

## Alternatives considered

1. **Keep `canonical_name` and disambiguate by docstring.** Rejected: the three uses have no shared semantic that a docstring could honestly describe. The field is genuinely overloaded.
2. **One canonical typed field + meta keys for the linker-display and qualified-name uses.** Rejected per ADR-0024 §"Fold-residue discipline" — both promotion thresholds (distinct-value and producer-module counts) pass on day one. Meta-key intermediate is doctrinally inferior.
3. **Two separate ADRs (one per field).** Rejected: the work is mechanically similar (same `# axis:` annotation pattern, same ADR-0024 promotion math, same per-analyzer per-file diff size), the migration cost is similar, and the consumer audience is identical. One ADR with two field decisions parallels ADR-0031's bundling of `discovery_language` + `protocol_origin`.
4. **Keep producer-side Format 1 fingerprint as a fallback for tree-sitter-unsupported languages.** Rejected for tree-sitter-supported producers (the demolition is clean). For tree-sitter-unsupported producers, `fingerprint=None` is the chosen behavior over "keep raw-bytes fallback with documented format inconsistency" — consistent absence is preferable to format multiplicity.

## References

- ADR-0014 (Generalized Symbol Identity): the stable_id factories that are NOT affected by this ADR's changes. Identity fields stay stable across the migration.
- ADR-0024 (Axis Declaration Template): the four-part template this ADR instantiates for both `display_label` (free-text, no catalog) and `qualified_name` (lightweight axis, per-language separator catalog).
- ADR-0027 (Symbol.kind = source-language syntactic construct): sibling-axis ADR on Symbol. ADR-0027 + ADR-0031 + ADR-0032 together resolve three Symbol axis decisions in coordinated fashion.
- ADR-0031 (Symbol.language reshape): the parallel ADR that ships in the same combined 0.12.0 release. Same shape, different field.
- ADR-0033 (Spec-vs-Data Validator Stage): the runtime drift gate this ADR defers to.
- Tracker INV-kovob (`canonical_name format dual-mode`): closes when this ADR's Phase 2 PR2 + PR3 land. The "dual-mode" framing was incomplete; this analysis revealed three modes.
- Tracker INV-fogum (`TOML fingerprints without hgfp1: prefix`): auto-closes when Phase 2 PR2's Format 1 demolition lands.
- Tracker INV-kurup (META: identifier-bearing fields emit non-canonical formats): this ADR resolves the canonical_name and fingerprint expressions; other identifier fields (stable_id under INV-hunup, node.id under INV-sadiv / INV-dulah) remain under separate work (Phase 5 + Phase 6).
- Lab-notebook source: `~/hypergumbo_lab_notebook/canonical-name-fingerprint-desire-paths-05312026.md`.
- Lab-notebook campaign plan: `~/.claude/plans/happy-swimming-sketch.md` (approved 2026-05-31).
