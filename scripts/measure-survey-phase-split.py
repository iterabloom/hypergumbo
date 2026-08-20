#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attribute a cold ``run_survey`` wall-clock to individual pipeline phases.

WHY THIS EXISTS (WI-balaf). ~71% of a cold run on this monorepo is *non-pass*
overhead: work that gets no ``AnalysisRun`` card, so ``analysis_runs[]`` cannot
answer "where did the time go". The figure was obtained by SUBTRACTION (wall
clock minus the sum of pass ``duration_ms``), which localises nothing — it only
says the missing time is not in a pass. This script attributes it positively.

HOW IT WORKS. ``run_survey`` already names its phases, but only as strings
passed to a nested ``show_progress(phase, pct)`` closure that formats a
progress line and throws the timing away. A closure cannot be patched from
outside, so instead of intercepting the phase MARKERS this script wraps the
phase FUNCTIONS — ``FileIndex.build``, ``run_all_analyzers``, ``finalize``,
``_emit_handler_slices``, ``format_tiered_behavior_map``,
``user_out_open_json_dump``, and the rest of the table below — each of which is
a module-level (hence patchable) callable.

INCLUSIVE VS EXCLUSIVE, AND WHY IT MATTERS HERE. The wrapped calls NEST:
``_emit_handler_slices`` and the budget-tier loop both call
``user_out_open_json_dump``, so a naive per-function total would count those
bytes two or three times and the split would over-explain the wall clock. Each
wrapper therefore pushes a frame recording the time its own wrapped children
consumed, and reports both:

    inclusive = time in the call, children included
    exclusive = inclusive - (time in wrapped children)

Only EXCLUSIVE times are summable, so only they are reconciled against the wall
clock. The residual is printed as UNATTRIBUTED rather than silently absorbed —
a phase this table does not name shows up there instead of inflating a
neighbour.

FIDELITY NOTES.
  * It calls ``run_survey(repo_root, out_path=None, progress=False)`` — the
    exact call ``_get_or_run_analysis`` makes on a cache miss, so all
    side-emission defaults (sketch fan-out, handler slices) apply. This is the
    cold path that ``hypergumbo slice --files`` pays, not a stripped variant.
  * ``out_path=None`` means the real results cache directory, on disk. Do not
    "helpfully" redirect it to /tmp: that is tmpfs here, and a 200 MB
    serialization to tmpfs is not the write this measures. ``--out`` exists for
    when you want a different target on purpose.
  * Wall clock is measured around the ``run_survey`` call only; interpreter and
    import startup are excluded (they are paid once per process either way).
  * Timing is by wrapper, not by sampling profiler: the observer cost is one
    ``perf_counter`` pair per wrapped call, and the wrapped set is coarse
    (dozens of calls, not millions), so distortion is negligible. That is the
    reason for wrapping named phases rather than reaching for cProfile, whose
    per-call overhead would land hardest on exactly the hot analyzer inner
    loops this script wants to leave undisturbed.

USAGE
    scripts/measure-survey-phase-split.py .                    # this repo
    scripts/measure-survey-phase-split.py ~/ALL_REPOS/foo/bar
    scripts/measure-survey-phase-split.py . --json split.json
"""

from __future__ import annotations

import argparse
import functools
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Modules holding phase callables. Pre-imported so that names which
# ``run_survey`` imports INSIDE its own body (sketch, taxonomy,
# framework_patterns, fingerprint) are already resolvable at patch time — an
# un-imported module has no attribute to rebind, and the local import would
# later fetch the pristine original.
_PREIMPORT = (
    "hypergumbo_core.cli",
    "hypergumbo_core.compact",
    "hypergumbo_core.discovery",
    "hypergumbo_core.entrypoints",
    "hypergumbo_core.finalize",
    "hypergumbo_core.fingerprint",
    "hypergumbo_core.framework_patterns",
    "hypergumbo_core.ir",
    "hypergumbo_core.metrics",
    "hypergumbo_core.profile",
    "hypergumbo_core.ranking",
    "hypergumbo_core.safety_zones",
    "hypergumbo_core.sketch",
    "hypergumbo_core.spec_validator",
    "hypergumbo_core.supply_chain",
    "hypergumbo_core.taxonomy",
)

# (label, attribute name). The label's prefix groups phases for the rollup;
# the attribute is located by object identity across every imported
# hypergumbo module, so the defining module need not be known here.
_TARGETS: tuple[tuple[str, str], ...] = (
    # --- setup ---
    ("setup/detect_profile", "detect_profile"),
    ("setup/detect_package_roots", "detect_package_roots"),
    # --- the passes (these DO get AnalysisRun cards) ---
    ("passes/run_all_analyzers", "run_all_analyzers"),
    ("passes/run_all_linkers", "run_all_linkers"),
    # --- graph post-processing ---
    ("graph/resolve_deferred_symbol_refs", "resolve_deferred_symbol_refs"),
    ("graph/_relativize_ir_paths", "_relativize_ir_paths"),
    ("graph/enrich_symbols", "enrich_symbols"),
    ("graph/materialize_route_symbols", "materialize_route_symbols"),
    ("graph/expand_class_based_view_routes", "expand_class_based_view_routes"),
    ("graph/populate_synthetic_class_b_identity",
     "populate_synthetic_class_b_identity"),
    ("graph/dedup_logical_synthetic_identities",
     "dedup_logical_synthetic_identities"),
    ("graph/widen_route_stable_ids", "widen_route_stable_ids"),
    ("graph/split_within_file_stable_id_collisions",
     "split_within_file_stable_id_collisions"),
    ("graph/deduplicate_edges", "deduplicate_edges"),
    ("graph/stamp_symbol_fingerprints", "stamp_symbol_fingerprints"),
    ("graph/_classify_symbols", "_classify_symbols"),
    ("graph/strip_test_file_only_concepts", "strip_test_file_only_concepts"),
    ("graph/create_boundary_nodes", "create_boundary_nodes"),
    ("graph/rank_symbols", "rank_symbols"),
    ("graph/finalize", "finalize"),
    # Nested inside finalize (ADR-0043 §6 moved them there); wrapping them
    # separately keeps finalize's own exclusive time honest.
    ("graph/validate_ir", "validate_ir"),
    ("graph/compute_repo_fingerprint", "compute_repo_fingerprint"),
    ("graph/compute_metrics", "compute_metrics"),
    ("graph/detect_entrypoints", "detect_entrypoints"),
    ("graph/emit_stderr_summary", "emit_stderr_summary"),
    # --- supply chain summary ---
    ("supply/_compute_supply_chain_summary", "_compute_supply_chain_summary"),
    ("supply/_find_derived_skipped", "_find_derived_skipped"),
    # --- sketch pre-computation (embedding model + ripgrep territory) ---
    ("sketch/_extract_config_info", "_extract_config_info"),
    ("sketch/_extract_readme_description", "_extract_readme_description"),
    ("sketch/additional_file_candidates", "additional_file_candidates"),
    ("sketch/compute_raw_in_degree", "compute_raw_in_degree"),
    ("sketch/compute_symbol_mention_centrality_batch",
     "compute_symbol_mention_centrality_batch"),
    # --- side outputs: the things `slice --files` never reads ---
    ("sideout/_emit_handler_slices", "_emit_handler_slices"),
    ("sideout/format_tiered_behavior_map", "format_tiered_behavior_map"),
    # --- serialization (nested inside the two above AND the main write) ---
    ("io/user_out_open_json_dump", "user_out_open_json_dump"),
)

_STACK: List[Dict[str, float]] = []
_TOTALS: Dict[str, Dict[str, float]] = {}


def _timed(label: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``fn`` to accumulate inclusive and exclusive wall-clock."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        frame: Dict[str, float] = {"child": 0.0}
        _STACK.append(frame)
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            incl = time.perf_counter() - t0
            _STACK.pop()
            rec = _TOTALS.setdefault(label, {"incl": 0.0, "excl": 0.0, "n": 0.0})
            rec["incl"] += incl
            rec["excl"] += incl - frame["child"]
            rec["n"] += 1
            # Charge our whole inclusive span to the nearest wrapped ancestor
            # so its exclusive time excludes us.
            if _STACK:
                _STACK[-1]["child"] += incl

    return wrapper


def _hypergumbo_modules() -> List[Any]:
    """Every imported module in the hypergumbo namespace."""
    return [
        mod for name, mod in list(sys.modules.items())
        if mod is not None and name.startswith("hypergumbo")
    ]


def _patch_targets() -> tuple[List[str], List[str]]:
    """Wrap every locatable target in every module that holds it.

    Returns (patched_labels, missing_labels).

    A target is located by object IDENTITY, and every DISTINCT object found
    under the name gets its own wrapper sharing the label. Taking only the
    first name match is wrong and was silently wrong here: two different
    functions are named ``run_all_analyzers`` (``analyze/registry.py`` and
    ``analyze/all_analyzers.py``). The probe patched the registry one, ``cli``
    calls the other, and the analyzer phase read 0.0s while the pass cards it
    was supposed to reconcile against read 0.4s. Wrapping every homonym means
    whichever binding the call site holds is timed.

    Double-counting is not a risk even when one homonym calls the other: the
    inner call is charged to the outer frame's ``child``, so exclusive time —
    the only figure summed against the wall clock — stays correct.
    """
    patched: List[str] = []
    missing: List[str] = []
    mods = _hypergumbo_modules()
    for label, attr in _TARGETS:
        originals: List[Any] = []
        for mod in mods:
            candidate = getattr(mod, attr, None)
            if callable(candidate) and not any(candidate is o for o in originals):
                originals.append(candidate)
        if not originals:
            missing.append(f"{label} ({attr})")
            continue
        for original in originals:
            wrapper = _timed(label, original)
            for mod in mods:
                if getattr(mod, attr, None) is original:
                    setattr(mod, attr, wrapper)
        patched.append(
            label if len(originals) == 1 else f"{label} x{len(originals)}"
        )
    return patched, missing


def _patch_file_index_build() -> bool:
    """Wrap ``FileIndex.build`` — a classmethod, so it needs its own handling.

    ``getattr(FileIndex, "build")`` yields a *bound* classmethod; wrapping that
    bound object and reinstalling it as a ``staticmethod`` keeps the call form
    ``FileIndex.build(repo_root, excludes=...)`` working unchanged.
    """
    try:
        discovery = importlib.import_module("hypergumbo_core.discovery")
        bound = discovery.FileIndex.build
    except (ImportError, AttributeError):  # pragma: no cover - defensive
        return False
    discovery.FileIndex.build = staticmethod(  # type: ignore[method-assign]
        _timed("setup/FileIndex.build", bound)
    )
    return True


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _pass_totals(runs: List[Any]) -> Dict[str, float]:
    """Sum ``duration_ms`` per pass, by pass id.

    ``run_all_analyzers`` returns ``list[dict]`` (already serialized), not
    ``AnalysisRun`` objects, and ``AnalysisRun.to_dict`` renames ``pass_id`` to
    ``pass`` on the way out. Reading only the dataclass attribute made every
    row ``<unknown>`` at 0.0s — a silent zero that would have made the
    pass/non-pass reconciliation meaningless. Handle both shapes.
    """
    out: Dict[str, float] = {}
    for run in runs:
        if isinstance(run, dict):
            pass_id = run.get("pass") or run.get("pass_id") or "<unknown>"
            duration = run.get("duration_ms") or 0
        else:
            pass_id = getattr(run, "pass_id", None) or "<unknown>"
            duration = getattr(run, "duration_ms", 0) or 0
        out[pass_id] = out.get(pass_id, 0.0) + duration / 1000.0
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Attribute a cold run_survey wall-clock to pipeline phases.",
    )
    ap.add_argument("repo", help="Repository root to analyze.")
    ap.add_argument(
        "--out", default=None,
        help="Behavior-map output path. Default: the real results cache dir "
             "(the faithful cold-slice target; do NOT point this at tmpfs if "
             "you care about the serialization number).",
    )
    ap.add_argument("--json", dest="json_out", default=None,
                    help="Also write the split as JSON to this path.")
    ap.add_argument(
        "--lean", action="store_true",
        help="Counterfactual arm: run with the side outputs a caller like "
             "`slice --files` never reads turned OFF (no sketch fan-out, no "
             "handler slices, no sketch pre-computation). Pair it with a "
             "default run on the same tree to price what declining them buys. "
             "This is a MEASUREMENT arm, not a proposed default — other "
             "consumers (sketch) do read those blocks.",
    )
    ap.add_argument("--top", type=int, default=12,
                    help="How many individual passes to list (default 12).")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2

    for name in _PREIMPORT:
        importlib.import_module(name)

    patched, missing = _patch_targets()
    if _patch_file_index_build():
        patched.append("setup/FileIndex.build")

    # Capture the AnalysisRun cards on their way through finalize, so the
    # pass/non-pass reconciliation does not require re-reading a 200 MB survey.
    captured_runs: List[Any] = []
    finalize_mod = importlib.import_module("hypergumbo_core.finalize")
    real_finalize = finalize_mod.finalize

    def capturing_finalize(ctx: Any) -> Any:
        captured_runs.extend(list(getattr(ctx, "analysis_runs", []) or []))
        return real_finalize(ctx)

    for mod in _hypergumbo_modules():
        if getattr(mod, "finalize", None) is real_finalize:
            setattr(mod, "finalize", capturing_finalize)

    cli = importlib.import_module("hypergumbo_core.cli")

    print(f"repo:      {repo_root}", file=sys.stderr)
    print(f"wrapped:   {len(patched)} phase callables", file=sys.stderr)
    if missing:
        print(f"NOT FOUND: {', '.join(missing)}", file=sys.stderr)
    arm = "LEAN (side outputs OFF)" if args.lean else "default (all side outputs ON)"
    print(f"running cold survey, arm = {arm}...", file=sys.stderr)

    out_path = Path(args.out).resolve() if args.out else None
    lean_kwargs: Dict[str, Any] = {}
    if args.lean:
        lean_kwargs = {
            "no_sketch_fan_out": True,
            "enable_handler_slices": False,
            "include_sketch_precomputed": False,
        }
    t0 = time.perf_counter()
    generated = cli.run_survey(
        repo_root=repo_root, out_path=out_path, progress=False, **lean_kwargs,
    )
    wall = time.perf_counter() - t0

    pass_times = _pass_totals(captured_runs)
    pass_total = sum(pass_times.values())
    attributed = sum(rec["excl"] for rec in _TOTALS.values())
    unattributed = wall - attributed

    rows = sorted(
        ((label, rec) for label, rec in _TOTALS.items()),
        key=lambda kv: kv[1]["excl"], reverse=True,
    )

    def pct(x: float) -> str:
        return f"{100.0 * x / wall:5.1f}%" if wall > 0 else "  n/a"

    print()
    print("=" * 78)
    print(f"COLD run_survey WALL CLOCK: {wall:.1f}s   arm={arm}")
    print(f"  {repo_root}")
    print("=" * 78)
    print()
    print(f"{'phase':52s} {'excl s':>8s} {'share':>7s} {'incl s':>8s} {'n':>4s}")
    print("-" * 78)
    for label, rec in rows:
        print(f"{label:52s} {rec['excl']:8.1f} {pct(rec['excl']):>7s} "
              f"{rec['incl']:8.1f} {int(rec['n']):4d}")
    print("-" * 78)
    print(f"{'ATTRIBUTED (sum of exclusive)':52s} {attributed:8.1f} "
          f"{pct(attributed):>7s}")
    print(f"{'UNATTRIBUTED (residual)':52s} {unattributed:8.1f} "
          f"{pct(unattributed):>7s}")
    print()

    # A wrapped phase that never fired is a control signal, not a 0.0s row:
    # it means the phase did not run on this repo (or the wrapper missed the
    # binding the call site holds). Say so rather than letting absence read as
    # "costs nothing".
    never_called = [
        label for label, _ in _TARGETS if label not in _TOTALS
    ] + ([] if "setup/FileIndex.build" in _TOTALS else ["setup/FileIndex.build"])
    if never_called:
        print(f"WRAPPED BUT NEVER CALLED ({len(never_called)}): "
              f"{', '.join(never_called)}")
        print()

    # Group rollup: the decision-grade view.
    groups: Dict[str, float] = {}
    for label, rec in _TOTALS.items():
        groups[label.split("/", 1)[0]] = (
            groups.get(label.split("/", 1)[0], 0.0) + rec["excl"]
        )
    print("ROLLUP BY GROUP")
    for group, secs in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"  {group:12s} {secs:8.1f}s {pct(secs):>7s}")
    print(f"  {'unattrib':12s} {unattributed:8.1f}s {pct(unattributed):>7s}")
    print()

    print(f"PASS CARDS: {len(captured_runs)} runs, "
          f"{pass_total:.1f}s of duration_ms in total ({pct(pass_total)} of wall)")
    if captured_runs and pass_total <= 0.0:
        print("  !! WARNING: cards exist but every duration_ms is 0 — the "
              "accessor is reading the wrong field, or the timers are unwired. "
              "Do NOT read the pass/non-pass split off this run.")
    for pass_id, secs in sorted(pass_times.items(), key=lambda kv: -kv[1])[:args.top]:
        print(f"  {pass_id:44s} {secs:8.1f}s")
    print()

    print("ARTIFACTS WRITTEN")
    total_bytes = 0
    for path in generated:
        size = _dir_bytes(Path(path))
        total_bytes += size
        print(f"  {size / 1e6:9.1f} MB  {path}")
    slice_dirs = {Path(p).parent for p in generated
                  if Path(p).parent.name.endswith(".slices")}
    for sdir in slice_dirs:
        print(f"  (slice dir: {sdir})")
    print(f"  {total_bytes / 1e6:9.1f} MB  TOTAL across "
          f"{len(generated)} artifact(s)")

    if args.json_out:
        payload = {
            "repo": str(repo_root),
            "arm": "lean" if args.lean else "default",
            "wall_s": wall,
            "attributed_s": attributed,
            "unattributed_s": unattributed,
            "pass_card_total_s": pass_total,
            "phases": {
                label: {"exclusive_s": rec["excl"], "inclusive_s": rec["incl"],
                        "calls": int(rec["n"])}
                for label, rec in _TOTALS.items()
            },
            "passes": pass_times,
            "artifacts": {str(p): _dir_bytes(Path(p)) for p in generated},
            "not_found": missing,
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
