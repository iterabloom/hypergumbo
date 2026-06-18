<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0034: ID-Construction Discipline

- Status: **Accepted**
- Date: 2026-06-01
- Supersedes: —
- Superseded by: Partially superseded by ADR-0037 (Out-of-scope `:unresolved` blessing), ADR-0036 (reviewer-checklist item 5 correction); core factory-discipline decision in force
- Related: ADR-0014 (Typed stable_id factories — the family of `make_*_stable_id` factories whose discipline this ADR generalizes to `Symbol.id` construction), ADR-0033 (Spec-vs-Data Validator Stage — the runtime enforcement substrate; this ADR's policy is enforced by ADR-0033's `id_format` validator class), ADR-0031 (Symbol.language reshape — its Class B "synthetic stand-in" classification clarifies which language string belongs in the canonical ID's first segment for linker-emitted Symbols); tracker items INV-sadiv (218 call_site nodes emitted with ad-hoc `<path>::<role>::<line>` IDs — closed by this ADR's six-site migration), INV-dulah (META: ID-format escapes), INV-hunup (META: stable_id multiplicity — out of scope here, addressed by Phase 6 PR1).

> **Amendment (2026-06-11, per the 2026-06-10 design interview — ADRs 0035–0042, PR #4181):** Two corrections. (a) §"Out of scope" blesses `make_unresolved_edge` and the `:unresolved` dst shape it stamps; ADR-0037 ruling 4 retires that shape (the `unresolved` kind-slot token folds into `external_symbol`), and the Edge-ID validation question this ADR deferred to "a future ADR" is taken up by ADR-0036/ADR-0037. (b) Reviewer-checklist item 5's claim that the `id_format` validator "pairs `Symbol.id`'s trailing segment with `Symbol.kind`" was inaccurate — ADR-0036 Evidence #2 establishes the shipped gate was shape-only and never round-tripped slot values against `Symbol.name`/`Symbol.kind`; ADR-0036's round-trip checks (Ruling 2, enforcement layer 2) are the fix.

## Context

### The architectural absence

`Symbol.id` is the primary identity field on every Symbol. The canonical schema is documented at `packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py:288` as:

```
<language>:<path>:<start_line>-<end_line>:<name>:<kind>
```

Single-colon separators, exactly five segments, lowercase identifier for the `language` and `kind` segments, `<digit>+-<digit>+` shape for the span segment. Ten language analyzers — `go.py`, `rust.py`, `java.py`, `csharp.py`, `php.py`, `js_ts.py`, `ruby.py`, `kotlin.py`, `swift.py`, `py.py` — use the `make_symbol_id(...)` factory at every Symbol emit site to produce IDs conforming to this schema.

But six linker passes did NOT use the factory. They constructed IDs by f-string, with their own ad-hoc schema:

```python
# linkers/http.py:1324 (pre-migration)
id=f"{rel_path}::http_client::{call.line}",
# Produces e.g. "packages/foo/bar.py::http_client::42"
```

This produces three structural deviations from the canonical schema simultaneously:

1. **Path-prefix instead of language-prefix.** The first segment is the file path, not the language. Cross-language edge detection that splits the ID by `:` and reads the first segment as the language now reads `packages/foo/bar.py` and treats every linker-emitted call_site as if it were in a brand-new language. INV-sadiv documents 50 such spurious-language detections from 218 affected nodes.
2. **Double-colon separators.** The schema requires single-colon separators. Tooling that splits the ID on `:` and counts segments now finds either too many segments (when path contains no colon, the double-colon produces empty segments between them) or a different decomposition than the canonical analyzer-emitted shape.
3. **Wrong segment count.** The canonical schema has five segments; the linker shape has three.

The result was a **silent schema bifurcation** between analyzer Symbols (canonical) and linker call_site Symbols (ad-hoc). The bifurcation went undetected for many bakeoff cycles because no runtime validator scanned the emitted ID corpus for shape conformance.

### The lab-notebook posture

The id-construction-discipline lab-notebook entry at `~/hypergumbo_lab_notebook/id-construction-discipline-05312026.md` documented the problem and proposed a reviewer-time checklist:

> Before merging any PR that adds a `Symbol(id=...)` line: confirm the ID is built via one of the canonical factories in `analyze/base.py` (lines 288-309). If you're constructing an ID by f-string, stop and ask whether a factory exists for the shape you need. If no factory fits, the right move is to add a factory, not to invent a new shape.

The lab-notebook explicitly named the limit of this posture:

> This is a manual version of what a validator stage would do automatically. A reviewer can miss an f-string-constructed ID in the diff; a validator class scanning every Symbol.id at runtime cannot.

ADR-0033 introduced the spec-vs-data validator stage. With that infrastructure in place, the runtime check that the lab-notebook called for became implementable.

## Decision

Adopt **canonical-factory discipline** for `Symbol.id` construction as policy, enforced by the `id_format` validator class introduced in Phase 5 PR1 of the ADR-0033 campaign.

### The rule

**Every `Symbol(id=...)` site MUST use one of the canonical factories at `analyze/base.py:288-309`.** Specifically:

- `make_symbol_id(lang, path, start_line, end_line, name, kind)` — primary factory for analyzer- and linker-emitted Symbols.
- `make_file_id(lang, path)` — file pseudo-symbols; equivalent to `make_symbol_id(lang, path, 1, 1, "file", "file")`.

F-string construction of `Symbol.id` is **prohibited**. The validator class flags any non-canonical shape, and the policy violation surfaces in the `validation_report` section of the behavior-map artifact.

### Choice of language string for synthetic stand-ins

Linker passes that emit call_site Symbols frequently produce ADR-0031 Class B "synthetic stand-ins" — Symbols representing an externalized protocol call (HTTP client, database query, message-queue publisher) rather than a real source declaration. Class B Symbols carry `Symbol.language = None` because the synthetic isn't *in* a host language at runtime.

For the canonical-ID's first segment, the **`discovery_language`** of the host file (where the protocol call was detected) is the correct choice. The discovery_language is always a real language string — the language of the file in which the linker found the call. This keeps the canonical first segment a parseable language even when `Symbol.language` itself is `None`.

```python
# Pattern for linker-emitted call_site Symbols
return Symbol(
    id=make_symbol_id(
        call.language,           # host file's language — always a real string
        str(rel_path),
        call.line,
        call.line,
        "http_client",           # the framework role / call shape
        "call_site",
    ),
    # ADR-0031 Class B: synthetic stand-in.
    language=None,
    discovery_language=call.language,
    protocol_origin="http",
    # ...
)
```

### Reviewer checklist (when adding a new emit site)

1. Is this a `Symbol(id=...)` construction? If no, this ADR doesn't apply. If yes, continue.
2. Does the line read `id=make_symbol_id(...)` or `id=make_file_id(...)`? If yes, you're conforming.
3. If the line reads `id=f"..."` or `id="..." + something`, **stop**. Replace with the appropriate factory call.
4. For Class B synthetic stand-ins: confirm the factory's first argument is the host's `discovery_language`, not the (`None`) `Symbol.language`.
5. Confirm the `kind` argument (5th positional) is in the `symbol_kinds` registry — the `id_format` validator pairs `Symbol.id`'s trailing segment with `Symbol.kind`, and cross-axis coherence is enforced separately by `axis_conformance`. <!-- corrected/retired — see amendment (ADR-0036 Evidence #2: the shipped `id_format` gate was shape-only and never round-tripped slot values against `Symbol.name`/`Symbol.kind`; ADR-0036 Ruling 2 / enforcement layer 2 round-trip checks are the fix) -->

### Out of scope

This ADR addresses only `Symbol.id` construction. Three adjacent identity-discipline gaps remain open and are tracked separately:

- **`Symbol.stable_id` format and multiplicity** (INV-hunup, INV-bazij) — Phase 6 PR1 / PR3 territory. The `stable_id` field has its own schema (`sha256:<16hex>` and family-specific factory outputs) and its own validator class (Phase 6).
- **`Edge.id` format** — Edge IDs are addressed by a separate canonical factory at `analyze/base.py:make_unresolved_edge` and have a different shape constraint. A future ADR (Phase 6 PR1 sibling) extends the validator to Edge IDs. <!-- corrected/retired — see amendment (ADR-0037 Ruling 4 retires the `:unresolved` dst shape this bullet blesses: the `unresolved` kind-slot token folds into `external_symbol`; the deferred "future ADR" Edge-ID validation question is taken up by ADR-0036/ADR-0037) -->
- **Stable-id collision counting** (INV-bazij P0, 60% collision rate) — Phase 6 PR3 territory. Collision detection is a separate validator concern (cross-record coherence), not a per-record shape check.

## Enforcement

### Runtime check (Phase 5 PR1 — landed)

The `id_format` validator class at `packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py:_check_id_format` is the runtime enforcement. It iterates every Symbol in the emitted IR, applies the canonical regex, and produces a structured `ValidationViolation` for each non-conforming ID. The violation message includes a problem-category tag (`double_colon_separator (INV-sadiv)` / `wrong_field_count` / `non_canonical_language_prefix` / `malformed_span_segment` / `non_canonical_kind_suffix`) so operators can quickly diagnose which emit site needs migration.

### Static check — known gap

There is **no static-AST check today** that flags `id=f"..."` patterns in source. A future static linter (analogous to `multi_value_field_axis.py`'s static enforcement of the `# axis:` annotation) could scan for f-string ID construction at PR-review time. Until that lands, the discipline relies on the runtime validator + reviewer awareness.

### Reviewer-time check (this ADR's reviewer checklist)

The checklist above is the documented manual gate. Until the static-AST companion lands, reviewers are the first line of defense for new emit sites; the runtime validator is the second.

## Implementation status

- **Phase 5 PR1 (landed)**: `id_format` validator class shipped; six INV-sadiv linker sites migrated to `make_symbol_id`:
  - `linkers/http.py:1324` (http_client call_site)
  - `linkers/database_query.py:351` (db_query call_site)
  - `linkers/subprocess_cli.py:329` (subprocess_call call_site)
  - `linkers/message_queue.py:417` (mq_publisher / mq_subscriber function)
  - `linkers/graphql_resolver.py:430` (resolver function)
  - `linkers/graphql.py:208` (graphql_client function)
- **Phase 5 PR2 (this ADR)**: policy documentation.
- **Phase 6 PR1 (planned)**: extend the validator to `Symbol.stable_id` format, address INV-hunup multiplicity and INV-dulah escapes via the same validator-driven cleanup model.

## Consequences

### Positive

- **Schema bifurcation closed** at the analyzer ↔ linker boundary. Both producers now emit IDs conforming to the same five-segment canonical shape.
- **Cross-language edge detection regains correctness**. The 50 spurious-language detections in the dogfood corpus 20260528 cease because the linker-emitted call_site IDs no longer parse as `language=packages/...`.
- **Discipline travels.** Future linker passes that emit Symbols (new framework support, new protocol detection) cannot drift back into f-string construction without the validator surfacing the violation immediately.
- **Reviewer load shifts.** What was an unreliable manual checklist becomes a CI-gateable structural property. Reviewers can stop scanning for `id=f"..."` patterns; the validator does it.

### Negative

- **Static-AST gap remains** until the f-string-id pattern detector lands. A reviewer who reads a `id=f"..."` line in a diff still has to flag it manually; the validator only catches it after the PR is merged and the corpus is re-analyzed.
- **Per-PR overhead at write time.** Every `Symbol(id=...)` site now requires the reviewer to verify the factory is used. The reviewer-checklist friction is small but non-zero.

### Neutral

- **No artifact-shape change.** The canonical schema was already the documented standard; INV-sadiv was a population-side bug, not a schema change. Downstream consumers that already parse canonical IDs see no difference.
- **No SCHEMA_VERSION bump.** The IR dataclass shapes are unchanged; only the runtime enforcement surface is new.

## References

- ADR-0014 (Typed stable_id factories) — the parallel discipline for `Symbol.stable_id`.
- ADR-0031 §"Class B synthetic stand-ins" — clarifies the language-string choice for linker-emitted Symbols.
- ADR-0033 §"Validator classes" — the four-class catalog this ADR's `id_format` class extends to five.
- Lab notebook `id-construction-discipline-05312026.md` — the original posture document.
- Tracker INV-sadiv — the violated invariant this ADR's runtime enforcement closes.
