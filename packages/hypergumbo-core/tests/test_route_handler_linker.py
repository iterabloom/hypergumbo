"""Tests for route-handler linker.

The route-handler linker creates edges from route symbols to their handler symbols
using metadata like controller_action (Rails), view_name (Django), etc.
"""

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.route_handler import (
    link_routes_to_handlers,
    link_route_handler,
    _check_routes_available,
    PASS_ID,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestRouteHandlerLinker:
    """Tests for route-handler edge creation."""

    def test_rails_controller_action_linking(self) -> None:
        """Rails routes with controller_action metadata get linked to handler methods."""
        # Route symbol with controller_action metadata
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "users#index",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler method symbol (UsersController#index)
        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"class": "UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"
        assert edge.meta["controller_action"] == "users#index"

    def test_rails_nested_controller_action(self) -> None:
        """Rails routes with namespaced controllers are linked correctly."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:20-20:GET /admin/users:route",
            name="GET /admin/users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=60),
            meta={
                "http_method": "GET",
                "route_path": "/admin/users",
                "controller_action": "admin/users#index",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler in namespaced controller
        handler = Symbol(
            id="ruby:/app/controllers/admin/users_controller.rb:10-15:Admin::UsersController#index:method",
            name="Admin::UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/admin/users_controller.rb",
            span=Span(start_line=10, end_line=15, start_col=2, end_col=5),
            meta={"class": "Admin::UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id

    def test_no_matching_handler(self) -> None:
        """Routes without matching handlers don't create edges."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "users#index",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # No handler exists
        result = link_routes_to_handlers([route], [])

        assert len(result.edges) == 0

    def test_route_without_handler_metadata(self) -> None:
        """Routes without handler metadata don't create edges."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /static:route",
            name="GET /static",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/static",
                # No controller_action
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 0

    def test_multiple_routes_same_handler(self) -> None:
        """Multiple routes can link to the same handler."""
        route1 = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        route2 = Symbol(
            id="ruby:/app/config/routes.rb:11-11:GET /people:route",
            name="GET /people",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=11, end_line=11, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},  # Same handler
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route1, route2, handler], [])

        assert len(result.edges) == 2

    def test_elixir_phoenix_controller_action(self) -> None:
        """Phoenix routes with controller/action metadata get linked."""
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:15-15:GET /users:route",
            name="GET /users",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller": "UserController",
                "action": "index",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="elixir:/lib/app_web/controllers/user_controller.ex:20-30:UserController.index:function",
            name="UserController.index",
            kind="function",
            language="elixir",
            path="/lib/app_web/controllers/user_controller.ex",
            span=Span(start_line=20, end_line=30, start_col=2, end_col=5),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id

    def test_malformed_controller_action_no_hash(self) -> None:
        """Malformed controller_action without # doesn't match."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users_index"},  # No #
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 0

    def test_rails_dot_notation_handler(self) -> None:
        """Rails handler with dot notation (Controller.action) is found."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler uses dot notation instead of #
        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController.index:method",
            name="UsersController.index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1

    def test_rails_qualified_name_lookup(self) -> None:
        """Rails handler found via qualified_name metadata."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler has simple name but qualified_name matches
        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:index:method",
            name="index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"qualified_name": "UsersController#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1

    def test_rails_action_name_with_class_metadata(self) -> None:
        """Rails handler found by action name + class metadata."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler named just "index" but with class metadata
        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:index:method",
            name="index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"class": "UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1

    def test_phoenix_namespaced_controller_with_dot(self) -> None:
        """Phoenix handler with .Controller.action pattern is found."""
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:15-15:GET /users:route",
            name="GET /users",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=50),
            meta={
                "controller": "UserController",
                "action": "index",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        # Handler with full namespace prefix (matches .UserController.index)
        handler = Symbol(
            id="elixir:/lib/app_web/controllers/user_controller.ex:20-30:AppWeb.UserController.index:function",
            name="AppWeb.UserController.index",
            kind="function",
            language="elixir",
            path="/lib/app_web/controllers/user_controller.ex",
            span=Span(start_line=20, end_line=30, start_col=2, end_col=5),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1

    def test_phoenix_namespaced_controller_no_leading_dot(self) -> None:
        """Phoenix handler with Controller.action pattern (no leading dot) is found."""
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:15-15:GET /users:route",
            name="GET /users",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=50),
            meta={
                "controller": "UserController",
                "action": "index",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        # Handler ends with UserController.index (no dot before User)
        handler = Symbol(
            id="elixir:/lib/app_web/controllers/user_controller.ex:20-30:MyUserController.index:function",
            name="MyUserController.index",
            kind="function",
            language="elixir",
            path="/lib/app_web/controllers/user_controller.ex",
            span=Span(start_line=20, end_line=30, start_col=2, end_col=5),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1

    def test_laravel_controller_action_linking(self) -> None:
        """Laravel routes with Controller@action format get linked."""
        route = Symbol(
            id="php:/routes/web.php:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="php",
            path="/routes/web.php",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "UserController@index",
            },
            origin="php-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="php:/app/Http/Controllers/UserController.php:20-30:UserController.index:method",
            name="UserController.index",
            kind="method",
            language="php",
            path="/app/Http/Controllers/UserController.php",
            span=Span(start_line=20, end_line=30, start_col=2, end_col=5),
            origin="php-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"
        assert edge.meta["controller_action"] == "UserController@index"

    def test_laravel_no_at_symbol_not_matched_as_laravel(self) -> None:
        """controller_action without @ is matched as Rails, not Laravel."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},  # Rails format, not Laravel
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Even if we have a handler with @, it won't match because Rails format is used
        handler = Symbol(
            id="php:/app/Http/Controllers/UsersController.php:20-30:UsersController@index:method",
            name="UsersController@index",
            kind="method",
            language="php",
            path="/app/Http/Controllers/UsersController.php",
            span=Span(start_line=20, end_line=30, start_col=2, end_col=5),
            origin="php-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        # No match because Rails format looks for UsersController#index, not @
        assert len(result.edges) == 0

    def test_express_handler_ref_linking(self) -> None:
        """Express routes with handler_ref metadata get linked to handler functions."""
        route = Symbol(
            id="javascript:/app/src/app.js:10-10:userController.list:route",
            name="userController.list",
            kind="route",
            language="javascript",
            path="/app/src/app.js",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_ref": "userController.list",
            },
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        # Handler function
        handler = Symbol(
            id="javascript:/app/src/userController.js:5-10:list:function",
            name="list",
            kind="function",
            language="javascript",
            path="/app/src/userController.js",
            span=Span(start_line=5, end_line=10, start_col=0, end_col=1),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"
        assert edge.meta["handler_ref"] == "userController.list"

    def test_express_exact_match_handler(self) -> None:
        """Express handler is found by exact name match when available."""
        route = Symbol(
            id="javascript:/app/src/app.js:10-10:handleRequest:route",
            name="handleRequest",
            kind="route",
            language="javascript",
            path="/app/src/app.js",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/request",
                "handler_ref": "handleRequest",  # Simple name, not qualified
            },
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="javascript:/app/src/app.js:15-20:handleRequest:function",
            name="handleRequest",
            kind="function",
            language="javascript",
            path="/app/src/app.js",
            span=Span(start_line=15, end_line=20, start_col=0, end_col=1),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_express_suffix_match_handler(self) -> None:
        """Express handler found via suffix match when direct lookup fails."""
        # Route references "api.getUser" but handler is "routes/api.getUser"
        route = Symbol(
            id="javascript:/app/src/app.js:10-10:api.getUser:route",
            name="api.getUser",
            kind="route",
            language="javascript",
            path="/app/src/app.js",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users/:id",
                "handler_ref": "api.getUser",
            },
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        # Handler has a qualified name that ends with the function name
        handler = Symbol(
            id="javascript:/app/src/routes/api.js:5-10:routes/api.getUser:function",
            name="routes/api.getUser",  # Qualified name that ends with .getUser
            kind="function",
            language="javascript",
            path="/app/src/routes/api.js",
            span=Span(start_line=5, end_line=10, start_col=0, end_col=1),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_run_is_created(self) -> None:
        """Linker creates an AnalysisRun record."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.files_analyzed > 0  # Tracks routes processed


class TestLinkerEntryPoint:
    """Tests for linker registry integration."""

    def test_check_routes_available(self) -> None:
        """_check_routes_available counts routes with handler metadata."""
        route_with_handler = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        route_without_handler = Symbol(
            id="ruby:/app/config/routes.rb:20-20:GET /static:route",
            name="GET /static",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=50),
            meta={},  # No handler metadata
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        non_route = Symbol(
            id="ruby:/app/models/user.rb:5-10:User:class",
            name="User",
            kind="class",
            language="ruby",
            path="/app/models/user.rb",
            span=Span(start_line=5, end_line=10, start_col=0, end_col=3),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        from pathlib import Path

        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            symbols=[route_with_handler, route_without_handler, non_route],
            edges=[],
        )

        count = _check_routes_available(ctx)
        assert count == 1  # Only route_with_handler has handler metadata

    def test_link_route_handler_entry_point(self) -> None:
        """link_route_handler linker entry point works via LinkerContext."""
        route = Symbol(
            id="ruby:/app/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/app/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:15-20:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        from pathlib import Path

        ctx = LinkerContext(
            repo_root=Path("/tmp"),
            symbols=[route, handler],
            edges=[],
        )

        result = link_route_handler(ctx)

        assert len(result.edges) == 1
        assert result.run is not None
