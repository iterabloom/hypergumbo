#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Split the analyzer phase into FIXED cost and PER-FILE cost.

WHY (WI-zagij / WI-madut). Both incremental-analysis designs assume the analyzer
phase is per-file work that a cache could skip when a file did not change. On
this monorepo that phase is ~64s of a 160.9s cold ``--minimal`` analysis, so the
assumption decides whether either item is worth building. But "the analyzers walk
files" does not imply "the cost is proportional to files": grammar loading,
language-pack imports, registry construction and per-analyzer setup are paid per
LANGUAGE regardless of how many files changed, and no cache can skip them.

    analyzer_wall(n) ~= FIXED + PER_FILE * n

FIXED is the floor an incremental design cannot go below. If FIXED dominates,
per-file memoization buys far less than the phase's headline share suggests and
both items should be re-priced or closed on the evidence.

HOW. ``run_all_analyzers`` takes ``max_files``, which caps how many files EACH
language analyzer processes. Sweeping it holds the language mix fixed — every
grammar still loads, every analyzer still runs — while varying the per-file work,
which is exactly the decomposition wanted. Fitting wall against the files the
cards say were actually analyzed gives the intercept directly.

WHY NOT SWEEP WHOLE ``run_survey`` RUNS. Each would cost the ~95s of linkers and
graph post-processing that this question is not about, turning a 6-point sweep
into an hour. This calls ``run_all_analyzers`` directly, after doing the setup
``run_survey`` does first (file index, profile, size limit), so a data point at
``max_files=1`` costs about a second.

READ THE WALL, NOT THE CARD SUM. The dispatcher is a ``ThreadPoolExecutor``, so
per-pass ``duration_ms`` figures OVERLAP: on this repo they total 157.7s inside a
93.9s wall span. Card sums cannot be added into a share of wall time (that error
produced the earlier 22/7/71 split). This script reports both and the ratio
between them, because the ratio is itself the parallelism measurement.

USAGE
    scripts/measure-analyzer-scaling.py .
    scripts/measure-analyzer-scaling.py . --caps 1,5,25,100,400
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional, Tuple


def _card_rows(runs: List[Any]) -> List[Tuple[str, float, int]]:
    """(pass_id, seconds, files_analyzed) for each AnalysisRun card.

    ``run_all_analyzers`` returns ``list[dict]`` and ``AnalysisRun.to_dict``
    renames ``pass_id`` to ``pass``; reading the attribute yields None for every
    row, which silently zeroes the whole table.
    """
    out: List[Tuple[str, float, int]] = []
    for run in runs:
        if isinstance(run, dict):
            pid = run.get("pass") or run.get("pass_id") or "<unknown>"
            ms = run.get("duration_ms") or 0
            files = run.get("files_analyzed") or 0
        else:  # pragma: no cover - production returns dicts
            pid = getattr(run, "pass_id", None) or "<unknown>"
            ms = getattr(run, "duration_ms", 0) or 0
            files = getattr(run, "files_analyzed", 0) or 0
        out.append((pid, ms / 1000.0, files))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decompose analyzer wall-clock into fixed and per-file cost.",
    )
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--caps", default="1,3,10,30,100,300",
                    help="Comma-separated max_files values to sweep, plus an "
                         "uncapped run (default 1,3,10,30,100,300).")
    ap.add_argument("--top", type=int, default=12,
                    help="Slowest passes to list for the uncapped run.")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2

    from hypergumbo_core.analyze.all_analyzers import run_all_analyzers
    from hypergumbo_core.discovery import (
        DEFAULT_EXCLUDES, FileIndex, set_file_index, set_max_file_bytes,
    )
    from hypergumbo_core.profile import detect_profile

    # The setup run_survey performs before dispatching analyzers. Done once:
    # it is not part of the phase under measurement.
    print(f"indexing {repo_root} ...", file=sys.stderr)
    file_index = FileIndex.build(repo_root, excludes=list(DEFAULT_EXCLUDES))
    set_file_index(file_index)
    profile = detect_profile(repo_root, count_loc=True)
    set_max_file_bytes(None)
    profile_dict = profile.to_dict()

    caps: List[Optional[int]] = [int(c) for c in args.caps.split(",") if c.strip()]
    caps.append(None)  # uncapped

    print(f"{'max_files':>10s} {'wall s':>9s} {'files':>8s} {'cards s':>9s} "
          f"{'card/wall':>10s} {'passes':>7s}")
    print("-" * 60)
    points: List[Tuple[int, float]] = []
    last_rows: List[Tuple[str, float, int]] = []
    for cap in caps:
        t0 = time.perf_counter()
        result = run_all_analyzers(repo_root, max_files=cap, profile=profile_dict)
        wall = time.perf_counter() - t0
        runs = result[0]
        rows = _card_rows(runs)
        files = sum(f for _p, _s, f in rows)
        cards = sum(s for _p, s, _f in rows)
        points.append((files, wall))
        last_rows = rows
        label = "uncapped" if cap is None else str(cap)
        print(f"{label:>10s} {wall:9.1f} {files:8d} {cards:9.1f} "
              f"{cards / max(wall, 1e-9):10.2f} {len(rows):7d}")

    # --- least-squares fit: wall = FIXED + PER_FILE * files -----------------
    xs = [float(f) for f, _w in points]
    ys = [w for _f, w in points]
    n = len(points)
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        if denom else 0.0
    )
    intercept = mean_y - slope * mean_x
    full_files, full_wall = points[-1]

    print()
    print("=" * 66)
    print("DECOMPOSITION  (wall = FIXED + PER_FILE x files)")
    print("=" * 66)
    print(f"  FIXED (intercept)        {intercept:8.1f}s  "
          f"{100.0 * intercept / max(full_wall, 1e-9):5.1f}% of the full run")
    print(f"  PER_FILE (slope)         {1000.0 * slope:8.2f} ms/file")
    print(f"  full uncapped run        {full_wall:8.1f}s over {full_files} files")
    print(f"  modelled per-file total  {slope * full_files:8.1f}s")
    print()
    print("  READ AS: FIXED is the floor a per-file cache CANNOT remove — it is")
    print("  paid whenever the analyzer phase runs at all. The per-file total is")
    print("  the ONLY part an incremental design can win back, and only in full.")
    print()

    print(f"SLOWEST PASSES, uncapped (card seconds; these OVERLAP each other)")
    per_file_rows = [
        (p, s, f, s / f) for p, s, f in last_rows if f > 0
    ]
    for pid, secs, files, per in sorted(per_file_rows, key=lambda r: -r[1])[: args.top]:
        print(f"  {pid:32s} {secs:7.1f}s over {files:5d} files "
              f"= {1000.0 * per:8.1f} ms/file")

    zero_file = [(p, s) for p, s, f in last_rows if f == 0 and s > 0.05]
    if zero_file:
        print()
        print("PASSES BILLING TIME WITH ZERO FILES ANALYZED "
              "(pure fixed cost — nothing for a per-file cache to skip):")
        for pid, secs in sorted(zero_file, key=lambda r: -r[1])[:10]:
            print(f"  {pid:32s} {secs:7.1f}s")

    langs = Counter({p: f for p, _s, f in last_rows if f})
    print()
    print(f"languages with files: {len(langs)}; total files analyzed: "
          f"{sum(langs.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
