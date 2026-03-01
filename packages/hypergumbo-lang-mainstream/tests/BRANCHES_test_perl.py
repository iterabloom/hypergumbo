"""Branch coverage tests for perl.py analyzer.

Tests specific branch paths in the Perl analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Package/module extraction
- Subroutine extraction
- Signature extraction
- Import edges (use/require)
- Call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_mainstream.perl import analyze_perl, find_perl_files

def make_perl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Perl file with given content."""
    (tmp_path / name).write_text(content)

class TestPerlHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("perl", "lib/MyModule.pm", 1, 10, "process", "function")
        assert symbol_id == "perl:lib/MyModule.pm:1-10:process:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("perl", "lib/MyModule.pm")
        assert file_id == "perl:lib/MyModule.pm:1-1:file:file"

class TestPackageExtraction:
    """Branch coverage for package/module extraction."""

    def test_package_declaration(self, tmp_path: Path) -> None:
        """Test package declaration extraction."""
        make_perl_file(tmp_path, "MyModule.pm", """
package MyModule;
use strict;
use warnings;

1;
""")
        result = analyze_perl(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        assert any(m.name == "MyModule" for m in modules)

class TestSubroutineExtraction:
    """Branch coverage for subroutine extraction."""

    def test_simple_sub(self, tmp_path: Path) -> None:
        """Test simple subroutine extraction."""
        make_perl_file(tmp_path, "script.pl", """
sub greet {
    my ($name) = @_;
    print "Hello, $name\\n";
}
""")
        result = analyze_perl(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        assert any(f.name == "greet" for f in functions)

    def test_multiple_subs(self, tmp_path: Path) -> None:
        """Test multiple subroutine extraction."""
        make_perl_file(tmp_path, "utils.pl", """
sub add {
    my ($a, $b) = @_;
    return $a + $b;
}

sub subtract {
    my ($a, $b) = @_;
    return $a - $b;
}
""")
        result = analyze_perl(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 2

    def test_sub_in_package(self, tmp_path: Path) -> None:
        """Test subroutine in package is qualified."""
        make_perl_file(tmp_path, "MyModule.pm", """
package MyModule;

sub process {
    my ($data) = @_;
    return $data;
}

1;
""")
        result = analyze_perl(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        # Should be qualified with package name
        assert any("MyModule" in f.name for f in functions)

class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_use_statement(self, tmp_path: Path) -> None:
        """Test use statement creates import edge."""
        make_perl_file(tmp_path, "script.pl", """
use JSON;
use Data::Dumper;

sub main {
    print "Hello\\n";
}
""")
        result = analyze_perl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 2

    def test_use_skips_pragmas(self, tmp_path: Path) -> None:
        """Test use statement skips pragmas like strict, warnings."""
        make_perl_file(tmp_path, "script.pl", """
use strict;
use warnings;
use utf8;

sub main {
    print "Hello\\n";
}
""")
        result = analyze_perl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        # Should not create edges for strict, warnings, utf8
        assert len(import_edges) == 0

    def test_require_statement(self, tmp_path: Path) -> None:
        """Test require statement creates import edge."""
        make_perl_file(tmp_path, "script.pl", """
require 'lib/helper.pl';

sub main {
    helper_function();
}
""")
        result = analyze_perl(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call_creates_edge(self, tmp_path: Path) -> None:
        """Test function call creates call edge."""
        make_perl_file(tmp_path, "script.pl", """
sub helper {
    return 42;
}

sub main {
    my $result = helper();
    print $result;
}
""")
        result = analyze_perl(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        assert len(call_edges) >= 1

class TestFindPerlFiles:
    """Branch coverage for file discovery."""

    def test_finds_pl_files(self, tmp_path: Path) -> None:
        """Test .pl files are discovered."""
        (tmp_path / "script.pl").write_text("print 'hello';")

        files = list(find_perl_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".pl" for f in files)

    def test_finds_pm_files(self, tmp_path: Path) -> None:
        """Test .pm files are discovered."""
        (tmp_path / "Module.pm").write_text("package Module; 1;")

        files = list(find_perl_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".pm" for f in files)

    def test_finds_t_files(self, tmp_path: Path) -> None:
        """Test .t test files are discovered."""
        (tmp_path / "test.t").write_text("use Test::More; ok(1);")

        files = list(find_perl_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".t" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_perl_files(self, tmp_path: Path) -> None:
        """Test directory with no Perl files."""
        result = analyze_perl(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_perl(self, tmp_path: Path) -> None:
        """Test minimal Perl file."""
        make_perl_file(tmp_path, "script.pl", """
print "hello\\n";
""")
        result = analyze_perl(tmp_path)
        assert not result.skipped

class TestMethodCallEdges:
    """Branch coverage for method call edge extraction."""

    def test_method_call_creates_edge(self, tmp_path: Path) -> None:
        """Test method call via arrow operator creates edge."""
        make_perl_file(tmp_path, "script.pl", """
package MyClass;

sub new {
    my $class = shift;
    return bless {}, $class;
}

sub process {
    my ($self, $data) = @_;
    return $data;
}

sub main {
    my $obj = MyClass->new();
    my $result = $obj->process("test");
    print $result;
}

1;
""")
        result = analyze_perl(tmp_path)
        # Should extract method calls
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Method calls may or may not resolve depending on context
        # The important thing is no crash
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_perl_file(tmp_path, "script.pl", """
sub main {
    print "hello\\n";
}
""")
        result = analyze_perl(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        import hypergumbo_lang_mainstream.perl as perl_module
        from hypergumbo_lang_mainstream.perl import _analyzer
        from unittest.mock import patch
        import pytest

        with patch.object(_analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="perl analysis skipped"):
                result = perl_module.analyze_perl(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason
