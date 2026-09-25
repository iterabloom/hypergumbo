# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo verify-claims CLI command (ADR-0016 Phase 3)."""
import json
from pathlib import Path

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import VERIFY_CLAIMS_SCHEMA_VERSION


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
    # WI-nulot / INV-gatog: --json is a versioned top-level object (envelope),
    # not a bare array — so metadata can be added without breaking consumers.
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, dict)
    assert data["schema_version"] == VERIFY_CLAIMS_SCHEMA_VERSION
    assert data["view"] == "verify-claims"
    assert len(data["verdicts"]) == 1
    assert data["verdicts"][0]["verdict"] == "confirmed"
    # The machine-facing taint-coverage signal is present (empty here — the
    # single boundary claim is not a taint claim).
    assert data["unsupported_taint_languages"] == []


def _make_json_claims_args(tmp_path: Path) -> FakeArgs:
    """Build a verify-claims FakeArgs over a single-confirmed-claim fixture."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
                "language": "python", "path": "a.py",
                "span": {"start_line": 1, "end_line": 5}}],
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:b.py:1:g:function", "type": "calls",
                "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims = {"claims": [
        {"id": "SC-001", "text": "No net",
         "constraint": {"boundary": "net_send", "must_not_exist": True}},
    ]}
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    return args


def test_verify_claims_format_json_matches_json_flag(
    tmp_path: Path, capsys,
) -> None:
    """WI-kitud: ``--format json`` emits the same versioned envelope as the
    ``--json`` back-compat alias, bringing verify-claims onto the shared
    ``--format text|json`` read-view convention."""
    args = _make_json_claims_args(tmp_path)
    args.format = "json"

    rc = cmd_verify_claims(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == VERIFY_CLAIMS_SCHEMA_VERSION
    assert data["view"] == "verify-claims"
    assert data["verdicts"][0]["verdict"] == "confirmed"


def test_verify_claims_json_alias_overrides_format_text(
    tmp_path: Path, capsys,
) -> None:
    """WI-kitud: the ``--json`` alias forces JSON even when ``--format`` is the
    default ``text`` — back-compat: ``--json`` has always meant JSON."""
    args = _make_json_claims_args(tmp_path)
    args.format = "text"
    args.json_output = True

    rc = cmd_verify_claims(args)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["view"] == "verify-claims"


def test_verify_claims_json_exposes_unsupported_taint_languages(
    tmp_path: Path, capsys
) -> None:
    """WI-nulot: the INV-javam taint-coverage signal (previously stderr-only) is
    machine-visible in --json. A taint-flow claim evaluated against a repo whose
    language has no taint catalog exposes that language in
    ``unsupported_taint_languages`` — so a CI gate parsing the JSON can tell a
    'confirmed' verdict apart from a genuinely-verified one.

    The example was BASH until INV-jurif gave it an environment-read source to
    go with INV-vavup's redirection sinks; bash now satisfies the both-halves
    predicate and is correctly absent from this list. Switched to a language
    that genuinely has no catalogue, so the test still exercises the signal
    rather than quietly asserting nothing — a fixture that stops discriminating
    is worse than a deleted test, because it still looks like coverage.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "markdown:README.md:1-10:intro:section", "name": "intro",
             "kind": "section", "language": "markdown", "path": "README.md",
             "span": {"start_line": 1, "end_line": 10}},
        ],
        edges=[],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))

    claims = {
        "claims": [
            {"id": "TF-001", "text": "Plaintext must not reach host filesystem",
             "constraint": {"taint_flow": {"source_taint": "plaintext",
                                           "prohibited_sink_zone": "host_fs"}}},
        ],
    }
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = True

    cmd_verify_claims(args)

    data = json.loads(capsys.readouterr().out)
    assert "markdown" in data["unsupported_taint_languages"]
    assert "bash" not in data["unsupported_taint_languages"], (
        "bash carries both taint halves as of INV-jurif and must no longer be "
        "reported as an unverified language"
    )


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
            "dst": "javascript:external:0-0:fs.unlinkSync:unresolved", "is_resolved": False,
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
    violated = [r for r in data["verdicts"] if r["verdict"] == "violated"]
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
             "dst": "python:external:0-0:Fernet.decrypt:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            # handler calls Path.write_text (taint sink - host_fs).
            # io-boundary:F3 — write_text is method-kind, so the edge carries
            # its receiver module (pathlib.Path); a bare unresolved method
            # call would be suppressed (INV-tapat).
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:pathlib.Path:0-0:write_text:unresolved",
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
    assert data["verdicts"][0]["verdict"] == "violated"
    assert data["verdicts"][0]["evidence_count"] >= 1
    assert "approximate" in data["verdicts"][0]["details"]


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
             "dst": "python:external:0-0:Fernet.decrypt:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            # handler calls store
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:app.py:20-30:store:function",
             "type": "calls", "confidence": 0.9},
            # store calls Fernet.encrypt (sanitizer)
            {"src": "python:app.py:20-30:store:function",
             "dst": "python:external:0-0:Fernet.encrypt:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            # store calls Path.write_text (taint sink). io-boundary:F3 — the
            # method-kind sink carries its receiver module so the (sanitized)
            # flow is still detected and reported confirmed-safe.
            {"src": "python:app.py:20-30:store:function",
             "dst": "python:pathlib.Path:0-0:write_text:unresolved",
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
             "dst": "haskell:external:0-0:putStrLn:unresolved", "is_resolved": False,
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
                        "source_taint": "host_secret",
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
    # BEHAVIOUR CHANGED, deliberately. This test used to assert rc == 0 with
    # the comment "verdict is still confirmed ... but the notice must be
    # present so humans don't misread the verdict as a pass". A stderr notice
    # is not proportionate to a verdict: `confirmed` at rc=0 is what a CI gate
    # keys on and what a human reads as a pass, and the notice is not in the
    # JSON verdict at all. The tool must not report a clean bill of health for
    # a language it never analysed — measured live, a PHP file doing
    # `file_put_contents("/tmp/out", $_GET['payload'])` returned `confirmed`.
    # The boundary side of this same command already downgrades a blind
    # analysis to INCONCLUSIVE (WI-kajil / INV-bitig); taint was the
    # inconsistent one. The notice stays — it says WHICH language is unverified.
    assert rc == 2
    out, err = capsys.readouterr()
    assert "INCONCLUSIVE" in out
    assert "brainfuck" in err
    assert "no taint-flow catalog" in err
    assert "NOT actually verified" in err
    assert "INV-javam" in err


def test_a_data_file_in_a_covered_repo_does_not_block_confirmation(
    tmp_path: Path, capsys,
) -> None:
    """A language with no taint catalogue but NO CODE must not block a verdict.

    REGRESSION. The first version of this gate blocked on
    ``unsupported_taint_languages`` directly, so a repo containing a single
    YAML file — or JSON, or markdown, i.e. every real repo, including one
    that merely keeps its own claims file in-tree — returned ``inconclusive``
    forever and ``confirmed`` became unreachable. That is the blanket
    downgrade that makes a verdict worthless in the opposite direction, and
    it is why the gate keys on languages that produced CALL EDGES rather than
    on languages merely present.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
             "language": "python", "path": "a.py",
             "span": {"start_line": 1, "end_line": 5}},
            # Present, uncatalogued for taint, and carrying no call structure.
            {"id": "yaml:conf.yaml:1:root:config", "name": "root",
             "kind": "config", "language": "yaml", "path": "conf.yaml",
             "span": {"start_line": 1, "end_line": 2}},
        ],
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:b.py:1:g:function",
                "type": "calls", "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "TF-YAML", "text": "No secrets to disk",
         "constraint": {"taint_flow": {"source_taint": "host_secret",
                                       "prohibited_sink_zone": "host_fs"}}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 0, "a data file must not make every verdict inconclusive"
    assert "CONFIRMED" in capsys.readouterr().out


def test_taint_claim_confirms_when_every_language_is_covered(
    tmp_path: Path, capsys,
) -> None:
    """CONTROL for the downgrade above: the gate must not be blanket.

    A repo whose only language has a taint catalogue, with call edges present
    and no violating flow, still CONFIRMS at rc=0. Without this control the
    downgrade could be implemented as "always inconclusive" and the test above
    would still pass — the failure mode that makes a safety gate useless by
    making every verdict meaningless.
    """
    bmap = _make_behavior_map(
        nodes=[_node("python:a.py:1:f:function", "python", "a.py")],
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:helpers.py:1:helper:function",
                "type": "calls", "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "TF-002", "text": "No secrets to disk",
         "constraint": {"taint_flow": {"source_taint": "host_secret",
                                       "prohibited_sink_zone": "host_fs"}}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 0
    assert "CONFIRMED" in capsys.readouterr().out


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
        # A realistic analysed repo produces call edges. This fixture used
        # edges=[], which is a VACUOUS analysis — nothing was examined — and
        # a taint claim can no longer be confirmed against one, for the same
        # INV-bitig reason a boundary claim never could
        # (test_verify_claims_vacuous_analysis_inconclusive). The property
        # under test here is that the NOTICE does not fire for a supported
        # language, which is unchanged; the fixture just has to describe a
        # repo the analysis actually looked at. Same fix as the comment in
        # test_verify_claims_json_output.
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:b.py:1:g:function",
                "type": "calls", "confidence": 0.9}],
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
             "dst": "python:external:0-0:myapp.config.get_secret:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:pathlib.Path.write_text:unresolved", "is_resolved": False,
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
    assert data["verdicts"][0]["verdict"] == "violated"
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
             "dst": "python:external:0-0:myapp.config.get_secret:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:pathlib.Path.write_text:unresolved", "is_resolved": False,
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
    assert data["verdicts"][0]["verdict"] == "violated"
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
             "dst": "python:external:0-0:Fernet.decrypt:unresolved", "is_resolved": False,
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
             "dst": "python:external:0-0:plaintext_decrypt:unresolved", "is_resolved": False,
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
             "dst": "python:external:0-0:get:unresolved", "is_resolved": False,
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
    # no propagation runs for shellscript.
    #
    # THE VERDICT USED TO BE ``confirmed`` (rc 0) HERE, and the comment above
    # said so in as many words: "claim confirmed vacuously". Vacuous is the
    # problem — shellscript has no I/O catalogue, so the analysis could not
    # look, and INV-dabov replaced that all-clear with ``inconclusive``. The
    # branch this test exists to cover is still exercised: the per-language
    # loop runs before any verdict is produced.
    assert rc == 2


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


def _taint_vocab_args(
    tmp_path: Path,
    source_taint: str,
    prohibited_sink_zone: str,
    sink_yaml: "str | None" = None,
) -> "FakeArgs":
    """Args for a taint_flow claim, over a behavior map with a real call edge.

    The map is deliberately non-empty: a claim rejected for its VOCABULARY must
    be rejected on a run that could otherwise have produced findings, or the
    test cannot tell refusal apart from an empty analysis.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "shellscript:Main.sh:1-10:main:function", "name": "main",
             "kind": "function", "language": "shellscript", "path": "Main.sh",
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
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [{
        "id": "TF-1", "text": "x",
        "constraint": {"taint_flow": {
            "source_taint": source_taint,
            "prohibited_sink_zone": prohibited_sink_zone,
        }},
    }]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    if sink_yaml is not None:
        sinks_file = tmp_path / "sinks.yaml"
        sinks_file.write_text(sink_yaml, encoding="utf-8")
        args.taint_sinks = [str(sinks_file)]
    return args


def test_verify_claims_unknown_source_taint_exits_2(
    tmp_path: Path, capsys,
) -> None:
    """INV-todas: an unrecognised ``source_taint`` must not read as an
    all-clear.

    The boundary arm has had this guard since INV-gobob / WI-ruzib and its
    comment states the mechanism exactly: an unknown value makes the lookup
    return nothing, so ``chain_count`` is 0 and the claim confirms. The taint
    arm has the identical shape — ``verify_claim`` filters findings on
    ``f.taint_label == tf.source_taint`` — and was never given the check.

    Measured before the fix, on a fixture that really leaks
    (``os.environ["API_KEY"]`` through ``open(...).write``): ``source_taint:
    secret_material`` returned **confirmed, rc 0**, while the correct label
    ``host_secret`` returned violated, rc 1.
    """
    rc = cmd_verify_claims(
        _taint_vocab_args(tmp_path, "secret_material", "host_fs"),
    )
    assert rc == 2, (
        "an unrecognised taint label resolved the claim instead of refusing "
        "it; 'I could not parse your claim' and 'your claim holds' must not "
        "share an exit code"
    )
    err = capsys.readouterr().err
    assert "secret_material" in err, "the error must name the offending value"
    assert "host_secret" in err, (
        "the error must list the vocabulary — the labels are not discoverable "
        "anywhere else on the error path"
    )


def test_verify_claims_unknown_sink_zone_exits_2_with_a_suggestion(
    tmp_path: Path, capsys,
) -> None:
    """INV-todas, the other half — and the near-miss that motivates it.

    ``host_filesystem`` for ``host_fs`` is the shape an author actually
    produces on a first attempt. Before the fix it returned confirmed, rc 0.
    """
    rc = cmd_verify_claims(
        _taint_vocab_args(tmp_path, "host_secret", "host_filesystem"),
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "host_filesystem" in err
    assert "host_fs" in err, (
        "a near miss must get the did-you-mean the boundary arm already gives"
    )


def test_a_boundary_claim_earlier_in_the_file_does_not_mask_a_taint_typo(
    tmp_path: Path, capsys,
) -> None:
    """A mixed claims file must be scanned THROUGH, not up to the first
    non-taint claim.

    Most real claims files carry both kinds. A boundary claim has no
    ``taint_flow``, so the vocabulary loop skips it — and had that skip been
    written as a ``break`` rather than a ``continue``, every taint claim after
    the first boundary claim would go unchecked and INV-todas would be half
    open, silently, in exactly the files most likely to exist. The boundary
    claim is placed FIRST here so the test fails if that ever regresses.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "shellscript:Main.sh:1-10:main:function", "name": "main",
             "kind": "function", "language": "shellscript", "path": "Main.sh",
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
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "B-1", "text": "no network",
         "constraint": {"boundary": "net_send", "must_not_exist": True}},
        {"id": "TF-1", "text": "x",
         "constraint": {"taint_flow": {
             "source_taint": "host_secret",
             "prohibited_sink_zone": "host_filesystem",
         }}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 2, (
        "the typo'd taint claim after a boundary claim was not reached"
    )
    assert "host_filesystem" in capsys.readouterr().err


def test_a_user_supplied_zone_is_still_accepted(tmp_path: Path, capsys) -> None:
    """NON-VACUITY, and the reason this check cannot live in ``load_claims``.

    Unlike ``KNOWN_IO_BOUNDARIES``, the taint vocabularies are NOT constants —
    ``--taint-sinks`` may legitimately declare a zone no built-in catalogue
    mentions. Validating against built-ins alone would reject a correct claim,
    which is a different failure and not an improvement. So the check runs
    against the RESOLVED catalogue, and this pins that a user-declared zone
    survives it.
    """
    cmd_verify_claims(_taint_vocab_args(
        tmp_path, "plaintext", "custom_zone",
        sink_yaml=(
            "zone: custom_zone\n"
            "trust_level: untrusted\n"
            "sinks:\n"
            "  shellscript:\n"
            "    - module: my.pkg\n"
            "      functions: [doStuff]\n"
        ),
    ))
    # ASSERTED ON THE REASON, NOT THE EXIT CODE. This test first checked
    # ``rc != 2`` and that was too weak to mean anything: rc 2 covers every
    # inconclusive cause, and INV-dabov later made this same fixture exit 2 for
    # an unrelated one (``shellscript`` has no I/O catalogue). An exit code
    # that two different mechanisms can produce cannot discriminate between
    # them, so the vocabulary check is verified by its own absence from the
    # error stream instead.
    err = capsys.readouterr().err
    assert "unknown prohibited_sink_zone" not in err, (
        "a zone the user declared was rejected as unknown vocabulary; the "
        "check is reading built-ins instead of the resolved catalogue"
    )
    assert "custom_zone" not in err


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
    net_send claim still CONFIRMS (rc=0) — the coverage gate is not blanket.

    THE FIXTURE'S SECOND LANGUAGE WAS ``javascript`` AND IS NOW ``go``, and the
    swap is the point rather than a workaround. javascript is declared
    method-call blind (``analyzer_disclosure``: it emits no call edge for an
    external instance-method call, WI-nasuf), so a clean verdict on a
    repository containing it is deliberately no longer BARE — it carries
    ``analyzer_method_call_blind`` and exits 3. Leaving javascript here and
    relaxing the assertion to accept rc 3 would have destroyed this test's
    contract, which is that a clean verdict over SIGHTED languages stays bare.
    go is declared sighted and measured so.

    The behaviour this test used to cover for javascript is not lost: it is
    pinned from the other side in
    ``test_declared_analyzer_blindness.py::TestTheVerdict``, where a blind
    language must NOT produce a bare confirmed and a sighted one must.
    """
    bmap = _make_behavior_map(
        nodes=[
            _node("python:a.py:1:f:function", "python", "a.py"),
            _node("go:b.go:1:g:function", "go", "b.go"),
        ],
        edges=[
            {"src": "python:a.py:1:f:function",
             "dst": "python:helpers.py:1:helper:function",
             "type": "calls", "confidence": 0.9},
            {"src": "go:b.go:1:g:function",
             "dst": "go:util.go:1:util:function",
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
             "dst": "python:external:0-0:writeit:unresolved", "is_resolved": False, "type": "calls",
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
    assert json.loads(capsys.readouterr().out)["verdicts"][0]["verdict"] == "violated"

    # With CLI override: entry is relabeled cli_label, so claims_label no
    # longer seeds anything.
    #
    # THIS ASSERTION CHANGED, AND THE OLD ONE WAS THE DEFECT (INV-todas). It
    # read ``rc == 0`` / ``confirmed``, with the rationale "the override
    # displaced it" — but a displaced label can be carried by no finding, so
    # that was the tool answering "I cannot evaluate your claim" with "your
    # claim holds". rc 2 is the honest verdict, and it is also STRONGER
    # evidence for the precedence this test exists to prove: the error names
    # the winner and the loser, so the override is visible in the output
    # rather than inferred from a silence.
    over = FakeArgs()
    over.path = str(tmp_path)
    over.input = str(input_file)
    over.claims = str(claims_file)
    over.json_output = True
    over.taint_sources = [str(tmp_path / "cli_src.yaml")]
    rc = cmd_verify_claims(over)
    assert rc == 2
    err = capsys.readouterr().err
    assert "claims_label" in err, "the displaced label must be named"
    assert "cli_label" in err, (
        "the CLI label must appear in the surviving vocabulary — that IS the "
        "precedence evidence"
    )


def test_language_with_a_token_call_edge_still_falsely_confirms(
    tmp_path: Path, capsys,
) -> None:
    """A catalogued-but-blind language must not yield ``confirmed``.

    CLOSED by :func:`verify_claims.method_starved_modules`. Kotlin's catalogue is
    method-shaped (30 method-keyed modules against 1 function-keyed), and Kotlin
    emits no call edge for an external instance-method call — ~95% of its
    catalogued sinks (WI-nasuf). The gate now asks whether any method-construct
    call edge landed in a method-keyed module the repo actually calls, instead of
    "did this language produce ANY call edge".

    THE FIXTURE WAS CORRECTED WHEN THIS WAS CLOSED, and that matters more than the
    marker removal. It asserted the right verdict against a map that did not model
    the defect: its only edge went to ``kotlin:App.kt:9-12:helper:function``, a bare
    intra-repo symbol naming no catalogued module. Running ``hypergumbo survey`` over
    the real Kotlin source this test describes — a ``Socket`` read written to a
    ``File`` — emits something different and more specific: the CONSTRUCTOR calls
    ``kotlin:java.net.Socket:0-0:Socket:external_symbol`` and
    ``kotlin:java.io.File:0-0:File:external_symbol``, with ``writeText`` and
    ``readBytes`` producing no edge at all. The edges below are that real output.

    BEHAVIOURAL EVIDENCE, since this test's failure mode is a silent pass: the live
    fixture repo goes ``rc=0 confirmed`` -> ``rc=2 inconclusive``, while a clean
    Kotlin repo, a pure-computation Python repo and a Python repo with a real
    ``asyncio.start_server`` -> ``os.mkdir`` flow all keep their prior verdicts
    (``confirmed`` / ``confirmed`` / ``violated``). The per-predicate measurement is
    in ``scripts/measure-blind-language-signal.py``.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "kotlin:App.kt:1-8:handler:function", "name": "handler",
             "kind": "function", "language": "kotlin", "path": "App.kt",
             "span": {"start_line": 1, "end_line": 8}},
        ],
        # Real `survey` output: the constructors are emitted, the METHOD calls on
        # them (writeText / readBytes) are not. Enough call edges for the old
        # 'produced any call edges' check to consider the language covered.
        edges=[
            {"src": "kotlin:App.kt:1-8:handler:function",
             "dst": "kotlin:java.io.File:0-0:File:external_symbol",
             "type": "calls", "confidence": 0.9},
            {"src": "kotlin:App.kt:1-8:handler:function",
             "dst": "kotlin:java.net.Socket:0-0:Socket:external_symbol",
             "type": "calls", "confidence": 0.9},
            # LOAD-BEARING, and dropping it is what caught the abstention rule
            # while this was being written. The gate abstains for any language
            # that never stamps ``call_construct`` (JS/TS stamp it zero times),
            # so Kotlin has to demonstrate it stamps the field at all before an
            # unstamped external call counts as evidence of blindness. Real
            # ``survey`` output supplies exactly this edge for the intra-repo
            # ``main() -> Handler`` call.
            {"src": "kotlin:App.kt:12-14:main:function",
             "dst": "kotlin:App.kt:4-10:Handler:class",
             "type": "calls", "confidence": 0.9,
             "meta": {"call_construct": "function"}},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "TF-KT", "text": "No untrusted input to disk",
         "constraint": {"taint_flow": {"source_taint": "untrusted_input",
                                       "prohibited_sink_zone": "host_fs"}}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 2, "a language whose sinks are structurally invisible must " \
                    "not produce a confirmed verdict"


def test_uncatalogued_language_WITH_code_blocks_confirmation(
    tmp_path: Path, capsys,
) -> None:
    """A code-bearing language with no taint catalogue makes a claim inconclusive.

    This is the branch the PHP repro exercises live:
    ``file_put_contents("/tmp/out", $_GET['payload'])`` returned ``confirmed``
    at exit 0 because PHP has no taint sources or sinks.

    It needs a fixture where an uncatalogued language carries CALL EDGES
    alongside a catalogued language that also does. The sibling brainfuck
    test above cannot reach it: its fixture has no edges at all, so it goes
    inconclusive down the vacuous-analysis path instead and would keep
    passing if the uncatalogued-language rule were deleted outright.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
             "language": "python", "path": "a.py",
             "span": {"start_line": 1, "end_line": 5}},
            {"id": "brainfuck:m.bf:1:main:function", "name": "main",
             "kind": "function", "language": "brainfuck", "path": "m.bf",
             "span": {"start_line": 1, "end_line": 5}},
        ],
        edges=[
            {"src": "python:a.py:1:f:function",
             "dst": "python:b.py:1:g:function",
             "type": "calls", "confidence": 0.9},
            # The uncatalogued language HAS call structure — so a taint flow
            # could travel through it and the analysis cannot say it does not.
            {"src": "brainfuck:m.bf:1:main:function",
             "dst": "brainfuck:m.bf:9:helper:function",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "TF-BF", "text": "No secrets to disk",
         "constraint": {"taint_flow": {"source_taint": "host_secret",
                                       "prohibited_sink_zone": "host_fs"}}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False

    rc = cmd_verify_claims(args)
    assert rc == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert "brainfuck" in out, "the verdict must name WHICH language was blind"


def _fixture_only_uncatalogued_args(tmp_path: Path, *, code_path: str):
    """A repo whose ONLY uncatalogued-language code sits at ``code_path``.

    Mirrors ``test_uncatalogued_language_WITH_code_blocks_confirmation``
    exactly except for that path, so the pair is a controlled comparison:
    same languages, same edge shapes, same claim. The only variable is
    whether the brainfuck file is production code or a test fixture.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:a.py:1:f:function", "name": "f", "kind": "function",
             "language": "python", "path": "a.py",
             "span": {"start_line": 1, "end_line": 5}},
            {"id": f"brainfuck:{code_path}:1:main:function", "name": "main",
             "kind": "function", "language": "brainfuck", "path": code_path,
             "span": {"start_line": 1, "end_line": 5}},
        ],
        edges=[
            {"src": "python:a.py:1:f:function",
             "dst": "python:b.py:1:g:function",
             "type": "calls", "confidence": 0.9},
            {"src": f"brainfuck:{code_path}:1:main:function",
             "dst": f"brainfuck:{code_path}:9:helper:function",
             "type": "calls", "confidence": 0.9},
        ],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({"claims": [
        {"id": "TF-BF", "text": "No secrets to disk",
         "constraint": {"taint_flow": {"source_taint": "host_secret",
                                       "prohibited_sink_zone": "host_fs"}}},
    ]}))
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    return args


def test_a_fixture_only_uncatalogued_language_does_not_block_confirmation(
    tmp_path: Path, capsys,
) -> None:
    """INV-dabuf — the language census must ask the SAME production question
    the flow filter already asks.

    ``verify_claims`` excludes test/fixture/migration-SOURCED flows by default
    (``include_non_production=False``, WI-bifob): a taint flow originating in a
    fixture is not the shipped application doing it. The language census that
    decides whether ANY claim may be confirmed asked no such question — it
    counted every ``calls`` edge in the tree — so one fixture file in a
    language with no taint catalogue blocked every claim in the repo. One tool,
    two answers about whether a fixture counts.

    MEASURED on hypergumbo's own self-proof at dev ``d7b069b106``: 18 of 18
    claims inconclusive, rc 2, every one blocked by
    ``(bash, csharp, solidity)`` — where csharp is 3 files and solidity 1,
    ALL of them under ``tests/fixtures/``.
    """
    args = _fixture_only_uncatalogued_args(
        tmp_path, code_path="tests/fixtures/schema-corpus/m.bf",
    )
    rc = cmd_verify_claims(args)
    out = capsys.readouterr().out
    assert "brainfuck" not in out, (
        f"a brainfuck FIXTURE cannot be the shipped application reaching the "
        f"filesystem, so it must not block the verdict; got: {out!r}"
    )
    assert rc == 0, f"expected the claim to resolve; got rc={rc}, out={out!r}"


def test_include_non_production_sources_puts_the_fixture_back_in_the_census(
    tmp_path: Path, capsys,
) -> None:
    """SYMMETRY WITH THE FLOW FILTER, which is the whole justification.

    The fix is defensible only because it makes the census ask the question
    the flow filter already asks. If the flag that widens the flow filter did
    not also widen the census, the two would disagree again in the opposite
    direction — a user who asked to count fixture flows would still be told
    the fixture's language was irrelevant.
    """
    args = _fixture_only_uncatalogued_args(
        tmp_path, code_path="tests/fixtures/schema-corpus/m.bf",
    )
    args.include_non_production_sources = True
    rc = cmd_verify_claims(args)
    out = capsys.readouterr().out
    assert rc == 2 and "brainfuck" in out, (
        f"with fixtures counted, the uncatalogued fixture language must block "
        f"again; got rc={rc}, out={out!r}"
    )


# ---------------------------------------------------------------------------
# INV-zosun: a verdict must record what catalogue it trusted.
# ---------------------------------------------------------------------------

_PROVENANCE_OVERLAY = (
    "language: python\n"
    "status: overlay\n"
    "net_send:\n"
    "  - module: requests\n"
    "    functions: [post]\n"
)


def _provenance_args(
    tmp_path: Path,
    *,
    cli_overlay: bool = False,
    claims_overlay: bool = False,
    json_output: bool = True,
) -> "FakeArgs":
    """A boundary claim over a trivial map, with overlays on either layer."""
    bmap = _make_behavior_map(
        nodes=[{"id": "python:a.py:1:f:function", "name": "f",
                "kind": "function", "language": "python", "path": "a.py",
                "span": {"start_line": 1, "end_line": 5}}],
        edges=[{"src": "python:a.py:1:f:function",
                "dst": "python:os:0-0:os.listdir:external_symbol",
                "type": "calls", "confidence": 0.9}],
    )
    input_file = tmp_path / "hg.json"
    input_file.write_text(json.dumps(bmap))
    overlay = tmp_path / "ov.yaml"
    overlay.write_text(_PROVENANCE_OVERLAY, encoding="utf-8")

    claims: dict = {"claims": [
        {"id": "SC-1", "text": "no network",
         "constraint": {"boundary": "net_send", "must_not_exist": True}},
    ]}
    if claims_overlay:
        claims["extra_catalogs"] = {"io_primitives": [str(overlay)]}
    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump(claims))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = json_output
    if cli_overlay:
        args.io_primitives = [str(overlay)]
    return args


def test_the_envelope_records_a_cli_supplied_catalogue(
    tmp_path: Path, capsys,
) -> None:
    """INV-zosun: a `confirmed` reached against a user-supplied catalogue and
    one reached against the shipped catalogue were byte-identical.

    The only trace was a stderr line naming the overlay path — absent from the
    ``--json`` envelope entirely, so it vanished the moment anyone piped the
    output anywhere. That matters because a row is not a detection-only grant:
    since INV-buzab a classified call is what ``examined`` MEANS, so a row
    carrying the wrong boundary buys a `confirmed` for the boundary actually
    claimed. Demonstrated end-to-end: the same fixture went
    ``inconclusive`` rc 2 -> ``confirmed`` rc 0 on a five-line overlay whose
    only lie was filing ``requests.post`` under ``fs_read``.

    This does not change any verdict. It makes the trust decision visible.
    """
    cmd_verify_claims(_provenance_args(tmp_path, cli_overlay=True))
    env = json.loads(capsys.readouterr().out)
    prov = env["catalog_provenance"]
    assert prov["user_supplied"] is True
    assert prov["layers"]["io_primitives"]["cli"], (
        "the overlay the verdict was computed against is not in the envelope"
    )
    assert prov["layers"]["io_primitives"]["claims_file"] == []


def test_the_envelope_separates_the_claims_file_layer_from_the_cli_layer(
    tmp_path: Path, capsys,
) -> None:
    """THE DISTINCTION THAT MATTERS FOR A SELF-GRADING REPO.

    A catalogue passed on the command line is supplied by whoever RAN the
    tool. One reached through the claims file's ``extra_catalogs:`` travels
    WITH the repo — and if the claims file and its catalogues live inside the
    tree under analysis, the repo is grading itself. Demonstrated: a directory
    containing ``main.py``, ``claims.yaml`` and an overlay mislabelling
    ``requests.post`` returns ``confirmed`` rc 0, where the same repo without
    the ``extra_catalogs:`` line returns ``inconclusive`` rc 2.

    Collapsing the two layers into one list would report that a catalogue was
    used while hiding the fact that the subject supplied it.
    """
    cmd_verify_claims(_provenance_args(tmp_path, claims_overlay=True))
    prov = json.loads(capsys.readouterr().out)["catalog_provenance"]
    assert prov["user_supplied"] is True
    assert prov["layers"]["io_primitives"]["claims_file"], (
        "a claims-file-supplied catalogue must be attributed to that layer"
    )
    assert prov["layers"]["io_primitives"]["cli"] == []


def test_the_key_is_present_and_negative_on_a_shipped_catalogue_run(
    tmp_path: Path, capsys,
) -> None:
    """NON-VACUITY, and a stable envelope shape.

    ``user_supplied: false`` is the load-bearing half — a consumer must be
    able to assert that a verdict rested on the shipped catalogue alone.
    Emitting the key only when an overlay is present would teach consumers to
    read absence as "none", which is the same mistake as reading "no chains
    found" as "no I/O". The envelope follows the existing convention
    (``dataflow_coverage``, INV-karud a3): always present.
    """
    cmd_verify_claims(_provenance_args(tmp_path))
    prov = json.loads(capsys.readouterr().out)["catalog_provenance"]
    assert prov["user_supplied"] is False
    for kind, layers in prov["layers"].items():
        assert layers == {"cli": [], "claims_file": []}, kind
    assert set(prov["layers"]) == {
        "io_primitives", "taint_sources", "taint_sinks", "taint_sanitizers",
    }


def test_the_text_reader_is_told_too(tmp_path: Path, capsys) -> None:
    """A disclosure that exists only under --json is half shipped.

    That is this file's own precedent, recorded on INV-karud (a3) when
    WI-bifob's exclusion bucket reached the dataclass and never the text
    renderer. The stderr line that exists today is worse than json-only: it is
    discarded by any redirect, and it is not attached to the verdict.
    """
    cmd_verify_claims(
        _provenance_args(tmp_path, cli_overlay=True, json_output=False),
    )
    out = capsys.readouterr().out
    assert "ov.yaml" in out, (
        "the text verdict does not say which catalogue produced it"
    )


# ---------------------------------------------------------------------------
# INV-pojib (b)/(c): the exit code carries the repo-supplied dependency
# ---------------------------------------------------------------------------


def _caveat_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A flow that exists, and a project-local sanitizer that removes it.

    Shaped so the STRUCTURAL pass adjudicates it: the sanitizer is called by an
    intermediate function rather than by the seed, because the barrier exempts
    the seed function by design and a same-function sanitizer is honoured only
    on the DDG arm.
    """
    bmap = _make_behavior_map(
        nodes=[
            {"id": "python:app.py:1-10:handler:function", "name": "handler",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
            {"id": "python:app.py:11-20:mid:function", "name": "mid",
             "kind": "function", "language": "python", "path": "app.py",
             "span": {"start_line": 11, "end_line": 20}},
        ],
        edges=[
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:external:0-0:myapp.config.get_secret:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:1-10:handler:function",
             "dst": "python:app.py:11-20:mid:function",
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:11-20:mid:function",
             "dst": "python:external:0-0:myapp.util.launder:unresolved", "is_resolved": False,
             "type": "calls", "confidence": 0.9},
            {"src": "python:app.py:11-20:mid:function",
             "dst": "python:external:0-0:pathlib.Path.write_text:unresolved", "is_resolved": False,
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

    san = tmp_path / "project_sanitizers.yaml"
    san.write_text(
        'description: "Project-local laundering"\n'
        "transforms:\n"
        "  - input_taint: project_secret\n"
        "    output_taint: safe\n"
        "    functions:\n"
        "      python:\n"
        "        - myapp.util.launder\n"
    )

    claims_file = tmp_path / "claims.yaml"
    claims_file.write_text(yaml.dump({
        "claims": [{
            "id": "TF-POJIB",
            "text": "Project secrets must not reach host_fs",
            "constraint": {"taint_flow": {
                "source_taint": "project_secret",
                "prohibited_sink_zone": "host_fs",
            }},
        }],
    }))
    return input_file, user_src, san, claims_file


def _caveat_args(tmp_path: Path, *, with_sanitizer: bool, as_json: bool):
    input_file, user_src, san, claims_file = _caveat_fixture(tmp_path)
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = as_json
    args.taint_sources = [str(user_src)]
    if with_sanitizer:
        args.taint_sanitizers = [str(san)]
    return args


def test_verify_claims_exit_3_when_a_repo_supplied_sanitizer_holds_it_up(
    tmp_path: Path, capsys,
) -> None:
    """INV-pojib (b). THE POINT OF THE WHOLE ITEM: a CI gate reads ``$?``.

    Remedy (a1) already named the sanitizer in the verdict prose. Measured on
    the shipped CLI at dev 1ec23deb31, that left the machine surface unchanged —
    an 8-line sanitizer file turned a real ``violated`` rc 1 into ``confirmed``
    rc 0, byte-identical to a verdict the analysis earned unaided. Nothing a
    gate reads had moved.
    """
    rc = cmd_verify_claims(_caveat_args(
        tmp_path, with_sanitizer=True, as_json=True,
    ))
    assert rc == 3, (
        "a verdict resting on an entry the analysed repository supplied about "
        "itself must not exit 0 alongside verdicts the analysis earned"
    )
    data = json.loads(capsys.readouterr().out)
    verdict = data["verdicts"][0]
    assert verdict["verdict"] == "confirmed_with_caveats"
    assert verdict["caveats"][0]["kind"] == "user_supplied_sanitizer"
    assert "myapp.util.launder" in verdict["caveats"][0]["entries"]


def test_verify_claims_exit_1_without_the_sanitizer(
    tmp_path: Path, capsys,
) -> None:
    """CONTROL, and the one that makes the test above mean anything.

    The same fixture with no sanitizer file is a real violation. Without this,
    exit 3 could equally be a fixture that never had a flow.
    """
    rc = cmd_verify_claims(_caveat_args(
        tmp_path, with_sanitizer=False, as_json=True,
    ))
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdicts"][0]["verdict"] == "violated"


def test_caveated_verdict_is_visible_on_the_text_surface(
    tmp_path: Path, capsys,
) -> None:
    """A disclosure that exists only under ``--json`` is half shipped — the
    same argument that put WI-bifob's exclusion bucket and INV-zosun's
    provenance block on the text renderer.
    """
    cmd_verify_claims(_caveat_args(
        tmp_path, with_sanitizer=True, as_json=False,
    ))
    out = capsys.readouterr().out
    assert "CONFIRMED WITH CAVEATS" in out
    assert "CAVEAT (user_supplied_sanitizer)" in out
    assert "myapp.util.launder" in out
    assert "all 1 CONFIRMED" not in out, (
        "the summary line must not report a caveated verdict as an unqualified "
        "pass — that line is what a human skims"
    )
