# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gotun: java's def/use extractor, per statement and on the production path.

Until this extractor, java had a CFG mapping and 69 catalogued sinks but no
def/use, so ``dataflow_coverage`` reported java ``dataflow_capable: false`` and
every java taint finding carried ``analysis_method: structural`` with the walk
``unavailable``: a real flow and a mere co-location read the same.

The unit half pins what each statement defines and uses. The production half
runs ``verify-claims`` and pins what a reader of the verdict can now tell apart,
and that the DDG names its functions exactly as the analyzer does (a DDG keyed
on ids the call graph never emitted would be inert).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cli import main
from hypergumbo_lang_mainstream.java_def_use import JavaDefUseExtractor


def _statement(body: str) -> tuple[Any, bytes]:
    """Parse ``body`` as the only statement of a method and return it."""
    src = ("class A {\n  void m() {\n    " + body + "\n  }\n}\n").encode()
    tree = tree_sitter.Parser(get_language("java")).parse(src)
    method = tree.root_node.children[0].child_by_field_name("body").named_children[0]
    return method.child_by_field_name("body").named_children[0], src


@pytest.mark.parametrize(("body", "defines", "uses"), [
    ("byte[] pt = c.doFinal(ct);", ["pt"], ["c", "ct"]),
    ("int a = x, b[] = {a, y};", ["a", "b"], ["a", "x", "y"]),
    ("var e = new Foo<String>(pt);", ["e"], ["pt"]),
    ("x = y + 1;", ["x"], ["y"]),
    ("x += z;", ["x"], ["x", "z"]),
    ("this.f = pt;", ["this"], ["pt", "this"]),
    ("a.b.c = v;", ["a"], ["a", "v"]),
    ("arr[i] = v;", ["arr"], ["arr", "i", "v"]),
    ("i++;", ["i"], ["i"]),
    ("System.out.println(Map.class);", [], ["System"]),
    ("Foo.<String>bar(t);", [], ["Foo", "t"]),
    ("Object z = (String) cast;", ["z"], ["cast"]),
    ("int y = cond ? a1 : b1;", ["y"], ["a1", "b1", "cond"]),
    ("Runnable k = obj::go;", ["k"], ["obj"]),
    ("throw new X(pt);", [], ["pt"]),
    ("assert pt != null;", [], ["pt"]),
    ("(a).f = v;", ["a"], ["a", "v"]),
])
def test_a_statement_defines_its_targets_and_uses_its_reads(
    body: str, defines: list[str], uses: list[str],
) -> None:
    node, src = _statement(body)
    result = JavaDefUseExtractor().extract(node, src)
    assert sorted(result.defines) == sorted(defines)
    assert sorted(set(result.uses)) == sorted(uses)


def test_an_assignment_inside_a_condition_defines() -> None:
    """``while ((line = r.readLine()) != null)``: the CFG hands the extractor
    only the condition, and the stream-reading idiom binds ``line`` there."""
    loop, src = _statement("while ((line = r.readLine()) != null) { use(line); }")
    cond = loop.child_by_field_name("condition")
    result = JavaDefUseExtractor().extract(cond, src)
    assert result.defines == ["line"]
    assert result.uses == ["r"]


def test_a_lambda_reads_what_it_captures_and_defines_nothing() -> None:
    """A lambda may not assign an enclosing local, so its bindings are not this
    function's. What it captures is still read where the lambda is created."""
    node, src = _statement("Runnable r = () -> { int q = pt; this.f = q; };")
    result = JavaDefUseExtractor().extract(node, src)
    assert result.defines == ["r"]
    assert "pt" in result.uses


def test_try_resources_and_catch_parameters_bind() -> None:
    node, src = _statement(
        "try (FileOutputStream o = new FileOutputStream(p)) { o.write(e); }"
        " catch (IOException ex) { log(ex); }"
    )
    resource = node.child_by_field_name("resources").named_children[0]
    catch = next(c for c in node.named_children if c.type == "catch_clause")
    param = catch.named_children[0]
    extractor = JavaDefUseExtractor()
    assert extractor.extract(resource, src).defines == ["o"]
    assert extractor.extract(resource, src).uses == ["p"]
    assert extractor.extract(param, src).defines == ["ex"]


def test_a_resource_naming_an_existing_variable_only_reads_it() -> None:
    """Java 9's ``try (out)`` closes a variable declared earlier."""
    node, src = _statement("try (out) { out.write(e); }")
    resource = node.child_by_field_name("resources").named_children[0]
    result = JavaDefUseExtractor().extract(resource, src)
    assert result.defines == []
    assert result.uses == ["out"]


def test_a_write_through_a_call_result_defines_nothing() -> None:
    node, src = _statement("make().x = v;")
    result = JavaDefUseExtractor().extract(node, src)
    assert result.defines == []
    assert result.uses == ["v"]


# --------------------------------------------------------------------------
# Production path
# --------------------------------------------------------------------------

_SECRET_CLAIM = """claims:
  - id: SECRET-FS
    text: Secrets never reach a filesystem write.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
"""

_FLOW = """import java.io.FileWriter;

class Env {
    void dump() throws Exception {
        String v = System.getenv("API_KEY");
        String w = v.trim();
        FileWriter f = new FileWriter("o.txt");
        f.write(w);
    }
}
"""

#: The control: the secret is read and the file is written in one method, but
#: nothing the write receives came from the read.
_CO_LOCATED = """import java.io.FileWriter;

class Env {
    void dump() throws Exception {
        String v = System.getenv("API_KEY");
        System.out.println(v.length());
        String w = "constant";
        FileWriter f = new FileWriter("o.txt");
        f.write(w);
    }
}
"""


def _verify(tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str],
            monkeypatch: pytest.MonkeyPatch) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Env.java").write_text(source)
    (tmp_path / "claims.yaml").write_text(_SECRET_CLAIM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
          "--format", "json"])
    return json.loads(capsys.readouterr().out)


def test_java_is_data_flow_capable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _verify(tmp_path, _FLOW, capsys, monkeypatch)
    [java] = [r for r in out["dataflow_coverage"]["languages"] if r["language"] == "java"]
    assert java["dataflow_capable"] is True
    assert java["blockers"] == []


def test_a_real_flow_is_confirmed_by_the_ddg(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [finding] = _verify(tmp_path, _FLOW, capsys, monkeypatch)["verdicts"][0]["evidence"]
    assert finding["analysis_method"] == "ddg"
    assert finding["walk_verdict"] == "confirmed"


def test_a_co_located_non_flow_is_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still reported (the walk only subtracts on a definite refutation, and a
    read passed to a call escapes), but no longer labelled as a data flow."""
    [finding] = _verify(tmp_path, _CO_LOCATED, capsys, monkeypatch)["verdicts"][0]["evidence"]
    assert finding["analysis_method"] == "ddg_mixed"
    assert finding["walk_verdict"] != "confirmed"


def test_the_ddg_names_functions_as_the_analyzer_does(tmp_path: Path) -> None:
    """Nested classes, a constructor, an overload pair and an enum method: every
    function the DDG covers must be a symbol the java analyzer emitted, or the
    taint BFS never looks it up. An anonymous class's method is named on its
    enclosing class by both. A record's method gets no symbol from the
    analyzer (``record_declaration`` is not an enclosing type to
    ``_get_class_ancestors``), so the DDG must skip it (6, not 7) rather than
    mint an id nobody emitted."""
    from hypergumbo_core.ddg_build import build_repo_ddg
    from hypergumbo_lang_mainstream.java import analyze_java

    (tmp_path / "Outer.java").write_text(
        "class Outer {\n"
        "  Outer(int a) { int b = a; int c = b; }\n"
        "  void f(int x) { int y = x; int z = y; }\n"
        "  void f(String s) { String t = s; String u = t; }\n"
        "  static class Inner {\n"
        "    void g(int x) { int y = x; int z = y; }\n"
        "  }\n"
        "  enum E { A; void h(int x) { int y = x; int z = y; } }\n"
        "  Runnable r = new Runnable() {\n"
        "    public void run() { int y = 1; int z = y; }\n"
        "  };\n"
        "}\n"
    )
    (tmp_path / "R.java").write_text(
        "record R(int a) {\n"
        "  int twice() { int y = a; int z = y; return z; }\n"
        "}\n"
    )
    ddg = build_repo_ddg(tmp_path, ("java",))
    # Called directly, the analyzer emits absolute paths; the orchestrator
    # relativises them (INV-buhur), so do the same here.
    emitted = {s.id.replace(f"{tmp_path}/", "") for s in analyze_java(tmp_path).symbols}
    assert len(ddg.ddg_symbols) == 6
    assert ddg.ddg_symbols <= emitted, ddg.ddg_symbols - emitted
