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


class TestDjangoSignalReceiverEdges:
    """Test Django signal receiver edge detection.

    When a function is decorated with @receiver(signal, ...), we should create
    a signal_receiver edge from the signal to the decorated function.
    """

    def test_receiver_decorator_creates_signal_receiver_edge(
        self, tmp_path: Path
    ) -> None:
        """@receiver(post_save) creates signal_receiver edge from post_save to handler."""
        code = '''
# Simulate Django signal imports
class Signal:
    pass

post_save = Signal()
post_delete = Signal()

def receiver(signal, **kwargs):
    def decorator(func):
        return func
    return decorator

@receiver(post_save)
def my_handler(sender, instance, **kwargs):
    pass
'''
        py_file = tmp_path / "signals.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Find signal_receiver edges
        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        assert len(signal_edges) >= 1, "Expected signal_receiver edge"

        # The edge should be from post_save to my_handler
        edge = signal_edges[0]
        assert "post_save" in edge.src
        assert "my_handler" in edge.dst

    def test_receiver_with_sender_kwarg(self, tmp_path: Path) -> None:
        """@receiver(signal, sender=Model) still creates signal_receiver edge."""
        code = '''
class Signal:
    pass

post_save = Signal()

def receiver(signal, **kwargs):
    def decorator(func):
        return func
    return decorator

class User:
    pass

@receiver(post_save, sender=User)
def user_saved_handler(sender, instance, **kwargs):
    pass
'''
        py_file = tmp_path / "signals_with_sender.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        assert len(signal_edges) >= 1, "Expected signal_receiver edge"
        edge = signal_edges[0]
        assert "post_save" in edge.src
        assert "user_saved_handler" in edge.dst

    def test_multiple_signals_in_receiver(self, tmp_path: Path) -> None:
        """@receiver([signal1, signal2]) creates multiple signal_receiver edges."""
        code = '''
class Signal:
    pass

post_save = Signal()
post_delete = Signal()

def receiver(signals, **kwargs):
    def decorator(func):
        return func
    return decorator

@receiver([post_save, post_delete])
def multi_signal_handler(sender, instance, **kwargs):
    pass
'''
        py_file = tmp_path / "multi_signals.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        # Should have edges from both signals
        assert len(signal_edges) >= 2, "Expected two signal_receiver edges"

        signal_srcs = {e.src for e in signal_edges}
        assert any("post_save" in src for src in signal_srcs)
        assert any("post_delete" in src for src in signal_srcs)

    def test_receiver_no_args_no_crash(self, tmp_path: Path) -> None:
        """@receiver() with no args should not crash."""
        code = '''
def receiver(*args, **kwargs):
    def decorator(func):
        return func
    return decorator

@receiver()  # No signal argument - should be handled gracefully
def handler(sender, **kwargs):
    pass
'''
        py_file = tmp_path / "receiver_no_args.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        # Should not have signal_receiver edges (no signal specified)
        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]
        assert len(signal_edges) == 0

    def test_receiver_via_attribute_access(self, tmp_path: Path) -> None:
        """@dispatch.receiver(signal) works with attribute access."""
        code = '''
class Signal:
    pass

post_save = Signal()

class dispatch:
    @staticmethod
    def receiver(signal):
        def decorator(func):
            return func
        return decorator

@dispatch.receiver(post_save)
def handler(sender, **kwargs):
    pass
'''
        py_file = tmp_path / "attribute_receiver.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        assert len(signal_edges) >= 1, "Expected signal_receiver edge"
        edge = signal_edges[0]
        assert "post_save" in edge.src
        assert "handler" in edge.dst

    def test_receiver_with_imported_signal(self, tmp_path: Path) -> None:
        """Signal imported from another module creates signal_receiver edge."""
        # Create signals module
        signals_code = '''
class Signal:
    pass

post_save = Signal()
'''
        signals_file = tmp_path / "signals.py"
        signals_file.write_text(signals_code)

        # Create handlers module that imports the signal
        handlers_code = '''
from signals import post_save

def receiver(signal, **kwargs):
    def decorator(func):
        return func
    return decorator

@receiver(post_save)
def my_handler(sender, instance, **kwargs):
    pass
'''
        handlers_file = tmp_path / "handlers.py"
        handlers_file.write_text(handlers_code)

        result = analyze_python(tmp_path)

        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        assert len(signal_edges) >= 1, "Expected signal_receiver edge from imported signal"
        edge = signal_edges[0]
        assert "post_save" in edge.src
        assert "my_handler" in edge.dst

    def test_receiver_with_function_signal_resolved(self, tmp_path: Path) -> None:
        """Signal that is a function gets properly resolved to a symbol.

        This tests the code path where signal_symbol is found in local_symbols.
        """
        code = '''
# Signal is defined as a function (could be a factory or class-based signal)
def post_save(*args, **kwargs):
    pass

def receiver(signal, **kwargs):
    def decorator(func):
        return func
    return decorator

@receiver(post_save)
def my_handler(sender, **kwargs):
    pass
'''
        py_file = tmp_path / "function_signal.py"
        py_file.write_text(code)

        result = analyze_python(tmp_path)

        signal_edges = [
            e for e in result.edges
            if e.edge_type == "signal_receiver"
        ]

        assert len(signal_edges) >= 1, "Expected signal_receiver edge"
        edge = signal_edges[0]
        # The edge src should reference the post_save function symbol (resolved path)
        assert "post_save" in edge.src
        assert "function" in edge.src  # Should be a function symbol, not unresolved
        assert "my_handler" in edge.dst
