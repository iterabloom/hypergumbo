# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-zimud: a DISJUNCTIVE module slot must be expanded on BOTH consumer paths.

``cpp.py`` sets an unresolved call's module slot to the comma-joined list of
every ``#include`` in the calling file, and documents the contract in its own
comment: *"the semantics is 'this call could be from any of the included
headers'; downstream consumers may split the module_hint on commas if they need
per-header resolution."*

EXACTLY ONE OF THE TWO CONSUMERS HONOURED IT. ``io_boundary`` splits the slot to
CLASSIFY (``_module_hint_candidates``). The coverage gate handed the whole
joined string to ``module_io_is_enumerated``, where it is a synthetic
pseudo-module that no ``module_completeness`` entry can ever match — so a C++
call site with more than one system include was PERMANENTLY unexaminable rather
than merely unexamined. A contract stated in a producer comment is not enforced
(LIVE.md rule 7: one fact, two homes, and the second silently wins).

MEASURED over the WI-lutuh sweep on unmodified upstream repos: 19,273 of 81,711
C/C++ external dsts carry a comma — libzmq 21.7%, plasma-desktop 40.3%,
shaka-packager 29.5% — while all three C repos sampled are at 0.0%.

ALL, NOT ANY, and the two paths differ on purpose. Classification asks *does any
spelling name a primitive*, and an ANY answer there is a positive claim. This
gate asks *was the surface this call could have come from enumerated*, where a
non-match is informative only if EVERY possible home was enumerated. ANY here
would let one enumerated header vouch for a file that also includes twenty that
are not — the fail-open direction this gate exists to refuse.

WHAT THIS DOES NOT DO. No C or C++ module is enumerated today: both catalogues
mention ``module_completeness`` only in a comment. So on the shipped catalogues
this changes no verdict, and saying so matters — what it changes is that C++
coverage stops being IMPOSSIBLE and becomes merely UNMET, which is the state
WI-lutuh's enumeration campaign can act on. The overlay test below is what
demonstrates the difference.
"""

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    load_catalog,
    load_overlay_catalog,
    module_hint_disjuncts,
)
from hypergumbo_core.verify_claims import _uncatalogued_external_modules

_JOINED = "string,sys/socket.h,ws2tcpip.h"


def _edge(module: str) -> dict:
    return {
        "src": "cpp:src/zmq.cpp:10-20:connect_peer:function",
        # A callee the catalogue does NOT name: a call it DOES classify is
        # skipped by the loop above this gate ("a call the catalogue matched
        # was examined"), so a catalogued name would test nothing here.
        "dst": f"cpp:{module}:0-0:zmq_peer_setup:external_symbol",
        "type": "calls",
        "is_resolved": False,
        "line": 12,
    }


# ---------------------------------------------------------------------------
# The grouped expansion
# ---------------------------------------------------------------------------


def test_a_joined_slot_expands_to_one_group_per_include():
    assert module_hint_disjuncts(_JOINED) == [
        ["string"], ["sys/socket.h", "sys/socket"], ["ws2tcpip.h", "ws2tcpip"],
    ]


def test_a_single_module_slot_is_one_disjunct():
    """A language emitting one module per slot asks the same question as before."""
    assert module_hint_disjuncts("os") == [["os"]]


def test_a_lone_header_still_offers_the_stripped_spelling():
    """The slot keeps ``stdio.h`` while ``c.yaml`` declares ``stdio``."""
    assert module_hint_disjuncts("stdio.h") == [["stdio.h", "stdio"]]


def test_empty_and_blank_disjuncts_are_dropped():
    assert module_hint_disjuncts(",, ,") == []


def test_the_flat_candidate_list_is_derived_from_the_same_expansion():
    """One home: classification and coverage cannot drift about what a disjunct IS."""
    from hypergumbo_core.io_boundary import _module_hint_candidates
    flat = _module_hint_candidates(_JOINED)
    assert flat[0] == _JOINED, "the whole slot is still offered first"
    for group in module_hint_disjuncts(_JOINED):
        for spelling in group:
            assert spelling in flat


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_the_joined_pseudo_module_is_no_longer_what_gets_reported():
    """`string,sys/socket.h,ws2tcpip.h` names nothing a reader can act on."""
    unknown = _uncatalogued_external_modules(
        [_edge(_JOINED)], {"cpp": load_catalog("cpp")},
    )
    assert _JOINED not in unknown


def test_every_unenumerated_include_is_reported_by_name():
    unknown = _uncatalogued_external_modules(
        [_edge(_JOINED)], {"cpp": load_catalog("cpp")},
    )
    assert set(unknown) == {"string", "sys/socket", "ws2tcpip"}


def test_a_single_module_slot_is_reported_exactly_as_before():
    """The regression guard for every non-C++ language."""
    unknown = _uncatalogued_external_modules(
        [_edge("requests")], {"cpp": load_catalog("cpp")},
    )
    assert unknown == ["requests"]


@pytest.fixture()
def overlay(tmp_path: Path) -> Path:
    (tmp_path / "cpp-headers.yaml").write_text(
        "language: cpp\n"
        "status: overlay\n"
        "module_completeness:\n"
        "  - module: string\n"
        "    completeness: complete\n"
        '    retrieved: "2026-08-26"\n'
        "  - module: sys/socket\n"
        "    completeness: complete\n"
        '    retrieved: "2026-08-26"\n'
        "  - module: ws2tcpip\n"
        "    completeness: complete\n"
        '    retrieved: "2026-08-26"\n'
    )
    return tmp_path / "cpp-headers.yaml"


def test_enumerating_every_include_finally_covers_the_call(overlay: Path):
    """THE POINT OF THE FIX: coverage stops being impossible and becomes unmet.

    Before, no overlay could reach this call — the gate compared against the
    joined string, which no entry can spell.
    """
    catalog = load_overlay_catalog(overlay)
    assert _uncatalogued_external_modules([_edge(_JOINED)], {"cpp": catalog}) \
        == []


def test_one_unenumerated_include_still_withholds(tmp_path: Path):
    """ALL, not ANY — and this is the assertion that says which."""
    (tmp_path / "partial.yaml").write_text(
        "language: cpp\n"
        "status: overlay\n"
        "module_completeness:\n"
        "  - module: sys/socket\n"
        "    completeness: complete\n"
        '    retrieved: "2026-08-26"\n'
    )
    catalog = load_overlay_catalog(tmp_path / "partial.yaml")
    unknown = _uncatalogued_external_modules([_edge(_JOINED)], {"cpp": catalog})
    assert "sys/socket" not in unknown
    assert set(unknown) == {"string", "ws2tcpip"}


# ---------------------------------------------------------------------------
# THE THIRD CONSUMER, AND WHY IT IS A TRIPWIRE RATHER THAN A FIX
#
# This item's statement says the disjunction must be expanded on EVERY consumer
# path, and the filed repro exercises two. Enumerating every caller of
# ``module_io_is_enumerated`` turns up a THIRD — the ``external_potential``
# suppression gate in ``_compute_external_potential`` (io_boundary.py), which
# hands the RAW slot to the predicate with no expansion:
#
#     if module_hint and catalog.module_io_is_enumerated(module_hint):
#         continue
#
# THE FIRST READING WAS THAT THIS IS A LATENT BUG THAT ACTIVATES WHEN WI-lutuh
# GIVES cpp ``module_completeness`` ENTRIES. THAT READING IS WRONG, and it is
# recorded here because the correction is the useful part.
#
# A disjunctive slot exists ONLY on an UNRESOLVED edge. cpp.py builds it in the
# ``else`` branch of the resolved/unresolved test and emits it through
# ``make_unresolved_edge``; a resolved call carries its real module. And
# ``_compute_external_potential`` drops unresolved edges at F3 Filter 1 (ADR-0028)
# BEFORE reaching the enumeration gate. So the gate never sees a comma-joined
# hint, and its non-expansion cannot be observed.
#
# Verified on a real run rather than by reading: a two-include C++ fixture
# produces exactly two comma-joined dsts and BOTH are ``is_resolved: False``
# (~/hypergumbo_lab_notebook/zimud_check_08262026/).
#
# So the correct artifact is a TRIPWIRE on the assumption that makes the gate
# safe, not a fix to the gate. If a future change lets a RESOLVED edge carry a
# disjunctive hint, this test fails and points at the gate that would then be
# wrong.
# ---------------------------------------------------------------------------


def test_a_disjunctive_slot_only_ever_rides_an_unresolved_edge(tmp_path: Path):
    """The assumption that makes the third consumer unreachable.

    If this fails, ``_compute_external_potential``'s enumeration gate
    (io_boundary.py, ``module_io_is_enumerated(module_hint)``) becomes
    reachable with a comma-joined argument that can never match — and it
    needs the same ALL-over-disjuncts expansion the coverage gate got.
    """
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    src = tmp_path / "net.cpp"
    src.write_text(
        "#include <string>\n"
        "#include <sys/socket.h>\n"
        "#include <ws2tcpip.h>\n"
        "std::string f(int fd) {\n"
        "    getpeername(fd, nullptr, nullptr);\n"
        "    return std::string();\n"
        "}\n"
    )
    result = analyze_cpp(tmp_path)

    disjunctive = [
        e for e in result.edges
        if "," in str(e.dst).split(":")[1] if str(e.dst).startswith("cpp:")
    ]
    assert disjunctive, "fixture produced no disjunctive slot; it tests nothing"
    resolved = [e for e in disjunctive if getattr(e, "is_resolved", True)]
    assert resolved == [], (
        "a RESOLVED edge now carries a comma-joined module slot. The "
        "external_potential suppression gate compares that raw string against "
        "module_io_is_enumerated, which no entry can ever match, so the "
        "suppression would silently never fire. Expand it over "
        "module_hint_disjuncts with ALL semantics, as the coverage gate does."
    )
