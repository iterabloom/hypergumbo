# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-zamud (INV-mumov, largest remaining limb): a Django test class's fields
are assigned in ``setUp`` / ``setUpTestData``, not ``__init__``.

``_collect_class_field_maps`` reads a class's OWN ``__init__`` and nothing
else, so ``self.orga = Organizer.objects.create(...)`` written in ``setUp``
types nothing, and the chain that hangs off it -- ``self.orga.events.create()``
-- reaches no catalogue entry. Measured on pretix at filing: 1,155 call sites
of that shape (476 assigned in the class's own setUp, 679 inherited from a
base test class), which the 2026-09-09 residue census scores as 2,775 shapes
in the derivability instrument: the largest single population left in
INV-mumov's family, three times the remaining ORM-chain residue.

The binding rule is the one the walker already uses for a LOCAL
(``_DjangoReceiverOracle.instance_from_call``); what is new is only WHERE the
assignment is read from and that the resulting field type is inherited through
resolved project bases, because a pretix test's fixture is usually built in a
base class's setUp and consumed in a subclass's test method.

THE REFUTATION, pre-registered on the item at filing: a base class's setUp
field must NOT be attributed to a subclass that REASSIGNS the same field to a
different model. A subclass's own binding wins, and a non-model call
(``OrderForm()``) binds nothing at all.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, analyze_python


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _slot_at(edges: list[Edge], line: int, method: str) -> str | None:
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


class Organizer(models.Model):
    name = models.CharField(max_length=10)


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, related_name="events", on_delete=models.CASCADE)


class Ticket(models.Model):
    event = models.ForeignKey(Event, related_name="tickets", on_delete=models.CASCADE)


class Team(models.Model):
    name = models.CharField(max_length=10)


class OrderForm:
    def save(self):
        return None
'''


class TestSetUpAssignedFieldsCarryTheModel:
    """The filed row: the fixture is built in setUp and used in a test."""

    def test_setup_field_reaches_the_related_manager(self, tmp_path: Path) -> None:
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")

    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.orga.events.create")
        assert _slot_at(edges, line, "create") == DJANGO_ORM_MODULE

    def test_setuptestdata_classmethod_field_binds_through_cls(
        self, tmp_path: Path
    ) -> None:
        """``setUpTestData`` is a classmethod: the receiver is ``cls``."""
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.orga = Organizer.objects.create(name="x")

    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.orga.events.create")
        assert _slot_at(edges, line, "create") == DJANGO_ORM_MODULE

    def test_direct_write_on_an_attribute_receiver_is_a_KNOWN_GAP(
        self, tmp_path: Path
    ) -> None:
        """``self.orga.save()`` does NOT reach the catalogue, and the cause is
        NOT this rule.

        The ORM instance-write re-key (WI-sozoj -> WI-gamas -> WI-sihoh) lives
        inside ``_process_call``'s ``isinstance(func.value, ast.Name)`` branch,
        so it only ever sees a BARE-NAME receiver (``order.save()``,
        ``self.save()``). An ATTRIBUTE receiver never enters that branch at
        all, whatever typed it. Verified independent of setUp: a declared
        ``ForeignKey`` field is refused the same way
        (``self.organizer.save()`` inside the model that declares it).

        This test PINS the gap so it cannot be lost, and asserts the field
        itself IS typed -- the accessor-hop sibling above resolves through the
        very same ``self.orga``. Filed as the WI-zamud residual; ~1,110 pretix
        sites. When that residual lands, this assertion flips and the docstring
        goes with it.
        """
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")

    def test_it(self):
        self.orga.save()
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        assert _slot_at(edges, _line_of(src, "self.orga.save()"), "save") != (
            DJANGO_ORM_MODULE
        )
        # ... yet the SAME field, one accessor hop on, does reach it. The
        # binding is present; only the write branch cannot consume it.
        assert _slot_at(
            edges, _line_of(src, "self.orga.events.create"), "create",
        ) == DJANGO_ORM_MODULE


class TestInheritanceThroughProjectBases:
    """679 of the 1,155 filed sites inherit the fixture from a base class."""

    def test_base_class_setup_field_is_inherited_by_a_subclass(
        self, tmp_path: Path
    ) -> None:
        src = '''\
from django.test import TestCase

from models import Organizer


class BaseFixture(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")


class EventTest(BaseFixture):
    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.orga.events.create")
        assert _slot_at(edges, line, "create") == DJANGO_ORM_MODULE

    def test_inheritance_crosses_files(self, tmp_path: Path) -> None:
        """The declaring file is not the calling file (the WI-gulaz reason)."""
        base = '''\
from django.test import TestCase

from models import Organizer


class BaseFixture(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")
'''
        src = '''\
from base import BaseFixture


class EventTest(BaseFixture):
    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(
            tmp_path, {"models.py": MODELS, "base.py": base, "t.py": src}
        )
        line = _line_of(src, "self.orga.events.create")
        assert _slot_at(edges, line, "create") == DJANGO_ORM_MODULE


class TestTheRefutationHolds:
    """Pre-registered on the item: the narrowings are deliberate."""

    def test_a_subclass_reassignment_wins_over_the_base(
        self, tmp_path: Path
    ) -> None:
        """The filed refutation cell. ``Team`` has no ``events`` accessor, so
        crediting the BASE's ``Organizer`` here would be a wrong answer, not a
        missing one."""
        src = '''\
from django.test import TestCase

from models import Organizer, Team


class BaseFixture(TestCase):
    def setUp(self):
        self.thing = Organizer.objects.create(name="x")


class TeamTest(BaseFixture):
    def setUp(self):
        self.thing = Team.objects.create(name="t")

    def test_it(self):
        self.thing.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.thing.events.create")
        assert _slot_at(edges, line, "create") != DJANGO_ORM_MODULE

    def test_a_non_model_call_binds_nothing(self, tmp_path: Path) -> None:
        """``OrderForm`` is a plain class: its ``save`` keeps its own identity."""
        src = '''\
from django.test import TestCase

from models import OrderForm


class FormTest(TestCase):
    def setUp(self):
        self.form = OrderForm()

    def test_it(self):
        self.form.save()
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.form.save()")
        assert _slot_at(edges, line, "save") != DJANGO_ORM_MODULE

    def test_a_plain_method_assignment_is_not_a_fixture(
        self, tmp_path: Path
    ) -> None:
        """Only the setUp FAMILY is read. A field first assigned in an ordinary
        helper is not a class-level fixture and must not be inherited."""
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def _make(self):
        self.orga = Organizer.objects.create(name="x")

    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        line = _line_of(src, "self.orga.events.create")
        assert _slot_at(edges, line, "create") != DJANGO_ORM_MODULE


class TestTheFixtureChainAndItsRefusals:
    """The pass iterates to a fixed point and refuses what it cannot key."""

    def test_a_two_hop_fixture_chain_binds_in_the_second_round(
        self, tmp_path: Path
    ) -> None:
        """``self.event`` is typeable only once ``self.orga`` has been recorded
        -- the reason the pass is a fixed point rather than one sweep."""
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")
        self.event = self.orga.events.create(name="e")

    def test_it(self):
        self.event.organizer.save()
        self.event.tickets.create()
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        # ``self.event`` typed to Event => its DECLARED forward FK resolves.
        assert _slot_at(edges, _line_of(src, "self.event.tickets"), "create") == (
            DJANGO_ORM_MODULE
        )

    def test_a_local_bound_in_setup_carries_into_the_field(
        self, tmp_path: Path
    ) -> None:
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        made = Organizer.objects.create(name="x")
        self.orga = made

    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        # A local is NOT an ORM call, so the field stays unbound: the pass binds
        # from a CALL, and this documents that boundary rather than implying it.
        assert _slot_at(edges, _line_of(src, "self.orga.events"), "create") != (
            DJANGO_ORM_MODULE
        )

    def test_get_or_create_tuple_unpacking_binds_the_instance(
        self, tmp_path: Path
    ) -> None:
        """``get_or_create`` returns ``(instance, created)``; only slot 0 is
        the model."""
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga, created = Organizer.objects.get_or_create(name="x")

    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        assert _slot_at(edges, _line_of(src, "self.orga.events"), "create") == (
            DJANGO_ORM_MODULE
        )

    def test_a_same_short_name_class_in_one_file_is_refused(
        self, tmp_path: Path
    ) -> None:
        """``symbol_by_name`` is last-write-wins, so the map could be the wrong
        twin's -- refused exactly as WI-supat refuses the receiver-type id."""
        src = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")


class EventTest(TestCase):
    def test_it(self):
        self.orga.events.create(name="e")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        assert _slot_at(edges, _line_of(src, "self.orga.events"), "create") != (
            DJANGO_ORM_MODULE
        )

    def test_a_django_repo_with_no_setup_method_anywhere_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """The ``no units`` early return: a Django app with models but no test
        fixture must take exactly the path it took before."""
        src = '''\
from models import Organizer


def make():
    return Organizer.objects.create(name="x")
'''
        edges = _edges(tmp_path, {"models.py": MODELS, "t.py": src})
        assert _slot_at(edges, _line_of(src, "Organizer.objects.create"), "create") == (
            DJANGO_ORM_MODULE
        )
