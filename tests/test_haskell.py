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




def make_haskell_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Haskell file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestHaskellAnalyzerAvailability:
    """Tests for tree-sitter-haskell availability detection."""

    def test_is_haskell_tree_sitter_available(self) -> None:
        """Check if tree-sitter-haskell is detected."""
        from hypergumbo.analyze.haskell import is_haskell_tree_sitter_available

        # Should be True since we installed tree-sitter-haskell
        assert is_haskell_tree_sitter_available() is True


class TestHaskellFunctionDetection:
    """Tests for Haskell function symbol extraction."""

    def test_detect_function_with_signature(self, tmp_path: Path) -> None:
        """Detect function with type signature."""
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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


class TestHaskellEdgeCases:
    """Edge case tests for Haskell analyzer."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty Haskell file produces no symbols."""
        from hypergumbo.analyze.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Empty.hs", "")

        result = analyze_haskell(tmp_path)

        assert not result.skipped
        # Only file symbol should exist
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo.analyze.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Bad.hs", """
foo x =
    -- missing body
""")

        result = analyze_haskell(tmp_path)

        # Should not crash
        assert not result.skipped

    def test_no_haskell_files(self, tmp_path: Path) -> None:
        """Directory with no Haskell files returns empty result."""
        from hypergumbo.analyze.haskell import analyze_haskell

        make_haskell_file(tmp_path, "main.py", "print('hello')")

        result = analyze_haskell(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestHaskellSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo.analyze.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """add x y = x + y
""")

        result = analyze_haskell(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.span.start_line == 1


class TestHaskellAnalyzeFallback:
    """Tests for fallback when tree-sitter-haskell is unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        """Returns skipped result when tree-sitter-haskell not available."""
        from hypergumbo.analyze import haskell

        # Mock tree-sitter-haskell as unavailable
        monkeypatch.setattr(haskell, "is_haskell_tree_sitter_available", lambda: False)

        make_haskell_file(tmp_path, "Main.hs", "main = print 1")

        result = haskell.analyze_haskell(tmp_path)

        assert result.skipped
        assert "tree-sitter-haskell" in result.skip_reason
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "haskell-v1"


class TestHaskellSignatureExtraction:
    """Tests for Haskell function signature extraction."""

    def test_simple_type_signature(self, tmp_path: Path) -> None:
        """Extract type signature from function with type annotation."""
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

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
        from hypergumbo.analyze.haskell import analyze_haskell

        make_haskell_file(tmp_path, "Main.hs", """
helper x = x + 1
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "helper"]
        assert len(funcs) == 1
        assert funcs[0].signature is None
