# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shipped taint sinks are only the ones io_primitives cannot derive (ADR-0060).

Commit 51e1d232f3 retired a shipped ``taint_sinks/`` directory because a hand
list of I/O primitives drifted from ``io_primitives/``, which already enumerates
them. ADR-0060 brings the directory back for sinks with NO I/O counterpart --
code execution and DOM injection -- and these gates are what keep it from
regrowing into the retired list.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core import taint as T
from hypergumbo_core.io_boundary import load_catalog

_SHIPPED = sorted((Path(T.__file__).parent / "taint_sinks").glob("*.yaml"))


def _shipped_sinks() -> list[tuple[str, T.TaintSink]]:
    catalog = T.load_taint_catalog([], _SHIPPED, [])
    return [(lang, sink) for lang, sinks in sorted(catalog._sinks.items()) for sink in sinks]


def test_the_directory_ships_and_loads() -> None:
    """Reach first: an empty glob would make every gate below vacuous."""
    zones = {s.zone for _lang, s in _shipped_sinks()}
    assert zones == {"code_execution", "dom_injection"}, zones


def test_no_shipped_sink_sits_in_a_boundary_derived_zone() -> None:
    derived = {zone for zone, _trust in T.AUTO_SINK_ZONE_MAP.values()}
    offenders = [(lang, s.module, s.name, s.zone) for lang, s in _shipped_sinks()
                 if s.zone in derived]
    assert offenders == [], (
        "a shipped sink in a zone io_primitives derives belongs in io_primitives/ "
        f"(51e1d232f3, ADR-0060): {offenders}"
    )


def test_no_shipped_sink_names_a_catalogued_io_primitive() -> None:
    offenders = []
    for lang, s in _shipped_sinks():
        row = load_catalog(lang).lookup_with_module(s.name, s.module, call_construct=s.kind)
        if row is not None and row.module == s.module:
            offenders.append((lang, s.module, s.name, row.boundary))
    assert offenders == [], offenders


def test_the_full_catalogue_carries_them() -> None:
    """The production loader, not only the file parser, reads the directory."""
    catalog = T.load_full_taint_catalog()
    assert any(s.zone == "code_execution" and s.name == "eval" and s.module == "builtins"
               for s in catalog.sinks_for_language("python"))
