# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bapuk: ``std::getline``'s boundary is its STREAM ARGUMENT's, and the row was absent.

THE GAP. ``cpp.yaml`` had no row for ``std::getline`` and ``c.yaml`` none for POSIX
``getline``, so the idiomatic C++ standard-input read

    std::string s;
    std::getline(std::cin, s);

produced NO source at all. ``std::cin`` is rowed (an ``ipc_recv`` attribute) and
fires where it is literally written, but the call that TRANSFERS the bytes was
invisible; measured on the four-function fixture below before the change, the
whole file yielded ``ipc_recv: 2`` -- the two textual ``std::cin`` references --
and nothing for any of the four ``getline`` calls, including the two whose stream
is a file or a parameter.

WHY A FIXED ROW WOULD BE WRONG. The stream is an ARGUMENT (INV-bagok / INV-zumin
class (b)), the ``fgets`` shape: ``getline(in, s)`` over an ``ifstream`` and
``getline(std::cin, s)`` are one row and two different crossings.

THE REACHABILITY FINDING THAT SHAPED THE FIX, taken before any row was written
(LIVE rule 9, and the item asked for exactly this check). cpp's module slot for
an unresolved call is the COMMA-JOINED LIST OF EVERY ``#include`` IN THE FILE --
``std::getline(std::cin, s)`` arrives as module ``iostream,fstream,string`` --
while the ATTRIBUTE path emits ``std`` for ``std::cin``. So the namespace the
call names was discarded and a ``std.getline`` row could never have matched.
Measured over the corpus, no single header dominates either: of 78 C++ files
containing a ``getline`` call, ``string`` appears in 51.3%, ``sstream`` 48.7%,
``fstream`` 47.4%, ``iostream`` 38.5%, and 14.1% include none of them -- so
rowing under any one header would have reached at most half the population.

THE FIX IS TO STOP DISCARDING THE NAMESPACE. ``std`` joins the module
disjunction rather than replacing it, and that is deliberate: the include list
is how ``std::printf`` in a file including ``<stdio.h>`` reaches c.yaml's
inherited ``stdio.printf`` row, and REPLACING the slot would have deleted that
classification. Classification asks whether ANY disjunct names a primitive, so
adding one can only add matches.

WHAT THAT COSTS ELSEWHERE, disclosed rather than discovered later:
``_adjudicate_external_modules`` reads the same disjunction with the OPPOSITE
quantifier (ALL must be enumerated, since one unenumerated home leaves the call
genuinely unexamined), so a new disjunct can only make a call HARDER to call an
examined negative. That is inert today because cpp.yaml declares no
``module_completeness`` at all -- python is the only catalogue that declares any
-- so every C++ external call is already unexamined; WI-hasul owns that for c and
is cited, not folded.

THE STAMP USES THE DECLARED TYPE, not a value origin, because C++ hands it over:
``std::ifstream in(p);`` is the most-vexing-parse shape, a ``declaration`` whose
type node is ``std::ifstream`` and whose declarator is a ``function_declarator``.
So the stream's kind is read off its declaration rather than chased through a
binding, which is strictly more reliable than the one-hop rule c needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.cpp import (
    _cpp_stream_target_kind,
    analyze_cpp,
)


@pytest.fixture()
def cpp_available():
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_cpp"):
        pytest.skip("C++ tree-sitter grammar not installed")


_PRELUDE = (
    "#include <iostream>\n#include <fstream>\n"
    "#include <sstream>\n#include <string>\n\n"
)


def _edges(tmp_path: Path, body: str):
    (tmp_path / "a.cpp").write_text(_PRELUDE + body, encoding="utf-8")
    result = analyze_cpp(tmp_path)
    assert not result.skipped
    return result.edges


def _getline_edges(edges):
    return [
        e for e in edges
        if e.edge_type == "calls" and e.dst.split(":")[3] == "getline"
    ]


def _stamps(edges) -> list[str]:
    return [
        (e.meta or {})["io_target_kind"]
        for e in _getline_edges(edges)
        if (e.meta or {}).get("io_target_kind") is not None
    ]


class TestTheStreamArgumentDecides:
    def test_getline_over_cin_reads_a_standard_stream(self, tmp_path, cpp_available) -> None:
        edges = _edges(tmp_path, """
void fromStdin() {
    std::string s;
    std::getline(std::cin, s);
}
""")
        assert _stamps(edges) == ["std_stream"]

    def test_getline_over_an_ifstream_reads_a_host_path(self, tmp_path, cpp_available) -> None:
        """The most-vexing-parse declaration: type node ``std::ifstream``."""
        edges = _edges(tmp_path, """
void fromFile(const char* p) {
    std::ifstream in(p);
    std::string s;
    std::getline(in, s);
}
""")
        assert _stamps(edges) == ["host_path"]

    def test_getline_over_an_assigned_ifstream_reads_a_host_path(
        self, tmp_path, cpp_available,
    ) -> None:
        """The ``= std::ifstream(p)`` spelling reaches the same type node."""
        edges = _edges(tmp_path, """
void fromFile2(const char* p) {
    std::ifstream in = std::ifstream(p);
    std::string s;
    std::getline(in, s);
}
""")
        assert _stamps(edges) == ["host_path"]

    def test_getline_over_a_stringstream_is_in_memory(self, tmp_path, cpp_available) -> None:
        edges = _edges(tmp_path, """
void fromString(const std::string& t) {
    std::istringstream ss(t);
    std::string s;
    std::getline(ss, s);
}
""")
        assert _stamps(edges) == ["in_memory"]

    def test_an_unqualified_getline_still_resolves(self, tmp_path, cpp_available) -> None:
        """``using namespace std;`` is common; the call is then bare."""
        edges = _edges(tmp_path, """
using namespace std;
void bare() {
    string s;
    getline(cin, s);
}
""")
        assert _stamps(edges) == ["std_stream"]


class TestAbstentionIsTheDefault:
    def test_a_parameter_stream_stamps_nothing(self, tmp_path, cpp_available) -> None:
        """``std::istream&`` may be a file, a pipe or a buffer -- no answer."""
        edges = _edges(tmp_path, """
void fromStream(std::istream& is) {
    std::string s;
    std::getline(is, s);
}
""")
        assert _stamps(edges) == []
        assert len(_getline_edges(edges)) == 1

    def test_an_unbound_identifier_stamps_nothing(self, tmp_path, cpp_available) -> None:
        edges = _edges(tmp_path, """
void unknown() {
    std::string s;
    std::getline(somewhere, s);
}
""")
        assert _stamps(edges) == []


class TestTheHelperAbstainsOnEveryUnprovableShape:
    """Unit level, so each abstention branch is pinned by its own input."""

    def _kind(self, body: str, callee: str = "std::getline"):
        import tree_sitter
        import tree_sitter_cpp
        from hypergumbo_core.analyze.base import iter_tree, node_text

        raw = (_PRELUDE + body).encode("utf-8")
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_cpp.language())
        )
        tree = parser.parse(raw)
        for n in iter_tree(tree.root_node):
            if n.type != "call_expression":
                continue
            fn = n.child_by_field_name("function")
            if fn is not None and node_text(fn, raw).strip().endswith("getline"):
                return _cpp_stream_target_kind(n, raw, callee)
        raise AssertionError("no getline call in fixture")  # pragma: no cover

    def test_a_call_outside_any_function_body(self, cpp_available) -> None:
        """A namespace-scope initialiser has no enclosing function_definition,
        so there is no scope to resolve the declaration in."""
        assert self._kind(
            "std::istream& g();\nint dummy = (std::getline(someStream, s), 0);\n"
        ) is None

    def test_a_declared_type_that_is_not_a_stream(self, cpp_available) -> None:
        """`std::string` is a declaration, and the walk must record that it
        answers NOTHING rather than leaving an earlier answer standing."""
        assert self._kind(
            "void f() {\n  std::string notAStream;\n"
            "  std::string s;\n  std::getline(notAStream, s);\n}\n"
        ) is None

    def test_a_call_with_too_few_arguments(self, cpp_available) -> None:
        assert self._kind("void f() {\n  std::getline();\n}\n") is None

    def test_an_argument_that_is_not_an_identifier(self, cpp_available) -> None:
        """A call expression as the stream: nothing to resolve a declaration for."""
        assert self._kind(
            "std::istream& pick();\n"
            "void f() {\n  std::string s;\n  std::getline(pick(), s);\n}\n"
        ) is None

    def test_a_callee_not_in_the_table(self, cpp_available) -> None:
        assert self._kind(
            "void f() {\n  std::string s;\n  std::getline(std::cin, s);\n}\n",
            callee="std::somethingelse",
        ) is None

    def test_a_later_declaration_does_not_reach_an_earlier_call(
        self, cpp_available,
    ) -> None:
        """Order is the point: a declaration BELOW the call must not be read as
        if it reached it."""
        assert self._kind(
            "void f() {\n  std::string s;\n  std::getline(in, s);\n"
            "  std::ifstream in(\"p\");\n}\n"
        ) is None


class TestTheStampReachesTheCatalogue:
    """End to end through production's classifier, with the shipped catalogue."""

    def _classified(self, tmp_path, body: str):
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        edges = _edges(tmp_path, body)
        (edge,) = _getline_edges(edges)
        prim = classify_call(
            {"cpp": load_catalog("cpp")}, edge.dst, edge.meta,
            dst_ref=edge.dst_ref,
        )
        return None if prim is None else (prim.boundary, prim.module, prim.name)

    def test_a_stdin_getline_is_an_ipc_receive(self, tmp_path, cpp_available) -> None:
        got = self._classified(tmp_path, """
void fromStdin() {
    std::string s;
    std::getline(std::cin, s);
}
""")
        assert got == ("ipc_recv", "std", "getline")

    def test_a_file_getline_is_a_filesystem_read(self, tmp_path, cpp_available) -> None:
        got = self._classified(tmp_path, """
void fromFile(const char* p) {
    std::ifstream in(p);
    std::string s;
    std::getline(in, s);
}
""")
        assert got == ("fs_read", "std", "getline")

    def test_an_unstamped_getline_falls_back_to_fs_read(
        self, tmp_path, cpp_available,
    ) -> None:
        got = self._classified(tmp_path, """
void fromStream(std::istream& is) {
    std::string s;
    std::getline(is, s);
}
""")
        assert got == ("fs_read", "std", "getline")


class TestTheIncludeSlotSurvives:
    """REPLACING the module slot would have deleted a classification."""

    def test_std_printf_still_reaches_the_inherited_stdio_row(
        self, tmp_path, cpp_available,
    ) -> None:
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        (tmp_path / "b.cpp").write_text(
            "#include <stdio.h>\nvoid w() { std::printf(\"x\"); }\n",
            encoding="utf-8",
        )
        result = analyze_cpp(tmp_path)
        calls = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst.split(":")[3] == "printf"
        ]
        assert len(calls) == 1
        prim = classify_call(
            {"cpp": load_catalog("cpp")}, calls[0].dst, calls[0].meta,
            dst_ref=calls[0].dst_ref,
        )
        assert prim is not None and (prim.module, prim.name) == ("stdio", "printf")


class TestTheExternalSentinelSurvives:
    """The REMOVAL this change caused once, and must never cause again.

    A file with NO system includes leaves the module slot at the `external`
    SENTINEL, and that sentinel is load-bearing: it is what enables
    `lookup_with_module`'s SHORT-NAME fallback. An earlier draft of WI-bapuk
    named `std` there whenever the callee was `std::`-qualified -- accurate,
    and useless, because cpp.yaml keys chrono under
    `std::chrono::system_clock`, not under `std`. Naming a module the
    catalogue has nothing under switched the fallback off and DELETED the
    classification.

    Measured on modsecurity: two `std::chrono::system_clock::now()` chains in
    `src/collection/backend/collection_data.cc` went host_info_read -> nothing.
    The naive A/B diff HID it, reporting 112 added / 102 removed for apt,
    because changing the module slot changes every dst string and so the diff
    key itself moved; it only surfaced on a module-stable key.
    """

    def test_a_std_call_with_no_includes_keeps_the_short_name_fallback(
        self, tmp_path, cpp_available,
    ) -> None:
        from hypergumbo_core.io_boundary import classify_call, load_catalog

        (tmp_path / "t.cc").write_text(
            "void f() {\n    auto n = std::chrono::system_clock::now();\n"
            "    (void)n;\n}\n",
            encoding="utf-8",
        )
        result = analyze_cpp(tmp_path)
        calls = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst.split(":")[3] == "now"
        ]
        assert len(calls) == 1
        assert calls[0].dst.split(":")[1] == "external", calls[0].dst
        prim = classify_call(
            {"cpp": load_catalog("cpp")}, calls[0].dst, calls[0].meta,
            dst_ref=calls[0].dst_ref,
        )
        assert prim is not None, "the short-name fallback was switched off"
        assert (prim.module, prim.name, prim.boundary) == (
            "std::chrono::system_clock", "now", "host_info_read",
        )


class TestCGetline:
    """POSIX ``getline(&buf, &n, stdin)`` -- a ROW-ONLY addition to c's mechanism."""

    def test_c_getline_is_dual_rowed(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog

        cat = load_catalog("c")
        rows = [p for p in cat.primitives if p.name == "getline"]
        assert {p.boundary for p in rows} == {"fs_read", "ipc_recv"}
        assert all(p.boundary_ruling == "call_site_undecidable" for p in rows)

    def test_c_getline_falls_back_to_fs_read(self) -> None:
        """The same first-declared-row rule ``fgets`` relies on."""
        from hypergumbo_core.io_boundary import load_catalog

        cat = load_catalog("c")
        order = [p.boundary for p in cat.primitives if p.name == "getline"]
        assert order[0] == "fs_read"

    def test_c_getline_narrows_on_the_stream_argument(self, tmp_path) -> None:
        """c.py already stamps from the stream argument; getline takes it THIRD."""
        from hypergumbo_core.io_boundary import classify_call, load_catalog
        from hypergumbo_lang_mainstream.c import analyze_c
        from hypergumbo_core.analyze.base import is_grammar_available

        if not is_grammar_available("tree_sitter_c"):
            pytest.skip("C tree-sitter grammar not installed")
        (tmp_path / "m.c").write_text(
            "#include <stdio.h>\n"
            "void f(void) {\n"
            "  char *b = 0; size_t n = 0;\n"
            "  getline(&b, &n, stdin);\n"
            "}\n",
            encoding="utf-8",
        )
        result = analyze_c(tmp_path)
        calls = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst.split(":")[3] == "getline"
        ]
        assert len(calls) == 1
        prim = classify_call(
            {"c": load_catalog("c")}, calls[0].dst, calls[0].meta,
            dst_ref=calls[0].dst_ref,
        )
        assert prim is not None and prim.boundary == "ipc_recv"
