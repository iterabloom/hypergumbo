# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/bakeoff-map.

Verifies: classification by name, live session probing across the three
format eras (phase12, early broad, modern deep), zip-index heuristics,
`--zip-deep` temp-extract path, tarball handling, filter flags, render
modes, stale-reflect_statuses detection, and error tolerance for
unreadable state.json / broken archives.

All tests use tmp_path fixtures with synthetic session trees — no real
bakeoff runs are needed. The script is imported as a module for direct
function calls.
"""

import importlib.machinery
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def bmap():
    """Import scripts/bakeoff-map as a module despite the hyphen."""
    script_path = str(Path(__file__).parent.parent / "scripts" / "bakeoff-map")
    loader = importlib.machinery.SourceFileLoader("bakeoff_map", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_map", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers to build synthetic sessions
# ---------------------------------------------------------------------------


def _write_state(session_dir: Path, state: dict) -> None:
    (session_dir / "state.json").write_text(json.dumps(state))


def _make_cohort_iter(session_dir: Path, cohort: int, iter_n: int, repo: str) -> None:
    out_dir = session_dir / "out" / f"cohort-{cohort:03d}" / f"iter-{iter_n:03d}" / repo
    out_dir.mkdir(parents=True)
    (out_dir / "hg.json").write_text("{}")


def _make_diag_report(session_dir: Path, cohort: int, iter_n: int) -> None:
    diag_dir = session_dir / "diag" / f"cohort-{cohort:03d}" / f"iter-{iter_n:03d}"
    diag_dir.mkdir(parents=True)
    (diag_dir / "report.yaml").write_text("verdicts: []")


def _make_reflect(
    session_dir: Path,
    cohort: int,
    iter_n: int,
    repos: list[str],
    *,
    prompts: bool = True,
    assessments: bool = True,
    summary: bool = True,
) -> None:
    reflect_dir = session_dir / "reflect" / f"cohort-{cohort:03d}" / f"iter-{iter_n:03d}"
    reflect_dir.mkdir(parents=True)
    for repo in repos:
        if prompts:
            (reflect_dir / f"{repo}.prompt.md").write_text("# prompt")
        if assessments:
            (reflect_dir / f"{repo}.assessment.yaml").write_text("verdict: GOOD")
    if summary:
        (reflect_dir / "summary.yaml").write_text("score: 1.0")


def _make_cohort_metadata(session_dir: Path, cohort: int, repos: list[str]) -> None:
    cohort_dir = session_dir / "cohorts" / f"cohort-{cohort:03d}"
    cohort_dir.mkdir(parents=True)
    (cohort_dir / "metadata.json").write_text(json.dumps({"repos": repos}))


@pytest.fixture()
def modern_deep_session(tmp_path):
    session = tmp_path / "deep-20260409-054615"
    session.mkdir()
    repos = ["alertmanager", "prometheus", "kafka"]
    state = {
        "session_id": "abc123",
        "pool_path": "/p",
        "workdir": str(session),
        "current_cohort": repos,
        "cohort_number": 1,
        "iteration": 2,
        "tested_repos": repos,
        "verdicts": [
            {"repo_name": r, "verdict": "GOOD", "highlights": [], "concerns": [], "metrics": {}}
            for r in repos
        ],
        "created_at": "2026-04-09T00:00:00",
        "updated_at": "2026-04-09T00:00:00",
        "hypergumbo_code_hash": "deadbeef",
        "reflect_statuses": [
            {
                "cohort_number": 1,
                "has_prompts": False,
                "has_assessments": False,
                "has_summary": False,
                "assessment_count": 0,
                "repo_count": 3,
            }
        ],
    }
    _write_state(session, state)
    _make_cohort_metadata(session, 1, repos)
    for it in (1, 2):
        for repo in repos:
            _make_cohort_iter(session, 1, it, repo)
        _make_diag_report(session, 1, it)
        _make_reflect(session, 1, it, repos)
    return session


@pytest.fixture()
def early_broad_session(tmp_path):
    session = tmp_path / "broad-20260124-001510"
    session.mkdir()
    state = {
        "session_id": "br1",
        "pool_path": "/p",
        "workdir": str(session),
        "current_cohort": ["a", "b"],
        "cohort_number": 1,
        "iteration": 1,
        "tested_repos": ["a", "b"],
        "issues": [],
        "convergence_history": [
            {"iteration": 1, "cohort": 1, "timestamp": "t", "total_issues": 0, "new_issues": 0, "critical": 0, "high": 0}
        ],
        "created_at": "2026-01-24T00:00:00",
        "updated_at": "2026-01-24T00:00:00",
    }
    _write_state(session, state)
    _make_cohort_metadata(session, 1, ["a", "b"])
    for repo in ("a", "b"):
        _make_cohort_iter(session, 1, 1, repo)
    return session


@pytest.fixture()
def phase12_session(tmp_path):
    session = tmp_path / "phase12-01-20260105-1707"
    (session / "out" / "repo-a").mkdir(parents=True)
    (session / "out" / "repo-b").mkdir(parents=True)
    (session / "repos").mkdir()
    return session


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_phase12(bmap):
    kind, era, iso = bmap.classify("phase12-01-20260105-1707")
    assert kind == "phase12"
    assert era == "phase12"
    assert iso == "2026-01-05T17:07:00"


def test_classify_phase12_misc(bmap):
    kind, era, iso = bmap.classify("phase12-misc-20260111-0430")
    assert kind == "phase12"
    assert iso == "2026-01-11T04:30:00"


def test_classify_broad_early(bmap):
    kind, era, iso = bmap.classify("broad-20260124-001510")
    assert kind == "broad"
    assert era == "early"
    assert iso == "2026-01-24T00:15:10"


def test_classify_broad_modern(bmap):
    kind, era, _ = bmap.classify("broad-20260301-000000")
    assert kind == "broad"
    assert era == "modern"


def test_classify_deep_modern(bmap):
    kind, era, iso = bmap.classify("deep-20260409-054615")
    assert kind == "deep"
    assert era == "modern"
    assert iso == "2026-04-09T05:46:15"


def test_classify_deep_zip_strips_suffix(bmap):
    kind, _era, iso = bmap.classify("deep-20260226-114923.zip")
    assert kind == "deep"
    assert iso == "2026-02-26T11:49:23"


def test_classify_adhoc(bmap):
    kind, era, iso = bmap.classify("adhoc-20260124-1524-framework.zip")
    assert kind == "adhoc"
    assert era == "n/a"
    assert iso == "2026-01-24T15:24:00"


def test_classify_special_deep_postfix(bmap):
    kind, _era, _ = bmap.classify("deep-postfix-validation")
    assert kind == "special"


def test_classify_special_named_date(bmap):
    # deep-20260301-final has a date but non-standard suffix
    kind, _era, iso = bmap.classify("deep-20260301-final")
    assert kind == "special"
    assert iso == "2026-03-01T00:00:00"


def test_classify_tarball_special(bmap):
    kind, _era, iso = bmap.classify("bakeoff_session_2026-02-04.tar.gz")
    assert kind == "special"
    assert iso and iso.startswith("2026-02-04")


def test_classify_unknown(bmap):
    kind, _era, iso = bmap.classify("random-garbage")
    assert kind == "unknown"
    assert iso is None


def test_classify_bad_date_parts(bmap):
    # _iso_from_parts should tolerate odd lengths
    assert bmap._iso_from_parts("bad", None) is None
    assert bmap._iso_from_parts(None, None) is None
    assert bmap._iso_from_parts("20260105", "12") == "2026-01-05T00:00:00"
    assert bmap._iso_from_parts("20260105", "123456") == "2026-01-05T12:34:56"


def test_era_for_invalid(bmap):
    assert bmap._era_for(None) == "n/a"
    assert bmap._era_for("not-a-date") == "n/a"


def test_scan_for_date_dashed(bmap):
    assert bmap._scan_for_date("bakeoff_session_2026-02-04").startswith("2026-02-04")


def test_scan_for_date_none(bmap):
    assert bmap._scan_for_date("nothing-here") is None


# ---------------------------------------------------------------------------
# Live session probing
# ---------------------------------------------------------------------------


def test_probe_modern_deep_converged(bmap, modern_deep_session):
    rec = bmap.build_record(modern_deep_session)
    bmap.probe_live_session(modern_deep_session, rec)
    assert rec.has_state_json is True
    assert rec.iteration == 2
    assert rec.cohort_count_on_disk == 1
    assert rec.iteration_count_on_disk == 2
    assert rec.convergence.verdict == "converged"
    assert all(v.verdict == "GOOD" for v in rec.verdicts)
    assert rec.stages["run"].status == "done"
    assert rec.stages["diagnose"].status == "done"
    assert rec.stages["reflect_summary"].status == "done"
    # Stale reflect_statuses should be flagged
    assert any("stale reflect_statuses" in a for a in rec.anomalies)
    # Nothing should be missing
    assert not rec.missing


def test_probe_early_broad_converged(bmap, early_broad_session):
    rec = bmap.build_record(early_broad_session)
    bmap.probe_live_session(early_broad_session, rec)
    assert rec.kind == "broad"
    assert rec.convergence.verdict == "converged"
    assert rec.stages["run"].status == "done"
    assert rec.stages["diagnose"].status == "missing"


def test_probe_phase12(bmap, phase12_session):
    rec = bmap.build_record(phase12_session)
    bmap.probe_live_session(phase12_session, rec)
    assert rec.kind == "phase12"
    assert rec.stages["run"].status == "done"
    assert rec.stages["diagnose"].status == "not_applicable"
    assert rec.convergence.verdict == "n/a"
    assert rec.cohort_count_on_disk == 2  # two repo dirs in out/


def test_phase12_empty_out_dir(bmap, tmp_path):
    session = tmp_path / "phase12-02-20260106-1556"
    (session / "out").mkdir(parents=True)
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.stages["run"].status == "missing"


def test_phase12_no_out_dir(bmap, tmp_path):
    session = tmp_path / "phase12-03-20260107-0050"
    session.mkdir()
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.stages["run"].status == "missing"


def test_probe_deep_not_converged(bmap, tmp_path):
    session = tmp_path / "deep-20260305-000000"
    session.mkdir()
    state = {
        "session_id": "x",
        "pool_path": "/p",
        "workdir": str(session),
        "current_cohort": ["r"],
        "cohort_number": 1,
        "iteration": 3,
        "tested_repos": ["r"],
        "verdicts": [
            {"repo_name": "r", "verdict": "WARN", "highlights": [], "concerns": ["BAD"], "metrics": {}}
        ],
        "created_at": "t",
        "updated_at": "t",
        "hypergumbo_code_hash": "h",
        "reflect_statuses": [],
    }
    _write_state(session, state)
    _make_cohort_metadata(session, 1, ["r"])
    _make_cohort_iter(session, 1, 1, "r")
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "not_converged"
    assert "1/1 not GOOD" in rec.convergence.detail


def test_probe_deep_clean_first_pass(bmap, tmp_path):
    session = tmp_path / "deep-20260306-000000"
    session.mkdir()
    state = {
        "session_id": "y",
        "pool_path": "/p",
        "workdir": str(session),
        "current_cohort": ["r"],
        "cohort_number": 1,
        "iteration": 1,
        "tested_repos": ["r"],
        "verdicts": [
            {"repo_name": "r", "verdict": "GOOD", "highlights": [], "concerns": [], "metrics": {}}
        ],
        "created_at": "t",
        "updated_at": "t",
        "hypergumbo_code_hash": "h",
        "reflect_statuses": [],
    }
    _write_state(session, state)
    _make_cohort_iter(session, 1, 1, "r")
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "clean_first_pass"


def test_probe_deep_no_verdicts(bmap, tmp_path):
    session = tmp_path / "deep-20260307-000000"
    session.mkdir()
    _write_state(session, {"session_id": "z", "iteration": 1, "verdicts": []})
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "unknown"


def test_probe_broad_not_converged(bmap, tmp_path):
    session = tmp_path / "broad-20260202-000000"
    session.mkdir()
    state = {
        "session_id": "b",
        "iteration": 2,
        "verdicts": [],  # ignored for broad
        "convergence_history": [
            {"iteration": 1, "cohort": 1, "critical": 3, "high": 5, "new_issues": 2},
            {"iteration": 2, "cohort": 1, "critical": 1, "high": 2, "new_issues": 1},
        ],
    }
    _write_state(session, state)
    rec = bmap.build_record(session)
    rec.kind = "broad"
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "not_converged"
    assert rec.issues_critical == 1
    assert rec.issues_high == 2


def test_probe_broad_no_history(bmap, tmp_path):
    session = tmp_path / "broad-20260203-000000"
    session.mkdir()
    _write_state(session, {"session_id": "b2", "convergence_history": []})
    rec = bmap.build_record(session)
    rec.kind = "broad"
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "unknown"


def test_probe_broken_state_json(bmap, tmp_path):
    session = tmp_path / "deep-20260310-000000"
    session.mkdir()
    (session / "state.json").write_text("{not valid json")
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.has_state_json is False
    assert any("unreadable" in a for a in rec.anomalies)


def test_probe_missing_state_json(bmap, tmp_path):
    session = tmp_path / "deep-20260311-000000"
    session.mkdir()
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.has_state_json is False
    assert rec.convergence.verdict == "unknown"


def test_stale_assessments_detection(bmap, tmp_path):
    session = tmp_path / "deep-20260315-000000"
    session.mkdir()
    state = {
        "session_id": "s",
        "iteration": 1,
        "verdicts": [{"repo_name": "r", "verdict": "GOOD", "concerns": [], "metrics": {}}],
        "reflect_statuses": [
            {"cohort_number": 1, "has_prompts": True, "has_assessments": False, "has_summary": False, "assessment_count": 0, "repo_count": 1}
        ],
    }
    _write_state(session, state)
    _make_cohort_iter(session, 1, 1, "r")
    _make_reflect(session, 1, 1, ["r"], prompts=True, assessments=True, summary=False)
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert any("assessments on disk" in a for a in rec.anomalies)


def test_stale_summary_detection(bmap, tmp_path):
    session = tmp_path / "deep-20260316-000000"
    session.mkdir()
    state = {
        "session_id": "s2",
        "iteration": 1,
        "verdicts": [{"repo_name": "r", "verdict": "GOOD", "concerns": [], "metrics": {}}],
        "reflect_statuses": [
            {"cohort_number": 1, "has_prompts": True, "has_assessments": True, "has_summary": False, "assessment_count": 1, "repo_count": 1}
        ],
    }
    _write_state(session, state)
    _make_cohort_iter(session, 1, 1, "r")
    _make_reflect(session, 1, 1, ["r"], prompts=True, assessments=True, summary=True)
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert any("summary on disk" in a for a in rec.anomalies)


def test_reflect_status_no_cohort_number(bmap, tmp_path):
    """Entries with None cohort_number are skipped without crashing."""
    session = tmp_path / "deep-20260317-000000"
    session.mkdir()
    state = {
        "session_id": "s3",
        "iteration": 1,
        "verdicts": [{"repo_name": "r", "verdict": "GOOD", "concerns": [], "metrics": {}}],
        "reflect_statuses": [{"has_prompts": False}],  # missing cohort_number
    }
    _write_state(session, state)
    _make_cohort_iter(session, 1, 1, "r")
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert not any("stale reflect_statuses" in a for a in rec.anomalies)


def test_partial_run_stage(bmap, tmp_path):
    session = tmp_path / "deep-20260318-000000"
    session.mkdir()
    state = {
        "session_id": "p",
        "iteration": 1,
        "verdicts": [{"repo_name": "r", "verdict": "GOOD", "concerns": [], "metrics": {}}],
        "reflect_statuses": [],
    }
    _write_state(session, state)
    _make_cohort_metadata(session, 1, ["r"])
    _make_cohort_metadata(session, 2, ["r"])
    _make_cohort_iter(session, 1, 1, "r")  # cohort 2 has no iter dirs -> partial
    rec = bmap.build_record(session)
    bmap.probe_live_session(session, rec)
    assert rec.stages["run"].status == "partial"


# ---------------------------------------------------------------------------
# Zip/tarball probing
# ---------------------------------------------------------------------------


def _make_zip_with_session(path: Path, session_name: str, extras: list[str] = None) -> None:
    extras = extras or []
    with zipfile.ZipFile(path, "w") as zf:
        state = {
            "session_id": "zip1",
            "iteration": 1,
            "verdicts": [{"repo_name": "r", "verdict": "GOOD", "concerns": [], "metrics": {}}],
            "convergence_history": [],
            "reflect_statuses": [],
        }
        zf.writestr(f"{session_name}/state.json", json.dumps(state))
        zf.writestr(f"{session_name}/out/cohort-001/iter-001/r/hg.json", "{}")
        zf.writestr(f"{session_name}/diag/cohort-001/iter-001/report.yaml", "[]")
        zf.writestr(f"{session_name}/reflect/cohort-001/iter-001/r.prompt.md", "p")
        zf.writestr(f"{session_name}/reflect/cohort-001/iter-001/r.assessment.yaml", "a")
        zf.writestr(f"{session_name}/reflect/cohort-001/iter-001/summary.yaml", "s")
        for extra in extras:
            zf.writestr(extra, "")


def test_probe_zip_index_mode(bmap, tmp_path):
    zpath = tmp_path / "deep-20260226-114923.zip"
    _make_zip_with_session(zpath, "deep-20260226-114923")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert rec.has_state_json is True
    assert rec.archived is True
    assert rec.stages["run"].status == "done"
    assert rec.stages["reflect_summary"].status == "done"
    assert rec.cohort_count_on_disk == 1
    assert rec.convergence.verdict == "clean_first_pass"


def test_probe_zip_deep_mode(bmap, tmp_path):
    zpath = tmp_path / "deep-20260226-114924.zip"
    _make_zip_with_session(zpath, "deep-20260226-114924")
    original = zpath.stat().st_size
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=True)
    assert rec.size_bytes == original  # compressed size preserved
    assert rec.has_state_json is True
    assert rec.stages["diagnose"].status == "done"


def test_probe_zip_no_state(bmap, tmp_path):
    zpath = tmp_path / "adhoc-20260124-1524.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("framework_test/fastapi/main.py", "code")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert rec.has_state_json is False
    assert rec.convergence.verdict == "unknown"


def test_probe_zip_phase12_flat_layout(bmap, tmp_path):
    """adhoc-style or phase12 zip with flat out/<repo>/ layout."""
    zpath = tmp_path / "phase12-17-20260221-2133.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("phase12-17/out/repo-a/symbols.txt", "s")
        zf.writestr("phase12-17/out/repo-b/symbols.txt", "s")
    rec = bmap.build_record(zpath)
    rec.kind = "phase12"  # normally classify() would set this
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert rec.stages["run"].status == "done"
    assert rec.stages["diagnose"].status == "not_applicable"


def test_probe_zip_empty(bmap, tmp_path):
    zpath = tmp_path / "deep-20260227-000000.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("empty.txt", "")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert rec.stages["run"].status == "missing"


def test_probe_zip_broken(bmap, tmp_path):
    zpath = tmp_path / "deep-20260228-000000.zip"
    zpath.write_bytes(b"not a zip")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert any("zip unreadable" in a for a in rec.anomalies)


def test_probe_zip_deep_broken(bmap, tmp_path):
    zpath = tmp_path / "deep-20260229-000000.zip"
    zpath.write_bytes(b"still not a zip")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=True)
    assert any("zip extract failed" in a for a in rec.anomalies)


def test_probe_zip_bad_state_json(bmap, tmp_path):
    zpath = tmp_path / "deep-20260230-000000.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("deep-20260230/state.json", "{bad json")
        zf.writestr("deep-20260230/out/cohort-001/iter-001/r/hg.json", "{}")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=False)
    assert any("unreadable" in a for a in rec.anomalies)


def test_probe_zip_deep_multi_toplevel(bmap, tmp_path):
    """Zip with multiple top-level dirs still gets probed (uses tmp root)."""
    zpath = tmp_path / "deep-20260231-000000.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("dir-a/out/cohort-001/iter-001/r/hg.json", "{}")
        zf.writestr("dir-b/out/cohort-002/iter-001/r/hg.json", "{}")
    rec = bmap.build_record(zpath)
    bmap.probe_zip_session(zpath, rec, deep=True)
    # Probe shouldn't crash; run stage may or may not be detected depending on
    # which root the fallback picks — we just assert graceful handling.
    assert rec.archived is True


def test_probe_tarball(bmap, tmp_path):
    tpath = tmp_path / "bakeoff_session_2026-02-04.tar.gz"
    with tarfile.open(tpath, "w:gz") as tf:
        def _add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        _add("session/out/cohort-001/iter-001/r/hg.json", b"{}")
        _add("session/diag/cohort-001/iter-001/report.yaml", b"[]")
    rec = bmap.build_record(tpath)
    bmap.probe_tarball(tpath, rec)
    assert rec.archived is True
    assert rec.stages["run"].status == "done"
    assert rec.stages["diagnose"].status == "done"


def test_probe_tarball_broken(bmap, tmp_path):
    tpath = tmp_path / "bakeoff_session_bad.tar.gz"
    tpath.write_bytes(b"not a tar")
    rec = bmap.build_record(tpath)
    bmap.probe_tarball(tpath, rec)
    assert any("tar unreadable" in a for a in rec.anomalies)


# ---------------------------------------------------------------------------
# Walking and filtering
# ---------------------------------------------------------------------------


@pytest.fixture()
def populated_root(tmp_path, modern_deep_session, early_broad_session, phase12_session):
    # modern_deep_session / early_broad_session / phase12_session all live in tmp_path
    # Add deprecated/ which must be skipped
    (tmp_path / "deprecated").mkdir()
    (tmp_path / "deprecated" / "phase12-14-deprecated-20260120-0516").mkdir()
    # And a stray zip
    zpath = tmp_path / "deep-20260228-000000.zip"
    _make_zip_with_session(zpath, "deep-20260228-000000")
    return tmp_path


def test_walk_root_excludes_deprecated(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    names = [r.name for r in records]
    assert "deprecated" not in names
    assert "phase12-14-deprecated-20260120-0516" not in names
    assert "deep-20260409-054615" in names
    assert "broad-20260124-001510" in names
    assert "phase12-01-20260105-1707" in names
    assert "deep-20260228-000000.zip" in names


def test_walk_root_sorted_by_timestamp(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    timestamps = [r.timestamp for r in records if r.timestamp]
    assert timestamps == sorted(timestamps)


def test_walk_root_unrecognized_file(bmap, tmp_path):
    (tmp_path / "random.txt").write_text("garbage")
    records = bmap.walk_root(tmp_path, deep_zips=False)
    assert len(records) == 1
    assert any("unrecognized" in a for a in records[0].anomalies)


def test_filters_by_kind(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    args = bmap.parse_args(["--kind", "deep"])
    filtered = bmap.apply_filters(records, args)
    assert {r.kind for r in filtered} == {"deep"}


def test_filters_by_only(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    args = bmap.parse_args(["--only", "broad-20260124-001510"])
    filtered = bmap.apply_filters(records, args)
    assert len(filtered) == 1
    assert filtered[0].name == "broad-20260124-001510"


def test_filters_by_date_range(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    args = bmap.parse_args(["--since", "2026-02-01", "--until", "2026-04-30"])
    filtered = bmap.apply_filters(records, args)
    assert all(r.timestamp >= "2026-02-01" for r in filtered)
    assert not any(r.name.startswith("phase12") for r in filtered)


def test_filters_missing_only(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    args = bmap.parse_args(["--missing"])
    filtered = bmap.apply_filters(records, args)
    # The modern deep fixture has all stages done => should NOT be in filtered
    assert not any(r.name == "deep-20260409-054615" for r in filtered)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_text(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    out = bmap.render_text(records, populated_root, excluded=["deprecated/"])
    assert "bakeoff-map:" in out
    assert "SUMMARY" in out
    assert "By kind:" in out
    assert "deprecated" in out  # excluded notice
    assert "deep-20260409-054615" in out


def test_render_text_truncates_long_names(bmap, tmp_path):
    name = "deep-20260301-" + "x" * 50
    (tmp_path / name).mkdir()
    (tmp_path / name / "state.json").write_text(
        json.dumps({"session_id": "q", "iteration": 1, "verdicts": []})
    )
    records = bmap.walk_root(tmp_path, deep_zips=False)
    out = bmap.render_text(records, tmp_path, excluded=[])
    assert "..." in out


def test_render_json(bmap, populated_root):
    records = bmap.walk_root(populated_root, deep_zips=False)
    out = bmap.render_json(records)
    data = json.loads(out)
    assert len(data) >= 3
    assert all("stages" in r for r in data)


def test_fmt_size_all_units(bmap):
    assert bmap._fmt_size(0) == "0B"
    assert bmap._fmt_size(500) == "500B"
    assert bmap._fmt_size(2048) == "2.0K"
    assert bmap._fmt_size(5 * 1024**2) == "5.0M"
    assert bmap._fmt_size(2 * 1024**3) == "2.0G"
    assert bmap._fmt_size(3 * 1024**4) == "3.0T"
    assert bmap._fmt_size(4 * 1024**5) == "4.0P"


def test_fmt_stages_phase12(bmap):
    rec = bmap.SessionRecord(name="p", path="/p", kind="phase12", era="phase12", archived=False, timestamp=None)
    assert bmap._fmt_stages(rec) == "run only"


def test_fmt_stages_dash_when_empty(bmap):
    rec = bmap.SessionRecord(name="x", path="/x", kind="deep", era="modern", archived=False, timestamp=None)
    assert bmap._fmt_stages(rec) == "-"


def test_fmt_stages_partial_star(bmap):
    rec = bmap.SessionRecord(name="x", path="/x", kind="deep", era="modern", archived=False, timestamp=None)
    rec.stages["run"] = bmap.StageInfo(status="done", count=1)
    rec.stages["diagnose"] = bmap.StageInfo(status="partial", count=1)
    s = bmap._fmt_stages(rec)
    assert "run" in s and "diag*" in s


def test_fmt_convergence_unknown_passthrough(bmap):
    rec = bmap.SessionRecord(name="x", path="/x", kind="deep", era="modern", archived=False, timestamp=None)
    rec.convergence = bmap.ConvergenceSummary(verdict="something_odd")
    assert bmap._fmt_convergence(rec) == "something_odd"


def test_fmt_missing_compact_all_missing(bmap):
    rec = bmap.build_record(Path("/fake/deep-postfix-validation"))
    # All stages are initialized to "missing" by build_record
    rec.missing = bmap._derive_missing(rec)
    assert rec.missing  # sanity — there are gaps
    result = bmap._fmt_missing_compact(rec)
    assert result == "all stages missing"


def test_fmt_missing_compact_partial_stages(bmap):
    rec = bmap.build_record(Path("/fake/deep-20260409-054615"))
    rec.stages["run"] = bmap.StageInfo(status="done", count=8)
    rec.stages["diagnose"] = bmap.StageInfo(status="done", count=8)
    rec.stages["reflect_prompts"] = bmap.StageInfo(status="partial", count=6)
    rec.stages["reflect_assessments"] = bmap.StageInfo(status="missing", count=0)
    rec.stages["reflect_summary"] = bmap.StageInfo(status="partial", count=3)
    rec.missing = bmap._derive_missing(rec)
    result = bmap._fmt_missing_compact(rec)
    assert "asmt=NONE" in result
    assert "prompt=6/?" in result
    assert "sum=3/?" in result
    assert "run" not in result  # run is done, shouldn't appear


def test_fmt_missing_compact_only_reflect_missing(bmap):
    rec = bmap.build_record(Path("/fake/broad-20260124-001510"))
    rec.stages["run"] = bmap.StageInfo(status="done", count=1)
    rec.stages["diagnose"] = bmap.StageInfo(status="missing", count=0)
    rec.stages["reflect_prompts"] = bmap.StageInfo(status="missing", count=0)
    rec.stages["reflect_assessments"] = bmap.StageInfo(status="missing", count=0)
    rec.stages["reflect_summary"] = bmap.StageInfo(status="missing", count=0)
    rec.missing = bmap._derive_missing(rec)
    result = bmap._fmt_missing_compact(rec)
    assert "diag=NONE" in result
    assert "prompt=NONE" in result
    # Should NOT say "all stages missing" since run is done
    assert result != "all stages missing"


def test_convergence_special_session_with_verdicts(bmap, tmp_path):
    """Special sessions with deep-style verdicts should get proper convergence."""
    session = tmp_path / "deep-20260306-0400"
    session.mkdir()
    state = {
        "session_id": "sp",
        "iteration": 1,
        "verdicts": [
            {"repo_name": "a", "verdict": "GOOD", "concerns": [], "metrics": {}},
            {"repo_name": "b", "verdict": "WARN", "concerns": ["BAD"], "metrics": {}},
        ],
        "reflect_statuses": [],
    }
    _write_state(session, state)
    _make_cohort_iter(session, 1, 1, "a")
    rec = bmap.build_record(session)
    assert rec.kind == "special"  # non-standard name
    bmap.probe_live_session(session, rec)
    # Should assess convergence via verdicts, not return n/a
    assert rec.convergence.verdict == "not_converged"
    assert "1/2 not GOOD" in rec.convergence.detail


def test_convergence_special_session_with_conv_history(bmap, tmp_path):
    """Special sessions with broad-style convergence_history should get assessed."""
    session = tmp_path / "broad-20260307-post-fixes"
    session.mkdir()
    state = {
        "session_id": "sp2",
        "iteration": 1,
        "convergence_history": [
            {"iteration": 1, "cohort": 1, "critical": 0, "high": 0, "new_issues": 0},
        ],
    }
    _write_state(session, state)
    rec = bmap.build_record(session)
    assert rec.kind == "special"
    bmap.probe_live_session(session, rec)
    assert rec.convergence.verdict == "converged"


def test_missing_section_is_compact_in_render(bmap, tmp_path):
    """Each session with gaps gets exactly one line in the MISSING section."""
    session = tmp_path / "broad-20260124-001510"
    session.mkdir()
    state = {
        "session_id": "b",
        "iteration": 1,
        "convergence_history": [
            {"iteration": 1, "cohort": 1, "critical": 0, "high": 0, "new_issues": 0},
        ],
    }
    _write_state(session, state)
    _make_cohort_metadata(session, 1, ["r"])
    _make_cohort_iter(session, 1, 1, "r")
    records = bmap.walk_root(tmp_path, deep_zips=False)
    out = bmap.render_text(records, tmp_path, excluded=[])
    missing_section = out.split("MISSING STAGES\n")[1].split("\n\n")[0] if "MISSING STAGES" in out else ""
    # Should be exactly one content line (the dashes header + one entry)
    content_lines = [l for l in missing_section.split("\n") if l.strip() and not l.startswith("-")]
    assert len(content_lines) == 1
    assert "broad-20260124-001510" in content_lines[0]


# ---------------------------------------------------------------------------
# Main / CLI
# ---------------------------------------------------------------------------


def test_main_text(bmap, populated_root, capsys):
    rc = bmap.main(["--root", str(populated_root)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "bakeoff-map:" in captured.out
    assert "SUMMARY" in captured.out


def test_main_json(bmap, populated_root, capsys):
    rc = bmap.main(["--root", str(populated_root), "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)


def test_main_missing_root(bmap, tmp_path, capsys):
    rc = bmap.main(["--root", str(tmp_path / "nonexistent")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_main_excluded_only_shown_when_present(bmap, tmp_path, capsys):
    """If deprecated/ doesn't exist, no excluded notice is emitted."""
    rc = bmap.main(["--root", str(tmp_path)])
    assert rc == 0
    assert "Excluded" not in capsys.readouterr().out


def test_main_with_filters(bmap, populated_root, capsys):
    rc = bmap.main(["--root", str(populated_root), "--kind", "deep"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "phase12" not in out.split("SUMMARY")[0]  # no phase12 rows in timeline
