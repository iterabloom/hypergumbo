"""Tests for Ruby analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindRubyFiles:
    """Tests for Ruby file discovery."""

    def test_finds_ruby_files(self, tmp_path: Path) -> None:
        """Finds .rb files."""
        from hypergumbo_lang_mainstream.ruby import find_ruby_files

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
        from hypergumbo_lang_mainstream.ruby import is_ruby_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_ruby_tree_sitter_available() is True

    def test_is_ruby_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_mainstream.ruby import is_ruby_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_ruby_tree_sitter_available() is False

    def test_is_ruby_tree_sitter_available_no_ruby(self) -> None:
        """Returns False when tree-sitter is available but ruby grammar is not."""
        from hypergumbo_lang_mainstream.ruby import is_ruby_tree_sitter_available

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "test.rb").write_text("class Test; end")

        with patch("hypergumbo_lang_mainstream.ruby.is_ruby_tree_sitter_available", return_value=False):
            result = analyze_ruby(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-ruby" in result.skip_reason


class TestRubyMethodExtraction:
    """Tests for extracting Ruby methods."""

    def test_extracts_method(self, tmp_path: Path) -> None:
        """Extracts Ruby method definitions."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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


    def test_extracts_singleton_method(self, tmp_path: Path) -> None:
        """Extracts Ruby class methods (def self.method_name)."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "service.rb"
        rb_file.write_text("""
class MyService
  def self.call(args)
    new(args).perform
  end

  def self.perform!(data)
    data
  end

  def perform
    nil
  end
end
""")

        result = analyze_ruby(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Class methods use dot separator
        assert "MyService.call" in method_names
        assert "MyService.perform!" in method_names
        # Instance method uses hash separator
        assert "MyService#perform" in method_names

    def test_singleton_method_call_edges(self, tmp_path: Path) -> None:
        """Calls inside singleton methods produce call edges."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "service.rb").write_text("""
class Builder
  def self.build(data)
    process(data)
  end

  def process(data)
    data
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        build_calls = [
            e for e in call_edges
            if "build" in e.src and "process" in e.dst
        ]
        assert len(build_calls) >= 1, (
            f"Expected call edge from self.build to process, got {len(build_calls)}. "
            f"All call edges: {[(e.src, e.dst) for e in call_edges]}"
        )

    def test_singleton_method_at_top_level(self, tmp_path: Path) -> None:
        """Singleton method at top level (no enclosing class) uses bare name."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "top.rb").write_text("""
def self.standalone_helper(x)
  x + 1
end
""")

        result = analyze_ruby(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "standalone_helper" in method_names


class TestRubyClassExtraction:
    """Tests for extracting Ruby classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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


class TestRubyInheritanceEdges:
    """Tests for extracting Ruby inheritance edges (META-001)."""

    def test_extracts_base_class_metadata(self, tmp_path: Path) -> None:
        """Extracts base_classes metadata for class with superclass."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class BaseModel
  def save
  end
end

class User < BaseModel
  def greet
  end
end
""")

        result = analyze_ruby(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["BaseModel"]

    def test_creates_extends_edge(self, tmp_path: Path) -> None:
        """Creates extends edge from class to its superclass."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class BaseModel
  def save
  end
end

class User < BaseModel
  def greet
  end
end
""")

        result = analyze_ruby(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        base = next((s for s in result.symbols if s.name == "BaseModel"), None)
        assert user is not None
        assert base is not None

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert extends_edges[0].src == user.id
        assert extends_edges[0].dst == base.id

    def test_no_edge_for_external_superclass(self, tmp_path: Path) -> None:
        """No edge created when superclass is not in analyzed codebase."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class User < ActiveRecord::Base
  def greet
  end
end
""")

        result = analyze_ruby(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        # base_classes metadata should still be extracted
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["ActiveRecord::Base"]

        # But no extends edge (ActiveRecord::Base is external)
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 0

    def test_qualified_name_matches_simple_name(self, tmp_path: Path) -> None:
        """Edge created when qualified superclass matches a simple class name."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class Base
  def save
  end
end

class User < SomeModule::Base
  def greet
  end
end
""")

        result = analyze_ruby(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        base = next((s for s in result.symbols if s.name == "Base"), None)
        assert user is not None
        assert base is not None
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["SomeModule::Base"]

        # Edge should be created (SomeModule::Base matches Base via last segment)
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert extends_edges[0].src == user.id
        assert extends_edges[0].dst == base.id

    def test_no_metadata_for_class_without_superclass(self, tmp_path: Path) -> None:
        """Class without superclass has no base_classes metadata."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "models.rb"
        rb_file.write_text("""
class User
  def greet
  end
end
""")

        result = analyze_ruby(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        # No base_classes metadata when there's no superclass
        assert user.meta is None or user.meta.get("base_classes") is None

    def test_extends_prefers_required_class_over_name_collision(
        self, tmp_path: Path
    ) -> None:
        """When multiple classes share a name, extends resolves via require_relative hint.

        INV-015: Same bug as Python (Django 238 Model stubs). Two files define
        class 'Model'; child file uses require_relative to specify which one.
        Edge should resolve to the required Model.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Real Model class in models/model.rb
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.rb").write_text(
            "class Model\n"
            "  def save\n"
            "  end\n"
            "end\n"
        )

        # Test stub Model class (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "helpers.rb").write_text(
            "class Model\n"
            "  # test stub\n"
            "end\n"
        )

        # A file that require_relative models/model and extends Model
        (tmp_path / "app.rb").write_text(
            "require_relative 'models/model'\n"
            "\n"
            "class Article < Model\n"
            "  def publish\n"
            "  end\n"
            "end\n"
        )

        result = analyze_ruby(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        article_extends = [e for e in extends_edges if "Article" in e.src]
        assert len(article_extends) == 1, (
            f"Expected 1 extends edge from Article, got {len(article_extends)}"
        )

        # Edge should point to models/model.rb::Model, NOT tests/helpers.rb::Model
        edge = article_extends[0]
        assert "models/model.rb" in edge.dst or "models\\model.rb" in edge.dst, (
            f"Article extends edge should point to models/model.rb::Model, "
            f"but points to: {edge.dst}"
        )

    def test_extends_same_file_class_preferred_over_other_file(
        self, tmp_path: Path
    ) -> None:
        """When base class is defined in the same file, prefer it over other files."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Base defined in file A
        (tmp_path / "a.rb").write_text(
            "class Base\n  def run\n  end\nend\n"
        )

        # Base defined in file B AND used as base in same file
        (tmp_path / "b.rb").write_text(
            "class Base\n  def run\n  end\nend\n"
            "\n"
            "class Child < Base\n  def go\n  end\nend\n"
        )

        result = analyze_ruby(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        assert len(child_extends) == 1

        # Should resolve to b.rb::Base (same file), not a.rb::Base
        edge = child_extends[0]
        assert "b.rb" in edge.dst, (
            f"Child extends edge should prefer same-file Base (b.rb), "
            f"but points to: {edge.dst}"
        )

    def test_extends_deterministic_fallback_when_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """When no same-file or require match, extends uses deterministic fallback."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Two files define 'Base', neither is required
        (tmp_path / "mod_a.rb").write_text(
            "class Base\n  def run\n  end\nend\n"
        )
        (tmp_path / "mod_b.rb").write_text(
            "class Base\n  def run\n  end\nend\n"
        )
        # A third file extends 'Base' without requiring either
        (tmp_path / "child.rb").write_text(
            "class Child < Base\n  def go\n  end\nend\n"
        )

        result = analyze_ruby(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        # Should still create an edge (deterministic fallback)
        assert len(child_extends) == 1


class TestRubyModuleExtraction:
    """Tests for extracting Ruby modules."""

    def test_extracts_module(self, tmp_path: Path) -> None:
        """Extracts module declarations."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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

    def test_no_self_referential_edge_for_module_call(self, tmp_path: Path) -> None:
        """No self-referential edge when method calls module-level method with same name.

        E.g., logger method calling Postal.logger should NOT create logger -> logger edge.
        The analyzer should detect that the receiver is different (Postal vs self).
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "inspector.rb"
        rb_file.write_text("""
module Postal
  def self.logger
    @logger ||= Logger.new
  end
end

class MessageInspector
  def logger
    Postal.logger
  end
end
""")

        result = analyze_ruby(tmp_path)

        # Find edges from logger method
        logger_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and "logger" in e.src.lower()
        ]
        # Should have no self-referential edges
        self_refs = [e for e in logger_edges if e.src == e.dst]
        assert len(self_refs) == 0, f"Found self-referential edges: {self_refs}"

    def test_bare_method_call_cross_file(self, tmp_path: Path) -> None:
        """Bare method call (no parens) to method in another file is resolved."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Define a method in one file
        (tmp_path / "helper.rb").write_text("""
def global_helper
  puts "helping globally"
end
""")

        # Call it with bare identifier in another file
        (tmp_path / "caller.rb").write_text("""
def do_work
  global_helper
end
""")

        result = analyze_ruby(tmp_path)

        # Should have edge from do_work to global_helper
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cross_file_edges = [
            e for e in call_edges
            if "do_work" in e.src and "global_helper" in e.dst
        ]
        assert len(cross_file_edges) >= 1, f"Expected cross-file bare call edge: {call_edges}"


class TestRubyRequires:
    """Tests for detecting Ruby require statements."""

    def test_detects_require_statement(self, tmp_path: Path) -> None:
        """Detects require statements."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "test.rb").write_text("class Test; end")

        with patch("hypergumbo_lang_mainstream.ruby.is_ruby_tree_sitter_available", return_value=True):
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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Create a file with only comments
        (tmp_path / "empty.rb").write_text("# Just a comment\n\n")

        result = analyze_ruby(tmp_path)


        assert result.run is not None

    def test_cross_file_method_call(self, tmp_path: Path) -> None:
        """Detects method calls across files."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import (
            _extract_symbols_from_file,
            is_ruby_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.ruby import (
            _extract_edges_from_file,
            is_ruby_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import (
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


class TestRequireHintsExtraction:
    """Tests for require hints extraction for disambiguation."""

    def test_extracts_require_hints(self, tmp_path: Path) -> None:
        """Extracts require paths and converts to PascalCase class names."""
        from hypergumbo_lang_mainstream.ruby import (
            _extract_require_hints,
            is_ruby_tree_sitter_available,
        )

        if not is_ruby_tree_sitter_available():
            pytest.skip("tree-sitter-ruby not available")

        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)

        rb_file = tmp_path / "main.rb"
        rb_file.write_text("""
require 'user_service'
require_relative 'math/calculator'

def main
  UserService.new
  Calculator.add(1, 2)
end
""")

        source = rb_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_require_hints(tree, source)

        # Check that hints are extracted and converted to PascalCase
        assert "UserService" in hints
        assert hints["UserService"] == "user_service"
        assert "Calculator" in hints
        assert hints["Calculator"] == "math/calculator"

    def test_extracts_require_with_rb_extension(self, tmp_path: Path) -> None:
        """Strips .rb extension from require paths."""
        from hypergumbo_lang_mainstream.ruby import (
            _extract_require_hints,
            is_ruby_tree_sitter_available,
        )

        if not is_ruby_tree_sitter_available():
            pytest.skip("tree-sitter-ruby not available")

        import tree_sitter_ruby
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_ruby.language())
        parser = tree_sitter.Parser(lang)

        rb_file = tmp_path / "test.rb"
        rb_file.write_text("""
require_relative 'helpers/string_utils.rb'
""")

        source = rb_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_require_hints(tree, source)

        # .rb extension should be stripped, snake_case converted to PascalCase
        assert "StringUtils" in hints
        assert hints["StringUtils"] == "helpers/string_utils.rb"


class TestRubySignatureExtraction:
    """Tests for Ruby method signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        # INV-006: Infer controller_action for resource routes
        assert ctx.metadata["controller_action"] == "users#index"

    def test_rails_resource_singular(self, tmp_path: Path) -> None:
        """Detects Rails resource :profile (singular) macro."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

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
        # INV-006: Infer controller_action for resource routes
        assert ctx.metadata["controller_action"] == "profile#index"

    def test_rails_post_route_with_controller_action(self, tmp_path: Path) -> None:
        """Detects Rails post route with controller#action."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  post '/sessions', to: 'sessions#create'
end
""")
        result = analyze_ruby(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "post"), None)
        assert ctx is not None
        assert ctx.metadata["controller_action"] == "sessions#create"


class TestRailsRouteSymbols:
    """Tests for Rails route Symbol extraction (enables entrypoint detection)."""

    def test_route_symbols_created_for_http_methods(self, tmp_path: Path) -> None:
        """Route DSL calls create Symbol objects with kind='route'."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  get '/users', to: 'users#index'
  post '/sessions', to: 'sessions#create'
end
""")
        result = analyze_ruby(tmp_path)

        # Find route symbols
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 2

        get_route = next((s for s in route_symbols if "GET" in s.name), None)
        assert get_route is not None
        assert get_route.name == "GET /users"
        assert get_route.meta["http_method"] == "GET"
        assert get_route.meta["route_path"] == "/users"
        assert get_route.language == "ruby"

        post_route = next((s for s in route_symbols if "POST" in s.name), None)
        assert post_route is not None
        assert post_route.name == "POST /sessions"
        assert post_route.meta["http_method"] == "POST"

    def test_route_symbols_for_resources_macro(self, tmp_path: Path) -> None:
        """Resources macro creates expanded RESTful route symbols.

        INV-006 improvement: Instead of single RESOURCES symbol, emit all
        7 RESTful routes to enable route-handler linking for all actions.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  resources :articles
end
""")
        result = analyze_ruby(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        # Should have 7 RESTful routes: index, show, new, create, edit, update, destroy
        assert len(route_symbols) == 7

        # Check each route has correct http_method and controller_action
        routes_by_action = {s.meta["controller_action"]: s for s in route_symbols}

        # Collection routes
        assert "articles#index" in routes_by_action
        assert routes_by_action["articles#index"].meta["http_method"] == "GET"
        assert routes_by_action["articles#index"].meta["route_path"] == "/articles"

        assert "articles#create" in routes_by_action
        assert routes_by_action["articles#create"].meta["http_method"] == "POST"

        assert "articles#new" in routes_by_action
        assert routes_by_action["articles#new"].meta["http_method"] == "GET"
        assert routes_by_action["articles#new"].meta["route_path"] == "/articles/new"

        # Member routes (with :id parameter)
        assert "articles#show" in routes_by_action
        assert routes_by_action["articles#show"].meta["http_method"] == "GET"
        assert routes_by_action["articles#show"].meta["route_path"] == "/articles/:id"

        assert "articles#edit" in routes_by_action
        assert routes_by_action["articles#edit"].meta["http_method"] == "GET"

        assert "articles#update" in routes_by_action
        assert routes_by_action["articles#update"].meta["http_method"] in ("PATCH", "PUT")

        assert "articles#destroy" in routes_by_action
        assert routes_by_action["articles#destroy"].meta["http_method"] == "DELETE"

    def test_route_symbols_for_resource_singular(self, tmp_path: Path) -> None:
        """Singular resource macro creates 6 RESTful route symbols (no index).

        resource :profile creates routes without :id param and no index.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  resource :profile
end
""")
        result = analyze_ruby(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        # Singular resource: show, new, create, edit, update, destroy (no index)
        assert len(route_symbols) == 6

        routes_by_action = {s.meta["controller_action"]: s for s in route_symbols}

        # No index for singular resource
        assert "profiles#index" not in routes_by_action

        # Singular routes don't have :id in path
        assert "profiles#show" in routes_by_action
        assert routes_by_action["profiles#show"].meta["route_path"] == "/profile"

        assert "profiles#create" in routes_by_action
        assert "profiles#new" in routes_by_action
        assert "profiles#edit" in routes_by_action
        assert "profiles#update" in routes_by_action
        assert "profiles#destroy" in routes_by_action

    def test_route_symbols_singular_resource_already_plural(self, tmp_path: Path) -> None:
        """Singular resource whose name already ends in 's' doesn't double the 's'.

        resource :audit_logs should pluralize to audit_logs (not audit_logss).
        resource :settings should pluralize to settings (not settingss).
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  resource :audit_logs
  resource :settings
end
""")
        result = analyze_ruby(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        controller_actions = {s.meta["controller_action"] for s in route_symbols}

        # Should NOT have double-s
        assert not any("audit_logss" in ca for ca in controller_actions), (
            f"Double-s bug: found {[ca for ca in controller_actions if 'audit_log' in ca]}"
        )
        assert not any("settingss" in ca for ca in controller_actions), (
            f"Double-s bug: found {[ca for ca in controller_actions if 'setting' in ca]}"
        )

        # Should have correctly pluralized names
        assert "audit_logs#show" in controller_actions
        assert "settings#show" in controller_actions

    def test_route_symbols_include_controller_action(self, tmp_path: Path) -> None:
        """Route symbols include controller_action in metadata when specified."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  get '/home', to: 'pages#home'
end
""")
        result = analyze_ruby(tmp_path)

        route_symbol = next((s for s in result.symbols if s.kind == "route"), None)
        assert route_symbol is not None
        assert route_symbol.meta["controller_action"] == "pages#home"

    def test_route_hash_rocket_syntax(self, tmp_path: Path) -> None:
        """Route symbols extract controller_action from hash rocket syntax.

        Rails supports both:
          get '/path', to: 'ctrl#action'  (explicit to: option)
          get '/path' => 'ctrl#action'    (hash rocket shorthand)

        This test verifies the hash rocket syntax is properly parsed.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  post "persist" => "sessions#persist"
  get "login" => "sessions#new"
  delete "logout" => "sessions#destroy"
  match "login/reset" => "sessions#begin_password_reset", via: [:get, :post]
end
""")
        result = analyze_ruby(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 4

        # Check each route has correct controller_action
        # Verify all routes have controller_action metadata
        for route in route_symbols:
            assert "controller_action" in route.meta, f"Missing controller_action for {route.name}"
            assert "#" in route.meta["controller_action"], f"Invalid controller_action: {route.meta['controller_action']}"

        # Check specific routes
        by_action = {s.meta["controller_action"]: s for s in route_symbols}
        assert "sessions#persist" in by_action
        assert "sessions#new" in by_action
        assert "sessions#destroy" in by_action
        assert "sessions#begin_password_reset" in by_action


class TestRouteDetectionFileFiltering:
    """Tests for route detection false positive prevention.

    Route detection should only create route symbols from files that are
    actually route definition files, not test files or arbitrary app code.
    This prevents test HTTP helpers (get '/path') from being misclassified
    as route definitions.
    """

    def test_test_files_do_not_produce_route_symbols(self, tmp_path: Path) -> None:
        """HTTP method calls in spec/ files are test helpers, not route defs."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        spec_dir = tmp_path / "spec" / "requests"
        spec_dir.mkdir(parents=True)
        (spec_dir / "users_spec.rb").write_text("""
describe 'Users API' do
  it 'lists users' do
    get '/api/v1/users'
    expect(response).to have_http_status(:ok)
  end

  it 'creates a user' do
    post '/api/v1/users', params: { name: 'Alice' }
    expect(response).to have_http_status(:created)
  end

  it 'deletes a user' do
    delete '/api/v1/users/1'
    expect(response).to have_http_status(:no_content)
  end
end
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 0, (
            f"Test file should not produce route symbols, got: "
            f"{[s.name for s in route_symbols]}"
        )

    def test_test_directory_variants_filtered(self, tmp_path: Path) -> None:
        """All common test directory names are filtered."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        for test_dir_name in ("test", "tests", "features"):
            test_dir = tmp_path / test_dir_name
            test_dir.mkdir(exist_ok=True)
            (test_dir / "api_test.rb").write_text("""
get '/health'
post '/login', params: { user: 'admin' }
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 0

    def test_route_file_still_produces_symbols(self, tmp_path: Path) -> None:
        """Files named routes.rb still produce route symbols normally."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes.rb").write_text("""
Rails.application.routes.draw do
  get '/users', to: 'users#index'
  post '/sessions', to: 'sessions#create'
end
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 2

    def test_config_routes_directory(self, tmp_path: Path) -> None:
        """Files in config/routes/ directory produce route symbols."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        routes_dir = tmp_path / "config" / "routes"
        routes_dir.mkdir(parents=True)
        (routes_dir / "api.rb").write_text("""
get '/api/v1/health', to: 'health#check'
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 1

    def test_sinatra_routes_in_app_files(self, tmp_path: Path) -> None:
        """Sinatra-style routes (with do blocks) detected in non-test files."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "app.rb").write_text("""
require 'sinatra'

get '/users' do
  json users
end

post '/users' do
  create_user(params)
end
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 2

    def test_bare_http_calls_in_app_files_not_routes(self, tmp_path: Path) -> None:
        """Bare get/post calls without blocks or to: are not route definitions."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "client.rb").write_text("""
class ApiClient
  def fetch_users
    get '/api/v1/users'
  end

  def create_user(data)
    post '/api/v1/users', body: data
  end
end
""")
        result = analyze_ruby(tmp_path)
        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 0, (
            f"Bare HTTP calls without blocks should not produce routes, got: "
            f"{[s.name for s in route_symbols]}"
        )


class TestRubyBlockCallAttribution:
    """Tests for call edge attribution inside Ruby blocks.

    Ruby uses blocks extensively (each, map, times, etc.). Calls inside these
    blocks must be attributed to the enclosing method.
    """

    def test_call_inside_each_block_attributed(self, tmp_path: Path) -> None:
        """Calls inside each block are attributed to enclosing method.

        When you have:
            def process
              items.each do |item|
                helper(item)  # This call should be from process
              end
            end

        The call to helper() should be attributed to process, not lost.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("""
def helper(x)
  puts x
end

def process
  items = [1, 2, 3]
  items.each do |item|
    helper(item)
  end
end
""")

        result = analyze_ruby(tmp_path)

        # Find symbols
        process_method = next((s for s in result.symbols if s.name == "process"), None)
        helper_method = next((s for s in result.symbols if s.name == "helper"), None)

        assert process_method is not None, "Should find process method"
        assert helper_method is not None, "Should find helper method"

        # The call to helper() inside the block should be attributed to process
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_method.id
                and e.dst == helper_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside each block should be attributed to process"

    def test_call_inside_brace_block_attributed(self, tmp_path: Path) -> None:
        """Calls inside brace {} blocks are attributed to enclosing method.

        Ruby allows both do...end and {...} block syntax.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("""
def worker
  puts "working"
end

def caller
  3.times { worker }
end
""")

        result = analyze_ruby(tmp_path)

        # Find symbols
        caller_method = next((s for s in result.symbols if s.name == "caller"), None)
        worker_method = next((s for s in result.symbols if s.name == "worker"), None)

        assert caller_method is not None
        assert worker_method is not None

        # The call to worker() inside the brace block should be attributed to caller
        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller_method.id
                and e.dst == worker_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside brace block should be attributed to caller"

    def test_nested_blocks_attributed_to_outer_method(self, tmp_path: Path) -> None:
        """Calls inside nested blocks are attributed to the outermost method."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("""
def helper
  puts "help"
end

def outer
  items = [[1, 2], [3, 4]]
  items.each do |row|
    row.each do |cell|
      helper
    end
  end
end
""")

        result = analyze_ruby(tmp_path)

        # Find symbols
        outer_method = next((s for s in result.symbols if s.name == "outer"), None)
        helper_method = next((s for s in result.symbols if s.name == "helper"), None)

        assert outer_method is not None
        assert helper_method is not None

        # Call inside nested blocks should be attributed to outer
        call_edge = next(
            (
                e for e in result.edges
                if e.src == outer_method.id
                and e.dst == helper_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside nested blocks should be attributed to outermost method"


class TestRubyReceiverCalls:
    """Tests for method calls with explicit receivers.

    Ruby method calls on receivers (e.g., User.find, obj.process) are common
    in Rails. The key improvement is disambiguation: when multiple classes define
    the same method, the receiver class name resolves to the correct target.
    """

    def test_receiver_disambiguates_same_name_methods(self, tmp_path: Path) -> None:
        """When User and Post both have find(), User.find() resolves to User#find."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "user.rb").write_text("""
class User
  def find(id)
    id
  end
end
""")

        (tmp_path / "post.rb").write_text("""
class Post
  def find(id)
    id
  end
end
""")

        (tmp_path / "controller.rb").write_text("""
class UsersController
  def show
    User.find(1)
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        show_edges = [
            e for e in call_edges
            if "show" in e.src and "find" in e.dst
        ]
        assert len(show_edges) >= 1, (
            f"Expected at least 1 call edge for User.find, got {len(show_edges)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )
        # The edge must resolve to User#find, not Post#find
        assert any("User" in e.dst for e in show_edges), (
            f"Expected edge to User#find, but got: {[(e.dst, e.evidence_type) for e in show_edges]}"
        )

    def test_constant_receiver_creates_receiver_call(self, tmp_path: Path) -> None:
        """Constant receiver (class name) creates edge with receiver_call evidence."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "service.rb").write_text("""
class Service
  def process(data)
    data
  end
end
""")

        (tmp_path / "handler.rb").write_text("""
class Handler
  def handle
    Service.process("data")
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        receiver_edges = [
            e for e in call_edges
            if "handle" in e.src and "process" in e.dst
            and e.evidence_type == "receiver_call"
        ]
        assert len(receiver_edges) == 1, (
            f"Expected 1 receiver_call edge, got {len(receiver_edges)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )
        assert receiver_edges[0].confidence == 0.85

    def test_scope_resolution_receiver_call(self, tmp_path: Path) -> None:
        """Scope resolution receiver ActiveRecord::Base.connection creates edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "base.rb").write_text("""
module ActiveRecord
  class Base
    def connection
      nil
    end
  end
end
""")

        (tmp_path / "migrator.rb").write_text("""
class Migrator
  def migrate
    ActiveRecord::Base.connection
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        scope_edges = [
            e for e in call_edges
            if "migrate" in e.src and "connection" in e.dst
            and e.evidence_type == "receiver_call"
        ]
        assert len(scope_edges) >= 1, (
            f"Expected at least 1 receiver_call edge, got {len(scope_edges)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )

    def test_scope_resolution_full_namespace_receiver(self, tmp_path: Path) -> None:
        """Full namespace in scope resolution receiver resolves call edge.

        When class is defined with inline namespace (class Voice::InboundCallBuilder),
        the method is registered as Voice::InboundCallBuilder#perform!. A call like
        Voice::InboundCallBuilder.perform!() must extract the full namespace to match.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "builder.rb").write_text("""
class Voice::InboundCallBuilder
  def self.perform!(params)
    new(params).build
  end

  def build
    nil
  end
end
""")

        (tmp_path / "controller.rb").write_text("""
class VoiceController
  def create
    Voice::InboundCallBuilder.perform!(request_params)
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        ns_edges = [
            e for e in call_edges
            if "create" in e.src and "perform!" in e.dst
            and e.evidence_type == "receiver_call"
        ]
        assert len(ns_edges) >= 1, (
            f"Expected receiver_call edge for Voice::InboundCallBuilder.perform!(), "
            f"got {len(ns_edges)}. All call edges: "
            f"{[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )

    def test_chained_new_method_call(self, tmp_path: Path) -> None:
        """Service.new(args).perform creates a call edge to the instance method.

        The pattern ClassName.new(...).method() is standard Rails service object style.
        The outer call has a 'call' node as receiver (the .new() call), which must be
        recognized and the class extracted from the inner call's receiver.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "service.rb").write_text("""
class StatusUpdateService
  def perform
    update_records
  end

  def update_records
    nil
  end
end
""")

        (tmp_path / "handler.rb").write_text("""
class Handler
  def process
    StatusUpdateService.new(data).perform
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        chained_edges = [
            e for e in call_edges
            if "process" in e.src and "perform" in e.dst
        ]
        assert len(chained_edges) >= 1, (
            f"Expected call edge for StatusUpdateService.new(data).perform, "
            f"got {len(chained_edges)}. All call edges: "
            f"{[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )

    def test_chained_namespaced_new_method_call(self, tmp_path: Path) -> None:
        """Voice::Builder.new(args).build creates call edge with namespaced receiver."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "builder.rb").write_text("""
class Voice::Builder
  def build
    nil
  end
end
""")

        (tmp_path / "controller.rb").write_text("""
class Controller
  def handle
    Voice::Builder.new(params).build
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        chained_edges = [
            e for e in call_edges
            if "handle" in e.src and "build" in e.dst
        ]
        assert len(chained_edges) >= 1, (
            f"Expected call edge for Voice::Builder.new(params).build, "
            f"got {len(chained_edges)}. All call edges: "
            f"{[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )

    def test_receiver_call_no_match_no_false_fallthrough(self, tmp_path: Path) -> None:
        """Variable-receiver call should NOT fall through to bare name lookup.

        ``obj.process("data")`` calls a method on ``obj``, which is a different
        dispatch from the top-level ``process`` function. The analyzer should
        not create a false edge.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Only define process (no class wrapping) - won't have qualified name
        (tmp_path / "helpers.rb").write_text("""
def process(data)
  data
end
""")

        (tmp_path / "main.rb").write_text("""
def run
  obj.process("data")
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should NOT match the top-level process function
        # because obj is a variable receiver with unknown type
        process_edges = [
            e for e in call_edges
            if "run" in e.src and "process" in e.dst
        ]
        assert len(process_edges) == 0, (
            f"Variable-receiver call 'obj.process' should NOT match top-level "
            f"process. Got: {[(e.src, e.dst, e.evidence_type) for e in process_edges]}"
        )

    def test_receiver_call_resolver_fallback(self, tmp_path: Path) -> None:
        """Qualified name not found directly; resolver finds method by short name.

        When a class uses a namespace prefix (Admin::UserFinder), its methods
        are stored as "Admin::UserFinder#locate". A call like Finder.locate()
        tries "Finder#locate" (not found) and falls to the resolver.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Namespaced class: symbol stored as "Admin::UserFinder#locate"
        (tmp_path / "finder.rb").write_text("""
class Admin::UserFinder
  def locate(id)
    id
  end
end
""")

        # Caller uses short name Finder (not Admin::UserFinder)
        (tmp_path / "app.rb").write_text("""
class App
  def run
    Finder.locate(42)
  end
end
""")

        result = analyze_ruby(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolver_edges = [
            e for e in call_edges
            if "run" in e.src and "locate" in e.dst
            and e.evidence_type == "receiver_call"
        ]
        assert len(resolver_edges) == 1, (
            f"Expected 1 resolver fallback edge, got {len(resolver_edges)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )
        assert resolver_edges[0].confidence == 0.75

    def test_receiver_call_outside_method_ignored(self, tmp_path: Path) -> None:
        """Receiver calls at module level (no enclosing method) produce no receiver_call edges."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "top.rb").write_text("""
class Service
  def work
    true
  end
end

# Module-level call, not inside a method
Service.work
""")

        result = analyze_ruby(tmp_path)

        receiver_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.evidence_type == "receiver_call"
        ]
        assert len(receiver_edges) == 0


class TestRailsCallbackEdges:
    """Tests for Rails before_action/after_action/around_action callback detection.

    Rails controllers use class-level callback declarations (e.g., before_action :authenticate!)
    that create implicit call edges from the class to the callback method. Without these edges,
    callback methods appear as orphans in the behavior map.
    """

    def test_before_action_creates_callback_edge(self, tmp_path: Path) -> None:
        """before_action :method creates invokes_callback edge from class to method."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class UsersController
  before_action :authenticate!

  def authenticate!
    true
  end

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "UsersController"), None)
        auth_method = next((s for s in result.symbols if s.name == "UsersController#authenticate!"), None)

        assert class_sym is not None, "Should find UsersController class"
        assert auth_method is not None, "Should find authenticate! method"

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == auth_method.id
        ]
        assert len(callback_edges) == 1, (
            f"Expected 1 invokes_callback edge, got {len(callback_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )
        assert callback_edges[0].confidence == 0.9

    def test_after_action_creates_callback_edge(self, tmp_path: Path) -> None:
        """after_action :method creates invokes_callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class LoggingController
  after_action :log_request

  def log_request
    true
  end

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "LoggingController"), None)
        log_method = next((s for s in result.symbols if s.name == "LoggingController#log_request"), None)

        assert class_sym is not None
        assert log_method is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == log_method.id
        ]
        assert len(callback_edges) == 1

    def test_around_action_creates_callback_edge(self, tmp_path: Path) -> None:
        """around_action :method creates invokes_callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class TimingController
  around_action :measure_time

  def measure_time
    yield
  end

  def show
    nil
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "TimingController"), None)
        measure_method = next((s for s in result.symbols if s.name == "TimingController#measure_time"), None)

        assert class_sym is not None
        assert measure_method is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == measure_method.id
        ]
        assert len(callback_edges) == 1

    def test_multiple_callbacks_in_one_declaration(self, tmp_path: Path) -> None:
        """before_action :method1, :method2 creates edges for both methods."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class OrdersController
  before_action :authenticate!, :set_order

  def authenticate!
    true
  end

  def set_order
    nil
  end

  def show
    nil
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "OrdersController"), None)
        auth_method = next((s for s in result.symbols if s.name == "OrdersController#authenticate!"), None)
        set_method = next((s for s in result.symbols if s.name == "OrdersController#set_order"), None)

        assert class_sym is not None
        assert auth_method is not None
        assert set_method is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == class_sym.id
        ]
        dst_ids = {e.dst for e in callback_edges}
        assert auth_method.id in dst_ids, "Should have edge to authenticate!"
        assert set_method.id in dst_ids, "Should have edge to set_order"
        assert len(callback_edges) == 2

    def test_callback_resolves_cross_file(self, tmp_path: Path) -> None:
        """Callback method defined in parent class (different file) still creates edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "base.rb").write_text("""
class ApplicationController
  def authenticate!
    true
  end
end
""")

        (tmp_path / "users_controller.rb").write_text("""
class UsersController < ApplicationController
  before_action :authenticate!

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "UsersController"), None)
        auth_method = next((s for s in result.symbols if s.name == "ApplicationController#authenticate!"), None)

        assert class_sym is not None, "Should find UsersController"
        assert auth_method is not None, "Should find ApplicationController#authenticate!"

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == auth_method.id
        ]
        assert len(callback_edges) == 1, (
            f"Expected cross-file callback edge, got {len(callback_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )

    def test_callback_outside_class_ignored(self, tmp_path: Path) -> None:
        """Bare before_action at top level (outside class) creates no edges."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "config.rb").write_text("""
before_action :something

def something
  nil
end
""")

        result = analyze_ruby(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0

    def test_legacy_before_filter(self, tmp_path: Path) -> None:
        """before_filter (Rails 3 API) creates callback edge same as before_action."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class LegacyController
  before_filter :check_auth

  def check_auth
    true
  end

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "LegacyController"), None)
        check_method = next((s for s in result.symbols if s.name == "LegacyController#check_auth"), None)

        assert class_sym is not None
        assert check_method is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == check_method.id
        ]
        assert len(callback_edges) == 1

    def test_callback_unresolvable_no_edge(self, tmp_path: Path) -> None:
        """Callback method that doesn't exist in codebase creates no edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class SomeController
  before_action :nonexistent_method

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0

    def test_callback_inside_method_body_ignored(self, tmp_path: Path) -> None:
        """before_action inside a method body (not class-level) creates no callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "controller.rb").write_text("""
class DynamicController
  def setup_callbacks
    before_action :do_something
  end

  def do_something
    true
  end

  def index
    []
  end
end
""")

        result = analyze_ruby(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0, (
            f"Callback inside method body should not create edge, got: "
            f"{[(e.src, e.dst) for e in callback_edges]}"
        )


class TestRailsModelCallbackEdges:
    """Tests for Rails model lifecycle callbacks (before_save, after_create, validate, etc.).

    Rails models use class-level callbacks similar to controller callbacks.
    These create invokes_callback edges from the model class to the callback method.
    """

    def test_before_save_creates_callback_edge(self, tmp_path: Path) -> None:
        """before_save :method creates invokes_callback edge from model to method."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "user.rb").write_text("""
class User < ApplicationRecord
  before_save :normalize_email

  def normalize_email
    self.email = email.downcase
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "User"), None)
        callback = next((s for s in result.symbols if s.name == "User#normalize_email"), None)
        assert class_sym is not None
        assert callback is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == callback.id
        ]
        assert len(edges) == 1
        assert edges[0].evidence_type == "rails_callback"

    def test_after_create_creates_callback_edge(self, tmp_path: Path) -> None:
        """after_create :method creates invokes_callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "account.rb").write_text("""
class Account < ApplicationRecord
  after_create :send_welcome

  def send_welcome
    Mailer.welcome(self).deliver_later
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Account"), None)
        callback = next((s for s in result.symbols if s.name == "Account#send_welcome"), None)
        assert class_sym is not None
        assert callback is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == callback.id
        ]
        assert len(edges) == 1

    def test_validate_creates_callback_edge(self, tmp_path: Path) -> None:
        """validate :method creates invokes_callback edge for custom validation."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "order.rb").write_text("""
class Order < ApplicationRecord
  validate :must_have_items

  def must_have_items
    errors.add(:items, "can't be empty") if items.empty?
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Order"), None)
        callback = next((s for s in result.symbols if s.name == "Order#must_have_items"), None)
        assert class_sym is not None
        assert callback is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == callback.id
        ]
        assert len(edges) == 1

    def test_after_commit_creates_callback_edge(self, tmp_path: Path) -> None:
        """after_commit :method creates invokes_callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "payment.rb").write_text("""
class Payment < ApplicationRecord
  after_commit :process_payment

  def process_payment
    PaymentGateway.charge(self)
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Payment"), None)
        callback = next((s for s in result.symbols if s.name == "Payment#process_payment"), None)
        assert class_sym is not None
        assert callback is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == callback.id
        ]
        assert len(edges) == 1


class TestBlockStyleCallbacks:
    """Tests for block-style Rails callbacks (do...end and lambda blocks).

    Rails callbacks support both named-method and block-style invocations.
    Block-style callbacks like:
        after_commit do
          provision_database
        end
    should create invokes_callback edges from the class to the methods
    called within the block body.
    """

    def test_do_block_callback_creates_edge(self, tmp_path: Path) -> None:
        """after_commit do...end creates invokes_callback edges for calls in block."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "server.rb").write_text("""
class Server < ApplicationRecord
  after_commit do
    provision_database
  end

  def provision_database
    true
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Server"), None)
        provision = next((s for s in result.symbols if s.name == "Server#provision_database"), None)

        assert class_sym is not None, "Should find Server class"
        assert provision is not None, "Should find provision_database method"

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == provision.id
        ]
        assert len(callback_edges) == 1, (
            f"Expected 1 invokes_callback edge for block callback, got {len(callback_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )
        assert callback_edges[0].evidence_type == "rails_block_callback"

    def test_do_block_callback_multiple_calls(self, tmp_path: Path) -> None:
        """Block with multiple method calls creates edges for each."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "server.rb").write_text("""
class Server < ApplicationRecord
  after_create do
    provision_database
    send_welcome_email
  end

  def provision_database
    true
  end

  def send_welcome_email
    true
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Server"), None)
        provision = next((s for s in result.symbols if s.name == "Server#provision_database"), None)
        welcome = next((s for s in result.symbols if s.name == "Server#send_welcome_email"), None)

        assert class_sym is not None
        assert provision is not None
        assert welcome is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == class_sym.id
        ]
        dst_ids = {e.dst for e in callback_edges}
        assert provision.id in dst_ids, "Should have edge to provision_database"
        assert welcome.id in dst_ids, "Should have edge to send_welcome_email"

    def test_brace_block_callback_creates_edge(self, tmp_path: Path) -> None:
        """after_save { method_call } creates invokes_callback edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "model.rb").write_text("""
class Account < ApplicationRecord
  before_save { normalize_name }

  def normalize_name
    self.name = name.strip
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Account"), None)
        normalize = next((s for s in result.symbols if s.name == "Account#normalize_name"), None)

        assert class_sym is not None
        assert normalize is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == normalize.id
        ]
        assert len(callback_edges) == 1

    def test_do_block_callback_with_parens(self, tmp_path: Path) -> None:
        """Method calls with parentheses in block are also detected."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "model.rb").write_text("""
class Invoice < ApplicationRecord
  after_commit do
    send_notification()
  end

  def send_notification
    true
  end
end
""")

        result = analyze_ruby(tmp_path)

        class_sym = next((s for s in result.symbols if s.name == "Invoice"), None)
        notify = next((s for s in result.symbols if s.name == "Invoice#send_notification"), None)

        assert class_sym is not None
        assert notify is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback"
            and e.src == class_sym.id
            and e.dst == notify.id
        ]
        assert len(callback_edges) == 1

    def test_block_callback_unresolvable_no_edge(self, tmp_path: Path) -> None:
        """Block calling a method that doesn't exist creates no edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "model.rb").write_text("""
class Thing < ApplicationRecord
  after_save do
    nonexistent_method
  end
end
""")

        result = analyze_ruby(tmp_path)
        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0

    def test_block_callback_empty_block_no_crash(self, tmp_path: Path) -> None:
        """Empty block callback doesn't crash."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "model.rb").write_text("""
class Thing < ApplicationRecord
  after_save do
  end
end
""")

        result = analyze_ruby(tmp_path)
        # No crash, no callback edges
        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0


class TestRubyJobEnqueueDetection:
    """Tests for ActiveJob perform_later / Sidekiq perform_async detection."""

    def test_perform_later_creates_enqueues_edge(self, tmp_path: Path) -> None:
        """SomeJob.perform_later(args) should create an enqueues edge to perform."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "send_email_job.rb").write_text("""
class SendEmailJob < ApplicationJob
  def perform(user_id)
    UserMailer.welcome(user_id).deliver_now
  end
end
""")
        (tmp_path / "controller.rb").write_text("""
class UsersController
  def create
    SendEmailJob.perform_later(user.id)
  end
end
""")

        result = analyze_ruby(tmp_path)

        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            f"Expected enqueues edge from create to SendEmailJob#perform, "
            f"got edge types: {[e.edge_type for e in result.edges]}"
        )

        # Verify the edge targets the perform method
        edge = enqueue_edges[0]
        assert "SendEmailJob" in edge.dst
        assert "perform" in edge.dst

    def test_perform_async_creates_enqueues_edge(self, tmp_path: Path) -> None:
        """SomeWorker.perform_async(args) should create an enqueues edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "cleanup_worker.rb").write_text("""
class CleanupWorker
  include Sidekiq::Worker

  def perform(batch_id)
    Batch.find(batch_id).cleanup!
  end
end
""")
        (tmp_path / "scheduler.rb").write_text("""
class Scheduler
  def run_cleanup
    CleanupWorker.perform_async(batch.id)
  end
end
""")

        result = analyze_ruby(tmp_path)

        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            "Expected enqueues edge from run_cleanup to CleanupWorker#perform"
        )
        edge = enqueue_edges[0]
        assert "CleanupWorker" in edge.dst
        assert "perform" in edge.dst

    def test_perform_later_chained_set(self, tmp_path: Path) -> None:
        """SomeJob.set(wait: 1.hour).perform_later(args) should create enqueues edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "notify_job.rb").write_text("""
class NotifyJob < ApplicationJob
  def perform(message_id)
    Message.find(message_id).notify!
  end
end
""")
        (tmp_path / "service.rb").write_text("""
class MessageService
  def send_notification
    NotifyJob.set(wait: 2.seconds).perform_later(message.id)
  end
end
""")

        result = analyze_ruby(tmp_path)

        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            "Expected enqueues edge for chained set().perform_later() pattern"
        )
        edge = enqueue_edges[0]
        assert "NotifyJob" in edge.dst
        assert "perform" in edge.dst

    def test_perform_later_no_perform_method_still_creates_edge(self, tmp_path: Path) -> None:
        """If job class has no perform method in repo, edge targets the class."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # Only caller file, no job definition
        (tmp_path / "controller.rb").write_text("""
class OrderController
  def process
    ShipmentJob.perform_later(order.id)
  end
end
""")

        result = analyze_ruby(tmp_path)

        # Even without the job class, we should still detect the pattern
        # and create an enqueues edge (may be unresolved)
        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            "Expected enqueues edge even when job class is not in repo"
        )

    def test_namespaced_job_enqueue(self, tmp_path: Path) -> None:
        """Namespace::SomeJob.perform_later should resolve to perform method."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "batch_job.rb").write_text("""
module Processing
  class BatchJob < ApplicationJob
    def perform(items)
      items.each { |i| process(i) }
    end
  end
end
""")
        (tmp_path / "controller.rb").write_text("""
class ImportController
  def start
    Processing::BatchJob.perform_later(items)
  end
end
""")

        result = analyze_ruby(tmp_path)

        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            "Expected enqueues edge for namespaced job"
        )
        edge = enqueue_edges[0]
        assert "perform" in edge.dst

    def test_job_class_without_perform_method(self, tmp_path: Path) -> None:
        """If job class exists but has no perform method, edge targets the class."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "sync_job.rb").write_text("""
class SyncJob < ApplicationJob
  # perform method defined in parent (not in repo)
end
""")
        (tmp_path / "service.rb").write_text("""
class SyncService
  def trigger
    SyncJob.perform_later(account.id)
  end
end
""")

        result = analyze_ruby(tmp_path)

        enqueue_edges = [e for e in result.edges if e.edge_type == "enqueues"]
        assert len(enqueue_edges) >= 1, (
            "Expected enqueues edge targeting job class"
        )
        edge = enqueue_edges[0]
        assert "SyncJob" in edge.dst


class TestActiveRecordAssociationEdges:
    """Tests for ActiveRecord has_many/belongs_to/has_one association detection.

    Rails models use class-level association macros (has_many, belongs_to, etc.)
    that define relationships between models. These create association edges
    from the declaring class to the target model class.
    """

    def test_has_many_creates_association_edge(self, tmp_path: Path) -> None:
        """has_many :comments creates association edge from Post to Comment."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "post.rb").write_text("""
class Post < ApplicationRecord
  has_many :comments
end
""")
        (tmp_path / "comment.rb").write_text("""
class Comment < ApplicationRecord
  belongs_to :post
end
""")

        result = analyze_ruby(tmp_path)

        post_sym = next((s for s in result.symbols if s.name == "Post"), None)
        comment_sym = next((s for s in result.symbols if s.name == "Comment"), None)
        assert post_sym is not None, "Should find Post class"
        assert comment_sym is not None, "Should find Comment class"

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == post_sym.id
            and e.dst == comment_sym.id
        ]
        assert len(assoc_edges) == 1, (
            f"Expected 1 association edge from Post to Comment, "
            f"got {len(assoc_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )
        assert assoc_edges[0].evidence_type == "activerecord_association"
        assert assoc_edges[0].confidence == 0.90

    def test_belongs_to_creates_association_edge(self, tmp_path: Path) -> None:
        """belongs_to :account creates association edge from User to Account."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "user.rb").write_text("""
class User < ApplicationRecord
  belongs_to :account
end
""")
        (tmp_path / "account.rb").write_text("""
class Account < ApplicationRecord
  has_many :users
end
""")

        result = analyze_ruby(tmp_path)

        user_sym = next((s for s in result.symbols if s.name == "User"), None)
        account_sym = next((s for s in result.symbols if s.name == "Account"), None)
        assert user_sym is not None
        assert account_sym is not None

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == user_sym.id
            and e.dst == account_sym.id
        ]
        assert len(assoc_edges) == 1, (
            f"Expected 1 association edge from User to Account, "
            f"got {len(assoc_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )

    def test_has_one_creates_association_edge(self, tmp_path: Path) -> None:
        """has_one :profile creates association edge from User to Profile."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "user.rb").write_text("""
class User < ApplicationRecord
  has_one :profile
end
""")
        (tmp_path / "profile.rb").write_text("""
class Profile < ApplicationRecord
  belongs_to :user
end
""")

        result = analyze_ruby(tmp_path)

        user_sym = next((s for s in result.symbols if s.name == "User"), None)
        profile_sym = next((s for s in result.symbols if s.name == "Profile"), None)
        assert user_sym is not None
        assert profile_sym is not None

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == user_sym.id
            and e.dst == profile_sym.id
        ]
        assert len(assoc_edges) == 1

    def test_association_with_class_name_option(self, tmp_path: Path) -> None:
        """has_many :messages, class_name: 'ChatMessage' uses explicit class name."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "conversation.rb").write_text("""
class Conversation < ApplicationRecord
  has_many :messages, class_name: "ChatMessage"
end
""")
        (tmp_path / "chat_message.rb").write_text("""
class ChatMessage < ApplicationRecord
  belongs_to :conversation
end
""")

        result = analyze_ruby(tmp_path)

        conv_sym = next((s for s in result.symbols if s.name == "Conversation"), None)
        msg_sym = next((s for s in result.symbols if s.name == "ChatMessage"), None)
        assert conv_sym is not None
        assert msg_sym is not None

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == conv_sym.id
            and e.dst == msg_sym.id
        ]
        assert len(assoc_edges) == 1, (
            f"Expected edge from Conversation to ChatMessage, "
            f"got {len(assoc_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )

    def test_association_target_not_found_creates_unresolved(self, tmp_path: Path) -> None:
        """Association to class not in repo creates unresolved edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "order.rb").write_text("""
class Order < ApplicationRecord
  belongs_to :customer
end
""")

        result = analyze_ruby(tmp_path)

        order_sym = next((s for s in result.symbols if s.name == "Order"), None)
        assert order_sym is not None

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == order_sym.id
        ]
        assert len(assoc_edges) == 1, (
            f"Expected unresolved association edge, got {len(assoc_edges)}"
        )
        assert "unresolved" in assoc_edges[0].dst
        assert assoc_edges[0].confidence == 0.70

    def test_has_many_pluralization_ies(self, tmp_path: Path) -> None:
        """has_many :categories correctly singularizes to Category (ies → y)."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "product.rb").write_text("""
class Product < ApplicationRecord
  has_many :categories
end
""")
        (tmp_path / "category.rb").write_text("""
class Category < ApplicationRecord
end
""")

        result = analyze_ruby(tmp_path)

        product_sym = next((s for s in result.symbols if s.name == "Product"), None)
        category_sym = next((s for s in result.symbols if s.name == "Category"), None)
        assert product_sym is not None
        assert category_sym is not None

        assoc_edges = [
            e for e in result.edges
            if e.edge_type == "association"
            and e.src == product_sym.id
            and e.dst == category_sym.id
        ]
        assert len(assoc_edges) == 1

    def test_singularization_sses(self) -> None:
        """Plurals ending in sses (addresses) singularize correctly."""
        from hypergumbo_lang_mainstream.ruby import _association_name_to_class

        assert _association_name_to_class("addresses") == "Address"
        assert _association_name_to_class("mattresses") == "Mattress"

    def test_singularization_ses(self) -> None:
        """Plurals ending in ses (statuses, buses) singularize correctly."""
        from hypergumbo_lang_mainstream.ruby import _association_name_to_class

        assert _association_name_to_class("statuses") == "Status"
        assert _association_name_to_class("buses") == "Bus"


class TestRubyDelegateEdges:
    """Tests for Ruby 'delegate' macro edge detection.

    Rails models and classes use `delegate :method, to: :association` to forward
    method calls to associated objects. These create `delegates_to` edges from
    the delegating class to the target method on the associated class.
    """

    def test_delegate_creates_edge_to_target_method(self, tmp_path: Path) -> None:
        """delegate :auto_resolve_after, to: :account creates edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "conversation.rb").write_text("""
class Conversation < ApplicationRecord
  belongs_to :account
  delegate :auto_resolve_after, to: :account
end
""")
        (tmp_path / "account.rb").write_text("""
class Account < ApplicationRecord
  def auto_resolve_after
    24.hours
  end
end
""")

        result = analyze_ruby(tmp_path)

        conv_sym = next((s for s in result.symbols if s.name == "Conversation"), None)
        method_sym = next(
            (s for s in result.symbols if s.name == "Account#auto_resolve_after"), None,
        )
        assert conv_sym is not None, "Should find Conversation class"
        assert method_sym is not None, "Should find Account#auto_resolve_after method"

        delegate_edges = [
            e for e in result.edges
            if e.edge_type == "delegates_to"
            and e.src == conv_sym.id
            and e.dst == method_sym.id
        ]
        assert len(delegate_edges) == 1, (
            f"Expected 1 delegates_to edge, got {len(delegate_edges)}. "
            f"All edges: {[(e.edge_type, e.src, e.dst) for e in result.edges]}"
        )
        assert delegate_edges[0].evidence_type == "ruby_delegate"
        assert delegate_edges[0].confidence == 0.85

    def test_delegate_multiple_methods(self, tmp_path: Path) -> None:
        """delegate :name, :email, to: :user creates edges for both methods."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "profile.rb").write_text("""
class Profile < ApplicationRecord
  belongs_to :user
  delegate :name, :email, to: :user
end
""")
        (tmp_path / "user.rb").write_text("""
class User < ApplicationRecord
  def name
    first_name + " " + last_name
  end

  def email
    @email
  end
end
""")

        result = analyze_ruby(tmp_path)

        profile_sym = next((s for s in result.symbols if s.name == "Profile"), None)
        assert profile_sym is not None

        delegate_edges = [
            e for e in result.edges
            if e.edge_type == "delegates_to" and e.src == profile_sym.id
        ]
        assert len(delegate_edges) == 2, (
            f"Expected 2 delegates_to edges, got {len(delegate_edges)}"
        )

    def test_delegate_target_not_found_creates_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """When target class or method not found, creates unresolved edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "conversation.rb").write_text("""
class Conversation < ApplicationRecord
  delegate :timezone, to: :account
end
""")
        # No account.rb file — target class not in codebase

        result = analyze_ruby(tmp_path)

        conv_sym = next((s for s in result.symbols if s.name == "Conversation"), None)
        assert conv_sym is not None

        delegate_edges = [
            e for e in result.edges
            if e.edge_type == "delegates_to" and e.src == conv_sym.id
        ]
        assert len(delegate_edges) == 1
        assert "unresolved" in delegate_edges[0].dst
        assert delegate_edges[0].confidence == 0.65

    def test_delegate_to_class(self, tmp_path: Path) -> None:
        """delegate :name, to: :class is a valid pattern (delegates to class)."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "widget.rb").write_text("""
class Widget < ApplicationRecord
  delegate :name, to: :class
end
""")

        result = analyze_ruby(tmp_path)

        widget_sym = next((s for s in result.symbols if s.name == "Widget"), None)
        assert widget_sym is not None

        # :class delegates to the class itself — no meaningful edge needed
        delegate_edges = [
            e for e in result.edges
            if e.edge_type == "delegates_to" and e.src == widget_sym.id
        ]
        # :class is a special pseudo-association, skip it
        assert len(delegate_edges) == 0

    def test_delegate_target_class_exists_but_method_missing(
        self, tmp_path: Path,
    ) -> None:
        """When target class exists but delegated method not found, unresolved edge."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "conversation.rb").write_text("""
class Conversation < ApplicationRecord
  delegate :nonexistent_method, to: :account
end
""")
        (tmp_path / "account.rb").write_text("""
class Account < ApplicationRecord
  def timezone
    "UTC"
  end
end
""")

        result = analyze_ruby(tmp_path)

        conv_sym = next((s for s in result.symbols if s.name == "Conversation"), None)
        assert conv_sym is not None

        delegate_edges = [
            e for e in result.edges
            if e.edge_type == "delegates_to" and e.src == conv_sym.id
        ]
        assert len(delegate_edges) == 1
        assert "unresolved" in delegate_edges[0].dst
        assert "Account#nonexistent_method" in delegate_edges[0].dst
        assert delegate_edges[0].confidence == 0.65

    def test_delegate_inside_method_ignored(self, tmp_path: Path) -> None:
        """delegate inside a method body should be ignored (only class-level)."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "service.rb").write_text("""
class Service
  def setup
    delegate :foo, to: :bar
  end
end
""")

        result = analyze_ruby(tmp_path)

        delegate_edges = [
            e for e in result.edges if e.edge_type == "delegates_to"
        ]
        assert len(delegate_edges) == 0


class TestRubyVariableReceiverNoFalsePositive:
    """Tests that variable-receiver calls don't produce false positive edges.

    When a call like ``user.account`` has a local variable receiver, the
    analyzer should NOT fall through to bare-name global lookup, which
    would match an arbitrary ``account`` method from a different class.
    """

    def test_variable_receiver_does_not_match_unrelated_method(self, tmp_path: Path) -> None:
        """user.account should NOT create an edge to Billing#account.

        Previously, when ``user`` is a local variable (not a constant),
        _try_receiver_call returned False, and the code fell through to
        bare-name lookup, matching whatever method named ``account``
        happened to be last in global_symbols.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        # File 1: Billing class with an account method
        (tmp_path / "billing.rb").write_text("""
class Billing
  def account
    @account
  end
end
""")

        # File 2: User class with a show method that calls user.account
        (tmp_path / "users_controller.rb").write_text("""
class UsersController
  def show
    user = User.find(1)
    user.account
  end
end
""")

        result = analyze_ruby(tmp_path)

        # Find the show method
        show = next(
            (s for s in result.symbols if s.name == "UsersController#show"), None
        )
        assert show is not None, "Should find UsersController#show"

        billing_account = next(
            (s for s in result.symbols if s.name == "Billing#account"), None
        )
        assert billing_account is not None, "Should find Billing#account"

        # There should NOT be an edge from show -> Billing#account
        # because `user` is a local variable with unknown type
        false_edges = [
            e for e in result.edges
            if e.src == show.id and e.dst == billing_account.id
        ]
        assert len(false_edges) == 0, (
            f"Variable receiver call 'user.account' should NOT create edge "
            f"to Billing#account. Got: {false_edges}"
        )

    def test_same_class_method_still_resolves(self, tmp_path: Path) -> None:
        """A bare call (no receiver) within a class should still resolve.

        ``process`` called inside ``Worker#run`` should resolve to
        ``Worker#process`` when both are in the same file.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "worker.rb").write_text("""
class Worker
  def run
    process
  end

  def process
    # do work
  end
end
""")

        result = analyze_ruby(tmp_path)

        run_sym = next(
            (s for s in result.symbols if s.name == "Worker#run"), None
        )
        process_sym = next(
            (s for s in result.symbols if s.name == "Worker#process"), None
        )
        assert run_sym is not None
        assert process_sym is not None

        # Bare call (no receiver) should still resolve via local symbols
        call_edges = [
            e for e in result.edges
            if e.src == run_sym.id and e.dst == process_sym.id
        ]
        assert len(call_edges) >= 1, (
            "Bare call 'process' inside Worker#run should resolve to Worker#process"
        )

    def test_constant_receiver_still_resolves(self, tmp_path: Path) -> None:
        """User.find should still resolve when User is a constant receiver."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "user.rb").write_text("""
class User
  def self.find(id)
    # find user
  end
end
""")

        (tmp_path / "controller.rb").write_text("""
class Controller
  def show
    User.find(1)
  end
end
""")

        result = analyze_ruby(tmp_path)

        show = next(
            (s for s in result.symbols if s.name == "Controller#show"), None
        )
        find = next(
            (s for s in result.symbols if s.name == "User.find"), None
        )
        assert show is not None
        assert find is not None

        # Constant receiver should still resolve
        call_edges = [
            e for e in result.edges
            if e.src == show.id and e.dst == find.id
        ]
        assert len(call_edges) >= 1, (
            "Constant receiver User.find should still resolve"
        )


class TestRubyNewConstructorResolution:
    """Tests that SomeClass.new resolves to SomeClass#initialize, not a method named 'new'.

    In Ruby, .new is the constructor class method that allocates and calls #initialize.
    A Rails controller action ``def new`` is an instance method, not the constructor.
    Calling ``SomeClass.new(args)`` should NOT resolve to a controller's ``def new``
    action — it should resolve to ``SomeClass#initialize`` if it exists.
    """

    def test_new_does_not_match_controller_new_action(self, tmp_path: Path) -> None:
        """RoutesController.new should NOT create edge to RoutesController#new action.

        Rails controllers define ``def new`` as an action (instance method) exposed
        via routes. But ``RoutesController.new`` is a constructor call, not an
        invocation of the ``new`` action.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "routes_controller.rb").write_text("""
class RoutesController
  def new
    @route = Route.new
  end

  def create
    # ...
  end
end
""")

        (tmp_path / "service.rb").write_text("""
class RouteService
  def build
    controller = RoutesController.new
    controller.create
  end
end
""")

        result = analyze_ruby(tmp_path)

        build = next(
            (s for s in result.symbols if s.name == "RouteService#build"), None
        )
        new_action = next(
            (s for s in result.symbols if s.name == "RoutesController#new"), None
        )
        assert build is not None, "Should find RouteService#build"
        assert new_action is not None, "Should find RoutesController#new"

        # RoutesController.new is a constructor call, NOT the new action
        false_edges = [
            e for e in result.edges
            if e.src == build.id and e.dst == new_action.id
        ]
        assert len(false_edges) == 0, (
            "RoutesController.new should NOT match the #new controller action"
        )

    def test_new_resolves_to_initialize(self, tmp_path: Path) -> None:
        """SomeClass.new should create an edge to SomeClass#initialize."""
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "widget.rb").write_text("""
class Widget
  def initialize(name)
    @name = name
  end

  def display
    puts @name
  end
end
""")

        (tmp_path / "factory.rb").write_text("""
class Factory
  def create_widget
    Widget.new("test")
  end
end
""")

        result = analyze_ruby(tmp_path)

        create_widget = next(
            (s for s in result.symbols if s.name == "Factory#create_widget"), None
        )
        initialize = next(
            (s for s in result.symbols if s.name == "Widget#initialize"), None
        )
        assert create_widget is not None, "Should find Factory#create_widget"
        assert initialize is not None, "Should find Widget#initialize"

        # .new should resolve to #initialize
        init_edges = [
            e for e in result.edges
            if e.src == create_widget.id and e.dst == initialize.id
        ]
        assert len(init_edges) >= 1, (
            "Widget.new should create edge to Widget#initialize"
        )

    def test_new_without_initialize_no_edge(self, tmp_path: Path) -> None:
        """SomeClass.new with no #initialize defined creates no edge.

        If the class has no user-defined #initialize, the .new call invokes
        Object#initialize (which is not in user code), so no edge should be created.
        """
        from hypergumbo_lang_mainstream.ruby import analyze_ruby

        (tmp_path / "simple.rb").write_text("""
class Simple
  def hello
    puts "hello"
  end
end
""")

        (tmp_path / "caller.rb").write_text("""
class Caller
  def run
    Simple.new
  end
end
""")

        result = analyze_ruby(tmp_path)

        run = next(
            (s for s in result.symbols if s.name == "Caller#run"), None
        )
        hello = next(
            (s for s in result.symbols if s.name == "Simple#hello"), None
        )
        assert run is not None, "Should find Caller#run"
        assert hello is not None, "Should find Simple#hello"

        # .new with no initialize should NOT create an edge to Simple#hello
        false_edges = [
            e for e in result.edges
            if e.src == run.id and e.dst == hello.id
        ]
        assert len(false_edges) == 0, (
            "Simple.new should NOT create false edges to arbitrary methods"
        )
