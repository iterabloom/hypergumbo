<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0036: node.id Grammar v2 — Anchored Parsing, Colon Policy, Kind-Slot Purity

- Status: **Accepted**
- Date: 2026-06-10
- Supersedes: — (tightens the grammar whose factory discipline ADR-0034 established; ADR-0034 remains in force. Corrects ADR-0034's reviewer-checklist claim that a kind-slot round-trip check already existed — the shipped `id_format` gate is shape-only; Ruling 2's round-trip validator here is the actual instrument)
- Superseded by: —
- Related: ADR-0034 (ID-Construction Discipline — the factories this grammar binds), ADR-0033 (Spec-vs-Data Validator Stage — the enforcement substrate), ADR-0035 (stable-id v6 identity contract — the hash-identity sibling; node.id is the *location* identity, stable_id the *semantic* identity), ADR-0037 (edge resolution semantics — owns the `:unresolved` dst-suffix convention on `Edge.dst` that coordinates with Ruling 2's migration), ADR-0027 (Symbol.kind registry — the vocabulary Ruling 2 binds the kind slot to), ADR-0031 (Symbol.language reshape — `discovery_language` for synthetic stand-ins, relevant to the lang slot of synthetic IDs), ADR-0024 (axis-declaration template — governs the `meta.reference_syntax` key registration). Tracker items: see §"Tracker items".

> **Decision provenance.** The rulings below were made by the project owner in a design
> interview held 2026-06-10, after reviewing evidence from the verified 446-item tracker
> root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`,
> family **id-format-factory-bypass**, strategy item id-format:F1). This ADR records
> **decided rulings**, not proposals.

## Context

### The grammar was documented but never specified

`Symbol.id` (node.id in the behavior-map output) is documented as the 5-slot form

```
{lang}:{path}:{start}-{end}:{name}:{kind}
```

at `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py:288-306`. But
`make_symbol_id` is a bare f-string with no validation — any slot value containing `:`
silently changes the colon count, and nothing defines what a parser should do about it.
The format is a *convention*, not a *grammar*: no colon policy per slot, no declared
sentinel vocabulary, no contract tying the kind slot to `node.kind`. ADR-0034 fixed
*who* constructs IDs (canonical factories only); it did not fix *what the factories are
allowed to produce*. The root-cause analysis found the dominant failure cohorts
(~2,831 round-trip failures) flow **through** the factories with wrong values in slots —
the defect is one layer deeper than factory bypass.

### Evidence

1. **Colon-bearing slot values break naive parsing.** Rust module stand-ins like
   `rust:std::collections::HashMap:0-0:module:module` carry the native `::`-namespaced
   module path in the path slot — eight colons (nine naive `split(":")` tokens) against
   the documented four. INV-dulah's pass-20 finding catalogued four distinct colon-count
   cases a parser must survive (Rust `::` paths, 3-colon npm-package IDs, colon-bearing
   route names, a Solidity carry-over).
2. **The runtime format gate is shape-only.** `_check_id_format` and
   `_CANONICAL_ID_PATTERN` (`spec_validator.py:1159-1320`) verify the five-slot *shape*
   but never round-trip slot values against `Symbol.name` / `Symbol.kind`. An ID whose
   name slot disagrees with `Symbol.name`, or whose kind slot carries a value that is
   not a symbol-kind registry member, passes the gate.
3. **Kind-slot impurity at scale.** ~1,645 synthetic external-reference nodes carry
   use-site *reference syntax* in the kind slot instead of `node.kind`:
   `unresolved` (1,312), `attribute` (214), `doc_link` (106), `module` (11),
   `script` (1), `package` (1) — all on nodes whose `node.kind` is `external_symbol` —
   plus a small non-external remainder (`npm_package`→`package` 2, `tsconfig`→`file` 1).
   (WI-pubiv; the related route/event *role*-in-kind-slot cohort of 22 nodes is a
   distinct producer bug split out to WI-kugaj.)
4. **6.0.0 already groped toward the rule, per-site.** The Phase 6 sweeps sanitized
   colons ad hoc at individual emit sites: Rust impl-method `::` → `.` in the ID name
   slot (`rust.py:953`), websocket event `:` → `_` (`linkers/websocket.py:325`), plus
   parallel one-off escapes in `markdown.py:78-80` and `js_module.py:235`. Four sites,
   three different replacement characters, no governing principle. This ADR codifies
   the rule those fixes were groping toward.

## Decision

### Ruling 1 — Anchored grammar with slot-level colon policy

The 5-slot form `{lang}:{path}:{start}-{end}:{name}:{kind}` is **retained**, now as a
grammar with per-slot colon rules:

| Slot | Colon policy | Charset contract |
|---|---|---|
| `lang` | MUST be colon-free | lowercase identifier, member of the language catalog |
| `path` | **colon-tolerant** | any non-empty string; Rust `::` module paths, Windows drive prefixes etc. keep their native form |
| `span` | MUST be colon-free | `\d+-\d+` |
| `name` | MUST be colon-free | sanitized per the rule below; may be empty |
| `kind` | MUST be colon-free | lowercase identifier, member of the symbol-kind registry (Ruling 2) |

**Name sanitization.** Names containing `:` are sanitized `:` → `.` in the ID name slot
**only**. This is documented as **lossy**: the ID is a location-addressed key, not a
fidelity surface — full-fidelity names live in `Symbol.name` (and `qualified_name` per
ADR-0032). The single `.` replacement supersedes the three divergent per-site choices
from 6.0.0 (`.`, `_`, ` `); existing sites migrate to the canonical rule as the
factories absorb it. Consumers that need the exact name MUST read `Symbol.name`, never
re-derive it from the ID.

**Anchored parsing.** Because exactly one slot is colon-tolerant, parsing is anchored
from both ends and is deterministic without escaping:

- `lang` = everything up to the **first** `:`;
- `kind` = everything after the **last** `:`;
- `name` = the second-to-last slot (colon-free by construction);
- `span` = the third-from-last slot, which MUST match `\d+-\d+`;
- `path` = the remainder between `lang` and `span`.

Operationally: `rsplit(":", 3)` for the right anchors, `split(":", 1)` for the left
anchor, then per-slot charset validation; any slot failing its contract is a validator
violation. This is the normative statement of the parse the IR's `_parse_dangling_id`
(`ir.py:1093`) and `_CANONICAL_ID_PATTERN` already approximate — v2 makes the
last-3-tokens / first-token rule the grammar, not an implementation accident.

### Ruling 2 — Kind-slot purity

**The ID's kind slot MUST equal `node.kind` (a symbol-kind registry value per ADR-0027)
on every node.** The kind slot is a denormalized copy of `node.kind`, nothing else — not
reference syntax, not a framework role, not a resolution status.

The ~1,645 synthetic external-reference nodes where the slot carries use-site reference
syntax (`unresolved` / `attribute` / `doc_link` / ...) migrate:

- the reference syntax moves to a **registered meta key** — `meta.reference_syntax`,
  registered with a `MetaKeySpec` entry in the axis-meta-keys registry per ADR-0024;
- the IDs change (kind slot becomes `external_symbol`), riding the
  `make_synthetic_symbol()` chokepoint planned in the same campaign (strategy Wave 2,
  item synthetic:F2). No per-site sweep — the chokepoint applies the grammar once, per
  the campaign's "factory/post-pass, never per-site sweeps" doctrine.

The validator round-trip check becomes: **kind slot == `node.kind` AND `node.kind` ∈
symbol-kind registry.** Both halves are necessary: equality alone would bless a node
whose `kind` field itself carries a non-registry value.

Coordination notes:
- `Edge.dst` strings that reference the migrating IDs (e.g. the cgo linker's
  `go:C:0-0:{name}:unresolved` prefix match, `linkers/cgo.py:66`) are resolution-status
  conventions owned by ADR-0037 (edge resolution semantics, strategy synthetic:F3). The
  ID change and the edge-finalization change land in coordinated releases so the
  `:unresolved` suffix convention is retired from node IDs and re-derived on edges from
  the single resolution verdict, not left half-migrated.
- The 22-node route/event cohort (kind slot carrying a *role* — `route`,
  `event_subscriber`, `event_publisher` — that `node.kind` normalizes to `function`) is
  the same purity violation from a different producer; it is tracked separately as
  WI-kugaj and resolves via ADR-0027's `meta["framework_role"]` pattern, not
  `meta.reference_syntax`.
- Which language the **lang slot** of a synthetic ID carries when `Symbol.language` is
  `None` (ADR-0031 Class B) is settled at the `make_synthetic_symbol()` chokepoint; the
  existing corpus uses the discovery language (e.g. `go:C:0-0:puts:...` from Go call
  sites), and the chokepoint is expected to bind the slot to
  `discovery_language or language`. This is an implementation point under ADR-0031's
  field semantics, not a new ruling here.

### Ruling 3 — Sentinel enumeration

Legal sentinel values per slot are **enumerated here**. Anything outside the
enumeration is a validator violation — ending the bug-vs-intent ambiguity where a
reviewer could not tell whether `0-0` or `<external>` in an ID was a deliberate
convention or an emitter defect.

| Slot | Sentinel | Meaning | Anchor |
|---|---|---|---|
| `path` | `<external>` | external/boundary pseudo-symbol with no file anchor | `ir.py:1130-1136` (parser), `ir.py:1289` (boundary synthesizer) |
| `span` | `0-0` | synthetic node with no source location (module stand-ins, external references, protocol synthetics) | dependency-linker module IDs (`linkers/dependency.py:56-58`), cgo externals |
| `span` | `1-1` *(with name `file`, kind `file`)* | file pseudo-symbol | `make_file_id`, `base.py:309-319` |
| `name` | `file` *(only in the file-pseudo-symbol triple above)* | file pseudo-symbol | `make_file_id` |

The `lang` and `kind` slots have **no sentinel values**: `lang` must always be a
catalog language and `kind` must always be a registry symbol-kind. Extending this table
requires amending this ADR (or a successor registry module, if the enumeration later
outgrows a table — at four rows it does not).

### Enforcement

Three layers, extending ADR-0034's:

1. **Factories** (`make_symbol_id`, `make_file_id`, and the Wave-2
   `make_synthetic_symbol()` chokepoint) apply the colon policy: reject or sanitize
   per-slot at construction time, instead of interpolating blindly.
2. **Runtime validator** (`spec_validator.py` `id_format` class) gains round-trip
   sub-checks (strategy id-format:F3, landing as the canary *before* cohort
   migrations): kind slot == `node.kind` ∈ registry; name slot ==
   sanitized `Symbol.name`; span/path sentinels ∈ the Ruling 3 enumeration.
3. **Static linter** (WI-vodin) flags `Symbol(id=f"...")` construction at the source
   level, after the factory API is final (strategy id-format:F4).

### Amendment — id-format:F3 partial landing (2026-06-13)

The Enforcement layer-2 round-trip validator (`spec_validator._check_id_roundtrip`,
wired into `validate_ir`) shipped as a Wave-2 T0 PR. What landed, and the gating:

- **Round-trip (Ruling 2), advisory.** For ids already passing the shape-only
  `_CANONICAL_ID_PATTERN`, the check parses the last three colon-free tokens via
  `rsplit(":", 3)` and flags **kind-slot ∉ registry** and **kind-slot ≠
  `Symbol.kind`**, both at `warning`. Ruling 2 phrases the membership half as
  `node.kind ∈ registry`; the implementation instead checks the **id kind-slot**
  ∈ registry, because `axis_conformance` already owns `node.kind` membership.
  Checking the slot is the net-new "purity" instrument and is strictly stronger:
  it also guards a malformed slot when `Symbol.kind` is absent. The two verdicts
  coincide on every realistic case (when slot == kind, slot ∈ registry ⇔ kind ∈
  registry).
- **Name-slot non-empty, advisory.** A subset of the grammar; the full Ruling-1
  `name slot == sanitized(Symbol.name)` round-trip and the producer-side
  `make_symbol_id` `:`→`.` sanitization (id-changing for any colon-named symbol,
  hence T1) are **not yet landed** — deferred follow-ups.
- **Span `start <= end`, error.** A subset of Ruling 3 with no id-changing
  backlog. The **full Ruling-3 sentinel enumeration** (path `<external>`; the
  `0-0` and `1-1`+`file`+`file` span triples) is **not yet landed** — a deferred
  follow-up (it risks false positives on uncatalogued conventions and wants a
  corpus scan before going strict).
- **Why advisory.** A strict (error) pass red-flags the known id-changing (T1)
  backlog that cannot clear before the v6 stable_id bump: the ~1,645
  external_symbol kind-slot disagreements (WI-pubiv), the 22-node route/event
  role cohort (WI-kugaj), and the single tsconfig node (audit-findings 0005,
  whose producer folded `Symbol.kind` → `file` but left `tsconfig` in the id
  kind-slot). The canary makes that backlog **measurable now** (the
  schema-coverage-corpus ratchet baselines were bumped +11 to pin it); a gating
  tracker item promotes the membership/round-trip/name checks to `error` once
  those folds land.
- **Decision #5 (register-vs-fold), resolved.** GraphQL `mutation` /
  `subscription` are **registered** as `language_construct` siblings of
  `query`/`fragment` — they were an audit-findings 0007 omission, NOT a
  deliberate unify-to-`query` fold (the only `query` verdict on record is
  SPARQL's, audit-findings 0007). The anonymous-operation fallback `operation`
  is registered as `pending_classification` (its semantic fold to `query` is
  id-changing, deferred). `tsconfig` is **not** registered — it is
  DEPRECATE-NO-FOLD (audit-findings 0005); the fix folds its id kind-slot to
  `file` (id-changing/T1). Registering kinds is identity-neutral.

## Alternatives considered

1. **Reversible percent-escaping in all slots** (escape `:` as `%3A` everywhere,
   making every slot colon-free and the grammar trivially splittable). Rejected:
   it changes every existing colon-bearing ID — all Rust module nodes — for zero
   information gain; it hurts grep-ability and human readability of the single most
   user-visible identifier in the output; and every consumer (including external
   ones) must learn to unescape. The anchored grammar achieves deterministic parsing
   with one lossy sanitization in one slot, leaving the high-volume Rust path-slot
   IDs byte-identical.
2. **Opaque IDs** (random or hash-based node IDs with all semantics in typed fields).
   Rejected for now: `Edge.src` / `Edge.dst` are bare ID strings, and the
   cross-language edge detector branches on the first slot today (the failure mode
   ADR-0034 documented for INV-sadiv's path-prefixed IDs demonstrates how load-bearing
   that slot is). A typed-endpoint migration is far larger than this ADR and is not
   blocked by it — the anchored grammar is forward-compatible with later opacity,
   since consumers are now told to read `Symbol.name`/`Symbol.kind` rather than parse
   slots for fidelity.

## Consequences

### Positive

- **The grammar is decidable.** Every ID either parses under the anchored rule or is a
  validator violation with a specific slot-level diagnosis. The "4 different colon-count
  cases" parser burden INV-dulah documented collapses to one rule.
- **Resolves WI-pubiv** (the ~1,645-node kind-slot disagreement) with a structural
  answer — purity plus a registered meta key — instead of a documentation shrug, and
  unblocks the INV-dulah cohort under the INV-kurup META umbrella.
- **Codifies the 6.0.0 fixes.** The per-site sanitizations stop being four local
  conventions and become instances of one rule with one replacement character.
- **The round-trip validator becomes specifiable.** "Shape-only" was the gate's ceiling
  because no spec said what the slots must round-trip against. Now one does.

### Negative

- **~1,645 node IDs change** when the kind-slot migration rides the
  `make_synthetic_symbol()` chokepoint. Edge endpoints referencing those IDs are
  remapped in the same event (coordinated with ADR-0037's edge finalization).
  Cross-version ID pinning for those nodes breaks; same precedent as the ADR-0023 /
  ADR-0027 / ADR-0031 identity-churn events.
- **Name sanitization is lossy by design.** A consumer holding only an ID cannot
  distinguish a literal `.` from a sanitized `:` in the name slot. Accepted: fidelity
  lives in `Symbol.name`; the ID is an address.
- **Naive `split(":")` consumers must move to anchored parsing.** Internal consumers
  already mostly use the last-3-tokens shape; external consumers get the grammar
  spelled out here.

### Neutral / acknowledged

- The `make_synthetic_symbol()` chokepoint, the validator sub-checks, and the static
  linter are **campaign work items, not deliverables of this ADR** (strategy Wave 2:
  synthetic:F2, id-format:F3, id-format:F4 / WI-vodin). This ADR is the spec they
  implement.
- `Edge.id` format and the `:unresolved` dst-suffix retirement are ADR-0037 territory.
- stable_id hashing is untouched; identity-contract changes are ADR-0035.

## Tracker items

- `WI-pubiv-rasut-rijuk-gutof-mupoz-sugif-lovuh-razov` — node.id kind-slot disagrees
  with node.kind on ~1,645 external_symbol nodes. **Resolved by Ruling 2** (purity +
  `meta.reference_syntax`), implementation riding the Wave-2 chokepoint.
- `INV-dulah-kisiz-gobak-jafov-soduj-tomim-dufut-kinil` — node.id format escapes the
  documented 5-slot shape (Rust `::`, npm packages, route IDs); pass-26 expanded its
  scope to the kind-slot cohort. **Unblocked by Rulings 1-2**; closes on the validated
  zero-violation cohort run after the chokepoint migration.
- `INV-kurup-tidam-ribus-rorif-jigad-tijah-losus-funah` — META umbrella: identifier-
  bearing fields emit non-canonical formats from several emission paths. This ADR
  supplies the missing grammar for the node.id member of the family.
- `WI-vodin-jugin-kugib-fizab-sobun-sivub-tohim-guzat` — static-AST linter for
  `Symbol(id=f"...")` patterns + `Edge.id` validator extension. **Enforcement layer 3**
  of this ADR; lands after the factory API is final.
- `WI-kugaj-vaguj-bisog-nupod-dahur-guzir-konom-bukun` — the route/event *role*-in-
  kind-slot split-out (22 nodes). Same purity violation, different producer and
  different meta key (`framework_role` per ADR-0027); resolved under its own item in
  conformance with Ruling 2.

## References

- ADR-0034 (ID-Construction Discipline): the factory monopoly this grammar binds; its
  §"Enforcement" gap (no source-level check) is WI-vodin.
- ADR-0033 (Spec-vs-Data Validator Stage): the runtime substrate for the round-trip
  sub-checks.
- ADR-0035 (stable-id v6 identity contract): the sibling identity surface; this ADR
  deliberately changes nothing about hash identity.
- ADR-0037 (edge resolution semantics): owns `Edge.dst` `:unresolved` suffix retirement
  and `is_resolved`/`dst_ref` finalization; coordinated release with Ruling 2's
  migration.
- ADR-0027 (Symbol.kind registry) and ADR-0024 (axis template): the kind vocabulary and
  the meta-key registration mechanics.
- ADR-0031 (Symbol.language reshape): `discovery_language` semantics informing the
  synthetic-ID lang slot.
- Strategy: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` §1
  (id-format-factory-bypass corrected thesis), §2 Wave 1 item 7 (this ADR) and Wave 2
  (synthetic:F2 chokepoint, id-format:F2-F5).
- Code anchors: `analyze/base.py:288-306` (`make_symbol_id`), `base.py:309-319`
  (`make_file_id`), `spec_validator.py:1159-1320` (shape-only gate), `ir.py:1093`
  (`_parse_dangling_id`), `rust.py:953` and `linkers/websocket.py:325` (the 6.0.0
  ad-hoc sanitizations this ADR codifies).
