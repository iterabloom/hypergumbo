# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mumov: a declared relation accessor on an UNTYPED root carries the ORM module.

WI-gulaz indexes every relation accessor the project declares, but consults it
only through :meth:`_DjangoReceiverOracle.instance_class` — so the accessor is
recognised only once the ROOT is known to be an instance of the owning class.
Where the root is a bare parameter or an unresolved dotted path, the index is
never reached and the chain loses the module at its first hop.

MEASURED before this change (`~/hypergumbo_lab_notebook/mumov_tip_09102026/`):
of 428 production recall cells in pretix's largest flat residue bucket, 230 turn
on an accessor pretix's own models declare as a ``related_name`` and 20 more on
the default ``<model>_set`` form. A 40-site uniform random read-back against
source found 40 of 40 genuinely Django and 0 not.

THE COLLISION, STATED RATHER THAN SLIPPED THROUGH. WI-gulaz's module docstring
pre-registers "an untyped root" among the shapes that must NOT be typed, and
three of its tests encode it. This module does not pretend otherwise: it
overturns that condition for the no-evidence case and KEEPS it wherever the
analyzer holds positive contrary evidence. The line is:

  * the immediate owner's class is KNOWN and does not declare the accessor
    (``ev.foo.seats`` — ``ev`` is an ``Event``, ``Event`` has no ``foo``)
    => still refused. Knowing the owner and finding no such relation is
    evidence AGAINST, and relaxing that would be the false-all-clear direction.
  * nothing is known about the root at all (``event.seats`` where ``event`` is
    an untyped parameter) => typed, because the accessor name is one THIS
    PROJECT declares and the module slot needs only "a Django manager", not
    which model.

The MODEL stays unknown: ``manager_model`` still returns None here, so the
instance-binding path is unaffected and no wrong model is stamped. Widening the
slot must not widen the binding (LIVE.md rule 20 — partial evidence that
suppresses a fallback is worse than none).

SAFETY BY CONSTRUCTION: the index is built from the project's own
``models.Model`` subclasses, so in a non-Django repository it is empty and this
rule cannot fire at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, analyze_python

MODELS = '''\
from django.db import models


class Organizer(models.Model):
    pass


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, related_name="events", on_delete=models.CASCADE)


class Seat(models.Model):
    event = models.ForeignKey(Event, related_name="seats", on_delete=models.CASCADE)


class Payment(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
'''

VIEWS = '''\
from models import Event


def helper():
    pass


def untyped(request, event, order):
    event.seats.filter(row=1).exists()
    request.event.seats.filter(row=2).exists()
    order.events.filter(live=True).count()
    event.payment_set.filter(ok=True).count()
    event.not_a_relation.filter(x=1).count()
    ev = Event.objects.get(pk=1)
    ev.foo.seats.count()
    request.thing.helper.count()
'''


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


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
def project_edges(tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
    return _edges(tmp_path_factory.mktemp("untypedroot"), {"models.py": MODELS, "views.py": VIEWS})


class TestADeclaredAccessorOnAnUnknownRoot:
    """The 250-site population: nothing is known about the root either way."""

    def test_bare_parameter_root(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "event.seats.filter(row=1)")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_unresolved_dotted_root(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "request.event.seats.filter(row=2)")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_accessor_owned_by_a_different_model_still_types(self, project_edges: list[Edge]) -> None:
        """The slot needs "a Django manager", not WHICH model — so an accessor
        declared anywhere in the project types even on a mismatched root."""
        line = _line_of(VIEWS, "order.events.filter(live=True)")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE

    def test_default_model_set_form(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "event.payment_set.filter(ok=True)")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE


class TestTheRefutationConditionThatSurvives:
    """WI-gulaz's refusal is KEPT wherever there is positive contrary evidence."""

    def test_a_name_the_project_never_declares_is_still_refused(
        self, project_edges: list[Edge]
    ) -> None:
        line = _line_of(VIEWS, "event.not_a_relation.filter(x=1)")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_a_known_owner_that_does_not_declare_the_hop_is_still_refused(
        self, project_edges: list[Edge]
    ) -> None:
        """``ev`` IS an Event and Event has no ``foo`` — evidence AGAINST, so the
        accessor beyond it must not be trusted. This is the half of WI-gulaz's
        pre-registered refutation that this change does NOT overturn."""
        line = _line_of(VIEWS, "ev.foo.seats.count()")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_a_project_function_name_is_not_an_accessor(
        self, project_edges: list[Edge]
    ) -> None:
        line = _line_of(VIEWS, "request.thing.helper.count()")
        assert _slot_at(project_edges, line, "count") == "external"
