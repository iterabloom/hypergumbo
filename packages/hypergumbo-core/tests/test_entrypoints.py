"""Tests for entrypoint detection heuristics."""
import pytest

from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.entrypoints import (
    detect_entrypoints,
    Entrypoint,
    EntrypointKind,
)
from hypergumbo_core.paths import is_test_file


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    language: str = "python",
    decorators: list[str] | None = None,
    meta: dict | None = None,
    supply_chain_tier: int = 1,
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
        supply_chain_tier=supply_chain_tier,
    )



class TestIsTestFile:
    """Tests for is_test_file function."""

    def test_python_test_prefix(self) -> None:
        """Detect Python test_ prefix."""
        assert is_test_file("test_main.py")
        assert is_test_file("src/test_utils.py")

    def test_python_test_suffix(self) -> None:
        """Detect Python _test.py suffix."""
        assert is_test_file("main_test.py")
        assert is_test_file("src/utils_test.py")

    def test_python_spec_patterns(self) -> None:
        """Detect Python spec patterns."""
        assert is_test_file("spec_main.py")
        assert is_test_file("main_spec.py")

    def test_go_test_suffix(self) -> None:
        """Detect Go _test.go suffix."""
        assert is_test_file("main_test.go")
        assert is_test_file("pkg/handlers/user_test.go")

    def test_mock_filename_suffix(self) -> None:
        """Detect *_mock.* filename patterns."""
        assert is_test_file("user_mock.go")
        assert is_test_file("service_mock.py")
        assert is_test_file("src/handler_mock.ts")

    def test_mock_filename_prefix(self) -> None:
        """Detect mock_*.* filename patterns."""
        assert is_test_file("src/mock_user.go")
        assert is_test_file("mock_service.py")

    def test_fake_filename_suffix(self) -> None:
        """Detect *_fake.* filename patterns."""
        assert is_test_file("user_fake.go")
        assert is_test_file("src/handler_fake.ts")

    def test_fake_filename_prefix(self) -> None:
        """Detect fake_*.* filename patterns."""
        assert is_test_file("src/fake_user.go")
        assert is_test_file("fake_handler.go")

    def test_fakes_directory(self) -> None:
        """Detect files in fakes/ directory."""
        assert is_test_file("pkg/rtc/transport/transportfakes/fake_handler.go")
        assert is_test_file("internal/fakes/mock_service.go")

    def test_mocks_directory(self) -> None:
        """Detect files in mocks/ directory."""
        assert is_test_file("pkg/mocks/user_service.go")
        assert is_test_file("src/mocks/api_client.ts")

    def test_fixtures_directory(self) -> None:
        """Detect files in fixtures/ directory."""
        assert is_test_file("tests/fixtures/sample_data.json")
        assert is_test_file("fixtures/test_user.py")

    def test_testdata_directory(self) -> None:
        """Detect files in testdata/ directory."""
        assert is_test_file("pkg/testdata/sample.txt")
        assert is_test_file("testdata/config.yaml")

    def test_testutils_directory(self) -> None:
        """Detect files in testutils/ directory."""
        assert is_test_file("pkg/testutils/helpers.go")
        assert is_test_file("testutils/factory.py")

    def test_regular_file_not_detected(self) -> None:
        """Regular source files are not detected as test files."""
        assert not is_test_file("src/main.py")
        assert not is_test_file("pkg/handlers/user.go")
        assert not is_test_file("internal/api/routes.ts")

    def test_case_insensitive_directories(self) -> None:
        """Directory matching is case-insensitive."""
        assert is_test_file("src/MOCKS/service.go")
        assert is_test_file("Fixtures/data.json")
        assert is_test_file("TESTDATA/sample.txt")

    def test_compound_directory_names(self) -> None:
        """Detect directories ending with 'fakes' or 'mocks'."""
        # These hit endswith("fakes") and endswith("mocks") specifically
        assert is_test_file("pkg/rtc/transport/transportfakes/handler.go")
        assert is_test_file("internal/servicemocks/client.go")


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

    def test_detect_controller_concept(self) -> None:
        """Symbol with controller concept is detected as controller entrypoint."""
        sym = make_symbol(
            "UsersController",
            kind="class",
            path="src/controllers/users.ts",
            meta={
                "concepts": [
                    {"concept": "controller", "framework": "nestjs"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].symbol_id == sym.id
        assert ctrl_eps[0].confidence >= 0.95
        assert "Nestjs" in ctrl_eps[0].label or "controller" in ctrl_eps[0].label.lower()

    def test_detect_task_concept(self) -> None:
        """Symbol with task concept is detected as background task entrypoint."""
        sym = make_symbol(
            "process_order",
            path="src/jobs/orders.py",
            meta={
                "concepts": [
                    {"concept": "task", "framework": "celery"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.BACKGROUND_TASK]
        assert len(task_eps) == 1
        assert task_eps[0].symbol_id == sym.id
        assert task_eps[0].confidence >= 0.95

    def test_detect_scheduled_task_concept(self) -> None:
        """Symbol with scheduled_task concept is detected as scheduled task entrypoint."""
        sym = make_symbol(
            "cleanup_expired",
            path="src/jobs/cleanup.java",
            language="java",
            meta={
                "concepts": [
                    {"concept": "scheduled_task", "framework": "spring-boot"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.SCHEDULED_TASK]
        assert len(task_eps) == 1
        assert task_eps[0].symbol_id == sym.id
        assert task_eps[0].confidence >= 0.95

    def test_detect_websocket_handler_concept(self) -> None:
        """Symbol with websocket_handler concept is detected as websocket entrypoint."""
        sym = make_symbol(
            "handle_message",
            path="src/websocket/chat.ts",
            meta={
                "concepts": [
                    {"concept": "websocket_handler", "framework": "nestjs"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ws_eps = [e for e in entrypoints if e.kind == EntrypointKind.WEBSOCKET_HANDLER]
        assert len(ws_eps) == 1
        assert ws_eps[0].symbol_id == sym.id
        assert ws_eps[0].confidence >= 0.95
        assert "WebSocket" in ws_eps[0].label

    def test_detect_websocket_gateway_concept(self) -> None:
        """Symbol with websocket_gateway concept is detected as websocket entrypoint."""
        sym = make_symbol(
            "ChatGateway",
            kind="class",
            path="src/chat/chat.gateway.ts",
            meta={
                "concepts": [
                    {"concept": "websocket_gateway", "framework": "nestjs"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ws_eps = [e for e in entrypoints if e.kind == EntrypointKind.WEBSOCKET_HANDLER]
        assert len(ws_eps) == 1
        assert ws_eps[0].confidence >= 0.95

    def test_detect_event_handler_concept(self) -> None:
        """Symbol with event_handler concept is detected as event handler entrypoint."""
        sym = make_symbol(
            "onUserCreated",
            path="src/events/user.py",
            meta={
                "concepts": [
                    {"concept": "event_handler", "framework": "django"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        event_eps = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(event_eps) == 1
        assert event_eps[0].symbol_id == sym.id
        assert event_eps[0].confidence >= 0.95

    def test_detect_command_concept(self) -> None:
        """Symbol with command concept is detected as CLI command entrypoint."""
        sym = make_symbol(
            "import_data",
            path="src/management/commands/import_data.py",
            meta={
                "concepts": [
                    {"concept": "command", "framework": "django"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cmd_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cmd_eps) == 1
        assert cmd_eps[0].symbol_id == sym.id
        assert cmd_eps[0].confidence >= 0.95

    def test_detect_liveview_concept(self) -> None:
        """Symbol with liveview concept is detected as controller entrypoint."""
        sym = make_symbol(
            "DashboardLive",
            kind="module",
            path="lib/myapp_web/live/dashboard_live.ex",
            language="elixir",
            meta={
                "concepts": [
                    {"concept": "liveview", "framework": "phoenix"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].symbol_id == sym.id
        assert ctrl_eps[0].confidence >= 0.95
        assert "LiveView" in ctrl_eps[0].label

    def test_detect_graphql_resolver_concept(self) -> None:
        """Symbol with graphql_resolver concept is detected as GraphQL entrypoint."""
        sym = make_symbol(
            "Query",
            kind="class",
            path="src/graphql/resolvers.ts",
            meta={
                "concepts": [
                    {"concept": "graphql_resolver", "framework": "apollo"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        gql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(gql_eps) == 1
        assert gql_eps[0].symbol_id == sym.id
        assert gql_eps[0].confidence >= 0.95
        assert "resolver" in gql_eps[0].label.lower()

    def test_detect_graphql_schema_concept(self) -> None:
        """Symbol with graphql_schema concept is detected as GraphQL entrypoint."""
        sym = make_symbol(
            "typeDefs",
            path="src/graphql/schema.ts",
            meta={
                "concepts": [
                    {"concept": "graphql_schema"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        gql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(gql_eps) == 1
        assert gql_eps[0].confidence >= 0.95
        assert "schema" in gql_eps[0].label.lower()

    def test_multiple_different_concepts_first_wins(self) -> None:
        """Symbol with multiple different concepts keeps only the first entrypoint.

        The detect_entrypoints() deduplication keeps one entry per symbol_id.
        This prevents duplicate entries when both semantic and path-based
        detection would match the same symbol. The first-detected entry wins.
        """
        # A symbol that is both a route AND a controller (unusual but possible)
        sym = make_symbol(
            "UserController",
            kind="class",
            path="src/api/users.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "GET"},
                    {"concept": "controller", "framework": "fastapi"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Only one entrypoint per symbol due to deduplication
        sym_eps = [e for e in entrypoints if e.symbol_id == sym.id]
        assert len(sym_eps) == 1
        # First concept (route) wins
        assert sym_eps[0].kind == EntrypointKind.HTTP_ROUTE

    def test_duplicate_concept_types_deduplicated(self) -> None:
        """Multiple concepts of the same type on one symbol produce one entrypoint."""
        # Symbol with two route concepts (e.g., multiple HTTP methods)
        sym = make_symbol(
            "user_endpoint",
            path="src/api/users.py",
            meta={
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "GET"},
                    {"concept": "route", "path": "/users", "method": "POST"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only produce one entrypoint (first one wins)
        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1

    def test_concept_without_framework_uses_generic_label(self) -> None:
        """Concept without framework info uses generic label."""
        sym = make_symbol(
            "process_job",
            path="src/jobs/worker.py",
            meta={
                "concepts": [
                    {"concept": "task"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.BACKGROUND_TASK]
        assert len(task_eps) == 1
        assert "task" in task_eps[0].label.lower()

    def test_controller_without_framework_uses_generic_label(self) -> None:
        """Controller concept without framework uses generic label."""
        sym = make_symbol(
            "UserController",
            kind="class",
            path="src/controllers/users.py",
            meta={
                "concepts": [
                    {"concept": "controller"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].label == "Controller"

    def test_scheduled_task_without_framework_uses_generic_label(self) -> None:
        """Scheduled task concept without framework uses generic label."""
        sym = make_symbol(
            "cleanup_job",
            path="src/tasks/cleanup.py",
            meta={
                "concepts": [
                    {"concept": "scheduled_task"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.SCHEDULED_TASK]
        assert len(task_eps) == 1
        assert task_eps[0].label == "Scheduled task"

    def test_websocket_handler_without_framework_uses_generic_label(self) -> None:
        """WebSocket handler concept without framework uses generic label."""
        sym = make_symbol(
            "handle_message",
            path="src/ws/handler.py",
            meta={
                "concepts": [
                    {"concept": "websocket_handler"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ws_eps = [e for e in entrypoints if e.kind == EntrypointKind.WEBSOCKET_HANDLER]
        assert len(ws_eps) == 1
        assert ws_eps[0].label == "WebSocket handler"

    def test_event_handler_without_framework_uses_generic_label(self) -> None:
        """Event handler concept without framework uses generic label."""
        sym = make_symbol(
            "on_user_created",
            path="src/events/handlers.py",
            meta={
                "concepts": [
                    {"concept": "event_handler"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        event_eps = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(event_eps) == 1
        assert event_eps[0].label == "Event handler"

    def test_command_without_framework_uses_generic_label(self) -> None:
        """Command concept without framework uses generic label."""
        sym = make_symbol(
            "migrate",
            path="src/commands/migrate.py",
            meta={
                "concepts": [
                    {"concept": "command"}  # No framework specified
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cmd_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cmd_eps) == 1
        assert cmd_eps[0].label == "CLI command"

    def test_duplicate_controller_concepts_deduplicated(self) -> None:
        """Multiple controller concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "BaseController",
            kind="class",
            path="src/controllers/base.py",
            meta={
                "concepts": [
                    {"concept": "controller", "framework": "django"},
                    {"concept": "controller", "framework": "fastapi"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1

    def test_duplicate_task_concepts_deduplicated(self) -> None:
        """Multiple task concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "process_data",
            path="src/jobs/processor.py",
            meta={
                "concepts": [
                    {"concept": "task", "framework": "celery"},
                    {"concept": "task", "framework": "rq"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.BACKGROUND_TASK]
        assert len(task_eps) == 1

    def test_duplicate_scheduled_task_concepts_deduplicated(self) -> None:
        """Multiple scheduled_task concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "daily_cleanup",
            path="src/jobs/scheduled.py",
            meta={
                "concepts": [
                    {"concept": "scheduled_task"},
                    {"concept": "scheduled_task"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        task_eps = [e for e in entrypoints if e.kind == EntrypointKind.SCHEDULED_TASK]
        assert len(task_eps) == 1

    def test_duplicate_websocket_concepts_deduplicated(self) -> None:
        """Multiple websocket concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "ChatHandler",
            kind="class",
            path="src/ws/chat.py",
            meta={
                "concepts": [
                    {"concept": "websocket_handler"},
                    {"concept": "websocket_gateway"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ws_eps = [e for e in entrypoints if e.kind == EntrypointKind.WEBSOCKET_HANDLER]
        assert len(ws_eps) == 1

    def test_duplicate_event_handler_concepts_deduplicated(self) -> None:
        """Multiple event_handler concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "handle_events",
            path="src/events/handler.py",
            meta={
                "concepts": [
                    {"concept": "event_handler", "framework": "django"},
                    {"concept": "event_handler", "framework": "celery"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        event_eps = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(event_eps) == 1

    def test_duplicate_command_concepts_deduplicated(self) -> None:
        """Multiple command concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "run_command",
            path="src/cli/commands.py",
            meta={
                "concepts": [
                    {"concept": "command"},
                    {"concept": "command"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cmd_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cmd_eps) == 1

    def test_duplicate_graphql_concepts_deduplicated(self) -> None:
        """Multiple graphql concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "Query",
            kind="class",
            path="src/graphql/query.py",
            meta={
                "concepts": [
                    {"concept": "graphql_resolver"},
                    {"concept": "graphql_schema"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        gql_eps = [e for e in entrypoints if e.kind == EntrypointKind.GRAPHQL_SERVER]
        assert len(gql_eps) == 1

    def test_liveview_and_controller_share_kind_deduplicated(self) -> None:
        """LiveView and controller map to same kind, so are deduplicated.

        Both 'liveview' and 'controller' concepts map to EntrypointKind.CONTROLLER.
        When both are present, only one CONTROLLER entrypoint is created.
        """
        sym = make_symbol(
            "DashboardLive",
            kind="module",
            path="lib/app_web/live/dashboard_live.ex",
            language="elixir",
            meta={
                "concepts": [
                    {"concept": "controller", "framework": "phoenix"},
                    {"concept": "liveview", "framework": "phoenix"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        # Both concepts map to CONTROLLER, but only one entry is created
        assert len(ctrl_eps) == 1

    def test_detect_main_function_concept(self) -> None:
        """Detect main_function concept from language convention patterns."""
        sym = make_symbol(
            "main",
            kind="function",
            path="main.go",
            language="go",
            meta={
                "concepts": [
                    {"concept": "main_function", "framework": "main-functions"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert entrypoints[0].kind == EntrypointKind.MAIN_FUNCTION
        assert entrypoints[0].confidence == 0.80  # Lower than framework patterns
        assert "Go main()" in entrypoints[0].label

    def test_main_function_without_language_uses_unknown(self) -> None:
        """main_function entrypoint uses 'Unknown' when language is not set."""
        sym = Symbol(
            id="test:main.txt:1-10:main:function",
            name="main",
            kind="function",
            language="",  # Empty language
            path="main.txt",
            span=Span(1, 10, 0, 100),
            meta={
                "concepts": [
                    {"concept": "main_function", "framework": "main-functions"}
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        assert "Unknown main()" in entrypoints[0].label

    def test_duplicate_main_function_concepts_deduplicated(self) -> None:
        """Multiple main_function concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "main",
            kind="function",
            path="main.py",
            language="python",
            meta={
                "concepts": [
                    {"concept": "main_function", "framework": "main-functions"},
                    {"concept": "main_function", "framework": "main-functions"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        main_eps = [e for e in entrypoints if e.kind == EntrypointKind.MAIN_FUNCTION]
        assert len(main_eps) == 1

    def test_detect_library_export_concept(self) -> None:
        """Detects library_export concept and creates LIBRARY_EXPORT entrypoint."""
        sym = make_symbol(
            "doSomething",
            kind="function",
            path="index.ts",
            language="typescript",
            meta={
                "concepts": [
                    {
                        "concept": "library_export",
                        "framework": "library-exports",
                        "export_name": "doSomething",
                        "is_default": "false",
                    }
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.LIBRARY_EXPORT
        assert ep.confidence == 0.75
        assert "doSomething" in ep.label

    def test_library_export_default_export(self) -> None:
        """Default exports have appropriate label."""
        sym = make_symbol(
            "Hls",
            kind="class",
            path="index.ts",
            language="typescript",
            meta={
                "concepts": [
                    {
                        "concept": "library_export",
                        "framework": "library-exports",
                        "export_name": "Hls",
                        "is_default": True,  # Note: can be bool or string
                    }
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.LIBRARY_EXPORT
        assert "default" in ep.label.lower()

    def test_duplicate_library_export_concepts_deduplicated(self) -> None:
        """Multiple library_export concepts on same symbol produce one entrypoint."""
        sym = make_symbol(
            "exportedFunc",
            kind="function",
            path="index.js",
            language="javascript",
            meta={
                "concepts": [
                    {"concept": "library_export", "framework": "library-exports"},
                    {"concept": "library_export", "framework": "library-exports"},
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        lib_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(lib_eps) == 1

    def test_npm_bin_entrypoint_detection(self) -> None:
        """npm bin entries (package.json "bin") are detected as CLI entrypoints."""
        # npm_bin concept comes from config-conventions.yaml matching kind="bin"
        sym = Symbol(
            id="json:package.json:5-5:my-cli:bin",
            name="my-cli",
            kind="bin",
            path="package.json",
            language="json",
            span=Span(5, 5, 0, 30),
            meta={"concepts": [{"concept": "npm_bin", "framework": "config-conventions"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cli_eps) == 1
        assert cli_eps[0].confidence == 0.99  # Declared in manifest - highest confidence
        assert "npm CLI" in cli_eps[0].label
        assert "my-cli" in cli_eps[0].label

    def test_cargo_binary_entrypoint_detection(self) -> None:
        """Cargo binary targets ([[bin]]) are detected as CLI entrypoints."""
        # cargo_binary concept comes from config-conventions.yaml matching kind="binary"
        sym = Symbol(
            id="toml:Cargo.toml:20-25:my-tool:binary",
            name="my-tool",
            kind="binary",
            path="Cargo.toml",
            language="toml",
            span=Span(20, 25, 0, 100),
            meta={"concepts": [{"concept": "cargo_binary", "framework": "config-conventions"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cli_eps) == 1
        assert cli_eps[0].confidence == 0.99  # Declared in manifest
        assert "Cargo binary" in cli_eps[0].label

    def test_pyproject_script_entrypoint_detection(self) -> None:
        """pyproject.toml [project.scripts] entries are detected as CLI entrypoints."""
        # pyproject_script concept will come from config-conventions.yaml
        sym = Symbol(
            id="toml:pyproject.toml:10-10:my-app:script",
            name="my-app",
            kind="script",
            path="pyproject.toml",
            language="toml",
            span=Span(10, 10, 0, 40),
            meta={"concepts": [{"concept": "pyproject_script", "framework": "config-conventions"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cli_eps) == 1
        assert cli_eps[0].confidence == 0.99  # Declared in manifest
        assert "Python CLI" in cli_eps[0].label

    def test_main_guard_entrypoint_detection(self) -> None:
        """Python modules with main guard (if __name__ == '__main__') are detected as entrypoints."""
        sym = Symbol(
            id="python:script.py:1-50:<module:script.py>:module",
            name="<module:script.py>",
            kind="module",
            path="script.py",
            language="python",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [{"concept": "main_guard", "framework": "python"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        main_eps = [e for e in entrypoints if e.kind == EntrypointKind.MAIN_FUNCTION]
        assert len(main_eps) == 1
        assert main_eps[0].confidence == 0.85  # Structural pattern
        assert "if __name__" in main_eps[0].label

    def test_main_guard_deduplicated_with_main_function(self) -> None:
        """main_guard and main_function concepts on same symbol don't create duplicates."""
        # If a symbol has both main_guard and main_function concepts, only create one entry
        sym = Symbol(
            id="python:main.py:1-50:main:function",
            name="main",
            kind="function",
            path="main.py",
            language="python",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [
                {"concept": "main_function", "framework": "python"},  # From main-functions.yaml
                {"concept": "main_guard", "framework": "python"},  # From analyzer
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only have one MAIN_FUNCTION entry (first concept wins)
        main_eps = [e for e in entrypoints if e.kind == EntrypointKind.MAIN_FUNCTION]
        assert len(main_eps) == 1
        # main_function has lower confidence (0.80) than main_guard (0.85), but it's first
        assert main_eps[0].confidence == 0.80

    def test_controller_by_name_entrypoint_detection(self) -> None:
        """Classes named *Controller are detected as entrypoints (naming heuristic)."""
        sym = Symbol(
            id="python:controllers.py:1-50:UserController:class",
            name="UserController",
            kind="class",
            path="controllers.py",
            language="python",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [{"concept": "controller_by_name", "framework": "naming-conventions"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].confidence == 0.70  # Naming heuristic - lowest tier
        assert "by name" in ctrl_eps[0].label

    def test_handler_by_name_entrypoint_detection(self) -> None:
        """Classes named *Handler are detected as entrypoints (naming heuristic)."""
        sym = Symbol(
            id="python:handlers.py:1-30:RequestHandler:class",
            name="RequestHandler",
            kind="class",
            path="handlers.py",
            language="python",
            span=Span(1, 30, 0, 0),
            meta={"concepts": [{"concept": "handler_by_name", "framework": "naming-conventions"}]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        handler_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(handler_eps) == 1
        assert handler_eps[0].confidence == 0.70  # Naming heuristic
        assert "by name" in handler_eps[0].label

    def test_naming_convention_skipped_if_framework_detected(self) -> None:
        """Naming-based detection skipped if framework detection already matched."""
        # A class that has both @Controller annotation AND is named FooController
        sym = Symbol(
            id="java:FooController.java:1-50:FooController:class",
            name="FooController",
            kind="class",
            path="FooController.java",
            language="java",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [
                {"concept": "controller", "framework": "spring-boot"},  # From annotation
                {"concept": "controller_by_name", "framework": "naming-conventions"},  # From name
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only have one CONTROLLER entry (framework detection wins)
        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].confidence == 0.95  # Framework detection, not naming

    def test_handler_by_name_skipped_if_controller_detected(self) -> None:
        """Handler naming convention skipped if controller already detected.

        This tests the deduplication when a class has both controller_by_name
        (0.70) processed first and handler_by_name (0.70) second. Since both
        map to CONTROLLER, the handler_by_name should be skipped.
        """
        # A class named FooControllerHandler - matches both patterns
        sym = Symbol(
            id="java:FooController.java:1-50:FooControllerHandler:class",
            name="FooControllerHandler",
            kind="class",
            path="FooControllerHandler.java",
            language="java",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [
                {"concept": "controller_by_name", "framework": "naming-conventions"},
                {"concept": "handler_by_name", "framework": "naming-conventions"},
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only have one CONTROLLER entry (controller_by_name processed first)
        ctrl_eps = [e for e in entrypoints if e.kind == EntrypointKind.CONTROLLER]
        assert len(ctrl_eps) == 1
        assert ctrl_eps[0].confidence == 0.70  # Naming heuristic
        assert "Controller (by name)" in ctrl_eps[0].label

    def test_symbol_with_empty_concepts_skipped(self) -> None:
        """Symbols with meta but empty concepts list are skipped."""
        sym = Symbol(
            id="test:empty:1-5:test:function",
            name="test",
            kind="function",
            path="test.py",
            language="python",
            span=Span(1, 5, 0, 30),
            meta={"concepts": []},  # Empty concepts list
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])
        assert len(entrypoints) == 0

    def test_duplicate_manifest_concepts_deduplicated(self) -> None:
        """Multiple manifest concepts on same symbol don't create duplicate entries."""
        # Unlikely in practice but tests defensive deduplication
        sym = Symbol(
            id="json:package.json:5-5:my-cli:bin",
            name="my-cli",
            kind="bin",
            path="package.json",
            language="json",
            span=Span(5, 5, 0, 30),
            meta={"concepts": [
                {"concept": "npm_bin", "framework": "config-conventions"},
                {"concept": "npm_bin", "framework": "config-conventions"},  # Duplicate
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only create one CLI_COMMAND entry despite duplicate concepts
        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cli_eps) == 1

    def test_command_and_manifest_concept_deduplicated(self) -> None:
        """Symbol with both command and npm_bin concepts gets one CLI_COMMAND entry.

        This tests the deduplication path when a command concept (0.95 confidence)
        is processed before a manifest concept (0.99 confidence). The manifest concept
        should be skipped since CLI_COMMAND is already added by command.
        """
        sym = Symbol(
            id="json:package.json:5-5:my-cli:bin",
            name="my-cli",
            kind="bin",
            path="package.json",
            language="json",
            span=Span(5, 5, 0, 30),
            meta={"concepts": [
                # command processed first (0.95) - e.g., Click/Typer command
                {"concept": "command", "framework": "click"},
                # npm_bin processed second, should be skipped (0.99)
                {"concept": "npm_bin", "framework": "config-conventions"},
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Should only create one CLI_COMMAND entry (from command, not npm_bin)
        cli_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cli_eps) == 1
        # The one that was created should be the command one (0.95)
        assert cli_eps[0].confidence == 0.95
        assert "Click command" in cli_eps[0].label


class TestConnectivityBasedRanking:
    """Tests for connectivity-based entrypoint ranking."""

    def test_entrypoints_sorted_by_connectivity(self) -> None:
        """Entrypoints with edges rank higher than those without."""
        # Create three route handlers with same base confidence
        route_concepts = {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]}
        route_a = make_symbol("route_a", path="a.py", language="python", meta=route_concepts)
        route_b = make_symbol("route_b", path="b.py", language="python", meta=route_concepts)
        route_c = make_symbol("route_c", path="c.py", language="python", meta=route_concepts)

        # Create helper functions that route_b and route_c call
        helper1 = make_symbol("helper1", path="helpers.py", language="python")
        helper2 = make_symbol("helper2", path="helpers.py", language="python", start_line=10, end_line=15)
        helper3 = make_symbol("helper3", path="helpers.py", language="python", start_line=20, end_line=25)

        nodes = [route_a, route_b, route_c, helper1, helper2, helper3]

        # route_a calls nothing (0 edges)
        # route_b calls 1 helper (1 edge)
        # route_c calls 3 helpers (3 edges)
        edges = [
            Edge.create(src=route_b.id, dst=helper1.id, edge_type="calls", line=2),
            Edge.create(src=route_c.id, dst=helper1.id, edge_type="calls", line=2),
            Edge.create(src=route_c.id, dst=helper2.id, edge_type="calls", line=3),
            Edge.create(src=route_c.id, dst=helper3.id, edge_type="calls", line=4),
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        # Should find all three routes
        route_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 3

        # Routes with edges should rank before route without edges
        # Note: route_b and route_c both hit the 1.0 confidence cap, so they tie
        # The key behavior is that route_a (no edges) is last
        assert route_eps[2].symbol_id == route_a.id, "route_a with 0 edges should rank last"
        # Both route_b and route_c should have higher confidence than route_a
        assert route_eps[0].confidence >= route_eps[2].confidence
        assert route_eps[1].confidence >= route_eps[2].confidence

    def test_connectivity_boost_increases_confidence(self) -> None:
        """Entrypoints with more edges should have higher confidence scores."""
        route_concepts = {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]}
        route_isolated = make_symbol("route_isolated", path="isolated.py", language="python", meta=route_concepts)
        route_connected = make_symbol("route_connected", path="connected.py", language="python", meta=route_concepts)
        helper = make_symbol("helper", path="helper.py", language="python")

        nodes = [route_isolated, route_connected, helper]

        # route_connected calls helper multiple times (simulated by multiple edges)
        edges = [
            Edge.create(src=route_connected.id, dst=helper.id, edge_type="calls", line=i)
            for i in range(10)  # 10 outgoing edges
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        route_eps = {ep.symbol_id: ep for ep in entrypoints if ep.kind == EntrypointKind.HTTP_ROUTE}

        # Connected route should have higher confidence than isolated one
        assert route_eps[route_connected.id].confidence > route_eps[route_isolated.id].confidence

    def test_all_entrypoints_still_returned(self) -> None:
        """Connectivity ranking should not filter out any entrypoints."""
        # Create many route handlers with concept metadata
        route_concepts = {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]}
        routes = [
            make_symbol(f"route_{i}", path=f"file{i}.py", language="python", start_line=i, meta=route_concepts)
            for i in range(10)
        ]
        helper = make_symbol("helper", path="helper.py", language="python")

        nodes = routes + [helper]

        # Only first route has edges
        edges = [Edge.create(src=routes[0].id, dst=helper.id, edge_type="calls", line=1)]

        entrypoints = detect_entrypoints(nodes, edges)

        # All 10 routes should be returned
        route_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 10, "All entrypoints should be returned regardless of connectivity"

    def test_incoming_edges_not_counted(self) -> None:
        """Only outgoing edges should affect ranking, not incoming edges."""
        route_concepts = {"concepts": [{"concept": "route", "method": "GET", "path": "/test"}]}
        route_caller = make_symbol("route_caller", path="caller.py", language="python", meta=route_concepts)
        route_callee = make_symbol("route_callee", path="callee.py", language="python", meta=route_concepts)
        other = make_symbol("other", path="other.py", language="python")

        nodes = [route_caller, route_callee, other]

        # route_caller calls route_callee (route_callee has incoming edge, not outgoing)
        # route_caller also calls other
        edges = [
            Edge.create(src=route_caller.id, dst=route_callee.id, edge_type="calls", line=1),
            Edge.create(src=route_caller.id, dst=other.id, edge_type="calls", line=2),
        ]

        entrypoints = detect_entrypoints(nodes, edges)
        route_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.HTTP_ROUTE]

        # route_caller (2 outgoing) should rank before route_callee (0 outgoing)
        assert route_eps[0].symbol_id == route_caller.id
        assert route_eps[1].symbol_id == route_callee.id


class TestLifecycleHookConceptDetection:
    """Tests for lifecycle_hook concept-based entrypoint detection (ADR-0003 v1.1.x).

    The lifecycle_hook concept is used by android.yaml to match Android lifecycle
    methods like Activity.onCreate(), Application.onCreate(), etc.
    """

    def test_detect_android_activity_from_concept(self) -> None:
        """lifecycle_hook concept with Activity base creates ANDROID_ACTIVITY entrypoint."""
        symbol = make_symbol(
            name="MainActivity.onCreate",
            path="MainActivity.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "AppCompatActivity",
                        "matched_method_name": "onCreate",
                    }
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.ANDROID_ACTIVITY
        assert ep.confidence == 0.95
        assert "MainActivity" in ep.label

    def test_detect_android_application_from_concept(self) -> None:
        """lifecycle_hook concept with Application base creates ANDROID_APPLICATION entrypoint."""
        symbol = make_symbol(
            name="MyApp.onCreate",
            path="MyApp.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "Application",
                        "matched_method_name": "onCreate",
                    }
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.ANDROID_APPLICATION
        assert ep.confidence == 0.95
        assert "MyApp" in ep.label

    def test_detect_android_fragment_from_concept(self) -> None:
        """lifecycle_hook concept with Fragment base creates CONTROLLER entrypoint."""
        symbol = make_symbol(
            name="HomeFragment.onCreate",
            path="HomeFragment.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "Fragment",
                        "matched_method_name": "onCreate",
                    }
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.CONTROLLER
        assert ep.confidence == 0.95
        assert "Fragment" in ep.label

    def test_detect_android_service_from_concept(self) -> None:
        """lifecycle_hook concept with Service base creates CONTROLLER entrypoint."""
        symbol = make_symbol(
            name="BackgroundService.onCreate",
            path="BackgroundService.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "Service",
                        "matched_method_name": "onCreate",
                    }
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.CONTROLLER
        assert "Service" in ep.label

    def test_detect_android_broadcast_receiver_from_concept(self) -> None:
        """lifecycle_hook concept with BroadcastReceiver base creates CONTROLLER entrypoint."""
        symbol = make_symbol(
            name="PushReceiver.onReceive",
            path="PushReceiver.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "BroadcastReceiver",
                        "matched_method_name": "onReceive",
                    }
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        assert len(entrypoints) == 1
        ep = entrypoints[0]
        assert ep.kind == EntrypointKind.CONTROLLER
        assert "BroadcastReceiver" in ep.label

    def test_no_duplicate_activity_entrypoints(self) -> None:
        """Multiple lifecycle_hook concepts on same symbol don't create duplicates."""
        symbol = make_symbol(
            name="MainActivity.onCreate",
            path="MainActivity.java",
            language="java",
            kind="method",
            meta={
                "concepts": [
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "Activity",
                        "matched_method_name": "onCreate",
                    },
                    # Duplicate concept entry (shouldn't happen, but handle gracefully)
                    {
                        "concept": "lifecycle_hook",
                        "framework": "android",
                        "matched_parent_base_class": "Activity",
                        "matched_method_name": "onCreate",
                    },
                ]
            },
        )

        entrypoints = detect_entrypoints([symbol], [])

        # Should only create one entrypoint
        activity_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.ANDROID_ACTIVITY]
        assert len(activity_eps) == 1


class TestEntrypointSerialization:
    """Tests for Entrypoint serialization methods."""

    def test_to_dict(self) -> None:
        """Entrypoint.to_dict() returns correct dictionary structure."""
        ep = Entrypoint(
            symbol_id="python:app.py:1-5:handler:function",
            kind=EntrypointKind.HTTP_ROUTE,
            confidence=0.95,
            label="HTTP GET /users",
        )

        result = ep.to_dict()

        assert result == {
            "symbol_id": "python:app.py:1-5:handler:function",
            "kind": "http_route",
            "confidence": 0.95,
            "label": "HTTP GET /users",
        }

    def test_to_dict_all_kinds(self) -> None:
        """to_dict() correctly serializes all EntrypointKind values."""
        for kind in EntrypointKind:
            ep = Entrypoint(
                symbol_id="test:id",
                kind=kind,
                confidence=0.9,
                label="Test",
            )
            result = ep.to_dict()
            assert result["kind"] == kind.value


class TestEntrypointRankingPenalties:
    """Tests for test/vendor penalty-based ranking.

    Entrypoints in test files or vendor code should be deprioritized
    (lower confidence) rather than excluded entirely. This ensures:
    - Production code entrypoints rank higher by default
    - Test/vendor entrypoints are still discoverable if needed
    - The full graph data is preserved
    """

    def test_test_file_penalty(self) -> None:
        """Entrypoints in test files receive a 50% confidence penalty."""
        # Production main function
        prod_main = make_symbol(
            "main",
            path="src/app.py",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        # Test main function
        test_main = make_symbol(
            "main",
            path="tests/test_app.py",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        nodes = [prod_main, test_main]

        entrypoints = detect_entrypoints(nodes, [])

        # Both should be detected
        assert len(entrypoints) == 2

        # Production main should have higher confidence
        prod_ep = next(e for e in entrypoints if "src/app.py" in e.symbol_id)
        test_ep = next(e for e in entrypoints if "tests/test_app.py" in e.symbol_id)

        # Base confidence is 0.80 for main_function
        # Test file gets 50% penalty: 0.80 * 0.5 = 0.40
        assert prod_ep.confidence == pytest.approx(0.80, rel=0.01)
        assert test_ep.confidence == pytest.approx(0.40, rel=0.01)

        # Production should rank first
        assert entrypoints[0].symbol_id == prod_main.id

    def test_vendor_tier_penalty(self) -> None:
        """Entrypoints in vendor code (tier >= 3) receive a 70% penalty."""
        # First-party main function (tier 1)
        first_party = make_symbol(
            "main",
            path="src/main.go",
            language="go",
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=1,
        )
        # External dependency main function (tier 3)
        vendor = make_symbol(
            "main",
            path="vendor/github.com/lib/main.go",
            language="go",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=3,
        )
        nodes = [first_party, vendor]

        entrypoints = detect_entrypoints(nodes, [])

        # Both should be detected
        assert len(entrypoints) == 2

        # First-party should have higher confidence
        fp_ep = next(e for e in entrypoints if "src/main.go" in e.symbol_id)
        vendor_ep = next(e for e in entrypoints if "vendor/" in e.symbol_id)

        # Base confidence is 0.80 for main_function
        # Vendor gets 70% penalty: 0.80 * 0.3 = 0.24
        assert fp_ep.confidence == pytest.approx(0.80, rel=0.01)
        assert vendor_ep.confidence == pytest.approx(0.24, rel=0.01)

        # First-party should rank first
        assert entrypoints[0].symbol_id == first_party.id

    def test_test_and_vendor_penalties_stack(self) -> None:
        """Both penalties apply if entrypoint is in test file AND vendor code."""
        # First-party production code
        prod = make_symbol(
            "main",
            path="src/main.py",
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=1,
        )
        # Vendor test file (both penalties)
        vendor_test = make_symbol(
            "main",
            path="vendor/lib/tests/test_main.py",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=3,
        )
        nodes = [prod, vendor_test]

        entrypoints = detect_entrypoints(nodes, [])

        prod_ep = next(e for e in entrypoints if "src/main.py" in e.symbol_id)
        vendor_test_ep = next(e for e in entrypoints if "vendor/" in e.symbol_id)

        # Base 0.80 * 0.5 (test penalty) * 0.3 (vendor penalty) = 0.12
        assert prod_ep.confidence == pytest.approx(0.80, rel=0.01)
        assert vendor_test_ep.confidence == pytest.approx(0.12, rel=0.01)

    def test_http_route_test_penalty(self) -> None:
        """HTTP routes in test files also receive test penalty."""
        # Production route
        prod_route = make_symbol(
            "get_users",
            path="src/api/routes.py",
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]},
        )
        # Test route (e.g., mock endpoint in tests)
        test_route = make_symbol(
            "mock_get_users",
            path="tests/conftest.py",
            start_line=10,
            meta={"concepts": [{"concept": "route", "path": "/test/users", "method": "GET"}]},
        )
        nodes = [prod_route, test_route]

        entrypoints = detect_entrypoints(nodes, [])

        prod_ep = next(e for e in entrypoints if "src/api" in e.symbol_id)
        test_ep = next(e for e in entrypoints if "tests/" in e.symbol_id)

        # Base confidence is 0.95 for HTTP routes
        # Test file gets 50% penalty: 0.95 * 0.5 = 0.475
        assert prod_ep.confidence == pytest.approx(0.95, rel=0.01)
        assert test_ep.confidence == pytest.approx(0.475, rel=0.01)

        # Production route should rank first
        assert entrypoints[0].symbol_id == prod_route.id

    def test_derived_artifact_penalty(self) -> None:
        """Entrypoints in derived artifacts (tier 4) also receive vendor penalty."""
        # First-party source
        source = make_symbol(
            "main",
            path="src/main.ts",
            language="typescript",
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=1,
        )
        # Transpiled output (tier 4)
        derived = make_symbol(
            "main",
            path="dist/main.js",
            language="javascript",
            start_line=1,
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=4,
        )
        nodes = [source, derived]

        entrypoints = detect_entrypoints(nodes, [])

        source_ep = next(e for e in entrypoints if "src/main.ts" in e.symbol_id)
        derived_ep = next(e for e in entrypoints if "dist/main.js" in e.symbol_id)

        # Derived (tier 4) should also get the vendor penalty (tier >= 3)
        assert source_ep.confidence > derived_ep.confidence
        assert derived_ep.confidence == pytest.approx(0.80 * 0.3, rel=0.01)

    def test_connectivity_boost_still_applies_after_penalty(self) -> None:
        """Connectivity boost is applied after penalties."""
        # Test file main with high connectivity
        test_main = make_symbol(
            "main",
            path="tests/test_main.py",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        # Create edges to give it connectivity
        edges = [
            Edge.create(
                src=test_main.id,
                dst=f"python:tests/helper.py:{i}-{i+1}:func{i}:function",
                edge_type="calls",
                line=i,
                origin="test",
                origin_run_id="test",
            )
            for i in range(10)  # 10 outgoing edges
        ]
        nodes = [test_main]

        entrypoints = detect_entrypoints(nodes, edges)

        assert len(entrypoints) == 1
        ep = entrypoints[0]

        # Base 0.80 * 0.5 (test penalty) = 0.40
        # Plus connectivity boost: log(1 + 10) / 10 ≈ 0.24
        # Total: 0.40 + 0.24 = 0.64 (capped at 0.25 boost)
        # Actually: 0.40 + min(0.25, log(11)/10) = 0.40 + 0.24 = 0.64
        assert ep.confidence > 0.40  # Should be boosted
        assert ep.confidence < 0.80  # But still penalized

    def test_ranking_order_respects_penalties(self) -> None:
        """Final ranking correctly orders by penalized confidence."""
        # High-confidence production route
        prod_route = make_symbol(
            "api_handler",
            path="src/api.py",
            meta={"concepts": [{"concept": "route", "path": "/api", "method": "GET"}]},
            supply_chain_tier=1,
        )
        # Low-confidence test main
        test_main = make_symbol(
            "main",
            path="tests/test_main.py",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
            supply_chain_tier=1,
        )
        # Medium-confidence vendor route
        vendor_route = make_symbol(
            "health_check",
            path="vendor/lib/health.py",
            start_line=20,
            meta={"concepts": [{"concept": "route", "path": "/health", "method": "GET"}]},
            supply_chain_tier=3,
        )
        nodes = [test_main, vendor_route, prod_route]  # Intentionally scrambled

        entrypoints = detect_entrypoints(nodes, [])

        # Expected order after penalties:
        # 1. prod_route: 0.95 (no penalty)
        # 2. test_main: 0.80 * 0.5 = 0.40
        # 3. vendor_route: 0.95 * 0.3 = 0.285
        assert len(entrypoints) == 3
        assert entrypoints[0].symbol_id == prod_route.id
        assert entrypoints[1].symbol_id == test_main.id
        assert entrypoints[2].symbol_id == vendor_route.id
