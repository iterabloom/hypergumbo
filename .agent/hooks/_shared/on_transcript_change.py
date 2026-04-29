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

import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
import uuid


# ---------------------------------------------------------------------------
# Per-session path helpers (ADR-0018 amendment, Option 2)
# ---------------------------------------------------------------------------

CURRENT_TRANSCRIPT_RE = re.compile(
    r"\.current_session_transcript\.([A-Za-z0-9_-]+)\.jsonl$"
)


def _session_id_from_transcript_path(transcript_path: str) -> str:
    """Extract the session_id from a per-session current transcript path.

    Per-session transcript paths have the form
    ``<repo>/.agent/.current_session_transcript.<session_id>.jsonl``.
    Returns an empty string if the basename does not match — callers
    should treat that as "no session id known" and fall back to
    repo-global state files.
    """
    basename = os.path.basename(transcript_path)
    m = CURRENT_TRANSCRIPT_RE.search(basename)
    return m.group(1) if m else ""

# ---------------------------------------------------------------------------
# Cohort metadata helpers (WI-tatuh / INV-gajap)
# ---------------------------------------------------------------------------

# Relative paths used for SHA lookups — the PLAYBOOKS registry currently
# lives in the same file as the rest of the hook infra.
_INFRA_REL_PATH = ".agent/hooks/_shared/on_transcript_change.py"
_PLAYBOOK_REGISTRY_REL_PATH = _INFRA_REL_PATH

# Module-level SHA cache: {(repo_root, rel_path): sha_string}
_sha_cache: dict[tuple[str, str], str] = {}


def _get_file_commit_sha(repo_root: str, rel_path: str) -> str:
    """Return the commit SHA that last modified *rel_path*, cached per-process.

    Uses ``git log -1`` to find the most recent commit touching the file.
    Returns an empty string on any error (not a git repo, file untracked,
    git not installed, timeout).
    """
    key = (repo_root, rel_path)
    if key not in _sha_cache:
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%H", "--", rel_path],
                capture_output=True, text=True, cwd=repo_root, timeout=5,
            )
            _sha_cache[key] = (
                result.stdout.strip() if result.returncode == 0 else ""
            )
        except (OSError, subprocess.TimeoutExpired):
            _sha_cache[key] = ""
    return _sha_cache[key]


def _extract_transcript_metadata(transcript_path: str) -> dict[str, str]:
    """Extract ``main_llm`` and ``vendor_version`` from the transcript JSONL.

    Scans lines from the end of the file for efficiency — we only need the
    most recent values:

    * ``main_llm``: the ``message.model`` field on the last assistant-type
      entry (identifies the LLM whose transcript is being analyzed).
    * ``vendor_version``: the top-level ``version`` field on any entry
      (Claude Code writes this on session-start lines).

    Returns a dict with both keys (empty strings if not found).
    """
    main_llm = ""
    vendor_version = ""
    if not os.path.exists(transcript_path):
        return {"main_llm": main_llm, "vendor_version": vendor_version}

    try:
        with open(transcript_path, "rb") as f:
            lines = f.readlines()

        for line in reversed(lines):
            if main_llm and vendor_version:
                break
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not main_llm:
                msg = entry.get("message")
                if isinstance(msg, dict) and "model" in msg:
                    main_llm = str(msg["model"])
            if not vendor_version:
                ver = entry.get("version")
                if ver is not None:
                    vendor_version = str(ver)
    except OSError:
        pass

    return {"main_llm": main_llm, "vendor_version": vendor_version}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DISTILL_MODEL = os.environ.get("TRANSCRIPT_DISTILL_MODEL", "mistralai/mistral-small-3.2-24b-instruct")
SELECT_MODEL = os.environ.get("TRANSCRIPT_SELECT_MODEL", "mistralai/mistral-small-2603")
MAX_TOKENS = int(os.environ.get("TRANSCRIPT_MAX_TOKENS", "4000"))
DEDUP_TOKENS = int(os.environ.get("TRANSCRIPT_DEDUP_TOKENS", "100000"))
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
     "spirals. The playbook also names three shapes where you should NOT file a new item: "
     "existing-coverage (an item already covers the surface — use `tracker discuss <ID>` "
     "with a regression note instead; run `scripts/tracker tags` to enumerate the vocabulary "
     "before searching), conversation-in-progress (the human is actively discussing the "
     "concern — ask before filing), and property-of-existing-invariant (the new failure mode "
     "is structurally a property of a tracked invariant — file as a regression note on the "
     "parent INV, not a sibling item)."),
    ("smart-test-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/smart-test-playbook.md",
     "Always use the pytest alias (which invokes smart-test) instead of python -m pytest or "
     "direct pytest. Smart-test provides a compact ~20-line summary, saves full output to "
     ".ci/pytest-output.log, and runs only tests affected by changed files. Commit "
     ".ci/affected-tests.txt with every PR for CI smart test selection."),
    ("output-capture-long-running-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/output-capture-long-running-playbook.md",
     "NEVER pipe the output of long-running commands (auto-pr, merge-pr, pytest, bakeoff-*, "
     "ci-debug, release-check) through | tail -N or | head -N. Redirect to a file "
     "(command > /tmp/cmd.log 2>&1) and use the Read tool or Grep on the file. For commands "
     "that take many minutes, run in background and point a Monitor at the file with an "
     "alternation covering every terminal state, not just the happy path. Hazard: auto-pr's "
     ".ops backup/restore cycle can overwrite tracker discuss/add/update operations made "
     "during the run — avoid tracker writes while auto-pr is in flight. Recovery procedure "
     "(including the pre-pop stash verification step) lives in the playbook itself."),
    ("process-validation-queue-with-bakeoffs-and-uat",
     ".agent/agent_playbooks_protocols_sops_skills/process-validation-queue-with-bakeoffs-and-uat.md",
     "Routine for processing the awaits_bakeoff_validation queue end-to-end. "
     "Seven phases for the cohort path: audit-and-classify each tagged item by "
     "validation modality — read each item's discussion thread for explicit "
     "modality requests like 'validate via UAT' / 'ground-truth required' and "
     "honor those before falling back to claim-shape defaults; classify into "
     "Bucket A cohort-validatable / B-prospector pipeline-dependent / B-shape "
     "shape-claim / D-stale already-validated / E-misapplied no-quantitative-"
     "claim / F-reverted fix-undone; strip anomalies first; design a minimum-"
     "target cohort that exercises every Bucket A claim, drawing repos from "
     "~/ALL_REPOS/; run + write a verification script importing canonical "
     "taxonomy (DO NOT hand-roll allowlists — one such mistake produced 3000+ "
     "false flags); fill substantive YAMLs with verdicts for every applicable "
     "claim (not just the auto-injected ones); apply via aggregate; hand-"
     "correct inconclusive plurality with rationale for niche-language claims; "
     "tackle regression sub-items (fix structurally, or close wont_do for by-"
     "design limitations or cohort-coverage gaps); re-iterate with `bakeoff-"
     "deep cycle --workdir <existing-session>` to produce iter-002 in the "
     "same session, NEVER fresh `init` for a validation re-run (breaks "
     "convergence tracking). DIRECTED UAT-BAKEOFF PATH (parallel modality, "
     "not cohort-residue): for human-curated item lists or items with an "
     "explicit UAT modality request, validation runs in ~/hypergumbo_lab_"
     "notebook/bakeoff_artifacts/hg-uat-vX.Y.Z/ created by the HUMAN copying "
     "~/hypergumbo_lab_notebook/hg-uat-template/. Two-agent split with a "
     "deliberate firewall: ORCHESTRATOR AGENT (this agent, with hypergumbo "
     "source + tracker access) drafts per-item plan.md from tracker discussion "
     "threads with verdict criteria pre-committed as concrete thresholds, "
     "presents to human for approval, writes plan.md to a new round dir, then "
     "stops. HUMAN runs `./bin/status --sync` and starts the UAT AGENT (a "
     "separate naive agent following the campaign's own AGENTS.md, with NO "
     "hypergumbo source access and NO tracker access — that's the validation "
     "discipline). UAT agent runs hypergumbo against ~/ALL_REPOS/ targets, "
     "ground-truths against TARGET REPO source (not hypergumbo internals), "
     "writes report.md with verdict matrix (moved/no_move/inconclusive). "
     "ORCHESTRATOR returns post-round to apply tag mutations: moved→strip + "
     "discussion; no_move→keep tag, file P1 regression sub-item parent-linked "
     "and WITHOUT awaits_bakeoff_validation tag; inconclusive→keep tag + "
     "discussion noting what would unblock. Hard rules: don't relay hypergumbo "
     "internals across the firewall; don't override UAT verdicts post-hoc "
     "(file an observation, let next round re-validate); regression sub-items "
     "DON'T inherit the awaits_bakeoff_validation tag; never strip tag without "
     "paired discussion entry. Multi-campaign discipline: one campaign per "
     "release, prior campaigns persist as the convergence record, never "
     "delete. Bucket B items also get UAT-style spot-check (10-15 candidate "
     "ground-truth) rather than cohort."),
    ("pre-work-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/pre-work-playbook.md",
     "Checklist before starting any new feature: verify no auto-pr is in flight "
     "(PR_PENDING gate), flush queued vPRs if remote is available, sync dev and main "
     "branches, review the spec and changelog for current progress, then create a feature "
     "branch with the naming convention author/[feat|fix|docs|refactor]/description."),
    ("recover-state-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/recover-state-playbook.md",
     "After context compaction, recover state from two files in "
     "~/<repo>_lab_notebook/guidance_log/: stop_hook_state.json (hook-written: "
     "last_completed_utc, current_branch, guidance_file, bakeoff fields) and "
     "agent_notes.json (agent-written free-text notes field, via "
     "scripts/agent-notes --set/--append). Also check guidance_file for recent "
     "stop hook output and run tracker ready for pending work items. Keep notes "
     "fresh after key milestones via the scripts/agent-notes tool only — the "
     "stop_hook_state.json file is hook-owned and must not be edited directly. "
     "Before signing off (no in-flight work, no auto-pr pending), append a "
     "one-paragraph summary of what the next session needs to know — open "
     "invariant violations, P1+ defects discovered, status changes on tracked "
     "invariants, cross-cutting context — via scripts/agent-notes --append. "
     "The during-session 'keep notes fresh' rule covers updates as you go; the "
     "session-end refresh is the symmetric write obligation that closes the "
     "recovery loop."),
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
     "workflows: ci.yml (per-PR smart-test), full-suite (twice daily at 01:00 and 13:00 UTC, all packages), "
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
     "Three-phase audit of the [Unreleased] sections of both CHANGELOG.md (main tool) and "
     "packages/hypergumbo-tracker/CHANGELOG.md (tracker package). Phase 0 (relocation): move "
     "misplaced entries to the correct changelog — tracker-only work (commits touching only "
     "packages/hypergumbo-tracker/) belongs in the tracker changelog, not the main one. "
     "Phase 1 (completeness): compare each section against path-filtered git log; tracker "
     "commits are not 'missing' from the main changelog. Phase 2 (organization): calibrate "
     "detail level against recent released sections. Merge duplicates, normalize granularity, "
     "group related items, reorder by significance. Never remove information. Budget: max 3 "
     "rounds of organization edits per changelog."),

    ("agentic-session-retrospective",
     ".agent/agent_playbooks_protocols_sops_skills/agentic-session-retrospective.md",
     "Structured post-hoc analysis of an agent's decision-making during an autonomous session. "
     "Evaluates how the agent decided what to build — not what it built. Five phases: (1) read "
     ".agent/.last_session_transcript.jsonl (rotated on session start, vendor-agnostic) AND "
     ".agent/.last_injection_history.jsonl (parallel sidecar recording every playbook injection "
     "event with the distilled goal, selected/injected/skipped-dedup playbook IDs — this is the "
     "ONLY way to answer 'were the right playbooks injected at the right times?', since Claude "
     "Code's additionalContext mechanism does not round-trip into the session JSONL); older "
     "sessions live gzipped in .agent/.archived-transcripts/<UTC-stamp>/, (2) reconstruct the "
     "decision sequence as a timeline with branching points, (3) analyze infrastructure "
     "interactions — stop hook steering, CI/merge overhead, tracker task selection, playbook "
     "injection relevance (compute precision, dedup-hit rate, top-injected, read-then-injected "
     "overlap), AGENTS.md compliance, bakeoff integration, (4) quantify time/token allocation "
     "across feature work, CI overhead, research, compliance, error recovery, and idle time, "
     "(5) synthesize findings as structured proposals (what happened, impact, root cause, "
     "proposed improvement, category) and record in the lab notebook. Creates tracker items "
     "for actionable improvements. Time box: 30-60 minutes total."),

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

    ("trackerize",
     ".agent/agent_playbooks_protocols_sops_skills/trackerize-playbook.md",
     "When the user says 'trackerize', decompose the plan under discussion into individual "
     "self-contained tracker items. Check existing tracker items first to avoid duplicates and "
     "inform priority assignment (0-4). Use --isbefore for real dependencies between items, tags "
     "for filterability, and parent relationships only when structurally compelling. Prefer flat "
     "lists. If what to trackerize is ambiguous, ask the user to clarify. Create items in "
     "dependency order so --isbefore references are valid. Summarize created items for the user."),

    ("tracker-reply-playbook",
     ".agent/agent_playbooks_protocols_sops_skills/tracker-reply-playbook.md",
     "When tracker check-messages surfaces unread human messages, reply substantively before "
     "starting new feature work. Four-step protocol: (1) read full thread via tracker show, "
     "(2) classify the message (approval, directive, question, tabling, correction), (3) reply "
     "with evidence and artifacts — do not promise future action, act now, (4) update item "
     "status if warranted. Anti-patterns: drive-by acknowledgments, replying in the same turn "
     "as starting a new feature branch, promising to investigate next session instead of now."),
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
    *,
    main_llm: str = "",
    vendor_version: str = "",
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

    **Cohort metadata (v2, WI-tatuh / INV-gajap / WI-nadud):**

    Every entry carries top-level cohort metadata so distribution shifts are
    discoverable from the corpus alone:

    * ``pipeline_version`` — forward marker (``"v2"`` since WI-nadud normalization)
    * ``infra_sha`` — commit SHA of this file at write time
    * ``playbook_registry_sha`` — commit SHA of the PLAYBOOKS registry file
    * ``main_llm`` — the LLM whose transcript is being analyzed
    * ``vendor`` — always ``"claude-code"`` (the only adapter currently)
    * ``vendor_version`` — Claude Code version from the session JSONL
    * ``scoring_model`` — alias for the legacy *model* parameter; *model* is
      kept as a backward-compat key for existing data loaders
    """
    prompt, response = _truncate_to_budget(prompt, response, MAX_TOKENS)
    log_path = TRAINING_LOG
    if not log_path:
        log_path = os.path.join(repo_root, ".agent", ".training-data.jsonl")

    infra_sha = _get_file_commit_sha(repo_root, _INFRA_REL_PATH)
    playbook_registry_sha = _get_file_commit_sha(
        repo_root, _PLAYBOOK_REGISTRY_REL_PATH,
    )

    obj = {
        "timestamp": datetime.datetime.now().isoformat(),
        "step": step,
        "model": model,
        "scoring_model": model,
        "pipeline_version": "v2",
        "infra_sha": infra_sha,
        "playbook_registry_sha": playbook_registry_sha,
        "main_llm": main_llm,
        "vendor": "claude-code",
        "vendor_version": vendor_version,
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


def log_injection_history(
    repo_root: str,
    *,
    transcript_offset: int,
    agent_goals: str,
    selected: list[str],
    injected: list[str],
    skipped_dedup: list[str],
    event_id: str,
    session_id: str,
) -> None:
    """Append an injection-event record to the per-session rotated sidecar.

    Fixes ADR-0018's "retrospective blindness" gap. Claude Code's
    ``additionalContext`` mechanism does not write hook-injected text back
    into the session transcript JSONL, so a retrospective on
    ``.last_session_transcript.jsonl`` cannot see which playbooks were
    injected, when, or whether they were relevant. The sidecar at
    ``<repo>/.agent/.current_injection_history.<session_id>.jsonl`` is the
    durable record of every poll the LLM-driven selector made: the
    distilled goal, what it picked, what was actually injected (after
    dedup), and what was skipped because it was already in the model's
    context.

    Per-session isolation (ADR-0018 amendment / Option 2): each session
    writes to its own per-session sidecar file. On session END,
    ``rotate-on-session-end.sh`` promotes that file into the global
    ``.last_injection_history.jsonl`` slot:

        .current_injection_history.<sid>.jsonl   (this session, while alive)
            -> .last_injection_history.jsonl     (on session end)
            -> .second_to_last_injection_history.jsonl  (when next session ends)
            -> .agent/.archived-transcripts/<UTC-stamp>/injection_history.jsonl.gz

    Best-effort: any ``OSError`` is swallowed so a logging hiccup never
    breaks the pipeline. Mirrors the resilience pattern in
    ``log_training_example``.

    *transcript_offset* is the byte offset in the filtered transcript at
    the moment of injection (matches the dedup state's offsets — useful
    for cross-referencing).
    """
    filename = f".current_injection_history.{session_id}.jsonl"
    log_path = os.path.join(repo_root, ".agent", filename)
    record = {
        "timestamp": datetime.datetime.now().isoformat(),
        "session_id": session_id,
        "transcript_offset": transcript_offset,
        "event_id": event_id,
        "agent_goals": agent_goals,
        "selected": selected,
        "injected": injected,
        "skipped_dedup": skipped_dedup,
        "distill_model": DISTILL_MODEL,
        "select_model": SELECT_MODEL,
    }
    try:
        agent_dir = os.path.dirname(log_path)
        if agent_dir:
            os.makedirs(agent_dir, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Best-effort — never break the pipeline for logging failures


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


# WI-bodog: presentation helpers for the injection output block.
#
# Pre-WI-bodog format used a bare-id divider (``--- <pb_id> ---``)
# wrapped under ``[Transcript Analysis — N relevant playbook(s)]`` with
# raw file content immediately after.  Empirical signal from
# scripts/measure-playbook-overlap.py (WI-fusak) showed the agent
# re-Read injected playbooks within a few turns of receiving them — the
# block didn't *look* like a reference document, so the agent didn't
# recognize it as one.  These helpers reshape the output so each entry
# leads with a natural-language title plus a repo-relative path
# (matching the surfaces the agent actually grepped/spoke about) and so
# the leading SPDX HTML comment doesn't dominate the first visual line.

_SPDX_HEADER_RE = re.compile(
    r"^<!--\s*SPDX-License-Identifier:[^>]*-->\s*\n", re.IGNORECASE,
)
# Markdown headings — H1 / H2 / H3.  HTML comments are not headings.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def strip_spdx_header(content: str) -> str:
    """Strip a leading ``<!-- SPDX-License-Identifier: ... -->`` HTML
    comment and the trailing blank line that follows it.

    Conditional — applies only when the comment is on line 1.  Files
    that already have a heading on line 1 (9 of 21 playbooks) are
    unaffected.  Returns the stripped content with no leading
    whitespace; ``read_playbook`` already produces a stripped body, so
    skipping the strip here would still leave the SPDX marker as the
    first visible line.
    """
    if not content:
        return content
    match = _SPDX_HEADER_RE.match(content)
    if not match:
        return content
    return content[match.end():].lstrip("\n")


def extract_natural_title(content: str) -> str:
    """Return the first H1/H2/H3 heading text, or "" if none found.

    Scans the content (post-SPDX-strip) line by line, skipping blank
    lines and HTML comments, returning the first markdown heading
    text.  Used to give injected playbooks a human-recognizable label
    in the divider — sampling of the WI-fusak overlap data showed the
    agent referred to playbooks by their natural-language title (e.g.
    "the priorities playbook"), never by the bare kebab-case id.
    """
    if not content:
        return ""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--"):
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            return m.group(2).strip()
        # First non-blank, non-comment line is not a heading — bail
        # rather than scanning the whole document looking for one.
        return ""
    return ""


def format_playbook_block(pb_id: str, pb_path: str, content: str) -> str:
    """Render a single playbook block for injection.

    Output shape::

        --- <natural title> — <repo-relative path> ---
        If the task below matches, consult this instead of re-reading the file.

        <content (with SPDX header stripped)>

    Falls back to ``pb_id`` for the title when no heading is found.
    The framing hint is on a fresh line so the divider stays visually
    distinct.
    """
    body = strip_spdx_header(content)
    title = extract_natural_title(body) or pb_id
    divider = f"--- {title} — {pb_path} ---"
    hint = (
        "If the task below matches, consult this instead of re-reading "
        "the file."
    )
    return f"{divider}\n{hint}\n\n{body}"


def _state_path(repo_root: str, session_id: str) -> str:
    """Return the per-session injection-state file path.

    Per-session isolation (ADR-0018 amendment / Option 2): each session
    has its own state file keyed by session_id, so concurrent sessions
    in the same repo never poison each other's dedup state.
    """
    filename = f".transcript-injection-state.{session_id}.json"
    return os.path.join(repo_root, ".agent", filename)


def _lock_path(repo_root: str, session_id: str) -> str:
    """Return the per-session injection-state lock file path.

    Sibling of ``_state_path``; used by ``injection_state_lock`` to
    serialize concurrent hook invocations (WI-ritut).
    """
    filename = f".transcript-injection-state.{session_id}.lock"
    return os.path.join(repo_root, ".agent", filename)


@contextlib.contextmanager
def injection_state_lock(repo_root: str, session_id: str):
    """Exclusive advisory lock covering the injection-state critical section.

    Serializes concurrent ``on_transcript_change`` hook invocations on the
    same session so their load-check-save sequences don't overlap. When
    the agent makes parallel tool calls, each tool's PostToolUse hook
    fires concurrently; without this lock every hook reads the same
    pre-write state, independently decides to inject the same playbooks,
    and emits duplicate content into the agent's context. With the lock,
    the second hook blocks until the first commits its injection record,
    then reads the updated state and correctly dedups (WI-ritut).

    POSIX-only (``fcntl.flock``). The lock is advisory and per-session —
    concurrent sessions in the same repo use different lockfiles and do
    not serialize against each other.
    """
    path = _lock_path(repo_root, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_fd = open(path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fd.close()


def _empty_state(session_id: str) -> dict:
    """Return a fresh injection state tagged with the session id."""
    return {
        "session_id": session_id,
        "injections": {},
        "last_compact_offset": 0,
    }


def load_injection_state(repo_root: str, session_id: str) -> dict:
    """Load per-session injection tracking state.

    Returns empty state if the state file is missing or corrupt. Under
    per-session isolation the state file path encodes session_id, so no
    cross-session validation is needed.

    State format:
    {
        "session_id": "<sid>",
        "injections": {"pb_id": <byte_offset_at_injection_time>, ...},
        "last_compact_offset": <byte_offset_of_last_compact_boundary>
    }
    """
    path = _state_path(repo_root, session_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return _empty_state(session_id)
    return _empty_state(session_id)


def save_injection_state(
    repo_root: str, session_id: str, state: dict,
) -> None:
    """Persist per-session injection tracking state atomically."""
    state["session_id"] = session_id
    path = _state_path(repo_root, session_id)
    agent_dir = os.path.dirname(path)
    if agent_dir:
        os.makedirs(agent_dir, exist_ok=True)
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
    session_id: str,
) -> tuple[set[str], dict]:
    """Determine which playbooks should be skipped due to recent injection.

    Uses a per-session state file to track when each playbook was injected
    (by byte offset in the transcript). Invalidates injections that
    occurred before the most recent compact_boundary event, since the LLM
    no longer has that context.

    Returns (set of pb_ids to skip, updated state dict).
    """
    state = load_injection_state(repo_root, session_id)
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

    # Derive session_id from the transcript path basename. The polling
    # script (poll-transcript-change.sh) constructs the path with the
    # session_id baked in, so the transcript path is the authoritative
    # source of session identity at this layer.
    session_id = _session_id_from_transcript_path(transcript_path)
    if not session_id:
        if verbose:
            print(
                f"[dry-run] Could not derive session_id from path: "
                f"{transcript_path!r}",
                file=sys.stderr,
            )
        sys.exit(0)

    if not dry_run and not os.environ.get("OPENROUTER_API_KEY"):
        if verbose:
            print("[dry-run] OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(0)

    # Determine repo root (this script lives at .agent/hooks/_shared/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, "..", "..", ".."))

    # Extract cohort metadata from transcript (WI-tatuh / INV-gajap).
    # Done once here; passed to every log_training_example call below.
    _tmeta = _extract_transcript_metadata(transcript_path)
    _cohort_kw: dict[str, str] = {
        "main_llm": _tmeta["main_llm"],
        "vendor_version": _tmeta["vendor_version"],
    }

    # Serialize concurrent hook invocations on the same session so parallel
    # hooks don't race on injection state and emit duplicate playbooks
    # (WI-ritut). Everything from the state load through the state save
    # must happen under this lock; sys.exit() inside the block releases
    # the lock via normal process-exit cleanup.
    with injection_state_lock(repo_root, session_id):
        _run_injection_pipeline(
            transcript_path=transcript_path,
            session_id=session_id,
            repo_root=repo_root,
            dry_run=dry_run,
            verbose=verbose,
            cohort_kw=_cohort_kw,
        )


def _run_injection_pipeline(
    *,
    transcript_path: str,
    session_id: str,
    repo_root: str,
    dry_run: bool,
    verbose: bool,
    cohort_kw: dict,
) -> None:
    """Load → decide → inject → save. Must run under injection_state_lock.

    Extracted from ``main`` so the critical-section boundary is explicit
    and the lock wrapping in ``main`` stays a single ``with`` statement
    instead of a 150-line indent.
    """
    # Step 0a: Check if all playbooks are recently injected (skip LLM calls entirely)
    all_ids = [pb_id for pb_id, _, _ in PLAYBOOKS]
    already, inj_state = recently_injected(
        transcript_path, all_ids, repo_root, session_id,
    )
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
            model=DISTILL_MODEL, **cohort_kw,
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
        **cohort_kw,
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

    # Pre-compute current_size once — used by both branches below.
    current_size = (os.path.getsize(transcript_path)
                    if os.path.exists(transcript_path) else 0)

    if not relevant:
        if verbose:
            print(f"[step 3] No playbooks to inject "
                  f"({len(skipped)} deduped)", file=sys.stderr)
        if not dry_run:
            # Save state even if nothing to inject (compaction tracking
            # still matters).
            save_injection_state(repo_root, session_id, inj_state)
            # Record this poll to the injection-history sidecar — even
            # zero-injection polls are valuable because they tell a
            # retrospective how often the selector said "none" or
            # everything was deduped (vs. how often it actually fired).
            log_injection_history(
                repo_root,
                transcript_offset=current_size,
                agent_goals=agent_goals,
                selected=selected,
                injected=[],
                skipped_dedup=skipped,
                event_id=event_id,
                session_id=session_id,
            )
        sys.exit(0)

    # Record injection offsets before outputting
    for pb_id, content in relevant:
        inj_state["injections"][pb_id] = current_size

    if not dry_run:
        save_injection_state(repo_root, session_id, inj_state)
        # Record this injection event to the sidecar so retrospectives
        # can later evaluate whether the selector was actually picking
        # the right playbooks for what the agent was doing.
        log_injection_history(
            repo_root,
            transcript_offset=current_size,
            agent_goals=agent_goals,
            selected=selected,
            injected=[pb_id for pb_id, _ in relevant],
            skipped_dedup=skipped,
            event_id=event_id,
            session_id=session_id,
        )

    # Output: injected into the agent's conversation.
    # WI-bodog: header uses a neutral noun ("document(s)") rather than
    # "playbook(s)" because 9 of 21 entries are protocols/guides/SOPs.
    # Each block is rendered via ``format_playbook_block`` so the
    # divider leads with a natural title + repo-relative path and a
    # one-line framing hint, and the leading SPDX HTML comment is
    # stripped so it doesn't dominate the first visual line.
    pb_paths = {pb_id: pb_path for pb_id, pb_path, _ in PLAYBOOKS}
    print(f"[Transcript Analysis — {len(relevant)} relevant document(s)]")
    if dry_run:
        print(f"Agent goals: {agent_goals}")
    print()
    for pb_id, content in relevant:
        pb_path = pb_paths.get(pb_id, "")
        print(format_playbook_block(pb_id, pb_path, content))
        print()


if __name__ == "__main__":
    main()
