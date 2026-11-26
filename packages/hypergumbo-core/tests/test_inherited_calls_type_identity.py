# SPDX-License-Identifier: AGPL-3.0-or-later
"""A typed receiver's type is a declaration, not a name (ADR-0064).

``inherited_calls`` Site 2 looks a method up on the receiver's type. The type
reaches it as a NAME on the unresolved edge (``receiver_type_hint``), and
until a file's import table said otherwise the name was looked up among every
class of the language: one namesake bound, two refused. With the table
(``LinkerContext.import_scopes``) the linker asks which declaration the name
means from the caller's place (``type_visibility.visible_types``): the one it
means is bound even when a namesake exists, and a name that means no
declaration of the repository from there is not bound to a namesake at all.

A file with no table keeps the name lookup, so a language whose analyzer has
not adopted the table does not move.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/inherited_calls.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
from hypergumbo_core.linkers.registry import LinkerContext

CALLER_FILE = "web/Handler.cs"


def _sym(kind: str, name: str, qualified: str | None, path: str, lines: tuple[int, int]) -> Symbol:
    return Symbol(
        id=f"csharp:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language="csharp", path=path, qualified_name=qualified,
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _type_with_method(namespace: str | None, type_name: str, method: str, path: str) -> list[Symbol]:
    """A class and one method of it, in ``path``."""
    qualified = f"{namespace}.{type_name}" if namespace else None
    return [
        _sym("class", type_name, qualified, path, (1, 9)),
        _sym("method", f"{type_name}.{method}", f"{qualified}.{method}" if qualified else None,
             path, (3, 5)),
    ]


def _caller() -> Symbol:
    return _sym("method", "Handler.Get", "App.Web.Handler.Get", CALLER_FILE, (10, 20))


def _stub(caller: Symbol, callee: str, **facts: str) -> Edge:
    return make_unresolved_edge(
        "csharp", caller.id, callee, 12, "test-pass", "test-run", **facts,
    )


def _scope(*usings: str) -> ImportScope:
    scope = ImportScope()
    for namespace in usings:
        scope.add_open_wildcard(namespace)
    return scope


def _link(symbols: list[Symbol], stub: Edge, scope: ImportScope | None) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[stub],
        import_scopes={} if scope is None else {CALLER_FILE: scope},
    )
    return link_inherited_calls(ctx).edges


def _two_conns() -> tuple[list[Symbol], Symbol, Symbol]:
    wanted = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    return [*wanted, *namesake], wanted[1], namesake[1]


def test_without_a_table_two_namesakes_are_refused_as_before() -> None:
    symbols, _, _ = _two_conns()
    caller = _caller()
    assert _link([*symbols, caller], _stub(caller, "Execute", receiver_type_hint="Conn"), None) == []


def test_the_table_settles_which_namesake_the_file_means() -> None:
    symbols, wanted, _ = _two_conns()
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*symbols, caller], stub, _scope("App.Db"))
    assert edge.dst == wanted.id
    assert edge.is_resolved
    assert edge.meta["resolved_by"] == "receiver_type"
    assert edge.derived_from[0] == stub.id


def test_a_lone_namesake_the_file_cannot_see_is_not_bound() -> None:
    """One ``Conn`` in the repository, in a namespace the caller's file does
    not import: the name means a library's type here. Without the table the
    lone namesake is bound; with it, nothing is."""
    lone = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    assert [e.dst for e in _link([*lone, caller], stub, None)] == [lone[1].id]
    assert _link([*lone, caller], stub, _scope("System.Data")) == []


def test_a_name_two_usings_both_supply_is_refused() -> None:
    symbols, _, _ = _two_conns()
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    assert _link([*symbols, caller], stub, _scope("App.Db", "Legacy")) == []


def test_a_type_of_the_callers_own_namespace_needs_no_using() -> None:
    near = _type_with_method("App.Web", "Conn", "Execute", "web/Conn.cs")
    far = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*near, *far, caller], stub, _scope())
    assert edge.dst == near[1].id


def test_a_qualifier_names_the_type_and_the_edge_says_so() -> None:
    """``Util.Helper()``: the call names its owner. The same lookup settles
    which ``Util``; the mark is ``qualified``, not ``receiver_type``."""
    wanted = _type_with_method("App.Tools", "Util", "Helper", "tools/Util.cs")
    namesake = _type_with_method("Legacy", "Util", "Helper", "legacy/Util.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", qualifier="Util")
    assert stub.meta["qualifier"] == "Util"
    (edge,) = _link([*wanted, *namesake, caller], stub, _scope("App.Tools"))
    assert edge.dst == wanted[1].id
    assert edge.meta["resolved_by"] == "qualified"

    full = _stub(caller, "Helper", qualifier="Legacy.Util")
    (edge,) = _link([*wanted, *namesake, caller], full, _scope("App.Tools"))
    assert edge.dst == namesake[1].id


def test_a_qualifier_is_not_read_in_a_file_with_no_table() -> None:
    """The fact is new with the table; a file without one has no producer of
    it, and nothing is inferred from it there."""
    wanted = _type_with_method("App.Tools", "Util", "Helper", "tools/Util.cs")
    caller = _caller()
    assert _link([*wanted, caller], _stub(caller, "Helper", qualifier="Util"), None) == []


def test_candidates_with_no_qualified_name_keep_the_name_lookup() -> None:
    """A producer that records no qualified names cannot be judged by
    visibility: the table changes nothing for it."""
    lone = _type_with_method(None, "Conn", "Execute", "db/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([*lone, caller], stub, _scope("Whatever"))
    assert edge.dst == lone[1].id


# --- one type in several files, and the caller's own type ---------------------


def test_a_type_declared_in_two_files_is_one_type() -> None:
    """A partial class: two declarations, one qualified name. The name is
    settled (it is not a tie), and a method of either part is on the type."""
    first = _sym("class", "Conn", "App.Db.Conn", "db/Conn.cs", (1, 9))
    second = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.Extra.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Conn")
    (edge,) = _link([first, *second, *namesake, caller], stub, _scope("App.Db"))
    assert edge.dst == second[1].id


def _handler_with(method: str | None, namespace: str, path: str) -> list[Symbol]:
    symbols = [_sym("class", "Handler", f"{namespace}.Handler", path, (1, 30))]
    if method:
        symbols.append(_sym("method", f"Handler.{method}", f"{namespace}.Handler.{method}", path, (22, 24)))
    return symbols


def test_a_bare_call_starts_at_the_callers_own_type_not_at_a_namesake() -> None:
    """``Helper()`` inside ``App.Web.Handler``, which has no ``Helper``; an
    unrelated ``Other.Handler`` has one. By name the namesake's method is
    bound (the first class of that name that has it). With the table the walk
    starts at the type the caller is declared in, and finds nothing."""
    own = _handler_with(None, "App.Web", CALLER_FILE)
    namesake = _handler_with("Helper", "Other", "other/Handler.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    assert [e.dst for e in _link([*own, *namesake, caller], stub, None)] == [namesake[1].id]
    assert _link([*own, *namesake, caller], stub, _scope()) == []


def test_a_bare_call_finds_a_method_of_another_part_of_its_own_type() -> None:
    own = _handler_with(None, "App.Web", CALLER_FILE)
    other_part = _handler_with("Helper", "App.Web", "web/Handler.Helpers.cs")
    namesake = _handler_with("Helper", "Other", "other/Handler.cs")
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    (edge,) = _link([*own, *namesake, *other_part, caller], stub, _scope())
    assert edge.dst == other_part[1].id
    assert edge.meta["resolved_by"] == "own_type"


def test_a_caller_whose_type_has_no_qualified_name_keeps_the_name_walk() -> None:
    own = [_sym("class", "Handler", None, CALLER_FILE, (1, 30)),
           _sym("method", "Handler.Helper", None, CALLER_FILE, (22, 24))]
    caller = _sym("method", "Handler.Get", None, CALLER_FILE, (10, 20))
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    (edge,) = _link([*own, caller], stub, _scope())
    assert edge.dst == own[1].id


# --- a generic type and its non-generic namesake are two types ----------------


def _arity(symbols: list[Symbol], arity: int) -> list[Symbol]:
    symbols[0].meta = {"type_arity": arity}
    return symbols


def test_the_number_of_type_arguments_is_part_of_which_type_is_meant() -> None:
    """``Policy`` and ``Policy<TResult>`` share a name and a qualified name and
    are different types. Where the declarations say how many type parameters
    they take, the hint's own type arguments choose."""
    plain = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs"), 0)
    generic = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.cs"), 1)
    caller = _caller()
    symbols = [*plain, *generic, caller]
    (edge,) = _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy<int>"), _scope("App.Db"))
    assert edge.dst == generic[1].id
    (edge,) = _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy"), _scope("App.Db"))
    assert edge.dst == plain[1].id
    # No declaration takes two: the name means no type of the repository here.
    assert _link(symbols, _stub(caller, "Execute", receiver_type_hint="Policy<int, string>"),
                 _scope("App.Db")) == []


def test_parts_of_one_generic_type_are_still_one_type() -> None:
    first = _arity([_sym("class", "Policy", "App.Db.Policy", "db/Policy.TResult.cs", (1, 9))], 1)
    second = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.Extra.cs"), 1)
    plain = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs"), 0)
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Policy<Dictionary<string, int>>")
    (edge,) = _link([*first, *second, *plain, caller], stub, _scope("App.Db"))
    assert edge.dst == second[1].id


def test_declarations_that_do_not_say_their_arity_are_told_apart_by_name_only() -> None:
    """A producer that records no arity: the two namesakes cannot be told
    apart, and they are not one type either. Refused."""
    plain = _type_with_method("App.Db", "Policy", "Execute", "db/Policy.cs")
    generic = _arity(_type_with_method("App.Db", "Policy", "Execute", "db/Policy.TResult.cs"), 1)
    caller = _caller()
    stub = _stub(caller, "Execute", receiver_type_hint="Policy<int>")
    (edge,) = _link([*plain, *generic, caller], stub, _scope("App.Db"))
    # One qualified name, arity known for one declaration only: treated as
    # parts of one type, as before the arity existed (first part with the method).
    assert edge.dst in (plain[1].id, generic[1].id)


def test_a_bare_call_stays_in_the_generic_or_the_plain_type_it_is_written_in() -> None:
    """``Helper()`` inside the non-generic ``Handler``; the generic
    ``Handler<T>`` of the same namespace has a ``Helper``, the plain one does
    not. The caller is declared in the plain one."""
    plain = _arity([_sym("class", "Handler", "App.Web.Handler", CALLER_FILE, (1, 30))], 0)
    generic = _arity(_handler_with("Helper", "App.Web", "web/Handler.T.cs"), 1)
    caller = _caller()
    stub = _stub(caller, "Helper", enclosing_class="Handler")
    assert _link([*plain, *generic, caller], stub, _scope()) == []
    other_part = _arity(_handler_with("Helper", "App.Web", "web/Handler.Helpers.cs"), 0)
    (edge,) = _link([*plain, *generic, *other_part, caller], stub, _scope())
    assert edge.dst == other_part[1].id


# --- what counts as a type ------------------------------------------------------


def test_every_type_like_kind_can_be_a_receivers_type() -> None:
    """A record, an enum, an actor: a method declared on one is a method on a
    type. The linker reads the kinds from the symbol-kind registry (plus the
    Ruby ``module`` it always had), not from a list of its own, which had
    left ``record`` out: a C# ``Point.Origin()`` on a record was refused."""
    from hypergumbo_core.symbol_kinds import type_like_kind_names

    caller = _caller()
    for kind in sorted(type_like_kind_names() | {"module"}):
        holder = [
            _sym(kind, "Point", "App.Geo.Point", "geo/Point.cs", (1, 9)),
            _sym("method", "Point.Origin", "App.Geo.Point.Origin", "geo/Point.cs", (3, 5)),
        ]
        stub = _stub(caller, "Origin", qualifier="Point")
        edges = _link([*holder, caller], stub, _scope("App.Geo"))
        assert [e.dst for e in edges] == [holder[1].id], kind


# --- a call on super / base starts at the parent ---------------------------------


def _extends(child: Symbol, parent: Symbol) -> Edge:
    return Edge.create(src=child.id, dst=parent.id, edge_type="extends", line=1,
                       origin="test", origin_run_id="test-run")


def _link_all(symbols: list[Symbol], edges: list[Edge]) -> list[Edge]:
    ctx = LinkerContext(repo_root=Path("/"), symbols=symbols, edges=edges)
    return link_inherited_calls(ctx).edges


def test_a_call_on_the_parent_does_not_bind_the_callers_own_override() -> None:
    """``base.Run()`` inside ``Child.Run``: the callee is the parent's Run.
    With only ``enclosing_class`` the walk starts AT the caller's type and
    finds the override, which is the caller itself. The fact
    ``super_receiver`` makes it start one step up."""
    base = _type_with_method("App", "Base", "Run", "Base.cs")
    child = _type_with_method("App", "Child", "Run", "Child.cs")
    caller = child[1]
    extends = _extends(child[0], base[0])
    plain = _stub(caller, "Run", enclosing_class="Child")
    assert [e.dst for e in _link_all([*base, *child], [extends, plain])] == [caller.id]
    up = _stub(caller, "Run", enclosing_class="Child", super_receiver=True)
    assert up.meta["super_receiver"] is True
    (edge,) = _link_all([*base, *child], [extends, up])
    assert edge.dst == base[1].id
    assert edge.meta["resolved_by"] == "own_type"


def test_a_call_on_the_parent_reaches_a_grandparent_and_stops_where_none_has_it() -> None:
    top = _type_with_method("App", "Top", "Run", "Top.cs")
    middle = [_sym("class", "Middle", "App.Middle", "Middle.cs", (1, 9))]
    child = _type_with_method("App", "Child", "Run", "Child.cs")
    edges = [_extends(child[0], middle[0]), _extends(middle[0], top[0])]
    up = _stub(child[1], "Run", enclosing_class="Child", super_receiver=True)
    (edge,) = _link_all([*top, *middle, *child], [*edges, up])
    assert edge.dst == top[1].id
    # No ancestor declares it (the parent is a library's type): unresolved,
    # never the caller's own method.
    alone = _type_with_method("App", "Solo", "Run", "Solo.cs")
    lone = _stub(alone[1], "Run", enclosing_class="Solo", super_receiver=True)
    assert _link_all(alone, [lone]) == []


# --- a member that is not a method can be what a call invokes --------------------


def _type_with_member(kind: str, member: str) -> list[Symbol]:
    return [
        _sym("class", "Clock", "App.Util.Clock", "util/Clock.cs", (1, 9)),
        _sym(kind, f"Clock.{member}", f"App.Util.Clock.{member}", "util/Clock.cs", (3, 3)),
    ]


def test_an_invoked_field_or_property_of_the_type_is_the_callee() -> None:
    """``public static Func<TimeSpan, Task> Sleep = Task.Delay;`` then
    ``Clock.Sleep(t)``: the call invokes what the member holds, and the member
    is all the source names. Once the type is settled and no method of that
    name is on it, the member is bound."""
    caller = _caller()
    for kind in ("field", "property"):
        holder = _type_with_member(kind, "Sleep")
        named = _stub(caller, "Sleep", qualifier="Clock")
        (edge,) = _link([*holder, caller], named, _scope("App.Util"))
        assert (edge.dst, edge.edge_type, edge.meta["resolved_by"]) == (holder[1].id, "calls", "qualified")
        typed = _stub(caller, "Sleep", receiver_type_hint="Clock")
        (edge,) = _link([*holder, caller], typed, _scope("App.Util"))
        assert (edge.dst, edge.meta["resolved_by"]) == (holder[1].id, "receiver_type")


def test_a_method_of_that_name_is_preferred_to_a_member() -> None:
    holder = _type_with_member("field", "Sleep")
    method = _sym("method", "Clock.Sleep", "App.Util.Clock.Sleep", "util/Clock.cs", (5, 7))
    caller = _caller()
    (edge,) = _link([*holder, method, caller], _stub(caller, "Sleep", qualifier="Clock"), _scope("App.Util"))
    assert edge.dst == method.id


def test_a_member_is_bound_only_where_the_type_is_settled() -> None:
    """With no table the type is found by name alone, and a name-found type's
    field is not a reason to bind a call: unchanged, nothing bound."""
    holder = _type_with_member("field", "Sleep")
    caller = _caller()
    assert _link([*holder, caller], _stub(caller, "Sleep", receiver_type_hint="Clock"), None) == []


# --- an alias names a type whose own name is different ----------------------------


def test_a_receiver_typed_by_an_alias_reaches_the_aliased_type() -> None:
    """``using Svc = App.Db.Conn;`` and a receiver declared ``Svc``: the type
    is ``Conn``. The candidates are found by the ALIASED name, not by the
    alias, which no class is called."""
    conn = _type_with_method("App.Db", "Conn", "Execute", "db/Conn.cs")
    namesake = _type_with_method("Legacy", "Conn", "Execute", "legacy/Conn.cs")
    scope = _scope()
    scope.add_named("App.Db", "Conn", alias="Svc")
    caller = _caller()
    (edge,) = _link([*conn, *namesake, caller], _stub(caller, "Execute", receiver_type_hint="Svc"), scope)
    assert edge.dst == conn[1].id


# --- a language whose imports name files ------------------------------------------


def test_a_typed_receiver_follows_a_named_import_to_its_file() -> None:
    """TypeScript-shaped: two classes ``Conn`` in two files, no namespaces.
    The caller's file imports one of them; by name the two were a tie."""
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    def ts(kind: str, name: str, path: str, lines: tuple[int, int]) -> Symbol:
        return Symbol(
            id=f"typescript:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
            language="typescript", path=path,
            span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
            origin="test", origin_run_id="test-run",
        )

    wanted = [ts("class", "Conn", "src/db.ts", (1, 9)), ts("method", "Conn.execute", "src/db.ts", (3, 5))]
    namesake = [ts("class", "Conn", "legacy/db.ts", (1, 9)), ts("method", "Conn.execute", "legacy/db.ts", (3, 5))]
    caller = ts("function", "run", "src/main.ts", (10, 20))
    stub = make_unresolved_edge(
        "typescript", caller.id, "execute", 12, "test-pass", "test-run", receiver_type_hint="Conn",
    )
    symbols = [*wanted, *namesake, caller]
    assert link_inherited_calls(LinkerContext(repo_root=Path("/"), symbols=symbols, edges=[stub])).edges == []
    scope = ImportScope(module_kind=MODULES_ARE_FILES)
    scope.add_named("src/db.ts", "Conn")
    ctx = LinkerContext(repo_root=Path("/"), symbols=symbols, edges=[stub],
                        import_scopes={"src/main.ts": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == wanted[1].id
    assert edge.meta["resolved_by"] == "receiver_type"


# --- a function called by its imported name ----------------------------------------


def _ts(kind: str, name: str, path: str, lines: tuple[int, int] = (1, 5)) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language="typescript", path=path,
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _ts_stub(caller: Symbol, callee: str, **facts: str) -> Edge:
    return make_unresolved_edge("typescript", caller.id, callee, 12, "test-pass", "test-run", **facts)


def _file_scope() -> ImportScope:
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    return ImportScope(module_kind=MODULES_ARE_FILES)


def _ts_link(symbols: list[Symbol], stub: Edge, scope: ImportScope | None) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[stub],
        import_scopes={} if scope is None else {"src/main.ts": scope},
    )
    return link_inherited_calls(ctx).edges


def test_a_bare_call_to_an_imported_function_reaches_the_file_the_import_names() -> None:
    """``import {helper} from './util'``; ``helper()``. Two functions named
    helper exist; the import says which."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "helper", "legacy/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper")
    stub = _ts_stub(caller, "helper")
    (edge,) = _ts_link([wanted, decoy, caller], stub, scope)
    assert (edge.dst, edge.edge_type, edge.is_resolved) == (wanted.id, "calls", True)
    assert edge.meta["resolved_by"] == "import"
    assert edge.derived_from[0] == stub.id
    # No binding for the name, or no table at all: nothing is bound.
    assert _ts_link([wanted, decoy, caller], stub, _file_scope()) == []
    assert _ts_link([wanted, decoy, caller], stub, None) == []


def test_a_call_through_a_binding_of_a_directory_reaches_a_function_of_its_files() -> None:
    """Go: ``import "m/pkg/log"``; ``log.Print()``. The import names a
    directory, and the package is the files directly in it: not a
    subdirectory, not a directory of the same last name elsewhere."""
    wanted = _ts("function", "Print", "pkg/log/print.go")
    below = _ts("function", "Print", "pkg/log/internal/print.go")
    elsewhere = _ts("function", "Print", "vendor/log/print.go")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_namespace("pkg/log/", "log")
    stub = _ts_stub(caller, "Print", qualifier="log")
    (edge,) = _ts_link([wanted, below, elsewhere, caller], stub, scope)
    assert (edge.dst, edge.meta["resolved_by"]) == (wanted.id, "import")
    # Two files of the directory declaring the name are a tie.
    twin = _ts("function", "Print", "pkg/log/print_other.go")
    assert _ts_link([wanted, twin, caller], stub, scope) == []
    # A file is still only itself: a binding of the file ``pkg/log`` names no directory.
    file_bound = _file_scope()
    file_bound.add_namespace("pkg/log", "log")
    assert _ts_link([wanted, caller], stub, file_bound) == []


def test_an_imported_function_under_another_local_name() -> None:
    """``import {helper as h} from './util'``; ``h()``."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "h", "legacy/h.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper", alias="h")
    (edge,) = _ts_link([wanted, decoy, caller], _ts_stub(caller, "h"), scope)
    assert edge.dst == wanted.id


def test_a_call_through_a_namespace_binding_reaches_that_files_function() -> None:
    """``import * as util from './util'``; ``util.helper()``."""
    wanted, decoy = _ts("function", "helper", "src/util.ts"), _ts("function", "helper", "legacy/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_namespace("src/util.ts", "util")
    (edge,) = _ts_link([wanted, decoy, caller], _ts_stub(caller, "helper", qualifier="util"), scope)
    assert (edge.dst, edge.meta["resolved_by"]) == (wanted.id, "import")
    # A qualifier that is no binding of the file is not a module of it.
    assert _ts_link([wanted, decoy, caller], _ts_stub(caller, "helper", qualifier="other"), scope) == []


def test_the_bound_file_must_declare_the_function() -> None:
    """A re-export: the binding names a file that does not declare the name.
    Nothing is bound (the lookup does not follow re-exports)."""
    elsewhere = _ts("function", "helper", "src/impl.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/index.ts", "helper")
    assert _ts_link([elsewhere, caller], _ts_stub(caller, "helper"), scope) == []


def test_a_nearer_namesake_of_another_arity_does_not_hide_the_type() -> None:
    """C# looks a type up by name AND arity together. The caller's own
    namespace declares a non-generic ``Registry``; a namespace the file uses
    declares ``Registry<TKey>``. A receiver declared ``Registry<string>`` is
    the generic one: the nearer scope does not declare a type of that arity."""
    nearer = _arity(_type_with_method("App.Web", "Registry", "Dispose", "web/Registry.cs"), 0)
    generic = _arity(_type_with_method("Lib", "Registry", "Dispose", "lib/Registry.cs"), 1)
    caller = _caller()
    symbols = [*nearer, *generic, caller]
    (edge,) = _link(symbols, _stub(caller, "Dispose", receiver_type_hint="Registry<string>"), _scope("Lib"))
    assert edge.dst == generic[1].id
    # Written without type arguments, the same receiver is the nearer one.
    (edge,) = _link(symbols, _stub(caller, "Dispose", receiver_type_hint="Registry"), _scope("Lib"))
    assert edge.dst == nearer[1].id


# --- found on the JavaScript / TypeScript adapter -----------------------------------


def test_the_caller_of_a_component_script_binds_typescript_declarations() -> None:
    """A call at the top of a ``.vue`` script has the component FILE for its
    caller, labelled ``vue``; the function it imports is TypeScript. One
    language for calls (``type_visibility.call_language``)."""
    helper = _ts("function", "helper", "src/util.ts")
    component = Symbol(
        id="vue:src/Main.vue:1-40:Main.vue:file", name="Main.vue", kind="file", language="vue",
        path="src/Main.vue", span=Span(start_line=1, end_line=40, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    scope = _file_scope()
    scope.add_named("src/util.ts", "helper")
    stub = make_unresolved_edge("vue", component.id, "helper", 12, "test-pass", "test-run")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[helper, component], edges=[stub],
                        import_scopes={"src/Main.vue": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == helper.id
    # A typed receiver in the same script: the class is TypeScript too.
    conn = [_ts("class", "Conn", "src/db.ts", (1, 9)), _ts("method", "Conn.execute", "src/db.ts", (3, 5))]
    scope.add_named("src/db.ts", "Conn")
    typed = make_unresolved_edge("vue", component.id, "execute", 14, "test-pass", "test-run",
                                 receiver_type_hint="Conn")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[*conn, component], edges=[typed],
                        import_scopes={"src/Main.vue": scope})
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == conn[1].id


def test_a_call_on_a_receiver_is_not_a_call_of_an_imported_function() -> None:
    """``import {merge} from './util'`` and, elsewhere, ``x.merge()``: the
    member call has a receiver, so the import does not say what it invokes."""
    merge = _ts("function", "merge", "src/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_named("src/util.ts", "merge")
    member = make_unresolved_edge("typescript", caller.id, "merge", 12, "test-pass", "test-run",
                                  call_construct="method")
    assert _ts_link([merge, caller], member, scope) == []


def test_a_type_of_a_language_whose_imports_name_files_is_one_file() -> None:
    """Two classes ``Handler`` in two TypeScript files share the qualified
    name ``Handler`` and are two types. ``this.helper()`` in one of them must
    not find the other's ``helper`` (in C# the two would be parts of one
    partial class)."""
    own = [_ts("class", "Handler", "src/main.ts", (1, 30)),
           Symbol(id="typescript:src/main.ts:10-20:Handler.get:method", name="Handler.get", kind="method",
                  language="typescript", path="src/main.ts", qualified_name="Handler.get",
                  span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
                  origin="test", origin_run_id="test-run")]
    own[0].qualified_name = "Handler"
    other = [_ts("class", "Handler", "legacy/main.ts", (1, 30)), _ts("method", "Handler.helper", "legacy/main.ts", (3, 5))]
    other[0].qualified_name, other[1].qualified_name = "Handler", "Handler.helper"
    stub = make_unresolved_edge("typescript", own[1].id, "helper", 12, "test-pass", "test-run",
                                enclosing_class="Handler")
    assert _ts_link([*own, *other], stub, _file_scope()) == []


# --- through a file that only passes the name on -----------------------------------


def _ts_link_all(symbols: list[Symbol], stub: Edge, scopes: dict[str, ImportScope]) -> list[Edge]:
    ctx = LinkerContext(repo_root=Path("/"), symbols=symbols, edges=[stub], import_scopes=scopes)
    return link_inherited_calls(ctx).edges


def test_an_imported_function_is_followed_through_a_barrel_file() -> None:
    """``import {helper} from './index'``; index.ts: ``export * from './impl'``."""
    wanted, decoy = _ts("function", "helper", "src/impl.ts"), _ts("function", "helper", "legacy/util.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    mine, barrel = _file_scope(), _file_scope()
    mine.add_named("src/index.ts", "helper")
    barrel.add_reexport_all("src/impl.ts")
    scopes = {"src/main.ts": mine, "src/index.ts": barrel}
    (edge,) = _ts_link_all([wanted, decoy, caller], _ts_stub(caller, "helper"), scopes)
    assert (edge.dst, edge.meta["resolved_by"]) == (wanted.id, "import")


def test_a_typed_receiver_is_followed_through_a_reexport() -> None:
    conn = [_ts("class", "Conn", "src/db.ts", (1, 9)), _ts("method", "Conn.execute", "src/db.ts", (3, 5))]
    decoy = [_ts("class", "Conn", "legacy/db.ts", (1, 9)), _ts("method", "Conn.execute", "legacy/db.ts", (3, 5))]
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    mine, barrel = _file_scope(), _file_scope()
    mine.add_named("src/index.ts", "Conn")
    barrel.add_reexport("src/db.ts", "Conn")
    scopes = {"src/main.ts": mine, "src/index.ts": barrel}
    stub = _ts_stub(caller, "execute", receiver_type_hint="Conn")
    (edge,) = _ts_link_all([*conn, *decoy, caller], stub, scopes)
    assert (edge.dst, edge.meta["resolved_by"]) == (conn[1].id, "receiver_type")


def test_a_bare_call_to_a_function_a_wildcard_import_supplies() -> None:
    """Rust ``use crate::schema::*; setup()``. Two wildcard files that both
    declare the name are a tie."""
    wanted, other = _ts("function", "setup", "src/schema.ts"), _ts("function", "setup", "src/other.ts")
    caller = _ts("function", "run", "src/main.ts", (10, 20))
    scope = _file_scope()
    scope.add_open_wildcard("src/schema.ts")
    stub = _ts_stub(caller, "setup")
    (edge,) = _ts_link_all([wanted, other, caller], stub, {"src/main.ts": scope})
    assert (edge.dst, edge.meta["resolved_by"]) == (wanted.id, "import")
    scope.add_open_wildcard("src/other.ts")
    assert _ts_link_all([wanted, other, caller], stub, {"src/main.ts": scope}) == []


# --- a declaration named by a bare call or a ``new``, in a language of declared names ----


def _decl(kind: str, package: str, name: str, path: str, language: str = "kotlin",
          lines: tuple[int, int] = (1, 9)) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{lines[0]}-{lines[1]}:{name}:{kind}", name=name, kind=kind,
        language=language, path=path, qualified_name=f"{package}.{name}",
        span=Span(start_line=lines[0], end_line=lines[1], start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )


def _named_link(symbols: list[Symbol], stub: Edge, scope: ImportScope | None, path: str) -> list[Edge]:
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[stub],
        import_scopes={} if scope is None else {path: scope},
    )
    return link_inherited_calls(ctx).edges


def test_a_bare_call_names_a_type_of_the_callers_package_or_an_imported_one() -> None:
    """Kotlin writes a constructor as a call. ``Request(url)`` in package
    ``okhttp3`` is ``okhttp3.Request``, not a ``Request`` nested in a test
    helper elsewhere; with ``import fake.Request`` it is that one."""
    own = _decl("class", "okhttp3", "Request", "okhttp3/Request.kt")
    other = _decl("interface", "fake", "Request", "fake/FakeDns.kt")
    caller = _decl("function", "okhttp3", "run", "okhttp3/Cache.kt", lines=(10, 20))
    stub = make_unresolved_edge("kotlin", caller.id, "Request", 12, "test-pass", "test-run")
    (edge,) = _named_link([own, other, caller], stub, ImportScope(), caller.path)
    assert (edge.dst, edge.edge_type, edge.is_resolved) == (own.id, "calls", True)
    assert edge.meta["resolved_by"] == "same_module"
    assert edge.derived_from[0] == stub.id
    imported = ImportScope()
    imported.add_named("fake", "Request")
    (edge,) = _named_link([own, other, caller], stub, imported, caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (other.id, "import")
    # No table for the file: the stub is left as it is.
    assert _named_link([own, other, caller], stub, None, caller.path) == []


def test_a_bare_call_names_a_function_through_the_table() -> None:
    """A top-level function of the caller's package, or an imported one;
    two wildcard packages that both declare it are a tie; a name nothing in
    scope declares is a library's."""
    mine = _decl("function", "app", "helper", "app/Util.kt")
    theirs = _decl("function", "lib", "helper", "lib/Util.kt")
    third = _decl("function", "ext", "helper", "ext/Util.kt")
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))
    stub = make_unresolved_edge("kotlin", caller.id, "helper", 12, "test-pass", "test-run")
    symbols = [mine, theirs, third, caller]
    (edge,) = _named_link(symbols, stub, ImportScope(), caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (mine.id, "same_module")
    elsewhere = _decl("function", "web", "run", "web/Main.kt", lines=(10, 20))
    far = make_unresolved_edge("kotlin", elsewhere.id, "helper", 12, "test-pass", "test-run")
    assert _named_link([*symbols, elsewhere], far, ImportScope(), elsewhere.path) == []
    star = ImportScope()
    star.add_open_wildcard("lib")
    (edge,) = _named_link([*symbols, elsewhere], far, star, elsewhere.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (theirs.id, "import")
    star.add_open_wildcard("ext")
    assert _named_link([*symbols, elsewhere], far, star, elsewhere.path) == []


def test_a_function_and_a_type_of_one_name_in_one_scope_are_a_tie() -> None:
    function = _decl("function", "app", "Widget", "app/Factory.kt")
    cls = _decl("class", "app", "Widget", "app/Widget.kt")
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))
    stub = make_unresolved_edge("kotlin", caller.id, "Widget", 12, "test-pass", "test-run")
    assert _named_link([function, cls, caller], stub, ImportScope(), caller.path) == []


def test_overloads_of_one_function_are_one_declaration() -> None:
    """Two ``fun helper`` of one package share a qualified name: the call is
    bound to the first of them in the file (overload selection is not made)."""
    first = _decl("function", "app", "helper", "app/Util.kt", lines=(1, 3))
    second = _decl("function", "app", "helper", "app/Util.kt", lines=(5, 7))
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))
    stub = make_unresolved_edge("kotlin", caller.id, "helper", 12, "test-pass", "test-run")
    (edge,) = _named_link([second, first, caller], stub, ImportScope(), caller.path)
    assert edge.dst == first.id


def test_the_callers_own_file_declares_the_function_it_calls() -> None:
    """Two files that declare one qualified name are never loaded together
    (two PHP scripts that each define ``hardAssert``): a call in one of them
    is to its own, whichever path sorts first."""
    early = _decl("function", "", "hardAssert", "interop/client.php", language="php")
    late = _decl("function", "", "hardAssert", "qps/client.php", language="php")
    for own, other in ((early, late), (late, early)):
        caller = _decl("function", "", "run", own.path, language="php", lines=(10, 20))
        stub = make_unresolved_edge("php", caller.id, "hardAssert", 12, "test-pass", "test-run")
        (edge,) = _named_link([other, own, caller], stub, ImportScope(), caller.path)
        assert edge.dst == own.id


def test_a_bare_call_cannot_name_a_type_where_the_language_writes_new() -> None:
    """Java and PHP construct with ``new``: a bare ``Request()`` is a method
    or a function, never the class of that name."""
    cls = _decl("class", "app", "Request", "app/Request.java", language="java")
    caller = _decl("method", "app", "Main.run", "app/Main.java", language="java", lines=(10, 20))
    stub = make_unresolved_edge("java", caller.id, "Request", 12, "test-pass", "test-run")
    assert _named_link([cls, caller], stub, ImportScope(), caller.path) == []


def test_new_names_a_type_through_the_table() -> None:
    """``new Conn()``: an unresolved ``instantiates`` stub whose callee is the
    type as written. The type of the caller's package, the imported one, or a
    qualified one; never a namesake that is not visible."""
    own = _decl("class", "app.db", "Conn", "app/db/Conn.java", language="java")
    other = _decl("class", "legacy", "Conn", "legacy/Conn.java", language="java")
    caller = Symbol(
        id="java:app/db/Main.java:10-20:Main.run:method", name="Main.run", kind="method",
        language="java", path="app/db/Main.java", qualified_name="app.db.Main.run",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    stub = make_unresolved_edge(
        "java", caller.id, "Conn", 12, "test-pass", "test-run", instantiates=True,
    )
    assert not stub.is_resolved
    assert (stub.edge_type, stub.is_resolved) == ("instantiates", False)
    (edge,) = _named_link([own, other, caller], stub, ImportScope(), caller.path)
    assert (edge.dst, edge.edge_type, edge.is_resolved) == (own.id, "instantiates", True)
    assert edge.meta["resolved_by"] == "same_module"
    imported = ImportScope()
    imported.add_named("legacy", "Conn")
    (edge,) = _named_link([own, other, caller], stub, imported, caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (other.id, "import")
    qualified = make_unresolved_edge(
        "java", caller.id, "legacy.Conn", 12, "test-pass", "test-run", instantiates=True,
    )
    (edge,) = _named_link([own, other, caller], qualified, ImportScope(), caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (other.id, "qualified")
    outsider = Symbol(
        id="java:web/Main.java:10-20:Main.run:method", name="Main.run", kind="method",
        language="java", path="web/Main.java", qualified_name="web.Main.run",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    unseen = make_unresolved_edge(
        "java", outsider.id, "Conn", 12, "test-pass", "test-run", instantiates=True,
    )
    assert _named_link([own, other, outsider], unseen, ImportScope(), outsider.path) == []


def test_a_bare_call_inside_a_class_falls_to_the_table_after_its_own_members() -> None:
    """Kotlin ``helper()`` inside ``class Main``: a member of Main or of its
    ancestors first (the stub carries ``enclosing_class``); when the class
    has none, the top-level function of the caller's package. And a second
    call of the same function from the same caller makes no second edge."""
    main = _decl("class", "app", "Main", "app/Main.kt", lines=(1, 40))
    helper = _decl("function", "app", "helper", "app/Util.kt")
    caller = Symbol(
        id="kotlin:app/Main.kt:10-20:Main.run:method", name="Main.run", kind="method",
        language="kotlin", path="app/Main.kt", qualified_name="app.Main.run",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    first = make_unresolved_edge("kotlin", caller.id, "helper", 12, "test-pass", "test-run",
                                 enclosing_class="Main")
    second = make_unresolved_edge("kotlin", caller.id, "helper", 15, "test-pass", "test-run",
                                  enclosing_class="Main")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[main, helper, caller], edges=[first, second],
                        import_scopes={caller.path: ImportScope()})
    (edge,) = link_inherited_calls(ctx).edges
    assert (edge.dst, edge.meta["resolved_by"]) == (helper.id, "same_module")
    assert edge.derived_from[0] == first.id


def test_kotlin_has_a_hierarchy_walker() -> None:
    """The Kotlin analyzer withholds a bare call that is not its own class's
    member and stamps ``enclosing_class`` for this linker to walk; without a
    walker registered for Kotlin nothing walked it. A bare ``save()`` in
    ``Child : Base`` reaches ``Base.save``: the superclass first, then the
    interfaces, as for Java."""
    base = _decl("class", "app", "Base", "app/Base.kt", lines=(1, 9))
    save = Symbol(
        id="kotlin:app/Base.kt:3-5:Base.save:method", name="Base.save", kind="method",
        language="kotlin", path="app/Base.kt", qualified_name="app.Base.save",
        span=Span(start_line=3, end_line=5, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    child = _decl("class", "app", "Child", "app/Child.kt", lines=(1, 30))
    caller = Symbol(
        id="kotlin:app/Child.kt:10-20:Child.run:method", name="Child.run", kind="method",
        language="kotlin", path="app/Child.kt", qualified_name="app.Child.run",
        span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
        origin="test", origin_run_id="test-run",
    )
    extends = Edge.create(
        src=child.id, dst=base.id, edge_type="extends", line=1, origin="test",
        origin_run_id="test-run", evidence_type="ast_inheritance",
    )
    stub = make_unresolved_edge("kotlin", caller.id, "save", 12, "test-pass", "test-run",
                                enclosing_class="Child")
    ctx = LinkerContext(repo_root=Path("/"), symbols=[base, save, child, caller], edges=[extends, stub])
    (edge,) = link_inherited_calls(ctx).edges
    assert (edge.dst, edge.meta["resolved_by"]) == (save.id, "own_type")


# --- found on the Java, Kotlin and PHP adapters ---------------------------------------


def _java(kind: str, package: str, name: str, path: str, lines: tuple[int, int] = (1, 9)) -> Symbol:
    return _decl(kind, package, name, path, language="java", lines=lines)


def test_a_nested_type_named_with_its_owner_is_found_by_its_last_name() -> None:
    """Java names a nested type ``Outer.Inner``. A file writes it
    ``Outer.Inner`` from outside and ``Inner`` inside ``Outer``; both mean
    it, and a ``Builder`` of another class does not."""
    outer = _java("class", "db", "Config", "db/Config.java", lines=(1, 40))
    builder = _java("class", "db", "Config.Builder", "db/Config.java", lines=(5, 20))
    build = _java("method", "db", "Config.Builder.build", "db/Config.java", lines=(8, 9))
    other = _java("class", "web", "Request.Builder", "web/Request.java", lines=(5, 20))
    other_build = _java("method", "web", "Request.Builder.build", "web/Request.java", lines=(8, 9))
    symbols = [outer, builder, build, other, other_build]

    outside = _java("method", "db", "Main.run", "db/Main.java", lines=(3, 9))
    new = make_unresolved_edge("java", outside.id, "Config.Builder", 4, "test-pass", "test-run", instantiates=True)
    (edge,) = _named_link([*symbols, outside], new, ImportScope(own_namespace="db"), outside.path)
    assert (edge.dst, edge.edge_type, edge.meta["resolved_by"]) == (builder.id, "instantiates", "qualified")
    typed = make_unresolved_edge(
        "java", outside.id, "build", 5, "test-pass", "test-run",
        receiver_type_hint="Config.Builder", call_construct="method",
    )
    (edge,) = _named_link([*symbols, outside], typed, ImportScope(own_namespace="db"), outside.path)
    assert edge.dst == build.id

    inside = _java("method", "db", "Config.fresh", "db/Config.java", lines=(30, 35))
    new = make_unresolved_edge("java", inside.id, "Builder", 31, "test-pass", "test-run", instantiates=True)
    (edge,) = _named_link([*symbols, inside], new, ImportScope(own_namespace="db"), inside.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (builder.id, "same_module")


def test_a_bare_call_reaches_a_method_the_file_imports() -> None:
    """``import static util.Text.pad;`` then ``pad(s)``: the method the import
    names. A wildcard over the type supplies it too. Without the import a
    method of that name is not a function in scope."""
    pad = _java("method", "util", "Text.pad", "util/Text.java", lines=(3, 5))
    namesake = _java("method", "ui", "Box.pad", "ui/Box.java", lines=(3, 5))
    caller = _java("method", "app", "Main.run", "app/Main.java", lines=(3, 9))
    stub = make_unresolved_edge("java", caller.id, "pad", 4, "test-pass", "test-run")
    symbols = [pad, namesake, caller]
    assert _named_link(symbols, stub, ImportScope(own_namespace="app"), caller.path) == []
    named = ImportScope(own_namespace="app")
    named.add_named(module="util.Text", name="pad")
    (edge,) = _named_link(symbols, stub, named, caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (pad.id, "import")
    star = ImportScope(own_namespace="app")
    star.add_open_wildcard("util.Text")
    (edge,) = _named_link(symbols, stub, star, caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (pad.id, "import")


def test_a_method_of_an_enclosing_type_is_not_bound_as_an_import() -> None:
    """The bare-call lookup takes a method only through an import: the type
    the caller is declared in, and its ancestors, are the hierarchy walk's."""
    own = _java("method", "app", "Main.pad", "app/Main.java", lines=(12, 14))
    caller = _java("method", "app", "Main.run", "app/Main.java", lines=(3, 9))
    stub = make_unresolved_edge("java", caller.id, "pad", 4, "test-pass", "test-run")
    assert _named_link([own, caller], stub, ImportScope(own_namespace="app"), caller.path) == []


def test_a_call_in_a_class_body_starts_at_that_class() -> None:
    """A property initialiser's caller is the class itself: ``helper()``
    written there is the class's own or an ancestor's, and ``Nested()`` its
    nested type."""
    base = _decl("class", "app", "Base", "app/Base.kt", lines=(1, 9))
    helper = _decl("method", "app", "Base.helper", "app/Base.kt", lines=(2, 3))
    owner = _decl("class", "app", "Widget", "app/Widget.kt", lines=(1, 30))
    nested = _decl("class", "app", "Widget.Nested", "app/Widget.kt", lines=(20, 25))
    extends = Edge.create(
        src=owner.id, dst=base.id, edge_type="extends", line=1, origin="test", origin_run_id="test-run",
    )
    # A namesake class elsewhere: the caller's own qualified name, not the
    # name ``Widget``, says which class the walk starts at.
    namesake = _decl("class", "other", "Widget", "other/Widget.kt", lines=(1, 30))
    its_base = _decl("class", "other", "Frame", "other/Frame.kt", lines=(1, 9))
    its_helper = _decl("method", "other", "Frame.helper", "other/Frame.kt", lines=(2, 3))
    also_extends = Edge.create(
        src=namesake.id, dst=its_base.id, edge_type="extends", line=1, origin="test",
        origin_run_id="test-run",
    )
    symbols = [namesake, its_base, its_helper, base, helper, owner, nested]
    call = make_unresolved_edge(
        "kotlin", owner.id, "helper", 4, "test-pass", "test-run", enclosing_class="Widget",
    )
    ctx = LinkerContext(
        repo_root=Path("/"), symbols=symbols, edges=[also_extends, extends, call],
        import_scopes={owner.path: ImportScope(own_namespace="app")},
    )
    (edge,) = link_inherited_calls(ctx).edges
    assert edge.dst == helper.id
    construct = make_unresolved_edge(
        "kotlin", owner.id, "Nested", 5, "test-pass", "test-run", enclosing_class="Widget",
    )
    (edge,) = _named_link(symbols, construct, ImportScope(own_namespace="app"), owner.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (nested.id, "same_module")


def test_an_imported_member_that_holds_a_callable_is_bound_when_no_method_is() -> None:
    """Kotlin ``import cfg.Defaults.retry`` then ``retry()``: the object's
    method of that name, or failing one its property (a function-typed value)."""
    prop = _decl("field", "cfg", "Defaults.retry", "cfg/Defaults.kt", lines=(3, 3))
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))
    stub = make_unresolved_edge("kotlin", caller.id, "retry", 12, "test-pass", "test-run")
    table = ImportScope(own_namespace="app")
    table.add_named(module="cfg.Defaults", name="retry")
    (edge,) = _named_link([prop, caller], stub, table, caller.path)
    assert (edge.dst, edge.meta["resolved_by"]) == (prop.id, "import")
    method = _decl("method", "cfg", "Defaults.retry", "cfg/Defaults.kt", lines=(5, 7))
    (edge,) = _named_link([prop, method, caller], stub, table, caller.path)
    assert edge.dst == method.id


# --- overloads: the argument count the call site writes (WI-tosut) --------------------


def _with_parameters(symbol: Symbol, *names: str, defaults: int = 0, variadic: bool = False) -> Symbol:
    """``symbol`` declaring the parameters ``names``, the last ``defaults`` of
    them with a default value; ``variadic`` adds a parameter taking any number."""
    from hypergumbo_core.overload_arity import parameter_entry

    entries = [
        parameter_entry(name, default=index >= len(names) - defaults)
        for index, name in enumerate(names)
    ]
    if variadic:
        entries.append(parameter_entry("rest", variadic=True))
    symbol.meta = {"parameters": entries}
    return symbol


def _typed_call(caller: Symbol, callee: str, hint: str, argument_count: int | None) -> Edge:
    return make_unresolved_edge(
        "kotlin", caller.id, callee, 12, "test-pass", "test-run",
        receiver_type_hint=hint, call_construct="method", argument_count=argument_count,
    )


def test_the_overload_that_takes_the_arguments_written_is_the_one_bound() -> None:
    """``server.takeRequest()`` is the overload with no parameter, not the one
    declared after it that takes two; with a default, ``start()`` is
    ``start(port = 0)``. A call that does not say how many arguments it
    writes lands where it did before: on the last overload."""
    server = _decl("class", "web", "Server", "web/Server.kt", lines=(1, 40))
    plain = _with_parameters(_decl("method", "web", "Server.takeRequest", "web/Server.kt", lines=(5, 6)))
    timed = _with_parameters(
        _decl("method", "web", "Server.takeRequest", "web/Server.kt", lines=(8, 9)), "timeout", "unit")
    start = _with_parameters(
        _decl("method", "web", "Server.start", "web/Server.kt", lines=(11, 12)), "port", defaults=1)
    bind = _with_parameters(
        _decl("method", "web", "Server.start", "web/Server.kt", lines=(14, 15)), "address", "backlog")
    caller = _decl("function", "web", "run", "web/Main.kt", lines=(10, 20))
    symbols = [server, plain, timed, start, bind, caller]

    def target(callee: str, argument_count: int | None) -> str | None:
        edges = _named_link(symbols, _typed_call(caller, callee, "Server", argument_count),
                            ImportScope(own_namespace="web"), caller.path)
        return edges[0].dst if edges else None

    assert target("takeRequest", 0) == plain.id
    assert target("takeRequest", 2) == timed.id
    assert target("takeRequest", None) == timed.id
    assert target("takeRequest", 5) is None
    assert target("start", 0) == start.id
    assert target("start", 1) == start.id
    assert target("start", 2) == bind.id


def test_an_overload_the_type_inherits_is_a_candidate_beside_its_own() -> None:
    """The subclass declares ``send(a)``; ``send(a, b)`` is the parent's. A
    call with two arguments on the subclass is the parent's overload. A
    method that records no parameters is of unknown arity and is not left out."""
    base = _decl("class", "net", "Channel", "net/Channel.kt", lines=(1, 20))
    wide = _with_parameters(_decl("method", "net", "Channel.send", "net/Channel.kt", lines=(3, 4)), "a", "b")
    child = _decl("class", "net", "Tls", "net/Tls.kt", lines=(1, 20))
    narrow = _with_parameters(_decl("method", "net", "Tls.send", "net/Tls.kt", lines=(3, 4)), "a")
    extends = Edge.create(
        src=child.id, dst=base.id, edge_type="extends", line=1, origin="test", origin_run_id="test-run",
    )
    caller = _decl("function", "net", "run", "net/Main.kt", lines=(10, 20))
    symbols = [base, wide, child, narrow, caller]

    def target(argument_count: int | None) -> str | None:
        ctx = LinkerContext(
            repo_root=Path("/"), symbols=symbols,
            edges=[extends, _typed_call(caller, "send", "Tls", argument_count)],
            import_scopes={caller.path: ImportScope(own_namespace="net")},
        )
        edges = link_inherited_calls(ctx).edges
        return edges[0].dst if edges else None

    assert target(1) == narrow.id
    assert target(2) == wide.id
    narrow.meta = None
    assert target(2) == narrow.id


def test_a_bare_call_takes_the_function_overload_that_fits() -> None:
    one = _with_parameters(_decl("function", "app", "helper", "app/Util.kt", lines=(1, 3)), "a")
    two = _with_parameters(_decl("function", "app", "helper", "app/Util.kt", lines=(5, 7)), "a", "b")
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))

    def target(argument_count: int | None) -> str | None:
        stub = make_unresolved_edge(
            "kotlin", caller.id, "helper", 12, "test-pass", "test-run", argument_count=argument_count,
        )
        edges = _named_link([one, two, caller], stub, ImportScope(own_namespace="app"), caller.path)
        return edges[0].dst if edges else None

    assert target(2) == two.id
    assert target(1) == one.id
    assert target(None) == one.id
    assert target(3) is None


def test_an_overload_whose_parameters_all_get_arguments_beats_a_wider_one() -> None:
    """``log(format)`` and, declared after it, ``log(format, vararg args)`` and
    ``log(format, level = 0)``: a call with one argument is the first, by the
    rule the three languages share (``overload_arity.best_by_arity``); with
    two it is the one with the default, before the variadic one."""
    logger = _decl("class", "app", "Logger", "app/Logger.kt", lines=(1, 40))
    exact = _with_parameters(_decl("method", "app", "Logger.log", "app/Logger.kt", lines=(3, 4)), "format")
    variadic = _with_parameters(
        _decl("method", "app", "Logger.log", "app/Logger.kt", lines=(6, 7)), "format", variadic=True)
    defaulted = _with_parameters(
        _decl("method", "app", "Logger.log", "app/Logger.kt", lines=(9, 10)), "format", "level", defaults=1)
    caller = _decl("function", "app", "run", "app/Main.kt", lines=(10, 20))
    symbols = [logger, exact, defaulted, variadic, caller]

    def target(argument_count: int) -> str:
        (edge,) = _named_link(symbols, _typed_call(caller, "log", "Logger", argument_count),
                              ImportScope(own_namespace="app"), caller.path)
        return edge.dst

    assert target(1) == exact.id
    assert target(2) == defaulted.id
    assert target(3) == variadic.id


def test_a_call_to_its_own_name_from_an_overload_goes_to_the_sibling_that_fits_as_well() -> None:
    """Two overloads take two arguments equally well, and the call is written
    in one of them (``this.send(a, b)``): the other is the callee."""
    owner = _decl("class", "net", "Channel", "net/Channel.kt", lines=(1, 40))
    first = _with_parameters(_decl("method", "net", "Channel.send", "net/Channel.kt", lines=(3, 6)), "a", "b")
    second = _with_parameters(_decl("method", "net", "Channel.send", "net/Channel.kt", lines=(8, 12)), "x", "y")
    symbols = [owner, first, second]
    for caller, other in ((second, first), (first, second)):
        stub = make_unresolved_edge(
            "kotlin", caller.id, "send", caller.span.start_line + 1, "test-pass", "test-run",
            enclosing_class="Channel", call_construct="method", argument_count=2,
        )
        (edge,) = _named_link(symbols, stub, ImportScope(own_namespace="net"), caller.path)
        assert edge.dst == other.id
