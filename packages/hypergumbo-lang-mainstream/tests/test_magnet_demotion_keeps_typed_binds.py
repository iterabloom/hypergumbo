# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finalize's magnet demotion keeps binds whose target type was known.

Two ways a correct bind used to be replaced by
``{lang}:external:0-0:<method>:unresolved`` at finalize (step 6c), both for a
method named in the stdlib-interface list (``Close``, ``Parse`` ...):

* WI-sibup -- a C++ call on a receiver whose declaration names its class
  (``Logger lg; lg.Close();``, ``receiver='declared_type'``), because the
  detector's receiver markers did not include that value;
* WI-nafif -- a call anchored on a class (a static field initializer,
  ``static int X = Parse("1");``), because the class anchor's owner was read
  as ``owner_of('P')`` (None), never equal to the method's owner.

Driven through ``run_behavior_map`` so the analyzer, finalize and the
published graph are all in the path.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/receiver_blind_magnets.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map


def _calls(repo: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    for rel, text in files.items():
        (repo / rel).write_text(text)
    out = repo / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    return {
        (names.get(e["src"], e["src"]), names.get(e["dst"], e["dst"]))
        for e in data["edges"] if e["type"] == "calls"
    }


def test_cpp_declared_type_receiver_keeps_its_bind(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {"app.cpp": (
        "class Logger { public: void Close() {} void Flush() {} };\n"
        "class App { public: void run() { Logger lg; lg.Close(); lg.Flush(); } };\n"
    )})
    assert ("App::run", "Logger::Close") in calls
    assert ("App::run", "cpp:external:0-0:Close:unresolved") not in calls
    assert ("App::run", "Logger::Flush") in calls  # control: never demoted


_JAVA = """\
class Outer {
  static int Parse(String s) { return 1; }
  static int X = Parse("1");
  static int fromMethod() { return Parse("2"); }
  static class Inner {
    static int Close(String s) { return 3; }
    static int Z = Close("1");
  }
}
"""

_CSHARP = """\
class Outer {
  static int Parse(string s) { return 1; }
  static int X = Parse("1");
  static int FromMethod() { return Parse("2"); }
}
"""

_KOTLIN = """\
class P {
    fun Parse(s: String): Int = 1
    val x = Parse("1")
    fun fromMethod(): Int = Parse("2")
}
"""


@pytest.mark.parametrize(
    "rel, text, pairs, lang",
    [
        ("Outer.java", _JAVA,
         [("Outer", "Outer.Parse"), ("Outer.Inner", "Outer.Inner.Close"),
          ("Outer.fromMethod", "Outer.Parse")], "java"),
        ("Outer.cs", _CSHARP,
         [("Outer", "Outer.Parse"), ("Outer.FromMethod", "Outer.Parse")], "csharp"),
        ("P.kt", _KOTLIN,
         [("P", "P.Parse"), ("P.fromMethod", "P.Parse")], "kotlin"),
    ],
    ids=["java", "csharp", "kotlin"],
)
def test_class_anchored_call_to_its_own_method_keeps_its_bind(
    tmp_path: Path, rel: str, text: str, pairs: list, lang: str,
) -> None:
    calls = _calls(tmp_path, {rel: text})
    for pair in pairs:
        assert pair in calls, (pair, sorted(calls))
    demoted = {dst for _, dst in calls if dst.startswith(f"{lang}:external:")}
    assert not demoted, demoted
