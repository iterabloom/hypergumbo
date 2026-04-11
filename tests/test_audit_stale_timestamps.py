# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/audit-stale-timestamps (WI-sofop V1).

Covers:
  - _parse_iso8601: ``Z`` suffix, explicit offset, unparseable input
  - _run_rule: missing file, missing field, drift, OK path, non-ISO
  - referenced_file_mtime compare mode
  - AuditReport.exit_code priority (drift > missing > ok)
  - _default_rules path layout
  - CLI main: text vs JSON output, --repo-root override
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_audit():
    script_path = str(
        Path(__file__).parent.parent / "scripts" / "audit-stale-timestamps"
    )
    loader = importlib.machinery.SourceFileLoader(
        "audit_stale_timestamps", script_path,
    )
    spec = importlib.util.spec_from_loader(
        "audit_stale_timestamps", loader,
    )
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass can resolve
    # type hints via sys.modules[cls.__module__].__dict__.
    sys.modules["audit_stale_timestamps"] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit_mod():
    return _import_audit()


class TestParseIso8601:
    def test_z_suffix(self, audit_mod) -> None:
        result = audit_mod._parse_iso8601("2026-04-11T10:30:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_explicit_offset(self, audit_mod) -> None:
        result = audit_mod._parse_iso8601("2026-04-11T10:30:00+00:00")
        assert result is not None

    def test_non_string(self, audit_mod) -> None:
        assert audit_mod._parse_iso8601(None) is None
        assert audit_mod._parse_iso8601(42) is None

    def test_unparseable(self, audit_mod) -> None:
        assert audit_mod._parse_iso8601("not-a-date") is None


class TestRunRuleMtimeCompare:
    def _write_state(
        self, tmp_path: Path, payload: dict, filename: str = "state.json",
    ) -> Path:
        p = tmp_path / filename
        p.write_text(json.dumps(payload))
        return p

    def test_missing_file(self, audit_mod, tmp_path: Path) -> None:
        rule = audit_mod.AuditRule(
            name="test",
            path=tmp_path / "nope.json",
            field_name="last_completed_utc",
            compare="mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_file"

    def test_unparseable_json(self, audit_mod, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not json}")
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="last_completed_utc",
            compare="mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_file"
        assert "read/parse failed" in result.detail

    def test_missing_field(self, audit_mod, tmp_path: Path) -> None:
        p = self._write_state(tmp_path, {"something_else": "value"})
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="last_completed_utc",
            compare="mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_field"

    def test_non_iso_field(self, audit_mod, tmp_path: Path) -> None:
        p = self._write_state(
            tmp_path, {"last_completed_utc": "nope"},
        )
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="last_completed_utc",
            compare="mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_field"

    def test_ok_within_threshold(self, audit_mod, tmp_path: Path) -> None:
        now = datetime.now(timezone.utc)
        p = self._write_state(
            tmp_path, {"last_completed_utc": now.isoformat()},
        )
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="last_completed_utc",
            compare="mtime",
            threshold_seconds=3600,
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "ok"

    def test_drift_exceeds_threshold(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=11)
        p = self._write_state(
            tmp_path, {"last_completed_utc": past.isoformat()},
        )
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="last_completed_utc",
            compare="mtime",
            threshold_seconds=3600,
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "drift"
        assert result.drift_seconds is not None
        assert result.drift_seconds > 3600


class TestRunRuleReferencedFileMtimeCompare:
    def test_missing_referenced_file(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        now = datetime.now(timezone.utc)
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "last_completed_utc": now.isoformat(),
            "guidance_file": str(tmp_path / "nope.md"),
        }))
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="guidance_file",
            compare="referenced_file_mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_file"

    def test_missing_last_completed(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        referenced = tmp_path / "guidance.md"
        referenced.write_text("# guidance")
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "guidance_file": str(referenced),
        }))
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="guidance_file",
            compare="referenced_file_mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "missing_field"

    def test_ok_referenced_file_fresh(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        referenced = tmp_path / "guidance.md"
        referenced.write_text("# guidance")
        now = datetime.now(timezone.utc)
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "last_completed_utc": now.isoformat(),
            "guidance_file": str(referenced),
        }))
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="guidance_file",
            compare="referenced_file_mtime",
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "ok"

    def test_drift_guidance_older_than_last_completed(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        referenced = tmp_path / "guidance.md"
        referenced.write_text("# old")
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=11)
        ).timestamp()
        os.utime(referenced, (old_ts, old_ts))

        now = datetime.now(timezone.utc)
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "last_completed_utc": now.isoformat(),
            "guidance_file": str(referenced),
        }))
        rule = audit_mod.AuditRule(
            name="test",
            path=p,
            field_name="guidance_file",
            compare="referenced_file_mtime",
            threshold_seconds=3600,
        )
        result = audit_mod._run_rule(rule)
        assert result.status == "drift"
        assert result.drift_seconds is not None
        assert result.drift_seconds > 3600


class TestAuditReport:
    def test_exit_code_ok(self, audit_mod) -> None:
        report = audit_mod.AuditReport()
        report.results.append(
            audit_mod.AuditResult(
                rule_name="r", path="p", status="ok",
            ),
        )
        assert report.exit_code == 0

    def test_exit_code_missing_file(self, audit_mod) -> None:
        report = audit_mod.AuditReport()
        report.results.append(
            audit_mod.AuditResult(
                rule_name="r", path="p", status="missing_file",
            ),
        )
        assert report.exit_code == 2

    def test_exit_code_drift_wins(self, audit_mod) -> None:
        report = audit_mod.AuditReport()
        report.results.append(
            audit_mod.AuditResult(
                rule_name="r1", path="p1", status="missing_file",
            ),
        )
        report.results.append(
            audit_mod.AuditResult(
                rule_name="r2", path="p2", status="drift",
            ),
        )
        assert report.exit_code == 1

    def test_to_dict(self, audit_mod) -> None:
        report = audit_mod.AuditReport()
        report.results.append(
            audit_mod.AuditResult(
                rule_name="r",
                path="p",
                status="ok",
                drift_seconds=100,
            ),
        )
        d = report.to_dict()
        assert d["exit_code"] == 0
        assert d["results"][0]["drift_seconds"] == 100


class TestDefaultRules:
    def test_rules_built_from_repo_root(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        repo_root = tmp_path / "myrepo"
        repo_root.mkdir()
        rules = audit_mod._default_rules(repo_root)
        assert len(rules) == 2
        # Both rules point at the same state file.
        assert rules[0].path == rules[1].path
        # Path includes the repo-name-derived lab notebook dir.
        assert "myrepo_lab_notebook" in str(rules[0].path)


class TestAuditEntry:
    def test_audit_aggregates_results(
        self, audit_mod, tmp_path: Path,
    ) -> None:
        rule = audit_mod.AuditRule(
            name="missing",
            path=tmp_path / "absent.json",
            field_name="x",
            compare="mtime",
        )
        report = audit_mod.audit([rule])
        assert len(report.results) == 1
        assert report.exit_code == 2


class TestFormatText:
    def test_text_output_includes_status_icons(
        self, audit_mod,
    ) -> None:
        report = audit_mod.AuditReport()
        report.results.append(
            audit_mod.AuditResult(
                rule_name="drift_rule",
                path="/tmp/x.json",
                status="drift",
                detail="drift details",
                drift_seconds=99999,
            ),
        )
        report.results.append(
            audit_mod.AuditResult(
                rule_name="ok_rule",
                path="/tmp/y.json",
                status="ok",
                drift_seconds=100,
            ),
        )
        report.results.append(
            audit_mod.AuditResult(
                rule_name="missing_field_rule",
                path="/tmp/z.json",
                status="missing_field",
                detail="no field",
            ),
        )
        report.results.append(
            audit_mod.AuditResult(
                rule_name="missing_file_rule",
                path="/tmp/w.json",
                status="missing_file",
                detail="file missing",
            ),
        )
        text = audit_mod._format_text(report)
        assert "[DRIFT]" in text
        assert "[OK]" in text
        assert "[MISSING_FIELD]" in text
        assert "[MISSING_FILE]" in text
        assert "Summary: 4 rules checked, 1 drift, 1 missing" in text


class TestCliMain:
    def test_text_mode(
        self, audit_mod, tmp_path: Path, capsys,
    ) -> None:
        # Use a repo-root override that points at an empty dir so
        # default rules resolve to a non-existent state file.
        exit_code = audit_mod.main([
            "--repo-root", str(tmp_path / "ghost"),
        ])
        captured = capsys.readouterr()
        assert "V1 report" in captured.out
        # Exit code is 2 because the state file is missing.
        assert exit_code == 2

    def test_json_mode(
        self, audit_mod, tmp_path: Path, capsys,
    ) -> None:
        exit_code = audit_mod.main([
            "--json",
            "--repo-root", str(tmp_path / "ghost"),
        ])
        captured = capsys.readouterr()
        # Exit code reflected in JSON.
        parsed = json.loads(captured.out)
        assert parsed["exit_code"] == 2
        assert len(parsed["results"]) == 2
        assert exit_code == 2

    def test_ok_exit_code_zero(
        self, audit_mod, tmp_path: Path, capsys,
    ) -> None:
        """Build a synthetic lab notebook layout where everything is fresh
        and verify the CLI exits 0."""
        repo_root = tmp_path / "fresh_repo"
        repo_root.mkdir()
        guidance = Path.home() / "fresh_repo_lab_notebook" / "guidance_log"
        guidance.mkdir(parents=True, exist_ok=True)

        state_path = guidance / "stop_hook_state.json"
        referenced = guidance / "stop_guidance_04112026_1200.md"
        referenced.write_text("# guidance")
        now = datetime.now(timezone.utc)
        state_path.write_text(json.dumps({
            "last_completed_utc": now.isoformat(),
            "guidance_file": str(referenced),
        }))

        try:
            exit_code = audit_mod.main([
                "--repo-root", str(repo_root),
            ])
            assert exit_code == 0
        finally:
            # Clean up synthetic state files so subsequent tests in the
            # real repo aren't polluted.
            state_path.unlink(missing_ok=True)
            referenced.unlink(missing_ok=True)
            try:
                guidance.rmdir()
                guidance.parent.rmdir()
            except OSError:
                pass
