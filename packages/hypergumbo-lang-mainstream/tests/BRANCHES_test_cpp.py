# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for cpp.py analyzer.

Tests specific branch paths in the C++ analyzer that may not be covered
by the main test suite. Focuses on:
- Base class extraction (simple, qualified)
- Signature extraction with various return types
- Function name extraction (qualified, simple, field identifier)
- Symbol extraction (classes, structs, enums, functions, methods)
- Namespace alias extraction
- Include directives (local, system)
- Call resolution with namespace aliases
- New expression instantiation
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_cpp_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a C++ file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestCppBaseClasses:
    """Branch coverage for _extract_base_classes_cpp."""

    def test_simple_inheritance(self, tmp_path: Path) -> None:
        """Test class inheriting from simple type identifier."""
        make_cpp_file(tmp_path, "animals.cpp", """
class Animal {
public:
    void speak() {}
};

class Dog : public Animal {
public:
    void bark() {}
};
""")
        data = analyze(tmp_path)
        dog = next((n for n in data["nodes"] if n["name"] == "Dog"), None)
        assert dog is not None
        assert dog.get("meta", {}).get("base_classes") == ["Animal"]

    def test_qualified_inheritance(self, tmp_path: Path) -> None:
        """Test class inheriting from qualified identifier like std::exception."""
        make_cpp_file(tmp_path, "error.cpp", """
#include <exception>

class MyError : public std::runtime_error {
public:
    void what() {}
};
""")
        data = analyze(tmp_path)
        err = next((n for n in data["nodes"] if n["name"] == "MyError"), None)
        assert err is not None
        bases = err.get("meta", {}).get("base_classes", [])
        assert any("runtime_error" in b for b in bases)

    def test_multiple_inheritance(self, tmp_path: Path) -> None:
        """Test class with multiple inheritance."""
        make_cpp_file(tmp_path, "multi.cpp", """
class Base1 {};
class Base2 {};

class Derived : public Base1, public Base2 {
    void method() {}
};
""")
        data = analyze(tmp_path)
        derived = next((n for n in data["nodes"] if n["name"] == "Derived"), None)
        assert derived is not None
        bases = derived.get("meta", {}).get("base_classes", [])
        assert "Base1" in bases and "Base2" in bases

    def test_struct_inheritance(self, tmp_path: Path) -> None:
        """Test struct inheriting from base type."""
        make_cpp_file(tmp_path, "structs.cpp", """
struct Base {
    int x;
};

struct Derived : Base {
    int y;
};
""")
        data = analyze(tmp_path)
        derived = next((n for n in data["nodes"] if n["name"] == "Derived"), None)
        assert derived is not None
        assert derived["kind"] == "struct"
        assert derived.get("meta", {}).get("base_classes") == ["Base"]

    def test_class_no_inheritance(self, tmp_path: Path) -> None:
        """Test class without inheritance."""
        make_cpp_file(tmp_path, "simple.cpp", """
class Simple {
public:
    void method() {}
};
""")
        data = analyze(tmp_path)
        simple = next((n for n in data["nodes"] if n["name"] == "Simple"), None)
        assert simple is not None
        assert simple.get("meta") is None or simple.get("meta", {}).get("base_classes") is None


class TestCppSignatureExtraction:
    """Branch coverage for _extract_cpp_signature."""

    def test_primitive_return_type(self, tmp_path: Path) -> None:
        """Test function with primitive return type."""
        make_cpp_file(tmp_path, "prim.cpp", """
int add(int a, int b) {
    return a + b;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "add"), None)
        assert func is not None
        assert "int a" in func["signature"]
        assert "int" in func["signature"]

    def test_void_return_type(self, tmp_path: Path) -> None:
        """Test function with void return type (should not appear in signature)."""
        make_cpp_file(tmp_path, "void_ret.cpp", """
void action(int x) {
    // do something
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "action"), None)
        assert func is not None
        # void should not appear after params
        assert func["signature"].endswith(")")

    def test_type_identifier_return(self, tmp_path: Path) -> None:
        """Test function with user-defined type return."""
        make_cpp_file(tmp_path, "user_ret.cpp", """
class Result {};

Result compute(int x) {
    return Result();
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "compute"), None)
        assert func is not None
        assert "Result" in func["signature"]

    def test_qualified_return_type(self, tmp_path: Path) -> None:
        """Test function with qualified identifier return type."""
        make_cpp_file(tmp_path, "qual_ret.cpp", """
#include <string>

std::string getName() {
    return "test";
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getName"), None)
        assert func is not None
        assert "string" in func["signature"]

    def test_static_function(self, tmp_path: Path) -> None:
        """Test function with static storage class."""
        make_cpp_file(tmp_path, "static_func.cpp", """
static int getValue() {
    return 42;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getValue"), None)
        assert func is not None
        # static may or may not appear depending on extraction
        assert "int" in func["signature"]

    def test_sized_type_return(self, tmp_path: Path) -> None:
        """Test function with sized type specifier return (long int)."""
        make_cpp_file(tmp_path, "sized_ret.cpp", """
long int getBigValue() {
    return 12345678L;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getBigValue"), None)
        assert func is not None
        assert "long" in func["signature"] or "int" in func["signature"]


class TestCppFunctionNameExtraction:
    """Branch coverage for _extract_function_name."""

    def test_qualified_method(self, tmp_path: Path) -> None:
        """Test method with qualified identifier (Class::method)."""
        make_cpp_file(tmp_path, "qualified.cpp", """
class MyClass {
public:
    void process();
};

void MyClass::process() {
    // implementation
}
""")
        data = analyze(tmp_path)
        # Should find the method with qualified name
        method = next((n for n in data["nodes"] if "process" in n["name"] and n["kind"] == "method"), None)
        assert method is not None
        assert "MyClass::process" in method["name"]

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test standalone function with simple identifier."""
        make_cpp_file(tmp_path, "simple_func.cpp", """
void helper() {
    // standalone function
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "helper"), None)
        assert func is not None
        assert func["kind"] == "function"


class TestCppSymbolExtraction:
    """Branch coverage for symbol extraction branches."""

    def test_class_specifier(self, tmp_path: Path) -> None:
        """Test class specifier extraction."""
        make_cpp_file(tmp_path, "myclass.cpp", """
class MyClass {
    int value;
};
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "MyClass"), None)
        assert cls is not None
        assert cls["kind"] == "class"

    def test_struct_specifier(self, tmp_path: Path) -> None:
        """Test struct specifier extraction."""
        make_cpp_file(tmp_path, "mystruct.cpp", """
struct MyStruct {
    int x;
    int y;
};
""")
        data = analyze(tmp_path)
        st = next((n for n in data["nodes"] if n["name"] == "MyStruct"), None)
        assert st is not None
        assert st["kind"] == "struct"

    def test_enum_specifier(self, tmp_path: Path) -> None:
        """Test enum specifier extraction."""
        make_cpp_file(tmp_path, "myenum.cpp", """
enum Color {
    RED,
    GREEN,
    BLUE
};
""")
        data = analyze(tmp_path)
        en = next((n for n in data["nodes"] if n["name"] == "Color"), None)
        assert en is not None
        assert en["kind"] == "enum"


class TestCppNamespaceAliases:
    """Branch coverage for _extract_namespace_aliases."""

    def test_namespace_alias(self, tmp_path: Path) -> None:
        """Test namespace alias extraction."""
        make_cpp_file(tmp_path, "alias.cpp", """
namespace fs = std::filesystem;

void test() {
    // uses fs alias
}
""")
        data = analyze(tmp_path)
        # Alias should be extracted for disambiguation
        # We can't directly test the alias dict, but verify the file parses
        func = next((n for n in data["nodes"] if n["name"] == "test"), None)
        assert func is not None


class TestCppIncludeDirectives:
    """Branch coverage for include directive edge creation."""

    def test_local_include(self, tmp_path: Path) -> None:
        """Test local include with double quotes."""
        make_cpp_file(tmp_path, "local_inc.cpp", """
#include "myheader.h"

void test() {}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("myheader.h" in e["dst"] for e in edges)

    def test_system_include(self, tmp_path: Path) -> None:
        """Test system include with angle brackets."""
        make_cpp_file(tmp_path, "sys_inc.cpp", """
#include <iostream>

void test() {}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("iostream" in e["dst"] for e in edges)

    def test_multiple_includes(self, tmp_path: Path) -> None:
        """Test multiple includes."""
        make_cpp_file(tmp_path, "multi_inc.cpp", """
#include <vector>
#include <string>
#include "local.h"

void test() {}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert len(edges) >= 3


class TestCppCallResolution:
    """Branch coverage for call expression resolution."""

    def test_local_call(self, tmp_path: Path) -> None:
        """Test call to local function."""
        make_cpp_file(tmp_path, "local_call.cpp", """
void helper() {}

void main() {
    helper();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_qualified_call(self, tmp_path: Path) -> None:
        """Test call with qualified identifier (Class::method)."""
        make_cpp_file(tmp_path, "qual_call.cpp", """
class Utils {
public:
    static void format() {}
};

void test() {
    Utils::format();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("format" in e["dst"] for e in edges)

    def test_field_expression_call(self, tmp_path: Path) -> None:
        """Test call with field expression (obj.method())."""
        make_cpp_file(tmp_path, "field_call.cpp", """
class Service {
public:
    void run() {}
};

void test() {
    Service s;
    s.run();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("run" in e["dst"] for e in edges)


class TestCppNewExpression:
    """Branch coverage for new expression instantiation."""

    def test_new_simple_type(self, tmp_path: Path) -> None:
        """Test new expression with simple type identifier."""
        make_cpp_file(tmp_path, "new_simple.cpp", """
class Widget {};

void test() {
    Widget* w = new Widget();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert any("Widget" in e["dst"] for e in edges)

    def test_new_qualified_type(self, tmp_path: Path) -> None:
        """Test new expression with qualified identifier."""
        make_cpp_file(tmp_path, "new_qual.cpp", """
namespace ui {
    class Button {};
}

void test() {
    ui::Button* b = new ui::Button();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert any("Button" in e["dst"] for e in edges)


class TestCppCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different file."""
        make_cpp_file(tmp_path, "utils.cpp", """
void utility() {}
""")
        make_cpp_file(tmp_path, "main.cpp", """
void utility();

void main() {
    utility();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("utility" in e["dst"] for e in edges)
