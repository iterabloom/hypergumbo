# SPDX-License-Identifier: AGPL-3.0-or-later
"""Incremental ``block -> tests`` index for coverage-directed test selection.

WHAT IT IS. After every run, fold that run's per-test coverage contexts into a
persistent map from AST block (see :mod:`hypergumbo_core.block_hash`) to the
tests observed executing it. At commit time, re-hash the changed files, diff
against the stored digests, and select the tests attached to blocks that moved.

WHY INCREMENTAL RATHER THAN A CENSUS SNAPSHOT. A snapshot cannot see code that
did not exist when it was taken, so under red-green-refactor every newly written
function would map to no test — the developer's own new test included. Folding
each run in as it happens closes that: the moment you run the new test, the
index learns the new blocks. This is the same reason pytest-testmon maintains
its database per-run instead of from a profiling pass.

THE THREE SEMANTICS THAT ARE EASY TO GET WRONG, each with tests of its own:

1. **Replace, do not union, per test.** Re-indexing a test REPLACES its edges.
   Union would keep an edge to a block the test no longer touches, and stale
   edges only ever accumulate. But replacement applies ONLY to tests present in
   this run — otherwise a targeted run of one file would erase the map for
   everything else.

2. **Import-time blocks belong to every test that touches the file.** Module
   bodies, class attributes and decorators execute once at import, under the
   EMPTY context, so no test owns them. Measured on this repo's census that is
   14.5% of all statements. Left unattached, editing a module-level constant
   selects zero tests. The rule that fixes it is an implication rather than a
   guess: if a test executes any line in file F then F was imported, so F's
   import-time blocks run whenever that test runs.

3. **A test that ran and produced no rows is UNMEASURED, not unneeded.** 15.9%
   of this suite is in that state — the tracker package is outside COV_PATHS,
   and subprocess/`scripts/` tests leave no in-process trace. Coverage can never
   select them, so they are recorded explicitly and reported by
   :class:`Selection`, which is what stops a future narrowing mode from
   silently dropping them.

SOUNDNESS BOUNDARY, stated because it is the thing that would bite. The index
knows "tests I have OBSERVED executing block B", a subset of "tests that execute
B", until every test has run since B appeared. So this selector is sound as of
the last FULL run and under-selects for newly added code between full runs. The
``last_full_run_sha`` marker exists so a caller can report that age rather than
pretend it does not exist. It is why this is a UNION member and never the gate.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from hypergumbo_core.block_hash import blocks_for_source, line_owner

#: ``(path, block name)`` — the join key. Deliberately not line-based.
BlockKey = tuple[str, str]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS block (
    path   TEXT NOT NULL,
    name   TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (path, name)
);
CREATE TABLE IF NOT EXISTS test_block (
    test_id TEXT NOT NULL,
    path    TEXT NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (test_id, path, name)
);
CREATE INDEX IF NOT EXISTS test_block_by_block ON test_block (path, name);
CREATE TABLE IF NOT EXISTS unmeasured_test (test_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(frozen=True)
class Selection:
    """What coverage has to say about a set of changed files.

    ``tests`` is a SHORTLIST to union with the other selectors, never a
    replacement for them: ``new_blocks`` and ``unknown_paths`` are precisely the
    cases coverage cannot speak to, and they are surfaced rather than folded
    into an empty answer that would look like "nothing to run".
    """

    tests: frozenset[str]
    changed_blocks: frozenset[BlockKey]
    new_blocks: frozenset[BlockKey]
    unknown_paths: frozenset[str]
    unmeasured: frozenset[str]
    #: Indexed but no longer readable — deleted, moved, or newly unparsable.
    #: Distinct from ``unknown_paths`` (never indexed at all) because the two
    #: warrant opposite responses: nothing is known about an unknown file, while
    #: a file that USED to exist has tests that exercised code which is now
    #: gone, and those are worth running.
    missing_paths: frozenset[str] = frozenset()


def open_index(path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the index database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _strip_phase(context: str) -> str:
    """``test|setup`` -> ``test``. pytest-cov records a context per phase."""
    return context.rsplit("|", 1)[0] if "|" in context else context


def normalize_test_id(node_id: str, repo_root: Optional[Path]) -> str:
    """Rewrite a pytest node id to be repo-relative, or leave it untouched.

    Node ids are relative to pytest's ROOTDIR, which varies by invocation: a run
    confined to one package makes that package the rootdir (several of ours
    carry ``[tool.pytest.ini_options]``), while a run spanning packages uses the
    repo. The same test therefore has two spellings, and storing both fragments
    the index into rows that never join — the same failure class as the
    ``|setup`` suffix that once matched 0 of 20,437 contexts.

    Resolution is by EXISTENCE, not by string surgery: a candidate is accepted
    only if the file is actually there. If more than one package could claim it
    the id is returned unchanged, because picking one would silently attach a
    test to the wrong file, and an unjoined row is a recoverable under-selection
    where a wrong row is a lie.
    """
    if repo_root is None or "::" not in node_id:
        return node_id
    file_part, _, rest = node_id.partition("::")
    if (repo_root / file_part).exists():
        return node_id
    candidates = [
        pkg for pkg in sorted((repo_root / "packages").glob("*"))
        if (pkg / file_part).exists()
    ]
    if len(candidates) != 1:
        return node_id
    rel = candidates[0].relative_to(repo_root)
    return f"{rel}/{file_part}::{rest}"


def _numbits_to_linenos(numbits: bytes) -> Iterable[int]:
    for byte_index, byte in enumerate(numbits):
        for bit in range(8):
            if byte & (1 << bit):
                yield byte_index * 8 + bit


def update_from_run(
    conn: sqlite3.Connection,
    coverage_db: Path,
    *,
    ran_tests: Optional[Iterable[str]] = None,
    git_sha: Optional[str] = None,
    full_run: bool = False,
    repo_root: Optional[Path] = None,
) -> None:
    """Fold one run's coverage contexts into the index.

    Blocks are re-hashed from the CURRENT source. That is correct only if this
    is called promptly after the run that produced ``coverage_db`` — the
    database holds line numbers from the tree as it was, and a file edited in
    between would map its lines onto the wrong blocks. Call it immediately
    after the run, which is what smart-test does.
    """
    src = sqlite3.connect(f"file:{coverage_db}?mode=ro", uri=True)
    try:
        rows = src.execute(
            "SELECT c.context, f.path, lb.numbits FROM line_bits lb "
            "JOIN context c ON c.id = lb.context_id "
            "JOIN file f ON f.id = lb.file_id"
        ).fetchall()
    finally:
        src.close()

    owners: dict[str, dict[int, str]] = {}
    digests: dict[str, dict[str, str]] = {}

    def owner_for(path: str) -> dict[int, str]:
        if path not in owners:
            try:
                blocks = blocks_for_source(path, Path(path).read_text())
            except (OSError, SyntaxError):  # pragma: no cover - removed source
                blocks = []
            owners[path] = line_owner(blocks)
            digests[path] = {b.name: b.digest for b in blocks}
        return owners[path]

    edges: dict[str, set[BlockKey]] = {}
    import_blocks: dict[str, set[str]] = {}
    for context, path, numbits in rows:
        own = owner_for(path)
        names = {own[n] for n in _numbits_to_linenos(numbits) if n in own}
        if not names:
            continue
        if context == "":
            import_blocks.setdefault(path, set()).update(names)
        else:
            test_id = normalize_test_id(_strip_phase(context), repo_root)
            edges.setdefault(test_id, set()).update((path, n) for n in names)

    # Import-time blocks run whenever the file is imported, so they belong to
    # every test that executes anything in that file. See §2 of the module
    # docstring for why this is an implication rather than an approximation.
    for keys in edges.values():
        for path in {p for p, _ in keys}:
            keys.update((path, n) for n in import_blocks.get(path, ()))

    cur = conn.cursor()
    for path, per_name in digests.items():
        cur.executemany(
            "INSERT INTO block (path, name, digest) VALUES (?, ?, ?) "
            "ON CONFLICT (path, name) DO UPDATE SET digest = excluded.digest",
            [(path, name, digest) for name, digest in per_name.items()],
        )
    # REPLACE per test, and only for tests that appear in THIS run.
    for test_id, keys in edges.items():
        cur.execute("DELETE FROM test_block WHERE test_id = ?", (test_id,))
        cur.executemany(
            "INSERT OR IGNORE INTO test_block (test_id, path, name) "
            "VALUES (?, ?, ?)",
            [(test_id, p, n) for p, n in keys],
        )
    # Producing edges is positive evidence at ANY coverage scope, so clearing
    # is always safe.
    cur.executemany(
        "DELETE FROM unmeasured_test WHERE test_id = ?",
        [(t,) for t in sorted(edges)],
    )
    # Recording a test as unmeasured is the opposite: it is a claim that the
    # test ran and produced NOTHING, which is only meaningful when coverage was
    # watching everything. smart-test's targeted path scopes --cov to the
    # CHANGED SOURCE FILES ONLY, so on an ordinary run most tests legitimately
    # touch nothing in scope. Trusting that would have marked 1,507 of 1,662
    # tests unmeasured on a single run -- and select_tests EXCLUDES unmeasured
    # tests, so most of the suite would have become permanently unselectable
    # while every unit test stayed green.
    if ran_tests is not None and full_run:
        cur.executemany(
            "INSERT OR IGNORE INTO unmeasured_test (test_id) VALUES (?)",
            [(t,) for t in sorted(set(ran_tests) - set(edges))],
        )
    if full_run:
        cur.execute(
            "INSERT INTO meta (key, value) VALUES ('last_full_run_sha', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (git_sha or "",),
        )
    conn.commit()


def changed_blocks(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> tuple[frozenset[BlockKey], frozenset[BlockKey]]:
    """``(changed, new)`` block keys, diffing each path against a SUPPLIED base.

    THE BASE COMES FROM THE CALLER, NOT FROM THE INDEX, and that distinction is
    the whole correctness of this function. The first version compared current
    source against the digests stored in the index — but the index is rewritten
    after every run, so the baseline moved every time: run 1 stored the working
    tree, run 2 compared the working tree against itself, found nothing, and
    selected zero tests while smart-test selected 87. It was answering "what
    changed since I last looked" when the question is "what changed in this
    commit".

    So change detection is git's job (``git show <base>:<path>``) and this
    function only diffs. ``None`` means the path did not exist at the base, i.e.
    the commit added it: every block is NEW and therefore has no test history.
    A path that is now unreadable had all of its base blocks removed, so they
    all count as changed — those tests exercised code that is gone.
    """
    changed: set[BlockKey] = set()
    new: set[BlockKey] = set()
    for path, base_src in base_sources.items():
        try:
            current = {b.name: b.digest
                       for b in blocks_for_source(path, Path(path).read_text())}
        except (OSError, SyntaxError):
            current = {}
        if base_src is None:
            new.update((path, name) for name in current)
            continue
        try:
            base = {b.name: b.digest
                    for b in blocks_for_source(path, base_src)}
        except SyntaxError:  # pragma: no cover - base commit did not parse
            continue
        for name, digest in current.items():
            if name not in base:
                new.add((path, name))
            elif base[name] != digest:
                changed.add((path, name))
        # Vanished blocks are the ONLY signal for a rename, move or deletion,
        # because children are removed from a parent's digest entirely.
        changed.update((path, name) for name in set(base) - set(current))
    return frozenset(changed), frozenset(new)


def select_tests(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> Selection:
    """Tests attached to blocks that changed relative to the supplied base."""
    unknown = {
        p for p in base_sources
        if not conn.execute(
            "SELECT 1 FROM block WHERE path = ? LIMIT 1", (p,)).fetchone()
    }
    changed, new = changed_blocks(conn, base_sources)
    tests: set[str] = set()
    for path, name in changed:
        tests.update(r[0] for r in conn.execute(
            "SELECT test_id FROM test_block WHERE path = ? AND name = ?",
            (path, name)))
    unmeasured = {r[0] for r in conn.execute(
        "SELECT test_id FROM unmeasured_test")}
    missing = {p for p in base_sources
               if p not in unknown and not Path(p).exists()}
    return Selection(
        tests=frozenset(tests - unmeasured),
        changed_blocks=changed,
        new_blocks=new,
        unknown_paths=frozenset(unknown),
        unmeasured=frozenset(unmeasured),
        missing_paths=frozenset(missing),
    )


# ── Node ids to run-set files ───────────────────────────────────────────────
#
# These live HERE rather than in selection_shadow because they convert the
# INDEX's output into the run set's vocabulary, and there are now three
# consumers: the shadow comparison, the Phase 2 union, and Phase 3 narrowing.
# A narrowing module importing them from `selection_shadow` would have read as
# narrowing depending on the shadow, which it does not.


def files_of(node_ids: Iterable[str]) -> frozenset[str]:
    """Reduce node ids to the files that contain them."""
    return frozenset(n.split("::")[0] for n in node_ids)


def rebase_to_repo(node_ids: Iterable[str], repo_root: Path) -> frozenset[str]:
    """Rewrite absolute node ids as repo-relative ones.

    The index stores absolute paths; every other selector in smart-test speaks
    repo-relative. Comparing or unioning across the two spellings makes the same
    file look like two different files, so every consumer rebases first.

    The separator is included in the prefix on purpose: without it a repo at
    ``/x/repo`` would also strip ``/x/repo-backup``.
    """
    prefix = f"{repo_root}/"
    return frozenset(
        n[len(prefix):] if n.startswith(prefix) else n for n in node_ids
    )


def selectable_test_files(
    node_ids: Iterable[str], repo_root: Path,
) -> frozenset[str]:
    """Repo-relative test FILES a run may safely be widened with.

    Two narrowings, both load-bearing for Phase 2:

    * node ids reduce to files, because the run set is a list of files;
    * files that do not exist are DROPPED. The index is persistent and
      out-of-repo, nothing prunes it when a test is renamed or deleted, and
      pytest treats a missing path as a collection error rather than a skip.
      Widening a run with a stale entry would redden it, which is the opposite
      of "can only add tests".
    """
    return frozenset(
        rel for rel in files_of(rebase_to_repo(node_ids, repo_root))
        if (repo_root / rel).is_file()
    )


def touching_test_files(
    conn: sqlite3.Connection, paths: Sequence[str], repo_root: Path,
) -> frozenset[str]:
    """Test FILES observed executing ANY block of ``paths`` (absolute).

    NAMED THIS WAY DELIBERATELY. The obvious name, ``tests_touching``, starts
    with ``test`` and so is COLLECTED AS A TEST by pytest in any test module that
    imports it — which produced ``fixture 'conn' not found`` at collection time,
    an error that names nothing about its real cause.

    THE KEEP SET FOR NARROWING, and deliberately wider than the changed-block
    selection. The local coverage gate is whole-file
    (``coverage report --include=<changed files> --fail-under=100``), so keeping
    only the changed blocks' tests leaves the rest of each changed file
    uncovered and fails the gate. These are exactly the tests that produced the
    file's coverage rows.

    THE FILTER IS IN PYTHON RATHER THAN IN AN ``IN (?,?,…)`` CLAUSE. The
    placeholder list has to be built from ``len(paths)``, which is string-built
    SQL as far as ruff (S608) and bandit (B608) can tell, and the honest answer
    to a static-analysis finding is to remove the construct rather than to
    annotate it twice. The table is a few tens of thousands of rows and this runs
    once per invocation, so scanning it costs nothing worth defending.
    """
    if not paths:
        return frozenset()
    want = set(paths)
    rows = conn.execute("SELECT DISTINCT test_id, path FROM test_block")
    return files_of(rebase_to_repo(
        {test_id for test_id, path in rows if path in want}, repo_root))


def speakable_test_files(
    conn: sqlite3.Connection, repo_root: Path,
) -> frozenset[str]:
    """Test FILES the index has positive coverage knowledge about.

    THE ONLY FILES NARROWING MAY DROP. A file qualifies when it has coverage
    rows AND none of its tests are recorded ``unmeasured``. Both halves matter:
    a file absent from the index was never observed at all, and a file with an
    unmeasured test ran without leaving a trace, so in neither case does the
    index's silence mean "this test is irrelevant".

    The unmeasured check is at FILE granularity because the run set is, which
    is conservative in a way worth naming: 133 of the 190 files with unmeasured
    tests are only PARTLY unmeasured — ``test_profile.py`` is held by 1 test of
    283 — so this rule keeps considerably more than node-granularity would.
    """
    measured = files_of(rebase_to_repo(
        {r[0] for r in conn.execute("SELECT DISTINCT test_id FROM test_block")},
        repo_root))
    unmeasured = files_of(rebase_to_repo(
        {r[0] for r in conn.execute("SELECT test_id FROM unmeasured_test")},
        repo_root))
    return measured - unmeasured


# ── Narrowing (Phase 3 / WI-bolot) ──────────────────────────────────────────
#
# Everything above only ever ADDS tests, so being wrong cost time. Narrowing can
# REMOVE one, so the rules are stated as data rather than left implicit in a
# shell pipeline. See tests/test_selection_narrow.py for the three rules and why
# each has its own failure mode.

#: Coverage measures ``packages/*/src``. A changed file outside that is not
#: coverable in-process at all, so the index's silence about it is a structural
#: fact rather than a gap in knowledge — which is why it does not forbid
#: narrowing the way an unknown *coverable* source file does.
_COVERABLE = ("src",)


def _is_test_path(rel: str) -> bool:
    """A changed TEST file is its own cover, so it never forbids narrowing."""
    return "/tests/" in rel or Path(rel).name.startswith(
        ("test_", "BRANCHES_test_"))


def _is_coverable(rel: str) -> bool:
    parts = Path(rel).parts
    return parts[:1] == ("packages",) and any(p in _COVERABLE for p in parts)


@dataclass(frozen=True)
class Narrowing:
    """The outcome of one narrowing attempt.

    ``refusal`` is the discriminator: when it is set, ``kept`` is the ORIGINAL
    run set and ``dropped`` is empty. A caller must not have to infer "nothing
    happened" from two set sizes being equal, because that is also what a
    successful narrowing that found nothing to drop looks like, and the two
    warrant different reporting.
    """

    kept: frozenset[str]
    dropped: frozenset[str]
    refusal: Optional[str]


def narrowing_blockers(
    selection: Selection, repo_root: Path,
) -> tuple[str, ...]:
    """Reasons this change set must not be narrowed at all.

    Deliberately NOT triggered by ``new_blocks`` or by unknown test / non-
    coverable paths — see the module-level tests for why each of those looks
    like a blocker and is not.
    """
    def rel(p: str) -> str:
        prefix = f"{repo_root}/"
        return p[len(prefix):] if p.startswith(prefix) else p

    out: list[str] = []
    for p in sorted(selection.unknown_paths):
        r = rel(p)
        if not _is_test_path(r) and _is_coverable(r):
            out.append(f"unknown coverable source: {r}")
    for p in sorted(selection.missing_paths):
        out.append(f"missing changed file: {rel(p)}")
    return tuple(out)


def narrow_run_set(
    run_set: Iterable[str],
    *,
    keep: Iterable[str],
    droppable: Iterable[str],
    forbidden: Iterable[str],
) -> Narrowing:
    """Shrink ``run_set`` to ``keep`` plus everything not safely droppable.

    ``keep`` is the FILE-TOUCH set — every test observed executing any block of
    a changed file, not merely the changed blocks — because the local coverage
    gate is whole-file. ``droppable`` is the set the index can speak about.
    Anything in ``run_set`` and outside ``droppable`` survives regardless.
    """
    original = frozenset(run_set)
    reasons = tuple(forbidden)
    if reasons:
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="; ".join(reasons))

    keep_set, drop_ok = frozenset(keep), frozenset(droppable)
    kept = frozenset(f for f in original if f in keep_set or f not in drop_ok)
    if not kept:
        # pytest exits 5 — GREEN — when it collects nothing, so a narrowing to
        # zero would read as a pass while running no tests at all.
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="narrowing would leave an empty run set")
    return Narrowing(kept=kept, dropped=original - kept, refusal=None)
