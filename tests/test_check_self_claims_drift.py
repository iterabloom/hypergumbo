# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/check-self-claims-drift (INV-zuhig snapshot v2).

The drift gate pins verdict + caveat kinds + credited-flows bucket per
claim. These tests pin the LOGIC on synthetic verdict JSONs — most
importantly the INV-zuhig shape: a verdict staying green while its
sanitizer-credited flow count collapses to zero must FAIL the gate.
"""

import importlib.machinery
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "check_self_claims_drift", str(SCRIPTS / "check-self-claims-drift")
    )
    spec = importlib.util.spec_from_loader("check_self_claims_drift", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


drift_mod = _load()


def _verdict(claim_id="c1", verdict="confirmed_with_caveats",
             caveat_kinds=("opaque_boundary",), sanitized=5):
    return {
        "claim_id": claim_id,
        "verdict": verdict,
        "caveats": [{"kind": k, "detail": "..."} for k in caveat_kinds],
        "sanitized_flows": sanitized,
    }


# --- summarize -------------------------------------------------------------


def test_summarize_buckets_credited_flows():
    s = drift_mod.summarize([_verdict(sanitized=98), _verdict("c2", sanitized=0)])
    assert s["c1"]["credited_flows"] == "nonzero"
    assert s["c2"]["credited_flows"] == "zero"


def test_summarize_sorts_and_dedupes_caveat_kinds():
    s = drift_mod.summarize([_verdict(
        caveat_kinds=("user_supplied_sanitizer", "opaque_boundary",
                      "opaque_boundary"),
    )])
    assert s["c1"]["caveats"] == ["opaque_boundary", "user_supplied_sanitizer"]


def test_summarize_tolerates_unkinded_caveat():
    v = _verdict()
    v["caveats"] = [{"detail": "no kind key"}]
    s = drift_mod.summarize([v])
    assert s["c1"]["caveats"] == ["<unkinded>"]


# --- diff ------------------------------------------------------------------


def test_no_drift_on_identical():
    a = drift_mod.summarize([_verdict()])
    assert drift_mod.diff(a, a) == []


def test_verdict_drift_detected():
    exp = drift_mod.summarize([_verdict()])
    act = drift_mod.summarize([_verdict(verdict="violated")])
    rows = drift_mod.diff(exp, act)
    assert [(r[0], r[1]) for r in rows] == [("c1", "verdict")]


def test_credited_flows_collapse_is_drift():
    # THE INV-zuhig SHAPE: verdict unchanged, credited evidence died.
    exp = drift_mod.summarize([_verdict(sanitized=98)])
    act = drift_mod.summarize([_verdict(sanitized=0)])
    rows = drift_mod.diff(exp, act)
    assert [(r[0], r[1]) for r in rows] == [("c1", "credited_flows")]


def test_new_caveat_kind_is_drift():
    exp = drift_mod.summarize([_verdict()])
    act = drift_mod.summarize([_verdict(
        caveat_kinds=("opaque_boundary", "user_supplied_sanitizer"),
    )])
    rows = drift_mod.diff(exp, act)
    assert [(r[0], r[1]) for r in rows] == [("c1", "caveats")]


def test_added_and_absent_claims_are_drift():
    exp = drift_mod.summarize([_verdict("old")])
    act = drift_mod.summarize([_verdict("new")])
    rows = drift_mod.diff(exp, act)
    assert {(r[0], r[1]) for r in rows} == {("old", "claim"), ("new", "claim")}


# --- main ------------------------------------------------------------------


def _write_run(tmp_path, verdicts, name="run.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"verdicts": verdicts}))
    return p


def test_main_ok_roundtrip(tmp_path, capsys):
    run = _write_run(tmp_path, [_verdict()])
    snap = tmp_path / "snap.json"
    assert drift_mod.main(["prog", str(run), str(snap), "--update"]) == 0
    assert drift_mod.main(["prog", str(run), str(snap)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_drift_exits_1_and_names_field(tmp_path, capsys):
    run_a = _write_run(tmp_path, [_verdict(sanitized=98)], "a.json")
    run_b = _write_run(tmp_path, [_verdict(sanitized=0)], "b.json")
    snap = tmp_path / "snap.json"
    assert drift_mod.main(["prog", str(run_a), str(snap), "--update"]) == 0
    assert drift_mod.main(["prog", str(run_b), str(snap)]) == 1
    err = capsys.readouterr().err
    assert "credited_flows" in err
    assert "REGRESSED" in err  # nonzero -> zero is the regression direction


def test_main_missing_snapshot_is_infrastructure(tmp_path):
    run = _write_run(tmp_path, [_verdict()])
    assert drift_mod.main(["prog", str(run), str(tmp_path / "nope.json")]) == 2


def test_main_v1_snapshot_is_infrastructure(tmp_path, capsys):
    # The old format ({claim_id: "verdict"}) must demand a re-record, not
    # crash or silently pass.
    run = _write_run(tmp_path, [_verdict()])
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"c1": "inconclusive"}))
    assert drift_mod.main(["prog", str(run), str(snap)]) == 2
    assert "v1" in capsys.readouterr().err


def test_main_unreadable_run_is_infrastructure(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    snap = tmp_path / "snap.json"
    assert drift_mod.main(["prog", str(bad), str(snap)]) == 2
