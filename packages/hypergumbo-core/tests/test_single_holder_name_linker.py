# SPDX-License-Identifier: AGPL-3.0-or-later
"""The last resort of ADR-0064: a call bound by its name when ONE declaration
holds that name, and refused whenever the call's own text points elsewhere."""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.import_scope import MODULES_ARE_FILES, ImportScope
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.single_holder_name import link_single_holder_name

CALLER_FILE = "src/main.ts"


def _sym(kind: str, name: str, path: str, lines: tuple[int, int] = (1, 5),
         language: str = "typescript") -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language=language, path=path,
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _caller(language: str = "typescript", path: str = CALLER_FILE) -> Symbol:
    return _sym("function", "run", path, (10, 20), language)


def _member_call(caller: Symbol, callee: str, **facts: object) -> Edge:
    """``x.callee()``: a call written on a receiver."""
    return make_unresolved_edge(
        caller.language, caller.id, callee, 12, "test-pass", "test-run",
        call_construct="method", **facts,
    )


def _bare_call(caller: Symbol, callee: str) -> Edge:
    """``callee()``: no receiver."""
    return make_unresolved_edge(caller.language, caller.id, callee, 12, "test-pass", "test-run")


def _files() -> ImportScope:
    return ImportScope(module_kind=MODULES_ARE_FILES)


def _link(symbols: list[Symbol], edges: list[Edge], scope: ImportScope | None,
          path: str = CALLER_FILE) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=edges,
        import_scopes={} if scope is None else {path: scope},
    )
    return link_single_holder_name(ctx).edges


def test_a_call_on_an_untyped_receiver_is_bound_to_the_one_holder_of_its_name() -> None:
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    stub = _member_call(caller, "drain")
    (edge,) = _link([holder, caller], [stub], _files())
    assert (edge.src, edge.dst, edge.edge_type, edge.is_resolved) == (caller.id, holder.id, "calls", True)
    assert edge.meta["resolved_by"] == "name"
    assert edge.confidence == 0.7
    assert edge.derived_from[0] == stub.id


def test_two_holders_of_the_name_bind_nothing() -> None:
    holders = [_sym("method", "Queue.drain", "src/queue.ts"), _sym("method", "Pool.drain", "src/pool.ts")]
    caller = _caller()
    assert _link([*holders, caller], [_member_call(caller, "drain")], _files()) == []


def test_a_file_whose_analyzer_handed_over_no_table_is_left_alone() -> None:
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    assert _link([holder, caller], [_member_call(caller, "drain")], None) == []


def test_a_fact_the_shared_lookup_already_read_refuses_the_name() -> None:
    """The caller named the receiver's type, or its own type: the type lookup
    and the hierarchy walk have answered. A library's ``List<T>.drain`` must
    not become the repository's one ``drain``."""
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    for facts in (
        {"receiver_type_hint": "List<string>"},
        {"enclosing_class": "Main"},
        {"enclosing_class": "Main", "super_receiver": True},
        {"inherited_field_receiver": "queue"},
        {"stdlib_member_name": True},
    ):
        assert _link([holder, caller], [_member_call(caller, "drain", **facts)], _files()) == [], facts


def test_a_qualifier_binds_by_name_only_when_the_files_imports_bind_it_to_the_repository() -> None:
    """``console.log()`` names an owner nothing in the file binds: it is a
    global. ``store.drain()`` with ``import {store} from './store'`` names an
    object of the repository whose type the analyzer could not say."""
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    stub = _member_call(caller, "drain", qualifier="store")
    assert _link([holder, caller], [stub], _files()) == []
    named = _files()
    named.add_named("src/store.ts", "store")
    (edge,) = _link([holder, caller], [stub], named)
    assert edge.dst == holder.id
    namespace = _files()
    namespace.add_namespace("src/index.ts", "store")
    (edge,) = _link([holder, caller], [_member_call(caller, "drain", qualifier="store.inner")], namespace)
    assert edge.dst == holder.id


def test_a_qualifier_in_a_language_of_declared_names_never_binds_by_name() -> None:
    """C#: a qualifier is a type or a namespace, and the type lookup is the
    whole answer (an alias of the table included)."""
    holder = _sym("method", "Queue.Drain", "Queue.cs", language="csharp")
    caller = _caller("csharp", "Main.cs")
    scope = ImportScope()
    scope.add_named("App", "Queue", alias="Q")
    stub = _member_call(caller, "Drain", qualifier="Q")
    assert _link([holder, caller], [stub], scope, path="Main.cs") == []
    # With no qualifier and no type the same call is bound.
    (edge,) = _link([holder, caller], [_member_call(caller, "Drain")], scope, path="Main.cs")
    assert edge.dst == holder.id


def test_a_bare_call_binds_by_name_only_through_a_binding_of_the_file() -> None:
    """A bare name is lexical, imported or a global. ``describe()`` with no
    binding is a global. ``helper()`` imported from a barrel file that does not
    declare it (a re-export) is a function of the repository: its ORIGINAL
    name, with one holder, is bound."""
    holder = _sym("function", "helper", "src/impl.ts")
    caller = _caller()
    assert _link([holder, caller], [_bare_call(caller, "helper")], _files()) == []
    barrel = _files()
    barrel.add_named("src/index.ts", "helper", alias="h")
    (edge,) = _link([holder, caller], [_bare_call(caller, "h")], barrel)
    assert (edge.dst, edge.meta["resolved_by"]) == (holder.id, "name")


def test_a_stub_another_linker_resolved_is_not_bound_again() -> None:
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    other = _sym("method", "Base.drain", "src/base.js", language="python")
    caller = _caller()
    stub = _member_call(caller, "drain")
    earlier = Edge.create(
        src=caller.id, dst=other.id, edge_type="calls", line=12, origin="earlier-linker",
        origin_run_id="r", evidence_type="ast_call", is_resolved=True,
        derived_from=[stub.id, other.id], resolved_by="receiver_type",
    )
    assert _link([holder, other, caller], [stub, earlier], _files()) == []


def test_one_edge_per_caller_and_target() -> None:
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    first, second = _member_call(caller, "drain"), _member_call(caller, "drain")
    second.line = 14
    assert len(_link([holder, caller], [first, second], _files())) == 1


def test_holders_are_counted_in_the_callers_language_and_are_callables() -> None:
    """A Python ``drain`` is no holder for a TypeScript call; a field, a
    class or a variable of that name is not something the name step binds.
    JavaScript and TypeScript are one language here, and the caller of a call
    in a component's script can be the component FILE, labelled ``vue``."""
    foreign = _sym("method", "Queue.drain", "queue.py", language="python")
    field = _sym("field", "Queue.drain", "src/q.ts")
    caller = _caller()
    assert _link([foreign, field, caller], [_member_call(caller, "drain")], _files()) == []
    holder = _sym("method", "Pool.drain", "src/pool.js", language="javascript")
    (edge,) = _link([foreign, field, holder, caller], [_member_call(caller, "drain")], _files())
    assert edge.dst == holder.id
    component = _sym("file", "Main.vue", "src/Main.vue", (1, 40), "vue")
    (edge,) = _link([holder, component], [_member_call(component, "drain")], _files(), path="src/Main.vue")
    assert edge.dst == holder.id


def test_edges_that_are_not_open_call_stubs_are_ignored() -> None:
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    imports = Edge.create(
        src=caller.id, dst="typescript:external:0-0:drain:unresolved", edge_type="imports",
        line=1, origin="t", origin_run_id="r", evidence_type="ast_import",
    )
    orphan = _member_call(_sym("function", "gone", "src/gone.ts"), "drain")
    shapeless = Edge.create(
        src=caller.id, dst="drain:unresolved", edge_type="calls", line=3, origin="t",
        origin_run_id="r", evidence_type="ast_call", is_resolved=False,
        meta={"call_construct": "method"},
    )
    assert _link([holder, caller], [imports, orphan, shapeless], _files()) == []


def test_a_holder_is_a_method_for_a_call_on_a_receiver_and_a_function_for_a_bare_call() -> None:
    """``cd.shout()`` on an imported object is not the module-level function
    ``shout``; a bare ``drain()`` is not some class's method ``drain``. And a
    function of the name does not make a second holder for the method."""
    function = _sym("function", "shout", "src/c.ts")
    method = _sym("method", "Queue.drain", "src/queue.ts")
    spare = _sym("function", "drain", "src/drain.ts")
    caller = _caller()
    scope = _files()
    scope.add_named("src/c.ts", "default", alias="cd")
    scope.add_named("src/index.ts", "drain")
    symbols = [function, method, spare, caller]
    assert _link(symbols, [_member_call(caller, "shout", qualifier="cd")], scope) == []
    (edge,) = _link(symbols, [_member_call(caller, "drain")], scope)
    assert edge.dst == method.id
    (edge,) = _link(symbols, [_bare_call(caller, "drain")], scope)
    assert edge.dst == spare.id


def test_a_call_whose_producer_stated_a_module_is_not_bound_by_name() -> None:
    """``require('lib').drain()``: the stub says the callee lives in ``lib``.
    A qualifier the file's table binds to the repository still decides for
    itself, whatever module spelling the stub carries."""
    holder = _sym("method", "Queue.drain", "src/queue.ts")
    caller = _caller()
    stated = make_unresolved_edge(
        "typescript", caller.id, "drain", 12, "test-pass", "test-run",
        module_hint="lib", call_construct="method",
    )
    assert _link([holder, caller], [stated], _files()) == []
    scope = _files()
    scope.add_named("src/store.ts", "store")
    bound = make_unresolved_edge(
        "typescript", caller.id, "drain", 12, "test-pass", "test-run",
        module_hint="./store", call_construct="method", qualifier="store",
    )
    (edge,) = _link([holder, caller], [bound], scope)
    assert edge.dst == holder.id
