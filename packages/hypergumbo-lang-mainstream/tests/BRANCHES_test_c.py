"""Branch coverage tests for c.py analyzer.

Tests specific branch paths in the C analyzer that may not be covered
by the main test suite. Focuses on:
- Function name extraction (direct declarator, pointer declarator)
- Signature extraction with various parameter and return types
- Symbol extraction (functions, structs, enums, typedefs)
- Call resolution with global symbol registry
- Header vs source file preference for definitions
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_c_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a C file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestCFunctionNameExtraction:
    """Branch coverage for _get_function_name."""

    def test_simple_function_declarator(self, tmp_path: Path) -> None:
        """Test function with simple declarator."""
        make_c_file(tmp_path, "simple.c", """
int add(int a, int b) {
    return a + b;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "add"), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_pointer_return_type(self, tmp_path: Path) -> None:
        """Test function with pointer return type (pointer_declarator)."""
        make_c_file(tmp_path, "pointer.c", """
int* allocate(int size) {
    return 0;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "allocate"), None)
        assert func is not None
        assert func["kind"] == "function"
        # Signature should include pointer indicator
        assert "int" in func["signature"]


class TestCSignatureExtraction:
    """Branch coverage for _extract_c_signature."""

    def test_primitive_return_type(self, tmp_path: Path) -> None:
        """Test function with primitive return type."""
        make_c_file(tmp_path, "prim.c", """
int getValue() {
    return 42;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getValue"), None)
        assert func is not None
        assert "int" in func["signature"]

    def test_void_return_type(self, tmp_path: Path) -> None:
        """Test function with void return type (should not appear in signature)."""
        make_c_file(tmp_path, "void_ret.c", """
void action(int x) {
    // do something
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "action"), None)
        assert func is not None
        assert func["signature"].endswith(")")

    def test_type_identifier_return(self, tmp_path: Path) -> None:
        """Test function with user-defined type return."""
        make_c_file(tmp_path, "user_ret.c", """
typedef struct { int x; } Point;

Point getPoint() {
    Point p = {0};
    return p;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getPoint"), None)
        assert func is not None
        assert "Point" in func["signature"]

    def test_sized_type_return(self, tmp_path: Path) -> None:
        """Test function with sized type specifier return (long int)."""
        make_c_file(tmp_path, "sized_ret.c", """
long int getBigValue() {
    return 12345678L;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getBigValue"), None)
        assert func is not None
        assert "long" in func["signature"] or "int" in func["signature"]

    def test_storage_class_specifier(self, tmp_path: Path) -> None:
        """Test function with static storage class."""
        make_c_file(tmp_path, "static_func.c", """
static int helper() {
    return 0;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "helper"), None)
        assert func is not None
        # Static may or may not appear in signature
        assert "int" in func["signature"]

    def test_type_qualifier(self, tmp_path: Path) -> None:
        """Test function with const qualifier."""
        make_c_file(tmp_path, "const_func.c", """
const int getConst() {
    return 0;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getConst"), None)
        assert func is not None
        # Const may appear in signature
        assert "int" in func["signature"]

    def test_multiple_parameters(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_c_file(tmp_path, "multi_param.c", """
int compute(int a, int b, int c) {
    return a + b + c;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "compute"), None)
        assert func is not None
        assert "int a" in func["signature"]
        assert "int b" in func["signature"]
        assert "int c" in func["signature"]


class TestCSymbolExtraction:
    """Branch coverage for _extract_symbols."""

    def test_function_definition(self, tmp_path: Path) -> None:
        """Test function definition extraction."""
        make_c_file(tmp_path, "func.c", """
void myFunc() {
    // implementation
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "myFunc"), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration (prototype) extraction."""
        make_c_file(tmp_path, "proto.h", """
int calculate(int x, int y);
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "calculate"), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_struct_specifier(self, tmp_path: Path) -> None:
        """Test struct specifier extraction."""
        make_c_file(tmp_path, "mystruct.c", """
struct Point {
    int x;
    int y;
};
""")
        data = analyze(tmp_path)
        st = next((n for n in data["nodes"] if n["name"] == "Point"), None)
        assert st is not None
        assert st["kind"] == "struct"

    def test_enum_specifier(self, tmp_path: Path) -> None:
        """Test enum specifier extraction."""
        make_c_file(tmp_path, "myenum.c", """
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

    def test_typedef_declaration(self, tmp_path: Path) -> None:
        """Test typedef declaration extraction with named struct."""
        make_c_file(tmp_path, "types.c", """
typedef struct Point {
    int x;
    int y;
} Point;
""")
        data = analyze(tmp_path)
        # Typedef creates type_identifier for the alias name
        td = next((n for n in data["nodes"] if n["name"] == "Point" and n["kind"] == "typedef"), None)
        assert td is not None

    def test_typedef_struct(self, tmp_path: Path) -> None:
        """Test typedef with struct."""
        make_c_file(tmp_path, "typedef_struct.c", """
typedef struct {
    int width;
    int height;
} Rectangle;
""")
        data = analyze(tmp_path)
        td = next((n for n in data["nodes"] if n["name"] == "Rectangle"), None)
        assert td is not None
        assert td["kind"] == "typedef"


class TestCCallResolution:
    """Branch coverage for _extract_edges."""

    def test_local_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_c_file(tmp_path, "local_call.c", """
void helper() {
}

void main_func() {
    helper();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different file."""
        make_c_file(tmp_path, "utils.c", """
void utility() {
}
""")
        make_c_file(tmp_path, "main.c", """
void utility();

void app() {
    utility();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("utility" in e["dst"] for e in edges)


class TestCHeaderVsSourcePreference:
    """Branch coverage for global symbol registry preference."""

    def test_prefers_source_over_header(self, tmp_path: Path) -> None:
        """Test that .c definitions are preferred over .h declarations."""
        make_c_file(tmp_path, "func.h", """
int process(int x);
""")
        make_c_file(tmp_path, "func.c", """
int process(int x) {
    return x * 2;
}

void caller() {
    process(42);
}
""")
        data = analyze(tmp_path)
        # The call edge should point to the .c file implementation
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        process_edge = next((e for e in edges if "process" in e["dst"]), None)
        assert process_edge is not None
        # Should reference the .c file, not the .h file
        assert "func.c" in process_edge["dst"]

    def test_header_only(self, tmp_path: Path) -> None:
        """Test function declared only in header."""
        make_c_file(tmp_path, "lib.h", """
int compute(int a, int b);
""")
        make_c_file(tmp_path, "app.c", """
int compute(int a, int b);

void run() {
    compute(1, 2);
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("compute" in e["dst"] for e in edges)


class TestCIdentifierTypes:
    """Branch coverage for identifier vs type_identifier handling."""

    def test_identifier_in_function(self, tmp_path: Path) -> None:
        """Test identifier extraction from function declarator."""
        make_c_file(tmp_path, "ident.c", """
int myFunction(int param) {
    return param;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "myFunction"), None)
        assert func is not None

    def test_type_identifier_in_struct(self, tmp_path: Path) -> None:
        """Test type_identifier extraction from struct."""
        make_c_file(tmp_path, "struct_ident.c", """
struct MyStruct {
    int value;
};
""")
        data = analyze(tmp_path)
        st = next((n for n in data["nodes"] if n["name"] == "MyStruct"), None)
        assert st is not None
        assert st["kind"] == "struct"


class TestCPointerDeclarators:
    """Branch coverage for pointer_declarator handling."""

    def test_void_pointer_return(self, tmp_path: Path) -> None:
        """Test function with void pointer return type."""
        make_c_file(tmp_path, "void_ptr.c", """
void* allocate(int size) {
    return 0;
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "allocate"), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_char_pointer_return(self, tmp_path: Path) -> None:
        """Test function with char* return type."""
        make_c_file(tmp_path, "char_ptr.c", """
char* getString() {
    return "hello";
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "getString"), None)
        assert func is not None
        assert func["kind"] == "function"


class TestCNestedCalls:
    """Branch coverage for nested function calls."""

    def test_nested_call(self, tmp_path: Path) -> None:
        """Test nested function calls."""
        make_c_file(tmp_path, "nested.c", """
int inner() {
    return 1;
}

int outer() {
    return inner() + 1;
}

void main_entry() {
    outer();
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        # Should have edges for both inner and outer calls
        assert any("inner" in e["dst"] for e in edges)
        assert any("outer" in e["dst"] for e in edges)


class TestCMultipleFiles:
    """Branch coverage for multi-file analysis."""

    def test_multiple_source_files(self, tmp_path: Path) -> None:
        """Test analysis of multiple C source files."""
        make_c_file(tmp_path, "file1.c", """
void func1() {
}
""")
        make_c_file(tmp_path, "file2.c", """
void func2() {
}
""")
        make_c_file(tmp_path, "file3.c", """
void func1();
void func2();

void main_app() {
    func1();
    func2();
}
""")
        data = analyze(tmp_path)
        # Should find symbols from all files
        func1 = next((n for n in data["nodes"] if n["name"] == "func1"), None)
        func2 = next((n for n in data["nodes"] if n["name"] == "func2"), None)
        main_app = next((n for n in data["nodes"] if n["name"] == "main_app"), None)
        assert func1 is not None
        assert func2 is not None
        assert main_app is not None

        # Should have call edges
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("func1" in e["dst"] for e in edges)
        assert any("func2" in e["dst"] for e in edges)
