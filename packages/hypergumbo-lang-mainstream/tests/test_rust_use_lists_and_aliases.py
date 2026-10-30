# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust brace-list or aliased ``use`` imports, and a call through its alias resolves (WI-silam).

rust.py's ``use_declaration`` branch took the imported path from the first
child of type ``scoped_identifier`` / ``identifier`` / ``use_wildcard`` /
``use_list``. A brace list (``use crate::m::{a, b};``, a ``scoped_use_list``)
and an alias (``use m::helper as h;``, a ``use_as_clause``) matched none, so
the declaration emitted NO imports edge at all -- among the most common use
forms, so a file's import set was silently incomplete.

And a call through an alias (``h2()`` after ``use crate_b::{Thing, helper as
h2};``) looked up a symbol NAMED ``h2``, found none, and minted
``rust:crate_b:0-0:helper`` -- a workspace-prefixed stand-in (INV-nuzas),
although ``crate_b::helper`` is right there. The alias map already knew
``h2 -> crate_b::helper``; the lookup now asks for ``helper``, the way the
plain ``use crate_b::helper; helper()`` already does.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.rust import analyze_rust


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _edges(root: Path, src_file: str, edge_type: str) -> list[tuple[int, str]]:
    """(line, dst) of ``src_file``'s edges (ids are repo-relative)."""
    result = analyze_rust(root)
    return sorted(
        (e.line or 0, e.dst) for e in result.edges
        if e.edge_type == edge_type and e.src.startswith(f"rust:{src_file}:")
    )


_ONE_CRATE = {
    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
    "src/lib.rs": "pub mod m;\npub mod n;\npub mod user;\n",
    "src/m.rs": (
        "pub fn helper() -> i32 { 1 }\npub fn third() -> i32 { 3 }\n"
        "pub fn other() -> i32 { 2 }\n"
    ),
    "src/n/mod.rs": "pub mod deep;\npub fn top() -> i32 { 4 }\n",
    "src/n/deep.rs": "pub fn d() -> i32 { 5 }\n",
}


class TestTheImportsEdge:
    def test_a_brace_list_imports_its_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use crate::m::{helper, other};\npub fn u() -> i32 { helper() + other() }\n"
        )})
        assert (1, "rust:src/m.rs:1-1:file:file") in _edges(tmp_path, "src/user.rs", "imports")

    def test_an_alias_imports_its_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use crate::m::helper as h3;\npub fn u() -> i32 { h3() }\n"
        )})
        assert (1, "rust:src/m.rs:1-1:file:file") in _edges(tmp_path, "src/user.rs", "imports")

    def test_a_nested_list_imports_each_module_once(self, tmp_path: Path) -> None:
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use crate::{m::{helper, third}, n::{self, deep::d}};\n"
            "pub fn u() -> i32 { helper() + third() + n::top() + d() }\n"
        )})
        imports = [d for line, d in _edges(tmp_path, "src/user.rs", "imports") if line == 1]
        assert sorted(imports) == [
            "rust:src/m.rs:1-1:file:file",
            "rust:src/n/deep.rs:1-1:file:file",
            "rust:src/n/mod.rs:1-1:file:file",
        ], imports

    def test_a_top_level_brace_list_imports_each_path(self, tmp_path: Path) -> None:
        """just: ``use { super::*, clap::{Arg, Command}, crate::m::helper };``
        was one stand-in whose module slot was the whole brace text."""
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use {\n  crate::m::helper,\n  std::fs,\n};\npub fn u() -> i32 { helper() }\n"
        )})
        imports = [d for line, d in _edges(tmp_path, "src/user.rs", "imports") if line == 1]
        assert sorted(imports) == [
            "rust:src/m.rs:1-1:file:file", "rust:std::fs:0-0:module:module",
        ], imports

    def test_a_std_brace_list_keeps_its_external_stand_ins(self, tmp_path: Path) -> None:
        """Control: a path outside the tree is the module stand-in it always
        was for a single path, one per path."""
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use std::{fs, io::Write};\npub fn u() -> i32 { let _ = fs::metadata(\".\"); 0 }\n"
        )})
        imports = [d for line, d in _edges(tmp_path, "src/user.rs", "imports") if line == 1]
        assert sorted(imports) == [
            "rust:std::fs:0-0:module:module", "rust:std::io::Write:0-0:module:module",
        ], imports


class TestACallThroughAnAlias:
    def test_in_one_crate(self, tmp_path: Path) -> None:
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use crate::m::helper as h3;\nuse crate::m::{third as t4};\n"
            "pub fn u() -> i32 { h3() + t4() }\n"
        )})
        calls = [d for _, d in _edges(tmp_path, "src/user.rs", "calls")]
        assert "rust:src/m.rs:1-1:helper:function" in calls, calls
        assert "rust:src/m.rs:2-2:third:function" in calls, calls
        assert not any(d.endswith(":unresolved") for d in calls), calls

    def test_across_workspace_members(self, tmp_path: Path) -> None:
        """The filed INV-nuzas fixture's shape."""
        _write(tmp_path, {
            "Cargo.toml": '[workspace]\nmembers = ["crate-a", "crate-b"]\n',
            "crate-a/Cargo.toml": (
                '[package]\nname = "crate-a"\nversion = "0.1.0"\n\n'
                '[dependencies]\ncrate-b = { path = "../crate-b" }\n'
            ),
            "crate-a/src/lib.rs": (
                "use crate_b::{Thing, helper as h2};\n"
                "pub fn run4() -> i32 { Thing::new().get() + h2() }\n"
            ),
            "crate-b/Cargo.toml": '[package]\nname = "crate-b"\nversion = "0.1.0"\n',
            "crate-b/src/lib.rs": (
                "pub fn helper() -> i32 { 1 }\npub struct Thing { pub v: i32 }\n"
                "impl Thing { pub fn new() -> Thing { Thing { v: 1 } } pub fn get(&self) -> i32 { self.v } }\n"
            ),
        })
        calls = [d for _, d in _edges(tmp_path, "crate-a/src/lib.rs", "calls")]
        assert "rust:crate-b/src/lib.rs:1-1:helper:function" in calls, calls
        assert not any(d.startswith("rust:crate_b:") for d in calls), calls
        imports = [d for _, d in _edges(tmp_path, "crate-a/src/lib.rs", "imports")]
        assert "rust:crate-b/src/lib.rs:1-1:file:file" in imports, imports

    def test_an_alias_to_an_external_function_stays_external(self, tmp_path: Path) -> None:
        """Control: ``use std::cmp::max as m; m(1, 2)`` is std's, named ``max``."""
        _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
            "use std::cmp::max as mx;\npub fn u() -> i32 { mx(1, 2) }\n"
        )})
        calls = [d for _, d in _edges(tmp_path, "src/user.rs", "calls")]
        assert not any(d.startswith("rust:src/") for d in calls), calls


def test_a_local_wrapper_named_like_the_alias_target_is_not_it(tmp_path: Path) -> None:
    """zellij: ``use zellij_utils::sessions::generate_random_name as
    generate_random_name_impl;`` inside a file whose own ``fn
    generate_random_name`` wraps it. The aliased call is the module's
    function, not the wrapper (which would read as calling itself)."""
    _write(tmp_path, {**_ONE_CRATE, "src/user.rs": (
        "use crate::m::helper as helper_impl;\n"
        "fn helper() -> i32 { helper_impl() }\n"
        "pub fn u() -> i32 { helper() }\n"
    )})
    by_src: dict[str, list[str]] = {}
    for e in analyze_rust(tmp_path).edges:
        if e.edge_type == "calls" and e.src.startswith("rust:src/user.rs:"):
            by_src.setdefault(e.src.split(":")[-2], []).append(e.dst)
    assert by_src["helper"] == ["rust:src/m.rs:1-1:helper:function"], by_src
