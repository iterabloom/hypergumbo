<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Process the `awaits_bakeoff_validation` Queue with Bakeoffs + UAT

## When to use

Run this procedure when the `awaits_bakeoff_validation` queue is large enough to warrant a dedicated session — empirically, around 15+ tagged items, or when the human surfaces it. The procedure routinizes what's otherwise an ad-hoc "drain the backlog" effort: triage anomalies first, validate everything that fits a single targeted cohort, then peel off the items that need a different validation modality (UAT spot-check, prospector aggregate, invariant property test).

The playbook treats validation as a **queue-processing pipeline**, not a per-item investigation. That framing matters because the queue accumulates anomalies over time (stale tags, misapplied tags, reverted-fix tags, regression sub-items whose parents are still tagged) and those anomalies must be cleared before substantive validation work, or they pollute the cohort verdicts.

## The seven phases

1. **Audit and classify the queue.** Read every tagged item's claim text. Sort each into a bucket.
2. **Strip anomalies first.** Stale (already-validated), misapplied (no quantitative claim), reverted-fix tags get stripped with audit-trail discussion entries.
3. **Design a minimum-target cohort.** Pick the smallest repo set whose union exercises all cohort-validatable claims.
4. **Run the cohort and verify per-claim.** Don't rely on the aggregator's auto-injection — write a verification script that loads each repo's `hg.json` and produces a per-claim, per-repo verdict matrix.
5. **Write substantive YAML assessments + apply.** Fill the `awaits_bakeoff_validation_verdicts` block for every applicable claim (not just the auto-injected ones). Run `bakeoff-deep-reflect aggregate --apply-verdicts`.
6. **Hand-correct inconclusive plurality.** Where one repo cleanly moved and the rest were inconclusive (niche-language target), strip with rationale.
7. **Tackle regressions, then re-iterate.** Investigate `no_move` parents; either fix structurally or close `wont_do` with a documented reason. After fixes ship, run `cycle --workdir <existing-session>` to produce iter-002 in the **same** session — not a fresh `init` — so convergence is visible in the session's iteration counter.

For items that don't fit a cohort (Bucket B: prospector-pipeline-dependent or shape claims), phase 8 is **UAT-style spot-check** — see the dedicated section below.

## Phase 1: Audit and classify

Run `scripts/tracker list --tag awaits_bakeoff_validation` and count. Then for each, read the claim text via `scripts/tracker show <id>` (use `--json | jq` for batch processing if the count is large). Sort into:

| Bucket | Definition | Validation path |
|---|---|---|
| **A** | Aggregate-count claim (FP-reduction, edge-count delta, tier-ratio shift, structural property) measurable from `hg.json` / `io-boundaries.txt` artifacts | Standard BROAD/DEEP cohort |
| **B-prospector** | Per-category candidate count from the dead-code prospector's `aggregate-v5` pipeline | Run prospector on the relevant target; not part of standard cohort |
| **B-shape** | Shape-of-output property (e.g., no symbol IDs match `packages.<pkg>.src.<mod>`) | Grep on cohort artifact OR add load-time invariant + property test |
| **D-stale** | Validation discussion entry already exists; tag was never stripped | Strip immediately in phase 2 |
| **E-misapplied** | Item is meta-infrastructure (the wiring task itself) or has no quantitative claim | Strip immediately in phase 2 |
| **F-reverted** | The shipped fix was undone; original quantitative claim no longer applies | Strip + mark `wont_do` immediately in phase 2 |

The classification is done by reading the item, not by reading the tag. A tag's presence is necessary but not sufficient — the queue accumulates anomalies precisely because nobody re-classifies on tag application.

## Phase 2: Strip anomalies first

For each item in buckets D / E / F:

```bash
scripts/tracker update <ID> --remove-tag awaits_bakeoff_validation [--status wont_do]
scripts/tracker discuss <ID> "<one-paragraph rationale: why this isn't actually awaiting cohort validation, with pointers to the prior validation discussion / the wiring nature / the revert PR>"
```

Do not skip the discussion entry. The audit trail is the entire point of the discipline — a future agent looking at why the tag came off must be able to reconstruct the reasoning without context-window archaeology.

**Anti-pattern:** treating the tag-strip as the whole transaction. Without the discussion, the next audit can't tell stale-with-evidence from random-strip and the queue rots again.

## Phase 3: Design a minimum-target cohort

For each Bucket A item, write down what the claim needs from a target repo:
- Language (Python / Java / Ruby / Go / Elm / etc.)
- Construct (monorepo layout / shared-helper IO sink / specific framework)
- Scale (small repo OK or needs a real-world target)

Then build a cohort matrix: rows are claims, columns are candidate repos. Pick the smallest column set that covers every row at least once. A few practical rules:

- **Match canonical-cohort conditions when possible.** For regression items (e.g., a no-move verdict on a specific cohort), include the same repos so the validation is directly comparable to the original failure.
- **Niche claims often only need one repo.** A monorepo-Python claim only needs one monorepo Python repo. The cohort plurality logic will call this "inconclusive" because the other repos can't test it; that's fine — phase 6 handles it.
- **Don't add repos for breadth.** This is a targeted validation cohort, not a coverage-survey cohort. Every repo costs minutes of analysis time.
- **All repos must be in the standard pool** (`~/whole_bunch_of_repos/` by default). Symlinking is possible but adds session friction.

Document the cohort in a lab notebook entry before running, with a coverage matrix mapping each Bucket A item to which repos are strong targets vs incidental coverage.

## Phase 4: Run the cohort and verify per-claim

```bash
./scripts/bakeoff-deep init --pool ~/whole_bunch_of_repos
./scripts/bakeoff-deep cohort --repos repo1,repo2,...,repoN --count N
./scripts/bakeoff-deep cycle > /tmp/cycle.log 2>&1   # 'run + diagnose + reflect' (creates prompts only)
```

The reflect step writes per-repo prompt files at `<workdir>/reflect/cohort-001/iter-001/<repo>.prompt.md` and announces an *"Injecting N awaits_bakeoff_validation claim(s) into reflect prompts"* line. Crucially, **N is small** — the auto-injector keyword-matches against item descriptions and typically catches only a few. The agent must fill verdicts for **every applicable claim**, not only the injected ones.

Write a verification script — Python is fine — that loads each repo's `hg.json` and `io-boundaries.txt` and produces a per-claim, per-repo verdict matrix:

```python
# Sketch (fill in the per-claim measurement logic)
import json
from collections import Counter
ARTIFACT_ROOT = "<workdir>/out/cohort-001/iter-001"
REPOS = [...]
for repo in REPOS:
    with open(f"{ARTIFACT_ROOT}/{repo}/hg.json") as f:
        d = json.load(f)
    # Per-claim measurements...
    # Output: claim_id -> (verdict, evidence)
```

**Hard rule: import from the actual codebase, do not hand-roll allowlists.** The most expensive mistake of this session was a hand-rolled `KNOWN_LANGS` set in the verification script that omitted `jsonnet` and `rst`, producing 3,000+ false-flag invalid-language nodes. Use `from hypergumbo_core.taxonomy import LANGUAGES` (or whatever is canonical for the property being checked). When the codebase changes, the verification script automatically tracks.

The script's output is the basis for filling the YAML assessments in phase 5.

## Phase 5: Write substantive YAML assessments + apply

For each repo, write `<workdir>/reflect/cohort-001/iter-001/<repo>.assessment.yaml` following the schema in the prompt files. The critical block:

```yaml
awaits_bakeoff_validation_verdicts:
  - item_id: "<full-id>"
    verdict: moved | no_move | inconclusive
    evidence: "<one-sentence pointer to the artifact or source file that justifies the verdict>"
```

Include an entry for **every claim that this repo can test** — not just the auto-injected one. The aggregator computes plurality across repos per claim, so missing entries = the repo gets counted as "no opinion" for that claim, which skews the plurality.

The other assessment fields (`refactoring`, `new_feature`, `understanding`, `io_security`, `dataflow_quality`, `task_scores`, `dimension_scores`, `improvement_ideas`, `questions`) get pragmatic but non-trivial answers. They feed the dev-perspective summary, not the per-claim verdict logic.

Then:

```bash
./scripts/bakeoff-deep-reflect aggregate --workdir <workdir> > /tmp/aggregate.log 2>&1
# Review the dry-run mutation plan
./scripts/bakeoff-deep-reflect aggregate --workdir <workdir> --apply-verdicts
```

The aggregator: strips tags on plurality `moved` (with audit-trail discussion), spawns regression sub-items on plurality `no_move` (with parent linkage), and emits no-action notes on `inconclusive` / `tied` plurality.

## Phase 6: Hand-correct inconclusive plurality

The aggregator's plurality logic counts per-repo verdicts and picks the most common. A claim that is `1 moved + 4 inconclusive` (niche-language or niche-construct claim) plurality-resolves to `inconclusive` and gets no auto-action. But the targeted repo's verdict is unambiguously `moved` — the cohort just happens not to have other repos that could test it.

When this happens, hand-strip the tag with a discussion entry that:
1. States the cohort+iter
2. Lists the per-repo verdicts (e.g., `chatwoot=moved (specific evidence); 4 others=inconclusive (not Rails targets)`)
3. Justifies the hand-strip ("the targeted-repo verdict is unambiguously moved; plurality logic penalizes niche claims")

Same for `tied` plurality (1 moved + 1 no_move + 3 inconclusive) when the no_move is structurally expected (e.g., framework linker by design doesn't support a particular framework family per its own docstring).

**Anti-pattern:** leaving an item tagged because the aggregator's plurality verdict was inconclusive when the underlying claim's actual validation target cleanly moved. Same outcome as the queue-rot in phase 1, just with extra ceremony.

## Phase 7: Tackle regressions, then re-iterate

For each regression sub-item the aggregator spawned (these are P1, parent-linked, status `todo_soft`):

1. **Investigate.** Read the parent's claim, the regression evidence, and trace producers / linkers / pipeline stages until you understand why the metric didn't move.
2. **Decide.** Three outcomes:
   - **Real bug:** fix it structurally. If the bug spans multiple producers (the common case for INV-nodij-style invariant violations), batch the fix per the structural-fix protocol.
   - **By-design limitation:** close `wont_do` with a discussion entry quoting the relevant docstring/spec language and proposing a follow-up if the gap matters (e.g., "Rails routing-table dispatch needs a separate config/routes.rb-aware linker; not yet warranted by prospector signal").
   - **Cohort-coverage gap:** close `wont_do` with a discussion entry naming the missing repo class (e.g., "decorator-based controller framework target like spring-petclinic"). The parent stays tagged for the next cohort that includes such a target.

3. **Ship the fix(es)** via `auto-pr`. One PR per logical fix; combine same-shape multi-producer fixes if they're in the same package.

4. **Re-iterate.** Crucial: do **not** call `bakeoff-deep init` for the validation re-run. Use `cycle --workdir <original-session>` to produce iter-002 in the **same** session:

```bash
./scripts/bakeoff-deep cycle --workdir ~/hypergumbo_lab_notebook/bakeoff_artifacts/deep-YYYYMMDD-HHMMSS
```

This is the convergence-tracking discipline from `bakeoff-artifacts-guide.md`: sessions are how the process measures fix-iterate convergence over time. Fresh `init`s break that signal and waste artifact storage.

5. **Re-verify** with the same Python script (point `ARTIFACT_ROOT` at `iter-002`) and re-write per-repo YAMLs with new verdicts. Re-apply.

## Phase 8: UAT-style spot-check (Bucket B items)

The remaining tagged items after the cohort iterations should be exclusively Bucket B (prospector-pipeline-dependent or shape claims). For each:

### B-prospector (per-category candidate counts)

These claims live in the WI-tubot prospector aggregate-v5 pipeline, which is orthogonal to BROAD/DEEP cohorts. Validation procedure:

1. Pick the relevant target repo (Jackson-using Java for `WI-gupah`, Airflow for `WI-nutav`, Django app for `WI-nosug`, candle/zed/codex for `WI-kivut`, Kafka for `WI-lisov`).
2. Run hypergumbo on the target with the dead-code prospector enabled. (See `bakeoff-deep` if there's a flag; otherwise invoke the prospector aggregate-v5 pipeline directly.)
3. Compute the per-category candidate count (e.g., `python_orm_dispatch`) before-and-after vs the claim threshold.
4. UAT layer: **sample 10–15 previously-flagged candidates** in the affected category. For each, open the source code at the reported file:line and ground-truth that the new dispatch edges connect them to the right call sites. This is the hg-uat-style verification — necessary because per-category counts can drop for the wrong reason (e.g., a different fix flipped reachability).
5. If the count moved AND the spot-checked candidates are connected correctly, strip the tag with a discussion citing both the count delta and the spot-check sample (sample size, file:line refs, verdict per sample).

### B-shape (shape-of-output properties)

These claims (e.g., `WI-davan` — no symbol IDs carry `packages.<pkg>.src.<mod>`) have no aggregate metric. Two paths:

1. **Grep the cohort artifact.** Pick a repo that exercises the construct (e.g., a monorepo Python repo for `WI-davan`). Grep `nodes[*].id` in `hg.json` for the bad shape. Zero matches across the repo's symbol IDs = strip with discussion citing the grep.
2. **Add a load-time invariant + property test.** Often the better long-term answer: convert the tag into structural enforcement. The validator fires on every analyzer run instead of waiting for a cohort that may not catch the property anyway.

If the shape claim corresponds to an existing invariant that's marked `satisfied`, also surface that as the canonical home for the claim and consider closing the regression sub-item.

## Process anti-patterns to avoid

- **Trusting auto-pr / merge-pr state announcements without cross-check.** When the API was returning 5xx during polling, the script's "🔄 Closing PR" message can be wrong. Always confirm with `./scripts/ci-debug pr-status <num>` before reporting up. Existing INV-rahib invariant covers this on the tool side; agent-side discipline is to not parrot the script's state-changes verbatim.
- **Over-filing tracker items.** When extending a discussion on an existing item would suffice, do that instead of spawning a new item. The user's repeated pushback this session was a signal — every new item costs queue-management overhead.
- **Manual cleanup of `.git/TRACKER_SYNC_PENDING`.** This marker leaks on SIGKILL (e.g., when the reflect-aggregate step's 60s subprocess timeout fires). Per WI-nutin, the right structural fix is fcntl.flock; until that lands, recognize the symptom (auto-pr exits with "Error: tracker sync in progress") and check whether a sync process is actually running before deleting the marker. Don't make the manual cleanup step part of the routine.
- **Hand-rolled allowlists in verification scripts.** Drift from the codebase's canonical sources guarantees false flags. Always import from the canonical module (`from hypergumbo_core.taxonomy import LANGUAGES`).
- **`init`-for-every-iteration.** Validation re-runs of a cohort go in iter-002 of the same session, not a fresh init. The bakeoff-artifacts-guide playbook covers this; it's worth re-reading.

## Process patterns worth keeping

- **Audit-then-act sequencing.** Anomaly cleanup before substantive validation work. Polluted queues produce polluted aggregate verdicts.
- **Verification-script-as-source-of-truth.** The script is the artifact that lets a future agent re-verify the same cohort with a different claim set. Save it (e.g., `/tmp/verify_bucket_a.py`) and reference it in the lab notebook entry.
- **Per-claim verdict completeness.** The aggregator only sees what's in the per-repo YAMLs. Filling all applicable claims (not just auto-injected ones) is what makes the plurality logic produce useful verdicts.
- **Hand-correction with rationale.** Plurality is a heuristic, not law. Niche claims that one targeted repo cleanly validates should be stripped with documented rationale.
- **Wont_do is a valid resolution.** Regression sub-items don't always need a fix. By-design limitations close `wont_do` with discussion. Cohort-coverage gaps close `wont_do` with a note about the missing repo class.
- **Structural-fix batching.** When N producers share a root cause (e.g., N analyzers each emitting `dst=f"unresolved:{name}"`), one PR fixes all N. Each gets a regression test.
- **Iter-NN convergence tracking.** Same session, incrementing iteration counter — the `bakeoff-deep status` view shows the fix-iterate trajectory and the convergence trend.

## What "done" looks like

The session is done when one of the following holds:

1. **Queue is empty.** Tag count = 0.
2. **Queue is residue.** All remaining items are documented Bucket B (prospector-dependent or shape-claim) entries waiting on a specific external trigger (next prospector run / a spot-check session).
3. **Time-budget exhausted.** Document where the session stopped, what items remain in which bucket, and what the next session should pick up. Update the lab notebook entry started in phase 3 with the final state.

A clean session report includes: starting tag count → ending tag count, list of PRs landed (with item IDs), regression sub-items spawned and how they were resolved, structural infrastructure issues surfaced (filed as new tracker items), and the final composition of the residual queue.
