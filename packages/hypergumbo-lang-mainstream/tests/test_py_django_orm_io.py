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


class TestAttributeChainReceiverEmitsACallEdge:
    """INV-mumov: ``obj.deep.method()`` emitted NO call edge at all.

    ``Item.objects.create(...)`` works — the Django marker above handles a
    chain rooted at a *class*. But ``event.organizer.issued_gift_cards.create()``
    is rooted at a local, and the analyzer emitted nothing for it. Measured on
    pretix: four calls to the same Django manager sink on adjacent lines, two
    emitting and two silent.

    WHY IT MATTERS TWICE OVER. A function whose sink calls are ALL
    attribute-chain shaped has no sink edge, is never considered, and its flow
    is never reported — a false negative, the expensive direction. And the same
    absence makes the call invisible to ``callees_at``, so the §3a walk cannot
    ask whether the callee consumes the value and records an ESCAPE instead;
    measured 2026-08-06, a substantial share of INV-busis's "genuine non-call
    escape sites" are calls in exactly this state.
    """

    def test_attribute_chain_on_a_local_emits_an_edge(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text(
            "def handler(event):\n"
            "    event.organizer.issued_gift_cards.create(value=1)\n"
        )
        result = analyze_python(tmp_path)
        dsts = {
            e.dst for e in result.edges
            if e.edge_type in ("calls", "unresolved_external_call")
        }
        assert "python:external:0-0:create:unresolved" in dsts, sorted(dsts)

    def test_single_attribute_on_a_param_still_emits(
        self, tmp_path: Path
    ) -> None:
        """In-fixture positive control — the shape that ALREADY worked.

        ``obj.bar()`` emits ``python:external:0-0:bar:unresolved`` today. Pinned
        beside the broken case so a regression there cannot hide behind the new
        assertion, and so the pair shows the defect is about chain DEPTH rather
        than about attribute calls in general.
        """
        (tmp_path / "app2.py").write_text(
            "def handler(obj):\n"
            "    obj.bar()\n"
        )
        result = analyze_python(tmp_path)
        dsts = {
            e.dst for e in result.edges
            if e.edge_type in ("calls", "unresolved_external_call")
        }
        assert "python:external:0-0:bar:unresolved" in dsts, sorted(dsts)

    def test_class_rooted_chain_keeps_its_django_module_hint(
        self, tmp_path: Path
    ) -> None:
        """Non-destructiveness: the Django marker must still win where it fires.

        ``Order.objects.create()`` is also an attribute chain. If a generic
        chain rule ran first it would emit ``external`` and DEMOTE a
        module-qualified django edge to an unqualified one — trading a false
        negative for a precision loss, which is not the trade being made here.
        """
        (tmp_path / "app3.py").write_text(
            "from django.db import models\n"
            "\n"
            "class Order(models.Model):\n"
            "    pass\n"
            "\n"
            "def make():\n"
            "    Order.objects.create(name='x')\n"
        )
        result = analyze_python(tmp_path)
        dsts = {
            e.dst for e in result.edges
            if e.edge_type in ("calls", "unresolved_external_call")
        }
        assert "python:django.db.models:0-0:create:unresolved" in dsts


class TestQuerySetChainPropagation:
    """INV-mumov, Phase 6 PR 1: the RESULT of ``<Model>.objects.<queryset-method>()``
    carries the ORM module, so the NEXT hop matches too.

    WI-sozoj typed the first hop (``Order.objects.filter(...)``) and nothing after
    it, so ``Order.objects.filter(...).exists()`` lost the type at ``.exists()`` --
    942 sentinel method edges on pretix rooted at ``<Model>.objects``, 976 of the
    1,625 ORM-chain edges on names the F3 gate refuses today (the 2026-09-06
    derivability census on INV-mumov). The root rule lives in
    ``_preserved_receiver_type`` beside the constructor root; which members return
    a QuerySet is DATA (``library_signatures/python.yaml``), so the chained hops
    propagate through ``TYPE_PRESERVING_MEMBERS`` exactly as ``pathlib.Path`` does.
    A Model-returning hop (``get``/``first``/``create``) deliberately does not
    propagate: its value is a project class, which carries no module.
    """

    _MODEL = (
        "from django.db import models\n"
        "\n"
        "class Order(models.Model):\n"
        "    pass\n"
        "\n"
    )

    def test_inline_chained_call_is_module_qualified(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def view():\n"
            "    return Order.objects.filter(active=True).exists()\n"
        )
        dsts = _orm_dsts(analyze_python(tmp_path).edges)
        assert "python:django.db.models:0-0:filter:unresolved" in dsts  # WI-sozoj, unchanged
        assert "python:django.db.models:0-0:exists:unresolved" in dsts

    def test_bound_queryset_variable_is_typed(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def purge():\n"
            "    qs = Order.objects.filter(active=False)\n"
            "    qs.delete()\n"
        )
        dsts = _orm_dsts(analyze_python(tmp_path).edges)
        assert "python:django.db.models:0-0:delete:unresolved" in dsts

    def test_two_hop_chain_propagates(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def view():\n"
            "    return Order.objects.filter(a=1).exclude(b=2).order_by('c').count()\n"
        )
        dsts = _orm_dsts(analyze_python(tmp_path).edges)
        for member in ("filter", "exclude", "order_by", "count"):
            assert f"python:django.db.models:0-0:{member}:unresolved" in dsts, member

    def test_model_returning_hop_does_not_propagate(self, tmp_path: Path) -> None:
        """``get`` returns a Model INSTANCE -- a project class, no module -- so the
        ``.save()`` after it stays where WI-sozoj deliberately left instance
        writes on typed locals: out of scope, disclosed, not mis-tagged."""
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def touch():\n"
            "    Order.objects.get(pk=1).save()\n"
        )
        dsts = _orm_dsts(analyze_python(tmp_path).edges)
        assert "python:django.db.models:0-0:get:unresolved" in dsts
        assert "python:django.db.models:0-0:save:unresolved" not in dsts

    def test_untyped_receiver_with_queryset_method_name_stays_external(
        self, tmp_path: Path
    ) -> None:
        """No ``.objects`` root, no type: a ``.filter(...).exists()`` on a parameter
        is the by-name rule INV-nular warns about and is NOT shipped here."""
        (tmp_path / "app.py").write_text(
            "def view(d):\n"
            "    return d.filter(x=1).exists()\n"
        )
        assert _orm_dsts(analyze_python(tmp_path).edges) == []

    def test_inline_and_bound_forms_agree(self, tmp_path: Path) -> None:
        """The one-predicate parity WI-zilag pinned for pathlib, on the ORM root."""
        (tmp_path / "inline.py").write_text(
            self._MODEL + "def a():\n    return Order.objects.filter(x=1).values_list('id')\n"
        )
        (tmp_path / "bound.py").write_text(
            self._MODEL + "def b():\n    qs = Order.objects.filter(x=1)\n    return qs.values_list('id')\n"
        )
        result = analyze_python(tmp_path)
        by_file = {}
        for e in result.edges:
            if e.edge_type == "calls" and ":django.db.models:" in e.dst:
                by_file.setdefault(Path(e.src.split(":")[1]).name, set()).add(e.dst.split(":")[3])
        assert by_file.get("inline.py") == by_file.get("bound.py") == {"filter", "values_list"}

    def test_queryset_returning_members_are_derived_from_the_signature_rows(self) -> None:
        from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, TYPE_PRESERVING_MEMBERS

        members = TYPE_PRESERVING_MEMBERS.get(DJANGO_ORM_MODULE, frozenset())
        assert {
            "filter", "exclude", "all", "order_by", "annotate", "select_related",
            "prefetch_related", "values", "values_list", "distinct",
        } <= members, sorted(members)
        # These return a Model instance, a scalar, or write -- never a QuerySet.
        assert not ({"get", "first", "last", "create", "count", "exists", "delete",
                     "update", "aggregate", "get_or_create"} & members)
