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
| `TRANSCRIPT_TRAINING_LOG` | `.agent/.training-data.jsonl` | Path for finetuning data collection (ChatML JSONL) |

### Training data collection for local model replacement

The pipeline depends on an external LLM (mistral-nemo via OpenRouter), which conflicts with hypergumbo's local-first philosophy. To enable eventual replacement with a finetuned local model, the pipeline logs every successful LLM input/output pair to `.agent/.training-data.jsonl` (gitignored).

Each line is a JSON object in the ChatML format expected by HuggingFace SFTTrainer / Unsloth:

```json
{
  "step": "goal_distillation",
  "model": "mistralai/mistral-nemo",
  "messages": [
    {"role": "user", "content": "<prompt>"},
    {"role": "assistant", "content": "<response>"}
  ]
}
```

The `step` field (`goal_distillation` or `relevance_rating`) allows filtering or weighting the two tasks independently during training. The `model` field tracks which model produced the response, so data quality can be assessed if the upstream model changes.

**Target local model:** Qwen2.5-0.5B-Instruct (Apache-2.0, 0.5B parameters). At this size, full finetuning (not LoRA/QLoRA) is feasible on consumer hardware (~4-6 GB VRAM). Inference at runtime would use llama.cpp or llama-cpp-python, eliminating the OpenRouter dependency entirely.

Logging is best-effort (OSError silently caught) and only fires on non-dry-run successful responses, so it never interferes with the pipeline. The log path is configurable via the `TRANSCRIPT_TRAINING_LOG` environment variable.

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

2. **Flash attention must be enabled at model load time** by passing `flash_attn=True` to the `Llama()` constructor. Without it, self-attention scales O(n²) with context length; with it, prefill throughput is roughly constant across context sizes. The `llm-gguf` plugin (Simon Willison's `llm` package) does not currently pass this flag — local inference code must use `llama-cpp-python` directly.

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
