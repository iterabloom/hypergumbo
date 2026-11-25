# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which go.py binds a call-family edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). One fixture repository holds one caller
per route; each test finds that caller's edge to the expected callee (the edge
must exist: a test that finds none fails) and pins the value:

- a bare call found in the caller's file table: ``same_file``; in the caller's
  own package (another file): ``same_module``; through a dot import: ``import``;
- ``b.F()`` through the file's import of ``b``: ``import``;
- a method on a typed receiver (a declaration, a field chain, a chained call's
  return type): ``receiver_type``;
- the interface-method preference among a method name's repo-wide candidates,
  and a value receiver of unknown type: ``name``;
- a function passed as an argument, a package-level alias, a struct field
  value: what scoped the referenced name (``same_file`` / ``same_module`` /
  ``import`` / ``qualified`` for a method expression / ``receiver_type`` for a
  method value);
- a chi ``r.Mount`` handler found in the file: ``same_file``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    ("CallUntypedInFile", "Maker.Frob", "ast_call", "same_file"),
    ("CallSamePackage", "other", "ast_call", "same_module"),
    ("CallImported", "Exported", "ast_call", "import"),
    ("CallDotted", "Dotted", "ast_call", "import"),
    ("CallTypedLocal", "Local.Do", "ast_call", "receiver_type"),
    ("CallTypedOtherFile", "Store.Put", "ast_call", "receiver_type"),
    ("Retry.CallFieldChain", "Integration.Notify", "ast_call", "receiver_type"),
    ("CallChained", "Query.Exec", "ast_call", "receiver_type"),
    ("CallUntypedValue", "Thing.Zork", "ast_call", "name"),
    ("CallInterface", "Worker.Run", "interface_dispatch", "name"),
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


def test_route_mount_handler_states_its_mark(analysis) -> None:
    """``r.Mount`` carries its handler found in the file's table."""
    mounts = [
        e for e in analysis.edges
        if (e.meta or {}).get("framework_dispatch") == "route_mount"
    ]
    assert len(mounts) == 1
    assert mounts[0].is_resolved
    assert mounts[0].meta["resolved_by"] == "same_file"


def test_an_import_of_the_module_root_filters_nothing(tmp_path: Path) -> None:
    """``run(root.F)`` where ``root`` imports the module's root package: the
    empty hint scopes no lookup, so the value is the lookup's own (``name``)."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/fx\n\ngo 1.21\n")
    (repo / "root.go").write_text("package fx\n\nfunc Rooted() {}\n")
    (repo / "sub" / "s.go").write_text(
        "package sub\n\nimport root \"example.com/fx\"\n\n"
        "func run(f any) {}\n\nfunc Ref() { run(root.Rooted) }\n"
    )
    edge = _edge(analyze_go(repo), "Ref", "Rooted", "function_reference_arg")
    assert edge.meta["resolved_by"] == "name"
