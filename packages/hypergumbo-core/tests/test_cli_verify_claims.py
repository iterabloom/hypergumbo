# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo verify-claims CLI command (ADR-0016 Phase 3)."""
import json
from pathlib import Path

import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.schema import SCHEMA_VERSION


class FakeArgs:
    pass


def _make_behavior_map(nodes, edges):
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": edges}


def test_verify_claims_all_confirmed(tmp_path: Path, capsys) -> None:
    """All claims confirmed when no forbidden boundaries exist."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
                "language": "python", "path": "a.py", "span": {"start_line": 1, "end_line": 5}}],
        edges=[{"src": "python:a.py:1:f:function", "dst": "python:os.py:1:os.listdir:function",
                "type": "calls", "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {"id": "SC-001", "text": "No network sends",
             "constraint": {"boundary": "net_send", "must_not_exist": True}},
            {"id": "SC-002", "text": "No subprocess",
             "constraint": {"boundary": "subprocess", "must_not_exist": True}},
        ]
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "CONFIRMED" in out


def test_verify_claims_violated(tmp_path: Path, capsys) -> None:
    """Returns exit 1 when a claim is violated."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
                "language": "python", "path": "a.py", "span": {"start_line": 1, "end_line": 5}}],
        edges=[{"src": "python:a.py:1:f:function", "dst": "python:sub.py:1:subprocess.run:function",
                "type": "calls", "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {"id": "SC-001", "text": "No subprocess",
             "constraint": {"boundary": "subprocess", "must_not_exist": True}},
        ]
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "VIOLATED" in out


def test_verify_claims_json_output(tmp_path: Path, capsys) -> None:
    """JSON output mode produces valid JSON."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
                "language": "python", "path": "a.py", "span": {"start_line": 1, "end_line": 5}}],
        edges=[],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {"id": "SC-001", "text": "No net",
             "constraint": {"boundary": "net_send", "must_not_exist": True}},
        ]
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True

    rc = cmd_verify_claims(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["verdict"] == "confirmed"


def test_verify_claims_missing_file(tmp_path: Path) -> None:
    """Returns error for missing claims file."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.claims = str(tmp_path / "nonexistent.yaml")
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 1


def test_verify_claims_missing_input(tmp_path: Path) -> None:
    """Returns error for missing/invalid input file."""
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text("claims:\n  - id: SC-001\n    text: test\n    constraint:\n      boundary: net_send\n      must_not_exist: true\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent_input.json")
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 1


def test_verify_claims_empty(tmp_path: Path, capsys) -> None:
    """No claims in file → exit 0."""
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text("claims: []\n")

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 0


def test_verify_claims_objc_catalog_bridging(tmp_path: Path, capsys) -> None:
    """Verify-claims detects ObjC I/O despite 'objective-c' / 'objc' mismatch.

    Nodes use language='objective-c' but symbol IDs prefix with 'objc:'.
    The catalog loading must bridge this so verify-claims can detect violations.
    """
    bmap = _make_behavior_map(
        nodes=[{
            "id": "objc:src/Cleanup.m:1-5:Cleanup.run:method",
            "name": "Cleanup.run",
            "kind": "method",
            "language": "objective-c",
            "path": "src/Cleanup.m",
            "span": {"start_line": 1, "end_line": 5},
        }],
        edges=[{
            "src": "objc:src/Cleanup.m:1-5:Cleanup.run:method",
            "dst": "objc:external:0-0:removeItemAtPath:error::unresolved",
            "type": "calls",
            "confidence": 0.5,
        }],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [{
            "id": "SC-001",
            "text": "No filesystem writes",
            "constraint": {"boundary": "fs_write", "must_not_exist": True},
        }],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True

    rc = cmd_verify_claims(args)
    # Should FAIL because ObjC fs_write was detected
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    violated = [r for r in data if r["verdict"] == "violated"]
    assert len(violated) == 1
