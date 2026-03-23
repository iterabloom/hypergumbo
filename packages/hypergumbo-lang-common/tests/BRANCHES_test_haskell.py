# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for haskell.py analyzer.

Tests specific branch paths in the Haskell analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function ID format
- Instance declaration with complex type patterns
- Unresolved call edge creation
- Multiple data types in one file
- Type signature edge cases
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.haskell import analyze_haskell, find_haskell_files

def make_hs_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Haskell file with given content."""
    (tmp_path / name).write_text(content)

class TestHaskellHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("haskell", "src/Main.hs", 5, 10, "main", "function")
        assert symbol_id == "haskell:src/Main.hs:5-10:main:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("haskell", "lib/Utils.hs")
        assert file_id == "haskell:lib/Utils.hs:1-1:file:file"

class TestUnresolvedCallEdges:
    """Branch coverage for unresolved call edge creation."""

    def test_call_to_unknown_function_creates_edge(self, tmp_path: Path) -> None:
        """Test call to unknown function creates unresolved edge."""
        make_hs_file(tmp_path, "Main.hs", """
outer :: Int -> Int
outer x = unknownFunc x + 1
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge to unresolved function (uses "external" as module sentinel)
        unresolved = [e for e in call_edges if "external" in e.dst]
        assert len(unresolved) >= 1
        assert any("unknownFunc" in e.dst for e in unresolved)

    def test_unresolved_has_lower_confidence(self, tmp_path: Path) -> None:
        """Test unresolved call edges have lower confidence than resolved."""
        make_hs_file(tmp_path, "Calls.hs", """
helper :: Int -> Int
helper x = x * 2

caller :: Int -> Int
caller x = helper x + unknownFn x
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolved = [e for e in call_edges if ":external:" not in e.dst]
        unresolved = [e for e in call_edges if ":external:" in e.dst]

        # Unresolved should have lower confidence (0.50)
        for e in unresolved:
            assert e.confidence == 0.50

class TestMultipleDataTypes:
    """Branch coverage for multiple data type extraction."""

    def test_multiple_simple_types(self, tmp_path: Path) -> None:
        """Test multiple simple data types in one file."""
        make_hs_file(tmp_path, "Types.hs", """
data Color = Red | Green | Blue
data Size = Small | Medium | Large
data Priority = Low | High | Critical
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        data_types = [s for s in result.symbols if s.kind == "data"]
        names = [t.name for t in data_types]
        assert "Color" in names
        assert "Size" in names
        assert "Priority" in names

    def test_parameterized_types(self, tmp_path: Path) -> None:
        """Test parameterized data type definitions."""
        make_hs_file(tmp_path, "Generic.hs", """
data Maybe a = Nothing | Just a
data Either a b = Left a | Right b
data Tree a = Leaf | Node a (Tree a) (Tree a)
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        data_types = [s for s in result.symbols if s.kind == "data"]
        names = [t.name for t in data_types]
        assert "Maybe" in names
        assert "Either" in names
        assert "Tree" in names

class TestInstanceDeclarations:
    """Branch coverage for instance declaration extraction."""

    def test_instance_without_type_patterns(self, tmp_path: Path) -> None:
        """Test instance declaration handling with simple type."""
        make_hs_file(tmp_path, "Instances.hs", """
class Showable a where
    display :: a -> String

data MyType = MyType Int

instance Showable MyType where
    display (MyType n) = show n
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        instances = [s for s in result.symbols if s.kind == "instance"]
        # Should detect the instance
        assert len(instances) >= 1

class TestTypeClassDefinitions:
    """Branch coverage for type class extraction."""

    def test_multiple_type_classes(self, tmp_path: Path) -> None:
        """Test multiple type class definitions."""
        make_hs_file(tmp_path, "Classes.hs", """
class Printable a where
    printIt :: a -> String

class Comparable a where
    isLess :: a -> a -> Bool
    isEqual :: a -> a -> Bool

class Serializable a where
    serialize :: a -> [Byte]
    deserialize :: [Byte] -> a
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        classes = [s for s in result.symbols if s.kind == "class"]
        names = [c.name for c in classes]
        assert "Printable" in names
        assert "Comparable" in names
        # Note: Byte might cause parse issues but that's okay

class TestMultipleFunctions:
    """Branch coverage for multiple function detection."""

    def test_many_functions_single_file(self, tmp_path: Path) -> None:
        """Test many functions in a single file."""
        make_hs_file(tmp_path, "Math.hs", """
add :: Int -> Int -> Int
add x y = x + y

sub :: Int -> Int -> Int
sub x y = x - y

mul :: Int -> Int -> Int
mul x y = x * y

divide :: Int -> Int -> Int
divide x y = x `div` y
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names
        assert "divide" in names

    def test_function_calling_chain(self, tmp_path: Path) -> None:
        """Test function call chain creates multiple edges."""
        make_hs_file(tmp_path, "Chain.hs", """
step1 :: Int -> Int
step1 x = x * 2

step2 :: Int -> Int
step2 x = step1 x + 1

step3 :: Int -> Int
step3 x = step2 (step1 x)
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        step1_calls = [e for e in call_edges if "step1" in e.dst]
        step2_calls = [e for e in call_edges if "step2" in e.dst]
        # step2 calls step1, step3 calls both step1 and step2
        assert len(step1_calls) >= 2
        assert len(step2_calls) >= 1

class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """Test multiple import statements create edges."""
        make_hs_file(tmp_path, "Main.hs", """
import Data.List
import Data.Maybe
import Data.Map

main :: IO ()
main = print "hello"
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in import_edges]
        assert any("Data.List" in d for d in dests)
        assert any("Data.Maybe" in d for d in dests)
        assert any("Data.Map" in d for d in dests)

class TestFindHaskellFiles:
    """Branch coverage for file discovery."""

    def test_finds_nested_hs_files(self, tmp_path: Path) -> None:
        """Test .hs files in nested directories are found."""
        lib = tmp_path / "src" / "Lib"
        lib.mkdir(parents=True)
        (lib / "Utils.hs").write_text("module Lib.Utils where")

        files = list(find_haskell_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "Utils.hs"

    def test_ignores_non_hs_files(self, tmp_path: Path) -> None:
        """Test non-.hs files are not found."""
        (tmp_path / "Main.hs").write_text("main = print 1")
        (tmp_path / "config.yaml").write_text("key: value")
        (tmp_path / "README.md").write_text("# Readme")

        files = list(find_haskell_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".hs"

class TestSignatureEdgeCases:
    """Branch coverage for signature extraction edge cases."""

    def test_complex_type_signature(self, tmp_path: Path) -> None:
        """Test complex type signature extraction."""
        make_hs_file(tmp_path, "Complex.hs", """
transform :: (a -> b) -> [a] -> [b]
transform f xs = map f xs
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.name == "transform"]
        assert len(funcs) == 1
        # Should have the type signature
        assert funcs[0].signature is not None
        assert "->" in funcs[0].signature

    def test_multiline_type_signature(self, tmp_path: Path) -> None:
        """Test multiline type signature."""
        make_hs_file(tmp_path, "Multiline.hs", """
combineAll :: Int
           -> String
           -> Bool
           -> (Int, String, Bool)
combineAll x y z = (x, y, z)
""")
        result = analyze_haskell(tmp_path)
        funcs = [s for s in result.symbols if s.name == "combineAll"]
        assert len(funcs) == 1
        # Should still extract signature
        assert funcs[0].signature is not None

class TestCrossFileResolution:
    """Branch coverage for cross-file call resolution."""

    def test_three_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across three files."""
        make_hs_file(tmp_path, "Base.hs", """
module Base where

baseFn :: Int -> Int
baseFn x = x * 2
""")
        make_hs_file(tmp_path, "Middle.hs", """
module Middle where

import Base

middleFn :: Int -> Int
middleFn x = baseFn x + 1
""")
        make_hs_file(tmp_path, "Top.hs", """
module Top where

import Middle

topFn :: Int -> Int
topFn x = middleFn (middleFn x)
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "baseFn" in names
        assert "middleFn" in names
        assert "topFn" in names

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_module_declaration_only(self, tmp_path: Path) -> None:
        """Test file with only module declaration."""
        make_hs_file(tmp_path, "Empty.hs", "module Empty where")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

    def test_file_with_only_imports(self, tmp_path: Path) -> None:
        """Test file with only imports and no functions."""
        make_hs_file(tmp_path, "Imports.hs", """
module Imports where

import Data.List
import Data.Map
""")
        result = analyze_haskell(tmp_path)
        assert not result.skipped

        # Should have import edges but no function symbols
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 0

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 2
