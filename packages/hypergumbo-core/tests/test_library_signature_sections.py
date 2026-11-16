# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library-signature catalogue's tuple_returns and member_types sections (WI-himim)."""
from __future__ import annotations

import pytest

from hypergumbo_core.library_signatures import (
    load_library_member_types,
    load_library_tuple_returns,
)


def test_the_shipped_asyncio_rows() -> None:
    tuples = load_library_tuple_returns("python")
    assert tuples["asyncio.open_connection"] == ("asyncio.StreamReader", "asyncio.StreamWriter")
    assert tuples["asyncio.open_unix_connection"] == ("asyncio.StreamReader", "asyncio.StreamWriter")
    members = load_library_member_types("python")
    assert members["asyncio.subprocess.Process.stdout"] == "asyncio.StreamReader"
    assert members["asyncio.subprocess.Process.stdin"] == "asyncio.StreamWriter"


def test_a_language_with_no_rows_has_none() -> None:
    assert load_library_tuple_returns("cobol") == {}
    assert load_library_member_types("cobol") == {}


def test_a_tuple_value_with_an_empty_position_is_refused() -> None:
    from hypergumbo_core.library_signatures import _split_tuple
    from hypergumbo_core.yaml_catalogs import CatalogueError

    assert _split_tuple("m.f", "a.A, b.B", "here") == ("a.A", "b.B")
    for bad in ("a.A, ", "a.A"):
        with pytest.raises(CatalogueError, match=r"m\.f"):
            _split_tuple("m.f", bad, "here")
