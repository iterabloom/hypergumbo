# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slow test masking for smart-test.

Uses the behavior map's symbol-level call graph to identify slow tests
that do not connect to changed code, allowing smart-test to deselect
them via ``pytest --deselect``.

How It Works
------------
1. Load the cached behavior map (nodes + edges).
2. Identify symbols in changed source files.
3. Reverse-BFS from changed symbols → ``affected_set`` (all transitively
   dependent symbol IDs).
4. For each slow test (>threshold mean duration) in the affected test
   files, check if the test's node ID is in ``affected_set``.
5. If not → emit ``--deselect=<pytest_node_id>``.

Fast tests (<threshold) always run unconditionally because their
aggregate cost is negligible.

Why This Design
---------------
- The behavior map already captures ~13K test nodes with outgoing call
  edges to production code.  The reverse BFS is the same algorithm used
  by ``slice --files``, operating on the in-memory graph (~57K edges)
  in microseconds.
- Test timings come from ``test_timings.json``, already maintained by
  smart-test.  No separate "slow list" file to maintain.
- Graceful degradation: missing behavior map or timings → no masking,
  all selected tests run as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO


# Default threshold: tests with mean duration above this are candidates
# for masking.  100ms chosen because ~90% of tests are below this.
DEFAULT_THRESHOLD_SECONDS = 0.1


def find_latest_behavior_map(repo_root: Path) -> Path | None:
    """Find the most recent cached behavior map for a repository.

    Searches ``~/.cache/hypergumbo/<fingerprint>/results/*/`` for the
    newest ``hypergumbo.results.json``.  Uses the same fingerprinting
    as ``sketch_embeddings._get_repo_fingerprint``.

    A slightly stale behavior map is acceptable — graph structure
    evolves slowly between commits.

    Returns None if no cached behavior map exists.
    """
    from .sketch_embeddings import _get_repo_fingerprint, _get_xdg_cache_base

    fingerprint = _get_repo_fingerprint(repo_root)
    cache_base = _get_xdg_cache_base()
    results_dir = cache_base / fingerprint / "results"

    if not results_dir.is_dir():
        return None

    newest: Path | None = None
    newest_mtime = 0.0

    for state_dir in results_dir.iterdir():
        candidate = state_dir / "hypergumbo.results.json"
        if candidate.is_file():
            try:
                mtime = candidate.stat().st_mtime
                if mtime > newest_mtime:
                    newest = candidate
                    newest_mtime = mtime
            except OSError:  # pragma: no cover
                continue

    return newest


def load_test_timings(
    timings_path: Path,
    threshold: float = DEFAULT_THRESHOLD_SECONDS,
) -> dict[str, float]:
    """Load test timings and return slow tests with their mean durations.

    Args:
        timings_path: Path to ``test_timings.json``.
        threshold: Only return tests with mean duration above this.

    Returns:
        Dict mapping pytest node ID → mean duration in seconds.
        Only includes tests above the threshold.
    """
    data = json.loads(timings_path.read_text())

    slow: dict[str, float] = {}
    for test_id, info in data.items():
        runs = info.get("runs", [])
        if not runs:
            continue
        mean_secs = sum(r["seconds"] for r in runs) / len(runs)
        if mean_secs >= threshold:
            slow[test_id] = mean_secs

    return slow


def _node_to_pytest_id(name: str, path: str) -> str:
    """Convert a behavior map node to a pytest node ID.

    Behavior map stores methods as ``TestClass.test_method`` with
    ``path="packages/.../test_foo.py"``.  Pytest uses
    ``packages/.../test_foo.py::TestClass::test_method``.

    Standalone functions are stored as ``test_func`` with the same path
    convention.
    """
    if "." in name:
        # Method: TestClass.test_method → path::TestClass::test_method
        class_name, method_name = name.split(".", 1)
        return f"{path}::{class_name}::{method_name}"
    else:
        # Standalone function: test_func → path::test_func
        return f"{path}::{name}"


class MaskResult:
    """Result of slow test masking computation.

    Attributes:
        deselections: List of ``--deselect=<pytest_node_id>`` strings.
        estimated_seconds_saved: Sum of mean durations of deselected tests.
        estimated_seconds_kept: Sum of mean durations of kept slow tests.
        total_slow_in_scope: Number of slow tests in affected files.
    """

    __slots__ = (
        "deselections",
        "estimated_seconds_kept",
        "estimated_seconds_saved",
        "total_slow_in_scope",
    )

    def __init__(
        self,
        deselections: list[str],
        estimated_seconds_saved: float,
        estimated_seconds_kept: float,
        total_slow_in_scope: int,
    ) -> None:
        self.deselections = deselections
        self.estimated_seconds_saved = estimated_seconds_saved
        self.estimated_seconds_kept = estimated_seconds_kept
        self.total_slow_in_scope = total_slow_in_scope


def compute_deselections(
    behavior_map_path: Path,
    changed_files: list[str],
    timings_path: Path,
    affected_test_files: list[str],
    threshold: float = DEFAULT_THRESHOLD_SECONDS,
) -> MaskResult:
    """Compute pytest --deselect arguments for slow unconnected tests.

    Args:
        behavior_map_path: Path to ``hypergumbo.results.json``.
        changed_files: List of changed source file paths (relative).
        timings_path: Path to ``test_timings.json``.
        affected_test_files: List of test file paths selected by
            ``slice --files``.
        threshold: Duration threshold in seconds.

    Returns:
        MaskResult with deselection args and summary stats.
    """
    from .paths import normalize_path, path_ends_with

    # Load behavior map
    bmap = json.loads(behavior_map_path.read_text())
    nodes = bmap.get("nodes", [])
    edges = bmap.get("edges", [])

    empty = MaskResult([], 0.0, 0.0, 0)

    if not nodes or not edges:
        return empty

    # Build file → node mapping
    file_to_nodes: dict[str, list[dict]] = {}
    for node in nodes:
        npath = node.get("path", "")
        if npath:
            norm = normalize_path(npath)
            file_to_nodes.setdefault(norm, []).append(node)

    # Find changed symbols (same logic as _handle_files_mode)
    changed_node_ids: set[str] = set()
    for changed_file in changed_files:
        changed_norm = normalize_path(changed_file)
        # Exact match
        if changed_norm in file_to_nodes:
            changed_node_ids.update(n["id"] for n in file_to_nodes[changed_norm])
            continue
        # Suffix match
        for file_path, file_nodes in file_to_nodes.items():
            if path_ends_with(file_path, changed_norm) or path_ends_with(changed_norm, file_path):
                changed_node_ids.update(n["id"] for n in file_nodes)

    if not changed_node_ids:
        return empty

    # Reverse BFS from changed symbols to find all affected nodes
    reverse_index: dict[str, list[str]] = {}
    for edge in edges:
        reverse_index.setdefault(edge["dst"], []).append(edge["src"])

    affected: set[str] = set()
    current_level = set(changed_node_ids)
    max_hops = 10

    for _ in range(max_hops):
        if not current_level:
            break
        next_level: set[str] = set()
        for node_id in current_level:
            if node_id in affected:  # pragma: no cover — defensive dedup
                continue
            affected.add(node_id)
            for caller_id in reverse_index.get(node_id, []):
                if caller_id not in affected:
                    next_level.add(caller_id)
        current_level = next_level

    # Load slow test timings
    slow_tests = load_test_timings(timings_path, threshold)
    if not slow_tests:
        return empty

    # Normalize affected test file paths for matching
    affected_test_norms = {normalize_path(f) for f in affected_test_files}

    # Find test nodes in affected files that are slow and NOT affected
    deselections: list[str] = []
    total_slow_in_scope = 0
    time_saved = 0.0
    time_kept = 0.0

    for node in nodes:
        npath = node.get("path", "")
        if not npath:
            continue

        norm_path = normalize_path(npath)
        # Must be in an affected test file
        if norm_path not in affected_test_norms:
            # Try suffix match against affected test files
            in_affected = False
            for atf in affected_test_norms:
                if path_ends_with(norm_path, atf) or path_ends_with(atf, norm_path):
                    in_affected = True
                    break
            if not in_affected:
                continue

        # Must be a test function or method
        name = node.get("name", "")
        kind = node.get("kind", "")
        is_test = False
        if kind == "function" and name.startswith("test_"):
            is_test = True
        elif kind == "method" and ".test_" in name:
            is_test = True

        if not is_test:
            continue

        # Convert to pytest node ID
        pytest_id = _node_to_pytest_id(name, npath)

        # Must be slow
        if pytest_id not in slow_tests:
            continue

        total_slow_in_scope += 1

        # If NOT in the affected set → deselect
        if node["id"] not in affected:
            deselections.append(f"--deselect={pytest_id}")
            time_saved += slow_tests[pytest_id]
        else:
            time_kept += slow_tests[pytest_id]

    return MaskResult(
        deselections=sorted(deselections),
        estimated_seconds_saved=time_saved,
        estimated_seconds_kept=time_kept,
        total_slow_in_scope=total_slow_in_scope,
    )


def main(
    argv: list[str] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """CLI entry point for slow test masking.

    Writes ``--deselect`` arguments to stdout (one per line) and a
    human-readable summary to stderr showing how many tests were masked
    and estimated time saved.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).
        stdout: Output stream for deselect arguments.
        stderr: Output stream for summary stats.

    Returns:
        0 on success (including graceful degradation with no output),
        1 on usage error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Compute pytest --deselect args for slow unconnected tests",
    )
    parser.add_argument(
        "--changed-files",
        required=True,
        help="File listing changed source files (one per line)",
    )
    parser.add_argument(
        "--timings",
        required=True,
        help="Path to test_timings.json",
    )
    parser.add_argument(
        "--affected-tests",
        required=True,
        help="File listing affected test files (one per line)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_SECONDS,
        help=f"Duration threshold in seconds (default: {DEFAULT_THRESHOLD_SECONDS})",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory)",
    )
    parser.add_argument(
        "--behavior-map",
        default=None,
        help="Path to behavior map JSON (default: auto-discover from cache)",
    )

    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    timings_path = Path(args.timings)
    changed_files_path = Path(args.changed_files)
    affected_tests_path = Path(args.affected_tests)

    # Validate inputs — degrade gracefully
    if not timings_path.is_file():
        return 0
    if not changed_files_path.is_file():
        return 0
    if not affected_tests_path.is_file():
        return 0

    # Find behavior map
    if args.behavior_map:
        bmap_path = Path(args.behavior_map)
    else:
        bmap_path = find_latest_behavior_map(repo_root)

    if bmap_path is None or not bmap_path.is_file():
        return 0

    # Read input files
    changed_files = [
        line.strip()
        for line in changed_files_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    affected_test_files = [
        line.strip()
        for line in affected_tests_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not changed_files or not affected_test_files:
        return 0

    try:
        result = compute_deselections(
            behavior_map_path=bmap_path,
            changed_files=changed_files,
            timings_path=timings_path,
            affected_test_files=affected_test_files,
            threshold=args.threshold,
        )
    except Exception:  # pragma: no cover — defensive against corrupt data
        return 0

    for d in result.deselections:
        stdout.write(d + "\n")

    if result.deselections:
        masked = len(result.deselections)
        kept = result.total_slow_in_scope - masked
        saved = result.estimated_seconds_saved

        def _fmt_time(secs: float) -> str:
            if secs >= 60:
                return f"{secs / 60:.1f}m"
            return f"{secs:.1f}s"

        parts = [
            f"masked {masked} slow unconnected tests",
            f"~{_fmt_time(saved)} saved",
            f"{kept} slow tests kept",
        ]

        # Estimate wall-clock time for kept slow tests
        if result.estimated_seconds_kept > 0:
            import os
            cpus = os.cpu_count() or 1
            wall_est = result.estimated_seconds_kept / cpus
            parts.append(f"~{_fmt_time(wall_est)} est. wall time @ {cpus} CPUs")

        stderr.write(f"  ({'; '.join(parts)})\n")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
