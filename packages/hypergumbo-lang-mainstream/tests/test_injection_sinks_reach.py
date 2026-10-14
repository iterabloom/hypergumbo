# SPDX-License-Identifier: AGPL-3.0-or-later
"""A value that reaches eval or document.write is a taint flow (WI-nokab, ADR-0060).

Before this, taint shipped no code- or DOM-injection sink: every built-in zone
derived from an I/O boundary and evaluation crosses none, so
``prohibited_sink_zone: code_execution`` was rejected as unknown. JS also
emitted no edge for a bare ``eval(x)``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str,
             label: str, zone: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: {label}\n        prohibited_sink_zone: {zone}\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


@pytest.mark.parametrize("call", ["eval(u);", "window.eval(u);"])
def test_js_page_url_evaluated_as_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str,
) -> None:
    src = f"function f() {{\n  const u = document.location.hash;\n  {call}\n}}\nmodule.exports = {{ f }};\n"
    verdict = _verdict(tmp_path, monkeypatch, "a.js", src, "host_secret", "code_execution")
    assert verdict["verdict"] == "violated", verdict["details"]


def test_js_page_url_written_into_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ("function f() {\n  const u = document.location.href;\n  document.write(u);\n}\n"
           "module.exports = { f };\n")
    verdict = _verdict(tmp_path, monkeypatch, "a.js", src, "host_secret", "dom_injection")
    assert verdict["verdict"] == "violated", verdict["details"]


def test_python_secret_evaluated_as_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = "import os\n\n\ndef f():\n    x = os.environ['EXPR']\n    return eval(x)\n"
    verdict = _verdict(tmp_path, monkeypatch, "a.py", src, "host_secret", "code_execution")
    assert verdict["verdict"] == "violated", verdict["details"]
    assert verdict["evidence"][0]["sink_primitives"] == ["builtins.eval"]


def test_the_zone_is_confirmed_when_nothing_reaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CONTROL: a constant evaluated is not a flow, and the zone is valid."""
    src = "def f():\n    return eval('1 + 1')\n"
    verdict = _verdict(tmp_path, monkeypatch, "a.py", src, "host_secret", "code_execution")
    assert verdict["verdict"] in ("confirmed", "confirmed_with_caveats"), verdict["details"]
