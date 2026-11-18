# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selecting from a graph of an OLDER tree: the seeds, the surplus, the added files.

A stored graph answers for a later tree only when every file that differs
between the two is seeded, and a file added since has no node to seed. These
tests pin what ``graph_select`` decides about both, and that each refusal is a
verdict of its own and never an empty answer.
"""
from __future__ import annotations

import sqlite3

import pytest

from hypergumbo_core import graph_rows, graph_select
from hypergumbo_core.graph_select import AddedFiles, Verdict

NO_ADDED = AddedFiles((), (), ())

#: The graph of the OLDER tree. a <- b <- test_b; a <- test_a; c <- test_c.
NODES = [("a", "pkg/src/a.py"), ("b", "pkg/src/b.py"), ("c", "pkg/src/c.py"),
         ("ta", "pkg/tests/test_a.py"), ("tb", "pkg/tests/test_b.py"),
         ("tc", "pkg/tests/test_c.py"), ("h", "pkg/tests/helper.py")]
EDGES = [("b", "a"), ("ta", "a"), ("tb", "b"), ("tc", "c"), ("ta", "h")]


@pytest.fixture
def stored() -> tuple[sqlite3.Connection, int]:
    conn = sqlite3.connect(":memory:")
    graph_rows.ensure_schema(conn)
    graph = graph_rows.store_graph(
        conn, nodes=NODES, edges=EDGES, tree="t", build="b", head="h", git_tree="gt",
        dirty_files=(), at="2026-11-17T00:00:00+00:00")
    return conn, graph.id


@pytest.mark.parametrize(("path", "is_test"), [
    ("packages/p/tests/test_a.py", True),
    ("tests/test_top.py", True),
    ("packages/p/tests/BRANCHES_test_a.py", True),
    ("packages/p/tests/helper.py", False),
    ("packages/p/tests/conftest.py", False),
    ("packages/p/tests/fixtures/test_x.py", False),   # not directly under tests/
    ("packages/p/src/p/test_utils.py", False),
    ("packages/p/contests/test_a.py", False),          # a directory that ends in "tests"
])
def test_a_test_file_is_test_star_py_directly_under_a_tests_directory(
        path: str, is_test: bool) -> None:
    assert graph_select.is_test_file(path) is is_test


def test_a_mention_is_the_path_or_the_stem_as_a_whole_word() -> None:
    added = "pkg/src/pkg/widget_store.py"
    assert graph_select.mentions(added, "from pkg.widget_store import Store")
    assert graph_select.mentions(added, 'open("pkg/src/pkg/widget_store.py")')
    assert not graph_select.mentions(added, "from pkg.widget_stores import Store")
    assert not graph_select.mentions(added, "my_widget_store_cache = 1"), "a longer word"
    assert not graph_select.mentions(added, "")


def test_a_short_stem_is_matched_by_the_whole_file_name() -> None:
    """``io`` as a word is in every other line; ``io.py`` names the file."""
    assert not graph_select.mentions("pkg/src/pkg/io.py", "import io\nx = io.StringIO()")
    assert graph_select.mentions("pkg/src/pkg/io.py", "see io.py for the readers")
    assert graph_select.mentions("pkg/data/abc.json", 'load("abc.json")')
    assert not graph_select.mentions("pkg/data/abc.json", "abc = 1")
    # Four characters is long enough to stand as a word.
    assert graph_select.mentions("pkg/src/pkg/keys.py", "from pkg import keys")


def test_each_added_file_runs_itself_is_mentioned_or_needs_an_analysis() -> None:
    added = ["pkg/tests/test_new.py", "pkg/src/widget_store.py", "pkg/tests/fixtures/tree/x.txt",
             "pkg/tests/conftest.py", "pkg/src/widget_store.py"]
    lines = {
        "pkg/src/a.py": "from pkg.widget_store import Store\n# see conftest for the fixture",
        "pkg/src/widget_store.py": "class Store: ...  # widget_store x.txt",
        "pkg/tests/fixtures/tree/x.txt": "x.txt",
    }
    judged = graph_select.judge_added(added, lines)
    assert judged.run_themselves == ("pkg/tests/test_new.py",)
    # x.txt is named in its own text, which does not count, and in the text of
    # another ADDED file, which does: that file is a seed like any other.
    assert judged.mentioned == (("pkg/src/widget_store.py", "pkg/src/a.py"),
                                ("pkg/tests/fixtures/tree/x.txt", "pkg/src/widget_store.py"))
    # conftest.py is named in a.py's added line and still needs an analysis.
    assert judged.need_analysis == ((
        "pkg/tests/conftest.py",
        "an added conftest.py applies to every test under its directory and nothing names it"),)


def test_a_file_only_its_own_text_names_is_not_mentioned() -> None:
    judged = graph_select.judge_added(
        ["pkg/tests/fixtures/tree/lonely_fixture.txt"],
        {"pkg/tests/fixtures/tree/lonely_fixture.txt": "lonely_fixture", "pkg/src/a.py": "A = 2"})
    assert judged.need_analysis == (
        ("pkg/tests/fixtures/tree/lonely_fixture.txt", "no other changed file mentions it"),)
    assert judged.mentioned == () and judged.run_themselves == ()
    assert graph_select.judge_added([], {"pkg/src/a.py": "A = 2"}) == NO_ADDED


def test_the_seeds_are_the_change_and_everything_else_that_differs(
        stored: tuple[sqlite3.Connection, int]) -> None:
    conn, graph = stored
    # The change edits b.py. Since the graph was made, c.py changed too.
    chosen = graph_select.select(conn, graph, changed=["pkg/src/b.py"],
                                 differing=["pkg/src/c.py", "pkg/src/b.py"], added=NO_ADDED)
    assert chosen.seeds == ("pkg/src/b.py", "pkg/src/c.py")
    assert chosen.extra_seeds == ("pkg/src/c.py",)
    assert chosen.files == ("pkg/src/b.py", "pkg/src/c.py", "pkg/tests/test_b.py",
                            "pkg/tests/test_c.py")
    # test_c is reached only through the file that is not this change's.
    assert chosen.extra_test_files == ("pkg/tests/test_c.py",)


def test_a_graph_of_the_same_tree_has_no_surplus(stored: tuple[sqlite3.Connection, int]) -> None:
    conn, graph = stored
    chosen = graph_select.select(conn, graph, changed=["pkg/src/a.py", "pkg/src/a.py"],
                                 differing=[], added=NO_ADDED)
    assert chosen.seeds == ("pkg/src/a.py",)
    assert chosen.extra_seeds == () and chosen.extra_test_files == ()
    # helper.py is what test_a depends ON: a change to a.py does not reach it.
    assert chosen.files == ("pkg/src/a.py", "pkg/src/b.py", "pkg/tests/test_a.py",
                            "pkg/tests/test_b.py")


def test_a_test_the_change_reaches_or_edits_is_not_surplus(
        stored: tuple[sqlite3.Connection, int]) -> None:
    conn, graph = stored
    # b.py (the change) reaches test_b; so does a.py (not the change's), which
    # also reaches test_a. The change edits test_c itself; c.py differs too.
    chosen = graph_select.select(
        conn, graph, changed=["pkg/src/b.py", "pkg/tests/test_c.py"],
        differing=["pkg/src/a.py", "pkg/src/c.py"], added=NO_ADDED)
    assert chosen.extra_test_files == ("pkg/tests/test_a.py",)


def test_an_added_test_file_is_in_the_answer_though_the_graph_has_no_node_for_it(
        stored: tuple[sqlite3.Connection, int]) -> None:
    conn, graph = stored
    added = graph_select.judge_added(["pkg/tests/test_new.py"], {})
    chosen = graph_select.select(conn, graph, changed=["pkg/tests/test_new.py"],
                                 differing=["pkg/tests/test_new.py"], added=added)
    assert chosen.files == ("pkg/tests/test_new.py",)
    assert chosen.added is added


def test_a_graph_that_is_not_stored_is_an_error_not_an_empty_selection() -> None:
    conn = sqlite3.connect(":memory:")
    graph_rows.ensure_schema(conn)
    with pytest.raises(graph_rows.GraphNotStored):
        graph_select.select(conn, 5, changed=["pkg/src/a.py"], differing=[], added=NO_ADDED)


def test_the_verdict_uses_refuses_for_an_added_file_or_refuses_for_the_surplus(
        stored: tuple[sqlite3.Connection, int]) -> None:
    conn, graph = stored
    plain = graph_select.select(conn, graph, changed=["pkg/src/b.py"],
                                differing=["pkg/src/c.py", "pkg/src/a.py"], added=NO_ADDED)
    assert len(plain.extra_test_files) == 2  # test_a and test_c
    assert graph_select.verdict(plain, max_extra_tests=2) == (Verdict.USE, None)
    refused, why = graph_select.verdict(plain, max_extra_tests=1)
    assert refused is Verdict.TOO_FAR_BEHIND
    assert why == ("2 file(s) differ from the stored graph's tree besides this change's own, "
                   "and they reach 2 test file(s) this change does not (limit 1)")

    lonely = graph_select.judge_added(
        ["pkg/data/one_fixture.txt", "pkg/data/two_fixture.txt"], {})
    with_added = graph_select.select(conn, graph, changed=["pkg/src/b.py"], differing=[],
                                     added=lonely)
    refused, why = graph_select.verdict(with_added, max_extra_tests=0)
    assert refused is Verdict.ADDED_FILE
    assert why == ("pkg/data/one_fixture.txt was added since the stored graph and no other "
                   "changed file mentions it (and 1 more such file(s))")
    # The added file is the reason given even when the surplus is over the limit too.
    both = graph_select.select(conn, graph, changed=["pkg/src/b.py"],
                               differing=["pkg/src/c.py"], added=graph_select.judge_added(
                                   ["pkg/data/one_fixture.txt"], {}))
    refused, why = graph_select.verdict(both, max_extra_tests=0)
    assert refused is Verdict.ADDED_FILE
    assert why == ("pkg/data/one_fixture.txt was added since the stored graph and no other "
                   "changed file mentions it")


def test_added_tracker_data_needs_no_analysis() -> None:
    """A tracker op file is written by every ``tracker add``, nothing in a
    change mentions it, and no analysis gives it a node (the stored graph of
    2026-11-18 held 0 paths under ``.agent/tracker*``): a fresh analysis
    would answer nothing for it. Once the stored graph was refused for one,
    and the run spent 331 s analysing the repository (WI-pufug)."""
    ops = [".agent/tracker-workspace/.ops/.WI-x.ops", ".agent/tracker/.ops/.INV-y.ops"]
    judged = graph_select.judge_added(ops, {"pkg/src/a.py": "A = 2"})
    assert judged.need_analysis == () and judged.run_themselves == ()
    assert judged.mentioned == ()
    assert judged.tracker_data == tuple(sorted(ops))
    # Elsewhere under .agent the rule is unchanged.
    other = graph_select.judge_added([".agent/hooks/_shared/new_hook.py"], {"pkg/src/a.py": "A = 2"})
    assert other.need_analysis == ((".agent/hooks/_shared/new_hook.py",
                                    "no other changed file mentions it"),)
