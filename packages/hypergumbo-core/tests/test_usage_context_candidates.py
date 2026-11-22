# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/framework_patterns.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/reference_candidates.py
"""Which symbol a ``UsageContext``'s handler name binds is a function of the source.

``framework_patterns.resolve_deferred_symbol_refs`` and the name fallback in
``enrich_symbols`` each kept one flat ``name -> Symbol`` table over every
symbol of every language, last write wins, in the order ``symbols`` arrived.
Measured on pretix: the ``UsageContext`` of the Django route ``^locale/set$``
carried ``symbol_ref`` = the Python view ``LocaleSet`` in one run and
``class LocaleSet`` of a vendored ``pdf.worker.js`` in another.

The rule is ``reference_candidates``': a reference written in a file of
language L names code of language L; a full name outranks a short one; and
candidates are indexed in source order. A list-order defect is invisible to
a single ordering, so every binding test here hands the pass the SAME symbols
in EVERY order and asserts one answer.
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from hypergumbo_core.framework_patterns import (
    FrameworkPatternDef,
    Pattern,
    UsagePatternSpec,
    _module_match_depth,
    enrich_symbols,
    resolve_deferred_symbol_refs,
)
from hypergumbo_core.ir import Span, Symbol, UsageContext
from hypergumbo_core.reference_candidates import (
    candidate_languages,
    source_order_key,
    two_tier_name_table,
)


def _decl(
    language: str | None, path: str, line: int, name: str, kind: str = "function",
    qualified_name: str | None = None,
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{line}-{line + 2}:{name}:{kind}",
        name=name,
        qualified_name=qualified_name if qualified_name is not None else name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=line, end_line=line + 2, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
        meta={},
    )


def _ctx(path: str, **metadata: str) -> UsageContext:
    return UsageContext.create(
        kind="call", context_name="path", position="args[1]", path=path,
        span=Span(5, 5, 0, 50), symbol_ref=None, metadata=dict(metadata),
    )


def _resolved_in_every_order(
    path: str, metadata: dict[str, str], *declarations: Symbol,
) -> tuple[str | None, object]:
    """``(symbol_ref, stats)`` of one context, asserted the same for every
    ordering of the symbols."""
    outcomes = []
    for order in itertools.permutations(declarations):
        ctx = _ctx(path, **metadata)
        stats = resolve_deferred_symbol_refs(list(order), [ctx])
        outcomes.append((ctx.symbol_ref, stats))
    assert all(o == outcomes[0] for o in outcomes), [o[0] for o in outcomes]
    return outcomes[0]


class TestDeferredResolutionCandidates:
    def test_a_python_reference_does_not_bind_a_javascript_class(self) -> None:
        """The pretix case. ``urls.py`` declares nothing, so its language is
        read off its extension."""
        view = _decl("python", "base/views.py", 10, "LocaleSet", "class")
        vendored = _decl(
            "javascript", "static/pdfjs/pdf.worker.js", 900, "LocaleSet", "class",
        )

        ref, stats = _resolved_in_every_order(
            "base/urls.py", {"view_name": "LocaleSet"}, view, vendored,
        )

        assert ref == view.id
        assert stats.resolved_exact == 1
        assert stats.resolved_by_position == 0

    def test_host_language_is_read_from_the_files_own_declarations(self) -> None:
        """A file whose extension says nothing: the language of the symbols
        declared in it is the reference's host language."""
        in_file = _decl("ruby", "config/routes", 1, "draw")
        handler = _decl("ruby", "app/users.rb", 3, "index")
        other = _decl("php", "legacy/users.php", 3, "index")

        ref, _stats = _resolved_in_every_order(
            "config/routes", {"handler": "index"}, in_file, handler, other,
        )

        assert ref == handler.id

    def test_no_known_host_language_keeps_every_symbol(self) -> None:
        handler = _decl("php", "legacy/users.php", 3, "index")

        ref, _stats = _resolved_in_every_order(
            "config/routes", {"handler": "index"}, handler,
        )

        assert ref == handler.id

    def test_javascript_and_typescript_are_one_candidate_set(self) -> None:
        handler = _decl("javascript", "handlers.js", 3, "listUsers")
        other = _decl("python", "handlers.py", 3, "listUsers")

        ref, _stats = _resolved_in_every_order(
            "router.ts", {"handler": "listUsers"}, handler, other,
        )

        assert ref == handler.id

    def test_a_full_name_outranks_a_short_one(self) -> None:
        nested = _decl(
            "python", "a_views.py", 20, "LoginView", "class",
            qualified_name="Outer.LoginView",
        )
        top_level = _decl("python", "b_views.py", 5, "LoginView", "class")

        ref, stats = _resolved_in_every_order(
            "urls.py", {"view_name": "LoginView"}, nested, top_level,
        )

        # ``nested`` comes first in source order and holds the short name;
        # the reference equals ``top_level``'s full name.
        assert ref == top_level.id
        assert stats.resolved_exact == 1

    def test_a_stand_in_without_a_language_is_not_a_candidate(self) -> None:
        stand_in = _decl(None, "urls.py", 5, "LocaleSet", "route")
        view = _decl("python", "views.py", 10, "LocaleSet", "class")

        ref, _stats = _resolved_in_every_order(
            "urls.py", {"view_name": "LocaleSet"}, stand_in, view,
        )

        assert ref == view.id


class TestDeferredResolutionTies:
    """Several candidates hold the name at the tier that answers."""

    def test_the_module_the_reference_names_decides(self) -> None:
        first = _decl("python", "app1/views.py", 10, "list_users")
        second = _decl("python", "app2/views.py", 10, "list_users")

        for dotted, expected in (
            ("app1.views.list_users", first), ("app2.views.list_users", second),
        ):
            ref, stats = _resolved_in_every_order(
                "urls.py", {"view_name": dotted}, first, second,
            )
            assert ref == expected.id
            assert stats.resolved_path_hint == 1
            assert stats.resolved_by_position == 0

    def test_the_module_an_analyzer_recorded_decides(self) -> None:
        """``view_name`` holds the last segment only; the analyzer wrote the
        module the reference's import names beside it. The tree here was
        analysed from below its import root, so the module is longer than
        either file's path."""
        organizer = _decl("python", "control/views/organizer.py", 300, "MailSetup", "class")
        event = _decl("python", "control/views/event.py", 800, "MailSetup", "class")

        for module, expected in (
            ("pretix.control.views.organizer", organizer),
            ("pretix.control.views.event", event),
        ):
            ref, stats = _resolved_in_every_order(
                "control/urls.py", {"view_name": "MailSetup", "module_name": module},
                organizer, event,
            )
            assert ref == expected.id
            assert stats.resolved_path_hint == 1

    def test_a_package_the_reference_names_decides(self) -> None:
        """``from app import views`` where ``views`` is a package: the holder
        declared inside it is the one."""
        ours = _decl("python", "app/views/auth.py", 10, "LoginView", "class")
        theirs = _decl("python", "other/views/auth.py", 10, "LoginView", "class")

        ref, stats = _resolved_in_every_order(
            "app/urls.py", {"view_name": "LoginView", "module_name": "app.views"},
            ours, theirs,
        )

        assert ref == ours.id
        assert stats.resolved_path_hint == 1

    def test_a_module_no_holder_is_in_decides_nothing(self) -> None:
        """``django.contrib.auth.views`` is neither ``app/views.py`` nor
        ``other/views.py``: the pick falls to position, as it would with no
        module at all."""
        first = _decl("python", "app/views.py", 10, "LoginView", "class")
        second = _decl("python", "other/views.py", 10, "LoginView", "class")

        ref, stats = _resolved_in_every_order(
            "urls.py",
            {"view_name": "LoginView", "module_name": "django.contrib.auth.views"},
            first, second,
        )

        assert ref == first.id
        assert stats.resolved_by_position == 1
        assert stats.resolved_path_hint == 0

    @pytest.mark.parametrize(
        ("path", "module", "depth"),
        [
            # the reference is shorter than the file's module path
            ("backend/app/views.py", "views", 1),
            ("backend/app/views.py", "app.views", 2),
            # the file's module path is shorter: analysed below the import root
            ("control/views/organizer.py", "pretix.control.views.organizer", 3),
            # the same name one level up is a different module
            ("app/views.py", "django.contrib.auth.views", 0),
            ("app1/views.py", "app2.views", 0),
            # a package: the file sits inside the module named
            ("app/views/auth.py", "app.views", 2),
            ("app/views/__init__.py", "app.views", 2),
            # other separators a reference can be written with
            ("handlers/users.go", "handlers", 1),
            ("lib/admin/users.rb", "Admin::Users".lower(), 2),
            # nothing to compare
            ("views.py", "", 0),
            ("", "views", 0),
        ],
    )
    def test_module_match_depth(self, path: str, module: str, depth: int) -> None:
        parts = [p for p in module.replace("::", ".").split(".") if p]
        assert _module_match_depth(path, parts) == depth

    def test_the_references_own_file_decides(self) -> None:
        elsewhere = _decl("python", "a_views.py", 10, "handler")
        own = _decl("python", "routes.py", 30, "handler")

        ref, stats = _resolved_in_every_order(
            "routes.py", {"view_name": "handler"}, elsewhere, own,
        )

        assert ref == own.id
        assert stats.resolved_ambiguous == 1

    def test_production_code_outranks_test_code_for_a_production_reference(
        self,
    ) -> None:
        in_tests = _decl("python", "app/tests/test_views.py", 10, "handler")
        production = _decl("python", "app/views.py", 10, "handler")

        ref, stats = _resolved_in_every_order(
            "app/urls.py", {"view_name": "handler"}, in_tests, production,
        )

        assert ref == production.id
        assert stats.resolved_exact == 1

    def test_no_evidence_takes_the_first_in_source_order_and_says_so(self) -> None:
        first = _decl("python", "views1.py", 10, "handler")
        second = _decl("python", "views2.py", 10, "handler")
        third = _decl("python", "views2.py", 40, "handler")

        ref, stats = _resolved_in_every_order(
            "urls.py", {"view_name": "handler"}, first, second, third,
        )

        assert ref == first.id
        assert stats.resolved_by_position == 1
        assert stats.resolved_exact == 0
        assert stats.total_resolved == 1

    def test_a_module_hint_two_candidates_match_is_no_evidence(self) -> None:
        first = _decl("python", "app/views.py", 10, "handler")
        second = _decl("python", "app/views.py", 40, "handler")

        ref, stats = _resolved_in_every_order(
            "urls.py", {"view_name": "app.views.handler"}, first, second,
        )

        assert ref == first.id
        assert stats.resolved_by_position == 1


def _route_pattern() -> FrameworkPatternDef:
    return FrameworkPatternDef(
        id="test-django",
        language="python",
        patterns=[
            Pattern(
                concept="route",
                usage=UsagePatternSpec(kind="^call$", name="^path$"),
                extract={"path": "metadata.route_path"},
            ),
        ],
    )


def _enriched_names_in_every_order(
    path: str, view_name: str, *declarations: Symbol,
) -> list[str]:
    """Ids of the symbols the name fallback stamps ``route`` on, asserted the
    same for every ordering of the symbols."""
    outcomes = []
    for order in itertools.permutations(declarations):
        for s in declarations:
            s.meta = {}
        ctx = _ctx(path, view_name=view_name, route_path="/x/")
        with patch(
            "hypergumbo_core.framework_patterns.load_framework_patterns",
            return_value=_route_pattern(),
        ):
            enrich_symbols(list(order), {"test-django"}, usage_contexts=[ctx])
        outcomes.append(sorted(
            s.id for s in declarations
            if any(c.get("concept") == "route" for c in (s.meta or {}).get("concepts", []))
        ))
    assert all(o == outcomes[0] for o in outcomes), outcomes
    return outcomes[0]


class TestEnrichmentNameFallback:
    """``enrich_symbols`` phase 3, for a context whose ``symbol_ref`` is unset."""

    def test_a_python_reference_does_not_enrich_a_javascript_function(self) -> None:
        view = _decl("python", "views.py", 10, "locale_set")
        vendored = _decl("javascript", "static/pdf.worker.js", 900, "locale_set")

        assert _enriched_names_in_every_order(
            "urls.py", "locale_set", view, vendored,
        ) == [view.id]

    def test_a_callable_outranks_a_class_of_the_same_short_name(self) -> None:
        klass = _decl("python", "a_models.py", 10, "Admin.export", "class")
        method = _decl("python", "views.py", 10, "Report.export", "method")

        assert _enriched_names_in_every_order(
            "urls.py", "export", klass, method,
        ) == [method.id]

    def test_among_callables_the_first_in_source_order_is_taken(self) -> None:
        first = _decl("python", "views1.py", 10, "A.list_users", "method")
        second = _decl("python", "views2.py", 10, "B.list_users", "method")

        assert _enriched_names_in_every_order(
            "urls.py", "list_users", first, second,
        ) == [first.id]

    def test_a_full_name_outranks_a_short_one(self) -> None:
        nested = _decl(
            "python", "a_views.py", 20, "LoginView", "function",
            qualified_name="Outer.LoginView",
        )
        top_level = _decl("python", "b_views.py", 5, "LoginView", "class")

        assert _enriched_names_in_every_order(
            "urls.py", "LoginView", nested, top_level,
        ) == [top_level.id]

    def test_nothing_of_the_host_language_holds_the_name(self) -> None:
        vendored = _decl("javascript", "static/pdf.worker.js", 900, "locale_set")

        assert _enriched_names_in_every_order("urls.py", "locale_set", vendored) == []


class TestReferenceCandidates:
    def test_source_order_is_path_then_span_then_id(self) -> None:
        a = _decl("python", "a.py", 30, "z")
        b = _decl("python", "b.py", 1, "a")
        c = _decl("python", "a.py", 2, "y")
        no_span = Symbol(
            id="python:a.py:0-0:m:module", name="m", kind="module",
            language="python", path="a.py", span=None,
        )

        ordered = sorted([a, b, c, no_span], key=source_order_key)

        assert ordered == [no_span, c, a, b]

    def test_candidate_languages(self) -> None:
        assert candidate_languages([None]) is None
        assert candidate_languages([]) is None
        assert candidate_languages(["python", None]) == frozenset({"python"})
        assert candidate_languages(["typescript"]) == frozenset(
            {"javascript", "typescript"}
        )
        assert candidate_languages(["vue", "javascript"]) == frozenset(
            {"vue", "javascript", "typescript"}
        )

    def test_two_tier_table_reports_the_tied_keys_and_their_holders(self) -> None:
        full = _decl("python", "a.py", 1, "run", qualified_name="pkg.run")
        short_1 = _decl("python", "b.py", 1, "run", qualified_name="b.run")
        short_2 = _decl("python", "c.py", 1, "pkg.run", qualified_name="x.pkg.run")
        same = _decl("python", "d.py", 1, "solo")

        tiers = two_tier_name_table([full, short_1, short_2, same])

        # ``pkg.run`` is ``full``'s qualified name: one holder at that tier,
        # though ``short_2`` is also NAMED ``pkg.run``.
        assert tiers.symbol_by_name["pkg.run"] is full
        assert "pkg.run" not in tiers.collisions
        # ``run`` is nobody's qualified name and two symbols' name.
        assert tiers.symbol_by_name["run"] is full
        assert tiers.tied["run"] == (full, short_1)
        # A symbol whose name and qualified name are one string is one holder.
        assert "solo" not in tiers.collisions
        assert tiers.collisions == frozenset({"run"})
