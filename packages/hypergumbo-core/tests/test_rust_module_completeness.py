# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rust's ``module_completeness`` grants (WI-lutuh).

WHAT A GRANT ASSERTS, and why it is the dangerous direction. ``completeness:
complete`` says EVERY I/O primitive a module exposes is catalogued, which is
what lets a consumer read silence as an examined negative instead of "none I
could see". Rust declared ZERO of them, so no Rust module could ever be an
examined negative and every clean Rust boundary verdict was withheld. The fix
is not a flag flip: a wrong grant manufactures a false all-clear in a security
tool, which is why the python audit's unaided first draft -- 5 of 32 verdicts
wrong, EVERY error on the false-all-clear side -- is the evidence bar these
grants had to clear.

HOW THE 20 WERE REACHED. A mechanical probe over the installed toolchain's
``library/`` refuses on any syscall reach or I/O-bearing surface and NEVER
grants; over 33 modules it left 18 candidates. A candidate is not a grant,
because the probe reads a module's own text and a re-export can carry I/O in
from a module whose text is clean. Each of the 18 was then adjudicated against
the source, which caught exactly that:

  ``std::collections::HashMap`` came back with NO SIGNAL, yet ``HashMap::new()``
  returns ``HashMap<K, V, RandomState>`` and RandomState's initialiser calls
  ``crate::sys::random::hashmap_random_keys`` -- an OS entropy read. It is
  invisible to a text scan of ``map.rs`` because RandomState is DEFINED IN
  ANOTHER MODULE. **A DEFAULT TYPE PARAMETER CAN CARRY A BOUNDARY.**

THAT FINDING DID NOT COST THE GRANT, and the reason is a cross-language
consistency argument rather than a judgement call. The shipped python catalogue
already grants ``secrets``, ``random`` AND ``uuid`` -- all three draw on the OS
RNG -- on the recorded reasoning that the seeding belongs to the row of the
module that PERFORMS it (``os.urandom``) and not to the surface of the module
that consumes it. ``os.getrandom`` was removed from ``env_read`` outright with
"a CSPRNG read is not an environment read under any reading", so this
vocabulary has no kind for entropy at all. Refusing rust's hash collections on
a read python grants would make ``module_completeness`` mean something
different per language, which is the failure mode the axis discipline exists to
prevent. :class:`TestTheEntropyGrantsRestOnThePythonPrecedent` pins that
dependency: if python's grants ever go, rust's rationale must be revisited
rather than silently outliving its basis.

WHAT IS DELIBERATELY WITHHELD, because a grant list is only as good as its
refusals. ``std`` itself is never granted -- marking the whole standard library
complete would turn every unmatched ``std`` call into an examined negative. The
clock modules are withheld for the OPPOSITE reason to the others: WI-pavob
ruled a clock read IS ``host_info_read``, so ``std::time`` and
``std::time::SystemTime`` need ROWS, not a no-I/O declaration, and those rows
are WI-tubij's cross-language scope.

MATCHING IS EXACT, so every audited module is declared individually --
``std::collections`` does not vouch for ``std::collections::HashMap``, and no
grant is inferred from any other.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import compute_boundary_coverage

#: Every module this audit declares, with the evidence that carried it.
GRANTED = [
    ("std::borrow::Cow", "one-hop clean; a clone-on-write value wrapper"),
    ("std::cmp", "one-hop clean; ordering and comparison only"),
    ("std::collections", "entropy via RandomState only; python precedent"),
    ("std::collections::BTreeSet", "one-hop clean; ordered set, no hasher"),
    ("std::collections::HashMap", "entropy via RandomState only; python precedent"),
    ("std::collections::HashSet", "entropy via RandomState only; python precedent"),
    ("std::collections::hash_map::Entry", "entropy via RandomState only; python precedent"),
    ("std::ffi::CStr", "one-hop clean; borrowed C string view"),
    ("std::io::ErrorKind", "plain enum: ZERO pub fns, one pub(crate) as_str"),
    ("std::iter", "one-hop clean; iterator adapters"),
    ("std::mem", "one-hop clean; size/align/swap/replace"),
    ("std::net::IpAddr", "one-hop clean; address value type"),
    ("std::net::SocketAddr", "value type; its only std impl is the IDENTITY ToSocketAddrs"),
    ("std::ptr", "over-broad net finds ZERO; ptr::metadata is fat-pointer metadata"),
    ("std::slice", "over-broad net finds ZERO"),
    ("std::sync", "WI-pavob: thread contention is not a boundary"),
    ("std::sync::Arc", "one-hop clean; atomic refcount"),
    ("std::sync::Mutex", "WI-pavob: a futex moves no data"),
    ("std::sync::mpsc", "intra-process channel, so not ipc_recv; grant stands"),
    ("std::time::Duration", "one-hop clean; pure arithmetic in core"),
]

#: Audited and REFUSED. A grant list is only as good as its refusals.
WITHHELD = [
    ("std", "granting the whole stdlib makes every unmatched call an examined negative"),
    ("std::env", "env_read/env_write rows"),
    ("std::fs", "the filesystem surface itself"),
    ("std::io", "the I/O module itself"),
    ("std::io::BufReader", "implements Read; delegated I/O is still I/O"),
    ("std::os::windows::fs", "platform filesystem extensions"),
    ("std::path", "canonicalize / fs::metadata / read_dir"),
    ("std::path::Path", "carries the filesystem methods"),
    ("std::path::PathBuf", "DEREFS to Path, so it inherits Path's surface"),
    ("std::process", "subprocess surface"),
    ("std::thread", "reaches libc/sys"),
    ("std::time", "WI-pavob ruled a clock read IS host_info_read: needs ROWS"),
    ("std::time::SystemTime", "WI-pavob: SystemTime::now is a clock read"),
]


@pytest.fixture(scope="module")
def rust_completeness() -> dict[str, str]:
    catalog = load_catalog("rust")
    assert catalog is not None, "the rust catalogue must load"
    return catalog.module_completeness


@pytest.fixture(scope="module")
def rust_catalog():
    catalog = load_catalog("rust")
    assert catalog is not None, "the rust catalogue must load"
    return catalog


class TestTheAuditedModulesAreDeclared:
    """WI-lutuh's statement: rust declared zero, so nothing could be clean."""

    @pytest.mark.parametrize("module,why", GRANTED)
    def test_module_is_enumerated(self, rust_catalog, module: str, why: str) -> None:
        assert rust_catalog.module_io_is_enumerated(module), (
            f"{module} was audited and adjudicated grantable ({why}) but the "
            f"catalogue does not declare it, so it still blocks every verdict"
        )

    def test_rust_declares_a_nonzero_number_of_modules(self, rust_completeness) -> None:
        assert len(rust_completeness) >= len(GRANTED), (
            "this is WI-lutuh's whole statement: zero grants means no rust "
            "module can ever be an examined negative"
        )


class TestTheRefusalsStayRefused:
    """A grant list is only as good as the modules it declines to grant."""

    @pytest.mark.parametrize("module,why", WITHHELD)
    def test_module_is_not_declared(self, rust_catalog, module: str, why: str) -> None:
        assert not rust_catalog.module_io_is_enumerated(module), (
            f"{module} must NOT carry a completeness grant: {why}"
        )

    def test_bare_std_is_never_granted(self, rust_catalog) -> None:
        # The single most dangerous false positive available in this audit.
        assert not rust_catalog.module_io_is_enumerated("std")

    def test_the_clock_modules_await_rows_not_a_grant(self, rust_catalog) -> None:
        # WI-pavob ruled the clock IN as host_info_read; WI-tubij owns the rows.
        for module in ("std::time", "std::time::SystemTime"):
            assert not rust_catalog.module_io_is_enumerated(module)


class TestMatchingIsExactAndNothingIsInferred:
    """Declaring a parent must never vouch for a child, or vice versa."""

    def test_a_parent_grant_does_not_vouch_for_an_undeclared_child(
        self, rust_catalog
    ) -> None:
        assert rust_catalog.module_io_is_enumerated("std::collections")
        # Audited neither way, so it must block despite its parent's grant.
        assert not rust_catalog.module_io_is_enumerated(
            "std::collections::VecDeque"
        )

    def test_a_child_grant_does_not_vouch_for_its_parent(self, rust_catalog) -> None:
        assert rust_catalog.module_io_is_enumerated("std::net::IpAddr")
        assert not rust_catalog.module_io_is_enumerated("std::net")

    def test_no_grant_is_reachable_by_suffix(self, rust_catalog) -> None:
        # `_module_matches` (the TAGGER) matches trailing components; this gate
        # must not, or a cosmetic respell becomes a security-relevant edit.
        assert not rust_catalog.module_io_is_enumerated("mycrate::std::ptr")
        assert not rust_catalog.module_io_is_enumerated("ptr")


class TestEveryGrantIsADatedPositiveDeclaration:
    """The standing ruling: a no-I/O claim is a POSITIVE dated declaration."""

    def test_the_map_holds_a_parseable_date_per_module(
        self, rust_completeness
    ) -> None:
        # The loader stores the RETRIEVED DATE as the value, not the literal
        # "complete" — the flag gates entry, the date is what survives. It
        # already rejects a missing or non-string date, so what is left to
        # check is that the string is a real ISO date rather than free text.
        for module, value in rust_completeness.items():
            _dt.date.fromisoformat(str(value)), f"{module}: {value!r}"

    def test_every_yaml_entry_declares_complete_and_a_date(self) -> None:
        import pathlib

        import yaml

        from hypergumbo_core import io_boundary

        path = (
            pathlib.Path(io_boundary.__file__).parent
            / "io_primitives"
            / "rust.yaml"
        )
        raw = yaml.safe_load(path.read_text())
        entries = raw.get("module_completeness") or []
        assert entries, "rust.yaml must carry module_completeness entries"
        for entry in entries:
            assert entry.get("completeness") == "complete", (
                f"{entry.get('module')} declares "
                f"{entry.get('completeness')!r}; the only grant this audit "
                f"makes is 'complete'"
            )
            assert entry.get("retrieved"), (
                f"{entry.get('module')} has no retrieved date; a grant is a "
                f"DATED declaration, so a reader can tell how stale it is"
            )
            _dt.date.fromisoformat(str(entry["retrieved"]))
        assert len({e["module"] for e in entries}) == len(entries), (
            "a duplicate module key would SILENTLY WIN in YAML"
        )


class TestTheEntropyGrantsRestOnThePythonPrecedent:
    """Four rust grants borrow python's reasoning; pin the dependency.

    ``HashMap::new()`` reads OS entropy through ``RandomState``. It is granted
    anyway because python grants ``secrets``/``random``/``uuid`` on the
    recorded reasoning that OS-RNG seeding belongs to the PERFORMING module's
    row, not the consuming module's surface. If those grants ever go, rust's
    rationale has lost its basis and must be re-argued rather than quietly
    outliving it.
    """

    @pytest.mark.parametrize("module", ["secrets", "random", "uuid"])
    def test_python_still_grants_the_entropy_consumers(self, module: str) -> None:
        catalog = load_catalog("python")
        assert catalog is not None
        assert catalog.module_io_is_enumerated(module), (
            f"python no longer grants {module}; four rust grants "
            f"(collections, HashMap, HashSet, hash_map::Entry) cite it"
        )

    @pytest.mark.parametrize(
        "module",
        [
            "std::collections",
            "std::collections::HashMap",
            "std::collections::HashSet",
            "std::collections::hash_map::Entry",
        ],
    )
    def test_the_rust_side_of_the_precedent(self, rust_catalog, module: str) -> None:
        assert rust_catalog.module_io_is_enumerated(module)


def _rust_coverage(edges: list[dict]):
    """Run the real gate over the real shipped rust catalogue."""
    return compute_boundary_coverage(
        edges, {"rust"}, {"rust": load_catalog("rust")},
    )


def _call_into(module: str, name: str) -> list[dict]:
    """A first-party function making one external call into ``module``.

    The module slot carries the full path, so `_module_from_symbol_path` reads
    `std::collections::HashMap` back out. NEVER string-parse the id yourself —
    a rust module slot contains `::` and splitting on `:` shears it.
    """
    return [
        {"src": "rust:src/main.rs:1-1:file:file",
         "dst": f"rust:{module}:0-0:{name}:external_symbol", "type": "imports"},
        {"src": "rust:src/main.rs:3-5:run:function",
         "dst": f"rust:{module}:0-0:{name}:external_symbol", "type": "calls"},
    ]


class TestTheGrantsActuallyPermitACleanVerdict:
    """THE POSITIVE CONTROL, and the reason it is not optional.

    On the five-repo rust cohort these grants move NO verdict — every surviving
    `inconclusive` is held by a DIFFERENT rung (method-starvation on
    `std::path::Path`, languages with no catalogue at all, third-party crates).
    "No movement" on its own cannot tell a working grant blocked upstream from
    a grant that does nothing, and those call for opposite next actions. This
    exercises the gate directly, so the mechanism is demonstrated even where the
    cohort cannot show it.
    """

    def test_a_granted_module_supports_a_clean_verdict(self) -> None:
        coverage = _rust_coverage(_call_into("std::collections::HashMap", "new"))
        assert coverage.complete is True, (
            f"a call into a module this audit ENUMERATED must be adjudicable; "
            f"before these grants rust had none and this was False. "
            f"reason: {coverage.reason!r}"
        )

    def test_an_ungranted_stdlib_module_still_withholds(self) -> None:
        # The clock: audited, deliberately NOT granted, needs rows (WI-tubij).
        coverage = _rust_coverage(_call_into("std::time", "SystemTime"))
        assert coverage.complete is False, (
            "std::time performs a host_info_read and must keep blocking"
        )
        assert "std::time" in coverage.reason

    def test_a_third_party_crate_still_withholds(self) -> None:
        # Catalogues are stdlib-scoped by standing ruling. This is the control
        # that proves the gate did not simply start permitting everything.
        coverage = _rust_coverage(_call_into("age_core::format", "Stanza"))
        assert coverage.complete is False
        assert "age_core" in coverage.reason

    def test_an_undeclared_sibling_of_a_granted_module_withholds(self) -> None:
        # Exact matching, exercised at the GATE rather than on the predicate.
        coverage = _rust_coverage(
            _call_into("std::collections::VecDeque", "new")
        )
        assert coverage.complete is False, (
            "granting std::collections must not vouch for an unaudited child"
        )
