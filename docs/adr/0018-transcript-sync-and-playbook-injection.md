<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0018: Vendor-Agnostic Transcript Sync and LLM-Driven Playbook Injection

Date: 2026-03-29 (amended 2026-04-08)
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

**Stage 2: LLM-driven sparse selection.** When the filtered transcript grows, a hook calls `on_transcript_change.py`, which:
1. Selects the most recent entries within a token budget (default 4K tokens)
2. Sends them to Small 3.2 via OpenRouter to distill the agent's current goals
3. Sends the goals + the playbook summaries (currently 19) to Small 2603 with reasoning enabled, asking it to select 0-3 relevant playbooks
4. Reads and outputs the full content of every selected playbook

**Stage 3: Context injection.** The hook's stdout is injected back into the agent's conversation via the AI tool's native hook system. The mechanism varies by tool (see §3 below).

### Vendor-specific feedback hooks

| Tool | Hook event | Trigger cadence | Injection mechanism |
|------|-----------|----------------|-------------------|
| Claude Code | `PostToolUse` | After each tool call | `hookSpecificOutput.additionalContext` in JSON |
| Codex CLI | `PostToolUse` | After each tool call | `additionalContext` in JSON |
| Gemini CLI | `BeforeModel` | Before every LLM API call | Appends message to `llm_request.messages` |
| Cursor | `stop` | At task completion | `followup_message` (auto-submits as next user prompt) |
| Cursor | `afterAgentResponse` | After each assistant message | `additional_context` (documented, awaiting bug fix) |
| Cursor | `postToolUse` | After each tool call | `additional_context` (documented, awaiting bug fix) |

**Claude Code `FileChanged` bug (v2.1.83–v2.1.87+):** The original design used `FileChanged` for event-driven injection in Claude Code. However, `FileChanged` hooks do not fire as of v2.1.87 (2026-03-29): the configuration is parsed and displayed by `/hooks`, but the underlying file watcher never triggers the hook command. This affects all `FileChanged` matchers, confirmed with a trivial canary test. The workaround uses `PostToolUse` with `poll-transcript-change.sh`, which checks whether the filtered transcript has grown since the last poll (one `stat()` call, <1ms when nothing changed). When Claude Code fixes `FileChanged`, revert to the event-driven configuration for lower overhead.

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
│                                # Owns transcript + sidecar rotation + archive
├── filter-transcript.py        # Incremental noise filter
├── launch-transcript-sync.sh   # Shared: kill stale watcher (PID file + pgrep), launch new
├── kill-transcript-sync.sh     # Shared: SIGTERM via PID file with pgrep DEST-scoped fallback
├── poll-transcript-change.sh   # Size-based polling for tools without FileChanged (or where it's broken)
├── on_transcript_change.sh     # Shell wrapper → Python
├── on_transcript_change.py     # Two-step LLM pipeline + log_injection_history sidecar writer
└── test-transcript-pipeline.sh # Dry-run test harness

.agent/hooks/{claude-code,codex-cli,gemini-cli,cursor}/
├── session-start.sh            # Discovers transcript, launches watcher
├── session-end.sh              # Kills watcher
└── post-tool-use-transcript.sh # Tool-specific feedback hook adapters
                                # (wraps poll output in vendor JSON format)

.agent/agent_playbooks_protocols_sops_skills/
├── *.md                        # 19 extracted playbook files

Config files:
├── .claude/settings.json       # PostToolUse polling hook (FileChanged broken as of v2.1.87)
├── .codex/hooks.json           # PostToolUse hook
├── .gemini/settings.json       # BeforeModel hook
└── .cursor/hooks.json          # stop + afterAgentResponse + postToolUse
```

### Per-session state invariant and session tokens

**Naming convention:** Any file in `.agent/` matching `.transcript-*` is per-session transient state. On session start, `sync-transcript.sh` clears all matching files with `rm -f .agent/.transcript-*` — a glob-based reset that automatically covers new state files without requiring manual registration.

**Session token (defense in depth):** `sync-transcript.sh` writes a random token to `.agent/.transcript-session-token` on startup. Every state file writer (`save_injection_state`, `save_state`, `poll-transcript-change.sh`) embeds the current token. Every state file reader checks the embedded token against the current token file — mismatches cause the state to be treated as empty. This catches stale state even if the glob reset was skipped (e.g., watcher not launched, session-start hook timed out).

**Why both layers:** The glob reset handles the normal path. The session token handles edge cases where state files are created outside the watcher (e.g., by `on_transcript_change.py`) or where the watcher launch fails. Neither layer alone covers all failure modes.

> **Amendment (2026-04-08, Option 2 — per-session isolation):** The
> single-global-slot rotation model documented in this section was
> superseded after the 2026-04-08 watcher-leak lifecycle test. The
> table below describes the *original* design; see the
> "Per-session isolation amendment" section further down for the
> current pipeline shape, which keys the live transcript and
> injection-history sidecar by `session_id`, performs rotation at
> session END instead of session START, and serializes concurrent
> end events with `flock`.

**Invariant test (original):** `TestSessionResetInvariant` verified that every known `.transcript-*` file matched the glob pattern and that persistent files did not. This test has been replaced by `TestPerSessionNamingInvariants`, which verifies that per-session stems can be keyed by `session_id` without colliding with global slot filenames.

Per-session and persistent state files (original design):

| File | Per-session? | Cleared on start? |
|------|---|---|
| `.current_session_transcript.jsonl` | Yes | Yes (explicit rm, after rotation) |
| `.transcript-sync.pid` | Yes | Yes (glob) |
| `.transcript-sync-state.json` | Yes | Yes (glob) |
| `.transcript-poll-state` | Yes | Yes (glob) |
| `.transcript-injection-state.json` | Yes | Yes (glob + token) |
| `.transcript-session-token` | Yes | Yes (glob, then rewritten) |
| `.last_session_transcript.jsonl` | Rotated | No — `.current` → `.last` on session start |
| `.second_to_last_transcript.jsonl` | Rotated | No — `.last` → `.second_to_last`; `.second_to_last` archived to `.archived-transcripts/<UTC-stamp>/transcript.jsonl.gz` before being clobbered |
| `.current_injection_history.jsonl` | Rotated | No — deliberately uses a different prefix than `.transcript-*` so the glob does NOT touch it. Rotated parallel to the transcript |
| `.last_injection_history.jsonl` | Rotated | No — `.current_injection_history` → `.last_injection_history` on session start |
| `.second_to_last_injection_history.jsonl` | Rotated | No — `.last_injection_history` → `.second_to_last_injection_history`; archived to `.archived-transcripts/<UTC-stamp>/injection_history.jsonl.gz` before being clobbered |
| `.archived-transcripts/<UTC-stamp>/{transcript,injection_history}.jsonl.gz` | Persistent | No — gzipped pair per archived session, mtime preserved via `touch -r` |
| `.training-data.jsonl` | No | No (accumulates for finetuning) |
| `.parse-outcomes.jsonl` | No | No (accumulates; sidecar for parse failures) |

The injection-history sidecar (`*_injection_history.jsonl`) is the durable record of every playbook injection event. It exists because Claude Code's `additionalContext` mechanism (and equivalent vendor mechanisms) inject the hook's stdout into the API request without writing it back into the session JSONL. Without the sidecar, the `agentic-session-retrospective` playbook's Phase 2d question — "which playbooks were injected and were they relevant?" — is structurally unanswerable. The writer is `log_injection_history` in `on_transcript_change.py`; it fires from both the success path and the zero-injection early-exit path so precision/recall analysis sees both signal and noise. Each record contains the distilled goal, the selected/injected/skipped-dedup playbook IDs, and the model identifiers. Records are append-only JSON-per-line; under the per-session amendment they are rotated at session END (not start) by `rotate-on-session-end.sh`.

## Per-session isolation amendment (2026-04-08, Option 2)

### Problem the original design did not handle

The original rotation model assumed exactly one Claude Code (or Codex / Gemini) session per repo at any time. Two concurrent sessions in the same repo collided in three ways:

1. Both watchers wrote to the same `.current_session_transcript.jsonl` DEST, racing on every filter pass.
2. Both used a single global `.agent/.transcript-sync.pid`; whichever wrote last clobbered the other's PID.
3. The orphan-cleanup pgrep scoped by `DEST` argument could not distinguish a stale orphan from a live sibling, so session B's startup unconditionally killed session A's still-live watcher (the bug observed in the 2026-04-08 manual lifecycle test, Step 5).

Beyond the watcher leak, the rotation slots themselves were undefined under concurrency: "previous session" has no meaning if two sessions are alive at once.

### Decision

Each session writes to its own per-session current files, keyed by a sanitized `session_id` derived from the vendor's hook input:

| Per-session file | Path |
|---|---|
| Filtered transcript | `.agent/.current_session_transcript.<session_id>.jsonl` |
| Injection-history sidecar | `.agent/.current_injection_history.<session_id>.jsonl` |
| Watcher PID file | `.agent/.transcript-sync.<session_id>.pid` |
| Filter offset state | `.agent/.transcript-sync-state.<session_id>.json` |
| Poll state | `.agent/.transcript-poll-state.<session_id>` |
| Dedup / injection state | `.agent/.transcript-injection-state.<session_id>.json` |

The global `.last_session_transcript.jsonl`, `.second_to_last_transcript.jsonl`, `.last_injection_history.jsonl`, and `.second_to_last_injection_history.jsonl` slots are preserved with redefined semantics: they are written at **session END**, not session start, by `rotate-on-session-end.sh`. `.last_*` now means **"most recently *ended* session in this repo,"** with concurrent end events serialized by an exclusive `flock` on `.agent/.rotation.lock`.

### Lifecycle

* **Session start.** The vendor hook extracts a `session_id` from its native input source via `session_id_helpers.sh` (Claude Code: `.session_id` UUID; Codex: basename of `transcript_path`; Gemini: `GEMINI_SESSION_ID` env var or `transcript_path` basename; Cursor: hardcoded `cursor-singleton`). The hook calls `launch-transcript-sync.sh <SRC> <SESSION_ID>`, which:
  1. Runs a one-time legacy cleanup (kills any pre-amendment global watcher and removes legacy state files).
  2. Walks `.agent/.transcript-sync.*.pid`. Files whose recorded PID is dead are *crashed sessions*; their orphaned `.current_session_transcript.<sid>.jsonl` and `.current_injection_history.<sid>.jsonl` are archived directly into `.agent/.archived-transcripts/crashed-<UTC-stamp>-<sid>/` (skipping `.last_*` — a crashed session must not claim "most recently ended"), and the stale state files are removed. Files whose recorded PID is alive are *live siblings* and are left strictly alone.
  3. Launches `sync-transcript.sh <SRC> <DEST> <SESSION_ID>` in the background. The watcher writes the per-session DEST and PID file and never participates in rotation.
* **Session end.** The vendor hook calls `kill-transcript-sync.sh <REPO_ROOT> <SESSION_ID>`, which kills only the watcher whose per-session PID file or pgrep `argv[+3]` matches `SESSION_ID`. It then calls `rotate-on-session-end.sh <REPO_ROOT> <SESSION_ID>`, which acquires the `.rotation.lock` flock, archives the existing `.second_to_last_*` pair into a timestamped subdir, demotes `.last_*` → `.second_to_last_*`, promotes the per-session `.current_*.<sid>.*` → `.last_*`, and cleans up the per-session state files for `<sid>`. Concurrent end events serialize via flock; last writer wins the `.last_*` slot.
* **Polling.** `poll-transcript-change.sh <SESSION_ID>` reads the per-session DEST and the per-session poll state (`.transcript-poll-state.<sid>`).
* **Dedup / injection state.** `on_transcript_change.py`'s `recently_injected()`, `load_injection_state()`, `save_injection_state()`, and `log_injection_history()` all take a `session_id` argument; the state and sidecar paths embed it.

### What goes away

* The `.transcript-*` glob session reset in `sync-transcript.sh` is removed. Per-session paths make a glob reset unnecessary by construction.
* The global `.transcript-session-token` mechanism (and `_read_session_token` in both `filter-transcript.py` and `on_transcript_change.py`) is removed. The per-session DEST is now the authoritative session identity at every layer.
* The pgrep-by-DEST orphan cleanup in `kill-transcript-sync.sh` is replaced by pgrep-by-`SESSION_ID` (matched at `argv[+3]` from the `sync-transcript.sh` script-name field), so kill events cannot reach sibling sessions even when the per-session PID file is missing.

### Cursor exemption

Cursor's transcript backing store is a single global SQLite database (`state.vscdb`) shared by all Cursor windows in all workspaces. Per-session isolation requires a SQLite-aware extractor that fans out to per-conversation files, which is deferred (tracker `WI-rijoj`). Until that work lands, Cursor is enforced single-session-per-repo: `cursor/session-start.sh` checks for a live `cursor-singleton` watcher via the per-session PID file and aborts the transcript-sync wiring with a clear stderr message if one is found. The Cursor session itself still launches normally — only the transcript-sync side is gated.

### Migration

The first `launch-transcript-sync.sh` invocation in any repo after this amendment lands runs a one-time legacy cleanup that:

* Reads the legacy `.agent/.transcript-sync.pid`, kills its PID if alive, and removes the file.
* `pgrep`s for any 2-positional-arg `sync-transcript.sh` process whose DEST matches the legacy global path and kills it.
* Removes legacy global state files: `.transcript-sync-state.json`, `.transcript-poll-state`, `.transcript-injection-state.json`, `.transcript-session-token`, and the legacy `.current_session_transcript.jsonl`.

After the first post-amendment session starts, all legacy state is gone and the per-session pipeline takes over.

### New invariants

* Every per-session file's filename embeds `<session_id>`. `TestPerSessionNamingInvariants` enforces this convention: per-session stems and global slot filenames cannot collide.
* `kill-transcript-sync.sh` matches by `SESSION_ID` only. `TestKillScript.test_kill_skips_sibling_session_in_same_repo` enforces this — the structural fix for the watcher-leak bug.
* `launch-transcript-sync.sh`'s orphan sweep distinguishes crashed sessions from live siblings by checking whether the recorded PID is alive. `TestCrashedSessionOrphan` enforces both directions.
* `rotate-on-session-end.sh` is the *only* writer of `.last_*` and `.second_to_last_*`. `TestRotateOnSessionEnd` covers the rotation chain, mtime preservation, empty-current handling, and concurrent serialization via flock.

### Files changed (summary)

`launch-transcript-sync.sh`, `sync-transcript.sh`, `kill-transcript-sync.sh`, `poll-transcript-change.sh`, `filter-transcript.py`, `on_transcript_change.py`, `rotate-on-session-end.sh` (new), `session_id_helpers.sh` (new), 4 vendor `session-start.sh`, 4 vendor `session-end.sh`, 5 vendor polling hooks, `tests/test_watcher_lifecycle.py`, `tests/test_transcript_pipeline_properties.py`, `agentic-session-retrospective.md`, `AGENTS.md`, `.gitignore`, `CHANGELOG.md`.

## Consequences

### Benefits
- **Context efficiency**: AGENTS.md shrinks from ~660 to ~180 lines. Playbooks are loaded on-demand, only when relevant.
- **Accuracy**: A second LLM rates relevance based on what the agent is *actually doing*, not what it might do.
- **Vendor neutrality**: The filtered transcript and downstream hook are tool-agnostic. Only transcript discovery and feedback injection are vendor-specific.
- **Noise reduction**: 83% fewer lines in the filtered transcript means 83% fewer unnecessary hook invocations and API calls.
- **Compaction awareness**: The dedup system understands when the LLM's context has been compressed and allows re-injection of playbooks that were likely lost.

### Costs
- **External LLM dependency**: The sparse selection requires an OpenRouter API key and network access. If unavailable, the hook exits silently (no playbooks injected, but no errors either).
- **Latency**: Two LLM calls per invocation add latency. Step 1 uses Small 3.2 (fast distillation); step 2 uses Small 2603 with reasoning enabled (longer timeout, ~854 reasoning tokens observed). The token budget is halved (4K vs 8K), partially offsetting the cost of the reasoning model.
- **State files**: Gitignored runtime files (filtered transcript, PID file, filter state, poll state, injection state, session token, rotated transcript pair, injection-history sidecar pair, archived-transcripts subdirs).
- **Vendor hook bugs**: Claude Code's `FileChanged` hook does not fire (v2.1.83–v2.1.87+), requiring `PostToolUse` polling as a workaround. Two of three Cursor feedback hooks are non-functional (regressions since v2.0.64). Both workarounds add per-tool-call overhead (one `stat()` call) but no meaningful latency.
- **Heuristic dedup**: The compaction + token-distance dedup is a heuristic. It's possible to re-inject a playbook the LLM still has (wasted tokens) or fail to re-inject one it lost (missed context). The heuristic errs on the side of re-injection.

### Configuration

All parameters are environment variables with sensible defaults:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | (required) | API authentication |
| `TRANSCRIPT_DISTILL_MODEL` | `mistralai/mistral-small-3.2-24b-instruct` | LLM for goal distillation (step 1) |
| `TRANSCRIPT_SELECT_MODEL` | `mistralai/mistral-small-2603` | LLM for playbook selection with reasoning (step 2) |
| `TRANSCRIPT_MAX_TOKENS` | `4000` | Token budget for transcript window |
| `TRANSCRIPT_DEDUP_TOKENS` | `100000` | Token distance before allowing re-injection. Bumped from 50000 (April 2026) after observing that long sessions outgrew the prior ~220KB window within 10–15 turns and thrashed re-injecting the same playbooks |
| `TRANSCRIPT_TRAINING_LOG` | `.agent/.training-data.jsonl` | Path for finetuning data collection (ChatML JSONL) |
| `TRANSCRIPT_PARSE_OUTCOME_LOG` | `.agent/.parse-outcomes.jsonl` | Path for parse-outcome sidecar (dormant — parse_selection has no parse failures) |

### Training data collection for local model replacement

The pipeline depends on external LLMs (Small 3.2 and Small 2603 via OpenRouter), which conflicts with hypergumbo's local-first philosophy. To enable eventual replacement with a finetuned local model, the pipeline logs every successful LLM input/output pair to `.agent/.training-data.jsonl` (gitignored).

Each line is a JSON object in the ChatML format expected by HuggingFace SFTTrainer / Unsloth:

```json
{
  "step": "goal_distillation",
  "event_id": "a1b2c3d4-...",
  "model": "mistralai/mistral-small-3.2-24b-instruct",
  "messages": [
    {"role": "user", "content": "<prompt>"},
    {"role": "assistant", "content": "<response>"}
  ]
}
```

The `step` field (`goal_distillation` or `sparse_selection`) allows filtering or weighting the two tasks independently during training. The `model` field tracks which model produced the response (step 1 uses `DISTILL_MODEL`, step 2 uses `SELECT_MODEL`), so data quality can be assessed if the upstream models change. The `event_id` (UUID v4) is present on `sparse_selection` entries and serves as a join key to the parse-outcome sidecar (see below).

**Target local model:** Qwen2.5-0.5B-Instruct (Apache-2.0, 0.5B parameters). At this size, full finetuning (not LoRA/QLoRA) is feasible on consumer hardware (~4-6 GB VRAM). Inference at runtime would use llama.cpp or llama-cpp-python, eliminating the OpenRouter dependency entirely. Note: the new pipeline relies on reasoning for the selection step, which a 0.5B model cannot do. The distillation step remains a candidate for local replacement.

Logging is best-effort (OSError silently caught) and only fires on non-dry-run successful responses, so it never interferes with the pipeline. The log path is configurable via the `TRANSCRIPT_TRAINING_LOG` environment variable.

### Parse-outcome sidecar (dormant)

**This subsystem is dormant** since the migration from `parse_ratings` (regex-based score extraction) to `parse_selection` (exact playbook ID matching). The new parser checks whether each known playbook ID appears in the LLM response text — a playbook is either mentioned or not, making parse misses structurally impossible.

The sidecar infrastructure (`log_parse_outcome`, `TRANSCRIPT_PARSE_OUTCOME_LOG`) is retained for backward compatibility with existing `.parse-outcomes.jsonl` files and in case future changes reintroduce fragile parsing. The `log_parse_outcome` function is no longer called in normal operation.

### G-Vendi-guided data selection and finetuning pipeline

Rather than finetuning on all collected examples indiscriminately, we apply the G-Vendi diversity measure (arXiv:2505.20161) to select a maximally diverse training subset. G-Vendi quantifies diversity via the entropy of model-induced loss gradients, achieving Spearman's ρ ≈ 0.9 correlation with OOD generalization — far stronger than surface-level metrics like embedding similarity or n-gram entropy. Notably, the paper uses Qwen2.5-0.5B-Instruct as its gradient proxy model — the same model we finetune.

The pipeline is implemented in `scripts/finetune-transcript-model` with three subcommands:

**Phase 1: `select` — G-Vendi data selection**
1. Load collected training examples from `.agent/.training-data.jsonl`
2. For each example, forward+backward through Qwen2.5-0.5B-Instruct to compute loss gradients
3. Project gradients to d=1024 dimensions via CountSketch (a JL-preserving projection that avoids materializing the full ~500M-dimensional gradient vector — O(|θ|) time and O(max\_param\_tensor + d) memory per sample)
4. Compute the Vendi Score: eigenvalue entropy of the normalized kernel matrix K = GG^T/N
5. K-means cluster in gradient space; select samples preferring sparse clusters (underrepresented gradient regions — the Prismatic Synthesis insight from §3 of the paper)
6. Inject task-specific system prompts (`goal_distillation` vs `relevance_rating`) and write the selected subset to `.agent/.training-data-selected.jsonl`

**Phase 2: `train` — Full finetune**
- Full finetune of Qwen2.5-0.5B-Instruct (HuggingFace weights, ~1GB float16) using HuggingFace TRL's SFTTrainer
- 10% held-out evaluation split with early stopping on eval loss
- Fits in ~4-6 GB VRAM (or CPU RAM) — no gradient checkpointing or LoRA needed at this model size
- Saves the finetuned model to `.agent/finetuned-model/`

**Phase 3: `quantize` — GGUF conversion**
- Converts the finetuned HuggingFace model to F16 GGUF via llama.cpp's `convert_hf_to_gguf.py`
- Quantizes to IQ4_XS (~350 MB) via `llama-quantize`
- Optional importance matrix (`--imatrix`) for higher quality quantization
- Outputs `.agent/transcript-model.gguf`

```
# Full pipeline
./scripts/finetune-transcript-model select
./scripts/finetune-transcript-model train
./scripts/finetune-transcript-model quantize
```

Dependencies (`torch`, `transformers`, `trl`, `datasets`, `scikit-learn`) are NOT part of the hypergumbo install — they must be installed in a separate venv. The script gates all heavy imports behind dependency checks so `--help` always works.

### Local inference: build and runtime requirements

CPU inference performance depends critically on two settings that are **not** enabled by default:

1. **`llama-cpp-python` must be built from source with native CPU optimization.** PyPI does not publish pre-built wheels for this package — `pip install llama-cpp-python` always builds from source via scikit-build-core. However, the default cmake build targets a generic x86-64 instruction set (SSE3). To use the host CPU's SIMD instructions (AVX2, AVX-512, VNNI), rebuild with:

   ```bash
   CMAKE_ARGS="-DGGML_NATIVE=ON" pip install llama-cpp-python --force-reinstall --no-binary llama-cpp-python
   ```

   Verify the runtime detects the correct features by checking the `CPU :` line in verbose model loading output. On a Zen 4 CPU (e.g., Ryzen 8700G), expect to see `AVX = 1 | AVX2 = 1 | AVX512 = 1 | AVX512_VNNI = 1`.

2. **Flash attention must be enabled at model load time** by passing `flash_attn=True` to the `Llama()` constructor. Without it, self-attention scales O(n²) with context length; with it, prefill throughput is roughly constant across context sizes. The `llm-gguf` plugin does not currently pass this flag; the `llm-llama-server` plugin gets it via llama-server's auto default, but for embedded Python inference, `llama-cpp-python` must be called directly with `flash_attn=True`.

3. **Virtualized environments** (Proxmox, QEMU/KVM, etc.) must expose the host CPU's instruction set to the guest. The default Proxmox CPU type (`x86-64-v2-AES`) hides AVX/AVX-512 for live-migration compatibility. Change to `host` type in the VM's processor settings if the machine is single-node.

**Measured impact** (Qwen2.5-0.5B-Instruct-IQ4_XS, 6-core Ryzen 8700G, CPU-only):

| Config | 8K input prefill | Extrapolated 16K pipeline |
|--------|---:|---:|
| Generic build, no flash attn | 577s | ~50 min |
| Generic build, flash attn | 150s | ~5.8 min |
| Native build, flash attn | **17s** | **~45s** |

The benchmarks used `llama-cpp-python` directly (not the `llm` CLI), specifically to pass `flash_attn=True`. A complete benchmark script is at `/tmp/bench-q25.py` in the development environment.

### Future work
- **Local model deployment**: Once the finetuned GGUF is produced, replace `openrouter_chat()` in `on_transcript_change.py` with local llama.cpp inference via `llama-cpp-python`. Must pass `flash_attn=True` to the `Llama()` constructor.
- **Prismatic augmentation**: Use the sparse-cluster signal from G-Vendi selection to guide generation of additional synthetic training examples targeting underrepresented gradient regions (full Prismatic Synthesis loop from arXiv:2505.20161 §3).
- **Streaming filter**: Currently reads all new bytes on each invocation. Could use a tail-follow approach for lower latency on very active sessions.
- **Multi-tool compaction detection**: Only Claude Code's `compact_boundary` is currently detected. Codex and Gemini may have analogous signals.
- **Playbook auto-discovery**: Currently uses a hardcoded registry. Could scan the playbooks directory and generate summaries automatically.
- **Cost tracking**: Log OpenRouter token usage per session to monitor the cost of the relevance-rating calls.
- **Claude Code `FileChanged` revert**: When Anthropic fixes the `FileChanged` hook bug, revert `.claude/settings.json` from `PostToolUse` polling back to event-driven `FileChanged` on `.current_session_transcript.jsonl`. The original config is preserved in the lab notebook (`filechanged_hook_issue.md`).
- **Read-then-injected overlap signal**: Track cases where the agent explicitly read a playbook via the `Read` tool and then the same playbook was injected by the pipeline (or vice versa). This is pure waste — captured in the injection-history sidecar but not yet measured. A simple post-hoc analysis script could compute the overlap rate per session.

## Amendments

### 2026-04-08 — Watcher leak fix and retrospective blindness fix

Two distinct production gaps were found while auditing the pipeline against this ADR and fixed in a single PR:

**1. Watcher leak.** Thirteen `sync-transcript.sh` processes had accumulated since Apr 5, all writing to the same shared destination. Three structural bugs combined to produce the leak:

- `kill-transcript-sync.sh` had no fallback when the PID file was missing — silent leaks were unkillable.
- `launch-transcript-sync.sh` only consulted the PID file, never scanned by process name, so each new session started a fresh watcher even when stale ones were running.
- `sync-transcript.sh`'s EXIT trap removed the PID file unconditionally, racing with the next session's PID write.

Fixes: `kill-transcript-sync.sh` now has a two-phase cleanup (PID file path + `pgrep` fallback scoped to the repo's expected DEST argument); `launch-transcript-sync.sh` delegates to the kill script before launching; `sync-transcript.sh`'s cleanup trap is now conditional on `cat "$PID_FILE" == "$$"`. Also fixed: the rotation/glob-reset block was deleting the PID file written immediately before it (reordered so the rm runs first), and the `do_sync` calls now have `|| true` because the function's last command doubles as a return-value test that interacts badly with `set -euo pipefail` when the filter drops every new line as noise.

The lifecycle is gated by `tests/test_watcher_lifecycle.py`, which spawns real subprocesses through the actual shell scripts and asserts the kill/launch/EXIT-trap invariants.

**2. Retrospective blindness.** Claude Code's `additionalContext` mechanism injects the hook's stdout into the API request without writing it back into the session JSONL, so the rotated transcript files contained zero record of which playbooks were injected. The `agentic-session-retrospective` Phase 2d question — "which playbooks were injected and were they relevant?" — was structurally unanswerable.

Fix: a new `log_injection_history()` writer in `on_transcript_change.py` appends a JSON record per LLM poll to `.agent/.current_injection_history.jsonl`, capturing the distilled goal, the selected playbooks, the playbooks that actually reached the agent (after dedup), the playbooks that were skipped by dedup, and the model identifiers. The sidecar rotates parallel to the transcript pair — `.current → .last → .second_to_last` — and `sync-transcript.sh` archives the about-to-be-clobbered `.second_to_last_*` pair into `.agent/.archived-transcripts/<UTC-stamp>/{transcript,injection_history}.jsonl.gz` with `gzip -c` and `touch -r` to preserve the original session-end mtime. Sidecar files use a different prefix (`*_injection_history.jsonl`) than the per-session glob (`.transcript-*`) so they survive the session reset by construction; this is gated by an extension to `TestSessionResetInvariant`.

The writer fires from both the success path AND the zero-injection early-exit path so precision/recall analysis sees both signal and noise. It is best-effort (`OSError`-suppressed) following the same pattern as `log_training_example`. Tests live in `tests/test_transcript_pipeline_properties.py::TestInjectionHistory` and `::TestSidecarRotation`.

**3. Dedup window bump.** `TRANSCRIPT_DEDUP_TOKENS` default raised from 50,000 to 100,000. At 4.4 chars/token, the prior 220KB window meant long sessions outgrew it within 10–15 turns and re-injected the same playbooks. The new ~440KB window is still a heuristic, but trades CPU-cheap suppression for a meaningful reduction in re-injection thrash.
