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


def _edge(src: str, dst: str, edge_type: str = "imports") -> Edge:
    """Helper to create an Edge with minimal boilerplate.

    Mirrors the post-ADR-0023-§6-Phase-3 Vue analyzer shape: edge_type
    is the canonical 'imports'; the raw component path (the linker's
    resolution input) lives in meta['import_path'].
    """
    return Edge.create(
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=1,
        origin="test",
        origin_run_id="test-run",
        evidence_type="import",
        confidence=0.95,
        meta={"import_path": dst},
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
        file_symbols = [s for s in result.symbols if s.kind == "file" and s.meta and s.meta.get("component_framework") == "vue"]
        assert len(file_symbols) >= 1
        header_file = next((s for s in file_symbols if "Header.vue" in s.path), None)
        assert header_file is not None

        # Should create a resolved edge from component_ref to component_file
        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        file_symbols = [s for s in result.symbols if s.kind == "file" and s.meta and s.meta.get("component_framework") == "vue"]
        assert len(file_symbols) >= 1
        btn_file = next((s for s in file_symbols if "Button.vue" in s.path), None)
        assert btn_file is not None

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        file_symbols = [s for s in result.symbols if s.kind == "file" and s.meta and s.meta.get("component_framework") == "vue"]
        modal_file = next((s for s in file_symbols if "Modal.vue" in s.path), None)
        assert modal_file is not None

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        file_symbols = [s for s in result.symbols if s.kind == "file" and s.meta and s.meta.get("component_framework") == "vue"]
        header_files = [s for s in file_symbols if "Header.vue" in s.path]
        assert len(header_files) == 1

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        file_symbols = [s for s in result.symbols if s.kind == "file" and s.meta and s.meta.get("component_framework") == "vue"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
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

        resolved = [e for e in result.edges if e.edge_type == "imports"]
        assert len(resolved) == 0

    def test_edge_with_empty_import_path_skipped(self, tmp_path: Path) -> None:
        """WI-mihiz / audit-findings 0011: edge with empty meta['import_path']
        (unresolved component ref with dangling component dst) is skipped —
        the linker can only resolve real filesystem paths."""
        (tmp_path / "App.vue").write_text("<template><Ghost/></template>")

        # Producer-shape edge for an unresolved local component reference:
        # src is a file_id, dst is a 5-part dangling component id, and
        # meta['import_path'] is empty.
        edge = Edge.create(
            src="vue:App.vue:1-1:file:file",
            dst="vue:component:Ghost:0-0:Ghost:component",
            edge_type="imports",
            line=1,
            origin="test",
            origin_run_id="test-run",
            evidence_type="import",
            confidence=0.6,
            meta={
                "import_path": "",
                "source_path": "App.vue",
                "component_name": "Ghost",
            },
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            edges=[edge],
        )
        result = link_vue_components(ctx)

        resolved = [e for e in result.edges if e.edge_type == "imports"]
        assert len(resolved) == 0

    def test_activation_requires_vue(self) -> None:
        """Linker activation requires vue framework or language."""
        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("vue-component-linker")
        assert linker is not None
        # Should run when vue is detected
        assert linker.activation.should_run({"vue"}, {"vue"})
        assert linker.activation.should_run(set(), {"vue"})
        # Should not run without vue
        assert not linker.activation.should_run(set(), {"python"})


class TestInvBahovCanonicalFileIdReuse:
    """INV-bahov: vue_component linker must emit canonical file Symbol ids
    and dedup against pre-existing canonical Symbols emitted by upstream
    producers (analyzers and the orchestrator's
    ``synthesize_file_symbols_for_dangling_edges``).

    Sibling family of INV-ronuf (websocket, WI-hifol PR #3819) and INV-movor
    (js_module, PR #3823). Each test mirrors the INV-movor pin pattern in
    ``test_js_module_linker.py::TestInvMovorCanonicalFileIdReuse``.
    """

    def test_canonical_file_id_shape(self, tmp_path: Path) -> None:
        """Emitted file Symbol id matches ``make_file_id`` shape
        (``vue:{path}:1-1:file:file``), not the legacy
        ``vue:{path}:component_file:1:{name}``."""
        from hypergumbo_core.analyze.base import make_file_id

        (tmp_path / "App.vue").write_text("<template><Header/></template>")
        (tmp_path / "Header.vue").write_text("<template><h1>Hi</h1></template>")

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

        file_symbols = [s for s in result.symbols if s.kind == "file"]
        assert len(file_symbols) >= 1
        for sym in file_symbols:
            expected = make_file_id("vue", sym.path)
            assert sym.id == expected, (
                f"vue_component file Symbol id {sym.id!r} does not match "
                f"canonical make_file_id shape {expected!r}"
            )

    def test_skips_when_canonical_file_symbol_already_present(
        self, tmp_path: Path
    ) -> None:
        """When a canonical kind=file Symbol already exists for a path
        (emitted by an upstream producer), the linker must NOT mint a
        parallel shadow Symbol — it reuses the existing one."""
        from hypergumbo_core.analyze.base import make_file_id, make_file_stable_id

        (tmp_path / "App.vue").write_text("<template><Header/></template>")
        (tmp_path / "Header.vue").write_text("<template><h1>Hi</h1></template>")

        # Pre-existing canonical Symbol (as emitted by the orchestrator's
        # synthesize_file_symbols_for_dangling_edges or by an analyzer).
        existing_header_file = Symbol(
            id=make_file_id("vue", "Header.vue"),
            stable_id=make_file_stable_id("vue", "Header.vue"),
            name="Header.vue",
            kind="file",
            language="vue",
            path="Header.vue",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="upstream-producer",
            origin_run_id="upstream-run",
            meta={},
        )
        app_ref = _sym(
            "vue:App.vue:component_ref:1:Header", "Header", "component_ref",
            path="App.vue", meta={"import_path": "./Header.vue"},
        )
        raw_edge = _edge(app_ref.id, "./Header.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[existing_header_file, app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        # The linker must not emit a duplicate Symbol for Header.vue.
        emitted_for_header = [
            s for s in result.symbols
            if s.kind == "file" and s.path == "Header.vue"
        ]
        assert len(emitted_for_header) == 0, (
            f"vue_component emitted {len(emitted_for_header)} duplicate "
            "file Symbol(s) for Header.vue despite an existing canonical "
            "Symbol in ctx.symbols"
        )

        # The resolved import edge must point at the existing canonical id.
        resolved = [e for e in result.edges if e.edge_type == "imports"]
        assert len(resolved) == 1
        assert resolved[0].dst == existing_header_file.id

    def test_stable_id_uses_canonical_helper(self, tmp_path: Path) -> None:
        """Emitted Symbol.stable_id matches ``make_file_stable_id`` output,
        not the legacy literal that reused ``sym_id`` as stable_id."""
        from hypergumbo_core.analyze.base import make_file_stable_id

        (tmp_path / "App.vue").write_text("<template><Card/></template>")
        (tmp_path / "Card.vue").write_text("<template><div/></template>")

        app_ref = _sym(
            "vue:App.vue:component_ref:1:Card", "Card", "component_ref",
            path="App.vue", meta={"import_path": "./Card.vue"},
        )
        raw_edge = _edge(app_ref.id, "./Card.vue")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[app_ref],
            edges=[raw_edge],
        )
        result = link_vue_components(ctx)

        card = next(
            (s for s in result.symbols if s.kind == "file" and s.path == "Card.vue"),
            None,
        )
        assert card is not None
        assert card.stable_id == make_file_stable_id("vue", "Card.vue")
