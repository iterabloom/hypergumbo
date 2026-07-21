<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Audit-findings 0017: Endpoint-Shape Long-Tail Classifications

- Date: 2026-07-20
- Status: Mixed (1 RESOLVED — `script_src`; 21 UNRESOLVED — verdicts recorded, per-pattern fold migrations pending)
- Closes: WI-pumav (endpoint_shape long-tail fold audit) — the verdict pass; the per-subset FOLD migrations remain follow-on work
- Sibling: [audit-findings 0001](0001-dispatch-publish-family.md) / [0002](0002-ipc-family.md) / [0016](0016-resolver-openapi-rpc-family.md) — same methodology, other `Edge.edge_type` families
- Methodology: per [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md). Filed under the audit-findings format defined in [`docs/audits/README.md`](README.md).

## Context

ADR-0023 §6's Phase-4b work folded 33 endpoint_shape values (dst-kind /
bridge / IPC / publish / dispatch families) and WI-hirud pruned the
protocol-call trio, but WI-tavas-voror had re-added a long tail of
endpoint_shape values for registry-completeness and deferred their fold
to an audit that was never filed. This document is that audit: a
per-value CANONICAL-vs-FOLD verdict pass over the 22 long-tail values,
so a human can sequence the (heterogeneous, per-pattern) fold plan
before any producer migration. It is a *verdict* pass, not the
migration — one value (`script_src`) was already producer-migrated and
is pruned in the same PR; the other 21 stay live pending their
per-pattern Phase-3/4b′ cycles.

### What the audit looked at

Every emit site for each value (literal-kwarg producers; verified
across `packages/` incl. `hypergumbo-lang-*`). The fold targets are
heterogeneous exactly as ADR-0023 §Phase-4b warned — some to `calls`,
some to `references`, some to `includes`/`extends`/`depends_on`/
`contains`, one to pub-sub, one to `data_flows_to`. Consumer coupling
is minimal: of all 22, only `crypto_flow` appears in a consumer
edge-type set (`ranking.py:208`, weight 0.8).

## Methodology

The CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy and the
four-leakage-test procedure are defined in
[ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).
This document applies that methodology. The four cautioned "maybe
genuinely distinct" values (`renders`, `signal_receiver`, `uses_mixin`,
`association`) each got a real four-test pass — none is CANONICAL
(see Diagnostic findings).

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: abi_call
    verdict: FOLD
    fold_target: calls
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"abi_call\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Solidity ABI call IS a call (linkers/solidity_abi.py:234). Fold to calls + meta['call_kind']='abi' — NOT meta['protocol']='abi' (PROTOCOL_KINDS is a closed enum {ipc,http,grpc,graphql}); the synthetic symbol already carries call_kind='abi'. Test 4 (mechanism vs. category)."
  - value: association
    verdict: FOLD
    fold_target: references
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"association\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Ruby ActiveRecord association (ruby.py:1597) is a framework construct fully recovered by meta['framework_dispatch']='activerecord_association' already emitted → references + meta['ref_construct']='association'. Test 3 (construct vs. relationship). (Cautioned 'maybe distinct' — four-test pass says fold.)"
  - value: base_image
    verdict: FOLD
    fold_target: depends_on
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"base_image\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Dockerfile intra-file multi-stage FROM...AS (dockerfile.py:192-202): stage→stage build-graph dependency → depends_on + meta['ref_construct']='dockerfile_stage'. RULED 2026-07-20 (4-lens investigation) depends_on over extends: the SAME producer already emits depends_on for the structurally identical COPY --from stage edge (dockerfile.py:305-315), the depends_on docstring names Dockerfile explicitly, extends has zero non-class precedent, and slice treats extends as structural (skipped in forward BFS) which would split the two identical stage edges. No supply-chain pollution (tiering is path-based; package-dep resolution uses depends_on_manifest). Test 1/4."
  - value: build_tag_alternative_of
    verdict: FOLD
    fold_target: references
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"build_tag_alternative_of\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Go same-qualified-name symbols under differing build_constraint (go.py:4507-4527) → references + meta['ref_construct']='build_tag_alternative'. RULED 2026-07-20 (4-lens investigation) FOLD over the defensible borderline-CANONICAL: the 'symmetric equivalence' is not actually delivered (the producer emits a single arbitrary-direction edge per pair, not a reciprocal pair), the relationship is fully reconstructible from endpoints (Test 1), and it is a lone Go-only producer — below ADR-0024's N=3-values-OR-N=2-producers promotion bar. RE-EVAL TRIGGER: promote to CANONICAL when a second language grows a conditional-compilation-variant equivalence (C #ifdef, Rust #[cfg], platform-specific files)."
  - value: caller_invokes
    verdict: FOLD
    fold_target: calls
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"caller_invokes\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Tauri IPC caller→command (linkers/tauri_ipc.py:796) IS a call; 'ipc' is the mechanism → calls + meta['protocol']='ipc' (parallel to the ipc_calls fold, audit-findings 0002). Test 4 (mechanism vs. category)."
  - value: contains_routes
    verdict: FOLD
    fold_target: contains
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"contains_routes\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Controller→route-handler span enclosure (linkers/controller_routes.py:160); the dst concept 'route' is queryable from the dst node → contains. Test 1 (property-derivability)."
  - value: crypto_flow
    verdict: FOLD
    fold_target: data_flows_to
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"crypto_flow\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "CAUTION (a) CONFIRMED: crypto_flow (linkers/crypto_flow.py:358) already sets data_direction='src_to_dst' + channel as first-class fields; it is dataflow, NOT a call. Fold to data_flows_to + meta['ref_construct']='crypto' (ADR-0038 ruling 3 territory). Ranking-weight coupling at ranking.py:208 must move with it. Test 4 + ADR-0038."
  - value: depends
    verdict: FOLD
    fold_target: depends_on
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"depends\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Manifest-declared dependency (requirements.py:223, bitbake.py:227) → the canonical depends_on synonym (manifest's own declaration). Test 4 (mechanism vs. category)."
  - value: extends_template
    verdict: FOLD
    fold_target: extends
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"extends_template\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Twig/Blade template extends parent (twig.py:101, blade.py:127), evidence 'extends' → extends + meta['ref_construct']='template'. Test 1 (property-derivability)."
  - value: includes_class
    verdict: FOLD
    fold_target: includes
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"includes_class\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Puppet manifest include of a class (puppet.py:432), evidence 'include' → includes + meta['ref_construct']='puppet_class'. Test 1/4."
  - value: includes_template
    verdict: FOLD
    fold_target: includes
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"includes_template\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Twig template includes a partial (twig.py:164), evidence 'include' → includes + meta['ref_construct']='template'. Test 1."
  - value: invokes_callback
    verdict: FOLD
    fold_target: dispatches_to
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"invokes_callback\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Elixir/Erlang behaviour callback + Rails callback (elixir.py:712, ruby.py:1319) is framework-registered indirection → dispatches_to + meta['mechanism']='callback'. Test 4 (mechanism vs. category)."
  - value: kernel_launch
    verdict: FOLD
    fold_target: calls
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"kernel_launch\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "CUDA kernel launch (cuda.py:316 — literally 'kernel_launch' if is_kernel_launch else 'calls') IS a call; the launch is the mechanism → calls + meta['mechanism']='kernel_launch'. Test 4."
  - value: links_to
    verdict: FOLD
    fold_target: references
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"links_to\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Markdown internal link (markdown.py:356) → references + meta['ref_construct']='markdown_link'. Test 3 (construct vs. relationship)."
  - value: notifies_resource
    verdict: FOLD
    fold_target: depends_on
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"notifies_resource\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Puppet resource notify — refresh-on-change (puppet.py:342-353). RULED 2026-07-20 (4-lens investigation, REVISED from the provisional event_publishes): same resource→resource ordering shape as its sibling requires_resource (→depends_on); it has no publisher/subscriber pair and no channel, so event_publishes would require a SYNTHETIC channel and would misclassify a config-refresh as a weight-0.8 async-IO/cross-cutting boundary. → depends_on + meta['ref_construct']='puppet_notify' + meta['refresh']=true (refresh preserved as a first-class queryable fact). Re-eval CANONICAL only if a consumer ever branches on refresh-propagation. Test 4."
  - value: renders
    verdict: FOLD
    fold_target: references
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"renders\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Controller action renders a view template (linkers/_view_template_core.py:270) → references + meta['ref_construct']='view_render'. Its JSX sibling renders_component ALREADY folded to references + ref_construct='jsx' — same construct-flavored reference, not distinct. Test 3/1. (Cautioned 'maybe distinct' — fold.)"
  - value: requires_resource
    verdict: FOLD
    fold_target: depends_on
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"requires_resource\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Puppet resource require — ordering dependency (puppet.py:334) → depends_on + meta['ref_construct']='puppet_require'. Test 4."
  - value: script_src
    verdict: FOLD
    fold_target: references
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"script_src\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: empty
    rationale: "HTML <script src=...> include: html.py:160 already emits references + meta['ref_construct']='script_src' (INV-vavat); NO producer emits a script_src edge type. Its dead registry entry is pruned in WI-pumav Batch 0 (this PR) — RESOLVED. Pruning it discharges the WI-pusuv access_mode-census coupling deferred on it. Test 3."
  - value: signal_receiver
    verdict: FOLD
    fold_target: dispatches_to
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"signal_receiver\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Django @receiver signal→handler via django.dispatch (py.py:4315-4324) is runtime-indirection dispatch → dispatches_to + meta['framework_dispatch']='django_signal' (the key the producer already stamps, matching django_orm_dispatch — a pure edge_type rename). RULED 2026-07-20 (4-lens investigation): emit shape is dispatcher-symbol→target, NOT publisher→subscriber; dispatches_to is in the dead-code reachability set (cli.py:6551) so this keeps @receiver handlers reachable, whereas event_publishes would strand them as false dead-code positives. Overrides the docstring's event_publishes guess. Test 4."
  - value: template_calls
    verdict: FOLD
    fold_target: calls
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"template_calls\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Vue template @click handler→component method (linkers/vue_template_method.py:118) IS a call; template is the mechanism → calls + meta['mechanism']='template'. Test 4."
  - value: uses_mixin
    verdict: FOLD
    fold_target: includes
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"uses_mixin\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "Sass/SCSS @include of a mixin (scss.py:411, evidence 'include') → includes + meta['ref_construct']='sass_mixin'. DISAGREES WITH DOCSTRING (references): canonical 'includes' explicitly covers mixins (incl. Ruby include/extend) and the evidence is literally 'include'. Test 1/4. (Cautioned 'maybe distinct' — fold.)"
  - value: uses_vocabulary
    verdict: FOLD
    fold_target: references
    status: UNRESOLVED
    diagnostic_test:
      cmd: "git grep -nE '\"uses_vocabulary\", AXIS_' packages/hypergumbo-core/src/hypergumbo_core/edge_types.py"
      expect: nonempty
    rationale: "SPARQL query→RDF prefix/vocabulary symbol (sparql.py:405) → references + meta['ref_construct']='rdf_vocabulary'. Test 3 (construct vs. relationship)."
```

**Net:** zero CANONICAL, 22 FOLD, zero DEPRECATE-NO-FOLD. One row
(`script_src`) RESOLVED (already producer-migrated; registry pruned in
this PR — WI-pumav Batch 0); 21 UNRESOLVED pending their per-pattern
fold migrations. Fold-target distribution (finalized after the 2026-07-20
4-lens ruling pass): 6→`references`, 4→`calls`, 4→`depends_on`,
3→`includes`, 2→`dispatches_to`, 1→`contains`, 1→`data_flows_to`,
1→`extends`. Zero `event_publishes` (the provisional `notifies_resource`
target was revised to `depends_on`). Every value has a live producer
(cited); none is dead.

## Diagnostic findings worth naming

**1. The three cautions all held or resolved cleanly.** (a) `crypto_flow`
CONFIRMED as dataflow-direction (ADR-0038 territory), NOT a naive
`calls` fold — its correct home is `data_flows_to`, direction already
handled by the `data_direction` field. (b) The four "maybe genuinely
distinct" values all folded on the four-test pass: `association`
(recovered by existing `framework_dispatch`), `renders` (its JSX
sibling already folded to `references`), `uses_mixin` (canonical
`includes` covers mixins), `signal_receiver` (dispatch indirection).
(c) `script_src` was a *dead* registry entry (0 producers) — its fold
already shipped under INV-vavat — so pruning it is the WI-pusuv-census
unblock, not a migration.

**2. Three values' four-test verdict overrode the registry docstring —
all RULED 2026-07-20 (4-lens investigation), docstrings confirmed
wrong:** `abi_call` (docstring `protocol='abi'`, but that's a closed
enum → `call_kind='abi'`; the fold should also correct the stale
`protocol` MetaKeySpec example list that still lists `abi`/`ipc_event`);
`uses_mixin` (docstring `references` → `includes`, matching the whole
include-family incl. the Ruby-mixin analog); `signal_receiver` (docstring
`event_publishes` → `dispatches_to`, meta `framework_dispatch='django_signal'`
— dispatch shape + dead-code-reachability correctness, not pub-sub).

**3. The two target-ambiguous values are RULED to `depends_on`
(2026-07-20, 4-lens investigation):** `base_image` → `depends_on` (its
sibling `COPY --from` stage edge already emits `depends_on`; `extends` is
class-only and would split slice's structural treatment); `notifies_resource`
→ `depends_on` (**REVISED from the provisional `event_publishes`** — same
resource→resource ordering shape as `requires_resource`; `event_publishes`
would need a synthetic channel and would misweight a config-refresh as an
async-IO boundary). The require/notify split is preserved in
`meta['ref_construct']` (`puppet_require` vs `puppet_notify`) + `refresh=true`,
not by two edge types.

**4. The borderline-CANONICAL value is RULED FOLD → `references`
(2026-07-20):** `build_tag_alternative_of`. The symmetric-equivalence
CANONICAL case declines because the producer emits a single
arbitrary-direction edge (not reciprocal — the symmetry is already a
lossy directed projection), the relation is derivable from the endpoints
(Test 1), and it is a lone Go-only producer below ADR-0024's N=3/N=2
promotion bar. RE-EVAL TRIGGER recorded on the verdict row: promote when
a second language grows a conditional-compilation-variant equivalence.

## Migration impact (prospective) — proposed per-PR subsets

This document is the verdict pass; only `script_src` (Batch 0) changed
code in the filing PR. The remaining 21 fold in per-pattern subsets,
each its own Phase-3/4b′ cycle with bakeoff validation, ordered by risk:

All targets finalized by the 2026-07-20 ruling pass — no per-batch
target decisions remain.

- **Batch 0 — `script_src` prune** (this PR; unblocks WI-pusuv; no producer/consumer change).
- **Batch 1 — `references` + `ref_construct`** (additive, lowest risk): `links_to`, `uses_vocabulary`, `association`, `renders`, `build_tag_alternative_of` (all → `references`; carry the build-tag re-eval trigger on that row).
- **Batch 2 — `includes` / `extends`**: `includes_template`, `includes_class`, `uses_mixin` → `includes`; `extends_template` → `extends`.
- **Batch 3 — `depends_on`**: `depends`, `requires_resource`, `base_image`, `notifies_resource` (the last carrying `ref_construct='puppet_notify'` + `refresh=true`).
- **Batch 4 — `calls` + mechanism/protocol** (centrality-sensitive): `abi_call` (`call_kind='abi'`, NOT `protocol`), `caller_invokes`, `kernel_launch`, `template_calls`.
- **Batch 4b — `dispatches_to`** (own sub-PR): `invokes_callback` (`mechanism='callback'`) + `signal_receiver` (`framework_dispatch='django_signal'`).
- **Batch 5 — `contains`**: `contains_routes`.
- **Batch 7 — `crypto_flow` → `data_flows_to`** (ADR-0038-coupled; ship last; update `ranking.py:208`).

## Related

- **Parent axis declaration**: [ADR-0023](../adr/0023-edge-type-relationship-not-endpoints.md) — the `Edge.edge_type` axis; §6 lists these long-tail values.
- **Methodology**: [ADR-0024 §"Family-audit verdict methodology"](../adr/0024-axis-declaration-template.md).
- **Sibling audits**: [audit-findings 0001](0001-dispatch-publish-family.md), [0002](0002-ipc-family.md) (the folded families this tail follows), [0016](0016-resolver-openapi-rpc-family.md) (the `pending_classification` resolver family, filed in the same pass).
- **Migration tracker**: WI-pumav (this audit + the fold batches), WI-kivip (the endpoint_shape fold-tail umbrella), WI-hirud (the protocol-call prune sibling), WI-pusuv (the downstream access_mode census the fold-tail drain unblocks).
