#!/usr/bin/env python3
"""Transcript change hook — two-step LLM pipeline that identifies which
playbooks/SOPs are relevant to the agent's current goals and injects
their full content back into the session.

Step 1: Send recent transcript entries to mistral-nemo via OpenRouter
        to distill the agent's current goals.
Step 2: Rate each playbook's relevance to those goals (1-10).
Step 3: Read and output the full content of every playbook rated above
        the confidence threshold.

stdout is injected back into the agent's conversation as context.

Requires: OPENROUTER_API_KEY environment variable.

Configuration (environment variables):
  OPENROUTER_API_KEY     — required
  TRANSCRIPT_MODEL       — model to use (default: mistralai/mistral-nemo)
  TRANSCRIPT_MAX_TOKENS  — token budget for transcript window (default: 16000)
  TRANSCRIPT_THRESHOLD   — minimum confidence to include a playbook (default: 7)
  TRANSCRIPT_DEDUP_TOKENS — suppress re-injection within this many tokens (default: 50000)
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("TRANSCRIPT_MODEL", "mistralai/mistral-nemo")
MAX_TOKENS = int(os.environ.get("TRANSCRIPT_MAX_TOKENS", "16000"))
THRESHOLD = int(os.environ.get("TRANSCRIPT_THRESHOLD", "7"))
DEDUP_TOKENS = int(os.environ.get("TRANSCRIPT_DEDUP_TOKENS", "50000"))
CHARS_PER_TOKEN = 4.4

# Playbook registry: (id, path relative to repo root, one-line summary)
# These match the files in .agent/agent_playbooks_protocols_sops_skills/.
PLAYBOOKS = [
    ("experiment-design-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/experiment-design-playbook.md",
     "Mini trial runs before full experiments, 8-hour rule for long commands."),
    ("bakeoff-broad-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-broad-priorities.md",
     "BROAD mode priority queue: reflect, aggregate, linkers, frameworks."),
    ("bakeoff-deep-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-deep-priorities.md",
     "DEEP mode priority queue: reflect, aggregate, slice, tiers, centrality."),
    ("bakeoff-artifacts-guide",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-artifacts-guide.md",
     "Where bakeoff artifacts are stored and how sessions are organized."),
    ("coverage-and-test-placement",
     ".agent/agent_playbooks_protocols_sops_skills/coverage-and-test-placement.md",
     "100% test coverage requirement, per-package isolation, test placement."),
    ("structural-fix-scope-expansion-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/structural-fix-scope-expansion-protocol.md",
     "Assume bugs are structural, name invariants, scope-expand across languages."),
    ("smart-test-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/smart-test-playbook.md",
     "Using pytest/smart-test alias, compact output, affected test selection."),
    ("pre-work-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-work-playbook.md",
     "Pre-work checklist: PR gate, vPR flush, branch sync, spec review."),
    ("recover-state-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook.md",
     "Post-compaction recovery from last_stop_check.json and tracker."),
    ("pre-commit-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-commit-playbook.md",
     "Pre-commit checklist: identity, tests, changelog, tracker, sign-off."),
    ("vpr-usage",
     ".agent/agent_playbooks_protocols_sops_skills/vpr-usage.md",
     "Virtual PR queue for offline resilience when remote is unavailable."),
    ("release-workflow",
     ".agent/agent_playbooks_protocols_sops_skills/release-workflow.md",
     "Two-step release: agent prepares, human signs tag and pushes."),
    ("ci-debug-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/ci-debug-protocol.md",
     "CI debugging with ci-debug script, workflow topology, dependencies."),
    ("optional-dependency-testing-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/optional-dependency-testing-playbook.md",
     "Testing tree-sitter grammars: real tests, mock only unavailability path."),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def openrouter_chat(prompt: str, max_completion_tokens: int = 1024) -> str:
    """Send a single-turn chat to OpenRouter and return the response text."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return ""

    payload = json.dumps({
        "model": MODEL,
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


def parse_ratings(ratings_text: str) -> dict[str, int]:
    """Parse the LLM's ratings response into {playbook_id: score}.

    Preferred format is ``<id>: <score>`` (one per line).
    Falls back to ``<score>/<max> <id>`` for robustness.
    """
    results: dict[str, int] = {}
    for pb_id, _, _ in PLAYBOOKS:
        # Preferred: "experiment-design-playbook: 8" (prompt asks for this)
        # Fallback: "8/10 experiment-design-playbook"
        # No further fallback — greedy digit grab risks matching stray numbers.
        patterns = [
            rf"{re.escape(pb_id)}\s*[:]\s*(\d+)",
            rf"(\d+)\s*/\s*10\s*.*?{re.escape(pb_id)}",
        ]
        for pattern in patterns:
            m = re.search(pattern, ratings_text, re.IGNORECASE)
            if m:
                score = int(m.group(1))
                if 1 <= score <= 10:
                    results[pb_id] = score
                    break
    return results


def read_playbook(repo_root: str, rel_path: str) -> str:
    """Read a playbook file and return its content."""
    full_path = os.path.join(repo_root, rel_path)
    if os.path.exists(full_path):
        with open(full_path) as f:
            return f.read().strip()
    return ""


INJECTION_STATE_FILENAME = ".transcript-injection-state.json"


def _state_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".agent", INJECTION_STATE_FILENAME)


def load_injection_state(repo_root: str) -> dict:
    """Load injection tracking state.

    State format:
    {
        "injections": {"pb_id": <byte_offset_at_injection_time>, ...},
        "last_compact_offset": <byte_offset_of_last_compact_boundary>
    }
    """
    path = _state_path(repo_root)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"injections": {}, "last_compact_offset": 0}


def save_injection_state(repo_root: str, state: dict) -> None:
    """Persist injection tracking state atomically."""
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
        agent_goals = openrouter_chat(step1_prompt)
        if not agent_goals:
            if verbose:
                print("[step 1] LLM returned empty response", file=sys.stderr)
            sys.exit(0)

    if verbose:
        print(f"[step 1] Agent goals: {agent_goals[:200]}", file=sys.stderr)

    # Step 2: Rate playbook relevance
    playbook_list = "\n".join(
        f"{i+1}. {pb_id}: {summary}"
        for i, (pb_id, _, summary) in enumerate(PLAYBOOKS)
    )
    step2_prompt = (
        f"An agentic coder has the following goal:\n\n{agent_goals}\n\n"
        "Below are several SOPs, protocols, or guidance documents that might "
        "be relevant to the agent's goal. For each document, please rate on a "
        "scale of 1 to 10, with 10 being the most confident, how sure you are "
        "that the document would help the agent complete its goal. Reply with "
        "one line per document in exactly this format:\n\n"
        "  <document-name>: <score>\n\n"
        "For example:\n"
        "  watering-succulents: 1\n"
        "  bread-dough-hydration: 10\n"
        "  bicycle-tire-pressure: 2\n"
        "  origami-crane-folding: 9\n"
        "  banjo-tuning-guide: 3\n"
        "  sourdough-starter-care: 8\n"
        "  knitting-cable-stitch: 4\n"
        "  hamster-wheel-sizing: 7\n"
        "  umbrella-repair-manual: 5\n"
        "  vintage-typewriter-ribbon: 6\n\n"
        + playbook_list
    )

    if verbose:
        print(f"[step 2] Relevance rating prompt: {len(step2_prompt):,} chars", file=sys.stderr)

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

    ratings_text = openrouter_chat(step2_prompt)
    if not ratings_text:
        if verbose:
            print("[step 2] LLM returned empty response", file=sys.stderr)
        sys.exit(0)

    if verbose:
        print(f"[step 2] Raw ratings:\n{ratings_text}", file=sys.stderr)

    ratings = parse_ratings(ratings_text)

    if verbose:
        print(f"[step 2] Parsed ratings: {ratings}", file=sys.stderr)

    # Step 3: Collect playbooks above threshold, skipping recently injected ones
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
        score = ratings.get(pb_id, 0)
        if score >= THRESHOLD:
            if pb_id in already:
                skipped.append((pb_id, score))
                continue
            content = read_playbook(repo_root, pb_path)
            if content:
                relevant.append((pb_id, score, content))
            elif verbose:
                print(f"[step 3] {pb_id} scored {score} but file missing: {pb_path}",
                      file=sys.stderr)

    if verbose and skipped:
        print(f"[step 3] Skipped (recently injected): "
              f"{', '.join(f'{pid}({s})' for pid, s in skipped)}", file=sys.stderr)

    if not relevant:
        if verbose:
            print(f"[step 3] No playbooks to inject ({THRESHOLD}/10 threshold, "
                  f"{len(skipped)} deduped)", file=sys.stderr)
        # Save state even if nothing to inject (compaction tracking still matters)
        if not dry_run:
            save_injection_state(repo_root, inj_state)
        sys.exit(0)

    # Record injection offsets before outputting
    current_size = (os.path.getsize(transcript_path)
                    if os.path.exists(transcript_path) else 0)
    for pb_id, score, content in relevant:
        inj_state["injections"][pb_id] = current_size

    if not dry_run:
        save_injection_state(repo_root, inj_state)

    # Output: injected into the agent's conversation
    print(f"[Transcript Analysis — {len(relevant)} relevant playbook(s) "
          f"(threshold: {THRESHOLD}/10)]")
    print(f"Agent goals: {agent_goals}")
    print()
    for pb_id, score, content in relevant:
        print(f"--- {pb_id} (relevance: {score}/10) ---")
        print(content)
        print()


if __name__ == "__main__":
    main()
