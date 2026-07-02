# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tosul route-concept parity gate — the gate-before-sweep for per-language
web-route detection breadth.

WI-tosul's residual (after its entrypoint-*existence* half was locked by
`test_entrypoint_parity.py` and reclassified in the emission-parity matrix) is a
per-language framework/route detection *coverage* problem — the named upstream
cause of dead-code-maybe's "Python monoculture" and the handler-slice HTTP
monoculture. Per the correctness-strategy G2 discipline (and WI-rubip: never
measure this from self-substrate counts at small N), this gate measures it on
*injected, idiom-verified* per-language fixtures run through the FULL pipeline
(`run_behavior_map`, not the isolated analyzer — route enrichment is a pipeline
concern), and locks the current state so a future breadth sweep ratchets against
it (a fix that closes a hole XPASSes the strict-xfail and forces a flip to green).

The 2026-07-01 re-measure (`fixtures/route-parity/`, one canonical web-route
idiom per language) found route detection is **three-mechanism and inconsistently
surfaced**, and one language is an outright gap:

  * python  — Flask `@app.route`      -> `meta['concepts']` `concept=route`
              (manifest-detection-gated: fires only with the framework declared
              in requirements.txt/pyproject — the partial explanation for the
              Python monoculture, since real Python repos reliably declare it)
  * rust    — actix `#[get]`          -> `meta['concepts']` `concept=route`
              (also manifest-gated: needs actix-web in Cargo.toml)
  * go      — net/http `HandleFunc`   -> `meta['framework_role']='route'`
              (analyzer/usage-level; fires with no manifest)
  * javascript — Express `app.get`    -> analyzer-level `meta['route_path']`
              + `meta['http_method']` (NOT a concept; fires with no manifest)
  * java    — Spring `@GetMapping`     -> NOTHING (the gap: spring-boot IS
              detected from the pom and spring-boot.yaml HAS the route pattern,
              yet enrich_symbols tags no route — WI-tolap)

`_route_detected` therefore accepts route detection via ANY of the three
surfacing mechanisms (`concept=route` / `framework_role=route` / `route_path`
meta) — capturing the real cross-language invariant "this language's canonical
web-route idiom is recognized" without prejudging which mechanism carries it.
The surfacing INCONSISTENCY itself is an INV-numat-family concern the WI-tosul
sweep should unify; this gate only locks detection existence per language.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.analyze.registry import ensure_discovered
from hypergumbo_core.cli import run_behavior_map

CORPUS = Path(__file__).resolve().parent / "fixtures" / "route-parity"

# Languages whose canonical web-route idiom the full pipeline detects today
# (measured 2026-07-01, then locked). Each is a hard assert.
ROUTE_GREEN = ["go", "javascript", "python", "rust"]

# (language) -> strict-xfail reason. A fix that makes the route fire XPASSes
# (under strict), failing the suite and forcing a flip into ROUTE_GREEN — the
# WI-tosul breadth ratchet.
ROUTE_HOLES = {
    "java": "WI-tolap: Spring @GetMapping emits no route concept even though "
            "spring-boot is detected and spring-boot.yaml has the route pattern.",
}


def _route_detected(behavior_map: dict) -> bool:
    """True if any node carries route detection via ANY of the three surfacing
    mechanisms the re-measure found: a ``concept=route`` enrichment, a
    ``framework_role=route`` tag, or analyzer-level ``route_path`` meta."""
    for node in behavior_map.get("nodes", []):
        meta = node.get("meta") or {}
        if meta.get("framework_role") == "route":
            return True
        if meta.get("route_path"):
            return True
        for concept in meta.get("concepts") or []:
            name = concept.get("concept") if isinstance(concept, dict) else concept
            if name == "route":
                return True
    return False


@pytest.fixture(scope="module")
def route_maps(tmp_path_factory) -> dict:
    """Run the full pipeline once per fixture language; cache the behavior map.

    Uses ``run_behavior_map`` (not the isolated analyzer) because route
    enrichment is a pipeline concern gated on framework detection — the whole
    point of the re-measure."""
    ensure_discovered()
    base = tmp_path_factory.mktemp("route-parity")
    maps: dict = {}
    for lang_dir in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        out = base / f"{lang_dir.name}.json"
        run_behavior_map(lang_dir, out_path=out, progress=False)
        maps[lang_dir.name] = json.loads(out.read_text())
    return maps


@pytest.mark.parametrize("lang", ROUTE_GREEN)
def test_route_detected(lang: str, route_maps: dict) -> None:
    """The canonical web-route idiom is detected (any surfacing mechanism)."""
    assert _route_detected(route_maps[lang]), (
        f"{lang}: canonical route idiom in fixtures/route-parity/{lang}/ "
        f"produced no route detection (concept / framework_role / route_path)"
    )


@pytest.mark.xfail(strict=True, reason=ROUTE_HOLES["java"])
def test_route_gap_java_still_open(route_maps: dict) -> None:
    """WI-tosul breadth ratchet: Java Spring `@GetMapping` route detection is a
    known gap (WI-tolap). Marked strict-xfail so that closing it (the route now
    fires) XPASSes and forces the maintainer to move ``java`` into ROUTE_GREEN.
    The assert really runs (exercising ``_route_detected`` on the java map), so
    the ratchet is live, not a skipped body."""
    assert _route_detected(route_maps["java"])
