"""Tests for decorator edge detection (INV-012).

Verifies that decorator applications create edges in the call graph:
- @decorator creates a "decorated_by" edge from the decorated function to the decorator
- @app.get() creates edges to both the decorator method and the app object
"""
import pytest
from pathlib import Path
import tempfile

from hypergumbo_lang_mainstream.py import analyze_python


class TestDecoratorEdges:
    """Test decorator edge detection."""

    def test_simple_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A simple @decorator creates a decorated_by edge."""
        code = '''
def my_decorator(func):
    return func

@my_decorator
def my_function():
    pass
'''
        py_file = tmp_path / "decorators.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find the decorated_by edge
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from my_function to my_decorator
        edge = decorated_by_edges[0]
        assert "my_function" in edge.src
        assert "my_decorator" in edge.dst

    def test_method_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A method decorator like @app.get() creates an edge."""
        code = '''
class App:
    def get(self, path):
        def decorator(func):
            return func
        return decorator

app = App()

@app.get("/users")
def list_users():
    pass
'''
        py_file = tmp_path / "method_decorator.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should reference list_users and app.get
        edge = decorated_by_edges[0]
        assert "list_users" in edge.src

    def test_class_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A class decorator creates a decorated_by edge."""
        code = '''
def dataclass(cls):
    return cls

@dataclass
class User:
    name: str
'''
        py_file = tmp_path / "class_decorator.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from User to dataclass
        edge = decorated_by_edges[0]
        assert "User" in edge.src
        assert "dataclass" in edge.dst

    def test_stacked_decorators_create_multiple_edges(self, tmp_path: Path) -> None:
        """Multiple decorators create multiple edges."""
        code = '''
def decorator_a(func):
    return func

def decorator_b(func):
    return func

@decorator_a
@decorator_b
def my_function():
    pass
'''
        py_file = tmp_path / "stacked.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find decorated_by edges for my_function
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "my_function" in e.src
        ]

        assert len(decorated_by_edges) >= 2, "Expected two decorated_by edges for stacked decorators"

    def test_decorator_with_arguments_creates_edge(self, tmp_path: Path) -> None:
        """Decorator with arguments @decorator(args) creates edge."""
        code = '''
def route(path):
    def decorator(func):
        return func
    return decorator

@route("/api/users")
def get_users():
    pass
'''
        py_file = tmp_path / "decorator_args.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        edge = decorated_by_edges[0]
        assert "get_users" in edge.src
        assert "route" in edge.dst

    def test_classmethod_decorator_creates_edge(self, tmp_path: Path) -> None:
        """A @ClassName.method decorator resolves to the class method.

        Tests the code path where the decorator receiver is a class name
        (not an instance variable) and the method is in local_symbols.
        """
        code = '''
class Registry:
    @staticmethod
    def register(func):
        return func

@Registry.register
def my_handler():
    pass
'''
        py_file = tmp_path / "classmethod_decorator.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from my_handler to Registry.register
        edge = decorated_by_edges[0]
        assert "my_handler" in edge.src
        assert "Registry.register" in edge.dst
