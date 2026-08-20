#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Count how many times a single run parses the SAME file.

WHY (WI-madut / WI-zagij, the "straightforward" test). Persistent per-file
memoization keyed on a git blob sha is the filed way to win back the analyzer
phase, but WI-madut's own analysis lists three preconditions — per-analyzer
purity, a complete cache key, and a determinism mode — and names its failure
mode as SILENT STALE HITS. Before paying that, this asks a cheaper question with
none of those hazards:

    within ONE process, on ONE tree state, is the same file parsed more than once?

Redundant work inside a single run can be shared with a plain in-memory cache.
There is no invalidation problem (the tree cannot change mid-run), no key to get
wrong, and no staleness to leak into output. If the redundancy factor is ~1.0
there is nothing here and the persistent-cache route is the only route; if it is
2x or 3x, a large part of the phase is recoverable by construction.

HOW. ``ast.parse`` and tree-sitter's ``Parser.parse`` are wrapped for the
duration of one ``run_survey``. Calls are keyed by a digest of the SOURCE BYTES
rather than by path, for two reasons: most call sites pass only text, so a path
is not reliably available; and content-keying is what a sharing cache would
actually key on, so the measured redundancy is exactly the redundancy such a
cache could remove.

WHAT THE NUMBERS MEAN
    calls            every parse performed
    distinct         unique source texts among them
    redundancy       calls / distinct — 1.0 means nothing is re-parsed
    wasted time      time spent in parses of an already-seen text

Wasted time is the honest upper bound on what sharing could return, and it is an
UPPER bound: a shared tree must be treated as read-only by every consumer, and
any consumer that mutates its tree would need a copy instead.

USAGE
    scripts/measure-parse-redundancy.py .            # --minimal-equivalent run
    scripts/measure-parse-redundancy.py . --full     # all side outputs on
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

_PY_CALLS: Counter = Counter()
_PY_TIME: Dict[str, float] = defaultdict(float)
_TS_CALLS: Counter = Counter()
_TS_TIME: Dict[str, float] = defaultdict(float)
_PY_FIRST_SEEN_TIME = 0.0
_PY_REPEAT_TIME = 0.0
_TS_FIRST_SEEN_TIME = 0.0
_TS_REPEAT_TIME = 0.0
# Per-CALL-SITE attribution. Which pass is doing the re-parsing decides the fix:
# two sites parsing the same file is a sharing opportunity; one site parsing it
# twice is a bug in that site.
_SITE_CALLS: Counter = Counter()
_SITE_TIME: Dict[str, float] = defaultdict(float)
_SITE_REPEAT: Dict[str, float] = defaultdict(float)


def _digest(payload: Any) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8", errors="replace")
    if not isinstance(payload, (bytes, bytearray)):  # pragma: no cover - defensive
        payload = repr(payload).encode()
    return hashlib.sha1(bytes(payload)).hexdigest()[:16]  # noqa: S324


def _caller_site() -> str:
    """First hypergumbo frame above the probe — the pass actually parsing."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_code.co_filename
        if "hypergumbo" in name and "measure-parse-redundancy" not in name:
            short = name.split("hypergumbo_")[-1] if "hypergumbo_" in name else name
            return f"{short}:{frame.f_lineno}"
        frame = frame.f_back
    return "<unknown>"  # pragma: no cover - probe always called from hypergumbo


def _install_probes() -> None:
    """Wrap both parsers, attributing each call to its source digest."""
    global _PY_FIRST_SEEN_TIME, _PY_REPEAT_TIME

    real_ast_parse = ast.parse

    def counting_ast_parse(source: Any, *a: Any, **kw: Any) -> Any:
        global _PY_FIRST_SEEN_TIME, _PY_REPEAT_TIME
        key = _digest(source)
        seen = _PY_CALLS[key] > 0
        site = _caller_site()
        t0 = time.perf_counter()
        try:
            return real_ast_parse(source, *a, **kw)
        finally:
            dt = time.perf_counter() - t0
            _PY_CALLS[key] += 1
            _PY_TIME[key] += dt
            _SITE_CALLS[site] += 1
            _SITE_TIME[site] += dt
            if seen:
                _PY_REPEAT_TIME += dt
                _SITE_REPEAT[site] += dt
            else:
                _PY_FIRST_SEEN_TIME += dt

    ast.parse = counting_ast_parse  # type: ignore[assignment]

    try:
        import tree_sitter
    except ImportError:  # pragma: no cover - tree-sitter is a hard dep
        return

    real_ts_parse = tree_sitter.Parser.parse

    def counting_ts_parse(self: Any, source: Any, *a: Any, **kw: Any) -> Any:
        global _TS_FIRST_SEEN_TIME, _TS_REPEAT_TIME
        key = _digest(source)
        seen = _TS_CALLS[key] > 0
        site = _caller_site()
        t0 = time.perf_counter()
        try:
            return real_ts_parse(self, source, *a, **kw)
        finally:
            dt = time.perf_counter() - t0
            _TS_CALLS[key] += 1
            _TS_TIME[key] += dt
            _SITE_CALLS[site] += 1
            _SITE_TIME[site] += dt
            if seen:
                _TS_REPEAT_TIME += dt
                _SITE_REPEAT[site] += dt
            else:
                _TS_FIRST_SEEN_TIME += dt

    tree_sitter.Parser.parse = counting_ts_parse  # type: ignore[assignment]


def _report(name: str, calls: Counter, times: Dict[str, float],
            first_time: float, repeat_time: float, top: int) -> None:
    total_calls = sum(calls.values())
    distinct = len(calls)
    total_time = first_time + repeat_time
    print()
    print("=" * 70)
    print(f"{name}")
    print("=" * 70)
    if not total_calls:
        print("  no parses recorded")
        return
    print(f"  calls                 {total_calls}")
    print(f"  distinct sources      {distinct}")
    print(f"  REDUNDANCY FACTOR     {total_calls / max(distinct, 1):.2f}x")
    print(f"  time in parses        {total_time:.1f}s")
    print(f"  ... first parse       {first_time:.1f}s")
    print(f"  ... RE-parses         {repeat_time:.1f}s   <- recoverable by sharing")
    repeated = [(k, c) for k, c in calls.items() if c > 1]
    print(f"  sources parsed >1x    {len(repeated)} of {distinct}")
    if repeated:
        worst = sorted(repeated, key=lambda kc: -times[kc[0]])[:top]
        print(f"  heaviest repeat offenders (by total parse time):")
        for key, count in worst:
            print(f"    {key}  {count}x  {times[key]:.2f}s total")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Count repeated parses of the same source within one run.",
    )
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--out", default=None, help="Behavior-map output path.")
    ap.add_argument("--full", action="store_true",
                    help="Run with side outputs ON (default mirrors --minimal, "
                         "which is what smart-test's slice now uses).")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2

    _install_probes()
    from hypergumbo_core import cli

    lean: Dict[str, Any] = {} if args.full else {
        "no_sketch_fan_out": True,
        "enable_handler_slices": False,
        "include_sketch_precomputed": False,
    }
    print(f"running survey on {repo_root} "
          f"({'full' if args.full else 'minimal'}) ...", file=sys.stderr)
    t0 = time.perf_counter()
    cli.run_survey(
        repo_root=repo_root,
        out_path=Path(args.out).resolve() if args.out else None,
        progress=False,
        **lean,
    )
    wall = time.perf_counter() - t0

    print()
    print(f"run_survey wall: {wall:.1f}s")
    _report("PYTHON (ast.parse)", _PY_CALLS, _PY_TIME,
            _PY_FIRST_SEEN_TIME, _PY_REPEAT_TIME, args.top)
    _report("TREE-SITTER (Parser.parse)", _TS_CALLS, _TS_TIME,
            _TS_FIRST_SEEN_TIME, _TS_REPEAT_TIME, args.top)

    print()
    print("=" * 70)
    print("BY CALL SITE  (two sites on one file = share it; one site twice = a bug)")
    print("=" * 70)
    print(f"  {'site':44s} {'calls':>7s} {'total s':>9s} {'re-parse s':>11s}")
    for site, n in _SITE_CALLS.most_common(14):
        print(f"  {site:44s} {n:7d} {_SITE_TIME[site]:9.1f} "
              f"{_SITE_REPEAT[site]:11.1f}")

    recoverable = _PY_REPEAT_TIME + _TS_REPEAT_TIME
    print()
    print("=" * 70)
    print(f"RE-PARSE TIME ACROSS BOTH PARSERS: {recoverable:.1f}s "
          f"({100.0 * recoverable / max(wall, 1e-9):.1f}% of wall)")
    print("  This is what an in-run shared-tree cache could return, with no")
    print("  invalidation risk — the tree cannot change mid-process. It is an")
    print("  UPPER bound: any consumer that mutates its tree needs its own copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
