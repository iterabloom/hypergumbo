# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call on a bare PROJECT object or companion resolves (WI-kipol).

``Factory.make()`` with ``object Factory { def make() }`` in the project was
left ``scala:external:0-0:make:unresolved`` with no hint: the receiver is not
a variable, and the static-owner slot deliberately names no project type. Now
the type-qualified lookup runs for a capitalised receiver that is a project
type and no local binds -- but only where Scala would bind it: the same file,
an explicit import whose path fits the symbol (WI-tipoh), or the caller's own
package. Same-named objects in other packages are common (lila: 74 ``Env``),
so a symbol from an unrelated package with no import is NOT bound.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _calls(tmp_path: Path, files: dict[str, str]) -> dict[str, list[str]]:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    found: dict[str, list[str]] = {}
    for e in data["edges"]:
        src = nodes.get(e["src"])
        if e["type"] == "calls" and src is not None:
            dst = nodes.get(e["dst"])
            label = f'{dst["path"]}:{dst["name"]}' if dst else e["dst"]
            found.setdefault(src["name"], []).append(label)
    return found


def test_a_project_object_call_resolves_where_scala_binds_it(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "a/Factory.scala": "package app\nobject Factory {\n  def make(): Int = 1\n}\n",
        "a/Use.scala": (
            "package app\n"
            "object Use {\n"
            "  def go(): Int = Factory.make()\n"
            "  def local(): Int = Helper.help()\n"
            "}\n"
            "object Helper {\n  def help(): Int = 2\n}\n"
        ),
        "b/Factory.scala": "package other\nobject Factory {\n  def make(): Int = 3\n}\n",
        "c/Imp.scala": (
            "package third\n"
            "import other.Factory\n"
            "object Imp {\n  def go(): Int = Factory.make()\n}\n"
        ),
    })
    assert calls["Use.go"] == ["a/Factory.scala:Factory.make"], "same package"
    assert calls["Use.local"] == ["a/Use.scala:Helper.help"], "same file"
    assert calls["Imp.go"] == ["b/Factory.scala:Factory.make"], "explicit import"


def test_an_unrelated_package_without_an_import_is_not_bound(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "b/Factory.scala": "package other\nobject Factory {\n  def make(): Int = 3\n}\n",
        "d/Lone.scala": "package fourth\nobject Lone {\n  def go(): Int = Factory.make()\n}\n",
    })
    assert not any(t.startswith("b/Factory.scala") for t in calls["Lone.go"]), calls


def test_an_inherited_member_is_hinted_for_the_linker(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "a/T.scala": (
            "package app\n"
            "trait Base {\n  def make(): Int = 1\n}\n"
            "object Factory extends Base\n"
            "object Use {\n  def go(): Int = Factory.make()\n}\n"
        ),
    })
    # The linker adds the resolved edge beside the hinted placeholder.
    assert "a/T.scala:Base.make" in calls["Use.go"]


def test_an_object_records_its_imported_bases(tmp_path: Path) -> None:
    """Objects now record ``base_classes`` (and the import each bare base came
    through), like classes and traits (WI-pivap's check then applies)."""
    from hypergumbo_lang_mainstream.scala import analyze_scala

    (tmp_path / "Actor.scala").write_text("package org.acme\nclass Actor\n")
    (tmp_path / "Main.scala").write_text(
        "package org.acme.app\nimport akka.actor.Actor\nobject Main extends Actor\n"
    )
    main = next(s for s in analyze_scala(tmp_path).symbols if s.name == "Main")
    assert main.meta == {
        "base_classes": ["Actor"],
        "base_class_imports": {"Actor": "akka.actor.Actor"},
    }
