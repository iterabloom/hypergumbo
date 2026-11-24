# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust method call is not bound to one of several same-named methods by declaration order (INV-hupis).

Two types of one file both declare ``execute``. The analyzer wrote the short
method name into a per-file table, last writer wins, and consulted that table
before the list resolver, so every call whose receiver it could not type was
bound to whichever ``impl`` came last, at 0.85. Three things made a receiver
untyped that the source types plainly:

* ``let db = get_db();`` -- a plain function's declared return type was not
  read (only ``Type::f()`` and ``recv.m()`` were);
* ``get_db().execute()`` -- the same, for a chain rooted at a function call;
* ``use util::helper; helper()`` -- a ``use`` path that starts at a child
  module, written without ``crate::`` or ``self::``, resolved to no file, so
  the call fell back to the bare name and the last file that declares it.

The rule, the one ``symbol_resolution`` states for every analyzer: a call is
bound when something in the source says which declaration it reaches; a pick
among several is unresolved. Each test runs both declaration orders, because
a single order cannot tell a typed resolution from a lucky pick, and asserts
first that both candidates have symbols.

Not every pair of same-named methods is a tie. A trait's method and its
implementations are ONE method, declared once: ``recv.fd()`` on a receiver
nothing types binds the trait's declaration, provided every ``fd`` in the
tree belongs to that trait (another type's ``fd`` anywhere, and the receiver
could be that type). A path call (``<X as Other>::fd(..)``) names its owner
in its own text and is not bound through this file's trait. Two ``impl``
blocks of one type that both declare the method (``#[cfg]`` alternatives)
are one method too, and keep the name lookup.

Kept, and stated by a test each: with ONE method of the name in the file an
untyped receiver still binds it by name.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

_CARGO = '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n'


def _impl(name: str, method: str, value: int) -> str:
    return (f"pub struct {name};\n\nimpl {name} {{\n    pub fn new() -> Self {{ {name} }}\n"
            f"    pub fn {method}(&self) -> i32 {{ {value} }}\n}}\n\n")


def _two(method: str, right: str, decoy: str, decoy_last: bool) -> str:
    """Both impls; the right one returns 1, the decoy 2."""
    a, b = _impl(right, method, 1), _impl(decoy, method, 2)
    return a + b if decoy_last else b + a


def _survey(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in {"Cargo.toml": _CARGO, **files}.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False, progress=False)
    return json.loads(out.read_text())


def _returns(repo: Path, node: dict) -> str:
    """The digit the declaration returns: "1" for the right one, "2" for the decoy."""
    lines = (repo / node["path"]).read_text().splitlines()
    text = " ".join(lines[node["span"]["start_line"] - 1: node["span"]["end_line"]])
    return text.split("{")[-1].split("}")[0].strip()


def _reached(data: dict, repo: Path, caller: str, method: str) -> list[str]:
    """What ``caller``'s calls to ``method`` reach: "1", "2", or "unresolved"."""
    nodes = {n["id"]: n for n in data["nodes"]}
    declared = [n for n in data["nodes"] if n["kind"] in ("function", "method")
                and n["name"].split("::")[-1] == method]
    assert sorted(_returns(repo, n) for n in declared) == ["1", "2"], "reach: both candidates need a symbol"
    (src,) = [n["id"] for n in data["nodes"] if n["name"] == caller]
    out = []
    for edge in data["edges"]:
        if edge["type"] != "calls" or edge["src"] != src or (edge.get("meta") or {}).get("superseded_by"):
            continue
        dst = nodes.get(edge["dst"])
        if dst is not None and dst in declared:
            out.append(_returns(repo, dst))
        elif edge["dst"].split(":")[-2].split("::")[-1] == method:
            out.append("unresolved")
    return out


ORDERS = pytest.mark.parametrize("decoy_last", [True, False], ids=["decoy_last", "decoy_first"])


@ORDERS
def test_a_variable_bound_from_a_function_takes_its_declared_return_type(tmp_path: Path, decoy_last: bool) -> None:
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub fn get_db() -> Conn {\n    Conn::new()\n}\n\n"
        "pub fn via_var() {\n    let db = get_db();\n    db.execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_var", "execute") == ["1"]


@ORDERS
def test_a_call_chained_on_a_function_takes_its_declared_return_type(tmp_path: Path, decoy_last: bool) -> None:
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub fn get_db() -> Conn {\n    Conn::new()\n}\n\n"
        "pub fn via_chain() {\n    get_db().execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_chain", "execute") == ["1"]


@ORDERS
def test_a_reference_or_wrapped_return_type_still_names_the_type(tmp_path: Path, decoy_last: bool) -> None:
    """``-> &Conn`` and ``-> Option<Conn>`` (unwrapped) are ``Conn`` for this purpose."""
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub fn borrowed(c: &Conn) -> &Conn {\n    c\n}\n\n"
        "pub fn maybe() -> Option<Conn> {\n    Some(Conn::new())\n}\n\n"
        "pub fn via_ref(c: &Other) {\n    let a = borrowed(c);\n    a.execute();\n}\n\n"
        "pub fn via_opt() {\n    let b = maybe().unwrap();\n    b.execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_ref", "execute") == ["1"]
    assert _reached(data, tmp_path, "via_opt", "execute") == ["1"]


@ORDERS
def test_an_untyped_receiver_is_not_bound_to_either_method(tmp_path: Path, decoy_last: bool) -> None:
    """``extlib`` is not in the tree: nothing says what ``x`` is."""
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub fn via_ext() {\n    let x = extlib::make();\n    x.execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_ext", "execute") == ["unresolved"]


@ORDERS
def test_a_function_of_unknown_return_type_does_not_type_the_receiver(tmp_path: Path, decoy_last: bool) -> None:
    """``make()`` returns ``impl Runner``: no concrete type, so no pick."""
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub trait Runner {}\nimpl Runner for Conn {}\n\n"
        "pub fn make() -> impl Runner {\n    Conn::new()\n}\n\n"
        "pub fn via_var() {\n    let x = make();\n    x.execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_var", "execute") == ["unresolved"]


@ORDERS
def test_a_use_path_through_a_child_module_names_its_function(tmp_path: Path, decoy_last: bool) -> None:
    decoy = "zdecoy" if decoy_last else "adecoy"
    data = _survey(tmp_path, {
        "src/lib.rs": f"mod {decoy};\nmod util;\n\nuse util::helper;\n\npub fn via_import() {{\n    helper();\n}}\n",
        "src/util.rs": "pub fn helper() -> i32 { 1 }\n",
        f"src/{decoy}.rs": "pub fn helper() -> i32 { 2 }\n",
    })
    assert _reached(data, tmp_path, "via_import", "helper") == ["1"]


def test_one_method_of_the_name_is_still_reached_by_an_untyped_receiver(tmp_path: Path) -> None:
    """Kept behaviour, stated: with a single candidate the short name still binds."""
    data = _survey(tmp_path, {"src/lib.rs": _impl("Conn", "execute", 1) + (
        "pub fn via_ext() {\n    let x = extlib::make();\n    x.execute();\n}\n"
    )})
    nodes = {n["id"]: n for n in data["nodes"]}
    (src,) = [n["id"] for n in data["nodes"] if n["name"] == "via_ext"]
    reached = [nodes[e["dst"]]["name"] for e in data["edges"]
               if e["type"] == "calls" and e["src"] == src and e["dst"] in nodes]
    assert "Conn::execute" in reached


def _dst_names(data: dict, caller: str) -> set[str]:
    nodes = {n["id"]: n for n in data["nodes"]}
    (src,) = [n["id"] for n in data["nodes"] if n["name"] == caller]
    return {nodes[e["dst"]]["name"] for e in data["edges"]
            if e["type"] == "calls" and e["src"] == src and e["dst"] in nodes
            and not (e.get("meta") or {}).get("superseded_by")}


_TRAIT = "pub trait Backend {\n    fn fd(&self) -> i32;\n}\n\n"
_IMPL_A = "pub struct A;\n\nimpl Backend for A {\n    fn fd(&self) -> i32 { 3 }\n}\n\n"
_IMPL_B = "pub struct B;\n\nimpl Backend for B {\n    fn fd(&self) -> i32 { 4 }\n}\n\n"
_GENERIC_CALLER = (
    "pub struct Holder<T: Backend> { backend: T }\n\n"
    "impl<T: Backend> Holder<T> {\n    pub fn get(&self) -> i32 {\n        self.backend.fd()\n    }\n}\n"
)


@pytest.mark.parametrize("impls", [_IMPL_A + _IMPL_B, _IMPL_B + _IMPL_A], ids=["a_then_b", "b_then_a"])
def test_a_trait_and_its_implementations_are_one_method(tmp_path: Path, impls: str) -> None:
    """An untyped receiver's ``fd()`` is the trait's ``fd``, whichever impl comes last."""
    data = _survey(tmp_path, {"src/lib.rs": _TRAIT + impls + _GENERIC_CALLER})
    assert "Backend::fd" in {n["name"] for n in data["nodes"]}, "reach"
    assert _dst_names(data, "Holder::get") >= {"Backend::fd"}
    assert not _dst_names(data, "Holder::get") & {"A::fd", "B::fd"}


@pytest.mark.parametrize("impls", [_IMPL_A + _IMPL_B, _IMPL_B + _IMPL_A], ids=["a_then_b", "b_then_a"])
def test_implementations_of_a_trait_declared_in_another_file_reach_that_trait(tmp_path: Path, impls: str) -> None:
    data = _survey(tmp_path, {
        "src/lib.rs": "mod backend;\nuse backend::Backend;\n\n" + impls + _GENERIC_CALLER,
        "src/backend.rs": _TRAIT,
    })
    assert _dst_names(data, "Holder::get") >= {"Backend::fd"}
    assert not _dst_names(data, "Holder::get") & {"A::fd", "B::fd"}


@pytest.mark.parametrize("impls", [_IMPL_A + _IMPL_B, _IMPL_B + _IMPL_A], ids=["a_then_b", "b_then_a"])
def test_implementations_of_a_trait_nothing_declares_are_not_picked_between(tmp_path: Path, impls: str) -> None:
    """``Backend`` comes from another crate: neither impl is preferred."""
    data = _survey(tmp_path, {
        "src/lib.rs": "use extlib::Backend;\n\n" + impls + _GENERIC_CALLER,
    })
    assert not _dst_names(data, "Holder::get") & {"A::fd", "B::fd"}


@ORDERS
def test_two_functions_of_one_name_in_a_file_type_nothing(tmp_path: Path, decoy_last: bool) -> None:
    """``a::get_db`` returns a Conn and ``b::get_db`` an Other: a bare ``get_db()`` is not typed by either."""
    data = _survey(tmp_path, {"src/lib.rs": _two("execute", "Conn", "Other", decoy_last) + (
        "pub mod a {\n    pub fn get_db() -> super::Conn {\n        super::Conn::new()\n    }\n}\n\n"
        "pub mod b {\n    pub fn get_db() -> super::Other {\n        super::Other::new()\n    }\n}\n\n"
        "use a::get_db;\n\n"
        "pub fn via_var() {\n    let db = get_db();\n    db.execute();\n}\n"
    )})
    assert _reached(data, tmp_path, "via_var", "execute") == ["unresolved"]


def test_without_a_lookup_a_plain_call_types_nothing() -> None:
    """The let-binding walker alone (no analyzer, so no function lookup) leaves ``db`` untyped."""
    import tree_sitter
    import tree_sitter_rust

    from hypergumbo_lang_mainstream.rust import _extract_var_types_rust

    source = b"fn get_db() -> Conn { Conn }\nfn f() { let db = get_db(); let c = Conn::new(); }\n"
    tree = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language())).parse(source)
    assert _extract_var_types_rust(tree.root_node, source, {}) == {"c": "Conn"}
    typed = _extract_var_types_rust(tree.root_node, source, {}, lambda name: {"get_db": "Conn"}.get(name))
    assert typed == {"db": "Conn", "c": "Conn"}


@pytest.mark.parametrize("impls", [_IMPL_A + _IMPL_B, _IMPL_B + _IMPL_A], ids=["a_then_b", "b_then_a"])
def test_a_same_named_method_on_an_unrelated_type_elsewhere_stops_the_trait_answer(tmp_path: Path, impls: str) -> None:
    """ripgrep's ``args.mode.update(..)`` in a file of ``impl Flag for X``: the receiver may be
    the other type, so nothing is bound, not the trait's method and not an implementation."""
    data = _survey(tmp_path, {
        "src/lib.rs": "mod mode;\n\n" + _TRAIT + impls + _GENERIC_CALLER,
        "src/mode.rs": "pub struct Mode;\n\nimpl Mode {\n    pub fn fd(&self) -> i32 { 9 }\n}\n",
    })
    assert "Mode::fd" in {n["name"] for n in data["nodes"]}, "reach"
    assert not _dst_names(data, "Holder::get") & {"Backend::fd", "A::fd", "B::fd", "Mode::fd"}


def test_a_use_path_that_names_no_child_module_resolves_to_no_file(tmp_path: Path) -> None:
    """``use serde::helper``: ``serde`` is no child module here, so the import stays external."""
    data = _survey(tmp_path, {
        "src/lib.rs": "mod util;\n\nuse serde::helper;\n\npub fn via_import() {\n    helper();\n}\n",
        "src/util.rs": "pub fn helper() -> i32 { 1 }\n",
    })
    imports = {e["dst"] for e in data["edges"] if e["type"] == "imports" and e["src"].startswith("rust:src/lib.rs")}
    assert not any("src/util.rs" in dst for dst in imports), imports
    assert any("serde" in dst for dst in imports), imports


def test_a_use_path_through_a_child_module_imports_its_file(tmp_path: Path) -> None:
    data = _survey(tmp_path, {
        "src/lib.rs": "mod util;\n\nuse util::helper;\n\npub fn via_import() {\n    helper();\n}\n",
        "src/util.rs": "pub fn helper() -> i32 { 1 }\n",
    })
    imports = {e["dst"] for e in data["edges"] if e["type"] == "imports" and e["src"].startswith("rust:src/lib.rs")}
    assert any("src/util.rs" in dst for dst in imports), imports


def test_implementations_in_other_files_are_still_the_one_trait_method(tmp_path: Path) -> None:
    """``impl Backend for C`` in another file is the same method, not an unrelated ``fd``."""
    data = _survey(tmp_path, {
        "src/lib.rs": "mod other;\n\n" + _TRAIT + _IMPL_A + _GENERIC_CALLER,
        "src/other.rs": "use crate::Backend;\n\npub struct C;\n\nimpl Backend for C {\n    fn fd(&self) -> i32 { 5 }\n}\n",
    })
    assert "C::fd" in {n["name"] for n in data["nodes"]}, "reach"
    assert _dst_names(data, "Holder::get") >= {"Backend::fd"}
    assert not _dst_names(data, "Holder::get") & {"A::fd", "C::fd"}


def test_two_impl_blocks_of_one_type_are_one_method(tmp_path: Path) -> None:
    """``#[cfg]`` alternatives of ``Conn::execute``: one type's method, still reached by name
    (kept behaviour: exactly one METHOD of the name in the caller's file)."""
    data = _survey(tmp_path, {"src/lib.rs": (
        "pub struct Conn;\n\n"
        "#[cfg(unix)]\nimpl Conn {\n    pub fn execute(&self) -> i32 { 1 }\n}\n\n"
        "#[cfg(windows)]\nimpl Conn {\n    pub fn execute(&self) -> i32 { 1 }\n}\n\n"
        "pub fn via_ext() {\n    let x = extlib::make();\n    x.execute();\n}\n"
    )})
    assert _dst_names(data, "via_ext") & {"Conn::execute"}


def test_a_path_call_that_names_another_trait_does_not_bind_this_files_trait(tmp_path: Path) -> None:
    """firecracker: ``<Frontend as VhostUserFrontend>::get_features(self)`` inside this file's own
    trait of the same method name calls the OTHER trait, which is not in the tree."""
    data = _survey(tmp_path, {"src/lib.rs": (
        "use vhost::VhostUserFrontend;\n\n"
        "pub trait Handle {\n    fn get_features(&self) -> u64;\n}\n\n"
        "pub struct Frontend;\n\n"
        "impl Handle for Frontend {\n    fn get_features(&self) -> u64 {\n"
        "        <Frontend as VhostUserFrontend>::get_features(self)\n    }\n}\n"
    )})
    assert not _dst_names(data, "Frontend::get_features") & {"Handle::get_features", "Frontend::get_features"}


def test_who_declares_a_method() -> None:
    """A trait's method and its implementations share an owner; an inherent method has its type's."""
    import tree_sitter
    import tree_sitter_rust

    from hypergumbo_lang_mainstream.rust import _rust_declaring_owner

    source = (
        b"fn free() {}\n"
        b"struct X;\n"
        b"impl X { fn own(&self) {} }\n"
        b"trait T { fn t(&self); fn d(&self) {} }\n"
        b"impl T for X { fn t(&self) {} }\n"
        b"impl<A> crate::m::G<A> for Vec<A> { fn g(&self) {} }\n"
    )
    tree = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language())).parse(source)
    owners = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type in ("function_item", "function_signature_item"):
            name = node.child_by_field_name("name").text.decode()
            owners.append((name, _rust_declaring_owner(node, source)))
    assert owners == [
        ("free", None),
        ("own", ("type", "X")),
        ("t", ("trait", "T")),
        ("d", ("trait", "T")),
        ("t", ("trait", "T")),
        ("g", ("trait", "G")),
    ]
