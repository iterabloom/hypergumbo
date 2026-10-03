# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Python stdlib line is the union over every supported interpreter
(WI-gisan, WI-jojuz; ADR-0041 §3)."""

from __future__ import annotations

import sys

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.python_stdlib_versions import (
    PYTHON_STDLIB_VERSION_WINDOWS,
    SUPPORTED_PYTHONS,
    in_window,
    supported_versions_label,
)


def test_the_windows_match_the_running_interpreter() -> None:
    """The windows are a claim about CPython. Every nightly leg checks it
    against the interpreter it runs, so a wrong window fails somewhere real."""
    here = sys.version_info[:2]
    wrong = sorted(
        name for name in PYTHON_STDLIB_VERSION_WINDOWS
        if (name in sys.stdlib_module_names) != in_window(name, here)
    )
    assert wrong == [], (
        f"PYTHON_STDLIB_VERSION_WINDOWS disagrees with Python "
        f"{here[0]}.{here[1]} for {wrong}"
    )


def test_a_window_is_inclusive_at_both_ends() -> None:
    assert not in_window("tomllib", (3, 10))
    assert in_window("tomllib", (3, 11))
    assert in_window("distutils", (3, 11))
    assert not in_window("distutils", (3, 12))
    assert in_window("cgi", (3, 12))
    assert not in_window("cgi", (3, 13))


def test_every_window_name_is_stdlib_on_some_supported_python() -> None:
    """A row here that no supported Python ships would be a typo, not a window."""
    assert all(
        any(in_window(name, v) for v in SUPPORTED_PYTHONS)
        for name in PYTHON_STDLIB_VERSION_WINDOWS
    )


def test_python_yaml_recognises_every_supported_interpreter_s_stdlib() -> None:
    """WI-gisan: generated from 3.12 alone, python.yaml called distutils, imp,
    asyncore, asynchat, smtpd and binhex third-party, although the 3.10/3.11
    runtime ships them (ADR-0041 §3: ``stdlib`` = shipped with the runtime)."""
    catalog = load_catalog("python")
    missing = sorted(
        name for name in PYTHON_STDLIB_VERSION_WINDOWS
        if not catalog.is_stdlib_module(name)
    )
    assert missing == [], missing
    assert catalog.is_stdlib_module("distutils.spawn")
    assert not catalog.is_stdlib_module("requests")


def test_python_yaml_declares_the_versions_its_stdlib_line_covers() -> None:
    provenance = load_catalog("python").stdlib_provenance or {}
    assert provenance.get("supported_versions") == supported_versions_label()
    assert supported_versions_label() == "3.10-3.13"


def test_recognising_distutils_as_stdlib_does_not_examine_it() -> None:
    """The safety argument, pinned (ADR-0041 §3 "Not a consumer"; INV-buzab).
    Recognition is a LABEL. distutils spawns processes and has no audited I/O
    surface, so a call into it still withholds a clean verdict."""
    from hypergumbo_core.verify_claims import compute_boundary_coverage

    catalog = load_catalog("python")
    assert catalog.is_stdlib_module("distutils.spawn")
    assert not catalog.module_io_is_enumerated("distutils.spawn")
    coverage = compute_boundary_coverage(
        [{
            "src": "python:app.py:1-5:handler:function",
            "dst": "python:distutils.spawn:0-0:spawn:external_symbol",
            "type": "calls",
        }],
        {"python"},
        {"python": catalog},
    )
    assert coverage.complete is False
    assert "distutils.spawn" in coverage.reason
