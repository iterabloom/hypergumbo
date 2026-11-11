# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prose-only changes: which tests a comment or docstring edit can affect.

WHAT IT IS. A changed ``.py`` file is PROSE-ONLY when its code is unchanged and
only comments or docstrings differ. Such a change cannot alter what any test
executes, so smart-test need not run every test that imports the file. It must
still run the tests whose outcome can depend on the file's TEXT. This module
decides which files are prose-only, stores the evidence about which tests read
which files, and computes the tests that may be dropped.

HOW A TEST CAN OBSERVE A PROSE-ONLY CHANGE. Three channels, each closed
separately:

1. **It reads the file as data** -- a gate that scans sources, a test that pins
   a line number or greps a docstring, a generator. The pytest plugin
   ``scripts/pytest_plugins/pytest_source_reads.py`` records every such read
   per test; :func:`fold_run` stores it. A test with a recorded read of a
   prose-only file is kept, and is ADDED when the import graph missed it.
2. **It starts a process** whose reads the plugin cannot see. Such a test is
   OPAQUE and is never dropped.
3. **Runtime introspection**, with no file read at decision time:
   ``__doc__``, frame and code line numbers, and ``inspect.getsource`` /
   ``linecache`` (which read once per process and then serve a cache, so only
   the first caller's read is recorded). :func:`introspection` finds the code
   that does this. A test file that does it is never dropped. For a source
   module that does it, the tests the coverage index saw executing that block
   are kept -- for ``getsource`` blocks only when one of those tests has a
   recorded read of the changed file, since that is the only way the cache
   could hold it. A file whose own ``__doc__`` is used (``description=__doc__``)
   is not prose-only when its docstrings change.

DROPPING NEEDS POSITIVE EVIDENCE. A test file is dropped only when
:func:`narrow_for_prose` finds no reason to keep it: its read record was
taken at its current code digest, every test it collects has a record, none is
opaque, none read a prose-only file, the coverage index knows every one of its
tests (rows, or recorded unmeasured on a full run), and none executed an
introspecting block. Anything unknown is kept.

WHAT IS PROSE-ONLY. :func:`classify` compares the base and current source:
the AST with docstrings removed must be equal, and so must the machine-read
comments (``# pragma:``, ``# covers:``, ``# type:``, ``# noqa`` ...), because a
changed ``# pragma: no cover`` changes the coverage gate's answer. Docstrings
are located as the first statement of a module, class or function; a string
anywhere else is code. A new, deleted or unparsable file is code.

A file read outside any test (a conftest import, a module body that scans
sources at import) is recorded as read by EVERY test: such a file is never
prose-only (:func:`eligible_prose_files`). A process started that way by
repository code makes the whole rule refuse (:func:`global_refusal`).

LIMITS, stated because they are where this would bite:

* The evidence is as old as the last run that recorded each test. A code
  change elsewhere that makes an already-recorded test start reading a file is
  invisible until that test runs again. This is the selection index's own
  boundary, and the reason the rule is local-only: CI runs the committed
  manifest, which this rule never touches.
* A value computed from a file's text by one test and kept in a module global
  for another (outside a fixture, outside import time, outside ``linecache``)
  is not seen.
* Line numbers inside formatted traceback text are not treated as
  introspection.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import sqlite3
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from hypergumbo_core.block_hash import blocks_for_source, line_owner
from hypergumbo_core.selection_index import rebase_to_repo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_test (
    test_id   TEXT PRIMARY KEY,
    test_file TEXT NOT NULL,
    opaque    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS read_test_by_file ON read_test (test_file);
CREATE TABLE IF NOT EXISTS read_edge (
    test_id TEXT NOT NULL,
    path    TEXT NOT NULL,
    PRIMARY KEY (test_id, path)
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
CREATE TABLE IF NOT EXISTS read_global (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS read_meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: Comments a tool reads. A change to one is a code change for this rule.
_MACHINE_COMMENT = re.compile(
    r"#!|#.*(?:coding[:=]|SPDX-|pragma|type:|noqa|covers:|axis:|fmt:|pylint:|"
    r"mypy:|nosec|isort:)")

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

CODE, DOCSTRINGS, COMMENTS = "code", "docstrings", "comments"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── What is prose-only ──────────────────────────────────────────────────────


def _machine_comments(source: str) -> Optional[list[str]]:
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _MACHINE_COMMENT.match(tok.string):
                out.append(tok.string.strip())
    except (tokenize.TokenError, SyntaxError):
        return None
    return sorted(out)


def prose_digests(source: str) -> Optional[tuple[str, str]]:
    """``(code digest, docstring digest)`` of ``source``; None if unparsable.

    The code digest covers the AST with docstrings removed plus the
    machine-read comments. Two sources with equal code digests differ only in
    prose.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    machine = _machine_comments(source)
    if machine is None:
        return None
    docs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docs.append(first.value.value)
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.dump(tree, include_attributes=False) + "\0" + "\n".join(machine)
    return (hashlib.sha256(code.encode()).hexdigest(),
            hashlib.sha256(json.dumps(docs).encode()).hexdigest())


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


# ── Runtime introspection ───────────────────────────────────────────────────

_DOC_NAMES = frozenset({"getdoc", "pydoc"})
_LINE_NAMES = frozenset({
    "f_lineno", "tb_lineno", "co_firstlineno", "co_lines", "co_positions",
    "getframeinfo", "extract_stack", "extract_tb", "currentframe", "_getframe",
    "walk_stack", "walk_tb", "getouterframes", "getinnerframes",
    "format_stack", "StackSummary",
})
_SOURCE_NAMES = frozenset({
    "getsource", "getsourcelines", "findsource", "getcomments", "linecache",
    "getline", "getlines", "lazycache",
})
#: Cheap test before parsing: a file with none of these words has no marker.
_ANY_MARKER = re.compile(
    "|".join(sorted({"__doc__", "help", "lineno", *_DOC_NAMES, *_LINE_NAMES,
                     *_SOURCE_NAMES})))


@dataclass(frozen=True)
class Introspection:
    """Lines of one file that introspect docstrings, line numbers or source.

    ``own_doc`` is a bare ``__doc__`` name: the file's own docstring, which
    matters only when that file's docstrings change.
    """

    own_doc: bool = False
    doc: frozenset[int] = frozenset()
    line: frozenset[int] = frozenset()
    source: frozenset[int] = frozenset()

    def any(self) -> bool:
        return bool(self.own_doc or self.doc or self.line or self.source)


def introspection(source: str) -> Introspection:
    """Find runtime introspection in ``source`` (unparsable: none found)."""
    if not _ANY_MARKER.search(source):
        return Introspection()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return Introspection()
    own = False
    doc: set[int] = set()
    line: set[int] = set()
    src: set[int] = set()

    def note(name: str, lineno: int) -> None:
        if name in _DOC_NAMES:
            doc.add(lineno)
        if name in _LINE_NAMES:
            line.add(lineno)
        if name in _SOURCE_NAMES:
            src.add(lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id == "__doc__":
                own = True
            note(node.id, node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr == "__doc__":
                doc.add(node.lineno)
            note(node.attr, node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "__doc__":
                doc.add(node.lineno)
            if "%(lineno)" in node.value:
                line.add(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                for part in alias.name.split("."):
                    note(part, node.lineno)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "help"):
            doc.add(node.lineno)
    return Introspection(own, frozenset(doc), frozenset(line), frozenset(src))


# ── Storing a run's evidence ────────────────────────────────────────────────


def _file_digest(path: Path) -> str:
    """A test file's code digest; its raw hash when it does not parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    digests = prose_digests(text)
    return digests[0] if digests else hashlib.sha256(text.encode()).hexdigest()


def fold_run(
    conn: sqlite3.Connection, reads_dir: Path, repo_root: Path, *,
    full_run: bool = False,
) -> int:
    """Store one run's recorded reads; returns the number of tests stored.

    ``reads_dir`` holds one JSON file per pytest process. Per test the stored
    reads are its own, those of every fixture it used (unioned across
    processes by fixture key, because one worker may build what another
    reuses) and those made while its module was imported.

    REPLACE PER TEST, like the coverage index: a test in this run replaces its
    earlier record, others are untouched. A test file whose code digest
    changed loses all its records first, since they describe other code.
    Unattributed reads accumulate, and are reset only by a full run.
    """
    ensure_schema(conn)
    tests: dict[str, dict[str, set[str]]] = {}
    used: dict[str, set[str]] = {}
    fixtures: dict[str, dict[str, set[str]]] = {}
    modules: dict[str, dict[str, set[str]]] = {}
    expected: dict[str, set[str]] = {}
    global_reads: set[str] = set()
    global_opaque: set[str] = set()

    def merge(into: dict[str, dict[str, set[str]]], key: str,
              bucket: Mapping[str, Sequence[str]]) -> None:
        slot = into.setdefault(key, {"reads": set(), "opaque": set()})
        slot["reads"].update(bucket.get("reads", ()))
        slot["opaque"].update(bucket.get("opaque", ()))

    for part in sorted(reads_dir.glob("*.json")):
        try:
            data = json.loads(part.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for test_id, bucket in data.get("tests", {}).items():
            merge(tests, test_id, bucket)
            used.setdefault(test_id, set()).update(bucket.get("fixtures", ()))
        for key, bucket in data.get("fixtures", {}).items():
            merge(fixtures, key, bucket)
        for key, bucket in data.get("modules", {}).items():
            merge(modules, key, bucket)
        for test_file, ids in data.get("expected", {}).items():
            expected.setdefault(test_file, set()).update(ids)
        loose = data.get("unattributed", {})
        global_reads.update(loose.get("reads", ()))
        global_opaque.update(loose.get("opaque", ()))

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
        module = modules.get(test_file, {"reads": set(), "opaque": set()})
        for test_id in ids:
            reads = set(tests[test_id]["reads"]) | module["reads"]
            opaque = set(tests[test_id]["opaque"]) | module["opaque"]
            for key in used.get(test_id, ()):
                fixture = fixtures.get(key)
                if fixture is not None:
                    reads |= fixture["reads"]
                    opaque |= fixture["opaque"]
            cur.execute("DELETE FROM read_edge WHERE test_id = ?", (test_id,))
            cur.execute(
                "INSERT INTO read_test (test_id, test_file, opaque) VALUES (?, ?, ?) "
                "ON CONFLICT (test_id) DO UPDATE SET "
                "test_file = excluded.test_file, opaque = excluded.opaque",
                (test_id, test_file, ",".join(sorted(opaque))))
            cur.executemany(
                "INSERT OR IGNORE INTO read_edge (test_id, path) VALUES (?, ?)",
                [(test_id, p) for p in sorted(reads)])

    if full_run:
        cur.execute("DELETE FROM read_global")
        cur.execute("DELETE FROM read_meta WHERE key = 'global_opaque'")
    cur.executemany("INSERT OR IGNORE INTO read_global (path) VALUES (?)",
                    [(p,) for p in sorted(global_reads)])
    spawned = sorted(global_opaque)
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


def global_refusal(conn: sqlite3.Connection) -> Optional[str]:
    """Why the rule must not act at all, or None."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT value FROM read_meta WHERE key = 'global_opaque'").fetchone()
    if row and row[0]:
        return f"a process was started outside any test ({row[0]})"
    if conn.execute("SELECT 1 FROM read_test LIMIT 1").fetchone() is None:
        return "no read evidence recorded yet (run smart-test --full once)"
    return None


def eligible_prose_files(
    conn: sqlite3.Connection,
    kinds: Mapping[str, str],
    sources: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split changed files into prose-only ones and the rest.

    ``kinds`` maps each changed repo-relative ``.py`` path to its
    :func:`classify` result and ``sources`` to its current text. Returns
    ``(prose, rejected)``: ``prose`` maps the eligible paths to DOCSTRINGS or
    COMMENTS, ``rejected`` maps the other prose-changed paths to the reason.
    Files classified as code appear in neither.
    """
    ensure_schema(conn)
    everyone = {r[0] for r in conn.execute("SELECT path FROM read_global")}
    prose: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for path, kind in sorted(kinds.items()):
        if kind == CODE:
            continue
        if path in everyone:
            rejected[path] = "read outside any test, so every test may depend on it"
        elif kind == DOCSTRINGS and introspection(sources.get(path, "")).own_doc:
            rejected[path] = "uses its own __doc__, and its docstrings changed"
        else:
            prose[path] = kind
    return prose, rejected


@dataclass(frozen=True)
class ProseNarrowing:
    """The outcome of applying the prose-only rule to a run set.

    ``kept`` is what to run. ``reasons`` gives, for each candidate test file
    that was kept, why it could not be dropped. ``added`` are readers of a
    prose-only file that the candidate set did not contain.
    """

    kept: frozenset[str]
    dropped: frozenset[str]
    added: frozenset[str]
    reasons: Mapping[str, str]


def _ids_by_file(ids: Iterable[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for test_id in ids:
        out.setdefault(test_id.split("::", 1)[0], set()).add(test_id)
    return out


def _introspecting_tests(
    conn: sqlite3.Connection, repo_root: Path, prose: Mapping[str, str],
    marker_files: Mapping[str, Introspection], sources: Mapping[str, str],
    reads: Mapping[str, set[str]], recorded: Iterable[str],
) -> tuple[set[str], set[str]]:
    """Tests that executed an introspecting block of a non-test module.

    Returns ``(test ids, unspeakable marker files)``. A marker file under
    ``packages/*/src`` is answered from the coverage index: the tests with a
    row for the block that holds the marker. Any other marker file (a script,
    a conftest) is outside coverage, and is returned for the caller to match
    against test files by location and by name.

    Docstring introspection counts only when a prose-only file's docstrings
    changed. A ``getsource``/``linecache`` block counts only when one of the
    tests executing it has a recorded read of a prose-only file: the cache is
    filled by a read, so if no such test ever read the file, none can be
    served it. That argument needs a read record for every one of those
    tests; without it the block counts.
    """
    docs_changed = DOCSTRINGS in prose.values()
    prose_paths = set(prose)
    known = set(recorded)
    observers: set[str] = set()
    unspeakable: set[str] = set()
    for rel, marks in sorted(marker_files.items()):
        channels = {"line": marks.line, "source": marks.source}
        if docs_changed:
            channels["doc"] = marks.doc
        if not any(channels.values()):
            continue
        parts = Path(rel).parts
        if not (parts[:1] == ("packages",) and "src" in parts):
            unspeakable.add(rel)
            continue
        owner = line_owner(blocks_for_source(rel, sources[rel]))
        rows = conn.execute(
            "SELECT test_id, name FROM test_block WHERE path = ?",
            (str(repo_root / rel),)).fetchall()
        for channel, lines in channels.items():
            names = {owner[n] for n in lines}
            ids = rebase_to_repo({t for t, name in rows if name in names}, repo_root)
            if (channel == "source" and ids <= known
                    and not any(reads.get(i, set()) & prose_paths for i in ids)):
                continue
            observers |= ids
    return observers, unspeakable


def narrow_for_prose(
    conn: sqlite3.Connection,
    repo_root: Path,
    run_set: Iterable[str],
    prose: Mapping[str, str],
    *,
    keep: Iterable[str] = (),
    tracked_sources: Mapping[str, str],
) -> ProseNarrowing:
    """Drop from ``run_set`` the tests a prose-only change cannot affect.

    ``prose`` is :func:`eligible_prose_files`' first result. ``keep`` is the
    selection for the NON-prose changed files, kept unconditionally.
    ``tracked_sources`` maps every tracked repo-relative ``.py`` file that may
    hold introspection to its text (the caller pre-filters nothing: this
    function finds the markers).
    """
    ensure_schema(conn)
    candidates = frozenset(f for f in run_set if f)
    keep_set = frozenset(f for f in keep if f)
    prose_paths = set(prose)

    reads: dict[str, set[str]] = {}
    for test_id, path in conn.execute("SELECT test_id, path FROM read_edge"):
        reads.setdefault(test_id, set()).add(path)
    records = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT test_id, test_file, opaque FROM read_test")}
    recorded = _ids_by_file(records)
    expected: dict[str, set[str]] = {}
    for test_file, test_id in conn.execute(
            "SELECT test_file, test_id FROM read_expected"):
        expected.setdefault(test_file, set()).add(test_id)
    digests = dict(conn.execute("SELECT test_file, digest FROM read_file"))
    measured = rebase_to_repo(
        {r[0] for r in conn.execute("SELECT DISTINCT test_id FROM test_block")}
        | {r[0] for r in conn.execute("SELECT test_id FROM unmeasured_test")},
        repo_root)

    marker_files = {
        rel: marks for rel, text in tracked_sources.items()
        if (marks := introspection(text)).any()
    }
    test_markers = {rel for rel in marker_files if _is_test_file(rel)}
    observers, unspeakable = _introspecting_tests(
        conn, repo_root, prose,
        {rel: m for rel, m in marker_files.items() if rel not in test_markers},
        tracked_sources, reads, records)

    def unspeakable_hit(test_file: str) -> Optional[str]:
        text = tracked_sources.get(test_file, "")
        for rel in sorted(unspeakable):
            marker = Path(rel)
            in_tests_dir = marker.name == "conftest.py" or "tests" in marker.parts
            if in_tests_dir and test_file.startswith(f"{marker.parent}/"):
                return rel
            if marker.stem in text or marker.stem.replace("-", "_") in text:
                return rel
        return None

    def reason_to_keep(test_file: str) -> Optional[str]:
        if test_file in test_markers:
            return "introspects docstrings, line numbers or source itself"
        ids = recorded.get(test_file)
        if not ids or test_file not in digests:
            return "no read record"
        if digests[test_file] != _file_digest(repo_root / test_file):
            return "read record predates a code change to the test"
        wanted = expected.get(test_file)
        if not wanted or not wanted <= ids:
            return "read record is incomplete"
        for test_id in sorted(ids):
            if records[test_id][1]:
                return f"starts a process ({records[test_id][1]})"
        hit = sorted(set().union(*(reads.get(i, set()) for i in ids)) & prose_paths)
        if hit:
            return f"reads {hit[0]}"
        if not wanted <= measured:
            return "coverage index does not know every test in it"
        if ids & observers:
            return "executes code that introspects docstrings, line numbers or source"
        marker = unspeakable_hit(test_file)
        if marker:
            return f"may execute introspecting code in {marker}"
        return None

    reasons: dict[str, str] = {}
    dropped: set[str] = set()
    for test_file in sorted(candidates - keep_set):
        why = reason_to_keep(test_file)
        if why is None:
            dropped.add(test_file)
        else:
            reasons[test_file] = why

    readers = {
        records[test_id][0] for test_id, paths in reads.items()
        if paths & prose_paths and test_id in records
    }
    added = frozenset(
        f for f in readers - candidates - keep_set
        if (repo_root / f).is_file())
    kept = (candidates - dropped) | keep_set | added
    return ProseNarrowing(kept=frozenset(kept), dropped=frozenset(dropped),
                          added=added, reasons=reasons)


def _is_test_file(rel: str) -> bool:
    return Path(rel).name.startswith(("test_", "BRANCHES_test_"))
