<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Agentic Session Retrospective Playbook

Structured post-hoc analysis of an agent's decision-making during an autonomous session. The goal is not to evaluate what was built (that's what bakeoff reflect does), but to evaluate *how the agent decided what to build* and how the autonomous infrastructure (AGENTS.md, hooks, scripts, playbooks) helped or hindered those decisions.

**When to run:** After an autonomous session ends, when a human wants to understand what happened and identify infrastructure improvements. This is a human-initiated activity, not an autonomous-mode task.

**Output:** A lab notebook entry (`~/hypergumbo_lab_notebook/session_retrospective_<MMDDYYYY_HHMM>.md`) with structured observations, proposed improvements, and optionally tracker items for the most actionable findings.

### Phase 0: Locate the Session Transcript

The transcript sync pipeline (ADR-0018) rotates normalized transcripts on each session start. There are TWO parallel rotation chains: the transcript itself and the injection-history sidecar (the latter is what makes Phase 2d answerable — see below).

| File | Contents |
|------|----------|
| `.agent/.current_session_transcript.jsonl` | Live session (being written to now) |
| `.agent/.last_session_transcript.jsonl` | Previous session — **this is what you typically want** |
| `.agent/.second_to_last_transcript.jsonl` | Two sessions ago (for comparison) |
| `.agent/.current_injection_history.jsonl` | Live session's playbook injection events |
| `.agent/.last_injection_history.jsonl` | Previous session's injection events — paired with `.last_session_transcript.jsonl` |
| `.agent/.second_to_last_injection_history.jsonl` | Two sessions ago — paired with `.second_to_last_transcript.jsonl` |
| `.agent/.archived-transcripts/<UTC-stamp>/transcript.jsonl.gz` | Older sessions, gzipped — paired with `injection_history.jsonl.gz` in the same dir |
| `.agent/.archived-transcripts/<UTC-stamp>/injection_history.jsonl.gz` | Older sessions' injection events |

All transcript files are vendor-agnostic JSONL produced by the sync pipeline. Vendor differences (Claude Code, Codex CLI, Gemini CLI, Cursor) are handled upstream by the per-vendor transcript sync adapters — the retrospective consumes only the normalized output. The injection-history sidecar is written by `on_transcript_change.py` regardless of vendor.

The archive directory's subdirectories are named with the UTC rotation timestamp in ISO 8601 basic format (`%Y%m%dT%H%M%SZ`). Each gzipped file's mtime is preserved from the source so `ls -la .agent/.archived-transcripts/*/` shows the original session-end time.

```bash
# Typical usage: analyze the previous session
TRANSCRIPT=".agent/.last_session_transcript.jsonl"
INJECTION_HISTORY=".agent/.last_injection_history.jsonl"
wc -l "$TRANSCRIPT"   # how big is it?
head -5 "$TRANSCRIPT"  # confirm start time
tail -5 "$TRANSCRIPT"  # confirm end time
wc -l "$INJECTION_HISTORY"  # how many injection events?
```

For multi-session aggregation (e.g., precision audits across a week), iterate the archive:

```bash
# Concatenate all archived injection histories into a single stream
for d in .agent/.archived-transcripts/*/; do
    zcat "$d/injection_history.jsonl.gz" 2>/dev/null
done | jq -s 'length'  # total events across the archive
```

If the file doesn't exist (e.g., the sync watcher wasn't running), fall back to vendor-specific transcript locations. For Claude Code: `~/.claude/projects/-home-*-hypergumbo/<session-uuid>/`. For Codex CLI: `~/.codex/sessions/`. For Gemini CLI: `~/.gemini/sessions/`. For Cursor: `.cursor/hooks-output/`. The injection-history sidecar has no vendor fallback — if it's missing, Phase 2d cannot be answered for that session.

**Sanity check:** Read the first and last 5 lines to confirm the time range covers the session of interest. If the transcript is very large (>50K lines), work in chunks — note line ranges as you go.

### Phase 1: Reconstruct the Decision Sequence

**Goal:** Build a timeline of what the agent did and why. This is the factual foundation for all subsequent analysis.

**Step 1 — Extract the session arc.** Scan the transcript for:
- User messages (explicit instructions, mode changes, interruptions)
- Tool calls that indicate major decisions (git checkout -b, auto-pr, bakeoff commands, tracker updates)
- Stop hook fires and the guidance they produced
- Context compaction events
- Error recovery sequences

Write a chronological table:

```markdown
| Time | Activity | Decision Point | Outcome |
|------|----------|---------------|---------|
| HH:MM | Description | What the agent chose to do and why | PR merged / test passed / blocked / etc. |
```

**Step 2 — Identify decision branching points.** For each entry in the timeline, ask: "What else could the agent have done here?" These are the interesting moments:
- Agent chose task A over task B — was the priority ordering correct?
- Agent chose to investigate vs. implement — was the information sufficient?
- Agent chose to retry vs. escalate — was the threshold appropriate?
- Agent chose to parallelize vs. serialize — was there a dependency it missed?
- Stop hook fired and the agent followed/ignored guidance — was the guidance good?

**Time box:** Phase 1 should take 10-15 minutes. If the session is very long (4+ hours), focus on the first hour and the last 30 minutes (startup decisions and wind-down decisions tend to be the most revealing).

### Phase 2: Analyze Infrastructure Interactions

**Goal:** Evaluate how the autonomous infrastructure shaped the agent's behavior — for better or worse.

Work through each category below. For each, note specific transcript evidence (timestamps, line numbers, or quoted text).

#### 2a. Stop Hook & Reflection Loop

- How many times did the stop hook fire? What path did it take each time (TODO blocking, cooldown, full reflection)?
- Did the guidance files contain actionable next steps, or were they generic?
- Did the agent follow the guidance, ignore it, or reinterpret it?
- Did the `last_stop_check.json` notes from a prior session successfully steer this session?
- Was the cooldown threshold (30 min) appropriate? Did the agent get stuck in cooldown when it should have done a full reflection, or vice versa?

#### 2b. CI & Merge Pipeline

- How many PRs were created? How many succeeded on the first try?
- What was the CI turnaround time? Did the agent use wait time productively?
- Were there any flaky merges, stale-pending detections, or hung-run retries?
- Did the PR_PENDING gate work correctly (preventing new work during CI)?
- How much context window was consumed by CI polling output?

#### 2c. Tracker & Task Selection

- Did `tracker ready` surface the right work items?
- Did the agent create tracker items when discovering new issues?
- Were tracker status updates timely and accurate?
- Did the priority ordering match what a human would have chosen?

#### 2d. Playbook Injection (ADR-0018)

Read `.agent/.last_injection_history.jsonl`. Each line is one LLM poll, recorded as a JSON object with these fields:

- `timestamp` — when the poll fired
- `session_token` — the session this event belongs to (defends against cross-session contamination)
- `transcript_offset` — byte position in the transcript at poll time
- `event_id` — unique ID per event
- `agent_goals` — the distilled goal the selector LLM was given
- `selected` — playbook IDs the selector picked
- `injected` — playbook IDs that actually reached the agent (`selected` minus dedup-skipped)
- `skipped_dedup` — playbook IDs that were already in the recent context window
- `distill_model`, `select_model` — which models were used

Compute:
- **Total polls** — `wc -l .agent/.last_injection_history.jsonl`
- **Empty-selection rate** — fraction of polls where `selected` is `[]`. High rate (>50%) suggests the selector is correctly recognizing "nothing relevant", but very high (>90%) suggests the polling is wasted CPU.
- **Dedup hit rate** — fraction of polls where `skipped_dedup` is non-empty. Very high (>50%) suggests the dedup window is too small or the selector keeps re-picking the same things.
- **Top-3 most-injected playbooks** — `jq -r '.injected[]' .agent/.last_injection_history.jsonl | sort | uniq -c | sort -rn | head -3`
- **Top-3 most-selected-but-deduped playbooks** — same with `.skipped_dedup[]`. These are the playbooks that fight the dedup loop hardest; consider whether they should be pinned, removed, or have their summaries rewritten.
- **Read-then-injected overlap** — for a sample of injection events, scan the transcript before the event for explicit `Read` tool calls of the same playbook file. If the agent already read the playbook and then it got injected anyway, that's pure waste.
- **Precision estimate** — manually rate a sample of 10 `agent_goals` + `injected` pairs against your sense of relevance (1=clearly relevant, 0=clearly irrelevant, 0.5=marginal). Report a precision number, but treat it as a vibes-based ceiling, not a measurement.

Also ask:
- Were any playbooks **missing** that would have been helpful? Cross-reference with the registry in `.agent/hooks/_shared/on_transcript_change.py:PLAYBOOKS`.
- Did injection **timing** align with need (before the agent needed it, not after)?
- Were the **distilled goals** accurate summaries of what the agent was actually doing? A bad goal distillation poisons every downstream selection.

#### 2e. AGENTS.md Compliance

- Did the agent violate any AGENTS.md rules? (Direct commits to dev, skipping TDD, missing coverage, etc.)
- Were the violations caught by hooks/guardrails, or did they slip through?
- Were there rules the agent followed that actively slowed it down without adding value?

#### 2f. Bakeoff Integration

- If a bakeoff was running, did the agent correctly interleave bakeoff work with feature work?
- Did bakeoff signals correctly steer the agent toward high-value fixes?
- Was the "one thing at a time" rule followed? Did it help or did it cause idle time?

### Phase 3: Quantify Time & Token Allocation

**Goal:** Understand where the agent's budget went. This surfaces systemic inefficiencies that aren't visible from individual decisions.

Estimate (rough is fine — ±20% is good enough):
- **Feature work** (writing code, tests): X%
- **CI/merge overhead** (waiting, retrying, polling): X%
- **Research/exploration** (reading code, exploring options): X%
- **Infrastructure compliance** (reflection, tracker updates, changelog): X%
- **Error recovery** (fixing mistakes, retrying failed operations): X%
- **Idle/blocked** (waiting with nothing to do): X%

Compare against a hypothetical ideal. If CI overhead is 30%, that's a signal to improve auto-pr. If error recovery is 20%, that's a signal to add guardrails.

### Phase 4: Synthesize Findings

**Goal:** Turn observations into actionable improvement proposals.

For each finding, use this template:

```markdown
## Finding N: [Short Title]

**What happened:** [Factual description with transcript evidence]

**Impact:** [Time wasted, tokens consumed, quality affected, or opportunity missed]

**Root cause:** [Why did this happen? Infrastructure gap, governance gap, or agent limitation?]

**Proposed improvement:** [Specific, implementable change to AGENTS.md, hooks, scripts, or playbooks]

**Category:** [GOVERNANCE | HOOKS | SCRIPTS | PLAYBOOKS | AGENTS.md | VENDOR-SPECIFIC]
```

Aim for 5-10 findings per session. Prioritize by impact. Not every finding needs a proposed improvement — some are just observations worth recording.

### Phase 5: Record and Route

**Step 1 — Write the lab notebook entry.**

```bash
TIMESTAMP=$(date +%m%d%Y_%H%M)
cat > ~/hypergumbo_lab_notebook/session_retrospective_${TIMESTAMP}.md <<'TEMPLATE'
# Agentic Session Retrospective: [Session Date/Range]

**Session:** [vendor] [session ID or slug]
**Duration:** [approximate]
**Autonomous mode:** [BROAD/DEEP/OFF, any mode changes]
**PRs merged:** [count]

## Decision Sequence
[Phase 1 timeline table]

## Infrastructure Analysis
[Phase 2 findings by category]

## Time Allocation
[Phase 3 estimates]

## Findings
[Phase 4 structured findings]

## Summary
[2-3 sentence executive summary: what went well, what should change]
TEMPLATE
```

**Step 2 — Create tracker items for actionable improvements.**

Only create tracker items for findings with concrete proposed improvements and clear implementation paths. Use kind `work_item` with status `needs_human_review` (infrastructure changes need human approval per AGENTS.md governance rules). Tag with `retrospective`.

```bash
scripts/tracker add --kind work_item --title "Improvement: [title]" \
  --tier 3 --status needs_human_review --tag retrospective \
  --description "[Finding description and proposed fix]"
```

**Step 3 — Cross-reference with prior retrospectives.**

Check if similar findings appeared in previous retrospectives:
```bash
grep -l "PATTERN" ~/hypergumbo_lab_notebook/session_retrospective_*.md
grep -l "PATTERN" ~/hypergumbo_lab_notebook/agentic_*_analysis_*.md
```

Recurring findings that haven't been addressed are higher priority than novel ones.

### Guard Rails

- **Do not re-litigate technical decisions.** "The agent should have used approach X instead of Y for the feature" is a bakeoff concern, not a retrospective concern. Focus on *process* and *infrastructure*, not *implementation choices*.
- **Do not over-index on a single session.** One session is one data point. Note patterns but don't propose major governance changes based on a single observation. If you see something once, record it. If you see it three times across sessions, propose a fix.
- **Vendor-agnostic framing.** When proposing improvements, consider whether they apply to all supported vendors (Claude Code, Codex CLI, Gemini CLI, Cursor) or are vendor-specific. Vendor-specific improvements should be tagged as such.
- **Time box the whole thing.** A retrospective should take 30-60 minutes. If you're spending more than an hour, you're going too deep. Write down what you have and stop.
