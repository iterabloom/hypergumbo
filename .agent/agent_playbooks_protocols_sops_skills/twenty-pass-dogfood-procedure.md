<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Twenty-pass dogfood procedure

A vendor-neutral procedure for running a dogfooding tranche on hypergumbo (or any tool that ships a CLI, an analysis substrate, and a tracker). A "tranche" is a contiguous block of ~20 dogfooding passes against the same substrate. The procedure is run by an orchestrating agent that spawns sub-agents; the orchestrator does not babysit a session.

## The one rule that governs everything: the agent must not perceive convergence

The agent's job is **exactly two things**:

1. **Dogfood** — find real issues in the tool's output/behavior that have **not already been found**.
2. **Orchestrate** — run the machinery (spawn the passes, aggregate them, maintain the provenance map, run the blinded consolidation/judging that produce severity *data*, preserve found issues to the tracker, keep the ledger clean).

Everything else is the **human's** job — deciding what the trend is, whether discovery is converging, when the campaign is "done," what to do next. The agent does not decide those, does not forecast them, and **does not even perceive them**. It is not enough for discovery to converge; it must converge *without the agent realizing it converged* — because an agent that can see "the campaign is mature / yield is dropping / we're at pass 90" lowers its effort and pre-judges novelty, and a low yield produced that way is a self-fulfilling artifact, not a signal.

The agent runs **every** phase in this playbook, including the post-tranche ones (trend-data assembly, materialization, coherence sweep). What it does **not** do is pronounce the trend's meaning, declare convergence, decide the campaign is "done," or decide what to do next — it produces the data (issues in the ledger and the tracker, the pass→row provenance map, blinded severities, per-pass severity tables) and the human draws those conclusions.

The agent also keeps its own opinions out of the work. Do not editorialize about yield, do not predict what a pass or a tranche "will" find, do not narrate "this looks like saturation." Run the machinery; report facts.

### Two structural guarantees that make the blindness real

Asking an agent to "ignore campaign position" does not work (the introspection-reliability literature is clear that declarative blinding fails and can backfire). Blindness has to be **structural** — the position information must be *absent from what the agent reads*.

1. **The issue ledger is campaign-position-free.** The ledger (`agent_notes`, see below) is a flat list of distinct known issues with **zero cadence signal**: no tranche names, no pass numbers or ranges, no dates-as-timeline, no cohort groupings, no counts of how much has been found. A discovery worker loads the **whole** ledger and learns *what issues exist* — and nothing about *how far along the campaign is*. There is simply nothing campaign-shaped in it to read.

2. **Pass→row provenance lives in a separate orchestrator-only map.** Plotting summed-severity-over-time (the human's trend analysis) needs to know which pass contributed which issue. That mapping is real and necessary — but it is **cadence**, so it must never be in the ledger. It lives in a separate map file (see §The pass→row map) that the orchestrator maintains and **no discovery/audit/judge worker ever reads**.

The split is the whole design: the ledger answers "what is known" (worker-facing, cadence-free); the map answers "which pass found what, and how severe" (human-facing, cadence-carrying).

## The issue ledger (`agent_notes`)

`agent_notes.json` (managed by `scripts/agent-notes`; its `notes` markdown field) is the canonical **issue ledger** — one entry per distinct issue discovered in the tool, organized as a flat list (or grouped by neutral subsystem/theme — never by tranche/pass/date). Each entry carries: a tracker ID (once materialized), status/priority/parent if known, and a neutral one-line description of the issue's mechanism. It carries **no** campaign-measurement or cadence content — that lives only in the analyst-facing files (`dogfood_tranches_index.md`, `dogfooding_trend_combined.md`).

- **Loaded whole, via the script.** Every discovery and audit worker begins by running `scripts/agent-notes --show` and reading the entire ledger. This is non-negotiable: a worker cannot decide whether a finding is new, or whether it enriches an existing issue, without the complete picture. (It can do so safely precisely because the ledger is cadence-free.)
- **Workers integrate, they do not dump.** When a worker records a finding, it integrates it into the ledger using judgment: **add a new row** if no existing entry captures the mechanism, or **enrich an existing row** if the finding is a facet/refinement/extension/correction of one already present. This keeps the ledger coherent for the next worker and is the sole collision-avoidance mechanism (workers never read each other's raw output). Entries stay factual and neutral — no severity scores, no KIND tags, no novelty self-judgment beyond the add-vs-enrich decision, and no cadence labels.
- **Kept clean.** Phase 6 (materialization) and Phase 7 (coherence sweep) keep the ledger campaign-position-free going forward; if cadence framing ever accretes, it is relocated to the index/trend files, not left where a worker would load it.

This is what the registry was always meant to be ("one row per distinct issue"); earlier runs let per-tranche cohort sections and per-pass listings accrete on top, which leaked cadence into every worker that loaded it. Do not let that happen again.

## The pass→row map

A separate file, `${TRANCHE_DIR}/pass_row_map.json`, owned by the orchestrator. For each **global pass number** it records the ledger row(s) that pass created or enriched, the local finding label(s) and the parent-stamped F-number(s), and — once Phase 4 has run — the blinded severity for each. Shape:

```json
{
  "81": [
    {"f_number": "F81.A1", "row": "<ledger row title or tracker id>", "action": "added",   "severity": null},
    {"f_number": "F81.A2", "row": "INV-virik",                         "action": "enriched", "severity": null}
  ],
  "82": [ ... ]
}
```

Severities are filled in after Phase 4. This file is the **sole** source for the human's summed-severity-over-time plot. It contains pass numbers (cadence) by construction, so it is **never** staged into, shown to, or otherwise read by any worker — only the orchestrator writes it and only the human (or a human-triggered plotting step) reads it.

## Sub-agent orchestration

The orchestrator owns the phase loop, the tranche state file, the pass→row map, and all global numbering. It does **not** run the passes itself — it spawns sub-agents and aggregates their return summaries. **The sub-agent boundary is the compaction event:** each sub-agent's bulky working context (substrate dumps, probe logs, command output — easily 50–100K tokens per pass) lives only inside that sub-agent and dies on return; the orchestrator sees only ~200-word summaries. That is the *only* reason work is chunked: a chunk holds however many passes fit comfortably in one sub-agent context (default 2). Chunking carries no parallelism and no independence semantics.

**Discovery is sequential.** One chunk at a time. Each chunk loads the ledger as updated by all prior chunks, so each pass steers away from everything already found. Concurrent discovery would defeat this — parallel workers cannot see each other's integrations — so it is not done.

A worker prompt contains exactly three things: its **task**, its **inputs**, and its **output format**. It is never told its place in the campaign, never told a tranche or global pass number, never told to expect re-confirmation, never told to pre-judge novelty. Global numbering is stamped by the orchestrator on aggregation.

## Tunable parameters

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `--chunk-size` | 2 | 1–5 | Passes per discovery sub-agent. The sub-agent boundary is the compaction event; this controls how much per-pass context one sub-agent holds. |
| `--judge-count` | 1 | 1–5 | Blind-judge ensemble size (Phase 4). 1 is cheapest and yields a usable per-card severity. ≥2 averages inter-rater variance; the upgrade is reversible (re-run more judges on the same cards later). |
| `--mid-tranche-pass` | (10th pass of the tranche) | — | Where the Phase 2 interim audit is inserted. |
| `--tranche-size` | 20 | 10–30 | Total passes. Below ~10 the signal is noisy; above ~30 it loses coherence. |

There is **no discovery-mode parameter.** Discovery is sequential, always.

## Phase 0 — Pre-tranche setup

Output: `${TRANCHE_DIR}/tranche_state.json` populated; substrate frozen; rubric/primer locked; ledger confirmed campaign-position-free; empty `pass_row_map.json`.

1. **Pick the tranche directory.** `TRANCHE_DIR=~/<repo>_lab_notebook/dogfood_tranche_<NN>` (NN = ordinal). Holds all tranche artifacts; never overwritten across tranches.
2. **Freeze the substrate.** Generate it once (`hypergumbo run . --out "${TRANCHE_DIR}/substrate.json"`) and record its sha256, or — if the analyzed code is unchanged from a prior tranche — reuse that frozen snapshot (symlink) and record the provenance (sha, repo HEAD).
3. **Lock the rubric and the judge primer.** Write `substrate_guide.md` (severity rubric + 6-field card format + KIND taxonomy + substrate file references + how to query the substrate + a neutral probe-class catalog). Assemble `judge_primer.md` (domain primer + full project spec, inlined + the 1–5 severity rubric) for Phase 4. The guide carries **no** fresh/saturated probe steers and **no** expectation framing — navigation comes from the ledger, not from the guide.
4. **Commit the meta-criterion verbatim** to `meta_criterion.txt`: *"If two findings are actually two symptoms of one underlying problem, then that underlying problem should be what gets judged, not the two symptoms individually."* Governs Phase 3 consolidation.
5. **Confirm the ledger is campaign-position-free.** Before any worker loads it, verify `agent_notes` carries no cadence framing (no tranche names, pass numbers/ranges, dates-as-timeline, cohort groupings, trend/severity rollups). If any is present, it is debt from a prior tranche — relocate it to the index/trend files now (see Phase 7). The ledger is loaded live via `scripts/agent-notes --show`; there is no separate registry copy.
6. **Initialize state + map.** Write `tranche_state.json` (ids, config, `first_pass_number`, `last_pass_number`, `current_pass`, `phase: discovery`, empty `passes_complete`, `chunk_to_passes`, `chunk_resource_stats`). Create an empty `pass_row_map.json`.

The state file carries global numbering and is read only by the orchestrator and by consolidation-stage sub-agents — never by discovery/audit/judge workers.

## Phase 1 — Discovery (sequential chunks)

Output: `${TRANCHE_DIR}/pass_NN.md` + `pass_NN.complete` per pass; ledger rows added/enriched; `pass_row_map.json` updated; `passes_complete` advanced.

For each chunk (sequentially), the orchestrator stages a working dir `${TRANCHE_DIR}/.staging/discovery_<chunk>/` with `substrate_guide.md`, then spawns ONE discovery sub-agent. The local↔global mapping (chunk → global passes; local label → F-number) is kept orchestrator-side and never given to the worker.

Prompt template (task + inputs + output format only):

```
You are a discovery sub-agent investigating <tool>. Your working
directory is ${working_dir} — write your output files there. It
contains substrate_guide.md (rubric + probe catalog + how to query
the substrate).

FIRST, load the complete known-issues ledger into your context:
    <repo>/scripts/agent-notes --show
Read all of it. It is your map of what is already known.

Then run ${chunk_size} passes (numbered locally 1..N). Each pass:
  TASK: Find real issues in <tool>'s output/behavior that are NOT
  already in the ledger. Probe the frozen substrate at
  ${substrate_file} (query with jq / streaming python; capture to
  probe_<pass>_*.log; do not Read the whole file). You may run the
  tool's subcommands against the repo for context. Record correct
  behavior you verify too (a confirmed-correct surface is a real
  finding). Pick whatever probe looks most informative given what the
  ledger already covers; usually the most information is on surfaces it
  does NOT yet cover, but re-testing a known issue on fresh substrate is
  legitimate (catches regressions). You are not assigned specific probes.
    - Record findings labeled A1, A2, … with a 1-2 sentence neutral
      headline (do not score severity), the evidence (file:line+values
      or command+output), and POSITIVE (looks correct) / NEGATIVE.
    - For any substantive NEGATIVE: regenerate the slice fresh and
      confirm it reproduces; note the outcome inline. Use temp copies
      for anything that writes; flag injection findings INVALIDATION-RISK.
    - Write the pass's findings to observations_<pass>.md; touch
      observations_<pass>.complete.
  THEN INTEGRATE the pass's findings into the ledger (after each pass,
  so the next pass sees them). For each finding decide, by judgment:
    - new issue → ADD a row (scripts/agent-notes --append "<row>"),
    - facet/refinement/extension/correction of an existing issue →
      ENRICH that row in place (exact-match python replace on the notes
      field; never fuzzy-replace; if you can't match exactly, add a row).
    Use a short NEUTRAL local handle ("p<localpass>-<A-label>: <title>");
    do NOT infer or stamp a tranche/campaign number. No severity, no KIND
    tags, no novelty judgment beyond add-vs-enrich, no cadence labels.
    Do not reorder/reformat existing ledger content.

Return <=200 words: headline findings (local labels, 1 line each) and
whether each ADDED or ENRICHED (name the enriched entry); negative
findings + their fresh-substrate outcome; surfaces you did NOT reach.
Do NOT return full writeups; do NOT report timing or token counts.
```

**Orchestrator aggregation, after each chunk:**

1. Verify the worker's ledger edits left a valid, coherent ledger (valid JSON; existing content preserved; only rows added/enriched). Keep a per-chunk backup of `agent_notes.json` taken *before* the chunk, so a corrupted integration is recoverable.
2. Stamp global numbering: move each `observations_k.md` → `pass_<global>.md`, rewriting local labels (`A1`…) to F-numbers (`F<global>.A1`…); touch `pass_<global>.complete`; record the chunk→passes and local-label→F-number maps in `tranche_state.json`.
3. **Update `pass_row_map.json`:** for each finding, record `{f_number, row, action: added|enriched, severity: null}` under its global pass. This is the cadence record for the human's later plotting.
4. Capture resource stats (duration, tokens, tool uses) from the tool result — never from a worker self-report.
5. Advance `current_pass`; append each global pass + a one-line headline to `passes_complete`.
6. When `--mid-tranche-pass` is reached, run Phase 2, then continue; otherwise spawn the next chunk.

The orchestrator does this mechanically. It does not tally yield-so-far, compare chunks, or remark on whether discovery is slowing — that is cadence perception, which is forbidden.

## Phase 2 / Phase 2.5 — Audits (quality checks, not consolidation)

Phase 2 runs after the mid-tranche pass on the first-half passes; Phase 2.5 runs after the final pass on the second-half passes. Each stages the relevant passes (renamed `local_1.md…` with global numbering stripped) plus `scripts/agent-notes --show` access, and spawns one audit sub-agent. Both are **checks**, not consolidation and not novelty decisions:

- Flag over-splitting (one issue stated twice) and over-folding (separate issues recorded as one), named neutrally — no final dispositions.
- Flag negative findings recorded without a fresh-substrate verification outcome; apply the fresh-substrate protocol to any found.
- Flag pattern-amplification (a claim re-asserted across ≥3 passes without fresh verification, or a number re-cited without fresh derivation).
- Phase 2.5 additionally flags would-have-been-pruned content (thin evidence; cross-pass repetition better recorded as an enrichment than a standalone row) — the input to the Phase 6 cleanup decision.

Auditors are campaign-position-blind (they see relative within-tranche structure, never global numbering) and get no pre-named verdicts. Outputs: `midtranche_audit.md`, `post_discovery_audit.md`. These are inputs to Phase 3; they are not staged into discovery workers.

## Phase 3 — Consolidation + blinded cards (data production)

Two dedup sub-agents in sequence (methodology-axis then causal-axis), then a blinded card writer. This produces the deconfounded units that Phase 4 judges. It is data production; it draws no trend conclusions.

- **3a methodology-axis:** assign each finding (every F-number) a KIND — INDEPENDENT / AUDIT-AXIS (with sweep id) / EXTENSION / CORRECTION / POINTER-ONLY / INVALIDATION — with HIGH/MED/LOW confidence. Record the structural fact each label implies. Do not roll labels up into counts. → `classification.md`.
- **3b causal-axis:** cluster findings that share one underlying root cause (per `meta_criterion.txt` — "would fixing one fix the other?"). HIGH/MED/LOW per cluster; default to NON-clustering at LOW. Record members, root cause, confidence, the earliest contributing pass, and the unifying fix. Maintain a `rejected_clusters.md` log of consolidations considered and rejected (this is what makes Phase 3 auditable). → `clusters.md`, `rejected_clusters.md`.
- **3c card writer (structurally blinded):** stage ONLY the consolidated findings + substrate guide in `${TRANCHE_DIR}/.staging/card_writer/` and point the sub-agent there — it must not see the trend goal, prior baselines, or the index. One card per cluster / per INDEPENDENT singleton (folded EXTENSION/CORRECTION do not get cards), in the 6-field format (Subject / Mechanic / Expectation / Scope / Exposure / Consequence). Card IDs are opaque `anon-card-NNN` carrying no tranche/pass/F-number. Run a leak-check grep (no tracker IDs, pass numbers, F-numbers, or the word "audit"). → `cards_blinded.md`, `mapping_anon_to_source.tsv`, `leak_check.md`.

## Phase 4 — Blind judging (severity data)

For each card × each judge (default 1), stage `judge_primer.md` + the single card (opaque ID) in a per-judge staging dir and spawn a blinded judge that scores 1–5 on the rubric and emits a JSON verdict on stdout. At `--judge-count ≥ 2` spawn strictly one sub-agent per (card, judge) for inter-rater independence; at 1, batching cards into one judge sub-agent is acceptable iff each is scored on an absolute rubric basis. The orchestrator joins verdicts with `mapping_anon_to_source.tsv`, **fills the `severity` field into `pass_row_map.json`** (via the card→F-number→pass chain), and writes the per-judge verdict files. → `verdicts_judge_*.jsonl`. This is the severity data the human's trend plot consumes; the agent computes no trend from it.

## Phase 5 — Trend data + retrospective (data, not verdicts)

Assemble the per-pass tables the human needs to judge the trend — straight from `pass_row_map.json`: per-pass finding count, per-pass mean blinded severity, per-pass summed severity (Σ-sev), plus the cluster inventory. Write them to `${TRANCHE_DIR}/trend.md`. These are **data**: present the tables and attach **no** convergence verdict, **no** "slope means X" narrative, **no** "done" call — the human reads the tables and decides whether and how it converged.

Also write a process-facts retrospective (`${TRANCHE_DIR}/retrospective.md`): did the audits' over-split/over-fold flags survive Phase 3; did the rejected-clusters log catch a consolidation that would otherwise have collapsed; open *methodology* questions (e.g. judge-count calibration). It carries **no findings-forecast** and **no probe-steer for the next tranche** — the ledger already conveys what is known, and the next tranche's workers navigate from it. Keep cadence framing (trend means, bucket rollups, cross-tranche comparisons) in the analyst-facing index/trend files, never in the ledger.

## Phase 6 — Materialize issues to the tracker (mechanical preservation)

So found work is not lost, materialize the cohort to the tracker. This is mechanical, not interpretive.

- For each issue that should be its own row (each cluster + each INDEPENDENT singleton), search the tracker for duplicates across all kinds/statuses; classify TRUE_DUPLICATE / RELATED_BUT_DISTINCT / NOT_A_DUPLICATE (LOW-confidence → RELATED_BUT_DISTINCT, never silently lose information). File new rows (`scripts/tracker add --kind work_item …`, tag `dogfood`) or fold onto existing rows (`tracker discuss …`). Priority may be derived from the blinded severity (5→P0 … 1→P4) as a mechanical mapping.
- **Status-adjustment for resolved-state fold targets:** if a new finding reproduces the mechanism of a `done`/`satisfied` row → reopen (`todo_hard`); of a `pending_validation` row → `violated` (validation failed); of a `wont_do` row → surface to the operator, do not auto-reopen. Default to action over escalation for `done`/`satisfied`/`pending_validation`.
- Record each card → tracker row in `tracker_materialization.tsv`.
- Keep the ledger campaign-position-free throughout (Phase 7 enforces). Do **not** write a carry-forward findings-forecast or probe-steer; the ledger already conveys what is known, and forecasting is the human's call.

Honor the tracker's auto-sync (do not push/commit `.ops` manually) and the "no tracker mutations while an auto-pr is in flight" rule.

## Phase 7 — Coherence sweep (keep the ledger campaign-position-free)

After Phase 6, sweep `agent_notes` once: dedup any accreted headers, and **relocate any campaign-measurement/cadence framing** (tranche names, pass numbers/ranges, dates-as-timeline, cohort groupings, trend means/rollups, convergence notes) out of the ledger and into the analyst-facing files (`dogfood_tranches_index.md`, `dogfooding_trend_combined.md`). The ledger that the next tranche's workers will load must contain only the per-issue list. This sweep is the ongoing enforcer of the structural-blindness guarantee; without it, cadence creeps back in and re-leaks into every worker. Apply edits with exact-match replace (no fuzzy-replace); back up first.

## Sentinels & state

Zero-byte sentinels signal step completion: `pass_NN.complete`, `tranche.checkpoint` (Phase 2), `tranche.post_discovery_audit` (Phase 2.5), `tranche.consolidated` (Phase 3), `tranche.judged` (Phase 4), `tranche.trend` (Phase 5), `tranche.materialized` (Phase 6), `tranche.coherence_swept` (Phase 7). They are advisory; the orchestrator can detect the underlying artifacts directly. `tranche_state.json` is the single source of truth for orchestrator state and is never read by discovery/audit/judge workers.

## Recovery & failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Discovery sub-agent crashes | orchestrator times out / no return | re-spawn with the same prompt; the pass record + ledger backup let it resume |
| Worker corrupts the ledger on integration | post-chunk JSON-validity / content-preservation check fails | restore the pre-chunk ledger backup; re-run the chunk |
| Pass writeup partial | file exists, no sentinel | re-spawn; first action is to continue the incomplete writeup |
| A negative finding's fresh-substrate check errors | regeneration command exits non-zero | mark the finding INDETERMINATE in state; Phase 3 handles it as manual review |
| Judge verdicts have no clear majority (≥2 judges) | spread ≥2 tiers in ≥25% of cards | spawn more judges; if still spread, surface to the operator |

## Vendor neutrality

The procedure assumes the vendor can: spawn a sub-agent and capture its return value; read/write local files; run shell commands. Structural blinding uses a staging directory (stage only allowed inputs; point the sub-agent at that dir). For vendors that can constrain sub-agent filesystem access, use that in addition. For vendors without sub-agent spawning, a tmux-supervisor fallback (context-flush keystroke every `--chunk-size` passes) is possible but unverified; prefer the sub-agent path.
