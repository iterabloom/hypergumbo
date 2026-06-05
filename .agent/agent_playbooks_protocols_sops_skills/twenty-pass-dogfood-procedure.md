<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Twenty-pass dogfood procedure

A soup-to-nuts, vendor-neutral procedure for running a 20-pass dogfooding tranche on hypergumbo (or any comparable tool that ships a CLI, an analysis substrate, and a tracker). Integrates the strengths of the passes-1-20 and passes-21-40 campaigns: 1-20's up-front two-axis consolidation criteria + per-cluster confidence levels + rejected-clusters log, 21-40's F-number provenance + INVALIDATION discipline + mid-stream audit posture. Uses sub-agent orchestration so the operator does not have to babysit a tmux session.

## Design principle: de-primed discovery, parent-owned numbering

Two structural invariants govern every sub-agent prompt in this procedure. They exist because a line-by-line review of an earlier run found that the per-prompt scaffolding was, on balance, *adding* bias rather than catching it: workers were starved of the project's real-issue list, so the orchestrator compensated with scaffolding (a thin distilled digest, parent-pre-assigned probes, workers reading each other's raw output) and leaked campaign-position and expectation cues into every prompt — which biased discovery toward finding less and pre-judging novelty, then reported the resulting low yield as a trend signal, partly circularly.

1. **Discovery workers receive the real-issue registry, not a biased expectation.** The cumulative, de-duplicated ledger of every distinct defect surfaced across prior tranches lives in `agent_notes.json` (see §The real-issue registry below). Every discovery and audit sub-agent is handed that registry and navigates toward what is **not** already in it. Workers are never told to "expect re-confirmation," never told to pre-judge a finding's novelty, and never handed another worker's raw findings. The downstream dedup gates (Phase 2/2.5 ratchets, Phase 3a/3b causal+methodology dedup, Phase 6.1 broad-tracker scan) remain the authoritative novelty decision — the discoverer's job is to observe and describe, not to decide what is new.

2. **The parent owns all global numbering; workers are campaign-position-blind.** A worker never sees "tranche NN" or a global pass number. It works in terms of local, opaque labels (this chunk's pass 1, 2; findings `A1`, `A2`); the parent stamps the global identity (`pass_61.md`, `F61.A1`, the chunk→{61,62} map) only on aggregation, and holds the ordinal↔token mapping its trend carbon-dating needs. This mirrors how Phase 3c already mints `anon-card-NNN` and keeps the unblinding key parent-side.

A worker prompt therefore contains exactly three things: its **task**, its **inputs**, and its **output format** — nothing about its place in the campaign, nothing about being evaluated, nothing pre-judging its findings. Every prompt template below is written to that rule; §Appendix B — de-priming rationale records, per cut, what was removed and why.

## The real-issue registry

`agent_notes.json` (at `~/hypergumbo_lab_notebook/guidance_log/`, the agent's persistent handoff document) doubles as the **canonical, complete registry of every distinct issue discovered across the dogfood tranches** — the cumulative cross-tranche discovery ledger. It is maintained as an **issues table** — one row per distinct issue, columns `Tracker ID | Pri | Status | Parent | Title` — complete and validated and consistent with its materialized tracker rows. "Curated" means **complete and clean, not thinned**. Every discovery worker (and every audit worker) is handed this registry as a primary input so it can navigate toward unmapped ground with real information instead of a biased expectation.

New findings enter the registry the same way they leave it — **as appended table rows** (Step 1.2 #5). That is precisely what keeps the per-chunk fold distilled and neutral: a row has columns for an id, a priority, a status, a parent, and a one-line title, and **no column for a severity rationale, a KIND tag, a novelty judgment, or a fold disposition**. The table schema structurally cannot carry the anchoring content that a raw findings-file dump (the removed D1 cross-worker read) would. In-tranche, the appended rows use a provisional handle in the `Tracker ID` slot (the real tracker ID is minted at Phase 6.1); priority may be blank until materialization.

**Two senses of "all real issues" — do not conflate them.** (1) The *discovery ledger* — what this registry is, and what loads into workers: the cross-tranche set of distinct dogfood-discovered issues. (2) *Integration into the entire tracker* — a separate operation (Phase 6.1 materialization) that reconciles those discovered issues against the project-wide tracker, which holds issues from all sources (human-filed, non-dogfood). "Kept in sync with the tracker" means narrowly that each ledger entry stays consistent with its tracker row (id/status) — **not** that the registry ingests every tracker item. The registry is the discovery subset; the tracker is the superset source of truth.

**De-primed by relocation, not deletion.** The registry must carry no campaign-measurement framing — trend means, bucket-Σ tables, "READ THIS FIRST tranche NN" headers, cohort-by-tranche rollups, "expect re-confirmation." That content is *not a real issue*; it lives in the parent-facing trend/index files (`trend_cluster_aware.md`, `dogfooding_trend_combined.md`, `dogfood_tranches_index.md`), which are the canonical home for it. The per-issue registry stays in full, **priorities included** — priorities are attributes of issues, not campaign expectations. Phase 7's coherence sweep is the ongoing enforcer of this boundary: keep the ledger complete, keep measurement-framing out, and don't let it bloat into a tracker clone.

## Purpose & scope

A "tranche" is a contiguous block of 20 dogfooding passes against the same substrate (or substrate family). The procedure produces:

- An immutable per-pass raw-observations record (lab notebook stanzas with F-numbered findings).
- TWO audit passes — a Phase 2 mid-tranche checkpoint on the first-half passes and a Phase 2.5 post-discovery audit on the second-half passes — that both feed Phase 3 consolidation and Phase 6.4's optional retroactive-cleanup decision.
- A two-axis consolidated cohort (methodology-axis + causal-axis) with per-cluster confidence and a rejected-clusters log.
- A blind-judge severity panel (default 1-judge; tunable up to 3-judge or higher for inter-rater variance).
- A cluster-aware trend report (single carbon-dating attribution: earliest cluster member; the former Methods B/C collapse to it while every cluster is same-pass, which has held to date — see Phase 5.1).
- A retrospective audit covering KIND-tag stability, rejected-clusters scoring, and methodology-question carry-forward.
- A carry-forward queue for the next tranche.

Use this procedure when you want a methodology-comparable dogfooding tranche — i.e., when you expect to compare its trend report to a prior tranche's, or treat it as a baseline for future ones. For one-off audit sweeps that don't need cross-tranche comparison, the lighter-weight `self-analysis-dogfooding-playbook.md` is appropriate.

## When NOT to use

- **Tranche-size mismatch.** Tranches smaller than ~10 passes don't yield enough buckets to produce a meaningful trend signal; larger than ~30 passes blow past the parent agent's working-context budget even with sub-agent chunking. Stay near 20.
- **Substrate is mid-flight changing.** If the codebase is being actively refactored during the tranche, per-pass findings will drift for reasons unrelated to discovery dynamics, and the consolidation work will be wasted. Freeze a substrate snapshot first (see Phase 0 step 2).
- **No prior baseline to compare against AND no plan to compare against future tranches.** The procedure's overhead is justified by cross-tranche comparison; without that audience, run the lighter playbook.

## Architecture: sub-agent orchestration

The parent agent (the one reading this playbook) owns the phase loop, the tranche state file, the final trend report, **all global numbering** (the chunk→pass map and the local-label→F-number map; see §Design principle), and the **curated real-issue registry** it injects into every discovery and audit worker. It does NOT directly run the 20 passes — it spawns sub-agents in chunks of `--chunk-size` passes (default 2) and aggregates their return summaries, stamping the global identity onto each returned artifact at aggregation time.

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
| `--substrate-policy` | snapshot | snapshot \| regen-each \| regen-on-suspicion | When to regenerate the analysis substrate. snapshot = once, at Phase 0. regen-each = every pass (expensive). regen-on-suspicion = whenever a multi-pass negative finding needs fresh-substrate verification. |
| `--discovery-mode` | region-parallel | region-parallel \| sequential | How Phase 1 chunks avoid colliding on the same surface. `region-parallel`: the parent assigns each chunk a broad *region* of the analysis output (multi-modal-sweep style) and folds each wave's accepted issues into the registry between waves — faster wall-clock. `sequential`: chunks run one after another, each handed the registry updated with the prior wave's accepted issues — cleaner shared state, slower. Both rely on the registry (not narrow pre-scripted probe checklists, and not cross-worker raw-output reads) for collision-avoidance. |

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

Also assemble `${TRANCHE_DIR}/judge_primer.md` — the **domain primer + the full project spec (`docs/hypergumbo-spec.md`, inlined) + the severity rubric** (the proven `dogfooding_blind_judge_primer.md` baseline, ≈155 KB). This is a superset of `substrate_guide.md`: the Phase 4 blind judges receive it (not the lighter substrate guide) because deciding whether a substrate observation is *wrong* requires the spec's statement of correct behavior, not just the rubric. Lock it at Phase 0 alongside the substrate guide so neither drifts mid-tranche.

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
  "judge_count": 1,
  "mid_tranche_pass": 10,
  "substrate_policy": "snapshot",
  "discovery_mode": "region-parallel",
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
  "registry_file": "<absolute path to the curated real-issue registry staged in Step 0.6>",
  "carry_forward_in": "<path to prior tranche's carry_forward.md or null>",
  "started_at_utc": "<ISO 8601>"
}
```

Write to `${TRANCHE_DIR}/tranche_state.json`. The parent reads and updates it across the whole loop. Discovery and audit workers do **NOT** read it — it carries global numbering and campaign position, which would break their blinding; they read only their staging directory. Consolidation-stage sub-agents (Phase 3), which legitimately work over the full provenance, may read it.

### Step 0.6 — Seed the tranche's working registry

Seed the tranche's **working registry** from the canonical real-issue ledger (see §The real-issue registry). This file is the parent's live known-state for the tranche: it starts as a copy of the canonical `agent_notes.json` registry and **accrues each chunk's new findings as the tranche runs** (Step 1.2 #5). Every discovery and audit worker reads it, so it is the sole collision-avoidance mechanism — workers never read each other's raw output.

```bash
cp ~/hypergumbo_lab_notebook/guidance_log/agent_notes.json \
   "${TRANCHE_DIR}/registry.json"
```

Before seeding, confirm the canonical ledger is **complete and clean** — every distinct prior-tranche issue present with id/description/mechanism/status/priority, and **no campaign-measurement framing** (no trend means, bucket-Σ tables, "READ THIS FIRST tranche NN" headers, or "expect re-confirmation" language). If measurement framing is present, it is a Phase 7 coherence-sweep debt from the prior tranche; relocate it to the trend/index files now rather than injecting it into this tranche's workers. Record `registry.json`'s absolute path in `tranche_state.json`'s `registry_file` field.

Two properties make the working registry a navigation aid (what ground is already mapped) rather than a biasing expectation (what a worker is likely to find) or an anchor (another worker's dispositions):

- **It is distilled and neutral.** Each entry is one issues-table row (`Tracker ID | Pri | Status | Parent | Title`) — the schema has no column for a severity, a novelty judgment, a KIND tag, or a fold disposition, so the fold structurally cannot carry them. The parent distills the worker's prose into that row when folding a chunk's findings in (Step 1.2 #5). This is the difference between the registry and the removed cross-worker raw-output read.
- **In-tranche updates are local; canonical reconciliation is deferred.** The working registry may hold provisional in-tranche findings; the *validated, deduped* cohort is reconciled back into the canonical `agent_notes.json` only at Phase 6.2. A finding the dedup stages later reject therefore never permanently pollutes the cross-tranche ledger.

## Phase 1 — Discovery (per-pass sub-agent chunks)

Output: `${TRANCHE_DIR}/pass_NN.md` per pass (immutable after written), `${TRANCHE_DIR}/pass_NN.complete` sentinel per pass, `passes_complete` array in tranche state.

### Step 1.1 — Chunk loop

For each chunk of `--chunk-size` passes (default 2), the parent prepares two things, then spawns a discovery sub-agent:

- **A staging/working directory** `${TRANCHE_DIR}/.staging/discovery_<chunk>/` containing the curated real-issue registry (`registry.json`), the substrate guide (probe-class catalog + severity rubric), and — under `--discovery-mode=region-parallel` — a one-line `region.txt` naming a broad surface of the analysis output to probe (multi-modal-sweep style, chosen to avoid colliding with concurrent chunks). Under `--discovery-mode=sequential`, the staged registry already reflects the prior wave's accepted issues and no region need be assigned.
- **The local↔global mapping**, kept parent-side and never given to the worker: chunk → {global pass numbers}, and (after return) local-label → F-number.

The worker is campaign-position-blind: it never learns its tranche ordinal or its global pass numbers. Prompt template:

```
You are a discovery sub-agent. Your working directory is
${working_dir}. Read only files within it; the parent has staged
exactly what you need: registry.json (the registry of issues
already discovered), substrate_guide.md (probe-class catalog + severity
rubric), and — if present — region.txt (the surface to probe this run).

Your task: run ${chunk_size} independent discovery passes over the
analysis substrate at ${substrate_file}, recording what its output
actually shows — defects where they exist, AND correct behavior where
you verify it (a confirmed-correct surface is a real finding, not a
non-result). Number your passes locally 1..${chunk_size}. For each pass:

  1. Choose a probe from the substrate guide's probe-class catalog.
     registry.json lists the issues already discovered. Use it to make
     an INFORMED choice, not to avoid known ground: usually the most
     information is on surfaces it does NOT yet cover, but deliberately
     re-testing a KNOWN issue on fresh substrate is legitimate and
     valuable — it catches regressions and confirms or overturns a prior
     claim. The registry tells you what is known so you can aim where the
     truth is most uncertain, not so you must steer clear of it. If
     region.txt is present, stay within that surface. You are not
     assigned specific probes; pick what looks most informative.
  2. Execute the probe. Capture all command output to files under
     ${working_dir}/probe_<local_pass>_*.log per the output-capture-
     long-running discipline.
  3. Record findings, labeled A1, A2, … per local pass. Each finding:
       - A 1-2 sentence neutral headline (describe what you observed;
         do not assess novelty or severity).
       - The substrate evidence (file:line + values, OR command + output).
       - Whether it is a POSITIVE observation (output looks correct) or
         a NEGATIVE one (something absent / zero / broken).
     Do NOT assign a KIND tag, a fold/standalone hint, or a novelty
     judgment — dedicated dedup stages decide those downstream, and a
     discoverer's guess only anchors them.
  4. NEGATIVE findings are the highest-risk class for multi-pass
     mismeasurement. Before recording any substantive negative finding,
     regenerate the substrate fresh and confirm the finding reproduces.
     Note the verification outcome inline ("regenerated at <path>;
     reproduced" / "did NOT reproduce — likely a substrate artifact").
  5. Write each pass's findings to
     ${working_dir}/observations_<local_pass>.md as immutable
     lab-notebook stanzas, and touch
     ${working_dir}/observations_<local_pass>.complete.

Return a short summary (<=200 words):
  - Headline findings (local labels + 1 line each).
  - Negative findings and their fresh-substrate verification outcome.
  - Probe classes / surfaces you did NOT reach, for the parent to route
    to another chunk.
  - Optionally, plainly: if anything about the data or tooling slowed
    or misled you, note it.
Do NOT return the full writeups — they live in the per-pass files. Do
NOT report timing or token counts; the parent reads those from the
tool result.
```

### Step 1.2 — Parent aggregation (numbering + registry update)

After each sub-agent returns, the parent — and ONLY the parent — stamps global identity onto the worker's local artifacts:

1. Reads the chunk summary from the sub-agent's return value, plus the worker's per-pass observation files from its working directory.
2. **Stamps global numbering.** For each local pass `k` in the chunk, moves `observations_k.md` → `${TRANCHE_DIR}/pass_<global>.md`, rewriting the local finding labels (`A1`, `A2`, …) to global F-numbers (`F<global>.A1`, …) in that canonical copy, and touches `pass_<global>.complete`. Records the chunk→{global passes} map and the local-label→F-number map in `tranche_state.json` (the trend carbon-dating needs this ordinal↔token mapping parent-side anyway).
3. **Captures resource stats** from the tool result (duration, tokens, tool uses) into the chunk's `tranche_state.json` record — never from a worker self-report.
4. Updates `tranche_state.json`: `current_pass` advances; `passes_complete` gets each global pass + its 1-line headline.
5. **Folds new issues into the working registry as table rows.** For each genuinely-new finding the chunk surfaced, the parent appends ONE row to `registry.json`'s issues table — `<provisional-handle> | <pri or blank> | new-this-tranche | <parent or blank> | <neutral one-line title>` — distilling the worker's prose into the structured row and dropping any severity / novelty / disposition language. This is the whole collision-avoidance mechanism: the next worker reads the updated table and steers away from mapped ground, without ever seeing another worker's raw output. Under `--discovery-mode=sequential` the parent does this before spawning the next chunk; under `region-parallel` it folds each returned wave between waves and assigns the next chunk a non-overlapping region. (Provisional handles become real tracker IDs at Phase 6.1; the canonical `agent_notes.json` is reconciled at Phase 6.2.)
6. If `--mid-tranche-pass` was just reached, transitions to Phase 2 (next chunk is the audit chunk). Otherwise spawns the next Phase 1 chunk.

### Step 1.3 — Fresh-substrate verification protocol

Every substantive NEGATIVE finding carries an inline verification outcome (Step 1.1 #4). The parent handles the two outcomes at aggregation:

- "regenerated at <path>; reproduced" — the finding proceeds as substantive.
- "regenerated at <path>; did NOT reproduce" — the finding is recorded as a substrate artifact, not a defect, and the parent checks whether any registry entry it appeared to contradict needs a Phase 3 review flag (a non-reproducing negative can be evidence that a previously-recorded negative was itself a mismeasurement). Phase 3 consolidation decides whether to invalidate the prior entry or keep it as a methodology-calibration record.

This catches multi-pass false-claim chains before they amplify into deep mistakes. The discipline is applied at finding-time by every worker, not retroactively by an auditor who must reconstruct which pass first asserted the claim — so no worker needs to see another worker's findings to apply it.

## Phase 2 — Mid-tranche checkpoint

Output: `${TRANCHE_DIR}/midtranche_audit.md`; `tranche.checkpoint` sentinel; any pass writeups annotated with KIND-tag ratchets.

After pass `--mid-tranche-pass` completes (default pass 10 of the 20-pass tranche), the parent stages the first-half passes into `${TRANCHE_DIR}/.staging/audit_first_half/`, renamed `local_1.md … local_<k>.md` (relative order within this tranche — the global numbering is stripped so the auditor stays campaign-position-blind), plus `registry.json`. Then it spawns a Phase 2 sub-agent:

```
You are a mid-tranche audit sub-agent. Your working directory is
${working_dir}; the parent staged the first-half passes there as
local_1.md … local_${k}.md (relative order within this tranche — you
are not told their global numbering) plus registry.json.

Your job is a CHECK, not a consolidation: catch over-folding, over-
splitting, or amplification in these passes before the second half
runs, symmetrically with the post-discovery audit on the second half.
Do NOT attempt full consolidation (Phase 3 does that), and do NOT
decide novelty against prior tranches (the dedup stages do that).

Across the staged passes:
  1. Flag over-splitting (distinct findings that look like one
     underlying issue stated twice) AND over-folding (findings recorded
     together that are actually separate issues). Name the candidates
     neutrally; do not assign final dispositions.
  2. Flag negative findings recorded without a fresh-substrate
     verification outcome; apply the Step 1.3 fresh-substrate protocol
     to any you find.
  3. Flag pattern-amplification: a claim re-asserted across 3+ of these
     passes without fresh-substrate verification, or a number re-cited
     without fresh derivation.

Write to ${working_dir}/midtranche_audit.md with sections:
  - Over-split / over-fold candidates (named neutrally).
  - Negative findings missing fresh-substrate verification.
  - Pattern-amplification warnings.
  - Whether the un-probed surfaces suggest a second-half direction.

Return a <=150-word summary.
```

On return, the parent moves `midtranche_audit.md` to `${TRANCHE_DIR}/`, restoring global pass references from its local↔global map, and touches `${TRANCHE_DIR}/tranche.checkpoint`. The parent then continues Phase 1 with the remaining passes, using the audit's "second-half direction" note to route the next chunk's region. The midtranche audit is **parent-facing**: it informs region routing but is NOT staged into second-half discovery workers (whose only inputs remain the registry, the region assignment, and the substrate guide — staging the audit's within-tranche findings would prime them).

## Phase 2.5 — Post-discovery second-half audit

Output: `${TRANCHE_DIR}/post_discovery_audit.md`; `tranche.post_discovery_audit` sentinel; any second-half pass writeups annotated with KIND-tag ratchets.

After the FINAL Phase 1 chunk completes (passes `--mid-tranche-pass + 1` through `--tranche-size` have all written their `pass_NN.md` + `pass_NN.complete`), and BEFORE Phase 3 starts, spawn a Phase 2.5 sub-agent that does the symmetric Phase 2 protocol on the second-half passes. Without this step, the first-half passes get a KIND-ratchet / amplification-warning audit but the second-half passes don't — they go straight into Phase 3 consolidation, which can absorb over-folding or amplification as if it were intentional.

The motivating gap: the first run of this playbook (tranche 03, passes 41-60) skipped this step and the parent agent could not honestly answer "is there content to prune?" for the second-half passes — Phase 2 only covered passes 41-50, and Phase 3 / 5 looked at the second half but with different questions (consolidation, retrospective) rather than the Phase 2-style ratchet/amplification check.

The parent stages the second-half passes into `${TRANCHE_DIR}/.staging/audit_second_half/`, renamed `local_1.md … local_<k>.md` (global numbering stripped), plus the first-half audit as `prior_audit.md` (also de-globalized) and `registry.json`. Then it spawns a Phase 2.5 sub-agent:

```
You are a post-discovery audit sub-agent. Your working directory is
${working_dir}; the parent staged the SECOND-HALF passes there as
local_1.md … local_${k}.md (relative order within this tranche — you
are not told their global numbering), the first-half audit as
prior_audit.md, and registry.json.

Your job is a CHECK, not a consolidation: catch over-folding, over-
splitting, and amplification in these passes — symmetrically with the
mid-tranche audit on the first half — before Phase 3 consolidation
absorbs the patterns as intentional. Do NOT attempt full consolidation
(Phase 3 runs after you), and do NOT decide novelty against prior
tranches.

Across the staged passes:
  1. Flag over-splitting and over-folding candidates, named neutrally;
     no final dispositions.
  2. Flag negative findings recorded without a fresh-substrate
     verification outcome — including any that would reach back and
     contradict a finding in prior_audit.md (a second-half observation
     that quietly invalidates a first-half claim). Apply the Step 1.3
     fresh-substrate protocol to any you find.
  3. Flag pattern-amplification: claims re-asserted across 3+ of these
     passes without fresh-substrate verification, or numbers re-cited
     from earlier passes without fresh derivation.
  4. Flag would-have-been-pruned content: any finding whose evidence
     base is too thin to file as a tracker row under Phase 6.1, or any
     cross-pass repetition better recorded as a note on an earlier
     finding than as a standalone one.

Write to ${working_dir}/post_discovery_audit.md with sections:
  - Over-split / over-fold candidates (named neutrally).
  - Negative findings missing fresh-substrate verification (including
    any reaching back to first-half findings).
  - Pattern-amplification warnings.
  - Would-have-been-pruned candidates (the explicit input to whether
    Phase 6.4 retroactive cleanup should fire).

Return a <=180-word summary.
```

On return, the parent moves `post_discovery_audit.md` to `${TRANCHE_DIR}/`, restoring global pass references from its local↔global map, and touches `${TRANCHE_DIR}/tranche.post_discovery_audit`.

The Phase 2.5 sub-agent's output feeds two downstream decisions:

1. **Phase 3 consolidation** reads `post_discovery_audit.md` alongside `midtranche_audit.md` as input. Both audits' over-split / over-fold candidates and pattern-amplification warnings are starting points for Phase 3a methodology-axis classification — to confirm or reverse on the evidence, not verdicts to defer to.
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
second-half audit). Both audits' over-split / over-fold candidates and
pattern-amplification warnings are starting points to confirm or
reverse on the evidence — not verdicts to defer to.

For each finding (every F-number across every pass), assign a final
KIND from the 6-way taxonomy (findings are NOT pre-tagged at discovery
time, so this is a fresh assignment, not a re-evaluation):
  - INDEPENDENT — single investigation; no co-emitted siblings.
  - AUDIT-AXIS — one of N rows produced by a single enumerative sweep.
    Mark with the sweep identifier.
  - EXTENSION — explicitly extends an earlier finding's scope.
  - CORRECTION — revises or replaces an earlier finding.
  - POINTER-ONLY — no local information; refers to another row.
  - INVALIDATION — negates a prior finding via fresh-substrate evidence.

For each finding, also assign a confidence level (HIGH / MED / LOW)
to the KIND assignment. HIGH = the kind is unambiguous from the
finding's evidence. MED = inferable from context. LOW = judgment call.

Record the structural fact each label implies: an AUDIT-AXIS row's
sweep identifier, an EXTENSION/CORRECTION row's parent F-number, an
INVALIDATION row's invalidated target. Do NOT compute or reason about
how any label rolls up into a count or a trend total — assign each
label purely on what the finding is. The parent applies the rollup
downstream; coupling your classification to the metric would be
labeling to the test.

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
  - Earliest pass (the carbon-dating anchor — earliest cluster member;
    this is the single attribution method, see Phase 5.1).
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

  Card ID: anon-card-NNN (NNN zero-padded; OPAQUE — it must carry no
    tranche ordinal and no pass/F-number, because the blind judge reads
    this line. The parent keeps the tranche<->card association in
    mapping_anon_to_source.tsv, never in the card itself).
  Subject: 1-line topic.
  Mechanic: how the behavior is produced.
  Expectation: what the substrate would look like if correct.
  Scope: which surface / kind / command is affected, with magnitude.
  Exposure: under what circumstances the behavior occurs, and what (if
    anything) encounters it — state it factually, do not assume a victim.
  Consequence: what, if anything, downstream depends on this being
    correct — state it neutrally and let the judge weigh severity; do
    not assert harm the evidence does not show.

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

For each card × each judge in the `--judge-count` ensemble (default 1), spawn a judge sub-agent. Same structural-blinding pattern as Phase 3.3: the parent stages exactly the allowed inputs in a per-judge staging directory before spawning. `${CARD_ID}` here is the **opaque** `anon-card-NNN` minted in Phase 3c — it carries no tranche ordinal or pass identity, so neither the staging path nor `card.md` leaks campaign position to the judge.

The staged primer is `judge_primer.md` — the **domain primer + the full project spec + the 1-5 severity rubric** (the proven `dogfooding_blind_judge_primer.md` baseline, ≈155 KB with `docs/hypergumbo-spec.md` inlined), assembled once at Phase 0. It is NOT merely the tranche-local `substrate_guide.md`: a judge that must decide whether the substrate is *wrong* needs the spec's statement of what correct looks like, not just the rubric. Describe it accurately to the judge so it actually consults the spec sections.

```bash
mkdir -p "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}"
cp "${TRANCHE_DIR}/judge_primer.md" \
   "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}/"
# Extract the single card matching ${CARD_ID} from cards_blinded.md
python3 extract_card.py "${TRANCHE_DIR}/cards_blinded.md" "${CARD_ID}" \
   > "${TRANCHE_DIR}/.staging/judge_${JUDGE_INDEX}_card_${CARD_ID}/card.md"
```

Sub-agent prompt:

```
You are a blind severity judge. Your working directory is
${working_dir}. Read only files within it; the parent has staged
exactly judge_primer.md (the domain primer + full project spec +
1-5 severity rubric) and card.md (the single card you judge).

Score the card on the 1-5 severity scale per the rubric. The card
describes a defect; consult the project-spec sections of the primer
when the card's severity hinges on what the tool is supposed to do.
Emit a JSON object on stdout (read card_id from the `Card ID:` line of
card.md; the orchestrator stamps the judge index):
  {"card_id": "<from card.md>", "severity": INT,
   "severity_label": "Cosmetic|Minor|Moderate|Significant|Severe",
   "rationale": "<2-4 sentence justification anchored in the rubric>"}

Do not write to any file. The orchestrator captures your stdout.
```

**Per-card vs batched isolation (document which you used).** At `--judge-count >= 2`, spawn **strictly one sub-agent per (card, judge)** and run them in parallel where the vendor allows — inter-rater independence is the whole point of an ensemble, and it is destroyed if one judge sees multiple cards or another judge's score. At `--judge-count == 1`, there is no inter-rater comparison to corrupt, so batching several cards into one judge sub-agent is acceptable **iff** the prompt scores each card on an absolute rubric basis (never "card X is worse than card Y"); this trades strict per-card isolation for far fewer spawns. Record which mode was used in `aggregate_summary.md` so a later reader knows whether the isolation guarantee was strict or batched.

The staging-directory pattern is the structural countermeasure. Where the vendor's sub-agent primitive permits constraining filesystem access (a sandbox flag, a working-directory restriction), use that in addition. See §Vendor parity table column "Structural blinding mechanism" for vendor specifics.

The parent collects each judge's verdict, stamps the `judge_index` it spawned that sub-agent with, and writes `verdicts_judge_${JUDGE_INDEX}.jsonl` for each judge.

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
- Per-pass COUNT table (single attribution method: earliest cluster member — see the carbon-dating note below).
- Per-pass MEAN BLIND SEVERITY table.
- Per-pass SEVERITY SUM table (the column the 1-20 baseline didn't ship; included by default here).
- Count + severity summary metrics (slope, early/late means, late-minus-early delta).
- Sanity checks (Σ-severity total matches the cohort; every cluster confirmed same-pass, validating the single-method collapse; etc.).
- Cluster inventory.
- Bucket-level 5-pass Σ-severity rollup for cross-tranche comparison.

**Carbon-dating: single method until a multi-pass cluster appears.** The original design carried three carbon-dating methods — A (earliest cluster member), B (latest member), C (1/n distributed across members). They differ only for *multi-pass* clusters; for a single-pass cluster (or a singleton) A = B = C identically. Every cluster across every tranche to date has been same-pass, so the three methods have produced identical numbers every time and the breakdown has never once disambiguated anything. This procedure therefore reports a **single** per-pass attribution — earliest cluster member (the former Method A) — and a same-pass sanity assertion. The first time a genuinely multi-pass cluster appears, the same-pass assertion fails loudly; re-enable the A/B/C three-method breakdown at that point. Collapsing now removes three near-duplicate tables that added reader load without adding signal.

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
  - ${TRANCHE_DIR}/midtranche_audit.md and
    ${TRANCHE_DIR}/post_discovery_audit.md (the two audits).
  - ${TRANCHE_DIR}/classification.md and ${TRANCHE_DIR}/clusters.md
    (the consolidated cohort).
  - ${TRANCHE_DIR}/rejected_clusters.md (the consolidation decisions log).

Produce a retrospective covering:
  1. KIND assignment stability: did the two audits' over-split /
     over-fold candidates survive Phase 3 classification, or did Phase 3
     reverse them? Where the audits and Phase 3 disagreed, which call
     held up against the evidence?
  2. Rejected-clusters scoring: did the rejected-clusters log catch any
     cluster you would have collapsed under one-shot consolidation?
     Name specific cases.
  3. Carry-forward to the next tranche: methodology questions and
     calibration adjustments ONLY. Do NOT pre-steer the next tranche's
     discovery — no "these probe classes are saturated, look elsewhere",
     no forecast of what it will find. The registry already conveys what
     has been found, and the next tranche's workers navigate from it.
     Carry-forward is for process facts (e.g., "the judge primer needed
     the spec inlined", "judge_count=1 drift was about +/-0.3"), not a
     findings forecast. It will be read by the next tranche's parent, so
     it must not carry an expectation about what that tranche will
     surface (Step 5.4 discipline).

Write to ${TRANCHE_DIR}/retrospective.md. Touch
${TRANCHE_DIR}/tranche.retrospective. Return a 200-word summary.
```

### Step 5.3 — Combined cross-tranche trend regeneration

Output: `~/hypergumbo_lab_notebook/dogfooding_trend_combined.md` (overwritten, not appended; this file is auto-generated and must not be hand-edited).

The parent agent runs `python3 ~/hypergumbo_lab_notebook/build_combined_trend.py` (no arguments). The script **auto-discovers** every tranche's `trend_cluster_aware.md` plus its `tranche_state.json` (if present) by globbing the lab-notebook directory — and it must match **both** directory spellings: `dogfood_tranch_*/` (the frozen pre-rename tranches 01–04) and `dogfood_tranche_*/` (tranches created after the PR #4073 `tranch`→`tranche` rename). The source list is NOT hardcoded; a new tranche is picked up purely by its directory existing. The script then produces a single combined report containing:

- A per-tranche metadata table (judge count, card count, methodology note, Σ-severity sub-total, parse path).
- A per-pass Σ-severity table covering ALL completed passes (one row per pass, s1 through the last completed pass). The value is the single-method attribution (earliest cluster member) from each per-tranche trend file's `## Per-pass SEVERITY SUM` table — the parser reads the 4th column of that table, so every per-tranche `trend_cluster_aware.md` must carry it (Phase 5.1 emits it by default).
- A 5-pass bucket Σ-severity rollup, all buckets across all tranches.
- An ASCII sparkline of the per-pass series.
- A per-chunk resource-consumption table (sub-agent tokens, tool uses, wall-clock seconds) for every Phase 1 chunk in tranches that have `tranche_state.json` data (pre-playbook tranches are absent from this section; the script notes that explicitly).
- An "Other-phase sub-agents" sub-section with the same fields for Phase 2 mid-tranche checkpoint and Phase 2.5 post-discovery audit.

The script lives in the lab notebook (not the repo) at `~/hypergumbo_lab_notebook/build_combined_trend.py`. To add a future tranche's data, just generate its `trend_cluster_aware.md` per Phase 5.1 (including the `## Per-pass SEVERITY SUM` table the parser reads) and ensure its `tranche_state.json` records per-chunk stats; the script picks up the new tranche on next run without code changes, regardless of which directory spelling the tranche uses. If you ever find yourself editing a hardcoded tranche list in the script, that is the F3 regression — restore the dual-spelling glob instead.

Why this step exists separately from the per-tranche trend report: Phase 5.1 produces a tranche-scoped view useful for the per-tranche retrospective and PR; Phase 5.3 produces the cross-tranche view useful for comparing trend signals (convergence question, within-tranche peak-then-decline pattern, per-chunk resource scaling). Without Phase 5.3, the cross-tranche view has to be hand-assembled by reading every per-tranche trend file each time, which is what produced multiple counting errors in tranche 03's session (off-by-one in tail flags, prose-vs-table count drift).

### Step 5.4 — Instrument-change seams (analysis-surface only)

When the procedure itself changes between tranches — a prompt redesign, a judge-count change, a card-pipeline change, a de-prime like this one — the *measurement instrument* changed, and any cross-seam trend comparison confounds the instrument change with genuine discovery dynamics. Handle a seam by **declaring it descriptively on the analysis surfaces and nowhere else**:

- **Declare it** on the post-hoc, analyst-facing surfaces only: the combined-trend report (`build_combined_trend.py` emits a "Methodological seams" section) and `dogfood_tranches_index.md`. State it as a fact about how the data was collected — "tranches X and Y ran under different instruments, so a cross-seam difference confounds the instrument change with discovery dynamics." State **no** expectation about the *direction* of the effect.
- **Quarantine it** from every run-time agent context. Do NOT put a seam caveat — or any expectation about its direction — into a discovery/audit worker, into the orchestrating parent *during a run*, or into the registry / `agent_notes.json`. A seam caveat is campaign-measurement framing: for a human analyst reading the trend it is healthy skepticism, but for an agent at a discovery moment it is an expectation-prime — and on the parent it is the worst case, because the parent's expectations propagate into every region assignment and every registry row the workers then read. (Knowing *which* procedure it is running is unavoidable and fine; carrying an expectation about *what the trend should show* is the prime to prevent.)

This is the de-prime discipline applied to the measurement of the de-prime itself: a seam note tells the analyst the comparison is confounded; it must never tell a working agent what to expect to find. The registry/measurement-framing boundary (§The real-issue registry, Phase 7) is what keeps the caveat out of blinded workers automatically; this step is the rule for the surfaces Phase 7 does not police.

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

BOUNDARY (see §The real-issue registry): agent_notes carries the
per-issue LEDGER ONLY — the issues table, cohort row listings, and
ledger metadata (active counts, scope, cross-refs). It must NOT carry
campaign-measurement framing: trend means, bucket-Σ rollups,
cross-tranche comparison tables, per-tranche severity rollups, or a
"READ THIS FIRST" block of headline trend numbers. That framing is an
expectation-prime for the next tranche's discovery workers, which seed
their working registry from this file. Any item below that would
produce such framing writes it to `dogfood_tranches_index.md` + the
trend files (Phase 6.3 / Step 5.3) instead — NOT here. Phase 7 item 8
is only the backstop; the job is to not produce the framing in the
first place.

  1. Top-of-file header update: keep at most ONE current-state header —
     a date plus a one-line pointer ("tranche ${TRANCHE_ID} cohort
     filed; cross-tranche trend lives in dogfood_tranches_index.md +
     dogfooding_trend_combined.md"). Do NOT put trend numbers here: no
     mean severity, no bucket-Σ row, no Significant+Severe percentage.
     Those are framing and live only in the index/trend files.
  2. Active set count update (e.g., "Current active set: ~X items
     after tranche ${TRANCHE_ID} materialization").
  3. Issues table preamble update: scope=<total rows> after this
     tranche's additions, cumulative summary by tranche.
  4. New cohort sub-section under the corpus table: "Tranche
     ${TRANCHE_ID} cohort filed YYYY-MM-DD — N discrete rows" listing
     the materialized rows by family/cluster with priority + parent.
   5. Inline annotations on existing parent rows that received tranche
     extensions: cross-reference the new child rows.
  6. Severity / status distribution: update the CURRENT-STATE table
     (all open issues by priority/status — this describes the ledger's
     present composition, which is ledger metadata and belongs here). Do
     NOT add a per-tranche-cohort column; a cohort-by-tranche rollup is
     framing → put it in `dogfood_tranches_index.md`.
  7. Bucket-Σ-severity rollup: append the tranche's row to
     `dogfood_tranches_index.md` (Phase 6.3), NOT to agent_notes. The
     combined per-pass + bucket view is regenerated by
     `build_combined_trend.py` (Step 5.3). Neither belongs in the registry.
  8. Cross-tranche comparison: record it in `dogfood_tranches_index.md`
     (its canonical home), NOT in agent_notes.
  9. Phase 2.5 disposition entry — record the post-discovery-audit
     disposition verbatim as a one-line entry under the tranche's cohort
     sub-section:
       - "Tranche ${TRANCHE_ID} Phase 6.4 disposition: skip — confirmed
         YYYY-MM-DD (Phase 2.5 found 0 would-have-been-pruned candidates,
         0 pattern-amplification warnings, 0 unverified negative findings)."
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

- `${TRANCHE_DIR}/carry_forward.md` with: open methodology questions, severity-rubric calibration adjustments, recommended Phase 0 changes (tunables, primer fixes), deferred Step 6.4 cleanup notes if any. Carry **no** findings-forecast and **no** probe-steer ("class X is saturated, look at Y") — that would prime the next tranche's parent and is redundant with the registry, which already conveys what has been found (Step 5.2 item 3 / Step 5.4 discipline).
- The next tranche's Phase 0 sub-agent should be pointed at this `carry_forward.md` via the `carry_forward_in` field in its tranche state file.
- Finalize `${TRANCHE_DIR}/tranche_state.json`: set `phase` to `complete`, populate the per-step completion timestamps, touch `${TRANCHE_DIR}/tranche.complete` sentinel.

## Phase 7 — End-of-tranche agent_notes coherence sweep

Output: `agent_notes.json` restructured for coherence; `${TRANCHE_DIR}/coherence_sweep_report.md`; `tranche.coherence_swept` sentinel.

Runs ONCE per tranche, after Phase 6 has finalized. NOT after every Phase 6 edit — per-edit coherence checks risk myopia (the sub-agent tweaks based on per-step context rather than seeing the full picture). This phase steps back and audits the whole `agent_notes.json` once the tranche's contributions have settled.

The motivating gap, identified during tranche 03 review: `agent_notes.json` grows across sessions as each new tranche / fold-audit / materialization event appends its own "READ THIS FIRST" header + cohort listing + cross-tranche table. Phase 6.2 instructs the sub-agent to APPEND content; nothing was deduplicating. After three tranches the notes had three competing READ-THIS-FIRST headers, asymmetric cohort coverage (tranche 01 not enumerated; 02 and 03 yes), and redundant cross-tranche comparison tables in slightly different formats. Future agents reading the notes were inheriting drift.

**This sweep is also the ongoing enforcer of the registry/measurement-framing boundary** (see §The real-issue registry). The registry that every next-tranche discovery worker reads is seeded from `agent_notes.json`; if campaign-measurement framing (trend means, bucket-Σ tables, "READ THIS FIRST tranche NN" headers, cohort-by-tranche rollups, "expect re-confirmation" language) is allowed to live in the registry, it will be injected straight into those workers and re-introduce the exact priming this redesign removed. Phase 7 therefore relocates such framing OUT of the registry and into the canonical parent-facing trend/index files (`dogfooding_trend_combined.md`, `dogfood_tranches_index.md`) where it belongs — and leaves the per-issue ledger (the issues table, priorities included) complete. The relocation is one-time-from-now-on: do it each sweep so the next tranche always seeds from a clean registry.

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

  8. **Measurement-framing in the registry (de-prime enforcement).**
     The issues table must contain only issue rows. Any campaign-
     measurement framing physically present in agent_notes — trend
     means / slopes, bucket-Σ-severity rollups, "READ THIS FIRST
     tranche NN" headers, cohort-by-tranche comparison tables, "expect
     re-confirmation" or "heavy re-confirmation" language — is framing,
     not a real issue. Relocate it to the canonical trend/index files
     (`dogfooding_trend_combined.md`, `dogfood_tranches_index.md`),
     leaving at most a one-line pointer in agent_notes. The per-issue
     ledger (issues table + priorities) stays in full. The goal: the
     next tranche's Step 0.6 can seed a clean registry with no relocation
     work left to do. Flag anything you cannot confidently relocate
     rather than deleting it.

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

Single source of truth for tranche state, owned by the parent. The parent updates it after every sub-agent return. Discovery and audit workers do **NOT** read it (it carries the global numbering and campaign position that would break campaign-position blinding); they read only their staging directory. Consolidation-stage sub-agents may read it.

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
  "discovery_mode": "region-parallel | sequential",
  "first_pass_number": "int",
  "last_pass_number": "int",
  "current_pass": "int",
  "phase": "discovery | midtranche_audit | post_discovery_audit | consolidation | judging | retrospective | carry_forward | complete",
  "passes_complete": [
    {"pass_number": "int", "headline": "string", "invalidation_risks": ["string"]}
  ],
  "chunk_to_passes": {"<chunk index>": ["int"]},
  "local_label_to_fnumber": {"<chunk>:<local_pass>:<local_label>": "string (F-number)"},
  "chunk_resource_stats": [
    {"chunk": "int", "duration_ms": "int", "tokens": "int", "tool_uses": "int"}
  ],
  "checkpoint_complete": "bool",
  "post_discovery_audit_complete": "bool",
  "consolidated_complete": "bool",
  "judged_complete": "bool",
  "retrospective_complete": "bool",
  "meta_criterion_file": "string",
  "substrate_guide_file": "string",
  "judge_primer_file": "string",
  "registry_file": "string (absolute path to the tranche's working registry)",
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
| A negative finding's fresh-substrate verification command errors | Substrate regeneration command exits non-zero | Mark the finding KIND=INDETERMINATE in `tranche_state.json`; Phase 3 handles INDETERMINATE as a manual-review queue |
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

## Appendix B — de-priming rationale

A line-by-line review of an earlier run's as-run sub-agent prompts found that much of the per-prompt scaffolding was *adding* bias rather than catching it. The root cause was a single omission — **discovery workers were never given the project's real-issue registry** — for which the orchestrator compensated with scaffolding (a thin distilled digest, parent-pre-assigned narrow probes, workers reading each other's raw output) and position/expectation primes. Give the workers the registry and the scaffolding becomes unnecessary; almost every specific defect below then dissolves. This appendix records, per cut, what was removed and where the design now handles the underlying need.

| Removed cue (and why it biased) | Where the redesign handles it |
|---|---|
| **Playbook name in the worker prompt** — invites a narrow worker to read a 1000-line orchestrator-POV doc and absorb parent-role framing. | §Design principle: a worker prompt is task + inputs + output format only. The worker never names or reads this playbook. |
| **Mini-trial / "the parent will assess your output + timing" framing** — evaluation-awareness makes an agent perform to the grader; also redundant since the tool result already carries duration + token counts. | Step 1.1 prompt carries no evaluation language; Step 1.2 #3 captures resource stats from the tool result, never a worker self-report. |
| **Campaign position** — "tranche 04", "passes 61-80" signal campaign maturity/lateness and bias effort downward; smeared across log paths, output filenames, finding labels, sentinels, return echoes, and cross-refs so a prose-only scrub misses it. | §Design principle #2 + Step 1.1/1.2: workers use local opaque labels on every surface (working dir, `probe_<local>.log`, `observations_<local>.md`, findings `A1`/`A2`, local sentinels); the parent stamps the global identity (`pass_61.md`, `F61.A1`) only on aggregation and keeps the ordinal↔token map parent-side. |
| **"Expect heavy re-confirmation / tag EXTENSION"** — pushes the worst-positioned actor to pre-judge novelty toward "fold"; redundant with ≥3 downstream dedup gates and circular (workers told to fold → cohort comes out fold-heavy → trend reads the fold as a discovery signal). | §The real-issue registry: workers get the registry (data, not an expectation) and navigate toward unmapped ground; the dedup stages, not the discoverer, decide novelty. |
| **Audit "folds are expected" preamble + pre-named verdicts** — hands the auditor (the one actor meant to *catch* over-folding) the answers before it evaluates. | §5.4 / Phase 2 + 2.5: neutral, symmetric job statement; no pre-named findings or verdicts; auditor gets the passes' local relative structure, never the global position. |
| **"Read the prior chunk's pass files so you don't duplicate"** — dumps a prior worker's raw findings (with self-severities + dispositions) into a later worker, anchoring severity/novelty and propagating the prime. | Step 0.6 + Step 1.2 #5: the working registry is the shared known-state, updated by the parent as distilled neutral table rows between chunks — no cross-worker raw-output read. |
| **Discovery-time provisional KIND tag + fold/standalone hint** — a biased provisional tag anchors the very ratchet audits that exist to make that call. | Step 1.1 drops it; KIND is assigned fresh by Phase 3a, which is the stage equipped to do it. |
| **Carbon-dating Methods A/B/C** — three methods that differ only for multi-pass clusters; every cluster to date has been same-pass, so A=B=C every time and the breakdown never disambiguated anything. | Phase 5.1: a single attribution (earliest cluster member) plus a same-pass sanity assertion that fails loudly the first time a multi-pass cluster appears. |

What the review **affirmed as correct and left unchanged**: the 20-pass tranche as the comparable unit; the structurally-blinded card writer (Phase 3c) and blind judges (Phase 4); two-axis consolidation (methodology then causal) with the rejected-clusters log as the authoritative novelty gate; parent-owned phase loop + state file; and cross-tranche Σ-severity bucket comparison. The redesign subtracts scaffolding; it does not weaken the parts that were working.

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
