<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0015: Symbol.kind — WI-zipis producer-sweep drain

- Date: 2026-07-07
- Status: All RESOLVED — 7 values registered on `language_construct`, 1 folded to `struct`; producers migrated, ratchet baseline drained to zero.
- Closes: the WI-zipis producer-side axis-leak backlog for `Symbol.kind` (the 8 baselined values in `.ci/producer-axis-coherence-baseline.json`).
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md); the four-leakage-test from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Ninth audit-findings doc on the `Symbol.kind` axis declared by [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md).

## Context

Unlike the prior `Symbol.kind` clusters — which were carved out of the registry
by a top-down audit — these eight values were surfaced **bottom-up** by the
WI-zipis producer-side descend sweep (the trustworthy replacement for per-cohort
`validation_report` counting, per the INV-numat methodology ruling). Every one
of them was emitted by an analyzer through a **positional-helper or
nested-closure** emit site that the literal-grep diagnostics of ADR-0027's
Phase-1 seeding never saw — the same blind-spot class that hid `message`
(audit-findings 0007), `generic`, `rpc`/`service` (audit-findings 0013 /
WI-rilal), and `inductive`/`theorem` before them.

All eight are **language constructs** (what the symbol *is* in its source
language), not framework roles — so this audit is the mirror image of
audit-findings 0013 (Cluster 27D), which *folded* framework-role kinds. Here the
default is **CANONICAL** (register the genuine construct), with a single FOLD
where a canonical equivalent already exists.

## Methodology

Each value's producer emit site was located, the source-language construct
identified, checked against the already-registered `Symbol.kind` values for a
canonical equivalent, and run through the four leakage tests. The decisive test
throughout was **property derivability** (test a): a value FOLDs iff it is
recoverable from an existing registered kind plus the symbol's `language`
(i.e. it is a pure spelling variant); it REGISTERs iff it carries construct
information no existing kind conveys.

## Diagnostic findings

### 1. Seven genuine constructs with no canonical equivalent → CANONICAL

`extension` (Dart), `filter` / `workflow` (PowerShell), `operator` /
`assumption` (TLA+), and `list` / `map` (Smithy) each name a distinct
source-language construct with its own keyword / AST node and **no** registered
synonym to fold to. They follow the governing precedent for genuine distinct
constructs surfaced late by the same blind-spot: Common-Lisp `generic`
(`defgeneric`) and Zig `error_set` were **registered**, not folded.

### 2. The Smithy shapes are treated consistently

Smithy's shape analyzer already **reuses generic registered kinds** for its
shapes (`service`→`interface`+meta, `union`→`union`, `enum`→`enum`,
`simple_type`→`simple_type`, `resource`→`resource`, `operation`→`operation`).
`structure` follows that pattern by reusing the existing generic `struct` (its
description already absorbs "record-type"); it FOLDs. But `list` and `map` have
**no** generic collection/associative kind to reuse (`type` is too broad and
would erase the `union`/`enum` distinctions its siblings preserve; `struct` is a
product type, not an ordered/associative collection), so they REGISTER as
peer aggregate shapes to the already-registered `union`/`enum`. The split is
principled, not ad hoc: reuse when a canonical home exists, register when it
does not.

### 3. `structure` is shared by two producers

Both Smithy (`structure_statement`) and Lean 4 (`structure` — a record/product
type) emitted `kind="structure"`. The FOLD applies to **both** producers;
neither carries residue beyond `struct`. (Lean's distinct `class` typeclass and
`inductive` type remain separate — `inductive` was registered by
audit-findings 0007 precisely because indexed families carry residue over a
plain sum type; `structure` carries none over `struct`.)

## Verdicts

```yaml
kind: audit_verdicts
axis: Symbol.kind
verdicts:
  - value: extension
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"extension\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Dart `extension` declaration — a top-level construct adding members to an existing type. Peer to class/mixin/enum (own keyword + extension_declaration node); NOT a class (defines no new type) nor a mixin (distinct mixin_declaration node already maps to `mixin`). No canonical equivalent. Producer: dart.py, via a nested make_symbol positional helper. Property-derivability does not fire → REGISTER."
  - value: filter
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"filter\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "PowerShell `filter` — a named callable whose body is an implicit per-pipeline-object process block. Distinct source keyword (own tree-sitter node), sibling to function/workflow; kept distinct like subroutine/procedure/generic rather than folded to function. Producer: powershell.py (function_statement child `filter`)."
  - value: workflow
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"workflow\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "PowerShell `workflow` — a named callable compiled to Windows Workflow Foundation (checkpoint/suspend-resume/parallel semantics). Distinct keyword + AST node, sibling to function/filter; kept distinct rather than folded to function (same rationale as filter/generic). Producer: powershell.py (function_statement child `workflow`)."
  - value: operator
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"operator\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "TLA+ operator definition (`Op(x) == ...`) — the primary TLA+ definitional construct, walked alongside theorem/assumption. Producer: tlaplus.py operator_definition via a nested add_symbol helper (a literal-grep blind spot). No canonical equivalent."
  - value: assumption
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"assumption\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "TLA+ `ASSUME` declaration — a named top-level assumption/axiom, sibling to theorem/operator. Producer: tlaplus.py `assumption` node via nested add_symbol. Distinct construct, no canonical equivalent."
  - value: list
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"list\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Smithy `list` shape — a named ordered-collection type declaration. First-class aggregate shape, sibling to the already-registered smithy shapes union/enum/simple_type; no generic collection kind exists to fold to (`type` too broad, `struct`/`record` are product types). Producer: smithy.py list_statement -> _extract_shape(..., 'list', ...)."
  - value: map
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert any(s.name == \"map\" and s.axis == \"language_construct\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Smithy `map` shape — a named key->value associative-type declaration. Sibling aggregate shape to list/union/enum; not a `struct` (associative, not fixed named members) and no generic map/dict kind exists to fold to. Producer: smithy.py map_statement -> _extract_shape(..., 'map', ...)."
  - value: structure
    verdict: FOLD
    fold_target: struct
    status: RESOLVED
    diagnostic_test:
      cmd: "python3 -c 'from hypergumbo_core.symbol_kinds import SYMBOL_KINDS; assert not any(s.name == \"structure\" for s in SYMBOL_KINDS)'"
      expect: exit_code:0
    rationale: "Both Smithy (`structure_statement`) and Lean 4 (`structure`) emitted a named-field product type — a pure spelling variant of the registered `struct` (whose description already absorbs record-types). Property-derivability FIRES: `structure` = `struct` + `language`, zero residue. Folded at both producers (smithy.py, lean.py) to emit kind=`struct`; the WI-zipis ratchet enforces no producer re-introduces it. Consistency: smithy's sibling shapes all reuse generic registered kinds, so structure reuses `struct` rather than minting a synonym."
```

## Migration impact

- **Producer-side:** `dart.py` / `powershell.py` / `tlaplus.py` / `smithy.py` already emitted the seven CANONICAL values; registering them makes the emissions valid (no producer change). The FOLD changed two emit sites — `smithy.py` `structure_statement` and `lean.py` `structure` — from `"structure"` to `"struct"`.
- **Registry-side:** seven `SymbolKindSpec` rows added on `AXIS_LANGUAGE_CONSTRUCT`. `structure` is intentionally NOT registered.
- **Test-side:** four `kind == "structure"` assertions (test/BRANCHES for smithy + lean) updated to `"struct"`.
- **Schema-side:** `docs/schema.json` regenerated (seven values added to the open `Symbol.kind` enum). `.ci/schema-coverage-baseline.json` records the seven as registered-but-not-self-corpus-observed (their exercising fixtures are the per-analyzer tests, not hypergumbo's own Python source).
- **Gate-side:** `.ci/producer-axis-coherence-baseline.json` `Symbol.kind` list drained to `[]`; the producer-side ratchet is now green with an empty backlog (all three vocab axes clean).

## Related

- [ADR-0027](../adr/0027-symbol-kind-language-construct-only.md) — declares the `Symbol.kind` = language-construct axis this audit applies.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy + §"Fold-residue discipline".
- Audit-findings 0013 — Cluster 27D framework-ROLE folds (the *contrasting* class; WI-rilal executed its rpc/service tail via the same producer-sweep that surfaced these eight).
- Audit-findings 0007 — Cluster 27H domain long-tail CANONICAL promotions (`inductive`/`theorem`/`message`), the precedent for registering genuine constructs surfaced by a literal-grep blind spot.
