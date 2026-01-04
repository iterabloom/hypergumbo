"""Tests for C++ analyzer.

Tests for the tree-sitter-based C++ analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo.analyze.cpp import (
    analyze_cpp,
    find_cpp_files,
    is_cpp_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def cpp_repo(tmp_path: Path) -> Path:
    """Create a minimal C++ repository for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main class header
    (src / "Calculator.hpp").write_text(
        """#ifndef CALCULATOR_HPP
#define CALCULATOR_HPP

namespace MyApp {

class Calculator {
public:
    int Add(int a, int b);
    int Multiply(int a, int b);

private:
    int counter = 0;
};

}

#endif
"""
    )

    # Class implementation
    (src / "Calculator.cpp").write_text(
        """#include "Calculator.hpp"
#include "Helper.hpp"

namespace MyApp {

int Calculator::Add(int a, int b) {
    counter++;
    return a + b;
}

int Calculator::Multiply(int a, int b) {
    return Helper::Process(a * b);
}

}
"""
    )

    # Helper class
    (src / "Helper.hpp").write_text(
        """#ifndef HELPER_HPP
#define HELPER_HPP

class Helper {
public:
    static int Process(int value);
};

#endif
"""
    )

    (src / "Helper.cpp").write_text(
        """#include "Helper.hpp"

int Helper::Process(int value) {
    return value * 2;
}
"""
    )

    # Struct and enum
    (src / "Types.hpp").write_text(
        """#ifndef TYPES_HPP
#define TYPES_HPP

struct Point {
    int x;
    int y;
};

enum Color {
    RED,
    GREEN,
    BLUE
};

#endif
"""
    )

    # Main entry point
    (src / "main.cpp").write_text(
        """#include <iostream>
#include "Calculator.hpp"

int main(int argc, char* argv[]) {
    MyApp::Calculator calc;
    std::cout << calc.Add(1, 2) << std::endl;
    auto ptr = new MyApp::Calculator();
    delete ptr;
    return 0;
}
"""
    )

    return tmp_path


class TestCppFileDiscovery:
    """Tests for C++ file discovery."""

    def test_finds_cpp_files(self, cpp_repo: Path) -> None:
        """Should find all .cpp and .hpp files."""
        files = list(find_cpp_files(cpp_repo))
        assert len(files) == 6

    def test_file_names(self, cpp_repo: Path) -> None:
        """Should find expected files."""
        files = [f.name for f in find_cpp_files(cpp_repo)]
        assert "Calculator.cpp" in files
        assert "Calculator.hpp" in files
        assert "main.cpp" in files


class TestCppSymbolExtraction:
    """Tests for symbol extraction from C++ files."""

    def test_extracts_classes(self, cpp_repo: Path) -> None:
        """Should extract class declarations."""
        result = analyze_cpp(cpp_repo)


        class_symbols = [s for s in result.symbols if s.kind == "class"]
        class_names = {s.name for s in class_symbols}
        assert "Calculator" in class_names
        assert "Helper" in class_names

    def test_extracts_structs(self, cpp_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_cpp(cpp_repo)


        struct_symbols = [s for s in result.symbols if s.kind == "struct"]
        struct_names = {s.name for s in struct_symbols}
        assert "Point" in struct_names

    def test_extracts_enums(self, cpp_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_cpp(cpp_repo)


        enum_symbols = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enum_symbols}
        assert "Color" in enum_names

    def test_extracts_functions(self, cpp_repo: Path) -> None:
        """Should extract function definitions."""
        result = analyze_cpp(cpp_repo)


        func_symbols = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in func_symbols}
        assert "main" in func_names

    def test_extracts_methods(self, cpp_repo: Path) -> None:
        """Should extract class method implementations."""
        result = analyze_cpp(cpp_repo)


        method_symbols = [s for s in result.symbols if s.kind == "method"]
        method_names = {s.name for s in method_symbols}
        # Class methods have qualified names
        assert "Calculator::Add" in method_names or "Add" in method_names
        assert "Calculator::Multiply" in method_names or "Multiply" in method_names

    def test_symbols_have_correct_language(self, cpp_repo: Path) -> None:
        """All symbols should have language='cpp'."""
        result = analyze_cpp(cpp_repo)


        for symbol in result.symbols:
            assert symbol.language == "cpp"

    def test_symbols_have_spans(self, cpp_repo: Path) -> None:
        """All symbols should have valid span information."""
        result = analyze_cpp(cpp_repo)


        for symbol in result.symbols:
            assert symbol.span is not None
            assert symbol.span.start_line > 0
            assert symbol.span.end_line >= symbol.span.start_line


class TestCppEdgeExtraction:
    """Tests for edge extraction from C++ files."""

    def test_extracts_include_edges(self, cpp_repo: Path) -> None:
        """Should extract #include directive edges."""
        result = analyze_cpp(cpp_repo)


        include_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(include_edges) >= 3  # At least Calculator.hpp, Helper.hpp, iostream

    def test_extracts_call_edges(self, cpp_repo: Path) -> None:
        """Should extract function call edges."""
        result = analyze_cpp(cpp_repo)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have calls to Add, Multiply, Process
        assert len(call_edges) >= 1

    def test_extracts_instantiate_edges(self, cpp_repo: Path) -> None:
        """Should extract new expression edges."""
        result = analyze_cpp(cpp_repo)


        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        # main creates new Calculator()
        assert len(instantiate_edges) >= 1

    def test_edges_have_confidence(self, cpp_repo: Path) -> None:
        """All edges should have confidence values."""
        result = analyze_cpp(cpp_repo)


        for edge in result.edges:
            assert 0.0 <= edge.confidence <= 1.0


class TestCppAnalysisRun:
    """Tests for analysis run metadata."""

    def test_creates_analysis_run(self, cpp_repo: Path) -> None:
        """Should create an AnalysisRun with metadata."""
        result = analyze_cpp(cpp_repo)


        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.files_analyzed == 6
        assert result.run.duration_ms >= 0

    def test_symbols_reference_run(self, cpp_repo: Path) -> None:
        """Symbols should reference the analysis run."""
        result = analyze_cpp(cpp_repo)


        for symbol in result.symbols:
            assert symbol.origin == PASS_ID
            assert symbol.origin_run_id == result.run.execution_id


class TestCppGracefulDegradation:
    """Tests for graceful degradation when tree-sitter unavailable."""

    def test_returns_skipped_when_unavailable(self) -> None:
        """Should return skipped result when tree-sitter unavailable."""
        with patch(
            "hypergumbo.analyze.cpp.is_cpp_tree_sitter_available",
            return_value=False,
        ):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = analyze_cpp(Path("/nonexistent"))
                assert result.skipped
                assert "tree-sitter-cpp" in result.skip_reason
                assert len(w) == 1


class TestCppTreeSitterAvailability:
    """Tests for tree-sitter availability detection."""

    def test_detects_missing_tree_sitter(self) -> None:
        """Should detect when tree-sitter is not installed."""
        with patch("importlib.util.find_spec", return_value=None):
            assert not is_cpp_tree_sitter_available()

    def test_detects_missing_cpp_grammar(self) -> None:
        """Should detect when tree-sitter-cpp is not installed."""
        def find_spec_mock(name: str):
            if name == "tree_sitter":
                return True
            return None

        with patch("importlib.util.find_spec", side_effect=find_spec_mock):
            assert not is_cpp_tree_sitter_available()


class TestCppSpecialCases:
    """Tests for special C++ syntax cases."""

    def test_handles_templates(self, tmp_path: Path) -> None:
        """Should handle template declarations."""
        (tmp_path / "template.hpp").write_text(
            """template<typename T>
class Container {
public:
    T value;
    T get() { return value; }
    void set(T v) { value = v; }
};
"""
        )

        result = analyze_cpp(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1

    def test_handles_namespaces(self, tmp_path: Path) -> None:
        """Should handle namespace declarations."""
        (tmp_path / "namespace.cpp").write_text(
            """namespace Outer {
namespace Inner {

void process() {}

}
}
"""
        )

        result = analyze_cpp(tmp_path)


        # Should find the process function
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1

    def test_handles_empty_files(self, tmp_path: Path) -> None:
        """Should handle empty C++ files gracefully."""
        (tmp_path / "empty.cpp").write_text("")

        result = analyze_cpp(tmp_path)


        # Should not crash
        assert result.run is not None

    def test_handles_io_errors(self, tmp_path: Path) -> None:
        """Should handle IO errors gracefully."""
        result = analyze_cpp(tmp_path)


        # Empty repo should not crash
        assert result.symbols == []
        assert result.edges == []

    def test_handles_method_calls(self, tmp_path: Path) -> None:
        """Should detect method calls on objects."""
        (tmp_path / "calls.cpp").write_text(
            """class Foo {
public:
    void bar() {}
};

void test() {
    Foo f;
    f.bar();
}
"""
        )

        result = analyze_cpp(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # test should call bar
        assert len(call_edges) >= 1

    def test_handles_static_method_calls(self, tmp_path: Path) -> None:
        """Should detect static method calls."""
        (tmp_path / "static.cpp").write_text(
            """class Utils {
public:
    static int compute(int x) { return x * 2; }
};

int main() {
    return Utils::compute(5);
}
"""
        )

        result = analyze_cpp(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # main should call compute
        assert len(call_edges) >= 1

    def test_handles_direct_function_calls(self, tmp_path: Path) -> None:
        """Should detect direct function calls (no object or class)."""
        (tmp_path / "direct.cpp").write_text(
            """void helper() {}

void caller() {
    helper();
}
"""
        )

        result = analyze_cpp(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # caller should call helper
        assert len(call_edges) >= 1

    def test_handles_simple_new(self, tmp_path: Path) -> None:
        """Should detect simple new expressions without namespace."""
        (tmp_path / "simple_new.cpp").write_text(
            """class Widget {};

void create() {
    Widget* w = new Widget();
}
"""
        )

        result = analyze_cpp(tmp_path)


        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        # create should instantiate Widget
        assert len(instantiate_edges) >= 1


class TestCppSignatureExtraction:
    """Tests for C++ function signature extraction."""

    def test_basic_function_signature(self, tmp_path: Path) -> None:
        """Basic function with parameters extracts signature."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "math.cpp"
        cpp_file.write_text("int add(int x, int y) { return x + y; }")

        result = analyze_cpp(tmp_path)

        add_sym = next((s for s in result.symbols if s.name == "add"), None)
        assert add_sym is not None
        assert add_sym.signature == "(int x, int y) int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Void return type function extracts signature without return type."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "util.cpp"
        cpp_file.write_text("void process(int count) { /* work */ }")

        result = analyze_cpp(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        assert process_sym.signature == "(int count)"

    def test_reference_parameter_signature(self, tmp_path: Path) -> None:
        """Reference parameters appear in signature."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "str.cpp"
        cpp_file.write_text("int size(const std::string& str) { return 0; }")

        result = analyze_cpp(tmp_path)

        size_sym = next((s for s in result.symbols if s.name == "size"), None)
        assert size_sym is not None
        assert "const std::string& str" in size_sym.signature
        assert size_sym.signature.endswith("int")

    def test_class_method_signature(self, tmp_path: Path) -> None:
        """Class method has signature extracted."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "class.cpp"
        cpp_file.write_text("void MyClass::process(int value) { /* impl */ }")

        result = analyze_cpp(tmp_path)

        method_sym = next((s for s in result.symbols if "process" in s.name), None)
        assert method_sym is not None
        assert method_sym.signature == "(int value)"

    def test_empty_params_signature(self, tmp_path: Path) -> None:
        """Function with no parameters has empty parens."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        result = analyze_cpp(tmp_path)

        main_sym = next((s for s in result.symbols if s.name == "main"), None)
        assert main_sym is not None
        assert main_sym.signature == "() int"

    def test_qualified_return_type(self, tmp_path: Path) -> None:
        """Qualified return type (std::string) appears in signature."""
        from hypergumbo.analyze.cpp import analyze_cpp

        cpp_file = tmp_path / "str.cpp"
        cpp_file.write_text("std::string getName() { return \"\"; }")

        result = analyze_cpp(tmp_path)

        get_sym = next((s for s in result.symbols if s.name == "getName"), None)
        assert get_sym is not None
        assert "std::string" in get_sym.signature

