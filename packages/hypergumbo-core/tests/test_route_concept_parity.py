# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tosul route-concept parity gate — the gate-before-sweep for per-language
web-route detection breadth, and (WI-tufil / WI-zugob) the identity contract the
route-marker symbols it finds must satisfy.

WI-tosul's residual (after its entrypoint-*existence* half was locked by
`test_entrypoint_parity.py` and reclassified in the emission-parity matrix) is a
per-language framework/route detection *coverage* problem — the named upstream
cause of dead-code-maybe's "Python monoculture" and the handler-slice HTTP
monoculture. Per the correctness-strategy G2 discipline (and WI-rubip: never
measure this from self-substrate counts at small N), this gate measures it on
*injected, idiom-verified* per-case fixtures run through the FULL pipeline
(`run_behavior_map`, not the isolated analyzer — route enrichment is a pipeline
concern), and locks the current state so a future breadth sweep ratchets against
it (a fix that closes a hole XPASSes the strict-xfail and forces a flip to green).

The 2026-07-01 re-measure (`fixtures/route-parity/`, one canonical web-route
idiom per language) found route detection is **three-mechanism and inconsistently
surfaced**:

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
  * java    — Spring `@GetMapping`     -> `meta['concepts']` `concept=route`
              (manifest-gated on the pom). This was the WI-tolap gap: spring-boot
              was detected from the pom but `refine_frameworks`' demote phase
              dropped it to `dev_frameworks` because its import patterns
              (`org.springframework.boot`) did not match a Spring MVC controller's
              actual annotation import (`org.springframework.web.bind.annotation`),
              which starved `enrich_symbols` of `spring-boot.yaml`. Closed by
              broadening spring-boot's import patterns to the Spring annotation
              namespaces (`org.springframework.web`/`.stereotype`/`.context`).

`_route_detected` therefore accepts route detection via ANY of the three
surfacing mechanisms (`concept=route` / `framework_role=route` / `route_path`
meta) — capturing the real cross-language invariant "this language's canonical
web-route idiom is recognized" without prejudging which mechanism carries it.
The surfacing INCONSISTENCY itself is an INV-numat-family concern the WI-tosul
sweep should unify; this gate only locks detection existence per case.

The ratchet: `ROUTE_GREEN` is the hard-locked floor (each entry a real assert).
When the sweep introduces a NEW per-language/framework fixture that does NOT yet
detect, add it to `ROUTE_HOLES` with a strict-xfail so that a later breadth fix
XPASSes and forces the maintainer to promote it into `ROUTE_GREEN`. `ROUTE_HOLES`
is empty now because every fixture case detects.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from hypergumbo_core.analyze.registry import ensure_discovered
from hypergumbo_core.cli import run_behavior_map

CORPUS = Path(__file__).resolve().parent / "fixtures" / "route-parity"

# Fixture CASES whose canonical web-route idiom the full pipeline detects today.
#
# A *case* is one directory under ``fixtures/route-parity/``. The original five
# are one-per-language; the cases added 2026-07-29 (WI-zugob) are
# language+framework, because a single language can reach route detection
# through several independent producers (``python`` is Flask, ``django`` and
# ``starlette`` are two further py.py paths) and each producer needs its own
# fixture to be gated at all.
#
# Measured 2026-07-01; java added 2026-07-02 (WI-tolap); the ten framework cases
# added 2026-07-29 (WI-zugob). Each entry is a hard assert.
#
# ``go-mount`` is deliberately NOT here: a chi/gorilla ``r.Mount()`` emits
# ``framework_role='route_mount'`` with no ``route_path``, so it is a route
# *mount point*, not a route, and ``_route_detected`` correctly does not count
# it. It exists as a case for the marker-identity properties below.
ROUTE_GREEN = [
    "annotation",
    "django",
    "elixir",
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
    """Run the full pipeline once per fixture case; cache the behavior map.

    Uses ``run_behavior_map`` (not the isolated analyzer) because route
    enrichment is a pipeline concern gated on framework detection — the whole
    point of the re-measure.

    Each case is COPIED OUT to a tmp dir and analyzed there rather than in
    place. Analyzing under ``…/tests/fixtures/…`` subjects every case to the
    pipeline's own test-path heuristics, which is not the thing this gate is
    trying to measure — and it silently suppressed a producer: the grpc linker
    emits its route marker for a ``.proto`` at an ordinary path but not for the
    byte-identical file under ``tests/fixtures/`` (measured 2026-07-29; in-repo
    ``.probe-grpc/`` → 4 markers, ``tests/fixtures/route-parity/grpc/`` → 0).
    A fixture that measures the suppression instead of the producer is a
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
# the same referential/identity invariants as any other Symbol. The WI-tosul
# re-measure (WI-tolap increment) found the trio go/js/java markers violated
# three validators at once:
#   * id_format        — the id kind-slot was the literal ``route`` while
#                        ``Symbol.kind`` is ``function`` (the ADR-0027 Phase-3
#                        fold set kind=function but left the id-slot a ``route``
#                        fossil), and ``route`` is not a registered symbol-kind;
#   * cross_field      — the framework_patterns materializer minted js/java
#                        markers with an empty ``origin_run_id`` (WI-mosil
#                        regression: a direct ``Symbol(...)`` constructor that
#                        never stamped the producing run's execution_id);
#   * axis_conformance — ``Symbol.origin`` named an unregistered pass-id
#                        (``route-materializer`` / ``django-cbv-method-expander``).
#
# WI-tufil closed that for the trio and locked it with ONE AGGREGATE test over
# three languages. 2026-07-29 (WI-zugob) replaced that aggregate with the
# per-case property tests below, for two reasons the aggregate structurally
# could not serve:
#
#   1. It checked three properties and never the id NAME-slot. Adding that one
#      assertion put ``go`` — the analyzer-direct producer the gate leans on as
#      its "the checks actually ran" anchor — in violation, inside the very
#      cohort WI-tufil had declared clean. A gate's UNCHECKED properties are a
#      blind spot exactly as much as an unwalked code path, so each property now
#      gets its own test and its own hole set.
#   2. Aggregating over languages means one language's violation cannot be
#      recorded without disabling the check for all of them. Per-case
#      parametrization + per-property ``*_HOLES`` lets the fleet sweep drain a
#      measured number one producer at a time.
#
# Every hole below was MEASURED on 2026-07-29 by running the full pipeline over
# these fixtures, not inferred from reading the producers.
#
# Production vs. cleanliness: most cases are ANALYZER- or LINKER-DIRECT (grammar
# or file-shape only, no framework detection) so they are produced in every
# environment. java's and javascript's markers come via
# ``framework_patterns.materialize_route_symbols``, gated on framework detection
# (spring-boot from the pom / express from package.json) whose import-evidence
# step can vary by environment (an earlier CI run had express undetected → no js
# marker). Those two are therefore exempt from the non-vacuity guard below; the
# property tests still validate every marker they DO produce.
ROUTE_MARKER_CASES = [
    "annotation",
    "django",
    "elixir",
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
# ``annotated_route`` kind-slot is the ADR-0027 Phase-3 fossil: the fold set
# ``kind="function"`` but left the role in the id.
KIND_SLOT_HOLES: dict[str, str] = {
    "annotation": "annotation_convention linker mints an 'annotated_route' id kind-slot (WI-zugob)",
    "elixir": "elixir.py mints a 'route' id kind-slot fossil (WI-zugob)",
    "go-mount": "go.py r.Mount() mints a 'route_mount' id kind-slot fossil (WI-zugob)",
    "grpc": "grpc linker mints a 'route' id kind-slot fossil (WI-zugob)",
    "js-named": "js_ts.py mints a 'route' id kind-slot fossil (WI-zugob)",
    "php": "php.py mints a 'route' id kind-slot fossil (WI-zugob)",
    "play-routes": "play_routes.py mints 'route'/'route_include' id kind-slot fossils (WI-zugob)",
    "ruby": "ruby.py mints a 'route' id kind-slot fossil (WI-zugob)",
    "swift": "swift.py mints a 'route' id kind-slot fossil (WI-zugob)",
}

# ADR-0036 Ruling 1: a node id's ``{name}`` slot must equal
# ``sanitize_id_name_segment(Symbol.name)``. Route-marker producers drifted here
# in the INVERTED direction from WI-vuzaf Pattern A: the id carries the specific
# ``"{METHOD} {path}"`` while ``Symbol.name`` carries the handler name, so a
# consumer reconstructing a node's name from its documented id gets a different
# string. ``analyze.base.make_route_symbol`` is the chokepoint that derives one
# from the other; a producer is fixed by routing through it.
#
# go was promoted out on migration (PR #64) — its four inline minting sites now
# route through the chokepoint. Its FIFTH site (``r.Mount()``) was missed by that
# PR and is the ``go-mount`` case here, which is exactly why the fixture exists:
# the original go fixture is net/http only and never exercised the mount path.
NAME_SLOT_HOLES: dict[str, str] = {
    "django": "py.py mints name='django:{view}' against a route-path id name-slot (WI-zugob)",
    "go-mount": "go.py r.Mount() mints name=handler_ref against a 'MOUNT {prefix}' id name-slot (WI-zugob)",
    "starlette": "py.py mints name='starlette:{view}' against a '{METHOD} {path}' id name-slot (WI-zugob)",
}

# ADR-0036: the canonical node id is FIVE anchored segments,
# ``{lang}:{path}:{start}-{end}:{name}:{kind}``, whose third slot is a
# ``{start}-{end}`` LINE SPAN. A wrong segment count is a HARD grammar break (an
# id that cannot be parsed back into its slots at all); a malformed span slot is
# a softer one (parseable, but the span cannot be recovered). Both are checked
# here because both are the same property — "this id conforms to the documented
# grammar" — and splitting them would just be another unchecked-property blind
# spot.
CANONICAL_ID_HOLES: dict[str, str] = {
    "annotation": "annotation_convention mints a raw 4-segment f-string id with no language slot (WI-zugob)",
    "django": "the CBV expander concatenates a colon-bearing name into the name slot -> 6 segments (WI-javag)",
    "grpc": "grpc linker mints a bare line number in the span slot instead of {start}-{end} (WI-zugob)",
}

# Every Symbol carries producer provenance: a non-empty ``origin`` naming
# registered pass-ids, and an ``origin_run_id`` joining a real AnalysisRun.
# WI-tufil closed the ``origin_run_id`` half for the trio; an EMPTY ``origin``
# list passed its "for elem in origin" check vacuously, which is how elixir's
# total absence of provenance survived.
ORIGIN_HOLES: dict[str, str] = {
    "elixir": "elixir.py route markers carry an empty Symbol.origin (WI-zugob)",
}


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
    producer stops emitting — a green that proves nothing (the pt.35 lesson).
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
