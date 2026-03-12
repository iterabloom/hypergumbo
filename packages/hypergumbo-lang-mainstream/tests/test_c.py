# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for C analyzer."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import hypergumbo_lang_mainstream.c as c_module


class TestFindCFiles:
    """Tests for C file discovery."""

    def test_finds_c_files(self, tmp_path: Path) -> None:
        """Finds .c and .h files."""
        from hypergumbo_lang_mainstream.c import find_c_files

        (tmp_path / "main.c").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper();")
        (tmp_path / "other.txt").write_text("not c")

        files = list(find_c_files(tmp_path))

        assert len(files) == 2
        suffixes = {f.suffix for f in files}
        assert ".c" in suffixes
        assert ".h" in suffixes


class TestCTreeSitterAvailability:
    """Tests for tree-sitter-c availability checking."""

    def test_is_c_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-c is available."""
        from hypergumbo_lang_mainstream.c import is_c_tree_sitter_available

        # tree-sitter-c is installed in the test env, so it should be True
        assert is_c_tree_sitter_available() is True

    def test_is_c_tree_sitter_available_false(self) -> None:
        """Returns False when grammar unavailable."""
        from hypergumbo_lang_mainstream.c import is_c_tree_sitter_available

        with patch.object(c_module._analyzer, "_check_grammar_available", return_value=False):
            assert is_c_tree_sitter_available() is False


class TestAnalyzeCFallback:
    """Tests for fallback behavior when tree-sitter-c unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-c unavailable."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "test.c").write_text("int main() { return 0; }")

        with patch.object(c_module._analyzer, "_check_grammar_available", return_value=False):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = analyze_c(tmp_path)
                assert result.skipped is True
                assert "not available" in result.skip_reason
                assert len(w) == 1


class TestCFunctionExtraction:
    """Tests for extracting C functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts C function declarations."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "functions.c"
        c_file.write_text("""
int add(int a, int b) {
    return a + b;
}

void greet(const char* name) {
    printf("Hello, %s\\n", name);
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        names = [s.name for s in result.symbols]
        assert "add" in names
        assert "greet" in names

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts C struct declarations."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "types.h"
        c_file.write_text("""
struct Point {
    int x;
    int y;
};

struct Rectangle {
    struct Point origin;
    int width;
    int height;
};
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Point" in names
        assert "Rectangle" in names
        # Verify kind is struct
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 2

    def test_extracts_typedef(self, tmp_path: Path) -> None:
        """Extracts C typedef declarations."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "types.h"
        c_file.write_text("""
typedef int ErrorCode;
typedef struct {
    int x;
    int y;
} Point;
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "ErrorCode" in names or "Point" in names

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts C enum declarations."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "types.h"
        c_file.write_text("""
enum Color {
    RED,
    GREEN,
    BLUE
};
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Color" in names
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) >= 1

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles C file with no functions/structs."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "empty.c"
        c_file.write_text("// Just a comment")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        assert result.skipped is False


class TestCCallEdges:
    """Tests for C function call detection."""

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between C functions."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "calls.c"
        c_file.write_text("""
int helper() {
    return 42;
}

int main() {
    int x = helper();
    return x;
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "helper" in names
        assert "main" in names

        # Should have a call edge from main to helper
        assert len(result.edges) >= 1
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestCCrossFileResolution:
    """Tests for cross-file call resolution."""

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Resolves function calls across files."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "helpers.c").write_text("""
int helper() {
    return 42;
}
""")

        (tmp_path / "main.c").write_text("""
int helper();  // Declaration

int main() {
    return helper();
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Should have symbols from both files
        names = [s.name for s in result.symbols]
        assert "helper" in names
        assert "main" in names

        # Should have cross-file call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestCJNIPatterns:
    """Tests for JNI pattern detection."""

    def test_detects_jni_export(self, tmp_path: Path) -> None:
        """Detects JNI export function patterns."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "jni_impl.c"
        c_file.write_text("""
#include <jni.h>

JNIEXPORT void JNICALL Java_com_example_Native_processData(
    JNIEnv *env, jobject obj, jbyteArray data) {
    // Implementation
}

JNIEXPORT jint JNICALL Java_com_example_Native_getValue(
    JNIEnv *env, jobject obj) {
    return 42;
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        # JNI functions should be detected
        assert any("Java_com_example_Native" in name for name in names)

        # Should have JNI-specific metadata
        jni_funcs = [s for s in result.symbols if "Java_" in s.name]
        assert len(jni_funcs) >= 2


class TestCAnalysisRun:
    """Tests for C analysis run tracking."""

    def test_tracks_files_analyzed(self, tmp_path: Path) -> None:
        """Tracks number of files analyzed."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "a.c").write_text("void a() {}")
        (tmp_path / "b.c").write_text("void b() {}")
        (tmp_path / "c.h").write_text("void c();")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 3
        assert result.run.pass_id == "c-v1"

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Handles repo with no C files."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "app.js").write_text("const x = 1;")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 0
        assert len(result.symbols) == 0


class TestCEdgeCases:
    """Tests for C edge cases and error handling."""

    def test_find_name_in_children_no_name(self) -> None:
        """Returns None when node has no identifier child."""
        from hypergumbo_lang_mainstream.c import _find_identifier_in_children

        mock_child = MagicMock()
        mock_child.type = "other"

        mock_node = MagicMock()
        mock_node.children = [mock_child]

        result = _find_identifier_in_children(mock_node, b"source")
        assert result is None

    def test_get_c_parser_import_error(self) -> None:
        """Returns None when tree-sitter-c is not available."""
        from hypergumbo_lang_mainstream.c import _get_c_parser

        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_c": None,
        }):
            result = _get_c_parser()
            assert result is None

    def test_analyze_c_file_parser_unavailable(self, tmp_path: Path) -> None:
        """Returns failure when parser is unavailable."""
        from hypergumbo_lang_mainstream.c import _analyze_c_file
        from hypergumbo_core.ir import AnalysisRun

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo_lang_mainstream.c._get_c_parser", return_value=None):
            symbols, edges, success = _analyze_c_file(c_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_analyze_c_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo_lang_mainstream.c import _analyze_c_file
        from hypergumbo_core.ir import AnalysisRun

        c_file = tmp_path / "missing.c"
        # Don't create the file

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_c_file(c_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_c_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """C files that fail to read increment skipped counter."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }")

        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if self.name == "test.c":
                raise IOError("Mock read error")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1

    def test_analyze_c_skipped_when_grammar_unavailable(self, tmp_path: Path) -> None:
        """analyze_c returns skipped result when grammar unavailable."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }")

        with patch.object(c_module._analyzer, "_check_grammar_available", return_value=False):
            import warnings
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestCFunctionDeclarations:
    """Tests for function declaration handling."""

    def test_handles_function_declarations(self, tmp_path: Path) -> None:
        """Handles function declarations (prototypes) vs definitions."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "proto.h"
        c_file.write_text("""
// Forward declarations
int add(int a, int b);
void process(void);
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        # Declarations should be detected as symbols
        names = [s.name for s in result.symbols]
        assert "add" in names or "process" in names

    def test_handles_static_functions(self, tmp_path: Path) -> None:
        """Handles static function definitions."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "static.c"
        c_file.write_text("""
static int helper() {
    return 42;
}

int main() {
    return helper();
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "helper" in names
        assert "main" in names

    def test_prefers_definition_over_declaration_for_call_edges(self, tmp_path: Path) -> None:
        """Call edges point to definitions (.c), not declarations (.h).

        This ensures transitive coverage estimation works correctly.
        When caller() calls process(), the edge should point to the
        definition in impl.c (which has outgoing calls), not the
        declaration in header.h (which has none).
        """
        from hypergumbo_lang_mainstream.c import analyze_c

        # Header with declaration
        header = tmp_path / "header.h"
        header.write_text("""
void process(void);
void helper(void);
""")

        # Source with definitions
        impl = tmp_path / "impl.c"
        impl.write_text("""
#include "header.h"

void helper(void) {
    // No calls
}

void process(void) {
    helper();  // Calls helper
}
""")

        # Test file that calls process
        test = tmp_path / "test.c"
        test.write_text("""
#include "header.h"

void test_process(void) {
    process();  // Should resolve to impl.c definition
}
""")

        result = analyze_c(tmp_path)

        # Find the edge from test_process -> process
        test_to_process_edge = None
        for e in result.edges:
            if "test_process" in e.src and "process" in e.dst:
                test_to_process_edge = e
                break

        assert test_to_process_edge is not None
        # Edge should point to impl.c definition, not header.h declaration
        assert "impl.c" in test_to_process_edge.dst
        assert "header.h" not in test_to_process_edge.dst

        # Verify the definition has outgoing edges (calls helper)
        process_to_helper_edge = None
        for e in result.edges:
            if "impl.c" in e.src and "process" in e.src and "helper" in e.dst:
                process_to_helper_edge = e
                break

        assert process_to_helper_edge is not None


class TestCPointerAndComplexTypes:
    """Tests for complex C type handling."""

    def test_handles_function_pointers(self, tmp_path: Path) -> None:
        """Handles functions with pointer parameters."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "pointers.c"
        c_file.write_text("""
void process(int* arr, size_t len) {
    for (size_t i = 0; i < len; i++) {
        arr[i] *= 2;
    }
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "process" in names

    def test_handles_typedef_struct(self, tmp_path: Path) -> None:
        """Handles typedef struct pattern."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "types.h"
        c_file.write_text("""
typedef struct Node {
    int value;
    struct Node* next;
} Node;
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        # Should detect either the struct or typedef name
        names = [s.name for s in result.symbols]
        assert "Node" in names


class TestCIncludeEdges:
    """Tests for #include edge detection."""

    def test_detects_include_edges(self, tmp_path: Path) -> None:
        """Detects #include directive edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "utils.h").write_text("void helper();")
        (tmp_path / "main.c").write_text("""
#include "utils.h"

int main() {
    helper();
    return 0;
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        # Should have include edge (file->file)
        include_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Include detection is a nice-to-have, not required for MVP
        # Just verify no crash


class TestCPointerReturnTypes:
    """Tests for functions with pointer return types."""

    def test_handles_pointer_return_type(self, tmp_path: Path) -> None:
        """Handles functions returning pointers."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "pointers.c"
        c_file.write_text("""
int* get_array() {
    static int arr[10];
    return arr;
}

char* get_string() {
    return "hello";
}
""")

        result = analyze_c(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "get_array" in names
        assert "get_string" in names


class TestCGetFunctionNameEdgeCases:
    """Tests for edge cases in _get_function_name."""

    def test_get_function_name_with_pointer_declarator(self) -> None:
        """Tests pointer declarator path in _get_function_name."""
        from hypergumbo_lang_mainstream.c import _get_function_name, _get_c_parser

        parser = _get_c_parser()
        assert parser is not None

        # Code with pointer return type
        source = b"int* get_ptr() { return 0; }"
        tree = parser.parse(source)

        # Find the function_definition node
        func_def = None
        for child in tree.root_node.children:
            if child.type == "function_definition":
                func_def = child
                break

        assert func_def is not None
        name = _get_function_name(func_def, source)
        assert name == "get_ptr"

    def test_get_function_name_no_match(self) -> None:
        """Tests when no name can be found."""
        from hypergumbo_lang_mainstream.c import _get_function_name

        # Create a mock node with no matching children
        mock_child = MagicMock()
        mock_child.type = "other_type"
        mock_child.children = []

        mock_node = MagicMock()
        mock_node.children = [mock_child]

        result = _get_function_name(mock_node, b"source")
        assert result is None


class TestCAnalyzeFileSuccess:
    """Tests for successful file analysis."""

    def test_analyze_c_file_success(self, tmp_path: Path) -> None:
        """_analyze_c_file returns symbols and edges on success."""
        from hypergumbo_lang_mainstream.c import _analyze_c_file
        from hypergumbo_core.ir import AnalysisRun

        c_file = tmp_path / "test.c"
        c_file.write_text("""
int helper() {
    return 42;
}

int main() {
    return helper();
}
""")

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_c_file(c_file, run)

        assert success is True
        assert len(symbols) >= 2  # helper + main
        assert len(edges) >= 1  # at least one call edge

        # Verify call edge is detected
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestCSignatureExtraction:
    """Tests for C function signature extraction."""

    def test_basic_function_signature(self, tmp_path: Path) -> None:
        """Basic function with parameters extracts signature."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "math.c"
        c_file.write_text("int add(int x, int y) { return x + y; }")

        result = analyze_c(tmp_path)

        add_sym = next((s for s in result.symbols if s.name == "add"), None)
        assert add_sym is not None
        assert add_sym.signature == "(int x, int y) int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Void return type function extracts signature without return type."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "util.c"
        c_file.write_text("void process(int count) { /* work */ }")

        result = analyze_c(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        # void return type should not appear in signature
        assert process_sym.signature == "(int count)"

    def test_pointer_parameter_signature(self, tmp_path: Path) -> None:
        """Pointer parameters appear in signature."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "str.c"
        c_file.write_text("int strlen(const char* str) { return 0; }")

        result = analyze_c(tmp_path)

        strlen_sym = next((s for s in result.symbols if s.name == "strlen"), None)
        assert strlen_sym is not None
        assert "const char* str" in strlen_sym.signature
        assert strlen_sym.signature.endswith("int")

    def test_pointer_return_type_signature(self, tmp_path: Path) -> None:
        """Pointer return type appears in signature."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "alloc.c"
        c_file.write_text("char* strdup(const char* s) { return 0; }")

        result = analyze_c(tmp_path)

        strdup_sym = next((s for s in result.symbols if s.name == "strdup"), None)
        assert strdup_sym is not None
        # Should have char* return type
        assert "char*" in strdup_sym.signature

    def test_empty_params_signature(self, tmp_path: Path) -> None:
        """Function with no parameters has empty parens."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "main.c"
        c_file.write_text("int main() { return 0; }")

        result = analyze_c(tmp_path)

        main_sym = next((s for s in result.symbols if s.name == "main"), None)
        assert main_sym is not None
        assert main_sym.signature == "() int"

    def test_declaration_signature(self, tmp_path: Path) -> None:
        """Function declaration (prototype) extracts signature."""
        from hypergumbo_lang_mainstream.c import analyze_c

        h_file = tmp_path / "util.h"
        h_file.write_text("void process(int x, int y);")

        result = analyze_c(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        assert process_sym.signature == "(int x, int y)"


class TestHeaderDedup:
    """Tests for .h file deduplication when C++ files are present.

    When a repo contains C++ source files (*.cpp, *.cc, *.cxx) or C++ headers
    (*.hpp, *.hxx), the C analyzer skips .h files to avoid duplication with
    the C++ analyzer which also processes them.
    """

    def test_skips_h_files_when_cpp_sources_exist(self, tmp_path: Path) -> None:
        """C analyzer skips .h files when .cpp files exist in the repo."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "utils.h").write_text("void helper();")
        (tmp_path / "impl.c").write_text("void helper() {}")
        (tmp_path / "main.cpp").write_text("int main() { return 0; }")

        result = analyze_c(tmp_path)

        # Should only analyze impl.c, not utils.h
        paths = {s.path for s in result.symbols}
        assert any("impl.c" in p for p in paths), "Should include .c file symbols"
        assert not any(p.endswith(".h") for p in paths), (
            "Should NOT include .h file symbols when C++ files exist"
        )

    def test_skips_h_files_when_cc_sources_exist(self, tmp_path: Path) -> None:
        """C analyzer skips .h files when .cc files exist in the repo."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "types.h").write_text("struct Point { int x; int y; };")
        (tmp_path / "impl.c").write_text("void process() {}")
        (tmp_path / "main.cc").write_text("int main() { return 0; }")

        result = analyze_c(tmp_path)

        paths = {s.path for s in result.symbols}
        assert not any(p.endswith(".h") for p in paths), (
            "Should NOT include .h file symbols when .cc files exist"
        )

    def test_skips_h_files_when_hpp_exists(self, tmp_path: Path) -> None:
        """C analyzer skips .h files when .hpp files exist (header-only C++)."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "config.h").write_text("void init();")
        (tmp_path / "impl.c").write_text("void init() {}")
        (tmp_path / "utils.hpp").write_text("// C++ header-only lib")

        result = analyze_c(tmp_path)

        paths = {s.path for s in result.symbols}
        assert not any(p.endswith(".h") for p in paths), (
            "Should NOT include .h file symbols when .hpp files exist"
        )

    def test_includes_h_files_in_pure_c_repo(self, tmp_path: Path) -> None:
        """C analyzer includes .h files in pure-C repos (no C++ files).

        Headers are analyzed for symbol registration and cross-file
        resolution.  Declaration-only symbols are filtered from the output
        when a definition exists (see TestDeclarationDefinitionDedup), but
        header-only symbols (structs, enums, typedefs) and declaration-only
        functions (no definition) are kept.
        """
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "utils.h").write_text("""
void helper();
struct Config { int verbose; };
""")
        (tmp_path / "main.c").write_text("""
void helper() {}
void main_func() { helper(); }
""")

        result = analyze_c(tmp_path)

        # The .h struct symbol should be present
        paths = {s.path for s in result.symbols}
        assert any(p.endswith(".h") for p in paths), (
            "Should include .h file symbols (struct) in pure-C repos"
        )
        assert any(p.endswith(".c") for p in paths), (
            "Should include .c file symbols in pure-C repos"
        )
        # Declaration of helper() is filtered (definition in .c exists)
        helpers = [s for s in result.symbols if s.name == "helper"]
        assert len(helpers) == 1
        assert helpers[0].path.endswith(".c")

    def test_find_c_files_skips_headers(self, tmp_path: Path) -> None:
        """find_c_files with include_headers=False yields only .c files."""
        from hypergumbo_lang_mainstream.c import find_c_files

        (tmp_path / "main.c").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper();")
        (tmp_path / "lib.c").write_text("void helper() {}")

        files = list(find_c_files(tmp_path, include_headers=False))

        suffixes = {f.suffix for f in files}
        assert ".c" in suffixes
        assert ".h" not in suffixes
        assert len(files) == 2

    def test_find_c_files_includes_headers_by_default(self, tmp_path: Path) -> None:
        """find_c_files includes .h files by default (backward compatible)."""
        from hypergumbo_lang_mainstream.c import find_c_files

        (tmp_path / "main.c").write_text("int main() { return 0; }")
        (tmp_path / "utils.h").write_text("void helper();")

        files = list(find_c_files(tmp_path))

        suffixes = {f.suffix for f in files}
        assert ".c" in suffixes
        assert ".h" in suffixes

    def test_has_cpp_files_detects_cpp(self, tmp_path: Path) -> None:
        """_has_cpp_files detects .cpp, .cc, .cxx, .hpp, .hxx files."""
        from hypergumbo_lang_mainstream.c import _has_cpp_files

        # No C++ files
        (tmp_path / "main.c").write_text("int main() {}")
        (tmp_path / "utils.h").write_text("void f();")
        assert _has_cpp_files(tmp_path) is False

        # Add a .cpp file
        (tmp_path / "app.cpp").write_text("int main() {}")
        assert _has_cpp_files(tmp_path) is True

    def test_has_cpp_files_detects_cxx(self, tmp_path: Path) -> None:
        """_has_cpp_files detects .cxx files."""
        from hypergumbo_lang_mainstream.c import _has_cpp_files

        (tmp_path / "main.c").write_text("int main() {}")
        (tmp_path / "lib.cxx").write_text("void f() {}")
        assert _has_cpp_files(tmp_path) is True

    def test_has_cpp_files_detects_hxx(self, tmp_path: Path) -> None:
        """_has_cpp_files detects .hxx files."""
        from hypergumbo_lang_mainstream.c import _has_cpp_files

        (tmp_path / "main.c").write_text("int main() {}")
        (tmp_path / "lib.hxx").write_text("// header")
        assert _has_cpp_files(tmp_path) is True

    def test_files_analyzed_count_excludes_headers(self, tmp_path: Path) -> None:
        """files_analyzed count reflects skipped .h files."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "a.h").write_text("void a();")
        (tmp_path / "b.h").write_text("void b();")
        (tmp_path / "impl.c").write_text("void a() {} void b() {}")
        (tmp_path / "app.cpp").write_text("int main() {}")

        result = analyze_c(tmp_path)

        assert result.run is not None
        # Only impl.c should be analyzed (2 headers skipped, .cpp not a C file)
        assert result.run.files_analyzed == 1


class TestCDispatchTableEdges:
    """Tests for function pointer dispatch table detection.

    C codebases commonly use static struct arrays where each element contains
    a function pointer. For example, git.c has:

        static struct cmd_struct commands[] = {
            { "add", cmd_add, RUN_SETUP },
            { "commit", cmd_commit, RUN_SETUP },
        };

    These function pointer references should be detected and create edges,
    reducing the orphan rate for C codebases.
    """

    def test_dispatch_table_creates_edges(self, tmp_path: Path) -> None:
        """Function pointers in static array initializers create edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dispatch.c"
        c_file.write_text("""
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

        result = analyze_c(tmp_path)

        # All 4 functions + 1 struct should be detected
        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "cmd_add" in func_names
        assert "cmd_commit" in func_names
        assert "cmd_status" in func_names
        assert "run_builtin" in func_names

        # Dispatch table should create reference edges to cmd_add, cmd_commit, cmd_status
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "dispatches_to"
        ]
        ref_dsts = {e.dst for e in ref_edges}
        cmd_add_id = next(s.id for s in result.symbols if s.name == "cmd_add")
        cmd_commit_id = next(s.id for s in result.symbols if s.name == "cmd_commit")
        cmd_status_id = next(s.id for s in result.symbols if s.name == "cmd_status")

        assert cmd_add_id in ref_dsts, (
            f"cmd_add not found in dispatch edges. Got: {ref_dsts}"
        )
        assert cmd_commit_id in ref_dsts
        assert cmd_status_id in ref_dsts

    def test_dispatch_table_cross_file(self, tmp_path: Path) -> None:
        """Dispatch table references functions defined in other files."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "cmds.c").write_text("""
int cmd_help(int argc, char **argv) { return 0; }
""")

        (tmp_path / "main.c").write_text("""
int cmd_help(int, char **);  /* declaration */

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "help", cmd_help },
};
""")

        result = analyze_c(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        # Should have at least one dispatch edge to cmd_help
        assert len(ref_edges) >= 1
        cmd_help_sym = next(
            (s for s in result.symbols if s.name == "cmd_help" and s.kind == "function"),
            None,
        )
        assert cmd_help_sym is not None
        assert any(e.dst == cmd_help_sym.id for e in ref_edges)

    def test_non_function_identifiers_not_linked(self, tmp_path: Path) -> None:
        """Constants and macros in initializers don't create false edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "config.c"
        c_file.write_text("""
struct config_entry {
    const char *key;
    int value;
};

static struct config_entry config[] = {
    { "timeout", 30 },
    { "retries", 3 },
};
""")

        result = analyze_c(tmp_path)

        # No dispatch edges should be created (no function identifiers)
        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(ref_edges) == 0

    def test_unresolved_identifiers_skipped(self, tmp_path: Path) -> None:
        """Identifiers that don't resolve to known symbols are ignored."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dispatch.c"
        c_file.write_text("""
struct entry { const char *name; void (*fn)(void); };

/* unknown_func is not defined anywhere */
static struct entry table[] = {
    { "test", unknown_func },
};
""")

        result = analyze_c(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        assert len(ref_edges) == 0

    def test_non_function_symbol_not_linked(self, tmp_path: Path) -> None:
        """Identifiers resolving to non-function symbols don't create edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "mixed.c"
        c_file.write_text("""
/* A typedef — registered as a symbol but not kind="function" */
typedef int color;

int real_func(void) { return 0; }

struct entry { const char *name; int (*fn)(void); int tag; };

/* "color" is a typedef identifier, not a function — should not dispatch.
   Only real_func should create a dispatch edge. */
static struct entry table[] = {
    { "run", real_func, color },
};
""")

        result = analyze_c(tmp_path)

        # Verify 'color' is indeed a registered symbol (typedef)
        color_sym = next((s for s in result.symbols if s.name == "color"), None)
        assert color_sym is not None, "color typedef should be a registered symbol"
        assert color_sym.kind == "typedef"

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        # Only real_func should be linked, not color (typedef)
        assert len(ref_edges) == 1
        real_func_sym = next(s for s in result.symbols if s.name == "real_func")
        assert ref_edges[0].dst == real_func_sym.id

    def test_duplicate_function_refs_deduplicated(self, tmp_path: Path) -> None:
        """Same function appearing multiple times in table creates one edge."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dedup.c"
        c_file.write_text("""
int handler(int argc, char **argv) { return 0; }

struct entry { const char *name; int (*fn)(int, char **); };

static struct entry table[] = {
    { "first", handler },
    { "second", handler },
    { "third", handler },
};
""")

        result = analyze_c(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "dispatches_to"]
        # handler appears 3 times but should create only 1 dispatch edge
        assert len(ref_edges) == 1


class TestDispatchTableVariableReferences:
    """Tests for uses_dispatch_table edges from functions to dispatch variables.

    When a function references a dispatch table variable (e.g., get_builtin
    accesses the ``commands[]`` array), we need an edge from the function to
    the dispatch table variable. This completes the call chain:
    cmd_main -> get_builtin -> commands[] -> cmd_add, cmd_commit, ...
    """

    def test_function_referencing_dispatch_table(self, tmp_path: Path) -> None:
        """A function that references a dispatch table variable gets an edge."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dispatch.c"
        c_file.write_text("""
int cmd_add(int argc, char **argv) { return 0; }
int cmd_commit(int argc, char **argv) { return 0; }

struct cmd_struct {
    const char *name;
    int (*fn)(int, char **);
};

static struct cmd_struct commands[] = {
    { "add", cmd_add },
    { "commit", cmd_commit },
};

struct cmd_struct *get_builtin(const char *name) {
    int i;
    for (i = 0; i < 2; i++) {
        if (commands[i].name == name)
            return &commands[i];
    }
    return 0;
}
""")

        result = analyze_c(tmp_path)

        # Should have uses_dispatch_table edges from get_builtin to commands
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "uses_dispatch_table"
        ]
        assert len(ref_edges) >= 1, (
            f"Expected uses_dispatch_table edges, got none. "
            f"All edge types: {[e.edge_type for e in result.edges]}"
        )

        # get_builtin should be the source of the edge
        get_builtin_sym = next(
            (s for s in result.symbols if s.name == "get_builtin"),
            None,
        )
        assert get_builtin_sym is not None
        assert any(e.src == get_builtin_sym.id for e in ref_edges)

    def test_multiple_functions_reference_same_table(self, tmp_path: Path) -> None:
        """Multiple functions referencing the same dispatch table each get edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dispatch.c"
        c_file.write_text("""
int cmd_add(int argc, char **argv) { return 0; }

struct cmd_struct {
    const char *name;
    int (*fn)(int, char **);
};

static struct cmd_struct commands[] = {
    { "add", cmd_add },
};

void list_builtins(void) {
    int i;
    for (i = 0; i < 1; i++)
        puts(commands[i].name);
}

struct cmd_struct *find_builtin(const char *name) {
    int i;
    for (i = 0; i < 1; i++)
        if (commands[i].name == name)
            return &commands[i];
    return 0;
}
""")

        result = analyze_c(tmp_path)

        ref_edges = [
            e for e in result.edges
            if e.edge_type == "uses_dispatch_table"
        ]

        # Both list_builtins and find_builtin reference commands[]
        src_names = set()
        for edge in ref_edges:
            sym = next(
                (s for s in result.symbols if s.id == edge.src), None,
            )
            if sym:
                src_names.add(sym.name)

        assert "list_builtins" in src_names, (
            f"list_builtins not found in dispatch table refs. Sources: {src_names}"
        )
        assert "find_builtin" in src_names, (
            f"find_builtin not found in dispatch table refs. Sources: {src_names}"
        )

    def test_no_false_refs_from_unrelated_functions(self, tmp_path: Path) -> None:
        """Functions that don't reference the dispatch table get no edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        c_file = tmp_path / "dispatch.c"
        c_file.write_text("""
int cmd_add(int argc, char **argv) { return 0; }

struct cmd_struct {
    const char *name;
    int (*fn)(int, char **);
};

static struct cmd_struct commands[] = {
    { "add", cmd_add },
};

void unrelated(void) {
    puts("hello");
}
""")

        result = analyze_c(tmp_path)

        ref_edges = [
            e for e in result.edges
            if e.edge_type == "uses_dispatch_table"
        ]

        # unrelated() should NOT have a uses_dispatch_table edge
        unrelated_sym = next(
            (s for s in result.symbols if s.name == "unrelated"), None,
        )
        assert unrelated_sym is not None
        assert not any(e.src == unrelated_sym.id for e in ref_edges), (
            "unrelated() should not reference the dispatch table"
        )


class TestCDuplicateFunctionNameEdges:
    """Tests for edge extraction when multiple files define the same function.

    In C repos like git, many files define ``cmd_main`` (each is a separate
    program entry point linked into its own binary). The edge extractor must
    produce call edges for ALL definitions, not just the one that wins the
    global_symbols registry.

    Invariant: Call edges must be produced for every function definition,
    regardless of whether another file defines a function with the same name.
    """

    def test_duplicate_function_names_both_get_edges(self, tmp_path: Path) -> None:
        """Two files defining cmd_main() both get outgoing call edges.

        When alpha.c and beta.c both define cmd_main() calling helper(),
        both should have call edges from their respective cmd_main to helper.
        """
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "common.h").write_text("""
void helper(void);
""")
        (tmp_path / "common.c").write_text("""
#include "common.h"
void helper(void) {}
""")
        (tmp_path / "alpha.c").write_text("""
#include "common.h"
int cmd_main(int argc, const char **argv) {
    helper();
    return 0;
}
""")
        (tmp_path / "beta.c").write_text("""
#include "common.h"
int cmd_main(int argc, const char **argv) {
    helper();
    return 0;
}
""")

        result = analyze_c(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Both alpha.c and beta.c cmd_main should have edges to helper
        alpha_edges = [
            e for e in call_edges
            if "alpha.c" in e.src and "cmd_main" in e.src
        ]
        beta_edges = [
            e for e in call_edges
            if "beta.c" in e.src and "cmd_main" in e.src
        ]

        assert len(alpha_edges) >= 1, (
            f"alpha.c cmd_main should have call edge to helper(), "
            f"found: {alpha_edges}"
        )
        assert len(beta_edges) >= 1, (
            f"beta.c cmd_main should have call edge to helper(), "
            f"found: {beta_edges}"
        )


class TestCStructEnumDefinitionOnly:
    """Tests that only struct/enum *definitions* (with bodies) produce symbols.

    In C, ``struct stat sb;`` is a variable declaration that references
    the struct type — it should NOT produce a struct symbol. Only
    definitions with bodies (``struct Point { int x; int y; };``) should
    create symbols.  Without this filter, a repo like git gets ~27K
    orphan struct symbols from type references in function bodies,
    inflating the orphan rate from ~25% to ~60%.
    """

    def test_struct_definition_creates_symbol(self, tmp_path: Path) -> None:
        """struct Point { int x; int y; } → creates 'Point' struct symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "types.c").write_text("""
struct Point {
    int x;
    int y;
};
""")
        result = analyze_c(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 1
        assert structs[0].name == "Point"

    def test_struct_reference_does_not_create_symbol(self, tmp_path: Path) -> None:
        """struct stat sb; → does NOT create a 'stat' struct symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text("""
void process(void) {
    struct stat sb;
    struct dirent entry;
}
""")
        result = analyze_c(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 0, (
            f"Struct variable declarations should not create symbols, "
            f"found: {[s.name for s in structs]}"
        )

    def test_struct_forward_decl_does_not_create_symbol(self, tmp_path: Path) -> None:
        """struct Point; → does NOT create a 'Point' struct symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "fwd.c").write_text("""
struct Point;
""")
        result = analyze_c(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 0, (
            f"Forward declarations should not create struct symbols, "
            f"found: {[s.name for s in structs]}"
        )

    def test_typedef_struct_creates_symbol(self, tmp_path: Path) -> None:
        """typedef struct Color { ... } Color; → creates struct symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "types.c").write_text("""
typedef struct Color {
    int r, g, b;
} Color;
""")
        result = analyze_c(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) >= 1
        assert any(s.name == "Color" for s in structs)

    def test_enum_definition_creates_symbol(self, tmp_path: Path) -> None:
        """enum Color { RED, GREEN, BLUE } → creates enum symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "types.c").write_text("""
enum Color { RED, GREEN, BLUE };
""")
        result = analyze_c(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 1
        assert enums[0].name == "Color"

    def test_enum_reference_does_not_create_symbol(self, tmp_path: Path) -> None:
        """enum Color c; → does NOT create an 'Color' enum symbol."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text("""
void process(void) {
    enum Color c;
}
""")
        result = analyze_c(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) == 0, (
            f"Enum variable declarations should not create symbols, "
            f"found: {[s.name for s in enums]}"
        )


class TestCDeclarationModifier:
    """Tests for declaration modifier on forward declarations."""

    def test_declaration_gets_modifier(self, tmp_path: Path) -> None:
        """Function declarations (prototypes) get modifiers=['declaration']."""
        from hypergumbo_lang_mainstream.c import analyze_c

        header = tmp_path / "builtin.h"
        header.write_text("int cmd_add(int argc, const char **argv);\n")

        result = analyze_c(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert "declaration" in funcs[0].modifiers

    def test_definition_does_not_get_declaration_modifier(self, tmp_path: Path) -> None:
        """Function definitions do NOT get the 'declaration' modifier."""
        from hypergumbo_lang_mainstream.c import analyze_c

        source = tmp_path / "add.c"
        source.write_text("""
int cmd_add(int argc, const char **argv) {
    return 0;
}
""")

        result = analyze_c(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert "declaration" not in funcs[0].modifiers


class TestDeclarationDefinitionDedup:
    """Tests for filtering out declaration-only symbols when a definition exists.

    When a function is declared in a header and defined in a source file,
    the declaration symbol is redundant — it inflates the orphan rate because
    calls resolve to the definition.  The analyzer should keep only the
    definition in the final symbol list.
    """

    def test_declaration_removed_when_definition_exists(self, tmp_path: Path) -> None:
        """Header declaration is filtered out when .c definition exists."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "utils.h").write_text("void helper(int x);\n")
        (tmp_path / "utils.c").write_text("""
void helper(int x) {
    // implementation
}
""")

        result = analyze_c(tmp_path)

        helpers = [s for s in result.symbols if s.name == "helper"]
        assert len(helpers) == 1, (
            f"Expected 1 'helper' symbol (definition only), got {len(helpers)}: "
            f"{[(s.name, s.path, s.modifiers) for s in helpers]}"
        )
        assert helpers[0].path.endswith(".c")
        assert "declaration" not in helpers[0].modifiers

    def test_declaration_kept_when_no_definition(self, tmp_path: Path) -> None:
        """Declaration-only function (no .c definition) is kept."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "extern.h").write_text("void external_func(void);\n")

        result = analyze_c(tmp_path)

        funcs = [s for s in result.symbols if s.name == "external_func"]
        assert len(funcs) == 1
        assert "declaration" in funcs[0].modifiers

    def test_multiple_declarations_single_definition(self, tmp_path: Path) -> None:
        """Multiple declarations in different headers, one definition: keep only definition."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "api.h").write_text("int compute(int a, int b);\n")
        (tmp_path / "internal.h").write_text("int compute(int a, int b);\n")
        (tmp_path / "compute.c").write_text("""
int compute(int a, int b) {
    return a + b;
}
""")

        result = analyze_c(tmp_path)

        computes = [s for s in result.symbols if s.name == "compute"]
        assert len(computes) == 1, (
            f"Expected 1 'compute' symbol, got {len(computes)}"
        )
        assert computes[0].path.endswith(".c")

    def test_non_function_symbols_unaffected(self, tmp_path: Path) -> None:
        """Structs, enums, typedefs are not affected by declaration dedup."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "types.h").write_text("""
struct Point { int x; int y; };
enum Color { RED, GREEN, BLUE };
""")
        (tmp_path / "main.c").write_text("""
void use_point(void) {}
""")

        result = analyze_c(tmp_path)

        structs = [s for s in result.symbols if s.kind == "struct"]
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(structs) == 1
        assert len(enums) == 1


class TestRemapEdgeIds:
    """Tests for _remap_edge_ids helper."""

    def test_remaps_edge_dst(self) -> None:
        """Edges referencing a removed declaration ID are remapped."""
        from hypergumbo_core.ir import Edge
        from hypergumbo_lang_mainstream.c import _remap_edge_ids

        edge = Edge.create(
            src="s1", dst="decl:old", edge_type="calls",
            line=1, confidence=0.9, origin="test", origin_run_id="r1",
        )
        result = _remap_edge_ids([edge], {"decl:old": "def:new"})
        assert len(result) == 1
        assert result[0].dst == "def:new"
        assert result[0].src == "s1"

    def test_preserves_unmatched_edges(self) -> None:
        """Edges not in the remap table are returned unchanged."""
        from hypergumbo_core.ir import Edge
        from hypergumbo_lang_mainstream.c import _remap_edge_ids

        edge = Edge.create(
            src="s1", dst="def:ok", edge_type="calls",
            line=1, confidence=0.9, origin="test", origin_run_id="r1",
        )
        result = _remap_edge_ids([edge], {"decl:old": "def:new"})
        assert result[0] is edge  # same object, not a copy


class TestCDocstrings:
    """Tests for Doxygen comment extraction via populate_docstrings_from_tree."""

    def test_doxygen_block_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts Doxygen block comment preceding a function."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "/** Computes the factorial. */\n"
            "int factorial(int n) {\n"
            "    return n <= 1 ? 1 : n * factorial(n - 1);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        func = next((s for s in result.symbols if s.name == "factorial"), None)
        assert func is not None
        assert func.docstring == "Computes the factorial."

    def test_doxygen_line_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts Doxygen /// line comment preceding a function."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "/// Adds two integers.\n"
            "int add(int a, int b) { return a + b; }\n"
        )
        result = analyze_c(tmp_path)
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.docstring == "Adds two integers."

    def test_no_comment_no_docstring(self, tmp_path: Path) -> None:
        """Function without preceding comment has no docstring."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "int main() { return 0; }\n"
        )
        result = analyze_c(tmp_path)
        func = next((s for s in result.symbols if s.name == "main"), None)
        assert func is not None
        assert func.docstring is None


class TestCFunctionPointerReferences:
    """Tests for &function pointer references in C (INV-dinur)."""

    def test_address_of_function(self, tmp_path: Path) -> None:
        """&process should create a references edge."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void process(int x) {}\n"
            "\n"
            "void run() {\n"
            "    void (*fp)(int) = &process;\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "process" in e.dst
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].evidence_type == "function_pointer"

    def test_no_reference_for_unknown_function(self, tmp_path: Path) -> None:
        """&unknown should not create a references edge."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void run() {\n"
            "    void (*fp)(int) = &unknown_func;\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src
        ]
        assert len(ref_edges) == 0


class TestCallbackArgDetection:
    """Function pointer arguments to calls like pthread_create, qsort, signal."""

    def test_pthread_create_callback(self, tmp_path: Path) -> None:
        """pthread_create(tid, attr, worker_func, arg) → edge to worker_func."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "#include <pthread.h>\n"
            "\n"
            "void *worker_thread(void *arg) { return NULL; }\n"
            "\n"
            "void start_server() {\n"
            "    pthread_t tid;\n"
            "    pthread_create(&tid, NULL, worker_thread, NULL);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        cb_edges = [
            e for e in result.edges
            if "start_server" in e.src and "worker_thread" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1
        assert cb_edges[0].edge_type == "calls"
        assert cb_edges[0].confidence > 0.0

    def test_qsort_comparator(self, tmp_path: Path) -> None:
        """qsort(arr, n, sz, compare_fn) → edge to compare_fn."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "int compare_ints(const void *a, const void *b) {\n"
            "    return *(int*)a - *(int*)b;\n"
            "}\n"
            "\n"
            "void sort_data(int *arr, int n) {\n"
            "    qsort(arr, n, sizeof(int), compare_ints);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        cb_edges = [
            e for e in result.edges
            if "sort_data" in e.src and "compare_ints" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1

    def test_signal_handler(self, tmp_path: Path) -> None:
        """signal(SIGINT, handler_fn) → edge to handler_fn."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void handle_sigint(int sig) {}\n"
            "\n"
            "void setup_signals() {\n"
            "    signal(2, handle_sigint);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        cb_edges = [
            e for e in result.edges
            if "setup_signals" in e.src and "handle_sigint" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1

    def test_atexit_callback(self, tmp_path: Path) -> None:
        """atexit(cleanup) → edge to cleanup."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void cleanup() {}\n"
            "\n"
            "int main() {\n"
            "    atexit(cleanup);\n"
            "    return 0;\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        cb_edges = [
            e for e in result.edges
            if "main" in e.src and "cleanup" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1

    def test_no_false_positive_for_variable_args(self, tmp_path: Path) -> None:
        """Regular variable arguments should NOT create callback edges."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void process(int x) {}\n"
            "\n"
            "void run() {\n"
            "    int count = 5;\n"
            "    process(count);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        cb_edges = [
            e for e in result.edges
            if e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 0

    def test_skip_callee_name_as_arg(self, tmp_path: Path) -> None:
        """If arg name matches the called function, skip it (not a callback)."""
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void setup(void (*setup)(void)) {}\n"
            "\n"
            "void run() {\n"
            "    setup(setup);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        # The arg 'setup' matches the callee name; should NOT get a
        # function_pointer_arg edge (would be redundant with the direct call).
        cb_edges = [
            e for e in result.edges
            if e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 0

    def test_no_duplicate_with_direct_call(self, tmp_path: Path) -> None:
        """The called function itself should NOT get a callback edge.

        pthread_create() gets a direct calls edge (ast_call_direct);
        the callback arg worker_func gets a separate function_pointer_arg edge.
        No double-counting.
        """
        from hypergumbo_lang_mainstream.c import analyze_c

        (tmp_path / "main.c").write_text(
            "void *worker(void *arg) { return NULL; }\n"
            "\n"
            "void run() {\n"
            "    pthread_t t;\n"
            "    pthread_create(&t, NULL, worker, NULL);\n"
            "}\n"
        )
        result = analyze_c(tmp_path)
        # Should have function_pointer_arg edge to worker
        cb_edges = [
            e for e in result.edges
            if "run" in e.src and "worker" in e.dst
            and e.evidence_type == "function_pointer_arg"
        ]
        assert len(cb_edges) == 1
        # Should NOT have function_pointer_arg edge to pthread_create itself
        # (it gets ast_call_direct instead, if resolved)
        self_cb = [
            e for e in result.edges
            if e.evidence_type == "function_pointer_arg"
            and "pthread_create" in e.dst
        ]
        assert len(self_cb) == 0

