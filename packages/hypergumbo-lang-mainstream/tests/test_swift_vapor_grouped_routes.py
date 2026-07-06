# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Vapor grouped-builder route recovery (INV-povit).

The original ``_extract_vapor_usage_contexts`` only matched a bare
``app``/``routes``/``router`` receiver directly followed by an HTTP verb, so
it silently dropped every route registered through Vapor's grouped-builder
chain — ``routes.grouped("users").get(...)``, the closure form
``routes.group("todos") { todos in todos.get(...) }``, and the
variable-bound form ``let g = routes.grouped("x"); g.get(...)`` — which is
how real RouteCollection controllers register the bulk of their endpoints
(VernissageServer: ~236 endpoints, previously EXPECTED_ROUTES_BUT_FOUND_0).

These tests exercise the structured, scope-tracking recovery through the
BUNDLED tree-sitter-swift grammar (``analyze_swift``), plus the false-positive
guards (import gate, ``.on`` verb validation, root-anchoring).
"""
from pathlib import Path

from hypergumbo_lang_mainstream.swift import analyze_swift


def _routes(result) -> set[tuple[str, str]]:
    """Return {(http_method, route_path)} from emitted route symbols."""
    return {
        (s.meta["http_method"], s.meta["route_path"])
        for s in result.symbols
        if (s.meta or {}).get("framework_role") == "route"
    }


def _write(tmp_path: Path, body: str, name: str = "routes.swift") -> None:
    (tmp_path / name).write_text(body)


class TestVaporMethodChainedGroups:
    def test_single_grouped_prefix(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.grouped("api").get("status", use: status)
}
''')
        assert ("GET", "/api/status") in _routes(analyze_swift(tmp_path))

    def test_double_grouped_prefix(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    routes.grouped("v1").grouped("users").post(use: create)
}
''')
        assert ("POST", "/v1/users") in _routes(analyze_swift(tmp_path))

    def test_middleware_group_contributes_no_path(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.grouped(UserAuth()).get("me", use: me)
    app.grouped("admin").grouped(AdminAuth()).delete(":id", use: remove)
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/me") in found
        assert ("DELETE", "/admin/:id") in found


class TestVaporClosureGroups:
    def test_closure_group(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    routes.group("todos") { todos in
        todos.get(use: index)
        todos.post(use: store)
    }
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/todos") in found
        assert ("POST", "/todos") in found

    def test_nested_closure_group(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    routes.group("todos") { todos in
        todos.get(use: index)
        todos.group(":id") { todo in
            todo.get(use: show)
            todo.delete(use: destroy)
        }
    }
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/todos") in found
        assert ("GET", "/todos/:id") in found
        assert ("DELETE", "/todos/:id") in found

    def test_closure_param_reuses_reserved_name(self, tmp_path: Path) -> None:
        # B1: the closure param shadows the reserved receiver name `routes`;
        # bindings must win over _VAPOR_RECEIVERS so the /v1 prefix applies.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    routes.group("v1") { routes in
        routes.get("users", use: list)
    }
}
''')
        assert ("GET", "/v1/users") in _routes(analyze_swift(tmp_path))


class TestVaporVariableBoundBuilders:
    def test_let_bound_group(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

struct UsersController: RouteCollection {
    func boot(routes: RoutesBuilder) throws {
        let users = routes.grouped("users")
        users.get(use: index)
        users.post(use: create)
        users.get(":id", use: show)
    }
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/users") in found
        assert ("POST", "/users") in found
        assert ("GET", "/users/:id") in found

    def test_transitive_let_binding(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    let api = routes.grouped("api")
    let users = api.grouped("users")
    users.get(use: index)
}
''')
        assert ("GET", "/api/users") in _routes(analyze_swift(tmp_path))

    def test_reassignment_updates_prefix(self, tmp_path: Path) -> None:
        # M2: `var admin` then `admin = admin.grouped("v2")` must update the prefix.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    var admin = routes.grouped("admin")
    admin = admin.grouped("v2")
    admin.post(use: create)
}
''')
        assert ("POST", "/admin/v2") in _routes(analyze_swift(tmp_path))

    def test_reassignment_to_non_builder_invalidates(self, tmp_path: Path) -> None:
        # M2 fail-safe: reassigning a builder var to a non-builder drops the
        # binding, so a later use emits nothing (never a stale/wrong path).
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    var g = routes.grouped("x")
    g = makeSomething()
    g.get("y", use: y)
}
''')
        # No route for /x/y or /x — g is no longer a builder.
        found = _routes(analyze_swift(tmp_path))
        assert not any(p.endswith("/y") for _, p in found)


class TestVaporOnMethod:
    def test_on_explicit_method(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.on(.GET, "stream", use: stream)
    app.grouped("v1").on(.POST, "upload", use: upload)
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/stream") in found
        assert ("POST", "/v1/upload") in found

    def test_on_non_http_method_not_a_route(self, tmp_path: Path) -> None:
        # B3: `.on(.stateChange)` is a generic event DSL, not an HTTP route.
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.on(.stateChange, "x", use: h)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()


class TestVaporTypedParamAndRoutesProperty:
    def test_typed_routesbuilder_param(self, tmp_path: Path) -> None:
        # M4: a helper taking a differently-named RoutesBuilder param.
        _write(tmp_path, '''
import Vapor

func setupUsers(_ builder: RoutesBuilder) {
    builder.grouped("users").get(use: index)
}
''')
        assert ("GET", "/users") in _routes(analyze_swift(tmp_path))

    def test_app_routes_property_passthrough(self, tmp_path: Path) -> None:
        # M3: `app.routes` is the Application's RoutesBuilder.
        _write(tmp_path, '''
import Vapor

func configure(_ app: Application) throws {
    app.routes.grouped("api").get("ping", use: ping)
}
''')
        assert ("GET", "/api/ping") in _routes(analyze_swift(tmp_path))


class TestVaporFalsePositiveGuards:
    def test_import_gate_blocks_non_vapor_file(self, tmp_path: Path) -> None:
        # B2: a file that does not import Vapor/Hummingbird yields no routes,
        # even if it has route-shaped calls on a reserved-looking receiver.
        _write(tmp_path, '''
import Foundation

func doStuff(_ app: Something) {
    app.get("not-a-route") { x in x }
}
''', name="Service.swift")
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_non_builder_variable_not_a_route(self, tmp_path: Path) -> None:
        # A local var that is not a builder does not fabricate routes.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    let cache = makeCache()
    cache.get("key", use: read)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_unrelated_grouped_chain_not_rooted_at_builder(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    let publishers = signal.grouped("topic")
    publishers.get(use: sink)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()


class TestVaporExistingBehaviorPreserved:
    def test_bare_receiver_still_works(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.get("hello") { req in "hi" }
}
''')
        result = analyze_swift(tmp_path)
        assert ("GET", "/hello") in _routes(result)
        # UsageContext contract preserved.
        ctxs = [c for c in result.usage_contexts if c.metadata.get("http_method") == "GET"]
        assert any(c.context_name == "app.get" and c.metadata["route_path"] == "hello"
                   for c in ctxs)

    def test_leading_slash_normalized(self, tmp_path: Path) -> None:
        # M6: a leading-slash literal must not double up.
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.get("/hello", use: hello)
}
''')
        assert ("GET", "/hello") in _routes(analyze_swift(tmp_path))


class TestVaporRoutesInBindings:
    """Routes registered inside a binding RHS must not be dropped (regression)."""

    def test_route_captured_in_let(self, tmp_path: Path) -> None:
        # The "capture the Route to configure it" idiom.
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    let route = app.get("hello", use: index)
    _ = route
}
''')
        assert ("GET", "/hello") in _routes(analyze_swift(tmp_path))

    def test_route_in_wildcard_assignment(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    _ = app.post("ping", use: ping)
}
''')
        assert ("POST", "/ping") in _routes(analyze_swift(tmp_path))

    def test_route_in_grouped_binding_rhs(self, tmp_path: Path) -> None:
        # A verb inside the RHS chain of a builder binding still registers.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    let base = routes.group("v1") { v1 in
        v1.get("health", use: health)
    }
    _ = base
}
''')
        assert ("GET", "/v1/health") in _routes(analyze_swift(tmp_path))


class TestVaporShadowedReservedNames:
    """A reserved name rebound to a non-builder must not fabricate routes."""

    def test_let_shadows_reserved_name(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func handler(req: Request) throws {
    let router = SomeCustomRouter()
    router.get("key", use: h)
    router.grouped("x").post("y", use: h2)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_assignment_shadows_reserved_name(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func handler(req: Request) throws {
    var routes = initialThing()
    routes = replacement()
    routes.get("z", use: h)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()


class TestVaporCoverageEdges:
    """Fail-safe branches: unusual shapes must never crash or fabricate routes."""

    def test_root_route_no_parens(self, tmp_path: Path) -> None:
        # A trailing-closure-only verb (no `()`) registers the root path.
        _write(tmp_path, '''
import Vapor

func routes(_ app: Application) throws {
    app.get { req in req }
}
''')
        assert ("GET", "/") in _routes(analyze_swift(tmp_path))

    def test_group_without_closure(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    routes.group("orphan")
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_group_closure_without_param(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    routes.group("x") { }
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_non_user_type_param(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

func withHandler(_ f: () -> Void) {
    f()
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_uninitialized_property(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
import Vapor

struct Config {
    let name: String
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_non_builder_receivers(self, tmp_path: Path) -> None:
        # nav-expression (non-`.routes`), chained non-group call, `self`, and a
        # literal-bound var: none resolve to a builder.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    config.settings.get("a", use: h)
    app.sorted().get("b", use: h)
    self.get("c", use: h)
    let n = 42
    n.get("d", use: h)
}
''')
        assert _routes(analyze_swift(tmp_path)) == set()

    def test_relet_shadow_and_plain_reassignment(self, tmp_path: Path) -> None:
        # Re-`let` to a non-builder drops the binding; a plain reassignment of an
        # untracked name is a no-op.
        _write(tmp_path, '''
import Vapor

func boot(routes: RoutesBuilder) throws {
    let g = routes.grouped("x")
    g.get("a", use: h)
    let g = makeOther()
    g.get("b", use: h)
    obj.field = 5
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/x/a") in found
        assert not any(p.endswith("/b") for _, p in found)


class TestVaporRealisticController:
    def test_controller_endpoint_count(self, tmp_path: Path) -> None:
        # A realistic RouteCollection controller mixing every idiom.
        _write(tmp_path, '''
import Vapor

struct AccountController: RouteCollection {
    func boot(routes: RoutesBuilder) throws {
        let accounts = routes.grouped("api").grouped("accounts")
        accounts.get(use: list)
        accounts.post(use: create)
        let secured = accounts.grouped(UserAuthenticator())
        secured.get(":id", use: read)
        secured.put(":id", use: update)
        secured.delete(":id", use: remove)
        accounts.group("search") { search in
            search.get(use: search)
            search.post("advanced", use: advanced)
        }
    }
}
''')
        found = _routes(analyze_swift(tmp_path))
        assert ("GET", "/api/accounts") in found
        assert ("POST", "/api/accounts") in found
        assert ("GET", "/api/accounts/:id") in found
        assert ("PUT", "/api/accounts/:id") in found
        assert ("DELETE", "/api/accounts/:id") in found
        assert ("GET", "/api/accounts/search") in found
        assert ("POST", "/api/accounts/search/advanced") in found
        assert len(found) >= 7
