# SPDX-License-Identifier: AGPL-3.0-or-later
"""Django ORM database-I/O visibility (producer side).

Django's ORM I/O arrives as bare untyped method calls the catalog correctly
refuses (INV-tapat/INV-maluk): ``.save()`` / ``.filter()`` / ``.get()`` on a
receiver hypergumbo cannot type. Left untyped, it is invisible to the
io-boundary detector.

These tests pin the producer's answer — TYPE the receiver via a
framework-syntax marker and emit a ``django.db.models``-module-qualified
``calls`` edge, so io-boundary's module-filter path (never the short-name gate)
can classify each method as ``db_read``/``db_write`` via python.yaml (producer
identity → consumer classification). Two type-verifying markers:

* ``<Model>.objects.<method>()`` — the Manager/QuerySet query API. The
  ``.objects`` attribute is Django's Manager-descriptor convention.
* ``self.save()`` / ``self.delete()`` in a class that DIRECTLY extends
  ``models.Model`` — the ORM instance-write surface, re-keyed from the plain
  external edge the analyzer would otherwise emit.

The io-boundary read/write CLASSIFICATION lives in the catalog (python.yaml), not
here — the producer only supplies the ``django.db.models`` module identity. So
these tests assert the emitted edge shape, not the boundary tag.
(history: WI-sozoj, WI-fuvuj)
"""

from pathlib import Path

import pytest

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
    """``obj.deep.method()`` emits a call edge, whatever the chain is rooted at.

    The Django marker above handles a chain rooted at a *class*
    (``Item.objects.create(...)``). A chain rooted at a local —
    ``event.organizer.issued_gift_cards.create()`` — needs an edge too.

    WHY IT MATTERS TWICE OVER. A function whose sink calls are ALL
    attribute-chain shaped has no sink edge, is never considered, and its flow
    is never reported — a false negative, the expensive direction. And the same
    absence makes the call invisible to ``callees_at``, so the §3a walk cannot
    ask whether the callee consumes the value and records an ESCAPE instead.
    (history: INV-mumov, INV-busis)
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
        """In-fixture positive control — the single-hop shape.

        ``obj.bar()`` emits ``python:external:0-0:bar:unresolved``. Pinned
        beside the chained case so a regression here cannot hide behind the
        chain assertion, and so the pair isolates chain DEPTH from attribute
        calls in general.
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
    """The RESULT of ``<Model>.objects.<queryset-method>()`` carries the ORM
    module, so the NEXT hop matches too.

    Typing only the first hop (``Order.objects.filter(...)``) would lose the type
    at ``.exists()`` in ``Order.objects.filter(...).exists()``, leaving an untyped
    method edge the catalogue's gate refuses. The root rule lives in
    ``_preserved_receiver_type`` beside the constructor root; which members return
    a QuerySet is DATA (``library_signatures/python.yaml``), so the chained hops
    propagate through ``TYPE_PRESERVING_MEMBERS`` exactly as ``pathlib.Path`` does.
    A Model-returning hop (``get``/``first``/``create``) deliberately does not
    propagate: its value is a project class, which carries no module.
    (history: INV-mumov)
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
        assert "python:django.db.models:0-0:filter:unresolved" in dsts  # the first hop
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
        ``.save()`` after it is not module-qualified: an instance write outside
        ``self`` in a Model subclass is out of the markers' scope -- a disclosed
        gap, not a mis-tag."""
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
        """No ``.objects`` root, no type: typing ``.filter(...).exists()`` on a
        parameter would be the by-name rule INV-nular warns against."""
        (tmp_path / "app.py").write_text(
            "def view(d):\n"
            "    return d.filter(x=1).exists()\n"
        )
        assert _orm_dsts(analyze_python(tmp_path).edges) == []

    def test_inline_and_bound_forms_agree(self, tmp_path: Path) -> None:
        """Inline and bound forms agree on the ORM root, as they do for pathlib
        (one predicate serves both; history: WI-zilag)."""
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


class TestQuerySetEvaluationCallSites:
    """The IMPLICIT evaluation of a lazy QuerySet gets a call site.

    A Django QuerySet reads nothing when it is built -- ``filter``/``all``/
    ``order_by`` compose a query -- and reads when it is EVALUATED: iterated,
    sliced by index, materialised by ``list()``. That execution is an implicit
    ``__iter__`` with no call site of its own, so py.py emits a ``calls`` edge to
    ``django.db.models.__iter__`` / ``__aiter__`` / ``__getitem__`` at the
    evaluation site, and the overlay rows those names under ``db_read``. Only
    because the read is represented where it happens may the lazy combinators
    sit under the disclosed ``db_compose`` boundary instead of ``db_read``
    (ADR-0049).

    Producer identity only, as with the other Django ORM markers: the boundary
    classification stays in the catalogue. (history: WI-fasap)
    """

    _MODEL = (
        "from django.db import models\n"
        "\n"
        "class Order(models.Model):\n"
        "    pass\n"
        "\n"
    )

    def _eval_edges(self, tmp_path: Path):
        return [
            e for e in analyze_python(tmp_path).edges
            if e.edge_type == "calls" and ":django.db.models:" in e.dst
            and e.dst.split(":")[3].startswith("__")
        ]

    def test_for_loop_over_an_inline_queryset_calls_iter(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def export():\n"
            "    for o in Order.objects.filter(active=True):\n"
            "        print(o)\n"
        )
        [edge] = self._eval_edges(tmp_path)
        assert edge.dst == "python:django.db.models:0-0:__iter__:unresolved"
        assert edge.line == 7
        assert edge.is_resolved is False
        assert edge.dst_ref is not None
        assert (edge.dst_ref.module_path, edge.dst_ref.name) == ("django.db.models", "__iter__")
        assert edge.meta["call_construct"] == "protocol"
        assert edge.meta["framework_dispatch"] == "django_orm"
        assert edge.meta["resolution_quality"] == "type_inferred"

    def test_for_loop_over_a_bound_queryset_calls_iter(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def export():\n"
            "    qs = Order.objects.filter(active=True).order_by('pk')\n"
            "    for o in qs:\n"
            "        print(o)\n"
        )
        assert [e.dst.split(":")[3] for e in self._eval_edges(tmp_path)] == ["__iter__"]

    def test_async_for_calls_aiter(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "async def export():\n"
            "    async for o in Order.objects.all():\n"
            "        print(o)\n"
        )
        assert [e.dst.split(":")[3] for e in self._eval_edges(tmp_path)] == ["__aiter__"]

    def test_comprehension_over_a_queryset_calls_iter(self, tmp_path: Path) -> None:
        """Both the first generator (evaluated in the ENCLOSING scope) and a
        later one (evaluated inside the comprehension's own scope) are reads."""
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def ids():\n"
            "    return [(a.pk, b.pk) for a in Order.objects.all() for b in Order.objects.none()]\n"
        )
        edges = self._eval_edges(tmp_path)
        assert {e.dst.split(":")[3] for e in edges} == {"__iter__"}
        assert all(e.line == 7 for e in edges)

    @pytest.mark.parametrize(
        "builtin", ("list", "tuple", "set", "frozenset", "sorted", "dict", "enumerate"),
    )
    def test_materialising_builtin_calls_iter(self, tmp_path: Path, builtin: str) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def snapshot():\n"
            f"    return {builtin}(Order.objects.values_list('pk', 'name'))\n"
        )
        assert [e.dst.split(":")[3] for e in self._eval_edges(tmp_path)] == ["__iter__"]

    def test_a_rebound_builtin_name_is_refused(self, tmp_path: Path) -> None:
        """INV-kipor's rule, applied here: the arm ASSERTS ``list`` is the
        builtin, so a name the enclosing scope rebound must not reach it."""
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def snapshot(list):\n"
            "    return list(Order.objects.all())\n"
        )
        assert self._eval_edges(tmp_path) == []

    def test_an_imported_name_shadowing_a_builtin_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from mylib import sorted\n"
            + self._MODEL
            + "def snapshot():\n"
            "    return sorted(Order.objects.all())\n"
        )
        assert self._eval_edges(tmp_path) == []

    def test_index_subscript_calls_getitem(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def newest():\n"
            "    return Order.objects.order_by('-pk')[0]\n"
        )
        [edge] = self._eval_edges(tmp_path)
        assert edge.dst == "python:django.db.models:0-0:__getitem__:unresolved"
        assert edge.line == 7

    def test_slice_subscript_reads_nothing_but_keeps_the_type(self, tmp_path: Path) -> None:
        """``qs[:n]`` returns another lazy QuerySet -- no read, no ``__getitem__``
        edge -- and the ``for`` over it is the read (``send_invoices_to_organizer``
        in pretix is exactly this shape)."""
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def batch(n):\n"
            "    qs = Order.objects.filter(sent=False)\n"
            "    for o in qs[:n]:\n"
            "        print(o)\n"
        )
        assert [e.dst.split(":")[3] for e in self._eval_edges(tmp_path)] == ["__iter__"]

    def test_untyped_iterables_emit_nothing(self, tmp_path: Path) -> None:
        """No ``.objects`` root, no type: a parameter that happens to be a
        QuerySet is the by-name rule INV-nular refuses, and a plain list is
        not a database."""
        (tmp_path / "app.py").write_text(
            "def a(queryset, items):\n"
            "    for o in queryset.filter(x=1):\n"
            "        print(o)\n"
            "    for i in items:\n"
            "        print(i)\n"
            "    return list(items), items[0], [i for i in items]\n"
        )
        assert self._eval_edges(tmp_path) == []

    def test_a_model_instance_is_not_iterated(self, tmp_path: Path) -> None:
        """``get`` returns a Model, not a QuerySet; iterating its attribute is
        not an ORM evaluation this analyzer can see."""
        (tmp_path / "app.py").write_text(
            self._MODEL
            + "def lines():\n"
            "    for line in Order.objects.get(pk=1).lines:\n"
            "        print(line)\n"
        )
        assert self._eval_edges(tmp_path) == []

    def test_getitem_is_a_type_preserving_member_for_the_slice_form(self) -> None:
        from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, TYPE_PRESERVING_MEMBERS

        assert "__getitem__" in TYPE_PRESERVING_MEMBERS.get(DJANGO_ORM_MODULE, frozenset())
