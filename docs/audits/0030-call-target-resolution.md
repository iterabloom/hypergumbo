<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# 0030 — How a call's target was chosen: seven spellings, none total

```yaml
kind: concept_leak_pairs
date: 2026-11-25
pinned_tree: 854caf8db
trigger: owner ruling 2026-11-24 (mark how every call edge was resolved, count, sample, then decide), and the cadence reminder (255 commits against a threshold of 72)
outcome: confirmed (seven of the eight suspected spellings; the eighth, disambiguation_fallback, is a different fact - pair 7)
reviewed_against: [ADR-0024, ADR-0028, ADR-0029, ADR-0037, ADR-0039, ADR-0053]
owner_rulings: none yet on the proposals below; the 2026-11-24 rulings they rest on are quoted
filed: see "Filed items"
referenced: [WI-mujug, WI-vusot, INV-jutog, INV-fahub, INV-zuhub, WI-jomik, WI-hugir]
```

## Suspicion

"By what evidence was this call edge's target chosen" is written in parts by `evidence_type`, `meta.receiver`,
`meta.resolution_quality`, `meta.disambiguation_fallback`, `meta.binding`, `confidence`, a resolver's private
`match_type` and one analyzer's private route constants. No one of them answers for every edge, the same answer is
spelled differently from one analyzer to the next, and the largest single population answers with nothing: a call
bound through an import and a call bound to the only function of that name in the repository are the same record.

Eight places were suspected; seven are confirmed below and one (`disambiguation_fallback`, pair 7) turned out to
state a different fact.

Falsifiable three ways: the unmarked population could have been produced by one mechanism (then silence would be an
answer); every analyzer could have spelled a typed receiver the same way; no reader could have depended on the
difference. None held.

Scope: the inventory and the counts are of `calls` edges. The mark proposed below covers the call family
(`calls` and `instantiates`: a constructor call names a type, and "which type of that name" is the same
question; 6,002 resolved `instantiates` edges in the same maps, 16 creation sites). The other name-resolved edge
types (`references`, `extends`, `implements`, `dispatches_to`) are not audited here.

## Method

Runtime inventory (playbook Step 2): every `calls` edge of 28 stored behavior maps (the maps made after PR #2385;
20 languages; 257,627 edges, superseded ones skipped), counted by each field and by the combination of fields.
Static inventory (Steps 2 and 4.5): a text scan of the 114 source files under `packages/*/src` that mention a
`calls` edge, for literal values of each key in kwarg form, dict-literal form and subscript-assignment form; an AST
census of call-edge creation sites; a grep of every reader of `LookupResult.match_type`. Helper-built values were
traced by hand where a verdict rests on them (`py._orm_resolution_quality`). No verdict below rests on "no
producer". f-string and dict-subscript-target producers of these keys: zero found by `grep -n 'f".*resolution_quality\|f".*receiver'`
and by reading each subscript assignment listed in the notebook file.

Measurement: for each resolved call edge, whether today's fields say anything about how the target was chosen,
and how many in-repository symbols of the caller's language share the target's short name and kind group
(`k_repo`), and how many of those are in the caller's file (`k_file`). `k_repo > 1` does not say an edge is wrong.
It says a choice among candidates was made.

Scripts and raw output: `~/hypergumbo_lab_notebook/resolution_mark_11252026/` (`inventory.py`,
`inventory_runtime.md`, `inventory_static.py`, `inventory_static.md`, `sites.py`, `unstated.py`, `unstated.md`).

## Inventory

### The spellings

| # | Where | Values | Written by | Read by |
|---|---|---|---|---|
| 1 | `Edge.evidence_type`, the receiver-flavoured call values | `ast_call_type_inferred`, `ast_method_type_inferred`, `ast_method_inferred`, `ast_call_static`, `ast_static_call`, `ast_method_this`, `ast_call_this`, `ast_method_this_property`, `ast_call_this_property`, `ast_call_inherited`, `ast_call_inherited_method`, `ast_call_inherited_field`, `ast_call_namespace`, `ast_call_extension`, `ast_call_ufcs` | java, kotlin, js_ts, php, rust, the `inherited_calls` and `receiver_type_dispatch` linkers | `receiver_blind_magnets._BARE_EVIDENCE` (by exclusion); the confidence bands of ADR-0039 |
| 2 | `meta.resolution_quality` | `typed`, `typed_receiver`, `type_inferred`, `chained_return_type`, `accessor_name`, `recovery`, `ambiguous` | go, ruby, lua, csharp, dart, py, swift, cpp, nim, fsharp, `method_call_recovery`, `receiver_blind_magnets` | `receiver_blind_magnets` (`!= "ambiguous"`), `verify_claims` (`== "accessor_name"`, two sites) |
| 3 | `meta.receiver` | `typed_var`, `typed_field`, `declared_type`, `self`, `qualified`, `bare`, `generic`, `field_chain`, `external`, `constant_external`, `stdlib` | go, ruby, rust, cpp, csharp, swift, solidity | `receiver_blind_magnets.RECEIVER_IDENTIFIES_TARGET`, `method_call_recovery` (`== "field_chain"`) |
| 4 | `meta.disambiguation_fallback` | `True` | linkers | `fallback_coherence` (static), consumers filtering the fallback population |
| 5 | `meta.binding` | `explicit_import` (elixir), `dot_import` (go) | two analyzers, on unresolved edges | no reader in `packages/*/src` or `scripts/` |
| 6 | `Edge.confidence` through `resolution_score` (ADR-0039 ruling 6) | a number: 1.0 exact, 0.90 path hint, 0.85 suffix, `0.70/sqrt(n)` ambiguous, times a per-analyzer factor | 84 of the 227 creation sites | ranking, thresholds |
| 7 | `LookupResult.match_type` (`symbol_resolution.py`) | `exact`, `path_hint`, `caller_file`, `suffix`, `soft_hint_fallback`, `ambiguous`, `suffix_ambiguous`; declared in a docstring only | the three shared resolvers | `analyze.base.defer_bare_method_call` (`in ("exact", "path_hint")`), go.py:1016 (same pair), kotlin.py:2852, nim.py:1352, `framework_patterns.py:2322`; 17 analyzer sites pass it through. It never reaches the output |
| 8 | `js_ts._JS_ROUTE_SCORE_EXACT` / `_SAME_PACKAGE` / `_RESOLVER` (js_ts.py:6482) | three constants named after the route taken, used as grades | js_ts | the grade only |

`Edge.is_resolved` is not in the table: ADR-0037 settled it as "the target is an in-repository symbol", and
nothing found here contradicts that.

### One fact, six spellings

A method call bound because the receiver's type was known:

| Analyzer | Spelling | Site |
|---|---|---|
| kotlin | `evidence_type="ast_call_type_inferred"` | kotlin.py:2628 |
| rust | `evidence_type="ast_call_type_inferred"` + `receiver="typed_var"` | rust.py:4020 |
| rust (a field) | `ast_call` + `receiver="typed_field"` | rust.py:3944 |
| js_ts | `evidence_type="ast_method_type_inferred"` | js_ts.py:8061 |
| go, ruby | `ast_call` + `resolution_quality="typed_receiver"` | go.py:4805, ruby.py:3482 |
| lua | `ast_call` + `resolution_quality="typed"` | lua.py:938 |
| csharp, dart | `ast_call` + `resolution_quality="type_inferred"` | csharp.py:2078, dart.py:1466 |
| py (the module slot of an UNRESOLVED edge) | `ast_call` + `resolution_quality="type_inferred"` | py.py:7542 |
| cpp | `ast_call` + `receiver="declared_type"` | cpp.py:2555 |

The same for a call on the enclosing object (`ast_method_this` js_ts.py:7873, `ast_call_this` kotlin.py:2350,
`receiver="self"` ruby.py:862, `ast_call_inherited` from the linker) and for a call that names its owner
(`ast_call_static` java.py:3276 and kotlin.py:2271, `ast_static_call` php.py:1696, `receiver="qualified"`
rust.py:3821, `ast_call_namespace` js_ts.py:7981).

### Creation sites

227 `Edge.create(..., edge_type="calls")` sites in 89 files and 91 direct `make_unresolved_edge` sites (wrappers
count once). 84 of the 227 pass a `resolution_score`; 19 pass a literal `receiver`; 13 a literal
`resolution_quality`; 35 build `meta` elsewhere. One creation site often serves several lookup routes (js_ts.py:7694
serves five), so the route is known where the callee is assigned, not where the edge is created.

## Measurements

28 maps, 257,627 call edges: 95,105 resolved, 162,522 unresolved. 64 distinct combinations of
(resolved, evidence_type, confidence, resolution_quality, receiver).

| What today's record says about a resolved call | Edges |
|---|---|
| Nothing: `ast_call` or `ast_call_direct`, no `receiver`, no `resolution_quality`, no fallback flag, an analyzer origin | 39,223 |
| A linker made it (`inherited_calls` 26,433; `method_call_recovery` 1,649; `vue_template_method` 412; `receiver_type_dispatch` 59; `build_target` 3) | 28,556 |
| A receiver-flavoured or reference `evidence_type` from an analyzer | 19,820 |
| A `receiver` or `resolution_quality` value | 7,506 |

Of the 39,223 that say nothing: 21,123 point into the caller's own file and 18,100 into another file; 14,273 had at
least one other in-repository symbol of the same short name and kind group (`k_repo > 1`), 4,393 of them
cross-file. By language (unstated / with `k_repo > 1`): java 6,046 / 4,228; kotlin 5,098 / 1,245; scala 4,760 /
2,220; cpp 4,545 / 555; lua 3,596 / 1,211; rust 3,517 / 1,581; typescript 3,167 / 786; swift 3,154 / 1,930; go
1,600 / 103; ruby 1,010 / 97. Full table: `unstated.md`.

`ast_method_inferred` is 7,668 resolved edges (javascript 4,821, typescript 2,268, php 568, vue 11). Its registry
text is "a method call where dispatch was inferred". Its largest producer is js_ts.py:8231, whose own comment is
"Case 4: Fallback - method name match with low confidence": the receiver is untyped and the method is found by its
name among every class of the repository. 238 of the 7,668 had `k_repo > 1`.

## Verdicts

T1 property derivability, T2 apex/peer, T3 construct vs relationship, T4 mechanism vs category.

### Per pair (playbook Step 6)

| # | Pair | T1 | T2 | T3 | T4 | Verdict |
|---|---|---|---|---|---|---|
| 1 | bare `ast_call` / `ast_call_direct` × the receiver-flavoured `ast_*` call values | no | **fires**: `ast_call` is the apex (ADR-0028 folded 28 call variants into it) and, in java, kotlin, js_ts, php and rust, the peer that means "not typed, not static, not this" | no | **fires**: the flavour is how the target was found, not how the call was seen | DEPRECATE the flavour as an `evidence_type` distinction: fold to `ast_call` + the mark. It moves confidence bands (ADR-0039), so it is a behaviour change and waits for the shared pass and an owner ruling |
| 2 | `ast_call_type_inferred` × `resolution_quality` ∈ {`typed`, `typed_receiver`, `type_inferred`, `chained_return_type`} × `receiver` ∈ {`typed_var`, `typed_field`, `declared_type`} | no | no | no | no: one fact, three homes | DEPRECATE the two meta spellings on resolved edges: fold to the mark's one value. WI-mujug already asks for the three `typed*` values to be folded or told apart |
| 3 | `receiver` shape values (`bare`, `generic`, `field_chain`) × its outcome values (the rest) | no | no | **fires**: an expression's shape against a resolution outcome | no | The outcome half folds to the mark; the shape half stays, as the call fact it is. Confirms the 2026-09-10 finding; its re-verdict of 2026-10-28 (readers match producers) still holds for the readers |
| 4 | `resolution_quality="ambiguous"` on a resolved edge (a pick among `n` candidates, graded `1/sqrt(n)`: go.py:5295, cpp.py:2615, swift.py:3667, nim.py:1351, fsharp.py:1773) × the same value on an unresolved edge (a tie refused, or a magnet demoted by `receiver_blind_magnets`) | no | **fires**: one token, a pick and a refusal | no | no | DEPRECATE on resolved edges (the mark says "by name"; `k` and the grade say among how many). On unresolved edges KEEP WITH A TRIGGER |
| 5 | `resolution_quality="recovery"` × the other values | no | no | no | **fires**: a mechanism (a class the caller constructs, plus the method's name) in a key registered as "pathway quality" | DEPRECATE: fold to the mark's own value for it |
| 6 | `resolution_quality="accessor_name"` × the rest | no | no | no | no | KEEP: ADR-0053's disclosure, read by `verify_claims`, stamped on unresolved edges (the module slot was typed from a name), which the mark does not cover yet |
| 7 | `disambiguation_fallback=True` × the mark | no | no | no | no | KEEP both, and DOCUMENT the boundary. Read at its producers (`cgo.py`, `jni.py`, `pyffi.py`: `is_fallback = len(candidates) > 1`), the flag says a pick among several candidates was made, which is the one thing the mark deliberately does not hold. The first draft of this audit read it as "resolved by name alone" and proposed a fold; the producers refuted that. A linker that matched an exported symbol is `key_match` with or without the flag |
| 8 | `match_type` × the mark | no | no | no | no: a resolver's internal answer to the same question | DOCUMENT: declare its seven values as constants in `symbol_resolution.py` and give `LookupResult` the one place that maps them to the mark. Three readers compare against bare string pairs today |
| 9 | `confidence` (a grade) × the mark (a category) | no | no | no | no | KEEP both. ADR-0039 ruling 6 put the grade in `confidence`; the category was lost on the way (0.85 is "exact match" from one site and "nothing passed" from another). Owner, 2026-11-24: a name-only match keeps its number until the count exists |
| 10 | `meta.binding` and `resolution_quality="type_inferred"` on UNRESOLVED edges (py.py:11196) × the mark | no | no | no | no | KEEP WITH A TRIGGER: these say how an outside module was identified, which is the mark's question asked of an edge that leaves the repository |
| 11 | `js_ts._JS_ROUTE_SCORE_*` × the mark | no | no | no | **fires** mildly: constants named for a route, used as a grade | DOCUMENT: stamp the mark beside each; the constants stay grades |

### Per value (ADR-0024), for the values the mark replaces

These are the audit's verdicts against the proposed axiom below. They do not move a registry row: audit 0004 holds
the receiver-flavoured `ast_*` values CANONICAL on the evidence-type axis, and that stands until the fold is ruled.

| Value | Home today | Verdict | Fold target |
|---|---|---|---|
| `ast_call_type_inferred`, `ast_method_type_inferred` | evidence_type | FOLD | `ast_call` + mark `receiver_type` |
| `typed`, `typed_receiver`, `type_inferred`, `chained_return_type` (resolved edges) | resolution_quality | FOLD | mark `receiver_type` |
| `typed_var`, `typed_field`, `declared_type` | receiver | FOLD | mark `receiver_type` |
| `ast_method_this`, `ast_call_this`, `ast_method_this_property`, `ast_call_this_property`, `ast_call_inherited` | evidence_type | FOLD | `ast_call` + mark `own_type` |
| `self` | receiver | FOLD | mark `own_type` |
| `ast_call_inherited_method`, `ast_call_inherited_field` | evidence_type | FOLD | `ast_call` + mark `receiver_type` (that the method sits on an ancestor is read from the target's owner) |
| `ast_call_static`, `ast_static_call`, `ast_call_namespace` | evidence_type | FOLD | `ast_call` + mark `qualified` |
| `qualified` | receiver | FOLD | mark `qualified` |
| `ast_method_inferred` | evidence_type | FOLD | `ast_call` + mark `name` |
| `ast_call_extension`, `ast_call_ufcs` | evidence_type | FOLD | `ast_call` + mark `receiver_type` |
| `recovery` | resolution_quality | FOLD | mark `class_hint` |
| `ambiguous` (resolved edges) | resolution_quality | FOLD | mark `name` |
| `disambiguation_fallback` | meta flag | CANONICAL: a pick was made (all edge types); orthogonal to the mark | none |
| `bare`, `generic`, `field_chain` | receiver | CANONICAL on a narrowed `receiver` (expression shape) | none |
| `external`, `constant_external`, `stdlib` | receiver | not ruled (unresolved edges) | none |
| `accessor_name`; `ambiguous` and `type_inferred` on unresolved edges | resolution_quality | CANONICAL until the mark covers identified externals | none |

### Keep triggers

- Pair 4 (unresolved `ambiguous`), pair 6, pair 10: re-open when a reader needs to know WHY a call is unresolved
  (a tie against nothing found), or when the mark is extended to edges whose target is an identified outside
  module. Watcher: the vocabulary test of the mark (below) fails on any new literal value of
  `resolution_quality`, `receiver` or `binding` in `packages/*/src`, so a new spelling of the question cannot be
  added without reading this document.
- Pair 9: re-open when the census by mark exists (it is the next thing built). Watcher: the owner's ruling of
  2026-11-24 names it.

## Silent bugs found on the way

1. **The magnet detector cannot see the largest name-only population.** `receiver_blind_magnets._BARE_EVIDENCE` is
   `{"ast_call", "ast_call_direct"}`, and its comment says every other evidence type "encodes that the receiver WAS
   characterised". `ast_method_inferred` is not in the set, and at js_ts.py:8231 and php.py:1657 it is stamped on a
   method found by name on an untyped receiver. Those 7,668 edges are outside the detector by construction. Not
   measured: how many of them the detector would call a harmful magnet.
2. **`resolution_quality != "ambiguous"` reads every future value as "receiver identified"** (already recorded on
   WI-mujug, 2026-10-28). `recovery` passes that test today: a class the caller constructs somewhere is treated as
   the receiver's known type.
3. **A grade cannot be read back as a category.** `ast_call` at 0.85 is 25,040 resolved edges; it is the registered
   base that a site gets by passing nothing and the value an exact resolver match interpolates to. A consumer that
   thresholds on confidence to drop weak resolutions keeps both.
4. **`match_type`'s "strong" pair is copied.** `("exact", "path_hint")` is written at `analyze/base.py`
   (`defer_bare_method_call`) and again at go.py:1016, over a vocabulary declared only in a docstring;
   `caller_file`, added later, is in neither. Whether a `caller_file` match should count as strong there was not
   examined.

## Proposals

Each was read against ADR-0024, ADR-0028, ADR-0029, ADR-0037, ADR-0039 and ADR-0053 before being written.

### A. One mark, with one declared vocabulary

**Axiom.** The mark names the evidence that scoped the choice of a call edge's in-repository target. It answers
"why this declaration and not another of the same name". It does not grade the match (`confidence`), say where the
target lives (`is_resolved`), name the construct at the call site (`call_construct`) or the shape of the receiver
expression (`receiver`).

**Values** (the first draft had nine; reading csharp.py and js_ts.py against it added `lexical_scope`, which
js_ts's position binder needs, and `same_module`, which Go's package scope needs):

| Value | The target was chosen because |
|---|---|
| `receiver_type` | the receiver's type was established and the method was looked up on that type or its ancestors |
| `own_type` | the receiver is the enclosing object (`self`, `this`, implicit) and the method was looked up on the enclosing type or its ancestors |
| `qualified` | the call site names the owner type or module, matched to the declaration's owner by that name |
| `import` | an import in the caller's file binds the called name, or its qualifier, to the declaring file or module |
| `lexical_scope` | a scope-aware binder found the declaration the call site sees (a nested function, a local binding resolved by position) |
| `same_file` | the name was looked up in a table of the caller's own file's declarations, with no scope rule behind it |
| `same_module` | the language makes the declarations of the caller's own package, namespace or module visible without an import, and the name was looked up among them |
| `name` | the name was looked up among the declarations of the whole repository: nothing scoped it. A nearness heuristic with no language rule behind it (same directory, same npm package, nearest line) is still `name` |
| `class_hint` | the caller constructs or names a class elsewhere in its body and that class has a method of this name (`method_call_recovery`) |
| `key_match` | a linker matched a key both sides declare (a route, a topic, an exported symbol), not a name in the caller's language |
| `semantic_backend` | a compiler-grade backend (SCIP, rust-analyzer) supplied the target |

What the mark deliberately does not hold: how many candidates there were. That is derivable (T1): the census
counts `k_file` and `k_repo` from the nodes, so `same_file` with `k_file > 1` is "picked by declaration order" and
`name` with `k_repo > 1` is "picked among same-named declarations", without a value for either.

**Where it lives.** Step 2 ships it as `Edge.meta["resolved_by"]`, registered in `axis_meta_keys`, its values
declared in one core module with an allowlist test, the pattern `call_constructs.py` set for `call_construct`.
It is passed to `Edge.create` as a keyword, so the creation sites do not change if it moves. ADR-0024's
fold-residue rule 3 (a key written by two or more producer modules is promoted to a field of the dataclass)
argues for a field; `call_construct`, written by 25 modules, was left in `meta`. Field or key is a decision for
the ADR that follows this audit.

**Total on resolved call-family edges.** Every `Edge.create` site whose edge type is `calls` or `instantiates`
passes it, checked statically
(an AST test over `packages/*/src`), and the census reports the unmarked count, which must be zero. Absent on
unresolved edges: nothing was chosen.

**ADR-0028.** Its axiom is that `evidence_type` names the pathway by which the analyzer concluded the edge exists,
and its Decision collapses call variants to `ast_call` plus `meta`, naming `meta["resolution_quality"]` as the home
of resolution residue. The mark is that residue given one vocabulary. ADR-0037 ruling 3 says resolution certainty
lives in `confidence` and `evidence_type`; the proposal narrows that sentence to `confidence` and the mark, and the
ADR that follows must say so by number.

### B. No reader changes in step 2

The mark is written and counted. `receiver_blind_magnets`, `method_call_recovery`, `verify_claims` and the
confidence derivation read what they read today. Re-pointing the magnet detector at the mark (silent bugs 1 and 2)
changes which edges are demoted and belongs with the shared pass.

### C. The folds are executed per language, when its analyzer adopts the shared pass

The owner's rule for the rollout is that each language's commit deletes what the shared pass replaced. The old
spellings of the table above are on that list. Until then both are written. The guard against the mark becoming a
ninth spelling that outlives nothing: a test pins the number of emit sites of each old spelling per file, and the
number may only go down.

### D. `match_type` gets constants and one mapping

`symbol_resolution.py` declares its seven values as constants and `LookupResult` gains the mark they map to
(`path_hint` → `import`, `caller_file` → `same_file`, the rest → `name`). One rule sits on top, in one helper: a
bare call whose candidate is a method of the caller's own enclosing type is `own_type` whatever the lookup said,
because that is the language's implicit-receiver rule and `defer_bare_method_call` already binds on it. `exact` is among "the rest": it says the
key was found verbatim, and kotlin.py:2852 already rewrites an `exact` hit with no import behind it to `suffix`
before asking whether to trust it. An analyzer that built the lookup key
from a type it had established (cpp.py:2497 passes a resolved type as the path hint; rust.py:4224 passes the
caller's directory) states its own mark instead.

## Filed items

| Item | What |
|---|---|
| WI-hirol | The mark: vocabulary, every creation site, the census (proposals A, B, D). Built first, no behaviour change. Its text says `calls`; the build covers the call family (`calls` and `instantiates`), as the Scope paragraph above explains |
| WI-topit | The magnet detector's two blind spots (silent bugs 1 and 2); a behaviour change, with the shared pass |
| WI-gilip | The folds of the per-value table, per language at its move to the shared pass (proposal C); needs the owner's ruling on the `evidence_type` folds. Its text lists `disambiguation_fallback`; pair 7's verdict above replaces that line |
| WI-dolib | The copied "strong match" pair and where `caller_file` belongs (silent bug 4) |
| WI-marig | `meta.binding` has no reader |

Not re-filed: WI-mujug (declare `resolution_quality`'s values; fold the three `typed*` spellings) stands and is
served by WI-gilip; WI-vusot (a value layer on `MetaKeySpec`) stands.

## Not checked

- The fields of `instantiates` edges (they get the mark; their existing spellings were not inventoried), and every
  other name-resolved edge type.
- Languages absent from the 28 maps: the static inventory covers their producers, the counts do not.
- `resolution_quality="accessor_name"` did not occur in the 28 maps (no Django repository) and
  `chained_return_type` occurred five times. The first's producer was read, not run.
- Readers outside `packages/*/src` and `scripts/` (a consumer of the JSON output).
- Whether any of the 14,273 unstated edges with `k_repo > 1` is wrong. The count sizes a population; the sample
  comes with the census by mark.
- A reader of `meta.binding` outside `packages/*/src` and `scripts/`.
