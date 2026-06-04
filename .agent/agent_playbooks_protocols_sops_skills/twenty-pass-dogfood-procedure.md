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

The parent agent (the one reading this playbook) owns the phase loop, the tranch state file, and the final trend report. It does NOT directly run the 20 passes — it spawns sub-agents in chunks of `--chunk-size` passes (default 2) and aggregates their return summaries.

**The sub-agent boundary IS the compaction event.** No `/compact` keystroke is invoked anywhere during normal operation. Each sub-agent's full working context (substrate dumps, probe output, command logs, lab notebook drafting, tracker queries — easily 50-100K tokens per pass) lives only inside the sub-agent and dies on return. The parent never accumulates that bulk; it sees only the ~200-word return summaries. The `--chunk-size` parameter therefore controls how many passes-worth of context any single sub-agent has to hold at once. Default 2 matches the empirical observation that a single pass adds ~50K tokens and two passes brings a sub-agent close to typical Opus-class working-context comfort.

Why sub-agent chunks instead of one long-running session:
- **Context hygiene.** Sub-agent boundaries are clean context resets without operator intervention.
- **Vendor neutrality.** Every modern CLI agent exposes sub-agent spawning OR can be approximated via the Appendix A tmux fallback. The procedure defines the abstraction (prompt-in, summary-out, dies-on-return); §Vendor parity table maps current vendors to that abstraction.
- **Independent verifiability.** Sub-agents for blind judging see only the rubric + the deconfounded card, not the discovery context or trend goal. This is the structural countermeasure to the same-actor bias that bit the 21-40 campaign.

For vendors that lack sub-agent spawning (or whose sub-agent primitive doesn't yet satisfy the contract above), Appendix A documents a tmux-based fallback that uses the `scripts/agent-supervisor` VENDOR_TABLE.

## Vendor parity table

Sub-agent spawn mechanism and Appendix-A fallback keystroke per vendor. Cross-references `scripts/agent-supervisor`'s `VENDOR_TABLE` for the underlying CLI invocation + graceful-exit keystroke that the fallback path reuses. Verification status mirrors the supervisor's convention: **Verified** = ground-truthed in production; **Unverified** = best-known value, must be confirmed in a throwaway session before running a real tranch.

| Vendor | Preferred path: sub-agent mechanism | Fallback path: context-flush keystroke (for Appendix A tmux mode) | Verification status |
| --- | --- | --- | --- |
| Claude Code | `Agent` tool with `subagent_type` parameter; supports parallel spawn; supports schema-validated return via `schema` parameter | `/compact` | **Verified** |
| Codex CLI | Codex's task-delegation primitive (verify name with `codex --help` before tranch); if absent, fall back to Appendix A | `/condense` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify both columns before first tranch |
| Cursor | Cursor's agent task-spawn primitive (verify with `cursor --help` before tranch); if absent, fall back to Appendix A. Note: single-session-per-repo quirk per supervisor `VENDOR_TABLE` — parallel sub-agents within the same repo are unsupported, serialize them | `/clear` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify both columns and single-session quirk before first tranch |
| Gemini CLI | Gemini's agent task-spawn primitive (verify with `gemini --help` before tranch); if absent, fall back to Appendix A. Note: before-model hook fires per LLM request not per turn, which may interact poorly with high-fan-out judging — measure overhead before committing to 3-judge ensemble | `/clear` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify both columns and per-request hook overhead before first tranch |

**Verification protocol** (one-time per vendor, similar to the supervisor's WI-batob procedure): in a throwaway tmux session, spawn the vendor CLI, attempt the preferred sub-agent mechanism with a trivial prompt ("write 'hello' to /tmp/test.txt and return 'done'") and confirm capture of the return value. Then test the fallback context-flush keystroke (`tmux send-keys '<keystroke>' Enter`) and confirm the agent's working context is reset. Document the verified values in this table via a follow-up PR.

## Tunable parameters

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `--chunk-size` | 2 | 1-5 | Number of passes per Phase 1 sub-agent. The sub-agent boundary is the compaction event; this parameter controls how much per-pass context any single sub-agent has to hold (substrate dumps + probe output + lab notebook drafting ≈ 50K tokens per pass). Default 2 matches the empirical comfort zone for Opus-class working context. Lower if individual passes are unusually heavy. |
| `--judge-count` | 1 | 1-5 | Blind-judge ensemble size in Phase 4. Default 1 matches the passes-21-40 panel methodology — cheap, fast, yields a usable severity distribution. Upgrade to 3 for inter-rater variance averaging (matches passes 1-20 baseline). The upgrade is **reversible**: re-running additional judges on the SAME cards later is supported — Phase 4 jsonl output is per-judge, so adding judge_2 and judge_3 to a tranch that ran with judge_count=1 is a follow-up sub-agent fan-out, not a redo. |
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

For each chunk of `--chunk-size` passes (default 2), spawn a Phase 1 sub-agent with this prompt template:

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

## Phase 6 — Tracker materialization, agent-notes integration, and carry-forward

Output: tracker rows for every cluster + INDEPENDENT singleton (tagged with the tranch ID); existing parent rows annotated with cross-references; `agent_notes.json` integrated with the new tranch's headline numbers and cohort summary; `${TRANCH_DIR}/carry_forward.md`; cross-tranch index appended; tranch state finalized.

The end-of-tranch cohort must become **actionable work in the tracker** and **discoverable state in agent_notes**. Without these steps the tranch produces a beautiful but inert lab-notebook artifact. Past evidence: the 2026-06-04 fold-audit cohort had to be materialized via 56 manual `tracker add` calls and ~30 `replace_once` calls into agent_notes.json after the lab-notebook work was complete — exactly the toil this phase eliminates.

### Step 6.1 — Tracker materialization sub-agent

Spawn:

```
You are the Phase 6.1 tracker materialization sub-agent for tranch
${TRANCH_ID}. Read ${TRANCH_DIR}/tranch_state.json,
${TRANCH_DIR}/clusters.md, ${TRANCH_DIR}/classification.md,
${TRANCH_DIR}/aggregate_summary.md (joined verdicts), and
${TRANCH_DIR}/mapping_anon_to_source.tsv.

For each consolidated finding that should become its own row (every
cluster from clusters.md + every INDEPENDENT singleton from
classification.md):

  1. Derive priority from the blind-judge severity per the standard
     mapping: 5 → P0, 4 → P1, 3 → P2, 2 → P3, 1 → P4.
  2. Decide whether to attach an existing META as parent. Cross-
     reference clusters.md's Root-cause text against existing META
     rows via `scripts/tracker list --kind invariant --status violated`.
     Attach only when the existing META's scope clearly covers the new
     row's mechanism; default to standalone if uncertain.
  3. Call `scripts/tracker add --kind work_item|invariant
     --title <derived from card Subject> --priority P<N>
     --status todo_hard --tag ${TRANCH_ID} --tag dogfood
     [--parent <META_ID>] --description <derived from card body +
     F-number provenance + 'filed via tranch ${TRANCH_ID}'>`.
  4. Record the returned tracker ID in
     ${TRANCH_DIR}/tracker_materialization.tsv with columns:
     card_id | source_row | tracker_id | priority | parent_id

For each EXISTING tracker row that received related-but-distinct
GENUINE_EXTENSION findings during this tranch (per Phase 3a
classification.md), call `scripts/tracker discuss <ROW_ID>
"[+ ${TRANCH_ID}: F<NN>.X1 ... — <1-line summary>]"` to record the
extension inline on the parent row's discussion thread.

Return a 200-word summary covering: number of rows materialized
(broken down by priority), number of existing rows annotated, parent-
attachment decisions, any cases where you defaulted to standalone
despite a near-miss META.

The tracker has automatic .ops sync; do NOT manually push or commit
.ops files. Do honor AGENTS.md's "do not mutate tracker while an
auto-pr is in flight" rule; if a PR_PENDING gate exists, wait for it
to clear before starting.
```

### Step 6.2 — Agent-notes integration sub-agent

Spawn:

```
You are the Phase 6.2 agent-notes integration sub-agent for tranch
${TRANCH_ID}. Read ${TRANCH_DIR}/tranch_state.json,
${TRANCH_DIR}/aggregate_summary.md, ${TRANCH_DIR}/trend_cluster_aware.md,
${TRANCH_DIR}/retrospective.md, and
${TRANCH_DIR}/tracker_materialization.tsv (Phase 6.1 output).

Read ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json. The
single editable field is `notes` (markdown). Plan the diff BEFORE
writing — produce a plan with these sections, each tied to a specific
replace_once OR append target in the current notes:

  1. Top-of-file header update (if the notes have a "Session theme" or
     "READ THIS FIRST" block, update it to reflect the new tranch's
     headline numbers: cohort count, mean severity, bucket-Σ-severity
     row, Significant+Severe percentage, link to the trend report).
  2. Active set count update (e.g., "Current active set: ~X items
     after tranch ${TRANCH_ID} materialization").
  3. Issues table preamble update: scope=<total rows> after this
     tranch's additions, cumulative summary by tranch.
  4. New cohort sub-section under the corpus table: "Tranch
     ${TRANCH_ID} cohort filed YYYY-MM-DD — N discrete rows" listing
     the materialized rows by family/cluster with priority + parent.
   5. Inline annotations on existing parent rows that received tranch
     extensions: cross-reference the new child rows.
  6. Severity / status distribution table: add a column for this
     tranch's cohort.
  7. Bucket-Σ-severity rollup: append a row to any existing trend
     section, format matching the prior tranch's row.
  8. Cross-tranch comparison note in the retrospective trace.

Apply edits via a Python script using replace_once() semantics
(exact-1-match guarantee) similar to the 2026-06-04 materialization
script archetype:

  def replace_once(old, new, label):
      if notes.count(old) != 1:
          raise RuntimeError(f'{label}: expected 1 match, found {notes.count(old)}')
      notes = notes.replace(old, new)

If a replace_once fails because the OLD literal doesn't match
exactly-once, surface the diagnostic — do NOT fuzzy-replace. Stale
text is better than a silent over-write.

Back up the prior notes file to /tmp/agent_notes-pre-${TRANCH_ID}.json
before any edits.

Return a 200-word summary: edit count, sections updated, any
replace_once failures and how resolved.
```

### Step 6.3 — Cross-tranch index append

The parent agent appends a row to `~/hypergumbo_lab_notebook/dogfood_tranchen_index.md` summarizing the tranch: ordinal, date range, headline bucket-Σ-severity, judge count, cohort count, link to retrospective. Creates the file if it doesn't exist.

### Step 6.4 — Optional retroactive cleanup (requires human edit-mode authorization)

If Phase 2's mid-tranch audit identified content that should have been a new row but got folded into a parent row's discussion thread during real-time passes, the cleanup is structurally identical to the 2026-06-04 fold-audit Phase 2 pruning: surgically trim migrated content from parent discussion entries via `scripts/tracker delete-msg` (whole-entry tombstone) or `edit-msg-text` (in-place supersession) under the WI-zonur per-message edit-mode window.

This step requires **human edit-mode authorization** — the operator must enable edit-mode via the OS-perm-gated TUI control before the parent agent invokes the cleanup sub-agent. The agent prompts the operator with the planned ops count and waits for authorization before proceeding. If authorization is not granted within a reasonable window, skip this step and document the deferred cleanup in `carry_forward.md`.

### Step 6.5 — Carry-forward + tranch finalization

The parent agent writes:

- `${TRANCH_DIR}/carry_forward.md` with: open methodology questions, severity rubric calibration adjustments, saturated probe classes, recommended Phase 0 changes for the next tranch, deferred Step 6.4 cleanup notes if any.
- The next tranch's Phase 0 sub-agent should be pointed at this `carry_forward.md` via the `carry_forward_in` field in its tranch state file.
- Finalize `${TRANCH_DIR}/tranch_state.json`: set `phase` to `complete`, populate the per-step completion timestamps, touch `${TRANCH_DIR}/tranch.complete` sentinel.

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
| Parent's own context fills up before Phase 5 | Parent agent self-reports approaching context limit, OR Phase 4 / 5 needs to load many per-card files | Parent agents cannot programmatically self-`/compact` — the keystroke is user-typed. Two real recovery options: (a) delegate the file-heavy work to a Phase-4-aggregator or Phase-5-reporter sub-agent so the bulk reads happen in a dying sub-agent context, OR (b) split the parent's work across multiple sessions, using `tranch_state.json` + sentinels as the resumption point. Under normal operation the parent's context grows only by ~50-80K tokens across a full tranch (sub-agent return summaries + brief state reads), so this row should rarely fire. |
| Token budget exceeded | Configurable; checked at phase boundaries | Halt at current phase, mark tranch partial, write a partial-retrospective covering completed phases only |

## Cost & budget estimation

Per-tranch token estimates assuming Opus-class models throughout:

| Phase | Sub-agent count | Tokens per sub-agent | Phase total |
|---|---|---|---|
| 0 (setup) | 0 (parent only) | ~50K parent | 50K |
| 1 (discovery) | 10 (20 passes / 2-per-chunk) | ~400K each | 4M |
| 2 (mid-tranch audit) | 1 | ~200K | 200K |
| 3 (consolidation) | 3 (methodology + causal + card-writer) | ~250K each | 750K |
| 4 (judging) | judge_count × card_count | ~80K each | 80K × J × C (≈4.8M at J=1, C=60) |
| 6 (materialization + agent-notes integration) | 2 sub-agents | ~250K each | 500K |
| 5 (retrospective) | 1 | ~150K | 150K |
| 6 (carry-forward) | 0 (parent only) | ~50K parent | 50K |

For a **default tranch (J=1, C≈60)**, Phase 4 is ~4.8M; full tranch including Phase 6 materialization ~6.5M. Matches the passes-21-40 panel's ~4M and is the cheapest configuration that still yields the bucket-Σ-severity convergence signal. **Upgrade option (J=3, C≈60):** Phase 4 jumps to ~14M; full tranch ~16M. Matches the passes-1-20 baseline (~18M for 198 verdicts) and gains inter-rater variance averaging. Upgrade is reversible — additional judges can be fanned out against the same cards in a follow-up step. **Floor (J=1, no card-writer separation):** saves ~500K but reintroduces same-actor bias; not recommended.

## Appendix A — tmux fallback for vendors without sub-agents

If the vendor doesn't expose sub-agent spawning, OR its sub-agent primitive doesn't yet satisfy the contract (prompt-in, summary-out, dies-on-return, optional parallelism), the procedure falls back to the `scripts/agent-supervisor` VENDOR_TABLE machinery. In the fallback, the agent runs all 20 passes in its own context window and the operator-run supervisor injects a context-flush keystroke (`/compact` for Claude Code; per-vendor equivalents from the §Vendor parity table; graceful-exit + respawn for any vendor without an equivalent) every `--chunk-size` passes via `tmux send-keys`.

The operator invokes the fallback via:

```bash
scripts/agent-supervisor dogfood-tranch start \
    --tranch-dir <path> \
    --first-pass <N+1> \
    --chunk-size 2 \
    --judge-count 3 \
    --vendor <claude-code|codex-cli|cursor|gemini-cli>
```

(This subcommand does not yet exist as of the playbook's filing. If you actually need the fallback, file a tracker item to extend the supervisor with the subcommand AND with a `context_flush_keystroke` column on `VENDOR_TABLE`. The supervisor's existing `cli_invocation` and `exit_keystroke` columns at `scripts/agent-supervisor:166-183` are reused, as is the WI-sakod session-start respawn branch for vendors whose context-flush degrades to graceful-exit + respawn.)

The supervisor spawns the agent CLI in a managed tmux session via `tmux_spawn_session` (already implemented), injects pass prompts via `tmux_send_line` (already implemented), injects the context-flush keystroke (NEW — the operator implements once per vendor after verification), and watches sentinel files in the tranch directory. Recovery semantics are equivalent to the sub-agent path: the tranch state file is the single source of truth, and any respawn reads it to resume.

The sub-agent path is preferred when available because:
- It does not require operator intervention to start.
- It does not depend on tmux being installed.
- It does not depend on the supervisor's vendor-specific keystroke table being verified for the vendor in use.
- Sub-agent contexts die deterministically; tmux-injected `/compact` depends on the CLI honoring the keystroke and the agent not having injected output that swallows it.

The fallback path is preferred only when the vendor lacks a viable sub-agent primitive AND the operator has verified the context-flush keystroke per the §Vendor parity table verification protocol.

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
