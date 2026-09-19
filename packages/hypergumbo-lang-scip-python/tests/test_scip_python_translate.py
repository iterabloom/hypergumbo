# SPDX-License-Identifier: AGPL-3.0-or-later
"""``translate_scip_python_to_hg`` on synthetic indexes and on the recording."""
from __future__ import annotations

from hypergumbo_core.ir import ExternalRef
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_lang_scip_python.translate import (
    DROPPED_KINDS,
    _external_ref,
    translate_scip_python_to_hg,
)

DEF = 0x01
PKG = "scip-python python sample 0.1.0"


def _index(*docs: scip_pb2.Document) -> bytes:
    return scip_pb2.Index(documents=list(docs)).SerializeToString()


def _doc(path: str, symbols: list[str], occurrences: list[tuple[str, int, list[int]]]) -> scip_pb2.Document:
    return scip_pb2.Document(
        relative_path=path,
        symbols=[scip_pb2.SymbolInformation(symbol=s) for s in symbols],
        occurrences=[scip_pb2.Occurrence(symbol=s, symbol_roles=r, range=rng) for s, r, rng in occurrences],
    )


class TestExternalRefs:
    def test_a_stdlib_method_keeps_its_defining_class_as_the_module(self) -> None:
        ref = _external_ref("scip-python python python-stdlib 3.11 pathlib/Path#glob().")
        assert ref == (ExternalRef(lang="python", module_path="pathlib.Path", name="glob"), "method")

    def test_a_module_level_function_is_a_function_construct(self) -> None:
        ref = _external_ref("scip-python python python-stdlib 3.11 re/compile().")
        assert ref == (ExternalRef(lang="python", module_path="re", name="compile"), "function")

    def test_a_non_callable_or_malformed_symbol_is_none(self) -> None:
        assert _external_ref("scip-python python python-stdlib 3.11 re/Pattern#") is None  # a type, not a call
        assert _external_ref("not a scip symbol") is None
        assert _external_ref("scip-python python python-stdlib 3.11 f().") is None  # no module at all


class TestTheTranslation:
    def test_parameters_and_module_declarations_are_dropped(self) -> None:
        f, param, module = f"{PKG} `m`/f().", f"{PKG} `m`/f().(x)", f"{PKG} `m`/__init__:"
        doc = _doc("m.py", [f, param, module], [(f, DEF, [0, 4, 5]), (param, DEF, [0, 6, 7]), (module, DEF, [0, 0, 1])])
        symbols, _ = translate_scip_python_to_hg(_index(doc), run_id="r")
        assert [(s.name, s.kind) for s in symbols] == [("f", "function")]
        assert DROPPED_KINDS == {"parameter", "declaration"}

    def test_every_symbol_carries_the_run_and_the_extension_language(self) -> None:
        f = f"{PKG} `m`/f()."
        symbols, _ = translate_scip_python_to_hg(_index(_doc("m.py", [f], [(f, DEF, [0, 4, 5])])), run_id="r")
        [s] = symbols
        assert s.origin_run_id == "r" and s.language == "python" and s.origin == ["scip"]

    def test_an_external_call_becomes_a_typed_stub_edge(self) -> None:
        caller, callee = f"{PKG} `m`/f().", "scip-python python python-stdlib 3.11 pathlib/Path#glob()."
        doc = _doc("m.py", [caller], [(caller, DEF, [0, 0, 5, 0]), (callee, 0, [2, 4, 8])])
        _, edges = translate_scip_python_to_hg(_index(doc), run_id="r")
        [edge] = edges
        assert edge.edge_type == "calls" and edge.is_resolved is False
        assert edge.dst == "python:pathlib.Path:0-0:glob:unresolved"
        assert edge.dst_ref == ExternalRef(lang="python", module_path="pathlib.Path", name="glob")
        assert edge.meta["call_construct"] == "method" and edge.line == 3

    def test_an_external_non_call_reference_is_dropped(self) -> None:
        caller, typ = f"{PKG} `m`/f().", "scip-python python python-stdlib 3.11 pathlib/Path#"
        doc = _doc("m.py", [caller], [(caller, DEF, [0, 0, 5, 0]), (typ, 0, [2, 4, 8])])
        _, edges = translate_scip_python_to_hg(_index(doc), run_id="r")
        assert edges == []

    def test_an_in_repo_call_resolves_to_the_defining_symbol(self) -> None:
        caller, callee = f"{PKG} `m`/f().", f"{PKG} `m`/g()."
        doc = _doc("m.py", [caller, callee], [(caller, DEF, [0, 0, 5, 0]), (callee, 0, [2, 4, 5]), (callee, DEF, [8, 4, 5])])
        symbols, edges = translate_scip_python_to_hg(_index(doc), run_id="r")
        ids = {s.name: s.id for s in symbols}
        [edge] = edges
        assert (edge.src, edge.dst, edge.edge_type, edge.is_resolved) == (ids["f"], ids["g"], "calls", True)
        assert edge.dst_ref is None


class TestExternalEdgeCases:
    def test_a_repeated_external_callee_is_resolved_once_and_emitted_per_site(self) -> None:
        caller, callee = f"{PKG} `m`/f().", "scip-python python python-stdlib 3.11 pathlib/Path#glob()."
        doc = _doc("m.py", [caller], [(caller, DEF, [0, 0, 9, 0]), (callee, 0, [2, 4, 8]), (callee, 0, [5, 4, 8])])
        _, edges = translate_scip_python_to_hg(_index(doc), run_id="r")
        assert [(e.dst, e.line) for e in edges] == [("python:pathlib.Path:0-0:glob:unresolved", 3),
                                                     ("python:pathlib.Path:0-0:glob:unresolved", 6)]
        assert all(e.dst_ref is not None and e.meta["call_construct"] == "method" for e in edges)

    def test_a_relationship_to_an_external_callable_is_not_a_call_and_is_dropped(self) -> None:
        impl, base = f"{PKG} `m`/C#run().", "scip-python python python-stdlib 3.11 threading/Thread#run()."
        doc = scip_pb2.Document(
            relative_path="m.py",
            symbols=[scip_pb2.SymbolInformation(
                symbol=impl, relationships=[scip_pb2.Relationship(symbol=base, is_implementation=True)],
            )],
            occurrences=[scip_pb2.Occurrence(symbol=impl, symbol_roles=DEF, range=[0, 4, 7])],
        )
        symbols, edges = translate_scip_python_to_hg(_index(doc), run_id="r")
        assert [s.name for s in symbols] == ["run"]
        assert edges == []  # an `implements` edge to an external method is neither pairable nor typable
