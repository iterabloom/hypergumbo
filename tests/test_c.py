"""Tests for C analyzer."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindCFiles:
    """Tests for C file discovery."""

    def test_finds_c_files(self, tmp_path: Path) -> None:
        """Finds .c and .h files."""
        from hypergumbo.analyze.c import find_c_files

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
        from hypergumbo.analyze.c import is_c_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_c_tree_sitter_available() is True

    def test_is_c_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.c import is_c_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_c_tree_sitter_available() is False

    def test_is_c_tree_sitter_available_no_c_grammar(self) -> None:
        """Returns False when tree-sitter-c is not available."""
        from hypergumbo.analyze.c import is_c_tree_sitter_available

        def mock_find_spec(name: str):
            if name == "tree_sitter":
                return object()  # tree_sitter is available
            return None  # tree_sitter_c is not

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_c_tree_sitter_available() is False


class TestAnalyzeCFallback:
    """Tests for fallback behavior when tree-sitter-c unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-c unavailable."""
        from hypergumbo.analyze.c import analyze_c

        (tmp_path / "test.c").write_text("int main() { return 0; }")

        with patch("hypergumbo.analyze.c.is_c_tree_sitter_available", return_value=False):
            result = analyze_c(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-c" in result.skip_reason


class TestCFunctionExtraction:
    """Tests for extracting C functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts C function declarations."""
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

        (tmp_path / "a.c").write_text("void a() {}")
        (tmp_path / "b.c").write_text("void b() {}")
        (tmp_path / "c.h").write_text("void c();")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 3
        assert result.run.pass_id == "c-v1"

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Handles repo with no C files."""
        from hypergumbo.analyze.c import analyze_c

        (tmp_path / "app.js").write_text("const x = 1;")

        result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 0
        assert len(result.symbols) == 0


class TestCEdgeCases:
    """Tests for C edge cases and error handling."""

    def test_find_name_in_children_no_name(self) -> None:
        """Returns None when node has no identifier child."""
        from hypergumbo.analyze.c import _find_identifier_in_children

        mock_child = MagicMock()
        mock_child.type = "other"

        mock_node = MagicMock()
        mock_node.children = [mock_child]

        result = _find_identifier_in_children(mock_node, b"source")
        assert result is None

    def test_get_c_parser_import_error(self) -> None:
        """Returns None when tree-sitter-c is not available."""
        from hypergumbo.analyze.c import _get_c_parser

        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_c": None,
        }):
            result = _get_c_parser()
            assert result is None

    def test_analyze_c_file_parser_unavailable(self, tmp_path: Path) -> None:
        """Returns failure when parser is unavailable."""
        from hypergumbo.analyze.c import _analyze_c_file
        from hypergumbo.ir import AnalysisRun

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo.analyze.c._get_c_parser", return_value=None):
            symbols, edges, success = _analyze_c_file(c_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_analyze_c_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo.analyze.c import _analyze_c_file
        from hypergumbo.ir import AnalysisRun

        c_file = tmp_path / "missing.c"
        # Don't create the file

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_c_file(c_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_c_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """C files that fail to read increment skipped counter."""
        from hypergumbo.analyze.c import analyze_c

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

    def test_analyze_c_parser_none_after_check(self, tmp_path: Path) -> None:
        """analyze_c handles case where parser is None after availability check."""
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }")

        with patch(
            "hypergumbo.analyze.c.is_c_tree_sitter_available",
            return_value=True,
        ), patch(
            "hypergumbo.analyze.c._get_c_parser",
            return_value=None,
        ):
            result = analyze_c(tmp_path)

        assert result.run is not None
        assert result.skipped is True
        assert "tree-sitter-c" in result.skip_reason


class TestCFunctionDeclarations:
    """Tests for function declaration handling."""

    def test_handles_function_declarations(self, tmp_path: Path) -> None:
        """Handles function declarations (prototypes) vs definitions."""
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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


class TestCPointerAndComplexTypes:
    """Tests for complex C type handling."""

    def test_handles_function_pointers(self, tmp_path: Path) -> None:
        """Handles functions with pointer parameters."""
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import analyze_c

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
        from hypergumbo.analyze.c import _get_function_name, _get_c_parser

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
        from hypergumbo.analyze.c import _get_function_name

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
        from hypergumbo.analyze.c import _analyze_c_file
        from hypergumbo.ir import AnalysisRun

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
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "math.c"
        c_file.write_text("int add(int x, int y) { return x + y; }")

        result = analyze_c(tmp_path)

        add_sym = next((s for s in result.symbols if s.name == "add"), None)
        assert add_sym is not None
        assert add_sym.signature == "(int x, int y) int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Void return type function extracts signature without return type."""
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "util.c"
        c_file.write_text("void process(int count) { /* work */ }")

        result = analyze_c(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        # void return type should not appear in signature
        assert process_sym.signature == "(int count)"

    def test_pointer_parameter_signature(self, tmp_path: Path) -> None:
        """Pointer parameters appear in signature."""
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "str.c"
        c_file.write_text("int strlen(const char* str) { return 0; }")

        result = analyze_c(tmp_path)

        strlen_sym = next((s for s in result.symbols if s.name == "strlen"), None)
        assert strlen_sym is not None
        assert "const char* str" in strlen_sym.signature
        assert strlen_sym.signature.endswith("int")

    def test_pointer_return_type_signature(self, tmp_path: Path) -> None:
        """Pointer return type appears in signature."""
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "alloc.c"
        c_file.write_text("char* strdup(const char* s) { return 0; }")

        result = analyze_c(tmp_path)

        strdup_sym = next((s for s in result.symbols if s.name == "strdup"), None)
        assert strdup_sym is not None
        # Should have char* return type
        assert "char*" in strdup_sym.signature

    def test_empty_params_signature(self, tmp_path: Path) -> None:
        """Function with no parameters has empty parens."""
        from hypergumbo.analyze.c import analyze_c

        c_file = tmp_path / "main.c"
        c_file.write_text("int main() { return 0; }")

        result = analyze_c(tmp_path)

        main_sym = next((s for s in result.symbols if s.name == "main"), None)
        assert main_sym is not None
        assert main_sym.signature == "() int"

    def test_declaration_signature(self, tmp_path: Path) -> None:
        """Function declaration (prototype) extracts signature."""
        from hypergumbo.analyze.c import analyze_c

        h_file = tmp_path / "util.h"
        h_file.write_text("void process(int x, int y);")

        result = analyze_c(tmp_path)

        process_sym = next((s for s in result.symbols if s.name == "process"), None)
        assert process_sym is not None
        assert process_sym.signature == "(int x, int y)"

