# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/test-leaderboard`` stores each test under pytest's node id (WI-jagus).

junit's ``classname`` is the module's dotted path followed by any classes.
The leaderboard took its last dotted part as a class, so every module-level
test function (``classname`` ends at the module) was stored under a path that
does not exist: ``a/tests.py::test_x::name`` for ``a/tests/test_x.py::name``.
On 2026-11-19 that was 12,647 of 41,671 keys, and every join of timings to
test ids missed them (smart-test's time estimate counted them as 0 s).

Pinned: the node id of a module-level function, of a method, of a nested
class and of a package whose name holds a dash; a broken key already in the
store is read under its node id, its runs merged with the right key's.
"""

# covers: scripts/test-leaderboard
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test-leaderboard"


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("test_leaderboard_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader("test_leaderboard_script", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_leaderboard_script"] = module
    loader.exec_module(module)
    return module


lb = _load()

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="packages.hypergumbo-core.tests.test_x" name="test_f" time="1.5"/>
<testcase classname="packages.hypergumbo-core.tests.test_x.TestK" name="test_m[a.b]" time="2"/>
<testcase classname="tests.test_y.TestOuter.TestInner" name="test_n" time="0.25"/>
<testcase classname="packages.p.tests.BRANCHES_test_z" name="test_b" time="0"/>
<testcase classname="" name="bare" time="1"/>
</testsuite></testsuites>"""


def test_node_ids_are_pytests(tmp_path: Path) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text(JUNIT)
    assert lb._parse_junit_xml(str(xml)) == [
        ("packages/hypergumbo-core/tests/test_x.py::test_f", 1.5),
        ("packages/hypergumbo-core/tests/test_x.py::TestK::test_m[a.b]", 2.0),
        ("tests/test_y.py::TestOuter::TestInner::test_n", 0.25),
        ("packages/p/tests/BRANCHES_test_z.py::test_b", 0.0),
        ("bare", 1.0),
    ]


def test_a_classname_with_no_test_module_keeps_the_old_reading(tmp_path: Path) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text('<testsuite><testcase classname="pkg.checks.Klass" name="t" time="1"/>'
                   '<testcase classname="single" name="u" time="1"/></testsuite>')
    assert lb._parse_junit_xml(str(xml)) == [("pkg/checks.py::Klass::t", 1.0),
                                             ("single.py::u", 1.0)]


def test_broken_keys_are_read_under_their_node_id(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "timings.json"
    store.write_text(json.dumps({
        "packages/hypergumbo-core/tests.py::test_x::test_f": {"runs": [
            {"timestamp": "2026-11-01", "seconds": 1.0}]},
        "packages/hypergumbo-core/tests/test_x.py::test_f": {"runs": [
            {"timestamp": "2026-11-02", "seconds": 2.0}]},
        "tests.py::test_y::test_g[p]": {"runs": [{"timestamp": "2026-11-03", "seconds": 3.0}]},
        "packages/p/tests.py::BRANCHES_test_z::TestK::test_h": {"runs": [
            {"timestamp": f"2026-11-0{i}", "seconds": float(i)} for i in range(1, 8)]},
        "packages/p/tests/test_ok.py::TestK::test_i": {"runs": []},
    }))
    monkeypatch.setattr(lb, "TIMINGS_PATH", store)
    timings = lb._load_timings()
    assert set(timings) == {
        "packages/hypergumbo-core/tests/test_x.py::test_f",
        "tests/test_y.py::test_g[p]",
        "packages/p/tests/BRANCHES_test_z.py::TestK::test_h",
        "packages/p/tests/test_ok.py::TestK::test_i"}
    assert [r["seconds"] for r in timings["packages/hypergumbo-core/tests/test_x.py::test_f"]["runs"]] == [1.0, 2.0]
    # Runs past the cap: the newest are kept.
    assert [r["seconds"] for r in
            timings["packages/p/tests/BRANCHES_test_z.py::TestK::test_h"]["runs"]] == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_an_ingest_rewrites_the_store_repaired(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "timings.json"
    store.write_text(json.dumps({"tests.py::test_y::test_old": {"runs": [{"timestamp": "t", "seconds": 1}]}}))
    monkeypatch.setattr(lb, "TIMINGS_PATH", store)
    xml = tmp_path / "junit.xml"
    xml.write_text(JUNIT)
    assert lb.cmd_ingest(str(xml)) == 0
    keys = set(json.loads(store.read_text()))
    assert "tests/test_y.py::test_old" in keys
    assert not any(lb._BROKEN_KEY.match(k) for k in keys)


def test_a_missing_or_unreadable_store_is_empty(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lb, "TIMINGS_PATH", tmp_path / "absent.json")
    assert lb._load_timings() == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(lb, "TIMINGS_PATH", bad)
    assert lb._load_timings() == {}
