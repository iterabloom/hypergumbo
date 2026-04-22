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
            assert e.evidence_type == "django_orm_dispatch"
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
