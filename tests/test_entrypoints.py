"""Tests for entrypoint detection heuristics."""
import pytest

from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.entrypoints import (
    detect_entrypoints,
    EntrypointKind,
    _is_test_file,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    language: str = "python",
    decorators: list[str] | None = None,
    meta: dict | None = None,
) -> Symbol:
    """Helper to create test symbols."""
    span = Span(start_line=start_line, end_line=end_line, start_col=0, end_col=10)
    sym_id = f"{language}:{path}:{start_line}-{end_line}:{name}:{kind}"
    # Store decorators in stable_id field for testing (hacky but works for tests)
    stable_id = ",".join(decorators) if decorators else None
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=span,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
        stable_id=stable_id,
        meta=meta,
    )


class TestFastAPIEntrypoints:
    """Tests for FastAPI route detection."""

    def test_detect_app_get_decorator(self) -> None:
        """Detect @app.get decorated functions."""
        sym = make_symbol("get_user", decorators=["get"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].symbol_id == sym.id
        assert entrypoints[0].kind == EntrypointKind.HTTP_ROUTE

    def test_detect_app_post_decorator(self) -> None:
        """Detect @app.post decorated functions."""
        sym = make_symbol("create_user", decorators=["post"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.HTTP_ROUTE

    def test_detect_router_decorator(self) -> None:
        """Detect @router.get/post decorated functions."""
        sym = make_symbol("list_items", decorators=["router"])
        nodes = [sym]

        # router decorator alone doesn't make it a route
        entrypoints = detect_entrypoints(nodes, [])
        # But combined patterns should work
        # For now, we detect common route decorators

    def test_detect_route_decorator(self) -> None:
        """Detect @app.route decorated functions."""
        sym = make_symbol("handle_request", decorators=["route"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.HTTP_ROUTE


class TestFlaskEntrypoints:
    """Tests for Flask route detection."""

    def test_detect_flask_route(self) -> None:
        """Detect Flask @app.route decorated functions."""
        sym = make_symbol("index", decorators=["route"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.HTTP_ROUTE


class TestCLIEntrypoints:
    """Tests for CLI entrypoint detection."""

    def test_detect_main_guard(self) -> None:
        """Detect if __name__ == '__main__' pattern."""
        # The main function in a file with main guard
        sym = make_symbol("main", path="src/cli.py")
        nodes = [sym]

        # We need a way to indicate this is a main-guarded function
        # For now, detect by name pattern
        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_detect_cli_by_name(self) -> None:
        """Detect CLI entry by function name patterns."""
        sym = make_symbol("cli", path="src/app.py")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_detect_click_command(self) -> None:
        """Detect Click CLI commands."""
        sym = make_symbol("run_server", decorators=["command"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.CLI_COMMAND

    def test_c_main_detected_as_cli(self) -> None:
        """C main function is detected as CLI_MAIN."""
        sym = make_symbol("main", path="src/main.c", language="c")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_cpp_main_detected_as_cli(self) -> None:
        """C++ main function is detected as CLI_MAIN."""
        sym = make_symbol("main", path="src/main.cpp", language="cpp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_glsl_main_not_detected_as_cli(self) -> None:
        """GLSL shader main function is NOT detected as CLI_MAIN."""
        sym = make_symbol("main", path="shaders/vertex.glsl", language="glsl")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # GLSL main is a shader entry point, not a CLI entry point
        assert not any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_hlsl_main_not_detected_as_cli(self) -> None:
        """HLSL shader main function is NOT detected as CLI_MAIN."""
        sym = make_symbol("main", path="shaders/pixel.hlsl", language="hlsl")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert not any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)

    def test_wgsl_main_not_detected_as_cli(self) -> None:
        """WGSL shader main function is NOT detected as CLI_MAIN."""
        sym = make_symbol("main", path="shaders/compute.wgsl", language="wgsl")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert not any(e.kind == EntrypointKind.CLI_MAIN for e in entrypoints)


class TestElectronEntrypoints:
    """Tests for Electron app detection."""

    def test_detect_electron_js(self) -> None:
        """Detect Electron main process file."""
        sym = make_symbol("createWindow", path="src/electron.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.ELECTRON_MAIN for e in entrypoints)

    def test_detect_preload_js(self) -> None:
        """Detect Electron preload script."""
        sym = make_symbol("contextBridge", path="src/preload.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert any(e.kind == EntrypointKind.ELECTRON_PRELOAD for e in entrypoints)

    def test_generic_renderer_not_matched(self) -> None:
        """Generic renderer.js is NOT matched to avoid false positives."""
        sym = make_symbol("render", path="src/renderer.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should not detect as Electron - too generic, causes false positives
        assert not any(e.label.startswith("Electron") for e in entrypoints)

    def test_one_entry_per_file(self) -> None:
        """Multiple symbols in same Electron file produce only one entry."""
        sym1 = make_symbol("createWindow", path="src/electron.js", language="javascript")
        sym2 = make_symbol("setupMenu", path="src/electron.js", language="javascript")
        sym3 = make_symbol("handleIPC", path="src/electron.js", language="javascript")
        nodes = [sym1, sym2, sym3]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only have one Electron main entry, not three
        electron_entries = [e for e in entrypoints if e.kind == EntrypointKind.ELECTRON_MAIN]
        assert len(electron_entries) == 1


class TestEntrypointResult:
    """Tests for Entrypoint result structure."""

    def test_entrypoint_has_required_fields(self) -> None:
        """Entrypoint contains symbol_id, kind, and confidence."""
        sym = make_symbol("get_user", decorators=["get"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.symbol_id == sym.id
        assert ep.kind == EntrypointKind.HTTP_ROUTE
        assert 0.0 <= ep.confidence <= 1.0
        assert ep.label is not None

    def test_entrypoint_to_dict(self) -> None:
        """Entrypoint serializes to dict."""
        sym = make_symbol("get_user", decorators=["get"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])
        d = entrypoints[0].to_dict()

        assert "symbol_id" in d
        assert "kind" in d
        assert "confidence" in d
        assert "label" in d


class TestMultipleEntrypoints:
    """Tests for detecting multiple entrypoints."""

    def test_detect_multiple_routes(self) -> None:
        """Detect multiple HTTP routes in same file."""
        sym1 = make_symbol("get_user", decorators=["get"], start_line=10)
        sym2 = make_symbol("create_user", decorators=["post"], start_line=20)
        sym3 = make_symbol("helper", start_line=30)  # Not an entrypoint
        nodes = [sym1, sym2, sym3]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 2

    def test_no_entrypoints(self) -> None:
        """Return empty list when no entrypoints found."""
        sym = make_symbol("helper_function")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # May still detect by name patterns, but helper_function is not one
        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 0


class TestEntrypointConfidence:
    """Tests for entrypoint confidence scoring."""

    def test_decorator_high_confidence(self) -> None:
        """Decorator-based detection has high confidence."""
        sym = make_symbol("get_user", decorators=["get"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert entrypoints[0].confidence >= 0.9

    def test_name_pattern_lower_confidence(self) -> None:
        """Name-based detection has lower confidence."""
        sym = make_symbol("main")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_MAIN]
        if cli_eps:
            assert cli_eps[0].confidence < 0.9


class TestAsyncHandlers:
    """Tests for async handler detection."""

    def test_detect_async_route(self) -> None:
        """Detect async HTTP handlers."""
        # Async functions are still functions, detected by decorator
        sym = make_symbol("async_get_user", decorators=["get"])
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.HTTP_ROUTE


class TestDjangoEntrypoints:
    """Tests for Django URL route detection.

    Django uses path() or url() calls in urls.py files to map URLs to views.
    Detection strategy: If a urls.py file imports a function, that function
    is likely a Django view entrypoint.
    """

    def test_detect_django_view_by_urls_import(self) -> None:
        """Detect Django views imported by urls.py."""

        # views.py: index function
        view_func = make_symbol("index", path="myapp/views.py")

        # urls.py: imports index from views
        urls_file = make_symbol("file", kind="file", path="myapp/urls.py")

        # Import edge from urls.py to the view function
        import_edge = Edge.create(
            src=urls_file.id,
            dst=view_func.id,
            edge_type="imports",
            line=1,
        )

        nodes = [view_func, urls_file]
        edges = [import_edge]

        entrypoints = detect_entrypoints(nodes, edges)

        # The view function should be detected as Django view entrypoint
        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 1
        assert django_eps[0].symbol_id == view_func.id

    def test_detect_multiple_django_views(self) -> None:
        """Detect multiple views imported by urls.py."""

        # Multiple view functions
        view1 = make_symbol("index", path="myapp/views.py", start_line=1)
        view2 = make_symbol("detail", path="myapp/views.py", start_line=10)
        view3 = make_symbol("helper", path="myapp/utils.py")  # Not in urls.py

        urls_file = make_symbol("file", kind="file", path="myapp/urls.py")

        # Import edges from urls.py
        edge1 = Edge.create(src=urls_file.id, dst=view1.id, edge_type="imports", line=1)
        edge2 = Edge.create(src=urls_file.id, dst=view2.id, edge_type="imports", line=2)

        nodes = [view1, view2, view3, urls_file]
        edges = [edge1, edge2]

        entrypoints = detect_entrypoints(nodes, edges)

        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 2
        assert {e.symbol_id for e in django_eps} == {view1.id, view2.id}

    def test_django_view_confidence(self) -> None:
        """Django view detection has appropriate confidence score."""

        view_func = make_symbol("index", path="myapp/views.py")
        urls_file = make_symbol("file", kind="file", path="myapp/urls.py")
        import_edge = Edge.create(src=urls_file.id, dst=view_func.id, edge_type="imports", line=1)

        nodes = [view_func, urls_file]
        edges = [import_edge]

        entrypoints = detect_entrypoints(nodes, edges)

        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 1
        # Should have high confidence - urls.py imports are intentional
        assert django_eps[0].confidence >= 0.85

    def test_django_urls_nested_path(self) -> None:
        """Detect views from nested urls.py files (app/urls.py)."""

        view_func = make_symbol("api_list", path="api/views.py")
        urls_file = make_symbol("file", kind="file", path="api/urls.py")
        import_edge = Edge.create(src=urls_file.id, dst=view_func.id, edge_type="imports", line=1)

        nodes = [view_func, urls_file]
        edges = [import_edge]

        entrypoints = detect_entrypoints(nodes, edges)

        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 1

    def test_django_ignore_non_urls_imports(self) -> None:
        """Non-urls.py imports don't trigger Django view detection."""

        # views.py imports from utils.py - this is NOT a Django route
        view_func = make_symbol("helper", path="myapp/utils.py")
        views_file = make_symbol("file", kind="file", path="myapp/views.py")
        import_edge = Edge.create(src=views_file.id, dst=view_func.id, edge_type="imports", line=1)

        nodes = [view_func, views_file]
        edges = [import_edge]

        entrypoints = detect_entrypoints(nodes, edges)

        # Should not detect as Django view - views.py importing utils.py is not a route
        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 0

    def test_django_view_label(self) -> None:
        """Django view entrypoints have descriptive labels."""

        view_func = make_symbol("article_detail", path="blog/views.py")
        urls_file = make_symbol("file", kind="file", path="blog/urls.py")
        import_edge = Edge.create(src=urls_file.id, dst=view_func.id, edge_type="imports", line=1)

        nodes = [view_func, urls_file]
        edges = [import_edge]

        entrypoints = detect_entrypoints(nodes, edges)

        django_eps = [e for e in entrypoints if e.kind == EntrypointKind.DJANGO_VIEW]
        assert len(django_eps) == 1
        assert "Django" in django_eps[0].label or "view" in django_eps[0].label.lower()


class TestExpressEntrypoints:
    """Tests for Express.js route detection.

    Express uses app.get/post/etc. or router.get/post/etc. to define routes.
    Detection strategy: Functions in files that match route patterns
    (routes.js, routes/*.js, router.js) or files that import express.
    """

    def test_detect_express_route_in_routes_file(self) -> None:
        """Detect functions in routes.js as Express routes."""
        sym = make_symbol("getUsers", path="src/routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1
        assert express_eps[0].symbol_id == sym.id

    def test_detect_express_route_in_router_file(self) -> None:
        """Detect functions in router.js as Express routes."""
        sym = make_symbol("createUser", path="api/router.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1

    def test_detect_express_route_in_routes_directory(self) -> None:
        """Detect functions in routes/*.js as Express routes."""
        sym = make_symbol("deleteItem", path="src/routes/items.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1

    def test_detect_express_route_typescript(self) -> None:
        """Detect Express routes in TypeScript files."""
        sym = make_symbol("updateUser", path="src/routes/users.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1

    def test_detect_multiple_express_routes(self) -> None:
        """Detect multiple route handlers in same file."""
        sym1 = make_symbol("getUser", path="src/routes.js", language="javascript", start_line=10)
        sym2 = make_symbol("createUser", path="src/routes.js", language="javascript", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 2

    def test_express_route_confidence(self) -> None:
        """Express route detection has medium-high confidence."""
        sym = make_symbol("handler", path="routes/api.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1
        # File pattern based - medium-high confidence
        assert express_eps[0].confidence >= 0.80

    def test_express_route_label(self) -> None:
        """Express route entrypoints have descriptive labels."""
        sym = make_symbol("getProducts", path="src/routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1
        assert "Express" in express_eps[0].label or "route" in express_eps[0].label.lower()

    def test_express_non_route_file_not_detected(self) -> None:
        """Functions in non-route files are not detected as Express routes."""
        sym = make_symbol("helper", path="src/utils.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 0

    def test_express_only_js_ts_files(self) -> None:
        """Only JavaScript/TypeScript files are detected as Express routes."""
        # Python file named routes.py should NOT be detected as Express
        sym = make_symbol("get_users", path="src/routes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 0

    def test_express_file_symbol_not_detected(self) -> None:
        """File symbols in route files are not detected as routes."""
        sym = make_symbol("file", kind="file", path="src/routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 0

    def test_express_tsx_files_not_detected(self) -> None:
        """TSX files in routes directory are React components, not Express routes.

        React file-based routing (TanStack Router, Next.js app router) uses
        routes/*.tsx for components, which should not be detected as Express routes.
        """
        sym = make_symbol("Dashboard", path="frontend/src/routes/index.tsx", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 0

    def test_express_jsx_files_not_detected(self) -> None:
        """JSX files in routes directory are React components, not Express routes."""
        sym = make_symbol("App", path="frontend/src/routes/App.jsx", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 0


class TestNestJSEntrypoints:
    """Tests for NestJS controller detection.

    NestJS uses @Controller decorator on classes and @Get/@Post/etc.
    decorators on methods. Detection strategy: Files matching the
    *.controller.ts naming convention.
    """

    def test_detect_nestjs_controller_file(self) -> None:
        """Detect methods in *.controller.ts as NestJS endpoints."""
        sym = make_symbol("findAll", path="src/users.controller.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 1
        assert nestjs_eps[0].symbol_id == sym.id

    def test_detect_nestjs_class_controller(self) -> None:
        """Detect classes in *.controller.ts as NestJS controllers."""
        sym = make_symbol("UsersController", kind="class", path="src/users.controller.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 1

    def test_detect_nestjs_in_controllers_directory(self) -> None:
        """Detect files in controllers/ directory as NestJS."""
        sym = make_symbol("getUsers", path="src/controllers/users.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 1

    def test_detect_multiple_nestjs_methods(self) -> None:
        """Detect multiple methods in same controller file."""
        sym1 = make_symbol("findAll", path="src/users.controller.ts", language="typescript", start_line=10)
        sym2 = make_symbol("create", path="src/users.controller.ts", language="typescript", start_line=20)
        sym3 = make_symbol("delete", path="src/users.controller.ts", language="typescript", start_line=30)
        nodes = [sym1, sym2, sym3]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 3

    def test_nestjs_controller_confidence(self) -> None:
        """NestJS controller detection has high confidence."""
        sym = make_symbol("findOne", path="src/items.controller.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 1
        # File pattern based - high confidence for .controller.ts
        assert nestjs_eps[0].confidence >= 0.85

    def test_nestjs_controller_label(self) -> None:
        """NestJS controller entrypoints have descriptive labels."""
        sym = make_symbol("update", path="src/products.controller.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 1
        assert "NestJS" in nestjs_eps[0].label or "controller" in nestjs_eps[0].label.lower()

    def test_nestjs_only_typescript(self) -> None:
        """Only TypeScript files are detected as NestJS controllers."""
        # JavaScript file should NOT be detected
        sym = make_symbol("findAll", path="src/users.controller.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        # NestJS is TypeScript-only framework
        assert len(nestjs_eps) == 0

    def test_nestjs_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="src/users.controller.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 0

    def test_nestjs_non_controller_file_not_detected(self) -> None:
        """Non-controller TypeScript files are not detected as NestJS."""
        sym = make_symbol("helper", path="src/users.service.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        nestjs_eps = [e for e in entrypoints if e.kind == EntrypointKind.NESTJS_CONTROLLER]
        assert len(nestjs_eps) == 0


class TestSpringBootEntrypoints:
    """Tests for Spring Boot controller detection.

    Spring Boot uses @Controller and @RestController annotations on classes.
    Detection strategy: Files matching *Controller.java or *Resource.java,
    or files in a controller/ or controllers/ directory.
    """

    def test_detect_spring_controller_file(self) -> None:
        """Detect methods in *Controller.java as Spring endpoints."""
        sym = make_symbol("getUsers", path="src/main/java/com/app/UserController.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1
        assert spring_eps[0].symbol_id == sym.id

    def test_detect_spring_rest_resource_file(self) -> None:
        """Detect methods in *Resource.java as Spring endpoints."""
        sym = make_symbol("createUser", path="src/main/java/com/app/UserResource.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1

    def test_detect_spring_in_controller_directory(self) -> None:
        """Detect files in controller/ directory as Spring."""
        sym = make_symbol("listProducts", path="src/main/java/com/app/controller/Products.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1

    def test_detect_spring_kotlin(self) -> None:
        """Detect Spring endpoints in Kotlin files."""
        sym = make_symbol("getItems", path="src/main/kotlin/com/app/ItemController.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1

    def test_detect_multiple_spring_methods(self) -> None:
        """Detect multiple methods in same controller file."""
        sym1 = make_symbol("getAll", path="src/main/java/UserController.java", language="java", start_line=10)
        sym2 = make_symbol("create", path="src/main/java/UserController.java", language="java", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 2

    def test_spring_controller_confidence(self) -> None:
        """Spring controller detection has high confidence."""
        sym = make_symbol("handleRequest", path="src/main/java/ApiController.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1
        assert spring_eps[0].confidence >= 0.85

    def test_spring_controller_label(self) -> None:
        """Spring controller entrypoints have descriptive labels."""
        sym = make_symbol("update", path="src/main/java/ProductController.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 1
        assert "Spring" in spring_eps[0].label or "controller" in spring_eps[0].label.lower()

    def test_spring_only_java_kotlin(self) -> None:
        """Only Java/Kotlin files are detected as Spring controllers."""
        # TypeScript file should NOT be detected
        sym = make_symbol("getUsers", path="src/UserController.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 0

    def test_spring_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="src/main/java/UserController.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 0

    def test_spring_non_controller_file_not_detected(self) -> None:
        """Non-controller Java files are not detected as Spring."""
        sym = make_symbol("helper", path="src/main/java/UserService.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        spring_eps = [e for e in entrypoints if e.kind == EntrypointKind.SPRING_CONTROLLER]
        assert len(spring_eps) == 0


class TestRailsEntrypoints:
    """Tests for Rails controller detection.

    Rails uses *_controller.rb naming convention in app/controllers/.
    Actions are public methods inside controller classes.
    """

    def test_detect_rails_controller_file(self) -> None:
        """Detect methods in *_controller.rb as Rails actions."""
        sym = make_symbol("index", path="app/controllers/users_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 1
        assert rails_eps[0].symbol_id == sym.id

    def test_detect_rails_nested_controller(self) -> None:
        """Detect controllers in nested namespaces."""
        sym = make_symbol("show", path="app/controllers/api/v1/users_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 1

    def test_detect_rails_application_controller(self) -> None:
        """Detect ApplicationController as base controller."""
        sym = make_symbol("ApplicationController", kind="class", path="app/controllers/application_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 1

    def test_detect_multiple_rails_actions(self) -> None:
        """Detect multiple actions in same controller."""
        sym1 = make_symbol("index", path="app/controllers/posts_controller.rb", language="ruby", start_line=10)
        sym2 = make_symbol("show", path="app/controllers/posts_controller.rb", language="ruby", start_line=20)
        sym3 = make_symbol("create", path="app/controllers/posts_controller.rb", language="ruby", start_line=30)
        nodes = [sym1, sym2, sym3]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 3

    def test_rails_controller_confidence(self) -> None:
        """Rails controller detection has high confidence."""
        sym = make_symbol("update", path="app/controllers/items_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 1
        assert rails_eps[0].confidence >= 0.85

    def test_rails_controller_label(self) -> None:
        """Rails controller entrypoints have descriptive labels."""
        sym = make_symbol("destroy", path="app/controllers/comments_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 1
        assert "Rails" in rails_eps[0].label or "controller" in rails_eps[0].label.lower()

    def test_rails_only_ruby_files(self) -> None:
        """Only Ruby files are detected as Rails controllers."""
        # Python file should NOT be detected
        sym = make_symbol("index", path="app/controllers/users_controller.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 0

    def test_rails_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="app/controllers/users_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 0

    def test_rails_non_controller_file_not_detected(self) -> None:
        """Non-controller Ruby files are not detected as Rails."""
        sym = make_symbol("helper", path="app/models/user.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 0

    def test_rails_requires_controllers_directory(self) -> None:
        """Controller files must be in app/controllers/ directory."""
        # A file named *_controller.rb elsewhere should NOT be detected
        sym = make_symbol("index", path="lib/users_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rails_eps = [e for e in entrypoints if e.kind == EntrypointKind.RAILS_CONTROLLER]
        assert len(rails_eps) == 0


class TestPhoenixEntrypoints:
    """Tests for Phoenix controller detection.

    Phoenix uses *_controller.ex naming convention in lib/*_web/controllers/.
    Also detects LiveView files (*_live.ex in lib/*_web/live/).
    """

    def test_detect_phoenix_controller_file(self) -> None:
        """Detect functions in *_controller.ex as Phoenix endpoints."""
        sym = make_symbol("index", path="lib/myapp_web/controllers/user_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 1
        assert phoenix_eps[0].symbol_id == sym.id

    def test_detect_phoenix_nested_controller(self) -> None:
        """Detect controllers in nested namespaces."""
        sym = make_symbol("show", path="lib/myapp_web/controllers/api/v1/user_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 1

    def test_detect_phoenix_liveview(self) -> None:
        """Detect LiveView files as Phoenix endpoints."""
        sym = make_symbol("mount", path="lib/myapp_web/live/user_live.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 1

    def test_detect_multiple_phoenix_actions(self) -> None:
        """Detect multiple actions in same controller."""
        sym1 = make_symbol("index", path="lib/app_web/controllers/page_controller.ex", language="elixir", start_line=10)
        sym2 = make_symbol("show", path="lib/app_web/controllers/page_controller.ex", language="elixir", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 2

    def test_phoenix_controller_confidence(self) -> None:
        """Phoenix controller detection has high confidence."""
        sym = make_symbol("create", path="lib/myapp_web/controllers/post_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 1
        assert phoenix_eps[0].confidence >= 0.85

    def test_phoenix_controller_label(self) -> None:
        """Phoenix controller entrypoints have descriptive labels."""
        sym = make_symbol("delete", path="lib/myapp_web/controllers/item_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 1
        assert "Phoenix" in phoenix_eps[0].label or "controller" in phoenix_eps[0].label.lower()

    def test_phoenix_only_elixir_files(self) -> None:
        """Only Elixir files are detected as Phoenix controllers."""
        # Ruby file should NOT be detected
        sym = make_symbol("index", path="lib/myapp_web/controllers/user_controller.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 0

    def test_phoenix_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="lib/myapp_web/controllers/user_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 0

    def test_phoenix_non_controller_file_not_detected(self) -> None:
        """Non-controller Elixir files are not detected as Phoenix."""
        sym = make_symbol("helper", path="lib/myapp/user.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 0

    def test_phoenix_requires_web_controllers_path(self) -> None:
        """Controller files must be in proper Phoenix path."""
        # A *_controller.ex outside _web/controllers should NOT be detected
        sym = make_symbol("index", path="lib/myapp/user_controller.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        phoenix_eps = [e for e in entrypoints if e.kind == EntrypointKind.PHOENIX_CONTROLLER]
        assert len(phoenix_eps) == 0


class TestGoFrameworkEntrypoints:
    """Tests for Go framework route detection.

    Go web frameworks (Gin, Echo, Fiber, Chi) typically use:
    - *_handler.go or *_controller.go naming
    - handlers/, controllers/, or routes/ directories
    """

    def test_detect_go_handler_file(self) -> None:
        """Detect functions in *_handler.go as Go endpoints."""
        sym = make_symbol("GetUser", path="internal/handlers/user_handler.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1
        assert go_eps[0].symbol_id == sym.id

    def test_detect_go_controller_file(self) -> None:
        """Detect functions in *_controller.go as Go endpoints."""
        sym = make_symbol("CreatePost", path="internal/api/post_controller.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1

    def test_detect_go_handlers_directory(self) -> None:
        """Detect files in handlers/ directory as Go endpoints."""
        sym = make_symbol("ListItems", path="pkg/handlers/items.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1

    def test_detect_go_controllers_directory(self) -> None:
        """Detect files in controllers/ directory as Go endpoints."""
        sym = make_symbol("DeleteUser", path="app/controllers/user.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1

    def test_detect_multiple_go_handlers(self) -> None:
        """Detect multiple handlers in same file."""
        sym1 = make_symbol("GetUser", path="handlers/user_handler.go", language="go", start_line=10)
        sym2 = make_symbol("CreateUser", path="handlers/user_handler.go", language="go", start_line=30)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 2

    def test_go_handler_confidence(self) -> None:
        """Go handler detection has high confidence."""
        sym = make_symbol("UpdateItem", path="internal/handlers/item_handler.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1
        assert go_eps[0].confidence >= 0.85

    def test_go_handler_label(self) -> None:
        """Go handler entrypoints have descriptive labels."""
        sym = make_symbol("DeleteOrder", path="handlers/order_handler.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 1
        assert "Go" in go_eps[0].label or "handler" in go_eps[0].label.lower()

    def test_go_only_go_files(self) -> None:
        """Only Go files are detected as Go handlers."""
        # Python file should NOT be detected
        sym = make_symbol("get_user", path="handlers/user_handler.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 0

    def test_go_file_symbol_not_detected(self) -> None:
        """File symbols in handler files are not detected."""
        sym = make_symbol("file", kind="file", path="handlers/user_handler.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 0

    def test_go_non_handler_file_not_detected(self) -> None:
        """Non-handler Go files are not detected."""
        sym = make_symbol("helper", path="internal/utils/helpers.go", language="go")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        go_eps = [e for e in entrypoints if e.kind == EntrypointKind.GO_HANDLER]
        assert len(go_eps) == 0


class TestLaravelEntrypoints:
    """Tests for Laravel controller detection.

    Laravel uses *Controller.php files in app/Http/Controllers/.
    Routes are defined in routes/*.php files.
    """

    def test_detect_laravel_controller_file(self) -> None:
        """Detect functions in *Controller.php as Laravel endpoints."""
        sym = make_symbol("index", path="app/Http/Controllers/UserController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 1
        assert laravel_eps[0].symbol_id == sym.id

    def test_detect_laravel_nested_controller(self) -> None:
        """Detect controllers in nested namespaces."""
        sym = make_symbol("show", path="app/Http/Controllers/Api/V1/UserController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 1

    def test_detect_laravel_class_controller(self) -> None:
        """Detect controller classes."""
        sym = make_symbol("UserController", kind="class", path="app/Http/Controllers/UserController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 1

    def test_detect_multiple_laravel_actions(self) -> None:
        """Detect multiple actions in same controller."""
        sym1 = make_symbol("index", path="app/Http/Controllers/PostController.php", language="php", start_line=10)
        sym2 = make_symbol("store", path="app/Http/Controllers/PostController.php", language="php", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 2

    def test_laravel_controller_confidence(self) -> None:
        """Laravel controller detection has high confidence."""
        sym = make_symbol("update", path="app/Http/Controllers/ItemController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 1
        assert laravel_eps[0].confidence >= 0.85

    def test_laravel_controller_label(self) -> None:
        """Laravel controller entrypoints have descriptive labels."""
        sym = make_symbol("destroy", path="app/Http/Controllers/OrderController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 1
        assert "Laravel" in laravel_eps[0].label or "controller" in laravel_eps[0].label.lower()

    def test_laravel_only_php_files(self) -> None:
        """Only PHP files are detected as Laravel controllers."""
        # Python file should NOT be detected
        sym = make_symbol("index", path="app/Http/Controllers/UserController.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 0

    def test_laravel_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="app/Http/Controllers/UserController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 0

    def test_laravel_non_controller_file_not_detected(self) -> None:
        """Non-controller PHP files are not detected as Laravel."""
        sym = make_symbol("helper", path="app/Models/User.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 0

    def test_laravel_requires_controllers_path(self) -> None:
        """Controller files must be in app/Http/Controllers/."""
        # A *Controller.php outside the proper path should NOT be detected
        sym = make_symbol("index", path="src/UserController.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        laravel_eps = [e for e in entrypoints if e.kind == EntrypointKind.LARAVEL_CONTROLLER]
        assert len(laravel_eps) == 0


class TestRustEntrypoints:
    """Tests for Rust web framework handler detection.

    Rust web frameworks (Actix-web, Axum, Rocket, Warp) typically use:
    - *_handler.rs or *_controller.rs files
    - handlers/ or controllers/ directories
    """

    def test_detect_rust_handler_file(self) -> None:
        """Detect functions in *_handler.rs as Rust handlers."""
        sym = make_symbol("get_user", path="src/user_handler.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1
        assert rust_eps[0].symbol_id == sym.id

    def test_detect_rust_controller_file(self) -> None:
        """Detect functions in *_controller.rs as Rust handlers."""
        sym = make_symbol("create_user", path="src/user_controller.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1

    def test_detect_rust_handlers_directory(self) -> None:
        """Detect functions in handlers/ directory."""
        sym = make_symbol("list_users", path="src/handlers/users.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1

    def test_detect_rust_controllers_directory(self) -> None:
        """Detect functions in controllers/ directory."""
        sym = make_symbol("delete_user", path="src/controllers/user.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1

    def test_detect_multiple_rust_handlers(self) -> None:
        """Detect multiple handlers in same file."""
        sym1 = make_symbol("create", path="src/api_handler.rs", language="rust", start_line=10)
        sym2 = make_symbol("update", path="src/api_handler.rs", language="rust", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 2

    def test_rust_handler_confidence(self) -> None:
        """Rust handler detection has appropriate confidence."""
        sym = make_symbol("handle_request", path="src/handlers/api.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1
        assert rust_eps[0].confidence >= 0.80

    def test_rust_handler_label(self) -> None:
        """Rust handler entrypoints have descriptive labels."""
        sym = make_symbol("get_items", path="src/item_handler.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 1
        assert "Rust" in rust_eps[0].label or "handler" in rust_eps[0].label.lower()

    def test_rust_only_rust_files(self) -> None:
        """Only Rust files are detected as Rust handlers."""
        # Python file should NOT be detected
        sym = make_symbol("get_user", path="src/user_handler.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 0

    def test_rust_file_symbol_not_detected(self) -> None:
        """File symbols in handler files are not detected."""
        sym = make_symbol("file", kind="file", path="src/user_handler.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 0

    def test_rust_non_handler_file_not_detected(self) -> None:
        """Non-handler Rust files are not detected."""
        sym = make_symbol("helper", path="src/utils.rs", language="rust")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        rust_eps = [e for e in entrypoints if e.kind == EntrypointKind.RUST_HANDLER]
        assert len(rust_eps) == 0


class TestAspNetCoreEntrypoints:
    """Tests for ASP.NET Core controller detection.

    ASP.NET Core uses *Controller.cs files, typically in a Controllers/ directory.
    Web API controllers follow the same convention.
    """

    def test_detect_aspnet_controller_file(self) -> None:
        """Detect methods in *Controller.cs as ASP.NET Core endpoints."""
        sym = make_symbol("GetUser", path="Controllers/UserController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1
        assert aspnet_eps[0].symbol_id == sym.id

    def test_detect_aspnet_nested_controller(self) -> None:
        """Detect controllers in nested namespaces."""
        sym = make_symbol("Create", path="src/Api/Controllers/V1/ItemController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1

    def test_detect_aspnet_controllers_directory(self) -> None:
        """Detect controllers in Controllers/ directory."""
        sym = make_symbol("Index", path="Controllers/HomeController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1

    def test_detect_aspnet_class_symbol(self) -> None:
        """Detect controller class symbols."""
        sym = make_symbol("UserController", kind="class", path="Controllers/UserController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1

    def test_detect_multiple_aspnet_actions(self) -> None:
        """Detect multiple actions in same controller."""
        sym1 = make_symbol("GetAll", path="Controllers/ProductController.cs", language="csharp", start_line=10)
        sym2 = make_symbol("GetById", path="Controllers/ProductController.cs", language="csharp", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 2

    def test_aspnet_controller_confidence(self) -> None:
        """ASP.NET Core controller detection has high confidence."""
        sym = make_symbol("Update", path="Controllers/OrderController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1
        assert aspnet_eps[0].confidence >= 0.85

    def test_aspnet_controller_label(self) -> None:
        """ASP.NET Core controller entrypoints have descriptive labels."""
        sym = make_symbol("Delete", path="Controllers/CustomerController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 1
        assert "ASP.NET" in aspnet_eps[0].label or "controller" in aspnet_eps[0].label.lower()

    def test_aspnet_only_csharp_files(self) -> None:
        """Only C# files are detected as ASP.NET controllers."""
        # Python file should NOT be detected
        sym = make_symbol("GetUser", path="Controllers/UserController.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 0

    def test_aspnet_file_symbol_not_detected(self) -> None:
        """File symbols in controller files are not detected."""
        sym = make_symbol("file", kind="file", path="Controllers/UserController.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 0

    def test_aspnet_non_controller_file_not_detected(self) -> None:
        """Non-controller C# files are not detected as ASP.NET."""
        sym = make_symbol("Helper", path="Services/UserService.cs", language="csharp")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aspnet_eps = [e for e in entrypoints if e.kind == EntrypointKind.ASPNET_CONTROLLER]
        assert len(aspnet_eps) == 0


class TestSinatraEntrypoints:
    """Tests for Sinatra (Ruby) route detection.

    Sinatra is a lightweight Ruby web framework that uses DSL-based routing.
    Common patterns include app.rb, application.rb, and files in routes/.
    """

    def test_detect_sinatra_app_file(self) -> None:
        """Detect methods in app.rb as Sinatra routes."""
        sym = make_symbol("get_index", path="app.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1
        assert sinatra_eps[0].symbol_id == sym.id

    def test_detect_sinatra_application_file(self) -> None:
        """Detect methods in application.rb as Sinatra routes."""
        sym = make_symbol("post_users", path="application.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1

    def test_detect_sinatra_server_file(self) -> None:
        """Detect methods in server.rb as Sinatra routes."""
        sym = make_symbol("handle_request", path="server.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1

    def test_detect_sinatra_routes_directory(self) -> None:
        """Detect methods in routes/ directory."""
        sym = make_symbol("show_user", path="routes/users.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1

    def test_detect_multiple_sinatra_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("index", path="app.rb", language="ruby", start_line=10)
        sym2 = make_symbol("create", path="app.rb", language="ruby", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 2

    def test_sinatra_route_confidence(self) -> None:
        """Sinatra route detection has appropriate confidence."""
        sym = make_symbol("update", path="app.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1
        assert sinatra_eps[0].confidence >= 0.80

    def test_sinatra_route_label(self) -> None:
        """Sinatra route entrypoints have descriptive labels."""
        sym = make_symbol("delete_item", path="app.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 1
        assert "Sinatra" in sinatra_eps[0].label or "route" in sinatra_eps[0].label.lower()

    def test_sinatra_only_ruby_files(self) -> None:
        """Only Ruby files are detected as Sinatra routes."""
        # Python file should NOT be detected
        sym = make_symbol("get_index", path="app.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 0

    def test_sinatra_file_symbol_not_detected(self) -> None:
        """File symbols in Sinatra files are not detected."""
        sym = make_symbol("file", kind="file", path="app.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 0

    def test_sinatra_non_route_file_not_detected(self) -> None:
        """Non-route Ruby files are not detected as Sinatra."""
        sym = make_symbol("helper", path="lib/utils.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 0

    def test_sinatra_test_file_not_detected(self) -> None:
        """Test files named app.rb are not detected as Sinatra routes."""
        # This was a real bug: test/integration/app.rb was misdetected in Sinatra repo
        syms = [
            make_symbol("TestApp", path="test/integration/app.rb", language="ruby"),
            make_symbol("describe", path="spec/app.rb", language="ruby"),
            make_symbol("helper", path="tests/server.rb", language="ruby"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        sinatra_eps = [e for e in entrypoints if e.kind == EntrypointKind.SINATRA_ROUTE]
        assert len(sinatra_eps) == 0


class TestKtorEntrypoints:
    """Tests for Ktor (Kotlin) route detection.

    Ktor is a Kotlin web framework. Routes are typically defined in
    files named *Routes.kt, *Routing.kt, or in routes/routing directories.
    """

    def test_detect_ktor_routes_file(self) -> None:
        """Detect functions in *Routes.kt as Ktor routes."""
        sym = make_symbol("getUsers", path="src/UserRoutes.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1
        assert ktor_eps[0].symbol_id == sym.id

    def test_detect_ktor_routing_file(self) -> None:
        """Detect functions in *Routing.kt as Ktor routes."""
        sym = make_symbol("configureRouting", path="src/ApiRouting.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1

    def test_detect_ktor_routes_directory(self) -> None:
        """Detect functions in routes/ directory."""
        sym = make_symbol("createUser", path="src/routes/Users.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1

    def test_detect_ktor_routing_directory(self) -> None:
        """Detect functions in routing/ directory."""
        sym = make_symbol("deleteItem", path="src/routing/Items.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1

    def test_detect_multiple_ktor_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("listProducts", path="src/ProductRoutes.kt", language="kotlin", start_line=10)
        sym2 = make_symbol("getProduct", path="src/ProductRoutes.kt", language="kotlin", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 2

    def test_ktor_route_confidence(self) -> None:
        """Ktor route detection has appropriate confidence."""
        sym = make_symbol("updateOrder", path="src/OrderRoutes.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1
        assert ktor_eps[0].confidence >= 0.80

    def test_ktor_route_label(self) -> None:
        """Ktor route entrypoints have descriptive labels."""
        sym = make_symbol("cancelOrder", path="src/OrderRoutes.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 1
        assert "Ktor" in ktor_eps[0].label or "route" in ktor_eps[0].label.lower()

    def test_ktor_only_kotlin_files(self) -> None:
        """Only Kotlin files are detected as Ktor routes."""
        # Python file should NOT be detected
        sym = make_symbol("getUsers", path="src/UserRoutes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 0

    def test_ktor_file_symbol_not_detected(self) -> None:
        """File symbols in Ktor files are not detected."""
        sym = make_symbol("file", kind="file", path="src/UserRoutes.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 0

    def test_ktor_non_route_file_not_detected(self) -> None:
        """Non-route Kotlin files are not detected as Ktor."""
        sym = make_symbol("helper", path="src/utils/Helper.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ktor_eps = [e for e in entrypoints if e.kind == EntrypointKind.KTOR_ROUTE]
        assert len(ktor_eps) == 0


class TestVaporEntrypoints:
    """Tests for Vapor (Swift) route detection.

    Vapor is a Swift web framework. Routes are typically defined in
    Controllers/*Controller.swift or routes.swift files.
    """

    def test_detect_vapor_controller_file(self) -> None:
        """Detect methods in *Controller.swift as Vapor routes."""
        sym = make_symbol("index", path="Sources/App/Controllers/UserController.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1
        assert vapor_eps[0].symbol_id == sym.id

    def test_detect_vapor_routes_file(self) -> None:
        """Detect methods in routes.swift as Vapor routes."""
        sym = make_symbol("configureRoutes", path="Sources/App/routes.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1

    def test_detect_vapor_controllers_directory(self) -> None:
        """Detect methods in Controllers/ directory."""
        sym = make_symbol("create", path="Sources/App/Controllers/TodoController.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1

    def test_detect_vapor_routes_directory(self) -> None:
        """Detect methods in routes/ directory."""
        sym = make_symbol("setup", path="Sources/App/Routes/UserRoutes.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1

    def test_detect_multiple_vapor_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("list", path="Sources/App/Controllers/ItemController.swift", language="swift", start_line=10)
        sym2 = make_symbol("show", path="Sources/App/Controllers/ItemController.swift", language="swift", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 2

    def test_vapor_route_confidence(self) -> None:
        """Vapor route detection has appropriate confidence."""
        sym = make_symbol("update", path="Sources/App/Controllers/OrderController.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1
        assert vapor_eps[0].confidence >= 0.80

    def test_vapor_route_label(self) -> None:
        """Vapor route entrypoints have descriptive labels."""
        sym = make_symbol("delete", path="Sources/App/Controllers/ProductController.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 1
        assert "Vapor" in vapor_eps[0].label or "route" in vapor_eps[0].label.lower()

    def test_vapor_only_swift_files(self) -> None:
        """Only Swift files are detected as Vapor routes."""
        # Python file should NOT be detected
        sym = make_symbol("index", path="Sources/App/Controllers/UserController.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 0

    def test_vapor_file_symbol_not_detected(self) -> None:
        """File symbols in Vapor files are not detected."""
        sym = make_symbol("file", kind="file", path="Sources/App/Controllers/UserController.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 0

    def test_vapor_non_route_file_not_detected(self) -> None:
        """Non-route Swift files are not detected as Vapor."""
        sym = make_symbol("helper", path="Sources/App/Services/UserService.swift", language="swift")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        vapor_eps = [e for e in entrypoints if e.kind == EntrypointKind.VAPOR_ROUTE]
        assert len(vapor_eps) == 0


class TestPlugEntrypoints:
    """Tests for Plug (Elixir) route detection.

    Plug is Elixir's HTTP middleware library. Routes are typically defined in
    router.ex files or files ending in _router.ex.
    """

    def test_detect_plug_router_file(self) -> None:
        """Detect functions in router.ex as Plug routes."""
        sym = make_symbol("match", path="lib/myapp/router.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1
        assert plug_eps[0].symbol_id == sym.id

    def test_detect_plug_named_router(self) -> None:
        """Detect functions in *_router.ex as Plug routes."""
        sym = make_symbol("call", path="lib/myapp/api_router.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1

    def test_detect_plug_in_plugs_directory(self) -> None:
        """Detect functions in plugs/ directory."""
        sym = make_symbol("init", path="lib/myapp/plugs/auth.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1

    def test_detect_plug_endpoint(self) -> None:
        """Detect functions in *_plug.ex files."""
        sym = make_symbol("call", path="lib/myapp/auth_plug.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1

    def test_detect_multiple_plug_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("get_user", path="lib/myapp/router.ex", language="elixir", start_line=10)
        sym2 = make_symbol("create_user", path="lib/myapp/router.ex", language="elixir", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 2

    def test_plug_route_confidence(self) -> None:
        """Plug route detection has appropriate confidence."""
        sym = make_symbol("call", path="lib/myapp/router.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1
        assert plug_eps[0].confidence >= 0.80

    def test_plug_route_label(self) -> None:
        """Plug route entrypoints have descriptive labels."""
        sym = make_symbol("match", path="lib/myapp/router.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 1
        assert "Plug" in plug_eps[0].label or "route" in plug_eps[0].label.lower()

    def test_plug_only_elixir_files(self) -> None:
        """Only Elixir files are detected as Plug routes."""
        # Python file should NOT be detected
        sym = make_symbol("match", path="lib/myapp/router.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 0

    def test_plug_file_symbol_not_detected(self) -> None:
        """File symbols in Plug files are not detected."""
        sym = make_symbol("file", kind="file", path="lib/myapp/router.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 0

    def test_plug_non_route_file_not_detected(self) -> None:
        """Non-route Elixir files are not detected as Plug."""
        sym = make_symbol("helper", path="lib/myapp/utils.ex", language="elixir")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        plug_eps = [e for e in entrypoints if e.kind == EntrypointKind.PLUG_ROUTE]
        assert len(plug_eps) == 0


class TestHapiEntrypoints:
    """Tests for Hapi (Node.js) route detection.

    Hapi is a Node.js web framework. Routes are typically defined in
    files named *routes.js/ts or in routes/ directory.
    """

    def test_detect_hapi_routes_file(self) -> None:
        """Detect functions in *routes.js as Hapi routes."""
        sym = make_symbol("getUsers", path="src/userRoutes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1
        assert hapi_eps[0].symbol_id == sym.id

    def test_detect_hapi_routes_ts_file(self) -> None:
        """Detect functions in *routes.ts as Hapi routes."""
        sym = make_symbol("createUser", path="src/apiRoutes.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1

    def test_detect_hapi_routes_directory(self) -> None:
        """Detect functions in plugins/ directory (Hapi-specific)."""
        # Note: routes/ is shared with Express, plugins/ is Hapi-specific
        sym = make_symbol("listItems", path="src/plugins/items.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1

    def test_detect_hapi_plugins_directory(self) -> None:
        """Detect functions in plugins/ directory (Hapi convention)."""
        sym = make_symbol("register", path="src/plugins/auth.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1

    def test_detect_multiple_hapi_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("get", path="src/userRoutes.js", language="javascript", start_line=10)
        sym2 = make_symbol("post", path="src/userRoutes.js", language="javascript", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 2

    def test_hapi_route_confidence(self) -> None:
        """Hapi route detection has appropriate confidence."""
        sym = make_symbol("handler", path="src/plugins/api.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1
        assert hapi_eps[0].confidence >= 0.80

    def test_hapi_route_label(self) -> None:
        """Hapi route entrypoints have descriptive labels."""
        sym = make_symbol("deleteUser", path="src/userRoutes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 1
        assert "Hapi" in hapi_eps[0].label or "route" in hapi_eps[0].label.lower()

    def test_hapi_only_js_ts_files(self) -> None:
        """Only JS/TS files are detected as Hapi routes."""
        # Python file should NOT be detected
        sym = make_symbol("getUsers", path="src/userRoutes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 0

    def test_hapi_file_symbol_not_detected(self) -> None:
        """File symbols in Hapi files are not detected."""
        sym = make_symbol("file", kind="file", path="src/userRoutes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 0

    def test_hapi_non_route_file_not_detected(self) -> None:
        """Non-route JS files are not detected as Hapi."""
        sym = make_symbol("helper", path="src/utils/helper.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 0

    def test_hapi_tsx_files_not_detected(self) -> None:
        """TSX files in routes/ are React components, not Hapi routes."""
        sym = make_symbol("Dashboard", path="frontend/src/routes/dashboard.tsx", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        hapi_eps = [e for e in entrypoints if e.kind == EntrypointKind.HAPI_ROUTE]
        assert len(hapi_eps) == 0


class TestFastifyEntrypoints:
    """Tests for Fastify (Node.js) route detection."""

    def test_detect_fastify_routes_file(self) -> None:
        """Detect functions in *.routes.js as Fastify routes."""
        sym = make_symbol("getUsers", path="src/user.routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1

    def test_detect_fastify_routes_ts_file(self) -> None:
        """Detect functions in *.routes.ts as Fastify routes."""
        sym = make_symbol("createOrder", path="src/order.routes.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1

    def test_detect_fastify_route_singular_file(self) -> None:
        """Detect functions in *.route.js (singular) as Fastify routes."""
        sym = make_symbol("getProduct", path="src/product.route.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1

    def test_detect_fastify_schema_file(self) -> None:
        """Detect functions in *.schema.js as Fastify schema (route adjacent)."""
        sym = make_symbol("validateUser", path="src/user.schema.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1

    def test_detect_multiple_fastify_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("get", path="src/api.routes.js", language="javascript", start_line=10)
        sym2 = make_symbol("post", path="src/api.routes.js", language="javascript", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 2

    def test_fastify_route_confidence(self) -> None:
        """Fastify route detection has appropriate confidence."""
        sym = make_symbol("handler", path="src/item.routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1
        assert fastify_eps[0].confidence >= 0.80

    def test_fastify_route_label(self) -> None:
        """Fastify route entrypoints have descriptive labels."""
        sym = make_symbol("deleteUser", path="src/user.routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 1
        assert "Fastify" in fastify_eps[0].label or "route" in fastify_eps[0].label.lower()

    def test_fastify_only_js_ts_files(self) -> None:
        """Only JS/TS files are detected as Fastify routes."""
        # Python file should NOT be detected
        sym = make_symbol("getUsers", path="src/user.routes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 0

    def test_fastify_file_symbol_not_detected(self) -> None:
        """File symbols in Fastify files are not detected."""
        sym = make_symbol("file", kind="file", path="src/user.routes.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 0

    def test_fastify_non_route_file_not_detected(self) -> None:
        """Non-route JS files are not detected as Fastify."""
        sym = make_symbol("helper", path="src/utils/helper.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        fastify_eps = [e for e in entrypoints if e.kind == EntrypointKind.FASTIFY_ROUTE]
        assert len(fastify_eps) == 0


class TestKoaEntrypoints:
    """Tests for Koa (Node.js) route detection."""

    def test_detect_koa_router_file(self) -> None:
        """Detect functions in *.router.js as Koa routes."""
        sym = make_symbol("getUsers", path="src/user.router.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1

    def test_detect_koa_router_ts_file(self) -> None:
        """Detect functions in *.router.ts as Koa routes."""
        sym = make_symbol("createOrder", path="src/order.router.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1

    def test_detect_koa_controller_file(self) -> None:
        """Detect functions in *.controller.js as Koa controllers."""
        sym = make_symbol("getProduct", path="src/product.controller.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1

    def test_detect_koa_middleware_file(self) -> None:
        """Detect functions in *.middleware.js as Koa middleware."""
        sym = make_symbol("authCheck", path="src/auth.middleware.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1

    def test_detect_multiple_koa_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("get", path="src/api.router.js", language="javascript", start_line=10)
        sym2 = make_symbol("post", path="src/api.router.js", language="javascript", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 2

    def test_koa_route_confidence(self) -> None:
        """Koa route detection has appropriate confidence."""
        sym = make_symbol("handler", path="src/item.router.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1
        assert koa_eps[0].confidence >= 0.80

    def test_koa_route_label(self) -> None:
        """Koa route entrypoints have descriptive labels."""
        sym = make_symbol("deleteUser", path="src/user.router.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 1
        assert "Koa" in koa_eps[0].label or "route" in koa_eps[0].label.lower()

    def test_koa_only_js_ts_files(self) -> None:
        """Only JS/TS files are detected as Koa routes."""
        # Python file should NOT be detected
        sym = make_symbol("getUsers", path="src/user.router.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 0

    def test_koa_file_symbol_not_detected(self) -> None:
        """File symbols in Koa files are not detected."""
        sym = make_symbol("file", kind="file", path="src/user.router.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 0

    def test_koa_non_route_file_not_detected(self) -> None:
        """Non-route JS files are not detected as Koa."""
        sym = make_symbol("helper", path="src/utils/helper.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 0

    def test_koa_tsx_files_not_detected(self) -> None:
        """TSX files with .controller. pattern are React, not Koa."""
        sym = make_symbol("UserController", path="src/components/user.controller.tsx", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        koa_eps = [e for e in entrypoints if e.kind == EntrypointKind.KOA_ROUTE]
        assert len(koa_eps) == 0


class TestGrapeEntrypoints:
    """Tests for Grape (Ruby) API detection."""

    def test_detect_grape_api_file(self) -> None:
        """Detect classes in *_api.rb as Grape APIs."""
        sym = make_symbol("UsersAPI", path="lib/users_api.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1

    def test_detect_grape_api_directory(self) -> None:
        """Detect classes in api/ directory as Grape APIs."""
        sym = make_symbol("Users", path="app/api/v1/users.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1

    def test_detect_grape_endpoints_directory(self) -> None:
        """Detect classes in endpoints/ directory as Grape endpoints."""
        sym = make_symbol("ProductsEndpoint", path="lib/endpoints/products.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1

    def test_detect_grape_entities_directory(self) -> None:
        """Detect classes in entities/ directory as Grape entities."""
        sym = make_symbol("UserEntity", path="app/api/entities/user.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1

    def test_detect_multiple_grape_apis(self) -> None:
        """Detect multiple APIs in same file."""
        sym1 = make_symbol("get", path="app/api/users.rb", language="ruby", start_line=10)
        sym2 = make_symbol("post", path="app/api/users.rb", language="ruby", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 2

    def test_grape_api_confidence(self) -> None:
        """Grape API detection has appropriate confidence."""
        sym = make_symbol("handler", path="app/api/items.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1
        assert grape_eps[0].confidence >= 0.80

    def test_grape_api_label(self) -> None:
        """Grape API entrypoints have descriptive labels."""
        sym = make_symbol("delete_user", path="lib/users_api.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 1
        assert "Grape" in grape_eps[0].label or "API" in grape_eps[0].label

    def test_grape_only_ruby_files(self) -> None:
        """Only Ruby files are detected as Grape APIs."""
        # Python file should NOT be detected
        sym = make_symbol("UsersAPI", path="lib/users_api.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 0

    def test_grape_file_symbol_not_detected(self) -> None:
        """File symbols in Grape files are not detected."""
        sym = make_symbol("file", kind="file", path="app/api/users.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 0

    def test_grape_non_api_file_not_detected(self) -> None:
        """Non-API Ruby files are not detected as Grape."""
        sym = make_symbol("helper", path="lib/utils/helper.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 0

    def test_grape_test_file_not_detected(self) -> None:
        """Test files in api directory are not detected as Grape APIs."""
        syms = [
            make_symbol("describe", path="spec/api/users_spec.rb", language="ruby"),
            make_symbol("test_api", path="test/users_api.rb", language="ruby"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        grape_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPE_API]
        assert len(grape_eps) == 0


class TestTornadoEntrypoints:
    """Tests for Tornado (Python) handler detection."""

    def test_detect_tornado_handler_file(self) -> None:
        """Detect functions in *_handler.py as Tornado handlers."""
        sym = make_symbol("UserHandler", path="app/user_handler.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1

    def test_detect_tornado_handlers_directory(self) -> None:
        """Detect functions in handlers/ directory as Tornado handlers."""
        sym = make_symbol("MainHandler", path="app/handlers/main.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1

    def test_detect_tornado_views_directory(self) -> None:
        """Detect functions in views/ directory as Tornado views."""
        sym = make_symbol("HomeView", path="app/views/home.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1

    def test_detect_tornado_websocket_handler(self) -> None:
        """Detect WebSocket handlers."""
        sym = make_symbol("ChatSocket", path="app/websocket_handler.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1

    def test_detect_multiple_tornado_handlers(self) -> None:
        """Detect multiple handlers in same file."""
        sym1 = make_symbol("get", path="app/handlers/api.py", language="python", start_line=10)
        sym2 = make_symbol("post", path="app/handlers/api.py", language="python", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 2

    def test_tornado_handler_confidence(self) -> None:
        """Tornado handler detection has appropriate confidence."""
        sym = make_symbol("Handler", path="app/handlers/items.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1
        assert tornado_eps[0].confidence >= 0.80

    def test_tornado_handler_label(self) -> None:
        """Tornado handler entrypoints have descriptive labels."""
        sym = make_symbol("DeleteHandler", path="app/user_handler.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 1
        assert "Tornado" in tornado_eps[0].label or "handler" in tornado_eps[0].label.lower()

    def test_tornado_only_python_files(self) -> None:
        """Only Python files are detected as Tornado handlers."""
        # Ruby file should NOT be detected
        sym = make_symbol("UserHandler", path="app/user_handler.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 0

    def test_tornado_file_symbol_not_detected(self) -> None:
        """File symbols in Tornado files are not detected."""
        sym = make_symbol("file", kind="file", path="app/handlers/main.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 0

    def test_tornado_non_handler_file_not_detected(self) -> None:
        """Non-handler Python files are not detected as Tornado."""
        sym = make_symbol("helper", path="app/utils/helper.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 0

    def test_tornado_test_file_not_detected(self) -> None:
        """Test files ending in _handler.py are not detected as Tornado handlers."""
        # This was a real bug: tests/test_user_error_handler.py was misdetected
        syms = [
            make_symbol("test_handler", path="tests/test_user_error_handler.py", language="python"),
            make_symbol("TestClass", path="test/test_handler.py", language="python"),
            make_symbol("test_func", path="spec/user_handler_test.py", language="python"),
            # Cover spec_ prefix pattern: file in handlers/ dir with spec_ prefix
            make_symbol("spec_func", path="app/handlers/spec_user.py", language="python"),
            # Cover _spec.py suffix pattern: file ending in _handler.py but also _spec.py
            make_symbol("describe", path="app/user_handler_spec.py", language="python"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 0

    def test_tornado_non_web_handlers_not_detected(self) -> None:
        """Non-web handler patterns are excluded (error, signal, event handlers)."""
        syms = [
            make_symbol("ErrorHandler", path="app/error_handler.py", language="python"),
            make_symbol("SignalHandler", path="app/signal_handler.py", language="python"),
            make_symbol("EventHandler", path="app/event_handler.py", language="python"),
            make_symbol("ExceptionHandler", path="app/exception_handler.py", language="python"),
            make_symbol("LoggingHandler", path="app/logging_handler.py", language="python"),
            make_symbol("LogHandler", path="app/log_handler.py", language="python"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        tornado_eps = [e for e in entrypoints if e.kind == EntrypointKind.TORNADO_HANDLER]
        assert len(tornado_eps) == 0


class TestAiohttpEntrypoints:
    """Tests for Aiohttp (Python) view detection."""

    def test_detect_aiohttp_view_file(self) -> None:
        """Detect classes in *_view.py as Aiohttp views."""
        sym = make_symbol("UserView", path="app/user_view.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1

    def test_detect_aiohttp_resource_file(self) -> None:
        """Detect classes in *_resource.py as Aiohttp resources."""
        sym = make_symbol("UserResource", path="app/user_resource.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1

    def test_detect_aiohttp_resources_directory(self) -> None:
        """Detect classes in resources/ directory as Aiohttp resources."""
        sym = make_symbol("ProductResource", path="app/resources/product.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1

    def test_detect_aiohttp_routes_file(self) -> None:
        """Detect handlers in routes.py file."""
        sym = make_symbol("setup_routes", path="app/routes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1

    def test_detect_multiple_aiohttp_views(self) -> None:
        """Detect multiple views in same file."""
        sym1 = make_symbol("get", path="app/user_view.py", language="python", start_line=10)
        sym2 = make_symbol("post", path="app/user_view.py", language="python", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 2

    def test_aiohttp_view_confidence(self) -> None:
        """Aiohttp view detection has appropriate confidence."""
        sym = make_symbol("Handler", path="app/user_view.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1
        assert aiohttp_eps[0].confidence >= 0.80

    def test_aiohttp_view_label(self) -> None:
        """Aiohttp view entrypoints have descriptive labels."""
        sym = make_symbol("DeleteView", path="app/user_view.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 1
        assert "Aiohttp" in aiohttp_eps[0].label or "view" in aiohttp_eps[0].label.lower()

    def test_aiohttp_only_python_files(self) -> None:
        """Only Python files are detected as Aiohttp views."""
        # Ruby file should NOT be detected
        sym = make_symbol("UserView", path="app/user_view.rb", language="ruby")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 0

    def test_aiohttp_file_symbol_not_detected(self) -> None:
        """File symbols in Aiohttp files are not detected."""
        sym = make_symbol("file", kind="file", path="app/user_view.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 0

    def test_aiohttp_non_view_file_not_detected(self) -> None:
        """Non-view Python files are not detected as Aiohttp."""
        sym = make_symbol("helper", path="app/utils/helper.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 0

    def test_aiohttp_test_file_not_detected(self) -> None:
        """Test files ending in _view.py are not detected as Aiohttp views."""
        syms = [
            make_symbol("test_view", path="tests/test_view.py", language="python"),
            make_symbol("TestView", path="test/user_view_test.py", language="python"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        aiohttp_eps = [e for e in entrypoints if e.kind == EntrypointKind.AIOHTTP_VIEW]
        assert len(aiohttp_eps) == 0


class TestSlimEntrypoints:
    """Tests for Slim (PHP) route detection."""

    def test_detect_slim_routes_file(self) -> None:
        """Detect functions in routes.php as Slim routes."""
        sym = make_symbol("registerRoutes", path="src/routes.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1

    def test_detect_slim_middleware_file(self) -> None:
        """Detect functions in *Middleware.php as Slim middleware."""
        sym = make_symbol("AuthMiddleware", path="src/AuthMiddleware.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1

    def test_detect_slim_actions_directory(self) -> None:
        """Detect classes in Actions/ directory as Slim actions."""
        sym = make_symbol("CreateUserAction", path="src/Actions/CreateUserAction.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1

    def test_detect_slim_handlers_directory(self) -> None:
        """Detect classes in Handlers/ directory as Slim handlers."""
        sym = make_symbol("HomeHandler", path="src/Handlers/HomeHandler.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1

    def test_detect_multiple_slim_routes(self) -> None:
        """Detect multiple routes in same file."""
        sym1 = make_symbol("get", path="src/routes.php", language="php", start_line=10)
        sym2 = make_symbol("post", path="src/routes.php", language="php", start_line=20)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 2

    def test_slim_route_confidence(self) -> None:
        """Slim route detection has appropriate confidence."""
        sym = make_symbol("handler", path="src/routes.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1
        assert slim_eps[0].confidence >= 0.80

    def test_slim_route_label(self) -> None:
        """Slim route entrypoints have descriptive labels."""
        sym = make_symbol("deleteUser", path="src/routes.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 1
        assert "Slim" in slim_eps[0].label or "route" in slim_eps[0].label.lower()

    def test_slim_only_php_files(self) -> None:
        """Only PHP files are detected as Slim routes."""
        # Python file should NOT be detected
        sym = make_symbol("handler", path="src/routes.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 0

    def test_slim_file_symbol_not_detected(self) -> None:
        """File symbols in Slim files are not detected."""
        sym = make_symbol("file", kind="file", path="src/routes.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 0

    def test_slim_non_route_file_not_detected(self) -> None:
        """Non-route PHP files are not detected as Slim."""
        sym = make_symbol("helper", path="src/Utils/Helper.php", language="php")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        slim_eps = [e for e in entrypoints if e.kind == EntrypointKind.SLIM_ROUTE]
        assert len(slim_eps) == 0


class TestMicronautEntrypoints:
    """Tests for Micronaut (Java/Kotlin) HTTP client detection.

    Micronaut HTTP clients are distinct from controllers - they're declarative
    interfaces for calling external services. Controllers use the same patterns
    as Spring and are detected by Spring detection.
    """

    def test_detect_micronaut_client_java(self) -> None:
        """Detect Micronaut HTTP client from *Client.java naming."""
        sym = make_symbol("UserClient", path="src/main/java/UserClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 1
        assert micronaut_eps[0].symbol_id == sym.id

    def test_detect_micronaut_client_kotlin(self) -> None:
        """Detect Micronaut HTTP client from *Client.kt naming."""
        sym = make_symbol("OrderClient", path="src/main/kotlin/OrderClient.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 1

    def test_detect_micronaut_client_directory(self) -> None:
        """Detect clients in client/ directory."""
        sym = make_symbol("getUsers", path="src/main/java/client/UserApi.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 1

    def test_detect_multiple_micronaut_clients(self) -> None:
        """Detect multiple Micronaut HTTP clients."""
        sym1 = make_symbol("UserClient", path="src/UserClient.java", language="java")
        sym2 = make_symbol("OrderClient", path="src/OrderClient.kt", language="kotlin")
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 2

    def test_micronaut_client_confidence(self) -> None:
        """Micronaut client has medium-high confidence."""
        sym = make_symbol("UserClient", path="src/UserClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert micronaut_eps[0].confidence == 0.85

    def test_micronaut_client_label(self) -> None:
        """Micronaut client has appropriate label."""
        sym = make_symbol("UserClient", path="src/UserClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert micronaut_eps[0].label == "Micronaut controller"

    def test_micronaut_only_java_kotlin_files(self) -> None:
        """Only Java/Kotlin files are detected as Micronaut clients."""
        # Python file should NOT be detected
        sym = make_symbol("UserClient", path="src/UserClient.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0

    def test_micronaut_file_symbol_not_detected(self) -> None:
        """File symbols in Micronaut files are not detected."""
        sym = make_symbol("file", kind="file", path="src/UserClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0

    def test_micronaut_non_client_file_not_detected(self) -> None:
        """Non-client Java files are not detected as Micronaut."""
        sym = make_symbol("Helper", path="src/utils/Helper.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0

    def test_grpc_service_client_not_detected(self) -> None:
        """gRPC stub wrappers (*ServiceClient) are NOT Micronaut clients.

        Files like AdServiceClient.java are typically gRPC stub wrappers,
        not Micronaut HTTP clients. They should be excluded.
        """
        sym = make_symbol("AdServiceClient", path="src/AdServiceClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0

    def test_grpc_client_not_detected(self) -> None:
        """*GrpcClient.java files are NOT Micronaut clients."""
        sym = make_symbol("UserGrpcClient", path="src/UserGrpcClient.java", language="java")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0

    def test_rpc_client_not_detected(self) -> None:
        """*RpcClient.java files are NOT Micronaut clients."""
        sym = make_symbol("OrderRpcClient", path="src/OrderRpcClient.kt", language="kotlin")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        micronaut_eps = [e for e in entrypoints if e.kind == EntrypointKind.MICRONAUT_CONTROLLER]
        assert len(micronaut_eps) == 0


class TestGraphQLServerEntrypoints:
    """Tests for GraphQL server (Apollo, Yoga, Mercurius) detection.

    GraphQL servers typically define resolvers in specific file patterns:
    - resolvers.js/ts, schema.js/ts, typeDefs.js/ts
    - *.resolver.js/ts, *.resolvers.js/ts
    - Files in resolvers/ or graphql/ directories
    """

    def test_detect_graphql_resolvers_file(self) -> None:
        """Detect functions in resolvers.js as GraphQL resolvers."""
        sym = make_symbol("Query", path="src/resolvers.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1
        assert graphql_eps[0].symbol_id == sym.id

    def test_detect_graphql_resolvers_ts_file(self) -> None:
        """Detect functions in resolvers.ts as GraphQL resolvers."""
        sym = make_symbol("Mutation", path="src/resolvers.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_schema_file(self) -> None:
        """Detect functions in schema.js as GraphQL schema."""
        sym = make_symbol("typeDefs", path="src/schema.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_typedefs_file(self) -> None:
        """Detect functions in typeDefs.ts as GraphQL type definitions."""
        sym = make_symbol("UserType", path="src/typeDefs.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_resolver_suffix(self) -> None:
        """Detect functions in *.resolver.ts as GraphQL resolvers."""
        sym = make_symbol("getUser", path="src/user.resolver.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_resolvers_suffix(self) -> None:
        """Detect functions in *.resolvers.js (plural) as GraphQL resolvers."""
        sym = make_symbol("userResolvers", path="src/user.resolvers.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_resolvers_directory(self) -> None:
        """Detect functions in resolvers/ directory as GraphQL resolvers."""
        sym = make_symbol("userQuery", path="src/resolvers/user.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_graphql_graphql_directory(self) -> None:
        """Detect functions in graphql/ directory as GraphQL server files."""
        sym = make_symbol("schema", path="src/graphql/index.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1

    def test_detect_multiple_graphql_resolvers(self) -> None:
        """Detect multiple resolvers in same file."""
        sym1 = make_symbol("Query", path="src/resolvers.ts", language="typescript", start_line=10)
        sym2 = make_symbol("Mutation", path="src/resolvers.ts", language="typescript", start_line=50)
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 2

    def test_graphql_resolver_confidence(self) -> None:
        """GraphQL resolver detection has appropriate confidence."""
        sym = make_symbol("resolver", path="src/resolvers.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1
        assert graphql_eps[0].confidence == 0.85

    def test_graphql_resolver_label(self) -> None:
        """GraphQL resolver entrypoints have descriptive labels."""
        sym = make_symbol("Query", path="src/resolvers.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 1
        assert "GraphQL" in graphql_eps[0].label or "resolver" in graphql_eps[0].label.lower()

    def test_graphql_only_js_ts_files(self) -> None:
        """Only JS/TS files are detected as GraphQL resolvers."""
        # Python file should NOT be detected
        sym = make_symbol("Query", path="src/resolvers.py", language="python")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_file_symbol_not_detected(self) -> None:
        """File symbols in GraphQL files are not detected."""
        sym = make_symbol("file", kind="file", path="src/resolvers.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_non_resolver_file_not_detected(self) -> None:
        """Non-resolver JS files are not detected as GraphQL."""
        sym = make_symbol("helper", path="src/utils/helper.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_test_file_not_detected(self) -> None:
        """Test files with resolver patterns are not detected."""
        sym = make_symbol("Query", path="tests/resolvers.test.ts", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_spec_file_not_detected(self) -> None:
        """Spec files with resolver patterns are not detected."""
        sym = make_symbol("Query", path="spec/resolvers.spec.js", language="javascript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_tsx_files_not_detected(self) -> None:
        """TSX files in graphql/ are React components, not GraphQL servers."""
        sym = make_symbol("GraphQLProvider", path="src/graphql/Provider.tsx", language="typescript")
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0

    def test_graphql_non_graphql_resolvers_not_detected(self) -> None:
        """Non-GraphQL resolver patterns are excluded (dns, promise, dependency)."""
        syms = [
            make_symbol("DnsResolver", path="src/dns-resolver.js", language="javascript"),
            make_symbol("resolve", path="src/promise_resolver.ts", language="typescript"),
            make_symbol("DependencyResolver", path="src/dependency-resolver.js", language="javascript"),
            make_symbol("resolve", path="src/path_resolver.js", language="javascript"),
            make_symbol("ModuleResolver", path="src/module-resolver.ts", language="typescript"),
        ]

        entrypoints = detect_entrypoints(syms, [])

        graphql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(graphql_eps) == 0


class TestIsTestFile:
    """Tests for _is_test_file function."""

    def test_python_test_prefix(self) -> None:
        """Detect Python test_ prefix."""
        assert _is_test_file("test_main.py")
        assert _is_test_file("src/test_utils.py")

    def test_python_test_suffix(self) -> None:
        """Detect Python _test.py suffix."""
        assert _is_test_file("main_test.py")
        assert _is_test_file("src/utils_test.py")

    def test_python_spec_patterns(self) -> None:
        """Detect Python spec patterns."""
        assert _is_test_file("spec_main.py")
        assert _is_test_file("main_spec.py")

    def test_go_test_suffix(self) -> None:
        """Detect Go _test.go suffix."""
        assert _is_test_file("main_test.go")
        assert _is_test_file("pkg/handlers/user_test.go")

    def test_mock_filename_suffix(self) -> None:
        """Detect *_mock.* filename patterns."""
        assert _is_test_file("user_mock.go")
        assert _is_test_file("service_mock.py")
        assert _is_test_file("src/handler_mock.ts")

    def test_mock_filename_prefix(self) -> None:
        """Detect mock_*.* filename patterns."""
        assert _is_test_file("src/mock_user.go")
        assert _is_test_file("mock_service.py")

    def test_fake_filename_suffix(self) -> None:
        """Detect *_fake.* filename patterns."""
        assert _is_test_file("user_fake.go")
        assert _is_test_file("src/handler_fake.ts")

    def test_fake_filename_prefix(self) -> None:
        """Detect fake_*.* filename patterns."""
        assert _is_test_file("src/fake_user.go")
        assert _is_test_file("fake_handler.go")

    def test_fakes_directory(self) -> None:
        """Detect files in fakes/ directory."""
        assert _is_test_file("pkg/rtc/transport/transportfakes/fake_handler.go")
        assert _is_test_file("internal/fakes/mock_service.go")

    def test_mocks_directory(self) -> None:
        """Detect files in mocks/ directory."""
        assert _is_test_file("pkg/mocks/user_service.go")
        assert _is_test_file("src/mocks/api_client.ts")

    def test_fixtures_directory(self) -> None:
        """Detect files in fixtures/ directory."""
        assert _is_test_file("tests/fixtures/sample_data.json")
        assert _is_test_file("fixtures/test_user.py")

    def test_testdata_directory(self) -> None:
        """Detect files in testdata/ directory."""
        assert _is_test_file("pkg/testdata/sample.txt")
        assert _is_test_file("testdata/config.yaml")

    def test_testutils_directory(self) -> None:
        """Detect files in testutils/ directory."""
        assert _is_test_file("pkg/testutils/helpers.go")
        assert _is_test_file("testutils/factory.py")

    def test_regular_file_not_detected(self) -> None:
        """Regular source files are not detected as test files."""
        assert not _is_test_file("src/main.py")
        assert not _is_test_file("pkg/handlers/user.go")
        assert not _is_test_file("internal/api/routes.ts")

    def test_case_insensitive_directories(self) -> None:
        """Directory matching is case-insensitive."""
        assert _is_test_file("src/MOCKS/service.go")
        assert _is_test_file("Fixtures/data.json")
        assert _is_test_file("TESTDATA/sample.txt")

    def test_compound_directory_names(self) -> None:
        """Detect directories ending with 'fakes' or 'mocks'."""
        # These hit endswith("fakes") and endswith("mocks") specifically
        assert _is_test_file("pkg/rtc/transport/transportfakes/handler.go")
        assert _is_test_file("internal/servicemocks/client.go")


class TestSemanticEntryDetection:
    """Tests for semantic entry detection from concept metadata.

    ADR-0003 v0.9.x introduces semantic entry detection: detecting entrypoints
    based on enriched symbol metadata (meta.concepts) from the FRAMEWORK_PATTERNS
    phase, rather than path-based heuristics.

    Semantic detection has:
    - Higher confidence (0.95) since it's based on actual decorator/pattern matching
    - Priority over path-based detection
    - Framework-aware labels
    """

    def test_detect_route_concept(self) -> None:
        """Symbol with route concept in meta.concepts is detected as route."""
        sym = make_symbol(
            "get_users",
            path="src/api/users.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "GET"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert route_eps[0].symbol_id == sym.id
        # Semantic detection should have high confidence
        assert route_eps[0].confidence >= 0.95

    def test_detect_post_route_concept(self) -> None:
        """Symbol with POST route concept is detected as route."""
        sym = make_symbol(
            "create_user",
            path="src/api/users.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "POST"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1

    def test_route_concept_includes_path_in_label(self) -> None:
        """Route concept label includes the path from concept metadata."""
        sym = make_symbol(
            "get_item",
            path="src/api/items.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/items/{id}", "method": "GET"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        # Label should include method and/or path info
        assert "GET" in route_eps[0].label or "/items" in route_eps[0].label

    def test_semantic_detection_priority_over_path_heuristics(self) -> None:
        """Semantic detection takes priority, avoiding duplicate detection.

        If a symbol is detected via concept metadata, it should NOT also be
        detected via path heuristics (which could produce duplicates or
        lower-confidence entries).
        """
        # Symbol in Express route file BUT also has concept metadata
        sym = make_symbol(
            "getUsers",
            path="src/routes/users.js",  # Express path pattern
            language="javascript",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "GET"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only be detected once, not twice
        all_eps = [e for e in entrypoints if e.symbol_id == sym.id]
        assert len(all_eps) == 1
        # And it should be the semantic detection (high confidence)
        assert all_eps[0].confidence >= 0.95

    def test_multiple_route_concepts_in_file(self) -> None:
        """Multiple symbols with route concepts are all detected."""
        sym1 = make_symbol(
            "get_users",
            path="src/api/users.py",
            start_line=10,
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]},
        )
        sym2 = make_symbol(
            "create_user",
            path="src/api/users.py",
            start_line=20,
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "POST"}]},
        )
        nodes = [sym1, sym2]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 2

    def test_model_concept_not_detected_as_entrypoint(self) -> None:
        """Model concept is NOT an entrypoint (models are not entry kinds)."""
        sym = make_symbol(
            "User",
            kind="class",
            path="src/models/user.py",
            meta={
                "concepts": [{"concept": "model", "framework": "fastapi"}]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Models are not entrypoints
        assert len(entrypoints) == 0

    def test_no_concepts_falls_back_to_path_heuristics(self) -> None:
        """Without concept metadata, path heuristics still work as fallback."""
        # Express route file but no concept metadata
        sym = make_symbol(
            "getUsers",
            path="src/routes/users.js",
            language="javascript",
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should be detected by Express path heuristics
        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1
        # Path heuristics have lower confidence
        assert express_eps[0].confidence < 0.95

    def test_empty_concepts_list_falls_back(self) -> None:
        """Empty concepts list falls back to path heuristics."""
        sym = make_symbol(
            "getUsers",
            path="src/routes/users.js",
            language="javascript",
            meta={"concepts": []},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should still be detected by Express path heuristics
        express_eps = [e for e in entrypoints if e.kind == EntrypointKind.EXPRESS_ROUTE]
        assert len(express_eps) == 1

    def test_react_router_not_detected_with_semantic(self) -> None:
        """React Router files without route concepts are NOT detected as routes.

        This is the key false positive elimination: React Router files in
        routes/*.tsx should NOT be flagged as Express/API routes because
        they don't have route concept metadata from FRAMEWORK_PATTERNS.
        """
        # React Router file - has a route-like path but no concept metadata
        sym = make_symbol(
            "Dashboard",
            path="frontend/src/routes/dashboard.tsx",
            language="typescript",
            kind="function",
            # No meta - React files don't get route concepts
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should NOT be detected as any route type
        # (The .tsx exclusion prevents Express/Hapi detection)
        route_like_eps = [
            e for e in entrypoints
            if e.kind in (
                EntrypointKind.HTTP_ROUTE,
                EntrypointKind.EXPRESS_ROUTE,
                EntrypointKind.HAPI_ROUTE,
            )
        ]
        assert len(route_like_eps) == 0

    def test_non_dict_concept_skipped(self) -> None:
        """Non-dict concepts in the list are skipped."""
        sym = make_symbol(
            "get_users",
            path="src/api/users.py",
            meta={
                "concepts": [
                    "invalid_string_concept",  # Not a dict - should be skipped
                    {"concept": "route", "path": "/users", "method": "GET"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1

    def test_route_concept_method_only(self) -> None:
        """Route concept with only method (no path) still detected."""
        sym = make_symbol(
            "create_resource",
            path="src/api/resources.py",
            meta={
                "concepts": [
                    {"concept": "route", "method": "POST"}  # No path
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        # Label should include method
        assert "POST" in route_eps[0].label
        assert "route" in route_eps[0].label.lower()

    def test_route_concept_path_only(self) -> None:
        """Route concept with only path (no method) still detected."""
        sym = make_symbol(
            "handle_request",
            path="src/api/handler.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/api/v1/resource"}  # No method
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        # Label should include path
        assert "/api/v1/resource" in route_eps[0].label

    def test_route_concept_no_method_no_path(self) -> None:
        """Route concept with neither method nor path still detected."""
        sym = make_symbol(
            "wildcard_handler",
            path="src/api/handler.py",
            meta={
                "concepts": [
                    {"concept": "route"}  # Minimal route concept
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        # Should have a generic label


class TestDeprecationWarnings:
    """Tests for deprecation warnings on path-based heuristics.

    ADR-0003 v0.9.x deprecates path-based entrypoint detection in favor
    of semantic detection via concept metadata from FRAMEWORK_PATTERNS.
    """

    def test_express_route_emits_deprecation_warning(self) -> None:
        """Express route detection via path emits deprecation warning."""
        from hypergumbo import entrypoints as ep_module

        sym = make_symbol(
            "get_users",
            path="src/routes/users.js",
            language="javascript",
            kind="function",
        )

        # Reset the warning tracker so warning fires even if already seen
        ep_module._deprecated_path_warnings_emitted.clear()

        with pytest.warns(DeprecationWarning, match="path-based"):
            detect_entrypoints([sym], [])

    def test_nestjs_controller_emits_deprecation_warning(self) -> None:
        """NestJS controller detection via path emits deprecation warning."""
        from hypergumbo import entrypoints as ep_module

        sym = make_symbol(
            "UserController",
            path="src/users/users.controller.ts",
            language="typescript",
            kind="class",
        )

        ep_module._deprecated_path_warnings_emitted.clear()

        with pytest.warns(DeprecationWarning, match="path-based"):
            detect_entrypoints([sym], [])

    def test_cli_detection_no_deprecation_warning(self) -> None:
        """CLI detection does NOT emit deprecation warning."""
        from hypergumbo import entrypoints as ep_module

        sym = make_symbol(
            "main",
            path="src/main.py",
            language="python",
            kind="function",
        )

        ep_module._deprecated_path_warnings_emitted.clear()

        # Should not raise DeprecationWarning
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detect_entrypoints([sym], [])
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

    def test_semantic_detection_no_deprecation_warning(self) -> None:
        """Semantic detection via concepts does NOT emit deprecation warning."""
        from hypergumbo import entrypoints as ep_module

        sym = make_symbol(
            "get_users",
            path="src/api/users.py",
            language="python",
            kind="function",
            meta={"concepts": [{"concept": "route", "method": "GET"}]},
        )

        ep_module._deprecated_path_warnings_emitted.clear()

        # Should not raise DeprecationWarning
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detect_entrypoints([sym], [])
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

    def test_decorator_detection_no_deprecation_warning(self) -> None:
        """Decorator-based HTTP route detection does NOT emit deprecation warning."""
        from hypergumbo import entrypoints as ep_module

        sym = make_symbol(
            "get_users",
            path="src/api/users.py",
            language="python",
            kind="function",
            decorators=["get", "route"],  # Decorators
        )

        ep_module._deprecated_path_warnings_emitted.clear()

        # Should not raise DeprecationWarning
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detect_entrypoints([sym], [])
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

    def test_deprecation_warning_once_per_framework(self) -> None:
        """Deprecation warning fires only once per framework per session."""
        from hypergumbo import entrypoints as ep_module

        sym1 = make_symbol(
            "get_users",
            path="src/routes/users.js",
            language="javascript",
            kind="function",
        )
        sym2 = make_symbol(
            "get_orders",
            path="src/routes/orders.js",
            language="javascript",
            kind="function",
        )

        ep_module._deprecated_path_warnings_emitted.clear()

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detect_entrypoints([sym1], [])
            detect_entrypoints([sym2], [])
            express_warnings = [
                x for x in w
                if issubclass(x.category, DeprecationWarning)
                and "Express" in str(x.message)
            ]
            # Only ONE warning for Express across both calls
            assert len(express_warnings) == 1


class TestConnectivityBasedRanking:
    """Tests for connectivity-based entrypoint ranking."""

    def test_entrypoints_sorted_by_connectivity(self) -> None:
        """Entrypoints with more outgoing edges should rank higher."""
        # Create three main() functions with same base confidence
        main_a = make_symbol("main", path="a.py", language="python")
        main_b = make_symbol("main", path="b.py", language="python")
        main_c = make_symbol("main", path="c.py", language="python")

        # Create helper functions that main_b and main_c call
        helper1 = make_symbol("helper1", path="helpers.py", language="python")
        helper2 = make_symbol("helper2", path="helpers.py", language="python", start_line=10, end_line=15)
        helper3 = make_symbol("helper3", path="helpers.py", language="python", start_line=20, end_line=25)

        nodes = [main_a, main_b, main_c, helper1, helper2, helper3]

        # main_a calls nothing (0 edges)
        # main_b calls 1 helper (1 edge)
        # main_c calls 3 helpers (3 edges)
        edges = [
            Edge.create(src=main_b.id, dst=helper1.id, edge_type="calls", line=2),
            Edge.create(src=main_c.id, dst=helper1.id, edge_type="calls", line=2),
            Edge.create(src=main_c.id, dst=helper2.id, edge_type="calls", line=3),
            Edge.create(src=main_c.id, dst=helper3.id, edge_type="calls", line=4),
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        # Should find all three main functions
        main_eps = [ep for ep in entrypoints if "main" in ep.symbol_id]
        assert len(main_eps) == 3

        # main_c (3 edges) should rank first, main_b (1 edge) second, main_a (0 edges) last
        assert main_eps[0].symbol_id == main_c.id, "main_c with 3 edges should rank first"
        assert main_eps[1].symbol_id == main_b.id, "main_b with 1 edge should rank second"
        assert main_eps[2].symbol_id == main_a.id, "main_a with 0 edges should rank last"

    def test_connectivity_boost_increases_confidence(self) -> None:
        """Entrypoints with more edges should have higher confidence scores."""
        main_isolated = make_symbol("main", path="isolated.py", language="python")
        main_connected = make_symbol("main", path="connected.py", language="python")
        helper = make_symbol("helper", path="helper.py", language="python")

        nodes = [main_isolated, main_connected, helper]

        # main_connected calls helper multiple times (simulated by multiple edges)
        edges = [
            Edge.create(src=main_connected.id, dst=helper.id, edge_type="calls", line=i)
            for i in range(10)  # 10 outgoing edges
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        main_eps = {ep.symbol_id: ep for ep in entrypoints if "main" in ep.symbol_id}

        # Connected main should have higher confidence than isolated one
        assert main_eps[main_connected.id].confidence > main_eps[main_isolated.id].confidence

    def test_all_entrypoints_still_returned(self) -> None:
        """Connectivity ranking should not filter out any entrypoints."""
        # Create many main functions
        mains = [
            make_symbol("main", path=f"file{i}.py", language="python", start_line=i)
            for i in range(10)
        ]
        helper = make_symbol("helper", path="helper.py", language="python")

        nodes = mains + [helper]

        # Only first main has edges
        edges = [Edge.create(src=mains[0].id, dst=helper.id, edge_type="calls", line=1)]

        entrypoints = detect_entrypoints(nodes, edges)

        # All 10 main functions should be returned
        main_eps = [ep for ep in entrypoints if "main" in ep.symbol_id]
        assert len(main_eps) == 10, "All entrypoints should be returned regardless of connectivity"

    def test_incoming_edges_not_counted(self) -> None:
        """Only outgoing edges should affect ranking, not incoming edges."""
        main_caller = make_symbol("main", path="caller.py", language="python")
        main_callee = make_symbol("main", path="callee.py", language="python")
        other = make_symbol("other", path="other.py", language="python")

        nodes = [main_caller, main_callee, other]

        # main_caller calls main_callee (main_callee has incoming edge, not outgoing)
        # main_caller also calls other
        edges = [
            Edge.create(src=main_caller.id, dst=main_callee.id, edge_type="calls", line=1),
            Edge.create(src=main_caller.id, dst=other.id, edge_type="calls", line=2),
        ]

        entrypoints = detect_entrypoints(nodes, edges)
        main_eps = [ep for ep in entrypoints if "main" in ep.symbol_id]

        # main_caller (2 outgoing) should rank before main_callee (0 outgoing)
        assert main_eps[0].symbol_id == main_caller.id
        assert main_eps[1].symbol_id == main_callee.id
