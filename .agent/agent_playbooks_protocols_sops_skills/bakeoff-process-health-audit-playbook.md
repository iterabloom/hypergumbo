# Bakeoff Process Health Audit

A periodic meta-assessment of the bakeoff feedback loop itself — not "what did hypergumbo detect?" but "is the bakeoff process producing useful signals and are those signals being acted on?"

## When to Run

- Periodically (weekly cadence suggested, or after a burst of sessions)
- When you suspect the bakeoff loop is spinning without progress
- When guidance_log stop hooks keep surfacing the same issues
- Before a release, to assess confidence in coverage/quality claims

## Time Box

30-45 minutes. This is a health check, not a re-analysis of artifacts.

## Step 1: Select the Time Window

Use a sliding window that guarantees a minimum sample size of 2 sessions:

1. List all sessions: `ls -d ~/hypergumbo_lab_notebook/bakeoff_artifacts/{broad,deep}-* | sort`
2. Filter to the past 7 days (by directory name timestamp, format `YYYYMMDD`).
3. If fewer than 2 sessions fall within that window, expand to 14 days.
4. If still fewer than 2, expand to 21 days. Continue adding 7 days until you have at least 2.
5. Record the window boundaries and session count. This cadence metric is itself a signal — if you had to go back 3+ weeks to find 2 sessions, bakeoff frequency may be too low.

Separate sessions into BROAD and DEEP buckets. Both are assessed, but they answer different questions (coverage vs. usefulness).

## Step 2: Session Inventory

For each session in the window, read `state.json` and record:

| Field | Where | What it tells you |
|-------|-------|-------------------|
| `session_id` | `state.json` | Unique session identifier |
| `created_at` | `state.json` | When the session started |
| `cohort_number` | `state.json` | How many cohorts were run |
| `iteration` | `state.json` | How many fix-iterate cycles happened |
| `verdicts[].verdict` | `state.json` | GOOD / WARN / FAIL per repo |
| `verdicts[].concerns` | `state.json` | What triggered non-GOOD verdicts |
| `hypergumbo_code_hash` | `state.json` | Whether the code changed between sessions |
| `reflect_statuses[]` | `state.json` | Were prompts generated? Assessments completed? Summary produced? |

Compile a summary table:

```
Session                     | Type  | Cohorts | Repos | GOOD | WARN | FAIL | Reflects Done? | Code Hash
deep-20260404-002123        | DEEP  | 1       | 3     | 0    | 3    | 0    | No             | 912b5f8f
deep-20260403-232448        | DEEP  | 1       | 2     | 1    | 1    | 0    | Yes            | 912b5f8f
broad-20260320-060444       | BROAD | 3       | 15    | 10   | 5    | 0    | No             | adc1a212
```

## Step 3: Convergence Trend Analysis

Answer these questions with evidence from the session inventory:

1. **Are sessions converging?** Compare verdict distributions across sessions with the same code hash. If WARN/FAIL counts aren't decreasing between iterations, the fix-iterate loop isn't working.

2. **Are iterations happening?** Check `iteration` counts. If most sessions are `iteration: 1` with WARN/FAIL verdicts, the agent is creating new sessions instead of iterating on existing ones. This is a process failure — the loop is open, not closed.

3. **Are the same concerns recurring?** Collect all `verdicts[].concerns` strings across sessions. Group by concern type (e.g., `LOW_CROSS_LANGUAGE_IO_PCT`, `LOW_IO_TAG_RATE`). If the same concern appears in 3+ sessions, either the fix isn't working or nobody's attempted a fix.

4. **Did the code actually change?** Compare `hypergumbo_code_hash` across sessions. Sessions with identical hashes and identical concerns are pure waste — the agent re-ran without fixing anything.

## Step 4: Reflect Pipeline Completion

The reflect pipeline (prompts -> assessments -> summary) is the qualitative feedback channel. Check completion rates:

For each session, examine `reflect_statuses[]`:
- `has_prompts: true` — Step 1 done (prompt generation)
- `has_assessments: true` — Step 2 done (LLM evaluation)
- `has_summary: true` — Step 3 done (aggregation)

Calculate the completion funnel:
```
Sessions with prompts generated:   X / N  (Y%)
Sessions with assessments done:    X / N  (Y%)
Sessions with summary produced:    X / N  (Y%)
```

Low completion rates indicate the agent is running bakeoffs but not closing the feedback loop. This is the most common process failure — sessions pile up without reflects.

## Step 5: Signal-to-Action Pipeline

Check whether bakeoff findings are being converted to concrete work:

1. **Tracker items from bakeoffs:** Run `scripts/tracker list --tag bakeoff_infrastructure` and `scripts/tracker list --tag analysis_quality` to find bakeoff-originated items. Count items created during the window, and their current statuses.

2. **PRs from bakeoff signals:** Check `git log --since="<window_start>" --oneline` for commits that reference bakeoff findings (look for "bakeoff", "coverage", "linker", "framework" in commit messages).

3. **Guidance log quality:** Read the guidance files in `~/hypergumbo_lab_notebook/guidance_log/` from the window period. Are the stop hook recommendations specific and actionable ("fix LOW_CROSS_LANGUAGE_IO_PCT by adding cgo linker edges for Go repos"), or vague and repetitive ("investigate bakeoff warnings")?

## Step 6: BROAD vs. DEEP Balance

Assess whether the right mode is being used:

- **BROAD gap:** If there are known coverage gaps (missing languages, unrecognized frameworks, broken linkers) but recent sessions are all DEEP, the mode selection is wrong.
- **DEEP gap:** If coverage is solid but feature quality is untested (no slice quality assessment, no reverse-slice validation), BROAD sessions are premature optimization.
- **Cadence balance:** Extreme imbalance (e.g., 15 DEEP sessions, 0 BROAD) suggests mode inertia rather than deliberate choice.

## Step 7: Synthesize Findings

Produce a structured assessment with these sections:

```markdown
# Bakeoff Process Health Audit — <date>

## Window
- Period: <start> to <end> (<N> days, expanded <M> times to reach minimum 2 sessions)
- Sessions: <N> total (<B> BROAD, <D> DEEP)

## Velocity
- Sessions per week: <N>
- Repos analyzed: <N> unique, <N> total (including re-runs)
- Cohorts completed: <N>

## Convergence
- Sessions that iterated (iteration > 1): <N> / <total>
- Recurring concerns: <list with frequency>
- Sessions with identical code hash + identical concerns: <N> (wasted runs)

## Reflect Pipeline
- Prompt generation rate: <N>%
- Assessment completion rate: <N>%
- Summary production rate: <N>%
- Bottleneck: <prompts | assessments | summaries | none>

## Signal-to-Action
- Tracker items created from bakeoff signals: <N>
- Tracker items resolved: <N>
- PRs merged from bakeoff findings: <N>
- Guidance log quality: <specific | vague | repetitive>

## Mode Balance
- BROAD sessions: <N>, last: <date>
- DEEP sessions: <N>, last: <date>
- Assessment: <balanced | BROAD-heavy | DEEP-heavy | stale-BROAD | stale-DEEP>

## Health Verdict
<HEALTHY | NEEDS_ATTENTION | UNHEALTHY>

Rationale: <1-3 sentences explaining the verdict>

## Recommended Actions
1. <specific, actionable recommendation>
2. ...
```

## Step 8: Record and Act

1. Save the audit to `~/hypergumbo_lab_notebook/bakeoff_health_audit_<MMDDYYYY_HHMM>.md`.
2. If the verdict is NEEDS_ATTENTION or UNHEALTHY, create tracker items for the recommended actions (kind: `work_item`, status: `todo_soft`, tag: `bakeoff_infrastructure`).
3. If the reflect pipeline completion rate is below 50%, that is the highest-priority fix — bakeoffs without reflects are noise.
