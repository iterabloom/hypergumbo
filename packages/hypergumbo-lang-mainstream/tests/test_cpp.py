# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for C++ analyzer.

Tests for the tree-sitter-based C++ analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_mainstream.cpp import (
    _extract_field_types_cpp,
    _get_enclosing_class,
    _resolve_cpp_field_chain,
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


class TestCppHeaderDedup:
    """Tests for .h file dedup between C and C++ analyzers.

    When a repo has .c files but NO C++ source files (.cpp/.cc/.cxx),
    .h files should NOT be claimed by the C++ analyzer — they belong
    to the C analyzer. This prevents phantom C++ symbols from header
    files in pure C repos (e.g., git, Linux kernel).
    """

    def test_skips_h_files_in_pure_c_repo(self, tmp_path: Path) -> None:
        """find_cpp_files should not include .h files when no .cpp/.cc/.cxx exist."""
        (tmp_path / "main.c").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper(void);")

        files = list(find_cpp_files(tmp_path))
        h_files = [f for f in files if f.suffix == ".h"]
        assert len(h_files) == 0, (
            f".h files should not be included in pure C repo, found: {h_files}"
        )

    def test_includes_h_files_when_cpp_exists(self, tmp_path: Path) -> None:
        """find_cpp_files should include .h files when .cpp files exist."""
        (tmp_path / "main.cpp").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper();")

        files = list(find_cpp_files(tmp_path))
        h_files = [f for f in files if f.suffix == ".h"]
        assert len(h_files) >= 1, (
            f".h files should be included when .cpp exists, found files: {files}"
        )

    def test_includes_h_files_when_cc_exists(self, tmp_path: Path) -> None:
        """find_cpp_files should include .h files when .cc files exist."""
        (tmp_path / "main.cc").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper();")

        files = list(find_cpp_files(tmp_path))
        h_files = [f for f in files if f.suffix == ".h"]
        assert len(h_files) >= 1

    def test_always_includes_hpp_files(self, tmp_path: Path) -> None:
        """.hpp files are always included (unambiguously C++)."""
        (tmp_path / "utils.hpp").write_text("void helper();")
        # No .cpp files

        files = list(find_cpp_files(tmp_path))
        hpp_files = [f for f in files if f.suffix == ".hpp"]
        assert len(hpp_files) >= 1

    def test_no_phantom_symbols_in_pure_c_repo(self, tmp_path: Path) -> None:
        """C++ analyzer should produce no symbols from .h files in a pure C repo."""
        (tmp_path / "main.c").write_text("""
int main() { return helper(); }
""")
        (tmp_path / "helper.h").write_text("""
int helper(void);
""")

        result = analyze_cpp(tmp_path)
        # Should have no symbols since only .h and .c files exist
        assert len(result.symbols) == 0, (
            f"C++ analyzer should not produce symbols from .h files in pure C repo, "
            f"found: {[s.name for s in result.symbols]}"
        )


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
        import hypergumbo_lang_mainstream.cpp as cpp_module

        with patch.object(
            cpp_module._analyzer, "_check_grammar_available",
            return_value=False,
        ):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = analyze_cpp(Path("/nonexistent"))
                assert result.skipped
                assert "not available" in result.skip_reason
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

    def test_prefers_definition_over_declaration_for_call_edges(
        self, tmp_path: Path
    ) -> None:
        """Call edges point to definitions (.cpp), not declarations (.h).

        This ensures transitive coverage estimation works correctly.
        When caller() calls process(), the edge should point to the
        definition in impl.cpp (which has outgoing calls), not the
        declaration in header.h (which has none).
        """
        # Header with declaration (no function body)
        header = tmp_path / "header.h"
        header.write_text("""
void process();
void helper();
""")

        # Source with definitions (has function body with calls)
        impl = tmp_path / "impl.cpp"
        impl.write_text("""
#include "header.h"

void helper() {
    // No calls
}

void process() {
    helper();  // Calls helper
}
""")

        # Test file that calls process
        test_file = tmp_path / "test.cpp"
        test_file.write_text("""
#include "header.h"

void test_process() {
    process();  // Should resolve to impl.cpp definition
}
""")

        result = analyze_cpp(tmp_path)

        # Find the edge from test_process -> process
        test_to_process_edge = None
        for e in result.edges:
            if "test_process" in e.src and "process" in e.dst:
                test_to_process_edge = e
                break

        assert test_to_process_edge is not None
        assert "impl.cpp" in test_to_process_edge.dst
        assert "header.h" not in test_to_process_edge.dst


class TestCppSignatureExtraction:
    """Tests for C++ function signature extraction."""

    def test_basic_function_signature(self, tmp_path: Path) -> None:
        """Basic function with parameters extracts signature."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "math.cpp"
        cpp_file.write_text("int add(int x, int y) { return x + y; }")

        result = analyze_cpp(tmp_path)

        add_sym = next((s for s in result.symbols if s.name == "add"), None)
        assert add_sym is not None
        assert add_sym.signature == "(int x, int y) int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Void return type function extracts signature without return type."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "util.cpp"
        cpp_file.write_text("void process(int count) { /* work */ }")

        result = analyze_cpp(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        assert process_sym.signature == "(int count)"

    def test_reference_parameter_signature(self, tmp_path: Path) -> None:
        """Reference parameters appear in signature."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "str.cpp"
        cpp_file.write_text("int size(const std::string& str) { return 0; }")

        result = analyze_cpp(tmp_path)

        size_sym = next((s for s in result.symbols if s.name == "size"), None)
        assert size_sym is not None
        assert "const std::string& str" in size_sym.signature
        assert size_sym.signature.endswith("int")

    def test_class_method_signature(self, tmp_path: Path) -> None:
        """Class method has signature extracted."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "class.cpp"
        cpp_file.write_text("void MyClass::process(int value) { /* impl */ }")

        result = analyze_cpp(tmp_path)

        method_sym = next((s for s in result.symbols if "process" in s.name), None)
        assert method_sym is not None
        assert method_sym.signature == "(int value)"

    def test_empty_params_signature(self, tmp_path: Path) -> None:
        """Function with no parameters has empty parens."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        result = analyze_cpp(tmp_path)

        main_sym = next((s for s in result.symbols if s.name == "main"), None)
        assert main_sym is not None
        assert main_sym.signature == "() int"

    def test_qualified_return_type(self, tmp_path: Path) -> None:
        """Qualified return type (std::string) appears in signature."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "str.cpp"
        cpp_file.write_text("std::string getName() { return \"\"; }")

        result = analyze_cpp(tmp_path)

        get_sym = next((s for s in result.symbols if s.name == "getName"), None)
        assert get_sym is not None
        assert "std::string" in get_sym.signature


class TestCppNamespaceAliases:
    """Tests for C++ namespace alias tracking (ADR-0007)."""

    def test_extracts_namespace_alias(self, tmp_path: Path) -> None:
        """Extracts namespace aliases from namespace_alias_definition."""
        from hypergumbo_lang_mainstream.cpp import _extract_namespace_aliases
        import tree_sitter
        import tree_sitter_cpp

        source = b"""
namespace fs = std::filesystem;
namespace io = std::iostream;
"""
        lang = tree_sitter.Language(tree_sitter_cpp.language())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(source)

        aliases = _extract_namespace_aliases(tree.root_node, source)

        assert "fs" in aliases
        assert aliases["fs"] == "std::filesystem"
        assert "io" in aliases
        assert aliases["io"] == "std::iostream"

    def test_namespace_alias_provides_path_hint(self, tmp_path: Path) -> None:
        """Namespace aliases are stored in FileAnalysis for call resolution."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "test.cpp").write_text("""
namespace MyNS = Some::Namespace;

void caller() {
    MyNS::helper();
}
""")

        result = analyze_cpp(tmp_path)

        # The alias should be extracted (checking via result)
        # Since we can't directly check FileAnalysis, we verify no crash
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "caller" for s in funcs)


class TestCppInheritanceExtraction:
    """Tests for C++ inheritance extraction (base_classes metadata).

    C++ uses single and multiple inheritance with access specifiers:
        class Dog : public Animal { };
        class Cat : Animal, public Printable { };
    The base_classes metadata enables the centralized inheritance linker.
    """

    def test_extracts_class_inheritance(self, tmp_path: Path) -> None:
        """Extracts base class from public inheritance."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "animal.cpp"
        cpp_file.write_text("""
class Animal {
public:
    virtual void speak() {}
};

class Dog : public Animal {
public:
    void speak() override {}
};
""")

        result = analyze_cpp(tmp_path)

        dog = next((s for s in result.symbols if s.name == "Dog"), None)
        assert dog is not None
        assert dog.meta is not None
        assert "base_classes" in dog.meta
        assert "Animal" in dog.meta["base_classes"]

    def test_extracts_private_inheritance(self, tmp_path: Path) -> None:
        """Extracts base class from private inheritance."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "impl.cpp"
        cpp_file.write_text("""
class Base {};
class Impl : private Base {};
""")

        result = analyze_cpp(tmp_path)

        impl = next((s for s in result.symbols if s.name == "Impl"), None)
        assert impl is not None
        assert impl.meta is not None
        assert "base_classes" in impl.meta
        assert "Base" in impl.meta["base_classes"]

    def test_extracts_multiple_inheritance(self, tmp_path: Path) -> None:
        """Extracts multiple base classes."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "multi.cpp"
        cpp_file.write_text("""
class Animal {};
class Printable {};
class Cat : public Animal, public Printable {};
""")

        result = analyze_cpp(tmp_path)

        cat = next((s for s in result.symbols if s.name == "Cat"), None)
        assert cat is not None
        assert cat.meta is not None
        assert "base_classes" in cat.meta
        assert "Animal" in cat.meta["base_classes"]
        assert "Printable" in cat.meta["base_classes"]

    def test_extracts_struct_inheritance(self, tmp_path: Path) -> None:
        """Extracts inheritance for struct (default public)."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "vec.cpp"
        cpp_file.write_text("""
struct BaseStruct { int x; };
struct Vector : BaseStruct { int y; };
""")

        result = analyze_cpp(tmp_path)

        vec = next((s for s in result.symbols if s.name == "Vector"), None)
        assert vec is not None
        assert vec.meta is not None
        assert "base_classes" in vec.meta
        assert "BaseStruct" in vec.meta["base_classes"]

    def test_extracts_qualified_base_class(self, tmp_path: Path) -> None:
        """Extracts qualified base class names (std::exception)."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "err.cpp"
        cpp_file.write_text("""
class MyError : public std::runtime_error {
    using std::runtime_error::runtime_error;
};
""")

        result = analyze_cpp(tmp_path)

        err = next((s for s in result.symbols if s.name == "MyError"), None)
        assert err is not None
        assert err.meta is not None
        assert "base_classes" in err.meta
        # Should extract the full qualified name
        assert "std::runtime_error" in err.meta["base_classes"]

    def test_no_base_classes_when_none(self, tmp_path: Path) -> None:
        """No base_classes when class has no inheritance."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "standalone.cpp"
        cpp_file.write_text("""
class Standalone {
    int value;
};
""")

        result = analyze_cpp(tmp_path)

        standalone = next((s for s in result.symbols if s.name == "Standalone"), None)
        assert standalone is not None
        # Either no meta or no base_classes key
        if standalone.meta:
            assert "base_classes" not in standalone.meta or standalone.meta["base_classes"] == []


class TestCppTemplateFunctionCalls:
    """Tests for C++ template function call detection.

    Template function calls like process<int>(42) produce a
    call_expression with a template_function child, which the
    current get_callee_name() doesn't handle.
    """

    def test_simple_template_call(self, tmp_path: Path) -> None:
        """process<int>(42) should create a call edge to process."""
        (tmp_path / "tmpl.cpp").write_text("""
template<typename T>
T process(T value) { return value; }

void caller() {
    int result = process<int>(42);
}
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert "process" in call_edges[0].dst
        assert call_edges[0].confidence >= 0.80

    def test_template_method_on_object(self, tmp_path: Path) -> None:
        """c.get<int>() should create a call edge to get.

        field_expression contains template_method which contains
        field_identifier. get_callee_name must look through template_method.
        """
        (tmp_path / "tmpl_method.cpp").write_text("""
class Container {
public:
    int get() { return 0; }
};

void caller() {
    Container c;
    int val = c.get<int>();
}
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        dst_names = [e.dst for e in call_edges]
        assert any("get" in d for d in dst_names)

    def test_qualified_template_call(self, tmp_path: Path) -> None:
        """NS::create<Widget>() should resolve via qualified_identifier."""
        (tmp_path / "ns_tmpl.cpp").write_text("""
namespace Factory {
    template<typename T>
    T create() { return T(); }
}

class Widget {};

void caller() {
    Widget w = Factory::create<Widget>();
}
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        assert any("create" in e.dst for e in call_edges)

    def test_pointer_return_template_call(self, tmp_path: Path) -> None:
        """Template function returning pointer (T*) should be extracted and resolved.

        Pointer return types wrap function_declarator inside pointer_declarator,
        which requires descending through the wrapper to find the name.
        """
        (tmp_path / "ptr.cpp").write_text("""
template<typename T>
T* make() { return new T(); }

class Obj {};

void caller() {
    Obj* o = make<Obj>();
}
""")

        result = analyze_cpp(tmp_path)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "make" in func_names, f"make not found in symbols: {func_names}"

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert any("make" in e.dst for e in call_edges)

    def test_reference_return_function(self, tmp_path: Path) -> None:
        """Functions returning references (T&) should be extracted as symbols."""
        (tmp_path / "ref.cpp").write_text("""
class Config {};
Config global_config;

Config& get_config() { return global_config; }

void caller() {
    Config& c = get_config();
}
""")

        result = analyze_cpp(tmp_path)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "get_config" in func_names

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert any("get_config" in e.dst for e in call_edges)

    def test_template_call_cross_file(self, tmp_path: Path) -> None:
        """Template calls should resolve across files."""
        (tmp_path / "util.cpp").write_text("""
template<typename T>
T clamp(T val, T lo, T hi) {
    if (val < lo) return lo;
    if (val > hi) return hi;
    return val;
}
""")
        (tmp_path / "main.cpp").write_text("""
template<typename T>
T clamp(T val, T lo, T hi);

void process() {
    int x = clamp<int>(50, 0, 100);
}
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        # Should resolve to util.cpp definition, not main.cpp declaration
        assert any("clamp" in e.dst for e in call_edges)


class TestCppStackConstruction:
    """Tests for stack object construction detection.

    In C++, stack-allocated objects like ``Widget w;`` or ``Widget w(42);``
    invoke constructors but don't produce ``call_expression`` or
    ``new_expression`` nodes. Only explicit ``new`` was previously detected.
    """

    def test_default_construction(self, tmp_path: Path) -> None:
        """``Widget w;`` should create an instantiates edge to Widget."""
        (tmp_path / "stack.cpp").write_text("""
class Widget {};

void caller() {
    Widget w;
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 1
        assert "Widget" in inst_edges[0].dst
        assert inst_edges[0].evidence_type == "stack_construction"

    def test_direct_construction_with_args(self, tmp_path: Path) -> None:
        """``Config c(1, 2);`` should create an instantiates edge."""
        (tmp_path / "direct.cpp").write_text("""
class Config {
public:
    Config(int a, int b) {}
};

void caller() {
    Config c(1, 2);
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 1
        assert "Config" in inst_edges[0].dst
        assert inst_edges[0].evidence_type == "stack_construction"

    def test_brace_initialization(self, tmp_path: Path) -> None:
        """``Widget w{};`` should create an instantiates edge."""
        (tmp_path / "brace.cpp").write_text("""
class Widget {};

void caller() {
    Widget w{};
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 1
        assert "Widget" in inst_edges[0].dst

    def test_compound_literal(self, tmp_path: Path) -> None:
        """``Widget{42}`` as an expression should create an instantiates edge."""
        (tmp_path / "compound.cpp").write_text("""
class Widget {};

void process(Widget w) {}

void caller() {
    process(Widget{42});
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert any("Widget" in e.dst for e in inst_edges)

    def test_no_false_positive_primitive(self, tmp_path: Path) -> None:
        """``int x;`` should NOT create an instantiates edge."""
        (tmp_path / "prim.cpp").write_text("""
void caller() {
    int x = 0;
    double y = 1.0;
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) == 0

    def test_cross_file_construction(self, tmp_path: Path) -> None:
        """Stack construction should resolve to class in another file."""
        (tmp_path / "service.cpp").write_text("""
class Service {
public:
    void run() {}
};
""")
        (tmp_path / "main.cpp").write_text("""
class Service;

void start() {
    Service svc;
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) >= 1
        assert any("Service" in e.dst for e in inst_edges)

    def test_qualified_type_construction(self, tmp_path: Path) -> None:
        """``ui::Button btn;`` should create an instantiates edge.

        Namespace-qualified types in declarations use ``qualified_identifier``
        instead of ``type_identifier``. The handler must extract the
        inner type_identifier from the qualified node.
        """
        (tmp_path / "widgets.cpp").write_text("""
namespace ui {
class Button {
public:
    void click() {}
};
}
""")
        (tmp_path / "main.cpp").write_text("""
namespace ui { class Button; }

void test() {
    ui::Button btn;
}
""")

        result = analyze_cpp(tmp_path)

        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(inst_edges) >= 1
        assert any("Button" in e.dst for e in inst_edges)


class TestCppStructEnumDefinitionOnly:
    """Tests that only struct/enum definitions (with bodies) produce symbols.

    Same fix as C: references like ``struct stat sb;`` and forward
    declarations ``struct Foo;`` should not create struct symbols.
    """

    def test_struct_definition_creates_symbol(self, tmp_path: Path) -> None:
        """struct Point { ... } → creates struct symbol."""
        (tmp_path / "types.cpp").write_text("""
struct Point {
    int x;
    int y;
};
""")
        result = analyze_cpp(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 1
        assert structs[0].name == "Point"

    def test_struct_reference_does_not_create_symbol(self, tmp_path: Path) -> None:
        """struct stat sb; → does NOT create struct symbol."""
        (tmp_path / "main.cpp").write_text("""
void process() {
    struct stat sb;
}
""")
        result = analyze_cpp(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 0, (
            f"Struct references should not create symbols, "
            f"found: {[s.name for s in structs]}"
        )

    def test_enum_definition_creates_symbol(self, tmp_path: Path) -> None:
        """enum Color { RED, GREEN } → creates enum symbol."""
        (tmp_path / "types.cpp").write_text("""
enum Color { RED, GREEN, BLUE };
""")
        result = analyze_cpp(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 1
        assert enums[0].name == "Color"

    def test_enum_reference_does_not_create_symbol(self, tmp_path: Path) -> None:
        """enum Color c; → does NOT create enum symbol."""
        (tmp_path / "main.cpp").write_text("""
void process() {
    enum Color c;
}
""")
        result = analyze_cpp(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 0, (
            f"Enum references should not create symbols, "
            f"found: {[s.name for s in enums]}"
        )


class TestCppDispatchTableEdges:
    """Tests for function pointer dispatch table detection in C++.

    C++ codebases use the same dispatch table patterns as C:
    static struct Foo table[] = { { "name", func_ptr }, ... };
    These function pointer references create dispatches_to edges.
    """

    def test_dispatch_table_creates_edges(self, tmp_path: Path) -> None:
        """Function pointers in static array initializers create edges."""
        (tmp_path / "dispatch.cpp").write_text("""
int cmd_add(int argc, char **argv) { return 0; }
int cmd_commit(int argc, char **argv) { return 0; }
int cmd_status(int argc, char **argv) { return 0; }

struct cmd_struct {
    const char *name;
    int (*fn)(int, char **);
};

static struct cmd_struct commands[] = {
    { "add", cmd_add },
    { "commit", cmd_commit },
    { "status", cmd_status },
};

int run_builtin(struct cmd_struct *p) {
    return p->fn(0, 0);
}
""")
        result = analyze_cpp(tmp_path)

        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "cmd_add" in func_names
        assert "cmd_commit" in func_names
        assert "cmd_status" in func_names

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        ref_dsts = {e.dst for e in ref_edges}
        cmd_add_id = next(s.id for s in result.symbols if s.name == "cmd_add")
        cmd_commit_id = next(s.id for s in result.symbols if s.name == "cmd_commit")
        cmd_status_id = next(s.id for s in result.symbols if s.name == "cmd_status")

        assert cmd_add_id in ref_dsts
        assert cmd_commit_id in ref_dsts
        assert cmd_status_id in ref_dsts

        # Verify evidence type and confidence
        for e in ref_edges:
            assert e.evidence_type == "dispatch_table_initializer"
            assert 0.6 <= e.confidence <= 0.85

    def test_dispatch_table_cross_file(self, tmp_path: Path) -> None:
        """Dispatch table references functions defined in other files."""
        (tmp_path / "cmds.cpp").write_text("""
int cmd_help(int argc, char **argv) { return 0; }
""")
        (tmp_path / "main.cpp").write_text("""
int cmd_help(int, char **);

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "help", cmd_help },
};
""")
        result = analyze_cpp(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(ref_edges) >= 1
        cmd_help_sym = next(
            (s for s in result.symbols if s.name == "cmd_help" and s.kind == "function"),
            None,
        )
        assert cmd_help_sym is not None
        assert any(e.dst == cmd_help_sym.id for e in ref_edges)

    def test_non_function_identifiers_not_linked(self, tmp_path: Path) -> None:
        """Constants and macros in initializers don't create false edges."""
        (tmp_path / "dispatch.cpp").write_text("""
int cmd_run(int argc, char **argv) { return 0; }

#define FLAG_SETUP 1

struct entry {
    const char *name;
    int (*fn)(int, char **);
    int flags;
};

static struct entry table[] = {
    { "run", cmd_run, FLAG_SETUP },
};
""")
        result = analyze_cpp(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        ref_dsts = {e.dst for e in ref_edges}
        cmd_run_id = next(s.id for s in result.symbols if s.name == "cmd_run")
        assert cmd_run_id in ref_dsts
        # FLAG_SETUP should not create an edge (it's a macro, not a function)
        assert len(ref_edges) == 1

    def test_duplicate_function_refs_deduplicated(self, tmp_path: Path) -> None:
        """Same function appearing multiple times in table gets one edge."""
        (tmp_path / "dispatch.cpp").write_text("""
int handler(int argc, char **argv) { return 0; }

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "first", handler },
    { "second", handler },
    { "third", handler },
};
""")
        result = analyze_cpp(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(ref_edges) == 1

    def test_non_function_symbol_not_linked(self, tmp_path: Path) -> None:
        """Struct names in initializers don't create dispatch edges.

        When a known symbol (e.g. a struct) appears in an initializer list,
        it must not produce a dispatches_to edge since it isn't a function.
        """
        (tmp_path / "dispatch.cpp").write_text("""
int real_func(int argc, char **argv) { return 0; }

struct Config {
    int value;
};

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "real", real_func },
};

/* Config is a known symbol but not a function — should not be linked.
   We put Config's name in a second table to test the non-function filter. */
struct meta_entry { const char *name; const char *tag; };

static struct meta_entry meta[] = {
    { "cfg", "Config" },
};
""")
        result = analyze_cpp(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        # Only real_func should be linked (Config is a struct, not a function)
        assert len(ref_edges) == 1
        real_func_id = next(s.id for s in result.symbols if s.name == "real_func")
        assert ref_edges[0].dst == real_func_id


class TestCppDispatchTableVariableReferences:
    """Tests for uses_dispatch_table edges in C++.

    When a function references a dispatch table variable in its body,
    a uses_dispatch_table edge connects the function to the table.
    """

    def test_function_referencing_dispatch_table(self, tmp_path: Path) -> None:
        """A function that references a dispatch table variable gets an edge."""
        (tmp_path / "dispatch.cpp").write_text("""
int cmd_add(int argc, char **argv) { return 0; }
int cmd_rm(int argc, char **argv) { return 0; }

struct cmd_struct {
    const char *name;
    int (*fn)(int, char **);
};

static struct cmd_struct commands[] = {
    { "add", cmd_add },
    { "rm", cmd_rm },
};

int run_command(const char *name) {
    for (int i = 0; commands[i].name; i++) {
        if (strcmp(commands[i].name, name) == 0)
            return commands[i].fn(0, 0);
    }
    return -1;
}
""")
        result = analyze_cpp(tmp_path)

        table_edges = [e for e in result.edges if e.edge_type == "uses_dispatch_table"]
        assert len(table_edges) >= 1
        run_cmd_sym = next(
            (s for s in result.symbols if s.name == "run_command"),
            None,
        )
        assert run_cmd_sym is not None
        assert any(e.src == run_cmd_sym.id for e in table_edges)

        for e in table_edges:
            assert e.evidence_type == "dispatch_table_reference"
            assert e.confidence == 0.85

    def test_multiple_functions_reference_same_table(self, tmp_path: Path) -> None:
        """Multiple functions referencing the same table each get an edge."""
        (tmp_path / "dispatch.cpp").write_text("""
int handler_a(int argc, char **argv) { return 0; }

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "a", handler_a },
};

int lookup(const char *n) {
    return table[0].fn(0, 0);
}

void print_table() {
    for (int i = 0; table[i].name; i++) {}
}
""")
        result = analyze_cpp(tmp_path)

        table_edges = [e for e in result.edges if e.edge_type == "uses_dispatch_table"]
        srcs = {e.src for e in table_edges}
        lookup_sym = next(s for s in result.symbols if s.name == "lookup")
        print_sym = next(s for s in result.symbols if s.name == "print_table")
        assert lookup_sym.id in srcs
        assert print_sym.id in srcs

    def test_no_false_refs_from_unrelated_functions(self, tmp_path: Path) -> None:
        """Functions that don't reference the table get no edge."""
        (tmp_path / "dispatch.cpp").write_text("""
int cmd_x(int argc, char **argv) { return 0; }

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "x", cmd_x },
};

int unrelated() {
    return 42;
}
""")
        result = analyze_cpp(tmp_path)

        table_edges = [e for e in result.edges if e.edge_type == "uses_dispatch_table"]
        unrelated_sym = next(s for s in result.symbols if s.name == "unrelated")
        assert not any(e.src == unrelated_sym.id for e in table_edges)


class TestCppDocstrings:
    """Tests for Doxygen comment extraction via populate_docstrings_from_tree."""

    def test_doxygen_block_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts Doxygen block comment preceding a function."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "/** Computes the factorial. */\n"
            "int factorial(int n) {\n"
            "    return n <= 1 ? 1 : n * factorial(n - 1);\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        func = next((s for s in result.symbols if s.name == "factorial"), None)
        assert func is not None
        assert func.docstring == "Computes the factorial."

    def test_doxygen_line_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts Doxygen /// line comment preceding a function."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "/// Adds two integers.\n"
            "int add(int a, int b) { return a + b; }\n"
        )
        result = analyze_cpp(tmp_path)
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.docstring == "Adds two integers."

    def test_doxygen_on_class(self, tmp_path: Path) -> None:
        """Extracts Doxygen comment preceding a class."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "/** Represents a widget. */\n"
            "class Widget {\n"
            "public:\n"
            "    void draw();\n"
            "};\n"
        )
        result = analyze_cpp(tmp_path)
        cls = next((s for s in result.symbols if s.name == "Widget"), None)
        assert cls is not None
        assert cls.docstring == "Represents a widget."

    def test_no_comment_no_docstring(self, tmp_path: Path) -> None:
        """Function without preceding comment has no docstring."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "int main() { return 0; }\n"
        )
        result = analyze_cpp(tmp_path)
        func = next((s for s in result.symbols if s.name == "main"), None)
        assert func is not None
        assert func.docstring is None


class TestCppFunctionPointerReferences:
    """Tests for &function pointer references in C++ (INV-dinur)."""

    def test_address_of_function(self, tmp_path: Path) -> None:
        """&process should create a references edge."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "void process(int x) {}\n"
            "\n"
            "void run() {\n"
            "    auto fp = &process;\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "process" in e.dst
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].evidence_type == "function_pointer"

    def test_qualified_function_pointer(self, tmp_path: Path) -> None:
        """&Class::method should create a references edge."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "class App {\n"
            "public:\n"
            "    static void handler(int x) {}\n"
            "};\n"
            "\n"
            "void run() {\n"
            "    auto fp = &App::handler;\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "handler" in e.dst
        ]
        assert len(ref_edges) == 1

    def test_cross_file_function_pointer(self, tmp_path: Path) -> None:
        """&func referencing a function in another file."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "utils.cpp").write_text(
            "void helper() {}\n"
        )
        (tmp_path / "main.cpp").write_text(
            "void helper();\n"
            "void run() {\n"
            "    auto fp = &helper;\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "helper" in e.dst
        ]
        assert len(ref_edges) >= 1

    def test_no_reference_for_unknown_function(self, tmp_path: Path) -> None:
        """&unknown should not create a references edge."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "void run() {\n"
            "    auto fp = &unknown_func;\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src
        ]
        assert len(ref_edges) == 0


class TestCallbackArgDetection:
    """Function pointer arguments in C++ calls (std::thread, etc.)."""

    def test_pthread_create_callback(self, tmp_path: Path) -> None:
        """pthread_create(tid, attr, worker, arg) → edge to worker."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "void* worker(void* arg) { return nullptr; }\n"
            "\n"
            "void run() {\n"
            "    pthread_t tid;\n"
            "    pthread_create(&tid, nullptr, worker, nullptr);\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        cb_edges = [
            e for e in result.edges
            if "run" in e.src and "worker" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1

    def test_no_false_positive_for_variables(self, tmp_path: Path) -> None:
        """Variable args should NOT create callback edges."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "main.cpp").write_text(
            "void process(int x) {}\n"
            "\n"
            "void run() {\n"
            "    int count = 5;\n"
            "    process(count);\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)
        cb_edges = [
            e for e in result.edges
            if e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 0


@pytest.mark.skipif(
    not is_cpp_tree_sitter_available(),
    reason="tree-sitter-cpp not available",
)
class TestCppFieldTypeResolution:
    """Tests for this->field->method() resolution via class_field_types."""

    def test_extract_field_types_basic(self, tmp_path: Path) -> None:
        """Extract field types from a class body."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Controller {
    Repo* _repo;
    Cache _cache;
    int _count;
};
"""
        tree = parser.parse(code)
        class_node = tree.root_node.children[0]
        fields = _extract_field_types_cpp(class_node, code)
        assert fields == {"_repo": "Repo", "_cache": "Cache"}
        assert "_count" not in fields  # primitives skipped

    def test_extract_field_types_template(self, tmp_path: Path) -> None:
        """Template types extract the inner type argument."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Ctrl {
    std::unique_ptr<Db> _db;
    std::vector<Item> _items;
};
"""
        tree = parser.parse(code)
        class_node = tree.root_node.children[0]
        fields = _extract_field_types_cpp(class_node, code)
        assert fields["_db"] == "Db"
        assert fields["_items"] == "Item"

    def test_this_field_method_call(self, tmp_path: Path) -> None:
        """this->_repo->save() resolves through field type registry."""
        (tmp_path / "main.cpp").write_text(
            "class Repo {\n"
            "public:\n"
            "    void save() {}\n"
            "};\n"
            "\n"
            "class Service {\n"
            "public:\n"
            "    void save() {}\n"
            "};\n"
            "\n"
            "class Controller {\n"
            "    Repo* _repo;\n"
            "public:\n"
            "    void run() {\n"
            "        this->_repo->save();\n"
            "    }\n"
            "};\n"
        )
        result = analyze_cpp(tmp_path)

        # Should resolve this->_repo->save() to Repo::save, not Service::save
        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "method_call_field_chain"
        ]
        assert len(chain_edges) == 1
        assert "save" in chain_edges[0].dst

    def test_chained_field_resolution(self, tmp_path: Path) -> None:
        """this->_inner->_db->query() resolves through multiple field hops."""
        (tmp_path / "main.cpp").write_text(
            "class Db {\n"
            "public:\n"
            "    void query() {}\n"
            "};\n"
            "\n"
            "class Inner {\n"
            "    Db* _db;\n"
            "};\n"
            "\n"
            "class Outer {\n"
            "    Inner* _inner;\n"
            "public:\n"
            "    void run() {\n"
            "        this->_inner->_db->query();\n"
            "    }\n"
            "};\n"
        )
        result = analyze_cpp(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "method_call_field_chain"
        ]
        assert len(chain_edges) == 1
        assert "query" in chain_edges[0].dst

    def test_non_this_receiver_falls_through(self, tmp_path: Path) -> None:
        """Non-this receivers use normal resolution, not field chain."""
        (tmp_path / "main.cpp").write_text(
            "class Svc {\n"
            "public:\n"
            "    void run() {}\n"
            "};\n"
            "\n"
            "void doStuff(Svc* s) {\n"
            "    s->run();\n"
            "}\n"
        )
        result = analyze_cpp(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "method_call_field_chain"
        ]
        assert len(chain_edges) == 0

    def test_class_field_types_populated(self, tmp_path: Path) -> None:
        """CppAnalyzer populates class_field_types in Pass 1."""
        (tmp_path / "main.cpp").write_text(
            "class Repo {\n"
            "public:\n"
            "    void save() {}\n"
            "};\n"
            "\n"
            "class Controller {\n"
            "    Repo* _repo;\n"
            "    int _count;\n"
            "};\n"
        )
        result = analyze_cpp(tmp_path)

        # Verify symbols were extracted
        ctrl = next((s for s in result.symbols if s.name == "Controller"), None)
        assert ctrl is not None

    def test_self_keywords_is_this(self) -> None:
        """CppAnalyzer uses 'this' as self keyword."""
        from hypergumbo_lang_mainstream.cpp import CppAnalyzer
        assert "this" in CppAnalyzer.self_keywords

    def test_struct_field_types(self, tmp_path: Path) -> None:
        """Field types are extracted from structs too."""
        (tmp_path / "main.cpp").write_text(
            "struct Db {\n"
            "    void query() {}\n"
            "};\n"
            "\n"
            "struct App {\n"
            "    Db* _db;\n"
            "    void run() {\n"
            "        this->_db->query();\n"
            "    }\n"
            "};\n"
        )
        result = analyze_cpp(tmp_path)

        chain_edges = [
            e for e in result.edges
            if e.evidence_type == "method_call_field_chain"
        ]
        assert len(chain_edges) == 1
        assert "query" in chain_edges[0].dst

    def test_extract_field_types_qualified_no_template(self, tmp_path: Path) -> None:
        """Qualified type without template args uses last type_identifier."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Ctrl {
    MyNs::MyClass _obj;
};
"""
        tree = parser.parse(code)
        class_node = tree.root_node.children[0]
        fields = _extract_field_types_cpp(class_node, code)
        # MyNs::MyClass has a type_identifier "MyClass" as direct child
        # of the qualified_identifier — exercises lines 180-183.
        assert "_obj" in fields
        assert fields["_obj"] == "MyClass"

    def test_resolve_chain_no_enclosing_class(self, tmp_path: Path) -> None:
        """_resolve_cpp_field_chain returns None when no enclosing class."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
void freeFunc() {
    this->_x->foo();
}
"""
        tree = parser.parse(code)
        # Find the field_expression inside the call
        func_def = tree.root_node.children[0]
        # Navigate: function_definition > compound_statement > expression_statement
        #           > call_expression > field_expression > field_expression (receiver)
        body = func_def.child_by_field_name("body")
        assert body is not None
        expr_stmt = body.children[1]  # skip '{'
        call = expr_stmt.children[0]
        top_field = call.child_by_field_name("function")
        assert top_field is not None and top_field.type == "field_expression"
        receiver = top_field.child_by_field_name("argument")
        assert receiver is not None

        result = _resolve_cpp_field_chain(
            receiver, code, {"SomeClass": {"_x": "Foo"}}, call,
        )
        assert result is None  # no enclosing class

    def test_resolve_chain_root_not_this(self, tmp_path: Path) -> None:
        """_resolve_cpp_field_chain returns None when root is not 'this'."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Ctrl {
    void run() {
        obj->_x->foo();
    }
};
"""
        tree = parser.parse(code)
        # Navigate to the receiver field_expression
        class_node = tree.root_node.children[0]
        body_node = None
        for child in class_node.children:
            if child.type == "field_declaration_list":
                body_node = child
                break
        assert body_node is not None
        func_def = None
        for child in body_node.children:
            if child.type == "function_definition":
                func_def = child
                break
        assert func_def is not None
        compound = func_def.child_by_field_name("body")
        assert compound is not None
        expr_stmt = compound.children[1]
        call = expr_stmt.children[0]
        top_field = call.child_by_field_name("function")
        assert top_field is not None
        receiver = top_field.child_by_field_name("argument")
        assert receiver is not None

        result = _resolve_cpp_field_chain(
            receiver, code, {"Ctrl": {"_x": "Foo"}}, call,
        )
        assert result is None  # root is "obj", not "this"

    def test_resolve_chain_unknown_class_in_registry(
        self, tmp_path: Path,
    ) -> None:
        """_resolve_cpp_field_chain returns None for unknown class."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Ctrl {
    void run() {
        this->_x->foo();
    }
};
"""
        tree = parser.parse(code)
        class_node = tree.root_node.children[0]
        body_node = None
        for child in class_node.children:
            if child.type == "field_declaration_list":
                body_node = child
                break
        assert body_node is not None
        func_def = None
        for child in body_node.children:
            if child.type == "function_definition":
                func_def = child
                break
        assert func_def is not None
        compound = func_def.child_by_field_name("body")
        assert compound is not None
        expr_stmt = compound.children[1]
        call = expr_stmt.children[0]
        top_field = call.child_by_field_name("function")
        assert top_field is not None
        receiver = top_field.child_by_field_name("argument")
        assert receiver is not None

        # Empty registry — Ctrl not found
        result = _resolve_cpp_field_chain(receiver, code, {}, call)
        assert result is None

    def test_resolve_chain_unknown_field_in_registry(
        self, tmp_path: Path,
    ) -> None:
        """_resolve_cpp_field_chain returns None for unknown field."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"""
class Ctrl {
    void run() {
        this->_x->foo();
    }
};
"""
        tree = parser.parse(code)
        class_node = tree.root_node.children[0]
        body_node = None
        for child in class_node.children:
            if child.type == "field_declaration_list":
                body_node = child
                break
        assert body_node is not None
        func_def = None
        for child in body_node.children:
            if child.type == "function_definition":
                func_def = child
                break
        assert func_def is not None
        compound = func_def.child_by_field_name("body")
        assert compound is not None
        expr_stmt = compound.children[1]
        call = expr_stmt.children[0]
        top_field = call.child_by_field_name("function")
        assert top_field is not None
        receiver = top_field.child_by_field_name("argument")
        assert receiver is not None

        # Ctrl exists but _x field not registered
        result = _resolve_cpp_field_chain(
            receiver, code, {"Ctrl": {"_y": "Other"}}, call,
        )
        assert result is None

    def test_get_enclosing_class_returns_none_for_free_function(
        self,
    ) -> None:
        """_get_enclosing_class returns None for free functions."""
        import tree_sitter
        import tree_sitter_cpp as tscpp

        parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
        code = b"void freeFunc() { int x = 1; }"
        tree = parser.parse(code)
        # The function body statement node has no class ancestor
        func_def = tree.root_node.children[0]
        body = func_def.child_by_field_name("body")
        assert body is not None
        result = _get_enclosing_class(body, code)
        assert result is None


class TestCppStdlibMethodGuard:
    """Tests for C++ stdlib method blocklist.

    Common STL container methods (clear, push_back, resize, etc.) called
    via member syntax (obj.clear()) must not resolve to project functions
    with the same name when the receiver type is unknown.
    """

    def test_stdlib_method_not_resolved_to_project_function(self, tmp_path: Path) -> None:
        """v.clear() must not resolve to a project-level clear() function."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("""
#include <vector>

void clear() {}

void process() {
    std::vector<int> v;
    v.push_back(1);
    v.clear();
    v.resize(10);
}
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # process() should NOT have edges to clear(), push_back(), resize()
        for edge in call_edges:
            if "process" in edge.src:
                assert "clear" not in edge.dst or edge.evidence_type != "function_call", (
                    f"v.clear() should not resolve to project clear(). Edge: {edge}"
                )


class TestCppSameClassPreference:
    """Tests for same-class method preference in bare call resolution."""

    def test_bare_call_prefers_same_class(self, tmp_path: Path) -> None:
        """Initialize() inside Parser::parse prefers Parser::Initialize."""
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        (tmp_path / "parser.cpp").write_text("""
class Parser {
public:
    void Initialize() {}
    void parse() {
        Initialize();
    }
};

class Packager {
public:
    void Initialize() {}
};
""")

        result = analyze_cpp(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        init_edges = [e for e in call_edges if "parse" in e.src and "Initialize" in e.dst]

        assert len(init_edges) == 1, f"Expected 1 Initialize edge from parse, got {init_edges}"
        # Should resolve to Parser::Initialize, not Packager::Initialize
        assert "Parser" in init_edges[0].dst, (
            f"Expected Parser::Initialize, got {init_edges[0].dst}"
        )

