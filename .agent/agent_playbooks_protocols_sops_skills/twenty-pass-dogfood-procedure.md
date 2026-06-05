<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Twenty-pass dogfood procedure

A soup-to-nuts, vendor-neutral procedure for running a 20-pass dogfooding tranche on hypergumbo (or any comparable tool that ships a CLI, an analysis substrate, and a tracker). Integrates the strengths of the passes-1-20 and passes-21-40 campaigns: 1-20's up-front two-axis consolidation criteria + per-cluster confidence levels + rejected-clusters log, 21-40's F-number provenance + INVALIDATION discipline + mid-stream audit posture. Uses sub-agent orchestration so the operator does not have to babysit a tmux session.

## Purpose & scope

A "tranche" is a contiguous block of 20 dogfooding passes against the same substrate (or substrate family). The procedure produces:

- An immutable per-pass raw-observations record (lab notebook stanzas with F-numbered findings).
- TWO audit passes — a Phase 2 mid-tranche checkpoint on the first-half passes and a Phase 2.5 post-discovery audit on the second-half passes — that both feed Phase 3 consolidation and Phase 6.4's optional retroactive-cleanup decision.
- A two-axis consolidated cohort (methodology-axis + causal-axis) with per-cluster confidence and a rejected-clusters log.
- A blind-judge severity panel (default 1-judge; tunable up to 3-judge or higher for inter-rater variance).
- A cluster-aware trend report (carbon-dating Methods A / B / C: earliest member / latest member / 1-n distributed).
- A retrospective audit covering KIND-tag stability, rejected-clusters scoring, and methodology-question carry-forward.
- A carry-forward queue for the next tranche.

Use this procedure when you want a methodology-comparable dogfooding tranche — i.e., when you expect to compare its trend report to a prior tranche's, or treat it as a baseline for future ones. For one-off audit sweeps that don't need cross-tranche comparison, the lighter-weight `self-analysis-dogfooding-playbook.md` is appropriate.

## When NOT to use

- **Tranche-size mismatch.** Tranches smaller than ~10 passes don't yield enough buckets to produce a meaningful trend signal; larger than ~30 passes blow past the parent agent's working-context budget even with sub-agent chunking. Stay near 20.
- **Substrate is mid-flight changing.** If the codebase is being actively refactored during the tranche, per-pass findings will drift for reasons unrelated to discovery dynamics, and the consolidation work will be wasted. Freeze a substrate snapshot first (see Phase 0 step 2).
- **No prior baseline to compare against AND no plan to compare against future tranches.** The procedure's overhead is justified by cross-tranche comparison; without that audience, run the lighter playbook.

## Architecture: sub-agent orchestration

The parent agent (the one reading this playbook) owns the phase loop, the tranche state file, and the final trend report. It does NOT directly run the 20 passes — it spawns sub-agents in chunks of `--chunk-size` passes (default 2) and aggregates their return summaries.

**The sub-agent boundary IS the compaction event.** No `/compact` keystroke is invoked anywhere during normal operation. Each sub-agent's full working context (substrate dumps, probe output, command logs, lab notebook drafting, tracker queries — easily 50-100K tokens per pass) lives only inside the sub-agent and dies on return. The parent never accumulates that bulk; it sees only the ~200-word return summaries. The `--chunk-size` parameter therefore controls how many passes-worth of context any single sub-agent has to hold at once. Default 2 matches the empirical observation that a single pass adds ~50K tokens and two passes brings a sub-agent close to typical Opus-class working-context comfort.

Why sub-agent chunks instead of one long-running session:
- **Context hygiene.** Sub-agent boundaries are clean context resets without operator intervention.
- **Vendor neutrality.** Every modern CLI agent exposes sub-agent spawning OR can be approximated via the Appendix A tmux fallback. The procedure defines the abstraction (prompt-in, summary-out, dies-on-return); §Vendor parity table maps current vendors to that abstraction.
- **Independent verifiability.** Sub-agents for blind judging see only the rubric + the deconfounded card, not the discovery context or trend goal. This is the structural countermeasure to the same-actor bias that bit the 21-40 campaign.

For vendors that lack sub-agent spawning (or whose sub-agent primitive doesn't yet satisfy the contract above), Appendix A documents a tmux-based fallback that uses the `scripts/agent-supervisor` VENDOR_TABLE.

## Vendor parity table

Sub-agent spawn mechanism and Appendix-A fallback keystroke per vendor. Cross-references `scripts/agent-supervisor`'s `VENDOR_TABLE` for the underlying CLI invocation + graceful-exit keystroke that the fallback path reuses. Verification status mirrors the supervisor's convention: **Verified** = ground-truthed in production; **Unverified** = best-known value, must be confirmed in a throwaway session before running a real tranche.

| Vendor | Preferred path: sub-agent mechanism | Structural blinding mechanism (Phase 3 card writer + Phase 4 judges) | Fallback path: context-flush keystroke (Appendix A tmux mode) | Verification status |
| --- | --- | --- | --- | --- |
| Claude Code | `Agent` tool with `subagent_type` parameter; supports parallel spawn; supports schema-validated return via `schema` parameter | Stage allowed files in `${TRANCHE_DIR}/.staging/<sub-agent-name>/`; pass staging directory as the working directory in the Agent prompt. The Agent tool does not currently restrict filesystem access, so blinding is enforced by (a) staging only allowed files, (b) the prompt directing the sub-agent to its working directory, and (c) avoiding `cwd` directives that would expose the broader repo. Strongest blinding pattern available for this vendor today | `/compact` | **Verified** for spawn + flush; **partial** for blinding (declarative-plus-staging, not filesystem-enforced) |
| Codex CLI | Codex's task-delegation primitive (verify name with `codex --help` before tranche); if absent, fall back to Appendix A | Same staging-directory pattern as Claude Code; verify Codex sub-agent file-access model before tranche | `/condense` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify all three columns before first tranche |
| Cursor | Cursor's agent task-spawn primitive (verify with `cursor --help` before tranche); if absent, fall back to Appendix A. Note: single-session-per-repo quirk per supervisor `VENDOR_TABLE` — parallel sub-agents within the same repo are unsupported, serialize them | Same staging-directory pattern; the single-session quirk means Phase 4 fan-out cannot be parallel, but blinding still works per sub-agent invocation | `/clear` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify all three columns and single-session quirk before first tranche |
| Gemini CLI | Gemini's agent task-spawn primitive (verify with `gemini --help` before tranche); if absent, fall back to Appendix A. Note: before-model hook fires per LLM request not per turn, which may interact poorly with high-fan-out judging — measure overhead before committing to high `--judge-count` | Same staging-directory pattern; verify Gemini sub-agent file-access model before tranche | `/clear` candidate, or graceful-exit + respawn via supervisor | **Unverified** — verify all three columns and per-request hook overhead before first tranche |

**Verification protocol** (one-time per vendor, similar to the supervisor's WI-batob procedure): in a throwaway tmux session, spawn the vendor CLI, attempt the preferred sub-agent mechanism with a trivial prompt ("write 'hello' to /tmp/test.txt and return 'done'") and confirm capture of the return value. Then test the fallback context-flush keystroke (`tmux send-keys '<keystroke>' Enter`) and confirm the agent's working context is reset. Document the verified values in this table via a follow-up PR.

## Tunable parameters

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `--chunk-size` | 2 | 1-5 | Number of passes per Phase 1 sub-agent. The sub-agent boundary is the compaction event; this parameter controls how much per-pass context any single sub-agent has to hold (substrate dumps + probe output + lab notebook drafting ≈ 50K tokens per pass). Default 2 matches the empirical comfort zone for Opus-class working context. Lower if individual passes are unusually heavy. |
| `--judge-count` | 1 | 1-5 | Blind-judge ensemble size in Phase 4. Default 1 matches the passes-21-40 panel methodology — cheap, fast, yields a usable severity distribution. Upgrade to 3 for inter-rater variance averaging (matches passes 1-20 baseline). The upgrade is **reversible**: re-running additional judges on the SAME cards later is supported — Phase 4 jsonl output is per-judge, so adding judge_2 and judge_3 to a tranche that ran with judge_count=1 is a follow-up sub-agent fan-out, not a redo. |
| `--mid-tranche-pass` | 10 | 5-15 | Pass number to insert the Phase 2 interim audit. Default at half-tranche. |
| `--tranche-size` | 20 | 10-30 | Total pass count. Above 30 the procedure starts losing coherence; below 10 the trend signal is too noisy. |
| `--substrate-policy` | snapshot | snapshot \| regen-each \| regen-on-suspicion | When to regenerate the analysis substrate. snapshot = once, at Phase 0. regen-each = every pass (expensive). regen-on-suspicion = whenever F39.A1-style multi-pass negative findings need fresh-substrate verification. |

## Phase 0 — Pre-tranche setup

Output: `${TRANCHE_DIR}/tranche_state.json` populated; substrate frozen; rubric locked.

### Step 0.1 — Pick the tranche directory

```bash
TRANCHE_DIR=~/hypergumbo_lab_notebook/dogfood_tranche_<NN>  # NN = tranche ordinal
mkdir -p "$TRANCHE_DIR"
```

The directory holds all tranche artifacts: state file, per-pass raw observations, consolidation outputs, blind-judge cards and verdicts, trend report, retrospective. Never overwritten across tranches.

### Step 0.2 — Freeze the substrate

If `--substrate-policy=snapshot` (default), generate the substrate once and lock it:

```bash
hypergumbo run . --out "${TRANCHE_DIR}/substrate.json"
sha256sum "${TRANCHE_DIR}/substrate.json" > "${TRANCHE_DIR}/substrate.sha256"
```

If `regen-each` or `regen-on-suspicion`, document the substrate-generation command in the tranche state file; substrate files go under `${TRANCHE_DIR}/substrates/pass_NN.json` so cross-pass comparability is preserved.

### Step 0.3 — Write the substrate guide

Copy or generate `${TRANCHE_DIR}/substrate_guide.md` covering: severity rubric (1=Cosmetic ... 5=Severe), status reconciliation rules, substrate file references. This locks the rubric so it can't drift mid-tranche. The 5-tier rubric and 6-field card format from `dogfooding_blind_judge_primer.md` is the proven baseline; reuse it unless you have a documented reason to diverge.

### Step 0.4 — Commit the meta-criterion verbatim

In `${TRANCHE_DIR}/meta_criterion.txt`, write:

```
If two findings are actually two symptoms of one underlying problem,
then that underlying problem should be what gets judged, not the two
symptoms individually.
```

This is the load-bearing user-provided rule from the 2026-05-29 baseline campaign. It governs all consolidation work in Phase 3.

### Step 0.5 — Initialize tranche state

```json
{
  "tranche_id": "<NN>",
  "tranche_dir": "<absolute path>",
  "vendor": "<claude-code|codex-cli|cursor|gemini-cli>",
  "tranche_size": 20,
  "compact_every": 2,
  "judge_count": 3,
  "mid_tranche_pass": 10,
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
  "meta_criterion_file": "meta_criterion.txt",
  "substrate_guide_file": "substrate_guide.md",
  "carry_forward_in": "<path to prior tranche's carry_forward.md or null>",
  "started_at_utc": "<ISO 8601>"
}
```

Write to `${TRANCHE_DIR}/tranche_state.json`. All sub-agents read this file as their first action; the parent updates it after every sub-agent return.

## Phase 1 — Discovery (per-pass sub-agent chunks)

Output: `${TRANCHE_DIR}/pass_NN.md` per pass (immutable after written), `${TRANCHE_DIR}/pass_NN.complete` sentinel per pass, `passes_complete` array in tranche state.

### Step 1.1 — Chunk loop

For each chunk of `--chunk-size` passes (default 2), spawn a Phase 1 sub-agent with this prompt template:

```
You are a Phase 1 discovery sub-agent for dogfooding tranche ${TRANCHE_ID}.
Read ${TRANCHE_DIR}/tranche_state.json first. You will run passes
${chunk_start} through ${chunk_end} against substrate ${substrate_file}.

For each pass:
  1. Pick a probe class. Use the substrate guide's probe-class catalog.
     Avoid probe classes already saturated by prior passes in this tranche
     (see passes_complete in tranche state for prior pass summaries).
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
         in this tranche, mark it INVALIDATION-RISK and trigger fresh-
         substrate regeneration before amplifying the claim. This is the
         F39.A1 multi-pass mismeasurement countermeasure.
  4. Write the pass writeup to ${TRANCHE_DIR}/pass_${pass_n}.md as
     immutable lab-notebook stanzas.
  5. Touch ${TRANCHE_DIR}/pass_${pass_n}.complete to signal completion.

Return a 200-word summary covering:
  - Pass numbers run.
  - Headline findings (F-numbers + 1-line each).
  - INVALIDATION-RISK flags raised (if any) and how resolved.
  - Probe classes still un-sampled, suggested for next chunk.
DO NOT return the full pass writeups — they live in the per-pass files.

When done, exit. The parent agent will spawn the next chunk after
absorbing your summary into tranche_state.json.
```

### Step 1.2 — Parent aggregation

After each sub-agent returns, the parent:
1. Reads the chunk summary from the sub-agent's return value.
2. Updates `tranche_state.json`: `current_pass` advances, `passes_complete` array gets each pass + its 1-line headline.
3. If `--mid-tranche-pass` was just reached, transitions to Phase 2 (next chunk is the audit chunk).
4. Otherwise spawns the next Phase 1 chunk.

### Step 1.3 — Fresh-substrate verification protocol

When an INVALIDATION-RISK flag is raised, the sub-agent's response must include either:
- "verification ran: substrate regenerated at <path>; finding reproduced" — finding proceeds as substantive, NOT as INVALIDATION.
- "verification ran: substrate regenerated at <path>; finding did NOT reproduce" — finding gets KIND=INVALIDATION and the previously-cited prior pass's finding gets marked-for-review in tranche state. Phase 3 consolidation will decide whether to invalidate the prior finding or keep it as a methodology-calibration record.

This is the procedure that catches multi-pass false-claim chains before they amplify into 4-pass-deep mistakes (the F34→F37 chain in the 21-40 campaign).

## Phase 2 — Mid-tranche checkpoint

Output: `${TRANCHE_DIR}/midtranche_audit.md`; `tranche.checkpoint` sentinel; any pass writeups annotated with KIND-tag ratchets.

After pass `--mid-tranche-pass` completes (default pass 10 of the 20-pass tranche), spawn a Phase 2 sub-agent:

```
You are the Phase 2 mid-tranche audit sub-agent for tranche ${TRANCHE_ID}.
Read ${TRANCHE_DIR}/tranche_state.json and every ${TRANCHE_DIR}/pass_NN.md
file for completed passes.

Your job is to catch over-folding or over-splitting in the first half
of the tranche, BEFORE patterns amplify across the second half. Do NOT
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

Write findings to ${TRANCHE_DIR}/midtranche_audit.md with sections:
  - KIND ratchets (per-pass diffs).
  - New INVALIDATION-RISK flags.
  - Pattern-amplification warnings.
  - Recommendation on whether second-half probe direction should adjust.

Touch ${TRANCHE_DIR}/tranche.checkpoint. Return a 150-word summary.
```

After Phase 2, the parent continues Phase 1 with the remaining passes. Sub-agents in the second half read `midtranche_audit.md` as part of their context.

## Phase 2.5 — Post-discovery second-half audit

Output: `${TRANCHE_DIR}/post_discovery_audit.md`; `tranche.post_discovery_audit` sentinel; any second-half pass writeups annotated with KIND-tag ratchets.

After the FINAL Phase 1 chunk completes (passes `--mid-tranche-pass + 1` through `--tranche-size` have all written their `pass_NN.md` + `pass_NN.complete`), and BEFORE Phase 3 starts, spawn a Phase 2.5 sub-agent that does the symmetric Phase 2 protocol on the second-half passes. Without this step, the first-half passes get a KIND-ratchet / amplification-warning audit but the second-half passes don't — they go straight into Phase 3 consolidation, which can absorb over-folding or amplification as if it were intentional.

The motivating gap: the first run of this playbook (tranche 03, passes 41-60) skipped this step and the parent agent could not honestly answer "is there content to prune?" for the second-half passes — Phase 2 only covered passes 41-50, and Phase 3 / 5 looked at the second half but with different questions (consolidation, retrospective) rather than the Phase 2-style ratchet/amplification check.

```
You are the Phase 2.5 post-discovery audit sub-agent for tranche ${TRANCHE_ID}.
Read ${TRANCHE_DIR}/tranche_state.json, ${TRANCHE_DIR}/midtranche_audit.md
(Phase 2's findings), and every ${TRANCHE_DIR}/pass_NN.md file for the
SECOND-HALF passes (passes from ${first_pass_number + mid_tranche_pass}
to ${last_pass_number}).

Your job is to catch over-folding, over-splitting, and amplification
in the second half of the tranche — symmetrically to Phase 2 on the
first half — BEFORE Phase 3 consolidation absorbs the patterns as if
they were intentional. Do NOT attempt full consolidation; Phase 3
runs after you.

For each completed second-half pass:
  1. Re-evaluate every finding's provisional KIND tag using the same
     6-way taxonomy from substrate_guide.md. Promote EXTENSION →
     STANDALONE / demote STANDALONE → EXTENSION as warranted.
  2. Cross-check for INVALIDATION-RISK findings that were not flagged
     at finding-time, including risks that would reach back into the
     FIRST-HALF passes (i.e., a second-half finding that quietly
     invalidates a first-half claim). Apply fresh-substrate verification
     if you find any.
  3. Flag pattern-amplification risks: claims re-asserted across 3+
     second-half passes without fresh-substrate verification, OR claims
     in the second half that re-cite first-half-derived denominators or
     numbers without fresh derivation.
  4. Flag would-have-been-pruned content: any finding whose evidence
     base is thin enough that you would not have filed it as a tracker
     row under Phase 6.1, OR any cross-pass repetition that would be a
     better fit as a discussion entry on an earlier finding than as a
     standalone finding.

Write to ${TRANCHE_DIR}/post_discovery_audit.md with sections:
  - KIND ratchets (per-pass diffs across the second-half passes).
  - New INVALIDATION-RISK flags (including any retroactively against
    first-half findings).
  - Pattern-amplification warnings.
  - Would-have-been-pruned candidates (the new section vs Phase 2; this
    is the explicit input to whether Phase 6.4 retroactive cleanup
    should fire).

Touch ${TRANCHE_DIR}/tranche.post_discovery_audit. Return a 180-word summary.
```

The Phase 2.5 sub-agent's output feeds two downstream decisions:

1. **Phase 3 consolidation** reads `post_discovery_audit.md` alongside `midtranche_audit.md` as input. Both audits' KIND ratchets are authoritative starting points for Phase 3a methodology-axis classification.
2. **Phase 6.4 retroactive cleanup** decision: if `post_discovery_audit.md`'s "would-have-been-pruned candidates" section is non-empty, the parent agent surfaces those candidates to the operator at Phase 6.4 (with the planned ops count) and waits for human edit-mode authorization. If empty, Phase 6.4 is genuinely skippable. Without Phase 2.5, the "skip Phase 6.4" decision is unsound because the second-half passes' would-have-been-pruned content was never enumerated.

**Tracker-tag surfacing (cross-session memory).** Immediately after Phase 2.5 returns:

- If disposition is **"trigger Phase 6.4 with N candidates"**: the parent agent files a single tracker row tagged `awaits_edit_mode_authorization` (title: `Tranche ${TRANCHE_ID} Phase 6.4 cleanup pending — N candidates`; description: the candidate list from `post_discovery_audit.md`; status: `todo_hard`; priority derived from the highest-severity candidate). This row IS the cross-session surfacing mechanism — subsequent sessions discover the pending work via standard `scripts/tracker list --tag awaits_edit_mode_authorization` queries and the stop-hook's `tracker check-messages` discovery. The tag is stripped by Step 6.4's cleanup sub-agent once the operator authorizes edit mode and the candidates have been applied (see Step 6.4).
- If disposition is **"skip — confirmed"**: no tracker row is filed. The disposition still lands in `agent_notes` via Phase 6.2's edit-item 9 as the historical record-of-decision.

Without this surfacing, a deferred Phase 6.4 becomes invisible across sessions — the disposition lives only in `post_discovery_audit.md`, which is a lab-notebook artifact not auto-loaded into agent context.

## Phase 3 — End-of-tranche consolidation

Output: `${TRANCHE_DIR}/classification.md` (methodology-axis), `${TRANCHE_DIR}/clusters.md` (causal-axis), `${TRANCHE_DIR}/rejected_clusters.md`, `tranche.consolidated` sentinel.

Phase 3 runs TWO sub-agents in sequence — not one. Methodology-axis dedup and causal-axis dedup answer different questions; conflating them is what produced the 21-40 over-fold. The order is methodology-first because audit-sweep collapse changes the unit-count that causal clustering operates on.

### Step 3.1 — Methodology-axis sub-agent

```
You are the Phase 3a methodology-axis dedup sub-agent for tranche
${TRANCHE_ID}. Read ${TRANCHE_DIR}/tranche_state.json, every
${TRANCHE_DIR}/pass_NN.md, ${TRANCHE_DIR}/midtranche_audit.md (Phase 2
first-half audit), and ${TRANCHE_DIR}/post_discovery_audit.md (Phase 2.5
second-half audit). Both audits' KIND ratchets are authoritative
starting points; honor them unless you have evidence to reverse.

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

Write the classification to ${TRANCHE_DIR}/classification.md with
session-by-session tables matching the 1-20 baseline format. Return a
150-word summary covering: AUDIT-AXIS sweep count, EXTENSION/
CORRECTION counts, INVALIDATION count, low-confidence cases.
```

### Step 3.2 — Causal-axis sub-agent

```
You are the Phase 3b causal-axis clustering sub-agent for tranche
${TRANCHE_ID}. Read ${TRANCHE_DIR}/tranche_state.json, every
${TRANCHE_DIR}/pass_NN.md, ${TRANCHE_DIR}/midtranche_audit.md,
${TRANCHE_DIR}/post_discovery_audit.md, and
${TRANCHE_DIR}/classification.md (Phase 3a output).

The meta-criterion is in ${TRANCHE_DIR}/meta_criterion.txt: "If two
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

Write to ${TRANCHE_DIR}/clusters.md.

CRITICAL: Maintain a separate ${TRANCHE_DIR}/rejected_clusters.md log
of tempting consolidations you considered and rejected. For each
rejected cluster:
  - The members you considered grouping.
  - Why you rejected the grouping (1-2 sentences naming the fix-surface
    divergence).
  - Where each member ended up instead (singleton or which other cluster).

The rejected-clusters log is what makes Phase 3 audit-able. Without
it, no future reader can second-guess your decisions.

Touch ${TRANCHE_DIR}/tranche.consolidated. Return a 200-word summary.
```

### Step 3.3 — Card generation

After both consolidation sub-agents complete, the parent spawns a card-writer sub-agent. The card writer must see ONLY the consolidated findings and the substrate guide — not the trend-analysis goal, not the prior-tranche baseline, not the cross-tranche index. This is the structural countermeasure to the same-actor bias.

**Structural blinding via staging directory** (not declarative).

Christian & Mazor (2026) and the introspection-reliability literature established that telling a model "ignore the biasing info" fails and sometimes backfires. The fix is to make the biasing info *unavailable* to the sub-agent, not to ask it not to read. The parent agent therefore stages allowed inputs in `${TRANCHE_DIR}/.staging/card_writer/` and points the sub-agent at the staging directory:

```bash
mkdir -p "${TRANCHE_DIR}/.staging/card_writer"
cp "${TRANCHE_DIR}/substrate_guide.md" "${TRANCHE_DIR}/.staging/card_writer/"
cp "${TRANCHE_DIR}/classification.md" "${TRANCHE_DIR}/.staging/card_writer/"
cp "${TRANCHE_DIR}/clusters.md" "${TRANCHE_DIR}/.staging/card_writer/"
cp "${TRANCHE_DIR}"/pass_*.md "${TRANCHE_DIR}/.staging/card_writer/"
```

The sub-agent prompt then constrains tool use to the staging directory. Vendor-specific guidance is in the §Vendor parity table column "Structural blinding mechanism." If the vendor cannot constrain sub-agent file access, the staging directory is still the right pattern — the sub-agent can `ls` outside the directory but the prompt + the absence of the file content in its prepared inputs makes accidental contamination far less likely than the pure-declarative pattern. Document this as a known weakness in the retrospective when running on unrestricted-vendor sub-agents.

```
You are the Phase 3c card writer for tranche ${TRANCHE_ID}. Your working
directory is ${TRANCHE_DIR}/.staging/card_writer. Read only files within
this directory; the parent has staged exactly the files you need.

For each consolidated finding (clusters become one card per cluster;
INDEPENDENT singletons become one card each; EXTENSION/CORRECTION
folded findings do NOT become cards), write a deconfounded card in
the 6-field format:

  Card ID: anon-card-${TRANCHE_ID}-NNN (NNN zero-padded).
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

Write cards to ${TRANCHE_DIR}/cards_blinded.md and the unblinding key
to ${TRANCHE_DIR}/mapping_anon_to_source.tsv. Run a leak-check grep
for the forbidden patterns and write the result to
${TRANCHE_DIR}/leak_check.md.

Return a 100-word summary: card count, leak-check result, any cards
flagged for re-review.
```

## Phase 4 — Blind judging

Output: `${TRANCHE_DIR}/verdicts_judge_N.jsonl` per judge; `${TRANCHE_DIR}/aggregate_summary.md`; `tranche.judged` sentinel.

### Step 4.1 — Fan-out judge sub-agents

For each card × each judge in the `--judge-count` ensemble (default 1), spawn a judge sub-agent in parallel. Same structural-blinding pattern as Phase 3.3: the parent stages exactly the allowed inputs in a per-judge staging directory before spawning.

```bash
mkdir -p "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}"
cp "${TRANCHE_DIR}/substrate_guide.md" \
   "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}/"
# Extract the single card matching ${CARD_ID} from cards_blinded.md
python3 extract_card.py "${TRANCHE_DIR}/cards_blinded.md" "${CARD_ID}" \
   > "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}/card.md"
```

Sub-agent prompt:

```
You are a Phase 4 blind judge sub-agent for tranche ${TRANCHE_ID},
assigned to card ${CARD_ID}, judge index ${JUDGE_INDEX}. Your working
directory is ${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}.
Read only files within this directory; the parent has staged exactly
substrate_guide.md (the rubric) and card.md (the single card you judge).

Score the card on the 1-5 severity scale per the rubric. Emit a JSON
object on stdout:
  {"card_id": "...", "judge_index": N, "severity": INT,
   "severity_label": "Cosmetic|Minor|Moderate|Significant|Severe",
   "rationale": "<2-4 sentence justification anchored in the rubric>"}

Do not write to any file. The orchestrator captures your stdout.
```

The staging-directory pattern is the structural countermeasure. Where the vendor's sub-agent primitive permits constraining filesystem access (a sandbox flag, a working-directory restriction), use that in addition. See §Vendor parity table column "Structural blinding mechanism" for vendor specifics.

The parent collects each judge's verdict and writes `verdicts_judge_${JUDGE_INDEX}.jsonl` for each judge.

### Step 4.2 — Reconciliation rule

Pre-commit before spawning the judges: if `--judge-count >= 2`, the reconciliation rule is "if judges spread >= 2 tiers, headline = median, spread is flagged in `aggregate_summary.md`." For `--judge-count == 1`, no reconciliation; the panel README MUST note the missing inter-rater check.

### Step 4.3 — Aggregate

Parent agent (or a Phase 4 aggregator sub-agent) joins verdicts + mapping_anon_to_source.tsv, computes the distribution, identifies Severe+Significant cards by source row, and writes `${TRANCHE_DIR}/aggregate_summary.md` matching the layout of the 1-20 baseline's `dogfooding_blind_judge_primer.md` output sections.

Touch `${TRANCHE_DIR}/tranche.judged`.

## Phase 5 — Trend report + retrospective

Output: `${TRANCHE_DIR}/trend_cluster_aware.md`, `${TRANCHE_DIR}/retrospective.md`, `tranche.retrospective` sentinel.

### Step 5.1 — Trend report

The parent runs (or spawns a sub-agent that runs) a trend-report builder modeled on `~/hypergumbo_lab_notebook/build_dogfooding_trend_2140.py`:

Inputs:
- `${TRANCHE_DIR}/mapping_anon_to_source.tsv`.
- `${TRANCHE_DIR}/clusters.md` (for cluster member-pass derivation).
- `${TRANCHE_DIR}/verdicts_judge_*.jsonl` (joined into per-card severity).

Outputs (in `${TRANCHE_DIR}/trend_cluster_aware.md`):
- Per-pass COUNT table (Methods A / B / C).
- Per-pass MEAN BLIND SEVERITY table.
- Per-pass SEVERITY SUM table (the column the 1-20 baseline didn't ship; included by default here).
- Count + severity summary metrics (slope, early/late means, late-minus-early delta).
- Sanity checks (Σ over methods agrees; singletons identical under A/B/C; etc.).
- Cluster inventory.
- Bucket-level 5-pass Σ-severity rollup for cross-tranche comparison.

**Canonical cohort for the trend report (CRITICAL).** The cohort that contributes severity to the per-pass + bucket Σ-sev tables is **strict-dedup + state-change fold-triggers**, NOT the full set of judged cards. Specifically:

- **Include** every card classified by Phase 6.1 Step 2 as `NOT_A_DUPLICATE` or `RELATED_BUT_DISTINCT` (these are novel discoveries that get their own tracker rows — they're the "new distinct invariants" the trend is measuring).
- **Include** every card classified as `TRUE_DUPLICATE` whose fold caused a state change on the fold target (per Step 4a: `done`/`satisfied` → `todo_hard`/`violated` reopen, or `pending_validation` → `violated` failed-validation). These cards' evidence MATERIALLY CHANGED PROJECT STATE on a previously-closed row — that's real discovery work even though no new row was created. Detect via `tracker_materialization.tsv`'s `fold_target_status_at_fold` ≠ `fold_target_status_after` (or `fold_target_status_after` contains "reopened").
- **Exclude** every card classified as `TRUE_DUPLICATE` that folded into an already-actionable (`todo_hard` / `violated` / `pending_validation`-not-flipped / `blocked` / `in_progress`) row WITHOUT triggering a state change. These were redundant discovery on known-open work — counting them inflates the severity total without representing distinct discovery or state change.

The trend builder script auto-detects state-change fold-triggers from the `fold_target_status_at_fold` and `fold_target_status_after` columns of `tracker_materialization.tsv`. The trend file's header must call out the cohort composition explicitly (e.g., "37 cards = 35 strict-dedup + 2 state-change fold-triggers").

This method evolved during tranche-03 review when the original "strict dedup" view (35 cards / 127 Σ-sev) was found to under-count 2 state-changing fold-triggers (cards 021 + 047, each severity 3 = 6 Σ-sev) that broke pending_validation on INV-mozaf + INV-jukok. The "canonical" view adds those 6 points back to give 37 cards / 133 Σ-sev. Tranches 01 and 02 predate this discipline; their trend files carry a "Methodology disclaimer (added 2026-06-05 retroactively)" footer noting they may slightly under-count their own fold-trigger state-changes. The cross-tranche apples-to-apples comparison via `dogfooding_trend_combined.md` is therefore very slightly biased toward post-tranche-03 cohorts, by an irreducible amount (the historical fold-audit work was not re-run with state-change tracking).

### Step 5.2 — Retrospective

Spawn a retrospective sub-agent:

```
You are the Phase 5 retrospective sub-agent for tranche ${TRANCHE_ID}.

Read:
  - ${TRANCHE_DIR}/trend_cluster_aware.md (the measured outcome).
  - ${TRANCHE_DIR}/midtranche_audit.md (mid-tranche KIND ratchets).
  - ${TRANCHE_DIR}/classification.md and ${TRANCHE_DIR}/clusters.md
    (the consolidated cohort).
  - ${TRANCHE_DIR}/rejected_clusters.md (the consolidation decisions log).

Produce a retrospective covering:
  1. KIND tag stability: how many real-time tags survived the
     mid-tranche audit and Phase 3 reclassification? Where did real-time
     tagging fail?
  2. Rejected-clusters scoring: did the rejected-clusters log catch any
     cluster you would have collapsed under one-shot consolidation?
     Name specific cases.
  3. Carry-forward to the next tranche: methodology questions raised,
     calibration adjustments suggested, probe classes saturated vs
     fresh.

Write to ${TRANCHE_DIR}/retrospective.md. Touch
${TRANCHE_DIR}/tranche.retrospective. Return a 200-word summary.
```

### Step 5.3 — Combined cross-tranche trend regeneration

Output: `~/hypergumbo_lab_notebook/dogfooding_trend_combined.md` (overwritten, not appended; this file is auto-generated and must not be hand-edited).

The parent agent runs `python3 ~/hypergumbo_lab_notebook/build_combined_trend.py` (no arguments). The script auto-discovers every tranche's `trend_cluster_aware.md` plus its `tranche_state.json` (if present), then produces a single combined report containing:

- A per-tranche metadata table (judge count, card count, methodology note, Σ-severity sub-total, parse path).
- A per-pass Method C Σ-severity table covering ALL completed passes (one row per pass, s1 through the last completed pass).
- A 5-pass bucket Σ-severity rollup, all buckets across all tranches.
- An ASCII sparkline of the per-pass series.
- A per-chunk resource-consumption table (sub-agent tokens, tool uses, wall-clock seconds) for every Phase 1 chunk in tranches that have `tranche_state.json` data (pre-playbook tranches are absent from this section; the script notes that explicitly).
- An "Other-phase sub-agents" sub-section with the same fields for Phase 2 mid-tranche checkpoint and Phase 2.5 post-discovery audit.

The script lives in the lab notebook (not the repo) at `~/hypergumbo_lab_notebook/build_combined_trend.py`. To add a future tranche's data, just generate its `trend_cluster_aware.md` per Phase 5.1 and ensure its `tranche_state.json` records per-chunk stats; the script picks up the new tranche on next run without code changes.

Why this step exists separately from the per-tranche trend report: Phase 5.1 produces a tranche-scoped view useful for the per-tranche retrospective and PR; Phase 5.3 produces the cross-tranche view useful for comparing trend signals (convergence question, within-tranche peak-then-decline pattern, per-chunk resource scaling). Without Phase 5.3, the cross-tranche view has to be hand-assembled by reading every per-tranche trend file each time, which is what produced multiple counting errors in tranche 03's session (off-by-one in tail flags, prose-vs-table count drift).

## Phase 6 — Tracker materialization, agent-notes integration, and carry-forward

Output: tracker rows for every cluster + INDEPENDENT singleton (tagged with the tranche ID); existing parent rows annotated with cross-references; `agent_notes.json` integrated with the new tranche's headline numbers and cohort summary; `${TRANCHE_DIR}/carry_forward.md`; cross-tranche index appended; tranche state finalized.

The end-of-tranche cohort must become **actionable work in the tracker** and **discoverable state in agent_notes**. Without these steps the tranche produces a beautiful but inert lab-notebook artifact. Past evidence: the 2026-06-04 fold-audit cohort had to be materialized via 56 manual `tracker add` calls and ~30 `replace_once` calls into agent_notes.json after the lab-notebook work was complete — exactly the toil this phase eliminates.

### Step 6.1 — Tracker materialization sub-agent

Spawn:

```
You are the Phase 6.1 tracker materialization sub-agent for tranche
${TRANCHE_ID}. Read ${TRANCHE_DIR}/tranche_state.json,
${TRANCHE_DIR}/clusters.md, ${TRANCHE_DIR}/classification.md,
${TRANCHE_DIR}/aggregate_summary.md (joined verdicts), and
${TRANCHE_DIR}/mapping_anon_to_source.tsv.

For each consolidated finding that should become its own row (every
cluster from clusters.md + every INDEPENDENT singleton from
classification.md):

  1. Derive priority from the blind-judge severity per the standard
     mapping: 5 → P0, 4 → P1, 3 → P2, 2 → P3, 1 → P4.

  2. **Card-level duplicate detection (broader-tracker scan).** BEFORE
     deciding new-row vs new-row-with-parent, search the existing
     tracker for potential duplicates of THIS card across ALL kinds and
     ALL statuses, not just violated invariants:

       - Extract 2-4 high-signal keywords from the card's Subject and
         Mechanic (e.g., "evidence_lang null cross-language",
         "stable_id collision route variants", "validation_report
         silent zero violations").
       - Run `scripts/tracker list --search <keyword>` for each keyword
         (or `scripts/tracker list | grep -i <pattern>` if --search is
         not supported); collect candidate IDs.
       - For each candidate, run `scripts/tracker show <id>` and read
         title + description + recent discussion thread. Classify the
         match:
           - **TRUE_DUPLICATE** — the existing row's mechanism is the
             same as the card's mechanism, even if scope/magnitude
             differs. Default action: do NOT create a new row. Instead
             record the card as a tranche-NN extension on the existing
             row in Step 5.
           - **RELATED_BUT_DISTINCT** — the existing row touches the
             same surface but the fix-surface diverges (different
             root cause, different code path, etc.). Default action:
             create the new row AND cross-reference the existing row
             in Step 5.
           - **NOT_A_DUPLICATE** — keyword collision only. No action.
       - Record the classification per card in a fourth column of
         tracker_materialization.tsv (NOT_A_DUPLICATE for cards with
         no candidate hits). LOW-confidence calls default to
         RELATED_BUT_DISTINCT, NOT TRUE_DUPLICATE, to avoid silent
         loss-of-information.

  3. Decide whether to attach an existing META as parent. Cross-
     reference clusters.md's Root-cause text against existing META
     rows via `scripts/tracker list --kind invariant --status violated`.
     Attach only when the existing META's scope clearly covers the new
     row's mechanism; default to standalone if uncertain.

  4. **File the card** based on the Step 2 classification:
       - **TRUE_DUPLICATE**: do NOT create a new row. Instead call
         `scripts/tracker discuss <existing_id>
         "[+ ${TRANCHE_ID}: <card_id> F<NN> — <1-line summary>
         (folded as duplicate; blind severity <N>)]"`. Record in
         tracker_materialization.tsv with `tracker_id=FOLDED_INTO:<existing_id>`.
         **THEN apply Step 4a status-adjustment rule below.**
       - **RELATED_BUT_DISTINCT or NOT_A_DUPLICATE**: call
         `scripts/tracker add --kind work_item|invariant
         --title <derived from card Subject> --priority <0-4 integer>
         --status todo_hard --tag dogfood_tranche_${TRANCHE_ID}
         --tag dogfood [--parent <META_ID>] --description
         <derived from card body + F-number provenance +
         'filed via tranche ${TRANCHE_ID}'>`. If RELATED_BUT_DISTINCT,
         ALSO call `scripts/tracker discuss <related_existing_id>
         "[+ ${TRANCHE_ID}: <new_row_id> — related: <one-line how>]"`
         so the relationship is bidirectionally discoverable.
         **THEN apply Step 4a status-adjustment rule below.**

  4a. **Status-adjustment rule for resolved-state fold targets.** Before
      proceeding to Step 5, check the existing row's current status via
      `scripts/tracker show <existing_id>`. If the fold target is in a
      resolved state — `done`, `satisfied`, `pending_validation`, or
      `wont_do` — a new tranche finding reproducing the mechanism is
      direct evidence that the resolved state may no longer be accurate.
      Apply per-status:

       - `done` or `satisfied`: REOPEN. Call
         `scripts/tracker update <existing_id> --status todo_hard --note
         "reopened: tranche-${TRANCHE_ID} <card_id> F<NN> reproduces the
         original mechanism on substrate at HEAD <SHA>; blind severity
         <N> — regression evidence"`. The card stays folded as a
         discussion entry; the existing row's status now reflects the
         regression. Do NOT silently leave it as `done` / `satisfied`.
       - `pending_validation`: VALIDATION JUST FAILED. Call
         `scripts/tracker update <existing_id> --status violated --note
         "validation failed: tranche-${TRANCHE_ID} <card_id> F<NN>
         reproduced the original mechanism"`. The card stays folded.
       - `wont_do`: SURFACE TO OPERATOR — do not auto-reopen. The
         deferral may have explicit rationale that new evidence does
         not override. Add a discussion entry on the existing row
         (`tracker discuss`) flagging the new evidence and the
         disposition decision the operator owes. If the playbook is
         running autonomously without an operator-in-the-loop, leave
         status as `wont_do` and surface to `carry_forward.md` for the
         next-tranche's Phase 0 sub-agent to escalate.
       - `todo_*` / `blocked` / `in_progress` / `needs_human_review`:
         no status change. The work is already actionable; the card
         fold adds evidence without changing disposition.

      The same status-adjustment rule applies to RELATED_BUT_DISTINCT
      cross-references when the related-existing row is in a resolved
      state AND the new card's mechanism clearly reproduces the
      original's even with fix-surface variation: log reasoning in the
      discussion entry, then either reopen (if mechanism reproduction
      is unambiguous) or escalate to operator (if surface variation
      makes the regression call HIGHLY ambiguous — but err on the side
      of reopening without asking). The default is action, not
      escalation; a reopened ticket has no cost the playbook cares
      about, while a missed regression silently buries a real defect on
      a closed row.

      Without this rule, Severe blind-judge verdicts get silently
      buried on `done` rows, the tracker's actionable-work view doesn't
      see them, and the same defect keeps getting "rediscovered" in
      every subsequent tranche.

  5. Record the returned tracker ID in
     ${TRANCHE_DIR}/tracker_materialization.tsv with columns:
     card_id | source_row | tracker_id_or_FOLDED_INTO | priority |
     parent_id | duplicate_classification | related_to (if applicable) |
     fold_target_status_at_fold (resolved state pre-mutation if any) |
     fold_target_status_after (status post-mutation; same if no change)

**Spec correction (from tranche 03 first-run findings):** tracker
enforces `kind=work_item` (not `invariant`) for `status=todo_hard`
because invariants require `statement`/`root_cause` fields and reject
todo_hard. Priority is an integer 0-4, not a "P"-prefixed string. The
sub-agent's first add-call may need to retry once after hitting these
constraints; that is normal and expected.

For each EXISTING tracker row that received related-but-distinct
GENUINE_EXTENSION findings during this tranche (per Phase 3a
classification.md), call `scripts/tracker discuss <ROW_ID>
"[+ ${TRANCHE_ID}: F<NN>.X1 ... — <1-line summary>]"` to record the
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
You are the Phase 6.2 agent-notes integration sub-agent for tranche
${TRANCHE_ID}. Read ${TRANCHE_DIR}/tranche_state.json,
${TRANCHE_DIR}/aggregate_summary.md, ${TRANCHE_DIR}/trend_cluster_aware.md,
${TRANCHE_DIR}/retrospective.md, ${TRANCHE_DIR}/post_discovery_audit.md
(Phase 2.5 disposition record — read its "Disposition for Phase 6.4"
section verbatim), and ${TRANCHE_DIR}/tracker_materialization.tsv
(Phase 6.1 output).

Read ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json. The
single editable field is `notes` (markdown). Plan the diff BEFORE
writing — produce a plan with these sections, each tied to a specific
replace_once OR append target in the current notes:

  1. Top-of-file header update (if the notes have a "Session theme" or
     "READ THIS FIRST" block, update it to reflect the new tranche's
     headline numbers: cohort count, mean severity, bucket-Σ-severity
     row, Significant+Severe percentage, link to the trend report).
  2. Active set count update (e.g., "Current active set: ~X items
     after tranche ${TRANCHE_ID} materialization").
  3. Issues table preamble update: scope=<total rows> after this
     tranche's additions, cumulative summary by tranche.
  4. New cohort sub-section under the corpus table: "Tranche
     ${TRANCHE_ID} cohort filed YYYY-MM-DD — N discrete rows" listing
     the materialized rows by family/cluster with priority + parent.
   5. Inline annotations on existing parent rows that received tranche
     extensions: cross-reference the new child rows.
  6. Severity / status distribution table: add a column for this
     tranche's cohort.
  7. Bucket-Σ-severity rollup: append a row to any existing trend
     section, format matching the prior tranche's row.
  8. Cross-tranche comparison note in the retrospective trace.
  9. Phase 2.5 disposition entry — record the post-discovery-audit
     disposition verbatim as a one-line entry under the tranche's cohort
     sub-section:
       - "Tranche ${TRANCHE_ID} Phase 6.4 disposition: skip — confirmed
         YYYY-MM-DD (Phase 2.5 found 0 would-have-been-pruned candidates,
         0 pattern-amplification warnings, 0 new INVALIDATION-RISKs)."
       - OR "Tranche ${TRANCHE_ID} Phase 6.4 disposition: trigger — N
         candidates pending operator edit-mode authorization (see tracker
         row tagged awaits_edit_mode_authorization filed YYYY-MM-DD)."
     If trigger, ALSO surface as a top-of-file TODO that subsequent
     sessions notice when they read agent_notes; the TODO is removed by
     Phase 6.4 when the cleanup actually runs.

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

Back up the prior notes file to /tmp/agent_notes-pre-${TRANCHE_ID}.json
before any edits.

Return a 200-word summary: edit count, sections updated, any
replace_once failures and how resolved.
```

### Step 6.3 — Cross-tranche index append

The parent agent appends a row to `~/hypergumbo_lab_notebook/dogfood_tranches_index.md` summarizing the tranche: ordinal, date range, headline bucket-Σ-severity, judge count, cohort count, link to retrospective. Creates the file if it doesn't exist.

### Step 6.4 — Optional retroactive cleanup (requires human edit-mode authorization)

If EITHER Phase 2's mid-tranche audit OR Phase 2.5's post-discovery audit identified content that should have been a new row but got folded into a parent row's discussion thread during real-time passes, OR if Phase 2.5 enumerated would-have-been-pruned candidates, the cleanup is structurally identical to the 2026-06-04 fold-audit Phase 2 pruning: surgically trim migrated content from parent discussion entries via `scripts/tracker delete-msg` (whole-entry tombstone) or `edit-msg-text` (in-place supersession) under the per-message edit-mode window.

The decision rule: **skip Phase 6.4 only if BOTH `midtranche_audit.md`'s pattern-amplification warnings AND `post_discovery_audit.md`'s would-have-been-pruned candidates section are empty.** Skipping based on Phase 2 alone is unsound because Phase 2 only saw the first-half passes.

This step requires **human edit-mode authorization** — the operator must enable edit-mode via the OS-perm-gated TUI control before the parent agent invokes the cleanup sub-agent. The agent prompts the operator with the planned ops count (derived from Phase 2 + Phase 2.5 candidates) and waits for authorization before proceeding. If authorization is not granted within a reasonable window, skip this step and document the deferred cleanup in `carry_forward.md`.

**Tracker-tag stripping (cleanup-time bookkeeping).** When Step 6.4 actually runs (operator authorized + sub-agent applied the candidates), the parent agent — as its closing action — strips the `awaits_edit_mode_authorization` tag from the tracker row Phase 2.5 filed at the trigger event, and adds a tracker discussion entry summarizing what was deleted/edited (with op counts and timestamps). The row itself is NOT deleted — it's converted from a pending-authorization beacon into a permanent record of the cleanup. If Phase 6.4 is deferred (no operator authorization within the window), the tag is NOT stripped; the row persists across sessions until cleanup runs OR the operator explicitly closes it via `tracker update --status wont_do --note "rationale"`.

**Agent-notes re-sync (post-mutation consistency).** Any retroactive tracker mutation to a tranche's cohort — whether Phase 6.4 cleanup, a follow-up duplicate-audit supersession, an off-by-one backfill, or any other tracker write touching `dogfood_tranche_${TRANCHE_ID}`-tagged rows — MUST be paired with a corresponding `agent_notes.json` update. Phase 6.2 ran ONCE during the initial Phase 6 (before any subsequent retroactive ops) and wrote a cohort listing that reflects the tracker state AT THAT MOMENT. Without a re-sync, the agent_notes listing drifts: headline counts become stale ("47 rows" when 12 are now superseded), supersession arrows go unrecorded, off-by-one fills don't appear. The re-sync uses the same `replace_once`-with-exact-match discipline Phase 6.2 used; the tranche-NN cohort sub-section is the anchor.

Concrete: every retroactive-tracker-mutation operation MUST be expressed as a paired (tracker op, agent_notes diff) tuple in the operation log. The sub-agent that performs the retroactive ops surfaces both halves; the parent agent verifies post-condition that the agent_notes cohort listing's effective-distinct count + per-row annotations match the tracker query result for `--tag dogfood_tranche_${TRANCHE_ID}` after the mutations land. Mismatch → halt and report.

### Step 6.5 — Carry-forward + tranche finalization

The parent agent writes:

- `${TRANCHE_DIR}/carry_forward.md` with: open methodology questions, severity rubric calibration adjustments, saturated probe classes, recommended Phase 0 changes for the next tranche, deferred Step 6.4 cleanup notes if any.
- The next tranche's Phase 0 sub-agent should be pointed at this `carry_forward.md` via the `carry_forward_in` field in its tranche state file.
- Finalize `${TRANCHE_DIR}/tranche_state.json`: set `phase` to `complete`, populate the per-step completion timestamps, touch `${TRANCHE_DIR}/tranche.complete` sentinel.

## Phase 7 — End-of-tranche agent_notes coherence sweep

Output: `agent_notes.json` restructured for coherence; `${TRANCHE_DIR}/coherence_sweep_report.md`; `tranche.coherence_swept` sentinel.

Runs ONCE per tranche, after Phase 6 has finalized. NOT after every Phase 6 edit — per-edit coherence checks risk myopia (the sub-agent tweaks based on per-step context rather than seeing the full picture). This phase steps back and audits the whole `agent_notes.json` once the tranche's contributions have settled.

The motivating gap, identified during tranche 03 review: `agent_notes.json` grows across sessions as each new tranche / fold-audit / materialization event appends its own "READ THIS FIRST" header + cohort listing + cross-tranche table. Phase 6.2 instructs the sub-agent to APPEND content; nothing was deduplicating. After three tranches the notes had three competing READ-THIS-FIRST headers, asymmetric cohort coverage (tranche 01 not enumerated; 02 and 03 yes), and redundant cross-tranche comparison tables in slightly different formats. Future agents reading the notes were inheriting drift.

Spawn:

```
You are the Phase 7 agent_notes coherence sweep sub-agent for tranche
${TRANCHE_ID}. Tranche ${TRANCHE_ID} just finished Phase 6.5. Your job is
to step back and restructure ~/hypergumbo_lab_notebook/guidance_log/
agent_notes.json's `notes` field once for the whole tranche — not to
make incremental edits, not to add new tranche-specific content (that
already happened in Phase 6.2), but to RESOLVE accumulated drift.

Read first:
  - ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json (full
    notes field).
  - ~/hypergumbo_lab_notebook/dogfooding_trend_combined.md (the
    auto-generated cross-tranche canonical source; use this to resolve
    contradictions between cross-tranche tables in agent_notes).
  - ~/hypergumbo_lab_notebook/dogfood_tranches_index.md (the canonical
    cross-tranche metadata table).
  - For each tranche NN with a dogfood_tranche_${NN} tag: `scripts/
    tracker list --tag dogfood_tranche_${NN}` row count.

Back up the current notes to /tmp/agent_notes-pre-phase7-${TRANCHE_ID}.json
BEFORE any edit.

Audit pass (read-only first, ALL audits before any edits):

  1. **READ-THIS-FIRST header proliferation.** Identify every section
     header labeled "READ THIS FIRST" or "CURRENT STATE" or equivalent.
     Each should be dated. Only the most recent (the just-completed
     tranche's) should remain in that role; older ones get demoted to
     "Historical: <date>" entries below, with their content preserved
     verbatim but no longer competing for top-of-file priority.

  2. **Cohort listing symmetry.** For each completed tranche (1-20,
     21-40, 41-60, etc. discovered via dogfooding_trend_combined.md or
     dogfood_tranches_index.md), check whether agent_notes enumerates
     its cohort rows. If NOT and the cohort has a tracker tag, the row
     count must match the tracker query — flag for backfill. If NOT
     and the cohort predates tracker tagging (e.g., tranche 01), surface
     this as an asymmetry; do NOT silently backfill, since the
     operator may have explicit reasons for the gap.

  3. **Cross-tranche comparison drift.** Identify all cross-tranche
     comparison tables in agent_notes. For each, compare against the
     canonical numbers in dogfooding_trend_combined.md + dogfood_
     tranches_index.md. Any divergence is staleness — replace with
     canonical, OR (if the table is making a different point) annotate
     why it diverges.

  4. **Cohort listing count vs tracker tag count.** For each tranche's
     cohort listing in agent_notes (where present), the enumerated
     active-row count must match `scripts/tracker list --tag dogfood_
     tranche_${NN} | wc -l` (or post-supersession effective count, if
     the listing is annotated for that). Mismatch → flag for fix.

  5. **Stale tracker IDs.** Spot-check tracker IDs mentioned in the
     notes against current tracker state via `scripts/tracker show
     <id>`. Specifically check rows mentioned with a specific status
     in the notes ("WI-foo (done)"); if the tracker now shows that
     row as todo_hard or violated, the parenthetical is stale.

  6. **Redundant table consolidation.** If two tables in agent_notes
     express the same cross-tranche comparison with different formatting
     or row order, keep one (the most complete) and reference it from
     the other.

  7. **Size and load-bearing-ness.** If agent_notes exceeds ~250K
     characters, flag for restructuring — fold older session-state
     sections into a "Historical sessions" appendix, keeping the
     current tranche and the immediate-prior tranche fully detailed.

After audit, plan the diff. Apply edits via replace_once() with
exact-1-match guarantee (NO fuzzy-replace; stale text is better than
silent overwrite). Document each edit in
${TRANCHE_DIR}/coherence_sweep_report.md with:
  - Audit findings (per item above).
  - Edit plan (replace_once calls intended).
  - Replace_once outcomes (successes + failures).
  - Backfill recommendations surfaced to operator (e.g., tranche 01
    cohort listing if absent).
  - Post-sweep size of agent_notes.

Touch ${TRANCHE_DIR}/tranche.coherence_swept. Return a 220-word summary.
```

### Step 7.1 — Operator review of surfaced asymmetries

If the Phase 7 audit surfaced a cohort-listing asymmetry that requires backfill (e.g., a pre-tagging tranche whose cohort isn't enumerated in agent_notes), the parent agent surfaces this to the operator for explicit authorization. Do NOT auto-backfill — the operator may have reasons for the asymmetry (e.g., the older tranche's enumeration would touch a large number of historical tracker rows, or the cohort is fluid).

If authorization is granted, spawn a backfill sub-agent that produces the missing cohort listing using whatever data is available (the older tranche's classification.md / clusters.md / verdicts file). The listing matches the format of the most recent tranche's cohort sub-section for symmetry.

### Step 7.2 — Why this is Phase 7 not Phase 6.6

Phase 6 ends when tranche finalization writes `tranche.complete`. The agent_notes coherence sweep operates on the WHOLE notes file across multiple tranches, not just this tranche's contribution — it's structurally a meta-phase. Numbering it Phase 7 marks the conceptual boundary: Phase 6 is tranche-scoped; Phase 7 is agent-notes-scoped. The new Phase 7 sentinel `tranche.coherence_swept` lives in the tranche directory because the sweep was triggered BY this tranche's completion, even though the artifact being modified is shared across all tranches.

## Tranche state file schema

Single source of truth for tranche state. Every sub-agent reads this file as its first action. The parent updates it after every sub-agent return.

```json
{
  "tranche_id": "string (ordinal)",
  "tranche_dir": "string (absolute path)",
  "vendor": "string",
  "tranche_size": "int (10-30)",
  "compact_every": "int (1-5)",
  "judge_count": "int (1-5)",
  "mid_tranche_pass": "int",
  "substrate_policy": "snapshot | regen-each | regen-on-suspicion",
  "first_pass_number": "int",
  "last_pass_number": "int",
  "current_pass": "int",
  "phase": "discovery | midtranche_audit | post_discovery_audit | consolidation | judging | retrospective | carry_forward | complete",
  "passes_complete": [
    {"pass_number": "int", "headline": "string", "invalidation_risks": ["string"]}
  ],
  "checkpoint_complete": "bool",
  "post_discovery_audit_complete": "bool",
  "consolidated_complete": "bool",
  "judged_complete": "bool",
  "retrospective_complete": "bool",
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
| `tranche.checkpoint` | Phase 2 sub-agent | Mid-tranche audit is complete |
| `tranche.post_discovery_audit` | Phase 2.5 sub-agent | Second-half post-discovery audit is complete |
| `tranche.consolidated` | Phase 3 sub-agent | Two-axis dedup is complete |
| `tranche.judged` | Phase 4 aggregator | All verdicts collected, aggregate summary written |
| `tranche.retrospective` | Phase 5 sub-agent | Retrospective is written |
| `tranche.complete` | Parent agent | All Phase 0-6 done; carry-forward written |
| `tranche.coherence_swept` | Phase 7 sub-agent | End-of-tranche agent_notes coherence sweep is done |

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
| Sub-agent crashes mid-chunk | Parent times out waiting for return | Re-spawn the sub-agent with the same prompt; the tranche state file lets it resume at the failed pass |
| Pass writeup partially written | Lab notebook file exists but no sentinel | Phase 1 sub-agent's first action is to check for incomplete writeups and continue them |
| INVALIDATION-RISK finding's fresh-substrate verification fails | Substrate regeneration command errors | Mark the finding KIND=INDETERMINATE in `tranche_state.json`; Phase 3 handles INDETERMINATE as a manual-review queue |
| Phase 3 sub-agent diverges from brief (e.g., consolidates everything) | Cluster count drops > 50% from raw F-number count | Re-spawn with tightened prompt; if it diverges again, fall back to single-axis dedup and document in retrospective |
| Judge verdicts have no clear majority | Phase 4 aggregator detects spread >= 2 tiers in >= 25% of cards | Spawn `--judge-count` additional judges; if still spread, escalate to operator and pause tranche |
| Parent's own context fills up before Phase 5 | Parent agent self-reports approaching context limit, OR Phase 4 / 5 needs to load many per-card files | Parent agents cannot programmatically self-`/compact` — the keystroke is user-typed. Two real recovery options: (a) delegate the file-heavy work to a Phase-4-aggregator or Phase-5-reporter sub-agent so the bulk reads happen in a dying sub-agent context, OR (b) split the parent's work across multiple sessions, using `tranche_state.json` + sentinels as the resumption point. Under normal operation the parent's context grows only by ~50-80K tokens across a full tranche (sub-agent return summaries + brief state reads), so this row should rarely fire. |
| Token budget exceeded | Configurable; checked at phase boundaries | Halt at current phase, mark tranche partial, write a partial-retrospective covering completed phases only |

## Cost & budget estimation

Per-tranche token estimates assuming Opus-class models throughout:

| Phase | Sub-agent count | Tokens per sub-agent | Phase total |
|---|---|---|---|
| 0 (setup) | 0 (parent only) | ~50K parent | 50K |
| 1 (discovery) | 10 (20 passes / 2-per-chunk) | ~400K each | 4M |
| 2 (mid-tranche audit) | 1 | ~200K | 200K |
| 2.5 (post-discovery audit) | 1 | ~200K | 200K |
| 3 (consolidation) | 3 (methodology + causal + card-writer) | ~250K each | 750K |
| 4 (judging) | judge_count × card_count | ~80K each | 80K × J × C (≈4.8M at J=1, C=60) |
| 6 (materialization + agent-notes integration) | 2 sub-agents | ~250K each | 500K |
| 5 (retrospective) | 1 | ~150K | 150K |
| 6 (carry-forward) | 0 (parent only) | ~50K parent | 50K |

For a **default tranche (J=1, C≈60)**, Phase 4 is ~4.8M; full tranche including Phase 2.5 + Phase 6 materialization ~6.7M. Matches the passes-21-40 panel's ~4M and is the cheapest configuration that still yields the bucket-Σ-severity convergence signal. **Upgrade option (J=3, C≈60):** Phase 4 jumps to ~14M; full tranche ~16M. Matches the passes-1-20 baseline (~18M for 198 verdicts) and gains inter-rater variance averaging. Upgrade is reversible — additional judges can be fanned out against the same cards in a follow-up step. **Floor (J=1, no card-writer separation):** saves ~500K but reintroduces same-actor bias; not recommended.

## Appendix A — tmux fallback for vendors without sub-agents

If the vendor doesn't expose sub-agent spawning, OR its sub-agent primitive doesn't yet satisfy the contract (prompt-in, summary-out, dies-on-return, optional parallelism), the procedure falls back to the `scripts/agent-supervisor` VENDOR_TABLE machinery. In the fallback, the agent runs all 20 passes in its own context window and the operator-run supervisor injects a context-flush keystroke (`/compact` for Claude Code; per-vendor equivalents from the §Vendor parity table; graceful-exit + respawn for any vendor without an equivalent) every `--chunk-size` passes via `tmux send-keys`.

The operator invokes the fallback via:

```bash
scripts/agent-supervisor dogfood-tranche start \
    --tranche-dir <path> \
    --first-pass <N+1> \
    --chunk-size 2 \
    --judge-count 1 \
    --vendor <claude-code|codex-cli|cursor|gemini-cli>
```

(This subcommand does not yet exist as of the playbook's filing. If you actually need the fallback, file a tracker item to extend the supervisor with the subcommand AND with a `context_flush_keystroke` column on `VENDOR_TABLE`. The supervisor's existing `cli_invocation` and `exit_keystroke` columns at `scripts/agent-supervisor:166-183` are reused, as is the WI-sakod session-start respawn branch for vendors whose context-flush degrades to graceful-exit + respawn.)

The supervisor spawns the agent CLI in a managed tmux session via `tmux_spawn_session` (already implemented), injects pass prompts via `tmux_send_line` (already implemented), injects the context-flush keystroke (NEW — the operator implements once per vendor after verification), and watches sentinel files in the tranche directory. Recovery semantics are equivalent to the sub-agent path: the tranche state file is the single source of truth, and any respawn reads it to resume.

The sub-agent path is preferred when available because:
- It does not require operator intervention to start.
- It does not depend on tmux being installed.
- It does not depend on the supervisor's vendor-specific keystroke table being verified for the vendor in use.
- Sub-agent contexts die deterministically; tmux-injected `/compact` depends on the CLI honoring the keystroke and the agent not having injected output that swallows it.

The fallback path is preferred only when the vendor lacks a viable sub-agent primitive AND the operator has verified the context-flush keystroke per the §Vendor parity table verification protocol.

## Tracker — agent_notes consistency discipline

The playbook treats the tracker and `agent_notes.json` as **paired representations of the same cohort** that must stay in sync across any operation that touches the cohort:

- **Initial materialization** (Phase 6.1 + 6.2): tracker rows are filed, then Phase 6.2 immediately writes the matching cohort listing to agent_notes. Single coordinated write.
- **Retroactive mutations** (Phase 6.4 cleanup, duplicate-audit supersessions, off-by-one fills, any tracker write touching `dogfood_tranche_${TRANCHE_ID}`-tagged rows): paired (tracker op, agent_notes diff) tuples. The sub-agent that performs the retroactive op MUST also perform the matching agent_notes edit, OR the parent agent does it as the closing action. Either way, the cohort listing's effective-distinct count + per-row annotations must equal the tracker query result for the tag.
- **Post-condition check** after any retroactive batch: `scripts/tracker list --tag dogfood_tranche_${TRANCHE_ID}` row count == count claimed in agent_notes cohort listing; supersession status reflected in agent_notes; off-by-one fills present in agent_notes.

Without this discipline, the tranche's cohort drifts between representations and future agents reading agent_notes get a stale picture (wrong headline numbers, missing supersessions, unrecorded fills). The drift is invisible until a third party tries to reconcile.

The first run of this playbook (tranche 03) exposed this gap directly: Phase 6.2 wrote the cohort listing once during initial Phase 6, then a retroactive duplicate audit found 12 supersession candidates and the playbook did not remind the operator that the agent_notes listing also needed updating. The discipline above is the corrective.

## Tracker-tag discipline

The playbook uses two tracker-tag conventions for cross-session state-of-decision discovery, both mirroring AGENTS.md's existing `awaits_bakeoff_validation` pattern:

- **`dogfood_tranche_${TRANCHE_ID}`** (set at Phase 6.1, permanent): every materialized row for the tranche's cohort carries this tag for cohort-level queryability. Stripping is not part of the playbook — the tag is the cohort's permanent membership marker.
- **`awaits_edit_mode_authorization`** (set at Phase 2.5 if disposition is "trigger", stripped at Phase 6.4 when cleanup runs): a single per-tranche row exists ONLY when Phase 2.5's disposition is "trigger Phase 6.4 with N candidates" AND the cleanup has not yet been executed. The tag is the cross-session surfacing mechanism — the next session's agent discovers the pending work via standard `scripts/tracker list --tag awaits_edit_mode_authorization` queries.

The single source of truth for the pending-cleanup queue is `scripts/tracker list --tag awaits_edit_mode_authorization`. Stop-hook nudging for this tag (similar to the `awaits_bakeoff_validation` nudge integration) is a separate hook-code change outside this playbook's scope; until that ships, the discoverability path is: agent starts session → reads agent_notes (Phase 6.2 wrote the disposition there) → finds tranche-NN Phase 6.4 TODO → queries the tracker tag → surfaces to operator. Skip-confirmed dispositions do not create a tagged row; their record-of-decision lives in agent_notes only.

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
- `sycophancy-lit-review.md` (lab notebook) — empirical basis for the structural-blinding design choice; see Christian & Mazor (2026) on why declarative blinding ("ignore the biasing info") fails, and the broader literature on why introspection-based bias defenses are theatrical.
