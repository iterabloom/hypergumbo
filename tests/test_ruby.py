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


class TestSinatraUsageContext:
    """Tests for Sinatra block-based route detection."""

    def test_sinatra_get_with_block(self, tmp_path: Path) -> None:
        """Detects Sinatra get route with do block."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "app.rb").write_text("""
require 'sinatra'

get '/users' do
  'Hello Users'
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "get"), None)
        assert ctx is not None
        assert ctx.kind == "call"
        assert ctx.metadata["route_path"] == "/users"
        assert ctx.metadata["http_method"] == "GET"
        assert ctx.metadata["has_block"] is True

    def test_sinatra_post_with_block(self, tmp_path: Path) -> None:
        """Detects Sinatra post route with block."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "app.rb").write_text("""
post '/users' do
  'Created'
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "post"), None)
        assert ctx is not None
        assert ctx.metadata["http_method"] == "POST"
        assert ctx.metadata["has_block"] is True

    def test_sinatra_multiple_routes(self, tmp_path: Path) -> None:
        """Detects multiple Sinatra routes."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "app.rb").write_text("""
get '/' do
  'Home'
end

post '/submit' do
  'Submitted'
end

delete '/users/:id' do
  'Deleted'
end
""")
        result = analyze_ruby(tmp_path)
        methods = {c.metadata["http_method"] for c in result.usage_contexts}
        assert "GET" in methods
        assert "POST" in methods
        assert "DELETE" in methods


class TestRailsUsageContext:
    """Tests for Rails route DSL UsageContext extraction."""

    def test_rails_route_with_to_option(self, tmp_path: Path) -> None:
        """Detects Rails get route with to: 'controller#action' option."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  get '/users', to: 'users#index'
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "get"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "/users"
        assert ctx.metadata["http_method"] == "GET"
        assert ctx.metadata["controller_action"] == "users#index"
        assert ctx.metadata["has_block"] is False

    def test_rails_resources_route(self, tmp_path: Path) -> None:
        """Detects Rails resources :users macro."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  resources :users
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "resources"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "users"
        assert ctx.metadata["http_method"] == "RESOURCES"

    def test_rails_resource_singular(self, tmp_path: Path) -> None:
        """Detects Rails resource :profile (singular) macro."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  resource :profile
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "resource"), None)
        assert ctx is not None
        assert ctx.metadata["route_path"] == "profile"
        assert ctx.metadata["http_method"] == "RESOURCES"

    def test_rails_post_route_with_controller_action(self, tmp_path: Path) -> None:
        """Detects Rails post route with controller#action."""
        from hypergumbo.analyze.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  post '/sessions', to: 'sessions#create'
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "post"), None)
        assert ctx is not None
        assert ctx.metadata["controller_action"] == "sessions#create"
