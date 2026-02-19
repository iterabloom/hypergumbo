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

import time
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
