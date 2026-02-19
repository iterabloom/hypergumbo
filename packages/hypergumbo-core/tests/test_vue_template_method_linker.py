"""Tests for Vue template-method linker.

The Vue template-method linker connects Vue template event handler directives
(e.g., @click="handleDelete") to their corresponding method/function symbols
defined in the same .vue file's <script> section.
"""

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.vue_template_method import (
    _count_template_directives,
    _normalize_path,
    link_vue_template_method,
    link_vue_template_methods,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestVueTemplateMethodLinker:
    """Tests for template-to-method edge creation."""

    def test_simple_click_handler(self) -> None:
        """@click="handleDelete" creates edge to handleDelete method in same file."""
        directive = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleDelete",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/App.vue:25-30:handleDelete:method",
            name="handleDelete",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == directive.id
        assert edge.dst == method.id
        assert edge.edge_type == "template_calls"

    def test_no_match_different_file(self) -> None:
        """Directive and method in different files don't link."""
        directive = Symbol(
            id="vue:src/A.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/A.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleClick",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/B.vue:25-30:handleClick:method",
            name="handleClick",
            kind="method",
            language="javascript",
            path="/repo/src/B.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_no_match_wrong_name(self) -> None:
        """Directive handler name doesn't match any method in same file."""
        directive = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "nonexistentMethod",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/App.vue:25-30:handleDelete:method",
            name="handleDelete",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_directive_without_handler_expression(self) -> None:
        """Directive without handler_expression metadata is skipped."""
        directive = Symbol(
            id="vue:src/App.vue:directive:5:img:v-bind:src",
            name="v-bind:src",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=30),
            meta={
                "element": "img",
                "directive_type": "v-bind",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/App.vue:25-30:imageUrl:method",
            name="imageUrl",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_matches_function_not_just_method(self) -> None:
        """Handler expression matches function symbols too."""
        directive = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleClick",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        func = Symbol(
            id="javascript:/repo/src/App.vue:25-30:handleClick:function",
            name="handleClick",
            kind="function",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, func],
            edges=[],
        )

        assert len(result.edges) == 1
        assert result.edges[0].dst == func.id

    def test_multiple_directives_same_file(self) -> None:
        """Multiple event handlers in same file create multiple edges."""
        directive1 = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleSave",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        directive2 = Symbol(
            id="vue:src/App.vue:directive:8:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=8, end_line=8, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleCancel",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method1 = Symbol(
            id="javascript:/repo/src/App.vue:25-30:handleSave:method",
            name="handleSave",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        method2 = Symbol(
            id="javascript:/repo/src/App.vue:35-40:handleCancel:method",
            name="handleCancel",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=35, end_line=40, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive1, directive2, method1, method2],
            edges=[],
        )

        assert len(result.edges) == 2

    def test_path_normalization_absolute_vs_relative(self) -> None:
        """Linker normalizes paths: Vue uses relative, JS uses absolute."""
        directive = Symbol(
            id="vue:src/components/Modal.vue:directive:3:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/components/Modal.vue",
            span=Span(start_line=3, end_line=3, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "close",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/components/Modal.vue:50-55:close:method",
            name="close",
            kind="method",
            language="javascript",
            path="/repo/src/components/Modal.vue",
            span=Span(start_line=50, end_line=55, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        assert len(result.edges) == 1

    def test_non_directive_symbols_ignored(self) -> None:
        """Non-directive symbols are ignored."""
        component_ref = Symbol(
            id="vue:src/App.vue:component_ref:5:Modal",
            name="Modal",
            kind="component_ref",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=20),
            meta={"import_path": "./Modal.vue"},
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/App.vue:25-30:Modal:method",
            name="Modal",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_vue_template_methods(
            repo_root=Path("/repo"),
            symbols=[component_ref, method],
            edges=[],
        )

        assert len(result.edges) == 0

    def test_normalize_path_absolute_not_under_repo(self) -> None:
        """Absolute path not under repo_root is returned as-is."""
        result = _normalize_path("/other/project/file.vue", Path("/repo"))
        assert result == "/other/project/file.vue"


class TestLinkerRegistryIntegration:
    """Tests for registry integration."""

    def test_count_template_directives(self) -> None:
        """_count_template_directives counts directives with handler_expression."""
        d_with = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={"directive_type": "v-on", "handler_expression": "handleClick"},
            origin="vue-v1",
            origin_run_id="test-run",
        )

        d_without = Symbol(
            id="vue:src/App.vue:directive:8:div:v-if",
            name="v-if",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=8, end_line=8, start_col=2, end_col=30),
            meta={"directive_type": "v-if"},
            origin="vue-v1",
            origin_run_id="test-run",
        )

        non_directive = Symbol(
            id="javascript:/repo/src/App.vue:20-25:handleClick:method",
            name="handleClick",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=20, end_line=25, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[d_with, d_without, non_directive],
            edges=[],
        )
        assert _count_template_directives(ctx) == 1

    def test_linker_entry_point(self) -> None:
        """link_vue_template_method entry point works via LinkerContext."""
        directive = Symbol(
            id="vue:src/App.vue:directive:5:button:v-on:click",
            name="v-on:click",
            kind="directive",
            language="vue",
            path="src/App.vue",
            span=Span(start_line=5, end_line=5, start_col=2, end_col=40),
            meta={
                "element": "button",
                "directive_type": "v-on",
                "handler_expression": "handleClick",
            },
            origin="vue-v1",
            origin_run_id="test-run",
        )

        method = Symbol(
            id="javascript:/repo/src/App.vue:25-30:handleClick:method",
            name="handleClick",
            kind="method",
            language="javascript",
            path="/repo/src/App.vue",
            span=Span(start_line=25, end_line=30, start_col=4, end_col=5),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        ctx = LinkerContext(
            repo_root=Path("/repo"),
            symbols=[directive, method],
            edges=[],
        )

        result = link_vue_template_method(ctx)
        assert len(result.edges) == 1
        assert result.run is not None
