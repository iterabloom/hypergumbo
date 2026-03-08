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

    def test_elixir_phoenix_liveview_route(self) -> None:
        """Phoenix LiveView routes link to the module's mount callback."""
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:20-20:LIVE /dashboard:route",
            name="LIVE /dashboard",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=50),
            meta={
                "http_method": "LIVE",
                "route_path": "/dashboard",
                "controller": "DashboardLive",
                "action": "mount",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="elixir:/lib/app_web/live/dashboard_live.ex:10-20:DashboardLive.mount:function",
            name="DashboardLive.mount",
            kind="function",
            language="elixir",
            path="/lib/app_web/live/dashboard_live.ex",
            span=Span(start_line=10, end_line=20, start_col=2, end_col=5),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"

    def test_liveview_module_fallback(self) -> None:
        """LIVE route falls back to LiveView module when action function missing.

        LiveView modules handle actions via handle_params/3, not separate
        per-action functions. When LIVE route has action="page" but no
        HomeLive.page function exists, resolve to the HomeLive module.
        """
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:5-5:LIVE /:route",
            name="LIVE /",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=40),
            meta={
                "http_method": "LIVE",
                "route_path": "/",
                "controller": "HomeLive",
                "action": "page",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        module = Symbol(
            id="elixir:/lib/app_web/live/home_live.ex:1-50:AppWeb.HomeLive:module",
            name="AppWeb.HomeLive",
            kind="module",
            language="elixir",
            path="/lib/app_web/live/home_live.ex",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, module], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == module.id
        assert edge.edge_type == "routes_to"

    def test_liveview_exact_module_name_match(self) -> None:
        """LIVE route resolves when controller exactly matches module name."""
        route = Symbol(
            id="elixir:/lib/router.ex:5-5:LIVE /admin:route",
            name="LIVE /admin",
            kind="route",
            language="elixir",
            path="/lib/router.ex",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=40),
            meta={
                "http_method": "LIVE",
                "route_path": "/admin",
                "controller": "AdminLive",
                "action": "index",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        module = Symbol(
            id="elixir:/lib/admin_live.ex:1-30:AdminLive:module",
            name="AdminLive",
            kind="module",
            language="elixir",
            path="/lib/admin_live.ex",
            span=Span(start_line=1, end_line=30, start_col=0, end_col=3),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, module], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == module.id

    def test_liveview_prefers_function_over_module(self) -> None:
        """LIVE route prefers action function when it exists."""
        route = Symbol(
            id="elixir:/lib/app_web/router.ex:5-5:LIVE /dash:route",
            name="LIVE /dash",
            kind="route",
            language="elixir",
            path="/lib/app_web/router.ex",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=40),
            meta={
                "http_method": "LIVE",
                "route_path": "/dash",
                "controller": "DashLive",
                "action": "mount",
            },
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        mount_func = Symbol(
            id="elixir:/lib/app_web/live/dash_live.ex:10-20:DashLive.mount:function",
            name="DashLive.mount",
            kind="function",
            language="elixir",
            path="/lib/app_web/live/dash_live.ex",
            span=Span(start_line=10, end_line=20, start_col=2, end_col=5),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        module = Symbol(
            id="elixir:/lib/app_web/live/dash_live.ex:1-50:DashLive:module",
            name="DashLive",
            kind="module",
            language="elixir",
            path="/lib/app_web/live/dash_live.ex",
            span=Span(start_line=1, end_line=50, start_col=0, end_col=3),
            origin="elixir-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, mount_func, module], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        # Should prefer the mount function, not the module
        assert edge.dst == mount_func.id

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

    def test_rails_deeply_namespaced_controller(self) -> None:
        """Rails routes with short names link to deeply namespaced controllers.

        In real Rails apps like Chatwoot, routes use short controller names
        (e.g., 'users#index') but actual controller methods have full namespaces
        (e.g., 'Api::V1::Accounts::UsersController#index'). The linker should
        use suffix matching to resolve these.
        """
        route = Symbol(
            id="ruby:/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Controller method with deep namespace
        handler = Symbol(
            id="ruby:/app/controllers/api/v1/accounts/users_controller.rb:15-20:Api::V1::Accounts::UsersController#index:method",
            name="Api::V1::Accounts::UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/api/v1/accounts/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"class": "Api::V1::Accounts::UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_suffix_match_prefers_exact(self) -> None:
        """When both exact match and suffix match exist, prefer exact."""
        route = Symbol(
            id="ruby:/config/routes.rb:10-10:GET /users:route",
            name="GET /users",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={"controller_action": "users#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Exact match
        exact_handler = Symbol(
            id="ruby:/app/controllers/users_controller.rb:5-10:UsersController#index:method",
            name="UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/users_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Suffix match (deeper namespace)
        namespaced_handler = Symbol(
            id="ruby:/app/controllers/api/users_controller.rb:15-20:Api::UsersController#index:method",
            name="Api::UsersController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/api/users_controller.rb",
            span=Span(start_line=15, end_line=20, start_col=2, end_col=5),
            meta={"class": "Api::UsersController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, exact_handler, namespaced_handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == exact_handler.id

    def test_rails_acronym_controller_resolution(self) -> None:
        """Rails controllers with acronym words (IP, HTTP, SMTP, API) are resolved.

        Rails inflector treats certain words as acronyms: 'ip_pool_rules' becomes
        'IPPoolRulesController', not 'IpPoolRulesController'.  The linker should
        handle this via case-insensitive fallback.
        """
        route = Symbol(
            id="ruby:/config/routes.rb:30-30:GET /ip_pool_rules:route",
            name="GET /ip_pool_rules",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=30, end_line=30, start_col=0, end_col=60),
            meta={"controller_action": "ip_pool_rules#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Actual controller uses Rails acronym inflection (IP not Ip)
        handler = Symbol(
            id="ruby:/app/controllers/ip_pool_rules_controller.rb:5-10:IPPoolRulesController#index:method",
            name="IPPoolRulesController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/ip_pool_rules_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "IPPoolRulesController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_acronym_http_endpoints_controller(self) -> None:
        """HTTPEndpointsController (not HttpEndpointsController) is resolved."""
        route = Symbol(
            id="ruby:/config/routes.rb:28-28:GET /http_endpoints:route",
            name="GET /http_endpoints",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=28, end_line=28, start_col=0, end_col=60),
            meta={"controller_action": "http_endpoints#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/http_endpoints_controller.rb:5-10:HTTPEndpointsController#index:method",
            name="HTTPEndpointsController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/http_endpoints_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "HTTPEndpointsController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_acronym_with_namespace(self) -> None:
        """Acronym controllers under a namespace are resolved."""
        route = Symbol(
            id="ruby:/config/routes.rb:50-50:GET /api/v1/ip_addresses:route",
            name="GET /api/v1/ip_addresses",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=50, end_line=50, start_col=0, end_col=60),
            meta={"controller_action": "api/v1/ip_addresses#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/api/v1/ip_addresses_controller.rb:5-10:Api::V1::IPAddressesController#index:method",
            name="Api::V1::IPAddressesController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/api/v1/ip_addresses_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "Api::V1::IPAddressesController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_acronym_deep_namespace_suffix(self) -> None:
        """Acronym controllers match via case-insensitive suffix in deep namespaces.

        Route says 'ip_pools#index' → normalized 'IpPoolsController', but the
        actual symbol is 'Admin::IPPoolsController#index'.  Neither exact match
        nor the case-sensitive suffix match works; the case-insensitive suffix
        match resolves it.
        """
        route = Symbol(
            id="ruby:/config/routes.rb:60-60:GET /ip_pools:route",
            name="GET /ip_pools",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=60, end_line=60, start_col=0, end_col=60),
            meta={"controller_action": "ip_pools#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="ruby:/app/controllers/admin/ip_pools_controller.rb:5-10:Admin::IPPoolsController#index:method",
            name="Admin::IPPoolsController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/admin/ip_pools_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "Admin::IPPoolsController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_acronym_skips_wrong_action(self) -> None:
        """Case-insensitive fallback skips symbols whose action does not match."""
        route = Symbol(
            id="ruby:/config/routes.rb:30-30:GET /ip_pools:route",
            name="GET /ip_pools",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=30, end_line=30, start_col=0, end_col=60),
            meta={"controller_action": "ip_pools#index"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Handler has the right controller but wrong action
        wrong_action = Symbol(
            id="ruby:/app/controllers/ip_pools_controller.rb:5-10:IPPoolsController#show:method",
            name="IPPoolsController#show",
            kind="method",
            language="ruby",
            path="/app/controllers/ip_pools_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "IPPoolsController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, wrong_action], [])
        assert len(result.edges) == 0

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

    def test_direct_id_handler_ref_linking(self) -> None:
        """Routes with handler_ref as a full symbol ID resolve directly.

        Flask-RESTful emits handler_ref as a full symbol ID like
        'python:/path:line:Class:class'. This should match the handler
        symbol by ID, not by name.
        """
        handler_id = (
            "python:/app/resources.py:10-20:UserResource:class"
        )
        route = Symbol(
            id="python:/app/api.py:5-5:GET /users:route",
            name="GET /users",
            kind="route",
            language="python",
            path="/app/api.py",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=40),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_ref": handler_id,
                "view_name": "UserResource",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id=handler_id,
            name="UserResource",
            kind="class",
            language="python",
            path="/app/resources.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"

    def test_direct_id_handler_ref_not_found(self) -> None:
        """Routes with handler_ref as symbol ID fall back gracefully.

        When handler_ref is a full ID but doesn't match any symbol,
        the linker should not crash and should produce no edge.
        """
        route = Symbol(
            id="python:/app/api.py:5-5:GET /foo:route",
            name="GET /foo",
            kind="route",
            language="python",
            path="/app/api.py",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=30),
            meta={
                "http_method": "GET",
                "route_path": "/foo",
                "handler_ref": "python:/app/missing.py:1-1:Gone:class",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route], [])

        assert len(result.edges) == 0


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


class TestDjangoViewNameLinking:
    """Tests for Django route-handler linking via view_name metadata."""

    def test_django_view_name_simple_linking(self) -> None:
        """Django routes with view_name get linked to view functions."""
        route = Symbol(
            id="python:/app/urls.py:10-10:GET /users/:route",
            name="GET /users/",
            kind="route",
            language="python",
            path="/app/urls.py",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users/",
                "view_name": "list_users",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="python:/app/views.py:20-30:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="/app/views.py",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"
        assert edge.meta.get("view_name") == "list_users"

    def test_django_view_name_class_based_view(self) -> None:
        """Django routes with CBV view_name get linked to view classes."""
        route = Symbol(
            id="python:/app/urls.py:15-15:GET /api/users/:route",
            name="GET /api/users/",
            kind="route",
            language="python",
            path="/app/urls.py",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=60),
            meta={
                "http_method": "GET",
                "route_path": "/api/users/",
                "view_name": "UserListView",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        # CBV class
        handler = Symbol(
            id="python:/app/views.py:50-70:UserListView:class",
            name="UserListView",
            kind="class",
            language="python",
            path="/app/views.py",
            span=Span(start_line=50, end_line=70, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.dst == handler.id

    def test_django_view_name_module_qualified(self) -> None:
        """Django routes with module.view_name pattern."""
        route = Symbol(
            id="python:/app/urls.py:20-20:GET /accounts/:route",
            name="GET /accounts/",
            kind="route",
            language="python",
            path="/app/urls.py",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "view_name": "accounts.views.list_accounts",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        # Function with simple name (should match last segment)
        handler = Symbol(
            id="python:/app/accounts/views.py:10-20:list_accounts:function",
            name="list_accounts",
            kind="function",
            language="python",
            path="/app/accounts/views.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1

    def test_django_view_name_no_match(self) -> None:
        """Django routes without matching view don't create edges."""
        route = Symbol(
            id="python:/app/urls.py:10-10:GET /users/:route",
            name="GET /users/",
            kind="route",
            language="python",
            path="/app/urls.py",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "view_name": "nonexistent_view",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        # Handler with different name
        handler = Symbol(
            id="python:/app/views.py:20-30:some_other_view:function",
            name="some_other_view",
            kind="function",
            language="python",
            path="/app/views.py",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 0


class TestGoRouteHandlerLinking:
    """Tests for Go/Gin route-handler linking via handler_name metadata."""

    def test_go_gin_simple_handler_linking(self) -> None:
        """Go/Gin routes with handler_name metadata get linked to handler functions."""
        route = Symbol(
            id="go:/app/main.go:10-10:listUsers:route",
            name="listUsers",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_name": "listUsers",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="go:/app/main.go:20-30:listUsers:function",
            name="listUsers",
            kind="function",
            language="go",
            path="/app/main.go",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"
        assert edge.meta["handler_name"] == "listUsers"

    def test_go_gin_qualified_handler_linking(self) -> None:
        """Go routes with package-qualified handler names get linked."""
        route = Symbol(
            id="go:/app/main.go:15-15:handlers.GetAPI:route",
            name="handlers.GetAPI",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/api",
                "handler_name": "handlers.GetAPI",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="go:/app/handlers/api.go:5-15:GetAPI:function",
            name="GetAPI",
            kind="function",
            language="go",
            path="/app/handlers/api.go",
            span=Span(start_line=5, end_line=15, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.dst == handler.id

    def test_go_gin_multiple_routes_linking(self) -> None:
        """Multiple Go routes linking to different handlers."""
        route1 = Symbol(
            id="go:/app/main.go:10-10:listUsers:route",
            name="listUsers",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_name": "listUsers",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        route2 = Symbol(
            id="go:/app/main.go:11-11:createUser:route",
            name="createUser",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=11, end_line=11, start_col=0, end_col=50),
            meta={
                "http_method": "POST",
                "route_path": "/users",
                "handler_name": "createUser",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        handler1 = Symbol(
            id="go:/app/main.go:20-30:listUsers:function",
            name="listUsers",
            kind="function",
            language="go",
            path="/app/main.go",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        handler2 = Symbol(
            id="go:/app/main.go:35-45:createUser:function",
            name="createUser",
            kind="function",
            language="go",
            path="/app/main.go",
            span=Span(start_line=35, end_line=45, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route1, route2, handler1, handler2], [])

        assert len(result.edges) == 2

    def test_go_gin_qualified_handler_suffix_match(self) -> None:
        """Go handler found via suffix match when simple name lookup fails."""
        route = Symbol(
            id="go:/app/main.go:15-15:controllers.GetUsers:route",
            name="controllers.GetUsers",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=15, end_line=15, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_name": "controllers.GetUsers",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        # Handler has a different qualified name that ends with .GetUsers
        handler = Symbol(
            id="go:/app/internal/controllers/users.go:5-15:internal/controllers.GetUsers:function",
            name="internal/controllers.GetUsers",
            kind="function",
            language="go",
            path="/app/internal/controllers/users.go",
            span=Span(start_line=5, end_line=15, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_go_handler_same_name_as_route_symbol(self) -> None:
        """Go handler is found even when route symbol has the same name.

        In real-world Go analysis, route symbols are created with the same name
        as their handler function (e.g., both named "ArticleCreate"). The route
        symbol is typically added to the symbol list after the function symbol.
        The linker must not let the route symbol shadow the function in the
        lookup dict.
        """
        # Handler function comes FIRST in the symbol list (as in real analysis)
        handler = Symbol(
            id="go:/app/routers.go:50-60:ArticleCreate:function",
            name="ArticleCreate",
            kind="function",
            language="go",
            path="/app/routers.go",
            span=Span(start_line=50, end_line=60, start_col=0, end_col=1),
            origin="go-v1",
            origin_run_id="test-run",
        )

        # Route symbol comes AFTER, shadows the function in a naive dict
        route = Symbol(
            id="go:/app/routers.go:30-30:ArticleCreate:route",
            name="ArticleCreate",
            kind="route",
            language="go",
            path="/app/routers.go",
            span=Span(start_line=30, end_line=30, start_col=0, end_col=50),
            meta={
                "http_method": "POST",
                "route_path": "/articles",
                "handler_name": "ArticleCreate",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        # The critical ordering: handler first, then route (real-world ordering)
        result = link_routes_to_handlers([handler, route], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id
        assert edge.edge_type == "routes_to"

    def test_express_handler_same_name_as_route_symbol(self) -> None:
        """Express handler is found even when route symbol has the same name.

        In Express/JS analysis, when a named handler function is used, the
        route symbol gets the handler's name. The resolver must still find
        the function, not the route.
        """
        handler = Symbol(
            id="javascript:/app/routes.js:20-30:getUsers:function",
            name="getUsers",
            kind="function",
            language="javascript",
            path="/app/routes.js",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=1),
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        route = Symbol(
            id="javascript:/app/routes.js:10-10:getUsers:route",
            name="getUsers",
            kind="route",
            language="javascript",
            path="/app/routes.js",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "handler_ref": "getUsers",
            },
            origin="js-ts-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([handler, route], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id

    def test_django_handler_same_name_as_route_symbol(self) -> None:
        """Django handler is found even when route symbol has a matching name.

        When Django URL patterns use view function names, the linker must
        resolve to the function, not a route symbol that might share the name.
        """
        handler = Symbol(
            id="python:/app/views.py:20-30:list_users:function",
            name="list_users",
            kind="function",
            language="python",
            path="/app/views.py",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=5),
            origin="python-v1",
            origin_run_id="test-run",
        )

        # Django route name includes "django:" prefix so it won't match,
        # but test the scenario where a route might share the view name
        route = Symbol(
            id="python:/app/urls.py:10-10:list_users:route",
            name="list_users",
            kind="route",
            language="python",
            path="/app/urls.py",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users/",
                "view_name": "list_users",
            },
            origin="python-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([handler, route], [])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == route.id
        assert edge.dst == handler.id

    def test_go_swagger_wired_handler_linking(self) -> None:
        """Go-swagger route with wired handler_name resolves via suffix match.

        After go-swagger handler wiring, routes have handler_name like
        "api.getAlertsHandler" which should resolve to "API.getAlertsHandler"
        method symbols via suffix matching on ".getAlertsHandler".
        """
        route = Symbol(
            id="go:/api/v2/restapi/operations/api.go:377-377:GET /alerts:route",
            name="api.getAlertsHandler",
            kind="route",
            language="go",
            path="/api/v2/restapi/operations/api.go",
            span=Span(start_line=377, end_line=377, start_col=0, end_col=80),
            meta={
                "http_method": "GET",
                "route_path": "/alerts",
                "handler_name": "api.getAlertsHandler",
                "handler_field": "AlertGetAlertsHandler",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="go:/api/v2/api.go:150-165:API.getAlertsHandler:method",
            name="API.getAlertsHandler",
            kind="method",
            language="go",
            path="/api/v2/api.go",
            span=Span(start_line=150, end_line=165, start_col=0, end_col=1),
            meta={"class": "API"},
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].src == route.id
        assert result.edges[0].dst == handler.id
        assert result.edges[0].edge_type == "routes_to"

    def test_go_handler_no_match(self) -> None:
        """Go routes with handler_name that doesn't match any function."""
        route = Symbol(
            id="go:/app/main.go:10-10:missingHandler:route",
            name="missingHandler",
            kind="route",
            language="go",
            path="/app/main.go",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/test",
                "handler_name": "missingHandler",
            },
            origin="go-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route], [])

        assert len(result.edges) == 0


class TestLaravelSuffixMatchFallback:
    """Tests for Laravel controller@action suffix-match resolution.

    PHP uses backslash-namespaced FQ names (App\\Http\\Controllers\\UserController)
    but routes reference short names (UserController@index). The suffix-match
    fallback allows resolution when the symbol table contains FQ names.
    """

    def test_backslash_namespace_suffix_match(self) -> None:
        """Laravel route with short name matches FQ backslash-namespaced symbol."""
        route = Symbol(
            id="php:/routes/web.php:5-5:GET /users:route",
            name="GET /users",
            kind="route",
            language="php",
            path="/routes/web.php",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "UserController@index",
            },
            origin="php-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="php:/app/Http/Controllers/UserController.php:10-20:App\\Http\\Controllers\\UserController.index:method",
            name="App\\Http\\Controllers\\UserController.index",
            kind="method",
            language="php",
            path="/app/Http/Controllers/UserController.php",
            span=Span(start_line=10, end_line=20, start_col=2, end_col=5),
            origin="php-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_dot_separator_suffix_match(self) -> None:
        """Laravel route matches when symbol uses dot separator."""
        route = Symbol(
            id="php:/routes/api.php:3-3:POST /orders:route",
            name="POST /orders",
            kind="route",
            language="php",
            path="/routes/api.php",
            span=Span(start_line=3, end_line=3, start_col=0, end_col=50),
            meta={
                "http_method": "POST",
                "route_path": "/orders",
                "controller_action": "OrderController@store",
            },
            origin="php-v1",
            origin_run_id="test-run",
        )

        handler = Symbol(
            id="php:/app/Http/Controllers/OrderController.php:15-25:Api\\OrderController.store:method",
            name="Api\\OrderController.store",
            kind="method",
            language="php",
            path="/app/Http/Controllers/OrderController.php",
            span=Span(start_line=15, end_line=25, start_col=2, end_col=5),
            origin="php-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_rails_reverse_suffix_namespaced_route_short_symbol(self) -> None:
        """Namespaced route controller_action resolves to short-named symbol.

        In Mastodon-style Rails apps, routes.rb produces controller_action
        like "api/v1/statuses#destroy" (fully namespaced), but the Ruby analyzer
        produces symbols named "StatusesController#destroy" (only the immediately
        enclosing class). The linker must reverse-suffix-match: check if the
        normalized controller class ENDS WITH the symbol's class portion.
        """
        route = Symbol(
            id="ruby:/config/routes.rb:42-42:DELETE /api/v1/statuses/:id:route",
            name="DELETE /api/v1/statuses/:id",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=42, end_line=42, start_col=0, end_col=60),
            meta={
                "http_method": "DELETE",
                "route_path": "/api/v1/statuses/:id",
                "controller_action": "api/v1/statuses#destroy",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Symbol has SHORT name — only the immediately enclosing class
        handler = Symbol(
            id="ruby:/app/controllers/api/v1/statuses_controller.rb:80-95:StatusesController#destroy:method",
            name="StatusesController#destroy",
            kind="method",
            language="ruby",
            path="/app/controllers/api/v1/statuses_controller.rb",
            span=Span(start_line=80, end_line=95, start_col=4, end_col=7),
            meta={"class": "StatusesController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].src == route.id
        assert result.edges[0].dst == handler.id
        assert result.edges[0].edge_type == "routes_to"

    def test_rails_reverse_suffix_no_false_positive(self) -> None:
        """Reverse suffix matching doesn't match partial controller names.

        "Api::V1::StatusesController" should NOT match a symbol named
        "SubscriptionStatusesController#destroy" — "StatusesController" is
        a suffix of "SubscriptionStatusesController" but not a namespace-
        boundary-separated suffix.
        """
        route = Symbol(
            id="ruby:/config/routes.rb:42-42:DELETE /api/v1/statuses/:id:route",
            name="DELETE /api/v1/statuses/:id",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=42, end_line=42, start_col=0, end_col=60),
            meta={
                "http_method": "DELETE",
                "route_path": "/api/v1/statuses/:id",
                "controller_action": "api/v1/statuses#destroy",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # "SubscriptionStatusesController" ends with "StatusesController"
        # but it's NOT the same controller — should NOT match
        wrong_handler = Symbol(
            id="ruby:/app/controllers/subscription_statuses_controller.rb:10-20:SubscriptionStatusesController#destroy:method",
            name="SubscriptionStatusesController#destroy",
            kind="method",
            language="ruby",
            path="/app/controllers/subscription_statuses_controller.rb",
            span=Span(start_line=10, end_line=20, start_col=4, end_col=7),
            meta={"class": "SubscriptionStatusesController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, wrong_handler], [])
        assert len(result.edges) == 0

    def test_rails_reverse_suffix_case_insensitive(self) -> None:
        """Reverse suffix matching works with acronym case differences.

        Route produces "api/v1/ip_addresses#index" → normalized
        "Api::V1::IpAddressesController", but the symbol uses the Rails
        acronym form "IPAddressesController#index".
        """
        route = Symbol(
            id="ruby:/config/routes.rb:50-50:GET /api/v1/ip_addresses:route",
            name="GET /api/v1/ip_addresses",
            kind="route",
            language="ruby",
            path="/config/routes.rb",
            span=Span(start_line=50, end_line=50, start_col=0, end_col=60),
            meta={
                "http_method": "GET",
                "route_path": "/api/v1/ip_addresses",
                "controller_action": "api/v1/ip_addresses#index",
            },
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        # Symbol uses Rails acronym casing
        handler = Symbol(
            id="ruby:/app/controllers/api/v1/ip_addresses_controller.rb:5-10:IPAddressesController#index:method",
            name="IPAddressesController#index",
            kind="method",
            language="ruby",
            path="/app/controllers/api/v1/ip_addresses_controller.rb",
            span=Span(start_line=5, end_line=10, start_col=2, end_col=5),
            meta={"class": "IPAddressesController"},
            origin="ruby-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])
        assert len(result.edges) == 1
        assert result.edges[0].dst == handler.id

    def test_no_false_suffix_match(self) -> None:
        """Suffix match doesn't match partial controller names."""
        route = Symbol(
            id="php:/routes/web.php:5-5:GET /users:route",
            name="GET /users",
            kind="route",
            language="php",
            path="/routes/web.php",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=50),
            meta={
                "http_method": "GET",
                "route_path": "/users",
                "controller_action": "UserController@index",
            },
            origin="php-v1",
            origin_run_id="test-run",
        )

        # "AdminUserController.index" should NOT match "UserController@index"
        handler = Symbol(
            id="php:/app/Http/Controllers/AdminUserController.php:10-20:AdminUserController.index:method",
            name="AdminUserController.index",
            kind="method",
            language="php",
            path="/app/Http/Controllers/AdminUserController.php",
            span=Span(start_line=10, end_line=20, start_col=2, end_col=5),
            origin="php-v1",
            origin_run_id="test-run",
        )

        result = link_routes_to_handlers([route, handler], [])

        assert len(result.edges) == 0

