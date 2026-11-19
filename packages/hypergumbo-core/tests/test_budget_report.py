# SPDX-License-Identifier: AGPL-3.0-or-later
"""The budget report: a ranking, the at-risk statements, the inferred coverage.

A small index is built for each test: which tests ran which statements of
which blocks. What is pinned, and what each would catch:

    a statement only tests through the change ran is at risk;   the at-risk
    one any other test ran is not                               rule itself
    an at-risk statement a test of the run ran is re-run        "not re-run"
    a pair with no statement record covers nothing              never smaller
    a file the caller does not count is not counted             the scope
    the ranking covers every changed statement, best value      the greedy
    per second first; an unpriced test is counted               cover
    the inferred figure is the measured one minus not re-run    the arithmetic
    a changed block with no record at its base is counted       no silent drop
    the manifest's files, baseline and deselected tests         the replay
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Optional

from hypergumbo_core import budget_report as br
from hypergumbo_core import selection_index as si
from hypergumbo_core.block_hash import blocks_for_source

SOURCE = textwrap.dedent('''
    def f(x):
        a = 1
        b = 2
        return a + b

    def g():
        return 3
''')


def _bitmap(ordinals: set[int]) -> bytes:
    return si._int_bitmap(sum(1 << o for o in ordinals))


def _index(tmp_path: Path, records: dict[str, dict[str, Optional[set[int]]]]):
    path = tmp_path / "m.py"
    path.write_text(SOURCE)
    conn = si.open_index(tmp_path / "index.sqlite")
    key = str(path)
    digests = {key: {b.name: b.digest for b in blocks_for_source(key, SOURCE)}}
    si.store_edges(
        conn, {t: [(key, b) for b in blocks] for t, blocks in records.items()},
        digests=digests,
        statements={t: {(key, b): _bitmap(ran) for b, ran in blocks.items() if ran is not None}
                    for t, blocks in records.items()})
    conn.commit()
    return conn, key


def test_at_risk_is_what_only_tests_through_the_change_ran(tmp_path: Path) -> None:
    conn, key = _index(tmp_path, {
        "t_through": {"f": {0, 1, 2}},
        "t_other": {"f": {0}},
        "t_through_g": {"g": {0}},
        "t_unrecorded": {"f": None, "g": None},
    })
    risk = br.at_risk(conn, through={"t_through", "t_through_g"}, ran=set())
    # f: statements 1 and 2 only t_through ran (t_other ran 0; the unrecorded
    # pair counts as running nothing); g: statement 0.
    assert risk.statements == 3 and risk.not_rerun == 3
    assert risk.by_file == {key: (3, 3)}
    rerun = br.at_risk(conn, through={"t_through", "t_through_g"}, ran={"t_through"})
    assert rerun.statements == 3 and rerun.not_rerun == 1
    none = br.at_risk(conn, through=set(), ran=set())
    assert none.statements == 0 and none.by_file == {}
    outside = br.at_risk(conn, through={"t_through"}, ran=set(), counts=lambda p: False)
    assert outside.statements == 0 and outside.by_file == {}


def test_a_statement_another_test_ran_is_not_at_risk(tmp_path: Path) -> None:
    conn, _ = _index(tmp_path, {"t_through": {"f": {0, 1}}, "t_other": {"f": {0, 1}}})
    assert br.at_risk(conn, through={"t_through"}, ran=set()).statements == 0


def test_the_ranking_buys_the_most_statements_per_second_first() -> None:
    s = [("m.py", "f", i) for i in range(6)]
    covers = {"slow_all": frozenset(s[1:5]), "fast_two": frozenset(s[1:3]),
              "fast_other_two": frozenset(s[3:5]), "useless": frozenset(),
              "unpriced": frozenset({s[5]})}
    seconds = {"slow_all": 10.0, "fast_two": 0.5, "fast_other_two": 0.5, "useless": 0.1}
    ranking = br.rank_cheapest_cover(covers, seconds)
    assert [step.test for step in ranking.steps] == ["fast_other_two", "fast_two", "unpriced"]
    assert ranking.statements == 5 and ranking.unpriced == 1
    last = ranking.steps[-1]
    # The unpriced test is priced at the median of the known (0.5).
    assert (last.cumulative_covered, last.cumulative_seconds) == (5, 1.5)
    assert br.rank_cheapest_cover({}, {}).steps == ()


def test_the_inferred_figure_is_the_measured_one_minus_not_rerun() -> None:
    assert br.inferred_coverage(1000, 0, 10) == 99.0
    assert br.inferred_coverage(1000, 5, 0) == 99.5
    assert br.inferred_coverage(0, 0, 0) is None


def test_timings_are_the_mean_of_the_runs(tmp_path: Path) -> None:
    store = tmp_path / "t.json"
    store.write_text(json.dumps({
        "packages/p/tests/test_a.py::test_x": {"runs": [{"seconds": 1.0}, {"seconds": 3.0}]},
        "tests/test_d.py::test_w": {"runs": []},
        "tests/test_e.py::test_v": None,
    }))
    assert br.load_timings(store) == {"packages/p/tests/test_a.py::test_x": 2.0}
    assert br.load_timings(tmp_path / "absent.json") == {}


def test_changed_statements_follow_the_records_at_the_base(tmp_path: Path) -> None:
    conn, key = _index(tmp_path, {"t_early": {"f": {0}}, "t_full": {"f": {0, 1, 2}},
                                  "t_g": {"g": {0}}})
    # An edited statement is a deletion (the return, 2) and an insertion
    # anchored to the statement before it (b = 2, 1); t_early ran neither.
    edited = br.changed_statements(conn, {key: (SOURCE, SOURCE.replace("a + b", "a - b"))})
    assert edited.covers == {"t_full": frozenset({(key, "f", 1), (key, "f", 2)})}
    assert edited.unrecorded == 0
    signature = br.changed_statements(conn, {key: (SOURCE, SOURCE.replace("def g():",
                                                                          "def g(y=1):"))})
    assert signature.covers == {"t_g": frozenset({(key, "g", 0)})}
    assert br.changed_statements(conn, {key: (SOURCE, "def f(:\n")}).covers == {}
    gone = br.changed_statements(conn, {key: (SOURCE, None)})
    assert set(gone.covers) == {"t_early", "t_full", "t_g"}
    prose = br.changed_statements(conn, {key: (SOURCE, SOURCE.replace("def g():",
                                                                      "def g():\n    '''G.'''"))})
    assert prose.covers == {}
    # The base is a version the index holds nothing of: counted, not ranked.
    older = SOURCE.replace("b = 2", "b = 4")
    unknown = br.changed_statements(conn, {key: (older, SOURCE.replace("b = 2", "b = 5"))})
    assert unknown.covers == {} and unknown.unrecorded == 1


def test_the_manifest_gives_the_baseline_the_files_and_the_deselected() -> None:
    text = "\n".join([
        "# Test selection manifest", "# Baseline: abc123", "# Selected tests: 2", "#",
        "# === DESELECTED_TESTS ===", "#- tests/test_a.py::test_slow",
        "# === CHANGED_SOURCE_FILES ===", "packages/p/src/p/m.py",
        "# === SELECTED_TESTS ===", "tests/test_a.py", "", "tests/test_b.py"])
    manifest = br.parse_manifest(text)
    assert manifest.baseline == "abc123"
    assert manifest.files == ("tests/test_a.py", "tests/test_b.py")
    assert manifest.deselected == frozenset({"tests/test_a.py::test_slow"})
    assert br.parse_manifest("# === SELECTED_TESTS ===\n").baseline is None


def test_tests_of_files_reads_the_index(tmp_path: Path) -> None:
    conn, _ = _index(tmp_path, {"tests/test_a.py::t1": {"f": {0}}, "tests/test_b.py::t2": {"g": {0}}})
    assert br.tests_of_files(conn, ["tests/test_a.py"]) == frozenset({"tests/test_a.py::t1"})


def test_the_report_says_each_number_and_what_it_lacks(tmp_path: Path) -> None:
    ranking = br.rank_cheapest_cover({f"t{i}": frozenset({("m.py", "f", i)}) for i in range(12)},
                                     {f"t{i}": 1.0 for i in range(12)})
    risk = br.AtRisk(statements=7, not_rerun=2, by_file={f"{tmp_path}/a.py": (5, 2), "b.py": (2, 0)})
    text = br.render(ranking, risk, {"statements": 1000, "missing": 0}, tmp_path,
                     selected=40, selected_seconds=12.5, ran=38, unrecorded=3)
    assert "selected: 40 indexed test(s), 12.5 test-seconds by the timing store; ran: 38" in text
    assert "ranking: 12 test(s) cover the 12 changed statement(s)" in text
    assert "... 2 more" in text
    assert "3 changed block(s) not ranked: the index holds no record of their base version" in text
    assert "at risk: 7 statement(s) only tests through the change run; 2 of them not re-run" in text
    assert "    2 not re-run of     5  a.py" in text and "b.py" not in text
    assert ("coverage: measured 100.00% (1000/1000) at the last full run; inferred after this "
            "change 99.80% (998/1000); the difference is the statements not re-run above") in text
    bare = br.render(br.rank_cheapest_cover({"t": frozenset({("m.py", "f", 1)})}, {}), br.AtRisk(),
                     None, tmp_path, selected=1, selected_seconds=0.0, ran=1)
    assert "priced at the median for want of a timing" in bare
    assert "coverage: no published full-run figure to infer from" in bare
    assert "not ranked" not in bare
    cut = br.render(ranking, br.AtRisk(), {"statements": 1000, "missing": 0}, tmp_path,
                    selected=12, selected_seconds=12.0, ran=12,
                    cut=br.AtRisk(statements=7, not_rerun=4,
                                  by_file={f"{tmp_path}/c.py": (7, 4)}))
    assert ("a cut to the ranking's 12 test(s) (12.00 s): 4 at-risk statement(s) not re-run; "
            "inferred 99.60% (996/1000)") in cut
    assert "    4 not re-run of     7  c.py" in cut
    assert "a cut" not in br.render(br.rank_cheapest_cover({}, {}), br.AtRisk(),
                                    {"statements": 10}, tmp_path, 0, 0.0, 0, cut=br.AtRisk())
    plain = br.render(br.rank_cheapest_cover({}, {}), br.AtRisk(), {"statements": 10}, tmp_path,
                      0, 0.0, 0)
    assert "ranking: no changed statement" in plain
    assert "inferred after this change 100.00% (10/10)" in plain and "difference" not in plain
    assert br.is_package_source("packages/p/src/p/m.py")
    assert not br.is_package_source("packages/p/tests/test_m.py")
    assert br.relative("/elsewhere/x.py", tmp_path) == "/elsewhere/x.py"
