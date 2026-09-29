# SPDX-License-Identifier: AGPL-3.0-or-later
"""io-boundaries and taint choose a catalogue row by ONE rule (INV-foda).

Both consumers take (callee name, module hint, call construct) and pick the
catalogue row the call is. They were two copies of that rule, and the copies
had drifted in five places:

1. ORDER. io-boundaries tried an exact qualified-name match before the module
   filter; taint filtered by module first, so a qualified name under a hint
   that failed the filter was classified by one and refused by the other.
2. THE DISJUNCTIVE SLOT (INV-funuf). The c/cpp analyzers put the file's whole
   ``#include`` list in the module slot. io-boundaries splits it into
   candidates; taint compared the joined string and matched nothing, so
   ``send`` / ``recv`` / ``write`` / ``read`` / ``connect`` / ``getenv`` in any
   file with two or more includes were io boundaries with NO taint entry --
   ``send(fd, getenv("API_KEY"), ...)`` CONFIRMED "no secret reaches the
   network".
3. THE METHOD-ON-A-DISJUNCTION ARM (INV-nizom), which only io-boundaries had.
4. SPELLING. io-boundaries' qualified match is separator-insensitive
   (``std::env::consts`` / ``std::env.consts``); taint's was exact.
5. PLACEHOLDERS. taint treated both ``external`` and ``<external>`` as "no
   module known" (ADR-0017 §3a); io-boundaries only ``external``, and sent
   ``<external>`` through the module filter, where it matched nothing.

The rule is now :func:`io_boundary.named_lookup_arm`, called by both. These
tests ask each consumer the same question over the SHIPPED catalogues and
require the same row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import io_boundary as iob
from hypergumbo_core import taint as T

_CAT_DIR = Path(T.__file__).parent / "io_primitives"


@pytest.fixture(scope="module")
def taint_index():
    sources, sinks, ambiguous = T._derive_auto_imports_from_io_primitives(_CAT_DIR)
    return {
        lang: (T._build_callee_index(list(sources.get(lang, [])) + list(sinks.get(lang, []))),
               ambiguous.get(lang, frozenset()))
        for lang in set(sources) | set(sinks)
    }


def _key(row):
    return None if row is None else (row.module, row.name, row.kind)


def _both(taint_index, lang, name, hint, construct=None):
    catalog = iob.load_catalog(lang)
    io_row = catalog.lookup_with_module(name, hint, call_construct=construct)
    index, ambiguous = taint_index[lang]
    taint_row = T._lookup_named_entry(index.get(name), name, hint, ambiguous, construct)
    return _key(io_row), _key(taint_row)


_INCLUDES = "stdlib.h,string.h,sys/socket.h,unistd.h"

#: (language, callee name, module hint, call construct, the row both must pick).
_CASES = {
    "c send under an include list": (
        "c", "send", _INCLUDES, None, ("sys/socket", "send", "function")),
    "c getenv under an include list": (
        "c", "getenv", _INCLUDES, None, ("stdlib", "getenv", "function")),
    "c recv under an include list": (
        "c", "recv", _INCLUDES, None, ("sys/socket", "recv", "function")),
    "cpp getenv under an include list": (
        "cpp", "getenv", "cstdlib,iostream,stdlib.h", None, ("stdlib", "getenv", "function")),
    "a qualified name under a hint the filter rejects": (
        "python", "csv.writer", "defusedcsv", "method", ("csv", "writer", "function")),
    "the <external> placeholder is no module": (
        "python", "getenv", "<external>", "function", ("os", "getenv", "function")),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_both_consumers_pick_the_same_row(taint_index, case):
    lang, name, hint, construct, expected = _CASES[case]
    io_row, taint_row = _both(taint_index, lang, name, hint, construct)
    assert io_row == taint_row == expected, (io_row, taint_row)


def test_a_method_under_an_include_list_is_refused_by_both(taint_index):
    """INV-nizom: ``fut.wait()`` in a unit that includes ``<sys/wait.h>``."""
    io_row, taint_row = _both(
        taint_index, "cpp", "wait", "future,sys/wait.h", "method",
    )
    assert io_row is None and taint_row is None


def test_a_mismatched_single_module_is_refused_by_both(taint_index):
    """THE CONTROL: the filter still refuses a module that is not the row's."""
    io_row, taint_row = _both(taint_index, "python", "getenv", "mylib", "function")
    assert io_row is None and taint_row is None


def test_the_rule_has_one_home():
    """Neither consumer keeps a private copy of the arm order."""
    import inspect
    assert "named_lookup_arm" in inspect.getsource(iob.IoBoundaryCatalog.lookup_with_module)
    assert "named_lookup_arm" in inspect.getsource(T._lookup_named_entry)
