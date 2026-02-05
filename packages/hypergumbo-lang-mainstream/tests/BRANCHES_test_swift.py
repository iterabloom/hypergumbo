"""Branch coverage tests for swift.py analyzer.

Tests specific branch paths in the Swift analyzer that may not be covered
by the main test suite. Focuses on:
- Base classes/protocol extraction for inheritance
- Signature extraction with various parameter and return types
- Symbol extraction (classes, structs, enums, protocols)
- Type declaration kind detection (class/struct/enum)
- Call resolution (local, global)
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_swift_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Swift file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestSwiftBaseClasses:
    """Branch coverage for _extract_base_classes_swift."""

    def test_class_inheritance(self, tmp_path: Path) -> None:
        """Test class inheriting from another class."""
        make_swift_file(tmp_path, "Animals.swift", """
class Animal {
    func speak() {}
}

class Dog: Animal {
    override func speak() {}
}
""")
        data = analyze(tmp_path)
        dog = next((n for n in data["nodes"] if n["name"] == "Dog"), None)
        assert dog is not None
        assert dog.get("meta", {}).get("base_classes") == ["Animal"]

    def test_protocol_conformance(self, tmp_path: Path) -> None:
        """Test struct conforming to protocol."""
        make_swift_file(tmp_path, "Point.swift", """
protocol Equatable {}

struct Point: Equatable {
    var x: Int
    var y: Int
}
""")
        data = analyze(tmp_path)
        point = next((n for n in data["nodes"] if n["name"] == "Point"), None)
        assert point is not None
        assert point.get("meta", {}).get("base_classes") == ["Equatable"]

    def test_multiple_protocols(self, tmp_path: Path) -> None:
        """Test class conforming to multiple protocols."""
        make_swift_file(tmp_path, "Vehicle.swift", """
protocol Drivable {}
protocol Flyable {}

class Car: Drivable, Flyable {
    func drive() {}
}
""")
        data = analyze(tmp_path)
        car = next((n for n in data["nodes"] if n["name"] == "Car"), None)
        assert car is not None
        bases = car.get("meta", {}).get("base_classes", [])
        assert "Drivable" in bases or "Flyable" in bases

    def test_class_no_inheritance(self, tmp_path: Path) -> None:
        """Test class without any inheritance."""
        make_swift_file(tmp_path, "Simple.swift", """
class Simple {
    func method() {}
}
""")
        data = analyze(tmp_path)
        simple = next((n for n in data["nodes"] if n["name"] == "Simple"), None)
        assert simple is not None
        assert simple.get("meta") is None or simple.get("meta", {}).get("base_classes") is None


class TestSwiftSignatureExtraction:
    """Branch coverage for _extract_swift_signature."""

    def test_user_type_param(self, tmp_path: Path) -> None:
        """Test function with user-defined type parameter."""
        make_swift_file(tmp_path, "UserFunc.swift", """
class User {}

func greet(user: User) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "greet"), None)
        assert func is not None
        assert "user: User" in func["signature"]

    def test_array_type_param(self, tmp_path: Path) -> None:
        """Test function with array type parameter."""
        make_swift_file(tmp_path, "ArrayFunc.swift", """
func process(items: [Int]) -> Int {
    return items.count
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "process"), None)
        assert func is not None
        assert "[Int]" in func["signature"]

    def test_dictionary_type_param(self, tmp_path: Path) -> None:
        """Test function with dictionary type parameter."""
        make_swift_file(tmp_path, "DictFunc.swift", """
func lookup(data: [String: Int]) -> Int {
    return data.count
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "lookup"), None)
        assert func is not None
        assert "String" in func["signature"] and "Int" in func["signature"]

    def test_optional_type_param(self, tmp_path: Path) -> None:
        """Test function with optional type parameter."""
        make_swift_file(tmp_path, "OptFunc.swift", """
func display(name: String?) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "display"), None)
        assert func is not None
        assert "?" in func["signature"]

    def test_tuple_type_param(self, tmp_path: Path) -> None:
        """Test function with tuple type parameter."""
        make_swift_file(tmp_path, "TupleFunc.swift", """
func pair(t: (Int, String)) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "pair"), None)
        assert func is not None
        # Tuple syntax in signature
        assert "(" in func["signature"]

    def test_function_type_param(self, tmp_path: Path) -> None:
        """Test function with closure/function type parameter."""
        make_swift_file(tmp_path, "ClosureFunc.swift", """
func onComplete(callback: (Int) -> Void) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "onComplete"), None)
        assert func is not None
        assert "->" in func["signature"]

    def test_return_type_included(self, tmp_path: Path) -> None:
        """Test function with return type is included in signature."""
        make_swift_file(tmp_path, "ReturnFunc.swift", """
func calculate(x: Int) -> Int {
    return x * 2
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "calculate"), None)
        assert func is not None
        assert "-> Int" in func["signature"]

    def test_void_no_return_type(self, tmp_path: Path) -> None:
        """Test function with no return type (void)."""
        make_swift_file(tmp_path, "VoidFunc.swift", """
func action(x: Int) {
    print(x)
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "action"), None)
        assert func is not None
        # No return type arrow
        assert "->" not in func["signature"]


class TestSwiftTypeDeclarations:
    """Branch coverage for type declaration kind detection."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration is detected as class kind."""
        make_swift_file(tmp_path, "MyClass.swift", """
class MyClass {
    func method() {}
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "MyClass"), None)
        assert cls is not None
        assert cls["kind"] == "class"

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration is detected as struct kind."""
        make_swift_file(tmp_path, "MyStruct.swift", """
struct MyStruct {
    var value: Int
}
""")
        data = analyze(tmp_path)
        st = next((n for n in data["nodes"] if n["name"] == "MyStruct"), None)
        assert st is not None
        assert st["kind"] == "struct"

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration is detected as enum kind."""
        make_swift_file(tmp_path, "MyEnum.swift", """
enum MyEnum {
    case first
    case second
}
""")
        data = analyze(tmp_path)
        en = next((n for n in data["nodes"] if n["name"] == "MyEnum"), None)
        assert en is not None
        assert en["kind"] == "enum"

    def test_protocol_declaration(self, tmp_path: Path) -> None:
        """Test protocol declaration is detected as protocol kind."""
        make_swift_file(tmp_path, "MyProtocol.swift", """
protocol MyProtocol {
    func action()
}
""")
        data = analyze(tmp_path)
        proto = next((n for n in data["nodes"] if n["name"] == "MyProtocol"), None)
        assert proto is not None
        assert proto["kind"] == "protocol"

    def test_protocol_inheritance(self, tmp_path: Path) -> None:
        """Test protocol inheriting from another protocol."""
        make_swift_file(tmp_path, "Protocols.swift", """
protocol BaseProtocol {
    func base()
}

protocol ExtendedProtocol: BaseProtocol {
    func extended()
}
""")
        data = analyze(tmp_path)
        ext = next((n for n in data["nodes"] if n["name"] == "ExtendedProtocol"), None)
        assert ext is not None
        assert ext["kind"] == "protocol"
        assert ext.get("meta", {}).get("base_classes") == ["BaseProtocol"]


class TestSwiftFunctionDeclarations:
    """Branch coverage for function extraction."""

    def test_method_in_class(self, tmp_path: Path) -> None:
        """Test method inside class (with enclosing type)."""
        make_swift_file(tmp_path, "ClassMethod.swift", """
class Service {
    func process() {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Service.process"), None)
        assert method is not None
        assert method["kind"] == "method"

    def test_standalone_function(self, tmp_path: Path) -> None:
        """Test top-level function (without enclosing type)."""
        make_swift_file(tmp_path, "TopLevel.swift", """
func helper() {}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "helper"), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_multiple_parameters(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_swift_file(tmp_path, "MultiParam.swift", """
func add(a: Int, b: Int) -> Int {
    return a + b
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "add"), None)
        assert func is not None
        assert "a: Int" in func["signature"]
        assert "b: Int" in func["signature"]


class TestSwiftImports:
    """Branch coverage for import extraction."""

    def test_import_module(self, tmp_path: Path) -> None:
        """Test import statement creates edge."""
        make_swift_file(tmp_path, "App.swift", """
import Foundation

func main() {
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("Foundation" in e["dst"] for e in edges)

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """Test multiple import statements."""
        make_swift_file(tmp_path, "Multi.swift", """
import Foundation
import UIKit

func setup() {
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert len(edges) >= 2


class TestSwiftCallResolution:
    """Branch coverage for call resolution."""

    def test_local_call(self, tmp_path: Path) -> None:
        """Test call to function in same file (local resolution)."""
        make_swift_file(tmp_path, "Local.swift", """
func helper() {}

func main() {
    helper()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different file (global resolution)."""
        make_swift_file(tmp_path, "Utils.swift", """
func format(s: String) -> String {
    return s
}
""")
        make_swift_file(tmp_path, "App.swift", """
func run() {
    format(s: "test")
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("format" in e["dst"] for e in edges)
