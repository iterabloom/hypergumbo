# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py
"""ADR-0036 Ruling 1: a Perl sub's id name slot is its sanitized name.

A sub in a package is named ``Pkg::sub``; its slot held the bare ``sub``,
so two packages' ``run`` differed only by span. The slot now comes from the
name through ``make_symbol_id``'s sanitizer (``Pkg..sub``), as a package
id's ``Foo..Bar`` already did. The stable id hashes the sub name and the
qualified name, never the id.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import sanitize_id_name_segment
from hypergumbo_lang_mainstream.perl import analyze_perl

_SRC = (
    "package Demo::Core;\n"
    "sub run { return Demo::Core::helper(); }\n"
    "sub helper { return 1; }\n"
    "package main;\n"
    "sub top { return 2; }\n"
    "1;\n"
)


def _result(tmp_path: Path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "Demo.pm").write_text(_SRC)
    return analyze_perl(tmp_path)


def test_the_fixture_emits_qualified_and_main_subs(tmp_path: Path) -> None:
    names = {(s.kind, s.name) for s in _result(tmp_path).symbols}
    assert {("function", "Demo::Core::run"), ("function", "Demo::Core::helper"),
            ("function", "top"), ("module", "Demo::Core")} <= names


def test_every_name_slot_is_the_sanitized_name(tmp_path: Path) -> None:
    wrong = [(s.id, s.name) for s in _result(tmp_path).symbols
             if s.kind != "file"
             and s.id.rsplit(":", 2)[1] != sanitize_id_name_segment(s.name)]
    assert wrong == []


def test_a_call_between_subs_ends_on_the_sub_id(tmp_path: Path) -> None:
    """Control: the resolved call lands on the minted id. The call writes its
    package: a bare ``helper()`` is reported for the shared linkers to bind
    (ADR-0064), so the analyzer alone makes no edge for it."""
    result = _result(tmp_path)
    ids = {s.name: s.id for s in result.symbols}
    calls = {(e.src, e.dst) for e in result.edges if e.edge_type == "calls"}
    assert (ids["Demo::Core::run"], ids["Demo::Core::helper"]) in calls
