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
