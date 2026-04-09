#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backfill cohort metadata for v0 training corpus entries.

Reads a v0 training-data JSONL snapshot and writes a parallel sidecar
JSONL file with per-entry cohort metadata resolved from git history.
Does NOT modify the original corpus.  Re-runnable (overwrites sidecar).

Resolution logic:
  1. Build a commit timeline for on_transcript_change.py from git log.
  2. For each entry's timestamp, binary-search the timeline to find the
     commit SHA that was effective at that moment.
  3. Pre-compute playbook counts per unique SHA via ``git show``.
  4. Extract the playbook count claimed in the prompt (sparse_selection
     entries only) via regex.

Sidecar schema (one row per corpus entry):
  entry_index, entry_timestamp, infra_sha, infra_sha_short,
  playbook_registry_sha, playbook_count_actual, playbook_count_in_prompt,
  main_llm_presumed

See WI-gigil for the full design rationale.
"""

import argparse
import bisect
import json
import os
import re
import subprocess
import sys

INFRA_PATH = ".agent/hooks/_shared/on_transcript_change.py"
# PLAYBOOKS registry lives in the same file for the entire v0 window
PLAYBOOK_REGISTRY_PATH = INFRA_PATH

# Regex for the opening line of each tuple in the PLAYBOOKS list.
# Matches:  ("some-id",  or  ("some_id",
_PLAYBOOK_TUPLE_RE = re.compile(r'^\s+\("[a-z][\w-]+",\s*$', re.MULTILINE)

# Regex for the playbook count stated in the sparse_selection prompt.
_PROMPT_COUNT_RE = re.compile(r"Below are (\d+) guidance documents")


def get_commit_timeline(
    repo_root: str, file_path: str,
) -> list[tuple[str, str]]:
    """Return all commits touching *file_path*, oldest-first.

    Each element is ``(naive_iso_timestamp, full_sha)`` where the
    timestamp has been stripped of its timezone offset so it can be
    compared directly to the entry timestamps in the corpus (which
    are naive local times from ``datetime.now().isoformat()``).
    """
    result = subprocess.run(
        ["git", "log", "--pretty=format:%aI %H", "--reverse", "--",
         file_path],
        capture_output=True, text=True, cwd=repo_root, timeout=30,
    )
    if result.returncode != 0:
        return []

    timeline: list[tuple[str, str]] = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            # Strip timezone: "2026-04-05T16:32:43-04:00" → "2026-04-05T16:32:43"
            naive_ts = parts[0][:19]
            timeline.append((naive_ts, parts[1]))
    return timeline


def resolve_sha_at_timestamp(
    timeline: list[tuple[str, str]],
    timestamps_only: list[str],
    entry_timestamp: str,
) -> str:
    """Find the most recent commit SHA committed on or before *entry_timestamp*.

    Uses ``bisect`` on the pre-extracted timestamp list for O(log n)
    lookup.  Returns an empty string if no commit predates the entry.
    """
    entry_ts = entry_timestamp[:19]  # normalize to YYYY-MM-DDTHH:MM:SS
    idx = bisect.bisect_right(timestamps_only, entry_ts)
    if idx == 0:
        return ""
    return timeline[idx - 1][1]


def count_playbooks_at_sha(
    repo_root: str, sha: str, file_path: str = INFRA_PATH,
) -> int:
    """Count PLAYBOOKS tuple entries in the file at a specific git SHA.

    Returns -1 on any error (SHA not found, file not in tree, etc.).
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{sha}:{file_path}"],
            capture_output=True, text=True, cwd=repo_root, timeout=10,
        )
        if result.returncode != 0:
            return -1
        return len(_PLAYBOOK_TUPLE_RE.findall(result.stdout))
    except (OSError, subprocess.TimeoutExpired):
        return -1


def extract_prompt_playbook_count(entry: dict) -> int:
    """Extract the playbook count from a sparse_selection prompt.

    Returns -1 for non-sparse_selection entries or when the count is
    not found in the prompt text.
    """
    if entry.get("step") != "sparse_selection":
        return -1
    messages = entry.get("messages", [])
    if not messages:
        return -1
    prompt = messages[0].get("content", "")
    match = _PROMPT_COUNT_RE.search(prompt)
    return int(match.group(1)) if match else -1


def backfill(
    corpus_path: str,
    output_path: str,
    repo_root: str,
    main_llm_presumed: str = "claude-opus-4-6",
    verbose: bool = True,
) -> int:
    """Run the backfill and return the number of entries processed."""
    if verbose:
        print(f"Corpus: {corpus_path}", file=sys.stderr)
        print(f"Output: {output_path}", file=sys.stderr)
        print(f"Repo:   {repo_root}", file=sys.stderr)

    # Build commit timeline
    if verbose:
        print("Building commit timeline...", file=sys.stderr)
    timeline = get_commit_timeline(repo_root, INFRA_PATH)
    timestamps_only = [ts for ts, _ in timeline]
    if verbose:
        print(f"  {len(timeline)} commits to {INFRA_PATH}", file=sys.stderr)

    # Pre-compute playbook counts per unique SHA
    if verbose:
        print("Pre-computing playbook counts per commit...", file=sys.stderr)
    unique_shas = {sha for _, sha in timeline}
    playbook_counts: dict[str, int] = {}
    for sha in sorted(unique_shas):
        playbook_counts[sha] = count_playbooks_at_sha(repo_root, sha)

    # Process corpus
    if verbose:
        print("Processing corpus...", file=sys.stderr)
    count = 0
    with open(corpus_path) as f_in, open(output_path, "w") as f_out:
        for idx, line in enumerate(f_in):
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp", "")
            infra_sha = resolve_sha_at_timestamp(
                timeline, timestamps_only, ts,
            )

            sidecar = {
                "entry_index": idx,
                "entry_timestamp": ts,
                "infra_sha": infra_sha,
                "infra_sha_short": infra_sha[:9] if infra_sha else "",
                "playbook_registry_sha": infra_sha,
                "playbook_count_actual": playbook_counts.get(infra_sha, -1),
                "playbook_count_in_prompt": extract_prompt_playbook_count(
                    entry,
                ),
                "main_llm_presumed": main_llm_presumed,
            }

            f_out.write(json.dumps(sidecar, ensure_ascii=False) + "\n")
            count += 1

            if verbose and (count % 1000 == 0):
                print(f"  {count} entries processed...", file=sys.stderr)

    if verbose:
        print(f"Done. {count} entries → {output_path}", file=sys.stderr)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill cohort metadata for v0 training corpus",
    )
    parser.add_argument(
        "corpus",
        help="Path to v0 training corpus JSONL file",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output sidecar JSONL path "
             "(default: <corpus-stem>-cohort-tags.jsonl)",
    )
    parser.add_argument(
        "--repo-root",
        help="Git repository root (default: auto-detect)",
    )
    parser.add_argument(
        "--main-llm-presumed",
        default="claude-opus-4-6",
        help="Presumed main LLM for v0 entries "
             "(default: claude-opus-4-6)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.corpus):
        print(f"Error: corpus not found: {args.corpus}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    if not output_path:
        stem, _, ext = args.corpus.rpartition(".")
        if not stem:
            stem = args.corpus
        output_path = f"{stem}-cohort-tags.jsonl"

    repo_root = args.repo_root
    if not repo_root:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        repo_root = result.stdout.strip()

    backfill(args.corpus, output_path, repo_root, args.main_llm_presumed)


if __name__ == "__main__":
    main()
