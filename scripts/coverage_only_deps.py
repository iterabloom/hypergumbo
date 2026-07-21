#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Coverage-only test-dependency augmentation for ``scripts/smart-test`` (WI-zaziz).

``smart-test`` selects the tests to run via ``hypergumbo slice --files`` — an
IMPORT-dependency reverse slice. That correctly finds every test that *imports*
a changed source file (directly or transitively), but it is blind to a distinct
class of dependency: a test that COVERS a source file through the analysis
pipeline without ever importing it.

The motivating case (the filed WI-zaziz repro): a change to
``hypergumbo_lang_mainstream/java.py`` selects the java-specific tests
(``test_java.py`` etc.) but NOT ``test_polyglot_call_site_coverage.py`` — the
sole test that covers ``java.py``'s static-import ``ExternalRef`` branch
(``java.py:1872-1878``). That polyglot test exercises the branch by running the
analyzer end-to-end, so there is no import edge for the slice to follow. The
result is a false ``<100%`` on CI's changed-file coverage gate even though the
full suite is 100% — and the manual workaround (add the test to the manifest by
hand) does not persist, because ``auto-pr`` regenerates the manifest from the
commit diff via ``smart-test --manifest``.

The fix is a checked-in DECLARATIVE map, ``.ci/coverage-only-deps.txt``, of
``<source-path> <covering-test-path...>`` pairs (repo-root-relative,
whitespace-separated; ``#`` comments and blank lines ignored). For any changed
source file present as a map key, this helper emits the mapped covering
test(s) — filtered to tests that (a) exist on disk (a stale entry can never
inject a phantom test path that would make pytest error) and (b) are not
already in the current selection. Because the augmentation lives in
``smart-test`` itself, the regenerated manifest ``auto-pr`` produces includes
the covering tests too, so the fix persists across rebases.

CLI: ``coverage_only_deps.py REPO_ROOT --map PATH [--tests-file PATH] < CHANGED``.
Reads newline-separated changed source paths from stdin (repo-root-relative).
Reads the current affected-tests selection from ``--tests-file`` (a missing file
is an empty selection). A missing ``--map`` file yields no additions. Writes the
additional test paths (one per line, sorted, deduplicated) to stdout. Always
exits 0 unless argv is malformed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set


def parse_map(text: str) -> Dict[str, List[str]]:
    """Parse the coverage-only-deps map text into ``{source: [test, ...]}``.

    Each non-blank, non-comment (``#``) line is whitespace-split into a source
    path followed by one or more covering-test paths. A line with fewer than
    two tokens (a source with no test) is meaningless and silently dropped.
    Repeated source keys accumulate (order preserved, duplicates kept out).
    """
    dep_map: Dict[str, List[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        source, tests = tokens[0], tokens[1:]
        bucket = dep_map.setdefault(source, [])
        for t in tests:
            if t not in bucket:
                bucket.append(t)
    return dep_map


def compute_adds(
    *,
    repo_root: Path,
    changed_source_files: Iterable[str],
    affected_tests: Iterable[str],
    dep_map: Dict[str, List[str]],
) -> List[str]:
    """Return covering-test paths to add, sorted and deduplicated.

    A path is included only if (a) its source is in the changed set AND the
    map, (b) the test file exists under ``repo_root`` (defensive against a
    stale map entry), and (c) the test is not already selected.
    """
    affected_set = {t.strip() for t in affected_tests if t.strip()}
    adds: Set[str] = set()
    for raw in changed_source_files:
        source = raw.strip()
        if not source or source not in dep_map:
            continue
        for test in dep_map[source]:
            if test in affected_set:
                continue
            if not (repo_root / test).is_file():
                continue
            adds.add(test)
    return sorted(adds)


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text().splitlines()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute coverage-only test-dependency additions for smart-test. "
            "Reads changed source files from stdin and the current "
            "affected-tests selection from --tests-file."
        )
    )
    parser.add_argument("repo_root", help="Repository root (paths resolve relative to this)")
    parser.add_argument(
        "--map",
        required=True,
        help="Path to the coverage-only-deps map file. A missing file yields "
             "no additions.",
    )
    parser.add_argument(
        "--tests-file",
        default=None,
        help="Path to current affected-tests file (one path per line). "
             "Missing file is treated as an empty selection.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    map_path = Path(args.map)
    dep_map = parse_map(map_path.read_text()) if map_path.exists() else {}
    changed = sys.stdin.read().splitlines()
    affected = _read_lines(Path(args.tests_file)) if args.tests_file else []

    for path in compute_adds(
        repo_root=repo_root,
        changed_source_files=changed,
        affected_tests=affected,
        dep_map=dep_map,
    ):
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
