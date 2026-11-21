# SPDX-License-Identifier: AGPL-3.0-or-later
"""The entrypoint-kind vocabulary holds only kinds something produces.

Audit 0027 (owner ruling 2026-11-21) removed 23 ``EntrypointKind`` values
that nothing had produced since #331 removed their detectors: ``cli_main``
(DEPRECATE-NO-FOLD: "this main runs a CLI parser" is a property of a main,
not a kind), the 21 framework-named route / controller kinds (FOLD into
``http_route`` / ``controller``, the framework in ``meta``, which is what
the analyzers already emit) and ``electron_renderer`` (FOLD into
``html_entry`` / ``script_module``). Each kept appearing in the hand-written
kind sets (program start, request ingress, the library-export demotion's
semantic kinds, the route-label dedup, the sketch groups) and in the schema,
so every reader carried a value no map can hold.

These tests pin the removal at every reader that imports the vocabulary:
the catalog (and with it the schema enum), the module-level kind sets, the
sketch's display groups and the taint request-parameter contracts. The
function-local sets (``_SEMANTIC_KINDS``, ``_ROUTE_KINDS``) name members of
the enum, so a member left there fails at import of the module's first call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.entrypoints import (
    PROGRAM_START_KINDS,
    REQUEST_INGRESS_KINDS,
    EntrypointKind,
    all_known_entrypoint_kinds,
)
from hypergumbo_core.sketch import _ENTRYPOINT_GROUPS
from hypergumbo_core.taint import (
    _PARAMETER_SEED_MODULE,
    GENERIC_ROUTE_CONTRACTS,
    REQUEST_PARAMETER_CONTRACTS,
)

#: The 23 values the ruling removes. ``electron_preload`` is kept (a real
#: role that gets a producer); ``go_handler`` is kept (a live producer).
REMOVED = (
    "cli_main",
    "electron_renderer",
    "django_view", "express_route", "nestjs_controller", "spring_controller",
    "rails_controller", "phoenix_controller", "laravel_controller",
    "rust_handler", "aspnet_controller", "sinatra_route", "ktor_route",
    "vapor_route", "plug_route", "hapi_route", "fastify_route", "koa_route",
    "grape_api", "tornado_handler", "aiohttp_view", "slim_route",
    "micronaut_controller",
)

_SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "schema.json"


def test_the_ruling_names_twenty_three_values() -> None:
    assert len(set(REMOVED)) == 23


@pytest.mark.parametrize("value", REMOVED)
def test_a_removed_kind_is_not_in_the_catalog(value: str) -> None:
    assert value not in all_known_entrypoint_kinds()
    assert value.upper() not in EntrypointKind.__members__


@pytest.mark.parametrize("value", REMOVED)
def test_a_removed_kind_is_in_no_kind_set(value: str) -> None:
    assert value not in REQUEST_INGRESS_KINDS
    assert value not in {k.value for k in PROGRAM_START_KINDS}
    for heading, kinds in _ENTRYPOINT_GROUPS:
        assert value not in kinds, heading
    assert value not in REQUEST_PARAMETER_CONTRACTS
    assert value not in _PARAMETER_SEED_MODULE


def test_the_kept_kinds_stay() -> None:
    known = all_known_entrypoint_kinds()
    assert {"electron_preload", "go_handler", "http_route", "controller",
            "html_entry", "script_module"} <= known


def test_program_start_is_the_ruled_set() -> None:
    """The program-start role (audit 0027 owner ruling 2026-11-21); the
    registry's own test pins each ruling."""
    assert PROGRAM_START_KINDS == frozenset({
        EntrypointKind.MAIN_FUNCTION, EntrypointKind.MAIN_GUARD,
        EntrypointKind.ELECTRON_MAIN})


def test_every_kind_a_reader_names_is_a_known_kind() -> None:
    known = all_known_entrypoint_kinds()
    assert REQUEST_INGRESS_KINDS <= known
    for _heading, kinds in _ENTRYPOINT_GROUPS:
        assert kinds <= known
    # A request-parameter contract keyed by kind names a request-ingress
    # kind; the module slot is keyed by such a kind or by a language of the
    # generic route contract.
    assert set(REQUEST_PARAMETER_CONTRACTS) <= REQUEST_INGRESS_KINDS
    assert set(_PARAMETER_SEED_MODULE) <= (
        set(REQUEST_PARAMETER_CONTRACTS) | set(GENERIC_ROUTE_CONTRACTS))


def test_the_published_schema_lists_no_removed_kind() -> None:
    """docs/schema.json is generated from the catalog and the ingress set
    (scripts/generate-schema); a stale file would still publish them."""
    schema = json.loads(_SCHEMA.read_text())
    text = json.dumps(schema)
    for value in REMOVED:
        assert f'"{value}"' not in text, value
        assert f" {value}," not in text and f" {value}\"" not in text, value


def test_every_kind_has_a_producer() -> None:
    """Each member is constructed somewhere in packages/*/src: an
    ``Entrypoint.create(kind=EntrypointKind.X)`` / ``Entrypoint(kind=...)``
    keyword, or a ``target_kind = EntrypointKind.X`` the next create reads.
    A kind nothing produces is the defect audit 0027 removed 23 of."""
    import ast

    produced: set[str] = set()
    root = Path(__file__).resolve().parents[2]
    for src in root.glob("*/src/**/*.py"):
        for node in ast.walk(ast.parse(src.read_text())):
            value = None
            if isinstance(node, ast.keyword) and node.arg == "kind":
                value = node.value
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id == "target_kind"):
                value = node.value
            if value is None:
                continue
            for sub in ast.walk(value):
                if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                        and sub.value.id == "EntrypointKind"):
                    produced.add(sub.attr)
    assert set(EntrypointKind.__members__) - produced == set()
