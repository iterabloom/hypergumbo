# SPDX-License-Identifier: AGPL-3.0-or-later
"""Incremental ``block -> tests`` index for coverage-directed test selection.

WHAT IT IS. After every run, fold that run's per-test coverage contexts into a
persistent map from AST block (see :mod:`hypergumbo_core.block_hash`) to the
tests observed executing it. At commit time, re-hash the changed files, diff
each one against its base-commit source, which the caller supplies
(``git show <base>:<path>``), and select the tests attached to blocks that
moved.

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
   EMPTY context, so no test owns them, and they are a substantial share of
   all statements. Left unattached, editing a module-level constant selects
   zero tests. The rule that fixes it is an implication rather than a
   guess: if a test executes any line in file F then F was imported, so F's
   import-time blocks run whenever that test runs.

3. **A test that ran and produced no rows is UNMEASURED, not unneeded.** A
   sizeable part of this suite is in that state — the tracker package is
   outside smart-test's COV_PATHS, and subprocess/`scripts/` tests leave no
   in-process trace. Coverage can never
   select them, so they are recorded explicitly (on full runs only: a targeted
   run's ``--cov`` scope makes silence meaningless) and reported by
   :class:`Selection`, which is what stops ``--narrow`` (below) from silently
   dropping them.

SOUNDNESS BOUNDARY, stated because it is the thing that would bite. The index
knows "tests I have OBSERVED executing block B", a subset of "tests that execute
B", until every test has run since B appeared. So this selector is sound as of
the last FULL run and under-selects for newly added code between full runs. The
``last_full_run_sha`` marker is written so a caller can report that age rather
than pretend it does not exist, but nothing reads it yet. It is why this is a
UNION member by default and never the merge gate; the opt-in, local-only
``--narrow`` mode (history: WI-bolot) may drop only ``speakable_test_files``
outside the keep set (``touching_test_files`` plus the selected tests' files),
and refuses outright on any ``narrowing_blockers`` reason.

RUN-SET HELPERS AND NARROWING. The index speaks absolute pytest node ids; the
run set is a list of repo-relative test files. ``files_of``, ``rebase_to_repo``
and ``selectable_test_files`` convert one to the other (the last also drops
files that no longer exist, so widening a run never adds a collection error).
Narrowing is stated as data rather than left to a shell pipeline:
``touching_test_files`` is the keep set (tests observed executing ANY block of a
changed file, because the local coverage gate is whole-file);
``speakable_test_files`` is the only droppable set (files with coverage rows and
no unmeasured test); ``narrowing_blockers`` names the reasons to refuse outright
(an unknown coverable source file, a missing changed file, changed import-time
code -- a module or class body, which every importer runs and no coverage row
records); and
``narrow_run_set`` applies them, returning a :class:`Narrowing` whose
``refusal`` is set, with the run set left whole, when a blocker fires or the
result would be empty.

STORAGE. Each test, source file and block is stored ONCE and numbered; the
``test -> block`` pairs are two integers each, in a ``WITHOUT ROWID`` table with
a second index by block. Spelling the three names out per pair (a pytest node
id, an absolute path, a block name: about 236 bytes, held again in the
primary-key index) is what made this repository's index 3.8 GB for 4.5 million
pairs. ``PRAGMA user_version`` holds :data:`FORMAT`. :func:`open_index` converts
a file of the earlier, spelled-out format (:func:`convert_format_1`) and refuses
a format it does not know (:class:`IndexFormatError`), because a file read with
the wrong schema looks like an EMPTY index, and to narrowing an empty index
says "no test runs this code". Callers read and write through the functions
here (:func:`store_edges`, :func:`stored_edges`, :func:`touching_tests`,
:func:`measured_tests`), never through the tables.
"""
from __future__ import annotations

import fcntl
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence

from hypergumbo_core.block_hash import blocks_for_source, line_owner

#: ``(path, block name)`` — the join key. Deliberately not line-based.
BlockKey = tuple[str, str]

#: The storage format, held in ``PRAGMA user_version``. Format 1 predates the
#: marker and reads as 0, like a file SQLite has only just created; the two are
#: told apart by format 1's ``test_block.test_id`` column (:func:`_format_of`).
FORMAT = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS block (
    id     INTEGER PRIMARY KEY,
    file   INTEGER NOT NULL,
    name   TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE (file, name)
);
CREATE TABLE IF NOT EXISTS test (
    id      INTEGER PRIMARY KEY,
    test_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS test_block (
    test  INTEGER NOT NULL,
    block INTEGER NOT NULL,
    PRIMARY KEY (test, block)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS test_block_by_block ON test_block (block, test);
CREATE TABLE IF NOT EXISTS unmeasured_test (test_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: The tables :func:`convert_format_1` rewrites. Every other table in the file
#: belongs to another module and is copied as it stands.
_OWN_TABLES = frozenset({"block", "test_block", "unmeasured_test", "meta"})

# The queries below are module constants so that the tests can ask SQLite for
# each one's plan: all three must be index lookups, never a scan of the pairs.

#: Tests observed executing any block of one file. ``?`` = the file's path.
_TOUCHING = (
    "SELECT DISTINCT t.test_id FROM file f "
    "JOIN block b ON b.file = f.id "
    "JOIN test_block tb ON tb.block = b.id "
    "JOIN test t ON t.id = tb.test "
    "WHERE f.path = ?"
)

#: Tests observed executing one block. ``?, ?`` = the file's path, the block.
_TESTS_OF_BLOCK = (
    "SELECT t.test_id FROM file f "
    "JOIN block b ON b.file = f.id "
    "JOIN test_block tb ON tb.block = b.id "
    "JOIN test t ON t.id = tb.test "
    "WHERE f.path = ? AND b.name = ?"
)

#: Tests with at least one pair: one primary-key probe per test.
_MEASURED = (
    "SELECT t.test_id FROM test t "
    "WHERE EXISTS (SELECT 1 FROM test_block tb WHERE tb.test = t.id)"
)

#: Format 1's pairs as integers. ``old`` is the attached format-1 file; a module
#: constant so a test can make the copy lose a pair and watch the count check
#: refuse the result.
_COPY_PAIRS = (
    "INSERT INTO test_block (test, block) "
    "SELECT t.id, b.id FROM old.test_block tb "
    "JOIN test t ON t.test_id = tb.test_id "
    "JOIN file f ON f.path = tb.path "
    "JOIN block b ON b.file = f.id AND b.name = tb.name"
)


class IndexFormatError(RuntimeError):
    """The file is not an index this code can read, or a conversion of one
    did not produce every pair. The file on disk is left as it was."""


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
    #: Changed or vanished blocks whose statements run at IMPORT (a module
    #: body, a class body outside any function). No test has a coverage row
    #: for those lines, so ``tests`` cannot name who runs them.
    import_time_changed: frozenset[BlockKey] = frozenset()


def _format_of(conn: sqlite3.Connection) -> int:
    """The format a file holds; 0 for a file with no index in it yet."""
    stamped = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if stamped:
        return stamped
    columns = {row[1] for row in conn.execute("PRAGMA table_info(test_block)")}
    return 1 if "test_id" in columns else 0


def open_index(path: Path) -> sqlite3.Connection:
    """Open the index database, creating or converting it as needed.

    A format-1 file is converted first (:func:`convert_format_1`). A file
    stamped with any other format than :data:`FORMAT` raises
    :class:`IndexFormatError` and is left alone: creating this format's empty
    tables beside another format's data would make every lookup miss.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    found = _format_of(conn)
    if found == 1:
        conn.close()
        convert_format_1(path)
        conn = sqlite3.connect(str(path))
        found = _format_of(conn)
    if found not in (0, FORMAT):
        conn.close()
        raise IndexFormatError(
            f"{path} holds selection-index format {found}; "
            f"this code reads format {FORMAT}")
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {FORMAT}")
    conn.commit()
    return conn


def _sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _free_backup_name(path: Path) -> Path:
    """``<name>.format-1``, or the first ``<name>.format-1.N`` not taken."""
    first = _sibling(path, ".format-1")
    candidate, n = first, 1
    while candidate.exists():
        n += 1
        candidate = _sibling(first, f".{n}")
    return candidate


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _write_format_2(source: Path, work: Path) -> None:
    """Write ``source``'s (format 1) content to a new file ``work`` in format 2.

    One pass in SQL, reading ``source`` through ATTACH and writing nothing to
    it. Names get their numbers in sorted order, so the tests of one test file
    and the blocks of one source file are neighbours in both pair indexes.
    """
    with closing(sqlite3.connect(str(work))) as new:
        new.executescript(_SCHEMA)
        new.execute("ATTACH DATABASE ? AS old", (str(source),))
        new.execute(
            "INSERT INTO file (path) "
            "SELECT path FROM old.block UNION SELECT path FROM old.test_block")
        new.execute(
            "INSERT INTO block (file, name, digest) "
            "SELECT f.id, b.name, b.digest FROM old.block b "
            "JOIN file f ON f.path = b.path")
        # A pair whose block has no ``block`` row still needs a number.
        new.execute(
            "INSERT OR IGNORE INTO block (file, name, digest) "
            "SELECT DISTINCT f.id, tb.name, '' FROM old.test_block tb "
            "JOIN file f ON f.path = tb.path")
        new.execute(
            "INSERT INTO test (test_id) "
            "SELECT DISTINCT test_id FROM old.test_block")
        new.execute(_COPY_PAIRS)
        want = new.execute("SELECT count(*) FROM old.test_block").fetchone()[0]
        got = new.execute("SELECT count(*) FROM test_block").fetchone()[0]
        if got != want:
            raise IndexFormatError(
                f"converting {source} kept {got} of {want} test-block pairs; "
                "the format-1 file is unchanged")
        new.execute(
            "INSERT INTO unmeasured_test (test_id) "
            "SELECT test_id FROM old.unmeasured_test")
        new.execute(
            "INSERT INTO meta (key, value) SELECT key, value FROM old.meta")
        # Tables other modules keep in this file (the read evidence of
        # prose_only), copied as they stand: tables first, then their indexes.
        others = new.execute(
            "SELECT type, name, tbl_name, sql FROM old.sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type != 'table'").fetchall()
        for kind, name, table, sql in others:
            if table in _OWN_TABLES:
                continue
            new.execute(sql)
            if kind == "table":
                # The one identifier comes from the source file's own schema
                # and is quoted; nothing else is interpolated.
                q = _quoted(name)
                copy = f"INSERT INTO main.{q} SELECT * FROM old.{q}"  # noqa: S608  # nosec B608
                new.execute(copy)
        new.execute(f"PRAGMA user_version = {FORMAT}")
        new.commit()
        new.execute("DETACH DATABASE old")


def convert_format_1(path: Path) -> Optional[Path]:
    """Rewrite a format-1 index at ``path`` as format 2, keeping the old file.

    Returns the path the format-1 file was kept under (``<name>.format-1``, or
    ``.format-1.N`` when that is taken), or ``None`` when ``path`` was not
    format 1 by the time this call held the lock. No test is run: format 1
    holds every fact format 2 needs.

    THE NEW FILE IS WRITTEN BESIDE THE OLD ONE AND SWAPPED IN. Until the swap
    ``path`` is the untouched format-1 file, so a conversion that fails or is
    killed loses nothing, and the next open starts it again. The swap is a hard
    link (the backup name) followed by an atomic replace, so ``path`` exists at
    every instant.

    ONE CONVERTER AT A TIME, by ``flock`` on ``<name>.lock``. An opener that
    waited for the lock re-reads the format under it: converting the file a
    winner has just swapped in would move the NEW index over the backup. The
    lock file is removed only once ``path`` is no longer format 1, because from
    then on nobody takes the lock; removing it after a failed conversion would
    let a waiter and a newcomer hold two different lock files at once.

    A process that is writing to the format-1 file while it is being converted
    loses those writes. smart-test folds a run under its own lock, and this is
    called from inside that, so the case needs two tools run by hand at once.
    """
    path = Path(path)
    lock_path = _sibling(path, ".lock")
    work = _sibling(path, ".converting")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(str(path))) as probe:
            still_format_1 = _format_of(probe) == 1
        kept: Optional[Path] = None
        if still_format_1:
            # A killed conversion leaves the work file and perhaps its journal;
            # SQLite would replay a stale journal into the new work file.
            leftovers = [_sibling(work, s) for s in ("", "-journal", "-wal", "-shm")]
            for leftover in leftovers:
                leftover.unlink(missing_ok=True)
            try:
                _write_format_2(path, work)
                kept = _free_backup_name(path)
                kept.hardlink_to(path)
                work.replace(path)
            finally:
                for leftover in leftovers:
                    leftover.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
        return kept


def _numbered(
    cur: sqlite3.Cursor, insert: str, select: str, names: Iterable[str],
) -> dict[str, int]:
    """Store each name once and return its number."""
    wanted = sorted(set(names))
    cur.executemany(insert, [(n,) for n in wanted])
    return {n: cur.execute(select, (n,)).fetchone()[0] for n in wanted}


def store_edges(
    conn: sqlite3.Connection,
    edges: Mapping[str, Iterable[BlockKey]],
    *,
    digests: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> None:
    """Replace the edges of each test in ``edges``; record ``digests``.

    ``edges`` maps a test id to the ``(path, block name)`` keys it executed.
    A test named here has its earlier edges REMOVED first (semantic 1 of the
    module docstring); a test not named keeps what it had. ``digests`` maps a
    path to ``{block name: digest}`` and may name blocks no test executed. A
    block that appears only in ``edges`` is stored with an empty digest.

    Does not commit: :func:`update_from_run` stores a run's edges, its
    unmeasured tests and the full-run marker in one transaction.
    """
    digests = digests or {}
    keys = {key for per_test in edges.values() for key in per_test}
    cur = conn.cursor()
    file_ids = _numbered(
        cur, "INSERT OR IGNORE INTO file (path) VALUES (?)",
        "SELECT id FROM file WHERE path = ?",
        [*digests, *(path for path, _ in keys)])
    cur.executemany(
        "INSERT INTO block (file, name, digest) VALUES (?, ?, ?) "
        "ON CONFLICT (file, name) DO UPDATE SET digest = excluded.digest",
        [(file_ids[path], name, digest)
         for path, per_name in digests.items()
         for name, digest in per_name.items()])
    cur.executemany(
        "INSERT OR IGNORE INTO block (file, name, digest) VALUES (?, ?, '')",
        sorted((file_ids[path], name) for path, name in keys))
    block_ids: dict[BlockKey, int] = {}
    for path in sorted({path for path, _ in keys}):
        for name, block_id in cur.execute(
                "SELECT name, id FROM block WHERE file = ?", (file_ids[path],)):
            block_ids[(path, name)] = block_id
    test_ids = _numbered(
        cur, "INSERT OR IGNORE INTO test (test_id) VALUES (?)",
        "SELECT id FROM test WHERE test_id = ?", edges)
    for test_id, per_test in edges.items():
        cur.execute("DELETE FROM test_block WHERE test = ?", (test_ids[test_id],))
        cur.executemany(
            "INSERT OR IGNORE INTO test_block (test, block) VALUES (?, ?)",
            sorted((test_ids[test_id], block_ids[key]) for key in set(per_test)))


def stored_edges(conn: sqlite3.Connection) -> Iterator[tuple[str, str, str]]:
    """Every ``(test id, path, block name)`` pair in the index, unordered."""
    yield from conn.execute(
        "SELECT t.test_id, f.path, b.name FROM test_block tb "
        "JOIN test t ON t.id = tb.test "
        "JOIN block b ON b.id = tb.block "
        "JOIN file f ON f.id = b.file")


def measured_tests(conn: sqlite3.Connection) -> frozenset[str]:
    """Test ids with at least one coverage edge."""
    return frozenset(r[0] for r in conn.execute(_MEASURED))


def touching_tests(
    conn: sqlite3.Connection, paths: Iterable[str],
) -> frozenset[str]:
    """Test ids observed executing ANY block of ``paths`` (as stored)."""
    return frozenset(
        r[0] for path in sorted(set(paths))
        for r in conn.execute(_TOUCHING, (path,)))


def _strip_phase(context: str) -> str:
    """``test|setup`` -> ``test``. pytest-cov records a context per phase."""
    return context.rsplit("|", 1)[0] if "|" in context else context


def normalize_test_id(node_id: str, repo_root: Optional[Path]) -> str:
    """Rewrite a pytest node id to be repo-relative, or leave it untouched.

    Node ids are relative to pytest's ROOTDIR, which varies by invocation: a run
    confined to one package makes that package the rootdir (several of ours
    carry ``[tool.pytest.ini_options]``), while a run spanning packages uses the
    repo. The same test therefore has two spellings, and storing both fragments
    the index into rows that never join — the same failure class as a
    ``|setup`` phase suffix left on a context (see ``_strip_phase``), which
    makes no context join at all.

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

    # REPLACE per test, and only for tests that appear in THIS run.
    store_edges(conn, edges, digests=digests)
    cur = conn.cursor()
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
    # touch nothing in scope. Trusting that would mark most of the suite
    # unmeasured after a single targeted run -- and select_tests EXCLUDES
    # unmeasured tests, so most of the suite would become permanently
    # unselectable while every unit test stayed green.
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
    the whole correctness of this function. The digests stored in the index are
    rewritten after every run, so diffing against them moves the baseline every
    time: once a run has stored the working tree, the next comparison is the
    working tree against itself, which finds nothing and selects zero tests.
    That answers "what changed since I last looked" when the question is "what
    changed in this commit".

    So change detection is git's job (``git show <base>:<path>``) and this
    function only diffs. ``None`` means the path did not exist at the base, i.e.
    the commit added it: every block is NEW and therefore has no test history.
    A path that is now unreadable had all of its base blocks removed, so they
    all count as changed — those tests exercised code that is gone.
    """
    changed, new, _import_time = _diff_blocks(base_sources)
    return changed, new


def _diff_blocks(
    base_sources: Mapping[str, Optional[str]],
) -> tuple[frozenset[BlockKey], frozenset[BlockKey], frozenset[BlockKey]]:
    """``(changed, new, import-time changed)``; see :func:`changed_blocks`.

    The third set is the subset of ``changed`` whose statements run at import.
    A vanished block is judged by what it was at the base, since the current
    source no longer says.
    """
    changed: set[BlockKey] = set()
    new: set[BlockKey] = set()
    import_time: set[BlockKey] = set()
    for path, base_src in base_sources.items():
        try:
            current = {b.name: b
                       for b in blocks_for_source(path, Path(path).read_text())}
        except (OSError, SyntaxError):
            current = {}
        if base_src is None:
            new.update((path, name) for name in current)
            continue
        try:
            base = {b.name: b for b in blocks_for_source(path, base_src)}
        except SyntaxError:  # pragma: no cover - base commit did not parse
            continue
        for name, block in current.items():
            if name not in base:
                new.add((path, name))
            elif base[name].digest != block.digest:
                changed.add((path, name))
                if block.import_time:
                    import_time.add((path, name))
        # Vanished blocks are the ONLY signal for a rename, move or deletion,
        # because children are removed from a parent's digest entirely.
        for name in set(base) - set(current):
            changed.add((path, name))
            if base[name].import_time:
                import_time.add((path, name))
    return frozenset(changed), frozenset(new), frozenset(import_time)


def select_tests(
    conn: sqlite3.Connection, base_sources: Mapping[str, Optional[str]],
) -> Selection:
    """Tests attached to blocks that changed relative to the supplied base."""
    unknown = {
        p for p in base_sources
        if not conn.execute(
            "SELECT 1 FROM block b JOIN file f ON f.id = b.file "
            "WHERE f.path = ? LIMIT 1", (p,)).fetchone()
    }
    changed, new, import_time = _diff_blocks(base_sources)
    tests: set[str] = set()
    for path, name in changed:
        tests.update(r[0] for r in conn.execute(_TESTS_OF_BLOCK, (path, name)))
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
        import_time_changed=import_time,
    )


# ── Node ids to run-set files ───────────────────────────────────────────────
#
# These live HERE rather than in selection_shadow because they convert the
# INDEX's output into the run set's vocabulary, and they have three
# consumers: the shadow comparison, the union, and narrowing. Importing them
# from `selection_shadow` would make narrowing appear to depend on the shadow,
# which it does not.


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

    Two narrowings, both required for widening a run safely:

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
    imports it, failing collection with ``fixture 'conn' not found``, an error
    that names nothing about its real cause.

    THE KEEP SET FOR NARROWING, and deliberately wider than the changed-block
    selection. The local coverage gate is whole-file
    (``coverage report --include=<changed files> --fail-under=100``), so keeping
    only the changed blocks' tests leaves the rest of each changed file
    uncovered and fails the gate. These are exactly the tests that produced the
    file's coverage rows.

    ONE INDEXED QUERY PER PATH (:func:`touching_tests`), walking from the file
    to its blocks to their tests, so the cost follows the changed files and not
    the size of the index.
    """
    return files_of(rebase_to_repo(touching_tests(conn, paths), repo_root))


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
    is conservative in a way worth naming: most files with unmeasured tests
    are only PARTLY unmeasured — a single unmeasured test holds back a file of
    hundreds — so this rule keeps considerably more than node-granularity
    would.
    """
    measured = files_of(rebase_to_repo(measured_tests(conn), repo_root))
    unmeasured = files_of(rebase_to_repo(
        {r[0] for r in conn.execute("SELECT test_id FROM unmeasured_test")},
        repo_root))
    return measured - unmeasured


# ── Narrowing (history: WI-bolot) ───────────────────────────────────────────
#
# Everything above only ever ADDS tests, so being wrong costs time. Narrowing can
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
    # AN IMPORT IS NOT A COVERAGE ROW. A module or class body runs when the
    # file is imported, under no test's context, so a test that only imports
    # the file has no row for it, and narrowing would read that absence as
    # proof of irrelevance. Every importer runs the changed code; the index
    # does not hold import edges, so the only safe keep set is the run set.
    # Most commits that modify package source change such a block, so this
    # refuses on most real changes. That is the cost of soundness here, not an
    # accident. A test file is exempt: its importers are not tests, and it
    # enters the run set as a changed test. (history: WI-zunug)
    for p, name in sorted(selection.import_time_changed):
        r = rel(p)
        if not _is_test_path(r) and _is_coverable(r):
            out.append(f"import-time code changed: {r} ({name})")
    return tuple(out)


def narrow_run_set(
    run_set: Iterable[str],
    *,
    keep: Iterable[str],
    droppable: Iterable[str],
    forbidden: Iterable[str],
    changed: Iterable[str] = (),
) -> Narrowing:
    """Shrink ``run_set`` to ``keep`` plus everything not safely droppable.

    ``keep`` is the FILE-TOUCH set — every test observed executing any block of
    a changed file, not merely the changed blocks — because the local coverage
    gate is whole-file. ``droppable`` is the set the index can speak about.
    Anything in ``run_set`` and outside ``droppable`` survives regardless, and
    so does every ``changed`` path in it: a test file the change edits is not in
    the coverage scope, so no row ties it to the change, yet it is the change's
    own evidence and must run.
    """
    original = frozenset(run_set)
    reasons = tuple(forbidden)
    if reasons:
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="; ".join(reasons))

    keep_set, drop_ok = frozenset(keep) | frozenset(changed), frozenset(droppable)
    kept = frozenset(f for f in original if f in keep_set or f not in drop_ok)
    if not kept:
        # pytest exits 5 — GREEN — when it collects nothing, so a narrowing to
        # zero would read as a pass while running no tests at all.
        return Narrowing(kept=original, dropped=frozenset(),
                         refusal="narrowing would leave an empty run set")
    return Narrowing(kept=kept, dropped=original - kept, refusal=None)
