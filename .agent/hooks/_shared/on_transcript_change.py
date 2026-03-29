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
CHARS_PER_TOKEN = 4.4

# Playbook registry: (id, path relative to repo root, one-line summary)
PLAYBOOKS = [
    ("autonomous-mode-guide",
     ".agent/agent_playbooks_protocols_sops_skills/autonomous-mode-guide",
     "BROAD vs DEEP bakeoff mode selection and when to switch between them."),
    ("lab-notebook-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/lab-notebook-playbook",
     "Recording observations in lab notebook during real-repo experiments."),
    ("experiment-design-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/experiment-design-playbook",
     "Mini trial runs before full experiments, 8-hour rule for long commands."),
    ("bakeoff-broad-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-broad-priorities",
     "BROAD mode priority queue: reflect, aggregate, linkers, frameworks."),
    ("bakeoff-deep-priorities",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-deep-priorities",
     "DEEP mode priority queue: reflect, aggregate, slice, tiers, centrality."),
    ("bakeoff-artifacts-guide",
     ".agent/agent_playbooks_protocols_sops_skills/bakeoff-artifacts-guide",
     "Where bakeoff artifacts are stored and how sessions are organized."),
    ("coverage-and-test-placement",
     ".agent/agent_playbooks_protocols_sops_skills/coverage-and-test-placement",
     "100% test coverage requirement, per-package isolation, test placement."),
    ("structural-fix-scope-expansion-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/structural-fix-scope-expansion-protocol",
     "Assume bugs are structural, name invariants, scope-expand across languages."),
    ("signing-and-identity",
     ".agent/agent_playbooks_protocols_sops_skills/signing-and-identity",
     "Git identity verification and DCO sign-off requirements."),
    ("smart-test-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/smart-test-playbook",
     "Using pytest/smart-test alias, compact output, affected test selection."),
    ("output-capture-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/output-capture-playbook",
     "Redirect long-running command output to files, never pipe through tail."),
    ("pre-work-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-work-playbook",
     "Pre-work checklist: PR gate, vPR flush, branch sync, spec review."),
    ("recover-state-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook",
     "Post-compaction recovery from last_stop_check.json and tracker."),
    ("pre-commit-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-commit-playbook",
     "Pre-commit checklist: identity, tests, changelog, tracker, sign-off."),
    ("integration-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/integration-protocol",
     "Feature branch workflow: tests, branch, commit, auto-pr, CI, merge."),
    ("merge-strategy",
     ".agent/agent_playbooks_protocols_sops_skills/merge-strategy",
     "Fast-forward merge default, rebase if diverged, squash discouraged."),
    ("vpr-usage",
     ".agent/agent_playbooks_protocols_sops_skills/vpr-usage",
     "Virtual PR queue for offline resilience when remote is unavailable."),
    ("auto-pr-ci-failure-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/auto-pr-ci-failure-playbook",
     "Recovery by exit code when auto-pr fails or CI is stuck."),
    ("release-workflow",
     ".agent/agent_playbooks_protocols_sops_skills/release-workflow",
     "Two-step release: agent prepares, human signs tag and pushes."),
    ("ci-debug-protocol",
     ".agent/agent_playbooks_protocols_sops_skills/ci-debug-protocol",
     "CI debugging with ci-debug script, workflow topology, dependencies."),
    ("optional-dependency-testing-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/optional-dependency-testing-playbook",
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
    """Parse the LLM's ratings response into {playbook_id: score}."""
    results: dict[str, int] = {}
    for pb_id, _, _ in PLAYBOOKS:
        # Look for patterns like "8 - autonomous-mode-guide" or
        # "autonomous-mode-guide: 8" or "8/10 autonomous-mode-guide"
        patterns = [
            rf"(\d+)\s*[/\-–:]\s*(?:10\s*)?.*?{re.escape(pb_id)}",
            rf"{re.escape(pb_id)}.*?(\d+)",
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    transcript_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit(0)

    # Determine repo root (this script lives at .agent/hooks/_shared/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", "..", ".."))

    # Step 0: Select recent entries within token budget
    recent = select_recent_entries(transcript_path)
    if not recent:
        sys.exit(0)

    # Step 1: Distill agent goals
    step1_prompt = (
        "Below are the latest turns in an agentic coding session. "
        "Please distill what the agent's present goals are.\n\n"
        + recent
    )
    agent_goals = openrouter_chat(step1_prompt)
    if not agent_goals:
        sys.exit(0)

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
        "just the number and document name for each, one per line.\n\n"
        + playbook_list
    )
    ratings_text = openrouter_chat(step2_prompt)
    if not ratings_text:
        sys.exit(0)

    ratings = parse_ratings(ratings_text)

    # Step 3: Collect playbooks above threshold
    relevant = []
    for pb_id, pb_path, pb_summary in PLAYBOOKS:
        score = ratings.get(pb_id, 0)
        if score >= THRESHOLD:
            content = read_playbook(repo_root, pb_path)
            if content:
                relevant.append((pb_id, score, content))

    if not relevant:
        sys.exit(0)

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
