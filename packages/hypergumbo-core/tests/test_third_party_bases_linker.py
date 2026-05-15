# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the third-party-base allow-list dispatch linker (WI-jifib).

The linker emits ``dispatches_to`` edges from Python subclasses of
well-known third-party Django/DRF/django-filter/Wagtail bases to their
framework-called override methods. Gated on Django framework detection
so it only fires on Django ecosystem repos.

Companion to WI-nosug's django_orm_dispatch (DJANGO_BASE_METHODS table
covers Django-internal bases). This linker covers the third-party gap
identified in WI-jusih's data pass.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers._third_party_bases import (
    THIRD_PARTY_BASE_METHODS,
    _THIRD_PARTY_FQN_PREFIXES,
    _build_method_index,
    _find_third_party_subclasses,
    _short_base_name,
    link_django_third_party_dispatch,
)
from hypergumbo_core.linkers.registry import (
    LinkerActivation,
    LinkerContext,
    get_linker,
    should_run_linker,
)


def _ctx(
    symbols: list[Symbol],
    edges: list[Edge] | None = None,
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


# ---------------------------------------------------------------------------
# Constants — property tests on the table itself.
# ---------------------------------------------------------------------------


class TestThirdPartyBaseMethods:
    def test_table_non_empty(self) -> None:
        assert THIRD_PARTY_BASE_METHODS, "table must have entries"

    def test_table_covers_data_pass_candidates(self) -> None:
        """Every candidate the WI-jusih data pass identified must appear."""
        for name in (
            "HierarkeyForm",
            "FilterSet",
            "WagtailFilterSet",
            "Serializer",
            "ModelSerializer",
            "HyperlinkedModelSerializer",
            "Page",
        ):
            assert name in THIRD_PARTY_BASE_METHODS, (
                f"third-party base {name!r} missing from table"
            )

    def test_no_overlap_with_django_orm_dispatch(self) -> None:
        """The allow-list must not duplicate Django stdlib bases —
        those are already covered by django_orm_dispatch. Duplicating
        them would emit duplicate edges (deduped) but muddies the
        framework_dispatch label."""
        from hypergumbo_core.linkers.django_orm_dispatch import (
            DJANGO_BASE_METHODS,
        )

        overlap = set(THIRD_PARTY_BASE_METHODS) & set(DJANGO_BASE_METHODS)
        assert overlap == set(), (
            f"third-party table overlaps Django stdlib: {overlap}"
        )

    def test_every_entry_has_at_least_one_method(self) -> None:
        for name, methods in THIRD_PARTY_BASE_METHODS.items():
            assert methods, f"{name} entry has empty method set"
            assert isinstance(methods, frozenset)

    def test_fqn_prefixes_cover_framework_namespaces(self) -> None:
        """Every framework namespace producing entries in the allow-list
        must have an FQN prefix registered, so INV-zuhub disambiguation
        works for qualified references."""
        for ns in ("hierarkey.", "django_filters.", "rest_framework.", "wagtail."):
            assert ns in _THIRD_PARTY_FQN_PREFIXES, (
                f"missing FQN prefix: {ns}"
            )

    def test_fqn_prefixes_all_terminate_in_dot(self) -> None:
        """FQN prefixes must end with '.' so str.startswith doesn't
        match partial namespace prefixes (e.g., 'wagtail' must not
        match 'wagtailfilter.X')."""
        for p in _THIRD_PARTY_FQN_PREFIXES:
            assert p.endswith("."), f"FQN prefix {p!r} doesn't end with '.'"


# ---------------------------------------------------------------------------
# Helper functions — local copies of django_orm_dispatch helpers must be
# tested separately (the modules deliberately duplicate them to avoid
# cross-module coupling on private names).
# ---------------------------------------------------------------------------


class TestShortBaseName:
    def test_bare_name_unchanged(self) -> None:
        assert _short_base_name("HierarkeyForm") == "HierarkeyForm"

    def test_qualified_collapsed(self) -> None:
        assert _short_base_name("wagtail.models.Page") == "Page"

    def test_generic_parameters_stripped(self) -> None:
        assert _short_base_name("Serializer[User]") == "Serializer"

    def test_angle_bracket_generics_stripped(self) -> None:
        assert _short_base_name("FilterSet<User>") == "FilterSet"


class TestBuildMethodIndex:
    def test_groups_by_path_and_qualified_name(self) -> None:
        methods = [
            _method_sym("SettingsForm.clean", path="a.py"),
            _method_sym("SettingsForm.save", path="a.py"),
            _method_sym("OtherForm.clean", path="b.py"),
        ]
        idx = _build_method_index(methods)
        assert idx[("a.py", "SettingsForm.clean")] == methods[0]
        assert idx[("a.py", "SettingsForm.save")] == methods[1]
        assert idx[("b.py", "OtherForm.clean")] == methods[2]

    def test_skips_non_methods(self) -> None:
        symbols = [_method_sym("S.save"), _class_sym("S")]
        idx = _build_method_index(symbols)
        assert list(idx.keys()) == [("app/models.py", "S.save")]

    def test_skips_non_python(self) -> None:
        methods = [
            _method_sym("S.save", language="ruby", path="s.rb"),
        ]
        assert _build_method_index(methods) == {}

    def test_skips_bare_method_names_without_class(self) -> None:
        bare = Symbol(
            id="python:a.py:1-5:save:method",
            name="save",
            kind="method",
            language="python",
            path="a.py",
            span=Span(1, 5, 0, 0),
        )
        assert _build_method_index([bare]) == {}


# ---------------------------------------------------------------------------
# _find_third_party_subclasses — direct chain walk semantics.
# ---------------------------------------------------------------------------


class TestFindThirdPartySubclasses:
    def test_hierarkey_form_direct_match(self) -> None:
        c = _class_sym(
            "SettingsForm",
            base_classes=["I18nFormMixin", "HierarkeyForm"],
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert subs[0][0] == c
        # HierarkeyForm inherits Form lifecycle methods.
        assert "clean" in subs[0][1]
        assert "save" in subs[0][1]
        assert subs[0][2] is False  # not a fallback (no in-tree collision)

    def test_filterset_direct_match(self) -> None:
        c = _class_sym(
            "OrderFilter",
            base_classes=["django_filters.FilterSet"],
            path="app/filters.py",
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert "filter_queryset" in subs[0][1]

    def test_serializer_direct_match(self) -> None:
        c = _class_sym(
            "UserSerializer",
            base_classes=["serializers.ModelSerializer"],
            path="api/serializers.py",
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert "to_representation" in subs[0][1]
        # ModelSerializer also overrides create / update over Serializer.
        assert "create" in subs[0][1]

    def test_wagtail_page_direct_match(self) -> None:
        c = _class_sym(
            "BlogPage",
            base_classes=["Page"],
            path="blog/models.py",
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert "serve" in subs[0][1]

    def test_non_third_party_base_skipped(self) -> None:
        c = _class_sym("Plain", base_classes=["object"])
        assert _find_third_party_subclasses([c]) == []

    def test_no_base_classes(self) -> None:
        c = _class_sym("Foo", base_classes=[])
        assert _find_third_party_subclasses([c]) == []

    def test_no_meta(self) -> None:
        c = _class_sym("Foo")
        assert _find_third_party_subclasses([c]) == []

    def test_non_class_kind_skipped(self) -> None:
        c = _class_sym("X", base_classes=["HierarkeyForm"], kind="function")
        assert _find_third_party_subclasses([c]) == []

    def test_non_python_skipped(self) -> None:
        c = _class_sym(
            "X", language="java", path="X.java",
            base_classes=["HierarkeyForm"],
        )
        assert _find_third_party_subclasses([c]) == []

    def test_non_string_base_entries_ignored(self) -> None:
        c = _class_sym("X", base_classes=["HierarkeyForm"])
        c.meta["base_classes"] = ["HierarkeyForm", None, 123]  # type: ignore[list-item]
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert "clean" in subs[0][1]

    def test_qualified_third_party_base(self) -> None:
        c = _class_sym(
            "MyPage",
            base_classes=["wagtail.models.Page"],
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        assert "serve" in subs[0][1]
        assert subs[0][2] is False  # qualified -> precise

    def test_inv_zuhub_fallback_when_in_tree_collision(self) -> None:
        """If an in-tree Python class has the same simple name as the
        third-party base AND the chain entry is unqualified, the match
        must be flagged as fallback (confidence <= 0.5)."""
        # User has their own `class Page(models.Model)` in-tree, and a
        # subclass that extends bare `Page` (ambiguous).
        in_tree_page = _class_sym(
            "Page", base_classes=["models.Model"], path="local/models.py",
        )
        consumer = _class_sym(
            "MyPage", base_classes=["Page"], path="local/blog.py",
        )
        in_tree_collisions = frozenset({"Page"})
        subs = _find_third_party_subclasses(
            [consumer, in_tree_page],
            in_tree_collisions=in_tree_collisions,
        )
        # Filter to consumer-only result.
        consumer_subs = [s for s in subs if s[0] is consumer]
        assert len(consumer_subs) == 1
        assert consumer_subs[0][2] is True  # is_fallback

    def test_inv_zuhub_qualified_still_precise_with_collision(self) -> None:
        """A qualified entry like ``wagtail.models.Page`` remains precise
        even if an in-tree class named `Page` collides."""
        consumer = _class_sym(
            "MyPage", base_classes=["wagtail.models.Page"],
        )
        subs = _find_third_party_subclasses(
            [consumer],
            in_tree_collisions=frozenset({"Page"}),
        )
        assert len(subs) == 1
        assert subs[0][2] is False  # qualified -> precise

    def test_multiple_third_party_bases_merge_methods(self) -> None:
        """If a class extends multiple third-party bases in the allow-
        list, it receives the union of their framework methods."""
        c = _class_sym(
            "OmniThing",
            base_classes=["Page", "FilterSet"],  # contrived but valid
        )
        subs = _find_third_party_subclasses([c])
        assert len(subs) == 1
        methods = subs[0][1]
        assert "serve" in methods           # from Page
        assert "filter_queryset" in methods  # from FilterSet

    def test_chain_walk_via_extends_edge(self) -> None:
        """A class extending an in-tree intermediate that extends
        HierarkeyForm should still receive HierarkeyForm methods —
        the BFS walks transitively."""
        base = _class_sym(
            "I18nSettingsForm",
            base_classes=["I18nFormMixin", "HierarkeyForm"],
            path="base/forms.py", span=(1, 5),
        )
        wrapper = _class_sym(
            "EventSettingsForm",
            base_classes=["I18nSettingsForm"],
            path="event/forms.py", span=(1, 5),
        )
        edge = Edge.create(
            src=wrapper.id, dst=base.id, edge_type="extends", line=1,
        )
        subs = _find_third_party_subclasses(
            [wrapper, base], edges=[edge],
        )
        # Both classes report HierarkeyForm methods.
        wrapper_subs = [s for s in subs if s[0] is wrapper]
        assert len(wrapper_subs) == 1
        assert "clean" in wrapper_subs[0][1]


# ---------------------------------------------------------------------------
# link_django_third_party_dispatch — emit edges with INV-zuhub.
# ---------------------------------------------------------------------------


class TestLinkThirdPartyDispatch:
    def test_emits_dispatches_to_for_hierarkey_form(self) -> None:
        c = _class_sym(
            "SettingsForm", base_classes=["HierarkeyForm"],
            path="app/forms.py",
        )
        clean = _method_sym(
            "SettingsForm.clean", path="app/forms.py", span=(5, 7),
        )
        save = _method_sym(
            "SettingsForm.save", path="app/forms.py", span=(9, 11),
        )
        unrelated = _method_sym(
            "SettingsForm.compute", path="app/forms.py", span=(13, 15),
        )
        result = link_django_third_party_dispatch(
            _ctx([c, clean, save, unrelated]),
        )
        dsts = {e.dst for e in result.edges}
        assert dsts == {clean.id, save.id}
        for e in result.edges:
            assert e.src == c.id
            assert e.edge_type == "dispatches_to"
            assert e.evidence_type == "ast_call_direct"
            meta = e.meta or {}
            assert meta.get("framework_dispatch") == "django_third_party"
            assert e.confidence == 0.90

    def test_emits_for_drf_modelserializer_override(self) -> None:
        c = _class_sym(
            "UserSerializer",
            base_classes=["serializers.ModelSerializer"],
            path="api/serializers.py",
        )
        to_rep = _method_sym(
            "UserSerializer.to_representation",
            path="api/serializers.py", span=(5, 7),
        )
        create = _method_sym(
            "UserSerializer.create",
            path="api/serializers.py", span=(9, 11),
        )
        result = link_django_third_party_dispatch(
            _ctx([c, to_rep, create]),
        )
        assert {e.dst for e in result.edges} == {to_rep.id, create.id}

    def test_emits_for_wagtail_page_override(self) -> None:
        c = _class_sym(
            "BlogPage", base_classes=["wagtail.models.Page"],
            path="blog/models.py",
        )
        serve = _method_sym(
            "BlogPage.serve", path="blog/models.py", span=(5, 7),
        )
        result = link_django_third_party_dispatch(_ctx([c, serve]))
        assert [e.dst for e in result.edges] == [serve.id]

    def test_no_edges_when_no_third_party_subclasses(self) -> None:
        c = _class_sym("Plain", base_classes=["object"])
        m = _method_sym("Plain.save")
        assert link_django_third_party_dispatch(_ctx([c, m])).edges == []

    def test_no_edges_when_subclass_has_no_override(self) -> None:
        c = _class_sym("SettingsForm", base_classes=["HierarkeyForm"])
        assert link_django_third_party_dispatch(_ctx([c])).edges == []

    def test_empty_input(self) -> None:
        result = link_django_third_party_dispatch(_ctx([]))
        assert result.edges == []
        assert result.symbols == []

    def test_inv_zuhub_fallback_lowers_confidence_and_flags_meta(
        self,
    ) -> None:
        """A class extending bare `Page` with an in-tree `Page` collision
        emits an edge at confidence <= 0.5 with disambiguation_fallback
        flag set."""
        in_tree_page = _class_sym(
            "Page", base_classes=["models.Model"], path="local/models.py",
        )
        consumer = _class_sym(
            "MyPage", base_classes=["Page"], path="local/blog.py",
        )
        serve = _method_sym(
            "MyPage.serve", path="local/blog.py", span=(5, 7),
        )
        result = link_django_third_party_dispatch(
            _ctx([in_tree_page, consumer, serve]),
        )
        consumer_edges = [e for e in result.edges if e.src == consumer.id]
        assert len(consumer_edges) == 1
        e = consumer_edges[0]
        assert e.confidence == 0.5
        assert (e.meta or {}).get("disambiguation_fallback") is True

    def test_dedupes_against_existing_dispatches_to_edges(self) -> None:
        c = _class_sym(
            "SettingsForm", base_classes=["HierarkeyForm"],
            path="app/forms.py",
        )
        save = _method_sym(
            "SettingsForm.save", path="app/forms.py",
        )
        existing = Edge.create(
            src=c.id, dst=save.id, edge_type="dispatches_to",
            line=1, origin="prior-linker", origin_run_id="r1",
        )
        result = link_django_third_party_dispatch(
            _ctx([c, save], edges=[existing]),
        )
        assert result.edges == []

    def test_run_duration_populated(self) -> None:
        c = _class_sym("S", base_classes=["HierarkeyForm"])
        save = _method_sym("S.save")
        result = link_django_third_party_dispatch(_ctx([c, save]))
        assert result.run.duration_ms >= 0


# ---------------------------------------------------------------------------
# Framework activation — AC-3: gate must be a hard cutoff.
# ---------------------------------------------------------------------------


class TestFrameworkActivation:
    def test_linker_is_registered_with_django_activation(self) -> None:
        reg = get_linker("django-third-party-dispatch")
        assert reg is not None
        activation = reg.activation
        assert isinstance(activation, LinkerActivation)
        assert not activation.always
        assert "django" in activation.frameworks

    def test_gate_blocks_non_django_repos(self) -> None:
        """AC-3: a repo without django detected must not run this linker."""
        assert should_run_linker(
            "django-third-party-dispatch",
            detected_frameworks=set(),
            detected_languages={"python"},
        ) is False

    def test_gate_opens_when_django_detected(self) -> None:
        assert should_run_linker(
            "django-third-party-dispatch",
            detected_frameworks={"django"},
            detected_languages={"python"},
        ) is True

    def test_gate_blocks_unrelated_framework(self) -> None:
        """Even with a Python framework that ISN'T Django, the linker
        must remain closed (e.g., FastAPI repo)."""
        assert should_run_linker(
            "django-third-party-dispatch",
            detected_frameworks={"fastapi", "celery"},
            detected_languages={"python"},
        ) is False
