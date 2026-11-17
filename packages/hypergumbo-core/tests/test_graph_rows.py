# SPDX-License-Identifier: AGPL-3.0-or-later
"""The reverse file walk, and the analysis graph kept as rows to answer it.

``slice --files`` answers "which files depend on these changed files" by
walking a whole behavior map backwards. ``graph_rows`` keeps what that walk
needs (which file each node is in, which node points at which) as rows in the
selection index, so the answer no longer needs the 330 MB survey.

The rows are a second reader of the graph, so the claim these tests carry is
EQUALITY: the walk over stored rows names exactly the files ``slice --files``
names on the same nodes and edges. It is checked three ways: against an
independent breadth-first oracle written here, against the CLI handler on
hand-built graphs, and against both on generated graphs.
"""
from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path
from typing import Optional

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hypergumbo_core import graph_rows
from hypergumbo_core.cli import _handle_files_mode
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.selection_index import open_index

Node = tuple[str, Optional[str]]
Pair = tuple[str, str]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    graph_rows.ensure_schema(conn)
    return conn


def _store(conn: sqlite3.Connection, nodes: list[Node], edges: list[Pair], *,
           tree: str = "t1", build: str = "b1", head: str = "h1",
           dirty_files: tuple[str, ...] = (),
           at: str = "2026-11-17T00:00:00+00:00") -> graph_rows.StoredGraph:
    return graph_rows.store_graph(conn, nodes=nodes, edges=edges, tree=tree, build=build,
                                  head=head, dirty_files=dirty_files, at=at)


def _reverse(edges: list[Pair]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for src, dst in edges:
        out.setdefault(dst, []).append(src)
    return out


def _oracle(changed: list[str], nodes: list[Node], edges: list[Pair], max_hops: int) -> list[str]:
    """Written apart from the code under test, for exact repo-relative paths:
    a node's file is named when the node is a seed or at most ``max_hops - 1``
    edges behind one."""
    path_of = dict(nodes)
    seeds = [node for node, path in nodes if path in changed]
    if not seeds:
        return []
    distance = dict.fromkeys(seeds, 0)
    queue = deque(seeds)
    reverse = _reverse(edges)
    while queue:
        node = queue.popleft()
        for caller in reverse.get(node, ()):
            if caller not in distance:
                distance[caller] = distance[node] + 1
                queue.append(caller)
    files = {path for node, path in nodes if path in changed}
    files |= {path_of[node] for node, hops in distance.items()
              if hops < max_hops and path_of.get(node)}
    return sorted(files)


def _cli(tmp_path: Path, changed: list[str], nodes: list[Node], edges: list[Pair],
         max_hops: Optional[int]) -> list[str]:
    """What ``slice --files`` writes for the same graph: the production path."""
    symbols = [Symbol(id=node, name="n", kind="function", language="python", path=path or "",
                      span=Span(start_line=1, end_line=1, start_col=0, end_col=0))
               for node, path in nodes]
    cli_edges = [Edge(id=f"e{i}", src=src, dst=dst, edge_type="calls", line=1, confidence=0.9,
                      origin="test", origin_run_id="test")
                 for i, (src, dst) in enumerate(edges)]
    listing = tmp_path / "changed.txt"
    listing.write_text("".join(f"{name}\n" for name in changed))
    out = tmp_path / "out.txt"

    class Args:
        files = str(listing)
        output = str(out)

    Args.max_hops = max_hops  # type: ignore[attr-defined]
    assert _handle_files_mode(Args(), symbols, cli_edges, tmp_path) == 0  # type: ignore[arg-type]
    return out.read_text().split()


CHAIN_NODES: list[Node] = [(f"n{i}", f"f{i}.py") for i in range(13)]
#: n1 depends on n0, n2 on n1, ...: a change to f0.py reaches n<k> in k hops.
CHAIN_EDGES: list[Pair] = [(f"n{i + 1}", f"n{i}") for i in range(12)]


def test_the_walk_follows_edges_backwards_and_names_files() -> None:
    nodes: list[Node] = [("a", "src/a.py"), ("b", "src/b.py"), ("t", "tests/test_a.py"),
                         ("other", "src/other.py")]
    edges: list[Pair] = [("b", "a"), ("t", "b"), ("a", "other")]
    got = graph_rows.dependent_files(["src/a.py"], nodes, _reverse(edges))
    # "other" is what a.py depends ON: a change to a.py does not reach it.
    assert got == ["src/a.py", "src/b.py", "tests/test_a.py"]


def test_the_hop_limit_names_nodes_up_to_one_less_than_the_limit() -> None:
    """Ten levels are visited, the seeds being the first: a node nine edges
    behind a seed is named, a node ten edges behind is not."""
    got = graph_rows.dependent_files(["f0.py"], CHAIN_NODES, _reverse(CHAIN_EDGES))
    assert got == sorted(f"f{i}.py" for i in range(10))
    assert graph_rows.MAX_HOPS == 10
    three = graph_rows.dependent_files(["f0.py"], CHAIN_NODES, _reverse(CHAIN_EDGES), max_hops=3)
    assert three == ["f0.py", "f1.py", "f2.py"]


def test_an_endpoint_that_is_no_node_is_still_walked_through() -> None:
    nodes: list[Node] = [("a", "a.py"), ("t", "tests/test_a.py")]
    edges: list[Pair] = [("ghost", "a"), ("t", "ghost")]
    assert graph_rows.dependent_files(["a.py"], nodes, _reverse(edges)) == [
        "a.py", "tests/test_a.py"]


def test_a_cycle_ends_and_a_node_reached_twice_is_named_once() -> None:
    nodes: list[Node] = [("a", "a.py"), ("b", "b.py"), ("c", "c.py")]
    edges: list[Pair] = [("b", "a"), ("c", "a"), ("c", "b"), ("b", "c"), ("a", "c")]
    assert graph_rows.dependent_files(["a.py"], nodes, _reverse(edges)) == [
        "a.py", "b.py", "c.py"]


def test_a_changed_file_is_matched_exactly_or_by_path_suffix() -> None:
    nodes: list[Node] = [("a", "pkg/src/a.py"), ("t", "pkg/tests/test_a.py"), ("x", "pkg/src/xa.py")]
    edges: list[Pair] = [("t", "a")]
    reverse = _reverse(edges)
    exact = graph_rows.dependent_files(["pkg/src/a.py"], nodes, reverse)
    assert exact == ["pkg/src/a.py", "pkg/tests/test_a.py"]
    # A longer spelling of the same file and a shorter one both find it; a
    # file whose NAME merely ends the same way (xa.py) is not it.
    assert graph_rows.dependent_files(["/repo/pkg/src/a.py"], nodes, reverse) == exact
    assert graph_rows.dependent_files(["src/a.py"], nodes, reverse) == exact


def test_no_node_in_the_changed_files_names_nothing() -> None:
    nodes: list[Node] = [("a", "a.py"), ("nowhere", None), ("blank", "")]
    assert graph_rows.dependent_files(["README.md"], nodes, {"a": ["nowhere"]}) == []
    assert graph_rows.dependent_files([], nodes, {}) == []


def test_two_nodes_sharing_an_id_seed_both_files_and_the_last_one_names_a_visit() -> None:
    """``slice --files`` keys its lookup by id, so the LAST node of an id says
    which file a visit names, while every node seeds from its own file."""
    nodes: list[Node] = [("dup", "first.py"), ("dup", "second.py"), ("seed", "s.py")]
    edges: list[Pair] = [("dup", "seed")]
    assert graph_rows.dependent_files(["s.py"], nodes, _reverse(edges)) == ["s.py", "second.py"]
    assert graph_rows.dependent_files(["first.py"], nodes, {}) == ["first.py", "second.py"]


def test_stored_rows_answer_as_the_graph_did() -> None:
    conn = _conn()
    graph = _store(conn, CHAIN_NODES + [("nowhere", None)], CHAIN_EDGES + CHAIN_EDGES)
    assert (graph.nodes, graph.edges) == (14, 12)  # a repeated edge is stored once
    assert graph_rows.walk(conn, graph.id, ["f0.py"]) == sorted(f"f{i}.py" for i in range(10))
    assert graph_rows.walk(conn, graph.id, ["f0.py"], max_hops=2) == ["f0.py", "f1.py"]
    assert graph_rows.walk(conn, graph.id, ["absent.py"]) == []


def test_a_walk_of_a_graph_that_is_not_stored_says_so() -> None:
    """Absent is not empty: no rows must never read as "nothing depends on it"."""
    conn = _conn()
    with pytest.raises(graph_rows.GraphNotStored):
        graph_rows.walk(conn, 41, ["f0.py"])


def test_storing_the_same_tree_and_build_again_replaces_its_rows() -> None:
    conn = _conn()
    first = _store(conn, [("a", "a.py"), ("t", "tests/test_a.py")], [("t", "a")],
                   dirty_files=("a.py",))
    second = _store(conn, [("a", "a.py"), ("u", "tests/test_u.py")], [("u", "a")], head="h2")
    assert [g.id for g in graph_rows.stored_graphs(conn)] == [second.id]
    assert second.id > first.id, "an id is never handed out twice"
    assert graph_rows.walk(conn, second.id, ["a.py"]) == ["a.py", "tests/test_u.py"]
    with pytest.raises(graph_rows.GraphNotStored):
        graph_rows.walk(conn, first.id, ["a.py"])
    for table in ("graph_path", "graph_node", "graph_edge", "graph_dirty"):
        count = f"SELECT COUNT(*) FROM {table} WHERE graph = ?"  # noqa: S608 -- names from this tuple
        assert conn.execute(count, (first.id,)).fetchone()[0] == 0


def test_only_the_newest_graphs_of_each_build_and_the_newest_builds_are_kept() -> None:
    conn = _conn()
    for build in range(graph_rows.KEEP_BUILDS + 1):
        for tree in range(graph_rows.KEEP_PER_BUILD + 1):
            _store(conn, [("a", "a.py")], [], tree=f"t{build}-{tree}", build=f"b{build}",
                   head=f"h{build}-{tree}")
    kept = graph_rows.stored_graphs(conn)
    # Newest first; the oldest build is gone, and so is each build's oldest tree.
    assert [(g.build, g.tree) for g in kept] == [
        (f"b{build}", f"t{build}-{tree}")
        for build in range(graph_rows.KEEP_BUILDS, 0, -1)
        for tree in range(graph_rows.KEEP_PER_BUILD, 0, -1)]
    assert conn.execute("SELECT COUNT(DISTINCT graph) FROM graph_node").fetchone()[0] == len(kept)


def test_the_newest_graph_can_be_asked_for_one_build_and_before_another() -> None:
    conn = _conn()
    assert graph_rows.newest_graph(conn) is None
    old = _store(conn, [("a", "a.py")], [], tree="t1", build="b1")
    other = _store(conn, [("a", "a.py")], [], tree="t2", build="b2",
                   dirty_files=("z.py", "a.py", "z.py"))
    new = _store(conn, [("a", "a.py")], [], tree="t3", build="b1")
    assert graph_rows.newest_graph(conn) == new
    assert graph_rows.newest_graph(conn, build="b2") == other
    # The files that differed from the commit are kept, sorted, each once;
    # a graph of exactly its commit has none.
    assert other.dirty_files == ("a.py", "z.py")
    assert new.dirty_files == ()
    assert graph_rows.newest_graph(conn, build="b1", before=new.id) == old
    assert graph_rows.newest_graph(conn, build="b3") is None


def test_the_rows_live_beside_the_selection_index_tables(tmp_path: Path) -> None:
    """One file holds both: the index opens it, the graph tables are added,
    and opening it again leaves the stored graph readable."""
    path = tmp_path / "index.sqlite"
    conn = open_index(path)
    graph_rows.ensure_schema(conn)
    graph = _store(conn, CHAIN_NODES, CHAIN_EDGES)
    conn.commit()
    conn.close()
    again = open_index(path)
    graph_rows.ensure_schema(again)
    assert graph_rows.newest_graph(again) == graph
    assert graph_rows.walk(again, graph.id, ["f3.py"], max_hops=2) == ["f3.py", "f4.py"]
    again.close()


def test_a_loaded_survey_gives_nodes_and_edge_pairs() -> None:
    nodes, edges = graph_rows.survey_graph({
        "nodes": [{"id": "a", "path": "a.py", "name": "a"}, {"id": "t", "path": "tests/test_a.py"},
                  {"id": "nowhere"}, {"id": "blank", "path": ""}],
        "edges": [{"src": "t", "dst": "a", "type": "calls"}],
        "view": "behavior_map",
    })
    assert nodes == [("a", "a.py"), ("t", "tests/test_a.py"), ("nowhere", None), ("blank", None)]
    assert edges == [("t", "a")]
    conn = _conn()
    graph = _store(conn, nodes, edges)
    assert graph_rows.walk(conn, graph.id, ["a.py"]) == ["a.py", "tests/test_a.py"]


def test_the_cli_and_the_rows_agree_on_a_hand_built_graph(tmp_path: Path) -> None:
    conn = _conn()
    graph = _store(conn, CHAIN_NODES, CHAIN_EDGES)
    for hops in (None, 1, 4, 10, 12):
        want = _cli(tmp_path, ["f2.py"], CHAIN_NODES, CHAIN_EDGES, hops)
        got = graph_rows.walk(conn, graph.id, ["f2.py"], max_hops=hops or graph_rows.MAX_HOPS)
        assert got == want
    assert _cli(tmp_path, ["f2.py"], CHAIN_NODES, CHAIN_EDGES, None) == sorted(
        f"f{i}.py" for i in range(2, 12))  # the reach control: ten levels from f2


_IDS = st.sampled_from([f"n{i}" for i in range(9)])
_PATHS = st.sampled_from(["a.py", "b.py", "pkg/c.py", "tests/test_a.py", "tests/test_b.py"])


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    nodes=st.lists(st.tuples(_IDS, _PATHS), min_size=1, max_size=9, unique_by=lambda n: n[0]),
    edges=st.lists(st.tuples(_IDS, _IDS), max_size=24),
    changed=st.lists(_PATHS, min_size=1, max_size=3, unique=True),
    max_hops=st.integers(min_value=1, max_value=6),
)
def test_rows_cli_and_oracle_agree_on_generated_graphs(
    tmp_path_factory: pytest.TempPathFactory, nodes: list[Node], edges: list[Pair],
    changed: list[str], max_hops: int,
) -> None:
    want = _oracle(changed, nodes, edges, max_hops)
    conn = _conn()
    graph = _store(conn, nodes, edges)
    assert graph_rows.walk(conn, graph.id, changed, max_hops=max_hops) == want
    assert _cli(tmp_path_factory.mktemp("cli"), changed, nodes, edges, max_hops) == want
