<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Cruft Audit Playbook

A procedure for systematically removing dead text from prompts (AGENTS.md, playbooks, hook summaries) without trimming the content that's actually doing work. Cruft accumulates over time as transitions happen ("we used to X, now we Y"), workarounds outlive the bugs they worked around, and session-anchored references point at sessions readers can't access. The hard part is distinguishing dead text from prose that anchors a rule, supplies rationale, or teaches a hard-to-articulate skill.

## When to run

- **Periodic, ~quarterly.** Cruft accumulates silently — a calendar trigger surfaces it.
- **After a transition lands** that deprecates a workaround (e.g., a structural fix replacing a hand-cleanup procedure). Search the playbooks for "until X lands" / "until that ships" referring to the now-shipped X.
- **When a prompt feels stale.** Subjective signal but real — when reading a playbook produces "wait, is this still right?" more than once, audit it.
- **When the human says "is there cruft we could remove?"** Common request after a season of fast change.

NOT a substitute for the conceptual-leak audit (`what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit`). Cruft audit asks "is this text dead?". Concept audit asks "is this category coherent?". Different question, different mode.

## The methodology

Two complementary passes, **both** mediated by interactive interview with the human:

### Pass 1: Syntactic grep

Cheap, surfaces obvious candidates:

```bash
# History markers
grep -rn -E "\b(deprecated|previously|no longer|originally|formerly|legacy|obsolete)\b" \
    .agent/agent_playbooks_protocols_sops_skills/ AGENTS.md

# Temporal qualifiers (require word boundaries — many real "now"s exist in prose)
grep -rn -E "\bnow\b" .agent/agent_playbooks_protocols_sops_skills/ AGENTS.md

# Session-anchored references
grep -rn -E "this (session|PR|investigation|run)|earlier today" \
    .agent/agent_playbooks_protocols_sops_skills/ AGENTS.md

# Stale "until X" workarounds
grep -rn -E "until [^.]*(ships|lands|merges|fixes)" \
    .agent/agent_playbooks_protocols_sops_skills/ AGENTS.md

# FIXMEs / TODOs in prompt text
grep -rn -E "\b(FIXME|TODO|XXX)\b" .agent/agent_playbooks_protocols_sops_skills/ AGENTS.md
```

Verify referenced things still exist / are still in their claimed state:
- Files referenced (`docs/X.md`, `scripts/Y`) — `test -f`
- Tracker items cited as "until <ID> lands" — `scripts/tracker show <ID>` to confirm status
- Configuration knobs and worker module paths — confirm they exist and are still referenced from the code

### Pass 2: Semantic read

What the grep won't catch — overexplanation, dead anecdotes, hypothetical cases that no longer happen, fallbacks for files that now always exist. Read each playbook section and ask:

1. **Reachability.** In this repo's actual configuration, is this branch / option / fallback ever entered?
2. **Recoverable referent.** When the prose mentions "this session" / "the user said" / "earlier", does the reader have the content (quote, link, extracted pattern), or is it self-citation pointing at a session they can't access?
3. **Teaching content.** Does the prose extract a recoverable pattern shape, supply rationale, or describe a hard-to-articulate generative skill? Or is it pure historical record of a specific past event?
4. **Rule already encoded.** Does the surrounding concrete rule already deliver the lesson this prose is restating?

### Critical: interactive interview mediates both passes

Neither pass alone produces accurate verdicts. The same word is sometimes cruft and sometimes load-bearing — `(deprecated)` next to a tracker status is anchoring a term that still appears in live data; `(deprecated)` next to a feature that's been removed entirely is dead text. You cannot tell from the regex hit alone.

**Do not auto-apply syntactic-pass hits.** Surface them as candidates with full context (file path, line range, **5 lines of surrounding context**, why-flagged tag) and have the human classify each.

## Calibration loop

The taxonomy below is the synthesis of one such session. The session itself was a calibration loop:

1. **Round 1: 5-7 candidates, no opinion from auditor.** The human classifies (`cruft` / `not cruft` / `explain`) and supplies the reasoning when classifying. The auditor updates its mental model.
2. **Round 2: 5-7 candidates, with auditor opinion + reasoning.** The human confirms or corrects. Auditor refines.
3. **Round 3 (optional): 5 more, with opinions.** If the hit rate is high (≥4/5 confirmed), proceed.
4. **Autonomous pass.** Auditor produces the full report applying the calibrated taxonomy.

Two procedural notes from the session:

- **Show 5 lines of surrounding context, not just the candidate.** A flagged sentence in isolation reads ambiguously; in context the verdict is often clear.
- **Read every word in the flagged area, not just the snippet.** In one round the auditor flagged a workaround for a fixed bug but missed the session-anchored prefix sitting two lines above the flagged content.
- **One candidate at a time during calibration.** Batches of 6 overwhelmed the human reviewer; one-at-a-time was the workable cadence.

## The calibrated taxonomy

### Cruft (delete)

- **Stale `until X lands` workarounds** where X has shipped. Verify by tracker lookup or by checking whether the bug being worked around can still be reproduced.
- **Workarounds for problems the tooling now handles automatically.** The skill being taught lives inside the code; the prose is redundant scaffolding.
- **Session-anchored references with no recoverable referent.** "The user's repeated pushback this session was a signal — every new item costs queue-management overhead." Drop the prefix; keep the lesson.
- **Pure historical records of specific past events with no extracted teaching shape.** "ADR-0025 and ADR-0026 were originally filed as ADRs in error and have been reclassified" — the renamed artifacts are referenced just above, the rule lives in another doc, the NOTE is pure history.

### Trim (one-word / short-phrase removal, surrounding content fine)

- **Stale temporal qualifiers** ("now", "currently", "still", "recently") that presuppose reader memory of a transition.
  - Example: "(`cycle` now includes reflect automatically; use `--skip-reflect` for fast iteration only.)" — drop "now"; the rest is current API affordance.
- **Session-anchor prefixes/parentheticals** when the surrounding sentence already extracts the recoverable pattern.
  - Example: "The most expensive mistake of this session was a hand-rolled `KNOWN_LANGS` set that omitted `jsonnet` and `rst`, producing 3,000+ false-flag invalid-language nodes." — the recoverable shape (KNOWN_LANGS / jsonnet+rst / 3,000+) is the teaching; the "of this session" qualifier is dead. Replace with de-anchored framing.
- **Truism reminders** that are redundant with concrete surrounding rules.
  - Example: a "Diminishing returns are real." sentence inside a budget rule that already says "good enough after 3 rounds, stop at 20 minutes."
  - Test: removing the truism, would the reader lose information? If no, trim. Truisms ARE valuable in isolation — they are trim candidates only when the concrete encoding is already present.

### Not cruft (keep)

- **Worked-example anchors** that survive future state changes. "first UAT, do not modify" remains accurate even after subsequent UAT campaigns ship.
- **Rationale or consequences beyond the rule.** "The full transcript lives on disk; you can search it freely; you never have to re-run the command" — repeats the rule reductively, but supplies *why* it matters and what an agent gains.
- **Concrete-situation illustrations.** Heuristic checklists where every bullet resolves to the same action are doing pattern-recognition work, not redundancy. "Will the output fit on one screen? If no, **redirect to a file**." The bullets prime the agent to recognize entry-shapes.
- **Live-data anchors.** Terms that still appear in extant tracker items, archives, or code — even when the term is "deprecated" in policy. The reader will encounter the term and need a referent.
- **Generative/teaching prose** for hard-to-articulate skills. "If `docs/blind-spots.md` does not yet exist, take 5 minutes to consider what the new frame *almost* assumes — what edge cases or alternative shapes the new structure makes harder to express." Even when the file currently exists, the fallback teaches *how* to do the activity de novo.
- **Fallbacks** for templatization / fork / fresh-clone scenarios — reachable under plausible future configurations.
- **Borderline cases default to keep.** When cost is small (one phrase) and the case could go either way, the default is keep. The audit revisits next cycle.

### Doc consistency issues (separate category — only flag when likely to derail)

A numbering mismatch (overview says "seven phases", body has eight) is a doc bug, not cruft. Only flag if (a) the inconsistency would meaningfully derail an agent reading the doc AND (b) the fix has bounded ripple effects. Cost-benefit usually doesn't favor making it a finding.

## What this audit does NOT cover

- **Conceptual leaks** — single field smuggling unrelated information. That's the fundamental-concept-audit.
- **Stale rules in code/tooling** that the prose accurately describes — those are tooling work items, not prompt cruft.
- **Coverage gaps** in the playbook system — that's a different audit ("are we missing a playbook for X?").

## Output format

Produce an audit report (do not auto-apply). The report file lives at `~/hypergumbo_lab_notebook/cruft_audit_<MMDDYYYY_HHMM>.md`. Structure:

1. **Summary.** Coverage scope, count by verdict (cruft / trim / borderline / consistency).
2. **Methodology.** Brief restatement of taxonomy applied.
3. **Findings.** Per-finding sections with:
   - File path and line range
   - Current text (verbatim, with surrounding context)
   - Why this is the verdict it got
   - Action (delete / specific replacement / no change)
   - "After" text where useful
4. **Items considered and rejected.** Representative not-cruft cases with the reasoning. Transparency about why these did NOT make the report — protects against over-trimming on future runs.
5. **Aggregate scale.** Total lines audited / lines changed.
6. **Apply / hold.** The human decides whether to apply.

When the human approves application, ship as a single PR touching only the playbooks involved. `.agent/**` is governance, so a governance approval is required for the apply step (the audit itself is read-only and does not need governance approval).

## Anti-patterns

- **Auto-applying syntactic-pass hits.** Same word is sometimes cruft, sometimes load-bearing. The grep surfaces candidates; only the interactive interview produces verdicts.
- **Skipping the calibration rounds.** Going straight to autonomous mode produces over-aggressive trimming. The first session used six examples to discover that "could be terser" is the wrong bar.
- **Reductive logical interpretation.** "These four bullets all resolve to the same action — they're redundant." In a prompt, redundancy that anchors pattern recognition is doing work the reductive read misses.
- **Trimming worked examples to abstract them.** "Specific repo names, dates, and PR numbers will rot" — true, but they're also what makes the rule recognizable. Strip the session anchor that points at unrecoverable context; keep the concrete shape.
- **Failing to verify referenced tracker IDs / file paths.** "Until WI-X lands" might be cruft if WI-X is closed, or load-bearing if it's open. You can't tell without checking.
