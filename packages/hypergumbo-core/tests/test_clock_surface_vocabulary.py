# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clock read is ``host_info_read`` in EVERY catalogued language (WI-tubij).

WHY THIS FILE EXISTS. The WI-pavob ruling ("yes in") made a clock read an I/O
boundary. That is a statement about the VOCABULARY, not about Rust, so it binds
every language the tool catalogues. Until these rows existed, ``host_info_read``
meant "reads the host, and also the clock" in erlang and elixir and "reads the
host, but never the clock" in the other thirteen — the per-language drift the
axis discipline exists to prevent.

THE CLASSIFICATION RULE, applied uniformly (a per-language rule would BE the
drift):

    A row is a clock read IFF calling it samples the host clock and returns
    that value, or a value derived from it, to the program.

    IN   wall clock, monotonic clock, CPU/thread time, clock resolution, uptime.
    OUT  arithmetic on already-captured values (``Instant::duration_since``).
    OUT  sleeping; SETTING the clock (``clock_settime`` is a host WRITE and a
         different boundary question, deliberately not folded in here).
    OUT  where an argument REPLACES the read (``time.localtime(t)``).
    IN   where the argument only shapes a read that always happens
         (``datetime.now(tz)``, ``Instant.now(clock)``).

A monotonic clock counts: on Linux CLOCK_MONOTONIC runs from boot, so an early
read leaks approximate uptime, which distinguishes machines. That was settled
when the ruling was made and is not re-litigated per language here.

WHAT THESE TESTS PIN. Not "the catalogue is complete" — that claim needs
``module_completeness`` and its own audit, and none is granted here. They pin
(a) that no language is silently skipped, (b) the specific primitives each
enumeration found, so a later edit cannot quietly drop one, and (c) the KEYING
constraint measured on WI-tubij, which is a safety property rather than a
stylistic one.
"""

from __future__ import annotations

import glob
import pathlib
from typing import ClassVar

import pytest

from hypergumbo_core import io_boundary


CATALOGUE_DIR = (
    pathlib.Path(io_boundary.__file__).parent / "io_primitives"
)
LANGUAGES = sorted(p.stem for p in CATALOGUE_DIR.glob("*.yaml"))


# Every language's clock read surface, as ENUMERATED 2026-08-27 against the
# named evidence. Module -> names. Kind is asserted separately below.
CLOCK_SURFACE: dict[str, set[tuple[str, str]]] = {
    # rustc 1.94.0, library/std/src/time.rs, read directly.
    "rust": {
        ("std::time::Instant", "now"), ("std::time::Instant", "elapsed"),
        ("std::time::SystemTime", "now"), ("std::time::SystemTime", "elapsed"),
    },
    # Python 3.12.3 stdlib introspection.
    "python": {
        ("time", "time"), ("time", "time_ns"),
        ("time", "monotonic"), ("time", "monotonic_ns"),
        ("time", "perf_counter"), ("time", "perf_counter_ns"),
        ("time", "process_time"), ("time", "process_time_ns"),
        ("time", "thread_time"), ("time", "thread_time_ns"),
        ("time", "clock_gettime"), ("time", "clock_gettime_ns"),
        ("time", "clock_getres"), ("time", "get_clock_info"),
        ("datetime.datetime", "now"), ("datetime.datetime", "utcnow"),
        ("datetime.datetime", "today"), ("datetime.date", "today"),
    },
    # node v20.19.6 introspection.
    "javascript": {
        ("Date", "now"), ("performance", "now"), ("performance", "timeOrigin"),
        ("process", "hrtime"), ("process", "uptime"), ("os", "uptime"),
    },
    # glibc headers, symbol-verified.
    # Header names carry no ".h" in these catalogues (cf. "unistd", "sys/wait").
    "c": {
        ("time", "time"), ("time", "clock"),
        ("time", "clock_gettime"), ("time", "clock_getres"),
        ("time", "timespec_get"), ("sys/time", "gettimeofday"),
        ("sys/times", "times"),
    },
    "cpp": {
        ("time", "time"), ("time", "clock"),
        ("time", "clock_gettime"), ("time", "clock_getres"),
        ("std::chrono::system_clock", "now"),
        ("std::chrono::steady_clock", "now"),
        ("std::chrono::high_resolution_clock", "now"),
    },
    # golang/go src/time/time.go — Since/Until sample internally.
    "go": {("time", "Now"), ("time", "Since"), ("time", "Until")},
    # openjdk/jdk System.java + java/time/Instant.java.
    "java": {
        ("java.lang.System", "currentTimeMillis"),
        ("java.lang.System", "nanoTime"),
        ("java.time.Instant", "now"),
        ("java.time.LocalDateTime", "now"),
        ("java.time.LocalDate", "now"),
        ("java.time.LocalTime", "now"),
        ("java.time.ZonedDateTime", "now"),
        ("java.time.OffsetDateTime", "now"),
        ("java.time.Clock", "systemUTC"),
    },
    "kotlin": {
        ("java.lang.System", "currentTimeMillis"),
        ("java.lang.System", "nanoTime"),
        ("java.time.Instant", "now"),
    },
    "scala": {
        ("java.lang.System", "currentTimeMillis"),
        ("java.lang.System", "nanoTime"),
        ("java.time.Instant", "now"),
    },
    # erlang/otp erts/preloaded/src/erlang.erl + lib/kernel/src/os.erl.
    "erlang": {
        ("erlang", "monotonic_time"), ("erlang", "system_time"),
        ("erlang", "timestamp"), ("erlang", "time_offset"),
        ("erlang", "localtime"), ("erlang", "universaltime"),
        ("os", "system_time"), ("os", "perf_counter"), ("os", "timestamp"),
    },
    # elixir-lang/elixir lib/elixir/lib/system.ex (+ the inherited :os rows).
    "elixir": {
        ("System", "monotonic_time"), ("System", "system_time"),
        ("System", "time_offset"), ("System", "os_time"),
        ("DateTime", "utc_now"), ("NaiveDateTime", "utc_now"),
        ("os", "system_time"), ("os", "perf_counter"), ("os", "timestamp"),
    },
    # haskell/time lib/Data/Time/Clock/**, plus base's GHC.Clock.
    "haskell": {
        ("Data.Time.Clock", "getCurrentTime"),
        ("Data.Time.Clock.System", "getSystemTime"),
        ("GHC.Clock", "getMonotonicTime"),
    },
    # swiftlang/swift-corelibs-foundation Sources/Foundation/NSDate.swift.
    "objc": {
        ("NSDate", "date"), ("NSDate", "timeIntervalSinceReferenceDate"),
        ("NSProcessInfo", "systemUptime"),
    },
    "swift": {
        ("Date", "now"), ("ProcessInfo", "systemUptime"),
        ("DispatchTime", "now"),
    },
    # GNU bash 5.2.21, run locally.
    # bash catalogues shell variables as attributes on the "shell" module
    # (cf. the existing shell.hostinfo). date(1) is a COMMAND, and this
    # catalogue models no command surface at all, so it is out of scope here
    # rather than smuggled in as a pseudo-module.
    "bash": {
        ("shell", "SECONDS"), ("shell", "EPOCHSECONDS"),
        ("shell", "EPOCHREALTIME"),
    },
}

# The six catalogues that declared ZERO method-kind rows before WI-tubij.
# Measured 2026-08-27: adding a method-kind row to one of these gives the
# language its FIRST method-keyed module, which admits it to
# ``method_starved_modules`` — and erlang's ``remote``/``local`` and haskell's
# ``application`` call constructs can never satisfy that check, so the row
# would MANUFACTURE the starvation report it exists to help retire (INV-pimir).
# In all six the clock API is genuinely a function or a shell variable, so
# truth and safety coincide and this constraint never forces a false
# description of the surface.
NO_METHOD_KIND = ("bash", "c", "cpp", "elixir", "erlang", "haskell")


def _catalog(language: str) -> io_boundary.IoBoundaryCatalog:
    cat = io_boundary.load_catalog(language)
    assert getattr(cat, "is_supported", False), f"{language} unsupported"
    return cat


def _clock_rows(language: str) -> set[tuple[str, str]]:
    return {
        (p.module, p.name)
        for p in _catalog(language).primitives
        if p.boundary == "host_info_read"
    }


class TestNoLanguageIsSilentlySkipped:
    """The item's own statement: fifteen catalogues, or the word drifts."""

    def test_every_shipped_catalogue_is_covered_by_the_enumeration(self) -> None:
        assert set(LANGUAGES) == set(CLOCK_SURFACE), (
            "a catalogue was added or removed without enumerating its clock "
            "surface — that is exactly the silent skip this item exists to "
            "prevent"
        )

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_the_language_declares_at_least_one_clock_read(
        self, language: str,
    ) -> None:
        assert CLOCK_SURFACE[language] & _clock_rows(language), (
            f"{language} declares no clock row, so host_info_read means "
            f"something different in {language} than it does elsewhere"
        )


class TestTheEnumeratedSurfaceIsPresent:
    @pytest.mark.parametrize("language", LANGUAGES)
    def test_every_enumerated_clock_primitive_is_catalogued(
        self, language: str,
    ) -> None:
        missing = CLOCK_SURFACE[language] - _clock_rows(language)
        assert not missing, f"{language} is missing clock rows: {sorted(missing)}"


class TestTheKeyingConstraintHolds:
    """Measured on WI-tubij; a safety property, not a style preference."""

    @pytest.mark.parametrize("language", NO_METHOD_KIND)
    def test_zero_method_languages_gain_no_method_kind_row(
        self, language: str,
    ) -> None:
        offenders = [
            f"{p.module}.{p.name}"
            for p in _catalog(language).primitives
            if p.kind == "method"
        ]
        assert not offenders, (
            f"{language} declares no method-keyed module today; adding "
            f"{offenders} would admit it to method_starved_modules where its "
            "call constructs can never satisfy route 1 (INV-pimir)"
        )

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_no_clock_row_is_keyed_as_a_kind_the_language_cannot_emit(
        self, language: str,
    ) -> None:
        """Every clock row carries one of the three real IoPrimitive kinds."""
        kinds = {
            p.kind for p in _catalog(language).primitives
            if (p.module, p.name) in CLOCK_SURFACE[language]
        }
        assert kinds <= {"function", "method", "attribute"}, (
            f"{language} clock rows carry unknown kinds: {kinds}"
        )


class TestTheRefusalsStayRefused:
    """Arithmetic on a captured value is not a clock read."""

    REFUSED: ClassVar[dict[str, list[tuple[str, str]]]] = {
        "rust": [("std::time::Instant", "duration_since"),
                 ("std::time::Instant", "checked_add"),
                 ("std::time::SystemTime", "duration_since")],
        "python": [("time", "sleep"), ("time", "mktime"),
                   ("time", "strptime"), ("time", "clock_settime"),
                   ("time", "localtime"), ("time", "gmtime")],
        "c": [("time", "clock_settime"), ("time", "mktime")],
        "go": [("time", "Sleep")],
        "erlang": [("erlang", "convert_time_unit")],
    }

    @pytest.mark.parametrize("language", sorted(REFUSED))
    def test_arithmetic_and_writes_are_not_clock_reads(
        self, language: str,
    ) -> None:
        rows = _clock_rows(language)
        wrongly_added = [r for r in self.REFUSED[language] if r in rows]
        assert not wrongly_added, (
            f"{language} catalogues {wrongly_added} as host_info_read, but "
            "these do arithmetic on a captured value, sleep, or WRITE the "
            "clock — none of them samples it"
        )
