<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0054: `candidates_unresolved` — splitting the pass-silence residue

Status: Accepted (2026-09-16)

| | |
|---|---|
| **Axis** | `AnalysisRun.silence_reason` (`pass-silence-reason`, lightweight per ADR-0024 §4) |
| **Date** | 2026-09-16 |
| **Trigger** | WI-bivim. Draining WI-finij's eleven silent pass-runs left five that scanned files, **found their construct**, and emitted nothing anyway. They landed in `unreported`, which is declared to mean the pass *"did not say why"* — about passes whose bodies had just measured the answer. |
| **Outcome** | **SPLIT.** `candidates_unresolved` is promoted out of the `unreported` residue. `unreported` and `no_candidate_construct` both stay CANONICAL. |
| **Cadence** | First audit on this axis. |

## Context

`derive_silence_reason` holds three integers — `files_analyzed`, `nodes_emitted`, `edges_emitted` — and can return exactly three values. Anything it cannot attribute becomes `UNREPORTED`, declared as the residue *"which makes it COUNTABLE instead of invisible and leaves a later pass to drain it producer by producer"*.

That drain happened. Eleven runs were silent after reading files; eleven bodies now make a positive claim; six were genuinely empty of their construct. The remaining five are the subject here.

## Step 3 — the four leakage tests

Pair: **A = `unreported`**, **B = "construct found, relation not established"**.

| test | outcome | evidence |
|---|---|---|
| **1. Property derivability** | **Does not fire** | `AnalysisRun` carries no candidate count, so the distinction is underivable from the record. `derive_silence_reason` provably cannot separate them: three ints in, three values out. (It *would* become derivable given a `candidates_found` counter — see "What would change this verdict".) |
| **2. Apex/peer overloading** | **FIRES — the diagnostic hit** | `unreported` is declared as an **apex** (the residue that catches the unclassifiable) but consumed as a **peer**: `format_silence_summary` prints it by name beside `no_candidate_files`. Under the apex reading the five are correctly placed; under the peer reading they are **mis-described**, because the peer reading asserts "did not say why" and these passes can say why. |
| **3. Construct vs. relationship** | Does not fire | Neither value is a syntactic-construct label. Both are pass-outcome labels on one dimension. |
| **4. Mechanism vs. category** | Fires weakly, already answered | `unreported` names a disclosure *mechanism* on a *category* axis, but the axis declares and justifies that explicitly. The weak hit reinforces one thing: **a residue whose count never moves is a mechanism wearing a category's clothes.** |

A Test-2 hit alone does not deprecate a *declared* residue — apex-and-peer is what a residue legitimately is, and the axis accepted that knowingly. What the hit establishes is narrower and sufficient: **leaving a characterizable population inside the residue is what makes the two readings diverge.** The remedy is to remove that population, not to retire `unreported`.

## Per-value verdicts

| value | verdict | rationale |
|---|---|---|
| `unreported` | **CANONICAL** | Stays, with its population narrowed. CANNOT-DETERMINE remains a required state and must never collapse into `""` (NOT APPLICABLE). |
| `no_candidate_construct` | **CANONICAL** | Unchanged, and explicitly **not** the home for these five: its declared text is *"none contained the construct this pass looks for"*, and the body's own measurement says otherwise. Stamping it would make the field assert the negation of a fact the same call just computed. |
| `candidates_unresolved` | **SPLIT (new)** | The pass found its construct and carried none through resolution. |

### On the name

The rejected framing — "an import with no export, a binding with no symbol" — describes the **repository**, and a value named for it (`relation_absent`, `no_candidate_counterpart`) would over-claim. A pass cannot distinguish the counterpart being genuinely absent (correct no-op) from its own matcher failing (recall defect), and the axis says those *"need opposite responses"*.

The over-claim is real, not theoretical. Four of the five are clean counterpart-absences, but **`ipc` is not**: `if not channel: continue` rejects a candidate on a quality filter with no pairing attempted, while an empty `receivers` list is a genuine missing counterpart. Two shapes at one site.

`candidates_unresolved` names the **stage** (resolution) and its **subject** (candidates), which is what is certain across all five. Against the axiom — *"names WHY a pass produced no symbols and no edges; never what the pass would have produced, nor what it cost"* — it names neither the output nor a counterfactual. `relation_unresolved` was rejected precisely because a linker's output *is* relations.

## Costing

**Lightweight.** ADR-0024's seven-step Declaration workflow does not apply: an existing axis is being reused. Verified absent and not required: no `*Spec` dataclass, no `*_on_axis` accessor, no drift script, no `docs/concept-axes.md` section, no entry in `check-producer-axis-coherence`.

**The consumer already exists and is vocabulary-generic**, which is what separates this from `prerequisite_absent`: `summarize_silence` counts by whatever string it finds and keeps out-of-vocabulary values under their own name; `format_silence_summary` prints every reason by name; `emit_silence_summary` is wired at `cli.py`. The value is read back on day one with zero consumer work.

**One line produces it, plus four that the audit's own prediction got wrong.** The non-empty branch of the producer helper returns the new value instead of `""`, with **zero edits at the eleven call sites** — the chokepoint guard (`if _derived == "" or not result.run.silence_reason`) clears a body claim on a pass that emitted, so a body may speak without knowing its own output.

That alone was predicted to take `unreported` to 0. **Measured, it took it to 2.** Four return paths reach `return` without calling the helper at all, and this audit first recorded them as a *latent* residual that "will appear on a JS/TS or Solidity corpus". Two of the four were firing on the hypergumbo self-survey while the prediction was being written: `wasm-bindgen` returns early when no Rust exports exist and its WRAPPER then scans 14 files, so `files_analyzed` was non-zero while the body never spoke; `message-dispatch` got *past* its bail with both sides non-empty and matched no pairs. They are now closed as a class rather than filed — a fix keyed to the instances that happened to fire would have left the other two waiting for a corpus to expose them.

`wasm-bindgen`'s claim is made *before* the wrapper's second scan, which is safe for a reason worth recording: that scan emits a symbol per load it finds, so if it finds any the pass is not silent and the chokepoint clears the claim. The guard is what makes an early claim legitimate.

## The honest cost, stated rather than implied

`unreported` goes **5 → 0** on the hypergumbo self-survey (final distribution: `no_candidate_files=43, no_candidate_construct=7, candidates_unresolved=4`). That reads as "fully drained" when 53 of the 64 linker modules carry no body claim at all and would land in `unreported` on a corpus where they are active. **A zero there measures the corpus, not completion** — this project's recurring ABSENT ≠ EMPTY defect, applied to this audit's own verdict. Mitigated by removing the *"the `unreported` count is the size of the remaining work"* gloss from the field comment, and by the summary line now showing `no_candidate_construct=6, candidates_unresolved=5` where it showed `unreported=5` — strictly more information, with the zero arriving beside visible non-zero siblings.

## Re-evaluation trigger

Declare has its own failure mode — the new value becomes a second undrained residue — so it carries a trigger.

> **T1.** A silence summary on any corpus shows `candidates_unresolved` at or above **60%** of all silent pass-runs. It is absorbing rather than differentiating, and the population needs splitting again (candidate-rejected-by-filter vs. counterpart-absent).
>
> **T2.** A run stamped `candidates_unresolved` is traced to a pass whose counterpart **is present in the same behavior map**. That is a recall defect wearing a correct-no-op label, and the value needs a sibling that says so.

Both are fireable independently of the work they gate. T1 reads the stderr summary already wired into every `survey` run, including every bakeoff. T2 reads the behavior map itself. Contrast `INV-hujog`, whose trigger waits on a value only the declined wire-up can emit and therefore cannot fire.

## Why this is an ADR and not an audit-findings document

It was first written as `docs/audits/0019`. **The audit-findings format cannot carry it**, and the reason lives in the enforcing code rather than in `docs/audits/README.md`: `audit_findings._REGISTRIES` binds exactly three axes — `Edge.edge_type`, `Symbol.kind`, `Edge.evidence_type` — each heavyweight and registry-backed, with `*Spec` objects the validator mechanically re-checks per row. `AnalysisRun.silence_reason` is a **lightweight catalog-derived** axis (ADR-0024 §4): plain string constants, no spec tuple, so it cannot be named in the `kind: audit_verdicts` block at all. `VALID_VERDICTS` is likewise a closed frozenset of CANONICAL / FOLD / DEPRECATE-NO-FOLD, admitting neither the SPLIT below nor any sibling.

`test_audit_findings.py::test_live_tree_audit_findings_docs_parse_and_validate` caught this. The prose rubric did not, because `docs/adr/README.md` asks only "decision present?" and says nothing about which axes the audit *mechanism* supports. **The operative rule, recorded here because it is written nowhere else: `docs/audits/` is for the three registry-backed axes; a per-value verdict on a lightweight axis files elsewhere.** `docs/surveys/python-stdlib-module-io-enumeration.md` hit the same validator on its own first attempt at `docs/audits/0019-…` and filed as a survey; this one carries a decision, so it files as an ADR.

Bucket 1 is also correct on the rubric's own terms, independently of the mechanical constraint: a value is declared and a prior ruling is overturned.

## A note on this document's verdict vocabulary

`README.md` records that the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy is shaped for value-folding audits, and asks that conclusions which do not slot in **propose a sibling format rather than shoehorn**. Two rows here slot cleanly; the third does not, because the trichotomy has FOLD (many → one) and no inverse.

This document therefore uses one new term, **SPLIT** — *a residue value's population is heterogeneous and part of it is promoted to its own name* — defined as FOLD's inverse. It is used **descriptively, here only**: `VALID_VERDICTS` is closed and SPLIT is not a member, so no document under `docs/audits/` may use it until that enum is extended.

ADR-0024's own refactor trigger (*"if a future axis declaration reveals that the verdict scheme genuinely varies per axis… promote this subsection into a standalone `docs/family-audit-methodology.md`"*) has now fired **once**, on a non-`Edge`/`Symbol` host dataclass. It should **not** promote on N=1. Threshold for promotion: a second non-`Edge`/`Symbol` axis needing a non-trichotomy verdict.

## What would change this verdict

1. **The value is under-specified.** If on a polyglot corpus `candidates_unresolved` is dominated by ipc-style *candidate rejection* rather than pairing failure, it is two states and the body should report which. Settled by instrumenting the eleven sites to count candidates-entering-resolution against candidates-filtered.
2. **The producer should be a counter.** An `Optional[int]` `candidates_found` on `AnalysisRun` would let `derive_silence_reason` compute both values and delete the helper, and would carry magnitude ("found 412 bindings, resolved 0"). This does not change the vocabulary verdict — the function must still *return* something — so it is a better producer, not a competing verdict, and it costs a schema bump of its own. **It must be `Optional[int]`, never `int = 0`**: a zero default read as a positive claim is exactly what had eight linkers reporting `files_analyzed=0` while reading 14–834 files (WI-finij).
3. **Unmeasured:** whether this shape occurs on **analyzer** passes. The structural argument says no (an analyzer emits one symbol per construct found, so finding implies emitting), but it was reasoned, not measured. A polyglot survey grouping analyzer runs by `silence_reason` settles it.
