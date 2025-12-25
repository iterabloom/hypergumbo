"""Tests for entrypoint detection heuristics."""
import pytest

from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.entrypoints import (
    detect_entrypoints,
    Entrypoint,
    EntrypointKind,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    language: str = "python",
    decorators: list[str] | None = None,
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
        from hypergumbo.ir import Edge

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
        from hypergumbo.ir import Edge

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
        from hypergumbo.ir import Edge

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
        from hypergumbo.ir import Edge

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
        from hypergumbo.ir import Edge

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
        from hypergumbo.ir import Edge

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
