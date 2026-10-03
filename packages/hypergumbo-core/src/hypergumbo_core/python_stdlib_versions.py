# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which top-level modules are Python standard library on SOME supported
interpreter but not all of them.

WHY THIS EXISTS (WI-gisan, WI-jojuz). "Is ``M`` the standard library?" has a
different answer on each Python. ``tomllib`` joined in 3.11 (PEP 680);
``distutils``, ``imp``, ``asyncore``, ``asynchat`` and ``smtpd`` left in 3.12;
the PEP 594 batch and ``lib2to3`` left in 3.13. Two places answered the
question from ONE interpreter, and each answer moved with it:

* ``io_primitives/python.yaml``'s ``stdlib_modules`` was generated from 3.12
  alone, so ``import distutils`` in a 3.10 project was stamped
  ``ecosystem=third_party``. That contradicts ADR-0041 §3's own axis
  definition: ``stdlib`` means shipped with the language runtime, and the 3.10
  runtime ships ``distutils``.
* The built-in catalogue scope gate (``test_catalogue_scope_gates.py``) read
  ``sys.stdlib_module_names`` of whichever Python ran it, so python.yaml's
  ``tomllib`` row failed the nightly 3.10 leg.

HOW. The stdlib line is the UNION over :data:`SUPPORTED_PYTHONS`. Because the
differences are few and only change when a Python is added to the matrix,
they are pinned here as version windows rather than recomputed:
``name: (since, until)``, the first and last minor version in which the
module IS standard library (``None`` = before 3.10 / still present).
``scripts/refresh-stdlib-modules`` writes the union into python.yaml (one
writer, ADR-0041 §3's single-source rule). The scope-gate test admits through
the same table. Every nightly leg re-checks each window against the
interpreter it runs (``test_python_stdlib_versions.py``), so a wrong window
fails somewhere real.

SOURCE. ``Python/stdlib_module_names.h`` on the CPython 3.10, 3.11, 3.12 and
3.13 branches, read 2026-10-02. That file is what
``sys.stdlib_module_names`` is generated from. Private ``_``-modules are
omitted: no catalogue names one.

WHY WIDENING IS SAFE. Recognising a module as stdlib grants nothing at a
verdict. The io-boundary closed-world gates ask
``IoBoundaryCatalog.module_io_is_enumerated``, an explicit per-module audit,
and never ``is_stdlib_module`` (ADR-0041 §3, "Not a consumer"; INV-buzab).
So ``distutils`` becoming stdlib changes its ecosystem LABEL, never an
all-clear. ``test_python_stdlib_versions.py`` pins that a ``distutils``
call still withholds.

ADDING A PYTHON. Append it to :data:`SUPPORTED_PYTHONS` and to the nightly
matrix, then diff its ``stdlib_module_names.h`` against the previous branch
and add or close windows here. The window test fails on the new leg until
you do.
"""

from __future__ import annotations

from typing import Optional

#: The interpreters hypergumbo supports and the nightly matrix runs
#: (``.woodpecker/nightly.yml``).
SUPPORTED_PYTHONS: tuple[tuple[int, int], ...] = (
    (3, 10), (3, 11), (3, 12), (3, 13),
)

_Window = tuple[Optional[tuple[int, int]], Optional[tuple[int, int]]]

#: Public top-level modules whose standard-library membership differs across
#: :data:`SUPPORTED_PYTHONS`, as ``name: (since, until)``.
PYTHON_STDLIB_VERSION_WINDOWS: dict[str, _Window] = {
    "tomllib": ((3, 11), None),  # PEP 680
    "binhex": (None, (3, 10)),
    # Removed in 3.12 (distutils: PEP 632; asynchat/asyncore/smtpd: PEP 594).
    **dict.fromkeys(
        ("asynchat", "asyncore", "distutils", "imp", "smtpd"), (None, (3, 11)),
    ),
    # Removed in 3.13 (PEP 594, plus lib2to3).
    **dict.fromkeys(
        (
            "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr",
            "lib2to3", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev",
            "pipes", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
        ),
        (None, (3, 12)),
    ),
}


def in_window(name: str, version: tuple[int, int]) -> bool:
    """True when ``name``'s window covers ``version``. Names outside the table
    are not decided here; ask the interpreter's own list."""
    since, until = PYTHON_STDLIB_VERSION_WINDOWS[name]
    return (since is None or since <= version) and (
        until is None or version <= until
    )


def supported_versions_label() -> str:
    """``"3.10-3.13"``: the span python.yaml's ``stdlib_provenance`` declares
    as ``supported_versions`` and the generator checks it against."""
    lo, hi = SUPPORTED_PYTHONS[0], SUPPORTED_PYTHONS[-1]
    return f"{lo[0]}.{lo[1]}-{hi[0]}.{hi[1]}"
