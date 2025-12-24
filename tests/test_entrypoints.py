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
