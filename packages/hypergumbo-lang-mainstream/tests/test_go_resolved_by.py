# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which a Go call-family edge is bound states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). One fixture repository holds one caller
per route; each test finds that caller's edge to the expected callee (the edge
must exist: a test that finds none fails) and pins the value.

Bound by go.py itself (``analyze_go`` alone):

- a bare call found in the caller's file table: ``same_file``; in the caller's
  own package (another file): ``same_module``;
- a function passed as an argument, a package-level alias, a struct field
  value: what scoped the referenced name (``same_file`` / ``same_module`` /
  ``import`` / ``qualified`` for a method expression / ``receiver_type`` for a
  method value);
- a chi ``r.Mount`` handler found in the file: ``same_file``.

Reported by go.py and bound by the shared pass (ADR-0064; the whole pipeline):

- ``b.F()`` through the file's import of ``b``, and a bare call through a dot
  import: ``import``;
- a method on a typed receiver (a declaration, a field chain, a callee's
  declared return type): ``receiver_type``;
- a value receiver of unknown type: not bound (the name step is off for Go).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.go import analyze_go

_FILES = {
    "go.mod": "module example.com/fx\n\ngo 1.21\n",
    "b/b.go": (
        "package b\n\n"
        "type Thing struct{}\n\n"
        "func NewThing() *Thing { return &Thing{} }\n\n"
        "func (t *Thing) Zork() {}\n\n"
        "func Exported() {}\n"
    ),
    "d/d.go": "package d\n\nfunc Dotted() {}\n",
    "a/other.go": (
        "package a\n\n"
        "func other() {}\n\n"
        "type Store struct{}\n\n"
        "func (s *Store) Put() {}\n"
    ),
    "a/a.go": (
        "package a\n\n"
        "import (\n"
        "\t\"example.com/fx/b\"\n"
        "\t. \"example.com/fx/d\"\n"
        ")\n\n"
        "type Local struct{}\n\n"
        "func (l *Local) Do() {}\n\n"
        "type Val struct{}\n\n"
        "func (v Val) Get() {}\n\n"
        "type Maker struct{}\n\n"
        "func (m *Maker) Frob() {}\n\n"
        "type Integration struct{}\n\n"
        "func (i *Integration) Notify() {}\n\n"
        "type Retry struct {\n\tintegration Integration\n}\n\n"
        "type Engine struct{}\n\n"
        "type Query struct{}\n\n"
        "func (e *Engine) NewQuery() *Query { return &Query{} }\n\n"
        "func (q *Query) Exec() {}\n\n"
        "type Command struct {\n\tRun func()\n}\n\n"
        "func helper() {}\n\n"
        "func run(f any) {}\n\n"
        "var aliasLocal = helper\n\n"
        "var aliasImported = b.Exported\n\n"
        "func CallSameFile() { helper() }\n\n"
        "func CallSamePackage() { other() }\n\n"
        "func CallImported() { b.Exported() }\n\n"
        "func CallDotted() { Dotted() }\n\n"
        "func CallTypedLocal(l *Local) { l.Do() }\n\n"
        "func CallTypedOtherFile(s *Store) { s.Put() }\n\n"
        "func (r *Retry) CallFieldChain() { r.integration.Notify() }\n\n"
        "func CallChained(e *Engine) { e.NewQuery().Exec() }\n\n"
        "func CallUntypedInFile(x []Maker) { x[0].Frob() }\n\n"
        "func CallUntypedValue() { b.NewThing().Zork() }\n\n"
        "func RefSameFile() { run(helper) }\n\n"
        "func RefSamePackage() { run(other) }\n\n"
        "func RefImported() { run(b.Exported) }\n\n"
        "func RefMethodExpr() { run(Val.Get) }\n\n"
        "func RefPointerMethodExpr() { run((*Local).Do) }\n\n"
        "func RefMethodValue(l *Local) { run(l.Do) }\n\n"
        "func RefLiteralMethodValue() { run((&Local{}).Do) }\n\n"
        "func FieldRef() { _ = Command{Run: helper} }\n"
    ),
    "w/w.go": (
        "package w\n\n"
        "type Worker interface {\n\tRun()\n}\n\n"
        "type FooImpl struct{}\n\n"
        "func (f *FooImpl) Run() {}\n\n"
        "var wk Worker\n\n"
        "func CallInterface() { wk.Run() }\n"
    ),
    "srv/srv.go": (
        "package srv\n\n"
        "import \"github.com/go-chi/chi/v5\"\n\n"
        "func apiRoutes() *chi.Mux { return chi.NewRouter() }\n\n"
        "func Setup() {\n"
        "\tr := chi.NewRouter()\n"
        "\tr.Mount(\"/api/v1\", apiRoutes())\n"
        "}\n"
    ),
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("go_mark") / "repo"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_go(repo)


def _edge(analysis, caller: str, callee: str, evidence: str | None = None):
    """The one resolved call-family edge from ``caller`` to ``callee``."""
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type in ("calls", "instantiates") and e.is_resolved
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
        and (evidence is None or e.evidence_type == evidence)
    ]
    assert len(found) == 1, (caller, callee, [
        (e.src, e.dst, e.evidence_type) for e in analysis.edges
        if e.edge_type == "calls" and e.src in by_id and by_id[e.src].name == caller
    ])
    return found[0]


@pytest.mark.parametrize(("caller", "callee", "evidence", "mark"), [
    ("CallSameFile", "helper", "ast_call", "same_file"),
    ("CallSamePackage", "other", "ast_call", "same_module"),
    ("RefSameFile", "helper", "function_reference_arg", "same_file"),
    ("RefSamePackage", "other", "function_reference_arg", "same_module"),
    ("RefImported", "Exported", "function_reference_arg", "import"),
    ("RefMethodExpr", "Val.Get", "function_reference_arg", "qualified"),
    ("RefPointerMethodExpr", "Local.Do", "function_reference_arg", "qualified"),
    ("RefMethodValue", "Local.Do", "function_reference_arg", "receiver_type"),
    ("RefLiteralMethodValue", "Local.Do", "function_reference_arg", "receiver_type"),
    ("aliasLocal", "helper", "function_reference", "same_file"),
    ("aliasImported", "Exported", "function_reference", "import"),
    ("FieldRef", "helper", "struct_field_reference", "same_file"),
    ("Setup", "apiRoutes", "ast_call_direct", "same_file"),
])
def test_route_states_its_mark(analysis, caller, callee, evidence, mark) -> None:
    edge = _edge(analysis, caller, callee, evidence)
    assert edge.meta["resolved_by"] == mark


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The whole pipeline's map of the fixture repository."""
    root = tmp_path_factory.mktemp("go_mark_shared")
    repo = root / "repo"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    run_behavior_map(repo_root=repo, out_path=root / "map.json", include_sketch_precomputed=False)
    return json.loads((root / "map.json").read_text())


def _shared_marks(data: dict[str, Any], caller: str, callee: str) -> list[str]:
    """The marks of the resolved ``calls`` edges from ``caller`` to ``callee``."""
    names = {n["id"]: n.get("name") for n in data["nodes"]}
    return [
        (e.get("meta") or {}).get("resolved_by") for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved")
        and names.get(e["src"]) == caller and names.get(e["dst"]) == callee
    ]


@pytest.mark.parametrize(("caller", "callee", "mark"), [
    ("CallImported", "Exported", "import"),
    ("CallDotted", "Dotted", "import"),
    ("CallTypedLocal", "Local.Do", "receiver_type"),
    ("CallTypedOtherFile", "Store.Put", "receiver_type"),
    ("Retry.CallFieldChain", "Integration.Notify", "receiver_type"),
    ("CallChained", "Query.Exec", "receiver_type"),
    # ``b.NewThing()`` is declared to return ``*Thing``: a typed receiver.
    ("CallUntypedValue", "Thing.Zork", "receiver_type"),
    # ``var wk Worker``: a package-level variable's declared type.
    ("CallInterface", "Worker.Run", "receiver_type"),
])
def test_a_reported_route_is_bound_by_the_shared_pass(shared, caller, callee, mark) -> None:
    assert _shared_marks(shared, caller, callee) == [mark]


@pytest.mark.parametrize(("caller", "callee"), [
    ("CallUntypedInFile", "Maker.Frob"),
])
def test_an_untyped_receiver_is_bound_by_no_route(shared, analysis, caller, callee) -> None:
    """``x[0].Frob()``: the analyzer does not type the receiver and nothing
    binds it by name."""
    assert _shared_marks(shared, caller, callee) == []
    by_id = {s.id: s.name for s in analysis.symbols}
    stubs = [
        e for e in analysis.edges
        if e.edge_type == "calls" and by_id.get(e.src) == caller
        and e.dst.split(":")[-2] == callee.split(".")[-1]
    ]
    assert [e.is_resolved for e in stubs] == [False]


def test_route_mount_handler_states_its_mark(analysis) -> None:
    """``r.Mount`` carries its handler found in the file's table."""
    mounts = [
        e for e in analysis.edges
        if (e.meta or {}).get("framework_dispatch") == "route_mount"
    ]
    assert len(mounts) == 1
    assert mounts[0].is_resolved
    assert mounts[0].meta["resolved_by"] == "same_file"


def test_an_import_of_the_module_root_names_its_directory(tmp_path: Path) -> None:
    """``run(root.F)`` where ``root`` imports the module's root package: the
    root is a directory like any other (ADR-0064), so the import scopes it."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/fx\n\ngo 1.21\n")
    (repo / "root.go").write_text("package fx\n\nfunc Rooted() {}\n")
    (repo / "sub" / "s.go").write_text(
        "package sub\n\nimport root \"example.com/fx\"\n\n"
        "func run(f any) {}\n\nfunc Ref() { run(root.Rooted) }\n"
    )
    edge = _edge(analyze_go(repo), "Ref", "Rooted", "function_reference_arg")
    assert edge.meta["resolved_by"] == "import"
