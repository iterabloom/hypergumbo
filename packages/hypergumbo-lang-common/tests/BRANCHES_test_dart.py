# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for dart.py analyzer.

Tests specific branch paths in the Dart analyzer that may not be covered
by the main test suite. Focuses on:
- Class, mixin, extension, and enum extraction
- Method, getter, setter extraction
- Top-level function and constructor extraction
- Signature extraction with various parameter types
- Import hint extraction (as prefix, show combinator)
- Call resolution and type inference
- Constructor instantiation and variable type tracking
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_dart_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Dart file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestDartClassExtraction:
    """Branch coverage for class_definition extraction."""

    def test_simple_class(self, tmp_path: Path) -> None:
        """Test simple class extraction."""
        make_dart_file(tmp_path, "user.dart", """
class User {
  String name;
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "User"), None)
        assert cls is not None
        assert cls["kind"] == "class"

    def test_abstract_class(self, tmp_path: Path) -> None:
        """Test abstract class extraction."""
        make_dart_file(tmp_path, "animal.dart", """
abstract class Animal {
  void speak();
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "Animal"), None)
        assert cls is not None
        assert cls["kind"] == "class"


class TestDartMixinExtraction:
    """Branch coverage for mixin_declaration extraction."""

    def test_simple_mixin(self, tmp_path: Path) -> None:
        """Test mixin extraction."""
        make_dart_file(tmp_path, "mixins.dart", """
mixin Loggable {
  void log(String msg) {
    print(msg);
  }
}
""")
        data = analyze(tmp_path)
        mixin = next((n for n in data["nodes"] if n["name"] == "Loggable"), None)
        assert mixin is not None
        assert mixin["kind"] == "mixin"


class TestDartExtensionExtraction:
    """Branch coverage for extension_declaration extraction."""

    def test_simple_extension(self, tmp_path: Path) -> None:
        """Test extension extraction."""
        make_dart_file(tmp_path, "extensions.dart", """
extension StringExt on String {
  String capitalize() {
    return this[0].toUpperCase() + substring(1);
  }
}
""")
        data = analyze(tmp_path)
        ext = next((n for n in data["nodes"] if n["name"] == "StringExt"), None)
        assert ext is not None
        assert ext["kind"] == "extension"


class TestDartEnumExtraction:
    """Branch coverage for enum_declaration extraction."""

    def test_simple_enum(self, tmp_path: Path) -> None:
        """Test enum extraction."""
        make_dart_file(tmp_path, "colors.dart", """
enum Color {
  red,
  green,
  blue
}
""")
        data = analyze(tmp_path)
        en = next((n for n in data["nodes"] if n["name"] == "Color"), None)
        assert en is not None
        assert en["kind"] == "enum"


class TestDartMethodExtraction:
    """Branch coverage for method_signature extraction."""

    def test_regular_method(self, tmp_path: Path) -> None:
        """Test regular method extraction in class."""
        make_dart_file(tmp_path, "service.dart", """
class Service {
  void process(String data) {
    print(data);
  }
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if "process" in n["name"] and n["kind"] == "method"), None)
        assert method is not None
        assert "Service.process" in method["name"]

    def test_method_with_return_type(self, tmp_path: Path) -> None:
        """Test method with explicit return type."""
        make_dart_file(tmp_path, "calc.dart", """
class Calculator {
  int add(int a, int b) {
    return a + b;
  }
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if "add" in n["name"] and n["kind"] == "method"), None)
        assert method is not None
        assert "int" in method["signature"]


class TestDartGetterSetterExtraction:
    """Branch coverage for getter_signature and setter_signature extraction."""

    def test_getter(self, tmp_path: Path) -> None:
        """Test getter extraction."""
        make_dart_file(tmp_path, "person.dart", """
class Person {
  String _name = 'John';

  String get name {
    return _name;
  }
}
""")
        data = analyze(tmp_path)
        getter = next((n for n in data["nodes"] if "name" in n["name"] and n["kind"] == "getter"), None)
        assert getter is not None
        assert "Person.name" in getter["name"]

    def test_setter(self, tmp_path: Path) -> None:
        """Test setter extraction."""
        make_dart_file(tmp_path, "person.dart", """
class Person {
  String _name = '';

  set name(String value) {
    _name = value;
  }
}
""")
        data = analyze(tmp_path)
        setter = next((n for n in data["nodes"] if "name" in n["name"] and n["kind"] == "setter"), None)
        assert setter is not None
        assert "Person.name" in setter["name"]


class TestDartFunctionExtraction:
    """Branch coverage for top-level function extraction."""

    def test_top_level_function(self, tmp_path: Path) -> None:
        """Test top-level function extraction."""
        make_dart_file(tmp_path, "main.dart", """
void main() {
  print('Hello');
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "main" and n["kind"] == "function"), None)
        assert func is not None

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters."""
        make_dart_file(tmp_path, "utils.dart", """
int multiply(int x, int y) {
  return x * y;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "multiply"), None)
        assert func is not None
        assert "int x" in func["signature"]
        assert "int y" in func["signature"]
        assert func["signature"].endswith(" int")


class TestDartConstructorExtraction:
    """Branch coverage for constructor_signature extraction."""

    def test_named_constructor(self, tmp_path: Path) -> None:
        """Test named constructor extraction."""
        make_dart_file(tmp_path, "point.dart", """
class Point {
  int x;
  int y;

  Point.origin() {
    x = 0;
    y = 0;
  }
}
""")
        data = analyze(tmp_path)
        ctor = next((n for n in data["nodes"] if "origin" in n["name"] and n["kind"] == "constructor"), None)
        assert ctor is not None


class TestDartSignatureExtraction:
    """Branch coverage for _extract_dart_signature."""

    def test_void_return(self, tmp_path: Path) -> None:
        """Test function with void return type (not in signature)."""
        make_dart_file(tmp_path, "action.dart", """
void action(int x) {
  print(x);
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "action"), None)
        assert func is not None
        # void should not appear after params
        assert func["signature"].endswith(")")

    def test_optional_parameters(self, tmp_path: Path) -> None:
        """Test function with optional parameters."""
        make_dart_file(tmp_path, "greet.dart", """
String greet(String name, {bool loud = false}) {
  return loud ? name.toUpperCase() : name;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "greet"), None)
        assert func is not None
        # Optional params should be captured with ... for defaults
        assert "loud" in func["signature"]


class TestDartImportHints:
    """Branch coverage for _extract_import_hints."""

    def test_import_with_as_prefix(self, tmp_path: Path) -> None:
        """Test import with as prefix."""
        make_dart_file(tmp_path, "app.dart", """
import 'dart:math' as math;

void main() {
  print(math.pi);
}
""")
        data = analyze(tmp_path)
        # Should parse without error
        func = next((n for n in data["nodes"] if n["name"] == "main"), None)
        assert func is not None

    def test_import_with_show(self, tmp_path: Path) -> None:
        """Test import with show combinator."""
        make_dart_file(tmp_path, "app.dart", """
import 'dart:math' show pi, sqrt;

void main() {
  print(pi);
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "main"), None)
        assert func is not None


class TestDartImportEdges:
    """Branch coverage for import edge creation."""

    def test_dart_core_import(self, tmp_path: Path) -> None:
        """Test dart:core import edge."""
        make_dart_file(tmp_path, "app.dart", """
import 'dart:io';

void main() {
  exit(0);
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("dart:io" in e["dst"] for e in edges)

    def test_package_import(self, tmp_path: Path) -> None:
        """Test package import edge."""
        make_dart_file(tmp_path, "app.dart", """
import 'package:flutter/material.dart';

void main() {
  runApp();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("flutter" in e["dst"] for e in edges)

    def test_local_import(self, tmp_path: Path) -> None:
        """Test local file import edge."""
        make_dart_file(tmp_path, "utils.dart", """
void helper() {}
""")
        make_dart_file(tmp_path, "app.dart", """
import 'utils.dart';

void main() {
  helper();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("utils.dart" in e["dst"] for e in edges)


class TestDartCallResolution:
    """Branch coverage for call resolution."""

    def test_local_function_call(self, tmp_path: Path) -> None:
        """Test call to local function."""
        make_dart_file(tmp_path, "app.dart", """
void helper() {
  print('helper');
}

void main() {
  helper();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different file."""
        make_dart_file(tmp_path, "utils.dart", """
void utility() {
  print('utility');
}
""")
        make_dart_file(tmp_path, "app.dart", """
void main() {
  utility();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("utility" in e["dst"] for e in edges)


class TestDartTypeInferredCalls:
    """Branch coverage for type-inferred method calls."""

    def test_method_call_on_typed_param(self, tmp_path: Path) -> None:
        """Test method call on parameter with known type."""
        make_dart_file(tmp_path, "service.dart", """
class Database {
  void save(String data) {
    print(data);
  }
}

void process(Database db) {
  db.save('test');
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("save" in e["dst"] for e in edges)


class TestDartConstructorInstantiation:
    """Branch coverage for constructor instantiation."""

    def test_new_expression(self, tmp_path: Path) -> None:
        """Test new expression creates instantiates edge."""
        make_dart_file(tmp_path, "app.dart", """
class Widget {}

void main() {
  var w = new Widget();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert any("Widget" in e["dst"] for e in edges)

    def test_const_expression(self, tmp_path: Path) -> None:
        """Test const expression creates instantiates edge."""
        make_dart_file(tmp_path, "app.dart", """
class Config {
  final String name;
  const Config(this.name);
}

void main() {
  var c = const Config('test');
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert any("Config" in e["dst"] for e in edges)


class TestDartVariableTypeTracking:
    """Branch coverage for variable type tracking from constructors."""

    def test_var_type_from_constructor(self, tmp_path: Path) -> None:
        """Test variable type inferred from constructor for method calls."""
        make_dart_file(tmp_path, "app.dart", """
class Service {
  void run() {
    print('running');
  }
}

void main() {
  var s = new Service();
  s.run();
}
""")
        data = analyze(tmp_path)
        # Should have both instantiates and calls edges
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("Service" in e["dst"] for e in inst_edges)
        assert any("run" in e["dst"] for e in call_edges)


class TestDartMultipleFiles:
    """Branch coverage for multi-file analysis."""

    def test_multiple_dart_files(self, tmp_path: Path) -> None:
        """Test analysis of multiple Dart files."""
        make_dart_file(tmp_path, "model.dart", """
class User {
  String name;
}
""")
        make_dart_file(tmp_path, "service.dart", """
class UserService {
  void save(String data) {
    print(data);
  }
}
""")
        make_dart_file(tmp_path, "main.dart", """
void main() {
  print('Hello');
}
""")
        data = analyze(tmp_path)
        user = next((n for n in data["nodes"] if n["name"] == "User"), None)
        svc = next((n for n in data["nodes"] if n["name"] == "UserService"), None)
        main = next((n for n in data["nodes"] if n["name"] == "main"), None)
        assert user is not None
        assert svc is not None
        assert main is not None


class TestDartSpanExtraction:
    """Branch coverage for _get_combined_span."""

    def test_span_without_body(self, tmp_path: Path) -> None:
        """Test span extraction when there's no function body."""
        make_dart_file(tmp_path, "interface.dart", """
abstract class Interface {
  void doSomething();
}
""")
        data = analyze(tmp_path)
        # Abstract methods don't have body
        method = next((n for n in data["nodes"] if "doSomething" in n["name"]), None)
        assert method is not None


class TestDartEnclosingClassDetection:
    """Branch coverage for _find_enclosing_class."""

    def test_method_in_mixin(self, tmp_path: Path) -> None:
        """Test method inside mixin gets prefixed with mixin name."""
        make_dart_file(tmp_path, "mixins.dart", """
mixin Printable {
  void printMe() {
    print('printing');
  }
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if "printMe" in n["name"]), None)
        assert method is not None
        assert "Printable.printMe" in method["name"]

    def test_method_in_extension(self, tmp_path: Path) -> None:
        """Test method inside extension gets prefixed with extension name."""
        make_dart_file(tmp_path, "extensions.dart", """
extension IntExt on int {
  int double() {
    return this * 2;
  }
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if "double" in n["name"]), None)
        assert method is not None
        assert "IntExt.double" in method["name"]
