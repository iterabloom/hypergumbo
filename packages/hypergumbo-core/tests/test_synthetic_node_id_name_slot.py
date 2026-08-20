# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-trip check: synthetic-node ``id`` name-slot == ``sanitize(Symbol.name)``.

WI-vuzaf Pattern A / ADR-0036 Ruling 1. Six linker families mint synthetic
``call_site`` / ``function`` stand-in Symbols (subprocess, database query, HTTP
client, GraphQL client, GraphQL resolver, message-queue publish/subscribe).
Historically each stuffed a *generic linker-category constant*
(``subprocess_call`` / ``db_query`` / ``http_client`` / ``graphql_client`` /
``resolver`` / ``mq_publisher``) into the ``{name}`` slot of the canonical
``{lang}:{path}:{span}:{name}:{kind}`` id, while ``Symbol.name`` carried the
*specific* value (``git config`` / ``SELECT users`` / ``GET /api/users`` / …).
A consumer reconstructing a node's name by parsing its documented stable id then
got a different string than ``Symbol.name`` — collapsing e.g. all subprocess
call-sites under the single label ``subprocess_call``.

ADR-0036 Ruling 1 fixes the contract: the id name slot MUST equal
``Symbol.name`` sanitized ``':' -> '.'`` (the round-trip is documented-lossy —
full fidelity lives in ``Symbol.name``). The producer-side landing here is that
each factory routes ``Symbol.name`` (not the category constant) through
``sanitize_id_name_segment`` into the id name slot.

As of WI-sikar the sanitization is ALSO always-on inside ``make_symbol_id``, so
the two layers agree and the substitution is idempotent. The earlier decision to
keep the fold out of the chokepoint rested on a premise measurement overturned —
see ``test_make_symbol_id_sanitizes_the_name_slot_globally``. The per-factory
routing stays because it is what pins the *right value* into the slot
(``Symbol.name``, not a category constant); the chokepoint only guarantees the
slot is colon-free. This module locks both.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id, sanitize_id_name_segment
from hypergumbo_core.linkers.database_query import (
    DatabaseQueryPattern,
    _create_query_symbol,
)
from hypergumbo_core.linkers.graphql import (
    GraphQLClientCall,
    _create_client_symbol as _create_graphql_client_symbol,
)
from hypergumbo_core.linkers.graphql_resolver import (
    ResolverPattern,
    _create_resolver_symbol,
)
from hypergumbo_core.linkers.http import (
    HttpClientCall,
    _create_client_symbol as _create_http_client_symbol,
)
from hypergumbo_core.linkers.message_queue import (
    MessageQueuePattern,
    _create_symbol as _create_mq_symbol,
)
from hypergumbo_core.linkers.subprocess_cli import (
    SubprocessCall,
    _create_call_symbol,
)

_ROOT = Path("/repo")


def _name_slot(symbol_id: str) -> str:
    """The ``{name}`` slot of a canonical id (second-from-last colon segment).

    ``kind`` and (post-Ruling-1) ``name`` are colon-free, so an
    ``rsplit(':', 2)`` from the right recovers the name slot even when the
    ``path`` slot itself contains colons.
    """
    return symbol_id.rsplit(":", 2)[-2]


def test_sanitize_id_name_segment_replaces_colons():
    """The scoped helper maps ``':'`` -> ``'.'`` (ADR-0036 Ruling 1)."""
    assert sanitize_id_name_segment("kafka:publish:topic") == "kafka.publish.topic"
    assert sanitize_id_name_segment("GET http://svc:8080/x") == "GET http.//svc.8080/x"
    assert sanitize_id_name_segment("no-colons here") == "no-colons here"


def test_make_symbol_id_sanitizes_the_name_slot_globally():
    """The chokepoint sanitizes ``':' -> '.'`` in the name slot (WI-sikar).

    This REPLACES an earlier guard that asserted the opposite. That guard's
    stated reason was that an always-on rewrite "would silently churn
    colon-bearing source ids like Obj-C selectors" — which reads as
    *currently-good ids become churned*. Measuring the objc analyzer showed the
    reverse: those ids were **already** broken, because the canonical parse is
    anchored from the right and a colon in the name slot shifts every anchor.

        name  'Manager.removeItemAtPath:error:'
        before objc:M.m:4-5:Manager.removeItemAtPath:error::method   -> 7 segments
        after  objc:M.m:4-5:Manager.removeItemAtPath.error.:method   -> 5, canonical

    So selector-style languages are the largest beneficiaries of the fold, not
    its casualties. Colons in the ``path`` slot stay verbatim — Rust's
    ``std::cmp`` ids depend on that, and the right-anchored parse tolerates them.
    """
    sid = make_symbol_id("objc", "M.m", 4, 5, "Manager.removeItemAtPath:error:", "method")
    assert sid == "objc:M.m:4-5:Manager.removeItemAtPath.error.:method"
    assert len(sid.split(":")) == 5


def test_make_symbol_id_leaves_path_slot_colons_intact():
    """Only the NAME slot is sanitized; a colon-bearing path is legal."""
    sid = make_symbol_id("rust", "std::cmp", 0, 0, "min", "external_symbol")
    assert sid == "rust:std::cmp:0-0:min:external_symbol"
    # Right-anchored parse still recovers the three trailing slots.
    span, name, kind = sid.split(":")[-3:]
    assert (span, name, kind) == ("0-0", "min", "external_symbol")


def test_make_symbol_id_sanitization_is_idempotent():
    """Producers that already sanitize (WI-vuzaf stand-ins) are unaffected."""
    pre = sanitize_id_name_segment("kafka:publish:topic")
    assert make_symbol_id("python", "p.py", 1, 1, pre, "call_site") == (
        make_symbol_id("python", "p.py", 1, 1, "kafka:publish:topic", "call_site")
    )


def _subprocess_symbol():
    return _create_call_symbol(
        SubprocessCall(
            executable="git", subcommand="config", line=4, file_path="/repo/runner.py"
        ),
        _ROOT,
    )


def _database_symbol():
    return _create_query_symbol(
        DatabaseQueryPattern(
            query_text="SELECT * FROM users",
            tables=["users"],
            line=3,
            file_path="/repo/app.py",
            language="python",
            query_type="SELECT",
        ),
        _ROOT,
    )


def _http_symbol():
    # A full URL carries colons (scheme + port) — exercises sanitization.
    return _create_http_client_symbol(
        HttpClientCall(
            method="GET",
            url="http://svc:8080/api/users",
            line=5,
            file_path="/repo/client.py",
            language="python",
        ),
        _ROOT,
    )


def _graphql_client_symbol():
    return _create_graphql_client_symbol(
        GraphQLClientCall(
            operation_type="query",
            operation_name="GetUser",
            query_text="{ user { id } }",
            line=2,
            file_path="/repo/q.py",
            language="python",
        ),
        _ROOT,
    )


def _resolver_symbol():
    return _create_resolver_symbol(
        ResolverPattern(
            type_name="Query",
            field_name="users",
            line=7,
            file_path="/repo/schema.py",
            language="python",
        ),
        _ROOT,
    )


def _mq_symbol():
    # name = "kafka:publish:events" — two literal colons, exercises sanitization.
    return _create_mq_symbol(
        MessageQueuePattern(
            type="publish",
            topic="events",
            line=9,
            file_path="/repo/producer.py",
            language="python",
            queue_type="kafka",
        ),
        _ROOT,
    )


_FACTORIES = {
    "subprocess": _subprocess_symbol,
    "database_query": _database_symbol,
    "http_client": _http_symbol,
    "graphql_client": _graphql_client_symbol,
    "resolver": _resolver_symbol,
    "message_queue": _mq_symbol,
}


def test_synthetic_node_id_name_slot_equals_sanitized_name():
    """Every synthetic-node factory writes ``sanitize(Symbol.name)`` into the id."""
    for label, factory in _FACTORIES.items():
        sym = factory()
        assert _name_slot(sym.id) == sanitize_id_name_segment(sym.name), (
            f"{label}: id name slot {_name_slot(sym.id)!r} != "
            f"sanitize(name) {sanitize_id_name_segment(sym.name)!r} (id={sym.id!r})"
        )
        # The name slot is colon-free by construction (grammar invariant).
        assert ":" not in _name_slot(sym.id), label


def test_synthetic_node_name_slot_not_category_constant():
    """Guard the specific regression: the slot is no longer the category token."""
    constants = {
        "subprocess": "subprocess_call",
        "database_query": "db_query",
        "http_client": "http_client",
        "graphql_client": "graphql_client",
        "resolver": "resolver",
    }
    for label, constant in constants.items():
        sym = _FACTORIES[label]()
        assert _name_slot(sym.id) != constant, f"{label} still emits the category constant"
