# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gulaz (INV-mumov candidate 2): Django reverse-relation managers and
custom manager names carry ``django.db.models``.

WI-sozoj's marker is the literal ``.objects`` attribute, so every Django
manager reached any other way was invisible: the REVERSE-RELATION manager
(``order.payments.create(...)`` where ``Payment.order = ForeignKey(Order,
related_name="payments")``), the forward many-to-many manager
(``event.sponsors.add(s)``), the default ``<model>_set`` accessor, and a
class-level manager under another name (pretix's soft-delete
``Checkin.all.filter(...)``). Measured on pretix before this change: 5,441
call sites on a related-name-like accessor and 73 on a custom manager name,
none typed.

The fix is a PROJECT-WIDE index of relation declarations, built once per
repository and keyed by class-symbol id, consulted by the same two consumers
the ``.objects`` marker already has: the chain root rule in
``_preserved_receiver_type`` and the bounded emission in ``_process_call``.
The manager itself stays untyped (Phase 6 PR 1's false-owner rule); its
QuerySet-returning members carry the module, its closed method set emits.

THE REFUTATION CONDITION, pre-registered: an accessor-like attribute on a
receiver whose resolved class does NOT own it must not be typed -- a
serializer field named like an accessor, a ``@property`` that returns a
QuerySet, an untyped root. Ownership walks the project bases, so an accessor
declared on an abstract base (``ForeignKey("self", related_name="addons")``)
is owned by the concrete subclass.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import (
    DJANGO_ORM_MANAGER_METHODS,
    DJANGO_ORM_MODULE,
    DJANGO_RELATED_MANAGER_WRITE_METHODS,
    analyze_python,
)


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _slot_at(edges: list[Edge], line: int, method: str) -> str | None:
    """The module slot of the one ``calls`` edge to ``method`` at ``line``."""
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.line == line
        and e.dst.split(":")[3] == method
    ]
    assert len(hits) <= 1, [e.dst for e in hits]
    return hits[0].dst.split(":")[1] if hits else None


def _line_of(src: str, needle: str) -> int:
    for i, text in enumerate(src.splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(needle)


MODELS = '''\
from django.db import models
from django_scopes import ScopedManager


class Organizer(models.Model):
    pass


class Sponsor(models.Model):
    name = models.CharField(max_length=10)


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, related_name="events", on_delete=models.CASCADE)
    sponsors = models.ManyToManyField("Sponsor", related_name="sponsored_events")
    twin = models.OneToOneField("self", related_name="twin_of", on_delete=models.CASCADE)

    def first_seat(self):
        return self.seats.first()

    def close_siblings(self):
        self.organizer.events.filter(live=True).update(live=False)

    def sponsor_names(self):
        return [s.name for s in self.sponsors.all()]


class Seat(models.Model):
    event = models.ForeignKey("Event", related_name="seats", on_delete=models.CASCADE)


class Order(models.Model):
    LABELS = {"a": "A"}
    all = ScopedManager(organizer="organizer")
    objects = models.Manager()


class Payment(models.Model):
    order = models.ForeignKey("Order", on_delete=models.CASCADE)
    refund_of = models.ForeignKey("self", related_name="+", on_delete=models.CASCADE)


class AbstractPosition(models.Model):
    addon_to = models.ForeignKey("self", related_name="addons", on_delete=models.CASCADE)

    class Meta:
        abstract = True


class OrderPosition(AbstractPosition):
    pass
'''

VIEWS = '''\
from models import Event, Order, OrderPosition, Organizer


def helper():
    pass


def seats(request, organizer: Organizer):
    ev = Event.objects.get(pk=1)
    for s in ev.seats.all():
        pass
    ev.seats.create(number=1)
    ev.sponsors.add(request.sponsor)
    organizer.events.filter(live=True).exists()
    o = Order.all.get(pk=2)
    o.payment_set.count()
    o2 = Order.objects.filter(x=1).first()
    o2.payment_set.exists()
    label = Order.LABELS.get("a")
    label.payment_set.count()
    pos = OrderPosition.objects.get(pk=3)
    pos.addons.all().delete()
    p, created = Order.objects.get_or_create(pk=4)
    p.payment_set.first()
    sp = ev.sponsors.create(name="x")
    sp.sponsored_events.count()
    Order.all.filter(pk=1).update(x=2)
    qs = Order.objects.filter(x=1)
    o3 = qs.first()
    o3.payment_set.count()
    request.order = Order.objects.get(pk=9)
    ev.foo.seats.count()
    request.thing.seats.create(number=2)
    make().seats.create(number=3)
    helper.all.filter(x=1)
'''


@pytest.fixture(scope="module")
def project_edges(tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
    return _edges(tmp_path_factory.mktemp("relmgr"), {"models.py": MODELS, "views.py": VIEWS})


class TestReverseRelationManagers:
    """``<instance>.<related_name>.<method>()`` carries the ORM module."""

    def test_self_accessor_inside_the_target_model(self, project_edges: list[Edge]) -> None:
        line = _line_of(MODELS, "self.seats.first()")
        assert _slot_at(project_edges, line, "first") == DJANGO_ORM_MODULE

    def test_self_forward_fk_then_accessor(self, project_edges: list[Edge]) -> None:
        """``self.organizer.events`` -- the forward FK field types ``self.organizer``
        as an Organizer, which owns ``events``."""
        line = _line_of(MODELS, "self.organizer.events.filter")
        assert _slot_at(project_edges, line, "update") == DJANGO_ORM_MODULE

    def test_forward_many_to_many_is_a_manager(self, project_edges: list[Edge]) -> None:
        line = _line_of(MODELS, "for s in self.sponsors.all()")
        assert _slot_at(project_edges, line, "__iter__") == DJANGO_ORM_MODULE

    def test_instance_bound_from_objects_get(self, project_edges: list[Edge]) -> None:
        """``ev = Event.objects.get(...)`` binds ``ev`` to Event, so ``ev.seats`` is
        a manager: the ``for`` evaluates it and ``create`` writes through it."""
        assert _slot_at(project_edges, _line_of(VIEWS, "for s in ev.seats.all()"), "__iter__") == DJANGO_ORM_MODULE
        assert _slot_at(project_edges, _line_of(VIEWS, "ev.seats.create"), "create") == DJANGO_ORM_MODULE

    def test_many_to_many_add_is_a_write(self, project_edges: list[Edge]) -> None:
        assert _slot_at(project_edges, _line_of(VIEWS, "ev.sponsors.add"), "add") == DJANGO_ORM_MODULE

    def test_annotated_parameter(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "organizer.events.filter")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_default_model_set_accessor(self, project_edges: list[Edge]) -> None:
        """``Payment.order`` declares no related_name, so Order owns ``payment_set``."""
        line = _line_of(VIEWS, "o.payment_set.count()")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE

    def test_instance_bound_from_a_chained_first(self, project_edges: list[Edge]) -> None:
        """``Order.objects.filter(...).first()`` walks back to its manager root."""
        line = _line_of(VIEWS, "o2.payment_set.exists()")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_instance_bound_from_get_or_create_tuple(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "p.payment_set.first()")
        assert _slot_at(project_edges, line, "first") == DJANGO_ORM_MODULE

    def test_instance_bound_from_a_related_manager_create(self, project_edges: list[Edge]) -> None:
        """``sp = ev.sponsors.create(...)`` yields a Sponsor, which owns the reverse
        M2M accessor ``sponsored_events``."""
        line = _line_of(VIEWS, "sp.sponsored_events.count()")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE

    def test_accessor_declared_on_an_abstract_base(self, project_edges: list[Edge]) -> None:
        """``AbstractPosition.addon_to = ForeignKey("self", related_name="addons")``:
        ownership walks the project bases, so OrderPosition owns ``addons``."""
        line = _line_of(VIEWS, "pos.addons.all().delete()")
        assert _slot_at(project_edges, line, "delete") == DJANGO_ORM_MODULE

    def test_a_bound_queryset_variable_loses_its_model(self, project_edges: list[Edge]) -> None:
        """``qs = Order.objects.filter(...); o3 = qs.first()``: the QuerySet type
        carries no model, so ``o3`` is not bound (a stated limit, not a defect)."""
        line = _line_of(VIEWS, "o3.payment_set.count()")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_an_attribute_target_binds_nothing(self, project_edges: list[Edge]) -> None:
        """``request.order = Order.objects.get(...)`` has no Name to bind."""
        line = _line_of(VIEWS, "request.order = Order.objects.get(pk=9)")
        assert _slot_at(project_edges, line, "get") == DJANGO_ORM_MODULE

    def test_a_non_relation_field_on_a_typed_instance(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "ev.foo.seats.count()")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_deeper_untyped_roots(self, project_edges: list[Edge]) -> None:
        assert _slot_at(project_edges, _line_of(VIEWS, "request.thing.seats.create"), "create") == "external"
        assert _slot_at(project_edges, _line_of(VIEWS, "make().seats.create"), "create") == "external"


CROSS_FILE = {
    "organizers.py": (
        "from django.db import models\n"
        "\n"
        "class Organizer(models.Model):\n"
        "    pass\n"
    ),
    "events.py": (
        "from django.db import models\n"
        "import organizers\n"
        "from organizers import Organizer\n"
        "from querysets import EventQuerySet\n"
        "\n"
        "class Event(models.Model):\n"
        "    organizer = models.ForeignKey(Organizer, related_name='events', on_delete=models.CASCADE)\n"
        "    published = EventQuerySet.as_manager()\n"
        "    visible = models.Manager.from_queryset(EventQuerySet)()\n"
        "\n"
        "class Venue(models.Model):\n"
        "    organizer = models.ForeignKey(organizers.Organizer, related_name='venues', on_delete=models.CASCADE)\n"
    ),
    "tickets.py": (
        "from django.db import models\n"
        "\n"
        "class Ticket(models.Model):\n"
        "    organizer = models.ForeignKey('Organizer', related_name='tickets', on_delete=models.CASCADE)\n"
    ),
    "twins.py": (
        "from django.db import models\n"
        "\n"
        "class Twin(models.Model):\n"
        "    pass\n"
        "\n"
        "class Twin(models.Model):\n"
        "    pass\n"
        "\n"
        "class Pair(models.Model):\n"
        "    twin = models.ForeignKey('Twin', related_name='pairs', on_delete=models.CASCADE)\n"
    ),
    "diamond.py": (
        "from django.db import models\n"
        "\n"
        "class A(models.Model):\n"
        "    pass\n"
        "\n"
        "class B(A):\n"
        "    pass\n"
        "\n"
        "class C(A):\n"
        "    pass\n"
        "\n"
        "class D(B, C):\n"
        "    pass\n"
    ),
    "querysets.py": "class EventQuerySet:\n    pass\n",
    "views.py": (
        "from organizers import Organizer\n"
        "from events import Event\n"
        "from twins import Twin\n"
        "from diamond import D\n"
        "\n"
        "def f():\n"
        "    org = Organizer.objects.get(pk=1)\n"
        "    org.events.count()\n"
        "    org.venues.count()\n"
        "    org.tickets.count()\n"
        "    Event.published.filter(x=1).exists()\n"
        "    Event.visible.filter(x=1).exists()\n"
        "    t = Twin.objects.get(pk=1)\n"
        "    t.pairs.count()\n"
        "    d = D.objects.get(pk=1)\n"
        "    d.nothing.count()\n"
    ),
}


class TestTargetResolutionAcrossFiles:
    """The relation index resolves a target the way Django does, in the
    declaring file, and refuses what it cannot pin to one class."""

    @pytest.fixture(scope="class")
    def edges(self, tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
        return _edges(tmp_path_factory.mktemp("xfile"), CROSS_FILE)

    def test_import_bound_name_target(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "org.events.count()"), "count") == DJANGO_ORM_MODULE

    def test_module_attribute_target(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "org.venues.count()"), "count") == DJANGO_ORM_MODULE

    def test_string_target_resolved_by_unique_repo_wide_short_name(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "org.tickets.count()"), "count") == DJANGO_ORM_MODULE

    def test_as_manager_and_from_queryset_managers(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "Event.published.filter"), "exists") == DJANGO_ORM_MODULE
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "Event.visible.filter"), "exists") == DJANGO_ORM_MODULE

    def test_a_short_name_declared_twice_in_one_file_registers_nothing(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "t.pairs.count()"), "count") == "external"

    def test_a_diamond_lineage_is_walked_once_per_class(self, edges: list[Edge]) -> None:
        assert _slot_at(edges, _line_of(CROSS_FILE["views.py"], "d.nothing.count()"), "count") == "external"


class TestCustomManagerNames:
    """``<Model>.<manager>.<method>()`` for a class-level Manager not called ``objects``."""

    def test_custom_manager_read(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "o = Order.all.get(pk=2)")
        assert _slot_at(project_edges, line, "get") == DJANGO_ORM_MODULE

    def test_custom_manager_chain_propagates(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "Order.all.filter(pk=1).update(x=2)")
        assert _slot_at(project_edges, line, "update") == DJANGO_ORM_MODULE

    def test_a_non_manager_class_attribute_binds_nothing(self, project_edges: list[Edge]) -> None:
        """``Order.LABELS.get("a")`` is a dict read; ``label`` must not become an Order."""
        assert _slot_at(project_edges, _line_of(VIEWS, 'label = Order.LABELS.get("a")'), "get") != DJANGO_ORM_MODULE
        assert _slot_at(project_edges, _line_of(VIEWS, "label.payment_set.count()"), "count") == "external"


class TestTheRefutationCondition:
    """An accessor-like attribute on a receiver that does not own it stays untyped."""

    def test_a_non_model_class_with_a_field_named_like_an_accessor(self, tmp_path: Path) -> None:
        views = (
            "class EventSerializer:\n"
            "    def m(self):\n"
            "        return self.seats.get(1)\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 3, "get") == "external"

    def test_an_untyped_root(self, tmp_path: Path) -> None:
        views = "def f(thing):\n    thing.seats.create(number=1)\n"
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 2, "create") == "external"

    def test_a_typed_non_model_local(self, tmp_path: Path) -> None:
        views = (
            "class Other:\n"
            "    pass\n"
            "\n"
            "def f():\n"
            "    x = Other()\n"
            "    x.events.all()\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 6, "all") == "external"

    def test_a_related_name_of_plus_declares_no_accessor(self, tmp_path: Path) -> None:
        """``Payment.refund_of = ForeignKey("self", related_name="+")`` -- Django
        creates no reverse accessor, so a same-named attribute is not one."""
        views = (
            "from models import Payment\n"
            "def f():\n"
            "    p = Payment.objects.get(pk=1)\n"
            "    p.payment_set.count()\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 4, "count") == "external"

    def test_a_placeholder_related_name_declares_nothing(self, tmp_path: Path) -> None:
        models = (
            "from django.db import models\n"
            "class Tag(models.Model):\n"
            "    pass\n"
            "class Base(models.Model):\n"
            '    tag = models.ForeignKey(Tag, related_name="%(class)ss", on_delete=models.CASCADE)\n'
            "    class Meta:\n"
            "        abstract = True\n"
        )
        views = (
            "from models import Tag\n"
            "def f():\n"
            "    t = Tag.objects.get(pk=1)\n"
            "    t.bases.count()\n"
        )
        edges = _edges(tmp_path, {"models.py": models, "views.py": views})
        assert _slot_at(edges, 4, "count") == "external"

    def test_an_ambiguous_string_target_registers_nothing(self, tmp_path: Path) -> None:
        """Two project classes share the short name the string names and neither
        is imported where the FK is declared: refuse rather than guess."""
        files = {
            "a.py": "from django.db import models\nclass Venue(models.Model):\n    pass\n",
            "b.py": "from django.db import models\nclass Venue(models.Model):\n    pass\n",
            "c.py": (
                "from django.db import models\n"
                "class Show(models.Model):\n"
                '    venue = models.ForeignKey("Venue", related_name="shows", on_delete=models.CASCADE)\n'
            ),
            "views.py": (
                "from a import Venue\n"
                "def f():\n"
                "    v = Venue.objects.get(pk=1)\n"
                "    v.shows.count()\n"
            ),
        }
        edges = _edges(tmp_path, files)
        assert _slot_at(edges, 4, "count") == "external"

    def test_a_one_to_one_reverse_accessor_is_an_instance_not_a_manager(self, tmp_path: Path) -> None:
        """``Event.twin = OneToOneField("self", related_name="twin_of")``: the reverse
        side is an Event instance, so ``.filter`` on it is not a manager call."""
        views = (
            "from models import Event\n"
            "def f():\n"
            "    e = Event.objects.get(pk=1)\n"
            "    e.twin_of.filter(x=1)\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 4, "filter") == "external"

    def test_a_local_function_is_not_a_class_root(self, project_edges: list[Edge]) -> None:
        """``helper.all.filter(...)`` where ``helper`` is a same-file FUNCTION: a
        bare name bound to anything but a class is refused as a manager root."""
        assert _slot_at(project_edges, _line_of(VIEWS, "helper.all.filter(x=1)"), "filter") == "external"

    def test_a_manager_name_on_a_non_model_class_is_refused(self, tmp_path: Path) -> None:
        views = (
            "class Registry:\n"
            "    all = dict()\n"
            "def f():\n"
            "    Registry.all.get('k')\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 4, "get") == "external"


class TestTheEmittedShape:
    def test_related_manager_edges_carry_the_marker_meta(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "ev.seats.create")
        [edge] = [e for e in project_edges if e.line == line and e.dst.split(":")[3] == "create"]
        assert edge.meta is not None
        assert edge.meta["call_construct"] == "method"
        assert edge.meta["framework_dispatch"] == "django_orm"
        assert edge.meta["resolution_quality"] == "type_inferred"
        assert edge.dst_ref is not None and edge.dst_ref.module_path == DJANGO_ORM_MODULE

    def test_the_manager_itself_is_not_typed(self, tmp_path: Path) -> None:
        """A project-defined manager method keeps the ``external`` slot -- typing the
        manager would put ``django.db.models`` in the owner slot of a project method."""
        views = (
            "from models import Event\n"
            "def f():\n"
            "    e = Event.objects.get(pk=1)\n"
            "    e.seats.reserve_all()\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 4, "reserve_all") == "external"


class TestTheWriteSurfaceParity:
    """The producer's related-manager write set and the overlay's db_write rows
    are one fact: every member is rowed, so a hand-restated set cannot drift."""

    def test_related_manager_writes_are_rowed_db_write(self) -> None:
        cat = load_catalog("python")
        rows = {
            p.name: p.boundary for p in cat.primitives
            if p.module == DJANGO_ORM_MODULE and p.name in DJANGO_RELATED_MANAGER_WRITE_METHODS
        }
        assert rows == dict.fromkeys(DJANGO_RELATED_MANAGER_WRITE_METHODS, "db_write")

    def test_the_write_set_is_disjoint_from_the_manager_set(self) -> None:
        assert DJANGO_RELATED_MANAGER_WRITE_METHODS == frozenset({"add", "remove", "clear", "set"})
        assert not DJANGO_RELATED_MANAGER_WRITE_METHODS & DJANGO_ORM_MANAGER_METHODS

    def test_objects_does_not_take_the_related_write_set(self, tmp_path: Path) -> None:
        """``Order.objects.add(...)`` is not Django; the write set is related-manager only."""
        views = "from models import Order\ndef f(x):\n    Order.objects.add(x)\n"
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _slot_at(edges, 3, "add") == "external"
