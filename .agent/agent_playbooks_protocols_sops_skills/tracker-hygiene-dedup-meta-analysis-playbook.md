<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Tracker Hygiene, Dedup & Meta-Analysis Sweep

An **on-demand** (human-triggered) sweep over the *committed* tracker corpus that (1) clusters open
items by root-cause family, (2) flags TRUE_DUPLICATE / RELATED_BUT_DISTINCT pairs across the corpus
using the Phase-3 "would fixing one fix the other?" test, (3) re-verifies that `violated` /
`pending_validation` statuses still hold against current code, and (4) emits a meta-analysis table of
the dominant invariant families. Subagents deepen the per-item investigations; the orchestrator stays
in the loop and can steer them as new information surfaces.

This is the **corpus-wide** counterpart to the dedup machinery that already exists in two narrower
forms: the **create-time** duplicate check in the `trackerize-playbook.md` (§8, applied when *adding*
one item), and the **campaign-scoped** consolidation in `twenty-pass-dogfood-procedure.md`
(Phases 3/6, applied to *fresh dogfood findings*). This playbook reuses that procedure's dedup
vocabulary verbatim (see §"Dedup vocabulary") but points it at the existing ~hundreds of tracker
items rather than at new findings — so it answers "is the tracker itself coherent, deduplicated, and
still-true?" not "where do these new findings go?"

## When to run

On explicit request only. Good triggers: the open-item count has grown large and feels redundant;
after a big multi-PR push that may have silently resolved or duplicated items; before a planning /
prioritization session; after a dogfood tranche dumps many new items; when status drift is suspected
(items marked `violated` that may already be fixed, or `satisfied` that may have regressed). It is
**not** cadence-driven — there is no stop-hook nudge and it is never auto-invoked.

## Guardrails (read before touching the tracker)

These are non-negotiable and several are inherited from AGENTS.md:

- **Never read `.ops` files.** Read item state only through `scripts/tracker show <ID>` /
  `show <ID> --json` and `scripts/tracker list --json`. The CLI compiles ops into current state.
- **Never manually commit or push tracker `.ops`.** Auto-sync handles it. Do not stage
  `.agent/tracker/.ops/` or `.agent/tracker-workspace/.ops/`.
- **No tracker mutations while an `auto-pr` run is in flight.** Check the `PR_PENDING` gate first
  (`test -f .git/PR_PENDING`). `auto-pr` rebases can clobber concurrent `tracker discuss`/`update`.
  All write-back (Phase 6) happens in one window with no `auto-pr` running.
- **Positive evidence to downgrade; no weasel words.** Moving an item *toward* resolved
  (`violated`→`satisfied`, `pending_validation`→`satisfied`) requires **positive evidence the
  invariant now holds** — never "couldn't reproduce." Absence of reproduction is not proof of a fix
  (it may be masked, or the probe may be wrong). "All known cases pass" is banned. Moving *away* from
  resolved on a confirmed reproduction (`pending_validation`→`violated`) may be applied directly.
- **Editing an existing item's title/description needs human-enabled edit-mode.** The agent can
  `discuss` (append) any time, but `edit-msg-text` / `delete-msg` / title edits require an open
  edit-mode window (human-authorized, 30-min, OS-enforced). Defer those to a manifest and the final
  step exactly as the dogfood procedure does (see Phase 6).
- **The agent produces data + recommendations; the human authorizes destructive moves.** Folds
  (closing one item into another), status downgrades, and edit-mode cleanups are *proposed* by the
  agent and *confirmed* by the human before write-back. Cross-links and confirmed-failure upgrades are
  routine and may be applied directly.

## Artifacts & directory layout

All sweep artifacts live under a single timestamped directory, never overwritten across sweeps:

```
${SWEEP_DIR}=~/hypergumbo_lab_notebook/tracker_hygiene_sweep_<UTC-START>/
  sweep_state.json              # orchestrator-only: start timestamp, repo HEAD, snapshot hash,
                                #   cluster map, dispatch log, correction counter
  snapshot/corpus.json          # frozen `tracker list --json` + per-item `show --json` (READ-ONLY for workers)
  blackboard/
    corrections.md              # orchestrator → workers, APPEND-ONLY (the live-steering channel)
    claims.tsv                  # worker ↔ worker lease table (item_id, worker_label, state)
  findings/<worker_label>.jsonl # worker → orchestrator, ONE json line per investigated item (streamed)
  clusters/
    candidate_clusters.md       # Phase 1 mechanical clusters
    clusters.md                 # Phase 3 confirmed causal clusters
    rejected_clusters.md        # Phase 3 audit log (consolidations considered + rejected)
  dedup/dup_pairs.tsv           # Phase 3 dup classifications
  status/status_verification.tsv# Phase 4 reproduce-or-not results
  meta_analysis.md              # Phase 5 deliverable: dominant-invariant-family table
  materialization.tsv           # Phase 6 record of every tracker mutation (one row per action)
  edit_mode_cleanup_manifest.md # Phase 6 deferred title/desc edits awaiting the human edit-mode window
  notebook_entry.md             # the lab notebook entry (carries the precise start timestamp)
```

## Phase 0 — Setup, freeze, timestamp the lab notebook

**The very first action is to stamp the start time.** A lab notebook entry is opened immediately so
the precise moment the analysis begins is recorded before any work happens.

1. **Capture the start timestamp and pin the code.**
   ```bash
   SWEEP_START_UTC=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
   REPO_HEAD=$(git rev-parse HEAD)
   SWEEP_DIR=~/hypergumbo_lab_notebook/tracker_hygiene_sweep_${SWEEP_START_UTC//:/}
   mkdir -p "$SWEEP_DIR"/{snapshot,blackboard,findings,clusters,dedup,status}
   ```
   The pinned `REPO_HEAD` is what Phase 4 reproduces against, so a status verdict is reproducible.

2. **Open the lab notebook entry (timestamp first).** Write `${SWEEP_DIR}/notebook_entry.md` with a
   header that records `SWEEP_START_UTC`, `REPO_HEAD`, the current branch, and the tunable parameters
   in force. Leave running-log and results sections to be filled as phases complete. This file is the
   durable record; keep appending to it (never rewrite earlier entries).

3. **Freeze the corpus snapshot.** Pull the whole tracker once so every worker reads a *consistent*
   set (the live tracker mutates under auto-sync):
   ```bash
   ./scripts/tracker list --json > "$SWEEP_DIR/snapshot/corpus.json"
   ```
   Then enrich with per-item `show --json` for every open item into the same snapshot dir (one file or
   a JSON map). Record the snapshot's sha256 and the item-id set in `sweep_state.json`. **Workers read
   only this frozen snapshot, never the live tracker**, so the corpus can't shift mid-sweep.

4. **Sanity-check tracker integrity.** Run `./scripts/tracker validate` and
   `./scripts/tracker tags --count`. Note any validation errors or deprecated/colliding tags in the
   notebook — they are inputs to the hygiene findings, not blockers.

5. **Commit the meta-criterion verbatim** to `sweep_state.json` (governs Phase 3, identical to the
   dogfood procedure): *"If two findings are actually two symptoms of one underlying problem, then
   that underlying problem should be what gets judged, not the two symptoms individually."*

6. **Initialize the blackboard.** Create an empty `blackboard/corrections.md` (with a one-line header
   explaining the append-only contract) and an empty `blackboard/claims.tsv`. Set
   `sweep_state.json.correction_counter = 0`.

## Phase 1 — Inventory & mechanical clustering (cheap first pass)

Before spending any subagent budget, cluster mechanically from the snapshot — this is free signal and
it seeds the worker assignments.

1. **Pull the open-item universe** from `snapshot/corpus.json` (open = not `done`/`satisfied`/
   `wont_do`; keep resolved items in a side list — Phase 4 re-checks a sample of `satisfied` for
   regression and Phase 3 may fold an open item *onto* a resolved one).

2. **Build mechanical clusters** from three cheap signals, in priority order:
   - **Existing parent / `isbefore` edges** (`scripts/tracker deps <ID>`): items already sharing a META
     parent (e.g. `INV-luhur`, `INV-numat`, `INV-jahiv`, `INV-bazij`) form a seed cluster.
   - **Shared tags** (`scripts/tracker tags --count`): tag co-membership is a weak grouping signal.
   - **Shared identifiers in titles/descriptions:** snake_case symbol names, file paths, edge-type /
     evidence-type / field names appearing in ≥2 items. Use snake_case-only matching (generic English
     words over-group — same lesson as `highsev_root_review.py`).

3. Write the result to `clusters/candidate_clusters.md`: each candidate cluster gets a provisional
   root-cause hypothesis (one sentence), its member item-ids, and the signal(s) that grouped them.
   **These are candidates, not verdicts** — Phase 2 confirms or splits them with real investigation.

## Phase 2 — Subagent deep investigation (parallel, steerable)

This is where items get genuinely investigated and where the orchestrator↔worker communication design
earns its keep. Workers deepen each candidate cluster: read the actual item via `tracker show`, read
the cited source at `REPO_HEAD`, confirm/refute the grouping, and gather the evidence Phases 3–5 need.

### Worker assignment

Spawn investigation subagents, **one cluster (or a small batch of unclustered singletons) per
worker**, capped at the vendor's concurrency limit. Each worker gets a label
(`inv-<cluster-id>`) and a bounded work-list of item-ids. **Keep work-lists short** (default 3–5
items, `--items-per-worker`) so chunk boundaries — the reliable steering points — are frequent.

### Worker prompt contract (task + inputs + output format only)

```
You are a tracker-investigation subagent. Working dir: ${SWEEP_DIR}.
Your assigned items: <item-ids>. Your label: <worker_label>.

INPUTS (read-only): snapshot/corpus.json (frozen item state — use this, do
NOT query the live tracker), and blackboard/corrections.md.

BEFORE EACH ITEM, in order:
  1. Re-read blackboard/corrections.md in full. It is the orchestrator's live
     channel; it may reassign you, kill an item, merge a cluster, or hand you a
     new hypothesis discovered by a sibling worker. Honor the latest applicable
     correction. Record the highest correction-id you have applied.
  2. Claim the item: append "<item_id>\t<worker_label>\tclaimed" to
     blackboard/claims.tsv ONLY if no live claim exists for it (skip if another
     worker holds it — avoids double work).

PER ITEM (investigate, do not just summarize the existing text):
  - Read the item: scripts/tracker show <ID> (NEVER read .ops files).
  - Read the cited source at the pinned HEAD (file:line refs in the item).
  - Answer, with evidence (file:line / command+output, captured to a probe log
    in the working dir):
      * ROOT CAUSE: the single underlying mechanism, in one sentence. Name the
        violated invariant if there is one.
      * CLUSTER FIT: does this item share a ROOT (not merely a symptom/theme)
        with its candidate-cluster siblings? "Would fixing one fix the other?"
      * STILL-TRUE (only if status is violated/pending_validation): does the
        item reproduce at the pinned HEAD? Give the reproduction command +
        result. If it does NOT reproduce, gather POSITIVE evidence the invariant
        now holds (don't just report "couldn't reproduce").
      * DUP CANDIDATES: any other item-id whose ROOT+FIX you believe matches
        (TRUE_DUPLICATE) or overlaps (RELATED_BUT_DISTINCT).
  - IMMEDIATELY append one JSON line to findings/<worker_label>.jsonl:
      {"item":"<ID>","root":"...","invariant":"<INV-id|null>",
       "cluster_fit":"confirm|split|move:<cluster>","kind":"<KIND>",
       "still_true":"reproduces|holds_now|indeterminate|n/a",
       "evidence":"...","dup_candidates":[["<ID>","TRUE_DUPLICATE|RELATED_BUT_DISTINCT","HIGH|MED|LOW"]],
       "applied_correction":<int>}
    (streaming per-item lets the orchestrator see your progress before you return).

RETURN (<=200 words): per-item one-liners (root + still_true + any dup candidate),
clusters you'd split or merge, and items you could NOT reach. Do not paste full
writeups; they are in findings/*.jsonl and the probe logs.
```

### Orchestrator loop & live steering — the communication design

The orchestrator does **not** fire-and-forget. It actively supervises:

1. **Stream-watch the findings.** Tail `findings/*.jsonl` as workers append. This gives partial
   visibility *before* any worker returns — the orchestrator sees a dup candidate or a root-cause
   collision the moment a worker writes it, not at chunk end.

2. **Detect cross-worker discoveries.** When two workers independently land on the same root, or
   worker A finds that worker B's item is actually a duplicate, or a new hypothesis emerges that
   should redirect in-flight work — that is a *correction event*.

3. **Push the correction (the live channel).** Append a timestamped, numbered block to
   `blackboard/corrections.md` and bump `correction_counter`:
   ```
   ## CORRECTION 7 — 2026-06-08T14:33:10Z
   REASSIGN: inv-cluster-3 — items WI-foo, WI-bar move to cluster inv-cluster-1 (shared root: <X>).
   STOP-ITEM: WI-baz — confirmed TRUE_DUPLICATE of WI-qux by inv-cluster-2; stop separate investigation, fold in Phase 3.
   NEW-HYPOTHESIS: for the INV-luhur family, also check whether <Y> co-occurs; sibling worker found it on WI-zap.
   ```
   Workers re-read this **before each item**, so a worker with a short work-list picks the correction
   up within one item — usually seconds-to-minutes, not at the end of a long run. The
   `applied_correction` field in each finding line lets the orchestrator **confirm propagation**: if a
   worker is still emitting `applied_correction: 6` after correction 7 was posted, it hasn't hit a
   boundary yet.

4. **For vendors with live messaging to running agents** (e.g. spawn the worker in the background and
   use the harness's send-message-to-agent capability), additionally nudge the worker with the new
   correction-id so it re-reads sooner. This is an *accelerator* on top of the corrections file, not a
   replacement — the file is the source of truth and the vendor-neutral channel.

5. **Re-dispatch at boundaries.** When a worker returns, integrate its findings, update the cluster
   map in `sweep_state.json`, and either (a) re-spawn / continue it with an updated work-list
   reflecting the corrections, or (b) retire it. New work discovered mid-sweep (e.g. a dup chain that
   pulls in items nobody was assigned) becomes a fresh work-list for the next available worker.

**Honest limits of the steering.** You cannot inject a message into the middle of a worker's
token generation — communication lands at **boundaries** (per-item re-read points and chunk
returns). Responsiveness is engineered, not magical: it comes from (a) short work-lists →
frequent boundaries, (b) the re-read-corrections-before-each-item discipline, (c) streamed
per-item findings giving the orchestrator early sight, and (d) the optional live nudge for
vendors that support it. Do not design steering that assumes mid-item interruption.

## Phase 3 — Corpus dedup (TRUE_DUPLICATE / RELATED_BUT_DISTINCT)

Now resolve the candidate clusters into confirmed root-cause families and classify the dup pairs that
Phase 2 surfaced. This reuses the dogfood procedure's two-axis method, pointed at *existing items*.

1. **Causal-axis consolidation (3b analogue).** For each candidate cluster, apply the meta-criterion's
   test — **"would fixing one item fix the other?"** — to each member pair. Members that share one
   underlying root cause form a confirmed family; record its root cause, members, the unifying fix,
   and HIGH/MED/LOW confidence in `clusters/clusters.md`. **Default to NON-clustering at LOW.** Keep a
   `clusters/rejected_clusters.md` log of consolidations considered and rejected — this is what makes
   the sweep auditable and prevents over-folding.

2. **Pairwise dup classification (Phase-6 trichotomy analogue).** For every dup candidate from
   Phase 2, classify the pair into `dedup/dup_pairs.tsv` with the **existing vocabulary**:
   - **TRUE_DUPLICATE** — same root *and* same fix; one item fully subsumes the other. Resolving one
     resolves both. → Phase 6 folds the redundant item onto the canonical one.
   - **RELATED_BUT_DISTINCT** — shared theme/symptom but a distinct fix, or one is independently
     closable. → Phase 6 cross-links both; neither is closed. **LOW-confidence → RELATED_BUT_DISTINCT,
     never TRUE_DUPLICATE** (never silently lose an item).
   - **NOT_A_DUPLICATE** — leave both alone.

   The same safe-default the dogfood procedure uses applies: **when in doubt, RELATED_BUT_DISTINCT**
   (cross-link) over TRUE_DUPLICATE (fold), because a wrongly-folded item is silently lost when its
   host closes.

3. **Pick the canonical item per TRUE_DUPLICATE.** Prefer the META/parent item, then the
   earlier/lower-id, then the higher-priority one, then the one with the richer evidence trail. Record
   the canonical↔redundant direction in `dup_pairs.tsv` — Phase 6 needs it.

## Phase 4 — Status verification (violated / pending_validation still hold)

For every `violated` and `pending_validation` open item (and a sampled set of `satisfied` items for
regression), use the Phase-2 `still_true` finding (the worker already reproduced at `REPO_HEAD`).
Record into `status/status_verification.tsv`: `item_id`, `current_status`, `outcome`, `evidence`,
`proposed_status`. Map outcomes to status per this table — **mirrors the bakeoff-validation lifecycle
and the dogfood resolved-fold status-adjustment rules**:

| Current | Phase-2 outcome | Proposed action |
|---|---|---|
| `violated` | reproduces at HEAD | keep `violated`; add a discuss note with the fresh positive-failure evidence (re-confirmation) |
| `violated` | does NOT reproduce **+ positive evidence invariant now holds** | propose `satisfied` (or `pending_validation` if not yet bakeoff-validated) — **human-confirmed** |
| `violated` | does NOT reproduce, no positive evidence | **keep `violated`**, flag `indeterminate` for a deeper probe; never close on absence |
| `pending_validation` | validation now passes (positive evidence) | propose `satisfied` — human-confirmed |
| `pending_validation` | reproduces / validation fails | set `violated` directly (confirmed failure — routine, no human gate) |
| `satisfied` (sampled) | regression reproduces | reopen to `violated` directly (confirmed regression) |

The `indeterminate` rows are the ones the operator most needs to see — they are exactly where "is it
still broken?" couldn't be answered cheaply. Never let an `indeterminate` masquerade as a clean bill.

## Phase 5 — Meta-analysis table (the deliverable)

Assemble `meta_analysis.md` from the confirmed families (Phase 3) joined with statuses (Phase 4). This
is the human-facing read. Present it as **data tables**, and let the human prioritize — the agent does
not decide which family to attack next.

**Dominant-invariant-family table** (one row per confirmed root-cause family, sorted by member count
then severity):

| Family (root / META anchor) | Members | Priority mix | Status mix | Has resolved root? | Representative items | Unifying fix |
|---|---|---|---|---|---|---|

Plus a few summary cuts:
- **Dedup ledger:** count of TRUE_DUPLICATE folds proposed, RELATED_BUT_DISTINCT cross-links, and the
  net open-item reduction if all folds are accepted.
- **Status drift:** counts of items whose status this sweep proposes to change, split by direction
  (downgrade-to-resolved vs. confirmed-failure upgrade vs. `indeterminate`).
- **Hygiene findings:** validation errors, deprecated/colliding tags, orphaned `isbefore` edges,
  items pointing at a now-`satisfied` parent, etc.

Write the same tables into `notebook_entry.md` under a "Results" heading.

## Phase 6 — Materialize (mechanical, gated, batched)

Apply the accepted changes back to the tracker. **Confirm no `auto-pr` is in flight first.** Batch the
mutations rather than one commit per call — use `scripts/tracker batch <file.htrac>` (deferred
auto-sync) or group the `update`/`discuss` calls and let auto-sync flush once.

1. **TRUE_DUPLICATE folds (human-confirmed).** For each pair: `tracker discuss <canonical> "Folds in
   <redundant>: <shared root + fix>."`, then close the redundant item with a rationale —
   `tracker update <redundant> --status wont_do --note "Duplicate of <canonical> (TRUE_DUPLICATE,
   tracker-hygiene-sweep <SWEEP_START_UTC>). Same root: <X>, same fix."`. Cross-link from the canonical
   side too so the redundant item's evidence isn't lost.
2. **RELATED_BUT_DISTINCT cross-links.** `tracker discuss` on **both** items naming the relationship;
   set a `parent` only if there is a compelling structural reason (prefer flat cross-links, per the
   trackerize playbook). Neither item is closed.
3. **Status changes.** Apply the Phase-4 proposals: confirmed-failure upgrades and confirmed
   regressions directly (with `--note` rationale); downgrades-to-resolved only after human
   confirmation, each with positive-evidence rationale in the note.
4. **Hygiene fixes.** Resolve deprecated/colliding tags via `tracker tags rename/deprecate`; re-parent
   items whose parent is now resolved; record each.
5. **Record everything** in `materialization.tsv` (one row per action: `item_id`, `action`,
   `target`, `dup_class|status_change|hygiene`, `rationale`).
6. **Deferred title/description cleanup.** Folds and re-scopings often leave an item's *title/body*
   describing pre-fold scope. Title/body edits need the human edit-mode window. Append-only `discuss`
   notes now; record the redundant-prose trims in `edit_mode_cleanup_manifest.md` (which item, which
   field/message, what to trim); as the **last step**, ask the human to enable edit-mode and confirm,
   then perform the `edit-msg-text`/title edits within the 30-minute window.

## Phase 7 — Finalize the lab notebook

Append to `notebook_entry.md`: the end timestamp, total items reviewed, families confirmed, folds /
cross-links / status changes applied vs. proposed-but-deferred, and any `indeterminate` items needing
follow-up. The entry — opened in Phase 0 with the precise start timestamp and closed here — is the
durable record of the sweep. Do **not** write a "the tracker is now clean / converged" verdict; report
the facts and counts and let the human read them.

## Tunable parameters

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `--items-per-worker` | 4 | 1–8 | Work-list length per investigation subagent. Smaller → more frequent steering boundaries, more spawn overhead. |
| `--max-concurrency` | vendor cap | — | Concurrent investigation workers; min(harness cap, sensible). |
| `--cluster-confidence-floor` | MED | LOW/MED/HIGH | Minimum confidence to treat a candidate cluster as a confirmed family in Phase 3. |
| `--satisfied-sample` | 10% | 0–100% | Fraction of `satisfied` items spot-checked for regression in Phase 4. |
| `--auto-apply` | off | off/cross-links-only | If `cross-links-only`, RELATED_BUT_DISTINCT cross-links + confirmed-failure upgrades apply without prompting; folds & downgrades always need human confirmation. |

## Dedup vocabulary (reused verbatim from the twenty-pass dogfood procedure)

So the two playbooks stay interoperable, this sweep uses the *same* labels:

- **Methodology-axis KIND** (per item, when characterizing how it relates to a sibling):
  INDEPENDENT / EXTENSION / CORRECTION / POINTER-ONLY / INVALIDATION (HIGH/MED/LOW confidence). An
  EXTENSION/CORRECTION must share the same *fix* as its target, not merely the same *symptom class* —
  when in doubt, INDEPENDENT.
- **Causal-axis clustering:** group by shared root cause via *"would fixing one fix the other?"*;
  default NON-clustering at LOW; maintain a `rejected_clusters.md`.
- **Dup trichotomy:** TRUE_DUPLICATE / RELATED_BUT_DISTINCT / NOT_A_DUPLICATE; LOW-confidence →
  RELATED_BUT_DISTINCT; when in doubt prefer the non-lossy choice (cross-link over fold).
- **meta-criterion** (verbatim): *"If two findings are actually two symptoms of one underlying
  problem, then that underlying problem should be what gets judged, not the two symptoms
  individually."*

## Recovery & failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Worker crashes mid-cluster | no return / no recent `findings/*.jsonl` append | re-spawn with the same work-list; its claims + streamed findings let it resume; un-investigated items still show `claimed` with no finding line |
| Two workers double-claim an item | duplicate live rows in `claims.tsv` | orchestrator keeps the first finding, discards the second; tighten the claim-before-investigate check |
| A correction never propagates | a worker's `applied_correction` stays below the latest id past its next boundary | the worker is mid-item; wait for the boundary, or (vendor permitting) live-nudge it; never assume it ignored the correction |
| Live tracker changed during the sweep | snapshot sha differs from a fresh `tracker list` at Phase 6 | re-pull, diff the item-set; for items added/changed since freeze, re-run Phase 2 on just those before writing back |
| `auto-pr` in flight at Phase 6 | `.git/PR_PENDING` exists | wait; do not mutate the tracker until it clears (rebase-clobber hazard) |
| Status verdict can't be reached cheaply | Phase-2 `still_true: indeterminate` | keep current status, flag for a deeper probe; never close on absence of reproduction |

## Vendor neutrality

The sweep assumes the vendor can: spawn a subagent and capture its return value; read/write local
files; run shell commands. The orchestrator↔worker communication uses the **blackboard files**
(`corrections.md` + streamed `findings/*.jsonl` + `claims.tsv`) as the portable channel — this works
for any vendor. Live mid-flight nudging (background agent + send-message) is an optional accelerator
where supported; where it is not, short work-lists + the re-read-before-each-item discipline keep
steering latency to roughly one item. The frozen snapshot guarantees every worker sees the same corpus
regardless of vendor scheduling.
