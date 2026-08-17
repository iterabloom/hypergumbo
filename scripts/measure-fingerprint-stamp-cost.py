#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Price the ``stamp_symbol_fingerprints`` post-pass and prove its cost model.

WHY (WI-balaf). ``scripts/measure-survey-phase-split.py`` attributed 324s of a
517s cold ``run_survey`` on this monorepo to ONE non-pass function,
``fingerprint.stamp_symbol_fingerprints`` — 62.6% of wall, and invisible in
``analysis_runs[]`` because it is a post-pass with no AnalysisRun card. That
localises the cost but does not explain it, and an unexplained hot spot is a
correlation: it names a line, not a mechanism.

THE MECHANISM THIS SCRIPT TESTS. ``stamp_symbol_fingerprints`` caches one
parsed tree per (path, language), so each file is PARSED once — the docstring's
claim, and it holds. But the per-symbol step
(``_python_context_fingerprint`` / ``_tree_sitter_context_fingerprint``)
locates the smallest node covering the symbol's span by walking the ENTIRE
cached tree. So the pass is:

    cost(file) ~= parse(file) + symbols(file) * nodes(file)

i.e. the tree cache eliminated repeated PARSING but not repeated LOCATION, and
the located-per-symbol full-tree scan makes the pass quadratic in file size:
a big file is expensive twice over, once for having many AST nodes and again
for having many symbols that each rescan them.

HOW IT IS TESTED — prediction, not just measurement. A hot-spot claim is
falsifiable only if it predicts the number it is supposed to explain:

  1. Measure the per-call cost of the locator directly, on real repository
     files, across a range of file sizes. If the model is right, per-call cost
     rises with the file's AST node count (and the fitted ns/node is roughly
     constant across files).
  2. Read the ACTUAL symbols-per-file distribution out of a written survey, so
     the multiplier is production's own, not an assumption.
  3. Predict total = sum over files of symbols(f) * measured_cost_per_call(f),
     and compare against the 324s the phase-split attributed. Reconciling to
     the observed total is what turns "this function is slow" into "this
     function is slow FOR THIS REASON".

A prediction that misses badly is the useful outcome too: it means the cost
lives somewhere else inside the pass (path resolution, file reads, the
tree-sitter branch) and the fix would have been aimed at the wrong line.

USAGE
    scripts/measure-fingerprint-stamp-cost.py --survey <path/to/survey.json>
    scripts/measure-fingerprint-stamp-cost.py --survey ... --samples 24
"""

from __future__ import annotations

import argparse
import ast
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_symbol_shape(
    survey_path: Path, repo_root: Path,
) -> Tuple[Dict[str, int], Counter, Counter]:
    """Return (py_symbols_per_file, per_language_symbols, per_language_files).

    Counts only symbols the pass would actually try to stamp: a path, a usable
    span, and a language. Counting every node would inflate the multiplier with
    rows the pass skips in its first three guards.
    """
    with survey_path.open() as fh:
        survey = json.load(fh)

    py_per_file: Dict[str, int] = defaultdict(int)
    lang_symbols: Counter = Counter()
    lang_files: Dict[str, set] = defaultdict(set)

    for node in survey.get("nodes", []):
        path = node.get("path")
        span = node.get("span")
        language = node.get("language")
        if not path or not language or not isinstance(span, dict):
            continue
        start = span.get("start_line")
        end = span.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start <= 0 or end < start:
            continue
        lang_symbols[language] += 1
        lang_files[language].add(path)
        if language == "python":
            py_per_file[path] += 1

    return dict(py_per_file), lang_symbols, Counter(
        {lang: len(paths) for lang, paths in lang_files.items()}
    )


def _ast_node_count(tree: ast.AST) -> int:
    return sum(1 for _ in ast.walk(tree))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Price stamp_symbol_fingerprints and validate its cost model.",
    )
    ap.add_argument("--survey", required=True,
                    help="A written survey.json to read the real "
                         "symbols-per-file distribution from.")
    ap.add_argument("--repo", default=".", help="Repository root.")
    ap.add_argument("--samples", type=int, default=16,
                    help="Python files to benchmark the locator on, chosen "
                         "across the size range (default 16).")
    ap.add_argument("--calls", type=int, default=25,
                    help="Locator calls per sampled file (default 25).")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    survey_path = Path(args.survey).resolve()
    if not survey_path.is_file():
        print(f"error: no survey at {survey_path}", file=sys.stderr)
        return 2

    from hypergumbo_core.fingerprint import _python_context_fingerprint

    print(f"reading symbol shape from {survey_path} "
          f"({survey_path.stat().st_size / 1e6:.0f} MB)...", file=sys.stderr)
    py_per_file, lang_symbols, lang_files = _load_symbol_shape(
        survey_path, repo_root,
    )

    print()
    print("=" * 78)
    print("SYMBOLS THE PASS WOULD STAMP, BY LANGUAGE")
    print("=" * 78)
    print(f"{'language':22s} {'symbols':>10s} {'files':>8s} {'sym/file':>10s}")
    for lang, count in lang_symbols.most_common(15):
        files = lang_files[lang]
        print(f"{lang:22s} {count:10d} {files:8d} {count / max(files, 1):10.1f}")
    total_symbols = sum(lang_symbols.values())
    print(f"{'TOTAL':22s} {total_symbols:10d} {sum(lang_files.values()):8d}")
    print()

    # --- benchmark the locator across the real file-size range -------------
    candidates = [
        (path, count) for path, count in py_per_file.items()
        if (repo_root / path).is_file()
    ]
    if not candidates:
        print("error: no python files from the survey exist on disk "
              "(wrong --repo?)", file=sys.stderr)
        return 2
    candidates.sort(key=lambda pc: (repo_root / pc[0]).stat().st_size)
    step = max(1, len(candidates) // args.samples)
    sampled = candidates[::step][: args.samples]
    # Always include the largest file: it is where a quadratic model and a
    # linear one disagree most, so excluding it would hide the effect.
    if candidates[-1] not in sampled:
        sampled.append(candidates[-1])

    print("=" * 78)
    print("LOCATOR COST PER CALL vs FILE AST SIZE  (_python_context_fingerprint)")
    print("=" * 78)
    print(f"{'file':46s} {'lines':>6s} {'nodes':>8s} {'ms/call':>9s} {'ns/node':>8s}")
    rows: List[Tuple[str, int, int, float]] = []
    for path, _sym_count in sampled:
        abs_path = repo_root / path
        try:
            source = abs_path.read_bytes()
            tree = ast.parse(source.decode("utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        nodes = _ast_node_count(tree)
        n_lines = source.count(b"\n") + 1
        # Query spans spread through the file so the sample is not biased to
        # cheap early-exit positions.
        # (start_line, end_line) pairs — the locator takes plain ints, not a
        # Span; constructing one needs start_col/end_col it never reads.
        spans = []
        for i in range(1, args.calls + 1):
            start = max(1, int(n_lines * i / (args.calls + 1)))
            spans.append((start, min(n_lines, start + 3)))
        t0 = time.perf_counter()
        for start, end in spans:
            _python_context_fingerprint(tree, start, end)
        elapsed = time.perf_counter() - t0
        ms_per_call = 1000.0 * elapsed / len(spans)
        ns_per_node = 1e9 * elapsed / len(spans) / max(nodes, 1)
        rows.append((path, n_lines, nodes, ms_per_call))
        shown = path if len(path) <= 46 else "..." + path[-43:]
        print(f"{shown:46s} {n_lines:6d} {nodes:8d} {ms_per_call:9.3f} "
              f"{ns_per_node:8.1f}")

    if not rows:
        print("error: no sampled file parsed", file=sys.stderr)
        return 2

    ns_per_node_all = [
        1e6 * ms / max(nodes, 1) for _p, _l, nodes, ms in rows
    ]
    print()
    print(f"ns/node across {len(rows)} files: "
          f"median {statistics.median(ns_per_node_all):.1f}, "
          f"min {min(ns_per_node_all):.1f}, max {max(ns_per_node_all):.1f}")
    print("  (a roughly constant ns/node is the signature of a per-call "
          "FULL-TREE scan: cost tracks the tree, not the span)")
    print()

    # --- predict the whole-pass cost from the model ------------------------
    median_ns_per_node = statistics.median(ns_per_node_all)
    predicted = 0.0
    parse_total = 0.0
    node_total = 0
    priced_files = 0
    for path, sym_count in candidates:
        abs_path = repo_root / path
        try:
            source = abs_path.read_bytes()
            t0 = time.perf_counter()
            tree = ast.parse(source.decode("utf-8", errors="replace"))
            parse_total += time.perf_counter() - t0
            nodes = _ast_node_count(tree)
        except (OSError, SyntaxError, ValueError):
            continue
        node_total += nodes
        priced_files += 1
        predicted += sym_count * nodes * median_ns_per_node / 1e9

    print("=" * 78)
    print("PREDICTION FROM THE COST MODEL  (python branch only)")
    print("=" * 78)
    print(f"  python files priced            {priced_files}")
    print(f"  total AST nodes                {node_total}")
    print(f"  python symbols to stamp        {lang_symbols.get('python', 0)}")
    print(f"  one-off parse cost (measured)  {parse_total:8.1f}s")
    print(f"  PREDICTED locate cost          {predicted:8.1f}s"
          f"   = sum(symbols_f * nodes_f) * {median_ns_per_node:.1f} ns")
    print(f"  PREDICTED python-branch total  {parse_total + predicted:8.1f}s")
    print()
    print("  Compare against the exclusive time the phase-split attributed to")
    print("  graph/stamp_symbol_fingerprints. Reconciling means the quadratic")
    print("  locate is the mechanism; a large shortfall means the cost is")
    print("  elsewhere in the pass (non-python branch, file reads, path")
    print("  resolution) and the fix must be aimed there instead.")
    print()

    worst = sorted(
        ((p, c) for p, c in candidates),
        key=lambda pc: -pc[1],
    )[:10]
    print("TOP FILES BY SYMBOL COUNT (the quadratic term's biggest contributors)")
    for path, sym_count in worst:
        abs_path = repo_root / path
        try:
            tree = ast.parse(
                abs_path.read_bytes().decode("utf-8", errors="replace")
            )
            nodes = _ast_node_count(tree)
        except (OSError, SyntaxError, ValueError):  # pragma: no cover
            continue
        cost = sym_count * nodes * median_ns_per_node / 1e9
        shown = path if len(path) <= 52 else "..." + path[-49:]
        print(f"  {shown:52s} {sym_count:5d} sym x {nodes:6d} nodes "
              f"= {cost:7.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
