# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-hovoj: a comprehension or lambda binding is a NEW name, not the outer one.

The untyped-root rule (INV-mumov) types ``root.payment_set.count()`` as a Django
manager call when ``payment_set`` is a declared reverse accessor and nothing is
known about ``root``. Its guard refuses a root that THIS block assigned but could
not type (``label = Order.LABELS.get("a")``: a dict value, not a model). The
guard read the block's assigned names unpruned inside a comprehension or lambda,
so ``[label.payment_set.count() for label in events]`` after that assignment was
refused: the comprehension's own ``label`` is a fresh binding over ``events``,
the same unknown root the rule types in a function without the assignment. The
``var_types`` half of the boundary was already pruned (INV-ruluv); this pins the
``assigned_names`` half. A free read of the OUTER name inside a comprehension
that does not rebind it stays refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, analyze_python

MODELS = '''
from django.db import models


class Order(models.Model):
    LABELS = {"a": "x"}
    name = models.CharField(max_length=10)


class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
'''

VIEWS = '''
from models import Order


def shadowed_comprehension(events):
    label = Order.LABELS.get("a")
    return [label.payment_set.count() for label in events]


def shadowed_lambda(events):
    label = Order.LABELS.get("a")
    return list(map(lambda label: label.payment_set.exists(), events))


def unshadowed_comprehension(events):
    return [ev.payment_set.count() for ev in events]


def free_read_in_comprehension(events):
    label = Order.LABELS.get("a")
    return [label.payment_set.first() for ev in events]


def top_level_dict_value(event):
    label = Order.LABELS.get("a")
    return label.payment_set.last()
'''


def _line_of(src: str, needle: str) -> int:
    for i, text in enumerate(src.splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(needle)


def _slot_at(edges: list[Edge], line: int, method: str) -> str | None:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.line == line and e.dst.split(":")[3] == method
    ]
    assert len(hits) <= 1, [e.dst for e in hits]
    return hits[0].dst.split(":")[1] if hits else None


@pytest.fixture(scope="module")
def edges(tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
    root = tmp_path_factory.mktemp("hovoj")
    (root / "models.py").write_text(MODELS)
    (root / "views.py").write_text(VIEWS)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
        return analyze_python(root).edges


@pytest.mark.parametrize("needle,method", [
    ("for label in events", "count"),
    ("lambda label:", "exists"),
])
def test_a_subscope_binding_shadowing_an_untyped_outer_name_is_typed(
    edges: list[Edge], needle: str, method: str,
) -> None:
    assert _slot_at(edges, _line_of(VIEWS, needle), method) == DJANGO_ORM_MODULE


def test_control_unshadowed_comprehension_is_typed(edges: list[Edge]) -> None:
    assert _slot_at(edges, _line_of(VIEWS, "for ev in events"), "count") == DJANGO_ORM_MODULE


@pytest.mark.parametrize("needle,method", [
    ("label.payment_set.first() for ev", "first"),
    ("return label.payment_set.last()", "last"),
])
def test_the_outer_dict_value_is_still_refused(
    edges: list[Edge], needle: str, method: str,
) -> None:
    """The guard's own case: a Name this block bound to a non-model, read
    at top level or as a FREE variable inside a comprehension that does not
    rebind it."""
    assert _slot_at(edges, _line_of(VIEWS, needle), method) == "external"
