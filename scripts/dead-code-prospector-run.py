#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lightweight one-shot dead-code-maybe prospecting run.

Runs `hypergumbo dead-code-maybe --format json --exclude-annotated` on a
user-specified set of repos and aggregates the results by linker gap
category.  Produces a single JSON artifact ranking candidates globally.

Per WI-tubot (created from WI-duroz human directive 2026-04-10): this
is an explicit ONE-SHOT variant, not a recurring bakeoff cohort.  The
goal is to surface which linker gaps appear most frequently across a
20-30 repo polyglot subset, prioritizing linker investment.

Usage:
    python scripts/dead-code-prospector-run.py \
        --pool ~/ALL_REPOS/whole_bunch_of_repos \
        --repos repo1,repo2,repo3 \
        --output ~/hypergumbo_lab_notebook/prospector_runs/run-YYYYMMDD/

If --repos is omitted, uses a built-in default selection of 20
polyglot repos covering Go, Java, Python, JS/TS, and Rust.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Default polyglot subset — spans multiple languages to maximize
# cross-language linker gap detection.  User may override with --repos.
_DEFAULT_REPOS = [
    # Go
    "alertmanager", "prometheus", "kafka",
    "containerd", "buildkit",
    # Java
    "spring-boot", "trino",
    # Python
    "airflow", "django", "superset",
    # JS/TS
    "vscode", "apollo-server",
    # Rust
    "wasmtime", "arti",
    # Polyglot / bindings
    "envoy", "cilium",
    # Smaller for variety
    "cowboy", "vapor",
]


def _categorize_candidate(name: str, path: str) -> str:
    """Group a dead-code candidate by likely linker gap category.

    Uses path + name heuristics to assign candidates to gap kinds.
    """
    lower_path = path.lower()
    lower_name = name.lower()

    if "unmarshal" in lower_name or "marshaljson" in lower_name or "unmarshalyaml" in lower_name:
        return "yaml_json_marshal"
    if "cli/" in lower_path and ("cmd" in lower_path or "cmd" in lower_name):
        return "cobra_cli_dispatch"
    if "maintenance" in lower_name or ".gc" in lower_name:
        return "goroutine_lifecycle"
    if "restapi/" in lower_path or "configure" in lower_name:
        return "swagger_generated"
    if "cluster/" in lower_path or "memberlist" in lower_path or "tlstransport" in lower_name:
        return "memberlist_callbacks"
    if ".exec" in lower_name and "stage" in lower_name:
        return "pipeline_stage_dispatch"
    if re.search(r"api/|rpc/|proto/|ffi/|native/|bindings/|bridge/", lower_path):
        return "cross_language_api"
    if "handler" in lower_name or "_request" in lower_name or "_response" in lower_name:
        return "handler_or_dto"
    return "uncategorized"


def _run_hypergumbo(repo_path: Path) -> dict | None:
    """Run hypergumbo dead-code-maybe on a repo and return parsed JSON."""
    try:
        result = subprocess.run(
            [
                "hypergumbo", "dead-code-maybe",
                str(repo_path),
                "--format", "json",
                "--exclude-annotated",
            ],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print(f"  [{repo_path.name}] TIMEOUT after 10 min", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  [{repo_path.name}] FAILED (exit {result.returncode})", file=sys.stderr)
        print(f"    stderr: {result.stderr[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [{repo_path.name}] JSON parse error: {e}", file=sys.stderr)
        return None


def run_prospecting(
    pool: Path, repos: list[str], output_dir: Path,
) -> dict:
    """Run dead-code-maybe on each repo and aggregate by category.

    Returns a summary dict and writes per-repo + aggregate JSON to
    output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    per_repo: dict[str, dict] = {}
    aggregate_categories: dict[str, list] = {}
    failed: list[str] = []

    for repo_name in repos:
        repo_path = pool / repo_name
        if not repo_path.exists():
            print(f"  [{repo_name}] SKIP (not in pool)", file=sys.stderr)
            failed.append(repo_name)
            continue
        print(f"  [{repo_name}] analyzing...", file=sys.stderr)
        result = _run_hypergumbo(repo_path)
        if result is None:
            failed.append(repo_name)
            continue
        candidates = result.get("dead_candidates", [])
        summary = result.get("summary", {})
        per_repo[repo_name] = {
            "summary": summary,
            "candidate_count": len(candidates),
        }
        # Categorize candidates and aggregate
        for c in candidates:
            cat = _categorize_candidate(c.get("name", ""), c.get("path", ""))
            aggregate_categories.setdefault(cat, []).append({
                "repo": repo_name,
                "name": c.get("name", ""),
                "path": c.get("path", ""),
                "language": c.get("language", ""),
                "loc": c.get("lines_of_code", 0),
                "cross_language_hits": c.get("cross_language_hits", 0),
                "path_shape_boost": c.get("path_shape_boost", 0),
            })
        # Save per-repo output
        (output_dir / f"{repo_name}.json").write_text(
            json.dumps(result, indent=2),
        )

    # Sort each category by combined score
    for cat in aggregate_categories:
        aggregate_categories[cat].sort(
            key=lambda c: -(c["cross_language_hits"] + c["path_shape_boost"]),
        )

    # Build summary
    category_counts = {
        cat: len(items) for cat, items in aggregate_categories.items()
    }
    total_candidates = sum(category_counts.values())

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pool": str(pool),
        "repos_requested": repos,
        "repos_analyzed": list(per_repo.keys()),
        "repos_failed": failed,
        "total_candidates": total_candidates,
        "category_counts": dict(sorted(
            category_counts.items(), key=lambda x: -x[1],
        )),
        "per_repo": per_repo,
        "top_by_category": {
            cat: items[:10] for cat, items in aggregate_categories.items()
        },
    }
    (output_dir / "aggregate.json").write_text(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lightweight one-shot dead-code-maybe prospecting run",
    )
    parser.add_argument(
        "--pool", type=Path,
        default=Path.home() / "ALL_REPOS" / "whole_bunch_of_repos",
        help="Pool directory containing repos",
    )
    parser.add_argument(
        "--repos", type=str, default=None,
        help="Comma-separated list of repo names (default: built-in subset)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory (default: ~/hypergumbo_lab_notebook/"
             "prospector_runs/run-<timestamp>/)",
    )
    args = parser.parse_args(argv)

    if args.repos:
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    else:
        repos = _DEFAULT_REPOS

    if args.output:
        output_dir = args.output
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (
            Path.home() / "hypergumbo_lab_notebook" / "prospector_runs"
            / f"run-{stamp}"
        )

    print(f"Pool:    {args.pool}")
    print(f"Repos:   {len(repos)} ({', '.join(repos[:3])}...)")
    print(f"Output:  {output_dir}")
    print()

    summary = run_prospecting(args.pool, repos, output_dir)

    print()
    print("=" * 60)
    print("PROSPECTING SUMMARY")
    print("=" * 60)
    print(f"Repos analyzed: {len(summary['repos_analyzed'])}")
    print(f"Repos failed:   {len(summary['repos_failed'])}")
    print(f"Total candidates: {summary['total_candidates']}")
    print()
    print("Top categories by candidate count:")
    for cat, count in list(summary["category_counts"].items())[:10]:
        print(f"  {cat:<30} {count:>6}")
    print()
    print(f"Full artifact: {output_dir / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
