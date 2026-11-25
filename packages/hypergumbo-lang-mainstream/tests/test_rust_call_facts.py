# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Rust analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence Rust itself uses and that it holds:

* an item of the caller's own module (a bare ``f()``; ``same_module``), or of
  a module of the same file a ``use ...::*`` opens (``use super::*`` in
  ``mod tests``; ``import``);
* an item this file declares under the path the call writes (``Foo::new()``
  with ``impl Foo`` here, ``qualified``; ``Self::new()``, ``own_type``);
* a path rooted where the module layout reads it (``crate::`` / ``self::`` /
  ``super::`` / an in-tree crate), followed to the file that declares the
  item (``qualified``);
* a method of the receiver's known type declared in this file
  (``receiver_type`` / ``own_type``);
* the one-trait rule: every method of the name in the tree is one trait's
  (``name``, kept as the analyzer's own trait evidence).

Every other call is emitted unresolved with the facts the ``inherited_calls``
linker reads: ``qualifier`` (the owner a path call names, as written) and
``receiver_type_hint`` (the receiver's type), or nothing for a bare name,
which the linker looks up in the file's import table. The table
(``AnalysisResult.import_scopes``) holds the bindings whose module is a FILE
of the tree.

The first half pins the table and each route on the analyzer alone. The
second half runs the whole pipeline for the cases the shared pass exists for,
each with a namesake in a third file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.rust import _rust_type_is_external, analyze_rust

_CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in {"Cargo.toml": _CARGO, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _calls(tmp_path: Path, files: dict[str, str], caller: str) -> list[Edge]:
    """The ``calls`` edges the analyzer emits from the function named ``caller``."""
    result = analyze_rust(_write(tmp_path, files))
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _bound(edges: list[Edge]) -> list[tuple[str, str]]:
    """(target id, mark) of the resolved edges."""
    return [(e.dst, e.meta["resolved_by"]) for e in edges if e.is_resolved]


def _stub(edges: list[Edge]) -> dict[str, Any]:
    """The facts of the one unresolved edge (there must be exactly one, and no resolved one)."""
    assert not _bound(edges), _bound(edges)
    (stub,) = [e for e in edges if not e.is_resolved]
    meta = stub.meta or {}
    return {k: meta[k] for k in ("callee_name", "qualifier", "receiver_type_hint", "call_construct")
            if k in meta}


# --------------------------------------------------------------- the table

def _scopes(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    return analyze_rust(_write(tmp_path, files)).import_scopes


def test_every_file_hands_over_its_table_with_explicit_and_namespace_bindings(tmp_path: Path) -> None:
    scopes = _scopes(tmp_path, {
        "src/lib.rs": "pub mod db;\npub mod util;\nmod quiet;\n",
        "src/db.rs": "pub struct Conn;\npub fn connect() {}\n",
        "src/util.rs": (
            "use crate::db::Conn;\n"                  # explicit
            "use crate::db::connect as open;\n"       # explicit, renamed
            "use crate::db::{self as store};\n"       # namespace, renamed
            "use crate::db;\n"                        # namespace
            "use crate::{db::{self as d2}};\n"        # namespace in a nested group
            "use std::collections::HashMap;\n"        # a library: not recorded
            "use serde::Serialize;\n"                 # a registry crate: not recorded
            "use crate::nowhere::Thing;\n"            # a path the layout cannot place
        ),
        "src/quiet.rs": "pub fn alone() {}\n",
    })
    assert set(scopes) == {"src/lib.rs", "src/db.rs", "src/util.rs", "src/quiet.rs"}
    util = scopes["src/util.rs"]
    assert util.module_kind == "file"
    assert (util.named("Conn").module, util.named("Conn").name) == ("src/db.rs", "Conn")
    assert (util.named("open").module, util.named("open").name) == ("src/db.rs", "connect")
    assert util.namespace("store") == util.namespace("db") == util.namespace("d2") == "src/db.rs"
    assert util.resolve("HashMap") is None and util.resolve("Serialize") is None
    # ``crate::nowhere`` reaches no module file: the path stops at the crate
    # root, which does not declare ``Thing`` (the lookup then finds nothing).
    assert util.named("Thing").module == "src/lib.rs"
    # ``mod m;`` binds the module's name to its file; a file with no import has
    # an empty table, which still says the file reports its calls.
    lib = scopes["src/lib.rs"]
    assert lib.namespace("db") == "src/db.rs" and lib.namespace("quiet") == "src/quiet.rs"
    assert scopes["src/quiet.rs"].resolve("alone") is None


def test_wildcards_and_what_the_table_leaves_out(tmp_path: Path) -> None:
    scopes = _scopes(tmp_path, {
        "src/lib.rs": "pub mod db;\npub mod kinds;\npub mod user;\n",
        "src/db.rs": "pub fn connect() {}\n",
        "src/kinds.rs": "pub enum Kind { A, B }\n",
        "src/user.rs": (
            "use crate::db::*;\n"           # opens db.rs
            "use crate::kinds::Kind::*;\n"  # opens an enum: not recorded
            "use std::io::*;\n"             # a library: not recorded
            "mod a { use crate::db::connect as f; }\n"
            "mod b { use crate::kinds::Kind as f; }\n"  # ``f`` bound twice: neither kept
            "mod c { mod inner; }\n"        # a ``mod`` inside an inline module: not recorded
            "mod d { use super::*; }\n"     # opens this file
        ),
    })
    user = scopes["src/user.rs"]
    assert user.wildcard_modules == ("src/db.rs", "src/user.rs")
    assert user.resolve("f") is None and user.resolve("inner") is None


# --------------------------------------------------------------- bare calls

_DB = "pub struct Conn;\nimpl Conn {\n    pub fn new() -> Conn { Conn }\n    pub fn execute(&self) {}\n}\n"


def test_a_bare_call_binds_its_own_modules_function(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {"src/lib.rs": "fn helper() {}\npub fn run() { helper(); }\n"}, "run")
    assert _bound(edges) == [("rust:src/lib.rs:1-1:helper:function", "same_module")]


def test_a_bare_call_through_use_super_in_an_inline_module(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {"src/lib.rs": (
        "fn helper() {}\n"
        "mod tests {\n    use super::*;\n    pub fn t() { helper(); }\n}\n"
    )}, "t")
    assert _bound(edges) == [("rust:src/lib.rs:1-1:helper:function", "import")]


def test_a_bare_call_through_two_in_file_wildcards_that_both_supply_it_is_a_tie(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {"src/lib.rs": (
        "mod a { pub fn helper() {} }\n"
        "mod b { pub fn helper() {} }\n"
        "mod c {\n    use super::a::*;\n    use super::b::*;\n    use super::*;\n"
        "    pub fn t() { helper(); }\n}\n"
    )}, "t")
    assert _stub(edges) == {"callee_name": "helper"}


def test_an_in_file_wildcard_is_followed_through_the_module_it_opens(tmp_path: Path) -> None:
    """``use super::*`` in ``tests`` opens the root, whose ``use self::inner::*``
    opens ``inner``: Rust's glob imports are transitive."""
    edges = _calls(tmp_path, {"src/lib.rs": (
        "mod inner { pub fn deep() {} }\n"
        "use self::inner::*;\n"
        "mod tests {\n    use super::*;\n    use super::*;\n    pub fn t() { deep(); }\n}\n"
    )}, "t")
    assert _bound(edges) == [("rust:src/lib.rs:1-1:deep:function", "import")]


def test_an_imported_function_is_reported_by_the_name_written_here(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\n",
        "src/db.rs": "pub fn connect() {}\n",
        "src/user.rs": "use crate::db::connect as open;\npub fn run() { open(); }\n",
    }
    stub = next(e for e in _calls(tmp_path, files, "run") if not e.is_resolved)
    # The table maps ``open``; the stub names no library module.
    assert stub.dst == "rust:external:0-0:open:unresolved"
    assert _stub(_calls(tmp_path, files, "run")) == {"callee_name": "open"}


def test_a_spawned_task_is_its_own_modules_function_or_the_imports(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod jobs;\npub mod srv;\n",
        "src/jobs.rs": "pub async fn far() {}\n",
        "src/srv.rs": (
            "use crate::jobs::far;\n"
            "async fn near() {}\n"
            "pub async fn run() { tokio::spawn(near); tokio::spawn(far); tokio::spawn(unknown); }\n"
        ),
    }
    edges = _calls(tmp_path, files, "run")
    assert _bound(edges) == [("rust:src/srv.rs:2-2:near:function", "same_module")]
    # ``far`` is the import table's; ``unknown`` is nobody's and gets no edge.
    assert [e.meta["callee_name"] for e in edges if not e.is_resolved] == ["far"]


# --------------------------------------------------------------- path calls

def test_a_path_call_to_an_impl_in_this_file_and_through_self(tmp_path: Path) -> None:
    files = {"src/lib.rs": _DB + "pub fn run() { Conn::new(); }\nimpl Conn {\n    fn again() { Self::new(); }\n}\n"}
    assert _bound(_calls(tmp_path, files, "run")) == [("rust:src/lib.rs:3-3:Conn..new:method", "qualified")]
    assert _bound(_calls(tmp_path, files, "Conn::again")) == [("rust:src/lib.rs:3-3:Conn..new:method", "own_type")]


def test_a_rooted_path_is_followed_to_the_file_that_declares_the_item(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\npub use db::Conn;\n",
        "src/db.rs": _DB + "pub fn connect() {}\npub mod pool { pub fn take() {} }\n",
        "src/user.rs": (
            "pub fn run() {\n    crate::db::connect();\n    crate::db::Conn::new();\n"
            "    crate::db::pool::take();\n    crate::Conn::new();\n}\n"
            "mod inner {\n    fn f() { super::super::db::connect(); super::run(); }\n}\n"
        ),
    }
    edges = _calls(tmp_path, files, "run")
    assert sorted(_bound(edges)) == [
        ("rust:src/db.rs:3-3:Conn..new:method", "qualified"),
        ("rust:src/db.rs:6-6:connect:function", "qualified"),
        ("rust:src/db.rs:7-7:take:function", "qualified"),
    ]
    # ``crate::Conn`` is a re-export: lib.rs does not declare it. Reported.
    assert [e.meta["qualifier"] for e in edges if not e.is_resolved] == ["crate::Conn"]
    inner = _calls(tmp_path, files, "f")
    assert sorted(_bound(inner)) == [
        ("rust:src/db.rs:6-6:connect:function", "qualified"),
        ("rust:src/user.rs:1-6:run:function", "qualified"),
    ]


def test_a_path_through_a_use_or_mod_binding_is_reported(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\n",
        "src/db.rs": _DB + "pub fn connect() {}\n",
        "src/user.rs": (
            "use crate::db;\nuse crate::db::Conn;\n"
            "pub fn run() { db::connect(); }\n"
            "pub fn make() { Conn::new(); }\n"
        ),
    }
    assert _stub(_calls(tmp_path, files, "run")) == {"callee_name": "connect", "qualifier": "db"}
    assert _stub(_calls(tmp_path, files, "make")) == {"callee_name": "new", "qualifier": "Conn"}


def test_a_path_through_a_mod_declaration_is_reported_by_the_callees_own_name(tmp_path: Path) -> None:
    files = {"src/lib.rs": "mod util;\npub fn run() { util::helper(); }\n", "src/util.rs": "pub fn helper() {}\n"}
    assert _stub(_calls(tmp_path, files, "run")) == {"callee_name": "helper", "qualifier": "util"}


def test_a_generic_named_path_call_is_reported_because_it_names_its_owner(tmp_path: Path) -> None:
    """``clone`` is a name an untyped call is silenced on; ``Thing::clone(x)``
    names its owner, so it is reported for the shared pass."""
    files = {"src/lib.rs": "pub fn run(x: u8) { Thing::clone(&x); }\n"}
    assert _stub(_calls(tmp_path, files, "run")) == {"callee_name": "Thing::clone", "qualifier": "Thing"}


# --------------------------------------------------------------- method calls

def test_a_typed_receiver_binds_its_types_method_in_this_file(tmp_path: Path) -> None:
    files = {"src/lib.rs": _DB + "pub fn run(c: Conn) { c.execute(); }\n"}
    edges = _calls(tmp_path, files, "run")
    assert _bound(edges) == [("rust:src/lib.rs:4-4:Conn..execute:method", "receiver_type")]


def test_a_typed_receiver_whose_type_is_elsewhere_reports_the_type(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\n",
        "src/db.rs": _DB,
        "src/user.rs": (
            "use crate::db::Conn;\n"
            "pub fn run(c: Conn) { c.execute(); }\n"
            "pub fn chain() { Conn::new().execute(); }\n"
        ),
    }
    assert _stub(_calls(tmp_path, files, "run")) == {
        "callee_name": "execute", "receiver_type_hint": "Conn", "call_construct": "method"}
    chain = [e for e in _calls(tmp_path, files, "chain") if (e.meta or {}).get("callee_name") == "execute"]
    assert _stub(chain)["receiver_type_hint"] == "Conn"


def test_a_type_written_as_a_path_is_not_this_files_namesake(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\n",
        "src/db.rs": _DB,
        "src/user.rs": _DB + "pub fn run(c: crate::db::Conn) { c.execute(); }\n",
    }
    assert _stub(_calls(tmp_path, files, "run"))["receiver_type_hint"] == "crate::db::Conn"


def test_self_reports_the_impls_type_when_the_method_is_elsewhere(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub mod db;\npub mod more;\n",
        "src/db.rs": _DB,
        "src/more.rs": "use crate::db::Conn;\nimpl Conn {\n    pub fn twice(&self) { self.execute(); }\n}\n",
    }
    assert _stub(_calls(tmp_path, files, "Conn::twice")) == {
        "callee_name": "execute", "receiver_type_hint": "Conn", "call_construct": "method"}


def test_a_variable_rooted_field_chain_reports_the_fields_type(tmp_path: Path) -> None:
    files = {"src/lib.rs": (
        "pub mod db;\nuse crate::db::Conn;\n"
        "pub struct Holder { conn: Conn }\n"
        "pub fn run(h: Holder) { h.conn.execute(); }\n"
    ), "src/db.rs": _DB}
    assert _stub(_calls(tmp_path, files, "run"))["receiver_type_hint"] == "Conn"


def test_an_untyped_receiver_reports_nothing_and_a_generic_name_on_it_is_silent(tmp_path: Path) -> None:
    files = {"src/lib.rs": _DB + "pub fn run(x: u8) { x.execute(); x.clone(); }\n"}
    assert _stub(_calls(tmp_path, files, "run")) == {"callee_name": "execute", "call_construct": "method"}


def test_a_generic_name_on_a_receiver_of_a_tree_type_is_reported(tmp_path: Path) -> None:
    """``c.clone()`` with ``c: Conn``: the type is the tree's, so the shared
    pass may find ``Conn``'s ``clone``; ``v.clone()`` with ``v: Vec<u8>`` is
    the prelude's and stays silent."""
    files = {
        "src/lib.rs": "pub mod db;\npub mod user;\n",
        "src/db.rs": _DB,
        "src/user.rs": "use crate::db::Conn;\npub fn run(c: Conn, v: Vec<u8>) { c.clone(); v.clone(); }\n",
    }
    assert _stub(_calls(tmp_path, files, "run"))["receiver_type_hint"] == "Conn"


def test_the_one_trait_rule_still_binds_a_type_parameters_call(tmp_path: Path) -> None:
    """``backend: T`` with ``T: Backend``: every ``fd`` of the tree is the
    trait's, and no declaration of the tree is named ``T``."""
    files = {"src/lib.rs": (
        "pub trait Backend {\n    fn fd(&self) -> i32;\n}\n"
        "pub struct A;\nimpl Backend for A {\n    fn fd(&self) -> i32 { 1 }\n}\n"
        "pub struct B;\nimpl Backend for B {\n    fn fd(&self) -> i32 { 2 }\n}\n"
        "pub struct H<T: Backend> { backend: T }\n"
        "impl<T: Backend> H<T> {\n    pub fn get(&self) -> i32 { self.backend.fd() }\n}\n"
    )}
    assert _bound(_calls(tmp_path, files, "H::get")) == [("rust:src/lib.rs:2-2:Backend..fd:method", "name")]


def test_a_type_that_is_provably_a_librarys() -> None:
    assert _rust_type_is_external("std::collections::VecDeque<u8>", {}, None)
    assert _rust_type_is_external("Vec<u8>", {}, None)
    assert _rust_type_is_external("Map", {"Map": "serde_json::Map"}, None)
    assert not _rust_type_is_external("crate::db::Conn", {}, None)
    assert not _rust_type_is_external("Conn", {}, None)


# --------------------------------------------------------------- end to end

_SHAPES = {
    "src/lib.rs": "pub mod db;\npub mod legacy;\npub mod util;\npub mod app;\n",
    "src/db.rs": _DB + "pub fn get_db() -> Conn { Conn::new() }\n",
    "src/legacy.rs": _DB + "pub fn get_db() -> Conn { Conn::new() }\n",
    "src/util.rs": "pub fn helper() -> i32 { 1 }\n",
    "src/app.rs": (
        "use crate::db::Conn;\nuse crate::db::get_db;\nuse crate::util;\n\n"
        "pub fn helper() -> i32 { 2 }\n\n"
        "pub fn via_param(c: Conn) { c.execute(); }\n"
        "pub fn via_import() { let db = get_db(); db.execute(); }\n"
        "pub fn via_namespace() -> i32 { util::helper() }\n"
        "pub fn via_untyped(x: u8) { x.execute(); }\n"
    ),
}


def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str) -> list[tuple[str, str]]:
    """(target id, mark) of the live resolved calls of the function named ``caller``."""
    (src,) = [n["id"] for n in data["nodes"] if n.get("name") == caller and n["path"] == "src/app.rs"]
    return sorted(
        (e["dst"], e["meta"]["resolved_by"]) for e in data["edges"]
        if e["type"] == "calls" and e["src"] == src and e.get("is_resolved")
        and not (e.get("meta") or {}).get("superseded_by")
    )


def test_the_shared_pass_binds_what_the_imports_name_and_nothing_else(tmp_path: Path) -> None:
    """A typed receiver whose type is imported from db.rs with a namesake in
    legacy.rs; a function imported by name with a namesake in legacy.rs (whose
    return type types the receiver); a call through a namespace binding with
    a namesake in the caller's own file; an untyped receiver."""
    data = _map(tmp_path, _SHAPES)
    assert _resolved(data, "via_param") == [("rust:src/db.rs:4-4:Conn..execute:method", "receiver_type")]
    assert _resolved(data, "via_import") == [
        ("rust:src/db.rs:4-4:Conn..execute:method", "receiver_type"),
        ("rust:src/db.rs:6-6:get_db:function", "import"),
    ]
    assert _resolved(data, "via_namespace") == [("rust:src/util.rs:1-1:helper:function", "import")]
    assert _resolved(data, "via_untyped") == []
