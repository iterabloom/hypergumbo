"""Tests for TypeScript decorator edge detection (INV-012 scope expansion).

Verifies that decorator applications create edges in the call graph for TypeScript.
"""
import pytest
from pathlib import Path

from hypergumbo_lang_mainstream.js_ts import analyze_javascript, is_tree_sitter_available


pytestmark = pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter-typescript not available"
)


class TestTypeScriptDecoratorEdges:
    """Test decorator edge detection for TypeScript."""

    def test_class_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A @decorator on a class creates a decorated_by edge."""
        code = '''
function Injectable() {
    return function(target: any) { return target; };
}

@Injectable()
class UserService {
    getUsers() { return []; }
}
'''
        ts_file = tmp_path / "service.ts"
        ts_file.write_text(code)

        result = analyze_javascript(tmp_path)

        # Find the decorated_by edge
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from UserService to Injectable
        edge = decorated_by_edges[0]
        assert "UserService" in edge.src
        assert "Injectable" in edge.dst

    def test_multiple_class_decorators_create_multiple_edges(self, tmp_path: Path) -> None:
        """Multiple decorators create multiple edges."""
        code = '''
function Injectable() {
    return function(target: any) { return target; };
}

function Controller(path: string) {
    return function(target: any) { return target; };
}

@Injectable()
@Controller('/users')
class UserController {
}
'''
        ts_file = tmp_path / "controller.ts"
        ts_file.write_text(code)

        result = analyze_javascript(tmp_path)

        # Find decorated_by edges for UserController
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "UserController" in e.src
        ]

        assert len(decorated_by_edges) >= 2, "Expected two decorated_by edges for stacked decorators"

    def test_method_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A @decorator on a method creates a decorated_by edge."""
        code = '''
function Get(path: string) {
    return function(target: any, key: string) { };
}

class UserController {
    @Get('/users')
    getUsers() { return []; }
}
'''
        ts_file = tmp_path / "controller.ts"
        ts_file.write_text(code)

        result = analyze_javascript(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge for method decorator"

        # The edge should reference getUsers and Get
        edge = decorated_by_edges[0]
        assert "getUsers" in edge.src
        assert "Get" in edge.dst
