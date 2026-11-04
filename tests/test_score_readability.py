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


def test_dataflow_uses_hypergumbos_reaching_definitions(panel) -> None:
    scores, _ = panel
    # ``a`` and ``b`` are entry definitions used once each at the return.
    plain = scores["plain"]
    assert (plain.dataflow, plain.vars, plain.reach) == (2, 2, 2)
    loop = scores["Widget.loop"]
    assert loop.vars == 3  # total, items, i
    assert loop.dataflow >= 8
    assert loop.reach == 10  # total defined at 21, returned at 31
    assert scores["Widget.deco"].vars == 1  # x


def test_loc_params_and_calls(panel) -> None:
    scores, _ = panel
    assert scores["plain"].loc == 3  # def, docstring, return
    assert scores["plain"].params == 4  # a, b, *args, **kw
    assert scores["Widget.loop"].loc == 12
    assert scores["Widget.loop"].calls == 1  # os.getcwd()
    assert scores["rec"].calls == 1


def test_flags_and_bands_follow_the_thresholds(mod, sample) -> None:
    report = mod.score_file(sample, mod.Thresholds(cognitive=6, nesting=2, loc=10))
    by_name = {s.name: s for s in report.functions}
    assert by_name["Widget.loop"].flags == ["loc>10", "cognitive>6", "nesting>2"]
    assert by_name["Widget.loop"].band == "split"
    assert by_name["Widget.deco"].flags == []
    assert by_name["Widget.deco"].band == "ok"
    report = mod.score_file(sample, mod.Thresholds(dataflow=4))
    by_name = {s.name: s for s in report.functions}
    assert by_name["plain"].band == "ok"
    assert by_name["Widget.deco"].band == "watch"
    assert by_name["Widget.deco"].flags == ["dataflow>4"]


def test_cli_text_sorts_by_cognitive_and_strict_gates(mod, sample, capsys) -> None:
    assert mod.main([str(sample)]) == 0
    out = capsys.readouterr().out.splitlines()
    rows = [line.split()[1] for line in out[2:6]]
    assert rows == ["Widget.loop", "Widget.deco", "rec", "plain"]
    assert out[-1].startswith("summary: ok 4, watch 0, split 0;")
    assert mod.main([str(sample), "--strict"]) == 0
    assert mod.main([str(sample), "--strict", "--max-cognitive", "6"]) == 1
    capsys.readouterr()
    assert mod.main([str(sample), "--top", "1", "--sort", "loc"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 4 and out[2].split()[1] == "Widget.loop"


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
    # plain 2 and rec 3 edges sit under the threshold; deco 5 and loop 13 do not.
    assert file["summary"]["bands"] == {"ok": 2, "watch": 2, "split": 0}
    assert file["summary"]["flag_counts"] == {"dataflow": 2}
    assert file["summary"]["cognitive"] == {"max": 7, "median": 3.5}


def test_no_dataflow_skips_the_ddg_and_a_syntax_error_still_scores(mod, tmp_path, capsys) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def good(n):\n    if n:\n        return 1\n\nthis is not python\n")
    assert mod.main([str(broken), "--no-dataflow", "--json"]) == 0
    (file,) = json.loads(capsys.readouterr().out)["files"]
    (fn,) = file["functions"]
    assert fn["name"] == "good"
    # The analyzer's error recovery still names the intact function.
    assert fn["mccabe"] == 2 and fn["params"] == 1
    assert fn["cognitive"] == 1 and fn["nesting"] == 1
    assert fn["dataflow"] is None and fn["vars"] is None and fn["reach"] is None


def test_cli_refuses_a_missing_file(mod, tmp_path, capsys) -> None:
    assert mod.main([str(tmp_path / "nope.py")]) == 2
    assert "not a file" in capsys.readouterr().err
