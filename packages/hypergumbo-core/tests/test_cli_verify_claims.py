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
        # A realistic analyzed repo produces call edges; without any, the
        # WI-kajil coverage gate would (correctly) report inconclusive.
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:b.py:1:g:function", "type": "calls",
                "confidence": 0.9}],
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


def test_verify_claims_inconclusive_exits_2(tmp_path: Path, capsys) -> None:
    """ADR-0033 Phase 3 PR4 / WI-rolol sub-task A: a claim without a
    machine-checkable constraint returns ``inconclusive`` and the CLI
    exits with code 2 (distinguishing it from confirmed=0 / violated=1).

    Exercises the inconclusive icon ("?"), the per-verdict counter, the
    summary line, and the exit-code-2 path.
    """
    bmap = _make_behavior_map(nodes=[], edges=[])
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    # A claim with NO must_not_exist, NO max_chains, NO taint_flow:
    # the verify-claims machinery has no constraint to check, which
    # post-Phase-3-PR4 returns inconclusive (was a silent confirm).
    claims = {
        "claims": [
            {"id": "SC-001", "text": "No machine-checkable constraint",
             "constraint": {"boundary": "fs_read"}},
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
    assert rc == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert "?" in out


def test_verify_claims_typescript_alias_catalog_bridging(tmp_path: Path, capsys) -> None:
    """Verify-claims detects TS I/O via 'typescript'→'javascript' catalog alias.

    Exercises the catalog secondary-keying branch when Symbol.language=
    'typescript' but the catalog loaded is the shared JavaScript catalog
    (catalog.language='javascript' ≠ lang). Was previously the
    'objective-c' alias case before the objc language-tag harmonization.
    """
    bmap = _make_behavior_map(
        nodes=[{
            "id": "typescript:src/cleanup.ts:1-5:cleanup:function",
            "name": "cleanup",
            "kind": "function",
            "language": "typescript",
            "path": "src/cleanup.ts",
            "span": {"start_line": 1, "end_line": 5},
        }],
        edges=[{
            "src": "typescript:src/cleanup.ts:1-5:cleanup:function",
            "dst": "javascript:external:0-0:fs.unlinkSync:unresolved",
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


def test_verify_claims_taint_flow_violated(tmp_path: Path, capsys) -> None:
    """Taint-flow claim violated when plaintext reaches host_fs zone."""
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            # handler calls Fernet.decrypt (taint source)
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:Fernet.decrypt:unresolved",
             "type": "calls", "confidence": 0.9},
            # handler calls write_text (taint sink - host_fs)
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:write_text:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-001",
                "text": "Plaintext must not reach host filesystem",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "plaintext",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True

    rc = cmd_verify_claims(args)
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data[0]["verdict"] == "violated"
    assert data[0]["evidence_count"] >= 1
    assert "approximate" in data[0]["details"]


def test_verify_claims_taint_flow_confirmed(tmp_path: Path, capsys) -> None:
    """Taint-flow claim confirmed when sanitizer is on the path."""
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
            {"id": "python:app.py:20-30:store:function", "name": "store",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 20, "end_line": 30}},
        ],
        edges=[
            # handler calls Fernet.decrypt (taint source)
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:Fernet.decrypt:unresolved",
             "type": "calls", "confidence": 0.9},
            # handler calls store
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:app.py:20-30:store:function",
             "type": "calls", "confidence": 0.9},
            # store calls Fernet.encrypt (sanitizer)
            {"src": "python:app.py:20-30:store:function",
             "dst": "python:external:0-0:Fernet.encrypt:unresolved",
             "type": "calls", "confidence": 0.9},
            # store calls write_text (taint sink)
            {"src": "python:app.py:20-30:store:function",
             "dst": "python:external:0-0:write_text:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-001",
                "text": "Plaintext must not reach host filesystem",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "plaintext",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
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


def test_verify_claims_taint_no_sources(tmp_path: Path, capsys) -> None:
    """Taint-flow claim confirmed when no taint sources found for languages."""
    bmap = _make_behavior_map(
        nodes=[
            {"id": "haskell:Main.hs:1-10:main:function", "name": "main",
             "kind": "function", "language": "haskell", "path": "Main.hs",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            {"src": "haskell:Main.hs:1-10:main:function",
             "dst": "haskell:external:0-0:putStrLn:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-001",
                "text": "No plaintext to disk",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "plaintext",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
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


# ============================================================================
# INV-javam: taint-flow surfaces languages it can't verify
# ============================================================================


def test_verify_claims_notice_for_unsupported_taint_language(
    tmp_path: Path, capsys,
) -> None:
    """INV-javam: when a repo has taint-flow claims but the language has
    no sources/sinks in the taint catalog, stderr carries an explicit
    notice. Otherwise 'confirmed' is misleading — the language wasn't
    actually analyzed.
    """
    # Brainfuck has no taint catalog entries whatsoever; a claim against
    # a repo in that language will trivially 'confirm' without the notice.
    bmap = _make_behavior_map(
        nodes=[
            {"id": "brainfuck:m.bf:1:main:function", "name": "main",
             "kind": "function", "language": "brainfuck", "path": "m.bf",
             "span": {"start_line": 1, "end_line": 5}},
        ],
        edges=[],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-001",
                "text": "No secrets to disk",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "secret",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    # Verdict is still "confirmed" (no taint findings) but the notice
    # must be present so humans don't misread the verdict as a pass.
    assert rc == 0
    _, err = capsys.readouterr()
    assert "brainfuck" in err
    assert "no taint-flow catalog" in err
    assert "NOT actually verified" in err
    assert "INV-javam" in err


def test_verify_claims_no_notice_when_no_taint_claims(
    tmp_path: Path, capsys,
) -> None:
    """INV-javam anti-regression: the taint-flow notice only fires when
    taint claims are actually evaluated. Pure boundary claims on an
    unsupported language shouldn't trigger it.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "brainfuck:m.bf:1:main:function", "name": "main",
             "kind": "function", "language": "brainfuck", "path": "m.bf",
             "span": {"start_line": 1, "end_line": 5}},
        ],
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
    args.json_output = False

    rc = cmd_verify_claims(args)
    # WI-kajil: brainfuck has no I/O catalog and produced no call edges, so the
    # boundary claim is INCONCLUSIVE (rc=2) — the analysis cannot see its I/O.
    # This test's point is the absence of the *taint-flow* notice (there are no
    # taint claims), which holds regardless of the boundary verdict.
    assert rc == 2
    _, err = capsys.readouterr()
    assert "no taint-flow catalog" not in err


def test_verify_claims_no_notice_when_taint_language_supported(
    tmp_path: Path, capsys,
) -> None:
    """INV-javam anti-regression: don't fire the notice when every
    detected language has taint-catalog coverage (no false alarm
    on fully-supported codebases).
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:a.py:1:f:function", "name": "f",
             "kind": "function", "language": "python", "path": "a.py",
             "span": {"start_line": 1, "end_line": 5}},
        ],
        edges=[],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-001",
                "text": "No plaintext to disk",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "plaintext",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
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
    _, err = capsys.readouterr()
    assert "no taint-flow catalog" not in err


# ---------------------------------------------------------------------------
# WI-votan: --taint-sources / --taint-sinks / --taint-sanitizers CLI flags +
# extra_catalogs: claims-file key
# ---------------------------------------------------------------------------


def test_verify_claims_cli_taint_sources_flag_wires_user_source(
    tmp_path: Path, capsys,
) -> None:
    """An ``--taint-sources`` path that declares a new taint label on an
    existing module function makes a constraint on that label verifiable
    end-to-end through cmd_verify_claims.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            # handler reads from myapp.config.get_secret (the user-declared
            # source) and writes to pathlib.Path.write_text (auto-derived
            # sink in zone=host_fs).
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:myapp.config.get_secret:unresolved",
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:pathlib.Path.write_text:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    # User source YAML: label a project-specific secret-fetcher as
    # carrying the new ``project_secret`` taint label.
    user_src = tmp_path / "project_sources.yaml"
    user_src.write_text(
        'description: "Project secrets"\n'
        "taint_label: project_secret\n"
        "sources:\n"
        "  python:\n"
        "    - module: myapp.config\n"
        "      functions: [get_secret]\n"
        "      return_tainted: true\n"
    )

    claims = {
        "claims": [
            {
                "id": "TF-VOTAN-1",
                "text": "Project secrets must not reach host_fs",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "project_secret",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True
    args.taint_sources = [str(user_src)]

    rc = cmd_verify_claims(args)
    assert rc == 1  # violation detected via user-declared source
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data[0]["verdict"] == "violated"
    # Visibility: summary line to stderr names the counts so the user
    # knows the override took effect.
    assert "Loaded project-local taint catalog" in err
    assert "1 source path(s)" in err


def test_verify_claims_extra_catalogs_claims_file_key(
    tmp_path: Path, capsys,
) -> None:
    """The claims YAML may declare extra catalog paths under a top-level
    ``extra_catalogs:`` key; paths resolve relative to the claims file.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:myapp.config.get_secret:unresolved",
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:pathlib.Path.write_text:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    user_src = tmp_path / "project_sources.yaml"
    user_src.write_text(
        'description: "Project secrets"\n'
        "taint_label: project_secret\n"
        "sources:\n"
        "  python:\n"
        "    - module: myapp.config\n"
        "      functions: [get_secret]\n"
        "      return_tainted: true\n"
    )

    claims_dir = tmp_path / "security"
    claims_dir.mkdir()
    claims_file = claims_dir / "claims.yaml"
    claims_file.write_text(
        "extra_catalogs:\n"
        "  sources: [../project_sources.yaml]\n"
        "claims:\n"
        "  - id: TF-VOTAN-2\n"
        "    text: project_secret must not reach host_fs\n"
        "    constraint:\n"
        "      taint_flow:\n"
        "        source_taint: project_secret\n"
        "        prohibited_sink_zone: host_fs\n"
    )

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True

    rc = cmd_verify_claims(args)
    assert rc == 1
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data[0]["verdict"] == "violated"
    assert "Loaded project-local taint catalog" in err


def test_verify_claims_bad_taint_source_path_errors(
    tmp_path: Path, capsys,
) -> None:
    """A typo in ``--taint-sources`` is caught at load time and returns
    exit 2 (inconclusive) with a clear error — a silent fallthrough to
    built-in defaults would be worse than failing loudly. INV-nufob: a
    broken taint config means verification could not proceed, which is
    inconclusive (exit 2), never a confirmed (0) or violated (1) verdict.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:Fernet.decrypt:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {
                "id": "TF-VOTAN-3",
                "text": "plaintext must not reach host_fs",
                "constraint": {
                    "taint_flow": {
                        "source_taint": "plaintext",
                        "prohibited_sink_zone": "host_fs",
                    },
                },
            },
        ],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    args.taint_sources = [str(tmp_path / "nonexistent_sources.yaml")]

    rc = cmd_verify_claims(args)
    assert rc == 2
    _, err = capsys.readouterr()
    assert "Taint catalog path not found" in err


def _boundary_only_claims_file(tmp_path: Path) -> Path:
    """A claims file with a single boundary (non-taint_flow) constraint."""
    claims = {
        "claims": [
            {"id": "SC-NET", "text": "No network sends",
             "constraint": {"boundary": "net_send", "must_not_exist": True}},
        ]
    }
    p = tmp_path / "claims.yaml"
    p.write_text(yaml.dump(claims))
    return p


def _covered_python_map(tmp_path: Path) -> Path:
    """A behavior map with a python call edge (coverage complete) and no
    net_send boundary."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f",
                "kind": "function", "language": "python", "path": "a.py",
                "span": {"start_line": 1, "end_line": 5}}],
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:os.py:1:os.listdir:function",
                "type": "calls", "confidence": 0.9}],
    )
    p = tmp_path / "hg.json"
    p.write_text(json.dumps(bmap))
    return p


def test_verify_claims_bad_taint_path_with_boundary_only_claims_not_silent(
    tmp_path: Path, capsys,
) -> None:
    """INV-nufob silent-confirm closure: ``--taint-sources <bad-path>`` with
    only boundary (non-taint_flow) claims must NOT silently report
    'all CONFIRMED' exit 0. The flags are validated regardless of whether any
    claim has a taint_flow constraint, so a typo errors out (exit 2).
    """
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(_covered_python_map(tmp_path))
    args.claims = str(_boundary_only_claims_file(tmp_path))
    args.json_output = False
    args.taint_sources = [str(tmp_path / "nonexistent.yaml")]

    rc = cmd_verify_claims(args)
    assert rc == 2
    out, err = capsys.readouterr()
    assert "Taint catalog path not found" in err
    assert "CONFIRMED" not in out


def test_verify_claims_taint_flags_without_taint_claims_warns(
    tmp_path: Path, capsys,
) -> None:
    """INV-nufob: when valid ``--taint-*`` flags are passed but no claim has a
    taint_flow constraint, the catalog is validated but unused — the command
    warns rather than silently ignoring the flags.
    """
    src = tmp_path / "src.yaml"
    src.write_text(
        "taint_label: ut\nsources:\n  python:\n    - module: m\n"
        "      functions: [f]\n"
    )
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(_covered_python_map(tmp_path))
    args.claims = str(_boundary_only_claims_file(tmp_path))
    args.json_output = False
    args.taint_sources = [str(src)]

    rc = cmd_verify_claims(args)
    # Boundary claim confirmed (exit 0) but with an explicit warning.
    assert rc == 0
    _, err = capsys.readouterr()
    assert "no claim has a taint_flow constraint" in err
    assert "validated but" in err


def test_verify_claims_malformed_taint_yaml_clean_error(
    tmp_path: Path, capsys,
) -> None:
    """INV-nufob: a malformed-YAML ``--taint-sources`` file (with a taint
    claim, so the loader runs) yields a clean exit-2 error, not an uncaught
    yaml.YAMLError traceback.
    """
    bad = tmp_path / "broken.yaml"
    bad.write_text("sources: [unclosed\n")
    claims = {
        "claims": [
            {"id": "TF", "text": "no plaintext to host_fs",
             "constraint": {"taint_flow": {"source_taint": "plaintext",
                                           "prohibited_sink_zone": "host_fs"}}},
        ]
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(_covered_python_map(tmp_path))
    args.claims = str(claims_file)
    args.json_output = False
    args.taint_sources = [str(bad)]

    rc = cmd_verify_claims(args)
    assert rc == 2
    out, err = capsys.readouterr()
    assert "could not parse" in err
    assert "Traceback" not in err and "Traceback" not in out


def test_verify_claims_wrongshape_taint_yaml_clean_error(
    tmp_path: Path, capsys,
) -> None:
    """INV-nufob: a wrong-shape ``--taint-sources`` file (sources is a scalar,
    with a taint claim) yields a clean exit-2 error, not an uncaught
    AttributeError traceback.
    """
    bad = tmp_path / "wrong.yaml"
    bad.write_text("sources: not_a_mapping\n")
    claims = {
        "claims": [
            {"id": "TF", "text": "no plaintext to host_fs",
             "constraint": {"taint_flow": {"source_taint": "plaintext",
                                           "prohibited_sink_zone": "host_fs"}}},
        ]
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(_covered_python_map(tmp_path))
    args.claims = str(claims_file)
    args.json_output = False
    args.taint_sources = [str(bad)]

    rc = cmd_verify_claims(args)
    assert rc == 2
    out, err = capsys.readouterr()
    assert "must be a" in err
    assert "Traceback" not in err and "Traceback" not in out


# ============================================================================
# Phase 3: per-entry-point DDG path + per-language propagation
# ============================================================================


def test_verify_claims_python_ddg_path_fires(tmp_path: Path) -> None:
    """When the path contains Python source with assignments, the DDG
    helper builds non-empty edges and verify-claims routes Python
    propagation through propagate_taint_ddg (covers cli.py:4057)."""
    # A small Python module under the analyzed path so the DDG helper
    # discovers a function with an assignment.
    (tmp_path / "mod.py").write_text(
        "def f(x):\n    y = x + 1\n    return y\n", encoding="utf-8",
    )
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:mod.py:1-3:f:function", "name": "f",
             "kind": "function", "language": "python", "path": "mod.py",
             "span": {"start_line": 1, "end_line": 3}},
        ],
        edges=[
            {"src": "python:mod.py:1-3:f:function",
             "dst": "python:external:0-0:plaintext_decrypt:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [{
            "id": "TF-DDG",
            "text": "no plaintext to host_fs",
            "constraint": {
                "taint_flow": {
                    "source_taint": "plaintext",
                    "prohibited_sink_zone": "host_fs",
                },
            },
        }],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    # Ensure Python def/use extractor is registered (sibling tests may
    # have cleared it).
    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import (
            PythonDefUseExtractor,
        )
        register_def_use_extractor("python")(PythonDefUseExtractor)

    # Exit code depends on whether the synthetic edge reaches a host_fs
    # sink — either way the DDG branch in cmd_verify_claims executes.
    rc = cmd_verify_claims(args)
    assert rc in (0, 1)


def test_verify_claims_invokes_refine_external_edges(tmp_path: Path) -> None:
    """WI-dilih: when the analyzed path contains Python source whose
    DDG can resolve a method-call receiver to a module, the per-language
    propagation loop invokes ``refine_external_edges`` on the Python
    edge slice before propagation (covers cli.py:4101).

    The .py fixture has an `os.environ` assignment followed by a
    ``.get()`` call, so ``hints_by_caller`` is non-empty and the
    refinement is wired through.
    """
    (tmp_path / "mod.py").write_text(
        "import os\n"
        "def f():\n"
        "    x = os.environ\n"
        "    return x.get('FOO')\n",
        encoding="utf-8",
    )
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:mod.py:2-4:f:function", "name": "f",
             "kind": "function", "language": "python", "path": "mod.py",
             "span": {"start_line": 2, "end_line": 4}},
        ],
        edges=[
            {"src": "python:mod.py:2-4:f:function",
             "dst": "python:external:0-0:get:unresolved",
             "type": "calls", "confidence": 0.4, "line": 4},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [{
            "id": "TF-REFINE",
            "text": "untrusted input must not reach host_fs",
            "constraint": {
                "taint_flow": {
                    "source_taint": "untrusted_input",
                    "prohibited_sink_zone": "host_fs",
                },
            },
        }],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import (
            PythonDefUseExtractor,
        )
        register_def_use_extractor("python")(PythonDefUseExtractor)

    rc = cmd_verify_claims(args)
    assert rc in (0, 1)


def test_verify_claims_skips_language_with_only_sinks(
    tmp_path: Path, capsys,
) -> None:
    """When a language has sinks declared but no sources, the
    per-language loop continues past it (covers cli.py:4055)."""
    # Provide a project-local sink for a language that auto-derive
    # doesn't supply sources for (or where the auto-derived sources
    # don't intersect this map's nodes). Java is a candidate: the
    # behavior map contains Java edges but project-local sinks
    # without project-local sources leave Java with only sinks for
    # this run.
    sinks_file = tmp_path / "java_sinks.yaml"
    sinks_file.write_text(
        "zone: custom_zone\n"
        "trust_level: untrusted\n"
        "sinks:\n"
        "  shellscript:\n"
        "    - module: my.pkg\n"
        "      functions: [doStuff]\n",
        encoding="utf-8",
    )

    bmap = _make_behavior_map(
        nodes=[
            {"id": "shellscript:Main.sh:1-10:main:function", "name": "main",
             "kind": "function", "language": "shellscript",
             "path": "Main.sh",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[
            {"src": "shellscript:Main.sh:1-10:main:function",
             "dst": "shellscript:my.pkg:0-0:doStuff:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [{
            "id": "TF-empty-source",
            "text": "shellscript should not write to custom_zone",
            "constraint": {
                "taint_flow": {
                    "source_taint": "plaintext",
                    "prohibited_sink_zone": "custom_zone",
                },
            },
        }],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    args.taint_sinks = [str(sinks_file)]

    rc = cmd_verify_claims(args)
    # shellscript has no taint sources in the auto-derived catalog →
    # per-language loop hits the `continue` branch (cli.py:4055) →
    # no propagation runs for shellscript → claim confirmed vacuously.
    assert rc == 0


def _load_error_args(tmp_path: Path, claims_text: str) -> "FakeArgs":
    """Build args for a claims file that should fail load_claims validation.

    The malformed claims short-circuit before analysis, so no behavior_map
    input is required — but every attribute cmd_verify_claims reads before
    load_claims (path, claims) must be present.
    """
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(claims_text)
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "hg.json")
    args.claims = str(claims_file)
    args.json_output = False
    return args


def test_verify_claims_malformed_yaml_exits_2(tmp_path: Path, capsys) -> None:
    """INV-zurih: malformed YAML claims → rc=2 + clean stderr, no traceback."""
    args = _load_error_args(tmp_path, "not: valid: yaml: [\n")
    rc = cmd_verify_claims(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "Error" in err
    assert "Traceback" not in err


def test_verify_claims_list_root_exits_2(tmp_path: Path, capsys) -> None:
    """WI-fuhaf: a top-level list (no 'claims:' key) → rc=2, not AttributeError."""
    args = _load_error_args(tmp_path, "- id: SC-1\n  text: x\n")
    rc = cmd_verify_claims(args)
    assert rc == 2
    assert "Error" in capsys.readouterr().err


def test_verify_claims_unknown_boundary_exits_2(tmp_path: Path, capsys) -> None:
    """WI-ruzib / INV-gobob: unknown boundary value → rc=2 instead of a
    silent 'confirmed' verdict at rc=0."""
    args = _load_error_args(
        tmp_path,
        "claims:\n  - id: BANANA\n    text: x\n"
        "    constraint:\n      boundary: banana\n      must_not_exist: true\n",
    )
    rc = cmd_verify_claims(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "banana" in err


def test_verify_claims_unknown_field_exits_2(tmp_path: Path, capsys) -> None:
    """WI-bopoz: typo'd field name → rc=2 instead of a silently-dropped field."""
    args = _load_error_args(
        tmp_path,
        "claims:\n  - id: SC-1\n    text: x\n    constrant: {}\n",
    )
    rc = cmd_verify_claims(args)
    assert rc == 2
    assert "Error" in capsys.readouterr().err


def _boundary_claim_args(tmp_path: Path, bmap: dict) -> "FakeArgs":
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "NO_NET", "text": "no network",
         "constraint": {"boundary": "net_send", "must_not_exist": True}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    return args


def _node(symbol_id: str, lang: str, path: str):
    return {"id": symbol_id, "name": symbol_id.split(":")[-2], "kind": "function",
            "language": lang, "path": path,
            "span": {"start_line": 1, "end_line": 5}}


def test_verify_claims_blind_supported_language_inconclusive(
    tmp_path: Path, capsys,
) -> None:
    """WI-kajil P0: a supported language analyzed but producing zero call edges
    makes a must_not_exist boundary claim INCONCLUSIVE (rc=2), not a silent
    'confirmed' at rc=0 against an analysis blind to that language's I/O."""
    bmap = _make_behavior_map(
        nodes=[
            _node("python:a.py:1:f:function", "python", "a.py"),
            _node("javascript:b.js:1:g:function", "javascript", "b.js"),
        ],
        # python produced a (non-IO) call edge; javascript produced none.
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:helpers.py:1:helper:function",
                "type": "calls", "confidence": 0.9}],
    )
    rc = cmd_verify_claims(_boundary_claim_args(tmp_path, bmap))
    assert rc == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out


def test_verify_claims_covered_languages_confirm(tmp_path: Path, capsys) -> None:
    """Control: when every supported language produced call edges, a clean
    net_send claim still CONFIRMS (rc=0) — the coverage gate is not blanket."""
    bmap = _make_behavior_map(
        nodes=[
            _node("python:a.py:1:f:function", "python", "a.py"),
            _node("javascript:b.js:1:g:function", "javascript", "b.js"),
        ],
        edges=[
            {"src": "python:a.py:1:f:function",
             "dst": "python:helpers.py:1:helper:function",
             "type": "calls", "confidence": 0.9},
            {"src": "javascript:b.js:1:g:function",
             "dst": "javascript:util.js:1:util:function",
             "type": "calls", "confidence": 0.9},
        ],
    )
    rc = cmd_verify_claims(_boundary_claim_args(tmp_path, bmap))
    assert rc == 0
    assert "CONFIRMED" in capsys.readouterr().out


def test_verify_claims_vacuous_analysis_inconclusive(tmp_path: Path, capsys) -> None:
    """INV-bitig P0: an analysis that produced no call edges at all (empty /
    wrong-cwd) cannot confirm must_not_exist — INCONCLUSIVE (rc=2)."""
    bmap = _make_behavior_map(
        nodes=[_node("python:a.py:1:f:function", "python", "a.py")],
        edges=[],
    )
    rc = cmd_verify_claims(_boundary_claim_args(tmp_path, bmap))
    assert rc == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_verify_claims_cli_source_overrides_claims_file_source(
    tmp_path: Path, capsys,
) -> None:
    """INV-hukug end-to-end: a --taint-sources flag overriding a source the
    claims file declares via extra_catalogs.sources must REPLACE it. The claim
    checks for the claims-file label; once the CLI relabels that source, the
    label no longer seeds taint and the claim flips violated -> confirmed.
    Pre-fix, both labels coexisted and the claim stayed violated.
    """
    # entry (source) calls writeit (sink). dispatcher calls entry so entry is
    # the source-callee (start_at: callee seeds BFS at entry).
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:cli.py:1:dispatcher:function", "name": "dispatcher",
             "kind": "function", "language": "python", "path": "cli.py",
             "span": {"start_line": 1, "end_line": 2}},
            {"id": "python:api.py:1:entry:function", "name": "entry",
             "kind": "function", "language": "python", "path": "api.py",
             "span": {"start_line": 1, "end_line": 9}},
        ],
        edges=[
            {"src": "python:cli.py:1:dispatcher:function",
             "dst": "python:api.py:1:entry:function", "type": "calls",
             "confidence": 0.9},
            {"src": "python:api.py:1:entry:function",
             "dst": "python:external:0-0:writeit:unresolved", "type": "calls",
             "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    (tmp_path / "claims_src.yaml").write_text(yaml.dump({
        "taint_label": "claims_label",
        "sources": {"python": [
            {"module": "myproj.api", "start_at": "callee", "functions": ["entry"]},
        ]},
    }))
    (tmp_path / "sink.yaml").write_text(yaml.dump({
        "zone": "Z", "trust_level": "untrusted",
        "sinks": {"python": [{"module": "myproj.io", "functions": ["writeit"]}]},
    }))
    (tmp_path / "cli_src.yaml").write_text(yaml.dump({
        "taint_label": "cli_label",
        "sources": {"python": [
            {"module": "myproj.api", "start_at": "callee", "functions": ["entry"]},
        ]},
    }))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({
        "extra_catalogs": {"sources": ["claims_src.yaml"], "sinks": ["sink.yaml"]},
        "claims": [{
            "id": "TF", "text": "no claims_label to Z",
            "constraint": {"taint_flow": {
                "source_taint": "claims_label", "prohibited_sink_zone": "Z",
            }},
        }],
    }))

    base = FakeArgs()
    base.path = str(tmp_path)
    base.input = str(input_file)
    base.claims = str(claims_file)
    base.json_output = True

    # Baseline (no CLI override): claims_label seeds entry -> flow to Z -> violated.
    rc = cmd_verify_claims(base)
    assert rc == 1
    assert json.loads(capsys.readouterr().out)[0]["verdict"] == "violated"

    # With CLI override: entry is relabeled cli_label, so claims_label no longer
    # seeds -> the claims_label claim is confirmed (the override displaced it).
    over = FakeArgs()
    over.path = str(tmp_path)
    over.input = str(input_file)
    over.claims = str(claims_file)
    over.json_output = True
    over.taint_sources = [str(tmp_path / "cli_src.yaml")]
    rc = cmd_verify_claims(over)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["verdict"] == "confirmed"
