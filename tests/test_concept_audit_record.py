# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/concept-audit-record``.

Named to match the script basename (with hyphens normalized to
underscores) so ``top_level_test_map.py`` selects this file when the
script changes — per-PR smart-test runs these tests automatically.

Companion tests for ``.agent/hooks/_shared/check_audit_cadence.py``
live in ``test_check_audit_cadence.py``; the two files share helpers
by duplication (each test file is independently runnable).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = REPO_ROOT / "scripts" / "concept-audit-record"


def _load(path: Path, name: str) -> Any:
    """Load a Python source file as a module regardless of extension."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


record = _load(RECORD_PATH, "concept_audit_record")


# --- helpers (duplicated from test_check_audit_cadence.py for
#     independent runnability per the test_on_transcript_change.py pattern) ---

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=path, check=True,
    )


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    fpath = path / filename
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content)
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=path, check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


# --- head_sha ---

def test_head_sha_returns_current_head(tmp_path: Path):
    _init_repo(tmp_path)
    expected = _commit(tmp_path, "a.txt", "1", "first")
    assert record.head_sha(tmp_path) == expected


# --- now_iso_local ---

def test_now_iso_local_is_iso_8601_string():
    s = record.now_iso_local()
    assert "T" in s  # ISO-8601 separator
    # Tail is either +HH:MM, -HH:MM, or Z (UTC fallback).
    assert s[-6] in ("+", "-") or s.endswith("Z")


# --- record() ---

def test_record_creates_state_file(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"

    state = record.record(
        "Foo.bar", state_path=state_path, repo_root=tmp_path,
    )
    assert state["suspect_domain"] == "Foo.bar"
    assert state["audits_run"] == 1
    assert len(state["last_audit_sha"]) == 40
    saved = json.loads(state_path.read_text())
    assert saved == state


def test_record_increments_audits_run(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"

    record.record("Foo.bar", state_path=state_path, repo_root=tmp_path)
    state2 = record.record(
        "Baz.qux", state_path=state_path, repo_root=tmp_path,
    )
    assert state2["audits_run"] == 2
    assert state2["suspect_domain"] == "Baz.qux"


def test_record_handles_corrupt_prior_state(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not valid json {")

    state = record.record(
        "Foo.bar", state_path=state_path, repo_root=tmp_path,
    )
    # Counter restarts from 1 because prior state was unreadable.
    assert state["audits_run"] == 1


# --- main() ---

def test_main_no_arg_returns_2(capsys):
    rc = record.main(["concept-audit-record"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage:" in err


def test_main_too_many_args_returns_2(capsys):
    rc = record.main(["concept-audit-record", "a", "b"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage:" in err


def test_main_empty_arg_returns_2(capsys):
    rc = record.main(["concept-audit-record", "   "])
    assert rc == 2
    err = capsys.readouterr().err
    assert "non-empty" in err


def test_main_records_and_prints_confirmation(tmp_path: Path, capsys):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    # A findings document is now a precondition for recording (2026-08-27);
    # the notebook root is patched so this never reads the real one.
    nb = tmp_path / "nb"
    nb.mkdir()
    (nb / "concept-audit-edge_edge_type_04292026.md").write_text("# f\n")

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", nb):
        rc = record.main(["concept-audit-record", "Edge.edge_type"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Edge.edge_type" in out
    assert state_path.exists()


# --- WI/audit 2026-08-27: the recorder must refuse to advance the cadence
#     state when the audit left no findings document.
#
#     WHY THIS EXISTS. The 2026-08-20 record advanced `audits_run` for suspect
#     domain "call_construct" with no write-up anywhere and no Examples entry,
#     so the cadence bookkeeping moved while the audit trail did not. The
#     2026-08-27 re-run of the same domain found five off-axis values the
#     earlier pass had left behind — recoverable only because it re-derived the
#     inventory from scratch. A silent audit is no audit; this is the gate.

def _writeup(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_text("# findings\n")
    return p


def test_slug_strips_separators_and_case():
    assert record.slug("call_construct") == "callconstruct"
    assert record.slug("Edge.edge_type") == "edgeedgetype"
    assert record.slug("identity vocabulary") == "identityvocabulary"
    assert record.slug("supply-chain-tier") == "supplychaintier"


def test_findings_documents_matches_lab_notebook_writeup(tmp_path: Path):
    nb = tmp_path / "nb"
    want = _writeup(nb, "concept-audit-call_construct_08272026.md")
    _writeup(nb, "concept-audit-supply_chain_tier_07152026.md")
    found = record.findings_documents(
        "call_construct", repo_root=tmp_path / "repo", notebook_root=nb,
    )
    assert found == [want]


def test_findings_documents_matches_hyphen_underscore_variants(tmp_path: Path):
    """The suspect is spelled with underscores, the file with hyphens."""
    nb = tmp_path / "nb"
    want = _writeup(nb, "concept-audit-identity-vocabulary_07152026.md")
    found = record.findings_documents(
        "identity_vocabulary", repo_root=tmp_path / "repo", notebook_root=nb,
    )
    assert found == [want]


def test_findings_documents_matches_docs_audits(tmp_path: Path):
    repo = tmp_path / "repo"
    want = _writeup(
        repo / "docs" / "audits",
        "0018-symbol-kind-type-family-abstract-predicate.md",
    )
    found = record.findings_documents(
        "symbol_kind type family", repo_root=repo, notebook_root=tmp_path / "nb",
    )
    assert found == [want]


def test_findings_documents_empty_when_nothing_matches(tmp_path: Path):
    nb = tmp_path / "nb"
    _writeup(nb, "concept-audit-supply_chain_tier_07152026.md")
    assert record.findings_documents(
        "call_construct", repo_root=tmp_path / "repo", notebook_root=nb,
    ) == []


def test_findings_documents_tolerates_missing_roots(tmp_path: Path):
    """Neither root existing is the fresh-clone case, not an error."""
    assert record.findings_documents(
        "anything", repo_root=tmp_path / "nope", notebook_root=tmp_path / "gone",
    ) == []


def test_main_refuses_when_no_findings_document(tmp_path: Path, capsys):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", tmp_path / "nb"):
        rc = record.main(["concept-audit-record", "call_construct"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "no findings document" in err.lower()
    # THE POINT OF THE GATE: the state must not advance on a refusal.
    assert not state_path.exists()


def test_refusal_leaves_a_prior_record_untouched(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    before = json.dumps({"audits_run": 12, "suspect_domain": "prior"})
    state_path.write_text(before)

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", tmp_path / "nb"):
        rc = record.main(["concept-audit-record", "call_construct"])

    assert rc == 2
    assert state_path.read_text() == before


def test_main_records_when_a_findings_document_exists(tmp_path: Path, capsys):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    nb = tmp_path / "nb"
    _writeup(nb, "concept-audit-call_construct_08272026.md")

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", nb):
        rc = record.main(["concept-audit-record", "call_construct"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "concept-audit-call_construct_08272026.md" in out
    assert json.loads(state_path.read_text())["suspect_domain"] == "call_construct"


def test_main_force_records_without_a_findings_document(tmp_path: Path, capsys):
    """The escape hatch, for a write-up this script cannot see."""
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", tmp_path / "nb"):
        rc = record.main(["concept-audit-record", "--force", "call_construct"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "--force" in captured.err or "force" in captured.err.lower()
    assert state_path.exists()


def test_main_rejects_an_unknown_flag(capsys):
    rc = record.main(["concept-audit-record", "--nope", "call_construct"])
    assert rc == 2
    assert "usage:" in capsys.readouterr().err


# --- Recency. The first cut of this gate was satisfied by ANY historical
#     document naming the domain, which meant it would have passed the very
#     case it was built for: on 2026-08-20 the April-era
#     docs/audits/0012-evidence-type-cluster-d-call-construct.md already
#     existed. A write-up only witnesses THIS audit if it postdates the last
#     recorded one.

def _age(path: Path, iso: str) -> None:
    """Set a file's mtime from an ISO-8601 timestamp."""
    import datetime as _dt
    ts = _dt.datetime.fromisoformat(iso).timestamp()
    import os
    os.utime(path, (ts, ts))


def test_findings_documents_filters_by_recency(tmp_path: Path):
    nb = tmp_path / "nb"
    old = _writeup(nb, "concept-audit-call_construct_04292026.md")
    _age(old, "2026-04-29T00:00:00+00:00")
    new = _writeup(nb, "concept-audit-call_construct_08272026.md")
    _age(new, "2026-08-27T00:00:00+00:00")

    import datetime as _dt
    cutoff = _dt.datetime.fromisoformat("2026-08-20T00:00:00+00:00")
    got = record.findings_documents(
        "call_construct", repo_root=tmp_path / "repo",
        notebook_root=nb, newer_than=cutoff,
    )
    assert got == [new]


def test_prior_audit_time_reads_the_state_file(tmp_path: Path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(
        {"last_audit_iso_date": "2026-08-20T17:13:46.475918-04:00"}
    ))
    got = record.prior_audit_time(sp)
    assert got is not None and got.year == 2026 and got.month == 8


def test_prior_audit_time_is_none_when_absent_corrupt_or_unparseable(tmp_path: Path):
    assert record.prior_audit_time(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert record.prior_audit_time(bad) is None
    nodate = tmp_path / "nodate.json"
    nodate.write_text(json.dumps({"audits_run": 3}))
    assert record.prior_audit_time(nodate) is None
    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"last_audit_iso_date": "not-a-date"}))
    assert record.prior_audit_time(junk) is None


def test_main_refuses_when_the_only_writeup_predates_the_last_audit(
    tmp_path: Path, capsys,
):
    """THE 2026-08-20 CASE, reproduced."""
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"last_audit_iso_date": "2026-08-20T00:00:00+00:00", "audits_run": 12}
    ))
    nb = tmp_path / "nb"
    stale = _writeup(nb, "concept-audit-call_construct_04292026.md")
    _age(stale, "2026-04-29T00:00:00+00:00")

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", nb):
        rc = record.main(["concept-audit-record", "call_construct"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "no findings document" in err.lower()
    assert json.loads(state_path.read_text())["audits_run"] == 12


def test_main_records_when_the_writeup_postdates_the_last_audit(
    tmp_path: Path, capsys,
):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    state_path = tmp_path / ".agent" / ".last_concept_audit.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"last_audit_iso_date": "2026-08-20T00:00:00+00:00", "audits_run": 12}
    ))
    nb = tmp_path / "nb"
    fresh = _writeup(nb, "concept-audit-call_construct_08272026.md")
    _age(fresh, "2026-08-27T00:00:00+00:00")

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path), \
         patch.object(record, "NOTEBOOK_ROOT", nb):
        rc = record.main(["concept-audit-record", "call_construct"])

    assert rc == 0
    assert json.loads(state_path.read_text())["audits_run"] == 13


def test_findings_documents_accepts_a_naive_cutoff(tmp_path: Path):
    """A state file written without a UTC offset still filters correctly."""
    import datetime as _dt
    nb = tmp_path / "nb"
    old = _writeup(nb, "concept-audit-call_construct_04292026.md")
    _age(old, "2026-04-29T00:00:00+00:00")
    new = _writeup(nb, "concept-audit-call_construct_08272026.md")
    _age(new, "2026-08-27T00:00:00+00:00")

    naive = _dt.datetime(2026, 8, 20)          # no tzinfo
    assert naive.tzinfo is None
    got = record.findings_documents(
        "call_construct", repo_root=tmp_path / "repo",
        notebook_root=nb, newer_than=naive,
    )
    assert got == [new]
