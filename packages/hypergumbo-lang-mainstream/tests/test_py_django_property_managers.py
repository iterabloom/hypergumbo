# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-valav (absorbs WI-tabig): a Django manager or QuerySet reached through a
``@property`` carries ``django.db.models``.

:class:`DjangoRelationIndex` reads relation DECLARATIONS -- ``related_name=``,
the ``<model>_set`` convention, a manager constructor in a model body. A model
that exposes a manager through a property declares nothing the index reads::

    @property
    def fees(self):
        return self.all_fees(manager='objects')

so ``order.fees.filter(...)`` landed in the ``external`` slot while the
identical ``order.all_fees.filter(...)`` was typed. pretix has four such
properties (``Order.fees`` / ``Order.positions`` / ``OrderPosition.checkins`` /
``Customer.stored_addresses``); INV-mumov's shuffled-index ablation measured
``fees`` alone at +32 ORM edges.

THE RULE ADJUDICATES ON THE GETTER'S RETURN EXPRESSION, NEVER ON ITS NAME. A
getter is admitted only when its body holds exactly ONE ``return``, and that
expression is, under the same oracle every other Django receiver goes through:

  * a manager root reached from a TYPED root (``self.all_fees``,
    ``Model.objects``) -- the name-only accessor rule is not evidence enough to
    type a second name off;
  * that manager CALLED with only ``manager=`` (the RelatedManager's
    ``__call__``, which returns a manager of the same relation); or
  * a QuerySet-preserving chain off either (``self.all_fees.filter(...)``).

Everything else refuses: ``.count()`` / ``.exists()`` / ``.first()`` (an int, a
bool, an instance), a getter with two returns, a root typed by name alone. A
related manager keeps its write surface (``add`` / ``remove`` / ``clear`` /
``set``); a QuerySet does not have one and does not get it.

Only Django MODEL classes are indexed, because the name-keyed half of the rule
(``declares_accessor_anywhere``) widens with this index, and a view or form
property is not a declaration about the schema.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import (
    DJANGO_ORM_MODULE,
    DjangoRelationIndex,
    analyze_python,
)

MODELS = '''\
import functools
from functools import cached_property

from django.db import models


class Seat(models.Model):
    pass


class Order(models.Model):
    @property
    def fees(self):
        """Related manager for all non-canceled fees."""
        return self.all_fees(manager='objects')

    @property
    def fee_manager(self):
        return self.all_fees

    @property
    def paid_fees(self):
        return self.all_fees.filter(paid=True)

    @property
    def unpaid_paid_fees(self):
        return self.paid_fees.exclude(refunded=True)

    @cached_property
    def open_fees(self):
        return OrderFee.objects.filter(open=True)

    @property
    def fee_count(self):
        return self.all_fees.count()

    @property
    def first_fee(self):
        return self.all_fees.first()

    @property
    def maybe_fees(self):
        if not self.pk:
            return None
        return self.all_fees.all()

    @property
    def guessed_seats(self):
        return self.request.event.seats.all()

    @property
    def positional_fees(self):
        return self.all_fees('objects')

    @functools.cached_property
    def dotted_open_fees(self):
        return self.all_fees.exclude(open=False)

    @staticmethod
    def static_fees():
        return OrderFee.objects.all()

    @property
    def unknown_called(self):
        return self.not_a_relation(manager='objects')

    @property
    def label(self):
        return "order"

    @label.setter
    def label(self, value):
        pass


class OrderFee(models.Model):
    order = models.ForeignKey(Order, related_name="all_fees", on_delete=models.CASCADE)


class Invoice(models.Model):
    @property
    def fees(self):
        return self.invoice_fees.filter(paid=True)


class InvoiceFee(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="invoice_fees", on_delete=models.CASCADE)


class Placement(models.Model):
    seat = models.ForeignKey(Seat, related_name="seats", on_delete=models.CASCADE)


class AbstractPosition(models.Model):
    addon_to = models.ForeignKey("self", related_name="addons", on_delete=models.CASCADE)

    @property
    def live_addons(self):
        return self.addons.filter(canceled=False)

    @property
    def subclass_checkins(self):
        return self.all_checkins(manager='objects')

    class Meta:
        abstract = True


class OrderPosition(AbstractPosition):
    @property
    def checkins(this):
        def _unused():
            return None
        return this.all_checkins(manager='objects')


class Checkin(models.Model):
    position = models.ForeignKey(
        OrderPosition, related_name="all_checkins", on_delete=models.CASCADE,
    )


class Exporter:
    @property
    def rows(self):
        return OrderFee.objects.filter(open=True)
'''

VIEWS = '''\
from models import Exporter, Order, OrderPosition


def typed(order: Order, op: OrderPosition, exporter: Exporter, f):
    order.fees.filter(a=1)
    order.fees.create(a=1)
    order.fees.add(f)
    order.fee_manager.all()
    order.paid_fees.filter(a=1)
    order.paid_fees.add(f)
    order.unpaid_paid_fees.filter(a=1)
    order.open_fees.filter(a=1)
    order.fee_count.filter(a=1)
    order.first_fee.filter(a=1)
    order.maybe_fees.filter(a=1)
    order.guessed_seats.filter(a=1)
    order.positional_fees.filter(a=1)
    order.label.filter(a=1)
    order.dotted_open_fees.filter(a=1)
    order.static_fees.filter(a=1)
    order.unknown_called.filter(a=1)
    op.checkins.order_by("-datetime")
    op.live_addons.filter(a=1)
    op.subclass_checkins.filter(a=1)
    exporter.rows.filter(a=1)
    order.fees.exclude(a=1).first()
    order.all_fees.filter(a=1)


def untyped(thing, f):
    thing.fees.filter(a=1)
    thing.fees.add(f)
    thing.checkins.all()
    thing.fee_count.filter(a=1)
    thing.rows.filter(a=1)
    guessed = thing.fees.get(pk=1)
    guessed.save()


def bound(order: Order):
    fee = order.fees.get(pk=1)
    fee.save()
'''


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _line_of(src: str, needle: str) -> int:
    hits = [i for i, text in enumerate(src.splitlines(), 1) if needle in text]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


def _orm_edge(edges: list[Edge], line: int, method: str) -> Edge | None:
    """The one ``calls`` edge to ``method`` at ``line`` in views.py."""
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.line == line
        and e.src.split(":")[1].endswith("views.py")
        and e.dst.split(":")[3] == method
    ]
    assert len(hits) <= 1, [e.dst for e in hits]
    return hits[0] if hits else None


def _slot(edges: list[Edge], needle: str, method: str) -> str | None:
    edge = _orm_edge(edges, _line_of(VIEWS, needle), method)
    return edge.dst.split(":")[1] if edge is not None else None


@pytest.fixture(scope="module")
def edges(tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
    return _edges(
        tmp_path_factory.mktemp("propmgr"), {"models.py": MODELS, "views.py": VIEWS},
    )


class TestReach:
    """Assert reach FIRST: the fixture's own control must type."""

    def test_the_declared_accessor_itself_types(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.all_fees.filter(a=1)", "filter") == DJANGO_ORM_MODULE

    def test_the_views_file_produced_edges(self, edges: list[Edge]) -> None:
        assert _orm_edge(edges, _line_of(VIEWS, "order.label.filter"), "filter") is not None


class TestAdmittedReturnShapes:
    def test_called_related_manager(self, edges: list[Edge]) -> None:
        """pretix ``Order.fees``: ``self.all_fees(manager='objects')``."""
        edge = _orm_edge(edges, _line_of(VIEWS, "order.fees.filter(a=1)"), "filter")
        assert edge is not None and edge.dst.split(":")[1] == DJANGO_ORM_MODULE
        assert (edge.meta or {}).get("framework_dispatch") == "django_orm"

    def test_called_related_manager_write(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.fees.create(a=1)", "create") == DJANGO_ORM_MODULE

    def test_a_related_manager_keeps_its_write_surface(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.fees.add(f)", "add") == DJANGO_ORM_MODULE

    def test_bare_related_manager(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.fee_manager.all()", "all") == DJANGO_ORM_MODULE

    def test_queryset_chain(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.paid_fees.filter(a=1)", "filter") == DJANGO_ORM_MODULE

    def test_a_queryset_has_no_related_write_surface(self, edges: list[Edge]) -> None:
        """``QuerySet.add`` does not exist: the RelatedManager rows stay off."""
        assert _slot(edges, "order.paid_fees.add(f)", "add") != DJANGO_ORM_MODULE

    def test_dotted_cached_property(self, edges: list[Edge]) -> None:
        assert _slot(
            edges, "order.dotted_open_fees.filter(a=1)", "filter",
        ) == DJANGO_ORM_MODULE

    def test_a_property_over_a_property(self, edges: list[Edge]) -> None:
        assert _slot(
            edges, "order.unpaid_paid_fees.filter(a=1)", "filter",
        ) == DJANGO_ORM_MODULE

    def test_cached_property_objects_chain(self, edges: list[Edge]) -> None:
        assert _slot(edges, "order.open_fees.filter(a=1)", "filter") == DJANGO_ORM_MODULE

    def test_pretix_checkins(self, edges: list[Edge]) -> None:
        """pretix ``OrderPosition.checkins`` (WI-tabig). The fixture also
        spells the instance parameter ``this`` and nests a ``def`` with its own
        ``return``: neither is the getter's return."""
        assert _slot(
            edges, 'op.checkins.order_by("-datetime")', "order_by",
        ) == DJANGO_ORM_MODULE

    def test_inherited_from_an_abstract_base(self, edges: list[Edge]) -> None:
        """A property declared on an abstract base over an accessor the base
        owns is owned by the concrete subclass (the lineage walk)."""
        assert _slot(edges, "op.live_addons.filter(a=1)", "filter") == DJANGO_ORM_MODULE

    def test_the_chain_carries_on(self, edges: list[Edge]) -> None:
        assert _slot(
            edges, "order.fees.exclude(a=1).first()", "first",
        ) == DJANGO_ORM_MODULE

    def test_a_typed_root_is_stamped_type_inferred(self, edges: list[Edge]) -> None:
        edge = _orm_edge(edges, _line_of(VIEWS, "order.fees.create(a=1)"), "create")
        assert edge is not None
        assert (edge.meta or {}).get("resolution_quality") == "type_inferred"


class TestRefusedReturnShapes:
    """The REFUTATION condition: a getter whose return is not a manager or a
    QuerySet keeps the ``external`` slot, whatever it is called."""

    @pytest.mark.parametrize("needle", [
        "order.fee_count.filter(a=1)",       # .count() -> int
        "order.first_fee.filter(a=1)",       # .first() -> instance
        "order.maybe_fees.filter(a=1)",      # two returns
        "order.guessed_seats.filter(a=1)",   # root typed by NAME only
        "order.positional_fees.filter(a=1)", # positional arg, not manager=
        "order.label.filter(a=1)",           # not ORM at all
        # An abstract base's getter over an accessor only its SUBCLASS owns is
        # adjudicated on the class that declares it, which does not own it.
        "op.subclass_checkins.filter(a=1)",
        "exporter.rows.filter(a=1)",         # not a model class
        "order.static_fees.filter(a=1)",     # not a property
        "order.unknown_called.filter(a=1)",  # manager= on a non-manager
    ])
    def test_refused(self, edges: list[Edge], needle: str) -> None:
        assert _orm_edge(edges, _line_of(VIEWS, needle), "filter") is not None
        assert _slot(edges, needle, "filter") != DJANGO_ORM_MODULE


class TestUntypedRoot:
    """INV-mumov's name-only rule extends to a model property the index
    admitted, and stamps the weaker provenance so the disclosure sees it."""

    def test_an_admitted_property_name_types_on_an_untyped_root(
        self, edges: list[Edge],
    ) -> None:
        edge = _orm_edge(edges, _line_of(VIEWS, "thing.fees.filter(a=1)"), "filter")
        assert edge is not None and edge.dst.split(":")[1] == DJANGO_ORM_MODULE
        assert (edge.meta or {}).get("resolution_quality") == "accessor_name"

    def test_an_inherited_property_name_types_on_an_untyped_root(
        self, edges: list[Edge],
    ) -> None:
        assert _slot(edges, "thing.checkins.all()", "all") == DJANGO_ORM_MODULE

    def test_a_name_with_mixed_kinds_keeps_no_related_write_surface(
        self, edges: list[Edge],
    ) -> None:
        """``fees`` is a RelatedManager on ``Order`` and a QuerySet on
        ``Invoice``: with nothing known about the root, ``add`` -- which only
        the first has -- is not typed off the name."""
        assert _orm_edge(edges, _line_of(VIEWS, "thing.fees.add(f)"), "add") is not None
        assert _slot(edges, "thing.fees.add(f)", "add") != DJANGO_ORM_MODULE

    @pytest.mark.parametrize("needle", [
        "thing.fee_count.filter(a=1)",  # refused getter: name never enters
        "thing.rows.filter(a=1)",       # non-model property: name never enters
    ])
    def test_a_refused_getter_name_stays_untyped(
        self, edges: list[Edge], needle: str,
    ) -> None:
        assert _orm_edge(edges, _line_of(VIEWS, needle), "filter") is not None
        assert _slot(edges, needle, "filter") != DJANGO_ORM_MODULE


class TestInstanceBinding:
    def test_a_get_through_the_property_binds_the_model(self, edges: list[Edge]) -> None:
        """``fee = order.fees.get(...)`` binds ``fee`` to ``OrderFee``, so its
        ``save()`` is the ORM instance write (WI-gamas)."""
        assert _slot(edges, "    fee.save()", "save") == DJANGO_ORM_MODULE

    def test_a_name_only_root_binds_no_model(self, edges: list[Edge]) -> None:
        """Widening the SLOT must not widen the BINDING: off an untyped root the
        manager is typed by name, but which model it yields is unknown, so
        ``guessed`` stays untyped and its ``save()`` is not the ORM write."""
        assert _slot(edges, "guessed = thing.fees.get(pk=1)", "get") == DJANGO_ORM_MODULE
        assert _orm_edge(edges, _line_of(VIEWS, "guessed.save()"), "save") is not None
        assert _slot(edges, "guessed.save()", "save") != DJANGO_ORM_MODULE


class TestNoModelsNoRule:
    def test_the_same_property_source_without_django_types_nothing(
        self, tmp_path: Path,
    ) -> None:
        models = (
            "class Order:\n"
            "    @property\n"
            "    def fees(self):\n"
            "        return self.all_fees(manager='objects')\n"
        )
        views = "def f(thing):\n    thing.fees.filter(a=1)\n"
        edges = _edges(tmp_path, {"models.py": models, "views.py": views})
        hits = [
            e for e in edges if e.edge_type == "calls" and e.line == 2
            and e.dst.split(":")[3] == "filter"
        ]
        assert hits and all(e.dst.split(":")[1] != DJANGO_ORM_MODULE for e in hits)

    def test_an_empty_index_declares_no_property(self) -> None:
        assert DjangoRelationIndex().declares_accessor_anywhere("fees") is False
