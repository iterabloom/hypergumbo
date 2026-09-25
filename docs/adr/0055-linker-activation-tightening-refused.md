<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0055: Linker activation tightening is refused, on measurement

Date: 2026-09-16
Status: Accepted

## Decision

**No linker among the 25 that declare `always_on_unreviewed()` has its activation gate tightened.** This is a refusal on measurement, not a default. It binds future work through a four-part criterion (none of the 25 currently satisfies it), a blocking precondition, and three re-evaluation triggers.

**Decision rule applied:** a gate is justified only when *(measured cost avoided) exceeds (recall risk × detection false-negative rate)*, **and** the resulting recall loss is disclosed. All three terms fail today.

## Context

61 linkers are registered: 24 genuinely gated, 12 deliberately `always=True`, and 25 declaring `always_on_unreviewed()` — a factory added in PR #1007 that is behaviourally identical to `always=True` and means "runs unconditionally, and nobody assessed whether it should be gated." That declaration exists to produce this worklist.

LIVE.md records the governing asymmetry: *"LOOSENING A WITHHOLDING GATE IS THE FALSE-ALL-CLEAR DIRECTION, TIGHTENING AN ACTIVATION GATE THE RECALL-LOSS ONE."* A gated linker whose framework detection misses drops real edges and leaves no hole where a hole would be visible.

## The number this turns on, and the instrument fault in the inherited one

`~/hypergumbo_lab_notebook/nanon_passes_09062026/readback.py` filters silence as `edges_emitted == 0`, ignoring `nodes_emitted`. That is **looser than the repo's own definition**: `pass_silence.py` states that a pass emitting nodes but no edges is **not silent**, and that calling such passes silent "would have been the headline's original error repeated one level down." apollo-server's `graphql-resolver-linker` emitted 91 nodes and 0 edges and was counted as silent.

Recomputed strictly (`nodes==0 ∧ edges==0 ∧ files>0`) over the 9 cohort surveys in `~/hypergumbo_lab_notebook/jokij_dstref_09062026/out/*/survey.json`:

| population | ms | % of 2,538,624 ms total pass time |
|---|---:|---:|
| the loose filter (source of the circulated "4.5%") | 39,048 | 1.54% |
| strict silence, all passes | 21,192 | 0.83% |
| strict silence, linkers only | 19,432 | 0.765% |
| — Protocol-subcategory, among the 25 | 15,087 | 0.594% |
| — **Framework-subcategory, among the 25 (the gateable prize)** | **4,216** | **0.166%** |
| — Infrastructure-subcategory, among the 25 | 0 | 0% |

The circulated "4.5%" was the loose filter on the 8-repo cohort *excluding* pretix, which alone carries 90% of cohort pass time. The "4.78%" figure was the hypergumbo self-survey — one small pure-Python repo. Neither was wrong; neither sized this decision.

**10 of the 13 Framework-subcategory linkers among the 25 cost zero measured ms.** airflow, caddy-module, decorator-dispatch, django-orm, jackson, kafka-streams, rust-trait, controller-routes, middleware-chain and router-routes never appear in the silent population. This is structural: they read framework-pattern-enriched symbols, and `profile.py` records that `enrich_symbols` only loads patterns for detected frameworks — **they are already de-facto framework-gated through their data dependency.** An explicit gate on those ten is redundant for cost and purely additive for risk. The entire 0.166% is graphql-resolver (3,915 ms), orm (230 ms) and route-handler (71 ms).

## Three hypotheses, evaluated

**H1 — the pass-silence axis as a selection instrument: REJECTED as a selector, ACCEPTED as a veto.** The proposal was to gate linkers that report `no_candidate_construct` across a corpus. It is circular: that value is *produced by the very scan the gate would skip*. The gate must key on a cheap proxy (`detected_frameworks` / `detected_languages`), and the axis says nothing about whether the proxy correlates with the construct. ADR-0054 also records that 53 of 64 linker modules make no body claim at all, so the instrument is 83% unpopulated — selecting from it is the ABSENT ≠ EMPTY defect. Accepted narrowly and asymmetrically: `candidates_unresolved` on a repo where a linker's gating key is absent **proves gating would destroy real work**. It can only ever say "don't gate."

**H2 — "gating need not be silent": REJECTED; the premise is false in today's tree.** `run_all_linkers` filters `active_linkers` *before* dispatch, so a gated-off linker produces no `LinkerResult`, therefore no `AnalysisRun` — and `silence_reason` is a field **on** `AnalysisRun`. The disclosure carrier does not exist for a pass that never ran. Only crashes reach `limits.skipped_passes`, via `_record_linker_crash`, and `pass_silence.py` records that the exclusion of non-analyzer passes from that channel is deliberate. Further, `prerequisite_absent` is **not** the right landing value: it is declared as *"a declared upstream **pass** did not run"* — an ordering defect — and an activation predicate evaluating false is not that. Stamping it there would be the fabricated-disclosure failure the axis explicitly warns against.

Disclosure would convert *silent* recall loss into *annotated* recall loss: strictly better than silent, strictly worse than no loss. It does not flip a calculus whose prize is 0.166%. It is a **blocking precondition** for any future tightening, not a property of today's tree.

**H3 — ADR-3bbb's subcategories as the criterion: PARTIALLY ACCEPTED, but it is a default, not a law, and it captures almost none of the cost.** All 25 declare a subcategory (13 Framework, 8 Protocol, 4 Infrastructure, 0 Bridge) — none is undeclared. But ADR-3bbb says a Protocol linker *"typically activates always"*, not *must*; the subcategory does not entail the activation. The split also breaks in four places: `ipc` is labelled Protocol but is Electron-only and JS/TS-only; `method_call_recovery` is labelled Protocol but is a pure graph rewrite matching the ADR's own Infrastructure definition; `decorator_dispatch` is labelled Framework but recognises only hypergumbo's own `register_analyzer`/`register_linker`; `graphql_resolver` is labelled Framework but matches GraphQL *protocol* semantics across five unrelated libraries. And applying the rule captures 0.166%, because the cost sits in Protocol linkers that are framework-agnostic by definition and have nothing to gate on.

## The criterion (applied: zero of 25 qualify)

A linker among the 25 becomes a tightening candidate only when **all four** hold:

1. **Subcategory is Framework or Bridge**, and the label survives a behaviour check rather than resting on the docstring.
2. **Measured strict-silent cost ≥ 2% of total pass time** on a ≥8-repo polyglot cohort including at least one >1M-ms repo. Best current candidate: graphql-resolver at 0.154%. **Fails by 13×.**
3. **The linker is demonstrated to work** — ≥1 edge on ≥1 cohort repo where its framework *is* detected. graphql-resolver emits **0 edges on apollo-server** (79 files, 91 nodes, framework correctly detected; filed as WI-dinum). Gating an unproven linker freezes it behind a gate that hides the evidence of its own defect.
4. **Its gating token is not on `profile.py`'s own unreliable list.** `profile.py` names `graphql` explicitly as a token that must stay gated out of bare-name promotion (WI-rofiz). Keying a gate on a token the repo already distrusts inverts its own finding.

## Detection reliability

Single producer: `detected_frameworks=set(profile.frameworks)` in `cli.py`, from `detect_profile`. `profile.py` states the design: *"Detection is intentionally shallow — we look for package names in dependency files rather than analyzing imports."*

Two documented false-negative families, both already tracker-filed. WI-lohok: Rails is loaded by Bundler and no app code says `require 'rails'`, so `refine_frameworks` demoted it to `dev_frameworks`, and *"a demoted framework's YAML never applies."* WI-tosul: a manifest-silent Flask/Express app produced a bare exact import the prefix gate rejected, so *"the framework's YAML never loaded and its routes stayed dark."* Both needed hand-maintained allowlists — the failure mode is live and recurring.

Gating reads the **post-demotion** set: `refine_frameworks` runs before the linker context is built, and only `profile.frameworks` is passed, so every demotion is a gate-off. `--frameworks=none` empties the set entirely, which today costs nothing and after tightening would silently disable 13 linkers.

**Blast radius of a miss:** the linker does not run, its edges never exist, no `AnalysisRun` is recorded, `skipped_passes` is untouched, and the behavior map is **indistinguishable** from one where the framework is genuinely absent. Silent, total, per-linker.

Language gating was tested as a safer alternative and **refuted by the same data**: subprocess-linker's cost is entirely on kserve and pretix, which both have Python; every repo where ipc-linker ran silent has JavaScript. The linkers' own file globs already language-gate them. (`LinkerActivation` also offers only `language_pairs`, with no single-language field.)

## Why this cannot be validated — the decisive argument

A tightening would be proven by a paired differential on `bakeoff-broad`: run tip and tip+gate over a ≥10-repo cohort including ≥3 where the gated framework is present, diff edge sets per repo, and require **zero** edges present in baseline and absent in gated, attributed to the gated `pass_id`.

That differential can only prove no loss **on repos where detection succeeded**. The failure mode being risked is detection failing on a repo the cohort does not contain — and by construction such a repo produces an empty baseline too, so the differential reads clean. Validating the actual risk needs a **labelled** cohort with ground-truth framework presence independent of `profile.frameworks`, which does not exist. **The change cannot be validated against its own failure mode.** That alone settles it.

## Blocking precondition

No tightening ships before a disclosure channel exists for gate-off: a new axis value (e.g. `activation_gate_closed`) on a carrier that exists **without** an `AnalysisRun` — explicitly not `prerequisite_absent` (see ADR-0056). Building that channel does not by itself authorise tightening.

## Re-evaluation triggers

Each is fireable from ordinary bakeoff output, without performing any gating work.

> **T1 — cost.** On any single survey from any routine bakeoff, strict-silent (`nodes_emitted==0 ∧ edges_emitted==0 ∧ files_analyzed>0`) **Framework-subcategory** linker time reaches **≥2.0%** of that survey's total pass time, or **≥60,000 ms** absolute on one repo. Computable from `analysis_runs` in every survey already written.
>
> **T2 — safety.** Any Framework-subcategory linker among the 25 reports `no_candidate_construct` on ≥8 of 10 cohort repos whose `profile.frameworks` lacks its framework, **and** emits ≥1 edge on ≥1 repo where `profile.frameworks` contains it. Both halves read the already-wired silence axis from the same `survey.json`.
>
> **T3 — detection.** Framework detection stops being manifest-shallow: a producer lands that confirms a framework from source constructs rather than manifests, with a false-negative rate measured on a labelled cohort. Fires from someone else's work.

## What would change this decision

1. **T1 firing.** The 0.166% is 9 repos, one language-family-heavy. A Java/Spring or Rails-heavy cohort could move jackson / kafka-streams / rust-trait off zero. Cheapest falsifier, checkable against any existing bakeoff artifact.
2. **A measured false-negative rate for framework detection below ~1%.** The objection is proportional to that rate, and it is currently unmeasured — this ADR reasons from two documented failure families, not from a rate.
3. **graphql-resolver's apollo-server zero being explained as correct** (WI-dinum). That would clear criterion 3 for the linker carrying 93% of the gateable prize, though it would still fail criteria 2 and 4.
4. **A disclosure channel landing with a demonstrated reader** — a consumer that surfaces gate-offs to a user, proven to fire. Retires the H2 objection, not the validation gap.
5. **Superlinear scaling past pretix.** Measured 0.97% at 2.3M ms; if a 20M-ms monorepo shows silent linker time at 10%, T1's absolute-ms half fires and the cost argument changes shape.

## Filing note

This is bucket 1 per `docs/adr/README.md` — a load-bearing refusal binding future work through a criterion, a precondition and three triggers. It is **not** an audit-findings document: `docs/audits/` is mechanically restricted to the three registry-backed axes (see ADR-0054), and this carries no per-value verdict table. The bakeoff-validation tag does not apply — this PR claims no improvement, changes no behaviour, and asserts a *foregone* prize.

## Related

- [ADR-3aaa](3aaa-architectural-analysis-and-revision-plan.md) — the activation mechanism this declines to exercise further.
- [ADR-3bbb](3bbb-linker-subcategory-restoration.md) — the subcategory vocabulary evaluated as H3.
- [ADR-0054](0054-pass-silence-candidates-unresolved.md) — the disclosure axis evaluated as H1/H2, and the filing constraint.
- [ADR-0056](0056-pass-silence-producerless-values.md) — why `prerequisite_absent` is not the landing value for a closed gate.
