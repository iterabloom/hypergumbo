# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/score-readability``: the per-function reader-load panel.

Every expected number below was computed by hand from the fixture against the
rules the script's module docstring states; the test pins the rules, not the
script's own output.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

FIXTURE = '''"""Fixture."""
import os


def plain(a, b=2, *args, **kw):
    """Add."""
    return a + b


class Widget:
    @staticmethod
    def deco(x):
        if x and x > 1 or x < -1:
            return x
        elif x == 0:
            return 0
        else:
            return -x

    def loop(self, items):
        total = 0
        for i in items:
            while i:
                if i % 2:
                    total += i
                i -= 1
        try:
            os.getcwd()
        except OSError:
            pass
        return total


def rec(n):
    return 1 if n <= 1 else n * rec(n - 1)
'''

GLOBALS_FIXTURE = '''import re

PATTERN = re.compile("x")
LIMIT = 3
UNUSED = 0
TABLE = {
    "a": 1,
    "b": 2,
}


def helper(v):
    return v


def uses_globals(s):
    """Read two module names and call a helper.

    Two more docstring lines, then a comment.
    """
    # a comment line
    found = PATTERN.match(s)

    LIMIT = 1
    return helper(found) if found else LIMIT


def no_globals(s):
    return s


def uses_table(k):
    return TABLE[k]


class Box:
    size = 3


def uses_box():
    return Box.size
'''

RECURSION_FIXTURE = '''def rules():
    return (*first.rules(), *second.rules())


class Node:
    def walk(self, other):
        if other:
            return self.walk(other.child)
        return other.walk(None)

    @classmethod
    def build(cls, n):
        return cls.build(n - 1) if n else helper.build(n)
'''

LITERAL_FIXTURE = '''def message(n):
    text = (
        "first line "
        "second line "
        f"{n} third"
    )
    table = {
        "a": 1,
        "b": 2,
    }
    return text, table
'''

CAPTURE_FIXTURE = '''def outer(regex, flags):
    def inner():
        if isinstance(regex, str):
            return (regex, flags)
        return regex
    return inner
'''


@pytest.fixture(scope="module")
def mod():
    loader = importlib.machinery.SourceFileLoader(
        "score_readability", str(SCRIPTS / "score-readability"),
    )
    spec = importlib.util.spec_from_loader("score_readability", loader)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve ``from __future__`` annotations through sys.modules.
    sys.modules["score_readability"] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("score") / "sample.py"
    path.write_text(FIXTURE)
    return path


@pytest.fixture(scope="module")
def panel(mod, sample):
    report = mod.score_file(sample, mod.Thresholds())
    return {s.name: s for s in report.functions}, report


def test_every_function_is_scored_once(panel) -> None:
    scores, report = panel
    assert sorted(scores) == ["Widget.deco", "Widget.loop", "plain", "rec"]
    assert report.lines == FIXTURE.count("\n")
    assert len(report.functions) == 4


def test_mccabe_comes_from_the_analyzer(panel) -> None:
    scores, _ = panel
    # 1 + if + elif + and + or
    assert scores["Widget.deco"].mccabe == 5
    # 1 + for + while + if + except
    assert scores["Widget.loop"].mccabe == 5
    assert scores["rec"].mccabe == 2  # ternary
    assert scores["plain"].mccabe == 1


def test_cognitive_complexity_follows_the_sonar_rules(panel) -> None:
    scores, _ = panel
    # if +1; ``x and x > 1 or x < -1`` = an or-run +1 and an and-run +1;
    # elif +1; else +1.
    assert scores["Widget.deco"].cognitive == 5
    # for +1 (nesting 0); while +1+1; if +1+2; except +1 -> 7
    assert scores["Widget.loop"].cognitive == 7
    # ternary +1; direct recursion +1
    assert scores["rec"].cognitive == 2
    assert scores["plain"].cognitive == 0


def test_nesting_counts_statement_depth(panel) -> None:
    scores, _ = panel
    assert scores["Widget.loop"].nesting == 3  # for > while > if
    assert scores["Widget.deco"].nesting == 1  # one if chain
    assert scores["rec"].nesting == 0
    assert scores["plain"].nesting == 0


def test_dataflow_counts_distinct_def_use_triples(panel) -> None:
    scores, _ = panel
    # ``a`` and ``b`` are entry definitions used once each at the return.
    plain = scores["plain"]
    assert (plain.dataflow, plain.vars) == (2, 2)
    # x (entry, line 12) is read at lines 13, 14, 15 and 18: the three reads
    # on line 13 are one triple.
    assert (scores["Widget.deco"].dataflow, scores["Widget.deco"].vars) == (4, 1)
    # total: 21->25, 21->31, 25->25, 25->31; i: 22 and 26 each -> 23, 24, 25, 26;
    # items: 20->22.
    loop = scores["Widget.loop"]
    assert (loop.dataflow, loop.vars) == (13, 3)
    # n read three times on one line is one triple.
    assert (scores["rec"].dataflow, scores["rec"].vars) == (1, 1)
    for s in scores.values():
        assert s.dataflow_uncovered == 0 and not s.dataflow_suspect


def test_reach_counts_code_lines_between_a_body_definition_and_its_use(panel) -> None:
    scores, _ = panel
    # Parameters are the only definitions in plain, deco and rec: reach 0.
    assert scores["plain"].reach == 0
    assert scores["Widget.deco"].reach == 0
    assert scores["rec"].reach == 0
    # total defined at 21, returned at 31: nine code lines lie between.
    assert scores["Widget.loop"].reach == 9


def test_loc_excludes_the_docstring_and_reports_it(panel) -> None:
    scores, _ = panel
    assert (scores["plain"].loc, scores["plain"].doc) == (2, 1)  # def, return
    assert (scores["Widget.loop"].loc, scores["Widget.loop"].doc) == (12, 0)
    assert scores["plain"].params == 4  # a, b, *args, **kw
    assert scores["Widget.loop"].calls == 1  # os.getcwd()
    assert scores["rec"].calls == 1


def test_globals_docstring_and_reach_on_a_module_with_bindings(mod, tmp_path) -> None:
    path = tmp_path / "globals.py"
    path.write_text(GLOBALS_FIXTURE)
    report = mod.score_file(path, mod.Thresholds())
    by_name = {s.name: s for s in report.functions}
    uses = by_name["uses_globals"]
    # PATTERN and helper are module names read; LIMIT is rebound in the body,
    # re is an import, found is local.
    assert (uses.globals, uses.global_names) == (2, ["PATTERN", "helper"])
    # What a reader of uses_globals has to look at elsewhere in the file:
    # PATTERN is one code line; helper is a def, which weighs its signature
    # (one line), not its body: a caller reads a name, not an implementation.
    assert uses.global_lines == 2
    table = by_name["uses_table"]
    # A data binding keeps its code lines, string-keyed dict lines included.
    assert (table.globals, table.global_names, table.global_lines) == (1, ["TABLE"], 4)
    assert (by_name["uses_box"].global_names, by_name["uses_box"].global_lines) == (["Box"], 1)
    assert (by_name["no_globals"].globals, by_name["no_globals"].global_lines) == (0, 0)
    assert by_name["helper"].globals == 0
    # def, found =, LIMIT =, return: the four-line docstring and the comment
    # and the blank line are not code.
    assert (uses.loc, uses.doc) == (4, 4)
    # found defined at 22 and read at 25: one code line (LIMIT = 1) between
    # them; the blank line 23 does not count.
    assert uses.reach == 1
    # Module-level: the import, three one-line assignments and the four-line
    # TABLE literal are code outside defs and classes; the ten bindings exclude
    # the import.
    assert report.summary["module_lines"] == 8
    assert report.summary["module_bindings"] == 10
    # helper 0, uses_globals 2, no_globals 0, uses_table 4, uses_box 1
    assert report.summary["global_lines"] == {"max": 4, "median": 1}


def test_recursion_is_a_bare_name_call_or_self_cls_on_a_method(mod, tmp_path) -> None:
    path = tmp_path / "recursion.py"
    path.write_text(RECURSION_FIXTURE)
    report = mod.score_file(path, mod.Thresholds(), dataflow=False)
    by_name = {s.name: s for s in report.functions}
    # first.rules() and second.rules() are other modules' functions, not recursion.
    assert by_name["rules"].cognitive == 0
    # if (+1) and self.walk (+1); other.walk is not recursion.
    assert by_name["Node.walk"].cognitive == 2
    # ternary (+1) and cls.build (+1); helper.build is not recursion.
    assert by_name["Node.build"].cognitive == 2


def test_loc_counts_logical_lines_and_the_json_keeps_the_physical_count(mod, tmp_path) -> None:
    path = tmp_path / "literal.py"
    path.write_text(LITERAL_FIXTURE)
    report = mod.score_file(path, mod.Thresholds(), dataflow=False)
    (fn,) = report.functions
    # def, text = (, table = {, return: the three string continuations, the
    # two string-keyed dict lines and the two closing brackets are read, not
    # reasoned about.
    assert (fn.loc, fn.loc_physical) == (4, 11)


def test_sort_globals_orders_by_global_lines_and_the_table_drops_params_and_calls(
    mod, tmp_path, capsys,
) -> None:
    path = tmp_path / "globals.py"
    path.write_text(GLOBALS_FIXTURE)
    assert mod.main([str(path), "--sort", "globals", "--top", "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    # uses_table reads four module lines, uses_globals three.
    assert [line.split()[1] for line in out[2:4]] == ["uses_table", "uses_globals"]
    assert out[1].split() == [
        "band", "function", "lines", "loc", "doc", "mcc", "cog", "nest", "dflow", "vars",
        "reach", "gloc", "flags",
    ]
    assert out[-1].endswith("global lines max 4 median 1")


def test_a_nested_function_reading_a_captured_variable_is_counted_once(mod, tmp_path) -> None:
    path = tmp_path / "capture.py"
    path.write_text(CAPTURE_FIXTURE)
    report = mod.score_file(path, mod.Thresholds())
    by_name = {s.name: s for s in report.functions}
    # regex: 1->2 (the nested def), 1->3, 1->4, 1->5; flags: 1->2, 1->4. The raw
    # list repeats the 1->2 triples once per read inside inner.
    assert (by_name["outer"].dataflow, by_name["outer"].vars) == (6, 2)
    assert by_name["outer.inner"].dataflow == 0


def test_a_coverage_gap_marks_dataflow_suspect(mod, sample, monkeypatch, capsys) -> None:
    import hypergumbo_core.cfg as cfg_mod

    monkeypatch.setattr(cfg_mod, "uncovered_semantic_lines", lambda *a, **k: frozenset({3}))
    report = mod.score_file(sample, mod.Thresholds())
    assert all(s.dataflow_uncovered == 1 and s.dataflow_suspect for s in report.functions)
    assert report.summary["dataflow_suspect"] == 4
    assert all(s.band == "ok" for s in report.functions)  # a floor never flags
    assert mod.main([str(sample), "--sort", "line"]) == 0
    out = capsys.readouterr().out
    assert " 2? " in out and "dataflow suspect 4" in out
    # A language whose gate cannot say is suspect too.
    monkeypatch.setattr(cfg_mod, "uncovered_semantic_lines", lambda *a, **k: None)
    report = mod.score_file(sample, mod.Thresholds())
    assert all(s.dataflow_uncovered is None and s.dataflow_suspect for s in report.functions)


def test_flags_and_bands_follow_the_thresholds(mod, sample) -> None:
    report = mod.score_file(sample, mod.Thresholds(cognitive=6, nesting=2, loc=10))
    by_name = {s.name: s for s in report.functions}
    # A flag carries the measured value beside the threshold it crossed.
    assert by_name["Widget.loop"].flags == ["loc 12>10", "cognitive 7>6", "nesting 3>2"]
    assert by_name["Widget.loop"].band == "split"
    assert by_name["Widget.deco"].flags == []
    assert by_name["Widget.deco"].band == "ok"
    report = mod.score_file(sample, mod.Thresholds(dataflow=3))
    by_name = {s.name: s for s in report.functions}
    assert by_name["plain"].band == "ok"
    assert by_name["Widget.deco"].band == "watch"
    assert by_name["Widget.deco"].flags == ["dataflow 4>3"]
    # deco's 4 and loop's 13 both exceed 3; the count keys on the measure name.
    assert report.summary["flag_counts"] == {"dataflow": 2}


def test_cli_text_sorts_by_cognitive_and_strict_gates(mod, sample, capsys) -> None:
    assert mod.main([str(sample)]) == 0
    out = capsys.readouterr().out.splitlines()
    rows = [line.split()[1] for line in out[2:6]]
    assert rows == ["Widget.loop", "Widget.deco", "rec", "plain"]
    # Module lines: the module docstring and the import. Bindings: plain, Widget, rec.
    assert out[-1].startswith("summary: ok 4, watch 0, split 0; module lines 2, module bindings 3;")
    assert mod.main([str(sample), "--strict"]) == 0
    assert mod.main([str(sample), "--strict", "--max-cognitive", "6"]) == 1
    capsys.readouterr()
    assert mod.main([str(sample), "--top", "1", "--sort", "loc"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 4 and out[2].split()[1] == "Widget.loop"
    assert mod.main([str(sample), "--top", "1", "--sort", "globals"]) == 0


def test_cli_json_carries_the_whole_panel(mod, sample, capsys) -> None:
    assert mod.main([str(sample), "--json", "--max-dataflow", "4"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["thresholds"] == {
        "loc": 50, "mccabe": 10, "cognitive": 15, "nesting": 4, "dataflow": 4,
    }
    (file,) = data["files"]
    assert file["path"] == str(sample)
    names = [f["name"] for f in file["functions"]]
    assert names == ["plain", "Widget.deco", "Widget.loop", "rec"]
    # Only loop's 13 edges exceed 4; deco sits exactly on the threshold.
    assert file["summary"]["bands"] == {"ok": 3, "watch": 1, "split": 0}
    assert file["summary"]["flag_counts"] == {"dataflow": 1}
    assert file["summary"]["cognitive"] == {"max": 7, "median": 3.5}
    assert file["summary"]["dataflow_suspect"] == 0
    assert file["functions"][0]["global_names"] == []
    assert file["functions"][0]["global_lines"] == 0
    assert file["functions"][0]["loc"] == 2 and file["functions"][0]["loc_physical"] == 2
    assert file["functions"][0]["dataflow_uncovered"] == 0
    # params and calls are not in the text table; the JSON still carries them.
    assert file["functions"][0]["params"] == 4 and file["functions"][0]["calls"] == 0
    assert file["functions"][2]["flags"] == ["dataflow 13>4"]  # Widget.loop


def test_no_dataflow_skips_the_ddg_and_a_syntax_error_still_scores(mod, tmp_path, capsys) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def good(n):\n    if n:\n        return 1\n\nthis is not python\n")
    assert mod.main([str(broken), "--no-dataflow", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    (file,) = data["files"]
    (fn,) = file["functions"]
    assert fn["name"] == "good"
    # The analyzer's error recovery still names the intact function.
    assert fn["mccabe"] == 2 and fn["params"] == 1
    assert fn["loc"] == 3 and fn["loc_physical"] == 3
    # An unclosed bracket makes the tokenizer give up at the end of the file;
    # the logical-line pass keeps the lines it had classified by then.
    assert mod.non_logical_lines(b'y = (\n    "a"\n    "b"\n)\nx = (\n') == {2, 3, 4}
    assert fn["cognitive"] == 1 and fn["nesting"] == 1
    assert fn["dataflow"] is None and fn["vars"] is None and fn["reach"] is None
    assert fn["dataflow_uncovered"] is None
    assert file["summary"]["dataflow_suspect"] == 0  # not measured is not suspect


def test_cli_refuses_a_missing_file(mod, tmp_path, capsys) -> None:
    assert mod.main([str(tmp_path / "nope.py")]) == 2
    assert "not a file" in capsys.readouterr().err
