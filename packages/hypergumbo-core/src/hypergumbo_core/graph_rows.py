# SPDX-License-Identifier: AGPL-3.0-or-later
"""The reverse file walk, and the analysis graph kept as rows to answer it.

WHAT PROBLEM THIS SOLVES. smart-test picks tests for a change with
``hypergumbo slice --files``: from the nodes in the changed files, walk every
edge backwards and name the files reached. The walk takes milliseconds. What
it walks takes five and a half minutes to compute (a whole-repository
analysis) and is cached as a survey of some 330 MB, keyed by the exact tree,
in a cache that evicts: of fourteen surveys registered over two days, one was
still on disk. A walk on an OLDER graph names the same test files as a walk on
the fresh one (741 pairs of commits and the comparisons smart-test logs beside
every fresh slice; an edited test file always runs), so the analysis does not
have to be on the path of every test run -- provided an older graph is still
there to walk.

WHAT IS KEPT. Only what the walk reads: for each node, an id number and the
file it is in; for each edge, the pair (source, target). No names, no stable
ids, no edge types, because the walk follows every edge type and answers in
files. A graph of 68,798 nodes and 274,523 distinct edges is about 4 MB of
rows where its survey is 349 MB, and it is read back in a fraction of a second.

WHERE. In the selection index's SQLite file, in tables of this module's own
(``graph``, ``graph_path``, ``graph_node``, ``graph_edge``, ``graph_dirty``, ``graph_format``):
:func:`ensure_schema` adds them to an open index. That file is one per
machine, outside every checkout, so a graph stored by a run in one checkout
can be walked from another, which a per-checkout survey cache cannot offer.
``selection_index`` copies tables it does not own as they stand when it
converts a file, so the two modules share the file without knowing each
other's layout.

ONE WALK, TWO READERS. :func:`dependent_files` is the walk itself, moved here
from the ``slice --files`` handler, which now calls it. :func:`walk` loads a
stored graph and calls the same function. So "the rows answer as the survey
did" rests on the rows holding the same nodes and pairs, not on two copies of
a traversal agreeing; the package's ``test_graph_rows.py`` checks it against
an oracle written apart from both, on hand-built and generated graphs.

THE WALK'S RULES, kept exactly as ``slice --files`` had them, because a stored
graph must not answer differently from the command it replaces:

* A changed file's nodes are found by exact path, else by path suffix at a
  directory boundary in either direction (``/repo/pkg/a.py`` and ``a.py`` both
  find ``pkg/a.py``).
* Every node of a changed file is a seed, and its file is named.
* Edges are followed from target to source, whatever their type. An endpoint
  that is no node (an unresolved reference) is walked through and names no
  file.
* :data:`MAX_HOPS` levels are visited, the seeds being the first: a node nine
  edges behind a seed is named, one ten edges behind is not.
* Ids are looked up in a mapping, so when two nodes share an id the LAST one
  says which file a visit names, while each seeds from its own file. Node
  order is stored for that reason.

WHAT IS KEPT AND FOR HOW LONG. A graph is identified by the tree it was made
from and the analyzer build that made it (the two directory names the survey
cache files it under); storing that pair again replaces it. The newest
:data:`KEEP_PER_BUILD` graphs of each build are kept, for the newest
:data:`KEEP_BUILDS` builds: two per build because a fresh graph is compared
with the one before it, several builds because a worktree and the main
checkout run different ones.

WHICH TREE A GRAPH IS OF. ``head`` is the commit that was checked out,
``git_tree`` the id of that commit's tree, and ``dirty_files`` the files that
differed from it (changed and not committed, or not tracked) when the analysis
ran. The tree id is kept beside the commit because a branch commit is
rewritten when its pull request is merged by rebase: the commit id then names
nothing reachable, while the tree it pointed at is the tree of the merged
commit and can still be compared with. Together they name the tree: a caller that
walks the graph for a LATER tree has to seed every file that differs between
the two, because an edge that exists now and not in the graph starts in such a
file, and a file added since is not in the graph at all. A flag saying only
"the tree was dirty" would make the graph useless for that, so the list is
kept.

THE ROWS ARE A CACHE WITH A FORMAT. :data:`FORMAT` is stored beside the
tables. :func:`ensure_schema` drops and recreates them when the stored number
is another one (or none: the first layout had no number), because a graph
that is lost costs one analysis and a graph read with the wrong layout costs
a wrong selection.

ABSENT IS NOT EMPTY. Walking a graph that is not stored raises
:class:`GraphNotStored`. An empty answer means "no node of the stored graph is
in these files", which for a file ADDED since the graph was made is a fact
about the graph's age and not about the file; the caller decides what an
added file needs.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Mapping, Optional, Sequence, TypeVar

from .paths import normalize_path, path_ends_with

K = TypeVar("K", bound=Hashable)

#: Levels the walk visits, the seeds being the first.
MAX_HOPS = 10
#: Graphs kept per analyzer build: the newest and the one it is compared with.
KEEP_PER_BUILD = 2
#: Analyzer builds whose graphs are kept.
KEEP_BUILDS = 4

#: The layout of this module's tables. 1 had no ``git_tree`` and no number.
FORMAT = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph (
    id    INTEGER PRIMARY KEY,
    tree  TEXT NOT NULL,
    build TEXT NOT NULL,
    head  TEXT NOT NULL,
    git_tree TEXT,
    at    TEXT NOT NULL,
    nodes INTEGER NOT NULL,
    edges INTEGER NOT NULL,
    UNIQUE (tree, build)
);
CREATE TABLE IF NOT EXISTS graph_path (
    graph INTEGER NOT NULL,
    num   INTEGER NOT NULL,
    path  TEXT NOT NULL,
    PRIMARY KEY (graph, num)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS graph_node (
    graph INTEGER NOT NULL,
    seq   INTEGER NOT NULL,
    node  INTEGER NOT NULL,
    path  INTEGER,
    PRIMARY KEY (graph, seq)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS graph_dirty (
    graph INTEGER NOT NULL,
    path  TEXT NOT NULL,
    PRIMARY KEY (graph, path)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS graph_edge (
    graph INTEGER NOT NULL,
    dst   INTEGER NOT NULL,
    src   INTEGER NOT NULL,
    PRIMARY KEY (graph, dst, src)
) WITHOUT ROWID;
"""

#: Everything a graph owns, its own row last.
_DROP = (
    "DELETE FROM graph_path WHERE graph = ?",
    "DELETE FROM graph_node WHERE graph = ?",
    "DELETE FROM graph_edge WHERE graph = ?",
    "DELETE FROM graph_dirty WHERE graph = ?",
    "DELETE FROM graph WHERE id = ?",
)


_DROP_TABLES = (
    "DROP TABLE IF EXISTS graph_path",
    "DROP TABLE IF EXISTS graph_node",
    "DROP TABLE IF EXISTS graph_edge",
    "DROP TABLE IF EXISTS graph_dirty",
    "DROP TABLE IF EXISTS graph",
)


class GraphNotStored(LookupError):
    """No graph with this id is in the index: there is nothing to walk."""


@dataclass(frozen=True)
class StoredGraph:
    """One stored graph: where it came from and how large it is.

    ``tree`` and ``build`` are the survey cache's names for the tree state and
    the analyzer identity. ``head`` is the commit checked out when the graph
    was made, ``git_tree`` the id of that commit's tree (None when it was not
    recorded) and ``dirty_files`` the files that differed from the commit
    then, sorted; none means the graph is of exactly that commit.
    """

    id: int
    tree: str
    build: str
    head: str
    git_tree: Optional[str]
    dirty_files: tuple[str, ...]
    at: str
    nodes: int
    edges: int


def dependent_files(
    changed_files: Sequence[str],
    nodes: Iterable[tuple[K, Optional[str]]],
    reverse: Mapping[K, Iterable[K]],
    max_hops: int = MAX_HOPS,
) -> list[str]:
    """The files of the nodes that reach a node of ``changed_files``, sorted.

    ``nodes`` is every ``(id, path)`` of the graph in the graph's own order,
    and ``reverse`` maps a node id to the ids with an edge TO it. The rules
    are in the module docstring; the paths returned are spelled as ``nodes``
    spells them.
    """
    by_path: dict[str, list[tuple[K, str]]] = {}
    path_of: dict[K, Optional[str]] = {}
    for node, path in nodes:
        path_of[node] = path
        if path:
            by_path.setdefault(normalize_path(path), []).append((node, path))

    seeds: list[tuple[K, str]] = []
    for changed in changed_files:
        wanted = normalize_path(changed)
        if wanted in by_path:
            seeds.extend(by_path[wanted])
            continue
        for file_path, members in by_path.items():
            if path_ends_with(file_path, wanted) or path_ends_with(wanted, file_path):
                seeds.extend(members)
    if not seeds:
        return []

    files = {path for _node, path in seeds}
    visited: set[K] = set()
    level = {node for node, _path in seeds}
    hops = 0
    while level and hops < max_hops:
        following: set[K] = set()
        for node in level:
            if node in visited:
                # Reached again through a node of this same level.
                continue
            visited.add(node)
            path = path_of.get(node)
            if path:
                files.add(path)
            for caller in reverse.get(node, ()):
                if caller not in visited:
                    following.add(caller)
        level = following
        hops += 1
    return sorted(files)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add this module's tables to an open index; a no-op when they exist in
    this :data:`FORMAT`. Tables of another format are dropped first, with the
    graphs in them (module docstring)."""
    conn.execute("CREATE TABLE IF NOT EXISTS graph_format (format INTEGER NOT NULL)")
    stamped = conn.execute("SELECT format FROM graph_format").fetchone()
    if stamped is None or int(stamped[0]) != FORMAT:
        for statement in _DROP_TABLES:
            conn.execute(statement)
        conn.execute("DELETE FROM graph_format")
        conn.execute("INSERT INTO graph_format (format) VALUES (?)", (FORMAT,))
    conn.executescript(_SCHEMA)


def stored_graphs(conn: sqlite3.Connection) -> list[StoredGraph]:
    """Every stored graph, newest first."""
    dirty: dict[int, list[str]] = {}
    for graph_id, path in conn.execute("SELECT graph, path FROM graph_dirty ORDER BY path"):
        dirty.setdefault(int(graph_id), []).append(str(path))
    rows: list[Sequence[Any]] = conn.execute(
        "SELECT id, tree, build, head, git_tree, at, nodes, edges "
        "FROM graph ORDER BY id DESC").fetchall()
    return [StoredGraph(id=int(ident), tree=str(tree), build=str(build), head=str(head),
                        git_tree=None if git_tree is None else str(git_tree),
                        dirty_files=tuple(dirty.get(int(ident), ())), at=str(at),
                        nodes=int(nodes), edges=int(edges))
            for ident, tree, build, head, git_tree, at, nodes, edges in rows]


def newest_graph(
    conn: sqlite3.Connection, *, build: Optional[str] = None, before: Optional[int] = None,
) -> Optional[StoredGraph]:
    """The newest stored graph; of one ``build`` when given, and older than
    graph ``before`` when given. None when there is none."""
    for graph in stored_graphs(conn):
        if build is not None and graph.build != build:
            continue
        if before is not None and graph.id >= before:
            continue
        return graph
    return None


def _drop(conn: sqlite3.Connection, graph_id: int) -> None:
    for statement in _DROP:
        conn.execute(statement, (graph_id,))


def _prune(conn: sqlite3.Connection) -> None:
    """Keep the newest graphs of the newest builds (module docstring)."""
    builds: list[str] = []
    kept: dict[str, int] = {}
    for graph in stored_graphs(conn):
        if graph.build not in kept:
            builds.append(graph.build)
            kept[graph.build] = 0
        kept[graph.build] += 1
        if builds.index(graph.build) >= KEEP_BUILDS or kept[graph.build] > KEEP_PER_BUILD:
            _drop(conn, graph.id)


def store_graph(
    conn: sqlite3.Connection,
    *,
    nodes: Iterable[tuple[str, Optional[str]]],
    edges: Iterable[tuple[str, str]],
    tree: str,
    build: str,
    head: str,
    git_tree: Optional[str],
    dirty_files: Iterable[str],
    at: str,
) -> StoredGraph:
    """Store one graph as rows, replacing the graph of the same ``tree`` and
    ``build``, and drop the graphs that are no longer kept.

    ``nodes`` is every ``(id, path)`` in the graph's order; ``edges`` every
    ``(source id, target id)``. A repeated pair is stored once.
    ``dirty_files`` is every file that differed from ``head`` when the graph
    was made (module docstring). The caller commits; until it does, this
    connection holds the file's write lock.
    """
    dirty = tuple(sorted(set(dirty_files)))
    numbers: dict[str, int] = {}
    paths: dict[str, int] = {}
    node_rows: list[tuple[int, int, Optional[int]]] = []
    for seq, (node, path) in enumerate(nodes):
        number = numbers.setdefault(node, len(numbers))
        node_rows.append((seq, number, paths.setdefault(path, len(paths)) if path else None))
    pairs = {(numbers.setdefault(dst, len(numbers)), numbers.setdefault(src, len(numbers)))
             for src, dst in edges}

    # The index is one file for every checkout on the machine, so another
    # process may be storing a graph right now. The write lock is taken BEFORE
    # the highest id is read: read first, and two writers choose the same id
    # and the second one's graph is lost to a constraint error.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    # Numbered past every id in use BEFORE the old graph of this tree goes, so
    # an id is never handed out twice and "newer" always means "higher".
    graph_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM graph").fetchone()[0])
    old = conn.execute("SELECT id FROM graph WHERE tree = ? AND build = ?", (tree, build)).fetchone()
    if old is not None:
        _drop(conn, int(old[0]))
    conn.execute(
        "INSERT INTO graph (id, tree, build, head, git_tree, at, nodes, edges) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (graph_id, tree, build, head, git_tree, at, len(node_rows), len(pairs)))
    conn.executemany("INSERT INTO graph_dirty (graph, path) VALUES (?, ?)",
                     ((graph_id, path) for path in dirty))
    conn.executemany("INSERT INTO graph_path (graph, num, path) VALUES (?, ?, ?)",
                     ((graph_id, number, path) for path, number in paths.items()))
    conn.executemany("INSERT INTO graph_node (graph, seq, node, path) VALUES (?, ?, ?, ?)",
                     ((graph_id, seq, number, path) for seq, number, path in node_rows))
    conn.executemany("INSERT INTO graph_edge (graph, dst, src) VALUES (?, ?, ?)",
                     ((graph_id, dst, src) for dst, src in sorted(pairs)))
    _prune(conn)
    return StoredGraph(id=graph_id, tree=tree, build=build, head=head, git_tree=git_tree,
                       dirty_files=dirty,
                       at=at, nodes=len(node_rows), edges=len(pairs))


@dataclass(frozen=True)
class LoadedGraph:
    """A stored graph read back, in the shape :func:`dependent_files` takes."""

    nodes: tuple[tuple[int, Optional[str]], ...]
    reverse: Mapping[int, Sequence[int]]


def load_graph(conn: sqlite3.Connection, graph_id: int) -> LoadedGraph:
    """Read stored graph ``graph_id``, for a caller that walks it more than once.

    Raises :class:`GraphNotStored` when no graph has this id: an answer of
    "no file depends on these" must come from a graph, never from its absence.
    """
    if conn.execute("SELECT 1 FROM graph WHERE id = ?", (graph_id,)).fetchone() is None:
        raise GraphNotStored(f"graph {graph_id} is not in the index")
    path_names: dict[int, str] = dict(conn.execute(
        "SELECT num, path FROM graph_path WHERE graph = ?", (graph_id,)))
    nodes = tuple(
        (int(node), None if path is None else path_names[path])
        for node, path in conn.execute(
            "SELECT node, path FROM graph_node WHERE graph = ? ORDER BY seq", (graph_id,)))
    reverse: dict[int, list[int]] = {}
    for dst, src in conn.execute(
            "SELECT dst, src FROM graph_edge WHERE graph = ?", (graph_id,)):
        reverse.setdefault(dst, []).append(src)
    return LoadedGraph(nodes=nodes, reverse=reverse)


def walk(
    conn: sqlite3.Connection, graph_id: int, changed_files: Sequence[str],
    max_hops: int = MAX_HOPS,
) -> list[str]:
    """:func:`dependent_files` on a stored graph (:func:`load_graph` says what
    happens when the graph is not stored)."""
    loaded = load_graph(conn, graph_id)
    return dependent_files(changed_files, loaded.nodes, loaded.reverse, max_hops)


def survey_graph(
    survey: Mapping[str, Any],
) -> tuple[list[tuple[str, Optional[str]]], list[tuple[str, str]]]:
    """The nodes and edge pairs of a loaded survey (a behavior map), as
    :func:`store_graph` takes them. A node with no path gets ``None``.

    The caller reads the file: this module does no I/O of its own beyond the
    connection it is handed.
    """
    nodes = [(str(node["id"]), node.get("path") or None) for node in survey["nodes"]]
    edges = [(str(edge["src"]), str(edge["dst"])) for edge in survey["edges"]]
    return nodes, edges
