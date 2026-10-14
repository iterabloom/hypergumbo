# SPDX-License-Identifier: AGPL-3.0-or-later
"""``declared_by_an_included_header``: the C/C++ resolvers' shim rule (WI-rimon).

A translation unit that includes the header declaring a catalogued function
calls the function that header declares, so a same-named definition in another
file does not capture the call. The rule asks the SHIPPED catalogue through the
one row-choice rule with the include list as the module slot; these pin its
answer on the shapes the resolvers hand it.
"""
from __future__ import annotations

import pytest

from hypergumbo_core import io_boundary as iob


@pytest.mark.parametrize("language,name,includes,expected", [
    ("c", "sendto", ["stdlib.h", "sys/socket.h"], True),
    ("c", "send", ["sys/socket.h"], True),            # an ambiguous name, lifted by the header
    ("c", "sendto", ["stdlib.h", "string.h"], False),  # the declaring header is not included
    ("c", "sendto", [], False),                         # no include evidence at all
    ("c", "my_helper", ["sys/socket.h"], False),        # not a catalogued primitive
    ("cpp", "sendto", ["sys/socket.h"], True),          # cpp asks through its C parent
])
def test_the_header_decides(language: str, name: str, includes: list[str], expected: bool) -> None:
    assert iob.declared_by_an_included_header(language, name, includes) is expected


def test_the_catalogue_is_loaded_once_per_language() -> None:
    iob.declared_by_an_included_header("c", "send", ["sys/socket.h"])
    first = iob._INCLUDED_HEADER_CATALOGS["c"]
    iob.declared_by_an_included_header("c", "recv", ["sys/socket.h"])
    assert iob._INCLUDED_HEADER_CATALOGS["c"] is first
