# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Vue component linker.

The Vue component linker resolves `imports_component` edges from the Vue
analyzer, which have raw import paths (e.g., './Header.vue') as their dst,
to actual symbol-to-symbol edges. This enables component composition graphs
and reduces Vue component orphan rates.
"""

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.vue_component import link_vue_components
from hypergumbo_core.linkers.registry import LinkerContext


def _sym(
    id: str,
    name: str,
    kind: str,
    path: str,
    language: str = "vue",
    start: int = 1,
    end: int = 5,
    meta: dict | None = None,
) -> Symbol:
    """Helper to create a Symbol with minimal boilerplate."""
    return Symbol(
        id=id,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
        meta=meta or {},
    )


def _edge(src: str, dst: str, edge_type: str = "imports_component") -> Edge:
    """Helper to create an Edge with minimal boilerplate."""
    return Edge.create(
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=1,
        origin="test",
        origin_run_id="test-run",
        evidence_type="import",
        confidence=0.95,
    )


class TestVueComponentLinker:
    """Tests for Vue component import resolution."""

    def test_resolves_relative_import(self, tmp_path: Path) -> None:
        """Resolves ./Header.vue import from App.vue to Header.vue component."""
        # Create .vue files on disk
        (tmp_path / "App.vue").write_text("<template><Header/></template>")
        (tmp_path / "Header.vue").write_text("<template><h1>Hi</h1></template>")

        # App.vue has a component_ref for Header
        app_ref = _sym(
            "vue:App.vue:component_ref:1:Header", "Header", "component_ref",
            path="App.vue", meta={"import_path": "./Header.vue"},
        )
        # Header.vue has a method symbol (representing content)
        header_method = _sym(
            "vue:Header.vue:method:3:render", "render", "method",
            path="Header.vue",
        )

        # The raw-path edge from the analyzer
        raw_edge = _edge(app_ref.id, "./Header.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref, header_method],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        # Should create a component_file symbol for Header.vue
        file_symbols = [s for s in result.symbols if s.kind == "component_file"]
        assert len(file_symbols) >= 1
        header_file = next((s for s in file_symbols if "Header.vue" in s.path), None)
        assert header_file is not None

        # Should create a resolved edge from component_ref to component_file
        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1
        assert resolved[0].src == app_ref.id
        assert resolved[0].dst == header_file.id

    def test_resolves_subdirectory_import(self, tmp_path: Path) -> None:
        """Resolves ./components/Button.vue from parent directory."""
        comp_dir = tmp_path / "components"
        comp_dir.mkdir()
        (tmp_path / "App.vue").write_text("<template><Button/></template>")
        (comp_dir / "Button.vue").write_text("<template><button/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Button", "Button", "component_ref",
            path="App.vue", meta={"import_path": "./components/Button.vue"},
        )
        raw_edge = _edge(app_ref.id, "./components/Button.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        file_symbols = [s for s in result.symbols if s.kind == "component_file"]
        assert len(file_symbols) >= 1
        btn_file = next((s for s in file_symbols if "Button.vue" in s.path), None)
        assert btn_file is not None

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1
        assert resolved[0].dst == btn_file.id

    def test_resolves_at_alias(self, tmp_path: Path) -> None:
        """Resolves @/components/Modal.vue using @ = src/ convention."""
        src_dir = tmp_path / "src" / "components"
        src_dir.mkdir(parents=True)
        (tmp_path / "src" / "App.vue").write_text("<template><Modal/></template>")
        (src_dir / "Modal.vue").write_text("<template><div/></template>")

        app_ref = _sym(
            "vue:src/App.vue:component_ref:1:Modal", "Modal", "component_ref",
            path="src/App.vue", meta={"import_path": "@/components/Modal.vue"},
        )
        raw_edge = _edge(app_ref.id, "@/components/Modal.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        file_symbols = [s for s in result.symbols if s.kind == "component_file"]
        modal_file = next((s for s in file_symbols if "Modal.vue" in s.path), None)
        assert modal_file is not None

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1
        assert resolved[0].dst == modal_file.id

    def test_no_duplicate_file_symbols(self, tmp_path: Path) -> None:
        """Multiple imports of same component create only one file symbol."""
        (tmp_path / "Header.vue").write_text("<template><h1/></template>")
        (tmp_path / "Page1.vue").write_text("<template><Header/></template>")
        (tmp_path / "Page2.vue").write_text("<template><Header/></template>")

        ref1 = _sym(
            "vue:Page1.vue:component_ref:1:Header", "Header", "component_ref",
            path="Page1.vue", meta={"import_path": "./Header.vue"},
        )
        ref2 = _sym(
            "vue:Page2.vue:component_ref:1:Header", "Header", "component_ref",
            path="Page2.vue", meta={"import_path": "./Header.vue"},
        )
        edge1 = _edge(ref1.id, "./Header.vue")
        edge2 = _edge(ref2.id, "./Header.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[ref1, ref2],
            edges=[edge1, edge2],
        )
        result = link_vue_components(ctx)

        file_symbols = [s for s in result.symbols if s.kind == "component_file"]
        header_files = [s for s in file_symbols if "Header.vue" in s.path]
        assert len(header_files) == 1

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 2
        assert all(e.dst == header_files[0].id for e in resolved)

    def test_unresolved_import_skipped(self, tmp_path: Path) -> None:
        """Imports to non-existent .vue files produce no resolved edges."""
        (tmp_path / "App.vue").write_text("<template><Missing/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Missing", "Missing", "component_ref",
            path="App.vue", meta={"import_path": "./Missing.vue"},
        )
        raw_edge = _edge(app_ref.id, "./Missing.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 0

    def test_non_vue_edges_ignored(self, tmp_path: Path) -> None:
        """Only imports_component edges are processed."""
        call_edge = _edge("src1", "dst1", edge_type="calls")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            edges=[call_edge],
        )
        result = link_vue_components(ctx)

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_creates_component_file_for_source_too(self, tmp_path: Path) -> None:
        """Component file symbols are created for the importing file too."""
        (tmp_path / "App.vue").write_text("<template><Header/></template>")
        (tmp_path / "Header.vue").write_text("<template><h1/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Header", "Header", "component_ref",
            path="App.vue", meta={"import_path": "./Header.vue"},
        )
        raw_edge = _edge(app_ref.id, "./Header.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        file_symbols = [s for s in result.symbols if s.kind == "component_file"]
        paths = {s.path for s in file_symbols}
        # Both App.vue and Header.vue should have component_file symbols
        assert any("App.vue" in p for p in paths)
        assert any("Header.vue" in p for p in paths)

    def test_at_alias_without_extension(self, tmp_path: Path) -> None:
        """@ alias import without .vue extension tries appending it."""
        src_dir = tmp_path / "src" / "components"
        src_dir.mkdir(parents=True)
        (tmp_path / "src" / "App.vue").write_text("<template><Card/></template>")
        (src_dir / "Card.vue").write_text("<template><div/></template>")

        app_ref = _sym(
            "vue:src/App.vue:component_ref:1:Card", "Card", "component_ref",
            path="src/App.vue", meta={"import_path": "@/components/Card"},
        )
        raw_edge = _edge(app_ref.id, "@/components/Card")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1

    def test_at_alias_unresolvable(self, tmp_path: Path) -> None:
        """@ alias to nonexistent directory returns no edges."""
        (tmp_path / "App.vue").write_text("<template><Ghost/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Ghost", "Ghost", "component_ref",
            path="App.vue", meta={"import_path": "@/components/Ghost.vue"},
        )
        raw_edge = _edge(app_ref.id, "@/components/Ghost.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 0

    def test_relative_import_without_extension(self, tmp_path: Path) -> None:
        """Relative import without .vue extension tries appending it."""
        (tmp_path / "App.vue").write_text("<template><Nav/></template>")
        (tmp_path / "Nav.vue").write_text("<template><nav/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Nav", "Nav", "component_ref",
            path="App.vue", meta={"import_path": "./Nav"},
        )
        raw_edge = _edge(app_ref.id, "./Nav")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1

    def test_bare_import_path(self, tmp_path: Path) -> None:
        """Bare import path (no ./ prefix) resolves relative to source dir."""
        comp_dir = tmp_path / "components"
        comp_dir.mkdir()
        (comp_dir / "Parent.vue").write_text("<template><Child/></template>")
        (comp_dir / "Child.vue").write_text("<template><span/></template>")

        parent_ref = _sym(
            "vue:components/Parent.vue:component_ref:1:Child", "Child",
            "component_ref", path="components/Parent.vue",
            meta={"import_path": "Child.vue"},
        )
        raw_edge = _edge(parent_ref.id, "Child.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[parent_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1

    def test_bare_import_without_extension(self, tmp_path: Path) -> None:
        """Bare import without .vue extension tries appending it."""
        (tmp_path / "App.vue").write_text("<template><Btn/></template>")
        (tmp_path / "Btn.vue").write_text("<template><button/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Btn", "Btn", "component_ref",
            path="App.vue", meta={"import_path": "Btn"},
        )
        raw_edge = _edge(app_ref.id, "Btn")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 1

    def test_bare_import_unresolvable(self, tmp_path: Path) -> None:
        """Bare import that doesn't resolve to any file is skipped."""
        (tmp_path / "App.vue").write_text("<template><Ghost/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Ghost", "Ghost", "component_ref",
            path="App.vue", meta={"import_path": "Nonexistent"},
        )
        raw_edge = _edge(app_ref.id, "Nonexistent")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 0

    def test_edge_without_source_symbol_skipped(self, tmp_path: Path) -> None:
        """Edge whose src has no matching symbol path is skipped."""
        (tmp_path / "Target.vue").write_text("<template/></template>")

        # Edge with src ID that doesn't match any symbol
        orphan_edge = _edge("vue:unknown.vue:component_ref:1:X", "./Target.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],  # No symbols → src path unresolvable
            edges=[orphan_edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports_component"]
        assert len(resolved) == 0

    def test_activation_requires_vue(self) -> None:
        """Linker activation requires vue framework or language."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("vue-components")
        assert linker is not None
        # Should run when vue is detected
        assert linker.activation.should_run({"vue"}, {"vue"})
        assert linker.activation.should_run(set(), {"vue"})
        # Should not run without vue
        assert not linker.activation.should_run(set(), {"python"})
