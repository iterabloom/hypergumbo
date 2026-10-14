# SPDX-License-Identifier: AGPL-3.0-or-later
"""A harmless call site must not erase a real one in the same function (INV-rajak).

Two calls to one callee from one function collapse into one edge, and the edge
used to remember only the per-site values sites HAD. Measured on the shipped
CLI before this change, each pair differing by one added line:

* Go: ``bufio.NewScanner(r)`` over a parameter is an ``ipc_recv`` chain and an
  ``untrusted_input`` source. Add ``bufio.NewScanner(strings.NewReader(s))``
  above it and both are gone: the taint verdict went ``violated`` (2 flows) to
  ``inconclusive`` (0 flows).
* Python: ``json.load(open(p))`` reads a file. Add ``open(q, "w")`` and a
  ``must_not_exist: fs_read`` claim CONFIRMED, exit 0.

Each test runs the harmless-sibling file; the one-site file is the control.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_GO_MOD = "module example.com/r\n\ngo 1.21\n"

_GO_BOUNDARY = '''package r

import (
\t"bufio"
\t"io"
\t"strings"
)

func F(s string, r io.Reader) int {{
\t{sibling}
\tb := bufio.NewScanner(r)
\tn := 0
\tfor b.Scan() {{
\t\tn++
\t}}
\treturn n
}}
'''

_GO_TAINT = '''package r

import (
\t"bufio"
\t"io"
\t"os/exec"
\t"strings"
)

func F(s string, r io.Reader) {{
\t{sibling}
\tb := bufio.NewScanner(r)
\tfor b.Scan() {{
\t\texec.Command(b.Text()).Run()
\t}}
}}
'''

_IN_MEMORY = "a := bufio.NewScanner(strings.NewReader(s)); _ = a"
_NO_SIBLING = "_ = strings.ToUpper(s)"


def _run(argv: list[str], cache: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue()


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path, claim: str) -> dict:
    claims = tmp_path / "claims.yaml"
    claims.write_text("claims:\n  - id: C\n    text: t\n    constraint:\n" + claim)
    out = _run(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    (verdict,) = json.loads(out)["verdicts"]
    return verdict


def _go_repo(tmp_path: Path, template: str, sibling: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(_GO_MOD)
    (repo / "f.go").write_text(template.format(sibling=sibling))
    return repo


@pytest.mark.parametrize("sibling", [_NO_SIBLING, _IN_MEMORY])
def test_the_parameter_scanner_still_receives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str,
) -> None:
    repo = _go_repo(tmp_path, _GO_BOUNDARY, sibling)
    out = _run(["io-boundaries", str(repo), "--format", "json"],
               tmp_path / "cache", monkeypatch)
    chains = {b: {c["primitive"] for c in v["chains"]}
              for b, v in json.loads(out)["boundaries"].items()}
    assert "bufio.NewScanner" in chains.get("ipc_recv", set()), chains


@pytest.mark.parametrize("sibling", [_NO_SIBLING, _IN_MEMORY])
def test_the_parameter_scanner_still_mints_untrusted_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str,
) -> None:
    repo = _go_repo(tmp_path, _GO_TAINT, sibling)
    verdict = _verdict(tmp_path, monkeypatch, repo,
                       "      taint_flow:\n        source_taint: untrusted_input\n"
                       "        prohibited_sink_zone: subprocess\n")
    assert verdict["verdict"] == "violated", verdict["details"]


@pytest.mark.parametrize("sibling", ["", '    open(q, "w").close()\n'])
def test_a_write_beside_a_read_does_not_confirm_no_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "import json\n\n\ndef f(p, q):\n    cfg = json.load(open(p))\n"
        f"{sibling}    return cfg\n"
    )
    verdict = _verdict(tmp_path, monkeypatch, repo,
                       "      boundary: fs_read\n      must_not_exist: true\n")
    assert verdict["verdict"] == "violated", verdict["details"]
