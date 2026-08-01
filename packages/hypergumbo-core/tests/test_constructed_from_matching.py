# SPDX-License-Identifier: AGPL-3.0-or-later
"""A framework YAML can key on ``meta["constructed_from"]`` today (WI-nopod).

The whole argument for putting the constructor binding on ``Symbol.meta``
rather than on ``UsageContext`` was that the matcher then needs **no change**
— the existing ``meta_match`` field takes a ``{key: regex}`` map over
``Symbol.meta``. That is a claim about behaviour, so it is asserted here
rather than inferred from reading the matcher.

The alternative designs both cost an ADR ruling: a ``UsageContext`` with a
result position widens ``symbol_ref`` from "the symbol being used" to
"the symbol produced by this call" and adds a fifth pattern category to
ADR-3ccc; a new edge type is ADR-0023's territory. This one costs a
registered meta key.
"""
from __future__ import annotations

from hypergumbo_core.axis_meta_keys import AXIS_SYMBOL_META, find_meta_key
from hypergumbo_core.framework_patterns import Pattern
from hypergumbo_core.ir import Symbol


def _var(name: str, meta: dict[str, str] | None) -> Symbol:
    return Symbol(
        id=f"python:m.py:1-1:{name}:variable",
        name=name,
        kind="variable",
        language="python",
        path="m.py",
        span=None,
        meta=meta,
    )


def test_key_is_registered_on_the_symbol_meta_axis() -> None:
    spec = find_meta_key("constructed_from")
    assert spec is not None, "constructed_from must be in the meta-key registry"
    assert spec.axis == AXIS_SYMBOL_META
    assert len(spec.description) > 80, "a registered key needs a real rationale"


def test_meta_match_selects_a_constructed_object() -> None:
    """The load-bearing claim: an unmodified matcher keys on the new fact."""
    pattern = Pattern(
        concept="asgi_app",
        meta_match={"constructed_from": r"^FastAPI$"},
    )
    assert pattern.matches(_var("app", {"constructed_from": "FastAPI"}))
    assert not pattern.matches(_var("other", {"constructed_from": "Flask"}))
    assert not pattern.matches(_var("plain", None))


def test_meta_match_is_start_anchored_which_shapes_the_yaml_pattern() -> None:
    r"""``meta_match`` applies ``regex.match``, not ``search`` — so a pattern
    for a possibly-qualified callee must be written to match from position 0.

    Measured, not assumed: ``framework_patterns`` calls
    ``regex.match(value_str)``. The natural-looking ``(^|\.)FastAPI$`` fails
    on ``fastapi.FastAPI`` because ``^`` matches empty at position 0 and the
    rest cannot then consume the prefix. ``(.*\.)?FastAPI$`` is the form that
    works, and it is pinned here so the next author keying on a qualified
    callee does not rediscover it the hard way.

    This is the cost of the producer keeping qualification — accepted,
    because stripping it would make a namespaced callee indistinguishable
    from a same-named local, which is unrecoverable rather than merely
    inconvenient.
    """
    naive = Pattern(concept="x", meta_match={"constructed_from": r"(^|\.)FastAPI$"})
    assert naive.matches(_var("a", {"constructed_from": "FastAPI"}))
    assert not naive.matches(_var("b", {"constructed_from": "fastapi.FastAPI"})), (
        "if this ever passes, meta_match switched to search() and the guidance "
        "in this test (and any YAML written against it) needs revisiting"
    )

    correct = Pattern(concept="x", meta_match={"constructed_from": r"(.*\.)?FastAPI$"})
    assert correct.matches(_var("a", {"constructed_from": "FastAPI"}))
    assert correct.matches(_var("b", {"constructed_from": "fastapi.FastAPI"}))
    assert not correct.matches(_var("c", {"constructed_from": "NotFastAPI"}))
