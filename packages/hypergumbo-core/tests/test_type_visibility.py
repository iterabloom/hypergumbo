# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which type a name means from one place in one file (``type_visibility``, ADR-0064)."""
# covers: packages/hypergumbo-core/src/hypergumbo_core/type_visibility.py
from __future__ import annotations

from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core import type_visibility
from hypergumbo_core.type_visibility import dotted, without_type_arguments


def _type(qualified_name: str | None, name: str = "", path: str = "a.cs") -> Symbol:
    short = name or (qualified_name or "").rpartition(".")[2]
    return Symbol(
        id=f"csharp:{path}:1-2:{qualified_name or short}:class", name=short, kind="class",
        language="csharp", path=path, span=Span(1, 2, 0, 0), qualified_name=qualified_name,
    )


def _scope(*usings: str, aliases: dict[str, tuple[str, str]] | None = None) -> ImportScope:
    scope = ImportScope()
    for namespace in usings:
        scope.add_open_wildcard(namespace)
    for local, (module, name) in (aliases or {}).items():
        scope.add_named(module, name, alias=local)
    return scope


def visible_types(written: str, caller: str | None, scope: ImportScope,
                  candidates: list[Symbol], caller_path: str | None = None) -> list[Symbol] | None:
    """The function under test, with its candidate lookup made from a list."""
    def named(short: str) -> list[Symbol]:
        return [c for c in candidates if c.name == short]
    return type_visibility.visible_types(written, caller, scope, named, caller_path=caller_path)


def _names(found: list[Symbol] | None) -> list[str] | None:
    return None if found is None else sorted(s.qualified_name or "" for s in found)


def test_the_callers_own_namespace_answers_before_a_using() -> None:
    """``namespace App { ... new Conn() }`` with ``using Other;`` and a Conn in
    both: C# takes App.Conn, and the search stops there."""
    conns = [_type("App.Conn"), _type("Other.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Other"), conns)) == ["App.Conn"]


def test_the_innermost_enclosing_scope_answers_first() -> None:
    conns = [_type("App.Conn"), _type("App.Web.Conn"), _type("Conn")]
    assert _names(visible_types("Conn", "App.Web.Handler.Get", _scope(), conns)) == ["App.Web.Conn"]
    assert _names(visible_types("Conn", "App.Jobs.Worker.Run", _scope(), conns)) == ["App.Conn"]
    assert _names(visible_types("Conn", "Tool.Run", _scope(), conns)) == ["Conn"]


def test_a_type_nested_in_the_callers_type_is_in_scope() -> None:
    inner = [_type("App.Outer.Inner"), _type("Lib.Inner")]
    assert _names(visible_types("Inner", "App.Outer.Go", _scope("Lib"), inner)) == ["App.Outer.Inner"]


def test_a_using_supplies_the_name_when_no_enclosing_scope_declares_it() -> None:
    conns = [_type("Db.Conn"), _type("Net.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Db"), conns)) == ["Db.Conn"]


def test_two_usings_that_both_supply_the_name_are_a_tie() -> None:
    conns = [_type("Db.Conn"), _type("Net.Conn")]
    assert _names(visible_types("Conn", "App.Main.Run", _scope("Db", "Net"), conns)) == [
        "Db.Conn", "Net.Conn",
    ]


def test_a_name_no_visible_scope_declares_means_nothing_here() -> None:
    """``Exception`` with ``using System;`` and an in-repository
    ``Tool.Errors.Exception`` the file does not import: not that one."""
    elsewhere = [_type("Tool.Errors.Exception")]
    assert visible_types("Exception", "App.Main.Run", _scope("System"), elsewhere) == []
    assert visible_types("Exception", "App.Main.Run", _scope(), []) == []


def test_an_alias_is_what_it_names_and_nothing_else() -> None:
    types = [_type("A.B"), _type("App.Svc"), _type("A.B.T")]
    scope = _scope(aliases={"Svc": ("A", "B")})
    # The alias wins over a type of the caller's own namespace with that name.
    assert _names(visible_types("Svc", "App.Main.Run", scope, types)) == ["A.B"]
    # An alias of a namespace: ``Svc.T`` is ``A.B.T``.
    assert _names(visible_types("Svc.T", "App.Main.Run", scope, types)) == ["A.B.T"]
    assert visible_types("Svc.Missing", "App.Main.Run", scope, types) == []


def test_a_qualified_name_is_resolved_from_the_enclosing_scopes_outward() -> None:
    helpers = [_type("App.Util.Helper"), _type("Other.Util.Helper")]
    assert _names(visible_types("Util.Helper", "App.Web.Handler.Get", _scope(), helpers)) == [
        "App.Util.Helper",
    ]
    assert _names(visible_types("Other.Util.Helper", "App.Web.Handler.Get", _scope(), helpers)) == [
        "Other.Util.Helper",
    ]
    assert _names(visible_types("Util.Helper", "App.Main.Run", _scope("Other"), helpers)) == [
        "App.Util.Helper",
    ]


def test_type_arguments_and_the_global_prefix_are_not_part_of_the_name() -> None:
    boxes = [_type("App.Box"), _type("Box")]
    assert _names(visible_types("Box<int>", "App.Main.Run", _scope(), boxes)) == ["App.Box"]
    assert _names(visible_types("global::Box", "App.Main.Run", _scope(), boxes)) == ["Box"]


def test_a_candidate_with_no_qualified_name_cannot_be_judged() -> None:
    assert visible_types("Conn", "App.Main.Run", _scope(), [_type("App.Conn"), _type(None, "Conn")]) is None


def test_a_caller_with_no_qualified_name_sees_the_global_namespace_and_the_usings() -> None:
    conns = [_type("Conn"), _type("Db.Conn")]
    assert _names(visible_types("Conn", None, _scope("Db"), conns)) == ["Conn"]
    assert _names(visible_types("Conn", None, _scope("Db"), conns[1:])) == ["Db.Conn"]


def test_separators_are_one() -> None:
    assert dotted("a::b::C") == dotted("\\a\\b\\C") == "a.b.C"


def test_the_type_arguments_written_are_counted_at_the_top_level() -> None:
    from hypergumbo_core.type_visibility import written_arity

    assert written_arity("Policy") == 0
    assert written_arity("Policy<int>") == 1
    assert written_arity("Map<string, List<int>>") == 2
    assert written_arity("A.B.Triple<X, Func<Y, Z>, W>") == 3


# --- languages whose imports name files -------------------------------------------


def _file_type(name: str, path: str) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:1-2:{name}:class", name=name, kind="class",
        language="typescript", path=path, span=Span(1, 2, 0, 0),
    )


def _file_scope() -> ImportScope:
    from hypergumbo_core.import_scope import MODULES_ARE_FILES

    return ImportScope(module_kind=MODULES_ARE_FILES)


def _paths(found: list[Symbol] | None) -> list[str] | None:
    return None if found is None else sorted(s.path for s in found)


def test_a_declaration_of_the_callers_own_file_answers_first() -> None:
    conns = [_file_type("Conn", "src/main.ts"), _file_type("Conn", "src/db.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn")
    assert _paths(visible_types("Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/main.ts"]


def test_an_imported_name_is_the_declaration_in_the_file_the_import_names() -> None:
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Conn", "legacy/db.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn")
    assert _paths(visible_types("Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]


def test_an_import_alias_is_looked_up_by_the_original_name() -> None:
    """``import {Conn as C} from './db'``: no class is called ``C``."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("C", "other/c.ts")]
    scope = _file_scope()
    scope.add_named("src/db.ts", "Conn", alias="C")
    assert _paths(visible_types("C", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]


def test_a_name_reached_through_a_namespace_binding() -> None:
    """``import * as db from './db'`` then ``db.Conn``; Rust ``use crate::db;``
    then ``db::Conn``."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Conn", "legacy/db.ts")]
    scope = _file_scope()
    scope.add_namespace("src/db.ts", "db")
    assert _paths(visible_types("db.Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]
    assert _paths(visible_types("db::Conn", None, scope, conns, caller_path="src/main.ts")) == ["src/db.ts"]
    # An unknown qualifier, and a path deeper than one member, name nothing here.
    assert visible_types("other.Conn", None, scope, conns, caller_path="src/main.ts") == []
    assert visible_types("db.inner.Conn", None, scope, conns, caller_path="src/main.ts") == []
    # A binding keyed by a path of several segments, as the file writes it.
    scope.add_namespace("legacy/db.ts", "crate.legacy.db")
    assert _paths(visible_types("crate::legacy::db::Conn", None, scope, conns, caller_path="src/main.ts")) == ["legacy/db.ts"]


def test_a_name_no_import_binds_is_not_a_type_of_the_repository_here() -> None:
    """A global, a library type, or a name re-exported through a file that
    does not declare it: nothing, however many namesakes exist."""
    conns = [_file_type("Conn", "legacy/db.ts"), _file_type("Conn", "src/db.ts")]
    assert visible_types("Conn", None, _file_scope(), conns, caller_path="src/main.ts") == []
    reexport = _file_scope()
    reexport.add_named("src/index.ts", "Conn")
    assert visible_types("Conn", None, reexport, conns, caller_path="src/main.ts") == []


def test_a_wildcard_import_of_a_file_supplies_its_declarations() -> None:
    """Rust ``use crate::db::*;``: every item of that file is in scope. Two
    wildcard files that both declare the name are a tie; an explicit binding
    and the caller's own file still answer first."""
    conns = [_file_type("Conn", "src/db.rs"), _file_type("Conn", "src/net.rs"), _file_type("Conn", "src/old.rs")]
    one = _file_scope()
    one.add_open_wildcard("src/db.rs")
    assert _paths(visible_types("Conn", None, one, conns, caller_path="src/main.rs")) == ["src/db.rs"]
    two = _file_scope()
    two.add_open_wildcard("src/db.rs")
    two.add_open_wildcard("src/net.rs")
    assert _paths(visible_types("Conn", None, two, conns, caller_path="src/main.rs")) == ["src/db.rs", "src/net.rs"]
    two.add_named("src/old.rs", "Conn")
    assert _paths(visible_types("Conn", None, two, conns, caller_path="src/main.rs")) == ["src/old.rs"]


def test_a_written_type_without_its_type_arguments() -> None:
    assert without_type_arguments("Policy") == "Policy"
    assert without_type_arguments("Policy<int>") == "Policy"
    assert without_type_arguments("App.Map<string, List<int>> ") == "App.Map"


# --- a name that reaches the importer through a file that only passes it on --------


def _through(scopes: dict[str, ImportScope], written: str, caller_scope: ImportScope,
             candidates: list[Symbol]) -> list[str] | None:
    def named(short: str) -> list[Symbol]:
        return [c for c in candidates if c.name == short]
    return _paths(type_visibility.visible_types(
        written, None, caller_scope, named, caller_path="src/main.ts", scopes=scopes,
    ))


def test_a_reexported_name_is_followed_to_the_file_that_declares_it() -> None:
    """``import {Conn} from './index'``; index.ts: ``export {Conn} from './db'``."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Conn", "legacy/db.ts")]
    caller = _file_scope()
    caller.add_named("src/index.ts", "Conn")
    barrel = _file_scope()
    barrel.add_reexport("src/db.ts", "Conn")
    assert _through({"src/index.ts": barrel}, "Conn", caller, conns) == ["src/db.ts"]
    # Without the tables, or when the barrel handed none over: not found.
    assert _through({}, "Conn", caller, conns) == []


def test_a_reexport_under_another_name_and_an_import_that_is_exported() -> None:
    """``export {Conn as Db} from './db'``; and ``import {Conn} from './db';
    export {Conn}`` (Rust ``pub use db::Conn;``), which needs no record of its
    own: the barrel's binding says where the name comes from."""
    conns = [_file_type("Conn", "src/db.ts"), _file_type("Db", "legacy/other.ts")]
    caller = _file_scope()
    caller.add_named("src/index.ts", "Db")
    renamed = _file_scope()
    renamed.add_reexport("src/db.ts", "Conn", exported_as="Db")
    assert _through({"src/index.ts": renamed}, "Db", caller, conns) == ["src/db.ts"]
    caller = _file_scope()
    caller.add_named("src/index.ts", "Conn")
    imported = _file_scope()
    imported.add_named("src/db.ts", "Conn")
    assert _through({"src/index.ts": imported}, "Conn", caller, conns) == ["src/db.ts"]


def test_names_passed_on_wholesale_and_through_several_files() -> None:
    """``export * from './db'`` / ``pub use db::*;``, two files deep; a
    namespace binding and a wildcard import of the barrel follow the same way."""
    conns = [_file_type("Conn", "src/db/conn.ts"), _file_type("Conn", "legacy/db.ts")]
    outer, inner = _file_scope(), _file_scope()
    outer.add_reexport_all("src/db/index.ts")
    inner.add_open_wildcard("src/db/conn.ts")
    scopes = {"src/index.ts": outer, "src/db/index.ts": inner}
    caller = _file_scope()
    caller.add_named("src/index.ts", "Conn")
    assert _through(scopes, "Conn", caller, conns) == ["src/db/conn.ts"]
    caller = _file_scope()
    caller.add_namespace("src/index.ts", "api")
    assert _through(scopes, "api.Conn", caller, conns) == ["src/db/conn.ts"]
    caller = _file_scope()
    caller.add_open_wildcard("src/index.ts")
    assert _through(scopes, "Conn", caller, conns) == ["src/db/conn.ts"]


def test_two_files_passing_the_name_on_are_a_tie_and_a_cycle_ends() -> None:
    conns = [_file_type("Conn", "src/a.ts"), _file_type("Conn", "src/b.ts")]
    barrel = _file_scope()
    barrel.add_reexport_all("src/a.ts")
    barrel.add_reexport_all("src/b.ts")
    caller = _file_scope()
    caller.add_named("src/index.ts", "Conn")
    assert _through({"src/index.ts": barrel}, "Conn", caller, conns) == ["src/a.ts", "src/b.ts"]
    one, two = _file_scope(), _file_scope()
    one.add_reexport_all("src/two.ts")
    two.add_reexport_all("src/one.ts")
    caller = _file_scope()
    caller.add_named("src/one.ts", "Missing")
    assert _through({"src/one.ts": one, "src/two.ts": two}, "Missing", caller, conns) == []


def test_a_chain_longer_than_anyone_writes_is_given_up() -> None:
    conns = [_file_type("Conn", "src/f10.ts")]
    scopes = {}
    for i in range(10):
        scope = _file_scope()
        scope.add_reexport(f"src/f{i + 1}.ts", "Conn")
        scopes[f"src/f{i}.ts"] = scope
    caller = _file_scope()
    caller.add_named("src/f0.ts", "Conn")
    assert _through(scopes, "Conn", caller, conns) == []
    caller = _file_scope()
    caller.add_named("src/f5.ts", "Conn")
    assert _through(scopes, "Conn", caller, conns) == ["src/f10.ts"]


def test_a_file_of_a_language_of_declared_names_passes_nothing_on() -> None:
    """Only a table of files is followed: a C# table's modules are namespaces."""
    conns = [_file_type("Conn", "src/db.ts")]
    caller = _file_scope()
    caller.add_named("src/index.ts", "Conn")
    other = ImportScope()
    other.add_named("src/db.ts", "Conn")
    assert _through({"src/index.ts": other}, "Conn", caller, conns) == []


# --- which rule answered (the mark of a bind made through the lookup) --------------


def _how(written: str, caller: str | None, scope: ImportScope, candidates: list[Symbol],
         caller_path: str | None = None) -> tuple[list[str] | None, str | None]:
    def named(short: str) -> list[Symbol]:
        return [c for c in candidates if c.name == short]
    found, by = type_visibility.visible_declarations(
        written, caller, scope, named, caller_path=caller_path,
    )
    return (None if found is None else sorted(dotted(s.qualified_name or s.path) for s in found)), by


def test_the_lookup_says_which_rule_answered() -> None:
    """A bind made through the lookup is marked by what scoped it: the
    caller's own package (``same_module``), an import (``import``: an explicit
    binding, an alias, a wildcard), or the owner the call site wrote
    (``qualified``: a rooted or a dotted name). Nothing found: no rule."""
    conns = [_type("App.Db.Conn"), _type("Legacy.Conn"), _type("App.Web.Conn")]
    assert _how("Conn", "App.Web.Handler.Get", ImportScope(), conns) == (["App.Web.Conn"], "same_module")
    imported = ImportScope()
    imported.add_named("App.Db", "Conn")
    assert _how("Conn", "Other.Handler.Get", imported, conns) == (["App.Db.Conn"], "import")
    wildcard = ImportScope()
    wildcard.add_open_wildcard("Legacy")
    assert _how("Conn", "Other.Handler.Get", wildcard, conns) == (["Legacy.Conn"], "import")
    assert _how("Db.Conn", "App.Web.Handler.Get", ImportScope(), conns) == (["App.Db.Conn"], "qualified")
    assert _how("global::Legacy.Conn", "App.Web.Handler.Get", ImportScope(), conns) == (["Legacy.Conn"], "qualified")
    assert _how("Missing", "App.Web.Handler.Get", ImportScope(), conns) == ([], None)


def test_the_file_rule_says_which_rule_answered() -> None:
    conns = [_file_type("Conn", "src/main.ts"), _file_type("Conn", "src/db.ts")]
    assert _how("Conn", None, _file_scope(), conns, caller_path="src/main.ts") == (["src/main.ts"], "same_file")
    bound = _file_scope()
    bound.add_named("src/db.ts", "Conn")
    assert _how("Conn", None, bound, conns, caller_path="src/other.ts") == (["src/db.ts"], "import")
    ns = _file_scope()
    ns.add_namespace("src/db.ts", "db")
    assert _how("db.Conn", None, ns, conns, caller_path="src/other.ts") == (["src/db.ts"], "import")
    assert _how("Conn", None, _file_scope(), conns, caller_path="src/other.ts") == ([], None)


def test_a_php_name_with_a_leading_backslash_is_a_full_name() -> None:
    r"""``\Util\helper`` is rooted at the global namespace: the caller's own
    namespace does not reinterpret it, and an alias does not either."""
    helpers = [_type("App.Util.helper"), _type("Util.helper")]
    assert _how("\\Util\\helper", "App.Main.run", ImportScope(), helpers) == (["Util.helper"], "qualified")
    assert _how("Util\\helper", "App.Main.run", ImportScope(), helpers) == (["App.Util.helper"], "qualified")
    aliased = ImportScope()
    aliased.add_named(module="Other", name="Kit", alias="Util")
    assert _how("\\Util\\helper", "App.Main.run", aliased, helpers) == (["Util.helper"], "qualified")


# --- packages that do not nest (Java, Kotlin, PHP) -----------------------------------


def _package(namespace: str, **flags: bool) -> ImportScope:
    return ImportScope(own_namespace=namespace, **flags)


def test_a_parent_package_is_no_scope_where_packages_do_not_nest() -> None:
    """``Builder`` written in ``jedis.search`` is not ``jedis.Builder``: a
    parent package needs an import. Where namespaces nest (no package named
    by the table) it is."""
    builders = [_type("jedis.Builder")]
    assert _how("Builder", "jedis.search.Query.run", ImportScope(), builders) == (["jedis.Builder"], "same_module")
    assert _how("Builder", "jedis.search.Query.run", _package("jedis.search"), builders) == ([], None)
    imported = _package("jedis.search")
    imported.add_named(module="jedis", name="Builder")
    assert _how("Builder", "jedis.search.Query.run", imported, builders) == (["jedis.Builder"], "import")


def test_the_own_package_and_the_enclosing_types_are_scopes() -> None:
    helpers = [_type("a.b.Helper"), _type("a.b.Outer.Helper"), _type("a.Helper")]
    assert _how("Helper", "a.b.Main.run", _package("a.b"), helpers) == (["a.b.Helper"], "same_module")
    assert _how("Helper", "a.b.Outer.Inner.run", _package("a.b"), helpers) == (["a.b.Outer.Helper"], "same_module")


def test_a_caller_that_is_a_scope_sees_what_it_declares() -> None:
    """A call in a class body (a property initialiser) has the class for its
    caller: the class's nested types are in scope, in either kind of language."""
    nested = [_type("a.b.Outer.Nested"), _type("x.Nested")]
    for scope in (ImportScope(), _package("a.b")):
        assert _how("Nested", "a.b.Outer", scope, nested) == (["a.b.Outer.Nested"], "same_module")


def test_a_caller_with_no_qualified_name_is_in_the_files_package() -> None:
    """A call at the top of a file: the table's package is its scope."""
    helpers = [_type("a.b.helper"), _type("helper")]
    assert _how("helper", None, _package("a.b"), helpers) == (["a.b.helper"], "same_module")
    assert _how("helper", None, _package(""), helpers) == (["helper"], "same_module")
    assert _how("helper", None, ImportScope(), helpers) == (["helper"], "same_module")


def test_a_name_with_an_owner_may_be_a_full_name() -> None:
    """``x.y.Util`` from package ``a.b``: relative to the caller's scopes
    first, then the full name. A bare name is never the unnamed package's."""
    utils = [_type("x.y.Util"), _type("Util")]
    assert _how("x.y.Util", "a.b.Main.run", _package("a.b"), utils) == (["x.y.Util"], "qualified")
    assert _how("Util", "a.b.Main.run", _package("a.b"), utils) == ([], None)
    nearer = [*utils, _type("a.b.x.y.Util")]
    assert _how("x.y.Util", "a.b.Main.run", _package("a.b"), nearer) == (["a.b.x.y.Util"], "qualified")


def test_a_php_bare_function_falls_back_to_the_global_namespace() -> None:
    """PHP: ``helper()`` in ``App\\Http`` is ``App\\Http\\helper`` or the global
    ``helper``; a class is never looked for in the global namespace."""
    function = _type("helper")
    function.kind = "function"
    php = _package("App.Http", global_functions_visible=True)
    assert _how("helper", "App.Http.run", php, [function]) == (["helper"], "same_module")
    assert _how("Conn", "App.Http.run", php, [_type("Conn")]) == ([], None)
    assert _how("helper", "App.Http.run", _package("App.Http"), [function]) == ([], None)
    own = _type("App.Http.helper")
    own.kind = "function"
    assert _how("helper", "App.Http.run", php, [function, own]) == (["App.Http.helper"], "same_module")
