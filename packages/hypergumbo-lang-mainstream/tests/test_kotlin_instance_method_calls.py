# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Kotlin instance-method call on an external receiver emits a call edge
carrying the receiver's declared type (WI-nasuf's kotlin cell).

WI-nasuf, read in code and confirmed on okhttp and detekt: the
``navigation_expression`` branch resolved ``instance.method()`` only when
``resolver.lookup(...)`` found an in-repo symbol (Case 3), then tried an
extension function (Case 3b) and a qualified fallback (Case 4, also gated on
``found``), and emitted NOTHING otherwise. A JDK method is never in the repo's
registry, so ``File(p).writeText(s)`` emitted the constructor edge and
nothing for the method, in both the two-step and the chained form. 89 of
kotlin's 93 catalogued sinks are method-kind, so essentially the whole
catalogue was unreachable through the shape real code uses; okhttp's 573
Kotlin files produced 49 io-boundary chains, every one a bare ``println``.

The fix mirrors java (INV-vugon) and javascript (INV-misup): a typed receiver
whose in-repo lookup misses emits an unresolved edge whose module slot is the
type qualified through the file's imports (or written inline), with
``call_construct: method`` and ``receiver_type_hint`` for the Tier-2 linkers;
an untyped receiver emits the ``external`` placeholder; and
``analyzer_disclosure`` flips kotlin's dated blindness declaration per the
owner's 2026-08-23 ruling. kotlin inherits java's catalogue
(``_CATALOG_PARENTS``), so ``java.sql.Connection.commit`` is a kotlin sink too.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def _edges(root: Path, source: str, name: str = "A.kt") -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(source)
    return analyze_kotlin(root).edges


def _call(edges: list[Edge], method: str, *, line: int | None = None) -> Edge:
    hits = [
        e for e in edges
        if e.edge_type == "calls"
        and e.dst.split(":")[-2].split(".")[-1] == method
        and (line is None or e.line == line)
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges if method in e.dst]
    return hits[0]


def _tagged(edges: list[Edge]) -> int:
    return tag_io_boundaries(edges, {"kotlin": load_catalog("kotlin")})


class TestTypedReceiverNamesItsModule:
    def test_two_step_and_chained_file_write(self, tmp_path: Path) -> None:
        """WI-nasuf's own fixture: the constructor emitted, the method did not."""
        edges = _edges(
            tmp_path / "f",
            "import java.io.File\n"
            "\n"
            "fun go(p: String, s: String) {\n"
            "    val f = File(p)\n"
            "    f.writeText(s)\n"
            "    File(p).writeText(s)\n"
            "}\n",
        )
        two_step = _call(edges, "writeText", line=5)
        chained = _call(edges, "writeText", line=6)
        for e in (two_step, chained):
            assert e.dst == "kotlin:java.io.File:0-0:writeText:unresolved", e.dst
            assert (e.meta or {}).get("call_construct") == "method"
        assert (two_step.meta or {}).get("receiver_type_hint") == "File"
        assert _tagged(edges) >= 2

    def test_parameter_and_declared_types(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "d",
            "import java.io.File\n"
            "import java.sql.Connection\n"
            "\n"
            "fun read(f: File): String = f.readText()\n"
            "\n"
            "fun commit(make: () -> Connection) {\n"
            "    val c: Connection = make()\n"
            "    c.commit()\n"
            "}\n",
        )
        assert _call(edges, "readText").dst == "kotlin:java.io.File:0-0:readText:unresolved"
        assert _call(edges, "commit").dst == "kotlin:java.sql.Connection:0-0:commit:unresolved"

    def test_inline_qualified_type(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "q",
            "fun go(p: String) {\n"
            "    val f = java.io.File(p)\n"
            "    f.readText()\n"
            "}\n",
        )
        assert _call(edges, "readText").dst == "kotlin:java.io.File:0-0:readText:unresolved"

    def test_the_edge_reaches_the_inherited_java_catalogue(self, tmp_path: Path) -> None:
        from hypergumbo_core.taint import (
            _build_callee_index,
            _match_propagation_entry,
            load_builtin_taint_catalog,
        )

        edges = _edges(
            tmp_path / "k",
            "import java.sql.Connection\n"
            "\n"
            "fun commit(c: Connection) {\n"
            "    c.commit()\n"
            "}\n",
        )
        edge = _call(edges, "commit")
        catalog = load_builtin_taint_catalog()
        index = _build_callee_index(catalog.sinks_for_language("kotlin"))
        ambiguous = catalog.ambiguous_names_for_language("kotlin")
        matched = _match_propagation_entry(
            index, edge.dst, ambiguous,
            (edge.meta or {}).get("call_construct"),
            is_resolved=edge.is_resolved, language="kotlin",
        )
        assert matched is not None, edge.dst
        assert matched.qualified_name == "java.sql.Connection.commit"

    def test_a_project_class_method_still_resolves_in_repo(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "p",
            "class Helper {\n"
            "    fun run() {}\n"
            "}\n"
            "\n"
            "fun go() {\n"
            "    val h = Helper()\n"
            "    h.run()\n"
            "}\n",
        )
        runs = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":Helper.run:method")]
        assert runs, [e.dst for e in edges]

    def test_a_nested_project_class_constructed_by_its_dotted_name_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """Read back from okhttp: ``val b = Request.Builder(); b.url(x)`` resolved
        to ``Builder.url`` in the old arm and went unresolved once the receiver
        carried the dotted type -- kotlin keys nested methods by the inner class."""
        edges = _edges(
            tmp_path / "nest",
            "class Request {\n"
            "    class Builder {\n"
            "        fun url(u: String): Builder = this\n"
            "    }\n"
            "}\n"
            "\n"
            "fun go(u: String) {\n"
            "    val b = Request.Builder()\n"
            "    b.url(u)\n"
            "}\n",
        )
        urls = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":Builder.url:method")]
        assert urls, [e.dst for e in edges if "url" in e.dst]

    def test_a_local_does_not_retype_a_field_used_in_a_later_method(
        self, tmp_path: Path,
    ) -> None:
        """Read back from okhttp: ``var factory: LoggingEventListener.Factory`` in one
        test retyped the class field ``factory`` (a ``TestValueFactory``) in a later
        one, so ``factory.newRoute()`` lost its in-repo resolution for a wrong
        module. Locals close with their function; fields stay file-wide."""
        edges = _edges(
            tmp_path / "scope",
            "import java.io.File\n"
            "\n"
            "class Factory {\n"
            "    fun newRoute(): String = \"r\"\n"
            "}\n"
            "\n"
            "class T {\n"
            "    private val factory = Factory()\n"
            "\n"
            "    fun a(p: String) {\n"
            "        var factory: File = File(p)\n"
            "        factory.readText()\n"
            "        val tmp = File(p)\n"
            "        tmp.exists()\n"
            "    }\n"
            "\n"
            "    fun b() {\n"
            "        factory.newRoute()\n"
            "        tmp.length()\n"
            "    }\n"
            "}\n",
        )
        assert _call(edges, "readText").dst == "kotlin:java.io.File:0-0:readText:unresolved"
        assert _call(edges, "exists").dst == "kotlin:java.io.File:0-0:exists:unresolved"
        # ``tmp`` shadowed nothing and is gone once ``a`` closes.
        assert _call(edges, "length").dst == "kotlin:external:0-0:length:unresolved"
        routes = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":Factory.newRoute:method")]
        assert routes, [e.dst for e in edges if "newRoute" in e.dst]

    def test_a_local_bound_to_a_builder_chain_is_typed_from_its_root(
        self, tmp_path: Path,
    ) -> None:
        """Read back from okhttp and detekt: ``val r = MockResponse().setBody(x)`` and
        ``val s = Cfg(..).subConfig("a")`` were typed from the chain ROOT before;
        a stricter callee test lost 95 resolved sites. The root stays the evidence."""
        edges = _edges(
            tmp_path / "chain",
            "import java.io.File\n"
            "\n"
            "class Cfg(val k: String) {\n"
            "    fun sub(x: String): Cfg = this\n"
            "    fun value(): String = k\n"
            "}\n"
            "\n"
            "fun go(p: String) {\n"
            "    val s = Cfg(\"a\").sub(\"x\").sub(\"y\")\n"
            "    s.value()\n"
            "    val f = File(p).resolve(\"x\")\n"
            "    f.readText()\n"
            "}\n",
        )
        values = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":Cfg.value:method")]
        assert values, [e.dst for e in edges if "value" in e.dst]
        assert _call(edges, "readText").dst == "kotlin:java.io.File:0-0:readText:unresolved"

    def test_a_dotted_constructor_at_the_root_of_a_longer_chain_types_the_outer_class(
        self, tmp_path: Path,
    ) -> None:
        """Read back from okhttp: ``val request = Request.Builder().url(u).build()``
        then ``request.tag()`` resolved to ``Request.tag`` before; typing the local
        ``Request.Builder`` lost 89 sites. The bare ``Request.Builder()`` keeps the
        precise type (pinned above)."""
        edges = _edges(
            tmp_path / "build",
            "class Request {\n"
            "    fun tag(): String = \"t\"\n"
            "    class Builder {\n"
            "        fun url(u: String): Builder = this\n"
            "        fun build(): Request = Request()\n"
            "    }\n"
            "}\n"
            "\n"
            "fun go(u: String) {\n"
            "    val request = Request.Builder().url(u).build()\n"
            "    request.tag()\n"
            "    val multiline =\n"
            "      Request\n"
            "        .Builder()\n"
            "        .url(u)\n"
            "        .build()\n"
            "    multiline.tag()\n"
            "}\n",
        )
        tags = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(":Request.tag:method")]
        assert len(tags) == 2, [e.dst for e in edges if "tag" in e.dst]

    def test_a_generic_declared_type_binds_its_base(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "gen",
            "fun go(x: String) {\n"
            "    val xs: MutableList<String> = mutableListOf()\n"
            "    xs.add(x)\n"
            "}\n",
        )
        edge = _call(edges, "add")
        assert edge.dst == "kotlin:external:0-0:add:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "MutableList"

    def test_a_call_whose_callee_is_an_expression_types_nothing(self, tmp_path: Path) -> None:
        """``(make)()`` -- a parenthesized callee is neither a name nor a navigation."""
        edges = _edges(
            tmp_path / "paren",
            "fun go(make: () -> Any, d: String) {\n"
            "    val x = (make)()\n"
            "    x.write(d)\n"
            "}\n",
        )
        edge = _call(edges, "write")
        assert edge.dst == "kotlin:external:0-0:write:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") is None

    def test_a_nested_type_on_an_imported_outer_qualifies_through_it(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "mapentry",
            "import java.util.Map\n"
            "\n"
            "fun go(m: Map<String, String>) {\n"
            "    for (en in m.entries) {\n"
            "        val entry: Map.Entry<String, String> = en\n"
            "        entry.getKey()\n"
            "    }\n"
            "}\n",
        )
        assert _call(edges, "getKey").dst == "kotlin:java.util.Map.Entry:0-0:getKey:unresolved"

    def test_a_nested_type_on_an_unimported_outer_is_left_alone(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "nested",
            "fun go(make: () -> Outer.Entry) {\n"
            "    val e: Outer.Entry = make()\n"
            "    e.ping()\n"
            "}\n",
        )
        edge = _call(edges, "ping")
        assert edge.dst == "kotlin:external:0-0:ping:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "Entry"

    def test_an_unimported_bare_type_is_left_alone(self, tmp_path: Path) -> None:
        """No import, no inline path: the slot stays ``external`` and the hint stays."""
        edges = _edges(
            tmp_path / "u",
            "fun go(w: Widget, d: String) {\n"
            "    w.write(d)\n"
            "}\n",
        )
        edge = _call(edges, "write")
        assert edge.dst == "kotlin:external:0-0:write:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "Widget"
        assert (edge.meta or {}).get("call_construct") == "method"
        assert _tagged(edges) == 0


class TestUntypedReceiverEmitsThePlaceholder:
    def test_untyped_receiver(self, tmp_path: Path) -> None:
        edges = _edges(
            tmp_path / "n",
            "fun go(d: String) {\n"
            "    val x = make()\n"
            "    x.write(d)\n"
            "}\n",
        )
        edge = _call(edges, "write")
        assert edge.dst == "kotlin:external:0-0:write:unresolved", edge.dst
        assert (edge.meta or {}).get("call_construct") == "method"
        assert (edge.meta or {}).get("receiver_type_hint") is None

    def test_the_declaration_flips(self) -> None:
        from hypergumbo_core.analyzer_disclosure import emits_external_method_call_edges

        assert emits_external_method_call_edges("kotlin") is True
