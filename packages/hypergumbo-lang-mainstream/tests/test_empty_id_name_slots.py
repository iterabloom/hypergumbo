# SPDX-License-Identifier: AGPL-3.0-or-later
"""No producer emits an id whose name slot is empty (WI-pusan, INV-kurup).

id-format:F3's round-trip flags "Symbol.id name-slot is empty". On grpc
the class came from four producers; three are here (starlark's is in
hypergumbo-lang-common):

* cpp: ``enum : uint16_t { ... }``. tree-sitter fills the absent name with
  a zero-width MISSING ``type_identifier``, which read as a name, so an
  anonymous enum became ``...:36-49::enum``.
* ruby: error recovery's MISSING method (``foo.`` parses as a call to an
  empty identifier) became a ``calls`` edge to ``ruby:external:0-0::``.
* python: a base class that is a call expression
  (``class X(collections.namedtuple(...))``) formats as ``""`` and became an
  ``extends`` edge to ``python:external:0-0::``.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import symbol_name_slot
from hypergumbo_core.spec_validator import _check_id_roundtrip


def test_an_anonymous_cpp_enum_with_a_base_type_is_no_symbol(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    (tmp_path / "h.hpp").write_text(
        "class H {\n public:\n  enum : unsigned short {\n    kA = 1,\n  };\n};\n"
        "enum Named : int { kB };\n"
    )
    symbols = analyze_cpp(tmp_path).symbols
    assert [s.name for s in symbols if s.kind == "enum"] == ["Named"]
    # Its enumerators went with it: ``::kA`` named an owner that has no name.
    assert all(s.name and not s.name.startswith("::") for s in symbols)
    assert _check_id_roundtrip(symbols) == []


def test_a_ruby_call_with_a_missing_method_is_no_edge(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.ruby import analyze_ruby

    (tmp_path / "a.rb").write_text("def m\n  foo.bar\nend\nfoo.\n")
    edges = analyze_ruby(tmp_path).edges
    assert [e.dst for e in edges if e.edge_type == "calls" and not symbol_name_slot(e.dst)] == []
    assert any(e.edge_type == "calls" and symbol_name_slot(e.dst) == "bar" for e in edges)


def test_a_python_call_expression_base_is_no_extends_edge(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.py import analyze_python

    (tmp_path / "a.py").write_text(
        "import collections\n\n"
        "class Details(collections.namedtuple('Details', ('a', 'b')), Exception):\n"
        "    pass\n"
    )
    extends = [e for e in analyze_python(tmp_path).edges if e.edge_type == "extends"]
    assert [symbol_name_slot(e.dst) for e in extends] == ["Exception"]


def test_a_cpp_base_split_across_lines_is_one_qualified_name(tmp_path: Path) -> None:
    """The same INV-kurup pass found grpc's one remaining id_format error:
    ``class M : public ns::Engine::\\n    Endpoint::Info {`` kept the newline
    and indentation in the base name, which became the external id's module
    slot (``cpp:ns::Engine::\\n    Endpoint:0-0:Info:...``)."""
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    (tmp_path / "m.hpp").write_text(
        "class M : public ns::Engine::\n"
        "              Endpoint::Info {\n};\n"
    )
    result = analyze_cpp(tmp_path)
    [m] = [s for s in result.symbols if s.name == "M"]
    assert m.meta["base_classes"] == ["ns::Engine::Endpoint::Info"]
    assert not [e.dst for e in result.edges if "\n" in e.dst or "  " in e.dst]
