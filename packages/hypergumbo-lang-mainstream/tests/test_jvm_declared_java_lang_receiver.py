# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-dibij: a receiver DECLARED with a bare java.lang type carries java.lang.

``fun f(pb: ProcessBuilder) { pb.start() }`` needs no import (JLS 7.3; kotlin and
scala both import ``java.lang.*`` implicitly), but kotlin's ``_kt_receiver_module``
and scala's typed-receiver call arm accepted only an import or an inline dotted
path, so the call went out as ``external:0-0:start`` and classified as NOTHING.
Measured before the fix through ``verify-claims``: "No subprocess launches" came
back ``confirmed_with_caveats`` (exit 3), the caveat calling the receiver's type
"could not be determined", while the same code spelled ``java.lang.ProcessBuilder``
came back ``violated``. Scala also disagreed with itself: its parameterless arm
(``_library_owner``) already took java.lang's closed list.

The closed list and its shadows are the ones the static-call arm uses
(``static_owner_module``), so a project type, an import of another type of the
same name, or a name kotlin / scala shadow keeps the placeholder. Every
assertion goes through the production analyzers; REACH comes first.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import classify_call, load_catalog

_KOTLIN = '''\
class A {
    fun qualified(pb: java.lang.ProcessBuilder) { pb.start() }
    fun bare(pb: ProcessBuilder) { pb.command() }
    fun runtime(rt: Runtime) { rt.exec("ls") }
    fun shadowed(s: String) { s.trim() }
}
'''

_SCALA = '''\
object A {
  def qualified(pb: java.lang.ProcessBuilder): Unit = { pb.start() }
  def bare(pb: ProcessBuilder): Unit = { pb.command() }
  def runtime(rt: Runtime): Unit = { rt.exec("ls") }
  def shadowed(l: Iterable[Int]): Unit = { l.iterator() }
}
'''

#: The project defines its OWN ``ProcessBuilder``: it wins over java.lang's.
_KOTLIN_OWN = '''\
class ProcessBuilder { fun start(): Int = 1 }
class B { fun own(pb: ProcessBuilder) { pb.start() } }
'''

_SCALA_OWN = '''\
class ProcessBuilder { def start(): Int = 1 }
object B { def own(pb: ProcessBuilder): Unit = { pb.start() } }
'''

#: An explicit import of ANOTHER ``ProcessBuilder`` outranks the implicit one.
_KOTLIN_IMPORTED = '''\
import org.other.ProcessBuilder
class C { fun other(pb: ProcessBuilder) { pb.start() } }
'''

_SCALA_IMPORTED = '''\
import org.other.ProcessBuilder
object C { def other(pb: ProcessBuilder): Unit = { pb.start() } }
'''

_EXT = {"kotlin": "A.kt", "scala": "A.scala"}


def _edges(tmp_path: Path, lang: str, source: str) -> dict[str, object]:
    """``{method name: edge}`` for the calls edges of one fixture."""
    if lang == "kotlin":
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin as analyze
    else:
        from hypergumbo_lang_mainstream.scala import analyze_scala as analyze
    (tmp_path / _EXT[lang]).write_text(source)
    return {edge.dst.split(":")[-2]: edge
            for edge in analyze(tmp_path).edges if edge.edge_type == "calls"}


def _slot(edge) -> str:
    return edge.dst.split(":")[1]


def _classified(lang: str, edge) -> str | None:
    prim = classify_call({lang: load_catalog(lang)}, edge.dst, edge.meta,
                         dst_ref=edge.dst_ref)
    return None if prim is None else f"{prim.module}.{prim.name} {prim.boundary}"


_BOTH = pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN), ("scala", _SCALA)],
                                ids=["kotlin", "scala"])


@_BOTH
class TestABareJavaLangDeclaredType:
    def test_reach_the_qualified_control_classifies(
            self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["start"]
        assert _slot(edge) == "java.lang.ProcessBuilder"
        assert _classified(lang, edge) == "java.lang.ProcessBuilder.start subprocess"

    def test_the_bare_spelling_names_the_same_owner(
            self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["command"]
        assert _slot(edge) == "java.lang.ProcessBuilder"
        assert edge.dst_ref is not None
        assert edge.dst_ref.module_path == "java.lang.ProcessBuilder"
        assert _classified(lang, edge) is not None

    def test_runtime_exec_classifies(self, tmp_path: Path, lang: str, source: str) -> None:
        edge = _edges(tmp_path, lang, source)["exec"]
        assert _slot(edge) == "java.lang.Runtime"
        assert _classified(lang, edge) == "java.lang.Runtime.exec subprocess"

    def test_a_shadowed_java_lang_name_keeps_the_placeholder(
            self, tmp_path: Path, lang: str, source: str) -> None:
        name = "trim" if lang == "kotlin" else "iterator"
        edges = _edges(tmp_path, lang, source)
        assert name in edges, "reach: the call must still be emitted"
        assert not _slot(edges[name]).startswith("java.lang")


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_OWN), ("scala", _SCALA_OWN)],
                         ids=["kotlin", "scala"])
def test_a_project_type_shadows_java_lang(tmp_path: Path, lang: str, source: str) -> None:
    """The call binds the project's own method, and no edge names java.lang."""
    edges = _edges(tmp_path, lang, source)
    assert edges["ProcessBuilder.start"].is_resolved, sorted(edges)
    assert not any("java.lang" in edge.dst for edge in edges.values())


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_IMPORTED),
                                         ("scala", _SCALA_IMPORTED)],
                         ids=["kotlin", "scala"])
def test_an_import_of_another_type_outranks_java_lang(
        tmp_path: Path, lang: str, source: str) -> None:
    assert _slot(_edges(tmp_path, lang, source)["start"]) == "org.other.ProcessBuilder"


_CLAIMS = ("claims:\n  - id: NO-SPAWN\n    text: No subprocess launches\n"
           "    constraint:\n      boundary: subprocess\n      must_not_exist: true\n")


@pytest.mark.parametrize("lang,source", [
    ("kotlin", "fun f(pb: ProcessBuilder) {\n    pb.start()\n}\n"),
    ("scala", "object A {\n  def f(pb: ProcessBuilder): Unit = { pb.start() }\n}\n"),
], ids=["kotlin", "scala"])
def test_verify_claims_sees_the_launch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str, source: str) -> None:
    """The filed consequence, end to end: the claim is VIOLATED, not confirmed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / _EXT[lang]).write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "violated", verdict
