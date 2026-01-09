"""Tests for Ruby analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindRubyFiles:
    """Tests for Ruby file discovery."""

    def test_finds_ruby_files(self, tmp_path: Path) -> None:
        """Finds .rb files."""
        from hypergumbo.analyze.ruby import find_ruby_files

        (tmp_path / "app.rb").write_text("class App; end")
        (tmp_path / "config.rb").write_text("module Config; end")
        (tmp_path / "other.txt").write_text("not ruby")

        files = list(find_ruby_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".rb" for f in files)


class TestRubyTreeSitterAvailability:
    """Tests for tree-sitter-ruby availability checking."""

    def test_is_ruby_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-ruby is available."""
        from hypergumbo.analyze.ruby import is_ruby_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_ruby_tree_sitter_available() is True

    def test_is_ruby_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.ruby import is_ruby_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_ruby_tree_sitter_available() is False

    def test_is_ruby_tree_sitter_available_no_ruby(self) -> None:
        """Returns False when tree-sitter is available but ruby grammar is not."""
        from hypergumbo.analyze.ruby import is_ruby_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()  # tree-sitter available
            return None  # ruby grammar not available

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_ruby_tree_sitter_available() is False


class TestAnalyzeRubyFallback:
    """Tests for fallback behavior when tree-sitter-ruby unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-ruby unavailable."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "test.rb").write_text("class Test; end")

        with patch("hypergumbo.analyze.ruby.is_ruby_tree_sitter_available", return_value=False):
            result = analyze_ruby(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-ruby" in result.skip_reason


class TestRubyMethodExtraction:
    """Tests for extracting Ruby methods."""

    def test_extracts_method(self, tmp_path: Path) -> None:
        """Extracts Ruby method definitions."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("""
def greet(name)
  puts "Hello, #{name}!"
end

def helper(x)
  x + 1
end
""")

        result = analyze_ruby(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "greet" in method_names
        assert "helper" in method_names


class TestRubyClassExtraction:
    """Tests for extracting Ruby classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class User
  def initialize(name)
    @name = name
  end

  def greet
    puts "Hello, #{@name}!"
  end
end

class InternalData
  attr_accessor :value
end
""")

        result = analyze_ruby(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "InternalData" in class_names


class TestRubyModuleExtraction:
    """Tests for extracting Ruby modules."""

    def test_extracts_module(self, tmp_path: Path) -> None:
        """Extracts module declarations."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "utils.rb"
        rb_file.write_text("""
module Helpers
  def self.format(text)
    text.strip
  end
end

module Internal
  class Processor
    def process
    end
  end
end
""")

        result = analyze_ruby(tmp_path)


        modules = [s for s in result.symbols if s.kind == "module"]
        module_names = [s.name for s in modules]
        assert "Helpers" in module_names
        assert "Internal" in module_names


class TestRubyMethodCalls:
    """Tests for detecting method calls in Ruby."""

    def test_detects_method_call(self, tmp_path: Path) -> None:
        """Detects calls to methods in same file."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "utils.rb"
        rb_file.write_text("""
def caller
  helper
end

def helper
  puts "helping"
end
""")

        result = analyze_ruby(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestRubyRequires:
    """Tests for detecting Ruby require statements."""

    def test_detects_require_statement(self, tmp_path: Path) -> None:
        """Detects require statements."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "main.rb"
        rb_file.write_text("""
require 'json'
require_relative 'helper'

def main
  puts "Hello"
end
""")

        result = analyze_ruby(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edges for require statements
        assert len(import_edges) >= 1


class TestRubyEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "test.rb").write_text("class Test; end")

        with patch("hypergumbo.analyze.ruby.is_ruby_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_ruby": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_ruby"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_ruby(tmp_path)

        assert result.skipped is True
        assert "Failed to load Ruby parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.ruby import analyze_ruby

        # Create a file with only comments
        (tmp_path / "empty.rb").write_text("# Just a comment\n\n")

        result = analyze_ruby(tmp_path)


        assert result.run is not None

    def test_cross_file_method_call(self, tmp_path: Path) -> None:
        """Detects method calls across files."""
        from hypergumbo.analyze.ruby import analyze_ruby

        # File 1: defines helper
        (tmp_path / "helper.rb").write_text("""
def greet(name)
  "Hello, #{name}"
end
""")

        # File 2: calls helper
        (tmp_path / "main.rb").write_text("""
require_relative 'helper'

def run
  greet("world")
end
""")

        result = analyze_ruby(tmp_path)


        # Verify both files analyzed
        assert result.run.files_analyzed >= 2


class TestRubyInstanceMethods:
    """Tests for Ruby instance method extraction."""

    def test_extracts_instance_methods(self, tmp_path: Path) -> None:
        """Extracts instance methods from classes."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "user.rb"
        rb_file.write_text("""
class User
  def initialize(name)
    @name = name
  end

  def get_name
    @name
  end

  def set_name(name)
    @name = name
  end
end
""")

        result = analyze_ruby(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Methods should include class context
        assert any("initialize" in name for name in method_names)
        assert any("get_name" in name for name in method_names)


class TestRubyFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.ruby import (
            _extract_symbols_from_file,
            is_ruby_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_ruby_tree_sitter_available():
            pytest.skip("tree-sitter-ruby not available")

        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        rb_file = tmp_path / "test.rb"
        rb_file.write_text("def test; end")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(rb_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.ruby import (
            _extract_edges_from_file,
            is_ruby_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_ruby_tree_sitter_available():
            pytest.skip("tree-sitter-ruby not available")

        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        rb_file = tmp_path / "test.rb"
        rb_file.write_text("def test; end")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(rb_file, parser, {}, {}, run)

        assert result == []


class TestRubyModuleMethods:
    """Tests for module-level methods."""

    def test_extracts_module_method(self, tmp_path: Path) -> None:
        """Extracts methods defined inside modules (not classes)."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "helpers.rb"
        rb_file.write_text("""
module Helpers
  def format_text(text)
    text.strip.downcase
  end

  def clean_data(data)
    data.compact
  end
end
""")

        result = analyze_ruby(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Methods should be qualified with module name
        assert any("Helpers.format_text" in name for name in method_names)


class TestRubyExplicitCalls:
    """Tests for explicit method calls with arguments."""

    def test_detects_explicit_call_local(self, tmp_path: Path) -> None:
        """Detects method calls with arguments to local methods."""
        from hypergumbo.analyze.ruby import analyze_ruby

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("""
def process(data)
  format(data, true)
end

def format(data, flag)
  data.to_s
end
""")

        result = analyze_ruby(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_detects_explicit_call_global(self, tmp_path: Path) -> None:
        """Detects method calls with arguments to global methods."""
        from hypergumbo.analyze.ruby import analyze_ruby

        # File 1: defines format
        (tmp_path / "formatter.rb").write_text("""
def format(data, flag)
  data.to_s
end
""")

        # File 2: calls format
        (tmp_path / "processor.rb").write_text("""
def process(data)
  format(data, true)
end
""")

        result = analyze_ruby(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestRubyHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo.analyze.ruby import (
            _find_child_by_type,
            is_ruby_tree_sitter_available,
        )

        if not is_ruby_tree_sitter_available():
            pytest.skip("tree-sitter-ruby not available")

        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)

        source = b"# comment\n"
        tree = parser.parse(source)

        # Try to find a child type that doesn't exist
        result = _find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None


# ============================================================================
# Rails Route Detection Tests
# ============================================================================


class TestRailsRouteDetection:
    """Tests for Rails route DSL detection."""

    def test_rails_get_route(self, tmp_path: Path) -> None:
        """Rails get route should be detected with stable_id='get'."""
        from hypergumbo.analyze.ruby import analyze_ruby

        routes_file = tmp_path / "routes.rb"
        routes_file.write_text("""
Rails.application.routes.draw do
  get '/users', to: 'users#index'
end
""")

        result = analyze_ruby(tmp_path)


        # Find route symbols
        routes = [s for s in result.symbols if s.stable_id == "get"]
        assert len(routes) == 1
        assert routes[0].meta is not None
        assert routes[0].meta.get("route_path") == "/users"

    def test_rails_post_route(self, tmp_path: Path) -> None:
        """Rails post route should be detected."""
        from hypergumbo.analyze.ruby import analyze_ruby

        routes_file = tmp_path / "routes.rb"
        routes_file.write_text("""
Rails.application.routes.draw do
  post '/users', to: 'users#create'
end
""")

        result = analyze_ruby(tmp_path)


        routes = [s for s in result.symbols if s.stable_id == "post"]
        assert len(routes) == 1

    def test_rails_all_http_methods(self, tmp_path: Path) -> None:
        """Rails should detect all HTTP method routes."""
        from hypergumbo.analyze.ruby import analyze_ruby

        routes_file = tmp_path / "routes.rb"
        routes_file.write_text("""
Rails.application.routes.draw do
  get '/get', to: 'test#get'
  post '/post', to: 'test#post'
  put '/put', to: 'test#put'
  patch '/patch', to: 'test#patch'
  delete '/delete', to: 'test#delete'
end
""")

        result = analyze_ruby(tmp_path)


        stable_ids = {s.stable_id for s in result.symbols if s.stable_id}
        assert "get" in stable_ids
        assert "post" in stable_ids
        assert "put" in stable_ids
        assert "patch" in stable_ids
        assert "delete" in stable_ids

    def test_rails_resources_route(self, tmp_path: Path) -> None:
        """Rails resources macro should be detected."""
        from hypergumbo.analyze.ruby import analyze_ruby

        routes_file = tmp_path / "routes.rb"
        routes_file.write_text("""
Rails.application.routes.draw do
  resources :users
end
""")

        result = analyze_ruby(tmp_path)


        # resources creates a route entry
        resources = [s for s in result.symbols if s.kind == "route" and "users" in s.name]
        assert len(resources) >= 1


class TestRubySignatureExtraction:
    """Tests for Ruby method signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "calc.rb").write_text("""
def add(x, y)
  x + y
end
""")
        result = analyze_ruby(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "add"]
        assert len(methods) == 1
        assert methods[0].signature == "(x, y)"

    def test_optional_params(self, tmp_path: Path) -> None:
        """Extracts signature with optional parameters (default values)."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "greeter.rb").write_text("""
def greet(name, greeting = "Hello")
  puts "#{greeting}, #{name}!"
end
""")
        result = analyze_ruby(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "greet"]
        assert len(methods) == 1
        assert methods[0].signature == "(name, greeting = ...)"

    def test_keyword_params(self, tmp_path: Path) -> None:
        """Extracts signature with keyword parameters."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "server.rb").write_text("""
def configure(host:, port: 8080)
  @host = host
  @port = port
end
""")
        result = analyze_ruby(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "configure"]
        assert len(methods) == 1
        assert methods[0].signature == "(host:, port: ...)"

    def test_splat_and_block_params(self, tmp_path: Path) -> None:
        """Extracts signature with splat and block parameters."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "handler.rb").write_text("""
def process(*args, **kwargs, &block)
  block.call(*args, **kwargs)
end
""")
        result = analyze_ruby(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "process"]
        assert len(methods) == 1
        assert methods[0].signature == "(*args, **kwargs, &block)"

    def test_no_params(self, tmp_path: Path) -> None:
        """Extracts signature for method with no parameters."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "simple.rb").write_text("""
def answer
  42
end
""")
        result = analyze_ruby(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and s.name == "answer"]
        assert len(methods) == 1
        assert methods[0].signature == "()"


# ============================================================================
# Deprecation Warning Tests (ADR-0003 v1.0.x)
# ============================================================================


class TestRailsRouteDetectionDeprecation:
    """Tests for deprecation warnings on analyzer-level Rails route detection."""

    def test_rails_route_emits_deprecation_warning(self, tmp_path: Path) -> None:
        """Rails route detection emits deprecation warning."""
        import warnings
        from hypergumbo.analyze import ruby as ruby_module
        from hypergumbo.analyze.ruby import analyze_ruby

        # Reset the warning deduplication set
        ruby_module._deprecated_route_warnings_emitted.clear()

        routes_file = tmp_path / "routes.rb"
        routes_file.write_text("""
Rails.application.routes.draw do
  get '/users', to: 'users#index'
  post '/users', to: 'users#create'
end
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_ruby(tmp_path)

        # Should have at least one deprecation warning for Rails
        deprecation_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        warning_message = str(deprecation_warnings[0].message)
        assert "Rails" in warning_message
        assert "deprecated" in warning_message.lower()

    def test_deprecation_warning_emitted_once_per_session(
        self, tmp_path: Path
    ) -> None:
        """Deprecation warning is emitted only once per session."""
        import warnings
        from hypergumbo.analyze import ruby as ruby_module
        from hypergumbo.analyze.ruby import analyze_ruby

        # Reset the warning deduplication set
        ruby_module._deprecated_route_warnings_emitted.clear()

        # Create multiple route files
        (tmp_path / "routes.rb").write_text("""
get '/users', to: 'users#index'
post '/users', to: 'users#create'
""")
        (tmp_path / "admin_routes.rb").write_text("""
get '/admin', to: 'admin#index'
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_ruby(tmp_path)

        # Should have exactly one Rails deprecation warning (deduplicated)
        rails_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Rails" in str(warning.message)
        ]
        assert len(rails_warnings) == 1

    def test_no_deprecation_warning_without_route_calls(
        self, tmp_path: Path
    ) -> None:
        """No deprecation warning for files without route calls."""
        import warnings
        from hypergumbo.analyze import ruby as ruby_module
        from hypergumbo.analyze.ruby import analyze_ruby

        # Reset the warning deduplication set
        ruby_module._deprecated_route_warnings_emitted.clear()

        rb_file = tmp_path / "model.rb"
        rb_file.write_text("""
class User
  def initialize(name)
    @name = name
  end

  def greet
    "Hello, #{@name}!"
  end
end
""")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            analyze_ruby(tmp_path)

        # Should have no deprecation warnings for route detection
        route_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "Rails" in str(warning.message)
        ]
        assert len(route_warnings) == 0
