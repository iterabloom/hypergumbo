# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hilot: ``<cstdlib>`` is C's ``stdlib``, in C and C++ and nowhere else.

C++ spells each C library header a second way (ISO C++ [headers], "C++
headers for C library facilities"): ``<cstdlib>`` declares what
``<stdlib.h>`` declares, in namespace ``std``. ``cpp.py`` puts the file's
include list in an unresolved call's module slot verbatim, so a file that
includes ``<cstdlib>``, ``<cstring>`` and ``<sys/socket.h>`` stamps
``cstdlib,cstring,sys/socket.h``. ``c.yaml`` keys its rows under the C STEM
(``stdlib``), ``io_boundary`` deliberately did not strip a leading ``c`` (it
would maul ``crypto``, ``cmark``), and a non-sentinel slot suppresses the
short-name fallback -- so ``getenv`` / ``fopen`` / ``printf`` in such a file
classified as NOTHING, and verify-claims withheld "calls into 3 module(s) that
the I/O catalog could not classify (cstdlib, cstring, sys/socket)".

THE FIX IS A CLOSED TABLE IN THE SPELLING LAYER, NOT DUPLICATED ROWS.
:func:`io_boundary.module_hint_disjuncts` already offers a disjunct's
spellings (``stdio.h`` -> ``stdio``); it now also offers the C stem of a C++
C-library header, from :data:`io_boundary._CXX_C_LIBRARY_HEADERS`. Both
consumers derive from that one function -- classification (ANY spelling, via
``named_lookup_arm``, shared by io and taint) and the coverage gate (ALL
disjuncts enumerated) -- so they cannot disagree about what ``cstdlib`` is.

SCOPED BY LANGUAGE. Python's ``cmath`` is a real module that is not its
``math``, and ``math`` is a declared ``module_completeness`` entry there; an
unscoped table would let the coverage gate vouch for ``cmath``. The table
applies only when the CALL's language is c or cpp.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges
from hypergumbo_core.io_boundary import (
    _CXX_C_LIBRARY_HEADERS,
    declared_by_an_included_header,
    load_catalog,
    module_hint_disjuncts,
    tag_io_boundaries,
)
from hypergumbo_core.verify_claims import _uncatalogued_external_modules

_SLOT = "cstdlib,cstring,cstdio,ctime,sys/socket.h"

#: ISO C++ [headers] "C++ headers for C library facilities", plus the five
#: C++17-deprecated / C++20-removed ones and C++26's two C23 additions.
#: Written out rather than derived so the table cannot agree with itself.
_STANDARD = {
    "cassert", "cctype", "cerrno", "cfenv", "cfloat", "cinttypes", "climits",
    "clocale", "cmath", "csetjmp", "csignal", "cstdarg", "cstddef", "cstdint",
    "cstdio", "cstdlib", "cstring", "ctime", "cuchar", "cwchar", "cwctype",
    "ccomplex", "ciso646", "cstdalign", "cstdbool", "ctgmath",
    "cstdbit", "cstdckdint",
}


class TestTheSpelling:

    def test_the_table_is_exactly_the_standard_list(self) -> None:
        assert set(_CXX_C_LIBRARY_HEADERS) == _STANDARD

    def test_every_entry_maps_to_its_c_stem(self) -> None:
        for cxx, stem in _CXX_C_LIBRARY_HEADERS.items():
            assert cxx == "c" + stem, (cxx, stem)

    @pytest.mark.parametrize("language", ["c", "cpp"])
    def test_a_cxx_header_offers_its_c_stem(self, language: str) -> None:
        assert module_hint_disjuncts("cstdlib", language) == [["cstdlib", "stdlib"]]

    def test_the_joined_include_slot_expands_per_disjunct(self) -> None:
        assert module_hint_disjuncts(_SLOT, "cpp") == [
            ["cstdlib", "stdlib"], ["cstring", "string"], ["cstdio", "stdio"],
            ["ctime", "time"], ["sys/socket.h", "sys/socket"],
        ]

    @pytest.mark.parametrize("language", [None, "python", "javascript"])
    def test_no_other_language_gets_the_table(self, language) -> None:
        """Python's ``cmath`` is complex math, not ``math``."""
        assert module_hint_disjuncts("cmath", language) == [["cmath"]]

    @pytest.mark.parametrize("module", ["crypto", "cmark", "curl", "cstd", "cstdlib.h"])
    def test_the_rule_is_closed_not_a_leading_c_strip(self, module: str) -> None:
        assert module_hint_disjuncts(module, "cpp") == [
            [module] + ([module[:-2]] if module.endswith(".h") else [])
        ]


class TestTheRowChoice:
    """Both consumers of ``named_lookup_arm``: io-boundaries and taint."""

    @pytest.mark.parametrize("name,boundary,primitive", [
        ("getenv", "env_read", "stdlib.getenv"),
        ("fopen", "fs_read", "stdio.fopen"),
        ("time", "host_info_read", "time.time"),
        ("sendto", "net_send", "sys/socket.sendto"),
    ])
    def test_io_classifies_through_the_cxx_spelling(
        self, name: str, boundary: str, primitive: str,
    ) -> None:
        row = load_catalog("cpp").lookup_with_module(
            name, _SLOT, call_construct="function",
        )
        assert row is not None, name
        assert (row.boundary, row.qualified_name) == (boundary, primitive)

    def test_taint_mints_the_source_through_the_cxx_spelling(self) -> None:
        from hypergumbo_core.taint import load_builtin_taint_catalog

        source = load_builtin_taint_catalog().match_source("cpp", "getenv", _SLOT)
        assert source is not None and source.module == "stdlib"

    def test_the_included_header_declares_the_function(self) -> None:
        """WI-rimon's resolver gate asks the same rule, so it sees it too."""
        assert declared_by_an_included_header("cpp", "getenv", ["cstdlib"])
        assert not declared_by_an_included_header("cpp", "getenv", ["cstring"])

    def test_cpp_analyzer_output_classifies(self, tmp_path: Path) -> None:
        """The filed shape, on real analyzer output through the CLI's path."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "#include <cstdlib>\n#include <cstring>\n#include <cstdio>\n"
            "#include <sys/socket.h>\n\n"
            "int send_key(int fd) {\n"
            "    const char *key = getenv(\"API_KEY\");\n"
            "    FILE *f = fopen(\"/tmp/x\", \"r\");\n"
            "    return (int)sendto(fd, key, strlen(key), 0, nullptr, 0);\n"
            "}\n"
        )
        raw = [e.to_dict() for e in analyze_cpp(tmp_path).edges]
        edges = _rehydrate_io_boundary_edges(raw)
        tag_io_boundaries(edges, {"cpp": load_catalog("cpp")})
        got = {
            e.dst.split(":")[-2]: (e.meta or {}).get("io_boundary")
            for e in edges if e.edge_type == "calls"
        }
        assert {"getenv", "fopen", "sendto", "strlen"} <= set(got), got  # reach
        assert got["getenv"] == "env_read"
        assert got["fopen"] == "fs_read"
        assert got["sendto"] == "net_send"


def _edge(module: str, language: str = "cpp", name: str = "zz_unrowed") -> dict:
    return {
        "src": f"{language}:src/a.cpp:10-20:caller:function",
        "dst": f"{language}:{module}:0-0:{name}:external_symbol",
        "type": "calls", "is_resolved": False, "line": 12,
    }


class TestTheCoverageGate:
    """The ALL question asks the same expansion, scoped the same way."""

    def test_an_unenumerated_disjunct_is_reported_by_its_catalogue_key(self) -> None:
        unknown = _uncatalogued_external_modules(
            [_edge("cstdlib,cstring")], {"cpp": load_catalog("cpp")},
        )
        assert set(unknown) == {"stdlib", "string"}

    def test_enumerated_c_stems_cover_their_cxx_spelling(self) -> None:
        """``<cerrno>`` / ``<cstdint>`` declare what ``errno.h`` / ``stdint.h`` do.

        Both stems carry a ``complete`` audit in ``c.yaml``; the C++ header
        adds no I/O to them, so the audit covers it. Before WI-hilot this call
        was reported unexaminable.
        """
        catalog = load_catalog("cpp")
        assert catalog.module_io_is_enumerated("errno")  # reach
        assert catalog.module_io_is_enumerated("stdint")  # reach
        assert _uncatalogued_external_modules(
            [_edge("cerrno,cstdint")], {"cpp": catalog},
        ) == []

    def test_python_cmath_is_not_vouched_for_by_math(self) -> None:
        catalog = load_catalog("python")
        assert catalog.module_io_is_enumerated("math")  # reach
        assert not catalog.module_io_is_enumerated("cmath")  # reach
        unknown = _uncatalogued_external_modules(
            [{
                "src": "python:app.py:1-5:f:function",
                "dst": "python:cmath:0-0:zz_unrowed:external_symbol",
                "type": "calls", "is_resolved": False, "line": 3,
            }],
            {"python": catalog},
        )
        assert unknown == ["cmath"]
