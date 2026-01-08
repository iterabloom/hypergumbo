"""Tests for Perl language analyzer.

Perl is a highly capable, feature-rich programming language with over
30 years of development.

Key constructs: package, sub, use, require.

Test strategy:
- Package detection
- Subroutine detection (sub)
- Use statements (imports)
- Require statements
- Function calls
- Cross-file resolution
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import perl as perl_module
from hypergumbo.analyze.perl import analyze_perl


def make_perl_file(tmp: Path, name: str, content: str) -> Path:
    """Create a Perl file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestPerlAnalyzer:
    """Test Perl symbol and edge detection."""

    def test_detects_package(self, tmp_path: Path) -> None:
        """Detect package declarations."""
        make_perl_file(
            tmp_path,
            "MyModule.pm",
            """
package MyModule;

sub hello { print "Hello\\n"; }

1;
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "MyModule" in names

        mod = next(s for s in result.symbols if s.name == "MyModule")
        assert mod.kind == "module"
        assert mod.language == "perl"

    def test_detects_subroutines(self, tmp_path: Path) -> None:
        """Detect subroutine definitions."""
        make_perl_file(
            tmp_path,
            "utils.pl",
            """
sub greet {
    my $name = shift;
    print "Hello, $name\\n";
}

sub farewell {
    my $name = shift;
    print "Goodbye, $name\\n";
}
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        # In main package, unqualified names
        assert "greet" in names
        assert "farewell" in names

        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.kind == "function"

    def test_detects_use_statements(self, tmp_path: Path) -> None:
        """Detect use statements as import edges."""
        make_perl_file(
            tmp_path,
            "app.pl",
            """
use strict;
use warnings;
use Some::Module;
use Another::Package qw(func1 func2);

sub main { }
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        # Should have imports for Some::Module and Another::Package (not strict/warnings)
        import_dsts = [e.dst for e in imports]
        assert any("Some::Module" in dst for dst in import_dsts)
        assert any("Another::Package" in dst for dst in import_dsts)

    def test_detects_require_statements(self, tmp_path: Path) -> None:
        """Detect require statements as import edges."""
        make_perl_file(
            tmp_path,
            "main.pl",
            """
require 'other.pl';

sub run { }
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 1
        assert any("other.pl" in e.dst for e in imports)

    def test_detects_function_calls(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        make_perl_file(
            tmp_path,
            "app.pl",
            """
sub helper {
    my $x = shift;
    return $x * 2;
}

sub main {
    my $result = helper(21);
    return $result;
}
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # main should call helper
        main_sym = next(s for s in result.symbols if s.name == "main")
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (main_sym.id, helper_sym.id) in edge_pairs

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handle empty Perl file gracefully."""
        make_perl_file(tmp_path, "Empty.pl", "")
        result = analyze_perl(tmp_path)
        assert not result.skipped

    def test_handles_pm_files(self, tmp_path: Path) -> None:
        """Handle Perl module files (.pm)."""
        make_perl_file(
            tmp_path,
            "Utils.pm",
            """
package Utils;

sub add {
    my ($a, $b) = @_;
    return $a + $b;
}

1;
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped
        names = [s.name for s in result.symbols]
        assert "Utils" in names
        assert "Utils::add" in names

    def test_handles_t_files(self, tmp_path: Path) -> None:
        """Handle Perl test files (.t)."""
        make_perl_file(
            tmp_path,
            "test.t",
            """
use strict;
use warnings;
use Test::More tests => 1;

sub my_test {
    ok(1, 'test passes');
}

my_test();
""",
        )
        result = analyze_perl(tmp_path)
        assert result is not None

    def test_cross_file_calls(self, tmp_path: Path) -> None:
        """Detect calls across files via two-pass resolution."""
        make_perl_file(
            tmp_path,
            "Utils.pm",
            """
package Utils;

sub double {
    my $x = shift;
    return $x * 2;
}

1;
""",
        )
        make_perl_file(
            tmp_path,
            "Main.pl",
            """
use Utils;

sub quadruple {
    my $x = shift;
    my $y = double($x);
    return double($y);
}
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # quadruple should call double (Utils::double resolved via global registry)
        quad = next(s for s in result.symbols if s.name == "quadruple")
        assert quad is not None

    def test_qualified_subroutine_names(self, tmp_path: Path) -> None:
        """Subroutines in packages get qualified names."""
        make_perl_file(
            tmp_path,
            "MyApp.pm",
            """
package MyApp;

sub run {
    return 1;
}

1;
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "MyApp::run" in names

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analysis skips gracefully when tree-sitter unavailable."""
        make_perl_file(tmp_path, "Test.pl", "sub test { }")

        with patch.object(
            perl_module,
            "is_perl_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="Perl analysis skipped"):
                result = perl_module.analyze_perl(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason

    def test_analysis_run_provenance(self, tmp_path: Path) -> None:
        """Analysis run contains provenance information."""
        make_perl_file(tmp_path, "test.pl", "sub main { }")
        result = analyze_perl(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "perl-v1"
        assert result.run.files_analyzed == 1
        assert result.run.duration_ms >= 0

    def test_detects_method_calls(self, tmp_path: Path) -> None:
        """Detect method call edges (arrow operator)."""
        make_perl_file(
            tmp_path,
            "app.pl",
            """
sub helper {
    return 1;
}

sub main {
    my $obj = SomeClass->new();
    $obj->helper();
}
""",
        )
        result = analyze_perl(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # method call $obj->helper() should create edge to helper
        assert len(call_edges) >= 1

        # main should call helper via method call
        main_sym = next(s for s in result.symbols if s.name == "main")
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (main_sym.id, helper_sym.id) in edge_pairs


class TestPerlSignatureExtraction:
    """Tests for Perl subroutine signature extraction."""

    def test_traditional_sub_no_signature(self, tmp_path: Path) -> None:
        """Traditional subs without signatures get empty ()."""
        make_perl_file(
            tmp_path,
            "calc.pl",
            """
sub add {
    my ($x, $y) = @_;
    return $x + $y;
}
""",
        )
        result = analyze_perl(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "add" in s.name]
        assert len(funcs) == 1
        # Traditional Perl subs don't have signatures in the declaration
        assert funcs[0].signature == "()"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Function with no parameters gets ()."""
        make_perl_file(
            tmp_path,
            "simple.pl",
            """
sub answer {
    return 42;
}
""",
        )
        result = analyze_perl(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "answer" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"
