# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Swift analyzer."""
import pytest
from pathlib import Path

from hypergumbo_core.analyze.base import find_child_by_type
from unittest.mock import patch, MagicMock

class TestFindSwiftFiles:
    """Tests for Swift file discovery."""

    def test_finds_swift_files(self, tmp_path: Path) -> None:
        """Finds .swift files."""
        from hypergumbo_lang_mainstream.swift import find_swift_files

        (tmp_path / "Main.swift").write_text("func main() {}")
        (tmp_path / "Utils.swift").write_text("class Utils {}")
        (tmp_path / "other.txt").write_text("not swift")

        files = list(find_swift_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".swift" for f in files)

class TestSwiftTreeSitterAvailability:
    """Tests for tree-sitter-swift availability checking."""

    def test_is_swift_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-swift is available."""
        from hypergumbo_lang_mainstream.swift import is_swift_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_swift_tree_sitter_available() is True

    def test_is_swift_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_mainstream.swift import is_swift_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_swift_tree_sitter_available() is False

    def test_is_swift_tree_sitter_available_no_swift(self) -> None:
        """Returns False when tree-sitter is available but swift grammar is not."""
        from hypergumbo_lang_mainstream.swift import is_swift_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_swift_tree_sitter_available() is False

class TestAnalyzeSwiftFallback:
    """Tests for fallback behavior when tree-sitter-swift unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-swift unavailable."""
        from hypergumbo_lang_mainstream import swift as swift_module
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "test.swift").write_text("func test() {}")

        with patch.object(swift_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="swift analysis skipped"):
                result = analyze_swift(tmp_path)

        assert result.skipped is True
        assert "swift" in result.skip_reason

class TestSwiftFunctionExtraction:
    """Tests for extracting Swift functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Swift function declarations."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Main.swift"
        swift_file.write_text("""
func main() {
    print("Hello, world!")
}

func helper(x: Int) -> Int {
    return x + 1
}
""")

        result = analyze_swift(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names

class TestSwiftClassExtraction:
    """Tests for extracting Swift classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Models.swift"
        swift_file.write_text("""
class User {
    var name: String

    init(name: String) {
        self.name = name
    }

    func greet() {
        print("Hello, \\(name)!")
    }
}

class Point {
    var x: Int
    var y: Int
}
""")

        result = analyze_swift(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "Point" in class_names

class TestSwiftStructExtraction:
    """Tests for extracting Swift structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Types.swift"
        swift_file.write_text("""
struct Vector {
    var x: Double
    var y: Double
}

struct Config {
    var apiKey: String
    var timeout: Int
}
""")

        result = analyze_swift(tmp_path)

        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "Vector" in struct_names
        assert "Config" in struct_names

class TestSwiftProtocolExtraction:
    """Tests for extracting Swift protocols."""

    def test_extracts_protocol(self, tmp_path: Path) -> None:
        """Extracts protocol declarations."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Protocols.swift"
        swift_file.write_text("""
protocol Drawable {
    func draw()
}

protocol Clickable {
    func onClick()
}
""")

        result = analyze_swift(tmp_path)

        protocols = [s for s in result.symbols if s.kind == "protocol"]
        protocol_names = [s.name for s in protocols]
        assert "Drawable" in protocol_names
        assert "Clickable" in protocol_names

class TestSwiftEnumExtraction:
    """Tests for extracting Swift enums."""

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts enum declarations."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Enums.swift"
        swift_file.write_text("""
enum Color {
    case red
    case green
    case blue
}

enum Direction: String {
    case north = "N"
    case south = "S"
}
""")

        result = analyze_swift(tmp_path)

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [s.name for s in enums]
        assert "Color" in enum_names
        assert "Direction" in enum_names

class TestSwiftFunctionCalls:
    """Tests for detecting function calls in Swift."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Utils.swift"
        swift_file.write_text("""
func caller() {
    helper()
}

func helper() {
    print("helping")
}
""")

        result = analyze_swift(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

class TestSwiftImports:
    """Tests for detecting Swift import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Main.swift"
        swift_file.write_text("""
import Foundation
import UIKit

func main() {
    print("Hello")
}
""")

        result = analyze_swift(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

class TestSwiftEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Raises error when parser loading fails (base class does not catch)."""
        from hypergumbo_lang_mainstream import swift as swift_module
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "test.swift").write_text("func test() {}")

        with patch.object(swift_module._analyzer, "_check_grammar_available", return_value=True):
            with patch.object(
                swift_module._analyzer, "_create_parser",
                side_effect=RuntimeError("Parser load failed"),
            ):
                with pytest.raises(RuntimeError, match="Parser load failed"):
                    analyze_swift(tmp_path)

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "empty.swift").write_text("// Just a comment\n")

        result = analyze_swift(tmp_path)

        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Helper.swift").write_text("""
func greet(name: String) -> String {
    return "Hello, \\(name)"
}
""")

        (tmp_path / "Main.swift").write_text("""
func run() {
    greet(name: "world")
}
""")

        result = analyze_swift(tmp_path)

        assert result.run.files_analyzed >= 2

class TestSwiftMethodExtraction:
    """Tests for extracting methods from classes."""

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods defined inside classes."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "User.swift"
        swift_file.write_text("""
class User {
    var name: String = ""

    func getName() -> String {
        return name
    }

    func setName(newName: String) {
        name = newName
    }
}
""")

        result = analyze_swift(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert any("getName" in name for name in method_names)

class TestSwiftFileReadErrors:
    """Tests for file read error handling.

    The base TreeSitterAnalyzer.analyze() method handles file read errors
    during Pass 1 by incrementing files_skipped. Internal functions now
    receive pre-parsed trees, so file read errors are handled at the
    analyzer level.
    """

    def test_analyzer_handles_read_error_in_pass1(self, tmp_path: Path) -> None:
        """Analyzer skips files with read errors during Pass 1."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        # Create a valid file plus a file that will fail to read
        (tmp_path / "good.swift").write_text("func good() {}")
        bad_file = tmp_path / "bad.swift"
        bad_file.write_text("func bad() {}")

        original_read_bytes = Path.read_bytes

        def patched_read_bytes(self: Path) -> bytes:
            if self.name == "bad.swift":
                raise OSError("Read failed")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", patched_read_bytes):
            result = analyze_swift(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped >= 1
        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "good" in func_names

class TestSwiftHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo_lang_mainstream.swift import is_swift_tree_sitter_available

        if not is_swift_tree_sitter_available():
            pytest.skip("tree-sitter-swift not available")

        import tree_sitter_swift
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_swift.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None

class TestSwiftSignatureExtraction:
    """Tests for Swift function signature extraction."""

    def test_basic_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic function."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Calculator.swift").write_text("""
class Calculator {
    func add(x: Int, y: Int) -> Int {
        return x + y
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(x: Int, y: Int) -> Int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void function."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Logger.swift").write_text("""
class Logger {
    func log(message: String) {
        print(message)
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(message: String)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from function with no parameters."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Counter.swift").write_text("""
class Counter {
    func getCount() -> Int {
        return 0
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getCount" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "() -> Int"

class TestSwiftClosureCallAttribution:
    """Tests for call edge attribution inside Swift closures.

    Swift uses closures extensively (map, filter, completion handlers). Calls inside
    these closures must be attributed to the enclosing function.
    """

    def test_call_inside_map_closure_attributed(self, tmp_path: Path) -> None:
        """Calls inside map closures are attributed to enclosing function.

        When you have:
            func process() {
                items.map { item in helper(item) }
            }

        The call to helper() should be attributed to process, not lost.
        """
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "App.swift"
        swift_file.write_text("""
func helper(_ x: Int) -> Int {
    return x * 2
}

func process() {
    let items = [1, 2, 3]
    let _ = items.map { item in helper(item) }
}
""")

        result = analyze_swift(tmp_path)

        # Find symbols
        process_func = next(
            (s for s in result.symbols if s.name == "process" and s.kind == "function"),
            None,
        )
        helper_func = next(
            (s for s in result.symbols if s.name == "helper" and s.kind == "function"),
            None,
        )

        assert process_func is not None, "Should find process function"
        assert helper_func is not None, "Should find helper function"

        # The call to helper() inside the closure should be attributed to process
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_func.id
                and e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside map closure should be attributed to process"

    def test_call_inside_completion_handler_attributed(self, tmp_path: Path) -> None:
        """Calls inside completion handler closures are attributed to enclosing function."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Async.swift"
        swift_file.write_text("""
func doWork() {
    print("working")
}

func performAsync(completion: () -> Void) {
    completion()
}

func caller() {
    performAsync {
        doWork()
    }
}
""")

        result = analyze_swift(tmp_path)

        # Find symbols
        caller_func = next(
            (s for s in result.symbols if s.name == "caller" and s.kind == "function"),
            None,
        )
        dowork_func = next(
            (s for s in result.symbols if s.name == "doWork" and s.kind == "function"),
            None,
        )

        assert caller_func is not None
        assert dowork_func is not None

        # The call to doWork() inside the closure should be attributed to caller
        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller_func.id
                and e.dst == dowork_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside completion handler should be attributed to caller"

class TestSwiftInheritanceExtraction:
    """Tests for Swift inheritance/conformance extraction.

    Swift uses inheritance for classes (: SuperClass) and protocol conformance
    (: Protocol) with the same syntax. The base_classes metadata enables the
    centralized inheritance linker to create edges.
    """

    def test_extracts_class_inheritance(self, tmp_path: Path) -> None:
        """Extracts base class from class inheritance."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Models.swift"
        swift_file.write_text("""
class Animal {
    func speak() {}
}

class Dog: Animal {
    override func speak() {}
}
""")

        result = analyze_swift(tmp_path)

        dog = next((s for s in result.symbols if s.name == "Dog"), None)
        assert dog is not None
        assert dog.meta is not None
        assert "base_classes" in dog.meta
        assert "Animal" in dog.meta["base_classes"]

    def test_extracts_protocol_conformance(self, tmp_path: Path) -> None:
        """Extracts protocol conformance as base_classes."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Protocols.swift"
        swift_file.write_text("""
protocol Drawable {
    func draw()
}

class Circle: Drawable {
    func draw() {}
}
""")

        result = analyze_swift(tmp_path)

        circle = next((s for s in result.symbols if s.name == "Circle"), None)
        assert circle is not None
        assert circle.meta is not None
        assert "base_classes" in circle.meta
        assert "Drawable" in circle.meta["base_classes"]

    def test_extracts_multiple_protocols(self, tmp_path: Path) -> None:
        """Extracts multiple protocol conformances."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Multi.swift"
        swift_file.write_text("""
protocol Equatable {}
protocol Hashable {}

struct Point: Equatable, Hashable {
    var x: Int
    var y: Int
}
""")

        result = analyze_swift(tmp_path)

        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta is not None
        assert "base_classes" in point.meta
        assert "Equatable" in point.meta["base_classes"]
        assert "Hashable" in point.meta["base_classes"]

    def test_extracts_class_plus_protocol(self, tmp_path: Path) -> None:
        """Extracts both class inheritance and protocol conformance."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Mixed.swift"
        swift_file.write_text("""
class Vehicle {}
protocol Drivable {}

class Car: Vehicle, Drivable {}
""")

        result = analyze_swift(tmp_path)

        car = next((s for s in result.symbols if s.name == "Car"), None)
        assert car is not None
        assert car.meta is not None
        assert "base_classes" in car.meta
        assert "Vehicle" in car.meta["base_classes"]
        assert "Drivable" in car.meta["base_classes"]

    def test_no_base_classes_when_none(self, tmp_path: Path) -> None:
        """Does not add base_classes when class has no inheritance."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Standalone.swift"
        swift_file.write_text("""
class StandaloneClass {
    var value: Int = 0
}
""")

        result = analyze_swift(tmp_path)

        standalone = next((s for s in result.symbols if s.name == "StandaloneClass"), None)
        assert standalone is not None
        # Either no meta or no base_classes key
        if standalone.meta:
            assert "base_classes" not in standalone.meta or standalone.meta["base_classes"] == []


class TestSwiftVisibilityModifiers:
    """Tests for Swift visibility modifier extraction."""

    def test_method_visibility(self, tmp_path: Path) -> None:
        """Methods with visibility modifiers get them extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Vis.swift"
        swift_file.write_text("""
class Vis {
    public func pubMethod() {}
    private func privMethod() {}
    internal func intMethod() {}
}
""")
        result = analyze_swift(tmp_path)

        pub = next(s for s in result.symbols if s.name == "Vis.pubMethod")
        assert "public" in pub.modifiers

        priv = next(s for s in result.symbols if s.name == "Vis.privMethod")
        assert "private" in priv.modifiers

        internal = next(s for s in result.symbols if s.name == "Vis.intMethod")
        assert "internal" in internal.modifiers

    def test_class_visibility(self, tmp_path: Path) -> None:
        """Classes with visibility modifiers get them extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Classes.swift"
        swift_file.write_text("""
public class PubClass {}
""")
        result = analyze_swift(tmp_path)

        pub = next(s for s in result.symbols if s.name == "PubClass")
        assert "public" in pub.modifiers

    def test_final_modifier(self, tmp_path: Path) -> None:
        """Final modifier is extracted from functions and classes."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Final.swift"
        swift_file.write_text("""
class Parent {
    final func locked() {}
}

final class Sealed {}
""")
        result = analyze_swift(tmp_path)

        locked = next(s for s in result.symbols if s.name == "Parent.locked")
        assert "final" in locked.modifiers

        sealed = next(s for s in result.symbols if s.name == "Sealed")
        assert "final" in sealed.modifiers


class TestNormalizeSwiftSignature:
    """Tests for Swift signature normalization (ADR-0014 §3)."""

    def test_basic_method(self) -> None:
        from hypergumbo_lang_mainstream.swift import normalize_swift_signature
        assert normalize_swift_signature("(x: Int, y: Int) -> Int") == "(Int,Int)Int"

    def test_no_return(self) -> None:
        from hypergumbo_lang_mainstream.swift import normalize_swift_signature
        assert normalize_swift_signature("(msg: String)") == "(String)"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.swift import normalize_swift_signature
        assert normalize_swift_signature(None) is None


class TestSwiftFunctionReferences:
    """Tests for Swift function references in non-call contexts (INV-dinur).

    Swift allows passing functions by name as arguments (e.g., ``map(process)``)
    or assigning them to variables (``let handler = process``).
    """

    def test_function_reference_as_argument(self, tmp_path: Path) -> None:
        """Function passed as argument to higher-order function."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "class App {\n"
            "    func process(_ x: Int) -> Int { return x * 2 }\n"
            "    func run() {\n"
            "        [1, 2, 3].map(process)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "process" in e.dst
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].evidence_type == "function_reference"

    def test_function_reference_assignment(self, tmp_path: Path) -> None:
        """Function assigned to a variable."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "func transform(_ x: Int) -> Int { return x + 1 }\n"
            "func setup() {\n"
            "    let handler = transform\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "setup" in e.src and "transform" in e.dst
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].evidence_type == "function_reference"

    def test_function_reference_cross_file(self, tmp_path: Path) -> None:
        """Function reference resolves cross-file via resolver."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Utils.swift").write_text(
            "func transform(_ x: Int) -> Int { return x + 1 }\n"
        )
        (tmp_path / "App.swift").write_text(
            "func run() {\n"
            "    let handler = transform\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "transform" in e.dst
        ]
        assert len(ref_edges) == 1

    def test_function_reference_argument_cross_file(self, tmp_path: Path) -> None:
        """Function reference as argument resolves cross-file."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Utils.swift").write_text(
            "func double(_ x: Int) -> Int { return x * 2 }\n"
        )
        (tmp_path / "App.swift").write_text(
            "func run() {\n"
            "    [1, 2].map(double)\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "double" in e.dst
        ]
        assert len(ref_edges) == 1

    def test_no_reference_for_non_function(self, tmp_path: Path) -> None:
        """Identifier that doesn't resolve to a function creates no edge."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "func run() {\n"
            "    let x = unknown\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0


class TestSwiftNavigationCalls:
    """Tests for method calls via dot navigation (receiver.method() pattern).

    Swift code commonly calls methods on receivers (e.g. session.request(),
    FileManager.default.fileExists()). The analyzer must extract the METHOD
    name, not the receiver name, from navigation_expression call targets.
    """

    def test_method_call_resolves_to_method_name(self, tmp_path: Path) -> None:
        """session.request() should produce a call edge to 'request', not 'session'."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Service.swift").write_text(
            "class Service {\n"
            "    func request(_ url: String) -> String { return url }\n"
            "}\n"
        )
        (tmp_path / "App.swift").write_text(
            "func fetch() {\n"
            "    let svc = Service()\n"
            "    svc.request(\"http://example.com\")\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should find a call edge from fetch -> request
        request_edges = [
            e for e in call_edges
            if "request" in e.dst and "fetch" in e.src
        ]
        assert len(request_edges) >= 1, (
            f"Expected call edge to 'request' method, got call edges: "
            f"{[(e.src.split(':')[-2], e.dst.split(':')[-2]) for e in call_edges]}"
        )

    def test_chained_navigation_call(self, tmp_path: Path) -> None:
        """URLSession.shared.dataTask() should extract 'dataTask' as callee."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Net.swift").write_text(
            "func fetchData() {\n"
            "    URLSession.shared.dataTask(with: URL(string: \"x\")!)\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have an unresolved edge to 'dataTask'
        dt_edges = [e for e in call_edges if "dataTask" in e.dst]
        assert len(dt_edges) >= 1, (
            f"Expected call to 'dataTask', got: "
            f"{[e.dst.split(':')[-2] for e in call_edges]}"
        )

    def test_same_file_method_via_navigation(self, tmp_path: Path) -> None:
        """self.helper() or obj.helper() should resolve to helper in same file."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Util.swift").write_text(
            "class Util {\n"
            "    func helper() -> Int { return 42 }\n"
            "    func run() {\n"
            "        self.helper()\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [
            e for e in call_edges
            if "helper" in e.dst and "run" in e.src
        ]
        assert len(helper_calls) >= 1, (
            f"Expected call from run -> helper, got: "
            f"{[(e.src.split(':')[-2], e.dst.split(':')[-2]) for e in call_edges]}"
        )


class TestSwiftReceiverTypeTracking:
    """Tests for receiver type tracking in method resolution.

    When a variable has a known type (from type annotation or constructor call),
    method calls on that variable should resolve to the correct type's method.
    """

    def test_type_annotation_resolves_method(self, tmp_path: Path) -> None:
        """let store: Store = ...; store.send() → Store.send()."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Store.swift").write_text(
            "class Store {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
            "class TestStore {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
        )
        (tmp_path / "App.swift").write_text(
            "func test() {\n"
            "    let store: Store = Store()\n"
            "    store.send(\"action\")\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edges = [e for e in call_edges if "send" in e.dst and "test" in e.src]
        assert len(send_edges) >= 1
        # Should resolve to Store.send, not TestStore.send
        store_send = [e for e in send_edges if "Store.send" in e.dst]
        assert len(store_send) >= 1, (
            f"Expected Store.send, got: {[e.dst for e in send_edges]}"
        )

    def test_constructor_infers_type(self, tmp_path: Path) -> None:
        """let store = Store(); store.send() → Store.send()."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Store.swift").write_text(
            "class Store {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
        )
        (tmp_path / "App.swift").write_text(
            "func test() {\n"
            "    let store = Store()\n"
            "    store.send(\"action\")\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edges = [e for e in call_edges if "send" in e.dst and "test" in e.src]
        assert len(send_edges) >= 1
        store_send = [e for e in send_edges if "Store.send" in e.dst]
        assert len(store_send) >= 1, (
            f"Expected Store.send, got: {[e.dst for e in send_edges]}"
        )

    def test_receiver_is_type_name_directly(self, tmp_path: Path) -> None:
        """ClassName.method() where receiver IS the type name."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Config.swift").write_text(
            "class Config {\n"
            "    static func load() -> String { return \"\" }\n"
            "}\n"
        )
        (tmp_path / "App.swift").write_text(
            "func setup() {\n"
            "    Config.load()\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        load_edges = [e for e in call_edges if "load" in e.dst and "setup" in e.src]
        assert len(load_edges) >= 1

    def test_type_hint_used_for_resolver(self, tmp_path: Path) -> None:
        """When qualified name not in symbols, type is used as path_hint for resolver."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Store.swift").write_text(
            "class Store {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
        )
        (tmp_path / "App.swift").write_text(
            "func test() {\n"
            "    let store = Store()\n"
            "    store.unknownMethod()\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        # unknownMethod doesn't exist as a symbol, so it falls through to resolver
        # The path_hint should be "Store" (from var_types)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unknown_edges = [e for e in call_edges if "unknownMethod" in e.dst]
        assert len(unknown_edges) >= 1  # Should produce an unresolved edge

    def test_same_file_type_qualified(self, tmp_path: Path) -> None:
        """Type-qualified resolution in same file → local_symbols path."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "All.swift").write_text(
            "class Store {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
            "class TestStore {\n"
            "    func send(_ action: String) {}\n"
            "}\n"
            "func test() {\n"
            "    let store = Store()\n"
            "    store.send(\"action\")\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edges = [e for e in call_edges if "send" in e.dst and "test" in e.src]
        assert len(send_edges) >= 1
        store_send = [e for e in send_edges if "Store.send" in e.dst]
        assert len(store_send) >= 1, (
            f"Expected Store.send, got: {[e.dst for e in send_edges]}"
        )
        # Confidence should be 0.90 (type-qualified)
        assert store_send[0].confidence == 0.90

    def test_extract_var_type_annotation(self) -> None:
        """Test _extract_var_type with type annotation."""
        from hypergumbo_lang_mainstream.swift import _extract_var_type

        import tree_sitter
        from tree_sitter_language_pack import get_language
        lang = get_language("swift")
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(b"let x: Store = Store()")
        for node in tree.root_node.children:
            if node.type == "property_declaration":
                name, typ = _extract_var_type(node, b"let x: Store = Store()")
                assert name == "x"
                assert typ == "Store"
                return
        raise AssertionError("No property_declaration found")

    def test_extract_var_type_constructor(self) -> None:
        """Test _extract_var_type with constructor call (no annotation)."""
        from hypergumbo_lang_mainstream.swift import _extract_var_type

        import tree_sitter
        from tree_sitter_language_pack import get_language
        lang = get_language("swift")
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(b"let store = Store()")
        for node in tree.root_node.children:
            if node.type == "property_declaration":
                name, typ = _extract_var_type(node, b"let store = Store()")
                assert name == "store"
                assert typ == "Store"
                return
        raise AssertionError("No property_declaration found")

    def test_extract_var_type_no_type(self) -> None:
        """Test _extract_var_type when no type is available."""
        from hypergumbo_lang_mainstream.swift import _extract_var_type

        import tree_sitter
        from tree_sitter_language_pack import get_language
        lang = get_language("swift")
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(b"let x = compute()")
        for node in tree.root_node.children:
            if node.type == "property_declaration":
                name, typ = _extract_var_type(node, b"let x = compute()")
                assert name == "x"
                assert typ is None  # compute() starts lowercase, not a constructor
                return
        raise AssertionError("No property_declaration found")


class TestSwiftComputedProperties:
    """Tests for extracting computed properties as callable nodes.

    Computed properties (var x: T { get { ... } }) are the primary API pattern
    for many Swift libraries (SwiftyJSON, Kingfisher, TCA). They must appear as
    symbols so they contribute to slice graphs and call resolution.
    """

    def test_getter_only_computed_property(self, tmp_path: Path) -> None:
        """Computed property with implicit getter is extracted as property symbol."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "JSON.swift").write_text(
            "struct JSON {\n"
            "    var arrayValue: [Any] {\n"
            "        return []\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        prop_names = [s.name for s in props]
        assert "JSON.arrayValue" in prop_names

    def test_getter_setter_computed_property(self, tmp_path: Path) -> None:
        """Computed property with explicit get/set is extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Config.swift").write_text(
            "class Config {\n"
            "    private var _timeout: Int = 30\n"
            "    var timeout: Int {\n"
            "        get { return _timeout }\n"
            "        set { _timeout = newValue }\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        prop_names = [s.name for s in props]
        assert "Config.timeout" in prop_names

    def test_computed_property_not_stored(self, tmp_path: Path) -> None:
        """Stored properties (no computed_property child) are NOT extracted as symbols."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Point.swift").write_text(
            "struct Point {\n"
            "    var x: Int = 0\n"
            "    var y: Int = 0\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        props = [s for s in result.symbols if s.kind == "property"]
        assert len(props) == 0, f"Stored properties should not be extracted: {[s.name for s in props]}"

    def test_computed_property_has_span(self, tmp_path: Path) -> None:
        """Computed property symbol has correct span."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Span.swift").write_text(
            "struct S {\n"
            "    var computed: Int {\n"
            "        return 42\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        prop = next((s for s in result.symbols if s.name == "S.computed"), None)
        assert prop is not None
        assert prop.span.start_line == 2
        assert prop.span.end_line == 4

    def test_call_inside_computed_property_attributed(self, tmp_path: Path) -> None:
        """Calls inside computed property body are attributed to the property."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "func helper() -> Int { return 42 }\n"
            "\n"
            "struct App {\n"
            "    var value: Int {\n"
            "        return helper()\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        prop = next((s for s in result.symbols if s.name == "App.value"), None)
        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert prop is not None, "Should find computed property"
        assert helper is not None, "Should find helper function"

        call_edge = next(
            (e for e in result.edges if e.src == prop.id and e.dst == helper.id and e.edge_type == "calls"),
            None,
        )
        assert call_edge is not None, "Call inside computed property should create edge from property to callee"

    def test_computed_property_modifiers(self, tmp_path: Path) -> None:
        """Modifiers (public, static) are extracted for computed properties."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Mod.swift").write_text(
            "class Mod {\n"
            "    public static var shared: Mod {\n"
            "        return Mod()\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        prop = next((s for s in result.symbols if s.name == "Mod.shared"), None)
        assert prop is not None
        assert "public" in prop.modifiers
        assert "static" in prop.modifiers


class TestSwiftSubscriptDeclarations:
    """Tests for extracting subscript declarations as callable nodes.

    Subscripts (subscript(index: Int) -> T { ... }) are a primary API pattern
    for collection-like types in Swift (SwiftyJSON, Alamofire, etc.).
    """

    def test_subscript_with_int_param(self, tmp_path: Path) -> None:
        """Subscript with Int parameter is extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "List.swift").write_text(
            "struct List {\n"
            "    subscript(index: Int) -> String {\n"
            "        return \"\"\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        subs = [s for s in result.symbols if s.kind == "subscript"]
        assert len(subs) == 1
        assert subs[0].name == "List.subscript(index:)"

    def test_subscript_with_string_param(self, tmp_path: Path) -> None:
        """Subscript with String parameter is extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Dict.swift").write_text(
            "struct Dict {\n"
            "    subscript(key: String) -> Int {\n"
            "        get { return 0 }\n"
            "        set { }\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        subs = [s for s in result.symbols if s.kind == "subscript"]
        assert len(subs) == 1
        assert subs[0].name == "Dict.subscript(key:)"

    def test_subscript_has_signature(self, tmp_path: Path) -> None:
        """Subscript symbol includes parameter signature."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Arr.swift").write_text(
            "struct Arr {\n"
            "    subscript(index: Int) -> String {\n"
            "        return \"\"\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        sub = next((s for s in result.symbols if s.kind == "subscript"), None)
        assert sub is not None
        assert sub.signature is not None
        assert "Int" in sub.signature

    def test_call_inside_subscript_attributed(self, tmp_path: Path) -> None:
        """Calls inside subscript body are attributed to the subscript."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "func validate(_ i: Int) -> Int { return i }\n"
            "\n"
            "struct App {\n"
            "    subscript(index: Int) -> Int {\n"
            "        return validate(index)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        sub = next((s for s in result.symbols if s.kind == "subscript"), None)
        validate = next((s for s in result.symbols if s.name == "validate"), None)
        assert sub is not None, "Should find subscript"
        assert validate is not None, "Should find validate function"

        call_edge = next(
            (e for e in result.edges if e.src == sub.id and e.dst == validate.id and e.edge_type == "calls"),
            None,
        )
        assert call_edge is not None, "Call inside subscript should create edge from subscript to callee"

    def test_multiple_subscripts(self, tmp_path: Path) -> None:
        """Multiple subscripts in same type are all extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Multi.swift").write_text(
            "struct Multi {\n"
            "    subscript(index: Int) -> String {\n"
            "        return \"\"\n"
            "    }\n"
            "    subscript(key: String) -> String {\n"
            "        return \"\"\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        subs = [s for s in result.symbols if s.kind == "subscript"]
        assert len(subs) == 2
        sub_names = {s.name for s in subs}
        assert "Multi.subscript(index:)" in sub_names
        assert "Multi.subscript(key:)" in sub_names


class TestSwiftShortNameCollision:
    """Tests for short-name collision prevention (AMB-METHOD invariant).

    When multiple types define the same method name (append, filter, get),
    bare-name resolution must not produce confident false-positive edges.
    Methods should only be registered by qualified name (Type.method), so
    bare calls fall through to the NameResolver which handles ambiguity.
    """

    def test_same_method_name_different_types_no_false_positive(self, tmp_path: Path) -> None:
        """Two types with same method name should not produce false-positive edge.

        When TypeA.process() calls process() meaning self.process(), it should
        NOT resolve to TypeB.process().
        """
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Types.swift").write_text(
            "class TypeA {\n"
            "    func process() {\n"
            "        print(\"A\")\n"
            "    }\n"
            "    func run() {\n"
            "        process()\n"
            "    }\n"
            "}\n"
            "\n"
            "class TypeB {\n"
            "    func process() {\n"
            "        print(\"B\")\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)

        # Find the call edge from TypeA.run -> process
        run_sym = next((s for s in result.symbols if s.name == "TypeA.run"), None)
        assert run_sym is not None

        # There should be an edge, but NOT a confident (0.85) same-file edge
        # to TypeB.process. Either it should resolve to TypeA.process via the
        # resolver's suffix matching, or be marked as ambiguous/unresolved.
        call_edges = [
            e for e in result.edges
            if e.src == run_sym.id and e.edge_type == "calls"
        ]

        type_b = next((s for s in result.symbols if s.name == "TypeB.process"), None)
        assert type_b is not None

        # The call MUST NOT confidently resolve to TypeB.process
        false_positive = next(
            (e for e in call_edges if e.dst == type_b.id and e.confidence > 0.80),
            None,
        )
        assert false_positive is None, (
            f"Bare 'process()' call in TypeA.run should not confidently resolve to "
            f"TypeB.process (confidence={false_positive.confidence if false_positive else 'N/A'})"
        )

    def test_top_level_function_still_resolves_locally(self, tmp_path: Path) -> None:
        """Top-level functions (no enclosing type) should still resolve via local_symbols."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "App.swift").write_text(
            "func helper() -> Int { return 42 }\n"
            "\n"
            "func caller() {\n"
            "    helper()\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        caller = next((s for s in result.symbols if s.name == "caller"), None)
        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert caller is not None
        assert helper is not None

        call_edge = next(
            (e for e in result.edges if e.src == caller.id and e.dst == helper.id),
            None,
        )
        assert call_edge is not None, "Top-level function calls should still resolve locally"
        assert call_edge.confidence == 0.85

    def test_method_resolves_via_resolver_not_local(self, tmp_path: Path) -> None:
        """Method calls should go through the resolver, not bare-name local match."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Service.swift").write_text(
            "class Service {\n"
            "    func execute() { print(\"exec\") }\n"
            "    func run() {\n"
            "        execute()\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        run_sym = next((s for s in result.symbols if s.name == "Service.run"), None)
        exec_sym = next((s for s in result.symbols if s.name == "Service.execute"), None)
        assert run_sym is not None
        assert exec_sym is not None

        # The call should resolve (via resolver suffix match) but NOT with
        # 0.85 local-symbol confidence — it should go through the resolver
        call_edge = next(
            (e for e in result.edges if e.src == run_sym.id and e.dst == exec_sym.id),
            None,
        )
        assert call_edge is not None, "Method should still resolve via resolver"

    def test_three_types_same_method_ambiguous(self, tmp_path: Path) -> None:
        """3+ types with same method name produces low-confidence or unresolved edge."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "Many.swift").write_text(
            "class A {\n"
            "    func update() {}\n"
            "}\n"
            "class B {\n"
            "    func update() {}\n"
            "}\n"
            "class C {\n"
            "    func update() {}\n"
            "    func run() {\n"
            "        update()\n"
            "    }\n"
            "}\n"
        )
        result = analyze_swift(tmp_path)
        run_sym = next((s for s in result.symbols if s.name == "C.run"), None)
        assert run_sym is not None

        call_edges = [
            e for e in result.edges
            if e.src == run_sym.id and e.edge_type == "calls"
        ]

        # With 3+ candidates, resolver should either:
        # - Not resolve (unresolved_external_call), or
        # - Resolve with very low confidence (ambiguous)
        for e in call_edges:
            if "update" in e.dst:
                assert e.confidence < 0.80, (
                    f"3-way ambiguous 'update()' call should have low confidence, "
                    f"got {e.confidence}"
                )


class TestSwiftErrorNodeRecovery:
    """Tests for recovering class/struct symbols from ERROR nodes.

    tree-sitter-swift fails to parse certain Swift patterns (preprocessor
    directives, _$ identifiers), producing ERROR nodes instead of proper
    class_declaration nodes. The analyzer should recover the class name
    from ERROR nodes when possible.
    """

    def test_class_with_preprocessor_directive_recovered(
        self, tmp_path: Path,
    ) -> None:
        """Class with complex preprocessor directives should be extracted."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Store.swift"
        # Reproduces tree-sitter-swift parse failure: @dynamicMemberLookup,
        # @preconcurrency @MainActor, _$ identifiers, and #if/#else/#endif
        # cause ERROR nodes instead of class_declaration.
        swift_file.write_text("""\
import Foundation

@dynamicMemberLookup
@preconcurrency @MainActor
public final class Store<State, Action>: _Store {
    var children: [String: AnyObject] = [:]

    @_spi(Internals) public var cancellables: [UUID: Any] { [:] }

    #if !os(visionOS)
    let _$observationRegistrar = 0
    #else
    let _$observationRegistrar = 1
    #endif

    public func send(_ action: Action) {
        // dispatch action
    }
}
""")
        result = analyze_swift(tmp_path)

        # The class should be recovered even if the parser produces an ERROR node
        class_syms = [s for s in result.symbols if s.kind == "class"]
        class_names = {s.name for s in class_syms}
        assert "Store" in class_names, (
            f"Store class should be recovered from ERROR node. "
            f"Found classes: {class_names}"
        )

    def test_recover_class_from_error_node_no_name(self) -> None:
        """Recovery returns None when ERROR node has keyword but no name."""
        from hypergumbo_lang_mainstream.swift import _recover_class_from_error_node

        # ERROR node with 'class' keyword but empty name identifier
        keyword_child = MagicMock()
        keyword_child.type = "class"
        keyword_child.children = []

        empty_name = MagicMock()
        empty_name.type = "simple_identifier"
        empty_name.start_byte = 0
        empty_name.end_byte = 0

        error_node = MagicMock()
        error_node.type = "ERROR"
        error_node.children = [keyword_child, empty_name]

        result = _recover_class_from_error_node(error_node, b"")
        assert result is None

    def test_recover_class_from_error_node_type_identifier(self) -> None:
        """Recovery works with type_identifier instead of simple_identifier."""
        from hypergumbo_lang_mainstream.swift import _recover_class_from_error_node

        keyword_child = MagicMock()
        keyword_child.type = "struct"
        keyword_child.children = []

        name_node = MagicMock()
        name_node.type = "type_identifier"
        name_node.start_byte = 7
        name_node.end_byte = 14

        error_node = MagicMock()
        error_node.type = "ERROR"
        error_node.children = [keyword_child, name_node]

        result = _recover_class_from_error_node(error_node, b"struct MyModel { }")
        assert result is not None
        assert result[0] == "MyModel"
        assert result[1] == "struct"

    def test_recover_class_type_identifier_empty_name(self) -> None:
        """Recovery returns None when type_identifier has empty name."""
        from hypergumbo_lang_mainstream.swift import _recover_class_from_error_node

        keyword_child = MagicMock()
        keyword_child.type = "enum"
        keyword_child.children = []

        name_node = MagicMock()
        name_node.type = "type_identifier"
        name_node.start_byte = 0
        name_node.end_byte = 0

        error_node = MagicMock()
        error_node.type = "ERROR"
        error_node.children = [keyword_child, name_node]

        result = _recover_class_from_error_node(error_node, b"")
        assert result is None


class TestSwiftVaporUsageContext:
    """Tests for Vapor route UsageContext extraction."""

    def test_vapor_simple_route(self, tmp_path: Path) -> None:
        """Detects simple Vapor route registrations like app.get("path")."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "routes.swift"
        swift_file.write_text('''
import Vapor

func routes(_ app: Application) throws {
    app.get("hello") { req in
        return "Hello, world!"
    }
}
''')
        result = analyze_swift(tmp_path)

        assert len(result.usage_contexts) >= 1
        ctx = result.usage_contexts[0]
        assert ctx.kind == "call"
        assert ctx.context_name == "app.get"
        assert ctx.position == "args[last]"
        assert ctx.metadata["route_path"] == "hello"
        assert ctx.metadata["http_method"] == "GET"

        # Route symbols should also be created
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        route = routes[0]
        assert route.name == "GET /hello"
        assert route.meta["http_method"] == "GET"
        assert route.meta["route_path"] == "/hello"

    def test_vapor_multiple_routes(self, tmp_path: Path) -> None:
        """Detects multiple Vapor route methods."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "routes.swift"
        swift_file.write_text('''
import Vapor

func routes(_ app: Application) throws {
    app.get("users") { req in
        return "list"
    }
    app.post("users") { req in
        return "create"
    }
    app.delete("users", ":id") { req in
        return "delete"
    }
}
''')
        result = analyze_swift(tmp_path)

        assert len(result.usage_contexts) >= 3
        methods = {ctx.metadata["http_method"] for ctx in result.usage_contexts}
        assert "GET" in methods
        assert "POST" in methods
        assert "DELETE" in methods

    def test_vapor_routes_receiver(self, tmp_path: Path) -> None:
        """Detects routes registered on 'routes' and 'router' receivers."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "routes.swift"
        swift_file.write_text('''
import Vapor

func boot(routes: RoutesBuilder) throws {
    routes.get("api", "status") { req in
        return "ok"
    }
}
''')
        result = analyze_swift(tmp_path)

        assert len(result.usage_contexts) >= 1
        ctx = result.usage_contexts[0]
        assert ctx.context_name == "routes.get"
        assert ctx.metadata["http_method"] == "GET"

    def test_vapor_use_handler(self, tmp_path: Path) -> None:
        """Detects Vapor routes with use: handler parameter."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "routes.swift"
        swift_file.write_text('''
import Vapor

struct UserController {
    func index(req: Request) throws -> String {
        return "users"
    }
}

func routes(_ app: Application) throws {
    let controller = UserController()
    app.get("users", use: controller.index)
}
''')
        result = analyze_swift(tmp_path)

        assert len(result.usage_contexts) >= 1
        ctx = result.usage_contexts[0]
        assert ctx.kind == "call"
        assert ctx.metadata["http_method"] == "GET"

    def test_vapor_no_routes_in_non_route_code(self, tmp_path: Path) -> None:
        """Does not extract usage contexts from non-route code."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        swift_file = tmp_path / "Model.swift"
        swift_file.write_text('''
import Foundation

struct User {
    let name: String

    func getName() -> String {
        return name
    }
}
''')
        result = analyze_swift(tmp_path)
        assert len(result.usage_contexts) == 0
