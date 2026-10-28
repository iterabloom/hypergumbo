# SPDX-License-Identifier: AGPL-3.0-or-later
"""A claim on a zone with no sink for a present language is withheld, for EVERY built-in zone (WI-sojok).

The per-language empty-zone gate (INV-pivam) judged only ADR-0060's zones,
``code_execution`` and ``dom_injection``, on the premise that an I/O zone's
sinks come from the catalogue the no-catalogue gate already checks. That gate
asks whether a LANGUAGE has a taint catalogue, not whether it has a sink in
the claim's zone: Go has none in ``database``, and the shipped
"untrusted input never reaches a database" claim read ``confirmed`` on a Go
repo that does ``db.Exec(input)`` -- a clean verdict on a zone the analysis
cannot see in that language, while a ``code_execution`` claim on the same repo
was withheld.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.taint import AUTO_SINK_ZONE_MAP, builtin_sink_zones

_GO = '''package main

import (
\t"database/sql"
\t"fmt"
)

var db *sql.DB

func main() {
\tvar v string
\tfmt.Scanln(&v)
\tdb.Exec(v)
}
'''


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module x\n")
    (repo / "main.go").write_text(_GO)
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: c\n    text: t\n    constraint:\n      taint_flow:\n"
        f"        source_taint: untrusted_input\n        prohibited_sink_zone: {zone}\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_the_builtin_zones_are_the_io_and_non_boundary_ones() -> None:
    zones = builtin_sink_zones()
    assert {zone for zone, _ in AUTO_SINK_ZONE_MAP.values()} <= zones
    assert {"code_execution", "dom_injection", "database"} <= zones


def test_a_database_claim_on_go_is_withheld(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(tmp_path, monkeypatch, "database")
    assert verdict["verdict"] == "inconclusive"
    assert "no sink in the 'database' zone" in verdict["details"]


def test_control_a_zone_go_has_sinks_in_is_not_withheld_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, "network")
    assert "no sink in the 'network' zone" not in verdict["details"]


def test_a_project_declared_zone_is_not_judged(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from hypergumbo_core.cli import _sink_zone_gaps

    claim = SimpleNamespace(constraint_taint_flow=SimpleNamespace(
        source_taint="untrusted_input", prohibited_sink_zone="relay"))
    catalog = SimpleNamespace(sinks_for_language=lambda lang: [])
    assert _sink_zone_gaps([claim], catalog, {"go"}, {}) == {}
