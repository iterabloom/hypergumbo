# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the React component linker.

Covers: JSX component detection, PascalCase filtering, component map building,
self-reference prevention, React built-in filtering, dotted component names,
and registry integration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Span, Symbol


def _make_span() -> Span:
    return Span(start_line=1, end_line=10, start_col=0, end_col=0)


def _make_js_sym(
    name: str,
    path: str = "src/App.tsx",
    language: str = "typescript",
    kind: str = "function",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-10:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=_make_span(),
    )


class TestIsPascalCase:
    """Tests for _is_pascal_case helper."""

    def test_pascal_case(self) -> None:
        from hypergumbo_core.linkers.react_component import _is_pascal_case

        assert _is_pascal_case("Button")
        assert _is_pascal_case("UserProfile")
        assert _is_pascal_case("MyApp")

    def test_not_pascal_case(self) -> None:
        from hypergumbo_core.linkers.react_component import _is_pascal_case

        assert not _is_pascal_case("button")
        assert not _is_pascal_case("myFunc")
        assert not _is_pascal_case("")

    def test_all_caps_with_underscore(self) -> None:
        from hypergumbo_core.linkers.react_component import _is_pascal_case

        assert not _is_pascal_case("MY_CONSTANT")
        assert not _is_pascal_case("API_KEY")

    def test_single_uppercase_letter(self) -> None:
        from hypergumbo_core.linkers.react_component import _is_pascal_case

        assert _is_pascal_case("A")


class TestBuildComponentMap:
    """Tests for _build_component_map."""

    def test_pascal_case_function(self) -> None:
        from hypergumbo_core.linkers.react_component import _build_component_map

        sym = _make_js_sym("Button", path="src/Button.tsx")
        result = _build_component_map([sym])
        assert "Button" in result
        assert result["Button"] is sym

    def test_pascal_case_class(self) -> None:
        from hypergumbo_core.linkers.react_component import _build_component_map

        sym = _make_js_sym("App", kind="class")
        result = _build_component_map([sym])
        assert "App" in result

    def test_filters_lowercase(self) -> None:
        from hypergumbo_core.linkers.react_component import _build_component_map

        sym = _make_js_sym("helper", path="src/utils.ts")
        result = _build_component_map([sym])
        assert len(result) == 0

    def test_filters_non_js_ts(self) -> None:
        from hypergumbo_core.linkers.react_component import _build_component_map

        sym = Symbol(
            id="python:app.py:1-1:MyClass:class",
            name="MyClass",
            kind="class",
            language="python",
            path="app.py",
            span=_make_span(),
        )
        result = _build_component_map([sym])
        assert len(result) == 0

    def test_filters_non_function_class(self) -> None:
        from hypergumbo_core.linkers.react_component import _build_component_map

        sym = _make_js_sym("MyVar", kind="variable")
        result = _build_component_map([sym])
        assert len(result) == 0


class TestScanForJsxComponents:
    """Tests for _scan_file_for_jsx_components."""

    def test_basic_jsx_component(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("return <Button onClick={handleClick} />;")
        result = _scan_file_for_jsx_components(f)
        assert "Button" in result

    def test_multiple_components(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("""
return (
    <Header>
        <Sidebar />
        <MainContent />
    </Header>
);
""")
        result = _scan_file_for_jsx_components(f)
        assert "Header" in result
        assert "Sidebar" in result
        assert "MainContent" in result

    def test_ignores_html_elements(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("<div><span>hello</span></div>")
        result = _scan_file_for_jsx_components(f)
        assert result == []

    def test_ignores_react_builtins(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("""
return (
    <React.Fragment>
        <Suspense fallback={<div>Loading</div>}>
            <StrictMode>
                <MyComponent />
            </StrictMode>
        </Suspense>
    </React.Fragment>
);
""")
        result = _scan_file_for_jsx_components(f)
        # Only MyComponent should be detected, not Fragment/Suspense/StrictMode
        assert result == ["MyComponent"]

    def test_dotted_component_name(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("<Form.Control />")
        result = _scan_file_for_jsx_components(f)
        assert "Form.Control" in result

    def test_nonexistent_file(self) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        result = _scan_file_for_jsx_components(Path("/nonexistent/file.tsx"))
        assert result == []

    def test_self_closing_tag(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("<Icon name='home' />")
        result = _scan_file_for_jsx_components(f)
        assert "Icon" in result

    def test_multiline_jsx(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import _scan_file_for_jsx_components

        f = tmp_path / "App.tsx"
        f.write_text("""<Modal
    isOpen={true}
    onClose={handleClose}
>""")
        result = _scan_file_for_jsx_components(f)
        assert "Modal" in result


class TestLinkReactComponents:
    """Tests for link_react_components."""

    def test_basic_link(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        # Component definition in one file
        button_sym = _make_js_sym("Button", path="src/Button.tsx")

        # Usage in another file
        app_file = tmp_path / "src" / "App.tsx"
        app_file.parent.mkdir(parents=True, exist_ok=True)
        app_file.write_text("return <Button onClick={submit} />;")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "renders_component"
        assert edge.dst == button_sym.id
        assert edge.confidence == 0.80
        assert edge.evidence_type == "jsx_element"

    def test_no_components(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        sym = _make_js_sym("helper", path="src/utils.ts")
        result = link_react_components(tmp_path, [sym])
        assert result.edges == []
        assert result.run is not None

    def test_no_jsx_usage(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "src" / "App.tsx"
        app_file.parent.mkdir(parents=True, exist_ok=True)
        app_file.write_text("const x = 1;")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert result.edges == []

    def test_jsx_component_not_in_map(self, tmp_path: Path) -> None:
        """JSX element with no matching component symbol produces no edge."""
        from hypergumbo_core.linkers.react_component import link_react_components

        # Button is a component but UnknownComponent is not in symbols
        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "App.tsx"
        app_file.write_text("<UnknownComponent />")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert result.edges == []

    def test_self_reference_excluded(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        # Component using itself (shouldn't create edge)
        comp_file = tmp_path / "RecursiveComp.tsx"
        comp_file.write_text("return <RecursiveComp />;")
        sym = _make_js_sym("RecursiveComp", path=str(comp_file))

        result = link_react_components(tmp_path, [sym])
        assert result.edges == []

    def test_self_reference_relative_path(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        # Component using itself with relative path
        comp_file = tmp_path / "Comp.tsx"
        comp_file.write_text("return <Comp />;")
        sym = _make_js_sym("Comp", path="Comp.tsx")

        result = link_react_components(tmp_path, [sym])
        assert result.edges == []

    def test_deduplicates_edges(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "App.tsx"
        app_file.write_text("<Button /><Button variant='primary' />")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert len(result.edges) == 1

    def test_deduplicates_file_paths(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "App.tsx"
        app_file.write_text("<Button />")
        sym1 = _make_js_sym("App", path=str(app_file))
        sym2 = _make_js_sym("helper", path=str(app_file))

        result = link_react_components(tmp_path, [sym1, sym2, button_sym])
        assert len(result.edges) == 1

    def test_multiple_components_multiple_files(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        header_sym = _make_js_sym("Header", path="src/Header.tsx")
        footer_sym = _make_js_sym("Footer", path="src/Footer.tsx")

        app_file = tmp_path / "App.tsx"
        app_file.write_text("<Header /><Footer />")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, header_sym, footer_sym])
        assert len(result.edges) == 2
        dst_ids = {e.dst for e in result.edges}
        assert header_sym.id in dst_ids
        assert footer_sym.id in dst_ids

    def test_empty_inputs(self) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        result = link_react_components(Path("/test"), [])
        assert result.edges == []
        assert result.run is not None

    def test_run_metadata(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        result = link_react_components(tmp_path, [])
        assert isinstance(result.run, AnalysisRun)
        assert result.run.duration_ms >= 0

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_sym = _make_js_sym("App", path="/nonexistent/App.tsx")

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert result.edges == []

    def test_javascript_language(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.jsx", language="javascript")
        app_file = tmp_path / "App.jsx"
        app_file.write_text("<Button />")
        app_sym = _make_js_sym("App", path=str(app_file), language="javascript")

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert len(result.edges) == 1

    def test_dotted_component_resolves(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        control_sym = _make_js_sym("Control", path="src/Form/Control.tsx")
        app_file = tmp_path / "App.tsx"
        app_file.write_text("<Form.Control />")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, control_sym])
        assert len(result.edges) == 1
        assert result.edges[0].dst == control_sym.id

    def test_filters_non_js_ts_symbols(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        python_sym = Symbol(
            id="python:app.py:1-1:main:function",
            name="main",
            kind="function",
            language="python",
            path="app.py",
            span=_make_span(),
        )
        result = link_react_components(tmp_path, [python_sym, button_sym])
        assert result.edges == []

    def test_non_relative_path_fallback(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        app_file = other_dir / "App.tsx"
        app_file.write_text("<Button />")
        app_sym = _make_js_sym("App", path=str(app_file))

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        result = link_react_components(repo_root, [app_sym, button_sym])
        assert len(result.edges) == 1
        assert str(other_dir) in result.edges[0].src

    def test_src_id_format(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.react_component import link_react_components

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "src" / "App.tsx"
        app_file.parent.mkdir(parents=True, exist_ok=True)
        app_file.write_text("<Button />")
        app_sym = _make_js_sym("App", path=str(app_file))

        result = link_react_components(tmp_path, [app_sym, button_sym])
        assert len(result.edges) == 1
        assert result.edges[0].src.startswith("typescript:")
        assert "Button" in result.edges[0].src
        assert "jsx_usage" in result.edges[0].src


class TestReactComponentRegistry:
    """Tests for registry integration."""

    def test_registered(self) -> None:
        from hypergumbo_core.linkers.registry import get_linker
        import hypergumbo_core.linkers.react_component

        linker = get_linker("react_component")
        assert linker is not None
        assert linker.name == "react_component"

    def test_activation_framework(self) -> None:
        from hypergumbo_core.linkers.registry import get_linker
        import hypergumbo_core.linkers.react_component

        linker = get_linker("react_component")
        assert linker.activation.should_run({"react"}, set())
        assert not linker.activation.should_run({"vue"}, set())
        assert not linker.activation.should_run(set(), {"javascript", "typescript"})

    def test_requirements(self) -> None:
        from hypergumbo_core.linkers.registry import get_linker
        import hypergumbo_core.linkers.react_component

        linker = get_linker("react_component")
        assert len(linker.requirements) == 2
        req_names = {r.name for r in linker.requirements}
        assert "js_ts_files" in req_names
        assert "react_components" in req_names

    def test_requirements_met(self) -> None:
        from hypergumbo_core.linkers.registry import LinkerContext, check_linker_requirements
        import hypergumbo_core.linkers.react_component

        js_sym = _make_js_sym("App")
        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[js_sym, button_sym],
            detected_frameworks={"react"},
        )
        diagnostics = check_linker_requirements(ctx)
        diag = next((d for d in diagnostics if d.linker_name == "react_component"), None)
        assert diag is not None
        assert diag.all_met

    def test_requirements_unmet(self) -> None:
        from hypergumbo_core.linkers.registry import LinkerContext, check_linker_requirements
        import hypergumbo_core.linkers.react_component

        # Only lowercase symbols - no React components
        sym = _make_js_sym("helper", kind="function")
        ctx = LinkerContext(
            repo_root=Path("/test"),
            symbols=[sym],
            detected_frameworks={"react"},
        )
        diagnostics = check_linker_requirements(ctx)
        diag = next((d for d in diagnostics if d.linker_name == "react_component"), None)
        assert diag is not None
        assert not diag.all_met

    def test_via_registry_dispatch(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.registry import LinkerContext, run_linker
        import hypergumbo_core.linkers.react_component

        button_sym = _make_js_sym("Button", path="src/Button.tsx")
        app_file = tmp_path / "App.tsx"
        app_file.write_text("<Button />")
        app_sym = _make_js_sym("App", path=str(app_file))

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_sym, button_sym],
            detected_frameworks={"react"},
        )
        result = run_linker("react_component", ctx)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "renders_component"
