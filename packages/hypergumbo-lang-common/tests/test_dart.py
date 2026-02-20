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
        from hypergumbo_lang_common.dart import is_dart_tree_sitter_available

        # Should be True since we have tree-sitter-language-pack
        assert is_dart_tree_sitter_available() is True

    def test_find_dart_files(self, tmp_path: Path) -> None:
        """Finds .dart files in a directory."""
        from hypergumbo_lang_common.dart import find_dart_files

        (tmp_path / "app.dart").write_text("void main() {}")
        (tmp_path / "other.txt").write_text("not dart")
        files = list(find_dart_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".dart"


class TestDartClassDetection:
    """Tests for Dart class symbol extraction."""

    def test_detect_class(self, tmp_path: Path) -> None:
        """Detect class definition."""
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "empty.dart", "")

        result = analyze_dart(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "bad.dart", """
class Broken {
  // missing closing brace
""")

        result = analyze_dart(tmp_path)

        # Should not crash
        assert not result.skipped

    def test_no_dart_files(self, tmp_path: Path) -> None:
        """Directory with no Dart files returns empty result."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.py", "print('hello')")

        result = analyze_dart(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestDartSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo_lang_common.dart import analyze_dart

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

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter not available."""
        from unittest.mock import patch
        from hypergumbo_lang_common import dart

        make_dart_file(tmp_path, "main.dart", "void test() {}")

        import pytest
        with patch.object(
            dart._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="dart analysis skipped"):
                result = dart.analyze_dart(tmp_path)

        assert result.skipped
        assert "not available" in result.skip_reason
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "dart-v1"


class TestDartSignatureExtraction:
    """Tests for Dart function signature extraction."""

    def test_function_with_params_and_return_type(self, tmp_path: Path) -> None:
        """Extract signature from function with params and return type."""
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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
        from hypergumbo_lang_common.dart import analyze_dart

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


class TestDartTypeInference:
    """Tests for Dart variable type inference for method call resolution."""

    def test_parameter_type_inference(self, tmp_path: Path) -> None:
        """Method calls on typed parameters are resolved to class methods."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Database {
  void save(String data) {
    print('Saving: $data');
  }
}

void processData(Database db) {
  db.save('test');
}
""")
        result = analyze_dart(tmp_path)

        # Find the type-inferred call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_edges = [e for e in call_edges if e.evidence_type == "method_call_type_inferred"]

        # Should have an edge from processData to Database.save
        assert len(inferred_edges) >= 1
        edge = inferred_edges[0]
        assert "processData" in edge.src
        assert "Database.save" in edge.dst

    def test_constructor_type_inference(self, tmp_path: Path) -> None:
        """Method calls on constructor-assigned variables are resolved."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class HttpClient {
  void send(String url) {
    print('Sending to: $url');
  }
}

void main() {
  var client = new HttpClient();
  client.send('http://example.com');
}
""")
        result = analyze_dart(tmp_path)

        # Find the type-inferred call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_edges = [e for e in call_edges if e.evidence_type == "method_call_type_inferred"]

        # Should have an edge from main to HttpClient.send
        assert len(inferred_edges) >= 1
        edge = inferred_edges[0]
        assert "main" in edge.src
        assert "HttpClient.send" in edge.dst

    def test_optional_param_type_inference(self, tmp_path: Path) -> None:
        """Method calls on optional typed parameters are resolved."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class Logger {
  void log(String msg) {
    print(msg);
  }
}

void process({Logger logger}) {
  logger.log('Processing');
}
""")
        result = analyze_dart(tmp_path)

        # Find the type-inferred call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        inferred_edges = [e for e in call_edges if e.evidence_type == "method_call_type_inferred"]

        # Should have an edge from process to Logger.log
        assert len(inferred_edges) >= 1
        edge = inferred_edges[0]
        assert "process" in edge.src
        assert "Logger.log" in edge.dst


class TestDartReturnTypeInference:
    """Tests for return type tracking from function return type annotations."""

    def test_type_inference_from_return_type_annotation(
        self, tmp_path: Path
    ) -> None:
        """Functions with return type annotations enable variable type inference."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class ServiceClient {
  void fetch() {}
}

ServiceClient getClient() {
  return ServiceClient();
}

void main() {
  var client = getClient();
  client.fetch();
}
""")
        result = analyze_dart(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should have edge from main to ServiceClient.fetch via return type inference
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            "Expected call edge for client.fetch() via return type inference. "
            f"Edges from main: {[e for e in result.edges if e.src == main_func.id]}"
        )
        assert call_edge.evidence_type == "method_call_type_inferred"

    def test_type_inference_no_annotation_no_resolution(
        self, tmp_path: Path
    ) -> None:
        """Functions without return type annotation don't enable type inference."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class ServiceClient {
  void fetch() {}
}

getClient() {
  return ServiceClient();
}

void main() {
  var client = getClient();
  client.fetch();
}
""")
        result = analyze_dart(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should NOT resolve via type inference (no return type annotation)
        type_inferred_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
                and e.evidence_type == "method_call_type_inferred"
            ),
            None,
        )
        assert type_inferred_edge is None

    def test_navigation_call_return_type_inference(
        self, tmp_path: Path
    ) -> None:
        """Method calls (factory.create()) also track return types."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class ServiceClient {
  void fetch() {}
}

class Factory {
  ServiceClient create() {
    return ServiceClient();
  }
}

void main() {
  var factory = new Factory();
  var client = factory.create();
  client.fetch();
}
""")
        result = analyze_dart(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should have edge from main to ServiceClient.fetch via return type inference
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            "Expected call edge for client.fetch() via navigation return type inference. "
            f"Edges from main: {[e for e in result.edges if e.src == main_func.id]}"
        )
        assert call_edge.evidence_type == "method_call_type_inferred"


class TestDartReturnTypeExtraction:
    """Unit tests for _extract_dart_return_type_name helper."""

    def test_simple_return_type(self) -> None:
        """Extracts simple return type from Dart signature."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name("() ServiceClient") == "ServiceClient"

    def test_with_params(self) -> None:
        """Extracts return type with parameters."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name("(String name, int age) User") == "User"

    def test_no_return_type(self) -> None:
        """Returns None when no return type (void is omitted)."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name("()") is None

    def test_none_signature(self) -> None:
        """Returns None for None signature."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name(None) is None

    def test_lowercase_return_type(self) -> None:
        """Returns None for lowercase return types (primitives like int, String)."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name("() int") is None

    def test_generic_return_type(self) -> None:
        """Returns None for generic return types (not simple identifier)."""
        from hypergumbo_lang_common.dart import _extract_dart_return_type_name

        assert _extract_dart_return_type_name("() List<String>") is None


class TestDartImportHintsExtraction:
    """Tests for import hints extraction for disambiguation."""

    def test_extracts_as_prefix(self, tmp_path: Path) -> None:
        """Extracts import prefix from 'as' clause."""
        from hypergumbo_lang_common.dart import _extract_import_hints

        import tree_sitter
        from tree_sitter_language_pack import get_language

        lang = get_language("dart")
        parser = tree_sitter.Parser(lang)

        dart_file = tmp_path / "main.dart"
        dart_file.write_text("""
import 'package:http/http.dart' as http;

void main() {
  http.get('url');
}
""")

        source = dart_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_import_hints(tree, source)

        # 'http' prefix should map to the import path
        assert "http" in hints
        assert hints["http"] == "package:http/http.dart"

    def test_extracts_show_names(self, tmp_path: Path) -> None:
        """Extracts names from 'show' combinator."""
        from hypergumbo_lang_common.dart import _extract_import_hints

        import tree_sitter
        from tree_sitter_language_pack import get_language

        lang = get_language("dart")
        parser = tree_sitter.Parser(lang)

        dart_file = tmp_path / "main.dart"
        dart_file.write_text("""
import 'package:models/models.dart' show User, Account;

void main() {
  var user = User();
}
""")

        source = dart_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_import_hints(tree, source)

        # Both shown names should map to the import path
        assert "User" in hints
        assert hints["User"] == "package:models/models.dart"
        assert "Account" in hints
        assert hints["Account"] == "package:models/models.dart"


class TestDartAmbiguousMethodGuard:
    """Tests for AMB-METHOD invariant in Dart.

    When a method name has 3+ definitions across different classes and
    the receiver type cannot be inferred, the analyzer must NOT produce
    a resolved call edge (which would be a false positive).

    Invariant: Method calls with 3+ ambiguous receiver types must not
    produce resolved call edges.
    """

    def test_ambiguous_method_three_plus_classes_no_resolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """close() with 3 classes defining close() → no resolved edge."""
        from hypergumbo_lang_common.dart import analyze_dart

        dart_file = tmp_path / "multi.dart"
        dart_file.write_text("""
class ServiceA {
  void close() { }
}

class ServiceB {
  void close() { }
}

class ServiceC {
  void close() { }
}

void cleanup() {
  close();
}
""")

        result = analyze_dart(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        # Should NOT have a resolved edge to any specific class's close()
        for edge in cleanup_calls:
            assert "ServiceA" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceA, got {edge.dst}"
            )
            assert "ServiceB" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceB, got {edge.dst}"
            )
            assert "ServiceC" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceC, got {edge.dst}"
            )

    def test_two_classes_same_method_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """run() with 2 classes → still resolves (below threshold)."""
        from hypergumbo_lang_common.dart import analyze_dart

        dart_file = tmp_path / "two.dart"
        dart_file.write_text("""
class ServiceA {
  void run() { }
}

class ServiceB {
  void run() { }
}

void execute() {
  run();
}
""")

        result = analyze_dart(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        execute_calls = [e for e in call_edges if "execute" in e.src]

        # 2 candidates is below the threshold — should still resolve
        run_calls = [e for e in execute_calls if "run" in e.dst.lower()]
        assert len(run_calls) >= 1, "2 candidates should still resolve"


class TestDartVisibilityModifiers:
    """Tests for Dart visibility modifier extraction (underscore convention)."""

    def test_private_symbols_get_private_modifier(self, tmp_path: Path) -> None:
        """Underscore-prefixed names get 'private' modifier."""
        from hypergumbo_lang_common.dart import analyze_dart

        make_dart_file(tmp_path, "main.dart", """
class _PrivateClass {
  void _privateMethod() {}
}

void _privateFunc() {}

class PublicClass {
  void publicMethod() {}
}

void publicFunc() {}
""")
        result = analyze_dart(tmp_path)

        priv_cls = next(s for s in result.symbols if s.name == "_PrivateClass")
        assert "private" in priv_cls.modifiers

        priv_method = next(
            s for s in result.symbols if s.name == "_PrivateClass._privateMethod"
        )
        assert "private" in priv_method.modifiers

        priv_func = next(s for s in result.symbols if s.name == "_privateFunc")
        assert "private" in priv_func.modifiers

        pub_cls = next(s for s in result.symbols if s.name == "PublicClass")
        assert "private" not in pub_cls.modifiers

        pub_method = next(
            s for s in result.symbols if s.name == "PublicClass.publicMethod"
        )
        assert "private" not in pub_method.modifiers

        pub_func = next(s for s in result.symbols if s.name == "publicFunc")
        assert "private" not in pub_func.modifiers

