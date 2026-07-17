# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sozoj: Django ORM database-I/O visibility (producer side).

Django's ORM I/O is invisible to the io-boundary detector because it arrives as
bare untyped method calls the catalog correctly refuses (INV-tapat/INV-maluk):
``.save()`` / ``.filter()`` / ``.get()`` on a receiver hypergumbo cannot type.

These tests pin the sanctioned fix — TYPE the receiver via a framework-syntax
marker and emit a ``django.db.models``-module-qualified ``calls`` edge, so
io-boundary's module-filter path (never the short-name gate) can classify each
method as ``db_read``/``db_write`` via python.yaml (producer identity → consumer
classification, the WI-fuvuj division). Two type-verifying markers:

* ``<Model>.objects.<method>()`` — the Manager/QuerySet query API. The
  ``.objects`` attribute is Django's Manager-descriptor convention; the chained
  receiver emits no edge at all today (measured), so this is net-new emission.
* ``self.save()`` / ``self.delete()`` in a class that DIRECTLY extends
  ``models.Model`` — the ORM instance-write surface, re-keyed from the plain
  external edge the analyzer already emits.

The io-boundary read/write CLASSIFICATION lives in the catalog (python.yaml), not
here — the producer only supplies the ``django.db.models`` module identity. So
these tests assert the emitted edge shape, not the boundary tag.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.py import (
    _class_directly_extends_django_model,
    analyze_python,
)


def _orm_dsts(edges: list) -> list[str]:
    """Return the dst ids of calls edges into the django.db.models module."""
    return [
        e.dst
        for e in edges
        if e.edge_type == "calls" and ":django.db.models:" in e.dst
    ]


class TestDjangoManagerMarker:
    """``<Model>.objects.<method>()`` → django.db.models module-qualified edge."""

    def test_manager_read_methods_emit_module_qualified_edge(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    pass\n"
            "\n"
            "def view():\n"
            "    qs = Order.objects.filter(active=True)\n"
            "    o = Order.objects.get(pk=1)\n"
            "    return qs, o\n"
        )
        result = analyze_python(tmp_path)
        dsts = _orm_dsts(result.edges)
        assert "python:django.db.models:0-0:filter:unresolved" in dsts
        assert "python:django.db.models:0-0:get:unresolved" in dsts

    def test_manager_write_methods_emit_module_qualified_edge(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    pass\n"
            "\n"
            "def make():\n"
            "    Order.objects.create(name='x')\n"
            "    Order.objects.bulk_create([])\n"
        )
        result = analyze_python(tmp_path)
        dsts = _orm_dsts(result.edges)
        assert "python:django.db.models:0-0:create:unresolved" in dsts
        assert "python:django.db.models:0-0:bulk_create:unresolved" in dsts

    def test_manager_edge_carries_module_hint_and_framework_meta(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    pass\n"
            "\n"
            "def view():\n"
            "    return Order.objects.all()\n"
        )
        result = analyze_python(tmp_path)
        edge = next(
            e for e in result.edges
            if e.dst == "python:django.db.models:0-0:all:unresolved"
        )
        assert edge.is_resolved is False
        assert edge.dst_ref is not None
        assert edge.dst_ref.module_path == "django.db.models"
        assert edge.dst_ref.name == "all"
        assert edge.meta is not None
        assert edge.meta.get("call_construct") == "method"
        assert edge.meta.get("framework_dispatch") == "django_orm"

    def test_non_orm_objects_method_emits_no_django_edge(
        self, tmp_path: Path
    ) -> None:
        """A ``.objects.<method>()`` whose method is NOT in the bounded ORM set
        stays invisible — no django.db.models edge (precision-safe)."""
        (tmp_path / "app.py").write_text(
            "def view(store):\n"
            "    return store.objects.frobnicate()\n"
        )
        result = analyze_python(tmp_path)
        assert _orm_dsts(result.edges) == []


class TestDjangoInstanceWrite:
    """``self.save()`` / ``self.delete()`` in a models.Model subclass → db write."""

    def test_self_delete_in_model_subclass_rekeys_to_django(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    def deactivate(self):\n"
            "        self.delete()\n"
        )
        result = analyze_python(tmp_path)
        dsts = _orm_dsts(result.edges)
        assert "python:django.db.models:0-0:delete:unresolved" in dsts
        # The plain external edge must NOT also be emitted (re-key, not add).
        assert "python:external:0-0:delete:unresolved" not in [
            e.dst for e in result.edges
        ]

    def test_self_delete_in_non_model_class_stays_external(
        self, tmp_path: Path
    ) -> None:
        """A non-Model class's ``self.delete()`` is untouched (no false ORM tag)."""
        (tmp_path / "app.py").write_text(
            "class Cache:\n"
            "    def clear(self):\n"
            "        self.delete()\n"
        )
        result = analyze_python(tmp_path)
        assert _orm_dsts(result.edges) == []
        assert "python:external:0-0:delete:unresolved" in [
            e.dst for e in result.edges
        ]

    def test_self_write_edge_carries_framework_meta(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    def stamp(self):\n"
            "        self.save()\n"
        )
        result = analyze_python(tmp_path)
        edge = next(
            e for e in result.edges
            if e.dst == "python:django.db.models:0-0:save:unresolved"
        )
        assert edge.meta is not None
        assert edge.meta.get("framework_dispatch") == "django_orm"
        # The self-branch's enclosing_class hint is preserved through the re-key.
        assert edge.meta.get("enclosing_class") == "Order"


class TestClassDirectlyExtendsDjangoModel:
    """Unit coverage of the Model-subclass gate helper."""

    def _cls(self, tmp_path: Path, bases: list[str]) -> dict:
        from hypergumbo_core.ir import Span, Symbol

        sym = Symbol(
            id="python:app.py:1-3:Order:class",
            name="Order",
            kind="class",
            language="python",
            path="app.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="r",
            meta={"base_classes": bases},
        )
        return {"Order": sym}

    def test_direct_models_model_base_is_django(self, tmp_path: Path) -> None:
        assert _class_directly_extends_django_model(
            "Order", self._cls(tmp_path, ["models.Model"])
        )

    def test_fully_qualified_base_is_django(self, tmp_path: Path) -> None:
        assert _class_directly_extends_django_model(
            "Order", self._cls(tmp_path, ["django.db.models.Model"])
        )

    def test_non_model_base_is_not_django(self, tmp_path: Path) -> None:
        assert not _class_directly_extends_django_model(
            "Order", self._cls(tmp_path, ["object"])
        )

    def test_bare_model_base_is_not_django(self, tmp_path: Path) -> None:
        """A bare ``Model`` base is ambiguous — degrade to invisible, not guess."""
        assert not _class_directly_extends_django_model(
            "Order", self._cls(tmp_path, ["Model"])
        )

    def test_missing_name_is_not_django(self, tmp_path: Path) -> None:
        assert not _class_directly_extends_django_model("Missing", {})

    def test_non_class_symbol_is_not_django(self, tmp_path: Path) -> None:
        from hypergumbo_core.ir import Span, Symbol

        fn = Symbol(
            id="python:app.py:1-1:f:function",
            name="f",
            kind="function",
            language="python",
            path="app.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="test",
            origin_run_id="r",
            meta={"base_classes": ["models.Model"]},
        )
        assert not _class_directly_extends_django_model("f", {"f": fn})

    def test_class_without_meta_is_not_django(self, tmp_path: Path) -> None:
        from hypergumbo_core.ir import Span, Symbol

        sym = Symbol(
            id="python:app.py:1-3:Order:class",
            name="Order",
            kind="class",
            language="python",
            path="app.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="test",
            origin_run_id="r",
            meta=None,
        )
        assert not _class_directly_extends_django_model("Order", {"Order": sym})
