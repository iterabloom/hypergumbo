<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0035: stable_id v6 Identity Contract — Unique-Within-Run Scope-Chain Hashing

- Status: **Accepted**
- Date: 2026-06-10
- Decided by: project owner, via design interview (2026-06-10) over the verified 446-item tracker root-cause analysis (`~/hypergumbo_lab_notebook/correctness_strategy_06102026.md`, identity-hash-coarseness family — 22 items, 4 of the corpus's 6 P0s). This document records a decided ruling, not a proposal.
- Supersedes: ADR-0014's "survives renames and file moves" primary contract for `stable_id` (the field survives line drift and body edits but no longer survives moves/renames); the consumer-side `(stable_id, canonical_name)` disambiguation policy previously accepted as the INV-zudob residue escape hatch (dead since ADR-0032 removed `canonical_name`).
- Superseded by: —
- Related: ADR-0014 (Generalized Symbol Identity — the contract being revised), ADR-0031 (Symbol.language reshape — the empirical churn precedent: predicted 20-30 changed ids, actual 262), ADR-0032 (canonical_name removal — kills the old collision escape hatch), ADR-0033 (Spec-vs-Data Validator Stage — the enforcement substrate for §5), ADR-0034 (ID-Construction Discipline — the factory-chokepoint pattern this ADR's per-kind table rides on), ADR-0036 (node.id grammar v2 — the sibling identity decision for `Symbol.id`; the stable_id-rendering slice lands with it in the same train).

## Context

### What stable_id was promised to be

ADR-0014 defined `stable_id` as *interface identity*: a signature-based hash designed to "track symbols across refactors (renames, moves, documentation changes)." The spec's worked example (`docs/hypergumbo-spec.md`, "Example" block under §identity fields) still says exactly that: rename `authenticate` to `verify_credentials`, move the file, and "stable_id stays the same (signature unchanged)."

### What it actually is

The promise was falsified twice over, in opposite directions:

1. **It never delivered uniqueness.** The original hash inputs (kind, param_count, arity_flags, decorators, containing_stable_id) collided at ~60% on the dogfood corpus — 155 bash functions in one file shared one stable_id; 152 zero-parameter pytest tests collided likewise (recorded at `spec_validator.py:882-897`). The Phase 6 PR3 fix (scheme v5) added `name` + `qualified_name` to the hash basis (`analyze/base.py:2333-2338`). Residue persisted: 18.94% of Python class nodes still collide cross-module post-v3 (49× `TestAdaAnalysisUnavailable`-style per-language fallback classes, 36× `TestAnalysisRun` — INV-zudob); Python function-locals omit the enclosing function from `containing_stable_id` and collapse (WI-gitun, P0); `make_typed_stable_id` never took a `name` parameter at all (`analyze/base.py:770-775`), so distinct-named TypeScript methods collapse at 16.88% on HTRAC (WI-zitod); `make_dependency_stable_id` hashes only `(language, name)` (`analyze/base.py:591-598`), so the same package declared in five manifests is one node (WI-titiz, P0); and `py.py` carries its own divergent local `_compute_stable_id` (py.py:1574-1625) that earlier sweeps never listed as a producer.

2. **It silently stopped delivering rename-stability.** v5's fix put `name` and `qualified_name` into the hash. From that commit forward, renames churn `stable_id` — but the spec's worked example, the "Does NOT change when: Renaming, moving between files" contract line, and the `stable_id_scheme = "hypergumbo-stableid-v2"` version string were never updated (WI-foful). The documented contract and the shipped behavior have described different fields for months.

So the field today is neither unique (its operational requirement as a graph key) nor rename-stable (its documented promise). It is an ambiguous foreign key with stale documentation.

### Why ambiguity is a P0, not a nuisance

The wrong-sibling-attribution class is already live at P0 severity on the read side: 506 `calls` edges attribute the call to a same-base-name sibling method whose span does not contain the call line (WI-jafat). That specific defect is an edge-endpoint resolution bug, not a stable_id collision — but it demonstrates exactly what an ambiguous identity key does to consumers: an LLM agent attributes behavior to the wrong method. Any contract that *accepts* stable_id collisions keeps that failure class structurally possible at every join over the field.

### The fix-then-reopen history falsifies per-symptom patching

INV-tazaj (the META umbrella: "hash input set too coarse") was closed 2026-06-01 on a validator all-clear that was false — the collision check's denominator silently excludes the None-stable_id cohort (15.2% of Symbols on the max10 substrate; WI-niluv), and the 5% umbrella threshold (`spec_validator.py:897-904`) tolerates thousands of collisions below the alarm line. It was reopened 2026-06-07. Three structural lessons: the contract must be collision-free *by design*, not collision-rare; the validator must measure against an honest denominator; and the producers must be fixed as one event, not serially (ADR-0031's serial-bump fallout: predicted 20-30 changed ids, actual 262).

### The dead escape hatch

The previously accepted policy for INV-zudob's same-shape residue was: consumers disambiguate collided twins by joining `(stable_id, canonical_name)`. ADR-0032 removed `canonical_name`. The escape hatch no longer exists; any policy resting on it is void. This ADR supersedes that policy rather than reconstructing it on `display_label`.

## Decision

### 1. Primary contract: UNIQUE-WITHIN-RUN

`stable_id`'s primary contract becomes **uniqueness within a single analysis run**, achieved by folding the full scope chain into the hash:

```
repo-relative module/path → enclosing class(es) → enclosing function(s)
  → name → kind → within-scope occurrence index (where applicable)
```

plus the existing structural inputs (signature/arity flags, decorators, visibility where present). The occurrence index applies only when two definitions are otherwise hash-identical within one scope (e.g., conditional redefinition, same-scope overloads); it is an ordinal over emit order within that scope, never a line number.

What survives: line drift, body edits, comment/docstring changes, edits elsewhere in the file. What now churns: file moves and renames of the file, container, or symbol. **Zero by-design collisions** — two semantically distinct symbols never share a stable_id by construction.

Cross-version content tracking — "is this the same function after the refactor?" — is explicitly delegated to `fingerprint` (content identity) and `shape_id` (structural identity), which exist for exactly that purpose. `stable_id` is the graph's unambiguous foreign key; the content hashes are the refactor-tracking instruments.

This includes unifying `py.py`'s divergent local `_compute_stable_id` (py.py:1574-1625) with the base formula, fixing WI-gitun's enclosing-function omission via the scope chain, and giving `make_typed_stable_id` mandatory `name`/`qualified_name` parameters (closing WI-zitod's producer gap at `analyze/base.py:770-775`).

### 2. The symbol NAME stays in the hash; the spec's worked example is rewritten

v5 already hashes `name` + `qualified_name` (`analyze/base.py:2333-2338`); v6 keeps them. The spec's worked example claiming "stable_id stays the same" across a rename is **rewritten** to state the v6 contract: rename ⇒ new stable_id; same `fingerprint` + new name ⇒ detectable rename. Rename-tracking is fingerprint's job, and under this contract it becomes a *positive capability* (join on fingerprint across versions, observe the name change) instead of a silent identity merge. The rewrite rides WI-foful's spec-history backfill (v2 → v5 → v6 scheme chain documented before the bump — never bump onto an undocumented chain).

### 3. Occurrence policy for synthetic nodes — the per-kind two-axis rule

Synthetic nodes (linker stand-ins, doc-graph nodes) previously collapsed or multiplied per-producer with no declared policy. v6 declares one rule with two axes, decided per kind by what the node *represents*:

- **LOGICAL identity** — kinds representing an **external target** (a thing that exists once regardless of how many code sites reference it). One node per logical thing, deduped, stable across runs. Constructed `make_protocol_stable_id`-style (`analyze/base.py:652-686`): category namespace + logical identity parts.
- **SITE identity** — kinds representing a **code location** (a specific place in a specific file). Keyed `(path, logical target, within-file occurrence index)`. **Line numbers stay out of identity hashes** — they live in `span` for display. Rationale: a line-keyed id churns on every unrelated edit above the site; an occurrence-indexed id churns only when same-target sites in that file are added, removed, or reordered.

The normative kind→axis table. This table is the artifact; the validator enforces conformance, and a new synthetic-emitting kind MUST be added to this table (via ADR amendment or successor audit-findings doc) before it ships.

> **Reading note (kept current to v8).** This table was authored as the v6 design and is now annotated in place so a reader extracting only §3 sees the **live keys**. Several rows were re-keyed by the later amendments below — the synthetic:F2 protocol-synth chokepoint, and especially the **v8 SITE-key + widened-route-key** amendment (2026-06-16). Where a row's v6 mint key was superseded, the cell now leads with **CURRENT (v8):** and points to the amendment; the amendment section is retained as history/rationale. Rows without a **CURRENT (v8):** marker are unchanged from v6.

| Kind / synthetic family | Representative producers | Axis | Identity key |
|---|---|---|---|
| Message-queue topic / event channel | `message_queue.py`, `event_sourcing.py` | LOGICAL | `(category, queue_type, topic)` |
| Database query / table stand-in | `database_query.py` | LOGICAL | `(category, db, logical query/table)` |
| External symbol (unresolved import / external package target) | import linkers, `dependency.py` externals | LOGICAL | `(ecosystem/language, module path, name)` — current as of v8; a kind-slot re-keying (the "5b" limb, ADR-0036 Ruling 2, ~1,645 node-id changes) is **deferred** to the v6/ADR-0037 coordinated event, NOT landed in this slice (see synthetic:F2 amendment below). |
| `route` | route analyzers/linkers | LOGICAL (within-run id v8-widened) | **CURRENT (v8):** `route_site:{language}:{normalize_path(rel_path)}:{route:{method}:{path}}` — the v6 mint key `route:{method}:{path}` is widened by the `widen_route_stable_ids` post-pass with the declaring file + language (see the v8 amendment below: WI-gokiv). A §3-only reader must use the widened key; the bare `route:{method}:{path}` collided cross-file/cross-language (146 nodes). |
| Entry-point | entrypoint detection | LOGICAL | `entry:{entry_type}:{name}` (unchanged) |
| `call_site` (http) | `http.py` → `make_site_stable_id` | SITE | **CURRENT (v8):** `site:http:{rel_path}:{method}:{url_path}` at mint; `:occ:<n>` added downstream (v8, WI-napoh — see amendment below). Replaces the v6 file-blind `make_route_stable_id`/`make_protocol_stable_id` factory this SITE kind wrongly borrowed (31 http/sql nodes collided). |
| `call_site` (subprocess) | `subprocess_cli.py:332` | SITE | `(path, invoked command, occurrence idx)` |
| `call_site` (sql) | `database_query.py` → `make_site_stable_id` | SITE | **CURRENT (v8):** `site:db_query:{rel_path}:{query_type}:{tables}` at mint; `:occ:<n>` added downstream (v8, WI-napoh — see amendment below). Replaces the v6 file-blind LOGICAL factory this SITE kind wrongly borrowed (counted in the 31 http/sql collision nodes). |
| `call_site` (abi) | `solidity_abi.py:211` | SITE | `(path, contract/function, occurrence idx)` |
| `link` (markdown) | `markdown.py:317` | SITE | `(path, link target, occurrence idx)` |
| Protocol-synth stand-in — null-filled by the `make_synthetic_symbol_identity` chokepoint (IPC, Phoenix IPC, OpenAPI operation, ObjC bridge incl. `#selector` references, WebSocket, WASM-bindgen, yjs_crdt, crypto_flow, …) | `ipc.py`, `openapi.py`, `swift_objc.py`, `websocket.py`, `wasm_bindgen.py`, `yjs_crdt.py`, `crypto_flow.py`, … | injective (SITE-style) | `(protocol_origin, kind, path, name, occurrence)` |

**Amendment (synthetic:F2, 5a).** The protocol-synth row above was added when the
`make_synthetic_symbol_identity` chokepoint (`analyze/base.py`) began backstop-stamping
`stable_id` / `display_label` / `fingerprint` on Class-B stand-ins (`language=None` +
`protocol_origin` set) that their linkers left null. The stable_id key is **injective** over
`(protocol_origin, kind, path, name, occurrence)`: `kind` separates a definition from a reference to
the same name (`@objc func` vs a `#selector` use-site), `path` separates same-named stand-ins minted
in different files, and the within-key `occurrence` index — the **line kept out of the hash** (the
SITE rule above, applied uniformly) — separates role-distinct same-name siblings a linker leaves
otherwise-identical (e.g. a CRDT writer and an observer on the same channel, both `kind="function"`
with `name=channel`). A coarser `(protocol_origin, name)` key was **rejected**: applied to these
families it manufactured by-design collisions (an `@objc` definition with a `#selector` reference; a
writer with a same-channel observer), turning honest `stable_id=None` into a *wrong, colliding*
value — exactly the "accepted collisions" option §1 rejects. Only **message_queue / event_sourcing /
database_query** self-stamp `stable_id` at mint (with their own family-specific keys, e.g.
message_queue's `(queue_type, type, topic)`) and are preserved byte-for-byte by the chokepoint's
skip-if-set guard; the other protocol families — including **yjs_crdt** and **crypto_flow** — leave
`stable_id=None` and are filled here. (graphql / graphql_resolver also mint Class-B nodes; their
self-stamped `stable_id`s are preserved untouched, with any non-canonical-format residue tracked
separately under `_check_stable_id_format`.) The 5b external_symbol kind-slot re-keying (ADR-0036
Ruling 2, ~1,645 node-id changes) is deferred to the v6 / ADR-0037 coordinated event, not this slice.

The External-symbol identity key matches the shipped boundary dedupe behavior for non-file kinds (`_dedupe_key` / `_canonical_external_stable_id`, `ir.py:1065-1137`): the module-path slot keeps same-named symbols in different external modules distinct, as §1's zero-by-design-collision contract requires. `(ecosystem, name)` is §4's presentation-time aggregation key — a view rule, never an identity key.

**Amendment (v8 — SITE-key + widened-route-key, 2026-06-16; WI-gokiv / WI-napoh / WI-bolup).** The Wave-2 identity gate (27-repo corpus + self-tree) confirmed the v6/v7 producers and per-file gate are clean, but found the corpus collision rate is NOT ~0 — 149 cross-file collisions, all in synthetic/linker-stamped nodes the file-identity train had not yet reached: (a) **`route` (146 nodes)** — `make_route_stable_id` keys `route:{method}:{path}` file-blind, so the same `(method, path)` in different files (express's 33 example apps) or different languages (gin-Go + express-JS) collides; (b) **http/sql `call_site` (31 nodes)** — these SITE-axis kinds borrowed the LOGICAL `make_route_stable_id` / `make_protocol_stable_id` factories (file-blind), violating their own §3 SITE classification; (c) **CBV HTTP-verb methods (2 nodes)** — `py.py` routed `kind="method"` declarations through `make_route_stable_id(verb, class_name)`, a category error (a method is a file-anchored declaration, not a LOGICAL route). The 2026-06-16 human ruling: `route` stays **LOGICAL in spirit** (within-app dedup is owned structurally by the route materializer's `seen_routes`, keyed on meta — not by stable_id) but its **within-run identity widens** with the declaring file + language. The fix is a coordinated **v7→v8 atomic bump** (§6): (a) a post-pass `widen_route_stable_ids` folds `route_site:{language}:{normalize_path(rel_path)}:{id}` onto route nodes (a post-pass, not 16 producer edits, because the producers carry absolute paths at mint while `sym.path` is repo-relative only after the normalisation pass — folding the normalised path is the single complete, location-independent point); (b) a new `make_site_stable_id(protocol_origin, rel_path, target)` factory in a dedicated `site:` namespace path-anchors the http/sql call_sites at mint (the within-file `:occ:` ordinal is still completed downstream by `split_within_file_stable_id_collisions`); (c) the `py.py` CBV verb-method branch is deleted so those methods flow through the normal file-anchored `make_typed_stable_id`/`_compute_stable_id` path. The closure metric stays **all-axis** (no LOGICAL exclusion): the producer fixes drive the honest count to ~0 directly, and the genuinely-by-design LOGICAL kinds (message_queue / event_sourcing) are collapsed to one node by `dedup_logical_synthetic_identities` so they never present as collisions — making an axis-filtered metric both unnecessary and unsafe (it would risk re-hiding the genuine INV-zudob cross-file declaration class).

### 4. Dependency identity: one node per manifest declaration

`make_dependency_stable_id` (`analyze/base.py:591-598`) gains the declaring manifest path: a package declared in N manifests becomes **N nodes**, each keeping its own span, version constraint, and provenance. Consequences: version skew across workspace members becomes node-level detectable (today `rich` pinned differently in two `pyproject.toml`s is one node with one constraint, silently wrong for at least one manifest — WI-titiz measured 15 cross-file collapses on the self-substrate); "all dependencies" views aggregate by `(ecosystem, name)` at presentation time instead of pre-collapsing at identity time.

The same-shape analogues are **audited for the same gap in the same train**: `make_module_stable_id` (`analyze/base.py:581-588`), `make_interface_stable_id` (`analyze/base.py:632-639`), and `make_type_stable_id` (`analyze/base.py:642-649`) all hash only `(language, name)` — two `IRepository` interfaces in different files collapse exactly like the dependency case. The audit decides path-anchoring per kind and any resulting hash change rides this same scheme bump (decision §6), not a later one.

The read-side companion defect — the dependency linker's global flat `dict[dep_name → Symbol]` that misattributes `depends_on_manifest` edges to whichever manifest was written last (WI-timon, `linkers/dependency.py:83-104`) — becomes *fixable* once per-manifest nodes exist; the package-scoped lookup fix lands in the same release window but is a read-side change, not part of the hash contract.

### 5. Enforcement: hard per-file uniqueness + near-zero corpus threshold with an honest denominator

Two validator changes (ADR-0033 substrate), replacing the 5% umbrella threshold at `spec_validator.py:897-904`:

- **Per-file emit-time uniqueness — hard check.** Within one file's emitted symbols, a duplicated stable_id is an `error`, not a rate contribution. Zero tolerance; this is the by-design-collision-free contract made executable at the producer boundary.
- **Whole-corpus collision rate — threshold near zero.** The umbrella check's threshold drops from 5% to effectively zero (a shrink-only pinned baseline for not-yet-migrated producers, ratcheted to 0). The denominator **includes the None-stable_id cohort** (WI-niluv's lesson): the report states both the collision rate over all Symbols and the None-cohort size as separate lines, so "no stable_id" and "colliding stable_id" are never conflated and a false all-clear of INV-tazaj's 2026-06-01 shape cannot recur. WI-niluv's denominator fix must land before or with this gate. **Landed-vs-deferred (see status blocks below):** the threshold-and-denominator validator surface and the producer reconciliation passes have landed (corpus-wide 43 → 14 collision groups, 0.43% → 0.1% on the self-tree); the corpus rate is **not yet at the ratcheted-to-0 target** — the residual ~14 are the INV-zudob cross-file file-identity class, whose fix is the deferred v7/WI-bokab bump (§6 follow-on), not this slice. The v8 amendment further drove the corpus collision count toward 0 by re-keying the route/SITE synthetic families.
- **Kind→axis conformance.** The §3 table is checked: synthetic kinds in the table must carry identity of the declared shape; a synthetic-emitting kind absent from the table is a violation.

Closure of the identity umbrellas (INV-tazaj, META-fabaz, INV-zudob) requires one full-corpus re-measurement under the new gates — positive evidence, not non-reproduction. **Status:** INV-tazaj's per-file (HARD) limb is met on the real corpus; the corpus-rate limb is **partially closed** — driven from 0.43% to 0.1%, with full closure of the INV-zudob cross-file class awaiting the deferred v7 file-identity bump (WI-bokab) and the v8 route/SITE re-keying. The two status blocks immediately below record precisely what landed vs what remains deferred.

**Implementation status (validator surface landed; producer fix deferred).** The validator
half of this section shipped as a follow-on PR to the §6 atomic bump: `validate_ir` now runs
`_check_stable_id_per_file_uniqueness` (the hard `error`), the corpus umbrella threshold dropped
to ~0 over an all-Symbols denominator, and `compute_stable_id_stats` surfaces the always-present
`validation_report.stable_id_stats` disclosure (report `schema_version` 0.1 → 0.2). The
**kind→axis conformance** limb is satisfied by the pre-existing `axis_conformance` check, which
already validates *every* `Symbol.kind` (synthetic stand-ins via the `language=None` /
`external_symbol` / markdown-`link` markers included) against the registry; the LOGICAL/SITE axis
*shape* is not hash-verifiable post-hoc and remains a producer/factory concern (the
`make_*_stable_id` factory chosen, ADR-0034). The gates are live and the fixture corpus is
collision-free.

**Producer reconciliation (landed; stays scheme v6 per §6).** The per-file collisions the gate
surfaced are now driven to zero by two post-linker passes (`analyze/base.py`,
`split_within_file_stable_id_collisions` + `dedup_logical_synthetic_identities`), run after the
enclosure post-pass and before `finalize` (R1). SITE families (call-sites, shell `export`s,
markdown links, manifest entries, throwaway vars) are occurrence-indexed via a deterministic
`:occ:<n>` re-hash of the colliding 2nd+ members; the LOGICAL families (message-queue/event
topics) are deduped to one hub node with all edges rewired onto the survivor. (graphql is
excluded from the LOGICAL set — its `graphql` protocol_origin is shared by resolver fields and
`graphql.py` client call-sites, so a coarse dedup would collapse distinct client sites; graphql
collisions flow to the SITE occurrence pass instead.) This **realizes §6's reserved occurrence-disambiguation, so it carries no scheme bump**
— the re-hash mechanism is used because Symbols do not retain their hash inputs post-mint
(re-calling `assemble_stable_id` is impossible without widening the Symbol), but it stays scheme
v6 and only the colliding symbols' values change (narrow blast radius, not a corpus-wide rehash).
**Measured (self-tree):** per-file collisions 35 → 0; corpus-wide 43 → 14 groups (0.43% → 0.1%).
**The residual ~14 are a DIFFERENT mechanism, NOT closed here:** cross-file collisions of
same-named top-level symbols in file-scoped tree-sitter languages (e.g. a bash `usage()` defined
in several scripts) whose hash omits file identity — the INV-zudob class, whose fix folds file
identity into the top-level `containing_stable_id` (a genuine corpus-wide rehash), tracked
separately. **INV-tazaj's per-file (HARD) contract is now met on the real corpus; full closure of
the corpus-rate limb awaits that cross-file file-identity fix.**

### 6. One atomic v5→v6 scheme bump

All hash-input changes in this ADR — scope-chain folding (§1), `make_typed_stable_id` name parameters (§1), occurrence policy hash parts (§3), manifest-path dependency identity and any analogue changes from the §4 audit, plus the py.py producer unification — land as **one** `STABLE_ID_SCHEME` bump, `hypergumbo-stableid-v5` → `hypergumbo-stableid-v6`. Rationale: each piece alone invalidates most of the Python substrate; serial bumps re-trigger the ADR-0031 class of fallout (predicted 20-30 changed ids, actual 262) once per bump. Spec and migration guide update in the same PR train. Precondition: the spec's scheme-history backfill (WI-foful — the spec still says v2 in places, e.g. `docs/hypergumbo-spec.md:388,684,1842`).

**Follow-on `v6 → v7` (WI-bokab; 2026-06-16).** §4's file-anchoring landed in the v6 bump for the Python AST path (`py.py`) and the `make_*` kind factories, but the **tree-sitter producers** — `BaseAnalyzer.compute_stable_id` (untyped tier) and `make_typed_stable_id` (typed tier) — still passed an empty `containing_stable_id`, carrying scope only in `qualified_name`. Same-named top-level symbols in distinct files therefore collided cross-file for the ~46 non-Python analyzers (the INV-zudob class — the tree-sitter analogue of the v3→v4 Python fix). Threading `make_file_stable_id(lang, normalize_path(rel_path))` into those producers is a hash-input change, so per the atomicity rule above it lands as its own single bump `hypergumbo-stableid-v6` → `v7` (recorded in the spec scheme-history table). Python values are unchanged (already file-anchored since v4); the `rust.py`↔`rust_scip.py` SCIP byte-parity contract (WI-zakub) is preserved by anchoring both backends with the identical repo-relative path.

## Alternatives considered

1. **Refactor-stable identity with accepted collisions + composite disambiguation key.** Keep the ADR-0014 contract, accept the residue, tell consumers to join on a composite. Rejected: keeps an ambiguous graph foreign key forever; the wrong-sibling-attribution P0 class (WI-jafat's shape) stays structurally possible at every stable_id join; and the previously blessed composite — `(stable_id, canonical_name)` — is already dead (ADR-0032). Rebuilding the hatch on `display_label` re-creates the same two-field identity smear with a new name.
2. **Split into `stable_id` (refactor-stable) + new `anchor_id` (unique-within-run).** Honest about the two semantics, but a schema addition plus a consumer migration on top of an already-large v6 train, and it institutionalizes two identity fields whose division of labor every consumer must re-learn — when `fingerprint`/`shape_id` already occupy the refactor-tracking role the new `stable_id` would have kept.
3. **Drop name from the hash (signature-only purity).** Restores the letter of the ADR-0014 rename-survival promise. Rejected: recreates the mass same-signature collisions that forced the v5 fix (155 bash functions on one id; 152 zero-parameter tests on another). The promise was only ever kept at the cost of the field being useless as a key.

## Consequences

### Positive

- **Zero by-design collisions.** `stable_id` becomes a trustworthy graph key; every join, dedup, and cross-reference over it stops being probabilistically wrong. Closes the mechanism behind INV-tazaj, INV-zudob, WI-gitun, WI-titiz, WI-zitod at the root rather than per-symptom.
- **Spec and code converge.** The rename example, the contract lines, and the scheme string describe the shipped field (WI-foful).
- **Rename detection becomes a feature.** Same fingerprint + new name across versions = detectable rename, queryable; previously renames were silently absorbed and undetectable in either direction.
- **Dependency truth.** Per-manifest nodes expose version skew across workspace members; the WI-timon misattribution class gains the substrate needed to fix it.
- **Honest validation.** The validator can no longer issue the 2026-06-01 false all-clear; denominator scope is explicit (WI-niluv).

### Negative

- **File moves and renames churn stable_id.** Cross-version consumers pinning stable_ids (dogfood-corpus links, external diff tooling) must migrate to fingerprint/shape_id joins for content tracking. This is the deliberate price of the contract; the migration guide documents the join recipe.
- **One large churn event.** The v6 bump invalidates effectively every Python-substrate stable_id (and most others). Mitigated by atomicity (§6): one event instead of four or five.
- **"All deps" views need aggregation.** Consumers that treated one dependency node per package now aggregate by `(ecosystem, name)`; until they do, dependency counts inflate.
- **The occurrence index is order-sensitive within (scope, target).** Inserting an earlier same-target call site in a file shifts later sites' indices. Accepted: strictly less churn than line-keyed identity, and SITE-kind ids were never promised cross-edit stability.

### Neutral / acknowledged

- **fingerprint's own defects are not fixed here.** The delegation in §1 leans on fingerprint, which currently conflates a file with its sole function because the structural walk strips comments (WI-vufah). That fix (shapeid v2→v3 + hgfp2→hgfp3 domain tags) is a trailing event in the same release, deliberately not folded into the v6 hash bump.
- **WI-jafat itself is read-side.** Its 506 wrong-sibling edges come from edge-endpoint resolution over a bare-name dict, not from stable_id collisions; ADR-0037 (edge resolution semantics) owns that fix. This ADR removes the identity-layer ambiguity that makes the same failure class reachable through stable_id joins.
- **The id grammar for `Symbol.id` is ADR-0036's territory.** The stable_id-rendering slice (canonical `sha256:<16hex>` rendering helpers) lands with the same train under ADR-0034's factory discipline.

## Tracker items

Full eight-segment IDs per tracker convention:

- `INV-tazaj-bufod-dinuh-damoh-lubaf-zadod-jisav-duzuj` — META umbrella "stable_id uniqueness — hash input set too coarse" (P0, violated). Closed 2026-06-01 on a false validator all-clear, reopened 2026-06-07; this ADR is its structural answer. Closes only on full-corpus re-measurement under §5's gates.
- `WI-gitun-jubos-rurok-tigal-bugud-tomuh-joruh-kanaz` — P0: Python function-locals omit enclosing function from `containing_stable_id`; closed by §1's scope chain.
- `WI-titiz-jubar-pipav-vufig-dafap-rasak-doril-bopok` — P0: TOML dependency stable_id hashes only package name; closed by §4.
- `WI-jafat-jujih-rulon-novit-pufor-niduj-gonoj-bagal` — P0: 506 calls edges attributed to wrong same-base-name sibling; the ambiguity class motivating §1's zero-collision ruling (fix itself lands under ADR-0037).
- `WI-zitod-jizal-bodaj-gufap-taroz-mupap-nudah-kamim` — P1: js_ts distinct-named symbols collapse (typed-tier name gap); closed by §1's `make_typed_stable_id` change.
- `WI-timon-sofof-takat-mijis-mubav-hogot-japuf-pimuf` — P1: `depends_on_manifest` global flat lookup misattribution; §4's read-side companion.
- `INV-zudob-rohaj-nodaf-pifir-bunih-rimus-rikup-lasah` — 18.94% cross-module class-collision residue post-v3; closed by §1; its dead `(stable_id, canonical_name)` residue policy superseded by §5.
- `WI-foful-dodor-nokif-punus-hugap-sokoj-gonir-jamam` — spec scheme-string and rename-example drift (v2 vs shipped v5); §2's rewrite and §6's precondition.
- `META-fabaz-narat-dizot-bamid-jilob-maluf-ribuv-buzob` — Data Integrity meta-invariant ("ID generation must include all disambiguating information"); §1 is its verbatim enactment for stable_id.
- `WI-vufah-sativ-risup-zutit-tanuj-mazis-hobuh-sanuk` — fingerprint file-vs-sole-function collision; acknowledged dependency of §1's delegation, fixed in the trailing shapeid/fingerprint event, not here.
- `WI-niluv-holur-fonok-tosuk-muzij-hobig-bovaz-lonob` — validator denominator silently excludes None-stable_id cohort; §5's denominator rule; must land before or with the closure gate.

## References

- ADR-0014 (Generalized Symbol Identity) — the original contract; §4's route/entry formulas survive as the LOGICAL-axis exemplars.
- ADR-0031 (Symbol.language Reshape) — the serial-bump fallout precedent (predicted 20-30 changed ids, actual 262) behind §6's atomicity ruling.
- ADR-0032 (canonical_name and fingerprint Reshape) — removed `canonical_name`, killing the collision escape hatch this ADR supersedes.
- ADR-0033 (Spec-vs-Data Validator Stage) — enforcement substrate for §5.
- ADR-0034 (ID-Construction Discipline) — the factory-chokepoint pattern; §3's table is implemented as canonical factories, never inline f-strings.
- ADR-0036 (node.id grammar v2) — sibling decision for `Symbol.id`; shares the v6 train.
- ADR-0037 (edge resolution semantics) — owns WI-jafat's read-side fix.
- v5 hash inputs: `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py:2333-2338`; typed-tier name gap: `analyze/base.py:770-775`; dependency factory: `analyze/base.py:591-598`; analogue factories: `analyze/base.py:581-588,632-639,642-649`; protocol factory: `analyze/base.py:652-686`; old 5% threshold: `packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py:897-904`.
- Strategy document: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` (identity-hash-coarseness family; Wave-2 T1 atomic train; gate G3).
