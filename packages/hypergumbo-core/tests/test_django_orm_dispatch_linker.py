# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Django ORM dispatch linker (WI-nosug).

Validates that the linker creates ``dispatches_to`` edges from Django
Model / Manager / QuerySet / Admin / View / Form subclasses to the
framework-called override methods defined on those subclasses.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.django_orm_dispatch import (
    DJANGO_BASE_METHODS,
    _build_method_index,
    _find_django_subclasses,
    _short_base_name,
    link_django_orm_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _ctx(
    symbols: list[Symbol], edges: list[Edge] | None = None,
) -> LinkerContext:
    return LinkerContext(
        repo_root=Path("/repo"),
        symbols=symbols,
        edges=list(edges or []),
    )


def _class_sym(
    name: str,
    *,
    path: str = "app/models.py",
    base_classes: list[str] | None = None,
    span: tuple[int, int] = (1, 20),
    kind: str = "class",
    language: str = "python",
) -> Symbol:
    meta: dict[str, object] = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
        meta=meta or None,
    )


def _method_sym(
    qualified_name: str,
    *,
    path: str = "app/models.py",
    span: tuple[int, int] = (5, 10),
    language: str = "python",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{span[0]}-{span[1]}:{qualified_name}:method",
        name=qualified_name,
        kind="method",
        language=language,
        path=path,
        span=Span(span[0], span[1], 0, 0),
    )


class TestShortBaseName:
    def test_bare_name_unchanged(self) -> None:
        assert _short_base_name("Model") == "Model"

    def test_qualified_collapsed(self) -> None:
        assert _short_base_name("django.db.models.Model") == "Model"

    def test_generic_parameters_stripped(self) -> None:
        assert _short_base_name("Manager[User]") == "Manager"

    def test_angle_bracket_generics_stripped(self) -> None:
        assert _short_base_name("QuerySet<User>") == "QuerySet"


class TestFindDjangoSubclasses:
    def test_model_subclass_matched(self) -> None:
        c = _class_sym("User", base_classes=["Model"])
        subs = _find_django_subclasses([c])
        assert len(subs) == 1
        assert subs[0][0] == c
        assert "save" in subs[0][1]
        assert "delete" in subs[0][1]

    def test_qualified_django_base(self) -> None:
        c = _class_sym("User", base_classes=["django.db.models.Model"])
        subs = _find_django_subclasses([c])
        assert len(subs) == 1

    def test_multiple_bases_merge_method_sets(self) -> None:
        """A CBV inheriting both ListView and View gets the union."""
        c = _class_sym(
            "MyListView", base_classes=["ListView", "LoginRequiredMixin"],
        )
        subs = _find_django_subclasses([c])
        assert len(subs) == 1
        methods = subs[0][1]
        # ListView contributes get_queryset; inherited View methods too.
        assert "get_queryset" in methods
        assert "get_context_data" in methods

    def test_non_class_kind_skipped(self) -> None:
        c = _class_sym("User", base_classes=["Model"], kind="function")
        assert _find_django_subclasses([c]) == []

    def test_non_python_skipped(self) -> None:
        c = _class_sym(
            "User", language="java", path="app.java",
            base_classes=["Model"],
        )
        assert _find_django_subclasses([c]) == []

    def test_non_django_base_skipped(self) -> None:
        c = _class_sym("Foo", base_classes=["object", "collections.OrderedDict"])
        assert _find_django_subclasses([c]) == []

    def test_no_base_classes(self) -> None:
        c = _class_sym("Foo", base_classes=[])
        assert _find_django_subclasses([c]) == []

    def test_no_meta(self) -> None:
        c = _class_sym("Foo")
        assert _find_django_subclasses([c]) == []

    def test_non_string_base_entries_ignored(self) -> None:
        c = _class_sym("User", base_classes=["Model"])
        c.meta["base_classes"] = ["Model", None, 123]  # type: ignore[list-item]
        subs = _find_django_subclasses([c])
        assert len(subs) == 1
        assert "save" in subs[0][1]

    def test_manager_subclass_matched(self) -> None:
        c = _class_sym("ActiveManager", base_classes=["Manager"])
        subs = _find_django_subclasses([c])
        assert len(subs) == 1
        assert "get_queryset" in subs[0][1]

    def test_queryset_subclass_matched(self) -> None:
        c = _class_sym("ActiveQuerySet", base_classes=["QuerySet"])
        subs = _find_django_subclasses([c])
        assert "filter" in subs[0][1]

    def test_admin_subclass_matched(self) -> None:
        c = _class_sym("UserAdmin", base_classes=["ModelAdmin"])
        subs = _find_django_subclasses([c])
        assert "save_model" in subs[0][1]

    def test_form_subclass_matched(self) -> None:
        c = _class_sym("UserForm", base_classes=["ModelForm"])
        subs = _find_django_subclasses([c])
        assert "clean" in subs[0][1]

    def test_view_subclass_matched(self) -> None:
        c = _class_sym("MyView", base_classes=["View"])
        subs = _find_django_subclasses([c])
        assert "dispatch" in subs[0][1]
        assert "get" in subs[0][1]

    def test_abstractuser_includes_auth_hooks(self) -> None:
        c = _class_sym("CustomUser", base_classes=["AbstractUser"])
        subs = _find_django_subclasses([c])
        methods = subs[0][1]
        assert "email_user" in methods
        assert "get_full_name" in methods


class TestBuildMethodIndex:
    def test_groups_by_path_and_qualified_name(self) -> None:
        methods = [
            _method_sym("User.save", path="a.py"),
            _method_sym("User.delete", path="a.py"),
            _method_sym("Article.save", path="b.py"),
        ]
        idx = _build_method_index(methods)
        assert idx[("a.py", "User.save")] == methods[0]
        assert idx[("a.py", "User.delete")] == methods[1]
        assert idx[("b.py", "Article.save")] == methods[2]

    def test_skips_non_methods(self) -> None:
        methods = [
            _method_sym("User.save"),
            _class_sym("User"),
        ]
        idx = _build_method_index(methods)
        assert list(idx.keys()) == [("app/models.py", "User.save")]

    def test_skips_non_python(self) -> None:
        methods = [
            _method_sym("User.save", language="ruby", path="u.rb"),
        ]
        assert _build_method_index(methods) == {}

    def test_skips_bare_method_names_without_class(self) -> None:
        """A method named just ``save`` (no dotted owner) can't be
        correlated with a Django subclass, so it's dropped."""
        bare = Symbol(
            id="python:a.py:1-5:save:method",
            name="save",
            kind="method",
            language="python",
            path="a.py",
            span=Span(1, 5, 0, 0),
        )
        assert _build_method_index([bare]) == {}


class TestLinkDjangoOrmDispatch:
    def test_emits_dispatches_to_for_model_subclass(self) -> None:
        c = _class_sym("User", base_classes=["Model"])
        save = _method_sym("User.save", span=(5, 7))
        delete = _method_sym("User.delete", span=(9, 11))
        unrelated = _method_sym("User.compute", span=(13, 15))
        result = link_django_orm_dispatch(_ctx([c, save, delete, unrelated]))
        dsts = {e.dst for e in result.edges}
        assert dsts == {save.id, delete.id}
        for e in result.edges:
            assert e.src == c.id
            assert e.edge_type == "dispatches_to"
            assert e.evidence_type == "ast_call_direct"
            assert (e.meta or {}).get("framework_dispatch") == "django_orm"
            assert e.confidence == 0.90

    def test_no_edges_when_no_django_subclasses(self) -> None:
        c = _class_sym("Plain", base_classes=["object"])
        save = _method_sym("Plain.save")
        assert link_django_orm_dispatch(_ctx([c, save])).edges == []

    def test_no_edges_when_subclass_has_no_override(self) -> None:
        """A Model subclass with no user-defined method overrides emits no edges."""
        c = _class_sym("User", base_classes=["Model"])
        assert link_django_orm_dispatch(_ctx([c])).edges == []

    def test_empty_input(self) -> None:
        result = link_django_orm_dispatch(_ctx([]))
        assert result.edges == []
        assert result.symbols == []

    def test_dedupes_against_existing_dispatches_to_edges(self) -> None:
        c = _class_sym("User", base_classes=["Model"])
        save = _method_sym("User.save")
        existing = Edge.create(
            src=c.id, dst=save.id,
            edge_type="dispatches_to",
            line=1,
            origin="prior-linker",
            origin_run_id="r1",
        )
        result = link_django_orm_dispatch(_ctx([c, save], edges=[existing]))
        assert result.edges == []

    def test_same_named_method_in_different_files_disambiguated(self) -> None:
        """Two classes named User in different files emit edges only to
        the method in the same file as the class."""
        user_a = _class_sym(
            "User", path="a.py", span=(1, 10), base_classes=["Model"],
        )
        user_b = _class_sym(
            "User", path="b.py", span=(1, 10), base_classes=["Model"],
        )
        save_a = _method_sym("User.save", path="a.py", span=(5, 7))
        save_b = _method_sym("User.save", path="b.py", span=(5, 7))
        result = link_django_orm_dispatch(
            _ctx([user_a, user_b, save_a, save_b]),
        )
        pairs = {(e.src, e.dst) for e in result.edges}
        assert pairs == {(user_a.id, save_a.id), (user_b.id, save_b.id)}

    def test_manager_get_queryset_override(self) -> None:
        mgr = _class_sym("ActiveManager", base_classes=["Manager"])
        gq = _method_sym("ActiveManager.get_queryset")
        result = link_django_orm_dispatch(_ctx([mgr, gq]))
        assert [e.dst for e in result.edges] == [gq.id]

    def test_listview_get_queryset_override(self) -> None:
        view = _class_sym("UserListView", base_classes=["ListView"])
        gq = _method_sym("UserListView.get_queryset")
        ctx = _method_sym("UserListView.get_context_data")
        result = link_django_orm_dispatch(_ctx([view, gq, ctx]))
        assert {e.dst for e in result.edges} == {gq.id, ctx.id}

    def test_modelform_clean_override(self) -> None:
        form = _class_sym("UserForm", base_classes=["ModelForm"])
        clean = _method_sym("UserForm.clean")
        result = link_django_orm_dispatch(_ctx([form, clean]))
        assert [e.dst for e in result.edges] == [clean.id]

    def test_modeladmin_save_model_override(self) -> None:
        admin = _class_sym("UserAdmin", base_classes=["ModelAdmin"])
        save_model = _method_sym("UserAdmin.save_model")
        result = link_django_orm_dispatch(_ctx([admin, save_model]))
        assert [e.dst for e in result.edges] == [save_model.id]

    def test_mixin_inheriting_class_still_picks_up_view_methods(self) -> None:
        """A CBV inheriting View via a mixin chain uses base_classes from
        its direct bases only — the linker does not traverse inheritance
        transitively, but the Python analyzer's base_classes includes the
        named bases, which is sufficient for common override patterns."""
        view = _class_sym(
            "MyView", base_classes=["LoginRequiredMixin", "View"],
        )
        dispatch = _method_sym("MyView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert [e.dst for e in result.edges] == [dispatch.id]

    def test_run_duration_populated(self) -> None:
        c = _class_sym("User", base_classes=["Model"])
        save = _method_sym("User.save")
        result = link_django_orm_dispatch(_ctx([c, save]))
        assert result.run.duration_ms >= 0


class TestConstants:
    def test_django_base_methods_non_empty(self) -> None:
        assert "Model" in DJANGO_BASE_METHODS
        assert "Manager" in DJANGO_BASE_METHODS
        assert "QuerySet" in DJANGO_BASE_METHODS
        assert "ModelAdmin" in DJANGO_BASE_METHODS
        assert "View" in DJANGO_BASE_METHODS
        assert "ModelForm" in DJANGO_BASE_METHODS

    def test_model_covers_core_lifecycle(self) -> None:
        model_methods = DJANGO_BASE_METHODS["Model"]
        for core in ("save", "delete", "clean", "__str__"):
            assert core in model_methods, f"Model missing core lifecycle method: {core}"

    def test_manager_covers_custom_queryset_hook(self) -> None:
        assert "get_queryset" in DJANGO_BASE_METHODS["Manager"]

    def test_view_covers_http_methods(self) -> None:
        view = DJANGO_BASE_METHODS["View"]
        for http_method in ("get", "post", "put", "delete", "dispatch"):
            assert http_method in view


class TestTransitiveSubclassWalk:
    """WI-halat: framework-dispatch must traverse the inheritance chain.

    UAT BUG-01 round 05 (pretix): direct subclasses passed 8/8, but
    every intermediate-base case (``Order(LoggedModel)`` /
    ``LoggedModel(models.Model)``, ``HierarkeyForm`` / its custom
    subclass) failed because the matcher only checked direct parents.
    """

    def test_one_intermediate_model_chain(self) -> None:
        # Order(LoggedModel) ; LoggedModel(models.Model)
        logged = _class_sym(
            "LoggedModel", path="app/abstract.py",
            base_classes=["models.Model"], span=(1, 5),
        )
        order = _class_sym(
            "Order", path="app/orders.py",
            base_classes=["LoggedModel"], span=(1, 5),
        )
        save = _method_sym(
            "Order.save", path="app/orders.py", span=(2, 4),
        )
        edge = Edge.create(
            src=order.id, dst=logged.id, edge_type="extends", line=1,

            origin="test", origin_run_id="test",
        )
        ctx = _ctx([order, logged, save], edges=[edge])
        result = link_django_orm_dispatch(ctx)
        assert {(e.src, e.dst) for e in result.edges} == {(order.id, save.id)}

    def test_form_chain(self) -> None:
        # CustomForm(HierarkeyForm) ; HierarkeyForm(forms.Form)
        hierarkey = _class_sym(
            "HierarkeyForm", path="app/forms_base.py",
            base_classes=["forms.Form"], span=(1, 5),
        )
        custom = _class_sym(
            "CustomForm", path="app/forms.py",
            base_classes=["HierarkeyForm"], span=(1, 5),
        )
        clean = _method_sym(
            "CustomForm.clean", path="app/forms.py", span=(2, 4),
        )
        edge = Edge.create(
            src=custom.id, dst=hierarkey.id, edge_type="extends", line=1,

            origin="test", origin_run_id="test",
        )
        ctx = _ctx([custom, hierarkey, clean], edges=[edge])
        result = link_django_orm_dispatch(ctx)
        assert {e.dst for e in result.edges} == {clean.id}

    def test_two_intermediates(self) -> None:
        # Order -> LoggedModel -> AbstractModel -> models.Model
        abstract = _class_sym(
            "AbstractModel", path="app/m_abstract.py",
            base_classes=["models.Model"], span=(1, 5),
        )
        logged = _class_sym(
            "LoggedModel", path="app/m_logged.py",
            base_classes=["AbstractModel"], span=(1, 5),
        )
        order = _class_sym(
            "Order", path="app/orders.py",
            base_classes=["LoggedModel"], span=(1, 5),
        )
        delete = _method_sym(
            "Order.delete", path="app/orders.py", span=(2, 4),
        )
        edges = [
            Edge.create(
                src=order.id, dst=logged.id, edge_type="extends", line=1,

                origin="test", origin_run_id="test",
            ),
            Edge.create(
                src=logged.id, dst=abstract.id,
                edge_type="extends", line=1,

                origin="test", origin_run_id="test",
            ),
        ]
        ctx = _ctx([order, logged, abstract, delete], edges=edges)
        result = link_django_orm_dispatch(ctx)
        assert {e.dst for e in result.edges} == {delete.id}

    def test_external_intermediate_does_not_block_direct_match(self) -> None:
        # Direct match still works when no extends edges exist (the
        # legacy direct-only path).
        c = _class_sym("User", base_classes=["models.Model"])
        save = _method_sym("User.save")
        ctx = _ctx([c, save], edges=[])
        assert {e.dst for e in link_django_orm_dispatch(ctx).edges} == {save.id}


class TestGenericCBVViewLifecycle:
    """WI-nipan / UAT DQ-02: generic CBVs (ListView, DetailView, …) inherit
    View's lifecycle methods (dispatch, setup, options, http_method_not_allowed),
    but Django's class hierarchy is external — so a project class
    `class Foo(ListView)` never reaches the bare ``View`` entry via the
    transitive base walk. The fix folds View's lifecycle into each generic
    CBV's frozenset.
    """

    def test_listview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserListView", base_classes=["ListView"])
        dispatch = _method_sym("UserListView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_detailview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserDetailView", base_classes=["DetailView"])
        dispatch = _method_sym("UserDetailView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_createview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserCreateView", base_classes=["CreateView"])
        dispatch = _method_sym("UserCreateView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_updateview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserUpdateView", base_classes=["UpdateView"])
        dispatch = _method_sym("UserUpdateView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_deleteview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserDeleteView", base_classes=["DeleteView"])
        dispatch = _method_sym("UserDeleteView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_templateview_subclass_emits_dispatch_edge(self) -> None:
        view = _class_sym("UserTemplateView", base_classes=["TemplateView"])
        dispatch = _method_sym("UserTemplateView.dispatch")
        result = link_django_orm_dispatch(_ctx([view, dispatch]))
        assert dispatch.id in {e.dst for e in result.edges}

    def test_listview_subclass_emits_setup_options_http_not_allowed(self) -> None:
        """Lifecycle methods other than dispatch are also picked up."""
        view = _class_sym("UserListView", base_classes=["ListView"])
        setup = _method_sym("UserListView.setup", span=(2, 4))
        options = _method_sym("UserListView.options", span=(5, 7))
        not_allowed = _method_sym(
            "UserListView.http_method_not_allowed", span=(8, 10),
        )
        result = link_django_orm_dispatch(_ctx([view, setup, options, not_allowed]))
        dsts = {e.dst for e in result.edges}
        assert setup.id in dsts
        assert options.id in dsts
        assert not_allowed.id in dsts

    def test_listview_subclass_still_emits_specific_methods(self) -> None:
        """ListView-specific methods aren't lost when View-lifecycle is folded in."""
        view = _class_sym("UserListView", base_classes=["ListView"])
        gq = _method_sym("UserListView.get_queryset", span=(2, 4))
        result = link_django_orm_dispatch(_ctx([view, gq]))
        assert gq.id in {e.dst for e in result.edges}


# ---------------------------------------------------------------------------
# INV-zuhub property tests: django_orm framework-dispatch fallback (WI-bojok PR3)
# ---------------------------------------------------------------------------


class TestInvZuhubDjangoOrmFallback:
    """INV-zuhub item 1, dispatches_to family — django_orm framework-base shape.

    Same structural pattern as the airflow / kafka conformance: short-
    base-name lookup against ``DJANGO_BASE_METHODS`` cannot distinguish
    Django's external framework type from an in-tree Python class of
    the same name (e.g. an in-tree ``Model`` class unrelated to
    ``django.db.models.Model``).
    """

    def test_dispatches_to_prefers_fqn_over_in_tree_collision(self) -> None:
        in_tree = _class_sym(
            "Model", path="app/internal/model.py",
        )
        user_cls = _class_sym(
            "User", path="app/models.py",
            base_classes=["django.db.models.Model"], span=(1, 30),
        )
        save_m = _method_sym(
            "User.save", path="app/models.py", span=(10, 15),
        )
        ctx = _ctx([in_tree, user_cls, save_m])
        result = link_django_orm_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence == 0.90
        assert (edge.meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_no_in_tree_collision_keeps_high_confidence(
        self,
    ) -> None:
        user_cls = _class_sym(
            "User", path="app/models.py",
            base_classes=["Model"], span=(1, 30),
        )
        save_m = _method_sym(
            "User.save", path="app/models.py", span=(10, 15),
        )
        ctx = _ctx([user_cls, save_m])
        result = link_django_orm_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        assert edges[0].confidence == 0.90
        assert (edges[0].meta or {}).get("disambiguation_fallback") is not True

    def test_dispatches_to_deterministic_fallback_when_ambiguous(self) -> None:
        in_tree = _class_sym(
            "Model", path="app/internal/model.py",
        )
        user_cls = _class_sym(
            "User", path="app/models.py",
            base_classes=["Model"], span=(1, 30),
        )
        save_m = _method_sym(
            "User.save", path="app/models.py", span=(10, 15),
        )
        ctx = _ctx([in_tree, user_cls, save_m])
        result = link_django_orm_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.confidence <= 0.5
        assert edge.meta is not None
        assert edge.meta.get("disambiguation_fallback") is True
        assert edge.meta.get("framework_dispatch") == "django_orm"

    def test_in_tree_collision_in_other_language_does_not_trigger(self) -> None:
        # A Java class named ``Model`` is not a Python collision — django
        # is Python-only, so the language scoping must exclude it.
        java_cls = _class_sym(
            "Model", path="app/Model.java",
            language="java", base_classes=None,
        )
        user_cls = _class_sym(
            "User", path="app/models.py",
            base_classes=["Model"], span=(1, 30),
        )
        save_m = _method_sym(
            "User.save", path="app/models.py", span=(10, 15),
        )
        ctx = _ctx([java_cls, user_cls, save_m])
        result = link_django_orm_dispatch(ctx)
        edges = [e for e in result.edges if e.src == user_cls.id]
        assert len(edges) == 1
        # No Python in-tree collision → precision match.
        assert edges[0].confidence == 0.90
        assert (edges[0].meta or {}).get("disambiguation_fallback") is not True
