#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How much of the run set could Phase 3 drop, and what forbids dropping it.

THE CONSTRAINT WI-bolot DOES NOT MENTION, which is why this exists. After the
suite, ``smart-test`` runs ``coverage report --include=<changed files>
--fail-under=100``. That gate is **whole-file**, not changed-lines. So a
narrowing that keeps only the tests attached to CHANGED BLOCKS can leave the
rest of the changed file uncovered and exit 1 — a red local run on every commit,
which is a worse outcome than a slow one. Narrowing has to be defined against
the gate it must survive, so the sets below are measured separately.

THREE SETS::

    RUN SET      what smart-test selects today (static import slice +
                 declarative augmentations) — the thing being narrowed.
    FILE-TOUCH   tests the index has observed executing ANY block of a changed
                 FILE. The largest set a narrowing could keep and still pass the
                 whole-file gate: these are exactly the tests that produced that
                 file's coverage rows.
    BLOCK-SELECT tests attached to the blocks that actually CHANGED — what
                 ``coverage-select`` already reports.

    headroom  = speakable - FILE-TOUCH        safe to drop, gate survives
    overshoot = (speakable & FILE-TOUCH) - BLOCK-SELECT   the extra cut that
                                                          would cost the gate

SPEAKABLE IS THE CEILING, AND IT IS THE POINT OF THE MEASUREMENT. A run-set file
may only be dropped if the index has positive knowledge of it: it has coverage
rows, and it is not in ``unmeasured_test``. 15.9% of this suite produces no
coverage rows at all (the tracker package is outside COV_PATHS; subprocess and
``scripts/`` tests leave no in-process trace), and any test never run locally is
simply absent. Absence of evidence is not evidence of absence, so everything
outside ``speakable`` is kept unconditionally — which bounds Phase 3's saving
regardless of how good the selector is.

WHAT FORBIDS NARROWING ENTIRELY, and the distinction that turned out to decide
the whole design:

    unknown SOURCE file   a changed non-test file the index has never seen. No
                          test has been observed touching it, so ANY droppable
                          test could be its real cover. This forbids dropping.
    missing path          a changed file that no longer exists. Its tests
                          exercised code that is now gone, and those are worth
                          running.
    new blocks            NOT a refusal, and the first version of this script
                          wrongly made it one — which reported "narrowing
                          refused" on every commit measured, because ordinary
                          development adds blocks. The reason new blocks look
                          scary is that coverage cannot know which test covers
                          new code; but the test that covers a developer's new
                          code is a CHANGED TEST FILE, and changed test files
                          enter the run set through a separate selector that
                          narrowing must not touch. Reported, not refused.

    unknown TEST file     also NOT a refusal, for the same reason: a brand-new
                          test file is unknown to the index by definition, and
                          it is its own cover.

Run::

    python scripts/measure-narrowing-headroom.py                  # vs origin/dev
    python scripts/measure-narrowing-headroom.py <base-ref> ...   # several bases
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                      / "packages" / "hypergumbo-core" / "src"))

from hypergumbo_core.selection_index import open_index, select_tests

ROOT = Path(
    subprocess.run(["git", "rev-parse", "--show-toplevel"],  # noqa: S607
                   capture_output=True, text=True, check=True).stdout.strip()
)
INDEX = Path(os.environ.get(
    "HG_SELECTION_INDEX",
    Path.home() / "hypergumbo_lab_notebook" / "selection_index.sqlite"))


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args],  # noqa: S603,S607
                          capture_output=True, text=True)


def _is_test(rel: str) -> bool:
    """A changed TEST file is its own cover, so it never forbids narrowing."""
    return "/tests/" in rel or Path(rel).name.startswith(
        ("test_", "BRANCHES_test_"))


def _manifest_run_set() -> set[str]:
    """The static run set, read from the committed manifest.

    Deliberately the MANIFEST set rather than a live smart-test invocation: the
    manifest is the pre-union selection, which is what Phase 3 narrows.
    """
    man = ROOT / ".ci" / "affected-tests.txt"
    if not man.exists():
        return set()
    body = man.read_text().split("# === SELECTED_TESTS ===\n", 1)
    if len(body) < 2:
        return set()
    return {ln.strip() for ln in body[1].splitlines()
            if ln.strip() and not ln.startswith("#")}


def _files(test_ids) -> set[str]:
    out = set()
    for t in test_ids:
        rel = t[len(f"{ROOT}/"):] if t.startswith(f"{ROOT}/") else t
        out.add(rel.split("::")[0])
    return out


def report(base: str) -> None:
    changed = [ln for ln in _git("diff", "--name-only", base).stdout.split()
               if ln.endswith(".py")]
    print(f"\n=== base {base} — {len(changed)} changed .py file(s) ===")
    if not changed:
        print("  nothing to measure")
        return

    abs_changed = [str(ROOT / c) for c in changed]
    base_sources = {
        str(ROOT / c): (_git("show", f"{base}:{c}").stdout
                        if _git("show", f"{base}:{c}").returncode == 0
                        else None)
        for c in changed
    }
    conn = open_index(INDEX)
    sel = select_tests(conn, base_sources)

    # Every test the index has observed executing ANY block of a changed file.
    marks = ",".join("?" * len(abs_changed))
    touch = {r[0] for r in conn.execute(
        "SELECT DISTINCT test_id FROM test_block "  # noqa: S608 - marks are ?
        f"WHERE path IN ({marks})", abs_changed)}
    unmeasured = {r[0] for r in conn.execute(
        "SELECT test_id FROM unmeasured_test")}
    known_test_files = _files(
        r[0] for r in conn.execute("SELECT DISTINCT test_id FROM test_block"))
    conn.close()

    run_set = _manifest_run_set()
    touch_files = _files(touch)
    block_files = _files(sel.tests)
    unmeasured_files = _files(unmeasured)

    speakable = {f for f in run_set
                 if f in known_test_files and f not in unmeasured_files}
    headroom = speakable - touch_files
    overshoot = (speakable & touch_files) - block_files

    # Only an unknown SOURCE file forbids dropping — see the module docstring.
    unknown_src = sorted(
        p for p in sel.unknown_paths
        if not _is_test(p[len(f"{ROOT}/"):] if p.startswith(f"{ROOT}/") else p)
    )

    denom = len(run_set) or 1
    print(f"  RUN SET (manifest)          {len(run_set):>5}")
    print(f"    of which index-speakable  {len(speakable):>5}"
          f"   <- the CEILING; the rest are unmeasured or unseen, always kept")
    print(f"  FILE-TOUCH (gate-safe keep) {len(touch_files & run_set):>5}")
    print(f"  BLOCK-SELECT (changed only) {len(block_files & run_set):>5}")
    print("  --")
    print(f"  HEADROOM  droppable safely  {len(headroom):>5}"
          f"   ({100.0 * len(headroom) / denom:.0f}% of the run set)")
    print(f"  OVERSHOOT costs the gate    {len(overshoot):>5}")
    print(f"  reported, not refusals      new_blocks={len(sel.new_blocks)} "
          f"unknown_test_files="
          f"{len(sel.unknown_paths) - len(unknown_src)}")
    blockers = []
    if unknown_src:
        blockers.append(f"unknown SOURCE files={len(unknown_src)}")
    if sel.missing_paths:
        blockers.append(f"missing={len(sel.missing_paths)}")
    if blockers:
        print(f"  NARROWING FORBIDDEN: {', '.join(blockers)}")
        for p in unknown_src[:5]:
            print(f"      {p}")
    else:
        print("  narrowing permitted")


for ref in (sys.argv[1:] or ["origin/dev"]):
    report(ref)
