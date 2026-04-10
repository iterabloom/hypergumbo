#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure read-then-injected playbook overlap (waste signal).

Cross-references a session's filtered transcript and its paired
injection history sidecar to detect waste: cases where the agent
explicitly Read() a playbook file via its Read tool AND the
on_transcript_change pipeline injected the same playbook.  The
overlap is waste — both paths loaded the same content.

Usage:
    python scripts/measure-playbook-overlap.py [--session <path>]
        [--format text|json]

If --session is omitted, uses .agent/.last_session_transcript.jsonl
and .agent/.last_injection_history.jsonl.

WI-fusak.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PLAYBOOK_DIR = ".agent/agent_playbooks_protocols_sops_skills"


def _playbook_id_from_path(path: str) -> str | None:
    """Extract playbook ID from a file path.

    Playbook IDs are the filename without the .md suffix.  Returns None
    if the path isn't under the playbooks directory.
    """
    if PLAYBOOK_DIR not in path:
        return None
    name = Path(path).name
    if not name.endswith(".md"):
        return None
    return name[: -len(".md")]


def _collect_read_playbooks(transcript_path: Path) -> Counter:
    """Walk the transcript for Read tool calls on playbook files."""
    counts: Counter = Counter()
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # pragma: no cover
            if event.get("type") != "assistant":
                continue
            msg = event.get("message", {})
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue  # pragma: no cover
            for item in content:
                if not isinstance(item, dict):
                    continue  # pragma: no cover
                if item.get("type") != "tool_use":
                    continue
                if item.get("name") != "Read":
                    continue
                file_path = item.get("input", {}).get("file_path", "")
                pb_id = _playbook_id_from_path(file_path)
                if pb_id:
                    counts[pb_id] += 1
    return counts


def _collect_injected_playbooks(sidecar_path: Path) -> Counter:
    """Walk the injection history sidecar for injected playbooks."""
    counts: Counter = Counter()
    with open(sidecar_path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # pragma: no cover
            for pb in event.get("injected", []):
                counts[pb] += 1
    return counts


def compute_overlap(
    transcript_path: Path,
    sidecar_path: Path,
) -> dict:
    """Compute read-then-injected overlap for a session.

    Returns a dict with:
    - read_playbooks: mapping of playbook_id → read count via Read tool
    - injected_playbooks: mapping of playbook_id → inject count
    - overlap: set of playbooks that were both read AND injected
    - waste_rate: overlap count / max(read_total, injected_total)
    - top_overlap: sorted list of (playbook_id, read_count, inject_count)
    """
    reads = _collect_read_playbooks(transcript_path)
    injections = _collect_injected_playbooks(sidecar_path)
    overlap = set(reads) & set(injections)
    total_reads = sum(reads.values())
    total_injections = sum(injections.values())
    denom = max(total_reads, total_injections, 1)
    overlap_events = sum(
        reads[pb] + injections[pb] for pb in overlap
    )
    return {
        "read_playbooks": dict(reads),
        "injected_playbooks": dict(injections),
        "overlap": sorted(overlap),
        "overlap_count": len(overlap),
        "total_reads": total_reads,
        "total_injections": total_injections,
        "waste_rate": round(overlap_events / denom, 3),
        "top_overlap": sorted(
            [(pb, reads[pb], injections[pb]) for pb in overlap],
            key=lambda x: -(x[1] + x[2]),
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure read-then-injected playbook overlap",
    )
    parser.add_argument(
        "--session", type=Path, default=None,
        help="Path to transcript jsonl (defaults to "
             ".agent/.last_session_transcript.jsonl)",
    )
    parser.add_argument(
        "--sidecar", type=Path, default=None,
        help="Path to injection history sidecar (defaults to "
             ".agent/.last_injection_history.jsonl)",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format",
    )
    args = parser.parse_args(argv)

    transcript = args.session or Path(".agent/.last_session_transcript.jsonl")
    sidecar = args.sidecar or Path(".agent/.last_injection_history.jsonl")

    if not transcript.exists():
        print(f"Error: transcript not found: {transcript}", file=sys.stderr)
        return 1
    if not sidecar.exists():
        print(f"Error: sidecar not found: {sidecar}", file=sys.stderr)
        return 1

    result = compute_overlap(transcript, sidecar)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print("Playbook Overlap Analysis (Read tool vs injection pipeline)")
        print("=" * 60)
        print(f"Total Read tool calls on playbook files: {result['total_reads']}")
        print(f"Total injection events:                  {result['total_injections']}")
        print(f"Overlapping playbooks:                   {result['overlap_count']}")
        print(f"Waste rate:                              {result['waste_rate'] * 100:.1f}%")
        print()
        if result["top_overlap"]:
            print("Top overlapping playbooks (both read AND injected):")
            print(f"  {'Playbook':<45} {'Reads':>6} {'Injects':>8}")
            print(f"  {'-' * 45} {'-' * 6} {'-' * 8}")
            for pb, reads, injects in result["top_overlap"][:10]:
                print(f"  {pb:<45} {reads:>6} {injects:>8}")
        else:
            print("No overlap detected — the agent did not Read() any "
                  "playbooks that were also injected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
