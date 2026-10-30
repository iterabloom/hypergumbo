# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust function VALUE names its function (META-mino).

rust.py recorded only calls (plus ``tokio::spawn(task)``), so a function used
as a value had no incoming edge at all and looked dead: a struct field
(``Cmd { run: handler }``), a binding (``let f = handler;``, ``static F: fn() =
handler;``), an array element (``[a, b]``, a dispatch table), a tuple element,
an assignment and a call argument (``xs.iter().map(parse)``).

Each is now a ``references`` edge, ``evidence_type="function_reference"``. A
bare name resolves the way Rust scopes one -- a function of this file, or one
a ``use`` brings in, never a same-named function elsewhere in the repo -- and a
name bound locally (a ``let``, a parameter, a closure parameter, a pattern) is
that local value. ``Self::f`` / ``Type::f`` / ``module::f`` resolve by their
full path.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.rust import analyze_rust


def _refs(root: Path, files: dict[str, str]) -> list[tuple[str, str]]:
    """(src name, dst name) of every function_reference edge."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    result = analyze_rust(root)
    by_id = {s.id: s for s in result.symbols}
    return sorted(
        (by_id[e.src].name if e.src in by_id else "<file>", by_id[e.dst].name)
        for e in result.edges
        if e.edge_type == "references" and e.evidence_type == "function_reference"
        and e.dst in by_id
    )


_TARGETS = "fn a1() -> i32 { 1 }\nfn a2() -> i32 { 2 }\n"


def test_every_value_position_names_its_function(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/main.rs": _TARGETS + (
        "struct Cmd { run: fn() -> i32 }\n"
        "fn take(f: fn() -> i32) -> i32 { f() }\n"
        "pub fn main() {\n"
        "    let c = Cmd { run: a1 };\n"
        "    let f = a2;\n"
        "    let t = [a1, a2];\n"
        "    let u = (a1, 0);\n"
        "    let mut h: fn() -> i32 = a1;\n"
        "    h = a2;\n"
        "    take(a1);\n"
        "    let _ = (c.run, f, t, u, h);\n"
        "}\n"
    )})
    assert refs == sorted([("main", "a1")] * 5 + [("main", "a2")] * 3)


def test_a_static_initializer_is_anchored_on_the_file(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": _TARGETS + "pub static F: fn() -> i32 = a1;\n"})
    assert [dst for _src, dst in refs] == ["a1"]


def test_an_iterator_adapter_argument_names_its_function(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": (
        "fn parse(s: &str) -> i32 { s.len() as i32 }\n"
        "pub fn all(xs: &[&str]) -> Vec<i32> { xs.iter().copied().map(parse).collect() }\n"
    )})
    assert refs == [("all", "parse")]


def test_a_used_function_resolves_and_an_unimported_one_does_not(tmp_path: Path) -> None:
    """A bare name names a function of this module or one ``use`` imports; a
    same-named function in another module that nothing imports is not it."""
    refs = _refs(tmp_path, {
        "src/lib.rs": "mod a;\nmod b;\nmod c;\n",
        "src/a.rs": "pub fn handler() -> i32 { 1 }\n",
        "src/b.rs": (
            "use crate::a::handler;\n"
            "pub fn wire() -> fn() -> i32 { let f = handler; f }\n"
        ),
        "src/c.rs": "pub fn other() -> fn() -> i32 { let f = handler; f }\n",
    })
    assert refs == [("wire", "handler")]


def test_a_qualified_path_resolves_by_its_path(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": (
        "pub struct S;\n"
        "impl S {\n"
        "    fn make() -> i32 { 1 }\n"
        "    pub fn table() -> [fn() -> i32; 1] { [Self::make] }\n"
        "}\n"
        "pub fn outside() -> fn() -> i32 { S::make }\n"
    )})
    assert refs == [("S::table", "S::make"), ("outside", "S::make")]


def test_a_returned_function_names_it(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": _TARGETS + (
        "pub fn pick(x: bool) -> fn() -> i32 { if x { return a1; } a2 }\n"
    )})
    assert refs == [("pick", "a1"), ("pick", "a2")]


def test_a_local_name_is_not_the_function(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": _TARGETS + (
        "pub fn p(a1: fn() -> i32) -> i32 { let f = a1; f() }\n"
        "pub fn q() -> i32 { let a2 = || 3; let t = [a2]; t[0]() }\n"
        "pub fn r(xs: &[i32]) -> i32 { xs.iter().map(|a1| a1 + 1).sum() }\n"
    )})
    assert refs == []


def test_values_that_are_not_functions_name_nothing(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": _TARGETS + (
        "const LIMIT: i32 = 3;\n"
        "pub struct W(i32);\n"
        "pub fn v() -> i32 { let n = LIMIT; let w = W(n); let r = a1(); let o = Some(r); w.0 + o.unwrap() }\n"
    )})
    assert refs == []


def test_a_closure_parameter_shadows_the_function(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": _TARGETS + (
        "fn take(x: i32) -> i32 { x }\n"
        "pub fn s(xs: &[i32]) -> i32 { xs.iter().map(|a1| take(a1)).sum::<i32>() }\n"
    )})
    assert refs == []


def test_an_imported_name_with_no_repo_function_names_nothing(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {
        "src/lib.rs": "mod util;\nuse std::cmp::max;\npub fn m() -> fn(i32, i32) -> i32 { let f = max; f }\n",
        "src/util.rs": "pub fn max(a: i32, b: i32) -> i32 { a + b }\n",
    })
    assert refs == []


def test_self_outside_an_impl_names_nothing(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {"src/lib.rs": (
        "pub trait T {\n"
        "    fn g() -> i32;\n"
        "    fn t() -> fn() -> i32 { Self::g }\n"
        "}\n"
    )})
    assert refs == []


def test_a_module_path_to_another_module_s_function_is_not_this_one(
    tmp_path: Path,
) -> None:
    """``crate::b::handler`` is b's handler, not a's."""
    _refs(tmp_path, {
        "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
        "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
        "src/a/mod.rs": "pub fn handler() -> i32 { 1 }\n",
        "src/b/mod.rs": "pub fn handler() -> i32 { 2 }\n",
        "src/c.rs": "pub fn wire() -> fn() -> i32 { crate::b::handler }\n",
    })
    result = analyze_rust(tmp_path)
    dsts = [
        e.dst for e in result.edges
        if e.evidence_type == "function_reference"
    ]
    assert len(dsts) == 1 and "src/b/mod.rs" in dsts[0], dsts


def test_without_a_crate_layout_an_ambiguous_path_names_nothing(
    tmp_path: Path,
) -> None:
    """No Cargo.toml to say which file ``crate::b`` is: two ``handler``s and
    no way to choose, so no edge rather than the first by order."""
    refs = _refs(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
        "src/a/mod.rs": "pub fn handler() -> i32 { 1 }\n",
        "src/b/mod.rs": "pub fn handler() -> i32 { 2 }\n",
        "src/c.rs": "pub fn wire() -> fn() -> i32 { crate::b::handler }\n",
    })
    assert refs == []


def test_an_imported_name_resolves_to_the_module_its_use_names(tmp_path: Path) -> None:
    _refs(tmp_path, {
        "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
        "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
        "src/a.rs": "pub fn handler() -> i32 { 1 }\n",
        "src/b.rs": "pub fn handler() -> i32 { 2 }\n",
        "src/c.rs": "use crate::b::handler;\npub fn wire() -> fn() -> i32 { handler }\n",
    })
    result = analyze_rust(tmp_path)
    dsts = [e.dst for e in result.edges if e.evidence_type == "function_reference"]
    assert len(dsts) == 1 and "src/b.rs" in dsts[0], dsts


def test_a_module_path_into_another_file_resolves(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\n",
        "src/a.rs": "pub fn handler() -> i32 { 1 }\n",
        "src/b.rs": "pub fn wire() -> fn() -> i32 { crate::a::handler }\n",
    })
    assert refs == [("wire", "handler")]


def test_a_type_path_into_another_file_resolves(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {
        "src/lib.rs": "pub mod a;\npub mod b;\n",
        "src/a.rs": "pub struct S;\nimpl S {\n    pub fn make() -> i32 { 1 }\n}\n",
        "src/b.rs": "use crate::a::S;\npub fn wire() -> fn() -> i32 { S::make }\n",
    })
    assert refs == [("wire", "S::make")]


def test_a_type_path_shared_by_two_crates_is_the_caller_s_crate_s(tmp_path: Path) -> None:
    """ripgrep: crates/regex and crates/pcre2 each have ``Error::regex``;
    ``.map_err(Error::regex)`` in crates/regex is its own."""
    err = "pub struct Error;\nimpl Error {\n    pub fn regex(e: i32) -> Error {{ let _ = e; Error }}\n}\n"
    files = {}
    for crate in ("regex", "pcre2"):
        files[f"crates/{crate}/Cargo.toml"] = f'[package]\nname = "{crate}"\nversion = "0.1.0"\n'
        files[f"crates/{crate}/src/lib.rs"] = "pub mod error;\npub mod config;\n"
        files[f"crates/{crate}/src/error.rs"] = err.replace("{{", "{").replace("}}", "}")
        files[f"crates/{crate}/src/config.rs"] = (
            "use crate::error::Error;\n"
            "pub fn build(r: Result<(), i32>) -> Result<(), Error> { r.map_err(Error::regex) }\n"
        )
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    result = analyze_rust(tmp_path)
    pairs = sorted(
        (e.src.split(":")[1].split("/")[1], e.dst.split(":")[1].split("/")[1])
        for e in result.edges if e.evidence_type == "function_reference"
    )
    assert pairs == [("pcre2", "pcre2"), ("regex", "regex")], pairs


def test_an_external_type_s_path_names_no_repo_impl(tmp_path: Path) -> None:
    """``.map(String::from)`` on ``&str`` is std's impl; a repo
    ``impl From<Name> for String`` is not it (zellij)."""
    refs = _refs(tmp_path, {"src/lib.rs": (
        "pub struct Name(String);\n"
        "impl From<Name> for String {\n    fn from(n: Name) -> String { n.0 }\n}\n"
        "pub fn words(s: &str) -> Vec<String> { s.split(' ').map(String::from).collect() }\n"
    )})
    assert refs == []
