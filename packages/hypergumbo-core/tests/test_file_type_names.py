# SPDX-License-Identifier: AGPL-3.0-or-later
"""A type name is hashed through the file's imports (stable-id scheme v17).

One type has several spellings in one file: ``String`` and
``java.lang.String``, an imported ``Callable`` and
``java.util.concurrent.Callable``, Rust's ``fmt::Display`` under ``use
std::fmt`` and ``std::fmt::Display``. Until v17 a type inside a compound type
was hashed as written, so the two spellings of one declaration hashed apart
(owner ruling 2026-11-20: canonicalize via imports, per file, local
information only). These tests pin the shared rule (``FileTypeNames``,
``file_type_names``, ``normalize_type``, ``resolving_type_names``); the
analyzers' tests check each language's bindings.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import (
    FileTypeNames,
    canonical_type_names,
    file_type_names,
    normalize_signature_names_first,
    normalize_signature_types_first,
    normalize_type,
    resolving_type_names,
)

_JAVA = file_type_names(
    [("Callable", "java.util.concurrent.Callable"), ("Map", "java.util.Map"),
     ("Bar", "a.b.Foo")],
    declared={"Local"},
    defaults={"java.lang.String": "String", "java.lang.Thread": "Thread",
              "java.lang.Local": "Local"},
)

_RUST = file_type_names(
    [("fmt", "std::fmt"), ("HashMap", "std::collections::HashMap")],
    defaults={"std::option::Option": "Option", "core::option::Option": "Option"},
    separator="::", quotes='"',
)


@pytest.mark.parametrize(("text", "expected"), [
    # an imported name is written as its path, an alias as what it names
    ("List<Callable>", "List<java.util.concurrent.Callable>"),
    ("Bar<T>", "a.b.Foo<T>"),
    # the rest of a chain follows its first name
    ("Map.Entry<K, V>", "java.util.Map.Entry<K, V>"),
    # a qualified spelling of a default type is its simple name
    ("List<java.lang.String>", "List<String>"),
    ("java.lang.Thread.State", "Thread.State"),
    # a name nothing binds stays as written, qualified or not
    ("List<Unknown>", "List<Unknown>"),
    ("x.y.Unknown", "x.y.Unknown"),
    # a declared name shadows the default: both spellings stay distinct
    ("Local", "Local"),
    ("java.lang.Local", "java.lang.Local"),
    # a member, a projection, a string literal and a label are not type names
    ("Foo.Callable", "Foo.Callable"),
    ("f().Callable", "f().Callable"),
    ("A#Callable", "A#Callable"),
    ('@A("Callable") Callable', '@A("Callable") java.util.concurrent.Callable'),
    ("{Callable: Callable}", "{Callable: java.util.concurrent.Callable}"),
    ("(Callable?: Callable)", "(Callable?: java.util.concurrent.Callable)"),
])
def test_dotted_chains_are_written_through_the_imports(text: str, expected: str) -> None:
    assert _JAVA.canonical(text) == expected


def test_a_skipped_name_is_left_alone() -> None:
    """A declaration's type parameter is not the import of the same name."""
    names = file_type_names([("T", "x.T"), ("U", "x.U")])
    assert names.canonical("Map<T, U>", skip={"T"}) == "Map<T, x.U>"


def test_the_head_is_kept_only_when_asked_and_only_for_a_dotted_language() -> None:
    assert _JAVA.canonical("Callable<Callable>", keep_head=True) == (
        "Callable<java.util.concurrent.Callable>")
    assert _JAVA.canonical("@Callable Callable", keep_head=True) == (
        "@java.util.concurrent.Callable Callable")
    assert _RUST.canonical("HashMap<u8, u8>", keep_head=True) == (
        "std::collections::HashMap<u8, u8>")


@pytest.mark.parametrize(("text", "expected"), [
    ("fmt::Display", "std::fmt::Display"),
    ("Box<dyn fmt::Debug>", "Box<dyn std::fmt::Debug>"),
    ("&'a HashMap<u8, u8>", "&'a std::collections::HashMap<u8, u8>"),
    ("core::option::Option<T>", "Option<T>"),
    ("std::option::Option<T>", "Option<T>"),
    ("<T as fmt::Display>::Output", "<T as std::fmt::Display>::Output"),
    ('Foo<"fmt">', 'Foo<"fmt">'),
    # a lifetime named like an import is a lifetime
    ("&'fmt str", "&'fmt str"),
    # ``T: Bound`` names T for its bound, it is not a type to rewrite
    ("T: fmt::Display", "T: std::fmt::Display"),
])
def test_rust_paths_are_written_through_the_use_declarations(text: str, expected: str) -> None:
    assert _RUST.canonical(text) == expected


def test_a_name_bound_to_two_paths_is_bound_to_neither() -> None:
    names = file_type_names([("Foo", "a.Foo"), ("Foo", "b.Foo"), ("Bar", "c.Bar"),
                             ("Bar", "c.Bar")])
    assert names.bound == {"Bar": "c.Bar"}
    assert names.canonical("List<Foo>") == "List<Foo>"


def test_a_declared_name_is_never_bound_and_empty_bindings_are_dropped() -> None:
    names = file_type_names([("Foo", "a.Foo"), ("", "x.Y"), ("Z", "")], declared=["Foo"])
    assert names.bound == {}
    assert names.declared == frozenset({"Foo"})


def test_import_order_and_an_unrelated_import_change_no_text() -> None:
    first = file_type_names([("A", "p.A"), ("B", "q.B")])
    second = file_type_names([("C", "r.C"), ("B", "q.B"), ("A", "p.A")])
    for text in ("Map<A, B>", "List<p.A>", "q.B"):
        assert first.canonical(text) == second.canonical(text)


def test_an_import_of_what_is_written_qualified_changes_no_text() -> None:
    """``List<a.b.Foo>`` hashes alike with and without ``import a.b.Foo``."""
    without = file_type_names([])
    with_import = file_type_names([("Foo", "a.b.Foo")])
    assert without.canonical("List<a.b.Foo>") == with_import.canonical("List<a.b.Foo>")
    assert with_import.canonical("List<Foo>") == "List<a.b.Foo>"


def test_nothing_to_rewrite_returns_the_text() -> None:
    empty = FileTypeNames(bound={})
    assert empty.canonical("List<java.lang.String>") == "List<java.lang.String>"
    assert _JAVA.canonical("") == ""


def test_canonical_type_names_reads_the_current_file_only() -> None:
    assert canonical_type_names("List<Callable>") == "List<Callable>"
    with resolving_type_names(_JAVA):
        assert canonical_type_names("List<Callable>") == "List<java.util.concurrent.Callable>"
        with resolving_type_names(None):
            assert canonical_type_names("List<Callable>") == "List<Callable>"
        assert canonical_type_names("List<Callable>") == "List<java.util.concurrent.Callable>"
    assert canonical_type_names("List<Callable>") == "List<Callable>"


def test_the_block_resets_when_its_body_raises() -> None:
    with pytest.raises(RuntimeError):
        with resolving_type_names(_JAVA):
            raise RuntimeError
    assert canonical_type_names("Callable") == "Callable"


@pytest.mark.parametrize(("text", "expected"), [
    # the head keeps the name the FQN strip cuts it to
    ("Callable", "Callable"),
    ("java.util.concurrent.Callable", "Callable"),
    ("x.List<Callable>", "List<java.util.concurrent.Callable>"),
    ("Callable | null", "Callable | null"),
    ("ns.Callable | null", "Callable | null"),
    # a head the strip does not govern is written through the imports
    ("Callable.() -> Unit", "java.util.concurrent.Callable.() -> Unit"),
    ("java.util.concurrent.Callable.() -> Unit", "java.util.concurrent.Callable.() -> Unit"),
    ("Callable | x.Bar", "java.util.concurrent.Callable | x.Bar"),
    ("(Callable) -> Unit", "(java.util.concurrent.Callable) -> Unit"),
])
def test_normalize_type_keeps_the_head_the_strip_governs(text: str, expected: str) -> None:
    with resolving_type_names(_JAVA):
        assert normalize_type(text, keep_type_path=True) == expected


def test_normalize_type_with_a_qualifier_kept_rewrites_every_name() -> None:
    names = file_type_names([("Str", "System.String")])
    with resolving_type_names(names):
        assert normalize_type("Str", keep_qualifier=True) == "System.String"
        assert normalize_type("List<Str>", ["Str"], keep_qualifier=True) == "List<$0>"


def test_the_shared_normalizers_hash_both_spellings_alike() -> None:
    with resolving_type_names(_JAVA):
        assert normalize_signature_types_first(
            "(List<Callable> xs) Map<String, Callable>", keep_type_path=True,
        ) == normalize_signature_types_first(
            "(List<java.util.concurrent.Callable> xs) "
            "Map<java.lang.String, java.util.concurrent.Callable>", keep_type_path=True,
        ) == "(List<java.util.concurrent.Callable>)Map<String, java.util.concurrent.Callable>"
        assert normalize_signature_names_first(
            "(f: (Callable) -> Unit): List<Callable>", keep_type_path=True,
        ) == normalize_signature_names_first(
            "(f: (java.util.concurrent.Callable) -> Unit): List<java.util.concurrent.Callable>",
            keep_type_path=True,
        )


def test_a_declared_name_wins_over_a_binding_built_by_hand() -> None:
    """``resolve`` checks the declared names itself: a ``FileTypeNames``
    built without :func:`file_type_names` still keeps a local type's name."""
    names = FileTypeNames(bound={"Foo": "a.Foo"}, declared=frozenset({"Foo"}))
    assert names.canonical("List<Foo>") == "List<Foo>"
