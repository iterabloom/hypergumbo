#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Map top-level source files to matching ``tests/test_*.py`` (WI-jozan).

`scripts/smart-test` uses `hypergumbo slice --files` for reverse-slice to
find test files that depend on changed source. That reverse-slice covers
the `packages/*/src/**` tree (what hypergumbo understands as source), but
not the top-level Python infrastructure — `.agent/hooks/_shared/*.py` and
`scripts/*`. A PR that only touches those produces an empty slice result
and `ci.yml`'s per-PR gate silently skips pytest entirely, so agent
infrastructure regressions are not caught until the 4-hour full-suite
cycle (the gap closed by WI-javan at full-suite level; this module
closes it at the per-PR level).

The mapping rule is conservative: a changed top-level source file maps
to ``tests/test_<basename>.py`` if that file exists, with one small
normalization — hyphens in a ``scripts/`` basename become underscores so
that ``scripts/agent-supervisor`` maps to ``tests/test_agent_supervisor.py``.
Files that do not have a matching test are silently skipped; the caller
falls back to its normal behaviour (running only the slice-found tests,
or writing an empty manifest). False positives (a test that does not
actually exercise the changed file) are preferred over false negatives
(a regression that sneaks past the per-PR gate), which is why the
module does not try to enforce import-graph correctness — that is the
reverse-slice's job for the code it can reach.

CLI: reads newline-separated changed paths from stdin (each relative to
the repo root), prints one matching ``tests/test_*.py`` path per line,
sorted and deduplicated. Exit code is always 0 — the absence of a match
is normal, not an error.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List


def _candidate_test_basename(path: str) -> str | None:
    """Return the base name of the expected ``test_<base>.py`` file, or None.

    Rules:
    - ``.agent/hooks/_shared/<name>.py`` → ``<name>`` (no transformation;
      those filenames are already snake_case).
    - ``scripts/<name>`` (exactly one path component under ``scripts/``)
      → ``<name>`` with hyphens collapsed to underscores so that
      ``auto-pr`` maps to ``auto_pr`` and ``agent-supervisor`` to
      ``agent_supervisor``. Subdirectory entries (``scripts/lib/x.sh``)
      are skipped because their basenames tend to be generic
      (``forgejo-api.sh``) and the hyphen→underscore heuristic would
      over-match unrelated tests.
    - All other paths return None.
    """
    if path.startswith(".agent/hooks/_shared/") and path.endswith(".py"):
        rel = path[len(".agent/hooks/_shared/"):]
        if "/" in rel:
            return None
        return rel[:-len(".py")]
    if path.startswith("scripts/"):
        rel = path[len("scripts/"):]
        if not rel or "/" in rel:
            return None
        # Strip a .py / .sh extension if present, then collapse hyphens.
        stem = rel
        for ext in (".py", ".sh"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        return stem.replace("-", "_")
    return None


def map_to_tests(changed_files: Iterable[str], repo_root: Path) -> List[str]:
    """Return sorted deduplicated list of matching ``tests/test_*.py`` paths."""
    out: set[str] = set()
    for raw in changed_files:
        path = raw.strip()
        if not path:
            continue
        base = _candidate_test_basename(path)
        if base is None:
            continue
        candidate = repo_root / "tests" / f"test_{base}.py"
        if candidate.is_file():
            out.add(f"tests/test_{base}.py")
    return sorted(out)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: top_level_test_map.py <repo_root>\n")
        return 2
    repo_root = Path(argv[1]).resolve()
    changed = sys.stdin.read().splitlines()
    for match in map_to_tests(changed, repo_root):
        print(match)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
