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

Phases 1–7 are the cohort path. The **Directed UAT-bakeoff path** (see dedicated section below) and **phase 8** (B-prospector and B-shape spot-checks) are peer modalities, not residue. Items route to UAT when (a) the human surfaces a specific item list and asks for ground-truth validation, (b) the item's discussion thread explicitly requests UAT / spot-check / ground-truth, or (c) the claim is edge-existence-shaped where cohort metrics could move for the wrong reason and only sampled ground-truthing is persuasive.

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

**Also read the discussion thread for explicit modality requests.** While reading each item, look for explicit instructions about how the validation should be performed: "validate via UAT," "spot-check on a real Airflow repo," "ground-truth required," "cohort metric only — no spot-check needed," etc. The author closest to the fix usually knows what evidence will be persuasive. When the discussion explicitly asks for UAT, route the item to the **Directed UAT-bakeoff path** below regardless of bucket. When it explicitly asks for cohort, keep it on the cohort path. When silent, decide by claim shape — edge-existence claims default to UAT, aggregate-metric claims default to cohort. Record the modality choice (and the reason) alongside the bucket classification in the lab notebook entry from phase 3.

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
- **All repos must be reachable under `~/ALL_REPOS/`** (the canonical catalog both cohort and UAT paths draw from). Symlinking is possible but adds session friction.

Document the cohort in a lab notebook entry before running, with a coverage matrix mapping each Bucket A item to which repos are strong targets vs incidental coverage.

## Phase 4: Run the cohort and verify per-claim

```bash
./scripts/bakeoff-deep init --pool ~/ALL_REPOS
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

## Directed UAT-bakeoff path

This is the front door for human-curated item lists and for items routed to UAT by the modality check in phase 1. UAT-bakeoff is intentionally less automated than the cohort path — kickoff is a human action, the agent is in advisory-only mode for campaign-creation steps. The deliberate friction is a pacing mechanism: UAT is the less battle-tested validation modality, and human gating prevents the agent from running ahead of judgment that hasn't been formed yet.

### Human-only kickoff steps

A UAT-bakeoff lives in `~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-vX.Y.Z/`, created by the human copying `~/hypergumbo_lab_notebook/hg-uat-template/`. The agent **must not**:

- Create or rename the campaign directory itself.
- Refresh `hg-docs/` even if the snapshots look stale (flag staleness in the conversation instead).
- Edit `<VERSION>` / `<ENVIRONMENT>` placeholders in `lab-notebook/index.md`.
- Run `./bin/status --set` or `--sync` for the initial `UNCONFIGURED → READY` flip.

If the campaign directory does not exist when this path is taken, the agent surfaces the suggested kickoff sequence and **stops** until the human reports `STATUS=READY`:

```bash
# Human runs:
cp -r ~/hypergumbo_lab_notebook/hg-uat-template ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>
cd ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>
# Refresh hg-docs/ from the upstream hypergumbo repo
# Edit lab-notebook/index.md to fill <VERSION> / <ENVIRONMENT>
./bin/status --sync   # flips STATUS to READY
```

Once `STATUS=READY`, the directed-UAT-bakeoff path proceeds through phases U1–U5 below. The phases are explicit about which actor — orchestrator agent, UAT agent, or human — performs each step, because the role boundaries are part of the validation discipline.

### Roles in the directed UAT-bakeoff path

Three actors with distinct boundaries:

| Actor | Where it runs | Source / tracker access | What it does |
|---|---|---|---|
| **Orchestrator agent** (the agent following this playbook) | hypergumbo repo | yes | Phase 1 audit, plan.md drafting from tracker (U1), tag management post-campaign (U4), cross-campaign housekeeping (U5). |
| **UAT agent** (a separate naive agent following the campaign's own AGENTS.md) | inside the campaign dir | **no** | Round execution (U3) — runs hypergumbo, ground-truths samples, writes report.md. |
| **Human** | — | — | Campaign kickoff (cp template, refresh hg-docs, fill placeholders, initial `--sync`); plan.md approval (U1); **starting the UAT agent** in the campaign dir (U2); signaling round/campaign completion. |

The deliberate firewall between orchestrator and UAT agent is the validation discipline. The UAT agent cannot cheat by knowing what hypergumbo's source "wants" the linker to do — it derives expected behavior from the plan.md's quoted claim text and the campaign's `hg-docs/` alone. This is a stronger validation stance than a source-aware agent could provide. **The orchestrator must not relay hypergumbo internals to the UAT agent through the human, the plan.md, or any other channel.** The plan.md transcribes only the public-facing claim text and concrete observable thresholds; it does not name internal linker classes, reference module paths, or quote source code.

### Phase U1 — Orchestrator agent: draft plan, get human approval, write to round directory

For each item routed to this round, the **orchestrator agent** reads the tracker discussion thread (`scripts/tracker show <ID>`) and drafts a per-item plan section. **The discussion-read step is mandatory** — it surfaces explicit modality requests, repo suggestions, and verdict criteria the original author may have left for the validator.

The plan must be **self-contained** because the UAT agent cannot read the tracker. Quoted claim text, target repo paths under `~/ALL_REPOS/`, expected observable signals, and verdict criteria as concrete thresholds all go in the plan. Use this template:

```markdown
## Item: <ID> — <abbreviated title>

**Claim** (transcribed by the orchestrator from the PR description or tracker discussion, so the UAT agent does not need tracker access): "<one-sentence quantitative claim>".
**Explicit modality request** (transcribed from discussion): yes / no — if yes, quote the discussion entry.
**Target repo**: <full path under ~/ALL_REPOS/>.
**Alternate targets**: <fallbacks if the primary doesn't exercise the construct cleanly>.
**Expected signal**: <observable JSON / sketch terms — provenance string, edge-count threshold, attribute presence — written so the UAT agent can evaluate from `hg.json` output alone, with no internal hypergumbo knowledge required>.
**Verdict criteria** (pre-committed by orchestrator, evaluated by UAT agent):
  - `moved` if <concrete threshold, e.g., "≥5 edges from <construct> to <impl> in `hg.json`, AND ≥4/5 ground-truthed correct against target source">.
  - `no_move` if <concrete failure threshold, e.g., "0 edges of the expected shape despite ≥3 instances of the construct in target source">.
  - `inconclusive` if <ambiguous-result threshold, e.g., "edges present but no clean ground-truth target found in this repo — re-route to <alternate>">.
**Ground-truth sample size**: N candidates, sampled by <strategy: e.g., "first N edges of the expected shape" or "N random calls of the dispatch construct in target source">.
**Ground-truth instructions for the UAT agent**: For each sampled candidate, open the target repo's source at the `file:line` reported by hypergumbo and confirm that the relationship hypergumbo claims (call edge / dispatch / boundary / etc.) actually exists. Verdict per candidate: pass / fail / ambiguous. Roll up to the per-item verdict per the criteria above.
```

After drafting all per-item sections, the orchestrator presents the assembled plan to the human for approval **inline in the conversation, before writing anything to disk**. Once approved, the orchestrator creates the round directory and writes plan.md:

```bash
# Orchestrator commands:
cd ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook
cp -r round-template round-NN-validation-<batch>
# Then write plan.md inside round-NN-validation-<batch>/ with the approved content.
```

The orchestrator does NOT run `./bin/status --sync` after writing plan.md — STATUS stays at `READY`. Phase U2 is the next gate.

### Phase U2 — Human: kick off the UAT agent

After plan.md is on disk and the human has reviewed it at the file path:

```bash
cd ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>
./bin/status --sync   # flips STATUS to IN_PROGRESS now that round-NN-*/ exists
# Start the UAT agent (vendor-specific):
#   claude        # or:  codex    cursor    gemini
```

**Only the human starts the UAT agent.** The orchestrator can suggest the command but does not run it. Once the UAT agent is running, **the orchestrator is unavailable for the duration of the round** — interfering across the firewall would defeat the validation. If the UAT agent surfaces a question that genuinely requires orchestrator-side knowledge (e.g., a malformed plan.md), the human relays it; the orchestrator's answer is recorded in the round's report.md so the verdict remains reproducible from the campaign artifacts alone.

### Phase U3 — UAT agent: execute the round (described from the orchestrator's POV)

This phase is the UAT agent's responsibility, executed per the campaign's `AGENTS.md`. The orchestrator does not run any of these steps. The description here is what the orchestrator can expect to land in `round-NN-*/data/` and `round-NN-*/report.md` when the round concludes:

- The UAT agent runs hypergumbo against the target repos named in plan.md, redirecting outputs per the campaign's output-capture conventions.
- It archives raw outputs into `round-NN-*/data/`, keyed by item ID.
- It evaluates each item's `Expected signal` against the `Verdict criteria` from plan.md, exactly — without softening, expanding, or substituting different criteria.
- For each item, it ground-truths the documented sample size by reading the target repo's source at the file:line reported by hypergumbo. UAT validation has no privileged source-of-truth — direct source reading at the target repo is the compensating discipline, and it's a stronger validation stance for output-shaped claims than importing canonical hypergumbo modules would be.
- It writes `round-NN-*/report.md` opening with a verdict matrix and continuing with per-item ground-truth narratives.
- Incidental findings (bugs / DQs / UX issues observed but not directly tested by plan.md) go into a separate "Incidental findings" subsection at the bottom of report.md, following the campaign's findings-distinction discipline (observation vs confirmed bug vs design question).

Expected report.md shape:

```markdown
# Round NN — Validation Verdicts

| Item | Verdict | Evidence |
|---|---|---|
| WI-... | moved | data/<artifact>: 12 edges of expected shape, ground-truth 5/5 correct (file:line refs in subsection). |
| WI-... | no_move | data/<artifact>: 0 expected edges despite 3 dispatch sites observed in target source. |
| WI-... | inconclusive | data/<artifact>: edges present but target lacks the construct cleanly; suggest re-target on <alternate>. |

## WI-... — <title>
<ground-truth narrative: file:line references in target repo, observed-vs-expected delta, incidental observations>

## Incidental findings
<bugs / DQs / UX issues surfaced during validation, classified per the campaign's standards>
```

When the round concludes, the human signals to the orchestrator (typically by ending the UAT agent session and saying so explicitly).

### Phase U4 — Orchestrator agent: tag management (post-round)

After the human signals the round complete, the orchestrator reads `~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook/round-NN-<batch>/report.md` and applies tracker mutations per the verdict matrix. **The discipline mirrors the cohort aggregator, applied manually:**

- **`moved` verdict** — strip the tag and add a resolution discussion entry:
  ```bash
  scripts/tracker update <ID> --remove-tag awaits_bakeoff_validation \
    --note "Validated via UAT round ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook/round-NN-<batch>/. <one-sentence evidence pointer>. Verdict: moved."
  ```
  (The `--note` shorthand combines the update with a discussion entry. Either form works.)

- **`no_move` verdict** — keep the parent tagged. File a P1 regression sub-item with `--parent <ID>`, `--status todo_soft`, **without** the `awaits_bakeoff_validation` tag (regression items earn the tag only when their own fix produces a fresh quantitative claim). Add a discussion entry on the parent pointing at the regression item:
  ```bash
  REGRESSION_ID=$(scripts/tracker add \
    --kind work_item \
    --title "Regression from UAT round-NN: <parent title abbreviated>" \
    --priority 1 \
    --status todo_soft \
    --parent <ID> \
    --description "UAT validation in ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook/round-NN-<batch>/ verdicted no_move. <evidence summary>. Investigate per the structural-fix protocol.")
  scripts/tracker discuss <ID> "Regression sub-item filed: $REGRESSION_ID. UAT verdict: no_move. See ~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook/round-NN-<batch>/report.md."
  ```

- **`inconclusive` verdict** — keep the parent tagged. Add a discussion entry recording the round, the verdict, and what would need to be different (different target repo, additional construct in target, fresh release with the dependent fix shipped) for a clean verdict next time:
  ```bash
  scripts/tracker discuss <ID> "UAT round-NN verdict: inconclusive. <reason>. Re-route to <suggested alternate target> in next round, or wait for <dependent change>."
  ```

The orchestrator also files tracker items for any incidental findings the UAT agent recorded under "Incidental findings" — bugs as `kind=work_item` `status=todo_hard` (or `todo_soft` if minor), DQs and UX issues per their normal classification.

**Anti-patterns specific to UAT tag management:**

- *Stripping a tag on `inconclusive`* to "clear the queue" — the residual tag is the queue's signal that more work is needed. Same shape as the cohort-path anti-pattern in phase 6 but inverted: there, the aggregator's plurality is too conservative and the agent corrects with rationale; here, the orchestrator's first instinct may be too aggressive and must be checked.
- *Inheriting the tag onto regression sub-items*. Regression items track a fresh problem; they earn `awaits_bakeoff_validation` only after their own fix ships with a fresh quantitative claim. Tagging them at filing time pollutes the queue.
- *Using `tracker update --remove-tag` without a paired discussion entry*. The audit trail is the entire point of the tag discipline — the resolution rationale must be reconstructable without context-window archaeology. `--note` collapses the two operations and is preferred.
- *Re-running plan.md verdict criteria post-hoc when the report.md verdict feels wrong.* If the orchestrator disagrees with a UAT verdict, the right path is to file a tracker observation and let the next round re-validate — not to override the UAT agent's verdict from outside the firewall. Override-from-outside is exactly the cheating modality the firewall prevents.

### Phase U5 — Orchestrator agent: round close-out and master-report synthesis

After tag mutations land, the orchestrator updates `lab-notebook/index.md` with a one-row summary of the round (status, items validated, items filed as regressions, items left inconclusive, link to the round's report.md).

When all planned rounds for the campaign are complete, the orchestrator writes `lab-notebook/master-report.md` — synthesizing per-round findings into unified bug/issue numbers (`BUG-NN`, `DQ-NN`, `UX-NN`) for incidental findings cross-referenced from per-round reports, with validation verdicts in their own section. Use the prior UAT's structure (`~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v2.6.0/lab-notebook/master-report.md`) as a reference. The orchestrator can author master-report.md because it summarizes already-public report.md contents — it does not require the firewall.

`./bin/status --sync` (run by the human or orchestrator) advances `STATUS` to `CONCLUDING` once `master-report.md` exists, and to `CONCLUDED` after the 2-day mtime window.

### Multi-campaign discipline

Each release with validation work gets its own campaign directory:

```
~/hypergumbo_lab_notebook/bakeoff_artifacts/
├── broad-...                         # BROAD bakeoff sessions
├── deep-...                          # DEEP bakeoff sessions
├── hg-uat-v2.6.0/                    # historical — first UAT, do not modify
├── hg-uat-v2.7.0/                    # next campaign, when v2.7.0 ships
└── hg-uat-v2.7.1/                    # subsequent, etc.
```

Per-campaign rules:

- **Don't delete prior campaigns.** They're the convergence record across releases. The lab-notebook/master-report.md of each is the authoritative narrative for what was validated when.
- **Open each new campaign's `lab-notebook/index.md` with a prelude** pointing at the prior campaign's `master-report.md` and noting: items the prior campaign cleanly stripped (now resolved), items the prior campaign filed regressions for and what's known to have shipped since, items rolling into this campaign for first-time validation (newly-tagged since the prior cutoff).
- **Don't re-validate items the prior campaign cleanly stripped** unless evidence suggests regression. Re-tagging a regressed item is the right path — it re-enters the queue and gets a fresh round in the new campaign.
- **One UAT-bakeoff per release, not per item batch.** Successive item batches within a release become additional rounds in the same campaign, not new campaigns. The campaign directory's `STATUS` (`READY`/`IN_PROGRESS`/`CONCLUDING`) tracks that.

### When the queue has both UAT-routed and cohort-routed items

A single processing session can use both paths in parallel. The cohort path lives in `~/hypergumbo_lab_notebook/bakeoff_artifacts/deep-<timestamp>/`; the UAT path lives in `~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/`. Tag management converges on the same tracker — items get the same `--remove-tag awaits_bakeoff_validation` mutation regardless of which path validated them, with discussion entries citing whichever artifact (bakeoff session or UAT round) produced the verdict. Don't duplicate validation across modalities for a single item — pick one path per item per processing session and record the choice in phase 1's classification table.

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

A processing session is done when one of the following holds:

1. **Queue is empty.** Tag count = 0.
2. **Queue is residue.** All remaining items are documented Bucket B (prospector-dependent or shape-claim) entries waiting on a specific external trigger (next prospector run / a UAT-bakeoff spot-check round in the next campaign).
3. **Time-budget exhausted.** Document where the session stopped, what items remain in which bucket, what modality each is routed to, and what the next session should pick up. Update the lab notebook entry started in phase 3 with the final state.

A clean session report includes: starting tag count → ending tag count, modality breakdown of items processed (cohort vs UAT-bakeoff vs prospector), list of PRs landed (with item IDs), regression sub-items spawned and how they were resolved, structural infrastructure issues surfaced (filed as new tracker items), and the final composition of the residual queue.

For UAT-bakeoff rounds specifically, the per-campaign report (`~/hypergumbo_lab_notebook/bakeoff_artifacts/hg-uat-v<release>/lab-notebook/master-report.md`) is the durable artifact — the processing-session lab notebook entry references it but doesn't duplicate it. A UAT campaign isn't "done" in the same sense as a session: campaigns persist across releases as the convergence record. The campaign's `STATUS` lifecycle (`UNCONFIGURED` → `READY` → `IN_PROGRESS` → `CONCLUDING` → `CONCLUDED`) tracks per-campaign progress; the validation queue (`scripts/tracker list --tag awaits_bakeoff_validation`) tracks cross-campaign convergence.
