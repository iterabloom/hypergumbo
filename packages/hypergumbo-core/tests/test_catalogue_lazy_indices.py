# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalogue's lookup indices are built when first read, and are its own.

``IoBoundaryCatalog`` built its three lookup dictionaries in
``__post_init__``, on every construction. ``load_catalog`` constructs a
catalogue eleven times on the way to the one it returns (the shipped file,
each overlay, and a ``dataclasses.replace`` for every field it restores after
a merge), so loading the Python catalogue indexed its 758 rows eleven times:
half of the 7 ms the load takes, and one ``verify-claims`` run makes about
forty loads. After the YAML parse was kept, ``load_catalog`` was the largest
fixed cost left in the forty slowest test files: 145 of 777 test-seconds, in
9,118 calls (``~/hypergumbo_lab_notebook/slow_tests_11142026/catalog``).

The indices are now built by the first lookup. What is pinned:

* constructing or loading a catalogue builds nothing, the first read through
  ANY reader builds once, and later reads build nothing;
* every reader answers on a catalogue nothing has read before;
* a catalogue made from another with ``dataclasses.replace`` has its own
  index objects. ``replace`` hands the new object the old one's field values,
  the index dictionaries among them; building in place then emptied the
  ORIGINAL's index too (``replace(c, primitives=[])`` made ``c.lookup``
  return nothing);
* equality does not depend on whether a catalogue has been read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

import pytest

from hypergumbo_core.io_boundary import IoBoundaryCatalog, load_catalog


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry, the number of rows indexed, per index build."""
    done: list[int] = []
    real = IoBoundaryCatalog._rebuild_indices

    def spy(self: IoBoundaryCatalog) -> None:
        done.append(len(self.primitives))
        real(self)

    monkeypatch.setattr(IoBoundaryCatalog, "_rebuild_indices", spy)
    return done


#: Each way of reading the indices, with a call that must find something.
#: (``all_boundaries_for`` and ``simultaneous_boundaries_for`` scan the row
#: list and read no index.)
READERS: dict[str, Callable[[IoBoundaryCatalog], Any]] = {
    "lookup_short": lambda c: c.lookup("open"),
    "lookup_qualified": lambda c: c.lookup("builtins.open"),
    "lookup_all": lambda c: c.lookup_all("builtins.open"),
    "lookup_with_module": lambda c: c.lookup_with_module("open", "builtins"),
}


def test_loading_a_catalogue_builds_no_index(builds: list[int]) -> None:
    catalog = load_catalog("python")
    assert len(catalog.primitives) > 500, "the shipped python catalogue"
    assert builds == []


@pytest.mark.parametrize("reader", sorted(READERS))
def test_the_first_read_builds_once_and_answers(reader: str, builds: list[int]) -> None:
    catalog = load_catalog("python")
    first = READERS[reader](catalog)
    assert first, f"{reader} must find its row on a catalogue nothing has read"
    assert builds == [len(catalog.primitives)]
    for other in READERS.values():
        other(catalog)
    assert READERS[reader](catalog) == first
    assert len(builds) == 1


def test_the_short_name_reader_builds_by_itself(builds: list[int]) -> None:
    """The public lookups all read the qualified index first, which builds;
    the short-name reader must not depend on being called second."""
    catalog = load_catalog("python")
    assert [p.module for p in catalog._short_rows("open")].count("builtins") == 2
    assert builds == [len(catalog.primitives)]


def test_a_dual_classified_row_keeps_both_boundaries() -> None:
    """The reason ``_by_qualified_all`` exists, on a fresh catalogue."""
    catalog = load_catalog("python")
    assert {p.boundary for p in catalog.lookup_all("builtins.open")} >= {"fs_read", "fs_write"}


def test_a_derived_catalogue_does_not_empty_its_original() -> None:
    original = load_catalog("python")
    assert original.lookup("open") is not None
    derived = replace(original, primitives=[])
    assert derived.lookup("open") is None
    assert original.lookup("open") is not None
    assert original.lookup_all("builtins.open")


def test_a_derived_catalogue_indexes_its_own_rows() -> None:
    original = load_catalog("python")
    kept = [p for p in original.primitives if p.name == "open"]
    assert kept and len(kept) < len(original.primitives)
    original.lookup("open")  # the original's indices exist before the copy
    derived = replace(original, primitives=kept)
    assert derived.lookup("open") is not None
    gone = next(p for p in original.primitives if p.name != "open")
    assert derived.lookup(gone.qualified_name) is None
    assert original.lookup(gone.qualified_name) is not None


def test_equality_does_not_depend_on_having_been_read() -> None:
    read, unread = load_catalog("python"), load_catalog("python")
    assert read == unread
    read.lookup("open")
    assert read == unread
    assert replace(read, primitives=read.primitives[:1]) != unread


def test_a_catalogue_built_directly_is_lazy_too(builds: list[int]) -> None:
    row = load_catalog("python").primitives[0]
    catalog = IoBoundaryCatalog(language="python", primitives=[row])
    assert builds == []
    assert catalog.lookup(row.qualified_name) is row
    assert builds == [1]


def test_an_explicit_rebuild_sees_rows_changed_in_place(builds: list[int]) -> None:
    """``_rebuild_indices`` stays callable for a caller that edits the row
    list of a catalogue it has already read."""
    source = load_catalog("python")
    first, second = source.primitives[0], next(
        p for p in source.primitives if p.qualified_name != source.primitives[0].qualified_name)
    catalog = IoBoundaryCatalog(language="python", primitives=[first])
    assert catalog.lookup(second.qualified_name) is None
    catalog.primitives.append(second)
    assert catalog.lookup(second.qualified_name) is None, "the built index is not re-read"
    catalog._rebuild_indices()
    assert catalog.lookup(second.qualified_name) is second
