"""Tests for the slice module (graph slicing for LLM context)."""
from typing import List


import pytest

from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.slice import (
    slice_graph,
    SliceQuery,
    find_entry_nodes,
    AmbiguousEntryError,
    rank_slice_nodes,
    SliceResult,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    language: str = "python",
) -> Symbol:
    """Helper to create test symbols."""
    span = Span(start_line=start_line, end_line=end_line, start_col=0, end_col=10)
    sym_id = f"{language}:{path}:{start_line}-{end_line}:{name}:{kind}"
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=span,
        origin=f"{language}-ast-v1",
        origin_run_id="uuid:test",
    )


def make_edge(
    src: Symbol,
    dst: Symbol,
    edge_type: str = "calls",
    confidence: float = 0.85,
) -> Edge:
    """Helper to create test edges."""
    return Edge.create(
        src=src.id,
        dst=dst.id,
        edge_type=edge_type,
        line=src.span.start_line,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
        confidence=confidence,
    )


class TestFindEntryNodes:
    """Tests for finding entry nodes by various match criteria."""

    def test_find_by_exact_name(self) -> None:
        """Match entry by exact function name."""
        sym_a = make_symbol("login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_partial_name(self) -> None:
        """Match entry by partial name (contains)."""
        sym_a = make_symbol("user_login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_file_path(self) -> None:
        """Match entry by file path."""
        sym_a = make_symbol("func_a", path="src/auth.py")
        sym_b = make_symbol("func_b", path="src/db.py")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "src/auth.py")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_node_id(self) -> None:
        """Match entry by exact node ID."""
        sym_a = make_symbol("login", path="src/auth.py", start_line=10, end_line=20)
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, sym_a.id)

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_multiple_matches(self) -> None:
        """Multiple nodes can match the same entry spec."""
        sym_a = make_symbol("handle_login")
        sym_b = make_symbol("login_user")
        sym_c = make_symbol("logout")
        nodes = [sym_a, sym_b, sym_c]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 2
        ids = {m.id for m in matches}
        assert sym_a.id in ids
        assert sym_b.id in ids

    def test_find_no_match(self) -> None:
        """Returns empty list when no match found."""
        sym_a = make_symbol("login")
        nodes = [sym_a]

        matches = find_entry_nodes(nodes, "nonexistent")

        assert matches == []

    def test_find_with_language_filter(self) -> None:
        """Language filter restricts matches to specified language."""
        py_main = make_symbol("main", path="src/app.py", language="python")
        js_main = make_symbol("main", path="src/index.js", language="javascript")
        nodes = [py_main, js_main]

        # Without filter: both match
        matches = find_entry_nodes(nodes, "main")
        assert len(matches) == 2

        # With python filter: only Python matches
        matches = find_entry_nodes(nodes, "main", language="python")
        assert len(matches) == 1
        assert matches[0].language == "python"

        # With javascript filter: only JS matches
        matches = find_entry_nodes(nodes, "main", language="javascript")
        assert len(matches) == 1
        assert matches[0].language == "javascript"


class TestAmbiguousEntryDetection:
    """Tests for detecting and reporting ambiguous entry points."""

    def test_raises_error_when_same_name_in_different_files(self) -> None:
        """Should raise AmbiguousEntryError when name matches symbols in different files."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20)

        with pytest.raises(AmbiguousEntryError) as exc_info:
            slice_graph(nodes, edges, query)

        # Error message should include helpful information
        error = exc_info.value
        assert "ping" in str(error)
        assert len(error.candidates) == 2
        assert py_ping.id in [c.id for c in error.candidates]
        assert ts_ping.id in [c.id for c in error.candidates]

    def test_no_error_when_exact_id_used(self) -> None:
        """Should not raise error when entry is specified by exact node ID."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        # Use exact ID - should work without ambiguity
        query = SliceQuery(entrypoint=py_ping.id, max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert py_ping.id in result.node_ids
        assert ts_ping.id not in result.node_ids

    def test_no_error_when_language_filter_used(self) -> None:
        """Language filter avoids ambiguity by restricting to one language."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        # Use language filter - should not be ambiguous
        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20, language="python")
        result = slice_graph(nodes, edges, query)

        assert py_ping.id in result.node_ids
        assert ts_ping.id not in result.node_ids

        # Language should be in the query dict
        query_dict = query.to_dict()
        assert query_dict["language"] == "python"

    def test_no_error_when_same_name_same_file(self) -> None:
        """Multiple matches in same file are OK (e.g., overloads or nested)."""
        func1 = make_symbol("handler", path="src/app.py", start_line=1, end_line=5)
        func2 = make_symbol("handler", path="src/app.py", start_line=10, end_line=15)
        nodes = [func1, func2]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="handler", max_hops=3, max_files=20)
        # Should not raise - same file is fine
        result = slice_graph(nodes, edges, query)

        # Both should be in the result
        assert func1.id in result.node_ids
        assert func2.id in result.node_ids

    def test_error_message_includes_file_paths(self) -> None:
        """Error message should help user disambiguate by showing paths."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        go_ping = make_symbol("ping", path="cmd/server.go", language="go")
        nodes = [py_ping, ts_ping, go_ping]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20)

        with pytest.raises(AmbiguousEntryError) as exc_info:
            slice_graph(nodes, edges, query)

        error_msg = str(exc_info.value)
        assert "src/app.py" in error_msg
        assert "web/client.ts" in error_msg
        assert "cmd/server.go" in error_msg


class TestSliceGraph:
    """Tests for BFS graph slicing."""

    def test_slice_single_node_no_edges(self) -> None:
        """Slice from a node with no outgoing edges."""
        sym_a = make_symbol("isolated")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="isolated", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        assert len(result.edge_ids) == 0
        assert result.limits_hit == []

    def test_slice_follows_calls(self) -> None:
        """Slice follows call edges."""
        sym_a = make_symbol("caller", start_line=1, end_line=5)
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b, "calls")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert len(result.edge_ids) == 1
        assert edge.id in result.edge_ids

    def test_slice_follows_imports(self) -> None:
        """Slice follows import edges."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        edge = make_edge(sym_a, sym_b, "imports")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="main", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_b.id in result.node_ids

    def test_slice_respects_hop_limit(self) -> None:
        """Slice stops at max_hops depth."""
        # Create chain: a -> b -> c -> d
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)
        sym_d = make_symbol("d", start_line=7, end_line=8)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)
        edge_cd = make_edge(sym_c, sym_d)

        nodes = [sym_a, sym_b, sym_c, sym_d]
        edges = [edge_ab, edge_bc, edge_cd]

        query = SliceQuery(entrypoint="a", max_hops=2, max_files=20)
        result = slice_graph(nodes, edges, query)

        # With max_hops=2: a (hop 0) -> b (hop 1) -> c (hop 2), but NOT d
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id in result.node_ids
        assert sym_d.id not in result.node_ids
        assert "hop_limit" in result.limits_hit

    def test_slice_respects_file_limit(self) -> None:
        """Slice stops when max_files is reached."""
        # Create nodes in different files
        sym_a = make_symbol("a", path="file1.py")
        sym_b = make_symbol("b", path="file2.py")
        sym_c = make_symbol("c", path="file3.py")

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=2)
        result = slice_graph(nodes, edges, query)

        # Should only include nodes from 2 files
        files_in_result = {n.split(":")[1] for n in result.node_ids}
        assert len(files_in_result) <= 2
        assert "file_limit" in result.limits_hit

    def test_slice_filters_low_confidence(self) -> None:
        """Slice can exclude edges below confidence threshold."""
        sym_a = make_symbol("caller", start_line=1, end_line=2)
        sym_b = make_symbol("callee_high", start_line=3, end_line=4)
        sym_c = make_symbol("callee_low", start_line=5, end_line=6)

        edge_high = make_edge(sym_a, sym_b, confidence=0.90)
        edge_low = make_edge(sym_a, sym_c, confidence=0.40)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_high, edge_low]

        query = SliceQuery(
            entrypoint="caller",
            max_hops=3,
            max_files=20,
            min_confidence=0.50,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id not in result.node_ids
        assert edge_high.id in result.edge_ids
        assert edge_low.id not in result.edge_ids

    def test_slice_excludes_tests(self) -> None:
        """Slice can exclude test files."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        sym_test = make_symbol("test_main", path="tests/test_main.py")

        edge_to_helper = make_edge(sym_a, sym_b)
        edge_to_test = make_edge(sym_a, sym_test)

        nodes = [sym_a, sym_b, sym_test]
        edges = [edge_to_helper, edge_to_test]

        query = SliceQuery(
            entrypoint="main",
            max_hops=3,
            max_files=20,
            exclude_tests=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_test.id not in result.node_ids

    def test_slice_handles_cycles(self) -> None:
        """Slice handles cyclic references without infinite loop."""
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)

        edge_ab = make_edge(sym_a, sym_b)
        edge_ba = make_edge(sym_b, sym_a)

        nodes = [sym_a, sym_b]
        edges = [edge_ab, edge_ba]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=20)
        result = slice_graph(nodes, edges, query)

        # Should visit both nodes exactly once
        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids


class TestSliceResult:
    """Tests for SliceResult structure and feature ID generation."""

    def test_feature_id_is_deterministic(self) -> None:
        """Same query produces same feature ID."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)

        result1 = slice_graph(nodes, edges, query)
        result2 = slice_graph(nodes, edges, query)

        assert result1.feature_id == result2.feature_id

    def test_feature_id_changes_with_query(self) -> None:
        """Different queries produce different feature IDs."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query1 = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        query2 = SliceQuery(entrypoint="entry", max_hops=5, max_files=20)

        result1 = slice_graph(nodes, edges, query1)
        result2 = slice_graph(nodes, edges, query2)

        assert result1.feature_id != result2.feature_id

    def test_to_dict_produces_valid_feature(self) -> None:
        """SliceResult.to_dict produces spec-compliant feature structure."""
        sym_a = make_symbol("entry")
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b)
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)
        feature = result.to_dict()

        assert "id" in feature
        assert feature["id"].startswith("sha256:")
        assert feature["name"] == "entry"
        assert "entry_nodes" in feature
        assert "node_ids" in feature
        assert "edge_ids" in feature
        assert "query" in feature
        assert feature["query"]["method"] == "bfs"
        assert feature["query"]["entrypoint"] == "entry"
        assert feature["query"]["hops"] == 3
        assert feature["query"]["max_files"] == 20
        assert "limits_hit" in feature


class TestSliceQuery:
    """Tests for SliceQuery dataclass."""

    def test_query_defaults(self) -> None:
        """Query has sensible defaults."""
        query = SliceQuery(entrypoint="foo")

        assert query.max_hops == 3
        assert query.max_files == 20
        assert query.min_confidence == 0.0
        assert query.exclude_tests is False
        assert query.method == "bfs"

    def test_query_to_dict(self) -> None:
        """Query serializes to dict for feature output."""
        query = SliceQuery(
            entrypoint="foo",
            max_hops=5,
            max_files=10,
            exclude_tests=True,
        )

        d = query.to_dict()

        assert d["method"] == "bfs"
        assert d["entrypoint"] == "foo"
        assert d["hops"] == 5
        assert d["max_files"] == 10
        assert d["exclude_tests"] is True


class TestIsTestFile:
    """Tests for test file detection patterns."""

    def test_test_underscore_prefix(self) -> None:
        """Detect test_ prefix in path."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("test_main.py")

    def test_tests_dir_prefix(self) -> None:
        """Detect tests/ directory prefix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("tests/main.py")

    def test_underscore_test_suffix(self) -> None:
        """Detect _test.py suffix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("main_test.py")
        assert _is_test_file("main_test.js")
        assert _is_test_file("main_test.ts")

    def test_dot_test_suffix(self) -> None:
        """Detect .test.py suffix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("main.test.py")
        assert _is_test_file("main.test.js")
        assert _is_test_file("main.test.ts")

    def test_spec_patterns(self) -> None:
        """Detect spec patterns."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("src/spec/main.py")
        assert _is_test_file("main_spec.py")
        assert _is_test_file("main.spec.js")

    def test_not_test_file(self) -> None:
        """Non-test files return False."""
        from hypergumbo.slice import _is_test_file
        assert not _is_test_file("src/main.py")
        assert not _is_test_file("utils.py")

    def test_go_test_suffix(self) -> None:
        """Detect Go _test.go suffix."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("main_test.go")
        assert _is_test_file("pkg/handlers/user_test.go")

    def test_mock_filename_patterns(self) -> None:
        """Detect *_mock.* and mock_*.* filename patterns."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("user_mock.go")
        assert _is_test_file("src/mock_service.py")

    def test_fake_filename_patterns(self) -> None:
        """Detect *_fake.* and fake_*.* filename patterns."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("handler_fake.ts")
        assert _is_test_file("pkg/fake_client.go")

    def test_mock_directories(self) -> None:
        """Detect files in fakes/, mocks/, fixtures/, testdata/, testutils/ directories."""
        from hypergumbo.slice import _is_test_file
        assert _is_test_file("pkg/fakes/handler.go")
        assert _is_test_file("src/mocks/service.ts")
        assert _is_test_file("tests/fixtures/data.json")
        assert _is_test_file("pkg/testdata/sample.txt")
        assert _is_test_file("internal/testutils/helpers.go")

    def test_compound_directory_names(self) -> None:
        """Detect files in directories ending with 'fakes' or 'mocks'."""
        from hypergumbo.slice import _is_test_file
        # These hit endswith("fakes") and endswith("mocks") specifically
        assert _is_test_file("pkg/rtc/transport/transportfakes/handler.go")
        assert _is_test_file("internal/servicemocks/client.go")


class TestSliceEdgeCases:
    """Edge case tests for slice functionality."""

    def test_entry_node_is_test_file_excluded(self) -> None:
        """Entry node in test file is excluded when exclude_tests=True."""
        sym = make_symbol("test_main", path="tests/test_main.py")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="test_main", max_hops=3, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        # Entry node should be excluded, so no nodes in result
        assert len(result.node_ids) == 0

    def test_edge_to_nonexistent_node(self) -> None:
        """Edge pointing to non-existent node is skipped."""
        sym_a = make_symbol("caller")
        # Create edge to non-existent node
        edge = Edge.create(
            src=sym_a.id,
            dst="python:nonexistent.py:1-5:missing:function",
            edge_type="calls",
            line=1,
        )

        nodes = [sym_a]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # Should only have the source node
        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        # Edge should not be included since dst doesn't exist
        assert len(result.edge_ids) == 0

    def test_no_matching_entry(self) -> None:
        """Slice with no matching entry returns empty result."""
        sym = make_symbol("real_function")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="nonexistent", max_hops=3)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 0
        assert len(result.edge_ids) == 0
        assert result.entry_nodes == []

    def test_slice_includes_file_level_imports(self) -> None:
        """Slice from function should include import edges from the containing file.

        Import edges source from file nodes (e.g., python:path:1-1:file:file),
        not function nodes. When slicing from a function, we should include
        the import edges from that function's file.
        """
        # Create a function node in main.py
        func = make_symbol("main", path="src/main.py")

        # Create the file node for main.py (this is what import edges source from)
        file_node = Symbol(
            id="python:src/main.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/main.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Create a module node (the import target)
        module_node = Symbol(
            id="python:os:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="os",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Create an import edge from file node to module
        import_edge = Edge.create(
            src=file_node.id,
            dst=module_node.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )

        nodes = [func, file_node, module_node]
        edges = [import_edge]

        # Slice from the function, not the file
        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # The function should be included
        assert func.id in result.node_ids

        # The import edge from the file should also be included
        assert import_edge.id in result.edge_ids, (
            "Import edges from the containing file should be included in the slice"
        )

    def test_slice_includes_imports_from_multiple_files(self) -> None:
        """Slice should include import edges from all visited files."""
        # main.py: main() calls helper()
        main_func = make_symbol("main", path="src/main.py")
        main_file = Symbol(
            id="python:src/main.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/main.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # utils.py: helper()
        helper_func = make_symbol("helper", path="src/utils.py")
        utils_file = Symbol(
            id="python:src/utils.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/utils.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Module nodes
        os_module = Symbol(
            id="python:os:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="os",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )
        json_module = Symbol(
            id="python:json:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="json",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Edges
        call_edge = make_edge(main_func, helper_func, "calls")
        import_os = Edge.create(
            src=main_file.id,
            dst=os_module.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )
        import_json = Edge.create(
            src=utils_file.id,
            dst=json_module.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )

        nodes = [main_func, main_file, helper_func, utils_file, os_module, json_module]
        edges = [call_edge, import_os, import_json]

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # Both function nodes should be visited
        assert main_func.id in result.node_ids
        assert helper_func.id in result.node_ids

        # Import edges from both files should be included
        assert import_os.id in result.edge_ids, "Import from main.py should be included"
        assert import_json.id in result.edge_ids, "Import from utils.py should be included"


class TestReverseSlice:
    """Tests for reverse slice - finding callers of a function."""

    def test_reverse_slice_query_has_reverse_flag(self) -> None:
        """SliceQuery should support a reverse flag."""
        query = SliceQuery(entrypoint="foo", reverse=True)
        assert query.reverse is True

    def test_reverse_slice_query_defaults_false(self) -> None:
        """SliceQuery.reverse should default to False."""
        query = SliceQuery(entrypoint="foo")
        assert query.reverse is False

    def test_reverse_slice_finds_callers(self) -> None:
        """Reverse slice should find functions that call the entry point."""
        # caller -> callee (entry)
        sym_caller = make_symbol("caller", start_line=1, end_line=5)
        sym_callee = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_caller, sym_callee, "calls")

        nodes = [sym_caller, sym_callee]
        edges = [edge]

        # Slice from callee in REVERSE - should find caller
        query = SliceQuery(entrypoint="callee", max_hops=3, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_callee.id in result.node_ids
        assert sym_caller.id in result.node_ids
        assert edge.id in result.edge_ids

    def test_reverse_slice_multi_hop(self) -> None:
        """Reverse slice should traverse multiple hops backward."""
        # a -> b -> c (entry)
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        # Slice from c in reverse - should find b, then a
        query = SliceQuery(entrypoint="c", max_hops=3, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_c.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_a.id in result.node_ids
        assert edge_bc.id in result.edge_ids
        assert edge_ab.id in result.edge_ids

    def test_reverse_slice_respects_hop_limit(self) -> None:
        """Reverse slice should respect max_hops limit."""
        # a -> b -> c -> d (entry)
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)
        sym_d = make_symbol("d", start_line=7, end_line=8)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)
        edge_cd = make_edge(sym_c, sym_d)

        nodes = [sym_a, sym_b, sym_c, sym_d]
        edges = [edge_ab, edge_bc, edge_cd]

        # From d, max_hops=2: d -> c -> b (NOT a)
        query = SliceQuery(entrypoint="d", max_hops=2, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_d.id in result.node_ids
        assert sym_c.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_a.id not in result.node_ids
        assert "hop_limit" in result.limits_hit

    def test_reverse_slice_respects_file_limit(self) -> None:
        """Reverse slice should respect max_files limit."""
        sym_a = make_symbol("a", path="file1.py")
        sym_b = make_symbol("b", path="file2.py")
        sym_c = make_symbol("c", path="file3.py")

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        query = SliceQuery(entrypoint="c", max_hops=10, max_files=2, reverse=True)
        result = slice_graph(nodes, edges, query)

        files_in_result = {n.split(":")[1] for n in result.node_ids}
        assert len(files_in_result) <= 2
        assert "file_limit" in result.limits_hit

    def test_reverse_slice_excludes_tests(self) -> None:
        """Reverse slice should exclude test files when requested."""
        sym_main = make_symbol("main", path="src/main.py")
        sym_test = make_symbol("test_main", path="tests/test_main.py")

        # test_main calls main - in reverse from main, should NOT find test
        edge = make_edge(sym_test, sym_main, "calls")

        nodes = [sym_main, sym_test]
        edges = [edge]

        query = SliceQuery(entrypoint="main", max_hops=3, reverse=True, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        assert sym_main.id in result.node_ids
        assert sym_test.id not in result.node_ids

    def test_reverse_slice_filters_low_confidence(self) -> None:
        """Reverse slice should filter edges below confidence threshold."""
        sym_entry = make_symbol("entry", start_line=1, end_line=2)
        sym_high = make_symbol("caller_high", start_line=3, end_line=4)
        sym_low = make_symbol("caller_low", start_line=5, end_line=6)

        edge_high = make_edge(sym_high, sym_entry, confidence=0.90)
        edge_low = make_edge(sym_low, sym_entry, confidence=0.40)

        nodes = [sym_entry, sym_high, sym_low]
        edges = [edge_high, edge_low]

        query = SliceQuery(
            entrypoint="entry",
            max_hops=3,
            min_confidence=0.50,
            reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_entry.id in result.node_ids
        assert sym_high.id in result.node_ids
        assert sym_low.id not in result.node_ids

    def test_reverse_slice_handles_cycles(self) -> None:
        """Reverse slice should handle cycles without infinite loop."""
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)

        edge_ab = make_edge(sym_a, sym_b)
        edge_ba = make_edge(sym_b, sym_a)

        nodes = [sym_a, sym_b]
        edges = [edge_ab, edge_ba]

        query = SliceQuery(entrypoint="a", max_hops=10, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids

    def test_reverse_slice_to_dict_includes_reverse(self) -> None:
        """SliceQuery.to_dict should include reverse flag."""
        query = SliceQuery(entrypoint="foo", reverse=True)
        d = query.to_dict()
        assert d["reverse"] is True

    def test_reverse_slice_different_feature_id(self) -> None:
        """Reverse and forward slices should have different feature IDs."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query_forward = SliceQuery(entrypoint="entry", reverse=False)
        query_reverse = SliceQuery(entrypoint="entry", reverse=True)

        result_forward = slice_graph(nodes, edges, query_forward)
        result_reverse = slice_graph(nodes, edges, query_reverse)

        assert result_forward.feature_id != result_reverse.feature_id


class TestRankSliceNodes:
    """Tests for rank_slice_nodes function."""

    def test_empty_slice_returns_sorted_ids(self) -> None:
        """Empty slice returns sorted node IDs."""
        # Create a slice result with node IDs but no matching Symbol objects
        result = SliceResult(
            entry_nodes=[],
            node_ids={"node_c", "node_a", "node_b"},
            edge_ids=set(),
            query=SliceQuery(entrypoint="foo"),
        )

        # Pass empty nodes list so slice_nodes will be empty
        ranked = rank_slice_nodes(result, [], [])

        # Should return sorted node IDs as fallback
        assert ranked == ["node_a", "node_b", "node_c"]

    def test_first_party_priority_false(self) -> None:
        """Raw centrality used when first_party_priority=False."""
        # Create symbols with different tiers
        first_party = make_symbol("my_func", path="src/main.py")
        first_party.supply_chain_tier = 1
        external = make_symbol("lodash", path="node_modules/lodash.js")
        external.supply_chain_tier = 3
        caller = make_symbol("caller", path="src/other.py")
        caller.supply_chain_tier = 1

        # Edge from caller to external
        edge = Edge.create(caller.id, external.id, "calls", 10, confidence=0.9)

        # Create a slice result with these nodes
        result = SliceResult(
            entry_nodes=[caller.id],
            node_ids={caller.id, external.id, first_party.id},
            edge_ids={edge.id},
            query=SliceQuery(entrypoint="caller"),
        )

        ranked = rank_slice_nodes(
            result,
            [first_party, external, caller],
            [edge],
            first_party_priority=False
        )

        # Without tier weighting, external should rank high (has incoming edge)
        # and first_party should rank lower (no incoming edges)
        external_rank = ranked.index(external.id)
        first_party_rank = ranked.index(first_party.id)
        assert external_rank < first_party_rank  # external ranks higher
