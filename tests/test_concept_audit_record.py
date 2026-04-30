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

    with patch.object(record, "STATE_FILE", state_path), \
         patch.object(record, "REPO_ROOT", tmp_path):
        rc = record.main(["concept-audit-record", "Edge.edge_type"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Edge.edge_type" in out
    assert state_path.exists()
