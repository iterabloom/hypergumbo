<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Twenty-pass dogfood procedure

A soup-to-nuts, vendor-neutral procedure for running a 20-pass dogfooding tranch on hypergumbo (or any comparable tool that ships a CLI, an analysis substrate, and a tracker). Integrates the strengths of the passes-1-20 and passes-21-40 campaigns: 1-20's up-front two-axis consolidation criteria + per-cluster confidence levels + rejected-clusters log, 21-40's F-number provenance + INVALIDATION discipline + mid-stream audit posture. Uses sub-agent orchestration so the operator does not have to babysit a tmux session.

## Purpose & scope

A "tranch" is a contiguous block of 20 dogfooding passes against the same substrate (or substrate family). The procedure produces:

- An immutable per-pass raw-observations record (lab notebook stanzas with F-numbered findings).
- A two-axis consolidated cohort (methodology-axis + causal-axis) with per-cluster confidence and a rejected-clusters log.
- A blind-judge severity panel (default 3-judge ensemble; tunable down to 1-judge for cost-constrained runs).
- A cluster-aware trend report (carbon-dating Methods A / B / C: earliest member / latest member / 1-n distributed).
- A retrospective audit comparing the tranch's pre-registered prediction to its measured outcome.
- A carry-forward queue for the next tranch.

Use this procedure when you want a methodology-comparable dogfooding tranch — i.e., when you expect to compare its trend report to a prior tranch's, or treat it as a baseline for future ones. For one-off audit sweeps that don't need cross-tranch comparison, the lighter-weight `self-analysis-dogfooding-playbook.md` is appropriate.

## When NOT to use

- **Tranch-size mismatch.** Tranches smaller than ~10 passes don't yield enough buckets to produce a meaningful trend signal; larger than ~30 passes blow past the parent agent's working-context budget even with sub-agent chunking. Stay near 20.
- **Substrate is mid-flight changing.** If the codebase is being actively refactored during the tranch, per-pass findings will drift for reasons unrelated to discovery dynamics, and the consolidation work will be wasted. Freeze a substrate snapshot first (see Phase 0 step 2).
- **No prior baseline to compare against AND no plan to compare against future tranches.** The procedure's overhead is justified by cross-tranch comparison; without that audience, run the lighter playbook.

## Architecture: sub-agent orchestration

The parent agent (the one reading this playbook) owns the phase loop, the tranch state file, and the final trend report. It does NOT directly run the 20 passes — it spawns sub-agents in chunks of `--compact-every` (default 2) and aggregates their return summaries.

Why sub-agent chunks instead of one long-running session:
- **Context hygiene.** A single pass loads the substrate (~100 MB JSON), runs probe scripts, queries the tracker, and writes a lab notebook stanza. Two passes' worth of that bloats context to where the next pass's reasoning is compromised. Sub-agent boundaries are clean context resets.
- **Vendor neutrality.** Every modern CLI agent supports sub-agent spawning (Claude Code: `Agent` tool; Codex CLI / Cursor / Gemini: equivalents in same family). The procedure does not assume a specific tool name; it assumes the abstraction.
- **Independent verifiability.** Sub-agents for blind judging see only the rubric + the deconfounded card, not the discovery context or trend goal. This is the structural countermeasure to the same-actor bias that bit the 21-40 campaign.

For vendors that lack sub-agent spawning, Appendix A documents a tmux-based fallback that uses the `scripts/agent-supervisor` VENDOR_TABLE.

## Tunable parameters

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `--compact-every` | 2 | 1-5 | Chunk size for Phase 1 sub-agents. Default 2 matches the empirical compaction cadence from the 21-40 campaign. |
| `--judge-count` | 3 | 1-5 | Blind-judge ensemble size in Phase 4. 3 matches passes 1-20; 1 matches passes 21-40 (cheaper, no inter-rater variance check). |
| `--mid-tranch-pass` | 10 | 5-15 | Pass number to insert the Phase 2 interim audit. Default at half-tranch. |
| `--tranch-size` | 20 | 10-30 | Total pass count. Above 30 the procedure starts losing coherence; below 10 the trend signal is too noisy. |
| `--substrate-policy` | snapshot | snapshot \| regen-each \| regen-on-suspicion | When to regenerate the analysis substrate. snapshot = once, at Phase 0. regen-each = every pass (expensive). regen-on-suspicion = whenever F39.A1-style multi-pass negative findings need fresh-substrate verification. |

## Phase 0 — Pre-tranch setup

Output: `${TRANCH_DIR}/tranch_state.json` populated; substrate frozen; pre-registered prediction committed; rubric locked.

### Step 0.1 — Pick the tranch directory

```bash
TRANCH_DIR=~/hypergumbo_lab_notebook/dogfood_tranch_<NN>  # NN = tranch ordinal
mkdir -p "$TRANCH_DIR"
```

The directory holds all tranch artifacts: state file, per-pass raw observations, consolidation outputs, blind-judge cards and verdicts, trend report, retrospective. Never overwritten across tranches.

### Step 0.2 — Freeze the substrate

If `--substrate-policy=snapshot` (default), generate the substrate once and lock it:

```bash
hypergumbo run . --out "${TRANCH_DIR}/substrate.json"
sha256sum "${TRANCH_DIR}/substrate.json" > "${TRANCH_DIR}/substrate.sha256"
```

If `regen-each` or `regen-on-suspicion`, document the substrate-generation command in the tranch state file; substrate files go under `${TRANCH_DIR}/substrates/pass_NN.json` so cross-pass comparability is preserved.

### Step 0.3 — Write the substrate guide

Copy or generate `${TRANCH_DIR}/substrate_guide.md` covering: severity rubric (1=Cosmetic ... 5=Severe), status reconciliation rules, substrate file references. This locks the rubric so it can't drift mid-tranch. The 5-tier rubric and 6-field card format from `dogfooding_blind_judge_primer.md` is the proven baseline; reuse it unless you have a documented reason to diverge.

### Step 0.4 — Commit the meta-criterion verbatim

In `${TRANCH_DIR}/meta_criterion.txt`, write:

```
If two findings are actually two symptoms of one underlying problem,
then that underlying problem should be what gets judged, not the two
symptoms individually.
```

This is the load-bearing user-provided rule from the 2026-05-29 baseline campaign. It governs all consolidation work in Phase 3.

### Step 0.5 — Pre-register a prediction

In `${TRANCH_DIR}/prediction.md`, write:
- Predicted bucket-level Σ-severity at end-of-tranch (one number per 5-pass bucket).
- Predicted singleton/cluster split.
- Predicted Significant+Severe percentage.
- Brief rationale.

Pre-registration forces future-you to explain a divergence rather than rationalize a confirmation. Without it, end-of-tranch interpretation drifts toward the agent's narrative prior.

### Step 0.6 — Initialize tranch state

```json
{
  "tranch_id": "<NN>",
  "tranch_dir": "<absolute path>",
  "vendor": "<claude-code|codex-cli|cursor|gemini-cli>",
  "tranch_size": 20,
  "compact_every": 2,
  "judge_count": 3,
  "mid_tranch_pass": 10,
  "substrate_policy": "snapshot",
  "first_pass_number": <N+1>,
  "last_pass_number": <N+20>,
  "current_pass": <N+1>,
  "phase": "discovery",
  "passes_complete": [],
  "checkpoint_complete": false,
  "consolidated_complete": false,
  "judged_complete": false,
  "retrospective_complete": false,
  "prediction_file": "prediction.md",
  "meta_criterion_file": "meta_criterion.txt",
  "substrate_guide_file": "substrate_guide.md",
  "carry_forward_in": "<path to prior tranch's carry_forward.md or null>",
  "started_at_utc": "<ISO 8601>"
}
```

Write to `${TRANCH_DIR}/tranch_state.json`. All sub-agents read this file as their first action; the parent updates it after every sub-agent return.

## Phase 1 — Discovery (per-pass sub-agent chunks)

Output: `${TRANCH_DIR}/pass_NN.md` per pass (immutable after written), `${TRANCH_DIR}/pass_NN.complete` sentinel per pass, `passes_complete` array in tranch state.

### Step 1.1 — Chunk loop

For each chunk of `--compact-every` passes (default 2), spawn a Phase 1 sub-agent with this prompt template:

```
You are a Phase 1 discovery sub-agent for dogfooding tranch ${TRANCH_ID}.
Read ${TRANCH_DIR}/tranch_state.json first. You will run passes
${chunk_start} through ${chunk_end} against substrate ${substrate_file}.

For each pass:
  1. Pick a probe class. Use the substrate guide's probe-class catalog.
     Avoid probe classes already saturated by prior passes in this tranch
     (see passes_complete in tranch state for prior pass summaries).
  2. Execute the probe. Capture all command output to files under
     /tmp/pass_${pass_n}_*.log per the output-capture-long-running
     playbook.
  3. Write findings with F-numbers: F${pass_n}.A1, F${pass_n}.A2, etc.
     Each finding gets:
       - A 1-2 sentence headline.
       - The substrate evidence (file:line + values, OR command + output).
       - A provisional KIND tag (INDEPENDENT / AUDIT-AXIS / EXTENSION /
         CORRECTION / POINTER-ONLY / INVALIDATION). Do NOT load-bear on
         this tag — it is provisional and will be revisited in Phase 3.
       - A provisional fold/standalone hint. Default to STANDALONE when
         uncertain. Default does NOT bind Phase 3 consolidation.
       - For any substantive NEGATIVE finding (something is absent/zero/
         broken) that conceptually reproduces a finding from a prior pass
         in this tranch, mark it INVALIDATION-RISK and trigger fresh-
         substrate regeneration before amplifying the claim. This is the
         F39.A1 multi-pass mismeasurement countermeasure.
  4. Write the pass writeup to ${TRANCH_DIR}/pass_${pass_n}.md as
     immutable lab-notebook stanzas.
  5. Touch ${TRANCH_DIR}/pass_${pass_n}.complete to signal completion.

Return a 200-word summary covering:
  - Pass numbers run.
  - Headline findings (F-numbers + 1-line each).
  - INVALIDATION-RISK flags raised (if any) and how resolved.
  - Probe classes still un-sampled, suggested for next chunk.
DO NOT return the full pass writeups — they live in the per-pass files.

When done, exit. The parent agent will spawn the next chunk after
absorbing your summary into tranch_state.json.
```

### Step 1.2 — Parent aggregation

After each sub-agent returns, the parent:
1. Reads the chunk summary from the sub-agent's return value.
2. Updates `tranch_state.json`: `current_pass` advances, `passes_complete` array gets each pass + its 1-line headline.
3. If `--mid-tranch-pass` was just reached, transitions to Phase 2 (next chunk is the audit chunk).
4. Otherwise spawns the next Phase 1 chunk.

### Step 1.3 — Fresh-substrate verification protocol

When an INVALIDATION-RISK flag is raised, the sub-agent's response must include either:
- "verification ran: substrate regenerated at <path>; finding reproduced" — finding proceeds as substantive, NOT as INVALIDATION.
- "verification ran: substrate regenerated at <path>; finding did NOT reproduce" — finding gets KIND=INVALIDATION and the previously-cited prior pass's finding gets marked-for-review in tranch state. Phase 3 consolidation will decide whether to invalidate the prior finding or keep it as a methodology-calibration record.

This is the procedure that catches multi-pass false-claim chains before they amplify into 4-pass-deep mistakes (the F34→F37 chain in the 21-40 campaign).

## Phase 2 — Mid-tranch checkpoint

Output: `${TRANCH_DIR}/midtranch_audit.md`; `tranch.checkpoint` sentinel; any pass writeups annotated with KIND-tag ratchets.

After pass `--mid-tranch-pass` completes (default pass 10 of the 20-pass tranch), spawn a Phase 2 sub-agent:

```
You are the Phase 2 mid-tranch audit sub-agent for tranch ${TRANCH_ID}.
Read ${TRANCH_DIR}/tranch_state.json and every ${TRANCH_DIR}/pass_NN.md
file for completed passes.

Your job is to catch over-folding or over-splitting in the first half
of the tranch, BEFORE patterns amplify across the second half. Do NOT
attempt full consolidation — that is Phase 3 work. Phase 2 is a check.

For each completed pass:
  1. Re-evaluate every finding's provisional KIND tag. Promote
     EXTENSION → STANDALONE if you find the finding is actually a
     distinct invariant. Demote STANDALONE → EXTENSION if you find the
     finding is genuinely a sibling of a prior one.
  2. Cross-check for INVALIDATION-RISK findings that were not flagged
     at finding-time. Apply the same fresh-substrate verification
     protocol as Phase 1 if you find any.
  3. Flag pattern-amplification risks: any claim that has been re-
     asserted across 3+ passes without fresh-substrate verification.

Write findings to ${TRANCH_DIR}/midtranch_audit.md with sections:
  - KIND ratchets (per-pass diffs).
  - New INVALIDATION-RISK flags.
  - Pattern-amplification warnings.
  - Recommendation on whether second-half probe direction should adjust.

Touch ${TRANCH_DIR}/tranch.checkpoint. Return a 150-word summary.
```

After Phase 2, the parent continues Phase 1 with the remaining passes. Sub-agents in the second half read `midtranch_audit.md` as part of their context.

## Phase 3 — End-of-tranch consolidation

Output: `${TRANCH_DIR}/classification.md` (methodology-axis), `${TRANCH_DIR}/clusters.md` (causal-axis), `${TRANCH_DIR}/rejected_clusters.md`, `tranch.consolidated` sentinel.

Phase 3 runs TWO sub-agents in sequence — not one. Methodology-axis dedup and causal-axis dedup answer different questions; conflating them is what produced the 21-40 over-fold. The order is methodology-first because audit-sweep collapse changes the unit-count that causal clustering operates on.

### Step 3.1 — Methodology-axis sub-agent

```
You are the Phase 3a methodology-axis dedup sub-agent for tranch
${TRANCH_ID}. Read ${TRANCH_DIR}/tranch_state.json, every
${TRANCH_DIR}/pass_NN.md, and ${TRANCH_DIR}/midtranch_audit.md.

For each finding (every F-number across every pass), assign a final
KIND from the same 6-way taxonomy used at finding-time:
  - INDEPENDENT — single investigation; no co-emitted siblings.
  - AUDIT-AXIS — one of N rows produced by a single enumerative sweep.
    Mark with the sweep identifier.
  - EXTENSION — explicitly extends an earlier finding's scope.
  - CORRECTION — revises or replaces an earlier finding.
  - POINTER-ONLY — no local information; refers to another row.
  - INVALIDATION — negates a prior finding via fresh-substrate evidence.

For each finding, also assign a confidence level (HIGH / MED / LOW)
to the KIND assignment. HIGH = the kind is explicit in the lab
notebook stanza. MED = inferable from context. LOW = judgment call.

For trend analysis, AUDIT-AXIS rows collapse to 1 finding per sweep
(first row of the sweep is the representative). EXTENSION and
CORRECTION rows fold into their parent. INVALIDATION rows do not
contribute to the new-finding count but are preserved as methodology-
calibration records.

Write the classification to ${TRANCH_DIR}/classification.md with
session-by-session tables matching the 1-20 baseline format. Return a
150-word summary covering: AUDIT-AXIS sweep count, EXTENSION/
CORRECTION counts, INVALIDATION count, low-confidence cases.
```

### Step 3.2 — Causal-axis sub-agent

```
You are the Phase 3b causal-axis clustering sub-agent for tranch
${TRANCH_ID}. Read ${TRANCH_DIR}/tranch_state.json, every
${TRANCH_DIR}/pass_NN.md, ${TRANCH_DIR}/midtranch_audit.md, and
${TRANCH_DIR}/classification.md (Phase 3a output).

The meta-criterion is in ${TRANCH_DIR}/meta_criterion.txt: "If two
findings are actually two symptoms of one underlying problem, then
that underlying problem should be what gets judged, not the two
symptoms individually."

Cluster findings that share a single underlying root cause. The
question is whether fixing one would fix the other. Two findings that
merely touch the same field, command, or subsystem do NOT
automatically cluster.

Apply HIGH / MED / LOW confidence to each cluster:
  - HIGH — same emitter path / same schema field / single identifiable bug.
  - MED — shared structural pattern; semi-independent code paths.
  - LOW — thematic link; fix surface plausibly different.

Default to NON-clustering if you would write LOW. This is the
load-bearing default from the 1-20 baseline.

For each cluster, write:
  - Members (F-numbers).
  - Root cause (1-3 sentences).
  - Confidence with justification.
  - Earliest pass (the carbon-dating Method A anchor).
  - Why-clustered (1 sentence: what unified fix would address it).

Write to ${TRANCH_DIR}/clusters.md.

CRITICAL: Maintain a separate ${TRANCH_DIR}/rejected_clusters.md log
of tempting consolidations you considered and rejected. For each
rejected cluster:
  - The members you considered grouping.
  - Why you rejected the grouping (1-2 sentences naming the fix-surface
    divergence).
  - Where each member ended up instead (singleton or which other cluster).

The rejected-clusters log is what makes Phase 3 audit-able. Without
it, no future reader can second-guess your decisions.

Touch ${TRANCH_DIR}/tranch.consolidated. Return a 200-word summary.
```

### Step 3.3 — Card generation

After both consolidation sub-agents complete, the parent spawns a card-writer sub-agent. CRITICAL: this sub-agent must NOT see the trend-analysis goal, the prediction, or the prior-tranch baseline. It sees only the consolidated findings and the substrate guide. This is the structural countermeasure to the same-actor bias.

```
You are the Phase 3c card writer for tranch ${TRANCH_ID}. You are
deliberately blinded from the trend-analysis context. Do NOT read
${TRANCH_DIR}/prediction.md or ${TRANCH_DIR}/tranch_state.json's
prior-tranch fields.

Read only: ${TRANCH_DIR}/substrate_guide.md (for the rubric),
${TRANCH_DIR}/classification.md (for the methodology-axis unit count),
${TRANCH_DIR}/clusters.md (for cluster identity), and the per-pass
${TRANCH_DIR}/pass_NN.md files for finding detail.

For each consolidated finding (clusters become one card per cluster;
INDEPENDENT singletons become one card each; EXTENSION/CORRECTION
folded findings do NOT become cards), write a deconfounded card in
the 6-field format:

  Card ID: anon-card-${TRANCH_ID}-NNN (NNN zero-padded).
  Subject: 1-line topic.
  Mechanic: how the defect produces the symptom.
  Expectation: what the substrate would look like if correct.
  Scope: which surface / kind / command is affected, with magnitude.
  Exposure: who hits the defect under what circumstances.
  Consequence: what downstream decisions get corrupted.

Cards must contain NO references to:
  - Tracker IDs (INV-*, WI-*, META-*).
  - Pass numbers (s1, s2, ..., pass-21, etc.).
  - F-numbers (F21.1, etc.).
  - The word "audit" in its hypergumbo-methodology sense.

Write cards to ${TRANCH_DIR}/cards_blinded.md and the unblinding key
to ${TRANCH_DIR}/mapping_anon_to_source.tsv. Run a leak-check grep
for the forbidden patterns and write the result to
${TRANCH_DIR}/leak_check.md.

Return a 100-word summary: card count, leak-check result, any cards
flagged for re-review.
```

## Phase 4 — Blind judging

Output: `${TRANCH_DIR}/verdicts_judge_N.jsonl` per judge; `${TRANCH_DIR}/aggregate_summary.md`; `tranch.judged` sentinel.

### Step 4.1 — Fan-out judge sub-agents

For each card × each judge in the `--judge-count` ensemble (default 3), spawn a judge sub-agent in parallel:

```
You are a Phase 4 blind judge sub-agent for tranch ${TRANCH_ID},
assigned to card ${CARD_ID}, judge index ${JUDGE_INDEX}.

Read ONLY:
  - ${TRANCH_DIR}/substrate_guide.md (severity rubric).
  - The single card in ${TRANCH_DIR}/cards_blinded.md matching ${CARD_ID}.

Do NOT read other cards. Do NOT read tranch_state.json. Do NOT read
the mapping file. Do NOT read any pass writeup. You are deliberately
blind to context.

Score the card on the 1-5 severity scale per the rubric. Emit a JSON
object on stdout:
  {"card_id": "...", "judge_index": N, "severity": INT,
   "severity_label": "Cosmetic|Minor|Moderate|Significant|Severe",
   "rationale": "<2-4 sentence justification anchored in the rubric>"}

Do not write to any file. The orchestrator captures your stdout.
```

The parent collects each judge's verdict and writes `verdicts_judge_${JUDGE_INDEX}.jsonl` for each judge.

### Step 4.2 — Reconciliation rule

Pre-commit before spawning the judges: if `--judge-count >= 2`, the reconciliation rule is "if judges spread >= 2 tiers, headline = median, spread is flagged in `aggregate_summary.md`." For `--judge-count == 1`, no reconciliation; the panel README MUST note the missing inter-rater check.

### Step 4.3 — Aggregate

Parent agent (or a Phase 4 aggregator sub-agent) joins verdicts + mapping_anon_to_source.tsv, computes the distribution, identifies Severe+Significant cards by source row, and writes `${TRANCH_DIR}/aggregate_summary.md` matching the layout of the 1-20 baseline's `dogfooding_blind_judge_primer.md` output sections.

Touch `${TRANCH_DIR}/tranch.judged`.

## Phase 5 — Trend report + retrospective

Output: `${TRANCH_DIR}/trend_cluster_aware.md`, `${TRANCH_DIR}/retrospective.md`, `tranch.retrospective` sentinel.

### Step 5.1 — Trend report

The parent runs (or spawns a sub-agent that runs) a trend-report builder modeled on `~/hypergumbo_lab_notebook/build_dogfooding_trend_2140.py`:

Inputs:
- `${TRANCH_DIR}/mapping_anon_to_source.tsv`.
- `${TRANCH_DIR}/clusters.md` (for cluster member-pass derivation).
- `${TRANCH_DIR}/verdicts_judge_*.jsonl` (joined into per-card severity).

Outputs (in `${TRANCH_DIR}/trend_cluster_aware.md`):
- Per-pass COUNT table (Methods A / B / C).
- Per-pass MEAN BLIND SEVERITY table.
- Per-pass SEVERITY SUM table (the column the 1-20 baseline didn't ship; included by default here).
- Count + severity summary metrics (slope, early/late means, late-minus-early delta).
- Sanity checks (Σ over methods agrees; singletons identical under A/B/C; etc.).
- Cluster inventory.
- Bucket-level 5-pass Σ-severity rollup for cross-tranch comparison.

### Step 5.2 — Retrospective

Spawn a retrospective sub-agent:

```
You are the Phase 5 retrospective sub-agent for tranch ${TRANCH_ID}.

Read:
  - ${TRANCH_DIR}/prediction.md (your pre-registered prediction).
  - ${TRANCH_DIR}/trend_cluster_aware.md (the measured outcome).
  - ${TRANCH_DIR}/midtranch_audit.md (mid-tranch KIND ratchets).
  - ${TRANCH_DIR}/classification.md and ${TRANCH_DIR}/clusters.md
    (the consolidated cohort).
  - ${TRANCH_DIR}/rejected_clusters.md (the consolidation decisions log).

Produce a retrospective covering:
  1. Pre-registered prediction vs measured outcome. Numeric divergence
     per bucket. Honest assessment of whether the prediction shape was
     right.
  2. KIND tag stability: how many real-time tags survived the
     mid-tranch audit and Phase 3 reclassification? Where did real-time
     tagging fail?
  3. Rejected-clusters scoring: did the rejected-clusters log catch any
     cluster you would have collapsed under one-shot consolidation?
     Name specific cases.
  4. Sycophancy review: did the consolidation work lean toward
     producing a prediction-matching outcome? Cite specific decisions
     that could have gone either way and explain why they went the way
     they did.
  5. Carry-forward to the next tranch: methodology questions raised,
     calibration adjustments suggested, probe classes saturated vs
     fresh.

Write to ${TRANCH_DIR}/retrospective.md. Touch
${TRANCH_DIR}/tranch.retrospective. Return a 200-word summary.
```

## Phase 6 — Carry-forward

Output: `${TRANCH_DIR}/carry_forward.md`; tranch state file finalized; cross-tranch comparison row appended to `~/hypergumbo_lab_notebook/dogfood_tranchen_index.md` (if it exists).

The parent agent writes:

- `${TRANCH_DIR}/carry_forward.md` with: open methodology questions, severity rubric calibration adjustments, saturated probe classes, recommended Phase 0 changes for the next tranch.
- An entry in `~/hypergumbo_lab_notebook/dogfood_tranchen_index.md` summarizing the tranch: ordinal, date range, headline bucket-Σ-severity, judge count, cohort count, link to retrospective.
- The next tranch's Phase 0 sub-agent should be pointed at this `carry_forward.md` via the `carry_forward_in` field in its tranch state file.

## Tranch state file schema

Single source of truth for tranch state. Every sub-agent reads this file as its first action. The parent updates it after every sub-agent return.

```json
{
  "tranch_id": "string (ordinal)",
  "tranch_dir": "string (absolute path)",
  "vendor": "string",
  "tranch_size": "int (10-30)",
  "compact_every": "int (1-5)",
  "judge_count": "int (1-5)",
  "mid_tranch_pass": "int",
  "substrate_policy": "snapshot | regen-each | regen-on-suspicion",
  "first_pass_number": "int",
  "last_pass_number": "int",
  "current_pass": "int",
  "phase": "discovery | midtranch_audit | consolidation | judging | retrospective | carry_forward | complete",
  "passes_complete": [
    {"pass_number": "int", "headline": "string", "invalidation_risks": ["string"]}
  ],
  "checkpoint_complete": "bool",
  "consolidated_complete": "bool",
  "judged_complete": "bool",
  "retrospective_complete": "bool",
  "prediction_file": "string (relative path)",
  "meta_criterion_file": "string",
  "substrate_guide_file": "string",
  "carry_forward_in": "string (absolute path or null)",
  "started_at_utc": "string (ISO 8601)",
  "last_update_utc": "string (ISO 8601)"
}
```

## Sentinel file convention

Sentinels are zero-byte files that signal phase or step completion. The parent agent watches for them; sub-agents touch them as their last action.

| Sentinel | Written by | Meaning |
|---|---|---|
| `pass_NN.complete` | Phase 1 sub-agent | Pass NN's lab notebook stanza is committed |
| `tranch.checkpoint` | Phase 2 sub-agent | Mid-tranch audit is complete |
| `tranch.consolidated` | Phase 3 sub-agent | Two-axis dedup is complete |
| `tranch.judged` | Phase 4 aggregator | All verdicts collected, aggregate summary written |
| `tranch.retrospective` | Phase 5 sub-agent | Retrospective is written |
| `tranch.complete` | Parent agent | All phases done; carry-forward written |

Sentinels are advisory, not load-bearing. If a sub-agent crashes after writing its work but before touching the sentinel, the parent can detect the work artifacts directly and proceed.

## Vendor neutrality

The procedure assumes the agent vendor exposes:
1. **A way to spawn a sub-agent** with a prompt and capture its return value. Claude Code uses the `Agent` tool. Other vendors expose equivalent abstractions (and any vendor that doesn't expose this is unlikely to be usable for any nontrivial dogfooding workflow).
2. **A way to read and write local files** (assumed universal across CLI agents).
3. **A way to run shell commands** (assumed universal).
4. **Optionally, a way to spawn multiple sub-agents in parallel** for the Phase 4 fan-out. Without parallelism, the judging step runs serially (slower but functionally equivalent).

For vendors without sub-agent spawning, see Appendix A.

## Recovery & failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Sub-agent crashes mid-chunk | Parent times out waiting for return | Re-spawn the sub-agent with the same prompt; the tranch state file lets it resume at the failed pass |
| Pass writeup partially written | Lab notebook file exists but no sentinel | Phase 1 sub-agent's first action is to check for incomplete writeups and continue them |
| INVALIDATION-RISK finding's fresh-substrate verification fails | Substrate regeneration command errors | Mark the finding KIND=INDETERMINATE in `tranch_state.json`; Phase 3 handles INDETERMINATE as a manual-review queue |
| Phase 3 sub-agent diverges from brief (e.g., consolidates everything) | Cluster count drops > 50% from raw F-number count | Re-spawn with tightened prompt; if it diverges again, fall back to single-axis dedup and document in retrospective |
| Judge verdicts have no clear majority | Phase 4 aggregator detects spread >= 2 tiers in >= 25% of cards | Spawn `--judge-count` additional judges; if still spread, escalate to operator and pause tranch |
| Parent's own context fills up before Phase 5 | Parent agent's `/compact` equivalent gets close to limit | Parent invokes `/compact` (or vendor equivalent) between phases; tranch state file persists across compactions |
| Token budget exceeded | Configurable; checked at phase boundaries | Halt at current phase, mark tranch partial, write a partial-retrospective covering completed phases only |

## Cost & budget estimation

Per-tranch token estimates assuming Opus-class models throughout:

| Phase | Sub-agent count | Tokens per sub-agent | Phase total |
|---|---|---|---|
| 0 (setup) | 0 (parent only) | ~50K parent | 50K |
| 1 (discovery) | 10 (20 passes / 2-per-chunk) | ~400K each | 4M |
| 2 (mid-tranch audit) | 1 | ~200K | 200K |
| 3 (consolidation) | 3 (methodology + causal + card-writer) | ~250K each | 750K |
| 4 (judging) | judge_count × card_count | ~80K each | 80K × (J × C) |
| 5 (retrospective) | 1 | ~150K | 150K |
| 6 (carry-forward) | 0 (parent only) | ~50K parent | 50K |

For a default tranch (J=3, C≈60), Phase 4 alone is ~14M; full tranch ~20M. Comparable to the passes-1-20 baseline's ~18M for 198 verdicts. Cost-optimization options: drop to J=1 (~5M total) at the cost of losing inter-rater variance; drop card-writer separation (~−500K) at the cost of reintroducing same-actor bias.

## Appendix A — tmux fallback for vendors without sub-agents

If the vendor doesn't expose sub-agent spawning, the procedure falls back to the `scripts/agent-supervisor` VENDOR_TABLE machinery. The operator runs:

```bash
scripts/agent-supervisor dogfood-tranch start \
    --tranch-dir <path> \
    --first-pass <N+1> \
    --compact-every 2 \
    --judge-count 3 \
    --vendor <vendor-name>
```

(This subcommand does not yet exist as of the playbook's filing; if you actually need the fallback, file a tracker item to extend the supervisor with the necessary subcommand and the VENDOR_TABLE `context_flush_keystroke` column.)

The supervisor spawns the agent CLI in a managed tmux session, injects pass prompts via `tmux_send_line`, injects the context-flush keystroke (`/compact` for Claude Code; graceful-exit + respawn for others) every `--compact-every` passes, and watches sentinel files in the tranch directory. Recovery semantics are equivalent to the sub-agent path: the tranch state file is the single source of truth, and any respawn reads it to resume.

The sub-agent path is preferred when available because it does not require operator intervention to start, does not depend on tmux being installed, and does not depend on the supervisor's vendor-specific keystroke table being verified for the vendor in use.

## References

- `dogfooding_blind_judge_primer.md` — the severity rubric and 6-field card format from the 1-20 baseline.
- `dogfooding_classification.md` — the methodology-axis dedup precedent (1-20 baseline).
- `dogfooding_clusters.md` — the causal-axis clustering precedent (1-20 baseline), including the rejected-clusters log format.
- `dogfooding_trend_cluster_aware.md` — the trend-report format (1-20 baseline).
- `notebookjournal_06022026_2100_fold_audit_passes_21-40.md` — the 21-40 fold-audit that motivated this procedure.
- `dogfooding_2140_panel/README.md` — the 1-judge panel design (21-40), source of methodology caveats.
- `build_dogfooding_trend_2140.py` — reference implementation of the Phase 5 trend builder.
- `output-capture-long-running-playbook.md` — output capture discipline referenced in Phase 1 sub-agent prompt.
- `self-analysis-dogfooding-playbook.md` — lighter alternative for one-off audit sweeps.
- `scripts/agent-supervisor` — the vendor parity infrastructure referenced in Appendix A.
