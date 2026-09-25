<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: `Edge.meta["call_construct"]` — the fold-residue key's own value family

- Date: 2026-09-10
- Status: 10 CANONICAL (RESOLVED, no migration needed) · 2 DEPRECATE-NO-FOLD (UNRESOLVED) · 1 axiom amendment proposed · 1 silent bug found · 2 adjacent leaks filed
- Trigger: the 72-commit conceptual-audit cadence. Suspect nominated after WI-dosuh (PR #903) added a value to this key and **no consumer read it**.
- Methodology: [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md), verdict trichotomy per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Audits the *fold-residue key* created by [audit-findings 0012](0012-evidence-type-cluster-d-call-construct.md).

## Why this is a survey, not an audit-findings document

It applies the audit trichotomy and produces a per-value verdict table, so by
shape it belongs in [`docs/audits/`](../audits/README.md). It cannot live there
yet, **and the reason is one of its own findings.**

`validate_against_registry` requires the declared axis to have a per-value
registry (`_REGISTRIES` in `audit_findings.py:126` knows exactly three:
`Edge.edge_type`, `Symbol.kind`, `Edge.evidence_type`). `call_construct` has a
registered *key* spec in `axis_meta_keys.py:305` but **no registry of its
values** — which is Diagnostic finding 3 below, and remedy 3 of WI-dapap. Filed
under `docs/audits/` the document fails
`test_audit_findings.py::test_live_tree_audit_findings_docs_parse_and_validate`
with *"declared axis has no registry mapping"*.

That failure is not an obstacle to route around; it is the format correctly
refusing to mechanically check verdicts it has no registry to check them
against. The verdict block below is kept in `kind: audit_verdicts` form so the
document can move to `docs/audits/` unchanged once the registry exists.

**Conversion trigger:** when WI-dapap lands the `call_construct` value
allowlist and registers the axis in `_REGISTRIES`, move this file to
`docs/audits/<NN>-call-construct-value-family.md`, add its README index row, and
let the mechanical check run.

## Context

`meta["call_construct"]` is not a native axis — it was **minted as a fold target**. [Audit-findings 0012](0012-evidence-type-cluster-d-call-construct.md) (2026-05-05) collapsed 28 `Edge.evidence_type` values (`method_call`, `function_call`, `constructor_call`, `pipe_call`, …) onto the `ast_call` apex and parked the surface-form distinction here. The key is registered in `axis_meta_keys.py:305` with the axiom:

> "Source-language call construct collapsed under `ast_call` apex (e.g. 'method', 'function', 'pipe', 'application')."

scoped by `applicable_edge_types=_CALL_FAMILY_EDGE_TYPES` (`calls` + `instantiates`) — a scope that is declared in code rather than prose *because a prose-only scope already drifted once* (WI-toruz).

**This audit asks a question audit 0012 could not**: four months on, does the parked vocabulary still name one thing? Fold-residue keys are structurally at risk — they are where the last audit put everything that did not fit, and nothing has audited *them*.

### The suspicion, in one sentence

> I think `call_construct` names a syntactic form in its axiom but functions as a single boolean gate in its consumers, and is accumulating values that name resolution locality, detection provenance, and inference pathway rather than source constructs.

**Falsifiable, and it half-failed.** Had the inventory shown a small vocabulary all read by consumers, or a value set uniformly construct-shaped, the audit would have returned a null result. The first clause is **confirmed**; the second is **mostly refuted** — 10 of 12 values are genuinely construct-shaped, and the field is in far better repair than the nomination assumed.

### A correction to the nomination

The nomination claimed the vocabulary was 4 values and that "nobody is currently looking at this field." **Both were wrong**, and the error is instructive: the initial grep matched only the `call_construct=` kwarg form and missed the `meta={"call_construct": …}` dict-literal form, which is where **126 of 140 emit sites live**. The field has a registry entry, a declared scope, a prior drift repair (WI-toruz), and a producer guard (INV-tadup). An inventory that greps one emit shape is not an inventory — this is Step 4.5's own lesson, arrived at the hard way.

## Step 2 — Inventory

140 emit sites, 12 distinct values, across both emit shapes:

| value | sites | languages | construct-shaped? |
| --- | --- | --- | --- |
| `method` | 65 | 14+ | yes |
| `function` | 41 | many | yes |
| `constructor` | 6 | dart, csharp, ruby | yes |
| `protocol` | 3 | python | yes |
| `application` | 3 | haskell, ocaml | yes |
| `remote` | 2 | erlang | yes |
| `pipe` | 2 | elixir | yes |
| `local` | 1 | erlang | yes |
| `monadic_action` | 1 | haskell | yes |
| `assignment` | 1 | javascript | **yes, but not a *call*** |
| `macro` | 1 | erlang | **no — inference pathway** |
| `macro_body` | 1 | rust | **no — detection provenance** |

## Step 3/4 — Diagnostic findings

### 1. The axiom says taxonomy; the consumers implement one boolean

**15 comparison sites** across `verify_claims.py` (8), `io_boundary.py` (4) and `taint.py` (3). **Every one tests `== "method"` or `!= "method"`.** Ten of the twelve values are never branched on anywhere.

This is Test 4 (mechanism vs. category) at the *field* level: the declared concept is "which source construct", the operational concept is "is this an untyped method call I must withhold?". `py.py:7051` states the collapse outright — *"every consumer of this disclosure filters on `method` because that is what 'a receiver was there' means."*

**This is not automatically a defect.** A disclosure key may legitimately be descriptive, read by humans and by `explain`/`survey` output rather than branched on. The finding is that the field's *name and axiom* promise a taxonomy while its *load-bearing* content is one bit, and nothing in the codebase records which of the two it is meant to be. That ambiguity is what let values 11 and 12 in.

### 2. SILENT BUG — `call_construct` is membership-tested against the catalogue `kind` vocabulary

`verify_claims.py:3908`, in the method-starvation gate:

```python
construct = (edge.get("meta") or {}).get("call_construct")
if construct and construct in module_kinds[language].get(module, set()):
```

`module_kinds` is built from `catalog.primitives` (`verify_claims.py:3850-3857`) and therefore holds **`IoPrimitive.kind` values, whose vocabulary is exactly `{"function", "method"}`** (`io_boundary.py:633`). A `call_construct` value is being tested for membership in a **different axis's** value set. It works only because two tokens happen to coincide.

Consequence: for the ten remaining values the first satisfaction route **can never fire**. Every elixir `pipe`, erlang `local`/`remote`, haskell `application`/`monadic_action`, ocaml `application`, rust `macro_body`, dart/csharp `constructor` and javascript `assignment` call falls through to the second route regardless of what the module declares. There is no error and no log line — the gate silently degrades for eight languages.

The surrounding comment shows the author reasoning in `{function, method}` terms throughout ("a module declaring ONLY methods still cannot be satisfied by a function-construct call"), so the two-value assumption is explicit in the prose and simply untrue of the field. This is Test 3 (construct vs. relationship) firing across a field boundary, and it is the audit's one concrete silent bug.

### 3. The producer guard is a DENYLIST, so new leakers ship unreviewed

`test_axis_meta_keys.py:411` pins `_FOLDED_CALL_CONSTRUCTS` — four values previously ejected from this field, with a positive control proving the walk fires. The comments are a record of **three separate axes** having leaked in before:

| ejected value | what it actually named |
| --- | --- |
| `remote_external`, `application_external` | resolution **status** |
| `chained_return_type` | resolution **mechanism** (now on `resolution_quality`) |
| `interface_dispatch` | inference **pathway** (already on `evidence_type`) |

INV-tadup pins the refuted values, which is the right guard for regression — but a denylist cannot stop a value it has never seen. **`assignment` was added on 2026-09-10 and passed every gate in the repository without any review of whether it belongs on this axis.** That is not hypothetical: it is this audit's own trigger.

### 4. `macro` repeats the exact pattern `interface_dispatch` was ejected for

`erlang.py:562` emits `evidence_type="macro_expansion"` and `meta={"call_construct": "macro"}` **on the same edge**, with a comment explaining that the evidence type is what a consumer reads to tell a seen call from an inferred one. The construct key then restates it.

Worse for the axiom: at `?LOG_INFO(...)` **tree-sitter expanded nothing** — the edge is inferred from a macro name plus an include. There is no observed source call construct to name. The axiom asks "which source-language call construct", and at this site the honest answer is "none was parsed".

That is precisely the ejection rationale recorded for `interface_dispatch`: *"an inference PATHWAY the same edge already carried as `evidence_type` — one value stated twice on two different axes."*

### 5. `macro_body` names where the call was found, not what it is

`rust.py:3123` walks a macro's `token_tree` via `_extract_macro_call_names` and emits a `calls` edge for each name found. The callee may be `Self::foo`, a `::`-qualified path, or a bare name — so the construct genuinely **varies per site**, and the one thing `macro_body` reliably reports is *the detector that found it*. Test 4 (mechanism vs. category) fires cleanly: this is a "how it was detected", which the playbook says "almost always belongs in metadata, not in the type".

There is no uniform fold target, because the underlying construct differs per emit site.

### 6. `assignment` is construct-shaped but is not a *call* — the axiom needs one word

Self-implicating finding, audited hardest for that reason. `js_ts.py:5985` stamps `assignment` for `ws.onmessage = h` — a property assignment that registers a handler, modelled as a `calls` edge so the catalogue can match it.

Is it on-axis? The value names a real, observed, source-language construct (`assignment_expression`), which is what the field exists to record, and it is the *most* informative thing a consumer could read there. But the axiom's words are "source-language **call** construct", and an assignment is definitionally not a call.

Two readings, and the choice matters for the next value:
- **(a) The value is off-axis.** Strict reading; would eject a value that correctly describes its site and leave the site with no disclosure at all.
- **(b) The axiom is one word too narrow.** The field's real job — visible in what its 12 values do — is *the source construct that produced this call-family edge*, which includes non-call constructs that register a callee.

This audit takes **(b)**, and the deciding argument is `constructor`: the registry's own defence of it (INV-kahig — ruby emits `calls` where dart/csharp emit `instantiates` for the same construct) already establishes that this key describes **the construct that produced a call-family edge**, not "the syntax of a call". `assignment` is the same shape one step further out. The axiom should be amended to match what it has always meant; the value is CANONICAL under the amended wording, and would be off-axis under the unamended one — so the amendment is load-bearing, not cosmetic.

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.meta["call_construct"]
verdicts:
  - value: method
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"method\"' -- packages/*/src"
      expect: nonempty
    rationale: "The apex construct and the only value any consumer branches on; 65 emit sites across 14+ languages."
  - value: function
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"function\"' -- packages/*/src"
      expect: nonempty
    rationale: "Free-function call; the construct peer of method. 41 emit sites."
  - value: constructor
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"constructor\"' -- packages/*/src"
      expect: nonempty
    rationale: "NOT redundant with edge_type: ruby emits calls where dart/csharp emit instantiates for the same source construct (INV-kahig), so this is the only cross-language invariant for object creation."
  - value: protocol
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c 'call_construct.*protocol' -- packages/hypergumbo-lang-mainstream/src"
      expect: nonempty
    rationale: "Python implicit protocol invocation (a for-loop invoking __iter__). A genuine construct with no receiver expression, which is why consumers filtering on method correctly exclude it."
  - value: application
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"application\"' -- packages/*/src"
      expect: nonempty
    rationale: "Functional-language juxtaposition application (haskell, ocaml). Distinct source construct, not a flavour of function."
  - value: pipe
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"pipe\"' -- packages/*/src"
      expect: nonempty
    rationale: "Elixir |> — the callee's first argument is supplied by the pipeline, which is a real syntactic difference at the call site."
  - value: remote
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"remote\"' -- packages/hypergumbo-lang-common/src/hypergumbo_lang_common/erlang.py"
      expect: nonempty
    rationale: "Erlang qualified call Mod:Fun() is a distinct syntactic form from Fun(), not a resolution outcome. Its suffixed sibling remote_external WAS ejected for naming resolution status; the bare form survives that test and the distinction is worth keeping visible."
  - value: local
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c '\"call_construct\": \"local\"' -- packages/hypergumbo-lang-common/src/hypergumbo_lang_common/erlang.py"
      expect: nonempty
    rationale: "Erlang unqualified call Fun(); the syntactic peer of remote. Named for the source form, not for where the callee was found."
  - value: monadic_action
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c 'call_construct.*monadic_action' -- packages/hypergumbo-lang-common/src"
      expect: nonempty
    rationale: "Haskell bare identifier in an executed do-position (INV-fofoj). A zero-argument action is executed with no application syntax at all, so no other construct value describes it."
  - value: assignment
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -c 'call_construct=\"assignment\"' -- packages/hypergumbo-lang-mainstream/src"
      expect: nonempty
    rationale: "WI-dosuh. A handler registration by property assignment; a real source construct producing a call-family edge. CANONICAL only under the amended axiom (see Action 1) — under the literal current wording 'source-language CALL construct' it would be off-axis, which is why the amendment ships with this verdict rather than after it."
  - value: macro
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -n '\"call_construct\": \"macro\"' -- packages/hypergumbo-lang-common/src/hypergumbo_lang_common/erlang.py"
      expect: empty
    rationale: "erlang.py:562 emits evidence_type=macro_expansion on the SAME edge; call_construct=macro restates the inference pathway on a second axis — the exact rationale recorded for ejecting interface_dispatch. tree-sitter expanded nothing at the site, so there is no observed source call construct to name. No fold target: the fact already has a home on evidence_type. Zero consumers read it."
  - value: macro_body
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -n '\"call_construct\": \"macro_body\"' -- packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py"
      expect: empty
    rationale: "rust.py:3123 names WHERE the call was found (inside a macro token tree), not what it is — Test 4, mechanism vs category. No uniform fold target exists because _extract_macro_call_names yields Self::-qualified, ::-qualified and bare callees, so the true construct varies per site. Producer should stamp the real construct or stamp nothing. Zero consumers read it."
```

## Step 5 — Adjacent concept sweep

Audit 0012 created three sibling fold-residue keys in the same migration. Both non-trivial ones leak, and neither has a guard of any kind:

### `meta["receiver"]` — CONFIRMED LEAK (9 values, 1 consumer read)

Two axes in one field:

- **syntactic shape of the receiver expression**: `qualified`, `field_chain`, `bare`
- **resolution outcome for the receiver**: `typed_var`, `typed_field`, `stdlib`, `generic`, `external`, `constant_external`

`external` is a resolution status — **the very thing `remote_external` and `application_external` were ejected from `call_construct` for naming**. The leak was folded out of one sibling key and is sitting live in the other. `constant_external` packs two facts into one token. Filed as **WI-mujug**.

### `meta["resolution_quality"]` — MINOR, document only (6 values, 3 consumer reads)

`typed` and `typed_receiver` are near-synonyms with no recorded boundary; `recovery` and `chained_return_type` name mechanisms while `ambiguous` and `type_inferred` name quality. Milder than `receiver` because a mechanism arguably *is* the quality signal here, and ADR-0053 recently made this key load-bearing for a disclosure. Needs a documented boundary, not a fold. Filed as **WI-mujug** (same item — one sweep, one remedy).

### `meta["visibility"]` — NULL RESULT

One value (`unexported`), two consumer reads, unambiguous. No action. Recorded so the next auditor does not re-derive it.

## Action

1. **Amend the `call_construct` axiom** in `axis_meta_keys.py:305-306` from "Source-language call construct" to "Source-language construct that produced this call-family edge", matching what `constructor` (INV-kahig) and `assignment` (WI-dosuh) already do. One sentence; no value migration. → **WI-dapap**
2. **Fix the cross-axis membership test** at `verify_claims.py:3908` so the starvation gate stops silently excluding ten of twelve values across eight languages. → **WI-dapap**
3. **Turn INV-tadup's denylist into an allowlist** so a new `call_construct` value must be declared before it can ship — the gate that would have caught `assignment` at review time instead of four months later. → **WI-dapap**
4. **Migrate the two DEPRECATE-NO-FOLD values** (`macro`, `macro_body`). Zero consumers read either, so the migration is producer-only and cannot change a verdict. → **WI-dapap**
5. **Adjacent sweep remedies** for `receiver` and `resolution_quality`. → **WI-mujug**

## Self-test

- [x] One-sentence suspicion written before the inventory
- [x] Falsifiable — and half-refuted: 10 of 12 values survived, and the nomination's own value count and "nobody is looking at it" claim were both wrong
- [x] All values inventoried, across **both** emit shapes (the first pass missed 126 of 140 sites)
- [x] Four leakage tests applied per candidate pair
- [x] Step 4.5 producer trace: every one of the 12 values traced to a file:line producer; **no verdict rests on "no producer exists"**, so no DEPRECATE-NO-FOLD here carries dead-vocabulary risk. Shapes checked: literal kwarg, dict-literal value, assignment-to-Name (`haskell.py:504`), f-string (zero hits), dict-subscript-target (zero hits)
- [x] Silent bug found with file:line (`verify_claims.py:3908`)
- [x] Three adjacent concepts swept (`receiver`, `resolution_quality`, `visibility`), one null result recorded
- [x] Findings durable: this document + tracker items
