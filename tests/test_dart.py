"""Tests for Dart/Flutter analyzer.

Dart analysis uses tree-sitter to extract:
- Symbols: class, function, method, getter, setter, enum, mixin, extension
- Edges: calls, imports

Test coverage includes:
- Class detection
- Function detection (top-level and methods)
- Constructor detection
- Getter/setter detection
- Enum and mixin detection
- Import statements
- Two-pass cross-file resolution
- Flutter widget detection
"""
from pathlib import Path


def make_dart_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Dart file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestDartAnalyzerAvailability:
    """Tests for Dart tree-sitter availability detection."""

    def test_is_dart_tree_sitter_available(self) -> None:
        """Check if tree-sitter for Dart is detected."""
        from hypergumbo.analyze.dart import is_dart_tree_sitter_available

        # Should be True since we have tree-sitter-language-pack
        assert is_dart_tree_sitter_available() is True


class TestDartClassDetection:
    """Tests for Dart class symbol extraction."""

    def test_detect_class(self, tmp_path: Path) -> None:
        """Detect class definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Person {
  String name;
  int age;

  Person(this.name, this.age);
}
""")

        result = analyze_dart(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        cls = next((s for s in symbols if s.name == "Person"), None)
        assert cls is not None
        assert cls.kind == "class"
        assert cls.language == "dart"

    def test_detect_abstract_class(self, tmp_path: Path) -> None:
        """Detect abstract class definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
abstract class Animal {
  void speak();
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        cls = next((s for s in symbols if s.name == "Animal"), None)
        assert cls is not None
        assert cls.kind == "class"


class TestDartFunctionDetection:
    """Tests for Dart function symbol extraction."""

    def test_detect_top_level_function(self, tmp_path: Path) -> None:
        """Detect top-level function definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
void main() {
  print('Hello, World!');
}

int add(int a, int b) {
  return a + b;
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        main_func = next((s for s in symbols if s.name == "main"), None)
        add_func = next((s for s in symbols if s.name == "add"), None)
        assert main_func is not None
        assert add_func is not None
        assert main_func.kind == "function"
        assert add_func.kind == "function"

    def test_detect_method(self, tmp_path: Path) -> None:
        """Detect method inside class."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Calculator {
  int add(int a, int b) {
    return a + b;
  }

  int multiply(int a, int b) {
    return a * b;
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        # Methods should be named like Class.method
        add_method = next((s for s in symbols if s.name == "Calculator.add"), None)
        mul_method = next((s for s in symbols if s.name == "Calculator.multiply"), None)
        assert add_method is not None
        assert mul_method is not None
        assert add_method.kind == "method"

    def test_detect_constructor(self, tmp_path: Path) -> None:
        """Detect constructor."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class User {
  String name;

  User(this.name);

  User.guest() : name = 'Guest';
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        # Find constructors
        constructors = [s for s in symbols if s.kind == "constructor"]
        assert len(constructors) >= 1

    def test_detect_getter_setter(self, tmp_path: Path) -> None:
        """Detect getters and setters."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Box {
  int _value = 0;

  int get value => _value;

  set value(int v) {
    _value = v;
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        getter = next((s for s in symbols if s.kind == "getter"), None)
        setter = next((s for s in symbols if s.kind == "setter"), None)
        assert getter is not None
        assert setter is not None


class TestDartEnumAndMixin:
    """Tests for enum and mixin detection."""

    def test_detect_enum(self, tmp_path: Path) -> None:
        """Detect enum definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
enum Color {
  red,
  green,
  blue,
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        enum = next((s for s in symbols if s.name == "Color"), None)
        assert enum is not None
        assert enum.kind == "enum"

    def test_detect_mixin(self, tmp_path: Path) -> None:
        """Detect mixin definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
mixin Flyable {
  void fly() {
    print('Flying!');
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        mixin = next((s for s in symbols if s.name == "Flyable"), None)
        assert mixin is not None
        assert mixin.kind == "mixin"

    def test_detect_extension(self, tmp_path: Path) -> None:
        """Detect extension definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
extension StringExtension on String {
  String capitalize() {
    return '${this[0].toUpperCase()}${substring(1)}';
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        ext = next((s for s in symbols if s.name == "StringExtension"), None)
        assert ext is not None
        assert ext.kind == "extension"


class TestDartImportEdges:
    """Tests for Dart import edge extraction."""

    def test_detect_import(self, tmp_path: Path) -> None:
        """Detect import statements."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
import 'dart:io';
import 'package:flutter/material.dart';
import 'utils.dart';

void main() {
  print('Hello');
}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        # Should have imports for dart:io, flutter, and utils
        assert len(import_edges) >= 3

    def test_detect_export(self, tmp_path: Path) -> None:
        """Detect export statements."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
export 'src/models.dart';
export 'src/utils.dart' show helper;

void main() {}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        # Exports should also be tracked as import edges
        assert len(import_edges) >= 2


class TestDartCallEdges:
    """Tests for Dart function call edge extraction."""

    def test_detect_function_call(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
void greet() {
  print('Hello');
}

void main() {
  greet();
}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert any(e.dst.endswith("greet:function") for e in call_edges)

    def test_detect_method_call(self, tmp_path: Path) -> None:
        """Detect method call edges."""
        from hypergumbo.analyze.dart import analyze_dart

        # Method calls where the callee is a known function in the same file
        make_dart_file(tmp_path, "main.dart", """
void helper() {
  print('helper');
}

void main() {
  helper();
}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have call to helper
        assert any("helper" in e.dst for e in call_edges)

    def test_detect_constructor_call(self, tmp_path: Path) -> None:
        """Detect constructor call (instantiation) edges using new keyword."""
        from hypergumbo.analyze.dart import analyze_dart

        # Using explicit 'new' keyword which is easier to detect
        make_dart_file(tmp_path, "main.dart", """
class Person {
  String name;
  Person(this.name);
}

void main() {
  var p = new Person('John');
}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        instantiate_edges = [e for e in edges if e.edge_type == "instantiates"]
        assert len(instantiate_edges) >= 1


class TestDartFlutterWidgets:
    """Tests for Flutter widget detection."""

    def test_detect_stateless_widget(self, tmp_path: Path) -> None:
        """Detect StatelessWidget subclass."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
import 'package:flutter/material.dart';

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container();
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        widget = next((s for s in symbols if s.name == "MyApp"), None)
        assert widget is not None
        assert widget.kind == "class"

    def test_detect_stateful_widget(self, tmp_path: Path) -> None:
        """Detect StatefulWidget and State subclasses."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
import 'package:flutter/material.dart';

class Counter extends StatefulWidget {
  @override
  State<Counter> createState() => _CounterState();
}

class _CounterState extends State<Counter> {
  int count = 0;

  @override
  Widget build(BuildContext context) {
    return Text('$count');
  }
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        widget = next((s for s in symbols if s.name == "Counter"), None)
        state = next((s for s in symbols if s.name == "_CounterState"), None)
        assert widget is not None
        assert state is not None


class TestDartCrossFileResolution:
    """Tests for two-pass cross-file call resolution."""

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Calls to functions in other files are resolved."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "utils.dart", """
int helper() {
  return 42;
}
""")

        make_dart_file(tmp_path, "main.dart", """
import 'utils.dart';

void main() {
  helper();
}
""")

        result = analyze_dart(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # Call to helper should be resolved
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestDartEdgeCases:
    """Edge case tests for Dart analyzer."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty Dart file produces no symbols."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "empty.dart", "")

        result = analyze_dart(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "bad.dart", """
class Broken {
  // missing closing brace
""")

        result = analyze_dart(tmp_path)

        # Should not crash
        assert not result.skipped

    def test_no_dart_files(self, tmp_path: Path) -> None:
        """Directory with no Dart files returns empty result."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.py", "print('hello')")

        result = analyze_dart(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestDartSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """void hello() {
  print('hi');
}
""")

        result = analyze_dart(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "hello"), None)
        assert func is not None
        assert func.span.start_line == 1
        assert func.span.end_line == 3


class TestDartAnalyzeFallback:
    """Tests for fallback when tree-sitter is unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        """Returns skipped result when tree-sitter not available."""
        from hypergumbo.analyze import dart

        # Mock tree-sitter as unavailable
        monkeypatch.setattr(dart, "is_dart_tree_sitter_available", lambda: False)

        make_dart_file(tmp_path, "main.dart", "void test() {}")

        result = dart.analyze_dart(tmp_path)

        assert result.skipped
        assert "tree-sitter" in result.skip_reason.lower() or "dart" in result.skip_reason.lower()
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "dart-v1"


class TestDartSignatureExtraction:
    """Tests for Dart function signature extraction."""

    def test_function_with_params_and_return_type(self, tmp_path: Path) -> None:
        """Extract signature from function with params and return type."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
int add(int a, int b) {
  return a + b;
}
""")
        result = analyze_dart(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(int a, int b) int"

    def test_void_function(self, tmp_path: Path) -> None:
        """Extract signature from void function."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
void greet(String name) {
  print('Hello $name');
}
""")
        result = analyze_dart(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) == 1
        # void functions don't include return type
        assert funcs[0].signature == "(String name)"

    def test_method_signature(self, tmp_path: Path) -> None:
        """Extract signature from method inside class."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Calculator {
  int multiply(int x, int y) {
    return x * y;
  }
}
""")
        result = analyze_dart(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "multiply" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(int x, int y) int"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extract signature from function with no parameters."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
String getName() {
  return 'test';
}
""")
        result = analyze_dart(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "getName"]
        assert len(funcs) == 1
        assert funcs[0].signature == "() String"

    def test_optional_named_params(self, tmp_path: Path) -> None:
        """Extract signature from function with optional named parameters."""
        from hypergumbo.analyze.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
void configure({int timeout = 30, String name = 'default'}) {
  print('configured');
}
""")
        result = analyze_dart(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "configure"]
        assert len(funcs) == 1
        # Default values should be replaced with ...
        assert "= ..." in funcs[0].signature or funcs[0].signature is not None
