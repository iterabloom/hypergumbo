#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests reachable from a changed file by a STRING literal, for smart-test.

WHAT ROTTED. ``test_module_key_axis.py::test_every_cited_emission_site_still_
exists`` pins that every ``file:line`` citation in ``MODULE_KEY_NOTIONS`` still
contains its quoted anchor. The INV-sihom fix added lines to
``hypergumbo_lang_mainstream/bash.py``, moving the redirect emission from
:1353 to :1399. The per-PR gate on that PR was GREEN — the citation test lives
in hypergumbo-core and names bash.py by STRING, so an import-graph reverse
slice never selected it. It fired on the next full-suite cron and was repaired
in passing two firings later, by a PR that happened to touch a core file
(WI-lujon).

WHY THE THREE EXISTING UNIONS DID NOT SWEEP IT UP. Playbooks, catalogue data
and docs all key on the CHANGED path's directory. This one has to key the
other way — on what some OTHER file's text contains — which is why it survived
its own family's fix.

WHY THE ITEM'S OWN PROPOSED FIX WOULD HAVE MISSED ITS OWN EXAMPLE. WI-lujon
proposed selecting "a test file that contains a string literal naming a changed
source path". Measured on the live tree: ``test_module_key_axis.py`` does not
contain the string ``bash`` at all. The citation lives in the SOURCE module the
test reads, so one hop is not enough and the shape needs two:

  1. find every file whose text contains a literal naming the changed path;
  2. a citer that IS a test is selected; a citer that is a source or a script
     contributes the tests that name IT, which is the same grep-derived shape
     the sibling unions use.

A CITATION HAS A SLASH IN IT. The literal is the import-root-relative path
(``hypergumbo_lang_mainstream/bash.py``), not the bare basename. That is
measured rather than stylistic: across the whole tree, slash-bearing literals
name 12 shipped sources from 9 citing files, while bare basenames match
fixtures, log lines and prose — ``bash.py`` alone appears in five test files
that have nothing to do with the citation. Keying on the basename would trade a
precise edge for noise, and the noise is what makes an over-selecting gate get
narrowed later by someone who does not know why it was wide.

WHAT THIS DOES NOT COVER, MEASURED RATHER THAN ASSUMED. Seven markdown files
under ``docs/`` (and ``CHANGELOG.md``) cite a shipped source by slash-path the
same way, and none of them is scanned here. That is deliberate and it is a
judgement that can go stale: today no test checks a doc's citations against the
tree, so an edge from a changed source to a doc-reading test would have no
reader. If such a gate is ever written -- an ADR citation checker is the
obvious one -- ``_scan_roots`` is where it attaches, and the second hop for a
docs citer is the existing doc-gate union rather than an import path.

CLI: ``citation_gate_tests.py REPO_ROOT < CHANGED``. Reads newline-separated
repo-relative changed paths from stdin, writes repo-relative test paths one per
line, sorted and deduplicated. Always exits 0 — no citation is the normal case.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


def _scan_roots(repo_root: Path) -> List[Path]:
    """Every file whose text could carry a citation."""
    found: List[Path] = []
    found += sorted(repo_root.glob("packages/*/src/**/*.py"))
    found += sorted(repo_root.glob("packages/*/tests/*.py"))
    found += sorted(repo_root.glob("tests/*.py"))
    found += sorted(repo_root.glob("scripts/*.py"))
    return found


def _is_test(path: Path, repo_root: Path) -> bool:
    relative = path.relative_to(repo_root).as_posix()
    return relative.startswith("tests/") or "/tests/" in relative


def citation_literals(changed: Iterable[str]) -> Dict[str, str]:
    """Map the literal a citer would write -> the changed path it names.

    For a package source the literal is import-root-relative, because that is
    what a citation in another package can write without naming a wheel
    layout. For anything else it is the repo-relative path itself.
    """
    literals: Dict[str, str] = {}
    for raw in changed:
        path = raw.strip()
        if not path or "/" not in path:
            continue
        parts = path.split("/")
        if len(parts) > 3 and parts[0] == "packages" and parts[2] == "src":
            literals["/".join(parts[3:])] = path
        else:
            literals[path] = path
    return literals


def citing_files(repo_root: Path, literals: Iterable[str]) -> Set[Path]:
    """Files whose text contains one of ``literals``, excluding self-citation."""
    wanted = [lit for lit in literals if lit]
    hits: Set[Path] = set()
    if not wanted:
        return hits
    for candidate in _scan_roots(repo_root):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file on a listed path
            continue
        relative = candidate.relative_to(repo_root).as_posix()
        for literal in wanted:
            if literal in text and not relative.endswith(literal):
                hits.add(candidate)
                break
    return hits


def citer_needles(repo_root: Path, citer: Path) -> List[str]:
    """How a test would name ``citer``: its import path, else its stem.

    The dotted import path is derived from the citer's own location rather
    than written down, and it is what makes the second hop an EDGE instead of
    a word match. Measured: for ``hypergumbo_core/taint.py`` the bare stem
    ``taint`` matches 142 test files -- any test that mentions taint at all --
    while ``hypergumbo_core.taint`` matches 64, and both reach the module's own
    test. A script under ``scripts/`` has no import path, so there the stem is
    all there is.
    """
    parts = citer.relative_to(repo_root).parts
    if len(parts) > 3 and parts[0] == "packages" and parts[2] == "src":
        module = ".".join(parts[3:])
        if module.endswith(".py"):
            module = module[: -len(".py")]
        module = module.removesuffix(".__init__")
        return [module]
    return [citer.stem]


def tests_naming(repo_root: Path, needles: Iterable[str]) -> Set[str]:
    """Tests whose text contains one of ``needles`` — the siblings' own rule.

    The module's same-named test is added unconditionally when one exists: a
    test that reaches its subject through a re-export or a fixture names
    neither spelling, and this gate exists precisely because a name-blind
    selector let a citation rot for two cron firings.
    """
    wanted = list(needles)
    found: Set[str] = set()
    for base in (
        sorted(repo_root.glob("packages/*/tests/*.py"))
        + sorted((repo_root / "tests").glob("test_*.py"))
    ):
        relative = base.relative_to(repo_root).as_posix()
        if any(base.name == f"test_{n.rsplit('.', 1)[-1]}.py" for n in wanted):
            found.add(relative)
            continue
        try:
            text = base.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file on a listed path
            continue
        if any(needle in text for needle in wanted):
            found.add(relative)
    return found


def selected_tests(repo_root: Path, changed: Iterable[str]) -> Set[str]:
    """Every test reachable from ``changed`` by a citation edge."""
    literals = citation_literals(changed)
    selected: Set[str] = set()
    for citer in citing_files(repo_root, literals):
        relative = citer.relative_to(repo_root).as_posix()
        if _is_test(citer, repo_root):
            selected.add(relative)
        else:
            selected |= tests_naming(repo_root, citer_needles(repo_root, citer))
    return selected


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo_root", help="repository root")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    for test in sorted(selected_tests(repo_root, sys.stdin.read().splitlines())):
        print(test)
    return 0


if __name__ == "__main__":
    sys.exit(main())
