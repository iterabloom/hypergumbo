# SPDX-License-Identifier: AGPL-3.0-or-later
"""verify-claims' exit-code contract, end to end through ``cli.main``.

THE CONTRACT (WI-tajup): ``0`` every claim confirmed; ``1`` at least one
violated; ``2`` bad input, before any verdict; ``3`` at least one
``confirmed_with_caveats`` and none violated or inconclusive; ``4`` at least
one inconclusive and none violated. No two outcomes share a code: a CI gate
reads ``$?``, so a shared code is a question the gate cannot ask. (``2``
used to mean "inconclusive, or the claims file failed validation", and a
missing claims file or ``--input`` map exited ``1``, the ``violated`` code.)

WHAT IS PINNED HERE, one test per condition:

* each verdict code, the five codes being pairwise distinct, and the
  precedence when several verdicts are present in one run (violated, then
  inconclusive, then caveats);
* every bad-input path the command has, each exiting 2 with an ``Error:``
  line and never the internal-error safety net: the claims file (missing,
  a directory, unreadable, not YAML, failing validation, a field of the
  wrong type), the ``--input`` map (missing, a directory, not JSON, not a
  behavior map, a record list of the wrong shape), the repository path, an
  argparse refusal, and a taint catalogue whose shape the loader cannot read.

Everything runs through ``main([...])``: the code a user's shell sees is
``main``'s return value, and the safety net that once turned a loader crash
into ``1`` lives there, not in ``cmd_verify_claims``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from hypergumbo_core.cli import main
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import (
    EXIT_BAD_INPUT,
    EXIT_CONFIRMED,
    EXIT_CONFIRMED_WITH_CAVEATS,
    EXIT_INCONCLUSIVE,
    EXIT_VIOLATED,
    VERDICT_EXIT_PRECEDENCE,
    ClaimVerdict,
    exit_code_for_verdicts,
)

_F = "python:a.py:1-5:f:function"


def _node(node_id: str = _F, name: str = "f") -> dict[str, Any]:
    return {"id": node_id, "name": name, "kind": "function",
            "language": "python", "path": "a.py",
            "span": {"start_line": 1, "end_line": 5}}


def _write_map(tmp_path: Path, bmap: dict[str, Any]) -> Path:
    path = tmp_path / "survey.json"
    path.write_text(json.dumps(bmap))
    return path


def _map(edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "nodes": [_node()], "edges": edges}


#: ``f`` lists a directory: no network, no subprocess.
_LISTDIR = {"src": _F, "dst": "python:os.py:1:os.listdir:function",
            "type": "calls", "confidence": 0.9}
#: ``f`` launches a subprocess.
_SUBPROCESS = {"src": _F, "dst": "python:sub.py:1:subprocess.run:function",
               "type": "calls", "confidence": 0.9}

_NO_NET = {"id": "NO-NET", "text": "no network sends",
           "constraint": {"boundary": "net_send", "must_not_exist": True}}
_NO_SUBPROCESS = {"id": "NO-SUB", "text": "no subprocess",
                  "constraint": {"boundary": "subprocess", "must_not_exist": True}}
#: No must_not_exist / max_chains / taint_flow: nothing to assert, so the
#: verdict is ``inconclusive`` (ADR-0033 Phase 3) -- a verdict, not bad input.
_NOTHING_TO_CHECK = {"id": "NOTHING", "text": "fs reads, unconstrained",
                     "constraint": {"boundary": "fs_read"}}
_NO_PLAINTEXT_TO_DISK = {
    "id": "TF-001", "text": "plaintext must not reach host_fs",
    "constraint": {"taint_flow": {"source_taint": "plaintext",
                                  "prohibited_sink_zone": "host_fs"}}}
_NO_FS_WRITE = {"id": "NO-FS-WRITE", "text": "no file writes",
                "constraint": {"boundary": "fs_write", "must_not_exist": True}}


def _claims(tmp_path: Path, *claims: dict[str, Any], name: str = "claims.yaml",
            **top: Any) -> Path:
    path = tmp_path / name
    path.write_text(yaml.dump({**top, "claims": list(claims)}))
    return path


def _run(capsys, argv: list[str]) -> tuple[int, str]:
    rc = main(["verify-claims", *argv])
    err = capsys.readouterr().err
    return rc, err


def _verdict_run(tmp_path: Path, capsys, bmap: dict[str, Any],
                 *claims: dict[str, Any], extra: list[str] | None = None) -> int:
    rc, err = _run(capsys, [
        str(tmp_path), "--claims", str(_claims(tmp_path, *claims)),
        "--input", str(_write_map(tmp_path, bmap)), *(extra or [])])
    assert "internal error" not in err, err
    return rc


def _fernet_map() -> dict[str, Any]:
    """handler decrypts (source); store encrypts (sanitizer), then writes."""
    handler = "python:app.py:1-10:handler:function"
    store = "python:app.py:20-30:store:function"
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {"id": handler, "name": "handler", "kind": "function",
             "language": "python", "path": "app.py",
             "span": {"start_line": 1, "end_line": 10}},
            {"id": store, "name": "store", "kind": "function",
             "language": "python", "path": "app.py",
             "span": {"start_line": 20, "end_line": 30}},
        ],
        "edges": [
            {"src": handler, "dst": "python:external:0-0:Fernet.decrypt:unresolved",
             "is_resolved": False, "type": "calls", "confidence": 0.9},
            {"src": handler, "dst": store, "type": "calls", "confidence": 0.9},
            {"src": store, "dst": "python:external:0-0:Fernet.encrypt:unresolved",
             "is_resolved": False, "type": "calls", "confidence": 0.9},
            {"src": store, "dst": "python:pathlib.Path:0-0:write_text:unresolved",
             "type": "calls", "confidence": 0.9},
        ],
    }


def _operator_sanitizer(tmp_path: Path) -> list[str]:
    """The operator vouches for Fernet.encrypt: a user-supplied sanitizer,
    which earns the clean taint verdict the ``user_supplied_sanitizer``
    caveat."""
    mine = tmp_path / "fernet.yaml"
    mine.write_text(
        "transforms:\n  - input_taint: plaintext\n    output_taint: ciphertext\n"
        "    functions:\n      python:\n        - Fernet.encrypt\n")
    return ["--taint-sanitizers", str(mine)]


# ---------------------------------------------------------------------------
# The codes themselves
# ---------------------------------------------------------------------------


def test_the_codes_are_the_ruled_ones() -> None:
    assert (EXIT_CONFIRMED, EXIT_VIOLATED, EXIT_BAD_INPUT,
            EXIT_CONFIRMED_WITH_CAVEATS, EXIT_INCONCLUSIVE) == (0, 1, 2, 3, 4)


def test_the_five_codes_are_pairwise_distinct() -> None:
    """A shared code is an outcome a gate cannot tell from another one."""
    codes = [EXIT_CONFIRMED, EXIT_VIOLATED, EXIT_BAD_INPUT,
             EXIT_CONFIRMED_WITH_CAVEATS, EXIT_INCONCLUSIVE]
    assert len(set(codes)) == len(codes)


def test_the_precedence_table_names_each_verdict_once_in_order() -> None:
    assert VERDICT_EXIT_PRECEDENCE == (
        ("violated", EXIT_VIOLATED),
        ("inconclusive", EXIT_INCONCLUSIVE),
        ("confirmed_with_caveats", EXIT_CONFIRMED_WITH_CAVEATS),
    )


def test_no_verdict_code_is_the_bad_input_code() -> None:
    """2 is reserved for input the command could not use, so a gate can tell
    a typo from a verdict."""
    assert EXIT_BAD_INPUT not in {code for _, code in VERDICT_EXIT_PRECEDENCE}
    assert EXIT_BAD_INPUT != EXIT_CONFIRMED


def _v(verdict: str) -> ClaimVerdict:
    return ClaimVerdict(claim_id=verdict, claim_text="", verdict=verdict)


@pytest.mark.parametrize("verdicts,code", [
    ([], EXIT_CONFIRMED),
    (["confirmed"], EXIT_CONFIRMED),
    (["violated"], EXIT_VIOLATED),
    (["inconclusive"], EXIT_INCONCLUSIVE),
    (["confirmed_with_caveats"], EXIT_CONFIRMED_WITH_CAVEATS),
    (["inconclusive", "violated"], EXIT_VIOLATED),
    (["confirmed_with_caveats", "violated"], EXIT_VIOLATED),
    (["confirmed_with_caveats", "inconclusive"], EXIT_INCONCLUSIVE),
    (["confirmed", "inconclusive", "confirmed_with_caveats", "violated"],
     EXIT_VIOLATED),
])
def test_exit_code_for_verdicts_precedence(verdicts: list[str], code: int) -> None:
    assert exit_code_for_verdicts([_v(v) for v in verdicts]) == code


# ---------------------------------------------------------------------------
# Verdict codes through the CLI
# ---------------------------------------------------------------------------


def test_all_confirmed_exits_0(tmp_path: Path, capsys) -> None:
    assert _verdict_run(tmp_path, capsys, _map([_LISTDIR]), _NO_NET) == 0


def test_a_violated_claim_exits_1(tmp_path: Path, capsys) -> None:
    assert _verdict_run(tmp_path, capsys, _map([_SUBPROCESS]), _NO_SUBPROCESS) == 1


def test_an_inconclusive_claim_exits_4(tmp_path: Path, capsys) -> None:
    assert _verdict_run(tmp_path, capsys, _map([_LISTDIR]), _NOTHING_TO_CHECK) == 4


def test_a_caveated_claim_exits_3(tmp_path: Path, capsys) -> None:
    rc = _verdict_run(tmp_path, capsys, _fernet_map(), _NO_PLAINTEXT_TO_DISK,
                      extra=_operator_sanitizer(tmp_path))
    assert rc == 3


def test_violated_outranks_inconclusive(tmp_path: Path, capsys) -> None:
    """One violated claim decides the run whatever else came back."""
    rc = _verdict_run(tmp_path, capsys, _map([_SUBPROCESS]),
                      _NOTHING_TO_CHECK, _NO_SUBPROCESS)
    assert rc == 1


def test_violated_outranks_a_caveat(tmp_path: Path, capsys) -> None:
    rc = _verdict_run(tmp_path, capsys, _fernet_map(), _NO_PLAINTEXT_TO_DISK,
                      _NO_FS_WRITE, extra=_operator_sanitizer(tmp_path))
    assert rc == 1


def test_violated_outranks_inconclusive_and_a_caveat_together(
    tmp_path: Path, capsys,
) -> None:
    rc = _verdict_run(tmp_path, capsys, _fernet_map(), _NO_PLAINTEXT_TO_DISK,
                      _NO_FS_WRITE, _NOTHING_TO_CHECK,
                      extra=_operator_sanitizer(tmp_path))
    assert rc == 1


def test_inconclusive_beside_a_caveat_exits_4(tmp_path: Path, capsys) -> None:
    """"Could not look" outranks "looked, with a named caveat"."""
    rc = _verdict_run(tmp_path, capsys, _fernet_map(), _NO_PLAINTEXT_TO_DISK,
                      _NOTHING_TO_CHECK, extra=_operator_sanitizer(tmp_path))
    assert rc == 4


def test_an_empty_claims_file_still_exits_0(tmp_path: Path, capsys) -> None:
    """``claims: []`` is documented as "no claims", not as bad input."""
    path = tmp_path / "claims.yaml"
    path.write_text("claims: []\n")
    rc, _ = _run(capsys, [str(tmp_path), "--claims", str(path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# Bad input: every path exits 2, with an Error line, never the safety net
# ---------------------------------------------------------------------------


def _assert_bad_input(rc: int, err: str, *needles: str) -> None:
    assert "internal error" not in err, err
    assert "This is a bug" not in err, err
    assert rc == EXIT_BAD_INPUT, err
    for needle in needles:
        assert needle in err, err


def _ok_claims(tmp_path: Path) -> Path:
    return _claims(tmp_path, _NO_NET, name="ok.yaml")


def test_a_missing_claims_file_exits_2(tmp_path: Path, capsys) -> None:
    """The filed repro: it exited 1, the code for ``violated``."""
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(tmp_path / "nope.yaml")])
    _assert_bad_input(rc, err, "Claims file not found")


def test_a_claims_path_that_is_a_directory_exits_2(tmp_path: Path, capsys) -> None:
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(tmp_path)])
    _assert_bad_input(rc, err, "is a directory")


def test_an_unreadable_claims_file_exits_2(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """A claims file the process may not read reached the safety net
    (``PermissionError`` -> exit 1). Simulated, because a test running as
    root reads a mode-000 file anyway."""
    claims = _ok_claims(tmp_path)
    real_read_text = Path.read_text

    def _denied(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == claims:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _denied)
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(claims)])
    _assert_bad_input(rc, err, "could not read claims file", "Permission denied")


def test_a_claims_file_that_is_not_yaml_exits_2(tmp_path: Path, capsys) -> None:
    path = tmp_path / "claims.yaml"
    path.write_text("claims: [unclosed\n")
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(path)])
    _assert_bad_input(rc, err, "could not parse claims file")


def test_a_claims_file_failing_validation_exits_2(tmp_path: Path, capsys) -> None:
    path = tmp_path / "claims.yaml"
    path.write_text("claims:\n  - id: X\n    constrant: {}\n")
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(path)])
    _assert_bad_input(rc, err, "constrant")


@pytest.mark.parametrize("constraint,needle", [
    # Each crashed past the loader into the safety net (exit 1) or, for
    # must_not_exist, read a string as a flag.
    ({"boundary": ["net_send"], "must_not_exist": True}, "boundary"),
    ({"boundary": "net_send", "must_not_exist": "no"}, "must_not_exist"),
    ({"boundary": "fs_read", "max_chains": "five"}, "max_chains"),
    ({"boundary": "fs_read", "max_chains": True}, "max_chains"),
    ({"taint_flow": {"source_taint": ["plaintext"],
                     "prohibited_sink_zone": "host_fs"}}, "source_taint"),
    ({"taint_flow": {"source_taint": "plaintext",
                     "prohibited_sink_zone": {"zone": "host_fs"}}},
     "prohibited_sink_zone"),
])
def test_a_claim_field_of_the_wrong_type_exits_2(
    tmp_path: Path, capsys, constraint: dict[str, Any], needle: str,
) -> None:
    claims = _claims(tmp_path, {"id": "X", "text": "x", "constraint": constraint})
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(claims),
                            "--input", str(_write_map(tmp_path, _map([_LISTDIR])))])
    _assert_bad_input(rc, err, needle, "'X'")


def test_a_well_typed_max_chains_still_loads(tmp_path: Path, capsys) -> None:
    """Control for the max_chains rows above: an int is a limit."""
    claim = {"id": "X", "text": "x",
             "constraint": {"boundary": "net_send", "max_chains": 0}}
    assert _verdict_run(tmp_path, capsys, _map([_LISTDIR]), claim) == 0


def test_a_missing_input_map_exits_2(tmp_path: Path, capsys) -> None:
    """The filed repro's second half: it exited 1."""
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(tmp_path / "nope.json")])
    _assert_bad_input(rc, err, "Input file not found")


def test_an_input_map_that_is_a_directory_exits_2(tmp_path: Path, capsys) -> None:
    mapdir = tmp_path / "survey.json"
    mapdir.mkdir()
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(mapdir)])
    _assert_bad_input(rc, err, "could not read", str(mapdir))


def test_an_input_map_that_is_not_json_exits_2(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "survey.json"
    bad.write_text("not json {")
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(bad)])
    _assert_bad_input(rc, err, "not valid JSON")


def test_an_input_that_is_not_a_behavior_map_exits_2(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "survey.json"
    bad.write_text("{}")
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(bad)])
    _assert_bad_input(rc, err, "missing required key")


@pytest.mark.parametrize("key,value,needle", [
    ("nodes", None, "'nodes' must be a list"),
    ("nodes", {"a": 1}, "'nodes' must be a list"),
    ("nodes", [5], "'nodes[0]' must be an object"),
    ("edges", None, "'edges' must be a list"),
    ("edges", 5, "'edges' must be a list"),
    ("edges", [_LISTDIR, "x"], "'edges[1]' must be an object"),
    ("entrypoints", None, "'entrypoints' must be a list"),
    ("entrypoints", "x", "'entrypoints' must be a list"),
    ("entrypoints", [5], "'entrypoints[0]' must be an object"),
    ("request_ingress", "x", "'request_ingress' must be a list"),
    ("request_ingress", [5], "'request_ingress[0]' must be an object"),
])
def test_an_input_map_record_list_of_the_wrong_shape_exits_2(
    tmp_path: Path, capsys, key: str, value: Any, needle: str,
) -> None:
    bmap = _map([_LISTDIR])
    bmap[key] = value
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(_write_map(tmp_path, bmap))])
    _assert_bad_input(rc, err, needle)


def test_a_null_request_ingress_is_read_as_absent(tmp_path: Path, capsys) -> None:
    """``ingress_candidates`` falls back to ``entrypoints`` when
    ``request_ingress`` is not a list: an older map's absent key, not bad
    input."""
    bmap = _map([_LISTDIR])
    bmap["request_ingress"] = None
    assert _verdict_run(tmp_path, capsys, bmap, _NO_NET) == 0


def test_a_repository_path_that_does_not_exist_exits_2(
    tmp_path: Path, capsys,
) -> None:
    rc, err = _run(capsys, [str(tmp_path / "no_such_repo"),
                            "--claims", str(_ok_claims(tmp_path))])
    _assert_bad_input(rc, err, "not a directory")


@pytest.mark.parametrize("argv", [
    ["--bogus"],
    ["--format", "xml"],
])
def test_an_argument_argparse_refuses_exits_2(
    tmp_path: Path, capsys, argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["verify-claims", str(tmp_path), "--claims",
              str(_ok_claims(tmp_path)), *argv])
    assert exc.value.code == EXIT_BAD_INPUT


def test_claims_without_a_value_exits_2(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["verify-claims", ".", "--claims"])
    assert exc.value.code == EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# Taint catalogues whose shape the loader could not read
# ---------------------------------------------------------------------------

_SOURCE_OK = {"module": "os", "functions": ["getenv"]}
_SINK_OK = {"module": "logging", "functions": ["info"]}
_SANITIZER_OK = {"input_taint": "plaintext", "output_taint": "ciphertext",
                 "functions": {"python": ["Fernet.encrypt"]}}


def _source_file(**over: Any) -> dict[str, Any]:
    entry = {**_SOURCE_OK, **over.pop("entry", {})}
    return {"taint_label": "plaintext",
            "sources": {"python": [entry]}, **over}


def _sink_file(**over: Any) -> dict[str, Any]:
    entry = {**_SINK_OK, **over.pop("entry", {})}
    return {"zone": "host_fs", "trust_level": "untrusted",
            "sinks": {"python": [entry]}, **over}


def _sanitizer_file(**over: Any) -> dict[str, Any]:
    row = {**_SANITIZER_OK, **over.pop("row", {})}
    return {"transforms": [row], **over}


#: (flag, file body, what the message must name). Each body crashed the
#: loader (AttributeError / TypeError) into main's safety net, exit 1.
_BROKEN_TAINT_FILES: list[tuple[str, dict[str, Any], str]] = [
    ("--taint-sanitizers", {"transforms": ["just a string"]}, "transforms[0]"),
    ("--taint-sanitizers", _sanitizer_file(row={"functions": ["a.b"]}),
     "transforms[0].functions"),
    ("--taint-sanitizers", _sanitizer_file(row={"functions": {"python": 5}}),
     "transforms[0].functions.python"),
    ("--taint-sanitizers", _sanitizer_file(row={"functions": {"python": [["a"]]}}),
     "transforms[0].functions.python[0]"),
    ("--taint-sanitizers", _sanitizer_file(row={"input_taint": ["plaintext"]}),
     "transforms[0].input_taint"),
    ("--taint-sanitizers", _sanitizer_file(row={"output_taint": ["ciphertext"]}),
     "transforms[0].output_taint"),
    ("--taint-sources", _source_file(taint_label=["plaintext"]), "taint_label"),
    ("--taint-sources", {"taint_label": "plaintext", "sources": {"python": None}},
     "sources.python"),
    ("--taint-sources", _source_file(entry={"module": ["os"]}),
     "sources.python[0].module"),
    ("--taint-sources", _source_file(entry={"functions": 5}),
     "sources.python[0].functions"),
    ("--taint-sources", _source_file(entry={"functions": [["getenv"]]}),
     "sources.python[0].functions[0]"),
    ("--taint-sources", _source_file(entry={"functions": [None]}),
     "sources.python[0].functions[0]"),
    ("--taint-sources", _source_file(entry={"methods": 5}),
     "sources.python[0].methods"),
    ("--taint-sources", _source_file(entry={"argument_tainted": 5}),
     "sources.python[0].argument_tainted"),
    ("--taint-sources", _source_file(entry={"argument_tainted": ["0"]}),
     "sources.python[0].argument_tainted[0]"),
    ("--taint-sources", _source_file(entry={"return_tainted": "no"}),
     "sources.python[0].return_tainted"),
    ("--taint-sinks", _sink_file(zone=["host_fs"]), "zone"),
    ("--taint-sinks", _sink_file(trust_level=["untrusted"]), "trust_level"),
    ("--taint-sinks", {"zone": "host_fs", "sinks": {"python": 5}}, "sinks.python"),
    ("--taint-sinks", _sink_file(entry={"module": 5}), "sinks.python[0].module"),
    ("--taint-sinks", _sink_file(entry={"methods": 5}), "sinks.python[0].methods"),
    ("--taint-sinks", _sink_file(entry={"functions": [5]}),
     "sinks.python[0].functions[0]"),
]


@pytest.mark.parametrize(
    "flag,body,needle", _BROKEN_TAINT_FILES,
    ids=[f"{flag[8:]}:{needle}" for flag, _, needle in _BROKEN_TAINT_FILES],
)
def test_a_taint_catalogue_of_the_wrong_shape_exits_2(
    tmp_path: Path, capsys, flag: str, body: dict[str, Any], needle: str,
) -> None:
    bad = tmp_path / "bad_catalogue.yaml"
    bad.write_text(yaml.dump(body))
    claims = _claims(tmp_path, _NO_PLAINTEXT_TO_DISK)
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(claims),
                            "--input", str(_write_map(tmp_path, _fernet_map())),
                            flag, str(bad)])
    _assert_bad_input(rc, err, needle, str(bad))


@pytest.mark.parametrize("flag,body", [
    ("--taint-sanitizers", _sanitizer_file()),
    ("--taint-sources", _source_file()),
    ("--taint-sinks", _sink_file()),
    # A null section or a null ``functions:`` is "none", as
    # ``_safe_load_catalog_yaml`` already reads a null section.
    ("--taint-sanitizers", {"transforms": None}),
    ("--taint-sanitizers", _sanitizer_file(row={"functions": None})),
    ("--taint-sources", {"taint_label": "plaintext", "sources": None}),
    ("--taint-sinks", {"zone": "host_fs", "sinks": None}),
], ids=["sanitizers", "sources", "sinks", "null-transforms",
        "null-functions", "null-sources", "null-sinks"])
def test_the_well_formed_controls_still_load(
    tmp_path: Path, capsys, flag: str, body: dict[str, Any],
) -> None:
    """Control for the table above: the same files, well formed, are read
    and the run reaches a verdict."""
    good = tmp_path / "good_catalogue.yaml"
    good.write_text(yaml.dump(body))
    rc = _verdict_run(tmp_path, capsys, _fernet_map(), _NO_PLAINTEXT_TO_DISK,
                      extra=[flag, str(good)])
    assert rc != EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# The help states the contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("statement", [
    "0 = all confirmed",
    "1 = at least one violated",
    "2 = bad input",
    "3 = at least one `confirmed_with_caveats` (and none violated or inconclusive)",
    "4 = at least one inconclusive (and none violated)",
    "violated wins, then inconclusive, then caveats",
    "Exit 4 is not a pass: the claim was not checked.",
])
def test_the_help_states_each_code(capsys, statement: str) -> None:
    with pytest.raises(SystemExit):
        main(["verify-claims", "--help"])
    # argparse wraps nothing in a RawDescription epilog, but the epilog itself
    # is hand-wrapped: compare on single spaces.
    help_text = " ".join(capsys.readouterr().out.split())
    assert statement in help_text


# ---------------------------------------------------------------------------
# Names YAML reads as something other than a string
# ---------------------------------------------------------------------------

#: ``- yes``, ``- 404`` and ``- null`` in a name list parse as a bool, an int
#: and None. On dev a SANITIZER name of any of them crashed at load (the index
#: rebuild calls ``"::" in name``), and a SOURCE or SINK name loaded as a row
#: that could never match, then crashed the taint walk (``"." in name``) the
#: moment a taint claim ran. Refused at load now, so the refusal below holds
#: under a boundary-only claim too -- for sources and sinks that run used to
#: exit 0, which makes this a NEW refusal there, not a moved crash.
_NON_STRING_NAMES = [True, 404, None]

_NAME_LISTS: list[tuple[str, Any, str]] = [
    ("--taint-sources", lambda v: _source_file(entry={"functions": [v]}),
     "sources.python[0].functions[0]"),
    ("--taint-sources", lambda v: _source_file(entry={"methods": [v]}),
     "sources.python[0].methods[0]"),
    ("--taint-sinks", lambda v: _sink_file(entry={"functions": [v]}),
     "sinks.python[0].functions[0]"),
    ("--taint-sinks", lambda v: _sink_file(entry={"methods": [v]}),
     "sinks.python[0].methods[0]"),
    ("--taint-sanitizers",
     lambda v: _sanitizer_file(row={"functions": {"python": [v]}}),
     "transforms[0].functions.python[0]"),
]


@pytest.mark.parametrize("value", _NON_STRING_NAMES, ids=["yes", "404", "null"])
@pytest.mark.parametrize(
    "flag,make,needle", _NAME_LISTS,
    ids=[needle for _, _, needle in _NAME_LISTS],
)
def test_a_name_that_is_not_a_string_is_refused_under_a_boundary_only_claim(
    tmp_path: Path, capsys, flag: str, make: Any, needle: str, value: Any,
) -> None:
    bad = tmp_path / "names.yaml"
    bad.write_text(yaml.dump(make(value)))
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(_write_map(tmp_path, _map([_LISTDIR]))),
                            flag, str(bad)])
    _assert_bad_input(rc, err, needle, str(bad))


@pytest.mark.parametrize("value", [True, None], ids=["yes", "null"])
def test_an_argument_index_that_is_not_an_integer_is_refused(
    tmp_path: Path, capsys, value: Any,
) -> None:
    """NEW REFUSAL: on dev ``argument_tainted: [yes]`` / ``[null]`` loaded (a
    bool indexes as 0 or 1, a None matches no argument) and the run reached
    a verdict."""
    bad = tmp_path / "src.yaml"
    bad.write_text(yaml.dump(_source_file(entry={"argument_tainted": [value]})))
    rc, err = _run(capsys, [str(tmp_path), "--claims", str(_ok_claims(tmp_path)),
                            "--input", str(_write_map(tmp_path, _map([_LISTDIR]))),
                            "--taint-sources", str(bad)])
    _assert_bad_input(rc, err, "sources.python[0].argument_tainted[0]")


def test_an_integer_argument_index_still_loads(tmp_path: Path, capsys) -> None:
    good = tmp_path / "src.yaml"
    good.write_text(yaml.dump(_source_file(entry={"argument_tainted": [404]})))
    assert _verdict_run(tmp_path, capsys, _map([_LISTDIR]), _NO_NET,
                        extra=["--taint-sources", str(good)]) == 0


# ---------------------------------------------------------------------------
# Every catalogue hypergumbo ships still loads through the stricter loaders
# ---------------------------------------------------------------------------

_CORE = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core"


@pytest.mark.parametrize("directory,loader", [
    ("taint_sources", "_load_source_yaml"),
    ("taint_sinks", "_load_sink_yaml"),
    ("taint_sanitizers", "_load_sanitizer_yaml"),
])
def test_every_shipped_taint_catalogue_loads(directory: str, loader: str) -> None:
    from hypergumbo_core import taint

    files = sorted((_CORE / directory).glob("*.yaml"))
    assert files, directory
    for path in files:
        loaded = getattr(taint, loader)(path)
        assert loaded, path


def test_the_full_shipped_taint_catalogue_loads() -> None:
    """The four layers the CLI stacks, community files included, with no
    user files: what every run without --taint-* flags loads."""
    from hypergumbo_core.taint import load_full_taint_catalog

    catalog = load_full_taint_catalog([], [], [])
    assert catalog.all_source_labels() and catalog.all_sink_zones()


def test_every_shipped_io_catalogue_loads() -> None:
    """Untouched by this change, checked anyway: each language's I/O
    catalogue with its shipped community overlays."""
    from hypergumbo_core.io_boundary import load_catalog

    files = sorted((_CORE / "io_primitives").glob("*.yaml"))
    assert files
    for path in files:
        assert load_catalog(path.stem).primitives, path
