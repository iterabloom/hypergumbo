# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py
"""ADR-0036 Ruling 1: a Rust node id's name slot is the sanitized name.

A method, a struct field, an enum variant and a trait method signature are
named ``Type::member``. The analyzer folded ``::`` to one ``.`` in the slot
(``A.run``) while the shared sanitizer, which every other producer goes
through (C++ ``A::f`` included), folds each ``:`` to ``.`` (``A..run``).
The slot now comes from the name through ``make_symbol_id``'s sanitizer. The
stable id hashes the name and qualified name, never the id.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import sanitize_id_name_segment
from hypergumbo_lang_mainstream.rust import analyze_rust

_SRC = (
    "pub struct A { command: u8 }\n"
    "pub enum E { X, Y }\n"
    "pub trait T {\n    fn tick(&self);\n}\n"
    "impl A {\n"
    "    pub fn run(&self) -> u8 { self.helper() }\n"
    "    fn helper(&self) -> u8 { self.command }\n"
    "}\n"
    "pub fn free() -> u8 { 1 }\n"
)


def _result(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text(_SRC)
    return analyze_rust(tmp_path)


def test_the_fixture_emits_every_member_shape(tmp_path: Path) -> None:
    names = {(s.kind, s.name) for s in _result(tmp_path).symbols}
    assert {("method", "A::run"), ("method", "A::helper"), ("field", "A::command"),
            ("field", "E::X"), ("method", "T::tick"), ("function", "free")} <= names


def test_every_name_slot_is_the_sanitized_name(tmp_path: Path) -> None:
    wrong = [(s.id, s.name) for s in _result(tmp_path).symbols
             if s.kind != "file"
             and s.id.rsplit(":", 2)[1] != sanitize_id_name_segment(s.name)]
    assert wrong == []


def test_the_call_between_methods_ends_on_the_method_id(tmp_path: Path) -> None:
    """Control: an edge built from the symbol keeps landing on it."""
    result = _result(tmp_path)
    ids = {s.name: s.id for s in result.symbols}
    calls = {(e.src, e.dst) for e in result.edges if e.edge_type == "calls"}
    assert (ids["A::run"], ids["A::helper"]) in calls
