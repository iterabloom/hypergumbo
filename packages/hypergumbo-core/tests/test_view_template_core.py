# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shared view-template linker core (WI-mifif).

The core extracts the probe-and-emit loop from the original Rails-only
``view_template.py`` so that Django, Phoenix, Spring, and Laravel strategies
can plug into the same pipeline. Verifies the contract that strategies
expose ``TemplateRenderEmission`` objects and the core filters by
filesystem existence, deduplicates template symbols, and emits ``renders``
edges with the convention metadata.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Symbol, Span
from hypergumbo_core.linkers._view_template_core import (
    PASS_ID,
    ExplicitStringStrategy,
    MethodNameStrategy,
    TemplateCandidate,
    TemplateRenderEmission,
    TemplateStrategy,
    link_via_strategies,
)
from hypergumbo_core.linkers.registry import LinkerContext


def _method_symbol(name: str = "UsersController#index") -> Symbol:
    return Symbol(
        id=f"ruby:app/controllers/users_controller.rb:5-10:{name}:method",
        name=name,
        kind="method",
        language="ruby",
        path="app/controllers/users_controller.rb",
        span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
        origin="ruby",
    )


class _StubStrategy(TemplateStrategy):
    """Yields a fixed list of emissions, used to exercise the core in isolation."""

    def __init__(self, emissions: list[TemplateRenderEmission]) -> None:
        self._emissions = emissions

    def find_emissions(self, ctx: LinkerContext):
        yield from self._emissions


class TestLinkViaStrategies:
    """Tests for the shared probe-and-emit loop."""

    def test_empty_strategies_returns_empty_result(self, tmp_path: Path) -> None:
        ctx = LinkerContext(repo_root=tmp_path)

        result = link_via_strategies(ctx, [])

        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None
        assert result.run.pass_id == PASS_ID

    def test_existing_candidate_emits_edge_and_template_symbol(
        self, tmp_path: Path
    ) -> None:
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "users.html").write_text("<h1>Users</h1>")

        method = _method_symbol()
        emission = TemplateRenderEmission(
            action_symbol_id=method.id,
            line=5,
            detection_pattern="implicit_convention",
            candidates=(
                TemplateCandidate(
                    path=Path("templates/users.html"), language="html"
                ),
            ),
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[method])

        result = link_via_strategies(ctx, [_StubStrategy([emission])])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == method.id
        assert edge.edge_type == "references"
        assert (edge.meta or {}).get("ref_construct") == "view_render"
        assert edge.evidence_type == "naming_convention"
        assert edge.confidence == 0.85
        assert (edge.meta or {}).get("detection_pattern") == "implicit_convention"
        assert edge.line == 5

        assert len(result.symbols) == 1
        template_sym = result.symbols[0]
        assert template_sym.kind == "template"
        assert template_sym.language == "html"
        assert template_sym.name == "users.html"
        assert template_sym.path == "templates/users.html"
        assert template_sym.origin == [PASS_ID]
        assert edge.dst == template_sym.id

    def test_missing_candidate_emits_nothing(self, tmp_path: Path) -> None:
        method = _method_symbol()
        emission = TemplateRenderEmission(
            action_symbol_id=method.id,
            line=5,
            detection_pattern="implicit_convention",
            candidates=(
                TemplateCandidate(
                    path=Path("templates/missing.html"), language="html"
                ),
            ),
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[method])

        result = link_via_strategies(ctx, [_StubStrategy([emission])])

        assert result.edges == []
        assert result.symbols == []

    def test_multiple_candidates_emit_per_existing(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "show.html").write_text("html")
        (template_dir / "show.json").write_text("json")
        # show.xml is intentionally absent — should not emit

        method = _method_symbol(name="UsersController#show")
        emission = TemplateRenderEmission(
            action_symbol_id=method.id,
            line=10,
            detection_pattern="implicit_convention",
            candidates=(
                TemplateCandidate(path=Path("templates/show.html"), language="html"),
                TemplateCandidate(path=Path("templates/show.json"), language="json"),
                TemplateCandidate(path=Path("templates/show.xml"), language="xml"),
            ),
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[method])

        result = link_via_strategies(ctx, [_StubStrategy([emission])])

        assert len(result.edges) == 2
        assert len(result.symbols) == 2
        dsts = {e.dst for e in result.edges}
        assert "html:templates/show.html:1-1:show.html:template" in dsts
        assert "json:templates/show.json:1-1:show.json:template" in dsts

    def test_dedup_template_symbols_across_emissions(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "shared.html").write_text("shared")

        m1 = _method_symbol(name="UsersController#index")
        m2 = _method_symbol(name="UsersController#alt")
        shared = TemplateCandidate(
            path=Path("templates/shared.html"), language="html"
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[m1, m2])

        result = link_via_strategies(
            ctx,
            [
                _StubStrategy(
                    [
                        TemplateRenderEmission(
                            action_symbol_id=m1.id,
                            line=5,
                            detection_pattern="implicit_convention",
                            candidates=(shared,),
                        ),
                        TemplateRenderEmission(
                            action_symbol_id=m2.id,
                            line=15,
                            detection_pattern="implicit_convention",
                            candidates=(shared,),
                        ),
                    ]
                )
            ],
        )

        # Two edges (one per method), one shared symbol.
        assert len(result.edges) == 2
        assert len(result.symbols) == 1

    def test_duplicate_emission_dedup_edge(self, tmp_path: Path) -> None:
        """Two strategies both emit the same (action, template) — only one edge."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "shared.html").write_text("shared")

        method = _method_symbol()
        shared_candidate = TemplateCandidate(
            path=Path("templates/shared.html"), language="html"
        )
        em = TemplateRenderEmission(
            action_symbol_id=method.id,
            line=5,
            detection_pattern="implicit_convention",
            candidates=(shared_candidate,),
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[method])

        result = link_via_strategies(
            ctx, [_StubStrategy([em]), _StubStrategy([em])]
        )

        # Two strategies, same emission → one edge, one symbol.
        assert len(result.edges) == 1
        assert len(result.symbols) == 1

    def test_multiple_strategies_compose(self, tmp_path: Path) -> None:
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        (template_dir / "from_a.html").write_text("a")
        (template_dir / "from_b.html").write_text("b")

        m_a = _method_symbol(name="A#index")
        m_b = _method_symbol(name="B#index")
        s_a = _StubStrategy(
            [
                TemplateRenderEmission(
                    action_symbol_id=m_a.id,
                    line=1,
                    detection_pattern="implicit_convention",
                    candidates=(
                        TemplateCandidate(
                            path=Path("templates/from_a.html"), language="html"
                        ),
                    ),
                )
            ]
        )
        s_b = _StubStrategy(
            [
                TemplateRenderEmission(
                    action_symbol_id=m_b.id,
                    line=2,
                    detection_pattern="explicit_string",
                    candidates=(
                        TemplateCandidate(
                            path=Path("templates/from_b.html"), language="html"
                        ),
                    ),
                )
            ]
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[m_a, m_b])

        result = link_via_strategies(ctx, [s_a, s_b])

        assert len(result.edges) == 2
        patterns = {(edge.meta or {}).get("detection_pattern") for edge in result.edges}
        assert patterns == {"implicit_convention", "explicit_string"}


class TestMethodNameStrategy:
    """Tests for the MethodNameStrategy abstract base."""

    def test_concrete_subclass_yields_emissions_per_action_method(
        self, tmp_path: Path
    ) -> None:
        controller = Symbol(
            id="ruby:app/controllers/users_controller.rb:1-50:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
            meta={"base_classes": ["ApplicationController"]},
            origin="ruby",
        )
        method = _method_symbol(name="UsersController#index")

        class _DummyStrategy(MethodNameStrategy):
            def is_action_class(self, sym, ctx):
                return sym.kind == "class" and any(
                    b == "ApplicationController"
                    for b in (sym.meta or {}).get("base_classes", [])
                )

            def is_action_method(self, method_name):
                return method_name == "index"

            def candidates_for(self, class_name, method, ctx):
                return [
                    TemplateCandidate(
                        path=Path(f"views/{class_name.lower()}/{method.name.rsplit('#', 1)[1]}.html"),
                        language="html",
                    )
                ]

        strat = _DummyStrategy()
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, method])
        emissions = list(strat.find_emissions(ctx))

        assert len(emissions) == 1
        em = emissions[0]
        assert em.action_symbol_id == method.id
        assert em.detection_pattern == "implicit_convention"
        assert em.candidates[0].path == Path("views/userscontroller/index.html")

    def test_no_action_class_yields_nothing(self, tmp_path: Path) -> None:
        method = _method_symbol(name="NotAController#index")

        class _DummyStrategy(MethodNameStrategy):
            def is_action_class(self, sym, ctx):
                return False

            def is_action_method(self, method_name):
                return True

            def candidates_for(self, class_name, method, ctx):  # pragma: no cover - unreachable
                return []

        strat = _DummyStrategy()
        ctx = LinkerContext(repo_root=tmp_path, symbols=[method])
        assert list(strat.find_emissions(ctx)) == []

    def test_empty_candidates_skipped(self, tmp_path: Path) -> None:
        """A strategy returning an empty candidate list yields no emission."""
        controller = Symbol(
            id="ruby:app/controllers/users_controller.rb:1-50:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
            meta={"base_classes": ["ApplicationController"]},
            origin="ruby",
        )
        method = _method_symbol(name="UsersController#index")

        class _DummyStrategy(MethodNameStrategy):
            def is_action_class(self, sym, ctx):
                return sym.kind == "class"

            def is_action_method(self, method_name):
                return True

            def candidates_for(self, class_name, method, ctx):
                return []  # strategy declines to emit for this action

        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, method])
        emissions = list(_DummyStrategy().find_emissions(ctx))
        assert emissions == []

    def test_skip_method_when_predicate_false(self, tmp_path: Path) -> None:
        controller = Symbol(
            id="ruby:app/controllers/users_controller.rb:1-50:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users_controller.rb",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
            meta={"base_classes": ["ApplicationController"]},
            origin="ruby",
        )
        action = _method_symbol(name="UsersController#index")
        non_action = _method_symbol(name="UsersController#_helper")

        class _DummyStrategy(MethodNameStrategy):
            def is_action_class(self, sym, ctx):
                return sym.kind == "class"

            def is_action_method(self, method_name):
                return not method_name.startswith("_")

            def candidates_for(self, class_name, method, ctx):
                return [
                    TemplateCandidate(
                        path=Path("views/index.html"), language="html"
                    )
                ]

        strat = _DummyStrategy()
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action, non_action])
        emissions = list(strat.find_emissions(ctx))
        assert len(emissions) == 1
        assert emissions[0].action_symbol_id == action.id


class TestExplicitStringStrategy:
    """Tests for the ExplicitStringStrategy abstract base."""

    def test_empty_candidates_skipped(self, tmp_path: Path) -> None:
        """An explicit-string strategy returning empty candidates yields nothing."""
        action = _method_symbol(name="users_view")

        class _DummyStrategy(ExplicitStringStrategy):
            def find_string_sites(self, ctx):
                yield (action, "weird://protocol/show", 1, "render_call")

            def string_to_candidates(self, string_value, action_symbol, ctx):
                return []  # declines to map this string

        ctx = LinkerContext(repo_root=tmp_path, symbols=[action])
        emissions = list(_DummyStrategy().find_emissions(ctx))
        assert emissions == []

    def test_concrete_subclass_emits_via_string_sites(self, tmp_path: Path) -> None:
        action = _method_symbol(name="users_view")

        class _DummyStrategy(ExplicitStringStrategy):
            def find_string_sites(self, ctx):
                yield (action, "users/show.html", 7, "render_call")

            def string_to_candidates(self, string_value, action_symbol, ctx):
                assert action_symbol is action
                return [
                    TemplateCandidate(
                        path=Path(f"templates/{string_value}"), language="html"
                    )
                ]

        strat = _DummyStrategy()
        ctx = LinkerContext(repo_root=tmp_path, symbols=[action])
        emissions = list(strat.find_emissions(ctx))

        assert len(emissions) == 1
        em = emissions[0]
        assert em.action_symbol_id == action.id
        assert em.line == 7
        assert em.detection_pattern == "render_call"
        assert em.candidates[0].path == Path("templates/users/show.html")
