# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Haskell analyzer.

Haskell analysis uses tree-sitter to extract:
- Symbols: function, data type, type class, instance
- Edges: calls, imports

Test coverage includes:
- Function detection (with and without type signatures)
- Data type detection (including records)
- Type class detection
- Instance detection
- Import statements
- Function calls
- Two-pass cross-file resolution
"""
from pathlib import Path
from unittest.mock import patch

import pytest




def make_haskell_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Haskell file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestHaskellAnalyzerAvailability:
    """Tests for tree-sitter-haskell availability detection."""

    def test_is_haskell_tree_sitter_available(self) -> None:
        """Check if tree-sitter-haskell is detected."""
        from hypergumbo_lang_common.haskell import is_haskell_tree_sitter_available

        # Should be True since we installed tree-sitter-haskell
        assert is_haskell_tree_sitter_available() is True


class TestHaskellFunctionDetection:
    """Tests for Haskell function symbol extraction."""

    def test_detect_function_with_signature(self, tmp_path: Path) -> None:
        """Detect function with type signature."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
add :: Int -> Int -> Int
add x y = x + y
""")

        result = analyze_haskell(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "haskell"

    def test_detect_function_without_signature(self, tmp_path: Path) -> None:
        """Detect function without type signature."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
greet name = "Hello " ++ name
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "greet"), None)
        assert func is not None
        assert func.kind == "function"

    def test_detect_main_function(self, tmp_path: Path) -> None:
        """Detect main function with do block."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
main :: IO ()
main = do
    putStrLn "Hello"
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "main"), None)
        assert func is not None
        assert func.kind == "function"


class TestHaskellDataTypeDetection:
    """Tests for Haskell data type symbol extraction."""

    def test_detect_simple_data_type(self, tmp_path: Path) -> None:
        """Detect simple data type definition."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Types.hs", """
data Color = Red | Green | Blue
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        dtype = next((s for s in symbols if s.name == "Color"), None)
        assert dtype is not None
        assert dtype.kind == "data"

    def test_detect_record_data_type(self, tmp_path: Path) -> None:
        """Detect record data type with fields."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Types.hs", """
data Person = Person { name :: String, age :: Int }
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        dtype = next((s for s in symbols if s.name == "Person"), None)
        assert dtype is not None
        assert dtype.kind == "data"


class TestHaskellTypeClassDetection:
    """Tests for Haskell type class symbol extraction."""

    def test_detect_type_class(self, tmp_path: Path) -> None:
        """Detect type class definition."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Classes.hs", """
class Printable a where
    printMe :: a -> String
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        tclass = next((s for s in symbols if s.name == "Printable"), None)
        assert tclass is not None
        assert tclass.kind == "class"

    def test_detect_instance(self, tmp_path: Path) -> None:
        """Detect type class instance."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Instances.hs", """
data Person = Person { name :: String }

class Printable a where
    printMe :: a -> String

instance Printable Person where
    printMe p = name p
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        # Instance should be detected
        instance = next((s for s in symbols if "Printable" in s.name and "Person" in s.name), None)
        assert instance is not None
        assert instance.kind == "instance"


class TestHaskellImportEdges:
    """Tests for Haskell import edge extraction."""

    def test_detect_simple_import(self, tmp_path: Path) -> None:
        """Detect simple import statement."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
import Data.List

main = print (sort [3, 1, 2])
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert any("Data.List" in e.dst for e in import_edges)

    def test_detect_qualified_import(self, tmp_path: Path) -> None:
        """Detect qualified import statement."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
import qualified Data.Map as M

main = print M.empty
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert any("Data.Map" in e.dst for e in import_edges)


class TestHaskellCallEdges:
    """Tests for Haskell function call edge extraction."""

    def test_detect_function_call(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
greet name = "Hello " ++ name

main = putStrLn (greet "World")
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # main calls greet
        assert any("greet" in e.dst for e in call_edges)


class TestHaskellCrossFileResolution:
    """Tests for two-pass cross-file call resolution."""

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Calls to functions in other files are resolved."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Utils.hs", """
module Utils where

helper x = x + 1
""")

        make_haskell_file(tmp_path, "Main.hs", """
module Main where

import Utils

main = print (helper 5)
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # Call to helper should be resolved
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestHaskellWhereClauseScoping:
    """Tests that where-clause bindings are NOT extracted as top-level symbols."""

    def test_where_clause_bindings_excluded(self, tmp_path: Path) -> None:
        """Local bindings in where clauses should not become top-level symbols."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
topLevel :: Int -> Int
topLevel x = helper x + 1
  where
    helper y = y * 2
    localBind = 42

main :: IO ()
main = print (topLevel 5)
""")
        result = analyze_haskell(tmp_path)
        non_file_symbols = [s for s in result.symbols if s.kind != "file"]
        names = {s.name for s in non_file_symbols}

        # Module-level functions should be extracted
        assert "topLevel" in names
        assert "main" in names

        # Where-clause local bindings should NOT be extracted
        assert "helper" not in names, (
            f"Where-clause binding 'helper' should not be a top-level symbol, "
            f"but found: {names}"
        )
        assert "localBind" not in names, (
            f"Where-clause binding 'localBind' should not be a top-level symbol, "
            f"but found: {names}"
        )

    def test_let_in_do_bindings_excluded(self, tmp_path: Path) -> None:
        """Local let bindings in do-notation should not become top-level symbols."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
main :: IO ()
main = do
    let result = 42
    print result
""")
        result = analyze_haskell(tmp_path)
        non_file_symbols = [s for s in result.symbols if s.kind != "file"]
        names = {s.name for s in non_file_symbols}

        assert "main" in names
        assert "result" not in names, (
            "Do-notation let binding 'result' should not be a top-level symbol"
        )


class TestHaskellEdgeCases:
    """Edge case tests for Haskell analyzer."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty Haskell file produces no symbols."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Empty.hs", "")

        result = analyze_haskell(tmp_path)

        assert not result.skipped
        # Only file symbol should exist
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Bad.hs", """
foo x =
    -- missing body
""")

        result = analyze_haskell(tmp_path)

        # Should not crash
        assert not result.skipped

    def test_no_haskell_files(self, tmp_path: Path) -> None:
        """Directory with no Haskell files returns empty result."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "main.py", "print('hello')")

        result = analyze_haskell(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestHaskellSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """add x y = x + y
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.span.start_line == 1


class TestHaskellAnalyzeFallback:
    """Tests for fallback when tree-sitter-haskell is unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-haskell not available."""
        from hypergumbo_lang_common import haskell

        make_haskell_file(tmp_path, "Main.hs", "main = print 1")

        with patch.object(
            haskell._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="haskell analysis skipped"):
                result = haskell.analyze_haskell(tmp_path)

        assert result.skipped
        assert "not available" in result.skip_reason
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "haskell-v1"


class TestHaskellSignatureExtraction:
    """Tests for Haskell function signature extraction."""

    def test_simple_type_signature(self, tmp_path: Path) -> None:
        """Extract type signature from function with type annotation."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
add :: Int -> Int -> Int
add x y = x + y
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == ":: Int -> Int -> Int"

    def test_io_type_signature(self, tmp_path: Path) -> None:
        """Extract type signature with IO monad."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
greet :: String -> IO ()
greet name = putStrLn name
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) == 1
        assert funcs[0].signature == ":: String -> IO ()"

    def test_no_signature(self, tmp_path: Path) -> None:
        """Function without type signature has None signature."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
helper x = x + 1
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "helper"]
        assert len(funcs) == 1
        assert funcs[0].signature is None


class TestHaskellExternalEdges:
    """Tests for external edge creation for I/O boundary matching."""

    def test_io_primitives_not_excluded_from_edges(self, tmp_path: Path) -> None:
        """putStrLn and print should produce call edges, not be silently dropped."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
greet :: String -> IO ()
greet name = putStrLn name

main :: IO ()
main = do
    print "hello"
    greet "world"
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # main should have a call edge to print
        assert any("print" in e.dst for e in call_edges), (
            f"Expected call edge to 'print', got: {[e.dst for e in call_edges]}"
        )
        # greet should have a call edge to putStrLn
        assert any("putStrLn" in e.dst for e in call_edges), (
            f"Expected call edge to 'putStrLn', got: {[e.dst for e in call_edges]}"
        )

    def test_qualified_unresolved_call_uses_module_hint(self, tmp_path: Path) -> None:
        """Qualified unresolved calls should carry module name, not '?'."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
module Main where

import qualified System.IO as SIO

doRead :: IO ()
doRead = SIO.hGetContents SIO.stdin
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have an external edge with System.IO as module hint
        hget_edges = [e for e in call_edges if "hGetContents" in e.dst]
        assert len(hget_edges) >= 1, (
            f"Expected edge to hGetContents, got: {[e.dst for e in call_edges]}"
        )
        # The module hint should be System.IO, not ?
        edge = hget_edges[0]
        assert "System.IO" in edge.dst, (
            f"Expected 'System.IO' in edge dst, got: {edge.dst}"
        )

    def test_readFile_creates_external_edge(self, tmp_path: Path) -> None:
        """Unresolved call to readFile creates an external edge matchable by I/O catalog."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
main :: IO ()
main = do
    content <- readFile "input.txt"
    writeFile "output.txt" content
""")

        result = analyze_haskell(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have edges to readFile and writeFile
        assert any("readFile" in e.dst for e in call_edges), (
            f"Expected edge to readFile, got: {[e.dst for e in call_edges]}"
        )
        assert any("writeFile" in e.dst for e in call_edges), (
            f"Expected edge to writeFile, got: {[e.dst for e in call_edges]}"
        )


    def test_unresolved_edges_matchable_by_io_catalog(self, tmp_path: Path) -> None:
        """External edges for unqualified calls should be matchable by I/O catalog.

        The module_hint in the symbol ID must be 'external' (not '?') so
        lookup_with_module falls back to unfiltered short-name matching.
        """
        from hypergumbo_lang_common.haskell import analyze_haskell
        from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries

        make_haskell_file(tmp_path, "Main.hs", """
main :: IO ()
main = do
    content <- readFile "input.txt"
    writeFile "output.txt" content
    putStrLn "done"
""")

        result = analyze_haskell(tmp_path)
        catalog = load_catalog("haskell")
        tagged = tag_io_boundaries(result.edges, {"haskell": catalog})
        assert tagged >= 2, (
            f"Expected at least 2 I/O tagged edges (readFile, writeFile), "
            f"got {tagged}. External edge dsts: "
            f"{[e.dst for e in result.edges if 'external' in e.dst or '?' in e.dst]}"
        )


class TestHaskellImportAliases:
    """Tests for import alias extraction and qualified call resolution."""

    def test_extracts_import_alias(self, tmp_path: Path) -> None:
        """Extracts import alias from 'import qualified as' statement."""
        from hypergumbo_lang_common.haskell import _extract_import_aliases

        import tree_sitter
        import tree_sitter_haskell

        lang = tree_sitter.Language(tree_sitter_haskell.language())
        parser = tree_sitter.Parser(lang)

        hs_file = tmp_path / "Main.hs"
        hs_file.write_text("""
module Main where

import qualified Data.Map as M
import qualified Data.List as L

main = print "hello"
""")

        source = hs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree, source)

        # Both aliases should be extracted
        assert "M" in aliases
        assert aliases["M"] == "Data.Map"
        assert "L" in aliases
        assert aliases["L"] == "Data.List"

    def test_qualified_call_uses_alias(self, tmp_path: Path) -> None:
        """Qualified call resolution uses import alias for path hint."""
        from hypergumbo_lang_common.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
module Main where

import qualified Data.Map as M

lookup_ :: String -> Int
lookup_ key = M.lookup key M.empty
""")

        result = analyze_haskell(tmp_path)

        # Should have call edge (we can't verify path_hint directly but can verify it doesn't crash)
        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "lookup_" for s in symbols)
