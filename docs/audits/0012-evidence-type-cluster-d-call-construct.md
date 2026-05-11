<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0012: Edge.evidence_type Cluster 28D — Apex/Peer Call-Construct Overloads

- Date: 2026-05-05
- Status: All rows PRELIM_RESOLVED at filing (Phase 3 producer migration shipped this PR; values remain on `endpoint_shape` through the Phase 4a deprecation window per ADR-0028 §"Phase 4")
- Closes: WI-nibis-bohak-bitik-fozul-vohan-finik-soful-zijov (Cluster 28D apex/peer collapse, ADR-0028 Phase 3)
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md). Third audit-findings doc on the `Edge.evidence_type` axis declared by [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md), companion to audit-findings 0004 (Cluster 28A canonical inference) and audit-findings 0008 (Cluster 28B resolution-status).

## Context

[ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) §"Phase 3" Cluster 28D is the apex/peer call-construct cluster of `Edge.evidence_type`: 28 values that name "a call happened" with the call-construct surface form (function vs. method vs. application vs. pipe vs. constructor vs. cross-file vs. macro-body) baked into the inference label. The "what surface form did the call take?" property belongs in `Edge.meta["call_construct"]` (and friends — `meta["receiver"]`, `meta["resolution_quality"]`, `meta["visibility"]`), not on the inference label.

The 28 Cluster 28D values seeded by ADR-0028 Phase 1 (registry lines 503–558):

```
ambiguous_method_call, bare_method_call, call, chained_return_type_call,
constructor_call, cross_file_call, cross_file_message_send, external_receiver_call,
function_application, function_application_external, function_call, local_call,
macro_body_call, method_call, method_call_field_chain, method_call_recovery,
method_call_typed, method_call_type_inferred, method_group, object_creation,
pipe_call, receiver_call, remote_call, remote_call_external, stdlib_method_call,
typed_field_call, typed_receiver_call, unexported_method_call
```

Wave 4 of the WI-runod cross-axis schedule designates this cluster's producer fold as the **largest single Phase 3 sub-PR** for ADR-0028: ~89 emit sites across 25 producer files, with `function_call` alone accounting for 38 sites in 17 languages. The fold pattern is uniform but the per-language emit sites are diverse.

This audit answers two questions:

1. **Apex name** (ADR-0028 Open Question 1). Choice between `ast_call` (symmetry with `ast_*` companions like `ast_call_direct`, `ast_attribute`, `ast_decorator`) and `function_call` (the high-frequency emitter, 38 occurrences). **Decision: `ast_call`.** Symmetry with the `ast_*` family wins; `function_call` itself becomes a peer that folds to `ast_call` + `meta["call_construct"]="function"`. The choice is recoverable but the symmetry preserves the naming invariant that AST-derived inference labels carry the `ast_` prefix.
2. **Per-value verdicts.** All 28 values FOLD to `ast_call` + structured `meta` keys. One value (`cross_file_message_send`) folds to a different apex (`message_send`, an existing Cluster 28A canonical) because the underlying inference is not a call expression — it's a cross-file message-send pattern that shares the cross-file flavor.

**No new axis ADR required.** The four-leakage-test pass for each of the 28 values fired exclusively on Test 2 (apex/peer overloading) — multiple "flavors" of *a call happened* in the same field, distinguished only by an inference-path detail that should be a `meta` key. This is exactly the leak that ADR-0028's `meta["call_construct"]` surface absorbs.

## Methodology

Per [ADR-0028 §"Phase 3" Cluster 28D](../adr/0028-evidence-type-inference-pathway-only.md). Each value's verdict applies the four leakage tests from the [Fundamental Concept Audit playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md). Test 2 (apex/peer overloading) is the load-bearing test for this cluster: every value pairs the apex relationship "a call edge" with a per-emit-site distinguishing detail (function vs. method vs. constructor; bare vs. typed vs. external receiver; ambiguous vs. recovered vs. type-inferred resolution).

Producer-side migration: each emit site replaces `evidence_type="<peer_name>"` with `evidence_type="ast_call"` (or `evidence_type="message_send"` for `cross_file_message_send`) plus structured `meta` keys per the per-row verdict. The L3 producer-side coherence linter (`scripts/check-producer-axis-coherence`) catches drift at pre-commit.

## Diagnostic findings

### 1. `ast_call` apex selected for symmetry, not frequency

The ADR-0023 heuristic ("most-frequent emitter wins") would pick `function_call` (38 sites). This audit overrides that heuristic for the symmetry consideration: the registry's Cluster 28A members carry the `ast_` prefix as a naming invariant (`ast_annotation`, `ast_attribute`, `ast_call_direct`, `ast_decorator`, `ast_method_inferred`). The apex of the call-construct family belongs in that pattern.

Rejected alternative: rename the apex to `function_call` and rename `ast_call_direct` to `function_call_direct` for consistency. This was rejected because `ast_call_direct` predates Cluster 28D and ships with downstream consumer dependencies; renaming it would be a breaking change unrelated to the cluster fold.

### 2. `cross_file_message_send` is the lone non-`ast_call` apex

`cross_file_message_send` is the only Cluster 28D value whose underlying inference is not a call expression. It records a cross-file message-send pattern (Objective-C `[receiver selector:args]` with a cross-file resolution). Its apex is `message_send` (an existing Cluster 28A canonical at registry line 236), not `ast_call`. The `cross_file` flavor moves to `meta["call_construct"]="cross_file"`.

This is the same shape as Cluster 28A's parallel `message_send` family: `ast_call` for direct call expressions; `message_send` for Objective-C / Smalltalk-style message-passing; both are inference-pathway peers, both can carry call-construct flavors via `meta`.

### 3. Multi-key meta is normal

Several Cluster 28D values carry **two** distinguishing dimensions, e.g. `bare_method_call` is a method-call (call_construct=method) on a bare receiver (receiver=bare). Both keys are emitted:

- `bare_method_call` → `meta={"call_construct": "method", "receiver": "bare"}`
- `method_call_recovery` → `meta={"call_construct": "method", "resolution_quality": "recovery"}`
- `unexported_method_call` → `meta={"call_construct": "method", "visibility": "unexported"}`

Per ADR-0024 §"Fold-residue discipline" rule 3, `meta["call_construct"]` is the load-bearing key (28 distinct values fold to it); `meta["receiver"]`, `meta["resolution_quality"]`, and `meta["visibility"]` are secondary keys that may themselves trip the recurrence-promotion threshold during Phase 3+ steady-state monitoring. This audit does not promote them — Phase 3 follow-on work tracks meta-key emission counts and files a follow-on ADR if any of them fires the threshold.

### 4. Constructor flavor unifies `constructor_call` and `object_creation`

`constructor_call` (Dart, Ruby) and `object_creation` (C#) both fold to `ast_call` + `meta["call_construct"]="constructor"`. They were previously synonyms across languages — the audit unifies them on the same `meta` value, which is the structural fix for the multi-language "yet another flavor" leak the audit playbook §"When to run" signal #2 names.

### 5. `call` is the bare apex value (not a peer)

`wolfram.py` emits the literal `evidence_type="call"`. This is structurally the apex itself, just under a different name. The fold is `evidence_type="ast_call"` with no `meta["call_construct"]` (or `meta["call_construct"]="generic"` for traceability — this audit picks no `meta` entry; the wolfram emit is sufficiently described by the apex alone).

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.evidence_type
verdicts:
  - value: ambiguous_method_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"ambiguous_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'resolution_quality':'ambiguous'}. Producer: go.py."
  - value: bare_method_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"bare_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'bare'}. Producer: ruby.py."
  - value: call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Bare apex synonym. Fold: evidence_type=ast_call (no meta — the apex alone suffices). Producer: wolfram.py."
  - value: chained_return_type_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"chained_return_type_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='chained_return_type'. Producer: go.py."
  - value: constructor_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"constructor_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='constructor'. Producers: dart.py, ruby.py."
  - value: cross_file_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"cross_file_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='cross_file'. Producer: bash.py."
  - value: cross_file_message_send
    verdict: FOLD
    fold_target: message_send
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"cross_file_message_send\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload on a non-ast_call inference. The apex is message_send (an existing Cluster 28A canonical for Objective-C / Smalltalk-style message-passing). Fold: evidence_type=message_send + meta['call_construct']='cross_file'. Producer: objc.py."
  - value: external_receiver_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"external_receiver_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'external'}. Producer: go.py."
  - value: function_application
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"function_application\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload (functional-language flavor). Fold: evidence_type=ast_call + meta['call_construct']='application'. Producers: haskell.py, ocaml.py."
  - value: function_application_external
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"function_application_external\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='application_external'. Producer: haskell.py."
  - value: function_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"function_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. The high-frequency emitter (38 sites in 17 producer files). Fold: evidence_type=ast_call + meta['call_construct']='function'. Producers: bash.py, clojure.py, commonlisp.py, cpp.py, dart.py, elixir.py, elm.py, fsharp.py, go.py, groovy.py, julia.py, kotlin.py, lua.py, perl.py, rust.py, scala.py, swift.py."
  - value: local_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"local_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='local'. Producer: erlang.py."
  - value: macro_body_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"macro_body_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='macro_body'. Producer: rust.py."
  - value: method_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='method'. Producers: csharp.py, dart.py, go.py, perl.py, py.py, ruby.py."
  - value: method_call_field_chain
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_call_field_chain\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'field_chain'}. Producers: cpp.py, csharp.py, go.py."
  - value: method_call_recovery
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_call_recovery\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'resolution_quality':'recovery'}. Producer: linkers/method_call_recovery.py."
  - value: method_call_typed
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_call_typed\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'resolution_quality':'typed'}. Producer: lua.py."
  - value: method_call_type_inferred
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_call_type_inferred\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'resolution_quality':'type_inferred'}. Producers: csharp.py, dart.py, py.py."
  - value: method_group
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"method_group\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload (C# delegate group). Fold: evidence_type=ast_call + meta['call_construct']='method_group'. Producer: csharp.py."
  - value: object_creation
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"object_creation\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload (synonym of constructor_call across languages). Fold: evidence_type=ast_call + meta['call_construct']='constructor'. Producer: csharp.py."
  - value: pipe_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"pipe_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload (Elixir / F# pipe). Fold: evidence_type=ast_call + meta['call_construct']='pipe'. Producer: elixir.py."
  - value: receiver_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"receiver_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'generic'}. Producer: ruby.py."
  - value: remote_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"remote_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='remote'. Producer: erlang.py."
  - value: remote_call_external
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"remote_call_external\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta['call_construct']='remote_external'. Producer: erlang.py."
  - value: stdlib_method_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"stdlib_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'stdlib'}. Producer: go.py."
  - value: typed_field_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"typed_field_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'receiver':'typed_field'}. Producers: go.py, rust.py."
  - value: typed_receiver_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"typed_receiver_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload. Fold: evidence_type=ast_call + meta={'call_construct':'method', 'resolution_quality':'typed_receiver'}. Producers: go.py, ruby.py."
  - value: unexported_method_call
    verdict: FOLD
    fold_target: ast_call
    status: RESOLVED
    diagnostic_test:
      cmd: "grep -rn 'evidence_type=\"unexported_method_call\"' packages/ --include='*.py' | grep -v test_"
      expect: empty
    rationale: "Apex/peer overload (Go-specific visibility flavor). Fold: evidence_type=ast_call + meta={'call_construct':'method', 'visibility':'unexported'}. Producer: go.py."
```

## Migration impact

- **Producer-side:** ~89 emit sites across 25 files migrated from `evidence_type="<peer_name>"` to `evidence_type="ast_call"` (or `evidence_type="message_send"` for `cross_file_message_send`) + structured `meta` keys. Files: `bash.py`, `clojure.py`, `commonlisp.py`, `cpp.py`, `csharp.py`, `dart.py`, `elixir.py`, `elm.py`, `erlang.py`, `fsharp.py`, `go.py`, `groovy.py`, `haskell.py`, `julia.py`, `kotlin.py`, `lua.py`, `objc.py`, `ocaml.py`, `perl.py`, `py.py`, `ruby.py`, `rust.py`, `scala.py`, `swift.py`, `wolfram.py`, `linkers/method_call_recovery.py`.
- **Registry-side:** No new entries required. The Cluster 28A apex `ast_call` (line 94) and `message_send` (line 236) already exist. The 28 Cluster 28D values stay in the registry on AXIS_ENDPOINT_SHAPE through the Phase 4a deprecation window per ADR-0028 §"Phase 4"; Phase 4b (gated on bakeoff validation per the `awaits_bakeoff_validation` discipline) will remove them.
- **Schema-side:** Open enum on `Edge.evidence_type` already accommodates the additive change (no SCHEMA_VERSION bump). The new `meta` keys (`call_construct`, `receiver`, `resolution_quality`, `visibility`) are documented in the `Edge.meta` open-form section of the schema; no per-key registration required.
- **Test-side:** Tests previously asserting `evidence_type == "<peer_name>"` on the migrated edges update to assert `evidence_type == "ast_call" and meta["call_construct"] == "<value>"` (and additional meta keys per row).
- **Cluster 28B holdovers:** Three Cluster 28B PRELIM_RESOLVED rows fold to Cluster 28D peer values (`chained_call_unresolved`→`method_call_field_chain`, `unresolved_method_call`→`method_call`, `unresolved_variable_method_call`→`method_call_type_inferred`). The Cluster 28B Phase 3 producer migration (audit-findings 0008) emits those peer values; this PR re-folds those producer sites to `ast_call` + `meta` while preserving `is_resolved=False`.

## Related

- [ADR-0028](../adr/0028-evidence-type-inference-pathway-only.md) — declares the `Edge.evidence_type` axis this audit applies; §"Phase 3" Cluster 28D names this fold; §"Open question 1" decided here in favor of `ast_call`.
- [ADR-0024](../adr/0024-axis-declaration-template.md) — the template ADR-0028 instantiates; defines the CANONICAL/FOLD/DEPRECATE-NO-FOLD verdict trichotomy.
- Audit-findings 0004 — Cluster 28A canonical inference (the registry seed for the inference_pathway axis where `ast_call` and `message_send` apex values live).
- Audit-findings 0008 — Cluster 28B resolution-status (the canary Phase 3 sub-PR; some of its fold targets are Cluster 28D peer values that this audit re-folds).
- WI-runod cross-axis schedule — Wave 4 of which this PR closes.
