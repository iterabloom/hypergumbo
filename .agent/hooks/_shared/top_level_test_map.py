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

The mapping rule is a **separator-insensitive prefix match**: a changed
source maps to every ``tests/test_<stem>*.py`` whose stem, reduced to
lowercase alphanumerics, starts with the source's basename reduced the
same way. So ``scripts/agent-supervisor`` reaches both
``test_agent_supervisor.py`` and ``test_agent_supervisor_meta_breaker.py``.
Files with no matching test are silently skipped; the caller falls back to
its normal behaviour (running only the slice-found tests, or writing an
empty manifest). False positives (a test that does not actually exercise
the changed file) are preferred over false negatives (a regression that
sneaks past the per-PR gate), which is why this module does not try to
enforce import-graph correctness — that is the reverse-slice's job for the
code it can reach.

Both halves of that rule are load-bearing, and the original 1:1 exact-match
rule failed WI-bisar on both (measured 2026-07-30: 27 of 73 root tests were
reachable from any top-level source):

* **Prefix**, because a script's tests are routinely split across files —
  one exact name reached one of them and left the rest selected by nothing.
* **Separator-insensitive**, because separator placement drifts between a
  script's name and its tests. ``scripts/auto-pr`` collapses to ``auto_pr``
  while its eleven test files are named ``test_autopr_*``; a plain prefix
  match still reaches **zero** of them, since ``test_autopr_x`` does not
  start with ``test_auto_pr``. Comparing with separators removed is the only
  one of the two that fixes the case that motivated the change.

``_shared/`` accepts ``.sh`` as well as ``.py``. That directory holds 13
shell helpers against 8 Python ones, so requiring ``.py`` there left most of
a directory this module claims to cover unreachable — while ``scripts/``
already accepted ``.py``, ``.sh`` and extensionless names. The asymmetry was
not a decision, just an omission.

Name-based mapping has a floor: a test named for the *behaviour* it pins
rather than the *file* it covers cannot be reached by any rule of this
shape, and neither can one covering several sources or a non-source
artifact. Those are enumerated in ``KNOWN_UNREACHABLE`` in
``tests/test_top_level_test_map.py``, which ratchets two-sidedly so the list
can only shrink and cannot rot. Reaching them needs a declarative marker in
the test file, not a cleverer heuristic.

CLI: reads newline-separated changed paths from stdin (each relative to
the repo root), prints one matching ``tests/test_*.py`` path per line,
sorted and deduplicated. Exit code is always 0 — the absence of a match
is normal, not an error.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List


def _normalized(name: str) -> str:
    """Reduce a name to lowercase alphanumerics for comparison.

    Dropping separators entirely is what lets ``auto-pr`` match
    ``test_autopr_*``; keeping them (in any form) does not.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _candidate_test_basename(path: str) -> str | None:
    """Return the base name of the expected ``test_<base>.py`` file, or None.

    Rules:
    - ``.agent/hooks/_shared/<name>.py`` or ``.sh`` → ``<name>`` (no
      transformation; those filenames are already snake_case).
    - ``scripts/<name>`` (exactly one path component under ``scripts/``)
      → ``<name>`` with hyphens collapsed to underscores so that
      ``auto-pr`` maps to ``auto_pr`` and ``agent-supervisor`` to
      ``agent_supervisor``. Subdirectory entries (``scripts/lib/x.sh``)
      are skipped because their basenames tend to be generic
      (``forgejo-api.sh``) and the hyphen→underscore heuristic would
      over-match unrelated tests.
    - All other paths return None.
    """
    if path.startswith(".agent/hooks/_shared/"):
        rel = path[len(".agent/hooks/_shared/"):]
        if "/" in rel:
            return None
        for ext in (".py", ".sh"):
            if rel.endswith(ext):
                return rel[: -len(ext)]
        return None
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
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return []
    candidates = [
        (p.name, _normalized(p.name[len("test_"):-len(".py")]))
        for p in sorted(tests_dir.glob("test_*.py"))
    ]
    out: set[str] = set()
    for raw in changed_files:
        path = raw.strip()
        if not path:
            continue
        base = _candidate_test_basename(path)
        if base is None:
            continue
        prefix = _normalized(base)
        if not prefix:
            # A stem with no alphanumerics (``scripts/-``) would make every
            # ``startswith`` true and select the entire root suite.
            continue
        for name, stem in candidates:
            if stem.startswith(prefix):
                out.add(f"tests/{name}")
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
