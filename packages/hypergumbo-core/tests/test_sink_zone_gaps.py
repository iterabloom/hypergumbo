# SPDX-License-Identifier: AGPL-3.0-or-later
"""Injection-zone claims are judged per language (INV-pivam, ADR-0060).

``code_execution`` and ``dom_injection`` are not derived from an I/O boundary,
so a language can carry a full taint catalogue and still have no sink in them,
and some shapes of them emit no call edge. Before this, a claim over either
zone read a plain ``confirmed`` in both cases. Now a present language with no
sink in the zone withholds the verdict, and declared unreached shapes qualify
a clean one. The end-to-end JS/Go cases live in lang-mainstream's
``test_injection_zone_coverage.py``; these cover the core pieces in isolation.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypergumbo_core.cli import _sink_zone_gaps, main
from hypergumbo_core.taint import shipped_non_boundary_sink_zones
from hypergumbo_core.verify_claims import (
    CAVEAT_UNREACHED_SINK_SHAPES,
    ClaimVerdict,
    SinkZoneGap,
    _apply_sink_zone_gap,
)


def _claim(label: str, zone: str | None) -> SimpleNamespace:
    tf = None if zone is None else SimpleNamespace(source_taint=label, prohibited_sink_zone=zone)
    return SimpleNamespace(constraint_taint_flow=tf)


class _Catalog:
    def __init__(self, zones_by_lang: dict[str, set[str]]) -> None:
        self._zones = zones_by_lang

    def sinks_for_language(self, lang: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(zone=z) for z in self._zones.get(lang, set())]


def _verdict(verdict: str = "confirmed") -> ClaimVerdict:
    return ClaimVerdict(claim_id="C", claim_text="t", verdict=verdict, details="No flow.")


def test_the_shipped_zones_and_their_unreached_shapes() -> None:
    zones = shipped_non_boundary_sink_zones()
    assert set(zones) == {"code_execution", "dom_injection"}
    assert any("new Function" in s for s in zones["code_execution"]["javascript"])
    assert any("innerHTML" in s for s in zones["dom_injection"]["typescript"])
    assert "python" not in zones["code_execution"]


def test_gaps_per_claim() -> None:
    catalog = _Catalog({"python": {"code_execution", "host_fs"},
                        "javascript": {"code_execution", "dom_injection"},
                        "go": {"host_fs"}})
    claims = [
        _claim("host_secret", None),                  # a boundary claim: skipped
        _claim("host_secret", "host_fs"),             # an I/O zone: not judged here
        _claim("host_secret", "code_execution"),
        _claim("host_secret", "code_execution"),      # the same key: computed once
        _claim("build_input", "code_execution"),      # a python-only label
        _claim("host_secret", "dom_injection"),
    ]
    gaps = _sink_zone_gaps(claims, catalog, {"python", "javascript", "go"},
                           {"build_input": frozenset({"python"})})
    ce = gaps[("host_secret", "code_execution")]
    assert ce.uncovered_languages == ("go",)
    assert [lang for lang, _ in ce.unreached_shapes] == ["javascript"]
    assert ("build_input", "code_execution") not in gaps   # python covers it, no shapes
    assert gaps[("host_secret", "dom_injection")].uncovered_languages == ("go", "python")
    assert ("host_secret", "host_fs") not in gaps


def test_an_uncovered_language_withholds_a_clean_verdict() -> None:
    gap = SinkZoneGap(zone="code_execution", uncovered_languages=("go",), unreached_shapes=())
    out = _apply_sink_zone_gap(_verdict(), gap)
    assert out.verdict == "inconclusive"
    assert "language(s) go has no sink in the 'code_execution' zone" in out.details
    two = SinkZoneGap(zone="dom_injection", uncovered_languages=("go", "python"),
                      unreached_shapes=())
    assert "go, python have no sink" in _apply_sink_zone_gap(_verdict(), two).details


def test_an_unreached_shape_qualifies_a_clean_verdict() -> None:
    gap = SinkZoneGap(zone="code_execution", uncovered_languages=(),
                      unreached_shapes=(("javascript", ("new Function(s)",)),))
    out = _apply_sink_zone_gap(_verdict(), gap)
    assert out.verdict == "confirmed_with_caveats"
    (caveat,) = out.caveats
    assert caveat["kind"] == CAVEAT_UNREACHED_SINK_SHAPES
    assert caveat["entries"] == ["javascript: new Function(s)"]
    assert caveat["zone"] == "code_execution"


def test_a_found_or_withheld_verdict_and_an_empty_gap_are_untouched() -> None:
    gap = SinkZoneGap(zone="code_execution", uncovered_languages=("go",), unreached_shapes=())
    for verdict in ("violated", "inconclusive"):
        assert _apply_sink_zone_gap(_verdict(verdict), gap).verdict == verdict
    empty = SinkZoneGap(zone="code_execution", uncovered_languages=(), unreached_shapes=())
    assert _apply_sink_zone_gap(_verdict(), empty).verdict == "confirmed"


def test_python_has_no_dom_sink_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the CLI: the gate is wired, and an I/O-zone claim beside it is not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return eval('1 + 1')\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n"
        "  - id: DOM\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: dom_injection\n"
        "  - id: CE\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: host_secret\n        prohibited_sink_zone: code_execution\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    assert verdicts["DOM"]["verdict"] == "inconclusive", verdicts["DOM"]["details"]
    assert "python" in verdicts["DOM"]["details"]
    assert verdicts["CE"]["verdict"] in ("confirmed", "confirmed_with_caveats")
    assert CAVEAT_UNREACHED_SINK_SHAPES not in [c["kind"] for c in verdicts["CE"]["caveats"]]
