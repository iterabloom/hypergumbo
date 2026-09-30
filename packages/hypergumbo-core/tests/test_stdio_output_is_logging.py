# SPDX-License-Identifier: AGPL-3.0-or-later
"""A process's own standard output is ``logging`` in EVERY catalogue (WI-runos).

WI-tolif ruled that writing to stdout / stderr is terminal output, not
inter-process communication, and moved python's ``sys.stdout`` /
``sys.stderr`` from ``ipc_send`` to ``logging``; WI-dutah carried the ruling to
c, javascript and rust. Three catalogues were never reached -- go
(``os.Stdout`` / ``os.Stderr``), java (``java.lang.System.out`` / ``.err``,
which kotlin and scala inherit) and cpp (``std::cout`` / ``std::cerr``) -- so a
stdio write there was a false ``ipc`` sink, and in java and cpp, where no
writer row carries the write, the ``logging`` claim read clean.

The earlier sweep matched by NAME ("stdout", "stderr") and missed cpp's
``cout`` / ``cerr``. So this test does not look for names: it takes every
catalogue in the tree and asks each one about every spelling of a standard
output stream that ANY catalogue uses. A new catalogue, or a stream a catalogue
rows under a boundary other than ``logging``, fails here.

The input direction is deliberately NOT moved: piped-in data is attacker-chosen,
so ``stdin`` / ``System.in`` / ``std::cin`` stay ``ipc_recv`` everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core
from hypergumbo_core.io_boundary import load_catalog

_CATALOG_DIR = Path(hypergumbo_core.__file__).parent / "io_primitives"
_LANGUAGES = sorted(p.stem for p in _CATALOG_DIR.glob("*.yaml"))

#: (module, name) of the process's own standard OUTPUT streams, as the
#: catalogues spell them. A catalogue that rows one must row it as logging.
_STD_OUTPUT = {
    ("sys", "stdout"), ("sys", "stderr"),
    ("stdio", "stdout"), ("stdio", "stderr"),
    ("std", "cout"), ("std", "cerr"), ("std", "clog"),
    ("os", "Stdout"), ("os", "Stderr"),
    ("java.lang.System", "out"), ("java.lang.System", "err"),
    ("process", "stdout"), ("process", "stderr"),
    ("std::io", "stdout"), ("std::io", "stderr"),
}

#: ... and its standard INPUT, which stays a minting ipc_recv source.
_STD_INPUT = {
    ("sys", "stdin"), ("stdio", "stdin"), ("std", "cin"), ("os", "Stdin"),
    ("java.lang.System", "in"), ("process", "stdin"), ("std::io", "stdin"),
}


def _rows(language: str, pairs: set[tuple[str, str]]) -> list:
    return [p for p in load_catalog(language, include_defaults=False).primitives
            if (p.module, p.name) in pairs]


@pytest.mark.parametrize("language", _LANGUAGES)
def test_a_standard_output_stream_is_logging(language: str) -> None:
    wrong = [(p.module, p.name, p.boundary) for p in _rows(language, _STD_OUTPUT)
             if p.boundary != "logging"]
    assert wrong == [], f"{language}: standard output rowed outside logging: {wrong}"


@pytest.mark.parametrize("language", _LANGUAGES)
def test_standard_input_stays_ipc_recv(language: str) -> None:
    wrong = [(p.module, p.name, p.boundary) for p in _rows(language, _STD_INPUT)
             if p.boundary != "ipc_recv"]
    assert wrong == [], f"{language}: standard input rowed outside ipc_recv: {wrong}"


@pytest.mark.parametrize(("language", "module", "name"), [
    ("go", "os", "Stdout"), ("go", "os", "Stderr"),
    ("java", "java.lang.System", "out"), ("java", "java.lang.System", "err"),
    ("kotlin", "java.lang.System", "out"), ("scala", "java.lang.System", "err"),
    ("cpp", "std", "cout"), ("cpp", "std", "cerr"), ("cpp", "std", "clog"),
    ("python", "sys", "stderr"), ("c", "stdio", "stdout"),
])
def test_the_stream_is_rowed_at_all(language: str, module: str, name: str) -> None:
    """THE CONTROL: the sweeps above are vacuous for a stream nobody rows.

    kotlin and scala have no row of their own -- they reach java's through
    ``_CATALOG_PARENTS`` -- which is why they are asked here by language.
    """
    hits = [p for p in load_catalog(language).primitives
            if (p.module, p.name) == (module, name)]
    assert hits, f"{language} has no row for {module}.{name}"
    assert {p.boundary for p in hits} == {"logging"}
