# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust grouped use-list registers one import alias per imported name.

WHY THIS FILE EXISTS (INV-zuvib).  ``_extract_use_aliases`` read three
use-forms -- ``use a::b;`` (``scoped_identifier``), ``use a::b as c;``
(``use_as_clause``) and ``use a;`` (``identifier``) -- and every one of them
is a DIRECT child of ``use_declaration``.  A grouped list is not: tree-sitter
wraps it in a ``scoped_use_list`` whose prefix and ``use_list`` are one level
down, so ``find_child_by_type`` (direct children only) matched nothing and the
whole declaration registered ZERO aliases.

Nothing failed loudly.  The file parses, the symbols extract and the call edge
is still emitted -- only its IDENTITY is wrong, because the module slot is
rebuilt from ``use_aliases``:

    use std::fs::File;             File::open(..)  ->  rust:std::fs::File:0-0:open
    use std::fs::{File, read};     File::open(..)  ->  rust:external:0-0:File..open

The second matches no ``io_primitives`` row, so the boundary disappears.  The
tests below pin the ALIAS MAP directly rather than the downstream edge: the
map is the producer every consumer in ``rust.py`` reads, and a test on one
consumer would leave the others unpinned.

The forms are enumerated from the grammar, not from the filed repro, because
a use-tree nests: the prefix may be an ``identifier`` / ``scoped_identifier``
/ ``crate`` / ``self`` / ``super``, and a list item may itself be any of a
name, an ``as`` clause, ``self``, a wildcard, or ANOTHER group.
"""

import pytest

from hypergumbo_lang_mainstream.rust import _extract_use_aliases

tree_sitter = pytest.importorskip("tree_sitter")
tree_sitter_rust = pytest.importorskip("tree_sitter_rust")


def _aliases(src: str) -> dict[str, str]:
    lang = tree_sitter.Language(tree_sitter_rust.language())
    parser = tree_sitter.Parser(lang)
    source = src.encode()
    return _extract_use_aliases(parser.parse(source), source)


# --- the filed repro, both directions -------------------------------------


def test_single_import_still_registers() -> None:
    """The form that already worked must keep working (control)."""
    assert _aliases("use std::fs::File;") == {"File": "std::fs::File"}


def test_grouped_list_registers_every_name() -> None:
    """INV-zuvib: the filed repro. Both names, each with the full path."""
    assert _aliases("use std::fs::{read_to_string, File};") == {
        "read_to_string": "std::fs::read_to_string",
        "File": "std::fs::File",
    }


# --- prefix shapes ---------------------------------------------------------


def test_single_segment_prefix() -> None:
    """``use std::{..}`` -- the prefix is a bare ``identifier``, not scoped."""
    assert _aliases("use std::{fs, io};") == {
        "fs": "std::fs",
        "io": "std::io",
    }


def test_crate_prefix() -> None:
    """``crate`` is its own node type, not an ``identifier``."""
    assert _aliases("use crate::{a, b::c};") == {
        "a": "crate::a",
        "c": "crate::b::c",
    }


def test_leading_colon_prefix() -> None:
    """A leading ``::`` is part of the path and must survive."""
    assert _aliases("use ::std::fs::{File};") == {"File": "::std::fs::File"}


@pytest.mark.parametrize("keyword", ["self", "super"])
def test_relative_prefix(keyword: str) -> None:
    """``self`` / ``super`` prefixes are node types of their own."""
    assert _aliases(f"use {keyword}::{{a, b}};") == {
        "a": f"{keyword}::a",
        "b": f"{keyword}::b",
    }


# --- list-item shapes ------------------------------------------------------


def test_scoped_item_keeps_its_own_qualification() -> None:
    """A list item may itself be scoped: the two paths must not collide."""
    assert _aliases("use std::{fs::File, io::Read};") == {
        "File": "std::fs::File",
        "Read": "std::io::Read",
    }


def test_as_clause_inside_a_group() -> None:
    """``as`` renames inside a group bind the NEW name to the FULL path."""
    assert _aliases("use std::fs::{File as F, read_to_string};") == {
        "F": "std::fs::File",
        "read_to_string": "std::fs::read_to_string",
    }


def test_self_inside_a_group_binds_the_module_itself() -> None:
    """``{self, File}`` imports the module under its own last segment."""
    assert _aliases("use std::fs::{self, File};") == {
        "fs": "std::fs",
        "File": "std::fs::File",
    }


def test_nested_group() -> None:
    """A group may contain a group; the prefix accumulates through both."""
    assert _aliases("use std::{fs::{File, read_to_string}, io::Read};") == {
        "File": "std::fs::File",
        "read_to_string": "std::fs::read_to_string",
        "Read": "std::io::Read",
    }


def test_wildcard_inside_a_group_yields_nothing_for_that_arm() -> None:
    """A wildcard names no importable symbol, so it registers none --
    matching the top-level ``use std::fs::*;`` behaviour -- but it must not
    suppress its SIBLINGS."""
    assert _aliases("use std::{fs::*, io::Read};") == {"Read": "std::io::Read"}


def test_top_level_wildcard_still_registers_nothing() -> None:
    """Control for the arm above: unchanged pre-existing behaviour."""
    assert _aliases("use std::fs::*;") == {}


# --- the pre-existing non-grouped forms, pinned as controls ---------------


def test_bare_use_registers_itself() -> None:
    assert _aliases("use serde;") == {"serde": "serde"}


def test_top_level_as_clause() -> None:
    assert _aliases("use std::fs::File as F;") == {"F": "std::fs::File"}


def test_top_level_as_clause_without_qualification() -> None:
    assert _aliases("use serde as s;") == {"s": "serde"}


def test_multiple_declarations_accumulate() -> None:
    assert _aliases(
        "use std::fs::{File};\nuse std::io::Read;\n"
    ) == {"File": "std::fs::File", "Read": "std::io::Read"}


# --- shapes the grammar produces that the walk must not mis-read -----------
#
# Each of these was found by dumping the parse tree, not by guessing: a
# `visibility_modifier` is a NAMED child and so lands in the same iteration as
# the use-tree; `as _` spells its alias as an ordinary `identifier`; and a
# `self` inside an `as` clause sits where a path normally does.


def test_pub_use_still_registers() -> None:
    """``pub`` is a NAMED child of ``use_declaration``, so it is walked
    alongside the use-tree and must be ignored rather than mis-read."""
    assert _aliases("pub use std::fs::{File};") == {"File": "std::fs::File"}


def test_pub_crate_use_still_registers() -> None:
    assert _aliases("pub(crate) use a::{b};") == {"b": "a::b"}


def test_anonymous_import_binds_no_name() -> None:
    """``use Trait as _;`` brings a trait into scope with NO name to call it
    through. The alias node is a plain identifier spelled ``_``, so a walk
    that trusts the node type would register a binding under ``_``."""
    assert _aliases("use std::io::Write as _;") == {}


def test_self_as_clause_binds_the_module_not_a_self_path() -> None:
    """``{self as f}`` renames the MODULE. Reading ``self`` as an ordinary
    path segment would compose the nonexistent ``std::fs::self``."""
    assert _aliases("use std::fs::{self as f, File};") == {
        "f": "std::fs",
        "File": "std::fs::File",
    }


def test_bare_use_self_binds_nothing() -> None:
    """``use self;`` has no prefix to name the module with."""
    assert _aliases("use self;") == {}


def test_trailing_comma_in_a_group() -> None:
    assert _aliases("use std::fs::{File,};") == {"File": "std::fs::File"}


def test_repeated_relative_prefix() -> None:
    assert _aliases("use super::super::{a};") == {"a": "super::super::a"}
