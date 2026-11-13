# SPDX-License-Identifier: AGPL-3.0-or-later
"""Route-concept parity gate: per-language web-route detection breadth, and the
identity contract the route-marker symbols it finds must satisfy (WI-tosul,
WI-tufil, WI-zugob).

Per-language framework/route detection *coverage* is measured here on
*injected, idiom-verified* per-case fixtures (`fixtures/route-parity/`), never
from self-substrate counts at small N (WI-rubip), and through the FULL pipeline
(`run_behavior_map`, not the isolated analyzer — route enrichment is a pipeline
concern). The gate locks the current state so a breadth sweep ratchets against
it (a fix that closes a hole XPASSes the strict-xfail and forces a flip to
green). Entrypoint *existence* per language is a separate gate,
`test_entrypoint_parity.py`.

Route detection surfaces through three mechanisms, inconsistently by language:

  * `meta['concepts']` `concept=route` — python Flask `@app.route`, rust actix
    `#[get]`, java Spring `@GetMapping`. Manifest-detection-gated: it fires only
    with the framework declared (requirements.txt/pyproject, Cargo.toml, the
    pom).
  * `meta['framework_role']='route'` — go net/http `HandleFunc`
    (analyzer/usage-level; fires with no manifest).
  * analyzer-level `meta['route_path']` + `meta['http_method']` — javascript
    Express `app.get` (NOT a concept; fires with no manifest).

For java, spring-boot's import patterns must include the Spring annotation
namespaces (`org.springframework.web`/`.stereotype`/`.context`): a Spring MVC
controller imports `org.springframework.web.bind.annotation`, not
`org.springframework.boot`, so without them `refine_frameworks` demotes
spring-boot to `dev_frameworks` and `enrich_symbols` never sees
`spring-boot.yaml` (history: WI-tolap).

`_route_detected` therefore accepts route detection via ANY of the three
surfacing mechanisms (`concept=route` / `framework_role=route` / `route_path`
meta) — capturing the real cross-language invariant "this language's canonical
web-route idiom is recognized" without prejudging which mechanism carries it.
The surfacing INCONSISTENCY itself is an INV-numat-family concern; this gate
only locks detection existence per case.

The ratchet: `ROUTE_GREEN` is the hard-locked floor (each entry a real assert).
When the sweep introduces a NEW per-language/framework fixture that does NOT yet
detect, add it to `ROUTE_HOLES` with a strict-xfail so that a later breadth fix
XPASSes and forces the maintainer to promote it into `ROUTE_GREEN`. `ROUTE_HOLES`
is empty because every fixture case detects.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from hypergumbo_core.analyze.registry import ensure_discovered
from hypergumbo_core.cli import run_behavior_map

# Every test here runs on ONE xdist worker, so the module fixture is built
# once per run (smart-test and CI pass --dist=loadgroup; see
# dist_args_for_run in scripts/smart-test).
pytestmark = pytest.mark.xdist_group("test_route_concept_parity")

CORPUS = Path(__file__).resolve().parent / "fixtures" / "route-parity"

# Fixture CASES whose canonical web-route idiom the full pipeline detects today.
#
# A *case* is one directory under ``fixtures/route-parity/``. Some cases are one
# per language, others are language+framework, because a single language can
# reach route detection through several independent producers (``python`` is
# Flask, ``django`` and ``starlette`` are two further py.py paths) and each
# producer needs its own fixture to be gated at all. Each entry is a hard
# assert.
#
# ``go-mount`` is deliberately NOT here: a chi/gorilla ``r.Mount()`` emits
# ``framework_role='route_mount'`` with no ``route_path``, so it is a route
# *mount point*, not a route, and ``_route_detected`` correctly does not count
# it. It exists as a case for the marker-identity properties below.
ROUTE_GREEN = [
    "annotation",
    "django",
    "elixir",
    "flask-restful",
    "go",
    "grpc",
    "java",
    "javascript",
    "js-named",
    "php",
    "play-routes",
    "python",
    "ruby",
    "rust",
    "starlette",
    "swift",
]

# (case) -> strict-xfail reason for a KNOWN route-detection hole.
# A fix that makes the route fire XPASSes (under strict), failing the suite and
# forcing a flip into ROUTE_GREEN — the WI-tosul breadth ratchet. Empty now: all
# fixture cases detect. Add an entry when the sweep introduces a new fixture
# whose route does not yet fire.
ROUTE_HOLES: dict[str, str] = {}


def _route_detected(behavior_map: dict) -> bool:
    """True if any node carries route detection via ANY of the three surfacing
    mechanisms: a ``concept=route`` enrichment, a ``framework_role=route`` tag,
    or analyzer-level ``route_path`` meta."""
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
    """Run the full pipeline once per fixture case; cache the behavior map.

    Uses ``run_behavior_map`` (not the isolated analyzer) because route
    enrichment is a pipeline concern gated on framework detection.

    Each case is COPIED OUT to a tmp dir and analyzed there rather than in
    place. Analyzing under ``…/tests/fixtures/…`` subjects every case to the
    pipeline's own test-path heuristics, which is not the thing this gate is
    trying to measure — and it silently suppresses producers: the grpc linker
    emits its route marker for a ``.proto`` at an ordinary path but not for the
    byte-identical file under ``tests/fixtures/``. A fixture that measures the suppression instead of the producer is a
    vacuous green, so the corpus is analyzed from a neutral location.
    """
    ensure_discovered()
    base = tmp_path_factory.mktemp("route-parity")
    corpus = base / "corpus"
    corpus.mkdir()
    maps: dict = {}
    for case_dir in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        staged = corpus / case_dir.name
        shutil.copytree(case_dir, staged)
        out = base / f"{case_dir.name}.json"
        run_behavior_map(staged, out_path=out, progress=False)
        maps[case_dir.name] = json.loads(out.read_text())
    return maps


@pytest.mark.parametrize("case", ROUTE_GREEN)
def test_route_detected(case: str, route_maps: dict) -> None:
    """The canonical web-route idiom is detected (any surfacing mechanism)."""
    assert _route_detected(route_maps[case]), (
        f"{case}: canonical route idiom in fixtures/route-parity/{case}/ "
        f"produced no route detection (concept / framework_role / route_path)"
    )


# ---------------------------------------------------------------------------
# WI-tufil / WI-zugob — route-marker symbols must satisfy the identity contract
# ---------------------------------------------------------------------------
#
# When a language surfaces a route via a *synthesized route-marker symbol*
# (``meta['framework_role'] in {route, route_mount, route_include}``) rather than
# a ``concept=route`` tag on the handler function, that marker must still satisfy
# the same referential/identity invariants as any other Symbol, each checked by
# its own validator: the id kind slot (id_format), the id name slot, the id
# grammar, and provenance (cross_field for ``origin_run_id``, axis_conformance
# for ``origin``). Marker producers break these in ways a direct ``Symbol(...)``
# constructor makes easy: a role left in the id kind slot, an empty
# ``origin_run_id`` because the producing run's execution_id was never stamped,
# an ``origin`` naming an unregistered pass-id.
#
# Each property gets its own test and its own hole set, parametrized per case:
#
# 1. A gate's UNCHECKED properties are a blind spot exactly as much as an
# unwalked code path; an aggregate test over a few properties hides the rest
# (an aggregate that never checked the name slot reported ``go`` clean while
# it violated it).
# 2. Aggregating over cases means one case's violation cannot be recorded
# without disabling the check for all of them. Per-case parametrization +
# per-property ``*_HOLES`` lets a sweep drain holes one producer at a time.
#
# Record a hole only after running the full pipeline over the fixture, not by
# reading the producer.
#
# Production vs. cleanliness: most cases are ANALYZER- or LINKER-DIRECT (grammar
# or file-shape only, no framework detection) so they are produced in every
# environment. java's and javascript's markers come via
# ``framework_patterns.materialize_route_symbols``, gated on framework detection
# (spring-boot from the pom / express from package.json) whose import-evidence
# step can vary by environment (express undetected means no js marker). Those
# two are therefore exempt from the non-vacuity guard below; the
# property tests still validate every marker they DO produce.
ROUTE_MARKER_CASES = [
    "annotation",
    "django",
    "elixir",
    "flask-restful",
    "go",
    "go-mount",
    "grpc",
    "java",
    "javascript",
    "js-named",
    "php",
    "play-routes",
    "ruby",
    "starlette",
    "swift",
]

# Cases whose marker production depends on framework detection from a manifest,
# which can vary by environment. Exempt from the non-vacuity guard only.
MANIFEST_GATED_CASES = {"java", "javascript"}

# ADR-0036 Ruling 2: the id kind-slot must equal ``Symbol.kind`` and name a
# registered symbol kind. A ``route`` / ``route_mount`` / ``route_include`` /
# ``annotated_route`` kind-slot puts the role in the id while ``Symbol.kind`` is
# ``function`` (the role belongs in meta, ADR-0027).
KIND_SLOT_HOLES: dict[str, str] = {}

# ADR-0036 Ruling 1: a node id's ``{name}`` slot must equal
# ``sanitize_id_name_segment(Symbol.name)``. The route-marker failure shape is
# the id carrying the specific ``"{METHOD} {path}"`` while ``Symbol.name``
# carries the handler name, so a consumer reconstructing a node's name from its
# documented id gets a different string. ``analyze.base.make_route_symbol`` is
# the chokepoint that derives one from the other; a producer is fixed by
# routing through it.
#
# go mints route markers at several sites; the ``go-mount`` case exists because
# the net/http-only ``go`` fixture never exercises the ``r.Mount()`` site, so a
# mount site that bypasses the chokepoint would go unseen (history: PR #64).
NAME_SLOT_HOLES: dict[str, str] = {}

# ADR-0036: the canonical node id is FIVE anchored segments,
# ``{lang}:{path}:{start}-{end}:{name}:{kind}``, whose third slot is a
# ``{start}-{end}`` LINE SPAN. A wrong segment count is a HARD grammar break (an
# id that cannot be parsed back into its slots at all); a malformed span slot is
# a softer one (parseable, but the span cannot be recovered). Both are checked
# here because both are the same property — "this id conforms to the documented
# grammar" — and splitting them would just be another unchecked-property blind
# spot.
CANONICAL_ID_HOLES: dict[str, str] = {}

# Every Symbol carries producer provenance: a non-empty ``origin`` naming
# registered pass-ids, and an ``origin_run_id`` joining a real AnalysisRun.
# ``origin`` must be NON-EMPTY: a "for elem in origin" check passes an empty
# list vacuously, so a producer with no provenance at all would go unseen
# (history: WI-tufil).
ORIGIN_HOLES: dict[str, str] = {}


def _route_marker_nodes(behavior_map: dict) -> list[dict]:
    """Nodes minted as standalone route markers (framework_role route/mount/include)."""
    out: list[dict] = []
    for node in behavior_map.get("nodes", []):
        meta = node.get("meta") or {}
        if meta.get("framework_role") in ("route", "route_mount", "route_include"):
            out.append(node)
    return out


def _case_param(case: str, holes: dict[str, str]):
    """Strict-xfail the cases recorded in ``holes``.

    Deliberately a MARKER, not an imperative ``pytest.xfail()`` call: the
    imperative form always reports xfail and can never XPASS, so it would record
    the hole while silently disabling the ratchet that makes the hole close.
    """
    if case in holes:
        return pytest.param(
            case, marks=pytest.mark.xfail(strict=True, reason=holes[case])
        )
    return pytest.param(case)


def _params(holes: dict[str, str]):
    return [_case_param(case, holes) for case in ROUTE_MARKER_CASES]


@pytest.mark.parametrize(
    "case", [c for c in ROUTE_MARKER_CASES if c not in MANIFEST_GATED_CASES]
)
def test_route_marker_case_produces_markers(case: str, route_maps: dict) -> None:
    """Each analyzer/linker-direct case actually mints a marker.

    Without this, every property test below passes VACUOUSLY the moment a
    producer stops emitting — a green that proves nothing.
    The manifest-gated pair is exempt because their production legitimately
    varies by environment.
    """
    assert _route_marker_nodes(route_maps[case]), (
        f"{case}: fixtures/route-parity/{case}/ produced no route-marker symbol, "
        f"so the identity properties below would pass vacuously (producer "
        f"regression, or the fixture no longer exercises the producer?)"
    )


@pytest.mark.parametrize("case", _params(KIND_SLOT_HOLES))
def test_route_marker_id_kind_slot_round_trips(case: str, route_maps: dict) -> None:
    """Every route-marker's id kind-slot equals Symbol.kind and is registered."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    known_kinds = all_symbol_kind_names()
    for m in _route_marker_nodes(route_maps[case]):
        mid = m["id"]
        kind = m["kind"]
        kind_slot = mid.rsplit(":", 1)[-1]
        assert kind_slot == kind, (
            f"{case}: route-marker id kind-slot {kind_slot!r} != Symbol.kind "
            f"{kind!r} ({mid})"
        )
        assert kind_slot in known_kinds, (
            f"{case}: route-marker id kind-slot {kind_slot!r} is not a "
            f"registered symbol-kind ({mid})"
        )


@pytest.mark.parametrize("case", _params(NAME_SLOT_HOLES))
def test_route_marker_id_name_slot_round_trips(case: str, route_maps: dict) -> None:
    """Every route-marker's id name-slot equals ``sanitize(Symbol.name)``."""
    from hypergumbo_core.analyze.base import sanitize_id_name_segment

    for m in _route_marker_nodes(route_maps[case]):
        mid = m["id"]
        # The id is anchored from both ends: {name} is second-from-last.
        name_slot = mid.rsplit(":", 2)[-2]
        assert name_slot == sanitize_id_name_segment(m.get("name") or ""), (
            f"{case}: route-marker id name-slot {name_slot!r} != "
            f"sanitized(Symbol.name) {m.get('name')!r} ({mid})"
        )


@pytest.mark.parametrize("case", _params(CANONICAL_ID_HOLES))
def test_route_marker_id_is_canonical_five_segment(
    case: str, route_maps: dict
) -> None:
    """Every route-marker id conforms to the ADR-0036 five-segment grammar."""
    for m in _route_marker_nodes(route_maps[case]):
        mid = m["id"]
        segments = mid.split(":")
        assert len(segments) == 5, (
            f"{case}: route-marker id has {len(segments)} segments, not the "
            f"canonical 5 ({{lang}}:{{path}}:{{start}}-{{end}}:{{name}}:{{kind}}) "
            f"— the id cannot be parsed back into its slots ({mid})"
        )
        assert re.fullmatch(r"\d+-\d+", segments[2]), (
            f"{case}: route-marker id span slot {segments[2]!r} is not the "
            f"canonical {{start}}-{{end}} line span ({mid})"
        )


@pytest.mark.parametrize("case", _params(ORIGIN_HOLES))
def test_route_marker_origin_provenance(case: str, route_maps: dict) -> None:
    """Every route-marker names its producing pass and run.

    ``origin`` is non-empty and every element is a registered pass-id
    (axis_conformance); ``origin_run_id`` is non-empty and joins a real
    AnalysisRun (cross_field).
    """
    from hypergumbo_core.catalog import all_known_pass_ids

    known_pass_ids = all_known_pass_ids()
    behavior_map = route_maps[case]
    run_ids = {r.get("execution_id") for r in behavior_map.get("analysis_runs", [])}
    run_ids.discard(None)
    run_ids.discard("")

    for m in _route_marker_nodes(behavior_map):
        mid = m["id"]
        origin = m.get("origin") or []
        assert origin, f"{case}: route-marker {mid} has an empty Symbol.origin"
        for elem in origin:
            assert elem in known_pass_ids, (
                f"{case}: route-marker {mid} origin element {elem!r} is not a "
                f"registered pass-id"
            )
        origin_run_id = m.get("origin_run_id") or ""
        assert origin_run_id, f"{case}: route-marker {mid} has empty origin_run_id"
        assert origin_run_id in run_ids, (
            f"{case}: route-marker {mid} origin_run_id {origin_run_id!r} "
            f"matches no AnalysisRun.execution_id"
        )
