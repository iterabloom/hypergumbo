"""Tests for hypergumbo.analyze.base module.

Tests the shared infrastructure used by all tree-sitter analyzers:
- iter_tree: Iterative tree traversal
- iter_tree_with_context: Context-aware traversal
- node_text, find_child_by_type, find_child_by_field: Node helpers
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    find_child_by_field,
    find_child_by_type,
    is_grammar_available,
    iter_tree,
    iter_tree_with_context,
    make_entry_stable_id,
    make_file_id,
    make_route_stable_id,
    make_symbol_id,
    node_text,
    normalize_generic_params,
    normalize_signature_go,
    normalize_signature_names_first,
    normalize_signature_php,
    normalize_signature_types_first,
    split_params_top_level,
    strip_fqn_prefix,
)

if TYPE_CHECKING:
    pass


class TestIterTree:
    """Tests for iter_tree function."""

    def test_iterates_all_nodes(self) -> None:
        """Should yield all nodes in the tree."""
        # Build mock tree: root -> [child1, child2 -> [grandchild]]
        grandchild = MagicMock()
        grandchild.children = []
        grandchild.type = "identifier"

        child1 = MagicMock()
        child1.children = []
        child1.type = "type"

        child2 = MagicMock()
        child2.children = [grandchild]
        child2.type = "function"

        root = MagicMock()
        root.children = [child1, child2]
        root.type = "program"

        nodes = list(iter_tree(root))

        assert len(nodes) == 4
        assert nodes[0] is root
        assert nodes[1] is child1
        assert nodes[2] is child2
        assert nodes[3] is grandchild

    def test_handles_empty_tree(self) -> None:
        """Should handle a tree with only root node."""
        root = MagicMock()
        root.children = []
        root.type = "program"

        nodes = list(iter_tree(root))

        assert len(nodes) == 1
        assert nodes[0] is root

    def test_depth_first_order(self) -> None:
        """Should traverse in depth-first order."""
        # Build: root -> [a -> [a1, a2], b -> [b1]]
        a1 = MagicMock(children=[], type="a1")
        a2 = MagicMock(children=[], type="a2")
        b1 = MagicMock(children=[], type="b1")
        a = MagicMock(children=[a1, a2], type="a")
        b = MagicMock(children=[b1], type="b")
        root = MagicMock(children=[a, b], type="root")

        types = [n.type for n in iter_tree(root)]

        # Depth-first: root, a, a1, a2, b, b1
        assert types == ["root", "a", "a1", "a2", "b", "b1"]


class TestIterTreeWithContext:
    """Tests for iter_tree_with_context function."""

    def test_tracks_function_context(self) -> None:
        """Should track enclosing function for nested nodes."""
        # Build: root -> [func_def -> [call_expr]]
        call_expr = MagicMock(children=[], type="call_expression")
        func_def = MagicMock(children=[call_expr], type="function_definition")
        root = MagicMock(children=[func_def], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        # root has no context, func_def has no context (it IS the context),
        # call_expr has func_def as context
        assert results[0] == (root, None)
        assert results[1] == (func_def, None)
        assert results[2] == (call_expr, func_def)

    def test_no_context_outside_function(self) -> None:
        """Should return None context for nodes outside any function."""
        import_node = MagicMock(children=[], type="import")
        root = MagicMock(children=[import_node], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        assert results[0] == (root, None)
        assert results[1] == (import_node, None)

    def test_nested_contexts(self) -> None:
        """Should track innermost context when nested."""
        # Build: root -> [outer_func -> [inner_func -> [call]]]
        call = MagicMock(children=[], type="call")
        inner_func = MagicMock(children=[call], type="function_definition")
        outer_func = MagicMock(children=[inner_func], type="function_definition")
        root = MagicMock(children=[outer_func], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        # call should have inner_func as context
        call_result = next(r for r in results if r[0].type == "call")
        assert call_result[1] is inner_func

    def test_multiple_context_types(self) -> None:
        """Should track context for multiple node types."""
        method_body = MagicMock(children=[], type="body")
        method = MagicMock(children=[method_body], type="method_definition")
        class_def = MagicMock(children=[method], type="class_definition")
        root = MagicMock(children=[class_def], type="program")

        # Track both class and method definitions
        results = list(
            iter_tree_with_context(root, {"class_definition", "method_definition"})
        )

        # method_body should have method as context (innermost)
        body_result = next(r for r in results if r[0].type == "body")
        assert body_result[1] is method


class TestNodeText:
    """Tests for node_text function."""

    def test_extracts_text(self) -> None:
        """Should extract text from node byte range."""
        node = MagicMock()
        node.start_byte = 6
        node.end_byte = 11
        source = b"hello world"

        result = node_text(node, source)

        assert result == "world"

    def test_handles_unicode(self) -> None:
        """Should handle UTF-8 encoded text."""
        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 6
        source = "日本語".encode("utf-8")

        result = node_text(node, source)

        assert result == "日本"


class TestFindChildByType:
    """Tests for find_child_by_type function."""

    def test_finds_matching_child(self) -> None:
        """Should return first child matching type."""
        child1 = MagicMock()
        child1.type = "identifier"
        child2 = MagicMock()
        child2.type = "type"
        node = MagicMock()
        node.children = [child1, child2]

        result = find_child_by_type(node, "type")

        assert result is child2

    def test_returns_none_when_not_found(self) -> None:
        """Should return None when no child matches."""
        child = MagicMock()
        child.type = "identifier"
        node = MagicMock()
        node.children = [child]

        result = find_child_by_type(node, "type")

        assert result is None


class TestFindChildByField:
    """Tests for find_child_by_field function."""

    def test_delegates_to_node_method(self) -> None:
        """Should delegate to node's child_by_field_name method."""
        expected = MagicMock()
        node = MagicMock()
        node.child_by_field_name.return_value = expected

        result = find_child_by_field(node, "name")

        node.child_by_field_name.assert_called_once_with("name")
        assert result is expected


class TestMakeSymbolId:
    """Tests for make_symbol_id function."""

    def test_generates_correct_format(self) -> None:
        """Should generate ID in expected format."""
        result = make_symbol_id("go", "main.go", 10, 20, "main", "function")

        assert result == "go:main.go:10-20:main:function"


class TestMakeFileId:
    """Tests for make_file_id function."""

    def test_generates_correct_format(self) -> None:
        """Should generate file ID in expected format."""
        result = make_file_id("python", "src/main.py")

        assert result == "python:src/main.py:1-1:file:file"


class TestMakeRouteStableId:
    """Tests for make_route_stable_id — ADR-0014 §4 route identity."""

    def test_different_paths_produce_different_ids(self) -> None:
        """GET /users and GET /posts must NOT collide."""
        id1 = make_route_stable_id("GET", "/users")
        id2 = make_route_stable_id("GET", "/posts")
        assert id1 != id2

    def test_different_methods_produce_different_ids(self) -> None:
        """GET /users and POST /users must NOT collide."""
        id1 = make_route_stable_id("GET", "/users")
        id2 = make_route_stable_id("POST", "/users")
        assert id1 != id2

    def test_case_insensitive_method(self) -> None:
        """Method is normalized to uppercase internally."""
        id1 = make_route_stable_id("get", "/users")
        id2 = make_route_stable_id("GET", "/users")
        assert id1 == id2

    def test_returns_hex_digest(self) -> None:
        """Result is a hex string (sha256 digest)."""
        result = make_route_stable_id("GET", "/users")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_route_stable_id("POST", "/api/v1/items")
        id2 = make_route_stable_id("POST", "/api/v1/items")
        assert id1 == id2


class TestMakeEntryStableId:
    """Tests for make_entry_stable_id — ADR-0014 §4 entry-point identity."""

    def test_different_names_produce_different_ids(self) -> None:
        """Two @vertex functions with different names must NOT collide."""
        id1 = make_entry_stable_id("vertex", "main_vs")
        id2 = make_entry_stable_id("vertex", "shadow_vs")
        assert id1 != id2

    def test_different_types_produce_different_ids(self) -> None:
        """@vertex main and @fragment main must NOT collide."""
        id1 = make_entry_stable_id("vertex", "main")
        id2 = make_entry_stable_id("fragment", "main")
        assert id1 != id2

    def test_returns_hex_digest(self) -> None:
        """Result is a hex string (sha256 digest)."""
        result = make_entry_stable_id("compute", "dispatch")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_entry_stable_id("fragment", "main_fs")
        id2 = make_entry_stable_id("fragment", "main_fs")
        assert id1 == id2


class TestStripFqnPrefix:
    """Tests for strip_fqn_prefix utility."""

    def test_simple_name_unchanged(self) -> None:
        assert strip_fqn_prefix("String") == "String"

    def test_dotted_path_strips_prefix(self) -> None:
        assert strip_fqn_prefix("java.lang.String") == "String"

    def test_two_segment_path(self) -> None:
        assert strip_fqn_prefix("http.Request") == "Request"

    def test_preserves_generic_params(self) -> None:
        assert strip_fqn_prefix("java.util.List<String>") == "List<String>"

    def test_nested_fqn_in_generics(self) -> None:
        assert strip_fqn_prefix("java.util.Map<java.lang.String, Integer>") == (
            "Map<java.lang.String, Integer>"
        )

    def test_square_bracket_generics(self) -> None:
        assert strip_fqn_prefix("scala.collection.List[Int]") == "List[Int]"

    def test_empty_string(self) -> None:
        assert strip_fqn_prefix("") == ""


class TestNormalizeGenericParams:
    """Tests for normalize_generic_params utility."""

    def test_replaces_type_params(self) -> None:
        assert normalize_generic_params("List<T>", ["T"]) == "List<$0>"

    def test_multiple_params(self) -> None:
        result = normalize_generic_params("Map<K, V>", ["K", "V"])
        assert result == "Map<$0, $1>"

    def test_preserves_concrete_types(self) -> None:
        result = normalize_generic_params("Map<String, T>", ["T"])
        assert result == "Map<String, $0>"

    def test_empty_type_params(self) -> None:
        assert normalize_generic_params("List<String>", []) == "List<String>"

    def test_type_param_in_multiple_positions(self) -> None:
        result = normalize_generic_params("(T, T) -> T", ["T"])
        assert result == "($0, $0) -> $0"

    def test_does_not_replace_substrings(self) -> None:
        # "T" should not match inside "String" or "Int"
        result = normalize_generic_params("Map<String, Int>", ["T"])
        assert result == "Map<String, Int>"


class TestSplitParamsTopLevel:
    """Tests for split_params_top_level utility."""

    def test_simple_params(self) -> None:
        assert split_params_top_level("int x, String y") == ["int x", "String y"]

    def test_generic_nesting(self) -> None:
        result = split_params_top_level("Map<String, Integer>, int x")
        assert result == ["Map<String, Integer>", "int x"]

    def test_empty_string(self) -> None:
        assert split_params_top_level("") == []

    def test_single_param(self) -> None:
        assert split_params_top_level("int x") == ["int x"]

    def test_nested_generics(self) -> None:
        result = split_params_top_level("Map<String, List<Integer>>, boolean flag")
        assert result == ["Map<String, List<Integer>>", "boolean flag"]


class TestNormalizeSignatureTypesFirst:
    """Tests for normalize_signature_types_first (Java, C#, Dart, Groovy)."""

    def test_java_basic(self) -> None:
        result = normalize_signature_types_first("(String name, int age) User")
        assert result == "(String,int)User"

    def test_java_void(self) -> None:
        result = normalize_signature_types_first("(String msg)")
        assert result == "(String)"

    def test_java_generics(self) -> None:
        result = normalize_signature_types_first(
            "(List<T> items) Map<K, V>", type_params=["T", "K", "V"]
        )
        assert result == "(List<$0>)Map<$1, $2>"

    def test_java_fqn(self) -> None:
        result = normalize_signature_types_first("(java.lang.String name) java.util.List")
        assert result == "(String)List"

    def test_java_varargs(self) -> None:
        result = normalize_signature_types_first("(String... args)")
        assert result == "(String)"

    def test_none_input(self) -> None:
        assert normalize_signature_types_first(None) is None

    def test_empty_input(self) -> None:
        assert normalize_signature_types_first("") is None

    def test_no_params(self) -> None:
        result = normalize_signature_types_first("() int")
        assert result == "()int"

    def test_constructor_no_return(self) -> None:
        result = normalize_signature_types_first("(String name, int age)")
        assert result == "(String,int)"

    def test_malformed_no_paren(self) -> None:
        assert normalize_signature_types_first("no parens here") is None

    def test_single_token_param(self) -> None:
        # Single token without space — treated as type
        result = normalize_signature_types_first("(int)")
        assert result == "(int)"

    def test_return_sep_colon(self) -> None:
        result = normalize_signature_types_first(
            "(String name): int", return_sep=":"
        )
        assert result == "(String)int"

    def test_void_return_not_skipped(self) -> None:
        result = normalize_signature_types_first(
            "(int x) void", skip_void_return=False
        )
        assert result == "(int)void"

    def test_empty_param_segment_skipped(self) -> None:
        """Empty param segments from consecutive commas are skipped."""
        result = normalize_signature_types_first("(int x,,String y) Result")
        assert result == "(int,String)Result"

    def test_unmatched_paren(self) -> None:
        """Unmatched opening paren returns None."""
        assert normalize_signature_types_first("(int x") is None


class TestNormalizeSignatureNamesFirst:
    """Tests for normalize_signature_names_first (Kotlin, Scala, Swift, Rust, TS, Python)."""

    def test_kotlin_basic(self) -> None:
        result = normalize_signature_names_first(
            "(name: String, age: Int): User", return_sep=":"
        )
        assert result == "(String,Int)User"

    def test_kotlin_no_return(self) -> None:
        result = normalize_signature_names_first("(msg: String)", return_sep=":")
        assert result == "(String)"

    def test_swift_arrow_return(self) -> None:
        result = normalize_signature_names_first(
            "(x: Int, y: Int) -> Int", return_sep="->"
        )
        assert result == "(Int,Int)Int"

    def test_rust_self_skip(self) -> None:
        result = normalize_signature_names_first(
            "(&self, x: i32) -> bool", return_sep="->", skip_self=True
        )
        assert result == "(i32)bool"

    def test_python_self_skip(self) -> None:
        result = normalize_signature_names_first(
            "(self, name: str, age: int) -> bool", return_sep="->", skip_self=True
        )
        assert result == "(str,int)bool"

    def test_python_no_annotations(self) -> None:
        result = normalize_signature_names_first(
            "(name, age)", return_sep="->"
        )
        assert result == "(name,age)"

    def test_typescript_generics(self) -> None:
        result = normalize_signature_names_first(
            "(items: Array<T>, mapper: (item: T) => U): Array<U>",
            type_params=["T", "U"],
            return_sep=":",
        )
        assert result == "(Array<$0>,(item: $0) => $1)Array<$1>"

    def test_none_input(self) -> None:
        assert normalize_signature_names_first(None) is None

    def test_empty_input(self) -> None:
        assert normalize_signature_names_first("") is None

    def test_scala_generics(self) -> None:
        result = normalize_signature_names_first(
            "(x: List[A], y: Map[A, B]): Option[B]",
            type_params=["A", "B"],
            return_sep=":",
        )
        assert result == "(List[$0],Map[$0, $1])Option[$1]"

    def test_malformed_no_paren(self) -> None:
        """Malformed signature without parens returns None."""
        assert normalize_signature_names_first("no parens here") is None

    def test_arrow_fallback_with_colon_sep(self) -> None:
        """When return_sep is ':', but rest starts with '->', use arrow fallback."""
        result = normalize_signature_names_first(
            "(x: Int) -> Bool", return_sep=":"
        )
        assert result == "(Int)Bool"

    def test_rest_without_separator(self) -> None:
        """Rest text that doesn't start with any separator is used as return."""
        result = normalize_signature_names_first(
            "(x: Int) Bool", return_sep=":"
        )
        assert result == "(Int)Bool"

    def test_empty_param_segment_skipped(self) -> None:
        """Empty param segments from consecutive commas are skipped."""
        result = normalize_signature_names_first(
            "(x: Int,,y: Bool): Bool", return_sep=":"
        )
        assert result == "(Int,Bool)Bool"


class TestNormalizeSignaturePhp:
    """Tests for normalize_signature_php."""

    def test_basic_typed(self) -> None:
        result = normalize_signature_php("(int $x, int $y): int")
        assert result == "(int,int)int"

    def test_void_return(self) -> None:
        result = normalize_signature_php("(string $msg): void")
        assert result == "(string)"

    def test_untyped_params(self) -> None:
        result = normalize_signature_php("($x, $y)")
        assert result == "($x,$y)"

    def test_none_input(self) -> None:
        assert normalize_signature_php(None) is None

    def test_nullable_type(self) -> None:
        result = normalize_signature_php("(?string $name): ?int")
        assert result == "(?string)?int"

    def test_malformed_no_paren(self) -> None:
        """Malformed signature without parens returns None."""
        assert normalize_signature_php("no parens") is None

    def test_empty_param_segment_skipped(self) -> None:
        """Empty param segments from consecutive commas are skipped."""
        result = normalize_signature_php("(int $x,,string $y): string")
        assert result == "(int,string)string"


class TestNormalizeSignatureGo:
    """Tests for normalize_signature_go."""

    def test_basic(self) -> None:
        result = normalize_signature_go("(name string, age int) error")
        assert result == "(string,int)error"

    def test_pointer_stripped(self) -> None:
        result = normalize_signature_go("(r *http.Request) error")
        assert result == "(Request)error"

    def test_tuple_return(self) -> None:
        result = normalize_signature_go("(x int) (int, error)")
        assert result == "(int)(int,error)"

    def test_no_return(self) -> None:
        result = normalize_signature_go("(msg string)")
        assert result == "(string)"

    def test_none_input(self) -> None:
        assert normalize_signature_go(None) is None

    def test_fqn_package_prefix(self) -> None:
        result = normalize_signature_go("(ctx context.Context) http.Response")
        assert result == "(Context)Response"

    def test_malformed_no_paren(self) -> None:
        """Malformed signature without parens returns None."""
        assert normalize_signature_go("no parens here") is None

    def test_empty_param_segment_skipped(self) -> None:
        """Empty param segments from consecutive commas are skipped."""
        result = normalize_signature_go("(x int,,y string) error")
        assert result == "(int,string)error"

    def test_single_token_param(self) -> None:
        """Single-token param (type without name) in Go."""
        result = normalize_signature_go("(error)")
        assert result == "(error)"

    def test_single_token_pointer_param(self) -> None:
        """Single-token pointer param has * stripped."""
        result = normalize_signature_go("(*Request)")
        assert result == "(Request)"

    def test_pointer_return(self) -> None:
        """Pointer return type has * stripped."""
        result = normalize_signature_go("(x int) *Response")
        assert result == "(int)Response"


class TestIsGrammarAvailable:
    """Tests for is_grammar_available function."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when grammar is importable."""
        # tree_sitter_go is installed in test environment
        result = is_grammar_available("tree_sitter_go")

        assert result is True

    def test_returns_false_when_missing(self) -> None:
        """Should return False when grammar is not installed."""
        result = is_grammar_available("tree_sitter_nonexistent_language")

        assert result is False

    def test_returns_false_when_tree_sitter_unavailable(self) -> None:
        """Should return False when tree_sitter itself is not installed (covers base.py:186)."""
        from unittest.mock import patch

        original_find_spec = __import__("importlib.util").util.find_spec

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return None
            return original_find_spec(name)

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            result = is_grammar_available("tree_sitter_go")

        assert result is False


class TestAnalysisResult:
    """Tests for AnalysisResult dataclass."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        result = AnalysisResult()

        assert result.symbols == []
        assert result.edges == []
        assert result.run is None
        assert result.skipped is False
        assert result.skip_reason == ""


class TestFileAnalysis:
    """Tests for FileAnalysis dataclass."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        analysis = FileAnalysis()

        assert analysis.symbols == []
        assert analysis.symbol_by_name == {}
