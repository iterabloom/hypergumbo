"""Tests for TreeSitterAnalyzer base class.

Tests the two-pass analysis framework that subclasses can use to
eliminate boilerplate. Uses mock grammars and minimal subclasses
to verify the orchestration logic without requiring any real
tree-sitter grammar.

Coverage targets:
- Grammar availability checking (both modes)
- Parser creation (both modes)
- Two-pass orchestration: file discovery → Pass 1 → registry → Pass 2
- Template method dispatch
- max_files limiting
- File-level symbol creation
- Error handling (unreadable files)
- Skipped result when grammar unavailable
- post_process hook
- as_registered_analyzer wrapper
- Custom resolver class
- Usage context extraction
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Optional
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_file_id,
    make_symbol_id,
)
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import NameResolver


# ---------------------------------------------------------------------------
# Mock tree-sitter node for shape_id tests
# ---------------------------------------------------------------------------


@dataclass
class MockNode:
    """Lightweight mock of tree_sitter.Node for shape_id tests."""

    type: str
    is_named: bool = True
    children: list["MockNode"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class StubAnalyzer(TreeSitterAnalyzer):
    """Minimal analyzer for testing the base class orchestration."""

    lang = "stub"
    pass_id = "stub-v1"
    pass_version = "test-0.1.0"
    file_patterns: ClassVar[list[str]] = ["*.stub"]
    grammar_module = "tree_sitter_stub"

    def _check_grammar_available(self) -> bool:
        return True

    def _create_parser(self) -> object:
        """Return a mock parser."""
        parser = MagicMock()
        # Make parser.parse return a mock tree with empty root
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.children = []
        mock_tree.root_node = mock_root
        parser.parse.return_value = mock_tree
        return parser

    def extract_symbols_from_file(self, tree, source, file_path, rel_path, run):
        """Extract a simple function symbol from each file."""
        analysis = FileAnalysis()
        # Simple: create one symbol per file based on filename
        name = Path(rel_path).stem
        sym = Symbol(
            id=make_symbol_id("stub", rel_path, 1, 10, name, "function"),
            name=name,
            kind="function",
            language="stub",
            path=rel_path,
            span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
            origin=self.pass_id,
            origin_run_id=run.execution_id,
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree, source, file_path, rel_path, local_symbols,
        global_symbols, run, import_aliases, resolver
    ):
        """Create no edges by default."""
        return []


class SkippedAnalyzer(StubAnalyzer):
    """Analyzer that reports grammar as unavailable."""

    def _check_grammar_available(self) -> bool:
        return False


class EdgeEmittingAnalyzer(StubAnalyzer):
    """Analyzer that emits call edges between symbols."""

    def extract_edges_from_file(
        self, tree, source, file_path, rel_path, local_symbols,
        global_symbols, run, import_aliases, resolver
    ):
        edges = []
        # If there's a symbol named "caller", create edge to "callee"
        caller = local_symbols.get("caller")
        if caller:
            result = resolver.lookup("callee")
            if result.found and result.symbol:
                edge = Edge.create(
                    src=caller.id,
                    dst=result.symbol.id,
                    edge_type="calls",
                    line=5,
                    origin=self.pass_id,
                    origin_run_id=run.execution_id,
                    evidence_type="call_expression",
                    confidence=0.85,
                )
                edges.append(edge)
        return edges


class FileSymbolAnalyzer(StubAnalyzer):
    """Analyzer that creates file-level symbols."""

    create_file_symbols = True


class MaxFilesAnalyzer(StubAnalyzer):
    """Analyzer that supports max_files."""

    supports_max_files = True


class ImportAliasAnalyzer(StubAnalyzer):
    """Analyzer that extracts import aliases."""

    def get_import_aliases(self, tree, source):
        return {"np": "numpy", "pd": "pandas"}


class UsageContextAnalyzer(StubAnalyzer):
    """Analyzer that extracts usage contexts."""

    def extract_usage_contexts_from_file(self, tree, source, file_path, symbol_by_name):
        contexts = []
        for name, sym in symbol_by_name.items():
            ctx = UsageContext.create(
                kind="call",
                context_name=f"test.{name}",
                position="args[0]",
                path=str(file_path),
                span=sym.span,
                symbol_ref=sym.id,
            )
            contexts.append(ctx)
        return contexts


class PostProcessAnalyzer(StubAnalyzer):
    """Analyzer that uses post_process to add extra symbols."""

    def post_process(self, symbols, edges, usage_contexts, run):
        # Add a synthetic "summary" symbol
        summary = Symbol(
            id=make_symbol_id("stub", "synthetic", 0, 0, "summary", "meta"),
            name="summary",
            kind="meta",
            language="stub",
            path="synthetic",
            span=Span(start_line=0, start_col=0, end_line=0, end_col=0),
            origin=self.pass_id,
            origin_run_id=run.execution_id,
        )
        return symbols + [summary], edges, usage_contexts


class LanguagePackAnalyzer(TreeSitterAnalyzer):
    """Analyzer that uses language_pack_name instead of grammar_module."""

    lang = "packtest"
    pass_id = "packtest-v1"
    pass_version = "test-0.1.0"
    file_patterns: ClassVar[list[str]] = ["*.pkt"]
    language_pack_name = "nim"  # Use a real language-pack name for testing

    def extract_symbols_from_file(self, tree, source, file_path, rel_path, run):
        return FileAnalysis()

    def extract_edges_from_file(
        self, tree, source, file_path, rel_path, local_symbols,
        global_symbols, run, import_aliases, resolver
    ):
        return []


class CustomRegistryAnalyzer(StubAnalyzer):
    """Analyzer that uses list-valued registry (like Go)."""

    def register_symbol(self, symbol, global_symbols):
        short_name = symbol.name.split(".")[-1]
        if short_name not in global_symbols:
            global_symbols[short_name] = []
        global_symbols[short_name].append(symbol)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTreeSitterAnalyzerBasic:
    """Basic orchestration tests."""

    def test_analyze_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty result for directory with no matching files."""
        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert isinstance(result, AnalysisResult)
        assert result.symbols == []
        assert result.edges == []
        assert result.usage_contexts == []
        assert result.run is not None
        assert result.run.pass_id == "stub-v1"
        assert result.run.version == "test-0.1.0"
        assert result.run.files_analyzed == 0
        assert result.run.duration_ms >= 0
        assert not result.skipped

    def test_analyze_with_files(self, tmp_path: Path) -> None:
        """Should extract symbols from matching files."""
        (tmp_path / "foo.stub").write_text("content")
        (tmp_path / "bar.stub").write_text("content")
        (tmp_path / "ignored.txt").write_text("not a stub file")

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert len(result.symbols) == 2
        names = {s.name for s in result.symbols}
        assert names == {"foo", "bar"}
        assert result.run.files_analyzed == 2

    def test_analyze_tracks_timing(self, tmp_path: Path) -> None:
        """Should record duration in run metadata."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert result.run.duration_ms >= 0

    def test_symbols_have_correct_metadata(self, tmp_path: Path) -> None:
        """Symbols should have correct language and origin fields."""
        (tmp_path / "example.stub").write_text("content")

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)

        sym = result.symbols[0]
        assert sym.language == "stub"
        assert sym.origin == "stub-v1"
        assert sym.kind == "function"


class TestTreeSitterAnalyzerSkipped:
    """Tests for grammar unavailability."""

    def test_skipped_when_grammar_unavailable(self, tmp_path: Path) -> None:
        """Should return skipped result when grammar is not available."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = SkippedAnalyzer()
        with pytest.warns(UserWarning, match="stub.*not available"):
            result = analyzer.analyze(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None


class TestTreeSitterAnalyzerEdges:
    """Tests for Pass 2 edge extraction."""

    def test_edges_extracted_in_pass2(self, tmp_path: Path) -> None:
        """Should extract edges using global symbol registry."""
        (tmp_path / "caller.stub").write_text("content")
        (tmp_path / "callee.stub").write_text("content")

        analyzer = EdgeEmittingAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert len(result.symbols) == 2
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].confidence == 0.85

    def test_resolver_receives_global_symbols(self, tmp_path: Path) -> None:
        """Resolver should have access to symbols from all files."""
        (tmp_path / "caller.stub").write_text("content")
        (tmp_path / "callee.stub").write_text("content")

        analyzer = EdgeEmittingAnalyzer()
        result = analyzer.analyze(tmp_path)

        # The edge from caller to callee proves the resolver found callee
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1


class TestTreeSitterAnalyzerFileSymbols:
    """Tests for file-level symbol creation."""

    def test_file_symbols_created_when_configured(self, tmp_path: Path) -> None:
        """Should create file-level symbols when create_file_symbols is True."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = FileSymbolAnalyzer()
        result = analyzer.analyze(tmp_path)

        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) == 1
        assert file_syms[0].language == "stub"

    def test_no_file_symbols_by_default(self, tmp_path: Path) -> None:
        """Should not create file-level symbols by default."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)

        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) == 0


class TestTreeSitterAnalyzerMaxFiles:
    """Tests for max_files limiting."""

    def test_max_files_limits_processing(self, tmp_path: Path) -> None:
        """Should stop after max_files files."""
        for i in range(5):
            (tmp_path / f"file{i}.stub").write_text(f"content {i}")

        analyzer = MaxFilesAnalyzer()
        result = analyzer.analyze(tmp_path, max_files=2)

        assert result.run.files_analyzed == 2
        assert len(result.symbols) == 2

    def test_max_files_none_processes_all(self, tmp_path: Path) -> None:
        """Should process all files when max_files is None."""
        for i in range(3):
            (tmp_path / f"file{i}.stub").write_text(f"content {i}")

        analyzer = MaxFilesAnalyzer()
        result = analyzer.analyze(tmp_path, max_files=None)

        assert result.run.files_analyzed == 3


class TestTreeSitterAnalyzerImportAliases:
    """Tests for import alias extraction."""

    def test_import_aliases_passed_to_edge_extraction(self, tmp_path: Path) -> None:
        """Import aliases should be available during edge extraction."""
        (tmp_path / "test.stub").write_text("content")

        # Create a subclass that checks aliases are passed correctly
        received_aliases = {}

        class CheckAliasAnalyzer(ImportAliasAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path, rel_path,
                                        local_symbols, global_symbols, run,
                                        import_aliases, resolver):
                nonlocal received_aliases
                received_aliases = import_aliases
                return []

        analyzer = CheckAliasAnalyzer()
        analyzer.analyze(tmp_path)

        assert received_aliases == {"np": "numpy", "pd": "pandas"}


class TestTreeSitterAnalyzerUsageContexts:
    """Tests for usage context extraction."""

    def test_usage_contexts_collected(self, tmp_path: Path) -> None:
        """Should collect usage contexts from all files."""
        (tmp_path / "handler.stub").write_text("content")

        analyzer = UsageContextAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert len(result.usage_contexts) == 1
        assert result.usage_contexts[0].kind == "call"


class TestTreeSitterAnalyzerPostProcess:
    """Tests for post_process hook."""

    def test_post_process_modifies_results(self, tmp_path: Path) -> None:
        """Should allow post_process to add/modify symbols."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = PostProcessAnalyzer()
        result = analyzer.analyze(tmp_path)

        names = {s.name for s in result.symbols}
        assert "test" in names  # From extract_symbols_from_file
        assert "summary" in names  # From post_process


class TestTreeSitterAnalyzerCustomRegistry:
    """Tests for custom register_symbol behavior."""

    def test_custom_registry_used_for_resolution(self, tmp_path: Path) -> None:
        """Should use custom register_symbol to build the registry."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = CustomRegistryAnalyzer()
        result = analyzer.analyze(tmp_path)

        # Should not crash with list-valued registry
        assert len(result.symbols) == 1


class TestTreeSitterAnalyzerErrorHandling:
    """Tests for error handling during file processing."""

    def test_skips_unreadable_files(self, tmp_path: Path) -> None:
        """Should skip files that raise OSError during read."""
        (tmp_path / "good.stub").write_text("content")
        (tmp_path / "bad.stub").write_text("content")

        # Patch read_bytes to raise OSError for "bad.stub"
        original_read_bytes = Path.read_bytes

        def patched_read_bytes(self_path: Path) -> bytes:
            if self_path.name == "bad.stub":
                raise OSError("mock read error")
            return original_read_bytes(self_path)

        analyzer = StubAnalyzer()
        with patch.object(Path, "read_bytes", patched_read_bytes):
            result = analyzer.analyze(tmp_path)

        assert result.run.files_analyzed == 1
        assert result.run.files_skipped == 1


class TestTreeSitterAnalyzerLanguagePack:
    """Tests for language-pack grammar mode."""

    def test_language_pack_mode_check(self) -> None:
        """Should use language_pack_name for availability checking."""
        analyzer = LanguagePackAnalyzer()
        # language_pack_name is set, grammar_module is not
        assert analyzer.language_pack_name == "nim"
        assert analyzer.grammar_module is None

    def test_language_pack_creates_parser(self, tmp_path: Path) -> None:
        """Should create a parser using language-pack."""
        (tmp_path / "test.pkt").write_text("content")

        analyzer = LanguagePackAnalyzer()
        result = analyzer.analyze(tmp_path)

        # Should not crash - parser was created from language pack
        assert isinstance(result, AnalysisResult)
        assert result.run.files_analyzed == 1


class TestTreeSitterAnalyzerGrammarModule:
    """Tests for direct grammar module mode."""

    def test_grammar_module_check_and_parse(self, tmp_path: Path) -> None:
        """Should use grammar_module for availability and parsing."""

        class GoStyleAnalyzer(TreeSitterAnalyzer):
            lang = "gostyle"
            pass_id = "gostyle-v1"
            pass_version = "test-0.1.0"
            file_patterns: ClassVar[list[str]] = ["*.go"]
            grammar_module = "tree_sitter_go"

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                return FileAnalysis()

            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                return []

        (tmp_path / "main.go").write_text("package main")

        analyzer = GoStyleAnalyzer()
        result = analyzer.analyze(tmp_path)

        assert result.run.files_analyzed == 1
        assert not result.skipped


class TestTreeSitterAnalyzerAsRegisteredAnalyzer:
    """Tests for as_registered_analyzer helper."""

    def test_returns_callable(self) -> None:
        """Should return a callable function."""
        analyzer = StubAnalyzer()
        func = analyzer.as_registered_analyzer()

        assert callable(func)

    def test_callable_delegates_to_analyze(self, tmp_path: Path) -> None:
        """The returned callable should call analyze()."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = StubAnalyzer()
        func = analyzer.as_registered_analyzer()
        result = func(tmp_path)

        assert isinstance(result, AnalysisResult)
        assert len(result.symbols) == 1

    def test_callable_passes_max_files(self, tmp_path: Path) -> None:
        """The returned callable should pass max_files through."""
        for i in range(5):
            (tmp_path / f"f{i}.stub").write_text(f"c{i}")

        analyzer = MaxFilesAnalyzer()
        func = analyzer.as_registered_analyzer()
        result = func(tmp_path, max_files=2)

        assert result.run.files_analyzed == 2


class TestTreeSitterAnalyzerReparse:
    """Tests verifying Pass 2 re-parses files correctly."""

    def test_pass2_receives_parsed_trees(self, tmp_path: Path) -> None:
        """Pass 2 should receive parsed tree for each file."""
        (tmp_path / "test.stub").write_text("hello world")

        trees_received = []

        class TreeCheckAnalyzer(StubAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                trees_received.append(tree)
                return []

        analyzer = TreeCheckAnalyzer()
        analyzer.analyze(tmp_path)

        assert len(trees_received) == 1
        # The tree should be a mock (from our _create_parser)
        assert trees_received[0] is not None


# ---------------------------------------------------------------------------
# shape_id computation tests (ADR-0014 §1)
# ---------------------------------------------------------------------------


def _make_tree(
    type_name: str,
    *children: MockNode,
    named: bool = True,
) -> MockNode:
    """Shortcut to build a MockNode tree."""
    return MockNode(type=type_name, is_named=named, children=list(children))


class TestComputeShapeId:
    """Tests for TreeSitterAnalyzer.compute_shape_id() and _cst_structure()."""

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()

    def test_leaf_node_returns_type_name(self) -> None:
        """A named leaf node should produce just its type name."""
        node = MockNode(type="identifier", is_named=True, children=[])
        structure = self.analyzer._cst_structure(node)
        assert structure == "identifier"

    def test_nested_structure_produces_s_expression(self) -> None:
        """A node with named children should produce an S-expression."""
        # function_definition → identifier + parameters(identifier) + block(return_statement(integer))
        tree = _make_tree(
            "function_definition",
            MockNode(type="identifier"),
            _make_tree("parameters", MockNode(type="identifier")),
            _make_tree("block", _make_tree("return_statement", MockNode(type="integer"))),
        )
        structure = self.analyzer._cst_structure(tree)
        # Closing parens are space-separated tokens (consistent for hashing)
        assert structure == (
            "(function_definition identifier (parameters identifier )"
            " (block (return_statement integer ) ) )"
        )

    def test_anonymous_nodes_skipped(self) -> None:
        """Anonymous nodes (punctuation) should be filtered out."""
        tree = _make_tree(
            "function_definition",
            MockNode(type="identifier"),
            MockNode(type="(", is_named=False),  # punctuation
            _make_tree("parameters", MockNode(type="identifier")),
            MockNode(type=")", is_named=False),  # punctuation
            MockNode(type=":", is_named=False),
            _make_tree("block", MockNode(type="pass_statement")),
        )
        structure = self.analyzer._cst_structure(tree)
        assert structure == (
            "(function_definition identifier"
            " (parameters identifier ) (block pass_statement ) )"
        )

    def test_comment_nodes_skipped(self) -> None:
        """Comment nodes should be filtered from the structure."""
        tree = _make_tree(
            "block",
            MockNode(type="comment"),
            MockNode(type="line_comment"),
            MockNode(type="block_comment"),
            MockNode(type="return_statement"),
        )
        structure = self.analyzer._cst_structure(tree)
        # Only return_statement should remain, making block a parent of one child
        assert structure == "(block return_statement )"

    def test_error_and_missing_nodes_skipped(self) -> None:
        """ERROR and MISSING nodes should be filtered out."""
        tree = _make_tree(
            "block",
            MockNode(type="ERROR"),
            MockNode(type="expression_statement"),
            MockNode(type="MISSING"),
        )
        structure = self.analyzer._cst_structure(tree)
        assert structure == "(block expression_statement )"

    def test_compute_shape_id_format(self) -> None:
        """compute_shape_id should return sha256:{16-hex-chars} format."""
        node = MockNode(type="identifier")
        shape_id = self.analyzer.compute_shape_id(node)
        assert shape_id.startswith("sha256:")
        hex_part = shape_id.split(":")[1]
        assert len(hex_part) == 16
        int(hex_part, 16)  # should not raise

    def test_same_structure_same_hash(self) -> None:
        """Structurally identical trees should produce the same shape_id."""
        tree_a = _make_tree("function_definition", MockNode(type="identifier"),
                            _make_tree("block", MockNode(type="pass_statement")))
        tree_b = _make_tree("function_definition", MockNode(type="identifier"),
                            _make_tree("block", MockNode(type="pass_statement")))
        assert self.analyzer.compute_shape_id(tree_a) == self.analyzer.compute_shape_id(tree_b)

    def test_different_structure_different_hash(self) -> None:
        """Structurally different trees should produce different shape_ids."""
        tree_a = _make_tree("function_definition", MockNode(type="identifier"),
                            _make_tree("block", MockNode(type="pass_statement")))
        tree_b = _make_tree("function_definition", MockNode(type="identifier"),
                            _make_tree("block", MockNode(type="return_statement")))
        assert self.analyzer.compute_shape_id(tree_a) != self.analyzer.compute_shape_id(tree_b)

    def test_hash_matches_manual_computation(self) -> None:
        """The hash should match a manual sha256 of the structure string."""
        node = _make_tree("block", MockNode(type="expression_statement"))
        structure = self.analyzer._cst_structure(node)
        expected = hashlib.sha256(structure.encode()).hexdigest()[:16]
        assert self.analyzer.compute_shape_id(node) == f"sha256:{expected}"

    def test_node_becomes_leaf_when_only_anonymous_children(self) -> None:
        """A named node with only anonymous children should be treated as a leaf."""
        node = _make_tree(
            "string",
            MockNode(type='"', is_named=False),
            MockNode(type="string_content", is_named=False),
            MockNode(type='"', is_named=False),
        )
        structure = self.analyzer._cst_structure(node)
        assert structure == "string"

    def test_skip_type_as_root_produces_empty(self) -> None:
        """A skip-type node (ERROR, comment) as root produces empty structure."""
        error_node = MockNode(type="ERROR")
        assert self.analyzer._cst_structure(error_node) == ""

        comment_node = MockNode(type="comment")
        assert self.analyzer._cst_structure(comment_node) == ""

    def test_anonymous_root_produces_empty(self) -> None:
        """An anonymous root node produces empty structure."""
        anon = MockNode(type=";", is_named=False)
        assert self.analyzer._cst_structure(anon) == ""

    def test_deeply_nested_does_not_recurse(self) -> None:
        """Deeply nested trees should work without hitting recursion limits."""
        # Build a chain 500 levels deep — would blow the stack with recursion
        node = MockNode(type="leaf_node")
        for i in range(500):
            node = _make_tree(f"wrapper_{i}", node)
        # Should not raise RecursionError
        shape_id = self.analyzer.compute_shape_id(node)
        assert shape_id.startswith("sha256:")


class TestShapeIdAutoComputation:
    """Tests for automatic shape_id computation via node_for_symbol in analyze()."""

    def test_shape_id_computed_from_node_for_symbol(self, tmp_path: Path) -> None:
        """Symbols in node_for_symbol should get shape_id auto-computed."""
        (tmp_path / "test.stub").write_text("content")

        # Build a mock node to return in node_for_symbol
        mock_node = _make_tree(
            "function_definition",
            MockNode(type="identifier"),
            _make_tree("block", MockNode(type="pass_statement")),
        )

        class ShapeIdAnalyzer(StubAnalyzer):
            def extract_symbols_from_file(self, tree, source, file_path, rel_path, run):
                analysis = FileAnalysis()
                sym = Symbol(
                    id=make_symbol_id("stub", rel_path, 1, 10, "test", "function"),
                    name="test",
                    kind="function",
                    language="stub",
                    path=rel_path,
                    span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
                    origin=self.pass_id,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(sym)
                analysis.symbol_by_name["test"] = sym
                analysis.node_for_symbol[sym.id] = mock_node
                return analysis

        analyzer = ShapeIdAnalyzer()
        result = analyzer.analyze(tmp_path)
        sym = result.symbols[0]

        assert sym.shape_id is not None
        assert sym.shape_id.startswith("sha256:")
        assert sym.shape_id == analyzer.compute_shape_id(mock_node)

    def test_shape_id_not_overwritten_if_already_set(self, tmp_path: Path) -> None:
        """If a symbol already has shape_id, auto-computation should not overwrite."""
        (tmp_path / "test.stub").write_text("content")

        mock_node = MockNode(type="identifier")

        class PresetShapeIdAnalyzer(StubAnalyzer):
            def extract_symbols_from_file(self, tree, source, file_path, rel_path, run):
                analysis = FileAnalysis()
                sym = Symbol(
                    id=make_symbol_id("stub", rel_path, 1, 10, "test", "function"),
                    name="test",
                    kind="function",
                    language="stub",
                    path=rel_path,
                    span=Span(start_line=1, start_col=0, end_line=10, end_col=0),
                    origin=self.pass_id,
                    origin_run_id=run.execution_id,
                    shape_id="sha256:preset_value_abc",
                )
                analysis.symbols.append(sym)
                analysis.symbol_by_name["test"] = sym
                analysis.node_for_symbol[sym.id] = mock_node
                return analysis

        analyzer = PresetShapeIdAnalyzer()
        result = analyzer.analyze(tmp_path)
        sym = result.symbols[0]

        # Should keep the preset value, not overwrite
        assert sym.shape_id == "sha256:preset_value_abc"

    def test_no_node_for_symbol_no_shape_id(self, tmp_path: Path) -> None:
        """Symbols without entries in node_for_symbol keep shape_id=None."""
        (tmp_path / "test.stub").write_text("content")

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)
        sym = result.symbols[0]

        # StubAnalyzer doesn't populate node_for_symbol
        assert sym.shape_id is None
