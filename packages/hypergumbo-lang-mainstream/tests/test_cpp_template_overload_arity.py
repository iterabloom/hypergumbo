# SPDX-License-Identifier: AGPL-3.0-or-later
"""cpp: a call among overloads that include a TEMPLATE binds by arity.

The defect (reduced from sherpa-onnx's keyword-spotter-transducer-impl.h): a
zero-argument ``Init()`` in an in-class constructor bound the one-parameter
member template ``Init(M *m)``. Both were free ``function``s with bare names
while the zero-argument overload was the method ``Impl::Init``, so the name
lookup found the template and the arity choice (WI-hilum) had nothing to
choose among: ``callables_by_name["Init"]`` held the template alone. With the
constructor and the template named as members of ``Impl`` the call has its
whole overload set.

Every fixture puts the WRONG overload last, where a name-keyed view (which
keeps the last same-named definition) would pick it, and the order test runs
the same set in both orders.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.cpp import analyze_cpp


def _targets(result, line: int, edge_type: str = "calls") -> list[tuple[str, int, object]]:
    """(dst name, dst start line, resolution_quality) per edge at ``line``."""
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        (by_id[e.dst].name, by_id[e.dst].span.start_line,
         (e.meta or {}).get("resolution_quality"))
        for e in result.edges
        if e.edge_type == edge_type and e.line == line and e.dst in by_id
        and by_id[e.dst].kind in ("function", "method")
    )


def test_the_items_repro_binds_the_zero_argument_overload(tmp_path: Path) -> None:
    (tmp_path / "a.hpp").write_text("""\
struct Mgr {};
struct Impl {
  Impl() { Init(); }
  void Init() { helper(); }
  template <typename M>
  void Init(M *m) { helper(); }
  void helper() {}
};
""")
    result = analyze_cpp(tmp_path)
    assert _targets(result, 3) == [("Impl::Init", 4, None)]


def test_the_choice_does_not_depend_on_definition_order(tmp_path: Path) -> None:
    """The template first, then last: the zero- and one-argument calls bind
    the same overloads either way."""
    first = """\
struct Impl {
  template <typename M> void Init(M *m) {}
  void Init() {}
  Impl() { Init(); }
  explicit Impl(int *p) { Init(p); }
};
"""
    last = """\
struct Impl {
  Impl() { Init(); }
  explicit Impl(int *p) { Init(p); }
  void Init() {}
  template <typename M> void Init(M *m) {}
};
"""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "a.cpp").write_text(first)
    (tmp_path / "two" / "a.cpp").write_text(last)
    one = analyze_cpp(tmp_path / "one")
    two = analyze_cpp(tmp_path / "two")
    assert _targets(one, 4) == [("Impl::Init", 3, None)]
    assert _targets(one, 5) == [("Impl::Init", 2, None)]
    assert _targets(two, 2) == [("Impl::Init", 4, None)]
    assert _targets(two, 3) == [("Impl::Init", 5, None)]


ARITIES = """\
struct Def {
  Def() { Go(); Go(1); Go(1, 2); }
  void Go(int a, int b) {}
  template <typename T>
  void Go(T t = T()) {}
};
struct Var {
  Var() { Run(); Run(1, 2, 3); }
  Var(int) { Run(1); }
  void Run(int a) {}
  template <typename... Ts>
  void Run(Ts... ts) {}
};
"""


def test_default_arguments_and_variadic_templates(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text(ARITIES)
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    go = sorted(
        by_id[e.dst].span.start_line for e in result.edges
        if e.edge_type == "calls" and e.line == 2 and e.dst in by_id
    )
    # Go() and Go(1): the template, whose one parameter has a default;
    # Go(1, 2): the two-parameter overload. One edge per (caller, callee).
    assert set(go) == {3, 5}
    # Run() and Run(1, 2, 3): only the variadic template takes them.
    assert set(_targets(result, 8)) == {("Var::Run", 12, None)}
    # Run(1): both take one argument; C++ prefers the non-template on an
    # equal match, which arity alone cannot see: an honest ambiguous record.
    assert [q for _, _, q in _targets(result, 9)] == ["ambiguous"]


def test_free_function_and_free_template_choose_by_arity(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("""\
void g() {}
template <class T> void g(T t) {}
void use() { g(); }
""")
    result = analyze_cpp(tmp_path)
    assert _targets(result, 3) == [("g", 1, None)]


NEWS = """\
struct Tc {
  Tc() {}
  template <typename T> Tc(T* a, T* b) {}
  Tc(int x) {}
};
void user() {
  Tc* a = new Tc(1);
  Tc* b = new Tc;
  Tc* c = new Tc(nullptr, nullptr);
  Tc* d = new Tc{};
  Tc* e = new Tc[3];
}
"""


def test_new_lands_on_the_constructor_its_arguments_select(tmp_path: Path) -> None:
    """``new Tc(..)`` lands its instantiates edge on a constructor; which one
    is chosen by the argument count, as a call's callee is: ``new Tc`` and
    ``new Tc[3]`` default-construct, ``new Tc{}`` passes none."""
    (tmp_path / "a.cpp").write_text(NEWS)
    result = analyze_cpp(tmp_path)
    expected = {7: 4, 8: 2, 9: 3, 10: 2, 11: 2}
    for line, ctor_line in expected.items():
        assert _targets(result, line, "instantiates") == [("Tc::Tc", ctor_line, None)], line


def test_new_among_same_arity_constructors_lands_on_the_class(tmp_path: Path) -> None:
    """Two one-parameter constructors: ``new Two(1)`` cannot be told apart by
    arity. The construction is certainly of ``Two``, so the edge lands on the
    class, from which the walk reaches both, not on the last one defined."""
    (tmp_path / "a.cpp").write_text("""\
struct Two {
  Two(int x) {}
  template <typename T> Two(T* p) {}
};
void user() { Two* t = new Two(1); }
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    edges = [
        e for e in result.edges
        if e.edge_type == "instantiates" and e.line == 5 and e.dst in by_id
    ]
    assert {by_id[e.dst].kind for e in edges} == {"struct"}
    assert all((e.meta or {}).get("resolution_quality") is None for e in edges)


def test_an_ambiguous_new_of_a_class_without_a_symbol_stays_ambiguous(
    tmp_path: Path,
) -> None:
    """The class is defined in a header outside the tree; only its
    out-of-line constructors are: the edge keeps the name's pick, marked
    ambiguous."""
    (tmp_path / "a.cpp").write_text("""\
#include "two.h"
Two::Two(int x) {}
template <typename T> Two::Two(T* p) {}
void user() {}
void make() {}
void use() { Two* t = new Two(1); }
""")
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    edges = [
        e for e in result.edges
        if e.edge_type == "instantiates" and e.line == 6 and e.dst in by_id
        and by_id[e.dst].kind == "method"
    ]
    assert [(e.meta or {}).get("resolution_quality") for e in edges] == ["ambiguous"]
    assert edges[0].confidence < 0.95

CONSTRUCTIONS = """\
#include <memory>
struct Box {
  Box() {}
  explicit Box(int x) {}
  Box(int x, int y) {}
  ~Box() {}
};
void build() {
  Box a;
  Box b(1);
  Box c{1, 2};
  Box d(1), *e;
  auto f = std::make_unique<Box>(1, 2);
  auto g = Box{};
}
"""


def test_each_construction_also_lands_on_the_constructor_it_calls(tmp_path: Path) -> None:
    """The class edge stays (consumers key on it); the constructor the
    argument count chose gets its own edge at the same line."""
    (tmp_path / "a.cpp").write_text(CONSTRUCTIONS)
    result = analyze_cpp(tmp_path)
    by_id = {s.id: s for s in result.symbols}

    def at(line: int) -> set[tuple[str, int]]:
        return {
            (by_id[e.dst].kind, by_id[e.dst].span.start_line) for e in result.edges
            if e.edge_type == "instantiates" and e.line == line and e.dst in by_id
        }

    assert at(9) == {("struct", 2), ("method", 3)}
    assert at(10) == {("struct", 2), ("method", 4)}
    assert at(11) == {("struct", 2), ("method", 5)}
    # ``Box d(1), *e``: the pointer constructs nothing.
    assert at(12) == {("struct", 2), ("method", 4)}
    # std::make_unique<Box>(1, 2) constructs Box(1, 2).
    assert ("method", 5) in at(13)
    assert at(14) == {("struct", 2), ("method", 3)}


def test_a_make_function_with_no_type_argument_constructs_nothing(tmp_path: Path) -> None:
    """``make_unique<5>()`` names no type; ``other::make_unique<Box>()`` is
    not the standard factory; neither is a construction."""
    (tmp_path / "a.cpp").write_text("""\
struct Box { Box() {} };
void build() {
  auto a = std::make_unique<5>();
  auto b = other::make_unique<Box>();
}
""")
    result = analyze_cpp(tmp_path)
    assert [e for e in result.edges if e.edge_type == "instantiates"] == []
