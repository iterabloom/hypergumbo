# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Django write through an ATTRIBUTE receiver is an ORM write (WI-kufok).

The ORM instance-write re-key (WI-sozoj -> WI-gamas -> WI-sihoh) lived only in
``_process_call``'s bare-Name branch, so ``order.save()`` and ``self.save()``
reached ``django.db.models`` while ``self.order.save()`` and
``pos.order.delete()`` fell to the Site-3 and chain branches and emitted the
``external`` placeholder -- whatever typed the receiver. The oracle that types
``self.order`` (a declared ``ForeignKey``, a setUp fixture) already answered;
only the write branches never asked it. Measured on pretix before this change:
1,086 attribute-receiver ``.save()`` / ``.delete()`` sites, 0 reaching
``django.db.models``.

ONE RULE AT EVERY EMIT SITE: ``_django_orm_instance_write`` asks the oracle for
the receiver's model and refuses when the model or a project base OVERRIDES the
method -- that call is the project's own ``save``, and WI-sihoh types the write
at its ``super().save()``, so one logical write is counted once.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, analyze_python

MODELS = '''\
from django.db import models


class Organizer(models.Model):
    name = models.CharField(max_length=10)


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, related_name="events", on_delete=models.CASCADE)
    name = models.CharField(max_length=10)

    def touch(self):
        self.organizer.save()
        self.name.upper()


class Audited(models.Model):
    def save(self, *a, **k):
        super().save(*a, **k)


class Entry(models.Model):
    log = models.ForeignKey(Audited, on_delete=models.CASCADE)

    def bump(self):
        self.log.save()
'''

VIEWS = '''\
from models import Event


def close(pk):
    ev = Event.objects.get(pk=pk)
    ev.organizer.delete()
'''

TESTS = '''\
from django.test import TestCase

from models import Organizer


class EventTest(TestCase):
    def setUp(self):
        self.orga = Organizer.objects.create(name="x")

    def test_it(self):
        self.orga.save()
'''


def _edges(root: Path) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in {"models.py": MODELS, "views.py": VIEWS, "t.py": TESTS}.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _slot(edges: list[Edge], file: str, src: str, needle: str, method: str) -> str | None:
    line = next(i for i, t in enumerate(src.splitlines(), 1) if needle in t)
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.line == line
        and e.src.split(":")[1].endswith("/" + file)
        and e.dst.split(":")[3] == method
    ]
    assert len(hits) == 1, [e.dst for e in hits]  # reach first: the call emits
    return hits[0].dst.split(":")[1]


@pytest.mark.parametrize("file,src,needle,method", [
    ("models.py", MODELS, "self.organizer.save()", "save"),  # declared ForeignKey
    ("views.py", VIEWS, "ev.organizer.delete()", "delete"),  # relation off a local
    ("t.py", TESTS, "self.orga.save()", "save"),              # setUp fixture field
])
def test_an_attribute_receiver_write_is_an_orm_write(
    tmp_path: Path, file: str, src: str, needle: str, method: str,
) -> None:
    assert _slot(_edges(tmp_path), file, src, needle, method) == DJANGO_ORM_MODULE


def test_a_model_that_overrides_save_keeps_its_own_method(tmp_path: Path) -> None:
    """THE REFUTATION: ``Audited.save`` is the project's; its ``super()`` is the write."""
    edges = _edges(tmp_path)
    assert _slot(edges, "models.py", MODELS, "self.log.save()", "save") != DJANGO_ORM_MODULE


def test_a_non_relation_field_is_not_a_model(tmp_path: Path) -> None:
    """THE CONTROL: ``self.name`` is a CharField, not a model instance."""
    edges = _edges(tmp_path)
    assert _slot(edges, "models.py", MODELS, "self.name.upper()", "upper") != DJANGO_ORM_MODULE


def test_io_boundaries_reports_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _edges(repo)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["io-boundaries", str(repo), "--format", "json"])
    chains = json.loads(buf.getvalue())["boundaries"].get("db_write", {"chains": []})["chains"]
    writers = {c["io_edge_src"].split(":")[-2].split(".")[-1] for c in chains}
    assert {"touch", "close", "test_it"} <= writers, writers
    assert "bump" not in writers, writers
