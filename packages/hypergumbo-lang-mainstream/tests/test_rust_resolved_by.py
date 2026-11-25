# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hirol: every route by which rust.py binds a ``calls`` edge states its mark.

``Edge.meta["resolved_by"]`` names what scoped the choice of the callee
(:mod:`hypergumbo_core.resolved_by`). One fixture crate holds one caller per
route; each test finds that caller's edge (it must exist) and pins the value:

- ``Foo::new()`` / ``crate::Foo::new()`` matched by the written path:
  ``qualified``; ``Self::new()`` and ``self.m()``: ``own_type``;
- ``self.field.m()``, a typed local, a chained call's return type:
  ``receiver_type``;
- a bare call (or ``tokio::spawn(f)``) to a free function of the caller's own
  module: ``same_module``;
- a function a ``use`` or ``mod`` binding names (``f()``, ``h3()`` for ``use
  ... as h3``, ``config::f()``): ``import``, bound by the shared pass from the
  file's import table (ADR-0064), so the fixture runs that pass too;
- the one-trait swap: ``name``;
- calls inside macro bodies: the same rule, with that route's own lookups.

A call the analyzer used to bind by a name looked up repository-wide, or by
this file's table of names, and that nothing in its file's imports explains
is bound by nobody now: ``test_a_name_alone_binds_nothing``.
"""

from __future__ import annotations

import pytest

from _rust_shared_pass import analyze_and_link

_FILES = {
    'Cargo.toml': '[package]\nname = "app"\nversion = "0.1.0"\n',
    'src/main.rs': 'mod config;\nmod other;\nmod near;\nmod clock;\nuse crate::config::parse_configs;\nuse crate::other::aliased_target as h3;\n\nstruct Foo { bar: Bar }\nstruct Bar;\nstruct Cmd;\n\nimpl Bar {\n    fn ping(&self) {}\n}\n\nimpl Cmd {\n    fn parse() -> Cmd { Cmd }\n    fn run(&self) {}\n}\n\nimpl Foo {\n    fn new() -> Foo { Foo { bar: Bar } }\n    fn own(&self) {}\n    fn qualified_local() { Foo::new(); }\n    fn self_path() { Self::new(); }\n    fn self_method(&self) { self.own(); }\n    fn self_field(&self) { self.bar.ping(); }\n    fn macro_self_path() { assert!(Self::new()); }\n}\n\nfn my_task() {}\nfn helper() -> bool { true }\n\nmod inner {\n    pub fn inner_only() {}\n}\n\nasync fn spawn_local() { tokio::spawn(my_task); }\nasync fn spawn_far() { tokio::spawn(far_task); }\nfn qualified_far() { Widget::make(); }\nfn use_module_path() { config::parse_in_mod(); }\nfn typed_var(b: &Bar) { b.ping(); }\nfn chained() { Cmd::parse().run(); }\nfn bare_module() { helper(); }\nfn bare_inner_only() { inner_only(); }\nfn bare_use() { parse_configs(); }\nfn bare_alias() { h3(); }\nfn bare_far() { far_function(); }\nfn untyped_far(x: u8) { x.zap_far(); }\nfn macro_bare() { assert!(helper()); }\nfn macro_qualified() { assert!(Foo::new()); }\nfn macro_short() { assert!(Gone::helper()); }\nfn macro_far() { assert!(far_function()); }\nfn main() {}\nfn scoped_crate_local() { crate::Foo::new(); }\nfn scoped_crate_far() { crate::other::Widget::make(); }\nimpl Foo {\n    fn bare_own(&self) { own_elsewhere(); }\n}\nextern "C" { fn now(); }\nfn sys() { SystemTime::now(); }\n',
    'src/config/mod.rs': 'pub fn parse_configs() {}\npub fn parse_in_mod() {}\n',
    'src/other.rs': 'pub struct Widget;\nimpl Widget {\n    pub fn make() {}\n    pub fn zap_far(&self) {}\n}\npub fn aliased_target() {}\npub async fn far_task() {}\npub fn far_function() {}\nuse crate::config;\npub fn scoped_use() { config::parse_configs(); }\nimpl crate::Foo {\n    pub fn own_elsewhere(&self) {}\n}\npub trait Speak { fn speak(&self); }\npub struct A;\npub struct B;\nimpl Speak for A { fn speak(&self) {} }\nimpl Speak for B { fn speak(&self) {} }\npub fn talk() { make_it().speak(); }\n',
    'src/clock.rs': 'pub fn now() {}\n',
}


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("rust_mark") / "app"
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return analyze_and_link(repo)


def _edge(analysis, caller: str, callee: str, evidence: str):
    """The one resolved ``calls`` edge from ``caller`` to ``callee``."""
    by_id = {s.id: s for s in analysis.symbols}
    found = [
        e for e in analysis.edges
        if e.edge_type == "calls" and e.is_resolved
        and e.evidence_type == evidence
        and e.src in by_id and by_id[e.src].name == caller
        and e.dst in by_id and by_id[e.dst].name == callee
    ]
    assert len(found) == 1, (caller, callee, [
        (e.dst, e.evidence_type) for e in analysis.edges
        if e.edge_type == "calls" and e.src in by_id and by_id[e.src].name == caller
    ])
    return found[0]


@pytest.mark.parametrize(("caller", "callee", "evidence", "mark"), [
    ("spawn_local", "my_task", "async_spawn", "same_module"),
    ("Foo::qualified_local", "Foo::new", "ast_call", "qualified"),
    ("Foo::self_path", "Foo::new", "ast_call", "own_type"),
    ("scoped_crate_local", "Foo::new", "ast_call", "qualified"),
    ("scoped_crate_far", "Widget::make", "ast_call", "qualified"),
    ("scoped_use", "parse_configs", "ast_call_direct", "import"),
    ("Foo::self_method", "Foo::own", "ast_call", "own_type"),
    ("Foo::self_field", "Bar::ping", "ast_call", "receiver_type"),
    ("typed_var", "Bar::ping", "ast_call_type_inferred", "receiver_type"),
    ("chained", "Cmd::run", "ast_call_type_inferred", "receiver_type"),
    ("bare_module", "helper", "ast_call", "same_module"),
    ("bare_use", "parse_configs", "ast_call_direct", "import"),
    ("bare_alias", "aliased_target", "ast_call_direct", "import"),
    ("use_module_path", "parse_in_mod", "ast_call_direct", "import"),
    ("talk", "Speak::speak", "ast_call", "name"),
    ("macro_bare", "helper", "ast_call", "same_file"),
    ("Foo::macro_self_path", "Foo::new", "ast_call", "own_type"),
    ("macro_qualified", "Foo::new", "ast_call", "qualified"),
    ("macro_short", "helper", "ast_call", "same_file"),
    ("macro_far", "far_function", "ast_call", "name"),
])
def test_route_states_its_mark(analysis, caller, callee, evidence, mark) -> None:
    edge = _edge(analysis, caller, callee, evidence)
    assert edge.meta["resolved_by"] == mark


@pytest.mark.parametrize(("caller", "callee", "stub"), [
    # A spawned task's name is not a call: with no binding there is no stub either.
    ("spawn_far", "far_task", False),          # no ``use`` of far_task in main.rs
    ("qualified_far", "Widget::make", True),   # Widget is neither declared in nor imported into main.rs
    ("bare_inner_only", "inner_only", True),   # an item of another module, not imported
    ("bare_far", "far_function", True),        # no ``use`` of far_function
    ("untyped_far", "Widget::zap_far", True),  # ``x: u8``: nothing types the receiver as a Widget
])
def test_a_name_alone_binds_nothing(analysis, caller, callee, stub) -> None:
    """These were bound by a name looked up repository-wide or in this file's
    table of names (marks ``name`` / ``same_file``). Nothing in the caller's
    file says the name means that declaration, so no resolved edge reaches it
    (ADR-0064); a call stays a stub."""
    by_id = {s.id: s for s in analysis.symbols}
    assert callee in {s.name for s in analysis.symbols}, "reach"
    from_caller = [e for e in analysis.edges if e.edge_type == "calls"
                   and e.src in by_id and by_id[e.src].name == caller]
    assert not [e for e in from_caller if e.dst in by_id and by_id[e.dst].name == callee]
    assert bool([e for e in from_caller if not e.is_resolved]) == stub, from_caller
