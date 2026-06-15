# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo.analyze.base module.

Tests the shared infrastructure used by all tree-sitter analyzers:
- iter_tree: Iterative tree traversal
- iter_tree_with_context: Context-aware traversal
- node_text, find_child_by_type, find_child_by_field: Node helpers
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    emit_module_attribute_refs,
    extract_doc_comment,
    find_child_by_field,
    find_child_by_type,
    is_grammar_available,
    iter_tree,
    iter_tree_with_context,
    make_doc_stable_id,
    make_entry_stable_id,
    make_file_id,
    make_protocol_stable_id,
    make_route_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text,
    normalize_generic_params,
    normalize_signature_go,
    normalize_signature_names_first,
    normalize_signature_php,
    normalize_signature_types_first,
    populate_docstrings_from_tree,
    split_params_top_level,
    strip_fqn_prefix,
    synthesize_file_symbols_for_dangling_edges,
    visibility_from_modifiers,
)
from hypergumbo_core.ir import Edge, Span, Symbol

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

    def test_returns_canonical_sha256_prefixed_form(self) -> None:
        """Result uses the canonical ``sha256:<16hex>`` shape (Phase 6 PR1).

        Pre-Phase-6 this returned a raw 64-char hexdigest; Phase 6 PR1
        (INV-hunup) aligned the output with the rest of the
        ``make_*_stable_id`` factory family so the ``id_format``
        validator pins a single canonical shape.
        """
        result = make_route_stable_id("GET", "/users")
        assert result.startswith("sha256:")
        suffix = result[len("sha256:"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_route_stable_id("POST", "/api/v1/items")
        id2 = make_route_stable_id("POST", "/api/v1/items")
        assert id1 == id2

    def test_empty_path_normalized_to_root(self) -> None:
        """Empty string path is normalized to '/' (INV-nimik)."""
        id_empty = make_route_stable_id("GET", "")
        id_root = make_route_stable_id("GET", "/")
        assert id_empty == id_root


class TestMakeEntryStableId:
    """Tests for make_entry_stable_id — ADR-0014 §4 entry-point identity."""

    def test_different_names_produce_different_ids(self) -> None:
        """Two @vertex functions with different names must NOT collide."""
        id1 = make_entry_stable_id("vertex", "main_vs", "shaders/a.wgsl")
        id2 = make_entry_stable_id("vertex", "shadow_vs", "shaders/a.wgsl")
        assert id1 != id2

    def test_different_types_produce_different_ids(self) -> None:
        """@vertex main and @fragment main must NOT collide."""
        id1 = make_entry_stable_id("vertex", "main", "shaders/a.wgsl")
        id2 = make_entry_stable_id("fragment", "main", "shaders/a.wgsl")
        assert id1 != id2

    def test_returns_canonical_sha256_prefixed_form(self) -> None:
        """Result uses the canonical ``sha256:<16hex>`` shape (Phase 6 PR1).

        See ``TestMakeRouteStableId.test_returns_canonical_sha256_prefixed_form``
        for the migration rationale.
        """
        result = make_entry_stable_id("compute", "dispatch", "shaders/a.wgsl")
        assert result.startswith("sha256:")
        suffix = result[len("sha256:"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_entry_stable_id("fragment", "main_fs", "shaders/a.wgsl")
        id2 = make_entry_stable_id("fragment", "main_fs", "shaders/a.wgsl")
        assert id1 == id2


class TestMakeProtocolStableId:
    """Tests for ``make_protocol_stable_id`` — Phase 6 PR1 (INV-hunup)."""

    def test_returns_canonical_sha256_prefixed_form(self) -> None:
        """Result uses the canonical ``sha256:<16hex>`` shape."""
        result = make_protocol_stable_id("db_query", "SELECT", "users")
        assert result.startswith("sha256:")
        suffix = result[len("sha256:"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_protocol_stable_id("message_queue", "kafka", "publish", "users")
        id2 = make_protocol_stable_id("message_queue", "kafka", "publish", "users")
        assert id1 == id2

    def test_different_categories_produce_different_ids(self) -> None:
        """Same parts under different categories must NOT collide.

        The category namespace protects against e.g. a graphql resolver
        named ``Query.users`` colliding with a db_query SELECT on
        ``users`` if both happened to hash the same identity tuple.
        """
        id1 = make_protocol_stable_id("db_query", "users")
        id2 = make_protocol_stable_id("graphql_resolver", "users")
        assert id1 != id2

    def test_different_parts_produce_different_ids(self) -> None:
        """Order matters: ``(a, b)`` and ``(b, a)`` produce distinct ids."""
        id1 = make_protocol_stable_id("event_sourcing", "publish", "user.created")
        id2 = make_protocol_stable_id("event_sourcing", "user.created", "publish")
        assert id1 != id2

    def test_accepts_zero_parts(self) -> None:
        """``make_protocol_stable_id("category")`` hashes the bare category.

        Defensive — no current caller passes zero parts, but the factory
        accepts it (the join over an empty tuple yields just the
        category) so the API doesn't blow up on a future caller.
        """
        result = make_protocol_stable_id("category_only")
        assert result.startswith("sha256:")
        assert len(result) == len("sha256:") + 16


class TestMakeTypedStableId:
    """Tests for make_typed_stable_id — ADR-0014 §3 typed-tier identity."""

    def test_returns_sha256_format(self) -> None:
        """Result uses sha256:{16-hex} format."""
        result = make_typed_stable_id(
            "method", "(String,int)User", name="m", qualified_name="m"
        )
        assert result.startswith("sha256:")
        assert len(result) == len("sha256:") + 16

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        id1 = make_typed_stable_id(
            "method", "(String,int)User", "public", name="m", qualified_name="m"
        )
        id2 = make_typed_stable_id(
            "method", "(String,int)User", "public", name="m", qualified_name="m"
        )
        assert id1 == id2

    def test_different_signatures_produce_different_ids(self) -> None:
        """Different normalized signatures produce different hashes."""
        id1 = make_typed_stable_id("method", "(String)void", name="m", qualified_name="m")
        id2 = make_typed_stable_id("method", "(int)void", name="m", qualified_name="m")
        assert id1 != id2

    def test_different_visibility_produces_different_ids(self) -> None:
        """Same signature with different visibility produces different hashes."""
        id1 = make_typed_stable_id(
            "method", "(String)void", "public", name="m", qualified_name="m"
        )
        id2 = make_typed_stable_id(
            "method", "(String)void", "private", name="m", qualified_name="m"
        )
        assert id1 != id2

    def test_different_containing_produces_different_ids(self) -> None:
        """Same signature in different containers produces different hashes."""
        id1 = make_typed_stable_id(
            "method", "(int)bool", "", "sha256:aaa", name="m", qualified_name="m"
        )
        id2 = make_typed_stable_id(
            "method", "(int)bool", "", "sha256:bbb", name="m", qualified_name="m"
        )
        assert id1 != id2

    def test_empty_visibility_accepted(self) -> None:
        """Empty visibility (Python, Go) produces a valid hash."""
        result = make_typed_stable_id(
            "function", "(int)bool", "", name="m", qualified_name="m"
        )
        assert result.startswith("sha256:")

    def test_different_decorators_produce_different_ids(self) -> None:
        """Same signature with different decorators produces different hashes."""
        id1 = make_typed_stable_id(
            "function", "()", decorators="lru_cache", name="m", qualified_name="m"
        )
        id2 = make_typed_stable_id(
            "function", "()", decorators="", name="m", qualified_name="m"
        )
        assert id1 != id2

    def test_decorator_order_matters(self) -> None:
        """Decorator string is included verbatim; callers sort before passing."""
        id1 = make_typed_stable_id(
            "method", "(int)void", decorators="A,B", name="m", qualified_name="m"
        )
        id2 = make_typed_stable_id(
            "method", "(int)void", decorators="B,A", name="m", qualified_name="m"
        )
        assert id1 != id2

    def test_differs_from_untyped(self) -> None:
        """Typed tier hash differs from what untyped would produce."""
        # Typed uses normalized_signature, untyped uses param_count + arity_flags.
        # These should never collide since the sig components are different.
        typed = make_typed_stable_id(
            "method", "(String,int)User", "public", name="m", qualified_name="m"
        )
        # An untyped-tier hash would look like "method:2:False,False,False:..."
        # Verify they don't accidentally match (sanity check)
        assert "method:2:" not in typed


class TestVisibilityFromModifiers:
    """Tests for visibility_from_modifiers utility."""

    def test_public(self) -> None:
        assert visibility_from_modifiers(["public", "static"]) == "public"

    def test_private(self) -> None:
        assert visibility_from_modifiers(["private"]) == "private"

    def test_protected(self) -> None:
        assert visibility_from_modifiers(["protected", "abstract"]) == "protected"

    def test_internal(self) -> None:
        assert visibility_from_modifiers(["internal"]) == "internal"

    def test_no_visibility(self) -> None:
        assert visibility_from_modifiers(["static", "final"]) == ""

    def test_empty_list(self) -> None:
        assert visibility_from_modifiers([]) == ""

    def test_none_input(self) -> None:
        assert visibility_from_modifiers(None) == ""


class TestIsExportedFromModifiers:
    """Tests for WI-zimum is_exported_from_modifiers utility."""

    def test_public_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["public"]) is True

    def test_exported_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["exported"]) is True

    def test_pub_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["pub"]) is True

    def test_pub_crate_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["pub(crate)"]) is True

    def test_pub_super_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["pub(super)"]) is True

    def test_pub_in_path_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["pub(in ::my_mod)"]) is True

    def test_private_is_not_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["private"]) is False

    def test_unexported_is_not_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["unexported"]) is False

    def test_empty_list_is_not_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers([]) is False

    def test_none_is_not_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(None) is False

    def test_mixed_public_plus_static_is_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["public", "static"]) is True

    def test_only_non_exported_modifier_is_not_exported(self) -> None:
        from hypergumbo_core.analyze.base import is_exported_from_modifiers
        assert is_exported_from_modifiers(["static", "final"]) is False


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


# ---------------------------------------------------------------------------
# WI-gapam: emit_module_attribute_refs — cross-language helper for
# tree-sitter analyzers that produces module_attr_ref edges for attribute
# reads on imported/global modules (matches ``attributes:`` entries in
# io_primitives/*.yaml).
# ---------------------------------------------------------------------------


def _parse_javascript(source: str):
    """Parse JavaScript source with tree-sitter-javascript and return the root node."""
    import tree_sitter
    import tree_sitter_javascript
    lang = tree_sitter.Language(tree_sitter_javascript.language())
    parser = tree_sitter.Parser(lang)
    tree = parser.parse(source.encode("utf-8"))
    return tree.root_node


def _fake_caller(symbol_id: str = "javascript:app.js:1-5:handler:function") -> Symbol:
    """Construct a minimal Symbol to serve as the caller for edge emission."""
    return Symbol(
        id=symbol_id,
        name="handler",
        kind="function",
        language="javascript",
        path="app.js",
        span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
    )


class TestEmitModuleAttributeRefs:
    """Tests for the cross-language ``emit_module_attribute_refs`` helper (WI-gapam)."""

    def test_basic_member_expression_emits_edge(self) -> None:
        """``process.env`` in JavaScript emits a module_attr_ref edge with
        the expected (src, dst, type) triple. ``process`` is a global in
        Node.js so the ``imports`` map treats it as already-qualified.
        """
        root = _parse_javascript("const p = process.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert len(edges) == 1
        assert edges[0].edge_type == "module_attr_ref"
        assert edges[0].src == caller.id
        assert edges[0].dst == "javascript:process:0-0:process.env:attribute"
        assert edges[0].confidence == 0.85

    def test_skips_callee_of_call(self) -> None:
        """``process.exit(0)`` already produces a ``calls`` edge via the
        calls pipeline; the helper must NOT emit a redundant
        ``module_attr_ref`` for the callee ``process.exit``.
        """
        root = _parse_javascript("process.exit(0);\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"process.exit(0);\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_skips_unknown_base(self) -> None:
        """Attribute reads whose base is not in the imports map are
        skipped — the helper is conservative and only emits edges for
        recognised module names.
        """
        root = _parse_javascript("const x = foo.bar;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const x = foo.bar;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_nested_attribute_read_inner_still_emitted(self) -> None:
        """For ``process.env.PATH``, the inner ``process.env`` is itself a
        ``member_expression`` (the object of the outer access) — the
        helper emits edges for BOTH the inner and outer matches.  The
        outer ``.PATH`` has a ``member_expression`` as base rather than
        a plain identifier, so only the inner match resolves against
        the imports map.  The test documents the current-behaviour
        contract: one edge emitted, for ``process.env``.  (Downstream
        would-be ``module_attr_ref`` edges for the outer attribute
        require the caller to expand imports to cover intermediate
        chains, which is explicitly out of scope for the generic helper.)
        """
        root = _parse_javascript("const p = process.env.PATH;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env.PATH;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        # Exactly one edge: process.env (the outer .PATH's base is a
        # member_expression, not an identifier, so it does not match).
        dsts = [e.dst for e in edges]
        assert "javascript:process:0-0:process.env:attribute" in dsts
        assert len(edges) == 1

    def test_multiple_distinct_modules_all_emitted(self) -> None:
        """One pass emits edges for each imported-base match in the tree."""
        source = "const a = process.env;\nconst b = window.location;\n"
        root = _parse_javascript(source)
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"process": "process", "window": "window"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        dsts = {e.dst for e in edges}
        assert "javascript:process:0-0:process.env:attribute" in dsts
        assert "javascript:window:0-0:window.location:attribute" in dsts

    def test_alias_maps_to_real_module_name(self) -> None:
        """The imports map can rename a local alias to the real module
        name (the Python WI-guhok pattern: ``import os as o`` →
        imports={"o": "os"} → edge dst uses ``os.environ`` not
        ``o.environ``).
        """
        root = _parse_javascript("const x = p.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const x = p.env;\n",
            {"p": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert len(edges) == 1
        assert edges[0].dst == "javascript:process:0-0:process.env:attribute"

    def test_empty_imports_map_emits_nothing(self) -> None:
        """Degenerate case: no known modules → no edges, even if the
        source contains plenty of member expressions.
        """
        root = _parse_javascript("const p = process.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env;\n",
            {},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_unknown_node_kind_emits_nothing(self) -> None:
        """When ``node_kinds`` does not include any type present in the
        tree, the helper is a no-op.  Covers the guard-against-wrong-config
        path for languages whose attribute-access node has an unfamiliar
        name.
        """
        root = _parse_javascript("const p = process.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("never_matches_this",),
            object_field_names=("object",),
            property_field_names=("property",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_field_name_fallback_order(self) -> None:
        """``object_field_names`` / ``property_field_names`` are tried in
        order; if the first is absent, the helper falls back to the
        second.  Simulated here by passing a bogus first name and the
        real name second — the edge still emits.
        """
        root = _parse_javascript("const p = process.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("does_not_exist", "object"),
            property_field_names=("also_does_not_exist", "property"),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert len(edges) == 1

    def test_missing_field_names_skip_node(self) -> None:
        """If NONE of the configured field names resolves for a matching
        node, the helper quietly skips it — it does not raise.
        """
        root = _parse_javascript("const p = process.env;\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"const p = process.env;\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("does_not_exist",),
            property_field_names=("also_does_not_exist",),

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_call_kind_without_function_field_skipped(self) -> None:
        """A ``call_node_kinds`` entry whose node has neither of the
        configured ``call_function_field_names`` is ignored by the
        callee-detection pre-pass (covered via a custom config that
        names a real node type but a field name it doesn't carry).
        Functionally: process.exit(0) is NOT treated as a callee and
        the attribute access is emitted as a module_attr_ref.  This
        exercises the defensive ``if callee is None: continue`` branch.
        """
        root = _parse_javascript("process.exit(0);\n")
        caller = _fake_caller()
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            b"process.exit(0);\n",
            {"process": "process"},
            caller,
            "javascript",
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),
            # Neither "nonexistent_field" nor "also_nonexistent" exists
            # on a call_expression, so the callee resolution returns None
            # and the call is ignored.
            call_node_kinds=("call_expression",),
            call_function_field_names=(
                "nonexistent_field", "also_nonexistent",
            ),

            pass_id="test-pass",
            run_id="test-run",
        )
        # Fallback: without callee-detection, process.exit is treated
        # as a plain attribute read and emitted.
        assert len(edges) == 1
        assert edges[0].dst == "javascript:process:0-0:process.exit:attribute"


def _parse_rust(source: str):
    """Parse Rust source with tree-sitter-rust and return the root node."""
    import tree_sitter
    import tree_sitter_rust
    lang = tree_sitter.Language(tree_sitter_rust.language())
    parser = tree_sitter.Parser(lang)
    tree = parser.parse(source.encode("utf-8"))
    return tree.root_node


class TestEmitModuleAttributeRefsScopedPath:
    """WI-vipur: tests for ``scoped_path=True`` mode — the left-recursive
    walk over ``scoped_identifier`` / ``qualified_identifier`` nodes used
    by languages whose scoped access is not a binary object+property
    pair.  Exercised with tree-sitter-rust's ``scoped_identifier``."""

    def test_basic_scoped_path_emits_dot_normalized_edge(self) -> None:
        """``std::env::consts::OS`` emits edges for each scoped_identifier
        level.  The middle level is the interesting one: base walks
        left to ``std`` (in imports), so an edge with dst containing
        ``std.env.consts`` fires — dot-normalized to survive downstream
        ``:``-split parsing."""
        source = "pub fn f() -> &'static str { std::env::consts::OS }\n"
        root = _parse_rust(source)
        caller = _fake_caller("rust:lib.rs:0-0:file:file")
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"std": "std"},
            caller,
            "rust",
            edges,
            node_kinds=("scoped_identifier",),
            object_field_names=("path",),
            property_field_names=("name",),
            call_node_kinds=("call_expression",),
            call_function_field_names=("function",),
            scoped_path=True,

            pass_id="test-pass",
            run_id="test-run",
        )
        dsts = [e.dst for e in edges]
        assert any(
            d == "rust:std.env.consts:0-0:std.env.consts.OS:attribute"
            for d in dsts
        ), dsts

    def test_scoped_call_callee_is_skipped(self) -> None:
        """``std::env::var("HOME")`` — the outer scoped_identifier is
        the callee of a call_expression, so the helper skips it to
        avoid double-count with the ``calls`` pipeline."""
        source = "pub fn f() { std::env::var(\"HOME\"); }\n"
        root = _parse_rust(source)
        caller = _fake_caller("rust:lib.rs:0-0:file:file")
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"std": "std"},
            caller,
            "rust",
            edges,
            node_kinds=("scoped_identifier",),
            object_field_names=("path",),
            property_field_names=("name",),
            call_node_kinds=("call_expression",),
            call_function_field_names=("function",),
            scoped_path=True,

            pass_id="test-pass",
            run_id="test-run",
        )
        dsts = [e.dst for e in edges]
        assert not any("std.env.var" in d for d in dsts), dsts

    def test_unknown_leftmost_skipped(self) -> None:
        """A scoped path whose leftmost identifier is not in the
        imports map emits no edges."""
        source = "pub fn f() -> i32 { mymod::inner::VALUE }\n"
        root = _parse_rust(source)
        caller = _fake_caller("rust:lib.rs:0-0:file:file")
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"std": "std"},
            caller,
            "rust",
            edges,
            node_kinds=("scoped_identifier",),
            object_field_names=("path",),
            property_field_names=("name",),
            scoped_path=True,

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []

    def test_aliased_leftmost_rewrites_to_real_module(self) -> None:
        """Imports map ``env -> std::env`` — the helper rewrites the
        leftmost segment so ``env::consts::OS`` emits edges carrying
        ``std.env.consts`` in the dst (for catalog matching)."""
        source = "pub fn f() -> &'static str { env::consts::OS }\n"
        root = _parse_rust(source)
        caller = _fake_caller("rust:lib.rs:0-0:file:file")
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"env": "std::env"},
            caller,
            "rust",
            edges,
            node_kinds=("scoped_identifier",),
            object_field_names=("path",),
            property_field_names=("name",),
            scoped_path=True,

            pass_id="test-pass",
            run_id="test-run",
        )
        dsts = [e.dst for e in edges]
        assert any("std.env.consts" in d for d in dsts), dsts

    def test_missing_path_field_skips_node(self) -> None:
        """If ``object_field_names`` resolves to no child (the path
        field is absent from this node shape), the helper quietly
        skips the node — no raise, no emit."""
        source = "pub fn f() -> &'static str { std::env::consts::OS }\n"
        root = _parse_rust(source)
        caller = _fake_caller("rust:lib.rs:0-0:file:file")
        edges: list[Edge] = []
        emit_module_attribute_refs(
            root,
            source.encode("utf-8"),
            {"std": "std"},
            caller,
            "rust",
            edges,
            node_kinds=("scoped_identifier",),
            object_field_names=("does_not_exist_anywhere",),
            property_field_names=("name",),
            scoped_path=True,

            pass_id="test-pass",
            run_id="test-run",
        )
        assert edges == []


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


def _make_comment_node(
    text: str,
    node_type: str = "comment",
    start_line: int = 0,
    end_line: int | None = None,
    start_byte: int = 0,
    prev_named_sibling: object = None,
) -> MagicMock:
    """Create a mock tree-sitter comment node."""
    if end_line is None:
        end_line = start_line
    end_byte = start_byte + len(text.encode("utf-8"))
    node = MagicMock()
    node.type = node_type
    node.start_point = (start_line, 0)
    node.end_point = (end_line, 0)
    node.start_byte = start_byte
    node.end_byte = end_byte
    node.prev_named_sibling = prev_named_sibling
    return node


class TestExtractDocComment:
    """Tests for extract_doc_comment helper."""

    def test_no_comment(self) -> None:
        """Returns None when no comment precedes the node."""
        decl = MagicMock()
        decl.prev_named_sibling = None
        assert extract_doc_comment(decl, b"") is None

    def test_non_comment_sibling(self) -> None:
        """Returns None when preceding sibling is not a comment."""
        sibling = MagicMock()
        sibling.type = "function_declaration"
        decl = MagicMock()
        decl.prev_named_sibling = sibling
        assert extract_doc_comment(decl, b"") is None

    def test_block_comment_javadoc(self) -> None:
        """Extracts from /** Javadoc */ style block comment."""
        comment_text = b"/** Handles user authentication. */"
        cnode = _make_comment_node(
            comment_text.decode(),
            node_type="block_comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, comment_text)
        assert result == "Handles user authentication."

    def test_line_comment_rust_triple_slash(self) -> None:
        """Extracts from /// rustdoc style line comments."""
        source = b"/// Computes the hash value."
        cnode = _make_comment_node(
            source.decode(),
            node_type="line_comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source)
        assert result == "Computes the hash value."

    def test_go_double_slash(self) -> None:
        """Extracts from // Go doc comments."""
        source = b"// NewServer creates a new server."
        cnode = _make_comment_node(
            source.decode(),
            node_type="comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source)
        assert result == "NewServer creates a new server."

    def test_ruby_hash_comment(self) -> None:
        """Extracts from # Ruby doc comments."""
        source = b"# Validates the input data."
        cnode = _make_comment_node(
            source.decode(),
            node_type="comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source)
        assert result == "Validates the input data."

    def test_skips_tag_lines(self) -> None:
        """Skips @param, @returns etc and returns first content line."""
        source = b"/** @param x the value\n * Process data.\n */"
        cnode = _make_comment_node(
            source.decode(),
            node_type="block_comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source)
        assert result == "Process data."

    def test_truncates_long_lines(self) -> None:
        """Truncates lines longer than max_len."""
        long_text = "A" * 100
        source = f"// {long_text}".encode()
        cnode = _make_comment_node(
            source.decode(),
            node_type="comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source, max_len=80)
        assert result is not None
        assert len(result) == 80
        assert result.endswith("\u2026")

    def test_blank_line_gap_stops_collection(self) -> None:
        """Stops collecting when there is a blank line gap between comments."""
        # First comment on line 0, second on line 3 (gap > 1)
        source = b"// Unrelated comment.\n\n\n// Actual doc."
        # The "actual doc" node is closer to the declaration
        actual = _make_comment_node(
            "// Actual doc.",
            start_line=3,
            start_byte=len(b"// Unrelated comment.\n\n\n"),
        )
        actual.end_byte = len(source)
        unrelated = _make_comment_node(
            "// Unrelated comment.",
            start_line=0,
            start_byte=0,
        )
        unrelated.end_byte = len(b"// Unrelated comment.")
        # Link: decl -> actual -> unrelated
        actual.prev_named_sibling = unrelated
        unrelated.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = actual
        result = extract_doc_comment(decl, source)
        # Should get "Actual doc." not "Unrelated comment."
        assert result == "Actual doc."

    def test_comment_all_tags(self) -> None:
        """Returns None when comment contains only tag lines."""
        source = b"/** @param x val\n * @returns nothing\n */"
        cnode = _make_comment_node(
            source.decode(),
            node_type="block_comment",
            start_byte=0,
        )
        cnode.prev_named_sibling = None
        decl = MagicMock()
        decl.prev_named_sibling = cnode
        result = extract_doc_comment(decl, source)
        assert result is None

    def test_no_prev_named_sibling_attr(self) -> None:
        """Handles nodes without prev_named_sibling attribute (MockNode)."""
        decl = MagicMock(spec=[])  # No attributes
        result = extract_doc_comment(decl, b"")
        assert result is None


class TestPopulateDocstringsFromTree:
    """Tests for populate_docstrings_from_tree position-based docstring lookup."""

    @staticmethod
    def _make_sym(
        start_line: int,
        start_col: int,
        docstring: str | None = None,
        span: Span | None = ...,  # type: ignore[assignment]
    ) -> Symbol:
        """Create a minimal Symbol for testing."""
        if span is ...:
            span = Span(start_line=start_line, start_col=start_col,
                        end_line=start_line, end_col=start_col + 10)
        return Symbol(
            id=f"test:file:{start_line}-{start_line}:func:function",
            name="func",
            kind="function",
            language="test",
            path="file.py",
            span=span,
            origin="test-pass",
            origin_run_id="run-1",
            docstring=docstring,
        )

    def test_basic_extraction(self) -> None:
        """Populates docstring when a comment precedes the declaration node."""
        source = b"// Computes the hash.\ndef func() {}"
        # Comment on line 0, function on line 1 col 0
        comment_node = _make_comment_node(
            "// Computes the hash.",
            node_type="comment",
            start_byte=0,
        )
        comment_node.end_byte = 21
        comment_node.prev_named_sibling = None

        func_node = MagicMock()
        func_node.prev_named_sibling = comment_node
        func_node.start_point = (1, 0)
        func_node.end_point = (1, 13)

        root = MagicMock()
        root.named_descendant_for_point_range.return_value = func_node

        sym = self._make_sym(start_line=2, start_col=0)
        populate_docstrings_from_tree(root, source, [sym])
        assert sym.docstring == "Computes the hash."

    def test_skips_existing_docstring(self) -> None:
        """Does not overwrite a symbol that already has a docstring."""
        root = MagicMock()
        sym = self._make_sym(start_line=2, start_col=0, docstring="Existing.")
        populate_docstrings_from_tree(root, b"", [sym])
        assert sym.docstring == "Existing."
        root.named_descendant_for_point_range.assert_not_called()

    def test_skips_none_span(self) -> None:
        """Gracefully skips symbols with span=None."""
        root = MagicMock()
        sym = self._make_sym(start_line=1, start_col=0, span=None)
        populate_docstrings_from_tree(root, b"", [sym])
        assert sym.docstring is None
        root.named_descendant_for_point_range.assert_not_called()

    def test_no_comment_returns_none(self) -> None:
        """Symbol without preceding comment gets no docstring."""
        func_node = MagicMock()
        func_node.prev_named_sibling = None

        root = MagicMock()
        root.named_descendant_for_point_range.return_value = func_node

        sym = self._make_sym(start_line=1, start_col=0)
        populate_docstrings_from_tree(root, b"def func(): pass", [sym])
        assert sym.docstring is None

    def test_multiple_symbols(self) -> None:
        """Multiple symbols: only those with doc comments get docstrings."""
        source = b"// Documented.\ndef a() {}\ndef b() {}"
        comment_node = _make_comment_node(
            "// Documented.",
            node_type="comment",
            start_byte=0,
        )
        comment_node.end_byte = 14
        comment_node.prev_named_sibling = None

        node_a = MagicMock()
        node_a.prev_named_sibling = comment_node
        node_a.start_point = (1, 0)
        node_a.end_point = (1, 10)

        node_b = MagicMock()
        node_b.prev_named_sibling = None
        node_b.start_point = (2, 0)
        node_b.end_point = (2, 10)

        root = MagicMock()
        root.named_descendant_for_point_range.side_effect = (
            lambda s, e: node_a if s == (0, 0) else node_b
        )

        sym_a = self._make_sym(start_line=1, start_col=0)
        sym_b = self._make_sym(start_line=2, start_col=0)
        sym_b.id = "test:file:2-2:b:function"
        populate_docstrings_from_tree(root, source, [sym_a, sym_b])
        assert sym_a.docstring == "Documented."
        assert sym_b.docstring is None

    def test_node_not_found(self) -> None:
        """When named_descendant_for_point_range returns None, skips gracefully."""
        root = MagicMock()
        root.named_descendant_for_point_range.return_value = None

        sym = self._make_sym(start_line=1, start_col=0)
        populate_docstrings_from_tree(root, b"some code", [sym])
        assert sym.docstring is None


class TestMakeUnresolvedEdge:
    """Tests for make_unresolved_edge — base + INV-nilud hint kwargs."""

    # WI-gifar (PR-1 of INV-nilud inherited_calls campaign): the three
    # _hint_ kwargs below let analyzers attach Edge.meta entries that the
    # upcoming inherited_calls linker can walk ancestor chains against.

    def test_creates_correct_edge(self) -> None:
        """Creates an Edge with correct format and confidence."""
        from hypergumbo_core.analyze.base import make_unresolved_edge

        edge = make_unresolved_edge(
            "c", "c:main.c:1-5:main:function", "fopen",
            3, "c-pass", "run-123",
        )
        assert edge.dst == "c:external:0-0:fopen:unresolved"
        assert edge.edge_type == "calls"
        assert edge.confidence == 0.50
        assert edge.evidence_type == "ast_call_direct"
        assert edge.is_resolved is False
        assert edge.origin == ["c-pass"]
        assert edge.origin_run_id == "run-123"
        assert edge.line == 3

    def test_custom_module_hint(self) -> None:
        """Uses module_hint instead of 'external' when provided."""
        from hypergumbo_core.analyze.base import make_unresolved_edge

        edge = make_unresolved_edge(
            "java", "java:IO.java:1-5:readFile:method", "read",
            10, "java-pass", "run-456",
            module_hint="java.io.InputStream",
        )
        assert edge.dst == "java:java.io.InputStream:0-0:read:unresolved"

    def _call(self, **overrides) -> object:
        kwargs = {
            "lang": "java",
            "src_id": "java:/app/Foo.java:1-5:Foo.bar:method",
            "callee_name": "baz",
            "line": 12,
            "pass_id": "java:1.0",
            "run_id": "run-xyz",
        }
        kwargs.update(overrides)
        return make_unresolved_edge(**kwargs)

    def test_default_edge_shape_is_unchanged(self) -> None:
        edge = self._call()
        assert edge.edge_type == "calls"
        assert edge.evidence_type == "ast_call_direct"
        assert edge.confidence == 0.50
        assert edge.is_resolved is False
        assert edge.dst == "java:external:0-0:baz:unresolved"
        assert edge.line == 12

    def test_no_hints_yields_no_meta(self) -> None:
        """Backward compat: omitting the new kwargs leaves Edge.meta as None."""
        edge = self._call()
        assert edge.meta is None

    def test_enclosing_class_hint_lands_in_meta(self) -> None:
        edge = self._call(enclosing_class="Foo")
        assert edge.meta == {"enclosing_class": "Foo"}

    def test_receiver_type_hint_lands_in_meta(self) -> None:
        edge = self._call(receiver_type_hint="Bar")
        assert edge.meta == {"receiver_type_hint": "Bar"}

    def test_inherited_field_receiver_lands_in_meta(self) -> None:
        edge = self._call(inherited_field_receiver="self.helper")
        assert edge.meta == {"inherited_field_receiver": "self.helper"}

    def test_all_three_hints_combined(self) -> None:
        edge = self._call(
            enclosing_class="Foo",
            receiver_type_hint="Bar",
            inherited_field_receiver="self.helper",
        )
        assert edge.meta == {
            "enclosing_class": "Foo",
            "receiver_type_hint": "Bar",
            "inherited_field_receiver": "self.helper",
        }

    def test_module_hint_kwarg_still_works(self) -> None:
        """module_hint must remain reachable alongside the new kwargs."""
        edge = self._call(module_hint="java.util", enclosing_class="Foo")
        assert edge.dst == "java:java.util:0-0:baz:unresolved"
        assert edge.meta == {"enclosing_class": "Foo"}


def _make_caller_symbol(
    sym_id: str = "python:src/main.py:10-20:foo:function",
    language: str = "python",
    path: str = "src/main.py",
) -> Symbol:
    return Symbol(
        id=sym_id,
        name="foo",
        kind="function",
        language=language,
        path=path,
        span=Span(start_line=10, start_col=0, end_line=20, end_col=0),
    )


class TestSynthesizeFileSymbolsForDanglingEdges:
    """Tests for WI-ramuv synthesize_file_symbols_for_dangling_edges."""

    def test_synthesizes_for_dangling_src(self) -> None:
        """A make_file_id-shape src with no matching Symbol gets one synthesized."""
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=make_file_id("python", "src/main.py"),
            dst=caller.id,
            edge_type="imports",
            line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])

        assert len(new_syms) == 1
        sym = new_syms[0]
        assert sym.id == "python:src/main.py:1-1:file:file"
        assert sym.kind == "file"
        assert sym.name == "src/main.py"
        assert sym.language == "python"
        assert sym.path == "src/main.py"
        assert sym.span.start_line == 1
        assert sym.span.end_line == 1
        assert sym.origin == ["orchestrator_file_symbol_synthesis"]

    def test_stamps_provided_origin_run_id(self) -> None:
        """synthetic:F1: a provided ``origin_run_id`` is stamped onto each
        synthesized file Symbol so the node->AnalysisRun JOIN
        (``origin_run_id`` -> ``execution_id``) resolves. ``origin`` stays the
        synthesis-mechanism pass-id."""
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=make_file_id("python", "src/main.py"),
            dst=caller.id,
            edge_type="imports",
            line=1,
            origin="test", origin_run_id="test",
        )
        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller], [edge], origin_run_id="uuid:file-synth-run",
        )
        assert len(new_syms) == 1
        assert new_syms[0].origin == ["orchestrator_file_symbol_synthesis"]
        assert new_syms[0].origin_run_id == "uuid:file-synth-run"

    def test_origin_run_id_defaults_empty(self) -> None:
        """The ``origin_run_id`` kwarg defaults to '' so pre-F1 call sites are
        unaffected; the orchestrator supplies the real execution_id."""
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=make_file_id("python", "src/main.py"), dst=caller.id,
            edge_type="imports", line=1, origin="test", origin_run_id="test",
        )
        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])
        assert new_syms[0].origin_run_id == ""
        # origin is always the synthesis mechanism (distinguishes the fixed code
        # from a regression that drops the origin stamp).
        assert new_syms[0].origin == ["orchestrator_file_symbol_synthesis"]

    def test_synthesizes_for_dangling_dst(self) -> None:
        """make_file_id-shape on the dst side is also covered."""
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=caller.id,
            dst=make_file_id("typescript", "lib/utils.ts"),
            edge_type="imports",
            line=5,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])

        assert len(new_syms) == 1
        assert new_syms[0].language == "typescript"
        assert new_syms[0].path == "lib/utils.ts"

    def test_skips_when_symbol_already_exists(self) -> None:
        """If an analyzer already emits a matching file Symbol, do not duplicate it."""
        existing = Symbol(
            id=make_file_id("html", "index.html"),
            name="index.html",
            kind="file",
            language="html",
            path="index.html",
            span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
        )
        edge = Edge.create(
            src=existing.id,
            dst="html:index.html:5-10:button:tag",
            edge_type="contains",
            line=5,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([existing], [edge])

        assert new_syms == []

    def test_dedupes_repeated_dangling_endpoints(self) -> None:
        """Multiple edges referencing the same dangling file id produce one Symbol."""
        caller_a = _make_caller_symbol("python:a.py:1-3:a:function", path="a.py")
        caller_b = _make_caller_symbol("python:b.py:1-3:b:function", path="b.py")
        file_id = make_file_id("python", "common.py")
        edges = [
            Edge.create(src=file_id, dst=caller_a.id, edge_type="imports", line=1, origin="test", origin_run_id="test"),
            Edge.create(src=file_id, dst=caller_b.id, edge_type="imports", line=1, origin="test", origin_run_id="test"),
        ]

        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller_a, caller_b], edges
        )

        assert len(new_syms) == 1
        assert new_syms[0].id == file_id

    def test_ignores_non_file_id_endpoints(self) -> None:
        """Endpoints that are not make_file_id-shape are left alone."""
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=caller.id,
            # An unresolved-external dst, not a file-id shape.
            dst="python:requests:0-0:get:unresolved",
            edge_type="calls",
            line=12,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])

        assert new_syms == []

    def test_preserves_colons_in_path(self) -> None:
        """Languages whose ``path`` slot contains colons (e.g. Dart ``dart:io``) parse correctly."""
        caller = _make_caller_symbol(
            "dart:lib/app.dart:1-3:main:function",
            language="dart",
            path="lib/app.dart",
        )
        # Dart's import-edge src for the dart:io stdlib module looks like
        # "dart:dart:io:1-1:file:file" — the path slot is "dart:io".
        file_id = "dart:dart:io:1-1:file:file"
        edge = Edge.create(
            src=file_id, dst=caller.id, edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])

        assert len(new_syms) == 1
        assert new_syms[0].language == "dart"
        assert new_syms[0].path == "dart:io"
        assert new_syms[0].id == file_id

    def test_returns_empty_when_no_dangling(self) -> None:
        """No edges → no synthesis."""
        assert synthesize_file_symbols_for_dangling_edges([], []) == []


class TestSynthesizeFileSymbolsHonorIdentity:
    """INV-vaguj: file-Symbol identity (``name`` and ``span``) must be honest.

    ``orchestrator_file_symbol_synthesis`` historically stamped two
    correlated lies into its file-kind Symbols: ``name`` reflected whatever
    path was packed into the dangling endpoint id (often absolute from an
    analyzer that hadn't normalised yet), and ``span`` was hardcoded
    ``start_line=1, end_line=1`` regardless of the file's actual extent.

    The fix takes a ``repo_root`` argument:

    1. **name / path normalisation** — paths under ``repo_root`` strip to
       repo-relative form, so a 3179-line ``packages/.../cli.py`` no longer
       ships as ``name="/home/.../cli.py"`` with ``path="packages/.../cli.py"``.
    2. **span end_line** — when the file is readable under ``repo_root``,
       ``end_line`` becomes the actual newline count. Unreadable files
       keep ``end_line=1`` (a valid value matching the schema, not the
       invariant-violating sentinel ``-1`` the tracker originally proposed).

    The legacy (``repo_root=None``) call form preserves the prior shape,
    so the older ``TestSynthesizeFileSymbolsForDanglingEdges`` cases that
    pre-date this fix continue to pass without modification.
    """

    def test_absolute_path_in_endpoint_normalises_to_relative(
        self, tmp_path: Path
    ) -> None:
        """An endpoint built with an absolute path lands as relative ``name``+``path``."""
        # Simulate an analyzer that emitted an absolute path in its file-id.
        target = tmp_path / "src" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('hi')\n")
        abs_file_id = make_file_id("python", str(target))
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=abs_file_id, dst=caller.id, edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller], [edge], repo_root=tmp_path,
        )

        assert len(new_syms) == 1
        sym = new_syms[0]
        assert sym.path == "src/main.py", (
            f"path should be repo-relative, got {sym.path!r}"
        )
        assert sym.name == "src/main.py", (
            f"name should mirror relative path, got {sym.name!r}"
        )
        assert not Path(sym.name).is_absolute(), (
            f"name {sym.name!r} must not be an absolute filesystem path"
        )

    def test_span_end_line_matches_file_line_count(
        self, tmp_path: Path
    ) -> None:
        """``span.end_line`` reflects the file's real extent, not a hardcoded 1."""
        target = tmp_path / "long.py"
        target.write_text("\n".join(f"line {i}" for i in range(1, 43)) + "\n")
        # 42 lines.
        file_id = make_file_id("python", str(target))
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=file_id, dst=caller.id, edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller], [edge], repo_root=tmp_path,
        )

        sym = new_syms[0]
        assert sym.span.start_line == 1
        assert sym.span.end_line == 42, (
            f"end_line should be the file's line count (42), got {sym.span.end_line}"
        )

    def test_unreadable_file_keeps_default_end_line(
        self, tmp_path: Path
    ) -> None:
        """File missing under ``repo_root`` → ``end_line`` stays at 1 (schema-valid sentinel)."""
        # No file at this path — synthesiser still must produce something.
        file_id = make_file_id("python", "missing/ghost.py")
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=file_id, dst=caller.id, edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller], [edge], repo_root=tmp_path,
        )

        sym = new_syms[0]
        assert sym.span.end_line == 1
        # Schema requires end_line >= 0; -1 would violate INV-piroh's gate.
        assert sym.span.end_line >= 0

    def test_repo_root_none_preserves_legacy_behaviour(self) -> None:
        """Existing call sites without ``repo_root`` keep the pre-fix shape.

        Backwards compat for tests / consumers that never had repo_root in
        scope. New orchestrator code path passes it; old call sites continue
        to produce hardcoded 1-1 spans with name=path. This preserves the
        existing six tests above without forcing a rewrite cascade.
        """
        caller = _make_caller_symbol()
        edge = Edge.create(
            src=make_file_id("python", "src/main.py"),
            dst=caller.id,
            edge_type="imports",
            line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges([caller], [edge])

        assert new_syms[0].name == "src/main.py"
        assert new_syms[0].path == "src/main.py"
        assert new_syms[0].span.end_line == 1

    def test_dart_colon_path_unchanged_under_repo_root(
        self, tmp_path: Path
    ) -> None:
        """Synthetic dart paths (``dart:io``) aren't filesystem paths; they pass through."""
        # `dart:io` is not under repo_root and is not a real path; the
        # synthesiser must not break it by trying to relativise.
        caller = _make_caller_symbol(
            "dart:lib/app.dart:1-3:main:function",
            language="dart",
            path="lib/app.dart",
        )
        file_id = "dart:dart:io:1-1:file:file"
        edge = Edge.create(
            src=file_id, dst=caller.id, edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )

        new_syms = synthesize_file_symbols_for_dangling_edges(
            [caller], [edge], repo_root=tmp_path,
        )

        assert new_syms[0].path == "dart:io"
        assert new_syms[0].name == "dart:io"
        assert new_syms[0].span.end_line == 1  # no file to count


class TestPopulateSyntheticClassBIdentity:
    """synthetic:F2 (5a): the ``populate_synthetic_class_b_identity`` post-pass
    backstops ``stable_id`` / ``display_label`` / ``fingerprint`` on Class-B
    synthetic protocol-synth Symbols (``language is None AND protocol_origin``
    set) that linkers leave null — identity-neutrally (per-field skip-if-set,
    so it never overrides an existing value). Closes META-huvuh's producer
    half (display_label absent on Class-B). LOGICAL kinds hash by name;
    SITE kinds (``call_site``) are occurrence-indexed, line out of the hash
    (ADR-0035 §3). Never touches supply_chain_* (WI-lidig)."""

    def _class_b(self, **kw):
        from hypergumbo_core.ir import Symbol, Span

        defaults = {
            "id": "javascript:app.js:5-5:ipcrequestchan:function",
            "name": "ipc:request:chan",
            "kind": "function",
            "language": None,
            "path": "app.js",
            "span": Span(start_line=5, end_line=5, start_col=0, end_col=0),
            "protocol_origin": "ipc",
            "discovery_language": "javascript",
            "origin": ["ipc-linker"],
            "origin_run_id": "uuid:run",
        }
        defaults.update(kw)
        return Symbol(**defaults)

    def test_logical_class_b_gets_identity_and_provenance_display(self) -> None:
        import hashlib
        from hypergumbo_core.analyze.base import (
            populate_synthetic_class_b_identity, make_protocol_stable_id,
        )

        s = self._class_b()
        populate_synthetic_class_b_identity([s])
        # Injective key (protocol_origin, kind, path, name, occurrence).
        assert s.stable_id == make_protocol_stable_id(
            "ipc", "function", "app.js", "ipc:request:chan", "0",
        )
        assert s.display_label == "ipc:request:chan"
        assert s.fingerprint == hashlib.sha256(s.id.encode()).hexdigest()[:16]

    def test_same_key_siblings_are_occurrence_indexed_and_line_independent(self) -> None:
        from hypergumbo_core.ir import Span
        from hypergumbo_core.analyze.base import (
            populate_synthetic_class_b_identity, make_protocol_stable_id,
        )

        # Two ABI call sites to the same function in the same file share
        # (protocol_origin, kind, path, name) → the occurrence index separates
        # them, with NO line number in the hash (ADR-0035 §3). Occurrence order
        # follows span, so the line-10 site is occurrence 0 regardless of list
        # order.
        a = self._class_b(
            id="typescript:c.ts:10-10:transfer:call_site",
            name="transfer", kind="call_site", protocol_origin="solidity_abi",
            discovery_language="typescript", path="c.ts",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=0),
        )
        b = self._class_b(
            id="typescript:c.ts:20-20:transfer:call_site",
            name="transfer", kind="call_site", protocol_origin="solidity_abi",
            discovery_language="typescript", path="c.ts",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=0),
        )
        populate_synthetic_class_b_identity([b, a])  # list order reversed: order is span-driven
        assert a.stable_id != b.stable_id
        assert a.stable_id == make_protocol_stable_id(
            "solidity_abi", "call_site", "c.ts", "transfer", "0",
        )
        assert b.stable_id == make_protocol_stable_id(
            "solidity_abi", "call_site", "c.ts", "transfer", "1",
        )

    def test_distinct_nodes_never_share_stable_id(self) -> None:
        """ADR-0035 §1 zero-by-design-collisions regression guard: distinct
        Class-B nodes must get distinct stable_ids. The coarse first-cut key
        (protocol_origin, name) collided (a) an @objc definition with a
        #selector reference to the same name [kind separates them now] and
        (b) a CRDT writer with a same-channel observer [occurrence separates
        the role-distinct same-name, same-kind siblings]."""
        from hypergumbo_core.ir import Span
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        defn = self._class_b(
            id="swift:A.swift:3-3:doThing:function", name="doThing",
            kind="function", protocol_origin="objc_bridge",
            discovery_language="swift", path="A.swift",
        )
        ref = self._class_b(
            id="swift:A.swift:9-9:doThing:reference", name="doThing",
            kind="reference", protocol_origin="objc_bridge",
            discovery_language="swift", path="A.swift",
        )
        pub = self._class_b(
            id="typescript:y.ts:10-10:doc:function", name="doc",
            kind="function", protocol_origin="yjs_crdt",
            discovery_language="typescript", path="y.ts",
            span=Span(start_line=10, end_line=10, start_col=0, end_col=0),
        )
        sub = self._class_b(
            id="typescript:y.ts:20-20:doc:function", name="doc",
            kind="function", protocol_origin="yjs_crdt",
            discovery_language="typescript", path="y.ts",
            span=Span(start_line=20, end_line=20, start_col=0, end_col=0),
        )
        populate_synthetic_class_b_identity([defn, ref, pub, sub])
        ids = [defn.stable_id, ref.stable_id, pub.stable_id, sub.stable_id]
        assert len(set(ids)) == 4, f"by-design collision among distinct nodes: {ids}"

    def test_skip_if_set_preserves_existing_values_byte_for_byte(self) -> None:
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        s = self._class_b(
            stable_id="sha256:deadbeefdeadbeef",
            display_label="prebuilt-label",
            fingerprint="cafebabecafebabe",
        )
        populate_synthetic_class_b_identity([s])
        assert s.stable_id == "sha256:deadbeefdeadbeef"
        assert s.display_label == "prebuilt-label"
        assert s.fingerprint == "cafebabecafebabe"

    def test_non_class_b_symbols_are_untouched(self) -> None:
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        # Class-A real-source symbol (language set, no protocol_origin) and a
        # Class-B-shaped symbol missing protocol_origin: neither is in scope.
        class_a = Symbol(
            id="python:m.py:1-2:foo:function", name="foo", kind="function",
            language="python", path="m.py",
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        )
        no_proto = Symbol(
            id="x:y:0-0:z:external_symbol", name="z", kind="external_symbol",
            language=None, path="<external>",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
        )
        populate_synthetic_class_b_identity([class_a, no_proto])
        assert class_a.stable_id is None
        assert class_a.display_label is None
        assert no_proto.stable_id is None
        assert no_proto.display_label is None

    def test_only_none_to_value_deltas_identity_neutral(self) -> None:
        """The post-pass must never change an existing stable_id/fingerprint/
        display_label VALUE — only fill nulls. Snapshot/compare."""
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        gap = self._class_b()
        stamped = self._class_b(
            id="javascript:b.js:1-1:mq:function", name="mq:topic",
            stable_id="sha256:aaaabbbbccccdddd", display_label="kept",
            fingerprint="1111222233334444",
        )
        before = {
            id(x): (x.stable_id, x.shape_id, x.fingerprint, x.display_label, x.id)
            for x in (gap, stamped)
        }
        populate_synthetic_class_b_identity([gap, stamped])
        # stamped: all fields byte-identical.
        assert (stamped.stable_id, stamped.fingerprint, stamped.display_label) == (
            "sha256:aaaabbbbccccdddd", "1111222233334444", "kept",
        )
        # gap: positive half — the null fields actually transitioned to values
        # (a genuine revert detector: fails if the stamping body is neutered).
        assert before[id(gap)][0] is None and gap.stable_id is not None
        assert before[id(gap)][2] is None and gap.fingerprint is not None
        assert before[id(gap)][3] is None and gap.display_label is not None
        # gap: only None->value transitions; id and shape_id never written.
        assert before[id(gap)][4] == gap.id  # id untouched
        assert gap.shape_id is None  # shape_id never written

    def test_preserves_supply_chain_fields_wi_lidig(self) -> None:
        """WI-lidig: the post-pass must NOT stamp supply_chain_reason/tier at
        mint (the annotator re-classifies tier==1 AND reason=='' nodes)."""
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        s = self._class_b(supply_chain_tier=1, supply_chain_reason="")
        populate_synthetic_class_b_identity([s])
        assert s.supply_chain_tier == 1
        assert s.supply_chain_reason == ""


class TestMakeDocStableId:
    """make_doc_stable_id (id-format:F2 4a): canonical, span/kind-disambiguated
    stable identity for documentation/config noise-node Symbols."""

    def test_canonical_sha256_shape(self) -> None:
        import re
        sid = make_doc_stable_id("markdown", "README.md", "section", "Intro", 1, 2)
        assert re.match(r"^sha256:[0-9a-f]{16}$", sid), sid

    def test_span_disambiguates_same_name_and_kind(self) -> None:
        # INV-dubam/INV-tazaj guard: two same-name same-kind nodes at different
        # spans (anonymous code blocks all named "code"; repeated headings) must
        # NOT collapse to one stable_id.
        a = make_doc_stable_id("markdown", "R.md", "code_block", "code:text", 5, 7)
        b = make_doc_stable_id("markdown", "R.md", "code_block", "code:text", 10, 12)
        assert a != b

    def test_each_component_changes_the_hash(self) -> None:
        base = make_doc_stable_id("markdown", "R.md", "section", "X", 1, 2)
        assert make_doc_stable_id("rst", "R.md", "section", "X", 1, 2) != base
        assert make_doc_stable_id("markdown", "Q.md", "section", "X", 1, 2) != base
        assert make_doc_stable_id("markdown", "R.md", "link", "X", 1, 2) != base
        assert make_doc_stable_id("markdown", "R.md", "section", "Y", 1, 2) != base
        assert make_doc_stable_id("markdown", "R.md", "section", "X", 9, 2) != base
        assert make_doc_stable_id("markdown", "R.md", "section", "X", 1, 9) != base

    def test_deterministic(self) -> None:
        assert make_doc_stable_id("markdown", "R.md", "section", "X", 1, 2) == \
            make_doc_stable_id("markdown", "R.md", "section", "X", 1, 2)
