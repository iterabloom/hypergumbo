# SPDX-License-Identifier: AGPL-3.0-or-later
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
    ArityFlags,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_file_id,
    make_symbol_id,
)
from hypergumbo_core.ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import NameResolver


# ---------------------------------------------------------------------------
# Mock tree-sitter node for shape_id tests
# ---------------------------------------------------------------------------


@dataclass
class MockNode:
    """Lightweight mock of tree_sitter.Node for shape_id and stable_id tests."""

    type: str
    is_named: bool = True
    children: list["MockNode"] = field(default_factory=list)
    text: bytes | None = None


# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class StubAnalyzer(TreeSitterAnalyzer):
    """Minimal analyzer for testing the base class orchestration."""

    lang = "stub"
    pass_id = "stub"
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
        mock_root.end_point = (0, 0)  # a real (row, col) so file-node spans compute (WI-sijug)
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


class FieldTypeAnalyzer(StubAnalyzer):
    """Analyzer that populates class_field_types for testing."""

    def extract_symbols_from_file(self, tree, source, file_path, rel_path, run):
        analysis = super().extract_symbols_from_file(
            tree, source, file_path, rel_path, run,
        )
        # Populate field types based on filename convention
        stem = Path(rel_path).stem
        if stem == "server":
            analysis.class_field_types = {
                "Server": {"app": "App", "db": "Database"},
            }
        elif stem == "app":
            analysis.class_field_types = {
                "App": {"handler": "Handler"},
            }
        return analysis


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
    pass_id = "packtest"
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
        assert result.run.pass_id == "stub"
        assert result.run.version == PASS_VERSION
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
        assert sym.origin == ["stub"]
        assert sym.kind == "function"


class TestAnalyzerVersionSemantic:
    """INV-kohat: AnalysisRun.version always holds package version, not code-hash."""

    def test_version_is_package_version_not_code_hash(self, tmp_path: Path) -> None:
        """run.version must equal PASS_VERSION even when pass_version is set."""
        analyzer = StubAnalyzer()
        assert analyzer.pass_version == "test-0.1.0"
        result = analyzer.analyze(tmp_path)
        assert result.run.version == PASS_VERSION

    def test_pass_version_carries_code_hash(self, tmp_path: Path) -> None:
        """run.pass_version must hold the analyzer's code-hash."""
        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)
        assert result.run.pass_version == "test-0.1.0"

    def test_version_fallback_when_no_pass_version(self, tmp_path: Path) -> None:
        """run.version still equals PASS_VERSION when pass_version is empty.

        INV-gizik / Phase 6 PR2 closure: ``pass_version`` is now auto-stamped
        from the concrete analyzer subclass's module hash when the subclass
        leaves it empty (mirrors the linker-side stamping). The empty-string
        default is therefore observable only briefly, replaced with a
        ``sha256:<hex>`` code-hash by the time ``analyze`` returns. The
        ``version`` field stays anchored to ``PASS_VERSION`` per INV-kohat.
        """
        analyzer = StubAnalyzer()
        analyzer.pass_version = ""
        result = analyzer.analyze(tmp_path)
        assert result.run.version == PASS_VERSION
        # INV-gizik: pass_version is now auto-stamped from the subclass
        # module hash when not set explicitly.
        assert result.run.pass_version.startswith("sha256:")


class TestPhase6PR2WriterContractWiring:
    """Phase 6 PR2: TreeSitterAnalyzer wires INV-lidul (config_fingerprint),
    INV-gizik (pass_version), INV-nihug (toolchain extension), and INV-pitab
    (warnings capture) at the base-class level."""

    def test_config_fingerprint_is_not_default(self, tmp_path: Path) -> None:
        """INV-lidul: config_fingerprint must not be the literal empty-dict
        default after analyze() completes; each analyzer's effective config
        produces a distinct fingerprint."""
        from hypergumbo_core.ir import _default_config_fingerprint

        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)
        assert result.run.config_fingerprint != _default_config_fingerprint()
        assert result.run.config_fingerprint.startswith("sha256:")

    def test_two_distinct_subclasses_get_distinct_fingerprints(
        self, tmp_path: Path,
    ) -> None:
        """INV-lidul: two analyzer subclasses with different config dicts
        produce different config_fingerprints."""

        class OtherStubAnalyzer(StubAnalyzer):
            lang = "stub2"
            pass_id = "stub2"
            file_patterns: ClassVar[list[str]] = ["*.stub2"]

        a1 = StubAnalyzer()
        a2 = OtherStubAnalyzer()
        r1 = a1.analyze(tmp_path)
        r2 = a2.analyze(tmp_path)
        assert r1.run.config_fingerprint != r2.run.config_fingerprint

    def test_pass_version_auto_stamped(self, tmp_path: Path) -> None:
        """INV-gizik: pass_version is auto-stamped from the subclass module
        hash even when the subclass leaves it empty."""
        analyzer = StubAnalyzer()
        analyzer.pass_version = ""  # explicit empty
        result = analyzer.analyze(tmp_path)
        assert result.run.pass_version.startswith("sha256:")

    def test_toolchain_extended_with_grammar_module(
        self, tmp_path: Path,
    ) -> None:
        """INV-nihug: toolchain dict gets grammar_module appended for
        grammar-backed analyzers. tree_sitter_version is also captured
        when the ``tree_sitter`` library exposes a package version via
        importlib.metadata (typical install)."""
        analyzer = StubAnalyzer()
        result = analyzer.analyze(tmp_path)
        # Grammar module is always set on grammar-backed analyzers.
        assert result.run.toolchain.get("grammar_module") == "tree_sitter_stub"
        # tree_sitter library version captured via importlib.metadata when
        # available — most installs expose it.
        assert "tree_sitter_version" in result.run.toolchain

    def test_toolchain_extended_with_language_pack(
        self, tmp_path: Path,
    ) -> None:
        """INV-nihug: language-pack-backed analyzers get a
        ``language_pack:<name>`` toolchain entry."""

        class PackStubAnalyzer(StubAnalyzer):
            grammar_module = None
            language_pack_name = "stub_pack"

        analyzer = PackStubAnalyzer()
        result = analyzer.analyze(tmp_path)
        assert (
            result.run.toolchain.get("grammar_module")
            == "language_pack:stub_pack"
        )

    def test_warnings_captured_for_grammar_unavailable_path(
        self, tmp_path: Path,
    ) -> None:
        """INV-pitab: the grammar-unavailable branch explicitly populates
        ``run.warnings`` so the per-run AnalysisRun record surfaces the
        skip reason in machine-readable form (the schema declares the
        field; the producer now writes to it)."""
        import pytest as _pytest

        class UnavailableStubAnalyzer(StubAnalyzer):
            def _check_grammar_available(self) -> bool:
                return False

        (tmp_path / "test.stub").write_text("content")
        analyzer = UnavailableStubAnalyzer()
        with _pytest.warns(UserWarning):
            result = analyzer.analyze(tmp_path)
        assert result.skipped is True
        assert any("not available" in w for w in result.run.warnings)


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

    def test_file_symbol_spans_full_file_not_line_one(self, tmp_path: Path) -> None:
        """WI-sijug: the inline file node spans the whole file (parsed ``end_point``),
        not the degenerate line-1-only ``Span(1,0,1,0)`` that fingerprinted just a
        shebang/comment and yielded a needless null."""

        class _MultiLineFileSymbolAnalyzer(FileSymbolAnalyzer):
            def _create_parser(self) -> object:
                parser = MagicMock()
                tree = MagicMock()
                # 0-indexed end_point row 4 → a 5-line file
                tree.root_node = MagicMock(children=[], end_point=(4, 0))
                parser.parse.return_value = tree
                return parser

        (tmp_path / "test.stub").write_text("a\nb\nc\nd\ne\n")
        result = _MultiLineFileSymbolAnalyzer().analyze(tmp_path)

        file_sym = next(s for s in result.symbols if s.kind == "file")
        assert file_sym.span.start_line == 1
        assert file_sym.span.end_line == 5  # full file, not the degenerate 1


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

        # INV-buhur: skipped files MUST also be recorded in run.failed_files so
        # the orchestrator can drain them into limits.failed_files for honest
        # partial-result reporting. Bumping files_skipped without recording
        # which files were dropped silently loses per-file granularity.
        assert len(result.run.failed_files) == 1
        assert result.run.failed_files[0]["path"].endswith("bad.stub")
        assert "mock read error" in result.run.failed_files[0]["reason"]


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
            pass_id = "gostyle"
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


# ---------------------------------------------------------------------------
# ArityFlags and classify_parameter_flags tests (ADR-0014 §2)
# ---------------------------------------------------------------------------


class TestArityFlags:
    """Tests for the ArityFlags dataclass."""

    def test_frozen(self) -> None:
        """ArityFlags should be immutable."""
        flags = ArityFlags(param_count=2, has_defaults=True, has_varargs=False, has_kwargs=False)
        with pytest.raises(AttributeError):
            flags.param_count = 3  # type: ignore[misc]

    def test_as_flags_str(self) -> None:
        """as_flags_str should produce the canonical form used in hashing."""
        flags = ArityFlags(param_count=3, has_defaults=True, has_varargs=False, has_kwargs=True)
        assert flags.as_flags_str() == "True,False,True"

    def test_as_flags_str_all_false(self) -> None:
        flags = ArityFlags(param_count=0, has_defaults=False, has_varargs=False, has_kwargs=False)
        assert flags.as_flags_str() == "False,False,False"

    def test_equality(self) -> None:
        a = ArityFlags(param_count=2, has_defaults=True, has_varargs=False, has_kwargs=False)
        b = ArityFlags(param_count=2, has_defaults=True, has_varargs=False, has_kwargs=False)
        assert a == b

    def test_inequality(self) -> None:
        a = ArityFlags(param_count=2, has_defaults=True, has_varargs=False, has_kwargs=False)
        b = ArityFlags(param_count=3, has_defaults=True, has_varargs=False, has_kwargs=False)
        assert a != b


class TestClassifyParameterFlags:
    """Tests for TreeSitterAnalyzer.classify_parameter_flags()."""

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()

    def test_empty_params(self) -> None:
        """No parameters should yield count=0 and all flags False."""
        params = MockNode(type="formal_parameters", children=[
            MockNode(type="(", is_named=False),
            MockNode(type=")", is_named=False),
        ])
        flags = self.analyzer.classify_parameter_flags(params)
        assert flags == ArityFlags(param_count=0, has_defaults=False, has_varargs=False, has_kwargs=False)

    def test_regular_params(self) -> None:
        """Regular named params should be counted."""
        params = MockNode(type="formal_parameters", children=[
            MockNode(type="(", is_named=False),
            MockNode(type="identifier"),
            MockNode(type=",", is_named=False),
            MockNode(type="identifier"),
            MockNode(type=")", is_named=False),
        ])
        flags = self.analyzer.classify_parameter_flags(params)
        assert flags.param_count == 2
        assert not flags.has_defaults
        assert not flags.has_varargs
        assert not flags.has_kwargs

    def test_varargs_detected(self) -> None:
        """rest_pattern/spread_parameter/splat_parameter should set has_varargs."""
        for vararg_type in ["rest_pattern", "spread_parameter", "splat_parameter",
                            "variadic_parameter"]:
            params = MockNode(type="parameters", children=[
                MockNode(type="identifier"),
                MockNode(type=vararg_type),
            ])
            flags = self.analyzer.classify_parameter_flags(params)
            assert flags.has_varargs, f"{vararg_type} should set has_varargs"
            assert flags.param_count == 2

    def test_kwargs_detected(self) -> None:
        """hash_splat_parameter/dictionary_splat_pattern should set has_kwargs."""
        for kwargs_type in ["hash_splat_parameter", "dictionary_splat_pattern"]:
            params = MockNode(type="parameters", children=[
                MockNode(type="identifier"),
                MockNode(type=kwargs_type),
            ])
            flags = self.analyzer.classify_parameter_flags(params)
            assert flags.has_kwargs, f"{kwargs_type} should set has_kwargs"
            assert flags.param_count == 2

    def test_default_param_detected(self) -> None:
        """assignment_pattern/optional_parameter/default_parameter should set has_defaults."""
        for default_type in ["assignment_pattern", "optional_parameter", "default_parameter"]:
            params = MockNode(type="parameters", children=[
                MockNode(type="identifier"),
                MockNode(type=default_type),
            ])
            flags = self.analyzer.classify_parameter_flags(params)
            assert flags.has_defaults, f"{default_type} should set has_defaults"
            assert flags.param_count == 2

    def test_nested_default_value_detected(self) -> None:
        """A typed_parameter with a default_value child should set has_defaults."""
        typed_param = MockNode(type="typed_parameter", children=[
            MockNode(type="identifier"),
            MockNode(type=":", is_named=False),
            MockNode(type="type_identifier"),
            MockNode(type="=", is_named=False),
            MockNode(type="default_value"),
        ])
        params = MockNode(type="parameters", children=[typed_param])
        flags = self.analyzer.classify_parameter_flags(params)
        assert flags.has_defaults
        assert flags.param_count == 1

    def test_mixed_params(self) -> None:
        """A mix of regular, default, varargs, and kwargs params."""
        params = MockNode(type="parameters", children=[
            MockNode(type="identifier"),          # regular
            MockNode(type="optional_parameter"),   # default
            MockNode(type="splat_parameter"),      # varargs
            MockNode(type="hash_splat_parameter"), # kwargs
        ])
        flags = self.analyzer.classify_parameter_flags(params)
        assert flags == ArityFlags(param_count=4, has_defaults=True, has_varargs=True, has_kwargs=True)


# ---------------------------------------------------------------------------
# compute_stable_id tests (ADR-0014 §2)
# ---------------------------------------------------------------------------


class TestComputeStableId:
    """Tests for TreeSitterAnalyzer.compute_stable_id()."""

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()

    def _make_function_node(
        self,
        params: list[MockNode] | None = None,
        decorators: list[MockNode] | None = None,
    ) -> MockNode:
        """Build a mock function_definition node."""
        children: list[MockNode] = []
        if decorators:
            children.extend(decorators)
        children.append(MockNode(type="identifier", text=b"my_func"))
        if params is not None:
            children.append(MockNode(type="parameters", children=params))
        else:
            children.append(MockNode(type="parameters", children=[]))
        children.append(_make_tree("block", MockNode(type="pass_statement")))
        return MockNode(type="function_definition", children=children)

    def test_format(self) -> None:
        """compute_stable_id should return sha256:{16-hex-chars} format."""
        node = self._make_function_node()
        sid = self.analyzer.compute_stable_id(node, kind="function")
        assert sid.startswith("sha256:")
        hex_part = sid.split(":")[1]
        assert len(hex_part) == 16
        int(hex_part, 16)

    def test_same_shape_same_id(self) -> None:
        """Two functions with identical signatures produce the same stable_id."""
        node_a = self._make_function_node(params=[MockNode(type="identifier")])
        node_b = self._make_function_node(params=[MockNode(type="identifier")])
        assert (
            self.analyzer.compute_stable_id(node_a, kind="function")
            == self.analyzer.compute_stable_id(node_b, kind="function")
        )

    def test_different_kind_different_id(self) -> None:
        """Different kind values produce different stable_ids."""
        node = self._make_function_node()
        sid_func = self.analyzer.compute_stable_id(node, kind="function")
        sid_method = self.analyzer.compute_stable_id(node, kind="method")
        assert sid_func != sid_method

    def test_different_param_count_different_id(self) -> None:
        """Different parameter counts produce different stable_ids."""
        node_1 = self._make_function_node(params=[MockNode(type="identifier")])
        node_2 = self._make_function_node(params=[
            MockNode(type="identifier"), MockNode(type="identifier"),
        ])
        assert (
            self.analyzer.compute_stable_id(node_1, kind="function")
            != self.analyzer.compute_stable_id(node_2, kind="function")
        )

    def test_containing_stable_id_differentiates(self) -> None:
        """Different containing_stable_id values produce different hashes."""
        node = self._make_function_node()
        sid_a = self.analyzer.compute_stable_id(node, "method", containing_stable_id="sha256:classA")
        sid_b = self.analyzer.compute_stable_id(node, "method", containing_stable_id="sha256:classB")
        assert sid_a != sid_b

    def test_no_params_node(self) -> None:
        """Nodes without a recognizable params child use default arity."""
        node = MockNode(type="class_definition", children=[
            MockNode(type="identifier", text=b"MyClass"),
            _make_tree("block", MockNode(type="pass_statement")),
        ])
        sid = self.analyzer.compute_stable_id(node, kind="class")
        assert sid.startswith("sha256:")

    def test_hash_matches_manual(self) -> None:
        """The hash should match a manual sha256 of the canonical string."""
        node = self._make_function_node(params=[
            MockNode(type="identifier"),
            MockNode(type="identifier"),
        ])
        sid = self.analyzer.compute_stable_id(node, kind="function")
        # Manual (stable_id v6 / ADR-0035 §1): the single formula is
        # kind:param_count:arity_flags:decorators:containing_stable_id
        # :name:qualified_name:occurrence_index
        # Here kind=function, param_count=2, arity=False,False,False,
        # decorators="", containing="", name="", qualified_name="",
        # occurrence_index=0 (the v6 carrier default). The four empty
        # decorators/containing/name/qualified_name slots give the run of
        # ":" between the arity flags and the trailing "0".
        expected_sig = "function:2:False,False,False:::::0"
        expected_hash = hashlib.sha256(expected_sig.encode()).hexdigest()[:16]
        assert sid == f"sha256:{expected_hash}"

    def test_name_disambiguates_same_shape(self) -> None:
        """Phase 6 PR3 (INV-bazij): two same-shape symbols with different
        names produce different stable_ids when name is passed."""
        node = self._make_function_node()
        sid_a = self.analyzer.compute_stable_id(node, kind="function", name="poll_ci")
        sid_b = self.analyzer.compute_stable_id(node, kind="function", name="do_merge")
        assert sid_a != sid_b

    def test_qualified_name_disambiguates(self) -> None:
        """Phase 6 PR3 (INV-bazij): same name, different qualified_name
        (e.g., same method name in two different classes) produces
        different stable_ids."""
        node = self._make_function_node()
        sid_a = self.analyzer.compute_stable_id(
            node, kind="method", name="run", qualified_name="ClassA.run",
        )
        sid_b = self.analyzer.compute_stable_id(
            node, kind="method", name="run", qualified_name="ClassB.run",
        )
        assert sid_a != sid_b

    def test_name_defaults_back_compatible(self) -> None:
        """Calls without name= remain valid (back-compat for legacy
        analyzers that haven't migrated yet)."""
        node = self._make_function_node()
        sid_legacy = self.analyzer.compute_stable_id(node, kind="function")
        sid_explicit_empty = self.analyzer.compute_stable_id(
            node, kind="function", name="", qualified_name="",
        )
        assert sid_legacy == sid_explicit_empty

    def test_decorators_extracted(self) -> None:
        """Decorator names should be included in the hash."""
        decorator = MockNode(type="decorator", children=[
            MockNode(type="identifier", text=b"staticmethod"),
        ])
        node = self._make_function_node(decorators=[decorator])
        sid_with = self.analyzer.compute_stable_id(node, kind="function")

        node_without = self._make_function_node()
        sid_without = self.analyzer.compute_stable_id(node_without, kind="function")
        assert sid_with != sid_without

    def test_decorator_names_sorted(self) -> None:
        """Decorators should be sorted so order doesn't matter."""
        dec_a = MockNode(type="decorator", children=[
            MockNode(type="identifier", text=b"alpha"),
        ])
        dec_b = MockNode(type="decorator", children=[
            MockNode(type="identifier", text=b"beta"),
        ])
        node_ab = self._make_function_node(decorators=[dec_a, dec_b])
        node_ba = self._make_function_node(decorators=[dec_b, dec_a])
        assert (
            self.analyzer.compute_stable_id(node_ab, kind="function")
            == self.analyzer.compute_stable_id(node_ba, kind="function")
        )


class TestFindParamsNode:
    """Tests for TreeSitterAnalyzer._find_params_node()."""

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()

    def test_finds_parameters(self) -> None:
        params = MockNode(type="parameters", children=[])
        node = MockNode(type="function_definition", children=[
            MockNode(type="identifier"),
            params,
        ])
        assert self.analyzer._find_params_node(node) is params

    def test_finds_formal_parameters(self) -> None:
        params = MockNode(type="formal_parameters", children=[])
        node = MockNode(type="method_declaration", children=[
            MockNode(type="identifier"),
            params,
        ])
        assert self.analyzer._find_params_node(node) is params

    def test_returns_none_when_missing(self) -> None:
        node = MockNode(type="class_definition", children=[
            MockNode(type="identifier"),
            _make_tree("block", MockNode(type="pass_statement")),
        ])
        assert self.analyzer._find_params_node(node) is None


class TestExtractDecoratorNames:
    """Tests for TreeSitterAnalyzer._extract_decorator_names()."""

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()

    def test_simple_decorator(self) -> None:
        """@staticmethod extracts 'staticmethod'."""
        node = MockNode(type="function_definition", children=[
            MockNode(type="decorator", children=[
                MockNode(type="identifier", text=b"staticmethod"),
            ]),
            MockNode(type="identifier", text=b"func"),
        ])
        assert self.analyzer._extract_decorator_names(node) == ["staticmethod"]

    def test_annotation_node_type(self) -> None:
        """Java-style @Override extracts 'Override'."""
        node = MockNode(type="method_declaration", children=[
            MockNode(type="annotation", children=[
                MockNode(type="identifier", text=b"Override"),
            ]),
            MockNode(type="identifier", text=b"run"),
        ])
        assert self.analyzer._extract_decorator_names(node) == ["Override"]

    def test_no_decorators(self) -> None:
        node = MockNode(type="function_definition", children=[
            MockNode(type="identifier", text=b"func"),
        ])
        assert self.analyzer._extract_decorator_names(node) == []

    def test_dotted_decorator(self) -> None:
        """@app.route extracts 'route' (last segment)."""
        node = MockNode(type="function_definition", children=[
            MockNode(type="decorator", children=[
                MockNode(type="attribute", children=[
                    MockNode(type="identifier", text=b"app"),
                    MockNode(type=".", is_named=False),
                    MockNode(type="identifier", text=b"route"),
                ]),
            ]),
            MockNode(type="identifier", text=b"handler"),
        ])
        assert self.analyzer._extract_decorator_names(node) == ["route"]

    def test_call_decorator(self) -> None:
        """@decorator(args) extracts 'decorator'."""
        node = MockNode(type="function_definition", children=[
            MockNode(type="decorator", children=[
                MockNode(type="call", children=[
                    MockNode(type="identifier", text=b"property"),
                    MockNode(type="argument_list", children=[]),
                ]),
            ]),
            MockNode(type="identifier", text=b"value"),
        ])
        assert self.analyzer._extract_decorator_names(node) == ["property"]

    def test_decorator_without_identifier(self) -> None:
        """A malformed decorator node without identifier returns empty name."""
        node = MockNode(type="function_definition", children=[
            MockNode(type="decorator", children=[
                MockNode(type="@", is_named=False),
            ]),
            MockNode(type="identifier", text=b"func"),
        ])
        # Empty name is filtered since _decorator_node_name returns ""
        assert self.analyzer._extract_decorator_names(node) == []


# ---------------------------------------------------------------------------
# Field type registry and resolve_receiver_type tests
# ---------------------------------------------------------------------------


class TestFileAnalysisFieldTypes:
    """Tests for FileAnalysis.class_field_types field."""

    def test_defaults_to_empty_dict(self) -> None:
        """class_field_types defaults to empty dict."""
        analysis = FileAnalysis()
        assert analysis.class_field_types == {}

    def test_independent_instances(self) -> None:
        """Each FileAnalysis gets its own class_field_types dict."""
        a = FileAnalysis()
        b = FileAnalysis()
        a.class_field_types["Foo"] = {"bar": "Baz"}
        assert b.class_field_types == {}


class TestFieldTypeRegistryAggregation:
    """Tests for field type aggregation in analyze()."""

    def test_aggregates_across_files(self, tmp_path: Path) -> None:
        """analyze() merges class_field_types from multiple files."""
        (tmp_path / "server.stub").write_text("content")
        (tmp_path / "app.stub").write_text("content")

        analyzer = FieldTypeAnalyzer()
        analyzer.analyze(tmp_path)

        # Registry is cleared after analyze(), so test via a subclass
        # that captures it during Pass 2.
        captured = {}

        class CapturingAnalyzer(FieldTypeAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._field_type_registry)
                return []

        analyzer = CapturingAnalyzer()
        analyzer.analyze(tmp_path)

        assert "Server" in captured
        assert captured["Server"] == {"app": "App", "db": "Database"}
        assert "App" in captured
        assert captured["App"] == {"handler": "Handler"}

    def test_registry_cleared_after_analyze(self, tmp_path: Path) -> None:
        """_field_type_registry is cleared after analyze() completes."""
        (tmp_path / "server.stub").write_text("content")

        analyzer = FieldTypeAnalyzer()
        analyzer.analyze(tmp_path)
        assert analyzer._field_type_registry == {}

    def test_first_field_type_wins(self, tmp_path: Path) -> None:
        """When two files declare the same class+field, first-seen wins."""
        (tmp_path / "a.stub").write_text("content")
        (tmp_path / "b.stub").write_text("content")

        captured = {}

        class DuplicateAnalyzer(StubAnalyzer):
            call_count = 0

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = super().extract_symbols_from_file(
                    tree, source, file_path, rel_path, run,
                )
                DuplicateAnalyzer.call_count += 1
                # Both files claim Widget.size, with different types
                if DuplicateAnalyzer.call_count == 1:
                    analysis.class_field_types = {
                        "Widget": {"size": "Size2D"},
                    }
                else:
                    analysis.class_field_types = {
                        "Widget": {"size": "Dimension"},
                    }
                return analysis

            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._field_type_registry)
                return []

        DuplicateAnalyzer.call_count = 0
        analyzer = DuplicateAnalyzer()
        analyzer.analyze(tmp_path)

        # setdefault means first-seen wins
        assert captured["Widget"]["size"] in ("Size2D", "Dimension")

    def test_empty_when_no_field_types(self, tmp_path: Path) -> None:
        """Registry stays empty when no subclass populates class_field_types."""
        (tmp_path / "hello.stub").write_text("content")

        captured = {}

        class CapturingAnalyzer(StubAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._field_type_registry)
                return []

        analyzer = CapturingAnalyzer()
        analyzer.analyze(tmp_path)
        assert captured == {}


class TestMethodReturnTypeRegistryAggregation:
    """Tests for the WI-titor / INV-dihos method return-type aggregation
    step in ``TreeSitterAnalyzer.analyze()`` (§4c).

    Mirrors ``TestFieldTypeRegistryAggregation`` because the two
    registries follow the same first-writer-wins convention and the
    same end-of-analyze cleanup pattern.
    """

    def _make_return_type_analyzer(self):
        """A StubAnalyzer subclass that populates FileAnalysis.method_return_types
        based on filename, like FieldTypeAnalyzer does for class_field_types."""

        class MethodReturnTypeAnalyzer(StubAnalyzer):
            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = super().extract_symbols_from_file(
                    tree, source, file_path, rel_path, run,
                )
                stem = Path(rel_path).stem
                if stem == "engine":
                    analysis.method_return_types = {
                        "Engine::query": "Outcome",
                        "Engine::new": "Engine",
                    }
                elif stem == "outcome":
                    analysis.method_return_types = {
                        "Outcome::execute": "i32",
                    }
                return analysis

        return MethodReturnTypeAnalyzer

    def test_aggregates_across_files(self, tmp_path: Path) -> None:
        """analyze() merges method_return_types from multiple files into
        self._method_return_type_registry, available to Pass-2 callers."""
        (tmp_path / "engine.stub").write_text("content")
        (tmp_path / "outcome.stub").write_text("content")

        captured: dict[str, str] = {}
        ReturnTypeAnalyzer = self._make_return_type_analyzer()

        class CapturingAnalyzer(ReturnTypeAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._method_return_type_registry)
                return []

        CapturingAnalyzer().analyze(tmp_path)

        assert captured == {
            "Engine::query": "Outcome",
            "Engine::new": "Engine",
            "Outcome::execute": "i32",
        }

    def test_registry_cleared_after_analyze(self, tmp_path: Path) -> None:
        """_method_return_type_registry is cleared after analyze()
        to prevent stale data leaking across runs (same hygiene as
        _field_type_registry)."""
        (tmp_path / "engine.stub").write_text("content")

        analyzer = self._make_return_type_analyzer()()
        analyzer.analyze(tmp_path)
        assert analyzer._method_return_type_registry == {}

    def test_first_writer_wins(self, tmp_path: Path) -> None:
        """When two files declare the same ``Receiver::method``, first-seen wins."""
        (tmp_path / "a.stub").write_text("content")
        (tmp_path / "b.stub").write_text("content")

        captured: dict[str, str] = {}

        class DuplicateAnalyzer(StubAnalyzer):
            call_count = 0

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = super().extract_symbols_from_file(
                    tree, source, file_path, rel_path, run,
                )
                DuplicateAnalyzer.call_count += 1
                # Both files claim Widget::resize, with different types.
                if DuplicateAnalyzer.call_count == 1:
                    analysis.method_return_types = {"Widget::resize": "Size2D"}
                else:
                    analysis.method_return_types = {"Widget::resize": "Dimension"}
                return analysis

            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._method_return_type_registry)
                return []

        DuplicateAnalyzer.call_count = 0
        DuplicateAnalyzer().analyze(tmp_path)

        # setdefault means first-seen wins (whichever file the analyzer
        # visited first — the test doesn't control file iteration order,
        # so either value is acceptable as long as only one survives).
        assert captured["Widget::resize"] in ("Size2D", "Dimension")

    def test_empty_when_no_return_types(self, tmp_path: Path) -> None:
        """Registry stays empty when no subclass populates method_return_types."""
        (tmp_path / "hello.stub").write_text("content")

        captured: dict[str, str] = {}

        class CapturingAnalyzer(StubAnalyzer):
            def extract_edges_from_file(self, tree, source, file_path,
                                        rel_path, local_symbols,
                                        global_symbols, run,
                                        import_aliases, resolver):
                captured.update(self._method_return_type_registry)
                return []

        CapturingAnalyzer().analyze(tmp_path)
        assert captured == {}


class TestResolveVarFieldChain:
    """TreeSitterAnalyzer.resolve_var_field_chain() — WI-dizag shape B.

    The var-rooted sibling of resolve_receiver_type: same field-registry walk,
    but the root's type comes from ``var_types`` rather than ``enclosing_type``.
    Every None branch is exercised here so the production wiring in rust.py
    (which only takes the happy path on the fixture) is not the coverage floor.
    """

    def setup_method(self) -> None:
        self.analyzer = StubAnalyzer()
        self.analyzer._field_type_registry = {
            "Holder": {"f": "File", "inner": "Inner"},
            "Inner": {"g": "Socket"},
        }

    def _chain(self, chain: list[str]) -> tuple[MagicMock, bytes]:
        # Reuse the field_expression mock builder from the sibling test class.
        return TestResolveReceiverType._make_field_expr(self, chain)

    def test_var_field_resolves(self) -> None:
        """h.f resolves to File via var_types[h]=Holder, Holder.f=File."""
        node, source = self._chain(["h", "f"])
        assert self.analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) == "File"

    def test_nested_var_field_chain(self) -> None:
        """h.inner.g walks Holder -> Inner -> Socket."""
        node, source = self._chain(["h", "inner", "g"])
        assert self.analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) == "Socket"

    def test_untracked_root_returns_none(self) -> None:
        node, source = self._chain(["h", "f"])
        assert self.analyzer.resolve_var_field_chain(node, source, {}) is None

    def test_unknown_field_returns_none(self) -> None:
        node, source = self._chain(["h", "missing"])
        assert self.analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) is None

    def test_intermediate_type_not_in_registry_returns_none(self) -> None:
        self.analyzer._field_type_registry = {"Holder": {"inner": "Inner"}}
        node, source = self._chain(["h", "inner", "g"])
        assert self.analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) is None

    def test_bare_identifier_is_left_to_the_plain_var_lookup(self) -> None:
        """A root with no field chain has empty segments and is not answered
        here — Strategy 1.8's plain var_types lookup owns that case."""
        root = MagicMock()
        root.type = "identifier"
        root.start_byte, root.end_byte = 0, 1
        assert self.analyzer.resolve_var_field_chain(
            root, b"h", {"h": "Holder"},
        ) is None

    def test_non_identifier_root_returns_none(self) -> None:
        """A chain rooted at self (or any non-identifier) is the sibling's job."""
        node, source = self._chain(["self", "f"])
        # 'self' is an identifier token here, but a call_expression root is not:
        call_root = MagicMock()
        call_root.type = "call_expression"
        parent = MagicMock()
        parent.type = "field_expression"
        field = MagicMock()
        field.start_byte, field.end_byte = 0, 1
        parent.child_by_field_name = lambda n: (
            call_root if n == "value" else field if n == "field" else None
        )
        assert self.analyzer.resolve_var_field_chain(
            parent, b"x.f", {"x": "Holder"},
        ) is None

    def test_empty_registry_returns_none(self) -> None:
        self.analyzer._field_type_registry = {}
        node, source = self._chain(["h", "f"])
        assert self.analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) is None

    def test_no_registry_attr_returns_none(self) -> None:
        analyzer = StubAnalyzer()
        node, source = self._chain(["h", "f"])
        assert analyzer.resolve_var_field_chain(
            node, source, {"h": "Holder"},
        ) is None


class TestResolveReceiverType:
    """Tests for TreeSitterAnalyzer.resolve_receiver_type()."""

    def setup_method(self) -> None:
        """Set up analyzer with a populated field type registry."""
        self.analyzer = StubAnalyzer()
        self.analyzer._field_type_registry = {
            "Server": {"app": "App", "db": "Database"},
            "App": {"handler": "Handler"},
        }

    def _make_field_expr(self, chain: list[str]) -> tuple[MagicMock, bytes]:
        """Build a mock field_expression chain and matching source bytes.

        chain=["self", "app", "handler"] produces:
          source = b"self.app.handler"
          field_expression(value=field_expression(value="self", field="app"),
                           field="handler")

        Returns (top_node, source_bytes).
        """
        # Build source: "self.app.handler"
        source_str = ".".join(chain)
        source = source_str.encode()

        # Compute byte offsets for each token in "self.app.handler"
        offsets: list[tuple[int, int]] = []
        pos = 0
        for token in chain:
            offsets.append((pos, pos + len(token)))
            pos += len(token) + 1  # +1 for the dot

        # Build root node (the self keyword)
        root = MagicMock()
        root.type = "identifier"
        root.start_byte = offsets[0][0]
        root.end_byte = offsets[0][1]

        current = root
        for i, _field_name in enumerate(chain[1:], start=1):
            parent = MagicMock()
            parent.type = "field_expression"

            field_node = MagicMock()
            field_node.start_byte = offsets[i][0]
            field_node.end_byte = offsets[i][1]

            def make_child_fn(v, f):
                def child_by_field_name(name):
                    if name == "value":
                        return v
                    if name == "field":
                        return f
                    return None
                return child_by_field_name

            parent.child_by_field_name = make_child_fn(current, field_node)
            current = parent

        return current, source

    def test_simple_self_field(self) -> None:
        """self.app resolves to App type."""
        node, source = self._make_field_expr(["self", "app"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result == "App"

    def test_nested_chain(self) -> None:
        """self.app.handler resolves through chain: Server→App→Handler."""
        node, source = self._make_field_expr(["self", "app", "handler"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result == "Handler"

    def test_non_self_root_returns_none(self) -> None:
        """Non-self receiver root returns None."""
        node, source = self._make_field_expr(["other", "app"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_unknown_field_returns_none(self) -> None:
        """Unknown field name returns None."""
        node, source = self._make_field_expr(["self", "unknown"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_intermediate_type_not_in_registry(self) -> None:
        """self.app.handler fails when App is not in the registry."""
        # Only Server→app→App is in registry; App→handler isn't
        self.analyzer._field_type_registry = {
            "Server": {"app": "App"},
            # App is NOT in registry — so the second step fails
        }
        node, source = self._make_field_expr(["self", "app", "handler"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_no_enclosing_type_returns_none(self) -> None:
        """None enclosing_type returns None."""
        node, source = self._make_field_expr(["self", "app"])
        result = self.analyzer.resolve_receiver_type(node, source, None)
        assert result is None

    def test_empty_registry_returns_none(self) -> None:
        """Empty registry returns None."""
        self.analyzer._field_type_registry = {}
        node, source = self._make_field_expr(["self", "app"])
        result = self.analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_no_registry_attr_returns_none(self) -> None:
        """Missing _field_type_registry attribute returns None."""
        analyzer = StubAnalyzer()  # Fresh, no registry set
        node, source = self._make_field_expr(["self", "app"])
        result = analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_custom_self_keywords(self) -> None:
        """Analyzer with self_keywords={'this'} resolves 'this.app'."""

        class ThisAnalyzer(StubAnalyzer):
            self_keywords = frozenset({"this"})

        analyzer = ThisAnalyzer()
        analyzer._field_type_registry = {
            "Server": {"app": "App"},
        }
        node, source = self._make_field_expr(["this", "app"])
        result = analyzer.resolve_receiver_type(node, source, "Server")
        assert result == "App"

    def test_custom_self_keywords_rejects_self(self) -> None:
        """Analyzer with self_keywords={'this'} rejects 'self.app'."""

        class ThisAnalyzer(StubAnalyzer):
            self_keywords = frozenset({"this"})

        analyzer = ThisAnalyzer()
        analyzer._field_type_registry = {
            "Server": {"app": "App"},
        }
        node, source = self._make_field_expr(["self", "app"])
        result = analyzer.resolve_receiver_type(node, source, "Server")
        assert result is None

    def test_field_child_missing_returns_none(self) -> None:
        """If field_expression has no 'field' child, returns None."""
        node = MagicMock()
        node.type = "field_expression"
        node.child_by_field_name = lambda name: None
        result = self.analyzer.resolve_receiver_type(node, b"", "Server")
        assert result is None

    def test_value_child_missing_returns_none(self) -> None:
        """If field_expression has no 'value' child, returns None."""
        field_mock = MagicMock()
        field_mock.start_byte = 0
        field_mock.end_byte = 3

        node = MagicMock()
        node.type = "field_expression"

        def child_by_field_name(name):
            if name == "field":
                return field_mock
            return None
        node.child_by_field_name = child_by_field_name

        result = self.analyzer.resolve_receiver_type(node, b"app", "Server")
        assert result is None


class TestSelfKeywordsDefault:
    """Tests for self_keywords class attribute."""

    def test_default_is_self(self) -> None:
        """Default self_keywords is frozenset({'self'})."""
        assert StubAnalyzer.self_keywords == frozenset({"self"})
