# SPDX-License-Identifier: AGPL-3.0-or-later
"""An explicitly instantiated generic call emits the edge its plain spelling does (WI-nulig).

go.py's call arm handled a ``call_expression`` whose function is an
``identifier`` or a ``selector_expression``. tree-sitter-go spells an explicit
instantiation two other ways, so the call site vanished from the graph:

    Map[int](1)            type_conversion_expression(generic_type(type_identifier), operand)
    pkg.Map[int](1)        type_conversion_expression(generic_type(qualified_type), operand)
    Zero[int]()            call_expression(function=index_expression(identifier))
    pkg.Zero[int]()        call_expression(function=index_expression(selector_expression))

(One argument parses as a conversion, because ``T[int](x)`` is also how a
generic TYPE is converted; zero or several arguments parse as an index.
``Pair[int, string](1, "x")`` -- several type arguments AND several arguments --
is a plain call_expression with a type_arguments field and already worked.)

The same two node shapes also spell a call through a collection ELEMENT,
``handlers[k](x)``. Go instantiates only package-level functions and types, so
the shape is an instantiation when its operand names one: a bare name not bound
locally, or a selector on an imported package. A local, a package VARIABLE, or a
field is an element call and stays silent, as it was.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.go import analyze_go

_A = """package a

type MyInt int

type List[T any] []T

func Map[T any](x T) T { return x }

func Zero[T any]() T { var z T; return z }

func Pair[K comparable, V any](k K, v V) V { return v }

var handlers = map[string]func(int){"k": func(int) {}}

type S struct{ fs []func(int) }

func handler() {}

func Apply[T any](f T) T { return f }

func bareConv() { Map[int](1) }
func bareIndex() { Zero[int]() }
func bareVariadic(xs []int) { Map[[]int](xs) }
func bareMany() { Pair[int, string](1, "x") }
func bareInferred() { Map(2) }
func bareUnknown() { Nope[int](1) }
func typeConv() { List[int](nil) }
func plainConv() { MyInt(3) }
func localElement(fs []func(int)) { fs[0](1) }
func localElementConv(fs []func(int), i int) { fs[i](1) }
func packageVarElement() { handlers["k"](1) }
func fieldElement(s S) { s.fs[0](1) }
type Outer struct{ inner S }
func nestedFieldElement(o Outer) { o.inner.fs[0](1) }
func getFs() []func(int) { return nil }
func callResultElement() { getFs()[0](1) }
func byteConv(s string) []byte { return []byte(s) }
func refArg() { Apply[func()](handler) }
"""

_B = """package b

import (
\t"slices"

\t"example.com/t/a"
\t"github.com/x/sets"
)

func qualConv() { a.Map[int](3) }
func qualIndex() { a.Zero[int]() }
func qualInferred() { a.Map(4) }
func stdConv(s []int) { slices.Sort[[]int](s) }
func stdInferred(s []int) { slices.Sort(s) }
func stdTypeArg(s []int) { slices.Insert[[]int](s, 0, 1) }
func extIndex() { sets.New[string]() }
"""


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list]:
    repo = tmp_path_factory.mktemp("repo")
    (repo / "go.mod").write_text("module example.com/t\n\ngo 1.22\n")
    (repo / "a").mkdir()
    (repo / "a" / "a.go").write_text(_A)
    (repo / "b").mkdir()
    (repo / "b" / "b.go").write_text(_B)
    analysis = analyze_go(repo)
    names = {s.id: s.name for s in analysis.symbols}
    out: dict[str, list] = {}
    for e in analysis.edges:
        if e.edge_type == "calls":
            out.setdefault(names.get(e.src, e.src), []).append(e)
    return out


def _only(calls: dict[str, list], caller: str):
    assert caller in calls, sorted(calls)  # reach: the call site has an edge
    [edge] = calls[caller]
    return edge


@pytest.mark.parametrize("caller,plain", [
    ("bareConv", "bareInferred"),
    ("bareVariadic", "bareInferred"),
    ("qualConv", "qualInferred"),
])
def test_an_instantiated_call_binds_like_its_plain_spelling(
        calls: dict[str, list], caller: str, plain: str) -> None:
    edge, plain_edge = _only(calls, caller), _only(calls, plain)
    assert edge.dst == plain_edge.dst
    assert edge.dst.endswith(":Map:function")
    assert edge.meta["call_construct"] == plain_edge.meta["call_construct"] == "function"
    assert edge.is_resolved is True


@pytest.mark.parametrize("caller", ["bareIndex", "qualIndex"])
def test_the_index_spelling_binds_too(calls: dict[str, list], caller: str) -> None:
    edge = _only(calls, caller)
    assert edge.dst.endswith(":Zero:function")
    assert edge.meta["call_construct"] == "function"


def test_control_several_type_arguments_already_bound(calls: dict[str, list]) -> None:
    assert _only(calls, "bareMany").dst.endswith(":Pair:function")


@pytest.mark.parametrize("caller,dst", [
    ("stdConv", "go:slices:0-0:Sort:unresolved"),
    ("extIndex", "go:github.com/x/sets:0-0:New:unresolved"),
    # Control: a type argument that can only be a type (`[]int`) parses as a
    # plain call_expression with a type_arguments field, and always bound.
    ("stdTypeArg", "go:slices:0-0:Insert:unresolved"),
])
def test_an_external_instantiation_names_its_package(
        calls: dict[str, list], caller: str, dst: str) -> None:
    edge = _only(calls, caller)
    assert edge.dst == dst
    assert edge.is_resolved is False
    assert edge.meta["call_construct"] == _only(calls, "stdInferred").meta["call_construct"]


def test_an_unknown_bare_instantiation_is_a_positive_placeholder(calls: dict[str, list]) -> None:
    """INV-guzuj's rule: a call the resolver cannot place is still a call."""
    edge = _only(calls, "bareUnknown")
    assert edge.dst == "go:external:0-0:Nope:unresolved"
    assert edge.is_resolved is False


def test_a_generic_type_conversion_reads_as_its_plain_conversion(calls: dict[str, list]) -> None:
    """Same rule as `MyInt(3)`, whatever that rule is worth: the target is the type."""
    assert _only(calls, "typeConv").dst.endswith(":List:type")
    assert _only(calls, "plainConv").dst.endswith(":MyInt:type")


@pytest.mark.parametrize("caller", [
    "localElement", "localElementConv", "packageVarElement", "fieldElement",
    "nestedFieldElement", "byteConv",
])
def test_an_element_call_stays_silent(calls: dict[str, list], caller: str) -> None:
    """`handlers["k"](1)` calls an element, not `handlers`; a field cannot be
    instantiated. Nothing named, nothing emitted -- as before. (`[]byte(s)` is a
    conversion that names no callee at all.)"""
    assert caller not in calls, calls.get(caller)


def test_an_element_of_a_call_result_calls_only_the_producer(calls: dict[str, list]) -> None:
    [edge] = calls["callResultElement"]
    assert edge.dst.endswith(":getFs:function")


def test_a_function_passed_to_an_instantiated_call_is_referenced(calls: dict[str, list]) -> None:
    refs = [e for e in calls["refArg"] if e.evidence_type == "function_reference_arg"]
    assert [e.dst.rsplit(":", 2)[-2] for e in refs] == ["handler"]
    assert any(e.dst.endswith(":Apply:function") for e in calls["refArg"])
