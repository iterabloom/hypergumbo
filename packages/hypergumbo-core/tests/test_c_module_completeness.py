# SPDX-License-Identifier: AGPL-3.0-or-later
"""The C headers that declare no I/O are examined negatives; the rest are not (WI-hasul).

``c.yaml`` declared no ``module_completeness`` at all, so no C header could be an
examined negative, and since WI-lajus stamps a call's ``#include`` set the
coverage gate asks that every included header be enumerated (measurement 0023:
15 of 21 verdicts withheld on three repositories). The grants are the headers
whose whole surface -- probed from glibc's declarations -- is types, macros and
pure functions (docs/surveys/c-stdlib-module-io-enumeration.md).

THE REFUSALS ARE PINNED AS HARD AS THE GRANTS. A grant is the false-all-clear
direction: ``string`` (strfry reads the clock; strerror may read message
catalogues), ``assert`` (writes stderr on failure) and the headers whose I/O is
not rowed yet must stay unexaminable until someone does the work.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog

GRANTED = (
    "errno", "stdint", "stdbool", "stddef", "stdarg", "limits", "float",
    "linux/limits", "sys/types", "sys/param", "inttypes", "strings", "libgen",
    "ctype", "math",
)
REFUSED = (
    "string", "assert", "stdio", "unistd", "stdlib", "sys/socket", "sys/stat",
    "fcntl", "dirent",
)


@pytest.mark.parametrize("header", GRANTED)
def test_a_no_io_header_is_an_examined_negative(header: str) -> None:
    assert load_catalog("c").module_io_is_enumerated(header)


@pytest.mark.parametrize("header", REFUSED)
def test_a_header_with_unrowed_io_is_not(header: str) -> None:
    assert not load_catalog("c").module_io_is_enumerated(header)


def test_the_grants_are_exactly_the_audited_set() -> None:
    """A new grant must arrive through the survey, not beside it."""
    assert set(load_catalog("c", include_defaults=False).module_completeness) == \
        set(GRANTED)


def test_cpp_inherits_them() -> None:
    """cpp's catalogue merges c's (_CATALOG_PARENTS), grants included."""
    assert load_catalog("cpp").module_io_is_enumerated("errno")
