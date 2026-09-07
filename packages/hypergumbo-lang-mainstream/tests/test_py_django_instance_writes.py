# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-gamas (INV-mumov candidate 3): the Django ORM INSTANCE WRITE, recognised
through the model lineage rather than through one direct base.

WI-sozoj typed ``self.save()`` only in a class whose DIRECT base is the dotted
``models.Model``. Measured on pretix (767 files, 2,923 ``save``/``delete`` call
sites) that gate is in the wrong place: of the 306 sites whose receiver resolves
to a model, 215 call a ``save`` the PROJECT overrides -- so the call correctly
resolves to the project's own method and is not an I/O primitive at all -- and
56 of the 66 ``save`` overrides then call ``super().save()``, which emitted the
bare ``external`` sentinel. On a real Django project the ORM write is therefore
not usually at ``x.save()``; it is at the ``super().save()`` inside the
override, the CHOKEPOINT every upstream call funnels through.

Two rules, one mechanism -- the model lineage the relation index already
computes (``model_ids``, transitive through resolved project bases):

* ``self.save()`` in a class that is a model TRANSITIVELY. WI-sozoj required
  one DIRECT dotted ``models.Model`` base and so typed 2 sites in all of
  pretix.
* ``X.save()`` where X is an instance of a model class, on the unresolved
  branch only, so a project-defined method still wins the edge.

A THIRD rule was built, measured and WITHHELD: ``super().save()`` inside a
model's own override, which is where the write actually is on a real Django
project. Typing it is correct at the site (measurement 0016 read 61 such sites
back with 0 wrong-type) but it puts a taint sink inside a model method, and the
ADR-0017 walk reaches any such method from any function that merely mentions
the class by crossing a ``dispatches_to`` edge -- 3 true findings against 20
false ones. It returns when that walk is fixed.

THE REFUTATION CONDITION, pre-registered before the code: a ``ModelForm`` and a
serializer also carry ``save()``, and neither is an ORM write. The index marks a
model by a ``models.Model`` base, a ``django.db.models.*`` body assignment, or a
project base that is one; a form declares ``forms.CharField``, so it must stay
untyped. One typed form site removes the typed-receiver rule.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import (
    DJANGO_ORM_INSTANCE_WRITE_METHODS,
    DJANGO_ORM_MODULE,
    analyze_python,
)


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _calls_at(edges: list[Edge], line: int, method: str) -> list[Edge]:
    """Every ``calls`` edge to ``method`` at ``line``.

    A RESOLVED dst carries the QUALIFIED name in its name slot
    (``Order.save``), so the comparison normalises; a helper that matched the
    bare name only would silently report "no edge" for a first-party call and
    make a re-key test pass for the wrong reason.
    """
    return [
        e for e in edges
        if e.edge_type == "calls" and e.line == line
        and e.dst.split(":")[3].rpartition(".")[2] == method
    ]


def _slot_at(edges: list[Edge], line: int, method: str) -> str | None:
    """The module slot of the one ``calls`` edge to ``method`` at ``line``."""
    hits = _calls_at(edges, line, method)
    assert len(hits) <= 1, [e.dst for e in hits]
    return hits[0].dst.split(":")[1] if hits else None


def _resolved_at(edges: list[Edge], line: int, method: str) -> bool:
    return any(e.is_resolved for e in _calls_at(edges, line, method))


def _line_of(src: str, needle: str) -> int:
    for i, text in enumerate(src.splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(needle)


MODELS = '''\
from django.db import models


class Journal(models.Model):
    """Abstract project base that OVERRIDES both ORM writes."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)  # BASE_SUPER_SAVE

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)  # BASE_SUPER_DELETE


class Order(Journal):
    """A model TRANSITIVELY, overriding a write its base also overrides."""

    code = models.CharField(max_length=10)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)  # OVERRIDE_SUPER_SAVE

'''

STAMPED = '''\
from django.db import models


class TimeStamped(models.Model):
    """Abstract project base that defines NO ORM write."""

    class Meta:
        abstract = True

    created = models.DateTimeField()


class Ticket(TimeStamped):
    """A model TRANSITIVELY, whose lineage defines no ``save``."""

    def touch(self):
        self.save()  # SELF_SAVE_TRANSITIVE
'''

PLAIN = '''\
from django.db import models


class Note(models.Model):
    body = models.TextField()
'''

VIEWS = '''\
from .models import Order
from .plain import Note


def from_manager():
    n = Note.objects.get(pk=1)
    n.delete()  # BOUND_MANAGER_DELETE


def from_constructor():
    n = Note()
    n.save()  # BOUND_CONSTRUCTOR_SAVE


def annotated(n: Note):
    n.save()  # ANNOTATED_PARAM_SAVE


def untyped(x):
    x.save()  # UNTYPED_SAVE


def overridden(code):
    o = Order.objects.get(code=code)
    o.save()  # PROJECT_OVERRIDE_SAVE
'''

FORMS = '''\
from django import forms

from .models import Order


class OrderForm(forms.ModelForm):
    note = forms.CharField(max_length=10)

    class Meta:
        model = Order
        fields = ["code"]

    def apply(self):
        self.save()  # FORM_SELF_SAVE
        super().save()  # FORM_SUPER_SAVE


class Plain:
    def save(self):
        pass

    def run(self):
        obj = Plain()
        obj.save()  # PLAIN_SAVE
'''


@pytest.fixture
def app(tmp_path: Path) -> list[Edge]:
    return _edges(tmp_path / "app", {
        "__init__.py": "",
        "models.py": MODELS,
        "stamped.py": STAMPED,
        "plain.py": PLAIN,
        "views.py": VIEWS,
        "forms.py": FORMS,
    })


class TestTheLineageIsWalked:
    def test_self_save_in_a_transitively_derived_model_carries_the_orm_module(self, app):
        line = _line_of(STAMPED, "SELF_SAVE_TRANSITIVE")
        assert _slot_at(app, line, "save") == DJANGO_ORM_MODULE

    def test_a_model_with_no_relations_still_builds_the_index(self, tmp_path):
        """``__bool__`` must count ``model_ids``: a Django app whose models
        declare no relation and no custom manager would otherwise pass ``None``
        and lose every rule here."""
        edges = _edges(tmp_path / "solo", {
            "__init__.py": "",
            "plain.py": PLAIN,
            "use.py": (
                "from .plain import Note\n"
                "\n"
                "\n"
                "def go():\n"
                "    n = Note()\n"
                "    n.save()  # SOLO_SAVE\n"
            ),
        })
        assert _slot_at(edges, 6, "save") == DJANGO_ORM_MODULE


class TestTheTypedReceiver:
    def test_a_local_bound_from_a_manager_carries_the_orm_module(self, app):
        line = _line_of(VIEWS, "BOUND_MANAGER_DELETE")
        assert _slot_at(app, line, "delete") == DJANGO_ORM_MODULE

    def test_a_constructor_bound_local_carries_the_orm_module(self, app):
        line = _line_of(VIEWS, "BOUND_CONSTRUCTOR_SAVE")
        assert _slot_at(app, line, "save") == DJANGO_ORM_MODULE

    def test_an_annotated_parameter_carries_the_orm_module(self, app):
        line = _line_of(VIEWS, "ANNOTATED_PARAM_SAVE")
        assert _slot_at(app, line, "save") == DJANGO_ORM_MODULE

    def test_an_untyped_receiver_is_left_alone(self, app):
        line = _line_of(VIEWS, "UNTYPED_SAVE")
        assert _slot_at(app, line, "save") == "external"

    def test_a_project_defined_save_still_resolves_to_the_project_method(self, app):
        """The rule fires on the UNRESOLVED branch only. ``Order`` defines
        ``save``, so the call keeps its first-party edge and is not re-keyed."""
        line = _line_of(VIEWS, "PROJECT_OVERRIDE_SAVE")
        assert _resolved_at(app, line, "save")
        assert _slot_at(app, line, "save") != DJANGO_ORM_MODULE


class TestTheRefutationCondition:
    def test_a_model_form_save_is_not_an_orm_write(self, app):
        line = _line_of(FORMS, "FORM_SELF_SAVE")
        assert _slot_at(app, line, "save") != DJANGO_ORM_MODULE

    def test_a_plain_project_class_save_is_not_an_orm_write(self, app):
        line = _line_of(FORMS, "PLAIN_SAVE")
        assert _slot_at(app, line, "save") != DJANGO_ORM_MODULE


def test_the_write_method_set_is_the_two_django_instance_writes():
    assert DJANGO_ORM_INSTANCE_WRITE_METHODS == {"save", "delete"}


class TestTheNarrowingsAreDeliberate:
    def test_a_typed_non_model_receiver_is_not_an_orm_write(self, tmp_path):
        """The receiver's type is known and is NOT a model: the rule must ask
        the lineage, not merely whether a type was derivable."""
        src = (
            "class Bare:\n"
            "    pass\n"
            "\n"
            "\n"
            "def go():\n"
            "    b = Bare()\n"
            "    b.save()  # BARE\n"
        )
        edges = _edges(tmp_path / "z", {"__init__.py": "", "m.py": src})
        assert _slot_at(edges, _line_of(src, "BARE"), "save") == "external"

    def test_a_duplicated_class_name_keeps_the_direct_base_fallback(self, tmp_path):
        """The index skips a class whose short name is not unique in its file,
        so the oracle cannot answer for it. WI-sozoj's direct-dotted-base test
        stays as the fallback precisely so that shipped behaviour does not
        silently narrow on those files."""
        src = (
            "from django.db import models\n"
            "\n"
            "\n"
            "class Dup(models.Model):\n"
            "    a = models.TextField()\n"
            "\n"
            "\n"
            "class Dup(models.Model):\n"
            "    b = models.TextField()\n"
            "\n"
            "    def touch(self):\n"
            "        self.save()  # DUP\n"
        )
        edges = _edges(tmp_path / "w", {"__init__.py": "", "m.py": src})
        assert _slot_at(edges, _line_of(src, "DUP"), "save") == DJANGO_ORM_MODULE
