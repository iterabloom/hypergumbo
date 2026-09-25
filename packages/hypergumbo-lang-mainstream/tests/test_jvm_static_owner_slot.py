# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kilap: a kotlin / scala static call carries its owner in the module slot.

``Files.readAllBytes(p)``, ``System.getenv(k)`` and ``Instant.now()`` are called
on the TYPE. java writes that owner into the slot (INV-suril, INV-hahak). kotlin
and scala emitted the bare method under ``external``, explicit import or not,
so every catalogued JDK static classified as NOTHING in those two languages.
Reproduced before the fix through the production analyzers and
``classify_call``, with ``println`` / ``File.delete`` classifying as controls.

Every assertion below goes through the production path. It runs the analyzer
on a real source file, then ``classify_call`` with the edge's ``dst_ref``, as
``verify_claims`` calls it. REACH comes first in each class. A fixture that
emitted no edge would pass every "stays external" control vacuously.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.jvm_implicit_imports import (
    JAVA_LANG_TYPES,
    KOTLIN_SHADOWED_JAVA_LANG,
    SCALA_SHADOWED_JAVA_LANG,
    static_owner_module,
)

_KOTLIN = '''\
import java.nio.file.Files
import java.nio.file.Paths
import java.time.Instant

class System2 { fun x() {} }

class A {
    fun explicitImport(p: String): ByteArray {
        return Files.readAllBytes(Paths.get(p))
    }
    fun implicitJavaLang(): String? {
        return System.getenv("HOME")
    }
    fun clock(): Long {
        return Instant.now().toEpochMilli()
    }
    fun shadowed(): String {
        return String.format("%s", "x")
    }
    fun unimported() {
        Helper.doThing()
    }
    fun untyped(h: Any) {
        val q = make()
        q.untypedCall()
    }
    fun control(s: String) {
        println(s)
    }
}
'''

_SCALA = '''\
import java.nio.file.{Files, Paths}
import java.time.Instant
import scala.util.Properties

object A {
  def explicitImport(p: String): Array[Byte] = Files.readAllBytes(Paths.get(p))
  def implicitJavaLang(): String = System.getenv("HOME")
  def clock(): Instant = Instant.now()
  def props(): String = Properties.envOrElse("HOME", "/")
  def shadowed(): Long = Long.parseLong("1")
  def unimported(): Unit = Helper.doThing()
  def untyped(): Unit = { val q = make(); q.untypedCall() }
  def control(p: String): Unit = { val f = new java.io.File(p); f.delete() }
}
'''

#: A project that defines its OWN ``System``: that type wins over java.lang's in
#: both languages, so the slot must not claim ``java.lang.System``.
_KOTLIN_OWN_SYSTEM = '''\
object System {
    fun ping(): Int = 1
}

class B {
    fun own(): String? = System.getenv("HOME")
}
'''

_SCALA_OWN_SYSTEM = '''\
object System {
  def ping(): Int = 1
}

object B {
  def own(): String = System.getenv("HOME")
}
'''


def _edges(tmp_path: Path, lang: str, source: str) -> dict[str, object]:
    """``{method name: edge}`` for the unresolved call edges of one fixture."""
    if lang == "kotlin":
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin as analyze
        (tmp_path / "A.kt").write_text(source)
    else:
        from hypergumbo_lang_mainstream.scala import analyze_scala as analyze
        (tmp_path / "A.scala").write_text(source)
    out: dict[str, object] = {}
    for edge in analyze(tmp_path).edges:
        if edge.edge_type == "calls" and not edge.is_resolved:
            out[edge.dst.split(":")[-2]] = edge
    return out


def _slot(edge) -> str:
    return edge.dst.split(":")[1]


def _classified(lang: str, edge) -> str | None:
    prim = classify_call({lang: load_catalog(lang)}, edge.dst, edge.meta,
                         dst_ref=edge.dst_ref)
    return None if prim is None else f"{prim.module}.{prim.name} {prim.boundary}"


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN), ("scala", _SCALA)],
                         ids=["kotlin", "scala"])
class TestTheOwnerReachesTheSlot:
    def test_reach_the_control_classifies(self, tmp_path: Path, lang: str, source: str) -> None:
        edges = _edges(tmp_path, lang, source)
        name = "println" if lang == "kotlin" else "delete"
        assert _classified(lang, edges[name]) is not None, sorted(edges)

    def test_an_explicit_import_names_the_owner(self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["readAllBytes"]
        assert _slot(edge) == "java.nio.file.Files"
        assert _classified(lang, edge) == "java.nio.file.Files.readAllBytes fs_read"

    def test_java_lang_names_the_owner_without_an_import(
            self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["getenv"]
        assert _slot(edge) == "java.lang.System"
        assert _classified(lang, edge) == "java.lang.System.getenv env_read"

    def test_a_clock_read_classifies(self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["now"]
        assert _classified(lang, edge) == "java.time.Instant.now host_info_read"

    def test_the_construct_stamp_is_unchanged(self, tmp_path: Path, lang: str, source: str) -> None:
        """``call_construct`` records the call's SYNTAX and gates the sanitizer
        channel (#214); filling the slot must not drop it."""
        edge = _edges(tmp_path, lang, source)["readAllBytes"]
        assert (edge.meta or {}).get("call_construct") == "method"

    def test_the_edge_carries_a_structured_ref(self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["getenv"]
        assert edge.dst_ref is not None and edge.dst_ref.module_path == "java.lang.System"

    def test_an_unimported_type_keeps_the_placeholder(
            self, tmp_path: Path, lang: str, source: str) -> None:
        """INV-fazim: a bare simple name in the slot asserts a module that does
        not exist."""
        assert _slot(_edges(tmp_path, lang, source)["doThing"]) == "external"

    def test_an_untyped_value_receiver_keeps_the_placeholder(
            self, tmp_path: Path, lang: str, source: str) -> None:
        """A lowercase local is a VALUE, not a type: nothing to name."""
        assert _slot(_edges(tmp_path, lang, source)["untypedCall"]) == "external"

    def test_a_shadowed_java_lang_name_keeps_the_placeholder(
            self, tmp_path: Path, lang: str, source: str) -> None:
        edges = _edges(tmp_path, lang, source)
        name = "format" if lang == "kotlin" else "parseLong"
        assert _slot(edges[name]) == "external"


def test_scala_object_import_names_the_owner(tmp_path: Path) -> None:
    edge = _edges(tmp_path, "scala", _SCALA)["envOrElse"]
    assert _slot(edge) == "scala.util.Properties"
    assert _classified("scala", edge) == "scala.util.Properties.envOrElse env_read"


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_OWN_SYSTEM),
                                         ("scala", _SCALA_OWN_SYSTEM)],
                         ids=["kotlin", "scala"])
def test_a_project_type_shadows_java_lang(tmp_path: Path, lang: str, source: str) -> None:
    edges = _edges(tmp_path, lang, source)
    assert "getenv" in edges, "reach: the call must still be emitted"
    assert _slot(edges["getenv"]) != "java.lang.System"
    assert _classified(lang, edges["getenv"]) is None


_KOTLIN_IMPORTED_PROJECT_CLASS = {
    "api/Issue.kt": """\
package dev.acme.api

class Issue {
    class Entity(val name: String)
}
""",
    "core/Use.kt": """\
package dev.acme.core

import dev.acme.api.Issue

class Use {
    fun make(): Any = Issue.Entity("x")
}
""",
}


def test_a_call_on_an_imported_project_class_still_resolves(tmp_path: Path) -> None:
    """THE REGRESSION the first version of this change shipped on detekt: the
    nested-class constructor on an imported PROJECT class must keep resolving
    to the project symbol, not leave with the import path as an external."""
    from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

    for rel, text in _KOTLIN_IMPORTED_PROJECT_CLASS.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    calls = [e for e in analyze_kotlin(tmp_path).edges
             if e.edge_type == "calls" and "Entity" in e.dst]
    assert calls, "reach: the constructor call must be emitted"
    assert not any(":dev.acme.api.Issue:" in e.dst for e in calls), [e.dst for e in calls]


class TestTheResolver:
    def test_an_import_wins(self) -> None:
        assert static_owner_module(
            "Files", {"Files": "java.nio.file.Files"},
            shadowed=frozenset(), is_project_type=False) == "java.nio.file.Files"

    def test_an_imported_project_type_keeps_the_placeholder(self) -> None:
        """A project type is resolved by the Tier-2 linkers from the
        placeholder; its import path in the slot lost detekt 4 resolved calls."""
        assert static_owner_module(
            "Issue", {"Issue": "dev.detekt.api.Issue"},
            shadowed=frozenset(), is_project_type=True) is None

    def test_a_bare_import_value_is_not_a_path(self) -> None:
        """scala records ``import Parser.*`` as ``Parser -> "Parser"``."""
        assert static_owner_module(
            "Parser", {"Parser": "Parser"},
            shadowed=frozenset(), is_project_type=False) is None

    def test_java_lang(self) -> None:
        assert static_owner_module(
            "Runtime", {}, shadowed=frozenset(), is_project_type=False,
        ) == "java.lang.Runtime"

    @pytest.mark.parametrize("name,shadowed,project", [
        ("String", KOTLIN_SHADOWED_JAVA_LANG, False),
        ("Long", SCALA_SHADOWED_JAVA_LANG, False),
        ("System", frozenset(), True),
        ("Helper", frozenset(), False),
        ("files", frozenset(), False),
        ("a.b", frozenset(), False),
        ("", frozenset(), False),
    ])
    def test_none(self, name: str, shadowed: frozenset[str], project: bool) -> None:
        assert static_owner_module(
            name, {}, shadowed=shadowed, is_project_type=project) is None

    def test_the_shadow_lists_name_real_java_lang_types(self) -> None:
        """A shadow entry that is not in the closed list shadows nothing, so it
        is a typo, not a decision."""
        assert KOTLIN_SHADOWED_JAVA_LANG <= JAVA_LANG_TYPES
        assert SCALA_SHADOWED_JAVA_LANG <= JAVA_LANG_TYPES
