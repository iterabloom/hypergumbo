# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py
"""What the Go analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a BARE call where Go scoping places it (a declaration of
the caller's file, then of its package) and a function VALUE it can name
exactly (``reg(pkg.F)``, ``reg(x.M)``). Every call written ON something is
emitted unresolved with the facts the shared linkers read, and they decide
from the caller's file's import table (``AnalysisResult.import_scopes``, a
table of FILES whose modules are DIRECTORIES, ``pkg/log/``):

* ``qualifier``: the package as the file names it (``log.Print()``);
* ``receiver_type_hint``: the receiver's type as the caller's file writes it
  (a declaration, a parameter, a composite literal, a callee's declared
  return type, a struct field's declared type), or by its import path when
  the file does not import the type's package;
* ``stdlib_member_name`` on a method name the standard library's interfaces
  define.

Two things the shared resolution has no lookup for are bound here, by
identity: a method of a named type that is no struct or interface, and
``pkg.T(x)`` naming a type.

The first half pins what the analyzer emits, route by route. The second half
runs the whole pipeline for the cases the shared pass exists for, each with a
namesake in another directory that a bind by name would reach or tie with.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.import_scope import MODULES_ARE_FILES
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.go import _GoTree, _go_import_scope, analyze_go

_GO_MOD = "module example.com/m\n\ngo 1.22\n"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _analyze(tmp_path: Path, files: dict[str, str]) -> AnalysisResult:
    return analyze_go(_write(tmp_path, {"go.mod": _GO_MOD, **files}))


def _calls(result: AnalysisResult, caller: str) -> list[Edge]:
    """The ``calls`` edges the analyzer emits from the symbol named ``caller``."""
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _one(edges: list[Edge], callee: str) -> Edge:
    found = [e for e in edges if e.dst.split(":")[-2].split(".")[-1] == callee]
    assert len(found) == 1, (callee, [(e.dst, e.meta) for e in edges])
    return found[0]


_FACTS = ("receiver_type_hint", "qualifier", "stdlib_member_name", "call_construct")


def _facts(edge: Edge) -> dict[str, Any]:
    assert not edge.is_resolved, edge.dst
    meta = edge.meta or {}
    return {k: meta[k] for k in _FACTS if k in meta}


# --------------------------------------------------------------- the table

def test_every_file_hands_over_a_table_of_directories(tmp_path: Path) -> None:
    """A Go import names a directory: the module path stripped, joined to the
    go.mod's directory, written relative to the root with a trailing slash.
    The binding's name is the directory's ``package`` clause, or the alias
    as written; a dot import is an open wildcard; a blank import, a library
    and a directory with no Go file bind nothing. The caller's own directory
    is its ``own_namespace``; the root is ``/``."""
    result = _analyze(tmp_path, {
        "root.go": "package m\n\nfunc Root() {}\n",
        "pkg/log/log.go": "package logging\n\nfunc Print() {}\n",
        "pkg/db/db.go": "package db\n\ntype Store struct{}\n",
        "app/app.go": (
            "package app\n\n"
            "import (\n"
            '\t"fmt"\n'
            '\t"example.com/m"\n'
            '\t"example.com/m/pkg/log"\n'
            '\tstore "example.com/m/pkg/db"\n'
            '\t. "example.com/m/pkg/db"\n'
            '\t_ "example.com/m/pkg/log"\n'
            '\t"example.com/m/nothing"\n'
            ")\n\n"
            "func Use() { fmt.Println() }\n"
        ),
    })
    scope = result.import_scopes[str(tmp_path / "app" / "app.go")]
    assert scope.module_kind == MODULES_ARE_FILES
    assert scope.own_namespace == "app/"
    assert scope.library_names_screened is False
    assert scope.namespace("m") == "/"
    assert scope.namespace("logging") == "pkg/log/"
    assert scope.namespace("log") is None
    assert scope.namespace("store") == "pkg/db/"
    assert scope.wildcard_modules == ("pkg/db/",)
    assert scope.namespace("fmt") is None
    assert scope.namespace("nothing") is None
    assert result.import_scopes[str(tmp_path / "root.go")].own_namespace == "/"
    assert set(result.import_scopes) == {
        str(tmp_path / p) for p in ("root.go", "pkg/log/log.go", "pkg/db/db.go", "app/app.go")
    }


def test_what_the_table_leaves_out_is_counted_by_reason(tmp_path: Path) -> None:
    """A directory whose non-test files disagree on their package clause binds
    nothing; with no go.mod no import names the tree."""
    tree = _GoTree(module_path="example.com/m", module_dir="/r", root="/r",
                   clauses={"/r/two": {"a", "b"}, "/r/one": {"one"}})
    counts: dict[str, int] = {}
    scope = _go_import_scope("/r/x/x.go", [
        ("example.com/m/two", None), ("example.com/m/one", None), ("fmt", None),
        ("example.com/m/one", "_"), ("example.com/m/none", None),
    ], tree, counts)
    assert scope.namespace("one") == "one/"
    assert counts == {
        "several package clauses": 1, "library": 1, "blank": 1,
        "names no package of the analysis": 1,
    }
    counts = {}
    _go_import_scope("/r/x/x.go", [("example.com/m/one", None)], _GoTree(root="/r"), counts)
    assert counts == {"no go.mod": 1}


def test_an_empty_import_path_names_nothing(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"a/a.go": 'package a\n\nimport ""\n\nfunc A() {}\n'})
    assert result.import_scopes[str(tmp_path / "a" / "a.go")].wildcard_modules == ()
    assert [e for e in result.edges if e.edge_type == "imports"] == []


# --------------------------------------------------------------- the routes

_ROUTES = {
    "pkg/db/db.go": (
        "package db\n\n"
        "type Store struct{ Conn *Conn }\n\n"
        "type Conn struct{}\n\n"
        "func Open() *Store { return &Store{} }\n\n"
        "func (s *Store) Put() {}\n\n"
        "func (c *Conn) Close() {}\n\n"
        "type Level uint32\n\n"
        "func (l Level) Name() string { return \"\" }\n\n"
        "type Duration int64\n"
    ),
    "pkg/inner/inner.go": "package inner\n\ntype Deep struct{}\n\nfunc (d *Deep) Go() {}\n",
    "pkg/mid/mid.go": (
        "package mid\n\n"
        'import "example.com/m/pkg/inner"\n\n'
        "type Mid struct{ D inner.Deep }\n\n"
        "func Make() *Mid { return &Mid{} }\n"
    ),
    "app/app.go": (
        "package app\n\n"
        "import (\n"
        '\t"io"\n'
        '\t"os"\n'
        '\t"example.com/m/pkg/db"\n'
        '\t"example.com/m/pkg/mid"\n'
        ")\n\n"
        "type Local struct{}\n\n"
        "func (l *Local) Run() {}\n\n"
        "func helper() *Local { return &Local{} }\n\n"
        "func Package() { db.Open() }\n\n"
        "func Typed(s *db.Store) { s.Put() }\n\n"
        "func Own() { l := &Local{}; l.Run() }\n\n"
        "func Result() { x := db.Open(); x.Put(); helper().Run() }\n\n"
        "func Field(s *db.Store) { s.Conn.Close() }\n\n"
        "func Unnamed() { m := mid.Make(); m.D.Go() }\n\n"
        "func Untyped(xs []any) { xs[0].Frob(); xs[0].Close(); xs[0].frob() }\n\n"
        "func Unknown() { var x foo.T; x.Do() }\n\n"
        "func Stream() { r := io.LimitReader(os.Stdin, 8); r.Read(nil) }\n\n"
        "func Chain() { mk()().Close(); y := mk()(); y.Do() }\n\n"
        "func NamedType(l db.Level) { l.Name() }\n\n"
        "func Conversion() { _ = db.Duration(3) }\n"
    ),
}


def test_a_call_on_a_package_goes_out_with_its_qualifier(tmp_path: Path) -> None:
    edge = _one(_calls(_analyze(tmp_path, _ROUTES), "Package"), "Open")
    assert _facts(edge) == {"qualifier": "db", "call_construct": "function"}
    assert edge.dst == "go:example.com/m/pkg/db:0-0:Open:unresolved"


def test_a_typed_receiver_goes_out_with_its_type_as_written(tmp_path: Path) -> None:
    result = _analyze(tmp_path, _ROUTES)
    assert _facts(_one(_calls(result, "Typed"), "Put")) == {
        "receiver_type_hint": "db.Store", "call_construct": "method"}
    assert _facts(_one(_calls(result, "Own"), "Run")) == {
        "receiver_type_hint": "Local", "call_construct": "method"}


def test_a_call_result_is_typed_by_the_declared_return_type(tmp_path: Path) -> None:
    """``db.Open()`` is declared ``*Store`` in ``pkg/db``: in the caller's file
    that type is ``db.Store``; ``helper()`` returns the caller's own
    ``Local``."""
    calls = _calls(_analyze(tmp_path, _ROUTES), "Result")
    assert _one(calls, "Put").meta["receiver_type_hint"] == "db.Store"
    assert _one(calls, "Run").meta["receiver_type_hint"] == "Local"


def test_a_field_is_typed_in_the_file_of_its_struct(tmp_path: Path) -> None:
    """``Store.Conn`` is ``*Conn`` in ``pkg/db``, which the caller writes
    ``db.Conn``; ``Mid.D`` is ``inner.Deep``, whose package the caller does not
    import: the type goes out by its import path, which no table binds."""
    result = _analyze(tmp_path, _ROUTES)
    assert _one(_calls(result, "Field"), "Close").meta["receiver_type_hint"] == "db.Conn"
    assert _one(_calls(result, "Unnamed"), "Go").meta["receiver_type_hint"] == (
        "example.com/m/pkg/inner.Deep")


def test_an_untyped_receiver_says_nothing_but_the_library_and_the_case(tmp_path: Path) -> None:
    calls = _calls(_analyze(tmp_path, _ROUTES), "Untyped")
    assert _facts(_one(calls, "Frob")) == {"call_construct": "method"}
    close = _one(calls, "Close")
    assert _facts(close) == {"stdlib_member_name": True, "call_construct": "method"}
    assert close.meta["receiver"] == "stdlib"
    frob = _one(calls, "frob")
    assert (frob.meta["visibility"], frob.is_resolved) == ("unexported", False)


def test_a_type_no_import_names_goes_out_as_written(tmp_path: Path) -> None:
    """``var x foo.T`` with no import ``foo``: the hint is the text, which no
    table binds, so nothing binds the call by its name either."""
    edge = _one(_calls(_analyze(tmp_path, _ROUTES), "Unknown"), "Do")
    assert (edge.is_resolved, edge.meta["receiver_type_hint"]) == (False, "foo.T")


def test_an_untyped_local_keeps_its_handle_kind(tmp_path: Path) -> None:
    """``io.LimitReader(os.Stdin, 8)`` types nothing, but the receiver's
    binding still says what the read touches (WI-vutav)."""
    edge = _one(_calls(_analyze(tmp_path, _ROUTES), "Stream"), "Read")
    assert (edge.is_resolved, edge.meta["io_target_kind"]) == (False, "std_stream")
    assert "receiver_type_hint" not in edge.meta


def test_a_result_of_unknown_type_says_so(tmp_path: Path) -> None:
    calls = _calls(_analyze(tmp_path, _ROUTES), "Chain")
    close = _one(calls, "Close")
    assert (close.meta["receiver"], close.meta["stdlib_member_name"]) == ("field_chain", True)
    assert "receiver_type_hint" not in _one(calls, "Do").meta


def test_what_the_shared_pass_cannot_bind_is_bound_here(tmp_path: Path) -> None:
    """A method of ``type Level uint32`` (no type kind the linkers take), and
    ``db.Duration(3)``, a conversion: bound by identity, in the type's own
    directory."""
    result = _analyze(tmp_path, _ROUTES)
    [name] = _calls(result, "NamedType")
    assert (name.dst.split(":")[-2], name.meta["resolved_by"]) == ("Level.Name", "receiver_type")
    assert name.meta["resolution_quality"] == "typed_receiver"
    [conversion] = _calls(result, "Conversion")
    assert (conversion.dst.split(":")[-2], conversion.meta["resolved_by"]) == ("Duration", "import")


_TYPED_BY_GO = {
    "pkg/db/db.go": (
        "package db\n\n"
        "type Store struct{}\n\n"
        "func Open() *Store { return &Store{} }\n\n"
        "func (s *Store) Put() {}\n\n"
        "type Level uint32\n\n"
        "func (l Level) Name() string { return \"\" }\n"
    ),
    "app/vars.go": (
        "package app\n\n"
        'import "example.com/m/pkg/db"\n\n'
        "var std = db.Open()\n\n"
        "var (\n\tlvl db.Level\n\tlit = &Local{}\n\tn, m = 1, 2\n\tp, q = two()\n)\n\n"
        "type Local struct{}\n\n"
        "func (l *Local) Run() {}\n\n"
        "func two() (int, int) { return 1, 2 }\n"
    ),
    "app/app.go": (
        "package app\n\n"
        'import "example.com/m/pkg/db"\n\n'
        "func run(f func(*db.Store)) {}\n\n"
        "func Assert(v any) {\n\tv.(db.Store).Put()\n\ts, _ := v.(*db.Store)\n\ts.Put()\n"
        "\tx := v.(map[string]int)\n\t_ = x\n}\n\n"
        "func Closure() { run(func(s *db.Store) { s.Put() }) }\n\n"
        "func PackageVar() { std.Put(); lvl.Name(); lit.Run() }\n\n"
        "func Shadowed(xs []int) { for _, std := range xs { std.Put() } }\n"
    ),
}


def test_go_s_own_declarations_type_a_receiver(tmp_path: Path) -> None:
    """A type assertion (``v.(db.Store)``, ``s, _ := v.(*db.Store)``), a
    function literal's parameter, and a package-level variable of the
    caller's package (declared type, composite literal, a callee's declared
    return type, read in the declaring file) type the receiver; a local of
    the variable's name in scope hides it."""
    result = _analyze(tmp_path, _TYPED_BY_GO)
    assert [e.meta["receiver_type_hint"] for e in _calls(result, "Assert")] == [
        "db.Store", "db.Store"]
    assert _one(_calls(result, "Closure"), "Put").meta["receiver_type_hint"] == "db.Store"
    calls = _calls(result, "PackageVar")
    assert _one(calls, "Put").meta["receiver_type_hint"] == "db.Store"
    assert _one(calls, "Run").meta["receiver_type_hint"] == "Local"
    assert _one(calls, "Name").meta["resolved_by"] == "receiver_type"
    assert "receiver_type_hint" not in _one(_calls(result, "Shadowed"), "Put").meta


_DOTS = {
    "pkg/lib/lib.go": (
        "package lib\n\n"
        "type Logger struct{}\n\n"
        "func New() *Logger { return &Logger{} }\n\n"
        "func (l *Logger) Info() {}\n\n"
        "type Kind string\n"
    ),
    "app/app_test.go": (
        "package app_test\n\n"
        'import . "example.com/m/pkg/lib"\n\n'
        "func TestIt() {\n\tl := New()\n\tl.Info()\n\tvar o Logger\n\to.Info()\n"
        "\t_ = Kind(\"x\")\n\tMissing()\n}\n"
    ),
}


def test_a_dot_import_types_and_converts_as_go_does(tmp_path: Path) -> None:
    """In a file that dot-imports ``lib``, ``New()`` is ``lib.New`` and
    ``Logger`` is ``lib.Logger``, which the file writes bare; ``Kind("x")``
    converts to ``lib.Kind``. A name no package declares stays a stub."""
    calls = _calls(_analyze(tmp_path, _DOTS), "TestIt")
    info = [e for e in calls if e.dst.split(":")[-2] == "Info"]
    assert [e.meta["receiver_type_hint"] for e in info] == ["Logger", "Logger"]
    kind = _one(calls, "Kind")
    assert (kind.is_resolved, kind.meta["resolved_by"]) == (True, "import")
    new = _one(calls, "New")
    assert (new.is_resolved, new.meta["binding"]) == (False, "dot_import")
    assert _one(calls, "Missing").is_resolved is False


# --------------------------------------------- through the shared pass

def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", {"go.mod": _GO_MOD, **files})
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str, callee: str) -> list[tuple[str, str]]:
    """(target's path, mark) of each resolved ``calls`` edge from ``caller`` to
    a symbol whose name ends with ``callee``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    out = []
    for e in data["edges"]:
        src, dst = nodes.get(e["src"]), nodes.get(e["dst"])
        if (
            e["type"] == "calls" and e.get("is_resolved") and src and dst
            and src.get("name") == caller and dst.get("name", "").split(".")[-1] == callee
        ):
            out.append((dst["path"], (e.get("meta") or {}).get("resolved_by")))
    return out


_NAMESAKES = {
    "util/util.go": (
        "package util\n\nfunc Helper() int { return 1 }\n\n"
        "type Store struct{}\n\nfunc (s *Store) Put() {}\n\n"
        "func Marshal() {}\n"
    ),
    "decoy/util.go": (
        "package util\n\nfunc Helper() int { return 2 }\n\n"
        "type Store struct{}\n\nfunc (s *Store) Put() {}\n"
    ),
    "internal/json/json.go": "package json\n\nfunc Marshal(v any) ([]byte, error) { return nil, nil }\n",
    "app/sibling.go": "package app\n\nfunc sibling() {}\n",
    "other/sibling.go": "package other\n\nfunc sibling() {}\n",
    "app/app.go": (
        "package app\n\n"
        "import (\n"
        '\t"encoding/json"\n'
        '\t"example.com/m/util"\n'
        ")\n\n"
        "func ViaImport() { util.Helper() }\n\n"
        "func ViaType(s *util.Store) { s.Put() }\n\n"
        "func ViaResult() { util.Make().Put() }\n\n"
        "func OwnPackage() { sibling() }\n\n"
        "func Untyped(xs []any) { xs[0].Put() }\n\n"
        "func Library() { json.Marshal(1) }\n"
    ),
    "util/make.go": "package util\n\nfunc Make() *Store { return &Store{} }\n",
}


def test_through_the_shared_pass_each_call_reaches_its_own_declaration(tmp_path: Path) -> None:
    """Each callee has a namesake in another directory: an import binds the
    imported directory's, a typed receiver its type's, a bare name the
    caller's package's; an untyped receiver and a library call bind nothing
    (``json.Marshal`` is not ``internal/json``'s, whose directory is named
    like the package)."""
    data = _map(tmp_path, _NAMESAKES)
    assert _resolved(data, "ViaImport", "Helper") == [("util/util.go", "import")]
    assert _resolved(data, "ViaType", "Put") == [("util/util.go", "receiver_type")]
    assert _resolved(data, "ViaResult", "Put") == [("util/util.go", "receiver_type")]
    assert _resolved(data, "OwnPackage", "sibling") == [("app/sibling.go", "same_module")]
    assert _resolved(data, "Untyped", "Put") == []
    assert _resolved(data, "Library", "Marshal") == []


def test_through_the_shared_pass_a_dot_import_reaches_its_package(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        **_DOTS,
        "pkg/other/other.go": (
            "package other\n\ntype Logger struct{}\n\nfunc (l *Logger) Info() {}\n\n"
            "func New() *Logger { return &Logger{} }\n"
        ),
    })
    assert _resolved(data, "TestIt", "New") == [("pkg/lib/lib.go", "import")]
    assert _resolved(data, "TestIt", "Info") == [("pkg/lib/lib.go", "receiver_type")]
