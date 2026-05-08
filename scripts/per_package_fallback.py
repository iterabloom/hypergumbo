#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-package fallback for ``scripts/smart-test``'s test selection.

CI runs each ``packages/<P>/`` in isolation, so the manifest sanity check
in ``ci.yml`` requires at least one test under ``packages/<P>/tests/``
whenever any source file under ``packages/<P>/src/`` is changed.
``hypergumbo slice --files`` resolves tests via transitive imports, so
when the only changed source for package ``P`` is ``__init__.py`` and no
test in ``packages/P/tests/`` imports ``__init__`` directly, the slice
correctly reports zero matches — but the sanity check still expects
≥1, and the merge is blocked. This recurs every release because
``prepare-release`` bumps every package's ``__init__.py`` simultaneously.

The fix is a per-package fallback layer applied after the slice: for any
``packages/<P>/src/...`` file in the changed set whose package is not
already represented in the affected-tests output, append the
alphabetically-first ``test_*.py`` (or ``BRANCHES_test_*.py``) found in
``packages/<P>/tests/``. The choice is alphabetical for determinism;
any test in the directory satisfies the sanity check, and the
"lightweight test per package" cost is the same as the existing
version-only branch in ``smart-test`` (lines ~592–650), which uses the
same rule.

Files outside ``packages/<P>/src/`` (top-level ``scripts/``, hooks,
docs) are silently ignored — they don't define a per-package coverage
expectation and are handled separately by
``.agent/hooks/_shared/top_level_test_map.py``.

CLI: ``per_package_fallback.py REPO_ROOT --tests-file PATH < CHANGED``.
Reads newline-separated changed source paths from stdin (relative to
``REPO_ROOT``). Reads the current affected-tests selection (also
newline-separated, relative paths) from ``--tests-file``; a missing
file is treated as an empty selection. Writes additional test paths
(one per line, sorted, deduplicated) to stdout. Always exits 0
unless argv is malformed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Set


_TEST_FILE_PREFIXES = ("test_", "BRANCHES_test_")


def _affected_packages(changed_source_files: Iterable[str]) -> List[str]:
    """Extract unique package names from ``packages/<P>/src/...`` paths.

    Anything that doesn't match the ``packages/<P>/src/`` shape is dropped.
    """
    seen: Set[str] = set()
    ordered: List[str] = []
    for raw in changed_source_files:
        path = raw.strip()
        if not path:
            continue
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != "packages" or parts[2] != "src":
            continue
        pkg = parts[1]
        if pkg not in seen:
            seen.add(pkg)
            ordered.append(pkg)
    return ordered


def _packages_already_covered(affected_tests: Iterable[str]) -> Set[str]:
    covered: Set[str] = set()
    for raw in affected_tests:
        path = raw.strip()
        if not path:
            continue
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "packages" and parts[2] == "tests":
            covered.add(parts[1])
    return covered


def _first_test_in(tests_dir: Path) -> str | None:
    """Return the alphabetically-first ``test_*.py`` / ``BRANCHES_test_*.py``."""
    if not tests_dir.is_dir():
        return None
    candidates: List[str] = []
    for entry in tests_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if not name.endswith(".py"):
            continue
        if any(name.startswith(p) for p in _TEST_FILE_PREFIXES):
            candidates.append(name)
    if not candidates:
        return None
    return sorted(candidates)[0]


def compute_fallbacks(
    *,
    repo_root: Path,
    changed_source_files: Iterable[str],
    affected_tests: Iterable[str],
) -> List[str]:
    """Return per-package fallback test paths, sorted and deduplicated.

    Each returned path is relative to ``repo_root``. A path is included
    only if (a) the package's source changed, (b) the package is not
    already represented in ``affected_tests``, and (c) the package has
    at least one ``test_*.py`` / ``BRANCHES_test_*.py`` file.
    """
    affected_list = list(affected_tests)
    covered = _packages_already_covered(affected_list)
    affected_set = {t.strip() for t in affected_list if t.strip()}

    fallbacks: Set[str] = set()
    for pkg in _affected_packages(changed_source_files):
        if pkg in covered:
            continue
        tests_dir = repo_root / "packages" / pkg / "tests"
        first = _first_test_in(tests_dir)
        if first is None:
            continue
        rel = f"packages/{pkg}/tests/{first}"
        if rel in affected_set:
            continue
        fallbacks.add(rel)
    return sorted(fallbacks)


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text().splitlines()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute per-package fallback test paths for smart-test. Reads "
            "changed source files from stdin and the current affected-tests "
            "selection from --tests-file."
        )
    )
    parser.add_argument("repo_root", help="Repository root (paths resolve relative to this)")
    parser.add_argument(
        "--tests-file",
        default=None,
        help="Path to current affected-tests file (one path per line). "
             "Missing file is treated as an empty selection.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    changed = sys.stdin.read().splitlines()
    affected = _read_lines(Path(args.tests_file)) if args.tests_file else []

    fallbacks = compute_fallbacks(
        repo_root=repo_root,
        changed_source_files=changed,
        affected_tests=affected,
    )
    for path in fallbacks:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
