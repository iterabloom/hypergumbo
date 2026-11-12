# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prose-only changes: which tests a comment or docstring edit can affect.

WHAT IT IS. A changed ``.py`` file is PROSE-ONLY when its code is unchanged and
only comments or docstrings differ. Such a change cannot alter what any test
executes, so smart-test need not run every test that imports the file. It must
still run the tests whose outcome can depend on the file's TEXT. This module
decides which files are prose-only, stores the evidence about which tests can
see which text, and computes the tests such a change must run
(:func:`select_for_prose`). The import graph plays no part: the selection is
made from the record alone, over every test file in the repository.

HOW A TEST CAN OBSERVE A PROSE-ONLY CHANGE. The compiled code of a prose-only
change differs from its baseline in three things only: the source text, the
docstrings, and the line numbers. The pytest plugin
``scripts/pytest_plugins/pytest_source_reads.py`` records each way of seeing
one of them, per test, and :func:`fold_run` stores the record:

1. **It reads the file as data** -- a gate that scans sources, a test that pins
   a line number or greps a docstring, a generator, ``inspect.getsource``, a
   formatted traceback. A test with a recorded read of a prose-only file
   runs, whether or not it imports the file.
2. **It reads a docstring at runtime** (``x.__doc__``). The record names the
   definition whose docstring was read, so the test runs only when THAT
   docstring changed (:func:`changed_docstrings`). A read the plugin could not
   tie to a definition counts for every docstring.
3. **It reads a line number at runtime** (``frame.f_lineno``). The record
   names the file; a read that could not be tied to one counts for every file.
4. **It starts a process** whose reads the plugin cannot see. Such a test is
   OPAQUE and always runs, unless every program it starts is in
   :data:`INERT_PROGRAMS`.
5. **It is the changed file.** A test file always runs when it changed.

LEAVING A TEST OUT NEEDS POSITIVE EVIDENCE. A test file is left out only when
:func:`tests_for_prose` finds no reason to run it: its record was taken at
its current code digest, every test it collects has a record, the record
includes runtime observation (Python 3.12+), and none of the five ways above
applies. Anything unknown runs.

WHAT IS PROSE-ONLY. :func:`classify` compares the base and current source:
the AST with docstrings removed must be equal, and so must the machine-read
comments (``# pragma:``, ``# covers:``, ``# type:``, ``# noqa`` ...), because a
changed ``# pragma: no cover`` changes the coverage gate's answer. Those are
matched by pattern per line, so prose that quotes one counts as one. Docstrings
are located as the first statement of a module, class or function; a string
anywhere else is code. A new, deleted or unparsable file is code.

A read or observation made outside any test (a conftest import, a module body
that scans sources at import) holds for EVERY test: a file seen that way is
never prose-only (:func:`eligible_prose_files`). A process started that way by
repository code makes the whole rule refuse (:func:`global_refusal`).

THE EVIDENCE HAS A VERSION (:data:`RECORDER_VERSION`). When the plugin changes
what a record means, every stored record is discarded: an old record cannot
show that a test does NOT do something the old plugin never looked for.

LIMITS, stated because they are where this would bite:

* The evidence is as old as the last run that recorded each test. A code
  change elsewhere that makes an already-recorded test start reading a file is
  invisible until that test runs again. Every local run records, and the
  twice-daily full suite in CI runs every test regardless of this rule.
* A value computed from a file's text by one test and kept in a module global
  for another (outside a fixture, outside import time, outside ``linecache``)
  is not seen.
* A docstring or line number read by third-party code (a CLI library that
  builds help text from ``__doc__``), or through an attribute named plain
  ``lineno`` (a ``LogRecord``, a ``FrameSummary``), is not seen.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

#: The record format this module reads; ``pytest_source_reads.VERSION``.
RECORDER_VERSION = 2

#: Programs that cannot read a repository ``.py`` file whatever they are
#: given, so a test that starts only these is not opaque. ``rust-analyzer``
#: is here because the pipeline probes for it in most tests, and it reads
#: Rust projects only.
INERT_PROGRAMS = frozenset({
    "rust-analyzer", "rustup", "ldconfig", "sleep", "true", "false", "echo",
    "pwd", "mktemp",
})

#: Above this share of test files with no usable record, the rule gives way
#: to the import graph. Both selections are safe; this is about cost. A test
#: file the record says nothing about must run, and with few records "must
#: run" is most of the suite, which is more than the import graph selects.
MAX_UNKNOWN_SHARE = 0.10

#: Why a test file runs for lack of a usable record (see tests_for_prose).
NO_RECORD = "no read record"
STALE_RECORD = "read record predates a code change to the test"
INCOMPLETE_RECORD = "read record is incomplete"
UNOBSERVED_RECORD = "recorded without runtime observation (needs Python 3.12)"
_UNKNOWN = frozenset({NO_RECORD, STALE_RECORD, INCOMPLETE_RECORD, UNOBSERVED_RECORD})

READ, DOC, LINE = "r", "d", "l"
#: A docstring or line-number read the plugin could not tie to a definition.
ANY = "*"

_TABLES = ("read_test", "read_edge", "read_file", "read_expected",
           "read_global", "read_meta")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_test (
    test_id   TEXT PRIMARY KEY,
    test_file TEXT NOT NULL,
    opaque    TEXT NOT NULL,
    observed  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS read_test_by_file ON read_test (test_file);
CREATE TABLE IF NOT EXISTS read_edge (
    test_id TEXT NOT NULL,
    kind    TEXT NOT NULL,
    target  TEXT NOT NULL,
    PRIMARY KEY (test_id, kind, target)
);
CREATE TABLE IF NOT EXISTS read_file (
    test_file TEXT PRIMARY KEY,
    digest    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS read_expected (
    test_file TEXT NOT NULL,
    test_id   TEXT NOT NULL,
    PRIMARY KEY (test_file, test_id)
);
CREATE TABLE IF NOT EXISTS read_global (
    kind   TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (kind, target)
);
CREATE TABLE IF NOT EXISTS read_meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: Comments a tool reads. A change to one is a code change for this rule.
_MACHINE_COMMENT = re.compile(
    r"#\s*(?:pragma\b|type:|noqa\b|covers:|axis:|fmt:|pylint:|mypy:|nosec\b|"
    r"isort:|SPDX-)[^#\n]*"
    r"|^#!.*|#.*coding[:=].*")

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

CODE, DOCSTRINGS, COMMENTS = "code", "docstrings", "comments"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the evidence tables, discarding any of another version.

    The evidence is rebuilt by running tests, so a table from an older layout
    or an older recorder is dropped rather than migrated.
    """
    row = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'read_meta'").fetchone():
        row = conn.execute(
            "SELECT value FROM read_meta WHERE key = 'recorder'").fetchone()
    if row is None or row[0] != str(RECORDER_VERSION):
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO read_meta (key, value) VALUES ('recorder', ?)",
        (str(RECORDER_VERSION),))
    conn.commit()


# ── What is prose-only ──────────────────────────────────────────────────────


def _machine_comments(source: str) -> list[str]:
    """Machine-read comments in ``source``: a ``#`` directly followed by a
    directive (``# pragma: no cover``, ``# type: ignore``), a shebang, or an
    encoding line.

    A pattern, not a parser: a directive quoted in a docstring or a string
    (``# noqa``) is included. That errs toward calling a change code, which
    only costs the rule an opportunity. Prose that merely contains a
    directive's word ("the node type: ...") is not one.
    """
    return sorted(
        match.group(0).strip()
        for line in source.splitlines()
        for match in _MACHINE_COMMENT.finditer(line))


def _take_docstrings(tree: ast.Module) -> dict[str, list[str]]:
    """Remove every docstring from ``tree`` and return them by owner.

    The owner is named as Python names it at runtime (``__qualname__``:
    ``Class.method``, ``outer.<locals>.inner``), and the module's own is
    ``<module>``, so a recorded read of ``x.__doc__`` can be matched to the
    docstring that changed. A name defined twice (a property and its setter)
    keeps every docstring, in order.
    """
    found: dict[str, list[str]] = {}

    def take(node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
             name: str) -> None:
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            found.setdefault(name, []).append(first.value.value)
            node.body = node.body[1:] or [ast.Pass()]

    def scan(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCTIONS):
                take(child, prefix + child.name)
                scan(child, f"{prefix}{child.name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                take(child, prefix + child.name)
                scan(child, f"{prefix}{child.name}.")
            else:
                scan(child, prefix)

    take(tree, "<module>")
    scan(tree, "")
    return found


def _parse(source: str) -> Optional[tuple[ast.Module, dict[str, list[str]]]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    return tree, _take_docstrings(tree)


def prose_digests(source: str) -> Optional[tuple[str, str]]:
    """``(code digest, docstring digest)`` of ``source``; None if unparsable.

    The code digest covers the AST with docstrings removed plus the
    machine-read comments. Two sources with equal code digests differ only in
    prose.
    """
    parsed = _parse(source)
    if parsed is None:
        return None
    tree, docs = parsed
    code = (ast.dump(tree, include_attributes=False) + "\0"
            + "\n".join(_machine_comments(source)))
    return (hashlib.sha256(code.encode()).hexdigest(),
            hashlib.sha256(json.dumps(docs, sort_keys=True).encode()).hexdigest())


def classify(base: Optional[str], current: Optional[str]) -> str:
    """How ``current`` differs from ``base``: code, docstrings or comments.

    ``code`` is the answer whenever prose-only cannot be shown: a file that is
    new, deleted or unparsable on either side.
    """
    if base is None or current is None:
        return CODE
    old, new = prose_digests(base), prose_digests(current)
    if old is None or new is None or old[0] != new[0]:
        return CODE
    return COMMENTS if old[1] == new[1] else DOCSTRINGS


def changed_docstrings(base: str, current: str) -> frozenset[str]:
    """The owners (see :func:`_take_docstrings`) whose docstring differs
    between two sources, added and removed ones included. Empty when either
    side does not parse: :func:`classify` already calls that code."""
    old, new = _parse(base), _parse(current)
    if old is None or new is None:
        return frozenset()
    return frozenset(
        name for name in old[1].keys() | new[1].keys()
        if old[1].get(name) != new[1].get(name))


# ── Storing a run's evidence ────────────────────────────────────────────────


def _file_digest(path: Path) -> str:
    """A test file's code digest; its raw hash when it does not parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    digests = prose_digests(text)
    return digests[0] if digests else hashlib.sha256(text.encode()).hexdigest()


def _raw_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, as the recorder takes it; empty if unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


_CHANNELS = (("reads", READ), ("docs", DOC), ("lines", LINE))
_Events = dict[str, set[str]]


def _events() -> _Events:
    return {"reads": set(), "docs": set(), "lines": set(), "opaque": set()}


def _merge(into: _Events, bucket: Mapping[str, Iterable[str]]) -> None:
    for key, values in into.items():
        values.update(bucket.get(key, ()))


def fold_run(
    conn: sqlite3.Connection, reads_dir: Path, repo_root: Path, *,
    full_run: bool = False, skip: Iterable[str] = (),
) -> int:
    """Store one run's record; returns the number of tests stored.

    NOT STORED: a test that failed (named in the record, or in ``skip`` by a
    caller that knows from elsewhere), because it may have stopped before a
    read it would have made; and every test of a file that no longer hashes
    as it did when the run collected it, because the record may be stored
    long after the run (one that was killed) and would be credited to code
    it never saw. An earlier record of such a test stands.

    ``reads_dir`` holds one JSON file per pytest process; a file written by
    another version of the plugin is skipped. Per test the stored events are
    its own, those of every fixture it used (unioned across processes by
    fixture key, because one worker may build what another reuses) and those
    made while its module was imported.

    REPLACE PER TEST, like the coverage index: a test in this run replaces its
    earlier record, others are untouched. A test file whose code digest
    changed loses all its records first, since they describe other code.
    Unattributed events accumulate, and are reset only by a full run.
    """
    ensure_schema(conn)
    tests: dict[str, _Events] = {}
    observed: dict[str, bool] = {}
    used: dict[str, set[str]] = {}
    fixtures: dict[str, _Events] = {}
    modules: dict[str, _Events] = {}
    expected: dict[str, set[str]] = {}
    loose = _events()
    untrusted = set(skip)
    edited: set[str] = set()

    for part in sorted(reads_dir.glob("*.json")):
        try:
            data = json.loads(part.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("version") != RECORDER_VERSION:
            continue
        untrusted.update(data.get("failed", ()))
        for test_file, digest in data.get("sources", {}).items():
            if digest != _raw_digest(repo_root / test_file):
                edited.add(test_file)
        for test_id, bucket in data.get("tests", {}).items():
            _merge(tests.setdefault(test_id, _events()), bucket)
            used.setdefault(test_id, set()).update(bucket.get("fixtures", ()))
            observed[test_id] = bool(data.get("observed")) and observed.get(test_id, True)
        for key, bucket in data.get("fixtures", {}).items():
            _merge(fixtures.setdefault(key, _events()), bucket)
        for key, bucket in data.get("modules", {}).items():
            _merge(modules.setdefault(key, _events()), bucket)
        for test_file, ids in data.get("expected", {}).items():
            expected.setdefault(test_file, set()).update(ids)
        _merge(loose, data.get("unattributed", {}))
    for test_id in list(tests):
        if test_id in untrusted or test_id.split("::", 1)[0] in edited:
            del tests[test_id]

    cur = conn.cursor()
    by_file: dict[str, list[str]] = {}
    for test_id in tests:
        by_file.setdefault(test_id.split("::", 1)[0], []).append(test_id)
    for test_file, ids in by_file.items():
        digest = _file_digest(repo_root / test_file)
        row = cur.execute("SELECT digest FROM read_file WHERE test_file = ?",
                          (test_file,)).fetchone()
        if row is None or row[0] != digest:
            cur.execute("DELETE FROM read_edge WHERE test_id IN "
                        "(SELECT test_id FROM read_test WHERE test_file = ?)",
                        (test_file,))
            cur.execute("DELETE FROM read_test WHERE test_file = ?", (test_file,))
        cur.execute(
            "INSERT INTO read_file (test_file, digest) VALUES (?, ?) "
            "ON CONFLICT (test_file) DO UPDATE SET digest = excluded.digest",
            (test_file, digest))
        if test_file in expected:
            cur.execute("DELETE FROM read_expected WHERE test_file = ?", (test_file,))
            cur.executemany(
                "INSERT OR IGNORE INTO read_expected (test_file, test_id) VALUES (?, ?)",
                [(test_file, i) for i in sorted(expected[test_file])])
        for test_id in ids:
            events = _events()
            _merge(events, tests[test_id])
            _merge(events, modules.get(test_file, {}))
            for key in used.get(test_id, ()):
                _merge(events, fixtures.get(key, {}))
            cur.execute("DELETE FROM read_edge WHERE test_id = ?", (test_id,))
            cur.execute(
                "INSERT INTO read_test (test_id, test_file, opaque, observed) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (test_id) DO UPDATE SET "
                "test_file = excluded.test_file, opaque = excluded.opaque, "
                "observed = excluded.observed",
                (test_id, test_file, ",".join(sorted(events["opaque"])),
                 int(observed[test_id])))
            cur.executemany(
                "INSERT OR IGNORE INTO read_edge (test_id, kind, target) VALUES (?, ?, ?)",
                [(test_id, kind, target) for name, kind in _CHANNELS
                 for target in sorted(events[name])])

    if full_run:
        cur.execute("DELETE FROM read_global")
        cur.execute("DELETE FROM read_meta WHERE key = 'global_opaque'")
    cur.executemany(
        "INSERT OR IGNORE INTO read_global (kind, target) VALUES (?, ?)",
        [(kind, target) for name, kind in _CHANNELS for target in sorted(loose[name])])
    spawned = sorted(loose["opaque"])
    if spawned:
        row = cur.execute(
            "SELECT value FROM read_meta WHERE key = 'global_opaque'").fetchone()
        merged = sorted(set(spawned) | set((row[0] if row else "").split(",")) - {""})
        cur.execute(
            "INSERT INTO read_meta (key, value) VALUES ('global_opaque', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (",".join(merged),))
    conn.commit()
    return len(tests)


# ── Deciding ────────────────────────────────────────────────────────────────


def _not_inert(spawns: str) -> list[str]:
    """The recorded spawns (``event:program``, comma-joined) that could read
    a repository file."""
    return [s for s in spawns.split(",")
            if s and s.rpartition(":")[2] not in INERT_PROGRAMS]


def global_refusal(conn: sqlite3.Connection) -> Optional[str]:
    """Why the rule must not act at all, or None."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT value FROM read_meta WHERE key = 'global_opaque'").fetchone()
    spawned = _not_inert(row[0]) if row and row[0] else []
    if spawned:
        return f"a process was started outside any test ({','.join(spawned)})"
    if conn.execute("SELECT 1 FROM read_test LIMIT 1").fetchone() is None:
        return "no read evidence recorded yet (run smart-test --full once)"
    return None


def _doc_hit(targets: Iterable[str],
             changed_docs: Mapping[str, frozenset[str]]) -> Optional[str]:
    """The changed docstring one of these recorded reads could have seen.

    A target is ``path::owner``, ``path::*`` (any docstring of that file) or
    ``*`` (any docstring at all).
    """
    for target in sorted(targets):
        if target == ANY:
            for path in sorted(changed_docs):
                if changed_docs[path]:
                    return f"a docstring it cannot be tied to (so that of {path})"
            continue
        path, _, owner = target.partition("::")
        owners = changed_docs.get(path, frozenset())
        if owners and (owner == ANY or owner in owners):
            return f"the docstring of {path}::{sorted(owners)[0] if owner == ANY else owner}"
    return None


def _line_hit(targets: Iterable[str], prose: Mapping[str, str]) -> Optional[str]:
    """The prose-only file whose line numbers one of these reads could have
    seen. A target is a path, or ``*`` for any file."""
    for target in sorted(targets):
        if target == ANY and prose:
            return f"line numbers it cannot be tied to (so those of {sorted(prose)[0]})"
        if target in prose:
            return f"the line numbers of {target}"
    return None


def eligible_prose_files(
    conn: sqlite3.Connection,
    kinds: Mapping[str, str],
    changed_docs: Mapping[str, frozenset[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split changed files into prose-only ones and the rest.

    ``kinds`` maps each changed repo-relative ``.py`` path to its
    :func:`classify` result and ``changed_docs`` to its
    :func:`changed_docstrings`. Returns ``(prose, rejected)``: ``prose`` maps
    the eligible paths to DOCSTRINGS or COMMENTS, ``rejected`` maps the other
    prose-changed paths to the reason. Files classified as code appear in
    neither.
    """
    ensure_schema(conn)
    everyone: dict[str, set[str]] = {READ: set(), DOC: set(), LINE: set()}
    for kind, target in conn.execute("SELECT kind, target FROM read_global"):
        everyone[kind].add(target)
    prose: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for path, kind in sorted(kinds.items()):
        if kind == CODE:
            continue
        own_docs = {path: changed_docs.get(path, frozenset())}
        if path in everyone[READ]:
            rejected[path] = "read outside any test, so every test may depend on it"
        elif _line_hit(everyone[LINE], {path: kind}):
            rejected[path] = "its line numbers are read outside any test"
        elif _doc_hit(everyone[DOC], own_docs):
            rejected[path] = "a docstring it changed is read outside any test"
        else:
            prose[path] = kind
    return prose, rejected


def tests_for_prose(
    conn: sqlite3.Connection,
    repo_root: Path,
    universe: Iterable[str],
    prose: Mapping[str, str],
    changed_docs: Mapping[str, frozenset[str]],
) -> dict[str, str]:
    """The test files a prose-only change must run, each with the reason.

    ``universe`` is every test file on disk, ``prose`` is
    :func:`eligible_prose_files`' first result and ``changed_docs`` gives
    :func:`changed_docstrings` for each of its paths. A test file absent from
    the result has a complete, current record that shows none of the ways of
    seeing the change.

    The import graph is not consulted. Importing a prose-only file gives a
    test nothing the record does not show, and a test that reads the file
    without importing it is found here where the import graph would miss it.
    """
    ensure_schema(conn)
    docs = {path: changed_docs.get(path, frozenset()) for path in prose}

    edges: dict[str, dict[str, set[str]]] = {}
    for test_file, kind, target in conn.execute(
            "SELECT t.test_file, e.kind, e.target FROM read_edge e "
            "JOIN read_test t ON t.test_id = e.test_id"):
        edges.setdefault(test_file, {READ: set(), DOC: set(), LINE: set()})[kind].add(target)
    recorded: dict[str, set[str]] = {}
    spawns: dict[str, set[str]] = {}
    unobserved: set[str] = set()
    for test_id, test_file, opaque, observed in conn.execute(
            "SELECT test_id, test_file, opaque, observed FROM read_test"):
        recorded.setdefault(test_file, set()).add(test_id)
        spawns.setdefault(test_file, set()).update(_not_inert(opaque))
        if not observed:
            unobserved.add(test_file)
    expected: dict[str, set[str]] = {}
    for test_file, test_id in conn.execute(
            "SELECT test_file, test_id FROM read_expected"):
        expected.setdefault(test_file, set()).add(test_id)
    digests = dict(conn.execute("SELECT test_file, digest FROM read_file"))

    def reason(test_file: str) -> Optional[str]:
        if test_file in prose:
            return "is one of the changed files"
        ids = recorded.get(test_file)
        if not ids or test_file not in digests:
            return NO_RECORD
        if digests[test_file] != _file_digest(repo_root / test_file):
            return STALE_RECORD
        wanted = expected.get(test_file)
        if not wanted or not wanted <= ids:
            return INCOMPLETE_RECORD
        if spawns[test_file]:
            return f"starts a process ({','.join(sorted(spawns[test_file]))})"
        if test_file in unobserved:
            return UNOBSERVED_RECORD
        mine = edges.get(test_file, {READ: set(), DOC: set(), LINE: set()})
        hit = sorted(mine[READ] & prose.keys())
        seen = (hit[0] if hit else None) or _line_hit(mine[LINE], prose) \
            or _doc_hit(mine[DOC], docs)
        return f"reads {seen}" if seen else None

    return {test_file: why for test_file in sorted({f for f in universe if f})
            if (why := reason(test_file)) is not None}


@dataclass(frozen=True)
class ProseSelection:
    """What the prose-only rule decides for one change.

    ``files`` maps each prose-only changed path to DOCSTRINGS or COMMENTS;
    ``rejected`` maps the prose-changed paths the rule may not treat that way
    to the reason; ``tests`` maps each test file the prose-only files select
    to why it must run.
    """

    files: Mapping[str, str]
    rejected: Mapping[str, str]
    tests: Mapping[str, str]


def select_for_prose(
    conn: sqlite3.Connection,
    repo_root: Path,
    changed: Mapping[str, tuple[Optional[str], Optional[str]]],
    universe: Iterable[str],
) -> ProseSelection:
    """Apply the rule to a change.

    ``changed`` maps each changed repo-relative ``.py`` path to its
    ``(base, current)`` source, None for a side on which the file does not
    exist. The caller selects tests for every changed file NOT in the
    result's ``files`` by its usual means.

    With too little on record (:data:`MAX_UNKNOWN_SHARE`) no file is treated
    as prose-only and each is rejected with that reason.
    """
    kinds = {path: classify(base, current) for path, (base, current) in changed.items()}
    changed_docs = {
        path: changed_docstrings(base or "", current or "")
        for path, (base, current) in changed.items() if kinds[path] == DOCSTRINGS
    }
    files, rejected = eligible_prose_files(conn, kinds, changed_docs)
    if not files:
        return ProseSelection(files={}, rejected=rejected, tests={})
    everything = sorted({f for f in universe if f})
    tests = tests_for_prose(conn, repo_root, everything, files, changed_docs)
    unknown = sum(1 for why in tests.values() if why in _UNKNOWN)
    if unknown > MAX_UNKNOWN_SHARE * len(everything):
        why = (f"{unknown} of {len(everything)} test files have no usable record "
               "(smart-test --full records them all)")
        return ProseSelection(
            files={}, rejected={**rejected, **dict.fromkeys(files, why)}, tests={})
    return ProseSelection(files=files, rejected=rejected, tests=tests)
