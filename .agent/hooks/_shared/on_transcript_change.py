#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transcript change hook — two-step LLM pipeline that identifies which
playbooks/SOPs are relevant to the agent's current goals and injects
their full content back into the session.

Step 1: Send recent transcript entries to a distillation model
        (Small 3.2 via OpenRouter) to distill the agent's current goals.
Step 2: Send goals + playbook summaries to a reasoning model
        (Small 2603 via OpenRouter) to select 0-3 relevant playbooks.
Step 3: Read and output the full content of every selected playbook.

stdout is injected back into the agent's conversation as context.

Requires: OPENROUTER_API_KEY environment variable.

Configuration (environment variables):
  OPENROUTER_API_KEY        — required
  TRANSCRIPT_DISTILL_MODEL  — model for goal distillation (default: mistralai/mistral-small-3.2-24b-instruct)
  TRANSCRIPT_SELECT_MODEL   — model for playbook selection with reasoning (default: mistralai/mistral-small-2603)
  TRANSCRIPT_MAX_TOKENS     — token budget for transcript window (default: 4000)
  TRANSCRIPT_DEDUP_TOKENS   — suppress re-injection within this many tokens (default: 50000)
"""

import datetime
import json
import os
import re
import sys
import urllib.request
import urllib.error
import uuid

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DISTILL_MODEL = os.environ.get("TRANSCRIPT_DISTILL_MODEL", "mistralai/mistral-small-3.2-24b-instruct")
SELECT_MODEL = os.environ.get("TRANSCRIPT_SELECT_MODEL", "mistralai/mistral-small-2603")
MAX_TOKENS = int(os.environ.get("TRANSCRIPT_MAX_TOKENS", "4000"))
DEDUP_TOKENS = int(os.environ.get("TRANSCRIPT_DEDUP_TOKENS", "50000"))
CHARS_PER_TOKEN = 4.4

# Training data collection: log LLM inputs/outputs for future local model finetuning.
# Set TRANSCRIPT_TRAINING_LOG to a path to enable. Default: .agent/.training-data.jsonl
TRAINING_LOG = os.environ.get("TRANSCRIPT_TRAINING_LOG", "")

# Parse outcome sidecar: dormant since migration from parse_ratings to
# parse_selection.  parse_selection uses exact ID matching, making parse
# misses structurally impossible.  Kept for backward compatibility.
PARSE_OUTCOME_LOG = os.environ.get("TRANSCRIPT_PARSE_OUTCOME_LOG", "")

# Playbook registry: (id, path relative to repo root, one-line summary)
# These match the files in .agent/agent_playbooks_protocols_sops_skills/.
PLAYBOOKS = [
    ("experiment-design-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/experiment-design-playbook.md",
     "Always run a 1-repo mini trial before full experiments to validate setup and estimate "
     "runtime. If extrapolated single-command wall-clock time exceeds 8 hours, document "
     "the design in the lab notebook instead of running it. Do not draw conclusions from "
     "mini-trials — they are only for smoke testing and ballpark timing."),
    ("bakeoff-broad-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-broad-priorities.md",
     "BROAD mode priority queue and script reference for coverage-breadth bakeoffs. "
     "Priority order: reflect on results, aggregate across sessions, linkers, frameworks. "
     "Includes pipeline overlap guidance (reflect agents can run concurrently with the next "
     "cohort's run), batch workflow commands, and what to do when blocked."),
    ("bakeoff-deep-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-deep-priorities.md",
     "DEEP mode priority queue and script reference for feature-usefulness bakeoffs. "
     "Priority order: reflect, aggregate, slice quality, reverse slice, supply chain tiers, "
     "centrality, linkers. Includes session comparison (bakeoff-deep compare), introspection "
     "subcommands (status, active), and curriculum-based cohort selection."),
    ("bakeoff-artifacts-guide",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-artifacts-guide.md",
     "Bakeoff artifacts are stored in ~/hypergumbo_lab_notebook/bakeoff_artifacts/ as "
     "timestamped session directories (broad-* or deep-*). Sessions are auto-discovered by "
     "latest timestamp and never overwritten. Env var overrides available. Each session "
     "contains state.json, cohorts/, out/, diag/, and reflect/ subdirectories."),
    ("coverage-and-test-placement",
     ".agent/agent_playbooks_protocols_sops_skills/coverage-and-test-placement.md",
     "100% test coverage is required — no exceptions. Tests must live in the same package as "
     "the code they cover because CI tests each package in isolation. Subprocess tests do not "
     "contribute to coverage. Run check-package-coverage before pushing. Embedding-dependent "
     "code uses a separate .coveragerc when sentence-transformers is unavailable."),
    ("structural-fix-scope-expansion-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/structural-fix-scope-expansion-protocol.md",
     "When fixing bugs, assume they are structural: name the violated invariant, check for "
     "analogues across languages/constructs/pipeline stages. Create tracker items immediately "
     "(violated, todo_hard, todo_soft, or needs_human_review). Distinguish root-cause fixes "
     "from workarounds. When in doubt, use todo_hard — the circuit breaker prevents death "
     "spirals."),
    ("smart-test-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/smart-test-playbook.md",
     "Always use the pytest alias (which invokes smart-test) instead of python -m pytest or "
     "direct pytest. Smart-test provides a compact ~20-line summary, saves full output to "
     ".ci/pytest-output.log, and runs only tests affected by changed files. Commit "
     ".ci/affected-tests.txt with every PR for CI smart test selection."),
    ("pre-work-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-work-playbook.md",
     "Checklist before starting any new feature: verify no auto-pr is in flight "
     "(PR_PENDING gate), flush queued vPRs if remote is available, sync dev and main "
     "branches, review the spec and changelog for current progress, then create a feature "
     "branch with the naming convention author/[feat|fix|docs|refactor]/description."),
    ("recover-state-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook.md",
     "After context compaction, recover state from last_stop_check.json which records: "
     "current branch, last PR number/state, pending hard/soft TODOs, free-text notes, "
     "active bakeoff session path. Also check guidance_file for recent stop hook output "
     "and run tracker ready for pending work items. Keep notes fresh after key milestones."),
    ("pre-commit-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-commit-playbook.md",
     "Before every commit: verify git identity (user.name/user.email), run tests with "
     "100% coverage (pytest -n auto --cov-fail-under=100), update CHANGELOG.md and spec "
     "status indicators if feature status changed, check tracker for open items if fixing "
     "a bakeoff signal, then commit with sign-off (git commit -s)."),
    ("vpr-usage",
     ".agent/agent_playbooks_protocols_sops_skills/vpr-usage.md",
     "When the remote is unavailable, auto-pr queues virtual PRs (vPRs) in .git/PR_QUEUE. "
     "vPRs form a linear chain; flush pushes all as a single atomic PR. Commands: auto-pr "
     "list (show queue), auto-pr status (queue state and next steps), auto-pr flush (push "
     "all). To add changes while queue is non-empty, branch from the queue tip."),
    ("release-workflow",
     ".agent/agent_playbooks_protocols_sops_skills/release-workflow.md",
     "Two-step release workflow: agent runs prepare-release VERSION (bumps version, updates "
     "changelog, runs release-check, creates dev-to-main PR). Human then merges the PR and "
     "runs tag-release VERSION to create a GPG-signed tag and push it, triggering the "
     "release CI workflow. Separation ensures branch protection and human authorization."),
    ("ci-debug-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/ci-debug-protocol.md",
     "When CI fails but tests pass locally, use ci-debug runs/status/analyze-deps. Four CI "
     "workflows: ci.yml (per-PR smart-test), full-suite (every 4 hours, all packages), "
     "nightly (multi-Python matrix + integration tests), release (on tag push). Common root "
     "causes: missing pyproject.toml dependencies, version mismatches, platform differences. "
     "Never use pytest.skip() to hide failures."),
    ("optional-dependency-testing-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/optional-dependency-testing-playbook.md",
     "For PyPI-available tree-sitter grammars: add to pyproject.toml, write real tests, no "
     "mocking. For build-from-source grammars (built via scripts/build-source-grammars): "
     "write real tests that call the analyzer directly, plus a mock test only for the "
     "unavailability code path. Never use pytest.mark.skipif as an escape hatch."),
    ("changelog-audit-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/changelog-audit-playbook.md",
     "Two-phase audit of the [Unreleased] section of CHANGELOG.md. Phase 1 (completeness): "
     "compare against git log from the last release tag to HEAD, chunking the log to avoid "
     "context overload; identify merged work missing from the changelog — all commit types "
     "matter (features, fixes, CI, tests, refactors, docs, infra). Phase 2 (organization): "
     "calibrate detail level against recent released sections — Unreleased should match their "
     "granularity, not exceed it. Completeness is valued but verbosity is not completeness; "
     "one concise bullet per feature beats a multi-paragraph breakdown. Merge duplicate entries, "
     "normalize granularity toward feature-level descriptions, group related items, reorder by "
     "significance. Never remove information; concision means fewer words, not less content. "
     "Budget: max 3 rounds of organization edits."),

    ("agentic-session-retrospective",
     ".agent/agent_playbooks_protocols_sops_skills/agentic-session-retrospective.md",
     "Structured post-hoc analysis of an agent's decision-making during an autonomous session. "
     "Evaluates how the agent decided what to build — not what it built. Five phases: (1) read "
     ".agent/.last_session_transcript.jsonl (rotated on session start, vendor-agnostic), "
     "(2) reconstruct the decision "
     "sequence as a timeline with branching points, (3) analyze infrastructure interactions — "
     "stop hook steering, CI/merge overhead, tracker task selection, playbook injection relevance, "
     "AGENTS.md compliance, bakeoff integration, (4) quantify time/token allocation across feature "
     "work, CI overhead, research, compliance, error recovery, and idle time, (5) synthesize "
     "findings as structured proposals (what happened, impact, root cause, proposed improvement, "
     "category) and record in the lab notebook. Creates tracker items for actionable improvements. "
     "Time box: 30-60 minutes total."),

    ("bakeoff-process-health-audit",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-process-health-audit-playbook.md",
     "Meta-assessment of the bakeoff feedback loop: session convergence trends, reflect "
     "pipeline completion rates (prompts→assessments→summaries), signal-to-action flow "
     "(are findings becoming tracker items and PRs?), recurring concern detection, and "
     "BROAD/DEEP mode balance. Uses a sliding time window (1 week, expanding by 1 week "
     "until at least 2 sessions are found). Produces a structured health verdict."),

    ("self-analysis-dogfooding",
     ".agent/agent_playbooks_protocols_sops_skills/self-analysis-dogfooding-playbook.md",
     "Run hypergumbo on its own codebase to validate Python analysis quality and catch "
     "regressions. Covers: generating baseline artifacts (run, sketch, io-boundaries, "
     "symbols, routes), inspecting behavior map sanity (orphan rate, key symbols, IO "
     "boundary categories), validating slices and explain output against known architecture, "
     "diffing against prior baselines to detect regressions. Useful before refactoring shared "
     "modules, after changing analyzers or linkers, and when investigating bakeoff signals. "
     "Does not substitute for bakeoff on diverse repos."),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def openrouter_distill(prompt: str, max_completion_tokens: int = 1024) -> str:
    """Call Small 3.2 for goal distillation (no reasoning)."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return ""

    payload = json.dumps({
        "model": DISTILL_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_completion_tokens,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
        return ""


def openrouter_select(prompt: str, max_completion_tokens: int = 9144) -> str:
    """Call 2603 with reasoning for playbook selection."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return ""

    payload = json.dumps({
        "model": SELECT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_completion_tokens,
        "reasoning": {"enabled": True},
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
        return ""


def _truncate_to_budget(prompt: str, response: str, max_tokens: int) -> tuple[str, str]:
    """Truncate the longest field from the front so total fits within max_tokens.

    The step-1 prompt can contain the full transcript window plus framing text,
    easily exceeding the token budget.  Truncating from the *beginning* of the
    longest field preserves the most recent (most relevant) content.
    """
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    total = len(prompt) + len(response)
    if total <= max_chars:
        return prompt, response

    excess = total - max_chars
    # Truncate whichever field is longest, from the front
    if len(prompt) >= len(response):
        prompt = prompt[excess:]
    else:
        response = response[excess:]
    return prompt, response


def log_training_example(
    repo_root: str, step: str, prompt: str, response: str,
    model: str,
    extra: dict | None = None,
) -> None:
    """Append a ChatML training example to the training log.

    Each line is a JSON object with ``messages`` in the format expected by
    HuggingFace SFTTrainer / Unsloth for Qwen2.5 ChatML finetuning::

        {"step": "goal_distillation", "messages": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]}

    The ``step`` field is metadata (not part of the chat) so training scripts
    can filter or weight the two tasks independently.  Entries exceeding
    MAX_TOKENS are truncated from the front of the longest field.

    *extra* is an optional dict of additional metadata fields merged into the
    top-level JSON object (e.g. ``{"parse_misses": ["id1", "id2"]}``).
    """
    prompt, response = _truncate_to_budget(prompt, response, MAX_TOKENS)
    log_path = TRAINING_LOG
    if not log_path:
        log_path = os.path.join(repo_root, ".agent", ".training-data.jsonl")
    obj = {
        "timestamp": datetime.datetime.now().isoformat(),
        "step": step,
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response},
        ],
    }
    if extra:
        obj.update(extra)
    entry = json.dumps(obj, ensure_ascii=False)
    try:
        with open(log_path, "a") as f:
            f.write(entry + "\n")
    except OSError:
        pass  # Best-effort — don't break the pipeline for logging failures


def log_parse_outcome(
    repo_root: str, event_id: str, parse_misses: list[str],
) -> None:
    """Append a parse-outcome record to the sidecar log.

    Each line is a JSON object with the *event_id* (shared with the
    corresponding training-data entry) and the list of playbook IDs whose
    scores could not be extracted from the LLM response.  Only called when
    *parse_misses* is non-empty.
    """
    log_path = PARSE_OUTCOME_LOG
    if not log_path:
        log_path = os.path.join(repo_root, ".agent", ".parse-outcomes.jsonl")
    entry = json.dumps({
        "event_id": event_id,
        "parse_misses": parse_misses,
    }, ensure_ascii=False)
    try:
        with open(log_path, "a") as f:
            f.write(entry + "\n")
    except OSError:
        pass  # Best-effort


def select_recent_entries(transcript_path: str) -> str:
    """Read the latest entries from the transcript within the token budget."""
    if not os.path.exists(transcript_path):
        return ""

    max_chars = int(MAX_TOKENS * CHARS_PER_TOKEN)

    with open(transcript_path, "rb") as f:
        lines = f.readlines()

    selected: list[bytes] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) > max_chars:
            break
        selected.append(line)
        total += len(line)

    selected.reverse()
    return b"".join(selected).decode("utf-8", errors="replace")


def parse_selection(text: str) -> list[str]:
    """Extract playbook IDs mentioned in the sparse selection response."""
    selected = []
    for pb_id, _, _ in PLAYBOOKS:
        if pb_id in text.lower() or pb_id.replace("-", " ") in text.lower():
            selected.append(pb_id)
    if re.search(r'\bnone\b', text.lower()) and not selected:
        return []
    return selected


def read_playbook(repo_root: str, rel_path: str) -> str:
    """Read a playbook file and return its content."""
    full_path = os.path.join(repo_root, rel_path)
    if os.path.exists(full_path):
        with open(full_path) as f:
            return f.read().strip()
    return ""


INJECTION_STATE_FILENAME = ".transcript-injection-state.json"
SESSION_TOKEN_FILENAME = ".transcript-session-token"


def _state_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".agent", INJECTION_STATE_FILENAME)


def _read_session_token(repo_root: str) -> str:
    """Read the current session token (written by sync-transcript.sh on start)."""
    path = os.path.join(repo_root, ".agent", SESSION_TOKEN_FILENAME)
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _empty_state(repo_root: str) -> dict:
    """Return a fresh injection state tagged with the current session token."""
    return {
        "session_token": _read_session_token(repo_root),
        "injections": {},
        "last_compact_offset": 0,
    }


def load_injection_state(repo_root: str) -> dict:
    """Load injection tracking state.

    Returns empty state if the state file is missing, corrupt, or belongs
    to a different session (stale token).  This prevents cross-session
    byte offsets from poisoning the dedup logic.

    State format:
    {
        "session_token": "<token>",
        "injections": {"pb_id": <byte_offset_at_injection_time>, ...},
        "last_compact_offset": <byte_offset_of_last_compact_boundary>
    }
    """
    path = _state_path(repo_root)
    if os.path.exists(path):
        try:
            with open(path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return _empty_state(repo_root)

        # Validate session token — stale state from a prior session is
        # meaningless because byte offsets reference a different transcript.
        current_token = _read_session_token(repo_root)
        if current_token and state.get("session_token") != current_token:
            return _empty_state(repo_root)

        return state
    return _empty_state(repo_root)


def save_injection_state(repo_root: str, state: dict) -> None:
    """Persist injection tracking state atomically.

    Embeds the current session token so future reads can detect staleness.
    """
    state["session_token"] = _read_session_token(repo_root)
    path = _state_path(repo_root)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def find_last_compact_offset(transcript_path: str) -> int:
    """Find the byte offset of the last compact_boundary event in the transcript.

    Returns 0 if no compaction has occurred.
    """
    if not os.path.exists(transcript_path):
        return 0

    last_offset = 0
    offset = 0
    with open(transcript_path, "rb") as f:
        for line in f:
            if b'"compact_boundary"' in line:
                last_offset = offset
            offset += len(line)
    return last_offset


def recently_injected(
    transcript_path: str,
    playbook_ids: list[str],
    repo_root: str,
) -> tuple[set[str], dict]:
    """Determine which playbooks should be skipped due to recent injection.

    Uses a state file to track when each playbook was injected (by byte offset
    in the transcript). Invalidates injections that occurred before the most
    recent compact_boundary event, since the LLM no longer has that context.

    Returns (set of pb_ids to skip, updated state dict).
    """
    state = load_injection_state(repo_root)
    injections = state.get("injections", {})
    prev_compact = state.get("last_compact_offset", 0)

    # Find current compaction boundary
    current_compact = find_last_compact_offset(transcript_path)

    # If a new compaction occurred, invalidate all injections from before it
    if current_compact > prev_compact:
        injections = {
            pid: offset for pid, offset in injections.items()
            if offset > current_compact
        }
        state["last_compact_offset"] = current_compact

    # Also apply a token-based window: even without compaction, don't suppress
    # forever. If injection happened more than DEDUP_TOKENS ago, allow re-inject.
    current_size = os.path.getsize(transcript_path) if os.path.exists(transcript_path) else 0
    dedup_chars = int(DEDUP_TOKENS * CHARS_PER_TOKEN)

    still_valid: dict[str, int] = {}
    for pid, offset in injections.items():
        if current_size - offset <= dedup_chars:
            still_valid[pid] = offset

    state["injections"] = still_valid

    found = {pid for pid in playbook_ids if pid in still_valid}
    return found, state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or dry_run
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    transcript_path = args[0] if args else ""

    if not transcript_path or not os.path.exists(transcript_path):
        if verbose:
            print(f"[dry-run] No transcript at: {transcript_path!r}", file=sys.stderr)
        sys.exit(0)

    if not dry_run and not os.environ.get("OPENROUTER_API_KEY"):
        if verbose:
            print("[dry-run] OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(0)

    # Determine repo root (this script lives at .agent/hooks/_shared/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", "..", ".."))

    # Step 0a: Check if all playbooks are recently injected (skip LLM calls entirely)
    all_ids = [pb_id for pb_id, _, _ in PLAYBOOKS]
    already, inj_state = recently_injected(transcript_path, all_ids, repo_root)
    if len(already) == len(PLAYBOOKS):
        if verbose:
            print("[step 0] All playbooks recently injected — skipping LLM calls",
                  file=sys.stderr)
        sys.exit(0)

    # Step 0b: Select recent entries within token budget
    recent = select_recent_entries(transcript_path)
    if not recent:
        if verbose:
            print("[dry-run] No entries selected from transcript", file=sys.stderr)
        sys.exit(0)

    if verbose:
        approx_tokens = len(recent) / CHARS_PER_TOKEN
        print(f"[step 0] Selected {len(recent):,} chars (~{approx_tokens:,.0f} tokens) "
              f"from {transcript_path}", file=sys.stderr)

    # Step 1: Distill agent goals
    step1_prompt = (
        "Below are the latest turns in an agentic coding session. "
        "Please distill what the agent's present goals are.\n\n"
        + recent
    )

    if verbose:
        print(f"[step 1] Goal distillation prompt: {len(step1_prompt):,} chars", file=sys.stderr)

    if dry_run:
        print(f"[dry-run] Step 1 prompt ({len(step1_prompt):,} chars):", file=sys.stderr)
        print(step1_prompt[:1000], file=sys.stderr)
        if len(step1_prompt) > 1000:
            print(f"... ({len(step1_prompt) - 1000:,} more chars)", file=sys.stderr)
        print(file=sys.stderr)
        agent_goals = "(DRY RUN — goals would be distilled by LLM)"
    else:
        agent_goals = openrouter_distill(step1_prompt)
        if not agent_goals:
            if verbose:
                print("[step 1] LLM returned empty response", file=sys.stderr)
            sys.exit(0)
        log_training_example(
            repo_root, "goal_distillation", step1_prompt, agent_goals,
            model=DISTILL_MODEL,
        )

    if verbose:
        print(f"[step 1] Agent goals: {agent_goals[:200]}", file=sys.stderr)

    # Step 2: Sparse playbook selection
    playbook_list = "\n".join(
        f"- {pb_id}: {summary}"
        for pb_id, _, summary in PLAYBOOKS
    )
    step2_prompt = (
        f"An agentic coder has the following goal:\n\n{agent_goals}\n\n"
        f"Below are {len(PLAYBOOKS)} guidance documents. Select ONLY documents that address "
        "something the agent is concretely about to do right now, based on the "
        "goal above. Do NOT select documents that are merely \"generally useful.\" "
        "If the goal does not clearly indicate a specific activity that a document "
        "covers, say \"none\". Selecting 0 is the correct answer most of the time.\n\n"
        "Reply with ONLY the document names (0 to 3), one per line.\n\n"
        + playbook_list
    )

    if verbose:
        print(f"[step 2] Sparse selection prompt: {len(step2_prompt):,} chars", file=sys.stderr)

    if dry_run:
        print(f"[dry-run] Step 2 prompt ({len(step2_prompt):,} chars):", file=sys.stderr)
        print(step2_prompt, file=sys.stderr)
        print(file=sys.stderr)
        # In dry-run mode, show which playbooks exist on disk
        print("[dry-run] Playbook file status:", file=sys.stderr)
        for pb_id, pb_path, _ in PLAYBOOKS:
            full = os.path.join(repo_root, pb_path)
            status = "EXISTS" if os.path.exists(full) else "MISSING"
            print(f"  {status}: {pb_path}", file=sys.stderr)
        sys.exit(0)

    selection_text = openrouter_select(step2_prompt)
    if not selection_text:
        if verbose:
            print("[step 2] LLM returned empty response", file=sys.stderr)
        sys.exit(0)
    event_id = str(uuid.uuid4())
    log_training_example(
        repo_root, "sparse_selection", step2_prompt, selection_text,
        model=SELECT_MODEL,
        extra={"event_id": event_id},
    )

    if verbose:
        print(f"[step 2] Raw selection:\n{selection_text}", file=sys.stderr)

    selected = parse_selection(selection_text)

    if verbose:
        print(f"[step 2] Selected playbooks: {selected}", file=sys.stderr)

    # Step 3: Collect selected playbooks, skipping recently injected ones
    # (Reuse the dedup state computed in step 0a — compaction/token-distance
    # boundaries haven't changed since then.)

    if verbose:
        compact_offset = inj_state.get("last_compact_offset", 0)
        if compact_offset > 0:
            print(f"[step 3] Last compaction at byte offset {compact_offset}", file=sys.stderr)
        if already:
            print(f"[step 3] Recently injected (still in context): "
                  f"{', '.join(sorted(already))}", file=sys.stderr)

    relevant = []
    skipped = []
    for pb_id, pb_path, pb_summary in PLAYBOOKS:
        if pb_id not in selected:
            continue
        if pb_id in already:
            skipped.append(pb_id)
            continue
        content = read_playbook(repo_root, pb_path)
        if content:
            relevant.append((pb_id, content))
        elif verbose:
            print(f"[step 3] {pb_id} selected but file missing: {pb_path}",
                  file=sys.stderr)

    if verbose and skipped:
        print(f"[step 3] Skipped (recently injected): "
              f"{', '.join(sorted(skipped))}", file=sys.stderr)

    if not relevant:
        if verbose:
            print(f"[step 3] No playbooks to inject "
                  f"({len(skipped)} deduped)", file=sys.stderr)
        # Save state even if nothing to inject (compaction tracking still matters)
        if not dry_run:
            save_injection_state(repo_root, inj_state)
        sys.exit(0)

    # Record injection offsets before outputting
    current_size = (os.path.getsize(transcript_path)
                    if os.path.exists(transcript_path) else 0)
    for pb_id, content in relevant:
        inj_state["injections"][pb_id] = current_size

    if not dry_run:
        save_injection_state(repo_root, inj_state)

    # Output: injected into the agent's conversation
    print(f"[Transcript Analysis — {len(relevant)} relevant playbook(s)]")
    if dry_run:
        print(f"Agent goals: {agent_goals}")
    print()
    for pb_id, content in relevant:
        print(f"--- {pb_id} ---")
        print(content)
        print()


if __name__ == "__main__":
    main()
