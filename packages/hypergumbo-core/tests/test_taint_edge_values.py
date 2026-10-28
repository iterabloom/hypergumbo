# SPDX-License-Identifier: AGPL-3.0-or-later
"""The taint walk's edges carry a value, and a type node holds none (WI-kakup, WI-tibib).

(1) Every member of ``TAINT_CALL_EDGE_TYPES`` says what value it carries
(``TAINT_EDGE_VALUE``). The call family is derived from the edge-type
registry, so a new call-family edge type reaches taint automatically; this
test is what makes that addition answer the question INV-putug found
unasked for ``dispatches_to``.

(2) A value that REACHES a type node stops there. ``C(data)`` passes ``data``
to the instance -- the initializer, through ``construction_calls`` -- while
the ``instantiates`` edge lands on the class, and the class's own anchored
calls (a Python class body, which ran at import) never see it. Before this,
verify-claims reported ``def f(): data = input(); return C(data)`` violated
through ``class C: y = eval(EXPR)``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.taint import (
    TAINT_CALL_EDGE_TYPES,
    TAINT_EDGE_VALUE,
    _reachability_past_sanitizers,
)


def test_every_taint_edge_type_says_what_value_it_carries() -> None:
    assert set(TAINT_EDGE_VALUE) == set(TAINT_CALL_EDGE_TYPES)


_F = "python:app.py:5-6:f:function"
_C = "python:app.py:1-2:C:class"
_EVAL = "python:builtins:0-0:eval:function"
_INIT = "python:app.py:2-2:C.__init__:method"


def test_a_value_reaching_a_type_node_goes_no_further() -> None:
    forward = {_F: {_C, _INIT}, _C: {_EVAL}}
    reachable, *_ = _reachability_past_sanitizers(_F, "untrusted_input", forward, {})
    assert _INIT in reachable  # reach: the initializer hop still carries it
    assert _C not in reachable and _EVAL not in reachable


def test_a_type_node_that_is_the_seed_is_walked() -> None:
    """A class body that reads a source itself is where the flow starts."""
    reachable, *_ = _reachability_past_sanitizers(_C, "untrusted_input", {_C: {_EVAL}}, {})
    assert {_C, _EVAL} <= reachable


def test_past_a_sanitizer_a_type_node_still_stops_the_walk() -> None:
    from hypergumbo_core.taint import TaintSanitizer

    san = TaintSanitizer(input_taint="untrusted_input", output_taint="x", qualified_name="s")
    g = "python:app.py:8-9:g:function"
    forward = {_F: {g}, g: {_C}, _C: {_EVAL}}
    _, _, sanitized, *_ = _reachability_past_sanitizers(
        _F, "untrusted_input", forward, {g: {"untrusted_input": [san]}})
    assert g in sanitized  # reach
    assert _C not in sanitized and _EVAL not in sanitized


_CLAIM = ("claims:\n  - id: c\n    text: t\n    constraint:\n      taint_flow:\n"
          "        source_taint: untrusted_input\n        prohibited_sink_zone: code_execution\n")


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: str) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(app)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return json.loads(buf.getvalue())["verdicts"][0]["verdict"]


def test_constructing_a_class_does_not_run_its_body_on_the_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ("EXPR = '1'\n\n\nclass C:\n    y = eval(EXPR)\n\n    def __init__(self, v):\n"
           "        self.v = v\n\n\ndef f():\n    data = input()\n    return C(data)\n")
    assert _verdict(tmp_path, monkeypatch, app) != "violated"


def test_control_a_value_passed_to_a_function_that_evaluates_it_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = "def g(x):\n    return eval(x)\n\n\ndef f():\n    data = input()\n    return g(data)\n"
    assert _verdict(tmp_path, monkeypatch, app) == "violated"


def test_control_a_constructor_that_evaluates_its_argument_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The initializer hop (WI-satal) still carries the value."""
    app = ("class C:\n    def __init__(self, v):\n        eval(v)\n\n\n"
           "def f():\n    data = input()\n    return C(data)\n")
    assert _verdict(tmp_path, monkeypatch, app) == "violated"
