# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for entrypoint detection heuristics."""
import pytest

from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.entrypoints import (
    _diversity_cap,
    _is_frontend_file,
    compute_entrypoint_cap,
    detect_entrypoints,
    Entrypoint,
    EntrypointKind,
    BASE_ENTRYPOINT_CAP,
    MAX_ENTRYPOINT_CAP,
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

    def test_t_directory_convention(self) -> None:
        """Detect t/ as test directory (C/Perl convention).

        Used by git (t/helper/test-reach.c), Perl core (t/op/eval.t),
        and many C projects. The t/ convention is a well-known test
        directory pattern in these ecosystems.
        """
        assert is_test_file("t/helper/test-reach.c")
        assert is_test_file("t/t0000-basic.sh")
        assert is_test_file("t/op/eval.t")

    def test_t_directory_not_confused_with_src(self) -> None:
        """t/ detection should not match other single-char directories."""
        # These should NOT be test files
        assert not is_test_file("s/helper/util.c")
        assert not is_test_file("a/main.go")

    def test_hyphen_test_prefix_c_files(self) -> None:
        """Detect test-*.c filename pattern (hyphen-separated).

        Common in C projects: test-reach.c, test-path-utils.c, test-date.c.
        """
        assert is_test_file("t/helper/test-reach.c")
        assert is_test_file("test-date.c")
        assert is_test_file("src/test-parse.c")

    def test_hyphen_test_prefix_not_confused(self) -> None:
        """test- prefix detection should require word boundary."""
        # "testament.c" should NOT be a test file
        assert not is_test_file("src/testament.c")


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

    def test_go_library_export_entrypoint(self) -> None:
        """Go exported function with library_export concept becomes LIBRARY_EXPORT."""
        sym = make_symbol(
            "New",
            kind="function",
            path="gin.go",
            language="go",
            meta={
                "concepts": [
                    {
                        "concept": "library_export",
                        "framework": "library-exports",
                    }
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        lib_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(lib_eps) == 1
        assert lib_eps[0].confidence == 0.75

    def test_elixir_module_library_export_entrypoint(self) -> None:
        """Elixir module with library_export concept becomes LIBRARY_EXPORT."""
        sym = make_symbol(
            "Phoenix.Router",
            kind="module",
            path="lib/phoenix/router.ex",
            language="elixir",
            meta={
                "concepts": [
                    {
                        "concept": "library_export",
                        "framework": "library-exports",
                    }
                ]
            },
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        lib_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(lib_eps) == 1
        assert lib_eps[0].confidence == 0.75

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

    def test_service_by_name_not_treated_as_entrypoint(self) -> None:
        """Classes ending in 'Service' are tracked but not made entrypoints (covers entrypoints.py:480).

        Services are potential business logic entry points but we explicitly skip them
        for now to avoid over-detecting entrypoints in codebases with many service classes.
        """
        sym = Symbol(
            id="java:UserService.java:1-50:UserService:class",
            name="UserService",
            kind="class",
            path="src/UserService.java",
            language="java",
            span=Span(1, 50, 0, 0),
            meta={"concepts": [
                {"concept": "service_by_name", "framework": "naming-conventions"},
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Service classes should NOT create any entrypoints
        assert len(entrypoints) == 0

    def test_command_by_name_c_entrypoint(self) -> None:
        """C functions matching cmd_* are detected as CLI_COMMAND entrypoints.

        This covers the command_by_name naming convention used by git,
        systemd, busybox, and other C CLI tools where cmd_<name> functions
        implement subcommands.
        """
        sym = Symbol(
            id="c:builtin/commit.c:1536-1666:cmd_commit:function",
            name="cmd_commit",
            kind="function",
            path="builtin/commit.c",
            language="c",
            span=Span(1536, 1666, 0, 0),
            meta={"concepts": [
                {"concept": "command_by_name", "framework": "naming-conventions"},
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        cmd_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cmd_eps) == 1
        assert cmd_eps[0].confidence == 0.80
        assert "by name" in cmd_eps[0].label
        assert "cmd_commit" in cmd_eps[0].label

    def test_command_by_name_skipped_if_cli_command_exists(self) -> None:
        """command_by_name skipped if CLI_COMMAND already detected via framework."""
        sym = Symbol(
            id="c:builtin/commit.c:1536-1666:cmd_commit:function",
            name="cmd_commit",
            kind="function",
            path="builtin/commit.c",
            language="c",
            span=Span(1536, 1666, 0, 0),
            meta={"concepts": [
                {"concept": "command", "framework": "some-cli"},
                {"concept": "command_by_name", "framework": "naming-conventions"},
            ]},
        )
        nodes = [sym]

        entrypoints = detect_entrypoints(nodes, [])

        # Framework detection wins — only one CLI_COMMAND entry
        cmd_eps = [e for e in entrypoints if e.kind == EntrypointKind.CLI_COMMAND]
        assert len(cmd_eps) == 1
        assert cmd_eps[0].confidence == 0.95  # Framework confidence, not naming

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
        # Create three route handlers with same base confidence (distinct paths)
        route_a = make_symbol(
            "route_a", path="a.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/a"}]},
        )
        route_b = make_symbol(
            "route_b", path="b.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/b"}]},
        )
        route_c = make_symbol(
            "route_c", path="c.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/c"}]},
        )

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
        route_isolated = make_symbol(
            "route_isolated", path="isolated.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/isolated"}]},
        )
        route_connected = make_symbol(
            "route_connected", path="connected.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/connected"}]},
        )
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
        # Create many route handlers with concept metadata (distinct paths)
        routes = [
            make_symbol(
                f"route_{i}", path=f"file{i}.py", language="python", start_line=i,
                meta={"concepts": [{"concept": "route", "method": "GET", "path": f"/test/{i}"}]},
            )
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
        route_caller = make_symbol(
            "route_caller", path="caller.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/caller"}]},
        )
        route_callee = make_symbol(
            "route_callee", path="callee.py", language="python",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/callee"}]},
        )
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

    def test_transitive_connectivity_boost(self) -> None:
        """A main() that delegates to a high-connectivity function should outrank
        a utility main with many direct calls but shallow transitive reach.

        This mirrors the DMD pattern: compiler/src/dmd/main.d:main calls tryMain
        (178 edges), while msvc-lib.d:main calls 10 small utility functions.
        The compiler main should rank higher despite fewer direct edges.

        Both may hit the 1.0 confidence cap, so the test checks rank order
        (sorted position) rather than strict confidence inequality.
        """
        # Main function meta for D main() detection
        main_meta = {"concepts": [{"concept": "main_function", "language": "d"}]}

        # "Compiler main" — calls only tryMain, which calls many helpers
        compiler_main = make_symbol(
            "main", path="compiler/src/main.d", language="d",
            start_line=1, end_line=10, meta=main_meta,
        )
        try_main = make_symbol(
            "tryMain", path="compiler/src/main.d", language="d",
            start_line=20, end_line=200,
        )

        # "Utility main" — calls many small helpers directly
        utility_main = make_symbol(
            "main", path="vcbuild/msvc-lib.d", language="d",
            start_line=1, end_line=30, meta=main_meta,
        )

        # Create 50 helpers (mimics tryMain's high connectivity)
        helpers = [
            make_symbol(f"helper_{i}", path="helpers.d", language="d", start_line=i * 10)
            for i in range(50)
        ]

        nodes = [compiler_main, try_main, utility_main] + helpers

        edges = [
            # compiler_main calls tryMain (1 direct edge)
            Edge.create(src=compiler_main.id, dst=try_main.id, edge_type="calls", line=5),
            # tryMain calls 50 helpers (high transitive reach)
            *[
                Edge.create(src=try_main.id, dst=h.id, edge_type="calls", line=25 + i)
                for i, h in enumerate(helpers)
            ],
            # utility_main calls 10 helpers directly (moderate direct reach)
            *[
                Edge.create(src=utility_main.id, dst=helpers[i].id, edge_type="calls", line=5 + i)
                for i in range(10)
            ],
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        main_eps = [
            ep for ep in entrypoints
            if ep.kind == EntrypointKind.MAIN_FUNCTION
        ]
        assert len(main_eps) >= 2

        # Entrypoints are sorted by confidence desc, then by effective
        # out-degree desc.  The compiler main (1 direct + 0.5 * 50 transitive
        # = 26 effective) should outrank the utility main (10 effective).
        compiler_idx = next(
            i for i, ep in enumerate(main_eps) if ep.symbol_id == compiler_main.id
        )
        utility_idx = next(
            i for i, ep in enumerate(main_eps) if ep.symbol_id == utility_main.id
        )
        assert compiler_idx < utility_idx, (
            f"Compiler main should rank before utility main "
            f"(compiler at index {compiler_idx}, utility at {utility_idx})"
        )


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

    def test_aggressive_test_demotion_prevents_flooding(self) -> None:
        """Test entrypoints in test files are filtered out entirely.

        In repos like DMD where 98% of main() functions are in test files,
        the 90% test penalty (0.80 * 0.1 = 0.08) pushes them below the
        MIN_ENTRYPOINT_CONFIDENCE threshold (0.10), so they are excluded
        from results. Only production mains survive.
        """
        # 3 production mains
        prod_mains = [
            make_symbol(
                f"main_{i}",
                path=f"src/app_{i}.py",
                start_line=i * 100,
                meta={"concepts": [{"concept": "main_function"}]},
            )
            for i in range(3)
        ]
        # 50 test mains (simulating DMD-like flooding)
        test_mains = [
            make_symbol(
                f"test_main_{i}",
                path=f"tests/test_mod_{i}.py",
                start_line=i * 100,
                meta={"concepts": [{"concept": "main_function"}]},
            )
            for i in range(50)
        ]
        nodes = prod_mains + test_mains

        entrypoints = detect_entrypoints(nodes, [])

        # Only production mains survive (test mains at 0.08 are filtered)
        assert len(entrypoints) == 3

        # All surviving entries should be production
        for ep in entrypoints:
            assert "src/" in ep.symbol_id
            assert ep.confidence == pytest.approx(0.80, rel=0.01)

    def test_test_file_penalty(self) -> None:
        """Entrypoints in test files are filtered out by confidence threshold.

        Test file penalty (90% reduction) pushes main_function from 0.80 to
        0.08, which is below MIN_ENTRYPOINT_CONFIDENCE (0.10). Only the
        production main survives.
        """
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

        # Test entry filtered (0.80 * 0.1 = 0.08 < 0.10 threshold)
        assert len(entrypoints) == 1
        assert entrypoints[0].symbol_id == prod_main.id
        assert entrypoints[0].confidence == pytest.approx(0.80, rel=0.01)

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
        """Stacked penalties filter out vendor test entries entirely.

        Base 0.80 * 0.1 (test) * 0.3 (vendor) = 0.024 — well below
        MIN_ENTRYPOINT_CONFIDENCE (0.10). Only production entry survives.
        """
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

        # Vendor test entry filtered (0.024 < 0.10 threshold)
        assert len(entrypoints) == 1
        assert entrypoints[0].symbol_id == prod.id
        assert entrypoints[0].confidence == pytest.approx(0.80, rel=0.01)

    def test_http_route_test_penalty(self) -> None:
        """HTTP routes in test files are filtered by confidence threshold.

        Base 0.95 * 0.1 (test penalty) = 0.095 < MIN_ENTRYPOINT_CONFIDENCE (0.10).
        Only the production route survives.
        """
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

        # Test route filtered (0.095 < 0.10)
        assert len(entrypoints) == 1
        assert entrypoints[0].symbol_id == prod_route.id
        assert entrypoints[0].confidence == pytest.approx(0.95, rel=0.01)

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

    def test_test_file_main_no_connectivity_boost(self) -> None:
        """MAIN_FUNCTION in test files skips connectivity boost.

        Without the fix, a test-file main with 10 outgoing edges would get
        0.80 * 0.1 (test penalty) = 0.08 base + ~0.24 boost = 0.32,
        passing the MIN_ENTRYPOINT_CONFIDENCE (0.10) filter.  With the fix,
        the boost is skipped and 0.08 is below the threshold.
        """
        test_main = make_symbol(
            "main",
            path="tests/test_main.py",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        edges = [
            Edge.create(
                src=test_main.id,
                dst=f"python:tests/helper.py:{i}-{i+1}:func{i}:function",
                edge_type="calls",
                line=i,
                origin="test",
                origin_run_id="test",
            )
            for i in range(10)
        ]

        entrypoints = detect_entrypoints([test_main], edges)

        # Filtered out: 0.08 < MIN_ENTRYPOINT_CONFIDENCE (0.10)
        assert len(entrypoints) == 0

    def test_test_function_no_connectivity_boost(self) -> None:
        """TEST_FUNCTION entrypoints skip the connectivity boost.

        Test functions are entrypoints for test runners, not architectural
        entrypoints. Their connectivity should not lift them above the
        confidence threshold. Without this fix, a test function with
        0.80 * 0.1 = 0.08 base + 0.25 boost = 0.33 would pass the
        MIN_ENTRYPOINT_CONFIDENCE filter, flooding entries.txt with
        hundreds of test functions.
        """
        sym = make_symbol(
            "test_user_login", kind="function", path="tests/test_auth.py",
            meta={"concepts": [{"concept": "test_function", "framework": "pytest"}]},
        )
        edges = [
            Edge.create(
                src=sym.id,
                dst=f"python:tests/helper.py:{i}-{i+1}:func{i}:function",
                edge_type="calls",
                line=i,
                origin="test",
                origin_run_id="test",
            )
            for i in range(50)  # 50 outgoing edges — max boost
        ]

        entrypoints = detect_entrypoints([sym], edges)

        # Should be filtered out — TEST_FUNCTION skips connectivity boost,
        # so 0.80 * 0.1 = 0.08 < MIN_ENTRYPOINT_CONFIDENCE (0.10)
        assert len(entrypoints) == 0

    def test_ranking_order_respects_penalties(self) -> None:
        """Final ranking correctly orders by penalized confidence.

        test_main (0.80 * 0.1 = 0.08) is below threshold and filtered out.
        Remaining entries ordered: prod_route (0.95) > vendor_route (0.285).
        """
        # High-confidence production route
        prod_route = make_symbol(
            "api_handler",
            path="src/api.py",
            meta={"concepts": [{"concept": "route", "path": "/api", "method": "GET"}]},
            supply_chain_tier=1,
        )
        # Low-confidence test main (will be filtered)
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

        # test_main filtered (0.08 < 0.10 threshold)
        # Expected order: prod_route (0.95) > vendor_route (0.285)
        assert len(entrypoints) == 2
        assert entrypoints[0].symbol_id == prod_route.id
        assert entrypoints[1].symbol_id == vendor_route.id


class TestGoUtilityMainDemotion:
    """Tests for Go utility main() demotion (INV-mahap).

    In Go repos with many main() functions, deeply nested cmd/ directories
    (plugin shims, tool binaries) should be demoted relative to top-level
    cmd/ entries (the actual application binaries).
    """

    def test_deeply_nested_cmd_main_demoted(self) -> None:
        """Main in builtin/logical/consul/cmd/ is demoted vs cmd/vault/."""
        # Top-level cmd/ main (the actual server binary)
        server_main = make_symbol(
            "main",
            path="cmd/vault/main.go",
            language="go",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        # Deeply nested cmd/ main (plugin shim binary)
        plugin_main = make_symbol(
            "main",
            path="builtin/logical/consul/cmd/consul/main.go",
            language="go",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        entrypoints = detect_entrypoints([server_main, plugin_main], [])

        server_ep = next(e for e in entrypoints if "cmd/vault" in e.symbol_id)
        plugin_ep = next(e for e in entrypoints if "builtin" in e.symbol_id)

        # Server main should rank higher than plugin shim main
        assert server_ep.confidence > plugin_ep.confidence

    def test_pkg_cmd_not_demoted(self) -> None:
        """Main in pkg/cmd/grafana/ is NOT demoted (standard Go layout)."""
        grafana_main = make_symbol(
            "main",
            path="pkg/cmd/grafana/main.go",
            language="go",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        entrypoints = detect_entrypoints([grafana_main], [])

        assert len(entrypoints) == 1
        # pkg/cmd/ is at depth 1 — should NOT be penalized
        assert entrypoints[0].confidence == pytest.approx(0.80, rel=0.01)

    def test_top_level_cmd_not_demoted(self) -> None:
        """Main in cmd/prometheus/ is NOT demoted."""
        prom_main = make_symbol(
            "main",
            path="cmd/prometheus/main.go",
            language="go",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        entrypoints = detect_entrypoints([prom_main], [])

        assert len(entrypoints) == 1
        assert entrypoints[0].confidence == pytest.approx(0.80, rel=0.01)

    def test_codegen_filename_demoted_via_utility(self) -> None:
        """Main in gen.go files is demoted via utility file penalty."""
        codegen_main = make_symbol(
            "main",
            path="kinds/gen.go",
            language="go",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        server_main = make_symbol(
            "main",
            path="cmd/server/main.go",
            language="go",
            start_line=10,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        entrypoints = detect_entrypoints([codegen_main, server_main], [])

        codegen_ep = next(e for e in entrypoints if "gen.go" in e.symbol_id)
        server_ep = next(e for e in entrypoints if "cmd/server" in e.symbol_id)

        # Codegen should be demoted below server
        assert server_ep.confidence > codegen_ep.confidence
        # Utility penalty: 0.80 * 0.5 = 0.40
        assert codegen_ep.confidence == pytest.approx(0.40, rel=0.01)

    def test_non_go_main_not_affected_by_nested_cmd(self) -> None:
        """The nested-cmd penalty only applies to Go main_function entries."""
        java_main = make_symbol(
            "main",
            path="module/submodule/cmd/tool/Main.java",
            language="java",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        entrypoints = detect_entrypoints([java_main], [])

        assert len(entrypoints) == 1
        # Java main should NOT get the nested-cmd penalty
        assert entrypoints[0].confidence == pytest.approx(0.80, rel=0.01)


class TestConnectivityFallback:
    """Tests for centrality-based entrypoint fallback (DEEP mode).

    When no concept-based entrypoints are found (no routes, no main(), etc.),
    the system falls back to selecting the most-connected callable symbols.
    """

    def test_fallback_activates_when_no_concepts(self) -> None:
        """Connectivity fallback activates when no concept-based entrypoints exist."""
        # Symbols with no concept metadata
        func_a = make_symbol("processData", kind="function", path="lib.rs",
                             language="rust", start_line=1, end_line=10)
        func_b = make_symbol("helper", kind="function", path="lib.rs",
                             language="rust", start_line=20, end_line=30)
        func_c = make_symbol("run", kind="function", path="lib.rs",
                             language="rust", start_line=40, end_line=50)

        # processData calls helper and run; it's the most connected
        edges = [
            Edge.create(src=func_a.id, dst=func_b.id, edge_type="calls", line=1),
            Edge.create(src=func_a.id, dst=func_c.id, edge_type="calls", line=2),
            Edge.create(src=func_c.id, dst=func_b.id, edge_type="calls", line=3),
        ]

        entrypoints = detect_entrypoints([func_a, func_b, func_c], edges)

        assert len(entrypoints) > 0
        # The most-connected function should be ranked first
        assert entrypoints[0].symbol_id == func_a.id
        assert entrypoints[0].kind == EntrypointKind.CONNECTIVITY_BASED

    def test_fallback_does_not_activate_with_concepts(self) -> None:
        """No fallback when concept-based entrypoints exist."""
        func_main = make_symbol(
            "main", kind="function", path="main.py", language="python",
            meta={"concepts": [{"concept": "main_function", "framework": "main-functions"}]},
        )
        func_helper = make_symbol(
            "helper", kind="function", path="lib.py", language="python",
            start_line=10, end_line=20,
        )
        # helper has more connections but main has concept metadata
        edges = [
            Edge.create(src=func_helper.id, dst=func_main.id, edge_type="calls", line=1),
        ]

        entrypoints = detect_entrypoints([func_main, func_helper], edges)

        # Should have main_function, no connectivity_based
        kinds = {ep.kind for ep in entrypoints}
        assert EntrypointKind.MAIN_FUNCTION in kinds
        assert EntrypointKind.CONNECTIVITY_BASED not in kinds

    def test_fallback_confidence_lower_than_concepts(self) -> None:
        """Connectivity-based entrypoints have lower confidence than concept-based.

        The connectivity boost is intentionally skipped for fallback entrypoints
        to avoid double-counting (out-degree is already used for selection).
        Base confidence is 0.50, which stays below any concept-based entrypoint.
        """
        func = make_symbol("process", kind="function", path="lib.rs",
                           language="rust", start_line=1, end_line=10)
        edges = [
            Edge.create(src=func.id, dst="other:1", edge_type="calls", line=1),
            Edge.create(src=func.id, dst="other:2", edge_type="calls", line=2),
        ]

        entrypoints = detect_entrypoints([func], edges)

        assert len(entrypoints) > 0
        for ep in entrypoints:
            if ep.kind == EntrypointKind.CONNECTIVITY_BASED:
                # Base 0.50, no connectivity boost (double-count prevention)
                assert ep.confidence == 0.50

    def test_fallback_limits_to_top_n(self) -> None:
        """Fallback selects at most a bounded number of entrypoints."""
        # Create many disconnected functions
        symbols = []
        edges = []
        for i in range(20):
            sym = make_symbol(f"func_{i}", kind="function", path="lib.rs",
                              language="rust", start_line=i * 10, end_line=i * 10 + 5)
            symbols.append(sym)
            # Each function calls the next one
            if i > 0:
                edges.append(Edge.create(src=sym.id, dst=symbols[i - 1].id, edge_type="calls", line=i))

        entrypoints = detect_entrypoints(symbols, edges)

        # Should be bounded (not all 20 functions)
        assert len(entrypoints) <= 10

    def test_fallback_excludes_non_callables(self) -> None:
        """Fallback only considers callable symbols (functions, methods)."""
        # A struct with edges but non-callable
        struct_sym = make_symbol("Config", kind="struct", path="lib.rs",
                                language="rust", start_line=1, end_line=10)
        func_sym = make_symbol("init", kind="function", path="lib.rs",
                               language="rust", start_line=20, end_line=30)

        # Both have outgoing edges
        edges = [
            Edge.create(src=struct_sym.id, dst="other:1", edge_type="extends", line=1),
            Edge.create(src=func_sym.id, dst="other:2", edge_type="calls", line=1),
        ]

        entrypoints = detect_entrypoints([struct_sym, func_sym], edges)

        # Only the function should be an entrypoint
        assert len(entrypoints) >= 1
        ep_ids = {ep.symbol_id for ep in entrypoints}
        assert func_sym.id in ep_ids

    def test_fallback_penalizes_test_files(self) -> None:
        """Connectivity fallback applies same test-file penalty."""
        test_func = make_symbol("TestProcess", kind="function",
                                path="lib_test.go", language="go",
                                start_line=1, end_line=10)
        prod_func = make_symbol("Process", kind="function",
                                path="lib.go", language="go",
                                start_line=20, end_line=30)

        # Both have equal connectivity
        edges = [
            Edge.create(src=test_func.id, dst="other:1", edge_type="calls", line=1),
            Edge.create(src=prod_func.id, dst="other:2", edge_type="calls", line=1),
        ]

        entrypoints = detect_entrypoints([test_func, prod_func], edges)

        # Production function should rank higher
        if len(entrypoints) >= 2:
            prod_ep = next(ep for ep in entrypoints if ep.symbol_id == prod_func.id)
            test_ep = next(ep for ep in entrypoints if ep.symbol_id == test_func.id)
            assert prod_ep.confidence > test_ep.confidence


class TestTestFunctionConceptDetection:
    """Tests for test_function concept -> TEST_FUNCTION entrypoint mapping."""

    def test_test_function_concept_creates_entrypoint(self) -> None:
        """test_function concept produces a TEST_FUNCTION entrypoint.

        Uses a non-test path to avoid the test-file penalty filtering.
        The concept mapping is the focus of this test, not the penalty.
        """
        sym = make_symbol(
            "test_user_login", kind="function", path="src/runner.py",
            meta={"concepts": [{"concept": "test_function", "framework": "pytest"}]},
        )

        entrypoints = detect_entrypoints([sym], [])

        test_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.TEST_FUNCTION]
        assert len(test_eps) == 1
        assert test_eps[0].symbol_id == sym.id
        assert "test" in test_eps[0].label.lower()

    def test_benchmark_function_concept_creates_entrypoint(self) -> None:
        """benchmark_function concept produces a TEST_FUNCTION entrypoint.

        Uses a non-test path to avoid the test-file penalty filtering.
        """
        sym = make_symbol(
            "BenchmarkSort", kind="function", path="src/bench.go",
            meta={"concepts": [{"concept": "benchmark_function", "framework": "go-testing"}]},
        )

        entrypoints = detect_entrypoints([sym], [])

        test_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.TEST_FUNCTION]
        assert len(test_eps) == 1
        assert "benchmark" in test_eps[0].label.lower()

    def test_test_function_penalized_in_test_file(self) -> None:
        """TEST_FUNCTION in test files are filtered by confidence threshold.

        Base 0.80 * 0.1 (test penalty) = 0.08 < MIN_ENTRYPOINT_CONFIDENCE (0.10).
        """
        sym = make_symbol(
            "test_login", kind="function", path="tests/test_auth.py",
            meta={"concepts": [{"concept": "test_function", "framework": "pytest"}]},
        )

        entrypoints = detect_entrypoints([sym], [])

        # Filtered out due to low confidence
        assert len(entrypoints) == 0

    def test_test_function_does_not_duplicate(self) -> None:
        """Multiple test concepts on same symbol produce only one TEST_FUNCTION.

        Uses a non-test path to avoid the test-file penalty filtering.
        """
        sym = make_symbol(
            "test_api", kind="function", path="src/runner.py",
            meta={"concepts": [
                {"concept": "test_function", "framework": "pytest"},
                {"concept": "test_function", "framework": "unittest"},
            ]},
        )

        entrypoints = detect_entrypoints([sym], [])

        test_eps = [ep for ep in entrypoints if ep.kind == EntrypointKind.TEST_FUNCTION]
        assert len(test_eps) == 1


class TestRouteKindEntrypointDetection:
    """Tests for direct kind='route' entrypoint detection.

    Go and other analyzers create symbols with kind='route' and metadata
    (route_path, http_method) directly, bypassing YAML concept enrichment.
    These must be promoted to HTTP_ROUTE entrypoints.
    """

    def test_route_kind_promoted_to_entrypoint(self) -> None:
        """Symbol with kind='route' and route metadata becomes HTTP_ROUTE."""
        sym = make_symbol(
            "ListUsers",
            path="routers/api/v1/user.go",
            kind="route",
            language="go",
            meta={
                "route_path": "/api/v1/users",
                "http_method": "GET",
                "handler_name": "ListUsers",
            },
        )
        entrypoints = detect_entrypoints([sym], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert route_eps[0].symbol_id == sym.id
        assert route_eps[0].confidence == 0.90
        assert "GET" in route_eps[0].label
        assert "/api/v1/users" in route_eps[0].label

    def test_route_kind_method_only(self) -> None:
        """Route with method but no path still gets a label."""
        sym = make_symbol(
            "handler",
            path="routes.go",
            kind="route",
            language="go",
            meta={"http_method": "POST"},
        )
        entrypoints = detect_entrypoints([sym], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert "POST" in route_eps[0].label

    def test_route_kind_path_only(self) -> None:
        """Route with path but no method still gets a label."""
        sym = make_symbol(
            "handler",
            path="routes.go",
            kind="route",
            language="go",
            meta={"route_path": "/health"},
        )
        entrypoints = detect_entrypoints([sym], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert "/health" in route_eps[0].label

    def test_route_kind_no_metadata(self) -> None:
        """Route symbol with no meta still becomes entrypoint."""
        sym = make_symbol(
            "handler",
            path="routes.go",
            kind="route",
            language="go",
        )
        entrypoints = detect_entrypoints([sym], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert route_eps[0].label == "HTTP route"

    def test_no_duplicate_with_concept_route(self) -> None:
        """Route kind + route concept doesn't produce duplicate entrypoints."""
        sym = make_symbol(
            "ListUsers",
            path="routes.go",
            kind="route",
            language="go",
            meta={
                "route_path": "/users",
                "http_method": "GET",
                "concepts": [
                    {"concept": "route", "path": "/users", "method": "GET"},
                ],
            },
        )
        entrypoints = detect_entrypoints([sym], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        # Should be exactly 1, not 2
        assert len(route_eps) == 1

    def test_multiple_route_symbols(self) -> None:
        """Multiple route symbols each become entrypoints."""
        sym1 = make_symbol(
            "GetUser", path="api.go", kind="route", language="go",
            meta={"route_path": "/users/:id", "http_method": "GET"},
            start_line=1, end_line=1,
        )
        sym2 = make_symbol(
            "CreateUser", path="api.go", kind="route", language="go",
            meta={"route_path": "/users", "http_method": "POST"},
            start_line=10, end_line=10,
        )
        entrypoints = detect_entrypoints([sym1, sym2], [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 2
        ep_ids = {ep.symbol_id for ep in route_eps}
        assert sym1.id in ep_ids
        assert sym2.id in ep_ids


class TestLanguageDominanceRanking:
    """Tests for language dominance in entrypoint ranking.

    When multiple languages have entrypoints at similar confidence, the
    dominant language (by symbol count) should rank higher. This prevents
    a Python script from outranking a C main() in a 95% C codebase.
    """

    def test_dominant_language_ranks_first(self) -> None:
        """C main() should rank above Python main() in C-dominant repo.

        In a repo with 95 C symbols and 5 Python symbols, both languages
        have a main() function. The Python main has higher connectivity
        (more outgoing edges), which would normally give it a higher rank.
        But because C is the dominant language, C main should still rank
        first.

        This simulates the git repo scenario: 95% C code but Python
        scripts (git-p4.py) have higher connectivity in a single file,
        causing them to outrank the C main().
        """
        from hypergumbo_core.ir import Edge

        # C main function with modest connectivity (5 edges)
        c_main = make_symbol(
            "main", path="src/main.c", language="c",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        # 94 additional C symbols to make C dominant
        c_symbols = [
            make_symbol(
                f"c_func_{i}", path=f"src/lib_{i}.c", language="c",
                start_line=i * 10,
            )
            for i in range(94)
        ]

        # Python main with HIGHER connectivity (50 edges)
        # Uses a non-utility path to avoid utility penalty
        py_main = make_symbol(
            "main", path="contrib/p4/main.py", language="python",
            start_line=1,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        # 4 additional Python symbols (callees get re-called)
        py_symbols = [
            make_symbol(
                f"py_func_{i}", path=f"contrib/p4/util_{i}.py",
                language="python",
                start_line=i * 10 + 100,
            )
            for i in range(4)
        ]

        nodes = [c_main] + c_symbols + [py_main] + py_symbols

        # C main calls 5 C functions
        c_edges = [
            Edge.create(
                src=c_main.id, dst=c_symbols[i].id,
                edge_type="calls", line=i + 1,
                evidence_type="function_call", confidence=0.85,
                origin="test", origin_run_id="test",
            )
            for i in range(5)
        ]
        # Python main calls 50 functions (much higher connectivity)
        py_edges = [
            Edge.create(
                src=py_main.id, dst=py_symbols[i % len(py_symbols)].id,
                edge_type="calls", line=i + 1,
                evidence_type="function_call", confidence=0.85,
                origin="test", origin_run_id="test",
            )
            for i in range(50)
        ]

        entrypoints = detect_entrypoints(nodes, c_edges + py_edges)

        c_ep = next(
            (ep for ep in entrypoints if ep.symbol_id == c_main.id), None,
        )
        py_ep = next(
            (ep for ep in entrypoints if ep.symbol_id == py_main.id), None,
        )
        assert c_ep is not None, "C main should be detected as entrypoint"
        assert py_ep is not None, "Python main should be detected as entrypoint"

        # C main should rank higher than Python main despite lower
        # connectivity, because C is the dominant language (95 of 100
        # symbols).
        c_rank = entrypoints.index(c_ep)
        py_rank = entrypoints.index(py_ep)
        assert c_rank < py_rank, (
            f"C main (rank {c_rank}, conf {c_ep.confidence:.3f}) should rank "
            f"above Python main (rank {py_rank}, conf {py_ep.confidence:.3f}) "
            f"in a C-dominant repo (95% C by symbol count)"
        )

    def test_equal_languages_no_bias(self) -> None:
        """In a 50/50 repo, language dominance should not introduce bias.

        When two languages contribute equally, the tiebreaker should fall
        to connectivity (effective out-degree), not language.
        """
        from hypergumbo_core.ir import Edge

        # 50 Go symbols
        go_main = make_symbol(
            "main", path="cmd/main.go", language="go",
            meta={"concepts": [{"concept": "main_function"}]},
        )
        go_symbols = [
            make_symbol(
                f"go_func_{i}", path=f"pkg/lib_{i}.go", language="go",
                start_line=i * 10,
            )
            for i in range(49)
        ]

        # 50 Python symbols
        py_main = make_symbol(
            "main", path="app/main.py", language="python",
            start_line=1,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        py_symbols = [
            make_symbol(
                f"py_func_{i}", path=f"app/util_{i}.py", language="python",
                start_line=i * 10 + 100,
            )
            for i in range(49)
        ]

        nodes = [go_main] + go_symbols + [py_main] + py_symbols

        # Both mains have the same connectivity (10 edges each)
        go_edges = [
            Edge.create(
                src=go_main.id, dst=go_symbols[i].id,
                edge_type="calls", line=i + 1,
                evidence_type="function_call", confidence=0.85,
                origin="test", origin_run_id="test",
            )
            for i in range(10)
        ]
        py_edges = [
            Edge.create(
                src=py_main.id, dst=py_symbols[i].id,
                edge_type="calls", line=i + 1,
                evidence_type="function_call", confidence=0.85,
                origin="test", origin_run_id="test",
            )
            for i in range(10)
        ]

        entrypoints = detect_entrypoints(nodes, go_edges + py_edges)

        go_ep = next(
            (ep for ep in entrypoints if ep.symbol_id == go_main.id), None,
        )
        py_ep = next(
            (ep for ep in entrypoints if ep.symbol_id == py_main.id), None,
        )
        assert go_ep is not None
        assert py_ep is not None

        # Both should have the same confidence (language weights are equal)
        assert go_ep.confidence == pytest.approx(py_ep.confidence, rel=0.01), (
            f"Equal-language entrypoints should have same confidence: "
            f"Go={go_ep.confidence:.3f}, Python={py_ep.confidence:.3f}"
        )


class TestApplicationLibraryExportDemotion:
    """Tests for library_export demotion in application repos.

    When a repo has real semantic entrypoints (HTTP routes, CLI commands,
    main functions), library_export entries are noise — they represent
    API visibility (e.g., Go uppercase symbols), not developer-facing
    entrypoints. These should be heavily demoted so routes/commands
    dominate the entrypoint list.

    This is the "forgejo problem": 7,474 library_export entrypoints
    drowning out 772 meaningful HTTP routes.
    """

    def test_library_export_demoted_when_routes_exist(self) -> None:
        """library_export entries are filtered out when HTTP routes exist.

        With routes present, library_export demotion (90%) reduces confidence
        from 0.80 to 0.08, which is below MIN_ENTRYPOINT_CONFIDENCE (0.10).
        Only routes survive.
        """
        route = make_symbol(
            "ListUsers", path="routers/api/v1/user.go", kind="route",
            language="go",
            meta={"route_path": "/api/v1/users", "http_method": "GET"},
        )
        export1 = make_symbol(
            "GetEngine", path="models/engine.go", kind="function",
            language="go", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        export2 = make_symbol(
            "Find", path="models/find.go", kind="function",
            language="go", start_line=20,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [route, export1, export2]

        entrypoints = detect_entrypoints(nodes, [])

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]

        assert len(route_eps) == 1
        # Exports filtered out (0.80 * 0.1 = 0.08 < 0.10 threshold)
        assert len(export_eps) == 0

    def test_library_export_demoted_when_commands_exist(self) -> None:
        """library_export filtered when same-language CLI commands exist.

        Demotion (90%) drops from 0.80 to 0.08 < threshold.
        """
        command = make_symbol(
            "cmd_merge", path="builtin/merge.c", kind="function",
            language="c", start_line=1,
            meta={"concepts": [{"concept": "command_by_name"}]},
        )
        export = make_symbol(
            "Parse", path="lib/parse.c", kind="function",
            language="c", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [command, export]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 0

    def test_library_export_demoted_when_main_exists(self) -> None:
        """library_export filtered when main() function exists.

        Demotion (90%) drops from 0.80 to 0.08 < threshold.
        """
        main_fn = make_symbol(
            "main", path="cmd/server/main.go", kind="function",
            language="go", start_line=1,
            meta={"concepts": [{"concept": "main_function"}]},
        )
        export = make_symbol(
            "NewServer", path="pkg/server.go", kind="function",
            language="go", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [main_fn, export]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 0

    def test_library_export_not_demoted_in_pure_library(self) -> None:
        """library_export keeps full confidence when no semantic entries exist.

        A pure library (no routes, no commands, no main) should preserve
        library_export confidence since that IS the right entrypoint type.
        """
        export1 = make_symbol(
            "NewClient", path="client.go", kind="function",
            language="go", start_line=1,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        export2 = make_symbol(
            "Dial", path="transport.go", kind="function",
            language="go", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [export1, export2]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 2
        for ep in export_eps:
            assert ep.confidence >= 0.70, (
                f"library_export {ep.label} has confidence {ep.confidence:.2f}, "
                f"expected >= 0.70 in pure library (no demotion)"
            )

    def test_test_entries_dont_trigger_demotion(self) -> None:
        """TEST_FUNCTION entrypoints alone should NOT trigger demotion.

        A library with test functions but no routes/commands/main should
        keep its library_export entries at full confidence.
        """
        test_fn = make_symbol(
            "TestNewClient", path="client_test.go", kind="function",
            language="go", start_line=1,
            meta={"concepts": [{"concept": "test_function"}]},
        )
        export = make_symbol(
            "NewClient", path="client.go", kind="function",
            language="go", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [test_fn, export]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 1
        assert export_eps[0].confidence >= 0.70

    def test_same_language_demotion_works_in_polyglot(self) -> None:
        """Same-language demotion applies in polyglot repos.

        A Python app with Flask routes and library_export entries should
        see the demotion. Exports at 0.08 are filtered out.
        """
        route = make_symbol(
            "get_users", path="src/api/routes.py", kind="function",
            language="python", start_line=1,
            meta={"concepts": [{"concept": "route", "path": "/users", "method": "GET"}]},
        )
        export = make_symbol(
            "UserModel", path="src/models.py", kind="class",
            language="python", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [route, export]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 0

    def test_dominant_lang_exports_not_demoted_by_minority_semantic(self) -> None:
        """Dominant language library_export NOT demoted by minority language main().

        ArkLib scenario: 95% Lean library (163 files) with 5% Python helper
        scripts (6 files with main()). Python main() should NOT trigger
        demotion of Lean library_export entries — Lean exports ARE the
        real entrypoints for this repo.

        Fix for WI-sirim: minority-language semantic entrypoints must not
        suppress majority-language library exports.
        """
        # Lean is the dominant language (many symbols)
        lean_exports = [
            make_symbol(
                f"LeanDef{i}", path=f"ArkLib/Def{i}.lean", kind="function",
                language="lean", start_line=i,
                meta={"concepts": [{"concept": "library_export"}]},
            )
            for i in range(20)
        ]
        # Python is the minority language (few symbols)
        py_mains = [
            make_symbol(
                "main", path=f"scripts/helper{i}.py", kind="function",
                language="python", start_line=1 + i,
                meta={"concepts": [{"concept": "main_function"}]},
            )
            for i in range(3)
        ]
        nodes = lean_exports + py_mains

        entrypoints = detect_entrypoints(nodes, [])

        lean_eps = [
            e for e in entrypoints
            if e.kind == EntrypointKind.LIBRARY_EXPORT
        ]
        # Lean exports should survive — not demoted by Python main()
        assert len(lean_eps) >= 10, (
            f"Expected >= 10 Lean library_export entries to survive, "
            f"got {len(lean_eps)}. Minority-language main() should not "
            f"demote dominant-language library exports."
        )
        for ep in lean_eps:
            assert ep.confidence >= 0.50, (
                f"Lean export {ep.label} confidence {ep.confidence:.2f} "
                f"too low — should not be demoted by Python main()"
            )

    def test_forgejo_scale_scenario(self) -> None:
        """Simulate forgejo: 772 routes + 7474 library_exports.

        Top 20 entrypoints should be all routes (or main), not exports.
        This is the primary motivation for this feature.
        """
        routes = [
            make_symbol(
                f"Route{i}", path=f"routers/api/v1/r{i}.go", kind="route",
                language="go", start_line=i,
                meta={"route_path": f"/api/v1/r{i}", "http_method": "GET"},
            )
            for i in range(50)  # Subset of 772
        ]
        exports = [
            make_symbol(
                f"Export{i}", path=f"models/m{i}.go", kind="function",
                language="go", start_line=i + 100,
                meta={"concepts": [{"concept": "library_export"}]},
            )
            for i in range(200)  # Subset of 7474
        ]
        nodes = routes + exports

        entrypoints = detect_entrypoints(nodes, [])

        # Top 20 should all be routes
        top_20 = entrypoints[:20]
        for ep in top_20:
            assert ep.kind == EntrypointKind.HTTP_ROUTE, (
                f"Top 20 should be routes, but found {ep.kind.value}: {ep.label}"
            )


    def test_example_routes_dont_trigger_library_export_demotion(self) -> None:
        """Routes from example/ directories should not trigger library_export demotion.

        Library repos like livekit client-sdk-js have example code with HTTP
        routes (e.g., examples/rpc/api.ts) that are NOT real application
        entrypoints. These example routes should not cause library_export
        entries (e.g., Room class) to be demoted.
        """
        # Library export: the core class
        lib_export = make_symbol(
            "Room", path="src/room/Room.ts", kind="class",
            language="typescript", start_line=1,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        # Example route: exists in examples/ directory
        example_route = make_symbol(
            "getToken", path="examples/rpc/api.ts", kind="function",
            language="typescript", start_line=10,
            meta={"concepts": [{"concept": "route", "path": "/api/get-token", "method": "POST"}]},
        )
        nodes = [lib_export, example_route]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) >= 1, (
            "Library export should survive when only example routes exist"
        )
        assert export_eps[0].confidence >= 0.50, (
            f"Library export confidence {export_eps[0].confidence:.2f} too low — "
            f"example routes should not trigger demotion"
        )

    def test_infrastructure_exports_dampened_more_than_api_exports(self) -> None:
        """Library exports from infrastructure paths (telemetry/, metrics/, logging/)
        should be dampened more aggressively than API exports.

        In gemini-cli, 77 of 111 entrypoints were telemetry exports from
        packages/core/src/telemetry/*.ts. With connectivity boost, these
        survive the standard 0.1x demotion at ~0.14 confidence. Infrastructure-
        path exports get an additional dampening, dropping them below threshold.
        """
        route = make_symbol(
            "handle_mcp", path="src/server.ts", kind="function",
            language="typescript", start_line=1,
            meta={"concepts": [{"concept": "route", "path": "/mcp", "method": "POST"}]},
        )
        # Infrastructure export: telemetry plumbing
        telemetry_export = make_symbol(
            "ClearcutLogger", path="packages/core/src/telemetry/clearcut-logger.ts",
            kind="class", language="typescript", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        # API export: developer-facing class
        api_export = make_symbol(
            "McpClient", path="packages/core/src/mcp/client.ts",
            kind="class", language="typescript", start_line=10,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        # Helpers called by exports (for connectivity boost)
        helper1 = make_symbol("helper1", path="src/utils.ts", start_line=50, language="typescript")
        helper2 = make_symbol("helper2", path="src/utils.ts", start_line=60, language="typescript")
        nodes = [route, telemetry_export, api_export, helper1, helper2]

        # Give both exports outgoing edges so they get connectivity boost
        # (without boost, 0.80 * 0.1 = 0.08 < threshold and both are filtered)
        edges = [
            Edge.create(src=telemetry_export.id, dst=helper1.id, edge_type="calls", line=1),
            Edge.create(src=telemetry_export.id, dst=helper2.id, edge_type="calls", line=2),
            Edge.create(src=api_export.id, dst=helper1.id, edge_type="calls", line=1),
            Edge.create(src=api_export.id, dst=helper2.id, edge_type="calls", line=2),
        ]

        entrypoints = detect_entrypoints(nodes, edges)

        route_eps = [e for e in entrypoints if e.kind == EntrypointKind.HTTP_ROUTE]
        export_eps = {
            ep.symbol_id: ep
            for ep in entrypoints if ep.kind == EntrypointKind.LIBRARY_EXPORT
        }

        assert len(route_eps) == 1

        # Both exports get standard demotion (0.1x) from semantic entrypoints.
        # With connectivity boost, base 0.80 + boost -> ~1.0+, * 0.1 -> ~0.10+
        # API export should survive, infra export should be dampened further.
        api_ep = export_eps.get(api_export.id)
        telemetry_ep = export_eps.get(telemetry_export.id)

        # API export survives
        assert api_ep is not None, "API export should survive demotion"

        # Telemetry export should be filtered out (below MIN_ENTRYPOINT_CONFIDENCE)
        assert telemetry_ep is None, (
            f"Telemetry export should be filtered out, but has confidence "
            f"{telemetry_ep.confidence:.3f}" if telemetry_ep else ""
        )

    def test_infrastructure_exports_not_dampened_without_semantic_entrypoints(self) -> None:
        """In a pure library, infrastructure exports should NOT be dampened.

        If there are no routes/commands/main, the repo is a library and ALL
        exports are meaningful, even from telemetry/ paths.
        """
        telemetry_export = make_symbol(
            "Logger", path="src/telemetry/logger.ts",
            kind="class", language="typescript", start_line=1,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        api_export = make_symbol(
            "Client", path="src/client.ts",
            kind="class", language="typescript", start_line=1,
            meta={"concepts": [{"concept": "library_export"}]},
        )
        nodes = [telemetry_export, api_export]

        entrypoints = detect_entrypoints(nodes, [])

        export_eps = [e for e in entrypoints if e.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 2, (
            "Both exports should survive in a pure library (no semantic demotion)"
        )


class TestDeclarationDedup:
    """Tests for deduplication of declaration vs definition entrypoints."""

    def test_declaration_deduped_when_definition_exists(self) -> None:
        """When both declaration and definition exist for same name, keep definition."""
        # Declaration from header (e.g., builtin.h)
        decl = make_symbol(
            "cmd_add", path="builtin.h", kind="function",
            language="c", start_line=5,
            meta={"concepts": [{"concept": "command_by_name"}]},
        )
        # Manually set modifiers since make_symbol doesn't support it
        decl.modifiers = ["declaration"]

        # Definition from source (e.g., builtin/add.c)
        defn = make_symbol(
            "cmd_add", path="builtin/add.c", kind="function",
            language="c", start_line=10,
            meta={"concepts": [{"concept": "command_by_name"}]},
        )

        entrypoints = detect_entrypoints([decl, defn], [])

        # Should only have one entrypoint for cmd_add (the definition)
        cmd_add_eps = [ep for ep in entrypoints if "cmd_add" in ep.label]
        assert len(cmd_add_eps) == 1
        assert cmd_add_eps[0].symbol_id == defn.id

    def test_declaration_kept_when_no_definition(self) -> None:
        """When only a declaration exists (no definition), keep it."""
        decl = make_symbol(
            "cmd_add", path="builtin.h", kind="function",
            language="c", start_line=5,
            meta={"concepts": [{"concept": "command_by_name"}]},
        )
        decl.modifiers = ["declaration"]

        entrypoints = detect_entrypoints([decl], [])

        cmd_add_eps = [ep for ep in entrypoints if "cmd_add" in ep.label]
        assert len(cmd_add_eps) == 1


class TestRouteLabelDedup:
    """Tests for deduplication of HTTP_ROUTE entrypoints by (method, path)."""

    def test_same_route_from_multiple_symbols_deduplicated(self) -> None:
        """Multiple symbols producing HTTP POST /-/quit should yield one entry."""
        # Route symbol from route registration
        route_sym = make_symbol(
            "POST /-/quit", path="web/web.go", kind="route",
            language="go", start_line=565,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )
        # Handler method symbol with route concept
        handler_sym = make_symbol(
            "Handler.quit", path="web/web.go", kind="method",
            language="go", start_line=923,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )
        # Another route registration at a different line
        route_sym2 = make_symbol(
            "POST /-/quit", path="web/web.go", kind="route",
            language="go", start_line=574,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )

        entrypoints = detect_entrypoints([route_sym, handler_sym, route_sym2], [])

        quit_eps = [ep for ep in entrypoints if "/-/quit" in ep.label and "POST" in ep.label]
        assert len(quit_eps) == 1, (
            f"Expected 1 POST /-/quit entrypoint, got {len(quit_eps)}: "
            f"{[ep.label for ep in quit_eps]}"
        )

    def test_different_methods_same_path_not_deduplicated(self) -> None:
        """POST /-/quit and PUT /-/quit are different entrypoints."""
        post_sym = make_symbol(
            "POST /-/quit", path="web/web.go", kind="route",
            language="go", start_line=565,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )
        put_sym = make_symbol(
            "PUT /-/quit", path="web/web.go", kind="route",
            language="go", start_line=566,
            meta={"concepts": [{"concept": "route", "method": "PUT", "path": "/-/quit"}]},
        )

        entrypoints = detect_entrypoints([post_sym, put_sym], [])

        quit_eps = [ep for ep in entrypoints if "/-/quit" in ep.label]
        assert len(quit_eps) == 2

    def test_route_dedup_keeps_highest_confidence(self) -> None:
        """When deduplicating routes, the highest confidence entry is kept."""
        # Non-test file route (high confidence)
        prod_sym = make_symbol(
            "POST /-/quit", path="web/web.go", kind="route",
            language="go", start_line=565,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )
        # Test file route (will get confidence penalty)
        test_sym = make_symbol(
            "POST /-/quit", path="web/web_test.go", kind="route",
            language="go", start_line=100,
            meta={"concepts": [{"concept": "route", "method": "POST", "path": "/-/quit"}]},
        )

        entrypoints = detect_entrypoints([prod_sym, test_sym], [])

        quit_eps = [ep for ep in entrypoints if "/-/quit" in ep.label and "POST" in ep.label]
        assert len(quit_eps) == 1
        # Should keep the production one (higher confidence)
        assert quit_eps[0].symbol_id == prod_sym.id


class TestEntrypointConfidenceFiltering:
    """Tests for minimum confidence threshold and count cap.

    detect_entrypoints() should filter out entries below a minimum confidence
    threshold (0.10) and cap the total number of returned entries. This
    addresses the INV-mahap finding: 3100 library_export entries at 0.075
    confidence flooding --list-entries output.
    """

    def test_low_confidence_entries_filtered(self) -> None:
        """Entries below 0.10 confidence are excluded from results.

        library_export entries get 90% demotion when semantic entries exist,
        dropping from 0.80 to 0.08 — below the 0.10 threshold.
        """
        # Create a route (semantic) + many library_exports
        route = make_symbol(
            "handle_request", path="src/routes.py",
            meta={"concepts": [{"concept": "route"}]},
        )
        exports = []
        for i in range(100):
            exports.append(make_symbol(
                f"export_{i}", path="src/lib.py",
                start_line=i + 1,
                meta={"concepts": [{"concept": "library_export"}]},
            ))

        entrypoints = detect_entrypoints([route] + exports, [])

        # The route should survive (high confidence)
        route_eps = [ep for ep in entrypoints
                     if ep.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1

        # Library exports should be filtered out (0.80 * 0.1 = 0.08 < 0.10)
        export_eps = [ep for ep in entrypoints
                      if ep.kind == EntrypointKind.LIBRARY_EXPORT]
        assert len(export_eps) == 0, (
            f"Expected 0 low-confidence exports, got {len(export_eps)}"
        )

    def test_count_cap_limits_results(self) -> None:
        """detect_entrypoints caps results based on repo size.

        For small repos, the cap is BASE_ENTRYPOINT_CAP (50).
        Even when many entries pass the confidence threshold, the result
        is capped to prevent output flooding.
        """
        # Create more routes than the base cap (small repo)
        nodes = []
        for i in range(BASE_ENTRYPOINT_CAP + 20):
            nodes.append(make_symbol(
                f"route_{i}", path=f"src/routes_{i}.py",
                start_line=1,
                meta={"concepts": [{"concept": "route", "method": "GET", "path": f"/r/{i}"}]},
            ))

        entrypoints = detect_entrypoints(nodes, [])
        assert len(entrypoints) <= BASE_ENTRYPOINT_CAP

    def test_count_cap_scales_with_large_repo(self) -> None:
        """detect_entrypoints allows more entries for large repos.

        A repo with 20000 symbols should allow up to 200 entrypoints
        (node_count // 100), not just 50.
        """
        # Create 200 routes + 19800 non-entrypoint symbols = 20000 total
        nodes = []
        for i in range(200):
            nodes.append(make_symbol(
                f"route_{i}", path=f"src/routes_{i}.py",
                start_line=1,
                meta={"concepts": [{"concept": "route", "method": "GET", "path": f"/api/{i}"}]},
            ))
        for i in range(19800):
            nodes.append(make_symbol(
                f"helper_{i}", path=f"src/helpers_{i}.py",
                start_line=1,
            ))

        entrypoints = detect_entrypoints(nodes, [])
        # With 20000 nodes, cap is 200 — all 200 routes should be returned
        assert len(entrypoints) == 200

    def test_high_confidence_entries_preserved(self) -> None:
        """Entries above threshold are preserved."""
        route = make_symbol(
            "handle_request", path="src/routes.py",
            meta={"concepts": [{"concept": "route"}]},
        )
        main = make_symbol(
            "main", path="src/main.py",
            meta={"concepts": [{"concept": "main_function"}]},
        )

        entrypoints = detect_entrypoints([route, main], [])
        assert len(entrypoints) == 2

    def test_no_semantic_entries_exports_preserved(self) -> None:
        """Without semantic entries, library_exports keep full confidence.

        In a pure library (no routes/commands), exports are the only
        entrypoints and should NOT be filtered by the confidence threshold.
        """
        exports = []
        for i in range(5):
            exports.append(make_symbol(
                f"pub_fn_{i}", path="src/lib.rs",
                start_line=i + 1, language="rust",
                meta={"concepts": [{"concept": "library_export"}]},
            ))

        entrypoints = detect_entrypoints(exports, [])
        # No demotion happens (no semantic entries), so all at 0.80
        assert len(entrypoints) == 5


class TestComputeEntrypointCap:
    """Tests for compute_entrypoint_cap() scaling function."""

    def test_small_repo_gets_base_cap(self) -> None:
        """Repos with < 5000 nodes get the base cap of 50."""
        assert compute_entrypoint_cap(100) == BASE_ENTRYPOINT_CAP
        assert compute_entrypoint_cap(1000) == BASE_ENTRYPOINT_CAP
        assert compute_entrypoint_cap(4999) == BASE_ENTRYPOINT_CAP

    def test_medium_repo_scales_proportionally(self) -> None:
        """Repos with 5000-50000 nodes scale at 1% of node count."""
        assert compute_entrypoint_cap(10000) == 100
        assert compute_entrypoint_cap(20000) == 200
        assert compute_entrypoint_cap(25000) == 250

    def test_large_repo_capped_at_maximum(self) -> None:
        """Very large repos are capped at MAX_ENTRYPOINT_CAP (500)."""
        assert compute_entrypoint_cap(90000) == MAX_ENTRYPOINT_CAP
        assert compute_entrypoint_cap(200000) == MAX_ENTRYPOINT_CAP

    def test_zero_nodes(self) -> None:
        """Zero nodes returns base cap."""
        assert compute_entrypoint_cap(0) == BASE_ENTRYPOINT_CAP

    def test_exact_threshold(self) -> None:
        """At exactly 5000 nodes, cap equals base cap (5000 // 100 = 50)."""
        assert compute_entrypoint_cap(5000) == BASE_ENTRYPOINT_CAP

    def test_just_above_threshold(self) -> None:
        """At 5100 nodes, cap scales to 51."""
        assert compute_entrypoint_cap(5100) == 51


class TestFrontendFileDetection:
    """Tests for _is_frontend_file (WI-ronik)."""

    def test_tsx_file_is_frontend(self) -> None:
        """TSX files (React) are detected as frontend."""
        sym = make_symbol("handleRemove", path="src/components/Settings.tsx")
        assert _is_frontend_file(sym) is True

    def test_jsx_file_is_frontend(self) -> None:
        """JSX files (React) are detected as frontend."""
        sym = make_symbol("onClick", path="app/components/Button.jsx")
        assert _is_frontend_file(sym) is True

    def test_vue_file_is_frontend(self) -> None:
        """Vue files are detected as frontend."""
        sym = make_symbol("handleClick", path="src/views/UserList.vue")
        assert _is_frontend_file(sym) is True

    def test_svelte_file_is_frontend(self) -> None:
        """Svelte files are detected as frontend."""
        sym = make_symbol("onMount", path="src/routes/Dashboard.svelte")
        assert _is_frontend_file(sym) is True

    def test_components_dir_is_frontend(self) -> None:
        """Files in components/ directory are frontend."""
        sym = make_symbol("render", path="src/components/Table.ts")
        assert _is_frontend_file(sym) is True

    def test_server_ts_not_frontend(self) -> None:
        """Regular .ts files in non-frontend dirs are not frontend."""
        sym = make_symbol("handleRequest", path="src/api/users.ts")
        assert _is_frontend_file(sym) is False

    def test_python_not_frontend(self) -> None:
        """Python files are not frontend."""
        sym = make_symbol("get_users", path="src/views.py")
        assert _is_frontend_file(sym) is False

    def test_react_import_is_frontend(self) -> None:
        """Files importing React are detected via imports."""
        sym = make_symbol(
            "handler", path="src/utils/hooks.ts",
            meta={"imports": [{"module": "react", "name": "useState"}]},
        )
        assert _is_frontend_file(sym) is True

    def test_angular_import_is_frontend(self) -> None:
        """Files importing Angular are detected via imports."""
        sym = make_symbol(
            "onClick", path="src/app.service.ts",
            meta={"imports": [{"module": "@angular/core", "name": "Component"}]},
        )
        assert _is_frontend_file(sym) is True


class TestFrontendRouteSupression:
    """Tests for frontend route suppression in entrypoint detection (WI-ronik)."""

    def test_frontend_route_gets_low_confidence(self) -> None:
        """Route concept in a React component file gets near-zero confidence."""
        sym = make_symbol(
            "handleRemove", path="src/components/Settings.tsx",
            language="typescript",
            meta={"concepts": [{"concept": "route", "method": "DELETE", "path": "/:uid"}]},
        )
        eps = detect_entrypoints([sym], [])
        route_eps = [e for e in eps if e.kind == EntrypointKind.HTTP_ROUTE]
        # Should either be absent (below threshold) or have very low confidence
        if route_eps:
            assert route_eps[0].confidence <= 0.10, (
                f"Frontend route should have confidence <= 0.10, got {route_eps[0].confidence}"
            )

    def test_backend_route_unaffected(self) -> None:
        """Route concept in a backend file keeps normal confidence."""
        sym = make_symbol(
            "getUsers", path="src/api/users.ts",
            language="typescript",
            meta={"concepts": [{"concept": "route", "method": "GET", "path": "/users"}]},
        )
        eps = detect_entrypoints([sym], [])
        route_eps = [e for e in eps if e.kind == EntrypointKind.HTTP_ROUTE]
        assert len(route_eps) == 1
        assert route_eps[0].confidence == 0.95


class TestEntrypointConceptDetection:
    """Tests for generic 'entrypoint' concept -> entrypoint mapping.

    The 'entrypoint' concept is used by electron.yaml, cli.yaml, cli-go.yaml,
    cli-ruby.yaml, and akka-http.yaml but was previously silently ignored by
    _detect_from_concepts(). These tests verify it is now handled.
    """

    def test_entrypoint_concept_electron(self) -> None:
        """entrypoint concept with electron framework -> ELECTRON_MAIN."""
        sym = make_symbol(
            "main",
            path="src/main.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "entrypoint", "framework": "electron"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.ELECTRON_MAIN]
        assert len(ep) == 1
        assert ep[0].confidence >= 0.90
        assert "electron" in ep[0].label.lower()

    def test_entrypoint_concept_generic(self) -> None:
        """entrypoint concept without specific framework -> MAIN_FUNCTION."""
        sym = make_symbol(
            "bootstrap",
            path="src/app.py",
            language="python",
            meta={"concepts": [
                {"concept": "entrypoint", "framework": "cli"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.MAIN_FUNCTION]
        assert len(ep) == 1
        assert ep[0].confidence >= 0.90

    def test_entrypoint_concept_no_framework(self) -> None:
        """entrypoint concept with no framework field -> MAIN_FUNCTION."""
        sym = make_symbol(
            "init",
            path="src/init.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "entrypoint"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.MAIN_FUNCTION]
        assert len(ep) == 1
        assert ep[0].confidence >= 0.90

    def test_duplicate_entrypoint_concepts_deduplicated(self) -> None:
        """Multiple entrypoint concepts on same symbol produce one entry."""
        sym = make_symbol(
            "main",
            path="src/main.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "entrypoint", "framework": "electron"},
                {"concept": "entrypoint", "framework": "electron"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        main_eps = [e for e in entrypoints
                    if e.kind in (EntrypointKind.ELECTRON_MAIN, EntrypointKind.MAIN_FUNCTION)]
        assert len(main_eps) == 1


class TestSpaBootstrapConceptDetection:
    """Tests for SPA bootstrap entrypoint detection.

    SPA frameworks bootstrap via createRoot(), ReactDOM.render(), hydrateRoot().
    These should be detected as app_bootstrap concepts and mapped to
    SPA_BOOTSTRAP entrypoint kind.
    """

    def test_app_bootstrap_concept(self) -> None:
        """app_bootstrap concept -> SPA_BOOTSTRAP entrypoint."""
        sym = make_symbol(
            "main",
            path="src/index.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "react"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1
        assert ep[0].confidence >= 0.90
        assert "bootstrap" in ep[0].label.lower() or "spa" in ep[0].label.lower()

    def test_app_bootstrap_no_framework(self) -> None:
        """app_bootstrap concept without framework uses generic label."""
        sym = make_symbol(
            "init",
            path="src/main.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1

    def test_duplicate_app_bootstrap_deduplicated(self) -> None:
        """Multiple app_bootstrap concepts on same symbol produce one entry."""
        sym = make_symbol(
            "root",
            path="src/index.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "react"},
                {"concept": "app_bootstrap", "framework": "react"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1


    def test_solid_preferred_over_react_for_bare_createroot(self) -> None:
        """When both React and Solid claim app_bootstrap, prefer Solid.

        Solid.js uses bare createRoot() for reactive scope creation. React
        also matches bare createRoot() via its pattern. When both are present,
        the label should say "Solid" not "React" because Solid's createRoot
        is the more specific match.
        """
        sym = make_symbol(
            "app",
            path="src/index.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "react"},
                {"concept": "app_bootstrap", "framework": "solid"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1
        assert "solid" in ep[0].label.lower()
        assert "react" not in ep[0].label.lower()

    def test_solid_wins_even_when_react_is_first(self) -> None:
        """Framework priority applies regardless of concept order."""
        sym = make_symbol(
            "app",
            path="src/main.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "react"},
                {"concept": "app_bootstrap", "framework": "solid"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1
        assert "solid" in ep[0].label.lower()

    def test_react_only_uses_react_label(self) -> None:
        """When only React claims app_bootstrap, label says React."""
        sym = make_symbol(
            "root",
            path="src/index.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "react"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1
        assert "react" in ep[0].label.lower()

    def test_solid_only_uses_solid_label(self) -> None:
        """When only Solid claims app_bootstrap, label says Solid."""
        sym = make_symbol(
            "entry",
            path="src/index.tsx",
            language="typescript",
            meta={"concepts": [
                {"concept": "app_bootstrap", "framework": "solid"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.SPA_BOOTSTRAP]
        assert len(ep) == 1
        assert "solid" in ep[0].label.lower()


class TestPickBestBootstrapFramework:
    """Unit tests for _pick_best_bootstrap_framework helper."""

    def test_empty_concepts(self) -> None:
        """No concepts returns empty string."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        assert _pick_best_bootstrap_framework([]) == ""

    def test_single_framework(self) -> None:
        """Single app_bootstrap returns that framework."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [{"concept": "app_bootstrap", "framework": "vue"}]
        assert _pick_best_bootstrap_framework(concepts) == "vue"

    def test_solid_beats_react(self) -> None:
        """Solid takes priority over React."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [
            {"concept": "app_bootstrap", "framework": "react"},
            {"concept": "app_bootstrap", "framework": "solid"},
        ]
        assert _pick_best_bootstrap_framework(concepts) == "solid"

    def test_svelte_beats_react(self) -> None:
        """Svelte takes priority over React."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [
            {"concept": "app_bootstrap", "framework": "react"},
            {"concept": "app_bootstrap", "framework": "svelte"},
        ]
        assert _pick_best_bootstrap_framework(concepts) == "svelte"

    def test_non_bootstrap_concepts_ignored(self) -> None:
        """Non-app_bootstrap concepts don't affect the result."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [
            {"concept": "route", "framework": "express"},
            {"concept": "app_bootstrap", "framework": "react"},
        ]
        assert _pick_best_bootstrap_framework(concepts) == "react"

    def test_non_dict_concepts_ignored(self) -> None:
        """Non-dict entries in concepts are skipped."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [
            "not a dict",
            {"concept": "app_bootstrap", "framework": "solid"},
        ]
        assert _pick_best_bootstrap_framework(concepts) == "solid"

    def test_unknown_frameworks_use_first(self) -> None:
        """When frameworks aren't in priority list, use first."""
        from hypergumbo_core.entrypoints import _pick_best_bootstrap_framework
        concepts = [
            {"concept": "app_bootstrap", "framework": "alpine"},
            {"concept": "app_bootstrap", "framework": "preact"},
        ]
        assert _pick_best_bootstrap_framework(concepts) == "alpine"


class TestIpcHandlerConceptDetection:
    """Tests for ipc_handler concept -> EVENT_HANDLER entrypoint mapping.

    The ipc_handler concept is used by electron.yaml (ipcMain.on/handle)
    and tauri.yaml (#[tauri::command]) for cross-language IPC handlers.
    """

    def test_ipc_handler_concept_tauri(self) -> None:
        """ipc_handler concept with tauri framework -> EVENT_HANDLER."""
        sym = make_symbol(
            "greet",
            path="src-tauri/src/main.rs",
            language="rust",
            meta={"concepts": [
                {"concept": "ipc_handler", "framework": "tauri"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(ep) == 1
        assert ep[0].confidence >= 0.90
        assert "tauri" in ep[0].label.lower()
        assert "ipc" in ep[0].label.lower()

    def test_ipc_handler_concept_electron(self) -> None:
        """ipc_handler concept with electron framework -> EVENT_HANDLER."""
        sym = make_symbol(
            "handleFileOpen",
            path="src/main.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "ipc_handler", "framework": "electron"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(ep) == 1
        assert "electron" in ep[0].label.lower()

    def test_ipc_handler_no_framework(self) -> None:
        """ipc_handler concept without framework uses generic label."""
        sym = make_symbol(
            "handleMessage",
            path="src/handler.ts",
            language="typescript",
            meta={"concepts": [
                {"concept": "ipc_handler"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(ep) == 1
        assert "ipc" in ep[0].label.lower()

    def test_duplicate_ipc_handler_deduplicated(self) -> None:
        """Multiple ipc_handler concepts on same symbol produce one entry."""
        sym = make_symbol(
            "greet",
            path="src-tauri/src/main.rs",
            language="rust",
            meta={"concepts": [
                {"concept": "ipc_handler", "framework": "tauri"},
                {"concept": "ipc_handler", "framework": "tauri"},
            ]},
        )
        entrypoints = detect_entrypoints([sym], [])
        ep = [e for e in entrypoints if e.kind == EntrypointKind.EVENT_HANDLER]
        assert len(ep) == 1


# ==================== DIVERSITY CAP TESTS ====================


class TestDiversityCap:
    """Tests for per-kind diversity capping of entrypoints."""

    def _make_ep(self, kind: EntrypointKind, idx: int) -> Entrypoint:
        """Create a test entrypoint."""
        return Entrypoint(
            symbol_id=f"test:{idx}",
            kind=kind,
            confidence=0.9 - idx * 0.001,
            label=f"ep_{idx}",
        )

    def test_dominant_kind_capped_when_others_present(self) -> None:
        """When multiple kinds exist, no single kind dominates the cap."""
        # 80 GraphQL + 5 HTTP, cap=50
        # Without diversity: top 50 would be 50 GraphQL, 0 HTTP
        # With diversity: max 20 GraphQL (40%), then HTTP fills in
        eps = (
            [self._make_ep(EntrypointKind.GRAPHQL_SERVER, i) for i in range(80)]
            + [self._make_ep(EntrypointKind.HTTP_ROUTE, 80 + i) for i in range(5)]
        )
        result = _diversity_cap(eps, 50)
        assert len(result) <= 50
        graphql_count = sum(1 for e in result if e.kind == EntrypointKind.GRAPHQL_SERVER)
        http_count = sum(1 for e in result if e.kind == EntrypointKind.HTTP_ROUTE)
        # GraphQL capped at 40% in diversity pass, but overflow fills remaining
        # Still, HTTP should be represented
        assert http_count == 5  # All 5 HTTP routes should be included
        assert graphql_count > 0

    def test_diverse_kinds_not_capped(self) -> None:
        """When kinds are diverse, all fit within cap."""
        eps = [
            self._make_ep(EntrypointKind.HTTP_ROUTE, 0),
            self._make_ep(EntrypointKind.CLI_MAIN, 1),
            self._make_ep(EntrypointKind.GRAPHQL_SERVER, 2),
            self._make_ep(EntrypointKind.DJANGO_VIEW, 3),
        ]
        result = _diversity_cap(eps, 10)
        assert len(result) == 4

    def test_overflow_fills_remaining_slots(self) -> None:
        """Overflow entries fill remaining slots after diversity round."""
        eps = (
            [self._make_ep(EntrypointKind.HTTP_ROUTE, i) for i in range(30)]
            + [self._make_ep(EntrypointKind.GRAPHQL_SERVER, 30 + i) for i in range(30)]
        )
        result = _diversity_cap(eps, 50)
        assert len(result) == 50
        http_count = sum(1 for e in result if e.kind == EntrypointKind.HTTP_ROUTE)
        graphql_count = sum(1 for e in result if e.kind == EntrypointKind.GRAPHQL_SERVER)
        assert http_count > 0
        assert graphql_count > 0

    def test_cap_of_one(self) -> None:
        """Cap=1 returns exactly 1."""
        eps = [self._make_ep(EntrypointKind.HTTP_ROUTE, 0)]
        result = _diversity_cap(eps, 1)
        assert len(result) == 1

    def test_empty_list(self) -> None:
        """Empty input returns empty."""
        result = _diversity_cap([], 50)
        assert result == []

    def test_fewer_than_cap(self) -> None:
        """When total < cap, all are returned."""
        eps = [self._make_ep(EntrypointKind.HTTP_ROUTE, i) for i in range(5)]
        result = _diversity_cap(eps, 50)
        assert len(result) == 5
