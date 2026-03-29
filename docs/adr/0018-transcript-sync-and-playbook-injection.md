<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0018: Vendor-Agnostic Transcript Sync and LLM-Driven Playbook Injection

Date: 2026-03-29
Status: Accepted

## Context

### The AGENTS.md scaling problem

As hypergumbo's agentic infrastructure grew, AGENTS.md expanded to ~660 lines covering security boundaries, workflow protocols, bakeoff procedures, CI debugging, release processes, and more. Every line is loaded into the AI agent's context at session start, consuming ~15K tokens regardless of whether the agent is doing a bakeoff, fixing a bug, or running a release.

A proposed redesign (see PR #2608) extracts detailed procedures into individual playbook files under `.agent/agent_playbooks_protocols_sops_skills/`, with AGENTS.md retaining inline summaries and "For more explanation, please read..." pointers. This saves context tokens but introduces a new problem: the agent must know *when* to read which playbook.

### The cross-vendor challenge

The project supports four AI coding tools (Claude Code, Codex CLI, Gemini CLI, Cursor), each with different hook systems, transcript storage formats, and context injection mechanisms. Any solution for playbook loading must work across all four, with the vendor-specific parts isolated.

### What "lazy loading" actually requires

Naive lazy loading (agent decides to read a playbook when it thinks it needs one) fails because:
1. The agent doesn't know what it doesn't know — it may not realize a playbook exists for its current task
2. Reading files mid-conversation consumes tool calls and context
3. The agent may be in autonomous mode, where unnecessary file reads slow the feedback loop

A better approach: an external system observes what the agent is doing, determines which playbooks are relevant, and injects their content into the conversation automatically.

## Decision

### Architecture: three-stage pipeline

**Stage 1: Transcript sync with noise filtering.** A background watcher (launched at session start, killed at session end) uses `inotifywait` to monitor the AI tool's native session transcript. On each write, an incremental Python filter processes only new bytes (tracking byte offset between invocations) and appends meaningful lines to `.agent/.current_session_transcript.jsonl`. The filter drops:
- `bash_progress` lines with empty output
- `bash_progress` lines with output identical to the previous one (heartbeat dedup)
- `file-history-snapshot` bookkeeping lines
- Keeps the last `bash_progress` before any non-progress line (preserves final command output)

This achieves ~83% line reduction on real sessions (measured: 110K → 18K lines on a 24-hour autonomous session).

**Stage 2: LLM-driven relevance rating.** When the filtered transcript grows, a hook calls `on_transcript_change.py`, which:
1. Selects the most recent entries within a token budget (default 16K tokens)
2. Sends them to a cheap, fast LLM (mistral-nemo via OpenRouter) to distill the agent's current goals
3. Sends the goals + 14 playbook summaries to the same LLM, asking for 1-10 relevance ratings
4. Reads and outputs the full content of every playbook scoring above threshold (default 7/10)

**Stage 3: Context injection.** The hook's stdout is injected back into the agent's conversation via the AI tool's native hook system. The mechanism varies by tool (see §3 below).

### Vendor-specific feedback hooks

| Tool | Hook event | Trigger cadence | Injection mechanism |
|------|-----------|----------------|-------------------|
| Claude Code | `FileChanged` | Event-driven (file change) | stdout → conversation context |
| Codex CLI | `PostToolUse` | After each tool call | `additionalContext` in JSON |
| Gemini CLI | `BeforeModel` | Before every LLM API call | Appends message to `llm_request.messages` |
| Cursor | `stop` | At task completion | `followup_message` (auto-submits as next user prompt) |
| Cursor | `afterAgentResponse` | After each assistant message | `additional_context` (documented, awaiting bug fix) |
| Cursor | `postToolUse` | After each tool call | `additional_context` (documented, awaiting bug fix) |

Cursor's `additional_context` and `agent_message` fields are non-functional as of March 2026 (confirmed regressions since v2.0.64). The hooks are written to the documented spec and will activate when Cursor fixes them. The `stop` hook with `followup_message` provides a working fallback.

### Compaction-aware injection dedup

To avoid re-injecting a playbook the LLM already has in context, the system tracks injection state in `.agent/.transcript-injection-state.json`:

```json
{
  "injections": {"playbook-id": <byte_offset_at_injection_time>},
  "last_compact_offset": <byte_offset_of_last_compact_boundary>
}
```

Two eviction triggers allow re-injection:
1. **Compaction event**: Claude Code writes `compact_boundary` events (subtype in system messages) to the transcript when context is compressed. If a compaction occurred after a playbook was injected, the injection is invalidated (the LLM may have lost it).
2. **Token distance**: Even without compaction, if the injection was more than `DEDUP_TOKENS` (default 50K) ago in the filtered transcript, it is evicted.

The dedup scans the *filtered* transcript (not the native one), because:
- The filtered transcript contains only meaningful content, making token-distance a better proxy for context window position
- Our own injection markers survive into the filtered transcript (they're not `bash_progress` or noise)
- `compact_boundary` events survive filtering (they're system messages)

### File layout

```
.agent/hooks/_shared/
├── sync-transcript.sh          # Background watcher (inotifywait loop)
├── filter-transcript.py        # Incremental noise filter
├── launch-transcript-sync.sh   # Shared: kill stale watcher, launch new
├── kill-transcript-sync.sh     # Shared: SIGTERM + PID file cleanup
├── poll-transcript-change.sh   # For tools without FileChanged
├── on_transcript_change.sh     # Shell wrapper → Python
├── on_transcript_change.py     # Two-step LLM pipeline
└── test-transcript-pipeline.sh # Dry-run test harness

.agent/hooks/{claude-code,codex-cli,gemini-cli,cursor}/
├── session-start.sh            # Discovers transcript, launches watcher
├── session-end.sh              # Kills watcher
└── *-transcript.sh             # Tool-specific feedback hook adapters

.agent/agent_playbooks_protocols_sops_skills/
├── *.md                        # 14 extracted playbook files

Config files:
├── .claude/settings.json       # FileChanged hook
├── .codex/hooks.json           # PostToolUse hook
├── .gemini/settings.json       # BeforeModel hook
└── .cursor/hooks.json          # stop + afterAgentResponse + postToolUse
```

## Consequences

### Benefits
- **Context efficiency**: AGENTS.md shrinks from ~660 to ~180 lines. Playbooks are loaded on-demand, only when relevant.
- **Accuracy**: A second LLM rates relevance based on what the agent is *actually doing*, not what it might do.
- **Vendor neutrality**: The filtered transcript and downstream hook are tool-agnostic. Only transcript discovery and feedback injection are vendor-specific.
- **Noise reduction**: 83% fewer lines in the filtered transcript means 83% fewer unnecessary hook invocations and API calls.
- **Compaction awareness**: The dedup system understands when the LLM's context has been compressed and allows re-injection of playbooks that were likely lost.

### Costs
- **External LLM dependency**: The relevance rating requires an OpenRouter API key and network access. If unavailable, the hook exits silently (no playbooks injected, but no errors either).
- **Latency**: Two LLM calls per invocation add latency. Mitigated by using a fast, cheap model (mistral-nemo) and by the noise filter suppressing most invocations.
- **State files**: Five gitignored runtime files (filtered transcript, PID file, filter state, poll state, injection state).
- **Cursor limitations**: Two of three Cursor feedback hooks are non-functional due to bugs. The `stop` hook fallback means Cursor gets playbook injection only at task completion, not mid-task.
- **Heuristic dedup**: The compaction + token-distance dedup is a heuristic. It's possible to re-inject a playbook the LLM still has (wasted tokens) or fail to re-inject one it lost (missed context). The heuristic errs on the side of re-injection.

### Configuration

All parameters are environment variables with sensible defaults:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | (required) | API authentication |
| `TRANSCRIPT_MODEL` | `mistralai/mistral-nemo` | LLM for goal distillation and rating |
| `TRANSCRIPT_MAX_TOKENS` | `16000` | Token budget for transcript window |
| `TRANSCRIPT_THRESHOLD` | `7` | Minimum relevance score (1-10) to inject a playbook |
| `TRANSCRIPT_DEDUP_TOKENS` | `50000` | Token distance before allowing re-injection |

### Future work
- **Streaming filter**: Currently reads all new bytes on each invocation. Could use a tail-follow approach for lower latency on very active sessions.
- **Multi-tool compaction detection**: Only Claude Code's `compact_boundary` is currently detected. Codex and Gemini may have analogous signals.
- **Playbook auto-discovery**: Currently uses a hardcoded registry. Could scan the playbooks directory and generate summaries automatically.
- **Cost tracking**: Log OpenRouter token usage per session to monitor the cost of the relevance-rating calls.
