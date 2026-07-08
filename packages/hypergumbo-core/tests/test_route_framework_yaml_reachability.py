# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural invariant: every route-emitting framework YAML is reachable.

INV-fosam. A framework's route patterns only ever apply if
``load_framework_patterns(detected_name)`` resolves to its YAML — the loader maps
``detected_name`` through ``_FRAMEWORK_ALIASES`` and then looks for
``{resolved}.yaml``. When a *route-emitting* framework's detection key differs
from its YAML basename and no alias bridges them, the route YAML silently never
loads and the framework emits ZERO routes **even when it is correctly detected
(manifest or import)** — the alias-mismatch bug INV-fosam names
(``next`` vs ``nextjs.yaml``, ``vert.x`` vs ``vertx.yaml``, …).

The naive invariant "every ``LANGUAGE_FRAMEWORKS`` key resolves to a YAML" is
*wrong*: that dict also carries ML / test / ORM / UI / crypto libraries
(``pytorch``, ``rspec``, ``sqlalchemy``, ``vue``, ``wagmi`` …) detected for
classification, which have no route YAML by design (~140 of 243 keys). The
correct, non-heuristic invariant is the **reverse** one, scoped to the YAMLs that
actually define routes: every YAML that emits a ``route`` concept must be
reachable from at least one detection key (directly or via alias), OR be
explicitly listed as reachable only through manifest/package-name detection
(where the detected name equals the YAML basename, so no alias is needed) or
otherwise consciously out of import-detection scope.

This test catches future key/basename drift structurally: adding a route YAML
without wiring a detection key (or an alias) fails the test unless the orphan is
consciously exempted with a reason.
"""

import os

import yaml

from hypergumbo_core.framework_patterns import (
    _FRAMEWORK_ALIASES,
    get_frameworks_dir,
    load_framework_patterns,
)
from hypergumbo_core.profile import LANGUAGE_FRAMEWORKS

# Route-emitting YAMLs that no *import* detection key currently reaches. These
# are NOT alias mismatches (there is no import key to alias); they are reachable
# only via manifest/package-name detection (detected name == YAML basename), or
# their detection key is a generic shared token (``http``) that cannot alias to a
# single YAML. Verifying/wiring these is tracked separately (INV-fosam follow-up:
# WI-<orphan-route-yaml-detection-keys>); they are exempted here so this
# invariant stays green while still catching NEW drift.
_ROUTE_YAML_NO_IMPORT_KEY: frozenset[str] = frozenset(
    {
        # keyless — reachable only via manifest/package-name detection
        "flask-restful",  # Flask extension; flask-based apps + pip `Flask-RESTful`
        "lumen",  # composer laravel/lumen
        "masonite",  # pip masonite
        "restify",  # npm restify
        "scalatra",  # sbt scalatra
        # generic shared detection key `http` (Zig/std) — cannot alias to one YAML
        "http4k",
        "node-http",
    }
)

# The exact alias mismatches INV-fosam fixes: a route-emitting framework whose
# detection key differs from its YAML basename.
_INV_FOSAM_ALIAS_MISMATCHES = {
    "adonis": "adonisjs",
    "aspnetcore": "aspnet",
    "next": "nextjs",
    "vert.x": "vertx",
    "zio-http": "zio",
}


def _all_yaml_basenames() -> set[str]:
    fdir = get_frameworks_dir()
    return {f[:-5] for f in os.listdir(fdir) if f.endswith(".yaml")}


def _emits_route(basename: str) -> bool:
    """A YAML defines routes iff some pattern emits the ``route`` concept — the
    actual route-emission mechanism the route linkers consume."""
    fdir = get_frameworks_dir()
    with open(fdir / f"{basename}.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return any(pat.get("concept") == "route" for pat in data["patterns"])


def _all_detection_keys() -> set[str]:
    keys: set[str] = set()
    for frameworks in LANGUAGE_FRAMEWORKS.values():
        keys.update(frameworks.keys())
    return keys


def _resolve_to_basename(key: str) -> str:
    return _FRAMEWORK_ALIASES.get(key, key)


def test_every_route_emitting_yaml_is_reachable_or_exempted() -> None:
    """Every route-emitting framework YAML must be reachable from a detection
    key (directly or via alias), or explicitly exempted (INV-fosam)."""
    yaml_basenames = _all_yaml_basenames()
    route_yamls = {b for b in yaml_basenames if _emits_route(b)}

    reachable = {
        _resolve_to_basename(k)
        for k in _all_detection_keys()
        if _resolve_to_basename(k) in yaml_basenames
    }

    orphaned = route_yamls - reachable - _ROUTE_YAML_NO_IMPORT_KEY
    assert not orphaned, (
        "Route-emitting framework YAMLs unreachable from any detection key "
        f"(add a detection key or a _FRAMEWORK_ALIASES entry): {sorted(orphaned)}"
    )


def test_inv_fosam_alias_mismatched_frameworks_resolve() -> None:
    """Each INV-fosam alias-mismatched detection key resolves to its route YAML
    (behavioral lock on the specific fix)."""
    for key, expected_basename in _INV_FOSAM_ALIAS_MISMATCHES.items():
        pattern_def = load_framework_patterns(key)
        assert pattern_def is not None, (
            f"{key!r} resolves to NO framework YAML — its route patterns "
            f"({expected_basename}.yaml) never load (INV-fosam alias mismatch)"
        )


def test_exemption_set_members_are_actually_orphaned_route_yamls() -> None:
    """Guard the exemption set against staleness: every exempted name must be a
    real route-emitting YAML that is genuinely unreachable via an import key —
    otherwise it should be removed from the exemption (keeps the exemption honest
    and catches an exempted YAML that later gets a key)."""
    yaml_basenames = _all_yaml_basenames()
    reachable = {
        _resolve_to_basename(k)
        for k in _all_detection_keys()
        if _resolve_to_basename(k) in yaml_basenames
    }
    for name in _ROUTE_YAML_NO_IMPORT_KEY:
        assert name in yaml_basenames, f"exempted {name!r} is not a YAML (stale)"
        assert _emits_route(name), f"exempted {name!r} does not emit routes (stale)"
        assert name not in reachable, (
            f"exempted {name!r} IS reachable via a detection key now — "
            "remove it from _ROUTE_YAML_NO_IMPORT_KEY"
        )
